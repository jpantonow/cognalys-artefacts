#!/usr/bin/env python3
"""An executable oracle for findings that no detector can re-run.

Most findings in the recent runs are `tool=static:manual` and 78% of their
files are Kotlin, so the PMD replay answered `no_detector_coverage` for all
of them: no patch could be shown to remove what it was meant to remove.

A specification may have a Semgrep rule in data/detector_rules/semgrep/,
named after it (ZipSlipSpec.yml). The rule is the specification's violation
as a local, syntactic predicate, and it is run like any other detector: on
the reported file before and after the patch, attributed to the reported
method. The predicate lives in the rule file, next to the data it encodes,
so a specification gains an oracle by gaining a rule and no code changes.

A rule is written only where such a predicate is faithful: the violation is
visible in the reported method and its usual repair is made there too. A
rule whose repair lives elsewhere (a secret moved to build configuration, a
SimpleDateFormat declared in another class) would keep firing after a
correct patch and score it as incorrect, which is worse than not measuring;
those specifications have no rule and stay `no_detector_coverage`.

Rules live outside the specification files on purpose: experiment manifests
hash the specifications, and adding an oracle must not change the identity
of the rule a finding was adjudicated against. The rules directory is
hashed as its own manifest entry.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from cognalys.workspace import library_data, workspace_root

ROOT = workspace_root()
RULES_DIRECTORY = library_data("detector_rules", "semgrep")
SUFFIXES = (".java", ".kt")
TIMEOUT_SECONDS = 180

# The same vocabulary as detector_replay, repeated rather than imported so
# that module can import this one without a cycle.
ALERT_PRESENT = "alert_present"
ALERT_ABSENT = "alert_absent"
NOT_EXERCISED = "not_exercised"
DETECTOR_UNAVAILABLE = "detector_unavailable"
DETECTOR_ERROR = "detector_error"
INVALID_REPORT = "invalid_report"
FILE_NOT_PROCESSED = "file_not_processed"
# The specification has rules, but none checks the kind of constraint the
# finding reported: there is no oracle for this violation, which is not the
# same as the violation being absent.
CONSTRAINT_NOT_COVERED = "constraint_not_covered"

_RULE_ID = re.compile(r"^\s*- id:\s*(\S+)\s*$", re.MULTILINE)
_CHECKS = re.compile(r"^\s*checks:\s*\[([^\]]*)\]", re.MULTILINE)


def rule_checks(specification: str, rules_path: Optional[Path] = None) -> Dict[str, frozenset]:
    """For each rule of the specification, the constraint kinds it checks.

    Declared in the rule's metadata as `checks: [PROTO]`, using the analyser's
    own site kinds (ALG, KEYSIZE, PROTO, KSTYPE, CONSTR, FORB, ORDER, NOBS).
    A rule without the declaration maps to an empty set.
    """
    path = Path(rules_path) if rules_path else rule_file(specification)
    if path is None or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    starts = list(_RULE_ID.finditer(text))
    checks = {}
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        declared = _CHECKS.search(text, match.end(), end)
        checks[match.group(1)] = frozenset(
            item.strip().strip("'\"").upper()
            for item in (declared.group(1).split(",") if declared else [])
            if item.strip()
        )
    return checks


def constraint_kind(finding: Dict) -> str:
    """The site kind of the reported violation (`SSLCONTEXT-NOBS-03` -> NOBS), or "".

    Read from the finding's own columns or from the `detector_context` the
    extraction carries (utils.corpus_rows.detector_context).
    """
    context = finding.get("detector_context") or {}
    site = str(finding.get("detector_site_kind", "") or context.get("site_kind", "")
               or "").split(":", 1)[0].strip().upper()
    if site:
        return site
    code = finding.get("detector_code", "") or context.get("code", "")
    parts = str(code or "").upper().split("-")
    return parts[1] if len(parts) >= 3 else ""


def rule_file(specification: str) -> Optional[Path]:
    """The rule file for a specification, if it has one."""
    name = str(specification or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    path = RULES_DIRECTORY / f"{name}.yml"
    return path if path.is_file() else None


def find_semgrep() -> str:
    explicit = os.environ.get("TCC_SEMGREP", "")
    if explicit:
        return explicit if Path(explicit).exists() or shutil.which(explicit) else ""
    found = shutil.which("semgrep")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "semgrep"
    return str(local) if local.exists() else ""


@lru_cache(maxsize=2)
def semgrep_version(executable: str) -> str:
    try:
        completed = subprocess.run(
            [executable, "--version"], capture_output=True, text=True,
            timeout=60, stdin=subprocess.DEVNULL, errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "").strip().splitlines()[0] if completed.stdout else ""


def describe(specification: str) -> Dict:
    path = rule_file(specification)
    executable = find_semgrep()
    return {
        "tool": "semgrep",
        "version": semgrep_version(executable) if executable else "",
        "rules": str(path.relative_to(ROOT)) if path else "",
        "rules_sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path else "",
    }


# --- Where the reported method is ------------------------------------------

def _declaration(source: str, method: str, suffix: str, start: int = 0) -> Optional[re.Match]:
    name = re.escape(method)
    if suffix == ".kt":
        # `fun name(`, `fun <T> name(`, `fun Receiver.name(`
        pattern = rf"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?{name}\s*\("
    else:
        pattern = rf"\b{name}\s*\([^;{{}}]*\)\s*(?:throws\s[^{{;]*)?\{{"
    return re.compile(pattern).search(source, start)


def _skip_to(source: str, position: int, stops: str) -> int:
    """Index of the first character in `stops` outside strings, comments and
    nested brackets, starting at `position`; -1 if none."""
    depth = 0
    in_string: Optional[str] = None
    while position < len(source):
        character = source[position]
        if in_string:
            if character == "\\":
                position += 2
                continue
            if source.startswith(in_string, position):
                position += len(in_string)
                in_string = None
                continue
            position += 1
            continue
        if source.startswith('"""', position):
            in_string = '"""'
            position += 3
            continue
        if character in "\"'":
            in_string = character
        elif source.startswith("//", position):
            newline = source.find("\n", position)
            position = len(source) if newline == -1 else newline
            continue
        elif source.startswith("/*", position):
            close = source.find("*/", position + 2)
            position = len(source) if close == -1 else close + 2
            continue
        elif depth == 0 and character in stops:
            return position
        elif character in "({[":
            depth += 1
        elif character in ")}]":
            depth -= 1
            if depth < 0:
                return position if character in stops else -1
        position += 1
    return -1


