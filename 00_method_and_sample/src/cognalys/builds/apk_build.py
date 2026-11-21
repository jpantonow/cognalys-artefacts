#!/usr/bin/env python3
"""Build Android projects with ProGuard/R8 and resource shrinking disabled.

Default usage:
    python build_apks_no_proguard.py --projects-dir cloned_projects_sample

Release build:
    python build_apks_no_proguard.py --projects-dir cloned_projects_sample --variant release
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple


from cognalys.builds.git_directory import git_directory_for_build  # noqa: E402
from cognalys.utils.network_git import (  # noqa: E402
    network_git_timeout, network_subcommand, run_network_git,
)
from cognalys.utils.step_outcome import step_exit_code  # noqa: E402


INIT_GRADLE = r"""
// Input Leaf (2026-11-05) signs its debug build with the release config, whose
// keystore the repository does not hold: validateSigningDebug failed. Only a
// config whose keystore file is missing is replaced, by the platform debug key.
def signDebugWithTheDebugKey = { project, androidExt ->
    def debugKey = androidExt.hasProperty("signingConfigs") ? androidExt.signingConfigs.findByName("debug") : null
    if (debugKey == null) {
        return
    }
    def missing = { target ->
        def config = target.hasProperty("signingConfig") ? target.signingConfig : null
        config != null && config != debugKey && config.storeFile != null && !config.storeFile.exists()
    }
    def debugType = androidExt.buildTypes.findByName("debug")
    if (debugType != null && missing(debugType)) {
        project.logger.lifecycle("Debug build of ${project.path} signed with the debug key: its keystore ${debugType.signingConfig.storeFile} is missing")
        debugType.signingConfig = debugKey
    }
    if (androidExt.hasProperty("productFlavors")) {
        androidExt.productFlavors.all { flavor ->
            if (missing(flavor)) {
                flavor.signingConfig = debugKey
            }
        }
    }
}

def disableAndroidMinification = { project ->
    try {
        def androidExt = project.extensions.findByName("android")
        if (androidExt == null || !androidExt.hasProperty("buildTypes")) {
            return
        }

        androidExt.buildTypes.all { buildType ->
            if (buildType.hasProperty("minifyEnabled")) {
                buildType.minifyEnabled = false
            }
            if (buildType.hasProperty("shrinkResources")) {
                buildType.shrinkResources = false
            }
        }
    } catch (Throwable t) {
        project.logger.lifecycle("Could not disable minify/shrink for ${project.path}: ${t.message}")
    }
}

def checkDebugSigning = { project, androidExt ->
    try {
        if (androidExt != null && androidExt.hasProperty("buildTypes")) {
            signDebugWithTheDebugKey(project, androidExt)
        }
    } catch (Throwable t) {
        project.logger.lifecycle("Could not check the debug signing of ${project.path}: ${t.message}")
    }
}

def watchAndroidPlugins = { project ->
    ["com.android.application", "com.android.dynamic-feature"].each { pluginId ->
        project.plugins.withId(pluginId) {
            disableAndroidMinification(project)
            // AGP 7+ locks the DSL before afterEvaluate runs (Input Leaf's
            // signing change there had no effect); finalizeDsl is the last
            // point a build type's signing config can still change.
            def components = project.extensions.findByName("androidComponents")
            if (components != null) {
                components.finalizeDsl { androidExt -> checkDebugSigning(project, androidExt) }
            }
            project.afterEvaluate {
                disableAndroidMinification(project)
                if (components == null) {
                    checkDebugSigning(project, project.extensions.findByName("android"))
                }
            }
        }
    }
}

