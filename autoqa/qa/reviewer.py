"""One bounded Meta-only post-episode review, isolated from gameplay memory."""

import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg
from openai import APIConnectionError, APIStatusError, OpenAI
from openai.lib._pydantic import to_strict_json_schema

from autoqa.config import usage_cost
from autoqa.eval.metrics import read_json, read_lines
from autoqa.io import digest, write_json
from autoqa.secrets import model_key

from .schemas import EpisodeQAReview
from .trace import Trace, safe_file

EVENT_FIELDS = {
    "primitive": ("decision_idx", "primitive_idx", "requested", "tic_start", "tic_end"),
    "action_feedback": ("decision_idx", "tic_start", "tic_end", "frame_change_pct"),
    "navigation_update": (
        "decision_idx",
        "source",
        "text",
        "basis",
        "destination",
        "subgoals",
        "grounding_refs",
    ),
    "navigation_action": (
        "decision_idx",
        "intent",
        "basis",
        "destination",
        "subgoal",
        "grounding_refs",
    ),
    "locate": ("decision_idx", "concept", "found", "matches", "method"),
    "stuck_warning": ("decision_idx", "text"),
    "visual_cycle_warning": ("decision_idx", "text", "similar_to", "hamming_distance"),
    "tool_call": ("decision_idx", "name", "arguments"),
}


def observable_log(directory):
    output = []
    for event in read_lines(Path(directory) / "events.jsonl"):
        kind = event.get("event")
        if kind not in EVENT_FIELDS:
            continue
        if kind == "tool_call" and event.get("name") not in {
            "act",
            "note",
            "report_issue",
            "finish",
            "locate",
        }:
            continue
        output.append(
            dict(event=kind, **{key: event[key] for key in EVENT_FIELDS[kind] if key in event})
        )
    return output


def select_frames(directory, events):
    directory = Path(directory)
    frames = sorted((directory / "frames").glob("step_*.jpg"))
    if not frames:
        raise ValueError("No observed frames available for review")
    regular = {frames[round(i * (len(frames) - 1) / 11)] for i in range(12)}
    salient = []
    for event in events:
        if event["event"] in {"stuck_warning", "visual_cycle_warning"} or (
            event["event"] == "tool_call" and event.get("name") == "report_issue"
        ):
            step = event["decision_idx"]
            path = directory / "frames" / f"step_{step:04d}.jpg"
            if path.exists() and path not in salient:
                salient.append(path)
    return sorted(regular | set(salient[-12:]))[:24]


def prepare_video(directory, output):
    from .report import cut_clip

    trace = Trace(directory)
    source = output / "review_native.mp4"
    provenance = cut_clip(
        trace,
        [trace.start, trace.end],
        source,
        output / "native_cache",
        require_captions=True,
        size=(640, 480),
    )
    if not source.exists():
        raise ValueError("Verified complete replay unavailable")
    target = output / "review_input.mp4"
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-v",
            "error",
            "-i",
            str(source),
            "-vf",
            "scale=640:480,fps=10",
            "-an",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            str(target),
        ],
        check=True,
        timeout=90,
    )
    return target, provenance


