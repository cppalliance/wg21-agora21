"""repo_root() must find the checkout even when the package lives under site-packages."""

from pathlib import Path

import agora21.paths as paths


def test_repo_root_contains_pyproject_and_schemas() -> None:
    root = paths.repo_root()
    assert (root / "pyproject.toml").is_file()
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "agora21"' in text
    assert (root / "schemas").is_dir()


def test_repo_root_respects_agora21_repo_root(fake_paperlint_repo: Path) -> None:
    assert paths.repo_root() == fake_paperlint_repo.resolve()
