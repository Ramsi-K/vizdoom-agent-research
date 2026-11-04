"""Bounded live probes. The OpenAI SDK is an HTTP client for Meta only."""

from __future__ import annotations

import base64
import json
import time
from contextlib import aclosing
from pathlib import Path

import numpy as np
from meta_sam_parser import (
    decode_mask_to_raster,
    image_segmentation_format,
    parse_responses_stream,
)
from openai import AsyncOpenAI, OpenAI
from PIL import Image

from .sam_audit import SamAudit

BASE_URL = "https://api.meta.ai/v1"
PILOT = "muse-spark-1.3"
SAM = "sam-3.1"
CONCEPTS = ("green armor", "vest", "door", "switch", "medkit")
ACT = {
    "type": "function",
    "name": "act",
    "description": "Propose a calibration action; dry run only.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "moves": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "forward",
                                "backward",
                                "strafe_left",
                                "strafe_right",
                                "turn_left",
                                "turn_right",
                                "use",
                                "attack",
                                "wait",
                            ],
                        },
                        "amount": {"type": "integer", "minimum": 1, "maximum": 180},
                    },
                    "required": ["type", "amount"],
                    "additionalProperties": False,
                },
            },
            "run": {"type": "boolean"},
            "intent": {"type": "string", "maxLength": 200},
        },
        "required": ["moves", "run", "intent"],
        "additionalProperties": False,
    },
}


def image_input(path: Path, text: str):
    if Image.open(path).size != (640, 480):
        raise ValueError("Expected a 640x480 Doom JPEG")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return [
        {
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": text},
                {"type": "input_image", "image_url": "data:image/jpeg;base64," + encoded},
            ],
        }
    ]


def rate_headers(headers):
    # Request headers and arbitrary response headers must never enter evidence.
    return {k.lower(): v for k, v in headers.items() if k.lower().startswith("x-ratelimit-")}


def metrics(response, elapsed, headers):
    usage = response.usage.model_dump() if response.usage else None
    return dict(latency_ms=elapsed * 1000, usage=usage, rate_limits=rate_headers(headers))


def replay_input(original, output):
    calls = [item for item in output if item["type"] == "function_call"]
    if not calls:
        raise ValueError("No function_call was returned")
    if not any(i["type"] == "reasoning" and i.get("encrypted_content") for i in output):
        raise ValueError("No encrypted reasoning item was returned")
    # Preserve every output item, including commentary phase and reasoning summary.
    return (
        original
        + output
        + [
            {
                "type": "function_call_output",
                "call_id": call["call_id"],
                "output": json.dumps({"dry_run": True, "executed": False}),
            }
            for call in calls
        ]
    )


