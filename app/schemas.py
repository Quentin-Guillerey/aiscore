from typing import Optional

from pydantic import BaseModel


class RCMFlagIn(BaseModel):
    category: str
    violation_id: str

    # Normally omitted. override_type is derived from rcm_violations.csv so
    # that category and outcome cannot drift apart. Spreadsheet scorecards
    # commonly allow them to be selected independently, and that is the
    # defect this project exists to avoid.
    #
    # The single accepted use is the goodwill promotion: a partial_fail
    # category promoted to auto_zero when goodwill crosses the auto-zero
    # limit without approval. Human-confirmed in v1. Validated in
    # app.main._resolve_override_type; anything else is rejected with 422,
    # including any attempt to demote an auto_zero category.
    override_type: Optional[str] = None


class ScoreRequest(BaseModel):
    interaction_id: str
    channel: str
    channel_transition: bool = False
    ratings: dict[str, str]
    rcm_flag: Optional[RCMFlagIn] = None


class ReviewRequest(BaseModel):
    decision: str  # "confirm" | "reject"
    reviewed_by: str  # caller-asserted; not authenticated in the prototype


class ScoreOut(BaseModel):
    id: str
    scorecard_version: str
    interaction_id: str
    channel: str
    channel_transition: bool
    base_score: float
    final_score: float
    override_applied: Optional[str]
    rcm_category: Optional[str]
    rcm_violation_id: Optional[str]
    override_superseded: bool
    status: str
    excluded_na: list[str]
