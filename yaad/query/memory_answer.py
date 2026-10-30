"""Additive memory layer for answer generation.

Eligible, authorized memory records join the established units pipeline in
two ways: their supporting unit rows are injected into review context and
their date/amount values join the local value/conflict computation, so the
existing conflict rules, citation checks, and semantic review apply
unchanged. When the units path cannot answer, matched claims are
synthesized deterministically (no model call) with citations to current
evidence. With no matched records the pipeline behaves exactly as before.
"""
import json
import re
from pathlib import Path

from yaad.memory.ledger import list_transitions
from yaad.memory.records import (
    AMOUNT_PATTERN,
    amount_class_of,
    claims_match,
    conflict_partners,
    normalize_text,
)
from yaad.memory.relationships import (
    DIMENSION_LABELS,
    SUPPORTED,
    statement_content_words,
    statement_dimension_values,
    statement_dimensions,
    validate_pair,
    validate_relationship,
    value_mentioned,
)
from yaad.privacy.masking import is_sensitive
from yaad.query.retrieve import STOPWORDS

DATE_INTENT = ("deadline", "date", "when", "due")
AMOUNT_INTENT = ("fee", "amount", "deposit", "cost", "price", "payment")
UNDECIDED_QUESTION_HINTS = ("decid", "undecided", "finalis", "finaliz", "still open",
                            "pending", "tbd", "remain", "remains", "left to",
                            "yet to", "open question", "not decided")
INJECT_CAP = 4
MATCH_WORDS = 2

# Claim relevance tiers (Phase 1): Tier A claims may synthesize answers
# and join the value/conflict computation; Tier C claims provide context
# and attribution only, and must never change the verdict.
ANSWERABLE = "answerable"
CONTEXTUAL = "contextual"


def content_terms(text: str) -> set[str]:
    return {word for word in re.findall(r"[\w-]+", (text or "").casefold())
            if len(word) > 1 and word not in STOPWORDS}


def _is_undecided_question(question: str) -> bool:
    return any(hint in question.casefold() for hint in UNDECIDED_QUESTION_HINTS)


def authorized_supports(con, record_id: int, require_consent: bool = True) -> list[dict]:
    """Supports rows usable as answer premises: the unit still exists and
    still contains the excerpt. Cloud serving additionally requires live
    file and folder consent, since premises may travel to the model.
    Local serving passes require_consent=False: answering is local
    computation with no egress, so cloud consent (permission to send data
    to Meta) does not apply. Retained history never bypasses present
    authorization under either provider."""
    usable = []
    for row in con.execute(
            """SELECT e.evidence_id,e.excerpt,u.text,f.cloud_consent AS file_consent,
                      fo.cloud_consent AS folder_consent
               FROM memory_record_evidence e
               LEFT JOIN units u ON u.evidence_id=e.evidence_id
               LEFT JOIN files f ON f.id=u.file_id
               LEFT JOIN folders fo ON fo.id=f.folder_id
               WHERE e.record_id=? AND e.role='supports'""", (record_id,)):
        if (row["text"] is None or normalize_text(row["excerpt"]) not in normalize_text(row["text"])):
            continue
        if require_consent and (not row["file_consent"] or not row["folder_consent"]):
            continue
        usable.append({"evidence_id": row["evidence_id"], "excerpt": row["excerpt"]})
    return usable


def fetch_support_rows(db, folder_id: int | None, evidence_ids: list[str]) -> list[dict]:
    """Unit rows in search-result shape for the given evidence IDs."""
    if not evidence_ids:
        return []
    marks = ",".join("?" for _ in evidence_ids)
    sql = f"""SELECT u.*,f.path,f.kind AS file_kind,f.partial,fo.cloud_consent AS folder_cloud_consent
              FROM units u JOIN files f ON f.id=u.file_id JOIN folders fo ON fo.id=f.folder_id
              WHERE u.evidence_id IN ({marks})"""
    args: list = list(evidence_ids)
    if folder_id is not None:
        sql += " AND f.folder_id=?"
        args.append(folder_id)
    with db.connect() as con:
        return [dict(row) for row in con.execute(sql, args)]


def _record_text(record: dict) -> str:
    return f"{record['title']} {record.get('detail') or ''}"


def _matches_terms(record: dict, terms: set[str]) -> bool:
    """Question relevance: shared wording at the retrieval floor. Shared
    evidence corroborates but does not match, else every record citing a
    retrieved unit would answer every question."""
    return len(terms & content_terms(_record_text(record))) >= MATCH_WORDS


def matter_words(record: dict) -> set[str]:
    """The record's matter signature: validated key-phrase content words
    stored in record_key, minus digit-only artifacts ('5,000' tokenizes
    to value fragments, never subject matter). No schema change: every
    stored key already carries this signature."""
    parts = str(record.get("record_key") or "").split("|")
    if len(parts) not in (5, 6):
        return set()
    return {word for word in parts[4].split("+") if word and not word.isdigit()}


_SLOT_AMOUNT_TERMS = frozenset(set(AMOUNT_INTENT) | {"budget", "charge", "rent"})
_SLOT_DATE_TERMS = frozenset(DATE_INTENT)
_SLOT_PERSON_TERMS = frozenset({"who", "whom", "whose", "guest", "speaker",
                                "organizer", "organiser", "contact", "owner"})


def _question_slot(terms: set[str]) -> str | None:
    if terms & _SLOT_AMOUNT_TERMS:
        return "amount"
    if terms & _SLOT_DATE_TERMS:
        return "date"
    if terms & _SLOT_PERSON_TERMS:
        return "person"
    return None


_AMOUNT_SIGNATURE_NOUNS = frozenset(
    {"amount", "budget", "cost", "fee", "price", "payment", "charge", "rent"})


def _carries_slot(record: dict, slot: str) -> bool:
    if slot == "amount":
        has_amount = bool(record.get("amount_value")) or AMOUNT_PATTERN.search(
            f"{record.get('title') or ''} {record.get('detail') or ''}") is not None
        if not has_amount:
            return False
        # An incidental amount (a venue's deposit) is not the asked
        # amount: the record's own matter or statement must be about
        # money (fee/cost/budget/...), not merely mention a figure.
        statement_words = content_terms(
            f"{record.get('title') or ''} {record.get('detail') or ''}")
        return bool((matter_words(record) | statement_words)
                    & _AMOUNT_SIGNATURE_NOUNS)
    if slot == "date":
        return bool(record.get("date_value") or record.get("date_raw"))
    if slot == "person":
        if (record.get("assignee") or "").strip():
            return True
        values = statement_dimension_values(
            record.get("title"), record.get("detail"), {"person"})
        return bool(values.get("person"))
    return True


