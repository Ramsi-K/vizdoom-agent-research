"""Re-render a recorded demo without sending actions or commands to the engine."""

import json
import math
from collections import deque
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import vizdoom as vzd
from PIL import Image, ImageDraw, ImageFont

from autoqa.config import SCENARIOS, RunConfig
from autoqa.io import digest, write_json

from .env import configured_game


def result_frame(frame, title, detail, *, held=False):
    """Label a held/recap frame; never paint a fictional game HUD."""
    image = Image.fromarray(frame).convert("RGBA")
    overlay = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(overlay)
    scale = image.width / 1280
    draw.rectangle((0, 0, image.width, round(155 * scale)), fill=(10, 18, 24, 235))

    def font(size):
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", max(12, round(size * scale)))
        except OSError:
            return ImageFont.load_default(size=max(12, round(size * scale)))

    draw.text((24 * scale, 18 * scale), title, font=font(42), fill="#60F0A0")
    draw.text((24 * scale, 78 * scale), detail, font=font(23), fill="white")
    draw.text(
        (24 * scale, 118 * scale),
        "Result card · captured approach frame held"
        if held
        else "Recorded pickup footage · replayed at half speed",
        font=font(16),
        fill="#CBD5E1",
    )
    return np.asarray(Image.alpha_composite(image, overlay).convert("RGB"))


def replay(
    directory: Path,
    *,
    size=(1280, 720),
    from_tic=None,
    to_tic=None,
    captions=False,
    outcome_ending=True,
    output_name=None,
):
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
    # A natural level exit can reset both episode/server clocks to zero.
    # Preserve the raw telemetry; reconstruct elapsed tics by counting native playback.
    clock_reset = terminal["tic"] < telemetry[0]["tic"]
    if clock_reset and manifest.get("outcome") != "success":
        raise ValueError("Unexpected terminal clock regression; inspect raw telemetry")
    upper = to_tic if to_tic is not None else None if clock_reset else terminal["tic"]
    if (
        lower < telemetry[0]["tic"]
        or (upper is not None and not clock_reset and upper > terminal["tic"])
        or (upper is not None and lower > upper)
        or (lower == upper and terminal["tic"] > telemetry[0]["tic"])
    ):
        raise ValueError("Replay tic slice must lie inside the recorded interval")
    if output_name and (
        Path(output_name).name != output_name or Path(output_name).suffix != ".mp4"
    ):
        raise ValueError("Output name must be a new .mp4 filename inside the run directory")
    video = directory / (
        output_name
        or ("replay.mp4" if from_tic is None and to_tic is None else f"replay_{lower}_{upper}.mp4")
    )
    info_path = video.with_suffix(".json")
    if video.exists() or info_path.exists():
        raise FileExistsError("Replay output exists; preserve it or choose another tic slice")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    game = configured_game(config, size=size)
    # A demo flushed before natural termination has a trailing native tic.
    # End playback at the logged action boundary, without injecting any action.
    recorded_tics = None if clock_reset else terminal["tic"] - telemetry[0]["tic"]
    if recorded_tics is not None and recorded_tics > 0:
        game.set_episode_timeout(recorded_tics)
    count = 0
    advanced = 0
    present_success = (
        outcome_ending
        and from_tic is None
        and to_tic is None
        and manifest.get("outcome") == "success"
    )
    tail = deque(maxlen=35 if present_success else 0)
    ending_frames = 0
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
                if state is not None and (
                    (lower <= tic and (upper is None or tic < upper)) or recorded_tics == 0
                ):
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
                        frame = np.array(image)
                    writer.append_data(frame)
                    if present_success:
                        tail.append(frame.copy())
                    count += 1
                if recorded_tics == 0:
                    break
                game.advance_action(1, True)
                advanced += 1
            actual = dict(final_tic=game.get_episode_time(), reward_total=game.get_total_reward())
            matched = actual["final_tic"] == terminal["tic"] and math.isclose(
                actual["reward_total"], terminal["reward_total"], abs_tol=1e-8
            )
            if present_success and matched and tail:
                # Repeat only real captured frames. Do not advance the simulator or
                # manufacture post-win gameplay after its natural terminal state.
                for frame in tail:
                    recap = result_frame(
                        frame, "VICTORY REPLAY · 0.5x", "The final recorded second, slowed down"
                    )
                    writer.append_data(recap)
                    writer.append_data(recap)
                    ending_frames += 2
                detail = "Level objective completed"
                if terminal.get("armor", 0) > telemetry[0].get("armor", 0):
                    detail = f"Armor collected · final armor: {terminal['armor']:.0f}%"
                hold = result_frame(tail[0], "LEVEL COMPLETE", detail, held=True)
                for _ in range(3 * 35):
                    writer.append_data(hold)
                    ending_frames += 1
    finally:
        game.close()
    with imageio.get_reader(video) as reader:
        shape = list(reader.get_data(0).shape)
        codec = reader.get_meta_data()["codec"]
    info = dict(
        **actual,
        expected_final_tic=terminal["tic"],
        expected_reward_total=terminal["reward_total"],
        matches_telemetry=matched,
        frames=count + ending_frames,
        gameplay_frames=count,
        ending_frames=ending_frames,
        ending_type="recorded_recap_and_result_hold" if ending_frames else None,
        fps=35,
        shape=shape,
        codec=codec,
        size=list(size),
        from_tic=lower,
        to_tic=upper if upper is not None else telemetry[0]["tic"] + advanced,
        captions=captions,
        video_sha256=digest(video),
        lmp_sha256=manifest["episode_lmp_sha256"],
    )
    if clock_reset:
        info.update(
            terminal_clock_status="native_reset_reconstructed_by_replay",
            reconstructed_final_tic=telemetry[0]["tic"] + advanced,
            reconstructed_tics_used=advanced,
            reconstruction_source="counted advance_action(1) calls in native recording",
        )
    write_json(info_path, info)
    if not matched:
        raise ValueError("Replay final tic/reward differs from telemetry; inspect replay.json")
    if not count or codec != "h264" or shape != [size[1], size[0], 3]:
        raise ValueError("Replay encoding validation failed")
    print(json.dumps(dict(video=str(video), **info)), flush=True)
    return info
