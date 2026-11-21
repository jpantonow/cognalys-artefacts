# solution_applicator.py

"""
Solution Applicator - orchestrates the complete solution application workflow.

Uses relative_method_file and replaces the original method body
with corrected_implementation.method_body.
"""

import json
import hashlib
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List
from datetime import datetime, timezone

from cognalys.config.apply_solutions_config import ApplySolutionsConfig
from cognalys.llm.semantic_validation import normalize_coordinated_edit
from cognalys.solutions.scope_utils import (
    brace_depth_at,
    enclosing_class_stack,
    find_declaration_scope_mismatches,
    keep_doc_comment_on_anchor,
    scope_change_outside_edit,
)
from cognalys.utils.contribution_base import (
    code_exists_at, path_exists_at, relocated_path, renamed_paths,
    submodule_containing, submodule_of,
)
from cognalys.solutions.solution_mapper import SolutionMapper
from cognalys.solutions.worktree_manager import WorktreeManager
from cognalys.utils.network_git import run_network_git
from cognalys.utils.disposition import FindingDisposition, minimum_coordinated_edits
from cognalys.utils.edit_text import (
    keep_untouched_formatting,
    match_trailing_line_break,
    read_exact,
    read_source,
    remove_block,
    write_exact,
    write_source,
)


def validate_execution_report(report: Dict) -> list[str]:
    """Deterministic shape guard for execution_report.json.

    Not a full schema: a tripwire against accidental structural drift
    between the three scripts that read-modify-write this file
    (solution_applicator.py, validate_applied_solutions.py,
    repair_validation_failures.py). Findings are logged, not fatal.
    """
    issues = []
    details = report.get("per_solution_details")
    if not isinstance(details, list):
        return ["per_solution_details must be a list"]
    for index, entry in enumerate(details):
        if not isinstance(entry, dict):
            issues.append(f"per_solution_details[{index}] is not an object")
            continue
        if entry.get("status") == "SUCCESS" and not str(
                entry.get("finding_id", "")).strip():
            issues.append(
                f"per_solution_details[{index}] is SUCCESS without finding_id"
            )
    return issues