def _relevance_for(record: dict, terms: set[str], undecided_match: bool) -> dict:
    """Tier A needs shared wording plus matter overlap: at least one
    matter word in the question, so background-only matches ('Spring
    Fest' vs a publicity question) stay contextual. Undecided-path
    admissions are exempt: narrowly gated on both sides already.

    Slot gate: for a slot-specific question (amount/date), a record
    that does not carry that slot can never be Tier A, however well
    its event wording matches — a venue record must not answer a
    budget question. It demotes to Tier C context instead."""
    matter = sorted(matter_words(record) & terms)
    tier = ANSWERABLE if (undecided_match or matter) else CONTEXTUAL
    slot = _question_slot(terms)
    slot_carried = _carries_slot(record, slot) if slot else True
    if tier == ANSWERABLE and slot and not slot_carried and not undecided_match:
        tier = CONTEXTUAL
    return {"tier": tier,
            "shared": len(terms & content_terms(_record_text(record))),
            "matter_overlap": matter}


def _group_records(records: list[dict], usable: dict[int, list[dict]]) -> list[list[dict]]:
    """Union-find over the conservative same-claim predicate plus identity
    validation. Grouping is presentation-level: records are never merged
    or rewritten, and unrelated records sharing a date or amount stay
    separate claims. Usable supports are already resolving and authorized,
    so validation takes them as given without re-checking unit text."""
    parent = {record["id"]: record["id"] for record in records}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left, right):
        parent[find(left)] = find(right)

    def evidence_of(record_id: int) -> list[dict]:
        return [{"evidence_id": item["evidence_id"], "excerpt": item["excerpt"],
                 "unit_text": None, "file": None}
                for item in usable.get(record_id, [])]

    listed = list(records)
    for pos, left in enumerate(listed):
        for right in listed[pos + 1:]:
            left_ids = {item["evidence_id"] for item in usable.get(left["id"], [])}
            right_ids = {item["evidence_id"] for item in usable.get(right["id"], [])}
            if not claims_match(left, right, bool(left_ids & right_ids)):
                continue
            verdict = validate_pair("duplicate", left, right,
                                    evidence_of(left["id"]), evidence_of(right["id"]))
            if verdict["verdict"] == SUPPORTED:
                union(left["id"], right["id"])
    groups: dict[int, list[dict]] = {}
    for record in listed:
        groups.setdefault(find(record["id"]), []).append(record)
    return list(groups.values())


def _append_tiered_claim(plan: dict, group: list[dict], usable: dict[int, list[dict]],
                         terms: set[str], retrieved: set[str], undecided_question: bool) -> None:
    """Build one claim and file it Tier A (answerable) or Tier C
    (contextual). The tier characterizes the synthesized representative
    (explicit-first, lowest id): only answerable claims may synthesize
    answers or join the value/conflict computation."""
    ordered = sorted(group, key=lambda r: (r["statement_kind"] != "explicit", r["id"]))
    first = ordered[0]
    relevance = _relevance_for(
        first, terms, undecided_question and first["statement_kind"] == "unresolved")
    claim = _claim_from_group(group, usable, terms, retrieved)
    claim["relevance"] = relevance
    plan["claims" if relevance["tier"] == ANSWERABLE else "context_claims"].append(claim)


def _claim_sort_key(claim: dict) -> tuple:
    return (-claim["shared"], not claim["overlap"], claim["record_ids"])


def _claim_from_group(group: list[dict], supports: dict[int, list[dict]],
                      terms: set[str], retrieved: set[str]) -> dict:
    ordered = sorted(group, key=lambda r: (r["statement_kind"] != "explicit", r["id"]))
    first = ordered[0]
    evidence = []
    for record in ordered:
        evidence.extend(supports[record["id"]])
    evidence_ids = list(dict.fromkeys(item["evidence_id"] for item in evidence))
    return {"record_ids": [record["id"] for record in ordered],
            "record_type": first["record_type"],
            "matter": sorted(matter_words(first)),
            "status": first["status"],
            "proposal_only": all(record["statement_kind"] == "proposal" for record in ordered),
            "unresolved": first["statement_kind"] == "unresolved",
            "date_value": first["date_value"], "date_raw": first["date_raw"],
            "statement": (first.get("detail") or first["title"]).strip(),
            "evidence_ids": evidence_ids,
            "shared": len(terms & content_terms(_record_text(first))),
            "overlap": bool(retrieved & set(evidence_ids))}


STATUS_INTENTS = (
    ("proposal", ("proposal", "proposals", "proposed")),
    ("conflicted", ("conflicted", "conflicting", "conflicts", "conflict",
                    "disagree", "disagreement", "disagreeing")),
    ("stale", ("stale",)),
    ("superseded", ("superseded", "supersede")),
    ("unresolved", ("unresolved", "undecided", "still open", "not decided")),
)
STATUS_SCOPE_WORDS = ("memor", "record", "list", "show", "which", "what", "any",
                      "are", "is there", "are there", "remaining", "still", "left")
STATUS_LISTING_CAP = 10
# A status term led by a determiner names a document or entity ('the
# proposal' is usually event_proposal.pdf), not a memory-status question.
DETERMINERS = frozenset({"the", "a", "an", "this", "that"})


def _term_counts(asked: str, term: str, explicit_memory: bool) -> bool:
    """True when the term occurs outside a determiner-led noun phrase.

    Every occurrence is checked, so one document reference ('the
    proposal') cannot suppress a genuine second mention. An explicit
    memory scope ('list the stale records') always counts."""
    start = 0
    while True:
        idx = asked.find(term, start)
        if idx < 0:
            return False
        prev = asked[:idx].rstrip().rsplit(None, 1)
        prev_word = prev[-1].strip(".,;:!?\"'()") if prev else ""
        if explicit_memory or prev_word not in DETERMINERS:
            return True
        start = idx + len(term)


def detect_status_intent(question: str) -> list[str]:
    """Narrow status intents: a status term plus a listing-scope word.

    Deliberately does not fire on factual questions that merely mention a
    status word ('was the proposal accepted?'), and never lowers the
    general ≥2-word retrieval threshold. Determiner-led terms ('the
    proposal and the recording agree') name sources, not memory status,
    unless the question is explicitly scoped to memory."""
    asked = (question or "").casefold()
    if not any(scope in asked for scope in STATUS_SCOPE_WORDS):
        return []
    words = set(re.findall(r"[\w-]+", asked))
    explicit_memory = bool(words & {"memory", "memories", "record", "records"})
    return [name for name, terms in STATUS_INTENTS
            if any(_term_counts(asked, term, explicit_memory) for term in terms)]


