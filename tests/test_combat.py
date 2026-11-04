"""M3.5 control and native-recording checks, separate from autonomous runs."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import vizdoom as vzd
from pydantic import ValidationError

from autoqa.agent.tools import parameters, tools_for_level
from autoqa.config import SCENARIOS, Act, CombatAct, RunConfig
from autoqa.doom.env import CANONICAL_BUTTONS, COMBAT_BUTTONS, DoomEnv, configured_game
from autoqa.doom.replay import replay
from autoqa.io import digest, write_json
from autoqa.prompts.builder import prefix


def act(kind, amount=1, fire=False):
    return CombatAct.model_validate(
        dict(
            moves=[dict(type=kind, amount=amount, fire=fire)],
            run=False,
            intent="offline control verification",
        )
    )


def directory(tmp_path):
    (tmp_path / "frames").mkdir()
    return tmp_path


def calibration():
    return json.loads(Path("configs/calibration.json").read_text())["measured"]


def test_combat_schema_is_opt_in_and_legacy_protocol_unchanged():
    assert (
        hashlib.sha256(prefix().encode()).hexdigest()
        == "db51a3e4a27a21c49fc4821e997a554e78301e4aff4bf34bfb5ec50c1779da88"
    )
    assert (
        hashlib.sha256(json.dumps(tools_for_level(1), sort_keys=True).encode()).hexdigest()
        == "9d2159f8bf5b3356c665886d1d56e34d286e26b9de89d4775ea4089473559895"
    )
    assert "Combat control extension" in prefix(2, "objective only", combat=True)
    schema = next(t["parameters"] for t in tools_for_level(2, combat=True) if t["name"] == "act")
    move = schema["properties"]["moves"]["items"]
    assert set(move["required"]) == {"type", "amount", "fire"}
    assert move["additionalProperties"] is False
    assert "weapon_7" in move["properties"]["type"]["enum"]
    with pytest.raises(ValidationError):
        Act.model_validate(act("turn_left", 10, True).model_dump())
    assert parameters(Act) != parameters(CombatAct)


def test_extended_freedoom_horizon_preserves_other_profiles():
    config = RunConfig(scenario="freedoom2_combat_map01", max_tics=21000, max_decisions=300)
    assert config.max_tics == 21000
    assert RunConfig().max_tics == 2100
    with pytest.raises(ValidationError):
        RunConfig(scenario="my_way_home", max_tics=4201)
    with pytest.raises(ValidationError):
        RunConfig(scenario="freedoom2_combat_map01", max_tics=21001)


@pytest.mark.parametrize(
    ("kind", "amount"),
    [("weapon_1", 2), ("reload", 2), ("look_up", 46), ("turn_left", 181), ("forward", 36)],
)
def test_invalid_combat_controls_rejected(kind, amount):
    with pytest.raises(ValidationError):
        act(kind, amount)
    with pytest.raises(ValidationError):
        CombatAct.model_validate(
            dict(moves=[dict(type="wait", amount=1)], run=False, intent="missing fire")
        )


def test_native_aim_and_simultaneous_fire_move(tmp_path):
    config = RunConfig(scenario="basic_combat", max_tics=300)
    env = DoomEnv(config, directory(tmp_path), calibration())
    try:
        before = env._game.get_game_variable(vzd.GameVariable.PITCH)
        env.execute(act("look_up", 15))
        assert env.telemetry.last["pitch"] == pytest.approx(before - 15, abs=0.03)
        env.execute(act("look_down", 15))
        assert env.telemetry.last["pitch"] == pytest.approx(before, abs=0.04)
        ammo = env.telemetry.last["selected_weapon_ammo"]
        env.execute(act("strafe_left", 20, True))
        assert env.telemetry.last["selected_weapon_ammo"] < ammo
        assert env._game.get_available_game_variables() == []
        lines = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
        primitive = [e for e in lines if e["event"] == "primitive"][-1]
        assert primitive["button_vector"][COMBAT_BUTTONS.index(vzd.Button.MOVE_LEFT)] == 1
        assert primitive["button_vector"][COMBAT_BUTTONS.index(vzd.Button.ATTACK)] == 1
        assert primitive["requested"] == dict(type="strafe_left", amount=20, fire=True)
        assert primitive["requested_tics"] == 20
        assert "kills" in env.telemetry.last and "damage_dealt" in env.telemetry.last
        assert not hasattr(env.observe(), "kills")
    finally:
        env.close()


def test_native_weapon_switch_and_unowned_weapon_requests(tmp_path):
    env = DoomEnv(RunConfig(scenario="freedoom2_combat_map01"), directory(tmp_path), calibration())
    try:
        pistol = env.telemetry.last["selected_weapon"]
        env.execute(act("weapon_1"))
        for _ in range(3):
            env.execute(act("wait", 20))
        assert env.telemetry.last["selected_weapon"] != pistol
        env.execute(act("weapon_2"))
        for _ in range(3):
            env.execute(act("wait", 20))
        assert env.telemetry.last["selected_weapon"] == pistol
        before = env.telemetry.last["weapon_7"]
        env.execute(act("weapon_7"))
        assert env.telemetry.last["weapon_7"] == before  # never grants a weapon
    finally:
        env.close()


@pytest.mark.parametrize("long_directory", [False, True])
def test_native_recording_replay_for_compound_controls(tmp_path, long_directory):
    if long_directory:
        tmp_path = tmp_path / ("long-recording-directory-" * 6)
        tmp_path.mkdir()
        assert len(str(tmp_path / "episode.lmp")) > 115
    config = RunConfig(scenario="freedoom2_combat_map01", max_tics=300)
    env = DoomEnv(config, directory(tmp_path), calibration())
    try:
        start = env.start_tic
        for primitive in [
            ("look_up", 5, False),
            ("look_down", 5, False),
            ("strafe_left", 10, True),
            ("turn_right", 10, True),
            ("weapon_2", 1, False),
            ("wait", 20, False),
        ]:
            env.observe()
            env.execute(act(*primitive))
    finally:
        env.close()
    profile = SCENARIOS[config.scenario]
    cfg = Path(vzd.scenarios_path) / (profile["cfg"] + ".cfg")
    iwad = Path(vzd.__file__).parent / "freedoom2.wad"
    m = dict(
        config=config.model_dump(),
        vizdoom_version=vzd.__version__,
        iwad_sha256=digest(iwad),
        scenario_cfg_sha256=digest(cfg),
        scenario_wad_sha256=digest(iwad),
        episode_lmp_sha256=digest(tmp_path / "episode.lmp"),
        outcome="max_decisions",
        scenario_profile=profile,
    )
    write_json(tmp_path / "manifest.json", m)
    info = replay(tmp_path, size=(640, 480), outcome_ending=False)
    assert (tmp_path / "replay.mp4").stat().st_size > 0
    assert info["matches_telemetry"]
    assert info["frames"] == env.telemetry.last["tic"] - start


@pytest.mark.parametrize(
    "name",
    ["basic_combat", "defend_center_combat", "deadly_corridor_combat", "freedoom2_combat_map01"],
)
def test_native_profiles_are_combat_enabled(name):
    game = configured_game(RunConfig(scenario=name, max_tics=50))
    try:
        game.init()
        game.new_episode()
        assert game.get_available_buttons() == COMBAT_BUTTONS
        assert game.get_doom_skill() == 3
        assert game.get_available_game_variables() == []
        assert game.get_state().screen_buffer.shape == (480, 640, 3)
    finally:
        game.close()


def test_legacy_buttons_remain_unchanged():
    game = configured_game(RunConfig())
    try:
        game.init()
        assert game.get_available_buttons() == CANONICAL_BUTTONS
    finally:
        game.close()


def test_combat_timeout_does_not_become_success_from_shaping_reward():
    env = object.__new__(DoomEnv)
    env.combat = True
    env.config = RunConfig(scenario="deadly_corridor_combat", max_tics=100)
    env._game = SimpleNamespace(
        is_player_dead=lambda: False,
        is_episode_finished=lambda: True,
        get_episode_time=lambda: 101,
        get_total_reward=lambda: 500,
    )
    assert env.outcome() == "timeout"
    env._game.get_episode_time = lambda: 0
    assert env.outcome() == "success"


def test_native_death_clock_and_full_recording(tmp_path):
    config = RunConfig(scenario="defend_center_combat", max_tics=2100)
    env = DoomEnv(config, directory(tmp_path), calibration())
    try:
        for _ in range(65):
            if env.episode_finished():
                break
            env.observe()
            env.execute(act("wait", 35))
        assert env.episode_finished()
        outcome = env.outcome()
        assert outcome in {"player_dead", "success"}
    finally:
        env.close()
    profile = SCENARIOS[config.scenario]
    cfg = Path(vzd.scenarios_path) / (profile["cfg"] + ".cfg")
    wad = Path(vzd.scenarios_path) / (profile["cfg"] + ".wad")
    iwad = Path(vzd.__file__).parent / "freedoom2.wad"
    write_json(
        tmp_path / "manifest.json",
        dict(
            config=config.model_dump(),
            vizdoom_version=vzd.__version__,
            iwad_sha256=digest(iwad),
            scenario_cfg_sha256=digest(cfg),
            scenario_wad_sha256=digest(wad),
            episode_lmp_sha256=digest(tmp_path / "episode.lmp"),
            outcome=outcome,
            scenario_profile=profile,
            action_schema="combat_v1",
        ),
    )
    info = replay(tmp_path, size=(640, 480), outcome_ending=False)
    assert info["matches_telemetry"]
    assert info["frames"] > 0
    assert info["combat_final"]["kills"] == env.telemetry.last["kills"]


def test_compound_model_action_is_preserved_and_private_reasoning_not_logged(observation):
    from test_no_scripted_policy import FakeClient, FakeSession, turn

    from autoqa.agent.pilot import Pilot

    moves = [
        dict(type="strafe_right", amount=10, fire=True),
        dict(type="turn_left", amount=15, fire=True),
    ]
    client = FakeClient(
        [
            turn(
                arguments=dict(
                    moves=moves, run=False, intent="model chooses its own compound action"
                )
            )
        ]
    )
    session = FakeSession(observation)
    events = []
    pilot = Pilot(
        RunConfig(scenario="basic_combat", max_decisions=1),
        session,
        client,
        lambda event, **kw: events.append(dict(event=event, **kw)),
    )
    pilot.run_episode()
    assert session.actions[0]["moves"] == moves
    saved = [e for e in events if e["event"] == "model_decision"]
    assert len(saved) == 1
    assert all(item["type"] != "reasoning" for item in saved[0]["output"])
    assert "encrypted_content" not in json.dumps(saved)
    assert json.loads(saved[0]["output"][0]["arguments"])["moves"] == moves


def test_extra_control_vectors_match_requests_without_fallback(tmp_path):
    config = RunConfig(scenario="basic_combat", max_tics=1000)
    env = DoomEnv(config, directory(tmp_path), calibration())
    try:
        for kind, button in [
            ("jump", "JUMP"),
            ("crouch", "CROUCH"),
            ("reload", "RELOAD"),
            ("altattack", "ALTATTACK"),
            ("next_weapon", "SELECT_NEXT_WEAPON"),
            ("previous_weapon", "SELECT_PREV_WEAPON"),
        ]:
            env.observe()
            env.execute(act(kind))
            rows = [
                json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()
            ]
            row = [e for e in rows if e["event"] == "primitive"][-1]
            assert row["requested"] == dict(type=kind, amount=1, fire=False)
            assert row["button_vector"][COMBAT_BUTTONS.index(getattr(vzd.Button, button))] == 1
    finally:
        env.close()


def test_repeat_guard_distinguishes_fire_modifier():
    from autoqa.agent.guards import Guards
    from autoqa.doom.types import ActionFeedback

    guard = Guards(RunConfig(scenario="basic_combat"))
    feedback = ActionFeedback(1, 2, 0, 1, False)
    for fire in [True, False, True]:
        assert guard.observe(act("wait", 1, fire), feedback) is None


def test_experiment_runner_executes_only_selected_slot(tmp_path, monkeypatch):
    import autoqa.doom.replay
    import autoqa.run
    import scripts.run_m35 as runner

    calls = []

    def fake(config):
        calls.append(config.scenario)
        p = tmp_path / "synthetic_run"
        p.mkdir()
        return p, dict(outcome="max_decisions", total_usd=0.01)

    monkeypatch.setattr(autoqa.run, "run_episode", fake)
    monkeypatch.setattr(autoqa.doom.replay, "replay", lambda p: dict(matches_telemetry=True))
    monkeypatch.setattr(
        runner, "write_combat_summary", lambda dirs, out: dict(recorded_cost_usd=0.01)
    )
    out = tmp_path / "plan"
    assert runner.main(["--out", str(out)]) == 0
    assert calls == []
    assert runner.main(["--out", str(out), "--index", "0", "--execute"]) == 0
    assert calls == ["basic_combat"]
    plan = json.loads((out / "experiment.json").read_text())
    assert [s["status"] for s in plan["slots"]] == ["recorded", "pending", "pending", "pending"]
    with pytest.raises(SystemExit):
        runner.main(["--out", str(out), "--index", "0", "--execute"])
    assert calls == ["basic_combat"]


def test_native_target_kill_and_success_replay(tmp_path):
    # Engine-label aiming is confined to this offline calibration fixture.
    # Autonomous runs never receive these labels or this action sequence.
    import math

    config = RunConfig(scenario="basic_combat", max_tics=300)
    env = DoomEnv(config, directory(tmp_path), calibration())
    try:
        state = env._game.get_state()
        target = next(label for label in state.labels if label.object_name != "DoomPlayer")
        bearing = math.degrees(math.atan(((target.x + target.width / 2) - 320) / 320))
        if abs(bearing) >= 1:
            env.execute(act("turn_right" if bearing > 0 else "turn_left", round(abs(bearing))))
        for _ in range(30):
            if env.episode_finished():
                break
            env.observe()
            env.execute(act("attack", 10))
        assert env.outcome() == "success"
        assert env.telemetry.last["kills"] >= 1
    finally:
        env.close()
    profile = SCENARIOS[config.scenario]
    cfg = Path(vzd.scenarios_path) / (profile["cfg"] + ".cfg")
    wad = Path(vzd.scenarios_path) / (profile["cfg"] + ".wad")
    iwad = Path(vzd.__file__).parent / "freedoom2.wad"
    write_json(
        tmp_path / "manifest.json",
        dict(
            config=config.model_dump(),
            vizdoom_version=vzd.__version__,
            iwad_sha256=digest(iwad),
            scenario_cfg_sha256=digest(cfg),
            scenario_wad_sha256=digest(wad),
            episode_lmp_sha256=digest(tmp_path / "episode.lmp"),
            outcome="success",
            scenario_profile=profile,
            action_schema="combat_v1",
        ),
    )
    info = replay(tmp_path, size=(640, 480))
    assert info["matches_telemetry"]
    assert info["combat_final"]["kills"] >= 1
    assert info["ending_frames"] > 0
