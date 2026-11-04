"""Bounded multi-stage answer orchestration (offline-capable).

Stages: query understanding -> retrieval -> evidence classification ->
chronology/conflict analysis -> candidate generation -> independent
verification -> final construction/abstention. Deterministic by default;
model stages are opt-in per call, bounded, spend/consent gated, and
every model output is schema-validated. Deterministic validators always
have the final word.
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
from yaad.memory.relationships import DIGIT_NOUNS, REVISION_TOKENS
from yaad.query.retrieve import STOPWORDS

try:  # Shared seam (memory_answer owns these names): prefer them when present.
    from yaad.query.memory_answer import SLOT_SYNONYMS as _MEMORY_SLOT_SYNONYMS
    from yaad.query.memory_answer import canonical_term as _memory_canonical_term
except ImportError:  # pragma: no cover - fallback path when the seam is absent.
    _MEMORY_SLOT_SYNONYMS = None
    _memory_canonical_term = None

COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_DETAILED = "detailed"
COMPLEXITY_DEEP = "deep"

MAX_MODEL_STAGES = 3
MAX_EVIDENCE_ITEMS = 12


class QueryAnalysis(BaseModel):
    intent: Literal["factoid", "change", "history", "conflict", "open",
                    "multimodal", "other"]
    complexity: Literal["simple", "detailed", "deep"]
    entities: list[str] = Field(default_factory=list, max_length=10)
    needs_chronology: bool = False
    needs_conflict_check: bool = False
    model_config = {"extra": "forbid"}


class EvidenceLabel(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    label: Literal["supports", "contradicts", "supersedes", "context", "irrelevant"]
    reason: str = Field(default="", max_length=500)
    model_config = {"extra": "forbid"}


class EvidenceClassification(BaseModel):
    labels: list[EvidenceLabel] = Field(default_factory=list, max_length=24)
    model_config = {"extra": "forbid"}


class ChronologyEvent(BaseModel):
    evidence_id: str = Field(min_length=1, max_length=128)
    order: int = Field(ge=0, le=100)
    note: str = Field(default="", max_length=300)
    model_config = {"extra": "forbid"}


class ChronologyAnalysis(BaseModel):
    events: list[ChronologyEvent] = Field(default_factory=list, max_length=24)
    current_evidence_id: str | None = Field(default=None, max_length=128)
    conflict: bool = False
    model_config = {"extra": "forbid"}


class CandidateClaim(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    model_config = {"extra": "forbid"}


class CandidateAnswer(BaseModel):
    claims: list[CandidateClaim] = Field(default_factory=list, max_length=12)
    abstain: bool = False
    model_config = {"extra": "forbid"}


class ClaimVerdict(BaseModel):
    index: int = Field(ge=0, le=50)
    verdict: Literal["supported", "contradicted", "insufficient"]
    reason: str = Field(default="", max_length=500)
    model_config = {"extra": "forbid"}


class VerificationResult(BaseModel):
    verdicts: list[ClaimVerdict] = Field(default_factory=list, max_length=24)
    model_config = {"extra": "forbid"}


QUERY_ANALYSIS_SCHEMA = to_strict_schema(QueryAnalysis)
EVIDENCE_CLASSIFICATION_SCHEMA = to_strict_schema(EvidenceClassification)
CHRONOLOGY_SCHEMA = to_strict_schema(ChronologyAnalysis)
CANDIDATE_SCHEMA = to_strict_schema(CandidateAnswer)
VERIFICATION_SCHEMA = to_strict_schema(VerificationResult)

_CHANGE_HINTS = ("chang", "mov", "updat", "revis", "reschedul", "supersed",
                 "instead", "now ", "latest", "current")
_HISTORY_HINTS = ("histor", "before", "previous", "original", "was ", "what changed",
                  "timeline", "evol")
_CONFLICT_HINTS = ("conflict", "contradict", "disagree", "which is",
                   "two ", "both say", "different")
_MULTIMODAL_HINTS = ("poster", "image", "photo", "screenshot", "recording",
                     "audio", "voice", "pdf")
_OPEN_HINTS = ("still open", "undecided", "pending", "tbd", "missing",
               "not decided", "unknown")

# Same-matter threshold: shared content words between a unit and the
# question, mirroring the retrieval floor used by the memory layer.
MATTER_WORDS = 2

# Local synonym fallback for overlap counting ONLY. Each word maps to its
# equivalence class; classes are counted once via set semantics. Never
# consulted by grounding, slot extraction, or veto decisions.
_FALLBACK_SYNONYMS = {
    "cost": "fee", "fee": "fee", "price": "fee", "charge": "fee",
    "attend": "registration", "registration": "registration",
    "admission": "registration",
}

# Revision/recency cue: the relationships layer's revision vocabulary plus
# "instead" (explicit replacement language: "Room 5 ... Room 7 instead").
REVISION_MARKERS = frozenset(set(REVISION_TOKENS) | {"instead"})

# Digit-noun slot family (imported, never redefined): singular folds so
# "Room 5" and "Rooms 5" compare as the same filler.
_ROOM_NOUN_FOLD = {"room": "room", "rooms": "room", "hall": "hall", "halls": "hall"}


def _canonical_term(word: str) -> str:
    """One word's synonym equivalence class. Prefers the shared seam's
    ``canonical_term`` when importable and well-behaved, else the local
    fallback map, else identity."""
    if callable(_memory_canonical_term):
        try:
            return str(_memory_canonical_term(word))
        except (TypeError, AttributeError, KeyError, ValueError):
            pass
    return _FALLBACK_SYNONYMS.get(word, word)


def canonical_content_terms(text: str) -> set[str]:
    """Content words folded to synonym equivalence classes, counted once
    (set semantics). Used ONLY for matter-overlap counting. Each word
    contributes its morphology closure (the word, its singular fold,
    and WORD_FOLDS variants) before canonical mapping, mirroring the
    hybrid channel's match-time expansion so the classifier sees what
    retrieval saw."""
    from yaad.query.memory_answer import content_terms
    from yaad.query.retrieve import fold_term, word_folds
    out = set()
    for word in content_terms(text):
        forms = {word, fold_term(word)}
        for form in list(forms):
            forms.update(word_folds(form))
        out.update(_canonical_term(form) for form in forms)
    return out


def matter_overlap(question: str, text: str) -> int:
    """Synonym-aware shared content words between question and text."""
    return len(canonical_content_terms(question) & canonical_content_terms(text))


def shares_matter(question: str, text: str, threshold: int = MATTER_WORDS) -> bool:
    """True when question and text share at least ``threshold`` content
    words (synonym classes counted once)."""
    return matter_overlap(question, text) >= threshold


# Directive language aimed at the answerer/model: instructions are
# not factual assertions and must never become answer claims (the J05
# class: a probe-echoing instruction shares matter lexically but
# answers nothing). Covers ONLY what the verify layer cannot see:
# prompt-injection markers deliberately still flow to verification,
# where deterministic poisoning disposes of them (pinned by
# test_pipeline_verify_rejects_injection_source). Kept to
# answer-directed phrases with no legitimate evidence use.
_DIRECTIVE_MARKERS = (
    "in your answer", "in your response", "your answer should",
    "your response should", "answer with only", "respond with only",
    "output only", "reply with only",
)


def _is_directive(sentence: str) -> bool:
    """True when the sentence instructs the answerer rather than
    asserting a fact (see _DIRECTIVE_MARKERS)."""
    lowered = f" {sentence.casefold()} "
    return any(marker in lowered for marker in _DIRECTIVE_MARKERS)


def _pick_claim_sentence(question: str, text: str) -> str:
    """The unit's most question-relevant assertive sentence, verbatim.

    Scores each non-directive sentence by synonym-aware matter
    overlap with the question and keeps the best one that shares
    matter (earliest on ties), so header boilerplate never becomes
    the claim. Falls back to the first eligible sentence when
    nothing shares matter; the verify relevance gate then rejects it
    as before. A unit with no assertive sentence contributes no
    claim. Always a verbatim unit substring (<=500 chars), so
    excerpt containment still holds.
    """
    sentences = [part.strip() for part in
                 re.split(r"(?<=[.!?])\s+", (text or "").strip())]
    sentences = [part for part in sentences if part]
    eligible = [part for part in sentences if not _is_directive(part)]
    if not eligible:
        return ""
    best, best_overlap = eligible[0], -1
    for sentence in eligible:
        overlap = matter_overlap(question, sentence)
        if overlap > best_overlap and shares_matter(question, sentence):
            best, best_overlap = sentence, overlap
    return best[:500]


def room_slot_fillers(text: str) -> list[str]:
    """Room/hall slot fillers in occurrence order, deduped.

    Reuses the ``DIGIT_NOUNS`` room/hall adjacency rule from
    ``relationships._extended_values`` verbatim (noun followed by a
    pure-digit token), extended to single uppercase letters so the
    "Room A vs Room B" style is covered too. Lowercase letters never
    capture (article guard: "booked room a week ago"). Nouns fold to
    singular; identifiers casefold. Deterministic.
    """
    tokens = re.findall(r"[\w-]+", text or "", flags=re.UNICODE)
    folded = [token.casefold() for token in tokens]
    fillers: list[str] = []
    for pos, word in enumerate(folded):
        if word not in DIGIT_NOUNS or pos + 1 >= len(tokens):
            continue
        nxt, raw = folded[pos + 1], tokens[pos + 1]
        ident: str | None = None
        if nxt.isdigit():
            ident = nxt
        elif len(raw) == 1 and raw.isalpha() and raw[0].isupper():
            ident = nxt
        if ident is not None:
            filler = f"{_ROOM_NOUN_FOLD[word]} {ident}"
            if filler not in fillers:
                fillers.append(filler)
    return fillers


def has_revision_marker(text: str) -> bool:
    """True when the text carries a revision/recency marker word."""
    words = {word.casefold() for word in re.findall(r"[\w-]+", text or "", flags=re.UNICODE)}
    return bool(words & REVISION_MARKERS)


def _unit_text(unit: dict) -> str:
    return str(unit.get("text") or unit.get("excerpt") or "")


def _is_supports(unit: dict) -> bool:
    """Supports-labeled units only; retrieval rows without a role label
    default to supports (they are evidentiary premises)."""
    return str(unit.get("role", "supports")) == "supports"


def detect_room_rivalry(question: str, units: list[dict]) -> dict:
    """Deterministic rival-value conflict detector (single room slot).

    Fires only when 2+ supports-labeled units share matter with the
    question (``MATTER_WORDS``+) yet carry mutually exclusive room-slot
    fillers ("Room 5" vs "Room 7"). Single-slot rivalry is structural:
    exactly one filler per side on the one contested slot. A same-matter
    unit listing 2+ fillers ("Rooms 5, 6 and 7 available") is a
    multi-value listing, never a conflict; distinct matters never rival.
    """
    mattered = [unit for unit in units
                if _is_supports(unit) and shares_matter(question, _unit_text(unit))]
    fillers = {id(unit): room_slot_fillers(_unit_text(unit)) for unit in mattered}
    if any(len(found) > 1 for found in fillers.values()):
        return {"conflict": False, "slot": None, "rivals": [],
                "reason": "multi-value listing: a same-matter unit carries 2+ room fillers"}
    singled = [(unit, fillers[id(unit)][0]) for unit in mattered if fillers[id(unit)]]
    distinct = list(dict.fromkeys(filler for _, filler in singled))
    if len(singled) >= 2 and len(distinct) >= 2:
        rivals = [{"evidence_id": unit.get("evidence_id"), "filler": filler,
                   "text": _unit_text(unit)}
                  for unit, filler in singled if filler in distinct[:2]]
        first = [item for item in rivals if item["filler"] == distinct[0]][0]
        second = [item for item in rivals if item["filler"] == distinct[1]][0]
        return {"conflict": True, "slot": "room", "rivals": [first, second],
                "reason": f"rival room fillers: {distinct[0]} vs {distinct[1]}"}
    return {"conflict": False, "slot": None, "rivals": [],
            "reason": "no single-slot room rivalry among same-matter supports"}


def _same_matter_units(left: str, right: str) -> bool:
    """Unit-unit matter gate for the chronology recency cue."""
    return len(canonical_content_terms(left) & canonical_content_terms(right)) >= MATTER_WORDS


def order_by_recency(units: list[dict]) -> list[dict]:
    """Deterministic recency ordering: a unit carrying revision markers
    bubbles after its same-matter non-marked rivals (adjacent swaps to
    fixpoint, gated on shared matter overlap). Units with no
    same-matter rival never move, and marked-vs-marked relative order
    is preserved."""
    ordered = list(units)
    texts = [_unit_text(unit) for unit in ordered]
    marked = [has_revision_marker(text) for text in texts]
    changed = True
    while changed:
        changed = False
        for pos in range(len(ordered) - 1):
            if not marked[pos] or marked[pos + 1]:
                continue
            if _same_matter_units(texts[pos], texts[pos + 1]):
                ordered[pos], ordered[pos + 1] = ordered[pos + 1], ordered[pos]
                texts[pos], texts[pos + 1] = texts[pos + 1], texts[pos]
                marked[pos], marked[pos + 1] = marked[pos + 1], marked[pos]
                changed = True
    return ordered


def cloud_allowed(settings, rows) -> tuple[bool, str]:
    """Pipeline egress gate, mirroring the answer layer: local serving is
    local computation (allowed); cloud serving requires cloud_enabled and
    live folder consent on every row. Missing consent info fails closed."""
    from yaad.llm import is_local
    if is_local(settings):
        return True, "local serving: no egress"
    if not getattr(settings, "cloud_enabled", False):
        return False, "cloud review is disabled in app settings"
    rows = list(rows or [])
    if any(not bool(row.get("folder_cloud_consent")) for row in rows):
        return False, "one or more rows lack folder cloud consent"
    return True, "cloud serving: consent on every row"


@dataclass
class StageLog:
    stage: str
    model_used: bool
    ms: float
    detail: str = ""
    error: str | None = None
    status: str = DETERMINISTIC_ONLY
    error_category: str | None = None
    http_status: int | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class PipelineResult:
    analysis: QueryAnalysis
    evidence_ids: list[str]
    classification: EvidenceClassification
    chronology: ChronologyAnalysis
    candidate: CandidateAnswer
    verification: VerificationResult
    status: str
    answer: str
    citations: list[str]
    stages: list[StageLog] = field(default_factory=list)
    model_calls: int = 0


def classify_query(question: str) -> QueryAnalysis:
    q = (question or "").casefold()
    entities = [w for w in re.findall(r"[A-Z][\w-]{2,}", question or "")][:10]
    needs_chron = any(h in q for h in _CHANGE_HINTS + _HISTORY_HINTS)
    needs_conf = any(h in q for h in _CONFLICT_HINTS) or "conflict" in q
    if any(h in q for h in _CONFLICT_HINTS):
        intent = "conflict"
    elif any(h in q for h in _HISTORY_HINTS + _CHANGE_HINTS):
        intent = "change" if "chang" in q or "current" in q or "latest" in q else "history"
    elif any(h in q for h in _OPEN_HINTS):
        intent = "open"
    elif any(h in q for h in _MULTIMODAL_HINTS):
        intent = "multimodal"
    elif q.strip().endswith("?") or q.startswith(("what", "when", "where", "who", "how")):
        intent = "factoid"
    else:
        intent = "other"
    if needs_conf or intent in ("conflict", "history") or sum(
            1 for h in _CHANGE_HINTS + _HISTORY_HINTS + _CONFLICT_HINTS if h in q) >= 2:
        complexity = COMPLEXITY_DEEP
    elif needs_chron or intent in ("change", "multimodal", "open") or len(q.split()) > 14:
        complexity = COMPLEXITY_DETAILED
    else:
        complexity = COMPLEXITY_SIMPLE
    return QueryAnalysis(intent=intent, complexity=complexity, entities=entities,
                         needs_chronology=needs_chron, needs_conflict_check=needs_conf)


def _model_gate(settings, db, purpose: str, folder_id: int | None = None,
                estimate_usd: float = 0.0) -> str | None:
    from yaad.llm import is_local
    if is_local(settings):
        return None
    if not getattr(settings, "cloud_enabled", False) or not getattr(settings, "model_api_key", None):
        return f"{purpose} refused: cloud gate not satisfied"
    if folder_id is not None and db is not None:
        try:
            with db.connect() as con:
                row = con.execute("SELECT cloud_consent FROM folders WHERE id=?",
                                  (folder_id,)).fetchone()
            if row is None or not row["cloud_consent"]:
                return f"{purpose} refused: folder cloud consent not granted"
        except Exception:
            return f"{purpose} refused: consent check failed"
    try:
        from yaad.llm.meta_config import spend_refusal
        refusal = spend_refusal(db, settings, estimate_usd)
        if refusal:
            return f"{purpose} refused: {refusal}"
    except Exception:
        # Fail closed: a broken spend check must never waive the cap.
        return f"{purpose} refused: spend check failed"
    return None


def _call_model(client, messages, schema, prompt_id):
    raw = client.reason_json(messages, schema, prompt_id)
    if not isinstance(raw, dict):
        raise ValueError("model returned non-object")
    return raw


def _stage_info(client, status: str, exc=None) -> dict:
    """Observability record for one model-stage attempt."""
    info = call_snapshot(client)
    info["status"] = status
    info["error_category"] = error_category(exc) if exc is not None else None
    return info


def _stage_classify_evidence(question, rows, analysis, settings, db, client, use_model, folder_id=None):
    started = time.perf_counter()
    info: dict = {"status": DETERMINISTIC_ONLY}
    if use_model and client is not None and analysis.complexity == COMPLEXITY_DEEP:
        from yaad.llm import is_local
        from yaad.query.verify import (payload_chars, precall_estimate_usd,
                                       redact_for_cloud, spend_refusal as ceiling_refusal,
                                       stage_spend_ceiling_usd)
        local = is_local(settings)
        payload = [{"evidence_id": r["evidence_id"],
                    "text": redact_for_cloud(r["text"][:600], local)}
                   for r in rows[:MAX_EVIDENCE_ITEMS]]
        estimate = 0.0 if local else precall_estimate_usd(
            getattr(settings, "model", ""), payload_chars({"evidence": payload}))
        gate = _model_gate(settings, db, "evidence classification", folder_id,
                           estimate or 0.0)
        if gate is None and not local:
            ceiling_hit = ceiling_refusal(estimate, stage_spend_ceiling_usd(settings))
            if ceiling_hit:
                gate = f"evidence classification refused: {ceiling_hit}"
        if gate is None:
            try:
                raw = _call_model(client, [{"role": "user",
                    "content": f"Question: {question}\nEvidence: {payload}\n"
                               "Label each item supports/contradicts/supersedes/context/irrelevant."}],
                    EVIDENCE_CLASSIFICATION_SCHEMA, "evidence-classification-v1")
                labels = EvidenceClassification.model_validate(raw)
                ms = round((time.perf_counter() - started) * 1000, 1)
                return labels, True, ms, None, _stage_info(client, MODEL_VERIFIED)
            except (ValidationError, ValueError, RuntimeError) as exc:
                err = f"model classification invalid ({type(exc).__name__}); deterministic fallback"
                info = _stage_info(client, MODEL_ERROR, exc)
            except Exception as exc:
                err = f"model classification failed ({type(exc).__name__}); deterministic fallback"
                info = _stage_info(client, MODEL_ERROR, exc)
        else:
            err = gate + "; deterministic fallback"
    else:
        err = None
    # Synonym-aware overlap (SLOT_SYNONYMS classes counted once): a
    # paraphrased question still meets its evidence. Grounding and veto
    # decisions downstream always use raw text, never these classes.
    qterms = canonical_content_terms(question)
    labels = []
    for row in rows[:MAX_EVIDENCE_ITEMS]:
        overlap = len(qterms & canonical_content_terms(row["text"]))
        label = "supports" if overlap >= 2 else ("context" if overlap == 1 else "irrelevant")
        labels.append(EvidenceLabel(evidence_id=row["evidence_id"], label=label,
                                    reason=f"term overlap {overlap}"))
    ms = round((time.perf_counter() - started) * 1000, 1)
    return EvidenceClassification(labels=labels), False, ms, err, info


def _stage_chronology(question, rows, classification, analysis, settings, db, client, use_model, folder_id=None):
    started = time.perf_counter()
    info: dict = {"status": DETERMINISTIC_ONLY}
    err = None
    usable = [lab for lab in classification.labels if lab.label in ("supports", "contradicts", "supersedes")]
    if use_model and client is not None and analysis.needs_chronology and usable:
        from yaad.llm import is_local
        from yaad.query.verify import (payload_chars, precall_estimate_usd,
                                       redact_for_cloud, spend_refusal as ceiling_refusal,
                                       stage_spend_ceiling_usd)
        local = is_local(settings)
        by_id = {r["evidence_id"]: r for r in rows}
        payload = [{"evidence_id": lab.evidence_id,
                    "text": redact_for_cloud(
                        by_id.get(lab.evidence_id, {}).get("text", "")[:500], local)}
                   for lab in usable[:MAX_EVIDENCE_ITEMS]]
        estimate = 0.0 if local else precall_estimate_usd(
            getattr(settings, "model", ""), payload_chars({"evidence": payload}))
        gate = _model_gate(settings, db, "chronology analysis", folder_id,
                           estimate or 0.0)
        if gate is None and not local:
            ceiling_hit = ceiling_refusal(estimate, stage_spend_ceiling_usd(settings))
            if ceiling_hit:
                gate = f"chronology analysis refused: {ceiling_hit}"
        if gate is None:
            try:
                raw = _call_model(client, [{"role": "user",
                    "content": f"Order these evidence items chronologically. Evidence: {payload}\n"
                               "Return order indices, current item, and whether they conflict."}],
                    CHRONOLOGY_SCHEMA, "chronology-analysis-v1")
                chrono = ChronologyAnalysis.model_validate(raw)
                valid = {lab.evidence_id for lab in usable}
                chrono.events = [e for e in chrono.events if e.evidence_id in valid]
                if chrono.current_evidence_id not in valid:
                    chrono.current_evidence_id = None
                ms = round((time.perf_counter() - started) * 1000, 1)
                return chrono, True, ms, None, _stage_info(client, MODEL_VERIFIED)
            except Exception as exc:
                err = f"model chronology failed ({type(exc).__name__}); deterministic fallback"
                info = _stage_info(client, MODEL_ERROR, exc)
        else:
            err = gate + "; deterministic fallback"
    by_id = {r["evidence_id"]: r for r in rows}
    ordered_units = order_by_recency(
        [{"evidence_id": lab.evidence_id, "role": lab.label,
          "text": by_id.get(lab.evidence_id, {}).get("text", "")} for lab in usable])
    order_index = {u["evidence_id"]: i for i, u in enumerate(ordered_units)}
    events = [ChronologyEvent(evidence_id=lab.evidence_id,
                              order=order_index.get(lab.evidence_id, i))
              for i, lab in enumerate(usable)]
    labels = {lab.label for lab in usable}
    rivalry = detect_room_rivalry(question, ordered_units)
    current = ordered_units[-1]["evidence_id"] if ordered_units else None
    chrono = ChronologyAnalysis(events=events, current_evidence_id=current,
        conflict=("contradicts" in labels) or rivalry["conflict"])
    return chrono, False, round((time.perf_counter() - started) * 1000, 1), err, info


def _stage_candidate(question, rows, db, folder_id, local, classification, analysis):
    started = time.perf_counter()
    by_id = {r["evidence_id"]: r for r in rows}
    claims: list[CandidateClaim] = []
    seen: set[str] = set()

    def _emit(evidence_id: str) -> None:
        if len(claims) >= 6 or evidence_id in seen:
            return
        text = (by_id.get(evidence_id, {}).get("text", "") or "").strip()
        if not text:
            return
        sentence = _pick_claim_sentence(question, text)
        if not sentence:
            return
        seen.add(evidence_id)
        claims.append(CandidateClaim(text=sentence, evidence_ids=[evidence_id]))

    if db is not None:
        # Memory-aware selection: Tier A claims and validated conflict
        # sides pick WHICH retrieved units become candidates (first
        # resolving unit each, one claim per side so rival values can
        # join the answer). Memory never words the claim: the evidence
        # sentence does, keeping containment intact. Consent-gated via
        # the shared planner; unretrieved IDs are skipped (verify looks
        # up by_id). A planner failure falls back to classification
        # order rather than failing the stage.
        try:
            from yaad.query.memory_answer import plan_memory
            plan = plan_memory(db, question, folder_id, rows, local=local)
        except Exception:
            plan = None
        if plan is not None:
            for pair in plan.get("conflicts", []):
                for claim in pair:
                    for eid in claim.get("evidence_ids", []):
                        if eid in by_id:
                            _emit(eid)
                            break
            for claim in plan.get("claims", []):
                for eid in claim.get("evidence_ids", []):
                    if eid in by_id:
                        _emit(eid)
                        break
    for lab in classification.labels:
        if lab.label not in ("supports", "contradicts"):
            continue
        _emit(lab.evidence_id)
    cand = CandidateAnswer(claims=claims, abstain=not claims)
    return cand, round((time.perf_counter() - started) * 1000, 1)


def _pipeline_supported_is_disputed(question, supported, row_dicts, db,
                                    folder_id, local) -> bool:
    """True when supported claims jointly describe a dispute that must
    not serve as a co-fact answer. Two narrow, fail-closed triggers:

    1. The memory plan reports a conflict whose every side is cited by
    the supported claims: validated rivalry wins over the join. Single
    entries (dispute-describing records) count when cited.
    2. The joined answer itself states a dispute across >=2 files: the
    same bar as answer review's answered->conflict upgrade.

    A planner failure or missing memory degrades to trigger 2 only;
    trigger 2 needs no memory at all. Never raises."""
    supported_eids = {eid for claim in supported for eid in claim.evidence_ids}
    if db is not None:
        try:
            from yaad.query.memory_answer import plan_memory
            plan = plan_memory(db, question, folder_id, row_dicts, local=local)
        except Exception:
            plan = None
        if plan is not None:
            for entry in plan.get("conflicts", []) or []:
                sides = [set(claim.get("evidence_ids", [])) for claim in entry]
                if sides and all(supported_eids & side for side in sides):
                    return True
    try:
        from yaad.memory.records import states_dispute
    except Exception:
        return False
    joined = " ".join(claim.text for claim in supported[:3])
    if not states_dispute("", joined):
        return False
    by_id = {row.get("evidence_id"): row for row in row_dicts}
    files = {by_id[eid].get("file_id") for eid in supported_eids
             if eid in by_id and by_id[eid].get("file_id") is not None}
    return len(files) >= 2


def _stage_verify(question, candidate, rows, settings, db, client, use_model, folder_id=None):
    started = time.perf_counter()
    info: dict = {"status": DETERMINISTIC_ONLY}
    err = None
    by_id = {r["evidence_id"]: r for r in rows}
    if use_model and client is not None and candidate.claims:
        from yaad.llm import is_local
        from yaad.query.verify import (
            VERIFY_EVIDENCE_IDS_PER_CLAIM,
            VERIFY_EXCERPT_CHARS,
            _write_receipt,
            payload_chars,
            precall_estimate_usd,
            redact_for_cloud,
            spend_refusal as ceiling_refusal,
            stage_spend_ceiling_usd,
        )
        local = is_local(settings)
        payload = [{"index": i, "claim": c.text[:VERIFY_EXCERPT_CHARS],
                    "evidence": [redact_for_cloud(
                        by_id.get(e, {}).get("text", "")[:VERIFY_EXCERPT_CHARS], local)
                                 for e in c.evidence_ids[:VERIFY_EVIDENCE_IDS_PER_CLAIM]]}
                   for i, c in enumerate(candidate.claims)]
        estimate = 0.0 if local else precall_estimate_usd(
            settings.model, payload_chars({"claims": payload}))
        gate = _model_gate(settings, db, "claim verification", folder_id,
                           estimate or 0.0)
        if gate is None and not local:
            ceiling_hit = ceiling_refusal(estimate, stage_spend_ceiling_usd(settings))
            if ceiling_hit:
                gate = f"claim verification refused: {ceiling_hit}"
        if gate is None:
            import json
            sent = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                raw = _call_model(client, [{"role": "user",
                    "content": "Verify each claim against its evidence. Mark supported only "
                               f"when the evidence entails it. Items: {payload}"}],
                    VERIFICATION_SCHEMA, "claim-verification-v1")
                res = VerificationResult.model_validate(raw)
                idx = {c.index for c in res.verdicts}
                if idx.issuperset(range(len(candidate.claims))):
                    # Relevance survives model confirmation: the model
                    # judges claim-vs-evidence, but only claims that
                    # address the question can answer it. Without this,
                    # model confirmation re-serves the boilerplate the
                    # deterministic gate rejects (live-measured J05
                    # regression: answered-with-boilerplate). Poisoning
                    # survives too: a model that confirms entailment of
                    # an injection-bearing source must not re-serve what
                    # the deterministic veto refuses (live-measured J06:
                    # model quoted the pinned comment the veto abstains
                    # on). Only the safety veto is mirrored here; other
                    # model/deterministic disagreements keep model
                    # semantics.
                    from yaad.query.verify import CONTRADICTED, deterministic_check
                    fixed = []
                    for v in res.verdicts:
                        if v.verdict == "supported" \
                                and 0 <= v.index < len(candidate.claims):
                            claim = candidate.claims[v.index]
                            if not shares_matter(question, claim.text):
                                fixed.append(ClaimVerdict(
                                    index=v.index, verdict="insufficient",
                                    reason="claim does not address the question"))
                                continue
                            texts = [by_id.get(eid, {}).get("text", "")
                                     for eid in claim.evidence_ids]
                            texts = [text for text in texts if text]
                            if texts:
                                verdict, reason = deterministic_check(
                                    claim.text, texts)
                                if verdict == CONTRADICTED \
                                        and "prompt-injection" in reason:
                                    fixed.append(ClaimVerdict(
                                        index=v.index, verdict="insufficient",
                                        reason="prompt-injection source veto"))
                                    continue
                        fixed.append(v)
                    res = VerificationResult(verdicts=fixed)
                    ms = round((time.perf_counter() - started) * 1000, 1)
                    info = _stage_info(client, MODEL_VERIFIED)
                    return res, True, ms, None, info
                err = "model verification incomplete; deterministic fallback"
                info = _stage_info(client, MODEL_ERROR)
                info["error_category"] = "incomplete_response"
            except Exception as exc:
                err = f"model verification failed ({type(exc).__name__}); deterministic fallback"
                info = _stage_info(client, MODEL_ERROR, exc)
            finally:
                if db is not None:
                    _write_receipt(db, "pipeline claim verification", sent,
                                   f"verify excerpts for {len(payload)} claim(s)",
                                   dict(getattr(client, "last_call", None) or {}),
                                   settings, local)
        else:
            err = gate + "; deterministic fallback"
    from yaad.query.verify import CONTRADICTED, SUPPORTED, deterministic_check
    verdicts = []
    for i, claim in enumerate(candidate.claims):
        if not shares_matter(question, claim.text):
            # Relevance gate (mirrors the answer path's tiers): a claim
            # that shares no matter with the question cannot answer it,
            # however well the evidence contains it. Without this, a
            # boilerplate first sentence "answers" secret-extraction
            # probes like J05.
            verdicts.append(ClaimVerdict(index=i, verdict="insufficient",
                                         reason="claim does not address the question"))
            continue
        texts = [by_id.get(eid, {}).get("text", "") for eid in claim.evidence_ids]
        texts = [text for text in texts if text]
        if not texts:
            verdicts.append(ClaimVerdict(index=i, verdict="insufficient",
                                         reason="no containing excerpt"))
            continue
        verdict, reason = deterministic_check(claim.text, texts)
        if verdict == SUPPORTED:
            final = "supported"
        elif verdict == CONTRADICTED and "prompt-injection" in reason:
            # Injection poisons the claim, not the verdict: abstain on it
            # rather than flipping the pipeline into conflict status.
            final = "insufficient"
        elif verdict == CONTRADICTED:
            final = "contradicted"
        else:
            final = "insufficient"
        verdicts.append(ClaimVerdict(index=i, verdict=final, reason=reason))
    res = VerificationResult(verdicts=verdicts)
    return res, False, round((time.perf_counter() - started) * 1000, 1), err, info


def _log_stage(stages: list, name: str, used: bool, ms: float, detail: str,
               err, info: dict) -> None:
    stages.append(StageLog(
        name, used, ms, detail, err,
        status=info.get("status", DETERMINISTIC_ONLY),
        error_category=info.get("error_category"),
        http_status=(info.get("http_status")),
        model=info.get("model"),
        input_tokens=info.get("input_tokens"),
        output_tokens=info.get("output_tokens")))


def run_pipeline(db, question: str, folder_id: int | None, settings,
                 client=None, use_model: bool = False) -> PipelineResult:
    """Run the bounded pipeline. Deterministic unless use_model and gates pass."""
    stages: list[StageLog] = []
    model_calls = 0
    t0 = time.perf_counter()
    analysis = classify_query(question)
    stages.append(StageLog("query_understanding", False,
                           round((time.perf_counter() - t0) * 1000, 1),
                           f"{analysis.intent}/{analysis.complexity}"))
    t0 = time.perf_counter()
    from yaad.query.hybrid import hybrid_search
    try:
        rows = hybrid_search(db, question, folder_id)
    except Exception as exc:
        rows = []
        stages.append(StageLog("retrieval", False, 0.0, "", f"{type(exc).__name__}"))
    else:
        stages.append(StageLog("retrieval", False,
            round((time.perf_counter() - t0) * 1000, 1), f"{len(rows)} rows"))
    row_dicts = [dict(r) for r in rows]
    allow_model = use_model and model_calls < MAX_MODEL_STAGES
    classification, used, ms, err, info = _stage_classify_evidence(
        question, row_dicts, analysis, settings, db, client, allow_model, folder_id)
    model_calls += 1 if used else 0
    _log_stage(stages, "evidence_classification", used, ms,
               f"{len(classification.labels)} labels", err, info)
    allow_model = use_model and model_calls < MAX_MODEL_STAGES
    chronology, used, ms, err, info = _stage_chronology(
        question, row_dicts, classification, analysis, settings, db, client, allow_model, folder_id)
    model_calls += 1 if used else 0
    _log_stage(stages, "chronology", used, ms,
               f"{len(chronology.events)} events conflict={chronology.conflict}", err, info)
    t0 = time.perf_counter()
    from yaad.llm import is_local
    candidate, ms = _stage_candidate(question, row_dicts, db, folder_id,
                                     is_local(settings), classification, analysis)
    stages.append(StageLog("candidate_generation", False, ms, f"{len(candidate.claims)} claims"))
    allow_model = use_model and model_calls < MAX_MODEL_STAGES
    verification, used, ms, err, info = _stage_verify(
        question, candidate, row_dicts, settings, db, client, allow_model, folder_id)
    model_calls += 1 if used else 0
    _log_stage(stages, "verification", used, ms,
               f"{len(verification.verdicts)} verdicts", err, info)
    supported = [candidate.claims[v.index] for v in verification.verdicts
                 if v.verdict == "supported" and 0 <= v.index < len(candidate.claims)]
    contradicted = [v for v in verification.verdicts if v.verdict == "contradicted"]
    if contradicted or chronology.conflict:
        status, answer = "conflict", "Sources disagree; see citations."
    elif supported:
        status = "answered"
        answer = " ".join(c.text for c in supported[:3])
        if _pipeline_supported_is_disputed(question, supported, row_dicts,
                                           db, folder_id, is_local(settings)):
            # Rival sides joined as co-facts: validated memory rivalry
            # or a self-describing dispute upgrades the label. Both
            # sides stay cited via the conflict branch below.
            status, answer = "conflict", "Sources disagree; see citations."
    else:
        status, answer = "not_found", "Insufficient evidence to answer."
    # Citations only ever name supported claims; a conflict cites both
    # rival sides, and an abstention cites nothing (unverified candidate
    # claims must not masquerade as sources).
    citations: list[str] = []
    cited_claims = list(supported)
    if status == "conflict":
        cited_claims += [candidate.claims[v.index] for v in contradicted
                         if 0 <= v.index < len(candidate.claims)]
    for claim in cited_claims:
        for eid in claim.evidence_ids:
            if eid not in citations:
                citations.append(eid)
    stages.append(StageLog("final_construction", False, 0.0, status))
    return PipelineResult(analysis=analysis,
        evidence_ids=[r["evidence_id"] for r in row_dicts],
        classification=classification, chronology=chronology,
        candidate=candidate, verification=verification, status=status,
        answer=answer, citations=citations, stages=stages, model_calls=model_calls)
