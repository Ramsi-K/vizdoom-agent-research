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
    assert result["protocol"] == "self_observed_only_v2"
    assert (
        extract(tmp_path, protocol="self_observed_only_v1")["protocol"] == "self_observed_only_v1"
    )
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


@pytest.mark.parametrize("new_grounding_error", [False, True])
def test_resuming_skips_invalid_slots_without_rerunning_them(
    tmp_path, monkeypatch, new_grounding_error
):
    import autoqa.carryover as campaign
    from autoqa.io import digest

    monkeypatch.chdir(tmp_path)
    (tmp_path / "autoqa").mkdir()
    cfg = dict(
        protocol="self_observed_only_v1",
        scenario="my_way_home",
        level=2,
        seeds=[101, 102],
        attempts=2,
        model="muse-spark-1.3",
        effort="low",
        max_decisions=60,
        max_tics=2100,
        max_usd_per_episode=0.45,
        wall_seconds=600,
        request_timeout=45,
        max_total_usd=2.9,
    )
    import yaml

    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg))
    out = tmp_path / "campaign"
    out.mkdir()
    completed = []
    for attempt in (1, 2):
        directory = tmp_path / f"prior{attempt}"
        directory.mkdir()
        outcome = "max_decisions" if attempt == 1 else "invalid_api_error"
        write_json(directory / "manifest.json", dict(outcome=outcome, source_sha256={}))
        (directory / "events.jsonl").write_text("")
        completed.append(
            dict(seed=101, attempt=attempt, directory=str(directory), outcome=outcome, cost_usd=0.1)
        )
    write_json(
        out / "validation.json",
        dict(
            status="stopped_error_or_unknown_charge",
            config_sha256=digest(config),
            config=cfg,
            completed=completed,
            recorded_usd=0.2,
            unknown_charge_risk=True,
        ),
    )
    called = []

    def fake_run(config, *, carry_over, experiment):
        called.append((config.seed, experiment["attempt"]))
        directory = tmp_path / f"new{experiment['attempt']}"
        directory.mkdir()
        (directory / "events.jsonl").write_text(
            json.dumps(dict(event="grounding_error", error_type="APIConnectionError")) + "\n"
            if new_grounding_error and experiment["attempt"] == 1
            else ""
        )
        return directory, dict(outcome="max_decisions", total_usd=0.1)

    monkeypatch.setattr(campaign, "run_episode", fake_run)
    monkeypatch.setattr(
        campaign, "extract", lambda directory: dict(brief="uncertain", brief_sha256="fixture")
    )
    assert (
        campaign.main(
            [
                "--config",
                str(config),
                "--out",
                str(out),
                "--resume",
                "--reserve-usd",
                ".3",
                "--continue-after-invalid",
                "--execute",
                *(["--continue-with-reserved-errors"] if new_grounding_error else []),
            ]
        )
        == 1
    )
    assert called == [(102, 1), (102, 2)]
    state = json.loads((out / "validation.json").read_text())
    assert state["status"] == "finished_with_invalid_attempts"
    assert (out / "validation.before-resume-0.json").exists()


def test_feedback_injection_preserves_prefix_images_and_fresh_memory(observation):
    from autoqa.agent.client import sanitize_request
    from autoqa.agent.memory import Memory
    from autoqa.prompts.builder import build

    memory = Memory()
    cold = build(2, memory, observation, briefing_l2="Find the armor.")
    memory.carryover_brief = "Prior uncertain note: the patterned corridor might be a dead end."
    warm = build(2, memory, observation, briefing_l2="Find the armor.")
    assert cold[0] == warm[0]
    assert memory.scratchpad == "" and not memory.history
    assert "Observations from Your Previous Attempt" in warm[-1]["content"][0]["text"]
    assert warm[-1]["content"][1:] == cold[-1]["content"][1:]
    sanitize_request(warm, observation, level=2)
