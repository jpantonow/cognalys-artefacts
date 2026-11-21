#!/usr/bin/env python3

import argparse
import csv
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from cognalys.sources import read_rows
from cognalys.utils.contribution_base import contribution_base_status
from cognalys.utils.network_git import network_git_env, network_subcommand, run_network_git
from cognalys.utils.pr_text import kotlin_member_name
from cognalys.utils.step_outcome import step_exit_code
from cognalys.utils.corpus_rows import (
    INPUT_INVALID, detector_context, invalid_row_record, row_problems,
)
from cognalys.extraction.repository_guidance import discover_repository_guidance, infer_branch_policy
from cognalys.extraction.dependency_context import collect_dependency_context, group_owns_class
from cognalys.extraction.dependency_finding import collect_dependency_finding
from cognalys.extraction.release_ref import historical_class_lookup
from cognalys.extraction.app_caller_attribution import (
    attribute_to_application, declared_name, load_specification, reported_value,
)
from cognalys.extraction.apk_evidence import (
    apk_evidence_enabled,
    application_package,
    class_in_application_namespaces,
    collect_apk_evidence,
    collect_method_bytecode_bindings,
    name_looks_obfuscated,
    repository_namespaces,
)
from cognalys.workspace import library_data


COMMIT_PIN = re.compile(r"\bcommit\s*=\s*([0-9a-fA-F]{7,40})\b")
CHECKOUT_PIN = re.compile(r"\b(?:checkout|revision)\s*=\s*([A-Za-z0-9_.\-/]+)")
BARE_COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")
BARE_REF = re.compile(r"^[A-Za-z0-9_.\-/]+$")
# A column holding nothing but the value, and free text that mentions it.
# priority_reason is the annotation column this project already writes, and
# er7.csv puts the pin there.
SUBMODULE_TIMEOUT_SECONDS = 900


def populate_empty_submodules(repo_path: Path, logger: logging.Logger) -> list[str]:
    """Check out the declared submodules whose directories are empty.

    A clone leaves submodules empty, and extraction runs before the build
    step that initializes them. etesync declares cert4android as a submodule,
    so at.bitfire.cert4android.CustomCertService -- source in the project's
    own tree -- was recorded as an external dependency nobody could find.
    The build step's own routine is used, rewrite of dead protocols
    included. Returns the paths populated; a failure is logged, not raised.
    """
    gitmodules = Path(repo_path) / ".gitmodules"
    if not gitmodules.is_file():
        return []
    try:
        from cognalys.builds.apk_build import declared_submodule_paths, initialize_submodules
    except ImportError as exc:
        logger.warning("submodules of %s not initialised: %s", repo_path, exc)
        return []
    empty = [
        path for path in declared_submodule_paths(Path(repo_path))
        if not path.is_dir() or not any(path.iterdir())
    ]
    if not empty:
        return []
    import tempfile
    # A real file: the routine hands it to git as stdout.
    with tempfile.TemporaryFile("w+", encoding="utf-8") as log:
        failure, _ = initialize_submodules(
            Path(repo_path), log, SUBMODULE_TIMEOUT_SECONDS, Path(repo_path),
        )
    populated = [
        path.relative_to(Path(repo_path).resolve()).as_posix() for path in empty
        if path.is_dir() and any(path.iterdir())
    ]
    if failure:
        logger.warning("submodules of %s only partly initialised: %s",
                       repo_path, str(failure)[:300])
    # The update is bounded (utils.network_git): one that stalls ends, and
    # extraction reads the superproject without what it could not fetch.
    left_empty = [
        path.relative_to(Path(repo_path).resolve()).as_posix() for path in empty
        if not path.is_dir() or not any(path.iterdir())
    ]
    if left_empty:
        logger.warning("%s: continuing without submodule(s) %s",
                       repo_path, ", ".join(left_empty))
    if populated:
        logger.info("%s: checked out submodules %s", repo_path, ", ".join(populated))
    return populated


def _code_text(line: str) -> str:
    """The line with string and char literals emptied and a // comment removed."""
    line = re.sub(r'"(?:\\.|[^"\\])*"', '""', line)
    line = re.sub(r"'(?:\\.|[^'\\])'", "''", line)
    return line.split("//", 1)[0]


SPECIFICATIONS_DIR = library_data("specifications")


def _declaration_bounds(extractor, path: Path, line: int) -> Optional[Tuple[int, int]]:
    """(start, end) of the declaration that contains ``line``, or None.

    _extract_declaration_containing_line falls back to the nearest
    declaration above when none contains the line; that fallback is not the
    statement's declaration, so it is refused here.
    """
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = extractor._extract_declaration_containing_line(content, line)
    if not found or not (found[1] <= line <= found[2]):
        return None
    return found[1], found[2]


_SOURCE_LOCATION = re.compile(r"^(?P<file>[^:\\/]+\.(?:kt|java)):(?P<line>\d+)$")


def reported_source_line(row: Dict, file_path: Path) -> Optional[int]:
    """The line the detector recorded, when it names the resolved file."""
    location = str(row.get("detector_source_location", "") or "").strip()
    match = _SOURCE_LOCATION.match(location)
    if not match or match.group("file") != Path(file_path).name:
        return None
    return int(match.group("line"))


def reported_overload(extractor, content: str, file_path: Path, row: Dict,
                      source_method: str) -> Tuple[Optional[Tuple[str, int, int]], Optional[int]]:
    """The declaration of ``source_method`` holding the detector's line.

    Returns (extracted, line), or (None, None) when the row records no line
    in this file or the declaration holding it is not ``source_method``.
    """
    line = reported_source_line(row, file_path)
    if not line or not _declaration_bounds(extractor, file_path, line):
        return None, None
    containing = extractor._extract_declaration_containing_line(content, line)
    if containing and declared_name(containing[0]) == source_method:
        return containing, line
    return None, None


COMMIT_COLUMNS = ("commit",)
REF_COLUMNS = ("checkout", "revision")
FREE_TEXT_COLUMNS = ("priority_reason", "note", "notes")


def revision_pin(row: dict) -> str:
    """The revision the analysed artifact was built from, or "".

    An advisory can name a version the repository's default branch no longer
    resembles. CVE-2021-21385 reports
    org.mifos.mobilebanking.api.SelfServiceOkHttpClient at tag v1.0.0, and
    openMF/mifos-mobile has since been rewritten as a Kotlin Multiplatform
    project in which neither that class nor that package exists. The row in
    er7.csv says so directly -- "HISTORICAL_VERSION; checkout=v1.0.0;
    commit=476b7506671a32513318e12e82467b6b660c6c69" and, in the operator's
    own words, "Usar a tag antiga, cujo pacote difere do codigo moderno" --
    and nothing read it: extraction cloned the default branch at depth 1,
    failed to find the class, reported it as an external dependency, and
    spent 2m19s of model time establishing that it knew nothing.

    A commit is preferred over a tag because it cannot move. Returns "" when
    no column names a revision, which leaves the default-branch clone exactly
    as it was.
    """
    def value(column: str) -> str:
        return str(row.get(column, "") or "").strip()

    for column in COMMIT_COLUMNS:
        candidate = value(column)
        if BARE_COMMIT.match(candidate):
            return candidate
    for column in (*COMMIT_COLUMNS, *REF_COLUMNS, *FREE_TEXT_COLUMNS):
        match = COMMIT_PIN.search(value(column))
        if match:
            return match.group(1)
    for column in REF_COLUMNS:
        candidate = value(column)
        if candidate and BARE_REF.match(candidate):
            return candidate
    for column in (*COMMIT_COLUMNS, *REF_COLUMNS, *FREE_TEXT_COLUMNS):
        match = CHECKOUT_PIN.search(value(column))
        if match:
            return match.group(1)
    return ""


_HOSTED_REMOTE = re.compile(r"^(?:https?://|git@|ssh://)")


