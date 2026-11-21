"""Findings as people have them, turned into the rows every step reads.

The steps read one vocabulary (apk, class, method, spec, message, repo_url,
commit, tool, detector_*; see cognalys.sources). A user rarely has a file in
it: they have a SARIF report, Semgrep's --json output, or a spreadsheet whose
columns are called className and rule. This module takes those as they are:

- SARIF (.sarif, or any JSON with SARIF's "runs") and Semgrep JSON reports;
- rows whose columns use a common other name (className, rule_id, ...);
- findings located by file and line instead of class and method, which are
  resolved in the checkout given with ``repo``;
- several files at once, each row remembering the file and line it came from
  (the findings_source column).

Nothing is guessed. A row that still lacks a column the steps need, a file
and line outside any method, or a location without a checkout to read, is
returned as a problem with the reason, and check() says what a file lacks
before any run is spent on it.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from cognalys.sources import SOURCE_PROBLEM, read_rows

# The columns a finding cannot go without: the steps find the code by
# (apk, class, method) and the rules it breaks by spec.
REQUIRED = ("apk", "class", "method", "spec")
# Without these a finding is still analysed, with less to go on.
RECOMMENDED = ("message", "repo_url", "commit")
PROVENANCE = "findings_source"

# Other names for the corpus columns, compared without case, "_", "-" or " ".
ALIASES = {
    "class": ("classname", "csvclass", "fqcn", "qualifiedclass", "qualifiedclassname"),
    "method": ("methodname", "function", "functionname", "member"),
    "spec": ("specification", "rule", "ruleid", "checkid", "rulename"),
    "message": ("description", "msg", "text", "errormessage", "details"),
    "repo_url": ("repository", "repourl", "repositoryurl", "giturl"),
    "commit": ("sha", "revision", "commithash", "commitid", "rev"),
    "apk": ("apkname", "apkfile"),
    "tool": ("detector", "analyzer", "scanner"),
    "source_file": ("file", "path", "filepath", "sourcefile"),
    "line": ("lineno", "linenumber", "startline"),
}


def _normal(name: str) -> str:
    return re.sub(r"[\s_\-]", "", name.lower())


_ALIAS_TARGET = {_normal(alias): column for column, names in ALIASES.items() for alias in names}
_ALIAS_TARGET.update({_normal(column): column for column in ALIASES})


@dataclass
class Intake:
    rows: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return not self.problems


def spec_name(rule: str) -> str:
    """A rule id as a specification name usable in file names."""
    return re.sub(r"[^\w.\-]", "_", rule.strip()) or "UnnamedRule"


def apply_aliases(row: Mapping[str, str], renamed: dict | None = None) -> dict:
    """The row with known other names of corpus columns renamed.

    A column already named the corpus way wins over an alias of it, and an
    unknown column is kept as is, so a detector's own fields still reach
    the analysis.
    """
    present = {key for key in row if key in ALIASES or key in REQUIRED + RECOMMENDED}
    result = {}
    for key, value in row.items():
        if key is None:
            result[key] = value
            continue
        target = _ALIAS_TARGET.get(_normal(str(key)), key)
        if target != key and target not in present and target not in result:
            if renamed is not None:
                renamed[str(key)] = target
            result[target] = value
        else:
            result.setdefault(key, value)
    return result


def _rule_specification(rule: str, owned: Mapping[str, str], dotted: bool) -> str:
    """A reported rule id as a specification name.

    Semgrep prefixes an id with the directory of its rules file
    (home.me.rules.weak-cipher); only the last part names the rule.
    """
    name = rule.rsplit(".", 1)[-1] if dotted else rule
    return owned.get(name) or spec_name(name)


def _sarif_rows(report: Mapping) -> list[dict]:
    rows = []
    owned = own_rule_specifications()
    for run in report.get("runs") or []:
        driver = ((run.get("tool") or {}).get("driver") or {})
        tool = re.sub(r"\s+", "-", str(driver.get("name") or "sarif").strip())
        for result in run.get("results") or []:
            rule = str(result.get("ruleId") or (result.get("rule") or {}).get("id") or "")
            message = result.get("message") or {}
            text = message.get("text") or message.get("markdown") or ""
            location = ((result.get("locations") or [{}])[0].get("physicalLocation") or {})
            uri = str((location.get("artifactLocation") or {}).get("uri") or "")
            line = (location.get("region") or {}).get("startLine") or ""
            specification = _rule_specification(rule, owned, "semgrep" in tool.lower())
            rows.append({"spec": specification, "message": str(text),
                         "source_file": uri.removeprefix("file://"), "line": str(line),
                         "tool": f"static:{tool.lower()}", "detector_code": rule,
                         "detector_level": str(result.get("level") or "")})
    return rows


def own_rule_specifications() -> dict[str, str]:
    """Semgrep rule id -> specification, for cognalys's own rules."""
    from cognalys.validation import semgrep_rules

    owned = {}
    for path in semgrep_rules.rule_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for rule in re.findall(r"^\s*-\s*id:\s*['\"]?([\w.\-]+)", text, re.M):
            owned[rule] = path.stem
    return owned


