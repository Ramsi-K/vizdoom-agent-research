"""Pixel-only Meta grounding. A segmented candidate is not verified identity."""

import asyncio
import base64
import hashlib
import io
import json
import math
import time
from contextlib import aclosing

import numpy as np
from meta_sam_parser import (
    FramePrompt,
    PromptBox,
    PromptObject,
    build_object_prompt,
    decode_mask_to_raster,
    image_segmentation_format,
    parse_responses_stream,
)
from openai import AsyncOpenAI, OpenAI
from PIL import Image, ImageDraw

from autoqa.config import PRICES, usage_cost
from autoqa.sam_wire import SamAudit

BASE_URL = "https://api.meta.ai/v1"
SAM_IMAGE_USD = 0.0025
BOX_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "boxes": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "box": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["box", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["found", "boxes"],
    "additionalProperties": False,
}


def bearing(box, fov=90.0, width=640):
    center = (box[0] + box[2]) / 2
    focal = (width / 2) / math.tan(math.radians(fov) / 2)
    return math.degrees(math.atan((center - width / 2) / focal))


def pixels(box, size=(640, 480)):
    if len(box) != 4 or any(
        isinstance(x, bool)
        or not isinstance(x, (int, float))
        or not math.isfinite(x)
        or not 0 <= x <= 1000
        for x in box
    ):
        raise ValueError("Invalid normalized bounding box")
    result = [
        round(box[0] * size[0] / 1000),
        round(box[1] * size[1] / 1000),
        round(box[2] * size[0] / 1000),
        round(box[3] * size[1] / 1000),
    ]
    if not (result[0] < result[2] and result[1] < result[3]):
        raise ValueError("Empty or inverted bounding box")
    return result


def image_input(frame, text):
    with Image.open(io.BytesIO(frame)) as image:
        if image.size != (640, 480) or image.format != "JPEG":
            raise ValueError("Grounder requires a 640x480 JPEG")
    return [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": text},
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64," + base64.b64encode(frame).decode(),
                },
            ],
        }
    ]


async def parser_events(stream, audit):
    completed = set()
    async for event in stream:
        data = event.model_dump(exclude_none=True)
        audit.observe(data)
        lane = (data.get("item_id"), data.get("output_index"), data.get("content_index"))
        if data["type"] == "response.output_text.done":
            if lane in completed:
                continue
            completed.add(lane)
        yield data
        part = data.get("part", {})
        if (
            data["type"] == "response.content_part.done"
            and part.get("type") == "output_text"
            and lane not in completed
        ):
            completed.add(lane)
            yield dict(
                type="response.output_text.done",
                item_id=lane[0],
                output_index=lane[1],
                content_index=lane[2],
                text=part["text"],
            )


class GroundingBudgetExceeded(RuntimeError):
    pass