def hosted_remote_of(source: str) -> str:
    """The hosted repository behind a local checkout's path, or "".

    "" when ``source`` is not a local git checkout (it is already a URL) or
    when the checkout has no remote on a hosting service.
    """
    path = Path(source)
    if _HOSTED_REMOTE.match(source) or not (path / ".git").exists():
        return ""
    for remote in ("upstream", "origin"):
        url = subprocess.run(["git", "-C", str(path), "config", "--get", f"remote.{remote}.url"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
        if _HOSTED_REMOTE.match(url):
            return url
    return ""


# Methods a compiler gives a lambda or coroutine body: the source names the
# enclosing function instead, or nothing at all.
_SYNTHETIC_METHODS = {"invokeSuspend", "invoke", "apply", "accept", "run", "call",
                      "onClick", "onChanged", "emit", "get", "test"}


def governed_type(spec: str) -> str:
    """The API type a specification governs, from its name (MacSpec -> Mac)."""
    return spec[:-4] if spec.endswith("Spec") and len(spec) > 4 else spec


def _the_one_holding_value(candidates: list, value: str, token: str):
    """The single candidate whose code holds the detector's value as a string
    literal, or None.

    Candidates are tuples whose first item is the extracted (code, start,
    end). Only a plain token is compared ("EdDSA", "AES/CBC/PKCS5Padding"):
    a computed value has no literal, and the case stays ambiguous. Every
    other candidate must call ``token`` with a different literal first
    argument: one passing a constant or a parameter
    (`Cipher.getInstance(TRANSFORMATION)`, Password Store's
    `KeyPairGenerator.getInstance(algorithm.algorithm, ...)`) may produce the
    same value at run time, and the case stays ambiguous.
    """
    if not value or not re.fullmatch(r"[\w.\-/+]+", value):
        return None
    holding = [item for item in candidates if f'"{value}"' in item[0][0]]
    if len(holding) != 1:
        return None
    call = re.compile(rf"\b{re.escape(token)}\b(?:\s*\.\s*\w+)?\s*\(\s*(?P<first>[^,)]*)")
    for item in candidates:
        if item is holding[0]:
            continue
        firsts = [match.group("first").strip() for match in call.finditer(item[0][0])]
        if not firsts or any(not re.fullmatch(r'"[^"\\]*"', first) or first == f'"{value}"'
                             for first in firsts):
            return None
    return holding[0]


def locate_unnamed_method(extractor, file_path: Path, content: str, source_method: str,
                          method_name: str, spec: str, outer_class: str = "",
                          value: str = ""):
    """Find the declaration a reported method stands for when its name is not
    in the resolved file. Returns (extracted, name, file, content, mapping) or None.

    Password Store (2026-10-25) lost all seven RVSec findings here:
    - a lambda or coroutine body (SettingsActivity$$ExternalSyntheticLambda1
      .apply, KeyGenType$1.invokeSuspend) names no source function: the one
      function in the outer class that uses the governed API is taken;
    - SshKey.generateKeystoreNativeKey resolved to another file of the same
      package: a unique declaration of that name among the package's files
      is taken;
    - a lambda whose outer class never names the API (KeyGenType$1
      .invokeSuspend calls SshKey.generateKeystoreNativeKey, which holds the
      KeyPairGenerator call) stands for the one function it calls, declared
      in the same Gradle module, that uses the governed API.
    Only a single match is accepted; anything ambiguous stays not found,
    unless exactly one candidate holds the value the detector saw
    (``value``) as a literal and every other one calls the API with a
    different literal (see _the_one_holding_value).
    ``outer_class`` is the simple name of the class the bytecode names.
    """
    if method_name in _SYNTHETIC_METHODS or source_method in _SYNTHETIC_METHODS:
        token = governed_type(spec)
        if token:
            found: dict = {}
            for number, line in enumerate(content.split("\n"), 1):
                if re.search(rf"\b{re.escape(token)}\b", line):
                    candidate = extractor._extract_declaration_containing_line(content, number)
                    if candidate:
                        name = declared_name(candidate[0])
                        if name:
                            found[name] = (candidate, name)
            if len(found) == 1:
                candidate, name = next(iter(found.values()))
                return candidate, name, file_path, content, "governed_api_in_outer_class"
            if len(found) > 1:
                chosen = _the_one_holding_value(list(found.values()), value, token)
                if chosen:
                    return (chosen[0], chosen[1], file_path, content,
                            "governed_api_value_literal")
                return None
            return _callee_using_api(extractor, file_path, content, token,
                                     outer_class, value)
        return None
    declaration = re.compile(
        rf"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?{re.escape(source_method)}\s*\("
        rf"|\b{re.escape(source_method)}\s*\([^;{{)]*\)\s*(?:throws[^{{]*)?\{{")
    matches = []
    for sibling in sorted(file_path.parent.glob("*")):
        if sibling == file_path or sibling.suffix not in (".kt", ".java"):
            continue
        try:
            text = sibling.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if declaration.search(text):
            matches.append((sibling, text))
    if len(matches) != 1:
        return None
    sibling, text = matches[0]
    extracted = extractor.extract_method_flexible(text, source_method)
    if not extracted:
        return None
    return extracted, source_method, sibling, text, "same_package_file"


_NOT_A_CALL = {"if", "for", "while", "switch", "catch", "synchronized", "return", "when",
               "super", "this", "println", "require", "check", "error", "listOf", "setOf",
               "mapOf", "arrayOf", "launch", "async", "withContext", "runBlocking", "let",
               "apply", "also", "run", "with", "use", "forEach", "map", "filter"}
_MODULE_FILE_LIMIT = 4000


def _module_root(file_path: Path) -> Path:
    """The nearest ancestor holding a Gradle build file, else the file's folder."""
    for parent in file_path.parents:
        if any(parent.glob("build.gradle*")) or any(parent.glob("settings.gradle*")):
            return parent
    return file_path.parent


# Names a file declares, never the ones it calls: a call alone on its line
# (`    SshKey.generateKeystoreNativeKey(SshKey.Algorithm.Rsa, auth)`) is not a
# declaration of generateKeystoreNativeKey.
_KOTLIN_DECLARED = re.compile(r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w$.<>?, ]+\.)?(\w+)\s*\(")
_JAVA_DECLARED = re.compile(
    r"(?m)^\s*(?:@[\w.]+(?:\([^)]*\))?\s+)*"
    r"(?:(?:public|protected|private|static|final|synchronized|abstract|native|default"
    r"|strictfp)\s+)*(?:<[^>]+>\s+)?(?!(?:return|new|else|throw)\b)[\w$.<>\[\],?]+\s+"
    r"(\w+)\s*\([^;{}]*\)\s*(?:throws\s+[\w.$,\s]+)?\{")


def _declared_names(file_path: Path, content: str) -> Set[str]:
    pattern = _JAVA_DECLARED if file_path.suffix == ".java" else _KOTLIN_DECLARED
    return {name for name in pattern.findall(content) if name not in _NOT_A_CALL}


_JVM_NAME = re.compile(r'^\s*@file\s*:\s*JvmName\s*\(\s*"([\w$]+)"\s*\)', re.MULTILINE)


# A pin that names a commit (an abbreviated or full SHA), not a tag.
_COMMIT_PIN = re.compile(r"[0-9a-fA-F]{7,40}")


def kotlin_facade_name(stem: str, content: str) -> str:
    """The class Kotlin compiles a file's top-level functions into.

    The file name with every character a JVM name cannot hold turned into
    `_` and its first letter capitalised, plus Kt, unless `@file:JvmName`
    names it: Herald's ClientCerts.android.kt (2026-11-05) is
    ClientCerts_androidKt, which a plain stem + Kt never matched.
    """
    named = _JVM_NAME.search(content)
    if named:
        return named.group(1)
    name = re.sub(r"[^\w$]", "_", stem)
    return name[:1].upper() + name[1:] + "Kt"


def _outer_class_body(extractor, content: str, outer_class: str) -> str:
    """The body of the class the bytecode names (KeyGenType for KeyGenType$1),
    or "" when the file declares no such class (a Kotlin `FooKt` facade)."""
    if not outer_class:
        return ""
    header = re.compile(rf"\b(?:class|object|interface)\s+{re.escape(outer_class)}\b")
    for index, line in enumerate(content.split("\n")):
        if header.search(_code_text(line)):
            body = extractor._extract_method_body(content, index, line)
            if body and "{" in body:
                return body
    return ""


def _callee_using_api(extractor, file_path: Path, content: str, token: str,
                      outer_class: str = "", value: str = ""):
    """The one function the outer class calls, in its module, whose body names
    ``token``.

    Calls are read from the body of ``outer_class`` -- a file with several
    classes cannot contribute another class's callees -- or from the whole
    file when it declares no such class. Returns (extracted, name, file,
    content, mapping) or None when no such function exists or more than one
    does and ``value`` does not single one out.
    """
    declared = _declared_names(file_path, content)
    calling = _outer_class_body(extractor, content, outer_class) or content
    called = {name for name in re.findall(r"(?<![\w.])([a-z]\w+)\s*\(", calling)
              if name not in _NOT_A_CALL and name not in declared}
    # Qualified calls too: SshKey.generateKeystoreNativeKey(...).
    called |= {name for name in re.findall(r"\.([a-z]\w+)\s*\(", calling)
               if name not in _NOT_A_CALL and name not in declared}
    if not called:
        return None
    declaration = re.compile(
        r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w$.<>?, ]+\.)?(?P<kotlin>" + "|".join(map(re.escape, sorted(called)))
        + r")\s*\(|\b(?P<java>" + "|".join(map(re.escape, sorted(called)))
        + r")\s*\([^;{)]*\)\s*(?:throws[^{]*)?\{")
    token_pattern = re.compile(rf"\b{re.escape(token)}\b")
    matches: list = []
    seen = 0
    for path in sorted(_module_root(file_path).rglob("*")):
        if path.suffix not in (".kt", ".java") or not path.is_file() or path == file_path:
            continue
        posix = path.as_posix()
        if "/build/" in posix or re.search(r"/src/(?:test|androidTest|\w+Test)\w*/", posix):
            continue
        seen += 1
        if seen > _MODULE_FILE_LIMIT:
            return None
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not token_pattern.search(text):
            continue
        for match in declaration.finditer(text):
            name = match.group("kotlin") or match.group("java")
            extracted = extractor.extract_method_flexible(text, name)
            if extracted and token_pattern.search(extracted[0]):
                if (path, name) not in [(item[2], item[1]) for item in matches]:
                    matches.append((extracted, name, path, text))
            if len(matches) > 1 and not value:
                return None
    if len(matches) > 1:
        chosen = _the_one_holding_value(matches, value, token)
        if not chosen:
            return None
        extracted, name, path, text = chosen
        return extracted, name, path, text, "governed_api_value_literal"
    if len(matches) != 1:
        return None
    extracted, name, path, text = matches[0]
    return extracted, name, path, text, "governed_api_in_callee"


# Source sets an Android APK never compiles: other Kotlin Multiplatform
# targets and tests. Everything else -- main, androidMain, and flavour or
# build-type sets such as fdroid, foss, debug -- is Android code.
_NON_ANDROID_SOURCE_SET = re.compile(
    r"^(jvm|desktop|ios\w*|js|wasm\w*|native|macos\w*|linux\w*|mingw\w*|apple|tvos\w*|"
    r"watchos\w*|server|web)Main$|Test$|^test$|^testFixtures$")
_SHARED_SOURCE_SETS = {"commonMain", "jvmCommonMain", "jvmAndroidMain", "androidJvmMain",
                       "nonJsMain", "nonWebMain", "mobileMain", "jvmAndAndroidMain"}


def source_set_of(path: Path, root: Optional[Path] = None) -> str:
    """The Gradle source set a file sits in (src/<set>/...), or ""."""
    parts = (path.relative_to(root) if root else path).parts
    for index, part in enumerate(parts[:-1]):
        if part == "src" and index + 1 < len(parts) - 1:
            return parts[index + 1]
    return ""


def android_source_rank(path: Path, root: Optional[Path] = None) -> int:
    """0: compiled into an Android APK; 1: shared, may be; 2: never (desktop, iOS,
    JS, tests). A file outside any src/<set> layout ranks as Android code."""
    source_set = source_set_of(path, root)
    if source_set in _SHARED_SOURCE_SETS or source_set.endswith("CommonMain"):
        return 1
    if source_set and _NON_ANDROID_SOURCE_SET.search(source_set):
        return 2
    return 0


class RepositoryIndexer:
    # Bump when the shape of a cached index changes, so stale entries written
    # by an older build are refused rather than misread.
    # 2: source packages named "generated" are indexed
    # 3: a class in several source sets resolves to the one an APK runs
    # 4: a Kotlin file's facade is named as the compiler names it
    INDEX_CACHE_VERSION = 4

    def __init__(self, repo_root: Path, logger: logging.Logger):
        self.repo_root = Path(repo_root)
        self.logger = logger
        self.class_index: Dict[str, Path] = {}
        self.file_contents: Dict[Path, str] = {}
        # Source files the walk could not read. Their classes are missing
        # from the index, so a class that "is not in the checkout" may be in
        # one of them; the index built this way is also never cached, or a
        # transient read failure would be remembered for the commit.
        self.unreadable_files: List[str] = []

    def _index_cache_path(self) -> Optional[Path]:
        """Where this clone's index is remembered.

        Inside .git, never in the working tree: every later stage treats a
        fresh clone with uncommitted changes as unusable, and a cache file
        dropped beside the sources would be exactly that.
        """
        git_dir = self.repo_root / ".git"
        return git_dir / "pipeline_class_index.json" if git_dir.is_dir() else None

    def _index_fingerprint(self) -> str:
        """The commit this index describes, or "" if it cannot be trusted.

        A dirty working tree returns "", which disables the cache in both
        directions: the index would describe sources that are no longer on
        disk, and there is no cheap key that captures uncommitted edits.
        """
        try:
            head = subprocess.run(
                ["git", "-C", str(self.repo_root), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=30,
            )
            if head.returncode != 0:
                return ""
            dirty = subprocess.run(
                ["git", "-C", str(self.repo_root), "status", "--porcelain"],
                capture_output=True, text=True, timeout=120,
            )
            if dirty.returncode != 0 or dirty.stdout.strip():
                return ""
            return head.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    def _load_index_cache(self, fingerprint: str) -> bool:
        path = self._index_cache_path()
        if not path or not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.debug(f"Ignoring unreadable index cache {path}: {exc}")
            return False
        if (
            data.get("format_version") != self.INDEX_CACHE_VERSION
            or data.get("commit") != fingerprint
            or not isinstance(data.get("classes"), dict)
        ):
            return False
        self.class_index = {
            name: self.repo_root / relative
            for name, relative in data["classes"].items()
        }
        self.logger.info(
            f"Reusing cached index for {self.repo_root} at {fingerprint[:12]}: "
            f"{len(self.class_index)} unique class definitions"
        )
        return True

    def _store_index_cache(self, fingerprint: str) -> None:
        path = self._index_cache_path()
        if not path:
            return
        try:
            payload = {
                "format_version": self.INDEX_CACHE_VERSION,
                "commit": fingerprint,
                "classes": {
                    name: file_path.relative_to(self.repo_root).as_posix()
                    for name, file_path in self.class_index.items()
                },
            }
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            temporary.replace(path)
        except (OSError, ValueError) as exc:
            self.logger.debug(f"Could not store index cache {path}: {exc}")

    def build_index(self) -> None:
        # Walking every .java and .kt file and regexing out its class
        # declarations took 14-34s per run in the runs of 2026-09-07, redone
        # from scratch each time even when the clone was reused verbatim.
        # The result depends only on the checked-out commit.
        fingerprint = self._index_fingerprint()
        if fingerprint and self._load_index_cache(fingerprint):
            return
        self.logger.info(f"Building index for repository: {self.repo_root}")
        files_indexed = 0
        # Build output (build/generated/...) sits under build. A source package
        # may itself be named "generated" (br.unb.cic.cryptoapp.generated), so
        # that name alone does not mark a file as generated.
        ignored = {".git", ".gradle", "build", "node_modules"}
        source_files = list(self.repo_root.rglob("*.java")) + list(
            self.repo_root.rglob("*.kt")
        )
        for file_path in source_files:
            if ignored.intersection(file_path.relative_to(self.repo_root).parts):
                continue
            classes = self._extract_classes_from_file(file_path)
            files_indexed += len(classes)
            for class_name in classes:
                # A Kotlin Multiplatform class has one actual per platform,
                # and the last one indexed used to win: Amethyst's
                # SecretEncryption (2026-10-25) resolved to jvmMain, the
                # desktop actual an APK never runs, and the pull request
                # changed code the finding never executed.
                current = self.class_index.get(class_name)
                if current is None or (android_source_rank(file_path, self.repo_root)
                                       < android_source_rank(current, self.repo_root)):
                    self.class_index[class_name] = file_path

        self.logger.info(
            f"Indexed {files_indexed} classes from "
            f"{len(self.class_index)} unique class definitions"
        )
        if self.unreadable_files:
            self.logger.warning(
                f"{len(self.unreadable_files)} source file(s) could not be read; "
                "the class index is incomplete and is not cached"
            )
        elif fingerprint:
            self._store_index_cache(fingerprint)

    def _extract_classes_from_file(self, file_path: Path) -> Set[str]:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.file_contents[file_path] = content
        except OSError as e:
            self.logger.warning(f"Could not read {file_path} while indexing: {e}")
            try:
                relative = file_path.relative_to(self.repo_root).as_posix()
            except ValueError:
                relative = str(file_path)
            self.unreadable_files.append(relative)
            return set()

        classes = set()

        package_match = re.search(r"package\s+([\w.]+)", content)
        package = package_match.group(1) if package_match else ""

        class_pattern = (
            r"(?:public\s+)?"
            r"(?:abstract\s+)?"
            r"(?:final\s+)?"
            r"(?:static\s+)?"
            r"(?:class|interface|object|enum\s+class)\s+(\w+)"
        )

        if file_path.suffix == ".kt":
            facade = kotlin_facade_name(file_path.stem, content)
            classes.add(f"{package}.{facade}" if package else facade)

        for match in re.finditer(class_pattern, content):
            class_name = match.group(1)
            full_name = f"{package}.{class_name}" if package else class_name
            classes.add(full_name)

            inner_pattern = (
                r"(?:public\s+)?"
                r"(?:abstract\s+)?"
                r"(?:final\s+)?"
                r"(?:static\s+)?"
                r"class\s+(\w+)\s*[{<]"
            )

            for inner_match in re.finditer(inner_pattern, content[match.end():]):
                inner_class = inner_match.group(1)
                inner_full = f"{full_name}${inner_class}"
                classes.add(inner_full)

        return classes

    def find_file_for_class(self, class_name: str) -> Optional[Path]:
        return self.resolve_class(class_name)[0]

    def resolve_class(self, class_name: str) -> Tuple[Optional[Path], Dict]:
        """The file declaring `class_name`, and how it was found.

        The simple-name fallback used to return the first indexed class with
        the same simple name in any package, in index order. For
        com.amaze.filemanager.utils.FileUtils it returned
        com.amaze.filemanager.filesystem.files.FileUtils; had that class
        declared the reported method, a different class would have been
        reviewed and patched as the reported one. The fallback still exists,
        because classes move between packages across versions, but it now
        requires the candidate to share the reported package's root, picks
        the closest package when several do, gives up when that is a tie,
        and says which of these happened.
        """
        class_name = re.sub(r"\([^)]*\)$", "", class_name)
        if class_name in self.class_index:
            return self.class_index[class_name], {"method": "exact"}

        outer_class = class_name.split("$")[0]
        if outer_class in self.class_index:
            return self.class_index[outer_class], {"method": "outer_class"}

        class_simple = class_name.split(".")[-1].split("$")[-1]
        reported_package = outer_class.rsplit(".", 1)[0].split(".") \
            if "." in outer_class else []

        def shared_segments(indexed_class: str) -> int:
            indexed_package = indexed_class.split("$")[0].rsplit(".", 1)[0].split(".")
            count = 0
            for left, right in zip(reported_package, indexed_package):
                if left != right:
                    break
                count += 1
            return count

        candidates = sorted(
            (shared_segments(indexed_class), indexed_class, file_path)
            for indexed_class, file_path in self.class_index.items()
            if indexed_class.split(".")[-1].split("$")[-1] == class_simple
        )
        # Two leading segments are the organisation (com.amaze, org.kiwix);
        # a namesake outside it is another project's class. A reported class
        # with no package at all has nothing to compare, and keeps the old
        # behaviour of accepting a single namesake.
        required = min(2, len(reported_package))
        candidates = [item for item in candidates if item[0] >= required]
        if not candidates:
            return None, {"method": "not_found"}
        best = candidates[-1][0]
        closest = {item[2] for item in candidates if item[0] == best}
        if len(closest) > 1:
            return None, {
                "method": "ambiguous_simple_name",
                "candidates": sorted(
                    item[1] for item in candidates if item[0] == best
                )[:10],
            }
        _, resolved, file_path = candidates[-1]
        return file_path, {
            "method": "simple_name_fallback",
            "resolved_class": resolved,
            "shared_package_segments": best,
        }

    def get_file_content(self, file_path: Path) -> Optional[str]:
        if file_path in self.file_contents:
            return self.file_contents[file_path]

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.file_contents[file_path] = content
            return content
        except Exception as e:
            self.logger.debug(f"Error reading {file_path}: {e}")
            return None


class MethodExtractor:
    """
    Mesmo MethodExtractor do extrator original.
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger

    @staticmethod
    def extract_kotlin_implicit_receiver_calls(method_body: str) -> List[Dict]:
        """Describe calls made through implicit receivers in Kotlin scopes.

        This is deliberately metadata-only: the original method stays intact,
        while later stages can understand ``receiver.apply { load() }`` as a
        call on ``receiver``. Nested braces are balanced before calls are read.
        """
        scopes = []
        scope_pattern = re.compile(r"\.(apply|run)\s*\{")
        ignored = {
            "apply", "run", "with", "let", "also", "if", "when", "for",
            "while", "catch", "return", "fun", "suspendCoroutine",
        }

        for match in scope_pattern.finditer(method_body):
            open_brace = method_body.find("{", match.start())
            depth = 0
            close_brace = -1
            for index in range(open_brace, len(method_body)):
                if method_body[index] == "{":
                    depth += 1
                elif method_body[index] == "}":
                    depth -= 1
                    if depth == 0:
                        close_brace = index
                        break
            if close_brace < 0:
                continue

            line_start = method_body.rfind("\n", 0, match.start()) + 1
            receiver = method_body[line_start:match.start()].strip()
            if "=" in receiver:
                receiver = receiver.rsplit("=", 1)[-1].strip()
            body = method_body[open_brace + 1:close_brace]
            body_offset = open_brace + 1

            for call in re.finditer(r"(?<![.\w])([A-Za-z_]\w*)\s*\(", body):
                call_name = call.group(1)
                if call_name in ignored:
                    continue
                absolute = body_offset + call.start(1)
                scopes.append(
                    {
                        "scope_function": match.group(1),
                        "receiver_expression": receiver,
                        "call": call_name,
                        "method_line": method_body.count("\n", 0, absolute) + 1,
                        "normalized_call": f"{receiver}.{call_name}(...)"
                        if receiver else f"<implicit>.{call_name}(...)"
                    }
                )
        return scopes

    @staticmethod
    def _split_arguments(value: str) -> List[str]:
        """Split comma-separated parameters while preserving nested calls."""
        parts, start, depth = [], 0, 0
        quote = None
        for index, char in enumerate(value):
            if quote:
                if char == quote and (index == 0 or value[index - 1] != "\\"):
                    quote = None
                continue
            if char in {'"', "'"}:
                quote = char
            elif char in "(<[{":
                depth += 1
            elif char in ")>]}":
                depth = max(0, depth - 1)
            elif char == "," and depth == 0:
                parts.append(value[start:index].strip())
                start = index + 1
        tail = value[start:].strip()
        if tail:
            parts.append(tail)
        return parts

    @staticmethod
    def resolve_method_parameters(
        method_body: str,
        method_name: str,
        call_sites: str,
        error_message: str = "",
    ) -> List[Dict]:
        """Map method parameters to concrete call-site and runtime evidence."""
        header = method_body.split("{", 1)[0]
        name_match = re.search(rf"\b{re.escape(method_name)}\s*\(", header)
        if not name_match:
            return []
        open_paren = header.find("(", name_match.start())
        close_paren = header.rfind(")")
        if open_paren < 0 or close_paren <= open_paren:
            return []

        parameters = []
        for position, raw in enumerate(
            MethodExtractor._split_arguments(header[open_paren + 1:close_paren])
        ):
            clean = re.sub(r"\b(?:final|vararg|crossinline|noinline)\b", "", raw).strip()
            if ":" in clean:  # Kotlin: name: Type
                name, param_type = clean.split(":", 1)
                name = name.strip()
                param_type = param_type.split("=", 1)[0].strip()
            else:  # Java: Type name
                tokens = clean.replace("...", "[] ").split()
                if len(tokens) < 2:
                    continue
                name, param_type = tokens[-1], " ".join(tokens[:-1])
            parameters.append(
                {
                    "position": position,
                    "name": name,
                    "type": param_type,
                    "observed_call_values": [],
                    "runtime_evidence": [],
                    "resolution_status": "unresolved",
                }
            )

        clean_sites = "\n".join(
            re.sub(r"^\s*\d+:\s?", "", line)
            for line in call_sites.splitlines()
            if line.strip() != "---"
        )
        call_pattern = re.compile(rf"\b{re.escape(method_name)}\s*\(")
        for match in call_pattern.finditer(clean_sites):
            start = match.end()
            depth, quote, end = 1, None, -1
            for index in range(start, len(clean_sites)):
                char = clean_sites[index]
                if quote:
                    if char == quote and clean_sites[index - 1] != "\\":
                        quote = None
                    continue
                if char in {'"', "'"}:
                    quote = char
                elif char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        end = index
                        break
            if end < 0:
                continue
            arguments = MethodExtractor._split_arguments(clean_sites[start:end])
            for parameter, argument in zip(parameters, arguments):
                argument = argument.strip()
                if re.fullmatch(r"-?\d+(?:\.\d+)?|true|false|null|\"[^\"]*\"|'[^']*'", argument):
                    if argument not in parameter["observed_call_values"]:
                        parameter["observed_call_values"].append(argument)

        algorithm = re.search(
            r"(?:for|found)\s+(?:algorithm\s+)?([A-Za-z0-9_.-]+)",
            error_message,
            re.IGNORECASE,
        )
        if algorithm:
            for parameter in parameters:
                if "algorithm" in parameter["name"].lower():
                    parameter["runtime_evidence"].append(
                        f"Dynamic-analysis error identifies algorithm {algorithm.group(1)}"
                    )

        for parameter in parameters:
            if parameter["observed_call_values"] and parameter["runtime_evidence"]:
                parameter["resolution_status"] = "resolved"
            elif parameter["observed_call_values"] or parameter["runtime_evidence"]:
                parameter["resolution_status"] = "partially_resolved"
        return parameters

    def extract_method_from_java(
        self,
        file_content: str,
        method_name: str,
    ) -> Optional[Tuple[str, int, int]]:
        lines = file_content.split("\n")

        method_pattern = (
            rf"(?:public|private|protected|abstract|static|final|synchronized|native|\s)*"
            rf"(?:\w+(?:<[^>]+>)?)\s+"
            rf"{re.escape(method_name)}\s*\("
        )

        constructor_pattern = (
            rf"(?:public|private|protected|abstract|final|)\s*"
            rf"{re.escape(method_name)}\s*\("
        )

        for start_idx, line in enumerate(lines):
            if re.search(method_pattern, line) or re.search(constructor_pattern, line):
                method_body = self._extract_method_body(
                    file_content,
                    start_idx,
                    line,
                )

                if method_body:
                    method_lines = method_body.split("\n")
                    end_idx = start_idx + len(method_lines) - 1
                    return method_body, start_idx + 1, end_idx + 1

        return None

    def extract_method_flexible(
        self,
        file_content: str,
        method_name: str,
        source_line: Optional[int] = None,
    ) -> Optional[Tuple[str, int, int]]:
        """Extract Java/Kotlin declarations, including multiline signatures."""
        if source_line:
            containing = self._extract_declaration_containing_line(
                file_content, source_line
            )
            if containing:
                return containing

        lines = file_content.split("\n")
        escaped_name = re.escape(method_name)
        declaration_patterns = (
            # Kotlin functions, optionally with a receiver type.
            rf"\bfun\s+(?:<[^>]+>\s*)?(?:[\w?.<>]+\.)?{escaped_name}\s*\(",
            # Java methods and constructors. Calls are filtered below.
            rf"\b{escaped_name}\s*\(",
        )

        for start_idx in range(len(lines)):
            # Match the target on the candidate declaration line itself. The
            # previous 12-line window could find the name in a later method
            # declaration while extraction started in an unrelated statement.
            candidate_line = lines[start_idx]
            if not any(
                re.search(pattern, candidate_line)
                for pattern in declaration_patterns
            ):
                continue
            if not self._looks_like_declaration(lines, start_idx, method_name):
                continue
            extracted = self._extract_method_body(file_content, start_idx, lines[start_idx])
            if extracted:
                method_lines = extracted.split("\n")
                return extracted, start_idx + 1, start_idx + len(method_lines)

        return None

    def extract_class_initializer(
        self,
        file_content: str,
    ) -> Optional[Tuple[str, int, int]]:
        """Best-effort mapping for JVM <clinit> to a source initializer block."""
        lines = file_content.split("\n")
        for start_idx, line in enumerate(lines):
            if re.search(
                r"\b(?:companion\s+object|static\s*\{|init\s*\{|"
                r"(?:val|var)\s+\w+[^=]*(?:=|by\s+lazy\b).*\{)",
                line,
            ):
                body = self._extract_method_body(file_content, start_idx, line)
                if body:
                    body_lines = body.split("\n")
                    return body, start_idx + 1, start_idx + len(body_lines)
        return None

    def extract_static_initializer(
        self,
        file_content: str,
        api_type: str = "",
        source_line: Optional[int] = None,
    ) -> Optional[Tuple[str, int, int]]:
        """The source of JVM ``<clinit>``: a class's static initialisation.

        weeWX Weather's NetworkClient (2026-11-02) wrote its SSLContext setup
        in an Allman ``static`` block, ``static`` alone on one line and the
        brace on the next; extract_class_initializer only knew ``static {`` on
        one line, and the finding ended "método não encontrado". ``<clinit>``
        is the concatenation of every static block and static field
        initialiser (Java) and of the companion/object ``init`` blocks and
        property initialisers, or the file-level properties of a ``FileKt``
        (Kotlin). One of them is returned: the one holding the detector's
        line, else the first one naming ``api_type``, else the first static
        block. None when the file declares no static initialisation.
        """
        lines = file_content.split("\n")
        candidates: List[Tuple[str, int, int, int]] = []  # body, start, end, rank
        scopes: List[str] = []
        pending = ""
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            code = _code_text(line)
            stripped = code.strip()
            top = scopes[-1] if scopes else "file"
            kind = ""
            if top == "class" and re.match(r"static\s*(?:\{|$)", stripped):
                kind = "block"
            elif top == "object" and re.match(r"init\s*(?:\{|$)", stripped):
                kind = "block"
            elif top == "class" and re.search(r"\bstatic\b", stripped) and re.search(
                    r"=", stripped) and not re.search(r"\bclass\b|\(.*\)\s*\{?\s*$",
                                                     stripped.split("=", 1)[0]):
                kind = "field"
            elif top in ("object", "file") and re.match(
                    r"(?:(?:private|internal|public|protected|const|lateinit|"
                    r"override|@\w+(?:\([^)]*\))?)\s+)*(?:val|var)\s+\w+", stripped
            ) and ("=" in stripped or " by " in stripped
                   or (idx + 1 < len(lines) and lines[idx + 1].strip().startswith("="))):
                kind = "field"
            if kind:
                if kind == "block":
                    body = self._extract_method_body(file_content, idx, line)
                else:
                    body = self._initialiser_statement(lines, idx)
                if body:
                    end = idx + len(body.split("\n"))
                    candidates.append((body, idx + 1, end, 0 if kind == "block" else 1))
                    # The candidate's braces open and close inside it.
                    idx = end
                    continue
            # Track the scope each brace opens.
            header = stripped if stripped not in ("{", "") else pending
            for char in code:
                if char == "{":
                    if re.search(r"\bobject\b", header):
                        scopes.append("object")
                    elif re.search(r"\b(?:class|interface|enum|record)\b", header):
                        scopes.append("class")
                    else:
                        scopes.append("other")
                elif char == "}" and scopes:
                    scopes.pop()
            if stripped:
                pending = stripped
            idx += 1
        if not candidates:
            return None
        if source_line:
            for body, start, end, _ in candidates:
                if start <= source_line <= end:
                    return body, start, end
        if api_type:
            word = re.compile(rf"\b{re.escape(api_type)}\b")
            for body, start, end, _ in candidates:
                if word.search(body):
                    return body, start, end
        blocks = [c for c in candidates if c[3] == 0]
        if blocks:
            body, start, end, _ = blocks[0]
            return body, start, end
        return None

    @staticmethod
    def _initialiser_statement(lines: List[str], idx: int) -> Optional[str]:
        """A field/property declaration with its initialiser, up to where its
        brackets balance and (Java) its ``;`` or (Kotlin) no continuation follows."""
        depth = 0
        out: List[str] = []
        for pos in range(idx, min(len(lines), idx + 200)):
            code = _code_text(lines[pos])
            out.append(lines[pos])
            depth += sum(code.count(c) for c in "({[") - sum(code.count(c) for c in ")}]")
            if depth > 0:
                continue
            tail = code.rstrip()
            if tail.endswith(";"):
                return "\n".join(out)
            following = lines[pos + 1].strip() if pos + 1 < len(lines) else ""
            if tail.endswith(("=", ",", "+", "-", "*", "&&", "||", "->")) or following.startswith(
                    (".", "?.", "?:", "=", "+", "&&", "||")):
                continue
            if re.search(r"\bstatic\b", lines[idx]) and not tail.endswith(";"):
                # Java statement not finished yet.
                continue
            return "\n".join(out)
        return None

    def _extract_declaration_containing_line(
        self,
        file_content: str,
        source_line: int,
    ) -> Optional[Tuple[str, int, int]]:
        """Map a bytecode lambda/coroutine line back to its enclosing function."""
        lines = file_content.split("\n")
        target_idx = max(0, min(source_line - 1, len(lines) - 1))
        candidates: List[Tuple[str, int, int]] = []

        for start_idx in range(target_idx, -1, -1):
            line = lines[start_idx]
            if not (
                re.search(r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w?.<>]+\.)?\w+\s*\(", line)
                or re.search(
                    r"(?:public|private|protected|static|final|synchronized|"
                    r"abstract|native|\s)+[\w<>\[\], ?]+\s+\w+\s*\(",
                    line,
                )
            ):
                continue
            body = self._extract_method_body(file_content, start_idx, line)
            if not body:
                continue
            end_line = start_idx + len(body.split("\n"))
            candidate = (body, start_idx + 1, end_line)
            candidates.append(candidate)
            if start_idx + 1 <= source_line <= end_line:
                return candidate

        # A bytecode lambda can originate in a Kotlin property initializer.
        for start_idx in range(target_idx, -1, -1):
            line = lines[start_idx]
            if re.search(r"\b(?:val|var)\s+\w+.*(?:=|by\s+lazy\b).*\{", line):
                body = self._extract_method_body(file_content, start_idx, line)
            elif (re.search(r"\b(?:val|var)\s+\w+[^=]*=\s*$", line)
                    and start_idx + 1 < len(lines)
                    and "{" in lines[start_idx + 1]):
                # `val appModule =` with the initializer's block on the next
                # line (password.monitor's Koin module).
                block = self._extract_method_body(
                    file_content, start_idx + 1, lines[start_idx + 1])
                body = f"{line}\n{block}" if block else None
            else:
                continue
            if not body:
                continue
            end_line = start_idx + len(body.split("\n"))
            if start_idx + 1 <= source_line <= end_line:
                return body, start_idx + 1, end_line

        return candidates[0] if candidates else None

    @staticmethod
    def _looks_like_declaration(
        lines: List[str],
        start_idx: int,
        method_name: str,
    ) -> bool:
        signature = " ".join(lines[start_idx:start_idx + 12])
        prefix = signature.split(f"{method_name}(", 1)[0]
        stripped = lines[start_idx].strip()

        if re.search(r"\bfun\s+", prefix):
            return True
        if stripped.startswith((".", "return ", "throw ", "new ")):
            return False
        if "=" in prefix and not re.search(
            r"\b(?:public|private|protected|static|final|abstract|synchronized)\b",
            prefix,
        ):
            return False
        return bool(
            re.search(
                r"(?:^|\s)(?:public|private|protected|static|final|abstract|"
                r"synchronized|native|\w+(?:<[^>]+>)?(?:\[\])?)\s+$",
                prefix,
            )
        )

    def _extract_method_body(
        self,
        content: str,
        start_line_idx: int,
        start_line: str,
    ) -> Optional[str]:
        lines = content.split("\n")
        declaration_idx = start_line_idx

        # Brackets inside string literals and comments are not code: `fun label() = "open ("`
        # would otherwise read as an open parameter list.
        start_code = _code_text(start_line)
        brace_count = start_code.count("{") - start_code.count("}")
        paren_count = start_code.count("(") - start_code.count(")")
        body_lines = [start_line]

        if "{" in start_code and brace_count <= 0:
            return start_line

        # Signatures commonly span several lines in both Java and Kotlin.
        while start_line_idx + 1 < len(lines):
            if brace_count > 0:
                break
            if paren_count <= 0:
                # The parameter list has already closed (possibly on
                # start_line itself) with no opening brace consumed yet.
                # Only keep reading if a brace is about to open on its own
                # line (Allman style); otherwise this is either a Kotlin
                # expression body or a bodyless declaration (interface
                # method, abstract/native method), and the signature read so
                # far is the whole thing -- stop before it merges with
                # whatever follows (a sibling declaration, a comment, the
                # enclosing class's closing brace, ...).
                # A Java throws clause also continues the signature:
                # bitbanana's encodePbkdf2, aegis' createCipher and freeotp's
                # EncryptedKey.decrypt all put it on the next lines, and
                # stopping here recorded the signature alone as the body.
                following = lines[start_line_idx + 1].strip()
                read_so_far = "\n".join(body_lines).rstrip()
                if not (
                    following.startswith("{")
                    or re.match(r"throws\b", following)
                    or (re.search(r"\bthrows\b", read_so_far)
                        and read_so_far.endswith(","))
                ):
                    break
            next_line = lines[start_line_idx + 1]
            body_lines.append(next_line)
            start_line_idx += 1
            next_code = _code_text(next_line)
            brace_count += next_code.count("{") - next_code.count("}")
            paren_count += next_code.count("(") - next_code.count(")")
            if len(body_lines) >= 20 or ";" in next_line:
                break

        if brace_count <= 0:
            # Kotlin expression-bodied function.
            joined = "\n".join(body_lines)
            if re.search(r"\)\s*(?::[^=]+)?\s*=", joined):
                return self._with_expression_continuation(
                    lines, declaration_idx, start_line_idx, joined,
                )
            # Bodyless declaration: a Kotlin interface/abstract member (e.g.
            # a Retrofit service method) or a Java abstract/native method has
            # no braces and no '=' expression body -- its closed signature,
            # optionally followed by a trailing ';', is the whole
            # declaration.
            if paren_count <= 0 and re.search(
                r"\)\s*(?:throws\s+[\w.$<>,\s]+?)?(?::[^={]+)?\s*;?\s*$",
                joined.rstrip(),
            ):
                return joined
            return None

        idx = start_line_idx + 1
        in_string = False
        string_char = None
        in_comment = False

        while idx < len(lines) and brace_count > 0:
            line = lines[idx]

            i = 0
            while i < len(line):
                char = line[i]

                if char in ('"', "'") and not in_comment:
                    if not in_string:
                        in_string = True
                        string_char = char
                    elif char == string_char and (i == 0 or line[i - 1] != "\\"):
                        in_string = False

                if not in_string:
                    if i + 1 < len(line) and line[i:i + 2] == "//":
                        break
                    if i + 1 < len(line) and line[i:i + 2] == "/*":
                        in_comment = True
                    if i + 1 < len(line) and line[i:i + 2] == "*/":
                        in_comment = False
                        i += 1

                if not in_string and not in_comment:
                    if char == "{":
                        brace_count += 1
                    elif char == "}":
                        brace_count -= 1

                i += 1

            body_lines.append(line)

            if brace_count > 0:
                idx += 1

        return "\n".join(body_lines) if body_lines else None

    @staticmethod
    def _with_expression_continuation(
        lines: List[str], declaration_idx: int, last_idx: int, read: str,
    ) -> str:
        """A Kotlin expression body together with the lines it continues on.

        ``fun calculateChildType(): Int =`` followed by an indented
        ``runCatching { ... }`` is one declaration. Returning the first line
        alone handed the analysis a body with no code in it -- wifianalyzer's
        digest discriminator went unrecognised and the generated patch
        changed an algorithm whose result is compared with precomputed
        constants. The expression continues while a bracket it opened is
        still open, or while lines stay indented deeper than the declaration.
        """
        declaration = lines[declaration_idx]
        indent = len(declaration) - len(declaration.lstrip())

        def depth_of(text: str) -> int:
            text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
            text = re.sub(r"'(?:\\.|[^'\\])'", "''", text)
            text = text.split("//", 1)[0]
            return (sum(text.count(c) for c in "([{")
                    - sum(text.count(c) for c in ")]}"))

        depth = sum(depth_of(line) for line in read.split("\n"))
        collected = [read]
        idx = last_idx + 1
        while idx < len(lines) and len(collected) < 400:
            line = lines[idx]
            stripped = line.strip()
            if depth <= 0:
                if not stripped:
                    break
                if len(line) - len(line.lstrip()) <= indent:
                    break
            collected.append(line)
            depth += depth_of(line)
            idx += 1
        return "\n".join(collected)


class SingleCsvMethodExtractor:
    def __init__(
        self,
        output_dir: str,
        methods_output_dir: str,
        skip_cloning: bool = False,
        clone_timeout: int = 1800,
        clone_retries: int = 3,
        project: str = "",
    ):
        self.output_dir = Path(output_dir)
        self.clone_dir = self.output_dir
        self.methods_output_dir = Path(methods_output_dir)
        # Beside cloned_projects, and shared across runs the way clones are:
        # a published APK does not change.
        self.apk_cache_dir = self.output_dir.parent / "apks"

        self.skip_cloning = skip_cloning
        self.clone_timeout = clone_timeout
        self.clone_retries = clone_retries
        self.project = project.strip()
        # (got through, failed), filled in by the loops below so main() can
        # distinguish a partly successful batch from an empty one.
        self.clone_counts: tuple[int, int] = (0, 0)
        self.extraction_counts: tuple[int, int] = (0, 0)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.clone_dir.mkdir(parents=True, exist_ok=True)
        self.methods_output_dir.mkdir(parents=True, exist_ok=True)

        self.logger = self._setup_logging()

        self.rows: List[Dict] = []
        self.apk_urls: Dict[str, str] = {}
        # apk_name -> the revision the analysed artifact was built from, when
        # the input names one. See revision_pin.
        self.apk_revisions: Dict[str, str] = {}
        # apk_name -> the commit the clone is at after the pin was applied
        # (git rev-parse HEAD), recorded beside pinned_revision.
        self.checked_out_revisions: Dict[str, str] = {}
        # apk_name -> why its pinned revision could not be checked out. Its
        # rows are not extracted: the default branch is not the code the
        # finding was reported against.
        self.unavailable_pins: Dict[str, str] = {}
        self.cloned_repos: Dict[str, Path] = {}
        self.results: Dict[str, Dict] = {}
        self.start_time = datetime.now()

    def _setup_logging(self) -> logging.Logger:
        log_dir = self.output_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("SingleCsvMethodExtractor")
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()

        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = logging.FileHandler(
            log_dir / "single_csv_extraction.log",
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        return logger

    def read_csv(self, csv_file: str) -> bool:
        try:
            # utf-8-sig, not utf-8: a CSV exported with a BOM otherwise
            # yields "﻿apk" as the first DictReader key, so every
            # row.get("apk") returns "" and the --project filter below
            # discards the whole file ("0 linhas validas carregadas") long
            # before any repository is cloned. The scripts/ readers already
            # use utf-8-sig; these extraction modules were the two that did
            # not.
            # CSV, JSON Lines or a JSON array, by suffix (cognalys.sources).
            raw_rows = read_rows(csv_file)

            if not raw_rows:
                self.logger.error("CSV vazio")
                return False

            self.rows.clear()
            self.apk_urls.clear()

            invalid_rows = 0
            for row_idx, row in enumerate(raw_rows):
                # Before any value is read: a row whose columns shifted
                # carries a wrong class, method and spec that look valid.
                problems = row_problems(row)
                if problems:
                    invalid_rows += 1
                    self.results[f"row_{row_idx}"] = invalid_row_record(
                        row_idx, row, problems,
                    )
                    self.logger.warning(
                        "linha %s do CSV ignorada (%s): %s",
                        row_idx, INPUT_INVALID, "; ".join(problems),
                    )
                    continue
                repo_url = (row.get("repo_url") or "").strip()
                apk_url = (row.get("apk_url") or "").strip()
                apk = (row.get("apk") or "").strip()
                repo = (row.get("repo") or "").strip()

                if apk:
                    apk_name = apk
                elif apk_url:
                    apk_name = apk_url.split("/")[-1].strip()
                elif repo:
                    apk_name = self._safe_dir_name(repo) + ".apk"
                else:
                    apk_name = f"row_{row_idx}.apk"

                if self.project and apk_name != self.project:
                    continue

                if not repo_url:
                    self.results[f"row_{row_idx}"] = {
                        "status": "skipped",
                        "reason": "missing_repo_url",
                        "row_index": row_idx,
                    }
                    continue

                adapted = dict(row)
                adapted["row_index"] = row_idx
                adapted["apk"] = apk_name
                adapted["repo_url"] = repo_url
                adapted["apk_url"] = apk_url
                adapted["repo"] = repo
                adapted["class"] = row.get("class", "").strip()
                adapted["method"] = row.get("method", "").strip()
                adapted["message"] = row.get("message", row.get("pr_title", "")).strip()
                adapted["unique_msg"] = row.get("unique_msg", row.get("author", "")).strip()

                self.rows.append(adapted)
                self.apk_urls[apk_name] = repo_url
                pinned = revision_pin(row)
                if pinned:
                    previous = self.apk_revisions.get(apk_name)
                    if previous and previous != pinned:
                        self.logger.warning(
                            "%s: rows disagree on the analysed revision (%s "
                            "and %s); keeping the first",
                            apk_name, previous, pinned,
                        )
                    else:
                        self.apk_revisions[apk_name] = pinned

            self.logger.info(f"{len(self.rows)} linhas válidas carregadas")
            if invalid_rows:
                self.logger.warning(
                    f"{invalid_rows} linha(s) com estrutura inválida mantidas "
                    f"no relatório como {INPUT_INVALID}"
                )
            self.logger.info(f"{len(self.apk_urls)} repositórios únicos mapeados")

            return bool(self.rows)

        except Exception as e:
            self.logger.error(f"Erro lendo CSV: {e}")
            return False

    def _record_hosted_upstream(self, repo_dir: Path, url: str) -> None:
        """Keep the hosted repository of a clone made from a local checkout.

        `cognalys propose-prs --repo ../app` clones the user's checkout by
        its path, so the clone's origin is that path and the pull request
        step, which reads the repository's owner and name from the remotes,
        found none (AI Hub: "local checkout (no hosted repository)"). The
        checkout's own upstream or origin is recorded as this clone's
        upstream, which the PR step reads first.
        """
        hosted = hosted_remote_of(url)
        if not hosted:
            return
        remotes = subprocess.run(["git", "-C", str(repo_dir), "remote"], capture_output=True,
                                 text=True, stdin=subprocess.DEVNULL).stdout.split()
        verb = "set-url" if "upstream" in remotes else "add"
        subprocess.run(["git", "-C", str(repo_dir), "remote", verb, "upstream", hosted],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.logger.info("%s: upstream is %s (from the local checkout)", repo_dir.name, hosted)

    def clone_repositories(self) -> bool:
        if self.skip_cloning:
            self.logger.info(f"Reutilizando clones existentes em: {self.clone_dir}")
            self._map_existing_clones()
            return bool(self.cloned_repos)

        success = 0
        failed = 0
        timeout = 0
        total = len(self.apk_urls)

        for idx, (apk_name, url) in enumerate(self.apk_urls.items(), 1):
            repo_dir = self.clone_dir / self._safe_dir_name(apk_name)

            self.logger.info(f"[{idx}/{total}] Cloning {apk_name} from {url}")

            if repo_dir.exists() and (repo_dir / ".git").exists():
                self.logger.info(f"Repository already exists: {repo_dir}")
                # A clone cached from a run before the pin was read, or before
                # the input named one, is on the wrong revision until moved.
                self._apply_revision_pin(repo_dir, apk_name)
                self._ensure_complete_checkout(repo_dir, apk_name)
                self._record_hosted_upstream(repo_dir, url)
                self.cloned_repos[apk_name] = repo_dir
                success += 1
                continue

            if repo_dir.exists():
                shutil.rmtree(repo_dir)

            cloned = False
            timed_out = False
            for attempt in range(1, self.clone_retries + 1):
                try:
                    # core.autocrlf=false pins line endings to exactly what the
                    # remote committed, regardless of the invoking machine's
                    # global git config. Without it, a clone made with
                    # autocrlf=true (the common Windows default) leaves every
                    # text file CRLF-converted on disk while HEAD still has LF,
                    # so later stages that assume a fresh clone is clean (e.g.
                    # worktree_manager's dirty-repo guard) fail on every file.
                    result = subprocess.run(
                        [
                            "git", "-c", "core.autocrlf=false",
                            "clone", "--depth", "1", "--filter=blob:none",
                            url, str(repo_dir),
                        ],
                        timeout=self.clone_timeout,
                        capture_output=True,
                        text=True,
                        stdin=subprocess.DEVNULL,
                        # A stalled transfer fails within a minute instead
                        # of holding the attempt to its timeout; this loop
                        # is the retry (utils.network_git).
                        env=network_git_env(),
                    )
                    if result.returncode == 0:
                        self._apply_revision_pin(repo_dir, apk_name)
                        self._record_hosted_upstream(repo_dir, url)
                        self.cloned_repos[apk_name] = repo_dir
                        success += 1
                        cloned = True
                        self.logger.info(f"Successfully cloned {apk_name}")
                        break
                    error_msg = result.stderr[:500] or result.stdout[:500]
                    self.logger.warning(
                        f"Clone attempt {attempt}/{self.clone_retries} failed for {apk_name}: {error_msg}"
                    )
                except subprocess.TimeoutExpired as exc:
                    timed_out = True
                    self.logger.warning(
                        f"Clone attempt {attempt}/{self.clone_retries} timed out for {apk_name} "
                        f"after {self.clone_timeout}s: {exc.stderr or exc.stdout or ''}"
                    )
                    if self._resume_partial_clone(repo_dir, apk_name):
                        self._apply_revision_pin(repo_dir, apk_name)
                        self.cloned_repos[apk_name] = repo_dir
                        success += 1
                        cloned = True
                        timed_out = False
                        self.logger.info(
                            "Completed the interrupted clone of %s", apk_name
                        )
                        break
                    if (repo_dir / ".git").exists():
                        # Keep what was written: the next attempt resumes it
                        # rather than paying for the same bytes again.
                        if attempt < self.clone_retries:
                            time.sleep(attempt * 2)
                        continue
                except Exception as exc:
                    self.logger.warning(
                        f"Clone attempt {attempt}/{self.clone_retries} failed for {apk_name}: {exc}"
                    )
                self._remove_dir(repo_dir)
                if attempt < self.clone_retries:
                    time.sleep(attempt * 2)

            if not cloned:
                if timed_out:
                    timeout += 1
                else:
                    failed += 1

        self.logger.info(
            f"Cloning results: {success} success, {failed} failed, {timeout} timeout"
        )

        self.clone_counts = (success, failed + timeout)
        return success > 0

    def _checkout_is_incomplete(self, repo_dir: Path) -> bool:
        """Whether a clone that has a .git never finished writing its tree.

        A clone interrupted during "Updating files" keeps a valid .git and a
        valid HEAD while most of the working tree was never written, and the
        reuse check only ever asked whether .git existed. com.vayunmathur's
        clone sat like that across runs -- 14,000 files in the commit, one on
        disk -- so extraction indexed 0 classes, reported the finding as an
        external dependency, and every later run reused the same empty tree.

        The top level of the tree is compared rather than the whole of it
        because this runs on every reused clone. Measured on /mnt/c, `git
        status --porcelain` costs 12-21s on a healthy clone while listing the
        top level costs ~200ms; the failure mode this catches leaves nearly
        the entire tree unwritten, so the top level is enough to see it.

        A checkout interrupted with only deep files missing is not detected
        here. That is a deliberate trade: the check is cheap and runs always,
        and `git checkout --force` is idempotent, so a false positive costs
        one checkout pass rather than any damage.
        """
        listed = subprocess.run(
            ["git", "-C", str(repo_dir), "ls-tree", "--name-only", "HEAD"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if listed.returncode != 0:
            return False
        entries = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        if not entries:
            return False
        missing = [name for name in entries if not (repo_dir / name).exists()]
        if not missing:
            return False
        self.logger.warning(
            "%s has a repository but an incomplete working tree: %d of %d "
            "top-level entries are missing",
            repo_dir.name, len(missing), len(entries),
        )
        return True

    def _ensure_complete_checkout(self, repo_dir: Path, apk_name: str) -> bool:
        """Finish a reused clone whose checkout never completed."""
        if not self._checkout_is_incomplete(repo_dir):
            return True
        return self._resume_partial_clone(repo_dir, apk_name)

    def _resume_partial_clone(self, repo_dir: Path, apk_name: str) -> bool:
        """Finish a clone that timed out with its objects already fetched.

        A `git clone` that runs out of time has almost always run out of it in
        "Updating files", not on the network: the object transfer for a
        --depth 1 --filter=blob:none clone is seconds, and writing the working
        tree across the WSL 9p bridge to /mnt/c is minutes. Measured here,
        element-android checks out at ~37 files/s and Signal-Android managed
        ~11 files/s -- a 3x spread that no single fixed timeout can straddle
        safely.

        Deleting that directory and starting over, which is what happened
        before, threw away everything the attempt had achieved and made every
        retry restart from zero. A repository whose checkout takes longer than
        one timeout could then never be cloned, however many retries were
        allowed: three attempts simply burned three timeouts.

        Resuming instead turns the budget into retries x timeout. Returns
        False when the directory holds no usable repository -- a clone that
        died during the transfer -- so the caller can discard it and retry
        from scratch, which is the right response to that case.
        """
        if not (repo_dir / ".git").exists():
            return False
        head = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        if head.returncode != 0:
            return False
        self.logger.info(
            "Resuming the interrupted checkout of %s instead of recloning it",
            apk_name,
        )
        try:
            completed = subprocess.run(
                [
                    "git", "-C", str(repo_dir), "-c", "core.autocrlf=false",
                    "checkout", "--force", "HEAD",
                ],
                timeout=self.clone_timeout, capture_output=True, text=True,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            self.logger.warning(
                "Checkout of %s is still incomplete after another %ss; the "
                "next attempt will continue from here",
                apk_name, self.clone_timeout,
            )
            return False
        if completed.returncode != 0:
            self.logger.warning(
                "Could not resume the checkout of %s: %s",
                apk_name,
                (completed.stderr or completed.stdout)[:300],
            )
            return False
        return True

    def _apply_revision_pin(self, repo_dir: Path, apk_name: str) -> bool:
        """Move a clone onto the revision the input pinned, if it named one.

        The clone is made with --depth 1, so the pinned commit is not in it
        and has to be fetched on its own. GitHub serves an arbitrary reachable
        SHA this way; a tag is fetched by name. Either leaves the working tree
        detached at exactly the revision the finding was reported against.
        When neither fetch finds it, the history is deepened once (fetch
        --unshallow, or a plain fetch of a full clone) and the revision
        checked out from it.

        The commit the clone is then at is recorded (checked_out_revisions,
        written beside pinned_revision). Returns False when a pin was named
        and the clone is not at it. The caller keeps the clone -- deleting it
        would lose every other finding for the same artifact -- and the APK's
        rows are not extracted (unavailable_pins): reading the default branch
        instead would analyse code other than the code the finding was
        reported against (PLANO_CORRECOES_DEFESA A4).
        """
        revision = self.apk_revisions.get(apk_name, "")
        if not revision:
            self.unavailable_pins.pop(apk_name, None)
            return True
        applied, head = self._checkout_pinned_revision(repo_dir, apk_name, revision)
        if head:
            self.checked_out_revisions[apk_name] = head
        # A commit is compared with what was checked out; a tag fetched by
        # name is FETCH_HEAD itself, and an unreadable HEAD is not a mismatch.
        if applied and (not head or not _COMMIT_PIN.fullmatch(revision)
                        or head.lower().startswith(revision.lower())):
            self.unavailable_pins.pop(apk_name, None)
            return True
        self.unavailable_pins[apk_name] = (
            f"the commit the APK was built from ({revision}) could not be checked out"
            + (f"; the clone is at {head[:12]}" if head else ""))
        self.logger.error(
            "Could not check out the pinned revision %s for %s; its findings are "
            "not analysed, since the default branch is not the revision they were "
            "reported against", revision, apk_name,
        )
        return False

    def _checkout_pinned_revision(self, repo_dir: Path, apk_name: str,
                                  revision: str) -> Tuple[bool, str]:
        """(whether the clone was moved onto ``revision``, its HEAD then or "")."""

        def git(*arguments: str, timeout: int | None = None):
            command = ["git", "-C", str(repo_dir), *arguments]
            if network_subcommand(command):
                # Bounded and retried once (utils.network_git); a final
                # timeout still raises, as below.
                return run_network_git(
                    command, timeout or self.clone_timeout,
                    capture_output=True, text=True,
                )
            return subprocess.run(
                command,
                capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=timeout or self.clone_timeout,
            )

        def current_head() -> str:
            head = git("rev-parse", "HEAD")
            return str(head.stdout or "").strip() if head.returncode == 0 else ""

        head = git("rev-parse", "HEAD")
        if head.returncode == 0 and head.stdout.strip().startswith(revision):
            self.logger.info(
                "%s is already at the pinned revision %s", apk_name, revision,
            )
            return True, head.stdout.strip()

        for fetch_arguments in (
            ("fetch", "--depth", "1", "origin", revision),
            ("fetch", "--depth", "1", "origin", "tag", revision),
        ):
            try:
                fetched = git(*fetch_arguments)
            except subprocess.TimeoutExpired:
                self.logger.error(
                    "Timed out fetching pinned revision %s for %s",
                    revision, apk_name,
                )
                return False, current_head()
            if fetched.returncode != 0:
                continue
            checked_out = git("checkout", "--detach", "FETCH_HEAD")
            if checked_out.returncode == 0:
                self.logger.info(
                    "%s checked out at the pinned revision %s",
                    apk_name, revision,
                )
                return True, current_head()
            self.logger.warning(
                "Fetched %s for %s but could not check it out: %s",
                revision, apk_name,
                (checked_out.stderr or checked_out.stdout)[:300],
            )
            return False, current_head()

        # A server that does not serve an arbitrary commit by itself still
        # has it in the history: deepen once and check it out from there.
        deepen = (("fetch", "--unshallow", "origin")
                  if (repo_dir / ".git" / "shallow").is_file() else ("fetch", "origin"))
        try:
            fetched = git(*deepen)
        except subprocess.TimeoutExpired:
            self.logger.error("Timed out deepening %s to find %s", apk_name, revision)
            return False, current_head()
        if fetched.returncode == 0:
            checked_out = git("checkout", "--detach", revision)
            if checked_out.returncode == 0:
                self.logger.info(
                    "%s checked out at the pinned revision %s from its full history",
                    apk_name, revision,
                )
                return True, current_head()
        return False, current_head()

    def _map_existing_clones(self) -> None:
        for apk_name in self.apk_urls:
            repo_dir = self.clone_dir / self._safe_dir_name(apk_name)

            if repo_dir.exists() and (repo_dir / ".git").exists():
                self._apply_revision_pin(repo_dir, apk_name)
                self._ensure_complete_checkout(repo_dir, apk_name)
                self.cloned_repos[apk_name] = repo_dir
                self.logger.info(f"Found clone for {apk_name}: {repo_dir}")
            else:
                self.logger.warning(f"Missing clone for {apk_name}: {repo_dir}")

    def extract_methods(self) -> bool:
        if not self.cloned_repos:
            self.logger.error("No cloned repositories available")
            return False

        success_count = 0
        failed_count = 0
        skipped_count = 0

        rows_by_apk: Dict[str, List[Dict]] = {}

        for row in self.rows:
            rows_by_apk.setdefault(row["apk"], []).append(row)

        for apk_name, apk_rows in rows_by_apk.items():
            repo_path = self.cloned_repos.get(apk_name)

            if repo_path and apk_name in self.unavailable_pins:
                # Not the default branch instead: the finding is about the
                # code of the pinned commit (_apply_revision_pin).
                for row in apk_rows:
                    key = f"row_{row['row_index']}"
                    self.results[key] = {
                        "status": "failed",
                        "apk": apk_name,
                        "repo_url": row.get("repo_url", ""),
                        "reason": "pinned_revision_unavailable",
                        "json_output": self._write_unextracted_record(
                            row, apk_name, repo_path, "pinned_revision_unavailable",
                        ),
                    }
                    failed_count += 1
                continue

            if not repo_path:
                for row in apk_rows:
                    key = f"row_{row['row_index']}"
                    self.results[key] = {
                        "status": "failed",
                        "apk": apk_name,
                        "repo_url": row.get("repo_url", ""),
                        "reason": "repository_not_cloned",
                        "json_output": self._write_unextracted_record(
                            row, apk_name, None, "repository_not_cloned",
                        ),
                    }
                    failed_count += 1
                continue

            populated = populate_empty_submodules(repo_path, self.logger)
            indexer = RepositoryIndexer(repo_path, self.logger)
            if populated:
                # The index is keyed by the superproject's commit, which a
                # submodule checkout does not change: drop the stale one.
                cached_index = indexer._index_cache_path()
                if cached_index and cached_index.is_file():
                    cached_index.unlink()
            indexer.build_index()

            extractor = MethodExtractor(self.logger)

            apk_output_dir = self.methods_output_dir / self._safe_dir_name(apk_name)
            apk_output_dir.mkdir(parents=True, exist_ok=True)

            for row in apk_rows:
                row_index = row["row_index"]
                key = f"row_{row_index}"

                class_name = row.get("class", "").strip()
                method_name = row.get("method", "").strip()
                lookup_class, source_method, source_line, mapping = (
                    self._normalize_bytecode_target(class_name, method_name)
                )

                if not class_name or not method_name:
                    self.results[key] = {
                        "status": "skipped",
                        "apk": apk_name,
                        "repo_url": row.get("repo_url", ""),
                        "reason": "missing_class_or_method",
                        "json_output": self._write_unextracted_record(
                            row, apk_name, repo_path, "missing_class_or_method",
                        ),
                    }
                    skipped_count += 1
                    continue

                file_path, class_resolution = indexer.resolve_class(lookup_class)
                if file_path is None:
                    # The class is not in the checkout under any name the
                    # index accepts. Before that becomes a dead finding, ask
                    # git whether it was there when this APK was built: it
                    # may have moved to a module the package-root rule
                    # rejects, or it may have been deleted upstream, which
                    # is a stale finding rather than a pipeline gap. See
                    # extraction/release_ref.py.
                    history = historical_class_lookup(
                        repo_path, apk_name, lookup_class
                    )
                    class_resolution = {**class_resolution, "history": history}
                    if history["status"] == "relocated":
                        relocated = repo_path / history["current_path"]
                        if relocated.is_file():
                            file_path = relocated
                            class_resolution["method"] = "release_ref_relocation"
                            self.logger.warning(
                                "%s: %s is not in the checkout under that "
                                "name; at %s it was %s and that file is now "
                                "%s",
                                apk_name, lookup_class,
                                history["release_ref"].get("tag", ""),
                                history["historical_path"],
                                history["current_path"],
                            )
                if class_resolution.get("method") == "simple_name_fallback":
                    self.logger.warning(
                        "%s: %s is not in the checkout under that name; "
                        "reading its namesake %s instead",
                        apk_name, lookup_class,
                        class_resolution.get("resolved_class"),
                    )

                if not file_path:
                    # A library frame that implements the governed API took
                    # its value from the application: analyse the call that
                    # passed it. See extraction/app_caller_attribution.py.
                    attribution = attribute_to_application(
                        repo_path, row,
                        load_specification(SPECIFICATIONS_DIR, row.get("spec", "")),
                        lambda path, line: _declaration_bounds(extractor, path, line),
                    )
                    if attribution["status"] == "attributed":
                        site = attribution["call_site"]
                        file_path = repo_path / site["path"]
                        source_line = site["line"]
                        mapping = "app_caller_attribution"
                        class_resolution = {
                            **class_resolution,
                            "method": "app_caller_attribution",
                            "app_caller_attribution": attribution,
                        }
                        self.logger.warning(
                            "%s: %s.%s is library code implementing the "
                            "governed API; analysing the application call "
                            "that passed %r at %s:%s",
                            apk_name, class_name, method_name,
                            attribution.get("value"), site["path"], site["line"],
                        )
                    elif attribution["status"] != "not_applicable":
                        class_resolution = {
                            **class_resolution,
                            "app_caller_attribution": attribution,
                        }

                if not file_path:
                    # Collected for every class not found, as before: a
                    # dependency finding can be reported at an application
                    # class (dep_analyzer's OutdatedDependencySpec names
                    # com.duckduckgo.app.global.api.ApiRequest for an OkHttp
                    # version), and its remedy is still in the build files.
                    dependency_context = collect_dependency_context(
                        repo_path, lookup_class, apk_name,
                        method_name=method_name,
                    )
                    # A class in the application's own namespace is not a
                    # dependency, whatever the dependency matching made of
                    # it. org.mifos.mobilebanking's SelfServiceOkHttpClient,
                    # com.vayunmathur.passwords' CableEid and
                    # com.mde.potdroid.helpers.f were all recorded as
                    # dependency candidates, when the checkout was another
                    # revision or the name was obfuscated.
                    app_package = application_package(
                        apk_name, row.get("pacote", "")
                    )
                    namespaces = [app_package, *repository_namespaces(repo_path)]
                    own_class = class_in_application_namespaces(
                        lookup_class, namespaces,
                        owned_by_dependency=any(
                            group_owns_class(str(item.get("group", "")), lookup_class)
                            for item in dependency_context.get("declared_dependencies", [])
                            if isinstance(item, dict)
                        ),
                    )
                    application_namespace = {
                        "package": app_package,
                        "declared_namespaces": sorted({name for name in namespaces if name})[:40],
                        "reported_class_in_namespace": own_class,
                        "name_looks_obfuscated": bool(own_class) and name_looks_obfuscated(
                            lookup_class, method_name
                        ),
                    }
                    # A class the release tag has and the checkout does not
                    # is a finding that has aged out of this repository, not
                    # one the pipeline failed to analyse. Recording them
                    # under the same status made 16 stale findings read as
                    # extraction failures in the ledger.
                    history = class_resolution.get("history") or {}
                    if own_class and history.get("status") == "removed_since_release":
                        extraction_status = "application_class_removed_since_release"
                    elif own_class and indexer.unreadable_files:
                        # Not established as absent: the index skipped files.
                        extraction_status = "application_class_index_incomplete"
                        class_resolution["index_unreadable_files"] = (
                            indexer.unreadable_files[:50]
                        )
                    elif own_class:
                        extraction_status = "application_class_not_in_checkout"
                    else:
                        extraction_status = "external_dependency_candidate"
                    # The raw bytecode names, not the source-normalised
                    # lookup: the dex is compiled code.
                    apk_evidence = (
                        collect_apk_evidence(
                            apk_name, row.get("apk_url", ""), class_name,
                            method_name, self.apk_cache_dir,
                        )
                        if apk_evidence_enabled()
                        else {"status": "not_collected",
                              "reason": "TCC_SKIP_APK_EVIDENCE is set"}
                    )
                    repository_guidance = discover_repository_guidance(repo_path)
                    json_data = {
                        "extraction_metadata": {
                            "csv_index": row_index,
                            "apk_name": apk_name,
                            "repository_url": row.get("repo_url", ""),
                            "repository_path": str(repo_path),
                            "extraction_status": extraction_status,
                            "class_resolution": class_resolution,
                            "application_namespace": application_namespace,
                            "extraction_timestamp": datetime.now().isoformat(),
                            "bytecode_mapping": mapping,
                            "pinned_revision": self.apk_revisions.get(
                                apk_name, ""
                            ),
                            "checked_out_revision": self.checked_out_revisions.get(
                                apk_name, ""
                            ),
                        },
                        "error": {
                            "csv_class": class_name,
                            "csv_method": method_name,
                            "error_message": row.get("message", ""),
                            "unique_error_id": row.get("unique_msg", ""),
                            "spec": row.get("spec", ""),
                            "tool": row.get("tool", ""),
                            "detector_context": detector_context(row),
                        },
                        "class_info": {
                            "full_name": class_name,
                            "source_file_path": "",
                            "relative_method_file": "",
                        },
                        "method_info": {
                            "name": method_name,
                            "source_name": source_method,
                            "source_line_hint": source_line,
                        },
                        "repository_guidance": repository_guidance,
                        "branch_policy": infer_branch_policy(repository_guidance),
                        "dependency_context": dependency_context,
                        "apk_evidence": apk_evidence,
                        # What the detector message names, checked against
                        # this checkout. Empty for a finding whose message
                        # names no dependency or deprecation.
                        "dependency_finding": collect_dependency_finding(
                            repo_path, row.get("message", ""),
                        ),
                        "source_code": {
                            "method_body": "",
                            "imports": "",
                            "surrounding_context": "",
                            "call_sites": "",
                            "implicit_receiver_calls": [],
                            "method_parameter_resolution": [],
                        },
                    }
                    json_filename = (
                        f"{row_index}_{self._safe_dir_name(method_name)}.json"
                    )
                    json_path = apk_output_dir / json_filename
                    json_path.write_text(
                        json.dumps(json_data, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    self.results[key] = {
                        "status": extraction_status,
                        "apk_evidence_status": apk_evidence.get("status"),
                        "apk": apk_name,
                        "class": class_name,
                        "method": method_name,
                        "lookup_class": lookup_class,
                        "reason": "class_not_found_in_repository",
                        "dependency_status": dependency_context.get("status"),
                        "json_output": str(json_path),
                    }
                    success_count += 1
                    continue

                content = indexer.get_file_content(file_path)

                if not content:
                    self.results[key] = {
                        "status": "failed",
                        "apk": apk_name,
                        "class": class_name,
                        "method": method_name,
                        "reason": "source_file_unreadable",
                        "json_output": self._write_unextracted_record(
                            row, apk_name, repo_path, "source_file_unreadable",
                        ),
                    }
                    failed_count += 1
                    continue

                if method_name == "<clinit>":
                    extracted = extractor.extract_static_initializer(
                        content, governed_type(str(row.get("spec", "") or "")),
                        reported_source_line(row, file_path),
                    ) or extractor.extract_class_initializer(content)
                else:
                    if method_name == "<init>":
                        source_method = lookup_class.split("$")[-1].split(".")[-1]
                        if source_method.endswith("Kt"):
                            source_method = file_path.stem
                    extracted = None
                    if source_line is None:
                        # A name declared more than once is an overload set,
                        # and the first declaration is not necessarily the
                        # one that ran. FairEmail's Helper declares
                        # getFingerprint(Context), which only forwards "SHA1",
                        # above getFingerprint(Context, String), where the
                        # detector recorded MessageDigest.getInstance at
                        # line 3307; the analysis got the first and said the
                        # reported call was not in its packet
                        # (exp_final_20261019). The declaration holding the
                        # reported line is taken when it carries the method's
                        # own name.
                        extracted, source_line = reported_overload(
                            extractor, content, file_path, row, source_method,
                        )
                    if not extracted:
                        extracted = extractor.extract_method_flexible(
                            content,
                            source_method,
                            source_line=source_line,
                        )
                    if not extracted and method_name == "<init>":
                        extracted = extractor.extract_class_initializer(content)
                        if extracted:
                            source_method = "<source_initializer>"
                            mapping = "implicit_constructor_initializer"

                if not extracted and method_name != "<clinit>":
                    # A compiler-generated name (password.monitor's Koin
                    # `appModule$lambda$0$3`) declares nothing in the source,
                    # but the detector recorded the line it ran: the
                    # declaration that contains that line is the code.
                    reported_line = reported_source_line(row, file_path)
                    if reported_line and _declaration_bounds(
                            extractor, file_path, reported_line):
                        extracted = extractor._extract_declaration_containing_line(
                            content, reported_line,
                        )
                        source_method = (
                            declared_name(extracted[0]) or source_method
                        )
                        source_line = reported_line
                        mapping = "reported_source_line"

                if not extracted and method_name not in ("<clinit>", "<init>"):
                    located = locate_unnamed_method(
                        extractor, file_path, content, source_method, method_name,
                        str(row.get("spec", "")),
                        outer_class=lookup_class.split(".")[-1].split("$")[0],
                        value=reported_value(row),
                    )
                    if located:
                        extracted, source_method, file_path, content, mapping = located

                if not extracted:
                    # A dependency or deprecation finding is about what its
                    # message names, not about the method a tool attached it
                    # to. NewPipe's row names NewPipeSettings.init, which does
                    # not exist (the method is initSettings), and the file
                    # already imports androidx.preference.PreferenceManager
                    # instead of the deprecated one; failing the row aborted
                    # the run without ever checking that. Record it with the
                    # grounding instead, and let the analysis stage close it.
                    #
                    # Recorded whether or not the message names a dependency.
                    # Without one the row used to fail here and leave no
                    # trace past this script: termux's TerminalSession and
                    # AnySoftKeyboard's KeyboardThemeFactory exist and declare
                    # no initialize or loadTheme, and run_20260914_122712's
                    # reports never mentioned either. The analysis stage now
                    # closes such a record, with that reason, before any
                    # model call.
                    relative_source = self._safe_relative_path(file_path, repo_path)
                    dependency_finding = collect_dependency_finding(
                        repo_path, row.get("message", ""), str(relative_source),
                    )
                    json_path = self._write_method_not_found_record(
                        apk_output_dir, row, apk_name, repo_path, class_name,
                        method_name, source_method, source_line, mapping,
                        str(relative_source), dependency_finding,
                        class_resolution=class_resolution,
                    )
                    self.results[key] = {
                        "status": "method_not_found_recorded",
                        "apk": apk_name,
                        "class": class_name,
                        "method": method_name,
                        "source_file": str(file_path),
                        "reason": "method_not_found",
                        "dependency_status": dependency_finding.get("status"),
                        "json_output": str(json_path),
                    }
                    success_count += 1
                    continue

                method_body, start_line, end_line = extracted
                if mapping == "app_caller_attribution":
                    # The reviewed method is the application's caller, not
                    # the reported API method: its callers are the ones to
                    # collect below.
                    source_method = declared_name(method_body) or source_method
                implicit_receiver_calls = (
                    MethodExtractor.extract_kotlin_implicit_receiver_calls(method_body)
                    if file_path.suffix == ".kt" else []
                )

                content_lines = content.splitlines()
                context_start = max(0, start_line - 21)
                context_end = min(len(content_lines), end_line + 20)
                surrounding_context = "\n".join(
                    f"{line_no + 1}: {content_lines[line_no]}"
                    for line_no in range(context_start, context_end)
                )
                imports = "\n".join(
                    line.strip()
                    for line in content_lines
                    if re.match(r"\s*import\s+", line)
                )
                call_site_blocks = []
                call_pattern = re.compile(rf"\b{re.escape(source_method)}\s*\(")
                for line_no, line in enumerate(content_lines):
                    current_line = line_no + 1
                    if start_line <= current_line <= end_line:
                        continue
                    if not call_pattern.search(line):
                        continue
                    # Include argument construction and nearby assignments. A
                    # one-line window showed calls but hid where cryptographic
                    # keys, IVs, and algorithms came from.
                    block_start = max(0, line_no - 8)
                    block_end = min(len(content_lines), line_no + 9)
                    call_site_blocks.append(
                        "\n".join(
                            f"{index + 1}: {content_lines[index]}"
                            for index in range(block_start, block_end)
                        )
                    )
                    if len(call_site_blocks) >= 8:
                        break
                call_sites = "\n---\n".join(call_site_blocks)
                parameter_resolution = MethodExtractor.resolve_method_parameters(
                    method_body,
                    source_method,
                    call_sites,
                    str(row.get("message", "")),
                )

                relative_source = self._safe_relative_path(file_path, repo_path)
                repository_guidance = discover_repository_guidance(repo_path)
                branch_policy = infer_branch_policy(repository_guidance)
                # The raw bytecode names, as for apk_evidence: the dex is
                # compiled code.
                apk_bytecode = (
                    collect_method_bytecode_bindings(
                        apk_name, row.get("apk_url", ""), class_name,
                        method_name, self.apk_cache_dir,
                    )
                    if apk_evidence_enabled()
                    else {"status": "not_collected",
                          "reason": "TCC_SKIP_APK_EVIDENCE is set"}
                )
                # The detector's own line, when it lies in the reviewed
                # declaration: whether it is still on the contribution base
                # is what says the code changed upstream.
                reported_line = reported_source_line(row, file_path)
                reported_text = (
                    content_lines[reported_line - 1]
                    if reported_line and start_line <= reported_line <= end_line
                    and reported_line <= len(content_lines) else ""
                )
                json_data = {
                    "extraction_metadata": {
                        "csv_index": row_index,
                        "apk_name": apk_name,
                        "repository_url": row.get("repo_url", ""),
                        "repository_path": str(repo_path),
                        "extraction_status": "success",
                        "extraction_timestamp": datetime.now().isoformat(),
                        "pinned_revision": self.apk_revisions.get(apk_name, ""),
                        "checked_out_revision": self.checked_out_revisions.get(apk_name, ""),
                        "bytecode_mapping": mapping,
                        "class_resolution": class_resolution,
                        # Whether this file still exists where a pull request
                        # would land. Read-only and local: no fetch, so it
                        # costs nothing per finding, and an unresolvable base
                        # leaves target_present None rather than False.
                        "contribution_base": contribution_base_status(
                            repo_path,
                            str(relative_source),
                            branch_policy=branch_policy,
                            method_body=method_body,
                            statement=reported_text,
                        ),
                    },
                    "error": {
                        "csv_class": class_name,
                        "csv_method": method_name,
                        "error_message": row.get("message", ""),
                        "unique_error_id": row.get("unique_msg", ""),
                        "spec": row.get("spec", ""),
                        "tool": row.get("tool", ""),
                        "detector_context": detector_context(row),
                    },
                    "class_info": {
                        "full_name": class_name,
                        "source_file_path": relative_source,
                        "relative_method_file": str(Path(apk_name) / relative_source),
                    },
                    "method_info": {
                        "name": method_name,
                        "source_name": source_method,
                        "source_line_hint": source_line,
                    },
                    "repository_guidance": repository_guidance,
                    "branch_policy": branch_policy,
                    # A dependency or deprecation finding is about what the
                    # message names, not about the reported method's code.
                    "dependency_finding": collect_dependency_finding(
                        repo_path, row.get("message", ""), str(relative_source),
                    ),
                    "apk_bytecode": apk_bytecode,
                    "source_code": {
                        "method_body": method_body,
                        "start_line": start_line,
                        "end_line": end_line,
                        "imports": imports,
                        "surrounding_context": surrounding_context,
                        "call_sites": call_sites,
                        "implicit_receiver_calls": implicit_receiver_calls,
                        "method_parameter_resolution": parameter_resolution,
                    },
                }

                json_filename = f"{row_index}_{self._safe_dir_name(method_name)}.json"
                json_path = apk_output_dir / json_filename

                try:
                    with open(json_path, "w", encoding="utf-8") as f:
                        json.dump(json_data, f, indent=2, ensure_ascii=False)

                    self.results[key] = {
                        "status": "success",
                        "apk": apk_name,
                        "class": class_name,
                        "method": method_name,
                        "json_output": str(json_path),
                    }

                    success_count += 1

                except Exception as e:
                    self.results[key] = {
                        "status": "failed",
                        "apk": apk_name,
                        "class": class_name,
                        "method": method_name,
                        "reason": f"json_write_error: {e}",
                    }
                    failed_count += 1

        self.logger.info(
            f"Extraction complete: "
            f"{success_count} success, "
            f"{failed_count} failed, "
            f"{skipped_count} skipped"
        )

        # Recorded so main() can tell a batch where one finding failed apart
        # from one where none were extracted at all. A skipped finding counts
        # as gotten through: it was excluded deliberately and its project can
        # still go on to the later steps.
        self.extraction_counts = (success_count + skipped_count, failed_count)
        return failed_count == 0 and (success_count > 0 or skipped_count > 0)

    def write_tracking_json(self) -> None:
        duration = (datetime.now() - self.start_time).total_seconds()

        success = sum(1 for r in self.results.values() if r.get("status") == "success")
        failed = sum(1 for r in self.results.values() if r.get("status") == "failed")
        skipped = sum(1 for r in self.results.values() if r.get("status") == "skipped")

        tracking_data = {
            "summary": {
                "total_rows": len(self.rows),
                "repositories_configured": len(self.apk_urls),
                "repositories_cloned": len(self.cloned_repos),
                "methods_extracted": success,
                "failed": failed,
                "skipped": skipped,
                "output_dir": str(self.output_dir),
                "clone_dir": str(self.clone_dir),
                "methods_output_dir": str(self.methods_output_dir),
                "timestamp": datetime.now().isoformat(),
                "duration_seconds": round(duration, 2),
            },
            "results": self.results,
        }

        tracking_path = self.output_dir / "extraction_tracking.json"

        with open(tracking_path, "w", encoding="utf-8") as f:
            json.dump(tracking_data, f, indent=2, ensure_ascii=False)

        self.logger.info(f"Tracking JSON written to {tracking_path}")

    def write_summary(self) -> None:
        summary_path = self.output_dir / "logs" / "summary_tracked.txt"

        success = sum(1 for r in self.results.values() if r.get("status") == "success")
        failed = sum(1 for r in self.results.values() if r.get("status") == "failed")
        skipped = sum(1 for r in self.results.values() if r.get("status") == "skipped")

        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("=" * 70 + "\n")
            f.write("SINGLE CSV METHOD EXTRACTION SUMMARY\n")
            f.write("=" * 70 + "\n\n")
            f.write(f"Output dir: {self.output_dir}\n")
            f.write(f"Clone dir: {self.clone_dir}\n")
            f.write(f"Methods output dir: {self.methods_output_dir}\n\n")
            f.write(f"CSV rows loaded: {len(self.rows)}\n")
            f.write(f"Repositories configured: {len(self.apk_urls)}\n")
            f.write(f"Repositories cloned/mapped: {len(self.cloned_repos)}\n\n")
            f.write(f"Methods extracted: {success}\n")
            f.write(f"Failed: {failed}\n")
            f.write(f"Skipped: {skipped}\n")

        self.logger.info(f"Summary written to {summary_path}")

    def reset_repositories(self) -> bool:
        if not self.clone_dir.exists():
            self.logger.warning(f"No clone directory found: {self.clone_dir}")
            return False

        repos = [p for p in self.clone_dir.iterdir() if (p / ".git").exists()]

        self.logger.info(f"Resetting {len(repos)} repositories from {self.clone_dir}")

        success = 0
        failed = 0

        for repo_dir in repos:
            try:
                result_reset = subprocess.run(
                    ["git", "-c", "core.autocrlf=false", "reset", "--hard"],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL,
                )

                result_clean = subprocess.run(
                    ["git", "clean", "-fd"],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL,
                )

                if result_reset.returncode == 0 and result_clean.returncode == 0:
                    success += 1
                    self.logger.info(f"Successfully reset {repo_dir.name}")
                else:
                    failed += 1
                    self.logger.warning(
                        f"Failed resetting {repo_dir.name}: "
                        f"{result_reset.stderr or result_clean.stderr}"
                    )

            except Exception as e:
                failed += 1
                self.logger.error(f"Reset error for {repo_dir.name}: {e}")

        self.logger.info(f"Reset complete: {success} success, {failed} failed")

        return success > 0

    def run(self, csv_file: str, clone_only: bool = False) -> bool:
        self.logger.info("Starting single CSV workflow")
        self.logger.info(f"Output dir: {self.output_dir}")
        self.logger.info(f"Clone dir: {self.clone_dir}")
        self.logger.info(f"Methods output dir: {self.methods_output_dir}")

        if not self.read_csv(csv_file):
            return False

        clones_available = self.clone_repositories()
        if clone_only:
            return clones_available
        extraction_succeeded = self.extract_methods()
        self.write_tracking_json()
        self.write_summary()

        return extraction_succeeded

    @staticmethod
    def _safe_dir_name(name: str) -> str:
        name = name.strip()
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        return name or "unknown"

    @staticmethod
    def _normalize_bytecode_target(
        class_name: str,
        method_name: str,
    ) -> Tuple[str, str, Optional[int], str]:
        """Translate common JVM synthetic names back to source-level targets."""
        lookup_class = class_name
        source_method = method_name
        source_line = None
        mapping = "direct"

        # Soot-style lambda target:
        # pkg.Class.property_delegate$lambda$0(File.kt:31)
        trace = re.match(
            r"^(?P<owner>[\w.$]+)\.(?P<symbol>[^()]+)"
            r"\((?P<file>[^():]+):(?P<line>\d+)\)$",
            class_name,
        )
        if not trace:
            trace = re.match(
                r"^(?P<owner>[\w.$]+)\.(?P<symbol>[^()]+)"
                r"\((?P<file>[^():]+):(?P<line>\d+)\)$",
                method_name,
            )
        if trace:
            lookup_class = trace.group("owner")
            source_line = int(trace.group("line"))
            source_method = trace.group("symbol")
            mapping = "source_line"

        # Kotlin suspend functions compile to Class$function$N.invokeSuspend.
        if method_name == "invokeSuspend":
            parts = lookup_class.split("$")
            candidates = [
                part
                for part in parts[1:]
                if part not in {"Companion", "DefaultImpls"}
                and not part.isdigit()
                and not part.startswith("lambda")
            ]
            if candidates:
                source_method = candidates[-1]
                lookup_class = parts[0]
                mapping = "kotlin_coroutine"

        # A function taking or returning an inline value class is compiled to
        # name-<7-char hash> (Password Store's calculateTotp-Zyo9ksc); the
        # source declares the plain name.
        mangled = re.fullmatch(r"(?P<name>[A-Za-z_]\w*)-[A-Za-z0-9_]{7}", source_method)
        if mangled:
            source_method = mangled.group("name")
            mapping = "kotlin_value_class_mangling" if mapping == "direct" else mapping
        # An internal function carries its module (discoverySslContext$app)
        # and a default-arguments bridge $default; the source has neither.
        unsuffixed = kotlin_member_name(source_method)
        if unsuffixed != source_method:
            source_method = unsuffixed
            mapping = "kotlin_member_suffix" if mapping == "direct" else mapping

        # D8's desugared lambdas (Class$$ExternalSyntheticLambda1) live in the
        # outer class, like the javac and kotlinc ones below.
        if "$$ExternalSynthetic" in lookup_class:
            lookup_class = lookup_class.split("$$", 1)[0]
            mapping = "outer_class" if mapping == "direct" else mapping

        # Anonymous/lambda suffixes still live in the outer source class.
        if "$lambda$" in lookup_class or re.search(r"\$\d+$", lookup_class):
            lookup_class = lookup_class.split("$")[0]
            mapping = "outer_class" if mapping == "direct" else mapping

        return lookup_class, source_method, source_line, mapping

    @staticmethod
    def _remove_dir(path: Path) -> None:
        try:
            if path.exists():
                shutil.rmtree(path)
        except Exception:
            pass

    def _write_method_not_found_record(
        self, apk_output_dir: Path, row: Dict, apk_name: str, repo_path: Path,
        class_name: str, method_name: str, source_method: str,
        source_line: Optional[int], mapping: str, relative_source: str,
        dependency_finding: Dict, class_resolution: Optional[Dict] = None,
    ) -> Path:
        """The record for a finding whose reported method is absent.

        Carries the class file and the message's grounding, with no method
        body, so the analysis stage can close a finding this checkout has
        already resolved, analyse a dependency finding it still has, and
        close one that names no dependency with its reason.
        """
        repository_guidance = discover_repository_guidance(repo_path)
        json_data = {
            "extraction_metadata": {
                "csv_index": row["row_index"],
                "apk_name": apk_name,
                "repository_url": row.get("repo_url", ""),
                "repository_path": str(repo_path),
                "extraction_status": "method_not_found",
                "extraction_timestamp": datetime.now().isoformat(),
                "pinned_revision": self.apk_revisions.get(apk_name, ""),
                "checked_out_revision": self.checked_out_revisions.get(apk_name, ""),
                "bytecode_mapping": mapping,
                "class_resolution": class_resolution or {},
            },
            "error": {
                "csv_class": class_name,
                "csv_method": method_name,
                "error_message": row.get("message", ""),
                "unique_error_id": row.get("unique_msg", ""),
                "spec": row.get("spec", ""),
                "tool": row.get("tool", ""),
                "detector_context": detector_context(row),
            },
            "class_info": {
                "full_name": class_name,
                "source_file_path": relative_source,
                "relative_method_file": str(Path(apk_name) / relative_source),
            },
            "method_info": {
                "name": method_name,
                "source_name": source_method,
                "source_line_hint": source_line,
            },
            "repository_guidance": repository_guidance,
            "branch_policy": infer_branch_policy(repository_guidance),
            "dependency_finding": dependency_finding,
            "source_code": {
                "method_body": "",
                "imports": "",
                "surrounding_context": "",
                "call_sites": "",
                "implicit_receiver_calls": [],
                "method_parameter_resolution": [],
            },
        }
        apk_output_dir.mkdir(parents=True, exist_ok=True)
        json_path = apk_output_dir / (
            f"{row['row_index']}_{self._safe_dir_name(method_name)}.json"
        )
        json_path.write_text(
            json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        return json_path

    def _write_unextracted_record(
        self, row: Dict, apk_name: str, repo_path: Optional[Path],
        extraction_status: str,
    ) -> str:
        """A record for an input row extraction could not turn into source.

        Rows reached the analysis stage only when extraction produced a
        record for them. A repository that would not clone or a source file
        that could not be read stayed in this script's own results, and
        nowhere else: run_20260914_122712 read 15 findings while its
        analysis summary, tracking and retrospective accounted for 10. A
        record with no source goes through the analysis stage's deterministic
        checks and is closed there, with its reason and before any model
        call, so the run's own reports account for every input row.

        Returns the path written, or "" when even that failed.
        """
        method_name = str(row.get("method", "")).strip()
        class_name = str(row.get("class", "")).strip()
        json_data = {
            "extraction_metadata": {
                "csv_index": row["row_index"],
                "apk_name": apk_name,
                "repository_url": row.get("repo_url", ""),
                "repository_path": str(repo_path) if repo_path else "",
                "extraction_status": extraction_status,
                "extraction_timestamp": datetime.now().isoformat(),
                "pinned_revision": self.apk_revisions.get(apk_name, ""),
                "checked_out_revision": self.checked_out_revisions.get(apk_name, ""),
            },
            "error": {
                "csv_class": class_name,
                "csv_method": method_name,
                "error_message": row.get("message", ""),
                "unique_error_id": row.get("unique_msg", ""),
                "spec": row.get("spec", ""),
                "tool": row.get("tool", ""),
                "detector_context": detector_context(row),
            },
            "class_info": {
                "full_name": class_name,
                "source_file_path": "",
                "relative_method_file": "",
            },
            "method_info": {
                "name": method_name,
                "source_name": method_name,
                "source_line_hint": None,
            },
            "source_code": {
                "method_body": "",
                "imports": "",
                "surrounding_context": "",
                "call_sites": "",
                "implicit_receiver_calls": [],
                "method_parameter_resolution": [],
            },
        }
        apk_output_dir = self.methods_output_dir / self._safe_dir_name(apk_name)
        json_path = apk_output_dir / (
            f"{row['row_index']}_{self._safe_dir_name(method_name)}.json"
        )
        try:
            apk_output_dir.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(json_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            self.logger.error(
                "Could not record %s row %s (%s): %s",
                apk_name, row.get("row_index"), extraction_status, exc,
            )
            return ""
        return str(json_path)

    @staticmethod
    def _safe_relative_path(path: Path, base: Path) -> str:
        try:
            return str(path.resolve().relative_to(base.resolve()))
        except Exception:
            return str(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone repositories from a single CSV and extract methods"
    )

    parser.add_argument(
        "--csv-file",
        required=True,
        help="CSV único com repo_url, apk_url, class e method",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
        help="Diretório de saída para logs, tracking e cloned_projects",
    )

    parser.add_argument(
        "--methods-output-dir",
        required=True,
        help="Diretório onde os JSONs dos métodos extraídos serão salvos",
    )

    parser.add_argument(
        "--skip-cloning",
        action="store_true",
        help="Reutiliza clones já existentes em OUTPUT_DIR/cloned_projects",
    )

    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reseta todos os repositórios em OUTPUT_DIR/cloned_projects",
    )

    parser.add_argument(
        "--clone-timeout",
        type=int,
        default=1800,
        help=(
            "Timeout em segundos para cada tentativa de git clone. Uma "
            "tentativa que estoura o tempo com os objetos ja baixados e "
            "retomada pela seguinte, em vez de recomecar do zero."
        ),
    )
    parser.add_argument(
        "--clone-only",
        action="store_true",
        help="Clona/mapeia os repositórios sem executar a extração.",
    )
    parser.add_argument(
        "--clone-retries",
        type=int,
        default=3,
        help="Número de tentativas para cada git clone",
    )
    parser.add_argument(
        "--project",
        default="",
        help="Processa somente o nome exato deste APK/projeto",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    extractor = SingleCsvMethodExtractor(
        output_dir=args.output_dir,
        methods_output_dir=args.methods_output_dir,
        skip_cloning=args.skip_cloning,
        clone_timeout=args.clone_timeout,
        clone_retries=args.clone_retries,
        project=args.project,
    )

    if args.reset:
        success = extractor.reset_repositories()
        sys.exit(0 if success else 1)

    success = extractor.run(
        csv_file=args.csv_file,
        clone_only=args.clone_only,
    )
    if success:
        sys.exit(0)
    # One repository that will not clone, or one finding whose class is not in
    # the checkout, used to end the run for every other project in the batch.
    # The loops above already moved past it; only the exit code did not.
    got_through, failed = (
        extractor.clone_counts if args.clone_only else extractor.extraction_counts
    )
    sys.exit(step_exit_code(got_through, failed))


if __name__ == "__main__":
    main()