def _semgrep_rows(report: Mapping) -> list[dict]:
    rows = []
    owned = own_rule_specifications()
    for result in report.get("results") or []:
        check = str(result.get("check_id") or "")
        extra = result.get("extra") or {}
        rows.append({"spec": _rule_specification(check, owned, dotted=True),
                     "message": str(extra.get("message") or ""),
                     "source_file": str(result.get("path") or ""),
                     "line": str((result.get("start") or {}).get("line") or ""),
                     "tool": "static:semgrep", "detector_code": check,
                     "detector_level": str(extra.get("severity") or "")})
    return rows


def report_kind(path: Path) -> str:
    """sarif, semgrep or rows: how a findings file is to be read."""
    name = path.name.lower()
    if name.endswith(".sarif") or name.endswith(".sarif.json"):
        return "sarif"
    if path.suffix.lower() != ".json":
        return "rows"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return "rows"
    if isinstance(data, dict) and isinstance(data.get("runs"), list):
        return "sarif"
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return "semgrep"
    return "rows"


def file_rows(path: Path) -> list[dict]:
    """A findings file's records, before aliases, in file order."""
    kind = report_kind(path)
    if kind == "rows":
        return read_rows(path)
    report = json.loads(path.read_text(encoding="utf-8-sig"))
    return _sarif_rows(report) if kind == "sarif" else _semgrep_rows(report)


def _checkout_file(repo: Path, name: str) -> Path | None:
    """The checkout file a report names, absolute, relative or by its tail."""
    candidate = Path(name)
    for path in ((candidate,) if candidate.is_absolute() else (repo / candidate,)):
        if path.is_file():
            return path
    parts = [part for part in candidate.parts if part not in ("/", "\\")]
    for start in range(1, len(parts)):
        path = repo.joinpath(*parts[start:])
        if path.is_file():
            return path
    return None


