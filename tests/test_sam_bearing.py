import asyncio
import io
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
from meta_sam_parser import decode_mask_to_raster, image_segmentation_format, parse_responses_stream
from PIL import Image

from autoqa.agent.grounder import Grounder, bearing, image_input, parser_events, pixels
from autoqa.sam_wire import SamAudit

FIXTURES = Path(__file__).parent / "fixtures/m2"


def test_optical_bearing_on_ten_real_recorded_actor_frames():
    rows = json.loads((FIXTURES / "bearings.json").read_text())
    assert len(rows) == 10
    assert {r["scenario"] for r in rows} == {"my_way_home", "freedoom2_map01"}
    errors = []
    for row in rows:
        with Image.open(FIXTURES / row["frame"]) as frame:
            assert frame.size == (640, 480)
        angle = math.degrees(math.atan2(row["obj_y"] - row["pos_y"], row["obj_x"] - row["pos_x"]))
        truth = -((angle - row["angle_deg"] + 180) % 360 - 180)
        errors.append(abs(bearing(row["box"], row["camera_fov"]) - truth))
    assert sum(errors) / len(errors) < 3.0
    assert max(errors) < 3.0


def test_real_sam_wire_mask_and_confidence_decode_without_network():
    wire = json.loads((FIXTURES / "sam_events.json").read_text())
    frame = (FIXTURES / "my_way_home_00.jpg").read_bytes()
    audit = SamAudit(dict(input=image_input(frame, "green armor")))

    async def events():
        for event in wire:
            yield SimpleNamespace(model_dump=lambda exclude_none=True, data=event: data)

    async def parse():
        return await parse_responses_stream(
            parser_events(events(), audit), image_segmentation_format()
        ).final_result()

    parsed = asyncio.run(parse())
    assert audit.finish(parsed)["raw_text_matches_parser"]
    mask = next(r for r in parsed.records if r.kind == "mask")
    raster = decode_mask_to_raster(mask.mask)
    assert mask.mask.encoding == "one_bit"
    assert 0 < mask.confidence <= 1
    assert len(raster) == mask.mask.width * mask.mask.height
    assert sum(raster) > 0


def test_grounder_projects_recorded_mask_and_reports_geometry(monkeypatch):
    wire = json.loads((FIXTURES / "sam_events.json").read_text())

    class RecordedStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def __aiter__(self):
            async def events():
                for event in wire:
                    yield SimpleNamespace(model_dump=lambda exclude_none=True, data=event: data)

            return events()

    class RecordedClient(RecordedStream):
        def __init__(self, **kwargs):
            self.responses = self

        async def create(self, **kwargs):
            assert kwargs["model"] == "sam-3.1"
            return RecordedStream()

    monkeypatch.setattr("autoqa.agent.grounder.AsyncOpenAI", RecordedClient)
    grounder = Grounder("dummy", mode="sam_text")
    public, matches = grounder.locate((FIXTURES / "my_way_home_00.jpg").read_bytes(), "green armor")
    match = public["matches"][0]
    assert match["box"] == [304, 237, 353, 287]
    assert match["confidence"] == 0.8515625
    assert match["vertical_band"] == "level"
    assert matches[0]["mask"].shape == (480, 640)
    assert matches[0]["mask"].sum() == 1475
    assert match["area_fraction"] == 1475 / (640 * 480)
    assert match["identity_verified"] is False
    assert public["wire"][0]["raw_text_matches_parser"]


@pytest.mark.parametrize(
    "box",
    [
        [-1, 0, 500, 500],
        [0, 0, 1001, 1000],
        [500, 0, 400, 500],
        [0, 0, float("nan"), 500],
        [True, 0, 500, 500],
    ],
)
def test_localizer_boxes_are_rejected_not_clamped(box):
    with pytest.raises(ValueError):
        pixels(box)


def test_empty_completed_sam_is_not_a_successful_detection(observation):
    grounder = Grounder("dummy", mode="sam_text")

    async def empty(*args, **kwargs):
        return [], {"genuinely_empty_model_output": True}

    grounder.segment = empty
    public, masks = grounder.locate(observation.frame_jpeg, "green armor")
    assert public["found"] is False
    assert public["matches"] == [] and masks == []


def test_grounder_checks_image_and_proposal_coordinate_conventions(observation):
    assert pixels([250, 250, 750, 750]) == [160, 120, 480, 360]
    assert bearing([310, 200, 330, 300]) == 0
    out = io.BytesIO()
    Image.new("RGB", (100, 100)).save(out, format="JPEG")
    with pytest.raises(ValueError):
        image_input(out.getvalue(), "object")
