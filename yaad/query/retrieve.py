import re

STOPWORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "the", "to", "was", "were", "what", "when", "where", "which", "who", "why", "with", "do", "does", "did", "show", "shown", "me", "my", "i", "we", "you", "your", "please", "tell", "about", "there", "this", "that", "these", "those", "whats", "s"}

# Words the plural folder must never touch: folding "news" to "new" would
# retrieve unrelated "new X" units, which is exactly the noise folding
# must not add.
_FOLD_EXCEPTIONS = frozenset({"news"})


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
    if word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


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
