#!/usr/bin/env python3

"""
PR Generator

Generates:
- git branches
- commits
- diffs
- PR metadata JSONs

based on repositories modified by SolutionApplicator.
"""

import json
import hashlib
import difflib
import logging
import os
import re
import subprocess

from cognalys.utils.artifact_names import finding_artifact_stem
from cognalys.utils.disposition import (
    BUILD_NEVER_RAN_STATUSES,
    BUILD_VALIDATION_PASSING_STATUSES,
    COMPILE_ONLY_PASS,
    PRODUCTION_ONLY_TEST_BUILD_PREEXISTING,
    FindingDisposition,
    build_says_nothing_about_patch,
    decision_label,
    # Moved to utils, where the publication verdict reads it too.
    files_the_build_does_not_compile,  # noqa: F401
    publication_after_validation,
    repaired_patch_review_blocks,
    without_blockers_of_findings_without_effect,
)
from cognalys.prs.impact_class import adjudication_of, impact_class
from cognalys.prs.pr_grouping import edits_missing_from_diff, relate
from cognalys.prs.publication_checks import ai_policy, policy_files_in_checkout
from cognalys.prs.pr_outcomes import (
    open_pull_request_for, publication_search_root, recorded_pull_request_state,
)
from cognalys.prs.pr_template import (
    body_for_repository, facts_from_pull_request, problems_in_own_words,
    template_in_checkout, template_instructions_left,
)
from cognalys.utils.finding_source import (
    is_analyzer_tool, is_rvsec_finding, tool_and_spec_context,
)
from cognalys.utils.pr_text import (
    MAX_TITLE_CHARACTERS,
    analysed_identity,
    attributed_view,
    changes_list,
    earlier_data_still_reads,
    finding_paragraph,
    joint_finding_claims,
    joint_finding_without_effect,
    missing_effect_problems,
    review_text_problems,
    rule_only_justification_problems,
    merged_joint_without_effect,
    new_written_format,
    plain_explanation,
    short_symbol,
    source_names,
    symbol_phrase,
    finding_specs,
    split_title_marker,
    stale_names_paragraph_problems,
    stale_value_names_note,
    text_problems,
    title_from_edit_reasons,
    user_visible_effects,
    value_problem_reasons,
)
from pathlib import Path
from typing import Dict, List, Optional


# Left as the default, the commit message is the pull request's title.
DEFAULT_COMMIT_TEMPLATE = "Fix {method_name}"


# One Gradle invocation of the repository's formatter, configuration included
# (NotallyX's ktfmt run took about 35 s per invocation, 2026-10-30).
FORMATTER_TIMEOUT_SECONDS = 600


