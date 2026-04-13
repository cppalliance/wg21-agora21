"""Repository root and standard paths."""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_PYPROJECT_NAME = "agora21"
_ENV_REPO_ROOT_KEYS = ("AGORA21_REPO_ROOT",)


def _project_name_in_pyproject(path: Path) -> bool:
    """True if ``path/pyproject.toml`` declares this package (``[project]`` name)."""
    pp = path / "pyproject.toml"
    if not pp.is_file():
        return False
    try:
        text = pp.read_text(encoding="utf-8")
    except OSError:
        return False
    if "[project]" not in text:
        return False
    idx = text.find("[project]")
    chunk = text[idx : idx + 1200] if idx >= 0 else text
    return (
        f'name = "{_PROJECT_PYPROJECT_NAME}"' in chunk
        or f"name = '{_PROJECT_PYPROJECT_NAME}'" in chunk
    )


def _find_repo_root_walk_up(start: Path) -> Path | None:
    """Walk parents from ``start`` (file or directory) for this repo's ``pyproject.toml``."""
    cur = start.resolve()
    if cur.is_file():
        cur = cur.parent
    for _ in range(96):
        if _project_name_in_pyproject(cur):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def repo_root() -> Path:
    """Directory containing ``pyproject.toml`` for this project."""
    for key in _ENV_REPO_ROOT_KEYS:
        raw = os.environ.get(key)
        if raw:
            p = Path(raw).expanduser().resolve()
            if _project_name_in_pyproject(p):
                return p

    found = _find_repo_root_walk_up(Path(__file__).resolve())
    if found is not None:
        return found

    found = _find_repo_root_walk_up(Path.cwd())
    if found is not None:
        return found

    return Path(__file__).resolve().parent.parent.parent


def schema_path(name: str) -> Path:
    return repo_root() / "schemas" / name


def prompt_path(name: str) -> Path:
    """Path under ``prompts/`` (Mod prompt and other LLM instructions)."""
    return repo_root() / "prompts" / name
