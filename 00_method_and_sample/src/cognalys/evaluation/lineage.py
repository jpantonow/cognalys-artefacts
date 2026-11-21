#!/usr/bin/env python3
"""The chain from a detector observation to a published patch.

The paper cannot presently show which findings its numbers are about. The
corpus holds 142,580 observations, the sample 133, the pilot 11 decisions
and 5 patches, and nothing in the repository states which of the 133 the 11
are, or which of the 11 produced the 5. Without that chain a reader cannot
tell whether the five patches are five of the eleven decisions, and neither
can the author.

Nothing new has to be written during a run to recover it: every link
already exists in an artifact. This module joins them --

    corpus observation -> finding -> APK (and its version) -> repository
    -> run -> decision -> terminal stage -> pull request

-- and reports, per finding, how many observations collapse into it, which
runs saw it and what each concluded. Where the checkout is available it also
records ownership from the resolved Gradle coordinates rather than from the
package-prefix guess the sampling script uses, and whether the APK's release
tag still exists.

Two properties this is for. A finding reported in three observations is one
finding, not three, and counting the observations would inflate every rate
that uses it as a denominator. And several APKs bundling the same library
share one defect: those are not independent replications, and an analysis
that treats them as such overstates what it has seen.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional


from cognalys.evaluation.coverage import is_published  # noqa: E402
from cognalys.evaluation.decisions import action_for  # noqa: E402


def read_corpus(path: Path) -> List[Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def observation_identity(row: Dict[str, str]) -> str:
    """The finding an observation is about."""
    identity = str(row.get("unique_msg", "") or "").strip()
    if identity:
        return identity
    return "|".join(
        str(row.get(key, "") or "").strip()
        for key in ("apk", "class", "method", "message")
    )


def collapse_observations(rows: Iterable[Dict[str, str]]) -> Dict[str, Dict]:
    """One record per finding, carrying how many observations it came from."""
    findings: Dict[str, Dict] = {}
    for row in rows:
        identity = observation_identity(row)
        if not identity:
            continue
        record = findings.setdefault(identity, {
            "unique_msg": identity,
            "spec": str(row.get("spec", "") or ""),
            "apk": str(row.get("apk", "") or ""),
            "class": str(row.get("class", "") or ""),
            "method": str(row.get("method", "") or ""),
            "repo_url": str(row.get("repo_url", "") or ""),
            "apk_url": str(row.get("apk_url", "") or ""),
            "observations": 0,
            # Which applications carry this same finding. A defect in a
            # bundled library is reported once per application that ships
            # it, and those rows are one finding seen many times, not many
            # findings. Kept in full so the collapse can be audited.
            "apks": [],
            "repositories": [],
        })
        record["observations"] += 1
        for key, target in (("apk", "apks"), ("repo_url", "repositories")):
            value = str(row.get(key, "") or "").strip()
            if value and value not in record[target]:
                record[target].append(value)
    for record in findings.values():
        record["apk_count"] = len(record.get("apks") or [])
        record["repository_count"] = len(record.get("repositories") or [])
    return findings


def run_outcomes(run_root: Path) -> Dict[str, Dict]:
    """What one run concluded per finding, from its retrospective."""
    report = Path(run_root) / "retrospective" / "retrospective.json"
    if not report.is_file():
        return {}
    try:
        value = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    outcomes = {}
    for finding in value.get("findings", []) or []:
        if not isinstance(finding, dict):
            continue
        identity = str(finding.get("unique_error_id", "") or "")
        if not identity:
            continue
        decision = str(finding.get("decision", "") or "")
        outcomes[identity] = {
            "run_id": str(value.get("run_id", Path(run_root).name)),
            "decision": decision,
            "action": action_for(decision),
            "terminal_stage": str(finding.get("terminal_stage", "") or ""),
            "signatures": list(finding.get("signatures", []) or []),
            # Only an opened pull request, by the funnel's own definition; a
            # local draft path is kept apart so it cannot read as published.
            "pull_request": (
                str(finding.get("pull_request", "") or "")
                if is_published(finding) else ""
            ),
            "pull_request_draft": (
                "" if is_published(finding)
                else str(finding.get("pull_request", "") or "")
            ),
            "pipeline": value.get("pipeline", {}),
        }
    return outcomes


def _repository_for(repositories: Path, repo_url: str, apk: str) -> Optional[Path]:
    """The local clone of a finding's repository, if the directory has one."""
    repositories = Path(repositories)
    if not repositories.is_dir():
        return None
    candidates = []
    name = str(repo_url or "").rstrip("/").removesuffix(".git").rsplit("/", 1)
    if len(name) == 2 and name[1]:
        candidates.append(name[1])
    package = str(apk or "").rsplit("_", 1)[0]
    if package:
        candidates.append(package)
    for candidate in candidates:
        path = repositories / candidate
        if (path / ".git").exists():
            return path
    return None


