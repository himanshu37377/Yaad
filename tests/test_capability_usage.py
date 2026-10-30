import json

from yaad.llm.capability_usage import CapabilityUsageLog


def test_usage_log_accepts_calls_past_previous_limit(tmp_path):
    path = tmp_path / "capability_usage.json"
    log = CapabilityUsageLog(path)
    for index in range(25):
        log.record({"name": f"synthetic-{index}", "status": "PASS", "cost_usd": 0.01})

    saved = json.loads(path.read_text())
    assert len(saved["calls"]) == 25
    assert saved["calls"][-1]["name"] == "synthetic-24"


def test_unreadable_usage_log_does_not_block_testing(tmp_path, capsys):
    path = tmp_path / "capability_usage.json"
    path.write_text("not-json")
    log = CapabilityUsageLog(path)
    log.record({"name": "synthetic", "status": "PASS"})

    assert "continuing without log write" in capsys.readouterr().err
