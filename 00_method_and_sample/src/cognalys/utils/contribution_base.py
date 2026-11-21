#!/usr/bin/env python3
"""Where a fix would be contributed, and whether its target exists there.

Two stages hold different revisions of the same repository and never
compared them. Extraction reads the revision the analysed artifact was built
from -- pinned from the input when it names one -- while the applicator
builds its worktree from the branch a pull request would target. When those
two disagree about whether the reported file exists, nothing noticed until
the patch was about to be written.

run_20260909_125353 is the shape of it. The mifos finding was analysed at
476b7506 (tag v1.0.0), where
app/src/main/java/org/mifos/mobilebanking/api/SelfServiceOkHttpClient.java
exists; the contribution base, origin/dev at 71ff561, is a Kotlin
Multiplatform rewrite that does not contain that file or its package. The
run spent 1h50m of model time producing a 90%-confidence fix and then failed
with WORKTREE_SOURCE_FILE_NOT_FOUND, and the whole execution was reported as
FAILED -- for a finding that simply has nowhere to land.

The cause is not specific to a pinned revision, and neither is this. A file
renamed, moved, or deleted upstream since the analysed artifact was built
produces exactly the same situation, so the question asked here is the
general one: does the path this finding reports still exist on the branch a
contribution would target?
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from cognalys.utils.network_git import run_git_command


def _git(repo: Path, *arguments: str) -> tuple[int, str]:
    # A fetch is bounded like every network git command (utils.network_git).
    result = run_git_command(
        ["git", *arguments], cwd=str(repo),
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    return result.returncode, result.stdout.strip()


def base_branch_name(repo: Path, branch_policy: dict | None) -> str:
    """The branch a pull request for this repository would target.

    Same precedence the applicator has always used: the base the repository's
    own contribution guidance recommends, then the default branch the remote
    advertises, then whatever is currently checked out.

    A detached HEAD -- which is what a pinned revision leaves behind -- makes
    the last fallback return "", so callers get "" rather than a branch name
    invented from a detached checkout.
    """
    policy = branch_policy or {}
    base = str(policy.get("recommended_base_branch", "") or "").strip()
    if base:
        return base
    for remote in ("upstream", "origin"):
        code, ref = _git(
            repo, "symbolic-ref", "--quiet", "--short",
            f"refs/remotes/{remote}/HEAD",
        )
        if code == 0 and ref:
            return ref.split("/", 1)[-1]
    code, current = _git(repo, "branch", "--show-current")
    return current if code == 0 else ""


def resolve_base_ref(repo: Path, base: str, fetch: bool = True) -> str:
    """A fully-qualified ref that can be read for ``base``, or "" if none can be reached.

    Prefers a local branch, then a remote-tracking ref, and only then fetches.
    ``fetch=False`` keeps the check read-only for callers that must not touch
    the network -- the extraction stage runs once per finding and a fetch per
    finding would be paid for nothing.

    Always returns a fully-qualified ref (``refs/heads/<base>`` or
    ``refs/remotes/<remote>/<base>``), never the bare short name. TagMo's own
    repository has both a branch and something else matching the short name
    "master"; ``git worktree add -b <branch> <path> master`` refused with
    "fatal: ambiguous object name: 'master'" and aborted the whole run over
    one finding. A fully-qualified ref skips Git's short-name disambiguation
    entirely, so the same collision -- a branch and a tag, or a branch and
    another remote's ref, sharing a name -- can never recur here, for any
    repository this pipeline touches.
    """
    if not base:
        return ""
    if _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{base}")[0] == 0:
        return f"refs/heads/{base}"
    code, remotes = _git(repo, "remote")
    known = set(remotes.splitlines()) if code == 0 else set()
    for remote in ("upstream", "origin"):
        if remote not in known:
            continue
        remote_ref = f"refs/remotes/{remote}/{base}"
        if _git(repo, "show-ref", "--verify", "--quiet", remote_ref)[0] == 0:
            return remote_ref
        if fetch and _git(
            repo, "fetch", remote, f"refs/heads/{base}:refs/remotes/{remote}/{base}",
        )[0] == 0:
            return remote_ref
    return ""


def path_exists_at(repo: Path, ref: str, relative_path: str) -> bool:
    """Whether ``relative_path`` is present in the tree at ``ref``."""
    if not ref or not relative_path:
        return False
    return _git(repo, "cat-file", "-e", f"{ref}:{relative_path}")[0] == 0


def _normalized(text: str) -> str:
    """Collapse whitespace so indentation differences do not decide a match."""
    return " ".join(text.split())


def code_exists_at(repo: Path, ref: str, relative_path: str,
                   code: str) -> bool | None:
    """Whether ``code`` still appears in ``relative_path`` at ``ref``.

    None when the question cannot be asked -- no ref, no path, no code, or a
    file that cannot be read there. An unknown is not an absence.

    Whitespace is normalised because the comparison is about whether the
    reviewed code is still the code on that branch, not about how it is
    indented there.
    """
    if not ref or not relative_path or not code.strip():
        return None
    status, content = _git(repo, "show", f"{ref}:{relative_path}")
    if status != 0:
        return None
    return _normalized(code) in _normalized(content)


def renamed_paths(repo: Path, from_ref: str, to_ref: str) -> dict[str, str]:
    """Files git sees as renamed between two revisions, old path -> new path.

    Empty when either revision cannot be read -- a shallow clone without the
    analysed commit, say -- because a rename that cannot be shown is not one.
    The rename limit is lifted: the projects that move files are the ones
    that restructure into modules, and those diffs are the large ones.
    """
    if not from_ref or not to_ref:
        return {}
    code, output = _git(
        repo, "-c", "diff.renameLimit=0", "diff", "--find-renames",
        "--name-status", "--diff-filter=R", "-z", from_ref, to_ref,
    )
    if code != 0 or not output:
        return {}
    fields = output.split("\0")
    renames = {}
    for index in range(0, len(fields) - 2, 3):
        if fields[index].startswith("R"):
            renames[fields[index + 1]] = fields[index + 2]
    return renames


def relocated_path(repo: Path, from_ref: str, to_ref: str,
                   relative_path: str, code: str = "",
                   renames: dict[str, str] | None = None) -> str:
    """Where ``relative_path`` went between ``from_ref`` and ``to_ref``, or "".

    BetterUntis moved data/cache/DiskCache.kt into a core/data module and
    OpenBible moved logic/Getters.kt into a Kotlin Multiplatform shared
    module; both kept the reviewed method byte for byte, and both findings
    were recorded as having nowhere to land. A rename alone is not enough to
    send a patch there -- git calls a file renamed at 50% similarity -- so
    when the reviewed code is known it must still be in the new file, the
    same check ``analyzed_code_present`` makes for a file that did not move.
    """
    if renames is None:
        renames = renamed_paths(repo, from_ref, to_ref)
    new_path = renames.get(relative_path, "")
    if not new_path:
        return ""
    if code.strip() and code_exists_at(repo, to_ref, new_path, code) is not True:
        return ""
    return new_path


def submodule_containing(repo: Path, ref: str, relative_path: str) -> dict:
    """The git submodule ``relative_path`` lies in at ``ref``, or {}.

    EteSync's CustomCertService.kt is at cert4android/src/..., and
    cert4android is a submodule: the superproject's tree holds a commit
    pointer there, not files, so the path reads as absent and the finding was
    recorded as having nowhere to land. The code exists; it belongs to
    another repository, which is where a fix or an issue has to go.
    """
    if not ref or not relative_path:
        return {}
    parts = Path(relative_path).parts
    for depth in range(1, len(parts)):
        prefix = "/".join(parts[:depth])
        code, entry = _git(repo, "ls-tree", ref, "--", prefix)
        if code != 0 or not entry:
            return {}
        mode = entry.split(None, 1)[0]
        if mode == "160000":
            code, modules = _git(repo, "show", f"{ref}:.gitmodules")
            sections: list[dict[str, str]] = []
            for line in (modules.splitlines() if code == 0 else []):
                line = line.strip()
                if line.startswith("["):
                    sections.append({})
                elif sections and "=" in line:
                    key, _, value = line.partition("=")
                    sections[-1][key.strip()] = value.strip()
            url = next((section.get("url", "") for section in sections
                        if section.get("path") == prefix), "")
            fields = entry.split()
            return {"path": prefix, "url": url,
                    "commit": fields[2] if len(fields) > 2 else ""}
        if mode != "040000":
            return {}
    return {}


def submodule_of(repo: Path, relative_path: str = "") -> dict:
    """The submodule ``repo`` itself is, when it is one; else {}.

    The applicator finds a file's repository by walking up to the nearest
    .git, and a checked-out submodule has its own: for EteSync's
    cert4android/src/.../CustomCertService.kt that walk stops at
    cert4android/, so asking that repository for the superproject's path
    found nothing and the finding was reported as a missing file
    (run_melhorias_20261011_203822). ``relative_path``, when given, is the
    superproject-relative path, and must lie inside the submodule.
    """
    code, top = _git(repo, "rev-parse", "--show-superproject-working-tree")
    if code != 0 or not top:
        return {}
    try:
        path = Path(repo).resolve().relative_to(Path(top).resolve()).as_posix()
    except ValueError:
        return {}
    if relative_path and not relative_path.startswith(path + "/"):
        return {}
    url = _git(repo, "remote", "get-url", "origin")[1]
    commit = _git(repo, "rev-parse", "HEAD")[1]
    return {"path": path, "url": url, "commit": commit}


# A statement worth looking for: it calls something. A brace, a blank line or
# a bare argument line is found in any file and proves nothing either way.
_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
# Words a call pattern matches that call nothing.
_NOT_CALLS = frozenset({"if", "for", "while", "switch", "catch", "return",
                        "synchronized", "when", "throw", "assert", "super", "this"})
_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')
_NUMBER = re.compile(r"(?<![\w$.])\d+(?![\w.])")
# A statement split across lines on the base still counts as one.
_STATEMENT_LINES = 3
# Past this many files holding every name and literal, the statement is
# taken as present.
_MAX_CANDIDATE_FILES = 200


def _called_names(text: str) -> list[str]:
    return [name for name in _CALL.findall(_STRING.sub('""', text))
            if name not in _NOT_CALLS]


def reported_statement(statement: str) -> str:
    """The detector's reported line, stripped, when it holds a call; else ""."""
    text = str(statement or "").strip()
    return text if _called_names(text) else ""


