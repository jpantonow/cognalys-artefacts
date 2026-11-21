#!/usr/bin/env python3
"""
Run the complete TCC pipeline:

extraction -> optional build -> LLM fixes -> apply solutions -> PR metadata -> optional publish
"""

from __future__ import annotations

from cognalys.sources import read_rows
from cognalys.workspace import library_data, workspace_root
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


from cognalys.utils.disposition import BUILD_VALIDATION_PASSING_STATUSES  # noqa: E402
from cognalys.utils.preflight import run_preflight  # noqa: E402
from cognalys.utils.step_outcome import PARTIAL_SUCCESS_EXIT_CODE  # noqa: E402
from cognalys.utils.process_tree import (  # noqa: E402
    STEP_TIMEOUT_EXIT_CODE, run_with_deadline,
)

# Generous on purpose: an LLM step over a large batch legitimately runs for
# hours, and this deadline exists to end a step that has stopped, not one
# that is slow. Tune it from step_timings.json, which records every step.
DEFAULT_STEP_TIMEOUT_MINUTES = 720.0


PIPELINE_STEPS = (
    "rv_import", "filter_v3", "spec_generation", "cloning", "repository_setup", "extraction", "build", "spec_context",
    "detector_rules", "llm", "solutions", "validation", "validation_fix", "revalidation", "prs",
    "preview", "publish", "fix_prs", "revalidation_fix_prs", "issues", "issues_cleanup"
)


@dataclass(frozen=True)
class PipelinePaths:
    root: Path
    input_csv_file: Path
    csv_file: Path
    cloned_projects_dir: Path
    extracted_methods_dir: Path
    build_output_dir: Path
    error_solutions_dir: Path
    application_results_dir: Path
    worktrees_dir: Path
    pull_requests_dir: Path
    issues_dir: Path
    previews_dir: Path


class _FindingsFile(argparse.Action):
    """--findings, repeatable: the first file is csv_file, all are findings_files."""

    def __call__(self, parser, namespace, value, option_string=None):
        files = list(getattr(namespace, "findings_files", None) or [])
        files.append(value)
        namespace.findings_files = files
        setattr(namespace, self.dest, files[0])


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    """The findings CSV, the optional filter and the directories every step reads or writes."""
    parser.add_argument(
        "--csv-file", "--findings",
        dest="csv_file",
        action=_FindingsFile,
        default="data/processed/fdroid_prs_top190.csv",
        help=(
            "Findings file: CSV, JSON Lines (.jsonl), a JSON array (.json), "
            "SARIF or Semgrep's --json report. Repeat it to analyse several "
            "files in one run. Columns may use common other names "
            "(className, rule_id, ...), and a finding may be located by "
            "source_file and line instead of class and method (with "
            "--findings-repo). `cognalys check` says what a file lacks."
        ),
    )
    parser.add_argument(
        "--findings-repo",
        default="",
        help=(
            "Local checkout the findings are about. It resolves findings "
            "located by file and line, and fills apk, repo_url and commit "
            "where they are empty."
        ),
    )
    parser.add_argument(
        "--column-map",
        default="",
        help=(
            "JSON object mapping corpus columns to the findings file's own "
            "column names ({\"apk\": \"package\", ...}), for findings exported "
            "by another tool. The file is converted once, at the start of the "
            "run, to findings.jsonl in the run directory, which every step "
            "then reads."
        ),
    )
    parser.add_argument(
        "--enable-filter-v3",
        action="store_true",
        help=(
            "Run filtro_v3 as the first step and use its output CSV in all "
            "subsequent CSV-consuming steps. Disabled by default."
        ),
    )
    parser.add_argument(
        "--filter-v3-output",
        default="data/processed/errors_amostragem_v3.csv",
        help="Output CSV produced when --enable-filter-v3 is active.",
    )
    parser.add_argument(
        "--filter-v3-input",
        default="data/raw/errors.csv",
        help=(
            "Raw, unfiltered CSV consumed by filtro_v3. This is intentionally "
            "independent from --csv-file."
        ),
    )
    parser.add_argument(
        "--enable-rv-import",
        action="store_true",
        help=(
            "Run import_rv_misuses as the first step: convert a runtime-"
            "verification misuse package into the findings CSV and use it in "
            "all subsequent CSV-consuming steps. Disabled by default."
        ),
    )
    parser.add_argument(
        "--rv-import-dir",
        default="data/raw/exp_novo",
        help="Misuse package directory read by the rv_import step.",
    )
    parser.add_argument(
        "--rv-import-output",
        default="data/processed/rv_import_findings.csv",
        help="Findings CSV written by the rv_import step.",
    )
    parser.add_argument(
        "--run-dir",
        default="",
        help=(
            "One directory for this run's outputs: error_methods, "
            "error_solutions, application_results, pull_requests, issues "
            "and previews are created inside it. A directory option given "
            "explicitly still wins. Clones and worktrees stay shared."
        ),
    )
    parser.add_argument(
        "--cloned-projects-dir",
        default="outputs/cloned_projects",
        help="Directory where repositories are cloned and later built.",
    )
    parser.add_argument(
        "--extracted-methods-dir",
        default="outputs/error_methods_tracked",
        help="Directory where extracted method JSON files are written.",
    )
    parser.add_argument(
        "--build-output-dir",
        default="outputs/built_apks_no_proguard",
        help="Directory for APK build logs and reports.",
    )
    parser.add_argument(
        "--error-solutions-dir",
        default="outputs/error_solutions",
        help="Directory where LLM-generated solution JSON files are written.",
    )
    parser.add_argument(
        "--application-results-dir",
        default="outputs/application_results",
        help="Directory where solution application reports are written.",
    )
    parser.add_argument(
        "--worktrees-dir",
        default="outputs/finding_worktrees",
        help="Directory containing one independent Git worktree per finding.",
    )
    parser.add_argument(
        "--pull-requests-dir",
        default="outputs/pull_requests",
        help="Directory where PR metadata JSON files are written.",
    )


def _add_publication_arguments(parser: argparse.ArgumentParser) -> None:
    """Publishing pull requests and issues, revalidation, previews and the retrospective."""
    parser.add_argument(
        "--publish-prs",
        action="store_true",
        help="Run the optional step that forks, pushes and opens GitHub PRs.",
    )
    parser.add_argument(
        "--full-revalidation",
        action="store_true",
        help=(
            "Rebuild every finding in the revalidation step, including those "
            "that already passed and were not repaired. By default only "
            "repaired findings and findings that did not pass are rebuilt."
        ),
    )
    parser.add_argument(
        "--gradle-build-cache",
        action="store_true",
        help=(
            "Pass --build-cache to the Gradle builds of the validation steps "
            "so patched, baseline and repair builds share task outputs. Off "
            "by default."
        ),
    )
    parser.add_argument(
        "--allow-duplicate-pr",
        action="store_true",
        help=(
            "Open a new pull request even when an earlier run already "
            "published one for the same finding that may still be open. "
            "Forwarded only to the PR publication step."
        ),
    )
    parser.add_argument(
        "--allow-stale-review",
        action="store_true",
        help=(
            "Publish a draft that did not pass the current behaviour review "
            "(none recorded, or an older contract). Forwarded only to the PR "
            "publication step."
        ),
    )
    parser.add_argument(
        "--approve-reviewed-finding",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help=(
            "Authorize publication of a specific human-reviewed finding. "
            "Forwarded only to the PR and issue publication steps; a finding "
            "not named is only a dry run there."
        ),
    )
    parser.add_argument(
        "--publish-without-build-validation",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help=(
            "DANGEROUS. Also publish this finding when the build never ran "
            "because the repository cannot be built at all -- never when the "
            "build ran and the patch failed to compile. Nothing then "
            "establishes that the patch compiles. Can be repeated."
        ),
    )
    parser.add_argument(
        "--publish-fix-prs",
        action="store_true",
        help="Commit and push fix_prs revisions to their existing PR branches "
             "(only those of findings named by --approve-revision).",
    )
    parser.add_argument(
        "--approve-revision",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help="With --publish-fix-prs: push this finding's revalidated revision "
             "(repeatable). Forwarded only to revalidation_fix_prs.",
    )
    parser.add_argument(
        "--skip-fix-prs-compile-validation",
        action="store_true",
        help=(
            "Do not actually compile a fix_prs revision before publishing "
            "it (default: compile it with the same build machinery as "
            "validation/revalidation; ktlintCheck alone only checks "
            "formatting and would pass on code that does not compile)."
        ),
    )
    parser.add_argument(
        "--skip-fix-prs-pr-state-check",
        action="store_true",
        help=(
            "Do not re-fetch a PR's live state before revising it (default: "
            "check, since a PR closed or merged after its local snapshot "
            "was written should not be revised or pushed to)."
        ),
    )
    parser.add_argument(
        "--self-audit-prs",
        action="store_true",
        help=(
            "In the fix_prs step, re-check a PR with no maintainer feedback "
            "against the pipeline's current deterministic correctness gates "
            "and revise whatever they reject (default: off). Lets a gate "
            "added after publication repair the pull requests that predate "
            "it, instead of waiting for someone to notice the defect."
        ),
    )
    parser.add_argument(
        "--fix-ci-failures",
        action="store_true",
        help=(
            "In the fix_prs step, also revise a PR with no maintainer "
            "feedback to address its own failing CI checks (default: off, "
            "since this lets the model touch files outside the original "
            "finding's, gated by the same compile/style validation and "
            "--publish-fix-prs approval as any other revision)."
        ),
    )
    parser.add_argument(
        "--publish-issues",
        action="store_true",
        help="Publish one GitHub issue per generated PR metadata file.",
    )
    parser.add_argument(
        "--issues-dir",
        default="outputs/issues",
        help="Directory where issue publication records are written.",
    )
    parser.add_argument(
        "--no-retrospective",
        action="store_true",
        help=(
            "Do not run the end-of-run retrospective, which records where each "
            "finding stopped and verified facts for later runs."
        ),
    )
    parser.add_argument(
        "--online-dependency-resolution",
        action="store_true",
        help=(
            "When the offline Gradle dependency tree is unavailable (a project "
            "never built on this machine), resolve it once online during "
            "extraction. This identifies which library ships a reported "
            "class and downloads the jars its bytecode is read from."
        ),
    )
    parser.add_argument(
        "--no-statistics",
        action="store_true",
        help=(
            "Do not write the end-of-run statistics (funnel, decisions by "
            "label, stability against the previous run on the same findings, "
            "build causes) to <run>/statistics."
        ),
    )
    parser.add_argument(
        "--statistics-annotations",
        default="",
        help=(
            "CSV of human verdicts (apk, class, method, spec, verdict) to "
            "compare the pipeline's decisions with, when the file exists."
        ),
    )
    parser.add_argument(
        "--learning-dir",
        default=None,
        help=(
            "Directory holding the failure ledger and verified knowledge across "
            "runs; the run reads its findings' earlier attempts and verified "
            "imports from it and records its own there (default: "
            "TCC_LEARNING_DIR, else data/pipeline_learning)."
        ),
    )
    parser.add_argument(
        "--force-revalidation",
        action="store_true",
        help=(
            "Always run validation_fix/revalidation even when validation "
            "already succeeded for every solution (default: skip them and "
            "go straight to the next step, since there is nothing to fix "
            "or reconfirm)."
        ),
    )
    parser.add_argument(
        "--force-downstream-steps",
        action="store_true",
        help=(
            "Always run validation/validation_fix/revalidation/prs/publish/"
            "fix_prs/revalidation_fix_prs/issues even when the solutions "
            "step applied nothing and the llm step produced no issue "
            "candidates (default: skip straight to preview/issues_cleanup, "
            "since there is nothing for those steps to act on)."
        ),
    )
    parser.add_argument(
        "--reprocess-existing",
        action="store_true",
        help=(
            "Reprocess publish/issues records already marked as created "
            "instead of skipping them (forwarded to pr_creator.py and "
            "issue_creator.py, which already support this)."
        ),
    )
    parser.add_argument(
        "--pr-issue-style",
        choices=("v1", "v2", "v3"),
        default="v3",
        help=(
            "v3 (default) is v2 with the pull request and issue bodies as plain "
            "paragraphs: no headings or checkboxes, a passing build said in one "
            "sentence and a failed one not at all. "
            "v2 writes the pull request's title and summary in a "
            "maintainer's words under the headed body; v1 keeps the earlier deterministic text, "
            "which carried the pipeline's vocabulary and 'Fix <method>' "
            "titles into published pull requests."
        ),
    )
    parser.add_argument("--issue-title", default="Potential security issue found — would you be open to a pull request?")
    parser.add_argument(
        "--issue-title-without-security-impact",
        default="Question about the {api} use in {location} — would you be open to a pull request?",
        help=("Title of a pull-request draft's contact-first issue when its record has no "
              "confirmed security impact (impact_class security_impact); {api} and "
              "{location} are filled in. --issue-title is used otherwise."),
    )
    parser.add_argument("--issue-body", default="Hi! I'm a Computer Engineering student at the University of Brasília (UnB), working on a research project about how Android apps use security-sensitive APIs.")
    parser.add_argument(
        "--generate-previews",
        action="store_true",
        help="Generate Markdown previews during a complete pipeline run.",
    )
    parser.add_argument(
        "--previews-dir",
        default="outputs/previews",
        help="Directory for per-PR and per-issue Markdown previews.",
    )


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    """Which steps and projects run, cloning, and the frozen-experiment inputs."""
    parser.add_argument(
        "--start-from",
        choices=PIPELINE_STEPS,
        default=None,
        help="Start the pipeline from this step.",
    )
    parser.add_argument(
        "--only",
        choices=PIPELINE_STEPS,
        help="Run only one step.",
    )
    parser.add_argument(
        "--skip",
        action="append",
        choices=PIPELINE_STEPS,
        default=[],
        help="Skip a step. Can be used more than once.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit projects/solutions/PRs where supported. 0 means no limit.",
    )
    parser.add_argument(
        "--project",
        default="",
        help="Process only this exact APK/project name in every selected step.",
    )
    parser.add_argument(
        "--skip-cloning",
        action="store_true",
        help="Reuse repositories already present in --cloned-projects-dir.",
    )
    parser.add_argument(
        "--clone-timeout",
        type=int,
        default=1800,
        # 300 could not clone the two largest repositories in the corpus. The
        # cost is almost entirely writing the working tree across the WSL 9p
        # bridge to /mnt/c, and the measured rate is not stable:
        # element-android checks out at ~37 files/s and Signal-Android at ~11,
        # so 10k files is anywhere from 5 to 16 minutes. This is sized for the
        # slow end with margin, and a timeout that made progress is now
        # resumed rather than restarted, so the ceiling is this value times
        # --clone-retries.
        help=(
            "Timeout per repository clone attempt, in seconds. An attempt "
            "that times out with its objects already fetched is resumed by "
            "the next one instead of starting over."
        ),
    )
    parser.add_argument(
        "--clone-retries",
        type=int,
        default=3,
        help="Number of attempts per repository clone.",
    )
    parser.add_argument(
        "--experiment-manifest",
        type=Path,
        help=(
            "Run as a frozen experiment recorded in this file. Written on "
            "the first run and verified on every later one; a divergence "
            "stops the run. While it is set, specifications are not "
            "generated, web research not already archived is not fetched, "
            "and the learning store is not written."
        ),
    )
    parser.add_argument(
        "--experiment-live-research",
        action="store_true",
        help=(
            "With --experiment-manifest: research what is not archived live "
            "instead of leaving it empty (the case part too), and, with "
            "--llm-research-corpus-dir, store complete shared answers there. "
            "Specifications and the learning store stay frozen; the manifest "
            "declares the live research and records the research corpus the run "
            "started from: only this run, resumed, matches it again, and only "
            "while every recorded entry is still there unchanged."
        ),
    )
    parser.add_argument(
        "--experiment-condition",
        default="G",
        help="Name of the condition being run (B0, B1, G, an ablation).",
    )