def _listing_displayable(con, record: dict, require_consent: bool = True) -> tuple[list[str], int, int] | None:
    """Evidence IDs a status listing may show: folder consent gates the
    record; live rows need resolving authorized units; historical rows keep
    provenance unless their live file revoked consent. Returns
    (displayable, withheld, removed, changed) or None when excluded.
    Local serving passes require_consent=False (see authorized_supports)."""
    folder = con.execute("SELECT cloud_consent FROM folders WHERE id=?",
                         (record["folder_id"],)).fetchone()
    if folder is None:
        return None
    if require_consent and not folder["cloud_consent"]:
        return None
    live = record["status"] not in ("stale", "superseded")
    displayable, withheld, removed, changed = [], 0, 0, 0
    for row in con.execute(
            """SELECT e.evidence_id,e.excerpt,u.text,f.cloud_consent AS file_consent
               FROM memory_record_evidence e
               LEFT JOIN units u ON u.evidence_id=e.evidence_id
               LEFT JOIN files f ON f.id=u.file_id
               WHERE e.record_id=? AND e.role='supports'""", (record["id"],)):
        if row["text"] is None:
            if not live:
                removed += 1
            continue
        if normalize_text(row["excerpt"]) not in normalize_text(row["text"]):
            if not live:
                changed += 1
            continue
        if not require_consent or row["file_consent"]:
            displayable.append(row["evidence_id"])
        else:
            withheld += 1
    if live and not displayable:
        return None
    return displayable, withheld, removed, changed


def _status_records(con, folder_id: int | None, intent: str) -> list[dict]:
    filters = {
        "proposal": "m.statement_kind='proposal' AND m.status NOT IN ('stale','superseded')",
        "conflicted": "m.status='conflicted'",
        "stale": "m.status='stale'",
        "superseded": "m.status='superseded'",
        "unresolved": "m.statement_kind='unresolved' AND m.status NOT IN ('stale','superseded')",
    }
    sql = f"SELECT m.* FROM memory_records m WHERE {filters[intent]}"
    args: list = []
    if folder_id is not None:
        sql += " AND m.folder_id=?"
        args.append(folder_id)
    return [dict(row) for row in con.execute(sql + " ORDER BY m.id", args)]


# Deliberately narrow change-history triggers: only explicit "what changed"
# phrasings divert here. Temporal-but-factual questions ("when did the
# venue change?") stay on the units path, exactly as before.
CHANGES_TRIGGERS = ("what changed", "what has changed", "what have changed",
                    "recent changes", "show changes", "list changes",
                    "change history", "changes history")
CHANGES_LISTING_CAP = 10

TRANSITION_LABELS = {"record_created": "Recorded", "record_updated": "Updated",
                     "conflict_marked": "Conflicted", "conflict_cleared": "Conflict cleared",
                     "superseded": "Superseded", "stale_marked": "Stale",
                     "restored": "Restored"}


def detect_changes_intent(question: str) -> bool:
    """True only for explicit change-history phrasings. Narrow by design:
    a factual question that merely contains "change" elsewhere ("when did
    the venue change?") must not divert into a transitions listing."""
    asked = (question or "").casefold()
    return any(trigger in asked for trigger in CHANGES_TRIGGERS)


def _transition_evidence(con, items: list[dict],
                         require_consent: bool = True) -> tuple[list[str], int, int, int]:
    """Snapshot evidence IDs displayable now: live units with contained
    excerpts and current consent. Snapshot text itself is never quoted:
    removed or revoked sources surface as notes, never prose. Returns
    (displayable, withheld, removed, changed)."""
    displayable, withheld, removed, changed = [], 0, 0, 0
    for item in items:
        row = con.execute(
            """SELECT u.text,f.cloud_consent AS file_consent
               FROM units u LEFT JOIN files f ON f.id=u.file_id
               WHERE u.evidence_id=?""", (item.get("evidence_id"),)).fetchone()
        if row is None:
            removed += 1
            continue
        if normalize_text(str(item.get("excerpt") or "")) not in normalize_text(row["text"]):
            changed += 1
            continue
        if not require_consent or row["file_consent"]:
            displayable.append(item["evidence_id"])
        else:
            withheld += 1
    return displayable, withheld, removed, changed


def _changes_plan(db, folder_id: int | None, local: bool = False) -> dict:
    """Recent validated transitions, newest first, capped with a
    remainder count. Cloud serving shows only consented folders'
    transitions; local serving shows all (see authorized_supports)."""
    plan: dict = {"claims": [], "context_claims": [], "conflicts": [], "support_rows": [],
                  "record_ids": [], "changes_mode": True, "transitions": [],
                  "changes_remainder": 0, "conflict_validations": []}
    with db.connect() as con:
        consent = {row["id"]: row["cloud_consent"]
                   for row in con.execute("SELECT id,cloud_consent FROM folders")}
        entries = list_transitions(con, folder_id, limit=CHANGES_LISTING_CAP + 1)
        for entry in entries:
            if not local and not consent.get(entry["folder_id"]):
                continue
            if len(plan["transitions"]) >= CHANGES_LISTING_CAP:
                plan["changes_remainder"] += 1
                continue
            displayable, withheld, removed, changed = _transition_evidence(
                con, entry["evidence"], require_consent=not local)
            notes = []
            if withheld:
                notes.append(f"{withheld} source(s) withheld under current consent")
            if removed:
                notes.append("source removed")
            if changed:
                notes.append("source changed since transition")
            plan["transitions"].append({**entry, "display_evidence": displayable,
                                       "note": "; ".join(notes)})
    plan["record_ids"] = sorted({entry["record_id"] for entry in plan["transitions"]}
                                | {entry["related_record_id"] for entry in plan["transitions"]
                                   if entry["related_record_id"]})
    return plan


def _status_plan(db, folder_id: int | None, intents: list[str], local: bool = False) -> dict:
    """One claim per listed record (no grouping: a listing shows each
    memory separately), capped with a remainder count."""
    plan: dict = {"claims": [], "context_claims": [], "conflicts": [], "support_rows": [],
                  "record_ids": [], "status_mode": intents, "status_remainder": 0,
                  "conflict_validations": []}
    with db.connect() as con:
        for intent in intents:
            for record in _status_records(con, folder_id, intent):
                shown = _listing_displayable(con, record, require_consent=not local)
                if shown is None:
                    continue
                displayable, withheld, removed, changed = shown
                if len(plan["claims"]) >= STATUS_LISTING_CAP:
                    plan["status_remainder"] += 1
                    continue
                notes = []
                if withheld:
                    notes.append(f"{withheld} source(s) withheld under current consent")
                if removed:
                    notes.append("source removed")
                if changed:
                    notes.append("source changed since extraction")
                plan["claims"].append({
                    "record_ids": [record["id"]], "record_type": record["record_type"],
                    "status": record["status"],
                    "proposal_only": record["statement_kind"] == "proposal",
                    "unresolved": record["statement_kind"] == "unresolved",
                    "date_value": record["date_value"], "date_raw": record["date_raw"],
                    "statement": (record.get("detail") or record["title"]).strip(),
                    "evidence_ids": displayable, "shared": 0, "overlap": False,
                    "intent": intent, "note": "; ".join(notes)})
    plan["record_ids"] = sorted({claim["record_ids"][0] for claim in plan["claims"]})
    wanted: list[str] = []
    for claim in plan["claims"]:
        wanted.extend(claim["evidence_ids"])
    plan["support_rows"] = fetch_support_rows(db, folder_id, list(dict.fromkeys(wanted)))
    return plan


