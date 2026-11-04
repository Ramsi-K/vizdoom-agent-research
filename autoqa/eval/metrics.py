"""Reproducible episode metrics from recorded artifacts, without model calls."""

import json
import math
from pathlib import Path
from types import SimpleNamespace

from autoqa.doom.topology import WalkableTopology
from autoqa.io import digest

GAMEPLAY_OUTCOMES = {
    "success",
    "timeout",
    "max_decisions",
    "player_dead",
    "gave_up",
    "blocked",
    "budget_exhausted",
}
METRICS = (
    "completed",
    "progress_fraction",
    "spl",
    "coverage_fraction",
    "revisit_rate",
    "collision_rate",
    "locate_hit_rate",
    "use_precision",
    "cost_usd",
    "tics_used",
    "unique_cells_visited",
    "stuck_warning_count",
    "visual_cycle_warning_count",
    "locate_calls",
    "cache_hit_rate",
    "sampled_path_units",
)


def read_json(path):
    return json.loads(Path(path).read_text(), parse_constant=lambda v: _bad(v))


def _bad(value):
    raise ValueError(f"Non-finite JSON value: {value}")


def read_lines(path):
    return [
        json.loads(s, parse_constant=lambda v: _bad(v))
        for s in Path(path).read_text().splitlines()
        if s.strip()
    ]


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def xy(row):
    return row["pos_x"], row["pos_y"]


def cell(row):
    return tuple(math.floor(v / 128) for v in xy(row))


def topology_from_geometry(geometry, goal):
    sectors = [
        SimpleNamespace(
            floor_height=s["floor"],
            ceiling_height=s["ceiling"],
            lines=[
                SimpleNamespace(
                    x1=line["x1"],
                    y1=line["y1"],
                    x2=line["x2"],
                    y2=line["y2"],
                    is_blocking=line["blocking"],
                )
                for line in s["lines"]
            ],
        )
        for s in geometry.get("sectors", [])
    ]
    return WalkableTopology(sectors, goal)