def _add_build_arguments(parser: argparse.ArgumentParser) -> None:
    """The Android build the optional build and validation steps run."""
    parser.add_argument(
        "--build-variant",
        default="debug",
        choices=("debug", "release"),
        help="Android build variant used by the build step.",
    )
    parser.add_argument(
        "--build-timeout",
        type=int,
        default=1800,
        help="Timeout per Android project build, in seconds.",
    )
    parser.add_argument(
        "--build-no-proguard",
        action="store_true",
        help="Run the optional APK build step with ProGuard/R8 disabled.",
    )
    parser.add_argument(
        "--validate-build",
        action="store_true",
        help="Compile projects after applying solutions and report it in PRs.",
    )


# Production defaults (PLANO_CORRECOES_DEFESA A9): a command line that names
# its provider runs as the queue runs, and Ollama stays one --llm-provider
# away. The provider itself has no default (A9 adjustment, 2026-11-12).
# Each provider's model and endpoint, used where the command line names none.
PROVIDER_DEFAULTS = {
    "openai": {"llm_model": "gpt-6.1-terra", "llm_endpoint": "https://api.openai.com/v1"},
    "anthropic": {"llm_model": "claude-opus-5-5", "llm_endpoint": "https://api.anthropic.com"},
    "ollama": {"llm_model": "codellama:7b", "llm_endpoint": "http://localhost:11434"},
}
DEFAULT_MAX_COST_USD = 10.0


def apply_provider_defaults(args: argparse.Namespace) -> None:
    """Fill --llm-model and --llm-endpoint from --llm-provider when not given."""
    for name, value in PROVIDER_DEFAULTS[args.llm_provider].items():
        if getattr(args, name, None) is None:
            setattr(args, name, value)


def _add_llm_arguments(parser: argparse.ArgumentParser) -> None:
    """The model provider and every limit of the LLM steps."""
    parser.add_argument(
        "--llm-provider", choices=("ollama", "openai", "anthropic"), default=None,
        help="Provider used by the LLM step; required (llm-provider in the run's "
        "TOML), so a command line that names none spends nothing. --llm-model and "
        "--llm-endpoint default to that provider's (PROVIDER_DEFAULTS).",
    )
    parser.add_argument(
        "--spec-model",
        default="gpt-6.1-terra",
        help="Model (of --llm-provider) used to generate missing specification JSONs.",
    )
    parser.add_argument(
        "--overwrite-specifications",
        action="store_true",
        help="Regenerate specification JSONs that already exist.",
    )
    parser.add_argument(
        "--disable-llm-web-search",
        action="store_true",
        help="Disable OpenAI web search during evidence analysis and fix generation.",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Model used by the LLM step. Default: the provider's (openai "
        "gpt-6.1-terra, anthropic claude-opus-5-5, ollama codellama:7b).",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=DEFAULT_MAX_COST_USD,
        help="Stop making model calls once the run has cost this many US dollars "
        f"(prices from data/llm_prices.json; default {DEFAULT_MAX_COST_USD:g}, the "
        "queue's ceiling); 0 turns the ceiling off.",
    )
    parser.add_argument(
        "--llm-service-tier",
        choices=["auto", "default", "flex"],
        default="flex",
        help="OpenAI processing tier the model calls ask for. flex (the default) "
        "bills tokens at half price; a call Flex answers with 429 or leaves "
        "queued for 900 s goes again on the standard tier. auto sends no tier. "
        "The model reads the same either way. priority is not offered: "
        "data/llm_prices.json has no price for it, so --max-cost would undercount it.",
    )
    parser.add_argument(
        "--holdout-repository",
        action="store_true",
        help="Analyse each finding without what its own repository taught earlier "
        "runs: no maintainer answers from that repository (other repositories' "
        "answers stay) and none of its verified imports from the learning store. "
        "For rerunning the findings of published pull requests without the "
        "maintainers' verdict. Recorded in a frozen experiment's manifest.",
    )
    parser.add_argument(
        "--llm-research-reasoning-effort",
        choices=["none", "minimal", "low", "medium", "high", "xhigh"],
        default=None,
        help="Reasoning effort for the web-research calls only; the other LLM "
        "stages keep --llm-reasoning-effort. Default: the same effort.",
    )
    parser.add_argument(
        "--llm-endpoint",
        default=None,
        help="Endpoint of the LLM step's provider. Default: the provider's "
        "(https://api.openai.com/v1, https://api.anthropic.com, "
        "http://localhost:11434).",
    )
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=300,
        help="Timeout per Ollama request, in seconds.",
    )
    parser.add_argument(
        "--ollama-context-length", "--llm-context-length",
        dest="ollama_context_length",
        type=int,
        default=16384,
        help=(
            "Ollama physical context window in tokens. The old "
            "--llm-context-length name remains as a compatibility alias."
        ),
    )
    parser.add_argument(
        "--repository-context-budget",
        type=int,
        default=140000,
        help="Logical character budget for deterministic repository evidence.",
    )
    parser.add_argument(
        "--llm-max-output-tokens",
        type=int,
        default=32768,
        help="Output-token ceiling of each model call (Ollama num_predict; "
        "default 32768, the production runs'; -1 sets no cap).",
    )
    parser.add_argument(
        "--no-llm-resume",
        action="store_true",
        help="Reprocess all method JSON files in the LLM step.",
    )
    parser.add_argument(
        "--llm-cache-dir",
        help=(
            "Persistent LLM response cache directory, relative to the "
            "repository root (default: outputs/.llm_cache). It is deliberately "
            "outside the per-run output directory so responses survive across "
            "runs; use --no-llm-cache to disable reuse entirely."
        ),
    )
    parser.add_argument(
        "--openai-prompt-log-dir",
        help=(
            "Directory for JSON logs of every prepared OpenAI prompt. Defaults "
            "to APPLICATION_RESULTS_DIR/openai_prompt_logs for this pipeline run."
        ),
    )
    parser.add_argument(
        "--no-llm-cache",
        action="store_true",
        help="Disable the per-request LLM cache.",
    )
    parser.add_argument(
        "--llm-cache-bypass-finding",
        action="append",
        default=[],
        metavar="PATTERN",
        help=(
            "Ignore cached LLM responses for findings whose id, method or "
            "APK contains PATTERN (repeatable). Their fresh responses are "
            "still written to the cache; every other finding keeps using it."
        ),
    )
    parser.add_argument(
        "--llm-web-search-cache-ttl-hours",
        type=float,
        default=168.0,
        help=(
            "How long a cached web-research answer stays usable (default: "
            "168, one week). Research is the analysis step's most expensive "
            "stage and repeats verbatim across runs of the same finding; "
            "0 refetches every time."
        ),
    )
    parser.add_argument(
        "--llm-research-corpus-dir",
        help=(
            "Research corpus directory, relative to the repository root: answers "
            "to shared research requests kept across runs and served to an "
            "identical request (default: none). Not a cache: --no-llm-cache and "
            "--llm-web-search-cache-ttl-hours do not affect it."
        ),
    )
    parser.add_argument(
        "--llm-research-corpus-max-age-days",
        type=float,
        default=90.0,
        help="Days a research corpus entry stays usable (default: 90; 0 never expires).",
    )
    parser.add_argument(
        "--llm-retries",
        type=int,
        default=2,
        help=(
            "HTTP retries for an ordinary LLM call (default: 2). Each retry "
            "re-runs a full generation, so the budget is deliberately small."
        ),
    )
    parser.add_argument(
        "--llm-web-search-retries",
        type=int,
        default=1,
        help=(
            "HTTP retries for a web-research call (default: 1). Each retry "
            "re-runs the entire research pass, so this budget is much "
            "smaller than the one used for ordinary calls."
        ),
    )
    parser.add_argument(
        "--llm-workers",
        type=int,
        default=1,
        help=(
            "Findings analysed concurrently in the LLM step (default: 1, "
            "sequential). Findings are independent analyses that spend "
            "nearly all their time waiting on the provider."
        ),
    )
    parser.add_argument(
        "--llm-reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh"),
        default="high",
    )
    parser.add_argument(
        "--llm-validation-mode",
        choices=("strict", "balanced", "relaxed"),
        default="balanced",
    )
    parser.add_argument(
        "--allow-cache-invalidation",
        action="store_true",
        help="Permit regeneration of confirmed disposable cache entries.",
    )
    parser.add_argument(
        "--migration-policies-file",
        default="data/migration_policies.json",
        help="Case-specific migration policies keyed by unique_error_id.",
    )


