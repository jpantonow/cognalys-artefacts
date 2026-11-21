"""A run's corrections as patches: listed, written out, or applied elsewhere.

The solutions step applies each correction in its own git worktree, branched
from the tip of the branch a pull request would target (the contribution
base, not the commit the finding was analysed at), and records it in
application_results/execution_report.json. A correction's patch is that
worktree against its base commit. This module reads them back so a user can
see a patch (`cognalys diff`), put it in their own checkout (`cognalys
apply`) or go through them one by one (`cognalys review`), without a pull
request and without touching the pipeline's worktrees.

A correction is *safe* when nothing is left for a person to decide: a repair
decision (not one proposed for review), no manual review required, and a
build that passed. Everything else is *review*, like Ruff's unsafe fixes:
shown, never applied in bulk.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from cognalys.utils.disposition import BUILD_VALIDATION_PASSING_STATUSES, REPAIR_DECISIONS

REPORT = Path("application_results") / "execution_report.json"


@dataclass(frozen=True)
class Correction:
    finding_id: str
    apk: str
    method: str
    spec: str
    decision: str
    build_status: str
    manual_review: bool
    worktree: Path
    base_commit: str
    explanation: str = ""
    files: tuple[str, ...] = field(default_factory=tuple)

    @property
    def safe(self) -> bool:
        return (self.decision in REPAIR_DECISIONS and not self.manual_review
                and self.build_status in BUILD_VALIDATION_PASSING_STATUSES)

    @property
    def level(self) -> str:
        return "safe" if self.safe else "review"

    @property
    def name(self) -> str:
        # The id keeps two corrections of one method and spec apart.
        return f"{self.apk.removesuffix('.apk')}_{self.method}_{self.spec}_{self.finding_id}"


def corrections(run_dir: Path) -> list[Correction]:
    """The corrections a run applied, from its execution report ([] if none)."""
    path = Path(run_dir) / REPORT
    if not path.is_file():
        return []
    report = json.loads(path.read_text(encoding="utf-8"))
    found = []
    for item in report.get("per_solution_details") or []:
        if item.get("status") != "SUCCESS" or not item.get("worktree_path"):
            continue
        found.append(Correction(
            finding_id=str(item.get("finding_id", "")),
            apk=str(item.get("apk_name", "")),
            method=str(item.get("method_name", "")),
            spec=str(item.get("spec", "")),
            decision=str(item.get("decision", "")),
            build_status=str((item.get("build_validation") or {}).get("status", "not_run")).lower(),
            manual_review=bool(item.get("manual_review_required")),
            worktree=Path(str(item["worktree_path"])),
            base_commit=str(item.get("base_commit", "")),
            explanation=str(item.get("explanation", "")),
            files=tuple(str(entry.get("path", entry)) if isinstance(entry, dict) else str(entry)
                        for entry in item.get("changed_files") or []),
        ))
    return found


def _git(worktree: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(worktree), *arguments], capture_output=True,
                          text=True, check=check, errors="replace")


def patch(correction: Correction) -> str:
    """The correction as a unified diff against its base commit.

    Includes files the correction created but nothing committed yet (a run
    stopped before the PR step leaves them untracked).
    """
    if not correction.worktree.is_dir():
        raise FileNotFoundError(f"worktree no longer exists: {correction.worktree}")
    text = _git(correction.worktree, "diff", "--binary", correction.base_commit).stdout
    untracked = _git(correction.worktree, "ls-files", "--others", "--exclude-standard").stdout
    for name in untracked.splitlines():
        # --no-index exits 1 when the files differ, which they always do here.
        text += _git(correction.worktree, "diff", "--binary", "--no-index", "/dev/null", name,
                     check=False).stdout
    return text


def apply(correction: Correction, target: Path, check_only: bool = False) -> tuple[bool, str]:
    """Apply the correction to the git checkout at ``target``, without committing.

    `git apply --3way` places the patch even when the checkout moved on from
    the base commit, leaving conflict markers where it cannot; nothing is
    written when the checkout cannot take it at all.
    """
    target = Path(target)
    if not (target / ".git").exists():
        return False, f"{target} is not a git checkout"
    diff = patch(correction)
    if not diff.strip():
        return False, "the correction has no changes"
    arguments = ["git", "-C", str(target), "apply", "--3way", "--whitespace=nowarn"]
    if check_only:
        arguments = ["git", "-C", str(target), "apply", "--check", "--whitespace=nowarn"]
    completed = subprocess.run([*arguments, "-"], input=diff, capture_output=True, text=True,
                               errors="replace")
    head = subprocess.run(["git", "-C", str(target), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    note = ("" if not head or head.startswith(correction.base_commit[:12])
            else f" (your checkout is at {head[:12]}, the patch was made at "
                 f"{correction.base_commit[:12]})")
    if completed.returncode != 0:
        return False, (completed.stderr.strip() or "git apply refused the patch") + note
    return True, ("applies cleanly" if check_only
                  else "applied and staged, not committed (git diff --cached shows it)") + note
