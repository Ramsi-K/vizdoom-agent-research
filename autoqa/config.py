"""M1 run settings, action validation and explicit Meta pricing."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Move(StrictModel):
    type: Literal[
        "forward",
        "backward",
        "strafe_left",
        "strafe_right",
        "turn_left",
        "turn_right",
        "use",
        "attack",
        "wait",
    ]
    amount: int = Field(ge=1, le=180)

    @model_validator(mode="after")
    def limits(self):
        cap = 180 if self.type.startswith("turn_") else 1 if self.type == "use" else 35
        if self.amount > cap:
            raise ValueError(f"{self.type} amount exceeds {cap}")
        return self

    @property
    def tics(self):
        return 1 if self.type.startswith("turn_") else self.amount


class Act(StrictModel):
    moves: list[Move] = Field(min_length=1, max_length=4)
    run: bool
    intent: str = Field(max_length=200)

    @model_validator(mode="after")
    def total_tics(self):
        if sum(m.tics for m in self.moves) > 70:
            raise ValueError("An act call may use at most 70 tics")
        return self


class Note(StrictModel):
    text: str = Field(max_length=1500)
    mode: Literal["append", "replace"]


class Issue(StrictModel):
    category: Literal[
        "UNCLEAR_OBJECTIVE",
        "NAVIGATION_CONFUSION",
        "DEAD_END",
        "BLOCKED_PATH",
        "INTERACTION_NO_EFFECT",
        "VISUAL_AMBIGUITY",
        "MECHANIC_UNCLEAR",
        "SOFTLOCK_SUSPECTED",
        "UNREACHABLE_SUSPECTED",
        "OTHER",
    ]
    severity: Literal["low", "medium", "high"]
    summary: str = Field(min_length=1, max_length=300)
    evidence_steps: list[int] = Field(max_length=60)
    confidence: float = Field(ge=0, le=1)


class Finish(StrictModel):
    status: Literal["gave_up", "blocked"]
    rationale: str = Field(min_length=1, max_length=500)


class RunConfig(StrictModel):
    scenario: Literal["my_way_home"] = "my_way_home"
    level: Literal[1] = 1
    seed: int = 101
    model: Literal["muse-spark-1.3", "muse-spark-1.3-contributor"] = "muse-spark-1.3"
    effort: Literal["minimal", "low", "medium"] = "low"
    max_decisions: int = Field(default=60, ge=1, le=1000)
    max_tics: int = Field(default=2100, ge=1, le=2100)
    max_usd: float = Field(default=0.25, gt=0)
    wall_seconds: float = Field(default=900.0, gt=0)
    request_timeout: float = Field(default=45.0, gt=0)
    max_output_tokens: int = Field(default=3000, ge=3000, le=8000)


# USD per million tokens, source: Meta pricing-rate-limits, checked 2026-11-04.
PRICES = {"muse-spark-1.3": (1.25, 0.15, 4.25), "muse-spark-1.3-contributor": (0.10, 0.002, 0.20)}


def usage_cost(model, usage):
    incoming, cached, outgoing = PRICES[model]
    cached_tokens = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
    return (
        (usage["input_tokens"] - cached_tokens) * incoming
        + cached_tokens * cached
        + usage["output_tokens"] * outgoing
    ) / 1_000_000
