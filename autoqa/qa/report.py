"""Derived, self-contained evidence reports. Source runs are never rewritten."""

import base64
import html
import json
import shutil
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw

from autoqa.eval.aggregate import summarize
from autoqa.eval.metrics import episode_metrics, number, read_json
from autoqa.eval.plots import heatmaps
from autoqa.io import digest, write_json

from .reference import human_reference
from .trace import Trace, safe_file
from .verify import verify_run


def percentile(values, fraction):
    values = sorted(v for v in values if number(v))
    if not values:
        return None
    position = (len(values) - 1) * fraction
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def model_telemetry(trace):
    manifest = trace.manifest
    latency = [
        e.get("latency_ms")
        for e in trace.events
        if e.get("event") in {"model_response", "grounding_request"}
    ]
    usage = manifest.get("usage") or {}
    incoming = usage.get("input_tokens", 0)
    return dict(
        run_id=manifest["run_id"],
        model=manifest.get("model"),
        effort=manifest.get("effort"),
        episode_recorded_usd=manifest.get("total_usd"),
        usage=usage,
        cache_hit_rate=usage.get("cached_tokens", 0) / incoming if incoming else None,
        sam_requests=sum(
            e.get("event") == "grounding_request" and e.get("model") == "sam-3.1"
            for e in trace.events
        ),
        latency_ms_p50=percentile(latency, 0.5),
        latency_ms_p95=percentile(latency, 0.95),
        failed_request_events=sum(
            e.get("event") in {"api_error", "grounding_error"} for e in trace.events
        ),
        failed_request_charges="unknown; excluded from returned-usage total"
        if any(e.get("event") in {"api_error", "grounding_error"} for e in trace.events)
        else "none logged",
    )


def uri(path):
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4"}[path.suffix]
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def keyframe(trace, step, output):
    source = safe_file(trace.directory, f"frames/step_{step:04d}.jpg", {".jpg"})
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    frame_hash = digest(source)
    overlays = []
    matched_grounding = False
    for event in trace.events:
        if event.get("event") != "locate" or event.get("decision_idx") != step:
            continue
        result = event.get("result", event)
        if result.get("frame_sha256") != frame_hash:
            continue
        matched_grounding = True
        artifact = result.get("overlay")
        if artifact:
            candidate = safe_file(trace.directory, "grounding/" + artifact, {".png"})
            if candidate.exists():
                # Grounder-created overlay is tied to this exact observed JPEG digest.
                overlay = Image.open(candidate).convert("RGB")
                if overlay.size == image.size:
                    image = overlay
                    draw = ImageDraw.Draw(image)
                    overlays.append(str(candidate.relative_to(trace.directory)))
        if artifact:
            stem = Path(artifact).stem.removesuffix("_overlay")
            for index, _ in enumerate(result.get("matches", [])):
                raster = safe_file(trace.directory, f"grounding/{stem}_mask{index}.png", {".png"})
                if raster.exists():
                    mask = Image.open(raster).convert("L")
                    if mask.size == image.size and mask.getbbox():
                        tint = Image.new("RGB", image.size, "cyan")
                        image = Image.composite(
                            tint, image, mask.point(lambda value: round(value * 0.3))
                        )
                        draw = ImageDraw.Draw(image)
                        overlays.append(str(raster.relative_to(trace.directory)))
        for match in result.get("matches", []):
            if match.get("box"):
                draw.rectangle(match["box"], outline="cyan", width=2)
    points = [r for r in trace.rows if number(r.get("pos_x")) and number(r.get("pos_y"))]
    if points:
        inset = Image.new("RGB", (160, 160), "#151c28")
        pen = ImageDraw.Draw(inset)
        xmin, xmax = min(r["pos_x"] for r in points), max(r["pos_x"] for r in points)
        ymin, ymax = min(r["pos_y"] for r in points), max(r["pos_y"] for r in points)
        scale = min(140 / max(128, xmax - xmin), 120 / max(128, ymax - ymin))

        def project(row):
            return (10 + (row["pos_x"] - xmin) * scale, 145 - (row["pos_y"] - ymin) * scale)

        xy = [project(r) for r in points]
        if len(xy) > 1:
            pen.line(xy, fill="gray", width=2)
        before = [
            r
            for r in points
            if trace.tic(r.get("tic")) is not None and trace.tic(r["tic"]) <= trace.frame_tic(step)
        ]
        if before:
            x, y = project(before[-1])
            pen.ellipse((x - 3, y - 3, x + 3, y + 3), fill="orange")
        pen.text((3, 3), "Eval Telemetry Overlay", fill="white")
        image.paste(inset, (image.width - 160, 0))
    label = (
        "SAM mask candidates: identity unverified"
        if any("_mask" in p for p in overlays)
        else "Grounding boxes/candidates only: identity unverified"
        if overlays
        else "No accepted grounding candidate"
        if matched_grounding
        else "No matched grounding evidence; eval overlay only"
    )
    ImageDraw.Draw(image).text((5, image.height - 18), f"Step {step} | {label}", fill="cyan")
    image.save(output)
    return dict(
        source=str(source.relative_to(trace.directory)),
        source_sha256=frame_hash,
        grounding_overlays=overlays,
        grounding_sha256={
            name: digest(safe_file(trace.directory, name, {".png"})) for name in overlays
        },
        sam_raster_status="decoded_nonempty_raster_rendered"
        if any("_mask" in p for p in overlays)
        else "no_matched_nonempty_raster",
    )


