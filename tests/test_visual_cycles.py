import io
from dataclasses import replace

import pytest
from PIL import Image

from autoqa.agent.client import InformationLeakError, sanitize_request
from autoqa.agent.guards import Guards
from autoqa.agent.memory import Memory
from autoqa.config import RunConfig
from autoqa.prompts.builder import build


def frame(color, hud="black"):
    image = Image.new("RGB", (640, 480), color)
    image.paste(Image.new("RGB", (640, 120), hud), (0, 360))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=95)
    return out.getvalue()


def test_alternating_views_trigger_even_with_different_actions():
    guards = Guards(RunConfig())
    views = [frame("red"), frame("blue")]
    warnings = [guards.observe_view(views[d % 2], d) for d in range(16)]
    indices = [d for d, warning in enumerate(warnings) if warning]
    assert indices == [10, 14]
    assert warnings[10]["matched_decisions"] == [4, 0]
    assert warnings[10]["source"] == "visible_rgb_only"
    assert "not proof" in warnings[10]["text"]
    assert len(guards.views) == 16


def test_hud_changes_do_not_hide_cycle_and_novel_view_clears_warning():
    guards = Guards(RunConfig())
    for d in range(10):
        warning = guards.observe_view(frame("red", "white" if d % 2 else "black"), d)
    assert warning and guards.warning
    assert guards.observe_view(frame("blue"), 10) is None
    assert guards.warning is None


def test_adjacent_repeats_and_distinct_views_do_not_trigger():
    guards = Guards(RunConfig())
    for d in range(6):
        assert guards.observe_view(frame("red"), d) is None
    guards = Guards(RunConfig())
    for d in range(12):
        assert guards.observe_view(frame((20 * d,) * 3), d) is None
    for d in range(12, 80):
        guards.observe_view(frame("blue"), d)
    assert len(guards.views) == 48
    assert not Guards(RunConfig()).views  # never shared between episodes


def test_model_ledger_and_uncertain_locate_survive_next_decision(observation):
    memory = Memory()
    memory.record_locate(0, "panel", dict(found=False, matches=[], status="could_not_verify"))
    memory.record(0, "act use; visible dFrame=0.0%; outcome unknown")
    ledger = "d1: gray panels | tried: d0 use | observed: looks unchanged | uncertain: decorative?"
    memory.note(ledger, "replace")
    request = build(1, memory, observation)
    assert ledger in str(request)
    assert "could_not_verify" in str(request) and '"identity_verified": false' in str(request)
    assert len(memory.history) == 1
    memory.record(1, "act forward")
    assert memory.scratchpad == ledger  # harness never invents or revises landmark semantics
    for d in range(2, 25):
        memory.record(d, "act forward")
    assert len(memory.history) == 20
    memory.note("x" * 1500, "append")
    assert memory.scratchpad == "x" * 1500


@pytest.mark.parametrize("level", [1, 2, 3])
def test_ledger_and_cycle_text_respect_each_information_level(level, observation):
    memory = Memory()
    memory.note("gray panels | tried d0 use | observed looks unchanged | uncertain", "replace")
    obs = replace(observation, plan_png=b"frozen-map" if level == 3 else None)
    request = build(
        level,
        memory,
        obs,
        "Visual-cycle warning: similar views are not proof of position.",
        "GUIDED_BRIEFING" if level > 1 else None,
    )
    sanitize_request(request, obs, level)
    assert "gray panels" in str(request) and "Visual-cycle warning" in str(request)
    assert ("GUIDED_BRIEFING" in str(request)) == (level > 1)
    assert ("data:image/png" in str(request)) == (level == 3)
    memory.note('private telemetry: {"pos_x": 120}', "replace")
    with pytest.raises(InformationLeakError):
        sanitize_request(
            build(level, memory, obs, briefing_l2="GUIDED_BRIEFING" if level > 1 else None),
            obs,
            level,
        )