def review(
    directory,
    output,
    *,
    execute=False,
    skip_reviewer_video=False,
    max_usd=0.2,
    request_timeout=90,
    max_output_tokens=4000,
    client=None,
):
    if not 4000 <= max_output_tokens <= 8000:
        raise ValueError("Reviewer output budget must be in [4000, 8000]")
    if not 0 < request_timeout <= 300:
        raise ValueError("Reviewer request timeout must be in (0, 300]")
    directory, output = Path(directory), Path(output)
    if max_usd <= 0 or max_usd > 1:
        raise ValueError("Reviewer recorded-usage threshold must be in (0, 1]")
    output.mkdir(parents=True, exist_ok=False)
    # No telemetry/geometry is read to build model input. Only prepare_video uses
    # trace integrity/timing internally; its output contains player-visible pixels.
    events = observable_log(directory)
    selected = select_frames(directory, events)
    allowed = {int(path.stem.split("_")[1]) for path in (directory / "frames").glob("step_*.jpg")}
    instruction = (Path(__file__).resolve().parents[2] / "prompts/reviewer_v1.md").read_text()
    text = "Observable decision log (tics are simulator time, not inference time):\n" + json.dumps(
        events, separators=(",", ":")
    )
    write_json(
        output / "observable_input.json",
        dict(
            events=events,
            frames=[
                dict(path=str(path.relative_to(directory)), sha256=digest(path))
                for path in selected
            ],
        ),
    )
    state = dict(
        status="prepared",
        model="muse-spark-1.3",
        effort="high",
        max_output_tokens=max_output_tokens,
        request_timeout=request_timeout,
        recorded_usd=0.0,
        max_usd=max_usd,
        calls=[],
        unknown_charge_risk=False,
        prompt_sha256=hashlib.sha256(instruction.encode()).hexdigest(),
        reviewer_source_sha256=digest(Path(__file__)),
        source_run_id=read_json(directory / "manifest.json")["run_id"],
    )
    target = output / "reviewer_status.json"
    write_json(target, state)
    if not execute:
        return state
    owned = client is None
    client = client or OpenAI(
        api_key=model_key(Path.cwd()),
        base_url="https://api.meta.ai/v1",
        max_retries=0,
        timeout=request_timeout,
    )
    uploaded = None
    try:
        modes = ["keyframes"] if skip_reviewer_video else ["video", "keyframes"]
        for mode in modes:
            if state["recorded_usd"] >= max_usd:
                break
            content = [dict(type="input_text", text=text)]
            if mode == "video":
                try:
                    video, provenance = prepare_video(directory, output)
                    with video.open("rb") as file:
                        uploaded = client.files.create(
                            purpose="user_data",
                            expires_after={"anchor": "created_at", "seconds": 86400},
                            file=file,
                        )
                    content.append(dict(type="input_file", file_id=uploaded.id))
                    state["video_provenance"] = provenance
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    subprocess.SubprocessError,
                    APIStatusError,
                    APIConnectionError,
                ) as error:
                    state["video_preparation_error"] = type(error).__name__
                    write_json(target, state)
                    continue
            else:
                for path in selected:
                    checked = safe_file(directory, str(path.relative_to(directory)), {".jpg"})
                    content += [
                        dict(
                            type="input_text", text=f"Observed decision {path.stem.split('_')[1]}"
                        ),
                        dict(
                            type="input_image",
                            image_url="data:image/jpeg;base64,"
                            + base64.b64encode(checked.read_bytes()).decode(),
                        ),
                    ]
            request = dict(
                model="muse-spark-1.3",
                input=[
                    dict(role="system", content=instruction),
                    dict(role="user", content=content),
                ],
                reasoning=dict(effort="high"),
                max_output_tokens=max_output_tokens,
                store=False,
                text=dict(
                    format=dict(
                        type="json_schema",
                        name="EpisodeQAReview",
                        strict=True,
                        schema=to_strict_json_schema(EpisodeQAReview),
                    )
                ),
            )
            # No hosted tools are enabled. Token count + maximum output is a
            # conservative planning estimate, not a recovered or billed charge.
            try:
                count = client.post("/responses/input_tokens", body=request, cast_to=dict)
                tokens = count["input_tokens"]
                if not isinstance(tokens, int) or tokens < 0:
                    raise ValueError("Invalid token count")
            except (APIStatusError, APIConnectionError, KeyError, ValueError) as error:
                state.update(status="failed_token_preflight", preflight_error=type(error).__name__)
                write_json(target, state)
                return state
            estimate = (tokens * 1.25 + max_output_tokens * 4.25) / 1_000_000
            state.setdefault("preflight", []).append(
                dict(
                    mode=mode,
                    input_tokens=tokens,
                    uncached_input_and_max_output_estimate_usd=estimate,
                )
            )
            if state["recorded_usd"] + estimate > max_usd:
                state["status"] = "stopped_preflight_budget"
                write_json(target, state)
                if mode == "video":
                    continue
                return state
            state.update(status="request_in_flight", unknown_charge_risk=True, active_mode=mode)
            write_json(target, state)
            started = time.monotonic()
            try:
                response = client.responses.create(**request)
            except (APIStatusError, APIConnectionError) as error:
                status = getattr(error, "status_code", None)
                unknown = status not in {400, 401, 403, 404, 413, 415, 422, 429}
                state["calls"].append(
                    dict(
                        mode=mode,
                        error_type=type(error).__name__,
                        http_status=status,
                        usage=None,
                        cost_usd=None,
                    )
                )
                state.update(status="failed_api", unknown_charge_risk=unknown)
                write_json(target, state)
                if unknown or mode == "keyframes":
                    return state
                continue
            usage = response.usage.model_dump() if response.usage else None
            try:
                cost = usage_cost("muse-spark-1.3", usage) if usage else None
            except (KeyError, TypeError, ValueError):
                cost = None
            if cost is not None:
                state["recorded_usd"] += cost
            # Persist billing before touching optional SDK output fields.
            state["calls"].append(
                dict(mode=mode, status=response.status, usage=usage, cost_usd=cost)
            )
            state.update(status="response_received", unknown_charge_risk=cost is None)
            write_json(target, state)
            state["calls"][-1].update(
                dict(
                    mode=mode,
                    status=response.status,
                    usage=usage,
                    cost_usd=cost,
                    latency_ms=(time.monotonic() - started) * 1000,
                    output_structure=[
                        dict(
                            type=item.type,
                            status=getattr(item, "status", None),
                            content_types=[
                                getattr(c, "type", None)
                                for c in (getattr(item, "content", None) or [])
                            ],
                        )
                        for item in response.output
                    ],
                    output_text_chars=len(response.output_text or ""),
                )
            )
            state.update(status="response_received", unknown_charge_risk=cost is None)
            write_json(target, state)  # Account first, including malformed/incomplete responses.
            if cost is None:
                state["status"] = "failed_missing_or_invalid_usage"
                write_json(target, state)
                return state
            # Public model text only; private reasoning/encrypted content is never stored.
            (output / f"response_public_text_{len(state['calls'])}.txt").write_text(
                response.output_text or ""
            )
            state["calls"][-1]["incomplete_reason"] = getattr(
                getattr(response, "incomplete_details", None), "reason", None
            )
            write_json(target, state)
            try:
                if response.status != "completed":
                    raise ValueError("Incomplete model response")
                parsed = EpisodeQAReview.model_validate_json(response.output_text or "")
                if any(set(f.evidence_steps) - allowed for f in parsed.findings):
                    raise ValueError("Reviewer cited unobserved decision")
            except ValueError as error:
                state.update(status="failed_schema_or_incomplete", parse_error=type(error).__name__)
                write_json(target, state)
                if mode == "video" and state["recorded_usd"] < max_usd:
                    continue
                return state
            write_json(output / "reviewer.json", parsed.model_dump())
            state.update(
                status="completed",
                mode=mode,
                findings=len(parsed.findings),
                reviewer_sha256=digest(output / "reviewer.json"),
            )
            write_json(target, state)
            return state
        state["status"] = "failed_no_valid_review"
        write_json(target, state)
        return state
    finally:
        if uploaded:
            try:
                client.files.delete(uploaded.id)
            except (APIStatusError, APIConnectionError):
                state["file_cleanup"] = "delete_failed_expires_after_86400_seconds"
                write_json(target, state)
        if owned:
            client.close()
