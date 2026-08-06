"""
Service-layer tests. These exercise the actual HTTP endpoints against an
in-memory SQLite database — no Docker, no Postgres, runs in CI.

This closes the "reviewed but never run" gap on app/main.py. It also
incidentally exercises the GUID TypeDecorator's SQLite branch, which is the
same code path the standalone/exe build depends on.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# app.db reads DATABASE_URL at import time, so it must be set before
# app.main is imported. The value is overridden by the fixture below; this
# just has to be a valid URL so the import succeeds.
os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.main import app  # noqa: E402
from app.db import get_db  # noqa: E402
from app.models import Base  # noqa: E402

ALL_CHAT = [f"{a}.{b}" for a, b in
            [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7),
             (2, 1), (2, 2), (2, 3), (2, 4), (2, 5), (2, 6), (2, 7)]]
# 1.5 is chat-only (Chat grammar), so voice takes the other 13.
ALL_VOICE = [c for c in ALL_CHAT if c != "1.5"]


def _ratings(ids, rating="meets_expectations"):
    return {cid: rating for cid in ids}


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # keeps one connection so :memory: persists
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _post_score(client, **kwargs):
    payload = {
        "interaction_id": "t",
        "channel": "chat",
        "ratings": _ratings(ALL_CHAT),
    }
    payload.update(kwargs)
    return client.post("/scores", json=payload)


# --- basic scoring through the API ------------------------------------


def test_clean_chat_score(client):
    r = _post_score(client)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["base_score"] == 1.0
    assert d["final_score"] == 1.0
    assert d["status"] == "final"
    assert d["override_applied"] is None
    assert d["override_superseded"] is False


def test_scorecard_version_lands_on_the_row(client):
    d = _post_score(client).json()
    assert d["scorecard_version"], "scorecard_version must be recorded on every score"


def test_voice_excludes_chat_only_criterion(client):
    r = _post_score(client, channel="voice", ratings=_ratings(ALL_VOICE))
    assert r.status_code == 200, r.text
    assert r.json()["final_score"] == 1.0


def test_voice_rejects_chat_only_criterion(client):
    r = _post_score(client, channel="voice", ratings=_ratings(ALL_CHAT))
    assert r.status_code == 422
    assert "1.5" in r.text


def test_missing_criterion_is_422_not_500(client):
    partial = _ratings(ALL_CHAT)
    del partial["2.3"]
    r = _post_score(client, ratings=partial)
    assert r.status_code == 422
    assert "2.3" in r.text


def test_ungated_partial_credit_rejected(client):
    ratings = _ratings(ALL_CHAT)
    ratings["1.1"] = "partially_meets"
    r = _post_score(client, ratings=ratings)
    assert r.status_code == 422


# --- override behavior through the API --------------------------------


def test_auto_zero_is_held_pending(client):
    d = _post_score(client, rcm_flag={"category": "Fraud", "violation_id": "V1"}).json()
    assert d["base_score"] == 1.0
    assert d["final_score"] == 0.0
    assert d["status"] == "pending_human_review"


def test_partial_fail_publishes_immediately(client):
    d = _post_score(client, rcm_flag={"category": "Documentation", "violation_id": "V1"}).json()
    assert d["final_score"] == 0.5
    assert d["status"] == "final"


def test_partial_fail_does_not_raise_a_worse_score(client):
    # A hardcoded 50% would raise a worse score; the rule is min(0.5, base_score).
    d = _post_score(
        client,
        ratings=_ratings(ALL_CHAT, "did_not_meet"),
        rcm_flag={"category": "Documentation", "violation_id": "V1"},
    ).json()
    assert d["final_score"] == 0.0


# --- the human-in-the-loop gate ---------------------------------------


def test_pending_queue_contains_only_auto_zero(client):
    _post_score(client)  # clean, final
    _post_score(client, rcm_flag={"category": "Documentation", "violation_id": "V1"})  # partial_fail, final
    _post_score(client, rcm_flag={"category": "Fraud", "violation_id": "V1"})  # auto_zero, pending

    q = client.get("/scores", params={"status": "pending_human_review"}).json()
    assert len(q) == 1
    assert q[0]["rcm_category"] == "Fraud"


def test_confirm_keeps_the_zero(client):
    sid = _post_score(client, rcm_flag={"category": "Fraud", "violation_id": "V1"}).json()["id"]
    r = client.post(f"/scores/{sid}/review", json={"decision": "confirm", "reviewed_by": "qa"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["final_score"] == 0.0
    assert d["status"] == "final"
    assert d["override_superseded"] is False


def test_reject_reverts_score_but_preserves_the_flag(client):
    """The audit-trail requirement: a rejected flag must stay on the record."""
    sid = _post_score(client, rcm_flag={"category": "Fraud", "violation_id": "V1"}).json()["id"]
    d = client.post(f"/scores/{sid}/review", json={"decision": "reject", "reviewed_by": "qa"}).json()

    assert d["final_score"] == 1.0
    assert d["status"] == "final"
    assert d["override_superseded"] is True
    # These must NOT be cleared — overturn rates per category are the signal
    # that a category or its violation definitions need work.
    assert d["rcm_category"] == "Fraud"
    assert d["rcm_violation_id"] == "V1"
    assert d["override_applied"] == "auto_zero"


def test_cannot_review_twice(client):
    sid = _post_score(client, rcm_flag={"category": "Fraud", "violation_id": "V1"}).json()["id"]
    client.post(f"/scores/{sid}/review", json={"decision": "confirm", "reviewed_by": "qa"})
    r = client.post(f"/scores/{sid}/review", json={"decision": "confirm", "reviewed_by": "qa"})
    assert r.status_code == 409


def test_cannot_review_a_final_score(client):
    sid = _post_score(client).json()["id"]  # never pending
    r = client.post(f"/scores/{sid}/review", json={"decision": "confirm", "reviewed_by": "qa"})
    assert r.status_code == 409


def test_invalid_review_decision_rejected(client):
    sid = _post_score(client, rcm_flag={"category": "Fraud", "violation_id": "V1"}).json()["id"]
    r = client.post(f"/scores/{sid}/review", json={"decision": "maybe", "reviewed_by": "qa"})
    assert r.status_code == 422


# --- retrieval --------------------------------------------------------


def test_get_score_roundtrip(client):
    sid = _post_score(client).json()["id"]
    r = client.get(f"/scores/{sid}")
    assert r.status_code == 200
    assert r.json()["id"] == sid


def test_unknown_score_is_404(client):
    r = client.get("/scores/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


def test_unknown_status_filter_is_422(client):
    r = client.get("/scores", params={"status": "not_a_status"})
    assert r.status_code == 422
