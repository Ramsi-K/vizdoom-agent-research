"""Reviewer charges before parsing and never sends evaluator files to the model."""

import json
from types import SimpleNamespace

from PIL import Image

from autoqa.io import write_json
from autoqa.qa.reviewer import observable_log, review
from scripts.update_readme import fill


class FakeResponses:
    def __init__(self, text):
        self.text = text
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            status="completed",
            output_text=self.text,
            output=[
                SimpleNamespace(type="reasoning", content=None),
                SimpleNamespace(
                    type="message",
                    status="completed",
                    content=[SimpleNamespace(type="output_text")],
                ),
            ],
            usage=SimpleNamespace(model_dump=lambda: dict(input_tokens=100, output_tokens=100)),
        )


def inputs(tmp_path):
    p = tmp_path / "source"
    (p / "frames").mkdir(parents=True)
    Image.new("RGB", (640, 480)).save(p / "frames/step_0000.jpg")
    write_json(p / "manifest.json", dict(run_id="fixture"))
    (p / "events.jsonl").write_text(
        json.dumps(dict(event="stuck_warning", decision_idx=0, text="Repeated similar views"))
        + "\n"
        + json.dumps(dict(event="eval_only", pos_x="SECRET"))
        + "\n"
    )
    (p / "telemetry.jsonl").write_text("SECRET")
    (p / "geometry.json").write_text("SECRET")
    return p


def test_review_payload_and_usage_before_invalid_json(tmp_path):
    p = inputs(tmp_path)
    client = SimpleNamespace(
        post=lambda *a, **kw: {"input_tokens": 100}, responses=FakeResponses("")
    )
    state = review(p, tmp_path / "out", execute=True, skip_reviewer_video=True, client=client)
    assert state["status"] == "failed_schema_or_incomplete"
    assert state["recorded_usd"] > 0 and state["calls"][0]["output_text_chars"] == 0
    request = client.responses.requests[0]
    assert "SECRET" not in json.dumps(request)
    assert request["reasoning"] == {"effort": "high"}
    assert request["text"]["format"]["strict"] is True and request["store"] is False
    assert len(observable_log(p)) == 1


def test_reviewer_success_and_invalid_evidence_ids(tmp_path):
    p = inputs(tmp_path)
    response = dict(
        episode_summary="A repeat",
        inferred_objective="Unknown",
        findings=[
            dict(
                category="NAVIGATION_CONFUSION",
                severity="low",
                summary="Repeated views",
                rationale="A warning occurred",
                evidence_steps=[999],
                confidence=0.7,
                recommended_developer_check="Review the route choices.",
            )
        ],
    )
    client = SimpleNamespace(
        post=lambda *a, **kw: {"input_tokens": 100}, responses=FakeResponses(json.dumps(response))
    )
    state = review(p, tmp_path / "bad", execute=True, skip_reviewer_video=True, client=client)
    assert state["status"] == "failed_schema_or_incomplete"
    assert not (tmp_path / "bad/reviewer.json").exists()
    response["findings"][0]["evidence_steps"] = [0]
    client = SimpleNamespace(
        post=lambda *a, **kw: {"input_tokens": 100}, responses=FakeResponses(json.dumps(response))
    )
    state = review(p, tmp_path / "good", execute=True, skip_reviewer_video=True, client=client)
    assert state["status"] == "completed" and state["recorded_usd"] > 0


def test_readme_only_explicit_markers_and_part_two():
    text = "Intro must stay.\n<!-- AUTOQA: Key Findings START -->\nplaceholder\n<!-- AUTOQA: Key Findings END -->\n# Part II — Research Motivation\nProtected prose.\n# Part III — Methodology\n"
    updated, changed = fill(text, {"runs": 6}, "report.html")
    assert changed == ["Key Findings"] and "6 recorded episodes" in updated
    assert updated.split("# Part II —")[1] == text.split("# Part II —")[1]
    assert fill("Existing finished prose", {}, "report.html") == ("Existing finished prose", [])


def test_owned_client_receives_requested_timeout(tmp_path, monkeypatch):
    import autoqa.qa.reviewer as module

    p = inputs(tmp_path)
    captured = {}
    client = SimpleNamespace(
        post=lambda *a, **kw: {"input_tokens": 100},
        responses=FakeResponses(
            json.dumps(dict(episode_summary="No event", inferred_objective="Unknown", findings=[]))
        ),
        close=lambda: None,
    )

    def factory(**kwargs):
        captured.update(kwargs)
        return client

    monkeypatch.setattr(module, "OpenAI", factory)
    monkeypatch.setattr(module, "model_key", lambda root: "fixture-not-a-secret")
    state = review(
        p, tmp_path / "timeout", execute=True, skip_reviewer_video=True, request_timeout=180
    )
    assert captured["timeout"] == 180 and state["status"] == "completed"


def test_video_preparation_has_no_undefined_timeout(tmp_path, monkeypatch):
    import autoqa.qa.report as renderer
    import autoqa.qa.reviewer as module

    p = inputs(tmp_path)
    monkeypatch.setattr(module, "Trace", lambda directory: SimpleNamespace(start=10, end=100))

    def native(trace, bounds, source, cache, *, require_captions=False, size=(1280, 720)):
        assert require_captions is True
        assert size == (640, 480)
        source.write_bytes(b"synthetic video fixture")
        return {"status": "synthetic_fixture_only"}

    monkeypatch.setattr(renderer, "cut_clip", native)
    captured = []
    monkeypatch.setattr(
        module.subprocess, "run", lambda args, **kwargs: captured.append((args, kwargs))
    )
    output = tmp_path / "video"
    output.mkdir()
    target, provenance = module.prepare_video(p, output)
    assert target.name == "review_input.mp4"
    assert captured[0][1]["timeout"] == 90
    assert "scale=640:480,fps=10" in captured[0][0]
    assert "+faststart" in captured[0][0]
    assert provenance["status"] == "synthetic_fixture_only"


def test_human_measurements_cannot_be_invented(tmp_path):
    import pytest

    from autoqa.qa.reference import human_reference

    target = tmp_path / "scenarios/my_way_home/human_reference.json"
    target.parent.mkdir(parents=True)
    data = dict(
        scenario_id="my_way_home",
        human_runs_dir=None,
        human_runs_recorded=0,
        human_completion_rate=0.95,
        median_human_tics=100,
        documented_design_mechanics=[],
    )
    write_json(target, data)
    with pytest.raises(ValueError, match="SPECTATOR"):
        human_reference("my_way_home", root=tmp_path)
    data.update(human_completion_rate=None, median_human_tics=None)
    write_json(target, data)
    assert human_reference("my_way_home", root=tmp_path)["status"] == "not run"
    from pathlib import Path

    for source in Path("scenarios").glob("*/human_reference.json"):
        reference = human_reference(source.parent.name)
        assert reference["human_completion_rate"] is None
        assert reference["median_human_tics"] is None
