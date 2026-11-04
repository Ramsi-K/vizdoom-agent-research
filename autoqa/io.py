"""File-only research artifacts; no HTTP payload or headers are logged."""

import hashlib
import json
from pathlib import Path


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


class EventLog:
    def __init__(self, path: Path):
        self.path = path
        path.touch(exist_ok=True)

    def __call__(self, event, **fields):
        with self.path.open("a") as file:
            file.write(json.dumps(dict(event=event, **fields), allow_nan=False) + "\n")
