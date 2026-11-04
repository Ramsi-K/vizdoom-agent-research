"""Offline orchestration tests: injected runners never call models."""

import json
from pathlib import Path

import pytest
import yaml
from test_metrics import recorded_run

from autoqa.cli import main
from autoqa.io import write_json
from autoqa.sweep import analyze_runs, execute_sweep, load_config, prepare_sweep


def config_file(tmp_path, *, total=1.2):
    data = dict(
        name="synthetic",
        seeds=[101],
        scenarios={"my_way_home": dict(levels=[1, 2], max_decisions=60, max_tics=2100)},
        max_usd=0.6,
        max_total_usd=total,
    )
    path = tmp_path / "sweep.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_fast_core_matrices_and_limits():
    fast = load_config("configs/sweep_fast.yaml")
    core = load_config("configs/sweep_core.yaml")
    assert len(fast.runs()) == 15 and len(core.runs()) == 45
    assert {c.scenario for c in core.runs()} == {
        "my_way_home",
        "health_gathering_supreme",
        "freedoom2_map01",
    }
    assert all(c.max_decisions == 60 for c in fast.runs())
    assert all(
        c.max_decisions == 100 and c.max_tics == 4200
        for c in core.runs()
        if c.scenario == "freedoom2_map01"
    )


def test_preparation_and_default_cli_make_no_model_calls(tmp_path, monkeypatch):
    import autoqa.run

    monkeypatch.setattr(
        autoqa.run, "run_episode", lambda *a, **kw: pytest.fail("Paid runner reached")
    )
    cfg = config_file(tmp_path)
    out = tmp_path / "plan"
    assert main(["sweep", "--config", str(cfg), "--out", str(out)]) == 0
    plan = json.loads((out / "sweep_manifest.json").read_text())
    assert plan["status"] == "prepared_no_api_calls" and plan["planned_episodes"] == 2
    with pytest.raises(ValueError, match="approval"):
        execute_sweep(out)
    assert not (out / ".execution.lock").exists()
    with pytest.raises(FileExistsError):
        prepare_sweep(cfg, out)


def test_fake_sweep_checkpoints_and_does_not_rerun_recorded_slots(tmp_path):
    cfg = config_file(tmp_path)
    out = tmp_path / "plan"
    prepare_sweep(cfg, out)
    calls = []

    def runner(config, root):
        calls.append(config.level)
        p = recorded_run(tmp_path / "runs", config=config)
        return p, json.loads((p / "manifest.json").read_text())

    result = execute_sweep(out, approved=True, runner=runner)
    assert result["status"] == "finished" and calls == [1, 2]
    assert (out / "summary.json").exists() and (out / "summary.csv").exists()
    assert all((Path(s["run_directory"]) / "metrics.json").exists() for s in result["slots"])
    execute_sweep(out, approved=True, runner=runner)
    assert calls == [1, 2]
    assert len(list(out.glob("analysis_*"))) == 2


def test_total_threshold_and_unknown_cost_stop_before_next_call(tmp_path):
    cfg = config_file(tmp_path, total=0.6)
    out = tmp_path / "plan"
    prepare_sweep(cfg, out)
    calls = []

    def runner(config, root):
        calls.append(config.level)
        p = recorded_run(tmp_path / "runs", config=config, cost=0.6)
        return p, json.loads((p / "manifest.json").read_text())

    result = execute_sweep(out, approved=True, runner=runner)
    assert result["status"] == "stopped_total_cost_threshold" and calls == [1]
    assert result["remaining_slots"] == 1
    # Missing usage cost is separately refused on a fresh plan.
    other = tmp_path / "unknown"
    prepare_sweep(cfg, other)

    def unknown(config, root):
        p = recorded_run(tmp_path / "unknown_runs", config=config, cost=None)
        return p, json.loads((p / "manifest.json").read_text())

    assert execute_sweep(other, approved=True, runner=unknown)["status"] == "stopped_unknown_cost"


def test_failed_inflight_slot_is_not_automatically_retried(tmp_path):
    cfg = config_file(tmp_path)
    out = tmp_path / "plan"
    prepare_sweep(cfg, out)

    def fail(config, root):
        raise RuntimeError("Synthetic interrupted request")

    with pytest.raises(RuntimeError):
        execute_sweep(out, approved=True, runner=fail)
    plan = json.loads((out / "sweep_manifest.json").read_text())
    assert plan["status"] == "failed" and plan["slots"][0]["status"] == "running"
    with pytest.raises(ValueError, match="never auto-repeat"):
        execute_sweep(out, approved=True, runner=fail)


