"""Stage 2: conversational memory-status queries.

Narrow intent (status term + scope word) lists records by stored status with
citations. History is labeled as history; consent gates what is shown;
factual paths are untouched. Offline, no model calls.
"""
from test_memory_answers import Store, candidate

from yaad.memory.records import apply_supersession_links, mark_conflicts, mark_stale


def status_store(path):
    store = Store(path)
    store.add_file("a.txt", [("ev_prop", "Maybe we could book Room C for the afterparty"),
                             ("ev_c1", "The draft is due March 3rd"),
                             ("ev_gone", "Old claim text here"),
                             ("ev_u", "Workshop topics still need to be decided")])
    store.add_file("b.txt", [("ev_c2", "The draft is due April 4th"),
                             ("ev_old", "The workshop is in Room A"),
                             ("ev_new", "Correction: the workshop moved to Room B")])
    store.add_records("run-1", [
        candidate("Book Room C maybe", "book room", [("ev_prop", "book Room C")],
                  detail="Book Room C maybe", record_type="commitment", status="open",
                  statement_kind="proposal", date_value=None, date_raw=None),
        candidate("Draft March", "draft due", [("ev_c1", "draft is due March 3rd")]),
        candidate("Draft April", "draft due", [("ev_c2", "draft is due April 4th")],
                  date_value="2026-04-04", date_raw="April 4th"),
        candidate("Workshop Room A", "workshop room", [("ev_old", "workshop is in Room A")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Workshop Room B", "workshop room moved",
                  [("ev_new", "workshop moved to Room B")],
                  detail="Workshop Room B",
                  record_type="decision", status="decided", date_value=None, date_raw=None,
                  evidence=[{"evidence_id": "ev_new", "role": "supports",
                             "excerpt": "workshop moved to Room B"},
                            {"evidence_id": "ev_old", "role": "supersedes",
                             "excerpt": "workshop is in Room A"}]),
        candidate("Old claim", "old claim", [("ev_gone", "Old claim text")],
                  record_type="decision", status="decided", date_value=None, date_raw=None),
        candidate("Workshop topics undecided", "workshop topics",
                  [("ev_u", "Workshop topics still need to be decided")],
                  detail="Workshop topics undecided",
                  record_type="open_question", status="open", statement_kind="unresolved",
                  date_value=None, date_raw=None),
    ])
    with store.db.transaction() as con:
        con.execute("DELETE FROM units WHERE evidence_id='ev_gone'")
        assert apply_supersession_links(con, store.folder_id) == [4]
        assert mark_conflicts(con, store.folder_id) != []
        assert mark_stale(con, store.folder_id) != []
    return store


def test_proposal_listing(tmp_path):
    result = status_store(tmp_path / "s.sqlite3").ask("which memories are proposals?")
    assert result["status"] == "answered"
    assert result["memory"]["used"] is True
    assert "Proposal memories (1)" in result["answer"]
    (claim,) = result["memory"]["claims"]
    assert claim["proposal_only"] is True and claim["status"] == "open"
    assert claim["evidence_ids"] == ["ev_prop"]
    assert [card["id"] for card in result["evidence"]] == ["ev_prop"]


def test_conflict_listing_shows_both_sides(tmp_path):
    result = status_store(tmp_path / "s.sqlite3").ask("are any memories conflicted?")
    assert result["status"] == "answered"
    assert "Conflicted memories (2)" in result["answer"]
    assert len(result["memory"]["claims"]) == 2
    assert {card["id"] for card in result["evidence"]} == {"ev_c1", "ev_c2"}
    assert result["answer"].count("Conflicted") >= 2


def test_stale_and_superseded_listed_as_history(tmp_path):
    result = status_store(tmp_path / "s.sqlite3").ask("is anything stale or superseded?")
    assert result["status"] == "answered"
    assert "(history, not current facts)" in result["answer"]
    by_status = {claim["status"]: claim for claim in result["memory"]["claims"]}
    assert set(by_status) == {"stale", "superseded"}
    assert by_status["stale"]["note"] == "source removed"
    assert by_status["stale"]["evidence_ids"] == []
    assert by_status["superseded"]["evidence_ids"] == ["ev_old"]
    assert [card["id"] for card in result["evidence"]] == ["ev_old"]


def test_unresolved_listing(tmp_path):
    result = status_store(tmp_path / "s.sqlite3").ask("what is still undecided?")
    assert result["status"] == "answered"
    assert "Unresolved decisions (1)" in result["answer"]
    (claim,) = result["memory"]["claims"]
    assert claim["unresolved"] is True
    assert "Undecided" in result["answer"]


def test_empty_listing_is_explicit(tmp_path):
    store = Store(tmp_path / "empty.sqlite3")
    result = store.ask("which memories are proposals?")
    assert result["status"] == "answered"
    assert result["answer"] == "No proposal memories in the selected scope."
    assert result["evidence"] == [] and result["memory"]["used"] is False


def test_listing_respects_consent(tmp_path):
    store = status_store(tmp_path / "s.sqlite3")
    with store.db.transaction() as con:
        con.execute("UPDATE folders SET cloud_consent=0 WHERE id=?", (store.folder_id,))
    revoked = store.ask("which memories are proposals?")
    assert revoked["answer"] == "No proposal memories in the selected scope."
    assert revoked["memory"]["used"] is False

    store = status_store(tmp_path / "s2.sqlite3")
    with store.db.transaction() as con:
        con.execute("UPDATE files SET cloud_consent=0 WHERE path='/src/a.txt'")
    partial = store.ask("which memories are proposals?")
    assert partial["answer"] == "No proposal memories in the selected scope."
    history = store.ask("is anything stale?")
    assert history["status"] == "answered"
    assert "source removed" in history["answer"]


def test_status_looking_question_falls_through_without_memory_scope(tmp_path):
    store = Store(tmp_path / "fall.sqlite3")
    store.add_file("notes.txt", [("ev_a", "The decision about offline mode remains undecided.")])
    result = store.ask("Which project decision is still open?")
    assert result["status"] == "cloud_unavailable"
    assert result["memory"]["used"] is False
    assert "offline mode" in result["evidence"][0]["quote"].lower()


def test_factual_question_does_not_trigger_status_mode(tmp_path):
    store = status_store(tmp_path / "s.sqlite3")
    result = store.ask("was the proposal accepted?")
    assert result["status"] == "not_found"
    assert result["memory"]["used"] is False


def test_document_reference_does_not_trigger_status_listing(tmp_path):
    """RAG X2/X4 hijack: 'the proposal' names a source document, so these
    factual questions must not divert into a proposal-memory listing even
    though a proposal record exists in the store."""
    from yaad.query.memory_answer import detect_status_intent

    # Unit level: the exact RAG wordings (incl. the audio trigger word).
    assert detect_status_intent(
        "What registration fee do the proposal and the recording agree on?") == []
    assert detect_status_intent(
        "What is the poster deadline according to the notes and the proposal?") == []
    # Genuine status phrasings still fire.
    assert detect_status_intent("which memories are proposals?") == ["proposal"]
    assert detect_status_intent("are any memories conflicted?") == ["conflicted"]
    assert detect_status_intent("list the stale records") == ["stale"]
    # End to end: non-audio wording reaches the listing path, so a firing
    # intent would surface here (the audio wording short-circuits earlier
    # on stores without audio files).
    store = status_store(tmp_path / "s.sqlite3")
    for question in ("What registration fee do the proposal and the notes agree on?",
                     "What is the poster deadline according to the notes and the proposal?",
                     "what are the proposals?"):
        result = store.ask(question)
        assert "Proposal memories" not in result["answer"]
        assert result["memory"]["used"] is False


def test_explicit_memory_scope_overrides_document_reference(tmp_path):
    """A determiner-led status term still lists when the question is
    explicitly scoped to memory."""
    store = status_store(tmp_path / "s.sqlite3")
    result = store.ask("list the stale records")
    assert result["status"] == "answered"
    assert "(history, not current facts)" in result["answer"]
