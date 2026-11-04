"""Explicit paid M4 carry-over campaign. Run from a frozen checkout; no retries."""

import argparse
import json
from pathlib import Path

import yaml

from autoqa.agent.carryover import extract
from autoqa.config import RunConfig
from autoqa.io import digest, write_json
from autoqa.run import run_episode
from scripts.run_demo_worker import finalize_on_sigterm


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/sweep_carryover.yaml"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    cfg = yaml.safe_load(args.config.read_text())
    args.out.mkdir(parents=True, exist_ok=False)
    state = dict(
        status="prepared",
        config_sha256=digest(args.config),
        config=cfg,
        completed=[],
        recorded_usd=0.0,
        unknown_charge_risk=False,
    )
    target = args.out / "validation.json"
    write_json(target, state)
    if not args.execute:
        return 0
    with finalize_on_sigterm():
        for seed in cfg["seeds"]:
            previous = None
            for attempt in (1, 2):
                if state["recorded_usd"] + cfg["max_usd_per_episode"] + 0.03 > cfg["max_total_usd"]:
                    state["status"] = "stopped_budget_reserve"
                    write_json(target, state)
                    return 1
                config = RunConfig(
                    scenario=cfg["scenario"],
                    level=cfg["level"],
                    seed=seed,
                    model=cfg["model"],
                    effort=cfg["effort"],
                    max_decisions=cfg["max_decisions"],
                    max_tics=cfg["max_tics"],
                    max_usd=cfg["max_usd_per_episode"],
                    wall_seconds=cfg["wall_seconds"],
                    request_timeout=cfg["request_timeout"],
                )
                state.update(status="running", active=dict(seed=seed, attempt=attempt))
                write_json(target, state)
                directory, manifest = run_episode(
                    config,
                    carry_over=previous,
                    experiment=dict(
                        evaluation_group="carryover",
                        pair_id=f"{cfg['scenario']}_L{cfg['level']}_s{seed}",
                        attempt=attempt,
                    ),
                )
                state["completed"].append(
                    dict(
                        directory=str(directory),
                        outcome=manifest["outcome"],
                        seed=seed,
                        attempt=attempt,
                        cost_usd=manifest["total_usd"],
                    )
                )
                state["recorded_usd"] += manifest["total_usd"]
                errors = [
                    json.loads(line)
                    for line in (directory / "events.jsonl").read_text().splitlines()
                    if line.strip()
                ]
                unknown = any(
                    e.get("event") == "api_error"
                    or (
                        e.get("event") == "grounding_error"
                        and e.get("error_type") not in {"ValueError"}
                    )
                    for e in errors
                )
                state["unknown_charge_risk"] |= unknown
                write_json(target, state)
                if (
                    manifest["outcome"].startswith("invalid_")
                    or manifest["outcome"] == "interrupted"
                    or unknown
                ):
                    state["status"] = "stopped_error_or_unknown_charge"
                    write_json(target, state)
                    return 1
                previous = directory
                if attempt == 1:
                    brief = extract(directory)
                    print(
                        json.dumps(
                            dict(
                                carryover_source=directory.name,
                                brief_chars=len(brief["brief"]),
                                brief_sha256=brief["brief_sha256"],
                            )
                        ),
                        flush=True,
                    )
        state.update(status="finished", active=None)
        write_json(target, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
