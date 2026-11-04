"""Subject inheritance for supersession (Phase 1 regression suite).

Failure shape (recorded live in eval/real_eval_v3, chains folder, 5/5
runs): the extraction model proposed the venue chain's first link, but
deterministic validation rejected it -- "ambiguous: same subject not
established: only one side names fest, spring" -- because the revised
statement ("Revised venue is Grand Arena") never repeats the event
name the original statement carries ("Spring Fest venue is Riverside
Hall"). The original venue record therefore never stepped down.

Fix contract: a supersession claimant that corroborates no
distinguishing subject of its own may inherit the subject of the
statement its link cites, but only when the pair demonstrably competes
on one attribute and the folder holds no competing subject for that
rivalry. Conflicts and duplicates never inherit; a claimant naming
its own different subject never inherits; the deterministic validator
remains the final authority in every direction.

Deterministic fixtures on disposable databases; no model calls.
"""
from test_memory_answers import Store, candidate

from yaad.memory.records import apply_supersession_links
from yaad.memory.relationships import validate_pair

INITIAL_UNIT = "The committee confirmed the Spring Fest venue is Riverside Hall"
REVISED_UNIT = ("Spring Fest update: the venue Grand Arena is confirmed. "
                "The registration deadline is April 2, 2027.")


def venue_candidate(title, detail, excerpts, **over):
    base = {"record_type": "decision", "status": "decided",
            "date_value": None, "date_raw": None}
    base.update(over)
    return candidate(title, base.pop("key_phrase", "venue"), excerpts,
                     detail=detail, **base)


def chains_store(path):
    """Run-1 shape: the original venue statement names the event; the
    revision that replaces it does not repeat the event name."""
    store = Store(path)
    store.add_file("initial.txt", [("ev_init", INITIAL_UNIT)])
    store.add_file("update.txt", [("ev_rev", REVISED_UNIT)])
    return store


def statuses(db):
    with db.connect() as con:
        return {row["title"]: row["status"] for row in
                con.execute("SELECT title,status FROM memory_records")}


def test_venue_revision_without_event_name_supersedes(tmp_path):
    store = chains_store(tmp_path / "inherit.sqlite3")
    store.add_records("run-1", [
        venue_candidate("Venue Riverside Hall confirmed",
                        "Spring Fest venue is Riverside Hall",
                        [("ev_init", "Spring Fest venue is Riverside Hall")],
                        key_phrase="riverside venue"),
    ])
    store.add_records("run-2", [
        venue_candidate("Venue Grand Arena confirmed",
                        "Revised venue is Grand Arena and confirmed",
                        [("ev_rev", "venue Grand Arena is confirmed")],
                        key_phrase="grand arena venue",
                        evidence=[{"evidence_id": "ev_rev", "role": "supports",
                                   "excerpt": "venue Grand Arena is confirmed"},
                                  {"evidence_id": "ev_init", "role": "supersedes",
                                   "excerpt": "Spring Fest venue is Riverside Hall"}]),
    ])
    with store.db.transaction() as con:
        rejected: list = []
        stepped = apply_supersession_links(con, store.folder_id, rejected=rejected)
        assert len(stepped) == 1 and rejected == []
        transitions = [row["transition"] for row in con.execute(
            "SELECT transition FROM memory_transitions")]
    assert statuses(store.db) == {"Venue Riverside Hall confirmed": "superseded",
                                  "Venue Grand Arena confirmed": "decided"}
    assert "superseded" in transitions


# Recorded model statements from eval/real_eval_v3/results/
# records_chains_run{1..5}.json (title/detail verbatim). The live
# verdict for this pair was ambiguous in every run; the cited excerpts
# below are the fixture sentences the records were extracted from.
RECORDED_VENUE_PAIRS = [
    ("Venue Riverside Hall confirmed", "Spring Fest venue is Riverside Hall",
     "Venue Grand Arena confirmed", "Revised venue is Grand Arena and confirmed"),
    ("Initial venue Riverside Hall", "Spring Fest venue is Riverside Hall as initially confirmed",
     "Revised venue Grand Arena", "Revised venue is Grand Arena and confirmed"),
    ("Venue Riverside Hall", "Spring Fest venue is Riverside Hall",
     "Venue Grand Arena", "Revised venue is Grand Arena"),
    ("Spring Fest venue Riverside Hall", "Committee confirmed Spring Fest venue is Riverside Hall.",
     "Revised venue Grand Arena", "Revised new venue Grand Arena confirmed."),
    ("Initial venue Riverside Hall", "Spring Fest venue is Riverside Hall as initially confirmed",
     "Revised venue Grand Arena", "Revised venue is Grand Arena"),
]


