"""The security-impact class of a pull request, read from its finding's adjudication.

The PR survey counted incorporated pull requests without saying which ones
fixed something with a security consequence and which only satisfied the rule
(docs/BANCA_SIMULADA.md, M2). The adjudication already answers that in its
``runtime_security_impact`` field; this module turns that answer into a class
recorded on the pull request (`*_pr.json`), in its outcome record
(pr_outcomes) and counted by scripts/levantar_prs.py. No gate or decision
reads it; the contact-first issue's title does
(issues.issue_text.contact_issue_title), so a draft without a confirmed
security impact is not announced as a security issue.

The class comes from the adjudication's own field, never from the
specification or the algorithm:

- ``security_impact``: runtime_security_impact "confirmed";
- ``no_security_impact``: "non_security", a violation of the rule without a
  security consequence (a rule-only fix);
- ``impact_not_established``: "not_established", the adjudication looked and
  could not establish an impact either way;
- ``not_recorded``: no adjudication at all (a v1 analysis, a run whose
  artifacts are gone), or one that did not assess the impact: "unknown" (the
  evidence does not address the hazard) or any other value. The survey's
  sentence under its table says both.
"""
from __future__ import annotations

import json
from pathlib import Path

SECURITY_IMPACT = "security_impact"
NO_SECURITY_IMPACT = "no_security_impact"
IMPACT_NOT_ESTABLISHED = "impact_not_established"
NOT_RECORDED = "not_recorded"
IMPACT_CLASSES = (SECURITY_IMPACT, NO_SECURITY_IMPACT, IMPACT_NOT_ESTABLISHED, NOT_RECORDED)

_CLASS_OF_RUNTIME_IMPACT = {
    "confirmed": SECURITY_IMPACT,
    "non_security": NO_SECURITY_IMPACT,
    "not_established": IMPACT_NOT_ESTABLISHED,
}


def impact_class(adjudication: dict) -> str:
    """The impact class of an adjudication's runtime_security_impact."""
    if not isinstance(adjudication, dict):
        return NOT_RECORDED
    value = str(adjudication.get("runtime_security_impact", "") or "").strip()
    return _CLASS_OF_RUNTIME_IMPACT.get(value, NOT_RECORDED)


def adjudication_of(evidence_analysis: dict) -> dict:
    """The v2 adjudication an evidence analysis carries, or {} (v1, none)."""
    if not isinstance(evidence_analysis, dict):
        return {}
    adjudication = (evidence_analysis.get("v2") or {}).get("adjudication")
    return adjudication if isinstance(adjudication, dict) else {}


def recorded_impact_class(record: dict) -> str:
    """The class a PR record carries, or not_recorded when it has none."""
    value = record.get("impact_class") if isinstance(record, dict) else None
    return value if value in IMPACT_CLASSES else NOT_RECORDED


def _read_object(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def published_impact_class(created_record: Path) -> str:
    """The impact class of the pull request a `*_pr_created.json` records.

    The `*_pr.json` beside it carries the class since M2. A pull request
    published before that has none, and one whose evidence lookup failed when
    the record was written carries not_recorded; for both the adjudication is
    read from the run that published it: the finding's
    `v2_artifacts/<apk>/*_adjudication.json`, else the evidence analysis of
    its solution file. Neither found: not_recorded.
    """
    from cognalys.llm.fix_artifacts import _solution_files
    from cognalys.utils.artifact_names import filename_component
    from cognalys.utils.pr_text import analysed_identity

    created_record = Path(created_record)
    metadata = _read_object(created_record.with_name(
        created_record.name.replace("_pr_created.json", "_pr.json")))
    # pr_generator writes the field whether or not it found the evidence, so
    # a recorded not_recorded is no answer: the run's artifacts may have one.
    if recorded_impact_class(metadata) != NOT_RECORDED:
        return recorded_impact_class(metadata)
    if not metadata:
        return NOT_RECORDED
    solutions = created_record.parent.parent / "error_solutions"
    identity = analysed_identity(metadata)
    try:
        apks = sorted(path.name for path in solutions.iterdir() if path.is_dir())
    except OSError:
        return NOT_RECORDED
    for apk in apks:
        # The record is named <apk>_<method>_<spec> (artifact_names).
        if not created_record.name.startswith(filename_component(apk, "unknown_apk") + "_"):
            continue
        for path, data in _solution_files(solutions, apk, identity["csv_class"],
                                          identity["method_name"], identity["spec"]):
            prefix = path.name[:-len("_solution.json")]
            artifact = _read_object(solutions / "v2_artifacts" / apk / f"{prefix}_adjudication.json")
            return impact_class(artifact or adjudication_of(data.get("evidence_analysis")))
    return NOT_RECORDED
