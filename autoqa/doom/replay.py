"""Re-render a recorded demo without sending actions or commands to the engine."""

import json
import math
from pathlib import Path

import imageio.v2 as imageio
import vizdoom as vzd
from PIL import Image, ImageDraw

from autoqa.config import SCENARIOS, RunConfig
from autoqa.io import digest, write_json

from .env import configured_game


def replay(directory: Path, *, size=(1280, 720), from_tic=None, to_tic=None, captions=False):
    manifest = json.loads((directory / "manifest.json").read_text())
    config = RunConfig.model_validate(manifest["config"])
    telemetry = [
        json.loads(line) for line in (directory / "telemetry.jsonl").read_text().splitlines()
    ]
    terminal = telemetry[-1]
    profile = SCENARIOS[config.scenario]
    wad = Path(vzd.scenarios_path) / f"{profile['cfg']}.wad"
    if not wad.exists():
        wad = Path(vzd.__file__).parent / "freedoom2.wad"
    for path, expected in [
        (Path(vzd.__file__).parent / "freedoom2.wad", manifest["iwad_sha256"]),
        (Path(vzd.scenarios_path) / f"{profile['cfg']}.cfg", manifest["scenario_cfg_sha256"]),
        (wad, manifest["scenario_wad_sha256"]),
        (directory / "episode.lmp", manifest["episode_lmp_sha256"]),
    ]:
        if digest(path) != expected:
            raise ValueError(f"Replay source hash mismatch: {path.name}")
    if vzd.__version__ != manifest["vizdoom_version"]:
        raise ValueError("ViZDoom version differs from recording")
    lower = from_tic if from_tic is not None else telemetry[0]["tic"]
    upper = to_tic if to_tic is not None else terminal["tic"]
    if (
        lower < telemetry[0]["tic"]
        or upper > terminal["tic"]
        or lower > upper
        or (lower == upper and terminal["tic"] > telemetry[0]["tic"])
    ):
        raise ValueError("Replay tic slice must lie inside the recorded interval")
    video = directory / (
        "replay.mp4" if from_tic is None and to_tic is None else f"replay_{lower}_{upper}.mp4"
    )
    info_path = video.with_suffix(".json")
    if video.exists() or info_path.exists():
        raise FileExistsError("Replay output exists; preserve it or choose another tic slice")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    game = configured_game(config, size=size)
    # A demo flushed before natural termination has a trailing native tic.
    # End playback at the logged action boundary, without injecting any action.
    recorded_tics = terminal["tic"] - telemetry[0]["tic"]
    if recorded_tics > 0:
        game.set_episode_timeout(recorded_tics)
    count = 0
    try:
        game.init()
        game.replay_episode(str((directory / "episode.lmp").resolve()))
        with imageio.get_writer(
            video,
            fps=35,
            codec="libx264",
            macro_block_size=1,
            output_params=["-movflags", "+faststart"],
        ) as writer:
            while not game.is_episode_finished():
                tic = game.get_episode_time()
                state = game.get_state()
                if state is not None and (lower <= tic < upper or recorded_tics == 0):
                    frame = state.screen_buffer
                    if captions:
                        image = Image.fromarray(frame)
                        latest = next(
                            (
                                e
                                for e in reversed(events)
                                if e["event"] == "primitive" and e["tic_start"] <= tic
                            ),
                            None,
                        )
                        text = f"Tic {tic}" + (
                            f" | Step {latest['decision_idx']} | {latest['requested']}"
                            if latest
                            else ""
                        )
                        ImageDraw.Draw(image).text(
                            (12, 12), text, fill="white", stroke_width=2, stroke_fill="black"
                        )
                        import numpy as np

                        frame = np.array(image)
                    writer.append_data(frame)
                    count += 1
                if recorded_tics == 0:
                    break
                game.advance_action(1, True)
            actual = dict(final_tic=game.get_episode_time(), reward_total=game.get_total_reward())
    finally:
        game.close()
    matched = actual["final_tic"] == terminal["tic"] and math.isclose(
        actual["reward_total"], terminal["reward_total"], abs_tol=1e-8
    )
    with imageio.get_reader(video) as reader:
        shape = list(reader.get_data(0).shape)
        codec = reader.get_meta_data()["codec"]
    info = dict(
        **actual,
        expected_final_tic=terminal["tic"],
        expected_reward_total=terminal["reward_total"],
        matches_telemetry=matched,
        frames=count,
        fps=35,
        shape=shape,
        codec=codec,
        size=list(size),
        from_tic=lower,
        to_tic=upper,
        captions=captions,
        video_sha256=digest(video),
        lmp_sha256=manifest["episode_lmp_sha256"],
    )
    write_json(info_path, info)
    if not matched:
        raise ValueError("Replay final tic/reward differs from telemetry; inspect replay.json")
    if not count or codec != "h264" or shape != [size[1], size[0], 3]:
        raise ValueError("Replay encoding validation failed")
    print(json.dumps(dict(video=str(video), **info)), flush=True)
    return info
