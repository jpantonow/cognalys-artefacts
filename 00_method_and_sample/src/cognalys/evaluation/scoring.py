#!/usr/bin/env python3
"""Score normalized findings against a manifest, with the denominators shown.

The rule this module exists to enforce: a case the evaluator could not find
must never leave the denominator. The previous scorer gave a missing case
`{"passed": 0, "total": 0}`, which contributes nothing to `passed / total`,
so a manifest whose cases mostly did not match any artifact reported the
score of the handful that did. `cases_found` was reported beside it, but the
headline number did not depend on it -- and in the repository as it stands
three of the four manifest cases carry placeholder finding IDs that no
artifact can match, so that headline came from one case.

Three outcomes are kept apart for every comparison, because they mean
different things and only the first is a success:

  match             the artifact states what the manifest expects
  mismatch          the artifact states something else
  observed_unknown  the artifact does not state this at all

The third is not a mismatch. A run that declined to answer and a run that
answered wrongly are different failures with different fixes, and averaging
them together hides which one you have.
"""
from __future__ import annotations

from typing import Dict, List, Sequence


from cognalys.evaluation.decisions import ACTIONS  # noqa: E402
from cognalys.evaluation.normalize import UNKNOWN, axis_names, normalize  # noqa: E402


MATCH = "match"
MISMATCH = "mismatch"
OBSERVED_UNKNOWN = "observed_unknown"
CASE_MISSING = "case_missing"

MANIFEST_VERSION = 2

# Expectation values a manifest may use, per axis. Anything else is a
# manifest error rather than a silent zero: the previous vocabulary
# ("supported", "policy_or_review", "review_only") came from substrings of
# decision names and no longer means anything here.
_ALLOWED_VALUES = {
    "action": frozenset(ACTIONS),
    "detector_violation": frozenset({
        "confirmed", "source_established", "refuted", UNKNOWN,
    }),
    "runtime_security_impact": frozenset({
        "established", "not_established", "non_security", UNKNOWN,
    }),
    "cause_status": frozenset({"confirmed", "inferred", UNKNOWN}),
    "generation_status": frozenset({
        "supported", "supported_for_review", "blocked", UNKNOWN,
    }),
    "publication_status": frozenset({
        "eligible", "eligible_after_validation", "blocked", UNKNOWN,
    }),
    "scope": frozenset({
        "single_file", "multi_file", "external_contract", UNKNOWN,
    }),
}


def validate_manifest(manifest: Dict) -> List[str]:
    """Manifest errors, named. An invalid manifest must not score silently."""
    issues: List[str] = []
    version = manifest.get("manifest_version")
    if version != MANIFEST_VERSION:
        issues.append(
            f"manifest_version must be {MANIFEST_VERSION}; found {version!r}. "
            "Version 1 used the substring-derived repairability vocabulary, "
            "which this scorer no longer accepts."
        )
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        return issues + ["manifest must carry a non-empty 'cases' list"]
    seen = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            issues.append(f"case {index} is not an object")
            continue
        finding_id = str(case.get("finding_id", ""))
        if not finding_id:
            issues.append(f"case {index} has no finding_id")
        elif finding_id in seen:
            issues.append(f"duplicate finding_id {finding_id!r}")
        seen.add(finding_id)
        expected = case.get("expected", {})
        if not isinstance(expected, dict):
            issues.append(f"case {finding_id!r}: 'expected' must be an object")
            continue
        for axis, value in expected.items():
            if axis in ("required_claims", "forbidden_claims"):
                continue
            if axis not in _ALLOWED_VALUES:
                issues.append(
                    f"case {finding_id!r}: unknown axis {axis!r}; "
                    f"valid axes are {', '.join(sorted(axis_names()))}"
                )
                continue
            if value not in _ALLOWED_VALUES[axis]:
                issues.append(
                    f"case {finding_id!r}: {axis}={value!r} is not one of "
                    + ", ".join(sorted(_ALLOWED_VALUES[axis]))
                )
    return issues


def _compare(observed: object, expected: object) -> str:
    if observed == expected:
        return MATCH
    if observed == UNKNOWN:
        return OBSERVED_UNKNOWN
    return MISMATCH


