"""The application call that handed a library frame the reported value.

A runtime detector reports the frame that made the governed call, and when
the application calls the API through a library that implements it, that
frame is the library's. ch.rmy.android.http_shortcuts was reported at
org.conscrypt.ConscryptEngineSocket.setEnabledProtocols with TLSv1: Conscrypt
is the provider implementing SSLSocket.setEnabledProtocols, and the array
came from the application's TLSEnabledSSLSocketFactory, which sets
``enabledProtocols = arrayOf("TLSv1", ...)``. The finding was recorded as a
dependency candidate, analysed against bytecode it did not have, and closed
as unknown, while the defect sat in the checkout.

The attribution is deliberately narrow, because moving a finding to the wrong
code is worse than leaving it where it was reported:

* the reported method must itself be one of the API methods the
  specification governs, so the library frame is the API's implementation
  and its argument came from the caller. A library method that merely uses
  the API internally (okio's ByteString.digest asking for MD5) is never
  attributed: an application call with the same value elsewhere would be a
  different call, not this one;
* the reported value must be a concrete literal, and an application
  statement must pass that exact string literal to that API method;
* every match must lie in one declaration. Several candidates are recorded
  for audit and the finding stays where it was reported.

Test sources are not searched: a test is not where the shipped value comes
from.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional

SOURCE_SUFFIXES = (".java", ".kt")
EXCLUDED_PARTS = {
    "build", ".gradle", ".git", "test", "androidTest", "testDebug",
    "testRelease", "sharedTest", "node_modules",
}
# How far a statement may continue after the line that names the API: an
# array literal is often spread over the following lines.
STATEMENT_LINES = 8
MAX_CANDIDATES = 8

FOUND = re.compile(r"\bbut\s+found\s+(?P<value>.+?)\s*\.?\s*$", re.IGNORECASE)
# "`ep1: setEnabledProtocols(protocols);`" and "`g1: digest = getInstance(alg)`"
EVENT_CALL = re.compile(r"`\s*\w+\s*:\s*(?:[\w.]+\s*=\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")
IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")


def reported_value(row: Dict) -> str:
    """The concrete value the detector saw, or "" when it recorded none."""
    value = str(row.get("detector_value", "") or "").strip()
    if not value:
        match = FOUND.search(str(row.get("message", "") or ""))
        value = match.group("value").strip() if match else ""
    value = value.strip("'\"").strip()
    if not re.search(r"[A-Za-z0-9]", value) or len(value) > 120:
        return ""
    return value


def governed_methods(specification: Dict) -> List[str]:
    """The API method names the specification's events observe."""
    names: List[str] = []
    for event in specification.get("events") or []:
        text = json.dumps(event) if not isinstance(event, str) else event
        for match in EVENT_CALL.finditer(text):
            name = match.group("name")
            if name not in names:
                names.append(name)
    return names


def load_specification(specifications_dir: Path, spec: str) -> Dict:
    path = Path(specifications_dir) / f"{spec}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def api_name_patterns(method: str) -> List[re.Pattern]:
    """A call of the method, and its Kotlin property form for a setter."""
    patterns = [re.compile(rf"\b{re.escape(method)}\s*\(")]
    if method.startswith("set") and len(method) > 3 and method[3].isupper():
        prop = method[3].lower() + method[4:]
        patterns.append(re.compile(rf"\.\s*{re.escape(prop)}\s*=(?!=)"))
    return patterns


def _source_files(repo_root: Path) -> Iterable[Path]:
    for path in repo_root.rglob("*"):
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        relative = path.relative_to(repo_root).parts
        if any(part in EXCLUDED_PARTS for part in relative[:-1]):
            continue
        yield path


def statement_from(lines: List[str], index: int) -> str:
    """The statement starting at ``lines[index]``, up to STATEMENT_LINES.

    It ends at the first line where every bracket opened since the start is
    closed again, or at a semicolon outside brackets, so a literal in the next
    statement is not read as an argument of this one.
    """
    depth = 0
    taken: List[str] = []
    for line in lines[index:index + STATEMENT_LINES]:
        for position, char in enumerate(line):
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == ";" and depth <= 0:
                taken.append(line[:position])
                return "\n".join(taken)
        taken.append(line)
        if depth <= 0:
            break
    return "\n".join(taken)


def find_call_sites(repo_root: Path, method: str, value: str) -> List[Dict]:
    """Application statements that pass the literal value to the method."""
    literal = re.compile(r'"' + re.escape(value) + r'"')
    patterns = api_name_patterns(method)
    sites: List[Dict] = []
    for path in sorted(_source_files(repo_root)):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines):
            if not any(pattern.search(line) for pattern in patterns):
                continue
            if not literal.search(statement_from(lines, index)):
                continue
            sites.append({
                "path": path.relative_to(repo_root).as_posix(),
                "line": index + 1,
                "statement": line.strip()[:240],
            })
            if len(sites) >= MAX_CANDIDATES:
                return sites
    return sites


DECLARED_NAME = re.compile(
    r"\bfun\s+(?:<[^>]+>\s*)?(?:[\w?.<>]+\.)?(?P<kotlin>\w+)\s*\("
    r"|(?P<java>\w+)\s*\("
)
NOT_A_NAME = {"if", "for", "while", "switch", "catch", "synchronized", "return", "new"}


def declared_name(declaration: str) -> str:
    """The name a Java or Kotlin declaration declares, or ""."""
    # Annotations first: @SuppressLint("X") would otherwise read as the name.
    header = re.sub(r"@[\w.]+(?:\([^)]*\))?", " ", declaration.split("{", 1)[0])
    for match in DECLARED_NAME.finditer(header):
        name = match.group("kotlin") or match.group("java")
        if name and name not in NOT_A_NAME:
            return name
    return ""


def attribute_to_application(
    repo_root: Path,
    row: Dict,
    specification: Dict,
    containing_declaration,
) -> Dict:
    """Where in the application the reported library frame's value came from.

    ``containing_declaration(path, line)`` returns the (start, end) lines of
    the declaration enclosing ``line`` in ``path``, or None; the extractor
    supplies it so the attribution and the extraction agree on boundaries.

    Returns {"status": "attributed", ...} with the single call site, or a
    status saying why the finding stays where it was reported.
    """
    method = str(row.get("method", "") or "").strip()
    record: Dict = {
        "reported_frame": {
            "class": str(row.get("class", "") or "").strip(),
            "method": method,
        },
    }
    governed = governed_methods(specification)
    if not IDENTIFIER.match(method) or method not in governed:
        return {**record, "status": "not_applicable",
                "reason": "the reported method is not an API method the "
                          "specification governs",
                "governed_methods": governed}
    value = reported_value(row)
    if not value:
        return {**record, "status": "not_applicable",
                "reason": "the report carries no concrete value to match"}
    record["value"] = value
    sites = find_call_sites(Path(repo_root), method, value)
    if not sites:
        return {**record, "status": "no_application_call",
                "reason": f"no application statement passes \"{value}\" "
                          f"to {method}"}
    declarations = {}
    for site in sites:
        bounds = containing_declaration(Path(repo_root) / site["path"], site["line"])
        site["declaration"] = list(bounds) if bounds else None
        declarations.setdefault((site["path"], tuple(bounds) if bounds else None), site)
    record["call_sites"] = sites
    if len(declarations) != 1 or next(iter(declarations))[1] is None:
        return {**record, "status": "ambiguous",
                "reason": f"{len(sites)} application statement(s) in "
                          f"{len(declarations)} declaration(s) match; the "
                          "finding stays at the reported frame"}
    return {**record, "status": "attributed", "call_site": sites[0]}
