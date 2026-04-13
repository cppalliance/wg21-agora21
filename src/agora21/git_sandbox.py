"""Restrict agent (Claude / Cursor) edits to ``data/**`` by snapshotting the index and reverting everything else.

Before each substrate run we ``git add -A`` and record ``git write-tree``. After the run, any path outside
``data/`` that differs from that tree is restored (working tree + index). Untracked files outside ``data/``
are removed. Changes under ``data/`` are kept.

Set ``WG21_DISABLE_GIT_SANDBOX=1`` to skip (e.g. non-git workspace or local debugging).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

logger = logging.getLogger(__name__)


def _disabled() -> bool:
    v = os.environ.get("WG21_DISABLE_GIT_SANDBOX", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _is_under_data(repo_rel: str) -> bool:
    p = repo_rel.replace("\\", "/").strip()
    return p == "data" or p.startswith("data/")


def _run_git(root: Path, args: list[str], *, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def is_git_worktree(root: Path) -> bool:
    r = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return r.returncode == 0 and r.stdout.strip() == "true"


def snapshot_staged_tree(root: Path) -> str | None:
    """``git add -A`` and return ``git write-tree`` (tree of the index), or ``None`` if unavailable."""
    if _disabled():
        return None
    if not is_git_worktree(root):
        logger.info("git sandbox: not a git worktree; skipping snapshot.")
        return None
    add = _run_git(root, ["add", "-A"])
    if add.returncode != 0:
        logger.warning(
            "git sandbox: git add -A failed (%s); skipping snapshot. stderr=%s",
            add.returncode,
            (add.stderr or "")[:500],
        )
        return None
    wt = _run_git(root, ["write-tree"])
    if wt.returncode != 0:
        logger.warning(
            "git sandbox: git write-tree failed (%s); stderr=%s",
            wt.returncode,
            (wt.stderr or "")[:500],
        )
        return None
    sha = wt.stdout.strip()
    if len(sha) != 40:
        logger.warning("git sandbox: unexpected write-tree output %r", sha[:80])
        return None
    logger.info("git sandbox: snapshot index tree %s (pre-agent)", sha[:12])
    return sha


def _paths_differing_from_tree(root: Path, tree_sha: str) -> set[str]:
    paths: set[str] = set()
    for args in (
        ["diff", "--name-only", tree_sha],
        ["diff", "--name-only", "--cached", tree_sha],
    ):
        r = _run_git(root, args)
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            p = line.strip().replace("\\", "/")
            if p:
                paths.add(p)
    return paths


def _untracked_files(root: Path) -> list[str]:
    r = _run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    if r.returncode != 0:
        return []
    raw = r.stdout or ""
    if not raw:
        return []
    return [x.replace("\\", "/") for x in raw.split("\0") if x.strip()]


def revert_non_data_paths(root: Path, tree_sha: str) -> None:
    """Restore repo state for all paths except ``data/**`` to match *tree_sha* (drop untracked outside data)."""
    if _disabled():
        return
    if not is_git_worktree(root):
        return

    changed = _paths_differing_from_tree(root, tree_sha)
    for p in sorted(changed):
        if _is_under_data(p):
            continue
        rr = _run_git(root, ["restore", "--source", tree_sha, "--staged", "--worktree", "--", p])
        if rr.returncode != 0:
            logger.debug(
                "git restore %s from %s failed (%s); trying rm — stderr=%s",
                p,
                tree_sha[:12],
                rr.returncode,
                (rr.stderr or "")[:300],
            )
            bad = root / p
            if bad.is_file() or bad.is_symlink():
                try:
                    bad.unlink()
                except OSError as e:
                    logger.warning("git sandbox: could not remove %s: %s", bad, e)
            elif bad.is_dir():
                try:
                    shutil.rmtree(bad)
                except OSError as e:
                    logger.warning("git sandbox: could not rmtree %s: %s", bad, e)
            _run_git(root, ["rm", "-rf", "--cached", "--ignore-unmatch", "--", p])

    for p in _untracked_files(root):
        if _is_under_data(p):
            continue
        target = root / p
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except OSError as e:
            logger.warning("git sandbox: could not remove untracked %s: %s", target, e)

    logger.info(
        "git sandbox: reverted non-data paths to pre-agent tree %s (kept data/**).",
        tree_sha[:12],
    )


@contextmanager
def agent_git_sandbox(root: Path) -> Generator[str | None, None, None]:
    """Context manager: snapshot index before body; revert non-``data/`` after (always, including on error)."""
    tree = snapshot_staged_tree(root)
    try:
        yield tree
    finally:
        if tree is not None:
            revert_non_data_paths(root, tree)
