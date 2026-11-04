"""Entry point for the M0 spike; run with `uv run python scripts/run_m0_spike.py`."""

import sys

from autoqa.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["spike", *sys.argv[1:]]))