def plan_memory(db, question: str, folder_id: int | None, rows, local: bool = False) -> dict:
    """Match eligible, authorized records to the question.

    Returns deduped claims, explicit conflict pairs, and support rows for
    injection. Empty claims/conflicts means the units pipeline runs alone.
    Status-intent questions take a separate listing path keyed off stored
    status, never wording thresholds. Local serving authorizes resolving
    supports without cloud consent (see authorized_supports).
    """
    intents = detect_status_intent(question)
    if intents:
        return _status_plan(db, folder_id, intents, local=local)
    if detect_changes_intent(question):
        return _changes_plan(db, folder_id, local=local)
def match_memory_records(con, folder_id: int | None, terms: set[str],
                         undecided_question: bool, local: bool = False
                         ) -> tuple[list[dict], list[dict], dict[int, list[dict]]]:
    """Eligibility plus wording match shared by answer planning and the
    hybrid memory channel: live, non-inferred, authorized records split
    into matched/conflicted. Rows-independent by construction, so the
    retrieval channel and the planner always agree on Tier A."""
    sql = "SELECT m.* FROM memory_records m"
    args: list = []
    if folder_id is not None:
        sql += " WHERE m.folder_id=?"
        args.append(folder_id)
    sql += " ORDER BY m.id"
    stored = [dict(row) for row in con.execute(sql, args)]
    usable: dict[int, list[dict]] = {}
    for record in stored:
        if record["status"] in ("stale", "superseded"):
            continue
        if record["statement_kind"] == "inferred":
            continue
        supports = authorized_supports(con, record["id"], require_consent=not local)
        if not supports:
            continue
        usable[record["id"]] = supports
    matched, conflicted = [], []
    for record in stored:
        if record["id"] not in usable:
            continue
        if record["statement_kind"] == "unresolved" and not undecided_question:
            continue
        # Relevance needs shared wording (or the undecided gate),
        # else every record citing a retrieved unit would answer every
        # question.
        text_match = _matches_terms(record, terms)
        undecided_match = undecided_question and record["statement_kind"] == "unresolved"
        if not (text_match or undecided_match):
            continue
        (conflicted if record["status"] == "conflicted" else matched).append(record)
    return matched, conflicted, usable


def _slot_display(claim: dict, record: dict | None, slot: str) -> str:
    """Human-readable side of a date/amount rivalry: statement wording
    first, stored field as fallback, empty when neither names a value."""
    if slot == "date":
        return (claim.get("date_raw") or "").strip() or str((record or {}).get("date_value") or "")
    found = AMOUNT_PATTERN.search(claim.get("statement") or "")
    if found:
        return found.group(0).strip()
    # No normalized-code fallback: the synthesizer reads the cited
    # passages instead, and legacy text covers the rest. Internal
    # "INR150" codes must never reach answers.
    return ""


def _conflict_value_entry(first_id: int, second_id: int, validation: dict,
                          first_claim: dict, second_claim: dict,
                          first_record: dict | None = None,
                          second_record: dict | None = None) -> dict:
    """Internal display summary for one validated conflict pair: which
    dimension rivals and each side's value in its own words. Internal
    only (never serialized into the contract); empty values mean the
    synthesizer falls back to the legacy disagreement text."""
    entry: dict = {"record_ids": sorted((first_id, second_id)), "label": "",
                   "values": {}}
    rivalry = (validation.get("details") or {}).get("textual_rivalry") or {}
    if rivalry.get("contested") == 1 and rivalry.get("dimension"):
        entry["label"] = DIMENSION_LABELS[rivalry["dimension"]]
        left_id = validation.get("left_id")
        right_id = validation.get("right_id")
        shows = {left_id: rivalry.get("left_display") or "",
                 right_id: rivalry.get("right_display") or ""}
        entry["values"] = {rid: shows.get(rid, "") for rid in (first_id, second_id)}
        return entry
    first_dates = (first_claim.get("date_value") or None, second_claim.get("date_value") or None)
    slot = "date" if first_dates[0] and first_dates[1] and first_dates[0] != first_dates[1] else "amount"
    entry["label"] = "dates" if slot == "date" else "amounts"
    entry["values"] = {first_id: _slot_display(first_claim, first_record, slot),
                       second_id: _slot_display(second_claim, second_record, slot)}
    return entry


def _promote_validated_rivals(con, plan: dict, matched: list[dict]) -> None:
    """Move validated rival pairs out of Tier A claims into conflicts.

    The sweep normally marks rivals ahead of serving, but records that
    arrive (or revalidate) between sweeps must still surface as the
    disagreement they are, never as one settled claim. Only explicit,
    same-matter, single-record claims qualify; proposals, undecided
    records, and compatible claims are untouched."""
    by_id = {record["id"]: record for record in matched}
    singles = [claim for claim in plan["claims"]
               if len(claim["record_ids"]) == 1
               and not claim["proposal_only"] and not claim["unresolved"]]
    promoted: set[int] = set()
    for pos, first in enumerate(singles):
        first_id = first["record_ids"][0]
        if first_id in promoted:
            continue
        left = by_id.get(first_id)
        if left is None:
            continue
        for second in singles[pos + 1:]:
            second_id = second["record_ids"][0]
            if second_id in promoted:
                continue
            right = by_id.get(second_id)
            if right is None or matter_words(left) != matter_words(right):
                continue
            validation = validate_relationship(con, "conflict", first_id, second_id)
            if validation["verdict"] != SUPPORTED:
                continue
            plan["conflicts"].append([first, second])
            plan["conflict_validations"].append(
                {"record_ids": sorted((first_id, second_id)),
                 "verdict": validation["verdict"], "reasons": validation["reasons"]})
            plan["conflict_values"].append(_conflict_value_entry(
                first_id, second_id, validation, first, second, left, right))
            promoted.add(first_id)
            promoted.add(second_id)
            break
    if promoted:
        plan["claims"] = [claim for claim in plan["claims"]
                          if len(claim["record_ids"]) != 1
                          or claim["record_ids"][0] not in promoted]


