#!/usr/bin/env python3

"""
Fully Automated GitHub PR Creator

Features:
- scans pull_requests directory
- auto forks repositories
- configures git remotes
- pushes branches
- creates pull requests
- saves GitHub API responses
- skips already processed PRs

Usage:
    export GITHUB_TOKEN=ghp_xxxxxxxxx
    python3 auto_pr_creator.py
"""

import json
import os
import re
import time
import subprocess
import argparse
import base64
from cognalys.utils.step_outcome import step_exit_code
from cognalys.utils.network_git import run_git_command
from cognalys.utils.pr_text import finding_specs, text_problems
from cognalys.prs.pr_template import (
    PullRequestTemplate, body_for_repository, facts_from_pull_request,
    problems_in_own_words, template_in_checkout, template_instructions_left,
    template_on_github,
)
from cognalys.llm.fix_artifacts import with_recorded_review_notes
from cognalys.prs.refresh_text import description_after_follow_ups
from cognalys.prs.publication_checks import (
    publication_blockers,
    published_pull_request,
    review_absent_by_design,
    stale_review,
)
from cognalys.utils.disposition import (
    BUILD_VALIDATION_PASSING_STATUSES,
    COMPILE_ONLY_PASS,
    PRODUCTION_ONLY_TEST_BUILD_PREEXISTING,
    FindingDisposition,
    build_says_nothing_about_patch,
    repaired_patch_review_blocks,
)
from pathlib import Path

try:
    from cognalys.prs.pr_outcomes import (
        open_pull_request_for, publication_search_root, recorded_pull_request_state,
        records_publication, rejected_outcome_for,
    )
except ModuleNotFoundError:  # run as a script from src/prs
    from cognalys.prs.pr_outcomes import (
        open_pull_request_for, publication_search_root, recorded_pull_request_state,
        records_publication, rejected_outcome_for,
    )

try:
    import requests
except ModuleNotFoundError:
    requests = None


def follow_up_record_for(json_file: Path) -> Path:
    """The pr_followup record of a ``*_pr.json`` (written next to it by default)."""
    json_file = Path(json_file)
    return json_file.with_name(json_file.name.removesuffix("_pr.json") + "_followup.json")


def pushed_follow_ups(follow_up: dict, recorded: str, head: str) -> list:
    """The pushed follow-up rounds that took a pull request from ``recorded`` to ``head``.

    NotallyX#1167 (2026-10-30): the SHA-256 test-vector commit CodeRabbit asked
    for is pushed by pr_followup on top of the commit the PR record describes.
    The description update then saw a head it did not know and refused the
    record as stale, although the only commits after it were our own,
    verified and built. Rounds are accepted only as an unbroken chain: the
    first started from ``recorded``, each next one from the commit before it,
    the last pushed is ``head``, and every one has a passing build. Anything
    else (a maintainer's commit, a force-push, a round without its diff)
    returns [] and the record stays stale.
    """
    if not isinstance(follow_up, dict) or not recorded or not head:
        return []
    rounds = [item for item in (follow_up.get("previous_rounds") or []) if isinstance(item, dict)]
    rounds.append(follow_up)
    chain, base = [], recorded
    for item in rounds:
        if item.get("status") != "PUSHED":
            continue
        if str(item.get("head_sha", "")) != base or not item.get("commit"):
            return []
        if not item.get("committed_diff"):
            return []
        validation = item.get("build_validation") or {}
        if str(validation.get("status", "")).upper() != "SUCCESS":
            return []
        chain.append(item)
        base = str(item["commit"])
    return chain if chain and base == head else []


def metadata_with_follow_ups(metadata: dict, rounds: list) -> dict:
    """The PR record as the pull request stands after its pushed follow-ups.

    The diff and changed files of each round are added to the record's own,
    so a template box is ticked from the whole change (a test file a
    follow-up added ticks "tests", a log call it added unticks "no logs").
    The build facts stay the record's: every round was pushed only after its
    own passing build of the files it changed (pushed_follow_ups checks it).
    """
    if not rounds:
        return metadata
    merged = dict(metadata)
    merged["diff"] = "\n".join([str(metadata.get("diff", "") or "")]
                               + [str(item.get("committed_diff", "")) for item in rounds])
    merged["changed_files"] = list(dict.fromkeys(
        [str(path) for path in metadata.get("changed_files") or []]
        + [str(path) for item in rounds for path in item.get("changed_files") or []]))
    return merged


# ==========================================================
# CONFIG
# ==========================================================

PULL_REQUESTS_DIR = "pull_requests"

GITHUB_API = "https://api.github.com"


def production_only_build_note(build_validation: dict) -> str:
    """The operator's note for a pass that verified production code only.

    validate_applied_solutions records it when a module's test build fails
    at the same tasks on the base commit, before any test compiles; the tests
    the patch created there were removed from the branch. A patch test in
    another module whose test task passed is kept, so the note claims nothing
    about tests in general: only that the failing test build says nothing
    about the patch. The operator is told which tasks failed and which generated
    tests are gone, since neither is in the diff.
    """
    failed = [str(task) for task in build_validation.get("failed_test_tasks") or []]
    removed = [str(path) for path in build_validation.get("removed_generated_tests") or []]
    return (
        "NOTE: only the production build was verified; the test build fails "
        "the same way on the base commit without the patch"
        + (f" (failed test task(s): {', '.join(failed)})" if failed else "")
        + ", so that test build says nothing about the patch."
        + (" Generated test(s) removed from the branch: " + ", ".join(removed) + "."
           if removed else "")
    )


# ==========================================================
# AUTOMATED PR CREATOR
# ==========================================================