class Grounder:
    def __init__(
        self,
        key,
        *,
        mode="sam_text",
        model="muse-spark-1.3",
        fov=90.0,
        timeout=45,
        charge=None,
        directory=None,
        remaining=None,
    ):
        if model not in PRICES or mode not in {"sam_text", "muse_box_sam"}:
            raise ValueError("Only approved Meta grounding modes/models are allowed")
        self.key, self.mode, self.model, self.fov, self.timeout = key, mode, model, fov, timeout
        self.charge = charge or (lambda **kwargs: None)
        self.directory, self.remaining = directory, remaining or (lambda: timeout)
        self.cost_usd = 0.0
        self.calls = []
        self.counter = 0

    def bill(self, model, cost, usage, latency_ms):
        data = dict(model=model, cost_usd=cost, usage=usage, latency_ms=latency_ms)
        self.cost_usd += cost
        self.calls.append(data)
        self.charge(**data)

    def request_timeout(self):
        remaining = self.remaining()
        if remaining <= 0:
            raise GroundingBudgetExceeded("Grounding deadline or budget exhausted")
        return min(self.timeout, remaining)

    def propose(self, frame, concept):
        text = f"Locate visible {concept} in the world view of this image. Exclude HUD, weapon and text. Give tight boxes on a normalized 0-1000 grid, [x_min,y_min,x_max,y_max]. Return found=false and no boxes if absent or uncertain. Do not infer hidden objects. Confidence is your own estimate, not a calibrated probability."
        started = time.monotonic()
        with OpenAI(
            api_key=self.key, base_url=BASE_URL, max_retries=0, timeout=self.request_timeout()
        ) as client:
            response = client.responses.create(
                model=self.model,
                store=False,
                input=image_input(frame, text),
                reasoning={"effort": "low"},
                max_output_tokens=3000,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "VisibleBoxes",
                        "strict": True,
                        "schema": BOX_SCHEMA,
                    }
                },
            )
        if response.usage is None:
            raise ValueError("Grounding usage missing")
        usage = response.usage.model_dump()
        self.bill(
            self.model, usage_cost(self.model, usage), usage, (time.monotonic() - started) * 1000
        )
        if response.status != "completed":
            raise ValueError("Localizer response incomplete")
        output = json.loads(response.output_text)
        if (
            type(output.get("found")) is not bool
            or not isinstance(output.get("boxes"), list)
            or len(output["boxes"]) > 5
        ):
            raise ValueError("Malformed localizer response")
        if bool(output["boxes"]) != output["found"]:
            raise ValueError("Inconsistent localizer found/boxes")
        result = []
        for item in output["boxes"]:
            confidence = item["confidence"]
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                raise ValueError("Invalid localizer confidence")
            result.append(dict(box=pixels(item["box"]), localizer_confidence=confidence))
        return result

    async def segment(self, frame, prompt, *, trace_path=None):
        payload = dict(
            model="sam-3.1",
            input=image_input(frame, prompt),
            stream=True,
            store=False,
            metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
        )
        audit = SamAudit(payload, self.key, trace_path)
        started = time.monotonic()
        try:
            async with AsyncOpenAI(
                api_key=self.key, base_url=BASE_URL, max_retries=0, timeout=self.request_timeout()
            ) as client:
                stream = await client.responses.create(**payload)
                async with stream, aclosing(parser_events(stream, audit)) as events:
                    result = await parse_responses_stream(
                        events, image_segmentation_format()
                    ).final_result()
            wire = audit.finish(result)
            terminal = next(
                e["response"]
                for e in reversed(audit.data["events"])
                if e["type"] == "response.completed"
            )
            usage = terminal.get("usage") or {}
            images = usage.get("images_processed", 1)
            self.bill("sam-3.1", SAM_IMAGE_USD * images, usage, (time.monotonic() - started) * 1000)
            if result.outcome.status != "completed" or any(
                d.severity == "error" for d in result.diagnostics
            ):
                raise ValueError("Incomplete or invalid SAM parse")
            matches = []
            for record in result.records:
                if record.kind != "mask":
                    continue
                raster = np.frombuffer(decode_mask_to_raster(record.mask), dtype=np.uint8).reshape(
                    record.mask.height, record.mask.width
                )
                b = record.bounds
                box = [round(b.left), round(b.top), round(b.right), round(b.bottom)]
                if not (0 <= box[0] < box[2] <= 640 and 0 <= box[1] < box[3] <= 480):
                    raise ValueError("SAM bounds outside input image")
                canvas = np.zeros((480, 640), dtype=np.uint8)
                canvas[box[1] : box[3], box[0] : box[2]] = np.array(
                    Image.fromarray(raster).resize(
                        (box[2] - box[0], box[3] - box[1]), Image.Resampling.NEAREST
                    )
                )
                if not canvas.any():
                    continue
                matches.append(
                    dict(
                        id=record.object_id,
                        box=box,
                        segmentation_confidence=record.confidence,
                        encoding=record.mask.encoding,
                        mask=canvas,
                    )
                )
            return matches, wire
        finally:
            audit.save()

    def locate(self, frame, concept):
        if not isinstance(concept, str) or not 1 <= len(concept.split()) <= 4 or len(concept) > 80:
            raise ValueError("locate requires one 1-4 word noun phrase")
        self.counter += 1
        start_cost = self.cost_usd
        started = time.monotonic()
        proposals = self.propose(frame, concept) if self.mode == "muse_box_sam" else [None]
        results = []
        wire = []
        for index, proposal in enumerate(proposals):
            prompt = (
                concept
                if proposal is None
                else build_object_prompt(
                    size=(640, 480),
                    objects=[
                        PromptObject(
                            id=index + 1, prompts=[FramePrompt(box=PromptBox(*proposal["box"]))]
                        )
                    ],
                )
            )
            trace = (
                self.directory / f"locate_{self.counter:04d}_{index}.json"
                if self.directory
                else None
            )
            matches, summary = asyncio.run(self.segment(frame, prompt, trace_path=trace))
            wire.append(summary)
            for match in matches:
                local = proposal["localizer_confidence"] if proposal else None
                sam = match["segmentation_confidence"]
                # SAM confidence is conditional on a supplied box, not proof of identity.
                confidence = min(local, sam) if local is not None and sam is not None else sam
                if confidence is None or confidence < 0.30:
                    continue
                box = match["box"]
                results.append(
                    dict(
                        **match,
                        confidence=confidence,
                        localizer_confidence=local,
                        bearing_deg=bearing(box, self.fov),
                        vertical_band="above"
                        if (box[1] + box[3]) / 2 < 160
                        else "below"
                        if (box[1] + box[3]) / 2 > 320
                        else "level",
                        area_fraction=float(match["mask"].sum() / (640 * 480)),
                        identity_verified=False,
                    )
                )
        results = results[:5]
        artifact = None
        if self.directory:
            self.directory.mkdir(parents=True, exist_ok=True)
            overlay = Image.open(io.BytesIO(frame)).convert("RGB")
            draw = ImageDraw.Draw(overlay)
            for index, match in enumerate(results):
                Image.fromarray(match["mask"] * 255).save(
                    self.directory / f"locate_{self.counter:04d}_mask{index}.png"
                )
                draw.rectangle(match["box"], outline="cyan", width=2)
            artifact = f"locate_{self.counter:04d}_overlay.png"
            overlay.save(self.directory / artifact)
        return dict(
            found=bool(results),
            matches=[{k: v for k, v in m.items() if k != "mask"} for m in results],
            method=self.mode,
            proposals=proposals,
            cost_usd=self.cost_usd - start_cost,
            latency_ms=(time.monotonic() - started) * 1000,
            frame_sha256=hashlib.sha256(frame).hexdigest(),
            wire=wire,
            overlay=artifact,
        ), results
