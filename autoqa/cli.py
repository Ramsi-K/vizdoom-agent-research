"""The M0 CLI intentionally exposes only doctor and spike."""

import argparse
from pathlib import Path

from .m0.spike import run_spike


def main(argv=None):
    parser = argparse.ArgumentParser(description="ViZDoom Meta AI M0 feasibility tooling")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("doctor", "spike"):
        sub = commands.add_parser(command)
        sub.add_argument("--offline", action="store_true", help="Skip every live API probe")
        sub.add_argument("--timeout", type=float, default=12, help="Per-request timeout in seconds")
        if command == "spike":
            sub.add_argument("--out", type=Path, default=Path("docs/spike-results.md"))
            sub.add_argument("--calibration", type=Path, default=Path("configs/calibration.json"))
    args = parser.parse_args(argv)
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
