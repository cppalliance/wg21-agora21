"""Claude stream-json parsing."""

import json

from agora21.claude_stream import extract_json_value, json_from_assist, parse_stream_json_lines


def test_extract_json_value_embedded_in_prose() -> None:
    blob = 'Here you go:\n```json\n{"schema_version": "1.0.0", "paper_id": "n1"}\n```\ntrailing'
    j = extract_json_value(blob)
    assert isinstance(j, dict)
    assert j.get("paper_id") == "n1"


def test_extract_json_value_first_brace() -> None:
    s = 'Some text\n{"schema_version": "1.0.0", "paper_id": "p2", "x": 1}'
    j = extract_json_value(s)
    assert j and j.get("paper_id") == "p2"


def test_parse_stream_event_text_delta() -> None:
    full = '{"schema_version": "1.0.0", "paper_id": "n3", "mailing_id": "2026-02"}'
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
        json.dumps(
            {
                "type": "stream_event",
                "event": {"delta": {"type": "text_delta", "text": full}},
            }
        ),
    ]
    parsed = parse_stream_json_lines(lines)
    j = json_from_assist(parsed)
    assert isinstance(j, dict)
    assert j.get("paper_id") == "n3"


def test_parse_result_dict_direct() -> None:
    obj = {"schema_version": "1.0.0", "paper_id": "n4", "mailing_id": "2026-02"}
    lines = [json.dumps({"type": "result", "result": obj, "is_error": False})]
    parsed = parse_stream_json_lines(lines)
    assert json_from_assist(parsed) == obj
