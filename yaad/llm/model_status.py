"""Explicit model-execution status for every model invocation path.

A deterministic fallback is legitimate engineering, but a fallback
the caller cannot distinguish from a model verdict is a silent
outage (observed live: the entailment model arm 400d on every call
while verdicts carried no trace of it). Every model stage reports
one of:

- ``model_verified``: the model was invoked, its response parsed and
  schema-validated, and the result applied.
- ``deterministic_only``: no model invocation was attempted (feature
  off, gate refused, or nothing to judge).
- ``model_error``: invocation was attempted and failed; deterministic
  output stands in, explicitly flagged.

Snapshots carry only transport metadata (model id, HTTP status,
token counts, error codes) -- never prompts, evidence, or keys.
"""
from __future__ import annotations

MODEL_VERIFIED = "model_verified"
DETERMINISTIC_ONLY = "deterministic_only"
MODEL_ERROR = "model_error"


def error_category(exc: BaseException) -> str:
    """Stable failure class for a model-call exception."""
    from yaad.llm.meta_api import MetaAPIError

    if isinstance(exc, MetaAPIError):
        return f"http_{exc.status_code}"
    from pydantic import ValidationError

    if isinstance(exc, ValidationError):
        return "response_validation"
    if isinstance(exc, (OSError, TimeoutError)):
        return "transport"
    if isinstance(exc, ValueError):
        return "malformed_response"
    return f"unexpected_{type(exc).__name__}"


def call_snapshot(client) -> dict:
    """Transport metadata from a client's ``last_call`` record."""
    info = dict(getattr(client, "last_call", {}) or {})
    return {
        "model": info.get("model"),
        "http_status": info.get("status_code"),
        "input_tokens": info.get("input_tokens"),
        "output_tokens": info.get("output_tokens"),
        "error_code": info.get("error_code"),
    }
