"""Which Gradle source sets hold tests, which module they belong to, and what runs them.

Android modules keep tests in src/test and src/androidTest; Kotlin
Multiplatform modules name theirs after the target: androidUnitTest or
androidHostTest (JVM tests of the Android target), androidInstrumentedTest or
androidDeviceTest (on a device), commonTest, jvmTest. Fread's framework module
held six tests in src/androidUnitTest/kotlin that the pipeline could not see,
so the only test root it offered was another module's (plugins/bluesky), and
the generated test was built by that module's task (2026-10-15).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List

# "test", "androidTest" and every set whose name ends in "Test". Only the two
# classic names ignore case, as the checks this replaces did: a package
# directory such as src/latest/ is not a test set.
_TEST_SET = r"(?:(?i:test|androidTest)|[a-z][A-Za-z0-9]*Test)"
TEST_SOURCE_SET_SEGMENT = re.compile(rf"(?:^|/)src/({_TEST_SET})/")
TEST_SOURCE_ROOT = re.compile(rf"^((?:.*?/)?src/{_TEST_SET}/(?:java|kotlin))(?:/|$)")
_SOURCE_SET = re.compile(r"(?:^|/)src/([A-Za-z][A-Za-z0-9]*)/")

# Compiled by the validation but never run: no device.
INSTRUMENTED_TEST_SETS = frozenset({
    "androidtest", "androidinstrumentedtest", "androiddevicetest",
})
# JVM unit tests of Android code: the classic set, AGP's KMP androidTarget()
# set, and the com.android.kotlin.multiplatform.library host-test set.
_ANDROID_UNIT_TEST_SET = re.compile(r"^(?:test|\w*UnitTest|\w*HostTest)$")
# Production sets KMP compiles only for other targets: an APK never runs them.
_OTHER_TARGET_MAIN = re.compile(r"^(?!android|common)[a-z]\w*Main$")


def source_set_name(path: str) -> str:
    """The source set a repository path sits in ("main", "androidUnitTest"), or ""."""
    match = _SOURCE_SET.search(str(path).replace("\\", "/"))
    return match.group(1) if match else ""


def test_source_set(path: str) -> str:
    """The test source set a path sits in, or "" for production code."""
    match = TEST_SOURCE_SET_SEGMENT.search(str(path).replace("\\", "/"))
    return match.group(1) if match else ""


def is_test_source_path(path: str) -> bool:
    return bool(test_source_set(path))


# A file outside any src/<set>/ layout: a test/ or tests/ directory (a module's
# own test root), or a test class's name.
_TEST_DIRECTORY = re.compile(r"(?:^|/)tests?/")
_TEST_CLASS_NAME = re.compile(r"(?:Test|Tests|IT)$")


def is_test_source(path: str) -> bool:
    """Whether a repository path is test code: the one rule for every check (B8).

    Decided by the source set when the path has one (src/test, src/androidTest,
    src/<name>Test: commonTest, jvmTest, androidUnitTest...): a production set
    is production whatever its directories or file are called. Without a
    source set, a test/ or tests/ directory, or a file named *Test, *Tests or
    *IT. Three rules disagreed before: a substring ("test" anywhere in the
    path, so Attestation.kt and a latest/ package were tests), the file name,
    and a regular expression.
    """
    text = str(path or "").replace("\\", "/")
    if _SOURCE_SET.search(text):
        return is_test_source_path(text)
    if _TEST_DIRECTORY.search(text):
        return True
    return bool(_TEST_CLASS_NAME.search(Path(text).stem))


def is_instrumented_test_set(name: str) -> bool:
    return str(name).lower() in INSTRUMENTED_TEST_SETS


def source_module(path: str) -> str | None:
    """The module directory of a path ("" for the repository root).

    The directory above its src/<set>/ segment; None when the path has none,
    so callers that cannot tell the module do not guess one.
    """
    text = "/" + str(path).replace("\\", "/").lstrip("/")
    match = re.search(r"/src/[A-Za-z][A-Za-z0-9]*/", text)
    if not match:
        return None
    return text[:match.start()].strip("/")


def unit_test_sets_for(production_path: str) -> List[str] | None:
    """Test sets whose tests can exercise a production file, by name.

    androidMain/main (and flavor or build-type sets) take the module's
    Android JVM unit set; commonMain also takes commonTest. A set KMP
    compiles only for another target (jvmMain, desktopMain, iosMain) takes
    none: the APK does not contain that code. None when the path is not in a
    src/<set>/ layout at all.
    """
    name = source_set_name(production_path)
    if not name:
        return None
    if _OTHER_TARGET_MAIN.match(name):
        return []
    accepted = ["test", "*UnitTest", "*HostTest"]
    if name == "commonMain":
        accepted.append("commonTest")
    return accepted


def accepts_test_set(production_path: str, test_set: str) -> bool:
    accepted = unit_test_sets_for(production_path)
    if accepted is None:
        return True
    return ((test_set == "commonTest" and "commonTest" in accepted)
            or (bool(accepted) and bool(_ANDROID_UNIT_TEST_SET.match(test_set))
                and not is_instrumented_test_set(test_set)))


def test_roots_for_production(roots: Iterable[str], production_path: str) -> List[str] | None:
    """The test roots in the production file's own module that can test it.

    The module's Android JVM unit root first, then commonTest. None when the
    production file's module cannot be told from its path: the caller then
    keeps every root, as before.
    """
    module = source_module(production_path)
    if module is None or unit_test_sets_for(production_path) is None:
        return None
    ranked = []
    for root in roots:
        test_set = test_source_set(str(root) + "/")
        if source_module(str(root) + "/") != module or not accepts_test_set(
                production_path, test_set):
            continue
        ranked.append((test_set == "commonTest", str(root)))
    return [root for _common, root in sorted(ranked)]


def unit_test_task(module_dir: Path, test_sets: Iterable[str]) -> List[str]:
    """The Gradle tasks (unqualified by flavor) that run a module's changed JVM test sets.

    src/test and androidUnitTest run under testDebugUnitTest; the
    com.android.kotlin.multiplatform.library plugin's androidHostTest under
    testAndroidHostTest, and commonTest with it in such a module; another
    KMP target's set (jvmTest, desktopTest) under its own task of that name.
    """
    host_test_module = (Path(module_dir) / "src" / "androidHostTest").is_dir() or any(
        "com.android.kotlin.multiplatform.library" in text
        or re.search(r"\bandroidLibrary\s*\{", text)
        for text in _build_texts(Path(module_dir))
    )
    tasks = set()
    for name in test_sets:
        if name.endswith("HostTest") or (name == "commonTest" and host_test_module):
            tasks.add("testAndroidHostTest")
        elif name.lower() == "test" or name.endswith("UnitTest") or name == "commonTest":
            tasks.add("testDebugUnitTest")
        elif not is_instrumented_test_set(name):
            tasks.add(name)
    return sorted(tasks)


def _build_texts(module_dir: Path) -> List[str]:
    texts = []
    for name in ("build.gradle.kts", "build.gradle"):
        try:
            texts.append((module_dir / name).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return texts
