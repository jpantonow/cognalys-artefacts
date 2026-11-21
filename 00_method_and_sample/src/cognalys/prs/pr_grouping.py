"""Which pull requests of a run are the same change, or part of one another.

Each finding becomes its own pull request, and findings are not independent:

- AI Hub (2026-10-12): updateWebViewSettings and onCreateWindow were two
  findings of one setting. The onCreateWindow correction changed both
  WebViews; the other changed only one of them, so it was entirely inside
  the first. Publishing both would have shown the maintainer the same line
  twice.
- BitBanana: PBEKeySpecSpec and SecretKeyFactorySpec were both reported in
  encodePbkdf2, and the two corrections rewrote the same method in
  different ways; whichever was merged second would conflict.

relate() reads the diffs of a run's pull requests and says which one is
superseded by another (its changes are a subset) and which overlap (they
touch the same method or the same lines). Of two overlapping ones, the one
whose own method's change the other already makes is resolved with it (App
Manager, 2026-11-01: generateRSAKeyPair's draft made generateECCKeyPair's
"ECDH" -> "EC" too, and both counted as "PR pronto"). The PR step records it
in each PR's metadata, and pr_creator refuses a superseded or resolved one
and a second overlapping one, so the maintainer gets one pull request per
change.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping

_FILE = re.compile(r"^\+\+\+ b/(?P<path>.+)$")


def diff_changes(diff: str) -> set[tuple]:
    """(file, removed lines, added lines) of each hunk, whitespace-normalised."""
    changes = set()
    path, removed, added = "", [], []

    def close():
        if path and (removed or added):
            changes.add((path, tuple(removed), tuple(added)))

    for line in str(diff or "").splitlines():
        match = _FILE.match(line)
        if match or line.startswith("diff --git"):
            close()
            removed, added = [], []
            if match:
                path = match.group("path")
            continue
        if line.startswith("@@"):
            close()
            removed, added = [], []
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(" ".join(line[1:].split()))
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(" ".join(line[1:].split()))
    close()
    return changes


def edits_missing_from_diff(edits: Iterable[Mapping], diff: str) -> list[str]:
    """The reasons of edits that change nothing in the final diff.

    The summary describes every planned edit, so one whose lines never
    reached the diff is a change the pull request text promises and does
    not make (Grocy, 2026-10-23: "I also removed TLSv1.1", with TLSv1.1
    already gone on master).
    """
    added = {line for _, _, lines in diff_changes(diff) for line in lines if line}
    removed = {line for _, lines, _ in diff_changes(diff) for line in lines if line}
    missing = []
    for edit in edits:
        if not isinstance(edit, Mapping) or edit.get("edit_kind") == "create":
            continue
        original = {" ".join(line.split()) for line in str(edit.get("original_code", "")).splitlines()}
        corrected = {" ".join(line.split()) for line in str(edit.get("corrected_code", "")).splitlines()}
        plus, minus = corrected - original - {""}, original - corrected - {""}
        if (plus or minus) and not (plus & added) and not (minus & removed):
            missing.append(str(edit.get("reason") or edit.get("symbol") or "an edit"))
    return missing


def _repository(pr: Mapping) -> str:
    owner, name = str(pr.get("repo_owner", "")), str(pr.get("repo_name", ""))
    return f"{owner}/{name}".lower() if owner and name else str(pr.get("apk_name", ""))


def relate(prs: Iterable[Mapping]) -> dict[str, dict]:
    """finding_id -> {"superseded_by": id} and/or {"overlaps_with": [ids]}."""
    items = [(str(pr.get("finding_id", "")), pr, diff_changes(str(pr.get("diff", ""))))
             for pr in prs if pr.get("finding_id")]
    relations: dict[str, dict] = {}
    for finding, pr, changes in items:
        if not changes:
            continue
        for other, other_pr, other_changes in items:
            if (other == finding or _repository(other_pr) != _repository(pr)
                    or not _publishable(other_pr)):
                continue
            # Superseded: everything this one changes, the other changes too.
            # Of two identical changes the smaller finding id is kept.
            if changes <= other_changes and (changes != other_changes or other < finding):
                relations.setdefault(finding, {})["superseded_by"] = other
                break
    # A chain (A in B, B in C) ends at the last one, which makes every change
    # of the others; a finding never points at a draft that is not published.
    for finding, relation in list(relations.items()):
        target, seen = relation["superseded_by"], {finding}
        while "superseded_by" in relations.get(target, {}) and target not in seen:
            seen.add(target)
            target = relations[target]["superseded_by"]
        if target in seen:
            del relations[finding]
        else:
            relation["superseded_by"] = target
    superseded = {finding for finding, relation in relations.items() if "superseded_by" in relation}
    # Overlaps only among the pull requests that remain: one that is
    # superseded will not be published, so nothing collides with it.
    for finding, pr, changes in items:
        if not changes or finding in superseded:
            continue
        overlapping = []
        for other, other_pr, other_changes in items:
            if (other == finding or other in superseded
                    or _repository(other_pr) != _repository(pr) or not other_changes):
                continue
            same_method = (str(other_pr.get("method_name", "")) == str(pr.get("method_name", ""))
                           and str(other_pr.get("source_file", "")) == str(pr.get("source_file", "")))
            removed = {line for _, lines, _ in changes for line in lines if line}
            other_removed = {line for _, lines, _ in other_changes for line in lines if line}
            if same_method or removed & other_removed:
                overlapping.append(other)
        if overlapping:
            relations.setdefault(finding, {})["overlaps_with"] = sorted(overlapping)
    by_id = {finding: (pr, changes) for finding, pr, changes in items}
    for finding in sorted(relations):
        relation = relations[finding]
        for other in list(relation.get("overlaps_with") or []):
            if "resolved_with" in relations.get(other, {}) or not _publishable(by_id[other][0]):
                continue
            pr, changes = by_id[finding]
            other_pr, other_changes = by_id[other]
            mine = own_changes(pr, changes)
            theirs = own_changes(other_pr, other_changes)
            covered = bool(mine) and _covers(other_changes, mine)
            covers_back = bool(theirs) and _covers(changes, theirs)
            # The one whose own change the other's diff already makes is
            # resolved with it; of two that make each other's, the smaller
            # finding id is kept (as for superseded).
            if covered and (not covers_back or other < finding):
                relation["resolved_with"] = other
                relation["resolved_with_method"] = str(other_pr.get("method_name", ""))
                break
    # A target resolved later in the loop: follow it to the draft that stays,
    # and keep the link only when that one's diff still makes this change.
    for finding in sorted(relations):
        relation = relations[finding]
        if "resolved_with" not in relation:
            continue
        target, seen = relation["resolved_with"], {finding}
        while "resolved_with" in relations.get(target, {}) and target not in seen:
            seen.add(target)
            target = relations[target]["resolved_with"]
        pr, changes = by_id[finding]
        if target in seen or not _covers(by_id[target][1], own_changes(pr, changes)):
            del relation["resolved_with"]
            relation.pop("resolved_with_method", None)
        elif target != relation["resolved_with"]:
            relation["resolved_with"] = target
            relation["resolved_with_method"] = str(by_id[target][0].get("method_name", ""))
    for finding, relation in relations.items():
        if "resolved_with" in relation:
            # Kept apart from the overlap of the one that stays: nothing of
            # this one will be published to collide with it.
            kept = relations.get(relation["resolved_with"], {})
            if finding in kept.get("overlaps_with", []):
                kept["overlaps_with"].remove(finding)
                if not kept["overlaps_with"]:
                    del kept["overlaps_with"]
    return relations


def _publishable(pr: Mapping) -> bool:
    """Whether a draft can be published: a finding never yields to one that cannot.

    The behaviour review's hold, a review-only disposition and a validation
    block all leave ``publishable`` false (pr_generator), and so does a
    superseded or resolved one written earlier.
    """
    return (pr.get("publishable") is not False
            and str(pr.get("preview_status", "")).upper() not in {
                "SUPERSEDED", "RESOLVED_WITH"}
            and not pr.get("superseded_by") and not pr.get("resolved_with"))


def own_changes(pr: Mapping, changes: set) -> set:
    """The hunks of ``changes`` that edit the finding's own method.

    A hunk is the finding's own when a line it removes is a line of the
    method body the finding names (``original_method_body``); App Manager's
    generateRSAKeyPair draft (2026-11-01) also rewrote generateECCKeyPair,
    whose hunk is the other finding's.
    """
    body = {" ".join(line.split()) for line in str(pr.get("original_method_body", "")).splitlines()}
    body.discard("")
    return {change for change in changes if body & {line for line in change[1] if line}}


def _covers(changes: set, wanted: set) -> bool:
    """Whether every line ``wanted`` removes and adds, ``changes`` removes and adds, per file."""
    for path, removed, added in wanted:
        other_removed = {line for file, lines, _ in changes if file == path for line in lines}
        other_added = {line for file, _, lines in changes if file == path for line in lines}
        if not (set(removed) - {""} <= other_removed and set(added) - {""} <= other_added):
            return False
    return True
