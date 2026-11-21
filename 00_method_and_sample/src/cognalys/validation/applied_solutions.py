#!/usr/bin/env python3
"""Build patched projects and attach compilation results to the PR report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from cognalys.validation.behavioral import (  # noqa: E402
    fingerprint_digest,
    toolchain_fingerprint,
)

from cognalys.solutions.signature_change import changed_symbol_names  # noqa: E402
from cognalys.builds.command import build_invocation
from cognalys.utils.test_source_sets import (  # noqa: E402
    TEST_SOURCE_SET_SEGMENT, is_instrumented_test_set, is_test_source_path,
    test_source_set, unit_test_task,
)

# Attributions that mean the patch caused the diagnostic.
PATCH_CAUSED_ATTRIBUTIONS = frozenset({
    "generated_or_replaced_code", "caller_of_changed_symbol",
})


# Must stay equal to utils.disposition.BUILD_VALIDATION_PASSING_STATUSES,
# which is where pr_generator, pr_creator and the preview writer read the same
# set from; test_pr_creator_build_gate pins the two together.
# The patch compiled at the analysed revision while the base commit fails
# without it (see run_analyzed_revision_build).
ANALYZED_REVISION_PASS = "success_on_analyzed_revision"
# The production build passed; the module's test build fails at the same
# tasks on the base commit, before any test compiles, and the tests the patch
# created there were removed (see the patched_tests branch of main). Only
# production code was verified, which is what the status says.
PRODUCTION_ONLY_TEST_BUILD_PREEXISTING = "success_production_only_test_build_preexisting"
# The compile-only recheck after a failed or timed-out build, in a project
# whose only Gradle module is the patched one: everything compiled, nothing
# was packaged. "app module only" would suggest other modules went unbuilt.
COMPILE_ONLY_PASS = "success_compile_only"
PASSING_STATUSES = {
    "success", "success_app_module_only", ANALYZED_REVISION_PASS,
    PRODUCTION_ONLY_TEST_BUILD_PREEXISTING, COMPILE_ONLY_PASS,
}

# Failures this validation already proved the patch did not cause: the
# unmodified base commit fails the same way, or Gradle died configuring a
# project whose build files the patch never touched. The attribution was being
# computed carefully and then ignored by both the summary and the exit code,
# so a project that simply cannot build in this environment was reported as
# the finding's failure and aborted the run under --fail-on-validation-error.
# hanjoongcho/aaf-easydiary does exactly that: app/build.gradle.kts reads a
# local.properties that its README tells each developer to create, so neither
# the patched worktree nor the baseline can configure, and run_20260908_163511
# stopped there with a correct, applied Zip Slip fix already in hand.
#
# These are NOT passes. The patch remains uncompiled and unverified, so it
# stays outside PASSING_STATUSES and pr_generator still refuses to publish it.
# The only thing that changes is that the pipeline stops blaming the finding
# and stops aborting, so the remaining stages still produce their review
# artefacts.
UNATTRIBUTABLE_FAILURES = {
    "verified_preexisting_build_failure",
    "verified_preexisting_test_debt",
    "preexisting_build_configuration",
    # The validation requested a Gradle task that does not exist: the
    # patch's tests never compiled, so nothing is known against it.
    "validation_task_unresolved",
    # The local Gradle wrapper could not install its distribution; the
    # machine, not the project or the patch, has to be fixed.
    "build_environment",
    # The build was still compiling when the time limit ended it: nothing
    # failed, so nothing is known against the patch either.
    "build_timeout",
}


def failure_is_the_patch(result: dict) -> bool:
    """Whether a non-passing build result can be blamed on the patch."""
    if result.get("status") in PASSING_STATUSES:
        return False
    return str(
        result.get("failure_attribution", "")
    ) not in UNATTRIBUTABLE_FAILURES

# The worktree `.git` shim lives with the build step, which applies it around
# every Gradle build; the names stay importable here.
from cognalys.builds.git_directory import (  # noqa: E402,F401
    GIT_POINTER_BACKUP as _GIT_POINTER_BACKUP,
    build_reads_git_directory,
    readable_git_directory,
)


def validate_execution_report(report: dict) -> list[str]:
    """Deterministic shape guard for execution_report.json (see
    solution_applicator.py's identical check for why this exists)."""
    issues = []
    details = report.get("per_solution_details")
    if not isinstance(details, list):
        return ["per_solution_details must be a list"]
    for index, entry in enumerate(details):
        if not isinstance(entry, dict):
            issues.append(f"per_solution_details[{index}] is not an object")
            continue
        if entry.get("status") == "SUCCESS" and not str(
                entry.get("finding_id", "")).strip():
            issues.append(
                f"per_solution_details[{index}] is SUCCESS without finding_id"
            )
    return issues


def write_json_atomic(path: Path, value: dict | list) -> None:
    """Write via write-then-rename so a crash mid-write can't truncate a
    file every later pipeline step depends on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temporary.replace(path)


_JAVAC_DIAGNOSTIC = re.compile(
    r"^(.*\.(?:java|kt|kts)):(\d+):(?:\d+:)?\s*error:\s*(.*)$"
)
# kotlinc reports errors as `e: file://<path>:<line>:<col> <message>` (or, on
# older/other configurations, without the `file://` prefix) with no source
# excerpt on the following log line, unlike javac.
_KOTLINC_DIAGNOSTIC = re.compile(
    r"^e:\s+(?:file://)?(.*\.(?:kt|kts)):(\d+):(\d+)\s+(.*)$"
)
# Newer Kotlin Gradle plugins report through Gradle's problems API instead:
#   Problem found: Kotlin compiler: INVISIBLE_REFERENCE (id: kotlin:compiler:error:INVISIBLE_REFERENCE)
#     Kotlin compiler error
#       Cannot access 'fun calculateChildType(): Int': it is private in '...'.
#       Location: /path/Test.kt line 15
# with no `e:` line at all, so a test that did not compile read as a build
# with no diagnostics and validation_fix had nothing to repair.
_PROBLEM_REPORT = re.compile(
    r"^Problem found:\s.*\(id:\s*[\w.-]+:compiler:error:[\w.-]+\)\s*$"
)
_PROBLEM_LOCATION = re.compile(r"^Location:\s+(?:file://)?(.+?\.(?:java|kt|kts))\s+line\s+(\d+)\s*$")
_PROBLEM_REPORT_LINES = 12


def _problem_report(lines: list[str], index: int):
    """(path, line, message) of the error report starting at lines[index]."""
    message: list[str] = []
    for raw in lines[index + 1:index + 1 + _PROBLEM_REPORT_LINES]:
        text = raw.strip()
        location = _PROBLEM_LOCATION.match(text)
        if location:
            return location.group(1), int(location.group(2)), " ".join(message)
        if text.startswith("Problem found:"):
            return None
        if text and not re.fullmatch(r"\w[\w ]* compiler (?:error|warning)", text):
            message.append(text)
    return None


def _kotlinc_source_line(path: str, line_number: int) -> str:
    """Best-effort read of the offending line directly from the source file,
    since kotlinc (unlike javac) does not echo it into the build log."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
    except OSError:
        return ""
    if 1 <= line_number <= len(lines):
        return lines[line_number - 1].strip()
    return ""


def compiler_diagnostics(log_file: Path, coordinated_edits: list[dict]) -> list[dict]:
    """Extract compiler errors and attribute their source line to generated edits."""
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    additions = {
        line.strip()
        for edit in coordinated_edits if isinstance(edit, dict)
        for line in str(edit.get("corrected_code", "")).splitlines()
        if line.strip()
    }
    # A line the patch did not write can still be the patch's doing: it calls
    # a function whose signature the patch changed, or reads a constant it
    # deleted. hfut-schedule's `gitee.getUpdate()` was recorded as
    # "preexisting_test_or_source_debt" for exactly that reason, and the
    # regeneration guidance told the model to stop appending tests.
    changed_symbols = changed_symbol_names(
        edit for edit in coordinated_edits if isinstance(edit, dict)
    )
    diagnostics = []
    seen: set[tuple[str, int, str]] = set()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        match = _JAVAC_DIAGNOSTIC.match(line)
        if match:
            path = match.group(1).replace("\\", "/")
            line_number = int(match.group(2))
            message = match.group(3).strip()
            source_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        else:
            match = _KOTLINC_DIAGNOSTIC.match(line)
            if match:
                path = match.group(1).replace("\\", "/")
                line_number = int(match.group(2))
                message = match.group(4).strip()
            else:
                report = (_problem_report(lines, index)
                          if _PROBLEM_REPORT.match(line) else None)
                if not report:
                    continue
                path, line_number, message = report
                path = path.replace("\\", "/")
            source_line = _kotlinc_source_line(path, line_number)
        # A build that prints both formats reports each error twice.
        if (path, line_number, message) in seen:
            continue
        seen.add((path, line_number, message))
        generated = source_line in additions if source_line else False
        caller = bool(source_line) and any(
            re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", source_line)
            for name in changed_symbols
        )
        diagnostics.append({
            "path": path,
            "line": line_number,
            "message": message,
            "source_line": source_line,
            "attribution": (
                "generated_or_replaced_code" if generated
                else "caller_of_changed_symbol" if caller
                else "preexisting_code_outside_generated_edits"
            ),
        })
    return diagnostics


_GRADLE_TEST_FAILURE = re.compile(
    r"^(?P<suite>[\w.$]+)\s+>\s+(?P<test>\S.*?)\s+FAILED$"
)
# Any fully qualified throwable, not only names ending in Error/Exception:
# Mockito's verification failures are org.mockito...ArgumentsAreDifferent and
# ...NoInteractionsWanted, and wifianalyzer's three failing tests in
# run_20261009_012517 read as no failure at all, so nothing was repaired.
_GRADLE_TEST_CAUSE = re.compile(
    r"^(?P<exception>(?:[A-Za-z_][\w$]*\.)+[A-Z][\w$]*"
    r"|[\w.$]+(?:Error|Exception)[^\s]*)"
    r"(?::\s*(?P<detail>.*?))?\s+at\s+"
    r"(?P<file>[\w$]+\.(?:java|kt)):(?P<line>\d+)"
)


def _failing_test_source(locate, suite: str, test: str):
    """The failing test's own source file and the line that declares the test.

    Gradle prints the suite as a simple or fully qualified class name, nested
    classes after "$". Returns (None, 0) when no source of that class exists in
    the checkout, so a failure in a class the repository does not have is
    still skipped.
    """
    simple = suite.rsplit(".", 1)[-1].split("$", 1)[0]
    path = next((found for found in (locate(f"{simple}.kt"), locate(f"{simple}.java"))
                 if found is not None), None)
    if path is None:
        return None, 0
    try:
        source_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None, 0
    # Parameterised and display names carry more than the method name.
    name = re.split(r"[\s(\[]", test.strip(), maxsplit=1)[0].strip("`")
    declaration = re.compile(
        rf"(?:\bfun\s+`?{re.escape(name)}`?\s*\(|\b{re.escape(name)}\s*\()")
    backticked = f"`{test.strip()}`"
    number = next(
        (position for position, text in enumerate(source_lines, 1)
         if backticked in text),
        next((position for position, text in enumerate(source_lines, 1)
              if name and declaration.search(text)), 1),
    )
    return path, number


def failing_test_diagnostics(log_file: Path, worktree: Path,
                             coordinated_edits: list[dict]) -> list[dict]:
    """Extract failing unit tests in the same shape as compiler diagnostics.

    A test that compiles and then fails its assertion produces no compiler
    error, so every stage built on compiler_diagnostics went blind: the
    validation step recorded no failure_attribution, and the repair step
    reported "no repairable compiler diagnostics" and left the pipeline
    looping on the same broken test. Emitting the same record shape is what
    lets attribution and repair handle both without a parallel path.
    """
    try:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    additions = {
        line.strip()
        for edit in coordinated_edits if isinstance(edit, dict)
        for line in str(edit.get("corrected_code", "")).splitlines()
        if line.strip()
    }
    sources: dict[str, Path] = {}

    def locate(name: str):
        if name not in sources:
            sources[name] = next(
                (path for path in worktree.rglob(name)
                 if not any(part in {".git", "build", ".gradle"}
                            for part in path.parts)),
                None,
            )
        return sources[name]

    diagnostics = []
    for index, raw_line in enumerate(lines):
        failure = _GRADLE_TEST_FAILURE.match(raw_line.strip())
        if not failure:
            continue
        cause = None
        for offset in range(1, 4):
            if index + offset >= len(lines):
                break
            cause = _GRADLE_TEST_CAUSE.match(lines[index + offset].strip())
            if cause:
                break
        if not cause:
            continue
        path = locate(cause.group("file"))
        number = int(cause.group("line"))
        outside_frame = ""
        if path is None:
            # The cause sits in a frame outside the repository (a test runner
            # or platform class): NotallyX's generated tests failed with
            # "RuntimeException at AndroidInterceptors.java:88", Robolectric's
            # file, so this loop found no source, recorded no failing test,
            # and the repair step had nothing to repair -- a production change
            # that compiled, with the project's own 220 tests passing, was
            # dropped (c_arm_k3, 2026-11-15). The failing test's own source
            # file is then the file to diagnose, at the test's declaration.
            path, number = _failing_test_source(locate, failure.group("suite"),
                                                failure.group("test"))
            if path is None:
                continue
            outside_frame = f"{cause.group('file')}:{cause.group('line')}"
        try:
            source_lines = path.read_text(
                encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        source_line = (
            source_lines[number - 1].strip() if 0 < number <= len(source_lines)
            else ""
        )
        detail = (cause.group("detail") or "").strip()
        if outside_frame:
            caused_by = next(
                (lines[index + step].strip()
                 for step in range(offset + 1, offset + 4)
                 if index + step < len(lines)
                 and lines[index + step].strip().startswith("Caused by:")),
                "",
            )
            detail = "; ".join(part for part in (
                detail, f"thrown at {outside_frame}, outside the repository",
                caused_by) if part)
        diagnostics.append({
            "path": str(path).replace("\\", "/"),
            "line": number,
            "message": "%s failed: %s%s" % (
                failure.group("test"),
                cause.group("exception"),
                f" ({detail})" if detail else "",
            ),
            "source_line": source_line,
            "test_suite": failure.group("suite"),
            "test_name": failure.group("test"),
            "attribution": (
                "generated_or_replaced_code"
                if source_line and source_line in additions
                else "preexisting_code_outside_generated_edits"
            ),
        })
    return diagnostics


def diagnostic_signature(item: dict) -> tuple[str, str, str]:
    """Identify a diagnostic independently of its checkout/worktree prefix."""
    path = str(item.get("path", "")).replace("\\", "/")
    marker = "/src/"
    position = path.lower().find(marker)
    relative = path[position + 1:] if position >= 0 else Path(path).name
    return (
        relative.lower(),
        str(item.get("message", "")).strip(),
        str(item.get("source_line", "")).strip(),
    )


# The factory type whose algorithm literal a specification's allowlist
# constrains, read from the specification's own data: a type it applies to
# (applies_to_types) whose events call getInstance with an argument. Taken
# from the data rather than a table of specification names (2026-10-17
# audit), so a specification for another factory -- KeyGenerator,
# SecretKeyFactory -- is covered as soon as it records the call. A type whose
# getInstance takes no argument (CookieManager) is a singleton, not a factory
# with an algorithm. The detector message must still carry the "expecting one
# of X but found Y" allowlist; a specification without that shape --
# KeyPairGeneratorSpec reports an invalid key size -- stays unsupported there.
def algorithm_factory_type(spec: str, specification: dict | None = None) -> str:
    if specification is None:
        from cognalys.workspace import library_data

        try:
            specification = json.loads((library_data("specifications") / f"{spec}.json")
                                       .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
    patterns = [str(pattern) for event in specification.get("events") or []
                for pattern in (event or {}).get("api_patterns") or []]
    for qualified in specification.get("applies_to_types") or []:
        name = str(qualified).rsplit(".", 1)[-1]
        for pattern in patterns:
            if not (pattern.startswith(f"{name}.getInstance(") or pattern.startswith(".getInstance(")):
                continue
            if not pattern.split("getInstance(", 1)[1].startswith(")"):
                return name
    return ""



def protocol_filter_evidence(content: str, allowed: list) -> list:
    """Lines restricting the effective protocol set to allowlisted versions.

    Deliberately shallow: it looks for the enabled-protocol seam and an
    allowlisted version literal in the same source, which is what the
    generated fixes actually write. It is corroboration that the prescribed
    remediation is present, not a proof that it is correct -- the analyzer
    rerun that this record still demands is what settles that.
    """
    if "nabledProtocols" not in content:
        return []
    wanted = {value.casefold() for value in allowed}
    evidence = []
    for number, line in enumerate(content.splitlines(), 1):
        literals = re.findall(r"[\"']([A-Za-z0-9._-]+)[\"']", line)
        if any(item.casefold() in wanted for item in literals):
            evidence.append({"line": number, "source_line": line.strip()[:200]})
    return evidence


def parse_algorithm_predicate(spec: str, message: str) -> dict:
    """Read the detector's "expecting one of X but found Y" predicate.

    Returns either {"supported": False, "reason": ...} or the parsed
    predicate. The message shapes differ per specification in ways that a
    single hardcoded pattern missed: MessageDigestSpec, SSLContextSpec and
    CipherSpec brace their allowlist, while KeyStoreSpec, MacSpec,
    SignatureSpec and TrustManagerFactorySpec write a bare comma list -- and
    KeyStoreSpec emits "expecting one ofJCEKS,..." with no space at all.
    Both forms are the same predicate and are parsed as one.
    """
    factory_type = algorithm_factory_type(spec)
    if not factory_type:
        return {
            "supported": False,
            "reason": "no deterministic source predicate adapter for specification",
        }
    match = re.search(
        r"expecting\s+one\s+of\s*(?:\{(?P<braced>[^}]*)\}|(?P<bare>.*?))"
        r"\s+but\s+found\b(?P<found>.*)$",
        message,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {
            "supported": False,
            "reason": "detector message has no literal found/allowlist predicate",
        }
    forbidden = match.group("found").strip().rstrip(".").strip()
    if not re.search(r"[A-Za-z0-9]", forbidden):
        # "but found ." is the detector saying it could not resolve the
        # literal at all. That is the majority shape for several
        # specifications, and there is nothing deterministic to check.
        return {
            "supported": False,
            "reason": "detector reported no concrete literal for the violation",
        }
    listed = match.group("braced")
    if listed is None:
        listed = match.group("bare") or ""
    allowed, truncated = [], False
    for value in listed.split(","):
        value = value.strip().strip("'\"").strip()
        if not value:
            continue
        if set(value) <= {"."}:
            # CipherSpec elides the tail of its allowlist with "...", so a
            # replacement that is absent from the visible part is not proof
            # that the replacement is wrong.
            truncated = True
            continue
        allowed.append(value)
    if not allowed:
        return {
            "supported": False,
            "reason": "detector message carries no readable allowlist",
        }
    if any(forbidden.casefold() == value.casefold() for value in allowed):
        # SignatureSpec reports "found SHA256WITHRSA" against an allowlist
        # containing "SHA256withRSA". Case-insensitively the forbidden
        # literal is also an allowed one, so the same source occurrence
        # would count as both violation and fix; no source check can settle
        # that, and guessing either way would be wrong.
        return {
            "supported": False,
            "reason": (
                "detector allowlist contains the reported literal; the "
                "predicate cannot be decided from source"
            ),
        }
    return {
        "supported": True,
        "factory_type": factory_type,
        "forbidden": forbidden,
        "allowed": allowed,
        "allowlist_truncated": truncated,
    }


def _predicate_outcome(substituted: bool, removed: bool, filtered: bool) -> str:
    """Name the shape of fix that satisfied the predicate, if any."""
    if substituted:
        return "substituted"
    if removed:
        return "removed"
    if filtered:
        return "protocol_filtered"
    return ""


def _load_build_result(output_dir: Path, project_name: str,
                       return_code: int) -> dict:
    report = output_dir / "build_report.json"
    if not report.exists():
        return {
            "project_name": project_name,
            "status": "infrastructure_failure",
            "return_code": return_code,
            "reason": "build_report_missing",
            "log_file": "",
        }
    records = json.loads(report.read_text(encoding="utf-8"))
    return dict(records[0]) if records else {
        "project_name": project_name,
        "status": "infrastructure_failure",
        "return_code": return_code,
        "reason": "empty_build_report",
        "log_file": "",
    }


def previous_build_failed_in_place(output_dir: Path, worktree: Path) -> bool:
    """Whether this worktree's last build here failed to compile.

    A repair edits the same worktree and validates it again. Its build outputs
    are already there, and Gradle's up-to-date checks are content-based, so
    only what the repair changed is rebuilt -- minutes instead of the sixteen
    a `clean` build of hfut-schedule takes. A previous success is not treated
    this way: re-validating an untouched tree keeps the clean build, so a
    stale output can never stand in for a fresh one.

    (A successful production build is never rebuilt incrementally either: with
    nothing to compile Gradle reports every task up to date, the APKs it left
    are older than this build's start, and find_apks rightly refuses stale
    ones -- the run of 2026-09-26 recorded a passing hfut-schedule build as
    "no_apk_found_after_successful_build". After a test-only repair the earlier
    production record is reused instead; see repair_touched_only_tests.)
    """
    try:
        records = json.loads(
            (Path(output_dir) / "build_report.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return False
    target = str(Path(worktree))
    return any(
        isinstance(record, dict) and record.get("status") == "failed"
        and str(record.get("project_dir", "")) == target
        for record in records if isinstance(records, list)
    )


def previous_successful_build_record(output_dir: Path, worktree: Path) -> dict | None:
    """This worktree's last production build record here, when it succeeded."""
    try:
        records = json.loads(
            (Path(output_dir) / "build_report.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    target = str(Path(worktree))
    for record in records if isinstance(records, list) else []:
        if (isinstance(record, dict) and record.get("status") in PASSING_STATUSES
                and str(record.get("project_dir", "")) == target):
            return dict(record)
    return None


def previous_build_succeeded_in_place(output_dir: Path, worktree: Path) -> bool:
    """Whether this worktree's last production build here succeeded."""
    return previous_successful_build_record(output_dir, worktree) is not None


def changed_test_modules(source_paths) -> tuple:
    """Modules ("" for the repository root) whose instrumentation and unit tests changed.

    The leading "/" lets a project built from the repository root match:
    RedReader's "src/test/..." never contained "/src/test/", so the test its
    patch added -- and broke -- was never run (subset_newpr3). Only the
    source-set match ignores case; the module keeps its own, since it names a
    directory on a case-sensitive worktree and a Gradle project. Kotlin
    Multiplatform sets count too: androidInstrumentedTest as instrumentation,
    androidUnitTest, androidHostTest and commonTest as unit tests.
    """
    android, unit = set(), set()
    for module, test_set in _changed_test_sets(source_paths):
        (android if is_instrumented_test_set(test_set) else unit).add(module)
    return android, unit


def _changed_test_sets(source_paths) -> list[tuple[str, str]]:
    """(module, test source set) of every test source among `source_paths`."""
    found = []
    for raw in source_paths:
        path = "/" + str(raw).replace("\\", "/").lstrip("/")
        match = TEST_SOURCE_SET_SEGMENT.search(path)
        if match:
            found.append((path[:match.start()].strip("/"), match.group(1)))
    return found


def changed_unit_test_tasks(worktree: Path, source_paths) -> dict[str, list[str]]:
    """module -> the tasks (before flavor qualification) that run its changed unit tests.

    A module's own task for each set: testDebugUnitTest for src/test and
    androidUnitTest, testAndroidHostTest for a KMP androidHostTest module.
    """
    sets: dict[str, set[str]] = {}
    for module, test_set in _changed_test_sets(source_paths):
        if not is_instrumented_test_set(test_set):
            sets.setdefault(module, set()).add(test_set)
    return {
        module: unit_test_task(Path(worktree) / module, names) or ["testDebugUnitTest"]
        for module, names in sets.items()
    }


def is_test_source(path: str) -> bool:
    """Whether a repository-relative path is in a unit or instrumentation test set."""
    return is_test_source_path("/" + str(path).replace("\\", "/").lstrip("/"))


def repair_touched_only_tests(edits) -> bool:
    """Whether the validation repairs of a solution changed test sources only.

    `assembleDebug` compiles no test source set, so a production build that
    already succeeded is still valid after such a repair; only an incremental
    check is needed, not another twenty-minute clean build.
    """
    repairs = [
        item for item in edits or []
        if isinstance(item, dict) and item.get("edit_kind") == "validation_repair"
    ]
    return bool(repairs) and all(
        is_test_source(str(item.get("source_file") or item.get("source_file_path") or ""))
        for item in repairs
    )


def repaired_finding_ids(directory) -> set[str]:
    """Findings a validation repair succeeded for, from its per-finding records."""
    found: set[str] = set()
    if not directory:
        return found
    for path in sorted(Path(directory).glob("*_validation_fix.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("status") == "success":
            found.add(str(record.get("finding_id", "")))
    return found


FLAVOR_BLOCK_PATTERN = re.compile(r"productFlavors\s*\{")
FLAVOR_DECLARATION_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:(?:create|maybeCreate)\s*\(\s*"
    r"[\"'](?P<created>[A-Za-z_]\w*)[\"']\s*\)|"
    r"(?P<bare>[A-Za-z_]\w*))[ \t]*\{"
)
FLAVOR_DIMENSION_PATTERN = re.compile(
    r"\bdimension\s*(?:=\s*|\(\s*)?[\"']([^\"']+)[\"']"
)
FLAVOR_DIMENSIONS_LINE_PATTERN = re.compile(
    r"(?m)^[ \t]*flavorDimensions\b([^\r\n]*)"
)
QUOTED_VALUE_PATTERN = re.compile(r"[\"']([^\"']+)[\"']")


def _brace_structure(text: str) -> tuple[list[int], dict[int, int]]:
    """Return lexical brace depth and matching pairs for Gradle source.

    Gradle files commonly contain closures nested inside each product flavor.
    A plain multiline regex mistakes those closures for additional flavors.
    This small scanner ignores braces inside strings and comments, which is
    sufficient to identify only declarations directly inside productFlavors.
    """
    depths = [0] * (len(text) + 1)
    stack: list[int] = []
    pairs: dict[int, int] = {}
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = 0
    while index < len(text):
        depths[index] = len(stack)
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                depths[index + 1] = len(stack)
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and following == "/":
            depths[index + 1] = len(stack)
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            depths[index + 1] = len(stack)
            block_comment = True
            index += 2
            continue
        if char in "\"'":
            quote = char
        elif char == "{":
            stack.append(index)
        elif char == "}" and stack:
            pairs[stack.pop()] = index
        index += 1
    depths[len(text)] = len(stack)
    return depths, pairs


def _product_flavor_entries(text: str) -> list[tuple[str, str | None]]:
    """Return top-level product flavors and their declared dimensions."""
    depths, pairs = _brace_structure(text)
    block_match = FLAVOR_BLOCK_PATTERN.search(text)
    if not block_match:
        return []
    opening = block_match.end() - 1
    closing = pairs.get(opening)
    if closing is None:
        return []
    block = text[opening + 1:closing]
    block_depths, block_pairs = _brace_structure(block)
    entries: list[tuple[str, str | None]] = []
    for match in FLAVOR_DECLARATION_PATTERN.finditer(block):
        if block_depths[match.start()] != 0:
            continue
        name = match.group("created") or match.group("bare")
        flavor_opening = match.end() - 1
        flavor_closing = block_pairs.get(flavor_opening)
        if flavor_closing is None:
            continue
        body = block[flavor_opening + 1:flavor_closing]
        dimension_match = FLAVOR_DIMENSION_PATTERN.search(body)
        dimension = dimension_match.group(1) if dimension_match else None
        entries.append((name, dimension))
    return entries


def _declared_flavor_dimension_order(text: str) -> list[str]:
    dimensions: list[str] = []
    for match in FLAVOR_DIMENSIONS_LINE_PATTERN.finditer(text):
        for dimension in QUOTED_VALUE_PATTERN.findall(match.group(1)):
            if dimension not in dimensions:
                dimensions.append(dimension)
    return dimensions


def _selected_product_flavors(module_dir: Path) -> list[str]:
    """Choose one deterministic flavor from each declared dimension."""
    for name in ("build.gradle", "build.gradle.kts"):
        build_file = module_dir / name
        if not build_file.is_file():
            continue
        try:
            text = build_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        entries = _product_flavor_entries(text)
        if not entries:
            continue
        dimension_order = _declared_flavor_dimension_order(text)
        for _, dimension in entries:
            if dimension and dimension not in dimension_order:
                dimension_order.append(dimension)
        if len(dimension_order) == 1:
            # Gradle assigns a flavor that names no dimension to the only
            # declared one. NetworkSurvey declares flavorDimensions
            # ["version"] and flavors regular and cdr with no dimension line;
            # treating them as dimensionless left "version" empty, the task
            # stayed the ambiguous assembleDebugAndroidTest, and Gradle
            # rejected it before compiling a single test.
            entries = [
                (flavor, declared or dimension_order[0])
                for flavor, declared in entries
            ]
        if not dimension_order:
            return [sorted({flavor for flavor, _ in entries})[0]]
        by_dimension = {
            dimension: sorted({
                flavor for flavor, declared_dimension in entries
                if declared_dimension == dimension
            })
            for dimension in dimension_order
        }
        if any(not flavors for flavors in by_dimension.values()):
            # An incomplete dimension model is not enough evidence to invent
            # a Gradle variant. Falling back to the unqualified task produces
            # a clear Gradle diagnostic instead of silently selecting a wrong
            # combination.
            return []
        return [by_dimension[dimension][0] for dimension in dimension_order]
    return []


def _flavor_qualified_task(module_dir: Path, task: str) -> str:
    """Qualify assembleDebugAndroidTest/testDebugUnitTest with a flavor.

    Picking the alphabetically first flavor in each declared dimension is
    deterministic and needs no project-specific configuration. For a module
    with license=(foss, full) and release=(beta, stable), for example, the
    complete prefix is FossBeta rather than the ambiguous Beta.
    """
    flavors = _selected_product_flavors(module_dir)
    if not flavors:
        return task
    flavor_prefix = "".join(flavor[0].upper() + flavor[1:] for flavor in flavors)
    return re.sub(
        r"^(assemble|test|compile)Debug", rf"\1{flavor_prefix}Debug", task,
    )


# applicationId as well: catalog aliases name the plugin freely, and only an
# application module declares an applicationId.
_APPLICATION_PLUGIN = re.compile(
    r"""com\.android\.application|plugins\.android\.application\b"""
    r"""|\bapplicationId\s*[=( ]"""
)
_SETTINGS_FILES = ("settings.gradle", "settings.gradle.kts")
_SKIPPED_DIRECTORIES = {"build", ".gradle", ".git", "node_modules"}


def _gradle_settings_root(worktree: Path) -> Path | None:
    """The directory holding the settings file, at most two levels down."""
    if any((worktree / name).is_file() for name in _SETTINGS_FILES):
        return worktree
    found = [
        path.parent for depth in ("*", "*/*") for name in _SETTINGS_FILES
        for path in worktree.glob(f"{depth}/{name}")
        if not _SKIPPED_DIRECTORIES & set(path.relative_to(worktree).parts)
    ]
    return found[0] if len(set(found)) == 1 else None


def invoked_gradle_root(worktree: Path) -> Path:
    """The directory the build step runs Gradle from (apk_build's own rule).

    Task paths must be relative to it, not to the repository: BWT's settings
    file sits in comencryptbwt/, and ':comencryptbwt:app:assembleDebugAndroidTest'
    was refused as "project 'comencryptbwt' not found in root project"
    (rvsec_bwt_20261024), so the patch's test never compiled.
    """
    # Imported here: the repair and PR steps import this module and need
    # none of the build step.
    from cognalys.builds.apk_build import find_gradle_root

    try:
        return find_gradle_root(worktree) or worktree
    except OSError:
        return worktree


def module_test_task(worktree: Path, gradle_root: Path, module: str,
                     task: str) -> str | None:
    """A module's test task in Gradle's terms; None when the module is outside the root."""
    try:
        relative = (worktree / module).resolve().relative_to(gradle_root.resolve())
    except ValueError:
        return None
    qualified = _flavor_qualified_task(worktree / module, task)
    parts = [part for part in relative.parts if part]
    return f":{':'.join(parts)}:{qualified}" if parts else qualified


def _application_modules(root: Path) -> list[Path]:
    """Module directories that apply the Android application plugin."""
    modules = []
    build_files = []
    # A bounded walk that never enters build outputs or dependency trees: a
    # recursive glob over a checkout on a mounted drive took minutes.
    for current, directories, files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        directories[:] = [
            name for name in directories
            if name not in _SKIPPED_DIRECTORIES and not name.startswith(".")
            and depth < 3
        ]
        if depth == 0:
            continue
        build_files.extend(
            Path(current) / name for name in ("build.gradle", "build.gradle.kts")
            if name in files
        )
    for build_file in build_files:
        try:
            text = build_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(
            _APPLICATION_PLUGIN.search(line) and "apply false" not in line
            for line in text.splitlines()
        ):
            modules.append(build_file.parent)
    return sorted(set(modules))


def _module_of(root: Path, path: Path) -> Path | None:
    """The nearest directory above `path` (inside root) with a build file."""
    current = path.parent
    while current != root and root in current.parents:
        if any((current / name).is_file() for name in ("build.gradle", "build.gradle.kts")):
            return current
        current = current.parent
    return None


def production_build_tasks(worktree: Path, variant: str,
                           edited_paths: list[str] | None = None) -> list[str] | None:
    """One flavor's assemble task, when the project has flavors; else None.

    A plain assembleDebug builds every product flavor of every module.
    myexpenses' extern and intern flavors each compile the whole Kotlin app,
    and its patched build was still in the second one when the 1800-second
    limit ended run_20261006_223750's validation. Building the single
    application module with the flavors _selected_product_flavors already
    picks for the test tasks compiles everything the patch can reach -- the
    library modules it depends on included -- once. Anything less certain
    (no settings file, several application modules, no flavors) keeps the
    plain assemble task.
    """
    root = _gradle_settings_root(worktree)
    if root is None:
        return None
    modules = _application_modules(root)
    if len(modules) != 1:
        return None
    module = modules[0]
    flavors = _selected_product_flavors(module)
    if not flavors:
        return None
    # An edit in another module is compiled only if the application module
    # depends on it; when that cannot be read from its build file, build
    # everything as before.
    try:
        app_build = "\n".join(
            (module / name).read_text(encoding="utf-8", errors="replace")
            for name in ("build.gradle", "build.gradle.kts")
            if (module / name).is_file()
        )
    except OSError:
        return None
    for relative in edited_paths or []:
        edited = (worktree / relative).resolve()
        if module.resolve() in edited.parents:
            continue
        owner = _module_of(root.resolve(), edited)
        if owner is None:
            return None
        gradle_path = ":".join(owner.relative_to(root.resolve()).parts)
        accessor = ".".join(
            re.sub(r"[-_](\w)", lambda m: m.group(1).upper(), part)
            for part in owner.relative_to(root.resolve()).parts
        )
        if not (re.search(rf"""project\(\s*["']:{re.escape(gradle_path)}["']""", app_build)
                or re.search(rf"\bprojects\.{re.escape(accessor)}\b", app_build)):
            return None
    build_type = "Release" if variant == "release" else "Debug"
    prefix = "".join(flavor[0].upper() + flavor[1:] for flavor in flavors)
    path = ":".join(module.relative_to(root).parts)
    return [f":{path}:assemble{prefix}{build_type}"]


_AMBIGUOUS_TASK_PATTERN = re.compile(
    r"Cannot locate tasks? that match '([^']+)' as task '[^']+' is ambiguous "
    r"in project '[^']*'\. Candidates are: ([^\n]+)"
)
_MISSING_TASK_PATTERN = re.compile(
    r"Cannot locate tasks? that match '([^']+)' as task '[^']+' not found "
    r"in project"
)
_FLAVORED_TASK_PATTERN = re.compile(
    r"^(assemble|test|compile)[A-Z][A-Za-z0-9]*?(Debug|Release)"
)
_MISSING_PROJECT_PATTERN = re.compile(
    r"Cannot locate tasks? that match '([^']+)' as project '[^']+' not found "
    r"in root project"
)


def _task_rebased_on_root(requested: str, gradle_root: Path | None) -> str | None:
    """`requested` without its leading path segments that are no Gradle project.

    Segments are dropped only while what remains does not name a directory
    under the root Gradle runs from, so a project that is merely misspelled
    is never turned into a different one.
    """
    if gradle_root is None:
        return None
    segments = [part for part in requested.split(":") if part]
    if len(segments) < 2:
        return None
    projects, name = segments[:-1], segments[-1]
    for start in range(1, len(projects)):
        remainder = projects[start:]
        if (gradle_root / Path(*remainder)).is_dir():
            return f":{':'.join(remainder)}:{name}"
    return None


def tasks_after_selection_failure(log_file: Path,
                                  tasks: list[str],
                                  gradle_root: Path | None = None) -> list[str] | None:
    """The tasks to ask for instead, from Gradle's own refusal; None if unknown.

    The flavor a task is qualified with is read statically from the module's
    build file, and that reading is wrong whenever flavors are declared
    somewhere it does not look -- a convention plugin, a conditional block.
    BetterUntis' app declares foss and gms, the static reading found none,
    and :app:testDebugUnitTest was refused as ambiguous; FairEmail's
    :app:assembleAmazonDebug was refused as not found. Neither build compiled
    anything, so neither said anything about the patch.

    Gradle names the valid tasks in the ambiguity message, and the first in
    sorted order is chosen -- the same deterministic rule the static flavor
    selection applies. A task that does not exist loses its flavor qualifier
    instead; if the unqualified name is then ambiguous, the next failure
    names the candidates. A task whose leading project is not found in the
    root project was named from the repository rather than from the Gradle
    root (BWT's ':comencryptbwt:app:...'); it is rebased on that root.
    """
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    replacements: dict[str, str] = {}
    for match in _MISSING_PROJECT_PATTERN.finditer(text):
        rebased = _task_rebased_on_root(match.group(1), gradle_root)
        if rebased:
            replacements[match.group(1)] = rebased
    for match in _AMBIGUOUS_TASK_PATTERN.finditer(text):
        requested = match.group(1)
        candidates = sorted(re.findall(r"'([^']+)'", match.group(2)))
        if candidates:
            project, separator, _ = requested.rpartition(":")
            replacements[requested] = f"{project}{separator}{candidates[0]}"
    for match in _MISSING_TASK_PATTERN.finditer(text):
        requested = match.group(1)
        project, separator, name = requested.rpartition(":")
        unflavored = _FLAVORED_TASK_PATTERN.sub(r"\1\2", name)
        if unflavored != name:
            replacements[requested] = f"{project}{separator}{unflavored}"
    if not replacements:
        return None
    updated = [replacements.get(task, task) for task in tasks]
    return updated if updated != tasks else None


def run_build_phase_resolving_tasks(
        args: argparse.Namespace, project: Path, output_dir: Path,
        tasks: list[str] | None, **options) -> tuple[dict, list[str] | None]:
    """run_build_phase, retried when Gradle refuses a requested task name.

    Returns the result and the tasks that produced it, so a baseline built
    for comparison asks for the same tasks the patched build ran. Two retries
    cover the longest chain: a flavor that does not exist, then an
    unqualified name that is ambiguous.
    """
    result = run_build_phase(args, project, output_dir, tasks=tasks, **options)
    requested = list(tasks or [])
    for _ in range(2):
        log_file = Path(str(result.get("log_file", "")))
        if (result.get("status") != "failed" or not requested
                or build_failure_phase(log_file) != "task_selection"):
            break
        resolved = tasks_after_selection_failure(
            log_file, requested, invoked_gradle_root(project),
        )
        if not resolved:
            break
        print(f"{project.name}: Gradle refused {', '.join(requested)}; "
              f"retrying with {', '.join(resolved)}", file=sys.stderr)
        retry_options = dict(options, clean_first=False)
        result = run_build_phase(args, project, output_dir, tasks=resolved,
                                 **retry_options)
        result["task_selection_retry"] = {
            "refused": requested, "ran": resolved,
        }
        requested = resolved
    return result, (requested or tasks)


def run_build_phase(args: argparse.Namespace, project: Path, output_dir: Path,
                    tasks: list[str] | None = None,
                    clean_first: bool = False,
                    keep_going: bool = False) -> dict:
    command = [
        *build_invocation(args.build_script),
        "--projects-dir", str(project.parent),
        "--project", project.name,
        "--output-dir", str(output_dir),
        "--variant", args.variant,
        "--timeout", str(args.timeout),
        "--limit", "1",
        "--disable-minification",
    ]
    if tasks:
        command.extend(["--task", tasks[0]])
        for task in tasks[1:]:
            command.extend(["--additional-task", task])
    if clean_first:
        command.append("--clean-first")
    if keep_going:
        # A test build compiles androidTest and unit-test sources in separate
        # tasks. Without --continue Gradle stops at the first that fails, and
        # the repair step sees only its errors: run_20260914_141852 repaired
        # the instrumentation test while the unit test's identical error was
        # never compiled, so a successful repair would have failed again.
        command.append("--continue-after-task-failure")
    if getattr(args, "gradle_build_cache", False):
        command.append("--gradle-build-cache")
    if args.dry_run:
        command.append("--dry-run")
    already_modified = modified_tracked_files(project)
    completed = subprocess.run(command)
    result = _load_build_result(output_dir, project.name, completed.returncode)
    restored = restore_build_side_effects(project, already_modified)
    if restored:
        result["restored_build_side_effects"] = restored
    return result


def modified_tracked_files(project: Path) -> set[str] | None:
    """Tracked files with changes, staged or not; None outside a git checkout."""
    names: set[str] = set()
    for extra in ([], ["--cached"]):
        completed = subprocess.run(
            ["git", "-C", str(project), "diff", "--name-only", *extra],
            capture_output=True, text=True,
        )
        if completed.returncode != 0:
            return None
        names.update(line.strip() for line in completed.stdout.splitlines() if line.strip())
    return names


def restore_build_side_effects(project: Path, before: set[str] | None) -> list[str]:
    """Undo the build's own edits to tracked files; return what was restored.

    Some builds rewrite tracked files: com.vrem.wifianalyzer's bumps
    version_build and a timestamp in app/build.properties on every build. The
    patch's diff is read from the working tree, so that edit reached the
    recorded pull-request diff although the patch never made it. Only files
    the build itself began modifying are restored; anything already modified
    before the build -- the patch's own edits -- is left alone.
    """
    if before is None:
        return []
    after = modified_tracked_files(project)
    if not after:
        return []
    changed = sorted(after - before)
    if changed:
        subprocess.run(
            # Names are relative to the repository root, which the project
            # directory need not be.
            ["git", "-C", str(project), "checkout", "--",
             *(f":(top){name}" for name in changed)],
            capture_output=True, text=True,
        )
    return changed


_FAILED_TASK_PATTERN = re.compile(r"Execution failed for task '(:[^']+)'")
# A task Gradle refuses before executing it never gets "Execution failed":
# NotallyX's installLocalGitHooks, whose output directory sat under a .git
# that is a file, failed task validation with "Some problems were found with
# the configuration of task" and the "> Task ... FAILED" line alone.
_TASK_FAILED_LINE = re.compile(r"^> Task (:\S+) FAILED\s*$", re.MULTILINE)
_TASK_CONFIGURATION_PROBLEM = re.compile(
    r"Some problems were found with the configuration of task '(:[^']+)'"
)
# Gradle's own account of a failure, one block per failure under --continue.
_WHAT_WENT_WRONG_BLOCK = re.compile(
    r"^\* What went wrong:\s*\n(.*?)(?=^\* (?:Try|Exception is|Get more help):"
    r"|^BUILD FAILED|^FAILURE:|^\d+: Task failed|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Gradle evaluates every build script before it compiles a single source file,
# so a failure carrying one of these markers happened before any generated
# source could participate in the build.
_CONFIGURATION_FAILURE_MARKERS = (
    "A problem occurred evaluating project",
    "A problem occurred configuring project",
    "Could not compile build file",
    "Could not compile settings file",
    "Script compilation error",
    "Could not resolve all files for configuration",
    "Plugin [id:",
)

# The Gradle wrapper failing to install its distribution: Gradle itself never
# started. In run_20260929_104044 a truncated gradle-8.7-bin.zip in the local
# wrapper cache failed edziennik's patched build and its baseline alike, and
# the matching baseline recorded it as the project's own pre-existing failure.
_TOOLCHAIN_FAILURE_MARKERS = (
    "at org.gradle.wrapper.",
    "Could not install Gradle distribution",
)
_WRAPPER_UNZIP_PATTERN = re.compile(r"^Unzipping \S+ to (\S+)\s*$", re.MULTILINE)

# Gradle refusing the task names it was asked to run. Nothing was configured
# for execution and nothing was compiled, so the build says nothing about the
# patch; it says the validation asked for a task that does not exist.
_TASK_SELECTION_FAILURE_MARKERS = (
    "Cannot locate tasks that match",
    "is ambiguous in project",
    "not found in project",
)

# Paths whose content Gradle reads during configuration. Only an edit to one of
# these could make a configuration-phase failure the patch's fault.
_BUILD_FILE_SUFFIXES = (".gradle", ".gradle.kts", ".properties", ".toml")
_BUILD_FILE_NAMES = ("gradle-wrapper.properties", "settings.gradle", "libs.versions.toml")


def build_failure_phase(log_file: Path) -> str:
    """Classify where a Gradle build died.

    One of "toolchain", "task_selection", "configuration", "execution" or
    "unknown". "toolchain" means the Gradle wrapper could not install its
    distribution, so no build script was even read.
    compiler_diagnostics() only recognises javac/kotlinc lines, so a build that
    dies while evaluating build scripts yields no diagnostics at all and used to
    be recorded with an empty failure_attribution -- indistinguishable from a
    patch that broke compilation. A build that dies because a requested task
    does not exist yields none either, and is checked first: its log also
    carries configuration output.
    """
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "unknown"
    if any(marker in text for marker in _TOOLCHAIN_FAILURE_MARKERS):
        return "toolchain"
    if any(marker in text for marker in _TASK_SELECTION_FAILURE_MARKERS):
        return "task_selection"
    if any(marker in text for marker in _CONFIGURATION_FAILURE_MARKERS):
        return "configuration"
    if _FAILED_TASK_PATTERN.search(text):
        return "execution"
    return "unknown"


def wrapper_distribution_dirs(log_file: Path) -> list[str]:
    """Wrapper cache directories the log shows Gradle unzipping into."""
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return list(dict.fromkeys(_WRAPPER_UNZIP_PATTERN.findall(text)))


def discard_broken_wrapper_distributions(log_file: Path) -> list[str]:
    """Delete the wrapper distributions a toolchain failure left half-installed.

    Only a directory shaped like the wrapper's own cache entry,
    <gradle user home>/wrapper/dists/<distribution>/<hash>, is removed:
    Gradle downloads it again on the next build, so nothing is lost. Any
    other path named in the log is left alone. Returns the removed paths.
    """
    removed = []
    for text in wrapper_distribution_dirs(log_file):
        path = Path(text)
        if not (
            path.is_absolute() and path.is_dir() and not path.is_symlink()
            and path.parent.parent.name == "dists"
            and path.parent.parent.parent.name == "wrapper"
        ):
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            removed.append(text)
    return removed


def edits_touch_build_files(
    coordinated_edits: list[dict], changed_files: list[str] | None = None,
) -> list[str]:
    """Repository-relative build files the generated patch actually changed.

    Accepts both edit shapes: generation writes "source_file_path", while the
    persisted execution report renames it to "source_file". Reading only one of
    them silently returns "no build files touched" for every patch, which would
    exonerate exactly the patches that deserve blame, so both are honoured and
    the solution's flat changed_files list is folded in as well.
    """
    candidates = [str(path) for path in (changed_files or []) if str(path).strip()]
    for edit in coordinated_edits or []:
        if not isinstance(edit, dict):
            continue
        for key in ("source_file_path", "source_file"):
            path = str(edit.get(key, "")).strip()
            if path:
                candidates.append(path)
                break

    touched = []
    for path in candidates:
        name = path.rsplit("/", 1)[-1]
        if path.endswith(_BUILD_FILE_SUFFIXES) or name in _BUILD_FILE_NAMES:
            touched.append(path)
    return sorted(set(touched))


def failing_task_module(log_file: Path) -> str | None:
    """Return the Gradle module path of the task that made a build fail.

    None if the log names no single failing task (a configuration-time
    failure, for example) or names one directly in the root project.
    """
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _FAILED_TASK_PATTERN.search(text)
    if not match:
        return None
    module, _, _task_name = match.group(1).rpartition(":")
    return module or None


def failing_task_name(log_file: Path) -> str | None:
    """The simple name of the Gradle task that made a build fail."""
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _FAILED_TASK_PATTERN.search(text)
    if not match:
        return None
    return match.group(1).rpartition(":")[2] or None


def failed_task_paths(log_file: Path) -> set[str]:
    """Every task path the build reports as failed (several under --continue)."""
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {
        *_FAILED_TASK_PATTERN.findall(text),
        *_TASK_FAILED_LINE.findall(text),
        *_TASK_CONFIGURATION_PROBLEM.findall(text),
    }


def failure_reports(log_file: Path, checkouts) -> set[str]:
    """Gradle's "What went wrong" blocks, with the checkout paths taken out.

    Two builds of the same commit in different checkouts (the patched
    worktree, the baseline worktree) print the same failure with different
    paths; with each checkout replaced by one placeholder and whitespace
    collapsed, the same failure reads the same.
    """
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    roots = set()
    for checkout in checkouts:
        if not str(checkout or "").strip():
            continue
        forms = [str(checkout)]
        try:
            forms.append(str(Path(checkout).resolve()))
        except OSError:
            pass
        for form in forms:
            # Only absolute paths: "." or "app" would erase text, not a path.
            if os.path.isabs(form) and len(form.rstrip("/\\")) > 1:
                roots.add(form.rstrip("/\\"))
    reports = set()
    for block in _WHAT_WENT_WRONG_BLOCK.findall(text):
        # Longest first, so a checkout nested in another is replaced whole.
        for root in sorted(roots, key=len, reverse=True):
            block = block.replace(root, "<checkout>")
        normalised = " ".join(block.split())
        if normalised:
            reports.add(normalised)
    return reports


def _declared_product_flavors(module_dir: Path) -> list[str]:
    """Every product flavor a module declares, not only the selected one.

    A flavored module has no plain 'assembleDebugAndroidTest' or
    'testDebugUnitTest' task: Gradle requires a flavor-qualified name (for
    example 'assembleFlossDebugAndroidTest') and refuses the ambiguous
    unqualified one outright rather than picking a default.
    """
    for name in ("build.gradle", "build.gradle.kts"):
        build_file = module_dir / name
        if not build_file.is_file():
            continue
        try:
            text = build_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        entries = _product_flavor_entries(text)
        if entries:
            return sorted({flavor for flavor, _ in entries})
    return []


def unselected_flavor_failure(module_dir: Path, task_name: str) -> str | None:
    """The flavor a failing task builds, when validation does not build it.

    A plain `assembleDebug` fans out to EVERY product flavor, and Gradle
    interleaves their tasks, so one unbuildable flavor aborts the run before
    the flavor validation actually cares about has compiled anything. That is
    not a defect in the patch and no patch can avoid it: hanjoongcho/aaf-
    easydiary's gmsDev flavor needs ten Flaticon SVGs its README tells each
    developer to import by hand, so `:app:processGmsDevDebugResources` fails on
    missing drawables while the foss flavor -- the one _flavor_qualified_task
    selects, and the one the README says needs no extra setup -- builds and
    packages an APK cleanly with the patch applied.

    Gradle composes a variant task name as verb + Flavor + BuildType + suffix,
    so the flavor appears capitalised and ends on a CamelCase boundary. Both
    halves of that matter. Requiring the boundary stops a short flavor matching
    an unrelated word ("res" inside "processAaaDebugResources"), and trying the
    longest flavor first stops one flavor masking another that extends it --
    easydiary declares both "gms" and "gmsDev", and "Gms" alone matches
    processGmsDevDebugResources just as well while naming the wrong flavor.
    """
    if not task_name:
        return None
    selected = {flavor.lower() for flavor in _selected_product_flavors(module_dir)}
    candidates = sorted(
        (
            flavor for flavor in _declared_product_flavors(module_dir)
            if flavor.lower() not in selected
        ),
        key=len, reverse=True,
    )
    for flavor in candidates:
        capitalised = flavor[0].upper() + flavor[1:]
        start = task_name.find(capitalised)
        if start < 0:
            continue
        end = start + len(capitalised)
        if end == len(task_name) or task_name[end].isupper():
            return flavor
    return None


# A test source set's segment. Kept exact: a production package directory
# such as src/testing/ must not be mistaken for one. Kotlin Multiplatform
# sets (androidUnitTest, commonTest, ...) end in "Test", case kept.
_TEST_SOURCE_SET = re.compile(
    r"(?:^|/)src/(?:test|androidTest|testFixtures|(?-i:[a-z][A-Za-z0-9]*Test))/",
    re.IGNORECASE,
)
_GRADLE_BUILD_FILES = ("build.gradle", "build.gradle.kts")


def included_gradle_projects(worktree: Path) -> set[str] | None:
    """The projects the settings file includes (":app" -> "app"), or None if unknown.

    Only literal include lines are read. An include built in code (a loop
    over directories) cannot be counted, so the answer is None, as it is
    without a settings file.
    """
    root = _gradle_settings_root(worktree)
    if root is None:
        return None
    projects: set[str] = set()
    for name in _SETTINGS_FILES:
        try:
            text = (root / name).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        for line in text.splitlines():
            stripped = re.sub(r"//.*$", "", line).strip()
            if not re.match(r"include\b(?!Build)", stripped):
                if re.search(r"\binclude\s*[(\"' ]", stripped):
                    return None
                continue
            names = re.findall(r"[\"']([^\"']+)[\"']", stripped)
            rest = re.sub(r"[\"'][^\"']*[\"']", "", stripped[len("include"):])
            if (not names or re.search(r"[^\s,()]", rest)
                    or any("$" in item for item in names)):
                return None
            projects |= {item.strip(":") for item in names}
    return projects


def patched_modules_are_the_whole_project(worktree: Path, modules) -> bool:
    """Whether the settings file includes exactly the patched modules, nothing else."""
    root = _gradle_settings_root(worktree)
    projects = included_gradle_projects(worktree)
    if root is None or not projects or not modules:
        return False
    named = set()
    for module in modules:
        try:
            relative = (worktree / module).resolve().relative_to(root.resolve())
        except ValueError:
            return False
        named.add(":".join(part for part in relative.parts if part))
    return named == projects


def production_modules_for(worktree: Path, changed_paths) -> set[str]:
    """The Gradle modules whose production sources a patch changed.

    Only the conventional layout was recognised, by splitting each path at
    "/src/main/", so a module that keeps its sources anywhere else yielded no
    module at all and rescue_unrelated_module_failure returned on its first
    guard. nelenkov/wwwjdic keeps its :wwwjdic library sources under
    wwwjdic/src/; its patch changed wwwjdic/src/org/nick/wwwjdic/ocr/
    OcrFormEntity.java, the build failed only in :wwwjdic-app for a missing
    google-services.json, and the patched module was never recompiled on its
    own (run_20260915_175048).

    A module is the nearest directory below the worktree root carrying a
    Gradle build file, which holds for every source-set layout. Test source
    sets are excluded, and a file owned by the root project names no module
    that could be recompiled apart from the build that already failed.
    """
    modules: set[str] = set()
    for raw in changed_paths or ():
        path = str(raw).replace("\\", "/").strip("/")
        if not path or _TEST_SOURCE_SET.search(path):
            continue
        if "/src/main/" in path:
            modules.add(path.split("/src/main/", 1)[0])
            continue
        parts = path.split("/")[:-1]
        while parts:
            directory = worktree.joinpath(*parts)
            if any((directory / name).is_file() for name in _GRADLE_BUILD_FILES):
                modules.add("/".join(parts))
                break
            parts.pop()
    return modules


def rescue_unrelated_module_failure(
    args: argparse.Namespace, worktree: Path, finding_output: Path,
    production: dict, production_modules: set[str],
) -> dict | None:
    """Recheck whether the patched module(s) actually compile.

    A multi-module Android project can fail assembleDebug for reasons that
    have nothing to do with the patch -- most commonly a native/Rust/NDK
    submodule the validation machine cannot build (see
    keepass-rs-android/build-native-libraries.sh in com.ivanovsky.passnotes,
    which needs cargo, cargo-ndk and the Android NDK that are not installed
    here). When the failing Gradle task lives in a module the patch never
    touched, and the log contains no compiler diagnostic anywhere (so
    nothing actually failed to *compile*), it is worth specifically
    reverifying that the module(s) containing the patch still compile in
    isolation.

    This can only ever upgrade a "failed" production build into the
    distinctly-labeled "success_app_module_only" -- it never claims the
    full multi-module build or a packaged APK works, and it is skipped
    entirely unless the failing task is demonstrably outside every module
    the patch changed.
    """
    if production.get("status") not in ("failed", "timeout") or not production_modules:
        return None
    log_file = Path(str(production.get("log_file", "")))
    if compiler_diagnostics(log_file, []):
        return None  # A real compiler error was produced; not our case.
    # A build that ran out of time failed nowhere: it said nothing about the
    # patch. MyExpenses' patch changed one class in the PlayLicensingOrig
    # library, and :myExpenses:assembleExternDebug was still compiling the
    # app's Kotlin when 1800 seconds ran out. Compiling the patched modules
    # alone is the same narrower check as below, and labelled the same way.
    timed_out = production.get("status") == "timeout"
    failing_module = failing_task_module(log_file)
    if failing_module is None and not timed_out:
        return None
    if timed_out:
        failing_module = ""
    changed_module_prefixes = {
        f":{module.strip('/').replace('/', ':')}" for module in production_modules
    }
    unselected_flavor = None
    if any(
        failing_module == prefix or failing_module.startswith(prefix + ":")
        for prefix in changed_module_prefixes
    ):
        # The failure is inside a module the patch touched -- but the patched
        # module has flavors, and this task may belong to one validation never
        # builds. Recompiling the module in its selected flavor is then still
        # a real check of the patch, not a way around a failure it caused.
        unselected_flavor = unselected_flavor_failure(
            worktree / failing_module.strip(":").replace(":", "/"),
            failing_task_name(log_file) or "",
        )
        if unselected_flavor is None:
            return None
    # compileDebugSources is the variant's own compile anchor: it runs javac,
    # kotlinc or both, whichever the module applies. compileDebugKotlin only
    # exists in a module that applies the Kotlin plugin, so a Java-only
    # module such as wwwjdic's :wwwjdic library would have failed the
    # recompile on task selection alone.
    compile_tasks = [
        (
            f":{module.strip('/').replace('/', ':')}:"
            f"{_flavor_qualified_task(worktree / module, 'compileDebugSources')}"
        )
        for module in sorted(production_modules)
    ]
    rescue_build = run_build_phase(
        args, worktree, finding_output / "app_module_compile_only",
        tasks=compile_tasks,
    )
    # run_build_phase shells out to build_apks_no_proguard.py, which is built
    # around assembleDebug-style tasks: it reports "failed" whenever no APK
    # was copied out, even if Gradle's own exit code was 0 (reason
    # "no_apk_found_after_successful_build"). A plain compileDebugSources
    # task never produces an APK by design, so that check does not apply
    # here -- Gradle's return code is the only signal that means anything
    # for this task.
    if rescue_build.get("return_code") != 0:
        return None
    # In a project with one Gradle module the recheck compiled all of it:
    # Salty's draft (2026-10-29) said "the changed module builds", as if
    # another module had gone unbuilt, when only packaging had.
    only_module = patched_modules_are_the_whole_project(worktree, production_modules)
    result = {
        "status": COMPILE_ONLY_PASS if only_module else "success_app_module_only",
        "reason": (
            "full_build_timeout_patched_module_recompiled" if timed_out
            else "unselected_flavor_failure_patched_module_recompiled"
            if unselected_flavor
            else "unrelated_module_failure_only_patched_module_recompiled"
        ),
        "task": rescue_build.get("task", ""),
        "return_code": rescue_build.get("return_code"),
        "log_file": rescue_build.get("log_file", ""),
        "unrelated_module_failure_task": failing_module,
    }
    if unselected_flavor:
        result["unselected_flavor"] = unselected_flavor
    return result


# Everything the later pipeline stages know about a validation comes through
# this record: they read the persisted execution report, never the in-memory
# result. It is an explicit whitelist, so a field computed during validation
# but absent here is silently dropped -- which is exactly how the failing-test
# extraction came to look like it had never run. Its attribution was
# persisted (that key is listed) while the records the attribution was derived
# from were not, so the repair stage still reported nothing to repair.
BUILD_VALIDATION_DIAGNOSTIC_FIELDS = (
    "compiler_diagnostics",
    "baseline_compiler_diagnostics",
    "new_compiler_diagnostics",
    "failing_tests",
    "baseline_failing_tests",
    "new_failing_tests",
)


def build_validation_record(
    result: dict, finding_id: str, worktree: Path,
) -> dict:
    """The validation record that every later stage reads."""
    record = {
        "status": result.get("status", "unknown"),
        "task": result.get("task", ""),
        "return_code": result.get("return_code"),
        "reason": result.get("reason", ""),
        "log_file": result.get("log_file", ""),
        "finding_id": finding_id,
        "worktree_path": str(worktree.resolve()),
        "failure_attribution": result.get("failure_attribution", ""),
        # Named explicitly, or the reviewer reading this record cannot tell
        # which flavor went unbuilt behind a success_app_module_only.
        "unselected_flavor": result.get("unselected_flavor", ""),
    }
    for field in BUILD_VALIDATION_DIAGNOSTIC_FIELDS:
        record[field] = result.get(field, [])
    record["phases"] = result.get("phases", {})
    # Measurements that never change `status`, carried so the report can
    # show them (see replay_source_detector).
    # Absent when they did not run, rather than defaulted to a value that
    # would read as a result.
    if "detector_replay" in result:
        record["detector_replay"] = result["detector_replay"]
    # Which test tasks failed on both sides, which created tests went with
    # them, and which test modules no build reached: what a reviewer of a
    # production-only pass needs to see.
    for field in ("failed_test_tasks", "removed_generated_tests",
                  "test_modules_outside_gradle_root", "unresolved_test_tasks"):
        if field in result:
            record[field] = result[field]
    # Whether the recorded evidence supports calling the patch correct, and
    # which evidence is missing when it does not. A measurement like the two
    # above: it never changes `status`.
    try:
        from cognalys.evaluation.patch_correctness import assess

        record["patch_correctness"] = assess(record, PASSING_STATUSES)
    except Exception as exc:  # noqa: BLE001 -- a measurement must not fail validation
        record["patch_correctness"] = {"verdict": "not_assessed",
                                       "detail": str(exc)[:200]}
    return record


def paths_absent_from_commit(repo: Path, commit: str, paths: list[str]) -> bool:
    """Whether none of `paths` exists at `commit`: files the patch created.

    Read from the commit, not the checkout, so the answer does not depend on
    what the base clone currently has checked out.
    """
    if not paths or not commit or not repo.is_dir():
        return False
    for path in paths:
        completed = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{commit}:{path}"],
            capture_output=True, text=True,
        )
        if completed.returncode == 0:
            return False
    return True


_PATCH_TEST_PATH = re.compile(
    r"(?:^|/)src/(?:test|androidTest|(?-i:[a-z][A-Za-z0-9]*Test))/(?:java|kotlin)/",
    re.IGNORECASE,
)


def remove_patch_created_tests(
    worktree: Path, base_repo: Path, base_commit: str,
    diagnosed_paths: list[str], dry_run: bool,
) -> list[str]:
    """Delete the test files this patch created, when they are all that fails.

    Returns the removed paths, or [] when the condition does not hold --
    which is most of the time, and deliberately so. Three things must be
    true together: every diagnosed file is a test source, every one of them
    is absent from the base commit, and therefore nothing that existed
    before this patch is being removed. Anything else is a repair only the
    model can make. Shared by the repair step and by a test build that fails
    the same way on the base commit.
    """
    if not diagnosed_paths or not base_commit or not base_repo.is_dir():
        return []
    if not all(_PATCH_TEST_PATH.search(path) for path in diagnosed_paths):
        return []
    if not paths_absent_from_commit(base_repo, base_commit, diagnosed_paths):
        return []
    removed = []
    for path in diagnosed_paths:
        target = worktree / path
        if not target.is_file():
            continue
        if not dry_run:
            try:
                target.unlink()
            except OSError:
                continue
        removed.append(path)
    return removed


def _edit_path(item) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("source_file") or item.get("source_file_path") or "").replace("\\", "/")


def record_removed_tests(solution: dict, removed: list[str], assumption: str) -> None:
    """Make the solution describe the diff that is left once created tests are gone.

    The PR text lists its tests from coordinated_edits, so a removed test left
    there (or appended as a removal entry) is announced as "I also added a
    test" for a file the diff no longer has; the validation also rebuilds its
    module's test task from those paths. The required tests become a check
    for the reviewer when no test of the patch is left.

    The summary was written with those tests among the edits, so it is
    marked stale when one of them is dropped: the PR step then rewrites it
    from the edits that are left, or holds the pull request when it has no
    text model (pr_generator.text_problems_of).
    """
    gone = set(removed)
    if not gone:
        return
    edits = solution.get("coordinated_edits") or []
    if any(_edit_path(item) in gone for item in edits):
        solution["summary_stale"] = True
    solution["coordinated_edits"] = [
        item for item in edits
        if _edit_path(item) not in gone
    ]
    solution["changed_files"] = [
        path for path in solution.get("changed_files") or []
        if str(path).replace("\\", "/") not in gone
    ]
    solution["removed_generated_tests"] = sorted(
        gone | set(solution.get("removed_generated_tests") or [])
    )
    required = [str(item) for item in solution.get("required_tests") or []]
    if not any(is_test_source(_edit_path(item))
               for item in solution["coordinated_edits"]):
        solution["required_tests"] = []
    solution.setdefault("review_assumptions", []).append(
        assumption + (" Check by hand: " + "; ".join(required) if required else "")
    )
    solution["manual_review_required"] = True


# run_prs rewords a commit with this message to the pull request's title
# (pr_generator.commit_changes): it reached DSub2000#178 as the only commit.
REPAIR_COMMIT_PREFIX = "Repair validation compilation for "


def commit_repair(worktree: Path, paths: list[str], finding_id: str) -> str:
    # A file the patch created and the repair then deleted was never tracked
    # and is gone: naming it fails `git add` with "pathspec did not match"
    # and took the whole step down (Cryptomator, 2026-10-24). Only paths on
    # disk or in the index are staged.
    tracked = set(subprocess.run(
        ["git", "-C", str(worktree), "ls-files", "--", *paths],
        capture_output=True, text=True,
    ).stdout.split())
    paths = [path for path in paths if (worktree / path).exists() or path in tracked]
    # -A so a path the repair deleted is staged as a deletion whether the
    # applicator had committed it or left it untracked.
    if paths:
        subprocess.run(
            ["git", "-C", str(worktree), "add", "-A", "--", *paths], check=True
        )
    if subprocess.run(["git", "-C", str(worktree), "diff", "--cached", "--quiet"]).returncode == 0:
        return subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    # --signoff for the same reason as the PR commit: a repair commit rides
    # into the same pull request, and a DCO check fails the branch when any
    # single commit on it lacks the trailer.
    subprocess.run([
        "git", "-C", str(worktree), "commit", "--signoff", "-m",
        f"{REPAIR_COMMIT_PREFIX}{finding_id}",
    ], check=True)
    completed = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return completed.stdout.strip()


def checkout_matches_base(repo: Path, expected_commit: str) -> bool:
    if not repo.is_dir() or not expected_commit:
        return False
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    return completed.returncode == 0 and completed.stdout.strip() == expected_commit


# The baseline androidTest/unitTest build compiles the unmodified project at a
# fixed base_commit for a fixed task set; its outcome cannot depend on which
# finding or solution revision triggered it. Without caching, every finding
# that touches the same project rebuilds this identical baseline from a clean
# state (often ~10 minutes of Gradle work), and a fresh worktree/solution
# revision each pipeline run (see WorktreeManager.solution_revision) means
# this repeats indefinitely rather than being paid once per (repo, commit,
# task set). Only "success"/"failed" outcomes are cached: those are
# deterministic properties of the checkout; "timeout"/"infrastructure_failure"
# are more likely transient (flaky daemon, disk, network) and are always
# rebuilt.
CACHEABLE_BASELINE_STATUSES = {"success", "failed"}


# The cache stores a computed verdict, not just raw build facts, so it must be
# invalidated whenever anything changes what a baseline build does or how its
# outcome is read. Bump this on both kinds of change.
#
# Version 2 was build_outcome(): before it, a test-only task that legitimately
# produced no APK was recorded as status="failed" with return_code=0 and
# reason="no_apk_found_after_successful_build". A cached entry in that shape
# outlived the fix -- from_cache=true means it is never recomputed -- and made
# a passing baseline look like a failing one.
#
# Version 3 is local.properties provisioning. The build now writes the
# gitignored local.properties an Android project expects (an SDK path, and a
# throwaway keystore for scripts that read a signing config out of it), so a
# project that previously could not configure at all now builds. Every
# baseline cached before that recorded a configuration failure for a cause
# that no longer exists: run_20260908_203221 compared a patched build that
# reached resource linking against a baseline from 16:38 that had died on
# "local.properties (No such file or directory)". The verdict happened to
# still be right, but only by luck -- a stale "success" would have blamed the
# patch for an environmental failure instead.
#
# Version 4 is --continue on test builds. A baseline built without it stopped
# at the first failing test task, so its diagnostics covered fewer tasks than
# the patched build they are compared against.
#
# Version 5 is the .git shim on every build (builds.git_directory). A worktree
# baseline cached before it failed wherever the build scripts read or write
# under .git/ -- NotallyX's installLocalGitHooks in its unit-test build -- a
# failure the patched build, built with the shim, no longer has.
BASELINE_VERDICT_LOGIC_VERSION = 5


def baseline_cache_key(base_repo: Path, base_commit: str, tasks: list[str],
                       toolchain: str | None = None) -> str:
    # The toolchain is part of the key: a baseline built under another JDK,
    # SDK or set of declared dependency versions is not the baseline of this
    # build, and reusing it compares two different environments as if they
    # were one (see validation.behavioral.toolchain_fingerprint).
    if toolchain is None:
        toolchain = fingerprint_digest(toolchain_fingerprint(base_repo))
    payload = json.dumps(
        {
            "repo": str(base_repo.resolve()),
            "commit": base_commit,
            "tasks": sorted(tasks),
            "verdict_logic": BASELINE_VERDICT_LOGIC_VERSION,
            "toolchain": toolchain,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def replay_source_detector(solution: dict, worktree: Path) -> dict:
    """The reporting detector, re-run on the file before and after the patch.

    Measured only: the outcome is recorded and never changes a status. A
    finding from a tool no adapter re-runs is recorded as uncovered
    (detector_replay.ADAPTERS), rather than reading as "the alert went
    away".
    """
    tool = str(solution.get("tool", "") or "").strip().lower()
    relative = str(solution.get("source_file", "")).replace("\\", "/")
    base_repo = Path(str(solution.get("base_repository_path", "") or "."))
    base_commit = str(solution.get("base_commit", "") or "")
    # The corpus records the detector's own rule identifier as the spec
    # (for PMD, scan_fdroid_pmd.normalize_violations).
    rule = str(solution.get("spec", "") or "")
    try:
        from cognalys.validation.detector_replay import (
            adapter_for_finding, file_at_revision, no_coverage,
        )
        import tempfile

        adapter = adapter_for_finding(tool, rule)
        if adapter is None:
            return no_coverage(tool)
        detector = adapter.describe()
        # The reporting detector's own message: a replayed rule still firing
        # on a value it accepts is a rule mismatch, not an unchanged alert.
        options = {"reporter_message": str(solution.get("error_message", "") or "")}
        # The renames the patch's own validation accepted: the authority for
        # method_renamed, ahead of the lexical re-resolution.
        renames = {
            str(item.get("old", "")): str(item.get("new", ""))
            for item in (solution.get("semantic_validation") or {}).get("accepted_renames") or []
            if isinstance(item, dict) and item.get("old") and item.get("new")
        }
        if renames:
            options["confirmed_renames"] = renames
        if adapter.tool == "semgrep":
            from cognalys.validation.semgrep_rules import constraint_kind

            # The kind of constraint the finding broke: only the rules that
            # check it can say whether the patch removed it.
            kind = constraint_kind(solution)
            if kind:
                options["constraint_kind"] = kind
        with tempfile.TemporaryDirectory() as directory:
            baseline = file_at_revision(
                base_repo, base_commit, relative,
                Path(directory) / Path(relative).name,
            )
            if baseline is None:
                return {"outcome": "not_exercised", "detector": detector,
                        "detail": "the file does not exist at the base commit"}
            return {
                **adapter.replay_pair(
                    baseline, worktree / relative, rule,
                    str(solution.get("method_name", "")),
                    str(solution.get("csv_class", "")),
                    **options,
                ),
                "detector": detector,
            }
    except Exception as exc:  # noqa: BLE001 -- a measurement must not fail validation
        return {"outcome": "detector_unavailable", "detail": str(exc)[:200]}


def run_baseline_build(args: argparse.Namespace, base_repo: Path, base_commit: str,
                       tasks: list[str], cache_dir: Path) -> tuple[dict, bool]:
    """Build (or reuse a cached build of) the unmodified project's test target.

    Returns `(result, from_cache)`.
    """
    key = baseline_cache_key(base_repo, base_commit, tasks)
    cached_output = cache_dir / key
    cached_report = cached_output / "build_report.json"
    if cached_report.exists():
        cached = _load_build_result(cached_output, base_repo.name, 0)
        if cached.get("status") in CACHEABLE_BASELINE_STATUSES:
            return cached, True
    result = run_build_phase(
        args, base_repo, cached_output, tasks=tasks, clean_first=True,
        keep_going=True,
    )
    return result, False


def run_analyzed_revision_build(
    args: argparse.Namespace, base_repo: Path, base_commit: str,
    patched_worktree: Path, tasks: list[str] | None, output_dir: Path,
    worktree_root: Path | None = None,
) -> dict | None:
    """Build the patch applied to the revision the finding was analysed at.

    Only meaningful when the base commit cannot be built without the patch.
    The clone the analysis read is checked out at that revision; the patch is
    the patched worktree's difference from the base commit, applied with a
    three-way merge. Returns None when there is no distinct analysed revision,
    and a result with status "not_applicable" when the patch does not apply
    there -- neither is a claim about the patch.
    """
    def git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(repo), *arguments],
                              check=check, capture_output=True, text=True)

    try:
        analyzed = git(base_repo, "rev-parse", "HEAD").stdout.strip()
        base = git(base_repo, "rev-parse", f"{base_commit}^{{commit}}").stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    if not analyzed or analyzed == base:
        return None
    try:
        untracked = git(patched_worktree, "ls-files", "--others",
                        "--exclude-standard").stdout.split()
        if untracked:
            # Recorded with intent-to-add so `git diff` shows new files; the
            # commit made later adds them anyway.
            git(patched_worktree, "add", "--intent-to-add", "--", *untracked)
        patch = git(patched_worktree, "diff", "--binary", base).stdout
    except (subprocess.CalledProcessError, OSError):
        return None
    if not patch.strip():
        return None
    # Next to the clone, like the baseline worktrees: a worktree on another
    # file system (the run directory under /mnt/c in WSL) reads every file as
    # modified, and `git apply` then refuses them all (MetadataRemover,
    # exp_final_20261019 subset_newpr).
    worktree = (Path(worktree_root) if worktree_root else output_dir) / (
        f"analyzed_{analyzed[:12]}_{patched_worktree.name}")
    try:
        if worktree.exists():
            git(base_repo, "worktree", "remove", "--force", str(worktree), check=False)
        output_dir.mkdir(parents=True, exist_ok=True)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        git(base_repo, "worktree", "add", "--detach", str(worktree), analyzed)
        # A fresh worktree's index can carry stale stat data, and
        # `apply --3way` then refuses every file with "does not match index"
        # (MetadataRemover, exp_final_20261019 subset_newpr). Refresh it, try
        # a plain apply first and fall back to the three-way merge.
        git(worktree, "update-index", "-q", "--refresh", check=False)
        applied = None
        for mode in ([], ["--3way"]):
            applied = subprocess.run(
                ["git", "-C", str(worktree), "apply", *mode, "--whitespace=nowarn", "-"],
                input=patch, capture_output=True, text=True,
            )
            if applied.returncode == 0:
                break
    except (subprocess.CalledProcessError, OSError) as exc:
        return {"status": "not_applicable", "commit": analyzed,
                "reason": f"analyzed_worktree_unavailable: {str(exc)[:300]}"}
    if applied.returncode != 0:
        return {"status": "not_applicable", "commit": analyzed,
                "reason": f"patch_does_not_apply: {applied.stderr.strip()[:300]}"}
    result = run_build_phase(args, worktree, output_dir / "build", tasks=tasks,
                             clean_first=True)
    return {**result, "commit": analyzed}


def run_worktree_baseline_build(
    args: argparse.Namespace, base_repo: Path, base_commit: str,
    tasks: list[str] | None, cache_dir: Path,
) -> tuple[dict, bool]:
    """Build the unmodified project in a worktree, mirroring the patched layout.

    run_baseline_build() builds the base clone, where `.git` is a directory. A
    finding worktree's `.git` is a regular file, and real projects read paths
    like `.git/HEAD` during configuration (deriving a version or commit hash),
    so the clone can succeed exactly where every worktree fails. Comparing a
    worktree build against a clone baseline would therefore blame the patch for
    a layout difference. This baseline uses a worktree so the only difference
    left between the two builds is the patch itself.

    Returns `(result, from_cache)`; the result carries a "status" of
    "infrastructure_failure" when the baseline worktree cannot be created.
    """
    key = baseline_cache_key(base_repo, base_commit, (tasks or []) + ["@worktree"])
    cached_output = cache_dir / key
    cached_report = cached_output / "build_report.json"
    if cached_report.exists():
        cached = _load_build_result(cached_output, f"baseline_{key}", 0)
        if cached.get("status") in CACHEABLE_BASELINE_STATUSES:
            return cached, True

    worktree = cache_dir / f"{key}_worktree" / f"baseline_{key}"
    try:
        if not worktree.exists():
            worktree.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "-C", str(base_repo), "worktree", "add", "--detach",
                 str(worktree), base_commit],
                check=True, capture_output=True, text=True,
            )
    except (subprocess.CalledProcessError, OSError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        return {
            "status": "infrastructure_failure",
            "reason": f"baseline_worktree_unavailable: {str(detail)[:300]}",
        }, False

    result = run_build_phase(args, worktree, cached_output, tasks=tasks,
                             clean_first=True)
    return result, False


def _gradle_module_of_test(worktree: Path, gradle_root: Path, path: str) -> str | None:
    """The Gradle project path (":common", "" for the root) of a test source."""
    android, unit = changed_test_modules([path])
    modules = android | unit
    if not modules:
        return None
    try:
        relative = (worktree / next(iter(modules))).resolve().relative_to(
            gradle_root.resolve()
        )
    except ValueError:
        return None
    parts = [part for part in relative.parts if part]
    return f":{':'.join(parts)}" if parts else ""


# Tasks that run tests rather than build them: their failure means a test ran
# and failed, which says something about the patch.
_TEST_EXECUTION_TASK = re.compile(
    r"^(?:test\w*|connected\w*AndroidTest|device\w*AndroidTest|allTests"
    r"|\w*(?:Unit|Jvm|Host)Test)$"
)
_TESTS_RAN = re.compile(
    r"There were failing tests|\b\d+ tests? completed, \d+ failed"
)
_TEST_COMPILE_TASK = re.compile(
    r"^> Task (?P<project>\S*):(?P<name>compile\w*Test\w*)"
    r"(?:\s+(?P<outcome>[A-Z][A-Z-]*))?\s*$",
    re.MULTILINE,
)


def _tests_ran(log_text: str, failed_tasks: set[str]) -> bool:
    """Whether the build got as far as running tests and some failed."""
    if any(_TEST_EXECUTION_TASK.match(task.rpartition(":")[2])
           for task in failed_tasks):
        return True
    return bool(_TESTS_RAN.search(log_text)) or any(
        _GRADLE_TEST_FAILURE.match(line.strip()) for line in log_text.splitlines()
    )


def _test_source_compiled(log_text: str, project: str | None, path: str) -> bool:
    """Whether the log shows the task that compiles this test source succeeding.

    Under --continue, Gradle skips a task whose dependency failed in another
    module without reporting it as failed, so a module with no failed task
    has not necessarily compiled its tests.
    """
    if project is None:
        return False
    language = "Kotlin" if path.lower().endswith(".kt") else "Java"
    instrumented = is_instrumented_test_set(test_source_set("/" + path.lstrip("/")))
    outcomes = [
        match.group("outcome") for match in _TEST_COMPILE_TASK.finditer(log_text)
        if match.group("project") == project
        and language in match.group("name")
        and ("AndroidTest" in match.group("name")) == instrumented
    ]
    return bool(outcomes) and all(
        outcome in (None, "UP-TO-DATE", "FROM-CACHE") for outcome in outcomes
    )


def _commit_removed_tests(solution: dict, worktree: Path, removed: list[str],
                          assumption: str, dry_run: bool) -> None:
    """Commit the removal of tests the patch created and record it on the solution."""
    head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    # A created test is usually untracked and its removal leaves nothing to
    # commit; one the applicator committed leaves a deletion that must be.
    if not dry_run and head.returncode == 0:
        committed = commit_repair(
            worktree, removed, str(solution.get("finding_id", worktree.name)),
        )
        if committed and committed != head.stdout.strip():
            solution["commit_hash"] = committed
    record_removed_tests(solution, removed, assumption)


def settle_test_build_failing_on_base(
        solution: dict, worktree: Path, gradle_root: Path, production: dict,
        patched_tests: dict, baseline: dict, base_repo: Path, base_commit: str,
        test_paths: list[str], dry_run: bool) -> dict | None:
    """The result of a test build that failed with no diagnostic; None if unsettled.

    KeyPass's :common:assembleDebugAndroidTest failed at
    checkDebugAndroidTestAarMetadata (androidx.core 1.16.0 needs compileSdk
    35) with and without the patch, before the test the patch created there
    was compiled (rvsec_keypass_20261025). No diagnostic and no failing test
    meant no attribution: the patch was blamed, the repair step had nothing
    to repair, and a production fix that built was lost.

    Settled only when the base commit fails at every task the patched build
    failed at, and no failed task ran tests. Then the test build says nothing
    about the patch. A test of the patch is kept only when the log shows its
    compile task succeeding; if every other test source it touched is one it
    created, those are removed and the production result stands under its
    own status. A test file that existed on base is never removed, and the
    build stays failed, attributed to the base.

    A failure that names no task (a build script that does not evaluate in
    the test configuration) is settled the same way when every one of
    Gradle's "What went wrong" reports of the patched build, checkout paths
    removed, is also a report of the base build.
    """
    if baseline.get("status") != "failed":
        return None
    patched_log = Path(str(patched_tests.get("log_file", "")))
    baseline_log = Path(str(baseline.get("log_file", "")))
    patched_failed = failed_task_paths(patched_log)
    same_reports = []
    if patched_failed:
        if not patched_failed <= failed_task_paths(baseline_log):
            return None
    else:
        patched_reports = failure_reports(
            patched_log,
            [worktree, gradle_root, patched_tests.get("project_dir"),
             patched_tests.get("gradle_root")],
        )
        if not patched_reports or not patched_reports <= failure_reports(
                baseline_log,
                [baseline.get("project_dir"), baseline.get("gradle_root")]):
            return None
        same_reports = sorted(patched_reports)
    try:
        log_text = patched_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # A test that ran and failed is never base debt. failing_test_diagnostics
    # now reads a failure thrown in a platform frame through the test's own
    # source file, but one whose suite has no source in the checkout is still
    # not parsed, and it would look like the base's own failing tests and be
    # removed.
    if _tests_ran(log_text, patched_failed):
        return None
    failing_modules = {task.rpartition(":")[0] for task in patched_failed}
    projects = {path: _gradle_module_of_test(worktree, gradle_root, path)
                for path in test_paths}
    uncompiled = [
        path for path in test_paths
        if projects[path] in failing_modules
        or not _test_source_compiled(log_text, projects[path], path)
    ]
    verdict = {
        "failure_attribution": "verified_preexisting_test_debt",
        "reason": "test_build_fails_on_base_without_patch",
        "failed_test_tasks": sorted(patched_failed),
    }
    if same_reports:
        verdict["failure_reports_shared_with_base"] = same_reports
    removed = remove_patch_created_tests(
        worktree, base_repo, base_commit, uncompiled, dry_run=dry_run,
    )
    if not removed:
        return {**patched_tests, **verdict}
    _commit_removed_tests(
        solution, worktree, removed,
        "The module's test build fails on the base commit without this change "
        f"({', '.join(sorted(patched_failed)) or 'the same Gradle failure'}), before the generated test "
        "compiles, so it could not be compiled here and was removed.",
        dry_run,
    )
    print(
        f"{solution.get('finding_id', worktree.name)}: the test build fails the "
        f"same way on the base commit; removed the patch's own test(s) "
        f"{', '.join(removed)} and kept the production build's result.",
        file=sys.stderr,
    )
    return {
        **production, **verdict,
        "status": PRODUCTION_ONLY_TEST_BUILD_PREEXISTING,
        "removed_generated_tests": removed,
    }


def _test_module_of(path: str) -> str | None:
    android, unit = changed_test_modules([path])
    return next(iter(android | unit), None)


def outside_root_test_paths(source_paths, modules_outside_root: list[str]) -> list[str]:
    """The patch's test sources that sit in a module outside the Gradle root."""
    outside = set(modules_outside_root)
    return sorted(
        path for path in source_paths
        if is_test_source(path) and _test_module_of(path) in outside
    )


def settle_tests_outside_gradle_root(
        solution: dict, worktree: Path, gradle_root: Path, result: dict,
        test_paths: list[str], dry_run: bool) -> dict:
    """A passing result whose patch has tests that no Gradle build compiles.

    A test in a directory outside the root Gradle runs from gets no task, so
    it was never compiled; left alone it shipped under the production
    build's "success" (a repository-level src/test/ next to BWT's
    comencryptbwt/ settings is the shape). Tests the patch created there are
    removed, as when they fail to compile; a test file that existed on base,
    or a settings file that may relocate projects (projectDir), keeps the
    result failed.
    """
    if not test_paths:
        return result
    base_repo_text = str(solution.get("base_repository_path", "")).strip()
    base_repo = Path(base_repo_text) if base_repo_text else Path()
    base_commit = str(solution.get("base_commit", "")).strip()
    settings_text = "".join(
        (gradle_root / name).read_text(encoding="utf-8", errors="replace")
        for name in _SETTINGS_FILES if (gradle_root / name).is_file()
    )
    removed = []
    if base_repo_text and not re.search(r"\bprojectDir\b", settings_text):
        removed = remove_patch_created_tests(
            worktree, base_repo, base_commit, test_paths, dry_run=dry_run,
        )
    if not removed:
        return {
            **result,
            "status": "failed",
            "reason": "validation_test_module_outside_gradle_root",
            "failure_attribution": "validation_task_unresolved",
            "unresolved_test_tasks": test_paths,
        }
    _commit_removed_tests(
        solution, worktree, removed,
        "The generated test sat outside the directory Gradle builds the project "
        "from, so no build compiled it, and it was removed.",
        dry_run,
    )
    print(
        f"{solution.get('finding_id', worktree.name)}: removed the patch's own "
        f"test(s) {', '.join(removed)}, which no Gradle build of the project "
        "compiles.",
        file=sys.stderr,
    )
    return {**result, "removed_generated_tests": sorted(
        set(removed) | set(result.get("removed_generated_tests") or []))}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile patched projects and enrich execution_report.json."
    )
    parser.add_argument(
        "--build-script", type=Path, default=None,
        help="Program that builds the APKs; the package's own build stage by default.",
    )
    parser.add_argument("--projects-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execution-report", type=Path, required=True)
    parser.add_argument("--variant", choices=("debug", "release"), default="debug")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--project", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fail-on-validation-error", action="store_true",
        help="Return non-zero when any patched build remains non-passing.",
    )
    parser.add_argument(
        "--publish-without-build-validation", action="append", default=[],
        metavar="FINDING_ID",
        help=(
            "Do not abort the run when this finding's build fails for an "
            "infrastructure reason: the operator has already approved "
            "publishing it without compile validation, so the failure is "
            "expected. Has no effect on a build that ran and failed. "
            "Can be repeated."
        ),
    )
    parser.add_argument(
        "--baseline-cache-dir", type=Path, default=None,
        help=(
            "Directory for reusing baseline (unmodified-project) test builds "
            "keyed by repository+commit+task set+toolchain, shared across "
            "findings and runs. Defaults to a 'baseline_build_cache' "
            "directory next to --projects-dir."
        ),
    )
    parser.add_argument(
        "--revalidate-repaired-in", type=Path, default=None, metavar="DIR",
        help=(
            "Directory of *_validation_fix.json records. Only findings a "
            "repair succeeded for, and findings that did not pass, are built "
            "again; a finding that passed and was not repaired keeps its "
            "recorded result."
        ),
    )
    parser.add_argument(
        "--gradle-build-cache", action="store_true",
        help=(
            "Pass --build-cache to every Gradle build of this validation, so "
            "the patched, baseline and repair builds share task outputs. Off "
            "by default."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.execution_report.exists():
        print(
            f"Execution report not found: {args.execution_report}",
            file=sys.stderr,
        )
        return 2

    execution_report = json.loads(
        args.execution_report.read_text(encoding="utf-8")
    )
    baseline_cache_dir = (
        args.baseline_cache_dir
        or args.projects_dir.resolve().parent / "baseline_build_cache"
    )
    solutions = [
        item for item in execution_report.get("per_solution_details", [])
        if item.get("status") == "SUCCESS"
        and (not args.project or item.get("apk_name") == args.project)
    ]
    if args.limit > 0:
        solutions = solutions[:args.limit]
    # A revalidation exists to check what a repair changed. A finding that
    # already passed and was not repaired has nothing new to check: its
    # recorded result stands. In the last run this rebuilt comfychair, which
    # had passed, for six minutes, next to the finding that needed it.
    repaired_ids = (
        repaired_finding_ids(args.revalidate_repaired_in)
        if getattr(args, "revalidate_repaired_in", None) else None
    )
    if repaired_ids is not None:
        pending = []
        for item in solutions:
            recorded = (item.get("build_validation") or {}).get("status")
            if (recorded in PASSING_STATUSES
                    and str(item.get("finding_id", "")) not in repaired_ids):
                print(
                    f"Not revalidating {item.get('finding_id', '')}: it passed "
                    "and no repair changed it.", flush=True,
                )
                continue
            pending.append(item)
        solutions = pending
    build_results = []
    enriched = 0
    infrastructure_failures = 0
    approved_infrastructure_failures = 0
    publish_without_validation = {
        str(value).strip()
        for value in (getattr(args, "publish_without_build_validation", None)
                      or [])
        if str(value).strip()
    }
    print(
        f"Running isolated compilation validation for {len(solutions)} finding(s)",
        flush=True,
    )
    for solution in solutions:
        worktree_text = str(solution.get("worktree_path", "")).strip()
        if not worktree_text:
            solution["build_validation"] = {
                "status": "not_run",
                "reason": "finding_worktree_missing",
            }
            continue
        worktree = Path(worktree_text)
        finding_id = str(solution.get("finding_id", worktree.name))
        finding_output = args.output_dir / finding_id
        # A single-method fix has no coordinated_edits at all -- it records the
        # one file it changed in source_file. Reading only coordinated_edits
        # left changed_paths empty for every such solution, so
        # production_modules was empty too and rescue_unrelated_module_failure
        # returned immediately: the whole rescue path, both the unrelated-module
        # branch and the unselected-flavor one, was dead for single-method
        # fixes. run_20260908_203221 is the case -- easydiary's Zip Slip fix
        # built its foss APK (apk_count=1) and was still recorded as failed,
        # because the rescue that would have recognised the gmsDev-only failure
        # could not run.
        # Case is kept for locating modules on disk and naming Gradle
        # projects; the test-source-set checks below compare lowercased paths.
        source_paths = {
            str(item.get("source_file", "")).replace("\\", "/")
            for item in (solution.get("coordinated_edits") or [])
            if isinstance(item, dict)
        }
        primary_source = str(solution.get("source_file", "")).replace("\\", "/")
        if primary_source:
            source_paths.add(primary_source)
        android_test_modules, unit_test_modules = changed_test_modules(source_paths)
        gradle_root = invoked_gradle_root(worktree)
        test_tasks = []
        test_modules_outside_root = []
        unit_tasks = changed_unit_test_tasks(worktree, source_paths)
        for modules, tasks_of in (
                (android_test_modules, lambda _module: ["assembleDebugAndroidTest"]),
                (unit_test_modules,
                 lambda module: unit_tasks.get(module, ["testDebugUnitTest"]))):
            for module in sorted(modules):
                for task in tasks_of(module):
                    gradle_task = module_test_task(worktree, gradle_root, module, task)
                    if gradle_task is None:
                        test_modules_outside_root.append(module)
                    else:
                        test_tasks.append(gradle_task)
        outside_root_tests = outside_root_test_paths(source_paths, test_modules_outside_root)

        # A project whose build scripts read or write under .git/ gets a real
        # directory there for every build of this loop -- production, tests,
        # baselines, the analysed revision -- from the build step itself
        # (builds.git_directory); each result records it as
        # "git_directory_shim".
        incremental_after_failure = previous_build_failed_in_place(
            finding_output / "production", worktree,
        )
        # A repair that changed only test sources cannot change what
        # assembleDebug compiles, and that build already succeeded in this
        # worktree: its record stands, with its APKs, and nothing is rebuilt.
        production_tasks = production_build_tasks(
            worktree, args.variant,
            [path for path in source_paths if not is_test_source(path)],
        )
        reused = (
            None if incremental_after_failure
            or not repair_touched_only_tests(solution.get("coordinated_edits"))
            else previous_successful_build_record(finding_output / "production", worktree)
        )
        if reused is not None:
            production = {**reused, "reused_after_test_only_repair": True}
        else:
            production, production_tasks = run_build_phase_resolving_tasks(
                args, worktree, finding_output / "production",
                production_tasks,
                clean_first=not incremental_after_failure,
            )
            if incremental_after_failure:
                production["incremental_after_failed_build"] = True
            production_log = Path(str(production.get("log_file", "")))
            if (
                production.get("status") == "failed"
                and build_failure_phase(production_log) == "toolchain"
            ):
                # A truncated download in the wrapper cache fails every build
                # that uses that Gradle version until it is removed, so it is
                # removed and the build retried once.
                removed = discard_broken_wrapper_distributions(production_log)
                if removed:
                    print(
                        f"{finding_id}: removed unusable Gradle wrapper "
                        f"distribution(s) {', '.join(removed)}; rebuilding.",
                        file=sys.stderr,
                    )
                    production = run_build_phase(
                        args, worktree, finding_output / "production",
                        tasks=production_tasks, clean_first=True,
                    )
                    production["wrapper_distributions_reinstalled"] = removed
        phases = {"production": production}
        production_modules = production_modules_for(worktree, source_paths)
        rescue = rescue_unrelated_module_failure(
            args, worktree, finding_output, production, production_modules,
        )
        if rescue:
            phases["app_module_compile_only"] = rescue
            production = {**production, **rescue}
        result = dict(production)
        if production.get("status") == "failed":
            # A production build that fails without a single compiler
            # diagnostic used to be recorded with an empty failure_attribution,
            # so an environmental failure looked exactly like a patch that
            # broke compilation. Classify the phase, then confirm empirically
            # against an unpatched build of the same commit in the same layout.
            production_log = Path(str(production.get("log_file", "")))
            phase = build_failure_phase(production_log)
            touched_build_files = edits_touch_build_files(
                solution.get("coordinated_edits", []),
                solution.get("changed_files", []),
            )
            result["build_failure_phase"] = phase
            if phase == "toolchain":
                # The wrapper failed before reading any build script. A
                # baseline would reuse the same broken wrapper cache and
                # "reproduce" the failure, so it is not run. Only a patch
                # that edited the wrapper's own properties can be blamed.
                wrapper_edits = [
                    path for path in touched_build_files
                    if path.endswith("gradle-wrapper.properties")
                ]
                if wrapper_edits:
                    result["failure_attribution"] = "generated_patch"
                    result["build_files_changed_by_patch"] = wrapper_edits
                else:
                    result["reason"] = "gradle_distribution_unusable"
                    result["failure_attribution"] = "build_environment"
                    result["environment_remedy"] = {
                        "distribution_dirs": wrapper_distribution_dirs(
                            production_log
                        ),
                        "action": (
                            "Delete these wrapper distribution directories "
                            "so Gradle downloads them again, then rerun "
                            "the validation."
                        ),
                    }
            elif not compiler_diagnostics(
                production_log, solution.get("coordinated_edits", []),
            ):
                base_repo_text = str(
                    solution.get("base_repository_path", "")
                ).strip()
                base_repo = Path(base_repo_text) if base_repo_text else Path()
                base_commit = str(solution.get("base_commit", "")).strip()
                baseline = None
                if base_repo_text and base_commit and base_repo.is_dir():
                    # The same task the patched build ran: passnotes' patched
                    # :app:assembleAutomationDebug was compared with a plain
                    # assembleDebug baseline in run_20261009_012517, so the
                    # two builds did not compile the same thing.
                    baseline, from_cache = run_worktree_baseline_build(
                        args, base_repo, base_commit, production_tasks,
                        baseline_cache_dir,
                    )
                    baseline = dict(baseline, from_cache=from_cache)
                    phases["baseline_production"] = baseline

                if baseline and baseline.get("status") == "failed":
                    # The unmodified commit fails the same way in the same
                    # layout, so the patch cannot be responsible.
                    result["reason"] = "production_failure_reproduces_on_base_commit"
                    result["failure_attribution"] = (
                        "verified_preexisting_build_failure"
                    )
                    # That says nothing about the patch either way. The
                    # revision the finding was analysed at may still build:
                    # MetadataRemover's development branch pairs the Hilt
                    # Gradle plugin 2.57.1 with the 2.58 compiler and fails in
                    # :app:hiltJavaCompileDebug with or without any change,
                    # while the analysed commit, both at 2.57.1, builds
                    # (exp_final_20261019). The patch compiled there is
                    # evidence about the patch.
                    analyzed = run_analyzed_revision_build(
                        args, base_repo, base_commit, worktree,
                        production_tasks, finding_output / "analyzed_revision",
                        worktree_root=baseline_cache_dir,
                    )
                    if analyzed:
                        phases["analyzed_revision_production"] = analyzed
                        if analyzed.get("status") == "success":
                            result["status"] = ANALYZED_REVISION_PASS
                            result["analyzed_revision"] = analyzed.get("commit", "")
                            result["base_branch_fails_without_patch"] = True
                elif baseline and baseline.get("status") == "success":
                    result["failure_attribution"] = "generated_patch"
                elif phase == "configuration" and not touched_build_files:
                    # No baseline available, but Gradle died before compiling
                    # anything and the patch changed no build file, so the
                    # generated source could not have participated.
                    result["failure_attribution"] = (
                        "preexisting_build_configuration"
                    )
                elif phase == "configuration" and touched_build_files:
                    result["failure_attribution"] = "generated_patch"
                    result["build_files_changed_by_patch"] = touched_build_files
        if production.get("status") == "success" and test_tasks:
            patched_tests, test_tasks = run_build_phase_resolving_tasks(
                args, worktree, finding_output / "patched_tests",
                test_tasks, keep_going=True,
            )
            phases["patched_tests"] = patched_tests
            result = dict(patched_tests)
            if patched_tests.get("status") == "failed":
                patched_diagnostics = compiler_diagnostics(
                    Path(str(patched_tests.get("log_file", ""))),
                    solution.get("coordinated_edits", []),
                )
                # A test that compiles and then fails its assertion yields no
                # compiler diagnostic, so everything below used to see an
                # empty list and conclude nothing: no attribution here, and
                # "no repairable compiler diagnostics" in the repair step.
                patched_test_failures = failing_test_diagnostics(
                    Path(str(patched_tests.get("log_file", ""))), worktree,
                    solution.get("coordinated_edits", []),
                )
                if patched_test_failures:
                    result["failing_tests"] = patched_test_failures
                # Gradle refused the requested test task before compiling
                # anything. The patch's tests were never checked, so this
                # stays a failure that blocks publication, but it is recorded
                # as the validation's own fault: in run_20260914_141852 it
                # was an empty attribution, the repair step found nothing to
                # repair, and the tests' real compile error stayed hidden.
                # The baseline would fail the same way, so it is not built.
                task_unresolved = (
                    not patched_diagnostics and not patched_test_failures
                    and build_failure_phase(
                        Path(str(patched_tests.get("log_file", "")))
                    ) == "task_selection"
                )
                if task_unresolved:
                    result["build_failure_phase"] = "task_selection"
                    result["reason"] = "validation_test_task_unresolved"
                    result["failure_attribution"] = "validation_task_unresolved"
                    result["unresolved_test_tasks"] = test_tasks
                base_repo_text = str(
                    solution.get("base_repository_path", "")
                ).strip()
                base_repo = Path(base_repo_text) if base_repo_text else Path()
                base_commit = str(solution.get("base_commit", "")).strip()
                # Every error is in a file the base commit does not have, so
                # none of them can predate the patch and the baseline cannot
                # change the verdict. run_20260914_141852 rebuilt the whole
                # unpatched project and ran its unit tests only to learn that
                # two test files the patch had created did not exist there.
                # A failing test counts too: a test the patch created cannot
                # have been failing before it.
                diagnosed = [*patched_diagnostics, *patched_test_failures]
                diagnosed_paths = []
                for item in diagnosed:
                    try:
                        diagnosed_paths.append(
                            Path(str(item.get("path", ""))).resolve()
                            .relative_to(worktree.resolve()).as_posix()
                        )
                    except (ValueError, OSError):
                        diagnosed_paths = []
                        break
                only_in_created_files = bool(
                    base_repo_text
                    and diagnosed
                    and len(diagnosed_paths) == len(diagnosed)
                    and paths_absent_from_commit(
                        base_repo, base_commit, sorted(set(diagnosed_paths)),
                    )
                )
                if only_in_created_files:
                    phases["baseline_tests"] = {
                        "status": "skipped",
                        "reason": "diagnostics_only_in_files_the_patch_created",
                    }
                    result["baseline_compiler_diagnostics"] = []
                    result["new_compiler_diagnostics"] = patched_diagnostics
                    if patched_test_failures:
                        result["baseline_failing_tests"] = []
                        result["new_failing_tests"] = patched_test_failures
                    result["failure_attribution"] = "generated_patch"
                if (not task_unresolved and not only_in_created_files
                        and base_repo_text and base_commit
                        and base_repo.is_dir()):
                    # The clone is not always at the base: a pinned revision
                    # (the commit the analysed APK was built from) leaves it
                    # behind the branch tip a worktree starts from, and this
                    # baseline then never ran -- no attribution, so nothing
                    # for validation_fix to repair. A worktree at base_commit
                    # builds the same tree whatever the clone has checked out.
                    if checkout_matches_base(base_repo, base_commit):
                        baseline, from_cache = run_baseline_build(
                            args, base_repo, base_commit, test_tasks,
                            baseline_cache_dir,
                        )
                    else:
                        baseline, from_cache = run_worktree_baseline_build(
                            args, base_repo, base_commit, test_tasks,
                            baseline_cache_dir,
                        )
                    baseline = dict(baseline, from_cache=from_cache)
                    phases["baseline_tests"] = baseline
                    baseline_diagnostics = compiler_diagnostics(
                        Path(str(baseline.get("log_file", ""))), [],
                    )
                    baseline_signatures = {
                        diagnostic_signature(item)
                        for item in baseline_diagnostics
                    }
                    regressions = [
                        item for item in patched_diagnostics
                        if diagnostic_signature(item) not in baseline_signatures
                    ]
                    generated_failures = [
                        item for item in patched_diagnostics
                        if item.get("attribution") in PATCH_CAUSED_ATTRIBUTIONS
                    ]
                    result["baseline_compiler_diagnostics"] = baseline_diagnostics
                    result["new_compiler_diagnostics"] = regressions
                    baseline_test_failures = failing_test_diagnostics(
                        Path(str(baseline.get("log_file", ""))), base_repo, [],
                    )
                    # Compare by test identity, not by path: the two builds
                    # run in different checkouts.
                    already_failing = {
                        (item.get("test_suite"), item.get("test_name"))
                        for item in baseline_test_failures
                    }
                    new_test_failures = [
                        item for item in patched_test_failures
                        if (item.get("test_suite"), item.get("test_name"))
                        not in already_failing
                    ]
                    # A baseline that never built (its worktree could not be
                    # created) says nothing about which tests already failed;
                    # reading its empty list would blame the patch for all.
                    if (patched_test_failures
                            and baseline.get("status") in ("success", "failed")):
                        result["baseline_failing_tests"] = baseline_test_failures
                        result["new_failing_tests"] = new_test_failures
                        result["failure_attribution"] = (
                            "generated_patch" if new_test_failures
                            else "verified_preexisting_test_debt"
                        )
                    if (baseline.get("status") == "failed"
                            and patched_diagnostics
                            and not regressions and not generated_failures):
                        result["status"] = "failed"
                        result["reason"] = (
                            "repairable_test_failures_match_base_commit"
                        )
                        result["failure_attribution"] = (
                            "verified_preexisting_test_debt"
                        )
                        result["compiler_diagnostics"] = patched_diagnostics
                    if not patched_diagnostics and not patched_test_failures:
                        settled = settle_test_build_failing_on_base(
                            solution, worktree, gradle_root, production,
                            patched_tests, baseline, base_repo, base_commit,
                            sorted(path for path in source_paths
                                   if is_test_source(path)
                                   and path not in outside_root_tests),
                            dry_run=bool(args.dry_run),
                        )
                        if settled is not None:
                            result = settled
        if test_modules_outside_root:
            result["test_modules_outside_gradle_root"] = test_modules_outside_root
            if result.get("status") in PASSING_STATUSES:
                result = settle_tests_outside_gradle_root(
                    solution, worktree, gradle_root, result, outside_root_tests,
                    dry_run=bool(args.dry_run),
                )
        result["phases"] = phases
        result["detector_replay"] = replay_source_detector(solution, worktree)
        if any(phase.get("status") == "infrastructure_failure"
               for phase in phases.values()):
            if finding_id in publish_without_validation:
                # The operator already said this repository cannot be built
                # and approved publication anyway, so the failure is the
                # expected outcome rather than a surprise that should abort
                # every remaining stage.
                approved_infrastructure_failures += 1
            else:
                infrastructure_failures += 1
        diagnostics = compiler_diagnostics(
            Path(str(result.get("log_file", ""))),
            solution.get("coordinated_edits", []),
        )
        if diagnostics:
            result.setdefault("compiler_diagnostics", diagnostics)
            attributions = {item["attribution"] for item in diagnostics}
            result.setdefault("failure_attribution", (
                "generated_patch"
                if attributions & PATCH_CAUSED_ATTRIBUTIONS
                else "preexisting_test_or_source_debt"
            ))
        if result.get("status") == "timeout":
            # A compiler error printed before the limit was already
            # attributed above; otherwise the build simply did not finish.
            result.setdefault("failure_attribution", "build_timeout")
        result["finding_id"] = finding_id
        result["apk_name"] = solution.get("apk_name", "")
        result["worktree_path"] = str(worktree.resolve())
        feedback = {
            "finding_id": finding_id,
            "status": result.get("status", "unknown"),
            "failure_attribution": result.get("failure_attribution", ""),
            "build_failure_phase": result.get("build_failure_phase", ""),
            "build_files_changed_by_patch": result.get(
                "build_files_changed_by_patch", []
            ),
            "compiler_diagnostics": result.get("compiler_diagnostics", []),
            "baseline_compiler_diagnostics": result.get(
                "baseline_compiler_diagnostics", []
            ),
            "new_compiler_diagnostics": result.get(
                "new_compiler_diagnostics", []
            ),
            "environment_remedy": result.get("environment_remedy", {}),
            "regeneration_guidance": (
                "" if result.get("status") in PASSING_STATUSES else
                # No test compiled on either side, so there is no error in the
                # tests to repair; only where they live can change.
                "This module's test build fails on the base commit at the same "
                "tasks, before compiling any test. Do not edit a test file that "
                "already exists there; put new tests in a module whose test "
                "build works, or leave them out."
                if result.get("reason") == "test_build_fails_on_base_without_patch"
                else
                "A test of this patch is in a directory that no Gradle build of "
                "the project compiles. Put tests in a module under the "
                "directory holding the settings file, or leave them out."
                if result.get("reason") == "validation_test_module_outside_gradle_root"
                else
                "Repair every baseline-confirmed compiler error in the diagnosed "
                "test source using current repository APIs, preserving valid test "
                "intent. Do not publish while the complete changed test source set "
                "fails. Delete a file only when exact evidence proves it is wholly "
                "obsolete or duplicated and contains no unique valid test behavior."
                if result.get("failure_attribution")
                == "verified_preexisting_test_debt" else
                "Do not append new tests to a source file whose existing code "
                "does not compile. Prefer a compilable test source set/file; "
                "otherwise include only evidence-backed API updates needed to "
                "restore that edited test file before adding the new tests."
                if result.get("failure_attribution")
                == "preexisting_test_or_source_debt" else
                "The patch changed or removed a symbol that code it did not "
                "touch still uses (see the diagnostics: the caller lines that "
                "no longer compile). Either keep the symbol's signature and "
                "change only what it is set to, or update every caller in the "
                "same patch; do not leave callers that no longer compile."
                if result.get("failure_attribution") == "generated_patch"
                and any(
                    item.get("attribution") == "caller_of_changed_symbol"
                    for item in result.get("compiler_diagnostics", [])
                ) else
                "Repair the generated edits named by compiler diagnostics and "
                "regenerate the coordinated patch atomically."
                if result.get("failure_attribution") == "generated_patch" else ""
            ),
        }
        if result.get("failure_attribution") == "build_environment":
            dirs = result.get("environment_remedy", {}).get("distribution_dirs")
            print(
                f"{finding_id}: the Gradle wrapper could not install its "
                "distribution; the patch was never compiled. "
                + (f"Delete {', '.join(dirs)} and rerun the validation."
                   if dirs else "Check the wrapper cache and network."),
                file=sys.stderr,
            )
        finding_output.mkdir(parents=True, exist_ok=True)
        (finding_output / "validation_feedback.json").write_text(
            json.dumps(feedback, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        build_results.append(result)
        solution["build_validation"] = build_validation_record(
            result, finding_id, worktree,
        )
        enriched += 1

    args.output_dir.mkdir(parents=True, exist_ok=True)
    build_report_path = args.output_dir / "build_report.json"
    if repaired_ids is not None:
        # Findings this pass did not rebuild keep their earlier report entries.
        processed = {str(item.get("finding_id", "")) for item in build_results}
        try:
            earlier = json.loads(build_report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            earlier = []
        build_results = [
            item for item in earlier
            if isinstance(item, dict)
            and str(item.get("finding_id", "")) not in processed
        ] + build_results
    write_json_atomic(build_report_path, build_results)

    execution_report["build_validation"] = {
        "report_file": str(build_report_path.resolve()),
        "projects": len(build_results),
        "solutions_enriched": enriched,
    }
    shape_issues = validate_execution_report(execution_report)
    if shape_issues:
        print(
            "execution_report.json shape check found issues: "
            + "; ".join(shape_issues),
            file=sys.stderr,
        )
    write_json_atomic(args.execution_report, execution_report)

    success = sum(r.get("status") in PASSING_STATUSES for r in build_results)
    failed = sum(
        r.get("status") in {"failed", "timeout"} and failure_is_the_patch(r)
        for r in build_results
    )
    other = len(build_results) - success - failed
    preexisting = sum(
        r.get("status") not in PASSING_STATUSES and not failure_is_the_patch(r)
        for r in build_results
    )
    print(
        f"Compilation validation: {success} passed, {failed} failed, "
        f"{other} not executed"
        + (f" ({preexisting} pre-existing, not caused by the patch)"
           if preexisting else "")
        + f"; {enriched} solution records enriched"
    )

    # A compilation failure is a validation outcome that must remain visible
    # to the PR stage. Missing reports/infrastructure errors fail above.
    if approved_infrastructure_failures:
        print(
            f"{approved_infrastructure_failures} finding(s) could not be "
            "built here and were explicitly approved for publication "
            "without compile validation; continuing so the PR stages can "
            "run. Nothing establishes that those patches compile."
        )
    if infrastructure_failures:
        return 2
    # Only a failure the patch is actually responsible for stops the pipeline.
    # A build that fails identically without the patch is a fact about the
    # environment, and aborting on it discards every remaining stage for a
    # finding whose patch was never even compiled.
    if args.fail_on_validation_error and any(
            failure_is_the_patch(result) for result in build_results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
