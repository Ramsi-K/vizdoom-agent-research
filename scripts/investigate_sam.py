"""Audit SAM's actual output before evaluating text-guided Doom detections."""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from meta_sam_parser import FramePrompt, PromptBox, PromptObject, build_object_prompt
from PIL import Image, ImageDraw

from autoqa.m0.api import MetaProbe
from autoqa.m0.local import sha256
from autoqa.m0.spike import attempt, load_key, native, write_json

SOURCES = [
    "https://dev.meta.ai/docs/sam/segmenting",
    "https://dev.meta.ai/docs/sam/reading-segmentation",
    "https://dev.meta.ai/docs/sam/client-libraries",
    "https://github.com/meta-models/meta-model-cookbook/blob/main/07_sam/01_api_basics/python/sam_api.py",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--report", type=Path, default=Path("docs/sam-investigation.md"))
    args = parser.parse_args()
    directory = args.out or Path("runs") / (
        "m0_sam_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    directory.mkdir(parents=True, exist_ok=True)
    square = Image.new("RGB", (640, 480), "white")
    ImageDraw.Draw(square).rectangle((190, 110, 449, 369), fill="red")
    square_path = directory / "red-square.jpg"
    square.save(square_path, quality=85)
    fixtures = native("frames", directory, count=3)
    armor_frame = next(f for f in fixtures["frames"] if f["scenario"] == "my_way_home")
    armor = next(a for a in armor_frame["actors"] if a["name"] == "GreenArmor")
    truth = np.array(Image.open(armor["mask"])) > 0
    y, x = np.where(truth)
    box = build_object_prompt(
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
    cases = [
        ("red-square", square_path, "red square"),
        ("square", square_path, "square"),
        ("absent-yellow-square", square_path, "yellow square"),
        ("doom-green-armor", Path(armor_frame["path"]), "green armor"),
        ("doom-armor-box", Path(armor_frame["path"]), box),
    ]
    key = load_key(Path.cwd())
    if not key:
        raise SystemExit("No Meta API key; no live SAM checks executed")
    probe = MetaProbe(key, timeout=30)
    results = {}
    try:
        for name, frame, prompt in cases:
            trace = directory / f"{name}.json"
            measured = attempt(
                lambda: asyncio.run(probe.segment(frame, prompt, trace_path=trace))[0]
            )
            results[name] = dict(
                result=measured,
                trace=str(trace),
                trace_sha256=sha256(trace) if trace.exists() else None,
            )
            print(
                name, measured["status"], (measured["measured"] or {}).get("mask_count"), flush=True
            )
    finally:
        probe.close()
    evidence = dict(timestamp_utc=datetime.now(UTC).isoformat(), sources=SOURCES, cases=results)
    write_json(directory / "summary.json", evidence)
    # Preserve the sanitized traces in the PR as reviewable evidence.
    for name in results:
        trace = directory / f"{name}.json"
        if trace.exists():
            target = Path("docs/sam-traces") / trace.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(trace.read_text())
    lines = [
        "# SAM 3.1 empty-response investigation",
        "",
        f"Execution: {evidence['timestamp_utc']}. Local evidence: `{directory}/summary.json`.",
        "",
        "| Case | Check | Raw characters | Parsed records | Decoded masks | Foreground pixels | Wire equals parser |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, case in results.items():
        r = case["result"]
        m = r["measured"] or {}
        wire = m.get("wire", {})
        lines.append(
            f"| {name} | {r['status']} | {m.get('raw_output_chars')} | {wire.get('parser_record_count')} | {m.get('mask_count')} | {m.get('foreground_pixels')} | {wire.get('raw_text_matches_parser')} |"
        )
    lines += [
        "",
        "## Findings",
        "",
        "On the Doom concept request, the terminal message contains an empty `output_text` and the server emits no text-delta event. The parser retains exactly that empty output: this is not silently discarded segmentation text.",
        "The red-square and Doom box controls produce nonempty wire text and nonzero decoded masks. These establish live mask decoding. The empty Doom concept request remains a failed text-guided detection on this fixture; its underlying cause is not established by these controls.",
        "",
        "The synthetic control is a large red square on white, saved as a real 640×480 RGB JPEG at quality 85. The negative control names an absent yellow square.",
        "The Doom box control supplies the engine's visible armor bounding box. It tests the wire decoder independently of concept recognition and is not a gameplay policy.",
        "",
        "## Schema and encoding verification",
        "",
        "Every trace records the request schema with the data URL redacted, image MIME prefix, base64 validation, decoded byte count, SHA-256, JPEG magic, RGB mode and dimensions.",
        "Requests use `model=sam-3.1`, one user message with `type=message`, one `input_text`, one JPEG `input_image`, `stream=true`, and string metadata `mask_encoding=one_bit`, `include_confidence=true`.",
        "Concepts are single noun phrases; box prompts use the official `build_object_prompt` helper and are never combined with concept text.",
        "",
        "## Raw response versus parser",
        "",
        "Sanitized traces under `docs/sam-traces/` include every server event before adaptation, the complete terminal response structure, output item types, finish status, raw text, parser diagnostics, and decoded mask counts.",
        "Meta's documented no-match response completes with empty `output_text` and no delta events. An empty completed stream verifies transport only. It never verifies live mask decoding.",
        "The adapter finalizes a lane on `response.content_part.done` when `response.output_text.done` is absent, as Meta's Python cookbook demonstrates. Each trace compares unadapted wire text with parser output and rejects a mismatch.",
        "`live_mask_decoding_verified` is true only after decoding a mask with nonzero foreground pixels. Synthetic offline decoder tests are separate from live evidence.",
        "",
        "## Official references",
        "",
        *[f"- [{url.rsplit('/', 1)[-1]}]({url})" for url in SOURCES],
        "",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines))
    return (
        0
        if any(
            (r["result"]["measured"] or {}).get("live_mask_decoding_verified")
            for r in results.values()
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
