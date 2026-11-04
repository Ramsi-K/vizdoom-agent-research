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
            scenario_id="probe_broken_door" if fault else "my_way_home",
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


def alter_rows(p, transform):
    rows = [json.loads(line) for line in (p / "telemetry.jsonl").read_text().splitlines()]
    transform(rows)
    (p / "telemetry.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    m = json.loads((p / "manifest.json").read_text())
    m["telemetry_jsonl_sha256"] = digest(p / "telemetry.jsonl")
    write_json(p / "manifest.json", m)


def test_navigation_warn_is_behavior_not_defect(tmp_path):
    p = fixture_run(tmp_path, category="NAVIGATION_CONFUSION")
    with (p / "events.jsonl").open("a") as file:
        file.write(json.dumps(dict(event="stuck_warning", decision_idx=0, text="Repeat")) + "\n")
    m = json.loads((p / "manifest.json").read_text())
    m["events_jsonl_sha256"] = digest(p / "events.jsonl")
    write_json(p / "manifest.json", m)
    issue = verify_run(p)["issues"][0]
    assert issue["trace_verification_status"] == "behavior_confirmed"
    assert issue["causal_hypothesis"] == "CANDIDATE_NAVIGATION_FRICTION_OR_INTENTIONAL_MAZE"
    assert not issue["design_defect_confirmed"]


def test_objective_coverage_and_unattributed_combat_damage(tmp_path):
    p = fixture_run(tmp_path, category="UNCLEAR_OBJECTIVE", collision=False)

    def distances(rows):
        for row in rows:
            row["goal_dist_euclidean"] = 500

    alter_rows(p, distances)
    assert verify_run(p)["issues"][0]["trace_verification_status"] == "behavior_confirmed"
    findings = [
        dict(
            category="MECHANIC_UNCLEAR",
            severity="medium",
            summary="Hazard floor?",
            evidence_steps=[0, 1],
            confidence=0.8,
        )
    ]

    def damage(rows):
        for i, row in enumerate(rows):
            row["health"] = 100 - i * 20

    alter_rows(p, damage)
    assert (
        verify_run(p, findings=findings)["issues"][-1]["trace_verification_status"]
        == "unverifiable"
    )

    def attributed(rows):
        rows[1].update(damage_source="hazard_floor", health_loss=20)

    alter_rows(p, attributed)
    assert (
        verify_run(p, findings=findings)["issues"][-1]["trace_verification_status"]
        == "behavior_confirmed"
    )


def test_visible_lookup_uses_pre_action_frame_not_future_label(tmp_path):
    p = fixture_run(tmp_path, category="VISUAL_AMBIGUITY")
    with (p / "events.jsonl").open("a") as file:
        file.write(
            json.dumps(
                dict(event="locate", decision_idx=0, concept="green armor", found=False, matches=[])
            )
            + "\n"
        )
    m = json.loads((p / "manifest.json").read_text())
    m["events_jsonl_sha256"] = digest(p / "events.jsonl")
    write_json(p / "manifest.json", m)

    def after_only(rows):
        rows[1]["goal_visible_pixels"] = 500

    alter_rows(p, after_only)
    assert verify_run(p)["issues"][0]["trace_verification_status"] == "unverifiable"

    def before(rows):
        rows[0]["goal_visible_pixels"] = 500

    alter_rows(p, before)
    assert verify_run(p)["issues"][0]["trace_verification_status"] == "behavior_confirmed"


@pytest.mark.parametrize("moving", [False, True])
def test_coarse_branch_occupancy_vs_direct_progress(tmp_path, moving):
    p = fixture_run(tmp_path, category="DEAD_END", collision=not moving)
    vertices = [(0, 0), (768, 0), (768, 128), (0, 128)]
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
        ],
        objects=[dict(name="GreenArmor", x=704, y=64, z=0)],
    )
    write_json(p / "geometry.json", geometry)
    issue = verify_run(p)["issues"][0]
    assert issue["trace_verification_status"] == (
        "behavior_contradicted" if moving else "behavior_confirmed"
    )
    assert issue["verifier_metrics"]["coarse_topology"]["dynamic_reachability_verified"] is False


def test_control_alignment_requires_identified_interactive_geometry(tmp_path):
    p = fixture_run(tmp_path, category="INTERACTION_NO_EFFECT")
    write_json(
        p / "geometry.json",
        dict(sectors=[dict(lines=[dict(x1=200, y1=-100, x2=200, y2=100, interactive=True)])]),
    )
    assert verify_run(p)["issues"][0]["causal_hypothesis"] == "AGENT_CONTROL_MISALIGNMENT"
    write_json(
        p / "geometry.json",
        dict(sectors=[dict(lines=[dict(x1=40, y1=-100, x2=40, y2=100, interactive=True)])]),
    )
    assert (
        verify_run(p)["issues"][0]["causal_hypothesis"]
        == "CANDIDATE_PROGRESSION_BLOCKER_NEEDS_HUMAN_CHECK"
    )