class AutomatedPRCreator:

    def __init__(
        self,
        pull_requests_dir=PULL_REQUESTS_DIR,
        github_token=None,
        github_api=GITHUB_API,
        fork_wait_seconds=120,
        dry_run=False,
        skip_existing=True,
        base_branch=None,
        limit=0,
        project="",
        approved_reviewed_findings=None,
        publish_without_build_validation=None,
        allow_duplicate_pr=False,
        allow_text_problems=False,
        allow_ai_policy=False,
        allow_changed_upstream=False,
        allow_closed_repository=False,
        allow_stale_review=False,
        update_description=False,
        body_out=None,
        text_model=None,
    ):

        # Fill the repository's template into the description of a pull
        # request already published, instead of publishing one
        # (update_published_description). body_out: a directory that
        # receives the body each one would send.
        self.update_description = update_description
        self.body_out = Path(body_out) if body_out else None
        # remote_provider arguments of the model that revises a published
        # description for the follow-up commits pushed after it
        # (description_writer); built only when there are such commits.
        self.text_model = dict(text_model or {})
        self._description_writer = None

        self.github_token = os.getenv(
            "GITHUB_TOKEN"
        ) if github_token is None else github_token

        self.github_api = github_api.rstrip("/")

        self.fork_wait_seconds = fork_wait_seconds

        self.dry_run = dry_run

        self.skip_existing = skip_existing

        self.base_branch = base_branch

        self.limit = limit
        self.project = project.strip()
        # Publishing a second pull request for a finding that already has an
        # open one is refused unless a person asks for it on purpose.
        self.allow_duplicate_pr = allow_duplicate_pr
        # Text that reads like the pipeline (ids, its vocabulary, "Fix
        # <method>") is not published unless a person says it is fine.
        self.allow_text_problems = allow_text_problems
        # A repository's stated rules on AI-assisted contributions, and a
        # default branch that moved on, also stop publication unless waived.
        self.allow_ai_policy = allow_ai_policy
        self.allow_changed_upstream = allow_changed_upstream
        self.allow_closed_repository = allow_closed_repository
        # A draft that did not pass the current behaviour review is refused
        # unless a person publishes it knowing which review it skipped.
        self.allow_stale_review = allow_stale_review
        # Findings published by this invocation, for the overlap check.
        self.published_findings: set = set()
        self.approved_reviewed_findings = {
            str(value).strip() for value in (approved_reviewed_findings or [])
            if str(value).strip()
        }
        # Named per finding, never blanket, and only meaningful alongside an
        # approval for the same id: this widens what that approval accepts,
        # it does not grant one.
        self.publish_without_build_validation = {
            str(value).strip()
            for value in (publish_without_build_validation or [])
            if str(value).strip()
        }

        if requests is None and not self.dry_run:

            raise RuntimeError(
                "Missing dependency: requests. Install it with "
                "`python -m pip install requests`."
            )

        if not self.github_token and not self.dry_run:

            raise ValueError(
                "GitHub token not set. Use --github-token or GITHUB_TOKEN."
            )

        self.headers = {

            "Authorization":
                f"Bearer {self.github_token}",

            "Accept":
                "application/vnd.github+json"
        }
        if not self.github_token:
            # A dry run without a token still reads public repositories;
            # "Bearer None" would turn every read into a 401.
            self.headers.pop("Authorization")

        self.pr_dir = Path(
            pull_requests_dir
        )

        self.username = (
            "DRY_RUN_USER"
            if self.dry_run
            else self.get_authenticated_user()
        )

    # ======================================================
    # EXECUTE COMMAND
    # ======================================================

    def run_command(self, command, cwd, env=None):

        # The publishing push (and any other network git command) is bounded
        # and retried once when it stalls (utils.network_git); a final timeout
        # is code 124 and fails below like any other error. The token header
        # stays in `env`.
        result = run_git_command(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode != 0:

            raise RuntimeError(
                f"\nCommand failed:\n"
                f"{' '.join(command)}\n\n"
                f"STDOUT:\n{result.stdout}\n\n"
                f"STDERR:\n{result.stderr}"
            )

        return result.stdout.strip()

    # ======================================================
    # GET AUTHENTICATED USER
    # ======================================================

    def get_authenticated_user(self):

        response = requests.get(
            f"{self.github_api}/user",
            headers=self.headers
        )

        response.raise_for_status()

        user = response.json()["login"]

        print(f"Authenticated as: {user}")

        return user

    def github_contents(self, url, params=None):
        """JSON of a GitHub API read, or None when it is missing or fails."""
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=30)
        except requests.RequestException:
            return None
        if response.status_code != 200:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    # ======================================================
    # EXTRACT OWNER/REPO
    # ======================================================

    def extract_owner_repo(self, remote_url):

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

                return (
                    match.group(1),
                    match.group(2),
                    match.group(3)
                )

        raise ValueError(
            f"Could not parse remote URL:\n"
            f"{remote_url}"
        )

    def get_remote_url(self, repo_path, remote_name):

        try:

            return self.run_command(
                [
                    "git",
                    "config",
                    "--get",
                    f"remote.{remote_name}.url"
                ],
                cwd=repo_path
            )

        except Exception:

            return ""

    def resolve_original_repository(self, metadata):

        repo_path = metadata["repository"]

        host = metadata.get("repo_host")
        owner = metadata.get("repo_owner")
        repo = metadata.get("repo_name")

        if host and owner and repo:

            return host, owner, repo

        for remote_name in ["upstream", "origin"]:

            remote_url = self.get_remote_url(
                repo_path,
                remote_name
            )

            if not remote_url:

                continue

            return self.extract_owner_repo(
                remote_url
            )

        raise ValueError(
            f"Could not resolve repository owner/name from .git remotes: {repo_path}"
        )

    # ======================================================
    # CANONICAL REPOSITORY
    # ======================================================

    def resolve_canonical_repository(self, owner, repo):
        """The repository's current owner/name, as GitHub reports it now.

        The owner/name recorded at clone time goes stale when a repository is
        renamed or transferred (aivanovski/kpassnotes became
        aivanovski/keepassvault). GitHub keeps redirecting the old API path,
        so forking through it still works -- but the fork is created under
        the current name, and a push to {user}/{old name} finds nothing.
        Every later step therefore uses the name this lookup returns.
        """

        response = requests.get(
            f"{self.github_api}/repos/{owner}/{repo}",
            headers=self.headers,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Could not look up {owner}/{repo} on GitHub "
                f"(HTTP {response.status_code}): {response.text}"
            )

        full_name = str(response.json().get("full_name", "")).strip()
        if "/" not in full_name:
            raise RuntimeError(
                f"GitHub did not report a full name for {owner}/{repo}."
            )

        canonical_owner, canonical_repo = full_name.split("/", 1)
        if full_name.lower() != f"{owner}/{repo}".lower():
            print(
                f"Repository {owner}/{repo} is now {full_name} "
                "(renamed or transferred); using the current name."
            )
        return canonical_owner, canonical_repo

    # ======================================================
    # DETECT DEFAULT BRANCH
    # ======================================================

    def detect_default_branch(self, original_owner, repo):

        response = requests.get(
            f"{self.github_api}/repos/{original_owner}/{repo}",
            headers=self.headers,
        )
        if response.status_code == 200:
            default_branch = response.json().get("default_branch")
            if default_branch:
                return default_branch

        try:
            response.raise_for_status()
        except Exception as exc:
            raise RuntimeError(
                f"Could not determine the default branch for "
                f"{original_owner}/{repo}: {response.text}"
            ) from exc

        raise RuntimeError(
            f"GitHub did not report a default branch for {original_owner}/{repo}."
        )

    # ======================================================
    # CREATE FORK
    # ======================================================

    def create_fork(
        self,
        owner,
        repo
    ):

        print(
            f"Creating fork for "
            f"{owner}/{repo}"
        )

        url = (
            f"{self.github_api}/repos/"
            f"{owner}/{repo}/forks"
        )

        response = requests.post(
            url,
            headers=self.headers
        )

        # The fork is named by GitHub, not by us: it takes the upstream's
        # current name, and gets a suffix when this account already owns an
        # unrelated repository with that name. The response says which.
        if response.status_code in [202, 200]:
            try:
                fork_full_name = str(response.json().get("full_name", "")).strip()
            except ValueError:
                fork_full_name = ""
            if "/" in fork_full_name:
                return fork_full_name

        elif response.status_code not in [403, 422]:

            raise RuntimeError(
                f"Fork creation failed:\n"
                f"{response.text}"
            )

        existing = self.find_existing_fork(owner, repo)
        if existing:
            print(f"Using existing fork {existing}")
            return existing

        raise RuntimeError(
            f"Fork creation for {owner}/{repo} failed "
            f"(HTTP {response.status_code}) and no fork of it was found "
            f"under {self.username}:\n{response.text}"
        )

    def find_existing_fork(self, owner, repo):
        """{username}/{repo}, but only when it really is a fork of owner/repo.

        A same-named repository that is not a fork of this upstream must not
        receive the branch.
        """

        response = requests.get(
            f"{self.github_api}/repos/{self.username}/{repo}",
            headers=self.headers,
        )
        if response.status_code != 200:
            return ""

        data = response.json()
        upstream = f"{owner}/{repo}".lower()
        parents = {
            str((data.get(key) or {}).get("full_name", "")).lower()
            for key in ("parent", "source")
        }
        if data.get("fork") and upstream in parents:
            return str(data.get("full_name", "")).strip()
        return ""

    def wait_for_fork(self, fork_full_name):
        """Poll until the fork has branches, instead of sleeping blindly.

        Forking is asynchronous: the API answers before the repository can
        accept a push. An existing fork is ready on the first check.
        """

        print(f"Waiting for fork {fork_full_name} to be ready...")

        deadline = time.monotonic() + self.fork_wait_seconds
        while True:
            response = requests.get(
                f"{self.github_api}/repos/{fork_full_name}/branches",
                headers=self.headers,
                params={"per_page": 1},
            )
            if response.status_code == 200 and response.json():
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Fork {fork_full_name} was not ready after "
                    f"{self.fork_wait_seconds}s (last HTTP "
                    f"{response.status_code}). Retry later or raise "
                    "--fork-wait-seconds."
                )
            time.sleep(2)

    # ======================================================
    # CONFIGURE REMOTES
    # ======================================================

    def configure_git_remotes(
        self,
        repo_path,
        upstream_full_name,
        fork_full_name
    ):

        print("Configuring git remotes")

        fork_url = (
            f"https://github.com/"
            f"{fork_full_name}.git"
        )

        upstream_url = (
            f"https://github.com/"
            f"{upstream_full_name}.git"
        )

        remotes_output = self.run_command(
            [
                "git",
                "remote"
            ],
            cwd=repo_path
        )

        remotes = remotes_output.splitlines()

        if "origin" in remotes:

            self.run_command(
                [
                    "git",
                    "remote",
                    "remove",
                    "origin"
                ],
                cwd=repo_path
            )

        if "upstream" in remotes:

            self.run_command(
                [
                    "git",
                    "remote",
                    "remove",
                    "upstream"
                ],
                cwd=repo_path
            )

        self.run_command(
            [
                "git",
                "remote",
                "add",
                "origin",
                fork_url
            ],
            cwd=repo_path
        )

        self.run_command(
            [
                "git",
                "remote",
                "add",
                "upstream",
                upstream_url
            ],
            cwd=repo_path
        )

    # ======================================================
    # PUSH BRANCH
    # ======================================================

    def push_branch(
        self,
        repo_path,
        branch
    ):

        print(f"Pushing branch: {branch}")

        # Authenticate only this Git process. The token is passed through the
        # child environment, so it is not stored in the remote URL, Git config,
        # command line, or logs.
        credential = base64.b64encode(
            f"x-access-token:{self.github_token}".encode("utf-8")
        ).decode("ascii")
        git_env = os.environ.copy()
        git_env["GIT_TERMINAL_PROMPT"] = "0"
        git_env["GIT_CONFIG_COUNT"] = "1"
        git_env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        git_env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {credential}"

        self.run_command(
            [
                "git",
                "push",
                "--no-thin",
                "-u",
                "origin",
                branch
            ],
            cwd=repo_path,
            env=git_env,
        )

    # ======================================================
    # CREATE PR
    # ======================================================

    def create_pull_request(
        self,
        metadata,
        original_owner,
        repo,
        head_owner
    ):

        repo_path = metadata["repository"]

        branch = metadata["branch"]

        title = metadata["title"]

        body = metadata["body"]

        base_branch = (
            self.base_branch
            or metadata.get("base_branch")
            or self.detect_default_branch(
                original_owner,
                repo,
            )
        )

        print(
            f"Creating PR "
            f"{branch} -> {base_branch}"
        )

        url = (
            f"{self.github_api}/repos/"
            f"{original_owner}/{repo}/pulls"
        )

        payload = {

            "title": title,

            "body": body,

            "head": (
                f"{head_owner}:{branch}"
            ),

            "base": base_branch
        }

        response = requests.post(
            url,
            headers=self.headers,
            json=payload
        )

        if response.status_code not in [200, 201]:

            raise RuntimeError(
                f"PR creation failed:\n"
                f"{response.text}"
            )

        return response.json()

    def published_in_directory(self, finding_id: str) -> bool:
        """Whether this run's directory records a real publication of ``finding_id``.

        A publication record names the branch, and every branch carries its
        finding id (fix/<method>-<spec>-<finding id>-<revision>).
        """
        if not finding_id:
            return False
        for record in self.pr_dir.glob("*_pr_created.json"):
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not data.get("dry_run") and finding_id in str(data.get("branch", "")):
                return True
        return False

    @staticmethod
    def published_from(json_file: Path) -> bool:
        """Whether ``json_file``'s own creation record names a real pull request."""
        return bool(published_pull_request(json_file))

    @staticmethod
    def rerun_command(run_dir: Path) -> str:
        """The command that runs ``run_dir``'s findings again into a new directory.

        A rerun keeps the earlier run for comparison, so it goes to the next
        free run_vN beside it (run, run2 and run_v3 count alike), from the run
        file that made ``run_dir`` (run_v3.toml for run_v3), with its own
        clones: --run-dir alone keeps the clones shared, and the rerun would
        reset the earlier run's repositories (the queue's run_v2.toml files
        name clones_v2).
        """
        run_dir = Path(run_dir).resolve()
        versions = [1]
        for sibling in run_dir.parent.iterdir() if run_dir.parent.is_dir() else []:
            match = re.fullmatch(r"run(?:_v)?(\d*)", sibling.name)
            if match and sibling.is_dir():
                versions.append(int(match.group(1) or 1))
        version = max(versions) + 1
        run_file = next((path for path in (run_dir.parent / f"{run_dir.name}.toml",
                                           run_dir.parent / "run.toml") if path.is_file()),
                        None)
        clones = ""
        if run_file is not None:
            try:
                import tomllib

                clones = str(tomllib.loads(run_file.read_text(encoding="utf-8"))
                             .get("cloned-projects-dir", "") or "")
            except (OSError, ValueError):
                clones = ""
        clones = (re.sub(r"_v\d+$", "", clones.rstrip("/\\")) if clones
                  else str(run_dir.parent / "clones")) + f"_v{version}"
        return (f"cognalys run {run_file or '<run file>'} "
                f"--run-dir {run_dir.parent / f'run_v{version}'} "
                f"--cloned-projects-dir {clones}")

    def refuses_stale_review(self, metadata: dict, json_file: Path) -> bool:
        """Whether this draft is withheld for predating the current behaviour review.

        A draft already published from this file is not: skip_existing skips
        it, and the refusal asked for a paid rerun of a merged finding.
        """
        if self.skip_existing and self.published_from(json_file):
            return False
        unreviewed = review_absent_by_design(
            metadata.get("fix_scope"), metadata.get("review_contract_version"))
        if unreviewed:
            print(f"NOTE: {unreviewed}. Read the diff before approving it.")
            return False
        stale = stale_review(
            metadata.get("review_contract_version"),
            metadata.get("behavior_review_recorded"),
            fix_scope=metadata.get("fix_scope"),
        )
        if not stale:
            return False
        if self.allow_stale_review:
            print(
                "WARNING: --allow-stale-review: publishing without the current "
                f"behaviour review ({stale}). Its gates -- no-effect patches, "
                "regressions, edits for other findings -- never judged this diff."
            )
            return False
        print(
            "Publication blocked: this draft did not pass the current behaviour "
            f"review ({stale}). Run the finding again and review the new draft:\n"
            f"  {self.rerun_command(Path(json_file).resolve().parent.parent)}\n"
            "or pass --allow-stale-review to publish this one as it is."
        )
        return True

    def with_hand_written_description(self, json_file, metadata: dict) -> dict:
        """The record with the description written by hand beside it, if any.

        `<stem>_body.md`, a first line "# Title" replacing the title. The
        repository's form still applies to it (NotallyX#1167, 2026-10-30): it
        goes under the template's description, unless it already follows the
        template.
        """
        written = Path(json_file).parent / f"{Path(json_file).stem}_body.md"
        if not written.is_file():
            return metadata
        metadata = dict(metadata)
        text = written.read_text(encoding="utf-8").strip()
        first, _, rest = text.partition("\n")
        if first.startswith("# "):
            metadata["title"] = first[2:].strip()
            text = rest.strip()
        metadata["body"] = text
        metadata["body_written_by_contributor"] = True
        print(f"Using the hand-written description in {written.name}")
        template = self.checkout_template(metadata)
        if template:
            body, notes = body_for_repository(template, text, facts_from_pull_request(metadata))
            metadata["body"], metadata["plain_body"] = body, text
            metadata["pull_request_template"] = {"path": template.path, "notes": notes}
        return metadata

    def live_pull_request_state(self, html_url):
        """open / closed / merged for a published pull request, or None."""
        if requests is None or self.dry_run:
            return None
        match = re.search(
            r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", str(html_url)
        )
        if not match:
            return None
        try:
            response = requests.get(
                f"{self.github_api}/repos/{match.group(1)}/{match.group(2)}"
                f"/pulls/{match.group(3)}",
                headers=self.headers,
                timeout=30,
            )
            if response.status_code != 200:
                return None
            data = response.json()
        except (requests.RequestException, ValueError):
            return None
        if data.get("merged_at") or data.get("merged"):
            return "merged"
        return str(data.get("state", "")) or None

    def known_pull_request_state(self, html_url):
        """The live state, else a merge `cognalys pr-outcomes` recorded.

        A merged pull request never reopens, so its recorded merge is enough
        when GitHub cannot be read; a recorded "closed" or "open" is not (a
        closed one can be reopened), and the record then keeps blocking.
        """
        state = self.live_pull_request_state(html_url)
        if state:
            return state
        return "merged" if recorded_pull_request_state(html_url) == "merged" else None

    # ======================================================
    # PROCESS FILE
    # ======================================================

    def process_pr_file(self, json_file):

        print("\n" + "=" * 60)
        print(f"PROCESSING: {json_file.name}")
        print("=" * 60)

        with open(
            json_file,
            "r",
            encoding="utf-8"
        ) as f:

            metadata = json.load(f)

        # The review fields as the preview and the statistics read them: a
        # PR file written before it carried them (KeyPass's getKey, no
        # fix_scope) was refused as stale while its preview called the draft
        # unreviewed by design. The file's own fields, and the hand-written
        # description below, still win.
        metadata = with_recorded_review_notes(metadata, Path(json_file))

        # A description the contributor wrote by hand, kept beside the JSON
        # so it can be edited as Markdown: a first line "# Title" replaces
        # the title. Traffic Light asks that PR descriptions not come from
        # an LLM (exp_final_20261019).
        metadata = self.with_hand_written_description(json_file, metadata)

        # A pull request for this same finding was already closed by the
        # maintainers without merging. Proposing it again ignores what they
        # wrote; the recorded outcome carries their comments.
        rejected = rejected_outcome_for(metadata)
        if rejected:
            print(
                "Publication blocked: a pull request for this finding "
                f"({rejected.get('pull_request', '')}) was closed without "
                "merging. Maintainer feedback: "
                + " | ".join(
                    str(item.get("body", ""))[:300]
                    for item in rejected.get("maintainer_feedback", [])
                )
            )
            return "blocked"

        # Each run rebuilds its solutions, so a finding an earlier run already
        # sent to the maintainers comes back under a new branch. Opening it
        # again would put two pull requests for one change in front of them.
        own_record = json_file.parent / f"{json_file.stem}_created.json"
        already_open = open_pull_request_for(
            metadata, publication_search_root(self.pr_dir),
            own_record=own_record, live_state=self.known_pull_request_state,
            run_root=self.pr_dir.resolve().parent.parent,
        )
        if already_open and not self.allow_duplicate_pr:
            print(
                "Publication blocked: this finding already has a pull "
                f"request that may still be open ({already_open['pull_request']}, "
                f"branch {already_open['branch'] or 'unknown'}, "
                f"{already_open['state']}). Update that pull request instead, "
                "or pass --allow-duplicate-pr to open another on purpose."
            )
            return "blocked"

        # One pull request per change (prs.pr_grouping): never one whose
        # every change another already makes, and never the second of two
        # that rewrite the same method or lines.
        superseded = str(metadata.get("superseded_by", "")).strip()
        if superseded and not self.allow_duplicate_pr:
            print(
                f"Publication blocked: the pull request of finding {superseded} "
                "already makes every change this one makes; publish that one."
            )
            return "blocked"
        resolved_with = str(metadata.get("resolved_with", "")).strip()
        if resolved_with and not self.allow_duplicate_pr:
            print(
                f"Publication blocked: the pull request of finding {resolved_with} "
                "already makes this finding's change; publish that one."
            )
            return "blocked"
        overlapping = [str(value) for value in metadata.get("overlaps_with") or []]
        taken = [value for value in overlapping
                 if value in self.published_findings or self.published_in_directory(value)]
        if taken and not self.allow_duplicate_pr:
            print(
                "Publication blocked: finding(s) " + ", ".join(taken) + " change the "
                "same method or lines and already have a pull request; combine "
                "the two changes into that one instead of opening another."
            )
            return "blocked"

        # A build repair that reached a file the plan did not include, and
        # whose repaired patch regressed, had no effect or was never
        # reviewed: the draft-time review never read that file, so no
        # approval stands in for this one (validation.repair, 2026-10-31).
        repair_blocks = list(metadata.get("repaired_patch_review_blocks") or [])             or repaired_patch_review_blocks(metadata)
        if repair_blocks:
            print("Publication blocked: " + "; ".join(repair_blocks) + ". Rerun the "
                  "validation-fix step so the repaired patch is reviewed again.")
            return "blocked"

        # publishable is pr_generator's own verdict, which additionally
        # accounts for build validation, so it stays in the expression. A
        # review-only draft the verdict after validation cleared
        # (READY_FOR_PUBLICATION_WITH_NOTES) still needs this finding's
        # explicit approval, whatever its publishable flag says.
        review_only = (
            metadata.get("publishable") is False
            or metadata.get("publication_promoted_after_validation") is True
            or not FindingDisposition.from_metadata(metadata).publishable
        )
        finding_id = str(metadata.get("finding_id", "")).strip()
        explicitly_approved = finding_id in self.approved_reviewed_findings
        if review_only and not explicitly_approved:
            print(
                "Publication blocked: this PR artifact is a review-only "
                "preview and is not authorized for push or GitHub PR creation."
            )
            return "blocked"
        if explicitly_approved:
            build_validation = metadata.get("build_validation", {}) or {}
            build_status = str(build_validation.get("status", "")).lower()
            # "success_app_module_only" is a deliberately narrower pass: the
            # full multi-module build failed on a task outside every module
            # the patch touched (see rescue_unrelated_module_failure in
            # validate_applied_solutions.py -- typically a native/Rust/NDK
            # submodule the validation machine cannot build), but the
            # module(s) actually containing the patch were independently
            # recompiled and succeeded. It is accepted here, but never
            # silently conflated with a real "success": the caller is told
            # exactly what was and was not verified locally.
            unvalidated_ok = (
                finding_id in self.publish_without_build_validation
                and build_says_nothing_about_patch(build_validation)
            )
            if (
                build_status not in BUILD_VALIDATION_PASSING_STATUSES
                and not unvalidated_ok
            ):
                raise RuntimeError(
                    "Explicit review approval requires successful build "
                    f"validation; got {build_status or 'not_run'}"
                )
            if unvalidated_ok:
                print(
                    "\n" + "!" * 60 + "\n"
                    "WARNING: publishing WITHOUT compile validation.\n"
                    f"Finding {finding_id} was explicitly approved for "
                    "publication even though the build never ran or "
                    "fails the same way without the patch "
                    f"(status={build_status or 'not_run'}). Nothing in this "
                    "pipeline establishes that the patch compiles. The "
                    "maintainer's CI is the first build it will see, and a "
                    "broken pull request costs them time.\n"
                    + "!" * 60
                )
            if not str(metadata.get("commit_hash", "")).strip():
                raise RuntimeError(
                    "Explicit review approval requires a concrete commit hash"
                )
            if build_status == "success_app_module_only":
                # The rescue phase records what failed under
                # phases.app_module_compile_only, not at the top level, so
                # this note printed "failing task: unknown" for
                # TowerCollector, whose "official" flavor has no
                # google-services.json on this machine.
                rescue = (
                    (build_validation.get("phases") or {})
                    .get("app_module_compile_only") or {}
                )
                failing = (
                    build_validation.get("unrelated_module_failure_task")
                    or rescue.get("unrelated_module_failure_task")
                    or "unknown"
                )
                flavor = (
                    build_validation.get("unselected_flavor")
                    or rescue.get("unselected_flavor")
                )
                print(
                    "NOTE: only the patched module was recompiled in "
                    "isolation; the full multi-module build did not succeed "
                    f"locally (failing task or module: {failing}"
                    + (f", in the unbuilt flavor '{flavor}'" if flavor else "")
                    + "). The target repository's own CI is the first full "
                    "build this patch will see."
                )
            if build_status == COMPILE_ONLY_PASS:
                print(
                    "NOTE: the project's only module was compiled after the "
                    "full build did not finish; no APK was assembled locally."
                )
            if build_status == PRODUCTION_ONLY_TEST_BUILD_PREEXISTING:
                print(production_only_build_note(build_validation))
            if self.refuses_stale_review(metadata, json_file):
                return "blocked"
            print(
                "Explicit human approval accepted for reviewed finding "
                f"{finding_id} at commit {metadata['commit_hash']} "
                f"(solution revision "
                f"{metadata.get('solution_revision', 'unknown')})."
            )
        elif self.refuses_stale_review(metadata, json_file):
            return "blocked"
        # Nothing reaches GitHub for a finding the user did not name: a draft
        # whose own verdict was publishable used to be forked, pushed and
        # opened by any run of this step, approval or not (batch6 audit,
        # 2026-10-30). Without the approval it is this run's dry run.
        dry_run = self.dry_run or not explicitly_approved

        result_file = (
            json_file.parent
            / f"{json_file.stem}_created.json"
        )

        if result_file.exists() and self.skip_existing:
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    previous_result = json.load(f)
            except (OSError, json.JSONDecodeError):
                previous_result = {}

            # A dry-run record is only a simulation marker. It must never
            # prevent a later real publication.
            if not dry_run and previous_result.get("dry_run") is True:
                print("Previous result was dry-run only. Continuing with real publication.")
            else:
                print("Already processed. Skipping.")
                return

        repo_path = metadata["repository"]

        branch = metadata["branch"]

        host, original_owner, repo = (
            self.resolve_original_repository(
                metadata
            )
        )

        print(
            f"Original Repo: "
            f"{host}/{original_owner}/{repo}"
        )

        if host.lower() != "github.com":

            raise ValueError(
                f"PR creation is GitHub-only, but repository remote is {host}: "
                f"{original_owner}/{repo}"
            )

        # The last check, after approval and build: checked against the text
        # as it is now, since the title and body may have been edited by hand
        # after the PR step wrote them. A dry run reports it too.
        template = metadata.get("pull_request_template") or {}
        for note in (template.get("notes") or []) if isinstance(template, dict) else []:
            # What the repository's form asks that only the operator can
            # answer (an AI question with no assisted option): printed, the
            # operator decides (prs.pr_template).
            print(f"NOTE (pull-request template {template.get('path', '')}): {note}")
        # A body that fills the repository's template is held only for its
        # own words: the template's preamble is the maintainers' text
        # (pr_template.problems_in_own_words, thunderbird-android 2026-10-30).
        problems = problems_in_own_words(
            lambda body: text_problems(str(metadata.get("title", "")), body,
                                       str(metadata.get("error_message", "")),
                                       specs=finding_specs(metadata),
                                       code=str(metadata.get("diff", ""))),
            str(metadata.get("body", "")), str(metadata.get("plain_body", "")))
        # A line of the repository's form asking the author to write
        # something, still in the body (SMSecure run_v4, 2026-10-31).
        if metadata.get("pull_request_template"):
            problems = list(dict.fromkeys([*problems, *template_instructions_left(
                str(metadata.get("body", "")), self.checkout_template(metadata).text)]))
        if problems and not self.allow_text_problems:
            print(
                "Publication blocked: the pull request text still reads like "
                "the pipeline: " + "; ".join(problems) + ". Edit the title and "
                f"body in {json_file.name}, or pass --allow-text-problems."
            )
            return "blocked"

        # What the repository says now: its rules on AI-assisted
        # contributions, and whether its default branch still has the code
        # this changes (see publication_checks).
        blockers = publication_blockers(
            metadata, self.github_contents, api=self.github_api,
            allow_ai_policy=self.allow_ai_policy,
            allow_changed_upstream=self.allow_changed_upstream,
            allow_closed_repository=self.allow_closed_repository,
        )
        if blockers:
            print("Publication blocked: " + " ".join(blockers))
            return "blocked"

        if dry_run:

            print("DRY RUN: would create fork, configure remotes, push branch and create PR"
                  + ("" if explicitly_approved else
                     f"; pass --approve-reviewed-finding {finding_id or '<finding id>'} "
                     "to publish it"))

            dry_response = {
                "dry_run": True,
                "repository": repo_path,
                "original_owner": original_owner,
                "repo": repo,
                "branch": branch,
                "title": metadata.get("title"),
                "base_branch": self.base_branch or "auto-detected",
            }

            if records_publication(result_file):
                # The published pull request's record stays: refresh-text,
                # cleanup and the duplicate check read its URL (batch6
                # review, 2026-10-30).
                print(f"Kept {result_file.name}: it records the published pull request.")
                return

            with open(
                result_file,
                "w",
                encoding="utf-8"
            ) as f:

                json.dump(
                    dry_response,
                    f,
                    indent=2,
                    ensure_ascii=False
                )

            return

        original_owner, repo = self.resolve_canonical_repository(
            original_owner,
            repo
        )

        # --------------------------------------------------
        # FORK
        # --------------------------------------------------

        fork_full_name = self.create_fork(
            original_owner,
            repo
        )

        self.wait_for_fork(fork_full_name)

        # --------------------------------------------------
        # REMOTES
        # --------------------------------------------------

        self.configure_git_remotes(
            repo_path,
            f"{original_owner}/{repo}",
            fork_full_name
        )

        # --------------------------------------------------
        # PUSH
        # --------------------------------------------------

        self.push_branch(
            repo_path,
            branch
        )

        # --------------------------------------------------
        # CREATE PR
        # --------------------------------------------------

        pr_response = (
            self.create_pull_request(
                metadata,
                original_owner,
                repo,
                fork_full_name.split("/", 1)[0]
            )
        )

        # --------------------------------------------------
        # SAVE RESPONSE
        # --------------------------------------------------

        with open(
            result_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                pr_response,
                f,
                indent=2,
                ensure_ascii=False
            )

        self.published_findings.add(str(metadata.get("finding_id", "")))
        print("\nSUCCESS")
        print(
            f"PR URL: "
            f"{pr_response['html_url']}"
        )

    # ======================================================
    # UPDATE A PUBLISHED DESCRIPTION
    # ======================================================

    def description_template(self, metadata: dict, published: dict) -> PullRequestTemplate:
        """The template on the pull request's base branch now, else the local checkout's.

        The upstream repository is the one the pull request was opened on
        (its html_url), read at the base branch: the template the
        maintainer's bot checks against.
        """
        match = re.search(r"github\.com/([^/]+)/([^/]+)/pull/\d+",
                          str(published.get("html_url", "")))
        if match:
            template = template_on_github(
                self.github_contents, self.github_api, match.group(1), match.group(2),
                str(published.get("base", {}).get("ref", "") if isinstance(
                    published.get("base"), dict) else "") or str(metadata.get("base_branch", "")))
            if template or template.note:
                return template
        return self.checkout_template(metadata)

    def checkout_template(self, metadata: dict) -> PullRequestTemplate:
        """The template of the record's local checkout (absolute, or relative to the PR dir)."""
        repository = str(metadata.get("repository", "") or "")
        if not repository:
            return PullRequestTemplate()
        for candidate in (Path(repository), self.pr_dir / repository):
            if candidate.is_dir():
                return template_in_checkout(candidate)
        return PullRequestTemplate()

    def description_writer(self):
        """The model client that revises a description for pushed follow-ups."""
        if self._description_writer is None:
            if not self.text_model.get("model"):
                raise RuntimeError("no model to describe the pushed follow-up commits "
                                   "(--model)")
            from cognalys.llm.generate_error_fixes import OpenAIProvider
            from cognalys.llm.llm_providers import remote_provider

            options = dict(self.text_model)
            options.setdefault("prompt_log_dir", self.pr_dir / "description_prompts")
            self._description_writer = remote_provider(openai_class=OpenAIProvider, **options)
        return self._description_writer

    def patch_pull_request_body(self, api_url: str, body: str) -> None:
        """PATCH the body of a pull request (the pr_github.update_pr_metadata call)."""
        response = requests.patch(api_url, headers=self.headers, json={"body": body}, timeout=30)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Description update failed:\n{response.text}")

    def update_published_description(self, json_file: Path) -> str:
        """Fill the repository's template into a published pull request's description.

        NotallyX#1167 (2026-10-30) was opened with the plain paragraphs while
        the repository asks for its template. The description is filled again
        from the local records -- the hand-written body when there is one,
        else the plain text the PR step wrote -- with the facts the record
        holds (build, test files, diff). Follow-up commits pushed after the
        record are described too: the text is revised from their diffs
        (description_after_follow_ups), and a repository without a template
        gets its plain description sent again. The same text checks as
        publication run on the filled body. Only a finding named by
        --approve-reviewed-finding, outside --dry-run, is sent to GitHub;
        anything else prints the body it would send. Returns what happened.
        """
        json_file = Path(json_file)
        metadata = json.loads(json_file.read_text(encoding="utf-8"))
        created = json_file.parent / f"{json_file.stem}_created.json"
        published = {}
        if created.is_file():
            try:
                published = json.loads(created.read_text(encoding="utf-8"))
            except ValueError:
                published = {}
        api_url = str(published.get("url", "")).strip()
        if not api_url or published.get("dry_run"):
            print(f"{json_file.name}: not published; nothing to update.")
            return "not_published"
        finding_id = str(metadata.get("finding_id", "")).strip()
        # The record must describe what the pull request holds now: a commit
        # pushed after it (a follow-up such as the SHA-256 test vector
        # CodeRabbit asked for on NotallyX#1167) changes the diff, the tests
        # and the build the form is filled from.
        live = self.github_contents(api_url)
        head = str((live.get("head") or {}).get("sha", "")) if isinstance(live, dict) else ""
        recorded = str(metadata.get("commit_hash", "") or "")
        rounds: list = []
        if head and recorded and head != recorded:
            follow_up_file = follow_up_record_for(json_file)
            try:
                follow_up = json.loads(follow_up_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                follow_up = {}
            rounds = pushed_follow_ups(follow_up, recorded, head)
            if not rounds:
                print(f"{published.get('html_url', api_url)}: its head is {head[:12]}, but "
                      f"{json_file.name} describes {recorded[:12]}; regenerate the record "
                      "before updating the description.")
                return "stale_record"
            print(f"{json_file.name}: the head {head[:12]} is {len(rounds)} pushed follow-up "
                  f"commit(s) of {follow_up_file.name} on top of {recorded[:12]}; their "
                  "diffs are part of the description's facts.")
            metadata = metadata_with_follow_ups(metadata, rounds)
        written = json_file.parent / f"{json_file.stem}_body.md"
        title = str(published.get("title") or metadata.get("title", ""))
        if written.is_file():
            first, _, rest = written.read_text(encoding="utf-8").strip().partition("\n")
            description = rest.strip() if first.startswith("# ") else f"{first}\n{rest}".strip()
            if rounds:
                # The contributor's own words are never rewritten by a model
                # (a repository that asks for no AI text gets one written by
                # hand); they are told what the text has to cover.
                print(f"NOTE: {written.name} is used as written; the pull request also "
                      "holds " + "; ".join(
                          f"{str(item.get('commit', ''))[:12]} "
                          f"({item.get('commit_subject') or 'follow-up'})" for item in rounds)
                      + ". Make sure the text describes them.")
        else:
            description = str(metadata.get("plain_body") or metadata.get("body") or "").strip()
            if rounds:
                # NotallyX#1167 (2026-10-30): the text written for the first
                # commit was filled into the form again, the test-vector
                # commit only ticked a box that was already ticked, and the
                # body came out identical: "already up to date" while it
                # still described tests of equal and changed content only.
                description, problems = description_after_follow_ups(
                    self.description_writer(), description, rounds, title=title,
                    finding_id=finding_id)
                if problems:
                    print("Description update blocked: the text does not describe the "
                          "pushed follow-up commit(s): " + "; ".join(problems) + ".")
                    return "blocked"
        template = self.description_template(metadata, published)
        if template:
            body, notes = body_for_repository(
                template, description, facts_from_pull_request(metadata))
            for note in notes:
                print(f"NOTE (pull-request template {template.path}): {note}")
            form = f"template {template.path}"
        elif rounds:
            # No form to fill, but the plain text a follow-up made stale is
            # sent again just the same: the text must describe the final diff.
            body, form = description, "plain description"
        else:
            print(f"{json_file.name}: the repository has no pull-request template"
                  + (f" ({template.note})" if template.note else "") + "; nothing to update.")
            return "no_template"
        problems = problems_in_own_words(
            lambda text: text_problems(title, text, str(metadata.get("error_message", "")),
                                       specs=finding_specs(metadata),
                                       code=str(metadata.get("diff", ""))),
            body, description)
        if problems and not self.allow_text_problems:
            print("Description update blocked: the filled text still reads like the "
                  "pipeline: " + "; ".join(problems) + ".")
            return "blocked"
        if self.body_out is not None:
            self.body_out.mkdir(parents=True, exist_ok=True)
            (self.body_out / f"{json_file.stem}_{'template' if template else 'plain'}_body.md"
             ).write_text(body + "\n", encoding="utf-8")
        if body.strip() == str(published.get("body") or "").strip():
            if rounds and written.is_file():
                # Nothing revised the hand-written text for the commits pushed
                # after it, so "up to date" would claim what nobody checked
                # (2026-10-30 review of the NotallyX#1167 fix).
                print(f"{published.get('html_url', api_url)}: the description is the text "
                      f"already sent, but {written.name} was not revised for the pushed "
                      "follow-up commit(s); edit it if it does not describe them.")
                return "unchanged_written_by_hand"
            print(f"{published.get('html_url', api_url)}: description already up to date.")
            return "unchanged"
        approved = finding_id in self.approved_reviewed_findings
        if self.dry_run or not approved:
            print(f"DRY RUN: would update the description of "
                  f"{published.get('html_url', api_url)} ({form})"
                  + ("" if approved else f"; pass --approve-reviewed-finding {finding_id} "
                     "to send it") + ":\n\n" + body + "\n")
            return "would_update"
        state = self.live_pull_request_state(published.get("html_url", ""))
        if state != "open":
            print(f"{published.get('html_url', api_url)} is {state or 'unknown'}; "
                  "its description is left as it is.")
            return f"skipped_{state or 'unknown'}"
        self.patch_pull_request_body(api_url, body)
        published["body"] = body
        created.write_text(json.dumps(published, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Updated the description of {published.get('html_url', api_url)}.")
        return "updated"

    def run_description_updates(self, files) -> dict:
        """update_published_description over the files, the approved ones when named."""
        results: dict = {}
        failed = 0
        for json_file in files:
            if self.approved_reviewed_findings:
                try:
                    finding = str(json.loads(json_file.read_text(encoding="utf-8"))
                                  .get("finding_id", "")).strip()
                except (OSError, ValueError):
                    finding = ""
                if finding not in self.approved_reviewed_findings:
                    continue
            try:
                outcome = self.update_published_description(json_file)
            except Exception as error:  # one record's failure does not stop the others
                failed += 1
                print(f"\nFAILED\n{json_file.name}\n{error}")
                continue
            results[outcome] = results.get(outcome, 0) + 1
        print("\nDESCRIPTION UPDATES: " + (", ".join(
            f"{key} {value}" for key, value in sorted(results.items())) or "none"))
        held = ("blocked", "stale_record")
        return {"success": sum(value for key, value in results.items() if key not in held),
                "failed": failed, "blocked": sum(results.get(key, 0) for key in held),
                "results": results}

    # ======================================================
    # RUN
    # ======================================================

    def run(self):

        files = sorted(
            self.pr_dir.glob("*_pr.json")
        )
        if self.project:
            files = [
                file for file in files
                if file.name.startswith(f"{self.project}_")
            ]

        if self.limit > 0:

            files = files[:self.limit]

        print(
            f"Found {len(files)} PR metadata files"
        )

        if self.update_description:
            return self.run_description_updates(files)

        success = 0

        failed = 0

        blocked = 0

        for json_file in files:

            try:

                outcome = self.process_pr_file(
                    json_file
                )
                if outcome == "blocked":
                    blocked += 1
                else:
                    success += 1

            except Exception as e:

                failed += 1

                print("\nFAILED")
                print(json_file.name)
                print(e)

        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        print(f"Success: {success}")
        print(f"Failed: {failed}")
        print(f"Blocked: {blocked}")

        return {"success": success, "failed": failed, "blocked": blocked}


# ==========================================================
# MAIN
# ==========================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description="Create GitHub pull requests from *_pr.json metadata files."
    )
    parser.add_argument(
        "--pull-requests-dir",
        default=PULL_REQUESTS_DIR,
        help="Directory containing *_pr.json files (default: pull_requests)."
    )
    parser.add_argument(
        "--github-token",
        default=None,
        help="GitHub token. Defaults to GITHUB_TOKEN environment variable."
    )
    parser.add_argument(
        "--github-api",
        default=GITHUB_API,
        help="GitHub API base URL (default: https://api.github.com)."
    )
    parser.add_argument(
        "--fork-wait-seconds",
        type=int,
        default=120,
        help="Maximum seconds to wait for a new fork to accept a push (default: 120)."
    )
    parser.add_argument(
        "--base-branch",
        default=None,
        help="Override PR base branch. Default auto-detects upstream HEAD."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process only the first N PR metadata files. 0 means no limit."
    )
    parser.add_argument(
        "--project",
        default="",
        help="Publish PR metadata only for this exact APK/project name."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write *_created.json dry-run records without forking, pushing or creating PRs."
    )
    parser.add_argument(
        "--reprocess-existing",
        action="store_true",
        help="Reprocess PR metadata even when *_created.json already exists."
    )
    parser.add_argument(
        "--allow-duplicate-pr",
        action="store_true",
        help=(
            "Open a new pull request even though an earlier run already "
            "published one for the same finding that may still be open."
        ),
    )
    parser.add_argument(
        "--allow-text-problems",
        action="store_true",
        help=(
            "Publish even when the title or body still reads like the "
            "pipeline (evidence ids, its vocabulary, a 'Fix <method>' title)."
        ),
    )
    parser.add_argument(
        "--allow-ai-policy",
        action="store_true",
        help=(
            "Skip the note on a repository's AI-assisted contribution policy. "
            "It never blocks: the operator writes such a PR's text themselves; "
            "where submitting affirms that no AI was used, the warning with the "
            "quote is printed anyway."
        ),
    )
    parser.add_argument(
        "--allow-closed-repository",
        action="store_true",
        help=(
            "Publish although the repository says it accepts no contributions; "
            "only when the maintainer has invited this one."
        ),
    )
    parser.add_argument(
        "--allow-changed-upstream",
        action="store_true",
        help=(
            "Publish although the default branch changed the code this pull "
            "request touches since the analysed commit."
        ),
    )
    parser.add_argument(
        "--allow-stale-review",
        action="store_true",
        help=(
            "Publish a draft that did not pass the current behaviour review "
            "(none recorded, or an older contract). The refusal prints the "
            "command that runs the finding again."
        ),
    )
    parser.add_argument(
        "--approve-reviewed-finding",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help=(
            "Explicitly authorize publication of one human-reviewed finding. "
            "Requires successful build validation and a concrete commit. "
            "Can be repeated. A finding not named here is only a dry run: "
            "nothing is forked, pushed or opened for it."
        ),
    )
    parser.add_argument(
        "--publish-without-build-validation",
        action="append",
        default=[],
        metavar="FINDING_ID",
        help=(
            "DANGEROUS. Also authorize this finding when the build never "
            "ran -- an unbuildable repository, not a failing patch. Nothing "
            "then establishes that the patch compiles, and the maintainer's "
            "CI is the first build it sees. Never accepted for a build that "
            "ran and failed. Must accompany --approve-reviewed-finding for "
            "the same id. Can be repeated."
        ),
    )

    parser.add_argument(
        "--update-description",
        action="store_true",
        help=(
            "Fill the repository's pull-request template into the description "
            "of pull requests already published from this directory. Prints "
            "the body it would send; only a finding named by "
            "--approve-reviewed-finding, without --dry-run, is updated on GitHub."
        ),
    )
    parser.add_argument(
        "--body-out",
        default=None,
        metavar="DIR",
        help="With --update-description: also write each body it would send into DIR.",
    )
    # The model that revises a published description for the follow-up
    # commits pushed after it; the same defaults as pr_followup, which made
    # those commits.
    parser.add_argument("--model", default="gpt-6.1-terra",
                        help="With --update-description: the model that revises the "
                             "description for pushed follow-up commits.")
    parser.add_argument("--endpoint", default="https://api.openai.com/v1")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-output-tokens", type=int, default=32768)
    parser.add_argument("--reasoning-effort", default="high",
                        choices=("none", "low", "medium", "high", "xhigh"))
    parser.add_argument("--llm-cache-dir", default=None)
    parser.add_argument("--prompt-log-dir", default=None,
                        help="where the description model's prompts are logged "
                             "(default: description_prompts next to the PR records)")

    return parser.parse_args()


def description_model(args) -> dict:
    """remote_provider arguments of the description model, from the command line."""
    from cognalys.workspace import workspace_root

    model = {"model": args.model, "endpoint": args.endpoint, "timeout": args.timeout,
             "max_output_tokens": args.max_output_tokens,
             "reasoning_effort": args.reasoning_effort,
             "cache_dir": Path(args.llm_cache_dir or workspace_root() / "outputs" / ".llm_cache")}
    if args.prompt_log_dir:
        model["prompt_log_dir"] = Path(args.prompt_log_dir)
    return model


def main():

    args = parse_args()

    summary = AutomatedPRCreator(
        update_description=args.update_description,
        body_out=args.body_out,
        text_model=description_model(args),
        pull_requests_dir=args.pull_requests_dir,
        github_token=args.github_token,
        github_api=args.github_api,
        fork_wait_seconds=args.fork_wait_seconds,
        # Publishing, like a description update, is a dry run unless a
        # finding is approved by name; each unapproved one stays a dry run
        # in process_pr_file.
        dry_run=args.dry_run or not args.approve_reviewed_finding,
        skip_existing=not args.reprocess_existing,
        base_branch=args.base_branch,
        limit=args.limit,
        project=args.project,
        approved_reviewed_findings=args.approve_reviewed_finding,
        publish_without_build_validation=args.publish_without_build_validation,
        allow_duplicate_pr=args.allow_duplicate_pr,
        allow_text_problems=args.allow_text_problems,
        allow_ai_policy=args.allow_ai_policy,
        allow_changed_upstream=args.allow_changed_upstream,
        allow_closed_repository=args.allow_closed_repository,
        allow_stale_review=args.allow_stale_review,
    ).run()
    # A blocked finding is not a failure: it was withheld on purpose and its
    # reason is recorded. Only an attempt that broke counts against the step.
    return step_exit_code(
        summary["success"] + summary["blocked"], summary["failed"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
