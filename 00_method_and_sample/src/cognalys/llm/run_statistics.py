#!/usr/bin/env python3
"""How the analysis step counts what happened to each finding.

Moved out of FixGenerator.run_analysis, where the counting sat inline in the
loop that also writes solution files, so that what the step reports -- and
which of those outcomes fail it -- is defined in one place and can be tested
without constructing a generator.

Every finding ends in exactly one counter. The kinds that are not a success
are kept apart because they call for different responses: a crash is a
defect in this pipeline, a content refusal is the provider's verdict on the
prompt, a finding with nothing to analyse is an input problem, and an
exhausted call budget or a rejected API key is something the operator can
change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional

# Outcomes that are named as themselves; anything else that is neither a
# success nor an abstention is a failed analysis.
FAILURE_KINDS = frozenset({
    "crashed", "provider_refused", "provider_unavailable", "not_analyzable",
    "not_applicable", "provider_auth_failed", "llm_budget_exhausted",
})
# Kinds that fail the step: the finding produced nothing and the cause is in
# this pipeline or in something the operator controls, or (provider_unavailable,
# a server fault or capacity failure outlasting the call's resends) is left for
# a resume pass, as a crash is. A content refusal, an input with nothing to
# analyse and a finding that no longer applies do not.
STEP_FAILING_KINDS = (
    "failed_analysis", "crashed", "provider_unavailable", "provider_auth_failed",
    "llm_budget_exhausted",
)
_MESSAGES = {
    "crashed": ("error", "Analysis crashed: %s"),
    "provider_refused": ("error", "Provider refused the request content: %s"),
    "provider_unavailable": ("error", "Provider unavailable (left for a resume pass): %s"),
    "provider_auth_failed": ("error", "provider_auth_failed: %s"),
    "llm_budget_exhausted": ("error", "llm_budget_exhausted: %s"),
    # Not errors: the finding never had anything to analyse, or no longer
    # applies to this branch, and saying so costs nothing.
    "not_analyzable": ("warning", "Not analysable: %s"),
    "not_applicable": ("warning", "Not applicable: %s"),
}


def failure_kind(outcome: str) -> str:
    return outcome if outcome in FAILURE_KINDS else "failed"


def record_llm_budget(stats: Dict, budget: Optional[Dict]) -> None:
    """Add one finding's model-call spend to the run totals."""
    if not budget:
        return
    totals = stats["llm_budget"]
    totals["findings"] += 1
    for key in ("calls", "failed_calls", "transport_retries"):
        totals[key] += int(budget.get(key, 0) or 0)
    # Calls a finding's own identical earlier call answered: not sent.
    totals["replayed_calls"] = (int(totals.get("replayed_calls", 0) or 0)
                                + int(budget.get("replayed_calls", 0) or 0))
    totals["model_seconds"] = round(
        totals["model_seconds"] + float(budget.get("model_seconds", 0) or 0), 1,
    )


def record_input_invalid(stats: Dict, invalid: Iterable[Dict], logger) -> None:
    """Finding files that could not be loaded: counted, named, never dropped.

    A crash while loading is this pipeline's defect and is counted as one;
    a file that could not be read or parsed is an input problem.
    """
    invalid = list(invalid)
    stats["input_invalid"] = sum(1 for item in invalid if item.get("kind") != "crashed")
    stats["crashed"] += len(invalid) - stats["input_invalid"]
    for item in invalid:
        stats["failures"].append({
            "apk_name": Path(item["file"]).parent.name,
            "method_name": "",
            "finding_id": "",
            "kind": item.get("kind", "input_invalid"),
            "cause": item["cause"],
            "decision": "",
            "file": item["file"],
        })
    if invalid:
        logger.warning(
            "%d finding file(s) could not be loaded; they are counted and "
            "listed in solutions_tracking.json", len(invalid),
        )


def record_solution(stats: Dict, solution: Dict) -> int:
    """Count a generated fix; returns its confidence for the log line."""
    stats["successfully_analyzed"] += 1
    stats["fixes_generated"] += 1
    if solution.get("solution_metadata", {}).get("fix_scope") == "coordinated":
        stats["coordinated_solutions"] += 1
    confidence = solution.get("llm_analysis", {}).get("confidence_percent", 50)
    if confidence >= 75:
        stats["high_confidence"] += 1
    elif confidence >= 50:
        stats["medium_confidence"] += 1
    else:
        stats["low_confidence"] += 1
    return confidence


def record_failure(stats: Dict, logger, error: Dict, result) -> str:
    """Count a finding that produced no solution and was not an abstention."""
    kind = failure_kind(result.outcome)
    cause = result.failure_cause or "no cause recorded"
    if kind == "failed":
        stats["failed_analysis"] += 1
        logger.error("Failed to analyze")
    else:
        stats[kind] += 1
        level, message = _MESSAGES[kind]
        getattr(logger, level)(message, cause)
    stats["failures"].append({
        "apk_name": error["_file_path"].parent.name,
        "method_name": error.get("method_name", "unknown"),
        "finding_id": error.get("unique_error_id", ""),
        "kind": kind,
        "cause": result.failure_cause,
        "decision": result.decision,
        "llm_budget": result.llm_budget or {},
    })
    return kind


def step_failures(stats: Dict) -> int:
    """How many findings fail the step (see STEP_FAILING_KINDS)."""
    return sum(int(stats.get(kind, 0) or 0) for kind in STEP_FAILING_KINDS)