def statement_skeleton(statement: str) -> tuple[list[str], list[str]]:
    """What a statement keeps however it is rewritten: (called names, literals).

    Where's hmacSha256 (2026-10-28) went from ``mac.init(SecretKeySpec(key,
    "HmacSHA256"))`` to ``mac.init(SecretKeySpec(if (key.isEmpty())
    ByteArray(64) else key, "HmacSHA256"))`` on main: the caller's key still
    reaches SecretKeySpec, and comparing the line as text called the
    violation gone. AmaRadio's ``getInstance("MD5")`` became
    ``getInstance("SHA-256")``: its literal is what went.
    """
    text = reported_statement(statement)
    literals = _STRING.findall(text) + _NUMBER.findall(_STRING.sub('""', text))
    return list(dict.fromkeys(_called_names(text))), list(dict.fromkeys(literals))


def _calls_all(text: str, names: list[str]) -> bool:
    return all(re.search(r"(?<![\w$])%s\s*\(" % re.escape(name), text) for name in names)


def statement_in_text(content: str, statement: str) -> bool | None:
    """statement_on_branch for one file's text (read through an API, say).

    The same skeleton rule: every called name within a few consecutive
    lines, and every literal anywhere in the text. None when the statement
    holds no call to look for.
    """
    names, literals = statement_skeleton(statement)
    if not names:
        return None
    text = str(content or "")
    if not all(token in text for token in names + literals):
        return False
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return any(_calls_all(" ".join(lines[start:start + _STATEMENT_LINES]), names)
               for start in range(len(lines)))


