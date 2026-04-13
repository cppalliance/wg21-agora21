"""credentials: .env parsing without python-dotenv."""

import os
from pathlib import Path

import pytest

from agora21.credentials import _parse_dotenv_file, ensure_api_key


def test_parse_dotenv_file_basic(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "# c\n"
        "export ANTHROPIC_API_KEY=sk-test\n"
        "OPENROUTER_API_KEY=\n"
        "IGNORED=1\n",
        encoding="utf-8",
    )
    d = _parse_dotenv_file(p)
    assert d["ANTHROPIC_API_KEY"] == "sk-test"
    assert d["OPENROUTER_API_KEY"] == ""
    assert d["IGNORED"] == "1"


def test_parse_dotenv_file_bom(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_bytes('\ufeffANTHROPIC_API_KEY=abc\n'.encode("utf-8"))
    d = _parse_dotenv_file(p)
    assert d["ANTHROPIC_API_KEY"] == "abc"


def test_parse_dotenv_file_quotes(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text('FOO="x y"\n', encoding="utf-8")
    d = _parse_dotenv_file(p)
    assert d["FOO"] == "x y"


def _clear_claude_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
    ):
        monkeypatch.delenv(k, raising=False)


def test_ensure_api_key_direct_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_claude_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-direct")
    ensure_api_key()


def test_ensure_api_key_openrouter_requires_anthropic_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_claude_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret")
    with pytest.raises(ValueError, match="ANTHROPIC_BASE_URL"):
        ensure_api_key()


def test_ensure_api_key_openrouter_sets_auth_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_claude_env(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://openrouter.ai/api")
    ensure_api_key()
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "or-secret"
    assert os.environ["ANTHROPIC_API_KEY"] == ""
