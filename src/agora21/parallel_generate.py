"""Run ``eval`` for many papers in parallel using separate ``git worktree`` checkouts (sandbox isolation)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _sanitize_branch_token(paper_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", paper_id.lower().strip())
    s = re.sub(r"-+", "-", s).strip("-")
    return (s[:72] or "paper") + "-" + uuid.uuid4().hex[:8]


def _run_git(root: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _create_worktree(main_root: Path, wt_path: Path, branch: str) -> None:
    wt_path.parent.mkdir(parents=True, exist_ok=True)
    r = _run_git(main_root, ["worktree", "add", "-b", branch, str(wt_path), "HEAD"], check=False)
    if r.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed ({r.returncode}): {(r.stderr or r.stdout or '')[:800]}"
        )


def _remove_worktree(main_root: Path, wt_path: Path) -> None:
    if not wt_path.is_dir():
        return
    r = _run_git(main_root, ["worktree", "remove", "--force", str(wt_path)], check=False)
    if r.returncode != 0:
        logger.warning("git worktree remove failed for %s: %s", wt_path, (r.stderr or "")[:400])
        try:
            shutil.rmtree(wt_path, ignore_errors=True)
        except OSError as e:
            logger.warning("rmtree %s: %s", wt_path, e)


def _delete_branch(main_root: Path, branch: str) -> None:
    _run_git(main_root, ["branch", "-D", branch], check=False)


def _eval_in_worktree(
    *,
    main_root: Path,
    wt_path: Path,
    paper_id: str,
    mailing_id: str,
    output_dir: Path,
    dry_run: bool,
) -> tuple[str, bool, str | None]:
    """Run ``agora21 eval`` in *wt_path*; artifacts go to shared *output_dir*."""
    env = os.environ.copy()
    env["AGORA21_REPO_ROOT"] = str(wt_path)
    cmd = [
        sys.executable,
        "-m",
        "agora21",
        "eval",
        paper_id,
        "--mailing-id",
        mailing_id,
        "--output-dir",
        str(output_dir),
    ]
    if dry_run:
        cmd.append("--dry-run")
    r = subprocess.run(
        cmd,
        cwd=wt_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    pid = paper_id.lower()
    expected = output_dir / pid / "thread.json"
    if r.returncode == 0 and expected.is_file():
        return (pid, True, None)
    err = (r.stderr or r.stdout or "")[:2000] or f"exit {r.returncode}"
    return (pid, False, err)


def run_parallel_eval(
    main_root: Path,
    paper_ids: list[str],
    *,
    mailing_id: str,
    output_dir: Path,
    max_workers: int,
    dry_run: bool,
) -> dict[str, tuple[bool, str | None]]:
    """Create one git worktree per paper; run ``eval`` in parallel. Returns paper_id -> (ok, err)."""
    ids = [p.strip() for p in paper_ids if p and str(p).strip()]
    if not ids:
        logger.info("run_parallel_eval: no papers; nothing to do.")
        return {}

    workers = max(1, min(max_workers, len(ids)))
    base = Path(tempfile.mkdtemp(prefix="agora21-wt-"))
    meta: list[tuple[str, Path, str]] = []
    try:
        for raw in ids:
            token = _sanitize_branch_token(raw)
            branch = f"agora21-wt/{token}"
            wt_path = base / token
            _create_worktree(main_root, wt_path, branch)
            meta.append((raw.lower().strip(), wt_path, branch))

        results: dict[str, tuple[bool, str | None]] = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {
                ex.submit(
                    _eval_in_worktree,
                    main_root=main_root,
                    wt_path=wt,
                    paper_id=pid,
                    mailing_id=mailing_id,
                    output_dir=output_dir,
                    dry_run=dry_run,
                ): pid
                for pid, wt, br in meta
            }
            for fut in as_completed(futs):
                pid, ok, err = fut.result()
                results[pid] = (ok, err)

        return results
    finally:
        for _pid, wt_path, branch in reversed(meta):
            _remove_worktree(main_root, wt_path)
            _delete_branch(main_root, branch)
        try:
            shutil.rmtree(base, ignore_errors=True)
        except OSError:
            pass


def papers_from_json_arg(raw: str | None) -> list[str]:
    if raw is None:
        return []
    raw = raw.strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, list):
        return [str(x) for x in data]
    raise ValueError("--papers-json must be a JSON array of paper id strings")


def max_workers_from_config(cfg: dict[str, Any]) -> int:
    w = (cfg.get("parallel") or {}).get("max_workers", 10)
    return max(1, int(w))
