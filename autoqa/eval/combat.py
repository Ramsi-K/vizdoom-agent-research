"""M3.5 evidence summaries only; no gameplay policy or QA verdict generation."""

from collections import Counter
from pathlib import Path

from autoqa.eval.metrics import read_json, read_lines
from autoqa.io import digest, write_json


def combat_evidence(directory):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    events = read_lines(directory / "events.jsonl")
    telemetry = read_lines(directory / "telemetry.jsonl")
    for name in ("episode.lmp", "events.jsonl", "telemetry.jsonl"):
        if digest(directory / name) != manifest[name.replace(".", "_") + "_sha256"]:
            raise ValueError(f"Changed source artifact: {name}")
    requested = {}
    for event in events:
        if event["event"] == "tool_call" and event["name"] == "act":
            requested.setdefault(event["decision_idx"], []).append(event["arguments"])
    primitives = [e for e in events if e["event"] == "primitive"]
    matches = []
    for event in primitives:
        choices = requested.get(event["decision_idx"], [])
        matches.append(
            any(
                len(a["moves"]) > event["primitive_idx"]
                and a["moves"][event["primitive_idx"]] == event["requested"]
                and a["run"] == event["run"]
                for a in choices
            )
        )
    replay_path = directory / "replay.json"
    replay = read_json(replay_path) if replay_path.exists() else None
    if replay and (
        replay["lmp_sha256"] != manifest["episode_lmp_sha256"] or not replay["matches_telemetry"]
    ):
        raise ValueError("Replay was not matched to this recording")
    buttons = manifest["button_order"]
    firing = [e for e in primitives if e["button_vector"][buttons.index("ATTACK")]]
    model_notes = sum(e["event"] == "landmark_ledger" for e in events)
    return dict(
        run_id=manifest["run_id"],
        scenario=manifest["scenario_id"],
        level=manifest["level"],
        seed=manifest["seed"],
        model=manifest["model"],
        effort=manifest["effort"],
        config=manifest["config"],
        outcome=manifest["outcome"],
        decisions=manifest.get("decisions"),
        executed_acts=manifest.get("act_calls"),
        combat_final=manifest.get("combat_final"),
        peak_observed_kills=max((r.get("kills", 0) for r in telemetry), default=0),
        peak_observed_damage_dealt=max((r.get("damage_dealt", 0) for r in telemetry), default=0),
        primitives=len(primitives),
        model_matched_primitives=sum(matches),
        all_primitives_match_model=all(matches),
        firing_primitives=len(firing),
        requested_firing_tics=sum(e["requested_tics"] for e in firing),
        compound_firing_primitives=sum(
            e["requested"]["type"] not in ("attack", "altattack") for e in firing
        ),
        primitive_counts=dict(Counter(e["requested"]["type"] for e in primitives)),
        ledger_updates=model_notes,
        navigation_updates=sum(e["event"] == "navigation_update" for e in events),
        locate_calls=manifest.get("locate_calls"),
        grounding_model_calls=manifest.get("grounding_model_calls"),
        grounding_cost_usd=manifest.get("grounding_cost_usd"),
        recorded_cost_usd=manifest["total_usd"],
        api_errors=[
            {k: e.get(k) for k in ("attempt", "http_status", "error_type", "retry")}
            for e in events
            if e["event"] == "api_error"
        ],
        grounding_errors=sum(e["event"] == "grounding_error" for e in events),
        rejected_tool_batches=sum(e["event"] == "rejected_tool_calls" for e in events),
        navigation_rejections=sum(e["event"] == "navigation_rejected" for e in events),
        replay_verified=bool(replay and replay["matches_telemetry"]),
        gameplay_frames=replay.get("gameplay_frames", replay["frames"]) if replay else None,
        played_tics=replay.get("reconstructed_tics_used", manifest.get("tics_used"))
        if replay
        else manifest.get("tics_used"),
        video_sha256=replay.get("video_sha256") if replay else None,
        artifact_sha256={
            name: digest(directory / name)
            for name in (
                "manifest.json",
                "events.jsonl",
                "telemetry.jsonl",
                "episode.lmp",
                "replay.json",
                "replay.mp4",
                "memory.json",
            )
            if (directory / name).exists()
        },
        limits=[
            "Requested firing tics may include an unexecuted tail at natural termination; not a shot count",
            "SAM candidates do not verify identity; absence of locate calls is not a grounding failure",
            "Counts are recorded engine events, not QA verdicts or reliable performance estimates",
            "Recorded spending excludes unknown charges for failed or interrupted requests",
        ],
    )


def write_combat_summary(directories, output):
    output = Path(output)
    rows = [combat_evidence(p) for p in directories]
    result = dict(
        kind="M3.5 exploratory combat; separate from M3 benchmark",
        episodes=rows,
        recorded_cost_usd=sum(r["recorded_cost_usd"] for r in rows),
        unknown_failed_request_charges_possible=any(
            r["api_errors"] or r["grounding_errors"] for r in rows
        ),
    )
    write_json(output, result)
    return result