def plan_memory(db, question: str, folder_id: int | None, rows, local: bool = False) -> dict:
    """Match eligible, authorized records to the question.

    Returns deduped claims, explicit conflict pairs, and support rows for
    injection. Empty claims/conflicts means the units pipeline runs alone.
    Status-intent questions take a separate listing path keyed off stored
    status, never wording thresholds. Local serving authorizes resolving
    supports without cloud consent (see authorized_supports).
    """
    intents = detect_status_intent(question)
    if intents:
        return _status_plan(db, folder_id, intents, local=local)
    if detect_changes_intent(question):
        return _changes_plan(db, folder_id, local=local)
    plan: dict = {"claims": [], "context_claims": [], "conflicts": [], "support_rows": [],
                  "record_ids": [], "conflict_validations": [], "conflict_values": []}
    retrieved = {row["evidence_id"] for row in rows}
    terms = content_terms(question)
    undecided_question = _is_undecided_question(question)
    with db.connect() as con:
        matched, conflicted, usable = match_memory_records(
            con, folder_id, terms, undecided_question, local)
        # Conflicted records are never served alone: each needs a validated
        # partner that also matches the question. A validated pair about
        # something else must not override this question's answer.
        seen_pairs = set()
        for record in conflicted:
            partners = [partner for partner in
                        conflict_partners(con, record["folder_id"], record["id"])
                        if partner["id"] in usable and partner["id"] != record["id"]
                        and _matches_terms(partner, terms)]
            if not partners:
                continue
            for partner in partners:
                pair = tuple(sorted((record["id"], partner["id"])))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                first_claim = _claim_from_group([record], usable, terms, retrieved)
                second_claim = _claim_from_group([partner], usable, terms, retrieved)
                plan["conflicts"].append([first_claim, second_claim])
                validation = validate_relationship(con, "conflict", pair[0], pair[1])
                plan["conflict_validations"].append(
                    {"record_ids": [pair[0], pair[1]], "verdict": validation["verdict"],
                     "reasons": validation["reasons"]})
                plan["conflict_values"].append(_conflict_value_entry(
                    record["id"], partner["id"], validation,
                    first_claim, second_claim, record, partner))
        groups = _group_records(matched, usable)
        for group in groups:
            dates = {record["date_value"] for record in group if record["date_value"]}
            if len(dates) > 1:
                # Same matter, rival dates: a conflict, not one claim --
                # but only when the pairing itself validates.
                dated = sorted(group, key=lambda r: r["id"])
                validation = validate_relationship(con, "conflict", dated[0]["id"], dated[1]["id"])
                if validation["verdict"] != SUPPORTED:
                    for record in dated:
                        _append_tiered_claim(plan, [record], usable, terms, retrieved,
                                             undecided_question)
                    continue
                first_claim = _claim_from_group([dated[0]], usable, terms, retrieved)
                second_claim = _claim_from_group([dated[1]], usable, terms, retrieved)
                plan["conflicts"].append([first_claim, second_claim])
                plan["conflict_validations"].append(
                    {"record_ids": [dated[0]["id"], dated[1]["id"]],
                     "verdict": validation["verdict"], "reasons": validation["reasons"]})
                plan["conflict_values"].append(_conflict_value_entry(
                    dated[0]["id"], dated[1]["id"], validation,
                    first_claim, second_claim, dated[0], dated[1]))
                continue
            if len(group) > 1:
                # Same grouping artifact, textual values: a validated
                # rival pair splits out as a conflict; the rest tier
                # individually instead of joining one settled claim.
                ordered = sorted(group, key=lambda r: r["id"])
                split = None
                for pos, first in enumerate(ordered):
                    for second in ordered[pos + 1:]:
                        checked = validate_relationship(
                            con, "conflict", first["id"], second["id"])
                        if checked["verdict"] == SUPPORTED:
                            split = (first, second, checked)
                            break
                    if split is not None:
                        break
                if split is not None:
                    first, second, validation = split
                    first_claim = _claim_from_group([first], usable, terms, retrieved)
                    second_claim = _claim_from_group([second], usable, terms, retrieved)
                    plan["conflicts"].append([first_claim, second_claim])
                    plan["conflict_validations"].append(
                        {"record_ids": sorted((first["id"], second["id"])),
                         "verdict": validation["verdict"], "reasons": validation["reasons"]})
                    plan["conflict_values"].append(_conflict_value_entry(
                        first["id"], second["id"], validation,
                        first_claim, second_claim, first, second))
                    for record in ordered:
                        if record["id"] not in (first["id"], second["id"]):
                            _append_tiered_claim(plan, [record], usable, terms, retrieved,
                                                 undecided_question)
                    continue
            _append_tiered_claim(plan, group, usable, terms, retrieved, undecided_question)
        _promote_validated_rivals(con, plan, matched)
        plan["claims"].sort(key=_claim_sort_key)
        plan["context_claims"].sort(key=_claim_sort_key)
    wanted: list[str] = []
    for claim in plan["claims"]:
        wanted.extend(claim["evidence_ids"])
    for pair in plan["conflicts"]:
        for claim in pair:
            wanted.extend(claim["evidence_ids"])
    wanted = list(dict.fromkeys(wanted))
    # Injection is Tier A (+ validated conflicts) only: contextual rows
    # prepended into review context demonstrably flip verdicts (a clean
    # single-value answer turns ambiguous on file count alone), so
    # context claims stay visible in the contract and attribution without
    # entering the pipeline.
    plan["support_rows"] = fetch_support_rows(db, folder_id, wanted)[:INJECT_CAP]
    plan["record_ids"] = sorted({rid for claim in plan["claims"] for rid in claim["record_ids"]}
                                | {rid for claim in plan["context_claims"]
                                   for rid in claim["record_ids"]}
                                | {rid for pair in plan["conflicts"]
                                   for claim in pair for rid in claim["record_ids"]})
    return plan


def memory_pseudo_values(question: str, plan: dict, rows_by_id: dict) -> list[tuple]:
    """Memory claims as (field, value, row) entries joining the established
    value/conflict computation, so a record cannot silently override rival
    source evidence and rivals cannot silently override it."""
    q = question.casefold()
    wants_date = any(term in q for term in DATE_INTENT)
    wants_amount = any(term in q for term in AMOUNT_INTENT)
    if not (wants_date or wants_amount):
        return []
    entries = []
    claims = list(plan["claims"]) + [claim for pair in plan["conflicts"] for claim in pair]
    for claim in claims:
        row = next((rows_by_id[eid] for eid in claim["evidence_ids"] if eid in rows_by_id), None)
        if row is None:
            continue
        if wants_date and claim["date_raw"]:
            entries.append(("date", claim["date_raw"].strip(), row))
        if wants_amount:
            found = AMOUNT_PATTERN.search(claim["statement"])
            if found:
                entries.append(("amount", found.group(0).strip(), row))
    return entries


