"""How a validation starts the APK build."""
from __future__ import annotations

import sys
from pathlib import Path


def build_invocation(build_script) -> list[str]:
    """The interpreter and program that run the APK build.

    The package's own build stage, unless a caller names another program
    (--build-script) that exists.
    """
    if build_script and Path(build_script).is_file():
        return [sys.executable, str(build_script)]
    return [sys.executable, "-m", "cognalys.builds.apk_build"]
