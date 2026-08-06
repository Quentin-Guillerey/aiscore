import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session

from .db import get_db, engine
from .models import Base, Score, ScoreStatus, ReviewDecision
from .schemas import ScoreRequest, ScoreOut, ReviewRequest
from .scoring_engine import load_criteria, load_rcm_override_types, score_interaction, RCMFlag
from .scorecard_loader import CRITERIA_PATH, RCM_PATH, SCORECARD_VERSION

# Loaded once at import time. A criteria weight or RCM inconsistency raises
# immediately here — the service refuses to start rather than run with bad
# config. Same "fail loud, not silent" behavior as the standalone module.
criteria = load_criteria(CRITERIA_PATH)
rcm_override_types = load_rcm_override_types(RCM_PATH)

# The ONLY case where a caller may supply override_type explicitly, per the
# goodwill promotion rule in the project instructions: a category whose CSV
# override_type is partial_fail may be promoted to auto_zero when the goodwill
# amount crosses GOODWILL_AUTOZERO_LIMIT without approval. Human-confirmed in
# v1, so it arrives as an explicit caller decision rather than a derived one.
#
# Everything else derives override_type from rcm_violations.csv. Letting the
# caller set it freely would reintroduce exactly the defect this project was
# built to avoid: spreadsheet scorecards commonly let an evaluator pick a
# category and then select an unrelated override outcome, with nothing
# enforcing the link.
# A free field here would let a caller demote Fraud to a partial fail.
PROMOTABLE_CATEGORIES = {"Negligence"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # create_all is fine for the prototype stage. Before this touches real
    # interactions, replace with proper Alembic migrations — schema changes
    # need history, not just "make it match the models."
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="AISCORE", lifespan=lifespan)


def _parse_id(score_id: str) -> uuid.UUID:
    """A malformed id is caller error, not a server fault. Without this,
    uuid.UUID() raises ValueError inside the handler and FastAPI returns 500."""
    try:
        return uuid.UUID(score_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=422, detail=f"invalid score id '{score_id}'")


def _resolve_override_type(category: str, submitted: Optional[str]) -> Optional[str]:
    """Validate a caller-supplied override_type against the scorecard.

    Returns the value to hand the engine: None means "derive from the CSV",
    which is the normal path. A non-None return is an authorized promotion.
    """
    if category not in rcm_override_types:
        raise HTTPException(status_code=422, detail=f"unknown RCM category '{category}'")

    if submitted is None:
        return None

    csv_type = rcm_override_types[category]

    if submitted == csv_type:
        # Harmless but pointless. Treat as "derive" so there is exactly one
        # code path producing the effective override_type.
        return None

    if submitted != "auto_zero":
        raise HTTPException(
            status_code=422,
            detail=(
                f"override_type '{submitted}' is not permitted for category "
                f"'{category}'. Only promotion to auto_zero is accepted; "
                f"override_type is otherwise derived from the scorecard."
            ),
        )

    if csv_type != "partial_fail" or category not in PROMOTABLE_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"category '{category}' is not promotable to auto_zero. "
                f"Promotable categories: {sorted(PROMOTABLE_CATEGORIES)}."
            ),
        )

    return submitted


def _to_out(row: Score) -> ScoreOut:
    return ScoreOut(
        id=str(row.id),
        scorecard_version=row.scorecard_version,
        interaction_id=row.interaction_id,
        channel=row.channel,
        channel_transition=row.channel_transition,
        base_score=row.base_score,
        final_score=row.final_score,
        override_applied=row.override_applied,
        rcm_category=row.rcm_category,
        rcm_violation_id=row.rcm_violation_id,
        override_superseded=row.override_superseded,
        status=row.status.value,
        excluded_na=row.excluded_na or [],
    )


@app.post("/scores", response_model=ScoreOut)
def create_score(req: ScoreRequest, db: Session = Depends(get_db)):
    rcm_flag = None
    if req.rcm_flag:
        override_type = _resolve_override_type(
            req.rcm_flag.category, req.rcm_flag.override_type
        )
        rcm_flag = RCMFlag(
            category=req.rcm_flag.category,
            violation_id=req.rcm_flag.violation_id,
            override_type=override_type,
        )

    try:
        result = score_interaction(
            criteria=criteria,
            ratings=req.ratings,
            channel=req.channel,
            rcm_override_types=rcm_override_types,
            channel_transition=req.channel_transition,
            rcm_flag=rcm_flag,
        )
    except ValueError as e:
        # Bad input (missing rating, ungated partial credit, unknown
        # category) is a 422, not a 500 — the caller can fix the request.
        raise HTTPException(status_code=422, detail=str(e))

    row = Score(
        id=uuid.uuid4(),
        scorecard_version=SCORECARD_VERSION,
        interaction_id=req.interaction_id,
        channel=req.channel,
        channel_transition=req.channel_transition,
        ratings=req.ratings,
        excluded_na=result.excluded_na,
        base_score=result.base_score,
        final_score=result.final_score,
        override_applied=result.override_applied,
        rcm_category=rcm_flag.category if rcm_flag else None,
        rcm_violation_id=rcm_flag.violation_id if rcm_flag else None,
        status=ScoreStatus(result.status),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_out(row)


@app.get("/scores/{score_id}", response_model=ScoreOut)
def get_score(score_id: str, db: Session = Depends(get_db)):
    row = db.get(Score, _parse_id(score_id))
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _to_out(row)


@app.get("/scores", response_model=list[ScoreOut])
def list_scores(status: Optional[str] = None, db: Session = Depends(get_db)):
    """status=pending_human_review is the reviewer queue."""
    q = db.query(Score)
    if status:
        try:
            q = q.filter(Score.status == ScoreStatus(status))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown status '{status}'")
    rows = q.order_by(Score.created_at.desc()).all()
    return [_to_out(r) for r in rows]


@app.post("/scores/{score_id}/review", response_model=ScoreOut)
def review_score(score_id: str, req: ReviewRequest, db: Session = Depends(get_db)):
    row = db.get(Score, _parse_id(score_id))
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.status != ScoreStatus.PENDING_HUMAN_REVIEW:
        raise HTTPException(status_code=409, detail=f"score is '{row.status.value}', not pending review")

    if req.decision == "confirm":
        row.status = ScoreStatus.FINAL
        row.review_decision = ReviewDecision.CONFIRMED
        # final_score stays at the computed 0.0 — reviewer agreed the auto_zero holds.
    elif req.decision == "reject":
        # Reviewer determined the RCM flag was wrong. The score reverts to
        # base_score, but the rejected flag STAYS on the row: which categories
        # get overturned, and how often, is the signal that a category or its
        # violation definitions need work. Clearing it would delete the only
        # record that the flag was ever raised.
        row.final_score = row.base_score
        row.override_superseded = True
        row.status = ScoreStatus.FINAL
        row.review_decision = ReviewDecision.REJECTED
    else:
        raise HTTPException(status_code=422, detail="decision must be 'confirm' or 'reject'")

    # NOTE: reviewer identity is caller-asserted. There is no authentication
    # here and nothing prevents the original evaluator from reviewing their
    # own flag. Separation of reviewer from evaluator is a stated policy
    # requirement that is NOT enforced by this prototype.
    row.reviewed_by = req.reviewed_by
    row.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _to_out(row)
