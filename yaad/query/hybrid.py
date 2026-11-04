"""Deterministic hybrid retrieval (Phase 1): FTS5 keyword and Tier A
memory channels fused with Reciprocal Rank Fusion.

The keyword channel is the preserved search() core (plural-folded query
terms, one verb/noun fold class, multi-part subquery union, exact-phrase
ranking bonus, per-channel depth). The memory
channel ranks supporting units of Tier A matched records only --
contextual records never contribute candidates. Fused candidates flow
through every existing downstream gate unchanged (consent, taint,
validation, relevance tiers); fusion changes which candidates the gates
see, never what they accept.

The default channels make no embeddings and no model calls. The opt-in
"semantic" channel (OPTIONAL_CHANNELS, Settings.semantic_retrieval,
default off) adds a local embedding ranker over the same candidate
universe under the same rules; it changes candidate recall only, never
acceptance.
"""

from yaad.query.memory_answer import (
    ANSWERABLE,
    _is_undecided_question,
    _relevance_for,
    content_terms,
    match_memory_records,
)
from yaad.query.retrieve import STOPWORDS, fold_term, phrase_hits, question_phrases, search, split_subqueries, word_folds

CHANNELS = ("keyword", "memory")
# Opt-in only: fused solely when explicitly requested (see
# Settings.semantic_retrieval). Never part of the default set.
OPTIONAL_CHANNELS = ("semantic",)
RRF_K = 60
CHANNEL_DEPTH = 50
FUSED_TOP_N = 30
SUBQUERY_DEPTH = 20