def _line(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


_ANONYMOUS_OPENING = re.compile(r"\bnew\s+[\w.]+(?:\s*<[^;{}()]*>)?\s*\(")
_NAMED_CLASS = re.compile(r"\b(?:class|interface|enum|record)\s+\w+[^{;]*\{")


def _anonymous_bodies(blanked: str, start: int, end: int) -> List[Tuple[int, int]]:
    """(open brace, close brace) of each anonymous class body in blanked[start:end]."""
    bodies = []
    for match in _ANONYMOUS_OPENING.finditer(blanked, start, end):
        close_paren = _skip_to(blanked, match.end(), ")")
        if close_paren == -1:
            continue
        brace = close_paren + 1
        while brace < end and blanked[brace].isspace():
            brace += 1
        if brace < end and blanked[brace] == "{":
            close = _skip_to(blanked, brace + 1, "}")
            if close != -1:
                bodies.append((brace, close))
    return bodies


def _class_region(source: str, class_name: str, suffix: str) -> Optional[Tuple[int, int]]:
    """Where the nested or anonymous class ``class_name`` names sits in ``source``.

    ToFShare (2026-10-31): the finding was in MainActivity$8.onClick, and the
    replay took the first onClick of the file (lines 204-215), so the rule
    "did not fire" in a method it never looked at. javac numbers a class's
    anonymous classes $1, $2, ... in the order they appear in it, not counting
    those inside another nested class; a named member class is $Name. Java
    only: Kotlin names its lambdas and objects differently. None when the
    chain cannot be followed, never a guess.
    """
    segments = str(class_name or "").split("$")[1:]
    if not segments:
        return (0, len(source))
    if suffix != ".java":
        return None
    from cognalys.utils.identifier_words import blank_comments_and_literals

    blanked = blank_comments_and_literals(source)
    outer = str(class_name).split("$")[0].rsplit(".", 1)[-1]
    top = re.compile(rf"\b(?:class|interface|enum|record)\s+{re.escape(outer)}\b[^{{;]*\{{")\
        .search(blanked)
    if not top or _skip_to(blanked, top.end(), "}") == -1:
        return None
    region = (top.end() - 1, _skip_to(blanked, top.end(), "}"))
    is_enum = top.group(0).lstrip().startswith("enum")
    for segment in segments:
        if segment.isdigit():
            if is_enum and _enum_constant_bodies(blanked, region):
                # javac numbers an enum's constant bodies $1, $2, ... with
                # its anonymous classes; not followed (2026-11-01 review).
                return None
            inner = _anonymous_bodies(blanked, region[0] + 1, region[1])
            named = [(match.end() - 1, _skip_to(blanked, match.end(), "}"))
                     for match in _NAMED_CLASS.finditer(blanked, region[0] + 1, region[1])]
            nested = inner + [span for span in named if span[1] != -1]
            direct = [body for body in inner if not any(
                other != body and other[0] < body[0] and body[1] < other[1] for other in nested)]
            index = int(segment) - 1
            if index < 0 or index >= len(direct):
                return None
            region = direct[index]
        else:
            match = re.compile(rf"\b(?:class|interface|enum|record)\s+{re.escape(segment)}\b"
                               r"[^{;]*\{").search(blanked, region[0], region[1])
            if not match:
                return None
            close = _skip_to(blanked, match.end(), "}")
            if close == -1:
                return None
            region = (match.end() - 1, close)
            is_enum = match.group(0).lstrip().startswith("enum")
        if segment.isdigit():
            is_enum = False
    return region


def _enum_constant_bodies(blanked: str, region: Tuple[int, int]) -> bool:
    """Whether the enum body at ``region`` gives any constant a body (`A { ... },`)."""
    end = region[1]
    depth, index = 0, region[0] + 1
    while index < end:
        char = blanked[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif depth == 0 and char == ";":
            return False
        elif depth == 0 and char == "{":
            return True
        index += 1
    return False


def method_span(source: str, method: str, suffix: str,
                class_name: str = "") -> Optional[Tuple[int, int]]:
    """First and last line of the method's declaration and body, or None.

    Found by the declaration, then by bracket depth outside strings and
    comments. A Kotlin expression body (`fun f() = ...`) ends at the first
    later line indented no deeper than the declaration. When the method
    cannot be found the answer is None, never a guess. With a ``class_name``
    naming a nested or anonymous Java class (`MainActivity$8`), only that
    class's body is searched (_class_region); a Kotlin one keeps the
    file-wide search.
    """
    method = str(method or "").strip()
    if not method or method.startswith("<") or not method.isidentifier():
        return None
    region = (0, len(source))
    if "$" in str(class_name or "") and suffix == ".java":
        region = _class_region(source, class_name, suffix)
        if region is None:
            return None
    match = _declaration(source[:region[1]], method, suffix, region[0])
    if not match:
        return None
    start_line = _line(source, match.start())
    # The parameter list: from its "(" to the matching ")".
    open_paren = source.find("(", match.start() + len(method) - 1)
    close_paren = _skip_to(source, open_paren + 1, ")")
    if close_paren == -1:
        return None
    body = _skip_to(source, close_paren + 1, "{=;")
    if body == -1 or source[body] == ";":
        return None
    if source[body] == "=" and source[body:body + 2] != "==":
        line_start = source.rfind("\n", 0, match.start()) + 1
        indent = len(source[line_start:match.start()]) - len(
            source[line_start:match.start()].lstrip())
        lines = source.splitlines()
        last = _line(source, body)
        for number in range(last, len(lines)):
            text = lines[number]
            if text.strip() and len(text) - len(text.lstrip()) <= indent:
                # A closing bracket at the declaration's indentation still
                # belongs to the expression (`= run {` ... `}`).
                if text.lstrip()[:1] in ")}]":
                    last = number + 1
                break
            last = number + 1
        return start_line, last
    close_brace = _skip_to(source, body + 1, "}")
    if close_brace == -1:
        return None
    return start_line, _line(source, close_brace)


# --- Running a rule --------------------------------------------------------

def scan(target: Path, specification: str, timeout: int = TIMEOUT_SECONDS,
         rules_path: Optional[Path] = None) -> Dict:
    """Run the specification's rule over one file and return its matches.

    `rules_path` evaluates a candidate rule that is not installed yet (see
    rule_proposal); a run always uses the installed file.
    """
    target = Path(target)
    rules = Path(rules_path) if rules_path else rule_file(specification)
    if rules is None:
        return {"status": "no_detector_coverage", "violations": [],
                "detail": f"no Semgrep rule for {specification}"}
    executable = find_semgrep()
    if not executable:
        return {"status": DETECTOR_UNAVAILABLE, "violations": [],
                "detail": "no semgrep executable was found on this machine"}
    if target.suffix not in SUFFIXES:
        return {"status": NOT_EXERCISED, "violations": [],
                "detail": f"{target.suffix or 'this file type'} is not analysed "
                          "by the Java and Kotlin rules"}
    if not target.is_file():
        return {"status": NOT_EXERCISED, "violations": [],
                "detail": "the target file does not exist at this revision"}
    command = [
        executable, "scan", "--config", str(rules), "--json", "--quiet",
        "--metrics=off", "--disable-version-check", "--no-git-ignore",
        str(target.resolve()),
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"status": DETECTOR_ERROR, "violations": [],
                "detail": f"semgrep did not finish within {timeout}s"}
    except OSError as exc:
        return {"status": DETECTOR_UNAVAILABLE, "violations": [],
                "detail": f"semgrep could not be run: {exc}"[:300]}
    record = {"seconds": round(time.monotonic() - started, 2),
              "exit_code": completed.returncode}
    # Semgrep exits 0 with or without matches (no --error); anything else
    # is its own failure.
    if completed.returncode != 0:
        return {**record, "status": DETECTOR_ERROR, "violations": [],
                "detail": "semgrep exited with code "
                          f"{completed.returncode}: {(completed.stderr or '')[-300:]}"}
    try:
        report = json.loads(completed.stdout or "")
    except ValueError:
        return {**record, "status": INVALID_REPORT, "violations": [],
                "detail": "semgrep produced no readable JSON report"}
    errors = [
        error for error in report.get("errors", []) or []
        if isinstance(error, dict)
    ]
    if errors:
        # A syntax or partial-parsing error on the file means the rule may
        # have seen only part of it: an absent match says nothing.
        return {**record, "status": FILE_NOT_PROCESSED, "violations": [],
                "processing_errors": [
                    {"type": str(error.get("type", "")),
                     "message": str(error.get("message", ""))[:300]}
                    for error in errors[:5]
                ],
                "detail": "semgrep reported errors analysing this file"}
    violations = [
        {
            "rule": str(result.get("check_id", "")).rsplit(".", 1)[-1],
            "beginline": str((result.get("start") or {}).get("line", "")),
            "endline": str((result.get("end") or {}).get("line", "")),
            "message": str((result.get("extra") or {}).get("message", ""))[:200],
            "metadata": (result.get("extra") or {}).get("metadata") or {},
        }
        for result in report.get("results", []) or []
        if isinstance(result, dict)
    ]
    return {**record, "status": "scanned", "violations": violations}


def _patterns(metadata: Dict, key: str) -> List[str]:
    value = metadata.get(key) or []
    return [str(item) for item in (value if isinstance(value, list) else [value])]


def _bindings(violation: Dict, source: str) -> Dict[str, str]:
    """Names captured from the matched text by the rule's `bind` regex.

    Semgrep's JSON output does not carry metavariable values here, so a
    rule that needs one (the Cursor's variable name) declares a regular
    expression with named groups, applied to the matched source text.
    """
    pattern = (violation.get("metadata") or {}).get("bind")
    if not pattern or not violation.get("beginline", "").isdigit():
        return {}
    lines = source.splitlines()
    first = int(violation["beginline"]) - 1
    last = int(violation.get("endline") or violation["beginline"])
    match = re.search(str(pattern), "\n".join(lines[first:last]))
    return {key: value for key, value in (match.groupdict() if match else {}).items()
            if value}


def method_conditions_hold(violation: Dict, method_text: str,
                           source: str = "") -> Tuple[bool, str]:
    """Whether the rule's method-level conditions hold for this match.

    Declared in the rule's own metadata, as regular expressions over the
    reported method's text:

      method_requires_any   the method must contain one of these (for
                            ZipSlip: it reads an archive)
      method_excludes_any   the method must contain none of these (a
                            canonical-containment check)
      bind                  a regex with named groups applied to the matched
                            text; `{{NAME}}` in the two lists above is
                            replaced by the escaped captured value

    Evaluated here rather than as Semgrep patterns because Semgrep's Kotlin
    matching does not reach into every block (a check inside `try { }` was
    missed), and a missed exclusion would score a correct repair as
    incorrect. A regular expression over the method's text sees all of it.
    A placeholder that could not be bound makes its pattern match nothing,
    so an unbound exclusion never silences a match by accident.
    """
    metadata = violation.get("metadata") or {}
    bound = _bindings(violation, source)

    def resolve(pattern: str) -> Optional[str]:
        names = re.findall(r"\{\{(\w+)\}\}", pattern)
        if any(name not in bound for name in names):
            return None
        for name in names:
            pattern = pattern.replace("{{" + name + "}}", re.escape(bound[name]))
        return pattern

    required = [p for p in map(resolve, _patterns(metadata, "method_requires_any")) if p]
    if _patterns(metadata, "method_requires_any") and not any(
        re.search(pattern, method_text) for pattern in required
    ):
        return False, "the method does not match method_requires_any"
    for pattern in _patterns(metadata, "method_excludes_any"):
        resolved = resolve(pattern)
        if resolved and re.search(resolved, method_text):
            return False, f"the method matches method_excludes_any: {pattern}"
    return True, ""


def replay(target: Path, specification: str, method: str,
           class_name: str = "", rules_path: Optional[Path] = None,
           constraint_kind: str = "", **_options) -> Dict:
    """Whether the specification's rule fires in the reported method.

    With `constraint_kind`, only the rules that check that kind of
    constraint count. PassNotes and Feeder (exp_final_20261019) reported an
    app-defined TrustManager (NOBS); the only SSLContextSpec rule checks the
    protocol literal, which the correct patch kept as "TLS", so the pair
    read as unchanged and the patch as incorrect. The oracle has to check
    the property that was reported.
    """
    kind = str(constraint_kind or "").strip().upper()
    covering = None
    if kind:
        declared = {rule: kinds for rule, kinds in
                    rule_checks(specification, rules_path).items() if kinds}
        if declared:
            covering = {rule for rule, kinds in declared.items() if kind in kinds}
            if not covering:
                return {
                    "status": CONSTRAINT_NOT_COVERED, "violations": [],
                    "rule": specification, "method": method, "constraint_kind": kind,
                    "detail": f"no {specification} rule checks {kind} constraints; "
                              f"the rules check {sorted(set().union(*declared.values()))}",
                }
    result = scan(target, specification, rules_path=rules_path)
    if result["status"] != "scanned":
        return {**result, "rule": specification, "method": method}
    try:
        source = Path(target).read_text(encoding="utf-8", errors="replace")
    except OSError:
        source = ""
    span = method_span(source, method, Path(target).suffix, class_name)
    if span is None:
        hits = list(result["violations"])
        scope = "file"
        method_text = source
    else:
        hits = [
            violation for violation in result["violations"]
            if violation["beginline"].isdigit()
            and span[0] <= int(violation["beginline"]) <= span[1]
        ]
        scope = "method"
        method_text = "\n".join(source.splitlines()[span[0] - 1:span[1]])
    if covering is not None:
        hits = [violation for violation in hits if violation["rule"] in covering]
    excluded = []
    kept = []
    for violation in hits:
        holds, reason = method_conditions_hold(violation, method_text, source)
        (kept if holds else excluded).append({**violation, "excluded_because": reason})
    hits = [{key: value for key, value in violation.items()
             if key != "excluded_because"} for violation in kept]
    return {
        **result,
        "status": ALERT_PRESENT if hits else ALERT_ABSENT,
        "rule": specification,
        "method": method,
        **({"constraint_kind": kind, "rules_checking_it": sorted(covering)}
           if covering is not None else {}),
        "match_scope": scope,
        "method_lines": list(span) if span else [],
        "matching_violations": hits,
        "excluded_by_method_conditions": [
            {"beginline": item["beginline"], "reason": item["excluded_because"]}
            for item in excluded
        ],
        "violations_in_file": len(result["violations"]),
        "detail": (
            f"the {specification} rule fires in {method}" if hits and scope == "method"
            else "the rule fires in this file; the reported method could not be "
                 "located, so the match is attributed to the file" if hits
            else f"the {specification} rule does not fire in {method}"
            if scope == "method" else
            "the rule does not fire in this file"
        ),
    }


def rule_files() -> List[Path]:
    return sorted(RULES_DIRECTORY.glob("*.yml")) if RULES_DIRECTORY.is_dir() else []
