"""Synchronous pixel observations and one-to-one execution of validated actions."""

import hashlib
import io
from dataclasses import asdict
from pathlib import Path

import numpy as np
import vizdoom as vzd
from PIL import Image

from autoqa.config import Act
from autoqa.io import EventLog

from .telemetry import TelemetryRecorder
from .types import ActionFeedback, AgentObservation

CANONICAL_BUTTONS = [
    vzd.Button.MOVE_FORWARD,
    vzd.Button.MOVE_BACKWARD,
    vzd.Button.MOVE_LEFT,
    vzd.Button.MOVE_RIGHT,
    vzd.Button.SPEED,
    vzd.Button.USE,
    vzd.Button.ATTACK,
    vzd.Button.TURN_LEFT,
    vzd.Button.TURN_RIGHT,
    vzd.Button.TURN_LEFT_RIGHT_DELTA,
]


def configured_game(config, *, size=(640, 480)):
    game = vzd.DoomGame()
    game.load_config(str(Path(vzd.scenarios_path) / (config.scenario + ".cfg")))
    game.set_available_buttons(CANONICAL_BUTTONS)
    game.set_available_game_variables([])
    game.set_screen_format(vzd.ScreenFormat.RGB24)
    game.set_screen_resolution(
        {
            (640, 480): vzd.ScreenResolution.RES_640X480,
            (1280, 720): vzd.ScreenResolution.RES_1280X720,
        }[size]
    )
    game.set_window_visible(False)
    game.set_mode(vzd.Mode.PLAYER)
    game.set_render_hud(True)
    game.set_render_messages(True)
    game.set_render_weapon(True)
    game.set_render_crosshair(True)
    game.set_console_enabled(False)
    game.set_sound_enabled(False)
    game.set_depth_buffer_enabled(False)
    game.set_audio_buffer_enabled(False)
    game.set_automap_buffer_enabled(False)
    game.set_labels_buffer_enabled(True)
    game.set_objects_info_enabled(True)
    game.set_sectors_info_enabled(True)
    game.set_episode_timeout(config.max_tics)
    game.set_seed(config.seed)
    game.add_game_args("+viz_nocheat 0")
    game.set_button_max_value(vzd.Button.TURN_LEFT_RIGHT_DELTA, 0)
    return game


def jpeg(array, size=None):
    image = Image.fromarray(array)
    if size:
        image = image.resize(size)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85)
    return output.getvalue()


class DoomEnv:
    def __init__(self, config, directory, calibration):
        self.config, self.directory = config, directory
        self.level = 1
        self._game = configured_game(config)
        self.telemetry = TelemetryRecorder(directory, calibration)
        self.log = EventLog(directory / "events.jsonl")
        self.decision_idx = 0
        self._observation_counter = 0
        self.change = 0.0
        self.previous_frames = []
        self._closed = False
        self._recording = False
        try:
            self._game.init()
            self._game.set_seed(config.seed)
            self._game.new_episode(str((directory / "episode.lmp").resolve()))
            self._recording = True
            self.start_tic = self._game.get_episode_time()
            self._screen = self._game.get_state().screen_buffer.copy()
            self.telemetry.record_step(self._game, "init", 0)
        except BaseException:
            self._game.close()
            raise

    def observe(self):
        self.decision_idx = self._observation_counter
        self._observation_counter += 1
        frame = jpeg(self._screen)
        (self.directory / "frames" / f"step_{self.decision_idx:04d}.jpg").write_bytes(frame)
        elapsed = self._game.get_episode_time() - self.start_tic
        return AgentObservation(
            self.decision_idx,
            elapsed,
            max(0, self.config.max_tics - elapsed),
            frame,
            tuple(reversed(self.previous_frames[-2:])),
            self.change,
            None,
            hashlib.sha256(frame).hexdigest(),
        )

    def execute(self, action: Act):
        # Revalidate at the engine boundary. Invalid calls are never clamped.
        action = Act.model_validate(action.model_dump())
        start = self._game.get_episode_time()
        before = self._screen.copy()
        executed = 0
        for index, move in enumerate(action.moves):
            if self.episode_finished():
                break
            vector = [0.0] * len(CANONICAL_BUTTONS)
            button = {
                "forward": 0,
                "backward": 1,
                "strafe_left": 2,
                "strafe_right": 3,
                "use": 5,
                "attack": 6,
            }.get(move.type)
            if button is not None:
                vector[button] = 1
            if action.run and move.type in {"forward", "backward"}:
                vector[4] = 1
            if move.type in {"turn_left", "turn_right"}:
                vector[9] = move.amount * (-1 if move.type == "turn_left" else 1)
            tic_start = self._game.get_episode_time()
            self._game.make_action(vector, move.tics)
            self.telemetry.record_step(
                self._game, "primitive", self.decision_idx, index, move, action.run
            )
            self.log(
                "primitive",
                decision_idx=self.decision_idx,
                primitive_idx=index,
                requested=move.model_dump(),
                run=action.run,
                button_vector=vector,
                tic_start=tic_start,
                tic_end=self._game.get_episode_time(),
                requested_tics=move.tics,
                source="pilot_act",
            )
            executed += 1
        state = self._game.get_state()
        if state:
            self._screen = state.screen_buffer.copy()
        a = np.array(Image.fromarray(before).convert("L").resize((160, 120)), dtype=float)
        b = np.array(Image.fromarray(self._screen).convert("L").resize((160, 120)), dtype=float)
        self.change = float(np.abs(a - b).mean() / 255 * 100)
        self.previous_frames.append(jpeg(before, (320, 240)))
        self.previous_frames = self.previous_frames[-2:]
        feedback = ActionFeedback(
            start, self._game.get_episode_time(), self.change, executed, self.episode_finished()
        )
        self.telemetry.record_step(self._game, "decision", self.decision_idx)
        self.log("action_feedback", decision_idx=self.decision_idx, **asdict(feedback))
        return feedback

    def episode_finished(self):
        return self._game.is_episode_finished()

    def outcome(self):
        if self._game.is_player_dead():
            return "player_dead"
        if self.episode_finished():
            return "success" if self._game.get_total_reward() > 0 else "timeout"
        return None

    def close(self):
        if not self._closed:
            try:
                if self._recording:
                    self.telemetry.record_step(self._game, "terminal", self.decision_idx)
                    self._game.new_episode()  # flush, never exposed to the Pilot
            finally:
                self._game.close()
                self._closed = True

    def player_session(self):
        owner = self

        class Session:
            level = 1

            def observe(self):
                return owner.observe()

            def execute(self, action):
                return owner.execute(action)

            def episode_finished(self):
                return owner.episode_finished()

        return Session()
