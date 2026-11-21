#!/usr/bin/env python3
"""Formatting a patch with the formatter the repository itself configures.

Crustack/NotallyX#1167 (2026-10-30): the repository formats Kotlin with ktfmt
(kotlinLangStyle, 100 columns) through a pre-commit hook that a Gradle task
installs, while the pipeline only knew ktlintFormat. The published test, and
the follow-up commit pr_followup prepared for it, had lines over 100 columns;
the follow-up also had to delete the hook its own build installed, because
the hook runs Gradle without the build's properties. A maintainer who commits
with the hook would reformat our lines in their next commit.

This module finds the formatter the repository configures (Gradle plugins:
Spotless, ktfmt-gradle, kotlinter, ktlint-gradle, google-java-format; a hook
script or a pre-commit configuration naming one of them; .editorconfig for
the line limit), runs it as narrowly as the tool allows, and keeps only what
it changed on the lines the patch wrote:

- files outside the patch it touched are put back as they were;
- inside a patched file, a hunk that only reformats lines the patch did not
  write is dropped, so the diff a maintainer reads holds no unrelated
  reformatting even when the repository is not fully formatted;
- with ``layout_only`` (the PR step formats a patch the build already
  validated), a hunk that changes more than layout -- whitespace, line breaks,
  trailing commas, the order of import lines -- is dropped too, so nothing
  reaches the pull request that the build did not compile.

When nothing is configured, or the formatter fails or times out, the patch is
left exactly as it was and the record says why: formatting is never a reason
to hold a patch.
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from cognalys.utils.edit_text import read_exact, write_exact

# Gradle's words when a task or a task option does not exist: the precise
# invocation is retried with the module's whole formatting task.
_TASK_MISSING = re.compile(
    r"Task '[^']*' not found|Cannot locate tasks that match"
    r"|Unknown command-line option|Problem configuring task .* from command line"
    r"|not found in (?:root )?project", re.IGNORECASE)

_SKIPPED_PARTS = {".git", ".gradle", "build", "node_modules", ".idea", "out"}
_KOTLIN = (".kt", ".kts")
_JAVA = (".java",)


@dataclass(frozen=True)
class FormatterSpec:
    """One formatter: how a repository says it uses it, and its Gradle task.

    ``markers`` are plugin ids, buildscript coordinates or configuration
    blocks as they appear in build scripts and version catalogs; a bare tool
    name is not one, because a catalog names "ktlint" for Spotless's ktlint
    step as readily as for the ktlint plugin.
    """

    name: str
    markers: tuple[str, ...]
    hook_words: tuple[str, ...]
    extensions: tuple[str, ...]
    module_task: str
    # The width the tool formats to when the repository sets none.
    default_line_limit: int | None = None


FORMATTERS: tuple[FormatterSpec, ...] = (
    FormatterSpec(
        "spotless",
        (r"com\.diffplug\.spotless", r"(?m)^\s*spotless\s*\{"),
        ("spotlessApply", "spotless"),
        _KOTLIN + _JAVA + (".groovy", ".gradle", ".scala"),
        "spotlessApply"),
    FormatterSpec(
        "ktfmt",
        (r"com\.ncorti\.ktfmt\.gradle", r"(?m)^\s*ktfmt\s*\{"),
        ("ktfmt",),
        _KOTLIN, "ktfmtFormat", 100),
    FormatterSpec(
        "kotlinter",
        (r"org\.jmailen\.kotlinter", r"org\.jmailen\.gradle:kotlinter"),
        ("formatKotlin", "kotlinter"),
        _KOTLIN, "formatKotlin"),
    FormatterSpec(
        "ktlint",
        (r"org\.jlleitschuh\.gradle\.ktlint", r"org\.jlleitschuh\.gradle:ktlint-gradle"),
        ("ktlintFormat", "ktlint"),
        _KOTLIN, "ktlintFormat"),
    FormatterSpec(
        "google-java-format",
        (r"com\.github\.sherter\.google-java-format",),
        ("googleJavaFormat", "google-java-format"),
        _JAVA, "googleJavaFormat", 100),
)

# Where repositories keep hook scripts they install or ask contributors to
# install (NotallyX's .scripts/pre-commit, copied by installLocalGitHooks).
_HOOK_GLOBS = (".scripts/*", ".githooks/*", ".git-hooks/*", ".hooks/*", "hooks/*",
               ".husky/*", "scripts/*hook*", "scripts/pre-commit*", "tools/*hook*")
_PRE_COMMIT_CONFIG = ".pre-commit-config.yaml"


@dataclass
class Detection:
    formatter: FormatterSpec | None = None
    evidence: list[str] = field(default_factory=list)
    pre_commit_config: bool = False
    # Build scripts and version catalogs by checkout-relative path, read once.
    build_texts: dict[str, str] = field(default_factory=dict)


_SETTINGS_FILES = ("settings.gradle", "settings.gradle.kts")


def _repository_files(root: Path, names: Iterable[str],
                      keep: Callable[[Path], bool] = lambda _path: True) -> list[Path]:
    """Build files under ``root``; source trees are not walked.

    A build script never sits inside a source set, and walking every
    src/main/java of a large repository made one detection take minutes.
    A directory ``keep`` refuses is not walked either.
    """
    wanted = set(names)
    found = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [name for name in subdirectories
                             if name not in _SKIPPED_PARTS and name != "src"
                             and keep(Path(directory) / name)]
        found.extend(Path(directory) / name for name in files if name in wanted)
    return sorted(found)


def _part_of_the_build(root: Path) -> Callable[[Path], bool]:
    """Whether a directory belongs to the build that compiles the patch.

    A directory with a settings file of its own is another Gradle build:
    Intertune's innertubex submodule (intertune_v5, 2026-10-30) applies
    ktlint to itself, while the root build (app, innertube, ...) applies no
    formatter; read as the repository's configuration, it sent
    ":app:ktlintFormat" to a module that has no such task. Builds the root
    settings include (includeBuild: convention plugins) and buildSrc are
    part of it, and so is every directory on the way to the Gradle root.
    """
    from cognalys.prs.pr_validation import gradle_root

    build_root = gradle_root(root)
    included = {build_root / "buildSrc"}
    for name in _SETTINGS_FILES:
        included |= {(build_root / found).resolve() for found in
                     _INCLUDED_BUILD.findall(_read(build_root / name))}

    def keep(directory: Path) -> bool:
        directory = directory.resolve()
        if directory == build_root or directory in build_root.parents:
            return True
        return directory in included or not any(
            (directory / name).is_file() for name in _SETTINGS_FILES)
    return keep


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_INCLUDED_BUILD = re.compile(r"""\bincludeBuild\s*\(?\s*["']([^"']+)["']""")
_CONVENTION_SOURCES = (".kt", ".kts", ".groovy", ".gradle", ".java")


def _convention_plugin_files(root: Path, settings: Iterable[str]) -> list[Path]:
    """Sources of the build's own plugins: buildSrc and builds the settings include.

    A convention plugin configures the formatter in code (AGrahn's
    build-logic SpotlessPlugin.kt: ktfmt googleStyle, 100 columns,
    2026-10-30), out of reach of a walk that skips source trees.
    """
    roots = [root / "buildSrc"] + [(root / name).resolve() for text in settings
                                   for name in _INCLUDED_BUILD.findall(text)]
    found = []
    for build in roots:
        source = build / "src" / "main"
        if not source.is_dir() or (build != root and root not in build.parents):
            continue
        found += [path for path in sorted(source.rglob("*"))
                  if path.suffix in _CONVENTION_SOURCES and path.is_file()
                  and path.stat().st_size < 200_000]
    return found


def _build_texts(root: Path) -> dict[str, str]:
    files = _repository_files(root, ("build.gradle", "build.gradle.kts", "settings.gradle",
                                     "settings.gradle.kts", "libs.versions.toml"),
                              _part_of_the_build(root))
    settings = [_read(path) for path in files if path.name.startswith("settings.gradle")]
    files += _convention_plugin_files(root, settings)
    return {path.relative_to(root).as_posix(): _read(path) for path in dict.fromkeys(files)}


def _hook_texts(root: Path) -> dict[str, str]:
    texts = {}
    for pattern in _HOOK_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file() and path.stat().st_size < 200_000:
                texts[path.relative_to(root).as_posix()] = _read(path)
    return texts


def detect_formatter(project_root: Path) -> Detection:
    """The formatter this repository configures, and where that was read.

    A formatter a hook script names comes first: it is the one that runs on
    every commit a maintainer makes. Otherwise the first configured one in
    FORMATTERS order (Spotless wraps the others when both are present).
    """
    detection = Detection()
    # The checkout holds the Gradle root, wherever the settings file sits.
    build = detection.build_texts = _build_texts(project_root.resolve())
    configured: dict[str, list[str]] = {}
    for spec in FORMATTERS:
        for name, text in build.items():
            if any(re.search(marker, text) for marker in spec.markers):
                configured.setdefault(spec.name, []).append(name)
    hooks = _hook_texts(project_root.resolve())
    hooked = [spec for spec in FORMATTERS if spec.name in configured and any(
        word.lower() in text.lower() for text in hooks.values() for word in spec.hook_words)]
    detection.pre_commit_config = (project_root / _PRE_COMMIT_CONFIG).is_file()
    chosen = (hooked or [spec for spec in FORMATTERS if spec.name in configured] or [None])[0]
    if chosen is not None:
        detection.formatter = chosen
        detection.evidence = sorted(configured[chosen.name])
        detection.evidence += sorted(
            name for name, text in hooks.items()
            if any(word.lower() in text.lower() for word in chosen.hook_words))
    elif detection.pre_commit_config:
        detection.evidence = [_PRE_COMMIT_CONFIG]
    return detection


# ------------------------------------------------------------ line limit

def _editorconfig_patterns(section: str) -> list[str]:
    """Brace alternatives of an .editorconfig section, as fnmatch patterns."""
    match = re.search(r"\{([^{}]*)\}", section)
    if not match:
        return [section]
    return [pattern for option in match.group(1).split(",")
            for pattern in _editorconfig_patterns(
                section[:match.start()] + option.strip() + section[match.end():])]


def _editorconfig_value(project_root: Path, path: str, key: str) -> str | None:
    """``key`` for ``path`` from the .editorconfig files above it, nearest last."""
    target = (project_root / path)
    directories = [target.parent, *target.parent.parents]
    configs = []
    for directory in directories:
        config = directory / ".editorconfig"
        if config.is_file():
            configs.append(config)
        if directory == project_root or re.search(r"(?mi)^\s*root\s*=\s*true", _read(config)):
            break
    value = None
    for config in reversed(configs):
        relative = target.relative_to(config.parent).as_posix()
        section = None
        for raw in _read(config).splitlines():
            line = raw.strip()
            if not line or line[0] in "#;":
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            if section is None or "=" not in line:
                continue
            name, _, setting = (part.strip() for part in line.partition("="))
            if name.lower() != key:
                continue
            patterns = _editorconfig_patterns(section)
            subject = relative if "/" in section else Path(relative).name
            if any(fnmatch.fnmatchcase(subject, pattern.lstrip("/").replace("**", "*"))
                   for pattern in patterns):
                value = setting
    return value


def line_limit(project_root: Path, path: str, detection: Detection | None = None) -> int | None:
    """The line width the repository asks for ``path``, or None when it sets none.

    Read from the formatter's own setting (ktfmt's maxWidth), then
    .editorconfig's max_line_length, then the width the detected formatter
    uses by default (ktfmt and google-java-format: 100).
    """
    detection = detection or detect_formatter(project_root)
    spec = detection.formatter
    suffix = Path(path).suffix
    formats = spec is not None and suffix in spec.extensions
    texts = "\n".join(detection.build_texts.values()) if formats else ""
    if formats and spec.name in {"ktfmt", "spotless"} and suffix in _KOTLIN:
        width = re.search(r"\bmaxWidth\s*(?:\.set\s*\(|=|\()\s*(\d+)", texts)
        if width:
            return int(width.group(1))
    configured = (_editorconfig_value(project_root, path, "max_line_length") or "").strip()
    if configured.isdigit():
        return int(configured)
    if configured.lower() == "off" or not formats:
        return None
    if spec.name == "spotless":
        step = r"\bktfmt\s*\(" if suffix in _KOTLIN else r"\bgoogleJavaFormat\s*\("
        return 100 if re.search(step, texts) else None
    return spec.default_line_limit


def overlong_lines(text: str, limit: int | None, lines: Iterable[int] | None = None) -> list[int]:
    """1-based numbers of the lines (of ``lines``, or all) wider than ``limit``."""
    if not limit:
        return []
    split = text.splitlines()
    chosen = range(len(split)) if lines is None else sorted(lines)
    return [index + 1 for index in chosen
            if index < len(split) and len(split[index].rstrip("\r\n")) > limit]


# ------------------------------------------------------------ merging

_IMPORT_LINE = re.compile(r"^\s*(?:import|package)\s+\S")


def _layout_key(lines: list[str]) -> str:
    """The text without layout: whitespace and commas (trailing commas) removed."""
    return re.sub(r"[\s,]", "", "".join(lines))


def layout_only(old: list[str], new: list[str]) -> bool:
    """Whether a hunk only changes layout: spacing, breaks, trailing commas, import order."""
    if _layout_key(old) == _layout_key(new):
        return True
    significant_old = [line.strip() for line in old if line.strip()]
    significant_new = [line.strip() for line in new if line.strip()]
    return (bool(significant_old)
            and all(_IMPORT_LINE.match(line) for line in significant_old + significant_new)
            and sorted(significant_old) == sorted(significant_new))


def patch_lines(before: str | None, patched: str) -> set[int]:
    """0-based indices of the lines of ``patched`` the patch wrote."""
    lines = patched.splitlines(keepends=True)
    if before is None:
        return set(range(len(lines)))
    matcher = difflib.SequenceMatcher(None, before.splitlines(keepends=True), lines,
                                      autojunk=False)
    return {index for tag, _, _, j1, j2 in matcher.get_opcodes() if tag != "equal"
            for index in range(j1, j2)}


def _moved_import_hunks(opcodes: list[tuple], old: list[str], new: list[str],
                        ours: set[int]) -> set[int]:
    """Opcode indices that together only move the patch's own import lines.

    Sorting imports shows as two hunks: the patch's import removed where it
    was written and inserted where it sorts (NotallyX's ExportExtensions.kt,
    2026-10-30: ktfmt moved "import ...sha256Hash" below
    "resolveAttachmentFile"). The insertion sits beside lines the patch did
    not write, so judged alone it was dropped and the import stayed unsorted.
    The hunks are kept together when they only reorder imports and the
    imports the patch did not write keep their order among themselves (difflib
    may show the move as the neighbour moving past the patch's line); a
    repository whose own imports are unsorted keeps them as they are.
    """
    hunks = [index for index, (tag, i1, i2, j1, j2) in enumerate(opcodes) if tag != "equal"
             and [line for line in old[i1:i2] + new[j1:j2] if line.strip()]
             and all(_IMPORT_LINE.match(line) for line in old[i1:i2] + new[j1:j2]
                     if line.strip())]
    removed = [old[line].strip() for index in hunks
               for line in range(opcodes[index][1], opcodes[index][2]) if old[line].strip()]
    added = [new[line].strip() for index in hunks
             for line in range(opcodes[index][3], opcodes[index][4]) if new[line].strip()]
    written = {old[line].strip() for line in ours
               if line < len(old) and _IMPORT_LINE.match(old[line])}
    if not removed or not written or sorted(removed) != sorted(added):
        return set()

    def others(lines: list[str]) -> list[str]:
        return [line.strip() for line in lines
                if _IMPORT_LINE.match(line) and line.strip() not in written]

    return set(hunks) if others(old) == others(new) else set()


def _split_unwritten_edges(opcode: tuple, old: list[str], new: list[str],
                           ours: set[int]) -> list[tuple]:
    """A replace hunk with the lines the patch did not write peeled off its edges.

    difflib joins neighbouring changes into one hunk, so a formatter that
    re-spaces an untouched line next to a patch line would have it kept with
    the patch's hunk. An edge line the patch did not write becomes its own
    hunk when some leading (or trailing) new lines say the same thing in
    another layout.
    """
    tag, i1, i2, j1, j2 = opcode
    if tag != "replace":
        return [opcode]
    head: list[tuple] = []
    tail: list[tuple] = []

    def match(old_lines: list[str], candidates: Iterable[int], cut) -> int | None:
        return next((k for k in candidates if _layout_key(cut(k)) == _layout_key(old_lines)),
                    None)

    while i2 - i1 > 1 and i1 not in ours:
        k = match(old[i1:i1 + 1], range(1, j2 - j1), lambda k: new[j1:j1 + k])
        if k is None:
            break
        head.append(("replace", i1, i1 + 1, j1, j1 + k))
        i1, j1 = i1 + 1, j1 + k
    while i2 - i1 > 1 and (i2 - 1) not in ours:
        k = match(old[i2 - 1:i2], range(1, j2 - j1), lambda k: new[j2 - k:j2])
        if k is None:
            break
        tail.insert(0, ("replace", i2 - 1, i2, j2 - k, j2))
        i2, j2 = i2 - 1, j2 - k
    return [*head, ("replace", i1, i2, j1, j2), *tail]


def merge_formatting(before: str | None, patched: str, formatted: str,
                     only_layout: bool) -> tuple[str, int, list[dict[str, Any]]]:
    """``patched`` with the formatter's hunks that touch the patch's own lines.

    Returns the merged text, the number of hunks kept and the hunks dropped
    (1-based line range in ``patched`` and why: they only reformatted lines
    the patch did not write, or, with ``only_layout``, changed more than
    layout).
    """
    ours = patch_lines(before, patched)
    old = patched.splitlines(keepends=True)
    new = formatted.splitlines(keepends=True)
    merged: list[str] = []
    kept = 0
    dropped: list[dict[str, Any]] = []
    opcodes = [piece for opcode in
               difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
               for piece in _split_unwritten_edges(opcode, old, new, ours)]
    moved_imports = _moved_import_hunks(opcodes, old, new, ours)
    for index, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if tag == "equal":
            merged.extend(old[i1:i2])
            continue
        touched = index in moved_imports or (
            any(line in ours for line in range(i1, i2)) if i2 > i1
            else (i1 - 1) in ours or i1 in ours)
        if index in moved_imports or touched and (
                not only_layout or layout_only(old[i1:i2], new[j1:j2])):
            merged.extend(new[j1:j2])
            kept += 1
        else:
            merged.extend(old[i1:i2])
            dropped.append({"lines": [i1 + 1, max(i1 + 1, i2)], "reason": (
                "more than layout" if touched else "lines the patch did not write")})
    return "".join(merged), kept, dropped


# ------------------------------------------------------------ running

def _source_set(relative: str) -> str:
    """The Gradle source set of a module-relative path ("src/test/kotlin/..." -> "test")."""
    parts = relative.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "src" else ""


def formatter_commands(spec: FormatterSpec, project_root: Path,
                       files: list[str]) -> list[tuple[list[str], ...]]:
    """Gradle argument lists to try in order, one tuple per module.

    ktfmt-gradle's per-source-set task takes --include-only (paths relative
    to the module, as NotallyX's own hook passes them); Spotless formats one
    file through its IDE hook property. The others only format a whole
    module. The module's whole formatting task follows, tried when the
    precise one does not exist in that version of the plugin, and last the
    task from the root project, for a formatter applied there alone with a
    target that reaches into the modules (AGrahn's Spotless,
    target("**/*.kt") in the root build only, 2026-10-30: ":app:spotlessApply"
    does not exist). An unqualified task name runs in every project that has
    it; files it formats outside the patch are put back.
    """
    from cognalys.prs.pr_validation import gradle_module_path, gradle_root

    root = gradle_root(project_root)
    by_module: dict[str, list[str]] = {}
    for path in files:
        module = gradle_module_path(project_root, project_root / path)
        if module is not None:
            by_module.setdefault(module, []).append(path)
    commands = []
    for module, paths in sorted(by_module.items()):
        prefix = f":{module}:" if module else ""
        module_dir = root / module.replace(":", "/") if module else root
        broad = [f"{prefix}{spec.module_task}"]
        from_root = [spec.module_task]
        relative = [(project_root / path).resolve().relative_to(module_dir.resolve()).as_posix()
                    for path in paths]
        precise = broad
        if spec.name == "ktfmt":
            sets: dict[str, list[str]] = {}
            for item in relative:
                sets.setdefault(_source_set(item), []).append(item)
            narrowed: list[str] = []
            for name, items in sorted(sets.items()):
                if not name:
                    narrowed = []
                    break
                narrowed += [f"{prefix}{spec.module_task}{name[:1].upper()}{name[1:]}",
                             "--include-only=" + ",".join(items)]
            precise = narrowed or broad
        elif spec.name == "spotless" and len(paths) == 1:
            hook = f"-PspotlessIdeHook={(project_root / paths[0]).resolve()}"
            precise = [f"{prefix}{spec.module_task}", hook]
            from_root = [spec.module_task, hook]
        attempts: list[list[str]] = []
        for arguments in (precise, broad, from_root):
            if arguments not in attempts:
                attempts.append(arguments)
        commands.append(tuple(attempts))
    return commands


Runner = Callable[[list[str], Path, dict, int], subprocess.CompletedProcess]

# Signing-property placeholders each Gradle root needed, for this process.
_KNOWN_PLACEHOLDERS: dict[str, list[str]] = {}


def _run(command: list[str], cwd: Path, env: dict, timeout: int) -> subprocess.CompletedProcess:
    """One formatter invocation through the build step's Gradle runner.

    subprocess.run's timeout kills the client alone: the Gradle daemon and
    workers it started kept running, and one could rewrite a file after
    format_patch had put it back (2026-10-30 review, a probe whose worker
    wrote outside the patch two seconds after the timeout). run_gradle stops
    the client's whole process group, and takes the outcome from "BUILD
    SUCCESSFUL" when the client prints it and never exits (Salty,
    2026-10-29), where waiting for the timeout discarded the formatting.
    """
    from cognalys.builds import apk_build

    with tempfile.TemporaryDirectory(prefix="cognalys-format-") as directory:
        log_file = Path(directory) / "formatter.log"
        with open(log_file, "w", encoding="utf-8", errors="replace") as log:
            run = apk_build.run_gradle(command, cwd, env, log, log_file, timeout,
                                       grace=apk_build.HUNG_CLIENT_GRACE_SECONDS)
        output = log_file.read_text(encoding="utf-8", errors="replace")
    if run.outcome == "timeout":
        raise subprocess.TimeoutExpired(command, timeout, output=output)
    if run.outcome.startswith("hung_after_"):
        returncode = 0 if run.outcome == "hung_after_successful" else 1
    else:
        returncode = run.return_code if run.return_code is not None else 1
    return subprocess.CompletedProcess(command, returncode, output, "")


def _gradle_environment(root: Path) -> dict[str, str]:
    """The JDK the build step would pick for this wrapper, as JAVA_HOME."""
    from cognalys.builds.apk_build import compatible_java_home, detect_android_sdk

    env = os.environ.copy()
    if not env.get("JAVA_HOME"):
        java_home = compatible_java_home(root)
        if java_home:
            env["JAVA_HOME"] = str(java_home)
            env["PATH"] = str(Path(java_home) / "bin") + os.pathsep + env.get("PATH", "")
    if not env.get("ANDROID_HOME"):
        sdk = detect_android_sdk(None)
        if sdk:
            env["ANDROID_HOME"] = env["ANDROID_SDK_ROOT"] = str(sdk)
    return env


def _git_lines(project_root: Path, *args: str) -> list[str]:
    completed = subprocess.run(["git", "-C", str(project_root), *args], capture_output=True,
                               text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git failed")
    return [line for line in completed.stdout.split("\0") if line]


def _dirty_tracked(project_root: Path) -> list[str]:
    """Tracked files that differ from HEAD or the index (porcelain -z, unstripped)."""
    paths = []
    entries = _git_lines(project_root, "status", "--porcelain", "-z", "--untracked-files=no")
    skip = False
    for entry in entries:
        if skip:
            skip = False
            continue
        if entry[:1] in {"R", "C"}:
            skip = True  # the next entry is the rename's source
        paths.append(entry[3:])
    return paths


def _restore_outside(project_root: Path, allowed: set[str],
                     saved: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """Put back every tracked file the formatter changed outside the patch."""
    reverted, failed = [], []
    for path in sorted(set(_dirty_tracked(project_root)) | set(saved)):
        if path in allowed:
            continue
        target = project_root / path
        try:
            if path in saved:
                if not target.is_file() or target.read_bytes() != saved[path]:
                    target.write_bytes(saved[path])
                    reverted.append(path)
            else:
                _git_lines(project_root, "checkout", "--", path)
                reverted.append(path)
        except (OSError, RuntimeError):
            failed.append(path)
    return reverted, failed


# A file's text with its line endings as they are on disk, and its inverse.
# read_text's universal newlines turned a CRLF file's lines into LF ones:
# every line then differed from the base (git show keeps the \r), so all of
# them counted as written by the patch, and writing the text back changed
# every line ending of the file (2026-10-30 review). The same pair is what
# the edit appliers write with (edit_text.write_source).
_text_of = read_exact
_write_text = write_exact


def format_patch(project_root: Path, changed: list[str], before: dict[str, str | None], *,
                 timeout: int = 600, only_layout: bool = False,
                 runner: Runner | None = None) -> dict[str, Any]:
    """Run the repository's formatter over the patch's files; keep only the patch's lines.

    ``changed`` are repository-relative paths the patch wrote; ``before`` maps
    each to its text without the patch (None for a file the patch created).
    The files on disk are rewritten in place; the record says what happened.
    """
    from cognalys.builds.apk_build import gradle_command
    from cognalys.builds.git_directory import git_directory_for_build
    from cognalys.prs.pr_validation import gradle_root

    project_root = project_root.resolve()
    detection = detect_formatter(project_root)
    record: dict[str, Any] = {"formatter": detection.formatter.name if detection.formatter
                              else "", "configured_in": detection.evidence}
    spec = detection.formatter
    present = [path for path in changed if (project_root / path).is_file()]
    if spec is None and detection.pre_commit_config:
        return _pre_commit(project_root, present, before, record, timeout, only_layout,
                           runner or _run)
    if spec is None:
        record.update(status="NOT_CONFIGURED",
                      note="the repository configures no formatter the pipeline can run")
        return _with_line_report(project_root, record, present, before, detection)
    targets = [path for path in present if Path(path).suffix in spec.extensions]
    if not targets:
        record.update(status="NOT_CONFIGURED",
                      note=f"{spec.name} formats none of the patch's files")
        return record
    root = gradle_root(project_root)
    command = gradle_command(root)
    if command == ["gradle"] and not shutil.which("gradle"):
        record.update(status="NOT_CONFIGURED", note="no Gradle wrapper to run the formatter")
        return record
    patched = {path: _text_of(project_root / path) for path in targets}
    try:
        saved = {path: (project_root / path).read_bytes()
                 for path in _dirty_tracked(project_root)
                 if path not in patched and (project_root / path).is_file()}
    except RuntimeError as exc:
        record.update(status="FAILED", note=f"git status failed: {exc}")
        return record
    runner = runner or _run
    env = _gradle_environment(root)
    record["commands"] = []
    # Placeholders an earlier run on this Gradle root needed: each missing
    # property costs one configuration (NotallyX: four, about 35 s each).
    properties = _KNOWN_PLACEHOLDERS.setdefault(str(root), [])
    failure = ""

    def gradle(arguments: list[str]) -> tuple[bool, str]:
        """Run one formatting invocation; (succeeded, Gradle's output or the error)."""
        from cognalys.builds.apk_build import SIGNING_PLACEHOLDER, signing_property_missing_from

        # A build script that reads release signing properties stops before
        # any task without them, a formatting task included (NotallyX's
        # RELEASE_STORE_FILE, 2026-10-30): the build step's placeholders.
        for _ in range(6):
            full = [*command, *arguments, *properties, "--no-daemon", "--console=plain"]
            record["commands"].append([*arguments, *properties])
            with git_directory_for_build(root, project_root):
                completed = runner(full, root, env, timeout)
            output = f"{completed.stdout}\n{completed.stderr}"
            if completed.returncode == 0:
                return True, output
            missing = signing_property_missing_from(output)
            placeholder = f"-P{missing}={SIGNING_PLACEHOLDER}"
            if not missing or placeholder in properties:
                return False, output
            properties.append(placeholder)
        return False, output

    for attempts in formatter_commands(spec, project_root, targets):
        for arguments in attempts:
            try:
                succeeded, output = gradle(arguments)
            except subprocess.TimeoutExpired:
                failure = f"{' '.join(arguments)} timed out after {timeout}s"
                break
            except OSError as exc:
                failure = f"{' '.join(arguments)} could not start: {exc}"
                break
            failure = "" if succeeded else (
                f"{' '.join(arguments)} failed: " + output.strip()[-1500:])
            if succeeded or not _TASK_MISSING.search(output):
                break
        if failure:
            break
    reverted, not_reverted = _restore_outside(project_root, set(patched), saved)
    record["reverted_outside_patch"] = reverted
    if not_reverted:
        record["not_reverted"] = not_reverted
    if failure or not_reverted:
        for path, text in patched.items():
            _write_text(project_root / path, text)
        record.update(status="FAILED", note=failure or (
            "files the formatter changed outside the patch could not be restored: "
            + ", ".join(not_reverted)))
        return record
    return _merge_into_files(project_root, record, patched, before, only_layout, detection)


def _merge_into_files(project_root: Path, record: dict[str, Any], patched: dict[str, str],
                      before: dict[str, str | None], only_layout: bool,
                      detection: Detection) -> dict[str, Any]:
    formatted_files, kept_total, dropped_hunks = [], 0, []
    for path, text in patched.items():
        target = project_root / path
        formatted = _text_of(target)
        merged, kept, dropped = merge_formatting(before.get(path), text, formatted,
                                                 only_layout)
        kept_total += kept
        dropped_hunks += [dict(hunk, path=path) for hunk in dropped]
        if merged != formatted:
            _write_text(target, merged)
        if merged != text:
            formatted_files.append(path)
    record.update(status="FORMATTED" if formatted_files else "UNCHANGED",
                  formatted_files=formatted_files, kept_hunks=kept_total,
                  dropped_hunks=dropped_hunks)
    if dropped_hunks:
        record["note"] = (
            f"{len(dropped_hunks)} formatter hunk(s) left out: they reformatted lines the "
            "patch did not write" + (" or changed more than layout" if only_layout else ""))
    return _with_line_report(project_root, record, list(patched), before, detection)


def _with_line_report(project_root: Path, record: dict[str, Any], paths: list[str],
                      before: dict[str, str | None], detection: Detection) -> dict[str, Any]:
    """Patch lines still wider than the repository's limit, for the operator."""
    report = {}
    for path in paths:
        limit = line_limit(project_root, path, detection)
        target = project_root / path
        if not limit or not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        wide = overlong_lines(text, limit, patch_lines(before.get(path), text))
        if wide:
            report[path] = {"limit": limit, "lines": wide}
    if report:
        record["overlong_patch_lines"] = report
    return record


def _pre_commit(project_root: Path, files: list[str], before: dict[str, str | None],
                record: dict[str, Any], timeout: int, only_layout: bool,
                runner: Runner) -> dict[str, Any]:
    """Run the repository's pre-commit hooks on the patch's files, when the tool is here."""
    executable = shutil.which("pre-commit")
    if not executable or not files:
        record.update(status="NOT_CONFIGURED", formatter="pre-commit",
                      note="the repository has .pre-commit-config.yaml but the pre-commit "
                           "tool is not installed here" if files else "no file to format")
        return record
    patched = {path: _text_of(project_root / path) for path in files}
    saved = {path: (project_root / path).read_bytes() for path in _dirty_tracked(project_root)
             if path not in patched and (project_root / path).is_file()}
    arguments = [executable, "run", "--files", *files]
    record.update(formatter="pre-commit", commands=[arguments[1:]])
    try:
        completed = runner(arguments, project_root, os.environ.copy(), timeout)
        # pre-commit exits 1 when a hook modified files: that is the point.
        failure = "" if completed.returncode in (0, 1) else (
            completed.stdout + completed.stderr).strip()[-1500:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        failure = str(exc)
    reverted, not_reverted = _restore_outside(project_root, set(patched), saved)
    record["reverted_outside_patch"] = reverted
    if failure or not_reverted:
        for path, text in patched.items():
            _write_text(project_root / path, text)
        record.update(status="FAILED", note=failure or "could not restore " + ", ".join(
            not_reverted))
        return record
    return _merge_into_files(project_root, record, patched, before, only_layout, Detection())


def texts_at(project_root: Path, ref: str, paths: Iterable[str]) -> dict[str, str | None]:
    """Each path's text at ``ref`` (None where it does not exist), unstripped.

    The texts the patch started from: only the lines the patch wrote are
    formatted. A stripped `git show` would make every file's last line look
    rewritten. The text is as a checkout writes it (`cat-file --filters`):
    in a repository whose .gitattributes says eol=crlf, `git show` gave the
    LF blob while the patched file on disk is CRLF, so every line counted as
    written by the patch and the formatter's changes to all of them were kept
    (2026-10-30 review).
    """
    texts: dict[str, str | None] = {}
    for path in paths:
        completed = subprocess.run(["git", "-C", str(project_root), "cat-file", "--filters",
                                    f"{ref}:{path}"],
                                   capture_output=True, check=False)
        texts[path] = (completed.stdout.decode("utf-8", errors="surrogateescape")
                       if completed.returncode == 0 else None)
    return texts
