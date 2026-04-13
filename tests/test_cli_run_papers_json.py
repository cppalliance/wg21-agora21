"""``run --papers-json`` skips fetch and uses explicit ids."""

import argparse
import json
from pathlib import Path

import pytest

from agora21.cli import cmd_run


def test_cmd_run_papers_json_dry_run(fake_agora21_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = fake_agora21_repo / "batch-out"
    ns = argparse.Namespace(
        mailing_id="2026-02",
        output_dir=str(out),
        max_cap=99,
        max_processes=1,
        dry_run=True,
        papers_json='["p0001r0","p0002r0"]',
    )
    assert cmd_run(ns) == 0
    assert (out / "p0001r0" / "thread.json").is_file()
    assert (out / "p0002r0" / "thread.json").is_file()
    idx = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert idx["mailing_id"] == "2026-02"
    assert idx["total_papers"] == 2
    assert idx["succeeded"] == 2


def test_cmd_run_papers_json_invalid_returns_2(fake_agora21_repo: Path) -> None:
    ns = argparse.Namespace(
        mailing_id="2026-02",
        output_dir=str(fake_agora21_repo / "out"),
        max_cap=0,
        max_processes=1,
        dry_run=True,
        papers_json="not-json",
    )
    assert cmd_run(ns) == 2
