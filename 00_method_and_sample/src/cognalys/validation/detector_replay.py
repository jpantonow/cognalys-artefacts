#!/usr/bin/env python3
"""Re-run the detector that reported a finding, before and after the patch.

The pipeline's strongest executable evidence is that the patched project
still compiles. Compiling is not repairing: it shows the change is
well-formed, not that the reported problem is gone. The detector that
reported the finding can be asked directly, and it is the only thing that
can answer in the detector's own terms.

Two states exist that a naive replay would collapse into "fixed", and
keeping them apart is most of the value here:

  not_reproduced   the alert is absent from the BASELINE too. The patch
                   cannot be credited with removing something that was not
                   there when the scan was re-run -- most often because the
                   corpus was produced under different rulesets, at a
                   different revision, or by a scan of the whole project
                   rather than of this file.
  not_exercised    the scan did not analyse this file at all. PMD's Java
                   rulesets do not parse Kotlin, so for a .kt target the
                   answer is that nothing was checked. Reporting that as
                   "no alert" would count the pipeline's largest blind spot
                   as a success.

A third family of states exists because the detector itself can fail, and
a failed scan produces no violations exactly as a clean file does:

  detector_error      the detector ran and exited abnormally, or timed out.
  invalid_report      it exited cleanly but left no readable report.
  file_not_processed  the report is valid but records that this file could
                      not be analysed (a parse error, or a configuration
                      error that disabled rules).

Any of them on either side of a pair decides the pair: a repair is credited
only when both revisions were scanned and both reports can be trusted.

Nothing here is a general claim about security. A rule stopping to fire
means the rule stops firing.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, Optional, Tuple



DEFAULT_RULESETS = ",".join((
    "category/java/errorprone.xml",
    "category/java/bestpractices.xml",
    "category/java/performance.xml",
))
JAVA_SUFFIX = ".java"
GIT_TIMEOUT_SECONDS = 60

ALERT_PRESENT = "alert_present"
ALERT_ABSENT = "alert_absent"
NOT_EXERCISED = "not_exercised"
DETECTOR_UNAVAILABLE = "detector_unavailable"
DETECTOR_ERROR = "detector_error"
INVALID_REPORT = "invalid_report"
FILE_NOT_PROCESSED = "file_not_processed"
SCANNED = "scanned"
METHOD_REMOVED = "method_removed"
# The method the rule is matched on was renamed, not deleted: the alert's
# absence says nothing either way (Orgzly's sha1 -> sha256 read as removed).
METHOD_RENAMED = "method_renamed"
# The replayed rule still fires on a value the reporting detector's own
# message accepts (Fread's "TLS", accepted by RVSec as TLSV1.2,TLSV1.3,TLS
# and rejected by SSLContextSpec.yml): the two rules disagree, the patch is
# not contradicted.
DETECTOR_MISMATCH = "detector_mismatch"
# semgrep_rules.CONSTRAINT_NOT_COVERED: no rule checks the reported kind of
# constraint, so the replay is no oracle for this finding.
CONSTRAINT_NOT_COVERED = "constraint_not_covered"

# The states in which no statement about the alert can be made, in the order
# that decides a pair when its two sides fail differently.
UNTRUSTED_STATES = (
    CONSTRAINT_NOT_COVERED,
    DETECTOR_UNAVAILABLE, DETECTOR_ERROR, INVALID_REPORT,
    FILE_NOT_PROCESSED, NOT_EXERCISED,
)


def find_detector(explicit: str = "") -> str:
    """The PMD executable, or "" when the machine has none."""
    if explicit:
        return explicit if Path(explicit).exists() or shutil.which(explicit) else ""
    return shutil.which("pmd") or ""


def _read_report(report: Path) -> Dict:
    from cognalys.validation.pmd_report import read_report

    return read_report(report)


def _same_file(reported: str, target: Path) -> bool:
    if not reported:
        return False
    try:
        return Path(reported).resolve() == target.resolve()
    except OSError:
        return Path(reported).name == target.name


def scan_file(
    target: Path, rulesets: str = DEFAULT_RULESETS, minimum_priority: int = 5,
    detector: str = "", timeout: int = 300,
) -> Dict:
    """Run the detector over one file and return what it reported.

    Scoped to the file rather than the project: the question is about this
    file's code, and a project scan would take minutes and attribute
    nothing.
    """
    target = Path(target)
    executable = find_detector(detector)
    if not executable:
        return {
            "status": DETECTOR_UNAVAILABLE,
            "detail": "no PMD executable was found on this machine; the "
                      "original detector cannot be replayed here",
            "violations": [],
        }
    if target.suffix != JAVA_SUFFIX:
        return {
            "status": NOT_EXERCISED,
            "detail": (
                f"{target.suffix or 'this file type'} is not analysed by the "
                "Java rulesets the corpus was produced with, so the detector "
                "checked nothing here"
            ),
            "violations": [],
        }
    if not target.is_file():
        return {
            "status": NOT_EXERCISED,
            "detail": "the target file does not exist at this revision",
            "violations": [],
        }
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "report.xml"
        command = [
            executable, "check", "--dir", str(target.resolve()),
            "--rulesets", rulesets, "--format", "xml",
            "--report-file", str(report), "--minimum-priority",
            str(minimum_priority), "--no-cache", "--no-fail-on-violation",
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout,
                stdin=subprocess.DEVNULL, errors="replace",
            )
        except subprocess.TimeoutExpired:
            return {
                "status": DETECTOR_ERROR,
                "detail": f"the detector did not finish within {timeout}s",
                "violations": [],
            }
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "status": DETECTOR_UNAVAILABLE,
                "detail": f"the detector could not be run: {exc}"[:300],
                "violations": [],
            }
        elapsed = round(time.monotonic() - started, 2)
        parsed = _read_report(report)
    record = {
        "seconds": elapsed,
        "rulesets": rulesets,
        "minimum_priority": minimum_priority,
        "exit_code": completed.returncode,
        "report_status": parsed["status"],
    }
    # --no-fail-on-violation makes a scan with violations exit 0, so any
    # other code is the detector reporting its own failure (PMD: 1
    # unexpected error, 2 usage error, 5 recoverable errors in analysis).
    if completed.returncode != 0:
        return {
            **record, "status": DETECTOR_ERROR, "violations": [],
            "detail": (
                f"the detector exited with code {completed.returncode}: "
                + (completed.stderr or "").strip()[-300:]
            ),
        }
    if parsed["status"] != "ok":
        return {
            **record, "status": INVALID_REPORT, "violations": [],
            "detail": f"the detector's report is {parsed['status']}",
        }
    unprocessed = [
        error for error in parsed["processing_errors"]
        if not error.get("filename")
        or _same_file(error.get("filename", ""), target)
    ]
    if unprocessed or parsed["config_errors"]:
        return {
            **record, "status": FILE_NOT_PROCESSED, "violations": [],
            "processing_errors": unprocessed,
            "config_errors": parsed["config_errors"],
            "detail": "the report records that this file was not fully "
                      "analysed, so an absent alert says nothing",
        }
    return {**record, "status": SCANNED, "violations": parsed["violations"]}


def declares_method(path: Path, method: str) -> bool:
    """Whether the Java or Kotlin source still declares a method of this name.

    In Java a declaration is the name, a parameter list and a body
    (optionally after a throws clause); a call is followed by `;` or an
    operator instead. In Kotlin it is `fun name(`. Returns True when the
    question cannot be answered -- no method name, a constructor or
    initializer, an unreadable file -- so that uncertainty never turns a
    repair into a removal.
    """
    method = str(method or "").strip()
    if not method or method.startswith("<") or not method.isidentifier():
        return True
    try:
        source = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    if Path(path).suffix == ".kt":
        return bool(re.search(
            rf"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?{re.escape(method)}\s*\(",
            source,
        ))
    declaration = re.compile(
        rf"\b{re.escape(method)}\s*\([^;{{}}]*\)\s*(?:throws\s[^{{;]*)?\{{"
    )
    return bool(declaration.search(source))


_JAVA_DECLARATION = re.compile(
    r"\b([A-Za-z_$][\w$]*)\s*\(((?:[^;{}()]|\([^()]*\))*)\)\s*(?:throws\s[^{;]*)?\{")
_KOTLIN_DECLARATION = re.compile(
    r"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)\s*\(((?:[^()]|\([^()]*\))*)\)")
_NOT_METHODS = frozenset({"if", "for", "while", "switch", "catch", "synchronized", "when"})


def _declarations(source: str, kotlin: bool) -> Dict[str, list]:
    """name -> [(parameter list without spaces, body)] for every function declared."""
    from cognalys.utils.identifier_words import blank_comments_and_literals

    masked = blank_comments_and_literals(source)
    found: Dict[str, list] = {}
    for match in (_KOTLIN_DECLARATION if kotlin else _JAVA_DECLARATION).finditer(masked):
        name = match.group(1)
        if name in _NOT_METHODS:
            continue
        rest = masked[match.end():]
        brace, equals = rest.find("{"), rest.find("=")
        if kotlin and equals != -1 and (brace == -1 or equals < brace) \
                and "\n" not in rest[:equals]:
            start = match.end() + equals
            end = masked.find("\n", start)
            body = source[start:end if end != -1 else len(source)]
        else:
            start = match.end() - 1 if not kotlin else match.end() + max(brace, 0)
            depth, end = 0, len(masked)
            for index in range(start, len(masked)):
                if masked[index] == "{":
                    depth += 1
                elif masked[index] == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            body = source[start:end]
        found.setdefault(name, []).append((re.sub(r"\s+", "", match.group(2)), body))
    return found


def renamed_declaration(baseline: Path, patched: Path, method: str,
                        confirmed_renames: Optional[Dict[str, str]] = None) -> str:
    """The new name of `method` when the patch renamed it rather than deleting it, or "".

    A rename the patch's own validation confirmed (old -> new, with the new
    declaration present) is taken as given. Otherwise it is re-resolved from
    the two files: the old name no longer occurs in the patched file at all,
    and exactly one function the baseline did not declare has the old
    declaration's parameter list and a body that is still mostly the same
    code. Anything less is a removal.
    """
    method = str(method or "").strip()
    try:
        before = Path(baseline).read_text(encoding="utf-8", errors="replace")
        after = Path(patched).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    kotlin = Path(patched).suffix == ".kt"
    declared_after = _declarations(after, kotlin)
    new = str((confirmed_renames or {}).get(method, "") or "")
    if new:
        return new if new in declared_after else ""
    if not method.isidentifier() or re.search(rf"(?<![\w$]){re.escape(method)}(?![\w$])", after):
        return ""
    old = _declarations(before, kotlin).get(method) or []
    if len(old) != 1:
        return ""
    parameters, body = old[0]
    declared_before = _declarations(before, kotlin)
    import difflib

    candidates = [
        name for name, entries in declared_after.items()
        if name not in declared_before and any(
            entry_parameters == parameters and difflib.SequenceMatcher(
                None, body.replace(method, name), entry_body).ratio() >= 0.6
            for entry_parameters, entry_body in entries)
    ]
    return candidates[0] if len(candidates) == 1 else ""


def reporter_accepted_value(message: str, baseline: Path, patched: Path) -> str:
    """The literal the patch wrote in place of the reported value, when the
    reporting detector's own message accepts it; "" otherwise."""
    from cognalys.utils.pr_text import replacement_literal, reported_allowlist

    allowed = {value.casefold() for value in reported_allowlist(message)}
    if not allowed:
        return ""
    try:
        edit = {"original_code": Path(baseline).read_text(encoding="utf-8", errors="replace"),
                "corrected_code": Path(patched).read_text(encoding="utf-8", errors="replace")}
    except OSError:
        return ""
    value = replacement_literal(message, [edit])
    return value if value.casefold() in allowed else ""