def statement_on_branch(repo: Path, ref: str, statement: str) -> bool | None:
    """Whether the reported statement's skeleton is anywhere on ``ref``.

    Searched in every file, not only the reported one: a class split or moved
    without git seeing a rename, or a helper extracted into another file,
    still carries the violation, and an issue about it is still owed. The
    calls must sit within a few consecutive lines; a literal need only be in
    the same file, because one moved into a constant (getInstance(
    TRANSFORMATION)) is still the value passed. None when the question
    cannot be asked; False only when no file holds the skeleton.
    """
    names, literals = statement_skeleton(statement)
    if not ref or not names:
        return None
    arguments = ["grep", "-l", "-F", "--all-match"]
    for token in names + literals:
        arguments += ["-e", token]
    status, output = _git(repo, *arguments, ref, "--")
    if status == 1:
        return False
    if status != 0:
        return None
    candidates = [line.split(":", 1)[1] for line in output.splitlines() if ":" in line]
    if len(candidates) > _MAX_CANDIDATE_FILES:
        return True
    for path in candidates:
        code, content = _git(repo, "show", f"{ref}:{path}")
        if code != 0:
            return None
        lines = [" ".join(line.split()) for line in content.splitlines()]
        if any(_calls_all(" ".join(lines[start:start + _STATEMENT_LINES]), names)
               for start in range(len(lines))):
            return True
    return False


