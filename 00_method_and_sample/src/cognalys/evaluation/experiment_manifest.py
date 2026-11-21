#!/usr/bin/env python3
"""Record what a run was, and refuse to run it as something else.

A result is only a result of something. The runs in `outputs/` record a
pipeline commit and a flag saying the working tree was dirty, which is
almost the whole of it: the specifications were regenerated during the run,
external research was fetched live, the learning store carried state from
earlier runs into this one, and nothing wrote down the JDK, the Android SDK
or the resolved dependency versions the builds used. Re-running the same
findings therefore measures the pipeline *and* everything that drifted
underneath it, and the difference cannot be attributed.

A frozen experiment writes a manifest first and verifies it afterwards. The
verification is the point: a manifest that is only written is a label, and a
label does not stop the thing it names from changing.

What freezing forbids, and why each one is not a detail:

  specification generation   the pipeline writes the rule it is about to be
                             judged against, mid-run, from a model. A rule
                             that did not exist before the finding was
                             analysed cannot be the rule the finding was
                             measured against.
  unsnapshotted web research  an answer from the live web is not repeatable
                             and is not recorded.
  learning-store writes       a run that updates the store changes the
                             conditions of every run after it.

Enforcement is by environment variable, because the steps that must obey
are separate processes: `TCC_FROZEN_EXPERIMENT=1` is read by the
specification generator, the research client and the retrospective.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from cognalys.learning.knowledge import HISTORY_RULES_VERSION


FROZEN_ENVIRONMENT_VARIABLE = "TCC_FROZEN_EXPERIMENT"
# Set with the frozen variable by --experiment-live-research: web research
# not archived is fetched live (llm_providers._frozen_experiment) and stored
# in the research corpus; specifications and the learning store stay frozen.
LIVE_RESEARCH_VARIABLE = "TCC_EXPERIMENT_LIVE_RESEARCH"
MANIFEST_VERSION = 2
# Untracked files whose content is code the run executes. `git diff HEAD`
# does not see them, so without their own digest two runs with different
# versions of an untracked module recorded the same pipeline identity.
CODE_SUFFIXES = (".py", ".sh")


def frozen() -> bool:
    """Whether this process is part of a frozen experiment."""
    return str(os.environ.get(FROZEN_ENVIRONMENT_VARIABLE, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _run(*args: str, cwd: Optional[Path] = None) -> str:
    try:
        completed = subprocess.run(
            list(args), cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=60, errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def tree_digest(directory: Path, suffixes: Iterable[str] = (".json",)) -> Dict:
    """A hash over a directory's files, and the list it was taken over.

    The file names travel with the digest: a digest alone says two runs
    differ and not which rule appeared or vanished between them.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return {"status": "absent", "files": 0, "sha256": ""}
    files = sorted(
        path for path in directory.rglob("*")
        if path.is_file() and path.suffix in tuple(suffixes)
    )
    digest = hashlib.sha256()
    names = []
    for path in files:
        relative = path.relative_to(directory).as_posix()
        names.append(relative)
        digest.update(relative.encode("utf-8"))
        digest.update(file_digest(path).encode("utf-8"))
    return {
        "status": "present", "files": len(files),
        "sha256": digest.hexdigest(), "names": names,
    }


def pipeline_state(root: Path) -> Dict:
    """The code this run is of, including what is not committed.

    `uncommitted_changes: true` was all the runs recorded, which names the
    problem without identifying the code. The diff itself is hashed, so two
    runs from the same dirty tree are comparable and two from different ones
    are visibly not.
    """
    root = Path(root)
    commit = _run("git", "-C", str(root), "rev-parse", "HEAD")
    diff = _run("git", "-C", str(root), "diff", "HEAD")
    untracked = _run(
        "git", "-C", str(root), "ls-files", "--others", "--exclude-standard"
    )
    names = [name for name in untracked.splitlines() if name]
    untracked_code = sorted(name for name in names if name.endswith(CODE_SUFFIXES))
    digest = hashlib.sha256()
    for name in untracked_code:
        digest.update(name.encode("utf-8"))
        digest.update(file_digest(root / name).encode("utf-8"))
    return {
        "commit": commit,
        "branch": _run("git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"),
        "uncommitted_changes": bool(diff.strip()) or bool(untracked_code),
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else "",
        "untracked_files": names[:200],
        "untracked_code_files": untracked_code,
        "untracked_code_sha256": digest.hexdigest() if untracked_code else "",
    }


