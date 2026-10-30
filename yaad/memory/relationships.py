"""Evidence-verified relationship validation (deterministic, no model calls).

Common accept/reject mechanism for conflict, supersession, and
duplicate/identity relationships between memory records. A relationship is
accepted only when the records' evidence establishes that they concern the
same underlying subject or event and that the proposed relationship is
supported.

Verdicts:

* ``supported`` -- the evidence establishes the relationship; callers may act.
* ``contradicted`` -- the evidence refutes the relationship (different
  subjects, incompatible kinds, an older "replacement"); callers must not act.
* ``ambiguous`` -- the evidence is missing, invalid, or too thin to decide;
  callers must not act but must preserve the underlying records.

Validation considers, per relationship kind:

1. Entity and event identity, via proper-noun subject markers corroborated
   between the record statement (title/detail) and its cited excerpts.
   Mutually exclusive corroborated markers contradict the relationship; a
   distinguishing marker named by only one side leaves it ambiguous.
   Matching dates, amounts, or similar keywords alone never establish
   same-subject, and rival values (dates, amounts, revision words) never
   count as subject identity. Proper nouns structurally bound to a
   shared attribute dimension (venue, person, name) are competing
   values, not identity: conflicts additionally require a shared event
   anchor, so different proper nouns alone never force a conflict.
2. Record type and statement kind compatibility.
3. Evidence excerpts and their source identity: each side needs cited
   evidence whose unit still exists (live containment is the stale
   sweep's jurisdiction); conflicts additionally need independent
   statements -- both sides citing the identical excerpt is one
   statement cited twice, while distinct excerpts from one source are
   independent witnesses -- and duplicates treat shared evidence as
   corroborating identity.
4. Temporal information, where applicable: a replacement that predates its
   target cannot supersede it; duplicates must agree on date.

Each relationship keeps its existing kind-specific positive predicate
(``_rivals``, ``_supersession_related``, ``claims_match``); this layer adds
the shared evidence gates on top.
"""

import re

from yaad.query.retrieve import STOPWORDS

SUPPORTED = "supported"
CONTRADICTED = "contradicted"
AMBIGUOUS = "ambiguous"

RELATIONSHIP_KINDS = ("conflict", "supersession", "duplicate")

_TOKEN = re.compile(r"[\w-]+", re.UNICODE)

# Date vocabulary is rival-value territory, never subject identity: "Due
# March" vs "Due April" differ in the asserted value, not the subject.
MONTHS = frozenset({
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
})
WEEKDAYS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "mon", "tue", "wed", "thu", "fri", "sat", "sun",
})
# Amount vocabulary likewise: "Rs 150" vs "Rs 200" differ in value only.
# Symbols (Rs./$/INR/...) either tokenize to these words or vanish with
# their digits; glued forms ("INR150") are dropped as digit-bearing.
CURRENCY_TOKENS = frozenset({"inr", "usd", "eur", "rs", "rupee", "rupees"})
# Recency/revision/change descriptors state an editorial position ("old"
# vs "new", "original" vs "updated"), not entity identity.
REVISION_TOKENS = frozenset({
    "new", "old", "original", "updated", "update", "revised", "revision",
    "previous", "current", "latest", "former", "moved", "move",
    "changed", "change", "corrected", "correction", "rescheduled",
    "postponed", "rebooked", "cancelled", "canceled",
})