def score_case(case: Dict, value: Dict | None) -> Dict:
    """One case's per-axis outcomes, or a recorded absence."""
    finding_id = str(case.get("finding_id", ""))
    expected = case.get("expected", {}) or {}
    if value is None:
        return {
            "finding_id": finding_id,
            "found": False,
            "axes": {axis: CASE_MISSING for axis in expected
                     if axis in _ALLOWED_VALUES},
            "text_checks": {},
            "provenance": {},
            "outcomes": {CASE_MISSING: sum(
                1 for axis in expected if axis in _ALLOWED_VALUES
            )},
        }
    normalized = normalize(value)
    axes = {
        axis: _compare(normalized.get(axis, UNKNOWN), expected[axis])
        for axis in expected if axis in _ALLOWED_VALUES
    }
    # Text checks stay available as regression tripwires, but they are
    # reported apart from the axes and never enter the score: a phrase can
    # appear inside a sentence that denies it.
    statements = " \n".join(normalized["claim_statements"])
    text_checks = {}
    for phrase in expected.get("required_claims", []):
        text_checks[f"required:{phrase}"] = str(phrase).lower() in statements
    for phrase in expected.get("forbidden_claims", []):
        text_checks[f"forbidden:{phrase}"] = str(phrase).lower() not in statements
    outcomes: Dict[str, int] = {}
    for outcome in axes.values():
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {
        "finding_id": finding_id,
        "found": True,
        "artifact": str(value.get("_eval_artifact", "")),
        "normalized": normalized,
        "axes": axes,
        "text_checks": text_checks,
        "provenance": normalized["provenance"],
        "outcomes": outcomes,
    }


def score_run(manifest: Dict, results: Dict[str, Dict]) -> Dict:
    """Score every manifest case, including the ones with no artifact."""
    issues = validate_manifest(manifest)
    if issues:
        return {"manifest_issues": issues, "cases": {}, "score": None}
    cases = {}
    for case in manifest.get("cases", []):
        finding_id = str(case.get("finding_id", ""))
        cases[finding_id] = score_case(case, results.get(finding_id))
    totals: Dict[str, int] = {
        MATCH: 0, MISMATCH: 0, OBSERVED_UNKNOWN: 0, CASE_MISSING: 0,
    }
    for case in cases.values():
        for outcome, count in case["outcomes"].items():
            totals[outcome] = totals.get(outcome, 0) + count
    compared = sum(totals.values())
    found = sum(1 for case in cases.values() if case["found"])
    provenance_dangling = sorted(
        case["finding_id"] for case in cases.values()
        if case.get("provenance", {}).get("dangling_references")
    )
    provenance_unsupported = sorted(
        case["finding_id"] for case in cases.values()
        if case.get("provenance", {}).get("unsupported_claim_ids")
    )
    return {
        "manifest_issues": [],
        "cases": cases,
        "cases_total": len(cases),
        "cases_found": found,
        "cases_missing": sorted(
            case["finding_id"] for case in cases.values() if not case["found"]
        ),
        "case_coverage": round(found / len(cases), 4) if cases else None,
        "axis_outcomes": totals,
        # The denominator is every expected axis of every manifest case,
        # including the cases with no artifact. A run that produced nothing
        # scores zero here rather than scoring nothing.
        "axes_compared": compared,
        "score": round(totals[MATCH] / compared, 4) if compared else None,
        "claims_with_dangling_evidence": provenance_dangling,
        "claims_without_evidence": provenance_unsupported,
        "text_checks_failed": sorted(
            f"{case['finding_id']}:{name}"
            for case in cases.values()
            for name, passed in case.get("text_checks", {}).items()
            if not passed
        ),
    }


def compare_runs(runs: Sequence[Dict[str, Dict]], manifest: Dict) -> Dict:
    """Agreement across repetitions, per axis and by proposition.

    Claim identity is compared two ways on purpose. Claim IDs are assigned
    per response, so two answers that say the same thing with different IDs
    look unstable, and two answers that say different things under the same
    IDs look stable. The statement set is the weaker but meaningful check.
    """
    if len(runs) < 2:
        return {"runs": len(runs), "cases_compared": 0}
    stable = {"action": 0, "scope": 0, "claim_ids": 0, "claim_statements": 0}
    compared = 0
    unstable_cases = []
    for case in manifest.get("cases", []):
        finding_id = str(case.get("finding_id", ""))
        values = [run.get(finding_id) for run in runs]
        if any(value is None for value in values):
            continue
        compared += 1
        normalized = [normalize(value) for value in values]
        agreement = {
            "action": len({item["action"] for item in normalized}) == 1,
            "scope": len({item["scope"] for item in normalized}) == 1,
            "claim_ids": len({
                tuple(item["confirmed_claim_ids"]) for item in normalized
            }) == 1,
            "claim_statements": len({
                tuple(item["claim_statements"]) for item in normalized
            }) == 1,
        }
        for key, agreed in agreement.items():
            stable[key] += int(agreed)
        if not agreement["action"]:
            unstable_cases.append(finding_id)
    return {
        "runs": len(runs),
        "cases_compared": compared,
        "cases_absent_from_some_run": sum(
            1 for case in manifest.get("cases", [])
            if any(run.get(str(case.get("finding_id", ""))) is None
                   for run in runs)
        ),
        **{
            f"{key}_agreement": round(count / compared, 4) if compared else None
            for key, count in stable.items()
        },
        "cases_with_unstable_action": sorted(unstable_cases),
    }