// Kahon (2026-10-22) builds with Gradle's isolated projects, which rejects
// reaching subprojects through allprojects ("Project ':' cannot access
// 'Project.plugins' functionality on subprojects via 'allprojects'").
// gradle.lifecycle.beforeProject (Gradle 8.8+) runs in each project's own
// context; older Gradle keeps allprojects.
if (gradle.metaClass.respondsTo(gradle, "getLifecycle")) {
    gradle.lifecycle.beforeProject { project -> watchAndroidPlugins(project) }
} else {
    allprojects { project -> watchAndroidPlugins(project) }
}
"""


@dataclass
class BuildResult:
    project_name: str
    project_dir: str
    gradle_root: str
    task: str
    status: str
    return_code: Optional[int]
    duration_seconds: float
    apk_count: int
    copied_apks: List[str]
    log_file: str
    reason: str = ""
    # The APK was minified by R8 although minification was turned off (see
    # r8_optimized_apk): RVSec's frames may not map to source.
    r8_optimized: bool = False
    # Present when the build scripts read or write under .git/: whether the
    # build ran with a real .git directory (builds.git_directory).
    git_directory_shim: Optional[dict] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Android projects in a directory."
    )
    parser.add_argument(
        "--projects-dir",
        default="outputs/cloned_projects",
        help="Folder containing one subdirectory per cloned APK/project.",
    )
    parser.add_argument(
        "--output-dir",
        default="outputs/built_apks_no_proguard",
        help="Where generated APKs, logs and reports will be written.",
    )
    parser.add_argument(
        "--variant",
        default="debug",
        choices=("debug", "release"),
        help="Build variant. Debug is usually unsigned and already non-minified.",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Custom Gradle task. Overrides --variant, for example ':app:assembleDebug'.",
    )
    parser.add_argument(
        "--additional-task",
        action="append",
        default=[],
        help=(
            "Additional Gradle task to run in the same invocation. Can be "
            "repeated, for example assembleDebugAndroidTest or testDebugUnitTest."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Timeout per project in seconds.",
    )
    parser.add_argument(
        "--hung-client-grace",
        type=int,
        default=HUNG_CLIENT_GRACE_SECONDS,
        help=(
            "Seconds the log must stay unchanged after Gradle's end-of-build "
            "line before a client that has not exited is stopped and the "
            "build is judged by that line."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Build only the first N projects. 0 means no limit.",
    )
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Build only project directory names containing this text. Can be repeated.",
    )
    parser.add_argument(
        "--refresh-dependencies",
        action="store_true",
        help="Pass --refresh-dependencies to Gradle.",
    )
    parser.add_argument(
        "--gradle-property",
        action="append",
        default=[],
        help="KEY=VALUE passed to Gradle as -PKEY=VALUE. Can be repeated.",
    )
    parser.add_argument(
        "--clean-first",
        action="store_true",
        help="Run Gradle clean before the requested build task.",
    )
    parser.add_argument(
        "--gradle-build-cache",
        action="store_true",
        help=(
            "Pass --build-cache to Gradle. Content-addressed task outputs are "
            "reused across the patched, baseline and repair builds of a "
            "repository, so a module the patch did not touch is not compiled "
            "again. Off by default."
        ),
    )
    parser.add_argument(
        "--continue-after-task-failure",
        action="store_true",
        help=(
            "Pass --continue to Gradle, so independent tasks still run after "
            "one fails and a single build reports every compile error."
        ),
    )
    parser.add_argument(
        "--skip-submodules",
        action="store_true",
        help=(
            "Do not initialize Git submodules before building. By default, "
            "declared submodules are synchronized and initialized recursively."
        ),
    )
    parser.add_argument(
        "--disable-minification",
        action="store_true",
        default=True,
        help=(
            "Disable ProGuard/R8 and resource shrinking with an init script. "
            "Enabled by default for every build."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be built without running Gradle.",
    )
    parser.add_argument(
        "--android-sdk",
        default=None,
        help=(
            "Android SDK path to pass to Gradle as ANDROID_HOME/ANDROID_SDK_ROOT. "
            "On WSL this is often /mnt/c/Users/<user>/AppData/Local/Android/Sdk."
        ),
    )
    parser.add_argument(
        "--java-home",
        default=None,
        help=(
            "JDK path to pass to Gradle as JAVA_HOME. On Windows, Android Studio's "
            "bundled JDK is often C:\\Program Files\\Android\\Android Studio\\jbr."
        ),
    )
    return parser.parse_args()


def windows_path_to_wsl(path: str) -> Optional[Path]:
    if len(path) < 3 or path[1:3] not in (":\\", ":/"):
        return None

    drive = path[0].lower()
    rest = path[3:].replace("\\", "/")
    return Path(f"/mnt/{drive}") / rest


def infer_windows_user_from_cwd() -> Optional[str]:
    parts = Path.cwd().parts
    lowered = [part.lower() for part in parts]
    try:
        users_index = lowered.index("users")
    except ValueError:
        return None

    if users_index + 1 < len(parts):
        return parts[users_index + 1]
    return None


def existing_path_from(value: Optional[str]) -> Optional[Path]:
    if not value:
        return None

    expanded = os.path.expandvars(os.path.expanduser(value))
    candidates = [Path(expanded)]
    wsl_path = windows_path_to_wsl(expanded)
    if wsl_path is not None:
        candidates.append(wsl_path)

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def sdk_matches_host(path: Path) -> bool:
    """Reject a Windows SDK selected by a Linux/WSL build process."""
    if os.name == "nt":
        return True

    tool_pairs = [
        (path / "platform-tools" / "adb", path / "platform-tools" / "adb.exe"),
    ]
    build_tools = path / "build-tools"
    if build_tools.is_dir():
        for version in build_tools.iterdir():
            if version.is_dir():
                tool_pairs.append((version / "aapt", version / "aapt.exe"))
                tool_pairs.append((version / "aapt2", version / "aapt2.exe"))

    # A partially installed native SDK may not contain these tools yet. It is
    # incompatible only when Windows binaries are present without Linux peers.
    return not any(windows.is_file() and not native.is_file()
                   for native, windows in tool_pairs)


def detect_android_sdk(explicit_sdk: Optional[str]) -> Optional[Path]:
    if explicit_sdk:
        explicit = existing_path_from(explicit_sdk)
        return explicit if explicit and sdk_matches_host(explicit) else None

    candidates: List[Optional[str]] = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
    ]

    # Prefer an SDK native to the process. In WSL, selecting the Windows SDK
    # produces misleading "corrupted Build Tools" errors because Gradle looks
    # for aapt/adb while that SDK contains aapt.exe/adb.exe.
    home = Path.home()
    candidates.extend(
        [
            str(home / "Android" / "Sdk"),
            str(home / "Library" / "Android" / "sdk"),
            "/opt/android-sdk",
        ]
    )

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(str(Path(local_appdata) / "Android" / "Sdk"))

    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        candidates.append(str(Path(userprofile) / "AppData" / "Local" / "Android" / "Sdk"))
        wsl_userprofile = windows_path_to_wsl(userprofile)
        if wsl_userprofile is not None:
            candidates.append(str(wsl_userprofile / "AppData" / "Local" / "Android" / "Sdk"))

    inferred_user = infer_windows_user_from_cwd()
    if inferred_user:
        candidates.append(f"/mnt/c/Users/{inferred_user}/AppData/Local/Android/Sdk")

    for candidate in candidates:
        path = existing_path_from(candidate)
        if path is not None and sdk_matches_host(path):
            return path
    return None


def setup_logging(output_dir: Path) -> logging.Logger:
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("build_apks")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(logs_dir / "build_apks.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def has_settings_file(path: Path) -> bool:
    return (path / "settings.gradle").exists() or (path / "settings.gradle.kts").exists()


def find_gradle_root(project_dir: Path) -> Optional[Path]:
    if has_settings_file(project_dir):
        return project_dir

    candidates: List[Path] = []
    for child in project_dir.iterdir():
        if child.is_dir() and has_settings_file(child):
            candidates.append(child)

    if len(candidates) == 1:
        return candidates[0]

    android_dir = project_dir / "android"
    if has_settings_file(android_dir):
        return android_dir

    return None


def iter_projects(projects_dir: Path, filters: Sequence[str]) -> Iterable[Path]:
    for child in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if filters and not any(f.lower() in child.name.lower() for f in filters):
            continue
        yield child


def gradle_command(gradle_root: Path) -> List[str]:
    if platform.system().lower().startswith("win"):
        wrapper = gradle_root / "gradlew.bat"
    else:
        wrapper = gradle_root / "gradlew"

    # gradlew without gradle/wrapper/gradle-wrapper.jar cannot start
    # (AnonymousMessenger, 2026-10-23: "Could not find or load main class
    # org.gradle.wrapper.GradleWrapperMain"); F-Droid builds such repositories
    # with its own Gradle, and the distribution the properties name does too.
    jar_missing = not (gradle_root / "gradle" / "wrapper" / "gradle-wrapper.jar").is_file()
    if wrapper.exists() and (jar_missing or not wrapper_script_usable(wrapper)):
        # Vetro's gradlew is committed with its line breaks stripped (one
        # 8 KB line, 2026-10-16), and a CRLF script asks for "/bin/sh\r":
        # either way exec fails with "No such file or directory". The
        # distribution its properties name builds the project all the same.
        distribution = wrapper_distribution(gradle_root)
        if distribution is not None:
            return [str(distribution)]
    if wrapper.exists():
        # A wrapper committed without its executable bit (PaperCript's, on
        # 2026-10-14) failed the whole build with PermissionError.
        if wrapper.suffix != ".bat" and not os.access(wrapper, os.X_OK):
            return [wrapper_interpreter(wrapper), str(wrapper)]
        return [str(wrapper)]

    # A project can keep gradle-wrapper.properties but not the gradlew script
    # (rssreader, 2026-10-16), and this machine has no global gradle: run the
    # distribution the properties name, as gradlew itself would.
    distribution = wrapper_distribution(gradle_root)
    if distribution is not None:
        return [str(distribution)]
    return ["gradle"]


_TASK_NOT_FOUND = re.compile(r"Cannot locate tasks that match '[^']*' as task '[^']*' not found")


def build_type_task(task: str, result) -> str:
    """`:m:assembleXDebug` -> `:m:assembleX` when Gradle had no such task, else "".

    A name fdroiddata lists under `gradle:` can be a build type rather than a
    flavour; then its variant task carries no Debug suffix.
    """
    match = re.fullmatch(r"(?P<head>(?:.*:)?assemble[A-Z]\w*?)Debug", str(task or ""))
    if not match or getattr(result, "status", "") != "failed":
        return ""
    try:
        log = Path(result.log_file).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return ""
    return match.group("head") if _TASK_NOT_FOUND.search(log) else ""


_SINGLE_CANDIDATE = re.compile(r"Some candidates are: '(?P<task>assemble\w+)'\.\s*$", re.MULTILINE)


def suggested_task(task: str, result) -> str:
    """The one task Gradle suggests when the requested one does not exist, else "".

    vayunmathur's office and findfamily (2026-10-24) have no debug build type;
    Gradle answered ":office:assembleDebug ... Some candidates are:
    'assembleDev'". Only a single assemble candidate is taken.
    """
    if getattr(result, "status", "") != "failed":
        return ""
    try:
        log = Path(result.log_file).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return ""
    match = _SINGLE_CANDIDATE.search(log) if _TASK_NOT_FOUND.search(log) else None
    if not match:
        return ""
    prefix = str(task).rsplit(":", 1)[0] + ":" if ":" in str(task) else ""
    return prefix + match.group("task")


_AMBIGUOUS_TASK = re.compile(
    r"Cannot locate tasks that match '[^']*' as task '[^']*' is ambiguous[^\n]*?"
    r"Candidates are: (?P<candidates>[^\n]*)")


def ambiguous_task(task: str, result) -> str:
    """The one assemble task among the candidates Gradle names for an ambiguous one, else "".

    moshinda (2026-10-27): `gradle: [fdroid]` names the build type
    fdroidRelease, and the retry :mastodon:assembleFdroid was ambiguous
    between 'assembleFdroidRelease' and 'assembleFdroidReleaseUnitTest'. A
    test task builds no APK, so the APK task is the only real candidate.
    """
    if getattr(result, "status", "") != "failed":
        return ""
    try:
        log = Path(result.log_file).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return ""
    match = _AMBIGUOUS_TASK.search(log)
    if not match:
        return ""
    candidates = [name for name in re.findall(r"'(assemble\w+)'", match.group("candidates"))
                  if not name.endswith(("UnitTest", "AndroidTest"))]
    if len(candidates) != 1:
        return ""
    prefix = str(task).rsplit(":", 1)[0] + ":" if ":" in str(task) else ""
    return prefix + candidates[0]


def task_selection_failed(result) -> bool:
    """Whether Gradle stopped because the task name matched no task or several."""
    if getattr(result, "status", "") != "failed":
        return False
    try:
        log = Path(result.log_file).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return False
    return bool(_TASK_NOT_FOUND.search(log) or _AMBIGUOUS_TASK.search(log))


def fallback_task(task: str, result, requested: str, tried: Iterable[str]) -> str:
    """The next task to try after ``task`` named no single Gradle task, else "".

    The build-type retry comes first (N-Zik's `gradle: [foss]` is a build
    type, and Keryx's `gradle: [fdroid]` a flavour whose retry
    :androidApp:assembleFdroid builds; both rely on it), then the single
    candidate Gradle suggests or the single non-test one of an ambiguous name.
    F-Droid's own task, assemble<Flavours>Release, exists for every recipe
    with `gradle:`, but it is a release build: it is the last resort.
    """
    tried = set(tried)
    for candidate in (build_type_task(task, result), suggested_task(task, result),
                      ambiguous_task(task, result)):
        if candidate and candidate not in tried:
            return candidate
    if task_selection_failed(result):
        match = re.fullmatch(r"(?P<head>(?:.*:)?assemble[A-Z]\w*?)Debug", str(requested or ""))
        if match and match.group("head") + "Release" not in tried:
            return match.group("head") + "Release"
    return ""


_R8_RELEASE_MARKER = re.compile(rb'~~R8\{[^}]*"compilation-mode":"release"')
_D8_MARKER = re.compile(rb"~~D8\{")


def r8_optimized_apk(apk: Path) -> bool:
    """Whether an APK's dex was written by R8 in release mode (minified, merged).

    Password Store (2026-10-25): the retry :app:assembleFree matched the
    alias task assembleFreeRelease, and its convention plugin computes
    isMinifyEnabled, so R8 merged three lambdas into KeyGenType$1 and
    inlined jgit's SHA-1 into an app class; RVSec then reported findings on
    frames no source has. The compiler's marker in classes.dex says so: D8
    writes ~~D8{...}, R8 ~~R8{..."compilation-mode":"release"...}. A
    library's own R8 marker survives D8 dexing (Inure's debug APK holds one
    beside ~~D8{"compilation-mode":"debug"}), while a minified build has R8
    compile all of it: any D8 marker means the app's code was not shrunk.
    """
    import zipfile

    r8 = False
    try:
        with zipfile.ZipFile(apk) as bundle:
            for name in bundle.namelist():
                if re.fullmatch(r"classes\d*\.dex", name):
                    dex = bundle.read(name)
                    if _D8_MARKER.search(dex):
                        return False
                    r8 = r8 or bool(_R8_RELEASE_MARKER.search(dex))
    except (OSError, zipfile.BadZipFile, KeyError):
        return False
    return r8


_CORRUPT_GRADLE_LOCK = re.compile(r"Unexpected lock protocol found in lock file")


def remove_corrupt_gradle_locks(result, gradle_home: Optional[Path] = None,
                                gradle_root: Optional[Path] = None) -> int:
    """Delete the lock files a crash left corrupt; how many.

    Unstoppable Wallet (2026-10-24), after the PC rebooted mid-build:
    "Unexpected lock protocol found in lock file. Expected 3, found 0". A
    valid lock starts with its protocol version; a corrupt one is empty or
    starts with a zero byte (the retry of 2026-10-25 still failed when only
    empty files were removed). Gradle recreates them. Both Gradle's own
    caches and the project's .gradle folder are searched.
    """
    try:
        log = Path(result.log_file).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return 0
    if not _CORRUPT_GRADLE_LOCK.search(log):
        return 0
    home = gradle_home or Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")
    removed = 0
    folders = [home / "caches"] + ([Path(gradle_root) / ".gradle"] if gradle_root else [])
    for folder in folders:
        for lock in folder.rglob("*.lock"):
            try:
                if not lock.is_file():
                    continue
                with open(lock, "rb") as handle:
                    first = handle.read(1)
                if first in (b"", b"\x00"):
                    lock.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


_MISSING_SIGNING_PROPERTY = re.compile(
    r"Could not get unknown property '(\w+)' for SigningConfig")
# The providers API names neither the block nor the field; the build file
# line Gradle reports does.
_MISSING_GRADLE_PROPERTY = re.compile(
    r"Build file '([^']+)' line: (\d+).*?"
    r"Cannot query the value of Gradle property '(\w+)' because it has no value", re.DOTALL)
_SIGNING_FIELD = re.compile(r"\b(storeFile|storePassword|keyAlias|keyPassword)\b")
# A placeholder, never a key: only the release signing block reads it, and
# the debug variant this module builds is not signed with it.
SIGNING_PLACEHOLDER = "cognalys-unused"


def missing_signing_property(result) -> str:
    """The signing property a failed build's script read and nobody defined, else "".

    Librera (2026-10-22): app/build.gradle reads RELEASE_STORE_FILE and its
    siblings from the author's gradle.properties; fdroiddata strips that
    block before building, and without it evaluation stopped before any task.
    NotallyX (2026-10-28) reads them with providers.gradleProperty(...).get();
    that property counts only when the reported line assigns a signing field.
    """
    if getattr(result, "status", "") != "failed":
        return ""
    try:
        log = Path(result.log_file).read_text(encoding="utf-8", errors="replace")
    except (OSError, TypeError):
        return ""
    return signing_property_missing_from(log)


def signing_property_missing_from(log: str) -> str:
    """missing_signing_property on a Gradle output already in hand.

    The repository's formatter task (prs.repository_formatter) configures the
    same build script and stops at the same line (NotallyX, 2026-10-30).
    """
    match = _MISSING_SIGNING_PROPERTY.search(log)
    if match:
        return match.group(1)
    match = _MISSING_GRADLE_PROPERTY.search(log)
    if not match:
        return ""
    try:
        lines = Path(match.group(1)).read_text(encoding="utf-8", errors="replace").splitlines()
        line = lines[int(match.group(2)) - 1]
    except (OSError, IndexError):
        return ""
    return match.group(3) if _SIGNING_FIELD.search(line) else ""


def wrapper_interpreter(wrapper: Path) -> str:
    """The shell a non-executable gradlew asks for in its shebang.

    J2ME Loader's gradlew (2026-10-21) is `#!/usr/bin/env bash` and uses
    arrays; run as `sh gradlew`, Ubuntu's dash stopped at line 154 with
    'Syntax error: "(" unexpected'.
    """
    try:
        first_line = wrapper.read_bytes()[:256].split(b"\n", 1)[0]
    except OSError:
        return "sh"
    return "bash" if first_line.startswith(b"#!") and b"bash" in first_line else "sh"


def wrapper_script_usable(wrapper: Path) -> bool:
    """Whether a POSIX gradlew can be executed: real lines, LF line endings."""
    if wrapper.suffix == ".bat":
        return True
    try:
        head = wrapper.read_bytes()[:4096]
    except OSError:
        return False
    first_line, newline, _rest = head.partition(b"\n")
    return bool(newline) and not first_line.endswith(b"\r")


_DISTRIBUTION_URL = re.compile(r"^\s*distributionUrl\s*=\s*(\S+)", re.MULTILINE)


def wrapper_distribution(gradle_root: Path, gradle_home: Optional[Path] = None,
                         download: Optional[Callable[[str, Path], None]] = None) -> Optional[Path]:
    """bin/gradle of the distribution gradle-wrapper.properties names.

    Reuses one the wrapper already unpacked under GRADLE_USER_HOME, otherwise
    downloads and unpacks it there. None when there are no properties or the
    distribution cannot be had.
    """
    properties = gradle_root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    try:
        match = _DISTRIBUTION_URL.search(properties.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    if not match:
        return None
    url = match.group(1).replace("\\:", ":")
    if not urllib.parse.urlparse(url).netloc or not url.endswith(".zip"):
        # Rena-w4b's properties end at "distributionUrl=https\:" (2026-11-07):
        # nothing to fetch, and a dists/https: directory was left behind.
        return None
    name = url.rsplit("/", 1)[-1].removesuffix(".zip")          # gradle-9.5.1-bin
    version = re.sub(r"-(bin|all)$", "", name)                    # gradle-9.5.1
    gradle_home = gradle_home or Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")
    dists = gradle_home / "wrapper" / "dists" / name
    executable = "gradle.bat" if platform.system().lower().startswith("win") else "gradle"
    for candidate in sorted(dists.glob(f"*/{version}/bin/{executable}")):
        return candidate
    target = dists / "cognalys"
    try:
        target.mkdir(parents=True, exist_ok=True)
        archive = target / f"{name}.zip"
        (download or _download)(url, archive)
        import zipfile
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(target)
        archive.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 -- fall back to a global gradle
        logging.warning("Could not fetch Gradle distribution %s: %s", url, exc)
        return None
    script = target / version / "bin" / executable
    if not script.exists():
        return None
    script.chmod(script.stat().st_mode | 0o111)
    return script


def with_cargo_on_path(path: str, cargo_home: Optional[Path] = None) -> str:
    """PATH with rustup's bin directory when Rust is installed and missing from it.

    rustup puts ~/.cargo/bin on PATH only for login shells, so a Gradle Exec
    task that runs `cargo` failed to start it under a queue (vayunmathur's
    passwords, 2026-10-25) although Rust was installed.
    """
    home = cargo_home or Path(os.environ.get("CARGO_HOME") or Path.home() / ".cargo")
    binary = home / "bin"
    if not (binary / "cargo").exists() or str(binary) in path.split(os.pathsep):
        return path
    return str(binary) + os.pathsep + path if path else str(binary)


def task_relative_to_root(task: str, project_dir: Path, gradle_root: Path) -> str:
    """A task named by a repository path (`:sub/dir:assembleX`) in Gradle's terms.

    fdroiddata's `subdir` is relative to the repository, and the Gradle root
    can be below it: Olvid's subdir obv_messenger/app sits under the root
    obv_messenger, and `:obv_messenger/app:assembleProdZfdroidDebug` is no
    Gradle project (2026-10-24). The module's path from the root, with ':'
    between directories, is (`:app:assembleProdZfdroidDebug`).
    """
    match = re.match(r"^:(?P<path>[^:]*/[^:]*):(?P<name>[\w-]+)$", task)
    if not match:
        return task
    module = (Path(project_dir) / match["path"].strip("/")).resolve()
    try:
        relative = module.relative_to(Path(gradle_root).resolve())
    except ValueError:
        return task
    parts = [part for part in relative.parts if part]
    return f":{':'.join(parts)}:{match['name']}" if parts else match["name"]


_WRAPPER_CHECKSUM_FAILURE = re.compile(
    r"Verification of Gradle distribution failed|Expected checksum:", re.IGNORECASE)
_DISTRIBUTION_SUM = re.compile(r"^(\s*distributionSha256Sum\s*=\s*)(\S+)", re.MULTILINE)


def official_wrapper_checksum(gradle_root: Path,
                              fetch: Optional[Callable[[str], str]] = None) -> str:
    """The checksum services.gradle.org publishes for the wrapper's distribution
    when it differs from the one the project declares; "" otherwise.

    Encrypt-Decrypt and ThreadScoop (2026-10-24) declared the checksum of the
    other variant of Gradle 8.9 (-bin for -all and the reverse), so the
    wrapper refused the download it had asked for. Only distributions served
    by services.gradle.org, whose checksum is published beside them, qualify.
    """
    properties = gradle_root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    try:
        text = properties.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    url_match, sum_match = _DISTRIBUTION_URL.search(text), _DISTRIBUTION_SUM.search(text)
    if not url_match or not sum_match:
        return ""
    url = url_match.group(1).replace("\\:", ":")
    if not url.startswith("https://services.gradle.org/distributions/"):
        return ""

    def default_fetch(address: str) -> str:
        import urllib.request
        with urllib.request.urlopen(address, timeout=60) as response:
            return response.read().decode("ascii", errors="replace")

    try:
        official = (fetch or default_fetch)(url + ".sha256").strip().split()[0].lower()
    except Exception:  # noqa: BLE001 -- no published checksum, nothing to retry with
        return ""
    if not re.fullmatch(r"[0-9a-f]{64}", official) or official == sum_match.group(2).lower():
        return ""
    return official


@contextlib.contextmanager
def wrapper_checksum_replaced(gradle_root: Path, checksum: str, log):
    """gradle-wrapper.properties with ``checksum``, restored afterwards."""
    properties = gradle_root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    original = properties.read_bytes()
    properties.write_bytes(_DISTRIBUTION_SUM.sub(
        lambda match: match.group(1) + checksum,
        original.decode("utf-8", errors="replace"), count=1).encode("utf-8"))
    log.info("Using the checksum services.gradle.org publishes for the wrapper's "
             "distribution: the declared one belongs to another distribution")
    try:
        yield
    finally:
        properties.write_bytes(original)


def wrapper_checksum_failed(result) -> bool:
    try:
        return bool(_WRAPPER_CHECKSUM_FAILURE.search(
            Path(result.log_file).read_text(encoding="utf-8", errors="replace")))
    except (OSError, TypeError):
        return False


def _download(url: str, destination: Path) -> None:
    import urllib.request
    with urllib.request.urlopen(url, timeout=300) as response, open(destination, "wb") as handle:
        shutil.copyfileobj(response, handle)


# The newest JDK each Gradle release runs on (Gradle's compatibility matrix):
# a Gradle older than 8.5 fails on JDK 21 before reading the build, with
# "Unsupported class file major version 65".
_GRADLE_MAX_JDK = ((9, 1, 25), (8, 14, 24), (8, 10, 23), (8, 8, 22), (8, 5, 21),
                   (7, 6, 19), (7, 5, 18), (7, 3, 17), (7, 0, 16), (6, 7, 15),
                   (6, 3, 14), (6, 0, 13), (5, 4, 12))
# The newest JDK chosen when the project declares nothing newer: every build
# so far ran on 21 at most, and a Kotlin or Android plugin older than the
# JDK fails on it ("Unknown Java version"), so a Gradle 9 project moves to
# JDK 25 only when it asks for it.
_USUAL_MAX_JDK = 21
_DECLARED_JAVA = (
    re.compile(r"\bJavaVersion\.VERSION_(?:1_)?(\d+)\b"),
    re.compile(r"\bjvmToolchain\s*\(\s*(\d+)\s*\)"),
    re.compile(r"\bJvmTarget\.JVM_(?:1_)?(\d+)\b"),
    re.compile(r"\bJavaLanguageVersion\.of\s*\(\s*(\d+)\s*\)"),
)
_WRAPPER_VERSION = re.compile(r"gradle-(\d+)\.(\d+)")
_JDK_DIRECTORIES = (Path("/usr/lib/jvm"), Path("/Library/Java/JavaVirtualMachines"),
                    Path.home() / ".jdks")


def wrapper_gradle_version(gradle_root: Path) -> Optional[Tuple[int, int]]:
    properties = gradle_root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    try:
        match = _WRAPPER_VERSION.search(properties.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return (int(match.group(1)), int(match.group(2))) if match else None


def newest_supported_jdk(gradle_version: Tuple[int, int]) -> int:
    for major, minor, jdk in _GRADLE_MAX_JDK:
        if gradle_version >= (major, minor):
            return jdk
    return 11


def installed_jdks(directories: Sequence[Path] = _JDK_DIRECTORIES) -> dict:
    """Feature version -> JDK home, for the JDKs in the usual install places."""
    found = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.iterdir()):
            home = candidate / "Contents" / "Home" if (candidate / "Contents" / "Home").is_dir() else candidate
            match = re.search(r"(?<!\d)(?:1\.)?(\d{1,2})(?!\d)", candidate.name)
            if match and (home / "bin" / "java").exists():
                found.setdefault(int(match.group(1)), home.resolve())
    return found


def declared_java_version(gradle_root: Path) -> Optional[int]:
    """The newest Java version the project's build files compile for, else None.

    Read from JavaVersion.VERSION_N, jvmToolchain(N), JvmTarget.JVM_N and
    JavaLanguageVersion.of(N) in the build scripts and convention plugins.
    """
    ignored = {".git", ".gradle", "build", "outputs"}
    files = [*gradle_root.rglob("build.gradle"), *gradle_root.rglob("build.gradle.kts"),
             *(path for folder in ("build-logic", "buildSrc") if (gradle_root / folder).is_dir()
               for pattern in ("*.kt", "*.kts", "*.gradle")
               for path in (gradle_root / folder).rglob(pattern))]
    versions = []
    for path in files:
        try:
            if any(part in ignored for part in path.relative_to(gradle_root).parts[:-1]):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        for pattern in _DECLARED_JAVA:
            versions += [int(value) for value in pattern.findall(text)]
    return max(versions) if versions else None


def compatible_java_home(gradle_root: Path, jdks: Optional[dict] = None) -> Optional[Path]:
    """The installed JDK to run the project's Gradle wrapper on.

    The newest one the wrapper runs on, up to _USUAL_MAX_JDK. When the
    project compiles for a newer Java than that choice, the smallest
    installed JDK at or above it, still within what the wrapper runs on:
    keyholm (2026-10-26, Gradle 9.8 with JavaVersion.VERSION_25) cannot
    compile on 21. A declared version never lowers the choice: Android
    Gradle Plugin 8 needs JDK 17 to run whatever target the app declares,
    so a VERSION_11 project picking JDK 11 would stop building.

    None when the wrapper's version is unknown or no installed JDK is old
    enough; Gradle then uses whatever java the environment provides.
    """
    version = wrapper_gradle_version(gradle_root)
    if version is None:
        return None
    jdks = installed_jdks() if jdks is None else jdks
    ceiling = newest_supported_jdk(version)
    usual = [feature for feature in jdks if feature <= min(ceiling, _USUAL_MAX_JDK)]
    chosen = max(usual) if usual else None
    declared = declared_java_version(gradle_root)
    if declared and (chosen is None or chosen < declared):
        newer = [feature for feature in jdks if declared <= feature <= ceiling]
        if newer:
            chosen = min(newer)
    if chosen is None:
        supported = [feature for feature in jdks if feature <= ceiling]
        chosen = max(supported) if supported else None
    return jdks[chosen] if chosen is not None else None


def build_task(args: argparse.Namespace) -> str:
    if args.task:
        return args.task
    suffix = "Release" if args.variant == "release" else "Debug"
    return f"assemble{suffix}"


def write_init_script(output_dir: Path) -> Path:
    path = output_dir / "disable_proguard.init.gradle"
    path.write_text(INIT_GRADLE.strip() + "\n", encoding="utf-8")
    return path


_MINIFICATION_ASSIGNMENTS = (
    re.compile(rb"(\bminifyEnabled\s*(?:=\s*)?)true\b"),
    re.compile(rb"(\bisMinifyEnabled\s*=\s*)true\b"),
    re.compile(rb"(\bshrinkResources\s*(?:=\s*)?)true\b"),
    re.compile(rb"(\bisShrinkResources\s*=\s*)true\b"),
)
# An assignment of a computed value, to the end of its line: Password Store's
# convention plugin (2026-10-25) has `isMinifyEnabled = !minifySwitch.isPresent`,
# which the literal patterns above left alone, and its release APK came out
# of R8. Only a statement that starts with the flag (after a receiver such as
# `buildType.`) assigns it: `val changed = isMinifyEnabled != other` or a
# string naming it reads it, and rewriting those would break the script.
# `==`, `!=` and infix words (`as`, `and`) are not assignments either.
_COMPUTED_MINIFICATION = re.compile(
    rb"(?P<head>(?:^|(?<=[{;]))[ \t]*(?:[\w.]+\.)?"
    rb"(?:isMinifyEnabled|minifyEnabled|isShrinkResources|shrinkResources)"
    rb"(?:[ \t]*=(?!=)[ \t]*"
    rb"|[ \t]+(?!(?:as|and|or|in|is|instanceof|to|xor)\b)(?=!(?!=)|[\w(])))"
    rb"(?P<value>[^\n]*)", re.MULTILINE)


def _computed_flag_disabled(match: "re.Match[bytes]") -> bytes:
    """The assignment with its value replaced by false, when the value ends on its line.

    A value continued on the next line (an operator at the end, an open
    bracket) is left alone: cutting it would break the script.
    """
    value = match.group("value")
    code = re.sub(rb"\s*//.*$", b"", value).strip()
    if not code or code in (b"false", b"true") or code.startswith((b"{", b"=")):
        return match.group(0)
    if code.endswith((b"&&", b"||", b"+", b"-", b"*", b"/", b"?", b":", b",", b".", b"(", b"=")):
        return match.group(0)
    if any(code.count(open_) != code.count(close)
           for open_, close in ((b"(", b")"), (b"[", b"]"), (b"{", b"}"))):
        return match.group(0)
    return match.group("head") + b"false"


@contextlib.contextmanager
def disabled_minification_in_build_files(gradle_root: Path, enabled: bool, log):
    """Temporarily force Android Gradle build-type shrinking flags to false.

    Recent Android Gradle Plugin versions finalize variants before an init
    script's late DSL mutation can affect their task graph. Patch only the
    assignments of these flags (a literal, or a value that ends on its line)
    while Gradle runs, then restore every file byte-for-byte, including when
    the build fails or times out.
    """
    originals: dict[Path, bytes] = {}
    if not enabled:
        yield
        return
    ignored_dirs = {".git", ".gradle", "build", "outputs"}
    try:
        # Convention plugins (build-logic/, buildSrc/) set minification and
        # resource shrinking for every module that applies them: vayunmathur's
        # passwords (2026-10-24) kept isShrinkResources = true there while
        # youpipe/build.gradle.kts had minification turned off, and AGP
        # refused to configure the project. A convention plugin can also be
        # plain Kotlin (Password Store's build-logic/.../ApplicationPlugin.kt).
        candidates = sorted({
            *gradle_root.rglob("build.gradle"),
            *gradle_root.rglob("build.gradle.kts"),
            *(path for folder in ("build-logic", "buildSrc")
              for pattern in ("*.gradle.kts", "*.kt")
              for path in (gradle_root / folder).rglob(pattern)
              if (gradle_root / folder).is_dir()),
        })
        for path in candidates:
            try:
                relative_parts = path.relative_to(gradle_root).parts[:-1]
            except ValueError:
                continue
            if any(part in ignored_dirs for part in relative_parts):
                continue
            original = path.read_bytes()
            updated = original
            for pattern in _MINIFICATION_ASSIGNMENTS:
                updated = pattern.sub(rb"\1false", updated)
            updated = _COMPUTED_MINIFICATION.sub(_computed_flag_disabled, updated)
            if updated != original:
                originals[path] = original
                path.write_bytes(updated)
                log.write(f"Temporarily disabled minification in: {path}\n")
        log.flush()
        yield
    finally:
        for path, original in originals.items():
            path.write_bytes(original)
        if originals:
            log.write(
                f"Restored {len(originals)} Gradle build file(s) after build.\n"
            )
            log.flush()


def find_apks(gradle_root: Path, started_at: float, succeeded: bool = False) -> List[Path]:
    """APKs this build wrote, or, after a build Gradle found up to date, its outputs.

    The age filter keeps an earlier build's leftovers out. But a successful
    rebuild of an unchanged checkout writes nothing -- every task UP-TO-DATE
    -- and AmaRadio's second build (2026-10-16) was reported as producing no
    APK. Gradle vouches for the outputs it skipped, so they are this build's.
    """
    apks, existing = [], []
    for apk in gradle_root.rglob("build/outputs/apk/**/*.apk"):
        try:
            existing.append(apk)
            if apk.stat().st_mtime >= started_at - 2:
                apks.append(apk)
        except OSError:
            continue
    if not apks and succeeded:
        return sorted(existing)
    return sorted(apks)


def copy_apks(project_name: str, apks: Sequence[Path], output_dir: Path) -> List[str]:
    project_output = output_dir / "apks" / project_name
    project_output.mkdir(parents=True, exist_ok=True)

    copied = []
    for apk in apks:
        destination = project_output / apk.name
        # Only two APKs of this build sharing a name are told apart. A file an
        # earlier build left here is replaced: keeping it beside a renamed copy
        # gave fdroid-build two candidates for one flavour (AmaRadio and
        # WiFiAnalyzer, 2026-10-16) and neither was taken.
        if str(destination) in copied:
            destination = project_output / f"{apk.parent.name}-{apk.name}"
        shutil.copy2(apk, destination)
        copied.append(str(destination))

    return copied


def declared_submodule_paths(gradle_root: Path) -> list[Path]:
    """Return only the checkout paths explicitly declared by .gitmodules."""
    gitmodules = gradle_root / ".gitmodules"
    if not gitmodules.is_file():
        return []
    paths = []
    for line in gitmodules.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\s*path\s*=\s*(.+?)\s*$", line)
        if not match:
            continue
        candidate = (gradle_root / match.group(1)).resolve()
        try:
            candidate.relative_to(gradle_root.resolve())
        except ValueError:
            continue
        paths.append(candidate)
    return paths


def worktree_git_dir(gradle_root: Path) -> Optional[Path]:
    """Find the Git directory for this checkout without assuming .git is a dir."""
    completed = subprocess.run(
        ["git", "rev-parse", "--git-dir"], cwd=gradle_root,
        capture_output=True, text=True, check=False,
    )
    if completed.returncode != 0:
        return None
    git_dir = Path(completed.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = gradle_root / git_dir
    return git_dir.resolve()


def corrupt_submodule_configs(gradle_root: Path) -> list[Path]:
    """Locate binary-corrupted config files in this linked worktree's modules.

    A Git config is text. NUL bytes are therefore unambiguous evidence of a
    damaged file, unlike an ordinary submodule/network error.
    """
    git_dir = worktree_git_dir(gradle_root)
    module_root = git_dir / "modules" if git_dir else None
    if not module_root or not module_root.is_dir():
        return []
    corrupt = []
    for config in module_root.rglob("config"):
        try:
            if b"\0" in config.read_bytes():
                corrupt.append(config)
        except OSError:
            continue
    return corrupt


def recover_corrupt_submodules(
    gradle_root: Path, recovery_root: Path, log
) -> list[str]:
    """Quarantine corrupted pipeline-worktree submodules for a clean re-init.

    Nothing is deleted: the broken metadata and its checkout are moved to the
    build output so they remain available for inspection.  We repair only a
    module whose Git metadata path exactly corresponds to a path declared in
    .gitmodules; this avoids touching unrelated Git internals.
    """
    git_dir = worktree_git_dir(gradle_root)
    if git_dir is None:
        return []
    module_root = git_dir / "modules"
    declared = {path.relative_to(gradle_root.resolve()).as_posix(): path
                for path in declared_submodule_paths(gradle_root)}
    recovered = []
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    for config in corrupt_submodule_configs(gradle_root):
        try:
            relative = config.parent.relative_to(module_root).as_posix()
        except ValueError:
            continue
        checkout = declared.get(relative)
        if checkout is None:
            log.write(
                f"Not auto-repairing unknown corrupt submodule metadata: {config}\n"
            )
            continue
        quarantine = recovery_root / stamp / relative
        metadata = config.parent
        try:
            quarantine.mkdir(parents=True, exist_ok=True)
            shutil.move(str(metadata), str(quarantine / "git_metadata"))
            if checkout.exists():
                shutil.move(str(checkout), str(quarantine / "checkout"))
        except OSError as exc:
            log.write(f"Could not quarantine corrupted submodule {relative}: {exc}\n")
            continue
        recovered.append(relative)
        log.write(
            f"Quarantined corrupt submodule '{relative}' in {quarantine}; "
            "Git will initialize a clean copy.\n"
        )
    log.flush()
    return recovered


# Two declared submodule transports this build environment can never reach,
# whatever the target repository:
#
#  * git:// -- GitHub permanently disabled the unauthenticated protocol (port
#    9418) in 2022, and it is deprecated or gone on most other hosts too, so a
#    submodule URL an older repository still declares this way times out
#    rather than failing fast.
#  * git@github.com: / ssh://git@github.com/ -- this build has no SSH key or
#    known_hosts entry configured for any host, so an SCP-style or explicit
#    ssh:// GitHub submodule URL fails just as hard, immediately and for a
#    different reason ("Host key verification failed", not a timeout).
#    TagMo's amiitool submodule declares exactly this form
#    (run_20260927_175748).
#
# Each rewrite is a local `insteadOf` config, so .gitmodules is left alone and
# nothing about the checked-out revision changes: only the transport used to
# fetch it. https serves the same public repositories every one of these
# would otherwise be fetched from.
_SUBMODULE_URL_REWRITES: tuple[tuple[str, str], ...] = (
    ("git://", "https://"),
    ("git@github.com:", "https://github.com/"),
    ("ssh://git@github.com/", "https://github.com/"),
)


def declared_submodule_urls(gradle_root: Path) -> list[str]:
    """Every url declared in the repository's .gitmodules, in file order."""
    gitmodules = gradle_root / ".gitmodules"
    try:
        content = gitmodules.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [
        match.group(1).strip()
        for match in re.finditer(r"^\s*url\s*=\s*(\S+)", content, re.MULTILINE)
    ]


