"""Stable task/controls prefix, then bounded visible history and images."""

import base64
import hashlib
from pathlib import Path

from autoqa.doom.types import AgentObservation

L1_TASK = "You are an automated QA tester playing an unfamiliar first-person level. You are not told the objective. Work out what the level wants from what you can see, play it as a new player would, try to reach the end of the level, and report anything that confuses you or looks broken."
PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"


def prefix(level=1, briefing_l2=None, *, combat=False):
    task = (PROMPT_DIR / "level1_task.md").read_text().strip()
    if task != L1_TASK:
        raise ValueError("Pinned Level 1 task has changed")
    if level not in (1, 2, 3):
        raise ValueError("Unknown information level")
    if level == 1 and briefing_l2 is not None:
        raise ValueError("Level 1 may not receive a briefing")
    if level > 1 and not briefing_l2:
        raise ValueError("Guided levels require a briefing")
    intro = (
        task
        if level == 1
        else "You are an automated QA tester playing this level. Attempt the supplied objective and report confusing or apparently broken behavior."
    )
    result = intro + "\n\n" + (PROMPT_DIR / "system_v3.md").read_text()
    if combat:
        result += "\n\n" + (PROMPT_DIR / "combat_controls_v1.md").read_text()
    if level > 1:
        result += "\n\n" + briefing_l2
    if level == 3:
        result += "\nA frozen initial floor plan is supplied. Its start marker never updates. Localize yourself from visual landmarks."
    if level > 1:
        result += "\n\n" + (PROMPT_DIR / f"level{level}_navigation_v1.md").read_text()
    return result


def prompt_hash(level=1, briefing_l2=None, *, combat=False):
    return hashlib.sha256(prefix(level, briefing_l2, combat=combat).encode()).hexdigest()


def build(
    level: int, memory, obs: AgentObservation, guard_notes=None, briefing_l2=None, *, combat=False
):
    if level not in (1, 2, 3):
        raise ValueError("Unknown information level")
    if level < 3 and obs.plan_png is not None:
        raise ValueError("Only Level 3 may receive a map")
    if level == 3 and obs.plan_png is None:
        raise ValueError("Level 3 requires its frozen initial map")
    stable = prefix(level, briefing_l2, combat=combat)
    text = (
        f"Decision {obs.decision_idx}; elapsed {obs.tics_elapsed} tics; remaining {obs.tics_remaining} tics.\nLandmark/outcome ledger (model-written, 1500 characters maximum):\n{memory.scratchpad or '(empty; record landmarks as you observe them)'}\nRecent actions and uncertain locate evidence:\n"
        + "\n".join(memory.history)
    )
    if guard_notes:
        text += "\n" + guard_notes
    content = [{"type": "input_text", "text": text}]
    for label, data in [(f"t-{i + 1}", b) for i, b in enumerate(obs.prev_frames_jpeg)] + [
        ("current frame", obs.frame_jpeg)
    ]:
        content.extend(
            [
                {"type": "input_text", "text": label},
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
                },
            ]
        )
    items = [{"role": "system", "content": stable}]
    if level == 3:
        items.append(
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Frozen initial floor plan"},
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,"
                        + base64.b64encode(obs.plan_png).decode(),
                    },
                ],
            }
        )
    return items + [{"role": "user", "content": content}]