# Textual value dimensions: attributes whose competing proper-noun values
# can rival (venues, people, names) once the same event is established.
# Deliberately tight and explicit; dimension words gate candidacy, never
# decide alone. Inflections are listed, never stemmed.
VENUE_WORDS = frozenset({
    "venue", "venues", "location", "locations", "place", "places",
    "site", "sites", "address", "addresses", "hall", "halls",
    "arena", "arenas", "room", "rooms", "auditorium", "auditoriums",
    "theater", "theaters", "theatre", "theatres", "stadium", "stadiums",
    "center", "centers", "centre", "centres", "hotel", "hotels",
    "restaurant", "restaurants", "office", "offices", "campus", "campuses",
    "complex", "ground", "grounds",
})
PERSON_WORDS = frozenset({
    "person", "persons", "people", "lead", "leads", "led", "leading",
    "manage", "manages", "managed", "managing",
    "owner", "owners", "manager", "managers", "assignee", "assignees",
    "assigned", "contact", "contacts", "organizer", "organizers",
    "organiser", "organisers", "host", "hosts", "hosted", "chair",
    "chairs", "chaired", "coordinator", "coordinators", "responsible",
})
NAME_WORDS = frozenset({"name", "names", "named", "called"})
DIMENSIONS = {"venue": VENUE_WORDS, "person": PERSON_WORDS, "name": NAME_WORDS}
DIMENSION_LABELS = {"venue": "venues", "person": "people", "name": "names"}
# Dimension words in subject position: the run before names the value
# ("Alice leads", "Alice responsible"). All other dimension words take
# values only from their containing or following run, so modifiers
# ("Spring Fest venue", "Project manager") stay anchors.
VERB_WORDS = frozenset({
    "lead", "leads", "led", "leading",
    "manage", "manages", "managed", "managing",
    "responsible", "assigned", "hosts", "hosted", "chaired",
})
COPULAS = frozenset({"is", "was", "are", "be"})




def _is_marker(word: str) -> bool:
    folded = word.casefold()
    if len(folded) < 2 or folded in STOPWORDS:
        return False
    if any(char.isdigit() for char in folded):
        return False
    if folded in MONTHS or folded in WEEKDAYS:
        return False
    if folded in CURRENCY_TOKENS or folded in REVISION_TOKENS:
        return False
    return True


def subject_markers(title: str | None, detail: str | None, excerpts: list[str]) -> set[str]:
    """Proper-noun subject markers: capitalized words from the record
    statement and its cited excerpts, minus date/amount/revision
    vocabulary. Entity and event identity is carried by proper nouns;
    common-noun differences are paraphrase or value noise."""
    markers = set()
    for text in (title or "", detail or ""):
        for token in _TOKEN.findall(text):
            if len(token) > 1 and token[0].isupper() and _is_marker(token):
                markers.add(token.casefold())
    for excerpt in excerpts:
        for token in _TOKEN.findall(excerpt or ""):
            if len(token) > 1 and token[0].isupper() and _is_marker(token):
                markers.add(token.casefold())
    return markers


def _content_set(*texts: str | None) -> set[str]:
    words = set()
    for text in texts:
        words.update(word.casefold() for word in _TOKEN.findall(text or "")
                     if len(word) > 1)
    return words


def corroborated_markers(title: str | None, detail: str | None,
                         excerpts: list[str]) -> set[str]:
    """Markers the extractor treated as central: present both in the
    record statement and in its cited excerpts. A capitalized verb or
    adjective appearing once ("Deliver", "Short") is narrative noise,
    not a subject claim."""
    statement = _content_set(title, detail)
    cited = _content_set(*excerpts)
    return {word for word in subject_markers(title, detail, excerpts)
            if word in statement and word in cited}


def compare_subjects(left_title: str | None, left_detail: str | None,
                     left_excerpts: list[str], right_title: str | None,
                     right_detail: str | None, right_excerpts: list[str]
                     ) -> tuple[str | None, str | None]:
    """Subject-identity signal between two records.

    Returns (verdict, reason) with verdict ``contradicted`` when both
    sides corroborate different subjects, ``ambiguous`` when only one
    side names a distinguishing subject, or (None, None) when the
    markers say nothing either way. One-sided omission is
    underspecification, not contradiction.
    """
    left_all = subject_markers(left_title, left_detail, left_excerpts)
    right_all = subject_markers(right_title, right_detail, right_excerpts)
    left_only = sorted(corroborated_markers(left_title, left_detail, left_excerpts) - right_all)
    right_only = sorted(corroborated_markers(right_title, right_detail, right_excerpts) - left_all)
    if left_only and right_only:
        return (CONTRADICTED,
                f"records name different subjects ({', '.join(left_only)} vs "
                f"{', '.join(right_only)})")
    if left_only or right_only:
        return (AMBIGUOUS,
                "same subject not established: only one side names "
                f"{', '.join(left_only or right_only)}")
    return None, None