def rewrite_dead_submodule_protocols(
    gradle_root: Path, log
) -> tuple[list[str], Optional[dict]]:
    """Route submodule fetches this build cannot reach over https instead.

    Returns the affected urls, in the order .gitmodules declares them, and the
    environment to run the submodule commands under -- or ([], None) when
    nothing declared needs rewriting.

    The rewrite has to travel in the environment rather than in the
    repository's config. `git submodule update` clones each submodule with a
    child `git clone`, and a clone creating a new repository reads only
    system and global config -- never the superproject's `--local` -- so an
    insteadOf set there is silently ignored. Verified on git 2.43: the same
    clone that fails with the local config succeeds with GIT_CONFIG_COUNT,
    which every child process inherits. Nothing outside this build is
    touched, so the user's own git configuration is left alone.

    A repository can declare more than one of these transports at once (or
    several submodules on the same one); every distinct transport actually
    found gets exactly one `insteadOf` entry, covering every url that shares
    it.
    """
    affected: list[str] = []
    rules_needed: dict[str, str] = {}
    for url in declared_submodule_urls(gradle_root):
        for from_prefix, to_prefix in _SUBMODULE_URL_REWRITES:
            if url.startswith(from_prefix):
                affected.append(url)
                rules_needed.setdefault(from_prefix, to_prefix)
                break
    if not affected:
        return [], None
    env = os.environ.copy()
    try:
        index = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        index = 0
    for from_prefix, to_prefix in rules_needed.items():
        env["GIT_CONFIG_KEY_%d" % index] = f"url.{to_prefix}.insteadOf"
        env["GIT_CONFIG_VALUE_%d" % index] = from_prefix
        index += 1
    env["GIT_CONFIG_COUNT"] = str(index)
    log.write(
        "Declared submodule url(s) use a transport this build cannot reach; "
        "routing them over https for this build: "
        + ", ".join(affected) + "\n"
    )
    log.flush()
    return affected, env


