"""Minimal Claude Code ``stream-json`` parsing (subset of pragma-agent ``claude_code.py``)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def parse_stream_json_lines(lines: list[str]) -> dict[str, Any]:
    """Return shape compatible with downstream JSON extraction."""
    out: dict[str, Any] = {
        "text_blocks": [],
        "result_json": None,
        "session_id": None,
        "model": None,
        "stderr_hint": "",
    }
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        et = event.get("type")
        if et == "system" and event.get("subtype") == "init":
            out["session_id"] = event.get("session_id") or event.get("sessionId")
            out["model"] = event.get("model")
        elif et == "assistant":
            msg = event.get("message", {})
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                out["text_blocks"].append(content)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        t = block.get("text", "")
                        if t:
                            out["text_blocks"].append(t)
        elif et == "stream_event":
            # Headless CLI streams deltas; shape may be .event.delta or top-level .delta (see Claude Code stream-json).
            ev = event.get("event") or {}
            delta = ev.get("delta") or event.get("delta") or {}
            if delta.get("type") == "text_delta":
                t = delta.get("text")
                if isinstance(t, str) and t:
                    out["text_blocks"].append(t)
        elif et == "result":
            if event.get("is_error"):
                err = event.get("error") or event.get("message") or "unknown error"
                out["stderr_hint"] = str(err)[:2000]
            rt = event.get("result")
            if rt is None:
                continue
            if isinstance(rt, dict):
                out["result_json"] = rt
            elif isinstance(rt, str):
                try:
                    out["result_json"] = json.loads(rt)
                except json.JSONDecodeError:
                    out["result_json"] = {"text": rt}
            else:
                out["result_json"] = {"text": str(rt)}
    if out["result_json"] is None and out["text_blocks"]:
        out["result_json"] = {"text": "\n".join(out["text_blocks"])}
    return out


_FENCE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.I)


def json_from_assist(parsed: dict[str, Any]) -> Any | None:
    """Pull a JSON object from Claude ``stream-json`` parse result."""
    rj = parsed.get("result_json")
    if isinstance(rj, dict) and "text" not in rj:
        return rj
    if isinstance(rj, dict) and "text" in rj:
        text = rj.get("text") or ""
        j = extract_json_value(str(text))
        if j is not None:
            return j
    blocks = parsed.get("text_blocks") or []
    if blocks:
        joined = "\n".join(blocks)
        j = extract_json_value(joined)
        if j is not None:
            return j
        for block in reversed(blocks):
            j = extract_json_value(block)
            if j is not None:
                return j
    return None


def extract_json_value(text: str) -> Any | None:
    """Extract the first JSON **object** from prose, fences, or partial stream text."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                return json.JSONDecoder().raw_decode(text)[0]
            except json.JSONDecodeError:
                pass
    m = _FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            logger.debug("fence json parse failed")
    dec = json.JSONDecoder()
    start = 0
    while True:
        i = text.find("{", start)
        if i < 0:
            break
        try:
            obj, _ = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        start = i + 1
    return None
