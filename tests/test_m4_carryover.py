"""Feedback input boundary and opt-in environment fault controls."""

import json

import pytest

from autoqa.agent.carryover import extract, prompt_text
from autoqa.doom.faults import EnvironmentFaultInjector
from autoqa.io import write_json


def test_feedback_ignores_privileged_and_reviewer_artifacts(tmp_path):
    write_json(
        tmp_path / "manifest.json",
        dict(
            run_id="prior",
            scenario_id="my_way_home",
            level=2,
            seed=101,
            outcome="max_decisions",
            decisions=60,
        ),
    )
    write_json(
        tmp_path / "memory.json",
        dict(landmark_outcome_ledger="The red room might be familiar. " * 100),
    )
    write_json(
        tmp_path / "issues.json", [dict(source="pilot_inline", summary="A possible closed door")]
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps(dict(event="stuck_warning", decision_idx=4, text="Repeated forward")) + "\n"
    )
    for name in ("telemetry.jsonl", "metrics.json", "reviewer.json"):
        (tmp_path / name).write_text("PRIVILEGED_SECRET")
    result = extract(tmp_path)
    assert len(result["brief"]) <= 800 and "4" in result["brief"]
    assert "PRIVILEGED_SECRET" not in result["brief"]
    assert set(result["source_sha256"]) == {
        "manifest.json",
        "events.jsonl",
        "memory.json",
        "issues.json",
    }
    assert prompt_text(result["brief"]).startswith("## Observations from Your Previous Attempt")
    assert result == extract(tmp_path)


def test_fault_cannot_be_enabled_on_ordinary_run():
    with pytest.raises(RuntimeError):
        EnvironmentFaultInjector(is_fault_probe=False, fault="unresponsive_use_trigger")
    vector = [1, 1, 1]
    logged = []
    ordinary = EnvironmentFaultInjector()
    assert (
        ordinary.apply(vector, 1, lambda *a, **kw: logged.append(kw), decision_idx=0, tic=10)
        == vector
    )
    assert not logged
    probe = EnvironmentFaultInjector(is_fault_probe=True, fault="unresponsive_use_trigger")
    assert probe.apply(vector, 1, lambda *a, **kw: logged.append(kw), decision_idx=0, tic=10) == [
        1,
        0,
        1,
    ]
    assert vector == [1, 1, 1] and logged[0]["fault"] == "unresponsive_use_trigger"
