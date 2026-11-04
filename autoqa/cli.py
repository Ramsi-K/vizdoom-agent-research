"""File-based M0 diagnostics and M1 gameplay/replay commands."""

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
    run.add_argument("--scenario", choices=["my_way_home"], default="my_way_home")
    run.add_argument("--level", type=int, choices=[1], default=1)
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
    view = commands.add_parser("replay")
    view.add_argument("run_dir", type=Path)
    view.add_argument("--size", choices=["640x480", "1280x720"], default="1280x720")
    view.add_argument("--from-tic", type=int)
    view.add_argument("--to-tic", type=int)
    view.add_argument("--captions", action="store_true")
    args = parser.parse_args(argv)
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
