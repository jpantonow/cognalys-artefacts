"""The findings earlier runs left without a fix, as a findings file to run again.

After the pipeline changes, rerunning every finding of a run pays again for
the ones that already ended in a pull request. exp_final run2 had 34 findings
and 9 validated patches; what was worth another analysis were the others.
This reads the runs' outcomes (oldest first, the latest analysis of a finding
wins, see finding_statistics.combine_runs), keeps the findings whose outcome
is not settled, and writes their rows from the findings files the runs read
(run_inputs.json), so the subset runs exactly as the original did.

Settled by default: a ready pull request, a fix the target branch no longer
needs, an external contract (a server fixes the algorithm; no change on the
pipeline's side alters that) and a recognised false positive.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

SETTLED_OUTCOMES = (
    "PR pronto",
    "correção não aplicável: código mudou upstream",
    "correção não aplicável: arquivo fora do branch de destino",
    "contrato externo impede a correção",
    "falso positivo reconhecido",
    "violação sem impacto de segurança",
)

Key = Tuple[str, str, str, str]


def unpatched_findings(runs: Sequence[Path], include: Iterable[str] = ()):
    """The FindingResults of ``runs`` whose outcome is not settled."""
    from cognalys.api import load_results

    settled = set(SETTLED_OUTCOMES) - set(include)
    opened = opened_pull_requests(runs)
    return [finding for finding in load_results(*runs).findings
            if outcome_of(finding) not in settled
            and (finding.apk, finding.method, finding.spec) not in opened]


def opened_pull_requests(runs: Sequence[Path]) -> set:
    """(apk, method, spec) of every pull request any of the runs opened.

    The latest analysis wins in combine_runs, so a later subset that ended in
    an issue hid the pull request run2 had already opened for Droid SCEP.
    """
    opened = set()
    for run in runs:
        # The created file is GitHub's answer; the finding is named by the
        # file next to it and by the APK in both names.
        for path in Path(run).glob("pull_requests/*.apk_*_pr_created.json"):
            try:
                pr = json.loads(path.with_name(path.name.replace("_pr_created.json", "_pr.json"))
                                .read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            key = (path.name.split(".apk_")[0] + ".apk",
                   str(pr.get("method_name", "") or ""), str(pr.get("spec", "") or ""))
            if all(key):
                opened.add(key)
    return opened


def outcome_of(finding) -> str:
    """The finding's outcome, read through an issue to the decision behind it.

    final_outcome ranks an issue above the decision, so password-monitor's
    HIBP digest, a contract with a server, reads "issue para o mantenedor"
    and would be run again for nothing.
    """
    from cognalys.reporting.finding_statistics import final_outcome

    if finding.outcome.startswith("issue"):
        return final_outcome({"analysed": True, "decision": finding.decision,
                              "detector_violation": finding.detector_violation})[0]
    return finding.outcome


def findings_files(runs: Sequence[Path]) -> List[Path]:
    """The findings files the runs read, newest run first, without repeats."""
    files: List[Path] = []
    for run in reversed(list(runs)):
        try:
            inputs = json.loads((Path(run) / "run_inputs.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        path = Path(str(inputs.get("findings_csv") or ""))
        if path.name and path not in files:
            files.append(path)
    return files


def row_key(row: Dict[str, str]) -> Key:
    return (row.get("apk", ""), row.get("class", ""), row.get("method", ""), row.get("spec", ""))


def write_unpatched(runs: Sequence[Path], output: Path, include: Iterable[str] = ()) -> Dict:
    """Write the unsettled findings' rows to ``output``; what was kept and missed."""
    wanted = {finding.key: finding for finding in unpatched_findings(runs, include)}
    fieldnames: List[str] = []
    rows: Dict[Key, Dict[str, str]] = {}
    for path in findings_files(runs):
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for name in reader.fieldnames or []:
                if name not in fieldnames:
                    fieldnames.append(name)
            for row in reader:
                key = row_key(row)
                # Newest run's file first: its row is the one that run read.
                if key in wanted and key not in rows:
                    rows[key] = row
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow(rows[key])
    return {"kept": [wanted[key] for key in sorted(rows)],
            "missing": [wanted[key] for key in sorted(set(wanted) - set(rows))]}
