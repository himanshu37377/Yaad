"""Strict structured-output schema compatibility (offline gate).

The Meta API's ``json_schema`` strict mode rejects (HTTP 400, observed
live) any schema whose ``required`` array omits a key present in
``properties``. This suite pins every production schema sent with
``strict: True`` to the wire contract in ``yaad.llm.strict_schema``,
so a regression fails here instead of as a billed-or-burning live
400. No model calls; no network.
"""
import pytest
from pydantic import BaseModel, Field
from typing import Literal

from yaad.llm.strict_schema import (
    assert_strict_schema,
    strict_violations,
    to_strict_schema,
)
from yaad.memory.extract import (
    EXTRACTION_SCHEMA,
    LINK_VERIFICATION_SCHEMA,
    SECOND_LOOK_SCHEMA,
)
from yaad.scan.worker import VISION_SCHEMA
from yaad.query.answer import ANSWER_REVIEW_SCHEMA
from yaad.llm.meta_client import (
    JOINT_IMAGE_COMPARISON_SCHEMA,
    PDF_UNDERSTANDING_SCHEMA,
    PHOTO_DESCRIPTION_SCHEMA,
)
from yaad.query.verify import ENTAILMENT_SCHEMA
from yaad.query.pipeline import (
    CANDIDATE_SCHEMA,
    CHRONOLOGY_SCHEMA,
    EVIDENCE_CLASSIFICATION_SCHEMA,
    QUERY_ANALYSIS_SCHEMA,
    VERIFICATION_SCHEMA,
)

PRODUCTION_SCHEMAS = {
    "extraction": EXTRACTION_SCHEMA,
    "link-verification": LINK_VERIFICATION_SCHEMA,
    "link-second-look": SECOND_LOOK_SCHEMA,
    "vision": VISION_SCHEMA,
    "answer-review": ANSWER_REVIEW_SCHEMA,
    "pdf-understanding": PDF_UNDERSTANDING_SCHEMA,
    "photo-description": PHOTO_DESCRIPTION_SCHEMA,
    "joint-image-comparison": JOINT_IMAGE_COMPARISON_SCHEMA,
    "claim-entailment": ENTAILMENT_SCHEMA,
    "query-analysis": QUERY_ANALYSIS_SCHEMA,
    "evidence-classification": EVIDENCE_CLASSIFICATION_SCHEMA,
    "chronology": CHRONOLOGY_SCHEMA,
    "candidate-answer": CANDIDATE_SCHEMA,
    "claim-verification": VERIFICATION_SCHEMA,
}


def test_all_production_schemas_strict_compliant():
    failures = {name: strict_violations(schema)
                for name, schema in PRODUCTION_SCHEMAS.items()}
    failures = {name: problems for name, problems in failures.items() if problems}
    assert not failures, "non-compliant production schemas: " + repr(failures)


def test_production_registry_is_complete():
    # Fourteen strict-mode call sites ship in the codebase (thirteen
    # wired plus joint-image-comparison, implemented and live-proven
    # but awaiting an explicit invocation trigger); the registry must
    # cover every one of them.
    assert len(PRODUCTION_SCHEMAS) == 14


class _Nested(BaseModel):
    verdict: Literal["supported", "contradicted"]
    reason: str = ""
    model_config = {"extra": "forbid"}


class _Wrapper(BaseModel):
    items: list[_Nested] = Field(default_factory=list)
    note: str | None = None
    model_config = {"extra": "forbid"}


def test_transformer_inlines_refs_and_completes_required():
    strict = to_strict_schema(_Wrapper)
    assert strict_violations(strict) == []
    assert strict["required"] == ["items", "note"]
    item = strict["properties"]["items"]["items"]
    assert item["required"] == ["reason", "verdict"]
    assert item["properties"]["verdict"] == {
        "type": "string", "enum": ["supported", "contradicted"]}
    assert "$defs" not in strict


def test_transformer_nullable_union():
    strict = to_strict_schema(_Wrapper)
    assert strict["properties"]["note"] == {"type": ["string", "null"]}


def test_transformer_rejects_non_null_union():
    class _Bad(BaseModel):
        value: str | int
        model_config = {"extra": "forbid"}

    with pytest.raises(ValueError):
        to_strict_schema(_Bad)


def test_validator_catches_missing_required():
    schema = {"type": "object", "properties": {"a": {"type": "string"}},
              "required": [], "additionalProperties": False}
    assert any("missing: ['a']" in problem for problem in strict_violations(schema))


def test_validator_catches_refs_and_exotic_keywords():
    schema = {"type": "object",
              "properties": {"v": {"type": "string", "pattern": "^a|b$"}},
              "required": ["v"], "additionalProperties": False,
              "$defs": {}}
    problems = strict_violations(schema)
    assert any("$defs" in problem for problem in problems)
    assert any("pattern" in problem for problem in problems)


def test_wire_schema_roundtrip_preserves_validation_contract():
    from yaad.query.verify import EntailmentResult
    strict = to_strict_schema(EntailmentResult)
    assert strict["properties"]["items"]["items"]["properties"]["verdict"] == {
        "type": "string", "enum": ["supported", "contradicted", "insufficient"]}
    parsed = EntailmentResult.model_validate(
        {"items": [{"index": 0, "verdict": "supported", "reason": "quoted"}]})
    assert parsed.items[0].verdict == "supported"
    with pytest.raises(Exception):
        EntailmentResult.model_validate(
            {"items": [{"index": 0, "verdict": "maybe", "reason": "x"}]})


def test_no_production_schema_uses_references():
    import json
    for name, schema in PRODUCTION_SCHEMAS.items():
        blob = json.dumps(schema)
        assert "$ref" not in blob, name
        assert "$defs" not in blob, name