def matches(violation: Dict[str, str], rule: str, method: str,
            class_name: str = "") -> bool:
    """Whether a reported violation is the one the finding is about.

    Matched on the rule and the method, and on the class when the report
    names one. The message is deliberately not required: rule messages
    carry positions and identifiers that differ between versions, and
    demanding an exact match would report every rephrasing as a repair.
    """
    if str(violation.get("rule", "")).strip() != str(rule).strip():
        return False
    if str(violation.get("method", "")).strip() != str(method).strip():
        return False
    declared = str(violation.get("class", "")).strip()
    if declared and class_name:
        return declared == str(class_name).rsplit(".", 1)[-1] or \
            declared == str(class_name)
    return True


def replay(
    target: Path, rule: str, method: str, class_name: str = "",
    rulesets: str = DEFAULT_RULESETS, minimum_priority: int = 5,
    detector: str = "", timeout: int = 300,
) -> Dict:
    """Whether the reported alert fires on this file, as it stands."""
    scan = scan_file(target, rulesets, minimum_priority, detector, timeout)
    if scan["status"] != SCANNED:
        return {**scan, "rule": rule, "method": method}
    hits = [
        violation for violation in scan["violations"]
        if matches(violation, rule, method, class_name)
    ]
    return {
        **scan,
        "status": ALERT_PRESENT if hits else ALERT_ABSENT,
        "rule": rule,
        "method": method,
        "matching_violations": hits,
        "violations_in_file": len(scan["violations"]),
        "detail": (
            "the reported rule fires on this file" if hits else
            "the reported rule does not fire on this file; other rules may"
        ),
    }


