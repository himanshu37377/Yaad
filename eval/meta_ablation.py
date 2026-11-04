"""Meta-AI differentiation ablation (offline, mocked models).

Three arms on identical frozen cases:
  A deterministic-only (no model)
  B Meta-assisted (mocked Muse Spark classification/chronology)
  C Meta-assisted + independent entailment verification (mocked)

Mocks prove ORCHESTRATION (gates, schemas, vetoes), never model
quality. Live-model comparison requires approved spend; see the
live-validation proposal in the final report.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from yaad.config import Settings
from yaad.db import Database
from yaad.query.pipeline import run_pipeline
from yaad.query.verify import verify_claims

CASES = [
    {"q": "When is the CS201 midterm?",
     "units": [("a1", "CS201 midterm is on November 18, 2026 in Room LH-2."),
               ("a2", "Revised: CS201 midterm moved to November 20, 2026.")]},
    {"q": "What is the latest confirmed venue for Autumn Fest?",
     "units": [("v1", "Autumn Fest 2026 will be held at Riverside Hall."),
               ("v2", "Revised venue is Grand Arena for Autumn Fest.")]},
]


class MockSpark:
    """Deterministic stand-in with the MetaClient.reason_json shape."""

    def __init__(self, mode):
        self.mode = mode
        self.calls = 0

    def reason_json(self, messages, schema, prompt_id):
        self.calls += 1
        if "classification" in prompt_id:
            return {"labels": [
                {"evidence_id": "a1", "label": "supports", "reason": "mock"},
                {"evidence_id": "a2", "label": "supersedes", "reason": "mock"},
                {"evidence_id": "v1", "label": "supports", "reason": "mock"},
                {"evidence_id": "v2", "label": "supersedes", "reason": "mock"}]}
        if "chronology" in prompt_id:
            return {"events": [], "current_evidence_id": None, "conflict": True}
        if "verification" in prompt_id or "entailment" in prompt_id:
            if self.mode == "C":
                return {"verdicts": [{"index": 0, "verdict": "supported", "reason": "mock entailment"}],
                        "items": [{"index": 0, "verdict": "supported", "reason": "mock entailment"}]}
            raise RuntimeError("arm B skips entailment")
        raise ValueError(prompt_id)


def build_db(units, path):
    db = Database(path)
    with db.transaction() as con:
        con.execute("INSERT INTO folders(id,path,cloud_consent) VALUES(1,'/m',0)")
        con.execute("""INSERT INTO files(id,folder_id,path,sha256,kind,size,mtime,status)
                       VALUES(1,1,'/m/a.txt','h','text',10,1.0,'indexed')""")
        for eid, text in units:
            con.execute("INSERT INTO units(file_id,evidence_id,kind,locator,text) VALUES(1,?,'text','{}',?)",
                        (eid, text))
    return db


def main():
    settings = Settings()
    settings.model_provider = "local"  # mocked local path: no egress, no spend
    report = {"arms": {}, "disclaimer": "mocked models: orchestration only, not model quality"}
    for arm, use_model, mode in (("A_deterministic", False, "A"),
                                 ("B_meta_assisted", True, "B"),
                                 ("C_meta_plus_verify", True, "C")):
        rows = []
        for case in CASES:
            with tempfile.TemporaryDirectory() as tmp:
                db = build_db(case["units"], Path(tmp) / "m.sqlite3")
                client = MockSpark(mode) if use_model else None
                res = run_pipeline(db, case["q"], 1, settings,
                                   client=client, use_model=use_model)
                by_id = {eid: text for eid, text in case["units"]}
                vrep = verify_claims([(c.text, c.evidence_ids) for c in res.candidate.claims],
                                     by_id, settings=settings, db=db,
                                     client=client if mode == "C" else None,
                                     use_model=(mode == "C"))
                rows.append({"q": case["q"], "status": res.status,
                             "model_calls": res.model_calls,
                             "verdicts": [c.verdict for c in vrep.claims]})
        report["arms"][arm] = rows
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