def _card(row: dict) -> dict:
    try:
        locator = json.loads(row["locator"])
    except (TypeError, json.JSONDecodeError):
        return {}
    return {"id": row["evidence_id"], "file": row["path"], "locator": locator,
            "quote": row["text"][:600],
            "preview_url": f"/api/evidence/{row['evidence_id']}/preview",
            "sensitive": is_sensitive(row["text"], row["path"]),
            "historical": False,
            "record_ids": []}


def record_ids_by_evidence(plan: dict) -> dict[str, list[int]]:
    """Which memory records each cited evidence ID supports (UI attribution).
    Contextual claims attribute wherever their units are cited, without
    influencing the verdict."""
    mapping: dict[str, list[int]] = {}
    claims = (list(plan.get("claims", [])) + list(plan.get("context_claims", []))
              + [claim for pair in plan.get("conflicts", []) for claim in pair])
    for claim in claims:
        for eid in claim["evidence_ids"]:
            for rid in claim["record_ids"]:
                if rid not in mapping.setdefault(eid, []):
                    mapping[eid].append(rid)
    return mapping


def _trim_claim(claim: dict) -> dict:
    trimmed = {"record_ids": claim["record_ids"], "statement": claim["statement"],
               "proposal_only": claim["proposal_only"], "unresolved": claim["unresolved"],
               "date_value": claim["date_value"], "evidence_ids": claim["evidence_ids"],
               "status": claim.get("status")}
    if claim.get("note"):
        trimmed["note"] = claim["note"]
    if claim.get("relevance"):
        trimmed["relevance"] = dict(claim["relevance"])
    return trimmed


def contract_claims(plan: dict) -> dict:
    """UI-ready claim view: answerable and contextual statements with
    qualification flags, citations, per-claim relevance tiers, and the
    validation status of each surfaced conflict pair."""
    return {"claims": [_trim_claim(claim) for claim in plan.get("claims", [])],
            "context_claims": [_trim_claim(claim) for claim in plan.get("context_claims", [])],
            "conflicts": [[_trim_claim(claim) for claim in pair]
                          for pair in plan.get("conflicts", [])],
            "conflict_validations": [dict(entry) for entry in plan.get("conflict_validations", [])]}


def _claim_values(question: str, claim: dict) -> list[dict]:
    q = question.casefold()
    found = []
    if any(term in q for term in DATE_INTENT) and claim["date_raw"]:
        found.append({"field": "date", "value": claim["date_raw"].strip(),
                      "verification": "verified_text", "evidence_ids": claim["evidence_ids"]})
    if any(term in q for term in AMOUNT_INTENT):
        match = AMOUNT_PATTERN.search(claim["statement"])
        if match:
            found.append({"field": "amount", "value": match.group(0).strip(),
                          "verification": "verified_text", "evidence_ids": claim["evidence_ids"]})
    return found


def _unvalidated_rivalry(claims: list[dict]) -> tuple[dict, dict, str] | None:
    """First pair of joinable claims that look like competing values in
    one attribute dimension but never validated as a conflict: same
    matter, a shared dimension, and dimension-attributed values each
    side never mentions. Attribution (not raw markers) decides, so
    compatible claims that merely name different reporters ("Council
    booked X" vs "Committee praised X") join normally. Proposals and
    undecided records are already labeled, so they never trigger."""
    joinable = claims[:3]
    for pos, first in enumerate(joinable):
        if first["proposal_only"] or first["unresolved"]:
            continue
        for second in joinable[pos + 1:]:
            if second["proposal_only"] or second["unresolved"]:
                continue
            if not (set(first.get("matter", [])) & set(second.get("matter", []))):
                continue
            shared = statement_dimensions(first["statement"], "") \
                & statement_dimensions(second["statement"], "")
            if not shared:
                continue
            first_values = statement_dimension_values(first["statement"], "", shared)
            second_values = statement_dimension_values(second["statement"], "", shared)
            first_words = statement_content_words(first["statement"], "")
            second_words = statement_content_words(second["statement"], "")
            for dim in sorted(shared):
                if (any(not value_mentioned(value, second_words)
                        for value in first_values[dim])
                        and any(not value_mentioned(value, first_words)
                                for value in second_values[dim])):
                    return (first, second, dim)
    return None


def _claim_amount(claim: dict) -> str | None:
    """The claim's single normalized amount class, or None when its
    statement names zero or several amounts (never a rivalry side)."""
    klass, _raw = amount_class_of([claim.get("statement") or ""])
    return None if klass in ("noamount", "multi") else klass


def unvalidated_amount_rivalry(claims: list[dict]) -> tuple[dict, dict] | None:
    """First pair of joinable claims asserting different single amounts
    for the same matter without any validated relationship: they must
    be qualified, never settled into one answer and never resolved by
    picking a side. Proposals and undecided records are already
    labeled, so they never trigger."""
    joinable = claims[:3]
    for pos, first in enumerate(joinable):
        if first["proposal_only"] or first["unresolved"]:
            continue
        first_amount = _claim_amount(first)
        if first_amount is None:
            continue
        for second in joinable[pos + 1:]:
            if second["proposal_only"] or second["unresolved"]:
                continue
            if not (set(first.get("matter", [])) & set(second.get("matter", []))):
                continue
            second_amount = _claim_amount(second)
            if second_amount is not None and second_amount != first_amount:
                return (first, second)
    return None