def test_drift_lock_and_config_tampering_block_execution(tmp_path, monkeypatch):
    cfg = config_file(tmp_path)
    out = tmp_path / "plan"
    prepare_sweep(cfg, out)
    (out / ".execution.lock").touch()
    with pytest.raises(FileExistsError):
        execute_sweep(out, approved=True)
    (out / ".execution.lock").unlink()
    import autoqa.sweep

    monkeypatch.setattr(autoqa.sweep, "source_hashes", lambda root: {"changed": "source"})
    with pytest.raises(ValueError, match="changed since"):
        execute_sweep(out, approved=True)
    monkeypatch.undo()
    plan = json.loads((out / "sweep_manifest.json").read_text())
    plan["slots"][0]["config"]["model"] = "not-meta"
    write_json(out / "sweep_manifest.json", plan)
    with pytest.raises(ValueError, match="slots differ"):
        execute_sweep(out, approved=True)


def test_offline_analysis_preserves_source_artifacts_and_refuses_overwrite(tmp_path):
    p = recorded_run(tmp_path / "runs")
    out = tmp_path / "derived"
    analyze_runs([p], out)
    assert not (p / "metrics.json").exists()
    with pytest.raises(FileExistsError):
        analyze_runs([p], out)
    with pytest.raises(SystemExit) as error:
        main(
            [
                "sweep",
                "--config",
                "configs/sweep_fast.yaml",
                "--out",
                str(tmp_path / "plan"),
                "--unknown",
            ]
        )
    assert error.value.code == 2


def test_duplicate_yaml_unknown_fields_and_models_are_rejected(tmp_path):
    cfg = config_file(tmp_path)
    cfg.write_text(cfg.read_text() + "\nname: duplicate\n")
    with pytest.raises(ValueError, match="Duplicate YAML"):
        load_config(cfg)
    cfg = config_file(tmp_path)
    data = yaml.safe_load(cfg.read_text())
    data["model"] = "not-meta"
    cfg.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        load_config(cfg)


def test_bad_pair_stops_before_another_episode_and_reports_reason(tmp_path):
    cfg = config_file(tmp_path, total=1.8)
    data = yaml.safe_load(cfg.read_text())
    data["scenarios"]["my_way_home"]["levels"] = [1, 2, 3]
    cfg.write_text(yaml.safe_dump(data))
    out = tmp_path / "plan"
    prepare_sweep(cfg, out)
    calls = []

    def runner(config, root):
        calls.append(config.level)
        p = recorded_run(tmp_path / "runs", config=config)
        manifest = json.loads((p / "manifest.json").read_text())
        if config.level == 2:
            manifest["spawn_state"]["pos_x"] += 100
            write_json(p / "manifest.json", manifest)
        return p, manifest

    plan = execute_sweep(out, approved=True, runner=runner)
    assert plan["status"] == "stopped_pairing_error" and calls == [1, 2]
    assert plan["pairing_status"] == "incomplete"
    assert "different_spawn_pos_x" in plan["slots"][1]["pairing_errors"]


def test_execute_flag_is_required_and_resume_checks_config(tmp_path, monkeypatch):
    import autoqa.sweep

    cfg = config_file(tmp_path)
    out = tmp_path / "plan"
    calls = []

    def approved(output, *, approved):
        calls.append(approved)
        plan = json.loads((output / "sweep_manifest.json").read_text())
        return dict(
            **{k: v for k, v in plan.items() if k != "status"},
            status="finished",
            pairing_status="complete",
        )

    monkeypatch.setattr(autoqa.sweep, "execute_sweep", approved)
    assert main(["sweep", "--config", str(cfg), "--out", str(out)]) == 0
    assert calls == []
    assert main(["sweep", "--config", str(cfg), "--out", str(out), "--resume", "--execute"]) == 0
    assert calls == [True]
    cfg.write_text(cfg.read_text() + "\n# altered frozen config\n")
    with pytest.raises(SystemExit):
        main(["sweep", "--config", str(cfg), "--out", str(out), "--resume", "--execute"])
    assert calls == [True]