def code_gone_from_base(status: dict) -> str:
    """Why the reported statement is not on the contribution base, or "".

    ``analyzed_code_present`` is False whenever the reviewed body differs at
    all, so an unrelated edit elsewhere in the method reads as an absence.
    Only the reported statement itself settles it: AmaRadio's md5() and its
    MessageDigest.getInstance("MD5") line were gone from master, which
    hashes with SHA-256, and the run still wrote a maintainer issue about
    them (misuse3 run7, 2026-10-16). A file kept in a submodule is another
    repository's, not gone.
    """
    if status.get("reported_statement_present") is not False or status.get("submodule"):
        return ""
    if status.get("target_present") is False:
        return ("%s is not on the contribution base branch %s"
                % (status.get("source_file_path", ""), status.get("base_branch", "")))
    return ("the reported statement is no longer in %s, nor anywhere else on the "
            "contribution base branch %s"
            % (status.get("relocated_path") or status.get("source_file_path", ""),
               status.get("base_branch", "")))


def contribution_base_status(repo: Path, relative_path: str,
                             branch_policy: dict | None = None,
                             fetch: bool = False,
                             method_body: str = "",
                             statement: str = "") -> dict:
    """Whether this finding's code exists where a fix would be contributed.

    Two questions, because the file being there is not enough. Element
    Android's MainActivity.kt is present on develop, so ``target_present`` was
    true and the run proceeded -- but develop has carried the fix for
    CVE-2024-26131 since 1.6.12, and the reviewed v1.6.10 body is not there
    any more. 34 minutes of analysis and an 18-minute worktree later, the
    applicator reported ORIGINAL_CODE_NOT_FOUND. ``analyzed_code_present``
    asks the second question up front.

    Both are None, not False, whenever the question could not be asked -- an
    unresolvable base, a path that was never reported, a body that was never
    extracted. An unknown must not read as an absence, because absence is
    what stops a finding.

    ``statement`` is the line the detector reported, read at the analysed
    revision. When the body is not found on the base, or the file is not,
    ``reported_statement_present`` says whether that statement still is,
    anywhere on the base (statement_on_branch, code_gone_from_base).
    """
    base = base_branch_name(repo, branch_policy)
    ref = resolve_base_ref(repo, base, fetch=fetch)
    present: bool | None = None
    analyzed: bool | None = None
    relocated = ""
    if ref and relative_path:
        present = path_exists_at(repo, ref, relative_path)
        if not present:
            # The checkout is at the analysed revision, so HEAD is where the
            # reported path is known to be right.
            relocated = relocated_path(repo, "HEAD", ref, relative_path,
                                       method_body)
            present = bool(relocated) or present
        if present:
            analyzed = code_exists_at(repo, ref, relocated or relative_path,
                                      method_body)
    status = {
        "base_branch": base,
        "base_ref": ref,
        "source_file_path": relative_path,
        "target_present": present,
        "analyzed_code_present": analyzed,
    }
    if reported_statement(statement) and (analyzed is False or present is False):
        status["reported_statement_present"] = statement_on_branch(repo, ref, statement)
    if relocated:
        status["relocated_path"] = relocated
    if present is False:
        submodule = submodule_containing(repo, ref, relative_path)
        if submodule:
            status["submodule"] = submodule
    return status
