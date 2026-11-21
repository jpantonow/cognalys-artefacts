#!/usr/bin/env python3
"""What a generated unit test is allowed to import in this module.

A patch that adds a test is only worth generating if the test compiles.
einkbro's restoreLegacyBackupData fix (run_20260917_230558) was a correct
production change carrying a test that imported `org.robolectric` and
`androidx.test.core.app.ApplicationProvider`; the module declares neither,
so the Gradle build failed with five unresolved references, the compiler
repair correctly abstained -- fixing it would have meant editing the build
file, which the finding did not authorise -- and the production fix died
with its test. Twenty minutes of build time to learn something the module's
own dependency declarations state up front.

This module reads those declarations, including Gradle version catalogs,
and answers two questions: which libraries a test in this module may use,
and whether a given file imports something none of them provides. It is
deliberately silent when it cannot find the declarations: a check that
cannot see the build file must not reject the patch.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Iterable, List, Set


_BUILD_FILE_NAMES = ("build.gradle.kts", "build.gradle")
_CATALOG_PATH = ("gradle", "libs.versions.toml")

# A literal coordinate anywhere in a dependency declaration.
_LITERAL_COORDINATE = re.compile(
    r"""["']([a-zA-Z][\w.\-]*):([a-zA-Z][\w.\-]*)(:[^"']*)?["']"""
)
# `kotlin("test")`, the Kotlin DSL shorthand for org.jetbrains.kotlin:kotlin-test.
_KOTLIN_SHORTHAND = re.compile(r"""\bkotlin\(\s*["']([\w\-]+)["']""")
# Packages under an always-available prefix that ship in their own artifact:
# `kotlin.test` is not part of the Kotlin stdlib. In run_20260929_104044 a
# generated HomeApp test imported kotlin.test.Test, passed this check, and
# failed to compile.
_SEPARATE_ARTIFACT_PACKAGES = {
    "kotlin.test": "org.jetbrains.kotlin:kotlin-test",
}
# `libs.androidx.room.testing`, `libs.junit` -- a reference into the catalog.
_CATALOG_REFERENCE = re.compile(r"\blibs\.([A-Za-z][\w.]*)")
# `alias = { module = "group:artifact", ... }` or the group/name form.
_CATALOG_MODULE = re.compile(
    r"""(?m)^\s*(?P<alias>[\w.\-]+)\s*=\s*\{[^}\n]*?"""
    r"""(?:module\s*=\s*["'](?P<module>[^"']+)["']"""
    r"""|group\s*=\s*["'](?P<group>[^"']+)["'][^}\n]*?"""
    r"""name\s*=\s*["'](?P<name>[^"']+)["'])"""
)
_IMPORT = re.compile(r"(?m)^[ \t]*import[ \t]+(?:static[ \t]+)?([\w.]+)")

# Available to any module without being declared: the language runtimes and
# the Android platform stubs the unit-test task already puts on the
# classpath.
_ALWAYS_AVAILABLE = (
    "java.", "javax.", "jakarta.", "kotlin.", "android.", "dalvik.", "org.w3c.",
    "org.xml.", "org.json.",
)
# Tokens too common to identify a library. A coordinate sharing only these
# with an import says nothing about whether it provides it.
_GENERIC_TOKENS = frozenset({
    "org", "com", "io", "net", "dev", "me", "app", "lib", "libs", "android",
    "androidx", "core", "test", "testing", "tests", "api", "impl", "common",
    "util", "utils", "runtime", "ktx", "kotlin", "java", "google", "github",
    "compose", "ui", "main", "src",
})


_PACKAGE_HEAD_CHARS = 16_000
_MAX_PREFIXES = 40


def _outermost(prefixes: Iterable[str]) -> List[str]:
    """The prefixes no other one in the set already covers."""
    kept: List[str] = []
    for prefix in sorted(set(prefixes), key=lambda value: (value.count("."), value)):
        if not any(prefix == other or prefix.startswith(other + ".") for other in kept):
            kept.append(prefix)
    return sorted(kept)


def declared_group_prefixes(coordinates: Iterable[str]) -> List[str]:
    """The group ids of the declared coordinates, none inside another. A library's
    packages usually start with its group id (org.robolectric), or share a
    distinctive word with the coordinate (org.junit and junit:junit)."""
    return _outermost(
        coordinate.split(":", 1)[0] for coordinate in coordinates if ":" in coordinate)


def module_build_file(repo_root: Path, relative_path: str) -> Path | None:
    """The build file of the module that owns `relative_path`."""
    current = (repo_root / relative_path).parent
    try:
        current.relative_to(repo_root)
    except ValueError:
        return None
    while True:
        for name in _BUILD_FILE_NAMES:
            candidate = current / name
            if candidate.is_file():
                return candidate
        if current == repo_root or repo_root not in current.parents:
            return None
        current = current.parent


def _catalog(repo_root: Path) -> Dict[str, str]:
    """alias -> "group:artifact", from the Gradle version catalog."""
    catalog_file = repo_root.joinpath(*_CATALOG_PATH)
    try:
        text = catalog_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    entries: Dict[str, str] = {}
    for match in _CATALOG_MODULE.finditer(text):
        module = match.group("module") or (
            f"{match.group('group')}:{match.group('name')}"
            if match.group("group") else ""
        )
        if module:
            entries[match.group("alias").replace("-", ".").lower()] = module
    return entries


def declared_coordinates(repo_root: Path, relative_path: str) -> List[str]:
    """Every dependency coordinate the owning module declares.

    All configurations are read, not only the test ones: Gradle puts the
    module's `implementation` dependencies on the unit-test compile
    classpath as well, so a test may use them.
    """
    build_file = module_build_file(repo_root, relative_path)
    if build_file is None:
        return []
    try:
        text = build_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # A dotless group is kept only with a version, which is what makes
    # "junit:junit:4.13.2" a coordinate and "key:value" not one; dropping it
    # made org.junit.Test look undeclared in run_20260929_104044.
    coordinates = {
        f"{group}:{artifact}"
        for group, artifact, version in _LITERAL_COORDINATE.findall(text)
        if "." in group or version
    }
    coordinates.update(
        f"org.jetbrains.kotlin:kotlin-{name}"
        for name in _KOTLIN_SHORTHAND.findall(text)
    )
    catalog = _catalog(repo_root)
    for reference in _CATALOG_REFERENCE.findall(text):
        module = catalog.get(reference.lower())
        if module:
            coordinates.add(module)
    return sorted(coordinates)


def _tokens(value: str) -> Set[str]:
    return {
        token for token in re.split(r"[.:\-_]", value.lower())
        if token and token not in _GENERIC_TOKENS
    }


def _provided_by(imported: str, coordinates: Iterable[str]) -> bool:
    """Whether some declared coordinate can provide `imported`."""
    lowered = imported.lower()
    for package, artifact in _SEPARATE_ARTIFACT_PACKAGES.items():
        if lowered == package or lowered.startswith(package + "."):
            return any(c.lower().startswith(artifact) for c in coordinates)
    import_tokens = _tokens(imported)
    for coordinate in coordinates:
        group = coordinate.split(":", 1)[0].lower()
        if lowered == group or lowered.startswith(group + "."):
            return True
        # A group id is not always the package root -- mockwebserver ships
        # as com.squareup.okhttp3:mockwebserver and lives in
        # okhttp3.mockwebserver -- so a distinctive token shared by the
        # coordinate and the import counts as well.
        if import_tokens & _tokens(coordinate):
            return True
    return False


def repository_packages(repo_root: Path, limit: int = 4000) -> Set[str]:
    """Package roots the checkout itself declares."""
    roots: Set[str] = set()
    package = re.compile(r"(?m)^[ \t]*package[ \t]+([\w.]+)")
    seen = 0
    for path in repo_root.rglob("*"):
        if seen >= limit:
            break
        if path.suffix not in {".java", ".kt"} or not path.is_file():
            continue
        seen += 1
        # The package line follows the license header, which can be long:
        # read to 16 KB. At 600 characters RedReader's GPL header hid every
        # package but six, and a test importing its own
        # org.quantumbadger.redreader.common.General was refused as
        # TEST_IMPORT_NOT_ON_TEST_CLASSPATH (2026-11-03).
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:_PACKAGE_HEAD_CHARS]
        except OSError:
            continue
        match = package.search(head)
        if match:
            roots.add(match.group(1))
    return roots


def unsupported_test_imports(
    repo_root: Path, relative_path: str, code: str,
    package_roots: Iterable[str] | None = None,
) -> List[str]:
    """Imports in `code` that nothing on this module's test classpath provides.

    Returns [] whenever the answer cannot be established -- no build file,
    no parsed coordinate -- because a check that cannot see the
    declarations has no business rejecting a patch.
    """
    coordinates = declared_coordinates(repo_root, relative_path)
    if not coordinates:
        return []
    roots = set(package_roots) if package_roots is not None else repository_packages(repo_root)
    unsupported = []
    for imported in _IMPORT.findall(code or ""):
        if imported.startswith(_ALWAYS_AVAILABLE) and not any(
            imported == package or imported.startswith(package + ".")
            for package in _SEPARATE_ARTIFACT_PACKAGES
        ):
            continue
        if any(imported == root or imported.startswith(root + ".")
               for root in roots):
            continue
        if _provided_by(imported, coordinates):
            continue
        unsupported.append(imported)
    return sorted(dict.fromkeys(unsupported))


def module_test_classpath(repository_path: str, test_roots: Iterable[str]) -> Dict:
    """The test classpath of the module that owns the first test root.

    Returns {} when there is no checkout or no test source set, so a prompt
    section is added only when it carries something real.
    """
    repo_root = Path(str(repository_path or ""))
    roots = [str(root) for root in test_roots if str(root).strip()]
    if not repo_root.is_dir() or not roots:
        return {}
    summary = test_classpath_summary(repo_root, f"{roots[0]}/Probe.kt")
    return summary if summary.get("declared_dependencies") else {}


# Android framework packages whose classes need a device or Robolectric to
# exist at all: on a plain JVM, `WebView(context)` or `view.settings` throws.
_ANDROID_RUNTIME_IMPORT = re.compile(
    r"^\s*import\s+(?:android\.(?:webkit|view|widget|app|graphics|media|hardware|"
    r"location|bluetooth|nfc|content\.res)\b|androidx\.(?:appcompat|fragment|activity|"
    r"webkit|core\.view|compose\.ui)\b)",
    re.MULTILINE,
)
# The Kotlin Multiplatform names of the device test set as well.
_INSTRUMENTED_TEST = re.compile(
    r"(?:^|/)src/(?:androidTest|androidInstrumentedTest|androidDeviceTest)\w*/")


def android_runtime_source(repo_root: Path, relative_path: str) -> bool:
    """Whether a production file works with framework classes a JVM test cannot create."""
    try:
        text = (Path(repo_root) / relative_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(_ANDROID_RUNTIME_IMPORT.search(text))


def untestable_reason(repo_root: Path, test_path: str, production_paths: Iterable[str]) -> str:
    """Why a new test planned at ``test_path`` could not check the change, or "".

    AI Hub's mixed-content fix: three generations of a test for a WebView
    setting, each one reading the source or comparing two constants, because
    nothing else can check a WebView setting without a device. The plan is
    the place to know that, before any test is generated:

    - an instrumented test (src/androidTest) is compiled by the validation
      but never run, so it verifies nothing;
    - a JVM test (src/test) cannot create the framework classes the changed
      code uses unless the module declares Robolectric.
    """
    if not any(android_runtime_source(repo_root, path) for path in production_paths):
        return ""
    if _INSTRUMENTED_TEST.search(test_path):
        return "an instrumented test is compiled but never run by the validation"
    coordinates = declared_coordinates(Path(repo_root), test_path)
    if not any("robolectric" in coordinate.lower() for coordinate in coordinates):
        return ("the changed code uses Android framework classes a JVM test "
                "cannot create, and the module declares no Robolectric")
    return ""


def test_classpath_summary(repo_root: Path, relative_path: str,
                           package_roots: Iterable[str] | None = None) -> Dict:
    """What a test in this module may import, for a prompt to plan against.

    It names the import prefixes as well as the coordinates: the check
    (unsupported_test_imports) accepts an import by its package, and a draft
    told only "junit:junit" or "androidx.test.ext:junit" still guessed
    Robolectric or kotlin.test packages the module never declared.
    """
    repo_root = Path(repo_root)
    coordinates = declared_coordinates(repo_root, relative_path)
    roots = (set(package_roots) if package_roots is not None
             else repository_packages(repo_root) if coordinates and repo_root.is_dir()
             else set())
    return {
        "module_build_file": (
            str(build.relative_to(repo_root).as_posix())
            if (build := module_build_file(repo_root, relative_path)) else ""
        ),
        "declared_dependencies": coordinates,
        "declared_group_prefixes": declared_group_prefixes(coordinates)[:_MAX_PREFIXES],
        "repository_package_prefixes": _outermost(roots)[:_MAX_PREFIXES],
        "always_available_prefixes": list(_ALWAYS_AVAILABLE),
        "rule": (
            "A generated test may import the repository's own packages "
            "(repository_package_prefixes), the always-available prefixes, and "
            "the libraries among declared_dependencies (an import is accepted when its package starts with "
            "one of declared_group_prefixes or shares a distinctive word with a "
            "declared coordinate, as org.junit with junit:junit); use only those, and "
            "write the test "
            "the declared libraries support. kotlin.test is not always available: it "
            "needs a declared org.jetbrains.kotlin:kotlin-test* artifact. "
            "Anything else does not compile in this "
            "module, and adding it would mean editing the build file, which "
            "the finding does not authorise."
        ),
    }
