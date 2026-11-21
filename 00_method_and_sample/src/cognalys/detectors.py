"""Findings from a repository, produced by running a detector over it.

The pipeline began from findings someone else had collected: runtime
verification of an APK, a CSV. A repository on its own carries no list of
problems, so to start from one a detector has to run over it. This module
runs the project's Semgrep rules (data/detector_rules/semgrep, one file per
specification) over a checkout and turns each alert into a Finding in the
corpus vocabulary the steps read, located by the class and method that
enclose the reported line.

The rules were written as oracles, to replay one reported finding on one
file; run over a whole repository they are a discovery detector whose
coverage is what those rules cover, no more. Every way a scan can fail to
look (no semgrep, a rule that does not load, a file it could not parse) is
recorded in the result, so an empty list of findings always means the rules
looked and found nothing.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cognalys.extraction.value_consumers import enclosing_method_name
from cognalys.models import Finding
from cognalys.validation import semgrep_rules

SCAN_TIMEOUT_SECONDS = 900
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)", re.M)
_TYPE_DECLARATION = re.compile(
    r"^\s*(?:[\w@]+\s+)*(?:class|interface|object|enum)\s+([A-Za-z_]\w*)")


@dataclass(frozen=True)
class RepositoryTarget:
    """A repository to work on: a local checkout or a Git URL."""

    local_path: Path | None = None
    url: str = ""
    project_id: str = ""

    def source(self) -> str:
        return str(Path(self.local_path).resolve()) if self.local_path else self.url

    def name(self) -> str:
        """The project's identity, without a trailing .apk.

        Findings carry it as `<name>.apk` (the corpus column the steps key
        on), and the pipeline's own clones are named that way already.
        """
        if self.project_id:
            name = self.project_id
        elif self.local_path:
            name = Path(self.local_path).resolve().name
        else:
            name = self.url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        return name.removesuffix(".apk")


@dataclass(frozen=True)
class Snapshot:
    """What a local checkout holds right now."""

    path: Path
    commit: str
    dirty: bool
    changed_files: tuple[str, ...] = ()


def snapshot(path: Path) -> Snapshot:
    """The commit a checkout is at, and whether its tree differs from it.

    A patch is computed against the commit (the pipeline clones it); with
    uncommitted changes, what the user has on disk is not what was analysed.
    """
    path = Path(path).resolve()
    commit = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                            capture_output=True, text=True)
    if commit.returncode != 0:
        raise ValueError(f"{path} is not a Git checkout with a commit: "
                         f"{commit.stderr.strip()[:200]}")
    status = subprocess.run(["git", "-C", str(path), "status", "--porcelain"],
                            capture_output=True, text=True, check=True)
    changed = tuple(line[3:] for line in status.stdout.splitlines() if line.strip())
    return Snapshot(path=path, commit=commit.stdout.strip(), dirty=bool(changed),
                    changed_files=changed)


@dataclass(frozen=True)
class ScanResult:
    """What a scan found, and everything that kept it from looking."""

    target: str
    commit: str
    findings: tuple[Finding, ...] = ()
    problems: tuple[dict, ...] = ()
    specifications: tuple[str, ...] = ()
    # Matches deliberately not reported (test code, a rule's method
    # conditions): not problems, since nothing kept the scan from looking.
    excluded: tuple[dict, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.problems


# Test sources in Gradle and Maven layouts.
TEST_SOURCE = re.compile(r"(?:^|/)src/(?:test|androidTest|sharedTest|testFixtures)\w*/")


def rule_conditions_hold(result: dict, source: Path, method: str) -> tuple[bool, str]:
    """A Semgrep result against its rule's method conditions (method_excludes_any...)."""
    extra = result.get("extra") or {}
    violation = {
        "metadata": extra.get("metadata") or {},
        "beginline": str((result.get("start") or {}).get("line") or ""),
        "endline": str((result.get("end") or {}).get("line") or ""),
    }
    if not violation["metadata"]:
        return True, ""
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True, ""
    span = semgrep_rules.method_span(text, method, source.suffix)
    method_text = "\n".join(text.splitlines()[span[0] - 1:span[1]]) if span else text
    return semgrep_rules.method_conditions_hold(violation, method_text, text)


_LITERALS_AND_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//.*$')


def _opened_since(lines: list[str]) -> bool:
    """Whether the first line's braces are still open at the last line.

    A declaration without a body (`class A` with nothing after it) counts as
    open: it is the file's type until something says otherwise.
    """
    depth, opened = 0, False
    for line in lines:
        code = _LITERALS_AND_COMMENTS.sub("", line)
        for char in code:
            if char == "{":
                depth += 1
                opened = True
            elif char == "}":
                depth -= 1
                if opened and depth <= 0:
                    return False
    return True


def _enclosing_class(lines: list[str], index: int, path: Path, text: str) -> str:
    package = _PACKAGE.search(text)
    prefix = f"{package.group(1)}." if package else ""
    for position in range(index, -1, -1):
        declaration = _TYPE_DECLARATION.match(lines[position])
        # Only a type still open at the reported line encloses it: AI Hub's
        # top-level updateWebViewSettings follows a closed `object
        # WebViewProxyHelper { }` and was reported as its member.
        if declaration and _opened_since(lines[position:index + 1]):
            return prefix + declaration.group(1)
    # Kotlin compiles top-level functions into <File>Kt.
    stem = path.stem + ("Kt" if path.suffix == ".kt" else "")
    return prefix + stem


