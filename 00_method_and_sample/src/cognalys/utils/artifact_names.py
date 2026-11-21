"""File names for a finding's artifacts: pull request metadata and previews.

They were named <apk>_<method>, which two findings in the same method share.
org.cry.otp's hmac_sha is reported under MacSpec and under
SecretKeySpecSpec; in run_20261009_012517 both produced a pull request and
one *_pr.json overwrote the other, and so did their previews. The spec is
now part of the name. Readers of runs made before this look for the old
name as well (legacy_finding_artifact_stem).
"""
from __future__ import annotations

import re


def filename_component(value: str, fallback: str = "change") -> str:
    """A value made safe for a file name; "<init>" becomes "init"."""
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
    return value.strip("._-") or fallback


def finding_artifact_stem(apk: str, method: str, spec: str) -> str:
    return "_".join((filename_component(apk, "unknown_apk"),
                     filename_component(method),
                     filename_component(spec, "spec")))


def legacy_finding_artifact_stem(apk: str, method: str) -> str:
    """The name runs before the spec was added used."""
    return f"{filename_component(apk, 'unknown_apk')}_{filename_component(method)}"
