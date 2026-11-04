"""Offline M0 safety, protocol, and real engine smoke checks."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from meta_sam_parser import decode_mask_to_raster, image_segmentation_format, parse_responses_stream

from autoqa.cli import main
from autoqa.m0 import spike
from autoqa.m0.api import best_iou, parser_events, rate_headers, replay_input


def test_stateless_replay_preserves_all_output_and_call_ids():
    original = [{"role": "user", "content": "A frame"}]
    output = [
        {"type": "reasoning", "encrypted_content": "opaque", "summary": []},
        {"type": "message", "phase": "commentary", "content": []},
        {"type": "function_call", "name": "act", "call_id": "a", "arguments": "{}"},
        {"type": "function_call", "name": "act", "call_id": "b", "arguments": "{}"},
    ]
    replayed = replay_input(original, output)
    assert replayed[:5] == original + output
    assert [item["call_id"] for item in replayed[5:]] == ["a", "b"]
    assert all(
        json.loads(item["output"]) == {"dry_run": True, "executed": False} for item in replayed[5:]
    )


@pytest.mark.parametrize("output", [[], [{"type": "function_call", "call_id": "a"}]])
def test_missing_function_or_reasoning_is_not_success(output):
    with pytest.raises(ValueError):
        replay_input([], output)


def test_headers_are_allowlisted():
    assert rate_headers(
        {"Authorization": "secret", "Set-Cookie": "private", "X-Ratelimit-Limit-Requests": "3000"}
    ) == {"x-ratelimit-limit-requests": "3000"}


def test_iou_distinguishes_no_annotation_from_failed_detection():
    mask = np.array([[True, False], [False, True]])
    assert best_iou([mask], []) is None
    assert best_iou([], [mask]) == 0.0
    assert best_iou([mask], [mask]) == 1.0


def test_probe_metadata_cannot_overwrite_check_status():
    measured = {"status": "completed", "reason": "unannotated concepts"}
    wrapped = spike.attempt(lambda: measured)
    assert wrapped["status"] == "passed"
    assert wrapped["reason"] is None
    assert wrapped["measured"] == measured


def test_real_parser_decodes_synthetic_one_bit_wire_and_confidence():
    # Synthetic 2x2 diagonal bitmap, encoded once with the official codec.
    # This is an offline protocol fixture, not a claimed live API detection.
    wire = "<0f>0<|box;x1=0;y1=0;x2=1;y2=1;w=2;h=2;c=0.75|><|mask;x=0;y=0;data=2,2,!!!!!&T:u9`|>"

    async def events():
        lane = dict(item_id="msg", output_index=0, content_index=0)
        for event in [
            dict(type="response.output_text.delta", delta=wire, **lane),
            dict(
                type="response.content_part.done",
                part={"type": "output_text", "text": wire},
                **lane,
            ),
            dict(type="response.output_text.done", text=wire, **lane),
            dict(type="response.completed", response={"status": "completed"}),
        ]:
            yield SimpleNamespace(model_dump=lambda **kwargs: event)

    async def parse():
        return await parse_responses_stream(
            parser_events(events()), image_segmentation_format()
        ).final_result()

    parsed = asyncio.run(parse())
    assert parsed.outcome.status == "completed"
    assert not [d for d in parsed.diagnostics if d.severity == "error"]
    box = next(r for r in parsed.records if r.kind == "box")
    mask = next(r for r in parsed.records if r.kind == "mask")
    assert box.confidence == 0.75
    assert decode_mask_to_raster(mask.mask) == bytes([1, 0, 0, 1])


def test_keys_prefer_spec_name_without_modifying_dotenv(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    dotenv.write_text("META_MODEL_API_KEY=legacy\nMODEL_API_KEY=file-value\n")
    monkeypatch.setenv("MODEL_API_KEY", "environment-value")
    assert spike.load_key(tmp_path) == "environment-value"
    assert dotenv.read_text().startswith("META_MODEL_API_KEY=legacy")


def test_offline_runner_never_constructs_network_client(tmp_path, monkeypatch):
    monkeypatch.setattr(spike, "load_key", lambda _: "dummy-secret")
    monkeypatch.setattr(
        spike, "MetaProbe", lambda *a, **kw: pytest.fail("Network client in offline mode")
    )
    monkeypatch.setattr(spike, "environment", lambda *a: {"python_312": True})
    monkeypatch.setattr(spike, "iwad_inventory", lambda: {"sha": "test"})

    def native(task, directory, count=20):
        if task == "frames":
            return {"frames": [], "actor_names": {}}
        if task == "replay":
            return []
        return {"probe": task}

    monkeypatch.setattr(spike, "native", native)
    report = spike.run_spike(tmp_path, offline=True)
    for number in ("1", "2", "3", "9", "10"):
        assert report["checks"][number]["status"] == "could not verify"
        assert report["checks"][number]["measured"] is None
    assert "dummy-secret" not in (tmp_path / "docs/spike-results.md").read_text()


def test_native_engine_headless_replay_at_higher_resolution(tmp_path):
    result = spike.native("replay", tmp_path)
    assert {r["scenario"] for r in result} == {"my_way_home", "freedoom2"}
    for replay in result:
        assert replay["matches"]
        assert replay["replay_shape"] == [720, 1280, 3]
        assert replay["codec"] == "h264"
        assert Path(replay["lmp"]).stat().st_size > 0


@pytest.mark.parametrize("command", ["sweep", "review"])
def test_future_milestone_commands_are_not_exposed(command):
    with pytest.raises(SystemExit) as error:
        main([command])
    assert error.value.code == 2
