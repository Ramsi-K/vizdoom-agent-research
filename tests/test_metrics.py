"""Synthetic evaluation fixtures, separate from real experiment claims."""

import json

import pytest

from autoqa.config import RunConfig
from autoqa.eval.aggregate import classify, estimate, pairing_errors, summarize, write_summary
from autoqa.eval.metrics import episode_metrics
from autoqa.eval.plots import heatmaps
from autoqa.io import digest, write_json


def recorded_run(root, *, level=1, seed=101, outcome="max_decisions", config=None, cost=0.1):
    config = config or RunConfig(level=level, seed=seed)
    p = root / f"synthetic_{config.scenario}_L{config.level}_s{config.seed}"
    p.mkdir(parents=True)
    points = [64, 192, 64, 320, 320, 320]
    ts, events = [], []
    for index, x in enumerate(points):
        row = dict(
            event="init" if index == 0 else "primitive",
            decision_idx=max(0, index - 1),
            primitive_idx=0,
            tic=10 + index,
            pos_x=x,
            pos_y=64,
            pos_z=0,
            angle_deg=0,
            goal_dist_bfs=448 - x,
            goal_dist_bfs_status="measured_coarse_grid",
            displacement_units=abs(x - points[index - 1]) if index else 0,
            expected_free_displacement=128 if 0 < index < 5 else None,
            door_or_switch_activated=index == 5,
            goal_visible_pixels=0,
        )
        ts.append(row)
        if index:
            events.append(
                dict(
                    event="primitive",
                    decision_idx=index - 1,
                    primitive_idx=0,
                    requested=dict(type="use" if index == 5 else "forward", amount=1),
                )
            )
    events.extend(
        [
            dict(event="locate", decision_idx=0, concept="armor", matches=[dict(confidence=0.8)]),
            dict(event="locate_reused", decision_idx=0, concept="armor"),
            dict(event="grounding_error", decision_idx=1),
        ]
    )
    for name, rows in [("telemetry.jsonl", ts), ("events.jsonl", events)]:
        (p / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    manifest = dict(
        run_id=p.name,
        synthetic_fixture=True,
        scenario_id=config.scenario,
        level=config.level,
        seed=config.seed,
        outcome=outcome,
        total_usd=cost,
        config=config.model_dump(),
        source_sha256={"autoqa/agent/pilot.py": "synthetic_source"},
        prompt_sha256=f"prompt{config.level}",
        briefing_sha256="briefing" if config.level > 1 else None,
        plan_sha256="map" if config.level == 3 else None,
        spawn_state=dict(pos_x=64, pos_y=64, pos_z=0, angle_deg=0, tic=10),
        iwad_sha256="iwad",
        scenario_cfg_sha256="cfg",
        scenario_wad_sha256="wad",
        calibration_sha256="calibration",
        vizdoom_version="synthetic",
        tics_used=5,
        locate_calls=3,
        inline_issues=0,
        usage=dict(input_tokens=100, cached_tokens=25),
        decisions=5,
    )
    for name in ("telemetry.jsonl", "events.jsonl"):
        manifest[name.replace(".", "_") + "_sha256"] = digest(p / name)
    write_json(p / "manifest.json", manifest)
    vertices = [(0, 0), (512, 0), (512, 128), (0, 128)]
    geometry = dict(
        sectors=[
            dict(
                floor=0,
                ceiling=128,
                lines=[
                    dict(x1=a[0], y1=a[1], x2=b[0], y2=b[1], blocking=True)
                    for a, b in zip(vertices, vertices[1:] + vertices[:1], strict=True)
                ],
            )
        ]
    )
    write_json(p / "geometry.json", geometry)
    return p


def change_manifest(path, **fields):
    data = json.loads((path / "manifest.json").read_text())
    data.update(fields)
    write_json(path / "manifest.json", data)


def test_episode_math_and_candidate_reuse(tmp_path):
    p = recorded_run(tmp_path)
    m = episode_metrics(p)
    assert m["sampled_path_units"] == 512
    assert m["progress_fraction"] == pytest.approx(2 / 3)
    assert m["spl"] == 0
    assert m["unique_cells_visited"] == 3 and m["cell_entries"] == 4
    assert m["revisit_rate"] == 0.25 and m["coverage_fraction"] == 0.75
    assert m["total_accessible_cells"] == 4
    assert m["collision_rate"] == 0.25 and m["use_precision"] == 1
    assert m["locate_hit_rate"] == pytest.approx(2 / 3)
    assert m["cache_hit_rate"] == 0.25
    assert m["issues_reviewer_post"] is None


def test_missing_bfs_is_not_replaced_with_euclidean(tmp_path):
    p = recorded_run(tmp_path)
    rows = [json.loads(r) for r in (p / "telemetry.jsonl").read_text().splitlines()]
    for row in rows:
        row["goal_dist_bfs_status"] = "euclidean_fallback_no_sectors"
    (p / "telemetry.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    change_manifest(p, telemetry_jsonl_sha256=digest(p / "telemetry.jsonl"))
    m = episode_metrics(p)
    assert m["progress_fraction"] is None and m["spl"] is None
    change_manifest(p, outcome="success")
    assert episode_metrics(p)["progress_fraction"] == 1
    assert episode_metrics(p)["spl"] is None
    (p / "geometry.json").unlink()
    assert episode_metrics(p)["coverage_fraction"] is None


def test_hash_mismatch_and_nonfinite_input_are_rejected(tmp_path):
    p = recorded_run(tmp_path)
    (p / "events.jsonl").write_text("")
    with pytest.raises(ValueError, match="hash mismatch"):
        episode_metrics(p)
    data = json.loads((p / "manifest.json").read_text())
    data["total_usd"] = float("nan")
    (p / "manifest.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Non-finite"):
        episode_metrics(p)


def test_terminal_clock_reconstruction_requires_matching_native_recording(tmp_path):
    p = recorded_run(tmp_path, outcome="success")
    change_manifest(p, tics_used=-10, episode_lmp_sha256="wrong")
    write_json(
        p / "replay.json",
        dict(lmp_sha256="wrong", matches_telemetry=True, reconstructed_tics_used=1280),
    )
    assert episode_metrics(p)["tics_used"] is None
    (p / "episode.lmp").write_bytes(b"synthetic native fixture, not real gameplay")
    sha = digest(p / "episode.lmp")
    change_manifest(p, episode_lmp_sha256=sha)
    write_json(
        p / "replay.json",
        dict(lmp_sha256=sha, matches_telemetry=True, reconstructed_tics_used=1280),
    )
    assert episode_metrics(p)["tics_used"] == 1280
    assert episode_metrics(p)["tics_source"] == "native_replay_reconstruction"


@pytest.mark.parametrize(
    ("wins", "progress", "label"),
    [
        ((1, 1, 1), (1, 1, 1), "SELF_EVIDENT_TO_AGENT"),
        ((0, 1, 1), (0.1, 1, 1), "DISCOVERABILITY_SENSITIVE"),
        ((0, 0, 1), (0.1, 0.2, 1), "NAVIGATION_SENSITIVE"),
        ((0, 0, 0), (0.1, 0.2, 0.7), "PERSISTENT_FAILURE_L1_TO_L3"),
        ((0, 1, 0), (0.1, 1, 0.4), "NEGATIVE_MAP_TRANSFER"),
        ((0, 0, 0), (0.1, 0.2, None), None),
        ((0, 0, 0), (0.1, 0.2, 0.9), None),
    ],
)
def test_classification_rules_and_missing_evidence(wins, progress, label):
    levels = {
        i: dict(completed=w, progress_fraction=p)
        for i, w, p in zip((1, 2, 3), wins, progress, strict=True)
    }
    assert classify(levels)["classification"] == label
    assert not classify(levels)["causal_claim"]
    assert classify({1: levels[1], 2: levels[2]})["classification"] is None


def test_statistics_small_samples_and_reproducibility():
    assert estimate([])["mean"] is None
    assert estimate([0.2])["ci95"] is None
    assert estimate([0.2, 0.7, 0.9]) == estimate([0.2, 0.7, 0.9])
    interval = estimate([0] * 10, binary=True)["ci95"]
    assert interval[0] == 0 and interval[1] == pytest.approx(0.2775328)


def test_aggregate_exclusions_cost_and_pair_deltas(tmp_path):
    runs = [
        recorded_run(
            tmp_path, level=i, outcome="success" if i == 3 else "max_decisions", cost=i / 10
        )
        for i in (1, 2, 3)
    ]
    summary = summarize(runs)
    pair = next(p for p in summary["paired_comparisons"] if p["comparison"] == "L3-L2")
    assert pair["matched_pairs"] == 1 and pair["metrics"]["completed"]["mean"] == 1
    assert pair["metrics"]["cost_usd"]["mean"] == pytest.approx(0.1)
    assert summary["classifications"][0]["classification"] == "NAVIGATION_SENSITIVE"
    change_manifest(runs[1], outcome="invalid_api_error")
    invalid = summarize(runs)
    assert invalid["groups"][1]["included"] == 0
    assert invalid["recorded_cost_usd"] == pytest.approx(0.6)
    assert invalid["paired_comparisons"][0]["matched_pairs"] == 0
    assert invalid["classifications"][0]["classification"] is None
    output = tmp_path / "output"
    output.mkdir()
    write_summary(output, invalid)
    assert "delta_l3_minus_l2_cost_usd" in (output / "summary.csv").read_text()
    with pytest.raises(ValueError, match="Duplicate"):
        summarize([runs[0], runs[0]])


def test_paired_means_use_matching_seeds_and_reject_different_protocol(tmp_path):
    l1 = recorded_run(tmp_path, level=1, seed=101, cost=0.1)
    l2 = recorded_run(tmp_path, level=2, seed=101, cost=0.3)
    extra = recorded_run(tmp_path, level=2, seed=102, cost=0.9)
    summary = summarize([l1, l2, extra])
    assert summary["paired_comparisons"][0]["metrics"]["cost_usd"]["mean"] == pytest.approx(0.2)
    m = json.loads((l2 / "manifest.json").read_text())
    m["spawn_state"]["pos_x"] += 10
    change_manifest(l2, spawn_state=m["spawn_state"])
    pair = summarize([l1, l2])["paired_comparisons"][0]
    assert pair["matched_pairs"] == 0 and "different_spawn_pos_x" in pair["rejected"][0]["reasons"]
    a = json.loads((l1 / "manifest.json").read_text())
    b = a.copy()
    b["source_sha256"] = {"changed": "runtime"}
    assert "missing_or_different_source_sha256" in pairing_errors(a, b)


def test_heatmaps_are_readable_and_do_not_modify_inputs(tmp_path):
    from PIL import Image

    p = recorded_run(tmp_path)
    before = {f.name: digest(f) for f in p.iterdir()}
    output = tmp_path / "plots"
    paths = heatmaps([p], output)
    assert len(paths) == 2
    for name in paths:
        with Image.open(name) as img:
            assert img.size == (1500, 650)
            assert len(img.getcolors(maxcolors=100000)) > 3
    assert before == {f.name: digest(f) for f in p.iterdir()}


def test_real_replay_bound_schema_and_slices(tmp_path):
    p = recorded_run(tmp_path, outcome="success")
    (p / "episode.lmp").write_bytes(b"synthetic native fixture")
    sha = digest(p / "episode.lmp")
    change_manifest(p, tics_used=None, start_tic=10, episode_lmp_sha256=sha)
    replay = dict(
        lmp_sha256=sha,
        matches_telemetry=True,
        reconstructed_tics_used=1280,
        from_tic=10,
        to_tic=1290,
    )
    write_json(p / "replay.json", replay)
    assert episode_metrics(p)["tics_used"] == 1280
    replay["from_tic"] = 40
    write_json(p / "replay.json", replay)
    assert episode_metrics(p)["tics_used"] is None


def test_no_actions_or_initial_telemetry_do_not_invent_metrics(tmp_path):
    p = recorded_run(tmp_path)
    initial = json.loads((p / "telemetry.jsonl").read_text().splitlines()[0])
    (p / "telemetry.jsonl").write_text(json.dumps(initial) + "\n")
    change_manifest(p, telemetry_jsonl_sha256=digest(p / "telemetry.jsonl"), tics_used=0)
    metrics = episode_metrics(p)
    assert metrics["revisit_rate"] == 0 and metrics["sampled_path_units"] == 0
    assert metrics["collision_rate"] is None and metrics["use_precision"] is None
    (p / "telemetry.jsonl").write_text("")
    change_manifest(p, telemetry_jsonl_sha256=digest(p / "telemetry.jsonl"))
    metrics = episode_metrics(p)
    assert metrics["progress_fraction"] is None and metrics["coverage_fraction"] is None
    assert metrics["unique_cells_visited"] is None


def test_fault_probes_and_mixed_within_level_settings_are_not_pooled(tmp_path):
    a = recorded_run(tmp_path, seed=101)
    b = recorded_run(tmp_path, seed=102)
    config = json.loads((b / "manifest.json").read_text())["config"]
    config["max_usd"] = 0.7
    change_manifest(b, config=config)
    with pytest.raises(ValueError, match="Mixed protocol"):
        summarize([a, b])
    change_manifest(a, is_fault_probe=True)
    summary = summarize([a])
    assert summary["groups"][0]["included"] == 0
    assert summary["groups"][0]["completion_rate"] is None
    assert summary["recorded_cost_usd"] == 0.1
