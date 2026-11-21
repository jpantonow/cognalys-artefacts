#!/usr/bin/env python3
"""
Deterministic semantic validation for coordinated patches.

Extracted from generate_error_fixes.py (Passo 2 of the
modularization plan). These are the gates that decide whether an
LLM-generated edit set is allowed to reach a repository: edit-shape
normalization, the cross-file/API/lifecycle hazard checks, the
review-only vs. auto-apply decision taxonomy, and the retry targeting
that follows a failed check.

Every function here is deterministic and free of provider or filesystem
state beyond reading the repository under validation.
"""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional

from cognalys.llm.contract_gates import (
    required_numeric_constants,
    changed_stored_derivations,
    value_still_configures_other_api,
    kept_constant_key_material,
    pinned_negotiated_protocol,
    removed_user_setting,
    protocol_algorithm_changes,
    rewritten_test_expectations,
    source_text_tests,
)
from cognalys.llm.false_positive_rules import governed_call_names
from cognalys.llm.patch_effect_checks import patch_effect_issues
from cognalys.solutions.import_check import mismatched_imports
from cognalys.solutions.signature_change import (
    breaking_callers,
    changed_symbol_names,
    declarations,
    dangling_constant_references,
    repository_texts,
    language_of,
    unrelated_parameter_changes,
)
from cognalys.solutions.scope_utils import (
    brace_depth_at,
    enclosing_class_stack,
    find_declaration_scope_mismatches,
    indentation_of,
    line_scope_states,
)
from cognalys.utils.disposition import REVIEW_ONLY_DECISIONS  # noqa: F401  (re-export)
from cognalys.utils.edit_text import match_trailing_line_break
from cognalys.extraction.tls_client_evidence import added_protocol_filter_code
# Shared with the pull request text (utils.pr_text), which lists names that
# still say a replaced value and must judge them the way these gates do.
from cognalys.utils.identifier_words import (
    COMMENT_OR_LITERAL as _COMMENT_OR_LITERAL,
    IDENTIFIER_WORD as _IDENTIFIER_WORD,
    TYPE_IDENTIFIER as _TYPE_IDENTIFIER,
    blank_comments_and_literals,
    type_words as _type_words,
)
from cognalys.utils.test_source_sets import (
    TEST_SOURCE_ROOT,
    TEST_SOURCE_SET_SEGMENT,
    is_test_source,
    is_test_source_path,
    source_module,
    test_source_set,
)

# A heuristic that fails is treated as having found nothing, so a run is
# never stopped by one -- but the failure is logged, so it is not mistaken
# for a clean result.
_LOG = logging.getLogger(__name__)
from cognalys.utils.patch_scope import PatchScope


def normalize_coordinated_edit(edit: Dict) -> Dict:
    """Normalize safe legacy deletions without accepting truncated responses."""
    normalized = dict(edit)
    kind = str(normalized.get("edit_kind", ""))
    original = str(normalized.get("original_code", ""))
    corrected = str(normalized.get("corrected_code", ""))
    nonempty_lines = [line.strip() for line in original.splitlines() if line.strip()]
    import_only = bool(nonempty_lines) and all(
        re.fullmatch(r"import\s+[\w.*]+\s*;?", line)
        for line in nonempty_lines
    )
    # Drafts produced before `delete` existed used declaration + empty text for
    # import removal. Normalize only that unambiguous form; empty methods,
    # constants, classes, or arbitrary declarations remain invalid.
    if kind == "declaration" and not corrected.strip() and import_only:
        normalized["edit_kind"] = "delete"
        normalized["corrected_code"] = ""
    return normalized


def validate_coordinated_edit_shape(edit: Dict) -> List[str]:
    """Validate replacement/delete shape consistently at every pipeline stage."""
    issues = []
    kind = str(edit.get("edit_kind", ""))
    symbol = str(edit.get("symbol", ""))
    original = str(edit.get("original_code", ""))
    corrected = str(edit.get("corrected_code", ""))
    reason = str(edit.get("reason", ""))
    if not symbol.strip() or not reason.strip():
        issues.append("symbol and reason are required")
    if kind == "create":
        if original:
            issues.append("create edit must have exactly empty original_code")
        if not corrected.strip():
            issues.append("create edit requires complete non-empty file content")
    elif not original.strip():
        issues.append("original_code is required for non-create edits")
    if kind in {"delete", "delete_file"}:
        if corrected:
            issues.append(f"{kind} edit must have exactly empty corrected_code")
    elif not corrected.strip():
        issues.append("replacement edit requires non-empty corrected_code")
    if kind not in {
        "method", "declaration", "constant", "test", "create", "delete",
        "delete_file",
    }:
        issues.append("unsupported edit_kind")
    return issues


_TEST_DIR_NAMES = {"test", "androidtest", "tests"}


_TEST_FILE_SUFFIXES = (".java", ".kt")


def repository_has_test_infrastructure(repo_root: Path) -> bool:
    """Whether this repository has any existing test source root or file.

    Absence means the pipeline cannot safely author a new test file here:
    it would also have to guess at build wiring (a test source set, a JUnit
    dependency) with no evidence any of it is correct -- a materially
    different, riskier change than the finding itself authorizes. Callers
    use this to fall back to review-only publication that documents the
    required manual verification instead of silently expanding scope into
    the build configuration.
    """
    # Path("") normalizes to Path(".") -- an empty repository_path must not
    # silently fall back to scanning this process's own working directory.
    # Path(".").parts == () while any real relative or absolute path has at
    # least one part, so this distinguishes the two without misclassifying
    # a genuine single-segment path.
    if not repo_root.parts or not repo_root.is_dir():
        return False
    for directory, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [
            name for name in dirnames
            if name not in {".git", ".gradle", "build", "node_modules"}
        ]
        if Path(directory).name.lower() in _TEST_DIR_NAMES:
            return True
        for filename in filenames:
            stem = Path(filename).stem
            if (filename.endswith(_TEST_FILE_SUFFIXES)
                    and (stem.endswith("Test") or stem.endswith("Tests"))):
                return True
    return False


# A correct legacy-protocol filter routinely excludes each disallowed name
# with a negated comparison (`!protocol.equals("TLSv1")`,
# `!protocol.startsWith("SSL")`) rather than ever spelling out the English
# word "filter" or "remove" in the source text -- observed for real in a
# generated fix that built its result by counting/copying only the
# protocols that were NOT SSL-prefixed or a legacy TLSv1 name, which is
# filtering by exclusion but never uses either literal word. Recognizing
# this code pattern (not vocabulary) avoids rejecting correct
# implementations that just happen to phrase it differently.
_LEGACY_TLS_EXCLUSION_PATTERN = re.compile(
    r'!\s*[\w.]*\b(?:equals|equalsIgnoreCase|startsWith|contentEquals)\s*'
    r'\(\s*["\'](?:SSL\w*|TLSv1(?:\.[01])?)["\']'
)


# REVIEW_ONLY_DECISIONS is defined in utils.disposition and re-exported
# above: the applicator, the PR writers and the preview generator all need
# that vocabulary and none of them is an LLM concern.
MULTI_FILE_DECISIONS = REVIEW_ONLY_DECISIONS | {"MULTI_FILE_FIX_SUPPORTED"}


def plan_edits_beyond_method(plan: Dict, target_path: str, method_names) -> List[str]:
    """Planned edits that the single-method generator cannot make.

    That generator returns one replacement for the reported method. Gallery's
    AsyncTask plan was FIX_SUPPORTED on one file, but its edit was the class
    GetMediaAsynctask -- superclass, execute(), stopFetching() -- and the
    generator correctly answered that doInBackground alone could not carry
    it, so a supported finding ended as "Failed". An edit to another file, or
    to a symbol in the target file that is not the reported method, is listed.
    """
    names = {str(name) for name in method_names if name}
    beyond: List[str] = []
    for item in (plan or {}).get("required_edits") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("source_file_path", ""))
        symbol = str(item.get("symbol", "")).strip()
        if path != target_path:
            beyond.append(f"{path}:{symbol}")
            continue
        last = re.split(r"[.#:]", symbol)[-1].split("(")[0].strip() if symbol else ""
        if symbol and last not in names:
            beyond.append(f"{path}:{symbol}")
    return beyond


def minimum_planned_files(decision: str) -> int:
    """How many distinct files a multi-file decision must actually touch.

    MULTI_FILE_FIX_SUPPORTED auto-applies with no human review, so two or
    more files is part of the evidence the change is genuinely coordinated.
    Every other multi-file-family decision in REVIEW_ONLY_DECISIONS is
    always reviewed by a human before merge, so one correctly-scoped file
    is enough -- see REVIEW_ONLY_DECISIONS for why forcing a second there
    was actively harmful.
    """
    return 1 if decision in REVIEW_ONLY_DECISIONS else 2


def _package_of(path: str) -> str:
    """The package directory of a Java/Kotlin source path, or ""."""
    match = re.match(
        r"^(?:.*/)?src/[A-Za-z][A-Za-z0-9]*/(?:java|kotlin)/(.*)/[^/]+$", path
    )
    return match.group(1) if match else ""


@lru_cache(maxsize=16)
def _repository_package_roots(repository_path: str) -> frozenset:
    """Package roots of a checkout, walked once per repository."""
    from cognalys.extraction.test_classpath import repository_packages

    return frozenset(repository_packages(Path(repository_path)))


def test_imports_off_the_classpath(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Generated tests that import libraries the module does not declare.

    A test written against Robolectric in a module that never declared it
    does not compile, and the finding does not authorise editing the build
    file to make it compile. Caught here, the generation is retried against
    the test file; caught by Gradle instead, it costs a full build and
    takes the production fix down with it (see extraction.test_classpath).
    """

    from cognalys.extraction.test_classpath import unsupported_test_imports

    repository_path = str(error.get("repository_path", ""))
    if not repository_path or not Path(repository_path).is_dir():
        return []
    issues: List[Dict] = []
    roots = None
    for edit in edits:
        path = str(edit.get("source_file_path", ""))
        if not (edit.get("edit_kind") == "test" or is_test_source(path)):
            continue
        if roots is None:
            roots = _repository_package_roots(repository_path)
        missing = unsupported_test_imports(
            Path(repository_path), path,
            str(edit.get("corrected_code", "")), roots,
        )
        if missing:
            issues.append({
                "code": "TEST_IMPORT_NOT_ON_TEST_CLASSPATH",
                "path": path,
                "details": missing,
            })
    return issues


def test_outside_production_modules(test_path: str, production_paths: List[str]) -> str:
    """Why a test file sits in no module the patch's production files are in, or "".

    A test is built and run by its own module's task, against that module's
    classpath, and sees none of another module's internal declarations.
    Fread's wrapper (2026-10-15) lived in framework/ and its test in
    plugins/bluesky/, so the generator widened the wrapper to a public
    top-level "test seam" and the build ran :plugins:bluesky's tests.
    Paths whose module cannot be told (no src/<set>/ segment) are not judged.
    """
    test_module = source_module(test_path)
    modules = {source_module(path) for path in production_paths}
    modules.discard(None)
    if test_module is None or not modules or test_module in modules:
        return ""
    return (
        f"it is in module '{test_module or '(root)'}', but the production "
        "files it tests are in "
        + ", ".join(f"'{module or '(root)'}'" for module in sorted(modules))
        + "; put the test in a test source set of the production file's own module"
    )


def tests_in_other_modules(error: Dict, edits: List[Dict]) -> List[Dict]:
    """New test files placed in a module none of the patch's production files are in."""
    production = [
        str(edit.get("source_file_path") or edit.get("source_file") or "")
        for edit in edits
        if isinstance(edit, dict) and edit.get("edit_kind") != "test"
        and not is_test_source(
            str(edit.get("source_file_path") or edit.get("source_file") or ""))
    ]
    production = [path for path in production if path] or [
        str(error.get("source_file_path", ""))]
    issues = []
    for edit in edits:
        if not isinstance(edit, dict) or edit.get("edit_kind") != "create":
            continue
        path = str(edit.get("source_file_path") or edit.get("source_file") or "")
        if not test_source_set(path):
            continue
        reason = test_outside_production_modules(path, production)
        if reason:
            issues.append({
                "code": "TEST_IN_OTHER_MODULE",
                "path": path,
                "details": [f"{path}: {reason}"],
            })
    return issues


# Top-level declarations of a Kotlin or Java production file, with their
# modifiers: what a test seam widens.
_KOTLIN_TOP_LEVEL = re.compile(
    r"^(?P<mods>(?:@[\w.]+(?:\([^)\n]*\))?\s+)*"
    r"(?:(?:public|private|internal|protected|open|abstract|sealed|data|enum|"
    r"inline|value|annotation|final|suspend|const|lateinit|tailrec|operator|"
    r"infix|external)\s+)*)"
    r"(?:fun\s+interface|class|interface|object|fun|val|var|typealias)\s+"
    r"(?:<[^>\n]*>\s*)?(?:[\w.]+\.)?(?P<name>[A-Za-z_]\w*)",
    re.MULTILINE,
)
_JAVA_TOP_LEVEL = re.compile(
    r"^(?P<mods>(?:@[\w.]+(?:\([^)\n]*\))?\s+)*"
    r"(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed|strictfp)\s+)*)"
    r"(?:class|interface|enum|record|@interface)\s+(?P<name>[A-Za-z_]\w*)",
    re.MULTILINE,
)


def _top_level_visibility(code: str, path: str, base_depth: int) -> Dict[str, bool]:
    """name -> whether a top-level declaration of `code` is visible outside its module.

    `base_depth` is the brace depth the fragment starts at in its file: a
    method edit can close its class and declare a top-level type after it.
    """
    kotlin = path.lower().endswith((".kt", ".kts"))
    pattern = _KOTLIN_TOP_LEVEL if kotlin else _JAVA_TOP_LEVEL
    states = line_scope_states(code)
    lines = code.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    visible: Dict[str, bool] = {}
    for index, line in enumerate(lines):
        depth, token = states[index]
        if token or base_depth + depth != 0:
            continue
        match = pattern.match(line.lstrip())
        if not match:
            continue
        modifiers = set(re.findall(r"\b\w+\b", re.sub(r"@[\w.]+(?:\([^)]*\))?", "", match.group("mods"))))
        visible[match.group("name")] = (
            not modifiers & {"private", "internal"} if kotlin else "public" in modifiers
        )
    return visible


def test_seam_visibility(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Production declarations made visible outside their module only for a test.

    A test in the production file's own module reaches Kotlin internal and
    Java package-private declarations, so a new or widened public top-level
    declaration that only its own file and the patch's tests use widens the
    app's API for nothing. A production error: the production file is
    regenerated with the declaration kept internal/private.
    """
    repository = Path(str(error.get("repository_path", "")))
    tests = [
        edit for edit in edits if isinstance(edit, dict)
        and (edit.get("edit_kind") == "test"
             or is_test_source(str(edit.get("source_file_path", ""))))
    ]
    if not tests:
        return []
    issues = []
    for edit in edits:
        if not isinstance(edit, dict) or edit in tests:
            continue
        path = str(edit.get("source_file_path", ""))
        if not path.lower().endswith((".kt", ".java")):
            continue
        original = str(edit.get("original_code", ""))
        corrected = str(edit.get("corrected_code", ""))
        base_depth = 0
        if original:
            try:
                content = (repository / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            offset =content.find(original) if content else -1
            base_depth = (brace_depth_at(content, offset) if offset >= 0
                          else int(indentation_of(original) > 0))
        before = _top_level_visibility(original, path, base_depth)
        after = _top_level_visibility(corrected, path, base_depth)
        widened = sorted(
            name for name, visible in after.items()
            if visible and before.get(name) is not True
        )
        details, test_paths = [], set()
        for name in widened:
            word = re.compile(rf"\b{re.escape(name)}\b")
            using_tests = {
                str(test.get("source_file_path", "")) for test in tests
                if word.search(str(test.get("corrected_code", "")))
            }
            other_production = any(
                word.search(str(other.get("corrected_code", "")))
                for other in edits
                if isinstance(other, dict) and other not in tests
                and str(other.get("source_file_path", "")) != path
            )
            if using_tests and not other_production:
                details.append(
                    f"{name} in {path} is visible outside its module only for "
                    f"{', '.join(sorted(using_tests))}; keep helpers "
                    "internal/private (a test in the same module reaches "
                    "internal and package-private declarations)"
                )
                test_paths |= using_tests
        if details:
            issues.append({
                "code": "TEST_SEAM_VISIBILITY_WIDENED",
                "path": path,
                "details": details,
                "test_paths": sorted(test_paths),
            })
    return issues


def unrelated_test_placements(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Reject new test code appended to a test file that does not cover the target.

    An arbitrary test file compiles new assertions perfectly well, so no other
    gate objects. On spacecowboy/Feeder (PR #1210) that let TLS protocol
    assertions be written into DateFormattingTest.kt, which a human caught only
    by reading the diff. Relevance is accepted on any of three grounds: the
    file's name derives from the target's, it sits in the target's package, or
    the code already present in it references the target type or method.
    """
    target = str(error.get("source_file_path", "")).strip()
    if not target:
        return []
    target_stem = Path(target).stem
    target_package = _package_of(target)
    method = str(error.get("method_name", "")).strip()

    issues = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        # Generation writes "source_file_path"; the persisted PR metadata and
        # execution report rename it to "source_file". Reading only one key
        # makes this gate silently pass on exactly the artifacts it audits.
        path = str(
            edit.get("source_file_path") or edit.get("source_file") or ""
        ).strip()
        kind = str(edit.get("edit_kind", ""))
        if not path or kind == "create":
            # A dedicated new file is always an acceptable home.
            continue
        if kind != "test" and not is_test_source(path):
            continue
        stem = Path(path).stem
        if stem.lower().startswith(target_stem.lower()):
            continue
        if target_package and _package_of(path) == target_package:
            continue
        existing = str(edit.get("original_code", ""))
        if re.search(rf"\b{re.escape(target_stem)}\b", existing):
            continue
        if method and re.search(rf"\b{re.escape(method)}\s*\(", existing):
            continue
        issues.append({
            "code": "TEST_PLACED_IN_UNRELATED_FILE",
            "details": [
                f"{path} does not cover {target_stem}",
                (
                    "Create a dedicated test file named after the target "
                    f"(for example {target_stem}Test) in the target's package "
                    f"'{target_package or 'the target package'}' with "
                    "edit_kind=create, instead of appending these assertions "
                    "to an unrelated test."
                ),
            ],
        })
    return issues


_NEW_CALL_PATTERN = re.compile(r"\bnew\s+([A-Z][A-Za-z0-9_$]*)\s*\(")
_JAVA_TYPE_DECLARATION = r"\b(?:class|interface|enum|record)\s+{name}\b"


def _declared_constructor_arities(source: str, type_name: str) -> Optional[set]:
    """Argument counts the type's declared constructors accept, or None.

    None means "cannot tell" -- no declaration found, or a shape this crude
    parser will not judge (varargs, generics with commas). Returning None is
    how the check stays quiet instead of guessing.
    """
    if not re.search(_JAVA_TYPE_DECLARATION.format(name=re.escape(type_name)),
                     source):
        return None
    # A Kotlin class declares its constructors as `class X(...)` and
    # `constructor(...)`, which the Java pattern below never sees: Talk's
    # RemoteWipeInterceptor (2026-10-25) read as taking [0] arguments.
    code = blank_comments_and_literals(source)
    if (re.search(rf"\bclass\s+{re.escape(type_name)}\b[^{{\n]*\(", code)
            or re.search(r"\bconstructor\s*\(", code)):
        return None
    arities = set()
    explicit = False
    for match in re.finditer(
        rf"(?:public|protected|private)?\s*\b{re.escape(type_name)}\s*\(([^)]*)\)\s*(?:throws[^{{;]*)?\{{",
        source,
    ):
        arguments = match.group(1).strip()
        if "..." in arguments or "<" in arguments:
            return None  # varargs or generics: not judged here
        explicit = True
        arities.add(
            0 if not arguments else len([
                part for part in arguments.split(",") if part.strip()
            ])
        )
    if not explicit:
        # No explicit constructor: Java supplies a no-argument default.
        return {0}
    return arities


_ASSERT_EQUALITY = re.compile(
    r"\bassert(?:Equals|NotEquals)\s*\("
)
_LOCAL_DECLARATION = re.compile(
    r"\b(?P<type>[A-Z][A-Za-z0-9_$]*(?:\.[A-Z][A-Za-z0-9_$]*)*)"
    r"\s+(?P<name>[a-z][A-Za-z0-9_$]*)\s*="
)
_EQUALS_OVERRIDE = re.compile(r"\bboolean\s+equals\s*\(")


# Resolving a type used to walk the whole checkout, once per type name and
# once per gate. Three gates now resolve types during a single validation, so
# a file with a handful of repository types cost dozens of walks of a large
# Android tree -- enough to dominate the stage on a mounted filesystem. The
# walk happens once per checkout instead. Only paths are cached; contents are
# always read fresh, so a patched file is never served stale. Keys are
# per-worktree, and each finding validates its own worktree, so the file list
# cannot change underneath an entry while it is in use.
_SOURCE_INDEX_CACHE: Dict[str, Dict[str, List[Path]]] = {}
_SOURCE_INDEX_LIMIT = 8


def _source_index(repo_root: Path) -> Dict[str, List[Path]]:
    """Java/Kotlin sources in this checkout, grouped by file stem."""
    key = str(repo_root.resolve())
    index = _SOURCE_INDEX_CACHE.get(key)
    if index is None:
        index = {}
        for path in repo_root.rglob("*"):
            if path.suffix.lower() not in {".java", ".kt"}:
                continue
            if any(part in {".git", "build", ".gradle"} for part in path.parts):
                continue
            index.setdefault(path.stem, []).append(path)
        if len(_SOURCE_INDEX_CACHE) >= _SOURCE_INDEX_LIMIT:
            _SOURCE_INDEX_CACHE.clear()
        _SOURCE_INDEX_CACHE[key] = index
    return index


def repository_type_source(repo_root: Path, type_text: str,
                           cache: Dict[str, str],
                           overlay: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Source of the file declaring a possibly nested type, or None.

    `Schedule.Item` lives in Schedule.java, so searching for Item.java --
    which is what a simple-name lookup does -- finds nothing. The outer name
    is what names the file; the simple name is what must be declared in it.

    `overlay` (from `_coordinated_overlay`) is checked before the disk: a
    coordinated patch's own file, with every one of its own edits applied,
    so a symbol a sibling file adds in the same patch reads as declared
    rather than as missing from the unmodified checkout.
    """
    if type_text in cache:
        return cache[type_text] or None
    parts = type_text.split(".")
    file_stem, simple = parts[0], parts[-1]
    pattern = re.compile(_JAVA_TYPE_DECLARATION.format(name=re.escape(simple)))
    for path in _source_index(repo_root).get(file_stem, ()):
        text = None
        if overlay:
            try:
                relative = path.relative_to(repo_root).as_posix()
            except ValueError:
                relative = ""
            if relative in overlay:
                text = overlay[relative]
        if text is None:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        if pattern.search(text):
            cache[type_text] = text
            return text
    cache[type_text] = ""
    return None


def _coordinated_overlay(repo_root: Path, edits: List[Dict]) -> Dict[str, str]:
    """Every coordinated edit's file, as the applied patch will leave it.

    A symbol one file's edit adds (a new method, a new field) is not yet on
    disk, so checking a sibling file's call to it against the unmodified
    checkout reports SYMBOL_NOT_DECLARED_IN_REPOSITORY for a call the patch
    itself makes valid -- exactly what happened to the DNSResolver/
    DNSFilterService coordinated edit in run_20260922_134405, which added
    DNSResolver.closeResponseOutput() in one file and called it from
    another, and was rejected as calling an undeclared method.

    Every edit for a file is applied to its base content with the same
    original/corrected substitution the applier itself performs
    (solutions.solution_applicator), so a check sees the file the way the
    patch will actually leave it -- not reimplemented here, just mirrored,
    since this module does not import the applier.

    Best-effort: an edit whose `original_code` is not found in the file as
    built so far is left unapplied rather than raised. This overlay only
    feeds a heuristic check; the applier's own application is what actually
    matters and will still catch a genuinely broken edit.
    """
    grouped: Dict[str, List[Dict]] = {}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        path = str(edit.get("source_file_path") or edit.get("source_file") or "")
        if path:
            grouped.setdefault(path, []).append(edit)
    overlay: Dict[str, str] = {}
    for relative, file_edits in grouped.items():
        if any(edit.get("edit_kind") == "create" for edit in file_edits):
            content = ""
        else:
            try:
                content = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        for edit in file_edits:
            if edit.get("edit_kind") == "create":
                content = str(edit.get("corrected_code", ""))
                continue
            original = str(edit.get("original_code", ""))
            corrected = str(edit.get("corrected_code", ""))
            if original and original in content:
                content = content.replace(
                    original, match_trailing_line_break(original, corrected), 1,
                )
        overlay[relative] = content
    return overlay


def _assertion_arguments(code: str, start: int) -> List[str]:
    """Top-level arguments of the assertion call opening at `start`."""
    depth, buffer, arguments = 1, "", []
    for character in code[start:]:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                break
        if depth == 1 and character == ",":
            arguments.append(buffer.strip())
            buffer = ""
            continue
        buffer += character
    arguments.append(buffer.strip())
    return arguments


def identity_equality_assertions(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Reject a test asserting equality on a type that never overrides equals.

    net.gaast.giggity's generated test built two Schedule.Item values with
    the same fields and asserted assertEquals(first, second). Item declares
    no equals(Object), so Java compared references and the assertion could
    only fail -- which it did, after a compile that finally succeeded and a
    full unit-test run. The repair stage had already written exactly this in
    its remaining_uncertainties, so the knowledge existed; nothing checked it.

    Conservative by construction: it speaks only when both operands are local
    variables of the same repository-declared type whose source contains no
    equals declaration at all.
    """
    repo_root = Path(str(error.get("repository_path", "")))
    if not repo_root.parts or not repo_root.is_dir():
        return []

    cache: Dict[str, str] = {}
    overlay = _coordinated_overlay(repo_root, edits)
    issues = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        code = str(edit.get("corrected_code", ""))
        path = str(
            edit.get("source_file_path") or edit.get("source_file") or ""
        )
        if not code:
            continue
        declared = {
            match.group("name"): match.group("type")
            for match in _LOCAL_DECLARATION.finditer(code)
        }
        details = []
        for match in _ASSERT_EQUALITY.finditer(code):
            arguments = _assertion_arguments(code, match.end())
            # JUnit allows a leading message argument; the compared pair is
            # always the last two.
            operands = arguments[-2:]
            if len(operands) != 2:
                continue
            types = {declared.get(name) for name in operands}
            if len(types) != 1 or None in types:
                continue
            type_text = types.pop()
            source = repository_type_source(repo_root, type_text, cache, overlay)
            if source is None or _EQUALS_OVERRIDE.search(source):
                continue
            details.append(
                f"{type_text} declares no equals(Object), so "
                f"assertEquals({operands[0]}, {operands[1]}) compares "
                f"references and can only pass for the same instance"
            )
        if details:
            issues.append({
                "code": "EQUALITY_ASSERTED_ON_IDENTITY_TYPE",
                "source_file_path": path,
                "details": details,
            })
    return issues


def _live_code(code: str) -> str:
    """Comments blanked, each literal kept as one placeholder character.

    Offsets are preserved, and a literal argument still counts as an
    argument while the commas and brackets inside it do not.
    """
    characters = list(code)
    for match in _COMMENT_OR_LITERAL.finditer(code):
        for index in range(match.start(), match.end()):
            if characters[index] != "\n":
                characters[index] = " "
        if not match.group().startswith("/"):
            characters[match.start()] = "L"
    return "".join(characters)


def unresolvable_constructor_calls(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Reject generated code that calls a constructor the repository lacks.

    Coordinated drafts declare what they depend on in `required_contracts` --
    net.gaast.giggity's generated test literally stated that "Schedule(String)
    ... remain available" -- but nothing ever checked such a claim against the
    source. The test was written, applied, and only a two-and-a-half-minute
    Gradle run reported `constructor Schedule in class Schedule cannot be
    applied to given types`.

    Deliberately conservative: it only speaks when the type is declared in the
    repository and its constructors are simple enough to count, so an
    unresolved or unusual shape stays silent rather than blocking a good patch.
    """
    repo_root = Path(str(error.get("repository_path", "")))
    if not repo_root.parts or not repo_root.is_dir():
        return []

    # Shares repository_type_source with the other gates rather than keeping
    # a third private copy of the same lookup: one indexed walk of the
    # checkout now serves all of them. A `new Item(...)` call names a simple
    # type, so the nested-type handling there resolves it exactly as this
    # did before.
    sources: Dict[str, str] = {}
    overlay = _coordinated_overlay(repo_root, edits)

    def find_declaration(type_name: str) -> Optional[str]:
        return repository_type_source(repo_root, type_name, sources, overlay)

    def calls(code: str) -> List[tuple]:
        """(type, argument count, offset) of each `new T(...)` in live code."""
        found = []
        for match in _NEW_CALL_PATTERN.finditer(code):
            depth, arguments, buffer = 1, [], ""
            for character in code[match.end():]:
                if character in "([{":
                    depth += 1
                elif character in ")]}":
                    depth -= 1
                    if depth == 0:
                        break
                if depth == 1 and character == ",":
                    arguments.append(buffer)
                    buffer = ""
                    continue
                buffer += character
            if buffer.strip():
                arguments.append(buffer)
            found.append((match.group(1), len(arguments), match.start()))
        return found

    issues = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        # Talk's SSLSocketFactoryCompat draft (2026-10-25) kept the line
        # `//  httpClient.addInterceptor(new RemoteWipeInterceptor(...))` and
        # was rejected for it on two attempts. Only a live call the patch
        # adds is judged: one the base already makes has compiled before.
        code = _live_code(str(edit.get("corrected_code", "")))
        path = str(
            edit.get("source_file_path") or edit.get("source_file") or ""
        )
        if not code.strip() or not path.endswith(".java"):
            continue
        existing: Dict[tuple, int] = {}
        for type_name, count, _ in calls(_live_code(str(edit.get("original_code", "")))):
            existing[(type_name, count)] = existing.get((type_name, count), 0) + 1
        for type_name, count, _ in calls(code):
            if existing.get((type_name, count), 0) > 0:
                existing[(type_name, count)] -= 1
                continue
            source = find_declaration(type_name)
            if source is None:
                continue
            arities = _declared_constructor_arities(source, type_name)
            if arities is None:
                continue
            if count not in arities:
                issues.append({
                    "code": "CONSTRUCTOR_NOT_DECLARED_IN_REPOSITORY",
                    # The offending call is as often in a generated test as in
                    # production -- giggity's was -- and the code sets classify
                    # this one as production. Naming the file makes the retry
                    # regenerate whichever half actually carries the defect.
                    "source_file_path": path,
                    "details": [
                        f"{path} calls new {type_name}(...) with {count} "
                        f"argument(s)",
                        f"{type_name} declares constructors taking "
                        f"{sorted(arities)} argument(s)",
                        "Use an existing constructor or factory; do not "
                        "assume an API the repository does not declare.",
                    ],
                })
    return issues


def import_issues(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Added imports from a package the repository never uses (see import_check)."""
    repo_root = Path(str(error.get("repository_path", "")))
    if not repo_root.parts or not repo_root.is_dir():
        return []
    try:
        return mismatched_imports(repo_root, edits)
    except Exception as exc:  # noqa: BLE001 -- a heuristic must never block generation
        _LOG.warning("import check failed; no import issue reported: %s", exc)
        return []


def signature_change_issues(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Callers or references the patch itself leaves uncompilable.

    A heuristic gate: it reports only what solutions.signature_change can read
    with certainty, and an unexpected failure in it must never stop a patch.
    """
    try:
        evidence = error.get("evidence_analysis", {}) or {}
        finding_text = " ".join(str(value) for value in (
            error.get("error_message", ""), evidence.get("confirmed_cause", ""),
            (evidence.get("migration_assessment", {}) or {}).get("safe_strategy", ""),
        ))
        unrelated = unrelated_parameter_changes(edits, finding_text)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("unrelated-parameter check failed; none reported: %s", exc)
        unrelated = []
    repo_root = Path(str(error.get("repository_path", "")))
    if not repo_root.parts or not repo_root.is_dir():
        return unrelated
    try:
        names = changed_symbol_names(edits)
        if not names:
            return unrelated  # the common case: no signature or constant changes, no scan
        overlay = _coordinated_overlay(repo_root, edits)
        texts = repository_texts(repo_root, overlay, names)
        return unrelated + (
            breaking_callers(repo_root, edits, overlay, texts)
            + dangling_constant_references(repo_root, edits, overlay, texts)
        )
    except Exception as exc:  # noqa: BLE001 -- never block generation on a heuristic
        _LOG.warning("signature-change check failed; no broken caller reported: %s", exc)
        return unrelated


_LITERAL = re.compile(r'"([A-Za-z][A-Za-z0-9._/-]{1,23})"')
_DECLARED_FUNCTION = re.compile(
    r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w$.]+\.)?(?P<kotlin>[A-Za-z_$][\w$]*)\s*\((?P<kotlin_params>[^)]*)\)"
    r"|^[ \t]*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|synchronized|native|abstract"
    r"|default|override|strictfp)\s+)*[\w$.<>\[\],?]+\s+"
    r"(?P<java>[A-Za-z_$][\w$]*)\s*\((?P<java_params>[^)]*)\)\s*(?:throws[^{;]*)?\{",
    re.MULTILINE,
)
_NOT_DECLARATION_NAMES = frozenset({
    "if", "for", "while", "switch", "catch", "synchronized", "return", "new",
})


def _normalised_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


_ANONYMOUS_CLASS = re.compile(r"\bnew\s+[\w$.]+(?:<[^>]*>)?\s*\([^)]*\)\s*\{")


def _anonymous_class_spans(code: str) -> List[tuple]:
    """(start, end) of each anonymous class body: `new Type(...) { ... }`."""
    spans = []
    for match in _ANONYMOUS_CLASS.finditer(code or ""):
        depth, index = 0, match.end() - 1
        while index < len(code):
            if code[index] == "{":
                depth += 1
            elif code[index] == "}":
                depth -= 1
                if depth == 0:
                    spans.append((match.end(), index))
                    break
            index += 1
    return spans


def declared_function_signatures(code: str, skip_anonymous: bool = False) -> Dict[str, str]:
    """Function names declared in `code`, with their parameter text normalised.

    With `skip_anonymous`, methods declared inside an anonymous class body are
    left out: they leave with their class and are never renamed on their own.
    """
    declared: Dict[str, str] = {}
    spans = _anonymous_class_spans(code or "") if skip_anonymous else []
    for match in _DECLARED_FUNCTION.finditer(code or ""):
        if any(start <= match.start() <= end for start, end in spans):
            continue
        name = match.group("kotlin") or match.group("java") or ""
        if not name or name in _NOT_DECLARATION_NAMES:
            continue
        # `new X509TrustManager() {` instantiates an anonymous class; read as a
        # method declared with return type "new", its removal was reported as
        # a rename to every new helper of zSMTH's patch (2026-10-17).
        if match.group("java") and re.search(
                r"\bnew\s+$", code[match.start():match.start("java")]):
            continue
        params = match.group("kotlin_params") if match.group("kotlin") else match.group("java_params")
        declared.setdefault(name, " ".join(str(params or "").split()))
    return declared


def _behaviour_tokens(code: str) -> set:
    """Literals that name a behaviour: an algorithm, an encoding, a version.

    Only a short literal with a digit, or an all-capitals one, qualifies --
    "SHA-1", "MD5", "UTF-8", "AES", "TLSv1" -- so ordinary words ("data",
    "name") never count.
    """
    tokens = set()
    for literal in _LITERAL.findall(code or ""):
        if not (re.search(r"\d", literal) or literal.upper() == literal):
            continue
        token = _normalised_name(literal)
        if len(token) >= 3:
            tokens.add(token)
    return tokens


_VARIABLE_DECLARATION = re.compile(
    r"\b(?:String|byte\s*\[\s*\]|int|long|char\s*\[\s*\]|var|val)\s+([A-Za-z_]\w*)\s*(?::\s*[\w<>\[\]?]+\s*)?=")


def variable_name_contradictions(original: str, corrected: str,
                                 referenced_elsewhere=lambda name: False) -> List[Dict]:
    """Constants and variables whose kept name still states a removed behaviour.

    whoBIRD run7 (2026-10-18) moved every check to SHA-256 and kept
    `model16MD5`, `metaModelMD5` and `calcModelMD5` holding SHA-256 values.
    name_behavior_contradictions covers functions only. A name referenced
    outside the edited code is left alone: renaming it there would reach
    beyond the patch.
    """
    removed = _behaviour_tokens(original) - _behaviour_tokens(corrected) - _type_words(corrected)
    added = _behaviour_tokens(corrected) - _behaviour_tokens(original)
    if not removed or not added:
        return []
    kept = (set(_VARIABLE_DECLARATION.findall(original or ""))
            & set(_VARIABLE_DECLARATION.findall(corrected or "")))
    contradictions = []
    for name in sorted(kept):
        normalised = _normalised_name(name)
        stated = sorted(token for token in removed if token in normalised)
        if not stated or any(token in normalised for token in added):
            continue
        if referenced_elsewhere(name):
            continue
        contradictions.append({
            "code": "NAME_CONTRADICTS_BEHAVIOR",
            "name": name,
            "removed_behavior": stated[0],
            "subject": "variable",
            "message": (f"`{name}` still states '{stated[0]}' but now holds a "
                        f"{', '.join(sorted(added))} value; rename it (every reference is in "
                        "the edited code)"),
        })
    return contradictions


def _referenced_outside(error: Dict, edited_path: str):
    """A predicate: is `name` used in a source file other than `edited_path`?"""
    repository = Path(str(error.get("repository_path", "")))
    if not repository.is_dir():
        return lambda name: True  # unknown: never ask for a rename

    def referenced(name: str) -> bool:
        pattern = re.compile(rf"\b{re.escape(name)}\b")
        for count, path in enumerate(repository.rglob("*")):
            if count > 50_000:
                return True
            if path.suffix not in {".java", ".kt"} or not path.is_file():
                continue
            relative = path.relative_to(repository).as_posix()
            if relative == edited_path or "/build/" in f"/{relative}":
                continue
            try:
                if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                    return True
            except OSError:
                continue
        return False
    return referenced


# A selector whose name says the code accepts every certificate.
_ACCEPT_ALL_NAME = re.compile(
    r"\b(\w*(?:trust\w*all|allow\w*all\w*cert|insecure|unsafe)\w*)\b", re.IGNORECASE)


def accept_all_name_contradictions(original: str, corrected: str) -> List[Dict]:
    """A trust-all selector kept while the branch it selects now validates.

    Read You run2 (2026-10-18) kept `if (trustAllCerts)` and its default of
    true, and rewrote the branch to build a validating manager: the flag no
    longer means what it says, and `if (trustAllCerts) <validate> else
    <validate>` reads as a mistake. Validation by default means changing the
    selector's default, not what the selector selects.
    """
    from cognalys.extraction.tls_trust_evidence import trust_overrides

    if not trust_overrides(original)["trust_all"] or trust_overrides(corrected)["trust_all"]:
        return []
    contradictions = []
    for name in sorted({match.group(1) for match in _ACCEPT_ALL_NAME.finditer(corrected or "")}):
        guard = re.compile(rf"\bif\s*\(\s*!?\s*{re.escape(name)}\s*\)")
        if guard.search(original or "") and guard.search(corrected or ""):
            contradictions.append({
                "code": "NAME_CONTRADICTS_BEHAVIOR",
                "name": name,
                "subject": "selector",
                "message": (f"`{name}` still selects a branch, but that branch no longer accepts "
                            "every certificate; change the selector's default instead and "
                            "leave the accept-all branch as it was"),
            })
    return contradictions


def name_behavior_contradictions(original: str, corrected: str,
                                 extra_names=(), sibling_source: str = "") -> List[Dict]:
    """Functions whose kept name still states a behaviour the patch removed.

    QuantumBadger/RedReader closed PR #1373 partly because `General.sha1` was
    made to return a SHA-256 digest under the same name. The same file already
    declared `sha256`, which is what the callers should have been switched to.
    """
    removed = _behaviour_tokens(original) - _behaviour_tokens(corrected)
    added = _behaviour_tokens(corrected) - _behaviour_tokens(original)
    # Only a literal replaced by a different behaviour literal ("SHA-1" by
    # "SHA-256") changes what the name describes. A literal replaced by an
    # equivalent constant ("UTF-8" by Charsets.UTF_8) does not, and a
    # rejection gate must not fire on it.
    if not removed or not added:
        return []
    # A removed token that is also a word of a type the code uses leaves a
    # name echoing that type true (see _type_words).
    removed -= _type_words(corrected)
    names_before = set(declared_function_signatures(original))
    names_after = set(declared_function_signatures(corrected))
    for name in extra_names or ():
        if not name:
            continue
        pattern = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
        if pattern.search(original or ""):
            names_before.add(name)
        if pattern.search(corrected or ""):
            names_after.add(name)
    alternatives_source = declared_function_signatures(sibling_source)
    contradictions = []
    for name in sorted(names_before & names_after):
        normalised = _normalised_name(name)
        for token in sorted(removed):
            if token not in normalised or any(item in normalised for item in added):
                continue
            alternatives = sorted(
                other for other in alternatives_source
                if other != name and any(item in _normalised_name(other) for item in added)
            )
            contradictions.append({
                "code": "NAME_CONTRADICTS_BEHAVIOR",
                "name": name,
                "removed_behavior": token,
                "added_behavior": sorted(added),
                "existing_alternatives": alternatives,
                "message": (
                    f"{name} still states '{token}' but the patch removes it"
                    + (f" in favour of {', '.join(sorted(added))}" if added else "")
                    + (f"; call the existing {', '.join(alternatives)} instead"
                       if alternatives else
                       "; rename it when rename_safety reports no hard blocker (a "
                       "repository check the pipeline runs, not a maintainer "
                       "approval), updating every entry of rename_safety.references "
                       "in the same patch, or add a correctly named function and "
                       "switch the callers")
                ),
            })
    return contradictions


_EXECUTOR_SUBMIT = re.compile(r"\.\s*submit\s*[({]")
_HANDLES_TASK_FAILURE = re.compile(
    r"\bcatch\b|\brunCatching\b|\.\s*get\s*\(|\bsetUncaughtExceptionHandler\b"
    r"|\.\s*(?:exceptionally|handle|whenComplete)\s*\("
)


def executor_submit_discards_exceptions(original: str, corrected: str) -> bool:
    """A patch that newly submits work to an executor and never observes failure.

    ExecutorService.submit stores an exception in the returned Future; unless
    something calls get() on it, the failure is silent. Gallery's AsyncTask
    replacement did exactly that, turning a crash that surfaced a broken media
    scan into a screen that never finishes loading. execute(), a catch in the
    task, or reading the Future all keep the failure observable.
    """
    corrected = corrected or ""
    if len(_EXECUTOR_SUBMIT.findall(corrected)) <= len(_EXECUTOR_SUBMIT.findall(original or "")):
        return False
    if not re.search(r"Executor", corrected):
        return False
    return not _HANDLES_TASK_FAILURE.search(corrected)


_CALLED_NAME = re.compile(r"\.\s*([A-Za-z_$][\w$]*)\s*\(")


def redundant_caller_renames(edits: List[Dict]) -> List[Dict]:
    """Caller edits that only switch to a new name while the old one stays.

    Gallery's patch declared start() and an execute() that just calls it, then
    edited three callers from .execute() to .start() and left two others on
    .execute(). The caller edits changed nothing but a name the target still
    declares: churn a reviewer has to read, and two entry points to maintain.
    """
    declared = set()
    forwarding = set()
    for edit in edits:
        code = str(edit.get("corrected_code", ""))
        declared.update(declared_function_signatures(code))
        masked = blank_comments_and_literals(code)
        for declaration in declarations(code, language_of(str(edit.get("source_file_path", "")))):
            header = re.match(r"[\w$]+\s*\([^{};]*\)\s*(?:throws\s+[\w.,\s]+|:\s*[\w.<>,?\s]+)?\s*\{", masked[declaration.name_start:])
            if not header:
                continue
            opening = declaration.name_start + header.end() - 1
            body_size = len(_braced_body(masked, opening))
            body = _without_comments(code[opening + 1:opening + 1 + body_size]).strip()
            delegate = re.fullmatch(r"(?:return\s+)?(?:this\.)?(\w+)\s*\(([^{};]*)\)\s*;?", body)
            if delegate and re.sub(r"\s+", "", delegate[2]) == ",".join(p.name for p in declaration.parameters):
                forwarding.add((declaration.name, delegate[1]))
    issues = []
    for edit in edits:
        original = str(edit.get("original_code", ""))
        corrected = str(edit.get("corrected_code", ""))
        if not original or original == corrected:
            continue
        before = set(_CALLED_NAME.findall(original))
        after = set(_CALLED_NAME.findall(corrected))
        for old in sorted(before - after):
            if old not in declared:
                continue
            for new in sorted(after - before):
                if (old, new) not in forwarding:
                    continue
                restored = re.sub(
                    rf"\.(\s*){re.escape(new)}(\s*)\(", rf".\g<1>{old}\g<2>(", corrected,
                )
                if restored == original:
                    issues.append({
                        "code": "REDUNDANT_CALLER_RENAME",
                        "path": str(edit.get("source_file_path", "")),
                        "details": [
                            f"the edit only renames calls from {old}() to {new}(), "
                            f"and {old}() is still declared; keep the existing "
                            "call and drop this edit, or remove the old method"
                        ],
                    })
    return issues


def detected_renames(original: str, corrected: str) -> List[tuple]:
    """(old, new) pairs: a declaration that disappears while one with the same
    parameters appears under a different name."""
    before = declared_function_signatures(original, skip_anonymous=True)
    after = declared_function_signatures(corrected, skip_anonymous=True)
    # An override implements its supertype's name; it is never renamed, only
    # dropped with its class or added with a new one. LocalSend (2026-10-29)
    # replaced an accept-any X509TrustManager object by a TrustManagerFactorySpi,
    # and getAcceptedIssuers() -> engineGetTrustManagers() became an
    # UNAUTHORIZED_RENAME that masked the review of the draft itself.
    overrides = _overriding_function_names(original) | _overriding_function_names(corrected)
    removed = {name: params for name, params in before.items()
               if name not in after and name not in overrides}
    added = {name: params for name, params in after.items()
             if name not in before and name not in overrides}
    # One removed name is renamed to at most one added name, and the reverse.
    # zSMTH's patch renamed getUnsafeOkHttpClient to getSecureOkHttpClient and
    # also dropped an anonymous TrustManager whose getAcceptedIssuers() takes
    # no arguments either; every removed one was reported as renamed to
    # getSecureOkHttpClient (2026-10-17). BlueGate's draft renamed
    # generateNewKeyPair(alias) to generateNewKeyMaterial(alias) and extracted
    # prepareKeyGenParameterSpec(alias), and both were reported as its rename
    # (2026-10-29). Pairs are taken by the name words they share, most first;
    # a tie at the best score of either side names none.
    candidates = [
        (len(_name_words(old) & _name_words(new)), old, new)
        for new, params in added.items() for old, other in removed.items() if other == params
    ]
    used_old, used_new = set(), set()
    renames = []
    for score, old, new in sorted(candidates, key=lambda item: -item[0]):
        if old in used_old or new in used_new:
            continue
        tied = any(
            other_score == score and (other_old == old) != (other_new == new)
            and other_old not in used_old and other_new not in used_new
            for other_score, other_old, other_new in candidates
        )
        used_old.add(old)
        used_new.add(new)
        if not tied:
            renames.append((old, new))
    order = list(added)
    return sorted(renames, key=lambda pair: order.index(pair[1]))


_OVERRIDE_MODIFIER = re.compile(r"(?<![\w$])override\b|@Override\b")
_ANNOTATION_ONLY_LINE = re.compile(r"[ \t]*(?:@[\w.]+(?:\([^)\n]*\))?[ \t]*)+")


def _overriding_function_names(code: str) -> set:
    """Functions `code` declares as overrides: Kotlin `override`, or Java
    `@Override` on the declaration's line or the annotation lines above it."""
    masked = blank_comments_and_literals(code or "")
    names = set()
    for match in _DECLARED_FUNCTION.finditer(masked):
        group = "kotlin" if match.group("kotlin") else "java"
        name_start = match.start(group)
        line_start = masked.rfind("\n", 0, match.start()) + 1
        header = masked[line_start:name_start]
        while line_start > 0:
            previous_start = masked.rfind("\n", 0, line_start - 1) + 1
            previous = masked[previous_start:line_start - 1]
            if not _ANNOTATION_ONLY_LINE.fullmatch(previous):
                break
            header = previous + "\n" + header
            line_start = previous_start
        if _OVERRIDE_MODIFIER.search(header):
            names.add(match.group(group))
    return names


def _name_words(name: str) -> set:
    """The lower-case words of a camelCase or snake_case identifier."""
    return {word.lower() for word in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)}


def _rename_assessment(error: Dict, path: str, old: str) -> Dict:
    """The rename_safety assessment of `old` declared in `path`.

    The reported symbol's assessment was made with the evidence; any other
    renamed helper is assessed here, on the same checkout and by the same
    rules (rename_issues used to authorise the reported symbol alone, so a
    correct rename of a helper was refused). Cached on the error for the
    retries of the same draft.
    """
    key = f"{path}#{old}"
    by_symbol = error.get("rename_safety_by_symbol") or {}
    if key in by_symbol:
        return by_symbol[key]
    safety = error.get("rename_safety") or {}
    if safety.get("symbol") == old and (safety.get("declaration") or {}).get("path") == path:
        return safety
    repository = Path(str(error.get("repository_path", "")))
    if not repository.is_dir() or not (repository / path).is_file():
        return {"allowed": False, "symbol": old, "references": [],
                "hard_blockers": ["no rename_safety assessment allows renaming this symbol"]}
    from cognalys.extraction.rename_safety import assess_rename_safety
    from cognalys.extraction.repository_index import build_repository_index
    from cognalys.extraction.semantic_index import build_semantic_index

    try:
        index = build_repository_index(repository)
        semantic = build_semantic_index(
            repo_root=repository, source_cache=index["source_cache"],
            target_file=repository / path, target_method=old, target_arity=None,
        )
        assessment = assess_rename_safety(repository, path, old, semantic, index)
        if index.get("metadata", {}).get("truncated"):
            assessment["allowed"] = False
            assessment.setdefault("hard_blockers", []).append("repository_index_truncated")
    except Exception as exc:  # noqa: BLE001 -- an unassessable rename is not taken
        assessment = {"allowed": False, "symbol": old, "references": [],
                      "hard_blockers": [f"rename_safety_assessment_failed: {exc}"]}
    error.setdefault("rename_safety_by_symbol", {})[key] = assessment
    return assessment


def _hard_rename_blockers(assessment: Dict) -> List[str]:
    """Hard blockers; an assessment written before the hard/resolvable split
    states only blocked_by, all of which counted as hard."""
    if "hard_blockers" in assessment:
        return list(assessment.get("hard_blockers") or [])
    if assessment.get("allowed"):
        return []
    return list(assessment.get("blocked_by") or []) or [
        "no rename_safety assessment allows renaming this symbol"]


def _reference_count(text: str, name: str) -> int:
    """References to `name` (calls, `::name`, imports) outside declarations."""
    from cognalys.extraction.rename_safety import _declaration_pattern, reference_pattern

    declaration = _declaration_pattern(name)
    reference = reference_pattern(name)
    return sum(
        len(reference.findall(line))
        for line in str(text or "").splitlines() if not declaration.search(line)
    )


def _declaration_count(text: str, name: str) -> int:
    from cognalys.extraction.rename_safety import _declaration_pattern

    declaration = _declaration_pattern(name)
    return sum(1 for line in str(text or "").splitlines() if declaration.search(line))


def _count_after(before: str, path_edits: List[Dict], counter) -> int:
    """`counter` over the file once its edits are applied, from the edits
    themselves: what each corrected_code adds minus what its original_code
    held, so an edit that cannot be located in memory is still counted."""
    return counter(before) + sum(
        counter(str(edit.get("corrected_code", ""))) - counter(str(edit.get("original_code", "")))
        for edit in path_edits
    )


def rename_issues(error: Dict, edits: List[Dict]) -> tuple:
    """Check every rename the patch makes; return (errors, accepted pairs).

    A rename is accepted when its rename_safety assessment has no hard
    blocker, the new name is not already declared in the declaring file and
    does not state the behaviour the patch removes, the whole overload
    family moves together, and every reference the assessment found -- each
    call, `import ...name` and `::name` -- now names the new function. After
    the patch no reference that bound to the renamed declaration may still
    name the old one; where the old name stays declared elsewhere such a
    call would silently bind there instead of failing the build.

    Every failure goes back to the generator with the rule and the concrete
    failure (file:line of each stale reference, the hard blocker and its
    evidence); none holds or demotes a draft.
    """
    from cognalys.extraction.rename_safety import rename_rule_text

    errors: List[Dict] = []
    accepted: set = set()
    production = [
        edit for edit in edits
        if edit.get("edit_kind") != "test"
        and "test" not in str(edit.get("source_file_path", "")).lower()
    ]
    repository = Path(str(error.get("repository_path", "")))
    edits_by_path: Dict[str, List[Dict]] = {}
    for edit in edits:
        edits_by_path.setdefault(str(edit.get("source_file_path", "")), []).append(edit)

    def file_text(relative: str) -> str:
        try:
            return (repository / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    seen: set = set()
    for edit in production:
        path = str(edit.get("source_file_path", ""))
        for old, new in detected_renames(
            str(edit.get("original_code", "")), str(edit.get("corrected_code", "")),
        ):
            if (path, old, new) in seen:
                continue
            seen.add((path, old, new))
            assessment = _rename_assessment(error, path, old)
            references = [item for item in assessment.get("references") or []
                          if isinstance(item, dict)]
            reference_files = sorted({str(item.get("path", "")) for item in references} - {""})
            retry_paths = list(dict.fromkeys([path, *reference_files]))
            hard = _hard_rename_blockers(assessment)
            if hard:
                errors.append({
                    "code": "UNAUTHORIZED_RENAME",
                    "path": path,
                    "details": [f"{old} -> {new}"],
                    "blocked_by": hard,
                    "retry_paths": retry_paths,
                    "hint": (
                        f"{path} renames {old} to {new}, but rename_safety reports "
                        f"hard blockers: {'; '.join(hard[:5])}. Do not rename {old}: "
                        f"add a correctly named function beside it, switch every caller "
                        f"to it, and keep {old} only while callers outside this patch "
                        f"remain. " + rename_rule_text()
                    ),
                })
                continue
            declaring_source = file_text(path)
            if new in declared_function_signatures(declaring_source):
                # The names the retry must avoid and a free one built from
                # the planned name: told only "already declared", RedReader's
                # sha1 drafts picked another taken name (2026-11-03).
                from cognalys.extraction.rename_safety import (
                    declared_names, suggest_free_name)

                taken = (set(assessment.get("names_in_scope") or [])
                         | declared_names(declaring_source)
                         | set(declared_function_signatures(declaring_source)))
                free = suggest_free_name(new, taken)
                errors.append({
                    "code": "RENAME_TARGET_ALREADY_DECLARED",
                    "path": path,
                    "details": [f"{new} is already declared in {path}"],
                    "declared_names": sorted(taken)[:200],
                    "suggested_name": free,
                    "hint": (
                        f"{new} is already declared in {path}. Rename {old} to a "
                        f"name not in declared_names, such as {free}, or, when the "
                        f"existing {new} already computes what {old} should, switch "
                        f"every caller of {old} to it."
                    ),
                })
                continue
            # A new name that still states the removed behaviour is no
            # better than the old one (md5Hash -> legacyMd5Hash, now SHA-256).
            removed = (_behaviour_tokens(str(edit.get("original_code", "")))
                       - _behaviour_tokens(str(edit.get("corrected_code", ""))))
            added = (_behaviour_tokens(str(edit.get("corrected_code", "")))
                     - _behaviour_tokens(str(edit.get("original_code", ""))))
            stated = sorted(token for token in removed if token in _normalised_name(new))
            if stated and added and not any(token in _normalised_name(new) for token in added):
                errors.append({
                    "code": "NAME_CONTRADICTS_BEHAVIOR",
                    "path": path,
                    "name": new,
                    "removed_behavior": stated[0],
                    "message": (f"{new}, the new name of {old}, still states '{stated[0]}' "
                                f"but the function now uses {', '.join(sorted(added))}; "
                                "name it after what it computes"),
                })
                continue
            declaring_edits = edits_by_path.get(path, [])
            family = (len(assessment.get("declarations") or [])
                      or _declaration_count(declaring_source, old))
            remaining = _count_after(
                declaring_source, declaring_edits, lambda text: _declaration_count(text, old))
            if remaining > 0 and family <= 1:
                # The old name is still declared: nothing was renamed away and
                # no reference has to move.
                accepted.add((old, new))
                continue
            if remaining > 0:
                errors.append({
                    "code": "RENAME_PARTIAL_FAMILY",
                    "path": path,
                    "details": [f"{path}: {remaining} of {family} declarations of {old} "
                                "keep the old name"],
                    "retry_paths": retry_paths,
                    "hint": (
                        f"{path} renames one declaration of {old} to {new} and keeps "
                        f"{remaining} other declaration(s) named {old}; a call left on "
                        f"{old} could bind to a remaining overload. Rename every "
                        f"declaration of {old} in {path} to {new} together, or none. "
                        + rename_rule_text()
                    ),
                })
                continue
            stale: List[str] = []
            stale_paths: set = set()
            for relative in reference_files:
                expected = [item for item in references if item.get("path") == relative]
                before_text = file_text(relative)
                before_lines = before_text.splitlines()
                path_edits = edits_by_path.get(relative, [])
                listed = False
                for item in expected:
                    number = int(item.get("line") or 0)
                    line = (before_lines[number - 1].strip()
                            if 0 < number <= len(before_lines) else "")
                    if not line:
                        continue
                    covered = any(line in str(other.get("original_code", ""))
                                  for other in path_edits)
                    kept = any(line in str(other.get("corrected_code", ""))
                               for other in path_edits)
                    if not covered or kept:
                        stale.append(f"{relative}:{number}: {line}")
                        stale_paths.add(relative)
                        listed = True
                moved_away = _reference_count(before_text, old) - _count_after(
                    before_text, path_edits, lambda text: _reference_count(text, old))
                written = _count_after(
                    before_text, path_edits, lambda text: _reference_count(text, new),
                ) - _reference_count(before_text, new)
                if not listed and (moved_away < len(expected) or written < len(expected)):
                    stale.append(
                        f"{relative}: {len(expected)} reference(s) to {old} must move to "
                        f"{new}; the edits move {max(moved_away, 0)} and write "
                        f"{max(written, 0)} reference(s) to {new}")
                    stale_paths.add(relative)
            stale = list(dict.fromkeys(stale))
            if stale:
                elsewhere = [
                    str(item) for item in assessment.get("resolvable") or []
                    if str(item).startswith("same_name_declared_elsewhere")
                ]
                errors.append({
                    "code": "RENAME_CALL_REBINDS" if elsewhere else "RENAME_REFERENCES_NOT_UPDATED",
                    "path": path,
                    "details": stale,
                    "stale_paths": sorted(stale_paths),
                    "retry_paths": retry_paths,
                    "hint": (
                        f"{path} renames {old} to {new}, but these references still name "
                        f"{old}: {'; '.join(stale[:8])}."
                        + (f" {old} stays declared elsewhere ({'; '.join(elsewhere)}), so "
                           "they would bind to that declaration instead of failing the build."
                           if elsewhere else "")
                        + f" Update each of them to {new} in this batch, or keep {old}. "
                        + rename_rule_text()
                    ),
                })
                continue
            accepted.add((old, new))
    return errors, accepted


def rename_only_failure(errors: List[Dict]) -> bool:
    """Whether every error of a failed draft is a rename check
    (_RENAME_ROUTE_CODES: rename_issues' failures and a name left stating
    the removed behaviour).

    Such a draft is never dropped: its last attempt is followed by one more
    on the non-rename route (rename_retry_guidance with last_attempt).
    """
    codes = [str(item.get("code", "")) for item in errors or [] if isinstance(item, dict)]
    return bool(codes) and all(code in _RENAME_ROUTE_CODES for code in codes)


# What a passing build and the behaviour review can answer once the attempt
# on the non-rename route failed a rename check again (see
# rename_check_left_to_review).
_RENAME_CHECKS_LEFT_TO_REVIEW = frozenset({
    "RENAME_REFERENCES_NOT_UPDATED", "RENAME_PARTIAL_FAMILY", "NAME_CONTRADICTS_BEHAVIOR",
})


def rename_check_left_to_review(error: Dict, item: Dict, edits: List[Dict]) -> bool:
    """Whether a rename check the non-rename attempt failed again goes on to
    the behaviour review and the build, as a note for the reviewer.

    A rename question never drops a draft. BlueGate (2026-10-29) failed
    RENAME_REFERENCES_NOT_UPDATED on its non-rename attempt too and became an
    issue. Only what the build or a reader settles passes: a stale reference
    in a file the validation build compiles (a name the patch removed fails
    it there; a test file only once the patch edits it, which is when its
    tests are compiled), a rename that left part of an overload family, and
    a function or variable whose name still states the removed behaviour.
    Never an unauthorized rename, a call that would rebind to a declaration
    elsewhere, a new name already declared, a reference in a source set the
    build does not compile, or an accept-all selector whose branch now
    validates (a question of behaviour, not of a name).
    """
    code = str(item.get("code", ""))
    if code not in _RENAME_CHECKS_LEFT_TO_REVIEW:
        return False
    if code == "NAME_CONTRADICTS_BEHAVIOR":
        return bool(item.get("name") and item.get("removed_behavior"))
    if code == "RENAME_PARTIAL_FAMILY":
        return True
    repository = str(error.get("repository_path") or "")
    paths = [str(path) for path in item.get("stale_paths") or []]
    if not paths or not repository or not Path(repository).is_dir():
        return False
    from cognalys.extraction.rename_safety import (
        _BUILT_TEST_SOURCE_SETS, _built_by_debug_build, _source_set,
    )

    edited = {str(edit.get("source_file_path", "")) for edit in edits or []}
    for path in paths:
        if not _built_by_debug_build(Path(repository), path)[0]:
            return False
        if _source_set(PurePosixPath(path)) in _BUILT_TEST_SOURCE_SETS and path not in edited:
            return False
    return True


def demote_rename_checks_left_to_review(error: Dict, validation: Dict,
                                        edits: List[Dict]) -> Dict:
    """`validation` with its errors turned into warnings when every one is a
    rename check left to review; otherwise `validation` as it was.

    Each warning keeps its details (the remaining name or reference), which
    the preview lists for the operator and the publication verdict notes.
    """
    errors = [item for item in validation.get("errors") or [] if isinstance(item, dict)]
    if (validation.get("status") == "valid" or not errors
            or not all(rename_check_left_to_review(error, item, edits) for item in errors)):
        return validation
    left = [{**item, "details": list(item.get("details") or [item.get("message", "")]),
             "maintainer_decides": True, "rename_check_left_to_review": True}
            for item in errors]
    return {**validation, "status": "valid", "errors": [],
            "warnings": [*(validation.get("warnings") or []), *left]}


def _left_to_review_key(item: Dict) -> tuple:
    return (str(item.get("code", "")), str(item.get("name") or item.get("path") or ""))


def without_rename_checks_left_to_review(error: Dict, recorded: List[Dict],
                                         errors: List[Dict], edits: List[Dict]) -> List[Dict]:
    """`errors` less the rename checks the draft's own validation already left
    to review (`recorded`: its warnings), for the same name or file.

    The port to the base tip and the build repair run the generation's gates
    again on edits that keep the same names: a check the draft passed on to
    review would come back there as an objection, and a port refused over it
    becomes an issue, the ending demote_rename_checks_left_to_review avoids
    (review of D, 2026-10-29: a kept md5Hex now computing SHA-256 was
    refused at the port). Each one still passes only under the same rule.
    """
    left = {_left_to_review_key(item) for item in recorded or []
            if isinstance(item, dict) and item.get("rename_check_left_to_review")}
    if not left:
        return list(errors or [])
    return [item for item in errors or []
            if not (isinstance(item, dict) and _left_to_review_key(item) in left
                    and rename_check_left_to_review(error, item, edits))]


def rename_retry_guidance(errors: List[Dict], generation_paths, last_attempt: bool) -> str:
    """What a retry after a failed rename check is told, beyond each error's hint.

    A reference file outside this change cannot be updated by any retry, and
    a hard blocker does not go away: both mean the non-rename route. So does
    the last attempt, whatever the failure: a draft is never lost to a
    rename question.
    """
    # The rename rule and its non-rename route govern functions. A kept
    # variable or an accept-all selector that still states the removed
    # behaviour takes the extra attempt too, but its own message says what to
    # do: told on the last attempt to "keep the existing declaration's name",
    # a retry for a kept `md5Key` contradicted that message's "rename it
    # (every reference is in the edited code)" (review of D, 2026-10-29).
    rename_errors = [item for item in errors
                     if str(item.get("code", "")) in _RENAME_ROUTE_CODES
                     and item.get("subject") not in _NAMES_OUTSIDE_THE_RENAME_RULE]
    if not rename_errors:
        return ""
    from cognalys.extraction.rename_safety import rename_rule_text

    planned = set(generation_paths or [])
    outside = sorted({
        path for item in rename_errors for path in item.get("retry_paths") or []
        if path not in planned
    })
    hard = any(item.get("code") == "UNAUTHORIZED_RENAME" for item in rename_errors)
    parts = [rename_rule_text()]
    taken = [item for item in rename_errors
             if item.get("code") == "RENAME_TARGET_ALREADY_DECLARED"
             and item.get("suggested_name") and item.get("details")]
    if taken:
        parts.append(
            "A new name must not be one of the error's declared_names: "
            + "; ".join(f"{item['details'][0]}, a free name is {item['suggested_name']}"
                        for item in taken)
            + ".")
    if outside:
        parts.append(
            "These files reference the renamed function but are not part of this "
            f"change: {', '.join(outside)}. Do not rename it; add a correctly named "
            "function and switch the callers in the files of this change.")
    if hard or last_attempt:
        parts.append(
            "Take the non-rename route now: keep the existing declaration's name, add a "
            "correctly named function beside it with the same visibility, switch every "
            "caller in this change to it, and keep the weak-named one only while callers "
            "outside the patch remain.")
    return " ".join(parts)


# `new Interface(...) { ... }` in Java and `object : Interface { ... }` in
# Kotlin: an implementation written inline as an expression.
_ANONYMOUS_BODY_HEAD = re.compile(
    r"\bnew\s+[\w$.]+(?:\s*<[^;{}()]*>)?\s*\([^;{}]*\)\s*\{"
    r"|\bobject\s*:\s*[\w$.]+(?:\s*<[^;{}()]*>)?\s*(?:\([^;{}]*\))?\s*\{",
)


def anonymous_class_spans(code: str) -> List[tuple]:
    """Character ranges covered by anonymous-class bodies in `code`.

    A method declared inside one of these is not part of the enclosing
    file's API surface: it implements the interface or superclass being
    instantiated, and it lives only as long as that expression does.
    Callers use this to tell "this file stopped offering a public method"
    apart from "an inline implementation was deleted, which is the fix".
    """
    masked = blank_comments_and_literals(code)
    spans: List[tuple] = []
    for head in _ANONYMOUS_BODY_HEAD.finditer(masked):
        opening = head.end() - 1
        depth = 0
        for index in range(opening, len(masked)):
            character = masked[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    spans.append((opening, index))
                    break
        else:
            spans.append((opening, len(masked)))
    return spans


def _is_test_edit(edit: Dict) -> bool:
    return (edit.get("edit_kind") == "test"
            or is_test_source(str(edit.get("source_file_path", ""))))


_PRIVATE_KOTLIN_FUNCTION = re.compile(
    r"\bprivate\s+(?:[\w]+\s+)*fun\s+(?:<[^>]+>\s*)?(?:[\w?.<>]+\.)?(\w+)\s*\("
)
_PRIVATE_JAVA_METHOD = re.compile(
    r"\bprivate\s+(?:(?:static|final|synchronized|native)\s+)*"
    r"[\w$.<>\[\],? ]+?\s+(\w+)\s*\("
)
_ANY_FUNCTION = re.compile(
    r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w?.<>]+\.)?(\w+)\s*\("
    r"|^\s*(?:(?:public|protected|static|final|synchronized|abstract)\s+)+"
    r"[\w$.<>\[\],? ]+?\s+(\w+)\s*\(",
    re.MULTILINE,
)


def private_member_calls_in_tests(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Test edits that call a method the production class declares private.

    wifianalyzer's generated test called
    AccessPointsAdapterData().calculateChildType(), a private function: the
    test source set did not compile, and the validation could only report it
    after a full Gradle build. Only calls on a receiver of that class are
    read -- a variable initialised with its constructor, or the constructor
    call itself -- so a same-named method of another type is never flagged.
    """
    repo_root = Path(str(error.get("repository_path", "")))
    production_paths = {
        str(edit.get("source_file_path", "")) for edit in edits
        if not _is_test_edit(edit) and edit.get("source_file_path")
    }
    if error.get("source_file_path"):
        production_paths.add(str(error["source_file_path"]))
    overlay = _coordinated_overlay(repo_root, [
        edit for edit in edits if not _is_test_edit(edit)
    ])
    private_by_class: Dict[str, set] = {}
    for relative in production_paths:
        source = overlay.get(relative)
        if source is None:
            try:
                source = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        source = blank_comments_and_literals(source)
        private = set(_PRIVATE_KOTLIN_FUNCTION.findall(source))
        private |= set(_PRIVATE_JAVA_METHOD.findall(source))
        # An overload with wider visibility makes the call legal.
        visible = set()
        for match in _ANY_FUNCTION.finditer(source):
            name = match.group(1) or match.group(2)
            start = source.rfind("\n", 0, match.start()) + 1
            if name and not re.search(r"\bprivate\b", source[start:match.start() + 1]):
                visible.add(name)
        private -= visible
        if private:
            private_by_class[Path(relative).stem] = private
    issues: List[Dict] = []
    for edit in edits:
        if not _is_test_edit(edit) or not private_by_class:
            continue
        code = blank_comments_and_literals(str(edit.get("corrected_code", "")))
        found = []
        for class_name, names in private_by_class.items():
            receivers = set(re.findall(
                rf"\b(?:val|var)\s+(\w+)\s*(?::\s*{re.escape(class_name)}\s*)?="
                rf"\s*{re.escape(class_name)}\s*\(", code,
            ))
            receivers |= set(re.findall(
                rf"\b{re.escape(class_name)}\s+(\w+)\s*=\s*new\s+"
                rf"{re.escape(class_name)}\s*\(", code,
            ))
            receiver = "|".join(
                [rf"\b{re.escape(name)}" for name in receivers]
                + [rf"\b{re.escape(class_name)}\s*\([^()]*\)"]
            )
            for name in sorted(names):
                if re.search(rf"(?:{receiver})\s*\.\s*{re.escape(name)}\s*\(", code):
                    found.append(f"{class_name}.{name}")
        if found:
            issues.append({
                "code": "TEST_CALLS_PRIVATE_MEMBER",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    "the test calls a member the production class declares "
                    "private, so the test source set cannot compile: "
                    + ", ".join(found)
                    + ". Exercise it through the class's non-private behaviour "
                    "instead of widening its visibility for the test."
                ],
            })
    return issues


# A line that only casts a value and discards it: `x as T`, `x as? T`.
_DISCARDED_CAST = re.compile(
    r"^(?!(?:val|var|return|if|else|when|throw|import|package)\b)"
    r"[^=]*?\S\s+as\??\s+[\w.<>?, ]+\s*;?\s*$"
)
# A line that carries on the statement above it, or that the next line carries
# on: its value is used, so a cast on it is not discarded.
_CONTINUES_PREVIOUS = re.compile(
    r"^(?:\?\.|\?:|\.|as\??\s|&&|\|\||[+\-*/%<>]|==|!=|(?:and|or|xor)\s)")
_CONTINUED_BY_NEXT = re.compile(r"^(?:\?\.|\?:|\.|\)|&&|\|\||[+\-*/%<>]|==|!=)")
_OPEN_ENDING = ("=", "(", ",", "->", ".", "?:", "&&", "||", "+")


def _continued_lines(lines: List[str], index: int) -> bool:
    """Is line `index` part of a statement that spans several lines?

    Tasks' `val trustManager = trustManagerFactory.trustManagers` went on
    with `.single { it is X509TrustManager } as X509TrustManager`, and Ursa's
    `peerCertificates.map { certificate ->` with `certificate as?
    X509Certificate` and then `?: throw ...` (2026-10-25): both casts were
    read as discarded. A plain `{` above is not a continuation: tokn's cast
    right after `fun f() {` is still a statement of its own.
    """
    line = lines[index].strip()
    previous = next((item.strip() for item in reversed(lines[:index]) if item.strip()), "")
    following = next((item.strip() for item in lines[index + 1:] if item.strip()), "")
    return bool(
        _CONTINUES_PREVIOUS.match(line)
        or (previous.endswith(_OPEN_ENDING) and not previous.endswith("++"))
        or _CONTINUED_BY_NEXT.match(following)
    )


def statements_without_effect(edits: List[Dict]) -> List[Dict]:
    """Production lines a patch adds that compute a value and discard it.

    tokn's KeystoreManager gained `keystore.getEntry(KEY_ALIAS, null) as
    KeyStore.SecretKeyEntry` on a line of its own: the cast result went
    nowhere, and the line existed only so the detector would observe the call
    order it expects. A statement whose only effect is a discarded cast
    changes no behaviour a maintainer can see and should not be proposed.
    Statements, not physical lines, are judged (_continued_lines).
    """
    issues: List[Dict] = []
    for edit in edits:
        if _is_test_edit(edit):
            continue
        original_lines = {
            line.strip() for line in blank_comments_and_literals(
                str(edit.get("original_code", ""))
            ).splitlines()
        }
        lines = blank_comments_and_literals(str(edit.get("corrected_code", ""))).splitlines()
        added = [
            line.strip()
            for index, line in enumerate(lines)
            if line.strip() and line.strip() not in original_lines
            and _DISCARDED_CAST.match(line.strip())
            and not _continued_lines(lines, index)
        ]
        if added:
            issues.append({
                "code": "STATEMENT_WITHOUT_EFFECT",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    "added statement(s) cast a value and discard it, changing "
                    "no behaviour: " + " | ".join(added[:4])
                ],
            })
    return issues


# `try { receiver.call(args) } catch (...) { }` -- one bare call, its result
# unused, and a catch with nothing in it (comments are blanked first).
_SWALLOWED_BARE_CALL = re.compile(
    r"\btry\s*\{\s*(?P<call>[\w$.]+(?:\([^;{}]*\))?\s*\.\s*(?P<name>[\w$]+)"
    r"\s*\([^;{}]*\))\s*;?\s*\}\s*catch\s*\([^)]*\)\s*\{\s*\}",
)


def discarded_governed_calls(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Calls to the governed API a patch adds only for the detector to see.

    leekleak/traffic-light's KeyStore order finding (exp_final_20261019) was
    "fixed" with `try { keyStore.getKey(key, null) } catch (_: Exception) {}`
    added after the existing getEntry read: the key it returns goes nowhere
    and any failure is swallowed, so the line changes nothing the app does
    and exists only so the call sequence matches the rule. The equivalent
    compliant read -- return what getKey returns -- was available and is
    what tokn's patch did.

    Flagged: a production try block the patch adds whose only statement is a
    call to a method the specification's own events govern, whose result is
    not used, and whose catch is empty. A side-effect call outside the
    governed API (file.delete()) is not this gate's business.
    """
    governed = set(governed_call_names(error.get("specification_context")))
    if not governed:
        return []
    issues: List[Dict] = []
    for edit in edits:
        if _is_test_edit(edit):
            continue
        original = re.sub(r"\s+", " ", blank_comments_and_literals(
            str(edit.get("original_code", ""))))
        corrected = blank_comments_and_literals(str(edit.get("corrected_code", "")))
        found = [
            re.sub(r"\s+", " ", match.group("call")).strip()
            for match in _SWALLOWED_BARE_CALL.finditer(corrected)
            if match.group("name") in governed
            and re.sub(r"\s+", " ", match.group(0)) not in original
        ]
        if found:
            issues.append({
                "code": "GOVERNED_CALL_RESULT_DISCARDED",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    "the patch adds a call to the governed API whose result is "
                    "unused and whose exceptions are swallowed, so it changes "
                    "nothing the application does and only makes the detector "
                    "observe the call: " + " | ".join(found[:3])
                    + ". Use the compliant call for what the code actually "
                    "needs (its result replaces the non-compliant one), or "
                    "decide that no fix changes behaviour."
                ],
            })
    return issues


# A construction standing as a statement of its own. Java: `new T(...)` right
# after `;`, `{` or `}` (or at the start of the edit). Kotlin: `T(...)` at the
# start of a line or right after `;`, T capitalised and optionally qualified.
_JAVA_STATEMENT_NEW = re.compile(
    r"(?:\A|(?<=[;{}]))\s*(?P<statement>new\s+"
    r"(?P<type>(?:[A-Za-z_$][\w$]*\s*\.\s*)*[A-Za-z_$][\w$]*)"
    r"\s*(?:<[^;(){}]*>)?\s*\()"
)
_KOTLIN_STATEMENT_CALL = re.compile(
    r"(?m)(?:^|(?<=;))[ \t]*(?P<statement>"
    r"(?P<type>(?:[A-Za-z_]\w*\.)*[A-Z]\w*)[ \t]*(?:<[^;(){}=\n]*>)?[ \t]*\()"
)
# What the next line may start with when it carries on the expression above:
# a receiver, an operator, a trailing lambda, the end of the enclosing block
# (the construction is then the block's value) or the rest of an if/try.
_KOTLIN_CONTINUATION = re.compile(
    r"^(?:\?\.|\?:|\.|[)\]}{]|&&|\|\||[+\-*/%<>=!:,]|as\??\b|is\b|in\b"
    r"|else\b|catch\b|finally\b)"
)
# A line that heads a branch whose single statement follows it: the
# construction is then the branch's value, maybe of an if used as a value.
_KOTLIN_BRANCH_HEAD = re.compile(
    r"^(?:\}\s*)?(?:else\s+)?(?:if|while|for|when)\s*\(.*\)$"
    r"|^(?:\}\s*)?(?:else|try|do)$"
)
# A line whose expression goes on to the next one: it ends in a keyword that
# takes an operand (`... else`, `return`, `throw`), in an infix call (`a to`)
# or in an annotation of the expression below.
_KOTLIN_OPEN_TAIL = re.compile(
    r"(?:^|[^\w.])(?:else|return|throw|in|is|as)$"
    r"|[\w)\]]\s+[a-z_]\w*$"
    r"|@[\w.]+(?:\([^()]*\))?$"
)
# The opening of a block whose exceptions are caught: a construction there
# may exist for what it throws (`try { new URL(s); return true; } catch ...`).
_CATCHING_BLOCK_OPENER = re.compile(
    r"(?:\btry\s*(?:\([^{}]*\))?|\brunCatching(?:\s*<[^{}]*>)?)\s*$"
)
_PLATFORM_PACKAGE = re.compile(r"^(?:java|javax|android)\.")


def _governed_simple_names(error: Dict) -> set:
    """Simple names of the types the finding's specification governs."""
    from cognalys.llm.source_facts import governed_api_types

    names = {value.rsplit(".", 1)[-1].rsplit("$", 1)[-1]
             for value in governed_api_types(error.get("specification_context"))}
    return {name for name in names if name}


def _names_a_class(type_text: str, context: str, governed: set) -> bool:
    """Whether `T(...)` is known to construct a class built for its value only.

    Kotlin spells a constructor call like any capitalised function call:
    Compose's `Text(...)`, `Column { }` and factory functions look the same.
    And a class of the app's own may do its work in its constructor (an
    `init { app.register(this) }`, in Java too), so a class declared in the
    repository is no proof. Only these count: a platform qualifier, an
    explicit import from java./javax./android. (packages that hold no Kotlin
    functions), or a type the specification governs.
    """
    segments = type_text.split(".")
    if segments[0][:1].islower():
        return bool(_PLATFORM_PACKAGE.match(type_text))
    outer = re.escape(segments[0])
    return bool(
        segments[0] in governed
        or re.search(r"(?m)^\s*import\s+(?:java|javax|android)\.[\w.]*\b" + outer
                     + r"\s*;?\s*$", context)
        or re.search(r"(?m)^\s*import\s+(?:java|javax|android)\.[\w.]+\s+as\s+" + outer
                     + r"\b", context)
    )


def _inside_catching_block(text: str, position: int) -> bool:
    """Whether `position` of the blanked `text` sits in a try (or runCatching) block."""
    depth = 0
    for index in range(position - 1, -1, -1):
        if text[index] == "}":
            depth += 1
        elif text[index] == "{":
            if depth:
                depth -= 1
            elif _CATCHING_BLOCK_OPENER.search(text[max(0, index - 300):index]):
                return True
    return False


def _kotlin_line_opens_next(line: str) -> bool:
    """Whether the expression on `line` carries on into the next line."""
    return bool(
        (line.endswith(_OPEN_ENDING + (":", "-", "*", "/", "%", "!"))
         and not line.endswith(("++", "--")))
        or _KOTLIN_BRANCH_HEAD.match(line)
        or _KOTLIN_OPEN_TAIL.search(line)
    )


def _discarded_construction_statements(code: str, language: str, context: str,
                                       governed: set,
                                       preceding: Optional[str] = "") -> List[str]:
    """Each construction in `code` whose value no expression uses.

    `code` has its comments and literals blanked. `preceding` is the blanked
    file text before it ("" at the start of a file, None when unknown): an
    edit can start on the second line of `x =\\n    new T(...)`, so a
    construction opening the edit is judged by the line before it, and
    skipped when that line is unknown. Anything that could take the value --
    an assignment, a return, a throw, an argument, a receiver, the last
    expression of a block, a branch -- is left out, and so is a construction
    inside a try block, which may be there for the exception it throws: when
    unsure, a statement is not reported.
    """
    found: List[str] = []
    text = (preceding or "") + code
    offset = len(preceding or "")
    if language == "java":
        for match in _JAVA_STATEMENT_NEW.finditer(code):
            start = match.start("statement")
            if not code[:start].strip():
                head = None if preceding is None else preceding.rstrip()
                if head is None or (head and head[-1] not in ";{}"):
                    continue
            close = _closing_bracket(code, match.end() - 1)
            if close is None or not code[close + 1:].lstrip().startswith(";"):
                continue
            if (_inside_catching_block(text, offset + start)
                    or not _names_a_class(re.sub(r"\s+", "", match.group("type")),
                                          context, governed)):
                continue
            found.append(re.sub(r"\s+", " ", code[start:close + 1]))
        return found
    for match in _KOTLIN_STATEMENT_CALL.finditer(code):
        close = _closing_bracket(code, match.end() - 1)
        if close is None:
            continue
        line_end = code.find("\n", close)
        tail = code[close + 1:len(code) if line_end < 0 else line_end].strip()
        if tail and not tail.startswith(";"):
            continue
        if not tail:
            following = code[close + 1:].lstrip()
            # Nothing after it in the edit: the enclosing block may end here
            # and make it the block's value.
            if not following or _KOTLIN_CONTINUATION.match(following):
                continue
        start = match.start("statement")
        line_start = code.rfind("\n", 0, start) + 1
        if not code[line_start:start].strip():
            inside = [item.strip() for item in code[:line_start].splitlines() if item.strip()]
            if inside:
                previous = inside[-1]
            elif preceding is None:
                continue
            else:
                previous = next((item.strip() for item in reversed(preceding.splitlines())
                                 if item.strip()), "")
            if _kotlin_line_opens_next(previous):
                continue
        if (_inside_catching_block(text, offset + start)
                or not _names_a_class(match.group("type"), context, governed)):
            continue
        found.append(re.sub(r"\s+", " ", code[start:close + 1]))
    return found


def discarded_constructions(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Objects a patch constructs as statements of their own and never uses.

    BlueGate's KeyPairSpec finding (2026-10-29), "no construction of the key
    pair was observed" at the `keyPair.public` read of a KeyPair that
    KeyPairGenerator.generateKeyPair() returned, was answered with
    `KeyPair(generatedKeyPair.public, generatedKeyPair.private)` on a line of
    its own before `return generatedKeyPair`, plus `keyPair.getPublic()` for
    `keyPair.public` (the same JVM call). The new KeyPair went nowhere: the
    object reaching the reported call was the one the generator made, and the
    line existed only so the runtime checker would see a constructor. The
    behaviour review even wrote that the result is discarded, yet counted the
    added call as an effect, and the draft built and was ready to publish.

    Flagged, in a production edit: a statement the patch adds that is only a
    construction -- Java `new T(...);`, Kotlin `T(...)` -- of a type the
    specification governs or of a platform class (_names_a_class), whose
    value is not assigned, returned, thrown, passed, used as a receiver or
    left as a block's value, outside any try block. A construction the
    original code already had as such a statement, in any edit of the patch,
    is the code's own (moved, not added).
    """
    governed = _governed_simple_names(error)
    repo_root = Path(str(error.get("repository_path", "") or ""))
    overlay = (_coordinated_overlay(repo_root, edits)
               if repo_root.parts and repo_root.is_dir() else {})
    judged = []
    for edit in edits:
        if not isinstance(edit, dict) or _is_test_edit(edit):
            continue
        path = str(edit.get("source_file_path") or edit.get("source_file") or "")
        language = {".java": "java", ".kt": "kotlin", ".kts": "kotlin"}.get(
            Path(path).suffix.lower(), "")
        if language:
            judged.append((edit, path, language))
    contexts: Dict[str, str] = {}
    for _, path, _ in judged:
        # Imports and declarations sit outside the edited method: read them
        # from the file as the patch leaves it, and from every edit of it.
        contexts.setdefault(path, blank_comments_and_literals("\n".join([
            overlay.get(path, ""), *(
                str(item.get(key, "") or "") for item in edits if isinstance(item, dict)
                and str(item.get("source_file_path") or item.get("source_file") or "") == path
                for key in ("original_code", "corrected_code"))])))
    before: List[str] = []
    for edit, path, language in judged:
        before += _discarded_construction_statements(
            blank_comments_and_literals(str(edit.get("original_code", "") or "")),
            language, contexts[path], governed)
    issues: List[Dict] = []
    for edit, path, language in judged:
        corrected = str(edit.get("corrected_code", "") or "")
        preceding: Optional[str] = None
        if edit.get("edit_kind") == "create":
            preceding = ""
        elif path in overlay and corrected.strip():
            index = overlay[path].find(corrected.strip())
            if index >= 0:
                preceding = blank_comments_and_literals(overlay[path])[:index]
        added = []
        for statement in _discarded_construction_statements(
                blank_comments_and_literals(corrected), language, contexts[path],
                governed, preceding):
            if statement in before:
                before.remove(statement)
            else:
                added.append(statement)
        if added:
            issues.append({
                "code": "DISCARDED_CONSTRUCTION",
                "path": path,
                "details": [
                    "added statement(s) construct an object and discard it: "
                    + " | ".join(added[:3]),
                    "An object constructed and discarded changes nothing the "
                    "program uses; the runtime checker's finding concerns the "
                    "object that reaches the reported call -- change that "
                    "object or how it is produced, or leave the code as it is.",
                ],
            })
    return issues


# An extension point of the Java crypto and TLS architecture: an engine's
# service provider interface (TrustManagerFactorySpi, CipherSpi), a Provider,
# or an engine or socket factory class. Platform only: the supertype must be
# qualified by, or imported from, one of _JCA_PACKAGES.
_EXTENSION_POINT_NAME = re.compile(r"^(?:[A-Z][\w$]*Spi|Provider|[A-Z][\w$]*Factory)$")
_JCA_PACKAGES = r"(?:java\.security|javax\.crypto|javax\.net|javax\.security)"
_TYPE_DECLARATION = re.compile(
    r"\b(?:class|object|interface)\s+(?P<name>[A-Za-z_$][\w$]*)")
_PLAIN_IDENTIFIER = re.compile(r"(?<![\w$.])(?P<name>[a-z_][\w$]*)\b(?!\s*[(.])")
_THIS_MEMBER = re.compile(r"\bthis\s*\.\s*(?P<name>[a-z_][\w$]*)\b(?!\s*[(.])")
_CONSTRUCTION = re.compile(r"(?:\bnew\s+)?(?<![\w$.])(?P<type>[A-Z][\w$]*)\s*(?:<[^;(){}]*>)?\s*\(")
_ASSIGNED_NAME = re.compile(
    r"\b(?:val|var)\s+(?P<kotlin>[A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=(?!=)"
    r"|(?:^|[;{}(])\s*(?:final\s+)?[\w$.<>\[\],?]+\s+(?P<java>[A-Za-z_$][\w$]*)\s*=(?!=)",
    re.MULTILINE,
)
_NOT_VALUES = frozenset({"null", "true", "false", "this", "it", "new", "as", "is", "in",
                         "super", "return"})


def _declared_types(code: str) -> List[tuple]:
    """(name, supertypes, declaration text) of each class or object `code` declares.

    The supertypes are read from a Kotlin `: A(...), B` list or a Java
    `extends A implements B`; the text runs from the keyword to the end of
    the body, or of the header when there is none (`class F : TrustManagerFactory(spi,
    provider, "x")`).
    """
    found = []
    for match in _TYPE_DECLARATION.finditer(code):
        index = match.end()
        if code[index:index + 1] == "<":
            index = (_closing_bracket(code.replace("<", "(").replace(">", ")"), index) or index) + 1
        rest = code[index:]
        stripped = rest.lstrip()
        index += len(rest) - len(stripped)
        if stripped.startswith("("):
            # A Kotlin primary constructor, possibly with modifiers before it.
            index = (_closing_bracket(code, index) or index) + 1
        supertypes, end = _supertypes_and_end(code, index)
        found.append((match.group("name"), supertypes, code[match.start():end]))
    return found


# An object expression, `object : TrustManagerFactorySpi() { ... }`, and a
# Java anonymous class, `new Provider("x", 1.0, "y") { }`.
_ANONYMOUS_KOTLIN = re.compile(r"(?<![\w$])object\s*(?=:)")
_ANONYMOUS_JAVA = re.compile(
    r"\bnew\s+(?P<type>(?:[A-Za-z_$][\w$]*\s*\.\s*)*[A-Z][\w$]*)\s*(?:<[^;(){}]*>)?\s*\(")


def _anonymous_types(code: str) -> List[tuple]:
    """(start, end, supertypes) of each anonymous object or class `code` declares, by start."""
    found = []
    for match in _ANONYMOUS_KOTLIN.finditer(code):
        supertypes, end = _supertypes_and_end(code, match.end())
        if supertypes:
            found.append((match.start(), end, supertypes))
    for match in _ANONYMOUS_JAVA.finditer(code):
        close = _closing_bracket(code, match.end() - 1)
        body = re.match(r"\s*\{", code[close + 1:]) if close is not None else None
        if body:
            brace = close + 1 + body.end() - 1
            end = (_closing_bracket(code, brace) or len(code) - 1) + 1
            found.append((match.start(), end, [re.sub(r"\s+", "", match.group("type"))]))
    return sorted(found)


def _supertypes_and_end(code: str, index: int) -> tuple:
    """The supertypes a declaration header from `index` lists, and where its body ends."""
    header_end = index
    depth = 0
    while header_end < len(code):
        character = code[header_end]
        if character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif depth == 0 and (character in "{;" or (character == "\n" and not re.search(
                r"[:,(]\s*$|\b(?:extends|implements)\s*$", code[index:header_end]))):
            break
        header_end += 1
    supertype_text = code[index:header_end]
    supertypes: List[str] = []
    kotlin = re.match(r"\s*:\s*(.*)", supertype_text, re.DOTALL)
    java = re.search(r"\b(?:extends|implements)\b(.*)", supertype_text, re.DOTALL)
    listed = kotlin.group(1) if kotlin else (
        re.sub(r"\b(?:extends|implements)\b", ",", java.group(0)) if java else "")
    for part in re.split(r",(?![^(<]*[)>])", listed):
        qualified = re.match(r"\s*((?:[A-Za-z_$][\w$]*\s*\.\s*)*[A-Za-z_$][\w$]*)", part)
        if qualified and qualified.group(1).strip():
            supertypes.append(re.sub(r"\s+", "", qualified.group(1)))
    end = header_end
    if code[header_end:header_end + 1] == "{":
        end = (_closing_bracket(code, header_end) or len(code) - 1) + 1
    return supertypes, end


def _is_jca_extension_point(supertype: str, context: str) -> bool:
    return _is_jca_type(supertype, context, _EXTENSION_POINT_NAME)


def _is_jca_type(supertype: str, context: str, simple_names: "re.Pattern") -> bool:
    """Whether `supertype` is a platform crypto/TLS type whose simple name matches `simple_names`."""
    simple = supertype.rsplit(".", 1)[-1]
    if not simple_names.match(simple):
        return False
    if "." in supertype:
        return bool(re.match(_JCA_PACKAGES + r"\.", supertype))
    if re.search(r"(?m)^\s*import\s+" + _JCA_PACKAGES + r"\.[\w.]*\b"
                 + re.escape(simple) + r"\s*;?\s*$", context):
        return True
    # `import javax.net.ssl.*` covers it too, unless the code seen declares a
    # class of that name (review of 2026-10-29: the ICSdroid classes under a
    # wildcard import were not flagged). The other two conditions of
    # wrapped_same_objects still have to hold for a class of the app's own
    # package that needs no import.
    return bool(re.search(r"(?m)^\s*import\s+" + _JCA_PACKAGES + r"(?:\.\w+)*\.\*\s*;?\s*$",
                          context)
                and not re.search(r"\b(?:class|interface|object)\s+" + re.escape(simple) + r"\b",
                                  context))


def _mentions(text: str, names) -> bool:
    return any(re.search(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", text)
               for name in names if name)


def _calls_of(code: str, name: str) -> List[str]:
    """The argument text of every call of `name` in `code`, member or bare."""
    found = []
    for match in re.finditer(r"(?<![\w$])" + re.escape(name) + r"\s*\(", code):
        if re.search(r"\bfun\s+$|\b(?:void|[A-Z][\w$<>\[\]]*)\s+$", code[:match.start()]):
            continue  # a declaration of `name`, not a call
        close = _closing_bracket(code, match.end() - 1)
        if close is not None:
            found.append(code[match.end():close])
    return found


def _function_bodies(code: str) -> List[tuple]:
    """(name, body text) of each function or method blanked `code` declares with a body.

    A Kotlin expression body (`fun f(x) = expr`) runs to the end of its
    statement; a block body to its closing brace. An abstract member has none.
    """
    found = []
    for match in _KEY_FUNCTION.finditer(code):
        name = match.group("kotlin") or match.group("java")
        if (name in _NOT_DECLARATION_NAMES
                or (match.group("java") and match.group("type") in _NOT_JAVA_RETURN_TYPES)):
            continue
        close = _closing_bracket(code, match.end() - 1)
        if close is None:
            continue
        body = re.search(r"[{=;\n]", code[close + 1:close + 300])
        if not body or body.group() in {";", "\n"}:
            continue
        opening = close + 1 + body.start()
        end = (_closing_bracket(code, opening) if body.group() == "{"
               else _statement_end(code, opening))
        if end is not None:
            found.append((name, code[opening + 1:end]))
    return found


def _supplier_names(code: str, types: set) -> set:
    """Names `code` binds to a lambda or function whose body constructs one of `types`.

    Herald's mtlsEngine (frozen run, 2026-11-13): `val keyManager =
    MtlsKeyManager(...)` became `val createKeyManager = { MtlsKeyManager(...) }`,
    and only that lambda reached the app-written Provider and Spi, so the
    object the original passed never appeared in them by name.
    """
    if not types:
        return set()
    construction = re.compile(r"(?<![\w$.])(?:new\s+)?(?:" + "|".join(
        re.escape(item) for item in sorted(types)) + r")\s*(?:<[^;(){}]*>)?\s*\(")
    found = set()
    for match in _ASSIGNED_NAME.finditer(code):
        name = match.group("kotlin") or match.group("java")
        expression = code[match.end():_statement_end(code, match.end())]
        if (name and construction.search(expression)
                and re.match(r"\s*(?:\{|\([^()]*\)\s*->|[A-Za-z_$][\w$]*\s*->)", expression)):
            found.add(name)
    for name, body in _function_bodies(code):
        if construction.search(body):
            found.add(name)
    return found


def _hands_out(code: str, name: str) -> bool:
    """Whether blanked `code` returns or assigns `name` itself, or makes it an element it gives.

    `= arrayOf(keyManager)`, `return manager;`, `fun get() = manager`,
    `new KeyManager[]{manager}`: the value leaves whole. `return
    backing.getKey(alias, password)` only consults `backing` and `password`,
    and `this.backing = backing` keeps it for that.
    """
    value = r"(?<![\w$.])" + re.escape(name) + r"\b(?!\s*[.(\w$\[])"
    return bool(re.search(
        r"(?:\breturn\s+|\)\s*(?::[^=;(){}]+)?=(?!=)\s*)" + value
        + r"|(?:\b(?:arrayOf|listOf|setOf|mutableListOf)\s*(?:<[^;(){}]*>)?\s*\("
        r"|\[\s*\]\s*\{)(?:[^;(){}]*,)?\s*" + value, code))


def wrapped_same_objects(error: Dict, edits: List[Dict]) -> List[Dict]:
    """An app-written JCA factory or provider whose product is the object the code already passed.

    ICSdroid's SSLContextSpec finding (run_v2, 2026-10-29), an app-defined
    trust manager passed to SSLContext.init, was answered with three private
    classes -- a TrustManagerFactorySpi, a Provider and a TrustManagerFactory
    subclass -- whose engineGetTrustManagers returns the same CustomCertManager
    the code built before; init now received `trustManagerFactory.trustManagers`.
    The behaviour review called it "a substantive provenance repair" and the
    draft was counted ready, although the object that reaches init, and what
    it trusts, is the one it always was: the factory exists only so the rule
    stops matching. Tasks.org's openConnection (2026-10-25) did the same with
    a Provider registered for TrustManagerFactory.getInstance.

    Flagged, all of: a production edit declares a class that is new in the
    patch and extends an extension point (_is_jca_extension_point); an object
    the original code passed to a call the specification governs (a variable,
    or the class that built it) appears in that class, in a class it names,
    or in the arguments of a call on it; and that governed call now receives a
    value derived from the new class. An anonymous object or class extending
    an extension point counts as such a class. A patch that obtains a platform
    factory (TrustManagerFactory.getInstance(...).init(keyStore)) declares no
    class, and a test file is not judged.

    Herald's mtlsEngine (frozen run, 2026-11-13; two drafts reached PR pronto)
    put the factory behind a helper, `factory = hostAwareKeyManagerFactory(
    createKeyManager)` with `fun hostAwareKeyManagerFactory(...) =
    KeyManagerFactory.getInstance(x, HostAwareKeyManagerProvider(...))`, and
    passed the Provider a lambda building the MtlsKeyManager the original
    passed. So a function whose body constructs or returns a new class counts
    as derived from it, and a lambda or function that constructs the class of
    a passed object (_supplier_names) counts as that object. When the governed
    call reaches the class only through such a function, the class's product
    must be the app's own object -- a class the original constructed and
    passed, its supplier, or a passed value the class hands out whole
    (_hands_out: agent_baseline's `engineGetKeyManagers() =
    arrayOf(keyManager)`) -- and not a value it only consults: a platform
    KeyManagerFactory initialised over an app-written KeyStoreSpi view of the
    same key store (shellyelevatev2, 2026-11-14) makes its own key managers.
    """
    governed = [name for name in governed_call_names(error.get("specification_context"))
                if name.isidentifier()]
    production = [edit for edit in edits if isinstance(edit, dict) and not _is_test_edit(edit)
                  and Path(str(edit.get("source_file_path") or "")).suffix.lower()
                  in {".java", ".kt", ".kts"}]
    if not governed or not production:
        return []
    repo_root = Path(str(error.get("repository_path", "") or ""))
    files_before: Dict[str, str] = {}
    for edit in production:
        path = str(edit.get("source_file_path") or "")
        if path not in files_before:
            try:
                files_before[path] = blank_comments_and_literals(
                    (repo_root / path).read_text(encoding="utf-8", errors="replace")
                ) if repo_root.parts and edit.get("edit_kind") != "create" else ""
            except OSError:
                files_before[path] = ""
    before = "\n".join([*files_before.values(), *(
        blank_comments_and_literals(str(edit.get("original_code", "") or ""))
        for edit in production)])
    pieces = [blank_comments_and_literals(str(edit.get("corrected_code", "") or ""))
              for edit in production]
    after = "\n".join(pieces)
    # Imports may sit outside the edits: read them from every version seen.
    context = before + "\n" + after
    declared_before = {match.group("name") for match in _TYPE_DECLARATION.finditer(before)}
    new_types = [item for item in _declared_types(after) if item[0] not in declared_before]
    wrappers = [item for item in new_types
                if any(_is_jca_extension_point(supertype, context) for supertype in item[1])]
    # The same classes written anonymously (`object : TrustManagerFactorySpi()
    # { ... }`, `new Provider(...) { }`) are what a regeneration told about
    # the named ones reaches for (review of 2026-10-29). Each outermost one
    # new in the patch gets a name, and the text building it is replaced by
    # that name, so what it flows into is followed as for a named class.
    flat_before = " ".join(before.split())
    anonymous: List[tuple] = []
    for start, end, supertypes in _anonymous_types(after):
        if (any(_is_jca_extension_point(supertype, context) for supertype in supertypes)
                and " ".join(after[start:end].split()) not in flat_before
                and not any(start < kept[1] and kept[0] < end for kept in anonymous)):
            anonymous.append((start, end, supertypes, f"AnonymousExtensionPoint{len(anonymous)}"))
    shown = {item[0]: item[0] for item in wrappers}
    for start, end, supertypes, synthetic in anonymous:
        wrappers.append((synthetic, supertypes, after[start:end]))
        new_types.append((synthetic, supertypes, after[start:end]))
        shown[synthetic] = "an anonymous " + supertypes[0].rsplit(".", 1)[-1]
    offsets, position = [], 0
    for edit, piece in zip(production, pieces):
        offsets.append((position, position + len(piece), str(edit.get("source_file_path", ""))))
        position += len(piece) + 1
    anonymous_paths = {path for low, high, path in offsets
                       for start, _, _, _ in anonymous if low <= start < high}
    for start, end, _, synthetic in reversed(anonymous):
        after = after[:start] + synthetic + after[end:]
    if not wrappers:
        return []
    # What the original code passed to each governed call.
    passed: Dict[str, set] = {}
    passed_types: Dict[str, set] = {}
    for name in governed:
        for arguments in _calls_of(before, name):
            values = ({match.group("name") for match in _PLAIN_IDENTIFIER.finditer(arguments)}
                      | {match.group("name") for match in _THIS_MEMBER.finditer(arguments)}
                      ) - _NOT_VALUES
            types = {match.group("type") for match in _CONSTRUCTION.finditer(arguments)}
            for value in list(values):
                for assigned in re.finditer(
                        r"\b(?:val|var)\s+" + re.escape(value)
                        + r"\b\s*(?::\s*(?P<declared>[A-Z][\w$]*)[^=\n]*)?(?:=\s*(?:new\s+)?"
                        r"(?P<kotlin>[A-Z][\w$]*)\s*[(<])?"
                        r"|\b(?P<java_type>[A-Z][\w$]*)(?:<[^;=]*>)?\s+" + re.escape(value)
                        + r"\s*=\s*new\s+(?P<java>[A-Z][\w$]*)", before):
                    types |= {group for group in assigned.groupdict().values() if group}
            # A platform class is not an object the app built (SecureRandom()).
            types = {item for item in types if not _names_a_class(item, context, set())}
            if values or types:
                passed.setdefault(name, set()).update(values | types)
                passed_types.setdefault(name, set()).update(types)
    if not passed:
        return []

    def grow(names: set) -> set:
        for _ in range(4):
            grown = set(names)
            for match in _ASSIGNED_NAME.finditer(after):
                name = match.group("kotlin") or match.group("java")
                expression = after[match.end():_statement_end(after, match.end())]
                if name and _mentions(expression, names):
                    grown.add(name)
            if grown == names:
                break
            names = grown
        return names

    # Names whose value comes from a new extension-point class.
    wrapper_set = {item[0] for item in wrappers}
    direct = grow(set(wrapper_set))
    # And functions whose body constructs or returns one, with what they give.
    through_functions = grow(direct | {
        function for function, body in _function_bodies(after)
        if function not in wrapper_set and _mentions(body, wrapper_set)})
    names_of_new = {item[0] for item in new_types}
    for name, objects in passed.items():
        derived = direct
        receiving = [arguments for arguments in _calls_of(after, name)
                     if _mentions(arguments, derived)]
        app_objects = set(passed_types.get(name, set()))
        # A method of the new class itself is where the product is made, not
        # a supplier handed to it.
        suppliers = _supplier_names(after, app_objects) - {
            function for item in wrappers for function, _ in _function_bodies(item[2])}
        if receiving:
            objects = objects | suppliers
        else:
            derived = through_functions
            receiving = [arguments for arguments in _calls_of(after, name)
                         if _mentions(arguments, derived)]
            # Only the app's own object, what builds it, or a passed value
            # the new class hands out whole is its product; a value it only
            # consults (`backing.getKey(...)` in a KeyStoreSpi view a platform
            # factory reads) is not.
            objects = app_objects | suppliers | {
                value for value in objects - passed_types.get(name, set())
                if any(_hands_out(item[2], value) for item in wrappers)}
        if not receiving or not objects:
            continue
        # Where the factory's product is made: the new classes, the classes
        # they name, and the arguments of calls on what they produce.
        flow = [item[2] for item in wrappers]
        for receiver in derived:
            flow += [arguments for arguments in _calls_of(after, receiver)]
            for call in re.finditer(r"(?<![\w$])" + re.escape(receiver)
                                    + r"\s*\.\s*([A-Za-z_$][\w$]*)\s*\(", after):
                close = _closing_bracket(after, call.end() - 1)
                if close is not None:
                    flow.append(after[call.end():close])
        named = {other for other in names_of_new if _mentions("\n".join(flow), {other})}
        flow += [item[2] for item in new_types if item[0] in named]
        if not _mentions("\n".join(flow), objects):
            continue
        mentioned = {item for item in objects if _mentions("\n".join(flow), {item})}
        wrapper_names = ", ".join(shown[item[0]] for item in wrappers)
        # The files declaring the classes and those passing their product on.
        involved = sorted(anonymous_paths | {
            str(edit.get("source_file_path", "")) for edit in production
            if _mentions(blank_comments_and_literals(
                str(edit.get("corrected_code", "") or "")), derived)})
        return [{
            "code": "WRAPPED_SAME_OBJECT",
            "path": involved[0] if involved else "",
            "affected_paths": involved,
            "details": [
                f"the patch declares {wrapper_names}, extending a platform factory, "
                f"provider or service provider interface, whose product is "
                f"{', '.join(sorted(objects - suppliers))}, the object the original "
                f"code passed to {name}(...); {name} now receives it through that class"
                + (f" (built by {', '.join(sorted(suppliers & mentioned))})"
                   if suppliers & mentioned else ""),
                "A factory or provider the app writes around the object the code "
                "already passed changes nothing the program trusts or uses; the "
                "runtime checker's finding concerns the object that reaches the "
                "reported call -- obtain it from the platform (a factory's "
                "getInstance and init with the intended key store), change the "
                "object itself, or leave the code as it is.",
            ],
        }]
    return []


# An exception that says the runtime lacks what the new path asks for -- an
# algorithm, a provider, a parameter type, a class or method -- rather than
# that the data is bad. A fallback such a catch reaches runs because the new
# path is unsupported; one reached on BadPaddingException or
# AEADBadTagException is a migration that reads data written the old way and
# keeps the old value on purpose. A catch of Exception, Throwable or
# GeneralSecurityException could be either, so it is not judged.
_CAPABILITY_EXCEPTIONS = frozenset({
    "NoSuchAlgorithmException", "NoSuchProviderException",
    "InvalidAlgorithmParameterException", "InvalidParameterException",
    "UnsupportedOperationException", "StrongBoxUnavailableException",
    "ClassNotFoundException", "NoClassDefFoundError", "ReflectiveOperationException",
    "NoSuchMethodException", "NoSuchMethodError", "NoSuchFieldException",
    "NoSuchFieldError", "LinkageError", "ClassCastException",
})
# Of those, the ones that say the new value refuses the input it was given (a
# parameter, a size, a curve) rather than that the platform lacks it. A
# fallback behind such a catch to a value the rule excludes is, for the
# reported input, the forbidden algorithm again under another name (Trigger's
# EdDSA for ED25519, 2026-10-29); behind a catch for a missing algorithm it is
# the project's path for old devices (TLSv1.3, then "TLS" where it is
# missing), and newer devices use the new value.
_REJECTED_INPUT_EXCEPTIONS = frozenset({
    "InvalidAlgorithmParameterException", "InvalidParameterException",
})
_TRY_OPENER = re.compile(r"(?<![\w$])try\s*(?:\((?:[^(){}]|\([^(){}]*\))*\)\s*)?\{")
_CATCH_CLAUSE = re.compile(r"\s*catch\s*\((?P<caught>[^(){}]*)\)\s*\{")
_FINALLY_CLAUSE = re.compile(r"\s*finally\s*\{")
_CAUGHT_TYPE = re.compile(r"(?<![\w$])[A-Z][\w$]*")
_EXIT_STATEMENT = re.compile(r"(?<![\w$.])(?:return|throw)\b")
_RETURN_STATEMENT = re.compile(r"(?<![\w$.])return\b")
_RECEIVER_CALL = (r"(?P<receiver>[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)"
                  r"\s*\.\s*(?P<name>{names})\s*\(")


def _top_level(body: str) -> str:
    """`body` with everything inside its nested brackets blanked."""
    characters, depth = list(body), 0
    for index, character in enumerate(body):
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif depth > 0 and character != "\n":
            characters[index] = " "
    return "".join(characters)


def _enclosing_block(blank: str, start: int, end: int) -> tuple:
    """Offsets just inside the block holding `blank[start:end]` (the edit's edges when open)."""
    depth, low = 0, 0
    for index in range(start - 1, -1, -1):
        if blank[index] in ")]}":
            depth += 1
        elif blank[index] in "([{":
            if not depth:
                low = index + 1
                break
            depth -= 1
    depth, high = 0, len(blank)
    for index in range(end, len(blank)):
        if blank[index] in "([{":
            depth += 1
        elif blank[index] in ")]}":
            if not depth:
                high = index
                break
            depth -= 1
    return low, high


def _receiver_key(receiver: str) -> str:
    """A static receiver by its simple class name, an instance one by its text."""
    receiver = re.sub(r"\s+", "", receiver)
    last = receiver.rsplit(".", 1)[-1]
    return last if last[:1].isupper() else receiver


def _governed_value_calls(raw: str, blank: str, low: int, high: int,
                          names: set) -> List[tuple]:
    """(receiver key, method, first argument text) of each governed call in [low, high)."""
    pattern = re.compile(_RECEIVER_CALL.format(
        names="|".join(re.escape(name) for name in sorted(names))))
    calls = []
    for match in pattern.finditer(blank, low, high):
        opening = match.end() - 1
        close = _closing_bracket(blank, opening)
        if close is None:
            continue
        comma = _top_level(blank[opening + 1:close]).find(",")
        stop = opening + 1 + comma if comma >= 0 else close
        argument = re.sub(r"\s+", " ", raw[opening + 1:stop]).strip()
        if argument:
            calls.append((_receiver_key(match.group("receiver")), match.group("name"),
                          argument))
    return calls


def _try_fallbacks(blank: str) -> List[tuple]:
    """(try start, body end, statement end, fallback ranges) of each try in `blank`.

    The fallback ranges are the bodies of the catches whose exception says the
    runtime lacks a capability (_CAPABILITY_EXCEPTIONS) and, when the try body
    always returns and one of those catches falls through, the rest of the
    enclosing block after the try statement. Each range is (start, end,
    reached on a refused input: _REJECTED_INPUT_EXCEPTIONS).
    """
    found = []
    for match in _TRY_OPENER.finditer(blank):
        body_open = match.end() - 1
        body_close = _closing_bracket(blank, body_open)
        if body_close is None:
            continue
        position, ranges, falls_through, rejected_falls = body_close + 1, [], False, False
        while True:
            clause = _CATCH_CLAUSE.match(blank, position)
            finally_clause = None if clause else _FINALLY_CLAUSE.match(blank, position)
            if not clause and not finally_clause:
                break
            opening = (clause or finally_clause).end() - 1
            close = _closing_bracket(blank, opening)
            if close is None:
                break
            caught = set(_CAUGHT_TYPE.findall(clause.group("caught"))) if clause else set()
            if caught & _CAPABILITY_EXCEPTIONS:
                rejected = bool(caught & _REJECTED_INPUT_EXCEPTIONS)
                ranges.append((opening + 1, close, rejected))
                if not _EXIT_STATEMENT.search(_top_level(blank[opening + 1:close])):
                    falls_through = True
                    rejected_falls = rejected_falls or rejected
            position = close + 1
        if not ranges:
            continue
        if falls_through and _RETURN_STATEMENT.search(
                _top_level(blank[body_open + 1:body_close])):
            ranges.append((position, _enclosing_block(blank, match.start(), position)[1],
                           rejected_falls))
        found.append((match.start(), body_close, position, ranges))
    return found


def _checked_calls(error: Dict, names: set) -> set:
    """(receiver key, method) of each governed call on the reported line, when readable."""
    context = error.get("detector_context") or {}
    location = str(context.get("source_location", "")).split(",")[0].strip()
    file_name, _, number = location.rpartition(":")
    path = str(error.get("source_file_path", "") or "")
    repo_root = str(error.get("repository_path", "") or "")
    if (not number.isdigit() or not path or not repo_root
            or (file_name and Path(file_name).name != Path(path).name)):
        return set()
    try:
        lines = (Path(repo_root) / path).read_text(
            encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    if not 0 < int(number) <= len(lines):
        return set()
    line = lines[int(number) - 1]
    return {(key, name) for key, name, _ in _governed_value_calls(
        line, blank_comments_and_literals(line), 0, len(line), names)}


def fallbacks_to_original_call(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A new governed call whose capability fallback makes the reported call again.

    Trigger's KeyPairGeneratorSpec finding (run_v2, 2026-10-29), ED25519
    passed to KeyPairGenerator.getInstance where the rule allows RSA, EC, DSA
    and DH, was answered with getInstance("EC") initialised by reflection with
    NamedParameterSpec("Ed25519") -- which an EC generator rejects -- in a try
    whose catches (ClassNotFoundException, InvalidAlgorithmParameterException)
    fall through to getInstance("EdDSA") with the original initialisation:
    the provider's name for the Ed25519 generator the code already used. The
    allowed value reached the checker only on a path that cannot succeed, the
    app kept generating the same key, and the draft built and was counted
    ready; the behaviour review had even noticed the EdDSA call.

    Flagged, in a production edit: a try statement the patch adds, one of
    whose catches names an exception for a missing capability
    (_CAPABILITY_EXCEPTIONS), where the fallback that catch reaches (its body,
    or what follows the try when the try body always returns and the catch
    falls through) passes to a governed call -- same method, same receiver
    class or object -- as its first argument the reported value (JCA names are
    case-insensitive), the argument the original code passed to that call
    when it is not a value the rule allows, or, behind a catch for a refused
    input (_REJECTED_INPUT_EXCEPTIONS), a literal the rule's own allowlist
    excludes; while the try body, or the code before it in the same block,
    passes that call a different argument. Only for a finding that reports a
    value, and, when the reported line can be read, only at a call that line
    makes: a not-observed report names a value in passing (the protocol "TLS"
    of a trust-manager finding, 2026-10-30), and an upgrade of another call
    beside the fix (a GCM getInstance next to a fixed IV) is not the reported
    call. A fallback to an allowed value (a new TLS path falling back to
    another allowed protocol, even one the original passed elsewhere), a
    fallback to another excluded value behind a catch for a missing algorithm
    (newer devices use the new one), a version check (`if (SDK_INT >= ...)`)
    and a catch-all are not judged.
    """
    from cognalys.llm.adjudication_rules import reporter_allowlist
    from cognalys.utils.pr_text import reported_found_value

    names = {name for name in governed_call_names(error.get("specification_context"))
             if name.isidentifier()}
    # A published draft records its edits under source_file.
    production = [edit for edit in edits if isinstance(edit, dict) and not _is_test_edit(edit)
                  and Path(str(edit.get("source_file_path") or edit.get("source_file") or "")
                           ).suffix.lower() in {".java", ".kt", ".kts"}]
    if not names or not production:
        return []
    message = str(error.get("error_message") or error.get("message") or "")
    context = error.get("detector_context") or {}
    value_check = str(context.get("label", "violation")).strip() in ("", "violation")
    reported = (reported_found_value(message)
                or (str(context.get("value", "")) if value_check else "")
                ).strip().strip("'\"").casefold()
    if not value_check or not reported:
        return []
    allowed = {value.casefold() for value in reporter_allowlist(error)}
    checked = _checked_calls(error, names)
    original_arguments: Dict[tuple, set] = {}
    flat_original = ""
    for edit in production:
        raw = str(edit.get("original_code", "") or "")
        blank = blank_comments_and_literals(raw)
        flat_original += " " + " ".join(blank.split())
        for key, name, argument in _governed_value_calls(raw, blank, 0, len(raw), names):
            original_arguments.setdefault((key, name), set()).add(argument)
    issues: List[Dict] = []
    for edit in production:
        raw = str(edit.get("corrected_code", "") or "")
        blank = blank_comments_and_literals(raw)
        found: List[str] = []
        for start, body_close, end, ranges in _try_fallbacks(blank):
            if " ".join(blank[start:end].split()) in flat_original:
                continue
            new_path = _governed_value_calls(
                raw, blank, _enclosing_block(blank, start, end)[0], body_close, names)
            for low, high, rejected_input in ranges:
                for key, name, argument in _governed_value_calls(raw, blank, low, high, names):
                    if checked and (key, name) not in checked:
                        continue
                    literal = re.fullmatch(r'"([^"\\]*)"', argument)
                    value = literal.group(1).casefold() if literal else ""
                    if literal and value == reported:
                        reason = "the reported value"
                    elif (argument in original_arguments.get((key, name), ())
                          and not (literal and value in allowed)):
                        reason = "the argument the original code passed"
                    elif (literal and allowed and rejected_input and (key, name) in checked
                          and value not in allowed):
                        reason = "a value the rule does not allow"
                    else:
                        continue
                    if any((other_key, other_name) == (key, name) and other != argument
                           for other_key, other_name, other in new_path):
                        found.append(f"{key}.{name}({argument}), {reason}")
        if found:
            issues.append({
                "code": "FALLBACK_TO_ORIGINAL",
                "path": str(edit.get("source_file_path") or edit.get("source_file") or ""),
                "details": [
                    "the patch tries a new value in a try block, and the fallback its "
                    "catch for a missing algorithm, provider or class, or for a "
                    "refused parameter, reaches makes the governed call again with "
                    + " | ".join(list(dict.fromkeys(found))[:3]),
                    "Wherever the new path fails the app does what it did before, "
                    "and the new value reaches the runtime checker only where that "
                    "path succeeds. When the new value works on every platform the "
                    "app targets (its minSdk) for the reported input, call it without "
                    "the fallback; when the reported input needs the old algorithm, "
                    "protocol or provider (the new call rejects it), changing the "
                    "value does not fix it -- leave the code as it is rather than "
                    "wrap the old call.",
                ],
            })
    return issues


_SOURCE_IMPORT = re.compile(
    r"^[ \t]*import[ \t]+(?:static[ \t]+)?(?P<name>[A-Za-z_][\w.]*)"
    r"(?:[ \t]+as[ \t]+(?P<alias>\w+))?[ \t]*;?[ \t]*$", re.M)
_QUALIFIED_TYPE = re.compile(r"(?<![\w.$])(?P<package>(?:[a-z_]\w*\.)+)(?P<name>[A-Z]\w*)")
_STRING_LITERAL = re.compile(r'"((?:\\.|[^"\\\n])*)"')
_CAPITALISED_NAME = re.compile(r"(?<![\w$])[A-Z]\w*")
_IDENTIFIER = re.compile(r"(?<![\w$])[A-Za-z_]\w*")
_DECLARED_TYPE = re.compile(r"\b(?:class|interface|object|enum|record)\s+([A-Z]\w*)")


def _value_token(value: str) -> str:
    """The letters and digits of a value, lower-cased: "SHA-1" -> "sha1"."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _imported_types(code: str) -> Dict[str, str]:
    """Simple name (or alias) -> qualified name of each import and qualified type in `code`."""
    found = {(match.group("alias") or match.group("name").rsplit(".", 1)[-1]):
             match.group("name") for match in _SOURCE_IMPORT.finditer(code)}
    for match in _QUALIFIED_TYPE.finditer(blank_comments_and_literals(code)):
        found.setdefault(match.group("name"), match.group("package") + match.group("name"))
    return found


def _calls_with_literals(raw: str, names: set) -> List[tuple]:
    """(name, casefolded string literals of its arguments) of each `name(...)` in `raw`."""
    if not names:
        return []
    blank = blank_comments_and_literals(raw)
    pattern = re.compile(r"(?<![\w$])(?P<name>{})\s*\(".format(
        "|".join(re.escape(name) for name in sorted(names))))
    found = []
    for match in pattern.finditer(blank):
        close = _closing_bracket(blank, match.end() - 1)
        literals = ([literal.casefold() for literal
                     in _STRING_LITERAL.findall(raw[match.end():close])]
                    if close is not None else [])
        found.append((match.group("name"), literals))
    return found


def _calls_carrying_literal(raw: str, names: set, value: str) -> List[str]:
    """Each `name(...)` in `raw`, of `names`, whose arguments hold the literal `value`."""
    if not value:
        return []
    return [name for name, literals in _calls_with_literals(raw, names) if value in literals]


def _names_value_first(name: str, token: str) -> bool:
    """Whether `name`'s leading words spell `token`, with a word left after them.

    Ed25519KeyPairGenerator for "ed25519", SHA1Digest for "sha1"; not
    LegacySha1Digest (the value is not its first word) nor a class named
    only the value.
    """
    from cognalys.utils.identifier_words import identifier_words

    joined = ""
    for word in identifier_words(name)[:-1]:
        joined += word
        if joined == token:
            return True
        if len(joined) >= len(token):
            return False
    return False


# Holders whose construction keeps its arguments and does nothing else: the
# Kotlin standard library's and the JDK's own pairs, tuples and lists.
_STANDARD_CARRIERS = frozenset({
    "Pair", "Triple", "kotlin.Pair", "kotlin.Triple", "arrayOf", "listOf",
    "mutableListOf", "Pair.create", "List.of", "Map.entry", "Arrays.asList",
    "AbstractMap.SimpleEntry", "AbstractMap.SimpleImmutableEntry",
})
# A Kotlin data class or plain class whose parameters are all properties, or a
# Java record, declared on a line of its own with at most a visibility
# modifier: anything else (open, abstract, an annotation, inner, value) may
# give the type behaviour of its own and is not decided here.
_CARRIER_HEAD = re.compile(
    r"(?m)^[ \t]*(?:(?:public|internal|private|protected|static|final)\s+)*"
    r"(?P<kind>data\s+class|class|record)\s+(?P<name>[A-Z][\w$]*)\s*(?:<[^;(){}]*>)?\s*\(")
_KOTLIN_PROPERTY_PARAMETER = re.compile(
    r"^\s*(?:(?:public|internal|private|protected)\s+)?(?:val|var)\s+[A-Za-z_$][\w$]*\s*:")
# Words that are never a value the arguments read.
_CARRIER_KEYWORDS = _NOT_VALUES | frozenset({
    "if", "else", "when", "try", "catch", "finally", "throw", "object", "out",
    "fun", "val", "var", "for", "while", "do", "break", "continue", "instanceof",
    "class", "interface", "final", "static", "public", "private", "protected",
    "internal", "switch", "case", "default", "lateinit", "const", "by",
})
_ASSIGNMENT_OPERATOR = r"\s*(?::[^=\n;{}]*)?(?:[-+*/%&|^]|<<|>>>?)?=(?!=)"


def _top_level_parts(text: str) -> List[str]:
    """`text` split at the commas outside brackets."""
    parts, depth, start = [], 0, 0
    for index, character in enumerate(text):
        if character in "([{<":
            depth += 1
        elif character in ")]}>":
            depth -= 1
        elif character == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return [part for part in parts if part.strip()]


def _pure_carrier_spans(code: str) -> Dict[str, tuple]:
    """Name -> (start, end) of each type `code` declares that only holds values.

    `code` is blanked. The header must close with nothing after it but an
    empty body: a body on the next line (Allman braces) counts like one on the
    same line, and a supertype (`:`, `extends`, `implements`), a `where`
    clause or a default value is behaviour this does not decide.
    """
    found: Dict[str, tuple] = {}
    for match in _CARRIER_HEAD.finditer(code):
        close = _closing_bracket(code, match.end() - 1)
        if close is None:
            continue
        parameters = _top_level_parts(code[match.end():close])
        if not parameters or any("=" in part for part in parameters):
            continue
        if (match.group("kind") != "record"
                and not all(_KOTLIN_PROPERTY_PARAMETER.match(part) for part in parameters)):
            continue
        end = close + 1
        rest = code[end:]
        stripped = rest.lstrip()
        if stripped[:1] == "{":
            opening = end + (len(rest) - len(stripped))
            body_close = _closing_bracket(code, opening)
            if body_close is None or code[opening + 1:body_close].strip():
                continue
            end = body_close + 1
        elif stripped[:1] in {":", "("} or re.match(r"(?:extends|implements|where)\b", stripped):
            continue
        found[match.group("name")] = (match.start(), end)
    return found


def _pure_carrier_types(code: str) -> set:
    """Names of the types `code` declares that only hold values."""
    return set(_pure_carrier_spans(code))


def _constructions_of(blank: str, raw: str, names) -> List[tuple]:
    """(type, normalized argument text, blanked argument text) of each `T(...)`.

    Located in the blanked text and read from the raw one at the same offsets
    (blank_comments_and_literals keeps them), so a changed string literal
    ("SSL" to "TLS") makes a different argument text.
    """
    found = []
    for name in names:
        pattern = re.compile(r"(?<![\w$.])(?:new\s+)?(?:[a-z_][\w$]*\.)*"
                             + re.escape(name) + r"\s*(?:<[^;(){}]*>)?\s*\(")
        for match in pattern.finditer(blank):
            close = _closing_bracket(blank, match.end() - 1)
            if close is None:
                continue
            arguments = re.sub(r"\s+", "", raw[match.end():close])
            if arguments:
                found.append((name, arguments, blank[match.end():close]))
    return found


def _declares_member(code: str, name: str) -> bool:
    """Whether blanked `code` declares `name` as a property, field, function or method."""
    escaped = re.escape(name)
    if re.search(rf"\b(?:val|var|fun)\s+(?:<[^>]+>\s*)?(?:[\w?.<>]+\.)?{escaped}\b", code):
        return True
    return any(match.group("type") not in _CARRIER_KEYWORDS for match in re.finditer(
        rf"(?<![\w$.])(?P<type>[\w$.<>\[\],?]+)\s+{escaped}\s*[=;(]", code))


def _assignments(blank: str, raw: str, name: str) -> List[str]:
    """Normalized right-hand side of each assignment of `name`, `++`/`--` included."""
    found = []
    pattern = re.compile(rf"(?<![\w$]){re.escape(name)}" + _ASSIGNMENT_OPERATOR)
    for match in pattern.finditer(blank):
        end = _statement_end(blank, match.end())
        found.append(re.sub(r"\s+", "", raw[match.start():end]))
    for match in re.finditer(rf"(?:\+\+|--)\s*{re.escape(name)}\b"
                             rf"|(?<![\w$]){re.escape(name)}\s*(?:\+\+|--)", blank):
        found.append(re.sub(r"\s+", "", match.group(0)))
    return found


def _bound_without_assignment(blank: str, name: str) -> bool:
    """`name` bound by a lambda, destructuring, for or catch header: not decided."""
    escaped = re.escape(name)
    return bool(
        re.search(rf"(?<![\w$.]){escaped}\b[^{{}}\n]*->", blank)
        or re.search(rf"\b(?:val|var)\s*\([^)]*(?<![\w$]){escaped}\b", blank)
        or re.search(rf"\b(?:for|catch)\s*\([^)]*(?<![\w$]){escaped}\b", blank))


def _declares_local_or_field(code: str, name: str) -> bool:
    """Whether blanked `code` gives `name` a value of its own: val/var, or a Java local or field."""
    escaped = re.escape(name)
    if re.search(rf"\b(?:val|var)\s+{escaped}\b", code):
        return True
    for match in re.finditer(rf"(?m)(?:^|[;{{}}(])\s*(?:(?:public|protected|private|static|"
                             rf"final|transient|volatile)\s+)*(?P<type>[\w$.<>\[\]?]+)\s+"
                             rf"{escaped}\s*[=;]", code):
        if match.group("type") not in _CARRIER_KEYWORDS:
            return True
    return False


def _right_hand_sides(blank: str, name: str) -> List[str]:
    """Blanked right-hand side of each assignment of `name` in `blank`."""
    pattern = re.compile(rf"(?<![\w$]){re.escape(name)}" + _ASSIGNMENT_OPERATOR)
    return [blank[match.end():_statement_end(blank, match.end())]
            for match in pattern.finditer(blank)]


_KOTLIN_FUNCTION_HEAD = re.compile(
    r"\s*(?:@[\w.]+(?:\([^)]*\))?\s+)*(?:[a-z]+\s+)*fun\s+(?:<[^>]+>\s*)?"
    r"(?:[\w?.<>]+\.)?[A-Za-z_][\w$]*\s*\(")
_KOTLIN_CONSTRUCTOR_HEAD = re.compile(r"\s*(?:[a-z]+\s+)*constructor\s*\(")
_JAVA_METHOD_HEAD = re.compile(
    r"\s*(?:@[\w.]+(?:\([^)]*\))?\s+)*(?:(?:public|protected|private|static|final|"
    r"synchronized|abstract|native)\s+)*(?:<[^>]+>\s*)?(?:[\w$.<>\[\],?]+\s+)?"
    r"(?P<name>[A-Za-z_][\w$]*)\s*\(")


def _header_parameters(scope: str) -> Optional[set]:
    """Parameter names of the function the blanked `scope` opens with, or None.

    None when the text does not open with a Kotlin `fun`, `init` or
    `constructor`, or a Java method or constructor header: a name it does
    not declare may then be a parameter this cannot see.
    """
    if re.match(r"\s*(?:init|static)\s*\{", scope):
        return set()
    match = _KOTLIN_FUNCTION_HEAD.match(scope) or _KOTLIN_CONSTRUCTOR_HEAD.match(scope)
    if match is None:
        match = _JAVA_METHOD_HEAD.match(scope)
        if match is None or match.group("name") in _CARRIER_KEYWORDS:
            return None
    close = _closing_bracket(scope, match.end() - 1)
    if close is None:
        return None
    names = set()
    for part in _top_level_parts(scope[match.end():close]):
        part = part.split("=", 1)[0]
        kotlin = re.match(r"\s*(?:@[\w.]+\s+|vararg\s+|noinline\s+|crossinline\s+)*"
                          r"([A-Za-z_][\w$]*)\s*:", part)
        java = re.search(r"([A-Za-z_][\w$]*)\s*$", part)
        if kotlin:
            names.add(kotlin.group(1))
        elif java:
            names.add(java.group(1))
    return names


def _same_sources(arguments_blank: str, scope: str, context: Dict) -> bool:
    """Whether every value the arguments read is given exactly what it was before.

    `scope` is the blanked original edit the construction sits in. Followed
    from the arguments through the right-hand sides that assign them, and
    decided only when each name is a local `scope` declares, or -- when
    `scope` opens with a function header that does not take it as a
    parameter -- a field or property the original declares (a parameter
    takes whatever its callers pass, which this does not follow); the patch
    leaves every assignment of it as it was and binds it through no lambda,
    destructuring, for or catch; the values are read through no receiverless
    call and no call or member the patch declares; and, for a field or
    property, the patch constructs every class of the file declaring it with
    the arguments it did, so a property a constructor sets keeps its value
    (the classes the holder itself is passed to may change: BlueGate's
    Authenticator takes the KeyMaterial where it took the KeyPair).
    Capitalised names nobody declares are types and objects of other files.
    """
    before_all, after_all = context["before_blank"], context["after_blank"]
    declarations = context["declarations_blank"]
    edited_blank = before_all + "\n" + context["after_member_blank"]
    parameters = _header_parameters(scope)
    pending, seen, classes = [(arguments_blank, scope)], set(), set()
    while pending:
        expression, where = pending.pop()
        for call in re.finditer(r"(?P<dot>\.\s*)?(?P<name>[A-Za-z_][\w$]*)\s*(?:<[^;(){}]*>)?"
                                r"\s*\(", expression):
            name = call.group("name")
            if name in _CARRIER_KEYWORDS:
                continue
            if not call.group("dot") and name[:1].islower():
                return False
            if _declares_member(edited_blank, name):
                return False
        for member in re.finditer(r"\.\s*([A-Za-z_][\w$]*)\b", expression):
            if _declares_member(edited_blank, member.group(1)):
                return False
        for root in re.findall(r"(?<![\w$.])([A-Za-z_][\w$]*)\b(?!\s*(?:<[^;(){}]*>)?\s*\()",
                               expression):
            if root in _CARRIER_KEYWORDS or (root, where is None) in seen:
                continue
            seen.add((root, where is None))
            if len(seen) > 40:
                return False
            if where is not None and _declares_local_or_field(where, root):
                origin = where
            elif where is not None and (parameters is None or root in parameters):
                return False
            elif _declares_local_or_field(declarations, root):
                origin = None
                classes.update(name for path, text in context["path_texts"].items()
                               if _declares_local_or_field(text, root)
                               for name in context["path_classes"][path])
            elif root[:1].isupper() and not _declares_local_or_field(after_all, root):
                continue
            else:
                return False
            if any(_bound_without_assignment(text, root)
                   for text in (origin or declarations, after_all)):
                return False
            if sorted(_assignments(before_all, context["before_raw"], root)) != sorted(
                    _assignments(after_all, context["after_raw"], root)):
                return False
            pending.extend((value, origin)
                           for value in _right_hand_sides(origin or declarations, root))
    classes = sorted(classes)
    return (sorted(item[:2] for item in _constructions_of(
                before_all, context["before_raw"], classes))
            == sorted(item[:2] for item in _constructions_of(
                after_all, context["after_raw"], classes)))


def same_parts_app_carriers(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A governed object replaced by a holder of the app's own built from the same parts.

    BlueGate run_v7 (2026-10-31): RVSec's KeyPairSpec finding, "no generator of
    the private key given to the KeyPair constructor was observed" at
    `KeyPair(entry.certificate.publicKey, entry.privateKey)` of a key read back
    from AndroidKeyStore, was answered with a new `data class KeyMaterial(val
    publicKey: PublicKey, val privateKey: PrivateKey)` returned as
    `KeyMaterial(entry.certificate.publicKey, entry.privateKey)` and threaded
    through KeyManager, Authenticator and two activities. The monitor no
    longer saw a KeyPair constructor, but the code held, trusted and signed
    with exactly what it did; the behaviour review, asked whether the checker
    would still observe the same calls, said it would not and let the draft
    through. Returning the KeyStore.PrivateKeyEntry the store gave and using
    its own getters (run_v6) is a different thing: no holder is built.

    Flagged, all of, in production code: the original constructs a type the
    specification governs, `T(args)`; the patch constructs the governed type
    nowhere; the same file now builds, with argument text identical to the
    character (literals included), a holder that keeps its arguments and does
    nothing else -- a Kotlin data class or plain class of properties, or a
    Java record, the patch declares with no supertype, no default value and
    an empty body, or the standard library's Pair, Triple, list or array; and
    every value those arguments read is given what it was before
    (_same_sources). A changed value, another source of a part, a class with
    behaviour of its own, or a case those rules cannot settle gets no verdict
    (the review judges it): a patch that still builds the governed object
    from the holder, and one that reassigns a part, reads it through a
    function it rewrites or from a parameter, were flagged before these rules
    (review of 2026-10-31).
    """
    governed = _governed_simple_names(error)
    production = [edit for edit in edits if isinstance(edit, dict) and not _is_test_edit(edit)
                  and Path(str(edit.get("source_file_path") or edit.get("source_file") or "")
                           ).suffix.lower() in {".java", ".kt", ".kts"}]
    if not governed or not production:
        return []
    by_path: Dict[str, Dict[str, List[str]]] = {}
    for edit in production:
        path = str(edit.get("source_file_path") or edit.get("source_file") or "")
        entry = by_path.setdefault(path, {"before": [], "after": []})
        if edit.get("edit_kind") != "create":
            entry["before"].append(str(edit.get("original_code", "") or ""))
        entry["after"].append(str(edit.get("corrected_code", "") or ""))
    repo_root = Path(str(error.get("repository_path", "") or ""))
    files_before: Dict[str, str] = {}
    for path in by_path:
        try:
            if repo_root.parts:
                files_before[path] = blank_comments_and_literals(
                    (repo_root / path).read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            pass
    before_raw = "\n".join(text for entry in by_path.values() for text in entry["before"])
    after_raw = "\n".join(text for entry in by_path.values() for text in entry["after"])
    before_blank = blank_comments_and_literals(before_raw)
    after_blank = blank_comments_and_literals(after_raw)
    # A patch that still builds the governed object (from the holder, or
    # anywhere else it writes) still calls the governed API.
    if _constructions_of(after_blank, after_raw, governed):
        return []
    declarations_blank = "\n".join([*files_before.values(), before_blank])
    path_texts = {path: "\n".join([files_before.get(path, ""), *(
        blank_comments_and_literals(text) for text in entry["before"])])
        for path, entry in by_path.items()}
    declared_before = {match.group("name") for match in _TYPE_DECLARATION.finditer(
        declarations_blank)}
    spans = {name: span for name, span in _pure_carrier_spans(after_blank).items()
             if name not in declared_before and name not in governed}
    after_member_blank = list(after_blank)
    for start, end in spans.values():
        for index in range(start, end):
            if after_member_blank[index] != "\n":
                after_member_blank[index] = " "
    context = {
        "before_blank": before_blank, "after_blank": after_blank,
        "before_raw": before_raw, "after_raw": after_raw,
        "after_member_blank": "".join(after_member_blank),
        "declarations_blank": declarations_blank,
        "path_texts": path_texts,
        "path_classes": {path: {match.group("name") for match in _TYPE_DECLARATION.finditer(text)}
                         - governed for path, text in path_texts.items()},
    }
    carrier_names = set(spans) | _STANDARD_CARRIERS
    issues: List[Dict] = []
    for path, entry in by_path.items():
        path_before_raw, path_after_raw = "\n".join(entry["before"]), "\n".join(entry["after"])
        path_before = blank_comments_and_literals(path_before_raw)
        path_after = blank_comments_and_literals(path_after_raw)
        removed = [(item, blank_comments_and_literals(text)) for text in entry["before"]
                   for item in _constructions_of(blank_comments_and_literals(text), text,
                                                 governed)]
        existing = [item[:2] for item in _constructions_of(path_before, path_before_raw,
                                                           carrier_names)]
        built = [item for item in _constructions_of(path_after, path_after_raw, carrier_names)
                 if item[:2] not in existing]
        found = []
        for (governed_type, arguments, arguments_blank), scope in removed:
            holders = [name for name, held, _ in built if held == arguments]
            if not holders or not _same_sources(arguments_blank, scope, context):
                continue
            item = f"{governed_type}({arguments}) -> {holders[0]}({arguments})"
            if item not in found:
                found.append(item)
        if found:
            issues.append({
                "code": "SAME_PARTS_APP_CARRIER",
                "path": path,
                "details": [
                    "the patch replaces the governed object with a holder of the "
                    "app's own built from the same parts, read from the same "
                    "sources: " + " | ".join(found[:3]),
                    "A type that carries the governed object's parts only so the "
                    "governed API is no longer called corrects nothing: the code "
                    "holds, trusts and does what it did. Use the object the "
                    "platform already returns instead of rebuilding one (a key "
                    "store entry through its own getters), change what the parts "
                    "are or where they come from, or leave the code as it is.",
                ],
            })
    return issues


# The platform's key interfaces: a class implementing one is a key the code
# can hand to any engine without a governed constructor ever running.
_KEY_INTERFACE_NAME = re.compile(r"^(?:Key|SecretKey|PrivateKey|PublicKey)$")
# Members that copy a value without changing it: `keyBytes.clone()`,
# `password.toByteArray()`, `key.encoded`. A size or a length is not a copy.
_COPYING_MEMBERS = frozenset({
    "clone", "copyOf", "copyOfRange", "toByteArray", "toCharArray", "getBytes",
    "encodeToByteArray", "getEncoded", "encoded",
})
# `name`, `this.name`, or either followed by copying members whose arguments
# read no other value (`copyOf()`, `getBytes(UTF_8)`, `copyOfRange(0, 16)`).
_PLAIN_COPY = re.compile(
    r"^\s*(?:this\s*\.\s*)?(?P<root>[A-Za-z_$][\w$]*)"
    r"(?:\s*!!)?(?:\s*\??\s*\.\s*(?:" + "|".join(sorted(_COPYING_MEMBERS))
    + r")\b(?:\s*\((?:[^()A-Za-z_$]|[A-Z_][\w$]*)*\))?(?:\s*!!)?)*\s*$")
# A parameter or variable named for what it labels, not for material.
_LABEL_NAME = re.compile(
    r"(?i)^(?:\w*algorithm\w*|alg|algo|transformation|provider\w*|label|format|mode|padding)$")


def _plain_copy_root(expression: str) -> Optional[str]:
    """The name blanked `expression` is, or a plain copy of; None otherwise."""
    match = _PLAIN_COPY.match(expression)
    return match.group("root") if match else None


def _declared_as_string(name: str, code: str) -> bool:
    escaped = re.escape(name)
    return bool(re.search(rf"(?<![\w$.]){escaped}\s*:\s*(?:String|CharSequence)\b(?!\s*[.(])",
                          code)
                or re.search(rf"\b(?:String|CharSequence)\s+{escaped}\b", code))


def _material_parts(arguments_blank: str, code: str) -> tuple:
    """(names, expressions) a governed construction takes as material.

    Each top-level argument that is a name, or a plain copy of one, gives
    that name; a String argument (a name declared String, or named for an
    algorithm, provider or label) is not material, and a literal reads no
    value. Any other argument reading a value counts only as its whole text.
    """
    names, expressions = set(), set()
    for argument in _top_level_parts(arguments_blank):
        root = _plain_copy_root(argument)
        if root is not None:
            if root in _CARRIER_KEYWORDS or not re.match(r"[a-z_]", root):
                continue
            bare = re.fullmatch(r"\s*(?:this\s*\.\s*)?[\w$]+\s*", argument)
            if bare and (_declared_as_string(root, code) or _LABEL_NAME.match(root)):
                continue
            names.add(root)
        elif re.search(r"(?<![\w$.])[a-z_][\w$]*\b(?!\s*\()", argument):
            expressions.add(re.sub(r"\s+", "", argument))
    return names, expressions


def _carried_root(expression: str, names: set, expressions: set) -> Optional[str]:
    """The part blanked `expression` carries unchanged, or as a plain copy."""
    root = _plain_copy_root(expression)
    if root is not None and root in names:
        return root
    flat = re.sub(r"\s+", "", expression)
    return flat if flat and flat in expressions else None


def _copies_of(code: str, names: set, expressions: set = frozenset()) -> Dict[str, str]:
    """Name -> part, for each part and each name blanked `code` assigns a plain copy of one.

    `encoded = keyBytes.clone()` makes `encoded` a copy of keyBytes; `n =
    keyBytes.size` or `derived = pbkdf2(keyBytes, salt)` makes nothing.
    """
    found = {name: name for name in names}
    found.update({expression: expression for expression in expressions})
    for _ in range(3):
        grown = dict(found)
        for match in _ASSIGNED_NAME.finditer(code):
            name = match.group("kotlin") or match.group("java")
            expression = code[match.end():_statement_end(code, match.end())]
            root = _carried_root(expression, set(found), set(expressions))
            if name and root is not None and name not in grown:
                grown[name] = found[root]
        if grown == found:
            break
        found = grown
    return found


# `return x` and the right side of `=` (not `==`, `!=`, `<=`, `>=` or a
# compound assignment).
_VALUE_POSITION = re.compile(r"\breturn\b|(?<![=!<>+\-*/%&|^])=(?![=>])")


def _values_given(text: str) -> List[str]:
    """Every expression blanked `text` returns or assigns."""
    return [text[match.end():_statement_end(text, match.end())]
            for match in _VALUE_POSITION.finditer(text)]


def _shadowed_names(text: str) -> set:
    """Names a declared type's text binds itself: constructor parameters and members."""
    names = {match.group(1) for match in re.finditer(
        r"[(,]\s*(?:(?:private|public|protected|internal|final|override)\s+)*"
        r"(?:(?:val|var)\s+)?([A-Za-z_$][\w$]*)\s*:", text)}
    names |= {match.group(1) for match in re.finditer(
        r"[(,]\s*(?:final\s+)?[\w$.<>\[\]?]+\s+([A-Za-z_$][\w$]*)\s*(?=[,)])", text)}
    names |= {match.group("kotlin") or match.group("java")
              for match in _ASSIGNED_NAME.finditer(text)}
    return {name for name in names if name}


def same_parts_app_keys(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A key class the app writes over the parts the original gave a governed constructor.

    TOTP's hmac_sha (frozen run, 2026-11-13; the draft reached PR pronto and
    both judges scored its rule fix 0 and 2): RVSec's SecretKeySpec finding on
    `new SecretKeySpec(keyBytes, "RAW")` was answered, for the HmacSHA1 path,
    with `macKey = new SecretKey() { getAlgorithm() { return "HmacSHA1"; }
    getEncoded() { return encoded.clone(); } }` over `encoded =
    keyBytes.clone()`. Mac.init keys the same bytes as before; only the
    checker stops seeing a SecretKeySpec built. The adjudication had
    prescribed "a compatible SecretKey representation that does not pass a
    disallowed algorithm name to SecretKeySpec", and the behaviour review
    passed it with no regression.

    Flagged, all of, in production code: the original constructs a type the
    specification governs, `T(args)`, with material among its arguments (a
    name or a plain copy of one; a String argument, a name for an algorithm
    or label, or a literal is not material: _material_parts); the patch adds
    a class, named or anonymous, implementing the platform's Key, SecretKey,
    PrivateKey or PublicKey; and that class returns or stores that material
    or a plain copy of it (`clone`, `copyOf`, `toByteArray`: _copies_of), or
    is constructed with it as an argument. Material that only flows into
    another computation (`pbkdf2(password, salt)`, `digest(keyBytes)`, its
    size) is changed material, not carried. The governed construction may
    stay elsewhere.
    A key the platform makes (a KeyGenerator, a key store entry, a key
    factory) declares no class, and a test file is not judged. The governed
    types are those of the finding and of its joint findings (TOTP's unit was
    reported under the Mac rule, with SecretKeySpec's joined to it).
    """
    from cognalys.llm.evidence_packet import joint_specification_contexts

    governed = set(_governed_simple_names(error))
    for _, joint_context in joint_specification_contexts(error):
        governed |= _governed_simple_names({"specification_context": joint_context})
    production = [edit for edit in edits if isinstance(edit, dict) and not _is_test_edit(edit)
                  and Path(str(edit.get("source_file_path") or "")).suffix.lower()
                  in {".java", ".kt", ".kts"}]
    if not governed or not production:
        return []
    repo_root = Path(str(error.get("repository_path", "") or ""))
    files_before: Dict[str, str] = {}
    for edit in production:
        path = str(edit.get("source_file_path") or "")
        if path not in files_before:
            try:
                files_before[path] = blank_comments_and_literals(
                    (repo_root / path).read_text(encoding="utf-8", errors="replace")
                ) if repo_root.parts and edit.get("edit_kind") != "create" else ""
            except (OSError, ValueError):
                files_before[path] = ""
    before_raw = "\n".join(str(edit.get("original_code", "") or "") for edit in production
                           if edit.get("edit_kind") != "create")
    before = blank_comments_and_literals(before_raw)
    pieces = [blank_comments_and_literals(str(edit.get("corrected_code", "") or ""))
              for edit in production]
    after = "\n".join(pieces)
    context = "\n".join([*files_before.values(), before, after])
    constructions = _constructions_of(before, before_raw, governed)
    parts: Dict[str, tuple] = {}
    for governed_type, _, arguments in constructions:
        names, expressions = _material_parts(arguments, context)
        if names or expressions:
            known = parts.setdefault(governed_type, (set(), set()))
            known[0].update(names)
            known[1].update(expressions)
    if not parts:
        return []
    flat_before = " ".join("\n".join([*files_before.values(), before]).split())
    declared_before = {match.group("name") for match in _TYPE_DECLARATION.finditer(
        "\n".join([*files_before.values(), before]))}
    # (shown, interface, values the class returns or assigns, names it binds
    # itself, the arguments it is constructed with)
    keys: List[tuple] = []
    for name, supertypes, text in _declared_types(after):
        interfaces = [item for item in supertypes
                      if _is_jca_type(item, context, _KEY_INTERFACE_NAME)]
        if name in declared_before or not interfaces:
            continue
        arguments = [argument for call in _calls_of(after, name)
                     for argument in _top_level_parts(call)]
        interface = interfaces[0].rsplit(".", 1)[-1]
        keys.append((f"a class {name} implementing {interface}", interface,
                     _values_given(text), _shadowed_names(text), arguments))
    for start, end, supertypes in _anonymous_types(after):
        interfaces = [item for item in supertypes
                      if _is_jca_type(item, context, _KEY_INTERFACE_NAME)]
        if interfaces and " ".join(after[start:end].split()) not in flat_before:
            simple = interfaces[0].rsplit(".", 1)[-1]
            text = after[start:end]
            keys.append((f"an anonymous {simple}", simple, _values_given(text),
                         _shadowed_names(text), []))
    if not keys:
        return []
    for governed_type, (names, expressions) in sorted(parts.items()):
        carried = _copies_of(after, names, expressions)
        values = names | expressions
        for shown, interface, given, bound, arguments in keys:
            # A name the class binds itself (a constructor parameter, a
            # field) inside its body is not the outer part of that name.
            inside = {name for name in carried if name not in bound}
            held = sorted({
                name for name in (
                    [_carried_root(value, inside, expressions) for value in given]
                    + [_carried_root(argument, set(carried), expressions)
                       for argument in arguments])
                if name is not None})
            if not held:
                continue
            sources = sorted({carried[name] for name in held})
            paths = sorted({str(edit.get("source_file_path", "")) for edit, piece
                            in zip(production, pieces) if _mentions(piece, {interface})
                            and (_mentions(piece, set(held)) or any(
                                name in re.sub(r"\s+", "", piece) for name in held))})
            return [{
                "code": "SAME_PARTS_APP_KEY",
                "path": paths[0] if paths else "",
                "affected_paths": paths,
                "details": [
                    f"the patch adds {shown} that carries "
                    f"{', '.join(held)}"
                    + ("" if set(held) <= values else
                       f" (a copy of {', '.join(sources)})")
                    + f", what the original code gave {governed_type}(...): the rule "
                    "was evaded, not satisfied",
                    "A key class the app writes over the same material only hides "
                    "the governed construction from the runtime checker: the code "
                    "keys the same bytes for the same algorithm as before. Build the "
                    "key with the platform's own class and a value the rule accepts, "
                    "change the material itself, or leave the code as it is.",
                ],
            }]
    return []


def same_values_through_unobserved_apis(error: Dict, edits: List[Dict]) -> List[Dict]:
    """The reported value, produced again through a library the checker does not see.

    AGrahn's ECGenParameterSpecSpec finding (run_v3, 2026-10-29),
    KeyPairGenerator.getInstance("EdDSA", BouncyCastleProvider()) initialised
    with ECGenParameterSpec("ed25519"), was answered by generating the same
    Ed25519 key with Bouncy Castle's lightweight Ed25519KeyPairGenerator and
    converting it back to JCA keys. Same algorithm, same key: only the entry
    point changed, to one the runtime checker does not instrument, so the
    finding disappears without anything the app does changing -- CLAUDE.md's
    "different entry point" case. The draft built and was counted ready; the
    behaviour review called it a maintainer's choice.

    Flagged, per production file of the patch: the original code passes the
    reported value as a string literal (JCA names are case-insensitive) to a
    call the specification governs (its event names or governed types); the
    patch takes that call away (fewer of them, and no governed call with a
    new literal); and the patched code newly uses a class imported from a
    package outside the platform (java., javax., android.) whose leading
    words spell the reported value (Ed25519KeyPairGenerator for "ed25519",
    SHA1Digest for "SHA-1"). Only for a finding that reports a value. Not
    judged: a class the patch declares or the file names without an import
    (an app's own implementation, which the behaviour review weighs), a
    patch whose new names or literals also name a value the rule allows
    (GCMBlockCipher beside AESEngine for an "AES" report, a helper's
    toSha256() or a "SHA-256" passed to it: a different value is a change),
    a reported value that is a word of a governed type's name ("SSL" of
    SSLContext also starts Netty's SslContextBuilder, which defaults to
    TLS), and a reported value shorter than three characters or without a
    letter. The adversarial review of 2026-10-30 found each of these
    exclusions as a real change the first version closed without impact.
    Measured on 2026-10-30 over the 412 coordinated drafts and 233 pull
    request drafts under outputs/: 98 drafts drop the reported literal from
    every governed call, and only AGrahn's (its draft and its pull request)
    is flagged.
    """
    from cognalys.llm.adjudication_rules import reporter_allowlist
    from cognalys.utils.identifier_words import names_token_as_word
    from cognalys.utils.pr_text import reported_found_value

    governed = ({name for name in governed_call_names(error.get("specification_context"))
                 if name.isidentifier()} | _governed_simple_names(error))
    message = str(error.get("error_message") or error.get("message") or "")
    context = error.get("detector_context") or {}
    if not governed or str(context.get("label", "violation")).strip() not in ("", "violation"):
        return []
    shown = (reported_found_value(message) or str(context.get("value", ""))
             ).strip().strip("'\"")
    reported = shown.casefold()
    token = _value_token(reported)
    if len(token) < 3 or not re.search(r"[a-z]", token):
        return []
    # A value that is a word of the governed type's own name names the API
    # family, not one value of it: "SSL" of SSLContext is also the first word
    # of Netty's SslContextBuilder, whose default is TLS (adversarial review
    # of this gate, 2026-10-30). A class name cannot tell that value apart.
    if any(names_token_as_word(name, token) for name in governed if name[:1].isupper()):
        return []
    allowed_tokens = {part for value in reporter_allowlist(error)
                      for part in [_value_token(value)]
                      + [piece.casefold() for piece in re.split(r"[^A-Za-z0-9]+", value)]
                      if len(part) >= 3 and re.search(r"[a-z]", part) and part != token}
    # A published draft records its edits under source_file.
    by_path: Dict[str, List[Dict]] = {}
    for edit in edits:
        if not isinstance(edit, dict) or _is_test_edit(edit):
            continue
        path = str(edit.get("source_file_path") or edit.get("source_file") or "")
        if Path(path).suffix.lower() in {".java", ".kt", ".kts"}:
            by_path.setdefault(path, []).append(edit)
    repo_root = str(error.get("repository_path", "") or "")
    issues: List[Dict] = []
    for path, file_edits in by_path.items():
        original = "\n".join(str(edit.get("original_code", "") or "") for edit in file_edits)
        corrected = "\n".join(str(edit.get("corrected_code", "") or "") for edit in file_edits)
        removed = _calls_carrying_literal(original, governed, reported)
        if not removed or _calls_carrying_literal(corrected, governed, reported):
            continue
        # Only a governed call the patch takes away. A call that stays with a
        # new literal or a constant ("SSL" -> "TLSv1.2", the same getInstance
        # count) changed its value through the API the checker sees, whatever
        # else the patch imports: an SSLSocketFactoryCompat of the app's own
        # wrapping the new context starts with the reported "SSL" too
        # (adversarial review of this gate, 2026-10-30).
        before = _calls_with_literals(original, governed)
        after = _calls_with_literals(corrected, governed)
        if (not any(sum(1 for name, _ in after if name == gone)
                    < sum(1 for name, _ in before if name == gone) for gone in set(removed))
                or {literal for _, literals in after for literal in literals}
                - {literal for _, literals in before for literal in literals}):
            continue
        source = ""
        if repo_root:
            try:
                source = (Path(repo_root) / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                source = ""
        imports = {**_imported_types(source), **_imported_types(corrected)}
        added = (set(_CAPITALISED_NAME.findall(
                     blank_comments_and_literals(_SOURCE_IMPORT.sub("", corrected))))
                 - set(_CAPITALISED_NAME.findall(blank_comments_and_literals(original)))
                 - set(_DECLARED_TYPE.findall(blank_comments_and_literals(corrected))))
        # Any new identifier or literal naming an allowed value (a helper's
        # sha256(), a "SHA-256" passed to the app's own hasher) marks a
        # different value reached some other way: a change, not judged here.
        blank_original = blank_comments_and_literals(original)
        new_words = (set(_IDENTIFIER.findall(blank_comments_and_literals(
                         _SOURCE_IMPORT.sub("", corrected))))
                     - set(_IDENTIFIER.findall(blank_original)))
        new_literals = (set(_STRING_LITERAL.findall(_SOURCE_IMPORT.sub("", corrected)))
                        - set(_STRING_LITERAL.findall(original)))
        if (any(names_token_as_word(name, allowed)
                for name in added | new_words for allowed in allowed_tokens)
                or any(_value_token(literal) in allowed_tokens
                       or {piece.casefold() for piece in re.split(r"[^A-Za-z0-9]+", literal)}
                       & allowed_tokens for literal in new_literals)):
            continue
        same_value = [imports[name] for name in sorted(added - governed)
                      if imports.get(name) and not _PLATFORM_PACKAGE.match(imports[name])
                      and _names_value_first(name, token)]
        if same_value:
            issues.append({
                "code": "SAME_VALUE_UNOBSERVED_API",
                "path": path,
                "details": [
                    "the patch removes " + ", ".join(dict.fromkeys(removed))
                    + f'("{shown}") and produces the same {shown} through '
                    + ", ".join(same_value[:3])
                    + ", an API outside the platform's that the runtime checker "
                    "does not observe",
                    "The app ends up with the same algorithm, curve or key it had "
                    "before; only the entry point changed, so the checker stops "
                    "seeing the value without anything the app does changing. "
                    "Use a value the rule allows through the governed API when one "
                    "keeps the app working; when the reported value is required (a "
                    "peer, a stored key or a protocol needs it), leave the code as "
                    "it is rather than reach the same value another way.",
                ],
            })
    return issues


def comment_only_changes(edits: List[Dict]) -> List[Dict]:
    """A patch whose production code is the code it started from.

    org.cry.otp's HmacSHA1 finding (exp_final_20261019): the gates refused
    switching TOTP's RFC 6238 default to HmacSHA256, and the next draft added
    only the comment "RFC 6238 default; changing this breaks SHA-1-enrolled
    authenticators" -- a pull request that changes nothing and leaves the
    reported call exactly as the detector saw it. That is a finding with no
    client-side fix, which belongs in an issue, not a diff. A cosmetic fix
    that satisfies the rule changes code and is not affected.
    """
    production = [edit for edit in edits if not _is_test_edit(edit)]
    if not production:
        return []

    def code_of(text: str) -> str:
        # Comments removed, string literals kept: "SHA-1" -> "SHA-256" is a
        # change of code.
        kept = _COMMENT_OR_LITERAL.sub(
            lambda match: "" if match.group(0).startswith(("//", "/*")) else match.group(0),
            text or "",
        )
        return re.sub(r"\s+", "", kept)

    for edit in production:
        if code_of(str(edit.get("original_code", ""))) != code_of(
                str(edit.get("corrected_code", ""))):
            return []
    return [{
        "code": "COMMENT_ONLY_CHANGE",
        "path": str(production[0].get("source_file_path", "")),
        "details": [
            "the production edits change only comments or whitespace, so the "
            "reported code is unchanged and the pull request would fix "
            "nothing. Change the code the finding reports, or decide that no "
            "client-side fix is safe and say why."
        ],
    }]


_REJECTION = re.compile(
    r"\bthrow\b|(?<![\w.])(?:require|check|error)\s*\("
)
_KOTLIN_DECLARATION = re.compile(r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w?.<>]+\.)?\w+\s*\(")
_JAVA_DECLARATION = re.compile(
    r"^\s*(?:@[\w.]+(?:\([^)]*\))?\s+)*"
    r"(?:(?:public|protected|private|static|final|synchronized|abstract|native)\s+)*"
    r"(?:<[^>]+>\s+)?[\w$.]+(?:<[^()]*>)?(?:\[\])*\s+(\w+)\s*\([^;]*$"
)
_STATEMENT_WORDS = {
    "if", "for", "while", "switch", "catch", "return", "throw", "new", "else",
    "try", "do", "case", "assert", "yield", "synchronized",
}


def _starts_declaration(line: str) -> bool:
    """Whether a line opens a method declaration, not a statement."""
    if _KOTLIN_DECLARATION.search(line):
        return True
    match = _JAVA_DECLARATION.match(line)
    if not match:
        return False
    first = line.strip().split()[0] if line.strip() else ""
    before_paren = line[:line.find("(")]
    return (first not in _STATEMENT_WORDS
            and match.group(1) not in _STATEMENT_WORDS
            and "=" not in before_paren and " new " not in f" {before_paren} ")
_LITERAL = re.compile(r'"[^"]*"|\'[^\']\'|(?<![\w.])\d+(?:\.\d+)?[LlFf]?\b')


def _parameters_of_declaration(lines: List[str], index: int) -> set:
    """Parameter names of the declaration enclosing ``lines[index]``."""
    for start in range(index, -1, -1):
        if not _starts_declaration(lines[start]):
            continue
        header = " ".join(lines[start:start + 12])
        opening = header.find("(")
        depth, end = 0, -1
        for position in range(opening, len(header)):
            if header[position] == "(":
                depth += 1
            elif header[position] == ")":
                depth -= 1
                if depth == 0:
                    end = position
                    break
        if opening < 0 or end < 0:
            return set()
        names = set()
        for parameter in header[opening + 1:end].split(","):
            parameter = re.sub(r"@[\w.]+(?:\([^)]*\))?", " ", parameter).strip()
            kotlin = re.match(r"(?:va[lr]\s+)?(\w+)\s*:", parameter)
            java = re.search(r"(\w+)\s*$", parameter)
            if kotlin:
                names.add(kotlin.group(1))
            elif java:
                names.add(java.group(1))
        return names
    return set()


def _guard_of(lines: List[str], index: int) -> str:
    """The condition under which ``lines[index]`` rejects, or ""."""
    line = lines[index]
    inline = re.search(r"\b(?:require|check)\s*\((.*)\)", line)
    if inline:
        return inline.group(1)
    for back in range(index, max(-1, index - 4), -1):
        match = re.search(r"\bif\s*\((.*)\)", lines[back])
        if match:
            return match.group(1)
        when_arm = re.match(r"\s*([^-]+?)\s*->", lines[back])
        if when_arm and back == index:
            subject = next((
                re.search(r"\bwhen\s*\((\w+)\)", lines[earlier]).group(1)
                for earlier in range(index, max(-1, index - 15), -1)
                if re.search(r"\bwhen\s*\((\w+)\)", lines[earlier])
            ), "")
            if subject:
                return f"{subject} == {when_arm.group(1)}"
    return ""


def caller_value_rejections(error: Dict, edits: List[Dict]) -> List[Dict]:
    """New rejections of values the method's callers supply.

    A patch can satisfy a constraint rule by refusing the value the rule
    dislikes. When that value is a literal the application chose, that is a
    fix. When it arrives as a parameter -- a setting the user picked, a token
    already stored, a protocol field -- every caller that passes it today
    stops working. run_20261009_012517 produced two such pull requests ready
    to publish: org.cry.otp threw for shaType 0, so every stored SHA-1 TOTP
    token (the RFC 6238 default) would stop generating codes, and
    droid_scep threw for every RSA key size but 3072 and 4096, breaking the
    2048-bit enrolments servers commonly require and the stronger 8192.

    Flagged: a production line the patch adds that throws (or calls
    require/check/error), guarded by a condition that compares a parameter
    of the enclosing method with a literal. Null and emptiness checks are
    defensive validation, not a narrowing of accepted values, and are left
    alone.
    """
    repo_root = Path(str(error.get("repository_path", "")))
    production = [edit for edit in edits if not _is_test_edit(edit)]
    overlay = _coordinated_overlay(repo_root, production)
    issues: List[Dict] = []
    for edit in production:
        relative = str(edit.get("source_file_path", ""))
        original_lines = {
            line.strip() for line in blank_comments_and_literals(
                str(edit.get("original_code", ""))).splitlines()
        }
        corrected = str(edit.get("corrected_code", ""))
        content = overlay.get(relative) or corrected
        lines = content.splitlines()
        blanked = blank_comments_and_literals(content).splitlines()
        found = []
        added = {
            line.strip() for line in blank_comments_and_literals(corrected).splitlines()
            if line.strip() and line.strip() not in original_lines
        }
        for index, line in enumerate(blanked):
            if line.strip() not in added or not _REJECTION.search(line):
                continue
            guard = _guard_of(lines, index)
            if not guard or re.search(r"\bnull\b|isEmpty|isBlank|\.length\s*==\s*0", guard):
                continue
            parameters = _parameters_of_declaration(lines, index)
            used = sorted(
                name for name in parameters
                if re.search(rf"(?<![\w.]){re.escape(name)}\b", guard)
            )
            if used and _LITERAL.search(guard):
                found.append(f"{', '.join(used)}: if ({guard.strip()[:120]}) -> {line.strip()[:80]}")
        if found:
            issues.append({
                "code": "REJECTS_CALLER_SUPPLIED_VALUE",
                "path": relative,
                "details": [
                    "the patch makes the method reject values its callers may "
                    "already pass -- a user setting, a stored record or a "
                    "protocol field -- so existing users stop working: "
                    + " | ".join(found[:3])
                    + ". Keep accepting the values existing callers supply "
                    "(for example keep the old value working for stored data "
                    "while new data uses a compliant one), or decide that no "
                    "client-only fix is safe."
                ],
            })
    return issues


_VALUE_LITERAL = r"(?:-?\d[\d_]*[lLfF]?|\"[^\"\n]*\")"
# A value replaced by literals chosen by the patch: `x == 3072 ? 3072 : 4096`,
# `if (x == 3072) 3072 else 4096`, `x.coerceIn(..)`, `Math.max(x, 2048)`.
_VALUE_OVERRIDE = re.compile(
    rf"(?<![\w.])(?P<a>[a-z]\w*)\s*[=!]=\s*{_VALUE_LITERAL}\s*\?\s*{_VALUE_LITERAL}\s*:\s*{_VALUE_LITERAL}"
    rf"|\bif\s*\(\s*(?P<b>[a-z]\w*)\s*[=!]=\s*{_VALUE_LITERAL}\s*\)\s*{_VALUE_LITERAL}\s*else\s*{_VALUE_LITERAL}"
    r"|(?<![\w.])(?P<c>[a-z]\w*)\s*\.\s*coerce(?:In|AtLeast|AtMost)\s*\("
    rf"|\b(?:Math\s*\.\s*)?(?:max|min|maxOf|minOf)\s*\(\s*(?P<d>[a-z]\w*)\s*,\s*{_VALUE_LITERAL}\s*\)"
    rf"|\b(?:Math\s*\.\s*)?(?:max|min|maxOf|minOf)\s*\(\s*{_VALUE_LITERAL}\s*,\s*(?P<e>[a-z]\w*)\s*\)"
)
_SUBJECT_COMPARED = re.compile(
    rf"(?<![\w.])(?P<s>[a-z]\w*)\s*(?:[=!]=|[<>]=?)\s*{_VALUE_LITERAL}"
    r"|(?<![\w.])(?P<s2>[a-z]\w*)\s+(?:!?in)\s+"
    r"|\bwhen\s*\(\s*(?P<s3>[a-z]\w*)\s*\)"
)
# A literal produced by a branch: after `? ... :`, `else`, `?:` or `->`.
_LITERAL_BRANCH = re.compile(
    rf"(?:\?(?![:.])[^:]*:|\belse\b|\?:|->)\s*\(?\s*{_VALUE_LITERAL}\s*\)?\s*[;,)}}]?\s*$"
    rf"|(?:\?(?![:.])\s*{_VALUE_LITERAL}\s*:)"
)
_REASSIGNED_TO_LITERAL = re.compile(
    rf"^\s*(?P<s>[a-z]\w*)\s*=(?!=)\s*{_VALUE_LITERAL}\s*;?\s*$"
)
# Where a local's value comes from outside the method: a setting, a
# restriction, a widget, an intent extra, a parsed string.
_EXTERNAL_VALUE = re.compile(
    r"\.\s*get(?:Int|Long|String|Boolean|Float|Selected\w*|Text|\w*Extra)\s*\("
    r"|\bparse(?:Int|Long|Double|Float)\s*\(|\bto(?:Int|Long)(?:OrNull)?\s*\(|[Pp]refs?\b"
    r"|[Pp]references\b|[Ss]ettings\b|[Rr]estrictions\b"
)


def caller_value_overrides(error: Dict, edits: List[Dict]) -> List[Dict]:
    """New code that swaps the value a caller or the user chose for another.

    caller_value_rejections catches a patch that refuses such a value; this
    is the same harm done quietly. droid_scep's CertReq (exp_final_20261019)
    became `isKeyLen == 3072 ? 3072 : 4096` in three places, so a 2048-bit
    key that many SCEP servers require, or the stronger 8192 the user picked
    in the spinner, silently became 4096: the detector's rule is satisfied
    and the user's choice is gone.

    Flagged: an added production line that maps a value onto literals of
    the patch's choosing (comparison-and-literal conditional, coerceIn,
    max/min with a literal) when that value is a parameter of the enclosing
    method or a local the patch reads from a setting, restriction, widget,
    intent or parsed input.
    """
    issues: List[Dict] = []
    for edit in edits:
        if _is_test_edit(edit):
            continue
        original_lines = {
            re.sub(r"\s+", " ", line.strip())
            for line in str(edit.get("original_code", "")).splitlines()
        }
        corrected = str(edit.get("corrected_code", ""))
        lines = corrected.splitlines()
        found = []
        for index, line in enumerate(lines):
            if re.sub(r"\s+", " ", line.strip()) in original_lines:
                continue
            code = line.split("//", 1)[0]
            subjects = [next(group for group in match.groups() if group)
                        for match in _VALUE_OVERRIDE.finditer(code)]
            # The same mapping in other spellings: `x == 3072 || x == 4096 ? x
            # : 4096` (the model's second draft for droid_scep), `if (x in
            # allowed) x else 4096`, `when (x) { ... else -> 4096 }`, or a
            # plain `x = 4096` reassignment.
            if _LITERAL_BRANCH.search(code):
                subjects += [next(group for group in match.groups() if group)
                             for match in _SUBJECT_COMPARED.finditer(code)]
            reassigned = _REASSIGNED_TO_LITERAL.match(code)
            if reassigned:
                subjects.append(reassigned.group("s"))
            for subject in dict.fromkeys(subjects):
                initializer = next((
                    value.group(1) for value in (
                        re.search(rf"(?<![\w.]){re.escape(subject)}\s*(?::\s*[\w.<>?]+)?\s*=(?!=)\s*(.+)",
                                  lines[earlier])
                        for earlier in range(index - 1, -1, -1)
                    ) if value
                ), "")
                if (subject in _parameters_of_declaration(lines, index)
                        or _EXTERNAL_VALUE.search(initializer)):
                    found.append(f"{subject}: {line.strip()[:110]}")
        if found:
            issues.append({
                "code": "CALLER_SUPPLIED_VALUE_OVERRIDDEN",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    "the patch replaces a value the caller or the user chose with "
                    "one the patch picks, so existing choices silently stop "
                    "taking effect: " + " | ".join(found[:3])
                    + ". Keep honouring the values existing callers and settings "
                    "supply (offer a compliant default for new choices), or decide "
                    "that no client-only fix is safe."
                ],
            })
    return issues


# `if (<condition>) {` whose condition reads an option: a setting, a flag the
# user turns on, an allow/insecure/trust switch. A build-type check is not one.
_OPTION_GUARD = re.compile(
    r"\bif\s*\((?P<guard>[^{};]*?(?:[Aa]llow|[Ii]nsecure|[Uu]nsafe|[Ee]nabled|[Ss]etting"
    r"|[Pp]ref|[Oo]ption|[Tt]rust|[Ii]gnore|[Ss]kip|[Dd]isable)[^{};]*)\)\s*\{"
)


# A field or method declared with a modifier: what a branch shares with the
# class, unlike a local of the method it sits in.
_MEMBER_DECLARATION = (
    r"\b(?:private|protected|public|static|internal|final\s+static)\b[^;=\n(]*"
    r"(?<![\w.]){name}\s*[;=:(]"
)


_KEYWORDS = frozenset({
    "throw", "throws", "return", "null", "true", "false", "this", "super", "else",
    "when", "while", "break", "continue", "catch", "finally", "case", "default",
    "final", "static", "void", "class", "object", "override", "private", "public",
    "protected", "internal", "suspend", "lateinit", "synchronized", "instanceof",
})


def _braced_body(code: str, opening: int) -> str:
    depth = 0
    for index in range(opening, len(code)):
        if code[index] == "{":
            depth += 1
        elif code[index] == "}":
            depth -= 1
            if depth == 0:
                return code[opening + 1:index]
    return ""


# Words that open a statement or an expression, never a declaration: neither
# the type nor the name of a method header. BitBanana's continuePressed
# (frozen run, 2026-11-13) has a comment ending "...get here..." right above
# `if (mNumFails >= MAX) {`, which read as a method named `if` returning
# `here...`; its body ended before the edited call, so the method's own
# `catch (GeneralSecurityException | IOException e)` was missed and every
# draft was refused. Java and Kotlin control flow alike (`else if`, `when`).
_STATEMENT_WORDS = (
    r"(?:if|for|while|switch|catch|synchronized|when|try|do|else|return|new"
    r"|throw|super|this|assert|case|yield)")
_METHOD_HEADER = re.compile(
    rf"\b(?:fun\s+\w+|(?!{_STATEMENT_WORDS}\b)[\w<>\[\],.?]+\s+(?!{_STATEMENT_WORDS}\b)\w+)"
    r"\s*\([^)]*\)\s*(?::\s*[\w<>?.,\s]+)?"
    r"(?:throws\s+(?P<throws>[^{]+))?\{")


def enclosing_method_handles(repo_root: Path, edit: Dict, exception: str) -> bool:
    """Whether the unchanged method around an edited fragment already handles `exception`.

    The exception check reads only the edit's corrected_code. MTG Familiar's
    fetch (exp_final_20261019 subset_policy) kept its outer
    `catch (IOException e)` outside the edited anchor, so the fragment
    seemed to leave IOException unhandled and every draft was refused.
    """
    relative = str(edit.get("source_file_path", ""))
    anchor = str(edit.get("original_code", "")).strip()
    if not relative or not anchor:
        return False
    try:
        source = (Path(repo_root) / relative).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    position = source.find(anchor)
    if position < 0:
        return False
    header = None
    for match in _METHOD_HEADER.finditer(source, 0, position + 1):
        header = match
    if header is None:
        return False
    body = _braced_body(source, header.end() - 1)
    if position + len(anchor) > header.end() + len(body) + 1:
        return False
    names = {exception, "Exception", "Throwable"}
    declared = {item.strip().rsplit(".", 1)[-1]
                for item in (header.group("throws") or "").split(",") if item.strip()}
    if names & declared:
        return True
    outside = body.replace(anchor, "")
    return any(re.search(rf"\bcatch\s*\(\s*(?:final\s+)?[\w.\s|]*\b{re.escape(name)}\b", outside)
               for name in names)


# A method whose name says it serves the opted-in, weaker mode.
_OPT_IN_MEMBER = re.compile(
    r"(?i)\b\w*(?:insecure|unsafe|trustall|selfsigned|noverif|allowall|acceptall)\w*\s*\(")


# Names a fragment introduces itself: Kotlin val/var (destructured too), a
# Java typed local, a for-loop variable, a lambda parameter. The whitespace
# before a Java local's name stays on one line, so a Kotlin statement ending
# in a name never makes the next line's reassignment look like a declaration,
# and a statement keyword (`return name;`, `else name = x`) is not a type.
_LOCAL_DECLARATIONS = (
    re.compile(r"\b(?:val|var)\s+(\w+)"),
    re.compile(r"\b(?:val|var)\s*\(([^()]*)\)"),
    re.compile(r"(?<![\w.])(?!(?:return|throw|else|do|try|finally|synchronized|new|case|yield"
               r"|is|as|in|import|package)\b)"
               r"[\w.]+(?:<[\w<>\[\]?,. \t]*>)?(?:\[\])*\??[ \t]+(\w+)[ \t]*[;=](?!=)"),
    re.compile(r"\bfor\s*\(\s*(?:final\s+)?(?:[\w<>\[\]?.]+\s+)?(\w+)\s*(?::|\bin\b)"),
    re.compile(r"\bfor\s*\(\s*\(([^()]*)\)\s*in\b"),
    re.compile(r"\{\s*\(?([\w\s,:<>?.]*?)\)?\s*->"),
    re.compile(r"\(([\w\s,<>\[\]?.]*)\)\s*->"),
    re.compile(r"[(,=]\s*(\w+)\s*->"),
)
# A `when` block's branches (`when { allowInsecure -> ... }`) read names, they
# do not declare them.
_WHEN_BLOCK = re.compile(r"\bwhen\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*$")
# What a brace's header says it opens: a type body (class, object, interface,
# enum, record, Java anonymous class) or code (a function, init, accessor, a
# lambda after `=` or `->`). The last of these before the brace decides.
# `enum` and `record` are soft keywords, so they need a capitalised name after
# them: `return record` above an `if` does not open a type.
_BODY_OPENER = re.compile(
    r"(?P<type>(?<![\w:.$])(?:class|interface)\s+[A-Za-z_]|(?<![\w:.$])(?:enum|record)\s+[A-Z]"
    r"|(?<![\w:.$])object\b|\bnew\s+[\w.]+(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?\s*$)"
    r"|(?P<code>(?<![\w$])(?:fun|init|constructor|get|set|static)\b|->|=)")


def _opens_type_body(code: str, opening: int) -> bool:
    """Does the `{` at `opening` open a class-like body rather than code?"""
    boundary = max(code.rfind(character, 0, opening) for character in ";{}")
    header = code[boundary + 1:opening]
    while True:
        # Parameter lists and call arguments say nothing about the body.
        stripped = re.sub(r"\([^()]*\)", " ", header)
        if stripped == header:
            break
        header = stripped
    last = None
    for match in _BODY_OPENER.finditer(header):
        last = match
    return bool(last and last.group("type"))


def _is_member_level(code: str, position: int) -> bool:
    """Is `position` in `code` directly inside a type body or at file level?"""
    depth = 0
    for index in range(position - 1, -1, -1):
        if code[index] == "}":
            depth += 1
        elif code[index] == "{":
            if depth == 0:
                return _opens_type_body(code, index)
            depth -= 1
    return True


def _names_declared_in(code: str, context: str = "", offset: int = 0) -> set:
    """Local names `code` declares itself, so a same-named field elsewhere is another variable.

    With `context` (the whole file, comments and literals blanked, `code`
    starting at `offset`), a declaration directly in a class body or at file
    level is a member, not a local, and is not returned: a field declared
    with its initializer inside the snippet is still the field the option
    reads (DSub's insecure factory, written `private val f = trustAll()`).
    """
    names = set()
    for pattern in _LOCAL_DECLARATIONS:
        for match in pattern.finditer(code):
            if match.group(0).startswith("{") and _WHEN_BLOCK.search(code[:match.start()]):
                continue
            if context and _is_member_level(context, offset + match.start(1)):
                continue
            for part in match.group(1).split(","):
                # `a: Type` (Kotlin) or `Type a` (Java): the name is the word
                # before the colon, or the last word without one.
                words = re.findall(r"\w+", part.split(":", 1)[0])
                if words and words[-1] not in _KEYWORDS:
                    names.add(words[-1])
    return names


def _option_only_fields_changed(original: str, original_code: str,
                                production: List[Dict], relative: str) -> List[str]:
    """Fields a patch reassigns that the file only ever uses for an option.

    DSub's second draft kept every name -- insecureSslSocketFactory is still
    built in the constructor -- and only swapped the trust-all manager for
    the platform one, so the "allow insecure" branch now installs a factory
    that refuses the self-signed certificate it exists for. What decides it
    is where the field is read: inside `if (isAllowInsecureEnabled(...))`
    and in getInsecureSSLSocketFactory(), nowhere else.
    """
    spans = []
    for match in _OPTION_GUARD.finditer(original_code):
        guard = match.group("guard")
        if "BuildConfig" in guard or re.search(r"\bnull\b|isEmpty|isBlank", guard):
            continue
        body = _braced_body(original_code, match.end() - 1)
        spans.append((match.start(), match.end() + len(body)))
    for match in _OPT_IN_MEMBER.finditer(original_code):
        opening = original_code.find("{", match.end())
        if opening < 0 or ";" in original_code[match.end():opening]:
            continue
        body = _braced_body(original_code, opening)
        spans.append((match.start(), opening + len(body) + 1))
    if not spans:
        return []
    found = []
    for edit in production:
        if str(edit.get("source_file_path", "")) != relative:
            continue
        snippet = str(edit.get("original_code", ""))
        start = original.find(snippet) if snippet else -1
        if start < 0 or snippet == str(edit.get("corrected_code", "")):
            continue
        end = start + len(snippet)
        snippet_code = blank_comments_and_literals(snippet)
        # A local the snippet declares is not the field another method reads:
        # Clash Fest's generateAndroidKeyStoreKey declares `kpg` and `now`,
        # and so does mintSelfSigned, an opt-in member by its name, so every
        # other use sat inside the option and the gate fired on a new local.
        assigned = (set(re.findall(r"(?<![\w.])([a-z]\w*)\s*=(?!=)", snippet_code))
                    - _names_declared_in(snippet_code, original_code, start))
        for name in sorted(assigned):
            uses = [use.start() for use in re.finditer(rf"(?<![\w.]){re.escape(name)}\b", original_code)
                    if not start <= use.start() < end]
            declared = re.search(rf"\b[\w<>\[\]?]+\s+{re.escape(name)}\s*[;=]|\b(?:val|var)\s+{re.escape(name)}\b",
                                 original_code[:start])
            reads = [position for position in uses
                     if not (declared and declared.start() <= position < declared.end())]
            if reads and all(any(low <= position < high for low, high in spans) for position in reads):
                found.append(f"{name} is changed and is only used where the option is on")
    return found


def neutralized_option_branches(error: Dict, edits: List[Dict]) -> List[Dict]:
    """An option the patch keeps on screen but empties of its effect.

    removed_user_setting catches a patch that stops reading a setting. DSub
    (exp_final_20261019) kept `if (Util.isAllowInsecureEnabled(...))` and
    deleted the trust-all factory and the accept-all hostname verifier that
    branch installed, so "allow insecure connections" -- the switch users
    turn on for a home Subsonic server with a self-signed certificate --
    still shows, and does nothing.

    Flagged: a branch guarded by an option-like condition, present in the
    original file and still present in the patched one, whose original body
    used a name the patched file no longer contains at all.
    """
    repo_root = Path(str(error.get("repository_path", "")))
    production = [edit for edit in edits if not _is_test_edit(edit)]
    overlay = _coordinated_overlay(repo_root, production)
    issues: List[Dict] = []
    for relative, patched in overlay.items():
        try:
            original = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        original_code = blank_comments_and_literals(original)
        patched_code = blank_comments_and_literals(patched)
        patched_names = set(re.findall(r"[A-Za-z_]\w*", patched_code))
        found = []
        for match in _OPTION_GUARD.finditer(original_code):
            guard = re.sub(r"\s+", " ", match.group("guard")).strip()
            if "BuildConfig" in guard or re.search(r"\bnull\b|isEmpty|isBlank", guard):
                continue
            if guard not in re.sub(r"\s+", " ", patched_code):
                continue
            body = _braced_body(original_code, match.end() - 1)
            # A name the branch shares with the rest of the file (a field, a
            # helper), not a keyword or a local the branch declares itself.
            elsewhere = original_code[:match.start()] + original_code[match.end() + len(body):]
            # Only a member counts: bitbanana's setup activities lost the
            # local `hashedValue` to an inlined call while the emergency-PIN
            # branch kept working (exp_final_20261019 subset_gates).
            gone = sorted({
                name for name in re.findall(r"(?<![\w.])([a-z]\w{3,})\b", body)
                if name not in patched_names and name not in _KEYWORDS
                and re.search(rf"(?<![\w.]){re.escape(name)}\b", elsewhere)
                and re.search(_MEMBER_DECLARATION.format(name=re.escape(name)), original_code)
            })
            if gone:
                found.append(f"if ({guard[:80]}): {', '.join(gone[:3])} no longer exist")
        found += _option_only_fields_changed(original, original_code, production, relative)
        if found:
            issues.append({
                "code": "USER_OPTION_NEUTRALIZED",
                "path": relative,
                "details": [
                    "the patch keeps an option's check but removes what the option "
                    "turned on, so users who enabled it silently lose that behaviour: "
                    + " | ".join(found[:2])
                    + ". Keep the opted-in branch working and change only what runs "
                    "when the option is off; if the finding is the opted-in behaviour "
                    "itself, report it to the maintainer instead of patching it."
                ],
            })
    return issues


def _blank_comments(code: str) -> str:
    """`code` with comments blanked and string literals kept, offsets preserved."""
    characters = list(code)
    for match in _COMMENT_OR_LITERAL.finditer(code):
        if not match.group().startswith("/"):
            continue
        for index in range(match.start(), match.end()):
            if characters[index] != "\n":
                characters[index] = " "
    return "".join(characters)


def _closing_bracket(code: str, opening: int) -> Optional[int]:
    """Index of the bracket closing the one at `opening`, or None."""
    depth = 0
    for index in range(opening, len(code)):
        if code[index] in "([{":
            depth += 1
        elif code[index] in ")]}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _statement_end(code: str, start: int) -> int:
    """End of the statement running from `start`: `;` or a line not continued."""
    depth, index = 0, start
    while index < len(code):
        character = code[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth < 0:
                return index
        elif depth == 0 and character == ";":
            return index
        elif depth == 0 and character == "\n":
            before = code[start:index].rstrip()
            after = code[index:].lstrip()
            if not (after[:1] in {".", "?"} or before[-1:] in set("=+-*/,(&|")):
                return index
        index += 1
    return len(code)


_KEY_FUNCTION = re.compile(
    r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w$.]+\.)?(?P<kotlin>[A-Za-z_$][\w$]*)\s*\("
    r"|^[ \t]*(?:@[\w.]+(?:\([^)]*\))?\s*)*"
    r"(?:(?:public|protected|private|static|final|synchronized)\s+)*"
    r"(?P<type>[\w$.<>\[\],?]+)\s+(?P<java>[A-Za-z_$][\w$]*)\s*\(",
    re.MULTILINE,
)
# tokn's `private val keystore: KeyStore by lazy { KeyStore.getInstance(..) }`
# is a member holding the keystore as much as an `=` one.
_KEY_PROPERTY = re.compile(
    r"\b(?:val|var)\s+(?P<kotlin>[A-Za-z_]\w*)\b[^=\n;{]*?(?:=(?!=)|\bby\s+lazy\b)"
    r"|\b[A-Z][\w$.<>\[\]]*\s+(?P<java>[a-z_]\w*)\s*=(?!=)"
)
# A Kotlin `private class Vault(private val ctx: Context) {` is a class
# header, not a function returning `class`.
_NOT_JAVA_RETURN_TYPES = frozenset({"return", "new", "throw", "else", "case", "yield",
                                    "class", "interface", "object", "enum", "record"})
_CIPHER_INIT = re.compile(r"(?<![\w$])init\s*\(")
_CALLER_IV_SPEC = re.compile(r"\b(GCMParameterSpec|IvParameterSpec)\s*\(")
_PROVIDER_ARGUMENTS = re.compile(r"\bgetInstance\s*\(([^()]*)\)")
_KEY_GENERATOR = re.compile(r"\bKeyGenerator\s*\.\s*getInstance\s*\(([^()]*)\)")
# Key material the code builds itself: never an AndroidKeyStore key. A key
# unwrapped by a keystore RSA key (the pre-API-23 way of keeping an AES key)
# is software too, as is a KeyGenerator without the keystore provider.
_SOFTWARE_KEY = re.compile(r"\bSecretKeySpec\s*\(|\.\s*(?:generateSecret|unwrap)\s*\(")
_RANDOMIZATION_OFF = re.compile(
    r"\.\s*setRandomizedEncryptionRequired\s*\(\s*false\s*\)")
_KEYSTORE_PROVIDER = '"AndroidKeyStore"'
_SECRET_KEY_TYPE = re.compile(r"\b(?:SecretKey|Key)\b")
# The value an expression starts from: `(SecretKey) ks.getKey(..)` -> ks.
_EXPRESSION_HEAD = re.compile(
    r"\s*(?:\(\s*[A-Z][\w$.<>\[\]?]*\s*\)\s*|\(\s*|this\s*\.\s*)*([A-Za-z_][\w$]*)")


class _KeySources:
    """Where a key expression in one file comes from, read from that file alone."""

    def __init__(self, text: str):
        self.text = _blank_comments(text)
        self.code = blank_comments_and_literals(text)
        self.provider_names = set(re.findall(
            rf"\b(?:val|var|String)\s+(\w+)\s*(?::\s*String\s*)?=\s*{_KEYSTORE_PROVIDER}",
            self.text,
        ))
        self.functions: List[Dict] = []
        for match in _KEY_FUNCTION.finditer(self.code):
            name = match.group("kotlin") or match.group("java")
            if (name in _NOT_DECLARATION_NAMES
                    or (match.group("java") and match.group("type") in _NOT_JAVA_RETURN_TYPES)):
                continue
            close = _closing_bracket(self.code, match.end() - 1)
            if close is None:
                continue
            # The body opens on the declaration's own line; an abstract
            # member has none, and must not borrow the next declaration's.
            body = re.search(r"[{=;\n]", self.code[close + 1:close + 300])
            if not body or body.group() in {";", "\n"}:
                continue
            opening = close + 1 + body.start()
            end = (_closing_bracket(self.code, opening) if body.group() == "{"
                   else _statement_end(self.code, opening))
            if end is None:
                continue
            self.functions.append({
                "name": name, "start": match.start(), "body": opening, "end": end,
                "parameters": self._parameters(self.code[match.end():close]),
            })
        self.members: Dict[str, List[tuple]] = {}
        self.locals: List[tuple] = []
        for match in _KEY_PROPERTY.finditer(self.code):
            name = match.group("kotlin") or match.group("java")
            # (declaration start, end, initializer start)
            span = (match.start(), _statement_end(self.code, match.end() - 1), match.end())
            if any(item["body"] < match.start() < item["end"] for item in self.functions):
                self.locals.append((name, span))
            else:
                self.members.setdefault(name, []).append(span)

    @staticmethod
    def _parameters(text: str) -> Dict[str, str]:
        parameters = {}
        for part in _split_parameters(re.sub(r"@[\w.]+(?:\([^)]*\))?\s*", "", text)):
            if ":" in part:
                name, _, type_text = part.partition(":")
                name = name.split()[-1] if name.split() else ""
            else:
                words = part.split()
                name, type_text = (words[-1], " ".join(words[:-1])) if len(words) > 1 else ("", "")
            if name:
                parameters[name] = type_text.split("=")[0].strip()
        return parameters

    def enclosing(self, offset: int) -> Optional[Dict]:
        inside = [item for item in self.functions if item["body"] < offset < item["end"]]
        return min(inside, key=lambda item: item["end"] - item["body"]) if inside else None

    def _is_provider(self, argument: str) -> bool:
        argument = argument.strip()
        return argument == _KEYSTORE_PROVIDER or argument.split(".")[-1] in self.provider_names

    def _marker(self, text: str) -> bool:
        if re.search(r"\bKeyGenParameterSpec\b", text) and re.search(r"\bPURPOSE_ENCRYPT\b", text):
            return True
        return any(self._is_provider(argument)
                   for match in _PROVIDER_ARGUMENTS.finditer(text)
                   for argument in match.group(1).split(","))

    def _software(self, text: str, whole_file: bool = False) -> bool:
        # KeyPass' createKey calls KeyGenerator.getInstance(ALGORITHM) without
        # a provider; initialised with a KeyGenParameterSpec it still lands in
        # AndroidKeyStore. Across a whole file the two need not be one key.
        if _SOFTWARE_KEY.search(text):
            return True
        if not whole_file and re.search(r"\bKeyGenParameterSpec\b", text):
            return False
        return any(not any(self._is_provider(argument) for argument in match.group(1).split(","))
                   for match in _KEY_GENERATOR.finditer(text))

    def _generates_keystore_secret_key(self) -> bool:
        """A symmetric key generated in AndroidKeyStore; a bare alias lookup is not one."""
        if any(any(self._is_provider(argument) for argument in match.group(1).split(","))
               for match in _KEY_GENERATOR.finditer(self.text)):
            return True
        return (bool(re.search(r"\bKeyGenParameterSpec\b", self.text))
                and bool(re.search(r"\bPURPOSE_ENCRYPT\b", self.text))
                and not re.search(r"\bKeyPairGenerator\b", self.text))

    def _bound_local(self, name: str, function: Optional[Dict], offset: int) -> Optional[tuple]:
        bound = [span for local, span in self.locals
                 if local == name and function and function["body"] < span[0] < offset]
        return bound[-1] if bound else None

    def _returns(self, function: Dict) -> List[tuple]:
        """(expression, offset) of each value `function` returns."""
        if self.code[function["body"]] != "{":
            return [(self.text[function["body"] + 1:function["end"]], function["body"] + 1)]
        found = []
        for match in re.finditer(r"\breturn\b(?!@)", self.code[function["body"]:function["end"]]):
            start = function["body"] + match.end()
            if self.enclosing(start) is function:
                found.append((self.text[start:_statement_end(self.code, start)], start))
        return found

    def _resolve(self, expression: str, offset: int, following: set) -> Optional[str]:
        """The declaration the key `expression` at `offset` comes from, if a keystore one.

        Only the value the expression starts from is followed (a local's or
        member's initializer, a function's returned values), so a software
        key merely derived next to a keystore key is not taken for it.
        `following` holds the declarations on the current chain, against cycles.
        """
        if self._software(expression):
            return None
        if self._marker(expression):
            return expression.strip().splitlines()[0][:80]
        head = _EXPRESSION_HEAD.match(expression)
        if not head:
            return None
        name = head.group(1)
        function = self.enclosing(offset)
        if expression[head.end():].lstrip().startswith("("):
            for item in self.functions:
                if item["name"] != name or ("function", item["start"]) in following:
                    continue
                returns = self._returns(item)
                if any(self._software(value) for value, _ in returns):
                    continue
                following.add(("function", item["start"]))
                found = any(self._resolve(value, start, following) for value, start in returns)
                following.discard(("function", item["start"]))
                if found:
                    return name
            return None
        local = self._bound_local(name, function, offset)
        if local is None and function and name in function["parameters"]:
            return self._parameter_key(function, name, following)
        for span in [local] if local else self.members.get(name, []):
            if ("value", span[0]) in following:
                continue
            following.add(("value", span[0]))
            found = self._resolve(self.text[span[2]:span[1]], span[2], following)
            following.discard(("value", span[0]))
            if found:
                return self.text[span[0]:span[1]].strip().splitlines()[0][:80]
        return None

    def _parameter_key(self, function: Dict, name: str, following: set) -> Optional[str]:
        """A key parameter that is a keystore key wherever this file supplies it."""
        token = ("parameter", function["start"], name)
        if not _SECRET_KEY_TYPE.search(function["parameters"][name]) or token in following:
            return None
        position = list(function["parameters"]).index(name)
        calls = [match for match in re.finditer(
                     rf"(?:(?<=this\.)|(?<![\w$.])){re.escape(function['name'])}\s*\(", self.code)
                 if not any(item["start"] <= match.start() < item["body"] for item in self.functions)]
        if calls:
            following.add(token)
            for call in calls:
                arguments = _assertion_arguments(self.text, call.end())
                named = [argument.split("=", 1)[1] for argument in arguments
                         if re.match(rf"{re.escape(name)}\s*=(?!=)", argument)]
                positional = [argument for argument in arguments
                              if not re.match(r"[A-Za-z_]\w*\s*=(?!=)", argument)]
                argument = named[0] if named else (
                    positional[position] if position < len(positional) else "")
                if not self._resolve(argument, call.start(), following):
                    following.discard(token)
                    return None
            following.discard(token)
            return f"parameter {name}, a keystore key at every call in this file"
        # No caller in this file: SimpleNotes' encryptCredential(key: SecretKey,
        # ...) is handed the key its own androidKeyStoreKeyProvider generates,
        # and the file makes no other secret key.
        if self._software(self.text, whole_file=True) or not self._generates_keystore_secret_key():
            return None
        return (f"parameter {name} (the only secret key this file makes is its "
                "AndroidKeyStore one)")

    def keystore_key(self, expression: str, offset: int) -> Optional[str]:
        """Name of the keystore key `expression` evaluates to at `offset`, or None."""
        return self._resolve(expression, offset, set())

    def spec_kind(self, argument: str, offset: int) -> Optional[str]:
        match = _CALLER_IV_SPEC.search(argument)
        if match:
            return match.group(1)
        name = argument.strip()
        if not re.fullmatch(r"[A-Za-z_][\w$]*", name):
            return None
        function = self.enclosing(offset)
        spans = [span for local, span in self.locals
                 if local == name and function and function["body"] < span[0] < offset]
        for span in spans[-1:] or self.members.get(name, []):
            match = _CALLER_IV_SPEC.search(self.code[span[0]:span[1]])
            if match:
                return match.group(1)
        return None

    def caller_iv_encryptions(self) -> List[tuple]:
        """(key expression, spec kind, offset) of each encrypt init given an IV."""
        found = []
        for match in _CIPHER_INIT.finditer(self.code):
            arguments = _assertion_arguments(self.text, match.end())
            if len(arguments) < 3 or not re.search(r"\bENCRYPT_MODE\b", arguments[0]):
                continue
            kind = self.spec_kind(arguments[2], match.start())
            if kind:
                found.append((arguments[1], kind, match.start()))
        return found


def caller_iv_on_randomized_keystore_key(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A new encrypt init that hands an AndroidKeyStore key a caller-chosen IV.

    A keystore key requires randomized encryption unless its builder calls
    setRandomizedEncryptionRequired(false), and then Cipher.init(ENCRYPT_MODE,
    key, IvParameterSpec/GCMParameterSpec) throws "Caller-provided IV not
    permitted". SimpleNotes' encryptCredential, KeyPass' CryptoManager.encrypt
    and Salty's encodeCurrent (2026-10-22/25) all built and would have failed
    on every encryption. Amethyst#4642 was closed because the provider
    already chose a random IV.

    Only an init the original file lacks (by key expression and spec kind)
    is judged, and only when that key resolves, in the same file, to a key
    built with KeyGenParameterSpec for PURPOSE_ENCRYPT or read from
    AndroidKeyStore: Salty's encodeLegacy keeps its caller IV on a derived
    software key in the same file. A key built in another file is not judged.
    Only a setRandomizedEncryptionRequired(false) the original already has
    exempts the init: one the patch adds changes no key already generated,
    so those still refuse the IV, and it weakens every new one.

    A key the edited class only receives is followed to where the class (or
    a subclass) is constructed (_cross_file_keystore_key): Cryptomator Lite
    run_v6 (2026-10-30) gave BaseCipher.encrypt a SecureRandom IV through
    getIvParameterSpec(iv), for the key CryptoOperationsGCM reads from the
    keystore it generates with KeyGenParameterSpec and no
    setRandomizedEncryptionRequired(false). Neither the spec kind nor the key
    was visible in BaseCipher itself.
    """
    repo_root = Path(str(error.get("repository_path", "")))
    production = [edit for edit in edits if not _is_test_edit(edit)]
    issues: List[Dict] = []
    repository = _RepositorySources(repo_root)
    for relative, patched in _coordinated_overlay(repo_root, production).items():
        if not re.search(r"\bENCRYPT_MODE\b", patched):
            continue
        try:
            original = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            original = ""
        found = []
        judged = set()
        if (_CALLER_IV_SPEC.search(patched)
                and not (_RANDOMIZATION_OFF.search(_blank_comments(original))
                         and _RANDOMIZATION_OFF.search(_blank_comments(patched)))):
            before = {(_normalised_key(key), kind)
                      for key, kind, _ in _KeySources(original).caller_iv_encryptions()}
            sources = _KeySources(patched)
            for key, kind, offset in sources.caller_iv_encryptions():
                if (_normalised_key(key), kind) in before:
                    continue
                source = sources.keystore_key(key, offset)
                if source:
                    judged.add(_normalised_key(key))
                    found.append(f"init(ENCRYPT_MODE, {key.strip()}, {kind}(...)), key from "
                                 f"{source}")
        for key, init, offset in _new_parameter_encryptions(original, patched):
            if key in judged:
                continue
            link = _cross_file_keystore_key(repository, patched, key, offset)
            if link and all(item["randomized_encryption_required"] is True
                            for item in link["key_generated"]):
                judged.add(key)
                found.append(f"{init}, key from {link['key_from'][0]}; generated at "
                             f"{link['key_generated'][0]['source_file_path']}:"
                             f"{link['key_generated'][0]['line']}")
        if found:
            issues.append({
                "code": "CALLER_IV_REFUSED_BY_KEYSTORE_KEY",
                "path": relative,
                "details": [
                    "an AndroidKeyStore key requires randomized encryption by default, so "
                    "Cipher.init refuses a caller-provided IV and every encryption would "
                    "throw: " + " | ".join(found[:2])
                    + ". Let the provider choose the IV (init without parameters, "
                    "optionally with a SecureRandom) and keep reading cipher.iv. Turning "
                    "randomization off in the key's builder does not change keys already "
                    "generated and weakens new ones."
                ],
            })
    return issues


def _normalised_key(expression: str) -> str:
    return re.sub(r"^this\.|\s+", "", str(expression or ""))


def _new_parameter_encryptions(original: str, patched: str) -> List[tuple]:
    """(key, init text, offset in patched) of each encrypt init given parameters it lacked.

    An init with a third argument other than a SecureRandom (an IV or nonce
    spec, or an expression producing one) whose cipher and key had no such
    encryption before the patch.
    """
    from cognalys.extraction.iv_provenance import cipher_inits

    def caller_chosen(text: str) -> List[Dict]:
        return [item for item in cipher_inits(text)
                if item["mode"] == "encrypt" and not item["provider_iv"]
                and len(item["arguments"]) >= 3]

    before = {(item["receiver"], _normalised_key(item["arguments"][1]))
              for item in caller_chosen(original)}
    found = []
    for item in caller_chosen(patched):
        key = _normalised_key(item["arguments"][1])
        if (item["receiver"], key) not in before:
            found.append((key, item["text"], item["start"]))
    return found


class _RepositorySources:
    """The checkout's production Java and Kotlin sources, read once."""

    _LIMIT = 6000

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self._files: Optional[Dict[str, str]] = None

    def files(self) -> Dict[str, str]:
        if self._files is None:
            self._files = {}
            if self.repo_root.parts and self.repo_root.is_dir():
                for path in self.repo_root.rglob("*"):
                    if len(self._files) >= self._LIMIT:
                        break
                    if path.suffix not in _SOURCE_EXTENSIONS or not path.is_file():
                        continue
                    relative = path.relative_to(self.repo_root).as_posix()
                    if (_SKIP_DIRECTORIES & set(path.parts)) or is_test_source_path(relative):
                        continue
                    try:
                        self._files[relative] = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
        return self._files


# KeyGenParameterSpec.Builder (a key generated in AndroidKeyStore) and
# KeyProtection.Builder (a key imported into it): where a keystore key gets
# the properties the platform then enforces on every Cipher.init.
_KEYSTORE_KEY_BUILDER = re.compile(r"\b(?P<kind>KeyGenParameterSpec|KeyProtection)\s*\.\s*Builder\s*\(")
# A symmetric key: made by a KeyGenerator or imported as a SecretKeyEntry.
# A key pair's public half encrypts outside the keystore, with no IV rule.
_SYMMETRIC_KEYSTORE_KEY = re.compile(r"\bKeyGenerator\s*\.\s*getInstance\s*\(|\bSecretKeyEntry\b")
_RANDOMIZATION_SETTING = re.compile(r"\.\s*setRandomizedEncryptionRequired\s*\(")
# A key a construction site reads from a keystore or generates there.
_KEYSTORE_KEY_READ = re.compile(r"\.\s*(?:getKey|getEntry|generateKey)\s*\(")


def keystore_secret_key_builders(text: str) -> List[Dict]:
    """The symmetric keystore keys a file generates or imports for encryption.

    randomized_encryption_required: True when the file never calls
    setRandomizedEncryptionRequired (the platform default), False when it
    passes false, None when it passes anything else.
    """
    code = blank_comments_and_literals(text)
    if not _SYMMETRIC_KEYSTORE_KEY.search(code):
        return []
    plain = _blank_comments(text)
    if _RANDOMIZATION_OFF.search(plain):
        required: Optional[bool] = False
    elif _RANDOMIZATION_SETTING.search(code):
        required = None
    else:
        required = True
    found = []
    for match in _KEYSTORE_KEY_BUILDER.finditer(code):
        arguments = _assertion_arguments(plain, match.end())
        purposes = (arguments[1] if match.group("kind") == "KeyGenParameterSpec"
                    and len(arguments) > 1 else arguments[0])
        if not re.search(r"\bPURPOSE_ENCRYPT\b", purposes):
            continue
        end = _statement_end(code, match.start())
        found.append({
            "line": code.count("\n", 0, match.start()) + 1,
            "builder": re.sub(r"\s+", " ", plain[match.start():end]).strip()[:240],
            "randomized_encryption_required": required,
        })
    return found


def _local_initializer(code: str, name: str, position: int) -> str:
    found = ""
    for match in re.finditer(
            rf"(?<![\w.$]){re.escape(name)}\s*(?::\s*[\w.<>?]+\s*)?=(?!=)\s*([^;\n]+)",
            code[:position]):
        found = match.group(1)
    return found


# A declaration's text before its name: modifiers and annotations only.
_DECLARATION_PREFIX = re.compile(
    r"^\s*(?:(?:public|protected|private|internal)\s+|@[\w.]+(?:\([^)]*\))?\s*)*$")


def _parameter_name(part: str) -> str:
    """The name in one Java or Kotlin parameter declaration."""
    text = re.sub(r"@[\w.]+(?:\([^)]*\))?\s*", "", part).split("=")[0]
    words = (text.partition(":")[0] if ":" in text else re.sub(r"<[^>]*>", "", text)).split()
    return words[-1].lstrip(".") if words else ""


def _arguments_between(code: str, opening: int) -> Optional[List[str]]:
    close = _closing_bracket(code, opening)
    return None if close is None else _split_parameters(code[opening + 1:close])


def _constructors(code: str, class_name: str) -> List[Dict]:
    """The constructors `code` (comments and literals blanked) declares for `class_name`.

    Each: its parameter names in order, the ones a Kotlin primary
    constructor makes properties (val/var), the text its assignments are in
    (the class body for a Kotlin primary constructor), the arguments it hands
    its superclass or None, and whether it delegates to another constructor
    of the class (`this(...)`).
    """
    found: List[Dict] = []
    escaped = re.escape(class_name)
    header = re.search(rf"\bclass\s+{escaped}\b", code)
    primary_span = (0, 0)
    if header:
        primary = re.match(
            r"\s*(?:<[^>{(]*>)?\s*(?:(?:@[\w.]+(?:\([^)]*\))?|public|protected|private|internal)"
            r"\s+)*(?:constructor\s*)?\(", code[header.end():])
        body_open = code.find("{", header.end())
        class_body = ""
        if body_open >= 0:
            class_body = code[body_open:(_closing_bracket(code, body_open) or len(code)) + 1]
        if primary:
            opening = header.end() + primary.end() - 1
            close = _closing_bracket(code, opening)
            if close is not None:
                primary_span = (header.start(), close)
                parts = _split_parameters(code[opening + 1:close])
                rest = code[close + 1:body_open if body_open > close else len(code)]
                base = re.search(r":\s*(?:[\w$]+\.)*[A-Z][\w$]*\s*(?:<[^>]*>)?\s*\(", rest)
                found.append({
                    "parameters": [_parameter_name(part) for part in parts],
                    "properties": {_parameter_name(part) for part in parts
                                   if re.search(r"\b(?:val|var)\s", part)},
                    "body": class_body, "primary": True, "delegates": False,
                    "super": (_arguments_between(code, close + 1 + base.end() - 1)
                              if base else None),
                })
    for match in re.finditer(rf"(?<![\w$.])(?:{escaped}|constructor)\s*\(", code):
        if primary_span[0] <= match.start() <= primary_span[1]:
            continue
        line_start = code.rfind("\n", 0, match.start()) + 1
        if not _DECLARATION_PREFIX.match(code[line_start:match.start()]):
            continue
        close = _closing_bracket(code, match.end() - 1)
        if close is None:
            continue
        rest = code[close + 1:]
        kind, arguments = "", None
        delegation = re.match(r"\s*:\s*(this|super)\s*\(", rest)
        if delegation:
            kind = delegation.group(1)
            opening = close + delegation.end()
            arguments = _arguments_between(code, opening)
            after = (_closing_bracket(code, opening) or close) + 1
        else:
            throws = re.match(r"\s*(?:throws\s+[\w$.,\s]+?)?(?=\{)", rest)
            if not throws:
                continue
            after = close + 1 + throws.end()
        body_open = re.match(r"\s*\{", code[after:])
        body = ""
        if body_open:
            start = after + body_open.end() - 1
            body = code[start:(_closing_bracket(code, start) or len(code)) + 1]
        first = re.match(r"\{\s*(this|super)\s*\(", body)
        if first:
            kind = first.group(1)
            arguments = _arguments_between(body, first.end() - 1)
        found.append({
            "parameters": [_parameter_name(part)
                           for part in _split_parameters(code[match.end():close])],
            "properties": set(), "body": body, "primary": False,
            "delegates": kind == "this", "super": arguments if kind == "super" else None,
        })
    return found


def _member_parameter(constructor: Dict, member: str) -> Optional[int]:
    """The position of the constructor parameter the member is assigned from, or None."""
    parameters = constructor["parameters"]
    if member in constructor["properties"]:
        return parameters.index(member)
    escaped = re.escape(member)
    sources = re.findall(
        rf"(?<![\w$.])(?:this\s*\.\s*)?{escaped}\s*=(?!=)\s*([A-Za-z_][\w$]*)\s*(?=[;\n}}])",
        constructor["body"])
    if constructor["primary"]:
        sources += re.findall(
            rf"\b(?:val|var)\s+{escaped}\s*(?::[^=\n]+)?=\s*([A-Za-z_][\w$]*)\s*(?=[;\n}}])",
            constructor["body"])
    if len(set(sources)) == 1 and sources[0] in parameters:
        return parameters.index(sources[0])
    return None


def _member_bindings(code: str, class_name: str, member: str) -> Dict[int, int]:
    """{constructor arity: position of the parameter that becomes `member`}.

    Empty when a constructor does not assign the member from one of its
    parameters (a key the class builds itself, a field set later) or
    delegates to another: then the keys its constructions pass say nothing
    about the key it encrypts with.
    """
    bindings: Dict[int, int] = {}
    for constructor in _constructors(code, class_name):
        position = None if constructor["delegates"] else _member_parameter(constructor, member)
        arity = len(constructor["parameters"])
        if position is None or bindings.get(arity, position) != position:
            return {}
        bindings[arity] = position
    return bindings


def _subclass_bindings(code: str, name: str, base_bindings: Dict[int, int]) -> Dict[int, int]:
    """The base class's bindings carried through a direct subclass's constructors.

    What each subclass constructor hands the base at the bound position must
    be one of its own parameters; anything else (a constant, a key it
    builds) leaves the subclass unbound.
    """
    bindings: Dict[int, int] = {}
    for constructor in _constructors(code, name):
        arguments = constructor["super"]
        position = (base_bindings.get(len(arguments))
                    if arguments is not None and not constructor["delegates"] else None)
        handed = arguments[position].strip() if position is not None else ""
        if handed not in constructor["parameters"]:
            return {}
        arity = len(constructor["parameters"])
        index = constructor["parameters"].index(handed)
        if bindings.get(arity, index) != index:
            return {}
        bindings[arity] = index
    return bindings


def _construction_sites(repository: _RepositorySources, class_name: str,
                        member: str, class_code: str) -> Optional[List[tuple]]:
    """(path, line, argument passed for `member`, file text, offset) of each
    construction of the class or of a direct subclass, outside tests.

    None when a construction cannot be tied to the parameter the member is
    assigned from: no constructor of that arity binds it, or the call names
    its arguments.
    """
    files = repository.files()
    bindings = {class_name: _member_bindings(class_code, class_name, member)}
    if not bindings[class_name]:
        return None
    subclass = re.compile(
        rf"\bclass\s+(?P<name>[A-Z][\w$]*)\b[^{{;]*?"
        rf"(?:\bextends\s+(?:[\w$]+\.)*{re.escape(class_name)}\b|:\s*(?:[\w$]+\.)*"
        rf"{re.escape(class_name)}\s*\()")
    for text in files.values():
        if class_name in text:
            code = blank_comments_and_literals(text)
            for match in subclass.finditer(code):
                bindings[match.group("name")] = _subclass_bindings(
                    code, match.group("name"), bindings[class_name])
    sites = []
    for relative, text in files.items():
        if not any(name in text for name in bindings):
            continue
        code = blank_comments_and_literals(text)
        plain = _blank_comments(text)
        for name, binding in bindings.items():
            pattern = (rf"\bnew\s+(?:[\w$]+\s*\.\s*)*{re.escape(name)}\s*\("
                       if relative.endswith(".java")
                       else rf"(?<![\w.$@]){re.escape(name)}\s*\(")
            for match in re.finditer(pattern, code):
                # A Kotlin declaration (`class Name(`) or supertype call
                # (`: Name(`) is not a construction.
                line_start = code.rfind("\n", 0, match.start()) + 1
                if re.search(r"\b(?:class|fun|constructor)\s+$|:\s*$",
                             code[line_start:match.start()]):
                    continue
                arguments = _assertion_arguments(plain, match.end())
                position = binding.get(len(arguments))
                if position is None or any(
                        re.match(r"\s*[A-Za-z_][\w$]*\s*=(?!=)", argument) for argument in arguments):
                    return None
                sites.append((relative, code.count("\n", 0, match.start()) + 1,
                              arguments[position], plain, match.start()))
    return sites


def _cross_file_keystore_key(repository: _RepositorySources, text: str,
                             key: str, offset: int) -> Optional[Dict]:
    """Where a key the edited class only receives is read from AndroidKeyStore, if it always is.

    `key` is used at `offset` of the file `text`. Its head must be a member
    of the class that every constructor assigns from one of its parameters
    (no parameter or local of the function around `offset` binds that
    name). Every construction of the class or of a direct subclass that
    hands that parameter on must pass, for it, a key read from a keystore
    (getKey, getEntry) or generated there, no software key (SecretKeySpec,
    generateSecret, unwrap), in a file that itself builds a symmetric
    keystore key for encryption. One construction that does not leaves the
    key unresolved.

    Only the argument for the member's own parameter is read (review of
    batch4-iv, 2026-10-30): a class that receives a keystore wrapping key
    and encrypts with a key it builds from raw bytes (an envelope) encrypts
    with a software key, which takes a caller IV.
    """
    head = re.match(r"(?:this\.)?([A-Za-z_][\w$]*)$", key)
    code = blank_comments_and_literals(text)
    declared = re.search(r"\b(?:class|object)\s+([A-Z][\w$]*)", code)
    if not head or not declared:
        return None
    sources = _KeySources(text)
    function = sources.enclosing(offset)
    if function and (head.group(1) in function["parameters"]
                     or sources._bound_local(head.group(1), function, offset)):
        return None
    sites = _construction_sites(repository, declared.group(1), head.group(1), code)
    if not sites:
        return None
    key_from, generated = [], []
    for path, line, argument, plain, position in sites:
        value = (_local_initializer(plain, argument.strip(), position)
                 if re.fullmatch(r"[A-Za-z_][\w$]*", argument.strip()) else "")
        if _SOFTWARE_KEY.search(f"{argument} {value}"):
            return None
        builders = keystore_secret_key_builders(repository.files().get(path, ""))
        if not _KEYSTORE_KEY_READ.search(f"{argument} {value}") or not builders:
            return None
        key_from.append(f"{path}:{line} passes {argument.strip()}"
                        + (f" = {value.strip()[:120]}" if value else ""))
        generated.extend({"source_file_path": path, **builder} for builder in builders)
    unique = list({(item["source_file_path"], item["line"]): item for item in generated}.values())
    return {"key_from": key_from[:4], "key_generated": unique[:4]}


_KEY_PROVIDER_CONSEQUENCE = (
    "AndroidKeyStore enforces the key's properties on every Cipher.init: a key "
    "generated or imported without setRandomizedEncryptionRequired(false) "
    "requires randomized encryption, so init(ENCRYPT_MODE, key, <IV or nonce "
    "spec>) throws InvalidAlgorithmParameterException (caller-provided IV not "
    "permitted); only init(ENCRYPT_MODE, key) works, and cipher.getIV() then "
    "returns the IV the keystore chose. Adding setRandomizedEncryptionRequired"
    "(false) to the builder changes no key already generated."
)


def key_provider_constraints(error: Dict, paths: List[str],
                             patched: Optional[Dict[str, str]] = None) -> List[Dict]:
    """What the keystore enforces on the keys the edited files encrypt with.

    For each key an encrypt init of an edited production file uses, before
    or after the patch, the AndroidKeyStore generation or import of that key
    when the checkout settles it: in the same file (_KeySources), or where
    the class holding the key is constructed (_cross_file_keystore_key).
    Read from the repository's own key generation code, never per app.
    Cryptomator Lite run_v6 (2026-10-30): the behaviour review approved a
    SecureRandom IV for BaseCipher's encryption saying "no supplied source
    establishes a key configuration that rejects caller-supplied GCM
    parameters"; the key's KeyGenParameterSpec was in CryptoOperationsGCM,
    a file the patch did not edit.
    """
    from cognalys.extraction.iv_provenance import cipher_inits

    repo_root = Path(str(error.get("repository_path", "")))
    repository = _RepositorySources(repo_root)
    constraints: List[Dict] = []
    for relative in paths:
        if not relative.endswith(_SOURCE_EXTENSIONS) or is_test_source_path(relative):
            continue
        try:
            original = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        texts = [original] + ([patched[relative]] if patched and patched.get(relative) else [])
        seen = set()
        for text in texts:
            sources = _KeySources(text)
            for item in cipher_inits(text):
                key = _normalised_key(item["arguments"][1]) if item["mode"] == "encrypt" else ""
                if not key or key in seen:
                    continue
                seen.add(key)
                same_file = sources.keystore_key(item["arguments"][1], item["start"])
                link = None
                if same_file and keystore_secret_key_builders(text):
                    link = {"key_from": [f"{relative}: {same_file}"],
                            "key_generated": [{"source_file_path": relative, **builder}
                                              for builder in keystore_secret_key_builders(text)]}
                elif not same_file:
                    link = _cross_file_keystore_key(repository, text, key, item["start"])
                if not link:
                    continue
                required = [entry["randomized_encryption_required"] for entry in link["key_generated"]]
                constraints.append({
                    "source_file_path": relative,
                    "encrypt_init": f"line {item['line']}: {item['text']}",
                    "key": key,
                    "key_from": link["key_from"],
                    "key_generated": link["key_generated"],
                    "randomized_encryption_required": (
                        True if all(value is True for value in required)
                        else False if all(value is False for value in required) else None),
                    "consequence": _KEY_PROVIDER_CONSEQUENCE,
                })
    return constraints


def provider_iv_replaced(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A patch that has the code choose an IV the provider already chooses at random.

    Cryptomator Lite run_v6 (2026-10-30): the reported GCMParameterSpec only
    carries, for decryption, the IV BaseCipher's init(ENCRYPT_MODE, key) let
    the provider generate and kept through getIV() (iv_provenance
    provider_generated). The patch passed a SecureRandom IV to those inits: a
    random IV replaced by another, which repairs nothing, and for the
    AndroidKeyStore key it used every encryption would have thrown.
    Amethyst#4642 was closed by its maintainers for the same change.

    Judged only when iv_provenance establishes the provider's IV for this
    finding, and only on an encrypt init that had no parameters before the
    patch and has them after, on the same cipher and key.
    """
    from cognalys.extraction.iv_provenance import cipher_inits, iv_provenance_fact

    production = [edit for edit in edits if not _is_test_edit(edit)]
    repo_root = Path(str(error.get("repository_path", "")))
    overlay = _coordinated_overlay(repo_root, production)
    if not any(re.search(r"\bENCRYPT_MODE\b", text) for text in overlay.values()):
        return []
    fact = iv_provenance_fact(error)
    if fact.get("status") != "provider_generated":
        return []
    issues = []
    for relative, patched in overlay.items():
        try:
            original = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        provider = {(item["receiver"], _normalised_key(item["arguments"][1]))
                    for item in cipher_inits(original)
                    if item["mode"] == "encrypt" and item["provider_iv"]}
        replaced = [init for key, init, _ in _new_parameter_encryptions(original, patched)
                    if any(key == provider_key for _, provider_key in provider)]
        if replaced:
            issues.append({
                "code": "PROVIDER_IV_REPLACED",
                "path": relative,
                "details": [
                    str(fact.get("statement", "")) + " This patch has the code choose that IV "
                    "instead (" + " | ".join(replaced[:2]) + "): one random IV replaced by "
                    "another, which repairs nothing, and a key whose provider requires "
                    "randomized encryption refuses it. Leave the encryption as it is."
                ],
            })
    return issues


# `.putString(PrefsUtil.PIN_HASH, <value>)`: what a store writes under a key.
_STORE_WRITE = re.compile(
    r"\.\s*put(?:String|Int|Long|Boolean|Float|StringSet)?\s*\(\s*(?P<key>[A-Za-z_][\w.]*)\s*,\s*")
_SOURCE_EXTENSIONS = (".java", ".kt")
_SKIP_DIRECTORIES = {".git", "build", ".gradle", "node_modules", ".idea"}


def _store_writes(code: str) -> Dict[str, str]:
    """Key -> the function the written value is computed with, per write."""
    writes: Dict[str, str] = {}
    for match in _STORE_WRITE.finditer(code):
        value = code[match.end():match.end() + 200]
        # Only a call that starts the value expression computes that value.
        # Searching further would mistake putString(KEY, cached).commit()
        # for a value derived by commit() (bitbanana, 2026-10-20).
        call = re.match(r"\s*(?:[A-Za-z_]\w*\s*\.\s*)*([a-z]\w*)\s*\(", value)
        if call:
            depth = 1
            for end in range(call.end(), len(value)):
                depth += (value[end] == "(") - (value[end] == ")")
                if depth == 0:
                    break
            else:
                continue
            # prefs().getString(...) copies stored data; prefs() itself
            # does not derive its representation.
            if value[end + 1:].lstrip().startswith("."):
                continue
            writes.setdefault(match.group("key").rsplit(".", 1)[-1], call.group(1))
    return writes


def _stored_helper_returns(code: str, language: str) -> Dict[str, tuple]:
    """Return expressions of named helpers, without comments or literals."""
    code = blank_comments_and_literals(code)
    returns = {}
    for declaration in declarations(code, language):
        header = re.match(
            r"[\w$]+\s*\([^{};]*\)\s*(?:throws\s+[\w.,\s]+|:\s*[\w.<>,?\s]+)?\s*\{",
            code[declaration.name_start:])
        if header:
            opening = declaration.name_start + header.end() - 1
            expressions = tuple(re.sub(r"\s+", "", value) for value in
                                re.findall(r"\breturn\s+([^;{}\n]+)", _braced_body(code, opening)))
            returns[declaration.name] = expressions
    return returns


def _without_store_values(code: str) -> str:
    """Ignore derivations used directly to create values, rather than check them."""
    for match in reversed(list(_STORE_WRITE.finditer(code))):
        depth = 1
        for end in range(match.end(), len(code)):
            if code[end] == "(":
                depth += 1
            elif code[end] == ")":
                depth -= 1
                if depth == 0:
                    code = code[:match.end()] + " " * (end - match.end()) + code[end:]
                    break
    return code


def _enclosing_call_name(code: str, position: int) -> str:
    """Name of the call whose argument list holds `position`, or ""."""
    depth = 0
    for index in range(position - 1, -1, -1):
        character = code[index]
        if character == ")":
            depth += 1
        elif character == "(":
            if depth == 0:
                name = re.search(r"([A-Za-z_$][\w$]*)\s*$", code[:index])
                return name[1] if name else ""
            depth -= 1
        elif character in ";{}" and depth == 0:
            return ""
    return ""


def _stored_derivation_is_checked(code: str, function: str,
                                  patch_functions: frozenset = frozenset()) -> bool:
    """An assignment used only by a masked store write is creation, not a check.

    A use as an argument of a function the patch itself declares is not the
    old check either: the patch's own verifier or format test receives the
    new value. BitBanana's PasswordEntryActivity (frozen run v3, 2026-11-13)
    reads PASSWORD_HASH through the patch's verifyAppLockData and, to migrate
    a legacy value after a match, derives currentVerifier with the patched
    appLockDataHash, tests it with isLegacyAppLockVerifier and
    verifyAppLockData and stores it; that derivation was taken for the old
    equals() check and all three drafts were refused.
    """
    for call in re.finditer(rf"(?<![\w$]){re.escape(function)}\s*\(", code):
        start = max(code.rfind(";", 0, call.start()), code.rfind("{", 0, call.start())) + 1
        assignment = re.search(r"\b([A-Za-z_$][\w$]*)\s*=\s*(?:[\w$]+\s*\.\s*)*$", code[start:call.start()])
        if assignment:
            end = code.find(";", call.end())
            if end >= 0:
                uses = [use for use in re.finditer(
                    rf"(?<![\w$]){re.escape(assignment[1])}(?![\w$])", code) if use.start() > end]
                if all(_enclosing_call_name(code, use.start()) in patch_functions for use in uses):
                    continue
        return True
    return False


_EQUALITY_USE = r"(?:\.\s*(?:equals|contentEquals|equalsIgnoreCase)\s*\(\s*{0}(?![\w$])|(?<![\w$]){0}\s*\.\s*(?:equals|contentEquals|equalsIgnoreCase)\s*\(|[=!]=\s*{0}(?![\w$])|(?<![\w$]){0}\s*[=!]=(?!=))"


def _reads_go_through_patch_functions(code: str, key: str, patch_functions: frozenset) -> bool:
    """Every read of `key` goes only to functions the patch adds, never to equality.

    A read handed to the patch's own verifier is the new check; the old
    derivation left in the file then creates the new form (a migration after
    a match). BitBanana's PinEntryActivity (frozen run v3, 2026-11-13) reads
    PIN_HASH into normalVerifier, checks it with the patch's verifyAppLockData
    and, in migrateLegacyPin, derives and stores the new form. A read compared
    by equals or == anywhere, or not handed to such a function, keeps the file
    a reader to check.
    """
    if not patch_functions:
        return False
    reads = list(re.finditer(rf"\.\s*get\w*\s*\(\s*(?:[\w.]+\.)?{re.escape(key)}\b", code))
    if not reads:
        return False
    for read in reads:
        if _enclosing_call_name(code, read.start()) in patch_functions:
            continue
        start = max(code.rfind(";", 0, read.start()), code.rfind("{", 0, read.start()),
                    code.rfind("}", 0, read.start())) + 1
        assignment = re.search(r"\b([A-Za-z_$][\w$]*)\s*=(?!=)\s*[\w$().\s]*$",
                               code[start:read.start()])
        if not assignment:
            return False
        variable = re.escape(assignment[1])
        end = code.find(";", read.end())
        later = code[end + 1:] if end >= 0 else ""
        if re.search(_EQUALITY_USE.format(variable), later):
            return False
        uses = list(re.finditer(rf"(?<![\w$]){variable}(?![\w$])", later))
        if not uses or not all(_enclosing_call_name(later, use.start()) in patch_functions
                               for use in uses):
            return False
    return True


def stale_stored_value_readers(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Code that still checks a stored value the old way after the patch rewrote it.

    bitbanana (exp_final_20261019 subset_bitbanana): the patch made
    PinSetupActivity store PIN_HASH as a new prefixed verifier, while
    PinEntryActivity -- the unlock screen -- and PinSetupFragment still
    compared appLockDataHash(input) with PIN_HASH. It compiled, and any user
    who set or changed a PIN would have been locked out.

    Flagged: a key now written with a different function or a helper whose
    return expression changed, read in a production file that still checks
    it with the original single-input derivation.
    """
    repo_root = Path(str(error.get("repository_path", "")))
    production = [edit for edit in edits if not _is_test_edit(edit)]
    if not production or not repo_root.is_dir():
        return []
    before: Dict[str, str] = {}
    after: Dict[str, str] = {}
    for edit in production:
        for key, function in _store_writes(blank_comments_and_literals(
                str(edit.get("original_code", "")))).items():
            before.setdefault(key, function)
        for key, function in _store_writes(blank_comments_and_literals(
                str(edit.get("corrected_code", "")))).items():
            after.setdefault(key, function)
    changed = {key: before[key] for key in before
               if key in after and after[key] != before[key]}
    overlay = _coordinated_overlay(repo_root, production)
    changed_helpers = set()
    declared_before, declared_after = set(), set()
    for edit in production:
        language = language_of(str(edit.get("source_file_path", "")))
        for field, names in (("original_code", declared_before), ("corrected_code", declared_after)):
            names.update(declaration.name for declaration in declarations(
                blank_comments_and_literals(str(edit.get(field, ""))), language))
        old_returns = _stored_helper_returns(str(edit.get("original_code", "")), language)
        new_returns = _stored_helper_returns(str(edit.get("corrected_code", "")), language)
        changed_helpers.update(name for name, values in old_returns.items()
                               if values and name in new_returns and values != new_returns[name])
    stored_entries = (error.get("stored_value_contract") or {}).get("entries", [])
    known_readers = set()
    for entry in stored_entries:
        for reader in entry.get("readers", []):
            key = reader.get("key_expression", "").rsplit(".", 1)[-1]
            known_readers.add((reader.get("path"), key))
        for writer in entry.get("writers", []):
            function = writer.get("derivation", "").rsplit("#", 1)[-1]
            if function in changed_helpers | set(changed.values()):
                key = writer.get("key_expression", "").rsplit(".", 1)[-1]
                changed.setdefault(key, function)
    if not changed and not changed_helpers:
        return []
    # Functions the patch adds: its own verifier or format test.
    patch_functions = frozenset(declared_after - declared_before)
    sources = {}
    paths = []
    for directory, subdirectories, files in os.walk(repo_root):
        subdirectories[:] = [name for name in subdirectories
                             if name not in _SKIP_DIRECTORIES and not is_test_source(
                                 (Path(directory) / name).relative_to(repo_root).as_posix() + "/")]
        paths.extend(Path(directory) / name for name in files
                     if Path(name).suffix in _SOURCE_EXTENSIONS)
    for path in paths:
        if is_test_source(str(path.relative_to(repo_root)).replace("\\", "/")):
            continue
        relative = str(path.relative_to(repo_root)).replace("\\", "/")
        try:
            original = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sources[relative] = overlay.get(relative, original)
        # A helper can start returning a new format without being renamed.
        # Writers outside the patch then change behaviour too (bitbanana,
        # subset_bitbanana_readers, 2026-10-20).
        for key, function in _store_writes(blank_comments_and_literals(original)).items():
            if function in changed_helpers:
                changed.setdefault(key, function)
    stale = []
    stale_records = []
    for relative, content in sources.items():
        code = blank_comments_and_literals(content)
        checks = _without_store_values(code)
        for key, old_function in changed.items():
            if stored_entries and (relative, key) not in known_readers:
                continue
            reads = re.search(rf"\.\s*get\w*\s*\(\s*(?:[\w.]+\.)?{re.escape(key)}\b", code)
            uses_old = _stored_derivation_is_checked(checks, old_function, patch_functions)
            declares_old = re.search(rf"\b(?:fun|static|public|private)\b[^;{{]*\b{re.escape(old_function)}\s*\(",
                                     code)
            if reads and uses_old and not declares_old and not _reads_go_through_patch_functions(
                    code, key, patch_functions):
                stale.append(f"{relative} reads {key} and still checks it with {old_function}()")
                stale_records.append({"path": relative, "key": key, "derivation": old_function})
    if not stale:
        return []
    return [{
        "code": "STORED_VALUE_READERS_NOT_UPDATED",
        "path": stale[0].split(" ", 1)[0],
        "affected_paths": sorted({item["path"] for item in stale_records}),
        "stored_values": stale_records,
        "details": [
            "the patch changes how a stored value is written, but other code that reads "
            "the same value still checks it the old way, so a value written the new way "
            "stops matching there: " + " | ".join(stale[:4])
            + ". Update every reader to the new check (keeping the old one for values "
            "stored before), or keep writing the old form."
        ],
    }]


def reported_value_untouched(error: Dict, edits: List[Dict]) -> List[Dict]:
    """A patch that leaves the reported call and the reported value as they were.

    bitbanana's PBEKeySpec finding (exp_final_20261019) reported an
    iteration count of 5000 where the rule requires at least 10000; the pull
    request added spec.clearPassword() in a finally block. That is a real
    improvement of another requirement, but the call the detector reported
    still receives 5000, so the finding it was opened for is not fixed and
    the pull request said nothing about it.

    Only for findings that report a value (an algorithm, a size, a count, a
    protocol): an order finding can be fixed away from its reported line.
    Flagged when the reported line is unchanged in the patched file and no
    edited production file changes how often the reported value appears.

    In a unit with joint findings the patch may fix a joint finding and leave
    the reported one as it is: org.cry.otp's hmac_sha (c_arm_k3 r2) joined
    SecretKeySpecSpec into MacSpec, every draft changed the key label and kept
    the HmacSHA1 the plan forbade replacing, and six drafts died here as
    "correção não gerada". When the same check finds a joint finding's own
    reported value changed, nothing is blocked: the behaviour review judges
    the reported finding without effect and pr_attribution publishes the
    patch for the joint finding (r1 and r3 of the same unit ended that way).
    """
    issues = _reported_value_untouched_one(error, edits)
    if issues and any(
            _value_report(context) and not _reported_value_untouched_one(
                {**error, "detector_context": context,
                 "method_name": str(item.get("method", "")),
                 "joint_findings": []}, edits)
            for item in error.get("joint_findings") or [] if isinstance(item, dict)
            for context in [(error.get("joint_detector_contexts") or {}).get(
                str(item.get("unique_error_id", ""))) or {}]):
        return []
    return issues


def _value_report(context: Dict) -> bool:
    """Whether a detector record names a value at a parsable line (see below)."""
    location = str((context or {}).get("source_location", "")).split(",")[0].strip()
    name, _, number = location.rpartition(":")
    return bool(str((context or {}).get("value", "")).strip() and name and number.isdigit()
                and str(context.get("label", "violation")).strip() in ("", "violation"))


def _reported_value_untouched_one(error: Dict, edits: List[Dict]) -> List[Dict]:
    context = error.get("detector_context") or {}
    value = str(context.get("value", "")).strip()
    location = str(context.get("source_location", "")).split(",")[0].strip()
    name, _, number = location.rpartition(":")
    if not value or not name or not number.isdigit():
        return []
    # Only a value check names the defective value; for a not-observed report
    # (feeder's trust manager) the value is incidental -- the protocol "TLS".
    if str(context.get("label", "violation")).strip() not in ("", "violation"):
        return []
    repo_root = Path(str(error.get("repository_path", "")))
    production = [edit for edit in edits if not _is_test_edit(edit)]
    overlay = _coordinated_overlay(repo_root, production)
    # Callers moved off the reported method also fix it: org.cry.otp's
    # profiles stopped constructing MD5 and the class itself stayed.
    method = str(error.get("method_name", "")).strip()
    if method in ("<init>", "<clinit>"):
        method = str(error.get("csv_class", "")).split("$")[0].rsplit(".", 1)[-1]
    if method and re.fullmatch(r"[\w$]+", method):
        call = re.compile(rf"(?<![\w$]){re.escape(method)}\s*\(")
        before = after = 0
        for relative, patched in overlay.items():
            try:
                original = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []
            before += len(call.findall(blank_comments_and_literals(original)))
            after += len(call.findall(blank_comments_and_literals(patched)))
        if after < before:
            return []
    token = (re.compile(rf"(?<![\w.]){re.escape(value)}(?![\w.])")
             if re.fullmatch(r"[\d_]+", value) else re.compile(re.escape(f'"{value}"')))
    reported_file = ""
    for relative, patched in overlay.items():
        try:
            original = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        if len(token.findall(original)) != len(token.findall(patched)):
            return []
        if Path(relative).name == Path(name).name:
            reported_file = relative
            lines = original.splitlines()
            line_number = int(number)
            if not 0 < line_number <= len(lines):
                return []
            reported_line = re.sub(r"\s+", " ", lines[line_number - 1].strip())
            if not reported_line or reported_line not in {
                    re.sub(r"\s+", " ", line.strip()) for line in patched.splitlines()}:
                return []
    if not reported_file:
        return []
    # Verify-then-migrate keeps the reported call as the legacy path on
    # purpose and adds a compliant one beside it: a new call of the API the
    # reported line makes is that new path.
    callee = re.search(r"(?:new\s+)?([A-Za-z_$][\w$]*)\s*\(", reported_line)
    if callee:
        api = re.compile(rf"(?<![\w$]){re.escape(callee.group(1))}\s*\(")
        before = after = 0
        for relative, patched in overlay.items():
            try:
                original = (repo_root / relative).read_text(encoding="utf-8", errors="replace")
            except OSError:
                return []
            before += len(api.findall(blank_comments_and_literals(original)))
            after += len(api.findall(blank_comments_and_literals(patched)))
        if after > before:
            return []
    return [{
        "code": "REPORTED_VALUE_UNTOUCHED",
        "path": reported_file,
        "details": [
            f"the reported call at {location} is unchanged and still receives the "
            f"reported value {value}, so the finding is not fixed. Change the value "
            "or where it comes from, or decide that no safe fix exists and say "
            "why; an unrelated improvement to the same code is not a fix of this "
            "finding."
        ],
    }]


# Unchecked exceptions: catching one is never a compile error.
_UNCHECKED_EXCEPTIONS = frozenset({
    "RuntimeException", "IllegalArgumentException", "IllegalStateException",
    "NullPointerException", "NumberFormatException", "IndexOutOfBoundsException",
    "ArrayIndexOutOfBoundsException", "StringIndexOutOfBoundsException",
    "UnsupportedOperationException", "ClassCastException", "ArithmeticException",
    "SecurityException", "ConcurrentModificationException", "NoSuchElementException",
})
_CALL_NAME = re.compile(r"(?<![\w$])([A-Za-z_]\w*)\s*\(")


def _obsolete_catches(code: str, throws_by_method: Dict[str, set]) -> set:
    """Checked exceptions caught where nothing in the try block throws them.

    Only blocks whose every call is a method with known throws count; a
    block that calls anything else is left alone.
    """
    blanked = blank_comments_and_literals(code)
    obsolete: set = set()
    for match in re.finditer(r"\btry\s*\{", blanked):
        body = _braced_body(blanked, match.end() - 1)
        calls = {name for name in _CALL_NAME.findall(body)
                 if name not in {"if", "for", "while", "switch", "catch", "synchronized",
                                 "return", "new", "super", "this"}}
        if not calls or calls - set(throws_by_method):
            continue
        thrown = set().union(*(throws_by_method[name] for name in calls))
        rest = blanked[match.end() + len(body) + 1:]
        for clause in re.finditer(r"\s*catch\s*\(([^)]*)\)\s*\{", rest):
            if clause.start() != 0 and rest[:clause.start()].strip():
                break
            names = set(re.findall(r"\b([A-Za-z_]\w*Exception)\b", clause.group(1)))
            obsolete |= names - thrown - _UNCHECKED_EXCEPTIONS - {"Exception"}
            handler = _braced_body(rest, clause.end() - 1)
            rest = rest[clause.end() + len(handler) + 1:]
    return obsolete


def legacy_sinks_left_by_patch(error: Dict, edits: List[Dict]) -> List[str]:
    """Effective deprecated-protocol configuration the patched code still holds.

    Read with the client's own predicate (extraction.tls_client_evidence): for
    OkHttp a connection spec naming deprecated versions, for a platform socket
    a deprecated name at setEnabledProtocols or an applied SSLParameters --
    never the no-op assignment through defaultSSLParameters. The files the
    patch edits are read as it leaves them; the repository's other OkHttp
    configuration sites, which it does not touch, are still in place.
    """
    from cognalys.extraction.tls_client_evidence import legacy_protocol_sinks

    evidence = (error.get("case_migration_policy", {}) or {}).get(
        "established_tls_trust_evidence") or {}
    client = str((evidence.get("http_client") or {}).get("library", ""))
    production = [edit for edit in edits if isinstance(edit, dict) and not _is_test_edit(edit)]
    repository = Path(str(error.get("repository_path", "")))
    overlay = _coordinated_overlay(repository, production) if repository.is_dir() else {}
    texts = list(overlay.values()) + [
        str(edit.get("corrected_code", "")) for edit in production
        if str(edit.get("source_file_path") or edit.get("source_file") or "") not in overlay]
    sinks = [sink for text in texts for sink in legacy_protocol_sinks(text, client)]
    sinks.extend(f"{site} (not edited by the patch)"
                 for site in evidence.get("protocol_configuration_sites") or []
                 if site not in overlay)
    return list(dict.fromkeys(sinks))


def validate_coordinated_semantics(error: Dict, edits: List[Dict]) -> Dict:
    """Catch cross-file/API/lifecycle hazards before a patch can be applied."""
    errors, warnings = [], []
    rename_errors, accepted_renames = rename_issues(error, edits)
    authorized_renames = {old for old, _ in accepted_renames}
    errors.extend(rename_errors)
    errors.extend(redundant_caller_renames([
        edit for edit in edits
        if edit.get("edit_kind") != "test"
        and "test" not in str(edit.get("source_file_path", "")).lower()
    ]))
    for edit in edits:
        if executor_submit_discards_exceptions(
            str(edit.get("original_code", "")), str(edit.get("corrected_code", "")),
        ):
            errors.append({
                "code": "EXECUTOR_SUBMIT_DISCARDS_EXCEPTIONS",
                "path": str(edit.get("source_file_path", "")),
                "details": [
                    "work is submitted to an executor, nothing reads the Future "
                    "and nothing catches inside the task, so its exceptions are "
                    "silently lost; use execute(), catch and report inside the "
                    "task, or observe the Future"
                ],
            })
    for edit in edits:
        if (edit.get("edit_kind") == "test"
                or is_test_source(str(edit.get("source_file_path", "")))):
            continue
        sibling_source = ""
        try:
            sibling_source = (
                Path(str(error.get("repository_path", "")))
                / str(edit.get("source_file_path", ""))
            ).read_text(encoding="utf-8", errors="replace")
        except OSError:
            sibling_source = ""
        for contradiction in name_behavior_contradictions(
            str(edit.get("original_code", "")), str(edit.get("corrected_code", "")),
            (), sibling_source,
        ) + accept_all_name_contradictions(
            str(edit.get("original_code", "")), str(edit.get("corrected_code", "")),
        ) + variable_name_contradictions(
            str(edit.get("original_code", "")), str(edit.get("corrected_code", "")),
            _referenced_outside(error, str(edit.get("source_file_path", ""))),
        ):
            errors.append({
                "code": contradiction["code"],
                "path": str(edit.get("source_file_path", "")),
                "details": [contradiction["message"]],
                **{key: contradiction[key] for key in ("name", "removed_behavior", "subject")
                   if contradiction.get(key)},
            })
    method_pattern = re.compile(
        r"\b(public|protected)\s+((?:static\s+)?(?:final\s+)?)"
        r"([\w$.<>?\[\]]+)\s+([A-Za-z_]\w*)\s*\(([^)]*)\)",
        re.MULTILINE,
    )

    def public_contracts(code: str) -> set[tuple]:
        # Methods inside an anonymous implementation are excluded: they are
        # the instantiated interface's contract, not this file's. Deleting a
        # trust-all X509TrustManager -- the whole point of that repair --
        # read as three removed public methods and failed the contract check
        # (seafile getMediaSource, run_20260917_230558).
        inline_spans = anonymous_class_spans(code)
        contracts = set()
        for match in method_pattern.finditer(code):
            if any(start < match.start() < end for start, end in inline_spans):
                continue
            parameters = []
            for raw in match.group(5).split(","):
                raw = re.sub(r"@[\w.]+(?:\([^)]*\))?\s*", "", raw).strip()
                if not raw:
                    continue
                parts = raw.split()
                parameters.append(" ".join(parts[:-1]) if len(parts) > 1 else parts[0])
            contracts.add((match.group(1), "static" in match.group(2),
                           match.group(3), match.group(4), tuple(parameters)))
        return contracts

    test_code = "\n".join(
        str(item.get("corrected_code", "")) for item in edits
        if item.get("edit_kind") == "test"
        or is_test_source(str(item.get("source_file_path", "")))
    )
    production_code = "\n".join(
        str(item.get("corrected_code", "")) for item in edits
        if item.get("edit_kind") != "test"
        and not is_test_source(str(item.get("source_file_path", "")))
    )
    for issue in unrelated_test_placements(error, edits):
        errors.append(issue)

    for issue in tests_in_other_modules(error, edits):
        errors.append(issue)

    for issue in test_seam_visibility(error, edits):
        errors.append(issue)

    for issue in test_imports_off_the_classpath(error, edits):
        errors.append(issue)

    for issue in unresolvable_constructor_calls(error, edits):
        errors.append(issue)

    for issue in signature_change_issues(error, edits):
        errors.append(issue)

    for issue in import_issues(error, edits):
        errors.append(issue)

    for issue in identity_equality_assertions(error, edits):
        errors.append(issue)

    for issue in unresolved_repository_symbols(error, edits):
        errors.append(issue)

    for issue in private_member_calls_in_tests(error, edits):
        errors.append(issue)

    for issue in statements_without_effect(edits):
        errors.append(issue)

    for issue in discarded_governed_calls(error, edits):
        errors.append(issue)

    for issue in discarded_constructions(error, edits):
        errors.append(issue)

    for issue in wrapped_same_objects(error, edits):
        errors.append(issue)

    for issue in fallbacks_to_original_call(error, edits):
        errors.append(issue)

    for issue in same_values_through_unobserved_apis(error, edits):
        errors.append(issue)

    for issue in same_parts_app_carriers(error, edits):
        errors.append(issue)

    for issue in same_parts_app_keys(error, edits):
        errors.append(issue)

    # What the behaviour review let through in App Manager's drafts
    # (2026-11-01): a delegating alias, a key rebuilt from its own bytes, a
    # discarded governed read, a duplicated neighbour, a removed GCM spec.
    errors.extend(patch_effect_issues(error, edits))

    for issue in comment_only_changes(edits):
        errors.append(issue)

    for issue in caller_value_overrides(error, edits):
        errors.append(issue)

    for issue in neutralized_option_branches(error, edits):
        errors.append(issue)

    for issue in caller_iv_on_randomized_keystore_key(error, edits):
        errors.append(issue)

    for issue in provider_iv_replaced(error, edits):
        errors.append(issue)

    for issue in reported_value_untouched(error, edits):
        errors.append(issue)

    for issue in stale_stored_value_readers(error, edits):
        errors.append(issue)

    errors.extend(required_numeric_constants(error, edits))

    for issue in caller_value_rejections(error, edits):
        errors.append(issue)

    # Contracts outside the method: what existing tests pin, and a derived
    # value that is stored or sent (see contract_gates).
    for issue in rewritten_test_expectations(error, edits):
        errors.append(issue)

    for issue in changed_stored_derivations(error, edits):
        errors.append(issue)

    for issue in source_text_tests(edits):
        errors.append(issue)

    # A patch that fixes nothing, and one that breaks the other side of a
    # protocol (see contract_gates).
    for issue in kept_constant_key_material(error, edits):
        errors.append(issue)

    for issue in protocol_algorithm_changes(error, edits):
        errors.append(issue)

    for issue in value_still_configures_other_api(error, edits):
        errors.append(issue)

    for issue in pinned_negotiated_protocol(error, edits):
        errors.append(issue)

    for issue in removed_user_setting(error, edits):
        errors.append(issue)

    discriminator = (
        error.get("expanded_repository_context", {})
        .get("runtime_discriminator_assessment", {})
    )
    if discriminator.get("supported") is True:
        if re.search(r"\bMessageDigest\b|getInstance\s*\(\s*[\"'](?:MD5|SHA-?1)",
                     production_code, re.IGNORECASE):
            errors.append({
                "code": "RUNTIME_DISCRIMINATOR_STILL_USES_DIGEST",
                "details": ["replace the digest-derived discriminator with explicit variant/capability logic"],
            })
        if not test_code.strip():
            warnings.append({
                "code": "RUNTIME_DISCRIMINATOR_BEHAVIOR_NOT_TESTED",
                "details": ["a concrete test edit must preserve every known variant mapping and the fallback"],
            })
        known_ids = (
            error.get("expanded_repository_context", {})
            .get("android_build_variants", {})
            .get("known_application_ids", [])
        )
        missing_ids = [value for value in known_ids if value not in test_code]
        if known_ids and missing_ids and test_code.strip():
            warnings.append({
                "code": "KNOWN_APPLICATION_VARIANTS_NOT_TESTED",
                "details": missing_ids,
            })
        known_id_set = set(known_ids)
        unverified_literals = set()
        application_id_literal = re.compile(
            r"[\"']([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+){2,})[\"']"
        )
        for edit in edits:
            if (edit.get("edit_kind") == "test"
                    or is_test_source(str(edit.get("source_file_path", "")))):
                continue
            before_literals = set(application_id_literal.findall(
                str(edit.get("original_code", ""))
            ))
            after_literals = set(application_id_literal.findall(
                str(edit.get("corrected_code", ""))
            ))
            unverified_literals.update(
                value for value in after_literals - before_literals
                if value not in known_id_set
            )
        if unverified_literals:
            errors.append({
                "code": "UNVERIFIED_BEHAVIORAL_MAPPING_LITERAL",
                "details": sorted(unverified_literals),
            })
        unresolved_constants = discriminator.get(
            "unmapped_accepted_constants", []
        )
        if unresolved_constants:
            coverage = discriminator.get("mapping_coverage", {})
            warnings.append({
                "code": (
                    "LEGACY_DISCRIMINATOR_MAPPING_REQUIRES_REVIEW"
                    if coverage.get("current_variants") != "complete"
                    else "CURRENT_VARIANTS_COVERED_LEGACY_MAPPING_UNRESOLVED"
                ),
                "details": unresolved_constants,
                "mapping_coverage": coverage,
            })
    # Activated by the policy -- filters_deprecated_protocols, which the
    # specification's remediation entry or an evidence-selected variant
    # declares -- not by the wording of a required test (2026-10-17 audit).
    tls_policy = error.get("case_migration_policy", {}) or {}
    if (tls_policy.get("filters_deprecated_protocols")
            or tls_policy.get("consumer_scope")
            == "signature_preserving_internal_transport_change"):
        if re.search(
                r"SSLContext\.getInstance\s*\(\s*[\"']TLSv1\.[23][\"']\s*\)",
                production_code):
            errors.append({
                "code": "TLS_CONTEXT_VERSION_PINNED",
                "details": [
                    "retain generic provider negotiation and filter effective protocols"
                ],
            })
        # An OkHttp client moved from COMPATIBLE_TLS to its MODERN_TLS spec
        # drops the deprecated versions where they were configured.
        modern_spec = ("ConnectionSpec.MODERN_TLS" in production_code
                       and not re.search(r"COMPATIBLE_TLS|TlsVersion\.(?:TLS_1_0|TLS_1_1|SSL_3_0)",
                                         production_code))
        if not modern_spec and not ("enabledProtocols" in production_code
                and re.search(r"TLSv1(?:[\"']|\.1)", production_code)
                and (
                    "filter" in production_code
                    or "remove" in production_code
                    or _LEGACY_TLS_EXCLUSION_PATTERN.search(production_code)
                )):
            # A missing socket filter is separate hardening, not a defect of
            # a patch that fixes what was reported (Fread, tls_20261015: the
            # selector-only fix on OkHttp was refused here). It stays an
            # error where the filter is the whole remedy (the reported "TLS"
            # selector is kept), or where the patched code still turns a
            # deprecated protocol on through an effective sink.
            kept_sinks = legacy_sinks_left_by_patch(error, edits)
            if kept_sinks or (tls_policy.get("consumer_scope")
                              == "signature_preserving_internal_transport_change"):
                errors.append({
                    "code": "TLS_LEGACY_PROTOCOL_FILTER_MISSING",
                    "details": [
                        "filter the provider-enabled protocol set at the socket/transport boundary",
                        *[f"still enables a deprecated protocol: {sink[:200]}"
                          for sink in kept_sinks[:3]],
                    ],
                })
            else:
                warnings.append({
                    "code": "TLS_LEGACY_PROTOCOL_FILTER_MISSING",
                    "details": ["no socket-level protocol filter was added"],
                    "publication_consideration": (
                        "The client can still negotiate deprecated protocols; a "
                        "socket-level filter is separate hardening."),
                })
    # The test contract of a protocol filter applies to a patch that adds one
    # (code reading or setting a socket's enabled protocols); a selector-only
    # patch has no filter to test (Fread and crazyflie were flagged without one).
    if ((tls_policy.get("filters_deprecated_protocols")
         or tls_policy.get("consumer_scope")
         == "signature_preserving_internal_transport_change")
            and added_protocol_filter_code(edits) > 0):
        tls_protocol_literals = {
            value.lower() for value in re.findall(
                r"[\"'](TLSv\d+(?:\.\d+)*)[\"']", test_code, re.IGNORECASE
            )
        }
        known_tls_protocols = {
            "tlsv1", "tlsv1.0", "tlsv1.1", "tlsv1.2", "tlsv1.3",
        }
        has_future_protocol_assertion = (
            bool(tls_protocol_literals - known_tls_protocols)
            and bool(re.search(r"\b(?:assert\w*|check|expect\w*)\b", test_code))
        )
        # JSSE names: "TLSv1" is TLS 1.0 ("TLSv1.0" is not a JSSE name, and
        # either spelling satisfies the one requirement), and SSLv3 is the
        # other deprecated name a filter must remove.
        tls_test_requirements = {
            "SSLv3": r"SSLv3",
            "TLSv1": r"TLSv1(?:\.0)?[\"']",
            "TLSv1.1": r"TLSv1\.1",
            "TLSv1.2": r"TLSv1\.2",
            "TLSv1.3": r"TLSv1\.3",
            "empty result": (
                r"empty|none|no\s*acceptable|fails?\s*closed|"
                r"expected\s*=\s*IOException|assertFails|assertThrows"
            ),
        }
        missing = [
            label for label, pattern in tls_test_requirements.items()
            if not re.search(pattern, test_code, re.IGNORECASE)
        ]
        if not has_future_protocol_assertion:
            missing.append("future protocol")
        if missing:
            # A repository with no test infrastructure at all cannot be
            # asked to satisfy this contract by extending an existing test
            # -- authoring one from scratch would also require guessing at
            # build wiring this validator has no evidence for. Downgrade to
            # a warning there; process_error's own edit_issues check is what
            # actually decides whether that then falls back to review-only
            # publication (only for a review_only decision, never one that
            # auto-applies) or still blocks generation.
            # Tests are optional (2026-10-15): a missing test is a reviewer
            # note, never the reason a correct fix is dropped.
            target = warnings
            target.append({
                "code": "TLS_PROTOCOL_FILTER_CONTRACT_NOT_TESTED",
                "details": missing,
            })
    # The default-validation policy (Read You, 2026-10-18) is met by changing
    # the accept-all selector's default; a default still true keeps every
    # connection unvalidated whatever else the patch does.
    if (tls_policy.get("established_tls_trust_evidence") or {}).get(
            "trust_all_default_with_validating_branch"):
        still_default = re.findall(
            r"\b(\w*(?:trust\w*all|insecure|unsafe)\w*)\s*:\s*Boolean\s*=\s*true\b",
            production_code, re.IGNORECASE)
        if still_default:
            errors.append({
                "code": "ACCEPT_ALL_STILL_DEFAULT",
                "details": [f"`{name}` still defaults to true; make validation the default"
                            for name in sorted(set(still_default))],
            })
    # openHAB #4039 (2026-10-18): the maintainer showed OkHttp's default
    # connection specs already restrict every socket to TLS 1.2/1.3, so the
    # socket-factory wrapper the patch added changed nothing.
    # Only a filter the patch adds: code that already touched the protocols
    # is the maintainer's and may stay.
    protocol_filter = re.compile(
        r"\benabledProtocols\b|setEnabledProtocols\s*\(|:\s*(?:javax\.net\.ssl\.)?"
        r"SSLSocketFactory\s*\(\s*\)|extends\s+(?:javax\.net\.ssl\.)?SSLSocketFactory\b")
    original_production = "\n".join(
        str(item.get("original_code", "")) for item in edits
        if item.get("edit_kind") != "test"
        and not is_test_source(str(item.get("source_file_path", "")))
    )
    if (tls_policy.get("client_enforces_protocols")
            and len(protocol_filter.findall(production_code))
            > len(protocol_filter.findall(original_production))):
        errors.append({
            "code": "PROTOCOL_FILTER_REDUNDANT_FOR_CLIENT",
            "details": ["the client's own connection specs already exclude deprecated "
                        "protocols; propose no protocol filter"],
        })
    # whoBIRD (2026-10-18): values computed from the downloaded files are the
    # whole point of the change; a patch that retypes, shortens or leaves one
    # out would reject every model file it checks.
    replacement_values = [
        item for item in tls_policy.get("replacement_values") or [] if isinstance(item, dict)]
    missing_values = [
        str(item.get("constant", "")) for item in replacement_values
        if str(item.get("new_value", "")).lower() not in production_code.lower()]
    kept_old_values = [
        str(item.get("constant", "")) for item in replacement_values
        if str(item.get("old_value", "")) and str(item.get("old_value", "")).lower()
        in production_code.lower()]
    if missing_values or kept_old_values:
        errors.append({
            "code": "REPLACEMENT_VALUES_NOT_APPLIED",
            "details": ([f"{name}: the given SHA-256 value is missing" for name in missing_values]
                        + [f"{name}: the old value is still present" for name in kept_old_values]),
        })
    # Exact exception-message assertions are a cross-file API contract too.
    # Catch the common case where independently generated production and test
    # drafts use different wording; assembleDebug may not compile/run androidTest.
    thrown_messages = set(re.findall(
        r"(?:throw\s+(?:new\s+)?[A-Za-z_$][\w$.]*\s*\(|"
        r"(?:Exception|Error)\s*\()\s*[\"']([^\"']+)[\"']",
        production_code,
    ))
    asserted_messages = set(re.findall(
        r"assert(?:Equals|That)\s*\(\s*[\"']([^\"']+)[\"']\s*,\s*"
        r"[A-Za-z_$][\w$]*\.getMessage\s*\(\s*\)",
        test_code,
    ))
    mismatched_messages = sorted(asserted_messages - thrown_messages)
    if thrown_messages and mismatched_messages:
        errors.append({
            "code": "EXCEPTION_MESSAGE_CONTRACT_MISMATCH",
            "details": mismatched_messages,
            "production_messages": sorted(thrown_messages),
        })
    for edit in edits:
        before = public_contracts(str(edit.get("original_code", "")))
        after = public_contracts(str(edit.get("corrected_code", "")))
        # A rename the rename_safety assessment allowed, and rename_issues
        # verified reference by reference, is the one contract change allowed.
        removed = {
            contract for contract in before - after
            if contract[3] not in authorized_renames
        }
        if removed:
            errors.append({
                "code": "PUBLIC_API_CONTRACT_CHANGED",
                "path": str(edit.get("source_file_path", "")),
                "symbol": str(edit.get("symbol", "")),
                "details": [str(value) for value in sorted(removed)],
            })
        path_text = str(edit.get("source_file_path", ""))
        is_test_path = is_test_source(path_text)
        if path_text.lower().endswith((".kt", ".kts")) and not is_test_path:
            original = str(edit.get("original_code", ""))
            corrected = str(edit.get("corrected_code", ""))
            # An accepted rename keeps the function's own visibility: its new
            # name is not a new public API (NotallyX's md5Hash family).
            added_implicit_public = (
                implicitly_public_kotlin_functions(corrected)
                - implicitly_public_kotlin_functions(original)
                - {new for _, new in accepted_renames}
            )
            if added_implicit_public and not error.get(
                    "case_migration_policy", {}).get("allow_public_api_expansion"):
                errors.append({
                    "code": "UNNECESSARY_PUBLIC_API_EXPANSION",
                    "path": path_text,
                    "details": sorted(added_implicit_public),
                    # What a retry needs to clear it, and what it may keep.
                    "message": (
                        "these new Kotlin functions have no visibility modifier, so "
                        "they are public: " + ", ".join(sorted(added_implicit_public))
                        + ". Declare each private, or internal when another file of "
                        "this module calls it, and keep its name and parameters so "
                        "its callers still compile; a member of a private or internal "
                        "class, object or interface needs no modifier of its own."),
                })

    for edit in edits:
        if str(edit.get("edit_kind", "")) != "declaration":
            continue
        original = str(edit.get("original_code", ""))
        corrected = str(edit.get("corrected_code", ""))
        declaring_file = str(edit.get("source_file_path", ""))
        # No file content is available at this stage, so the anchor's
        # indentation is used as a proxy for "sits inside a class/object
        # body" -- reliable for consistently formatted Kotlin/Java sources.
        mismatches = find_declaration_scope_mismatches(
            original, corrected, indentation_of(original) > 0,
            other_edits=edits, declaring_file=declaring_file,
            anchor_owner_is_object=_anchor_owner_is_object(
                str(error.get("repository_path", "")), declaring_file, original),
        )
        if mismatches:
            errors.append({
                "code": "DECLARATION_SCOPE_MISMATCH",
                "path": declaring_file,
                "symbol": str(edit.get("symbol", "")),
                "details": mismatches,
            })

    primary_class = str(error.get("csv_class", "")).rsplit(".", 1)[-1]
    owner_retained = bool(primary_class and re.search(
        rf"\b(?:private|protected|public)\s+(?:final\s+)?"
        rf"{re.escape(primary_class)}\s+\w+\s*;", production_code
    ))
    explicit_dispose_contract_test = bool(re.search(
        r"\b(?:test\w*dispose|dispose\w*test|afterDispose|postDispose)\b",
        test_code, re.IGNORECASE,
    ))
    if owner_retained and not explicit_dispose_contract_test:
        warnings.append({
            "code": "OWNER_LIFECYCLE_NOT_TESTED",
            "details": [f"new retained {primary_class} reference requires a dispose test"],
        })

    if "setAccessible(true)" in test_code or ".getDeclaredMethod(" in test_code:
        errors.append({
            "code": "REFLECTION_ONLY_TEST_SEAM",
            "details": ["use a package-private or dedicated test-visible seam"],
        })

    policy_text = json.dumps(
        error.get("case_migration_policy", {}), ensure_ascii=False
    ).lower()
    if ("gcm" in policy_text or "authenticated" in policy_text) and test_code:
        if ("InvocationTargetException" in test_code
                and "AEADBadTagException" not in test_code):
            errors.append({
                "code": "AUTH_FAILURE_CAUSE_NOT_ASSERTED",
                "details": ["tamper tests must assert the authentication failure cause"],
            })

    # Cross-check checked-exception contracts among methods changed in this
    # atomic patch. The compiler remains authoritative, but this catches the
    # common staged-generation failure where one file changes throws and a
    # later file keeps an obsolete catch or forgets propagation.
    declaration_pattern = re.compile(
        r"\b(?:public|protected|private)?\s*(?:static\s+)?(?:final\s+)?"
        r"[\w$.<>?\[\]]+\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*"
        r"(?:throws\s+([^\{]+))?\{", re.MULTILINE,
    )
    # Java/Kotlin control-flow keywords that can never be a real method or
    # constructor name. declaration_pattern's "type" token accepts "." (to
    # match generics/qualified return types), so a trailing "..." in a
    # preceding comment -- e.g. "// publishing the progress....\nif (x) {"
    # -- satisfies it and makes the regex capture "if" as a declared method
    # with an empty throws set. That single bogus entry in throws_by_method
    # is enough to make `called` non-empty for almost any real method (any
    # "if (...) {" in its own body now matches a "tracked" name), bypassing
    # the "nothing to cross-check" skip below and flagging every checked
    # exception the method legitimately catches as obsolete.
    _RESERVED_KEYWORDS = {
        "if", "for", "while", "switch", "catch", "try", "synchronized",
        "when", "do", "else", "finally", "return",
    }

    def _constructor_pattern(source_file_path: str) -> Optional[re.Pattern]:
        # A constructor declares no return type ("public Foo() throws X {"),
        # so declaration_pattern -- which requires one -- can never match it
        # either. That silently dropped every constructor's throws clause
        # from throws_by_method, so any coordinated edit touching a caller
        # of a constructor with checked exceptions could never see them as
        # still required. Anchoring on the file's class name (Java's, and
        # Kotlin's conventional, file-name-equals-public-class-name rule)
        # recovers just the constructor case without the false-match risk
        # of simply making the return-type token optional.
        class_name = Path(source_file_path).stem.split("$")[0]
        if not re.match(r"^[A-Za-z_]\w*$", class_name):
            return None
        return re.compile(
            rf"\b(?:public|protected|private)?\s*{re.escape(class_name)}"
            r"\s*\([^)]*\)\s*(?:throws\s+([^\{]+))?\{"
        )

    def _declared_and_throws(edit: Dict) -> tuple:
        corrected = str(edit.get("corrected_code", ""))
        declared = {
            match.group(1) for match in declaration_pattern.finditer(corrected)
            if match.group(1) not in _RESERVED_KEYWORDS
        }
        throws: Dict[str, set] = {}
        for match in declaration_pattern.finditer(corrected):
            if match.group(1) in _RESERVED_KEYWORDS:
                continue
            throws.setdefault(match.group(1), set()).update({
                value.strip().rsplit(".", 1)[-1]
                for value in (match.group(2) or "").split(",") if value.strip()
            })
        constructor_pattern = _constructor_pattern(
            str(edit.get("source_file_path", ""))
        )
        if constructor_pattern is not None:
            class_name = Path(str(edit.get("source_file_path", ""))).stem
            for match in constructor_pattern.finditer(corrected):
                declared.add(class_name)
                throws.setdefault(class_name, set()).update({
                    value.strip().rsplit(".", 1)[-1]
                    for value in (match.group(1) or "").split(",") if value.strip()
                })
        return declared, throws

    throws_by_method = {}
    for edit in edits:
        _, edit_throws = _declared_and_throws(edit)
        for name, exceptions in edit_throws.items():
            throws_by_method.setdefault(name, set()).update(exceptions)
    for edit in edits:
        corrected = str(edit.get("corrected_code", ""))
        declared_here, declared_here_throws = _declared_and_throws(edit)
        called = {
            name for name in re.findall(r"\b([A-Za-z_]\w*)\s*\(", corrected)
            if name in throws_by_method and name not in declared_here
            and name not in _RESERVED_KEYWORDS
        }
        if not called:
            continue
        available = set().union(*(throws_by_method[name] for name in called))
        mentioned = set(re.findall(r"\b([A-Za-z_]\w*Exception)\b", corrected))
        # `catch (Exception e)` -- or a declared method's own
        # `throws Exception` -- structurally handles every checked
        # exception, but names none of them, so the lexical scan above
        # never sees it: DNSFilterService.stop already caught Exception
        # around its close() calls, the generated edit correctly kept that
        # catch around the new closeResponseOutput() call, and this check
        # reported IOException as unhandled anyway (run_20260922_134405).
        # Exception/Throwable is a universal handler; nothing can be missing
        # once one covers this fragment.
        declared_throws_here = (
            set().union(*declared_here_throws.values())
            if declared_here_throws else set()
        )
        catches_everything = bool(re.search(
            r"\bcatch\s*\(\s*(?:final\s+)?(?:Exception|Throwable)\b", corrected,
        )) or bool({"Exception", "Throwable"} & declared_throws_here)
        missing = set() if catches_everything else {
            exc for name in called for exc in throws_by_method[name]
            if (exc.endswith("Exception") and exc != "Exception"
                and exc not in mentioned)
        }
        missing = {
            exc for exc in missing
            if not enclosing_method_handles(
                Path(str(error.get("repository_path", ""))), edit, exc)
        }
        if missing:
            errors.append({
                "code": "CHECKED_EXCEPTION_NOT_PROPAGATED",
                "path": str(edit.get("source_file_path", "")),
                "details": sorted(missing),
            })
        # Judged per try block, and only where every call in the block is a
        # method this patch declares: a catch around anything else may be
        # handling what that call throws. bitbanana's PinSetupActivity
        # (exp_final_20261019 subset_policy2) catches GeneralSecurityException
        # and IOException around PrefsUtil.getEncryptedPrefs(), which declares
        # both; the patch did not touch that file, and every draft was refused
        # because the exceptions were not among those the patch's own methods
        # throw.
        obsolete = _obsolete_catches(corrected, throws_by_method)
        is_test_edit = (edit.get("edit_kind") == "test"
                        or is_test_source(str(edit.get("source_file_path", ""))))
        if obsolete and "Exception" not in available and not is_test_edit:
            errors.append({
                "code": "OBSOLETE_CHECKED_EXCEPTION_CATCH",
                "path": str(edit.get("source_file_path", "")),
                "details": sorted(obsolete),
            })

    # Replacing a disposable wrapper must not silently abandon the previous
    # sensitive instance on repeated assignment.
    for edit in edits:
        original = str(edit.get("original_code", ""))
        corrected = str(edit.get("corrected_code", ""))
        field_match = re.search(
            r"\b(?:SealedObject|\w*(?:Sealed|Protected|Encrypted)Value)\s+"
            r"([A-Za-z_]\w*)\s*;", original
        )
        if not field_match:
            continue
        field = field_match.group(1)
        assignments = re.findall(rf"\b{re.escape(field)}\s*=", production_code)
        if assignments and not re.search(
                rf"\b{re.escape(field)}\s*\.\s*(?:dispose|clear|close)\s*\(",
                production_code):
            errors.append({
                "code": "REPLACED_SENSITIVE_WRAPPER_NOT_DISPOSED",
                "path": str(edit.get("source_file_path", "")),
                "details": [field],
            })

    platform = (
        error.get("expanded_repository_context", {})
        .get("platform_context", {})
    )
    if (error.get("case_migration_policy", {}).get("platform_constraints", {})
            .get("must_support_api_21") is True):
        warnings.append({
            "code": "API_21_RUNTIME_VALIDATION_REQUIRED",
            "details": [f"detected minSdk={platform.get('android_min_sdk')}"],
        })
    # A patch that works but replaces behaviour the maintainer may have
    # chosen on purpose is the maintainer's call, not a reason to withhold it
    # (2026-10-22): kept as a warning for the operator. Gates where the patch
    # itself would break something (a protocol-mandated algorithm, a stored
    # derived value) stay errors.
    by_design = [issue for issue in errors if issue.get("code") in MAINTAINER_DECISION_CODES]
    errors = [issue for issue in errors if issue.get("code") not in MAINTAINER_DECISION_CODES]
    warnings.extend({**issue, "maintainer_decides": True} for issue in by_design)
    result = {"status": "invalid" if errors else "valid",
              "errors": errors, "warnings": warnings}
    if accepted_renames:
        # Travels with the solution: the detector replay takes these renames
        # as confirmed (method_renamed) instead of re-guessing them lexically.
        result["accepted_renames"] = [
            {"old": old, "new": new} for old, new in sorted(accepted_renames)]
    return result


# Gates that refuse a working patch only because the behaviour it replaces may
# be intended: a user option, a user setting, a value the caller chose.
MAINTAINER_DECISION_CODES = frozenset({
    "USER_OPTION_NEUTRALIZED", "USER_SETTING_REMOVED",
    "CALLER_SUPPLIED_VALUE_OVERRIDDEN", "REJECTS_CALLER_SUPPLIED_VALUE",
})


_SEMANTIC_VALIDATION_TEST_CODES = {
    "EQUALITY_ASSERTED_ON_IDENTITY_TYPE",
    "TEST_IMPORT_NOT_ON_TEST_CLASSPATH",
    "TEST_PLACED_IN_UNRELATED_FILE",
    "TEST_IN_OTHER_MODULE",
    "TLS_PROTOCOL_FILTER_CONTRACT_NOT_TESTED",
    "RUNTIME_DISCRIMINATOR_BEHAVIOR_NOT_TESTED",
    "KNOWN_APPLICATION_VARIANTS_NOT_TESTED",
}


_SEMANTIC_VALIDATION_PRODUCTION_CODES = {
    "CONSTRUCTOR_NOT_DECLARED_IN_REPOSITORY",
    "SYMBOL_NOT_DECLARED_IN_REPOSITORY",
    "CALLER_IV_REFUSED_BY_KEYSTORE_KEY",
    "PROVIDER_IV_REPLACED",
    "RUNTIME_DISCRIMINATOR_STILL_USES_DIGEST",
    "TLS_CONTEXT_VERSION_PINNED",
    "ACCEPT_ALL_STILL_DEFAULT",
    "PROTOCOL_FILTER_REDUNDANT_FOR_CLIENT",
    "REPLACEMENT_VALUES_NOT_APPLIED",
    "TLS_LEGACY_PROTOCOL_FILTER_MISSING",
    "UNVERIFIED_BEHAVIORAL_MAPPING_LITERAL",
    "LEGACY_DISCRIMINATOR_MAPPING_REQUIRES_REVIEW",
    "CURRENT_VARIANTS_COVERED_LEGACY_MAPPING_UNRESOLVED",
    "NAME_CONTRADICTS_BEHAVIOR",
    "EXECUTOR_SUBMIT_DISCARDS_EXCEPTIONS",
    "REDUNDANT_CALLER_RENAME",
    "UNAUTHORIZED_RENAME",
    "RENAME_TARGET_ALREADY_DECLARED",
    "RENAME_REFERENCES_NOT_UPDATED",
    "RENAME_PARTIAL_FAMILY",
    "RENAME_CALL_REBINDS",
    "TEST_SEAM_VISIBILITY_WIDENED",
    "DISCARDED_CONSTRUCTION",
    "WRAPPED_SAME_OBJECT",
    "FALLBACK_TO_ORIGINAL",
    "SAME_VALUE_UNOBSERVED_API",
    "SAME_PARTS_APP_CARRIER",
    "SAME_PARTS_APP_KEY",
    "DELEGATING_ALIAS_CALL",
    "SAME_OBJECT_REBUILT",
    "EMPTY_STORE_MANAGERS",
}
# The failures of rename_issues: each goes back to the generator with the
# rename rule, never to a hold (rename_retry_guidance).
_RENAME_CODES = frozenset({
    "UNAUTHORIZED_RENAME", "RENAME_TARGET_ALREADY_DECLARED",
    "RENAME_REFERENCES_NOT_UPDATED", "RENAME_PARTIAL_FAMILY", "RENAME_CALL_REBINDS",
})
# What takes the rename route: those, and a kept or new name that still
# states the removed behaviour, which is the same question. BWT's decryptDES
# and encryptChaCha20Poly1305 (2026-10-24) failed on NAME_CONTRADICTS_BEHAVIOR
# alone at every attempt, and that last failure dropped the draft without
# the attempt on the non-rename route a rename failure gets.
_RENAME_ROUTE_CODES = _RENAME_CODES | {"NAME_CONTRADICTS_BEHAVIOR"}
# Names the rename rule does not govern: a local variable or constant, whose
# every reference is in the edited code, and an accept-all selector, whose
# default changes instead (variable_name_contradictions and
# accept_all_name_contradictions mark their errors with these subjects).
_NAMES_OUTSIDE_THE_RENAME_RULE = frozenset({"variable", "selector"})


# Every method Java gives a class for free. A call to one of these resolves
# against any receiver whatever the repository declares.
_UNIVERSAL_METHODS = frozenset({
    "equals", "hashCode", "toString", "getClass", "wait", "notify",
    "notifyAll", "clone", "finalize",
})
# The compiler synthesises these on every enum; no source declares them.
_ENUM_SYNTHETIC_METHODS = frozenset({"values", "valueOf", "ordinal", "name"})
_INSTANCE_CALL = re.compile(
    r"\b(?P<receiver>[a-z][A-Za-z0-9_$]*)\s*\.\s*(?P<method>[a-z][A-Za-z0-9_$]*)\s*\("
)
_STATIC_CALL = re.compile(
    r"\b(?P<receiver>[A-Z][A-Za-z0-9_$]*(?:\.[A-Z][A-Za-z0-9_$]*)*)"
    r"\s*\.\s*(?P<method>[a-z][A-Za-z0-9_$]*)\s*\("
)
# The outline keeps the throws clause, so anchoring at end-of-line silently
# dropped every method that declares one -- Schedule.loadSchedule among them.
# A dropped declaration is worse than a missed check: the gate would report a
# real method as undeclared and block a correct patch.
_OUTLINE_SIGNATURE = re.compile(
    r"(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^()]*)\)"
    r"(?:\s*throws\s[\w$., ]+)?\s*$"
)
_TRUNCATION_MARKER = "... (API outline truncated)"


def declared_methods(source: str) -> Optional[Dict[str, set]]:
    """Method names a source declares, mapped to the arities they accept.

    Built from declared_api_surface(), which already separates a real
    signature from an ordinary call that merely looks like one -- the
    distinction this check depends on and the one that is easy to get wrong.
    Returns None when that outline was truncated, because a method missing
    from a partial outline is not evidence that the type lacks it.
    """
    from cognalys.llm.prompt_builders import declared_api_surface

    outline = declared_api_surface(source)
    if any(line.startswith(_TRUNCATION_MARKER) for line in outline):
        return None
    methods: Dict[str, set] = {}
    for line in outline:
        if re.match(r"^\s*(?:public|protected|private|static|final|abstract"
                    r"|sealed|open|data|inner|companion)?[\s\w]*\b"
                    r"(?:class|interface|enum|record|object)\b", line):
            continue
        match = _OUTLINE_SIGNATURE.search(line)
        if not match:
            continue
        parameters = match.group("params").strip()
        if "..." in parameters or "<" in parameters:
            arity = None  # varargs or generics: not counted
        else:
            arity = len([p for p in parameters.split(",") if p.strip()])
        methods.setdefault(match.group("name"), set())
        if arity is not None:
            methods[match.group("name")].add(arity)
    return methods


def _declared_supertypes(source: str, type_name: str) -> List[str]:
    """Types the named type extends or implements, as written."""
    match = re.search(
        rf"\b(?:class|interface)\s+{re.escape(type_name)}\b"
        r"(?P<tail>[^{]*)\{",
        source,
    )
    if not match:
        return []
    tail = match.group("tail")
    names = []
    for clause in re.findall(r"(?:extends|implements)\s+([^{]+)", tail):
        for item in clause.split(","):
            name = item.strip().split("<")[0].strip()
            if name and name[0].isupper():
                names.append(name)
    return names


# JDK types whose entire method contract is known here, so a class that only
# implements these has no hidden inherited API. Everything else outside the
# repository is treated as opaque.
_INERT_SUPERTYPES = {
    "Serializable": frozenset(),
    "Cloneable": frozenset(),
    "Parcelable": frozenset({"describeContents", "writeToParcel"}),
    "Comparable": frozenset({"compareTo"}),
    "Runnable": frozenset({"run"}),
    "Closeable": frozenset({"close"}),
    "AutoCloseable": frozenset({"close"}),
    "Iterable": frozenset({"iterator", "forEach", "spliterator"}),
}


def _without_comments(code: str) -> str:
    """Blank out comments so a commented-out call is not read as a call.

    orgzly's test suite carries `// for (String s: EncodingDetect.getAll())`,
    which named a method that genuinely does not exist -- correctly, because
    the line is disabled.
    """
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", code)


def _record_components(source: str, type_name: str) -> set:
    """Component names of a record, which the compiler turns into accessors.

    `public record RecordPolicyStrs(String policyName, String policyStr,
    String ownSymbols)` declares no method at all, yet policyName() and its
    siblings are callable.
    """
    match = re.search(
        rf"\brecord\s+{re.escape(type_name)}\s*\((?P<components>[^)]*)\)",
        source,
        re.DOTALL,
    )
    if not match:
        return set()
    names = set()
    for component in match.group("components").split(","):
        parts = component.strip().split()
        if len(parts) >= 2:
            names.add(parts[-1].strip("[]"))
    return names


def _declaring_candidates(repo_root: Path, type_text: str) -> List[Path]:
    """Files whose stem could declare this type."""
    return list(_source_index(repo_root).get(type_text.split(".")[0], ()))


_PACKAGE_QUALIFIER = re.compile(r"(?:[a-z][A-Za-z0-9_]*\.)+$")
_PACKAGE_DECLARATION = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)


def _package_qualifier_before(code: str, start: int) -> str:
    """The lowercase package path right before a type name: "javax.crypto"."""
    match = _PACKAGE_QUALIFIER.search(code[:start])
    return match.group(0).rstrip(".") if match else ""


def _repository_declares_in_package(repo_root: Path, type_text: str, package: str,
                                    cache: Dict[str, str],
                                    overlay: Optional[Dict[str, str]] = None) -> bool:
    """Whether a repository file in ``package`` declares the type."""
    source = repository_type_source(repo_root, type_text, cache, overlay)
    if source is None:
        return False
    declared = _PACKAGE_DECLARATION.search(source)
    return bool(declared) and declared.group(1) == package


def _is_kotlin_declaration(repo_root: Path, type_text: str) -> bool:
    return any(path.suffix.lower() == ".kt"
               for path in _declaring_candidates(repo_root, type_text))


_KOTLIN_MEMBER_DECLARATION = re.compile(
    r"\b(?:fun|val|var)\s+(?:<[^>]*>\s*)?(?:[\w$.<>?]+\.)?(?P<name>[A-Za-z_$][\w$]*)\b"
)


_KOTLIN_GENERATED_MEMBERS = frozenset({
    "copy", "values", "valueOf", "serializer", "invoke", "entries", "create",
    "newInstance", "builder", "toBuilder",
})
_KOTLIN_GENERATING_ANNOTATION = re.compile(
    r"@(?:Serializable|Parcelize|Entity|Dao|Database|Module|Provides|Inject"
    r"|AutoValue|Generated|Immutable|Value|Data|Builder)\b"
)


def _kotlin_supertypes(source: str, type_name: str) -> Optional[List[str]]:
    """Supertypes in a Kotlin declaration header, or None when it cannot be read."""
    match = re.search(
        rf"\b(?:class|interface|object)\s+{re.escape(type_name)}\b(?P<header>[^{{=]*)",
        source,
    )
    if not match:
        return None
    header = match.group("header")
    if ":" not in header:
        return []
    tail = header.split(":", 1)[1]
    names = []
    for item in re.split(r",(?![^<]*>)", tail):
        name = re.sub(r"\(.*", "", item.strip()).split("<")[0].strip().split(".")[-1]
        if name:
            names.append(name)
    return names


def _kotlin_member_declared_nowhere(
    repo_root: Path, source: str, type_name: str, method: str,
    overlay: Optional[Dict[str, str]], cache: Dict[str, str],
) -> bool:
    """Whether a member called on a Kotlin repository type is declared nowhere.

    hfut-schedule's generated test called
    `GiteeService.getUpdateQueryParameterNamesForTest()`, a "test seam" the
    same patch's later attempt had removed from the production file. The check
    was silent for every Kotlin type, and the missing member cost a
    six-minute androidTest build, a repair, and a forty-minute revalidation.

    Silent unless nothing could supply the member: no file of the repository
    (patched as it will be) declares a function or property of that name, and
    the type's supertypes are all declared in the repository, so nothing
    inherited from a library can be carrying it.
    """
    # Members the compiler or a plugin generates: no source declares them.
    if (method in _KOTLIN_GENERATED_MEMBERS or re.fullmatch(r"component\d+", method)
            or _KOTLIN_GENERATING_ANNOTATION.search(source)):
        return False
    supertypes = _kotlin_supertypes(source, type_name)
    if supertypes is None:
        return False
    for name in supertypes:
        if repository_type_source(repo_root, name, cache, overlay) is None:
            return False
    try:
        from cognalys.solutions.signature_change import declarations, repository_texts

        for text in repository_texts(repo_root, overlay or {}, [method]).values():
            if method not in text:
                continue
            if any(match.group("name") == method
                   for match in _KOTLIN_MEMBER_DECLARATION.finditer(text)):
                return False
            if any(item.name == method for item in declarations(text, "")):
                return False
    except Exception as exc:  # noqa: BLE001 -- a heuristic must not block generation
        _LOG.warning("Kotlin member lookup failed for %s; not reported as undefined: %s", method, exc)
        return False
    return True


def _method_resolves(
    repo_root: Path, type_text: str, method: str, arity: Optional[int],
    cache: Dict[str, str], seen: set,
    overlay: Optional[Dict[str, str]] = None,
) -> bool:
    """Whether `method` is reachable on `type_text`, erring towards yes.

    Silence is the default for everything this crude reader cannot settle: a
    type the repository does not declare, an outline that was truncated, or
    a supertype that lives outside the repository and could be carrying the
    method. Only a type whose whole hierarchy is visible here, and which
    declares no such method anywhere in it, is reported.
    """
    if type_text in seen:
        return True
    seen.add(type_text)
    simple = type_text.split(".")[-1]
    # The same simple name can be declared in more than one file -- diaguard
    # has a DateTimeUtils in main and another in androidTest -- and nothing
    # here resolves imports, so which one a receiver has is unknowable.
    if len(_declaring_candidates(repo_root, type_text)) > 1:
        return True
    source = repository_type_source(repo_root, type_text, cache, overlay)
    if source is None:
        return True  # not a repository type: nothing to check against
    if _is_kotlin_declaration(repo_root, type_text):
        # Kotlin puts callable members in places this Java-shaped reader does
        # not look: companion objects, top-level functions, properties whose
        # accessors are generated. Reporting against a partial view of a
        # Kotlin type would block correct code -- so the only Kotlin member
        # reported is one that no file of the repository declares anywhere.
        return not _kotlin_member_declared_nowhere(
            repo_root, source, simple, method, overlay, cache,
        )
    if re.search(rf"\benum\s+(?:class\s+)?{re.escape(simple)}\b", source) \
            and method in _ENUM_SYNTHETIC_METHODS:
        return True
    if method in _record_components(source, simple):
        return True
    methods = declared_methods(source)
    if methods is None:
        return True  # truncated outline: absence proves nothing
    if method in methods:
        return True
    # The outline is a lossy view: an annotation form, a Kotlin idiom or a
    # signature this reader cannot shape still declares a real method. So a
    # name that appears anywhere in the source as a call target is treated
    # as declared. Only a name with no occurrence at all is reported -- the
    # "invented API" shape that caused every measured failure. Judging
    # argument counts here was tried and produced false positives on
    # overloads whose other form the outline had dropped.
    if re.search(rf"\b{re.escape(method)}\s*\(", source):
        return True
    supertypes = _declared_supertypes(source, simple)
    if not supertypes:
        return False
    for name in supertypes:
        if repository_type_source(repo_root, name, cache, overlay) is None:
            # A supertype outside the repository usually brings an API that
            # cannot be read here -- anything extending Application or
            # AppCompatActivity inherits hundreds of methods -- so nothing
            # can be concluded and the check goes quiet. The exception is the
            # handful of JDK interfaces whose whole contract is known and
            # tiny: treating those as opaque would silence the gate for
            # almost every class, since implementing Serializable is
            # near-universal.
            if name not in _INERT_SUPERTYPES:
                return True
            if method in _INERT_SUPERTYPES[name]:
                return True
            continue
        if _method_resolves(repo_root, name, method, arity, cache, seen, overlay):
            return True
    return False


def _call_arity(code: str, start: int) -> Optional[int]:
    """Argument count of the call whose opening parenthesis precedes `start`."""
    depth, arguments, buffer = 1, [], ""
    for character in code[start:]:
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
            if depth == 0:
                break
        if depth == 1 and character == ",":
            arguments.append(buffer)
            buffer = ""
            continue
        buffer += character
    else:
        return None  # unbalanced: do not guess
    if buffer.strip():
        arguments.append(buffer)
    return len(arguments)


def unresolved_repository_symbols(error: Dict, edits: List[Dict]) -> List[Dict]:
    """Reject a call to a method the receiver's type does not declare.

    unresolvable_constructor_calls() closed the same hole for constructors
    only, and only by argument count. Every validation failure measured
    across the recorded runs was in a generated test, and the cause was the
    same each time: code written against an API the model expected rather
    than the one the repository declares. Catching it here replaces a two-
    to five-minute Gradle round trip with a source check.

    Deliberately conservative in the same way as its constructor sibling: a
    receiver whose type is not declared locally, a hierarchy that leaves the
    repository, an outline too large to read whole, or a call this reader
    cannot parse all produce silence rather than a block.
    """
    repo_root = Path(str(error.get("repository_path", "")))
    if not repo_root.parts or not repo_root.is_dir():
        return []

    cache: Dict[str, str] = {}
    overlay = _coordinated_overlay(repo_root, edits)
    issues = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        code = _without_comments(str(edit.get("corrected_code", "")))
        path = str(
            edit.get("source_file_path") or edit.get("source_file") or ""
        )
        if not code:
            continue
        declared = {
            match.group("name"): match.group("type")
            for match in _LOCAL_DECLARATION.finditer(code)
        }
        details = []
        for match in _INSTANCE_CALL.finditer(code):
            method = match.group("method")
            if method in _UNIVERSAL_METHODS:
                continue
            type_text = declared.get(match.group("receiver"))
            if not type_text:
                continue
            arity = _call_arity(code, match.end())
            if arity is None:
                continue
            if not _method_resolves(
                repo_root, type_text, method, arity, cache, set(), overlay
            ):
                details.append(
                    f"{type_text} declares no {method}(...), directly or "
                    f"through a supertype declared in this repository"
                )
        for match in _STATIC_CALL.finditer(code):
            method = match.group("method")
            if method in _UNIVERSAL_METHODS:
                continue
            type_text = match.group("receiver")
            arity = _call_arity(code, match.end())
            if arity is None:
                continue
            # A package-qualified receiver names one type exactly:
            # javax.crypto.Cipher.getInstance(...) in Cryptomator, whose own
            # org.cryptomator.util.crypto.Cipher declares no getInstance and
            # blocked three drafts (2026-10-26). Only a repository type in
            # that very package is checked.
            package = _package_qualifier_before(code, match.start())
            if package and not _repository_declares_in_package(
                    repo_root, type_text, package, cache, overlay):
                continue
            if not _method_resolves(
                repo_root, type_text, method, arity, cache, set(), overlay
            ):
                details.append(
                    f"{type_text} declares no static {method}(...)"
                )
        if details:
            issues.append({
                "code": "SYMBOL_NOT_DECLARED_IN_REPOSITORY",
                "source_file_path": path,
                "details": sorted(set(details)),
            })
    return issues


# Codes that make a production file drop symbols it had added. A test drafted
# against the earlier surface may call one of them, and would otherwise reach the
# next build still calling a member that no longer exists (hfut-schedule: a
# retry regenerated the service without its test seam, the test kept calling it).
_SURFACE_REMOVAL_CODES = frozenset({"UNNECESSARY_PUBLIC_API_EXPANSION"})

def _anchor_owner_is_object(repository_path: str, source_file: str, anchor: str) -> bool:
    """Whether the type enclosing `anchor` in the repository's file is a
    Kotlin object/companion. False whenever the file or anchor is not found."""
    if not repository_path or not source_file or not anchor.strip():
        return False
    try:
        content = (Path(repository_path) / source_file).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return False
    position = content.find(anchor.strip())
    if position < 0:
        return False
    stack = enclosing_class_stack(content, position)
    return bool(stack) and bool(stack[-1].get("is_object"))


_IMPLICIT_PUBLIC_FUN = re.compile(
    r"(?m)^[ \t]*(?!private\b|internal\b|protected\b|public\b)"
    r"fun\s+(?:<[^>]*>\s*)?(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*)\s*\("
)
_TYPE_HEADER = re.compile(r"\b(?:class|object|interface)\b")
# A type declaration line: annotations, modifiers, then the type keyword.
# Anything else before the keyword (`fun f() = object : T`, `Foo::class`)
# is not a declaration whose modifiers bound its members.
_TYPE_DECLARATION_LINE = re.compile(
    r"(?m)^[ \t]*(?:@[\w.]+(?:\([^()\n]*\))?[ \t]+)*"
    r"(?P<modifiers>(?:(?:private|internal|public|protected|data|sealed|abstract|open"
    r"|inner|enum|annotation|companion|value|inline|expect|actual|final|fun)[ \t]+)*)"
    r"(?P<keyword>class|object|interface)\b"
)
_LAST_TYPE_KEYWORD = re.compile(r"(?<!::)\b(?:class|object|interface)\b")


def _restricted_type_header(header: str) -> bool:
    """Whether a brace header declares a private or internal type.

    Only the header's last type keyword counts, and only when it starts a
    declaration line, so a header that is not plainly such a declaration
    reads as unrestricted (members counted as API, the conservative side).
    """
    keywords = list(_LAST_TYPE_KEYWORD.finditer(header))
    if not keywords:
        return False
    last = keywords[-1].start()
    for match in _TYPE_DECLARATION_LINE.finditer(header):
        if match.start("keyword") == last:
            return bool(re.search(r"\b(?:private|internal)\b", match.group("modifiers")))
    return False


def implicitly_public_kotlin_functions(code: str) -> set[str]:
    """Kotlin functions with no visibility modifier that are part of an API.

    A function declared inside another function's body (or a lambda, an init
    block, an accessor) is local: nothing outside that body can call it, so it
    widens no API. InterTune (2026-10-27): a local `migrateArtwork` inside a
    private method blocked a correct migration as a public-API expansion.
    A brace opens a type body when its header names class/object/interface;
    any other brace is a code body, and a `fun` inside one is local. The
    fragment's own outermost level counts as a member level.

    A member of a private or internal type is no wider than that type: Kotlin
    bounds a member's visibility by its container's. InterTune run_v3
    (2026-11-09): the abstract functions of an `internal interface` the draft
    added as a test seam (which cannot be made private) were reported as public
    API at two of three attempts, and the finding ended with no correction.
    """
    stripped = re.sub(r'"(?:\\.|[^"\\])*"', '""', code)
    stripped = re.sub(r"//[^\n]*", "", stripped)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.S)
    local_starts: list[tuple[int, int]] = []
    # (is code body, offset, declares a private or internal type)
    stack: list[tuple[bool, int, bool]] = []
    header_start = 0
    for index, char in enumerate(stripped):
        if char == "{":
            header = stripped[header_start:index]
            is_type = bool(_TYPE_HEADER.search(header))
            stack.append((not is_type, index, is_type and _restricted_type_header(header)))
            header_start = index + 1
        elif char == "}":
            if stack:
                is_body, start, restricted = stack.pop()
                if (is_body and not any(body for body, _, _ in stack)) or restricted:
                    local_starts.append((start, index))
            header_start = index + 1
    # A brace header is the text since the previous brace. A stray "class"
    # earlier in it (Foo::class) reads the brace as a type body, which counts
    # more functions as API -- the conservative side. An unclosed body (a
    # truncated fragment) is not treated as local or restricted either.
    names = set()
    for match in _IMPLICIT_PUBLIC_FUN.finditer(stripped):
        position = match.start()
        if any(start < position < end for start, end in local_starts):
            continue
        names.add(match.group(1))
    return names
# Contract gates that refuse the behaviour a production draft chose (see
# contract_gates): the replacement behaviour differs, so its tests do too.
_BEHAVIOUR_REFUSAL_CODES = frozenset({
    "PINNED_NEGOTIATED_PROTOCOL", "PROTOCOL_MANDATED_ALGORITHM", "USER_SETTING_REMOVED",
    "STORED_DERIVED_VALUE_CHANGED", "CONSTANT_KEY_MATERIAL_KEPT",
    "SHIPPED_CIPHERTEXT_UNREADABLE",
    "SHIPPED_CIPHERTEXT_REMOVED",
    "VALUE_STILL_CONFIGURES_OTHER_API",
})
# Feedback that asks a production draft to behave otherwise than the draft
# the planned tests were written against: a regression the behaviour review
# found, an edit for a joint finding with no effect, an input the new path
# dropped, an explicit default that changes more (all returned by
# coordinated_generation on the reviewed draft). Session writeEntry
# (d1_validation, 2026-11-10): JOINT_FINDING_EDIT_WITHOUT_EFFECT took the
# RawAesKey wrapper out of LogFile.java, and the kept
# LogFileKeyEncodingRegressionTest still cloned the key bytes as the removed
# code had and still assumed "the production Key implementation". Over every
# recorded run, 87 such regenerations of 49 units kept a test written for
# the refused draft. Unlike _BEHAVIOUR_REFUSAL_CODES, these take the tests
# only when a production file is regenerated: a regression the review found
# in a test regenerates that test alone.
_REDRAFTED_BEHAVIOUR_CODES = frozenset({
    "UNINTENDED_BEHAVIOR_CHANGE", "JOINT_FINDING_EDIT_WITHOUT_EFFECT",
    "DROPPED_INPUT_NO_LONGER_CONTRIBUTES", "EXPLICIT_DEFAULT_CHANGE_TOUCHES_MORE",
})


def semantic_validation_retry_targets(
    errors: List[Dict], generation_paths: List[str]
) -> set:
    """Pick which planned files a semantic-validation retry should regenerate.

    An error that names a specific ``path`` (e.g. a changed public contract)
    targets only that file. An error code known to describe test coverage or
    production behavior targets that half of the plan. Any other code falls
    back to every planned file, so a retry never silently skips a file that
    may need to change. Feedback that asks a regenerated production file to
    behave otherwise (_REDRAFTED_BEHAVIOUR_CODES) takes the planned tests too.
    """
    test_paths = {path for path in generation_paths if is_test_source(path)}
    production_paths = set(generation_paths) - test_paths
    targets: set = set()
    for item in errors:
        code = str(item.get("code", ""))
        affected = set(item.get("affected_paths") or [])
        if affected:
            targets |= affected & set(generation_paths)
            if affected - set(generation_paths):
                return set(generation_paths)
            continue
        # Checks in this module report the offending file as
        # `source_file_path`, matching the shape of an edit, while earlier
        # ones used `path`. Reading only one of them made the precise
        # per-file targeting below silently never fire for the others, so a
        # retry fell back to the code's half of the plan -- and regenerated
        # production for a defect that was in the generated test.
        path = str(item.get("path") or item.get("source_file_path") or "")
        if path and path in generation_paths:
            targets.add(path)
            # Files that must change together with this one, such as the
            # callers of a renamed declaration.
            targets |= set(item.get("retry_paths") or []) & set(generation_paths)
        elif code in _SEMANTIC_VALIDATION_TEST_CODES:
            targets |= test_paths
        elif code in _SEMANTIC_VALIDATION_PRODUCTION_CODES:
            targets |= production_paths
        else:
            return set(generation_paths)
    if any(str(item.get("code", "")) in _SURFACE_REMOVAL_CODES for item in errors):
        targets |= test_paths
    # A production draft whose behaviour was refused takes the planned tests
    # with it: they were written for the refused behaviour. Session's retry
    # (2026-10-15) changed getInstance("TLSv1.2") to "TLS" and kept a test
    # asserting the TLSv1.2 context. A defect that leaves the behaviour as
    # planned (a constructor that does not exist) keeps its tests.
    if any(str(item.get("code", "")) in _BEHAVIOUR_REFUSAL_CODES for item in errors):
        targets |= test_paths
    # The tests of a production file regenerated for one of these were written
    # for the draft being replaced, and so were their review_assumptions.
    # Test paths by source set (is_test_source_path), not by the word "test":
    # AndroidAttestation.kt is production.
    if (any(str(item.get("code", "")) in _REDRAFTED_BEHAVIOUR_CODES for item in errors)
            and any(not is_test_source_path(path) for path in targets)):
        targets |= {path for path in generation_paths if is_test_source_path(path)}
    return targets or set(generation_paths)


def validate_multi_file_scope(error: Dict, analysis: Dict) -> List[str]:
    """Reject supported decisions that did not account for impact evidence."""
    impact = (
        error.get("expanded_repository_context", {})
        .get("multi_file_impact", {})
    )
    if not isinstance(impact, dict) or impact.get("status") != "available":
        return []
    decision = str(analysis.get("decision", "")).upper()
    supported = {
        "FIX_SUPPORTED", "MULTI_FILE_FIX_SUPPORTED",
        "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW",
        "SAFE_INTERNAL_DIGEST_MIGRATION",
        "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
        "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
        "DEPENDENCY_FIX_SUPPORTED", "DEPENDENCY_REPLACEMENT_SUPPORTED",
    }
    if decision not in supported or not impact.get("multi_file_candidate"):
        return []
    issues = []
    scope = analysis.get("scope_assessment", {})
    if not isinstance(scope, dict):
        return ["scope_assessment is missing"]
    adjudication = (
        analysis.get("v2", {}).get("adjudication", {})
        if analysis.get("architecture_version") == "v2" else {}
    )
    review_with_publication_blocked = (
        decision in {
            "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW",
            "BACKEND_MIGRATION_REQUIRED_REVIEW_ONLY",
            "MULTI_REPOSITORY_MIGRATION_SUPPORTED",
        }
        and adjudication.get("generation_status")
        == "supported_for_review"
        and adjudication.get("publication_status") == "blocked"
        and not adjudication.get("blocking_for_generation")
    )
    if (scope.get("status") != "complete"
            and not review_with_publication_blocked):
        issues.append("scope_assessment.status must be complete")
    local_scope = scope.get("local_code_scope")
    if (local_scope not in {None, "complete", "not_applicable"}
            and not review_with_publication_blocked):
        issues.append("scope_assessment.local_code_scope must be complete")
    unresolved = scope.get("unresolved_relations", [])
    if not isinstance(unresolved, list):
        issues.append("scope_assessment.unresolved_relations must be a list")
    else:
        blocking_relations = []
        for relation in unresolved:
            # Legacy string relations remain blocking. New structured
            # relations let review-only fixes distinguish complete local
            # code scope from unavailable backend/deployment contracts.
            if not isinstance(relation, dict):
                blocking_relations.append(str(relation))
            elif relation.get("blocking_for_generation") is True:
                blocking_relations.append(
                    str(relation.get("description", relation.get("kind", "relation")))
                )
        if blocking_relations:
            issues.append("scope_assessment has generation-blocking relations")
    reviewed = scope.get("reviewed_files", [])
    reviewed_paths = {
        str(item.get("source_file_path", ""))
        for item in reviewed if isinstance(item, dict)
    } if isinstance(reviewed, list) else set()
    coverage = impact.get("coverage_requirements", {})
    signature_preserving = (
        error.get("case_migration_policy", {}).get("consumer_scope")
        == "signature_preserving_internal_transport_change"
    )
    required_review = set(coverage.get("declaration_files", []))
    if not signature_preserving:
        required_review.update(coverage.get("caller_files", []))
        required_review.update(coverage.get("ambiguous_call_files", []))
        required_review.update(coverage.get("flow_consumer_files", []))
    required_review.update(coverage.get("behavioral_contract_files", []))
    missing = sorted(path for path in required_review if path not in reviewed_paths)
    if missing and not review_with_publication_blocked:
        issues.append(
            "scope assessment did not review required files: "
            + ", ".join(missing[:12])
        )
    elif missing:
        # A review-only decision whose publication is already blocked and
        # whose adjudication reports no generation-blocking relation should
        # not die here: the three sibling scope checks above are already
        # waived in exactly this situation, and this one was the odd one out.
        # Waiving it silently would be worse than blocking, though, so the
        # unexamined consumers are handed to the human who has to approve the
        # patch. unreviewed_coverage_files is read by process_error, which
        # turns it into a review_assumption in the published artifacts.
        scope.setdefault("unreviewed_coverage_files", missing)
    # Every file fact below comes from one PatchScope built from the plan,
    # rather than from scope_assessment's own count. Those were two
    # derivations of the same thing, and checking one against the other is
    # what let a required new test file read as a coordinated multi-file
    # change while the adjudicator answered FIX_SUPPORTED.
    patch = PatchScope.from_required_edits(
        analysis.get("coordinated_fix_plan", {}).get("required_edits", [])
    )
    if patch.is_multi_file and decision == "FIX_SUPPORTED":
        issues.append("multi-file changes are required but decision is FIX_SUPPORTED")
    # Only a decision that must genuinely touch two or more production files
    # needs to be multi-file. minimum_planned_files deliberately allows a
    # REVIEW_ONLY_DECISIONS plan to be one correctly-scoped file, and this
    # check used to contradict it: such a plan passed there and died here.
    if (decision in MULTI_FILE_DECISIONS and not patch.is_multi_file
            and minimum_planned_files(decision) > 1):
        issues.append("multi-file decision lacks multi_file_change_required=true")
    if decision in MULTI_FILE_DECISIONS:
        edit_paths = {
            str(item.get("source_file_path", ""))
            for item in reviewed
            if isinstance(item, dict) and item.get("action") == "edit"
        } if isinstance(reviewed, list) else set()
        if len(patch.all_files) < minimum_planned_files(decision):
            issues.append("multi-file plan must edit at least two distinct files")
        if not edit_paths.issubset(patch.all_files):
            issues.append("reviewed files marked edit are missing from the plan")
    completeness = impact.get("scope_completeness", {})
    if (decision in MULTI_FILE_DECISIONS
            and completeness.get("status") != "complete"
            and not review_with_publication_blocked):
        issues.append("structural repository scope is incomplete")
    return issues


_SKIPPED_TEST_SCAN_DIRS = frozenset({".git", "build", ".gradle", "node_modules", ".idea", ".cxx"})


def repository_test_source_roots(repository_path: str, max_depth: int = 6) -> List[str]:
    """Test source sets that exist in the checkout, found on disk.

    The plan's established test roots came only from test files that
    happened to be in the evidence manifest, so a repository with a test
    source set but no test file in the packet looked like one without tests.
    """
    root = Path(str(repository_path or ""))
    if not repository_path or not root.is_dir():
        return []
    found: List[str] = []
    for directory, names, _files in os.walk(root):
        names[:] = [name for name in names if name not in _SKIPPED_TEST_SCAN_DIRS]
        parts = Path(directory).relative_to(root).parts
        if len(parts) >= max_depth:
            names[:] = []
        if (len(parts) >= 3 and parts[-3] == "src"
                and test_source_set(f"src/{parts[-2]}/")
                and parts[-1] in {"java", "kotlin"}):
            found.append(Path(*parts).as_posix())
            names[:] = []
    return sorted(found)


_TEST_INFRASTRUCTURE_TEXT = re.compile(
    r"test[\s_-]*source[\s_-]*root|established[\s_-]*test|test[\s_-]*root"
    r"|no\s+(?:established\s+)?test|test[\s_-]*source[\s_-]*set|test[\s_-]*file",
    re.IGNORECASE,
)
# Delimited explicitly: categories are snake_case ("caller_contract_documentation"),
# and "_" is a word character, so \b never matches inside them.
_DOCUMENTATION_ONLY_CATEGORY = re.compile(
    r"(?:^|[^a-z])(?:documentation|comments?|kdoc|javadoc)(?:[^a-z]|$)", re.IGNORECASE,
)
_PLANNED_TEST_PATH = re.compile(TEST_SOURCE_SET_SEGMENT.pattern + r"(?:java|kotlin)/")


def repair_patch_plan(plan: Dict, repository_has_test_roots: bool) -> tuple:
    """Deterministic repairs that keep a supported plan instead of abstaining.

    Gallery's AsyncTask migration (run_20260913_204821) had a supported,
    invariant-preserving plan that was discarded for two reasons neither of
    which was about the fix: it required unit tests in a repository with no
    test source set at all, and it padded the plan with five edits that only
    added comments at call sites. Only those two things are repaired here:

      * edits whose category is documentation or comments are dropped, when
        at least one real edit remains;
      * when the repository has no test source set, required tests become a
        reviewer checklist among the publication blockers, planned test files
        that could never be created are dropped, and generation blockers that
        only say tests cannot be planned move to publication.

    Any other issue is left for the validator to reject. Returns the repaired
    plan and a description of each repair made.
    """
    repaired = dict(plan)
    repairs: List[str] = []
    edits = [item for item in plan.get("required_edits") or [] if isinstance(item, dict)]
    documentation = [
        item for item in edits
        if _DOCUMENTATION_ONLY_CATEGORY.search(str(item.get("edit_category", "")))
    ]
    if documentation and len(documentation) < len(edits):
        edits = [item for item in edits if item not in documentation]
        repairs.append(
            "removed documentation-only edits to "
            + ", ".join(sorted({str(item.get("source_file_path", "")) for item in documentation}))
        )
    if not repository_has_test_roots:
        planned_tests = [
            item for item in edits
            if _PLANNED_TEST_PATH.search(str(item.get("source_file_path", "")))
        ]
        tests = [str(item) for item in plan.get("required_tests") or [] if str(item).strip()]
        blockers = [str(item) for item in plan.get("generation_blockers") or []]
        test_blockers = [item for item in blockers if _TEST_INFRASTRUCTURE_TEXT.search(item)]
        if (planned_tests and len(planned_tests) < len(edits)) or tests or test_blockers:
            if len(planned_tests) < len(edits):
                edits = [item for item in edits if item not in planned_tests]
            repaired["required_tests"] = []
            repaired["generation_blockers"] = [
                item for item in blockers
                if item not in test_blockers and item != "patch plan reports generation blockers"
            ]
            repaired["publication_blockers"] = list(dict.fromkeys([
                *(str(item) for item in plan.get("publication_blockers") or []),
                *(
                    "Reviewer checklist (the repository has no test source set, so "
                    f"no automated test was planned): {test}"
                    for test in tests
                ),
                *test_blockers,
            ]))
            repairs.append(
                "the repository has no test source set: moved "
                f"{len(tests)} required test(s), {len(planned_tests)} planned test "
                f"file(s) and {len(test_blockers)} test-infrastructure blocker(s) "
                "to the reviewer checklist"
            )
    repaired["required_edits"] = edits
    return repaired, repairs


_PARAMETER_ANNOTATION = re.compile(r"@[\w.]+(?:\([^)]*\))?\s*")
_PRIMITIVE_TYPES = frozenset({
    "boolean", "byte", "char", "short", "int", "long", "float", "double",
})
_PARAMETER_LIST = r"\(((?:[^()]|\([^()]*\))*)\)"


def _split_parameters(text: str) -> List[str]:
    parts, depth, buffer = [], 0, ""
    for char in text:
        if char in "<([{":
            depth += 1
        elif char in ">)]}":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(buffer)
            buffer = ""
        else:
            buffer += char
    parts.append(buffer)
    return [part.strip() for part in parts if part.strip()]


def _parameter_type(parameter: str) -> str:
    """The simple type of one Java or Kotlin parameter declaration."""
    text = _PARAMETER_ANNOTATION.sub("", parameter).strip()
    text = re.sub(r"^(?:(?:final|vararg|noinline|crossinline)\s+)+", "", text)
    if ":" in text:
        text = text.split(":", 1)[1].split("=", 1)[0].strip()
    else:
        tokens = text.split()
        if len(tokens) > 1:
            text = " ".join(tokens[:-1])
    text = text.replace("...", "[]")
    text = re.sub(r"<.*>", "", text).replace("?", "").replace(" ", "")
    return text.rsplit(".", 1)[-1]


def _planned_parameter_types(text: str):
    """Types a planned symbol names, or None when they cannot be read.

    A bare lower-case token that is not a primitive is a parameter name
    written without its type, and "..." stands for an unspecified list:
    neither says which overload is meant.
    """
    if text.strip() in {"...", "…"}:
        return None
    types = []
    for parameter in _split_parameters(text):
        cleaned = _PARAMETER_ANNOTATION.sub("", parameter).strip()
        if (":" not in cleaned and len(cleaned.split()) == 1
                and cleaned[:1].islower()
                and cleaned.replace("[]", "") not in _PRIMITIVE_TYPES):
            return None
        types.append(_parameter_type(parameter))
    return types


def _declared_parameter_types(content: str, name: str) -> List[List[str]]:
    """Every parameter-type list `content` declares for a method or constructor."""
    patterns = [
        re.compile(
            r"(?<![\w$.])" + re.escape(name) + r"\s*" + _PARAMETER_LIST
            + r"\s*(?:throws\s[^{;]*)?\{"
        ),
        re.compile(
            r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?" + re.escape(name)
            + r"\s*" + _PARAMETER_LIST
        ),
    ]
    declared: List[List[str]] = []
    for pattern in patterns:
        for match in pattern.finditer(content or ""):
            line_start = content.rfind("\n", 0, match.start()) + 1
            if re.search(r"\bnew\s*$", content[line_start:match.start()]):
                continue  # an anonymous subclass, not a declaration
            types = [_parameter_type(item) for item in _split_parameters(match.group(1))]
            if types not in declared:
                declared.append(types)
    return declared


def planned_symbol_mismatches(plan: Dict, packet: Dict) -> List[str]:
    """Planned edits naming a signature their file does not declare.

    nelenkov/wwwjdic's plan targeted `OcrFormEntity(Bitmap bitmap, String
    lang, String profile, boolean text)`; OcrFormEntity.java declares
    (Bitmap, int, String, Integer), (Bitmap, String, Integer) and (Bitmap)
    (run_20260915_175048). The edit still landed on the right constructor,
    because it matched on the original code, but the generated regression
    test searched the source for the invented parameter list and would have
    failed the first time it ran. The generation itself had written down
    "the target-plan constructor descriptor is stale" and gone on.

    Only a symbol with a readable parameter list, in an existing file whose
    exact source the packet carries and which declares that name at least
    once, is compared -- by the ordered simple types of its parameters. A new
    method, a symbol without parameters, or a list written as names alone is
    not judged.
    """
    contents = {
        str((item.get("data") or {}).get("source_file_path", "")):
        str((item.get("data") or {}).get("content", ""))
        for item in packet.get("evidence_catalog", [])
        if item.get("trust_level") == "repository_exact_source"
    }
    issues = []
    for edit in plan.get("required_edits") or []:
        if not isinstance(edit, dict) or edit.get("create_new_file") is True:
            continue
        path = str(edit.get("source_file_path", ""))
        content = contents.get(path, "")
        symbol = str(edit.get("symbol", "")).strip()
        match = re.match(r"^(?P<head>[^(]*)\((?P<params>.*)\)\s*$", symbol, re.DOTALL)
        if not content or not match:
            continue
        name = re.split(r"[.#:\s]", match.group("head").strip())[-1]
        if not re.fullmatch(r"[A-Za-z_$][\w$]*", name or ""):
            continue
        planned = _planned_parameter_types(match.group("params"))
        if planned is None:
            continue
        declared = _declared_parameter_types(content, name)
        if not declared or [item.lower() for item in planned] in [
                [item.lower() for item in types] for types in declared]:
            continue
        rendered = "; ".join(
            f"{name}({', '.join(types)})" for types in declared[:6]
        )
        issues.append(
            f"planned symbol {symbol} matches no declaration of {name} in "
            f"{path}; the file declares {rendered}"
        )
    return issues


def _validate_patch_plan(plan: Dict, packet: Dict,
                            adjudication: Dict, extra_test_roots=()) -> List[str]:
    issues = []
    if str(plan.get("decision", "")) != str(adjudication.get("decision", "")):
        issues.append("patch plan decision differs from adjudication")
    exact_paths = {
        str(item.get("data", {}).get("source_file_path", ""))
        for item in packet.get("evidence_catalog", [])
        if item.get("trust_level") == "repository_exact_source"
    }
    claim_ids = {
        str(item.get("claim_id", ""))
        for item in adjudication.get("_validated_claims", [])
        if isinstance(item, dict)
    }
    required_edits = plan.get("required_edits", [])
    if not isinstance(required_edits, list) or not required_edits:
        issues.append("supported adjudication requires at least one planned edit")
        required_edits = []
    test_source_roots = set()
    for item in packet.get("evidence_catalog", []):
        if item.get("kind") not in {"test_source", "test_context"}:
            continue
        data = item.get("data", {}) or {}
        test_path = str(data.get(
            "source_file_path", data.get("path", "")
        ))
        match = TEST_SOURCE_ROOT.match(test_path)
        if match:
            test_source_roots.add(match.group(1))
    # Test source sets found on disk (repository_test_source_roots) count as
    # established even when no test file from them is in the packet.
    test_source_roots.update(str(root) for root in extra_test_roots or ())
    production_planned = [
        str(edit.get("source_file_path", "")) for edit in required_edits
        if isinstance(edit, dict) and str(edit.get("source_file_path", ""))
        and not _PLANNED_TEST_PATH.search(str(edit.get("source_file_path", "")))
    ]
    seen = set()
    planned_test_path = False
    for edit in required_edits:
        if not isinstance(edit, dict):
            issues.append("each planned edit must be an object")
            continue
        raw = str(edit.get("source_file_path", ""))
        relative = Path(raw)
        if not raw or relative.is_absolute() or ".." in relative.parts:
            issues.append(f"unsafe planned path: {raw}")
            continue
        if raw in seen:
            issues.append(f"duplicate planned path: {raw}")
        seen.add(raw)
        create = edit.get("create_new_file") is True
        is_test_path = bool(_PLANNED_TEST_PATH.search(raw))
        if is_test_path:
            planned_test_path = True
            misplaced = test_outside_production_modules(raw, production_planned)
            if create and misplaced:
                issues.append(f"planned test {raw}: {misplaced}")
        create_in_established_test_root = create and any(
            raw == root or raw.startswith(root + "/")
            for root in test_source_roots
        )
        if raw not in exact_paths and not (
            create_in_established_test_root
        ):
            issues.append(f"planned path lacks exact source evidence: {raw}")
        supporting_claims = set(edit.get("supporting_claim_ids", []))
        if not supporting_claims:
            issues.append(f"planned edit {raw} has no supporting claim IDs")
        unknown_claims = supporting_claims - claim_ids
        if unknown_claims:
            issues.append(
                f"planned edit {raw} references unknown claims: "
                + ", ".join(sorted(unknown_claims))
            )
    if adjudication.get("generation_status") != "blocked" and not seen:
        issues.append("generation-eligible adjudication requires planned edits")
    plan_decision = str(adjudication.get("decision", ""))
    if (plan_decision in MULTI_FILE_DECISIONS
            and len(seen) < minimum_planned_files(plan_decision)):
        issues.append("multi-file adjudication requires at least two planned paths")
    # Tests are optional (2026-10-15): a required test without a planned test
    # file stays a reviewer note (the PR lists it under "How was this
    # tested?") instead of invalidating the plan. It invalidated nine
    # confirmed, otherwise plannable fixes in earlier runs (DSub2000 among
    # them), each ending as NO_SAFE_FIX.
    if plan.get("generation_blockers"):
        issues.append("patch plan reports generation blockers")
    issues.extend(planned_symbol_mismatches(plan, packet))
    if (packet.get("case_policy") or {}).get("consumer_scope") == "stored_secret_verifier":
        for item in packet.get("evidence_catalog", []):
            if item.get("kind") != "stored_value_contract":
                continue
            contract = item.get("data") or {}
            # A verifier stored outside preferences ("not_modelled") has no
            # collected readers to plan against, so it stays incomplete here.
            if contract.get("status") != "complete":
                issues.append("stored value migration scope is incomplete: " + "; ".join(
                    contract.get("limitations") or [
                        "persisted outside preferences at " + f"{item.get('path', '')}:{item.get('line', 0)}"
                        for item in contract.get("unmodelled_sinks", [])]))
            for path in contract.get("required_paths", []):
                if path not in exact_paths:
                    issues.append(f"stored value consumer lacks full source: {path}")
                if path not in seen:
                    issues.append(f"stored value migration plan omits affected consumer: {path}")
    return issues
