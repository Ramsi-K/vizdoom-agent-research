"""Measured engine probes used only by the M0 commands."""

from __future__ import annotations

import hashlib
import math
import subprocess
from pathlib import Path

import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
import vizdoom as vzd
from PIL import Image

SCENARIOS = ("my_way_home", "health_gathering_supreme", "freedoom2")
BUTTONS = [
    vzd.Button.MOVE_FORWARD,
    vzd.Button.SPEED,
    vzd.Button.TURN_LEFT,
    vzd.Button.TURN_RIGHT,
    vzd.Button.TURN_LEFT_RIGHT_DELTA,
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def game_for(scenario: str, *, replay=False, nomonsters=False, timeout=2100):
    game = vzd.DoomGame()
    game.load_config(str(Path(vzd.scenarios_path) / f"{scenario}.cfg"))
    game.set_available_buttons(BUTTONS)
    game.set_available_game_variables([])
    game.set_screen_resolution(
        vzd.ScreenResolution.RES_1280X720 if replay else vzd.ScreenResolution.RES_640X480
    )
    game.set_screen_format(vzd.ScreenFormat.RGB24)
    game.set_render_hud(True)
    game.set_render_messages(True)
    game.set_render_weapon(True)
    game.set_render_crosshair(True)
    game.set_window_visible(False)
    game.set_mode(vzd.Mode.PLAYER)
    game.set_sound_enabled(False)
    game.set_console_enabled(False)
    game.set_depth_buffer_enabled(False)
    game.set_automap_buffer_enabled(False)
    game.set_audio_buffer_enabled(False)
    game.set_labels_buffer_enabled(True)
    game.set_objects_info_enabled(True)
    game.set_sectors_info_enabled(True)
    game.set_episode_timeout(timeout)
    game.set_seed(101)
    game.set_doom_skill(1)
    game.add_game_args("+viz_nocheat 0")
    if scenario == "freedoom2":
        game.set_doom_map("MAP01")
    if nomonsters:
        game.add_game_args("-nomonsters")
    return game


def pose(game):
    return [
        game.get_game_variable(v)
        for v in (vzd.GameVariable.POSITION_X, vzd.GameVariable.POSITION_Y, vzd.GameVariable.ANGLE)
    ]


def signed_angle(after, before):
    return (after - before + 180) % 360 - 180


def calibrate():
    game = game_for("my_way_home")
    try:
        game.init()
        fov = game.get_game_variable(vzd.GameVariable.CAMERA_FOV)
        default_max = game.get_button_max_value(vzd.Button.TURN_LEFT_RIGHT_DELTA)
        turns = []
        for maximum in (default_max, 30.0):
            game.set_button_max_value(vzd.Button.TURN_LEFT_RIGHT_DELTA, maximum)
            for requested in (5.0, 45.0, 90.0, 180.0, -45.0):
                game.new_episode()
                before = pose(game)[2]
                game.make_action([0, 0, 0, 0, requested], 1)
                turns.append(
                    dict(
                        requested_deg=requested,
                        max_value=maximum,
                        applied_signed_deg=signed_angle(pose(game)[2], before),
                    )
                )
        game.set_button_max_value(vzd.Button.TURN_LEFT_RIGHT_DELTA, default_max)
        binary = {}
        for name, index in (("left", 2), ("right", 3)):
            game.new_episode()
            start = previous = pose(game)[2]
            trace = []
            total = 0.0
            for tic in range(1, 46):
                action = [0] * len(BUTTONS)
                action[index] = 1
                game.make_action(action, 1)
                current = pose(game)[2]
                total += abs(signed_angle(current, previous))
                trace.append(dict(tics=tic, accumulated_deg=total))
                previous = current
                if total >= 90:
                    break
            binary[name] = dict(start_angle=start, tics_per_90=tic, trace=trace)
        # Measure acceleration separately from steady movement. Short traces and
        # several headings expose collisions rather than hiding them in a mean.
        movement = []
        for speed in (False, True):
            for heading in (0, 90, 180, 270):
                game.new_episode()
                angle = pose(game)[2]
                game.make_action([0, 0, 0, 0, -signed_angle(heading, angle)], 1)
                previous = pose(game)
                distances = []
                for _ in range(8):
                    game.make_action([1, int(speed), 0, 0, 0], 1)
                    current = pose(game)
                    distances.append(math.dist(previous[:2], current[:2]))
                    previous = current
                movement.append(
                    dict(
                        speed=speed,
                        requested_heading=heading,
                        measured_heading=pose(game)[2],
                        units_per_tic=distances,
                    )
                )
        return dict(
            camera_fov_deg=fov,
            resolution=[640, 480],
            delta_default_max=default_max,
            delta_turn_samples=turns,
            binary_turns=binary,
            movement_samples=movement,
            forward_units_per_tic={
                name: max(m["units_per_tic"][-1] for m in movement if m["speed"] == enabled)
                for name, enabled in (("walk", False), ("run", True))
            },
            movement_method="Maximum eighth-tic displacement across four headings; inspect raw traces for collisions. Not a steady-state estimate.",
        )
    finally:
        game.close()


def capture_frames(directory: Path, count=20):
    directory.mkdir(parents=True, exist_ok=True)
    frames, names, hashes = [], {}, {}
    for scenario_index, scenario in enumerate(SCENARIOS):
        game = game_for(scenario, nomonsters=scenario == "freedoom2")
        seen_labels, seen_objects = set(), set()
        config = Path(vzd.scenarios_path) / f"{scenario}.cfg"
        hashes[scenario] = sha256(config)
        try:
            game.init()
            game.make_action([0] * len(BUTTONS), 5)
            # Select positive armor fixtures explicitly for perception calibration.
            # This is not an episode evaluation or a gameplay completion result.
            selected_episode = 0
            if scenario == "my_way_home":
                for selected_episode in range(32):
                    state = game.get_state()
                    goal = next((o for o in state.objects if o.name == "GreenArmor"), None)
                    if goal is not None:
                        x, y, angle = pose(game)
                        bearing = math.degrees(math.atan2(goal.position_y - y, goal.position_x - x))
                        game.make_action([0, 0, 0, 0, -signed_angle(bearing, angle)], 1)
                        if any(
                            label.object_name == "GreenArmor" for label in game.get_state().labels
                        ):
                            break
                    game.new_episode()
            for i in range(count // 3 + (scenario_index < count % 3)):
                state = game.get_state()
                if state is None:
                    raise RuntimeError(f"{scenario}: ended before frame capture")
                path = directory / f"{scenario}_{i:02d}.jpg"
                Image.fromarray(state.screen_buffer).save(path, quality=85)
                seen_labels.update(label.object_name for label in state.labels)
                seen_objects.update(obj.name for obj in state.objects)
                # The masks are engine-ground-truth actor masks, not semantic
                # annotations for doors/switches (which are sector linedefs).
                actors = []
                for label in state.labels:
                    mask_path = path.with_name(f"{path.stem}_label{label.value}.png")
                    mask = (state.labels_buffer == label.value).astype(np.uint8) * 255
                    Image.fromarray(mask).save(mask_path)
                    actors.append(dict(name=label.object_name, mask=str(mask_path)))
                frames.append(
                    dict(
                        scenario=scenario,
                        path=str(path),
                        actors=actors,
                        tic=game.get_episode_time(),
                        selected_episode=selected_episode,
                    )
                )
                game.make_action([0, 0, 0, 0, 5 if scenario == "my_way_home" else 45], 1)
            names[scenario] = dict(labels=sorted(seen_labels), objects=sorted(seen_objects))
        finally:
            game.close()
    return dict(frames=frames, actor_names=names, scenario_sha256=hashes)


def record_replay(directory: Path, scenario: str, *, nomonsters=False):
    directory.mkdir(parents=True, exist_ok=True)
    recording = (directory / f"{scenario}_{nomonsters}.lmp").resolve()
    game = game_for(scenario, nomonsters=nomonsters, timeout=71)
    try:
        game.init()
        game.new_episode(str(recording))
        while not game.is_episode_finished():
            # Calibration-only trace, isolated from all future gameplay code.
            game.make_action([0, 0, 1, 0, 0], 1)
        original = dict(final_tic=game.get_episode_time(), reward=game.get_total_reward())
        game.new_episode()  # flush demo
    finally:
        game.close()
    replay = game_for(scenario, replay=True, nomonsters=nomonsters, timeout=71)
    video = directory / f"{scenario}_{nomonsters}.mp4"
    captured = 0
    try:
        replay.init()
        replay.replay_episode(str(recording))
        with imageio.get_writer(
            video,
            fps=35,
            codec="libx264",
            macro_block_size=1,
            output_params=["-movflags", "+faststart"],
        ) as writer:
            while not replay.is_episode_finished():
                state = replay.get_state()
                writer.append_data(state.screen_buffer)
                captured += 1
                replay.advance_action(1, True)
        replayed = dict(final_tic=replay.get_episode_time(), reward=replay.get_total_reward())
    finally:
        replay.close()
    with imageio.get_reader(video) as reader:
        shape = list(reader.get_data(0).shape)
        codec = reader.get_meta_data()["codec"]
    result = dict(
        scenario=scenario,
        nomonsters=nomonsters,
        original=original,
        replay=replayed,
        matches=original == replayed,
        frames=captured,
        replay_shape=shape,
        codec=codec,
        lmp=str(recording),
        lmp_sha256=sha256(recording),
        video=str(video),
    )
    if not result["matches"]:
        raise ValueError(f"Replay mismatch: {result}")
    return result


def environment(root: Path, key_present: bool):
    import platform
    import sys

    ignored = (
        subprocess.run(
            ["git", "check-ignore", "-q", ".env"], cwd=root, capture_output=True
        ).returncode
        == 0
    )
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    ffmpeg = subprocess.run([exe, "-version"], capture_output=True, check=True, text=True)
    return dict(
        python=sys.version.split()[0],
        platform=platform.platform(),
        python_312=sys.version_info[:2] == (3, 12),
        key_present=key_present,
        env_ignored=ignored,
        ffmpeg=ffmpeg.stdout.splitlines()[0],
        vizdoom_version=vzd.__version__,
    )


def iwad_inventory():
    path = Path(vzd.__file__).parent
    wad = path / "freedoom2.wad"
    if not wad.is_file():
        raise FileNotFoundError("Bundled freedoom2.wad is absent")
    return dict(
        freedoom2_path=str(wad),
        freedoom2_sha256=sha256(wad),
        freedoom1_installed=(path / "freedoom1.wad").is_file(),
    )