def locate(path: Path, line: int) -> tuple[str, str]:
    """The class and method that enclose ``line`` of a source file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    index = min(max(line - 1, 0), max(len(lines) - 1, 0))
    return _enclosing_class(lines, index, path, text), enclosing_method_name(lines, index)


def rule_specifications(rules: list[Path]) -> dict[str, str]:
    """Semgrep rule id -> the specification (rule file) that declares it."""
    owned = {}
    for path in rules:
        text = path.read_text(encoding="utf-8", errors="replace")
        for rule in re.findall(r"^\s*-\s*id:\s*['\"]?([\w.\-]+)", text, re.M):
            owned[rule] = path.stem
    return owned


def discover_semgrep(target: RepositoryTarget, specifications: list[str] | None = None,
                     timeout: int = SCAN_TIMEOUT_SECONDS) -> ScanResult:
    """Run the Semgrep rules over a local checkout and return its findings."""
    if not target.local_path:
        raise ValueError("discovery runs on a local checkout; clone the URL first")
    repo = snapshot(target.local_path)
    rules = [path for path in semgrep_rules.rule_files()
             if not specifications or path.stem in specifications]
    executable = semgrep_rules.find_semgrep()
    project = target.name()
    if not executable:
        return ScanResult(target=target.source(), commit=repo.commit,
                          problems=({"status": semgrep_rules.DETECTOR_UNAVAILABLE,
                                     "detail": "no semgrep executable was found"},),
                          specifications=tuple(path.stem for path in rules))
    findings, problems, excluded = [], [], []
    # One Semgrep process for every rule: started once per rule, the scan
    # parsed the whole checkout nine times (about four minutes an app on
    # /mnt/c). A result names its rule id, which says its specification.
    specification_of = rule_specifications(rules)
    every = ",".join(path.stem for path in rules)
    command = [executable, "scan", *[item for rule in rules for item in ("--config", str(rule))],
               "--json", "--quiet", "--metrics=off", "--disable-version-check", str(repo.path)]
    report = {}
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=timeout, stdin=subprocess.DEVNULL,
                                   errors="replace", cwd=str(repo.path))
        try:
            report = json.loads(completed.stdout or "")
        except ValueError:
            problems.append({"specification": every,
                             "status": semgrep_rules.INVALID_REPORT,
                             "detail": (completed.stderr or "")[-300:]})
    except subprocess.TimeoutExpired:
        problems.append({"specification": every,
                         "status": semgrep_rules.DETECTOR_ERROR,
                         "detail": f"semgrep did not finish within {timeout}s"})
    for error in report.get("errors", []) or []:
        problems.append({"specification": specification_of.get(
                            str(error.get("rule_id") or "").rsplit(".", 1)[-1], every),
                         "status": semgrep_rules.FILE_NOT_PROCESSED,
                         "path": str((error.get("path") or "")),
                         "detail": str(error.get("message", ""))[:300]})
    for result in report.get("results", []) or []:
        source = Path(str(result.get("path", "")))
        source = source if source.is_absolute() else repo.path / source
        line = int((result.get("start") or {}).get("line") or 0)
        class_name, method = locate(source, line)
        relative = source.resolve().relative_to(repo.path).as_posix()
        check = str(result.get("check_id", "")).rsplit(".", 1)[-1]
        spec = specification_of.get(check, "unknown")
        if TEST_SOURCE.search(relative):
            # A test's own Cursor or digest is not what a maintainer
            # ships (nerdcalci's DatabaseMigrationTest): set apart, counted.
            excluded.append({"specification": spec, "reason": "test code",
                             "path": relative, "line": line, "method": method})
            continue
        if method:
            holds, reason = rule_conditions_hold(result, source, method)
            if not holds:
                # The same method conditions the replay applies: without
                # them `cursor?.use { }` was reported as a leak (euc_soh).
                excluded.append({"specification": spec, "reason": reason,
                                 "path": relative, "line": line, "method": method})
                continue
        if not method:
            # A field initializer or a top-level statement: the steps
            # locate code by method, and inventing one would be wrong.
            problems.append({"specification": spec, "status": "not_in_a_method",
                             "path": relative, "line": line, "rule": check})
            continue
        findings.append(Finding(
            apk=f"{project}.apk", class_name=class_name, method=method,
            spec=spec,
            message=str((result.get("extra") or {}).get("message", "")),
            repo_url=target.source(), commit=repo.commit, tool="static:semgrep",
            detector={"code": check, "source_location": f"{relative}:{line}"},
            extra={"origin": "app", "source_file": relative, "line": str(line)},
        ))
    return ScanResult(target=target.source(), commit=repo.commit,
                      findings=tuple(findings), problems=tuple(problems),
                      specifications=tuple(path.stem for path in rules),
                      excluded=tuple(excluded))
