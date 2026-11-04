"""Local semantic similarity for retrieval recall (opt-in, default off).

A recall assist only: it ranks the *same candidate universe* as keyword
search (same folder and file-kind filters, no consent differences —
answers already cite locally indexed text regardless of channel), and
its output flows through every downstream gate unchanged (tiers, slot
gating, taint sets, citation checks). It can widen what the gates see;
it can never decide what they accept.

Hardening contract:

* A configurable similarity floor (`Settings.semantic_min_similarity`,
  env `YAAD_SEMANTIC_MIN_SIMILARITY`) drops the noise tail: below-floor
  units never enter fusion, so semantic votes only ever come from
  genuinely similar text.
* Unit vectors are cached persistently in `unit_embeddings`, keyed by
  evidence_id and invalidated by (model, text hash): a unit is
  embedded once per content version, never once per query.
* The candidate universe is never truncated: every matching unit is
  considered, embedded in batches; corpora larger than one batch cost
  time, not silent recall loss. `last_stats` reports the unit count,
  fresh embeddings, cache hits, and wall time of the latest call so
  latency stays measurable.

Runs fully on-device via FastEmbed/ONNX (`BAAI/bge-small-en-v1.5`,
384 dims, quantized ONNX weights, no API key, no egress). Install with
``pip install yaad-memory[semantic]``; weights cache honors
``FASTEMBED_CACHE_PATH``.
"""
import hashlib
import time

MODEL_NAME = "BAAI/bge-small-en-v1.5"
_EMBED_BATCH = 256
_LOOKUP_CHUNK = 500

_model = None
last_stats: dict = {}


def _load():
    global _model
    if _model is None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "semantic retrieval needs the 'semantic' extra: "
                "pip install yaad-memory[semantic]") from exc
        _model = TextEmbedding(MODEL_NAME)
    return _model


def embed_texts(texts: list[str]):
    """Embed a batch; returns an (n, dim) float32 numpy array."""
    import numpy as np
    vectors = list(_load().embed(texts))
    return np.asarray(vectors, dtype=np.float32)


def _text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _vectors_for(db, rows):
    """(matrix, freshly_embedded, cache_hits) aligned with rows.

    Cache entries count only when the model and the content hash both
    match, so edited text and a model swap invalidate themselves.
    Missing units are embedded in batches and persisted; nothing is
    written when every row already hits."""
    import numpy as np
    hashes = {row["evidence_id"]: _text_hash(row["text"]) for row in rows}
    cached: dict[str, object] = {}
    evidence_ids = [row["evidence_id"] for row in rows]
    with db.connect() as con:
        for start in range(0, len(evidence_ids), _LOOKUP_CHUNK):
            chunk = evidence_ids[start:start + _LOOKUP_CHUNK]
            marks = ",".join("?" for _ in chunk)
            for row in con.execute(
                    f"""SELECT evidence_id,model,text_hash,dim,vector FROM unit_embeddings
                        WHERE evidence_id IN ({marks})""", chunk):
                # Integrity: a truncated/corrupt blob or a weight revision
                # under the same model name must be a cache miss, never a
                # misaligned matrix row.
                vector = row["vector"] or b""
                if (row["model"] == MODEL_NAME
                        and row["text_hash"] == hashes[row["evidence_id"]]
                        and len(vector) == int(row["dim"] or 0) * 4
                        and len(vector) > 0):
                    cached[row["evidence_id"]] = np.frombuffer(
                        vector, dtype=np.float32)
    missing = [row for row in rows if row["evidence_id"] not in cached]
    embedded = 0
    for start in range(0, len(missing), _EMBED_BATCH):
        chunk = missing[start:start + _EMBED_BATCH]
        matrix = embed_texts([row["text"] for row in chunk])
        with db.transaction() as con:
            con.executemany(
                """INSERT OR REPLACE INTO unit_embeddings(evidence_id,model,text_hash,dim,vector)
                   VALUES(?,?,?,?,?)""",
                [(row["evidence_id"], MODEL_NAME, hashes[row["evidence_id"]],
                  int(matrix[index].shape[0]), matrix[index].tobytes())
                 for index, row in enumerate(chunk)])
        for index, row in enumerate(chunk):
            cached[row["evidence_id"]] = matrix[index]
        embedded += len(chunk)
    matrix = np.vstack([cached[row["evidence_id"]] for row in rows]) \
        if rows else np.zeros((0, 0), dtype=np.float32)
    return matrix, embedded, len(rows) - embedded


def _semantic_scored(db, question: str, folder_id: int | None,
                     file_kinds=None, min_similarity: float | None = None
                     ) -> list[tuple[float, str]]:
    """(cosine, evidence_id) pairs above the floor, best first."""
    import numpy as np
    if not (question or "").strip():
        return []
    if min_similarity is None:
        from yaad.config import get_settings
        min_similarity = get_settings().semantic_min_similarity
    sql = """SELECT u.evidence_id,u.text FROM units u JOIN files f ON f.id=u.file_id
             WHERE 1=1"""
    args: list = []
    if folder_id is not None:
        sql += " AND f.folder_id=?"
        args.append(folder_id)
    if file_kinds:
        sql += " AND f.kind IN (" + ",".join("?" for _ in file_kinds) + ")"
        args.extend(file_kinds)
    sql += " ORDER BY u.id"
    with db.connect() as con:
        rows = con.execute(sql, args).fetchall()
    if not rows:
        return []
    started = time.perf_counter()
    query_vec = embed_texts([question])[0]
    matrix, embedded, cache_hits = _vectors_for(db, rows)
    query_norm = float(np.linalg.norm(query_vec))
    row_norms = np.linalg.norm(matrix, axis=1)
    denom = query_norm * row_norms
    scores = np.where(denom > 0, matrix @ query_vec / np.maximum(denom, 1e-12), -1.0)
    last_stats.update({
        "model": MODEL_NAME, "units": len(rows), "embedded": embedded,
        "cache_hits": cache_hits, "min_similarity": min_similarity,
        "ms": round((time.perf_counter() - started) * 1000, 1),
    })
    return sorted(
        ((float(score), row["evidence_id"])
         for score, row in zip(scores.tolist(), rows) if score >= min_similarity),
        key=lambda pair: (-pair[0], pair[1]))


def semantic_rank(db, question: str, depth: int, folder_id: int | None,
                  file_kinds=None, min_similarity: float | None = None) -> list[str]:
    """Ranked evidence_ids by cosine similarity to the question,
    floored at `min_similarity` (Settings default when not given).

    Empty question or no candidates yields []. Rank-stable: ties break
    by evidence_id so repeated runs agree byte-for-byte."""
    return [evidence_id for _, evidence_id in
            _semantic_scored(db, question, folder_id, file_kinds, min_similarity)[:depth]]


def semantic_scores(db, question: str, folder_id: int | None,
                    file_kinds=None, min_similarity: float | None = None
                    ) -> dict[str, float]:
    """{evidence_id: cosine} for every above-floor candidate -- the
    trace/introspection companion to `semantic_rank` (evaluation
    transcripts record these; serving never reads them)."""
    return {evidence_id: score for score, evidence_id in
            _semantic_scored(db, question, folder_id, file_kinds, min_similarity)}


def reset_model_cache() -> None:
    """Test hook: drop the singleton so each test starts unloaded."""
    global _model
    _model = None
