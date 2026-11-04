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

from .trace import Trace, safe_file
from .verify import verify_run


def uri(path):
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4"}[path.suffix]
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def keyframe(trace, step, output):
    source = safe_file(trace.directory, f"frames/step_{step:04d}.jpg", {".jpg"})
    image = Image.open(source).convert("RGB")
    draw = ImageDraw.Draw(image)
    frame_hash = digest(source)
    overlays = []
    for event in trace.events:
        if event.get("event") != "locate" or event.get("decision_idx") != step:
            continue
        result = event.get("result", event)
        if result.get("frame_sha256") != frame_hash:
            continue
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
    ImageDraw.Draw(image).text(
        (5, image.height - 18), f"Step {step} | SAM candidates: identity unverified", fill="cyan"
    )
    image.save(output)
    return dict(
        source=str(source.relative_to(trace.directory)),
        source_sha256=frame_hash,
        grounding_overlays=overlays,
    )


def cut_clip(trace, bounds, output, cache):
    """Cut only authenticated native-gameplay frames; never the replay result card."""
    lower, upper = max(trace.start, bounds[0] - 35), min(trace.end, bounds[1] + 35)
    for video in sorted(trace.directory.glob("*.mp4")):
        info_path = video.with_suffix(".json")
        if not info_path.exists():
            continue
        info = read_json(info_path)
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


def generate_report(directories, output, *, reviewers=None, clips=True):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "evidence").mkdir()
    (output / "clips").mkdir()
    reviewers = reviewers or {}
    traces = [Trace(p) for p in directories]
    verifications, observations, evidence = [], [], []
    for trace in traces:
        result = verify_run(
            trace.directory,
            reviewer=reviewers.get(trace.manifest["run_id"]),
            comparisons=[t.manifest for t in traces],
        )
        for issue in result["issues"]:
            for step in issue["evidence_steps"][:3]:
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
    ]
    comparison = None
    comparison_error = None
    if ordinary:
        try:
            comparison = summarize(ordinary)
        except ValueError as error:
            comparison_error = str(error)
    plots = heatmaps(ordinary, output / "heatmaps") if ordinary else []
    metrics = [episode_metrics(t.directory) for t in traces]
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
    humans = []
    for scenario in sorted({t.manifest["scenario_id"] for t in traces}):
        source = Path("scenarios") / scenario / "human_reference.json"
        context = read_json(source) if source.exists() else None
        humans.append(
            dict(
                scenario=scenario,
                human_completion_rate=None,
                median_human_tics=None,
                status="not run",
                documented_reference=context,
            )
        )
    summary = dict(
        schema_version=1,
        runs=len(traces),
        ordinary_runs=len(ordinary),
        fault_probes=probes,
        carryover_pairs=carry,
        verification=verifications,
        metrics=metrics,
        comparisons=comparison,
        comparison_error=comparison_error,
        human_reference=humans,
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
        "<h2>Episodes and metrics</h2>" + table(metrics),
        "<h2>Information-level comparisons</h2><pre>"
        + html.escape(json.dumps(comparison, indent=2))
        + "</pre><p>"
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
    sections += [
        "<h2>Carry-over feedback (RQ3)</h2>" + table(carry),
        "<h2>Human reference (RQ5)</h2>" + table(humans),
        "<h2>Model and cost telemetry</h2>"
        + table(
            [
                dict(
                    run_id=t.manifest["run_id"],
                    model=t.manifest.get("model"),
                    effort=t.manifest.get("effort"),
                    recorded_usd=t.manifest.get("total_usd"),
                    usage=t.manifest.get("usage"),
                    sam_calls=t.manifest.get("grounding_model_calls"),
                )
                for t in traces
            ]
        ),
    ]
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>QA evidence report</title><style>body{background:#12161f;color:#e5e7eb;font:15px system-ui;margin:2rem}aside{padding:1rem;background:#343024}table{display:block;overflow:auto;border-collapse:collapse;margin:1rem 0}th,td{padding:.5rem;border:1px solid #596170;max-width:32rem;vertical-align:top;white-space:pre-wrap}img,video{max-width:100%;width:800px}pre{white-space:pre-wrap}article{border-top:2px solid #596170;margin:2rem 0}input{padding:.4rem}</style>'
        + "".join(sections)
        + '<script>document.getElementById("filter").addEventListener("input",e=>document.querySelectorAll("article").forEach(a=>a.hidden=!a.textContent.toLowerCase().includes(e.target.value.toLowerCase())));</script></html>'
    )
    (output / "report.html").write_text(document)
    (output / "dossier.md").write_text("\n".join(dossier) + "\n")
    return summary
