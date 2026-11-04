"""Offline native-demo audit. Never imports a model client or sends live actions."""

import argparse
import hashlib
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np
import vizdoom as vzd
from PIL import Image

from autoqa.config import SCENARIOS, RunConfig
from autoqa.doom.demo_path import native_demo_path
from autoqa.doom.env import configured_game, jpeg
from autoqa.eval.combat import combat_evidence
from autoqa.eval.metrics import read_json, read_lines
from autoqa.io import digest, write_json

COUNTERS = {
    "kills": "KILLCOUNT",
    "hits": "HITCOUNT",
    "damage": "DAMAGECOUNT",
    "taken": "DAMAGE_TAKEN",
    "health": "HEALTH",
    "weapon": "SELECTED_WEAPON",
    "ammo": "SELECTED_WEAPON_AMMO",
}


def counters(game):
    return {k: game.get_game_variable(getattr(vzd.GameVariable, v)) for k, v in COUNTERS.items()}


def labels(state):
    return [
        dict(
            name=lab.object_name,
            category=lab.object_category,
            id=lab.object_id,
            value=lab.value,
            box=[lab.x, lab.y, lab.width, lab.height],
        )
        for lab in state.labels
    ]


def enemies(items):
    return [x for x in items if x["category"] in {"Monster", "Player"}]


def aim_gap(items, width=640, fov=90):
    """Horizontal distance to nearest enemy bbox edge; zero means x-overlap only."""
    focal = width / (2 * math.tan(math.radians(fov / 2)))
    distances = []
    for item in enemies(items):
        left, _, span, _ = item["box"]
        right = left + span - 1
        gap = max(left - width / 2, width / 2 - right, 0)
        distances.append(math.degrees(math.atan(gap / focal)))
    return min(distances) if distances else None


def aligned_mask(mask, label_buffer, items):
    scores = []
    for item in items:
        target = label_buffer == item["value"]
        intersection = int(np.count_nonzero(mask & target))
        union = int(np.count_nonzero(mask | target))
        if intersection:
            scores.append(dict(**item, iou=intersection / union))
    return max(scores, key=lambda x: x["iou"]) if scores else None


def decision_blocks(events, telemetry, span=60):
    """Attribute usage in event order; grounding charges inherit the invoking decision."""
    blocks = {}

    def block(decision):
        index = decision // span
        row = blocks.setdefault(
            index,
            dict(
                first_decision=index * span,
                last_decision=decision,
                recorded_usd=0.0,
                executed_acts=0,
                locate_calls=0,
                visual_cycle_warnings=0,
                new_sampled_cells=0,
                sampled_cell_reentries=0,
            ),
        )
        row["last_decision"] = max(row["last_decision"], decision)
        return row

    current = 0
    for event in events:
        current = event.get("decision_idx", current)
        row = block(current)
        if event["event"] in {"model_response", "grounding_request"}:
            row["recorded_usd"] += event["cost_usd"]
        for source, target in (
            ("action_feedback", "executed_acts"),
            ("locate", "locate_calls"),
            ("visual_cycle_warning", "visual_cycle_warnings"),
        ):
            row[target] += event["event"] == source
    seen, previous = set(), None
    for event in telemetry:
        if event["event"] not in {"init", "primitive"}:
            continue
        row = block(event["decision_idx"])
        cell = (event["cell_x"], event["cell_y"])
        row["new_sampled_cells"] += cell not in seen
        row["sampled_cell_reentries"] += cell in seen and cell != previous
        seen.add(cell)
        previous = cell
        row["last_kills"] = event["kills"]
        row["last_health"] = event["health"]
    return [blocks[index] for index in sorted(blocks)]


