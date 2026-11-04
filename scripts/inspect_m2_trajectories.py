"""Offline inspection of the three 60-decision M2 recordings; never used by the Pilot."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def xy(r):
    return (r["pos_x"], r["pos_y"])


def cell(r, size):
    return (math.floor(r["pos_x"] / size), math.floor(r["pos_y"] / size))


def segmentdist(p, a, b):
    d = (b[0] - a[0], b[1] - a[1])
    v = d[0] ** 2 + d[1] ** 2
    t = max(0, min(1, ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / v)) if v else 0
    return math.dist(p, (a[0] + t * d[0], a[1] + t * d[1]))


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("run_directories", nargs=3, type=Path)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
rows = []
for directory in args.run_directories:
    p = Path(directory)
    m = json.load(open(p / "manifest.json"))
    if m["scenario_id"] != "my_way_home" or m["decisions"] != 60 or m["seed"] != 101:
        raise ValueError("This M2 inspection requires seed-101 my_way_home runs of 60 decisions")
    ts = [json.loads(s) for s in open(p / "telemetry.jsonl")]
    es = [json.loads(s) for s in open(p / "events.jsonl")]
    geo = json.load(open(p / "geometry.json"))
    walls = [
        ((line["x1"], line["y1"]), (line["x2"], line["y2"]))
        for s in geo["sectors"]
        for line in s["lines"]
        if line["blocking"]
    ]
    ps = [t for t in ts if t["event"] == "primitive"]
    ds = [t for t in ts if t["event"] == "decision"]
    actions = {
        e["decision_idx"]: e["arguments"]
        for e in es
        if e["event"] == "tool_call" and e["name"] == "act"
    }
    moves = {
        (e["decision_idx"], e["primitive_idx"]): e["requested"]
        for e in es
        if e["event"] == "primitive"
    }
    translations = [
        t
        for t in ps
        if moves[t["decision_idx"], t["primitive_idx"]]["type"]
        in {"forward", "backward", "strafe_left", "strafe_right"}
    ]
    seen128 = {cell(ts[0], 128)}
    seen64 = {cell(ts[0], 64)}
    new128 = []
    new64 = []
    for t in ps:
        for size, seen, new in [(128, seen128, new128), (64, seen64, new64)]:
            c = cell(t, size)
            if c not in seen:
                new.append(dict(decision=t["decision_idx"], cell=list(c)))
                seen.add(c)
    entries = []
    for t in [ts[0]] + ps:
        c = cell(t, 128)
        if not entries or c != entries[-1]:
            entries.append(c)
    recurrence = []
    for t in ds:
        if any(
            math.dist(xy(t), xy(old)) <= 24
            and abs((t["angle_deg"] - old["angle_deg"] + 180) % 360 - 180) <= 15
            for old in ds
            if old["decision_idx"] <= t["decision_idx"] - 6
        ):
            recurrence.append(t["decision_idx"])
    turnerrors = []
    prev = ts[0]
    for t in ps:
        move = moves[t["decision_idx"], t["primitive_idx"]]
        if move["type"] in ("turn_left", "turn_right"):
            desired = move["amount"] * (1 if move["type"] == "turn_left" else -1)
            actual = (t["angle_deg"] - prev["angle_deg"] + 180) % 360 - 180
            turnerrors.append(abs((actual - desired + 180) % 360 - 180))
        prev = t
    windows = []
    for start, end in [(0, 19), (20, 39), (40, 59)]:
        pts = [t for t in ps if start <= t["decision_idx"] <= end]
        tr = [t for t in translations if start <= t["decision_idx"] <= end]
        windows.append(
            dict(
                decisions=[start, end],
                sampled_path_units=sum(t["displacement_units"] for t in pts),
                minimum_goal_distance=min(t["goal_dist_euclidean"] for t in pts),
                end_goal_distance=pts[-1]["goal_dist_euclidean"],
                new_128_cells=sum(start <= r["decision"] <= end for r in new128),
                new_64_cells=sum(start <= r["decision"] <= end for r in new64),
                translations=len(tr),
                translation_endpoints_under_1_unit=sum(t["displacement_units"] < 1 for t in tr),
                translation_collision_proxy=sum(t["collision_substep"] for t in tr),
            )
        )
    counts = Counter(moves[t["decision_idx"], t["primitive_idx"]]["type"] for t in ps)
    low = [t for t in translations if t["displacement_units"] < 1]
    row = dict(
        level=m["level"],
        directory=str(directory),
        start_goal_distance=ts[0]["goal_dist_euclidean"],
        minimum_goal_distance=min(t["goal_dist_euclidean"] for t in ts),
        minimum_goal_at_decision=min(ps, key=lambda t: t["goal_dist_euclidean"])["decision_idx"],
        final_goal_distance=ts[-1]["goal_dist_euclidean"],
        goal_visible_anywhere=any(t["goal_visible_pixels"] for t in ts),
        sampled_path_units=sum(t["displacement_units"] for t in ps),
        net_displacement_units=math.dist(xy(ts[0]), xy(ts[-1])),
        visited_sector_ids=sorted(set(t["sector_id"] for t in ts if t["sector_id"] is not None)),
        unique_128_cells=len(seen128),
        unique_64_cells=len(seen64),
        cell_128_entries=len(entries),
        cell_128_reentries=len(entries) - len(set(entries)),
        new_128_cells=new128,
        new_64_cells=new64,
        pose_recurrences=recurrence,
        translations=len(translations),
        translation_endpoints_under_1_unit=len(low),
        low_translation_endpoints_within_17_units_of_static_wall=sum(
            min(segmentdist(xy(t), a, b) for a, b in walls) <= 17 for t in low
        ),
        translation_collision_proxy=sum(t["collision_substep"] for t in translations),
        translation_endpoints_within_17_units_of_static_wall=sum(
            min(segmentdist(xy(t), a, b) for a, b in walls) <= 17 for t in translations
        ),
        last20_translation_endpoints_within_17_units_of_static_wall=sum(
            t["decision_idx"] >= 40 and min(segmentdist(xy(t), a, b) for a, b in walls) <= 17
            for t in translations
        ),
        primitive_counts=dict(counts),
        half_turns=sum(
            move["type"].startswith("turn_") and move["amount"] == 180 for move in moves.values()
        ),
        turn_max_control_error_deg=max(turnerrors),
        use_changes_sector_heights=sum(t["door_or_switch_activated"] for t in ps),
        stuck_warnings=sum(e["event"] == "stuck_warning" for e in es),
        frame_change_under_2pct=sum(
            e["event"] == "action_feedback" and e["frame_change_pct"] < 2 for e in es
        ),
        windows=windows,
        decision_trace=[
            dict(
                decision=t["decision_idx"],
                tic=t["tic"],
                x=t["pos_x"],
                y=t["pos_y"],
                yaw=t["angle_deg"],
                goal_distance=t["goal_dist_euclidean"],
                action=actions[t["decision_idx"]],
            )
            for t in ds
        ],
    )
    rows.append(row)
    print(
        json.dumps(
            dict(
                level=m["level"],
                new_128_cells_last20=windows[-1]["new_128_cells"],
                new_64_cells_last20=windows[-1]["new_64_cells"],
                pose_recurrences=len(recurrence),
                goal_visible=row["goal_visible_anywhere"],
            )
        )
    )
report = dict(
    methods=dict(
        sampled_path="Sum of primitive endpoint displacements; lower bound on true travelled path, excludes within-primitive curvature.",
        collision_proxy="Existing M0 eighth-tic displacement proxy: translation under 20% expected free displacement; not a ground-truth collision detector.",
        near_zero_translation="Endpoint displacement under 1 map unit; static-wall proximity <=17 units corroborates contact for a radius-16 player, but does not classify engine collision events.",
        reentries="128-unit cell entries after removing consecutive duplicate cells, minus distinct entered cells, including initial cell.",
        pose_recurrence="Decision endpoint within 24 units and 15 degrees of an endpoint at least six decisions earlier.",
        new_cells="First-ever primitive endpoint in cell, relative to initial sample; not continuous path coverage.",
        goal_distance="Euclidean straight-line distance, ignores walls; coarse BFS unresolved and not used.",
        replay_inspection="Not performed by this script. See docs/m2-results.md for the separate manual sampled replay review.",
    ),
    episodes=rows,
)
args.out.write_text(json.dumps(report, indent=2) + "\n")
