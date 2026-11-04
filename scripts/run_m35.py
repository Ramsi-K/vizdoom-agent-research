"""Prepare M3.5 or execute exactly one slot, then export its full native replay."""

import argparse
from pathlib import Path

import yaml

from autoqa.config import RunConfig, combat_enabled
from autoqa.eval.combat import write_combat_summary
from autoqa.eval.metrics import number, read_json
from autoqa.io import digest, write_json
from autoqa.sweep import UniqueLoader, source_hashes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/m35-experiments.yaml"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--index", type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    cfg = yaml.load(args.config.read_text(), Loader=UniqueLoader)
    if not number(cfg.get("max_total_usd")) or cfg["max_total_usd"] <= 0:
        parser.error("A finite positive total spending threshold is required")
    runs = [RunConfig.model_validate({**cfg["common"], **run}) for run in cfg["runs"]]
    if not runs or any(not combat_enabled(c.scenario) for c in runs):
        parser.error("Only explicit combat profiles are allowed in M3.5")
    path = args.out / "experiment.json"
    if not path.exists():
        args.out.mkdir(parents=True, exist_ok=False)
        write_json(
            path,
            dict(
                name=cfg["name"],
                config_sha256=digest(args.config),
                source_sha256=source_hashes(Path.cwd()),
                max_total_usd=cfg["max_total_usd"],
                status="prepared_no_api_calls",
                slots=[
                    dict(
                        index=i,
                        config=c.model_dump(),
                        status="pending",
                        directory=None,
                        cost_usd=0.0,
                    )
                    for i, c in enumerate(runs)
                ],
            ),
        )
    plan = read_json(path)
    if args.execute:
        if args.index is None or not 0 <= args.index < len(runs):
            parser.error("Choose one valid --index; no automatic next episode")
        if plan["source_sha256"] != source_hashes(Path.cwd()) or plan["config_sha256"] != digest(
            args.config
        ):
            parser.error("Frozen source/config changed; prepare a new experiment")
        if [s["config"] for s in plan["slots"]] != [c.model_dump() for c in runs] or plan[
            "max_total_usd"
        ] != cfg["max_total_usd"]:
            parser.error("Plan differs from the frozen experiment configuration")
        if any(s["status"] == "running" for s in plan["slots"]):
            parser.error("In-flight slot requires manual reconciliation; never auto-repeat calls")
        slot = plan["slots"][args.index]
        if slot["status"] != "pending":
            parser.error("Slot already attempted; preserve it and select another")
        if (
            sum(s["cost_usd"] for s in plan["slots"]) + runs[args.index].max_usd
            > plan["max_total_usd"] + 1e-9
        ):
            parser.error("Total recorded-usage threshold would be exceeded")
        # One invocation owns one paid run. A later slot needs another explicit command.
        lock = args.out / ".execution.lock"
        with lock.open("x"):
            try:
                from autoqa.doom.replay import replay
                from autoqa.run import run_episode

                slot["status"] = "running"
                plan["status"] = "running_one_slot"
                write_json(path, plan)
                directory, manifest = run_episode(runs[args.index])
                slot.update(
                    status="recorded",
                    directory=str(directory.resolve()),
                    cost_usd=manifest["total_usd"],
                    outcome=manifest["outcome"],
                )
                plan["status"] = "awaiting_next_explicit_command"
                write_json(path, plan)
                replay(directory)
                slot["replay_verified"] = True
                write_json(path, plan)
            finally:
                lock.unlink()
    directories = [Path(s["directory"]) for s in plan["slots"] if s["status"] == "recorded"]
    if directories:
        summary = write_combat_summary(directories, args.out / "summary.json")
        print(
            {
                "recorded_episodes": len(directories),
                "recorded_usd": summary["recorded_cost_usd"],
                "status": plan["status"],
            }
        )
    else:
        print(
            {
                "status": plan["status"],
                "planned_episodes": len(runs),
                "max_total_usd": plan["max_total_usd"],
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