def _record(record_id, title, detail):
    return {"id": record_id, "record_type": "decision", "title": title,
            "detail": detail, "date_value": None, "date_raw": None,
            "assignee": "", "status": "decided", "statement_kind": "explicit",
            "record_key": "", "created_at": ""}


def _supports(excerpt):
    return [{"evidence_id": "ev", "excerpt": excerpt, "unit_text": None,
             "file": "fixture.txt", "unit_present": True}]


def test_recorded_chains_venue_pairs_replay():
    """All five recorded runs: ambiguous by default, supported once the
    folder proof is supplied. Default callers see unchanged behavior."""
    for old_title, old_detail, new_title, new_detail in RECORDED_VENUE_PAIRS:
        claimant = _record(2, new_title, new_detail)
        target = _record(1, old_title, old_detail)
        claimant["created_at"], target["created_at"] = "2027-01-02", "2027-01-01"
        plain = validate_pair("supersession", claimant, target,
                              _supports("venue Grand Arena is confirmed"),
                              _supports("Spring Fest venue is Riverside Hall"))
        assert plain["verdict"] == "ambiguous", (old_title, new_title)
        inherited = validate_pair("supersession", claimant, target,
                                  _supports("venue Grand Arena is confirmed"),
                                  _supports("Spring Fest venue is Riverside Hall"),
                                  subject_uncontested=True)
        assert inherited["verdict"] == "supported", (old_title, new_title)
        assert inherited["details"]["subject_inherited"] is True


def test_date_revision_without_event_name_supersedes(tmp_path):
    store = Store(tmp_path / "date.sqlite3")
    store.add_file("initial.txt", [("ev_d1", "Spring Fest registration deadline is March 1, 2027")])
    store.add_file("update.txt", [("ev_d2", "Registration deadline moved to April 2, 2027")])
    store.add_records("run-1", [
        candidate("Spring Fest registration deadline", "registration deadline",
                  [("ev_d1", "Spring Fest registration deadline is March 1, 2027")],
                  date_value="2027-03-01", date_raw="March 1, 2027"),
    ])
    store.add_records("run-2", [
        candidate("Registration deadline April 2", "deadline moved",
                  [("ev_d2", "Registration deadline moved to April 2, 2027")],
                  date_value="2027-04-02", date_raw="April 2, 2027",
                  evidence=[{"evidence_id": "ev_d2", "role": "supports",
                             "excerpt": "Registration deadline moved to April 2, 2027"},
                            {"evidence_id": "ev_d1", "role": "supersedes",
                             "excerpt": "Spring Fest registration deadline is March 1, 2027"}]),
    ])
    with store.db.transaction() as con:
        stepped = apply_supersession_links(con, store.folder_id, rejected=[])
        assert len(stepped) == 1
    assert statuses(store.db)["Spring Fest registration deadline"] == "superseded"


def test_competing_event_blocks_inheritance(tmp_path):
    """Two events, identical venue vocabulary: the elliptical claimant
    could belong to either chain, so nothing steps down."""
    store = chains_store(tmp_path / "competing.sqlite3")
    store.add_file("harbor.txt", [("ev_hb", "Harbor Fest venue is Riverside Hall")])
    store.add_records("run-1", [
        venue_candidate("Venue Riverside Hall confirmed",
                        "Spring Fest venue is Riverside Hall",
                        [("ev_init", "Spring Fest venue is Riverside Hall")],
                        key_phrase="spring venue"),
        venue_candidate("Harbor venue Riverside Hall",
                        "Harbor Fest venue is Riverside Hall",
                        [("ev_hb", "Harbor Fest venue is Riverside Hall")],
                        key_phrase="harbor venue"),
    ])
    store.add_records("run-2", [
        venue_candidate("Venue Grand Arena confirmed",
                        "Revised venue is Grand Arena and confirmed",
                        [("ev_rev", "venue Grand Arena is confirmed")],
                        key_phrase="grand arena venue",
                        evidence=[{"evidence_id": "ev_rev", "role": "supports",
                                   "excerpt": "venue Grand Arena is confirmed"},
                                  {"evidence_id": "ev_init", "role": "supersedes",
                                   "excerpt": "Spring Fest venue is Riverside Hall"}]),
    ])
    with store.db.transaction() as con:
        rejected: list = []
        stepped = apply_supersession_links(con, store.folder_id, rejected=rejected)
        assert stepped == []
        assert any("ambiguous" in item["reason"] for item in rejected)
    assert set(statuses(store.db).values()) == {"decided"}


