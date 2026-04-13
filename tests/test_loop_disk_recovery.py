"""Recover thread JSON from disk when Claude wrote the file but stream parse failed."""

import json
import os
import time
from pathlib import Path

import pytest

from agora21.loop import try_recover_thread_from_disk


@pytest.fixture()
def minimal_thread_obj() -> dict:
    return {
        "schema_version": "1.0.0",
        "producer_git_sha": "",
        "paper_id": "p0001r0",
        "mailing_id": "2026-02",
        "title": "Test",
        "committee": "wg21_all",
        "heat_tier": "cold",
        "submission": {
            "title": "t",
            "body_markdown": "b",
            "author": "u/x",
            "flair": None,
            "score": 1,
        },
        "comments": [],
        "promoted": [],
    }


def test_recover_fresh_file(tmp_path: Path, minimal_thread_obj: dict) -> None:
    output_base = tmp_path
    p = output_base / "p0001r0" / "thread.json"
    p.parent.mkdir(parents=True)
    t0 = time.time()
    time.sleep(0.05)
    p.write_text(json.dumps(minimal_thread_obj) + "\n", encoding="utf-8")
    got = try_recover_thread_from_disk(output_base, "p0001r0", not_before=t0)
    assert got is not None
    assert got["paper_id"] == "p0001r0"


def test_recover_rejects_stale_mtime(tmp_path: Path, minimal_thread_obj: dict) -> None:
    output_base = tmp_path
    p = output_base / "p0001r0" / "thread.json"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(minimal_thread_obj) + "\n", encoding="utf-8")
    old = time.time() - 100.0
    os.utime(p, (old, old))
    got = try_recover_thread_from_disk(output_base, "p0001r0", not_before=time.time())
    assert got is None
