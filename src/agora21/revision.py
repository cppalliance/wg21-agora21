"""Git revision metadata for traceable JSON output."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agora21.paths import repo_root


def producer_git_sha(*, root: Path | None = None) -> str:
    """Short-lived SHA of the repository at ``root`` (default: agora21 repo root). Empty if unavailable."""
    r = root or repo_root()
    try:
        proc = subprocess.run(
            ["git", "-C", str(r), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""
