"""Git sandbox: only ``data/**`` may change across agent runs."""

import shutil
import subprocess
from pathlib import Path

import pytest

from agora21.git_sandbox import (
    agent_git_sandbox,
    is_git_worktree,
    revert_non_data_paths,
    snapshot_staged_tree,
)


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "sandbox@test.local"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "sandbox"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.mark.skipif(not shutil.which("git"), reason="git not on PATH")
def test_snapshot_and_revert_keeps_data_only(tmp_path: Path) -> None:
    repo = tmp_path
    _git_init(repo)
    (repo / "README.md").write_text("original\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "thread.json").write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    assert is_git_worktree(repo)
    tree = snapshot_staged_tree(repo)
    assert tree is not None and len(tree) == 40

    (repo / "README.md").write_text("HACKED\n", encoding="utf-8")
    (repo / "data" / "thread.json").write_text('{"ok": true}\n', encoding="utf-8")
    (repo / "evil.txt").write_text("x\n", encoding="utf-8")

    revert_non_data_paths(repo, tree)

    assert (repo / "README.md").read_text(encoding="utf-8") == "original\n"
    assert '"ok"' in (repo / "data" / "thread.json").read_text(encoding="utf-8")
    assert not (repo / "evil.txt").exists()


@pytest.mark.skipif(not shutil.which("git"), reason="git not on PATH")
def test_context_manager_restores_after_body(tmp_path: Path) -> None:
    repo = tmp_path
    _git_init(repo)
    (repo / "z.txt").write_text("z\n", encoding="utf-8")
    (repo / "data").mkdir()
    (repo / "data" / "a.json").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    with agent_git_sandbox(repo):
        (repo / "z.txt").write_text("changed\n", encoding="utf-8")
        (repo / "data" / "a.json").write_text("2\n", encoding="utf-8")

    assert (repo / "z.txt").read_text(encoding="utf-8") == "z\n"
    assert (repo / "data" / "a.json").read_text(encoding="utf-8") == "2\n"
