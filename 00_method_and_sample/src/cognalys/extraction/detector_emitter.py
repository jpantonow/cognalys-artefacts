"""The reporting detector's own code where it raises a finding's code.

FreeOTP (2026-10-27): the adjudicator confirmed that a PBKDF2 key reaches an
AES/GCM Cipher.init, and the planner then replaced the password-derived key
with a KeyGenerator key -- which no password can recreate -- and forbade "a
new alias or wrapper". RVSec's CipherSpec.mop, at the line that raises
CIPHER-CONSTR-00, says the opposite in its own comment: the direct
PBKDF2 -> Cipher("AES/...") route matches no admitted origin, and "the
conforming path ... is the re-wrap, getEncoded() -> new SecretKeySpec(bytes,
"AES")". The specification the pipeline generates comes from the published
rule, not from the checker that ran; this hands over the checker's code at
the reporting site, so the plan answers what will actually be checked.

Where the detector keeps its codes table and sources is data
(data/detector_emitters.json): a local checkout ($RVSEC_HOME) first, then the
published source at the pinned revision. Nothing found is a status, not an
error.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional

from cognalys.workspace import library_data

EMITTERS = library_data("detector_emitters.json")
_FILE_LINE = re.compile(r"^(?P<file>[\w.\-/]+):(?P<line>\d+)$")
MAX_EXCERPT_LINES = 70


def _cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "cognalys" / "detector_emitters"


def _fetch(url: str, timeout: int = 15) -> Optional[str]:
    cache = _cache_dir() / re.sub(r"[^\w.\-]+", "_", url)[-180:]
    try:
        return cache.read_text(encoding="utf-8")
    except OSError:
        pass
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 -- pinned https data URL
            text = response.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 -- offline is a status
        return None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return text


def _reader(entry: dict, fetch: Callable[[str], Optional[str]]) -> Callable[[str], Optional[str]]:
    """Text of a file of the detector's specification set, local checkout first."""
    home = os.environ.get(str(entry.get("home_env", "")), "") if entry.get("home_env") else ""
    local = Path(home) / str(entry.get("checkout_path", "")) if home else None

    def read(name: str) -> Optional[str]:
        if local is not None and (local / name).is_file():
            return (local / name).read_text(encoding="utf-8", errors="replace")
        template = str(entry.get("url_template", ""))
        return fetch(template.format(file=name)) if template else None
    return read


def _excerpt(source: str, line: int) -> str:
    """The emitting line and the comments and checks that lead to it."""
    lines = source.splitlines()
    end = min(len(lines), line + 2)
    start = max(0, line - MAX_EXCERPT_LINES)
    # Begin at the event or method that contains the line, when it is near.
    for index in range(line - 1, start - 1, -1):
        if re.match(r"\s*(?:event\s+\w+|(?:public|private|protected)\s[^;]*\()", lines[index]):
            start = index
            break
    return "\n".join(lines[start:end])


def detector_emitter(error: Dict, fetch: Callable[[str], Optional[str]] = _fetch,
                     emitters_path: Path = EMITTERS) -> Dict:
    """``{"status": "found", "file", "line", "excerpt"}`` for the finding's code."""
    code = str(error.get("detector_code") or error.get("code") or "").strip()
    if not code:
        return {"status": "no_detector_code"}
    try:
        catalog = json.loads(Path(emitters_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "no_catalog"}
    for entry in catalog.get("detectors", []):
        if not re.match(str(entry.get("code_pattern", "^$")), code):
            continue
        read = _reader(entry, fetch)
        table = read(str(entry.get("codes_file", "codes.csv")))
        if not table:
            return {"status": "unavailable", "detail": "the detector's codes table could not be read"}
        row = next((row for row in csv.DictReader(io.StringIO(table)) if row.get("code") == code), None)
        location = _FILE_LINE.match(str((row or {}).get("file_line", "")))
        if not location:
            return {"status": "code_not_in_table", "code": code}
        source = read(location.group("file"))
        if not source:
            return {"status": "unavailable", "detail": f"could not read {location.group('file')}"}
        return {
            "status": "found", "detector": entry.get("name", ""), "code": code,
            "file": location.group("file"), "line": int(location.group("line")),
            "excerpt": _excerpt(source, int(location.group("line"))),
        }
    return {"status": "no_detector_for_code", "code": code}
