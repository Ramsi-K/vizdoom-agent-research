"""Run native engine tasks in a child process so a crash becomes a finding."""

import argparse
import json
from pathlib import Path

from .local import calibrate, capture_frames, record_replay


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=("calibrate", "frames", "replay", "nomonsters"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    match args.task:
        case "calibrate":
            result = calibrate()
        case "frames":
            result = capture_frames(args.artifacts / "frames", args.count)
        case "replay":
            result = [record_replay(args.artifacts, s) for s in ("my_way_home", "freedoom2")]
        case "nomonsters":
            result = record_replay(args.artifacts, "freedoom2", nomonsters=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