def endpoint_audit(directory, manifest, events):
    """Match the original render-update schedule before auditing SAM pixels."""
    config = RunConfig.model_validate(manifest["config"])
    game = configured_game(config)
    primitives = [x for x in events if x["event"] == "primitive"]
    receipts = [x for x in events if x["event"] == "locate"]
    by_decision = {}
    for receipt in receipts:
        by_decision.setdefault(receipt["decision_idx"], []).append(receipt)
    frames, masks = [], []
    try:
        game.init()
        with native_demo_path(directory / "episode.lmp") as demo:
            game.replay_episode(demo)
            for path in sorted((directory / "frames").glob("step_*.jpg")):
                decision = int(path.stem.split("_")[-1])
                state = game.get_state()
                if state is None:
                    raise ValueError(f"Missing native frame at decision {decision}")
                sha = hashlib.sha256(jpeg(state.screen_buffer)).hexdigest()
                if sha != digest(path):
                    raise ValueError(f"Original frame mismatch at decision {decision}")
                items = labels(state)
                frames.append(
                    dict(
                        decision=decision,
                        tic=game.get_episode_time(),
                        frame_sha256=sha,
                        exact_frame_match=True,
                        enemies=enemies(items),
                        horizontal_gap_deg=aim_gap(
                            items,
                            width=state.screen_buffer.shape[1],
                            fov=game.get_game_variable(vzd.GameVariable.CAMERA_FOV),
                        ),
                        counters=counters(game),
                    )
                )
                for receipt in by_decision.get(decision, []):
                    if receipt["frame_sha256"] != sha:
                        raise ValueError("Locate input does not match observed frame")
                    overlay = receipt.get("overlay")
                    if not overlay or not re.fullmatch(r"locate_\d+_overlay\.png", overlay):
                        raise ValueError("Missing or malformed grounding artifact reference")
                    prefix = overlay.removesuffix("_overlay.png")
                    paths = sorted((directory / "grounding").glob(f"{prefix}_mask*.png"))
                    if len(paths) != len(receipt["matches"]):
                        raise ValueError("Saved candidate masks differ from locate receipt")
                    for mask_path, candidate in zip(paths, receipt["matches"]):
                        mask = np.asarray(Image.open(mask_path).convert("L")) > 0
                        masks.append(
                            dict(
                                decision=decision,
                                tic=game.get_episode_time(),
                                concept=receipt["concept"],
                                file=mask_path.name,
                                mask_sha256=digest(mask_path),
                                frame_sha256=sha,
                                exact_frame_match=True,
                                pixels=int(mask.sum()),
                                candidate=candidate,
                                best_object=aligned_mask(mask, state.labels_buffer, items),
                                best_enemy=aligned_mask(mask, state.labels_buffer, enemies(items)),
                            )
                        )
                for primitive in primitives:
                    if primitive["decision_idx"] == decision:
                        game.advance_action(primitive["requested_tics"], True)
    finally:
        game.close()
    return frames, masks


def tic_audit(directory, manifest, replay_info):
    """Measure ammo decrements/hit events; rendering here is not used for SAM IoU."""
    config = RunConfig.model_validate(manifest["config"])
    game = configured_game(config)
    steps = replay_info.get("reconstructed_tics_used", manifest.get("tics_used"))
    if not isinstance(steps, int) or steps < 1:
        raise ValueError("Verified elapsed native tics are required")
    rows, category_counts, kill_events, damage_events = [], Counter(), [], []
    dry_attack_tics = 0
    attack_tics = 0
    try:
        game.init()
        with native_demo_path(directory / "episode.lmp") as demo:
            game.replay_episode(demo)
            start = game.get_episode_time()
            for elapsed in range(steps):
                before = counters(game)
                state = game.get_state()
                if state is None or game.is_episode_finished():
                    raise ValueError("Demo ended before verified recorded interval")
                items = labels(state)
                category_counts.update((x["name"], x["category"]) for x in items)
                gap = aim_gap(
                    items,
                    width=state.screen_buffer.shape[1],
                    fov=game.get_game_variable(vzd.GameVariable.CAMERA_FOV),
                )
                tic = game.get_episode_time()
                game.advance_action(1, True)
                after = counters(game)
                last = game.get_last_action()
                firing = bool(last[manifest["button_order"].index("ATTACK")])
                attack_tics += firing
                dry_attack_tics += firing and before["ammo"] == 0 and before["weapon"] == 2
                spent = before["ammo"] - after["ammo"] if before["weapon"] == after["weapon"] else 0
                if spent > 0:
                    rows.append(
                        dict(
                            tic=tic,
                            seconds=elapsed / 35,
                            weapon=before["weapon"],
                            ammo_spent=spent,
                            hits=after["hits"] - before["hits"],
                            damage=after["damage"] - before["damage"],
                            kills=after["kills"] - before["kills"],
                            visible_enemy_sprites=len(enemies(items)),
                            horizontal_gap_deg=gap,
                        )
                    )
                if after["kills"] > before["kills"]:
                    kill_events.append(
                        dict(tic=tic, seconds=elapsed / 35, gained=after["kills"] - before["kills"])
                    )
                if after["taken"] > before["taken"]:
                    damage_events.append(
                        dict(
                            tic=tic,
                            seconds=elapsed / 35,
                            damage=after["taken"] - before["taken"],
                            health=after["health"],
                        )
                    )
            final = counters(game)
            expected = manifest["combat_final"]
            for key, target in (
                ("kills", "kills"),
                ("hits", "hits"),
                ("damage", "damage_dealt"),
                ("taken", "damage_taken"),
                ("health", "health"),
                ("weapon", "selected_weapon"),
                ("ammo", "selected_weapon_ammo"),
            ):
                if not math.isclose(final[key], expected[target], abs_tol=1e-8):
                    raise ValueError(f"Offline per-tic audit differs from final {target}")
    finally:
        game.close()
    return dict(
        shots=rows,
        kill_events=kill_events,
        damage_taken_events=damage_events,
        attack_tics=attack_tics,
        dry_pistol_attack_tics=dry_attack_tics,
        label_categories=[
            dict(name=k[0], category=k[1], appearances=v)
            for k, v in sorted(category_counts.items())
        ],
        native_final_matches=True,
        start_tic=start,
    )