def python_environment() -> Dict:
    """The installed distributions this interpreter resolves imports from.

    requirements.txt pins almost nothing, so two machines that both pass
    the preflight can still run different parser or HTTP-client versions.
    """
    frozen_list = _run(sys.executable, "-m", "pip", "freeze", "--all")
    lines = sorted(line for line in frozen_list.splitlines() if line.strip())
    return {
        "executable": sys.executable,
        "distributions": len(lines),
        "sha256": hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
        if lines else "",
    }


def toolchain() -> Dict:
    """Versions of everything a build outcome depends on."""
    java = _run("java", "-version") or _run("javac", "-version")
    environment = python_environment()
    return {
        "python_environment_sha256": environment["sha256"],
        "python_executable": environment["executable"],
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "java": java.splitlines()[0] if java else "",
        "gradle": (_run("gradle", "--version") or "").splitlines()[0]
        if _run("gradle", "--version") else "",
        "android_home": os.environ.get("ANDROID_HOME", "")
        or os.environ.get("ANDROID_SDK_ROOT", ""),
        "java_home": os.environ.get("JAVA_HOME", ""),
    }


def capture(
    root: Path, corpus: Path, specifications: Path,
    model: Dict, budgets: Dict, learning_dir: Path,
    cohort: Optional[Iterable[str]] = None, condition: str = "G",
    research_corpus: Optional[Dict] = None, live_research: bool = False,
    pr_outcomes_dir: Optional[Path] = None, holdout_repository: bool = False,
) -> Dict:
    """Everything a later run has to match to be the same experiment.

    ``research_corpus`` (research_corpus.corpus_digest of the run's corpus)
    and ``live_research`` are recorded only when given, so a manifest of a run
    without them is the one written before they existed.
    """
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "condition": condition,
        "pipeline": pipeline_state(root),
        "toolchain": toolchain(),
        "corpus": {
            "path": str(corpus),
            "sha256": file_digest(corpus),
        },
        "specifications": tree_digest(specifications),
        # The executable oracles (validation/semgrep_rules): a rule that
        # changes changes what "repaired" means, even with the same specs.
        "detector_rules": tree_digest(
            Path(root) / "data" / "detector_rules", suffixes=(".yml", ".json"),
        ),
        "model": dict(model),
        "budgets": dict(budgets),
        "learning_store": {
            "path": str(learning_dir),
            "ledger_sha256": file_digest(Path(learning_dir) / "failure_ledger.json"),
            "knowledge_sha256": file_digest(
                Path(learning_dir) / "verified_knowledge.json"
            ),
            "frozen": frozen(),
            # Which earlier attempts planning and generation read from it
            # (learning.knowledge.finding_history).
            "history_rules_version": HISTORY_RULES_VERSION,
        },
        "cohort": sorted(cohort or []),
    }
    # The maintainers' answers to earlier pull requests (prs.pr_outcomes):
    # adjudication, planning and generation read them (fix_generation_common.
    # learning_context), so a run with more or other answers is another
    # experiment, as one with another learning store is (B12, 2026-11-12).
    if pr_outcomes_dir is None:
        from cognalys.prs.pr_outcomes import OUTCOMES_DIR as pr_outcomes_dir
    manifest["pr_outcomes"] = {"path": str(pr_outcomes_dir), **tree_digest(Path(pr_outcomes_dir))}
    if research_corpus is not None:
        # Named apart from "corpus", which is the findings CSV.
        manifest["research_corpus"] = dict(research_corpus)
    if live_research:
        manifest["web_research"] = {"mode": "live"}
    if holdout_repository:
        # Recorded only when on, so a run without it writes the manifest it
        # wrote before the option existed (B13).
        manifest["evaluation"] = {"holdout_repository": True}
    return manifest


