"""Synthetic predicates are verification tests, never autonomous detection evidence."""

import json

import pytest
from PIL import Image

from autoqa.io import digest, write_json
from autoqa.qa.report import generate_report
from autoqa.qa.schemas import EpisodeQAReview
from autoqa.qa.trace import safe_file
from autoqa.qa.verify import verify_run


def fixture_run(tmp_path, *, category="BLOCKED_PATH", collision=True, reward=False, fault=False):
    p = tmp_path / "synthetic"
    (p / "frames").mkdir(parents=True)
    rows = [dict(event="init", tic=10, pos_x=0, pos_y=0, angle_deg=0, armor=0)]
    events = []
    for i in range(4):
        Image.new("RGB", (640, 480), "gray").save(p / "frames" / f"step_{i:04d}.jpg")
        rows.append(
            dict(
                event="primitive",
                decision_idx=i,
                primitive_idx=0,
                tic=45 + i * 35,
                pos_x=0 if collision else (i + 1) * 128,
                pos_y=0,
                angle_deg=0,
                armor=0,
                expected_free_displacement=128,
                displacement_units=0 if collision else 128,
                collision_substep=collision,
                reward_delta=int(reward),
                sector_heights=[[0, 128]],
                goal_dist_bfs_status="measured_coarse_grid",
                goal_dist_bfs=512 - (i + 1) * 128,
            )
        )
        events.append(
            dict(
                event="primitive",
                decision_idx=i,
                primitive_idx=0,
                tic_start=10 + i * 35,
                tic_end=45 + i * 35,
                requested_tics=35,
                requested=dict(type="use" if category == "INTERACTION_NO_EFFECT" else "forward"),
            )
        )
    if fault:
        events.append(
            dict(event="env_fault_blocked_use", decision_idx=0, fault="unresponsive_use_trigger")
        )
    for name, data in [("telemetry.jsonl", rows), ("events.jsonl", events)]:
        (p / name).write_text("".join(json.dumps(r) + "\n" for r in data))
    write_json(
        p / "manifest.json",
        dict(
            run_id="synthetic",
            scenario_id="my_way_home",
            level=2,
            seed=101,
            outcome="max_decisions",
            config={},
            is_fault_probe=fault,
            fault_injection="unresponsive_use_trigger" if fault else None,
            events_jsonl_sha256=digest(p / "events.jsonl"),
            telemetry_jsonl_sha256=digest(p / "telemetry.jsonl"),
        ),
    )
    write_json(
        p / "issues.json",
        [
            dict(
                source="pilot_inline",
                category=category,
                severity="medium",
                summary="synthetic physical symptom",
                evidence_steps=[0, 1],
                confidence=0.7,
            )
        ],
    )
    return p


@pytest.mark.parametrize("category", ["BLOCKED_PATH", "INTERACTION_NO_EFFECT"])
@pytest.mark.parametrize("contradicted", [False, True])
def test_physical_predicates(tmp_path, category, contradicted):
    p = fixture_run(tmp_path, category=category, collision=not contradicted, reward=contradicted)
    issue = verify_run(p)["issues"][0]
    assert issue["trace_verification_status"] == (
        "behavior_contradicted" if contradicted else "behavior_confirmed"
    )
    assert not issue["design_defect_confirmed"]


def test_fault_label_requires_both_manifest_and_in_window_event(tmp_path):
    p = fixture_run(tmp_path, category="INTERACTION_NO_EFFECT", fault=True)
    assert verify_run(p)["issues"][0]["causal_hypothesis"] == "CONFIRMED_INJECTED_FAULT"
    m = json.loads((p / "manifest.json").read_text())
    m["is_fault_probe"] = False
    write_json(p / "manifest.json", m)
    assert verify_run(p)["issues"][0]["causal_hypothesis"] is None


def test_missing_frame_and_unresolved_bfs_never_confirm_softlock(tmp_path):
    p = fixture_run(tmp_path, category="SOFTLOCK_SUSPECTED")
    assert verify_run(p)["issues"][0]["trace_verification_status"] == "unverifiable"
    (p / "frames/step_0000.jpg").unlink()
    assert verify_run(p)["issues"][0]["verifier_rule"] == "evidence_unavailable"


def test_dedup_union_and_html_escaping(tmp_path):
    p = fixture_run(tmp_path)
    review = tmp_path / "review.json"
    write_json(
        review,
        dict(
            findings=[
                dict(
                    category="BLOCKED_PATH",
                    severity="high",
                    summary="<script>bad()</script>",
                    rationale="",
                    evidence_steps=[1],
                    confidence=0.9,
                )
            ]
        ),
    )
    result = verify_run(p, reviewer=review)
    assert len(result["issues"]) == 1
    assert result["issues"][0]["source"] == "merged"
    assert result["issues"][0]["model_confidence"] == 0.9
    # A single-run report may lack aggregate manifest metadata, but must render evidence.
    from unittest.mock import patch

    with (
        patch("autoqa.qa.report.episode_metrics", return_value={"run_id": "synthetic"}),
        patch("autoqa.qa.report.summarize", return_value={}),
        patch("autoqa.qa.report.heatmaps", return_value=[]),
    ):
        before = {q.name: digest(q) for q in p.iterdir() if q.is_file()}
        generate_report([p], tmp_path / "report", reviewers={"synthetic": review}, clips=False)
        assert before == {q.name: digest(q) for q in p.iterdir() if q.is_file()}
    assert (tmp_path / "report/report.html").exists()
    assert (
        "Eval Telemetry Overlay" not in (tmp_path / "report/report.html").read_text()
    )  # raster overlay


def test_source_mutation_and_path_escape_rejected(tmp_path):
    p = fixture_run(tmp_path)
    with pytest.raises(ValueError):
        safe_file(p, "../.env")
    (p / "events.jsonl").write_text("")
    with pytest.raises(ValueError, match="changed"):
        verify_run(p)


def test_reviewer_strict_schema():
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(EpisodeQAReview)

    def walk(value):
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert set(value["required"]) == set(value["properties"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(schema)
