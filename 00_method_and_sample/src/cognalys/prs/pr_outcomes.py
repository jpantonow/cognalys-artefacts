"""What happened to published pull requests, so a rejected proposal is not made again.

QuantumBadger/RedReader closed PR #1373 with a written explanation: the premise
was wrong and the change was misleading. Nothing in the pipeline read a closed
pull request -- the revision step skipped it as "not open" -- so the same
finding could be analysed, patched and proposed to the same maintainers again
by any later run.

A closed or merged pull request is recorded here with the maintainers'
comments, one file per repository under data/pr_outcomes. Publication then
refuses a new pull request for a finding (same repository, specification, class
and method) whose previous pull request was closed without being merged.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from cognalys.prs.impact_class import recorded_impact_class
from cognalys.workspace import data_path


OUTCOMES_DIR = data_path("pr_outcomes")
MAX_COMMENT_CHARACTERS = 2000
# Where runs keep their pull requests: outputs/<run>/pull_requests and the
# same one or two levels deeper (queue reruns, consolidated folders, scan
# targets). Not a recursive walk: outputs also holds whole clones and
# worktrees. Read by the duplicate check and by outcome_report.published().
_RECORD_GLOBS = ("*/pull_requests/*_pr_created.json", "*/*/pull_requests/*_pr_created.json",
                 "*/*/*/pull_requests/*_pr_created.json", "pull_requests/*_pr_created.json")


def _repository_file(metadata: dict, outcomes_dir: Path) -> Path:
    owner = re.sub(r"[^\w.-]+", "_", str(metadata.get("repo_owner", "")).lower())
    repo = re.sub(r"[^\w.-]+", "_", str(metadata.get("repo_name", "")).lower())
    return Path(outcomes_dir) / f"{owner}__{repo}.json"


def _load(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _same_finding(record: dict, metadata: dict) -> bool:
    return all(
        str(record.get(field, "")) == str(metadata.get(field, ""))
        for field in ("spec", "csv_class", "method_name")
    )


def record_pr_outcome(metadata: dict, pr: dict, state: dict,
                      feedback: list[dict[str, Any]],
                      outcomes_dir: Path = OUTCOMES_DIR) -> Path:
    path = _repository_file(metadata, outcomes_dir)
    records = _load(path)
    url = str(pr.get("html_url", ""))
    entry = {
        "pull_request": url,
        "state": str(state.get("status", "")),
        "finding_id": str(metadata.get("finding_id", "")),
        "spec": str(metadata.get("spec", "")),
        "csv_class": str(metadata.get("csv_class", "")),
        "method_name": str(metadata.get("method_name", "")),
        "title": str(metadata.get("title", "")),
        # The PR record's security-impact class (prs.impact_class), for the
        # survey; not part of the response given to the model.
        "impact_class": recorded_impact_class(metadata),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        # Why a closed pull request was recorded as merged
        # (outcome_report.incorporated_evidence); only for those.
        **({"incorporated_evidence": state["incorporated_evidence"]}
           if state.get("incorporated_evidence") else {}),
        "maintainer_feedback": [
            {
                "author": str(item.get("author", "")),
                "kind": str(item.get("kind", "")),
                "body": str(item.get("body", ""))[:MAX_COMMENT_CHARACTERS],
            }
            for item in feedback or []
            if isinstance(item, dict) and str(item.get("body", "")).strip()
        ],
    }
    records = [record for record in records if record.get("pull_request") != url or not url]
    records.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)
    return path


def rejected_outcome_for(metadata: dict,
                         outcomes_dir: Path = OUTCOMES_DIR) -> Optional[dict]:
    """The recorded closed-without-merge pull request for this finding, if any."""
    for record in _load(_repository_file(metadata, outcomes_dir)):
        if record.get("state") == "closed" and _same_finding(record, metadata):
            return record
    return None


MAX_SIMILAR_RESPONSES = 4
# No limit on a maintainer's answer: it reaches the prompts whole (user
# decision of 2026-11-10). session-android#2241's answer was cut at 400
# characters, before "We'll hold off merging for now since it doesn't change
# anything functionally"; 12 of the 33 recorded answers with words were cut.
# Each recorded comment is already bounded by MAX_COMMENT_CHARACTERS.


def responses_to_same_rule(spec: str, outcomes_dir: Optional[Path] = None,
                           limit: int = MAX_SIMILAR_RESPONSES,
                           excluding_repository_url: str = "") -> list[dict]:
    """What maintainers did with earlier proposals for the same specification.

    TagMo and androidtv-news closed WebView-debugging pull requests as
    "enabled intentionally", SlimBook merged one; HomeApp's maintainer
    answered an SSL-error change with "you can put it behind a toggle".
    None of that reached the next proposal for the same rule. It is given to
    generation as evidence of what maintainers accept, never as a reason to
    drop a confirmed finding: decided ones (merged, closed) with words first.

    ``excluding_repository_url`` leaves that repository's own responses out:
    they are this_rule_in_same_repository's (InterTune run_v2, 2026-11-08:
    the closure of InterTune#52 for this very method reached adjudication
    as what "maintainers of other projects" answered).
    """
    spec = str(spec or "").strip()
    if not spec:
        return []
    directory = Path(outcomes_dir or OUTCOMES_DIR)
    excluded = _outcomes_file_for(excluding_repository_url, directory)
    found = [response for path in sorted(directory.glob("*.json")) if path != excluded
             for record in _load(path) if record.get("spec") == spec
             for response in [_maintainer_response(record, path.stem)] if response]
    return _decided_first(found, limit)


def _outcomes_file_for(repository_url: str, outcomes_dir: Path) -> Optional[Path]:
    """data/pr_outcomes file of a GitHub repository URL, or None for any other."""
    match = re.search(r"github\.com[/:]([^/]+)/([^/#]+?)(?:\.git)?/?$",
                      str(repository_url or "").strip(), re.IGNORECASE)
    if not match:
        return None
    return _repository_file({"repo_owner": match.group(1), "repo_name": match.group(2)},
                            outcomes_dir)


def responses_to_this_rule_in_same_repository(repository_url: str, spec: str,
                                              outcomes_dir: Optional[Path] = None,
                                              limit: int = MAX_SIMILAR_RESPONSES) -> list[dict]:
    """What this repository's maintainers did with our proposals for this same rule.

    InterTune#52 (MessageDigestSpec, WidgetStore.hash) was closed with "this
    hash only names the widget's cached cover files"; the next run's
    adjudication read it among other projects' answers and the finding
    became a ready pull request again. Each response names the code the
    proposal changed (changed_code), so the model can tell an answer about
    this very code from one about another method of the repository.
    Evidence about this finding and what these maintainers accept, never a
    verdict that dismisses it.
    """
    spec = str(spec or "").strip()
    path = _outcomes_file_for(repository_url, Path(outcomes_dir or OUTCOMES_DIR))
    if not spec or path is None:
        return []
    found = []
    for record in _load(path):
        response = _maintainer_response(record, path.stem)
        if response and record.get("spec") == spec:
            changed = ".".join(part for part in (
                str(record.get("csv_class", "")).strip(),
                str(record.get("method_name", "")).strip()) if part)
            found.append({**response, **({"changed_code": changed} if changed else {})})
    return _decided_first(found, limit)


def responses_in_same_repository(repository_url: str, spec: str = "",
                                 outcomes_dir: Optional[Path] = None,
                                 limit: int = MAX_SIMILAR_RESPONSES) -> list[dict]:
    """What this repository's maintainers did with our proposals for other rules.

    Amethyst#4642 was closed under GCMParameterSpecSpec ("the provider already
    generates the IV"); the Amethyst v3 CipherSpec draft of 2026-10-31 never
    saw it, because only same-specification responses reached generation.
    Responses for ``spec`` itself are left to
    responses_to_this_rule_in_same_repository. Evidence
    of what these maintainers accept, never a reason to drop a finding.
    """
    path = _outcomes_file_for(repository_url, Path(outcomes_dir or OUTCOMES_DIR))
    if path is None:
        return []
    spec = str(spec or "").strip()
    found = []
    for record in _load(path):
        response = _maintainer_response(record, path.stem)
        if response and (not spec or record.get("spec") != spec):
            found.append({**response, "rule": str(record.get("spec", ""))})
    return _decided_first(found, limit)


# A closure that answers the repository's rules, not the change: summit#54
# ("Sorry I am not currently accepting PR requests"), tuisku#11 ("ai generated
# or assisted code/pull requests aren't allowed"). Given as the answer to a
# rule (2026-10-31: one of the four MessageDigestSpec responses was summit's)
# it reads as a rejection of the change itself.
_REPOSITORY_POLICY = re.compile(
    r"\bnot\s+(?:currently\s+|yet\s+)?(?:accepting|taking|open\s+to)\s+(?:any\s+|external\s+|outside\s+|new\s+)?"
    r"(?:pull\s+requests?|prs?|pr\s+requests|contributions|patches)\b"
    r"|\b(?:ai|llm)[- ](?:generated|assisted|written)\b[^.]{0,80}?\b(?:not\s+(?:allowed|accepted|welcome)"
    r"|aren'?t\s+(?:allowed|accepted|welcome)|forbidden|prohibited|banned)"
    r"|\bno\s+(?:ai|llm)[- ](?:generated\s+)?(?:code|contributions|pull\s+requests|prs)\b"
    r"|\b(?:sign|signed)\s+(?:the|our)\s+(?:cla|contributor\s+license\s+agreement)\b",
    re.IGNORECASE)


def _feedback_items(record: dict, from_bots: bool) -> list[dict]:
    """The recorded feedback written by review bots (``from_bots``) or by people.

    A review bot's comments recorded as feedback (paroj/DSub2000#160: four
    Copilot review comments) reached every other repository's SSLContextSpec
    finding as what the maintainer said.
    """
    from cognalys.prs.pr_github import is_bot_login

    return [item for item in record.get("maintainer_feedback", []) or []
            if isinstance(item, dict)
            and is_bot_login(str(item.get("author", ""))) == from_bots]


def _maintainer_items(record: dict) -> list[dict]:
    """The recorded feedback whose author is not a review bot."""
    return _feedback_items(record, from_bots=False)


def repository_policy_closure(record: dict) -> bool:
    """Whether a closed pull request's maintainer words state a repository policy."""
    said = " ".join(str(item.get("body", "")) for item in _maintainer_items(record))
    return record.get("state") == "closed" and bool(_REPOSITORY_POLICY.search(said))


