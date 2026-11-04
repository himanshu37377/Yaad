"""Memory lifecycle: update taxonomy, provenance, and temporal reasoning.

Distinguishes new facts from corrections, contradictions, preferences,
temporary statements, uncertain observations, and supersessions so
memory evolves without silent overwrites. Temporal helpers separate
when an event occurred, when it was recorded, when it became
effective, and when it was superseded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

NEW_FACT = "new_fact"
CORRECTION = "correction"
CONTRADICTION = "contradiction"
PREFERENCE = "preference"
TEMPORARY = "temporary"
UNCERTAIN = "uncertain"
SUPERSESSION = "supersession"
RESTATED = "restated"

ORIGIN_DOCUMENT = "document"
ORIGIN_CONVERSATION = "conversation"
ORIGIN_MODEL = "model"

TRUST_ORDER = {ORIGIN_DOCUMENT: 3, ORIGIN_CONVERSATION: 2, ORIGIN_MODEL: 1}

_TEMPORARY_MARKERS = ("for now", "temporarily", "tentative", "provisional",
                      "this week only", "until further", "interim")
_UNCERTAIN_MARKERS = ("maybe", "might", "perhaps", "possibly", "unsure",
                      "not sure", "i think", "probably", "could be",
                      "propos", "suggest", "thinking of", "considering")
_PREFERENCE_MARKERS = ("prefer", "like ", "love ", "favorite", "favourite",
                       "i want", "please remember")
_CORRECTION_MARKERS = ("correction", "actually", "sorry,", "mistake",
                       "should be", "meant to say", "not anymore",
                       "no longer", "update:", "revised")
_SUPERSESSION_MARKERS = ("instead", "moved to", "changed to", "change to",
                         "increased to", "decreased to", "reduced to",
                         "raised to", "replaced", "superseded",
                         "rescheduled", "postponed to", "shifted to",
                         "pushed to", "new date", "new deadline",
                         "new venue", "new budget", "new fee")
_QUESTION_STARTERS = ("what", "when", "where", "who", "why", "how", "is",
                      "are", "do", "does", "can", "could", "show", "list")


@dataclass
class UpdateDecision:
    kind: str
    origin: str
    trust: int
    reason: str
    rival_record_id: int | None = None


def origin_of(path: str = "", run_id: str = "") -> str:
    if str(path or "").startswith("yaad-conversation://") or str(run_id).startswith("conversation"):
        return ORIGIN_CONVERSATION
    if str(run_id).startswith(("auto:", "extraction:", "manual:")) or path:
        return ORIGIN_DOCUMENT
    return ORIGIN_MODEL


def is_question_like(message: str) -> bool:
    text = (message or "").strip()
    if text.endswith("?"):
        return True
    first = (text.split() or [""])[0].casefold().rstrip("?,!")
    return first in _QUESTION_STARTERS


def classify_update(message: str, rival: dict | None = None,
                    same_values: bool = False) -> UpdateDecision:
    origin = ORIGIN_CONVERSATION
    trust = TRUST_ORDER[origin]
    q = f" {(message or '').casefold()} "
    if is_question_like(message):
        return UpdateDecision(RESTATED, origin, trust, "question: no durable update")
    if rival is not None and same_values:
        return UpdateDecision(RESTATED, origin, trust, "repetition of known values",
                              rival_record_id=rival.get("id"))
    # Explicit replacement language wins over hedges: "fee moved to
    # INR 200 instead" is a supersession even when it also says "maybe".
    if rival is not None and any(m in q for m in _SUPERSESSION_MARKERS):
        return UpdateDecision(SUPERSESSION, origin, trust,
                              "explicit replacement of known record",
                              rival_record_id=rival.get("id"))
    rival_id = rival.get("id") if rival is not None else None
    if any(m in q for m in _PREFERENCE_MARKERS):
        return UpdateDecision(PREFERENCE, origin, trust, "preference language",
                              rival_record_id=rival_id)
    if any(m in q for m in _TEMPORARY_MARKERS):
        return UpdateDecision(TEMPORARY, origin, trust, "temporary scope marker",
                              rival_record_id=rival_id)
    if any(m in q for m in _UNCERTAIN_MARKERS):
        return UpdateDecision(UNCERTAIN, origin, trust, "hedged observation",
                              rival_record_id=rival_id)
    if rival is not None:
        if any(m in q for m in _CORRECTION_MARKERS):
            return UpdateDecision(CORRECTION, origin, trust,
                                  "correction of known record",
                                  rival_record_id=rival.get("id"))
        return UpdateDecision(CONTRADICTION, origin, trust,
                              "rival values without replacement language",
                              rival_record_id=rival.get("id"))
    return UpdateDecision(NEW_FACT, origin, trust, "no known rival")


_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def parse_event_time(detail: str, date_value: str | None) -> str | None:
    if date_value and _ISO.match(date_value):
        return date_value
    match = _ISO.search(detail or "")
    return match.group(1) if match else None


def recorded_time(created_at: str | None) -> str | None:
    if not created_at:
        return None
    try:
        datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        return str(created_at)
    except ValueError:
        return None


def effective_time(record: dict, transitions: list[dict] | None = None) -> str | None:
    if record.get("status") == "superseded" and transitions:
        # Deterministic regardless of caller ordering: the LATEST
        # supersession is when the record stopped being effective. A
        # mere conflict never confers an effective time.
        supersessions = [tr for tr in transitions
                         if tr.get("transition") == "superseded"]
        if supersessions:
            latest = max(supersessions,
                         key=lambda tr: (tr.get("created_at") or "", tr.get("id") or 0))
            return latest.get("created_at") or record.get("created_at")
    return record.get("created_at")


def superseded_time(transitions: list[dict] | None) -> str | None:
    for tr in reversed(transitions or []):
        if tr.get("transition") == "superseded":
            return tr.get("created_at")
    return None


def temporal_summary(record: dict, transitions: list[dict] | None = None) -> dict:
    return {
        "event_time": parse_event_time(
            f"{record.get('title','')} {record.get('detail','')}", record.get("date_value")),
        "recorded_time": recorded_time(record.get("created_at")),
        "effective_time": effective_time(record, transitions),
        "superseded_time": superseded_time(transitions),
        "status": record.get("status"),
    }


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
