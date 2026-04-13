"""Load defaults.yaml with env overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in overrides.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "defaults.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("defaults.yaml must be a mapping")
    env_overlay: dict[str, Any] = {}
    if os.environ.get("AUTHOR_MODEL"):
        env_overlay.setdefault("author", {})["model"] = os.environ["AUTHOR_MODEL"]
    if os.environ.get("REVIEWER_MODEL"):
        env_overlay.setdefault("reviewer", {})["model"] = os.environ["REVIEWER_MODEL"]
    if os.environ.get("AUTHOR_TIMEOUT_S"):
        env_overlay.setdefault("author", {})["timeout_s"] = int(os.environ["AUTHOR_TIMEOUT_S"])
    if os.environ.get("REVIEWER_TIMEOUT_S"):
        env_overlay.setdefault("reviewer", {})["timeout_s"] = int(os.environ["REVIEWER_TIMEOUT_S"])
    if os.environ.get("MAX_REVIEW_ROUNDS"):
        env_overlay.setdefault("loop", {})["max_rounds"] = int(os.environ["MAX_REVIEW_ROUNDS"])
    return _deep_merge(raw, env_overlay)
