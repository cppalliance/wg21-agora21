"""API key mapping for Claude Code (Anthropic vs OpenRouter).

Duplicated from pragma-agent ``core/utils.py::ensure_api_key`` (trimmed: no
pinecone load_env). Source for drift review:
  d:/_pragma-group/pragma-agent/core/utils.py

When ``ANTHROPIC_API_KEY`` is unset but ``OPENROUTER_API_KEY`` is set, **both**
``OPENROUTER_API_KEY`` and ``ANTHROPIC_BASE_URL`` are required (Claude Code uses
the Anthropic-compatible base URL). Then map OpenRouter to Anthropic env:
  ANTHROPIC_API_KEY=""  (cleared)
  ANTHROPIC_AUTH_TOKEN=<openrouter secret>
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from agora21.paths import repo_root

logger = logging.getLogger(__name__)

# If the shell exports e.g. ANTHROPIC_API_KEY= (empty), python-dotenv will not
# overwrite it and `.env` appears to be ignored. Drop empties so file values apply.
_DOTENV_KEYS_CLEAR_IF_EMPTY = (
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CURSOR_API_KEY",
)


def _clear_empty_env_placeholders() -> None:
    for key in _DOTENV_KEYS_CLEAR_IF_EMPTY:
        if os.environ.get(key, None) == "":
            del os.environ[key]


def _env_nonempty(name: str) -> bool:
    v = os.environ.get(name)
    if v is None:
        return False
    return bool(str(v).strip())


def _describe_env_key(name: str) -> str:
    """How ``name`` looks in ``os.environ`` after dotenv load (no secret values)."""
    if name not in os.environ:
        return "absent"
    v = os.environ[name]
    if v == "":
        return "empty_string"
    if not str(v).strip():
        return "whitespace_only"
    return f"non_empty(len={len(v)})"


def _parse_dotenv_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file without requiring ``python-dotenv`` (fallback).

    Handles UTF-8 BOM, ``#`` comments, optional ``export `` prefix, and simple
    ``KEY=value`` / quoted values. Does not implement multiline values.
    """
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise OSError(f"cannot read {path}") from e
    out: dict[str, str] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def _apply_parsed_env(data: dict[str, str], *, override: bool) -> None:
    """Merge parsed ``KEY=value`` pairs into ``os.environ`` (``override`` matches dotenv)."""
    for k, v in data.items():
        if override:
            os.environ[k] = v
        elif k not in os.environ:
            os.environ[k] = v


def _describe_dotenv_file(path: Path) -> str:
    """Report key names and value *shapes* for API keys (no secrets)."""
    if not path.is_file():
        return "missing"
    try:
        vals = _parse_dotenv_file(path)
    except OSError as e:
        return f"read_error:{e!r}"
    bits: list[str] = []
    for key in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_BASE_URL"):
        if key not in vals:
            bits.append(f"{key}=not_in_file")
            continue
        v = vals[key]
        if v == "":
            bits.append(f"{key}=empty")
        elif not str(v).strip():
            bits.append(f"{key}=whitespace_only")
        else:
            bits.append(f"{key}=non_empty(len={len(v)})")
    return ", ".join(bits)


def log_credentials_diagnostics(reason: str = "ensure_api_key failed") -> None:
    """Log safe diagnostics for API key resolution (enable INFO on ``agora21.credentials`` to see)."""
    root = repo_root()
    env_a = _describe_env_key("ANTHROPIC_API_KEY")
    env_o = _describe_env_key("OPENROUTER_API_KEY")
    env_b = _describe_env_key("ANTHROPIC_BASE_URL")
    p_env = root / ".env"
    p_local = root / ".env.local"
    file_env = _describe_dotenv_file(p_env)
    file_local = _describe_dotenv_file(p_local)
    logger.warning(
        "wg21 credentials diagnostics (%s): repo_root=%s | "
        "os.environ ANTHROPIC_API_KEY=%s OPENROUTER_API_KEY=%s ANTHROPIC_BASE_URL=%s | "
        ".env [%s bytes]: %s | .env.local: %s",
        reason,
        root,
        env_a,
        env_o,
        env_b,
        p_env.stat().st_size if p_env.is_file() else 0,
        file_env,
        file_local,
    )


def load_repo_dotenv() -> None:
    """Load ``.env`` then ``.env.local`` from the repository root into ``os.environ``.

    Uses ``python-dotenv`` when installed; otherwise parses ``.env`` with a small
    built-in parser so missing optional deps do not skip loading secrets.

    Variables already set in the process environment are not overwritten.
    ``.env.local`` is loaded second so it can override keys from ``.env`` for
    machine-specific values (using ``override=True`` only for that file’s
    entries — see python-dotenv behavior).

    Empty placeholders for API-related keys are removed first so a non-empty
    value from ``.env`` is not blocked by ``export ANTHROPIC_API_KEY=`` in the shell.

    UTF-8 with BOM (common on Windows) is accepted via ``utf-8-sig``.

    Safe to call multiple times.
    """
    _clear_empty_env_placeholders()
    root = repo_root()
    env_path = root / ".env"
    local_path = root / ".env.local"
    enc = "utf-8-sig"
    try:
        from dotenv import load_dotenv
    except ImportError:
        load_dotenv = None  # type: ignore[assignment]
    if load_dotenv is not None:
        load_dotenv(env_path, encoding=enc)
        load_dotenv(local_path, override=True, encoding=enc)
        return
    _apply_parsed_env(_parse_dotenv_file(env_path), override=False)
    _apply_parsed_env(_parse_dotenv_file(local_path), override=True)


def ensure_api_key() -> None:
    """Ensure Claude Code can authenticate: direct Anthropic key or OpenRouter + base URL."""
    if _env_nonempty("ANTHROPIC_API_KEY"):
        return
    if _env_nonempty("OPENROUTER_API_KEY"):
        if not _env_nonempty("ANTHROPIC_BASE_URL"):
            log_credentials_diagnostics(
                "OPENROUTER_API_KEY set but ANTHROPIC_BASE_URL missing (required for Claude via OpenRouter)"
            )
            raise ValueError(
                "Claude Code via OpenRouter requires both OPENROUTER_API_KEY and ANTHROPIC_BASE_URL "
                "(Anthropic-compatible API base, e.g. https://openrouter.ai/api). "
                "Set ANTHROPIC_BASE_URL in the environment or repo-root .env / .env.local."
            )
        os.environ["ANTHROPIC_API_KEY"] = ""
        os.environ["ANTHROPIC_AUTH_TOKEN"] = os.environ["OPENROUTER_API_KEY"].strip()
        return
    root = repo_root()
    dotenv_ok = (root / ".env").is_file()
    log_credentials_diagnostics("no usable ANTHROPIC_API_KEY or OPENROUTER_API_KEY after load_repo_dotenv")
    raise ValueError(
        "No API key found: set ANTHROPIC_API_KEY, or set OPENROUTER_API_KEY with ANTHROPIC_BASE_URL "
        "for Claude via OpenRouter. Use a repo-root .env file if needed. "
        "If the variable is set but empty (shell or .env line with no value), it is ignored; "
        "check .env.local does not override with blanks. "
        f"Resolved repo root: {root} (.env present: {dotenv_ok}). "
        "If you use a non-editable install, run the CLI from the clone directory or set "
        "AGORA21_REPO_ROOT to that directory. "
        "See WARNING log line from agora21.credentials for safe diagnostics (file parse vs os.environ)."
    )
