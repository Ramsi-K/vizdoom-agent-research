"""The entire engine interface visible to the Pilot."""

from dataclasses import dataclass
from typing import Literal, Protocol

from autoqa.config import Act


@dataclass(frozen=True)
class AgentObservation:
    decision_idx: int
    tics_elapsed: int
    tics_remaining: int
    frame_jpeg: bytes
    prev_frames_jpeg: tuple[bytes, ...]
    frame_change_pct: float
    plan_png: bytes | None
    frame_sha256: str


@dataclass(frozen=True)
class ActionFeedback:
    tic_start: int
    tic_end: int
    frame_change_pct: float
    primitives_executed: int
    finished: bool


class PlayerSession(Protocol):
    level: Literal[1]

    def observe(self) -> AgentObservation: ...
    def execute(self, action: Act) -> ActionFeedback: ...
    def episode_finished(self) -> bool: ...
