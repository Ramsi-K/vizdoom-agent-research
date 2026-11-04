"""Fill explicit AUTOQA markers only. Existing README prose is never rewritten."""

import argparse
import re
from pathlib import Path

from autoqa.eval.metrics import read_json

SECTIONS = ("Demo", "Technology Overview", "Key Findings", "Quick Start")


def fill(text, summary, report):
    bodies = {
        "Demo": f"[Evidence report]({report}) — recorded episodes and verified replay clips.",
        "Technology Overview": "Python 3.12, uv, ViZDoom, Meta Muse Spark, SAM 3.1, Pillow and FFmpeg. Run manifests record the actual model and settings.",
        "Key Findings": f"Report covers {summary.get('runs', len(summary.get('episodes', [])))} recorded episodes. Behavior confirmation is not a game-defect verdict. See the report for outcomes, missing measurements and limitations.",
        "Quick Start": "```bash\nuv sync --locked\nuv run autoqa doctor --offline\nuv run autoqa report runs/<run_id> --out results/qa-report\n```\nGameplay and Reviewer API calls require explicit spending approval.",
    }
    updated = text
    changed = []
    for title in SECTIONS:
        pattern = re.compile(
            r"(?P<start><!-- AUTOQA: "
            + re.escape(title)
            + r" START -->)\n.*?\n(?P<end><!-- AUTOQA: "
            + re.escape(title)
            + r" END -->)",
            re.DOTALL,
        )
        matches = list(pattern.finditer(updated))
        if len(matches) > 1:
            raise ValueError(f"Duplicate placeholder block: {title}")
        if matches:
            updated = pattern.sub(
                lambda m: m["start"] + "\n" + bodies[title] + "\n" + m["end"], updated
            )
            changed.append(title)

    # A second independent boundary guard protects the research motivation.
    def part_two(document):
        start = document.find("# Part II —")
        end = document.find("# Part III —", start + 1)
        return document[start:end] if 0 <= start < end else None

    if part_two(updated) != part_two(text):
        raise ValueError("Refusing a change to Part II")
    return updated, changed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    old = args.readme.read_text()
    updated, changed = fill(old, read_json(args.summary), str(args.summary.parent / "report.html"))
    if args.write and changed:
        args.readme.write_text(updated)
    print(
        "Updated" if args.write else "Would update",
        ", ".join(changed) or "no explicit placeholders; existing prose preserved",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
