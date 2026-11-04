"""Offline trajectory and sampled-cell heatmaps, using the existing Pillow dependency."""

import math
from collections import Counter

from PIL import Image, ImageDraw

from .metrics import cell, number, read_json, read_lines, xy


def heatmaps(directories, output):
    output.mkdir(exist_ok=True)
    scenarios = sorted(
        {
            read_json(p / "manifest.json")["scenario_id"]
            for p in directories
            if not read_json(p / "manifest.json").get("is_fault_probe", False)
        }
    )
    written = []
    for scenario in scenarios:
        runs = []
        for p in directories:
            manifest = read_json(p / "manifest.json")
            if manifest["scenario_id"] == scenario and not manifest.get("is_fault_probe", False):
                ts = read_lines(p / "telemetry.jsonl")
                points = [
                    r
                    for r in ts
                    if r.get("event") in {"init", "primitive"}
                    and all(number(r.get(k)) for k in ("pos_x", "pos_y"))
                ]
                geo = read_json(p / "geometry.json") if (p / "geometry.json").exists() else {}
                runs.append((manifest, points, geo))
        lines = [line for _, _, geo in runs for s in geo.get("sectors", []) for line in s["lines"]]
        points = (
            [xy(t) for _, ts, _ in runs for t in ts]
            + [(line["x1"], line["y1"]) for line in lines]
            + [(line["x2"], line["y2"]) for line in lines]
        )
        if not points:
            continue
        xmin, xmax = min(p[0] for p in points) - 128, max(p[0] for p in points) + 128
        ymin, ymax = min(p[1] for p in points) - 128, max(p[1] for p in points) + 128
        scale = min(440 / max(1, xmax - xmin), 470 / max(1, ymax - ymin))
        peak = (
            max(
                (
                    max(
                        Counter(
                            cell(t) for m, ts, _ in runs if m["level"] == level for t in ts
                        ).values(),
                        default=0,
                    )
                    for level in (1, 2, 3)
                ),
                default=1,
            )
            or 1
        )

        def project(point, offset):
            return (
                offset + 250 + (point[0] - (xmin + xmax) / 2) * scale,
                300 - (point[1] - (ymin + ymax) / 2) * scale,
            )

        for kind in ("trajectory", "coverage"):
            image = Image.new("RGB", (1500, 650), "#12161F")
            draw = ImageDraw.Draw(image)
            draw.text(
                (18, 12),
                f"{scenario} | {kind} | evaluation telemetry (never agent input)",
                fill="white",
            )
            for level in (1, 2, 3):
                offset = (level - 1) * 500
                selected = [(m, ts, geo) for m, ts, geo in runs if m["level"] == level]
                draw.text(
                    (offset + 20, 42),
                    f"L{level} | {len(selected)} recorded episode(s)",
                    fill="white",
                )
                if not selected:
                    draw.text((offset + 20, 80), "Not scheduled / no recorded data", fill="#CBD5E1")
                    continue
                if kind == "coverage":
                    counts = Counter(cell(t) for _, ts, _ in selected for t in ts)
                    for (x, y), count in counts.items():
                        a, b = (
                            project((x * 128, (y + 1) * 128), offset),
                            project(((x + 1) * 128, y * 128), offset),
                        )
                        strength = math.log1p(count) / math.log1p(peak)
                        draw.rectangle(
                            [a, b],
                            fill=(round(40 + 210 * strength), round(65 + 110 * (1 - strength)), 60),
                        )
                geo = selected[0][2]
                for sector in geo.get("sectors", []):
                    for line in sector["lines"]:
                        draw.line(
                            [
                                project((line["x1"], line["y1"]), offset),
                                project((line["x2"], line["y2"]), offset),
                            ],
                            fill="#E0E7EF" if line["blocking"] else "#526477",
                            width=1,
                        )
                if scenario in {"my_way_home", "deadly_corridor_nav"}:
                    for obj in geo.get("objects", []):
                        if obj["name"] == "GreenArmor":
                            gx, gy = project((obj["x"], obj["y"]), offset)
                            draw.ellipse(
                                (gx - 4, gy - 4, gx + 4, gy + 4), outline="#60F0A0", width=2
                            )
                            draw.text((gx + 6, gy), "G", fill="#60F0A0")
                for manifest, ts, _ in selected:
                    color = (
                        "#60F0A0"
                        if manifest["outcome"] == "success"
                        else "#FFB454"
                        if not manifest["outcome"].startswith("invalid_")
                        else "#FF6677"
                    )
                    coords = [project(xy(t), offset) for t in ts]
                    if kind == "trajectory" and len(coords) > 1:
                        draw.line(coords, fill=color, width=2)
                    if coords:
                        x, y = coords[0]
                        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#2979FF")
                draw.text(
                    (offset + 20, 570),
                    "North up | blue=start | walls=initial geometry",
                    fill="#CBD5E1",
                )
                draw.text(
                    (offset + 20, 590),
                    "Green=success; amber=other; red=invalid"
                    if kind == "trajectory"
                    else f"Shared color scale: 1 to {peak} sampled endpoints/cell",
                    fill="#CBD5E1",
                )
            draw.text(
                (18, 625),
                "Endpoint chords may cross walls; cells are sampled visits, not continuous coverage or dwell time. Panels share scale.",
                fill="#CBD5E1",
            )
            path = output / f"{scenario}_{kind}.png"
            image.save(path)
            written.append(str(path))
    return written
