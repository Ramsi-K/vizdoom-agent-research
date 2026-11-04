"""Stable task/controls prefix, then bounded visible history and images."""

import base64
import hashlib
from pathlib import Path

from autoqa.doom.types import AgentObservation

L1_TASK = "You are an automated QA tester playing an unfamiliar first-person level. You are not told the objective. Work out what the level wants from what you can see, play it as a new player would, try to reach the end of the level, and report anything that confuses you or looks broken."
PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"


def prefix():
    task = (PROMPT_DIR / "level1_task.md").read_text().strip()
    if task != L1_TASK:
        raise ValueError("Pinned Level 1 task has changed")
    return task + "\n\n" + (PROMPT_DIR / "system_v1.md").read_text()


def prompt_hash():
    return hashlib.sha256(prefix().encode()).hexdigest()


def build(level: int, memory, obs: AgentObservation, guard_notes=None, briefing_l2=None):
    if level != 1:
        raise ValueError("Only Level 1 is implemented in M1")
    if briefing_l2 is not None or obs.plan_png is not None:
        raise ValueError("Level 1 may not receive a briefing or map")
    text = (
        f"Decision {obs.decision_idx}; elapsed {obs.tics_elapsed} tics; remaining {obs.tics_remaining} tics.\nScratchpad:\n{memory.scratchpad}\nRecent actions:\n"
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
    return [{"role": "system", "content": prefix()}, {"role": "user", "content": content}]
