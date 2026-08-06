"""
Scorecard data integrity. These checks were run once by hand during the
initial audit; enforcing them here means a bad edit to criteria.csv or
rcm_violations.csv fails CI instead of silently producing wrong scores.
"""

import csv
import os

import pytest

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CRITERIA = os.path.join(DATA, "criteria.csv")
RCM = os.path.join(DATA, "rcm_violations.csv")

VALID_CHANNELS = {"chat", "voice"}
VALID_OVERRIDE_TYPES = {"auto_zero", "partial_fail"}
VALID_AUTOMATION = {"rule", "model", "not_automatable"}


@pytest.fixture(scope="module")
def criteria_rows():
    with open(CRITERIA, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def rcm_rows():
    with open(RCM, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_weights_sum_to_one(criteria_rows):
    total = sum(float(r["weight"]) for r in criteria_rows)
    assert abs(total - 1.0) < 1e-9, f"weights sum to {total}, not 1.0"


def test_no_duplicate_criterion_ids(criteria_rows):
    ids = [r["criterion_id"] for r in criteria_rows]
    assert len(ids) == len(set(ids))


def test_every_criterion_has_valid_channels(criteria_rows):
    for r in criteria_rows:
        chans = set(r["applicable_channels"].split("|"))
        assert chans, f"{r['criterion_id']} has no channels"
        assert chans <= VALID_CHANNELS, f"{r['criterion_id']} has invalid channel: {chans - VALID_CHANNELS}"


def test_automation_method_populated_and_valid(criteria_rows):
    for r in criteria_rows:
        method = r.get("automation_method", "")
        assert method in VALID_AUTOMATION, f"{r['criterion_id']} has automation_method '{method}'"


def test_every_rcm_category_has_one_resolved_override_type(rcm_rows):
    by_category = {}
    for r in rcm_rows:
        by_category.setdefault(r["rcm_category"], set()).add(r["override_type"])

    for cat, types in by_category.items():
        assert len(types) == 1, f"category '{cat}' has inconsistent override_type: {types}"
        ot = next(iter(types))
        assert ot in VALID_OVERRIDE_TYPES, f"category '{cat}' has unresolved override_type '{ot}'"


def test_no_duplicate_violation_ids_within_category_and_channel(rcm_rows):
    seen = set()
    for r in rcm_rows:
        key = (r["rcm_category"], r["channel"], r["violation_id"])
        assert key not in seen, f"duplicate violation: {key}"
        seen.add(key)


def test_scorecard_version_file_is_present_and_nonempty():
    path = os.path.join(DATA, "SCORECARD_VERSION")
    assert os.path.exists(path), "SCORECARD_VERSION missing â app/scorecard_loader.py reads it at import time"
    with open(path, encoding="utf-8") as f:
        assert f.read().strip(), "SCORECARD_VERSION is empty"
