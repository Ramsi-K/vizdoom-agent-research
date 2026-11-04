"""Read immutable source traces and resolve only validated native clock resets."""

import math
from pathlib import Path

from autoqa.eval.metrics import number, read_json, read_lines
from autoqa.io import digest


def safe_file(root, relative, suffixes=None):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Evidence references must stay inside the run directory")
    target = (Path(root) / path).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise ValueError("Evidence symlink leaves the run directory")
    if suffixes and target.suffix.lower() not in suffixes:
        raise ValueError("Unsupported evidence file type")
    return target


class Trace:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.manifest = read_json(self.directory / "manifest.json")
        self.events = read_lines(self.directory / "events.jsonl")
        self.rows = read_lines(self.directory / "telemetry.jsonl")
        self.source_hashes = {}
        self.limitations = []
        self.finalized = self.manifest.get("outcome") != "running"
        for name in (
            "manifest.json",
            "events.jsonl",
            "telemetry.jsonl",
            "geometry.json",
            "episode.lmp",
            "issues.json",
        ):
            path = self.directory / name
            if not path.exists():
                continue
            actual = digest(path)
            expected = self.manifest.get(name.replace(".", "_") + "_sha256")
            if expected and expected != actual:
                raise ValueError(f"Source artifact changed: {name}")
            self.source_hashes[name] = actual
        if not self.finalized:
            self.limitations.append("Unfinalized source; behavior verification is unavailable")
        self.initial = next((r for r in self.rows if r.get("event") == "init"), {})
        self.start = self.initial.get("tic", 0)
        self.end = max(
            (r.get("tic", 0) for r in self.rows if number(r.get("tic"))), default=self.start
        )
        self.replay = None
        path = self.directory / "replay.json"
        if path.exists():
            candidate = read_json(path)
            lmp = self.directory / "episode.lmp"
            if (
                lmp.exists()
                and candidate.get("matches_telemetry")
                and candidate.get("lmp_sha256")
                == self.manifest.get("episode_lmp_sha256")
                == digest(lmp)
            ):
                self.replay = candidate
                elapsed = candidate.get("reconstructed_tics_used")
                if (
                    number(elapsed)
                    and elapsed >= 0
                    and candidate.get("from_tic") == self.start
                    and candidate.get("to_tic") == self.start + elapsed
                ):
                    self.end = self.start + elapsed
        self.geometry = (
            read_json(self.directory / "geometry.json")
            if (self.directory / "geometry.json").exists()
            else {}
        )
        self.primitives = [e for e in self.events if e.get("event") == "primitive"]
        self.feedback = {
            e["decision_idx"]: e for e in self.events if e.get("event") == "action_feedback"
        }
        self.by_step = {}
        for row in self.rows:
            if row.get("event") == "primitive":
                self.by_step[(row["decision_idx"], row["primitive_idx"])] = row

    def tic(self, value):
        if not number(value):
            return None
        if value < self.start:
            if self.replay and number(self.replay.get("reconstructed_tics_used")):
                return self.end
            return None
        return value

    def frame_tic(self, step):
        before = [e for e in self.primitives if e["decision_idx"] < step]
        if before:
            return self.tic(before[-1]["tic_end"])
        return self.start

    def evidence_range(self, steps):
        if not steps or any(not isinstance(s, int) or isinstance(s, bool) or s < 0 for s in steps):
            return None
        bounds = []
        for step in steps:
            if not (self.directory / "frames" / f"step_{step:04d}.jpg").exists():
                return None
            observed = any(e.get("decision_idx") == step for e in self.events)
            if not observed:
                return None
            start = self.frame_tic(step)
            ends = [self.tic(e["tic_end"]) for e in self.primitives if e["decision_idx"] == step]
            if start is None or any(t is None for t in ends):
                return None
            bounds.extend([start, *ends])
        return [int(min(bounds)), int(max(bounds))]

    def window(self, bounds, padding=70):
        lo, hi = max(self.start, bounds[0] - padding), min(self.end, bounds[1] + padding)
        rows = [
            r
            for r in self.rows
            if self.tic(r.get("tic")) is not None and lo <= self.tic(r.get("tic")) <= hi
        ]
        primitives = [
            e
            for e in self.primitives
            if self.tic(e.get("tic_end")) is not None
            and e["tic_start"] <= hi
            and self.tic(e["tic_end"]) >= lo
        ]
        events = [
            e for e in self.events if e.get("decision_idx") in {r.get("decision_idx") for r in rows}
        ]
        return lo, hi, rows, primitives, events


def distance_to_segment(point, line):
    a, b = (line["x1"], line["y1"]), (line["x2"], line["y2"])
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = (
        max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)))
        if dx or dy
        else 0
    )
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))
