"""
Shared fixtures. These previously lived only in test_scoring_engine.py, which
meant a second engine test file could not use them: pytest fixtures are not
importable across test modules, only inherited from a conftest. Putting them
here makes them available to every file in tests/.

The identically-named fixtures still defined inside test_scoring_engine.py
shadow these harmlessly. Delete them from that file if you want a single
source; nothing breaks either way.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.scoring_engine import load_criteria, load_rcm_override_types  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


@pytest.fixture(scope="module")
def criteria():
    return load_criteria(os.path.join(DATA, "criteria.csv"))


@pytest.fixture(scope="module")
def rcm_types():
    return load_rcm_override_types(os.path.join(DATA, "rcm_violations.csv"))


@pytest.fixture
def chat_ratings(criteria):
    return {cid: "meets_expectations" for cid, c in criteria.items()
            if "chat" in c.applicable_channels}


@pytest.fixture
def voice_ratings(criteria):
    return {cid: "meets_expectations" for cid, c in criteria.items()
            if "voice" in c.applicable_channels}