def cut_clip(trace, bounds, output, cache, *, require_captions=False, size=(1280, 720)):
    """Cut only authenticated native-gameplay frames; never the replay result card."""
    lower, upper = max(trace.start, bounds[0] - 35), min(trace.end, bounds[1] + 35)
    for video in sorted(trace.directory.glob("*.mp4")):
        info_path = video.with_suffix(".json")
        if not info_path.exists():
            continue
        info = read_json(info_path)
        if require_captions and info.get("captions") is not True:
            continue
        if info.get("size") != list(size):
            continue
        if not (
            info.get("matches_telemetry") is True
            and info.get("lmp_sha256") == trace.source_hashes.get("episode.lmp")
            and info.get("video_sha256") == digest(video)
        ):
            continue
        origin, end = info.get("from_tic"), info.get("to_tic")
        if not (
            number(origin)
            and number(end)
            and origin <= lower < upper <= end
            and info.get("fps") == 35
        ):
            continue
        start, stop = (
            lower - origin,
            min(upper - origin, info.get("gameplay_frames", info.get("frames", 0))),
        )
        if start >= stop:
            continue
        subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-v",
                "error",
                "-i",
                str(video),
                "-vf",
                f"trim=start_frame={start}:end_frame={stop},setpts=PTS-STARTPTS",
                "-an",
                "-c:v",
                "libx264",
                "-y",
                str(output),
            ],
            check=True,
            timeout=60,
        )
        return dict(
            status="cut_verified_native_replay",
            tic_range=[lower, upper],
            source_sha256=digest(video),
            gameplay_frames=stop - start,
        )
    if not (trace.directory / "episode.lmp").exists():
        return dict(status="unavailable_missing_native_recording")
    # Replay writes only to this derived copy, preserving all original source files.
    from autoqa.doom.replay import replay

    cache.mkdir(parents=True, exist_ok=False)
    for path in trace.directory.iterdir():
        if path.is_file() and path.suffix in {".json", ".jsonl", ".lmp"}:
            shutil.copy2(path, cache / path.name)
    replay(
        cache,
        size=size,
        from_tic=lower,
        to_tic=upper,
        captions=True,
        outcome_ending=False,
        output_name="evidence.mp4",
    )
    info = read_json(cache / "evidence.json")
    if info.get("matches_telemetry") is not True:
        return dict(status="unavailable_replay_did_not_match")
    shutil.copy2(cache / "evidence.mp4", output)
    return dict(
        status="rendered_verified_native_slice",
        tic_range=[lower, upper],
        video_sha256=digest(output),
    )


def table(rows):
    if not rows:
        return "<p>No observations recorded.</p>"
    keys = list(dict.fromkeys(k for row in rows for k in row))

    def escaped(value):
        return html.escape(
            json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        )

    return (
        "<table><thead><tr>"
        + "".join("<th>" + escaped(k) + "</th>" for k in keys)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join("<td>" + escaped(row.get(k)) + "</td>" for k in keys) + "</tr>"
            for row in rows
        )
        + "</tbody></table>"
    )


