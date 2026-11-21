"""Create and reuse one safe Git worktree per finding."""

from __future__ import annotations

import hashlib
import logging
import re
import subprocess
from pathlib import Path

from cognalys.utils.contribution_base import base_branch_name, resolve_base_ref


class WorktreeManager:
    def __init__(self, root: Path, logger: logging.Logger):
        self.root = Path(root).resolve()
        self.logger = logger
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _git(repo: Path, *args: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed: {result.stderr.strip()}"
            )
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def _has_only_line_ending_changes(repo: Path) -> bool:
        """True if every uncommitted change is a pure CRLF/LF flip.

        A base clone made before this project pinned core.autocrlf=false on
        clone (see extract_methods_single_csv.py) has every tracked text
        file reported as modified by `git status --porcelain`, purely from
        line-ending normalization -- not real edits. `git diff
        --ignore-all-space --quiet` is blind to that same normalization, so
        it exits 0 only when there is no actual content difference. Any
        untracked file is treated as real, unexpected state and left alone.
        """
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo,
            capture_output=True, text=True,
        ).stdout
        if any(line.startswith("??") for line in porcelain.splitlines()):
            return False
        unstaged_clean = subprocess.run(
            ["git", "diff", "--ignore-all-space", "--quiet"], cwd=repo,
        ).returncode == 0
        staged_clean = subprocess.run(
            ["git", "diff", "--cached", "--ignore-all-space", "--quiet"], cwd=repo,
        ).returncode == 0
        return unstaged_clean and staged_clean

    @staticmethod
    def _safe(value: str, fallback: str) -> str:
        normalized = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-.")
        return normalized[:80] or fallback

    @classmethod
    def branch_method_and_spec(cls, solution: dict) -> tuple[str, str]:
        """The method and specification a finding's branch is named after.

        The pull request's: the finding it is attributed to when the reported
        one has no effect (LocalSend run_v4, 2026-10-31: the branch read
        fix/loadorcreatekeystore-keypairspec-... for a patch that changed only
        the KeyStoreSpec call). The finding id and the worktree path stay the
        analysed finding's.
        """
        from cognalys.utils.pr_text import attributed_view

        named = attributed_view(solution, solution.get("pr_attribution"))
        return (cls._safe(str(named.get("method_name", "change")), "change"),
                cls._safe(str(named.get("spec", "finding")), "finding"))

    @staticmethod
    def finding_id(solution: dict) -> str:
        identity = str(solution.get("unique_error_id", "")) or "|".join(
            str(solution.get(key, ""))
            for key in ("apk_name", "csv_class", "method_name", "spec")
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def solution_revision(solution: dict) -> str:
        edits = solution.get("coordinated_edits", [])
        if isinstance(edits, list) and edits:
            identity = [
                {
                    "path": item.get("source_file_path", ""),
                    "kind": item.get("edit_kind", ""),
                    "original": item.get("original_code_sha256", "")
                    or hashlib.sha256(str(item.get("original_code", "")).encode(
                        "utf-8"
                    )).hexdigest(),
                    "corrected": hashlib.sha256(str(item.get(
                        "corrected_code", ""
                    )).encode("utf-8")).hexdigest(),
                }
                for item in edits if isinstance(item, dict)
            ]
        else:
            identity = {
                "original": solution.get("original_method_body", ""),
                "corrected": solution.get("corrected_method_body", ""),
            }
        canonical = repr(identity).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()[:10]

    def _base_ref(self, repo: Path, solution: dict) -> tuple[str, str]:
        """The branch a pull request targets, and a ref that can be read.

        The precedence lives in utils.contribution_base so the extraction
        stage can ask the same question before a run spends anything: it has
        to decide whether the reported file still exists where a fix would be
        contributed, and a second copy of this rule would be a second rule.
        """
        base = base_branch_name(repo, solution.get("branch_policy", {}))
        if not base:
            raise RuntimeError("Could not determine a contribution base branch")
        ref = resolve_base_ref(repo, base, fetch=True)
        if not ref:
            raise RuntimeError(f"Base branch '{base}' is unavailable")
        return base, ref

    def prepare(self, repo: Path, solution: dict) -> dict:
        repo = repo.resolve()
        try:
            self.root.relative_to(repo)
        except ValueError:
            pass
        else:
            raise RuntimeError("Worktrees directory must be outside the base repository")
        if self._git(repo, "status", "--porcelain"):
            if self._has_only_line_ending_changes(repo):
                self.logger.warning(
                    "Base clone %s has only line-ending noise (pre-dates "
                    "core.autocrlf=false cloning); normalizing with "
                    "'git checkout -- .' before creating the worktree",
                    repo,
                )
                try:
                    self._git(
                        repo, "-c", "core.autocrlf=false", "checkout", "--", ".",
                    )
                except RuntimeError:
                    # A repo whose diff cache disagrees with its own working
                    # tree (e.g. content removed outside git) can pass the
                    # line-ending-only check above yet still reject a plain
                    # checkout. Fall through to the standard diagnostic below
                    # instead of surfacing a confusing git plumbing error.
                    pass
            if self._git(repo, "status", "--porcelain"):
                raise RuntimeError(
                    "Base clone has uncommitted changes; worktree creation "
                    "requires a clean source repository"
                )
        finding = self.finding_id(solution)
        revision = self.solution_revision(solution)
        project = self._safe(str(solution.get("apk_name", "project")), "project")
        method, spec = self.branch_method_and_spec(solution)
        branch_prefix = f"fix/{method}-{spec}-{finding}-{revision}"
        path_prefix = (self.root / project / finding / revision).resolve()
        branch = branch_prefix
        path = path_prefix
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise RuntimeError("Unsafe worktree target") from exc

        registered = {}
        current_path = ""
        for line in self._git(repo, "worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                current_path = line[9:]
                registered.setdefault(current_path, "")
            elif line.startswith("branch ") and current_path:
                registered[current_path] = line[7:].removeprefix("refs/heads/")
        resolved_registered = {
            str(Path(key).resolve()): value for key, value in registered.items()
        }
        base_branch, base_ref = self._base_ref(repo, solution)
        base_commit = self._git(repo, "rev-parse", base_ref)
        attempt = 1
        orphaned_paths = []
        while True:
            if attempt > 100:
                raise RuntimeError(
                    "Could not allocate a finding worktree after 100 attempts"
                )
            suffix = "" if attempt == 1 else f"-{attempt}"
            path = Path(str(path_prefix) + suffix).resolve()
            branch = branch_prefix + suffix
            registered_branch = resolved_registered.get(str(path), "")
            existing_branch_path = next(
                (key for key, value in resolved_registered.items()
                 if value == branch), ""
            )
            if existing_branch_path and existing_branch_path != str(path):
                attempt += 1
                continue
            if registered_branch:
                if registered_branch != branch:
                    attempt += 1
                    continue
                clean = not self._git(path, "status", "--porcelain")
                at_base = self._git(path, "rev-parse", "HEAD") == base_commit
                if clean and at_base:
                    self.logger.info("Reusing pristine finding worktree %s", path)
                    break
                self.logger.info(
                    "Existing finding worktree %s contains a prior application; "
                    "allocating a fresh attempt", path,
                )
                attempt += 1
                continue
            if path.exists():
                orphaned_paths.append(str(path))
                self.logger.warning(
                    "Preserving unregistered worktree path %s as an orphan; "
                    "allocating a fresh attempt",
                    path,
                )
                attempt += 1
                continue
            branch_exists = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet",
                 f"refs/heads/{branch}"], cwd=repo,
            ).returncode == 0
            if branch_exists:
                branch_head = self._git(repo, "rev-parse", branch)
                if branch_head != base_commit:
                    attempt += 1
                    continue
            break

        if str(path) not in resolved_registered:
            path.parent.mkdir(parents=True, exist_ok=True)
            branch_exists = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet",
                 f"refs/heads/{branch}"], cwd=repo,
            ).returncode == 0
            args = ["worktree", "add"]
            if not branch_exists:
                args.extend(["-b", branch])
            args.append(str(path))
            args.append(branch if branch_exists else base_ref)
            self._git(repo, *args)
            self.logger.info("Created finding worktree %s on %s", path, branch)
        return {
            "finding_id": finding,
            "branch": branch,
            "base_branch": base_branch,
            "base_commit": base_commit,
            "solution_revision": revision,
            "worktree_attempt": attempt,
            "orphaned_worktree_paths": orphaned_paths,
            "worktree_path": str(path),
            "base_repository_path": str(repo),
        }
