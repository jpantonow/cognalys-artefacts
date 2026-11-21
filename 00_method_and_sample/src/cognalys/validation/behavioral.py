#!/usr/bin/env python3
"""Behavioural evidence for a patch, kept apart from "it compiles".

The baseline build was cached under the repository, commit and task list,
so changing the JDK, the Android SDK or a resolved dependency silently
reused a baseline built under different conditions; the toolchain
fingerprint here goes into its cache key.

The regression-suite run that also lived here was removed (2026-10-23): it
was off by default, never ran in the evaluated cases, and most target apps
have no unit tests covering the code a crypto patch changes.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Dict


_BUILD_FILES = (
    "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "gradle.properties",
    "gradle/libs.versions.toml", "gradle/wrapper/gradle-wrapper.properties",
)


def _first_line(*command: str) -> str:
    try:
        completed = subprocess.run(
            list(command), capture_output=True, text=True, timeout=60,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (completed.stdout or completed.stderr or "").strip()
    return output.splitlines()[0] if output else ""


def toolchain_fingerprint(project: Path) -> Dict:
    """What a build outcome depends on besides the source.

    The JDK, the Android SDK location and platform list, the Gradle wrapper
    the project pins, and a digest of every file that declares a dependency
    or a plugin version. Two baselines with different fingerprints were
    built under different conditions and must not be treated as the same.
    """
    project = Path(project)
    digest = hashlib.sha256()
    declared = []
    for name in _BUILD_FILES:
        for path in sorted(project.glob(f"**/{name}")):
            if any(part in {"build", ".gradle", ".git"} for part in path.parts):
                continue
            try:
                digest.update(path.relative_to(project).as_posix().encode())
                digest.update(path.read_bytes())
                declared.append(path.relative_to(project).as_posix())
            except (OSError, ValueError):
                continue
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT", "")
    platforms = sorted(
        path.name for path in (Path(sdk) / "platforms").iterdir()
    ) if sdk and (Path(sdk) / "platforms").is_dir() else []
    return {
        "java": _first_line("java", "-version"),
        "java_home": os.environ.get("JAVA_HOME", ""),
        "android_sdk": sdk,
        "android_platforms": platforms,
        "build_declarations_sha256": digest.hexdigest(),
        "build_declaration_files": len(declared),
    }


def fingerprint_digest(fingerprint: Dict) -> str:
    """A stable short digest of a fingerprint, for a cache key."""
    material = "|".join(
        f"{key}={fingerprint.get(key)}" for key in sorted(fingerprint)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
