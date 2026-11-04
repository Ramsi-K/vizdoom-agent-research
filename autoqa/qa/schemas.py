"""Separate model observations, physical trace evidence and causal hypotheses."""

from typing import Annotated, Literal

from pydantic import Field

from autoqa.config import Issue, StrictModel

Category = Issue.model_fields["category"].annotation
Step = Annotated[int, Field(ge=0)]


class ReviewFinding(StrictModel):
    category: Category
    severity: Literal["low", "medium", "high"]
    summary: str = Field(min_length=1, max_length=300)
    rationale: str = Field(max_length=500)
    evidence_steps: list[Step] = Field(min_length=1, max_length=60)
    confidence: float = Field(ge=0, le=1)
    recommended_developer_check: str = Field(max_length=300)


class EpisodeQAReview(StrictModel):
    episode_summary: str = Field(max_length=1500)
    inferred_objective: str = Field(max_length=500)
    findings: list[ReviewFinding] = Field(max_length=40)


class IssueRecord(StrictModel):
    issue_id: str
    run_id: str
    scenario_id: str
    level: int
    seed: int
    source: str
    category: Category
    severity: Literal["low", "medium", "high"]
    summary: str
    rationale: str = ""
    evidence_steps: list[Step]
    tic_range: list[int] | None = None
    model_confidence: float = Field(ge=0, le=1)
    trace_verification_status: Literal[
        "behavior_confirmed", "behavior_contradicted", "unverifiable"
    ] = "unverifiable"
    verifier_rule: str = "not_run"
    verifier_notes: str = ""
    verifier_metrics: dict = Field(default_factory=dict)
    causal_hypothesis: str | None = None
    sensitivity_class: str | None = None
    recommended_developer_check: str = ""
    evidence_frames: list[str] = Field(default_factory=list)
    replay_clip_path: str | None = None
    design_defect_confirmed: bool = False
    evidence_limitations: list[str] = Field(default_factory=list)