def replay_pair(
    baseline: Path, patched: Path, rule: str, method: str,
    class_name: str = "", **options,
) -> Dict:
    """The transition between two revisions of one file.

    Outcomes:
      repaired          present in the baseline, absent after the patch
      unchanged         present in both
      introduced        absent in the baseline, present after the patch
      not_reproduced    absent in the baseline, so nothing can be credited
      not_exercised     the detector did not analyse one of the revisions
      detector_unavailable, detector_error, invalid_report,
      file_not_processed
                        the scan of one revision cannot be trusted
      method_removed    the alert is gone with the method it was matched on
      method_renamed    the method was renamed, so the alert's absence says
                        nothing (renamed_declaration)
      detector_mismatch the rule still fires on a value the reporting
                        detector's message (`reporter_message`) accepts
    """
    replay_fn = options.pop("replay_fn", None) or replay
    reporter_message = str(options.pop("reporter_message", "") or "")
    confirmed_renames = options.pop("confirmed_renames", None)
    before = replay_fn(baseline, rule, method, class_name, **options)
    after = replay_fn(patched, rule, method, class_name, **options)
    statuses = {before["status"], after["status"]}
    untrusted = [state for state in UNTRUSTED_STATES if state in statuses]
    extra: Dict = {}
    if untrusted:
        outcome = untrusted[0]
    elif (before["status"] == ALERT_PRESENT and after["status"] == ALERT_ABSENT
          and not declares_method(patched, method)):
        # The rule is matched on the method, so deleting the method silences
        # it exactly as a repair would. That is removing the functionality,
        # not fixing it, and is not credited. A rename is neither: the rule
        # simply looks for the old name.
        renamed_to = renamed_declaration(baseline, patched, method, confirmed_renames)
        outcome = METHOD_RENAMED if renamed_to else METHOD_REMOVED
        if renamed_to:
            extra["renamed_to"] = renamed_to
    elif before["status"] == ALERT_PRESENT and after["status"] == ALERT_ABSENT:
        outcome = "repaired"
    elif before["status"] == ALERT_PRESENT:
        outcome = "unchanged"
        accepted = reporter_accepted_value(reporter_message, baseline, patched)
        if accepted:
            outcome = DETECTOR_MISMATCH
            extra["reporter_accepted_value"] = accepted
    elif after["status"] == ALERT_PRESENT:
        outcome = "introduced"
    else:
        outcome = "not_reproduced"
    return {
        "outcome": outcome,
        "baseline": before,
        "patched": after,
        **extra,
        "detail": {
            "repaired": "the reported rule fired before the patch and does "
                        "not fire after it",
            "unchanged": "the reported rule still fires after the patch",
            "introduced": "the rule did not fire before the patch and does "
                          "after it",
            "not_reproduced": "the rule does not fire on the unpatched file "
                              "either, so the patch cannot be credited with "
                              "removing it; the corpus entry was produced "
                              "under other conditions",
            NOT_EXERCISED: "the detector did not analyse this file",
            CONSTRAINT_NOT_COVERED: "no rule of this specification checks the "
                                    "kind of constraint the finding reported, "
                                    "so the replay says nothing about it",
            DETECTOR_UNAVAILABLE: "the detector could not be run here",
            METHOD_REMOVED: "the rule no longer fires, but the patched file "
                            "no longer declares the method it fired in; "
                            "removing the code is not credited as a repair",
            METHOD_RENAMED: "the rule no longer fires because the method it "
                            "is matched on was renamed; the replay says "
                            "nothing about the renamed method",
            DETECTOR_MISMATCH: "the replayed rule still fires, but on a value "
                               "the reporting detector's own message accepts; "
                               "the two rules disagree about this value",
            DETECTOR_ERROR: "the detector failed on one of the revisions",
            INVALID_REPORT: "the detector left no readable report for one "
                            "of the revisions",
            FILE_NOT_PROCESSED: "the detector recorded that it could not "
                                "analyse one of the revisions",
        }[outcome],
    }