def statement_words(title: str | None, detail: str | None) -> set[str]:
    """Casefolded statement words (title plus detail)."""
    return _content_set(title, detail)


def statement_dimensions(title: str | None, detail: str | None) -> set[str]:
    """Attribute dimensions named anywhere in the statement."""
    words = statement_words(title, detail)
    return {dim for dim, lexicon in DIMENSIONS.items() if words & lexicon}


def _attributed_markers(title: str | None, detail: str | None,
                        dimensions: set[str]) -> dict[str, set[str]]:
    """Statement markers structurally bound to a dimension word: the
    maximal capitalized run containing it (type nouns: "Riverside
    Hall"), the run before a subject verb ("Alice leads"), or one
    token after it, past an optional copula ("name is Falcon"). A
    lowercase gap ("leads the website") or a multi-token tail after a
    noun ("Spring Fest venue") breaks attribution, so nearby identity
    nouns stay anchors, never values. Runs never cross sentence or
    field boundaries, so punctuation (which tokenization erases) still
    contains them. A run holding dimension words of several dimensions
    (compressed titles: "Lead Alice Spring Fest Riverside Hall") is
    partitioned by nearest dimension word instead of attributed
    wholesale; equidistant markers go to every tied dimension, the
    conservative direction."""
    segments = []
    for field in (title or "", detail or ""):
        segments.extend(re.split(r"[.!?;\n]+", field))
    bound: dict[str, set[str]] = {dim: set() for dim in dimensions}
    for segment in segments:
        tokens = _TOKEN.findall(segment)
        folded = [token.casefold() for token in tokens]
        capped = [len(token) > 1 and token[0].isupper() and _is_marker(token)
                  for token in tokens]
        dim_at = [next((dim for dim in sorted(dimensions) if word in DIMENSIONS[dim]), None)
                  for word in folded]
        for pos, word in enumerate(folded):
            dim = dim_at[pos]
            if dim is None:
                continue
            if capped[pos]:
                start, end = pos, pos
                while start - 1 >= 0 and capped[start - 1]:
                    start -= 1
                while end + 1 < len(tokens) and capped[end + 1]:
                    end += 1
                for idx in range(start, end + 1):
                    nearest: dict[str, int] = {}
                    for other in range(start, end + 1):
                        other_dim = dim_at[other]
                        if other_dim is None:
                            continue
                        dist = abs(idx - other)
                        if other_dim not in nearest or dist < nearest[other_dim]:
                            nearest[other_dim] = dist
                    if not nearest:
                        continue
                    closest = min(nearest.values())
                    for cand_dim, dist in nearest.items():
                        if dist == closest:
                            bound[cand_dim].add(folded[idx])
                continue
            if word in VERB_WORDS:
                prev = pos - 1
                while prev >= 0 and capped[prev]:
                    bound[dim].add(folded[prev])
                    prev -= 1
            nxt = pos + 1
            if nxt < len(tokens) and folded[nxt] in COPULAS:
                nxt += 1
            if nxt < len(tokens) and capped[nxt] \
                    and (nxt + 1 >= len(tokens) or not capped[nxt + 1]):
                bound[dim].add(folded[nxt])
    return bound


# Explicit labels licensing lowercase value capture ("Venue: riverside
# hall", "The venue is riverside hall"). Only unambiguous labels
# qualify: bare nouns elsewhere never source lowercase values, and
# idiomatic "place" ("took place downtown") is excluded.
LABEL_WORDS = frozenset({"venue", "venues", "location", "locations"})
# Type nouns licensing adjacent numeric identifiers ("Meeting room 5").
DIGIT_NOUNS = frozenset({"room", "rooms", "hall", "halls"})
# Past participles and gerunds after a label are predicates, not names
# ("The venue is booked"): never captured as values.
_PARTICIPLE_SUFFIXES = ("ed", "ing")


