"""Recover an interrupted M3.5 prefix offline from logged inputs, never a policy.

Creates separately labelled artifacts; original files remain untouched. Every
saved observation JPEG and recorded primitive telemetry sample must match.
No model client is imported, and no unrecorded gameplay input is allowed.
"""

import argparse
import hashlib
import math
import shutil
from collections import defaultdict
from pathlib import Path

import vizdoom as vzd

from autoqa.config import RunConfig
from autoqa.doom.demo_path import native_demo_path
from autoqa.doom.env import configured_game, jpeg
from autoqa.eval.metrics import read_json, read_lines
from autoqa.io import digest, write_json

VARIABLES = {
    "pos_x": "POSITION_X",
    "pos_y": "POSITION_Y",
    "pos_z": "POSITION_Z",
    "angle_deg": "ANGLE",
    "pitch": "PITCH",
    "camera_fov": "CAMERA_FOV",
    "health": "HEALTH",
    "armor": "ARMOR",
    "kills": "KILLCOUNT",
    "items": "ITEMCOUNT",
    "hits": "HITCOUNT",
    "damage_dealt": "DAMAGECOUNT",
    "damage_taken": "DAMAGE_TAKEN",
    "selected_weapon": "SELECTED_WEAPON",
    "selected_weapon_ammo": "SELECTED_WEAPON_AMMO",
    **{f"ammo_{i}": f"AMMO{i}" for i in range(10)},
    **{f"weapon_{i}": f"WEAPON{i}" for i in range(10)},
}


def check_telemetry(game, expected):
    if game.get_episode_time() != expected["tic"]:
        raise ValueError("Reconstructed clock differs from original telemetry")
    for key, variable in VARIABLES.items():
        value = game.get_game_variable(getattr(vzd.GameVariable, variable))
        if not math.isclose(value, expected[key], abs_tol=1e-8):
            raise ValueError(f"Reconstruction differs at {key}, tic {expected['tic']}")
    state = game.get_state()
    if state and expected.get("sector_heights") is not None:
        heights = [[s.floor_height, s.ceiling_height] for s in state.sectors]
        if heights != expected["sector_heights"]:
            raise ValueError("Reconstructed sector heights differ")
    if not math.isclose(game.get_total_reward(), expected["reward_total"], abs_tol=1e-8):
        raise ValueError("Reconstructed reward differs")


