import json
from pathlib import Path

import pytest
import vizdoom as vzd
from pydantic import ValidationError

from autoqa.config import Act, Move, RunConfig
from autoqa.doom.env import DoomEnv
from autoqa.doom.replay import replay
from autoqa.io import digest, write_json


def recorded_run(directory, *, count=15):
    directory.mkdir()
    (directory / "frames").mkdir()
    config = RunConfig(max_tics=100)
    calibration = json.loads(Path("configs/calibration.json").read_text())["measured"]
    env = DoomEnv(config, directory, calibration)
    initial = env.telemetry.last.copy()
    try:
        # Determinism-only trace; never imported by autoqa run or the Pilot.
        for i in range(count):
            env.observe()
            move = (
                {"type": "turn_left", "amount": 15}
                if i % 3 == 0
                else {"type": "forward", "amount": 2}
            )
            env.execute(
                Act.model_validate({"moves": [move], "run": False, "intent": "determinism fixture"})
            )
    finally:
        env.close()
    manifest = dict(
        config=config.model_dump(),
        vizdoom_version=vzd.__version__,
        iwad_sha256=digest(Path(vzd.__file__).parent / "freedoom2.wad"),
        scenario_cfg_sha256=digest(Path(vzd.scenarios_path) / "my_way_home.cfg"),
        scenario_wad_sha256=digest(Path(vzd.scenarios_path) / "my_way_home.wad"),
        episode_lmp_sha256=digest(directory / "episode.lmp"),
    )
    write_json(directory / "manifest.json", manifest)
    return initial, env.telemetry.last


def test_repeat_seed_and_native_recording_are_deterministic(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    initial, final = recorded_run(first)
    initial2, final2 = recorded_run(second)
    assert initial["tic"] == 10  # bundled script startup; do not fake tic=1
    for key in ("pos_x", "pos_y", "angle_deg", "reward_total", "tic"):
        assert final[key] == final2[key]
        assert initial[key] == initial2[key]
    assert final["angle_deg"] != initial["angle_deg"]
    assert (first / "episode.lmp").stat().st_size == (second / "episode.lmp").stat().st_size
    result = replay(first, size=(1280, 720), captions=True)
    assert result["matches_telemetry"]
    assert result["final_tic"] == final["tic"]
    assert result["reward_total"] == pytest.approx(final["reward_total"])
    assert result["shape"] == [720, 1280, 3]
    assert result["codec"] == "h264"


def test_zero_action_stop_has_a_readable_initial_frame(tmp_path):
    directory = tmp_path / "zero"
    initial, final = recorded_run(directory, count=0)
    assert final["tic"] == initial["tic"]
    result = replay(directory, size=(640, 480))
    assert result["frames"] == 1
    assert result["matches_telemetry"]


def test_observation_has_pixels_but_no_ground_truth_and_bad_action_is_rejected(tmp_path):
    directory = tmp_path / "gate"
    directory.mkdir()
    (directory / "frames").mkdir()
    config = RunConfig(max_tics=5)
    cal = json.loads(Path("configs/calibration.json").read_text())["measured"]
    env = DoomEnv(config, directory, cal)
    try:
        obs = env.player_session().observe()
        assert obs.tics_elapsed == 0 and obs.plan_png is None
        assert not hasattr(obs, "pos_x")
        assert not hasattr(env.player_session(), "telemetry")
        assert env._game.get_available_game_variables() == []
        bad = Act.model_construct(
            moves=[Move.model_construct(type="forward", amount=99)], run=False, intent="invalid"
        )
        before = env._game.get_episode_time()
        with pytest.raises(ValidationError):
            env.execute(bad)
        assert env._game.get_episode_time() == before
        action = Act.model_validate(
            dict(moves=[dict(type="forward", amount=5)], run=False, intent="test")
        )
        env.execute(action)
        assert env.episode_finished()
        assert env.outcome() == "timeout"
    finally:
        env.close()


def test_natural_completion_clock_reset_is_reconstructed_without_rewriting_telemetry(
    tmp_path, monkeypatch
):
    import importlib
    from types import SimpleNamespace

    import numpy as np

    module = importlib.import_module("autoqa.doom.replay")

    class NativePlayback:
        steps = 0

        def init(self):
            pass

        def replay_episode(self, path):
            pass

        def close(self):
            pass

        def is_episode_finished(self):
            return self.steps == 3

        def get_episode_time(self):
            return 0 if self.is_episode_finished() else 10 + self.steps

        def get_total_reward(self):
            return 1.0

        def get_state(self):
            return SimpleNamespace(screen_buffer=np.zeros((480, 640, 3), dtype=np.uint8))

        def advance_action(self, tics, update):
            assert tics == 1 and update is True
            self.steps += 1

    class Media:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def append_data(self, frame):
            pass

        def get_data(self, index):
            return np.zeros((480, 640, 3), dtype=np.uint8)

        def get_meta_data(self):
            return dict(codec="h264")

    monkeypatch.setattr(module, "configured_game", lambda *args, **kwargs: NativePlayback())
    monkeypatch.setattr(module, "digest", lambda path: "fixture")
    monkeypatch.setattr(module.imageio, "get_writer", lambda *args, **kwargs: Media())
    monkeypatch.setattr(module.imageio, "get_reader", lambda *args, **kwargs: Media())
    write_json(
        tmp_path / "manifest.json",
        dict(
            config=RunConfig().model_dump(),
            outcome="success",
            vizdoom_version=vzd.__version__,
            iwad_sha256="fixture",
            scenario_cfg_sha256="fixture",
            scenario_wad_sha256="fixture",
            episode_lmp_sha256="fixture",
        ),
    )
    raw = "\n".join(json.dumps(row) for row in [dict(tic=10), dict(tic=0, reward_total=1.0)]) + "\n"
    (tmp_path / "telemetry.jsonl").write_text(raw)
    (tmp_path / "events.jsonl").write_text("")
    result = replay(tmp_path, size=(640, 480))
    assert result["matches_telemetry"] and result["final_tic"] == 0
    assert result["reconstructed_tics_used"] == 3
    assert result["reconstructed_final_tic"] == 13 and result["frames"] == 3
    assert (tmp_path / "telemetry.jsonl").read_text() == raw
