"""M0 runner and evidence reporting. Failures never become measured successes."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from dotenv import dotenv_values
from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt
from openai import APIConnectionError, APIStatusError
from PIL import Image

from .api import CONCEPTS, MetaProbe, best_iou
from .local import environment, iwad_inventory, sha256

TITLES = {
    "1": "Model access, credits, rate limits",
    "2": "Stateless image/tool/reasoning round trip",
    "3": "Reasoning-effort latency and token usage",
    "4": "Bundled IWAD and headless MAP01",
    "5": "-nomonsters and replay synchronization",
    "6": "Turn, movement and FOV calibration",
    "7": "Exact actor and label names",
    "8": "1280x720 HUD demo replay and H.264",
    "9": "SAM streaming and 20-frame perception benchmark",
    "10": "Files upload and video understanding",
}


def load_key(root: Path):
    values = dotenv_values(root / ".env")
    # Existing local setups used the longer name. Prefer the specified name,
    # and never mutate .env or copy the secret into a generated artifact.
    return (
        os.getenv("MODEL_API_KEY")
        or values.get("MODEL_API_KEY")
        or os.getenv("META_MODEL_API_KEY")
        or values.get("META_MODEL_API_KEY")
        or ""
    ).strip()


def error_reason(error):
    # Exception strings from HTTP clients may contain headers, URLs or bodies.
    if isinstance(error, APIStatusError):
        return f"Meta API returned HTTP {error.status_code}; response body omitted"
    if isinstance(error, APIConnectionError):
        return "Meta API connection failed (network/DNS/TLS); no response received"
    if isinstance(error, (ValueError, FileNotFoundError, RuntimeError)):
        return str(error)[:400]
    return type(error).__name__


def result(status, reason=None, **measured):
    return dict(status=status, reason=reason, measured=measured or None)


def attempt(fn):
    try:
        return {"status": "passed", "reason": None, "measured": fn()}
    except Exception as error:
        return result("could not verify", error_reason(error))


def native(task, directory, count=20, timeout=90):
    output = directory / f"{task}.json"
    command = [
        sys.executable,
        "-m",
        "autoqa.m0.worker",
        task,
        "--out",
        str(output),
        "--artifacts",
        str(directory),
        "--count",
        str(count),
    ]
    completed = subprocess.run(command, capture_output=True, timeout=timeout, text=True)
    if completed.returncode:
        raise RuntimeError(
            f"ViZDoom {task} worker exited {completed.returncode}; "
            "check native engine/shared-memory permissions outside the sandbox."
        )
    return json.loads(output.read_text())


def sam_benchmark(probe, frames, journal=None):
    rows = []
    actor_names = {
        "green armor": {"GreenArmor"},
        "vest": {"GreenArmor"},
        "medkit": {"Medikit", "CustomMedikit"},
    }
    for frame in frames:
        for concept in CONCEPTS:
            try:
                measured, predictions = asyncio.run(probe.segment(Path(frame["path"]), concept))
                truth = [
                    np.array(Image.open(a["mask"])) > 0
                    for a in frame["actors"]
                    if a["name"] in actor_names.get(concept, set())
                ]
                rows.append(
                    dict(
                        frame=frame["path"],
                        status="passed",
                        **measured,
                        iou=best_iou(predictions, truth),
                        annotation="engine actor mask"
                        if truth
                        else "not annotated for this concept",
                    )
                )
            except Exception as error:
                rows.append(
                    dict(
                        frame=frame["path"],
                        concept=concept,
                        status="could not verify",
                        reason=error_reason(error),
                    )
                )
                # Avoid repeating an authentication/connection error 100 times.
                if isinstance(error, (APIConnectionError, APIStatusError)):
                    if not isinstance(error, APIStatusError) or error.status_code in (
                        401,
                        403,
                        429,
                    ):
                        return dict(
                            rows=rows,
                            requested_frames=len(frames),
                            complete=False,
                            reason="Stopped on access, rate-limit or network failure",
                        )
            finally:
                if journal:
                    write_json(journal, rows)
                if len(rows) % 20 == 0:
                    print(
                        f"SAM benchmark: {len(rows)}/{len(frames) * len(CONCEPTS)} requests recorded",
                        flush=True,
                    )
    scored = [row["iou"] for row in rows if row.get("iou") is not None]
    confidence = [c for row in rows for c in row.get("confidences", []) if c is not None]
    return dict(
        rows=rows,
        requested_frames=len(frames),
        completed_requests=sum(r["status"] == "passed" for r in rows),
        complete=len(frames) == 20
        and all(r["status"] == "passed" and r.get("iou") is not None for r in rows),
        scored_requests=len(scored),
        iou_threshold=0.5,
        iou_hit_rate=sum(i >= 0.5 for i in scored) / len(scored) if scored else None,
        confidence_distribution=confidence,
        decoded_masks=sum(r.get("mask_count", 0) for r in rows),
        latency_ms={
            "min": min((r["latency_ms"] for r in rows if "latency_ms" in r), default=None),
            "median": float(np.median([r["latency_ms"] for r in rows if "latency_ms" in r]))
            if any("latency_ms" in r for r in rows)
            else None,
            "max": max((r["latency_ms"] for r in rows if "latency_ms" in r), default=None),
        },
        reason="Door/switch masks require manual semantic annotation; absent actor masks are unscored, never successes.",
    )


def sam_box_control(probe, frames, trace_path=None):
    """A ground-truth box probe validates wire decoding independently of detection."""
    for frame in frames:
        actor = next((a for a in frame["actors"] if a["name"] == "GreenArmor"), None)
        if actor is None:
            continue
        truth = np.array(Image.open(actor["mask"])) > 0
        y, x = np.where(truth)
        prompt = build_object_prompt(
            size=(640, 480),
            objects=[
                PromptObject(
                    id=1,
                    prompts=[
                        FramePrompt(
                            box=PromptBox(
                                int(x.min()), int(y.min()), int(x.max() + 1), int(y.max() + 1)
                            )
                        )
                    ],
                )
            ],
        )
        measured, masks = asyncio.run(
            probe.segment(Path(frame["path"]), prompt, trace_path=trace_path)
        )
        if not measured["live_mask_decoding_verified"]:
            raise ValueError(
                "SAM box control returned no mask; live mask decoding remains unverified"
            )
        return dict(
            **measured,
            iou=best_iou(masks, [truth]),
            note="Ground-truth box supplied for M0 decoder validation only; not text-guided detection performance",
        )
    raise ValueError("No visible GreenArmor actor mask available for box control")


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def write_report(path: Path, evidence):
    compact = json.loads(json.dumps(evidence))
    calibration = compact["checks"]["6"]["measured"]
    if calibration:
        calibration["binary_turns"] = {
            side: {"tics_per_90": data["tics_per_90"]}
            for side, data in calibration.get("binary_turns", {}).items()
        }
        calibration.pop("movement_samples", None)
    benchmark = compact["checks"]["9"]["measured"]
    if benchmark:
        benchmark.pop("rows", None)
    lines = [
        "# M0 feasibility findings",
        "",
        f"Executed: {evidence['timestamp_utc']} (UTC).",
        f"Evidence: `{evidence['artifacts']}/summary.json` (local, ignored).",
        "",
        "`passed` means the stated probe ran successfully. `partial` and `could not verify` do not satisfy the full acceptance criterion.",
        "",
        "| Section 2.5 item | Result | Limitation |",
        "| --- | --- | --- |",
    ]
    for key, title in TITLES.items():
        row = evidence["checks"][key]
        reason = (row["reason"] or "—").replace("|", "/").replace("\n", " ")
        lines.append(f"| {key}. {title} | {row['status']} | {reason} |")
    box = (evidence["auxiliary"].get("sam_box_control") or {}).get("measured") or {}
    if calibration:
        lines.extend(
            [
                "",
                "## Findings",
                "",
                f"Measured camera FOV: **{calibration.get('camera_fov_deg')}°**. Binary left/right turns take **{[v['tics_per_90'] for v in calibration.get('binary_turns', {}).values()]} tics** to pass 90° from rest. Positive delta angles turn right; a configured 30° button maximum clips larger turns.",
                f"Eighth-tic forward displacement: `{calibration.get('forward_units_per_tic')}` map units/tic, including acceleration. The full traces are in `configs/calibration.json`.",
                f"SAM text concepts: **{(benchmark or {}).get('completed_requests')}** completed requests on **{evidence.get('fixture_count')}** Doom frames, **{(benchmark or {}).get('decoded_masks')}** masks. IoU hit rate on **{(benchmark or {}).get('scored_requests')}** actor-annotated requests: **{(benchmark or {}).get('iou_hit_rate')}** at threshold 0.5; unannotated door/switch cases are excluded.",
                f"Separate box control: live decoding verified = **{box.get('live_mask_decoding_verified')}**, foreground pixels = **{box.get('foreground_pixels')}**, IoU = **{box.get('iou')}**. This proves decoder operation, not text-guided object recognition.",
            ]
        )
    lines.extend(
        [
            "",
            "## Measurements",
            "",
            "The JSON below is generated from this execution, not illustrative specification values.",
            "Secrets, request headers, encrypted reasoning and image payloads are excluded.",
            "",
            "```json",
            json.dumps(compact, indent=2, allow_nan=False),
            "```",
            "",
            "## Reproduce",
            "",
            "Run in WSL with Python 3.12 and `uv`: `make setup`, `make test`, `make doctor`, `make spike`.",
            "`autoqa doctor --offline` and `autoqa spike --offline` skip all network checks explicitly.",
            "The default spike issues at most 101 SAM requests (20 frames × 5 concepts, plus one box decoder control), three effort samples, a tool round trip and one video upload, with no automatic retries.",
            "Use `MODEL_API_KEY` in the environment or ignored `.env`; the existing `META_MODEL_API_KEY` name is accepted as a compatibility alias.",
            "A nonzero command exit means at least one required check is incomplete. No M1–M5 commands or gameplay agent are implemented.",
            "",
            "## Interpretation limits",
            "",
            "Calibration actions are scripted diagnostic probes, isolated in `autoqa/m0/`; they are not gameplay performance results.",
            "The perception fixtures intentionally seek a visible armor object, log the selected episode, and rotate at spawn. They do not measure completion or agent navigation.",
            "IoU is scored only against visible engine actor masks. Doors and switches have no actor mask and remain unverified without manual annotations.",
            "Movement measurements include the raw acceleration traces and collisions. The reported eighth-tic speeds are not steady-state speeds.",
            "The video test uploads a short real replay, not a two-minute stress test. Credits and missing rate-limit headers cannot be inferred from catalog access.",
            "See [SAM investigation](sam-investigation.md) and its sanitized raw event traces for empty-output controls. An empty completed stream never establishes live mask decoding; the box control must decode nonzero foreground pixels.",
            "Raw artifacts stay local under `runs/`; keep them for inspection. This document and `configs/calibration.json` preserve sanitized findings.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def run_spike(root: Path, *, offline=False, doctor=False, out=None, calibration=None, timeout=12):
    if doctor:
        return run_doctor(root, offline=offline, timeout=timeout)
    start = time.perf_counter()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = root / "runs" / f"m0_spike_{stamp}"
    directory.mkdir(parents=True)
    key = load_key(root)
    checks = {}
    auxiliary = {"environment": attempt(lambda: environment(root, bool(key)))}
    inventory = attempt(iwad_inventory)
    auxiliary["iwad"] = inventory
    frame_result = attempt(lambda: native("frames", directory, count=20))
    frames_data = frame_result["measured"] or {}
    frames = frames_data.get("frames", [])
    checks["7"] = (
        result("passed", **{k: v for k, v in frames_data.items() if k != "frames"})
        if frames
        else frame_result
    )
    calibration_result = attempt(lambda: native("calibrate", directory))
    checks["6"] = calibration_result
    if frames:
        vest_labels = frames_data["actor_names"].get("my_way_home", {}).get("labels", [])
        if "GreenArmor" not in vest_labels:
            checks["7"]["status"] = "partial"
            checks["7"]["reason"] = "GreenArmor not observed in visible Label.object_name samples"
    replay_result = attempt(lambda: dict(runs=native("replay", directory)))
    checks["8"] = replay_result
    checks["4"] = (
        result("passed", **inventory["measured"])
        if inventory["status"] == replay_result["status"] == "passed"
        else result("could not verify", "IWAD inventory or headless record/replay failed")
    )
    checks["5"] = attempt(lambda: native("nomonsters", directory))
    probe = MetaProbe(key, timeout=timeout) if key and not offline else None
    reason = (
        "Offline mode requested" if offline else "MODEL_API_KEY (or compatibility alias) is empty"
    )
    try:
        if probe:
            checks["1"] = attempt(probe.catalog)
            if checks["1"]["status"] == "passed":
                checks["1"]["status"] = "partial"
                checks["1"]["reason"] = (
                    "Credits remaining could not be verified; inspect measured model access and rate headers."
                )
            auxiliary["minimal_response"] = attempt(probe.minimal)
        else:
            checks["1"] = result("could not verify", reason)
        if probe and frames:
            frame = Path(frames[0]["path"])
            auxiliary["image_token_counter"] = attempt(lambda: probe.token_count(frame))
            checks["2"] = attempt(lambda: probe.tool_roundtrip(frame))
            efforts = {
                effort: attempt(lambda e=effort: probe.effort(frame, e))
                for effort in ("minimal", "low", "medium")
            }
            checks["3"] = result(
                "passed" if all(r["status"] == "passed" for r in efforts.values()) else "partial",
                efforts=efforts,
            )
            write_json(directory / "pre-sam.json", dict(checks=checks, auxiliary=auxiliary))
            checks["9"] = attempt(
                lambda: sam_benchmark(probe, frames, directory / "sam-progress.json")
            )
            if checks["9"]["status"] == "passed" and not checks["9"]["measured"]["complete"]:
                checks["9"]["status"] = "partial"
                checks["9"]["reason"] = checks["9"]["measured"]["reason"]
            auxiliary["sam_box_control"] = attempt(
                lambda: sam_box_control(probe, frames, directory / "sam-box-control.json")
            )
            videos = (replay_result.get("measured") or {}).get("runs", [])
            checks["10"] = (
                attempt(lambda: probe.video(Path(videos[0]["video"])))
                if videos
                else result(
                    "could not verify",
                    "No verified replay MP4 available",
                )
            )
        else:
            for n in ("2", "3", "9", "10"):
                checks[n] = result(
                    "could not verify", reason if not probe else "No Doom frame available"
                )
    finally:
        if probe:
            probe.close()
    evidence = dict(
        timestamp_utc=stamp,
        artifacts=str(directory.relative_to(root)),
        wall_seconds=time.perf_counter() - start,
        mode="spike",
        checks={k: checks[k] for k in TITLES},
        auxiliary=auxiliary,
        fixture_count=len(frames),
        fixture_manifest=str(directory / "frames.json"),
        api_base_url="https://api.meta.ai/v1",
    )
    write_json(directory / "summary.json", evidence)
    if not doctor:
        measured_calibration = dict(
            status=checks["6"]["status"],
            reason=checks["6"]["reason"],
            source=str((directory / "summary.json").relative_to(root)),
            source_sha256=sha256(directory / "summary.json"),
            measured=checks["6"]["measured"],
            actor_names=(checks["7"].get("measured") or {}).get("actor_names"),
        )
        write_json(calibration or root / "configs/calibration.json", measured_calibration)
        write_report(out or root / "docs/spike-results.md", evidence)
    for number, title in TITLES.items():
        print(f"{number:>2}. {checks[number]['status']}: {title}", flush=True)
    print(f"Evidence: {directory.relative_to(root)}/summary.json", flush=True)
    return evidence


def run_doctor(root: Path, *, offline=False, timeout=12):
    """Section 13.1's six preflight stages, with independent HTTP probes concurrent."""
    start = time.perf_counter()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = root / "runs" / f"m0_doctor_{stamp}"
    directory.mkdir(parents=True)
    key = load_key(root)
    checks = {"environment": attempt(lambda: environment(root, bool(key)))}
    checks["iwad"] = attempt(iwad_inventory)
    frames_result = attempt(
        lambda: native(
            "frames", directory, count=3, timeout=max(0.1, start + 15 - time.perf_counter())
        )
    )
    frames = (frames_result["measured"] or {}).get("frames", [])
    checks["headless_replay"] = attempt(
        lambda: dict(
            runs=native("replay", directory, timeout=max(0.1, start + 15 - time.perf_counter()))
        )
    )
    blocked = "Offline mode requested" if offline else "Missing API key or Doom frame"
    tasks = ("models", "rate_limits", "image_token_counter", "tool_roundtrip", "sam_stream")
    if key and not offline and frames:
        with ThreadPoolExecutor(max_workers=5) as pool:
            probe = MetaProbe(key, timeout=timeout, deadline=start + 15)
            try:
                frame = Path(frames[0]["path"])
                actions = (
                    probe.catalog,
                    probe.minimal,
                    lambda: probe.token_count(frame),
                    lambda: probe.tool_roundtrip(frame),
                    lambda: sam_box_control(probe, frames, directory / "sam-box-control.json"),
                )
                futures = {
                    name: pool.submit(attempt, action)
                    for name, action in zip(tasks, actions, strict=True)
                }
                checks.update({name: future.result() for name, future in futures.items()})
            finally:
                probe.close()
    else:
        checks.update({name: result("could not verify", blocked) for name in tasks})
    models = checks["models"]
    if models["status"] == "passed" and models["measured"]["missing"]:
        models.update(status="could not verify", reason="Required Meta models absent from catalog")
    env = checks["environment"]
    if env["status"] == "passed" and not all(
        env["measured"][k] for k in ("python_312", "key_present", "env_ignored")
    ):
        env.update(
            status="could not verify",
            reason="Python 3.12, ignored .env, and nonempty key are required",
        )
    duration = time.perf_counter() - start
    evidence = dict(
        timestamp_utc=stamp,
        artifacts=str(directory.relative_to(root)),
        mode="doctor",
        checks=checks,
        wall_seconds=duration,
        within_15_seconds=duration < 15,
        ready=all(c["status"] == "passed" for c in checks.values()) and duration < 15,
    )
    write_json(directory / "summary.json", evidence)
    for name, check in checks.items():
        print(f"{check['status']}: {name}", flush=True)
    print(f"Preflight: {duration:.2f}s; evidence: {directory.relative_to(root)}/summary.json")
    return evidence