def _extended_values(title: str | None, detail: str | None,
                     dimensions: set[str]) -> dict[str, set[str]]:
    """Lowercase and numeric values bound to explicit structure: up to
    three lowercase tokens after a venue/location label (past an
    optional copula), or a pure-digit token directly after a
    room/hall noun. Capitalized words belong to marker runs, never
    here; stopwords, dimension words, dates, participles, and digits
    (outside the room rule) end capture. Backstop-grade evidence:
    enough to qualify an answer, never to validate a conflict."""
    found: dict[str, set[str]] = {dim: set() for dim in dimensions}
    if "venue" not in dimensions:
        return found
    segments = []
    for field in (title or "", detail or ""):
        segments.extend(re.split(r"[.!?;\n]+", field))
    for segment in segments:
        tokens = _TOKEN.findall(segment)
        folded = [token.casefold() for token in tokens]
        for pos, word in enumerate(folded):
            if word in DIGIT_NOUNS and pos + 1 < len(tokens) and tokens[pos + 1].isdigit():
                found["venue"].add(f"{word} {tokens[pos + 1]}")
                continue
            if word not in LABEL_WORDS:
                continue
            nxt = pos + 1
            if nxt < len(tokens) and folded[nxt] in COPULAS:
                nxt += 1
            captured = []
            while nxt < len(tokens) and len(captured) < 3:
                token, low = tokens[nxt], folded[nxt]
                if len(token) <= 1 or token[0].isupper() or low in STOPWORDS:
                    break
                if any(low in DIMENSIONS[dim] for dim in dimensions):
                    break
                if low in MONTHS or low in WEEKDAYS or low in CURRENCY_TOKENS \
                        or low in REVISION_TOKENS or any(ch.isdigit() for ch in low):
                    break
                if len(low) > 3 and low.endswith(_PARTICIPLE_SUFFIXES):
                    break
                captured.append(low)
                nxt += 1
            if captured:
                found["venue"].add(" ".join(captured))
    return found


def statement_dimension_values(title: str | None, detail: str | None,
                               dimensions: set[str]) -> dict[str, set[str]]:
    """Per-dimension value sets from statement structure: attributed
    proper-noun markers plus explicitly labeled lowercase and numeric
    values. Statements-only (no corroboration); serve-time qualification
    uses this, formal validation does not."""
    attributed = _attributed_markers(title, detail, dimensions)
    extended = _extended_values(title, detail, dimensions)
    return {dim: set(attributed.get(dim, ())) | set(extended.get(dim, ()))
            for dim in dimensions}


def statement_content_words(title: str | None, detail: str | None) -> set[str]:
    """Casefolded statement words including digit tokens, for
    mentioned-testing multi-token and numeric values."""
    words = set()
    for text in (title or "", detail or ""):
        words.update(word.casefold() for word in _TOKEN.findall(text)
                     if len(word) > 1 or word.isdigit())
    return words


def value_mentioned(value: str, words: set[str]) -> bool:
    """True when every token of a (possibly multi-token) value appears
    in the word set."""
    parts = value.split()
    return bool(parts) and all(part in words for part in parts)