def _add_execution_arguments(parser: argparse.ArgumentParser) -> None:
    """Dry runs, step timeouts, the preflight and what a failing step does."""
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run supported steps in dry-run mode.",
    )
    parser.add_argument(
        "--approve-review-only-for-validation",
        action="store_true",
        help=(
            "Apply review-only solutions only in finding worktrees for "
            "validation; never authorizes publication."
        ),
    )
    parser.add_argument(
        "--step-timeout-minutes",
        type=float,
        default=DEFAULT_STEP_TIMEOUT_MINUTES,
        help=(
            "Deadline for each pipeline step, after which the step and every "
            "process it started are ended and the step counts as failed "
            f"(exit code {STEP_TIMEOUT_EXIT_CODE}). Guards against a step that "
            "stops making progress without any of its own timeouts firing. "
            f"0 disables it. Default: {DEFAULT_STEP_TIMEOUT_MINUTES:g}."
        ),
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help=(
            "Skip the pre-run checks (declared dependencies importable, "
            "pyflakes over src and scripts). They take seconds and guard a "
            "run that takes hours; skip them only knowingly."
        ),
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue with the next step even if one step fails.",
    )
    parser.add_argument(
        "--ignore-step-errors",
        action="store_true",
        help=(
            "Continue after failed steps and finish with exit code 0. "
            "Failures are still reported in the final summary."
        ),
    )


def _add_maintenance_arguments(parser: argparse.ArgumentParser) -> None:
    """Local cleanup and repository pruning, which run instead of the pipeline."""
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Only preview cleanup of PRs, fork branches, and local clones.",
    )
    parser.add_argument(
        "--prune-unused-repos", choices=("off", "report", "preview", "apply"), default="off",
        help="Only export Markdown for clones with merged/closed/no associated PRs; never delete. preview/apply are report aliases.",
    )
    parser.add_argument(
        "--prune-outputs-root", default="outputs",
        help="Root containing all runs to inspect for the repository report.",
    )
    parser.add_argument("--prune-estimate-size", action="store_true",
                        help="Estimate disk space in the report; potentially slow on WSL /mnt/c.")
    parser.add_argument(
        "--confirm-cleanup",
        action="store_true",
        help="With --cleanup, close the PRs, delete the branches and reset the "
             "clones of the findings named by --approve-cleanup.",
    )
    parser.add_argument(
        "--approve-cleanup",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help="With --confirm-cleanup or --confirm-issue-cleanup: the finding whose "
             "pull request or issue is closed (repeatable); every other one is "
             "only previewed.",
    )
    parser.add_argument(
        "--cleanup-issues",
        action="store_true",
        help="Only preview closing issues previously created by the pipeline.",
    )
    parser.add_argument(
        "--confirm-issue-cleanup",
        action="store_true",
        help="With --cleanup-issues, close the GitHub issues of the findings "
             "named by --approve-cleanup.",
    )


# --run-dir layout: option dest -> directory inside the run directory. The
# names are the ones runs have always used (the run directory is the parent
# of error_methods, which is where statistics and findings.jsonl go).
RUN_DIR_LAYOUT = {
    "extracted_methods_dir": "error_methods",
    "error_solutions_dir": "error_solutions",
    "application_results_dir": "application_results",
    "pull_requests_dir": "pull_requests",
    "issues_dir": "issues",
    "previews_dir": "previews",
}


