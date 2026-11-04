"""PII redaction before external model egress (offline, stubbed client).

Long digit runs (account/phone/identity numbers) must not travel to
external providers in cleartext. Redaction applies to egress-bound
copies only: grounding and validation always check the original
indexed text, and local (on-device) requests are never redacted.
"""
from test_memory_answers import Store

import yaad.llm.meta_client as meta_client
from yaad.config import Settings
from yaad.privacy.masking import is_sensitive, mask_value, redact_text
from yaad.query.answer import answer_question


def test_redact_text_masks_long_digit_runs():
    redacted, count = redact_text("Account 123456789012, call 555-1234.")
    assert "123456789012" not in redacted
    assert "••••9012" in redacted
    assert "555-1234" in redacted  # short runs are not identifiers
    assert count == 1


def test_redact_text_leaves_plain_text_untouched():
    redacted, count = redact_text("The workshop fee is Rs 150 per person.")
    assert (redacted, count) == ("The workshop fee is Rs 150 per person.", 0)


def test_mask_value_keeps_last_four():
    assert mask_value("1234567890") == "••••7890"
    assert mask_value("abc") == "••••"


class _CapturingReview:
    payload: dict = {}
    seen_texts: list = []
    last_call = {"provider": "Meta Model API", "model": "muse-spark-1.3",
                 "status_code": 200, "success": True, "duration_ms": 3,
                 "input_tokens": 100, "output_tokens": 40, "cached_tokens": 0,
                 "estimated_cost_usd": 0.001}

    def __init__(self, _settings):
        pass

    def reason_json(self, messages, schema, prompt_id):
        import json as _json
        body = _json.loads(messages[0]["content"])
        type(self).seen_texts = [item["text"] for item in body["sources"]]
        return type(self).payload


def _sensitive_store(tmp_path, cloud_enabled=True):
    store = Store(tmp_path / "pii.sqlite3")
    store.settings = Settings(db_path=tmp_path / "pii.sqlite3",
                              cloud_enabled=cloud_enabled,
                              model_api_key="stub-key-for-tests")
    store.add_file("admit.txt",
                   [("ev_pii", "Admit card: roll number 9876543210, hall B.")])
    return store


def test_cloud_review_egress_is_redacted(tmp_path, monkeypatch):
    store = _sensitive_store(tmp_path)
    _CapturingReview.payload = {
        "status": "answered", "answer": "The hall is B.",
        "claims": [{"claim": "The hall is B.", "evidence_ids": ["ev_pii"]}],
    }
    _CapturingReview.seen_texts = []
    monkeypatch.setattr(meta_client, "MetaClient", _CapturingReview)
    result = answer_question(store.db, "Which hall is on the admit card?",
                             store.folder_id, store.settings)
    assert _CapturingReview.seen_texts, "review must have run"
    assert all("9876543210" not in text for text in _CapturingReview.seen_texts)
    assert any("••••3210" in text for text in _CapturingReview.seen_texts)
    assert result["status"] in ("answered", "unverified")


def test_local_review_egress_is_not_redacted(tmp_path, monkeypatch):
    store = _sensitive_store(tmp_path)
    store.settings.model_provider = "local"
    _CapturingReview.payload = {
        "status": "answered", "answer": "The hall is B.",
        "claims": [{"claim": "The hall is B.", "evidence_ids": ["ev_pii"]}],
    }
    _CapturingReview.seen_texts = []
    import yaad.llm.local_client as local_client
    monkeypatch.setattr(local_client, "LocalClient", _CapturingReview)
    answer_question(store.db, "Which hall is on the admit card?",
                    store.folder_id, store.settings)
    assert _CapturingReview.seen_texts, "review must have run"
    assert any("9876543210" in text for text in _CapturingReview.seen_texts)


def test_grounding_checks_originals_not_redacted_copies(tmp_path, monkeypatch):
    """Grounding must compare model claims against the original indexed
    text: redaction hides digits from the model, never from validation."""
    store = _sensitive_store(tmp_path)
    _CapturingReview.payload = {
        "status": "answered", "answer": "The roll number is 9876543210.",
        "claims": [{"claim": "The roll number is 9876543210.",
                    "evidence_ids": ["ev_pii"]}],
    }
    _CapturingReview.seen_texts = []
    monkeypatch.setattr(meta_client, "MetaClient", _CapturingReview)
    result = answer_question(store.db, "What is the roll number?",
                             store.folder_id, store.settings)
    # The exact-number claim is grounded in the original unit text.
    assert result["status"] == "answered"
