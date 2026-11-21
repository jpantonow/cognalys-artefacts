#!/usr/bin/env python3
"""Reading a PMD XML report.

Moved here from scripts/scan_fdroid_pmd.py, which still uses it, because the
detector replay in validation/detector_replay.py reads the same reports and
library code cannot import from scripts/.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


REPORT_OK = "ok"
REPORT_MISSING = "missing"
REPORT_INVALID = "invalid"


def read_report(report: Path) -> dict:
    """The report's violations, and whether the report can be trusted at all.

    An empty violation list means two very different things: the detector
    looked and found nothing, or there is no usable report to look in. A
    reader that returns [] for both lets a crashed scan read as a clean
    file, so the second case carries its own status. PMD also records files
    it could not parse (<error>) and configuration problems (<configerror>)
    inside an otherwise valid report; those are returned so a caller can
    tell "no alert in this file" from "this file was never analysed".
    """
    report = Path(report)
    if not report.is_file():
        return {"status": REPORT_MISSING, "violations": [],
                "processing_errors": [], "config_errors": []}
    try:
        root = ET.parse(report).getroot()
    except (OSError, ET.ParseError) as exc:
        return {"status": REPORT_INVALID, "violations": [],
                "processing_errors": [], "config_errors": [],
                "detail": str(exc)[:200]}
    violations: list[dict[str, str]] = []
    processing_errors: list[dict[str, str]] = []
    config_errors: list[dict[str, str]] = []
    for node in root.iter():
        name = local_name(node.tag)
        if name == "error":
            processing_errors.append({
                "filename": node.attrib.get("filename", ""),
                "message": node.attrib.get("msg", "")[:300],
            })
        elif name == "configerror":
            config_errors.append({
                "rule": node.attrib.get("rule", ""),
                "message": node.attrib.get("msg", "")[:300],
            })
        elif name == "file":
            filename = node.attrib.get("name", "")
            for violation in node:
                if local_name(violation.tag) != "violation":
                    continue
                violations.append(
                    {
                        **violation.attrib,
                        "filename": filename,
                        "message": " ".join("".join(violation.itertext()).split()),
                    }
                )
    return {"status": REPORT_OK, "violations": violations,
            "processing_errors": processing_errors,
            "config_errors": config_errors}