class MetaProbe:
    def __init__(self, key: str, timeout=12.0, deadline=None):
        self.key = key
        self.timeout = timeout
        self.deadline = deadline
        self.client = OpenAI(api_key=key, base_url=BASE_URL, timeout=timeout, max_retries=0)

    def request_timeout(self):
        remaining = self.timeout if self.deadline is None else self.deadline - time.perf_counter()
        if remaining <= 0:
            raise RuntimeError("Doctor's 15-second diagnostic budget expired; run spike")
        return min(self.timeout, remaining)

    def close(self):
        self.client.close()

    def catalog(self):
        raw = self.client.models.with_raw_response.list(timeout=self.request_timeout())
        ids = sorted(m.id for m in raw.parse().data)
        missing = sorted({PILOT, SAM} - set(ids))
        return dict(
            model_ids=ids,
            missing=missing,
            rate_limits=rate_headers(raw.headers),
            credits_remaining=None,
            credits_note="No documented balance endpoint; dashboard balance could not be verified.",
        )

    def response(self, **payload):
        started = time.perf_counter()
        raw = self.client.responses.with_raw_response.create(
            model=PILOT, store=False, timeout=self.request_timeout(), **payload
        )
        response = raw.parse()
        elapsed = time.perf_counter() - started
        if response.status != "completed":
            raise ValueError(f"Responses status is {response.status}")
        return response, metrics(response, elapsed, raw.headers)

    def minimal(self):
        started = time.perf_counter()
        raw = self.client.responses.with_raw_response.create(
            model=PILOT,
            store=False,
            input="Reply OK.",
            max_output_tokens=16,
            reasoning={"effort": "minimal"},
            timeout=self.request_timeout(),
        )
        response = raw.parse()
        return dict(
            response_status=response.status,
            **metrics(response, time.perf_counter() - started, raw.headers),
        )

    def token_count(self, frame: Path):
        # Newer SDKs expose the counter, but the generic POST also works with
        # SDKs satisfying the specification's lower bound.
        payload = dict(model=PILOT, input=image_input(frame, "Describe the frame."))
        result = self.client.with_options(timeout=self.request_timeout()).post(
            "/responses/input_tokens", body=payload, cast_to=dict
        )
        if not isinstance(result.get("input_tokens"), int):
            raise ValueError("Token counter returned no input_tokens")
        return result

    def tool_roundtrip(self, frame: Path):
        original = image_input(
            frame,
            "Call act once with an intent describing this frame. This is a dry-run feasibility check.",
        )
        first, timing1 = self.response(
            input=original,
            tools=[ACT],
            tool_choice="auto",
            parallel_tool_calls=True,
            reasoning={"effort": "low"},
            include=["reasoning.encrypted_content"],
            max_output_tokens=3000,
        )
        output = [item.model_dump(exclude_none=True) for item in first.output]
        second, timing2 = self.response(
            input=replay_input(original, output),
            tools=[ACT],
            tool_choice="auto",
            parallel_tool_calls=True,
            reasoning={"effort": "low"},
            include=["reasoning.encrypted_content"],
            max_output_tokens=3000,
        )
        return dict(
            first=timing1,
            second=timing2,
            second_status=second.status,
            output_types=[item["type"] for item in output],
            function_calls=sum(item["type"] == "function_call" for item in output),
            encrypted_reasoning_replayed=True,
            game_actions_executed=0,
        )

    def effort(self, frame: Path, effort: str):
        _, timing = self.response(
            input=image_input(frame, "Describe the visible scene briefly."),
            reasoning={"effort": effort},
            max_output_tokens=3000,
        )
        return timing

    async def segment(self, frame: Path, concept: str, *, trace_path: Path | None = None):
        started = time.perf_counter()
        payload = dict(
            model=SAM,
            input=image_input(frame, concept),
            stream=True,
            metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
            store=False,
        )
        audit = SamAudit(payload, self.key, trace_path)
        async with AsyncOpenAI(
            api_key=self.key, base_url=BASE_URL, timeout=self.request_timeout(), max_retries=0
        ) as client:
            try:
                stream = await client.responses.create(**payload)
                audit.data["http_status"] = stream.response.status_code
                async with stream, aclosing(parser_events(stream, audit)) as events:
                    result = await parse_responses_stream(
                        events, image_segmentation_format()
                    ).final_result()
                wire_summary = audit.finish(result)
            finally:
                audit.save()
        if result.outcome.status != "completed":
            raise ValueError("SAM stream was incomplete")
        if any(d.severity == "error" for d in result.diagnostics):
            raise ValueError("SAM parser reported fatal diagnostics")
        masks, confidences, encodings = [], [], []
        for record in result.records:
            if record.kind != "mask":
                continue
            raster = np.frombuffer(decode_mask_to_raster(record.mask), dtype=np.uint8).reshape(
                record.mask.height, record.mask.width
            )
            # SAM masks can be box-local. Project them onto the original frame
            # using the parser's half-open bounds before comparing to labels.
            bounds = record.bounds
            left, top, right, bottom = map(
                round, (bounds.left, bounds.top, bounds.right, bounds.bottom)
            )
            if not (0 <= left < right <= 640 and 0 <= top < bottom <= 480):
                raise ValueError("SAM mask bounds outside input frame")
            resized = np.array(
                Image.fromarray(raster).resize(
                    (right - left, bottom - top), Image.Resampling.NEAREST
                )
            )
            mask = np.zeros((480, 640), dtype=bool)
            mask[top:bottom, left:right] = resized > 0
            masks.append(mask)
            confidences.append(record.confidence)
            encodings.append(record.mask.encoding)
        measured = dict(
            latency_ms=(time.perf_counter() - started) * 1000,
            concept=concept,
            confidences=confidences,
            encodings=encodings,
            mask_count=len(masks),
            parser="parse_responses_stream(image_segmentation_format())",
            completed=True,
            raw_output_chars=len(result.raw_output),
            foreground_pixels=[int(m.sum()) for m in masks],
            live_mask_decoding_verified=any(m.any() for m in masks),
            wire=wire_summary,
        )
        audit.data["decoded"] = measured
        audit.save()
        return measured, masks

    def video(self, path: Path):
        started = time.perf_counter()
        with path.open("rb") as file:
            uploaded = self.client.files.create(
                file=file,
                purpose="user_data",
                expires_after={"anchor": "created_at", "seconds": 86400},
            )
        upload_ms = (time.perf_counter() - started) * 1000
        try:
            response, timing = self.response(
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "Describe the visible motion in this Doom clip briefly.",
                            },
                            {"type": "input_file", "file_id": uploaded.id},
                        ],
                    }
                ],
                reasoning={"effort": "low"},
                max_output_tokens=3000,
            )
            if not response.output_text:
                raise ValueError("Video response contains no description")
            return dict(upload_ms=upload_ms, inference=timing, bytes=path.stat().st_size)
        finally:
            # Short expiry is also set in case cleanup is temporarily unavailable.
            try:
                self.client.files.delete(uploaded.id)
            except Exception:
                pass


async def parser_events(stream, audit=None):
    """Close output lanes on content_part.done for Meta's SSE variant."""
    closed = set()
    async for event in stream:
        data = event.model_dump(exclude_none=True)
        if audit is not None:
            audit.observe(data)
        lane = (data.get("item_id"), data.get("output_index"), data.get("content_index"))
        kind = data["type"]
        if kind == "response.output_text.done":
            if lane in closed:
                continue
            closed.add(lane)
        yield data
        part = data.get("part", {})
        if (
            kind == "response.content_part.done"
            and part.get("type") == "output_text"
            and lane not in closed
        ):
            closed.add(lane)
            yield dict(
                type="response.output_text.done",
                item_id=lane[0],
                output_index=lane[1],
                content_index=lane[2],
                text=part["text"],
            )


def best_iou(predictions, ground_truth):
    if not ground_truth:
        return None
    return max(
        (
            float(np.logical_and(p, g).sum() / max(1, np.logical_or(p, g).sum()))
            for p in predictions
            for g in ground_truth
        ),
        default=0.0,
    )