def apply_run_dir(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Point every per-run directory left at its default inside --run-dir."""
    if not getattr(args, "run_dir", ""):
        return
    run_dir = Path(args.run_dir)
    for dest, name in RUN_DIR_LAYOUT.items():
        if getattr(args, dest) == parser.get_default(dest):
            setattr(args, dest, str(run_dir / name))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the complete extraction/build/LLM/solutions/PR pipeline."
    )
    _add_input_arguments(parser)
    _add_publication_arguments(parser)
    _add_selection_arguments(parser)
    _add_build_arguments(parser)
    _add_llm_arguments(parser)
    _add_execution_arguments(parser)
    _add_maintenance_arguments(parser)
    args = parser.parse_args(argv)
    if args.llm_provider is None:
        # A9 adjustment (the user, 2026-11-12): nothing runs, clones or calls a
        # model until the provider is named; once it is, the production
        # defaults below apply.
        parser.error("set llm-provider (openai, anthropic or ollama): --llm-provider "
                     "on the command line or llm-provider in the run's TOML")
    apply_provider_defaults(args)
    apply_run_dir(parser, args)
    if args.prune_unused_repos != "off":
        incompatible = (
            args.cleanup or args.confirm_cleanup or args.cleanup_issues
            or args.confirm_issue_cleanup or args.only or args.publish_prs
            or args.publish_fix_prs or args.publish_issues
            or args.start_from is not None
        )
        if incompatible:
            parser.error("--prune-unused-repos cannot be combined with cleanup, publication, --only or --start-from")
    if args.start_from is None:
        args.start_from = "spec_generation"
    return args


def project_root() -> Path:
    return workspace_root()


def filter_v3_active(args: argparse.Namespace) -> bool:
    return (
        args.enable_filter_v3
        or args.only == "filter_v3"
        or args.start_from == "filter_v3"
    )


def rv_import_active(args: argparse.Namespace) -> bool:
    return (
        getattr(args, "enable_rv_import", False)
        or args.only == "rv_import"
        or args.start_from == "rv_import"
    )


def resolve_paths(args: argparse.Namespace, root: Path) -> PipelinePaths:
    filter_active = filter_v3_active(args)
    input_csv_file = (
        (root / args.filter_v3_input).resolve()
        if filter_active else (root / args.csv_file).resolve()
    )
    effective_csv_file = (
        (root / args.filter_v3_output).resolve()
        if filter_active else input_csv_file
    )
    if rv_import_active(args):
        # The converted package replaces --csv-file for every later step.
        effective_csv_file = (root / args.rv_import_output).resolve()
    return PipelinePaths(
        root=root,
        input_csv_file=input_csv_file,
        csv_file=effective_csv_file,
        cloned_projects_dir=(root / args.cloned_projects_dir).resolve(),
        extracted_methods_dir=(root / args.extracted_methods_dir).resolve(),
        build_output_dir=(root / args.build_output_dir).resolve(),
        error_solutions_dir=(root / args.error_solutions_dir).resolve(),
        application_results_dir=(root / args.application_results_dir).resolve(),
        worktrees_dir=(root / args.worktrees_dir).resolve(),
        pull_requests_dir=(root / args.pull_requests_dir).resolve(),
        issues_dir=(root / args.issues_dir).resolve(),
        previews_dir=(root / args.previews_dir).resolve(),
    )


# Steps that only run when something asks for them. `flag` is the argparse
# attribute that enables the step; `start_from` is the --start-from value that
# also admits it, which is not always the step's own name -- asking to start at
# "validation" admits the whole validation trio. issues_cleanup has no flag and
# is reachable only by starting there.
#
# This was five near-identical filter blocks, and the step vocabulary lived in
# six places in this file with nothing checking they agreed.
OPTIONAL_STEPS = {
    "build": {"flag": "build_no_proguard", "start_from": "build"},
    "publish": {"flag": "publish_prs", "start_from": "publish"},
    "issues": {"flag": "publish_issues", "start_from": "issues"},
    "preview": {"flag": "generate_previews", "start_from": "preview"},
    "validation": {"flag": "validate_build", "start_from": "validation"},
    "validation_fix": {"flag": "validate_build", "start_from": "validation"},
    "revalidation": {"flag": "validate_build", "start_from": "validation"},
    "issues_cleanup": {"flag": None, "start_from": "issues_cleanup"},
}


def _step_enabled(step: str, args: argparse.Namespace) -> bool:
    gate = OPTIONAL_STEPS[step]
    flag = gate["flag"]
    if flag and getattr(args, flag, False):
        return True
    return args.start_from == gate["start_from"]


def selected_steps(args: argparse.Namespace) -> List[str]:
    if getattr(args, "prune_unused_repos", "off") != "off":
        return ["prune_unused_repos"]
    if args.cleanup_issues:
        return ["issues_cleanup"]
    if args.cleanup:
        return ["cleanup"]
    if args.only:
        steps = [args.only]
    else:
        start_index = PIPELINE_STEPS.index(args.start_from)
        steps = list(PIPELINE_STEPS[start_index:])
        steps = [
            step for step in steps
            if step not in OPTIONAL_STEPS or _step_enabled(step, args)
        ]

    # Both input steps run first when asked for, and are dropped otherwise,
    # even from a --start-from that precedes them.
    for step, active in (
        ("filter_v3", filter_v3_active(args)),
        ("rv_import", rv_import_active(args)),
    ):
        if (
            active
            and args.only != step
            and step not in steps
            and not (args.cleanup or args.cleanup_issues)
        ):
            steps.insert(0, step)
        if not active:
            steps = [item for item in steps if item != step]

    skipped = set(args.skip)
    return [step for step in steps if step not in skipped]


def build_environment(root: Path) -> dict:
    """The steps' environment: this process's, with this cognalys importable.

    The steps run `python -m cognalys.<...>`, and must run the same code as
    the orchestrator that started them. That is the directory this package
    was imported from -- src/ in a checkout, site-packages when installed --
    not <workspace>/src: an installed package working on a checkout as its
    workspace would otherwise run the checkout's code in every step. Only
    that directory: a folder inside the package on the path would let a
    module be imported a second time under its bare name.
    """
    import cognalys

    env = os.environ.copy()
    module_paths = [Path(cognalys.__file__).resolve().parents[1]]
    current_pythonpath = env.get("PYTHONPATH")
    pythonpath_entries = [str(path) for path in module_paths]
    if current_pythonpath:
        pythonpath_entries.append(current_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    return env


def append_limit(command: List[str], limit: int) -> None:
    if limit > 0:
        command.extend(["--limit", str(limit)])


def print_model_cost(log_dir: Path) -> None:
    """The run's model calls in tokens and dollars, when it made any."""
    from cognalys.llm.cost import summary_lines, usage

    try:
        report = usage(log_dir)
    except OSError:
        return
    if report["calls"] or report["cache_hits"] or report.get("corpus_hits"):
        print("\nModel calls of this run:", flush=True)
        print("\n".join(summary_lines(report)), flush=True)


def _library_data(paths: PipelinePaths, *parts: str) -> Path:
    """The run root's own data (paths.root/data/...) when it has it, else the library's.

    A research workspace kept apart from the library holds its runs and
    inputs but no copy of the specifications or policies (workspace.library_data).
    """
    own = paths.root.joinpath("data", *parts)
    return own if own.exists() else library_data(*parts)


def openai_prompt_log_dir(args: argparse.Namespace, paths: PipelinePaths) -> Path:
    if args.openai_prompt_log_dir:
        return (paths.root / args.openai_prompt_log_dir).resolve()
    return (paths.application_results_dir / "openai_prompt_logs").resolve()


def step_ending_message(step: str, outcome: dict, timeout_minutes: float,
                        elapsed_seconds: float) -> str:
    """Why run_with_deadline ended a step, in the terms of the limit that hit.

    Amethyst (rvsec_amethyst_20261024/run_v2, 2026-10-29): the stall watch
    ended the llm step twice, 80 minutes in, and the run reported "ended
    after 720 minutes", the step deadline, which it never came near.
    """
    if outcome.get("idle"):
        return (
            f"Step '{step}' was ended after making no progress for "
            f"{int(outcome.get('idle_seconds') or 0)}s "
            f"({elapsed_seconds / 60:.0f} minutes after it started)."
        )
    return f"Step '{step}' was ended after {timeout_minutes:g} minutes without finishing."


def llm_step_watch(step: str, args: argparse.Namespace, paths: PipelinePaths) -> dict:
    """run_with_deadline's stall watch for the llm step, {} for the others.

    The step writes a prompt record for every model call and a log line for
    every stage, so three request timeouts (at least 30 minutes) without
    either means it is stuck rather than waiting on a slow answer.
    """
    if step != "llm":
        return {}
    return {
        "idle_seconds": max(3 * float(getattr(args, "llm_timeout", 600) or 600), 1800.0),
        "activity_paths": [
            openai_prompt_log_dir(args, paths),
            paths.error_solutions_dir,
            paths.error_solutions_dir / "logs",
        ],
    }


def validation_fully_passed(execution_report_path: Path) -> bool:
    """True only if every solution with a build_validation result reports a
    passing status. Used to skip a redundant validation_fix/revalidation
    pass when there is nothing left to fix or reconfirm.

    Passing is BUILD_VALIDATION_PASSING_STATUSES, the set every other stage
    uses. This compared against the literal "success" alone, so a finding
    whose app module compiled while an unrelated module could not
    ("success_app_module_only") made the whole run look failed:
    run_20260913_180317 reported "2 passed, 0 failed" and then ran
    validation_fix and a full revalidation anyway.
    """
    try:
        report = json.loads(execution_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    details = report.get("per_solution_details", [])
    if not isinstance(details, list):
        return False
    checked = False
    for entry in details:
        validation = (
            entry.get("build_validation") if isinstance(entry, dict) else None
        )
        if not isinstance(validation, dict):
            continue
        checked = True
        if validation.get("status") not in BUILD_VALIDATION_PASSING_STATUSES:
            return False
    return checked


def validation_fix_repaired_anything(execution_report_path: Path) -> bool:
    """True when validation_fix committed a repair to at least one worktree.

    Revalidation exists to confirm those repairs. With none applied it
    rebuilds unchanged worktrees and reproduces the failure validation already
    recorded -- minutes per finding for no new information. A missing or
    unreadable report fails safe (True: revalidate).
    """
    try:
        report = json.loads(execution_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    details = report.get("per_solution_details", [])
    if not isinstance(details, list):
        return True
    return any(
        isinstance(entry, dict)
        and isinstance(entry.get("validation_fix"), dict)
        and entry["validation_fix"].get("status") == "success"
        for entry in details
    )


def solutions_report_is_empty(execution_report_path: Path) -> bool:
    """True only when the solutions step's own report confirms zero total
    solutions -- i.e. validation, prs, publish, fix_prs and their
    revalidation/revision counterparts would all have nothing to act on.
    Missing or unparseable reports fail safe (False, meaning: don't skip).
    """
    try:
        report = json.loads(execution_report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return report.get("total_solutions") == 0


def has_issue_candidates(issue_candidates_dir: Path) -> bool:
    """True if the llm step wrote at least one issue-candidate file."""
    if not issue_candidates_dir.is_dir():
        return False
    return next(issue_candidates_dir.rglob("*_issue.json"), None) is not None


def _command_filter_v3(step, args, paths, python):
        return [
            python,
            "-m", "cognalys.corpus.filtro_v3",
            str(paths.input_csv_file),
            "--saida",
            str(paths.csv_file),
        ]


def _command_rv_import(step, args, paths, python):
        return [
            python,
            "-m", "cognalys.corpus.import_rv_misuses",
            str((paths.root / args.rv_import_dir).resolve()),
            "--output",
            str(paths.csv_file),
        ]


def _command_spec_generation(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.specifications.generate_specifications",
            "--csv-file",
            str(paths.csv_file),
            "--specifications-dir",
            str(_library_data(paths, "specifications")),
            "--model",
            args.spec_model,
            "--endpoint",
            args.llm_endpoint,
            "--timeout",
            str(args.llm_timeout),
            "--max-output-tokens",
            str(args.llm_max_output_tokens),
            "--reasoning-effort",
            args.llm_reasoning_effort,
            "--prompt-log-dir",
            str(openai_prompt_log_dir(args, paths)),
        ]
        if args.overwrite_specifications:
            command.append("--overwrite")
        if args.project:
            command.extend(["--project", args.project])
        return command


def _command_prune_unused_repos(step, args, paths, python):
    command = [
        python, "-m", "cognalys.maintenance.prune_unused_repositories",
        "--mode", args.prune_unused_repos,
        "--cloned-projects-dir", str(paths.cloned_projects_dir),
        "--worktrees-dir", str(paths.worktrees_dir),
        "--outputs-root", str(paths.root / args.prune_outputs_root),
    ]
    if args.project:
        command += ["--project", args.project]
    if args.dry_run:
        command.append("--dry-run")
    if getattr(args, "prune_estimate_size", False):
        command.append("--estimate-size")
    return command


def _command_cleanup(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.maintenance.cleanup_pipeline",
            "--pull-requests-dir",
            str(paths.pull_requests_dir),
        ]
        append_limit(command, args.limit)
        if args.confirm_cleanup:
            command.append("--confirm-cleanup")
            for finding_id in args.approve_cleanup:
                command.extend(["--approve-cleanup", finding_id])
        if args.project:
            command.extend(["--project", args.project])
        return command


def _command_cloning(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.extraction.extract_methods_single_csv",
            "--csv-file",
            str(paths.csv_file),
            "--output-dir",
            str(paths.cloned_projects_dir),
            "--methods-output-dir",
            str(paths.extracted_methods_dir),
            "--clone-timeout",
            str(args.clone_timeout),
            "--clone-retries",
            str(args.clone_retries),
        ]
        if step == "cloning":
            command.append("--clone-only")
        else:
            command.append("--skip-cloning")
        if step == "cloning" and args.skip_cloning:
            command.append("--skip-cloning")
        if args.project:
            command.extend(["--project", args.project])
        return command


def csv_project_names(csv_file: Path) -> list[str]:
    """The clone names a findings CSV selects, spelled as extraction spells them.

    Empty when the CSV is missing or any row names no APK: scoping a step to a
    partial list would silently skip the unnamed projects, so an unreadable
    CSV leaves the step unscoped rather than wrong.
    """
    if not csv_file.is_file():
        return []
    names: list[str] = []
    for row in read_rows(csv_file):
        apk = str(row.get("apk") or "").strip()
        apk_url = str(row.get("apk_url") or "").strip()
        name = apk or (apk_url.rsplit("/", 1)[-1].strip() if apk_url else "")
        if not name:
            return []
        if name not in names:
            names.append(name)
    return names


def scoped_projects(args, paths) -> list[str]:
    """--project when given, otherwise every project the CSV names.

    For the steps that walk the whole clone directory. The others already
    read run-scoped inputs, so a run without --project reached only its own
    findings there -- but repository_setup and build iterated every clone ever
    made in outputs/cloned_projects.
    """
    return [args.project] if args.project else csv_project_names(paths.csv_file)


def _command_repository_setup(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.extraction.prepare_repository_branches",
            "--projects-dir",
            str(paths.cloned_projects_dir),
            "--report",
            str(paths.application_results_dir / "repository_setup.json"),
        ]
        for project in scoped_projects(args, paths):
            command.extend(["--project", project])
        return command


def _command_build(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.builds.apk_build",
            "--projects-dir",
            str(paths.cloned_projects_dir),
            "--output-dir",
            str(paths.build_output_dir),
            "--variant",
            args.build_variant,
            "--timeout",
            str(args.build_timeout),
            "--disable-minification",
        ]
        append_limit(command, args.limit)
        for project in scoped_projects(args, paths):
            command.extend(["--project", project])
        if args.dry_run:
            command.append("--dry-run")
        return command


def _command_llm(step, args, paths, python):
        llm_input_dir = paths.extracted_methods_dir
        if args.project:
            llm_input_dir = llm_input_dir / args.project
        command = [
            python,
            "-m", "cognalys.llm.generate_error_fixes",
            "--input-dir",
            str(llm_input_dir),
            "--output-dir",
            str(paths.error_solutions_dir),
            "--model",
            args.llm_model,
            "--provider",
            args.llm_provider,
            "--endpoint",
            args.llm_endpoint,
            "--timeout",
            str(args.llm_timeout),
            "--ollama-context-length",
            str(args.ollama_context_length),
            "--repository-context-budget",
            str(args.repository_context_budget),
            "--pr-issue-style",
            args.pr_issue_style,
            "--max-output-tokens",
            str(args.llm_max_output_tokens),
            "--reasoning-effort",
            args.llm_reasoning_effort,
            "--validation-mode",
            args.llm_validation_mode,
            "--validation-feedback-dir",
            str(paths.application_results_dir / "build_validation"),
            "--prompt-log-dir",
            str(openai_prompt_log_dir(args, paths)),
            "--web-search-cache-ttl-hours",
            str(args.llm_web_search_cache_ttl_hours),
            "--web-search-retries",
            str(args.llm_web_search_retries),
            "--retries",
            str(args.llm_retries),
            "--workers",
            str(args.llm_workers),
        ]
        if args.no_llm_resume:
            command.append("--no-resume")
        if args.no_llm_cache:
            command.append("--no-cache")
        else:
            # The LLM step's own default is OUTPUT_DIR/.llm_cache, and
            # OUTPUT_DIR is the per-run error_solutions directory, so that
            # default produced a brand-new empty cache on every run and could
            # never hit. Anchor the cache outside the run directory so repeated
            # findings reuse the paid response instead of buying it again.
            command.extend(["--cache-dir", str(
                paths.root / (args.llm_cache_dir or "outputs/.llm_cache")
            )])
        if getattr(args, "llm_research_corpus_dir", None):
            command.extend([
                "--research-corpus-dir", str(paths.root / args.llm_research_corpus_dir),
                "--research-corpus-max-age-days", str(args.llm_research_corpus_max_age_days),
            ])
        if args.allow_cache_invalidation:
            command.append("--allow-cache-invalidation")
        if not args.disable_llm_web_search:
            command.append("--web-search")
        migration_policies_file = paths.root / args.migration_policies_file
        if (not migration_policies_file.exists()
                and args.migration_policies_file == "data/migration_policies.json"):
            migration_policies_file = _library_data(paths, "migration_policies.json")
        if migration_policies_file.exists():
            command.extend([
                "--migration-policies-file", str(migration_policies_file)
            ])
        if args.dry_run:
            command.append("--dry-run")
        return command


def _command_spec_context(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.specifications.enrich_spec_context",
            "--input-dir",
            str(paths.extracted_methods_dir),
            "--specifications-dir",
            str(_library_data(paths, "specifications")),
            "--required-specs-csv",
            str(paths.csv_file),
        ]
        if args.project:
            command.extend(["--project", args.project])
        return command


def _command_detector_rules(step, args, paths, python):
        # Rules are proposed for the specifications this run's CSV names and
        # checked against every run's extracted findings; a run only reads
        # them afterwards. See src/validation/rule_proposal.py.
        return [
            python,
            "-m", "cognalys.validation.propose_detector_rules",
            "--required-specs-csv", str(paths.csv_file),
            "--specifications-dir", str(_library_data(paths, "specifications")),
            "--model", args.llm_model,
            "--endpoint", args.llm_endpoint,
            "--timeout", str(args.llm_timeout),
            "--max-output-tokens", str(args.llm_max_output_tokens),
            "--reasoning-effort", args.llm_reasoning_effort,
            "--prompt-log-dir", str(openai_prompt_log_dir(args, paths)),
        ]


def _command_solutions(step, args, paths, python):
        solutions_input_dir = paths.error_solutions_dir
        if args.project:
            solutions_input_dir = solutions_input_dir / args.project
        command = [
            python,
            "-m", "cognalys.solutions.run_apply",
            "--error-solutions-dir",
            str(solutions_input_dir),
            "--cloned-projects-dir",
            str(paths.cloned_projects_dir),
            "--output-dir",
            str(paths.application_results_dir),
            "--worktrees-dir",
            str(paths.worktrees_dir),
            "--backup-dir",
            str(paths.application_results_dir / "method_backups"),
        ]
        append_limit(command, args.limit)
        if args.dry_run:
            command.append("--dry-run")
        if args.approve_review_only_for_validation:
            command.append("--approve-review-only-for-validation")
        if args.llm_provider in ("openai", "anthropic") and not args.dry_run:
            # A patch whose code changed on the base branch is ported onto it
            # rather than given up on (solutions.base_port).
            command.extend([
                "--port-model", args.llm_model,
                "--port-endpoint", args.llm_endpoint,
                "--port-timeout", str(args.llm_timeout),
                "--port-max-output-tokens",
                str(min(args.llm_max_output_tokens, 16384)),
                "--port-reasoning-effort", args.llm_reasoning_effort,
                "--port-prompt-log-dir", str(openai_prompt_log_dir(args, paths)),
            ])
        return command


def _command_prs(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.prs.run_prs",
            "--execution-report",
            str(paths.application_results_dir / "execution_report.json"),
            "--output-dir",
            str(paths.pull_requests_dir),
            "--pr-issue-style",
            args.pr_issue_style,
        ]
        # A text that fails its checks is rewritten, not only held back.
        if getattr(args, "llm_model", "") and not args.dry_run:
            command.extend([
                "--model", args.llm_model,
                "--endpoint", args.llm_endpoint,
                "--timeout", str(args.llm_timeout),
                "--max-output-tokens", str(min(args.llm_max_output_tokens, 16384)),
                "--reasoning-effort", args.llm_reasoning_effort,
                "--cache-dir", str(paths.error_solutions_dir.parent / ".llm_cache"),
                # Traced with the run's other calls (cognalys cost, --max-cost).
                "--prompt-log-dir", str(openai_prompt_log_dir(args, paths)),
            ])
        append_limit(command, args.limit)
        if args.project:
            command.extend(["--project", args.project])
        for finding_id in args.publish_without_build_validation:
            command.extend(
                ["--publish-without-build-validation", finding_id]
            )
        if args.dry_run:
            command.append("--dry-run")
        return command


def _command_validation(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.validation.applied_solutions",
            "--projects-dir",
            str(paths.cloned_projects_dir),
            "--output-dir",
            str(paths.application_results_dir / "build_validation"),
            "--execution-report",
            str(paths.application_results_dir / "execution_report.json"),
            "--variant",
            args.build_variant,
            "--timeout",
            str(args.build_timeout),
        ]
        append_limit(command, args.limit)
        if args.project:
            command.extend(["--project", args.project])
        if getattr(args, "gradle_build_cache", False):
            command.append("--gradle-build-cache")
        # Without this the run stops here on the infrastructure failure the
        # operator already knows about and approved, and the prs/publish
        # steps the approval exists for are never reached.
        for finding_id in args.publish_without_build_validation:
            command.extend(
                ["--publish-without-build-validation", finding_id]
            )
        if args.dry_run:
            command.append("--dry-run")
        if step == "revalidation":
            # No --fail-on-validation-error: one finding whose patch still
            # does not build must not stop the run. run_20261006_223750 ended
            # here on myexpenses' build timeout, and the prs step never ran
            # for feeder and networksurvey, whose patches had built. Each
            # finding's build status is what blocks its own publication
            # (pr_generator.build_validation_blocks_publication).
            if not getattr(args, "full_revalidation", False):
                command.extend([
                    "--revalidate-repaired-in",
                    str(paths.application_results_dir / "validation_fixes"),
                ])
        return command


def _command_validation_fix(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.validation.repair",
            "--execution-report",
            str(paths.application_results_dir / "execution_report.json"),
            "--output-dir",
            str(paths.application_results_dir / "validation_fixes"),
            "--model",
            args.llm_model,
            "--endpoint",
            args.llm_endpoint,
            "--timeout",
            str(args.llm_timeout),
            "--max-output-tokens",
            str(min(args.llm_max_output_tokens, 16384)),
            "--reasoning-effort",
            args.llm_reasoning_effort,
            "--prompt-log-dir",
            str(openai_prompt_log_dir(args, paths)),
        ]
        append_limit(command, args.limit)
        if args.project:
            command.extend(["--project", args.project])
        if args.dry_run:
            command.append("--dry-run")
        return command


def _command_publish(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.prs.pr_creator",
            "--pull-requests-dir",
            str(paths.pull_requests_dir),
        ]
        append_limit(command, args.limit)
        if args.project:
            command.extend(["--project", args.project])
        if args.allow_duplicate_pr:
            command.append("--allow-duplicate-pr")
        if args.allow_stale_review:
            command.append("--allow-stale-review")
        for finding_id in args.approve_reviewed_finding:
            command.extend(["--approve-reviewed-finding", finding_id])
        for finding_id in args.publish_without_build_validation:
            command.extend(
                ["--publish-without-build-validation", finding_id]
            )
        if args.dry_run:
            command.append("--dry-run")
        if args.reprocess_existing:
            command.append("--reprocess-existing")
        return command


def _command_fix_prs(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.prs.pr_feedback",
            "--pull-requests-dir",
            str(paths.pull_requests_dir),
            "--projects-dir",
            str(paths.cloned_projects_dir),
            "--worktrees-dir",
            str(paths.worktrees_dir),
            "--output",
            str(paths.application_results_dir / "fix_prs_report.json"),
            "--model",
            args.llm_model,
            "--endpoint",
            args.llm_endpoint,
            "--timeout",
            str(args.llm_timeout),
            "--max-output-tokens",
            str(args.llm_max_output_tokens),
            "--reasoning-effort",
            args.llm_reasoning_effort,
            "--prompt-log-dir",
            str(openai_prompt_log_dir(args, paths)),
        ]
        if args.project:
            command.extend(["--project", args.project])
        if args.skip_fix_prs_pr_state_check:
            command.append("--skip-pr-state-check")
        if args.self_audit_prs:
            command.append("--self-audit")
        if args.fix_ci_failures:
            command.append("--fix-ci-failures")
        if args.dry_run:
            command.append("--dry-run")
        return command


def _command_revalidation_fix_prs(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.prs.pr_feedback",
            "--revalidate",
            "--pull-requests-dir",
            str(paths.pull_requests_dir),
            "--projects-dir",
            str(paths.cloned_projects_dir),
            "--worktrees-dir",
            str(paths.worktrees_dir),
            "--output",
            str(paths.application_results_dir / "fix_prs_report.json"),
            "--timeout",
            str(args.llm_timeout),
            "--build-variant",
            args.build_variant,
            "--build-timeout",
            str(args.build_timeout),
        ]
        if args.project:
            command.extend(["--project", args.project])
        if args.skip_fix_prs_compile_validation:
            command.append("--skip-compile-validation")
        if args.dry_run:
            command.append("--dry-run")
        if args.publish_fix_prs:
            command.append("--publish")
            for finding_id in args.approve_revision:
                command.extend(["--approve-revision", finding_id])
        return command


def _command_preview(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.prs.generate_github_previews",
            "--pull-requests-dir",
            str(paths.pull_requests_dir),
            "--issue-candidates-dir",
            str(paths.error_solutions_dir / "issue_candidates"),
            "--evidence-reviews-dir",
            str(paths.error_solutions_dir / "evidence_reviews"),
            "--output-dir",
            str(paths.previews_dir),
            "--issue-title",
            args.issue_title,
            "--issue-title-without-security-impact",
            _title_without_security_impact(args),
            "--issue-body",
            args.issue_body,
            "--pr-issue-style",
            args.pr_issue_style,
        ]
        append_limit(command, args.limit)
        if args.project:
            command.extend(["--project", args.project])
        return command


def _title_without_security_impact(args) -> str:
    from cognalys.issues.issue_text import DEFAULT_TITLE_WITHOUT_SECURITY_IMPACT

    return (getattr(args, "issue_title_without_security_impact", None)
            or DEFAULT_TITLE_WITHOUT_SECURITY_IMPACT)


def _command_issues(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.issues.issue_creator",
            "--pull-requests-dir",
            str(paths.pull_requests_dir),
            "--issue-candidates-dir",
            str(paths.error_solutions_dir / "issue_candidates"),
            "--issues-dir",
            str(paths.issues_dir),
            "--title",
            args.issue_title,
            "--issue-title-without-security-impact",
            _title_without_security_impact(args),
            "--body",
            args.issue_body,
            "--pr-issue-style",
            args.pr_issue_style,
        ]
        # A v3 issue text that fails its checks is rewritten, not only held
        # back (issues.issue_text), as the prs step does for a pull request.
        if (args.pr_issue_style == "v3" and args.llm_provider in ("openai", "anthropic")
                and not args.dry_run):
            command.extend([
                "--model", args.llm_model,
                "--endpoint", args.llm_endpoint,
                "--timeout", str(args.llm_timeout),
                "--max-output-tokens", str(min(args.llm_max_output_tokens, 16384)),
                "--reasoning-effort", args.llm_reasoning_effort,
                "--cache-dir", str(paths.error_solutions_dir.parent / ".llm_cache"),
                "--prompt-log-dir", str(openai_prompt_log_dir(args, paths)),
            ])
        append_limit(command, args.limit)
        if args.project:
            command.extend(["--project", args.project])
        for finding_id in args.approve_reviewed_finding:
            command.extend(["--approve-reviewed-finding", finding_id])
        if args.dry_run:
            command.append("--dry-run")
        if args.reprocess_existing:
            command.append("--reprocess-existing")
        return command


def _command_issues_cleanup(step, args, paths, python):
        command = [
            python,
            "-m", "cognalys.issues.issue_cleanup",
            "--issues-dir",
            str(paths.issues_dir),
        ]
        append_limit(command, args.limit)
        if args.project:
            command.extend(["--project", args.project])
        if args.confirm_issue_cleanup:
            command.append("--confirm-issue-cleanup")
            for finding_id in args.approve_cleanup:
                command.extend(["--approve-issue-cleanup", finding_id])
        return command

# Every pipeline step's command line, by name. The chain of `if step == ...`
# this replaces ran to 452 lines, and the step vocabulary was spread over six
# places in this file with nothing checking they agreed -- adding or renaming
# a step meant finding all six. A step's command now has exactly one home.
STEP_COMMAND_BUILDERS = {
    "prune_unused_repos": _command_prune_unused_repos,
    "rv_import": _command_rv_import,
    "filter_v3": _command_filter_v3,
    "spec_generation": _command_spec_generation,
    "cleanup": _command_cleanup,
    "cloning": _command_cloning,
    "extraction": _command_cloning,
    "repository_setup": _command_repository_setup,
    "build": _command_build,
    "llm": _command_llm,
    "spec_context": _command_spec_context,
    "detector_rules": _command_detector_rules,
    "solutions": _command_solutions,
    "prs": _command_prs,
    "validation": _command_validation,
    "revalidation": _command_validation,
    "validation_fix": _command_validation_fix,
    "publish": _command_publish,
    "fix_prs": _command_fix_prs,
    "revalidation_fix_prs": _command_revalidation_fix_prs,
    "preview": _command_preview,
    "issues": _command_issues,
    "issues_cleanup": _command_issues_cleanup,
}


def command_for_step(
    step: str,
    args: argparse.Namespace,
    paths: PipelinePaths,
) -> List[str]:
    builder = STEP_COMMAND_BUILDERS.get(step)
    if builder is None:
        raise ValueError(f"Unknown step: {step}")
    return builder(step, args, paths, sys.executable)


def print_command(command: Sequence[str]) -> None:
    printable = " ".join(f'"{part}"' if " " in part else part for part in command)
    print(f"$ {printable}", flush=True)


def ensure_parent_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def format_elapsed_time(seconds: float) -> str:
    """Format a pipeline duration without wrapping hours after 24h."""
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"


def requested_service_tier(args: argparse.Namespace) -> str:
    """The OpenAI service tier this run's calls ask for; "" for any other provider."""
    if args.llm_provider != "openai":
        return ""
    return str(getattr(args, "llm_service_tier", "") or "")


def export_service_tier(args: argparse.Namespace) -> None:
    """Put the run's service tier where each step's OpenAIProvider reads it.

    Without it every step falls back to llm_providers.DEFAULT_SERVICE_TIER
    ("auto", the standard price), so the run's flex default would be lost
    silently. Other providers get no variable.
    """
    tier = requested_service_tier(args)
    if tier:
        from cognalys.llm.llm_providers import SERVICE_TIER_VARIABLE

        # Read by each step's OpenAIProvider, like the cost ceiling.
        os.environ[SERVICE_TIER_VARIABLE] = tier


def prepare_frozen_experiment(args: argparse.Namespace, paths: PipelinePaths,
                              root: Path) -> int:
    """Write or verify this run's manifest, and freeze what it depends on.

    The manifest's model block records the service tier the calls ask for
    (requested_service_tier), which experiment_manifest.verify compares.

    On the first run the manifest is written; on any later run against the
    same file it is verified, and a divergence stops the run before
    anything is spent. That order matters: a manifest that is only ever
    written records what happened without constraining it, which is what
    the retrospective's `uncommitted_changes: true` already did.

    Freezing is set in the environment because the steps that have to obey
    it are separate processes (see evaluation.experiment_manifest).
    """
    from cognalys.evaluation.experiment_manifest import (
        FROZEN_ENVIRONMENT_VARIABLE, LIVE_RESEARCH_VARIABLE, capture, read, verify, write,
    )
    from cognalys.llm.research_corpus import corpus_digest, corpus_entry_digests

    os.environ[FROZEN_ENVIRONMENT_VARIABLE] = "1"
    live_research = bool(getattr(args, "experiment_live_research", False))
    if live_research:
        os.environ[LIVE_RESEARCH_VARIABLE] = "1"
    else:
        os.environ.pop(LIVE_RESEARCH_VARIABLE, None)
    corpus_dir = getattr(args, "llm_research_corpus_dir", None)
    research_corpus = None
    if corpus_dir:
        research_corpus = {
            **corpus_digest(paths.root / corpus_dir),
            # Entries past it are not served: part of what the run was given.
            "max_age_days": float(args.llm_research_corpus_max_age_days),
        }
        if live_research:
            # The run adds entries as it researches: a later run against
            # this manifest (its resume, the same log folder) must still hold
            # these, unchanged; another run would start from a warmer corpus.
            research_corpus["entry_sha256"] = corpus_entry_digests(paths.root / corpus_dir)
            research_corpus["run_log_dir"] = str(openai_prompt_log_dir(args, paths))
    observed = capture(
        root=root,
        corpus=paths.csv_file,
        specifications=_library_data(paths, "specifications"),
        model={
            "provider": args.llm_provider, "model": args.llm_model,
            "endpoint": args.llm_endpoint,
            "reasoning_effort": args.llm_reasoning_effort,
            "spec_model": args.spec_model,
            # The tier the calls ask for (Flex halves the price and may
            # queue): conditions run on different tiers are declared apart.
            "service_tier": requested_service_tier(args),
            # None when the research calls keep the run's effort, which is
            # what a manifest written before the field reads as.
            "research_reasoning_effort": getattr(
                args, "llm_research_reasoning_effort", None),
        },
        budgets={
            # The option's dest (--llm-context-length is its alias): reading
            # args.llm_context_length raised AttributeError, so every
            # --experiment-manifest run stopped here.
            "context_length": args.ollama_context_length,
            "repository_context_budget": args.repository_context_budget,
            "max_output_tokens": args.llm_max_output_tokens,
            "retries": args.llm_retries,
            "workers": args.llm_workers,
            "timeout": args.llm_timeout,
            # The per-finding bound on model calls (llm_providers.CallBudget)
            # and the step deadline: conditions compared under different
            # limits are not given the same budget.
            **_finding_budget_limits(),
            "step_timeout_minutes": args.step_timeout_minutes,
        },
        learning_dir=resolved_learning_dir(args, root),
        condition=args.experiment_condition,
        research_corpus=research_corpus,
        live_research=live_research,
        holdout_repository=bool(getattr(args, "holdout_repository", False)),
    )
    manifest_path = Path(args.experiment_manifest)
    if not manifest_path.is_file():
        if getattr(args, "dry_run", False):
            # A rehearsal: every option the manifest records has been read
            # (the AttributeError that stopped every frozen run showed up
            # here), and nothing is recorded for a run that does not happen.
            print(
                f"Frozen experiment (dry run): the manifest was assembled and "
                f"is not written; the run itself writes it to {manifest_path}.",
                flush=True,
            )
            return 0
        write(manifest_path, observed)
        print(
            f"Frozen experiment: manifest written to {manifest_path}. "
            + ("Specification generation and learning-store writes are disabled "
               "for this run; web research not archived is fetched live."
               if live_research else
               "Specification generation, unarchived web research and "
               "learning-store writes are disabled for this run."),
            flush=True,
        )
        return 0
    differences = verify(read(manifest_path), observed)
    if differences:
        print(
            f"\nThis run does not match {manifest_path}:", file=sys.stderr,
        )
        for difference in differences:
            print(f"  {difference}", file=sys.stderr)
        print(
            "\nEither run it under the recorded conditions, or start a new "
            "manifest for the new ones. Comparing across them measures the "
            "difference between the conditions as well as the change.",
            file=sys.stderr, flush=True,
        )
        return 2
    print(
        f"Frozen experiment: this run matches {manifest_path}.", flush=True,
    )
    return 0


def finished_exit_code(partial_steps: List[str], warning_steps: List) -> int:
    """The exit code of a run no step of which failed.

    Partial coverage (some projects left behind) wins over a warning from an
    optional context step: a partial run that also had a spec_context
    warning exited 0, so a script reading the code saw a complete run
    (PLANO_CORRECOES_DEFESA A8). The warnings alone still exit 0.
    """
    return PARTIAL_SUCCESS_EXIT_CODE if partial_steps else 0


def _finding_budget_limits() -> dict:
    from cognalys.llm.llm_providers import budget_limits_from_environment

    limits = budget_limits_from_environment()
    return {
        "finding_max_calls": limits["max_calls"],
        "finding_max_seconds": limits["max_seconds"],
    }


def step_timings_path(paths: PipelinePaths) -> Path:
    return paths.error_solutions_dir.parent / "step_timings.json"


def earlier_step_timings(paths: PipelinePaths) -> List[dict]:
    """Steps earlier invocations of this run already recorded.

    A run is routinely resumed one step or one project at a time under the
    same run id. Rewriting the file with only the current invocation's steps
    left run_20260927_175748 with a single `publish` entry for a run whose
    LLM step alone took ninety minutes. An unreadable file contributes
    nothing rather than stopping the run.
    """
    try:
        recorded = json.loads(step_timings_path(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    steps = recorded.get("steps") if isinstance(recorded, dict) else None
    return [item for item in steps or [] if isinstance(item, dict)]


def record_step_timings(paths: PipelinePaths, timings: List[dict],
                        earlier: Sequence[dict] = ()) -> None:
    """How long each step took, rewritten after every step.

    `earlier` holds what previous invocations of the same run recorded, so
    each invocation adds to the file instead of replacing it. Written
    atomically so a run killed mid-write leaves the previous complete file
    rather than a truncated one. Never fails the run.
    """
    try:
        from cognalys.learning.knowledge import write_json_atomic

        write_json_atomic(
            step_timings_path(paths),
            {"steps": [*earlier, *timings]},
        )
    except OSError as exc:
        print(f"Step timings not recorded: {exc}", file=sys.stderr, flush=True)


def resolved_learning_dir(args: argparse.Namespace, root: Path) -> Path:
    """The learning store this run reads and writes."""
    chosen = (getattr(args, "learning_dir", None)
              or os.environ.get("TCC_LEARNING_DIR", "").strip()
              or "data/pipeline_learning")
    return (root / chosen).resolve()


def run_retrospective_safely(paths: PipelinePaths, args: argparse.Namespace,
                             executed_steps: List[str]) -> None:
    """Record what this run teaches the next one; never changes the exit code.

    Runs whenever any step ran, including after a failed step, since a run
    that stopped is exactly the one whose cause is worth recording.
    """
    if getattr(args, "no_retrospective", False) or not executed_steps:
        return
    try:
        from cognalys.learning.retrospective import run_retrospective

        report = run_retrospective(
            paths.error_solutions_dir.parent,
            resolved_learning_dir(args, paths.root),
            pipeline_root=paths.root,
            dirs={
                "error_solutions": paths.error_solutions_dir,
                "application_results": paths.application_results_dir,
                "pull_requests": paths.pull_requests_dir,
            },
        )
        print("\n=== RETROSPECTIVE ===", flush=True)
        print(report["summary_line"], flush=True)
        print(f"Report: {report['markdown_path']}", flush=True)
    except Exception as exc:  # noqa: BLE001 -- a retrospective must never fail a run
        print(f"Retrospective skipped: {exc}", file=sys.stderr, flush=True)


RUN_INPUTS_FILE = "run_inputs.json"
# Older runs recorded no inputs; one whose extracted findings cover this share
# of the findings CSV is taken to have run on the same input.
SAME_INPUT_OVERLAP = 0.8


# Steps whose failure is a result about the findings, not a stop: their
# reports are complete, and the preview step, which publishes nothing, still
# shows what the run concluded. Six runs (bluegate run, run_v5 and run_v6,
# lite run_v2, tasks run_v2, tokn run_v4; 2026-10-31 audit) ended with an
# empty previews/ because validation_fix exited 1 when its repair failed.
RESULT_STEPS_BEFORE_PREVIEW = frozenset({"validation", "validation_fix", "revalidation"})


def _read_run_inputs(run_dir: Path) -> dict:
    try:
        data = json.loads((run_dir / RUN_INPUTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def start_run_record(run_context: dict, run_dir: Path) -> None:
    """Record the library this pass runs, in run_inputs.json and run_status.json.

    run_inputs.json named only the findings file, so a changed outcome
    between two runs could not be told apart from a pipeline change (77 runs
    over 10-28..31 spanned batches 8-13, all merged the same day). The first
    pass's identity stays as "library"; run_status.json lists every pass.
    Never fails the run.
    """
    from cognalys.orchestration import run_status

    try:
        identity = run_status.library_identity()
        inputs = _read_run_inputs(run_dir)
        inputs.setdefault("library", identity)
        from cognalys.learning.knowledge import HISTORY_RULES_VERSION

        inputs.setdefault("learning_dir", os.environ.get("TCC_LEARNING_DIR", ""))
        inputs.setdefault("learning_history_rules_version", HISTORY_RULES_VERSION)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / RUN_INPUTS_FILE).write_text(
            json.dumps(inputs, indent=2) + "\n", encoding="utf-8")
        run_status.record_pass_start(run_dir, identity)
        run_context["run_dir"] = run_dir
    except (OSError, ValueError) as exc:
        print(f"Run status not recorded: {exc}", file=sys.stderr, flush=True)


def finish_run_record(run_context: dict) -> None:
    """Write run_status.json for the pass that ends; never changes the exit code."""
    run_dir = run_context.get("run_dir")
    if run_dir is None:
        return
    from cognalys.orchestration import run_status

    try:
        status = run_status.record_pass_end(
            run_dir,
            analysis_selected="llm" in run_context.get("steps", ()),
            analysis_outcome=run_context.get("analysis_outcome"),
            failed_steps=run_context.get("failed_steps", ()),
            identity_now=run_status.library_identity(),
        )
        print(f"Run status: {status['status']}"
              + (f" ({'; '.join(status['reasons'])})" if status.get("reasons") else ""),
              flush=True)
    except (OSError, ValueError) as exc:
        print(f"Run status not recorded: {exc}", file=sys.stderr, flush=True)


def _file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finding_keys_of_csv(csv_file: Path) -> set:
    return {
        (row.get("apk", ""), row.get("class", ""), row.get("method", ""),
         row.get("spec", ""))
        for row in read_rows(csv_file)
    }


def _finding_keys_of_run(run_dir: Path) -> set:
    keys = set()
    for path in run_dir.glob("error_methods/*/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta, error = data.get("extraction_metadata", {}), data.get("error", {})
        keys.add((meta.get("apk_name", ""), error.get("csv_class", ""),
                  error.get("csv_method", ""), error.get("spec", "")))
    return keys


def _llm_step_completed(run_dir: Path) -> bool:
    """Whether the run's analysis step finished; an interrupted run is no baseline.

    run_20261011_101038 was compared with run_20261011_091101, a run stopped
    after one of 25 analyses, and its stability table measured that.
    """
    try:
        timings = json.loads((run_dir / "step_timings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(
        step.get("step") == "llm" and step.get("exit_code") in (0, PARTIAL_SUCCESS_EXIT_CODE)
        for step in timings.get("steps", []) or []
    )


def export_cache_bypass_patterns(args: argparse.Namespace) -> list[str]:
    """Hand --llm-cache-bypass-finding to the steps' processes.

    Read by llm/llm_providers.py, in every step that analyses a finding; the
    steps inherit this environment (build_environment copies it).
    """
    patterns = [
        pattern.strip() for pattern in getattr(args, "llm_cache_bypass_finding", []) or []
        if pattern and pattern.strip()
    ]
    if patterns:
        os.environ["TCC_LLM_CACHE_BYPASS_FINDINGS"] = "\n".join(patterns)
    return patterns


def previous_run_on_same_input(run_dir: Path, csv_file: Path,
                               csv_sha256: str, max_candidates: int = 10):
    """The most recent other run that analysed the same findings, or None.

    A run that recorded its inputs is matched by the findings CSV's hash.
    One from before that record existed is matched when its extracted
    findings cover most of this CSV.
    """
    candidates = sorted(
        (path for path in run_dir.parent.glob("run_*")
         if path.is_dir() and path.resolve() != run_dir.resolve()
         and _llm_step_completed(path)),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )[:max_candidates]
    wanted = None
    for candidate in candidates:
        inputs_file = candidate / RUN_INPUTS_FILE
        if inputs_file.is_file():
            try:
                recorded = json.loads(inputs_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                recorded = {}
            if recorded.get("findings_csv_sha256") == csv_sha256:
                return candidate
            continue
        if wanted is None:
            wanted = _finding_keys_of_csv(csv_file)
        if wanted and len(_finding_keys_of_run(candidate) & wanted) >= SAME_INPUT_OVERLAP * len(wanted):
            return candidate
    return None


def run_statistics_safely(paths: PipelinePaths, args: argparse.Namespace,
                          executed_steps: List[str]) -> None:
    """Write <run>/statistics; never changes the exit code, like the retrospective."""
    if getattr(args, "no_statistics", False) or not executed_steps:
        return
    try:
        run_dir = paths.error_solutions_dir.parent
        csv_file = paths.csv_file
        if not csv_file.is_file() or not run_dir.is_dir():
            return
        csv_sha256 = _file_sha256(csv_file)
        # Merged: the library identity was written when the pass started.
        inputs = _read_run_inputs(run_dir)
        inputs.update(findings_csv=str(csv_file), findings_csv_sha256=csv_sha256)
        (run_dir / RUN_INPUTS_FILE).write_text(
            json.dumps(inputs, indent=2) + "\n", encoding="utf-8")
        previous = previous_run_on_same_input(run_dir, csv_file, csv_sha256)
        command = [
            sys.executable, "-m", "cognalys.reporting.finding_statistics",
            "--runs", *([str(previous)] if previous else []), str(run_dir),
            "--findings-csv", str(csv_file),
            "--report-dir", str(run_dir / "statistics"),
        ]
        annotations = getattr(args, "statistics_annotations", "")
        if annotations and (paths.root / annotations).is_file():
            command += ["--annotations", str((paths.root / annotations).resolve())]
        if rv_import_active(args):
            severe_sites = (paths.root / args.rv_import_dir / "severe-sites.csv").resolve()
            if severe_sites.is_file():
                command += ["--severe-sites", str(severe_sites)]
            corpus = (paths.root / args.rv_import_dir / "dataset.csv").resolve()
            if corpus.is_file():
                command += ["--corpus", str(corpus)]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
        print("\n=== STATISTICS ===", flush=True)
        if completed.returncode == 0:
            print(f"Report: {run_dir / 'statistics' / 'statistics.md'}"
                  + (f" (compared with {previous.name})" if previous else ""), flush=True)
        else:
            print(f"Statistics skipped: {completed.stderr.strip()[-500:]}",
                  file=sys.stderr, flush=True)
    except Exception as exc:  # noqa: BLE001 -- statistics must never fail a run
        print(f"Statistics skipped: {exc}", file=sys.stderr, flush=True)


def materialize_mapped_findings(args: argparse.Namespace, root: Path) -> Path:
    """The findings file with --column-map applied, as findings.jsonl.

    Written once into the run directory so every step reads the corpus
    columns from the same file, rather than each applying the map itself.
    A source row whose columns did not split where its header does is
    marked with its problem before conversion; otherwise the surplus or
    missing fields would turn into ordinary-looking values on the way.
    """
    from cognalys.sources import (
        SOURCE_PROBLEM, apply_column_map, load_column_map, write_rows,
    )
    from cognalys.utils.corpus_rows import row_problems

    source = root / args.csv_file
    mapping = load_column_map(root / args.column_map)
    rows = []
    for row in read_rows(source):
        problems = row_problems(row)
        row = {key: value for key, value in row.items() if key is not None}
        if problems:
            row[SOURCE_PROBLEM] = "; ".join(problems)
        rows.append(row)
    run_dir = resolve_paths(args, root).extracted_methods_dir.parent
    return write_rows(apply_column_map(rows, mapping), run_dir / "findings.jsonl")


def needs_intake(args: argparse.Namespace, root: Path) -> bool:
    """Whether the findings are not one file the steps can read as it is.

    Several files, a SARIF or Semgrep report, a checkout to resolve against,
    or columns under other names all go through cognalys.intake; a single
    file already in the corpus columns is read where it is, as always.
    """
    from cognalys import intake

    files = [root / name for name in (getattr(args, "findings_files", None) or [])]
    if len(files) > 1 or getattr(args, "findings_repo", ""):
        return True
    if not files or not files[0].is_file():
        return False
    if intake.report_kind(files[0]) != "rows":
        return True
    try:
        rows = read_rows(files[0])
    except (OSError, ValueError):
        return False
    columns = {key for row in rows[:50] for key in row if key}
    return not set(intake.REQUIRED) <= columns


def materialize_intake(args: argparse.Namespace, root: Path) -> Path:
    """The --findings files as one findings.jsonl in the run directory.

    A finding that could not be made a row is written with its problem
    (SOURCE_PROBLEM), so extraction counts it as invalid input with the
    reason instead of it vanishing between the user's file and the run.
    """
    from cognalys.intake import PROVENANCE, read_findings
    from cognalys.sources import SOURCE_PROBLEM, write_rows

    files = [root / name for name in (args.findings_files or [args.csv_file])]
    repo = getattr(args, "findings_repo", "")
    taken = read_findings(files, (root / repo) if repo else None)
    rows = list(taken.rows) + [
        {PROVENANCE: problem.get("source", ""), SOURCE_PROBLEM: problem["problem"]}
        for problem in taken.problems
    ]
    for problem in taken.problems:
        print(f"Findings input: {problem.get('source', '')}: {problem['problem']}",
              file=sys.stderr, flush=True)
    if taken.renamed:
        print("Findings input: read "
              + ", ".join(f"{source} as {target}" for source, target in sorted(taken.renamed.items())),
              flush=True)
    run_dir = resolve_paths(args, root).extracted_methods_dir.parent
    return write_rows(rows, run_dir / "findings.jsonl")


def _run_pipeline(args) -> int:
    pipeline_started_at = time.monotonic()
    executed_steps: List[str] = []
    run_context: dict = {}
    # The learning store this run exports for its steps (resolved_learning_dir)
    # is put back when it ends: a second run in the same process (api.run)
    # would otherwise read and write this run's store (review of 2026-11-10).
    learning_before = os.environ.get("TCC_LEARNING_DIR")

    def finish(exit_code: int) -> int:
        finish_run_record(run_context)
        if run_context:
            run_retrospective_safely(run_context["paths"], run_context["args"], executed_steps)
            run_statistics_safely(run_context["paths"], run_context["args"], executed_steps)
        if learning_before is None:
            os.environ.pop("TCC_LEARNING_DIR", None)
        else:
            os.environ["TCC_LEARNING_DIR"] = learning_before
        elapsed = format_elapsed_time(time.monotonic() - pipeline_started_at)
        print(f"Pipeline elapsed time: {elapsed}", flush=True)
        return exit_code

    root = project_root()
    if getattr(args, "column_map", ""):
        if len(getattr(args, "findings_files", None) or []) > 1:
            print("--column-map applies to a single --findings file.", file=sys.stderr)
            return finish(2)
        args.csv_file = str(materialize_mapped_findings(args, root))
    elif needs_intake(args, root):
        args.csv_file = str(materialize_intake(args, root))
    paths = resolve_paths(args, root)
    run_context.update(args=args, paths=paths)
    steps = selected_steps(args)
    run_context["steps"] = list(steps)
    if getattr(args, "online_dependency_resolution", False):
        # Read by extraction/dependency_context.py in the steps' processes,
        # which inherit this environment (build_environment copies it).
        os.environ["TCC_ONLINE_DEPENDENCY_RESOLUTION"] = "1"
    export_cache_bypass_patterns(args)
    if args.llm_provider in ("openai", "anthropic"):
        # Read by llm_providers.remote_provider in each step's process: the
        # steps take --endpoint and --model, not the provider.
        os.environ["COGNALYS_LLM_PROVIDER"] = args.llm_provider
    export_service_tier(args)
    # Read by learning.knowledge.load_knowledge in the steps' processes: the
    # history and imports a plan, a draft and a repair read come from the
    # store this run writes.
    os.environ["TCC_LEARNING_DIR"] = str(resolved_learning_dir(args, root))
    if getattr(args, "max_cost", 0) and args.llm_provider in ("openai", "anthropic"):
        from cognalys.llm.cost import MAX_COST_VARIABLE, PRICES_FILE, load_prices, price_of

        # Checked here, before any step: a ceiling that cannot be priced
        # would only be found out by the first call of the LLM step.
        if price_of(args.llm_model, load_prices()) is None:
            print(f"--max-cost needs the price of {args.llm_model} in data/{PRICES_FILE} "
                  "(USD per million input, cached_input and output tokens).", file=sys.stderr)
            return finish(2)
        # Read by each step's provider (llm.cost.RunCostGuard).
        os.environ[MAX_COST_VARIABLE] = str(args.max_cost)
    if getattr(args, "holdout_repository", False):
        from cognalys.llm.fix_generation_common import HOLDOUT_REPOSITORY_VARIABLE

        # Read by each step's prompts (learning_context, repair's imports).
        os.environ[HOLDOUT_REPOSITORY_VARIABLE] = "1"
    if getattr(args, "llm_research_reasoning_effort", None):
        from cognalys.llm.llm_providers import RESEARCH_EFFORT_VARIABLE

        # Read by the LLM step around its web-research calls only.
        os.environ[RESEARCH_EFFORT_VARIABLE] = args.llm_research_reasoning_effort

    if args.enable_filter_v3 and "filter_v3" not in steps:
        print(
            "--enable-filter-v3 cannot be combined with --skip filter_v3.",
            file=sys.stderr,
        )
        return finish(2)
    if "filter_v3" in steps and not paths.input_csv_file.is_file():
        print(
            f"Filter v3 input CSV not found: {paths.input_csv_file}",
            file=sys.stderr,
        )
        return finish(2)
    if "filter_v3" in steps and paths.input_csv_file == paths.csv_file:
        print(
            "Filter v3 input and output CSV must be different files.",
            file=sys.stderr,
        )
        return finish(2)
    if rv_import_active(args) and filter_v3_active(args):
        # filtro_v3 samples the raw detector corpus, not a converted package.
        print(
            "rv_import and filter_v3 cannot be enabled together.",
            file=sys.stderr,
        )
        return finish(2)
    if args.enable_rv_import and "rv_import" not in steps:
        print(
            "--enable-rv-import cannot be combined with --skip rv_import.",
            file=sys.stderr,
        )
        return finish(2)
    if "rv_import" in steps and not (root / args.rv_import_dir).is_dir():
        print(
            f"rv_import package directory not found: {root / args.rv_import_dir}",
            file=sys.stderr,
        )
        return finish(2)

    if not steps:
        print("No steps selected.", file=sys.stderr)
        return finish(2)

    if getattr(args, "experiment_live_research", False) and not args.experiment_manifest:
        print("--experiment-live-research needs --experiment-manifest.", file=sys.stderr)
        return finish(2)
    if args.experiment_manifest:
        code = prepare_frozen_experiment(args, paths, root)
        if code:
            return finish(code)

    # Before anything is created, cloned, or paid for. Three of this
    # project's recorded run failures were a NameError, an UnboundLocalError
    # and a provider refusal, each found only after the run had spent minutes
    # of model time on the finding it then lost.
    if not args.skip_preflight:
        report = run_preflight(root)
        if not report.ok:
            print("\nPreflight checks failed:", file=sys.stderr)
            for problem in report.problems:
                print(f"  {problem}", file=sys.stderr)
            print(
                "\nFix these before running, or pass --skip-preflight "
                "to run anyway.",
                file=sys.stderr,
                flush=True,
            )
            return finish(2)
        print(
            "Preflight passed: " + ", ".join(report.checks_run), flush=True,
        )

    ensure_parent_dirs(
        [
            paths.cloned_projects_dir,
            paths.extracted_methods_dir,
            paths.build_output_dir,
            paths.error_solutions_dir,
            paths.application_results_dir,
            paths.worktrees_dir,
            paths.pull_requests_dir,
            paths.issues_dir,
            paths.previews_dir,
        ]
    )
    paths.csv_file.parent.mkdir(parents=True, exist_ok=True)

    if not args.dry_run:
        start_run_record(run_context, paths.error_solutions_dir.parent)

    env = build_environment(root)
    failed_steps = []
    run_context["failed_steps"] = failed_steps
    warning_steps = []
    partial_steps = []
    step_timings: List[dict] = []
    earlier_timings = earlier_step_timings(paths)

    print("Pipeline steps:", " -> ".join(steps), flush=True)

    steps_remaining = list(steps)
    while steps_remaining:
        step = steps_remaining.pop(0)
        print(f"\n=== {step.upper()} ===", flush=True)
        command = command_for_step(step, args, paths)
        print_command(command)

        step_started = time.monotonic()
        step_started_wall = time.time()
        watch = llm_step_watch(step, args, paths)
        outcome = run_with_deadline(
            command, cwd=root, env=env,
            timeout_seconds=max(0.0, args.step_timeout_minutes) * 60,
            **watch,
        )
        if outcome.get("idle"):
            # Resumed once: findings with a saved solution are skipped and the
            # model calls already made come from the cache.
            print(f"Step '{step}' stalled; resuming it once.", file=sys.stderr, flush=True)
            outcome = run_with_deadline(
                command, cwd=root, env=env,
                timeout_seconds=max(0.0, args.step_timeout_minutes) * 60,
                **watch,
            )
        if step == "llm":
            # As the step itself ended, before the partial-success relabel
            # below: run_status reads whether it stalled or ran to its end.
            run_context["analysis_outcome"] = {
                "returncode": outcome["returncode"],
                "timed_out": bool(outcome.get("timed_out")),
                "idle": bool(outcome.get("idle")),
                "started_at": step_started_wall,
            }
        if (step == "llm" and outcome["returncode"] not in (0, PARTIAL_SUCCESS_EXIT_CODE)
                and any(paths.error_solutions_dir.glob("*.apk/*_solution.json"))):
            # The solutions already written are complete and worth applying:
            # Amethyst's two decrypt fixes (2026-10-25) were never applied
            # because the step that had produced them was stopped later.
            print(f"Step '{step}' did not finish, but the solutions it saved go on "
                  "to be applied.", file=sys.stderr, flush=True)
            outcome = {**outcome, "returncode": PARTIAL_SUCCESS_EXIT_CODE}
        result = subprocess.CompletedProcess(command, outcome["returncode"])
        executed_steps.append(step)
        step_timings.append({
            "step": step,
            "project": args.project or "",
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "seconds": round(time.monotonic() - step_started, 1),
            "exit_code": result.returncode,
            "timed_out": outcome["timed_out"],
        })
        record_step_timings(paths, step_timings, earlier_timings)
        if outcome["timed_out"]:
            print(
                step_ending_message(step, outcome, args.step_timeout_minutes,
                                    time.monotonic() - step_started),
                file=sys.stderr, flush=True,
            )
        if result.returncode == PARTIAL_SUCCESS_EXIT_CODE:
            # Some projects got through and some did not. The step has
            # already recorded which in its own report, and stopping here
            # would end the run for every project that was fine -- after
            # their work had been paid for. A batch of twenty repositories
            # nearly always contains one that will not clone or build.
            partial_steps.append(step)
            print(
                f"Step '{step}' finished partially: some projects failed and "
                f"the rest went through. Continuing with the survivors; the "
                f"failures are in this step's own report.",
                file=sys.stderr,
                flush=True,
            )
        elif result.returncode != 0:
            if step in {"spec_generation", "spec_context", "detector_rules"}:
                warning_steps.append((step, result.returncode))
                print(
                    f"Optional context step '{step}' failed with exit code "
                    f"{result.returncode}; continuing without blocking the pipeline.",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            failed_steps.append((step, result.returncode))
            print(
                f"Step '{step}' failed with exit code {result.returncode}.",
                file=sys.stderr,
                flush=True,
            )
            if (step in RESULT_STEPS_BEFORE_PREVIEW and "preview" in steps_remaining
                    and not (args.continue_on_error or args.ignore_step_errors)):
                skipped_now = [pending for pending in steps_remaining if pending != "preview"]
                steps_remaining = ["preview"]
                print(
                    "Running preview anyway, which publishes nothing"
                    + (f"; skipping {' and '.join(skipped_now)}" if skipped_now else "")
                    + ".", file=sys.stderr, flush=True,
                )
                continue
            if not (args.continue_on_error or args.ignore_step_errors):
                return finish(result.returncode)

        if step == "validation" and not args.force_revalidation:
            report_path = paths.application_results_dir / "execution_report.json"
            if validation_fully_passed(report_path):
                skipped_now = [
                    pending for pending in ("validation_fix", "revalidation")
                    if pending in steps_remaining
                ]
                if skipped_now:
                    steps_remaining = [
                        pending for pending in steps_remaining
                        if pending not in {"validation_fix", "revalidation"}
                    ]
                    print(
                        "Validation succeeded for every solution; skipping "
                        + " and ".join(skipped_now)
                        + " (use --force-revalidation to always run them).",
                        flush=True,
                    )

        if (step == "validation_fix" and not args.force_revalidation
                and "revalidation" in steps_remaining
                and not validation_fix_repaired_anything(
                    paths.application_results_dir / "execution_report.json"
                )):
            steps_remaining = [
                pending for pending in steps_remaining if pending != "revalidation"
            ]
            print(
                "validation_fix applied no repair; skipping revalidation, "
                "which would rebuild unchanged worktrees (use "
                "--force-revalidation to always run it).",
                flush=True,
            )

        if step == "solutions" and not args.force_downstream_steps:
            to_skip: set = set()
            reasons: List[str] = []
            if solutions_report_is_empty(
                paths.application_results_dir / "execution_report.json"
            ):
                to_skip |= {
                    "validation", "validation_fix", "revalidation", "prs",
                    "publish", "fix_prs", "revalidation_fix_prs",
                }
                reasons.append("nothing was applied")
            if not has_issue_candidates(
                paths.error_solutions_dir / "issue_candidates"
            ):
                to_skip.add("issues")
                reasons.append("no issue candidates were produced")
            skipped_now = [
                pending for pending in steps_remaining if pending in to_skip
            ]
            if skipped_now:
                steps_remaining = [
                    pending for pending in steps_remaining
                    if pending not in to_skip
                ]
                print(
                    "".join(reason.capitalize() + "; " for reason in reasons)
                    + "skipping " + " and ".join(skipped_now)
                    + " (use --force-downstream-steps to always run them). "
                    "preview still runs.",
                    flush=True,
                )

    if partial_steps:
        print(
            "\nSome projects did not get through every step: "
            + ", ".join(partial_steps)
            + ". The run continued with the ones that did; each step's own "
            "report names the projects that failed.",
            flush=True,
        )

    print_model_cost(openai_prompt_log_dir(args, paths))

    if failed_steps:
        print("\nPipeline finished with failures:", file=sys.stderr)
        for step, returncode in failed_steps:
            print(f"- {step}: exit code {returncode}", file=sys.stderr)
        if args.ignore_step_errors:
            print(
                "Step failures were ignored by --ignore-step-errors.",
                file=sys.stderr,
            )
            return finish(0)
        return finish(1)

    if warning_steps:
        print("\nPipeline finished with non-blocking context warnings:", flush=True)
        for step, returncode in warning_steps:
            print(f"- {step}: exit code {returncode}", flush=True)
    code = finished_exit_code(partial_steps, warning_steps)
    if code == PARTIAL_SUCCESS_EXIT_CODE:
        # Not "successfully": every step that ran got somewhere, but some
        # projects were left behind and the operator has to know that from
        # the exit code alone, not only from the text above.
        print("\nPipeline finished with partial coverage.", flush=True)
    elif not warning_steps:
        print("\nPipeline finished successfully.", flush=True)
    return finish(code)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if getattr(args, "prune_unused_repos", "off") != "off":
        from cognalys.maintenance.prune_unused_repositories import main as prune_main

        command = command_for_step(
            "prune_unused_repos", args, resolve_paths(args, project_root()),
        )
        # The step command is [python, "-m", module, *arguments]; the
        # pruning entry point is called in process with the arguments only.
        return prune_main(command[3:])
    if args.dry_run:
        return _run_pipeline(args)
    from cognalys.utils.repository_lock import RepositoryUsageLock, RepositoryBusyError

    try:
        with RepositoryUsageLock(project_root()):
            return _run_pipeline(args)
    except (RepositoryBusyError, OSError) as exc:
        print(f"Pipeline repository access stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