def read_findings(paths: Sequence[Path | str], repo: Path | str | None = None) -> Intake:
    """Every finding in ``paths`` as a corpus row; what cannot be one, as a problem.

    ``repo`` is the local checkout the findings are about. It resolves
    file+line locations to class and method and fills apk, repo_url and
    commit where a row leaves them empty.
    """
    intake = Intake()
    checkout = None
    if repo:
        from cognalys.detectors import RepositoryTarget, snapshot

        state = snapshot(Path(repo))
        checkout = (state.path, state.commit, f"{RepositoryTarget(local_path=state.path).name()}.apk")
    for path in map(Path, paths):
        try:
            records = file_rows(path)
        except (OSError, ValueError) as exc:
            intake.problems.append({"source": str(path), "problem": f"not readable: {exc}"})
            continue
        # A CSV's first finding is on line 2, under its header.
        offset = 2 if path.suffix.lower() not in (".json", ".jsonl", ".ndjson", ".sarif") else 1
        for number, record in enumerate(records, offset):
            source = f"{path.name}:{number}"
            if record.get(SOURCE_PROBLEM):
                intake.problems.append({"source": source, "problem": record[SOURCE_PROBLEM]})
                continue
            row = apply_aliases(record, intake.renamed)
            row[PROVENANCE] = source
            if checkout:
                root, commit, apk = checkout
                if not row.get("apk"):
                    row["apk"] = apk
                if not row.get("repo_url"):
                    row["repo_url"] = str(root)
                if not row.get("commit"):
                    row["commit"] = commit
            if not (row.get("class") and row.get("method")) and row.get("source_file"):
                problem = _locate(row, checkout[0] if checkout else None)
                if problem:
                    intake.problems.append({"source": source, "problem": problem})
                    continue
            apk = str(row.get("apk") or "").strip()
            if apk and not apk.lower().endswith(".apk"):
                # The steps key every artifact on <name>.apk.
                row["apk"] = f"{apk}.apk"
            missing = [column for column in REQUIRED if not str(row.get(column) or "").strip()]
            if missing:
                intake.problems.append({"source": source,
                                        "problem": f"missing {', '.join(missing)}"})
                continue
            intake.rows.append(row)
    intake.rows = merge_same_finding(intake.rows)
    return intake


MERGED_COLUMNS = (PROVENANCE, "detector_source_location", "line")


def merge_same_finding(rows: list[dict]) -> list[dict]:
    """One row per (apk, class, method, spec), the steps' finding identity.

    Two reports in one method (two lines, or the same result in a SARIF and
    a JSON export) are one finding to analyse and repair; the first row is
    kept and the others' locations and sources are added to it.
    """
    merged: dict[tuple, dict] = {}
    for row in rows:
        key = tuple(str(row.get(column) or "") for column in REQUIRED)
        first = merged.get(key)
        if first is None:
            merged[key] = row
            continue
        for column in MERGED_COLUMNS:
            values = [value for value in str(first.get(column) or "").split("; ") if value]
            value = str(row.get(column) or "")
            if value and value not in values:
                first[column] = "; ".join(values + [value])
    return list(merged.values())


def _locate(row: dict, repo: Path | None) -> str:
    """Fill class and method from source_file and line; the problem, or ""."""
    from cognalys.detectors import locate

    if repo is None:
        return ("located by file and line, which needs the checkout: "
                "pass --repo PATH")
    path = _checkout_file(repo, str(row["source_file"]))
    if path is None:
        return f"{row['source_file']} is not in the checkout"
    try:
        line = int(str(row.get("line") or "").strip())
    except ValueError:
        return f"no line number for {row['source_file']}"
    class_name, method = locate(path, line)
    if not method:
        return f"{row['source_file']}:{line} is not inside a method"
    row.setdefault("class", "")
    row.setdefault("method", "")
    row["class"] = row["class"] or class_name
    row["method"] = row["method"] or method
    row["source_file"] = path.relative_to(repo).as_posix()
    row.setdefault("detector_source_location", f"{row['source_file']}:{line}")
    return ""


def suggestions(columns: Iterable[str]) -> dict[str, str]:
    """For each required column absent from ``columns``, the closest present one."""
    columns = [column for column in columns if column]
    hints = {}
    for required in REQUIRED + RECOMMENDED:
        if required in columns:
            continue
        match = difflib.get_close_matches(required, columns, n=1, cutoff=0.6)
        if match:
            hints[required] = match[0]
    return hints


TEMPLATE_ROWS = (
    {"apk": "myapp.apk", "class": "com.example.crypto.Hasher", "method": "digest",
     "spec": "MessageDigestSpec", "message": "MD5 is not an allowed digest algorithm",
     "repo_url": "https://github.com/example/myapp", "commit": "0123456789abcdef0123456789abcdef01234567",
     "tool": "my-detector"},
    {"apk": "", "class": "", "method": "", "spec": "CipherSpec",
     "message": "ECB mode", "repo_url": "", "commit": "", "tool": "my-detector",
     "source_file": "app/src/main/java/com/example/crypto/Box.java", "line": "42"},
)
