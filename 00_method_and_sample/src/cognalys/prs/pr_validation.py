#!/usr/bin/env python3
"""
Compiling and style-checking a pull-request revision before it is published.

Extracted from scripts/fix_prs.py. repair_style_violations runs the project's
own ktlintFormat and then restores any file it reformatted outside the patch,
so a formatting rule cannot fail a revision that is otherwise correct and
cannot inflate someone else's diff either.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


from cognalys.prs.pr_github import run_git  # noqa: E402
from cognalys.builds.command import build_invocation
from cognalys.builds.git_directory import git_directory_for_build
from cognalys.validation.applied_solutions import (  # noqa: E402
    _flavor_qualified_task,
    _load_build_result,
    invoked_gradle_root,
)
from cognalys.utils.test_source_sets import (  # noqa: E402
    is_instrumented_test_set, test_source_set, unit_test_task,
)


_GRADLE_BUILD_SCRIPTS = ("build.gradle", "build.gradle.kts")


def gradle_root(project_root: Path) -> Path:
    """The directory Gradle runs from: the build step's rule, not the checkout.

    Shared with validate_applied_solutions (invoked_gradle_root), so a revision
    is compiled with the same task paths as the first validation. A checkout
    whose settings file sits in a subdirectory (BWT's comencryptbwt/) has its
    wrapper and its project paths there.
    """
    return invoked_gradle_root(project_root.resolve()).resolve()


def _wrapper(root: Path) -> Path:
    return root / ("gradlew.bat" if os.name == "nt" else "gradlew")


def gradle_module_path(project_root: Path, source: Path) -> str | None:
    """Gradle project path (without the leading colon) owning a source file.

    The nearest directory between the file and the Gradle root that has its
    own build script is the module; nested modules become "libs:foo". Taking
    the first path segment instead assumed every project is multi-module and
    turned a single-module layout with sources at the root (src/...) into a
    nonexistent ":src:assembleDebug". A file owned by no module below the
    root belongs to the root project, whose tasks are unqualified. A file
    outside the Gradle root belongs to no project Gradle builds: None.
    """
    root = gradle_root(project_root)
    directory = source.resolve().parent
    if directory != root and root not in directory.parents:
        return None
    while directory != root:
        if any((directory / name).is_file() for name in _GRADLE_BUILD_SCRIPTS):
            return directory.relative_to(root).as_posix().replace("/", ":")
        directory = directory.parent
    return ""


def compile_validation_task(project_root: Path, source: Path) -> str | None:
    """Pick the Gradle task that actually compiles a revised source file.

    Mirrors validate_applied_solutions.py's module/test-root detection: an
    androidTest source needs assembleDebugAndroidTest, a plain test source
    needs testDebugUnitTest, and anything else (production code) needs
    assembleDebug. _flavor_qualified_task then qualifies whichever of those
    with the module's declared product flavor, if any (see that function's
    docstring for why an unqualified task is refused outright by Gradle
    rather than defaulted). None for a file outside the Gradle root, which no
    task compiles.
    """
    module = gradle_module_path(project_root, source)
    if module is None:
        return None
    root = gradle_root(project_root)
    # Leading slash so a root-project source ("src/test/...") matches too.
    # Kotlin Multiplatform test sets run under their module's own task.
    test_set = test_source_set("/" + source.resolve().relative_to(root).as_posix())
    module_dir = root / module.replace(":", "/") if module else root
    if not test_set:
        base_task = "assembleDebug"
    elif is_instrumented_test_set(test_set):
        base_task = "assembleDebugAndroidTest"
    else:
        base_task = (unit_test_task(module_dir, [test_set]) or ["testDebugUnitTest"])[0]
    qualified_task = _flavor_qualified_task(module_dir, base_task)
    return f":{module}:{qualified_task}" if module else qualified_task


def run_compile_validation(
    project_root: Path,
    sources: list[Path],
    build_script: Path,
    output_dir: Path,
    variant: str,
    timeout: int,
) -> dict[str, Any]:
    """Actually compile the revised project before it reaches a public PR.

    run_style_validation below only runs ktlintCheck, a formatter -- it
    passes on a revision with a type error, a missing import, or a broken
    method signature just as readily as on correctly formatted code. This
    reuses the same build_apks_no_proguard.py machinery and flavor-qualified
    task resolution (see _flavor_qualified_task) as the main pipeline's
    validation/revalidation steps, so a fix_prs revision is held to the same
    "never publish code that does not compile" bar as a fresh PR. Every
    changed file is covered by one build, so a coordinated revision that
    touches, say, production code and an instrumentation test compiles both
    (via --additional-task) rather than only the originally reported file.
    """
    # The report intentionally stores portable, repository-relative checkout
    # paths.  subprocess.run changes cwd to that checkout, so forwarding its
    # still-relative parent to build_apks_no_proguard.py makes the child
    # resolve the path *under the checkout* and duplicates the whole suffix.
    # Normalize every path before composing the child command.
    project_root = project_root.resolve()
    sources = [source.resolve() for source in sources]
    build_script = build_script.resolve() if build_script else build_script
    output_dir = output_dir.resolve()
    if not _wrapper(gradle_root(project_root)).is_file():
        return {"status": "NOT_CONFIGURED", "reason": "Gradle wrapper not found"}
    by_source = {
        source: compile_validation_task(project_root, source) for source in sources
    }
    # A changed file no task compiles would ride on the others' "SUCCESS".
    outside = sorted(str(source) for source, task in by_source.items() if task is None)
    if outside:
        return {
            "status": "NOT_CONFIGURED",
            "reason": "changed sources outside the Gradle root: " + ", ".join(outside),
        }
    tasks = sorted(set(by_source.values()))
    if not tasks:
        return {"status": "NOT_CONFIGURED", "reason": "no changed sources to compile"}
    command = [
        *build_invocation(build_script),
        "--projects-dir", str(project_root.parent),
        "--project", project_root.name,
        "--output-dir", str(output_dir),
        "--variant", variant,
        "--timeout", str(timeout),
        "--limit", "1",
        "--disable-minification",
        "--task", tasks[0],
    ]
    for task in tasks[1:]:
        command.extend(["--additional-task", task])
    command.append("--clean-first")
    completed = subprocess.run(
        command, cwd=project_root, text=True, capture_output=True,
        check=False, timeout=timeout + 120,
    )
    result = _load_build_result(output_dir, project_root.name, completed.returncode)
    return {
        "status": "SUCCESS" if result.get("status") == "success" else "FAILED",
        "tasks": tasks,
        "returncode": completed.returncode,
        "build_result": result,
    }


def run_style_validation(
    project_root: Path,
    sources: list[Path],
    timeout: int,
) -> dict[str, Any]:
    root = gradle_root(project_root)
    wrapper = _wrapper(root)
    if not wrapper.is_file():
        return {"status": "NOT_CONFIGURED", "reason": "Gradle wrapper not found"}
    build_files = list(root.glob("build.gradle*")) + list(
        root.rglob("libs.versions.toml")
    )
    build_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in build_files
    ).lower()
    if "ktlint" not in build_text:
        return {"status": "NOT_CONFIGURED", "reason": "ktlint not configured"}
    # A file outside the Gradle root is in no module ktlint checks.
    modules = sorted({
        module for module in (gradle_module_path(project_root, source)
                              for source in sources)
        if module is not None
    })
    tasks = [f":{module}:ktlintCheck" if module else "ktlintCheck" for module in modules]
    if not tasks:
        # A bare gradlew runs the default task, whose success says nothing
        # about style.
        return {"status": "NOT_CONFIGURED",
                "reason": "no changed source inside the Gradle root"}
    command = [str(wrapper), *tasks]
    # A worktree's .git is a file; scripts that read it need the build step's shim.
    with git_directory_for_build(root, project_root):
        completed = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    combined = (completed.stdout + "\n" + completed.stderr).strip()
    return {
        "status": "SUCCESS" if completed.returncode == 0 else "FAILED",
        "command": command,
        "returncode": completed.returncode,
        "output_tail": combined[-8000:],
    }


def repair_style_violations(
    project_root: Path,
    sources: list[Path],
    timeout: int,
) -> dict[str, Any]:
    """Run the project's own ktlintFormat, then discard changes outside the patch.

    The violations that fail a revision are formatting rules ("Newline expected
    after opening parenthesis" and friends) that ktlintFormat fixes exactly and
    deterministically, so spending an LLM call to reword Kotlin by hand is both
    slower and less reliable. ktlintFormat rewrites the whole source set,
    though, and reformatting files this patch never touched would inflate
    someone else's pull request, so every file outside `sources` is restored.
    """
    root = gradle_root(project_root)
    wrapper = _wrapper(root)
    if not wrapper.is_file():
        return {"status": "NOT_CONFIGURED", "reason": "Gradle wrapper not found"}
    modules = sorted({
        module for module in (gradle_module_path(project_root, source)
                              for source in sources)
        if module is not None
    })
    tasks = [f":{module}:ktlintFormat" if module else "ktlintFormat"
             for module in modules]
    if not tasks:
        # A bare gradlew would run the default task and report a format pass
        # that formatted nothing.
        return {"status": "NOT_CONFIGURED",
                "reason": "no changed source inside the Gradle root"}
    try:
        with git_directory_for_build(root, project_root):
            completed = subprocess.run(
                [str(wrapper), *tasks], cwd=root, text=True,
                capture_output=True, check=False, timeout=timeout,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "FAILED", "error": str(exc)}

    allowed = {
        source.relative_to(project_root).as_posix() for source in sources
    }
    reverted = []
    not_reverted = []
    for line in run_git(project_root, "status", "--porcelain").splitlines():
        # run_git strips its output, so the first line loses the leading
        # space of " M path" and line[3:] cut the path's first letter
        # ("pp/src/..."), which then failed to check out (found while
        # generalising the formatter step, 2026-10-30).
        fields = line.strip().split(maxsplit=1)
        path = fields[1].strip() if len(fields) == 2 else ""
        if path and path not in allowed:
            try:
                run_git(project_root, "checkout", "--", path)
                reverted.append(path)
            except Exception:  # noqa: BLE001 -- recorded below
                not_reverted.append(path)
    if not_reverted:
        # The formatter touched files outside this revision and they could
        # not be restored; succeeding here would let them ride into the
        # pull request.
        return {
            "status": "FAILED",
            "error": "formatter changes outside the revision could not be "
                     "reverted: " + ", ".join(not_reverted[:10]),
            "tasks": tasks, "reverted_outside_scope": reverted,
            "not_reverted": not_reverted,
        }
    return {
        "status": "SUCCESS" if completed.returncode == 0 else "FAILED",
        "tasks": tasks,
        "returncode": completed.returncode,
        "reverted_unrelated_files": reverted,
        "output_tail": (completed.stdout + "\n" + completed.stderr).strip()[-2000:],
    }
