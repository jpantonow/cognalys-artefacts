"""A repository's pull-request template, filled with what is true of the change.

NotallyX#1167 (2026-10-30) was published with the plain v3 paragraphs, and
CodeRabbit's pre-merge check failed it: "does not follow the required
template". The repository has `.github/pull_request_template.md` with a
description, a related issue, a type of change, an AI-assisted disclosure and a
testing checklist. A contributor fills that form; so does the pipeline now.

The form keeps the template's own headings, order and checklists. HTML
comments (instructions to the author) are dropped. The plain description goes
under the description section. A checkbox is ticked only from a fact the
pipeline verified:

- the type of change is the bug-fix-like option (the pipeline fixes a misuse
  in existing code; it never adds a feature);
- "builds" only when the build validation passed on the patched tree, and
  only for a box that claims a build and no other check (lint, style, tests,
  a release build), naming the tasks that ran;
- "added/updated tests" only when the diff has a test file;
- "tested on a device/emulator" never: RVSec ran the original APK, not the
  patched one;
- "no secrets/debug logs" only when the added lines have no new log call and
  no secret-like literal;
- the AI disclosure is "AI-assisted", the user's decision of 2026-10-30, found
  by meaning across wordings. A template with no assisted-like option is left
  unticked and a note tells the operator, who answers it. A box that only
  acknowledges an AI policy is never ticked; a note asks the operator to read it.

The template's own placeholders go: a dangling "Fixes #", "_<Please
describe...>_" prompts, an example that says the change was tested on a
device, and a preamble the template asks to delete. Excerpts of templates of
popular Android repositories, read on 2026-10-30, are the regression cases
(tests/test_pr_template.py, RealTemplateTests).

Everything here is deterministic: headings, checkboxes and diff lines are
parsed, never interpreted by a model.
"""
from __future__ import annotations

import base64
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from cognalys.utils.disposition import PRODUCTION_ONLY_TEST_BUILD_PREEXISTING
from cognalys.utils.test_source_sets import is_test_source_path

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

# GitHub reads a single template from .github/, the root or docs/, and several
# from a PULL_REQUEST_TEMPLATE/ directory in any of them (docs.github.com,
# "Creating a pull request template for your repository"); names are
# case-insensitive.
TEMPLATE_DIRECTORIES = (".github", "", "docs")
_TEMPLATE_FILE = re.compile(r"^pull_request_template(?:\.md|\.markdown|\.txt)?$", re.IGNORECASE)
_TEMPLATE_DIRECTORY = re.compile(r"^pull_request_template$", re.IGNORECASE)
_TEMPLATE_IN_DIRECTORY = re.compile(r"\.(?:md|markdown|txt)$", re.IGNORECASE)
_DEFAULT_NAME = re.compile(r"^(?:default|pull_request_template)\.(?:md|markdown|txt)$",
                           re.IGNORECASE)


@dataclass(frozen=True)
class PullRequestTemplate:
    """The template a repository asks for: its path and text, or why none is used."""

    path: str = ""
    text: str = ""
    note: str = ""

    def __bool__(self) -> bool:
        return bool(self.text.strip())


def choose_template(paths: Iterable[str]) -> tuple[str, str]:
    """The template GitHub would fill from these repository paths, and a note.

    A single template file wins, in GitHub's order (.github/, root, docs/). A
    PULL_REQUEST_TEMPLATE/ directory is used only when its default is obvious:
    one file, or one named default/pull_request_template. Otherwise none is
    chosen and the note says why, so the plain body is kept knowingly.
    """
    normalized = [str(path).replace("\\", "/").strip("/") for path in paths]
    for directory in TEMPLATE_DIRECTORIES:
        for path in sorted(normalized):
            parent, _, name = path.rpartition("/")
            if parent == directory and _TEMPLATE_FILE.match(name):
                return path, ""
    def in_folder(path: str, directory: str) -> bool:
        parts = path.split("/")
        return (len(parts) >= 2 and _TEMPLATE_DIRECTORY.match(parts[-2]) is not None
                and "/".join(parts[:-2]) == directory
                and _TEMPLATE_IN_DIRECTORY.search(parts[-1]) is not None)

    for directory in TEMPLATE_DIRECTORIES:
        inside = sorted(path for path in normalized if in_folder(path, directory))
        if not inside:
            continue
        if len(inside) == 1:
            return inside[0], ""
        defaults = [path for path in inside if _DEFAULT_NAME.match(path.rsplit("/", 1)[-1])]
        if len(defaults) == 1:
            return defaults[0], ""
        folder = inside[0].rpartition("/")[0]
        return "", (f"{folder}/ holds {len(inside)} templates and none is the default; "
                    "the plain description was kept")
    return "", ""


def _candidate_paths(listing: Callable[[str], Iterable[tuple[str, bool]]]) -> list[str]:
    """Template candidate paths, from a listing of (name, is_directory) per directory."""
    paths = []
    for directory in TEMPLATE_DIRECTORIES:
        for name, is_directory in listing(directory):
            path = f"{directory}/{name}" if directory else name
            if is_directory and _TEMPLATE_DIRECTORY.match(name):
                paths.extend(f"{path}/{inner}" for inner, inner_is_dir in listing(path)
                             if not inner_is_dir)
            elif not is_directory and _TEMPLATE_FILE.match(name):
                paths.append(path)
    return paths