# Errors in which the server answered definitively: the repository or the
# pinned ref is not there, and no amount of retrying changes that. They are
# kept apart from the ones that say only that this machine could not reach
# the server just now -- a timeout, an unresolved host, a refused connection
# -- because recording one of those as permanent would blacklist a project
# over a network blip.
_NONRECOVERABLE_SUBMODULE_ERRORS = (
    "not our ref",
    "repository not found",
    "not found",
    "does not exist",
    "did not match any file(s) known to git",
    "reference is not a tree",
)
_TRANSIENT_SUBMODULE_ERRORS = (
    "timed out",
    "could not resolve host",
    "connection refused",
    "connection reset",
    "early eof",
    "unable to access",
    "operation timed out",
)
_UNBUILDABLE_RECORD = "tcc-unbuildable.json"


def _git_common_dir(gradle_root: Path) -> Optional[Path]:
    """The clone's own .git, shared by every worktree cut from it.

    Nothing written there can reach a diff or a pull request, and it lives
    exactly as long as the clone does, so a finding worktree and the clone
    it came from read the same record without any path being threaded
    through the pipeline's arguments.
    """
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=gradle_root, capture_output=True, text=True,
            timeout=60, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    common = Path(completed.stdout.strip())
    if not common.is_absolute():
        common = gradle_root / common
    return common if common.is_dir() else None


