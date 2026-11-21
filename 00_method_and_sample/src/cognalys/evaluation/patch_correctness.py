#!/usr/bin/env python3
"""When a patch may be called correct, from the evidence validation recorded.

"It compiles" was the strongest claim a validated patch carried, and a paper
table that counts compiling patches as fixes overstates what was shown. A
patch is called correct here only when three things were each shown:

  reproduced and eliminated   the violation was observed on the unpatched
                              code and is absent after the patch, by an
                              executable oracle -- today the reporting
                              detector's replay (validation.detector_replay);
  well-formed                 the patched project builds, and a failing build
                              is not attributed to the patch;
  controls preserved          the existing tests of the changed modules ran
                              and passed.

Anything short of that is `unverified` with the missing evidence named, not
a softer kind of correct: a finding from a tool no adapter re-runs, a module
with no tests, and a replay whose baseline never fired are three different
gaps and each needs a different fix. `incorrect` is kept for evidence that
contradicts the patch (the alert still fires, the method was deleted, the
build fails because of the generated code), and `environment_failure` for
runs that could not produce evidence at all.

This is a measurement for the experimental profile. It never changes a
validation status and never decides whether a pull request is opened.
"""
from __future__ import annotations

from typing import Dict, Iterable, List


from cognalys.utils.disposition import BUILD_VALIDATION_PASSING_STATUSES  # noqa: E402
from cognalys.validation.detector_replay import (  # noqa: E402
    CONSTRAINT_NOT_COVERED, DETECTOR_MISMATCH, METHOD_REMOVED, METHOD_RENAMED,
    NO_DETECTOR_COVERAGE, UNTRUSTED_STATES, reclassify,
)

CORRECT = "correct"
INCORRECT = "incorrect"
UNVERIFIED = "unverified"
ENVIRONMENT_FAILURE = "environment_failure"
VERDICTS = (CORRECT, INCORRECT, UNVERIFIED, ENVIRONMENT_FAILURE)

# The pipeline's own passing set, so a pass the validator records (an
# analysed-revision build, a production-only build) is not read here as a
# failure "not attributed to the patch".
DEFAULT_PASSING = BUILD_VALIDATION_PASSING_STATUSES


def _violation(record: Dict, satisfied: List[str], missing: List[str],
               contradicted: List[str]) -> None:
    replay = reclassify(record.get("detector_replay") or {})
    outcome = str(replay.get("outcome", "") or "not_run")
    if outcome == "repaired":
        satisfied.append("violation_reproduced_and_eliminated")
    elif outcome in {"unchanged", "introduced"}:
        contradicted.append(f"detector_replay_{outcome}")
    elif outcome == METHOD_REMOVED:
        contradicted.append("method_removed_to_silence_the_alert")
    # Neither contradicts the patch: the replayed rule disagrees with the
    # reporting detector about the patched value (Fread's "TLS"), or looks
    # for a method name the patch renamed (Orgzly's sha1 -> sha256).
    elif outcome == DETECTOR_MISMATCH:
        missing.append("replayed_rule_rejects_a_value_the_reporter_accepts")
    elif outcome == METHOD_RENAMED:
        missing.append("replayed_rule_matches_the_old_method_name")
    elif outcome == "not_reproduced":
        missing.append("violation_not_reproduced_on_the_baseline")
    elif outcome == NO_DETECTOR_COVERAGE or outcome == "not_applicable":
        missing.append("no_executable_oracle_for_this_detector")
    elif outcome == CONSTRAINT_NOT_COVERED:
        missing.append("no_executable_oracle_for_this_constraint")
    elif outcome in UNTRUSTED_STATES:
        missing.append(f"detector_replay_{outcome}")
    else:
        missing.append("detector_replay_not_run")
    # The source-level predicate is a second, independent signal: it cannot
    # establish reproduction, but it can contradict a patch.
    revalidation = record.get("finding_revalidation") or {}
    if revalidation.get("status") == "failed":
        contradicted.append("source_predicate_still_present")


def _build(record: Dict, passing: Iterable[str], satisfied: List[str],
           missing: List[str], contradicted: List[str]) -> bool:
    """Returns False when the environment produced no build evidence."""
    phases = record.get("phases") or {}
    if any((phase or {}).get("status") == "infrastructure_failure"
           for phase in phases.values()):
        return False
    status = str(record.get("status", "") or "")
    if status in passing:
        satisfied.append("builds")
    elif record.get("reason") == "original_finding_source_predicate_still_present":
        # Failed by the predicate, which _violation already reports.
        satisfied.append("builds")
    elif record.get("failure_attribution") == "generated_patch":
        contradicted.append("build_fails_because_of_the_patch")
    elif status in {"", "not_run"}:
        missing.append("build_not_run")
    else:
        missing.append(
            "build_fails_for_a_reason_not_attributed_to_the_patch"
        )
    return True





def assess(record: Dict, passing: Iterable[str] = DEFAULT_PASSING) -> Dict:
    """The verdict for one build-validation record, with its reasons."""
    satisfied: List[str] = []
    missing: List[str] = []
    contradicted: List[str] = []
    if not _build(record, set(passing), satisfied, missing, contradicted):
        return {"verdict": ENVIRONMENT_FAILURE, "satisfied": [],
                "missing": ["build_infrastructure_failed"], "contradicted": []}
    _violation(record, satisfied, missing, contradicted)
    if contradicted:
        verdict = INCORRECT
    elif missing:
        verdict = UNVERIFIED
    else:
        verdict = CORRECT
    return {"verdict": verdict, "satisfied": satisfied, "missing": missing,
            "contradicted": contradicted}


def tally(records: Iterable[Dict], passing: Iterable[str] = DEFAULT_PASSING) -> Dict:
    """Verdict counts and the most frequent missing evidence, over records."""
    verdicts = {verdict: 0 for verdict in VERDICTS}
    missing: Dict[str, int] = {}
    for record in records:
        result = assess(record, passing)
        verdicts[result["verdict"]] += 1
        for reason in result["missing"]:
            missing[reason] = missing.get(reason, 0) + 1
    return {"verdicts": verdicts,
            "missing_evidence": dict(sorted(missing.items(),
                                            key=lambda item: -item[1]))}
