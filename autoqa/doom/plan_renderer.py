"""Frozen initial L3 map: accepts an eval snapshot and never reads a live game."""

import io
import math

from PIL import Image, ImageDraw

from .topology import inside

BACKGROUND = "#12161F"
WALL = "#F0F4F8"
SPAWN = "#2979FF"
GOAL = "#00E676"


def render_plan(geometry, spawn, *, scenario="my_way_home", annotations=None):
    sectors = geometry["sectors"]
    lines = [line for sector in sectors for line in sector["lines"]]
    if not lines:
        raise ValueError("No initial geometry available for L3")
    xs = [line[key] for line in lines for key in ("x1", "x2")]
    ys = [line[key] for line in lines for key in ("y1", "y2")]
    # Leave room for a legend without distorting the map's uniform projection.
    scale = min(576 / max(1, max(xs) - min(xs)), 350 / max(1, max(ys) - min(ys)))
    xmid, ymid = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    def project(x, y):
        return (round(320 + (x - xmid) * scale), round(210 - (y - ymid) * scale))

    image = Image.new("RGB", (640, 480), BACKGROUND)
    draw = ImageDraw.Draw(image)
    owners = {}
    for sector in sectors:
        for line in sector["lines"]:
            edge = tuple(sorted(((line["x1"], line["y1"]), (line["x2"], line["y2"]))))
            owners.setdefault(edge, []).append((sector["floor"], sector["ceiling"]))
    for line in lines:
        p, q = project(line["x1"], line["y1"]), project(line["x2"], line["y2"])
        edge = tuple(sorted(((line["x1"], line["y1"]), (line["x2"], line["y2"]))))
        heights = owners[edge]
        threshold = any(floor == ceiling for floor, ceiling in heights) or len(set(heights)) > 1
        if line["blocking"] and not (threshold and len(heights) > 1):
            draw.line((p, q), fill=WALL, width=2)
        elif threshold:
            distance = math.dist(p, q)
            for start in range(0, max(1, round(distance)), 8):
                t0, t1 = start / max(1, distance), min(start + 4, distance) / max(1, distance)
                draw.line(
                    (
                        (p[0] + (q[0] - p[0]) * t0, p[1] + (q[1] - p[1]) * t0),
                        (p[0] + (q[0] - p[0]) * t1, p[1] + (q[1] - p[1]) * t1),
                    ),
                    fill="#00E5FF",
                    width=2,
                )
    x, y = project(spawn["pos_x"], spawn["pos_y"])
    angle = math.radians(spawn["angle_deg"])
    triangle = [
        (x + 10 * math.cos(angle + a), y - 10 * math.sin(angle + a)) for a in (0, 2.5, -2.5)
    ]
    draw.polygon(triangle, fill=SPAWN)
    draw.text((x + 10, y + 6), "S (Start)", fill=SPAWN)
    goals = []
    if scenario in {"my_way_home", "deadly_corridor_nav", "deadly_corridor_combat"}:
        goals = [obj for obj in geometry["objects"] if obj["name"] == "GreenArmor"]
    for obj in goals:
        gx, gy = project(obj["x"], obj["y"])
        draw.ellipse((gx - 6, gy - 6, gx + 6, gy + 6), fill=GOAL)
        draw.text((gx + 9, gy - 6), "G (Goal)", fill=GOAL)
    adjacency = [set() for _ in sectors]
    by_edge = {}
    for index, sector in enumerate(sectors):
        for line in sector["lines"]:
            edge = tuple(sorted(((line["x1"], line["y1"]), (line["x2"], line["y2"]))))
            by_edge.setdefault(edge, []).append((index, line["blocking"]))
    for entries in by_edge.values():
        if len(entries) == 2 and not any(blocking for _, blocking in entries):
            a, b = [index for index, _ in entries]
            adjacency[a].add(b)
            adjacency[b].add(a)
    for index, neighbors in enumerate(adjacency):
        if len(neighbors) != 1:
            continue
        polygon = [
            ((line["x1"], line["y1"]), (line["x2"], line["y2"])) for line in sectors[index]["lines"]
        ]
        if inside((spawn["pos_x"], spawn["pos_y"]), polygon) or any(
            inside((g["x"], g["y"]), polygon) for g in goals
        ):
            continue
        vertices = [point for edge in polygon for point in edge]
        center = (
            sum(p[0] for p in vertices) / len(vertices),
            sum(p[1] for p in vertices) / len(vertices),
        )
        if inside(center, polygon):
            dx, dy = project(*center)
            draw.text((dx, dy), "X", fill="#FFAB00")
    for tag in annotations or []:
        point = project(tag["x"], tag["y"])
        draw.text(point, tag["label"], fill="#FFAB00")
    draw.line((600, 70, 600, 35), fill=WALL, width=2)
    draw.polygon(((600, 28), (595, 39), (605, 39)), fill=WALL)
    draw.text((595, 14), "N", fill=WALL)
    # Choose a readable map-unit bar; label the actual scale exactly.
    bar = 256 if 256 * scale <= 250 else 128
    draw.line((32, 420, 32 + bar * scale, 420), fill=WALL, width=3)
    draw.text((32, 430), f"{bar} map units", fill=WALL)
    draw.text((250, 410), "Frozen initial plan; no live tracking", fill=WALL)
    draw.text((250, 428), "S start | G goal | cyan threshold", fill=WALL)
    draw.text((250, 464), "X terminal branch (static topology)", fill="#FFAB00")
    if not goals:
        draw.text((250, 446), "Goal position not annotated", fill="#FFAB00")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
