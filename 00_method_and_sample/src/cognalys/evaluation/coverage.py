#!/usr/bin/env python3
"""Coverage and abstention, with the denominator attached to every number.

A pipeline that abstains from everything produces no incorrect patch, and a
count of NO_SAFE_FIX therefore says nothing on its own about whether the
abstentions were right. What has to be reported together is:

  * how many findings were attributed to this run at all (the denominator);
  * how many of them ended with a patch to look at (coverage);
  * how many were abstained from (the complement, broken down by action);
  * and, of those abstentions, how many were on findings an independent
    reviewer judged locally repairable -- the avoidable ones.

Only the first three can be computed from the run's own artifacts. The
fourth needs the labelled worksheet, and when there are no labels this
module says `no_labels` rather than returning a flattering zero. That
distinction is the whole reason it exists: "no avoidable abstentions
observed" and "nobody has checked" are not the same sentence.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List, Optional


from cognalys.evaluation.decisions import (  # noqa: E402
    ABSTAIN, ACTIONS, INVALID, PROPOSING_ACTIONS, action_for,
)


# Oracle values for "could this have been repaired in this repository?".
REPAIRABLE = "repairable"
NOT_LOCALLY_REPAIRABLE = "not_locally_repairable"
REPAIRABILITY_LABELS = frozenset({REPAIRABLE, NOT_LOCALLY_REPAIRABLE, "unknown"})


def read_labels(path: Path) -> Dict[str, Dict[str, str]]:
    """Reviewer labels by finding id, from the worksheet.

    Returns {} when the file is absent or carries no filled-in row, so a
    caller can tell "unlabelled" from "labelled and empty".
    """
    path = Path(path)
    if not path.is_file():
        return {}
    labels: Dict[str, Dict[str, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            identity = str(row.get("unique_msg", "") or "").strip()
            if not identity:
                continue
            filled = {
                key: str(value or "").strip()
                for key, value in row.items()
                if key and str(value or "").strip()
            }
            # A row with only the carried corpus columns is not a label.
            if any(key.startswith(("detector_", "security_", "local_",
                                   "appropriate_", "patch_", "label"))
                   for key in filled):
                labels[identity] = filled
    return labels


def is_published(outcome: Dict) -> bool:
    """Whether a pull request for this finding actually exists.

    The same definition the retrospective's funnel uses -- a created pull
    request -- read from `reached.published` when the record carries it.
    `pull_request` alone is not enough: it also holds the path of a local
    draft that was never opened (seafile getMediaSource in
    run_20260920_175850), which counted as published and made the coverage
    table disagree with the funnel.
    """
    reached = outcome.get("reached")
    if isinstance(reached, dict) and "published" in reached:
        return bool(reached.get("published"))
    link = str(outcome.get("pull_request", "") or "").strip().lower()
    return link.startswith(("http://", "https://"))


def action_counts(outcomes: Iterable[Dict]) -> Dict[str, int]:
    """How many findings ended in each action, over every action name."""
    counts = {action: 0 for action in ACTIONS}
    for outcome in outcomes:
        counts[action_for(outcome.get("decision", ""))] += 1
    return counts


def coverage_report(
    outcomes: List[Dict], labels: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict:
    """Coverage, abstention and -- when labelled -- avoidable abstention.

    `outcomes` are the run's finding records (see
    learning.retrospective.collect_finding_outcomes): each carries at least
    `unique_error_id` and `decision`.
    """
    total = len(outcomes)
    counts = action_counts(outcomes)
    proposed = sum(counts[action] for action in PROPOSING_ACTIONS)
    published = sum(1 for outcome in outcomes if is_published(outcome))
    report = {
        "findings_attributed": total,
        "actions": counts,
        # Every rate carries its own denominator in the name of the key it
        # is derived from, and none is emitted for an empty denominator.
        "proposal_coverage": round(proposed / total, 4) if total else None,
        "proposals": proposed,
        "abstentions": counts[ABSTAIN],
        "abstention_rate": round(counts[ABSTAIN] / total, 4) if total else None,
        "undecided_or_invalid": counts[INVALID],
        "published": published,
        "publication_rate": round(published / total, 4) if total else None,
    }
    report["avoidable_abstention"] = avoidable_abstention(outcomes, labels)
    report["known_positive_abstention"] = known_positive_abstention(outcomes)
    return report


def known_positive_abstention(outcomes: List[Dict]) -> Dict:
    """Abstentions on findings a published advisory already confirms.

    The reviewer worksheet is one oracle; a published advisory analysed at
    its own vulnerable revision is another, and it needs no labelling. In
    run_20260927_175748 three of four such findings ended without a patch
    while this report said only that abstention was "unmeasured".
    """
    known = [
        outcome for outcome in outcomes
        if isinstance(outcome.get("known_positive"), dict)
        and outcome["known_positive"].get("advisories")
    ]
    if not known:
        return {"status": "none_in_run"}
    abstained = [
        outcome for outcome in known
        if action_for(outcome.get("decision", "")) not in PROPOSING_ACTIONS
    ]
    return {
        "status": "measured",
        "known_positives": len(known),
        "abstained": len(abstained),
        "rate": round(len(abstained) / len(known), 4),
        "advisories": sorted({
            identifier for outcome in abstained
            for identifier in outcome["known_positive"]["advisories"]
        }),
    }


def avoidable_abstention(
    outcomes: List[Dict], labels: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict:
    """Abstentions on findings the oracle judged locally repairable.

    The denominator is the labelled repairable findings this run attempted,
    not all abstentions: the question is "how many repairs did we walk away
    from", and a finding nobody labelled cannot answer it either way.
    """
    if not labels:
        return {
            "status": "no_labels",
            "detail": "evals/ground_truth.csv carries no reviewer labels, so "
                      "whether an abstention was avoidable is unmeasured. "
                      "This is not a count of zero.",
        }
    repairable_ids = {
        identity for identity, row in labels.items()
        if row.get("local_repairability") == REPAIRABLE
    }
    attempted = [
        outcome for outcome in outcomes
        if str(outcome.get("unique_error_id", "")) in repairable_ids
    ]
    avoidable = [
        str(outcome.get("unique_error_id", "")) for outcome in attempted
        if action_for(outcome.get("decision", "")) not in PROPOSING_ACTIONS
    ]
    return {
        "status": "measured",
        "labelled_repairable_in_this_run": len(attempted),
        "abstained_anyway": len(avoidable),
        "rate": round(len(avoidable) / len(attempted), 4) if attempted else None,
        "finding_ids": sorted(avoidable),
    }
