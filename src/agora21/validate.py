"""JSON Schema validation for thread documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agora21.paths import schema_path

_THREAD_SCHEMA = "thread-v1.0.0.schema.json"


def _validator() -> Draft202012Validator:
    p = schema_path(_THREAD_SCHEMA)
    schema = json.loads(p.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _validate_thread_raw(obj: dict[str, Any]) -> None:
    if obj.get("committee") == "wg21_only":
        _validator().validate({**obj, "committee": "wg21_all"})
    else:
        _validator().validate(obj)


def validate_thread_model(obj: dict[str, Any]) -> None:
    """Validate model/reviewer draft output against thread schema v1.0.0.

    Drafts may use ``producer_git_sha: \"\"``; :func:`~agora21.loop.finalize_thread_document`
    overwrites it with the repository git SHA before writing the final file.
    """
    _validate_thread_raw(obj)


def validate_thread(obj: dict[str, Any]) -> None:
    """Validate emitted thread JSON (schema v1.0.0, including ``producer_git_sha``)."""
    _validate_thread_raw(obj)


def validate_thread_file(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_thread(data)


def validation_error_message(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