def _textual_rivalry(left_title: str | None, left_detail: str | None,
                     left_excerpts: list[str], right_title: str | None,
                     right_detail: str | None, right_excerpts: list[str]
                     ) -> tuple[int, str | None, set[str], set[str], set[str], set[str]]:
    """Competing proper-noun values in a shared attribute dimension.

    Returns (contested, dimension, left_values, right_values,
    left_excluded, right_excluded). A dimension is contested only when
    both sides corroborate value markers the other side never mentions.
    Zero contested means no textual rivalry; more than one means the
    pair differs on multiple dimensions at once. The excluded sets hold
    each side's differing attributed markers; attributed markers both
    sides share stay event anchors, so "Spring Fest venue" word order
    never dissolves the anchor it names.
    """
    left_words = _content_set(left_title, left_detail, *left_excerpts)
    right_words = _content_set(right_title, right_detail, *right_excerpts)
    left_dims = {dim for dim, lexicon in DIMENSIONS.items() if left_words & lexicon}
    right_dims = {dim for dim, lexicon in DIMENSIONS.items() if right_words & lexicon}
    shared = left_dims & right_dims
    if not shared:
        return (0, None, set(), set(), set(), set())
    left_corr = corroborated_markers(left_title, left_detail, left_excerpts)
    right_corr = corroborated_markers(right_title, right_detail, right_excerpts)
    left_attr = _attributed_markers(left_title, left_detail, shared)
    right_attr = _attributed_markers(right_title, right_detail, shared)
    contested = 0
    dimension: str | None = None
    left_values: set[str] = set()
    right_values: set[str] = set()
    left_excluded: set[str] = set()
    right_excluded: set[str] = set()
    for dim in sorted(shared):
        ldiff = {m for m in left_attr[dim] if m in left_corr and m not in right_words}
        rdiff = {m for m in right_attr[dim] if m in right_corr and m not in left_words}
        left_excluded |= ldiff
        right_excluded |= rdiff
        if ldiff and rdiff:
            contested += 1
            dimension, left_values, right_values = dim, ldiff, rdiff
    if contested != 1:
        dimension, left_values, right_values = None, set(), set()
    return (contested, dimension, left_values, right_values, left_excluded, right_excluded)


def compare_anchored_subjects(left_title: str | None, left_detail: str | None,
                              left_excerpts: list[str], right_title: str | None,
                              right_detail: str | None, right_excerpts: list[str],
                              left_excluded: set[str], right_excluded: set[str]
                              ) -> tuple[str | None, str | None]:
    """Subject-identity signal over anchor markers only: corroborated
    markers minus each side's differing dimension values. Values
    compete; they never veto identity by themselves."""
    left_all = subject_markers(left_title, left_detail, left_excerpts) - left_excluded
    right_all = subject_markers(right_title, right_detail, right_excerpts) - right_excluded
    left_only = sorted(corroborated_markers(left_title, left_detail, left_excerpts)
                       - left_excluded - right_all)
    right_only = sorted(corroborated_markers(right_title, right_detail, right_excerpts)
                        - right_excluded - left_all)
    if left_only and right_only:
        return (CONTRADICTED,
                f"records name different subjects ({', '.join(left_only)} vs "
                f"{', '.join(right_only)})")
    if left_only or right_only:
        return (AMBIGUOUS,
                "same subject not established: only one side names "
                f"{', '.join(left_only or right_only)}")
    return None, None


def _ordered_values(title: str | None, detail: str | None, values: set[str]) -> list[str]:
    """Value markers in statement order with source casing, for
    readable rivalry reasons and conflict answers."""
    ordered = []
    seen = set()
    for token in _TOKEN.findall(f"{title or ''} {detail or ''}"):
        folded = token.casefold()
        if folded in values and folded not in seen:
            seen.add(folded)
            ordered.append(token)
    return ordered or sorted(values)


def _assignee_of(record: dict) -> str:
    return (record.get("assignee") or "").strip().casefold()


def _signature_of(record: dict) -> str:
    parts = str(record.get("record_key") or "").split("|")
    return parts[4] if len(parts) in (5, 6) else ""


def _result(kind: str, left_id: int | None, right_id: int | None,
            verdict: str, reasons: list[str], details: dict) -> dict:
    return {"kind": kind, "left_id": left_id, "right_id": right_id,
            "verdict": verdict, "reasons": list(reasons), "details": details}


def _details(left: dict, right: dict, left_supports: list[dict],
             right_supports: list[dict], left_ok: list[dict],
             right_ok: list[dict]) -> dict:
    left_excerpts = [str(item.get("excerpt") or "") for item in left_supports]
    right_excerpts = [str(item.get("excerpt") or "") for item in right_supports]
    left_ids = {str(item.get("evidence_id")) for item in left_supports}
    right_ids = {str(item.get("evidence_id")) for item in right_supports}
    return {
        "left_markers": sorted(subject_markers(left.get("title"), left.get("detail"), left_excerpts)),
        "right_markers": sorted(subject_markers(right.get("title"), right.get("detail"), right_excerpts)),
        "left_corroborated": sorted(corroborated_markers(
            left.get("title"), left.get("detail"), left_excerpts)),
        "right_corroborated": sorted(corroborated_markers(
            right.get("title"), right.get("detail"), right_excerpts)),
        "shared_evidence": sorted(left_ids & right_ids),
        "left_files": sorted({str(item.get("file")) for item in left_supports
                              if item.get("file")}),
        "right_files": sorted({str(item.get("file")) for item in right_supports
                               if item.get("file")}),
        "left_evidenced": len(left_ok),
        "right_evidenced": len(right_ok),
    }