def file_at_revision(
    repository: Path, revision: str, relative: str, destination: Path,
) -> Optional[Path]:
    """Write a file as it was at `revision`, for the baseline half of a pair."""
    completed = subprocess.run(
        ["git", "-C", str(repository), "show", f"{revision}:{relative}"],
        capture_output=True, text=True, errors="replace",
        timeout=GIT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        return None
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(completed.stdout, encoding="utf-8")
    return destination


def reclassify(record: Dict) -> Dict:
    """A stored pair, re-read under the rules above.

    Pairs recorded before the detector's own failures had a state of their
    own could credit "repaired" to a scan that exited abnormally: the failed
    side read as "alert absent". Each side stored its exit code, so such a
    pair can be recognised afterwards and is returned as a detector error
    instead of being counted. Anything else is returned unchanged.
    """
    if not isinstance(record, dict):
        return record
    if record.get("outcome") not in {"repaired", "unchanged", "introduced",
                                     "not_reproduced"}:
        return record
    for side in ("baseline", "patched"):
        code = (record.get(side) or {}).get("exit_code")
        if code not in (None, 0):
            return {
                **record,
                "outcome": DETECTOR_ERROR,
                "recorded_outcome": record.get("outcome"),
                "detail": f"the detector exited with code {code} on the "
                          f"{side} revision, so the recorded outcome cannot "
                          "be trusted",
            }
    return record


# --- Detector adapters -------------------------------------------------------
#
# The replay above is PMD's. Findings come from other tools too (ape, semgrep,
# CrySL-based analyses), and for those the honest answer is that no detector
# is available to re-run -- which must not read as "the alert is gone". Each
# adapter declares which tool it re-runs, which file types it analyses and
# how it identifies itself, so a result names the detector, version and
# configuration it came from, and a tool with no adapter is reported as
# uncovered. Adding a detector means adding an adapter here, not another
# special case at the call site.

NO_DETECTOR_COVERAGE = "no_detector_coverage"


@dataclass(frozen=True)
class DetectorAdapter:
    tool: str
    suffixes: Tuple[str, ...]
    replay_pair: Callable[..., Dict]
    describe: Callable[[], Dict]
    # One revision: does the reported rule fire on this file as it stands?
    replay: Callable[..., Dict] = replay


@lru_cache(maxsize=4)
def _pmd_version(detector: str = "") -> str:
    executable = find_detector(detector)
    if not executable:
        return ""
    try:
        completed = subprocess.run(
            [executable, "--version"], capture_output=True, text=True,
            timeout=60, stdin=subprocess.DEVNULL, errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = (completed.stdout or completed.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def _describe_pmd() -> Dict:
    return {
        "tool": "pmd",
        "version": _pmd_version(),
        "rulesets": DEFAULT_RULESETS,
        "minimum_priority": 5,
    }


ADAPTERS: Dict[str, DetectorAdapter] = {
    "pmd": DetectorAdapter(
        tool="pmd", suffixes=(JAVA_SUFFIX,),
        replay_pair=replay_pair, describe=_describe_pmd,
    ),
}


def adapter_for(tool: str) -> Optional[DetectorAdapter]:
    return ADAPTERS.get(str(tool or "").strip().lower())


def _semgrep_module():
    from cognalys.validation import semgrep_rules

    return semgrep_rules


def adapter_for_finding(tool: str, specification: str) -> Optional[DetectorAdapter]:
    """The detector that can re-run this finding, if any.

    The reporting tool's own adapter first (PMD re-runs a PMD finding).
    Otherwise the specification's Semgrep rule, when one exists: a finding
    from a tool nothing can re-run -- `static:manual`, most of the recent
    corpus -- is then checked against the rule its specification encodes.
    """
    adapter = adapter_for(tool)
    if adapter is not None:
        return adapter
    semgrep = _semgrep_module()
    if semgrep.rule_file(specification) is None:
        return None
    return DetectorAdapter(
        tool="semgrep", suffixes=semgrep.SUFFIXES,
        replay_pair=lambda baseline, patched, rule, method, class_name="", **options:
            replay_pair(baseline, patched, rule, method, class_name,
                        replay_fn=semgrep.replay, **options),
        describe=lambda: semgrep.describe(specification),
        replay=semgrep.replay,
    )


def reproduce(tool: str, repo_root: Path, relative: str, rule: str,
              method: str, class_name: str = "") -> Dict:
    """Whether the reporting detector fires on the reported file now.

    Evidence for adjudication, before any patch exists: an alert the
    detector itself raises on this revision, in this method, establishes
    that its rule applies here -- the question an adjudicator otherwise
    records as "reported-rule applicability is unconfirmed". Only
    `alert_present` settles anything; every other status is a limit of what
    could be checked (no adapter, the detector failed, the file is not a
    type it analyses) and must not be read as the rule not applying.
    """
    adapter = adapter_for_finding(tool, rule)
    if adapter is None:
        return {**no_coverage(tool), "status": NO_DETECTOR_COVERAGE}
    if not relative:
        return {"status": NOT_EXERCISED, "detail": "no reported file"}
    scan = adapter.replay(Path(repo_root) / relative, rule, method, class_name)
    return {
        "status": scan.get("status", ""),
        "detector": adapter.describe(),
        "rule": rule,
        "method": method,
        "matching_lines": [
            violation.get("beginline", "")
            for violation in scan.get("matching_violations", []) or []
        ][:10],
        "detail": str(scan.get("detail", ""))[:300],
    }


def no_coverage(tool: str) -> Dict:
    """The record for a finding whose detector cannot be re-run here."""
    return {
        "outcome": NO_DETECTOR_COVERAGE,
        "detector": {"tool": str(tool or "")},
        "detail": (
            f"no detector adapter re-runs {tool or 'an unrecorded tool'} and "
            "its specification has no Semgrep rule; "
            "whether the alert still fires was not checked, and any "
            "correctness claim has to rest on other evidence"
        ),
    }