def write_execution_report_atomic(path: Path, report: Dict) -> None:
    """Write execution_report.json via write-then-rename.

    A plain write can leave the file truncated if the process is killed
    mid-write; every later pipeline step in the same run depends on this
    file being valid JSON.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


def scoped_project(error_solutions_dir: str) -> str:
    """The one project this invocation was given, or "" for a whole run.

    `run_pipeline.py --project X` points the apply step at
    `<run>/error_solutions/X`; a whole-run invocation points it at
    `<run>/error_solutions` itself.
    """
    path = Path(str(error_solutions_dir or ""))
    return path.name if path.parent.name == "error_solutions" else ""


def details_with_other_projects(earlier_report: Dict | None,
                                current: List[Dict],
                                project: str) -> List[Dict]:
    """This invocation's results plus what earlier ones recorded for others.

    A run is resumed one project at a time under the same run id, and each
    invocation used to replace execution_report.json with only its own
    project. In run_20260927_175748 that left the report with TagMo alone,
    so the later steps and the retrospective saw no trace of any other
    project's application. A project-scoped invocation now keeps the
    earlier entries of every project it did not process; a whole-run
    invocation still replaces the report, since it re-examined everything.
    """
    if not project or not isinstance(earlier_report, dict):
        return list(current)
    processed = {project} | {str(item.get("apk_name", "")) for item in current}
    kept = [
        item for item in earlier_report.get("per_solution_details") or []
        if isinstance(item, dict)
        and str(item.get("apk_name", "")) not in processed
    ]
    return kept + list(current)


def workflow_status(results: List[Dict]) -> str:
    """One word for how an apply invocation went, from its own results.

    No solutions were even discovered/mapped to attempt, which is a
    meaningfully different outcome from every solution having been
    attempted and succeeded -- collapsing them both into "SUCCESS" hid an
    empty run behind a status that reads as completed work.
    """
    statuses = [item.get("status") for item in results]
    if not statuses:
        return "SUCCESS_WITH_NO_ACTION"
    if all(status == "DRY_RUN" for status in statuses):
        return "DRY_RUN"
    failed = statuses.count("FAILED")
    applied = statuses.count("SUCCESS")
    if (failed == 0 and applied == 0
            and statuses.count("NOT_APPLICABLE_TO_BASE") == len(statuses)):
        return "SUCCESS_WITH_NO_ACTION"
    if failed == 0:
        return "SUCCESS"
    return "PARTIAL" if applied > 0 else "FAILED"


def read_earlier_execution_report(path: Path) -> Dict | None:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) else None


def validate_coordinated_edit_authorization(solution: Dict) -> list[str]:
    """Reject v2 edits that are broader than the adjudicated patch plan."""
    architecture = solution.get("llm_architecture", {}) or {}
    if str(architecture.get("version", "v1")).lower() != "v2":
        return []
    plan = solution.get("authorized_patch_plan", {}) or {}
    planned_edits = [
        item for item in plan.get("required_edits", [])
        if isinstance(item, dict)
    ]
    if not planned_edits:
        return ["V2_COORDINATED_EDIT_WITHOUT_AUTHORIZED_PLAN"]
    planned_by_path = {
        Path(str(item.get("source_file_path", ""))).as_posix(): item
        for item in planned_edits if str(item.get("source_file_path", "")).strip()
    }
    issues = []
    sensitive_suffixes = {
        ".gradle", ".kts", ".toml", ".properties", ".lockfile",
    }
    for edit in solution.get("coordinated_edits", []):
        path = Path(str(edit.get("source_file_path", ""))).as_posix()
        planned = planned_by_path.get(path)
        if planned is None:
            issues.append(f"EDIT_OUTSIDE_AUTHORIZED_PLAN:{path}")
            continue
        if edit.get("edit_kind") == "create" and not planned.get(
                "create_new_file", False):
            issues.append(f"UNAUTHORIZED_FILE_CREATION:{path}")
        category = str(planned.get("edit_category", "")).lower()
        if Path(path).suffix.lower() in sensitive_suffixes and not any(
                token in category for token in ("dependency", "build", "config")):
            issues.append(f"SENSITIVE_FILE_CATEGORY_NOT_AUTHORIZED:{path}")
    return issues


_SOURCE_LAYOUT = re.compile(
    r"^(?:(?P<module>.+)/)?src/(?P<set>[^/]+)/(?P<lang>java|kotlin)/"
    r"(?:(?P<package>.+)/)?(?P<name>[^/]+)$"
)


def _created_file_relocations(base_repo: Path, base_ref: str, old_source: str,
                              new_source: str, created_edits: list[Dict],
                              ) -> dict[str, tuple[str, str, str]]:
    """Where files the patch creates belong once the reported file has moved.

    A created file is not in git's rename list: it exists at neither
    revision. BetterUntis' fix created DiskCacheTest.kt next to the old
    app/src/main/.../data/cache/DiskCache.kt, in package
    com.sapuseven.untis.data.cache; on develop the class lives in the
    core/data module under com.sapuseven.untis.core.data.cache, so the test
    would have been written into a module and package where it cannot see
    the class it tests. A file created in the old source's module and package
    follows it to the new module and package, keeping its own source set
    (test, androidTest, ...). The language directory is the one that source
    set already uses on the base, else the new source's.

    Returns old path -> (new path, old package, new package).
    """
    old, new = _SOURCE_LAYOUT.match(old_source), _SOURCE_LAYOUT.match(new_source)
    if not old or not new:
        return {}
    relocations = {}
    for edit in created_edits:
        path = Path(str(edit.get("source_file_path", ""))).as_posix()
        layout = _SOURCE_LAYOUT.match(path)
        if (not layout or layout["module"] != old["module"]
                or layout["package"] != old["package"]):
            continue
        source_set = layout["set"]
        prefix = f"{new['module']}/" if new["module"] else ""
        language = new["lang"]
        for candidate in (layout["lang"], "kotlin", "java"):
            if path_exists_at(base_repo, base_ref,
                              f"{prefix}src/{source_set}/{candidate}"):
                language = candidate
                break
        package_dir = f"{new['package']}/" if new["package"] else ""
        relocations[path] = (
            f"{prefix}src/{source_set}/{language}/{package_dir}{layout['name']}",
            (old["package"] or "").replace("/", "."),
            (new["package"] or "").replace("/", "."),
        )
    return relocations


def _with_package_moved(code: str, old_package: str, new_package: str) -> str:
    """A created file's package line and imports from the old package, moved.

    Only the package declaration and imports naming the old package change:
    the file is new, so nothing else can depend on where it was declared.
    """
    if not old_package or not new_package or old_package == new_package:
        return code
    old = re.escape(old_package)
    code = re.sub(rf"^(\s*package\s+){old}(\s*;?\s*)$",
                  rf"\g<1>{new_package}\g<2>", code, count=1, flags=re.M)
    return re.sub(rf"^(\s*import\s+(?:static\s+)?){old}\.",
                  rf"\g<1>{new_package}.", code, flags=re.M)


def already_on_base(change: Dict, base_text: str) -> bool:
    """Whether the base branch already holds this edit's corrected code.

    True when the corrected code is in the base file and the code it
    replaces is not: the edit would add nothing there. Whitespace is
    ignored, since the base may have reformatted the lines.
    """
    def flat(text) -> str:
        return " ".join(str(text or "").split())

    corrected, original = flat(change.get("corrected_code")), flat(change.get("original_code"))
    if not corrected or corrected == original or change.get("edit_kind") == "create":
        return False
    base = flat(base_text)
    return corrected in base and original not in base


def _text_at(repo: Path, ref: str, relative: str) -> "str | None":
    """``relative`` as committed at ``ref`` in ``repo``, or None when it is not there."""
    if not relative:
        return None
    try:
        done = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{relative}"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def port_already_fixed_confirmation(solution: Dict, base_repo: Path, base_ref: str) -> str:
    """Why the base is known to hold the fix the port said it holds, or "".

    The port's ALREADY_FIXED_UPSTREAM was taken on the model's word (B5).
    Two deterministic answers confirm it: the statement the detector
    reported, read at the analysed revision (HEAD of ``base_repo``), is in no
    file of the base (contribution_base.statement_on_branch); or every edit's
    corrected code is on the base and the code it replaces is not
    (already_on_base). Anything else, including a question that cannot be
    asked, confirms nothing.
    """
    from cognalys.llm.finding_context import reported_line
    from cognalys.utils.contribution_base import reported_statement, statement_on_branch

    relative = str(solution.get("source_file_path", "") or "")
    line = reported_line(solution, relative) if relative else 0
    analysed = _text_at(base_repo, "HEAD", relative) if line > 0 else None
    lines = analysed.splitlines() if analysed is not None else []
    statement = reported_statement(lines[line - 1]) if 0 < line <= len(lines) else ""
    if statement and statement_on_branch(base_repo, base_ref, statement) is False:
        return f"the reported statement `{statement}` is in no file of the base"
    edits = [edit for edit in solution.get("coordinated_edits") or []
             if isinstance(edit, dict) and edit.get("edit_kind") != "create"] or [{
                 "source_file_path": relative,
                 "original_code": solution.get("original_method_body", ""),
                 "corrected_code": solution.get("corrected_method_body", "")}]
    for edit in edits:
        base_text = _text_at(base_repo, base_ref, str(edit.get("source_file_path", "")))
        change = {"original_code": edit.get("original_code", ""),
                  "corrected_code": edit.get("corrected_code", ""),
                  "edit_kind": edit.get("edit_kind", "")}
        if base_text is None or not already_on_base(change, base_text):
            return ""
    return "every edit's corrected code is already on the base"


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def port_changes_correction(solution: Dict, ported: Dict) -> bool:
    """Whether a port rewrote the correction, not only where it sits (B6).

    What the behaviour review judged is what each edit writes: the lines of
    its corrected code that its original code does not hold (whitespace
    ignored). A port re-anchors a block on the base's code, so the lines it
    keeps from the base (its context) differ from the reviewed block's; that
    is not a new correction. A port that writes a line no reviewed edit wrote
    changed what is proposed. A created file is compared whole.
    """
    def written(record: Dict) -> set:
        edits = [edit for edit in record.get("coordinated_edits") or []
                 if isinstance(edit, dict)] or [{
                     "original_code": record.get("original_method_body", ""),
                     "corrected_code": record.get("corrected_method_body", "")}]
        lines = set()
        for edit in edits:
            before = {_flat(line) for line in str(edit.get("original_code") or "").splitlines()}
            lines |= {_flat(line) for line in str(edit.get("corrected_code") or "").splitlines()
                      if _flat(line) and _flat(line) not in before}
        return lines

    return not written(ported) <= written(solution)


def recorded_solution_record(solution: Dict) -> Dict:
    """The solution file the applicator's solution came from, or {}."""
    try:
        data = json.loads(Path(str(solution.get("solution_file", ""))).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def recorded_review_contract_version(solution: Dict):
    """The behaviour-review contract the solution passed; None when unreviewed.

    Read from the discovered solution when it carries the field, otherwise
    from its solution file's metadata (SolutionMapper builds the solution
    from a fixed list of fields).
    """
    if "review_contract_version" in solution:
        return solution["review_contract_version"]
    try:
        data = json.loads(Path(str(solution.get("solution_file", ""))).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    metadata = data.get("solution_metadata") if isinstance(data, dict) else None
    return metadata.get("review_contract_version") if isinstance(metadata, dict) else None


# The port's ALREADY_FIXED_UPSTREAM that no deterministic check confirmed (B5).
PORT_ALREADY_FIXED_UNCONFIRMED = "PORT_ALREADY_FIXED_UNCONFIRMED"


class SolutionApplicator:
    """Orchestrates end-to-end solution application workflow."""

    def __init__(self, config: ApplySolutionsConfig):

        self.config = config

        self.logger = logging.getLogger(
            self.__class__.__name__
        )

        self.results = []
        self.errors = []

        self.start_time = None
        self.worktrees = WorktreeManager(Path(config.worktrees_dir), self.logger)

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    @staticmethod
    def _find_git_root(source_file: Path) -> Path:
        current = source_file.parent
        while current != current.parent:
            if (current / ".git").exists():
                return current
            current = current.parent
        raise RuntimeError(f"No Git repository found for {source_file}")

    def _prepare_solution_branch(self, source_file: Path, solution: Dict) -> tuple[str, str]:
        """Create the contribution branch from the documented base before patching."""
        repo = self._find_git_root(source_file)
        if self._git(repo, "status", "--porcelain"):
            raise RuntimeError(
                "Repository has uncommitted changes before solution application; "
                "refusing to create a contribution branch"
            )

        policy = solution.get("branch_policy", {})
        base_branch = str(policy.get("recommended_base_branch", "")).strip()
        if not base_branch:
            for remote in ("upstream", "origin"):
                ref = self._git(repo, "symbolic-ref", "--quiet", "--short",
                                f"refs/remotes/{remote}/HEAD") if subprocess.run(
                    ["git", "symbolic-ref", "--quiet", "--short",
                     f"refs/remotes/{remote}/HEAD"], cwd=repo,
                    capture_output=True, text=True
                ).returncode == 0 else ""
                if ref:
                    base_branch = ref.split("/", 1)[-1]
                    break
        if not base_branch:
            base_branch = self._git(repo, "branch", "--show-current")
        if not base_branch:
            raise RuntimeError("Could not determine a base branch")

        local_exists = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet",
             f"refs/heads/{base_branch}"], cwd=repo
        ).returncode == 0
        if local_exists:
            self._git(repo, "checkout", base_branch)
        else:
            remotes = {
                remote.strip()
                for remote in self._git(repo, "remote").splitlines()
                if remote.strip()
            }

            def available_remote_ref() -> str:
                return next(
                    (
                        f"{remote}/{base_branch}"
                        for remote in ("upstream", "origin")
                        if remote in remotes and subprocess.run(
                            ["git", "show-ref", "--verify", "--quiet",
                             f"refs/remotes/{remote}/{base_branch}"], cwd=repo
                        ).returncode == 0
                    ),
                    "",
                )

            remote_ref = available_remote_ref()
            if not remote_ref:
                # Clones created with --single-branch only have the initially
                # selected branch in their fetch refspec. Fetch the documented
                # contribution base explicitly instead of silently falling
                # back to master/main.
                fetch_errors = []
                for remote in ("upstream", "origin"):
                    if remote not in remotes:
                        continue
                    fetch = run_network_git(
                        [
                            "git", "fetch", remote,
                            f"refs/heads/{base_branch}:"
                            f"refs/remotes/{remote}/{base_branch}",
                        ],
                        cwd=repo,
                        capture_output=True,
                        text=True,
                        raise_on_timeout=False,
                    )
                    if fetch.returncode == 0:
                        remote_ref = f"{remote}/{base_branch}"
                        self.logger.info(
                            "Fetched documented base branch %s from %s",
                            base_branch,
                            remote,
                        )
                        break
                    fetch_errors.append(
                        f"{remote}: {fetch.stderr.strip() or 'fetch failed'}"
                    )
            if not remote_ref:
                raise RuntimeError(
                    f"Documented base branch '{base_branch}' is not available "
                    "locally or on the configured remotes: "
                    + "; ".join(fetch_errors)
                )
            self._git(repo, "checkout", "-b", base_branch, remote_ref)

        method = re.sub(r"[^a-z0-9]+", "-", str(
            solution.get("method_name", "change")
        ).lower()).strip("-") or "change"
        spec = re.sub(r"[^a-z0-9]+", "-", str(
            solution.get("spec", "finding")
        ).lower()).strip("-") or "finding"
        identity = str(solution.get("unique_error_id", "")) or "|".join(
            str(solution.get(key, ""))
            for key in ("apk_name", "csv_class", "method_name", "spec")
        )
        finding_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
        timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        branch = f"fix/{method}-{spec}-{timestamp}-{finding_id}"
        self._git(repo, "checkout", "-b", branch)
        self.logger.info(
            "Prepared contribution branch %s from %s%s",
            branch,
            base_branch,
            f" according to {policy.get('source')}" if policy.get("source") else "",
        )
        return branch, base_branch

    def apply_solutions(self) -> Dict:
        """Execute complete workflow."""

        self.start_time = datetime.now()

        self.logger.info(
            "Starting solution application workflow"
        )

        Path(self.config.output_dir).mkdir(
        parents=True,
        exist_ok=True
    )


        try:

            # ==========================================================
            # STAGE 1
            # ==========================================================

            self.logger.info("=" * 60)
            self.logger.info("STAGE 1: DISCOVERY & MAPPING")
            self.logger.info("=" * 60)

            mapper = SolutionMapper(
                self.config.error_solutions_dir,
                self.config.cloned_projects_dir
            )

            mapping = mapper.build_mapping()

            report = mapper.generate_mapping_report()

            self.logger.info(
                f"Mapping report: {report}"
            )

            mapping_report_path = (
                Path(self.config.output_dir)
                / "mapping_report.json"
            )

            mapper.save_mapping_report(
                str(mapping_report_path)
            )

            # ==========================================================
            # STAGE 2
            # ==========================================================

            self.logger.info("=" * 60)
            self.logger.info("STAGE 2: APPLYING SOLUTIONS")
            self.logger.info("=" * 60)

            solutions = mapper.solutions

            if self.config.max_solutions > 0:

                solutions = solutions[
                    :self.config.max_solutions
                ]

            total_solutions = len(solutions)

            processed = 0

            for solution in solutions:

                processed += 1

                self.logger.info(
                    f"Processing "
                    f"{processed}/{total_solutions}: "
                    f"{solution['method_name']}"
                )

                # .appliable, not .publishable: a blocked publication is
                # still applied and built here. That asymmetry used to be
                # expressed by leaving publication_allowed out of this
                # expression, which is why setting manual_review_required for
                # a publication-blocked finding silently stopped application.
                review_only = not FindingDisposition.from_metadata(
                    solution
                ).appliable
                validation_approved = (
                    review_only
                    and self.config.approve_review_only_for_validation
                    and solution.get("publication_allowed") is False
                    and solution.get("semantic_validation", {}).get("status")
                    == "valid"
                )
                if review_only and not validation_approved:
                    self.logger.warning(
                        "Skipping review-only solution for %s; no repository "
                        "files or branches will be changed",
                        solution.get("method_name", "unknown"),
                    )
                    self.results.append({
                        "status": "REVIEW_ONLY",
                        "apk_name": solution.get("apk_name", ""),
                        "method_name": solution.get("method_name", ""),
                        "csv_class": solution.get("csv_class", ""),
                        "spec": solution.get("spec", ""),
                        "fix_scope": solution.get("fix_scope", "review_only"),
                        "decision": solution.get("decision", ""),
                        "manual_review_required": True,
                        "auto_apply": False,
                        "publication_allowed": False,
                        "solution_file": solution.get("solution_file", ""),
                        "message": (
                            "Patch was generated for human review and was not applied."
                        ),
                    })
                    continue
                if validation_approved:
                    self.logger.warning(
                        "Applying review-only solution for %s to an isolated "
                        "worktree for validation; publication remains disabled",
                        solution.get("method_name", "unknown"),
                    )
                    solution = dict(solution)
                    # The scope stays the one the solution file records
                    # (review_only), so the log and the PR JSON say the same
                    # thing; applying for validation is a separate fact.
                    solution["applied_for_validation"] = True
                    solution["publication_allowed"] = False

                result = self._apply_single_solution(
                    solution,
                    mapper
                )

                self.results.append(result)

            # ==========================================================
            # STAGE 3
            # ==========================================================

            self.logger.info("=" * 60)
            self.logger.info("STAGE 3: GENERATING REPORT")
            self.logger.info("=" * 60)

            execution_report = (
                self._generate_execution_report()
            )

            self._save_execution_report(
                execution_report
            )

            return execution_report

        except Exception as e:

            self.logger.error(
                f"Workflow error: {e}"
            )

            raise

    def _apply_coordinated_edits(
        self,
        solution: Dict,
        mapper: SolutionMapper,
        project_root: Path,
    ) -> tuple[bool, list[Dict], str]:
        """Validate and apply a repository-bounded edit set atomically."""
        edits = solution.get("coordinated_edits", [])
        authorization_issues = validate_coordinated_edit_authorization(solution)
        if authorization_issues:
            return False, [], ";".join(authorization_issues)
        # The same rule the generator applies, from one place, so a patch the
        # generator accepted is not refused here for its size.
        minimum_edits = minimum_coordinated_edits(
            str(solution.get("decision", "")),
            len([
                item for item in (
                    solution.get("authorized_patch_plan") or {}
                ).get("required_edits", []) or []
                if isinstance(item, dict)
            ]),
            str(solution.get("fix_scope", "")),
        )
        if (not isinstance(edits, list)
                or not (minimum_edits <= len(edits) <= 64)):
            return False, [], "INVALID_COORDINATED_EDIT_COUNT"
        project_root = project_root.resolve()
        prepared = []
        snapshots = {}
        # The bytes each file had, for a rollback: snapshots hold the LF
        # text the edits are spliced into (edit_text.read_source).
        on_disk = {}
        seen_blocks = set()
        try:
            for edit in edits:
                relative = Path(str(edit.get("source_file_path", "")))
                if relative.is_absolute() or ".." in relative.parts:
                    return False, [], "UNSAFE_COORDINATED_EDIT_PATH"
                target = (project_root / relative).resolve()
                try:
                    target.relative_to(project_root)
                except ValueError:
                    return False, [], "COORDINATED_EDIT_OUTSIDE_REPOSITORY"
                original = str(edit.get("original_code", ""))
                corrected = str(edit.get("corrected_code", ""))
                edit_kind = str(edit.get("edit_kind", "method"))
                if edit_kind == "create":
                    if target.exists():
                        return False, [], f"COORDINATED_CREATE_TARGET_EXISTS:{relative}"
                elif not target.is_file():
                    return False, [], f"COORDINATED_SOURCE_NOT_FOUND:{relative}"
                # The declaration-with-empty-text form predates edit_kind
                # "delete". The rule for when that is a safe deletion rather
                # than a truncated response lived here as a second copy of
                # normalize_coordinated_edit; they agreed, but only by luck --
                # a change to one would not have reached the other.
                normalized = normalize_coordinated_edit(edit)
                if normalized.get("edit_kind") != edit_kind:
                    edit_kind = str(normalized["edit_kind"])
                    edit["edit_kind"] = edit_kind
                    corrected = str(normalized.get("corrected_code", ""))
                content = read_source(target) if target.is_file() else ""
                if edit_kind != "create" and not original.strip():
                    return False, [], f"EMPTY_COORDINATED_EDIT:{relative}"
                if edit_kind == "create":
                    if original or not corrected.strip():
                        return False, [], f"INVALID_COORDINATED_CREATE:{relative}"
                elif edit_kind == "delete":
                    if corrected:
                        return False, [], f"NONEMPTY_DELETE_EDIT:{relative}"
                elif not corrected.strip():
                    return False, [], f"EMPTY_COORDINATED_EDIT:{relative}"
                expected_hash = str(edit.get("original_code_sha256", ""))
                if (expected_hash and hashlib.sha256(
                        original.encode("utf-8")).hexdigest() != expected_hash):
                    return False, [], f"COORDINATED_EDIT_HASH_MISMATCH:{relative}"
                original_occurrences = content.count(original) if original else 0
                if edit_kind != "create":
                    if original_occurrences == 0:
                        return False, [], f"ORIGINAL_CODE_NOT_FOUND:{relative}"
                    if original_occurrences > 1:
                        return (
                            False,
                            [],
                            f"ORIGINAL_CODE_NOT_UNIQUE:{relative}:"
                            f"count={original_occurrences}",
                        )
                block_key = (target, original)
                if block_key in seen_blocks:
                    return False, [], f"DUPLICATE_COORDINATED_EDIT:{relative}"
                seen_blocks.add(block_key)
                if edit_kind == "declaration":
                    anchor_offset = content.find(original)
                    anchor_nested = brace_depth_at(content, anchor_offset) > 0
                    owner = enclosing_class_stack(content, anchor_offset)
                    mismatches = find_declaration_scope_mismatches(
                        original, corrected, anchor_nested,
                        other_edits=edits,
                        declaring_file=str(edit.get("source_file_path", "")),
                        anchor_owner_is_object=bool(owner) and bool(
                            owner[-1].get("is_object")),
                    )
                    original, corrected = keep_doc_comment_on_anchor(
                        content, original, corrected)
                    if mismatches:
                        return (
                            False,
                            [],
                            f"DECLARATION_SCOPE_MISMATCH:{relative}:"
                            + " | ".join(mismatches),
                        )
                if edit_kind == "method":
                    corrected = mapper.replacer.remove_duplicate_leading_annotations(
                        content, original, corrected
                    )
                    if mapper.replacer.keeps_generated_layout(original, corrected):
                        # Spliced as written, so whatever it declares after
                        # an enclosing `}` lands where the model put it:
                        # the same scope check a declaration edit gets.
                        anchor_offset = content.find(original)
                        owner = enclosing_class_stack(content, anchor_offset)
                        mismatches = find_declaration_scope_mismatches(
                            original, corrected,
                            brace_depth_at(content, anchor_offset) > 0,
                            other_edits=edits,
                            declaring_file=str(edit.get("source_file_path", "")),
                            anchor_owner_is_object=bool(owner) and bool(
                                owner[-1].get("is_object")),
                        )
                        if mismatches:
                            return (
                                False,
                                [],
                                f"DECLARATION_SCOPE_MISMATCH:{relative}:"
                                + " | ".join(mismatches),
                            )
                    corrected = mapper.replacer.align_generated_indentation(
                        original, corrected
                    )
                snapshots.setdefault(target, None if edit_kind == "create" else content)
                on_disk.setdefault(target, None if edit_kind == "create" else read_exact(target))
                prepared.append(
                    (target, relative, original, corrected, edit_kind, edit)
                )
            changes = []
            updated_sources = dict(snapshots)
            for target, relative, original, corrected, edit_kind, edit in prepared:
                if edit_kind == "create":
                    updated_sources[target] = corrected
                    changes.append({
                        "source_file": relative.as_posix(),
                        "symbol": str(edit.get("symbol", "")),
                        "edit_kind": edit_kind,
                        "original_code": "",
                        "corrected_code": corrected,
                        "reason": str(edit.get("reason", "")),
                    })
                    continue
                if original not in updated_sources[target]:
                    return False, [], f"OVERLAPPING_COORDINATED_EDIT:{relative}"
                # Recorded and written from the same text, so the persisted
                # edit and the diff a maintainer sees cannot disagree.
                corrected = match_trailing_line_break(
                    original, keep_untouched_formatting(original, corrected))
                scope_change = scope_change_outside_edit(
                    updated_sources[target], original, corrected)
                if scope_change:
                    return (
                        False,
                        [],
                        f"EDIT_CHANGES_ENCLOSING_SCOPE:{relative}:{scope_change}",
                    )
                if corrected:
                    updated_sources[target] = updated_sources[target].replace(
                        original, corrected, 1
                    )
                else:
                    updated_sources[target] = remove_block(
                        updated_sources[target], original)
                changes.append({
                    "source_file": relative.as_posix(),
                    "symbol": str(edit.get("symbol", "")),
                    "edit_kind": edit_kind,
                    "original_code": original,
                    "corrected_code": corrected,
                    "reason": str(edit.get("reason", "")),
                })
            for target, updated in updated_sources.items():
                # Edits that remove everything an existing file held remove
                # the file. Writing it back empty left Feeder's
                # OkHttpBuilderExtensions.kt as a zero-byte source in the
                # pull request instead of a deletion.
                if snapshots.get(target) is not None and not updated.strip():
                    target.unlink()
                    deleted = target.relative_to(project_root).as_posix()
                    for change in changes:
                        if change["source_file"] == deleted:
                            change["file_deleted"] = True
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                # In the file's own line endings: a CRLF file written back
                # from the LF text changed every line (2026-10-30).
                write_source(target, updated)
            return True, changes, ""
        except Exception as exc:
            for target, content in on_disk.items():
                try:
                    if content is None:
                        if target.is_file():
                            target.unlink()
                    else:
                        write_exact(target, content)
                except OSError:
                    self.logger.error("Rollback failed for %s", target)
            return False, [], f"COORDINATED_PATCH_FAILED:{exc}"

    def _apply_single_solution(
        self,
        solution: Dict,
        mapper: SolutionMapper
    ) -> Dict:
        """Apply a single solution."""

        apk_name = solution['apk_name']

        method_name = solution['method_name']

        csv_class = solution['csv_class']

        result = {
            "apk_name": apk_name,
            "method_name": method_name,
            "csv_class": csv_class,
            "source_file": solution.get("source_file_path", ""),
            "spec": solution.get("spec", ""),
            "error_message": solution.get("error_message", ""),
            "tool": solution.get("tool", ""),
            "detector_context": solution.get("detector_context") or {},
            "repository_guidance": solution.get("repository_guidance", []),
            "branch_policy": solution.get("branch_policy", {}),
            "compatibility_policy": solution.get(
                "compatibility_policy", {}
            ),
            "migration_assessment": solution.get("migration_assessment", {}),
            "publication_considerations": solution.get(
                "publication_considerations", []
            ),
            "publication_blocker_record": solution.get(
                "publication_blocker_record", {}
            ),
            "digest_contract_assessment": solution.get(
                "digest_contract_assessment", {}
            ),
            "review_assumptions": solution.get("review_assumptions", []),
            "review_blockers": solution.get("review_blockers", []),
            "required_tests": solution.get("required_tests", []),
            "decision": solution.get("decision", ""),
            "manual_review_required": solution.get(
                "manual_review_required", False
            ),
            "auto_apply": solution.get("auto_apply", True),
            "publication_allowed": solution.get("publication_allowed", True),
            "publication_status": solution.get("publication_status", ""),
            "semantic_validation": solution.get(
                "semantic_validation", {"status": "not_run"}
            ),
            "stored_value_contract": solution.get("stored_value_contract", {}),
            "fix_scope": solution.get("fix_scope", "single_method"),
            "applied_for_validation": solution.get("applied_for_validation") is True,
            # The behaviour-review contract this draft passed (None: not
            # reviewed), carried with the row so the PR metadata and the
            # statistics need not find the solution file from the report's
            # place in a run directory.
            "review_contract_version": recorded_review_contract_version(solution),
            # Which findings the pull request is published for, when the
            # reported one has no effect (pr_generator.attributed).
            "pr_attribution": solution.get("pr_attribution") or {},
            "status": "FAILED",
            "error": None
        }

        try:

            base_source_file = mapper.locate_source_file(
                solution['relative_method_file']
            )

            if not base_source_file:

                result["error"] = "SOURCE_FILE_NOT_FOUND"

                return result

            original_method_body = solution[
                'original_method_body'
            ]

            corrected_method_body = solution[
                'corrected_method_body'
            ]

            source_content = base_source_file.read_text(
                encoding="utf-8",
                errors="replace",
            )
            corrected_method_body = (
                mapper.replacer.remove_duplicate_leading_annotations(
                    source_content,
                    original_method_body,
                    corrected_method_body,
                )
            )

            corrected_explanation = solution[
                'corrected_explanation'
            ]

            # Keep the actual normalized replacement in the execution report so later
            # preview generation does not need to reconstruct source from Git.
            result["original_method_body"] = original_method_body
            result["corrected_method_body"] = corrected_method_body

            if not original_method_body:

                result["error"] = (
                    "ORIGINAL_METHOD_BODY_NOT_FOUND"
                )

                return result

            if not corrected_method_body:

                result["error"] = (
                    "CORRECTED_METHOD_BODY_NOT_FOUND"
                )

                return result

            if not corrected_explanation:

                result["error"] = (
                    "CORRECTED_EXPLANATION_NOT_FOUND"
                )

                return result

            if self.config.dry_run:

                result["status"] = "DRY_RUN"

                result["quality_score"] = solution.get(
                    'quality_score',
                    0
                )

                result["explanation"] = solution.get(
                    'corrected_explanation',
                    "No explanation provided."
                )

                self.logger.info(
                    f"Dry run: would patch {method_name}"
                )

                return result

            base_repo = self._find_git_root(base_source_file)
            worktree = self.worktrees.prepare(base_repo, solution)
            worktree_root = Path(worktree["worktree_path"])
            relative_source = Path(str(solution.get("source_file_path", "")))
            if relative_source.is_absolute() or ".." in relative_source.parts:
                result["error"] = "UNSAFE_SOURCE_PATH"
                return result
            source_file = (worktree_root / relative_source).resolve()
            try:
                source_file.relative_to(worktree_root.resolve())
            except ValueError:
                result["error"] = "SOURCE_OUTSIDE_WORKTREE"
                return result
            if not source_file.is_file():
                # The worktree is built from the branch a pull request would
                # target, which is not necessarily the revision the finding
                # was analysed at. When the reported path simply does not
                # exist there -- pinned historical revision, or a file the
                # project renamed, moved or deleted since -- the patch has
                # nowhere to land. That is a property of the finding, not a
                # fault in this run, so it is named and counted apart from a
                # failure. See utils.contribution_base.
                # base_commit, not the branch name: it is the exact tree the
                # worktree was created from, so the answer cannot drift if the
                # branch moved between preparing and checking.
                base_ref = str(
                    worktree.get("base_commit")
                    or worktree.get("base_branch", "")
                )
                relocated = self._relocate_moved_sources(
                    solution, base_repo, base_ref, str(relative_source),
                    original_method_body,
                )
                if relocated:
                    self.logger.info(
                        "%s/%s: %s moved to %s on the base branch with the "
                        "reviewed code unchanged; the patch is applied there",
                        apk_name, method_name, relative_source, relocated,
                    )
                    result["relocated_source"] = {
                        "from": str(relative_source), "to": relocated,
                    }
                    result["source_file"] = relocated
                    relative_source = Path(relocated)
                    source_file = (worktree_root / relative_source).resolve()
                elif not path_exists_at(base_repo, base_ref, str(relative_source)):
                    result["status"] = "NOT_APPLICABLE_TO_BASE"
                    result["error"] = "SOURCE_FILE_ABSENT_FROM_BASE_BRANCH"
                    result["contribution_base"] = {
                        "base_branch": worktree.get("base_branch", ""),
                        "base_commit": base_ref,
                        "source_file_path": str(relative_source),
                        "target_present": False,
                    }
                    submodule = submodule_containing(
                        base_repo, base_ref, str(relative_source),
                    ) or submodule_of(base_repo, str(relative_source))
                    if submodule:
                        # The file is there, in another repository; a pull
                        # request against this one cannot change it.
                        result["error"] = "SOURCE_IN_SUBMODULE"
                        result["contribution_base"]["submodule"] = submodule
                    self.logger.warning(
                        "%s/%s cannot be contributed: %s does not exist on "
                        "the base branch %s, so there is nothing to patch "
                        "there",
                        apk_name, method_name, relative_source,
                        worktree.get("base_branch", "unknown"),
                    )
                    return result
                if not source_file.is_file():
                    result["error"] = "WORKTREE_SOURCE_FILE_NOT_FOUND"
                    return result
            result.update(worktree)

            if self.config.save_backups:

                self._backup_source_file(
                    source_file,
                    apk_name,
                    method_name
                )

            coordinated_edits = solution.get("coordinated_edits", [])
            if coordinated_edits:
                success, changes, coordinated_error = (
                    self._apply_coordinated_edits(
                        solution, mapper, worktree_root
                    )
                )
                if (not success and str(coordinated_error).startswith(
                        "ORIGINAL_CODE_NOT_FOUND")):
                    rebased = self._apply_rebased_coordinated_edits(
                        solution, mapper, worktree_root, base_repo,
                    )
                    upstream = [change for change in rebased or []
                                if change.get("already_on_base")]
                    if rebased is not None and len(upstream) == len(rebased):
                        # Every edit is already on the base: nothing to propose.
                        result["status"] = "NOT_APPLICABLE_TO_BASE"
                        result["error"] = "ORIGINAL_CODE_ABSENT_FROM_BASE_BRANCH"
                        result["contribution_base"] = {
                            "base_commit": str(worktree.get("base_commit", "")),
                            "source_file_path": str(relative_source),
                            "target_present": True, "analyzed_code_present": False,
                            "violation_absent_per_port": True,
                        }
                        return result
                    if rebased is not None:
                        if upstream:
                            result["edits_already_on_base"] = [
                                str(change.get("reason") or change.get("symbol", ""))
                                for change in upstream
                            ]
                            rebased = [change for change in rebased
                                       if not change.get("already_on_base")]
                        success, changes, coordinated_error = True, rebased, ""
                        result["rebased_onto_base"] = True
                        self.logger.info(
                            "%s/%s: edits written against the analysed "
                            "revision were merged onto the base branch",
                            apk_name, method_name,
                        )
                if (not success and str(coordinated_error).startswith(
                        "ORIGINAL_CODE_NOT_FOUND")):
                    port = self._port_to_base(
                        solution, mapper, worktree_root, base_repo,
                        str(worktree.get("base_commit", "")), result,
                    )
                    if port.get("applied"):
                        success, changes, coordinated_error = (
                            True, port["changes"], "",
                        )
                result["coordinated_edits"] = changes
                # A file several edits touch is one changed file, listed once.
                result["changed_files"] = list(dict.fromkeys(
                    item["source_file"] for item in changes
                ))
                result["fix_scope"] = solution.get(
                    "fix_scope", "coordinated"
                )
                # A port that found the violation gone has already named why
                # the finding is not applicable; the edit error says less.
                if (coordinated_error
                        and result.get("status") != "NOT_APPLICABLE_TO_BASE"):
                    result["error"] = coordinated_error
            else:
                from cognalys.solutions.solution_mapper import (
                    ALREADY_APPLIED, APPLIED, ORIGINAL_CODE_NOT_FOUND,
                    ORIGINAL_CODE_NOT_UNIQUE,
                )

                replaced = mapper.replacer.replace_method_status(
                    source_file=source_file,
                    original_method_body=original_method_body,
                    new_method_body=corrected_method_body,
                )
                success = replaced == APPLIED
                if replaced == ALREADY_APPLIED:
                    # The worktree is the base branch, untouched: its file
                    # already holds the corrected body and not the reviewed
                    # one, so there is nothing to propose. Reported as such,
                    # not as a patch that applied and changed nothing.
                    result["status"] = "NOT_APPLICABLE_TO_BASE"
                    result["error"] = ALREADY_APPLIED
                    result["contribution_base"] = {
                        "base_commit": str(worktree.get("base_commit", "")),
                        "source_file_path": str(relative_source),
                        "target_present": True, "analyzed_code_present": False,
                        "corrected_code_present": True,
                    }
                    return result
                if replaced == ORIGINAL_CODE_NOT_UNIQUE:
                    # Not ported: the port would face the same two copies.
                    result["error"] = f"{replaced}:{relative_source}"
                elif not success:
                    port = self._port_to_base(
                        solution, mapper, worktree_root, base_repo,
                        str(worktree.get("base_commit", "")), result,
                    )
                    if port.get("applied"):
                        success = True
                        ported = port["solution"]
                        result["original_method_body"] = ported[
                            "original_method_body"]
                        result["corrected_method_body"] = ported[
                            "corrected_method_body"]
                    elif replaced != ORIGINAL_CODE_NOT_FOUND:
                        # Why the reviewed body was not replaced, when it was
                        # there (PATCH_FAILED as before when it was not).
                        result["error"] = result.get("error") or replaced

            if not success:

                result["error"] = result.get("error") or "PATCH_FAILED"
                self._classify_base_drift(
                    result, base_repo, worktree.get("base_commit", ""),
                    str(relative_source), original_method_body,
                    apk_name, method_name,
                )

                return result

            result["status"] = "SUCCESS"

            result["quality_score"] = solution.get(
                'quality_score',
                0
            )

            result["explanation"] = solution.get(
                'corrected_explanation',
                "No explanation provided."
            )
            if result.get("edits_already_on_base") or (result.get("base_port") or {}).get("applied"):
                self._refresh_summary(solution, result)

            result["applied_timestamp"] = (
                datetime.now(timezone.utc).isoformat()
            )

            self.logger.info(
                f"Successfully patched "
                f"{method_name}"
            )

            return result

        except Exception as e:

            self.logger.error(
                f"Error applying solution: {e}"
            )

            result["error"] = str(e)

            return result

    def _backup_source_file(
        self,
        source_file: Path,
        apk_name: str,
        method_name: str
    ) -> Path:
        """Save a copy of a source file before patching."""

        backup_root = Path(self.config.backup_dir)

        if not backup_root.is_absolute():

            backup_root = (
                Path(self.config.output_dir)
                / backup_root
            )

        safe_apk = (
            apk_name
            .replace("/", "_")
            .replace("\\", "_")
            .replace(" ", "_")
        )

        safe_method = (
            method_name
            .replace("/", "_")
            .replace("\\", "_")
            .replace("<", "_")
            .replace(">", "_")
            .replace(":", "_")
            .replace("*", "_")
            .replace("?", "_")
            .replace('"', "_")
            .replace("|", "_")
        )

        backup_dir = backup_root / safe_apk

        backup_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        timestamp = datetime.utcnow().strftime(
            "%Y%m%dT%H%M%SZ"
        )

        backup_name = (
            f"{source_file.stem}_{safe_method}_{timestamp}"
            f"{source_file.suffix}.bak"
        )

        backup_path = backup_dir / backup_name

        shutil.copy2(
            source_file,
            backup_path
        )

        self.logger.info(
            f"Backup saved to {backup_path}"
        )

        return backup_path

    def _apply_rebased_coordinated_edits(
        self,
        solution: Dict,
        mapper: SolutionMapper,
        worktree_root: Path,
        base_repo: Path,
    ) -> list[Dict] | None:
        """Carry edits written against the analysed revision onto the base.

        The analysis reads the checkout at the revision the analysed APK was
        built from; the worktree starts from the tip of the base branch. When
        lines near the edit changed between the two, an edit's original code
        is not found and the patch was recorded as not applicable --
        nextcloudcookbook's trust-all helper is still on main, only the code
        around it moved. This applies the edits to the analysed files in a
        scratch directory, with the same function as always, and merges each
        file onto the base with git's three-way merge: the analysed file is
        the common ancestor, the patched one is "theirs", the base is "ours".

        Returns the recorded changes, or None (the worktree untouched) when a
        file is missing at either revision or any merge conflicts.
        """
        import tempfile

        worktree_root = Path(worktree_root).resolve()
        relatives = list(dict.fromkeys(
            str(edit.get("source_file_path", ""))
            for edit in solution.get("coordinated_edits", []) or []
            if isinstance(edit, dict) and edit.get("edit_kind") != "create"
        ))
        if not relatives or not base_repo:
            return None

        def analysed(relative: str):
            completed = subprocess.run(
                ["git", "-C", str(base_repo), "show", f"HEAD:{relative}"],
                capture_output=True,
            )
            if completed.returncode != 0:
                return None
            return completed.stdout.decode("utf-8", errors="replace").replace("\r\n", "\n")

        # The three sides are merged as LF views and the result is written in
        # the base file's own line endings (write_source). Merging the bytes
        # failed for a repository whose .gitattributes says eol=crlf: git
        # show gives the LF blob while the checkout holds CRLF, so every
        # line conflicted (2026-10-30 review); and a CRLF file read as LF
        # and written back changed every line.
        with tempfile.TemporaryDirectory() as directory:
            scratch = Path(directory) / "analysed"
            ancestors = {}
            for relative in relatives:
                content = analysed(relative)
                current = worktree_root / relative
                if content is None or not current.is_file():
                    return None
                ancestors[relative] = content
                path = scratch / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                write_exact(path, content)
            ok, changes, _ = self._apply_coordinated_edits(
                solution, mapper, scratch,
            )
            if not ok:
                return None
            merged: dict[str, str | None] = {}
            base_text: dict[str, str] = {}
            for relative, ancestor in ancestors.items():
                patched_path = scratch / relative
                ours = read_exact(worktree_root / relative).replace("\r\n", "\n")
                base_text[relative] = ours
                if not patched_path.exists():
                    # The edits removed the file: only safe if the base
                    # still holds exactly what was removed.
                    if ours != ancestor:
                        return None
                    merged[relative] = None
                    continue
                paths = {}
                for name, text in (("ours", ours), ("base", ancestor)):
                    paths[name] = Path(directory) / f"{name}.txt"
                    write_exact(paths[name], text)
                # Run in the scratch directory: from inside a checkout git
                # reads that repository first, and one it refuses (a /mnt/c
                # worktree of another owner, 2026-10-30) failed every merge.
                completed = subprocess.run(
                    ["git", "merge-file", "-p", str(paths["ours"]),
                     str(paths["base"]), str(patched_path)],
                    capture_output=True, cwd=directory,
                )
                if completed.returncode != 0:
                    return None
                merged[relative] = completed.stdout.decode("utf-8", errors="surrogateescape")
            created = {
                str(edit.get("source_file_path", "")):
                    read_exact(scratch / str(edit.get("source_file_path", "")))
                for edit in solution.get("coordinated_edits", []) or []
                if isinstance(edit, dict) and edit.get("edit_kind") == "create"
            }
        if any((worktree_root / relative).exists() for relative in created):
            return None
        for relative, text in merged.items():
            target = worktree_root / relative
            if text is None:
                target.unlink()
            else:
                write_source(target, text)
        for relative, text in created.items():
            target = worktree_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_source(target, text)
        for change in changes:
            change["rebased_onto_base"] = True
            # The merge is clean when the base already made the same change,
            # and that edit then adds nothing to the diff (Grocy, 2026-10-23:
            # master had already dropped TLSv1.1).
            if already_on_base(change, base_text.get(str(change.get("source_file", "")), "")):
                change["already_on_base"] = True
        return changes

    def _refresh_summary(self, solution: Dict, result: Dict) -> None:
        """Rewrite the pull request summary for the edits that were applied.

        Without a model client the old summary is kept and marked stale, and
        the PR step refuses to publish it (pr_generator: text_problems).
        """
        provider = self._port_provider()
        if provider is None:
            result["summary_stale"] = True
            return
        from cognalys.llm.finding_adjudication import (
            compose_summary_for_applied_edits, summary_error_from_solution)

        error = summary_error_from_solution(solution)
        edits = [
            {"source_file_path": change.get("source_file", ""),
             "reason": change.get("reason", ""),
             "original_code": change.get("original_code", ""),
             "corrected_code": change.get("corrected_code", "")}
            for change in result.get("coordinated_edits") or []
        ] or [{
            "source_file_path": solution.get("source_file_path", ""),
            "reason": "",
            "original_code": result.get("original_method_body", ""),
            "corrected_code": result.get("corrected_method_body", ""),
        }]
        result["explanation"] = compose_summary_for_applied_edits(
            provider, self.logger, error, edits)
        result["summary_refreshed"] = True

    def _port_provider(self):
        """The model client for base_port, created once; None when disabled."""
        if not self.config.port_model:
            return None
        if getattr(self, "_port_client", None) is None:
            from cognalys.llm.llm_providers import remote_provider
            self._port_client = remote_provider(
                endpoint=self.config.port_endpoint,
                model=self.config.port_model,
                timeout=self.config.port_timeout,
                max_output_tokens=self.config.port_max_output_tokens,
                reasoning_effort=self.config.port_reasoning_effort,
                cache_dir=Path(self.config.output_dir) / ".llm_cache",
                logger=self.logger,
                prompt_log_dir=(Path(self.config.port_prompt_log_dir)
                                if self.config.port_prompt_log_dir else None),
            )
        return self._port_client

    def _write_port_refusal_issue(self, solution: Dict, base_repo: Path, port: Dict) -> None:
        """An issue for a violation still on the base whose only port a gate refused.

        DSub2000's SSLContext (subset_gates) ended as "not applicable"
        and reached nobody: the base still installed the accept-all
        manager, and every port was refused because it removed what the
        user's "allow insecure" setting turns on. That is a decision for the
        maintainer, which is what an issue is for.
        """
        from cognalys.llm.fix_generation_common import post_validation_issue_candidate

        try:
            remote = subprocess.run(
                ["git", "-C", str(base_repo), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=30,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            remote = ""
        match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote)
        if not match:
            return
        error = str(port.get("error", ""))
        blockers = [code for code in re.findall(r"\b[A-Z][A-Z_]{5,}\b", error)
                    if code != "PORT_VIOLATES_SEMANTIC_GATES"]
        record = {}
        try:
            record = json.loads(Path(str(solution.get("solution_file", ""))).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        analysis = record.get("evidence_analysis") or solution.get("evidence_analysis") or {}
        index = (record.get("solution_metadata") or {}).get("error_index", solution.get("error_index", 0))
        cause = str(analysis.get("confirmed_cause") or "").strip()
        method = str(solution.get("method_name", ""))
        spec = str(solution.get("spec", ""))
        if not cause or not method:
            return
        # A maintainer knows the class, not the JVM's name for its constructor.
        named = (str(solution.get("csv_class", "")).split("$", 1)[0].rsplit(".", 1)[-1]
                 if method in {"<init>", "<clinit>"} else "") or method
        issue = post_validation_issue_candidate(None, blockers, named, spec, cause, "v2")
        safe_method = re.sub(r"[^A-Za-z0-9_.-]+", "_", method)
        apk = str(solution.get("apk_name", ""))
        path = (Path(self.config.error_solutions_dir) / "issue_candidates" / apk
                / f"{index}_{safe_method}_issue.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate = {
            "issue_kind": "generation_blocked",
            "analysis_decision": str(analysis.get("decision", "")),
            "generation_blockers": blockers,
            "repo_host": "github.com", "repo_owner": match.group(1),
            "repo_name": match.group(2), "apk_name": apk,
            "issue_target": "application_repository",
            "application_repository": f"https://github.com/{match.group(1)}/{match.group(2)}",
            "method_name": method, "csv_class": solution.get("csv_class", ""),
            "source_file": solution.get("source_file_path", ""),
            "spec": spec, "tool": solution.get("tool", ""),
            "error_message": solution.get("error_message", ""),
            "confirmed_cause": cause,
            "issue_content": issue,
            "port_refusal": error[:600],
        }
        # Its summary is the adjudication's cause sentence: rewritten when it
        # fails the issue's text checks (issues.issue_text).
        from cognalys.issues.issue_text import rewrite_issue_text

        rewrite_issue_text(self._port_provider(), self.logger, candidate, analysis)
        path.write_text(json.dumps(candidate, indent=2, ensure_ascii=False), encoding="utf-8")
        self.logger.info("Issue candidate prepared from a refused port: %s", path)

    def _port_to_base(self, solution: Dict, mapper: SolutionMapper,
                      worktree_root: Path, base_repo: Path, base_ref: str,
                      result: Dict) -> Dict:
        """Carry a patch whose code changed upstream onto the base; see base_port.

        Records the attempt on ``result["base_port"]``. A model that finds the
        violation gone from the base leaves the finding not applicable, with
        its reason, rather than failed.
        """
        provider = self._port_provider()
        if provider is None or not base_ref:
            return {}
        from cognalys.solutions.base_port import port_to_base

        def apply(ported: Dict):
            if ported.get("coordinated_edits"):
                return self._apply_coordinated_edits(ported, mapper, worktree_root)
            from cognalys.solutions.solution_mapper import APPLIED

            source = worktree_root / str(ported.get("source_file_path", ""))
            replaced = mapper.replacer.replace_method_status(
                source_file=source,
                original_method_body=ported.get("original_method_body", ""),
                new_method_body=ported.get("corrected_method_body", ""),
            )
            ok = replaced == APPLIED
            return ok, [], "" if ok else replaced

        def semantic_check(ported: Dict):
            from cognalys.validation.repair import semantic_regressions
            edits = ported.get("coordinated_edits") or [{
                "source_file_path": ported.get("source_file_path", ""),
                "original_code": ported.get("original_method_body", ""),
                "corrected_code": ported.get("corrected_method_body", ""),
            }]
            return semantic_regressions(worktree_root, solution, [
                {"source_file": edit.get("source_file_path", ""),
                 "symbol": edit.get("symbol", ""),
                 "original_code": edit.get("original_code", ""),
                 "corrected_code": edit.get("corrected_code", ""),
                 "reason": edit.get("reason", "")}
                for edit in edits if edit.get("edit_kind") != "create"
            ])

        reviews: List[Dict] = []

        def reviewed_check(ported: Dict):
            """The semantic gates, then, for a port that rewrote the correction,
            the behaviour review under the same rules: a regression or a patch
            without effect rejects the port (it goes back once with the
            scenario); a maintainer decision is a note; a review that does not
            run rejects nothing and is recorded."""
            objections = semantic_check(ported)
            if objections or not port_changes_correction(solution, ported):
                return objections
            review = self._review_port(solution, ported, worktree_root, provider)
            reviews.append({key: review.get(key) for key in (
                "status", "regressions", "patch_has_no_effect", "no_effect_reason",
                "maintainer_decision_required", "maintainer_decision",
                "review_contract_version") if key in review})
            found = []
            for item in review.get("regressions") or []:
                found.append({"code": "PORT_BEHAVIOR_REGRESSION",
                              "details": [str(item.get("scenario", ""))]})
            if review.get("patch_has_no_effect") and not review.get("explicit_default_only"):
                found.append({"code": "PORT_PATCH_HAS_NO_EFFECT",
                              "details": [str(review.get("no_effect_reason", ""))]})
            return found

        try:
            port = port_to_base(provider, solution, base_repo, worktree_root,
                                base_ref, apply, reviewed_check,
                                max_corrections=2 if solution.get("stored_value_contract") else 1)
        except Exception as exc:  # noqa: BLE001 -- a port is an extra chance
            self.logger.warning("%s: porting to the base failed: %s",
                                solution.get("method_name", ""), exc)
            return {}
        result["base_port"] = {
            key: port.get(key) for key in
            ("decision", "applied", "explanation", "error", "attempts")
            if key in port
        }
        if reviews:
            # Every behaviour review of a port that rewrote the correction;
            # the last is the one the applied port passed, if it applied.
            result["base_port"]["behavior_reviews"] = reviews
        if port.get("applied"):
            self.logger.info(
                "%s/%s: the reviewed patch no longer applied on the base and "
                "was ported onto it", solution.get("apk_name", ""),
                solution.get("method_name", ""),
            )
        elif (port.get("decision") == "ABSTAIN"
              and str(port.get("error", "")).startswith("PORT_VIOLATES_SEMANTIC_GATES")):
            self._write_port_refusal_issue(solution, base_repo, port)
        elif port.get("decision") == "ALREADY_FIXED_UPSTREAM" and not (
                confirmed := port_already_fixed_confirmation(solution, base_repo, base_ref)):
            # Not confirmed: the model's answer is an abstention, and the
            # finding stays a generated correction a person reads, never a
            # correction "not applicable" on the model's word alone.
            self.logger.warning(
                "%s: the port answered ALREADY_FIXED_UPSTREAM and no deterministic "
                "check confirms it; treated as ABSTAIN", solution.get("method_name", ""))
            port = {**port, "decision": "ABSTAIN", "error": PORT_ALREADY_FIXED_UNCONFIRMED,
                    "model_decision": "ALREADY_FIXED_UPSTREAM"}
            result["base_port"] = {
                key: port.get(key) for key in
                ("decision", "applied", "explanation", "error", "attempts", "model_decision")
                if key in port
            }
        elif port.get("decision") == "ALREADY_FIXED_UPSTREAM":
            result["base_port"]["confirmation"] = confirmed
            result["status"] = "NOT_APPLICABLE_TO_BASE"
            result["error"] = "ORIGINAL_CODE_ABSENT_FROM_BASE_BRANCH"
            result["contribution_base"] = {
                "base_commit": base_ref,
                "source_file_path": solution.get("source_file_path", ""),
                "target_present": True, "analyzed_code_present": False,
                "violation_absent_per_port": True,
            }
        return port

    def _review_port(self, solution: Dict, ported: Dict, worktree_root: Path,
                     provider) -> Dict:
        """The behaviour review of a ported patch, with the port's model client.

        Built as the draft's review was (FixGenerator.review_patch_behavior):
        the finding and its evidence analysis from the solution file, the
        ported edits, and the files they edit as they are on the base.
        """
        from cognalys.llm.generate_error_fixes import FixGenerator

        record = recorded_solution_record(solution)
        error = {**(record.get("original_error") or {}),
                 **(record.get("solution_metadata") or {}),
                 "evidence_analysis": record.get("evidence_analysis") or {},
                 "repository_path": str(worktree_root),
                 "method_name": solution.get("method_name", ""),
                 "source_file_path": solution.get("source_file_path", "")}
        edits = [dict(edit) for edit in ported.get("coordinated_edits") or []] or [{
            "source_file_path": ported.get("source_file_path", ""),
            "symbol": ported.get("method_name", ""), "edit_kind": "method",
            "original_code": ported.get("original_method_body", ""),
            "corrected_code": ported.get("corrected_method_body", ""), "reason": ""}]
        sources = {}
        for edit in edits:
            path = worktree_root / str(edit.get("source_file_path", ""))
            if path.is_file():
                sources[str(edit.get("source_file_path", ""))] = path.read_text(
                    encoding="utf-8", errors="replace")
        reviewer = object.__new__(FixGenerator)
        reviewer.provider = provider
        reviewer.logger = self.logger
        reviewer.output_dir = Path(self.config.output_dir)
        try:
            return reviewer.review_patch_behavior(error, edits, sources)
        except Exception as exc:  # noqa: BLE001 -- recorded, the port is not rejected for it
            self.logger.warning("%s: the behaviour review of the port failed: %s",
                                solution.get("method_name", ""), exc)
            return {"status": "not_run", "regressions": []}

    def _relocate_moved_sources(self, solution: Dict, base_repo: Path,
                                base_ref: str, relative_source: str,
                                original_method_body: str) -> str:
        """Point the solution at the files' new paths when the base moved them.

        The reported file is relocated only when the reviewed body is still in
        the file it moved to (see utils.contribution_base.relocated_path).
        Every other path the solution names -- coordinated edits, the
        authorised plan they are checked against -- follows the same renames,
        so a test that moved with its module moves with it; whether each
        edit's original code is still there is left to the usual check when
        the edits are applied.

        Returns the new path of the reported file, or "" with the solution
        untouched.
        """
        renames = renamed_paths(base_repo, "HEAD", base_ref)
        relocated = relocated_path(
            base_repo, "HEAD", base_ref, relative_source,
            original_method_body, renames=renames,
        )
        if not relocated:
            return ""
        solution["source_file_path"] = relocated
        created = _created_file_relocations(
            base_repo, base_ref, relative_source, relocated,
            [edit for edit in solution.get("coordinated_edits") or []
             if isinstance(edit, dict) and edit.get("edit_kind") == "create"],
        )
        plan = solution.get("authorized_patch_plan") or {}
        for item in [*(solution.get("coordinated_edits") or []),
                     *(plan.get("required_edits") or [])]:
            if not isinstance(item, dict):
                continue
            path = Path(str(item.get("source_file_path", ""))).as_posix()
            if path in renames:
                item["source_file_path"] = renames[path]
            elif path in created:
                new_path, old_package, new_package = created[path]
                item["source_file_path"] = new_path
                if item.get("edit_kind") == "create":
                    item["corrected_code"] = _with_package_moved(
                        str(item.get("corrected_code", "")),
                        old_package, new_package,
                    )
        return relocated

    def _classify_base_drift(self, result: Dict, base_repo: Path,
                             base_commit: str, relative_source: str,
                             original_method_body: str,
                             apk_name: str, method_name: str) -> None:
        """Separate "the branch moved on" from "the patch is wrong".

        A patch that will not apply has two very different causes. Either the
        solution's original code was never in this repository -- a real
        failure -- or it was, at the revision the finding was analysed at, and
        the contribution base has since changed it. Element Android's
        MainActivity.kt is on develop, so the file check passed, but develop
        has carried the fix for CVE-2024-26131 since 1.6.12 and the reviewed
        v1.6.10 body is gone. That is not a failed run; there is nothing left
        there to patch.

        The two are told apart by asking where the original body does exist:
        present at the analysed revision and absent from the base means the
        base moved. Present at neither means the solution is wrong, and the
        result is left as the failure it is.
        """
        if not str(result.get("error", "")).startswith((
            "ORIGINAL_CODE_NOT_FOUND", "PATCH_FAILED",
        )):
            return
        if not original_method_body.strip() or not base_commit:
            return
        at_base = code_exists_at(
            base_repo, base_commit, relative_source, original_method_body,
        )
        at_analyzed = code_exists_at(
            base_repo, "HEAD", relative_source, original_method_body,
        )
        if at_base is not False or at_analyzed is not True:
            return
        result["status"] = "NOT_APPLICABLE_TO_BASE"
        result["error"] = "ORIGINAL_CODE_ABSENT_FROM_BASE_BRANCH"
        result["contribution_base"] = {
            "base_branch": result.get("base_branch", ""),
            "base_commit": base_commit,
            "source_file_path": relative_source,
            "target_present": True,
            "analyzed_code_present": False,
        }
        self.logger.warning(
            "%s/%s cannot be contributed: the reviewed code is present at the "
            "analysed revision but no longer on the base branch %s, so there "
            "is nothing there to patch. For an advisory finding this usually "
            "means the project already shipped the fix.",
            apk_name, method_name, result.get("base_branch", "unknown"),
        )

    def _generate_execution_report(self) -> Dict:
        """Generate execution report."""

        details = details_with_other_projects(
            read_earlier_execution_report(
                Path(self.config.output_dir) / "execution_report.json"
            ),
            self.results,
            scoped_project(self.config.error_solutions_dir),
        )

        successfully_applied = sum(
            1
            for r in details
            if r.get("status") == "SUCCESS"
        )

        failed = sum(
            1
            for r in details
            if r.get("status") == "FAILED"
        )

        dry_run = sum(
            1
            for r in details
            if r.get("status") == "DRY_RUN"
        )

        review_only = sum(
            1
            for r in details
            if r.get("status") == "REVIEW_ONLY"
        )

        # A finding whose file does not exist on the contribution base branch
        # was never appliable there. Counting it as a failure reported a whole
        # run as FAILED for something no rerun can fix.
        not_applicable = sum(
            1
            for r in details
            if r.get("status") == "NOT_APPLICABLE_TO_BASE"
        )

        elapsed_time = (
            datetime.now() - self.start_time
        ).total_seconds() if self.start_time else 0

        report = {

            "execution_timestamp": (
                datetime.now(timezone.utc).isoformat()
            ),

            # The exit code is read from this status, so it describes what
            # this invocation did; the counts below also cover the projects
            # earlier invocations of the run recorded.
            "workflow_status": workflow_status(self.results),

            "execution_time_seconds": elapsed_time,

            "config": {
                "error_solutions_dir": (
                    self.config.error_solutions_dir
                ),
                "cloned_projects_dir": (
                    self.config.cloned_projects_dir
                ),
                "output_dir": self.config.output_dir,
                "worktrees_dir": self.config.worktrees_dir,
                "save_backups": self.config.save_backups,
                "backup_dir": self.config.backup_dir,
                "dry_run": self.config.dry_run,
                "max_solutions": self.config.max_solutions,
                "git_user_name": self.config.git_user_name,
                "git_user_email": self.config.git_user_email,
            },

            "total_solutions": len(details),

            "results": {
                "successfully_applied": (
                    successfully_applied
                ),

                "failed": failed,

                "dry_run": dry_run,
                "review_only_not_applied": review_only,
                "not_applicable_to_base_branch": not_applicable,
            },

            "per_solution_details": details,

            "errors": self.errors[:10]
        }

        return report

    def _save_execution_report(
        self,
        report: Dict
    ) -> None:
        """Save execution report to file."""

        report_path = (
            Path(self.config.output_dir)
            / "execution_report.json"
        )

        shape_issues = validate_execution_report(report)
        if shape_issues:
            self.logger.warning(
                "execution_report.json shape check found issues: %s",
                "; ".join(shape_issues),
            )

        write_execution_report_atomic(report_path, report)

        self.logger.info(
            f"Execution report saved to "
            f"{report_path}"
        )

        self.logger.info("=" * 60)
        self.logger.info("EXECUTION SUMMARY")
        self.logger.info("=" * 60)

        self.logger.info(
            f"Successfully Applied: "
            f"{report['results']['successfully_applied']}"
        )

        self.logger.info(
            f"Failed: "
            f"{report['results']['failed']}"
        )

        self.logger.info(
            f"Total Time: "
            f"{report['execution_time_seconds']:.2f}s"
        )
