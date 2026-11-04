import re

STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "the", "to", "was", "were", "what", "when", "where", "which", "who", "why", "with", "do", "does", "did", "show", "shown", "me", "my", "i", "we", "you", "your", "please", "tell", "about", "there", "this", "that", "these", "those", "whats", "s"}

# Words the plural folder must never touch: folding "news" to "new" would
# retrieve unrelated "new X" units, which is exactly the noise folding
# must not add. "-is"/"-us" endings are not plurals either ("analysis",
# "this", "status") and must never be stemmed.
_FOLD_EXCEPTIONS = frozenset({"news", "this"})


def fold_term(term: str) -> str:
    """Conservative inflection folding for query terms: regular plurals
    (drafts, fees, budgets, stories, boxes, churches) reduce to their
    singular; short words, ss/us endings, and known oddballs pass
    through untouched. Folding only ever *adds* the singular alongside
    the original term, so worst case is today's behavior. This is
    morphology of the question's own terms, not topic expansion:
    synonyms and related terms are still never introduced."""
    word = term.casefold()
    if len(word) <= 3 or word in _FOLD_EXCEPTIONS:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 4 and word[-3] in "sxz":
        return word[:-2]
    if word.endswith(("ches", "shes")) and len(word) > 5:
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


# Minimal verb/noun fold map: two tight equivalence classes,
# (quote/quoted, caterer/catering) plus the close/closes verb pair
# (deadline/registration "close" vs "closes" in evidence). Applied at
# query time by the hybrid keyword channel and the pipeline
# classifier's overlap counting only; search() stays frozen. This is
# not general synonym expansion: no other term gains match forms.
WORD_FOLDS = {
    "quote": ("quoted",),
    "quoted": ("quote",),
    "caterer": ("catering",),
    "catering": ("caterer",),
    "close": ("closes",),
    "closes": ("close",),
}


def word_folds(term: str) -> list[str]:
    """Extra match forms for one query term within the WORD_FOLDS class.

    Looks up both the term and its singular fold so inflected forms
    ("quotes", "caterers") reach the same class. Returns [] outside the
    class."""
    word = term.casefold()
    variants: list[str] = []
    for key in (word, fold_term(word)):
        for variant in WORD_FOLDS.get(key, ()):
            if variant != word and variant not in variants:
                variants.append(variant)
    return variants


_SUBQUERY_SPLIT = re.compile(r"\band\b|\bwho\b", re.IGNORECASE)


def split_subqueries(question: str) -> list[str]:
    """Split a multi-part question on and/who clause boundaries.

    Single-part questions come back as one clause, so their retrieval
    path is unchanged. Empty clauses and clauses with no content terms
    are dropped: they would retrieve nothing."""
    clauses = []
    for part in _SUBQUERY_SPLIT.split(question or ""):
        text = part.strip()
        if not text:
            continue
        terms = [term for term in re.findall(r"[\w-]+", text.casefold(), flags=re.UNICODE)
                 if len(term) > 1 and term not in STOPWORDS]
        if terms:
            clauses.append(text)
    return clauses


def question_phrases(question: str) -> list[str]:
    """Maximal runs of content words (length >= 2) in question order.
    Runs break at stopwords, so only genuinely adjacent wording counts
    as a phrase."""
    phrases, run = [], []
    for token in re.findall(r"[\w-]+", (question or "").casefold(), flags=re.UNICODE):
        if len(token) > 1 and token not in STOPWORDS:
            run.append(token)
        else:
            if len(run) >= 2:
                phrases.append(" ".join(run))
            run = []
    if len(run) >= 2:
        phrases.append(" ".join(run))
    return phrases


def _content_sequence(text: str) -> list[str]:
    return [token for token in re.findall(r"[\w-]+", (text or "").casefold(), flags=re.UNICODE)
            if len(token) > 1 and token not in STOPWORDS]


def phrase_hits(unit_text: str, phrases: list[str]) -> int:
    """How many question phrases appear as contiguous content-word runs
    in the unit text. Ranking bonus only: never filters, never expands."""
    words = _content_sequence(unit_text)
    hits = 0
    for phrase in phrases:
        span = phrase.split()
        if any(words[pos:pos + len(span)] == span for pos in range(len(words) - len(span) + 1)):
            hits += 1
    return hits


def search(db, query: str, folder_id: int | None = None, limit: int = 10, file_kinds: list[str] | None = None):
    terms = [term for term in re.findall(r"[\w-]+", query.casefold(), flags=re.UNICODE)
             if len(term) > 1 and term not in STOPWORDS]
    # No topic expansions: only the question's own terms may retrieve, so an
    # unrelated document can never outrank a genuine match.
    terms = list(dict.fromkeys(terms))
    if not terms:
        return []
    match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms[:20])
    sql = """SELECT u.*,f.path,f.kind AS file_kind,f.partial,fo.cloud_consent AS folder_cloud_consent FROM units_fts
             JOIN units u ON u.id=units_fts.rowid JOIN files f ON f.id=u.file_id
             JOIN folders fo ON fo.id=f.folder_id
             WHERE units_fts MATCH ?"""
    args = [match]
    if folder_id is not None:
        sql += " AND f.folder_id=?"
        args.append(folder_id)
    if file_kinds:
        sql += " AND f.kind IN (" + ",".join("?" for _ in file_kinds) + ")"
        args.extend(file_kinds)
    # Test guides and READMEs can help, but should not displace direct source files.
    sql += " ORDER BY CASE WHEN lower(f.path) LIKE '%readme%' THEN 1 ELSE 0 END, bm25(units_fts) LIMIT ?"
    args.append(limit)
    with db.connect() as con:
        return con.execute(sql, args).fetchall()