def nonrecoverable_submodule_reason(log_file: Path) -> str:
    """A definitive server rejection in the submodule log, or "".

    Returns "" when the log also shows a transient failure: the run cannot
    tell which of the two decided the outcome, and guessing permanent is the
    expensive mistake.
    """
    try:
        output = log_file.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return ""
    if any(marker in output for marker in _TRANSIENT_SUBMODULE_ERRORS):
        return ""
    for marker in _NONRECOVERABLE_SUBMODULE_ERRORS:
        if marker in output:
            return marker.replace(" ", "_")
    return ""


def record_unbuildable(gradle_root: Path, reason: str, detail: str) -> None:
    """Remember that this checkout cannot be built, and why."""
    common = _git_common_dir(gradle_root)
    if common is None:
        return
    try:
        (common / _UNBUILDABLE_RECORD).write_text(
            json.dumps({
                "reason": reason,
                "detail": detail,
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
            }, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def recorded_unbuildable_reason(gradle_root: Path) -> str:
    """Why a previous run concluded this checkout cannot be built, or ""."""
    common = _git_common_dir(gradle_root)
    if common is None:
        return ""
    record = common / _UNBUILDABLE_RECORD
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("detail") or data.get("reason") or "")


def run_submodule_commands(
    gradle_root: Path, log, timeout: int, env: Optional[dict] = None,
    force: bool = False,
) -> Optional[str]:
    # ``force``: a submodule a killed run left dirty would abort the update
    # of a checkout that was itself reset by force (fdroid.build_from_source).
    commands = (
        ["git", "submodule", "sync", "--recursive"],
        ["git", "submodule", "update", "--init", "--recursive", *(["--force"] if force else [])],
    )
    # A private submodule must fail at once, not wait for credentials:
    # Element X's enterprise submodule failed authentication, git retried,
    # and the retry sat on a prompt until the 3600 s timeout (2026-10-27).
    env = {**(env if env is not None else os.environ), "GIT_TERMINAL_PROMPT": "0"}
    for command in commands:
        if network_subcommand(command) is None:
            try:
                completed = subprocess.run(
                    command, cwd=gradle_root, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=subprocess.STDOUT, text=True,
                    timeout=timeout, check=False, env=env,
                )
            except subprocess.TimeoutExpired:
                return f"submodule_initialization_timeout_after_{timeout}s"
        else:
            # The update fetches: bounded, retried once when it stalls, and
            # its output read here so a stall can be told from a refusal
            # (AntennaPod's StoreMetadata fetch sat on a connection the
            # standby had killed for over an hour, 2026-10-29).
            # The build's own timeout (an hour) is no measure of a fetch.
            limit = min(timeout, network_git_timeout(command))
            try:
                completed = run_network_git(
                    command, limit, env=env, log=log, cwd=gradle_root,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, check=False,
                )
            except subprocess.TimeoutExpired as exc:
                _write_output(log, exc.stdout)
                return f"submodule_initialization_timeout_after_{limit}s"
            _write_output(log, getattr(completed, "stdout", None))
        if completed.returncode != 0:
            return f"submodule_initialization_return_code_{completed.returncode}"
    return None


def _write_output(log, output) -> None:
    if log is None or not isinstance(output, (str, bytes)) or not output:
        return
    log.write(output.decode("utf-8", errors="replace")
              if isinstance(output, bytes) else str(output))
    log.flush()


def initialize_submodules(
    gradle_root: Path, log, timeout: int, recovery_root: Path
) -> tuple[Optional[str], list[str]]:
    """Populate declared submodules in a clone or finding worktree.

    Git worktrees intentionally start with empty submodule directories even when
    the parent repository declares them. Gradle then includes those directories
    as projects, but they expose no Android variants, producing misleading
    ``No matching configuration`` errors. Initialize them before invoking Gradle
    and report setup failures separately from compilation failures.
    """
    gitmodules = gradle_root / ".gitmodules"
    if not gitmodules.is_file():
        return None, []

    log.write("Preparing declared Git submodules recursively.\n")
    log.flush()
    dead_urls, submodule_env = rewrite_dead_submodule_protocols(
        gradle_root, log
    )
    failure = run_submodule_commands(
        gradle_root, log, timeout, submodule_env
    )
    if failure is None:
        return None, []
    if dead_urls:
        # Named rather than left as a bare exit code, and named as what it
        # is: the rewrite was applied and the failure is whatever survived
        # it. gtalksms declares
        # git://github.com/JakeWharton/ActionBarSherlock.git/actionbarsherlock,
        # whose path segment after .git is not a repository on any protocol;
        # org.yaxim's url resolves over https but its pinned commit no
        # longer exists upstream. Neither is a protocol problem any more,
        # and a reason claiming otherwise would send the next reader after
        # the wrong thing.
        failure = (
            f"{failure}_after_git_protocol_rewrite:{','.join(dead_urls)}"
        )

    corrupt = corrupt_submodule_configs(gradle_root)
    if not corrupt:
        return unless_unused_by_build(gradle_root, failure, log), []
    log.write(
        "Detected NUL bytes in linked-worktree submodule config; attempting "
        "one recoverable re-initialization.\n"
    )
    recovered = recover_corrupt_submodules(gradle_root, recovery_root, log)
    if not recovered:
        return unless_unused_by_build(gradle_root, failure, log), []
    retry = run_submodule_commands(
        gradle_root, log, timeout, submodule_env
    )
    return unless_unused_by_build(gradle_root, retry, log), recovered


def unsettled_submodule_paths(gradle_root: Path) -> list[Path]:
    """Declared submodules not checked out at their recorded commit.

    `git submodule status` marks one never initialised with "-", one at
    another commit with "+" and one in conflict with "U": AntennaPod's
    StoreMetadata was cloned, then the fetch of its pinned commit hung.
    Without git's answer, the declared submodules left empty.
    """
    declared = declared_submodule_paths(gradle_root)
    try:
        listed = subprocess.run(
            ["git", "submodule", "status"], cwd=gradle_root,
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        listed = None
    if listed is None or listed.returncode != 0:
        return [path for path in declared
                if not path.is_dir() or not any(path.iterdir())]
    root = gradle_root.resolve()
    unsettled = []
    for line in (listed.stdout or "").splitlines():
        parts = line[1:].split()
        if line[:1] in ("-", "+", "U") and len(parts) >= 2:
            unsettled.append((root / parts[1]).resolve())
    return unsettled


# Files through which a Gradle build reaches other directories: its scripts,
# properties and version catalogs, and the native build files they hand to
# CMake or ndk-build (app/build.gradle -> src/main/cpp/CMakeLists.txt ->
# add_subdirectory(../third_party/opus)).
_BUILD_REFERENCE_NAMES = frozenset({
    "gradle.properties", "CMakeLists.txt", "Android.mk", "Application.mk",
})
_BUILD_REFERENCE_SUFFIXES = (".gradle", ".gradle.kts", ".cmake", ".versions.toml")
# Output and cache directories, never sources of a reference.
_BUILD_REFERENCE_SKIPPED_DIRECTORIES = frozenset({
    ".git", ".gradle", ".idea", ".cxx", "build", "node_modules",
})


def build_reference_files(gradle_root: Path, skip: Iterable[Path] = ()) -> Iterable[Path]:
    """Every build or native build file under the Gradle root, outside `skip`."""
    skipped = {Path(path).resolve() for path in skip}
    for directory, subdirectories, files in os.walk(gradle_root):
        current = Path(directory)
        subdirectories[:] = sorted(
            name for name in subdirectories
            if name not in _BUILD_REFERENCE_SKIPPED_DIRECTORIES
            and (current / name).resolve() not in skipped
        )
        for name in sorted(files):
            if name in _BUILD_REFERENCE_NAMES or name.endswith(_BUILD_REFERENCE_SUFFIXES):
                yield current / name


def submodule_used_by_build(gradle_root: Path, submodule: Path) -> bool:
    """Whether the Gradle build can need this submodule's files.

    Used when it sits inside a module (a directory below the root with its
    own build script: sources, assets, native code), or when any build file
    under the root -- Gradle scripts, properties, version catalogs, and the
    CMake or ndk-build files they reach -- names its path or directory name
    (includeBuild, srcDirs, a project included from it, add_subdirectory).
    Anything else -- a store-metadata or documentation submodule at the top
    level, like AntennaPod's StoreMetadata -- the build never reads.
    """
    root = gradle_root.resolve()
    try:
        relative = submodule.resolve().relative_to(root)
    except ValueError:
        return True
    parent = (root / relative).parent
    while parent != root:
        if any((parent / name).is_file() for name in _BUILD_SCRIPT_FILES):
            return True
        parent = parent.parent
    names = {relative.as_posix(), relative.name}
    # The submodule's own files (empty, or a partial checkout) say nothing
    # about whether the build reads it.
    for path in build_reference_files(root, skip=[root / relative]):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", text)
               for name in names):
            return True
    return False


_BUILD_SCRIPT_FILES = ("build.gradle", "build.gradle.kts", "CMakeLists.txt")


def unless_unused_by_build(gradle_root: Path, failure: Optional[str], log) -> Optional[str]:
    """`failure`, or None when every submodule left unsettled is one the build never reads.

    A submodule update that still fails after its bounded retries would stop
    the build as an infrastructure failure, although Gradle needs nothing
    from a submodule no build file names. The build then goes on without it,
    and the log says which.
    """
    if failure is None:
        return None
    unsettled = unsettled_submodule_paths(gradle_root)
    if not unsettled or any(submodule_used_by_build(gradle_root, path)
                            for path in unsettled):
        return failure
    names = ", ".join(
        os.path.relpath(path, gradle_root.resolve()).replace(os.sep, "/")
        for path in unsettled
    )
    log.write(
        f"Submodule setup failed ({failure}), but no build file uses {names}; "
        "continuing without it.\n"
    )
    log.flush()
    return None


# P4OC (2026-11-07): Java compiled for its declared 17 while Kotlin, with no
# jvmTarget of its own, took the JDK's 21. On the JDK of the Java target both agree.
_INCONSISTENT_JVM_TARGET = re.compile(
    r"Inconsistent JVM-target compatibility detected for tasks '[^']+' \((\d+)\) and '[^']+' \((\d+)\)")


def jdk_to_retry_on(result) -> Optional[int]:
    """The JDK feature version a build failing on its JDK is retried on, else None."""
    if result.reason in ("incompatible_build_jdk_for_kotlin_plugin",
                         "incompatible_build_jdk_for_android_jdk_image"):
        return 17
    if result.reason != "inconsistent_jvm_target_of_build_jdk":
        return None
    try:
        match = _INCONSISTENT_JVM_TARGET.search(
            Path(result.log_file).read_text(encoding="utf-8", errors="replace"))
    except (OSError, TypeError):
        return None
    return min(int(match.group(1)), int(match.group(2))) if match else None


def classify_gradle_failure(log_file: Path) -> Optional[str]:
    """Recognize host/setup failures that are not evidence against a patch."""
    try:
        output = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if re.search(r"Unknown Kotlin JVM target:\s*\d+", output):
        return "incompatible_build_jdk_for_kotlin_plugin"
    # An Android Gradle Plugin older than 8.2.1 runs jlink on
    # core-for-system-modules.jar, which the jlink of JDK 21+ refuses
    # (Courier, 2026-11-07, compileSdk 34 on JDK 21). The same retry on JDK 17.
    jlink = re.search(r"JdkImageTransform.*?(\S*/bin/jlink)", output, re.DOTALL)
    if jlink and not re.search(r"(?:jdk|java)-?17\b|-17[.-]", jlink.group(1)):
        return "incompatible_build_jdk_for_android_jdk_image"
    if _INCONSISTENT_JVM_TARGET.search(output):
        return "inconsistent_jvm_target_of_build_jdk"
    if (
        "No matching configuration of project" in output
        and "None of the consumable configurations have attributes" in output
    ):
        return "unavailable_or_unconfigured_gradle_subproject"
    return None


@contextlib.contextmanager
def exclusive_build_lock(gradle_root: Path, log):
    """Serialize builds targeting the same checkout/worktree."""
    lock_root = Path(tempfile.gettempdir()) / "tcc-pipeline-build-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(
        str(gradle_root.resolve()).encode("utf-8")
    ).hexdigest()[:20]
    lock_path = lock_root / f"{identity}.lock"
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        if os.name != "nt":
            import fcntl
            log.write(f"Waiting for build lock: {lock_path}\n")
            log.flush()
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            log.write(f"Acquired build lock: {lock_path}\n")
            log.flush()
        yield
    finally:
        if os.name != "nt":
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


