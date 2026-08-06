"""
AISCORE scoring engine — same logic as the standalone prototype, with one
addition: ScoreResult now carries a `status` field per
RCM_human_in_the_loop_policy.md. auto_zero results are computed but marked
pending_human_review rather than treated as committed.
"""

from dataclasses import dataclass, field
from typing import Optional
import csv


CREDIT = {
    "meets_expectations": 1.0,
    "partially_meets": 0.6,
    "did_not_meet": 0.0,
}
VALID_RATINGS = set(CREDIT) | {"na"}
VALID_OVERRIDE_TYPES = {"auto_zero", "partial_fail"}


@dataclass
class Criterion:
    criterion_id: str
    criterion_name: str
    category: str
    weight: float
    applicable_channels: set[str]
    automation_method: Optional[str] = None


def load_criteria(path: str) -> dict[str, Criterion]:
    criteria = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            criteria[row["criterion_id"]] = Criterion(
                criterion_id=row["criterion_id"],
                criterion_name=row["criterion_name"],
                category=row["category"],
                weight=float(row["weight"]),
                applicable_channels=set(row["applicable_channels"].split("|")),
                automation_method=row.get("automation_method") or None,
            )
    total = sum(c.weight for c in criteria.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"criteria weights sum to {total}, not 1.0 — fix the source file, don't silently normalize")
    return criteria


def load_rcm_override_types(path: str) -> dict[str, str]:
    by_category: dict[str, set[str]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_category.setdefault(row["rcm_category"], set()).add(row["override_type"])

    resolved = {}
    for cat, types in by_category.items():
        if len(types) > 1:
            raise ValueError(f"rcm_violations.csv: category '{cat}' has inconsistent override_type across rows: {types}")
        ot = next(iter(types))
        if ot not in VALID_OVERRIDE_TYPES:
            raise ValueError(
                f"rcm_violations.csv: category '{cat}' has unresolved override_type '{ot}' — "
                f"decide it and update the CSV before this category can be scored"
            )
        resolved[cat] = ot
    return resolved


@dataclass
class RCMFlag:
    category: str
    violation_id: str
    override_type: Optional[str] = None


@dataclass
class ScoreResult:
    base_score: float
    final_score: float
    override_applied: Optional[str]
    status: str  # "final" | "pending_human_review"
    excluded_na: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def score_interaction(
    criteria: dict[str, Criterion],
    ratings: dict[str, str],
    channel: str,
    rcm_override_types: dict[str, str],
    channel_transition: bool = False,
    rcm_flag: Optional[RCMFlag] = None,
) -> ScoreResult:
    if channel not in ("chat", "voice"):
        raise ValueError(f"unknown channel '{channel}'")

    applicable_ids = {cid for cid, c in criteria.items() if channel in c.applicable_channels}

    missing = applicable_ids - ratings.keys()
    if missing:
        raise ValueError(f"missing explicit rating (use 'na' if insufficient evidence) for: {sorted(missing)}")

    extra = ratings.keys() - applicable_ids
    if extra:
        raise ValueError(f"ratings given for criteria not applicable to channel '{channel}': {sorted(extra)}")

    for cid, rating in ratings.items():
        if rating not in VALID_RATINGS:
            raise ValueError(f"{cid}: invalid rating '{rating}'")
        if rating == "partially_meets" and not channel_transition:
            raise ValueError(
                f"{cid}: 'partially_meets' is only selectable when channel_transition "
                f"(chat escalated to an outbound call) is True — it is not a general band"
            )

    excluded_na = []
    numerator = 0.0
    denominator = 0.0
    for cid, rating in ratings.items():
        weight = criteria[cid].weight
        if rating == "na":
            excluded_na.append(cid)
            continue
        numerator += weight * CREDIT[rating]
        denominator += weight

    if denominator == 0:
        raise ValueError("every applicable criterion was rated NA — no denominator, cannot score this interaction")

    base_score = numerator / denominator

    override_applied = None
    final_score = base_score

    if rcm_flag is not None:
        # An unknown category is always an error. This used to be conditional
        # on override_type being absent, which meant a caller could invent a
        # category and supply its outcome, bypassing the scorecard entirely.
        if rcm_flag.category not in rcm_override_types:
            raise ValueError(f"unknown RCM category '{rcm_flag.category}'")

        csv_type = rcm_override_types[rcm_flag.category]

        if rcm_flag.override_type is None or rcm_flag.override_type == csv_type:
            ot = csv_type
        elif rcm_flag.override_type == "auto_zero" and csv_type == "partial_fail":
            # Goodwill promotion: partial_fail -> auto_zero only. Which
            # categories may be promoted is enforced at the API boundary;
            # the engine enforces the direction. Promotion is safe by
            # construction because auto_zero carries the human review gate.
            ot = "auto_zero"
        else:
            # The demotion case. Allowing it let a Fraud flag be scored as a
            # partial_fail AND escape the pending_human_review gate, which
            # defeats the entire policy in RCM_human_in_the_loop_policy.md.
            raise ValueError(
                f"override_type '{rcm_flag.override_type}' is not permitted for category "
                f"'{rcm_flag.category}' (scorecard says '{csv_type}'); only promotion "
                f"from partial_fail to auto_zero is accepted"
            )
        if ot == "auto_zero":
            final_score = 0.0
            override_applied = "auto_zero"
        elif ot == "partial_fail":
            final_score = min(0.5, base_score)
            override_applied = "partial_fail"
        else:
            raise ValueError(f"unresolved override_type for category '{rcm_flag.category}' — decide before scoring this interaction")

    # RCM_human_in_the_loop_policy.md: auto_zero is computed but held pending;
    # partial_fail and unflagged interactions publish immediately.
    status = "pending_human_review" if override_applied == "auto_zero" else "final"

    return ScoreResult(
        base_score=round(base_score, 4),
        final_score=round(final_score, 4),
        override_applied=override_applied,
        status=status,
        excluded_na=excluded_na,
        detail={
            "channel": channel,
            "channel_transition": channel_transition,
            "rcm_flag": rcm_flag.category if rcm_flag else None,
        },
    )