# Fields whose change makes two runs different experiments. Anything not
# listed is recorded but does not invalidate a comparison by itself.
_COMPARED = (
    ("pipeline", "commit"),
    ("pipeline", "diff_sha256"),
    ("pipeline", "untracked_code_sha256"),
    ("corpus", "sha256"),
    ("specifications", "sha256"),
    ("detector_rules", "sha256"),
    ("model", "model"),
    ("model", "provider"),
    ("model", "reasoning_effort"),
    # The OpenAI tier the calls asked for: Flex halves the price and may
    # queue a call, so a comparison across tiers is declared, not assumed.
    ("model", "service_tier"),
    # The web-research calls' own effort (absent when they keep the run's).
    ("model", "research_reasoning_effort"),
    # Recorded and, until 2026-11-12, not compared: two runs with different
    # output ceilings, repository evidence, retries, deadlines or per-finding
    # limits are not given the same budget (PLANO_CORRECOES_DEFESA A2).
    ("budgets", "context_length"),
    ("budgets", "repository_context_budget"),
    ("budgets", "max_output_tokens"),
    ("budgets", "retries"),
    ("budgets", "workers"),
    ("budgets", "timeout"),
    ("budgets", "finding_max_calls"),
    ("budgets", "finding_max_seconds"),
    ("budgets", "step_timeout_minutes"),
    ("toolchain", "java"),
    ("toolchain", "python"),
    ("toolchain", "python_environment_sha256"),
    ("learning_store", "ledger_sha256"),
    ("learning_store", "knowledge_sha256"),
    # The maintainer answers the prompts read (B12). A manifest written before
    # the field recorded none, and is compared without it.
    ("pr_outcomes", "sha256"),
    # The research answers the run was served from (absent without one),
    # and how old an entry may be to be served.
    ("research_corpus", "sha256"),
    ("research_corpus", "max_age_days"),
    # Whether research missing from the archive was fetched live.
    ("web_research", "mode"),
    # Whether each finding's own repository's history was withheld (B13).
    ("evaluation", "holdout_repository"),
)


# Sections a manifest written before they were recorded does not have: there
# is nothing to compare, and such a manifest still verifies its own run.
_COMPARED_WHEN_RECORDED = frozenset({"pr_outcomes"})


def _research_corpus_differences(expected: Dict, observed: Dict) -> List[str]:
    """A live-research run's corpus: every entry the manifest recorded, still unchanged.

    Such a run stores what it researches, so its corpus grows; a later run
    against the manifest is the same experiment only when it is that run
    resumed (the same prompt-log folder) and the entries it started from are
    all there with the same text. Another run against the manifest would be
    served what the first one researched, and is a different experiment.
    """
    expected_corpus = expected.get("research_corpus") or {}
    observed_corpus = observed.get("research_corpus") or {}
    recorded = expected_corpus.get("entry_sha256")
    if recorded is None:
        left, right = expected_corpus.get("sha256"), observed_corpus.get("sha256")
        return [] if left == right else [
            f"research_corpus.sha256: manifest has {left!r}, this run has {right!r}"]
    differences = []
    if expected_corpus.get("run_log_dir") != observed_corpus.get("run_log_dir"):
        differences.append(
            f"research_corpus.run_log_dir: manifest has {expected_corpus.get('run_log_dir')!r}, "
            f"this run has {observed_corpus.get('run_log_dir')!r} (with live research only "
            "the run that wrote the manifest may resume against it)")
    current = observed_corpus.get("entry_sha256") or {}
    changed = sorted(key for key, digest in recorded.items() if current.get(key) != digest)
    if changed:
        differences.append(f"research_corpus: {len(changed)} entr"
                           f"{'y' if len(changed) == 1 else 'ies'} the manifest recorded "
                           "missing or changed")
    return differences


def verify(expected: Dict, observed: Dict) -> List[str]:
    """Differences that make `observed` a different experiment.

    Returns them named, in the order compared, so a divergence report reads
    as a list of things to fix rather than as a failed equality.
    """
    differences = []
    live = (expected.get("web_research") or {}).get("mode") == "live"
    for section, field in _COMPARED:
        if live and (section, field) == ("research_corpus", "sha256"):
            differences.extend(_research_corpus_differences(expected, observed))
            continue
        if section in _COMPARED_WHEN_RECORDED and section not in expected:
            continue
        left = (expected.get(section) or {}).get(field)
        right = (observed.get(section) or {}).get(field)
        if left != right:
            differences.append(
                f"{section}.{field}: manifest has {left!r}, this run has {right!r}"
            )
    expected_cohort = set(expected.get("cohort") or [])
    observed_cohort = set(observed.get("cohort") or [])
    missing = sorted(expected_cohort - observed_cohort)
    extra = sorted(observed_cohort - expected_cohort)
    if missing:
        differences.append(f"cohort: {len(missing)} finding(s) missing from this run")
    if extra:
        differences.append(f"cohort: {len(extra)} finding(s) this run added")
    return differences


def write(path: Path, manifest: Dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def read(path: Path) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def refusal(action: str, detail: str = "") -> str:
    """The message a frozen step prints before refusing to do something."""
    return (
        f"frozen experiment: {action} is not allowed while "
        f"{FROZEN_ENVIRONMENT_VARIABLE} is set"
        + (f" ({detail})" if detail else "")
        + ". An experiment that rewrites its own inputs mid-run cannot be "
        "compared with anything, including itself."
    )
