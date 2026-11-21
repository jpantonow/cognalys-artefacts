"""Imports a patch adds from a package nothing in the repository uses.

hfut-schedule's generated test imported `com.squareup.retrofit2.http.Query`. That
is Retrofit's Gradle group, not its package; the repository already imported
`retrofit2.http.Query` in the file the test was written against. The unit-test
build failed on it, a repair fixed the one line, and the revalidation that
followed cost twenty minutes.

Deliberately narrow. An import is reported only when all of these hold:

  * the patch adds it (it is not in the edit's original text);
  * it is not a platform package (java, android, kotlin, junit, ...);
  * the repository already imports a class of the same simple name from another
    package, so the right one is known;
  * no file of the repository declares the package or imports from it;
  * no file imports anything from the same first two package segments, so a
    package of a library family the repository does use (a material3 next to a
    material import) is never second-guessed.

Anything it cannot settle is silent.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Set

_PLATFORM_PREFIXES = (
    "java.", "javax.", "android.", "androidx.", "kotlin.", "kotlinx.", "dalvik.",
    "org.junit", "junit.", "org.jetbrains.", "org.w3c.", "org.xml.", "sun.", "jdk.",
)
_IMPORT = re.compile(
    r"^[ \t]*import\s+(?P<static>static\s+)?(?P<fqn>[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)"
    r"(?:\s+as\s+[A-Za-z_$][\w$]*)?\s*;?[ \t]*$",
    re.MULTILINE,
)
_SKIP_DIRECTORIES = ("build", ".gradle", ".git", "node_modules")


def imports_in(code: str) -> Set[str]:
    """Fully qualified names a source imports, without wildcards or statics."""
    return {
        match.group("fqn") for match in _IMPORT.finditer(str(code or ""))
        if not match.group("static")
    }


def _grep(repo_root: Path, pattern: str, only_names: bool = False) -> List[str]:
    """Lines (or file names) of Java/Kotlin sources matching an extended regex."""
    command = ["grep", "-rhE" if not only_names else "-rlE", "--include=*.kt", "--include=*.java"]
    command += [f"--exclude-dir={name}" for name in _SKIP_DIRECTORIES]
    command += ["-e", pattern, str(repo_root)]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if completed.returncode not in (0, 1):
        raise OSError(completed.stderr[:200])
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _existing_imports_named(repo_root: Path, simple_names: Iterable[str]) -> Dict[str, Set[str]]:
    names = sorted({re.escape(name) for name in simple_names})
    if not names:
        return {}
    pattern = r"^[[:space:]]*import[[:space:]]+[A-Za-z_$][A-Za-z0-9_$.]*\.(" + "|".join(names) + r")([[:space:]]*;?[[:space:]]*$|[[:space:]]+as[[:space:]])"
    found: Dict[str, Set[str]] = {}
    for line in _grep(repo_root, pattern):
        for fqn in imports_in(line):
            found.setdefault(fqn.rsplit(".", 1)[-1], set()).add(fqn)
    return found


def _package_in_use(repo_root: Path, package: str) -> bool:
    escaped = re.escape(package)
    pattern = rf"^[[:space:]]*(package[[:space:]]+{escaped}|import[[:space:]]+{escaped})[.;[:space:]]"
    return bool(_grep(repo_root, pattern, only_names=True))


def _family_in_use(repo_root: Path, family: str) -> bool:
    escaped = re.escape(family)
    pattern = rf"^[[:space:]]*(package|import)[[:space:]]+{escaped}([.;]|[[:space:]]*$)"
    return bool(_grep(repo_root, pattern, only_names=True))


def mismatched_imports(repo_root: Path, edits: List[dict]) -> List[dict]:
    """Added imports the repository contradicts, per edit, with the import it uses."""
    candidates = []
    for edit in edits:
        if not isinstance(edit, dict) or edit.get("edit_kind") in {"delete", "delete_file"}:
            continue
        added = imports_in(str(edit.get("corrected_code", ""))) - imports_in(
            str(edit.get("original_code", "")))
        for fqn in sorted(added):
            if fqn.startswith(_PLATFORM_PREFIXES):
                continue
            candidates.append((edit, fqn))
    if not candidates:
        return []
    existing = _existing_imports_named(repo_root, {fqn.rsplit(".", 1)[-1] for _e, fqn in candidates})
    issues = []
    for edit, fqn in candidates:
        package, simple = fqn.rsplit(".", 1)
        known = existing.get(simple, set())
        if not known or fqn in known:
            continue
        family = ".".join(package.split(".")[:2])
        if _package_in_use(repo_root, package) or _family_in_use(repo_root, family):
            continue
        issues.append({
            "code": "IMPORT_PACKAGE_NOT_IN_REPOSITORY",
            "source_file_path": str(edit.get("source_file_path") or edit.get("source_file") or ""),
            "details": [
                f"`import {fqn}`: no file of this repository declares or imports "
                f"from `{package}`, and nothing imports from `{family}`",
                f"`{simple}` is already imported here as "
                + ", ".join(f"`{name}`" for name in sorted(known)[:3]),
                "Use the import the repository already uses; a Gradle group id "
                "is not a package name.",
            ],
        })
    return issues
