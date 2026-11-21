#!/usr/bin/env python3
"""The checkout the analysed APK was built from, when git still has it.

Findings are reported against a published APK -- `net.gsantner.markor_v2.11.0.apk`
-- while the pipeline reads the repository's current default branch. When a
class has been renamed, moved or deleted in the meantime, the reported class
is simply not there, and every one of those findings ended as
`extraction produced no source for the reported target
(extraction_status=application_class_not_in_checkout)`. That signature is
the single largest block in the failure ledger: 16 occurrences.

Reading it as one failure hid three different situations:

  * the class still exists, somewhere the class index did not look, because
    it moved outside the package root the simple-name fallback requires;
  * the class existed when the APK was built and was removed or renamed
    since, which makes the finding stale relative to the checkout -- a fact
    about the corpus, not a gap in the pipeline, and not something a pull
    request against today's code could fix;
  * the class was never in this repository at all.

This module tells them apart from the git history, without touching the
working tree: the release ref is only ever read through `git show` and
`git ls-tree`, so the clone stays on the contribution branch that the pull
request will target. Reproducing a finding at its own commit and porting a
repair to the current version are two different experiments, and this keeps
them separable instead of silently doing neither.
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional


# `name_v2.11.0.apk` (a version name) and `name_15390.apk` (a version code).
# A version name is matched against tag names. A version code is matched
# against the versionCode each tag's own build files declare -- read through
# git, never guessed from the tag's name.
_VERSION_NAME = re.compile(r"_v(\d+(?:\.\d+)+[\w.\-]*)\.apk$", re.IGNORECASE)
_VERSION_CODE = re.compile(r"_(\d+)\.apk$")

_SOURCE_SUFFIXES = (".java", ".kt")

# Where Android projects declare versionCode: the module build script, or a
# properties file it reads. Only shallow paths, so a vendored library's own
# build file is not mistaken for the application's.
_BUILD_FILE = re.compile(
    r"(?:^|/)(?:build\.gradle(?:\.kts)?|gradle\.properties|version\.properties)$"
)
_BUILD_FILE_MAX_DEPTH = 3
# `versionCode 123`, `versionCode = 123`, `versionCode=123`, `VERSION_CODE=123`.
# A computed value (`versionCode = major * 10000 + minor`) declares no
# literal and is, correctly, not matched.
_VERSION_CODE_DECLARATION = re.compile(
    r"\bversion_?code\s*[=:]?\s*(\d+)[ \t]*(?:$|[;,)])",
    re.IGNORECASE | re.MULTILINE,
)
# Bounds on the search: the most recent tags first, and a wall-clock cap so
# a repository with thousands of tags cannot stall extraction.
MAX_VERSION_CODE_TAGS = 300
VERSION_CODE_SEARCH_SECONDS = 90
GIT_TIMEOUT_SECONDS = 60
# Recorded with every resolution: a matching tag shows which sources the
# release was declared from, not that the published APK was built from them.
BINARY_EQUIVALENCE = "not_established"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    command = ["git", "-C", str(repo), *args]
    try:
        return subprocess.run(
            command, capture_output=True, text=True, errors="replace",
            timeout=GIT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", "git timed out")


def apk_version(apk_name: str) -> Dict[str, str]:
    """The version the APK name carries, and which kind it is."""
    name = str(apk_name or "")
    match = _VERSION_NAME.search(name)
    if match:
        return {"kind": "version_name", "value": match.group(1)}
    match = _VERSION_CODE.search(name)
    if match:
        return {"kind": "version_code", "value": match.group(1)}
    return {"kind": "unknown", "value": ""}


def candidate_tags(version: str) -> List[str]:
    """Tag spellings projects use for the same release, most literal first."""
    version = str(version or "").strip()
    if not version:
        return []
    return [
        version, f"v{version}", f"V{version}",
        f"release-{version}", f"release/{version}",
        f"r{version}", f"{version}-release",
    ]


def declared_version_codes(text: str) -> List[int]:
    """Every literal versionCode a build file declares."""
    return [int(match.group(1))
            for match in _VERSION_CODE_DECLARATION.finditer(text or "")]


def _tags_newest_first(repo: Path) -> Optional[List[str]]:
    listed = _git(repo, "tag", "--list", "--sort=-creatordate")
    if listed.returncode != 0:
        return None
    return [line.strip() for line in listed.stdout.splitlines() if line.strip()]


def _tag_declares_version_code(repo: Path, tag: str, code: int) -> bool:
    listed = _git(repo, "ls-tree", "-r", "--name-only", f"refs/tags/{tag}")
    if listed.returncode != 0:
        return False
    for path in listed.stdout.splitlines():
        path = path.strip()
        if (not _BUILD_FILE.search(path)
                or path.count("/") >= _BUILD_FILE_MAX_DEPTH):
            continue
        content = file_at_ref(repo, f"refs/tags/{tag}", path)
        if content and code in declared_version_codes(content):
            return True
    return False


def resolve_version_code(repo: Path, code: str) -> Dict:
    """The tag whose build files declare this versionCode, if unambiguous.

    Several tags may declare the same code (a release tagged twice, or a
    code left unbumped across releases). They are accepted only when they
    all name one commit; otherwise the candidates are reported and nothing
    is resolved, since picking one would answer about the wrong code.
    """
    tags = _tags_newest_first(repo)
    if tags is None:
        return {"status": "repository_unavailable"}
    searched = tags[:MAX_VERSION_CODE_TAGS]
    started = time.monotonic()
    matches: List[str] = []
    examined = 0
    for tag in searched:
        if time.monotonic() - started > VERSION_CODE_SEARCH_SECONDS:
            break
        examined += 1
        if _tag_declares_version_code(repo, tag, int(code)):
            matches.append(tag)
    record = {
        "tags_available": len(tags),
        "tags_searched": examined,
        "search_complete": examined == len(tags),
    }
    if not matches:
        return {**record, "status": "version_code_not_found"
                if examined == len(searched) else "version_code_search_incomplete"}
    commits = {
        _git(repo, "rev-list", "-n", "1", f"refs/tags/{tag}").stdout.strip()
        for tag in matches
    }
    if len(commits) != 1:
        return {**record, "status": "version_code_ambiguous",
                "candidates": matches[:20]}
    return {**record, "status": "resolved", "tag": matches[0],
            "ref": f"refs/tags/{matches[0]}", "commit": commits.pop()}


def resolve_release_ref(repo: Path, apk_name: str) -> Dict:
    """The tag this APK was built from, if the clone has one.

    Never falls back to a branch: a wrong ref would answer "was this class
    here?" about the wrong code, which is worse than not answering.
    """
    repo = Path(repo)
    version = apk_version(apk_name)
    record: Dict = {
        "apk_name": str(apk_name),
        "version_kind": version["kind"],
        "version": version["value"],
        "ref": "",
        "tag": "",
    }
    if not repo.is_dir():
        return {**record, "status": "repository_unavailable"}
    if version["kind"] == "version_code":
        return {
            **record, **resolve_version_code(repo, version["value"]),
            "resolution_method": "build_version_code",
            "binary_equivalence": BINARY_EQUIVALENCE,
        }
    if version["kind"] != "version_name":
        return {
            **record,
            "status": "version_not_resolvable",
            "detail": "no version could be read from the APK name",
        }
    listed = _git(repo, "tag", "--list")
    if listed.returncode != 0:
        return {**record, "status": "repository_unavailable"}
    tags = {line.strip() for line in listed.stdout.splitlines() if line.strip()}
    for candidate in candidate_tags(version["value"]):
        if candidate in tags:
            return {
                **record, "status": "resolved",
                "ref": f"refs/tags/{candidate}", "tag": candidate,
                "resolution_method": "tag_name",
                "binary_equivalence": BINARY_EQUIVALENCE,
            }
    return {
        **record, "status": "tag_not_found",
        "tags_available": len(tags),
        "tried": candidate_tags(version["value"]),
    }


def path_of_class_at_ref(repo: Path, ref: str, class_name: str) -> str:
    """Where `class_name` was declared at `ref`, by path convention.

    Matches the outer class's package path, then falls back to the simple
    name. Returns "" when the ref has no such file.
    """
    outer = str(class_name or "").split("$")[0]
    if not outer or not ref:
        return ""
    listed = _git(repo, "ls-tree", "-r", "--name-only", ref)
    if listed.returncode != 0:
        return ""
    paths = [
        line.strip() for line in listed.stdout.splitlines()
        if line.strip().endswith(_SOURCE_SUFFIXES)
    ]
    qualified = outer.replace(".", "/")
    exact = [path for path in paths if path.rsplit(".", 1)[0].endswith(qualified)]
    if exact:
        return sorted(exact, key=len)[0]
    simple = outer.rsplit(".", 1)[-1]
    by_name = [
        path for path in paths
        if Path(path).stem == simple
    ]
    return sorted(by_name, key=len)[0] if len(by_name) == 1 else ""


def relocated_path(repo: Path, historical_path: str) -> str:
    """Where that same file lives in the working tree now, if anywhere.

    Only an unambiguous match counts: one file with that name, of the same
    kind. A class that moved to another module is found here; two same-named
    files are left unresolved rather than guessed between.
    """
    repo = Path(repo)
    name = Path(str(historical_path or "")).name
    if not name or not repo.is_dir():
        return ""
    matches = [
        path for path in repo.rglob(name)
        if path.is_file() and ".git" not in path.parts
        and "build" not in path.parts
    ]
    if len(matches) != 1:
        return ""
    try:
        return matches[0].relative_to(repo).as_posix()
    except ValueError:
        return ""


def file_at_ref(repo: Path, ref: str, path: str) -> Optional[str]:
    """The file's content at `ref`, or None."""
    if not ref or not path:
        return None
    shown = _git(Path(repo), "show", f"{ref}:{path}")
    return shown.stdout if shown.returncode == 0 else None


def historical_class_lookup(
    repo: Path, apk_name: str, class_name: str,
) -> Dict:
    """Why the reported class is missing, and whether it can be recovered.

    Statuses:
      relocated                  present at the release and, unambiguously,
                                 still in the checkout under another path --
                                 the finding is analysable after all
      removed_since_release      present at the release, gone now: the
                                 finding is stale relative to this checkout,
                                 not a pipeline failure
      absent_at_release          not there either; the reported class was
                                 never in this repository under this name
      release_unresolved         no tag matched the APK version (by name, or
                                 by the versionCode its build files
                                 declare), so nothing above was established
    """
    reference = resolve_release_ref(repo, apk_name)
    if reference["status"] != "resolved":
        return {"status": "release_unresolved", "release_ref": reference}
    historical = path_of_class_at_ref(repo, reference["ref"], class_name)
    if not historical:
        return {
            "status": "absent_at_release", "release_ref": reference,
            "historical_path": "",
        }
    current = relocated_path(repo, historical)
    if current:
        return {
            "status": "relocated", "release_ref": reference,
            "historical_path": historical, "current_path": current,
        }
    return {
        "status": "removed_since_release", "release_ref": reference,
        "historical_path": historical, "current_path": "",
    }