def recover(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    original = read_json(source / "manifest.json")
    if original["outcome"] != "running" or (source / "episode.lmp").exists():
        raise ValueError("Recovery is only for an unfinalized run missing its native demo")
    if original["vizdoom_version"] != vzd.__version__:
        raise ValueError("Use the original ViZDoom version")
    for name, sha in original["source_sha256"].items():
        if digest(Path(name)) != sha:
            raise ValueError(f"Original runtime source changed: {name}")
    for name, key in (("iwad_path", "iwad_sha256"), ("scenario_cfg_path", "scenario_cfg_sha256")):
        if digest(Path(original[name])) != original[key]:
            raise ValueError("Original engine asset changed")
    events = read_lines(source / "events.jsonl")
    telemetry = read_lines(source / "telemetry.jsonl")
    source_hashes = {
        name: digest(source / name) for name in ("manifest.json", "events.jsonl", "telemetry.jsonl")
    }
    primitives = defaultdict(list)
    calls = defaultdict(list)
    for event in events:
        if event["event"] == "primitive":
            primitives[event["decision_idx"]].append(event)
        if event["event"] == "tool_call" and event.get("name") == "act":
            calls[event["decision_idx"]].append(event["arguments"])
    samples = {
        (row["decision_idx"], row["primitive_idx"]): row
        for row in telemetry
        if row["event"] == "primitive"
    }
    frames = sorted((source / "frames").glob("step_*.jpg"))
    if not frames or not samples:
        raise ValueError("Saved observations and executed primitive telemetry required")
    output.mkdir(parents=True, exist_ok=False)
    for name in ("events.jsonl", "telemetry.jsonl", "geometry.json"):
        shutil.copy2(source / name, output / name)
    (output / "frames").symlink_to(source / "frames", target_is_directory=True)
    (output / "grounding").symlink_to(source / "grounding", target_is_directory=True)
    config = RunConfig.model_validate(original["config"])
    game = configured_game(config)
    matched, inputs = [], 0
    try:
        game.init()
        game.set_seed(config.seed)
        with native_demo_path(output / "episode.lmp") as demo:
            game.new_episode(demo)
            check_telemetry(game, telemetry[0])
            for frame in frames:
                decision = int(frame.stem.split("_")[-1])
                state = game.get_state()
                if state is None or hashlib.sha256(jpeg(state.screen_buffer)).hexdigest() != digest(
                    frame
                ):
                    raise ValueError(f"Reconstructed observation differs at decision {decision}")
                matched.append((frame.name, digest(frame)))
                for event in primitives[decision]:
                    if not any(
                        len(call["moves"]) > event["primitive_idx"]
                        and call["moves"][event["primitive_idx"]] == event["requested"]
                        and call["run"] == event["run"]
                        for call in calls[decision]
                    ):
                        raise ValueError("Recorded input has no matching original model act")
                    if game.get_episode_time() != event["tic_start"]:
                        raise ValueError("Recorded input timing differs")
                    # Exact logged inputs only. Never choose, repair or add an action.
                    game.make_action(event["button_vector"], event["requested_tics"])
                    check_telemetry(game, samples[(decision, event["primitive_idx"])])
                    inputs += 1
            check_telemetry(game, telemetry[-1])
            game.new_episode()  # flush only; no further recorded inputs
    finally:
        game.close()
    for name, sha in source_hashes.items():
        if digest(source / name) != sha:
            raise ValueError("Original source artifacts changed during recovery")
    known = sum(
        e["cost_usd"] for e in events if e["event"] in {"model_response", "grounding_request"}
    )
    final = telemetry[-1]
    manifest = dict(original)
    manifest.update(
        run_id=output.name,
        original_run_id=source.name,
        outcome="interrupted_sigterm",
        recording_provenance="offline_reconstructed_prefix_from_exact_logged_inputs",
        original_recording_available=False,
        original_source_directory=str(source),
        original_source_sha256=source_hashes,
        total_usd=known,
        usage_status="known_completed_usage_only; failed/unlogged in-flight charges unknown",
        decisions=len(frames),
        act_calls=sum(e["event"] == "action_feedback" for e in events),
        model_calls=sum(e["event"] == "model_response" for e in events),
        locate_calls=sum(e["event"] == "tool_call" and e.get("name") == "locate" for e in events),
        grounding_model_calls=sum(e["event"] == "grounding_request" for e in events),
        grounding_cost_usd=sum(e["cost_usd"] for e in events if e["event"] == "grounding_request"),
        final_tic=final["tic"],
        start_tic=telemetry[0]["tic"],
        tics_used=final["tic"] - telemetry[0]["tic"],
        reward_total=final["reward_total"],
        spawn_state={
            key: telemetry[0][key] for key in ("pos_x", "pos_y", "pos_z", "angle_deg", "tic")
        },
        combat_final={
            key: final[key]
            for key in (
                "kills",
                "items",
                "hits",
                "damage_dealt",
                "damage_taken",
                "health",
                "armor",
                "selected_weapon",
                "selected_weapon_ammo",
            )
        },
        missing_original_final_memory=True,
    )
    for name in ("episode.lmp", "events.jsonl", "telemetry.jsonl"):
        manifest[name.replace(".", "_") + "_sha256"] = digest(output / name)
    verification = dict(
        original_source_sha256=source_hashes,
        matched_observation_frames=len(matched),
        matched_primitive_samples=inputs,
        telemetry_variables_checked=list(VARIABLES),
        sector_heights_and_reward_match=True,
        all_logged_inputs_match_model_requests=True,
        ordered_original_frame_index_sha256=hashlib.sha256(str(matched).encode()).hexdigest(),
        native_recording_sha256=manifest["episode_lmp_sha256"],
        recovery_script_sha256=digest(Path(__file__)),
        limitations=[
            "Original native recording was never finalized; this is a reconstruction",
            "No evidence for any gameplay after last recorded action",
            "Final model request and failed grounding charges may be unknown",
        ],
    )
    write_json(output / "manifest.json", manifest)
    write_json(output / "reconstruction.json", verification)
    print(
        dict(directory=str(output), frames=len(matched), primitives=inputs, known_usd=known),
        flush=True,
    )
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    recover(args.source, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