# Gradle's end-of-build line, and what Gradle itself may print after it.
# Over the 251 stored build logs with that line (2026-10-29), what followed
# it was the actionable-tasks summary, "Configuration cache entry
# stored/reused." and Gradle 9's "Consider enabling configuration cache"
# hint: 77 logs carried one of the last two, and a hung client there was
# recorded as a full timeout. The build-scan lines and the configuration
# cache report pointer are Gradle's own post-summary output too.
_END_OF_BUILD = re.compile(r"^BUILD (SUCCESSFUL|FAILED) in ")
_AFTER_END_OF_BUILD = re.compile(
    r"^(?:"
    r"\d+ actionable tasks?:.*"
    r"|Configuration cache entry (?:stored|reused|discarded).*"
    r"|Consider enabling configuration cache.*"
    r"|\d+ problems? (?:was|were) found \w+ the configuration cache.*"
    r"|See the complete report at .*"
    r"|Publishing (?:a )?build scan.*"
    r"|https://gradle\.com/s/\S+"
    r")?$")
# How long the log must stay quiet after that line before a client that has
# not exited is judged by it. An included or nested build prints its own
# output after the outer one's line, so the line must also be the last thing
# Gradle wrote.
HUNG_CLIENT_GRACE_SECONDS = 120


def finished_build_outcome(log_file: Path) -> Optional[str]:
    """"SUCCESSFUL" or "FAILED" when Gradle's end-of-build line is its last output."""
    try:
        with open(log_file, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 16384))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        stripped = line.strip()
        match = _END_OF_BUILD.match(stripped)
        if match:
            return match.group(1)
        if not _AFTER_END_OF_BUILD.match(stripped):
            return None
    return None


@dataclass
class GradleRun:
    """How a Gradle client ended: "exited", "timeout", or "hung_after_<outcome>"."""

    return_code: Optional[int]
    outcome: str


