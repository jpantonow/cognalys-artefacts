"""Bring the description of already-published pull requests up to date.

A pull request's body is written once, when it is opened. When the text the
pipeline generates improves afterwards -- session-android#2241, rssreader#4 and
ShortMessageSecure#83 named the CrySL rule but not RVSec, the analysis that
found the problem -- regenerating the run's PR step rewrites `*_pr.json`, but
the open pull request still shows the old words. This sends the regenerated
body to each still-open pull request the run published whose finding the user
approved by name; every other one is a dry run that prints the body.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Iterable


def commits_pushed_after(pr_file: Path, generated: dict) -> list[str]:
    """The follow-up commits pr_followup pushed on top of the record's commit.

    Read from the pr_followup record next to ``*_pr.json``: the pushed rounds
    chained from the record's commit_hash (a record regenerated after them
    names a later commit, and none count). A record without a commit_hash
    counts every pushed round: nothing shows its text covers them.
    """
    follow_up_file = Path(pr_file).with_name(
        Path(pr_file).name.removesuffix("_pr.json") + "_followup.json")
    try:
        follow_up = json.loads(follow_up_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(follow_up, dict):
        return []
    rounds = [item for item in follow_up.get("previous_rounds") or [] if isinstance(item, dict)]
    rounds.append(follow_up)
    recorded = str(generated.get("commit_hash", "") or "")
    pushed, base = [], recorded
    for item in rounds:
        if item.get("status") != "PUSHED":
            continue
        if not recorded or str(item.get("head_sha", "")) == base:
            base = str(item.get("commit", "") or "")
            pushed.append(base)
    return pushed


def refresh_published_text(pull_requests_dir: Path, findings: Iterable[str] = (),
                           dry_run: bool = False,
                           state: Callable[[dict], dict] | None = None,
                           update: Callable[[str, dict], None] | None = None,
                           approved: Iterable[str] = ()) -> list[dict]:
    """Update open pull requests whose regenerated body differs from the published one.

    ``findings`` narrows which records are considered; ``approved`` names the
    findings whose new body may be sent. A pull request whose finding is not
    approved, or any one under ``dry_run``, is reported as would_update with
    the body and nothing is sent: the batch5 review (2026-10-30) found that
    `cognalys publish --refresh-text` with no finding sent the regenerated
    body of every open pull request of the run, while a published text
    changes only after the user approves that specific finding.

    Returns one record per published pull request considered: its URL and
    what happened (updated, unchanged, would_update, skipped_<state>, or
    held_follow_ups for a pull request with follow-up commits pushed after
    its record, whose text pr_creator --update-description revises).
    """
    if state is None or update is None:
        from cognalys.prs.pr_github import fetch_current_pr_state, update_pr_metadata
        state = state or fetch_current_pr_state
        update = update or update_pr_metadata
    wanted = {str(item).strip() for item in findings if str(item).strip()}
    allowed = {str(item).strip() for item in approved if str(item).strip()}
    results = []
    for created_path in sorted(Path(pull_requests_dir).glob("*_pr_created.json")):
        generated_path = created_path.with_name(
            created_path.name.removesuffix("_pr_created.json") + "_pr.json")
        if not generated_path.is_file():
            continue
        published = json.loads(created_path.read_text(encoding="utf-8"))
        generated = json.loads(generated_path.read_text(encoding="utf-8"))
        if wanted and str(generated.get("finding_id", "")) not in wanted:
            continue
        record = {"url": published.get("html_url", ""),
                  "finding_id": generated.get("finding_id", "")}
        if commits_pushed_after(generated_path, generated):
            # The regenerated body describes the record's commit only: sent
            # to NotallyX#1167 it would have left out the SHA-256 test-vector
            # commit pushed after it, or undone the description pr_creator
            # --update-description revised for it (2026-10-30 review).
            results.append({**record, "result": "held_follow_ups"})
            continue
        body = str(generated.get("body", "") or "")
        if not body.strip() or body.strip() == str(published.get("body", "") or "").strip():
            results.append({**record, "result": "unchanged"})
            continue
        live = state(published)
        if live.get("status") != "open":
            results.append({**record, "result": f"skipped_{live.get('status', 'unknown')}"})
            continue
        if dry_run or str(record["finding_id"]).strip() not in allowed:
            results.append({**record, "result": "would_update", "body": body,
                            **({} if dry_run else {"approval_needed": True})})
            continue
        update(str(published.get("url", "")), {"body": body})
        published["body"] = body
        created_path.write_text(json.dumps(published, indent=2, ensure_ascii=False),
                                encoding="utf-8")
        results.append({**record, "result": "updated"})
    return results


# How much of one follow-up commit's diff the description model reads. The
# rounds pr_followup pushes answer one review comment each (NotallyX#1167's
# test vector was 25 lines); a longer diff still shows its files and reasons.
MAX_FOLLOW_UP_DIFF_CHARS = 8000

FOLLOW_UP_DESCRIPTION_SYSTEM = (
    "You keep the description of an open pull request accurate. The description "
    "was written for the pull request's first commit; the commits in follow_ups "
    "were pushed to it afterwards. Return the description revised so that it "
    "describes the final diff: change or extend only the sentences the follow-up "
    "commits make incomplete or wrong (a sentence about the tests must also cover "
    "a test a follow-up added or changed), add a sentence where no sentence covers "
    "a follow-up's change, and keep every other sentence and paragraph word for "
    "word, in the same order. Write as the contributor, in plain paragraphs: no "
    "headings, lists or checkboxes; never mention review bots, review comments, "
    "follow-ups, commits, or that the text was revised; never describe the "
    "contribution as automated. Use only the facts in follow_ups (the diff is "
    "authoritative). In covered_by, give for each follow-up commit the sentence of "
    "your description, copied exactly, that describes its change; it must be a "
    "sentence you wrote or changed, not one already in the description."
)


def follow_up_description_schema() -> dict:
    from cognalys.llm.llm_schemas import _array, _strict_object, _string

    return _strict_object({
        "description": _string("the whole description, revised"),
        "covered_by": _array(_strict_object({
            "commit": _string("the follow-up commit, as given in follow_ups"),
            "sentence": _string("the sentence of description describing its change"),
        })),
    })


def follow_up_facts(rounds: Iterable[dict]) -> list[dict]:
    """What each pushed follow-up commit changed, as the description model reads it."""
    facts = []
    for item in rounds:
        diff = str(item.get("committed_diff", "") or "")
        facts.append({
            "commit": str(item.get("commit", ""))[:12],
            "subject": str(item.get("commit_subject", "") or ""),
            "changes": [
                {"file": str(edit.get("source_file_path") or edit.get("source_file") or ""),
                 "kind": str(edit.get("edit_kind", "") or ""),
                 "reason": str(edit.get("reason", "") or "")}
                for edit in item.get("edits") or [] if isinstance(edit, dict)],
            "diff": diff[:MAX_FOLLOW_UP_DIFF_CHARS]
                    + ("\n[diff truncated]" if len(diff) > MAX_FOLLOW_UP_DIFF_CHARS else ""),
        })
    return facts


def _normalised(text: str) -> str:
    return " ".join(str(text or "").split())


def follow_up_coverage_problems(original: str, revised: str, rounds: list,
                                covered_by: Iterable[dict]) -> list:
    """Why a revised description does not yet describe every pushed follow-up.

    NotallyX#1167 (2026-10-30): the test-vector commit was pushed and the
    description kept saying the tests only compared digests of equal and
    changed content. Each follow-up commit must be named by a sentence that
    is in the revised text and was not in the original one, so a model that
    returns the old text, or points at an old sentence, is not taken as
    having described the change.
    """
    if not _normalised(revised):
        return ["the description came back empty"]
    old = _normalised(original)
    text = _normalised(revised)
    cited: dict = {}
    for item in covered_by or []:
        if isinstance(item, dict):
            cited.setdefault(str(item.get("commit", "")).strip()[:12], []).append(
                _normalised(item.get("sentence", "")))
    problems = []
    for item in rounds:
        commit = str(item.get("commit", ""))[:12]
        subject = str(item.get("commit_subject", "") or commit)
        sentences = [sentence for sentence in cited.get(commit, []) if sentence]
        if not sentences:
            problems.append(f"no sentence describes the commit {commit} ({subject})")
        elif not any(sentence in text for sentence in sentences):
            problems.append(f"the sentence given for {commit} ({subject}) is not in the "
                            "description")
        elif not any(sentence in text and sentence not in old for sentence in sentences):
            problems.append(f"the sentence given for {commit} ({subject}) was already in "
                            "the description before that commit; describe its change")
    return problems


def description_after_follow_ups(provider, description: str, rounds: list, *,
                                 title: str = "", finding_id: str = "",
                                 attempts: int = 2) -> tuple[str, list]:
    """The description revised to cover the follow-up commits pushed after it.

    pr_creator --update-description filled NotallyX#1167's template from the
    text written for the first commit, so after the SHA-256 test-vector
    follow-up the body was byte-identical to the published one and reported
    "already up to date" (2026-10-30). The text a maintainer reads is
    revised from the follow-ups' own diffs; the request carries those diffs,
    so a cached answer for the text before a later round is never reused.
    Returns (description, problems); problems not empty means the text
    still does not describe every follow-up and must not be sent.
    """
    from cognalys.llm.generate_error_fixes import ResponseParser
    from cognalys.llm.pipeline_common import prompt_json

    rounds = list(rounds or [])
    if not rounds:
        return description, []
    payload = {"title": title, "description": description,
               "follow_ups": follow_up_facts(rounds)}
    problems: list = []
    revised = description
    for _ in range(max(1, attempts)):
        request = dict(payload)
        if problems:
            request["problems_with_previous_answer"] = problems
            request["previous_answer"] = revised
        response = provider.generate(
            prompt_json(request), FOLLOW_UP_DESCRIPTION_SYSTEM, json_mode=True,
            json_schema=follow_up_description_schema(),
            schema_name="follow_up_description", trace_stage="pr_description.follow_ups",
            trace_context={"finding_id": finding_id,
                           "prompt_version": "pr-description-follow-ups-v1",
                           "schema_version": "1.0", "policy_version": "1.0"},
        )
        answer = ResponseParser.parse_json_object(response) or {}
        revised = str(answer.get("description", "") or "").strip()
        problems = follow_up_coverage_problems(description, revised, rounds,
                                               answer.get("covered_by") or [])
        if not problems:
            return revised, []
    return revised or description, problems
