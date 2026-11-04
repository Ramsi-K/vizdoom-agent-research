"""Detached offline worker survives launcher exit and flushes on parent SIGTERM."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import vizdoom as vzd

from autoqa.config import SCENARIOS, RunConfig
from autoqa.doom.replay import replay
from autoqa.io import digest, write_json


def test_detached_worker_sigterm_preserves_native_recording(tmp_path):
    worker = r"""
import json, signal, sys
from pathlib import Path
from autoqa.config import CombatAct, RunConfig
from autoqa.doom.env import DoomEnv
from scripts.run_demo_worker import finalize_on_sigterm
p=Path(sys.argv[1]); (p/'frames').mkdir()
config=RunConfig(scenario='deadly_corridor_combat', level=3, max_tics=100, effort='high')
cal=json.loads(Path('configs/calibration.json').read_text())['measured']
with finalize_on_sigterm():
 env=DoomEnv(config,p,cal)
 try:
  env.observe()
  env.execute(CombatAct.model_validate(dict(moves=[dict(type='wait',amount=5,fire=False)],run=False,intent='offline signal fixture')))
  (p/'ready').write_text('ready')
  signal.pause()
 except KeyboardInterrupt:
  (p/'signal_caught').write_text('SIGTERM')
 finally:env.close()
"""
    launcher = r"""
import subprocess,sys
from pathlib import Path
p=Path(sys.argv[1])
with (p/'worker.log').open('w') as log:
 child=subprocess.Popen([sys.executable,'-c',sys.argv[2],str(p)],start_new_session=True,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
print(child.pid)
"""
    result = subprocess.run(
        [sys.executable, "-c", launcher, str(tmp_path), worker],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    pid = int(result.stdout.strip())
    try:
        deadline = time.monotonic() + 8
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (tmp_path / "ready").exists(), (tmp_path / "worker.log").read_text()
        assert os.getsid(pid) == pid
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 8
        while not (tmp_path / "episode.lmp").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (tmp_path / "signal_caught").read_text() == "SIGTERM"
        assert (tmp_path / "episode.lmp").stat().st_size > 0
        profile = SCENARIOS["deadly_corridor_combat"]
        write_json(
            tmp_path / "manifest.json",
            dict(
                config=RunConfig(
                    scenario="deadly_corridor_combat", level=3, max_tics=100, effort="high"
                ).model_dump(),
                vizdoom_version=vzd.__version__,
                iwad_sha256=digest(Path(vzd.__file__).parent / "freedoom2.wad"),
                scenario_cfg_sha256=digest(Path(vzd.scenarios_path) / "deadly_corridor.cfg"),
                scenario_wad_sha256=digest(Path(vzd.scenarios_path) / "deadly_corridor.wad"),
                episode_lmp_sha256=digest(tmp_path / "episode.lmp"),
                scenario_profile=profile,
                action_schema="combat_v1",
                outcome="interrupted",
            ),
        )
        info = replay(tmp_path, size=(640, 480), outcome_ending=False)
        assert info["matches_telemetry"] and info["gameplay_frames"] == 5
    finally:
        # Only this offline fixture's worker; never a live experiment.
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
