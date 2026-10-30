import pytest

from scripts.meta_capability_suite import parse_json_output


def test_pdf_json_validator_accepts_markdown_fence():
    result = parse_json_output('```json\n{"application_deadline":"2026-11-12"}\n```')
    assert result == {"application_deadline": "2026-11-12"}


def test_pdf_json_validator_rejects_truncated_json():
    with pytest.raises(ValueError):
        parse_json_output('{"application_deadline":')
