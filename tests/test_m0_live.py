"""Explicitly opted-in live tests. No credentials or responses are printed."""

import asyncio
import json
from pathlib import Path

import pytest

from autoqa.m0.api import MetaProbe
from autoqa.m0.spike import load_key, native, sam_box_control

pytestmark = pytest.mark.live_api


@pytest.fixture(scope="module")
def live_probe(tmp_path_factory):
    key = load_key(Path.cwd())
    if not key:
        pytest.skip("Meta API key missing; no live checks executed")
    directory = tmp_path_factory.mktemp("live-frames")
    fixtures = native("frames", directory, count=3)
    probe = MetaProbe(key, timeout=30)
    yield probe, Path(fixtures["frames"][0]["path"])
    probe.close()


def test_required_meta_models(live_probe):
    probe, _ = live_probe
    assert not probe.catalog()["missing"]


def test_image_reasoning_tool_roundtrip(live_probe):
    probe, frame = live_probe
    measured = probe.tool_roundtrip(frame)
    assert measured["encrypted_reasoning_replayed"]
    assert measured["second_status"] == "completed"


def test_sam_stream_transport_and_parser(live_probe):
    probe, frame = live_probe
    measured, _ = asyncio.run(probe.segment(frame, "green armor"))
    assert measured["completed"]
    # A valid empty stream does not prove successful object/mask detection.


def test_nonempty_sam_mask_is_decoded(live_probe):
    probe, frame = live_probe
    fixtures = json.loads((frame.parent.parent / "frames.json").read_text())
    measured = sam_box_control(probe, fixtures["frames"])
    assert measured["live_mask_decoding_verified"]
    assert measured["encodings"] == ["one_bit"]
    assert measured["foreground_pixels"][0] > 0