# The key under which a response carries a review bot's comments. Named so it
# reads as what it is with no prompt sentence changed: planning and generation
# get it as automated review points on that earlier pull request; the
# adjudication and the behaviour review do not (as_maintainer_position).
AUTOMATED_REVIEW_KEY = "automated_review_comments_not_from_the_maintainer"


def _maintainer_response(record: dict, fallback_repository: str) -> dict:
    """One recorded outcome as evidence for the model, or {} when it says nothing.

    Only people's words are maintainer_said. A review bot's comments are kept
    apart, marked with their author as a bot (user decision of 2026-11-10:
    DSub2000#160's Copilot points are valid code-review points, not the
    maintainer's position).
    """
    if record.get("state") not in {"merged", "closed", "open"}:
        return {}
    said = " | ".join(" ".join(str(item.get("body", "")).split())
                      for item in _maintainer_items(record))
    automated = [
        {"author": str(item.get("author", "")), "author_is_bot": True,
         "comment": " ".join(str(item.get("body", "")).split())}
        for item in _feedback_items(record, from_bots=True)
        if str(item.get("body", "")).strip()]
    if record.get("state") == "open" and not said and not automated:
        return {}
    if repository_policy_closure(record):
        return {}
    match = _PULL_URL.search(str(record.get("pull_request", "")))
    return {
        "repository": f"{match.group(1)}/{match.group(2)}" if match else fallback_repository,
        "outcome": record.get("state"),
        "proposal": str(record.get("title", "")) or str(record.get("method_name", "")),
        "maintainer_said": said,
        **({AUTOMATED_REVIEW_KEY: automated} if automated else {}),
    }


