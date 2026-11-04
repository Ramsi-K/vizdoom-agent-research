"""Explicit paid M4 carry-over campaign. Run from a frozen checkout; no retries."""

import argparse
import inspect
import json
import math
from pathlib import Path

import yaml

from autoqa.agent.carryover import extract
from autoqa.config import RunConfig
from autoqa.io import digest, write_json
from autoqa.run import run_episode
from autoqa.sweep import UniqueLoader

try:
    from autoqa.worker import finalize_on_sigterm
except ModuleNotFoundError:  # Frozen M4 checkpoint predates the packaged wrapper.
    from scripts.run_demo_worker import finalize_on_sigterm


def validate_config(cfg):
    expected = {
        "protocol",
        "scenario",
        "level",
        "seeds",
        "attempts",
        "model",
        "effort",
        "max_decisions",
        "max_tics",
        "max_usd_per_episode",
        "wall_seconds",
        "request_timeout",
        "max_total_usd",
    }
    if not isinstance(cfg, dict) or set(cfg) != expected:
        raise ValueError("Carry-over configuration has missing or unexpected keys")
    if (
        cfg["protocol"] not in {"self_observed_only_v1", "self_observed_only_v2"}
        or cfg["attempts"] != 2
    ):
        raise ValueError(
            "Carry-over requires a supported extraction version and exactly two attempts"
        )
    seeds = cfg["seeds"]
    if (
        not isinstance(seeds, list)
        or not 1 <= len(seeds) <= 5
        or any(type(s) is not int or not 0 <= s <= 2**32 - 1 for s in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise ValueError("Carry-over requires 1–5 unique uint32 seeds")
    for key in ("max_total_usd", "max_usd_per_episode", "wall_seconds", "request_timeout"):
        if type(cfg[key]) not in (int, float) or not math.isfinite(cfg[key]) or cfg[key] <= 0:
            raise ValueError("Budgets and deadlines must be positive finite numbers")
    if cfg["max_total_usd"] < cfg["max_usd_per_episode"]:
        raise ValueError("Campaign threshold must cover one episode threshold")
    RunConfig(
        scenario=cfg["scenario"],
        level=cfg["level"],
        seed=seeds[0],
        model=cfg["model"],
        effort=cfg["effort"],
        max_decisions=cfg["max_decisions"],
        max_tics=cfg["max_tics"],
        max_usd=cfg["max_usd_per_episode"],
        wall_seconds=cfg["wall_seconds"],
        request_timeout=cfg["request_timeout"],
    )
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/sweep_carryover.yaml"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue finalized slots only; never rerun an attempt",
    )
    parser.add_argument(
        "--reserve-usd",
        type=float,
        default=0.0,
        help="Explicit campaign reserve for previously unknown charges",
    )
    parser.add_argument(
        "--continue-after-invalid",
        action="store_true",
        help="Skip finalized invalid slots; never rerun them",
    )
    parser.add_argument(
        "--continue-with-reserved-errors",
        action="store_true",
        help="Continue remaining slots only while explicit per-failure planning reserves fit --reserve-usd",
    )
    args = parser.parse_args(argv)
    if not 0 <= args.reserve_usd <= 4:
        parser.error("--reserve-usd must be finite and between 0 and 4")
    cfg = validate_config(yaml.load(args.config.read_text(), Loader=UniqueLoader))
    target = args.out / "validation.json"
    if args.resume:
        state = json.loads(target.read_text())
        if state["config_sha256"] != digest(args.config):
            raise ValueError("Carry-over config changed since preparation")
        if state["status"] == "running":
            raise ValueError("Refusing to resume a running or unresolved in-flight worker")
        if state.get("unknown_charge_risk") and args.reserve_usd <= 0:
            raise ValueError("Unknown charges require an explicit planning reserve")
        for row in state["completed"]:
            manifest = json.loads((Path(row["directory"]) / "manifest.json").read_text())
            if manifest["outcome"] in {"running", "interrupted"} or (
                manifest["outcome"].startswith("invalid_") and not args.continue_after_invalid
            ):
                raise ValueError("Cannot silently rerun an invalid/incomplete attempt")
            actual = {
                str(p.relative_to(Path.cwd())): digest(p)
                for p in sorted((Path.cwd() / "autoqa").rglob("*.py"))
            }
            if actual != manifest["source_sha256"]:
                raise ValueError("Runtime source changed; resume from the original frozen checkout")
        if args.execute:
            archive = (
                args.out / f"validation.before-resume-{len(state.get('resume_history', []))}.json"
            )
            if archive.exists():
                raise FileExistsError(archive)
            archive.write_bytes(target.read_bytes())
            state.setdefault("resume_history", []).append(
                dict(
                    prior_status=state["status"],
                    reserve_usd=args.reserve_usd,
                    orchestrator_sha256=digest(Path(__file__)),
                    skipped_invalid_slots=bool(args.continue_after_invalid),
                    continued_errors_with_explicit_reserve=bool(args.continue_with_reserved_errors),
                )
            )
    else:
        args.out.mkdir(parents=True, exist_ok=False)
        state = dict(
            status="prepared",
            config_sha256=digest(args.config),
            config=cfg,
            completed=[],
            recorded_usd=0.0,
            unknown_charge_risk=False,
        )
        write_json(target, state)
    if not args.execute:
        return 0
    with finalize_on_sigterm():
        for seed in cfg["seeds"]:
            previous = None
            for attempt in (1, 2):
                completed = next(
                    (
                        row
                        for row in state["completed"]
                        if row["seed"] == seed and row["attempt"] == attempt
                    ),
                    None,
                )
                if completed:
                    previous = Path(completed["directory"])
                    continue
                if (
                    state["recorded_usd"] + cfg["max_usd_per_episode"] + 0.03 + args.reserve_usd
                    > cfg["max_total_usd"]
                ):
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
                extraction = cfg["protocol"]
                options = {}
                if "carryover_protocol" in inspect.signature(run_episode).parameters:
                    options["carryover_protocol"] = extraction
                elif extraction != "self_observed_only_v1":
                    raise ValueError("Frozen checkpoint only supports carry-over v1")
                directory, manifest = run_episode(
                    config,
                    carry_over=previous,
                    **options,
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
                reserved = 0.0
                for finished in state["completed"]:
                    entries = [
                        json.loads(line)
                        for line in (Path(finished["directory"]) / "events.jsonl")
                        .read_text()
                        .splitlines()
                        if line.strip()
                    ]
                    reserved += sum(
                        0.03
                        if e.get("event") == "api_error"
                        else 0.02
                        if e.get("event") == "grounding_error"
                        and e.get("error_type") != "ValueError"
                        else 0
                        for e in entries
                    )
                state["failed_request_planning_reserve_usd"] = reserved
                state["reserve_is_not_recorded_usage"] = True
                may_continue = args.continue_with_reserved_errors and reserved <= args.reserve_usd
                if (
                    (
                        manifest["outcome"].startswith("invalid_")
                        and not (
                            manifest["outcome"] == "invalid_api_error"
                            and args.continue_after_invalid
                            and may_continue
                        )
                    )
                    or manifest["outcome"] == "interrupted"
                    or (unknown and not may_continue)
                ):
                    state["status"] = "stopped_error_or_unknown_charge"
                    write_json(target, state)
                    return 1
                previous = directory
                if attempt == 1:
                    brief = (
                        extract(directory, protocol=cfg["protocol"])
                        if "protocol" in inspect.signature(extract).parameters
                        else extract(directory)
                    )
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
        invalid = any(row["outcome"].startswith("invalid_") for row in state["completed"])
        state.update(
            status="finished_with_invalid_attempts" if invalid else "finished", active=None
        )
        write_json(target, state)
    return int(invalid)


if __name__ == "__main__":
    raise SystemExit(main())