def generate_report(
    directories, output, *, reviewers=None, clips=True, expected=None, experiment_context=None
):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "evidence").mkdir()
    (output / "clips").mkdir()
    reviewers = reviewers or {}
    traces = [Trace(p) for p in directories]
    verifications, observations, evidence = [], [], []
    sam_gallery = []
    for trace in traces:
        result = verify_run(
            trace.directory,
            reviewer=reviewers.get(trace.manifest["run_id"]),
            comparisons=[t.manifest for t in traces],
        )
        grounding_steps = [
            e["decision_idx"] for e in trace.events if e.get("event") == "locate" and e.get("found")
        ]
        for step in list(dict.fromkeys(grounding_steps))[:2]:
            target = output / "evidence" / f"sam_{trace.manifest['run_id']}_{step}.png"
            try:
                provenance = keyframe(trace, step, target)
                if provenance["sam_raster_status"] == "decoded_nonempty_raster_rendered":
                    sam_gallery.append(
                        dict(
                            run_id=trace.manifest["run_id"],
                            step=step,
                            path=str(target.relative_to(output)),
                        )
                    )
                    evidence.append(dict(run_id=trace.manifest["run_id"], step=step, **provenance))
            except (OSError, ValueError):
                pass
        for issue in result["issues"]:
            steps = issue["evidence_steps"]
            selected = list(
                dict.fromkeys(
                    [s for s in grounding_steps if s in steps] + steps[:1] + steps[-1:] + steps
                )
            )[:3]
            for step in selected:
                target = output / "evidence" / f"{issue['issue_id']}_{step}.png"
                try:
                    provenance = keyframe(trace, step, target)
                    issue["evidence_frames"].append(str(target.relative_to(output)))
                    evidence.append(dict(issue_id=issue["issue_id"], step=step, **provenance))
                except (ValueError, OSError) as error:
                    issue["evidence_limitations"].append(
                        f"Keyframe unavailable: {type(error).__name__}"
                    )
            if clips and issue["tic_range"]:
                target = output / "clips" / f"{issue['issue_id']}.mp4"
                try:
                    clip = cut_clip(
                        trace,
                        issue["tic_range"],
                        target,
                        output / "replay_cache" / issue["issue_id"],
                        require_captions=True,
                    )
                    evidence.append(dict(issue_id=issue["issue_id"], clip=clip))
                    if clip["status"] in {
                        "cut_verified_native_replay",
                        "rendered_verified_native_slice",
                    }:
                        issue["replay_clip_path"] = str(target.relative_to(output))
                    else:
                        issue["evidence_limitations"].append(clip["status"])
                except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                    issue["evidence_limitations"].append(
                        f"Clip unavailable: {type(error).__name__}"
                    )
            observations.append(issue)
        verifications.append(result)
    ordinary = [
        t.directory
        for t in traces
        if not t.manifest.get("is_fault_probe")
        and t.manifest.get("evaluation_group") != "carryover"
        and not t.manifest.get("carryover")
    ]
    comparison = None
    comparison_error = None
    if ordinary:
        try:
            comparison = summarize(ordinary, expected=expected)
        except ValueError as error:
            comparison_error = str(error)
    plots = heatmaps(ordinary, output / "heatmaps") if ordinary else []
    if comparison:
        for issue in observations:
            label = next(
                (
                    seed
                    for group in comparison["classifications"]
                    if group["scenario"] == issue["scenario_id"]
                    for seed in group["seeds"]
                    if seed["seed"] == issue["seed"]
                ),
                None,
            )
            if label:
                issue["sensitivity_class"] = label.get("classification")
    metrics = [episode_metrics(t.directory) for t in traces]
    costs = [model_telemetry(t) for t in traces]
    probes = [
        dict(
            run_id=t.manifest["run_id"],
            outcome=t.manifest["outcome"],
            findings=next(
                v["counts"] for v in verifications if v["run_id"] == t.manifest["run_id"]
            ),
        )
        for t in traces
        if t.manifest.get("is_fault_probe")
    ]
    carry = []
    try:
        from autoqa.qa.carryover_metrics import paired_results

        carry = paired_results(
            [t.directory for t in traces if t.manifest.get("evaluation_group") == "carryover"]
        )
    except ImportError:
        pass
    carry_plots = []
    for pair in carry:
        selected = sorted(
            (t for t in traces if t.manifest.get("pair_id") == pair["pair_id"]),
            key=lambda t: t.manifest.get("attempt", 0),
        )
        if len(selected) != 2:
            continue
        root = safe_file(output / "carryover_heatmaps", pair["pair_id"])
        root.mkdir(parents=True)
        sources = [
            heatmaps([t.directory], root / f"attempt{t.manifest['attempt']}") for t in selected
        ]
        for kind in ("trajectory", "coverage"):
            canvas = Image.new("RGB", (1000, 690), "#12161f")
            for index, (trace, paths) in enumerate(zip(selected, sources, strict=True)):
                source = next((Path(path) for path in paths if kind in Path(path).stem), None)
                if source:
                    image = Image.open(source)
                    level = trace.manifest["level"]
                    canvas.paste(
                        image.crop(((level - 1) * 500, 0, level * 500, 650)), (index * 500, 40)
                    )
                    ImageDraw.Draw(canvas).text(
                        (index * 500 + 10, 10),
                        f"Attempt {trace.manifest['attempt']} | {trace.manifest['outcome']} | {pair['status']}",
                        fill="white",
                    )
            target = root / f"paired_{kind}.png"
            canvas.save(target)
            carry_plots.append(target)
    humans = [
        human_reference(scenario)
        for scenario in sorted({t.manifest["scenario_id"] for t in traces})
    ]
    for reference in humans:
        reference["observations_by_documented_mechanic"] = [
            dict(
                mechanic_id=mechanic["id"],
                issue_ids=[
                    issue["issue_id"]
                    for issue in observations
                    if issue["scenario_id"] == reference["scenario"]
                    and issue["category"] == mechanic["category"]
                ],
            )
            for mechanic in reference["documented_design_mechanics"]
        ]
        reference["human_agent_difficulty_agreement"] = None
    level_rows, paired_rows = [], []
    if comparison:
        for group in comparison["groups"]:
            level_rows.append(
                {
                    **{
                        key: group[key]
                        for key in (
                            "scenario",
                            "level",
                            "planned",
                            "recorded",
                            "included",
                            "excluded",
                            "completion_rate",
                        )
                    },
                    **{
                        key: group["metrics"][key]
                        for key in (
                            "progress_fraction",
                            "spl",
                            "coverage_fraction",
                            "revisit_rate",
                            "use_precision",
                            "cost_usd",
                        )
                    },
                }
            )
        for pair in comparison["paired_comparisons"]:
            paired_rows.append(
                {
                    **{
                        key: pair[key]
                        for key in (
                            "scenario",
                            "comparison",
                            "planned_pairs",
                            "matched_pairs",
                            "status",
                        )
                    },
                    **{
                        "delta_" + key: pair["metrics"][key]
                        for key in (
                            "completed",
                            "progress_fraction",
                            "coverage_fraction",
                            "revisit_rate",
                            "cost_usd",
                        )
                    },
                }
            )
    summary = dict(
        schema_version=1,
        experiment_context=experiment_context,
        runs=len(traces),
        ordinary_runs=len(ordinary),
        fault_probes=probes,
        carryover_pairs=carry,
        verification=verifications,
        metrics=metrics,
        comparisons=comparison,
        comparison_error=comparison_error,
        human_reference=humans,
        model_and_cost_telemetry=costs,
        sam_evidence=sam_gallery,
        limitations=[
            "Behavior confirmation is not a game-defect verdict.",
            "Missing/unresolved graph distances remain null; autonomous completion is not human playability.",
        ],
    )
    write_json(output / "summary.json", summary)
    write_json(output / "issues.verified.json", observations)
    write_json(output / "evidence" / "index.json", evidence)
    guard = "Behavior confirmation checks a trace symptom, not a defective level. Agent control, perception, navigation and intended difficulty remain possible causes. Autonomous completion does not substitute for human playtesting. Historical recordings and M3 results are unchanged."
    sections = [
        "<h1>Evidence-backed QA report</h1><aside>" + guard + "</aside>",
        table(
            [
                dict(
                    recorded_runs=len(traces),
                    ordinary_gameplay=len(ordinary),
                    carryover_attempts=sum(
                        t.manifest.get("evaluation_group") == "carryover" for t in traces
                    ),
                    fault_probes=len(probes),
                    model_findings=len(observations),
                    design_defects_confirmed=0,
                )
            ]
        ),
        "<h2>Experiment scope</h2><pre>"
        + html.escape(json.dumps(experiment_context, indent=2))
        + "</pre>",
        "<h2>Episodes and metrics</h2>" + table(metrics),
        "<h2>Information-level comparisons</h2>"
        + table(level_rows)
        + "<h3>Paired differences and information sensitivity</h3>"
        + table(paired_rows)
        + table(comparison["classifications"] if comparison else [])
        + "<details><summary>Full statistics, rejected pairs and uncertainty</summary><pre>"
        + html.escape(json.dumps(comparison, indent=2))
        + "</pre></details><p>"
        + html.escape(comparison_error or "")
        + "</p>",
        "<h2>Controlled fault probes (excluded from ordinary results)</h2>" + table(probes),
        "<h2>Trajectory and coverage</h2>",
    ]
    for path in plots:
        path = Path(path)
        if not path.is_absolute() and not path.exists():
            path = output / "heatmaps" / path.name
        sections.append(
            '<img alt="Evaluation-only trajectory or coverage" src="' + uri(path) + '">'
        )
    sections.append(
        "<h2>SAM candidate evidence</h2><p>Source-matched decoded rasters; candidates are not verified object identities. Evaluation overlays were never agent inputs.</p>"
    )
    for entry in sam_gallery:
        sections.append(
            "<p>"
            + html.escape(f"{entry['run_id']} step {entry['step']}")
            + '</p><img alt="SAM candidate raster and evaluation-only overlay" src="'
            + uri(output / entry["path"])
            + '">'
        )
    if not sam_gallery:
        sections.append(
            "<p>No source-matched nonempty saved raster is available in the selected evidence.</p>"
        )
    sections.append('<h2>Trace-verified observations</h2><label>Filter <input id="filter"></label>')
    dossier = ["# Trace-verified QA observation dossier", "", guard, ""]
    for issue in observations:
        sections.append(
            "<article>"
            + table(
                [
                    {
                        k: issue[k]
                        for k in (
                            "issue_id",
                            "category",
                            "severity",
                            "trace_verification_status",
                            "causal_hypothesis",
                            "summary",
                            "verifier_notes",
                        )
                    }
                ]
            )
        )
        for frame in issue["evidence_frames"]:
            sections.append(
                '<img alt="Cited observed frame with evaluation overlay" src="'
                + uri(output / frame)
                + '">'
            )
        if issue["replay_clip_path"]:
            sections.append(
                '<video controls preload="none" src="'
                + uri(output / issue["replay_clip_path"])
                + '"></video>'
            )
        sections.append(
            "<p>" + html.escape("; ".join(issue["evidence_limitations"])) + "</p></article>"
        )
        dossier += [
            f"## {issue['issue_id']}",
            f"{issue['category']}: {issue['summary']}",
            f"Status: {issue['trace_verification_status']}; causal hypothesis: {issue['causal_hypothesis']}",
            issue["verifier_notes"],
            f"Evidence steps: {issue['evidence_steps']}; tics: {issue['tic_range']}",
            "",
        ]
    if not observations:
        dossier.append(
            "No inline or supplied Reviewer findings were recorded. This does not establish an absence of gameplay issues."
        )
        sections.append(
            "<p>No inline or supplied Reviewer findings. This does not establish an absence of issues.</p>"
        )
    sections.append("<h2>Carry-over feedback (RQ3)</h2>" + table(carry))
    for path in carry_plots:
        sections.append(
            '<img alt="Paired attempts: evaluation-only trajectory/coverage" src="'
            + uri(path)
            + '">'
        )
    sections += [
        "<h2>Human reference (RQ5)</h2>" + table(humans),
        "<h2>Episode model and cost telemetry</h2><p>Returned usage only. Unknown failed-request charges are excluded. Reviewer costs are separate from episode usage.</p>"
        + table(costs),
    ]
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>QA evidence report</title><style>body{background:#12161f;color:#e5e7eb;font:15px system-ui;margin:2rem}aside{padding:1rem;background:#343024}table{display:block;overflow:auto;border-collapse:collapse;margin:1rem 0}th,td{padding:.5rem;border:1px solid #596170;max-width:32rem;vertical-align:top;white-space:pre-wrap}img,video{max-width:100%;width:800px}pre{white-space:pre-wrap}article{border-top:2px solid #596170;margin:2rem 0}input{padding:.4rem}</style>'
        + "".join(sections)
        + '<script>document.getElementById("filter").addEventListener("input",e=>document.querySelectorAll("article").forEach(a=>a.hidden=!a.textContent.toLowerCase().includes(e.target.value.toLowerCase())));</script></html>'
    )
    (output / "report.html").write_text(document)
    (output / "dossier.md").write_text("\n".join(dossier) + "\n")
    return summary
