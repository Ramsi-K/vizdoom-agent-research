"""Synchronous pixel observations and one-to-one execution of validated actions."""

import hashlib
import io
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path

import numpy as np
import vizdoom as vzd
from PIL import Image

from autoqa.config import SCENARIOS, Act, CombatAct, combat_enabled
from autoqa.io import EventLog

from .demo_path import native_demo_path
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

COMBAT_BUTTONS = CANONICAL_BUTTONS + [
    vzd.Button.LOOK_UP_DOWN_DELTA,
    *[getattr(vzd.Button, f"SELECT_WEAPON{i}") for i in range(1, 8)],
    vzd.Button.SELECT_NEXT_WEAPON,
    vzd.Button.SELECT_PREV_WEAPON,
    vzd.Button.JUMP,
    vzd.Button.CROUCH,
    vzd.Button.RELOAD,
    vzd.Button.ALTATTACK,
]


def buttons_for(config):
    return COMBAT_BUTTONS if combat_enabled(config.scenario) else CANONICAL_BUTTONS


def configured_game(config, *, size=(640, 480)):
    game = vzd.DoomGame()
    profile = SCENARIOS[config.scenario]
    game.load_config(str(Path(vzd.scenarios_path) / (profile["cfg"] + ".cfg")))
    game.set_available_buttons(buttons_for(config))
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
    game.set_doom_map(profile["map"])
    if profile["nomonsters"]:
        game.add_game_args("-nomonsters")
    if combat_enabled(config.scenario):
        game.set_doom_skill(profile["skill"])
        game.add_game_args("+freelook 1")
        game.set_button_max_value(vzd.Button.LOOK_UP_DOWN_DELTA, 0)
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
        self.level = config.level
        self.combat = combat_enabled(config.scenario)
        self.buttons = buttons_for(config)
        self._plan_png = None
        self._game = configured_game(config)
        self.telemetry = TelemetryRecorder(
            directory,
            calibration,
            combat=self.combat,
            track_armor_goal=not self.combat or config.scenario == "deadly_corridor_combat",
        )
        self.log = EventLog(directory / "events.jsonl")
        self.decision_idx = 0
        self._observation_counter = 0
        self.change = 0.0
        self.previous_frames = []
        self._closed = False
        self._recording = False
        self._demo_paths = ExitStack()
        try:
            self._game.init()
            self._game.set_seed(config.seed)
            demo = self._demo_paths.enter_context(native_demo_path(directory / "episode.lmp"))
            self._game.new_episode(demo)
            self._recording = True
            self.start_tic = self._game.get_episode_time()
            self._screen = self._game.get_state().screen_buffer.copy()
            self.telemetry.record_step(self._game, "init", 0)
            if self.level == 3:
                import json

                from .plan_renderer import render_plan

                geometry = json.loads((directory / "geometry.json").read_text())
                self._plan_png = render_plan(
                    geometry, self.telemetry.last, scenario=config.scenario
                )
                (directory / "plan_l3.png").write_bytes(self._plan_png)
                self.log(
                    "static_plan",
                    tic=self.start_tic,
                    sha256=hashlib.sha256(self._plan_png).hexdigest(),
                    render_count=1,
                )
        except BaseException:
            self._game.close()
            self._demo_paths.close()
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
            self._plan_png if self.level == 3 else None,
            hashlib.sha256(frame).hexdigest(),
        )

    def execute(self, action: Act):
        # Revalidate at the engine boundary. Invalid calls are never clamped.
        action = (CombatAct if self.combat else Act).model_validate(action.model_dump())
        start = self._game.get_episode_time()
        before = self._screen.copy()
        executed = 0
        for index, move in enumerate(action.moves):
            if self.episode_finished():
                break
            vector = [0.0] * len(self.buttons)
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
            if self.combat:
                if move.fire:
                    vector[6] = 1
                if move.type.startswith("look_"):
                    vector[self.buttons.index(vzd.Button.LOOK_UP_DOWN_DELTA)] = move.amount * (
                        1 if move.type == "look_up" else -1
                    )
                names = {
                    **{f"weapon_{i}": f"SELECT_WEAPON{i}" for i in range(1, 8)},
                    "next_weapon": "SELECT_NEXT_WEAPON",
                    "previous_weapon": "SELECT_PREV_WEAPON",
                    "jump": "JUMP",
                    "crouch": "CROUCH",
                    "reload": "RELOAD",
                    "altattack": "ALTATTACK",
                }
                if move.type in names:
                    vector[self.buttons.index(getattr(vzd.Button, names[move.type]))] = 1
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
        if self.combat and self.episode_finished():
            profile = SCENARIOS[self.config.scenario]
            if profile["goal"] == "kill":
                return (
                    "success"
                    if self._game.get_game_variable(vzd.GameVariable.KILLCOUNT) > 0
                    else "timeout"
                )
            timed_out = self._game.get_episode_time() >= self.config.max_tics
            if profile["goal"] == "survive":
                return (
                    "success"
                    if timed_out and self.config.max_tics >= profile["timeout"]
                    else "timeout"
                )
            # Natural objective exit is distinct from a positive shaping reward.
            return "timeout" if timed_out else "success"
        if self.episode_finished():
            if self.config.scenario == "health_gathering_supreme":
                return (
                    "success"
                    if self._game.get_episode_time() - self.start_tic
                    >= SCENARIOS[self.config.scenario]["timeout"]
                    else "timeout"
                )
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
                self._demo_paths.close()
                self._closed = True

    def player_session(self):
        owner = self

        class Session:
            level = owner.level

            def observe(self):
                return owner.observe()

            def execute(self, action):
                return owner.execute(action)

            def episode_finished(self):
                return owner.episode_finished()

        return Session()