def template_in_checkout(repository: Path) -> PullRequestTemplate:
    """The pull-request template of a local checkout, or an empty one."""
    root = Path(repository)

    def listing(directory: str):
        folder = root / directory if directory else root
        try:
            return [(entry.name, entry.is_dir()) for entry in folder.iterdir()]
        except OSError:
            return []

    path, note = choose_template(_candidate_paths(listing))
    if not path:
        return PullRequestTemplate(note=note)
    try:
        text = (root / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return PullRequestTemplate(note=f"{path} could not be read")
    return PullRequestTemplate(path=path, text=text)


def template_on_github(get: Callable[..., object], api: str, owner: str, repo: str,
                       ref: str = "") -> PullRequestTemplate:
    """The pull-request template on a GitHub branch, read through the contents API.

    ``get(url, params)`` returns the decoded JSON of a GET or None (the
    publication checks' reader); nothing is written.
    """
    base = f"{api.rstrip('/')}/repos/{owner}/{repo}/contents"
    params = {"ref": ref} if ref else None

    def listing(directory: str):
        data = get(f"{base}/{directory}" if directory else base, params)
        if not isinstance(data, list):
            return []
        return [(str(item.get("name", "")), item.get("type") == "dir")
                for item in data if isinstance(item, dict)]

    path, note = choose_template(_candidate_paths(listing))
    if not path:
        return PullRequestTemplate(note=note)
    data = get(f"{base}/{path}", params)
    if not isinstance(data, dict) or "content" not in data:
        return PullRequestTemplate(note=f"{path} could not be read")
    try:
        text = base64.b64decode(str(data["content"])).decode("utf-8", errors="replace")
    except ValueError:
        return PullRequestTemplate(note=f"{path} could not be decoded")
    return PullRequestTemplate(path=path, text=text)


# ---------------------------------------------------------------------------
# Facts the ticks come from
# ---------------------------------------------------------------------------

# Builds that compiled and packaged the change. "builds locally" is not ticked
# for a compile-only pass (no APK), a pass of the changed module alone, or a
# pass on an older revision because the base branch does not build here.
BUILT_STATUSES = frozenset({"success", PRODUCTION_ONLY_TEST_BUILD_PREEXISTING})
_DIFF_FILE = re.compile(r"^diff --git a/(?P<old>\S+) b/(?P<new>\S+)$", re.MULTILINE)
_TEST_FILE_NAME = re.compile(
    r"(?:^|/)(?:tests?|__tests__|spec)/|(?:Tests?|Spec|IT)\.(?:kt|kts|java|scala|groovy|swift)$"
    r"|_test\.(?:go|py|rs|dart|rb|exs?)$|(?:^|/)test_[^/]+\.py$|\.(?:test|spec)\.[jt]sx?$")


def is_test_file(path: str) -> bool:
    """Whether a changed repository file is a test, in any build system's layout."""
    path = str(path).replace("\\", "/")
    return is_test_source_path(path) or bool(_TEST_FILE_NAME.search(path))


@dataclass(frozen=True)
class TemplateFacts:
    """What the pipeline verified about a change, the only source of a tick."""

    build_status: str = ""
    build_task: str = ""
    # The tasks that ran and passed on the patched tree, the packaging one
    # first: NotallyX#1167 built `assembleDebug` and then ran
    # `:app:testDebugUnitTest`, and a "builds" box names the first.
    build_tasks: tuple = ()
    test_files: tuple = ()
    added_lines: tuple = ()
    removed_lines: tuple = ()
    linked_issue: str = ""
    # The pipeline fixes a misuse in code that exists; "feature" would need a
    # diff that adds one, which no step produces.
    change_kind: str = "fix"

    @property
    def built(self) -> bool:
        return str(self.build_status).lower() in BUILT_STATUSES

    @property
    def tasks(self) -> tuple:
        return tuple(task for task in (self.build_tasks or (self.build_task,)) if task)


# The phases that built the patched tree. baseline_* and
# analyzed_revision_production built other revisions, so a task they ran says
# nothing about this change.
PATCHED_PHASES = ("production", "patched_tests")


def _passed_tasks(validation: dict) -> tuple:
    """The tasks the validation ran and passed on the patched tree."""
    phases = validation.get("phases") if isinstance(validation.get("phases"), dict) else {}
    tasks = [str(phases[name].get("task", "") or "") for name in PATCHED_PHASES
             if isinstance(phases.get(name), dict)
             and str(phases[name].get("status", "")).lower() == "success"]
    if str(validation.get("status", "")).lower() == "success":
        tasks.append(str(validation.get("task", "") or ""))
    return tuple(dict.fromkeys(task for task in tasks if task))


def diff_lines(diff: str) -> tuple[list[str], list[str]]:
    """The added and removed lines of a unified diff, without their markers."""
    added, removed = [], []
    for line in str(diff or "").splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added.append(line[1:])
        elif line.startswith("-"):
            removed.append(line[1:])
    return added, removed


def facts_from_pull_request(metadata: dict, diff: Optional[str] = None) -> TemplateFacts:
    """The facts of a ``*_pr.json`` (or the solution it is built from)."""
    diff = str(metadata.get("diff", "") if diff is None else diff)
    validation = metadata.get("build_validation") or {}
    if not isinstance(validation, dict):
        validation = {}
    files = [match.group("new") for match in _DIFF_FILE.finditer(diff)]
    files += [str(path) for path in metadata.get("changed_files") or []]
    added, removed = diff_lines(diff)
    return TemplateFacts(
        build_status=str(validation.get("status", "") or ""),
        build_task=str(validation.get("task", "") or ""),
        build_tasks=_passed_tasks(validation),
        test_files=tuple(dict.fromkeys(path for path in files if is_test_file(path))),
        added_lines=tuple(added),
        removed_lines=tuple(removed),
        linked_issue=str(metadata.get("linked_issue", "") or "").strip().lstrip("#"),
    )


_STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
_CALL = re.compile(r"([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\(")
_LOG_CALLEE = re.compile(
    r"^(?:(?:android\.util\.)?Log\.\w+|Timber\.\w+|System\.(?:out|err)\.print\w*"
    r"|console\.\w+|(?:[\w$]+\.)?printStackTrace|println|print|printf|debugPrint|NSLog"
    r"|(?:[\w$]+\.)?(?:logger|LOGGER|Logger|log|LOG|logging)\.\w+"
    r"|log|logd|logi|logw|loge|logv|logDebug|debugLog)$")
_SECRET = re.compile(
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_\w{20,}"
    r"|\bAIza[0-9A-Za-z_\-]{35}\b|\bsk-[A-Za-z0-9]{20,}|\bxox[abprs]-[A-Za-z0-9-]{10,}"
    r"|(?i:\b[\w$]*(?:password|passwd|secret|api_?key|apikey|access_?key|auth_?token|token"
    r"|private_?key|client_?secret)[\w$]*\s*[:=]\s*[\"'][^\"'\s]{6,}[\"'])")


def _log_callees(lines: Iterable[str]) -> Counter:
    calls = Counter()
    for line in lines:
        code = _STRING.sub('""', line).split("//", 1)[0]
        for match in _CALL.finditer(code):
            callee = re.sub(r"\s+", "", match.group(1))
            if _LOG_CALLEE.match(callee):
                calls[callee] += 1
    return calls


def new_log_calls(facts: TemplateFacts) -> list[str]:
    """Log calls the change adds, not counting ones it only rewords.

    NotallyX#1167 changed the message of an existing `log(TAG, ...)` (MD5 ->
    SHA-256); a removed call to the same function balances an added one.
    """
    added = _log_callees(facts.added_lines) - _log_callees(facts.removed_lines)
    return sorted(added)


def secret_like_lines(facts: TemplateFacts) -> list[str]:
    """Added lines holding something shaped like a credential."""
    return [line.strip() for line in facts.added_lines if _SECRET.search(line)]


# ---------------------------------------------------------------------------
# Filling
# ---------------------------------------------------------------------------

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(r"^ {0,3}(?P<marks>#{1,6})\s+(?P<title>.+?)\s*#*\s*$")
_BOLD_HEADING = re.compile(r"^ {0,3}\*\*(?P<title>[^*]{2,60}?)\*\*\s*:?\s*$")
_CHECKBOX = re.compile(r"^(?P<lead>\s*[-*+]\s+)\[(?P<mark>[ xX])\](?P<text>.*)$")
# A line that only points at an issue the template cannot know: "Fixes #",
# "Closes #(issue)", "Resolves: #XXX", or an example number. The pipeline links
# an issue only from facts.linked_issue. fossasia/pslab-android's template
# opens with a bare "Fixes #" above its first heading (2026-10-30), so every
# section is cleaned, not only the one about issues.
_DANGLING_ISSUE = re.compile(
    r"(?i)^\s*(?:[-*+]\s+)?(?:\*\*)?(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?|related(?:\s+to)?"
    r"|issues?|tickets?)(?:\*\*)?\s*:?\s*(?:\*\*)?\s*#\s*(?:\(?[\w ]*\)?|<[^<>]*>|_+|\d+|\.{3})?"
    r"\s*$")
# home-assistant/android's "User Documentation: home-assistant/companion.home-assistant#":
# a cross-repository reference with no number.
_DANGLING_REFERENCE = re.compile(r"(?:^|[\s:])[\w.-]+/[\w.-]+#\s*$")
# thunderbird-android's "_<Please provide a detailed description of your
# contribution here>_" and "Linked Issue/Ticket: _<Please include the issue
# number ...>_": an instruction, not an HTML tag (no attributes), alone or
# after a label.
_PLACEHOLDER = re.compile(
    r"^\s*(?P<label>[^<>`]{0,60}?:\s*)?[_*]*<[A-Z][^<>=/]*\s[^<>=/]*>[_*]*\s*$")
# NewPipe's "- tested on device/emulator(phone/tablet/TV/...) running Android
# XX": an example that, left in, says the change was tested on a device.
_TESTED = re.compile(r"(?i)\btested\b")
_EMPTY_BULLET = re.compile(r"^\s*[-*+]\s*$")
# Hermes Control's "How to test" (2026-11-02): "1.", "2.", "3." with nothing
# after them, steps for the author to write. Left in, the form showed three
# empty steps; removed, the section is answered like any blank testing one.
_EMPTY_STEP = re.compile(r"^\s*\d+[.)]\s*$")
_NOT_APPLICABLE = re.compile(r"(?i)^\s*(?:n/?a|none|-)\.?\s*$")
# thunderbird-android's preamble ends with "After reading, delete this line
# and the above to use the template": the template asks for it to go.
_DELETE_ABOVE = re.compile(
    r"(?i)\bdelete\b[^\n]{0,40}\b(?:this line|everything|all)\b[^\n]{0,20}\babove\b")
_AI_WORD = re.compile(r"(?<![A-Za-z])A\.?I(?![A-Za-z])|(?i:\bLLMs?\b|artificial intelligence"
                      r"|generative|copilot|chatgpt|\bclaude\b|\bgemini\b)")
_ASSISTED = re.compile(
    r"(?i)\bassist\w*|\bAI[- ]aided|\bwith\s+(?:the\s+)?help\s+(?:of|from)\s+(?:an?\s+)?(?:AI|LLM)"
    r"|\b(?:use[ds]?|using)\b[^.?;]{0,30}(?<![A-Za-z])(?:AI|LLMs?)(?![A-Za-z])"
    r"|\b(?:AI|LLM)\s+tools?\s+(?:were\s+|was\s+)?used|\bpartially\b")
# Any negation: thunderbird-android's "This contribution does not include any
# changes created or assisted by AI" names assistance only to deny it, and was
# taken for the assisted option before (2026-10-30).
_NEGATION = r"\b(?:no|not|never|none|without|neither|nor)\b|n['\u2019]t\b"
_NOT_ASSISTED = re.compile(r"(?i)" + _NEGATION
                           + r"|\bgenerat\w*|\bfully\b|\bentirely\b|\bautonomous\w*")
# A box that acknowledges a policy rather than answering how AI was used:
# home-assistant/android's "I have read the ... AI Policy", NewPipe's "The
# proposed changes follow the AI policy". Never ticked; the operator reads it.
_ACKNOWLEDGEMENT = re.compile(r"(?i)\bpolic(?:y|ies)\b|\bguidelines?\b|\brules\b")
# A box by which the contributor states that no AI was used (App Manager's
# "I know that generating a PR ... using AI/LLM is prohibited", 2026-11-01).
_AFFIRMATION = re.compile(
    r"(?i)^\W*(?:I|we)\s+(?:know|affirm|certify|attest|confirm|declare|understand|agree)\b"
    r".*(?:prohibit\w*|forbid\w*|not\s+(?:\w+\s+){0,2}(?:allowed|permitted|accepted)"
    r"|\bwithout\b|\bno\b)")
_YES_NO = re.compile(r"(?i)^\W*(?:yes|no)\b")
_YES = re.compile(r"(?i)^\W*yes\b")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")

_ROLES = (
    ("ai", re.compile(r"(?<![A-Za-z])A\.?I(?![A-Za-z])|(?i:\bLLM|artificial intelligence"
                      r"|generative|copilot|chatgpt)")),
    ("type", re.compile(r"(?i)\btype of (?:change|pr|pull request)|\bkind of change"
                        r"|\bchange type|\bcategory\b|\bwhat kind\b")),
    ("issue", re.compile(r"(?i)\bissues?\b|\bticket|\blinked\b|\bfixes\b|\bcloses\b|\breferences?\b"
                         r"|\btracking\b")),
    ("screenshots", re.compile(r"(?i)screen\s*shots?|screen\s*recordings?|\bvideos?\b|\bdemo\b"
                               r"|\bvisual\b|\bui changes\b|\bbefore\b.*\bafter\b")),
    ("checklist", re.compile(r"(?i)\btest|\bchecklist\b|\bquality\b|\bverif\w*|\bvalidation\b")),
)
# Where the description goes, best first. NewPipe's template (2026-10-30)
# opens with "What is it?" (its type-of-change boxes) and only then
# "Description of the changes in your PR"; the first weak match took the
# description and the real section kept "take over the world".
_DESCRIPTION_RANKS = (
    re.compile(r"(?i)\bdescri\w*"),
    re.compile(r"(?i)\bsummary\b|\boverview\b"),
    re.compile(r"(?i)\bmotivation\b|\bobjective\b|\bwhat (?:does|is|was|did)\b|\bchanges?\b"
               r"|\babout\b|\bpurpose\b|\bcontext\b|\bdetails\b|\bproposed\b|\bcontent\b"),
)
_DEVICE = re.compile(r"(?i)\bdevices?\b|\bemulators?\b|\bsimulators?\b|\bphysical\b"
                     r"|\bhardware\b|\bmanual(?:ly)?\s+test|\bAPI\s+level\b|\bon\s+(?:android|ios)\b")
_TESTS_PASS = re.compile(r"(?i)\btests?\s+(?:all\s+)?pass|\bpassing\s+tests?\b|\btest\s+suite\b"
                         r"|\ball\s+tests\b")
_TESTS_ADDED = re.compile(
    r"(?i)\b(?:add|added|adds|adding|update|updated|wrote|written|write|include[sd]?|new)\b"
    r"[^.;]{0,40}\btests?\b|\btests?\b[^.;]{0,30}\b(?:added|updated|written|included)\b")
# A box claims a build only in so many words, and only a build: AntennaPod's
# "I have run the automated code checks using `./gradlew checkstyle lint`" and
# thunderbird-android's "adheres to the existing codestyle (run `gradlew
# spotlessCheck`)" were ticked on a passing assembleDebug (2026-10-30).
_BUILDS = re.compile(r"(?i)\bbuild(?:s|ing)?\b|\bbuilt\b|\bcompil\w+|\bassembl\w*")
_OTHER_CHECK = re.compile(
    r"(?i)\blint\w*|checkstyle|spotbugs|detekt|ktlint|spotless|\bstyle\b|\bformat\w*"
    r"|\banaly[sz]\w*|\bwarnings?\b|\brelease\b|\btest\w*|\bsign\w*|proguard|\br8\b|\bminif\w*"
    r"|\bbenchmark\w*|\bCI\b|\bcheck\b|\bruns?\b|\blaunch\w*|\bworks?\b")
_GRADLE_TASKS = re.compile(r"gradlew(?:\.bat)?\s+([^`\n]+)")
_BUILD_TASK = re.compile(r"(?i)^(?::?[\w.-]+:)*(?:assemble\w*|build\w*|compile\w*|bundle\w*|clean)$")
_SECRETS = re.compile(r"(?i)\bsecrets?\b|\bcredentials?\b|\bapi[ _-]?keys?\b|\bpasswords?\b"
                      r"|\btokens?\b|\bprivate[ _-]?keys?\b")
_LOGS = re.compile(r"(?i)\blog(?:s|ging)?\b|\bprint\s+statements?\b|console\.log"
                   r"|\bdebug(?:ging)?\s+(?:logs?|code|statements?|output|prints?)\b")
# "No secrets / no debug logs" is a claim of absence; a box that asks for
# logging ("Added logging for the new flow") is not one.
_ABSENCE = re.compile(r"(?i)\b(?:no|not|none|without|never|removed?|free\s+of)\b|n['\u2019]t\b")
_BUG_FIX = re.compile(r"(?i)\bbug\b|\bbug-?fix\w*|\bhotfix\b|\bfix(?:es|ed)?\b|\bpatch\b")
_BREAKING = re.compile(r"(?i)(?<!non-)(?<!non )\bbreaking\b")
_FEATURE = re.compile(r"(?i)\bfeatures?\b|\benhancements?\b|\bfunctionality\b")
_BUG_FIX_LABEL = re.compile(r"(?i)^\W*bug\s*-?\s*fix")
# text_problems' summary-length problem (utils.pr_text): measured on the plain
# text, since a template's own preamble is not the summary.
_SUMMARY_PROBLEM = "summary of "


@dataclass
class _Section:
    heading: str = ""
    title: str = ""
    level: int = 0
    lines: list = field(default_factory=list)
    role: str = ""
    rank: int = 0


def _description_rank(title: str) -> int:
    for rank, pattern in enumerate(_DESCRIPTION_RANKS):
        if pattern.search(title):
            return rank
    return -1


def _role(title: str) -> str:
    for role, pattern in _ROLES:
        if pattern.search(title):
            return role
    return "description" if _description_rank(title) >= 0 else "other"


def _parse(text: str) -> list[_Section]:
    sections = [_Section()]
    for line in text.split("\n"):
        heading = _HEADING.match(line)
        bold = None if heading or _CHECKBOX.match(line) else _BOLD_HEADING.match(line)
        if heading or bold:
            title = (heading or bold).group("title").strip()
            level = len(heading.group("marks")) if heading else 7
            sections.append(_Section(heading=line.rstrip(), title=title, level=level,
                                     role=_role(title), rank=_description_rank(title)))
        else:
            sections[-1].lines.append(line.rstrip())
    return sections


def _plain(text: str) -> str:
    """Words a reader sees: link texts kept, URLs dropped.

    home-assistant/android links its AI policy at contributors.home-assistant.io,
    and "assistant" in the URL read as an AI-assisted answer (2026-10-30).
    """
    return _URL.sub("", _LINK.sub(r"\1", str(text)))


def _label(text: str) -> str:
    """The option's own words: its bold part, or the text before an explanation."""
    text = _plain(text)
    bold = _BOLD.search(text)
    if bold:
        return bold.group(1) or bold.group(2)
    return re.split(r"\s+\(|\s+[-\u2013\u2014]\s+|[:,]\s", text.strip(), maxsplit=1)[0]


def is_assisted_option(text: str) -> bool:
    """Whether an AI-disclosure option means AI-assisted, in any common wording."""
    for candidate in (_label(text), _plain(text)):
        if _NOT_ASSISTED.search(candidate):
            return False
        if _ASSISTED.search(candidate):
            return True
    return False


def _with_tasks(text: str, tasks: Iterable[str]) -> str:
    tasks = [f"`{task}`" for task in tasks if task]
    if not tasks:
        return text
    ran = " and ".join(tasks)
    stripped = text.rstrip()
    return (f"{stripped} Ran {ran}." if stripped.endswith((".", ")", "!"))
            else f"{stripped} (ran {ran})")


def _claims_only_a_build(item: str, facts: TemplateFacts) -> bool:
    """Whether a box claims that the change builds, and nothing else the pipeline did not run."""
    if not _BUILDS.search(item):
        return False
    code = " ".join(re.findall(r"`([^`]*)`", item))
    if _OTHER_CHECK.search(_plain(re.sub(r"`[^`]*`", " ", item))) or _OTHER_CHECK.search(code):
        return False
    ran = {task.lstrip(":") for task in facts.tasks}
    for command in _GRADLE_TASKS.findall(item):
        for task in command.split():
            if task.startswith("-") or task.lower() in ("or", "and", "similar", "etc"):
                continue
            task = task.strip(".,;()")
            if task and not _BUILD_TASK.match(task) and task.lstrip(":") not in ran:
                return False
    return True


# A template line addressed to the author: SMSecure's "Describe how this was
# tested." and "Add any extra context, screenshots, migration notes, or
# follow-up work here." were copied into the run_v4 body unchanged
# (2026-10-31), under "Testing" and "Notes". An imperative to the author, or
# a noun phrase standing for the text to write ("A clear and concise
# description of ..."), in a short plain line that is not a checkbox.
_AUTHOR_INSTRUCTION = re.compile(
    r"(?i)^(?:please\s+)?(?:briefly\s+)?(?:describe|explain|add|provide|include|list|mention"
    r"|link|summari[sz]e|write|tell\s+us|attach|give|outline|share|specify|insert|enter"
    r"|put|fill\s+in|detail|state|document|note\s+(?:any|here)|reference)\b"
    r"|^(?:an?|the|your)\s+(?:(?:clear|concise|short|brief|detailed|quick)\s+(?:and\s+)?)*"
    r"(?:description|summary|explanation|overview)\s+(?:of|here)\b"
    r"|\bgoes\s+here\.?$|^(?:your\s+)?(?:text|description|notes?)\s+here\.?$")
_LEAD_MARKS = re.compile(r"^\s*(?:[-*+>]\s+|\d+[.)]\s+)?[_*]*")
# A section asking why the change is made, which the description answers.
_REASON_TITLE = re.compile(r"(?i)^\W*why\b|\breasons?\b|\brationale\b|\bmotivation\b"
                           r"|\bproblem\b")
_TESTED_TITLE = re.compile(r"(?i)\btest(?:s|ed|ing)?\b|\bhow (?:was|is) this tested\b")


def author_instruction(line: str) -> bool:
    """Whether a template line asks the author to write something."""
    if _CHECKBOX.match(line) or _HEADING.match(line) or _BOLD_HEADING.match(line):
        return False
    text = _LEAD_MARKS.sub("", str(line)).strip().strip("_*").strip()
    return bool(text) and len(text.split()) <= 40 and bool(_AUTHOR_INSTRUCTION.search(text))


def template_instructions_left(body: str, template_text: str) -> list[str]:
    """Template lines asking the author to write something, left unchanged in a body.

    Each is one problem for the text check: the body is rewritten (or the form
    filled again); a template line the description itself copied is found
    the same way.
    """
    instructions = {line.strip() for line in _COMMENT.sub("", str(template_text or "")).splitlines()
                    if author_instruction(line)}
    left = [line.strip() for line in str(body or "").splitlines() if line.strip() in instructions]
    problems = [f"pull-request template instruction left unfilled: {line!r}"
                for line in dict.fromkeys(left)]
    # Placeholders that are not sentences: empty numbered steps the template
    # shipped, and a description-like section (Summary, Overview) answered
    # "n/a" although the change has a description (Hermes Control,
    # 2026-11-02: "## Summary / n/a" above "## Description" and "## How to
    # test / 1. 2. 3.").
    template_lines = {line.strip() for line in _COMMENT.sub("", str(template_text or "")).splitlines()}
    steps = [line.strip() for line in str(body or "").splitlines()
             if _EMPTY_STEP.match(line) and line.strip() in template_lines]
    if steps:
        problems.append("pull-request template steps left empty: " + " ".join(dict.fromkeys(steps)))
    for section in _parse(str(body or "")):
        content = [line for line in section.lines if line.strip()]
        if (section.heading and content
                and ((section.role == "description" and 0 <= section.rank <= 1)
                     or _REASON_TITLE.search(section.title))
                and all(_NOT_APPLICABLE.match(line) for line in content)):
            problems.append(f"pull-request template section {section.title!r} answered "
                            f"{content[0].strip()!r} instead of being filled or dropped")
    return problems


def testing_sentence(facts: TemplateFacts) -> str:
    """What a testing section can say truthfully: the build that passed, the tests added.

    Never a device test: RVSec ran the original APK, not the patched one.
    """
    parts = []
    if facts.built and facts.tasks:
        parts.append("Built with " + ", ".join(f"`./gradlew {task}`" for task in facts.tasks) + ".")
    elif facts.built:
        parts.append("The project builds with this change.")
    if facts.test_files:
        parts.append("Added " + ", ".join(f"`{path.rsplit('/', 1)[-1]}`"
                                          for path in facts.test_files) + ".")
    parts.append("Not run on a device or emulator.")
    return " ".join(parts)


def _without_placeholder(line: str) -> Optional[str]:
    """A template line with its placeholder resolved, or None when it goes."""
    if _CHECKBOX.match(line):
        return line
    if _DANGLING_ISSUE.match(line) or _DANGLING_REFERENCE.search(line):
        return None
    if _TESTED.search(line) and _DEVICE.search(line):
        return None  # RVSec ran the original APK, never the patched one.
    if author_instruction(line):
        return None
    placeholder = _PLACEHOLDER.match(line)
    if placeholder:
        label = (placeholder.group("label") or "").strip()
        issue = dict(_ROLES)["issue"]
        return f"{label} None." if label and issue.search(label) else None
    return "" if _EMPTY_BULLET.match(line) or _EMPTY_STEP.match(line) else line


def _blank(lines: list) -> bool:
    return not any(str(line).strip() for line in lines)


def _without_preamble(text: str) -> str:
    """The template after a line asking the author to delete it and what is above."""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if _DELETE_ABOVE.search(line):
            rest = lines[index + 1:]
            # The rule that separated the preamble from the form goes with it.
            while rest and (not rest[0].strip() or re.fullmatch(r"\s*([-*_])\1{2,}\s*", rest[0])):
                rest = rest[1:]
            return "\n".join(rest)
    return text


def template_headings(template: str) -> list[str]:
    """The headings of a template, without the ones inside HTML comments."""
    return [section.heading.strip()
            for section in _parse(_without_preamble(_COMMENT.sub("", str(template))))
            if section.heading]


def follows_template(text: str, template: str) -> bool:
    """Whether a text is already that template filled: it has most of its headings.

    A description written by hand in the form, or a record whose body is
    already the filled form, is sent as it is instead of nested in a second copy.
    """
    headings = template_headings(template)
    lines = {line.strip() for line in str(text).splitlines()}
    return bool(headings) and 2 * sum(heading in lines for heading in headings) > len(headings)


def fill_template(template: str, description: str, facts: TemplateFacts) -> tuple[str, list[str]]:
    """The template filled with the description and the verified facts, and notes.

    The notes are for the operator, never the maintainer: what the form asks
    that the pipeline cannot answer truthfully.
    """
    notes: list[str] = []
    text = _COMMENT.sub("", str(template).replace("\r\n", "\n").replace("\r", "\n"))
    sections = _parse(_without_preamble(text))

    # Every box starts unticked: a tick the template ships with is not a fact.
    boxes = []  # (section, line index, match)
    for section in sections:
        for index, line in enumerate(section.lines):
            match = _CHECKBOX.match(line)
            if match:
                section.lines[index] = f"{match.group('lead')}[ ]{match.group('text')}"
                boxes.append((section, index, match))

    def tick(section: _Section, index: int, match, text_override: str = "") -> None:
        section.lines[index] = f"{match.group('lead')}[x]{text_override or match.group('text')}"

    def question(section: _Section, index: int) -> str:
        """The line a Yes/No box answers: the nearest text above it, else its heading."""
        for line in reversed(section.lines[:index]):
            if line.strip() and not _CHECKBOX.match(line):
                return _plain(re.sub(r"^\s*[-*+]\s+", "", line))
        return section.title

    def context(box) -> str:
        section, index, match = box
        option = _plain(match.group("text"))
        return f"{question(section, index)} {option}" if _YES_NO.match(option) else option

    # The AI-disclosure question: the options of a section about AI, or else
    # every box that mentions AI (or answers Yes/No to a line that does).
    ai_sections = [section for section in sections if section.role == "ai"]
    ai_boxes = [box for box in boxes if box[0].role == "ai"] or (
        [] if ai_sections else [box for box in boxes if _AI_WORD.search(context(box))])
    handled = {id(box[2]) for box in ai_boxes}
    options = []
    for box in ai_boxes:
        option = _plain(box[2].group("text"))
        if _AFFIRMATION.search(option) and not _YES_NO.match(option):
            # App Manager (2026-11-01): "I know that generating a PR or its
            # code ... using AI/LLM is prohibited". Neither an answer nor a
            # box to tick for the user: the note quotes it, the user decides
            # (a note, never a blocker).
            notes.append(f"the template asks you to affirm \"{option.strip()}\"; the repository "
                         "requires that statement on submission, so it is left unticked: "
                         "decide yourself whether to submit")
            continue
        if (_ACKNOWLEDGEMENT.search(option) and not _YES_NO.match(option)
                and not is_assisted_option(option)):
            notes.append(f"the template asks to confirm \"{option.strip()}\"; left unticked "
                         "for you to read and tick")
        else:
            options.append(box)
    if options:
        def assisted(box) -> bool:
            option = _plain(box[2].group("text"))
            if _YES_NO.match(option):
                return bool(_YES.match(option)) and is_assisted_option(question(box[0], box[1]))
            return is_assisted_option(option)

        chosen = next((box for box in options if assisted(box)), None)
        if chosen:
            tick(*chosen)
        else:
            notes.append("the template's AI-disclosure question has no AI-assisted option; "
                         "answer it in the description before publishing")
    for section in ai_sections:
        if not any(box[0] is section for box in boxes):
            # A free-text AI question: answered in words.
            section.lines = [line for line in section.lines if line.strip()] + ["AI-assisted."]

    # The type of change: the bug-fix-like option, once.
    type_boxes = [box for box in boxes if box[0].role == "type" and id(box[2]) not in handled]
    if not type_boxes:
        type_boxes = [box for box in boxes if id(box[2]) not in handled
                      and _BUG_FIX_LABEL.match(_label(box[2].group("text")))]
    handled |= {id(box[2]) for box in type_boxes}
    if facts.change_kind == "fix" and type_boxes:
        # "Breaking" anywhere in the option excludes it; "feature" only in its
        # label, since a bug-fix option may explain itself as "adds no feature".
        candidates = [box for box in type_boxes
                      if not _BREAKING.search(box[2].group("text"))
                      and not _FEATURE.search(_label(box[2].group("text")))]
        chosen = (next((box for box in candidates if re.search(r"(?i)\bbug", box[2].group("text"))),
                       None)
                  or next((box for box in candidates if _BUG_FIX.search(box[2].group("text"))),
                          None))
        if chosen:
            tick(*chosen)
        else:
            notes.append("the template's type of change has no bug-fix option; none was ticked")

    logs = new_log_calls(facts)
    secrets = secret_like_lines(facts)
    for section, index, match in boxes:
        if id(match) in handled:
            continue
        item = match.group("text")
        words = _plain(item)
        if _DEVICE.search(words):
            continue  # RVSec ran the original APK, never the patched one.
        if _TESTS_PASS.search(words):
            continue  # Which tests ran is not recorded as passing for the whole suite.
        if _TESTS_ADDED.search(words):
            if facts.test_files:
                tick(section, index, match)
            continue
        if _SECRETS.search(words) or _LOGS.search(words):
            if (_ABSENCE.search(words) and not (_SECRETS.search(words) and secrets)
                    and not (_LOGS.search(words) and logs)):
                tick(section, index, match)
            continue
        if facts.built and _claims_only_a_build(item, facts):
            tick(section, index, match, _with_tasks(item, facts.tasks))

    # Text sections: the template's placeholders go; what is left is its own.
    for section in sections:
        section.lines = [cleaned for cleaned in map(_without_placeholder, section.lines)
                         if cleaned is not None]
    ranked = [section for section in sections if section.role == "description"]
    target = min(ranked, key=lambda section: section.rank) if ranked else None
    for position, section in enumerate(sections):
        following = sections[position + 1] if position + 1 < len(sections) else None
        parent = following is not None and following.level > section.level and section.level
        if section is target:
            kept = [line for line in section.lines if _CHECKBOX.match(line)]
            section.lines = ["", description.strip(), *([""] + kept if kept else []), ""]
        elif section.role == "issue":
            if facts.linked_issue:
                section.lines = [line for line in section.lines if line.strip()] + [
                    f"Fixes #{facts.linked_issue}"]
            elif _blank(section.lines):
                section.lines = ["None."]
        elif section.role == "screenshots":
            section.lines = ["n/a"]
        elif section.heading and _blank(section.lines) and not parent and _TESTED_TITLE.search(
                section.title):
            # SMSecure's "Testing" asked "Describe how this was tested."
            # (run_v4, 2026-10-31): answered with what ran, never "n/a".
            section.lines = [testing_sentence(facts)]
        elif (section.heading and _blank(section.lines) and not parent and target is not None
                and ((section.role == "description" and 0 <= section.rank <= 1)
                     or _REASON_TITLE.search(section.title))):
            # CometOTP's "Why" under "What this changes" (2026-11-07) is the
            # same: the description already says why the code changed.
            # A second description-like section (Hermes Control's "Summary"
            # above its "Description", 2026-11-02): the description went
            # to the other one, and "n/a" would say the change has none.
            section.heading = ""
            section.lines = []
        elif section.heading and _blank(section.lines) and not parent:
            section.lines = ["n/a"]

    if target is None:
        sections[0].lines = [description.strip(), "", *sections[0].lines]

    out: list[str] = []
    for section in sections:
        if section.heading:
            if out and out[-1].strip():
                out.append("")
            out.append(section.heading)
            body = section.lines
            if body and body[0].strip():
                out.append("")
        else:
            body = section.lines
        out.extend(body)
    rendered = "\n".join(out)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip() + "\n"
    return rendered.rstrip("\n"), list(dict.fromkeys(notes))


def body_for_repository(template: PullRequestTemplate, description: str,
                        facts: TemplateFacts) -> tuple[str, list[str]]:
    """The pull-request body for a repository: the filled template, or the plain text.

    Without a template the description is returned unchanged; a template that
    could not be chosen leaves its note. A description that already follows
    the template is not filled a second time.
    """
    if not template:
        return description, [template.note] if template.note else []
    if follows_template(description, template.text):
        return description, []
    return fill_template(template.text, description, facts)


def problems_in_own_words(check: Callable[[str], list], body: str, plain: str) -> list:
    """The text problems of a filled body that the pipeline's own words cause.

    The template is the maintainers' text: thunderbird-android's preamble
    (2026-10-30) put a filled body at 376 words of "summary" while the plain
    text had none, and a held PR could not be rewritten out of it. The plain
    text is checked in full; the filled body adds only a kind of problem
    (the words before its first colon) that neither the plain text nor the
    template's own words have, and never the summary length.
    """
    plain = str(plain or "").strip()
    if not plain or plain not in str(body):
        return list(check(body))
    own = list(check(plain))

    def kind(problem) -> str:
        return str(problem).split(":", 1)[0]

    known = {kind(problem) for problem in [*own, *check(str(body).replace(plain, "", 1))]}
    extra = [problem for problem in check(body)
             if kind(problem) not in known and not str(problem).startswith(_SUMMARY_PROBLEM)]
    return list(dict.fromkeys([*own, *extra]))
