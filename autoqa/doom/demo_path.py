"""Keep native demo command filenames short without moving run artifacts."""

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


@contextmanager
def native_demo_path(path):
    path = Path(path).resolve()
    # ViZDoom 1.3.2 hung in new_episode with a 115-character filename on WSL;
    # 105 characters worked. Leave room for the native command's other fields.
    if len(str(path).encode()) <= 100:
        yield str(path)
        return
    with TemporaryDirectory(prefix="autoqa-demo-", dir="/tmp") as temporary:
        alias = Path(temporary) / "episode.lmp"
        alias.symlink_to(path)
        yield str(alias)
