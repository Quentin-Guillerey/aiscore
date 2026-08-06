"""
Tests for the four fixes. Append these to tests/test_api.py — they reuse the
`client` and `_post_score` fixtures already defined there. Kept in a separate
file here only so the diff is obvious.

If you do append them, delete the duplicated imports and helpers below.
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.main import app  # noqa: E402
from app.db import get_db  # noqa: E402
from app.models import Base  # noqa: E402

ALL_CHAT = [f"{a}.{b}" for a, b in
            [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7),
             (2, 1), (2, 2), (2, 3), (2, 4), (2, 5), (2, 6), (2, 7)]]


def _ratings(ids, rating="meets_expectations"):
    return {cid: rating for cid in ids}


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


# --- override_type may not be set freely by the caller ----------------
#
# Spreadsheet scorecards commonly let an evaluator choose a category and then
# select an unrelated override outcome. These tests assert that defect did not
# come back through the API surface.


def test_caller_cannot_demote_auto_zero_to_partial_fail(client):
    r = _post_score(client, rcm_flag={
        "category": "Fraud",
        "violation_id": "V1",
        "override_type": "partial_fail",
    })
    assert r.status_code == 422, r.text
    assert "not permitted" in r.text


def test_demotion_attempt_does_not_persist_a_score(client):
    _post_score(client, rcm_flag={
        "category": "Fraud",
        "violation_id": "V1",
        "override_type": "partial_fail",
    })
    assert client.get("/scores").json() == []


def test_caller_cannot_promote_a_non_promotable_category(client):
    # Documentation is partial_fail in the CSV but is not a goodwill category.
    r = _post_score(client, rcm_flag={
        "category": "Documentation",
        "violation_id": "V1",
        "override_type": "auto_zero",
    })
    assert r.status_code == 422, r.text
    assert "not promotable" in r.text


def test_goodwill_promotion_is_accepted(client):
    # The one authorized case: Negligence promoted to auto_zero. Promotion
    # also pulls it into the human-review gate, which is the point.
    r = _post_score(client, rcm_flag={
        "category": "Negligence",
        "violation_id": "V1",
        "override_type": "auto_zero",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["final_score"] == 0.0
    assert d["status"] == "pending_human_review"


def test_negligence_without_promotion_stays_partial_fail(client):
    d = _post_score(client, rcm_flag={
        "category": "Negligence",
        "violation_id": "V1",
    }).json()
    assert d["final_score"] == 0.5
    assert d["status"] == "final"


def test_redundant_override_type_is_accepted_not_special_cased(client):
    # Submitting the value the CSV already holds is pointless but harmless.
    d = _post_score(client, rcm_flag={
        "category": "Fraud",
        "violation_id": "V1",
        "override_type": "auto_zero",
    }).json()
    assert d["final_score"] == 0.0
    assert d["status"] == "pending_human_review"


def test_unknown_category_is_422(client):
    r = _post_score(client, rcm_flag={"category": "NotACategory", "violation_id": "V1"})
    assert r.status_code == 422


# --- malformed ids are caller error, not 500 --------------------------


# "" is deliberately absent: /scores/ with an empty segment resolves to the
# LIST endpoint, not the detail one, so it correctly returns 200. That was a
# bug in this test, not in the application.
@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123", "  ", "'; DROP TABLE scores;--"])
def test_malformed_id_on_get_is_422_not_500(client, bad_id):
    r = client.get(f"/scores/{bad_id}")
    assert r.status_code in (404, 422), f"{bad_id!r} returned {r.status_code}"
    assert r.status_code != 500


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "123", "'; DROP TABLE scores;--"])
def test_malformed_id_on_review_is_422_not_500(client, bad_id):
    r = client.post(f"/scores/{bad_id}/review",
                    json={"decision": "confirm", "reviewed_by": "qa"})
    assert r.status_code in (404, 422), f"{bad_id!r} returned {r.status_code}"
    assert r.status_code != 500


# --- lifespan replaced on_event -------------------------------------------


def test_tables_are_created_on_startup(monkeypatch):
    """The lifespan handler must still run create_all. If this regresses, the
    service starts and then 500s on the first write.

    app.main's lifespan calls Base.metadata.create_all(bind=engine) against the
    module-level engine, NOT against whatever get_db is overridden to. Patching
    only get_db therefore creates tables in the wrong database. app.main.engine
    is patched here so the lifespan builds the schema in the same in-memory DB
    the session uses.
    """
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    monkeypatch.setattr("app.main.engine", test_engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        # No create_all here on purpose; the lifespan handler must do it.
        with TestClient(app) as c:
            r = c.post("/scores", json={
                "interaction_id": "startup",
                "channel": "chat",
                "ratings": _ratings(ALL_CHAT),
            })
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