def episode_metrics(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    for name in ("telemetry.jsonl", "events.jsonl"):
        expected = manifest.get(name.replace(".", "_") + "_sha256")
        if expected and digest(directory / name) != expected:
            raise ValueError(f"Artifact hash mismatch: {name}")
    telemetry = read_lines(directory / "telemetry.jsonl")
    events = read_lines(directory / "events.jsonl")
    initial = next((t for t in telemetry if t.get("event") == "init"), None)
    primitives = [t for t in telemetry if t.get("event") == "primitive"]
    points = ([initial] if initial else []) + primitives
    positions_valid = initial is not None and all(
        number(t.get(k)) for t in points for k in ("pos_x", "pos_y")
    )
    entries = []
    if positions_valid:
        for row in points:
            if not entries or cell(row) != entries[-1]:
                entries.append(cell(row))
    seen = set(entries)
    path = (
        sum(math.dist(xy(a), xy(b)) for a, b in zip(points, points[1:]))
        if positions_valid
        else None
    )
    reasons = {}
    outcome = manifest["outcome"]
    eligible = outcome in GAMEPLAY_OUTCOMES and not manifest.get("is_fault_probe", False)
    complete = int(outcome == "success")
    bfs = [t.get("goal_dist_bfs") for t in points]
    bfs_valid = initial is not None and all(
        t.get("goal_dist_bfs_status") == "measured_coarse_grid" and number(d) and d >= 0
        for t, d in zip(points, bfs, strict=True)
    )
    progress = 1.0 if complete else None
    spl = None
    if bfs_valid and bfs[0] > 0:
        progress = 1.0 if complete else max(0.0, min(1.0, (bfs[0] - min(bfs)) / bfs[0]))
        if path is not None:
            spl = complete * bfs[0] / max(bfs[0], path)
    else:
        if not complete:
            reasons["progress_fraction"] = "No positive initial, consistently measured BFS distance"
        reasons["spl"] = "No positive initial, consistently measured BFS distance"
    coverage, accessible = None, None
    geometry_path = directory / "geometry.json"
    if positions_valid and geometry_path.exists():
        graph = topology_from_geometry(read_json(geometry_path), xy(initial))
        reachable = set(graph.distances)
        if reachable and seen <= reachable:
            accessible = len(reachable)
            coverage = len(seen) / accessible
    if coverage is None:
        reasons["coverage_fraction"] = (
            "Initial coarse graph unavailable or does not resolve all sampled cells"
        )
    translations = [
        t
        for t in primitives
        if number(t.get("expected_free_displacement")) and t["expected_free_displacement"] > 0
    ]
    primitive_events = {
        (e["decision_idx"], e["primitive_idx"]): e for e in events if e.get("event") == "primitive"
    }
    uses = [
        t
        for t in primitives
        if primitive_events.get((t["decision_idx"], t["primitive_idx"]), {})
        .get("requested", {})
        .get("type")
        == "use"
    ]
    locate_results = [
        e for e in events if e.get("event") in {"locate", "locate_reused", "grounding_error"}
    ]
    calls = manifest.get("locate_calls", len(locate_results))
    # Reuse events carry no masks. Recover their previously delivered same-decision result.
    previous = {}
    positives = 0
    for event in locate_results:
        key = (event.get("decision_idx"), event.get("concept"))
        if event["event"] == "locate":
            previous[key] = any(
                number(m.get("confidence")) and m["confidence"] >= 0.30
                for m in event.get("matches", [])
            )
        positives += (
            bool(previous.get(key))
            if event["event"] == "locate_reused"
            else bool(event["event"] == "locate" and previous[key])
        )
    if len(locate_results) != calls:
        locate_rate = None
        reasons["locate_hit_rate"] = "Incomplete locate result log"
    else:
        locate_rate = ratio(positives, calls)
    tics = manifest.get("tics_used")
    tics_source = "manifest"
    if not number(tics) or tics < 0:
        tics = None
        tics_source = None
        replay_path = directory / "replay.json"
        if replay_path.exists():
            replay = read_json(replay_path)
            if (
                manifest.get("episode_lmp_sha256")
                and replay.get("lmp_sha256") == manifest["episode_lmp_sha256"]
                and replay.get("matches_telemetry")
                and (directory / "episode.lmp").exists()
                and digest(directory / "episode.lmp") == manifest["episode_lmp_sha256"]
            ):
                value = replay.get("reconstructed_tics_used")
                start = manifest.get("start_tic", initial.get("tic") if initial else None)
                full = number(start) and replay.get("from_tic", start) == start
                end = start + value if number(start) and number(value) else None
                if number(value) and value >= 0 and full and replay.get("to_tic", end) == end:
                    tics, tics_source = value, "native_replay_reconstruction"
        if tics is None:
            reasons["tics_used"] = (
                "Raw clock unavailable/reset; verified native replay reconstruction required"
            )
    usage = manifest.get("usage") or {}
    cost = manifest.get("total_usd")
    if not number(cost) or cost < 0:
        cost = None
        reasons["cost_usd"] = "No recorded usage cost"
    metrics = dict(
        schema_version=1,
        run_id=manifest["run_id"],
        scenario_id=manifest["scenario_id"],
        level=manifest["level"],
        seed=manifest["seed"],
        outcome=outcome,
        included_in_gameplay=eligible,
        exclusion_reason=None if eligible else "Invalid/incomplete outcome or fault probe",
        completed=complete,
        progress_fraction=progress,
        spl=spl,
        coverage_fraction=coverage,
        unique_cells_visited=len(seen) if positions_valid else None,
        total_accessible_cells=accessible,
        cell_entries=len(entries) if positions_valid else None,
        cell_reentries=len(entries) - len(seen) if positions_valid else None,
        revisit_rate=(1 - len(seen) / len(entries)) if entries else None,
        sampled_path_units=path,
        tics_used=tics,
        tics_source=tics_source,
        decisions_used=manifest.get("decisions"),
        collision_rate=ratio(
            sum(
                t["displacement_units"] < 0.20 * t["expected_free_displacement"]
                for t in translations
            ),
            len(translations),
        ),
        translation_primitives=len(translations),
        use_precision=ratio(sum(bool(t.get("door_or_switch_activated")) for t in uses), len(uses)),
        use_primitives=len(uses),
        stuck_warning_count=sum(e.get("event") == "stuck_warning" for e in events),
        visual_cycle_warning_count=sum(e.get("event") == "visual_cycle_warning" for e in events),
        locate_calls=calls,
        locate_hit_rate=locate_rate,
        locate_candidate_positive_calls=positives,
        locate_bearing_mae_deg=None,
        issues_reported_inline=manifest.get("inline_issues"),
        issues_reviewer_post=None,
        behavior_confirmed_count=None,
        behavior_confirmation_rate=None,
        cost_usd=cost,
        cache_hit_rate=ratio(usage.get("cached_tokens", 0), usage.get("input_tokens", 0)),
        api_error_count=sum(e.get("event") == "api_error" for e in events),
        grounding_error_count=sum(e.get("event") == "grounding_error" for e in events),
        unknown_failed_request_charges_possible=any(
            e.get("event") in {"api_error", "grounding_error"} for e in events
        ),
        goal_visible_anywhere=any((t.get("goal_visible_pixels") or 0) > 0 for t in telemetry),
        navigation_updates=sum(e.get("event") == "navigation_update" for e in events),
        navigation_actions=sum(e.get("event") == "navigation_action" for e in events),
        navigation_planning_verified=False,
        unavailable=reasons,
        definitions={
            "path": "Sum of primitive endpoint chords; not continuous distance",
            "coverage": "Visited endpoint cells / spawn-connected 128-unit cell-center graph; approximate",
            "revisit": "1 - unique entered cells / entries, including the initial cell; consecutive duplicates removed",
            "collision": "Displacement under 20% of M0 acceleration-sensitive proxy; not exact collision events",
            "locate_hit_rate": "Candidate-positive tool returns, not semantic IoU accuracy",
            "use_precision": "Observed sector-height changes per use; proxy, not exact switch success",
            "cost": "Returned-usage estimate, including grounding; unknown failed-request charges excluded",
            "qa": "Reviewer/trace verification not run (M4); bearing accuracy unavailable without target matching",
        },
        input_sha256={
            name: digest(directory / name)
            for name in (
                "manifest.json",
                "telemetry.jsonl",
                "events.jsonl",
                "geometry.json",
                "replay.json",
            )
            if (directory / name).exists()
        },
    )
    if not positions_valid:
        reasons["positions"] = "Missing/non-finite initial or primitive positions"
    return metrics
