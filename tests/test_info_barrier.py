import ast
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest

from autoqa.agent.client import InformationLeakError, sanitize_request
from autoqa.agent.memory import Memory
from autoqa.doom.types import AgentObservation
from autoqa.prompts.builder import L1_TASK, build


def test_agent_and_prompts_cannot_import_engine_or_eval():
    banned = ("vizdoom", "autoqa.doom.env", "autoqa.doom.telemetry", "autoqa.eval", "autoqa.m0")
    for directory in ("autoqa/agent", "autoqa/prompts"):
        for path in Path(directory).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else []
                )
                assert not any(name.startswith(banned) for name in names), path


def test_l1_prompt_and_observation_are_narrow_and_frozen(observation):
    request = build(1, Memory(), observation)
    assert request[0]["content"].startswith(L1_TASK)
    assert "GreenArmor" not in str(request)
    assert "objective is" not in str(request)
    assert {f.name for f in fields(AgentObservation)} == {
        "decision_idx",
        "tics_elapsed",
        "tics_remaining",
        "frame_jpeg",
        "prev_frames_jpeg",
        "frame_change_pct",
        "plan_png",
        "frame_sha256",
    }
    with pytest.raises(FrozenInstanceError):
        observation.plan_png = b"forbidden"
    sanitize_request(request, observation)


def test_information_level_gates(observation):
    with pytest.raises(ValueError):
        build(1, Memory(), replace(observation, plan_png=b"map"))
    with pytest.raises(ValueError):
        build(1, Memory(), observation, briefing_l2="mechanics")
    with pytest.raises(ValueError):
        build(2, Memory(), observation)
    with pytest.raises(ValueError):
        build(3, Memory(), observation, briefing_l2="mechanics")
    briefing = "ONLY_GUIDED_OBJECTIVE_AND_MECHANICS"
    plan = b"initial-static-map-fixture"
    guided = build(2, Memory(), observation, briefing_l2=briefing)
    informed = build(3, Memory(), replace(observation, plan_png=plan), briefing_l2=briefing)
    assert briefing not in str(build(1, Memory(), observation))
    assert briefing in str(guided) and briefing in str(informed)
    assert "data:image/png" not in str(guided)
    assert "data:image/png" in str(informed)
    sanitize_request(guided, observation, 2)
    sanitize_request(informed, replace(observation, plan_png=plan), 3)
    with pytest.raises(InformationLeakError):
        sanitize_request(informed, observation, 1)


@pytest.mark.parametrize("key", ["pos_x", "pos_y", "labels_buffer", "object_position", "sector"])
def test_telemetry_keys_are_rejected(key, observation):
    with pytest.raises(InformationLeakError):
        sanitize_request(
            [{"role": "user", "content": [{"type": "input_text", "text": f'{{"{key}":120}}'}]}],
            observation,
        )


def test_extra_image_is_rejected(observation):
    request = build(1, Memory(), observation)
    request[-1]["content"].append(
        {"type": "input_image", "image_url": "data:image/jpeg;base64,extra"}
    )
    with pytest.raises(InformationLeakError):
        sanitize_request(request, observation)


def test_swapping_current_frame_for_private_image_is_rejected(observation):
    request = build(1, Memory(), observation)
    next(c for c in request[-1]["content"] if c["type"] == "input_image")["image_url"] = (
        "data:image/jpeg;base64,private-label-buffer"
    )
    with pytest.raises(InformationLeakError):
        sanitize_request(request, observation, 1)
