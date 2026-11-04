"""File-based diagnostics, gameplay, replay, and M3 evaluation commands."""

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="ViZDoom Meta AI research harness")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("doctor", "spike"):
        sub = commands.add_parser(command)
        sub.add_argument("--offline", action="store_true", help="Skip every live API probe")
        sub.add_argument("--timeout", type=float, default=12, help="Per-request timeout in seconds")
        if command == "spike":
            sub.add_argument("--out", type=Path, default=Path("docs/spike-results.md"))
            sub.add_argument("--calibration", type=Path, default=Path("configs/calibration.json"))
    run = commands.add_parser("run")
    run.add_argument(
        "--scenario",
        choices=[
            "my_way_home",
            "health_gathering_supreme",
            "deadly_corridor_nav",
            "freedoom2_map01",
        ],
        default="my_way_home",
    )
    run.add_argument("--level", type=int, choices=[1, 2, 3], default=1)
    run.add_argument("--seed", type=int, default=101)
    run.add_argument(
        "--model",
        choices=["muse-spark-1.3", "muse-spark-1.3-contributor"],
        default="muse-spark-1.3",
    )
    run.add_argument("--effort", choices=["minimal", "low", "medium"], default="low")
    run.add_argument("--max-decisions", type=int, default=60)
    run.add_argument("--max-tics", type=int, default=2100)
    run.add_argument("--max-usd", type=float, default=0.25)
    run.add_argument("--wall-seconds", type=float, default=900.0)
    run.add_argument("--request-timeout", type=float, default=45.0)
    run.add_argument(
        "--grounding-mode", choices=["sam_text", "muse_box_sam"], default="muse_box_sam"
    )
    view = commands.add_parser("replay")
    view.add_argument("run_dir", type=Path)
    view.add_argument("--size", choices=["640x480", "1280x720"], default="1280x720")
    view.add_argument("--from-tic", type=int)
    view.add_argument("--to-tic", type=int)
    view.add_argument("--captions", action="store_true")
    view.add_argument(
        "--no-outcome-ending", action="store_true", help="Export gameplay frames only"
    )
    view.add_argument(
        "--output-name",
        help="New MP4 filename inside the run directory; existing files are preserved",
    )
    sweep = commands.add_parser("sweep", help="Prepare a sweep; paid execution needs --execute")
    sweep.add_argument("--config", type=Path, required=True)
    sweep.add_argument("--out", type=Path, required=True)
    sweep.add_argument(
        "--execute", action="store_true", help="Explicitly authorize paid Meta calls"
    )
    sweep.add_argument(
        "--resume", action="store_true", help="Use an existing prepared/checkpointed sweep"
    )
    analyze = commands.add_parser("analyze", help="Offline analysis of recorded runs; no API calls")
    analyze.add_argument("run_dirs", type=Path, nargs="+")
    analyze.add_argument("--out", type=Path, required=True)
    metrics = commands.add_parser("metrics", help="Offline metrics for one recorded episode")
    metrics.add_argument("run_dir", type=Path)
    metrics.add_argument("--out", type=Path, required=True, help="New derived JSON file")
    args = parser.parse_args(argv)
    if args.command in {"sweep", "analyze", "metrics"}:
        import json

        from .io import digest, write_json

        try:
            if args.command == "metrics":
                from .eval.metrics import episode_metrics

                if args.out.exists():
                    raise ValueError("Output already exists; choose a new file")
                result = episode_metrics(args.run_dir)
                args.out.parent.mkdir(parents=True, exist_ok=True)
                write_json(args.out, result)
                print(json.dumps(dict(output=str(args.out), outcome=result["outcome"])))
            elif args.command == "analyze":
                from .sweep import analyze_runs

                result = analyze_runs(args.run_dirs, args.out)
                print(json.dumps(dict(output=str(args.out), episodes=len(result["episodes"]))))
            else:
                from .sweep import execute_sweep, prepare_sweep

                if args.resume:
                    from .eval.metrics import read_json

                    plan = read_json(args.out / "sweep_manifest.json")
                    if digest(args.config) != plan["config_sha256"]:
                        raise ValueError("Configuration changed since preparation")
                else:
                    plan = prepare_sweep(args.config, args.out)
                if args.execute:
                    plan = execute_sweep(args.out, approved=True)
                print(
                    json.dumps(
                        {k: plan[k] for k in ("status", "planned_episodes", "max_total_usd")}
                    )
                )
                if args.execute and (
                    plan["status"] != "finished" or plan["pairing_status"] != "complete"
                ):
                    return 1
        except (ValueError, OSError) as error:
            parser.error(str(error))
        return 0
    if args.command == "run":
        from .config import RunConfig
        from .run import run_episode

        config = RunConfig.model_validate({k: v for k, v in vars(args).items() if k != "command"})
        _, manifest = run_episode(config)
        return (
            1
            if manifest["outcome"].startswith("invalid_") or manifest["outcome"] == "interrupted"
            else 0
        )
    if args.command == "replay":
        from .doom.replay import replay

        replay(
            args.run_dir,
            size=tuple(map(int, args.size.split("x"))),
            from_tic=args.from_tic,
            to_tic=args.to_tic,
            captions=args.captions,
            outcome_ending=not args.no_outcome_ending,
            output_name=args.output_name,
        )
        return 0
    from .m0.spike import run_spike

    if not 0 < args.timeout <= 120:
        parser.error("--timeout must be in (0, 120]")
    evidence = run_spike(
        Path.cwd(),
        offline=args.offline,
        doctor=args.command == "doctor",
        out=getattr(args, "out", None),
        calibration=getattr(args, "calibration", None),
        timeout=args.timeout,
    )
    if args.command == "doctor":
        return 0 if evidence["ready"] else 1
    # Partial means incomplete even if some subchecks were successful.
    return 0 if all(row["status"] == "passed" for row in evidence["checks"].values()) else 1
