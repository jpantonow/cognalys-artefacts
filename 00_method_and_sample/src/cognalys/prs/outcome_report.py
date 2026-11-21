"""What happened to every pull request the pipeline published: `cognalys pr-outcomes`.

pr_outcomes records a closed pull request when the fix_prs step happens to
revise it, so nothing gave the whole picture: which proposals were merged,
which were closed and why. That is the measure that matters most for this
work -- WiFiAnalyzer's "MD5 is used here as a checksum, not as a security
control" and HomeApp's "this is intentional to allow self-signed
certificates" were known only from reading GitHub.

This finds every real publication record (a *_pr_created.json that is not a
dry run), asks GitHub for the pull request's state and the comments of
anyone but its author, records them with pr_outcomes.record_pr_outcome (so a
rejected proposal is not made again), and summarises them.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from cognalys.prs.impact_class import published_impact_class
from cognalys.prs.pr_outcomes import OUTCOMES_DIR, _RECORD_GLOBS, record_pr_outcome

_PULL_URL = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)")


@dataclass
class Outcome:
    url: str
    repository: str
    number: int
    title: str
    # merged, incorporated (closed, the change is on the default branch and
    # a commit or the maintainer refers to the pull request),
    # equivalent_upstream (closed, the change is on the default branch and
    # nothing refers to the pull request), closed (without merge), open,
    # unknown
    state: str = "unknown"
    when: str = ""
    feedback: list = field(default_factory=list)
    finding: dict = field(default_factory=dict)
    awaiting_reply: bool = False  # open, and the last word is the maintainer's
    # Why a closed pull request counts as incorporated (incorporated_evidence).
    incorporated_evidence: dict = field(default_factory=dict)


def published(outputs: Path) -> list[tuple[Path, dict, str]]:
    """(record, PR metadata, pull request URL) of every real publication, once per URL."""
    seen, found = set(), []
    for pattern in _RECORD_GLOBS:
        for record_path in sorted(Path(outputs).glob(pattern)):
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.get("dry_run"):
                continue
            url = next((match.group(0) for match in _PULL_URL.finditer(json.dumps(record))), "")
            if not url or url in seen:
                continue
            seen.add(url)
            metadata_path = record_path.with_name(record_path.name.replace("_pr_created.json", "_pr.json"))
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                metadata = {}
            found.append((record_path, metadata, url))
    return found


def _short_finding_id(unique_error_id: str) -> str:
    """The id a pull request records for a finding (WorktreeManager.finding_id)."""
    return hashlib.sha256(str(unique_error_id).encode("utf-8")).hexdigest()[:12]


def earlier_pull_request_for(finding: dict, publications: list[tuple[Path, dict, str]],
                             outcomes_dir: Path = OUTCOMES_DIR) -> dict:
    """One of our pull requests already published for ``finding``, open or merged; {} if none.

    2026-10-31 reruns regenerated Grocy's load/save while #1013 was open,
    Fread's buildSSLFactory while #168 was, and Salty's encode beside #6:
    each paid analysis, plan, drafts and reviews for a pull request
    pr_creator then refused as a duplicate. ``publications`` is published():
    every real publication record under outputs/, at any depth _RECORD_GLOBS
    reaches, not only run_*. The same finding is its id (or a joint
    finding's), the id in the branch name, or the same repository, file,
    method and specification. The state is the one `cognalys pr-outcomes`
    recorded, else the creation record's; a closed pull request is not
    returned (the maintainer's answer is read elsewhere). An open one is
    preferred to a merged one. Returns {"pull_request", "state",
    "created_at"}.
    """
    from cognalys.prs.pr_outcomes import _load, _repository_file

    uids = [str(finding.get("unique_error_id", ""))] + [
        str(item.get("unique_error_id", "")) for item in finding.get("joint_findings") or []
        if isinstance(item, dict)]
    ids = {_short_finding_id(uid) for uid in uids if uid}
    source = str(finding.get("source_file_path", "")).strip()
    identities = {(source, str(finding.get("method_name", "")), str(finding.get("spec", "")))} | {
        (source, str(item.get("method", "")), str(item.get("specification", "")))
        for item in finding.get("joint_findings") or [] if isinstance(item, dict)}
    repository = re.search(r"github\.com[/:]([^/]+)/([^/#]+?)(?:\.git)?/?$",
                           str(finding.get("repository_url", "")).strip(), re.IGNORECASE)
    repository_key = f"{repository.group(1)}/{repository.group(2)}".lower() if repository else ""
    found: list[dict] = []
    for record_path, metadata, url in publications:
        owner, repo, _number = _PULL_URL.match(url).groups()
        try:
            record = json.loads(Path(record_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        branch = str((record.get("head") or {}).get("ref", "") or metadata.get("branch", ""))
        same = (str(metadata.get("finding_id", "")).strip() in ids
                or any(f"-{identifier}-" in f"-{branch}-" for identifier in ids)
                or (source and f"{owner}/{repo}".lower() == repository_key
                    and (str(metadata.get("source_file", "")).strip(),
                         str(metadata.get("method_name", "")),
                         str(metadata.get("spec", ""))) in identities))
        if not same:
            continue
        recorded = next((item for item in _load(_repository_file(
            {"repo_owner": owner, "repo_name": repo}, outcomes_dir))
            if item.get("pull_request") == url), {})
        state = str(recorded.get("state", "")) or (
            "merged" if record.get("merged_at") else str(record.get("state", "")))
        if state in {"open", "merged"}:
            found.append({"pull_request": url, "state": state,
                          "created_at": str(record.get("created_at", ""))})
    return next((item for item in found if item["state"] == "open"),
                found[0] if found else {})


# Pages a list read follows at most: 50 pages of 100 comments.
MAX_PAGES = 50


def follow_pages(fetch: Callable[[str], Any], max_pages: int = MAX_PAGES) -> Callable[[str], Any]:
    """get(url) over ``fetch(url) -> response``, reading a list to its last page.

    GitHub returns 30 comments or reviews a page and names the next page in
    the Link header (requests exposes it as ``response.links``); only the
    first page was read, so a pull request's 31st comment, the maintainer's
    answer as often as not, was never seen (PLANO_CORRECOES_DEFESA A12).
    A response that is not a list is returned as it is.
    """
    def get(url: str):
        response = fetch(url)
        data = response.json()
        if not isinstance(data, list):
            return data
        items, seen = list(data), {url}
        for _ in range(max_pages - 1):
            following = str(((getattr(response, "links", None) or {}).get("next") or {})
                            .get("url", "") or "")
            if not following or following in seen:
                break
            seen.add(following)
            response = fetch(following)
            page = response.json()
            if not isinstance(page, list):
                break
            items.extend(page)
        return items
    return get


def github_state(url: str, get: Callable[[str], dict | list]) -> tuple[str, str, str, list, bool]:
    """(state, date, title, others' comments, whether they await our reply) of a pull request."""
    owner, repo, number = _PULL_URL.match(url).groups()
    pull = get(f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}")
    author = str((pull.get("user") or {}).get("login", ""))
    if pull.get("merged_at"):
        state, when = "merged", pull["merged_at"]
    elif pull.get("state") == "closed":
        state, when = "closed", str(pull.get("closed_at", ""))
    else:
        state, when = str(pull.get("state", "unknown")), str(pull.get("created_at", ""))
    feedback, conversation = [], []
    for kind, path in (("comment", f"issues/{number}/comments"), ("review", f"pulls/{number}/comments"),
                       ("review", f"pulls/{number}/reviews")):
        for item in get(f"https://api.github.com/repos/{owner}/{repo}/{path}") or []:
            login = str((item.get("user") or {}).get("login", ""))
            if not login or login.endswith("[bot]"):
                continue
            moment = str(item.get("created_at") or item.get("submitted_at") or "")
            body = str(item.get("body", "") or "")
            if login != author and body.strip():
                feedback.append({"author": login, "kind": kind, "body": body})
            if body.strip():
                conversation.append((moment, login))
    # Auto-Shine#12's maintainer asked for a toast and network-survey#149's
    # asked how the issue was found; nothing showed that an answer was due.
    last = max(conversation, default=("", ""))[1]
    awaiting = state == "open" and bool(last) and last != author
    return state, str(when)[:10], str(pull.get("title", "")), feedback, awaiting


def incorporated(metadata: dict, owner: str, repo: str, get: Callable) -> bool:
    """Whether a closed pull request's change is on the default branch anyway.

    BombusMod#316 was closed, not merged: "merged (with a reworded commit
    message)". Counting it as rejected understated what maintainers accepted.
    The diff applying in reverse to the default branch is the evidence.
    """
    from cognalys.prs.publication_checks import GitHubReader, upstream_state

    diff = str(metadata.get("diff", "") or "")
    if not diff:
        return False

    def read(url: str, params: dict | None = None):
        query = f"?ref={params['ref']}" if params and params.get("ref") else ""
        try:
            return get(url + query)
        except Exception:  # noqa: BLE001 -- a missing file reads as None
            return None

    reader = GitHubReader(owner, repo, str(metadata.get("base_branch", "") or ""), read)
    return upstream_state(diff, reader.read)["state"] == "already_applied"


# A maintainer's words saying the change went in: "merged (with a reworded
# commit message)" (BombusMod#316), a cherry-pick, a commit named by its hash
# or link.
_TAKEN_IN = re.compile(
    r"(?i)\b(?:merged|merging|cherry-?pick(?:ed)?|applied|committed|pushed|landed)\b"
    r"|/commit/[0-9a-f]{7,40}|\b(?=[a-f]*\d)[0-9a-f]{7,40}\b")


def incorporated_evidence(metadata: dict, owner: str, repo: str, number: str,
                          get: Callable, feedback: list) -> dict:
    """What says a closed pull request's change is on the default branch, and whose it is.

    ``diff_reverse_applies``: the diff applies in reverse to the default
    branch now (incorporated). ``references``: a commit that refers to the
    pull request (the issue events GitHub records when a commit message names
    it, or closes it), or a maintainer's comment saying it went in. Without a
    reference the change may be the maintainer's own: giggity#543 was closed
    with "Oh, just made this change myself independently", and counting it as
    an acceptance overstated what maintainers took (A11).
    """
    applies = incorporated(metadata, owner, repo, get)
    references = []
    if applies:
        try:
            events = get(f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/events") or []
        except Exception:  # noqa: BLE001 -- no events read: no reference found
            events = []
        for event in events if isinstance(events, list) else []:
            if (isinstance(event, dict) and event.get("commit_id")
                    and event.get("event") in {"referenced", "closed", "merged"}):
                references.append(f"{event.get('event')} by commit {str(event['commit_id'])[:12]}")
        for item in feedback or []:
            if _TAKEN_IN.search(str(item.get("body", ""))):
                references.append(f"{item.get('kind', 'comment')} by {item.get('author', '')}")
    return {"diff_reverse_applies": applies, "references": references}


def refresh(outputs: Path, get: Callable[[str], dict | list],
            outcomes_dir: Path = OUTCOMES_DIR) -> list[Outcome]:
    outcomes = []
    for record_path, metadata, url in published(outputs):
        owner, repo, number = _PULL_URL.match(url).groups()
        outcome = Outcome(url=url, repository=f"{owner}/{repo}", number=int(number),
                          title=str(metadata.get("title", "")),
                          finding={key: metadata.get(key, "") for key in
                                   ("apk_name", "spec", "csv_class", "method_name", "finding_id")})
        try:
            (outcome.state, outcome.when, title, outcome.feedback,
             outcome.awaiting_reply) = github_state(url, get)
            outcome.title = title or outcome.title
        except Exception as exc:  # noqa: BLE001 -- one unreachable PR is reported, not fatal
            outcome.state = f"unknown ({str(exc)[:60]})"
        if outcome.state == "closed":
            evidence = incorporated_evidence(metadata, owner, repo, number, get,
                                             outcome.feedback)
            if evidence["diff_reverse_applies"]:
                outcome.incorporated_evidence = evidence
                outcome.state = ("incorporated" if evidence["references"]
                                 else "equivalent_upstream")
        if outcome.state in {"merged", "incorporated", "equivalent_upstream",
                             "closed", "open"} and metadata:
            # Either way the change is on the default branch: recorded as
            # merged, as before the two were told apart, so the proposal is
            # not treated as one the maintainer turned down; the evidence is
            # recorded with it, and the report counts the two apart.
            status = ("merged" if outcome.state in {"incorporated", "equivalent_upstream"}
                      else outcome.state)
            # A PR published before its record carried the impact class gets
            # the one its run's adjudication records (report only).
            record_pr_outcome({**metadata, "impact_class": published_impact_class(record_path),
                               "repo_owner": owner, "repo_name": repo},
                              {"html_url": url},
                              {"status": status,
                               **({"incorporated_evidence": outcome.incorporated_evidence}
                                  if outcome.incorporated_evidence else {})},
                              outcome.feedback, outcomes_dir)
        outcomes.append(outcome)
    return outcomes


def summary_lines(outcomes: Iterable[Outcome]) -> list[str]:
    outcomes = sorted(outcomes, key=lambda item: (item.state, item.repository))
    lines = []
    for item in outcomes:
        reason = next((entry["body"] for entry in reversed(item.feedback)), "")
        reason = " ".join(reason.split())[:110]
        waiting = "  [awaiting your reply]" if item.awaiting_reply else ""
        lines.append(f"{item.state:12} {item.when:10} {item.repository}#{item.number}  "
                     f"{item.title[:60]}{waiting}"
                     + (f"\n{'':24}\"{reason}\"" if reason else ""))
    counts: dict = {}
    for item in outcomes:
        key = item.state.split(" ")[0]
        counts[key] = counts.get(key, 0) + 1
    lines.append("")
    lines.append(f"{sum(counts.values())} pull request(s): "
                 + ", ".join(f"{name} {count}" for name, count in sorted(counts.items())))
    return lines
