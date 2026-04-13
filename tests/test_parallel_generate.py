"""Unit tests for parallel generation helpers (no git worktrees in these tests)."""

import tempfile
from pathlib import Path

import pytest

from agora21.parallel_generate import (
    max_workers_from_config,
    papers_from_json_arg,
    run_parallel_eval,
)
from agora21.paths import repo_root


def test_papers_from_json_arg():
    assert papers_from_json_arg(None) == []
    assert papers_from_json_arg("") == []
    assert papers_from_json_arg("[]") == []
    assert papers_from_json_arg('["a", "b"]') == ["a", "b"]


def test_papers_from_json_invalid():
    with pytest.raises(ValueError, match="JSON array"):
        papers_from_json_arg('{"x":1}')


def test_max_workers_from_config():
    assert max_workers_from_config({"parallel": {"max_workers": 7}}) == 7
    assert max_workers_from_config({}) == 10


def test_run_parallel_eval_empty():
    out = Path(tempfile.mkdtemp())
    assert run_parallel_eval(repo_root(), [], mailing_id="2026-02", output_dir=out, max_workers=3, dry_run=False) == {}