def validate_pair(kind: str, left, right,
                  left_supports: list[dict], right_supports: list[dict],
                  allow_conflicted_claimant: bool = False) -> dict:
    """Validate one proposed relationship between two records.

    ``left``/``right`` are record mappings (sqlite rows or dicts);
    supports are ``{"evidence_id", "excerpt", "unit_text", "file",
    "unit_present"}`` mappings with role ``supports`` only. ``unit_text``
    of None means the caller pre-filtered to resolving supports (serving
    time: taken as given); otherwise ``unit_present`` tells whether the
    cited unit still exists. For ``supersession``, left is the claimant
    (replacement) and right is the target. ``allow_conflicted_claimant``
    is a narrow supersession-only exception the caller may set after
    independently validating that the target is the claimant's own
    conflict partner; stale, proposed, and inferred claimants stay
    rejected, and every other constraint still applies.
    """
    if kind not in RELATIONSHIP_KINDS:
        raise ValueError(f"unknown relationship kind: {kind!r}")
    # Function-level import: yaad.memory.records imports this module, so
    # a top-level import back would be circular.
    from yaad.memory.records import claims_match, kind_group_of, normalize_text

    left, right = dict(left), dict(right)
    left_id, right_id = left.get("id"), right.get("id")

    def evidenced(supports: list[dict]) -> list[dict]:
        """Supports whose cited unit still exists. Live containment is
        the stale sweep's jurisdiction: a rewritten excerpt makes the
        record stale, it does not by itself invalidate a rivalry."""
        return [item for item in supports
                if item.get("unit_text") is None or item.get("unit_present", True)]

    left_ok = evidenced(left_supports)
    right_ok = evidenced(right_supports)
    details = _details(left, right, left_supports, right_supports, left_ok, right_ok)
    left_excerpts = [str(item.get("excerpt") or "") for item in left_supports]
    right_excerpts = [str(item.get("excerpt") or "") for item in right_supports]
    details["textual_rivalry"] = {"contested": 0, "dimension": None,
                                  "left_values": [], "right_values": [],
                                  "left_display": "", "right_display": ""}

    def fail(verdict: str, reason: str) -> dict:
        return _result(kind, left_id, right_id, verdict, [reason], details)

    def textual():
        rivalry = _textual_rivalry(
            left.get("title"), left.get("detail"), left_excerpts,
            right.get("title"), right.get("detail"), right_excerpts)
        details["textual_rivalry"] = {
            "contested": rivalry[0], "dimension": rivalry[1],
            "left_values": sorted(rivalry[2]), "right_values": sorted(rivalry[3]),
            "left_display": " ".join(_ordered_values(
                left.get("title"), left.get("detail"), rivalry[2])),
            "right_display": " ".join(_ordered_values(
                right.get("title"), right.get("detail"), rivalry[3]))}
        return rivalry

    def textual_conflict() -> dict:
        contested, dimension, left_values, right_values, left_excluded, right_excluded = textual()
        if contested == 0:
            return fail(CONTRADICTED, "records assert the same value; no rivalry to validate")
        if contested > 1 or dimension is None:
            return fail(AMBIGUOUS,
                        "records differ in multiple attribute dimensions; likely different events")
        left_anchor = (corroborated_markers(left.get("title"), left.get("detail"), left_excerpts)
                       - left_excluded)
        right_anchor = (corroborated_markers(right.get("title"), right.get("detail"), right_excerpts)
                        - right_excluded)
        if not (left_anchor & right_anchor):
            return fail(AMBIGUOUS,
                        "same event not established: no shared anchor markers")
        subject_verdict, subject_reason = compare_anchored_subjects(
            left.get("title"), left.get("detail"), left_excerpts,
            right.get("title"), right.get("detail"), right_excerpts,
            left_excluded, right_excluded)
        if subject_verdict is not None:
            return fail(subject_verdict, subject_reason or "subjects differ")
        first = " ".join(_ordered_values(left.get("title"), left.get("detail"), left_values))
        second = " ".join(_ordered_values(right.get("title"), right.get("detail"), right_values))
        return _result(
            kind, left_id, right_id, SUPPORTED,
            [f"same event with competing {DIMENSION_LABELS[dimension]}: "
             f"{first} vs {second}"], details)

    if kind == "supersession":
        claimant_kind = left.get("statement_kind")
        if claimant_kind != "explicit":
            return fail(CONTRADICTED,
                        f"a {claimant_kind} record cannot supersede a confirmed decision")
        if left.get("status") not in ("decided", "open") and not (
                allow_conflicted_claimant and left.get("status") == "conflicted"):
            return fail(CONTRADICTED,
                        f"replacement is {left.get('status')}; only a current record can supersede")
        if right.get("statement_kind") == "inferred":
            return fail(CONTRADICTED,
                        "target is inferred experiment output, not a servable claim")
        if right.get("status") in ("stale", "superseded"):
            return fail(CONTRADICTED, f"target is {right.get('status')}, not current")
        created_left, created_right = left.get("created_at") or "", right.get("created_at") or ""
        if created_left and created_right and created_left < created_right:
            return fail(CONTRADICTED,
                        "replacement predates the record it claims to replace")
        if not left_ok or not right_ok:
            missing = left_id if not left_ok else right_id
            return fail(AMBIGUOUS,
                        f"missing evidence: record {missing} cites no existing unit")
    else:
        if left.get("record_type") != right.get("record_type"):
            return fail(CONTRADICTED,
                        f"different record types ({left.get('record_type')} vs "
                        f"{right.get('record_type')}); not the same claim")
        if kind_group_of(left.get("statement_kind")) != kind_group_of(right.get("statement_kind")):
            return fail(CONTRADICTED,
                        f"different statement kinds ({left.get('statement_kind')} vs "
                        f"{right.get('statement_kind')}); not the same claim")
        if _assignee_of(left) != _assignee_of(right):
            return fail(CONTRADICTED, "records name different assignees")
        if not left_ok or not right_ok:
            missing = left_id if not left_ok else right_id
            return fail(AMBIGUOUS,
                        f"missing evidence: record {missing} cites no existing unit")
        if kind == "conflict":
            if _same_statements(left_ok, right_ok, normalize_text):
                return fail(AMBIGUOUS,
                            "both sides cite the identical statement; not independent witnesses")
            if not _assert_rival_values(left, right):
                return textual_conflict()
            if _differs_on_multiple_slots(left, right):
                return fail(AMBIGUOUS,
                            "records differ on multiple independent values (date and amount); "
                            "likely different events")
        else:  # duplicate
            if _dates_differ(left, right):
                return fail(CONTRADICTED, "records assert different dates; not the same claim")
            if not claims_match(left, right, bool(details["shared_evidence"])):
                return fail(AMBIGUOUS, "insufficient shared wording to establish identity")
            contested, dimension, _, _, _, _ = textual()
            if contested > 1:
                return fail(CONTRADICTED,
                            "records differ in multiple attribute dimensions; not the same claim")
            if contested == 1:
                label = DIMENSION_LABELS[dimension or "venue"]
                return fail(CONTRADICTED,
                            f"records assert competing {label} values; not the same claim")
            if details["shared_evidence"]:
                return _result(kind, left_id, right_id, SUPPORTED,
                               ["same subject, type, and date with shared evidence"], details)

    if kind == "conflict":
        # Slot path only: date/amount rivalry keeps the full-marker
        # subject veto, exactly as before textual values existed.
        subject_verdict, subject_reason = compare_subjects(
            left.get("title"), left.get("detail"), left_excerpts,
            right.get("title"), right.get("detail"), right_excerpts)
        if subject_verdict is not None:
            return fail(subject_verdict, subject_reason or "subjects differ")
        return _result(kind, left_id, right_id, SUPPORTED,
                       ["same subject; independent sources assert different values"], details)

    _, _, _, _, left_excluded, right_excluded = textual()
    subject_verdict, subject_reason = compare_anchored_subjects(
        left.get("title"), left.get("detail"), left_excerpts,
        right.get("title"), right.get("detail"), right_excerpts,
        left_excluded, right_excluded)
    if subject_verdict is not None:
        return fail(subject_verdict, subject_reason or "subjects differ")
    if kind == "supersession":
        return _result(kind, left_id, right_id, SUPPORTED,
                       ["same subject; current explicit replacement with distinct resolving evidence"],
                       details)
    return _result(kind, left_id, right_id, SUPPORTED,
                   ["same subject, type, and date with strong wording overlap"], details)