def as_maintainer_position(responses: list) -> list[dict]:
    """``responses`` as the maintainers' position only: without a review bot's
    comments, and without an open pull request on which only a bot spoke.

    For the adjudication and the behaviour review, which read the responses
    as what maintainers think of the change.
    """
    kept = []
    for response in responses or []:
        if not isinstance(response, dict):
            continue
        response = {key: value for key, value in response.items() if key != AUTOMATED_REVIEW_KEY}
        if response.get("outcome") == "open" and not response.get("maintainer_said"):
            continue
        kept.append(response)
    return kept


def _decided_first(found: list[dict], limit: int) -> list[dict]:
    """Closed, merged, then open, those with words first, and a merge always in.

    Closed-first with a limit of four left MessageDigestSpec with four
    closures and never AMARadio#122, merged (2026-10-31 audit): the evidence
    read as "maintainers reject this rule". When a merged response exists it
    takes the last place the closures would have filled.
    """
    rank = {"closed": 0, "merged": 1, "open": 2}
    found = sorted(found, key=lambda item: (rank.get(item["outcome"], 3),
                                            not item["maintainer_said"]))
    chosen = found[:limit]
    merged = next((item for item in found if item["outcome"] == "merged"), None)
    if merged is not None and merged not in chosen and chosen:
        chosen = sorted(chosen[:-1] + [merged],
                        key=lambda item: (rank.get(item["outcome"], 3), not item["maintainer_said"]))
    return chosen


