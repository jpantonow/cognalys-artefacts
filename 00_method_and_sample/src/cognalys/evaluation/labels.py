#!/usr/bin/env python3
"""Reviewer labels on separate axes, and agreement between reviewers.

The worksheet used to carry one column, `label`, with `real`,
`false_positive` or `unsure`, and the score derived from it treated "the
pipeline proposed a change" as a positive prediction of "real". Under that
mapping a genuine finding that genuinely cannot be repaired in this
repository -- confirmed cause, external contract, correct abstention -- was
counted as a false negative, indistinguishable from walking away from a bug
it could have fixed. One column cannot separate "is the finding real" from
"was the action right", and those are the two questions the project is
about.

So a finding is labelled on five axes, each with an explicit `unknown`,
and the action axis uses the same vocabulary as the pipeline's own actions
(evaluation.decisions) so the two are directly comparable rather than
mapped through an interpretation.

Agreement is reported per axis, as observed agreement and as Cohen's kappa.
Kappa is undefined when both reviewers used a single category -- which is
common on a small, skewed sample -- and is reported as undefined instead of
as a number that would read as perfect or as chance.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


from cognalys.evaluation.decisions import ACTIONS, PROPOSING_ACTIONS, action_for  # noqa: E402


UNKNOWN = "unknown"

# One question per axis. The reviewer answers what the code settles and
# writes `unknown` for the rest; an empty cell means "not reviewed yet",
# which is different again and never treated as a judgement.
AXES: Dict[str, tuple] = {
    # Did the code violate the rule the detector reported?
    "detector_validity": ("violated", "not_violated", UNKNOWN),
    # Does that violation matter for security in this context?
    "security_relevance": (
        "security_relevant", "not_security_relevant", UNKNOWN,
    ),
    # Could it be repaired inside this repository at all?
    "local_repairability": ("repairable", "not_locally_repairable", UNKNOWN),
    # What should the pipeline have done? Same vocabulary as its own
    # actions, so the comparison needs no translation.
    "appropriate_action": (*ACTIONS[:-1], UNKNOWN),
    # Only for findings that produced a patch.
    "patch_correctness": ("correct", "incorrect", "not_applicable", UNKNOWN),
}

CARRIED_COLUMNS = (
    "unique_msg", "spec", "apk", "class", "method", "message", "repo_url",
    "source_url",
)
WORKSHEET_COLUMNS = (
    *CARRIED_COLUMNS, *AXES, "reviewer", "evidence", "note",
)


def validate_row(row: Dict[str, str]) -> List[str]:
    """Values outside an axis's vocabulary, named."""
    issues = []
    for axis, allowed in AXES.items():
        value = str(row.get(axis, "") or "").strip().lower()
        if value and value not in allowed:
            issues.append(
                f"{row.get('unique_msg', '(no id)')}: {axis}={value!r} is not "
                f"one of {', '.join(allowed)}"
            )
    return issues


