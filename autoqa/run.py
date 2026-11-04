"""Composition root: connects narrow Pilot session to eval-only engine artifacts."""

import hashlib
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import vizdoom as vzd

from autoqa.agent.client import MetaClient
from autoqa.agent.grounder import Grounder
from autoqa.agent.pilot import Pilot
from autoqa.config import SCENARIOS
from autoqa.doom.env import DoomEnv
from autoqa.io import EventLog, digest, write_json
from autoqa.prompts.builder import prompt_hash
from autoqa.secrets import model_key


def run_episode(config, *, root=Path.cwd()):
    key = model_key(root)
    calibration_path = root / "configs/calibration.json"
    calibration = json.loads(calibration_path.read_text())
    if calibration["status"] != "passed":
        raise ValueError("A measured M0 calibration is required")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    directory = root / "runs" / f"{stamp}_{config.scenario}_L{config.level}_s{config.seed}"
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "frames").mkdir()
    log = EventLog(directory / "events.jsonl")
    git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    profile = SCENARIOS[config.scenario]
    config_path = Path(vzd.scenarios_path) / f"{profile['cfg']}.cfg"
    iwad = Path(vzd.__file__).parent / "freedoom2.wad"
    wad = Path(vzd.scenarios_path) / f"{profile['cfg']}.wad"
    if not wad.exists():
        wad = iwad
    briefing_path = root / "scenarios" / config.scenario / "briefing_l2.md"
    briefing = briefing_path.read_text() if config.level > 1 else None
    code_paths = sorted((root / "autoqa").rglob("*.py"))
    manifest = dict(
        run_id=directory.name,
        timestamp=stamp,
        git_commit=git_sha,
        source_sha256={str(p.relative_to(root)): digest(p) for p in code_paths},
        vizdoom_version=vzd.__version__,
        iwad_path=str(iwad),
        iwad_sha256=digest(iwad),
        scenario_cfg_path=str(config_path),
        scenario_cfg_sha256=digest(config_path),
        scenario_wad_sha256=digest(wad),
        prompt_sha256=prompt_hash(config.level, briefing),
        briefing_sha256=hashlib.sha256(briefing.encode()).hexdigest() if briefing else None,
        calibration_sha256=digest(calibration_path),
        scenario_id=config.scenario,
        level=config.level,
        seed=config.seed,
        model=config.model,
        effort=config.effort,
        config=config.model_dump(),
        is_fault_probe=False,
        fault_injection=None,
        engine_overrides={
            "mode": "PLAYER",
            "resolution": [640, 480],
            "hud": True,
            "episode_start_time": "bundled config (my_way_home: 10)",
            "episode_timeout": config.max_tics,
            "recording_seed_reset": True,
        },
        outcome="running",
        total_usd=0.0,
        usage=None,
        sam_grounding=config.grounding_mode,
    )
    write_json(directory / "manifest.json", manifest)
    env, client, pilot = None, None, None
    started = time.monotonic()
    try:
        env = DoomEnv(config, directory, calibration["measured"])
        manifest["spawn_state"] = {
            key: env.telemetry.last[key] for key in ("pos_x", "pos_y", "pos_z", "angle_deg", "tic")
        }
        manifest["plan_sha256"] = digest(directory / "plan_l3.png") if config.level == 3 else None
        client = MetaClient(key, config, log)
        pilot = Pilot(config, env.player_session(), client, log, briefing_l2=briefing)
        pilot.grounder = Grounder(
            key,
            mode=config.grounding_mode,
            model=config.model,
            fov=calibration["measured"]["camera_fov_deg"],
            timeout=config.request_timeout,
            charge=pilot.charge_grounding,
            directory=directory / "grounding",
            remaining=lambda: (
                0 if pilot.guards.budget_exhausted() else pilot.guards.remaining_seconds()
            ),
        )
        outcome = pilot.run_episode()
        manifest["outcome"] = env.outcome() or outcome
    except KeyboardInterrupt:
        manifest["outcome"] = "interrupted"
    except BaseException as error:
        manifest["outcome"] = "invalid_runtime_error"
        log("runtime_error", error_type=type(error).__name__)
        raise
    finally:
        try:
            if env:
                env.close()
        finally:
            if client:
                client.close()
            manifest["wall_seconds"] = time.monotonic() - started
            if pilot:
                manifest.update(
                    total_usd=pilot.guards.total_usd,
                    usage=pilot.tokens,
                    decisions=pilot.decisions,
                    model_calls=pilot.calls,
                    act_calls=pilot.actions,
                    inline_issues=len(pilot.issues),
                    locate_calls=pilot.locate_calls,
                    grounding_model_calls=len(pilot.grounder.calls),
                    grounding_cost_usd=pilot.grounder.cost_usd,
                )
                write_json(directory / "issues.json", pilot.issues)
            if env and env.telemetry.last:
                manifest.update(
                    final_tic=env.telemetry.last["tic"],
                    reward_total=env.telemetry.last["reward_total"],
                    start_tic=env.start_tic,
                    tics_used=env.telemetry.last["tic"] - env.start_tic,
                )
            for name in ("episode.lmp", "events.jsonl", "telemetry.jsonl"):
                path = directory / name
                if path.exists():
                    manifest[name.replace(".", "_") + "_sha256"] = digest(path)
            write_json(directory / "manifest.json", manifest)
    print(
        json.dumps(
            dict(
                run_dir=str(directory),
                outcome=manifest["outcome"],
                act_calls=manifest.get("act_calls"),
                cost_usd=manifest["total_usd"],
            )
        ),
        flush=True,
    )
    return directory, manifest
