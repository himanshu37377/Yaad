"""Reliability guarantees: transport failures stay named, the ledger
stays append-only, and the memory planner stays singular. No network:
the Meta transport is stubbed at the request boundary.
"""
from pathlib import Path

import pytest

from yaad.config import Settings
from yaad.llm.meta_api import MetaAPIError
from yaad.llm.meta_client import MetaClient

ROOT = Path(__file__).parents[1]


def client(tmp_path):
    settings = Settings(db_path=tmp_path / "rel.sqlite3", cloud_enabled=True,
                        model_api_key="stub-key-for-tests")
    return MetaClient(settings)


def test_rate_limit_maps_to_named_error_with_receipt(tmp_path, monkeypatch):
    def limited(*args, **kwargs):
        raise MetaAPIError(429, {"error": {"code": "rate_limited"}}, 12, {})
    monkeypatch.setattr("yaad.llm.meta_client.request_json", limited)
    adapter = client(tmp_path)
    with pytest.raises(MetaAPIError):
        adapter.reason_json([], {"type": "object"}, "p")
    assert adapter.last_call["error_code"] == "rate_limited"
    assert adapter.last_call["success"] is False
    assert adapter.last_call["status_code"] == 429


def test_server_error_without_code_maps_to_http_status(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise MetaAPIError(500, {}, 9, {})
    monkeypatch.setattr("yaad.llm.meta_client.request_json", broken)
    adapter = client(tmp_path)
    with pytest.raises(MetaAPIError):
        adapter.reason_json([], {"type": "object"}, "p")
    assert adapter.last_call["error_code"] == "http_500"


def test_timeout_maps_to_named_error(tmp_path, monkeypatch):
    def slow(*args, **kwargs):
        raise TimeoutError("timed out")
    monkeypatch.setattr("yaad.llm.meta_client.request_json", slow)
    adapter = client(tmp_path)
    with pytest.raises(TimeoutError):
        adapter.reason_json([], {"type": "object"}, "p")
    assert adapter.last_call["error_code"] == "TimeoutError"
    assert adapter.last_call["success"] is False


def test_ledger_has_no_update_or_delete_statements():
    """memory_transitions is append-only by construction: only INSERTs."""
    offenders = []
    for path in (ROOT / "yaad").rglob("*.py"):
        text = path.read_text()
        if "UPDATE memory_transitions" in text or "DELETE FROM memory_transitions" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_single_plan_memory_definition():
    """The historic shadowed duplicate must never return."""
    source = (ROOT / "yaad" / "query" / "memory_answer.py").read_text()
    assert source.count("\ndef plan_memory(") == 1
