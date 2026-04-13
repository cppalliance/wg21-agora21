"""Author (Claude Code) / reviewer (Cursor CLI) loop until JSON Schema passes.

Orchestration pattern derived from ``pragma-agent/core/workflows/skill_runner.py``
and substrates ``claude_code.py`` / ``cursor.py`` — duplicated in-tree without
importing pragma-agent.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Child CLI (claude / agent) may own the console on Windows; SIGINT still often
# reaches Python — we terminate this process explicitly so Ctrl+C ends the wait.
_active_subprocess: subprocess.Popen[Any] | None = None
_sigint_registered = False

from agora21.validate import validate_thread, validate_thread_model, validation_error_message
from agora21.revision import producer_git_sha
from agora21.claude_stream import extract_json_value, json_from_assist, parse_stream_json_lines
from agora21.git_sandbox import agent_git_sandbox
from agora21.config import load_config
from agora21.credentials import ensure_api_key
from agora21.paths import prompt_path, repo_root, schema_path

logger = logging.getLogger(__name__)

_LOG_ERR_CAP = 2500


def _safe_filename_segment(s: str) -> str:
    out = "".join(c if c.isalnum() or c in "-_" else "_" for c in s.lower())
    return out[:96] or "paper"


def _write_claude_debug_temp(
    root: Path,
    paper_id: str,
    round_i: int,
    out_lines: list[str],
    parsed: dict[str, Any],
    candidate: Any,
) -> None:
    """Write Claude ``stream-json`` stdout and parsed snapshots under ``temp/`` for debugging."""
    temp_dir = root / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = _safe_filename_segment(paper_id)
    stem = f"claude-{safe}-r{round_i}-{ts}"
    jsonl_path = temp_dir / f"{stem}.jsonl"
    jsonl_path.write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
    parsed_path = temp_dir / f"{stem}-parsed.json"
    try:
        parsed_path.write_text(
            json.dumps(parsed, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
    except (TypeError, ValueError) as e:
        logger.warning("Could not write %s: %s", parsed_path, e)
    if isinstance(candidate, dict):
        cand_path = temp_dir / f"{stem}-candidate.json"
        cand_path.write_text(
            json.dumps(candidate, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
    logger.info(
        "Debug: wrote Claude stream under %s (%s lines); parsed → %s",
        jsonl_path,
        len(out_lines),
        parsed_path.name,
    )


def _register_sigint_terminates_child() -> None:
    global _sigint_registered
    if _sigint_registered or not hasattr(signal, "SIGINT"):
        return

    def _on_sigint(_signum: int, _frame: Any) -> None:
        p = _active_subprocess
        if p is not None and p.poll() is None:
            logger.warning("Interrupt: terminating child process pid=%s", p.pid)
            try:
                p.terminate()
            except OSError as e:
                logger.warning("terminate() failed: %s", e)
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _on_sigint)
    _sigint_registered = True


def _which(name: str) -> str:
    p = shutil.which(name)
    return p or name


def _run_capture_lines(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_s: float,
    phase: str = "subprocess",
    stdin_text: str | None = None,
) -> tuple[int, list[str], str]:
    """Run ``cmd`` with captured stdout/stderr.

    Uses short :meth:`subprocess.Popen.communicate` timeouts instead of a single
    long ``subprocess.run(..., timeout=...)`` so the main thread can handle
    :exc:`KeyboardInterrupt` (Ctrl+C). A SIGINT handler also terminates the
    active child when the OS delivers SIGINT to Python.
    """
    global _active_subprocess
    _register_sigint_terminates_child()
    exe = cmd[0] if cmd else "?"
    logger.info(
        "%s: starting %s (timeout %.0fs); pid will be assigned to active child",
        phase,
        exe,
        float(timeout_s),
    )

    # Claude/agent emit UTF-8 (JSON, stream-json). On Windows the default locale encoding is often cp1252,
    # which raises UnicodeDecodeError on arbitrary bytes — force UTF-8 for pipes.
    popen_kw: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    if stdin_text is not None:
        popen_kw["stdin"] = subprocess.PIPE
    if sys.platform == "win32":
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(cmd, **popen_kw)
    _active_subprocess = proc
    logger.info("%s: child pid=%s", phase, proc.pid)
    deadline = time.monotonic() + float(timeout_s)
    stdout: str | None = None
    stderr: str | None = None
    input_data = stdin_text
    try:
        if input_data is not None:
            # Prompt via stdin avoids Windows CreateProcess command-line length limits (~8191 chars).
            try:
                stdout, stderr = proc.communicate(input=input_data, timeout=float(timeout_s))
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(cmd, timeout_s, output=stdout, stderr=stderr) from None
        else:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                    raise subprocess.TimeoutExpired(cmd, timeout_s, output=stdout, stderr=stderr)
                try:
                    # Short slice so Ctrl+C / SIGINT is handled frequently.
                    stdout, stderr = proc.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
    except KeyboardInterrupt:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        raise
    finally:
        _active_subprocess = None

    out_lines = (stdout or "").splitlines()
    err = stderr or ""
    code = proc.returncode if proc.returncode is not None else -1
    logger.info(
        "%s: finished exit_code=%s stdout_lines=%s stderr_chars=%s",
        phase,
        code,
        len(out_lines),
        len(err),
    )
    if err.strip():
        logfn = logger.warning if code != 0 else logger.info
        logfn("%s: stderr (first %s chars):\n%s", phase, _LOG_ERR_CAP, err[:_LOG_ERR_CAP])
    return code, out_lines, err


def _claude_env() -> dict[str, str]:
    ensure_api_key()
    env = os.environ.copy()
    env.setdefault("CLAUDE_CODE_DISABLE_TELEMETRY", "1")
    return env


def _cursor_env() -> dict[str, str]:
    env = os.environ.copy()
    if not env.get("CURSOR_API_KEY"):
        raise ValueError("CURSOR_API_KEY is required for the reviewer step")
    return env


def thread_json_path(output_base: Path, paper_id: str) -> Path:
    """Path to the thread JSON artifact under *output_base* (per-paper directory)."""
    return output_base / paper_id.lower() / "thread.json"


def finalize_thread_document(obj: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    """Set ``schema_version`` 1.0.0 and ``producer_git_sha``; validate final document."""
    out = {**obj, "schema_version": "1.0.0", "producer_git_sha": producer_git_sha(root=root)}
    validate_thread(out)
    return out


def build_full_prompt(paper_id: str, mailing_id: str, reviewer_feedback: str | None) -> str:
    doc = prompt_path("reddit.md").read_text(encoding="utf-8")
    schema_ref = schema_path("thread-v1.0.0.schema.json")
    schema_snippet = schema_ref.read_text(encoding="utf-8")[:12000]
    parts = [
        f"## Target paper\n- paper_id: {paper_id}\n- mailing_id: {mailing_id}\n",
        "## Mod prompt (prompts/reddit.md)\n\n",
        doc,
        "\n\n## JSON output contract\n",
        "Produce a single JSON object for the synthetic thread. It MUST validate against this schema "
        f"(file: {schema_ref.name}):\n\n```json\n{schema_snippet}\n```\n",
        "Include `\"producer_git_sha\": \"\"` if you do not know the repo SHA; it will be filled in after validation.\n",
        "Return JSON only in the final result — no HTML, no CSS, no markdown outside JSON.\n",
        "\n## Submission fields (author — follow exactly)\n",
        "- `submission.author`: one Reddit-style OP username for this thread, from prompts/reddit.md section 5 "
        "(e.g. `u/simd_ergonomics_watcher`). Must be a generated persona for this run — not a repo placeholder name.\n",
        "- `submission.body_markdown`: open with **Document:**, **Author:**, **Date:**, **Audience:** (Markdown), "
        "then a blank line and the paraphrase. Do **not** include a **Link:** line; the site linkifies Document.\n",
        "- Optional `submission.upvote_pct` (0–100) for the “(N% upvoted)” footer.\n",
        "- Entire JSON file must be valid UTF-8; use Unicode em dash (—) where appropriate, not mojibake.\n",
        "\n## Provenance (no hallucinated metadata)\n",
        "- Before writing `submission` and top-level `title`, open the canonical paper at "
        "`https://wg21.link/` + paper_id + `.html` or `.pdf` (whichever resolves). "
        "Copy **title**, **author list**, and **document date** from that source into the JSON. "
        "Do **not** invent authors, titles, or mailing metadata.\n",
        "\n## Committee routing (`committee` field — required)\n",
        "- Add string `committee`, exactly one of: `lwg`, `lewg`, `ewg`, `cwg`, `wg21_all`.\n",
        "- Use the mailing row **Subgroup** (open-std table) and paper content: map to LWG / LEWG / EWG / CWG when the paper is clearly driven by that committee.\n",
        "- Use `wg21_all` when the Subgroup is effectively whole-WG21 (e.g. **All of WG21**), or the paper is SG-only / admin / direction with no primary LWG|LEWG|EWG|CWG track "
        "— it still appears on **r/wg21** but not on r/lwg … r/cwg.\n",
    ]
    if reviewer_feedback:
        parts.append(
            "\n## Reviewer feedback (fix validation errors)\n\n" + reviewer_feedback + "\n",
        )
    return "".join(parts)


def run_claude_author(
    prompt: str,
    *,
    cwd: Path,
    model: str,
    timeout_s: float,
) -> tuple[list[str], str]:
    sid = str(uuid.uuid4())
    cmd = [
        _which("claude"),
        "-p",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--session-id",
        sid,
        "--permission-mode",
        os.environ.get("WG21_CLAUDE_PERMISSION_MODE", "acceptEdits"),
    ]
    with agent_git_sandbox(cwd):
        code, lines, err = _run_capture_lines(
            cmd,
            cwd=cwd,
            env=_claude_env(),
            timeout_s=timeout_s,
            phase="author (claude)",
            stdin_text=prompt,
        )
    return lines, err


def run_cursor_reviewer(
    draft_json: str,
    validation_error: str,
    *,
    cwd: Path,
    model: str,
    timeout_s: float,
) -> tuple[list[str], str]:
    prompt = (
        "You fix JSON so it validates against the thread schema. "
        "Preserve prompts/reddit.md rules for `submission`: `author` is a generated Reddit-style username; "
        "`body_markdown` has Document/Author/Date/Audience metadata without a separate Link: line; UTF-8 only. "
        "Include required `committee` (lwg|lewg|ewg|cwg|wg21_all) matching the thread JSON schema. "
        "Reply with a single JSON object only — no prose, no markdown fences.\n\n"
        "## Validation errors\n"
        f"{validation_error}\n\n## Draft JSON\n{draft_json}\n"
    )
    cmd = [
        _which("agent"),
        "-p",
        "--output-format",
        "stream-json",
        "--trust",
        "--mode",
        "ask",
        "--model",
        model,
        "--workspace",
        str(cwd),
    ]
    with agent_git_sandbox(cwd):
        code, lines, err = _run_capture_lines(
            cmd,
            cwd=cwd,
            env=_cursor_env(),
            timeout_s=timeout_s,
            phase="reviewer (cursor agent)",
            stdin_text=prompt,
        )
    return lines, err


def parse_agent_json(lines: list[str]) -> Any | None:
    parsed = parse_stream_json_lines(lines)
    j = json_from_assist(parsed)
    if j is not None:
        return j
    rj = parsed.get("result_json")
    if isinstance(rj, dict) and "text" in rj:
        return extract_json_value(str(rj.get("text", "")))
    return None


def try_recover_thread_from_disk(
    output_base: Path,
    paper_id: str,
    *,
    not_before: float,
) -> dict[str, Any] | None:
    """Load ``<output_base>/<paper>/thread.json`` if the author wrote it via tools but stdout had no parseable JSON.

    ``not_before`` is :func:`time.time` captured immediately before starting Claude. Only files whose mtime is
    at/after that moment (minus a few seconds slack) are accepted, so stale JSON from an earlier run is ignored.
    """
    if os.environ.get("WG21_DISABLE_DISK_THREAD_RECOVERY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return None
    path = thread_json_path(output_base, paper_id)
    if not path.is_file():
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if mtime < not_before - 5.0:
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.debug("Disk recovery: could not read JSON from %s: %s", path, e)
        return None
    if not isinstance(data, dict):
        return None
    try:
        validate_thread_model(data)
    except Exception as e:
        logger.debug(
            "Disk recovery: %s failed schema: %s",
            path,
            validation_error_message(e)[:300],
        )
        return None
    logger.info(
        "Recovered valid thread JSON from %s (file written by author; stream-json had no usable object).",
        path,
    )
    return data


def author_reviewer_loop(
    paper_id: str,
    mailing_id: str,
    *,
    root: Path | None = None,
    output_base: Path | None = None,
) -> dict[str, Any]:
    cfg = load_config()
    root = root or repo_root()
    out_base = output_base if output_base is not None else root / "data"
    max_rounds = int(cfg.get("loop", {}).get("max_rounds", 5))
    author_model = str(cfg.get("author", {}).get("model", "sonnet"))
    reviewer_model = str(cfg.get("reviewer", {}).get("model", "composer-1"))
    author_timeout = float(cfg.get("author", {}).get("timeout_s", 3600))
    reviewer_timeout = float(cfg.get("reviewer", {}).get("timeout_s", 3600))

    reviewer_notes = ""
    last_draft: dict[str, Any] | None = None

    for round_i in range(1, max_rounds + 1):
        logger.info("Round %s/%s for %s", round_i, max_rounds, paper_id)
        if reviewer_notes:
            logger.info("Carrying reviewer feedback into this round (first 800 chars):\n%s", reviewer_notes[:800])
        prompt = build_full_prompt(paper_id, mailing_id, reviewer_notes or None)
        round_started = time.time()
        out_lines, author_err = run_claude_author(prompt, cwd=root, model=author_model, timeout_s=author_timeout)
        parsed = parse_stream_json_lines(out_lines)
        candidate = json_from_assist(parsed)
        if candidate is None:
            rj = parsed.get("result_json")
            text = ""
            if isinstance(rj, dict):
                text = str(rj.get("text", ""))
            elif isinstance(rj, str):
                text = rj
            candidate = extract_json_value(text) if text else None
        if candidate is None and parsed.get("text_blocks"):
            candidate = extract_json_value(parsed["text_blocks"][-1])
        if candidate is None:
            disk = try_recover_thread_from_disk(out_base, paper_id, not_before=round_started)
            if isinstance(disk, dict):
                candidate = disk

        _write_claude_debug_temp(root, paper_id, round_i, out_lines, parsed, candidate)

        if candidate is None:
            logger.info(
                "Author output: no parseable JSON (stream lines=%s). Retrying round.",
                len(out_lines),
            )
            reviewer_notes = "Author did not return parseable JSON. Emit one JSON object only."
            continue

        last_draft = candidate if isinstance(candidate, dict) else None
        if not isinstance(candidate, dict):
            logger.info("Author output: top-level JSON is not an object (type=%s). Retrying round.", type(candidate).__name__)
            reviewer_notes = "Top-level JSON must be an object."
            continue

        try:
            validate_thread_model(candidate)
            return finalize_thread_document(candidate, root=root)
        except Exception as exc:
            err_txt = validation_error_message(exc)
            logger.info(
                "Schema validation failed; calling reviewer. Error (first 1200 chars):\n%s",
                err_txt[:1200],
            )
            draft_txt = json.dumps(candidate, indent=2)[:24000]
            r_lines, rev_err = run_cursor_reviewer(
                draft_txt,
                err_txt,
                cwd=root,
                model=reviewer_model,
                timeout_s=reviewer_timeout,
            )
            fixed = parse_agent_json(r_lines)
            if isinstance(fixed, dict):
                try:
                    validate_thread_model(fixed)
                    logger.info("Reviewer produced valid JSON; done.")
                    return finalize_thread_document(fixed, root=root)
                except Exception as exc2:
                    logger.info(
                        "Reviewer JSON still invalid: %s",
                        validation_error_message(exc2)[:800],
                    )
                    reviewer_notes = (
                        f"Schema still invalid after reviewer: {validation_error_message(exc2)}\n"
                        f"Reviewer JSON was:\n{json.dumps(fixed)[:8000]}"
                    )
            else:
                logger.info(
                    "Reviewer did not return parseable JSON (reviewer stdout lines=%s).",
                    len(r_lines),
                )
                reviewer_notes = (
                    f"Schema: {err_txt}\nReviewer did not return JSON; fix the draft.\n\n"
                    f"Previous draft:\n{draft_txt[:12000]}"
                )

    raise RuntimeError(
        f"Exhausted {max_rounds} rounds for {paper_id}; last error; draft={json.dumps(last_draft)[:2000]!r}",
    )


def write_thread_json(
    paper_id: str,
    data: dict[str, Any],
    output_base: Path,
) -> Path:
    out = thread_json_path(output_base, paper_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out


def generate_minimal_valid_thread(
    paper_id: str,
    mailing_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Deterministic placeholder for dry-run / CI without API keys."""
    pid = paper_id.lower()
    r = root or repo_root()
    return {
        "schema_version": "1.0.0",
        "producer_git_sha": producer_git_sha(root=r),
        "paper_id": pid,
        "mailing_id": mailing_id,
        "title": f"Synthetic thread for {pid}",
        "heat_tier": "warm",
        "submission": {
            "title": f"[WG21] {pid}",
            "body_markdown": "Placeholder body.",
            "author": "u/thread_op",
            "flair": None,
            "score": 42,
            "created_ago": "just now",
        },
        "comments": [],
        "promoted": [],
        "committee": "wg21_all",
    }
