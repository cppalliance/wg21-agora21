"""Schema validation."""

import json

import pytest

from agora21.paths import repo_root
from agora21.validate import validate_thread


def test_fixture_thread():
    root = repo_root()
    p = root / "tests" / "fixtures" / "thread-minimal.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    validate_thread(data)


def test_reject_bad_heat():
    root = repo_root()
    p = root / "tests" / "fixtures" / "thread-minimal.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["heat_tier"] = "invalid"
    with pytest.raises(Exception):
        validate_thread(data)