def test_claimant_naming_another_event_never_inherits(tmp_path):
    """A claimant that names its own different event is contradicted,
    not elliptical -- even when it (wrongly) cites the other event's
    evidence as the statement it replaces."""
    store = chains_store(tmp_path / "named.sqlite3")
    store.add_file("harbor.txt", [("ev_hb2", "Harbor Fest venue moved to Grand Arena")])
    store.add_records("run-1", [
        venue_candidate("Venue Riverside Hall confirmed",
                        "Spring Fest venue is Riverside Hall",
                        [("ev_init", "Spring Fest venue is Riverside Hall")],
                        key_phrase="riverside venue"),
    ])
    store.add_records("run-2", [
        venue_candidate("Harbor Fest Grand Arena",
                        "Harbor Fest venue moved to Grand Arena",
                        [("ev_hb2", "Harbor Fest venue moved to Grand Arena")],
                        key_phrase="grand arena venue",
                        evidence=[{"evidence_id": "ev_hb2", "role": "supports",
                                   "excerpt": "Harbor Fest venue moved to Grand Arena"},
                                  {"evidence_id": "ev_init", "role": "supersedes",
                                   "excerpt": "Spring Fest venue is Riverside Hall"}]),
    ])
    with store.db.transaction() as con:
        rejected: list = []
        assert apply_supersession_links(con, store.folder_id, rejected=rejected) == []
        assert any("contradicted" in item["reason"] for item in rejected)
    assert statuses(store.db)["Venue Riverside Hall confirmed"] == "decided"


def test_identical_venue_names_supersede_only_within_own_event(tmp_path):
    """Both events sit in Riverside Hall; Harbor's named revision must
    step down Harbor's record only, never Spring's."""
    store = chains_store(tmp_path / "own.sqlite3")
    store.add_file("harbor.txt", [("ev_hb", "Harbor Fest venue is Riverside Hall")])
    store.add_file("harbor2.txt", [("ev_hb2", "Harbor Fest venue moved to Grand Arena")])
    store.add_records("run-1", [
        venue_candidate("Venue Riverside Hall confirmed",
                        "Spring Fest venue is Riverside Hall",
                        [("ev_init", "Spring Fest venue is Riverside Hall")],
                        key_phrase="spring venue"),
        venue_candidate("Harbor venue Riverside Hall",
                        "Harbor Fest venue is Riverside Hall",
                        [("ev_hb", "Harbor Fest venue is Riverside Hall")],
                        key_phrase="harbor venue"),
    ])
    store.add_records("run-2", [
        venue_candidate("Harbor Fest Grand Arena",
                        "Harbor Fest venue moved to Grand Arena",
                        [("ev_hb2", "Harbor Fest venue moved to Grand Arena")],
                        key_phrase="grand arena venue",
                        evidence=[{"evidence_id": "ev_hb2", "role": "supports",
                                   "excerpt": "Harbor Fest venue moved to Grand Arena"},
                                  {"evidence_id": "ev_hb", "role": "supersedes",
                                   "excerpt": "Harbor Fest venue is Riverside Hall"}]),
    ])
    with store.db.transaction() as con:
        stepped = apply_supersession_links(con, store.folder_id, rejected=[])
        assert len(stepped) == 1
    result = statuses(store.db)
    assert result["Harbor venue Riverside Hall"] == "superseded"
    assert result["Venue Riverside Hall confirmed"] == "decided"


def test_rivalry_free_claimant_cannot_inherit(tmp_path):
    """An elliptical claimant that competes on no attribute at all has
    nothing to inherit over: still ambiguous, even with folder proof."""
    claimant = _record(2, "Revised arrangements", "Revised arrangements are confirmed")
    target = _record(1, "Venue Riverside Hall confirmed",
                     "Spring Fest venue is Riverside Hall")
    claimant["created_at"], target["created_at"] = "2027-01-02", "2027-01-01"
    verdict = validate_pair("supersession", claimant, target,
                            _supports("Revised arrangements are confirmed"),
                            _supports("Spring Fest venue is Riverside Hall"),
                            subject_uncontested=True)
    assert verdict["verdict"] == "ambiguous"
