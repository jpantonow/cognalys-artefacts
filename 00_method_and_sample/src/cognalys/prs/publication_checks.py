"""What the target repository says now, read just before a pull request is opened.

Two closed pull requests were decided by something the pipeline never looked
at, because it looks at the commit the APK was built from:

- tuisku#11: its README says "AI generated code is not permitted in this
  repository. Pull requests that have AI assistance ... would be rejected",
  and it was. Such a policy is reported, not enforced: the operator then
  writes the pull request or issue text themselves.
- giggity#543: "Oh, just made this change myself independently." The
  analysed commit was weeks behind the default branch, which already had
  the fix.

Both are read from GitHub at publication time: the policy from the files a
contributor is expected to read, and the code by applying the pull request's
diff to the default branch as it is now.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from cognalys.utils.patch_scope import is_test_path

# Where a project tells contributors its rules, in the order a contributor
# would read them. Root, .github/ and docs/.
_POLICY_FILE = re.compile(
    r"^(?:\.github/|docs/)?(?:README|CONTRIBUTING|CONTRIBUTE|AI[_-]?POLICY|AI[_-]?USAGE"
    r"|PULL_REQUEST_TEMPLATE|pull_request_template|CODE_OF_CONDUCT)(?:\.\w+)?$",
    re.IGNORECASE,
)
_AI = (r"(?:\bA\.?I\.?\b|\bLLMs?\b|large language models?|ChatGPT|Copilot|\bClaude\b|\bGemini\b"
       r"|generative|machine[- ]generated|vibe[- ]cod\w*)")
_RESTRICTION = (
    r"(?:not\s+(?:\w+\s+){0,2}(?:permitted|allowed|accepted|welcome)|n'?t\s+(?:\w+\s+){0,2}"
    r"(?:permitted|allowed|accepted|welcome)|prohibit\w*|forbid\w*|\bban(?:ned)?\b"
    r"|will\s+be\s+(?:closed|rejected)|would\s+be\s+(?:closed|rejected)|\breject\w*"
    r"|do\s+not\s+(?:submit|use|open|send)|don'?t\s+(?:submit|use|open|send)|\bno\s+AI\b"
    r"|must\s+(?:be\s+)?disclos\w*|must\s+(?:state|declare|mention)|disclos\w+\s+(?:any|the\s+use))"
)
# About contributions, not the app: "No ads, no AI, no tracking" in a feature
# list is not a rule for contributors.
_CONTRIBUTION = r"(?:\bcode\b|pull\s+requests?|\bPRs?\b|contribut\w*|\bcommits?\b|\bpatch\w*|submi\w+)"
_SENTENCE = re.compile(r"[^.!?\n]*(?:[.!?]|\n|$)")
_POLICY = re.compile(rf"(?is)(?=.*{_AI})(?=.*{_RESTRICTION})(?=.*{_CONTRIBUTION})")
_DIFF_FILE = re.compile(r"^diff --git a/(?P<old>\S+) b/(?P<new>\S+)$", re.MULTILINE)


# A project that takes no contributions at all. idunnololz/summit#54
# (2026-10-17) was closed with "Sorry I am not currently accepting PR
# requests" -- line 13 of its README said "Please note that I am currently not
# accepting PRs."
_CLOSED = re.compile(
    r"(?is)(?:\bnot\s+(?:\w+\s+){0,2}accept\w*|\bn'?t\s+(?:\w+\s+){0,2}accept\w*"
    r"|\bno\s+longer\s+accept\w*|\bnot\s+open\s+to|\bclosed\s+to)"
    r"\s+(?:\w+\s+){0,3}(?:PRs?\b|pull\s+requests?|contributions?|patches)"
)


def closed_to_contributions(files: dict) -> Optional[dict]:
    """The first sentence, in the given files, saying the project accepts no contributions."""
    for path, text in files.items():
        for match in _SENTENCE.finditer(str(text)):
            sentence = " ".join(match.group(0).split())
            if len(sentence) > 12 and _CLOSED.search(sentence):
                return {"path": path, "statement": sentence[:300]}
    return None


# A statement the contributor makes by submitting, about not using AI: App
# Manager's CONTRIBUTING (2026-11-01) "By submitting a pull or merge request,
# you affirm that the code was authored by you without the use of generative
# AI tools", and its template's box "I know that generating a PR or its code
# ... using AI/LLM is prohibited". Publishing would make that statement.
_AFFIRMATION = (r"(?:\bby\s+(?:submitting|opening|sending)\b|\byou\s+(?:affirm|certify|attest"
                r"|confirm|declare|agree)\b|\bI\s+(?:know|affirm|certify|attest|confirm|declare"
                r"|understand|agree)\b)")
# The negation must govern the AI term within a few words: "whether
# AI-assisted or not, is licensed under MIT" and "AI-generated code was
# reviewed, not pasted blindly" (2026-11-01 review) affirm nothing about AI.
_AI_TERM = (r"(?:A\.?I\.?|LLMs?|large\s+language\s+models?|ChatGPT|Copilot"
            r"|generative\s+(?:AI|tools?|models?))")
_NEGATED_AI = (
    r"(?:\bwithout\s+(?:the\s+)?(?:use\s+of\s+|using\s+|help\s+(?:of|from)\s+"
    r"|assistance\s+(?:of|from)\s+)?(?:any\s+)?(?:generative\s+)?" + _AI_TERM + r"(?![\w-])"
    r"|\bnot\s+(?:been\s+)?(?:use[ds]?|using|generated|created|written|produced|assisted)\s+"
    r"(?:(?:by|with|using|from)\s+)?(?:any\s+)?(?:generative\s+)?" + _AI_TERM + r"(?![\w-])"
    r"|\bno\s+(?:generative\s+)?" + _AI_TERM + r"(?![\w-])"
    r"|(?<![\w-])" + _AI_TERM + r"(?![\w-])[\w\s/,-]{0,40}?\b(?:is|are)\s+(?:strictly\s+)?"
    r"(?:prohibited|forbidden|banned|not\s+(?:allowed|permitted|accepted)))")
_AFFIRMED_POLICY = re.compile(
    rf"(?is)(?=.*{_AFFIRMATION})(?=.*{_NEGATED_AI})(?=.*{_CONTRIBUTION})")
# A line break inside a paragraph (reStructuredText and Markdown wrap prose),
# not one before a list item, a heading or a table row.
_WRAPPED_LINE = re.compile(r"(?<!\n)\n(?!\s*(?:[-*+#|>]|\d+\.|\n))")


def ai_policy(files: dict) -> Optional[dict]:
    """The first sentence, in the given files, that restricts or conditions AI-assisted contributions.

    ``kind`` is "affirmation_required" when submitting states that no AI was
    used (the contributor affirms it), else "restriction" (a rule or a
    preference the operator answers in the text).
    """
    for path, text in files.items():
        for match in _SENTENCE.finditer(_WRAPPED_LINE.sub(" ", str(text))):
            sentence = " ".join(match.group(0).split())
            if len(sentence) > 12 and _AFFIRMED_POLICY.match(sentence):
                return {"path": path, "statement": sentence[:300], "kind": "affirmation_required"}
    for path, text in files.items():
        for match in _SENTENCE.finditer(str(text)):
            sentence = " ".join(match.group(0).split())
            if len(sentence) > 12 and _POLICY.match(sentence):
                return {"path": path, "statement": sentence[:300], "kind": "restriction"}
    return None


def ai_affirmation_warning(repository: str, policy: dict) -> str:
    """The block printed where submitting affirms that no AI was used."""
    rule = "=" * 72
    return (f"{rule}\nWARNING: {repository} asks contributors to affirm on submission that no "
            f"AI was used.\n  {policy.get('path', '')}: \"{policy.get('statement', '')}\"\n"
            "  Not a blocker: decide yourself whether to submit, and write the text "
            f"yourself.\n{rule}")


def policy_files_in_checkout(repository) -> dict:
    """The files of a local checkout where a project states its contribution rules."""
    root = Path(repository)
    texts = {}
    for folder in ("", ".github", "docs"):
        try:
            entries = sorted((root / folder).iterdir()) if (root / folder).is_dir() else []
        except OSError:
            entries = []
        for entry in entries:
            name = f"{folder}/{entry.name}".lstrip("/")
            if entry.is_file() and _POLICY_FILE.match(name):
                try:
                    texts[name] = entry.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
    return texts


def current_review_contract_version() -> int:
    """The behaviour-review contract coordinated_generation applies now."""
    try:
        from cognalys.llm.coordinated_generation import REVIEW_CONTRACT_VERSION
    except ImportError:
        return 0
    return int(REVIEW_CONTRACT_VERSION)


# Fix paths that never run the behaviour review (finding_processing's
# single-method generation). A rerun takes the same path, so a draft from one
# is not outdated: Grocy's insertConscrypt and KeyPass's getKey came from it.
FIX_SCOPES_WITHOUT_REVIEW = frozenset({"single_method"})


def review_absent_by_design(fix_scope, version=None) -> str:
    """Why a draft has no behaviour review that a rerun would not change, or ""."""
    if version in (None, "") and str(fix_scope or "") in FIX_SCOPES_WITHOUT_REVIEW:
        return (f"the {fix_scope} fix path has no behaviour review; "
                "a rerun will not add one")
    return ""


# Why a draft is held when its behaviour review did not run (B4).
REVIEW_NOT_RUN = "no behaviour review ran on this draft"


def stale_review(version, reviewed: Optional[bool] = None,
                 current: Optional[int] = None, fix_scope=None) -> str:
    """What a draft's behaviour review lacks next to the current one, or "".

    The version is read from the draft's own data, never from run dates.
    Drafts from before the review existed (2026-10-23) or before its no-effect
    check (2026-10-25) stayed "PR pronto" and approvable: Syncthing-Lite's
    renamed constant with the same value, Orgzly's type annotation. A current
    version only says the draft passed today's gates, not that it is good: the
    diff still has to be read. A draft from a path without the review is not
    stale (review_absent_by_design says so instead).
    """
    current = current_review_contract_version() if current is None else int(current)
    try:
        recorded = int(version) if version not in (None, "") else None
    except (TypeError, ValueError):
        recorded = None
    if recorded is None and review_absent_by_design(fix_scope):
        return ""
    if recorded is None:
        if reviewed is False:
            return REVIEW_NOT_RUN
        if reviewed:
            return "its behaviour review predates the review contract versions"
        return "no behaviour review contract is recorded for this draft"
    if recorded < current:
        return f"its behaviour review used contract {recorded}; the current one is {current}"
    return ""


def published_pull_request(json_file) -> str:
    """The pull request opened from this ``*_pr.json``, or "".

    Read from its own creation record beside it (``<stem>_created.json``,
    written by pr_creator); a dry run's record opened nothing. pr_creator,
    the preview and the statistics ask this one question: a draft already
    published is not published again, so a stale-review banner and an approval command
    on its preview asked for a rerun of a finding the maintainers already have.
    """
    path = Path(json_file)
    record = path.with_name(f"{path.stem}_created.json")
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict) or data.get("dry_run") is True:
        return ""
    return str(data.get("html_url") or "")


def changed_paths(diff: str) -> list:
    return list(dict.fromkeys(match.group("new") for match in _DIFF_FILE.finditer(diff)))


def without_submodule_pointers(diff: str) -> str:
    """The diff without the sections that only move a submodule pointer.

    The contents API returns no file for a submodule path, so one such
    section turned the whole upstream check into "unknown". Clash Fest's
    preview (2026-10-28) ended with core/src/foss/golang/clash
    "Subproject commit ...-dirty", left in the worktree by the Go build.
    """
    sections = re.split(r"(?m)^(?=diff --git )", diff)
    kept = []
    for section in sections:
        changes = [line for line in section.splitlines()
                   if line[:1] in "+-" and not line.startswith(("+++", "---"))]
        if (section.startswith("diff --git ") and changes
                and all(line[1:].startswith("Subproject commit ") for line in changes)):
            continue
        kept.append(section)
    return "".join(kept)


def upstream_state(diff: str, read_file: Callable[[str], Optional[bytes]]) -> dict:
    """Whether the diff applies to the default branch as it is now.

    applies, already_applied (applies only in reverse: upstream made the same
    change), changed (applies neither way: the code moved on since the
    analysed commit), or unknown (a file could not be read).
    """
    diff = without_submodule_pointers(diff)
    paths = changed_paths(diff)
    if not paths:
        return {"state": "unknown", "detail": "the diff names no file"}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for path in paths:
            content = read_file(path)
            if content is None:
                if f"b/{path}\nnew file mode" in diff or f"--- /dev/null\n+++ b/{path}" in diff:
                    continue
                return {"state": "unknown", "detail": f"could not read {path} upstream"}
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        patch = root.parent / f"{root.name}.diff"
        patch.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8")
        try:
            def applies(*extra: str) -> bool:
                return subprocess.run(["git", "apply", "--check", *extra, str(patch)], cwd=root,
                                      capture_output=True, text=True).returncode == 0
            if applies():
                return {"state": "applies", "paths": paths}
            if applies("--reverse"):
                return {"state": "already_applied", "paths": paths}
            return {"state": "changed", "paths": paths}
        finally:
            patch.unlink(missing_ok=True)


class GitHubReader:
    """Files of a repository's default branch through the GitHub contents API.

    ``branch`` "" reads the default branch (no ref is sent).
    """

    def __init__(self, owner: str, repo: str, branch: str, get: Callable,
                 api: str = "https://api.github.com"):
        self.base = f"{api.rstrip('/')}/repos/{owner}/{repo}/contents"
        self.branch = branch
        self.get = get

    def _params(self) -> dict:
        return {"ref": self.branch} if self.branch else {}

    def read(self, path: str) -> Optional[bytes]:
        response = self.get(f"{self.base}/{path}", self._params())
        if response is None or not isinstance(response, dict) or "content" not in response:
            return None
        return base64.b64decode(response["content"])

    def listing(self, folder: str) -> Optional[list]:
        """Names of the files and folders in ``folder``, or None when it cannot be read."""
        response = self.get(f"{self.base}/{folder}".rstrip("/"), self._params())
        if not isinstance(response, list):
            return None
        return [str(item.get("name") or str(item.get("path", "")).rsplit("/", 1)[-1])
                for item in response if isinstance(item, dict)]

    def policy_files(self) -> dict:
        names = []
        for folder in ("", ".github", "docs"):
            listing = self.get(f"{self.base}/{folder}".rstrip("/"), self._params())
            if isinstance(listing, list):
                names += [item.get("path", "") for item in listing if item.get("type") == "file"]
        texts = {}
        for name in names:
            if _POLICY_FILE.match(name):
                content = self.read(name)
                if content is not None:
                    texts[name] = content.decode("utf-8", errors="replace")
        return texts


def _closed_repository_blockers(owner: str, repo: str, policy_files: dict,
                                allow_closed_repository: bool) -> list:
    if allow_closed_repository:
        return []
    closed = closed_to_contributions(policy_files)
    if not closed:
        return []
    return [f"{owner}/{repo} does not accept contributions ({closed['path']}: "
            f"\"{closed['statement']}\"). Respect it, or pass --allow-closed-repository "
            "if the maintainer has invited this contribution."]


def _print_ai_policy_note(owner: str, repo: str, policy_files: dict, metadata: dict,
                          allow_ai_policy: bool) -> None:
    """A note, never a blocker (2026-10-21): where a maintainer rules out
    AI-assisted contributions the operator writes the pull request or issue
    themselves, so the finding is still published."""
    policy = ai_policy(policy_files)
    recorded = metadata.get("ai_policy") if isinstance(metadata.get("ai_policy"), dict) else None
    if recorded and recorded.get("kind") == "affirmation_required" and (
            not policy or policy.get("kind") != "affirmation_required"):
        policy = recorded
    if policy and policy.get("kind") == "affirmation_required":
        # Where submitting states that no AI was used (App Manager,
        # 2026-11-01) the warning is printed even with --allow-ai-policy:
        # the user reads the quote and decides; it does not hold the PR.
        print(ai_affirmation_warning(f"{owner}/{repo}", policy))
    elif policy and not allow_ai_policy:
        print(
            f"Note: {owner}/{repo} restricts AI-assisted contributions ({policy['path']}: "
            f"\"{policy['statement']}\"). Write this contribution's text yourself.")


_SOURCE_SUFFIXES = (".java", ".kt", ".kts", ".scala", ".groovy")


def reported_code_state(metadata: dict, reader: "GitHubReader") -> dict:
    """Whether the code an issue reports is still on the default branch.

    present, gone (the file, or the reported statement in it, is not there
    any more) or unknown (nothing to look for, or a read failed), with a
    detail. The statement is the detector's reported line, recorded with the
    candidate (``reported_statement``) and matched by its skeleton
    (contribution_base.statement_in_text); a candidate without one is looked
    for by its method's name.
    """
    from cognalys.utils.contribution_base import reported_statement, statement_in_text

    path = str(metadata.get("source_file", "") or "").strip().lstrip("/")
    if not path.endswith(_SOURCE_SUFFIXES):
        return {"state": "unknown", "detail": "the candidate names no source file"}
    content = reader.read(path)
    if content is None:
        folder, _, name = path.rpartition("/")
        names = reader.listing(folder)
        if names is not None and name not in names:
            return {"state": "gone", "detail": f"{path} does not exist there"}
        return {"state": "unknown", "detail": f"could not read {path}"}
    text = content.decode("utf-8", errors="replace")
    statement = reported_statement(str(metadata.get("reported_statement", "") or ""))
    if statement:
        found = statement_in_text(text, statement)
        if found is False:
            return {"state": "gone",
                    "detail": f"{path} no longer has the reported statement `{statement[:120]}`"}
        if found:
            return {"state": "present", "detail": path}
    method = str(metadata.get("method_name", "") or "").strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", method):
        return {"state": "unknown", "detail": f"no statement or method name to look for in {path}"}
    if re.search(r"(?<![\w$])%s\s*\(" % re.escape(method), text):
        return {"state": "present", "detail": path}
    return {"state": "gone", "detail": f"{path} no longer has {method}"}


def issue_publication_blockers(metadata: dict, get: Callable,
                               api: str = "https://api.github.com",
                               allow_changed_upstream: bool = False,
                               allow_closed_repository: bool = False) -> list:
    """publication_blockers' live checks that apply to an issue.

    A repository closed to contributions blocks (--allow-closed-repository);
    an AI policy is a note, never a block, as for a pull request; and an
    issue about code the default branch no longer has blocks
    (--allow-changed-upstream). An issue addressed to an upstream library
    (issue_target upstream_library) reports code of another repository and
    is not looked for in this one. The issue path called none of these
    (PLANO_CORRECOES_DEFESA A7).
    """
    owner, repo = str(metadata.get("repo_owner", "")), str(metadata.get("repo_name", ""))
    if not owner or not repo:
        return []
    reader = GitHubReader(owner, repo, str(metadata.get("base_branch", "") or ""), get, api)
    policy_files = reader.policy_files()
    blockers = _closed_repository_blockers(owner, repo, policy_files, allow_closed_repository)
    _print_ai_policy_note(owner, repo, policy_files, metadata, False)
    if metadata.get("issue_target") == "upstream_library":
        return blockers
    state = reported_code_state(metadata, reader)
    if state["state"] == "gone" and not allow_changed_upstream:
        blockers.append(
            f"the code this issue reports has changed on the default branch of {owner}/{repo} "
            f"since the analysis ({state['detail']}); analyse the finding again at the current "
            "HEAD, or pass --allow-changed-upstream.")
    elif state["state"] == "unknown":
        print(f"Note: could not check {owner}/{repo} as it is now: {state['detail']}")
    return blockers


def publication_blockers(metadata: dict, get: Callable, api: str = "https://api.github.com",
                         allow_ai_policy: bool = False,
                         allow_changed_upstream: bool = False,
                         allow_closed_repository: bool = False) -> list:
    """Reasons not to open this pull request now, read from the repository on GitHub.

    ``get(url, params)`` returns the decoded JSON, or None when the read fails.
    """
    owner, repo = str(metadata.get("repo_owner", "")), str(metadata.get("repo_name", ""))
    branch = str(metadata.get("base_branch", "") or "")
    if not owner or not repo:
        return []
    reader = GitHubReader(owner, repo, branch, get, api)
    blockers = []
    policy_files = reader.policy_files()
    blockers.extend(_closed_repository_blockers(owner, repo, policy_files,
                                                allow_closed_repository))
    _print_ai_policy_note(owner, repo, policy_files, metadata, allow_ai_policy)
    diff = str(metadata.get("diff", "") or "")
    paths = changed_paths(diff)
    if paths and all(is_test_path(path) for path in paths):
        # FreeOTP#521: merged, but the finding stayed in the code. A pull
        # request must change the code the finding names.
        blockers.append(
            "the diff changes only tests (" + ", ".join(paths) + "); the code the "
            "finding names is unchanged, so there is no fix to propose.")
    if diff:
        state = upstream_state(diff, reader.read)
        if state["state"] == "already_applied":
            blockers.append(
                f"the default branch of {owner}/{repo} already contains this change; "
                "there is nothing to propose.")
        elif state["state"] == "changed" and not allow_changed_upstream:
            blockers.append(
                f"the code this pull request changes is different on the default branch of "
                f"{owner}/{repo} now ({', '.join(state['paths'])}); analyse the finding again "
                "at the current HEAD, or pass --allow-changed-upstream.")
        elif state["state"] == "unknown":
            print(f"Note: could not compare with {owner}/{repo} as it is now: {state.get('detail', '')}")
    return blockers
