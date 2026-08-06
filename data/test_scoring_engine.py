"""
Scoring engine tests. These are the cases that previously lived in the
standalone script's __main__ block; that script was deleted to remove a
duplicate copy of the scoring logic, which left the cases unrun. Restored
here so they execute on every push.

Fixtures (criteria, rcm_types, chat_ratings) come from tests/conftest.py.
They used to be defined in this file and were duplicated once a second
engine test module needed them, which is the same two-sources-of-truth
problem this project keeps running into. One definition now.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scoring_engine import score_interaction, RCMFlag  # noqa: E402


def test_all_meets_scores_one(criteria, rcm_types, chat_ratings):
    r = score_interaction(criteria, chat_ratings, "chat", rcm_types)
    assert r.final_score == 1.0
    assert r.status == "final"


def test_na_excluded_from_both_numerator_and_denominator(criteria, rcm_types, chat_ratings):
    ratings = dict(chat_ratings)
    target = next(iter(ratings))
    ratings[target] = "na"
    r = score_interaction(criteria, ratings, "chat", rcm_types)
    # NA drops out of the denominator too, so a perfect remainder is still 1.0
    assert r.final_score == 1.0
    assert r.excluded_na == [target]


def test_partial_credit_rejected_without_gate(criteria, rcm_types, chat_ratings):
    ratings = dict(chat_ratings)
    ratings["1.1"] = "partially_meets"
    with pytest.raises(ValueError, match="partially_meets"):
        score_interaction(criteria, ratings, "chat", rcm_types, channel_transition=False)


def test_partial_credit_allowed_with_gate(criteria, rcm_types, chat_ratings):
    ratings = dict(chat_ratings)
    ratings["1.1"] = "partially_meets"
    r = score_interaction(criteria, ratings, "chat", rcm_types, channel_transition=True)
    assert r.final_score < 1.0


def test_auto_zero_overrides_perfect_base_score(criteria, rcm_types, chat_ratings):
    r = score_interaction(criteria, chat_ratings, "chat", rcm_types, rcm_flag=RCMFlag("Fraud", "V1"))
    assert r.base_score == 1.0
    assert r.final_score == 0.0
    # RCM_human_in_the_loop_policy.md: auto_zero is computed but held pending
    assert r.status == "pending_human_review"


def test_partial_fail_takes_lower_of_half_and_base(criteria, rcm_types, chat_ratings):
    # Base already below 50% — the lower score stands, NOT a hardcoded 50%.
    # A hardcoded cap is the common spreadsheet bug; this asserts it is absent.
    ratings = {cid: "did_not_meet" for cid in chat_ratings}
    r = score_interaction(criteria, ratings, "chat", rcm_types, rcm_flag=RCMFlag("Documentation", "V1"))
    assert r.final_score == 0.0
    assert r.status == "final"


def test_partial_fail_caps_high_base_score(criteria, rcm_types, chat_ratings):
    r = score_interaction(criteria, chat_ratings, "chat", rcm_types, rcm_flag=RCMFlag("Documentation", "V1"))
    assert r.final_score == 0.5


def test_call_avoidance_resolves_to_auto_zero(criteria, rcm_types):
    # Was UNMAPPED; resolved 2026-07-31. Sourced from the CSV, not hardcoded.
    voice_ratings = {cid: "meets_expectations" for cid, c in criteria.items() if "voice" in c.applicable_channels}
    r = score_interaction(criteria, voice_ratings, "voice", rcm_types, rcm_flag=RCMFlag("Call_Avoidance", "V1"))
    assert r.final_score == 0.0
    assert r.status == "pending_human_review"


def test_missing_rating_is_an_error_not_a_silent_gap(criteria, rcm_types, chat_ratings):
    incomplete = dict(chat_ratings)
    del incomplete[next(iter(incomplete))]
    with pytest.raises(ValueError, match="missing explicit rating"):
        score_interaction(criteria, incomplete, "chat", rcm_types)
