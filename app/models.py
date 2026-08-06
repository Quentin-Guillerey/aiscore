import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, Boolean, DateTime, Enum, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import TypeDecorator, CHAR
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _utcnow():
    """datetime.utcnow() is deprecated in 3.12 and returns a naive datetime.
    An audit trail should not carry ambiguous timestamps."""
    return datetime.now(timezone.utc)


class GUID(TypeDecorator):
    """
    Portable UUID column. Uses Postgres' native UUID type where available,
    falls back to CHAR(36) on SQLite so the standalone/exe build works.

    Previously this was sqlalchemy.dialects.postgresql.UUID directly, which
    is Postgres-only and broke the standalone path.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class ScoreStatus(str, enum.Enum):
    FINAL = "final"
    PENDING_HUMAN_REVIEW = "pending_human_review"


class ReviewDecision(str, enum.Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class Score(Base):
    __tablename__ = "scores"

    id = Column(GUID(), primary_key=True, default=uuid.uuid4)
    scorecard_version = Column(String, nullable=False)

    interaction_id = Column(String, nullable=False, index=True)
    channel = Column(String, nullable=False)
    channel_transition = Column(Boolean, nullable=False, default=False)

    ratings = Column(JSON, nullable=False)           # {criterion_id: rating}, raw submission for audit
    excluded_na = Column(JSON, nullable=False, default=list)

    base_score = Column(Float, nullable=False)
    final_score = Column(Float, nullable=False)       # candidate value; not authoritative until status == FINAL

    # The RCM flag AS SUBMITTED. These are never cleared, including when a
    # reviewer rejects the flag — see override_superseded below. Which
    # categories reviewers keep overturning is the calibration signal that
    # tells you a category or its violation definitions are unclear, and
    # nulling these on reject destroys exactly that record.
    override_applied = Column(String, nullable=True)  # None | auto_zero | partial_fail
    rcm_category = Column(String, nullable=True)
    rcm_violation_id = Column(String, nullable=True)

    # True when a reviewer rejected the RCM flag above. The flag stays on the
    # row for audit; this marks that it no longer applies to final_score.
    override_superseded = Column(Boolean, nullable=False, default=False)

    status = Column(Enum(ScoreStatus), nullable=False, default=ScoreStatus.FINAL)

    created_at = Column(DateTime, default=_utcnow, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String, nullable=True)
    review_decision = Column(Enum(ReviewDecision), nullable=True)