def _conflict_answer(plan: dict, first: dict, second: dict, rows_by_id: dict) -> str:
    """Conflict text naming both rival values with their files. Falls
    back to the legacy statement join when no displayable values were
    captured (hand-built plans, or values neither side states in
    retrievable words). Never explains, dates, or ranks the sides."""
    for entry in plan.get("conflict_values", []):
        if set(entry.get("record_ids", [])) != set(first["record_ids"]) | set(second["record_ids"]):
            continue
        shows = dict(entry.get("values") or {})
        if entry.get("label") == "amounts":
            # Statements often omit the figures ("registration fee"
            # titles): the cited passages carry them instead.
            for claim in (first, second):
                rid = claim["record_ids"][0] if len(claim["record_ids"]) == 1 else None
                if rid is None or shows.get(rid):
                    continue
                for eid in claim["evidence_ids"]:
                    row = rows_by_id.get(eid)
                    found = AMOUNT_PATTERN.search(row["text"]) if row is not None else None
                    if found:
                        shows[rid] = found.group(0).strip()
                        break
        sides = []
        for claim in (first, second):
            rid = claim["record_ids"][0] if len(claim["record_ids"]) == 1 else None
            sides.append(shows.get(rid, "") if rid is not None else "")
        if not all(sides) or not entry.get("label"):
            break
        files = []
        for claim in (first, second):
            name = ""
            for eid in claim["evidence_ids"]:
                row = rows_by_id.get(eid)
                if row is not None:
                    name = Path(row["path"]).name
                    break
            files.append(f" ({name})" if name else "")
        return (f"Records disagree on {entry['label']}: {sides[0]}{files[0]} "
                f"versus {sides[1]}{files[1]}. The available evidence does "
                f"not establish which is final.")
    return (f"Memory records disagree: {first['statement']} "
            f"Versus: {second['statement']}")


def synthesize(question: str, plan: dict, support_rows: list[dict]) -> dict | None:
    """Deterministic answer from memory claims when the units path cannot
    answer. Every statement cites current, authorized evidence; proposals
    are labeled; conflicts name both sides. Unvalidated competing values
    are listed with qualification, never settled into one answer."""
    claims = plan["claims"]
    conflicts = plan["conflicts"]
    if not claims and not conflicts:
        return None
    rows_by_id = {row["evidence_id"]: row for row in support_rows}
    values: list[dict] = []
    if conflicts:
        first, second = conflicts[0]
        evidence_ids = list(dict.fromkeys(first["evidence_ids"] + second["evidence_ids"]))
        values = _claim_values(question, first) + _claim_values(question, second)
        status = "conflict"
        answer = _conflict_answer(plan, first, second, rows_by_id)
    else:
        rival = _unvalidated_rivalry(claims)
        amount_rival = None if rival is not None else unvalidated_amount_rivalry(claims)
        if rival is not None:
            first, second, dimension = rival
            answer = (f"Sources mention different {DIMENSION_LABELS[dimension]} but the "
                      f"evidence is insufficient to determine which applies. "
                      f"First account: {first['statement']} "
                      f"Second account: {second['statement']}")
            evidence_ids = list(dict.fromkeys(first["evidence_ids"] + second["evidence_ids"]))
            values = _claim_values(question, first) + _claim_values(question, second)
            status = "unverified"
        elif amount_rival is not None:
            first, second = amount_rival
            answer = (f"Sources mention different amounts but the "
                      f"evidence is insufficient to determine which applies. "
                      f"First account: {first['statement']} "
                      f"Second account: {second['statement']}")
            evidence_ids = list(dict.fromkeys(first["evidence_ids"] + second["evidence_ids"]))
            values = _claim_values(question, first) + _claim_values(question, second)
            status = "unverified"
        else:
            texts = []
            evidence_ids = []
            for claim in claims[:3]:
                text = claim["statement"]
                if claim["proposal_only"]:
                    text = f"Proposed: {text}"
                texts.append(text)
                evidence_ids.extend(claim["evidence_ids"])
                values.extend(_claim_values(question, claim))
            evidence_ids = list(dict.fromkeys(evidence_ids))
            answer = " ".join(texts)
            # Unresolved-only synthesis is not a settled answer: the
            # sources have not decided, so qualify rather than report
            # `answered`. Questions asking what remains undecided are
            # listing questions and keep `answered`; questions asking
            # for the decided value get `unverified`.
            joined = claims[:3]
            listing_ask = any(hint in question.casefold() for hint in
                              ("remain", "undecided", "still open", "left to",
                               "pending", "yet to"))
            status = ("unverified" if joined and all(c["unresolved"] for c in joined)
                      and not listing_ask else "answered")
    cards = evidence_cards(plan, support_rows, evidence_ids)
    if not cards:
        return None
    if len(answer) > 1200:
        answer = answer[:1200].rsplit(" ", 1)[0] + "…"
    return {"status": status, "answer": answer, "answer_sensitive": is_sensitive(answer, cards[0]["file"]),
            "values": values, "evidence": cards,
            "candidates": [{"file": card["file"], "partial": False} for card in cards],
            "disclosure": []}


HISTORY_TERMS = frozenset({"original", "previous", "first", "earlier",
                           "initial", "formerly"})


def historical_response(db, question: str, folder_id: int | None,
                        require_consent: bool = True) -> dict | None:
    """Deterministic history serving (M2): an original/previous
    amount or date question is answered from superseded records —
    the only place historical slot values are served as an answer,
    explicitly framed as the original value. Exactly one distinct
    historical slot value may answer; anything else abstains to the
    normal pipeline. Current claims are never used here."""
    terms = content_terms(question)
    if not (terms & HISTORY_TERMS):
        return None
    slot = _question_slot(terms)
    if slot not in ("amount", "date"):
        return None
    with db.connect() as con:
        records = [dict(row) for row in con.execute(
            """SELECT * FROM memory_records WHERE status='superseded'
               AND (? IS NULL OR folder_id=?) ORDER BY id""",
            (folder_id, folder_id))]
        usable: dict[int, list[dict]] = {}
        kept = []
        for record in records:
            if not _matches_terms(record, terms):
                continue
            if not _carries_slot(record, slot):
                continue
            supports = authorized_supports(con, record["id"],
                                           require_consent=require_consent)
            if supports:
                usable[record["id"]] = supports
                kept.append(record)
    if not kept:
        return None

    def slot_value(record: dict) -> str | None:
        if slot == "amount":
            return record.get("amount_value")
        return record.get("date_value")

    distinct = {slot_value(r) for r in kept if slot_value(r)}
    if len(distinct) != 1:
        return None
    kept.sort(key=lambda r: r["id"])
    first = kept[0]
    group = _claim_from_group(kept, usable, terms, set())
    support_rows = fetch_support_rows(db, folder_id, group["evidence_ids"])
    cards = evidence_cards({"claims": [group]}, support_rows, group["evidence_ids"])
    if not cards:
        return None
    label = "Originally" if "original" in terms or "first" in terms else "Previously"
    answer = f"{label}, {group['statement']}"
    if len(answer) > 1200:
        answer = answer[:1200].rsplit(" ", 1)[0] + "…"
    return {"status": "answered", "answer": answer,
            "answer_sensitive": is_sensitive(answer, cards[0]["file"]),
            "values": _claim_values(question, group), "evidence": cards,
            "candidates": [{"file": c["file"], "partial": False} for c in cards],
            "disclosure": []}


