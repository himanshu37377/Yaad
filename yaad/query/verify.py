"""Claim-level entailment verification (deterministic floor + model schema).

Deterministic vetoes first (citation existence, excerpt containment,
numeric/date consistency, negation, chronology, injection markers);
an optional separate model call judges semantic entailment for the
survivors. The model can only downgrade to contradicted/insufficient
or confirm; it can never invent evidence. Unavailable verification
fails closed to insufficient.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from yaad.llm.model_status import (
    DETERMINISTIC_ONLY,
    MODEL_ERROR,
    MODEL_VERIFIED,
    call_snapshot,
    error_category,
)
from yaad.llm.strict_schema import to_strict_schema
from yaad.privacy.masking import mask_value

try:  # Preferred redactor when the privacy layer provides it.
    from yaad.privacy.masking import redact_text as _masking_redact_text
except ImportError:
    _masking_redact_text = None

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
INSUFFICIENT = "insufficient"


class EntailmentItem(BaseModel):
    index: int = Field(ge=0, le=100)
    verdict: Literal["supported", "contradicted", "insufficient"]
    reason: str = Field(default="", max_length=500)
    model_config = {"extra": "forbid"}


class EntailmentResult(BaseModel):
    items: list[EntailmentItem] = Field(default_factory=list, max_length=50)
    model_config = {"extra": "forbid"}


ENTAILMENT_SCHEMA = to_strict_schema(EntailmentResult)

_CLAIM_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_NEGATIONS = (" not ", " never ", " no ", "n't ", "cannot", "without",
              "refuse", "cancelled", "canceled")
_INJECTION_MARKERS = ("ignore previous", "ignore all previous", "system prompt",
                      "you are now", "disregard", "override", "reveal your",
                      "jailbreak", "do anything now")
_CAUSAL_MARKERS = ("because", "therefore", "thus", "hence", "caused by",
                   "as a result", "led to", "due to")
_CHANGE_VERBS = ("moved", "rescheduled", "changed", "shifted", "revised")
_NOCHANGE_PHRASES = ("unchanged", "same as before", "has not changed",
                     "have not changed", "did not change", "still the")


@dataclass
class ClaimCheck:
    index: int
    text: str
    evidence_ids: list[str]
    verdict: str
    reason: str
    model_used: bool = False


@dataclass
class VerifyReport:
    claims: list[ClaimCheck] = field(default_factory=list)
    ms: float = 0.0
    model_calls: int = 0
    status: str = DETERMINISTIC_ONLY
    model_error: str | None = None
    model_info: dict = field(default_factory=dict)


VERIFY_PURPOSE = "pipeline verify"
VERIFY_PROMPT_ID = "claim-entailment-v2"

# v2 model instructions: verdict definitions plus the four rules that
# replace sampling variance on the frozen flip set (definite-NP
# bridging, change-vs-unchanged, single-role exclusivity, quoted
# reasons). Rule 3 is the conservative variant: bare "X presenting"
# leaves "Y will present" insufficient, never contradicted.
VERIFY_USER_INSTRUCTIONS = (
    "For each item, judge whether the CLAIM follows from its EVIDENCE excerpts only. "
    "Verdicts: "
    "supported: EVERY predicate of the claim is stated in, or holds in every reading of, "
    "the excerpts; paraphrase with the same truth conditions counts as stated. "
    "contradicted: the excerpts entail the negation of at least one claim predicate. "
    "insufficient: anything else, including unresolved reference and claims the excerpts "
    "neither state nor rule out. "
    "Rule 1. Reference: a definite description in the claim (\"the celebration\", "
    "\"the gathering\") counts as the evidence's named entity only if the evidence names "
    "exactly one candidate entity AND every other predicate of the claim matches that "
    "entity's excerpts. Otherwise insufficient. "
    "Rule 2. Change: a change-of-state verb in the evidence (moved, rescheduled, changed, "
    "shifted, revised to) about a slot contradicts a claim that the slot is unchanged / "
    "the same / still the old value. No original value is needed: a move entails a change. "
    "Rule 3. Exclusivity: one named person doing a role contradicts a claim that a "
    "different named person will do that same role only if the evidence marks the role "
    "as single-holder (only, sole, single, \"will present\" with exactly one presenter "
    "named for one event). Bare \"X presenting\" leaves \"Y will present\" insufficient, "
    "never contradicted. "
    "Rule 4. Quote the decisive excerpt span in reason (at most 200 chars) plus the rule "
    "number used. "
    "Example A (supported, rule 1): claim \"The celebration takes place at Riverside Hall\" "
    "+ evidence \"Autumn Fest 2026 will be held at Riverside Hall\" -> supported: sole "
    "candidate entity, venue predicate matches. "
    "Example B (contradicted, rule 2): claim \"The demo time is unchanged\" + evidence "
    "\"Sprint demo moved to Friday 5 PM\" -> contradicted: a move entails a change."
)

# Payload cap: at most 2 evidence IDs x 600 chars per claim.
VERIFY_EVIDENCE_IDS_PER_CLAIM = 2
VERIFY_EXCERPT_CHARS = 600

# Pre-call estimate assumes this many output tokens. Recalibrated from
# 256: six live receipts measured 930-1310 output tokens per batched
# call. Fail-safe direction (estimates rise toward actuals; the
# per-call ceiling still binds an order of magnitude above).
VERIFY_EXPECTED_OUTPUT_TOKENS = 1000

# Fallback per-call ceiling (USD) when neither
# ``pipeline_stage_max_spend_usd`` nor ``review_max_spend_usd`` is
# configured.
DEFAULT_PIPELINE_STAGE_MAX_SPEND_USD = 0.05

_DIGIT_RUN = re.compile(r"\b\d{7,}\b")


def build_verify_payload(question: str, checks: list[ClaimCheck],
                         unit_text_by_id: dict[str, str], local: bool = False) -> dict:
    """Capped model-bound payload: per claim at most 2 evidence IDs with
    at most 600 chars each. Ordering is preserved; originals are never
    mutated. Cloud-bound excerpts are redacted; local payloads travel
    verbatim (local computation is not egress). Citation validation
    always runs against the originals, never redacted text."""
    capped = []
    for check in checks or []:
        ids = list(dict.fromkeys(str(e) for e in (check.evidence_ids or [])))
        ids = ids[:VERIFY_EVIDENCE_IDS_PER_CLAIM]
        capped.append({
            "index": check.index,
            "statement": str(check.text or "")[:VERIFY_EXCERPT_CHARS],
            "evidence_ids": ids,
            "excerpts": {eid: redact_for_cloud(
                str((unit_text_by_id or {}).get(eid) or "")[:VERIFY_EXCERPT_CHARS],
                local) for eid in ids},
        })
    return {"question": str(question or ""), "claims": capped}


def payload_chars(payload: dict) -> int:
    """Characters of the JSON actually sent to the model."""
    import json
    return len(json.dumps(payload, ensure_ascii=False))


def precall_estimate_usd(model: str, chars: int) -> float | None:
    """Real pre-call cost estimate: ``chars // 4`` input tokens plus the
    documented expected-output budget, priced through the shared
    ``estimate_text_cost_usd`` helper. None when the model is unpriced
    (fail-closed: the caller must refuse)."""
    from yaad.llm.meta_config import MODEL_PRICES_PER_MILLION, estimate_text_cost_usd
    if model not in MODEL_PRICES_PER_MILLION:
        return None
    return estimate_text_cost_usd(model, {
        "input_tokens": max(1, int(chars) // 4),
        "output_tokens": VERIFY_EXPECTED_OUTPUT_TOKENS,
    })


def stage_spend_ceiling_usd(settings) -> float:
    """Per-call ceiling: ``pipeline_stage_max_spend_usd``, else
    ``review_max_spend_usd``, else the built-in default. Unparseable
    values fall back to the default; negatives clamp to 0 (refuse all)."""
    for name in ("pipeline_stage_max_spend_usd", "review_max_spend_usd"):
        raw = getattr(settings, name, None)
        if raw is None:
            continue
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return DEFAULT_PIPELINE_STAGE_MAX_SPEND_USD
    return DEFAULT_PIPELINE_STAGE_MAX_SPEND_USD


def spend_refusal(estimate_usd: float | None, ceiling_usd: float) -> str | None:
    """Refusal message when the call must not proceed, else None. Callers
    must pass the REAL pre-call estimate, never 0.0: a zero estimate
    would silently waive the ceiling."""
    if estimate_usd is None:
        return "cannot estimate cost for an unpriced model; refusing the model call"
    if estimate_usd > ceiling_usd:
        return (f"estimated ${estimate_usd:.4f} exceeds the per-call ceiling "
                f"${ceiling_usd:.4f}; refusing the model call")
    return None


def verify_consent(settings, rows) -> tuple[bool, str]:
    """Folder-consent check: exactly the pipeline gate, reused (not
    reimplemented) so the two can never drift apart."""
    from yaad.query.pipeline import cloud_allowed
    return cloud_allowed(settings, rows)


def _fallback_redact_text(text: str) -> str:
    """Minimal deterministic redactor used only when
    ``masking.redact_text`` does not exist: masks long digit runs (the
    ``is_sensitive`` digit rule) via ``mask_value``."""
    return _DIGIT_RUN.sub(lambda m: mask_value(m.group(0)), text or "")


def redact_for_cloud(text: str, local: bool) -> str:
    """Cloud-bound excerpts are redacted; the local path travels verbatim
    (local computation is not egress)."""
    if local:
        return text or ""
    if callable(_masking_redact_text):
        redacted, _ = _masking_redact_text(text or "")
        return str(redacted)
    return _fallback_redact_text(text)


def _model_votes(settings) -> int:
    """Ballot size for voted verification (default 1 = legacy single
    call). Clamped to 1..5; unparseable values fail safe to 1."""
    try:
        votes = int(getattr(settings, "verify_model_votes", 1) or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(votes, 5))


def _majority_verdict(verdicts: list[str], votes: int) -> str | None:
    """Self-consistency rule. Single vote: the ballot as-is (legacy).
    Multi-vote: a verdict needs 2+ agreeing ballots AND no opposing
    decisive ballot (a supported/contradicted split is genuine model
    disagreement, never a decision). No majority -> None, and the
    deterministic verdict stands."""
    if votes == 1:
        return verdicts[0] if len(verdicts) == 1 else None
    if verdicts.count(SUPPORTED) >= 2 and CONTRADICTED not in verdicts:
        return SUPPORTED
    if verdicts.count(CONTRADICTED) >= 2 and SUPPORTED not in verdicts:
        return CONTRADICTED
    if verdicts.count(INSUFFICIENT) >= 2:
        return INSUFFICIENT
    return None


def _write_receipt(db, purpose: str, content: bytes, content_kind: str,
                   info: dict, settings, local: bool) -> int | None:
    """One ``egress_log`` row per model attempt, mirroring the answer
    layer's columns and success encoding (None preserved, else int)."""
    provider = info.get("provider") or ("local" if local else "Meta Model API")
    model = info.get("model") or (settings.local_model if local else settings.model)
    with db.transaction() as con:
        cursor = con.execute(
            """INSERT INTO egress_log(file_id,unit_id,purpose,provider,model,bytes,content_kind,
                 status_code,success,duration_ms,input_tokens,output_tokens,cached_tokens,
                 estimated_cost_usd,error_code)
               VALUES(NULL,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (purpose, provider, model, len(content), content_kind,
             info.get("status_code"),
             None if info.get("success") is None else int(info["success"]),
             info.get("duration_ms"), info.get("input_tokens"),
             info.get("output_tokens"), info.get("cached_tokens"),
             info.get("estimated_cost_usd"), info.get("error_code")))
        return cursor.lastrowid


def split_claims(answer: str) -> list[str]:
    parts = [p.strip() for p in _CLAIM_SPLIT.split((answer or "").strip()) if p.strip()]
    if not parts and (answer or "").strip():
        return [(answer or "").strip()]
    return [p[:800] for p in parts][:12]


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUMBER.finditer(text or "")}


def _dates(text: str) -> set[str]:
    found = set(re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text or ""))
    found.update(m.group(0).casefold() for m in re.finditer(
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
        r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\b",
        text or "", re.I))
    return found


def deterministic_check(claim: str, evidence_texts: list[str]) -> tuple[str, str]:
    lowered = f" {claim.casefold()} "
    if any(m in lowered for m in _INJECTION_MARKERS):
        return CONTRADICTED, "prompt-injection marker in claim"
    if not evidence_texts:
        return INSUFFICIENT, "no evidence supplied"
    blob = "\n".join(evidence_texts)
    blob_fold = blob.casefold()
    for num in _numbers(claim):
        if num not in {n.replace(",", "") for n in _numbers(blob)}:
            return CONTRADICTED, f"number {num} not in evidence"
    for date in _dates(claim):
        if date not in _dates(blob):
            return CONTRADICTED, f"date {date} not in evidence"
    claim_neg = any(n in lowered for n in _NEGATIONS)
    ev_neg = any(n in f" {blob_fold} " for n in _NEGATIONS)
    if claim_neg != ev_neg:
        content = set(re.findall(r"[\w-]{4,}", lowered)) & set(re.findall(r"[\w-]{4,}", blob_fold))
        if len(content) >= 2:
            return CONTRADICTED, "negation polarity differs from evidence"
    ev_words = set(re.findall(r"[\w-]+", blob_fold))
    if any(p in lowered for p in _NOCHANGE_PHRASES) \
            and not any(p in blob_fold for p in _NOCHANGE_PHRASES) \
            and any(v in ev_words for v in _CHANGE_VERBS):
        # A move/change about a slot entails the slot changed, which is
        # the negation of "unchanged" regardless of the original value.
        # Emits only contradicted, never supported. Whole-word verbs
        # (so evidence "unchanged" is not a change verb) plus the
        # evidence-side no-change guard (agreement is not a dispute);
        # one shared content word still bars cross-matter fires. The
        # bar is 1, not the negation rule's 2: verbatim C5 shares
        # exactly one content word ("demo"), and the two-sided trigger
        # already binds the change to the claim's matter.
        content = set(re.findall(r"[\w-]{4,}", lowered)) & set(re.findall(r"[\w-]{4,}", blob_fold))
        if len(content) >= 1:
            return CONTRADICTED, "evidence asserts a change the claim denies"
    if any(m in lowered for m in _CAUSAL_MARKERS) and not any(m in blob_fold for m in _CAUSAL_MARKERS):
        return INSUFFICIENT, "causal link asserted without causal evidence"
    from yaad.memory.records import normalize_text
    claim_norm = normalize_text(claim)
    for excerpt in evidence_texts:
        ev_norm = normalize_text(excerpt or "")
        if not ev_norm:
            continue  # blank evidence can never contain (or be) a claim
        if claim_norm and (claim_norm in ev_norm or ev_norm in claim_norm):
            return SUPPORTED, "excerpt containment"
    from yaad.query.retrieve import fold_term
    claim_words = set(re.findall(r"[\w-]{4,}", lowered))
    ev_words = set(re.findall(r"[\w-]{4,}", blob_fold))
    if claim_words:
        # Support requires every content word of the claim (folded)
        # to appear in the evidence. A 60%-overlap rule certified
        # "Dinner includes dessert" from timing-only evidence: topical
        # overlap is not entailment. Novel wording -- paraphrase or
        # invention -- belongs to semantic review, which still cannot
        # override a no-shared-content veto.
        folded_evidence = {fold_term(word) for word in ev_words}
        if all(fold_term(word) in folded_evidence for word in claim_words):
            return SUPPORTED, "full word containment"
        if claim_words & ev_words:
            return INSUFFICIENT, "partial overlap; needs semantic review"
    return INSUFFICIENT, "no shared content with evidence"


def verify_claims(claims: list[tuple[str, list[str]]], unit_text_by_id: dict[str, str],
                  settings=None, db=None, client=None, use_model: bool = False,
                  rows=None, folder_id: int | None = None,
                  question: str = "") -> VerifyReport:
    started = time.perf_counter()
    report = VerifyReport()
    pending: list[int] = []
    for i, (text, eids) in enumerate(claims):
        existing = [(eid, unit_text_by_id.get(eid)) for eid in eids]
        if eids and any(t is None for _, t in existing):
            report.claims.append(ClaimCheck(i, text, eids, INSUFFICIENT, "cited evidence missing"))
            continue
        verdict, reason = deterministic_check(text, [t for _, t in existing if t])
        check = ClaimCheck(i, text, eids, verdict, reason)
        report.claims.append(check)
        if verdict == INSUFFICIENT:
            pending.append(i)
    report.model_info = {"attempted": False}
    if use_model and client is not None and pending and settings is not None:
        from yaad.llm import is_local
        local = is_local(settings)
        gate_ok = local or (
            getattr(settings, "cloud_enabled", False) and getattr(settings, "model_api_key", None))
        gate_reason = None if gate_ok else "cloud gate not satisfied"
        estimate = None
        payload: dict | None = None
        if gate_ok and not local:
            # Folder consent when context is available. Callers without
            # rows/folder keep the legacy flag-only gate (backward
            # compatible); new callers pass rows (preferred) or folder_id.
            if rows is not None:
                allowed, reason = verify_consent(settings, rows)
                if not allowed:
                    gate_ok, gate_reason = False, reason
            elif folder_id is not None and db is not None:
                try:
                    with db.connect() as con:
                        row = con.execute(
                            "SELECT cloud_consent FROM folders WHERE id=?",
                            (folder_id,)).fetchone()
                    if row is None or not row["cloud_consent"]:
                        gate_ok, gate_reason = False, "folder cloud consent not granted"
                except Exception:
                    gate_ok, gate_reason = False, "consent check failed"
            if gate_ok:
                payload = build_verify_payload(
                    question, [report.claims[i] for i in pending],
                    unit_text_by_id, local=False)
                estimate = precall_estimate_usd(settings.model, payload_chars(payload))
                if estimate is not None:
                    # Pre-flight the FULL ballot, not one vote: voting
                    # must not smuggle spend past the ceiling.
                    estimate = estimate * _model_votes(settings)
                refusal = spend_refusal(estimate, stage_spend_ceiling_usd(settings))
                if refusal:
                    gate_ok, gate_reason = False, refusal
                elif db is not None:
                    try:
                        from yaad.llm.meta_config import spend_refusal as global_refusal
                        capped = global_refusal(db, settings, estimate or 0.0)
                        if capped:
                            gate_ok, gate_reason = False, capped
                    except Exception:
                        gate_ok, gate_reason = False, "spend check failed"
        if not gate_ok:
            report.model_info["reason"] = gate_reason
            if estimate is not None:
                report.model_info["estimate_usd"] = estimate
        else:
            if payload is None:
                payload = build_verify_payload(
                    question, [report.claims[i] for i in pending],
                    unit_text_by_id, local=local)
            # Legacy wire shape (claim + evidence list): the frozen
            # entailment evaluation ran against this prompt, so the cap
            # and redaction only ever change content when a claim cites
            # 2+ evidence IDs or carries PII -- never for the frozen set.
            wire = [{"index": c["index"], "claim": c["statement"],
                     "evidence": [c["excerpts"][e] for e in c["evidence_ids"]]}
                    for c in payload["claims"]]
            import json
            sent = json.dumps(wire, ensure_ascii=False).encode("utf-8")
            redacted_any = any(
                c["excerpts"][e] != str(unit_text_by_id.get(e) or "")[:VERIFY_EXCERPT_CHARS]
                for c in payload["claims"] for e in c["evidence_ids"])
            report.model_info["attempted"] = True
            if estimate is not None:
                report.model_info["estimate_usd"] = estimate
            elif local:
                report.model_info["estimate_usd"] = 0.0
            votes = _model_votes(settings)
            report.model_info["votes"] = votes
            ballots: list[dict[int, EntailmentItem]] = []
            attempts: list[dict] = []
            try:
                for _ in range(votes):
                    raw = client.reason_json(
                        [{"role": "user", "content":
                          f"{VERIFY_USER_INSTRUCTIONS} Items: {wire}"}],
                        ENTAILMENT_SCHEMA, VERIFY_PROMPT_ID)
                    attempts.append(dict(getattr(client, "last_call", None) or {}))
                    res = EntailmentResult.model_validate(raw)
                    ballots.append({item.index: item for item in res.items})
                disagreements = 0
                for i in pending:
                    verdicts = [ballots[v][i].verdict for v in range(len(ballots))
                                if i in ballots[v]]
                    if len(set(verdicts)) > 1:
                        disagreements += 1
                    final = _majority_verdict(verdicts, votes)
                    if final is None:
                        continue
                    check = report.claims[i]
                    cited_texts = [str(unit_text_by_id.get(eid) or "").strip()
                                   for eid in check.evidence_ids]
                    if final == SUPPORTED and not any(cited_texts):
                        # A model verdict can never create evidence: with
                        # no non-empty cited excerpt there is nothing to
                        # be supported *by*, whatever the model says.
                        check.reason = "model confirmation rejected: no evidence text"
                        continue
                    if final == SUPPORTED and check.verdict == INSUFFICIENT \
                            and check.reason in ("no shared content with evidence",
                                                 "no evidence supplied"):
                        check.reason = "model confirmation rejected: no shared content"
                        continue
                    reason = next(ballots[v][i].reason for v in range(len(ballots))
                                  if i in ballots[v] and ballots[v][i].verdict == final)
                    check.verdict, check.reason, check.model_used = \
                        final, f"model: {reason}"[:500], True
                report.model_info["vote_disagreements"] = disagreements
                report.model_calls = len(ballots)
                report.status = MODEL_VERIFIED
                report.model_info.update(call_snapshot(client))
            except Exception as exc:
                # Fail closed to the deterministic verdicts, but say so:
                # a silent fallback previously hid a total model outage.
                report.status = MODEL_ERROR
                report.model_error = error_category(exc)
                report.model_info.update(call_snapshot(client))
            finally:
                if db is not None:
                    kind = f"verify excerpts for {len(pending)} claim(s)"
                    if not local:
                        kind += " (cloud path, redactor applied)"
                    if redacted_any:
                        kind += ", PII redacted"
                    for attempt in attempts or [dict(getattr(client, "last_call", None) or {})]:
                        report.model_info["receipt_id"] = _write_receipt(
                            db, VERIFY_PURPOSE, sent, kind, attempt,
                            settings, local)
    elif not pending:
        report.model_info["reason"] = "nothing to verify"
    else:
        report.model_info["reason"] = "model not requested"
    report.ms = round((time.perf_counter() - started) * 1000, 1)
    return report