def _assert_rival_values(left: dict, right: dict) -> bool:
    """Same matter asserting different dates or different single amounts
    (stored-field mirror of the sweep's rivalry predicate)."""
    if _signature_of(left) != _signature_of(right):
        return False
    if left.get("date_value") and right.get("date_value") \
            and left.get("date_value") != right.get("date_value"):
        return True
    left_amount, right_amount = left.get("amount_value"), right.get("amount_value")
    return bool(left_amount and right_amount and left_amount != right_amount)


def _differs_on_multiple_slots(left: dict, right: dict) -> bool:
    slots = 0
    if left.get("date_value") and right.get("date_value") \
            and left.get("date_value") != right.get("date_value"):
        slots += 1
    left_amount, right_amount = left.get("amount_value"), right.get("amount_value")
    if left_amount and right_amount and left_amount != right_amount:
        slots += 1
    return slots > 1


def _dates_differ(left: dict, right: dict) -> bool:
    return (left.get("date_value") or None) != (right.get("date_value") or None)


def _same_statements(left_ok: list[dict], right_ok: list[dict], normalize) -> bool:
    """True when both sides cite the identical statement(s): one statement
    cited twice is not a disagreement between witnesses. Distinct
    excerpts -- even from one source, as when a notice corrects itself --
    are independent statements."""
    left_texts = {normalize(str(item.get("excerpt") or "")) for item in left_ok}
    right_texts = {normalize(str(item.get("excerpt") or "")) for item in right_ok}
    left_texts.discard("")
    right_texts.discard("")
    return bool(left_texts) and left_texts == right_texts