def build_lineage(
    corpus: Path, run_roots: Iterable[Path],
    repositories: Optional[Path] = None,
    resolve_checkout_facts: bool = True,
) -> Dict:
    """Join every link, and say which links are missing rather than omitting.

    `resolve_checkout_facts` reads each clone for the release tag and the
    ownership of the reported class. It is the slow part, and turning it off
    leaves those fields as `not_collected` instead of as a guess.
    """
    findings = collapse_observations(read_corpus(corpus))
    seen_runs = []
    for run_root in run_roots:
        outcomes = run_outcomes(run_root)
        if not outcomes:
            continue
        seen_runs.append(Path(run_root).name)
        for identity, outcome in outcomes.items():
            record = findings.setdefault(identity, {
                "unique_msg": identity, "spec": "", "apk": "", "class": "",
                "method": "", "repo_url": "", "apk_url": "",
                "observations": 0,
                "not_in_corpus": True,
            })
            record.setdefault("runs", []).append(outcome)
    for record in findings.values():
        record.setdefault("runs", [])
        record["run_count"] = len(record["runs"])
        record["pull_requests"] = sorted({
            run["pull_request"] for run in record["runs"] if run["pull_request"]
        })
        record["actions"] = sorted({run["action"] for run in record["runs"]})
        record["checkout"] = _checkout_facts(
            record, repositories, resolve_checkout_facts,
        )
    return {
        "corpus": str(corpus),
        "runs": sorted(seen_runs),
        "findings": findings,
        "totals": totals(findings),
    }


def _checkout_facts(
    record: Dict, repositories: Optional[Path], resolve: bool,
) -> Dict:
    if not resolve or repositories is None:
        return {"status": "not_collected"}
    repo = _repository_for(repositories, record.get("repo_url", ""),
                           record.get("apk", ""))
    if repo is None:
        return {"status": "no_local_clone"}
    try:
        from cognalys.extraction.evidence_facts import artifact_ownership
        from cognalys.extraction.release_ref import resolve_release_ref

        return {
            "status": "collected",
            "repository": str(repo),
            "release_ref": resolve_release_ref(repo, record.get("apk", "")),
            "ownership": artifact_ownership(repo, record.get("class", "")),
        }
    except Exception as exc:  # noqa: BLE001 -- recorded, never fatal
        return {"status": "unavailable", "detail": str(exc)[:200]}


def totals(findings: Dict[str, Dict]) -> Dict:
    """Counts a funnel table can be built from, each with its own meaning."""
    values = list(findings.values())
    adjudicated = [record for record in values if record["runs"]]
    with_patch = [
        record for record in adjudicated
        if any(action in {"repair_unattended", "repair_for_review"}
               for action in record["actions"])
    ]
    published = [record for record in adjudicated if record["pull_requests"]]
    by_repository: Dict[str, int] = {}
    for record in values:
        key = record.get("repo_url", "")
        if not key:
            continue
        by_repository[key] = by_repository.get(key, 0) + 1
    # Several applications bundling one library report the same defect. The
    # widest such cluster is reported so an analysis does not read those
    # rows as independent replications.
    shared = sorted(
        (record for record in values if record.get("apk_count", 0) > 1),
        key=lambda record: -record["apk_count"],
    )
    return {
        "observations": sum(record["observations"] for record in values),
        "findings": len(values),
        "findings_in_corpus": sum(
            1 for record in values if not record.get("not_in_corpus")
        ),
        "findings_adjudicated": len(adjudicated),
        "findings_with_a_patch": len(with_patch),
        "findings_published": len(published),
        "distinct_repositories": len(by_repository),
        "findings_without_repository": sum(
            1 for record in values if not record.get("repo_url")
        ),
        "findings_shared_by_several_apks": len(shared),
        "widest_shared_finding": (
            {
                "unique_msg": shared[0]["unique_msg"],
                "apk_count": shared[0]["apk_count"],
            } if shared else {}
        ),
        "findings_per_repository_max": max(by_repository.values(), default=0),
    }


def as_rows(lineage: Dict) -> List[Dict]:
    """One flat row per finding, for a CSV a reader can follow by hand."""
    rows = []
    for record in lineage["findings"].values():
        last = record["runs"][-1] if record["runs"] else {}
        checkout = record.get("checkout", {})
        rows.append({
            "unique_msg": record["unique_msg"],
            "spec": record["spec"],
            "apk": record["apk"],
            "repo_url": record["repo_url"],
            "observations": record["observations"],
            "runs": record["run_count"],
            "last_run": last.get("run_id", ""),
            "last_decision": last.get("decision", ""),
            "last_action": last.get("action", ""),
            "terminal_stage": last.get("terminal_stage", ""),
            "pull_request": "; ".join(record["pull_requests"]),
            "release_ref_status": (
                checkout.get("release_ref", {}).get("status", "")
                if checkout.get("status") == "collected" else checkout.get("status", "")
            ),
            "ownership_status": (
                checkout.get("ownership", {}).get("status", "")
                if checkout.get("status") == "collected" else ""
            ),
            "owning_artifact": (
                checkout.get("ownership", {}).get("artifact", "")
                if checkout.get("status") == "collected" else ""
            ),
        })
    return sorted(rows, key=lambda row: (row["spec"], row["unique_msg"]))
