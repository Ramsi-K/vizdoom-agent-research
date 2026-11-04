import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image

from autoqa.config import RunConfig
from autoqa.doom.env import DoomEnv
from autoqa.doom.plan_renderer import render_plan


def test_map_has_real_geometry_spawn_and_goal_pixels():
    geometry = json.loads(Path("tests/fixtures/m2/my_way_home_geometry.json").read_text())
    data = render_plan(geometry, dict(pos_x=464, pos_y=-176, angle_deg=338.93920906329043))
    image = Image.open(io.BytesIO(data))
    pixels = np.array(image)
    assert image.size == (640, 480) and image.mode == "RGB"
    for color in ((240, 244, 248), (41, 121, 255), (0, 230, 118)):
        assert np.any(np.all(pixels == color, axis=2))


def test_paired_spawn_and_static_plan_are_identical_until_episode_ends(tmp_path):
    calibration = json.loads(Path("configs/calibration.json").read_text())["measured"]
    states = []
    for level in (1, 2, 3):
        directory = tmp_path / str(level)
        directory.mkdir()
        (directory / "frames").mkdir()
        env = DoomEnv(RunConfig(level=level, max_tics=5), directory, calibration)
        try:
            obs = env.observe()
            states.append({k: env.telemetry.last[k] for k in ("pos_x", "pos_y", "angle_deg")})
            assert (obs.plan_png is not None) == (level == 3)
            if level == 3:
                sha = hashlib.sha256(obs.plan_png).hexdigest()
                from autoqa.config import Act

                env.execute(
                    Act.model_validate(
                        dict(
                            moves=[dict(type="turn_left", amount=10)],
                            run=False,
                            intent="static-plan test",
                        )
                    )
                )
                assert hashlib.sha256(env.observe().plan_png).hexdigest() == sha
                assert (directory / "plan_l3.png").read_bytes() == obs.plan_png
                events = [
                    json.loads(line)
                    for line in (directory / "events.jsonl").read_text().splitlines()
                ]
                assert sum(e["event"] == "static_plan" for e in events) == 1
                assert (
                    next(e["tic"] for e in events if e["event"] == "static_plan") == env.start_tic
                )
        finally:
            env.close()
    assert states[0] == states[1] == states[2]


def test_freedoom_plan_records_unknown_exit_instead_of_inventing_goal(tmp_path):
    directory = tmp_path / "freedoom"
    directory.mkdir()
    (directory / "frames").mkdir()
    cal = json.loads(Path("configs/calibration.json").read_text())["measured"]
    env = DoomEnv(RunConfig(scenario="freedoom2_map01", level=3, max_tics=5), directory, cal)
    try:
        image = Image.open(io.BytesIO(env.observe().plan_png))
        assert image.size == (640, 480)
        pixels = np.array(image)
        assert np.any(np.all(pixels == (240, 244, 248), axis=2))
        assert not np.any(np.all(pixels == (0, 230, 118), axis=2))
    finally:
        env.close()


def test_combat_corridor_l3_marks_the_static_armor_goal(tmp_path):
    (tmp_path / "frames").mkdir()
    cal = json.loads(Path("configs/calibration.json").read_text())["measured"]
    env = DoomEnv(
        RunConfig(scenario="deadly_corridor_combat", level=3, max_tics=5, effort="high"),
        tmp_path,
        cal,
    )
    try:
        pixels = np.array(Image.open(io.BytesIO(env.observe().plan_png)))
        assert np.any(np.all(pixels == (0, 230, 118), axis=2))
        assert env.telemetry.goal is not None
    finally:
        env.close()