def evidence_cards(plan: dict, support_rows: list[dict], evidence_ids: list[str]) -> list[dict] | None:
    """Citation cards for memory answers; None when any cited unit is
    missing or unparsable, so claims never outrun their evidence."""
    rows_by_id = {row["evidence_id"]: row for row in support_rows}
    attribution = record_ids_by_evidence(plan)
    cards = []
    for eid in evidence_ids:
        row = rows_by_id.get(eid)
        if row is None:
            return None
        card = _card(row)
        if not card:
            return None
        card["record_ids"] = attribution.get(eid, [])
        cards.append(card)
    return cards


_STATUS_LABELS = {"proposal": "Proposal memories", "conflicted": "Conflicted memories",
                  "stale": "Stale memories", "superseded": "Superseded memories",
                  "unresolved": "Unresolved decisions"}


def _status_qualifier(claim: dict) -> str:
    if claim["status"] in ("stale", "superseded"):
        return claim["status"].capitalize()
    if claim["proposal_only"]:
        return "Proposed"
    if claim["unresolved"]:
        return "Undecided"
    if claim["status"] == "conflicted":
        return "Conflicted"
    return "Current"


def status_listing_response(db, folder_id: int | None, memory: dict, memory_meta: dict) -> dict:
    """Evidence-backed listing for status-intent questions. Stored records
    only: no model calls, no wording thresholds. History is labeled as
    history, never presented as current fact."""
    intents = memory.get("status_mode", [])
    if not memory["claims"]:
        label = " or ".join(name for name, _ in STATUS_INTENTS if name in intents) or "memory"
        return {"status": "answered",
                "answer": f"No {label} memories in the selected scope.",
                "answer_sensitive": False, "values": [], "evidence": [], "candidates": [],
                "disclosure": [], "memory": memory_meta}
    wanted: list[str] = []
    for claim in memory["claims"]:
        wanted.extend(claim["evidence_ids"])
    wanted = list(dict.fromkeys(wanted))
    support_rows = fetch_support_rows(db, folder_id, wanted)
    cards = evidence_cards(memory, support_rows, wanted) or []
    sections: dict[str, list[str]] = {}
    for claim in memory["claims"]:
        line = f"- {claim['statement']} [{_status_qualifier(claim)}"
        if claim.get("note"):
            line += f"; {claim['note']}"
        line += "]"
        sections.setdefault(claim.get("intent", ""), []).append(line)
    parts = []
    for intent in intents:
        lines = sections.get(intent, [])
        if not lines:
            continue
        heading = _STATUS_LABELS[intent] + (" (history, not current facts)"
                                            if intent in ("stale", "superseded") else "")
        parts.append(f"{heading} ({len(lines)}):\n" + "\n".join(lines))
    answer = "\n\n".join(parts)
    if memory.get("status_remainder"):
        answer += (f"\n\n+{memory['status_remainder']} more in the Memory view. "
                   "Each memory above cites its sources below.")
    elif cards:
        answer += "\n\nEach memory above cites its sources below."
    if len(answer) > 1200:
        answer = answer[:1200].rsplit(" ", 1)[0] + "…"
    return {"status": "answered", "answer": answer,
            "answer_sensitive": is_sensitive(answer, cards[0]["file"]) if cards else False,
            "values": [], "evidence": cards,
            "candidates": [{"file": card["file"], "partial": False} for card in cards],
            "disclosure": [], "memory": memory_meta}


def _trim_transition(entry: dict) -> dict:
    trimmed = {"id": entry["id"], "record_id": entry["record_id"],
               "transition": entry["transition"], "reason": entry["reason"],
               "created_at": entry["created_at"],
               "related_record_id": entry.get("related_record_id"),
               "evidence_ids": list(entry.get("display_evidence", []))}
    if entry.get("note"):
        trimmed["note"] = entry["note"]
    return trimmed


def changes_listing_response(db, folder_id: int | None, memory: dict, memory_meta: dict) -> dict:
    """Deterministic ledger-backed answer to change-history questions.
    Stored transitions only: no model calls, no wording thresholds beyond
    the narrow intent gate. Each line names what changed, when, and why,
    citing currently displayable evidence for which source backs it."""
    transitions = memory.get("transitions", [])
    meta = {**memory_meta, "transitions": [_trim_transition(entry) for entry in transitions]}
    if not transitions:
        return {"status": "answered",
                "answer": "No recorded changes in the selected scope.",
                "answer_sensitive": False, "values": [], "evidence": [], "candidates": [],
                "disclosure": [], "memory": meta}
    wanted: list[str] = []
    for entry in transitions:
        wanted.extend(entry.get("display_evidence", []))
    wanted = list(dict.fromkeys(wanted))
    support_rows = fetch_support_rows(db, folder_id, wanted)
    attribution_plan = {"claims": [{"record_ids": [entry["record_id"]],
                                    "evidence_ids": entry.get("display_evidence", [])}
                                   for entry in transitions]}
    cards = evidence_cards(attribution_plan, support_rows, wanted) or []
    # Name winners by title, not bare record id, so the current value
    # is visible in the history answer itself.
    title_by_id: dict[int, str] = {}
    wanted_ids = {entry["record_id"] for entry in transitions} | {
        entry["related_record_id"] for entry in transitions
        if entry.get("related_record_id")}
    if wanted_ids:
        with db.connect() as con:
            marks = ",".join("?" for _ in wanted_ids)
            title_by_id = {row["id"]: row["title"] for row in con.execute(
                f"SELECT id,title FROM memory_records WHERE id IN ({marks})",
                list(wanted_ids))}
    lines = []
    for entry in transitions:
        title = (entry.get("new_state") or {}).get("title") or f"record {entry['record_id']}"
        reason = entry["reason"]
        for record_id, record_title in title_by_id.items():
            reason = reason.replace(f"record {record_id}", f"'{record_title}'")
        line = (f"- {title}: {TRANSITION_LABELS.get(entry['transition'], entry['transition'])} "
                f"[{entry['created_at']}] — {reason}")
        if entry.get("note"):
            line += f" ({entry['note']})"
        lines.append(line)
    answer = "Recent memory changes:\n" + "\n".join(lines)
    if memory.get("changes_remainder"):
        answer += (f"\n\n+{memory['changes_remainder']} more in the ledger. "
                   "Each change above cites its sources below.")
    elif cards:
        answer += "\n\nEach change above cites its sources below."
    if len(answer) > 1200:
        answer = answer[:1200].rsplit(" ", 1)[0] + "…"
    return {"status": "answered", "answer": answer,
            "answer_sensitive": is_sensitive(answer, cards[0]["file"]) if cards else False,
            "values": [], "evidence": cards,
            "candidates": [{"file": card["file"], "partial": False} for card in cards],
            "disclosure": [], "memory": meta}