def validate_relationship(con, kind: str, left_id: int, right_id: int,
                          allow_conflicted_claimant: bool = False) -> dict:
    """Database-backed validation: fetch both records with their
    supporting excerpts, live unit texts, and source files, then
    validate. Missing records are ambiguous, never an exception."""
    left = con.execute("SELECT * FROM memory_records WHERE id=?", (left_id,)).fetchone()
    right = con.execute("SELECT * FROM memory_records WHERE id=?", (right_id,)).fetchone()
    if left is None or right is None:
        return {"kind": kind, "left_id": left_id, "right_id": right_id,
                "verdict": AMBIGUOUS, "reasons": ["record not found"], "details": {}}

    def supports(record_id: int) -> list[dict]:
        rows = []
        for row in con.execute(
                """SELECT e.evidence_id,e.excerpt,u.text AS unit_text,f.path AS file,
                          u.evidence_id AS unit_present
                   FROM memory_record_evidence e
                   LEFT JOIN units u ON u.evidence_id=e.evidence_id
                   LEFT JOIN files f ON f.id=u.file_id
                   WHERE e.record_id=? AND e.role='supports'""", (record_id,)):
            item = dict(row)
            item["unit_present"] = item["unit_present"] is not None
            if item["unit_text"] is None:
                # Deleted unit: normalize to "" so None keeps its narrow
                # meaning (caller-assured resolving supports).
                item["unit_text"] = ""
            rows.append(item)
        return rows

    return validate_pair(kind, left, right, supports(left_id), supports(right_id),
                           allow_conflicted_claimant=allow_conflicted_claimant)