class PrGenerator:

    def __init__(
        self,
        execution_report_path: str,
        output_dir: str = "pull_requests",
        branch_prefix: str = "fix",
        commit_template: str = DEFAULT_COMMIT_TEMPLATE,
        title_template: str = "Fix: {method_name}",
        max_prs: int = 0,
        dry_run: bool = False,
        project: str = "",
        style: str = "v1",
        publish_without_build_validation=None,
        text_model: Optional[dict] = None,
    ):
        # remote_provider arguments for rewriting a text that fails its
        # checks (repair_text); without them such a text is only held.
        self.text_model = dict(text_model or {})
        self._text_client = None

        self.execution_report_path = Path(
            execution_report_path
        )

        self.output_dir = Path(output_dir)
        self.branch_prefix = branch_prefix.strip("/")
        self.commit_template = commit_template
        self.title_template = title_template
        self.max_prs = max_prs
        self.dry_run = dry_run
        self.project = project.strip()
        self.style = style
        # Finding ids a human explicitly approved for publication even
        # though the build never ran. Per finding and never blanket: the
        # operator has to name the one they read.
        self.publish_without_build_validation = {
            str(value).strip()
            for value in (publish_without_build_validation or [])
            if str(value).strip()
        }

        self.output_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        self.logger = logging.getLogger(
            self.__class__.__name__
        )

    @staticmethod
    def safe_git_ref_component(value: str, fallback: str = "change") -> str:
        """Return a portable component suitable for a Git branch name."""
        value = value.strip()
        value = re.sub(r"[\x00-\x20\x7f~^:?*\[\\()$]+", "-", value)
        value = re.sub(r"[^A-Za-z0-9._/-]+", "-", value)
        value = re.sub(r"/+", "/", value)
        value = re.sub(r"-+", "-", value)
        value = value.strip("./-")
        value = value.replace("..", ".")
        value = value.replace("@{", "-")
        if value.endswith(".lock"):
            value = value[:-5] + "-lock"
        return value or fallback

    def build_branch_name(self, report: dict, solution: dict) -> str:
        """Build a stable branch name unique to both finding and pipeline run."""
        method = self.safe_git_ref_component(
            solution.get("method_name", "change")
        ).lower()
        spec = self.safe_git_ref_component(
            solution.get("spec", "finding")
        ).lower()
        timestamp = re.sub(
            r"[^0-9]", "", str(report.get("execution_timestamp", ""))
        )[:14] or "unknown-run"
        identity = "|".join(
            str(solution.get(field, ""))
            for field in (
                "apk_name", "csv_class", "method_name", "spec",
                "error_message", "source_file",
            )
        )
        finding_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
        return (
            f"{self.branch_prefix}/{method}-{spec}-{timestamp}-{finding_id}"
        )

    @staticmethod
    def safe_filename_component(value: str, fallback: str = "change") -> str:
        value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
        return value.strip("._-") or fallback

    # ==========================================================
    # GIT
    # ==========================================================

    def run_git(
        self,
        repo: Path,
        args: List[str],
        keep_whitespace: bool = False
    ) -> str:

        result = subprocess.run(
            ["git"] + args,
            cwd=repo,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:

            raise RuntimeError(
                result.stderr
            )

        # A diff ends in a newline, and its last line can be the context
        # line of a blank source line: a single space. strip() removed it
        # (rssreader, 2026-10-16) and left a hunk one line short, which
        # git apply rejects as a corrupt patch.
        if keep_whitespace:
            return result.stdout
        return result.stdout.strip()

    def try_git(
        self,
        repo: Path,
        args: List[str]
    ) -> str:

        result = subprocess.run(
            ["git"] + args,
            cwd=repo,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:

            return ""

        return result.stdout.strip()

    def git_succeeds(
        self,
        repo: Path,
        args: List[str]
    ) -> bool:
        """Run a git query whose answer is its exit code, not its output."""

        result = subprocess.run(
            ["git"] + args,
            cwd=repo,
            capture_output=True,
            text=True
        )

        return result.returncode == 0

    def parse_remote_url(
        self,
        remote_url: str
    ) -> Dict[str, str]:

        patterns = [
            r"git@([^:]+):([^/]+)/(.+?)(?:\.git)?$",
            r"https?://([^/]+)/([^/]+)/(.+?)(?:\.git)?/?$",
        ]

        for pattern in patterns:

            match = re.search(
                pattern,
                remote_url
            )

            if match:

                return {
                    "repo_host": match.group(1),
                    "repo_owner": match.group(2),
                    "repo_name": match.group(3),
                }

        return {
            "repo_host": "",
            "repo_owner": "",
            "repo_name": "",
        }

    def get_repository_metadata(
        self,
        repo: Path
    ) -> Dict[str, str]:

        for remote_name in ["upstream", "origin"]:

            remote_url = self.try_git(
                repo,
                [
                    "config",
                    "--get",
                    f"remote.{remote_name}.url"
                ]
            )

            if not remote_url:

                continue

            metadata = self.parse_remote_url(
                remote_url
            )

            metadata.update(
                {
                    "remote_name": remote_name,
                    "remote_url": remote_url,
                }
            )

            return metadata

        return {
            "remote_name": "",
            "remote_url": "",
            "repo_host": "",
            "repo_owner": "",
            "repo_name": "",
        }

    # ==========================================================
    # FIND GIT ROOT
    # ==========================================================

    def find_git_repo(
        self,
        path: Path
    ) -> Optional[Path]:

        current = path.parent

        while current != current.parent:

            if (current / ".git").exists():
                return current

            current = current.parent

        return None

    def resolve_source_file(self, report: dict, solution: dict) -> Path:
        """Resolve a report path without exposing the absolute path publicly."""
        worktree = str(solution.get("worktree_path", "")).strip()
        if worktree:
            return Path(worktree) / str(solution.get("source_file", ""))
        source_file = Path(solution["source_file"])
        if source_file.is_absolute():
            return source_file

        cloned_projects_dir = Path(
            report.get("config", {}).get("cloned_projects_dir", "")
        )
        return cloned_projects_dir / solution["apk_name"] / source_file

    @staticmethod
    def public_source_file(repo: Path, source_file: Path) -> str:
        """Return a repository-relative path safe for public text."""
        try:
            return source_file.resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            normalized = str(source_file).replace("\\", "/")
            if ".apk/" in normalized:
                return normalized.split(".apk/", 1)[1]
            return source_file.name

    # ==========================================================
    # BRANCH
    # ==========================================================

    def create_branch(
        self,
        repo: Path,
        branch_name: str
    ):

        existing = self.run_git(
            repo,
            [
                "branch",
                "--list",
                branch_name
            ]
        )

        if existing.strip():

            self.logger.info(
                f"Branch already exists: {branch_name}"
            )

            self.run_git(
                repo,
                [
                    "checkout",
                    branch_name
                ]
            )

            return

        self.logger.info(
            f"Creating branch {branch_name}"
        )

        self.run_git(
            repo,
            [
                "checkout",
                "-b",
                branch_name
            ]
        )

    # ==========================================================
    # COMMIT
    # ==========================================================

    def commit_changes(
        self,
        repo: Path,
        commit_message: str,
        changed_files: Optional[list[str]] = None,
    ) -> str:

        paths = sorted({str(path).strip() for path in (changed_files or [])
                        if str(path).strip()})
        self.run_git(repo, ["add", "--", *(paths or ["."])])

        # What is staged decides, not the whole status: a rerun over a branch
        # that already carries the fix still shows the build's untracked
        # output (app/build/, .gradle/), and committing nothing failed with
        # git's reason on stdout and an empty error (rssreader, 2026-10-16).
        status = self.run_git(
            repo,
            [
                "diff",
                "--cached",
                "--name-only"
            ]
        )

        if not status.strip():

            # A patch repaired after a failed build was already committed by
            # validation.repair under its internal message, and that commit
            # is what the pull request would show (DSub2000#178). It takes
            # the pull request's message instead.
            from cognalys.validation.repair import REPAIR_COMMIT_PREFIX

            head_message = self.run_git(repo, ["log", "-1", "--format=%s"]).strip()
            if head_message.startswith(REPAIR_COMMIT_PREFIX):
                self.run_git(repo, ["commit", "--amend", "--signoff", "-m", commit_message])
                return self.run_git(repo, ["rev-parse", "HEAD"])

            self.logger.info(
                "No changes to commit"
            )

            return ""

        from cognalys.validation.repair import REPAIR_COMMIT_PREFIX

        # try_git: a repository without commits has no subject to read.
        if self.try_git(repo, ["log", "-1", "--format=%s"]).startswith(
                REPAIR_COMMIT_PREFIX):
            # The repair commit under its internal message holds part of the
            # patch; what is staged now (the rest, or what the repository's
            # formatter changed in the repaired lines) joins it under the pull
            # request's message. A second commit on top left the internal
            # message in the pull request (NotallyX#1167, 2026-10-30, as
            # DSub2000#178 before it).
            self.run_git(repo, ["commit", "--amend", "--signoff", "-m", commit_message])
            return self.run_git(repo, ["rev-parse", "HEAD"])

        # --signoff appends the Developer Certificate of Origin trailer that
        # a DCO check requires, built by git from the user.name/user.email
        # this pipeline already configures -- the same identity that authors
        # the commit. Without it, nextcloud/news-android#1716 was opened and
        # immediately blocked: "There is one commit incorrectly signed off."
        # The remedy for an already-pushed commit is a rebase and a force
        # push, so it is much cheaper to sign every commit than to repair
        # the ones that needed it. Projects that do not check DCO ignore the
        # trailer.
        self.run_git(
            repo,
            [
                "commit",
                "--signoff",
                "-m",
                commit_message
            ]
        )

        commit_hash = self.run_git(
            repo,
            [
                "rev-parse",
                "HEAD"
            ]
        )

        return commit_hash

    def format_with_repository_formatter(
        self,
        repo: Path,
        solution: dict,
        changed_files: list[str],
    ) -> dict:
        """Format the patch's own lines with the formatter the repository configures.

        NotallyX#1167 (2026-10-30) was published with test lines over the
        100 columns its ktfmt pre-commit hook enforces. The build already
        validated this patch, so only layout changes are kept
        (repository_formatter.format_patch with only_layout); a formatter
        that is missing or fails leaves the patch as it was, with the reason
        in the record.
        """
        from cognalys.prs.repository_formatter import format_patch, texts_at
        from cognalys.validation.repair import REPAIR_COMMIT_PREFIX

        if not changed_files:
            return {}
        uncommitted = self.try_git(repo, ["status", "--porcelain", "--", *changed_files])
        head_message = self.try_git(repo, ["log", "-1", "--format=%s"])
        if not uncommitted and not head_message.startswith(REPAIR_COMMIT_PREFIX):
            # A rerun over a branch that already carries the fix commits
            # nothing; a formatting commit on top of a commit that may be
            # published would be a change nobody reviewed.
            return {"status": "NOT_RUN", "note": "the patch is already committed"}
        base = self.resolve_base_commit(repo, solution) or "HEAD"
        try:
            record = format_patch(
                repo, changed_files, texts_at(repo, base, changed_files),
                timeout=FORMATTER_TIMEOUT_SECONDS, only_layout=True)
        except Exception as exc:  # noqa: BLE001 -- formatting never holds a patch
            record = {"status": "FAILED", "note": str(exc)}
        if record.get("status") in {"FORMATTED", "FAILED"}:
            self.logger.info(
                "%s: repository formatter %s -> %s %s", solution.get("method_name", ""),
                record.get("formatter") or "none", record["status"],
                record.get("formatted_files") or record.get("note", ""))
        return record

    def resolve_base_commit(
        self,
        repo: Path,
        solution: dict
    ) -> str:
        """Resolve the commit this solution's branch was created from."""

        candidates = [
            str(solution.get("base_commit", "")).strip(),
            str(solution.get("base_branch", "")).strip(),
        ]

        for candidate in candidates:

            if not candidate:
                continue

            resolved = self.try_git(
                repo,
                [
                    "rev-parse",
                    "--verify",
                    "--quiet",
                    f"{candidate}^{{commit}}"
                ]
            )

            if resolved:
                return resolved

        return ""

    def diff_base_ref(
        self,
        repo: Path,
        solution: dict
    ) -> str:
        """Return the recorded base commit when it can anchor a diff."""

        base = self.resolve_base_commit(repo, solution)

        if not base:
            return ""

        head = self.try_git(
            repo,
            [
                "rev-parse",
                "--verify",
                "--quiet",
                "HEAD"
            ]
        )

        if not head or head == base:
            return ""

        if not self.git_succeeds(
            repo,
            [
                "merge-base",
                "--is-ancestor",
                base,
                head
            ]
        ):
            return ""

        return base

    def find_existing_fix_commit(
        self,
        repo: Path,
        solution: dict,
        changed_files: Optional[list[str]] = None,
    ) -> str:
        """Return the commit that already carries this solution's patch.

        Re-running a step over a finding whose branch an earlier run already
        built leaves a clean worktree: the edit is not pending, it is
        committed. commit_changes() cannot tell that apart from "nothing was
        ever applied", so a rerun used to count as a failed PR and abort every
        later step. Anchor the difference on the base commit the applicator
        recorded for this solution: when the checked out branch descends from
        that base and carries file changes on top of it, the work exists and
        the run may continue from it.
        """

        base = self.diff_base_ref(repo, solution)

        if not base:
            return ""

        head = self.try_git(
            repo,
            [
                "rev-parse",
                "--verify",
                "--quiet",
                "HEAD"
            ]
        )

        if not head:
            return ""

        committed_paths = {
            line.strip() for line in self.try_git(
                repo,
                [
                    "diff",
                    "--name-only",
                    f"{base}..{head}"
                ]
            ).splitlines() if line.strip()
        }

        if not committed_paths:
            return ""

        expected = {
            str(item).strip().replace("\\", "/")
            for item in (changed_files or []) if str(item).strip()
        }

        # An expected path list the committed range never touches means the
        # branch carries somebody else's work, not this solution's patch.
        if expected and not (expected & committed_paths):
            return ""

        return head

    # ==========================================================
    # DIFF
    # ==========================================================

    def get_diff(
        self,
        repo: Path,
        base: str = ""
    ) -> str:

        # HEAD~1 only describes the patch while the branch carries exactly
        # one commit. A branch an earlier run already committed, or one a
        # repair step added a second commit to, needs the recorded base
        # commit so the published diff is the whole contribution.
        # The committed range, not the working tree: Clash Fest's Go build
        # left its clash submodule dirty, and "Subproject commit ...-dirty"
        # reached the preview and the upstream check although the pushed
        # commit only touched two Kotlin files (2026-10-28).
        return self.run_git(
            repo,
            [
                "diff",
                "--ignore-submodules=dirty",
                base or "HEAD~1",
                "HEAD",
            ],
            keep_whitespace=True,
        )

    def get_uncommitted_diff(self, repo: Path) -> str:
        """Return an applied worktree patch without requiring a commit."""
        return self.run_git(repo, ["diff", "--no-ext-diff"], keep_whitespace=True)

    def uncommitted_patch_files(self, repo: Path, changed_files) -> list[str]:
        """Patch files still modified in the worktree after the commit.

        commit_changes stages only the solution's changed files, so a built
        edit the commit lacks used to show only in the working-tree diff.
        With the diff taken from the committed range, this keeps it visible.
        Submodules and files outside the patch are not looked at.
        """
        expected = {
            str(item).strip().replace("\\", "/")
            for item in changed_files or [] if str(item).strip()
        }
        if not expected:
            return []
        modified = self.try_git(
            repo, ["diff", "--name-only", "--ignore-submodules=all", "HEAD"]
        ).splitlines()
        return sorted(path.strip() for path in modified if path.strip() in expected)

    def review_notes(self, solution: dict) -> dict:
        """The behaviour review's notes and contract, for the PR metadata.

        The execution report carries the assumptions, warnings and
        human-decided publication points; the patch plan's publication
        blockers and the review contract are read from the run's solution
        file (fix_artifacts.recorded_review_notes).
        """
        from cognalys.llm.fix_artifacts import recorded_review_notes

        solutions_dir = self.execution_report_path.resolve().parent.parent / "error_solutions"
        # Stored under the analysed finding, whichever finding the pull
        # request is attributed to (attributed_view).
        analysed = analysed_identity(solution)
        recorded = recorded_review_notes(
            solutions_dir, str(solution.get("apk_name", "")),
            analysed["csv_class"], analysed["method_name"], analysed["spec"],
        )
        notes = {}
        for key in ("review_assumptions", "review_blockers", "publication_considerations",
                    "publication_blockers"):
            notes[key] = list(solution.get(key) or recorded.get(key) or [])
        # The execution-report row carries it (solution_applicator); a row
        # written before it did, or holding None, falls back to the run's files.
        row_version = solution.get("review_contract_version")
        notes["review_contract_version"] = (
            row_version if row_version is not None
            else recorded.get("review_contract_version"))
        # None when the solution file was not found: unknown, not "no review".
        notes["behavior_review_recorded"] = recorded.get("behavior_review_recorded")
        # The solution file's own scope first: the execution report defaults a
        # missing one to single_method (solution_applicator).
        notes["fix_scope"] = recorded.get("fix_scope") or solution.get("fix_scope", "")
        # Every publication blocker with its category and the review summary,
        # for publication_after_validation.
        notes["publication_blocker_record"] = (
            solution.get("publication_blocker_record")
            or recorded.get("publication_blocker_record") or {})
        # What the behaviour review of a repaired patch said
        # (validation.repair.record_repair_review), after the draft's own.
        repair_notes = solution.get("repair_review_notes") or {}
        for key in ("review_blockers", "publication_considerations"):
            # Items may be dicts (publication considerations), so not dict.fromkeys.
            notes[key] = [*notes[key], *(item for item in repair_notes.get(key) or []
                                         if item not in notes[key])]
        # A compatibility contract on a stored value nothing reads is settled
        # by searching for its readers (Your Local Weather, 2026-11-02).
        worktree = str(solution.get("worktree_path", "") or "").strip()
        if worktree:
            from cognalys.utils.persisted_value_readers import (
                discharge_compatibility_without_readers)
            notes = discharge_compatibility_without_readers(notes, Path(worktree), solution)
        return notes

    @staticmethod
    def elaborate_legacy_coordinated_summary(result: dict, style: str = "v1") -> str:
        """Replace the old checkpoint-assembly placeholder in PR metadata."""
        message = str(result.get("error_message", "")).strip()
        tool = str(result.get("tool", "")).strip()
        spec = str(result.get("spec", "")).strip()
        migration = result.get("migration_assessment", {}) or {}
        strategy = str(migration.get("safe_strategy", "")).strip()
        if not strategy and style in ("v2", "v3"):
            # migration_assessment.safe_strategy is empty for some real
            # solutions even though a detailed strategy exists nested under
            # compatibility_policy.case_migration_policy.strategy -- confirmed
            # against a real passwdsafe solution during investigation. Only
            # widen the lookup in v2 so v1 output stays byte-identical.
            strategy = str(
                (result.get("compatibility_policy", {}) or {})
                .get("case_migration_policy", {}).get("strategy", "")
            ).strip()
        edits = result.get("coordinated_edits", []) or []
        lines = finding_paragraph(
            tool, spec,
            short_symbol(result.get("csv_class", ""), result.get("method_name", "")),
            message, strategy,
        )
        if strategy:
            lines.extend([strategy, ""])
        reasons = {}
        for edit in edits:
            path = str(edit.get("source_file", "")).strip()
            reason = str(edit.get("reason", "")).strip()
            if path:
                reasons.setdefault(path, [])
                if reason and reason not in reasons[path]:
                    reasons[path].append(reason)
        lines.extend(changes_list(reasons))
        return plain_explanation("\n".join(lines))

    def joint_findings_without_effect(self, solution: dict) -> tuple[list, int]:
        """The unit's joint findings whose share of the patch has no effect, and how
        many joint findings it has (fix_artifacts.joint_without_effect_of).

        The draft's review and the review of a repaired patch
        (validation.repair.record_repair_review) together: review of
        2026-10-31, the repaired patch's verdict was read from neither."""
        recorded = {}
        try:
            from cognalys.llm.fix_artifacts import recorded_review_notes

            analysed = analysed_identity(solution)
            recorded = recorded_review_notes(
                self.execution_report_path.resolve().parent.parent / "error_solutions",
                str(solution.get("apk_name", "")), analysed["csv_class"],
                analysed["method_name"], analysed["spec"])
        except (AttributeError, OSError, TypeError):
            recorded = {}
        return (merged_joint_without_effect(recorded.get("joint_findings_without_effect"),
                                            solution.get("joint_findings_without_effect")),
                max(int(recorded.get("joint_finding_count") or 0),
                    int(solution.get("joint_finding_count") or 0)))

    def attributed(self, solution: dict) -> dict:
        """The execution-report row as its pull request presents it.

        LocalSend run_v4 (2026-10-31): the patch changed only the joint
        KeyStoreSpec call and the pull request was KeyPairSpec's. The
        behaviour review's pr_attribution (carried with the row, or read from
        the solution file) moves the pull request's spec, message, branch,
        text and pr-outcomes record to the findings the diff affects
        (pr_text.attributed_view); lookups of the draft's files keep the
        analysed identity (pr_text.analysed_identity).
        """
        attribution = solution.get("pr_attribution")
        if not attribution:
            try:
                from cognalys.llm.fix_artifacts import recorded_review_notes

                attribution = recorded_review_notes(
                    self.execution_report_path.resolve().parent.parent / "error_solutions",
                    str(solution.get("apk_name", "")), str(solution.get("csv_class", "")),
                    str(solution.get("method_name", "")), str(solution.get("spec", "")),
                ).get("pr_attribution")
            except (AttributeError, OSError, TypeError):
                attribution = None
        view = attributed_view(solution, attribution)
        if view is not solution:
            self.logger.info(
                "%s [%s] has no effect in its own patch; the pull request is attributed to "
                "%s [%s]", solution.get("method_name", ""), solution.get("spec", ""),
                view.get("method_name", ""), view.get("spec", ""))
        return view

    def text_problems_of(self, solution: dict, title: str, body: str, diff: str) -> list[str]:
        """Everything that keeps this pull request's text from being published."""
        without_effect, joint_count = self.joint_findings_without_effect(solution)
        effects = user_visible_effects(
            self.review_texts(solution, ("review_assumptions", "publication_considerations")))
        return (
            # A joint finding the patch leaves as it was is not resolved
            # (BlueGate run_v8, 2026-10-31); the text is rewritten.
            # A finding the pull request is not attributed to, the analysed
            # one included, is not claimed either (LocalSend run_v4).
            joint_finding_claims(body, without_effect, joint_count,
                                 str(solution.get("method_name", "")),
                                 str(solution.get("spec", ""))) +
            # The written-format fact is read from the final edits, so the
            # text is held to the diff that is published.
            text_problems(title, body, str(solution.get("error_message", "")),
                          new_written_format=new_written_format(
                              solution.get("coordinated_edits") or []),
                          earlier_data_still_reads=earlier_data_still_reads(
                              solution.get("coordinated_edits") or []),
                          # The rule ids of the finding and its joint ones;
                          # one the diff itself names is the API's name.
                          specs=finding_specs(solution), code=diff)
            + stale_names_paragraph_problems(body, solution.get("coordinated_edits") or [])
            # The review's notes to the reviewer are not the text a maintainer
            # reads (Your Local Weather, 2026-11-02). The sentence stating
            # an effect below restates a note's fact and is held only to the
            # note's wording (Weewx Weather, 2026-11-04).
            + review_text_problems(body, self.review_texts(solution), effects)
            # Why the change matters and what users will notice, when the
            # evidence and the review recorded them (primftpd, 2026-11-03).
            + rule_only_justification_problems(body, self.value_reasons(solution))
            + missing_effect_problems(body, effects)
            + [f"describes a change that is not in the diff: {reason}"
               for reason in edits_missing_from_diff(
                   solution.get("coordinated_edits") or [], diff)]
            # Set when the edits were ported to the base branch, and when
            # validation removed tests the patch created.
            + (["the summary was written for edits that changed after it "
                "was written; describe only the edits in the diff"]
               if solution.get("summary_stale") else [])
        )

    def recorded_evidence(self, solution: dict) -> dict:
        """The finding's evidence analysis, or {} when none is found."""
        from cognalys.llm.finding_adjudication import summary_error_from_solution
        from cognalys.llm.fix_artifacts import recorded_evidence_analysis

        try:
            evidence = summary_error_from_solution(solution).get("evidence_analysis") or {}
            if not evidence:
                # Stored under the analysed finding (review_notes).
                analysed = analysed_identity(solution)
                evidence = recorded_evidence_analysis(
                    self.execution_report_path.resolve().parent.parent / "error_solutions",
                    str(solution.get("apk_name", "")), analysed["csv_class"],
                    analysed["method_name"], analysed["spec"])
        except Exception:  # noqa: BLE001 -- no evidence only skips what reads it
            evidence = {}
        return evidence

    def value_reasons(self, solution: dict) -> list[str]:
        """Evidence statements saying why the reported value is a problem."""
        return value_problem_reasons(self.recorded_evidence(solution),
                                     str(solution.get("error_message", "")))

    def review_texts(self, solution: dict,
                     keys: tuple = ("review_assumptions", "publication_blockers",
                                    "publication_considerations")) -> list[str]:
        """The review's assumptions, blockers and considerations, as text."""
        try:
            notes = self.review_notes(solution)
        except Exception:  # noqa: BLE001 -- a missing report only skips this check
            notes = {}
        texts = []
        for key in keys:
            for item in notes.get(key) or solution.get(key) or []:
                texts.append(str(item.get("description", "")) if isinstance(item, dict)
                             else str(item))
        for item in ((notes.get("publication_blocker_record") or {}).get("blockers") or []
                     if "publication_blockers" in keys else []):
            if isinstance(item, dict):
                texts.append(str(item.get("description", "")))
        return [text for text in texts if text.strip()]

    def text_client(self):
        if not self.text_model.get("model"):
            return None
        if self._text_client is None:
            from cognalys.llm.llm_providers import remote_provider
            self._text_client = remote_provider(logger=self.logger, **self.text_model)
        return self._text_client

    def repair_text(self, solution: dict, default_title: str, diff: str,
                    title: str, body: str, problems: list[str],
                    attempts: int = 2) -> tuple[dict, str, str, list[str]]:
        """Write the summary again until the text passes its checks.

        A text that promises an edit the diff does not make (Grocy,
        2026-10-23), or that fails text_problems, is rewritten from the edits
        that are in the diff, with the problems it had. Returns the solution
        (its explanation and edits updated), title, body and what is still
        wrong; with no model client, the text is returned as it was.
        """
        client = self.text_client()
        if client is None or not problems:
            return solution, title, body, problems
        from cognalys.llm.finding_adjudication import (
            compose_summary_for_applied_edits, summary_error_from_solution)

        missing = set(edits_missing_from_diff(solution.get("coordinated_edits") or [], diff))
        edits = [edit for edit in solution.get("coordinated_edits") or []
                 if str(edit.get("reason") or edit.get("symbol") or "an edit") not in missing]
        repaired = {**solution, "coordinated_edits": edits, "summary_stale": False}
        for _ in range(attempts):
            explanation = compose_summary_for_applied_edits(
                client, self.logger, summary_error_from_solution(solution),
                [{"source_file_path": edit.get("source_file", ""),
                  "reason": edit.get("reason", ""),
                  "original_code": edit.get("original_code", ""),
                  "corrected_code": edit.get("corrected_code", "")} for edit in edits]
                or [{"source_file_path": solution.get("source_file", ""), "reason": "",
                     "original_code": solution.get("original_method_body", ""),
                     "corrected_code": solution.get("corrected_method_body", "")}],
                problems,
            )
            repaired["explanation"] = explanation
            title = self.descriptive_title(repaired, default_title)
            body = self.build_pr_body(repaired, diff)
            problems = self.text_problems_of(repaired, title, body, diff)
            if not problems:
                break
        self.logger.info("Rewrote the pull request text of %s%s",
                         solution.get("method_name", ""),
                         f"; still: {problems}" if problems else "")
        return repaired, title, body, problems

    @staticmethod
    def descriptive_title(result: dict, default_title: str) -> str:
        """Avoid titles such as 'Fix build' that describe only a method name."""
        # The maintainer summary's own title, when it wrote one that names
        # the change (pr_text.split_title_marker).
        proposed, _ = split_title_marker(str(result.get("explanation", "")))
        if proposed and not text_problems(proposed, "", specs=finding_specs(result)):
            return proposed[:MAX_TITLE_CHARACTERS]
        method = str(result.get("method_name", "")).strip()
        spec = str(result.get("spec", "")).strip()
        migration = result.get("migration_assessment", {}) or {}
        if (method.lower() in {"build", "create", "get", "set"}
                and migration.get("usage_kind") == "protocol"):
            return "Filter legacy TLS protocols without pinning TLS versions"
        # A deprecation finding names the API it is about, and the patch
        # replaces that API across the class and its callers. "Fix
        # doInBackground" (TowerCollector PR #284) names only the method the
        # detector attached the message to.
        message = str(result.get("error_message", ""))
        deprecated = re.search(
            r"\b(?:[a-z][a-z0-9_]*\.)+(?P<type>[A-Z][\w$]*)\b[^.]*\bdeprecat",
            message,
        )
        if deprecated:
            class_name = str(result.get("csv_class", "")).rsplit(".", 1)[-1].strip()
            return (
                f"Replace deprecated {deprecated.group('type')}"
                + (f" in {class_name}" if class_name else "")
            )
        # What the main production edit does, from its own reason.
        from_edits = title_from_edit_reasons(result.get("coordinated_edits") or [])
        if from_edits:
            return from_edits
        if method.lower() in {"build", "create", "get", "set"} and spec:
            return f"Address {spec} finding in {method}"
        # A JVM initializer name reads as "Fix <init>" (crazyflie-android-client
        # PR #103), which says nothing about what changed; name its class.
        initializer = {"<init>": "constructor", "<clinit>": "static initializer"}
        if method in initializer:
            class_name = str(result.get("csv_class", "")).rsplit(".", 1)[-1]
            class_name = class_name.split("$")[-1].strip()
            if class_name:
                subject = f"{class_name} {initializer[method]}"
                return f"Address {spec} finding in {subject}" if spec else f"Fix {subject}"
        return default_title

        # ==========================================================
        # PR BODY
        # ==========================================================
    @staticmethod
    def build_never_ran(build_status: str) -> bool:
        """Whether the build was stopped before it could compile the patch.

        The difference between this and a failure is the whole safety of the
        publish-without-validation approval: a patch that did not compile
        must never reach a maintainer, however explicitly its publication
        was approved.
        """
        return str(build_status).lower() in BUILD_NEVER_RAN_STATUSES

    @staticmethod
    def build_validation_blocks_publication(build_status: str) -> bool:
        """Whether this build result withholds the branch, commit and PR.

        This was written inline as `build_status != "success"`, which rejected
        the narrower "success_app_module_only" that validate_applied_solutions
        records and pr_creator documents accepting. Since a blocked validation
        skips the branch and the commit below it, the mismatch left such a
        finding with no commit hash -- and pr_creator's explicit-approval path
        accepts the status and then demands a hash, so it could never be
        reached. hanjoongcho/aaf-easydiary's Zip Slip fix stayed uncommitted in
        its worktree because an unselected gmsDev flavor is missing two
        drawable resources that have nothing to do with the patch.

        Named rather than inlined because the same rule is read in three other
        modules, and writing it out a fourth time is how it drifted.
        """
        return str(build_status).lower() not in (
            BUILD_VALIDATION_PASSING_STATUSES
        )

    def build_pr_body(
    self,
    result: dict,
    diff_text: str
) -> str:

        explanation = result.get(
            "explanation",
            "This method was changed in order to minimize the errors with the existing code."
        )
        _, explanation = split_title_marker(str(explanation))
        if str(explanation).strip() == (
            "Coordinated fix assembled from persisted per-file drafts."
        ):
            explanation = self.elaborate_legacy_coordinated_summary(
                result, style=self.style
            )

        method_name = str(result.get("method_name", "")).strip()
        class_name = str(result.get("csv_class", "")).strip()
        source_file = str(result.get("source_file", "")).strip()
        spec = str(result.get("spec", "")).strip()
        tool = str(result.get("tool", "")).strip()
        repository_guidance = result.get("repository_guidance", []) or []
        compatibility_policy = result.get("compatibility_policy", {}) or {}
        digest_contract = result.get("digest_contract_assessment", {}) or {}
        build_validation = result.get("build_validation", {}) or {}
        migration_assessment = result.get("migration_assessment", {}) or {}
        usage_kind = str(migration_assessment.get("usage_kind", ""))
        coordinated_edits = result.get("coordinated_edits", [])
        test_files = sorted({
            str(item.get("source_file", "")).strip()
            for item in coordinated_edits if isinstance(item, dict)
            and (item.get("edit_kind") == "test"
                 or "test" in str(item.get("source_file", "")).lower())
        })
        build_status = build_validation.get("status", "not_run")
        build_task = build_validation.get("task", "")
        task_suffix = f" (`{build_task}`)" if build_task else ""
        validation_passed = build_status in BUILD_VALIDATION_PASSING_STATUSES
        if build_status == "success":
            compilation_line = (
                f"- [x] Project compiles successfully{task_suffix}"
            )
        elif build_status == "success_app_module_only":
            # A maintainer reads this line as a claim about their project, so
            # it states exactly what ran. Reusing the sentence above would
            # assert a full build that did not happen.
            compilation_line = (
                f"- [x] The patched module compiles successfully{task_suffix}; "
                "the full multi-module build was not completed in this "
                "environment"
            )
        elif build_status == COMPILE_ONLY_PASS:
            # The project's only module compiled; no APK was assembled.
            compilation_line = (
                f"- [x] The project compiles successfully{task_suffix}; the APK "
                "was not assembled in this environment"
            )
        elif build_status == "success_on_analyzed_revision":
            # The maintainer's own branch did not build here even without the
            # change; say so, and say where the change was compiled instead.
            analyzed = str(build_validation.get("analyzed_revision", ""))[:10]
            compilation_line = (
                f"- [x] The change compiles on {analyzed or 'the analysed revision'}"
                f"{task_suffix}; the current base branch fails to build in this "
                "environment with or without this change"
            )
        elif build_status == PRODUCTION_ONLY_TEST_BUILD_PREEXISTING:
            # Only the production build passed; a test build fails on the base
            # branch before any test compiles. A patch test in another module
            # may still have compiled and run, so the line speaks only of the
            # failing test build, never of tests in general.
            compilation_line = (
                f"- [x] The app compiles successfully with this change{task_suffix}; "
                "the test build fails on the base branch with or without it, "
                "so it could not be used to check this change"
            )
        elif build_status in {"failed", "timeout"}:
            # Keep the failure in internal JSON reports, not in the public PR.
            compilation_line = None
        elif (
            str(result.get("finding_id", "")).strip()
            in self.publish_without_build_validation
            and self.build_never_ran(build_status)
        ):
            # Silence here would let a maintainer assume the usual local
            # build happened. It did not, and they are the ones who will
            # find out, so the PR says so in its own voice.
            compilation_line = (
                "- [ ] I was not able to build this project locally, so the "
                "change is unverified by compilation on my side"
            )
        else:
            compilation_line = None

        # A CSV row's tool column can name how the row was gathered rather
        # than an analyzer ("web_research", "static:manual"); named as the
        # tool it tells a maintainer something false about the finding.
        if not is_analyzer_tool(tool):
            tool = ""

        # The finding's location, said once and only when the summary does not
        # already say it. The summary is what a person reads; a table of the
        # same identifiers above it was noise.
        summary = plain_explanation(str(explanation))
        hidden_values = {
            "unknown", "unknownclass", "unknownfile", "unknown_method"
        }
        location = ""
        # Source names, not bytecode (pr_text.source_names): SMSecure #89
        # read "Affects `ConversationListAdapter.<init>`" (2026-10-31).
        shown_class = "" if class_name.lower() in hidden_values else class_name
        shown_method = "" if method_name.lower() in hidden_values else method_name
        owner, member, _kind = source_names(shown_class, shown_method)
        named = [
            value for value in (member or owner.rsplit(".", 1)[-1], source_file)
            if value and value.lower() not in hidden_values
        ]
        if named and not any(value in summary for value in named):
            phrase = symbol_phrase(shown_class, shown_method)
            parts = [value for value in (phrase, f"`{source_file}`" if source_file else "")
                     if value]
            location = "Affects " + " in ".join(parts) + "."

        # Build at column zero instead of relying on textwrap.dedent(). An LLM
        # explanation can contain an unindented second paragraph, which makes
        # dedent preserve the template indentation and breaks Markdown.
        # Only a tool that reported the finding has anything to explain; the
        # rule's name alone, for a candidate read from the code, is noise.
        glossary = (
            tool_and_spec_context(tool, spec)
            if self.style == "v2" and tool else ""
        )
        # What the evidence established, in sentences the evidence step wrote
        # (hosts checked, why a root is bundled, what the client already
        # enforces). Stated as written, not left for the summary model to
        # remember or reword, and only those the final diff bears out
        # (migration_policy.facts_supported_by_diff).
        from cognalys.llm.migration_policy import facts_supported_by_diff

        facts = " ".join(facts_supported_by_diff(
            compatibility_policy.get("case_migration_policy", {}) or {}, coordinated_edits))
        # Names the patch keeps that still name the replaced value
        # (`model16MD5` holding a SHA-256): said, so the diff is not read
        # as a mistake.
        stale_names = stale_value_names_note(
            str(result.get("error_message", "")), coordinated_edits)
        # A decision left to the maintainer (an algorithm above minSdk kept on
        # the last attempt) is said in the body, not only in the operator's
        # publication notes (Your Local Weather, 2026-11-02).
        from cognalys.validation.android_api_levels import maintainer_notes

        kept_warnings = [*(result.get("review_blockers") or []),
                         *((result.get("semantic_validation") or {}).get("warnings") or [])]
        if not kept_warnings:
            try:
                kept_warnings = list(self.review_notes(result).get("review_blockers") or [])
            except Exception:  # noqa: BLE001 -- without the report there is no note
                kept_warnings = []
        api_notes = " ".join(maintainer_notes(kept_warnings, diff_text))
        facts = " ".join(part for part in (facts, stale_names, api_notes) if part)
        if self.style == "v3":
            return self.plain_pr_body(
                summary, location, facts, tool, test_files, build_status,
                build_task, build_validation, usage_kind, compatibility_policy,
                digest_contract, repository_guidance,
            )
        body_parts = [
            "## Summary",
            "",
            *([location, ""] if location else []),
            summary,
            *(["", facts] if facts else []),
            *(["", glossary] if glossary else []),
            *(
                [
                    "",
                    "## Compatibility note",
                    "",
                    "This change updates an identifier used only for "
                    "derived, regenerable cache entries. Existing entries "
                    "created with the previous identifier may become cache "
                    "misses and be regenerated. The change is not intended "
                    "to invalidate user-owned source data, credentials, "
                    "preferences, or offline-only data. Legacy cache files "
                    "may remain until the project's normal cache cleanup.",
                ]
                if (usage_kind == "cache"
                    and compatibility_policy.get("allow_cache_invalidation"))
                else []
            ),
            *(
                [
                    "",
                    "## Backend migration prerequisite",
                    "",
                    str(digest_contract.get("pull_request_note", "")).strip(),
                    "",
                    "This change must be coordinated with the digest producer. "
                    "Deploy the backend compatibility change before enabling the "
                    "stronger digest in clients, retain the legacy value during the "
                    "transition, and remove it only after older clients are retired.",
                ]
                if str(digest_contract.get("pull_request_note", "")).strip()
                else []
            ),
            "",
            "## How was this tested?",
            "",
            (
                f"- [x] Automated test coverage added in {len(test_files)} test file(s)"
                # A statement, not a checkbox: unchecked, it read as a task
                # the contributor had left undone.
                if test_files else "- No automated test was added in this change"
            ),
            *(
                [compilation_line]
                if validation_passed else [
                    "- [ ] Build validation has not completed successfully "
                    "(local preview only)"
                ]
            ),
            # No "Before merging" section: the adjudicator's open questions
            # (publication_considerations) stay in the JSON for the operator.
        ]

        # required_tests, review_assumptions and review_blockers are not
        # rendered for the maintainer. The PR JSON carries them, and the
        # preview lists the ones a person decides above the approval flag
        # (generate_github_previews.operator_notes).

        guidance_text = "\n".join(
            str(item.get("content", "")) for item in repository_guidance
            if isinstance(item, dict)
        ).lower()
        if "ai assistance" in guidance_text or "ai use" in guidance_text:
            body_parts.extend([
                "", "## AI assistance", "",
                "- [x] AI assistance was used for this contribution.",
                "- [ ] Apply the repository's required AI-assistance label.",
            ])

        if self.style == "v2":
            body_parts.extend([
                "", "---", "",
                "*I'm an undergraduate Computer Engineering student at the "
                "University of Brasília (UnB); this contribution is part of "
                "a research project on the analysis of Android apps, and I'm "
                "happy to adjust it to match the project's conventions.*",
            ])
        else:
            body_parts.extend([
                "", "## Additional context", "",
                "I’m an undergraduate Computer Engineering student at the "
                "University of Brasília (UnB), and this contribution is part of "
                "a research project involving the analysis of Android apps.", "",
                "I’m happy to adjust the implementation to better match the "
                "project’s architecture, coding conventions, or maintainers’ "
                "recommendations.",
            ])

        body = "\n".join(body_parts)

        return body

    @staticmethod
    def plain_pr_body(
        summary: str, location: str, facts: str, tool: str, test_files: list,
        build_status: str, build_task: str, build_validation: dict,
        usage_kind: str, compatibility_policy: dict, digest_contract: dict,
        repository_guidance: list,
    ) -> str:
        """The v3 pull request body: plain paragraphs, as a person writes one.

        No headings, checkboxes or "How was this tested?" form (2026-10-22).
        A build that passed is said in one sentence; one that failed or never
        ran is not mentioned, since the maintainer tests the change anyway and
        a line about a build that did not happen only adds noise.
        """
        task = f" (`{build_task}`)" if build_task else ""
        if build_status == "success":
            built = f"The project builds with this change{task}."
        elif build_status == "success_app_module_only":
            built = f"The changed module builds with this change{task}."
        elif build_status == COMPILE_ONLY_PASS:
            # Compiled, not packaged: "builds" would claim an APK.
            built = f"The project compiles with this change{task}."
        elif build_status == "success_on_analyzed_revision":
            analyzed = str(build_validation.get("analyzed_revision", ""))[:10]
            built = (
                f"It builds on {analyzed or 'the revision I analysed'}{task}; the "
                "current branch doesn't build in my environment even without "
                "this change."
            )
        elif build_status == PRODUCTION_ONLY_TEST_BUILD_PREEXISTING:
            # Production code only: the failing test build checked nothing.
            # A kept patch test may have run elsewhere, so no claim about
            # tests in general.
            built = (
                f"The app builds with this change{task}. Its test build already "
                "fails on the current branch without it, so that build couldn't "
                "check the change."
            )
        else:
            built = ""
        tested = ""
        if test_files:
            names = ", ".join(f"`{name.rsplit('/', 1)[-1]}`" for name in test_files)
            tested = f"I also added a test for it ({names})."
        note = str(digest_contract.get("pull_request_note", "")).strip()
        guidance = "\n".join(
            str(item.get("content", "")) for item in repository_guidance
            if isinstance(item, dict)
        ).lower()
        paragraphs = [
            location,
            summary,
            facts,
            "I found this while running the app under RVSec, a runtime checker "
            "for how apps use the Java crypto and TLS APIs."
            if is_rvsec_finding(tool) else "",
            "Existing cache entries made with the old key will simply be "
            "regenerated; no user data is affected."
            if usage_kind == "cache" and compatibility_policy.get(
                "allow_cache_invalidation") else "",
            (note + " The backend has to accept the new value before clients "
             "send it, and keep the old one until older clients are gone.")
            if note else "",
            " ".join(part for part in (tested, built) if part),
            # Only where the repository's own guidance asks for it.
            "AI assistance was used for this contribution."
            if "ai assistance" in guidance or "ai use" in guidance else "",
            "I'm a Computer Engineering student at the University of Brasília "
            "(UnB), working on a research project on Android app analysis. "
            "Happy to adjust this to the project's conventions.",
        ]
        return "\n\n".join(item.strip() for item in paragraphs if item and item.strip())


    def generate_prs(self):

        with open(
            self.execution_report_path,
            encoding="utf-8"
        ) as f:

            report = json.load(f)

        results = report.get(
            "per_solution_details",
            []
        )
        if self.project:
            results = [
                result for result in results
                if result.get("apk_name") == self.project
            ]

        self.logger.info(
            f"Loaded {len(results)} solutions"
        )

        generated = 0
        failed = 0
        written: list = []

        for solution in results:

            if solution.get("status") != "SUCCESS":
                continue
            solution = self.attributed(solution)

            review_only = not FindingDisposition.from_metadata(
                solution
            ).publishable
            if review_only:
                self.logger.info(
                    "Generating a non-publishable PR preview for review-only "
                    "solution %s",
                    solution.get("apk_name", "unknown"),
                )

            build_status = str(
                solution.get("build_validation", {}).get("status", "")
            ).lower()
            validation_blocked = self.build_validation_blocks_publication(
                build_status
            )
            uncompiled = files_the_build_does_not_compile(solution.get("changed_files") or [])
            if uncompiled and not validation_blocked:
                # Amethyst#4642: the patch changed the desktop (jvmMain)
                # actual; assembleFdroidDebug never compiled it, and the pull
                # request still said "The project builds with this change".
                self.logger.warning(
                    "%s: the build compiled none of %s (source sets an Android APK "
                    "does not include); it says nothing about this patch",
                    solution.get("apk_name", "unknown"), ", ".join(uncompiled),
                )
                validation_blocked = True
                solution = {**solution, "build_validation": {
                    **(solution.get("build_validation") or {}),
                    "status": "not_covering_patch",
                    "reason": "changed files outside the compiled source sets: "
                              + ", ".join(uncompiled)}}
            finding_id = str(solution.get("finding_id", "")).strip()
            unvalidated_approval = (
                validation_blocked
                and finding_id in self.publish_without_build_validation
                and build_says_nothing_about_patch(solution.get("build_validation"))
            )
            if unvalidated_approval:
                validation_blocked = False
                self.logger.warning(
                    "PUBLISHING %s WITHOUT COMPILE VALIDATION: a human "
                    "approved finding %s explicitly and the build never ran "
                    "or fails the same way without the patch "
                    "(status=%s), so nothing here establishes that this "
                    "patch compiles. The maintainer's CI is the first build "
                    "it will see.",
                    solution.get("apk_name", "unknown"), finding_id,
                    build_status or "not_run",
                )
            elif (
                validation_blocked
                and finding_id in self.publish_without_build_validation
            ):
                # Named, but the build did run and the patch is what failed.
                self.logger.error(
                    "Refusing the publish-without-validation approval for "
                    "%s: build status %s means the patch itself did not "
                    "compile, which no approval may override",
                    finding_id, build_status or "not_run",
                )
            if validation_blocked:
                self.logger.warning(
                    "Generating a local, non-publishable PR preview for %s: "
                    "successful isolated build validation is still required "
                    "(status=%s)",
                    solution.get("apk_name", "unknown"),
                    build_status or "not_run",
                )

            if self.max_prs > 0 and generated >= self.max_prs:
                break

            source_file = self.resolve_source_file(report, solution)

            repo_path = self.find_git_repo(
                source_file
            )

            if not repo_path:

                self.logger.warning(
                    f"No git repository for {source_file}"
                )
                failed += 1
                continue

            repository_metadata = self.get_repository_metadata(
                repo_path
            )

            public_solution = dict(solution)
            public_solution["source_file"] = self.public_source_file(
                repo_path, source_file
            )

            method_name = solution["method_name"]

            branch_name = str(solution.get("branch", "")).strip()
            if not branch_name:
                branch_name = self.build_branch_name(report, solution)
            formatting: dict = {}

            try:

                # ==========================================
                # BRANCH
                # ==========================================

                if not self.dry_run and not validation_blocked:

                    self.create_branch(
                        repo_path,
                        branch_name
                    )

                # ==========================================
                # COMMIT
                # ==========================================

                pr_title = self.descriptive_title(
                    solution,
                    self.title_template.format(
                        method_name=method_name,
                        class_name=solution.get("csv_class", ""),
                        apk_name=solution.get("apk_name", "")
                    ),
                )
                # "Fix onCreateWindow" (AI Hub) told the maintainer nothing;
                # the commit says what the pull request says. A template
                # given on the command line is still honoured.
                commit_message = (
                    pr_title if self.commit_template == DEFAULT_COMMIT_TEMPLATE
                    else self.commit_template.format(
                        method_name=method_name,
                        class_name=solution.get("csv_class", ""),
                        apk_name=solution.get("apk_name", "")
                    )
                )

                if self.dry_run:

                    commit_hash = "DRY_RUN"

                elif validation_blocked:

                    # The solution has already been applied to its isolated
                    # worktree. Keep it uncommitted until validation succeeds,
                    # but still emit complete metadata for human inspection.
                    commit_hash = ""

                else:

                    changed_files = [
                        str(path) for path in solution.get(
                            "changed_files", []
                        )
                    ]

                    formatting = self.format_with_repository_formatter(
                        repo_path, solution, changed_files)

                    commit_hash = self.commit_changes(
                        repo_path,
                        commit_message,
                        changed_files=changed_files,
                    )

                    if not commit_hash:

                        commit_hash = self.find_existing_fix_commit(
                            repo_path, solution, changed_files
                        )

                        if commit_hash:
                            self.logger.info(
                                "Reusing commit %s that an earlier run "
                                "already created on %s for %s",
                                commit_hash[:10], branch_name, method_name,
                            )

                if not commit_hash and not validation_blocked:

                    self.logger.warning(
                        f"No commit created for {method_name}"
                    )
                    failed += 1
                    continue

                # ==========================================
                # DIFF
                # ==========================================

                if validation_blocked and not self.dry_run:
                    diff_text = self.get_uncommitted_diff(repo_path)
                elif self.dry_run and solution.get("coordinated_edits"):
                    edit_diffs = []
                    for edit in solution["coordinated_edits"]:
                        edit_path = str(edit.get("source_file", "source"))
                        edit_diffs.extend(difflib.unified_diff(
                            str(edit.get("original_code", "")).splitlines(),
                            str(edit.get("corrected_code", "")).splitlines(),
                            fromfile=f"a/{edit_path}",
                            tofile=f"b/{edit_path}",
                            lineterm="",
                        ))
                    diff_text = "\n".join(edit_diffs)
                elif self.dry_run:
                    diff_text = "\n".join(difflib.unified_diff(
                        str(solution.get("original_method_body", "")).splitlines(),
                        str(solution.get("corrected_method_body", "")).splitlines(),
                        fromfile=f"a/{public_solution.get('source_file', 'method')}",
                        tofile=f"b/{public_solution.get('source_file', 'method')}",
                        lineterm="",
                    ))
                else:
                    diff_text = self.get_diff(
                        repo_path, self.diff_base_ref(repo_path, solution)
                    )
                uncommitted = (
                    self.uncommitted_patch_files(repo_path, solution.get("changed_files") or [])
                    if commit_hash and commit_hash != "DRY_RUN" else []
                )

                # ==========================================
                # PR BODY
                # ==========================================

                pr_body = self.build_pr_body(
                    public_solution,
                    diff_text
                )

                problems = self.text_problems_of(solution, pr_title, pr_body, diff_text)
                if problems:
                    default_title = self.title_template.format(
                        method_name=method_name,
                        class_name=solution.get("csv_class", ""),
                        apk_name=solution.get("apk_name", ""),
                    )
                    repaired, new_title, pr_body, problems = self.repair_text(
                        public_solution, default_title, diff_text,
                        pr_title, pr_body, problems,
                    )
                    # The rewritten summary and its freshness travel with the
                    # edits: keeping summary_stale from before the rewrite made
                    # the template re-check below report the stale summary
                    # again and hold every draft a repair widened, ported or
                    # stripped of its generated tests in a repository with a
                    # pull-request template (review of 2026-10-31).
                    solution = {**solution,
                                "coordinated_edits": repaired.get("coordinated_edits", []),
                                "explanation": repaired.get("explanation",
                                                            solution.get("explanation", "")),
                                "summary_stale": repaired.get("summary_stale",
                                                              solution.get("summary_stale"))}
                    if (new_title != pr_title and commit_hash and commit_hash != "DRY_RUN"
                            and self.commit_template == DEFAULT_COMMIT_TEMPLATE):
                        # Only the local commit, not yet pushed: it says what
                        # the pull request says.
                        self.run_git(repo_path, ["commit", "--amend", "--signoff", "-m", new_title])
                        commit_hash = self.run_git(repo_path, ["rev-parse", "HEAD"])
                    pr_title = new_title

                # The repository's own pull-request form, when it has one
                # (NotallyX#1167, 2026-10-30: CodeRabbit failed the plain
                # body against .github/pull_request_template.md). The plain
                # text goes under its description; the checks run again on
                # the filled body, which is what the maintainer reads.
                plain_body = pr_body
                template = template_in_checkout(repo_path)
                pr_body, template_notes = body_for_repository(
                    template, plain_body, facts_from_pull_request(solution, diff_text))
                if template:
                    # Only the pipeline's own words can hold the PR: the
                    # template's preamble is the maintainers' text and no
                    # rewrite could remove it (thunderbird-android's put the
                    # filled body at 376 words of "summary", 2026-10-30).
                    problems = problems_in_own_words(
                        lambda body: self.text_problems_of(solution, pr_title, body, diff_text),
                        pr_body, plain_body)
                    # Except a line the template asks the author to replace,
                    # left as it was (SMSecure run_v4, 2026-10-31: "Describe
                    # how this was tested."): the fill drops such lines, so
                    # one left is a description that copied the form.
                    instructions = template_instructions_left(pr_body, template.text)
                    if instructions and template_instructions_left(plain_body, template.text):
                        # The title stays: the commit already carries it.
                        repaired, _title, plain_body, _still = self.repair_text(
                            {**public_solution, **{key: solution[key] for key in (
                                "coordinated_edits", "explanation") if key in solution}},
                            pr_title, diff_text, pr_title, plain_body, instructions)
                        solution = {**solution, "explanation": repaired.get(
                            "explanation", solution.get("explanation", ""))}
                        pr_body, template_notes = body_for_repository(
                            template, plain_body, facts_from_pull_request(solution, diff_text))
                        problems = problems_in_own_words(
                            lambda body: self.text_problems_of(solution, pr_title, body,
                                                               diff_text),
                            pr_body, plain_body)
                        instructions = template_instructions_left(pr_body, template.text)
                    problems = list(dict.fromkeys([*problems, *instructions]))

                # The publication verdict once the patch exists: committed,
                # built, reviewed and its text checked. A review-only draft
                # it clears reads READY_FOR_PUBLICATION_WITH_NOTES; the
                # solution record keeps its own publication_allowed, and
                # pr_creator still asks for the finding's explicit approval.
                notes = self.review_notes(solution)
                # Blockers only about a finding the patch left without effect
                # (the analysed one when the pull request was re-attributed,
                # or a joint one) do not hold the findings it does change
                # (LocalSend run_v5, 2026-10-31).
                without_effect, _count = self.joint_findings_without_effect(solution)
                notes, dropped_with_findings = without_blockers_of_findings_without_effect(
                    notes,
                    [str(item.get("specification", "")) for item in without_effect
                     if isinstance(item, dict)],
                    [str(solution.get("spec", ""))] + [
                        str(item.get("specification") or item.get("spec") or "")
                        for item in solution.get("joint_findings") or []
                        if isinstance(item, dict)
                        and not joint_finding_without_effect(item, without_effect)])
                if dropped_with_findings:
                    notes["publication_blockers_of_findings_without_effect"] = (
                        dropped_with_findings)
                verdict = publication_after_validation({
                    **solution, **notes,
                    "commit_hash": commit_hash if not self.dry_run else "",
                    "text_problems": problems,
                    "uncommitted_patch_files": uncommitted,
                })
                # The repaired patch's own review holds every draft, publishable
                # or review-only (disposition.repaired_patch_review_blocks).
                repair_blocks = repaired_patch_review_blocks(solution)
                promoted = (review_only and not validation_blocked and verdict["publishable"]
                            and not repair_blocks)
                publishable = ((not review_only and not validation_blocked) or promoted)                     and not repair_blocks
                notes["publication_blockers"] = [
                    line for line in notes.get("publication_blockers") or []
                    if line not in verdict["dropped_plan_blockers"]]
                self.logger.info(
                    "%s: fix_scope %s, %s%s", method_name, notes.get("fix_scope") or "unknown",
                    "publishable" if publishable else "not publishable",
                    (" (review-only draft kept by the verdict after validation; what it could not check is left as notes)" if promoted
                     else f" ({'; '.join(verdict['blocks'][:3])})"
                     if review_only and verdict["blocks"] else ""))

                pr_json = {

                    "repository":
                        Path(os.path.relpath(repo_path, Path.cwd())).as_posix(),

                    "remote_name":
                        repository_metadata["remote_name"],

                    "remote_url":
                        repository_metadata["remote_url"],

                    "repo_host":
                        repository_metadata["repo_host"],

                    "repo_owner":
                        repository_metadata["repo_owner"],

                    "repo_name":
                        repository_metadata["repo_name"],

                    "branch":
                        branch_name,

                    "base_branch":
                        solution.get("base_branch", ""),

                    "base_commit":
                        solution.get("base_commit", ""),

                    "finding_id":
                        solution.get("finding_id", ""),

                    "solution_revision":
                        solution.get("solution_revision", ""),

                    "commit_hash":
                        commit_hash,

                    "source_file":
                        public_solution.get("source_file", ""),

                    "spec":
                        solution.get("spec", ""),

                    "method_name":
                        method_name,

                    "csv_class":
                        solution.get("csv_class", ""),

                    "error_message":
                        solution.get("error_message", ""),

                    # The finding the draft was analysed and stored under,
                    # when the pull request is attributed to another one of
                    # its unit (LocalSend run_v4, 2026-10-31); every lookup
                    # of the draft's files reads it (fix_artifacts.
                    # with_recorded_review_notes, finding_statistics).
                    **({"analysed_finding": solution["analysed_finding"]}
                       if solution.get("analysed_finding") else {}),

                    "tool":
                        solution.get("tool", ""),

                    "build_validation":
                        solution.get("build_validation", {}),

                    "manual_review_required":
                        solution.get("manual_review_required", False),

                    "auto_apply": solution.get("auto_apply", True),

                    "publication_allowed":
                        solution.get("publication_allowed", True),

                    "publishable": publishable,

                    "preview_status": (
                        "BUILD_VALIDATION_BLOCKED"
                        if validation_blocked else
                        "REVIEW_ONLY_NOT_PUBLISHABLE" if repair_blocks else (
                            "READY_FOR_PUBLICATION_WITH_NOTES" if promoted else
                            "REVIEW_ONLY_NOT_PUBLISHABLE"
                            if review_only else "READY_FOR_PUBLICATION"
                        )
                    ),

                    # What the verdict after validation decided, for the
                    # operator: a promoted draft's notes, and what keeps
                    # any other review-only draft back.
                    "publication_promoted_after_validation": promoted,
                    "publication_notes": verdict["notes"],
                    "publication_verdict_blocks": verdict["blocks"],
                    # The behaviour review of a patch the build repair carried
                    # beyond the plan, and what it holds back; pr_creator and
                    # the statistics refuse the draft on these. None when no
                    # repair went beyond the plan: an empty review read as one
                    # that never ran (BlueGate run_v7, 2026-10-31).
                    "repair_behavior_review": solution.get("repair_behavior_review") or None,
                    "repaired_patch_review_blocks": repair_blocks,

                    # The decision as the final diff shows it; display only.
                    "decision_label": decision_label(
                        solution.get("decision", ""), solution.get("changed_files") or []),

                    # The adjudication's security-impact class, for the PR
                    # survey (prs.impact_class); report only.
                    "impact_class": impact_class(
                        adjudication_of(self.recorded_evidence(solution))),

                    "original_method_body":
                        solution.get("original_method_body", ""),

                    "corrected_method_body":
                        solution.get("corrected_method_body", ""),

                    "coordinated_edits":
                        solution.get("coordinated_edits", []),

                    "changed_files": solution.get("changed_files", []),

                    "diff": diff_text,

                    "title": pr_title,

                    "body":
                        pr_body,

                    # The text before it filled the repository's template,
                    # so the form can be filled again (pr_creator
                    # --update-description), and what the form asked that
                    # the operator answers.
                    "plain_body": plain_body,
                    "pull_request_template": {
                        "path": template.path,
                        "notes": template_notes,
                    },

                    # The repository's rule on AI-assisted contributions, and
                    # whether submitting affirms that none was used (App
                    # Manager, 2026-11-01); the preview and pr_creator print
                    # it as a warning. A note, never a blocker.
                    "ai_policy": ai_policy({
                        **policy_files_in_checkout(repo_path),
                        **({template.path: template.text} if template else {})}),

                    # What still reads like the pipeline rather than a
                    # person; pr_creator refuses to publish while it is
                    # not empty (see utils.pr_text.text_problems).
                    "text_problems": problems,

                    # Files of the patch whose built edit the commit lacks;
                    # the diff above shows only what is committed.
                    "uncommitted_patch_files": uncommitted,

                    # What the repository's own formatter changed in the
                    # patch before the commit, or why it did not run.
                    "repository_formatting": formatting,

                    "dry_run":
                        self.dry_run,

                    # For the operator, never the maintainer: what the
                    # preview lists before the approval flag, and the
                    # review contract pr_creator checks at approval.
                    **notes,
                }

                # Recorded so the preview can say so before anyone approves
                # a second pull request for a finding that already has one.
                # pr_creator makes the authoritative (live) check.
                # The state pr-outcomes recorded decides offline: a creation
                # record always says "open", so a merged one (ANOTHERpass
                # #103) would otherwise read as still open.
                existing_pull_request = open_pull_request_for(
                    pr_json, publication_search_root(self.output_dir),
                    live_state=recorded_pull_request_state,
                    run_root=self.output_dir.resolve().parent.parent,
                )
                pr_json["existing_open_pull_request"] = (
                    existing_pull_request["pull_request"]
                    if existing_pull_request else ""
                )
                if existing_pull_request:
                    self.logger.warning(
                        "%s already has a pull request that may still be "
                        "open (%s); publishing this revision would duplicate "
                        "it", method_name, existing_pull_request["pull_request"],
                    )

                # Named after the analysed finding, as the retrospective and
                # every earlier run name it.
                output_file = self.output_dir / (
                    finding_artifact_stem(
                        solution.get("apk_name", "unknown_apk"),
                        analysed_identity(solution)["method_name"],
                        analysed_identity(solution)["spec"],
                    ) + "_pr.json"
                )

                with open(
                    output_file,
                    "w",
                    encoding="utf-8"
                ) as f:

                    json.dump(
                        pr_json,
                        f,
                        indent=2
                    )

                self.logger.info(
                    f"PR generated: {output_file}"
                )
                written.append((output_file, pr_json))

                generated += 1

            except Exception as e:
                failed += 1

                self.logger.exception(
                    f"PR generation failed: {e}"
                )

        self.record_related_pull_requests(written)
        self.logger.info(
            f"PR generation summary: {generated} generated, {failed} failed"
        )
        return {"generated": generated, "failed": failed}

    def record_related_pull_requests(self, written: list) -> None:
        """Mark the pull requests of this run that repeat or collide with another.

        A superseded one is not publishable: another pull request already
        makes every change it makes (AI Hub's updateWebViewSettings inside
        onCreateWindow). Overlapping ones stay publishable, and pr_creator
        refuses the second of them; see prs.pr_grouping.
        """
        relations = relate([pr for _, pr in written])
        for output_file, pr_json in written:
            relation = relations.get(str(pr_json.get("finding_id", "")))
            if not relation:
                continue
            pr_json.update(relation)
            if relation.get("superseded_by"):
                pr_json["publishable"] = False
                pr_json["preview_status"] = "SUPERSEDED"
                self.logger.info(
                    "%s is superseded by finding %s, whose pull request makes "
                    "every change it makes", output_file.name, relation["superseded_by"],
                )
            elif relation.get("resolved_with"):
                pr_json["publishable"] = False
                pr_json["preview_status"] = "RESOLVED_WITH"
                self.logger.info(
                    "%s is resolved with finding %s (%s), whose pull request "
                    "already makes this finding's change", output_file.name,
                    relation["resolved_with"], relation.get("resolved_with_method", ""),
                )
            elif relation.get("overlaps_with"):
                self.logger.warning(
                    "%s overlaps findings %s (same method or lines); only one "
                    "of them should be published", output_file.name,
                    ", ".join(relation["overlaps_with"]),
                )
            output_file.write_text(json.dumps(pr_json, indent=2), encoding="utf-8")