@pytest.mark.parametrize("category", ["SOFTLOCK_SUSPECTED", "UNREACHABLE_SUSPECTED"])
def test_reachability_claims_require_dynamic_evidence(tmp_path, category):
    p = fixture_run(tmp_path, category=category)
    issue = verify_run(p)["issues"][0]
    assert issue["trace_verification_status"] == "unverifiable"
    assert issue["causal_hypothesis"] is None


def test_other_confirms_only_its_generic_guard(tmp_path):
    p = fixture_run(tmp_path, category="OTHER")

    def damage(rows):
        for i, row in enumerate(rows):
            row["health"] = 100 - 20 * i

    alter_rows(p, damage)
    issue = verify_run(p)["issues"][0]
    assert issue["trace_verification_status"] == "behavior_confirmed"
    assert "only that symptom" in issue["verifier_notes"]


def test_html_candidate_text_cannot_execute(tmp_path):
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
                    evidence_steps=[0],
                    confidence=0.9,
                )
            ]
        ),
    )
    from unittest.mock import patch

    with (
        patch("autoqa.qa.report.episode_metrics", return_value={}),
        patch("autoqa.qa.report.summarize", return_value={}),
        patch("autoqa.qa.report.heatmaps", return_value=[]),
    ):
        generate_report([p], tmp_path / "html", reviewers={"synthetic": review}, clips=False)
    markup = (tmp_path / "html/report.html").read_text()
    assert "&lt;script&gt;bad()&lt;/script&gt;" in markup
    assert "<script>bad()</script>" not in markup


def test_distinct_reviewer_targets_are_not_collapsed_into_one_window(tmp_path):
    p = fixture_run(tmp_path, category="OTHER")
    findings = [
        dict(
            source="reviewer_post",
            category="INTERACTION_NO_EFFECT",
            severity="low",
            summary=f"Different door {step}",
            evidence_steps=[step],
            confidence=0.6,
        )
        for step in (0, 2)
    ]
    result = verify_run(p, findings=findings)
    assert len(result["issues"]) == 3
    assert [i["summary"] for i in result["issues"][1:]] == ["Different door 0", "Different door 2"]


def test_saved_sam_raster_requires_exact_input_frame_hash(tmp_path):
    from PIL import ImageDraw

    from autoqa.qa.report import keyframe
    from autoqa.qa.trace import Trace

    p = fixture_run(tmp_path)
    (p / "grounding").mkdir()
    frame = p / "frames/step_0000.jpg"
    Image.open(frame).save(p / "grounding/locate_0001_overlay.png")
    mask = Image.new("L", (640, 480))
    ImageDraw.Draw(mask).rectangle((250, 150, 350, 250), fill=255)
    mask.save(p / "grounding/locate_0001_mask0.png")
    event = dict(
        event="locate",
        decision_idx=0,
        concept="candidate",
        found=True,
        frame_sha256=digest(frame),
        overlay="locate_0001_overlay.png",
        matches=[dict(box=[250, 150, 350, 250], confidence=0.8)],
    )
    with (p / "events.jsonl").open("a") as file:
        file.write(json.dumps(event) + "\n")
    m = json.loads((p / "manifest.json").read_text())
    m["events_jsonl_sha256"] = digest(p / "events.jsonl")
    write_json(p / "manifest.json", m)
    result = keyframe(Trace(p), 0, tmp_path / "matched.png")
    assert result["sam_raster_status"] == "decoded_nonempty_raster_rendered"
    trace = Trace(p)
    trace.events[-1]["frame_sha256"] = "mismatched-source"
    result = keyframe(trace, 0, tmp_path / "mismatched.png")
    assert result["sam_raster_status"] == "no_matched_nonempty_raster"
    assert Image.open(tmp_path / "matched.png").getpixel((300, 200)) != Image.open(
        tmp_path / "mismatched.png"
    ).getpixel((300, 200))


def test_partial_replay_cannot_reconstruct_terminal_clock(tmp_path):
    from autoqa.qa.trace import Trace

    p = fixture_run(tmp_path)
    (p / "episode.lmp").write_bytes(b"clearly synthetic fixture, not a real native demo")
    m = json.loads((p / "manifest.json").read_text())
    m.update(final_tic=0, episode_lmp_sha256=digest(p / "episode.lmp"))
    write_json(p / "manifest.json", m)
    data = dict(
        matches_telemetry=True,
        lmp_sha256=m["episode_lmp_sha256"],
        reconstructed_tics_used=140,
        from_tic=40,
        to_tic=80,
    )
    write_json(p / "replay.json", data)
    assert Trace(p).tic(0) is None
    data.update(from_tic=10, to_tic=150)
    write_json(p / "replay.json", data)
    assert Trace(p).tic(0) == 150
    assert Trace(p).tic(5) is None