def read_worksheet(path: Path) -> List[Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def labelled_rows(rows: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    """Rows a reviewer has actually answered on at least one axis."""
    return [
        row for row in rows
        if any(str(row.get(axis, "") or "").strip() for axis in AXES)
    ]


def _counts(values: Sequence[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def cohen_kappa(first: Sequence[str], second: Sequence[str]) -> Dict:
    """Agreement corrected for chance, with its own caveats attached.

    Returns `kappa: None` with a stated reason rather than a number when the
    statistic is undefined: no overlapping items, or both reviewers using a
    single category, where expected agreement is 1 and the correction
    divides by zero. On a small, skewed sample that is the normal case, and
    reporting 0.0 or 1.0 there would be an artefact of the formula.
    """
    pairs = [
        (left, right) for left, right in zip(first, second)
        if left and right
    ]
    if not pairs:
        return {"items": 0, "observed_agreement": None, "kappa": None,
                "detail": "no finding was labelled on this axis by both"}
    agreed = sum(1 for left, right in pairs if left == right)
    observed = agreed / len(pairs)
    left_counts = _counts([left for left, _ in pairs])
    right_counts = _counts([right for _, right in pairs])
    expected = sum(
        (left_counts.get(category, 0) / len(pairs))
        * (right_counts.get(category, 0) / len(pairs))
        for category in set(left_counts) | set(right_counts)
    )
    if abs(1 - expected) < 1e-12:
        return {
            "items": len(pairs), "observed_agreement": round(observed, 4),
            "kappa": None,
            "detail": "both reviewers used a single category, so chance "
                      "agreement is 1 and kappa is undefined",
        }
    return {
        "items": len(pairs),
        "observed_agreement": round(observed, 4),
        "kappa": round((observed - expected) / (1 - expected), 4),
        "categories_used": sorted(set(left_counts) | set(right_counts)),
    }


def agreement(
    first: Sequence[Dict[str, str]], second: Sequence[Dict[str, str]],
) -> Dict:
    """Per-axis agreement between two reviewers' worksheets."""
    by_id_first = {
        str(row.get("unique_msg", "")): row for row in first
        if str(row.get("unique_msg", ""))
    }
    by_id_second = {
        str(row.get("unique_msg", "")): row for row in second
        if str(row.get("unique_msg", ""))
    }
    shared = sorted(set(by_id_first) & set(by_id_second))
    report: Dict = {
        "findings_in_first": len(by_id_first),
        "findings_in_second": len(by_id_second),
        "findings_in_both": len(shared),
        "axes": {},
        "disagreements": [],
    }
    for axis in AXES:
        left = [
            str(by_id_first[identity].get(axis, "") or "").strip().lower()
            for identity in shared
        ]
        right = [
            str(by_id_second[identity].get(axis, "") or "").strip().lower()
            for identity in shared
        ]
        report["axes"][axis] = cohen_kappa(left, right)
        for identity, one, other in zip(shared, left, right):
            if one and other and one != other:
                report["disagreements"].append({
                    "unique_msg": identity, "axis": axis,
                    "first": one, "second": other,
                })
    return report


def score_against_pipeline(
    rows: Sequence[Dict[str, str]], decisions: Dict[str, Sequence[str]],
) -> Dict:
    """What the reviewer said the action should be, against what it was.

    Every finding lands in exactly one bucket, and the buckets that mean
    "not measured" are kept apart from the buckets that mean "wrong":

      agreed                the pipeline took the action the reviewer chose
      avoidable_abstention  the reviewer wanted a patch; none was produced
      unwarranted_action    the reviewer wanted none; one was produced
      different_action      both acted, but not in the same way
      not_labelled          no reviewer answer on this axis
      not_adjudicated       the pipeline has no decision for this finding
      undecided             the pipeline's decision is empty or unknown
    """
    buckets: Dict[str, List[str]] = {
        name: [] for name in (
            "agreed", "avoidable_abstention", "unwarranted_action",
            "different_action", "not_labelled", "not_adjudicated", "undecided",
        )
    }
    for row in rows:
        identity = str(row.get("unique_msg", "") or "").strip()
        expected = str(row.get("appropriate_action", "") or "").strip().lower()
        if not identity:
            continue
        if not expected or expected == UNKNOWN:
            buckets["not_labelled"].append(identity)
            continue
        observed_decisions = list(decisions.get(identity, []))
        if not observed_decisions:
            buckets["not_adjudicated"].append(identity)
            continue
        observed = action_for(
            _modal(observed_decisions)
        )
        if observed == "invalid":
            buckets["undecided"].append(identity)
        elif observed == expected:
            buckets["agreed"].append(identity)
        elif expected in PROPOSING_ACTIONS and observed not in PROPOSING_ACTIONS:
            buckets["avoidable_abstention"].append(identity)
        elif expected not in PROPOSING_ACTIONS and observed in PROPOSING_ACTIONS:
            buckets["unwarranted_action"].append(identity)
        else:
            buckets["different_action"].append(identity)
    measured = sum(
        len(buckets[name]) for name in
        ("agreed", "avoidable_abstention", "unwarranted_action",
         "different_action", "undecided")
    )
    return {
        "rows": len(rows),
        "measured": measured,
        "counts": {name: len(items) for name, items in buckets.items()},
        "agreement_rate": (
            round(len(buckets["agreed"]) / measured, 4) if measured else None
        ),
        "findings": buckets,
    }


def _modal(decisions: Sequence[str]) -> str:
    """The decision this pipeline gives most often for one finding."""
    if not decisions:
        return ""
    counts = _counts(list(decisions))
    return max(sorted(counts), key=lambda value: (counts[value], value))