def rrf_scores(ranked: dict[str, list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion scores: sum over channels of 1/(k+rank).
    Needs no score calibration across heterogeneous rankers."""
    scores: dict[str, float] = {}
    for ids in ranked.values():
        for rank, evidence_id in enumerate(ids, start=1):
            scores[evidence_id] = scores.get(evidence_id, 0.0) + 1.0 / (k + rank)
    return scores


def rrf_fuse(ranked: dict[str, list[str]], k: int = RRF_K) -> list[str]:
    """Fused order: score descending, evidence_id ascending. Fully
    deterministic; README demotion is applied by hybrid_search, which is
    the layer that holds file paths."""
    scores = rrf_scores(ranked, k)
    return sorted(scores, key=lambda eid: (-scores[eid], eid))


def _folded_query(question: str) -> str:
    """Original question terms plus their singular folds plus the
    quote/caterer fold-class equivalents. Folding only ever adds the
    question's own morphology (and its one mapped class) alongside the
    originals, so the folded query retrieves a superset of the plain
    one."""
    import re
    terms = [term for term in re.findall(r"[\w-]+", (question or "").casefold(),
                                         flags=re.UNICODE)
             if len(term) > 1 and term not in STOPWORDS]
    expanded: list[str] = []
    for term in terms:
        expanded.append(term)
        folded = fold_term(term)
        if folded != term:
            expanded.append(folded)
        for variant in word_folds(term):
            if variant not in expanded:
                expanded.append(variant)
    return " ".join(expanded)


def _keyword_channel(db, question: str, depth: int, folder_id, file_kinds) -> list[str]:
    clauses = split_subqueries(question)
    if len(clauses) <= 1:
        rows = search(db, _folded_query(question), folder_id, depth, file_kinds)
    else:
        # Multi-part question: retrieve per clause with a capped depth so
        # one clause cannot drown the other's gold, then union before RRF.
        per = min(depth, SUBQUERY_DEPTH)
        rows = []
        seen: set[str] = set()
        for clause in clauses:
            for row in search(db, _folded_query(clause), folder_id, per, file_kinds):
                if row["evidence_id"] not in seen:
                    seen.add(row["evidence_id"])
                    rows.append(row)
    phrases = question_phrases(question)
    if phrases:
        # Stable bonus sort: phrase hits first, today's (readme, BM25)
        # SQL order preserved within equal hits.
        rows = sorted(rows, key=lambda row: -phrase_hits(row["text"], phrases))
    return [row["evidence_id"] for row in rows[:depth]]


def _memory_channel(db, question: str, depth: int, folder_id, local: bool) -> list[str]:
    terms = content_terms(question)
    undecided = _is_undecided_question(question)
    with db.connect() as con:
        matched, _, usable = match_memory_records(con, folder_id, terms, undecided, local)
        tier_a = [record for record in matched
                  if _relevance_for(record, terms,
                                    undecided and record["statement_kind"] == "unresolved"
                                    )["tier"] == ANSWERABLE]
        tier_a.sort(key=lambda record: (-len(terms & content_terms(
            f"{record['title']} {record.get('detail') or ''}")), record["id"]))
        ordered: list[str] = []
        for record in tier_a:
            # Evidence_id order is rank-stable; the record order above
            # carries the relevance signal (usable row order itself is a
            # scan-order artifact with no ORDER BY behind it).
            for item in sorted(usable.get(record["id"], []),
                               key=lambda item: item["evidence_id"]):
                if item["evidence_id"] not in ordered:
                    ordered.append(item["evidence_id"])
    return ordered[:depth]


def _semantic_channel(db, question: str, depth: int, folder_id, file_kinds) -> list[str]:
    from yaad.query.embeddings import semantic_rank
    return semantic_rank(db, question, depth, folder_id, file_kinds)


def retrieve_channel(db, question: str, channel: str, depth: int = CHANNEL_DEPTH,
                     folder_id: int | None = None, file_kinds=None,
                     local: bool = False) -> list[str]:
    """Ranked evidence_ids from one channel (per-channel ablation entry
    point). Unknown channels fail fast; file_kinds applies to keyword
    retrieval only."""
    if channel not in CHANNELS + OPTIONAL_CHANNELS:
        raise ValueError(f"unknown retrieval channel: {channel!r}")
    if channel == "keyword":
        return _keyword_channel(db, question, depth, folder_id, file_kinds)
    if channel == "semantic":
        return _semantic_channel(db, question, depth, folder_id, file_kinds)
    return _memory_channel(db, question, depth, folder_id, local)


def route_channels(question: str, semantic_enabled: bool = False) -> tuple[str, ...]:
    """Channel selection: keyword+memory always; semantic joins when
    enabled and the question needs it -- complex questions plus short
    paraphrase-prone ones (at most 3 content terms), where lexical
    mismatch is likeliest. Term-rich simple questions skip the query
    embedding: keyword retrieval already covers them."""
    if not semantic_enabled:
        return CHANNELS
    from yaad.query.pipeline import classify_query
    if classify_query(question).complexity != "simple":
        return CHANNELS + OPTIONAL_CHANNELS
    if len(content_terms(question)) <= 3:
        return CHANNELS + OPTIONAL_CHANNELS
    return CHANNELS


def _diversify(rows: list, per_file_cap: int = 3) -> list:
    """Per-file cap preserving fused order: one verbose file cannot crowd
    other sources out of the top results."""
    kept, counts = [], {}
    overflow = []
    for row in rows:
        key = row["file_id"] if "file_id" in row.keys() else row["path"]
        if counts.get(key, 0) < per_file_cap:
            counts[key] = counts.get(key, 0) + 1
            kept.append(row)
        else:
            overflow.append(row)
    return kept + overflow


def expand_relations(db, evidence_ids: list[str], folder_id: int | None = None,
                     limit: int = 6) -> list[str]:
    """Relationship-aware expansion: conflict/supersession partners of
    already-ranked evidence join as candidates (gates still decide).
    When folder_id is given, partners are scoped to that folder."""
    if not evidence_ids:
        return []
    marks = ",".join("?" for _ in evidence_ids)
    with db.connect() as con:
        record_rows = con.execute(
            f"""SELECT DISTINCT record_id FROM memory_record_evidence
                WHERE evidence_id IN ({marks})""", evidence_ids).fetchall()
        record_ids = [r["record_id"] for r in record_rows]
        if not record_ids:
            return []
        marks2 = ",".join("?" for _ in record_ids)
        folder_clause, folder_args = "", []
        if folder_id is not None:
            folder_clause = """ AND e2.evidence_id IN
                (SELECT u.evidence_id FROM units u
                 JOIN files f ON f.id = u.file_id WHERE f.folder_id = ?)"""
            folder_args = [folder_id]
        partners = con.execute(
            f"""SELECT DISTINCT e2.evidence_id FROM memory_record_evidence e1
                JOIN memory_record_evidence e2 ON e2.record_id = e1.record_id
                WHERE e1.record_id IN ({marks2}){folder_clause}""",
            [*record_ids, *folder_args]).fetchall()
    seen = set(evidence_ids)
    extra = [r["evidence_id"] for r in partners if r["evidence_id"] not in seen]
    return sorted(extra)[:limit]


def hybrid_search(db, question: str, folder_id: int | None = None, file_kinds=None,
                  depth: int = CHANNEL_DEPTH, top_n: int = FUSED_TOP_N,
                  channels: tuple[str, ...] | None = None, local: bool = False,
                  diversify: bool = True, expand_relationships: bool = False) -> list:
    """Fused retrieval: RRF over the selected channels, README demotion
    as a deterministic tiebreak, top_n rows in fused order. Row shape is
    identical to search(); callers switch by replacing the call."""
    selected = CHANNELS if channels is None else tuple(channels)
    for channel in selected:
        if channel not in CHANNELS + OPTIONAL_CHANNELS:
            raise ValueError(f"unknown retrieval channel: {channel!r}")
    # Per-channel failure isolation: a broken optional channel (e.g.
    # semantic weights not installed) must never take down the working
    # keyword/memory channels. An explicitly requested single channel
    # still surfaces its failure (ablation callers rely on the raise).
    ranked: dict[str, list[str]] = {}
    for channel in selected:
        try:
            ranked[channel] = retrieve_channel(db, question, channel, depth,
                                               folder_id, file_kinds, local)
        except Exception:
            if len(selected) == 1:
                raise
            ranked[channel] = []
    scores = rrf_scores(ranked)
    if not scores:
        return []
    marks = ",".join("?" for _ in scores)
    with db.connect() as con:
        rows = {row["evidence_id"]: row for row in con.execute(
            """SELECT u.*,f.path,f.kind AS file_kind,f.partial,
                      fo.cloud_consent AS folder_cloud_consent
               FROM units u JOIN files f ON f.id=u.file_id
               JOIN folders fo ON fo.id=f.folder_id
               WHERE u.evidence_id IN (""" + marks + ")", tuple(scores))}
    # A unit deleted between channel ranking and fetch must not crash
    # fusion: rank only over rows that still resolve.
    scores = {eid: score for eid, score in scores.items() if eid in rows}
    ordered = sorted(scores, key=lambda eid: (-scores[eid],
                                               "readme" in rows[eid]["path"].casefold(),
                                               eid))
    fused = [rows[eid] for eid in ordered[:top_n]]
    if expand_relationships:
        extra_ids = [e for e in expand_relations(db, [r["evidence_id"] for r in fused], folder_id)
                     if e not in {r["evidence_id"] for r in fused}]
        if extra_ids:
            marks = ",".join("?" for _ in extra_ids)
            with db.connect() as con:
                extra_rows = con.execute(
                    """SELECT u.*,f.path,f.kind AS file_kind,f.partial,
                              fo.cloud_consent AS folder_cloud_consent
                       FROM units u JOIN files f ON f.id=u.file_id
                       JOIN folders fo ON fo.id=f.folder_id
                       WHERE u.evidence_id IN (""" + marks + ")", tuple(extra_ids)).fetchall()
            fused = list(fused) + list(extra_rows)
            fused = fused[:top_n + 6]
    return _diversify(fused) if diversify else fused
