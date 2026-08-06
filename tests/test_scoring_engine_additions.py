"""
Override-type validation on score_interaction.

Before this validation existed, a caller-supplied override_type was used
verbatim. Two consequences, both asserted closed here:

  1. An unknown category could be scored as long as an override_type came
     with it, bypassing rcm_violations.csv entirely.
  2. An auto_zero category could be demoted to partial_fail. That did not
     merely produce a wrong score: it also moved the result out of
     pending_human_review, so a Fraud flag could skip the human gate that
     RCM_human_in_the_loop_policy.md exists to enforce.

Fixtures (criteria, rcm_types, chat_ratings) come from tests/conftest.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scoring_engine import score_interaction, RCMFlag  # noqa: E402


def test_unknown_category_rejected_even_with_override_type(criteria, rcm_types, chat_ratings):
    with pytest.raises(ValueError, match="unknown RCM category"):
        score_interaction(criteria, chat_ratings, "chat", rcm_types,
                          rcm_flag=RCMFlag("NotACategory", "V1", override_type="partial_fail"))


def test_auto_zero_category_cannot_be_demoted(criteria, rcm_types, chat_ratings):
    with pytest.raises(ValueError, match="not permitted"):
        score_interaction(criteria, chat_ratings, "chat", rcm_types,
                          rcm_flag=RCMFlag("Fraud", "V1", override_type="partial_fail"))


def test_redundant_override_type_matching_csv_is_accepted(criteria, rcm_types, chat_ratings):
    r = score_interaction(criteria, chat_ratings, "chat", rcm_types,
                          rcm_flag=RCMFlag("Fraud", "V1", override_type="auto_zero"))
    assert r.final_score == 0.0
    assert r.status == "pending_human_review"


def test_goodwill_promotion_direction_is_allowed(criteria, rcm_types, chat_ratings):
    # partial_fail -> auto_zero. Safe by construction: the promoted result
    # inherits the human review gate.
    r = score_interaction(criteria, chat_ratings, "chat", rcm_types,
                          rcm_flag=RCMFlag("Negligence", "V1", override_type="auto_zero"))
    assert r.final_score == 0.0
    assert r.status == "pending_human_review"


def test_unpromoted_negligence_still_partial_fail(criteria, rcm_types, chat_ratings):
    r = score_interaction(criteria, chat_ratings, "chat", rcm_types,
                          rcm_flag=RCMFlag("Negligence", "V1"))
    assert r.final_score == 0.5
    assert r.status == "final"


def test_garbage_override_type_rejected(criteria, rcm_types, chat_ratings):
    with pytest.raises(ValueError, match="not permitted"):
        score_interaction(criteria, chat_ratings, "chat", rcm_types,
                          rcm_flag=RCMFlag("Documentation", "V1", override_type="nonsense"))
