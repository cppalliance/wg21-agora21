"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fake_agora21_repo(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Minimal repo root with ``pyproject.toml`` name ``agora21`` for ``AGORA21_REPO_ROOT`` tests."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "agora21"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    css_dir = tmp_path / "static" / "css"
    css_dir.mkdir(parents=True)
    (css_dir / "reddit-index.css").write_text(":root { --bg-canvas: #030303; }\n", encoding="utf-8")
    (css_dir / "reddit-thread.css").write_text(":root { --bg-canvas: #030303; }\n", encoding="utf-8")
    monkeypatch.setenv("AGORA21_REPO_ROOT", str(tmp_path))
    return tmp_path


# Legacy alias for tests still referencing the old fixture name.
@pytest.fixture
def fake_paperlint_repo(fake_agora21_repo: Path) -> Path:
    return fake_agora21_repo