_PULL_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/\d+", re.IGNORECASE)


def _load_object(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def records_publication(path: Path) -> bool:
    """Whether ``path`` is the record of a pull request or issue GitHub opened.

    A dry run never writes over such a record. Since the batch6 approval gate
    (2026-10-30) every finding not named on the command line is a dry run, so
    rerunning the publish or issues step with --reprocess-existing replaced
    the `*_created.json` of each published pull request and issue with a
    dry-run marker, and its URL and number -- what refresh-text, cleanup and
    the duplicate check read -- were lost.
    """
    record = _load_object(Path(path))
    return record.get("dry_run") is not True and bool(
        record.get("html_url") or record.get("number"))


def _published_finding(record_path: Path, record: dict) -> dict:
    """Identity of the finding a `*_pr_created.json` record published.

    The GitHub response says where the pull request went; what it was for is in
    the `*_pr.json` written beside it. A record without that companion still
    names its branch, and the branch carries the finding id.
    """
    companion = _load_object(
        record_path.with_name(record_path.name.replace("_pr_created", "_pr"))
    )
    if companion:
        return companion
    return {
        "finding_id": "",
        "branch": str((record.get("head") or {}).get("ref", "")),
    }


def _same_published_finding(published: dict, metadata: dict,
                            pull_request_repo: str, repo_key: str) -> bool:
    """Same finding by its id, else by repository, specification, class, method.

    The id is checked first and without the repository: a repository renamed
    since the pull request was opened still has the same finding.
    """
    finding_id = str(metadata.get("finding_id", "")).strip()
    if finding_id and (
        finding_id == str(published.get("finding_id", "")).strip()
        or f"-{finding_id}-" in f"-{published.get('branch', '')}-"
    ):
        return True
    return pull_request_repo == repo_key and _same_finding(published, metadata)


def publication_search_root(pull_requests_dir: Path) -> Path:
    """Where to look for earlier publications of a run's findings.

    The outputs/ directory the run writes under, so a rerun kept in another
    folder (Fread's rerun_20261015/run_v3 beside session_20261015/run, FreeOTP's
    subset_revisao_lacunas_v7 beside subset_revisao_lacunas) still sees them;
    the run's grandparent for a run kept outside any outputs/.
    """
    directory = Path(pull_requests_dir).resolve()
    return next((parent for parent in directory.parents if parent.name == "outputs"),
                directory.parent.parent)


def recorded_pull_request_state(url: str,
                                outcomes_dir: Optional[Path] = None) -> Optional[str]:
    """The state `cognalys pr-outcomes` last recorded for a pull request, or None.

    A creation record always says "open": without this, a pull request merged
    since (ANOTHERpass #103, merged 2026-10-25) still filled the preview's
    existing_open_pull_request.
    """
    match = _PULL_URL.search(str(url or ""))
    if not match:
        return None
    path = _repository_file({"repo_owner": match.group(1), "repo_name": match.group(2)},
                            Path(outcomes_dir or OUTCOMES_DIR))
    for record in _load(path):
        if record.get("pull_request") == url and record.get("state"):
            return str(record.get("state"))
    return None


_RECORD_DIRECTORIES: dict[str, list[Path]] = {}


def _publication_records(search_root: Path, run_root: Optional[Path] = None) -> list[Path]:
    """Every `*_pr_created.json` under ``search_root`` at the _RECORD_GLOBS depths.

    Globbing three levels of outputs/ through /mnt/c took 20-80 s (2026-10-31),
    once per draft. With ``run_root`` (the run's own app folder) the
    pull_requests folders of the rest of ``search_root`` are listed once per
    process -- they hold earlier publications -- and ``run_root`` is read
    fresh, so a record written by this process is still seen. The files are
    always listed fresh.
    """
    def directories(root: Path) -> set[Path]:
        return {path for pattern in _RECORD_GLOBS
                for path in Path(root).glob(pattern.rsplit("/", 1)[0]) if path.is_dir()}

    if run_root is None or Path(run_root).resolve() == Path(search_root).resolve():
        folders = directories(Path(search_root))
    else:
        key = str(Path(search_root).resolve())
        if key not in _RECORD_DIRECTORIES:
            _RECORD_DIRECTORIES[key] = sorted(directories(Path(search_root)))
        folders = set(_RECORD_DIRECTORIES[key]) | directories(Path(run_root))
    return sorted({path for folder in folders for path in folder.glob("*_pr_created.json")})


def open_pull_request_for(
    metadata: dict, search_root: Path, own_record: Optional[Path] = None,
    live_state: Optional[Any] = None, run_root: Optional[Path] = None,
) -> Optional[dict]:
    """A pull request published for this finding that may still be open.

    Every run rebuilds its solutions from scratch, so a finding an earlier run
    already sent to the maintainers is analysed, patched and validated again
    under a new solution revision and a new branch. Publishing that would open a
    second pull request for the same change (comfychair #43 was published by
    one run and offered again by the next). The records are searched under
    `search_root` (publication_search_root) at every depth _RECORD_GLOBS
    reaches: the 2026-10-31 audit found 19 real records the old
    `run_*/pull_requests` glob never saw -- every queue app's first run is
    `run/`, reruns are `runN/`, `rerun*/` and `subset_*/` -- so a rerun of
    KeyPass, ReadYou or BitBanana could have offered #1252, #1334 or #184
    again.

    `live_state(url)` returns "open", "closed" or "merged" for a pull request,
    or None when it cannot be told. A record stays blocking unless the state is
    known to be closed or merged: a network failure must not let a duplicate
    through.
    """
    repo_key = "/".join(
        str(metadata.get(key, "")).lower() for key in ("repo_owner", "repo_name")
    )
    for record_path in _publication_records(search_root, run_root):
        if own_record is not None and record_path.resolve() == Path(own_record).resolve():
            continue
        record = _load_object(record_path)
        url = str(record.get("html_url", ""))
        match = _PULL_URL.search(url)
        if not match or record.get("dry_run") is True:
            continue
        if record.get("state") != "open" or record.get("merged_at"):
            continue
        published = _published_finding(record_path, record)
        pull_request_repo = f"{match.group(1)}/{match.group(2)}".lower()
        if not _same_published_finding(
            published, metadata, pull_request_repo, repo_key,
        ):
            continue
        state = live_state(url) if live_state else None
        if state in {"closed", "merged"}:
            continue
        return {
            "pull_request": url,
            "branch": str((record.get("head") or {}).get("ref", "")),
            "solution_revision": str(published.get("solution_revision", "")),
            "record": str(record_path),
            "state": state or "open (last recorded)",
        }
    return None