def _log_process_tree(process: subprocess.Popen, log) -> None:
    """Write the processes of the client's session to the log, for the record."""
    if os.name == "nt":
        return
    try:
        listing = subprocess.run(
            ["ps", "-o", "pid,ppid,etime,args", "-s", str(process.pid)],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return
    if listing.strip():
        log.write("Processes still running:\n")
        log.write("".join(line[:300] + "\n" for line in listing.splitlines()))


def kill_process_group(process: subprocess.Popen, term_grace: float = 5.0) -> None:
    """Stop a client started in its own session, with every process it started.

    Killing only the client leaves the Gradle JVM and its workers running:
    each one holds gigabytes of WSL's memory until the machine restarts.
    """
    if os.name == "nt":
        process.kill()
    else:
        group = process.pid
        try:
            os.killpg(group, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.monotonic() + term_grace
        while time.monotonic() < deadline:
            process.poll()
            try:
                os.killpg(group, 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.1)
        try:
            os.killpg(group, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_gradle(
    cmd: Sequence[str], cwd: Path, env: dict, log, log_file: Path,
    timeout: float, grace: float = HUNG_CLIENT_GRACE_SECONDS,
    poll_interval: float = 1.0,
) -> GradleRun:
    """Run a Gradle client to its end, writing its output to `log`.

    A client can print "BUILD SUCCESSFUL" and never exit: Salty's
    clean assembleDebug (2026-10-29) finished in 4m 56s and was recorded as
    an hour-long timeout, then rebuilt as a compile-only check. When the
    end-of-build line is the last output, the log has not changed for
    `grace` seconds and the client is still alive, the process group is
    stopped and the line decides the outcome. A real timeout stops the
    whole group too.
    """
    log.flush()
    process = subprocess.Popen(
        list(cmd), cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
        text=True, start_new_session=os.name != "nt",
    )
    started = time.monotonic()
    last_size, last_change = -1, started
    # The client runs in its own session, out of reach of the step's group
    # kill. Whatever stops this process -- the step deadline or stall watch
    # (SIGTERM, turned into SystemExit by install_termination_handler), an
    # operator's Ctrl+C -- stops the client's group on the way out, or the
    # Gradle JVM and its workers outlive the build holding WSL's memory.
    try:
        while True:
            try:
                return GradleRun(process.wait(timeout=poll_interval), "exited")
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            try:
                size = os.path.getsize(log_file)
            except OSError:
                size = last_size
            if size != last_size:
                last_size, last_change = size, now
            if now - started >= timeout:
                kill_process_group(process)
                return GradleRun(None, "timeout")
            if now - last_change >= grace:
                finished = finished_build_outcome(log_file)
                if finished:
                    log.write(
                        f"\nGradle printed BUILD {finished} and wrote nothing for "
                        f"{int(now - last_change)} seconds, but the client did not "
                        "exit; stopping its process group and taking the outcome "
                        "from that line.\n"
                    )
                    _log_process_tree(process, log)
                    log.flush()
                    kill_process_group(process)
                    return GradleRun(None, f"hung_after_{finished.lower()}")
    except BaseException:
        kill_process_group(process)
        raise


def _exit_on_termination(signum, frame):  # noqa: ARG001 -- signal handler signature
    raise SystemExit(128 + signum)


def install_termination_handler() -> None:
    """Turn SIGTERM into SystemExit, so run_gradle's cleanup runs.

    The pipeline stops a step with SIGTERM to the step's process group
    (process_tree.terminate_tree); by default that ends this process at
    once and leaves the Gradle client, in its own session, running.
    """
    import threading

    if os.name != "nt" and threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _exit_on_termination)


def build_outcome(
    task_line: str,
    return_code: int,
    apk_count: int,
    infrastructure_reason: Optional[str],
) -> Tuple[str, str]:
    """Decide a build's status and reason from what the task promised.

    A unit-test or compile-only task legitimately produces no APK, so
    requiring one marks a passing build as failed. That is how the giggity
    baseline came back status=failed with return_code=0 and
    reason="no_apk_found_after_successful_build": a 65-second build was
    discarded, and an empty-diagnostics comparison against such a baseline
    would have mis-attributed a patch failure to pre-existing test debt.
    """
    produces_apk = not any(
        marker in task_line.lower()
        for marker in ("test", "compile", "check", "lint", "ktlint")
    )
    if infrastructure_reason:
        return "infrastructure_failure", infrastructure_reason
    if return_code != 0:
        return "failed", f"gradle_return_code_{return_code}"
    if not apk_count and produces_apk:
        return "failed", "no_apk_found_after_successful_build"
    return "success", ""


# Both conventions seen in the corpus: projects that hardcode keyAlias
# "android" and projects that reuse the Android debug-keystore alias. One
# keystore carries both entries so a single generated file satisfies either.
_SIGNING_ALIASES = ("android", "androiddebugkey")
# The documented Android debug-keystore password. Projects that hardcode a
# password at all hardcode this one.
_SIGNING_PASSWORD = "android"


def ensure_validation_keystore(cache_dir: Path, env: dict, log) -> Optional[Path]:
    """Generate, once, a throwaway keystore for release signing configs.

    Nothing signed with it is ever distributed: it exists so a project whose
    build script reads a keystore out of local.properties can configure at all.
    Returns None when keytool is unavailable, leaving the caller to write a
    local.properties without signing entries.
    """
    keystore = cache_dir / "validation.jks"
    if keystore.is_file():
        return keystore
    keytool = shutil.which("keytool", path=env.get("PATH", os.environ.get("PATH")))
    if not keytool:
        log.write("No keytool on PATH; skipping signing-config provisioning\n")
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    for alias in _SIGNING_ALIASES:
        result = subprocess.run(
            [
                keytool, "-genkeypair", "-noprompt",
                "-keystore", str(keystore),
                "-storepass", _SIGNING_PASSWORD,
                "-keypass", _SIGNING_PASSWORD,
                "-alias", alias,
                "-dname", "CN=tcc-validation, OU=validation, O=tcc, C=BR",
                "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
            ],
            capture_output=True, text=True, env=env, timeout=120,
        )
        if result.returncode != 0:
            log.write(
                f"keytool failed for alias {alias}: {result.stderr[:400]}\n"
            )
            return None
    return keystore if keystore.is_file() else None


def ensure_local_properties(gradle_root: Path, android_sdk: str,
                            keystore: Optional[Path], log) -> None:
    """Provide the local.properties an Android build expects to find.

    local.properties is per-developer and gitignored, so a freshly cloned
    project never has one, and a build script that reads it fails during
    configuration -- before a single source file is compiled, which means no
    generated patch can be validated at all. hanjoongcho/aaf-easydiary is the
    observed case: app/build.gradle.kts loads local.properties unconditionally
    and casts storePassword/storeFile to String, so both the patched worktree
    and the unmodified baseline died with FileNotFoundException.

    An existing file is never touched: it belongs to whoever put it there.
    """
    path = gradle_root / "local.properties"
    if path.exists():
        return
    # .properties treats backslash as an escape, so a Windows path has to be
    # written with forward slashes to survive the round trip. Path.as_posix()
    # does NOT do this: on Linux -- where this pipeline runs -- PosixPath does
    # not treat backslash as a separator and returns the string untouched.
    def properties_path(value) -> str:
        return str(value).replace("\\", "/")

    lines = []
    if android_sdk:
        lines.append(f"sdk.dir={properties_path(android_sdk)}")
    if keystore is not None:
        lines += [
            f"storeFile={properties_path(keystore)}",
            f"storePassword={_SIGNING_PASSWORD}",
            f"keyAlias={_SIGNING_ALIASES[0]}",
            f"keyPassword={_SIGNING_PASSWORD}",
        ]
    if not lines:
        return
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.write(f"Provisioned {path} ({len(lines)} propert(y/ies))\n")


def run_build(
    project_dir: Path,
    gradle_root: Path,
    task: str,
    init_script: Optional[Path],
    output_dir: Path,
    args: argparse.Namespace,
) -> BuildResult:
    logs_dir = output_dir / "logs" / "projects"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"{project_dir.name}.log"

    cmd = gradle_command(gradle_root)
    if args.clean_first:
        cmd.append("clean")
    cmd.append(task)
    cmd.extend(args.additional_task)
    if init_script is not None:
        cmd.extend(["-I", str(init_script)])
    cmd.extend(f"-P{item}" for item in getattr(args, "gradle_property", []) or [])
    cmd.extend(["--no-daemon", "--stacktrace"])
    if getattr(args, "continue_after_task_failure", False):
        cmd.append("--continue")
    if getattr(args, "gradle_build_cache", False):
        cmd.append("--build-cache")
    if args.refresh_dependencies:
        cmd.append("--refresh-dependencies")

    env = os.environ.copy()
    env.setdefault("ORG_GRADLE_PROJECT_android.injected.build.model.only.versioned", "3")
    env["PATH"] = with_cargo_on_path(env.get("PATH", ""))
    if args.android_sdk:
        android_sdk = str(Path(args.android_sdk).expanduser().resolve())
        env["ANDROID_HOME"] = android_sdk
        env["ANDROID_SDK_ROOT"] = android_sdk
    java_home = args.java_home
    if not java_home and not env.get("JAVA_HOME"):
        # Left to the default java, an old Gradle wrapper fails on a newer JDK
        # before the build is read; that failure says nothing about the patch.
        java_home = compatible_java_home(gradle_root)
    if java_home:
        java_home = str(Path(java_home).expanduser().resolve())
        env["JAVA_HOME"] = java_home
        env["PATH"] = str(Path(java_home) / "bin") + os.pathsep + env.get("PATH", "")

    started_at = time.time()
    started_iso = datetime.now().isoformat(timespec="seconds")

    # Before the lock, the SDK provisioning and Gradle: a checkout a previous
    # run proved unbuildable costs nothing to skip and minutes to re-attempt.
    previously_unbuildable = (
        "" if args.skip_submodules
        else recorded_unbuildable_reason(gradle_root)
    )
    if previously_unbuildable:
        with open(log_file, "w", encoding="utf-8", errors="replace") as log:
            log.write(f"Started: {started_iso}\n")
            log.write(f"Project: {project_dir}\n")
            log.write(
                "Skipped: a previous run recorded this checkout as "
                f"unbuildable ({previously_unbuildable}). Delete "
                f"{_UNBUILDABLE_RECORD} from the clone's .git directory to "
                "try again.\n"
            )
        return BuildResult(
            project_name=project_dir.name,
            project_dir=str(project_dir),
            gradle_root=str(gradle_root),
            task=task,
            status="infrastructure_failure",
            return_code=None,
            duration_seconds=round(time.time() - started_at, 2),
            apk_count=0,
            copied_apks=[],
            log_file=str(log_file),
            reason=f"previously_recorded_unbuildable:{previously_unbuildable}",
        )

    with open(log_file, "w", encoding="utf-8", errors="replace") as log:
        log.write(f"Started: {started_iso}\n")
        log.write(f"Project: {project_dir}\n")
        log.write(f"Gradle root: {gradle_root}\n")
        if args.android_sdk:
            log.write(f"Android SDK: {env['ANDROID_HOME']}\n")
        if java_home:
            log.write(f"JAVA_HOME: {env['JAVA_HOME']}\n")
        log.write(f"Command: {' '.join(cmd)}\n\n")
        log.flush()

        with exclusive_build_lock(gradle_root, log):
            # Inside the lock: two findings can share one gradle_root, and
            # both would otherwise race to create the same local.properties.
            ensure_local_properties(
                gradle_root,
                env.get("ANDROID_HOME", ""),
                ensure_validation_keystore(
                    Path(args.projects_dir).resolve().parent
                    / "validation_signing",
                    env, log,
                ),
                log,
            )
            if not args.skip_submodules:
                submodule_error, recovered_submodules = initialize_submodules(
                    gradle_root,
                    log,
                    args.timeout,
                    output_dir / "submodule_recovery" / project_dir.name,
                )
                if submodule_error:
                    duration = time.time() - started_at
                    log.write(f"\nSubmodule setup failed: {submodule_error}\n")
                    # A server that answered "not found" will answer it
                    # again. Recording that spares every future run the
                    # whole attempt; a failure that only says this machine
                    # could not reach the server is left unrecorded.
                    permanent = nonrecoverable_submodule_reason(log_file)
                    if permanent:
                        record_unbuildable(
                            gradle_root, permanent, submodule_error
                        )
                        log.write(
                            "Recorded as unbuildable: the server answered "
                            f"definitively ({permanent}), so later runs will "
                            "skip this checkout instead of retrying it.\n"
                        )
                    return BuildResult(
                        project_name=project_dir.name,
                        project_dir=str(project_dir),
                        gradle_root=str(gradle_root),
                        task=task,
                        status="infrastructure_failure",
                        return_code=None,
                        duration_seconds=round(duration, 2),
                        apk_count=0,
                        copied_apks=[],
                        log_file=str(log_file),
                        reason=submodule_error,
                    )
                if recovered_submodules:
                    log.write(
                        "Submodule recovery succeeded for: "
                        f"{', '.join(recovered_submodules)}\n"
                    )
            with disabled_minification_in_build_files(
                gradle_root, init_script is not None, log,
            ):
                # Innermost: git does not recognise a worktree while its .git
                # is the shim, so the window holds the Gradle process alone.
                # The shim goes on the checkout, which holds a Gradle root
                # in a subdirectory (android/).
                with git_directory_for_build(gradle_root, project_dir) as git_shim:
                    if git_shim is not None:
                        log.write(
                            "Build scripts use .git/ "
                            f"({', '.join(git_shim['build_scripts_reading_git'])}); "
                            + ("building with a temporary .git directory.\n"
                               if git_shim["applied"] else
                               ".git is already a directory or cannot be shimmed.\n")
                        )
                    completed = run_gradle(
                        cmd, gradle_root, env, log, log_file, args.timeout,
                        grace=getattr(args, "hung_client_grace", HUNG_CLIENT_GRACE_SECONDS),
                    )
                if completed.outcome == "timeout":
                    duration = time.time() - started_at
                    log.write(f"\nTimed out after {args.timeout} seconds.\n")
                    return BuildResult(
                        project_name=project_dir.name,
                        project_dir=str(project_dir),
                        gradle_root=str(gradle_root),
                        task=task,
                        status="timeout",
                        return_code=None,
                        duration_seconds=round(duration, 2),
                        apk_count=0,
                        copied_apks=[],
                        log_file=str(log_file),
                        reason=f"timeout_after_{args.timeout}s",
                        git_directory_shim=git_shim,
                    )

    duration = time.time() - started_at
    # A client stopped after "BUILD SUCCESSFUL" is recorded with Gradle's own
    # verdict, return code 0, so every reader of the record treats it as the
    # success Gradle reported; the reason says how it ended.
    return_code = (
        0 if completed.outcome == "hung_after_successful"
        else completed.return_code
    )
    succeeded = return_code == 0
    apks = find_apks(gradle_root, started_at, succeeded=succeeded)
    copied = copy_apks(project_dir.name, apks, output_dir) if apks else []

    infrastructure_reason = (
        classify_gradle_failure(log_file) if not succeeded else None
    )
    status, reason = build_outcome(
        " ".join([task, *args.additional_task]),
        return_code if return_code is not None else 1,
        len(copied),
        infrastructure_reason,
    )
    if (completed.outcome, status) in {("hung_after_successful", "success"),
                                       ("hung_after_failed", "failed")}:
        reason = f"gradle_client_hung_after_build_{completed.outcome[len('hung_after_'):]}"

    return BuildResult(
        project_name=project_dir.name,
        project_dir=str(project_dir),
        gradle_root=str(gradle_root),
        task=" ".join([task, *args.additional_task]),
        status=status,
        return_code=return_code,
        duration_seconds=round(duration, 2),
        apk_count=len(copied),
        copied_apks=copied,
        log_file=str(log_file),
        reason=reason,
        git_directory_shim=git_shim,
    )


def write_reports(results: Sequence[BuildResult], output_dir: Path) -> None:
    report_json = output_dir / "build_report.json"
    report_csv = output_dir / "build_report.csv"

    report_json.write_text(
        json.dumps([asdict(result) for result in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    fieldnames = [
        "project_name",
        "project_dir",
        "gradle_root",
        "task",
        "status",
        "return_code",
        "duration_seconds",
        "apk_count",
        "copied_apks",
        "log_file",
        "reason",
        "r8_optimized",
    ]
    with open(report_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row = asdict(result)
            row.pop("git_directory_shim", None)
            row["copied_apks"] = ";".join(result.copied_apks)
            writer.writerow(row)


def main() -> int:
    install_termination_handler()
    args = parse_args()
    detected_android_sdk = detect_android_sdk(args.android_sdk)
    if detected_android_sdk is not None:
        args.android_sdk = str(detected_android_sdk)

    projects_dir = Path(args.projects_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(output_dir)

    if not projects_dir.exists():
        logger.error("Projects directory does not exist: %s", projects_dir)
        return 2
    if args.android_sdk and not Path(args.android_sdk).expanduser().exists():
        logger.error("Android SDK directory does not exist: %s", args.android_sdk)
        return 2
    if not args.android_sdk:
        logger.error(
            "Android SDK not found. Install it with Android Studio or pass --android-sdk."
        )
        return 2
    if args.java_home and not Path(args.java_home).expanduser().exists():
        logger.error("JAVA_HOME directory does not exist: %s", args.java_home)
        return 2

    task = requested_task = build_task(args)
    init_script = (
        write_init_script(output_dir) if args.disable_minification else None
    )
    projects = list(iter_projects(projects_dir, args.project))
    if args.limit > 0:
        projects = projects[: args.limit]

    logger.info("Projects dir: %s", projects_dir)
    logger.info("Output dir: %s", output_dir)
    logger.info("Android SDK: %s", args.android_sdk)
    logger.info("Gradle task: %s", task)
    logger.info(
        "Minification override: %s",
        str(init_script) if init_script else "not applied",
    )
    logger.info("Clean before build: %s", args.clean_first)
    logger.info("Initialize Git submodules: %s", not args.skip_submodules)
    logger.info("Projects selected: %d", len(projects))

    results: List[BuildResult] = []

    for index, project_dir in enumerate(projects, start=1):
        gradle_root = find_gradle_root(project_dir)
        if gradle_root is None:
            logger.warning("[%d/%d] Skipping %s: no settings.gradle found", index, len(projects), project_dir.name)
            results.append(
                BuildResult(
                    project_name=project_dir.name,
                    project_dir=str(project_dir),
                    gradle_root="",
                    task=task,
                    status="skipped",
                    return_code=None,
                    duration_seconds=0,
                    apk_count=0,
                    copied_apks=[],
                    log_file="",
                    reason="no_gradle_settings_found",
                )
            )
            continue

        logger.info("[%d/%d] Building %s", index, len(projects), project_dir.name)

        if args.dry_run:
            results.append(
                BuildResult(
                    project_name=project_dir.name,
                    project_dir=str(project_dir),
                    gradle_root=str(gradle_root),
                    task=task,
                    status="dry_run",
                    return_code=None,
                    duration_seconds=0,
                    apk_count=0,
                    copied_apks=[],
                    log_file="",
                    reason="",
                )
            )
            continue

        task = task_relative_to_root(requested_task, project_dir, gradle_root)
        result = run_build(project_dir, gradle_root, task, init_script, output_dir, args)
        if wrapper_checksum_failed(result):
            checksum = official_wrapper_checksum(gradle_root)
            if checksum:
                with wrapper_checksum_replaced(gradle_root, checksum, logger):
                    result = run_build(project_dir, gradle_root, task, init_script,
                                       output_dir, args)
        retry_version = jdk_to_retry_on(result)
        if retry_version and not args.java_home:
            compatible_jdks = sorted(
                path.parent.parent for root in (Path("/usr/lib/jvm"), Path.home() / ".jdks")
                if root.is_dir()
                for path in root.glob(f"java-{retry_version}*/bin/java")
                if path.is_file()
            )
            if compatible_jdks:
                first_log = Path(result.log_file)
                preserved_log = first_log.with_suffix(".jdk-incompatible.log")
                if first_log.is_file():
                    shutil.copy2(first_log, preserved_log)
                selected_jdk = compatible_jdks[0]
                logger.warning(
                    "[%d/%d] %s: current JDK is incompatible with the build's "
                    "plugins (%s); retrying once with %s",
                    index, len(projects), project_dir.name, result.reason, selected_jdk,
                )
                args.java_home = str(selected_jdk)
                try:
                    result = run_build(
                        project_dir, gradle_root, task, init_script, output_dir, args
                    )
                finally:
                    args.java_home = None
        def with_signing_placeholders(build_task_name: str, result: BuildResult) -> BuildResult:
            # One property per failed evaluation, so a few rounds at most.
            defined: list[str] = []
            for _ in range(6):
                missing = missing_signing_property(result)
                if not missing or missing in defined:
                    break
                defined.append(missing)
                logger.warning(
                    "[%d/%d] %s: build script reads undefined signing property %s; "
                    "retrying with a placeholder", index, len(projects), project_dir.name, missing,
                )
                original = list(getattr(args, "gradle_property", []) or [])
                args.gradle_property = original + [f"{name}={SIGNING_PLACEHOLDER}" for name in defined]
                try:
                    result = run_build(project_dir, gradle_root, build_task_name, init_script,
                                       output_dir, args)
                finally:
                    args.gradle_property = original
            return result

        result = with_signing_placeholders(task, result)
        if remove_corrupt_gradle_locks(result, gradle_root=gradle_root):
            logger.warning("[%d/%d] %s: removed empty Gradle cache locks a crash left; "
                           "retrying once", index, len(projects), project_dir.name)
            result = run_build(project_dir, gradle_root, task, init_script, output_dir, args)
        # N-Zik (2026-10-22): fdroiddata's `gradle: [foss]` named a build
        # type, not a flavour, so assembleFossDebug did not exist; the variant
        # is assembleFoss. moshinda's assembleFdroid was then ambiguous
        # (fallback_task). A release variant reads the release signing
        # properties the debug one never touched, so each retry goes through
        # the placeholder loop again.
        tried = [task]
        current = task
        for _ in range(3):
            fallback = fallback_task(current, result, task, tried)
            if not fallback:
                break
            logger.warning(
                "[%d/%d] %s: %s names no single task; retrying with %s",
                index, len(projects), project_dir.name, current, fallback,
            )
            tried.append(fallback)
            current = fallback
            result = with_signing_placeholders(
                current, run_build(project_dir, gradle_root, current, init_script, output_dir, args))
        optimized_apks = [apk for apk in result.copied_apks if r8_optimized_apk(Path(apk))] \
            if result.status == "success" else []
        if optimized_apks and len(optimized_apks) < len(result.copied_apks):
            # A flavour-only retry (Keryx's :androidApp:assembleFdroid) builds
            # the debug and the release build type; the D8 copies beside the
            # R8 one are the debug build RVSec needs.
            for apk in optimized_apks:
                Path(apk).unlink(missing_ok=True)
            result.copied_apks = [apk for apk in result.copied_apks if apk not in optimized_apks]
            result.apk_count = len(result.copied_apks)
            logger.warning("[%d/%d] %s: dropped %d R8-optimized APK(s) built beside the debug one",
                           index, len(projects), project_dir.name, len(optimized_apks))
        elif optimized_apks:
            debug_task = (current.rsplit(":", 1)[0] + ":" if ":" in current else "") + "assembleDebug"
            if current != task and debug_task not in tried:
                # A retried name can resolve to a minified release task
                # (r8_optimized_apk); the plain debug build of the module
                # keeps the names RVSec must report.
                logger.warning(
                    "[%d/%d] %s: %s produced an R8-optimized APK; retrying once with %s",
                    index, len(projects), project_dir.name, current, debug_task,
                )
                optimized = result
                result = with_signing_placeholders(debug_task, run_build(
                    project_dir, gradle_root, debug_task, init_script, output_dir, args))
                if result.status == "success" and not all(
                        r8_optimized_apk(Path(apk)) for apk in result.copied_apks):
                    stale = set(optimized.copied_apks) - set(result.copied_apks)
                else:
                    stale = set(result.copied_apks) - set(optimized.copied_apks)
                    result = optimized
                for apk in stale:
                    Path(apk).unlink(missing_ok=True)
            if result.copied_apks and all(
                    r8_optimized_apk(Path(apk)) for apk in result.copied_apks):
                # An app that needs R8 to fit the dex limit builds no other
                # way; its APK is kept, and flagged.
                result.r8_optimized = True
                logger.warning(
                    "[%d/%d] %s: only an R8-optimized APK was built; lambda and "
                    "inlined frames may not map to source",
                    index, len(projects), project_dir.name,
                )
        results.append(result)
        logger.info(
            "[%d/%d] %s: %s, apk_count=%d, log=%s",
            index,
            len(projects),
            project_dir.name,
            result.status,
            result.apk_count,
            result.log_file,
        )

    write_reports(results, output_dir)

    success = sum(1 for result in results if result.status == "success")
    failed = sum(1 for result in results if result.status == "failed")
    infrastructure_failure = sum(
        1 for result in results if result.status == "infrastructure_failure"
    )
    skipped = sum(1 for result in results if result.status == "skipped")
    timeout = sum(1 for result in results if result.status == "timeout")

    logger.info(
        "Done: %d success, %d failed, %d infrastructure failure, %d skipped, %d timeout",
        success,
        failed,
        infrastructure_failure,
        skipped,
        timeout,
    )
    logger.info("Reports: %s and %s", output_dir / "build_report.json", output_dir / "build_report.csv")

    # A skipped project is neither: it was excluded on purpose and nothing
    # about it says whether the step worked.
    unsuccessful = failed + infrastructure_failure + timeout
    return step_exit_code(success, unsuccessful)


if __name__ == "__main__":
    raise SystemExit(main())
