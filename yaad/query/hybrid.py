"""Deterministic hybrid retrieval (Phase 1): FTS5 keyword and Tier A
memory channels fused with Reciprocal Rank Fusion.

The keyword channel is the preserved search() core (plural-folded query
terms, exact-phrase ranking bonus, per-channel depth). The memory
channel ranks supporting units of Tier A matched records only --
contextual records never contribute candidates. Fused candidates flow
through every existing downstream gate unchanged (consent, taint,
validation, relevance tiers); fusion changes which candidates the gates
see, never what they accept.

No embeddings, no model calls, no schema changes. The "semantic"
channel name is reserved for Phase 2 and fails fast until then.
"""

from yaad.query.memory_answer import (
    ANSWERABLE,
    _is_undecided_question,
    _relevance_for,
    content_terms,
    match_memory_records,
)
from yaad.query.retrieve import STOPWORDS, fold_term, phrase_hits, question_phrases, search

CHANNELS = ("keyword", "memory")
RRF_K = 60
CHANNEL_DEPTH = 50
FUSED_TOP_N = 30


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
    """Original question terms plus their singular folds. Folding only
    ever adds the question's own morphology alongside the originals, so
    the folded query retrieves a superset of the plain one."""
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
    return " ".join(expanded)


def _keyword_channel(db, question: str, depth: int, folder_id, file_kinds) -> list[str]:
    rows = search(db, _folded_query(question), folder_id, depth, file_kinds)
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


def retrieve_channel(db, question: str, channel: str, depth: int = CHANNEL_DEPTH,
                     folder_id: int | None = None, file_kinds=None,
                     local: bool = False) -> list[str]:
    """Ranked evidence_ids from one channel (per-channel ablation entry
    point). Unknown channels fail fast; file_kinds applies to keyword
    retrieval only."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown retrieval channel: {channel!r}")
    if channel == "keyword":
        return _keyword_channel(db, question, depth, folder_id, file_kinds)
    return _memory_channel(db, question, depth, folder_id, local)


def hybrid_search(db, question: str, folder_id: int | None = None, file_kinds=None,
                  depth: int = CHANNEL_DEPTH, top_n: int = FUSED_TOP_N,
                  channels: tuple[str, ...] | None = None, local: bool = False) -> list:
    """Fused retrieval: RRF over the selected channels, README demotion
    as a deterministic tiebreak, top_n rows in fused order. Row shape is
    identical to search(); callers switch by replacing the call."""
    selected = CHANNELS if channels is None else tuple(channels)
    for channel in selected:
        if channel not in CHANNELS:
            raise ValueError(f"unknown retrieval channel: {channel!r}")
    ranked = {channel: retrieve_channel(db, question, channel, depth, folder_id,
                                        file_kinds, local)
              for channel in selected}
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
    return [rows[eid] for eid in ordered[:top_n]]