def audit(directory):
    directory = Path(directory)
    evidence = combat_evidence(directory)
    if (
        not evidence["replay_verified"]
        or digest(directory / "replay.mp4") != evidence["video_sha256"]
    ):
        raise ValueError("A verified unchanged full MP4 is required")
    manifest = read_json(directory / "manifest.json")
    if manifest["vizdoom_version"] != vzd.__version__:
        raise ValueError("Native audit requires the recorded ViZDoom version")
    profile = SCENARIOS[manifest["scenario_id"]]
    iwad = Path(vzd.__file__).parent / "freedoom2.wad"
    cfg = Path(vzd.scenarios_path) / f"{profile['cfg']}.cfg"
    wad = cfg.with_suffix(".wad")
    for path, key in (
        (iwad, "iwad_sha256"),
        (cfg, "scenario_cfg_sha256"),
        (wad if wad.exists() else iwad, "scenario_wad_sha256"),
    ):
        if digest(path) != manifest[key]:
            raise ValueError(f"Native audit asset differs from recording: {path.name}")
    events = read_lines(directory / "events.jsonl")
    telemetry = read_lines(directory / "telemetry.jsonl")
    frames, masks = endpoint_audit(directory, manifest, events)
    detail = tic_audit(directory, manifest, read_json(directory / "replay.json"))
    shots = detail["shots"]
    primitives = [x for x in events if x["event"] == "primitive"]
    translations = [
        x
        for x in telemetry
        if x["event"] == "primitive" and x["expected_free_displacement"] is not None
    ]
    points = [x for x in telemetry if x["event"] in {"init", "primitive"}]
    entries = []
    for row in points:
        cell = (row["cell_x"], row["cell_y"])
        if not entries or entries[-1] != cell:
            entries.append(cell)
    summary = dict(
        run_id=manifest.get("original_run_id", directory.name),
        recording_provenance=manifest.get("recording_provenance", "original_finalized_native"),
        outcome=manifest["outcome"],
        exact_observation_frames=len(frames),
        ammo_decrement_events=len(shots),
        ammo_units_spent=sum(s["ammo_spent"] for s in shots),
        damaging_ammo_events=sum(s["damage"] > 0 for s in shots),
        nondamaging_ammo_events=sum(s["damage"] == 0 for s in shots),
        ammo_events_without_enemy_sprite=sum(s["visible_enemy_sprites"] == 0 for s in shots),
        ammo_events_horizontal_bbox_overlap=sum(s["horizontal_gap_deg"] == 0 for s in shots),
        dry_pistol_attack_tics=detail["dry_pistol_attack_tics"],
        attack_tics=detail["attack_tics"],
        masks=len(masks),
        mask_enemy_iou_ge_half=sum(
            m["best_enemy"] is not None and m["best_enemy"]["iou"] >= 0.5 for m in masks
        ),
        masks_without_object_overlap=sum(m["best_object"] is None for m in masks),
        cells=len(set(entries)),
        cell_entries=len(entries),
        reentries=len(entries) - len(set(entries)),
        translation_primitives=len(translations),
        collision_proxy_count=sum(x["collision_substep"] for x in translations),
        use_primitives=sum(x["requested"]["type"] == "use" for x in primitives),
        geometry_changing_use_proxy=sum(
            bool(x["door_or_switch_activated"]) for x in telemetry if x["event"] == "primitive"
        ),
        visual_cycle_warnings=sum(x["event"] == "visual_cycle_warning" for x in events),
        kills=evidence["combat_final"]["kills"],
        recorded_usd=manifest["total_usd"],
    )
    return dict(
        audit_script_sha256=digest(Path(__file__)),
        vizdoom_version=vzd.__version__,
        artifact_directory=str(directory),
        reconstruction_verification_sha256=digest(directory / "reconstruction.json")
        if (directory / "reconstruction.json").exists()
        else None,
        summary=summary,
        observations=frames,
        masks=masks,
        decision_blocks=decision_blocks(events, telemetry),
        **detail,
        evidence=evidence,
        limits=[
            "Ammo-decrement events are observed discharges, not requested ATTACK duration; same-tic pickups can hide consumption.",
            "Non-damaging discharges are not automatically tactically wasted; no counterfactual or suppression assessment is made.",
            "Horizontal bbox overlap ignores pitch, spread, autoaim and whether a sprite is alive; it is not exact ballistic aim accuracy.",
            "SAM IoU uses exact original JPEG-matched replay frames. Enemy-category overlap does not verify model concept semantics.",
            "Per-tic rendering can differ from original primitive-update rendering; it is not used for SAM pixel audits.",
            "Coverage and collision figures use sampled endpoints and the existing acceleration-sensitive proxy; no QA defect is inferred.",
        ],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("Preserve earlier audits; choose a new output directory")
    args.out.mkdir(parents=True)
    summaries = []
    for directory in args.run_dirs:
        result = audit(directory)
        write_json(args.out / f"{result['summary']['run_id']}.json", result)
        summaries.append(result["summary"])
        print(result["summary"], flush=True)
    write_json(args.out / "summary.json", dict(runs=summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
