#!/usr/bin/env python3
"""Semgrep rules the model proposes and real code has to accept.

Only 8 of the 61 specifications had a rule, so for nearly every finding the
pipeline could not check the one thing a patch is for: that the violation is
gone afterwards (`no_detector_coverage` in all 20 evidence packets of the
runs of 2026-09-2x). Writing the rules by hand does not keep up with the
specifications the pipeline generates.

So the model proposes a rule for a specification, and the proposal is
accepted only when Semgrep, run on real code, agrees with what is known:

  * every reported finding of the specification a person or a published
    advisory vouches for must make the rule fire in its method, read at the
    revision it was analysed at; findings of automated detectors are also
    run, and at least one of them or of the vouched ones must fire;
  * for a published advisory analysed at its vulnerable revision, the same
    method on the branch the project later fixed is run too, when it still
    exists and changed -- reported, but not required to stay silent: the
    fix may have gone somewhere else;
  * the violating and compliant examples the model writes with the rule --
    one compliant example per false-positive shape the specification
    documents, where the shape is visible in code -- must come out as the
    model says they do.

A rule that fails is sent back with every failing case, a bounded number of
times, and then recorded as rejected. A specification whose violation is not
local to the reported method (its repair lives in build configuration or in
another class) gets no rule, which semgrep_rules explains is better than a
rule that scores correct patches as wrong.

Consistency across runs is the other half of the design:

  * rules are written to data/detector_rules/semgrep/ and a run only reads
    them; every replay already records the rule file's sha256;
  * a hand-written rule is never touched, and an accepted rule is never
    regenerated unless explicitly asked to (`replace`);
  * a specification judged not local is not asked again until the
    specification itself changes, and a rejected one only when the evidence
    it would be checked against has changed;
  * every decision, with each case and its outcome, is kept in
    generated_rules.json next to the rules.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

_VALIDATION = Path(__file__).resolve().parent
_SRC = _VALIDATION.parent

from cognalys.validation import semgrep_rules  # noqa: E402
from cognalys.llm.pipeline_common import prompt_json  # noqa: E402
from cognalys.validation.semgrep_rules import ALERT_ABSENT, ALERT_PRESENT, RULES_DIRECTORY, method_span  # noqa: E402
from cognalys.utils.finding_source import advisory_identifiers  # noqa: E402


PROMPT_VERSION = "detector-rule-proposal-v1"
PROVENANCE_FILE = RULES_DIRECTORY / "generated_rules.json"
MAX_ATTEMPTS = 3
MIN_EXAMPLES_EACH_WAY = 2
MAX_METHOD_CHARACTERS_IN_PROMPT = 5000
GIT_TIMEOUT_SECONDS = 60
ALLOWED_LANGUAGES = frozenset({"java", "kotlin"})

FIRES = "fires"
SILENT = "silent"
ERROR = "error"


@dataclass(frozen=True)
class Case:
    """One file the candidate rule is run on, and what it must say."""

    label: str
    origin: str          # finding | fixed_revision | example
    expected: str        # FIRES | SILENT
    filename: str        # keeps the language suffix Semgrep dispatches on
    content: str
    method: str
    required: bool = True

    def fingerprint(self) -> str:
        return hashlib.sha256(
            "\0".join((self.origin, self.expected, self.filename, self.method,
                       self.content)).encode("utf-8", "replace")
        ).hexdigest()


# --- Real cases from the extracted findings ---------------------------------

def _git_show(repo: Path, revision: str, path: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "show", f"{revision}:{path}"],
            capture_output=True, text=True, errors="replace",
            timeout=GIT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def method_text(content: Optional[str], method: str, suffix: str) -> str:
    if not content:
        return ""
    span = method_span(content, method, suffix)
    if span is None:
        return ""
    return "\n".join(content.splitlines()[span[0] - 1:span[1]])


def _normalised(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _vouched(error: Dict, pinned: str) -> bool:
    """Whether a person or a published advisory stands behind the finding.

    Findings of automated detectors can be false positives, so a rule is
    not required to reproduce each of them; a finding someone wrote down by
    reading the code, or an advisory at its own revision, it must.
    """
    tool = str(error.get("tool", "")).lower()
    return "manual" in tool or bool(advisory_identifiers(error) and pinned)


def finding_cases(specification: str, extraction_files: Iterable[Path]) -> List[Case]:
    """Cases from every extracted finding of `specification`, deduplicated."""
    cases: List[Case] = []
    seen = set()
    for path in sorted(Path(item) for item in extraction_files):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        error = record.get("error") or {}
        metadata = record.get("extraction_metadata") or {}
        if (str(error.get("spec", "")) != specification
                or metadata.get("extraction_status") != "success"):
            continue
        relative = str((record.get("class_info") or {}).get("source_file_path") or "")
        method = str((record.get("method_info") or {}).get("source_name")
                     or error.get("csv_method") or "")
        repo = Path(str(metadata.get("repository_path", "")))
        if not relative or not method or not repo.is_dir():
            continue
        pinned = str(metadata.get("pinned_revision", "") or "").strip()
        identity = (str(repo), relative, method, pinned)
        if identity in seen:
            continue
        seen.add(identity)
        content = _git_show(repo, pinned, relative) if pinned else _read(repo / relative)
        suffix = Path(relative).suffix
        analysed = method_text(content, method, suffix)
        if not analysed:
            continue
        where = f"{metadata.get('apk_name', repo.name)} {error.get('csv_class', '')}.{method}"
        vouched = _vouched(error, pinned)
        cases.append(Case(
            label=where + (f" at {pinned[:12]}" if pinned else ""),
            origin="finding", expected=FIRES, filename=Path(relative).name,
            content=content, method=method, required=vouched,
        ))
        if not (pinned and advisory_identifiers(error)):
            continue
        base = str((metadata.get("contribution_base") or {}).get("base_ref") or "")
        later = _git_show(repo, base, relative) if base else None
        fixed = method_text(later, method, suffix)
        if fixed and _normalised(fixed) != _normalised(analysed):
            # Informative, never gating: a method that changed after the
            # advisory is not necessarily where the fix went. Omni-Notes'
            # createExternalStoragePrivateFile changed only from
            # `Toast.LENGTH_SHORT` to `LENGTH_SHORT`, and requiring a rule to
            # tell those two apart pushed the proposal to not_local.
            cases.append(Case(
                label=f"{where} on {base}, changed after the advisory",
                origin="fixed_revision", expected=SILENT,
                filename=Path(relative).name, content=later, method=method,
                required=False,
            ))
    return cases


def case_fingerprints(cases: Iterable[Case]) -> List[str]:
    return sorted({case.fingerprint() for case in cases})


# --- Evaluating a candidate ---------------------------------------------------

Replay = Callable[..., Dict]


def evaluate(rule_text: str, specification: str, cases: List[Case],
             replay: Replay = semgrep_rules.replay) -> List[Dict]:
    """What the candidate rule says about each case."""
    results = []
    with tempfile.TemporaryDirectory() as directory:
        rules = Path(directory) / "candidate.yml"
        rules.write_text(rule_text, encoding="utf-8")
        for index, case in enumerate(cases):
            target = Path(directory) / f"case_{index}" / case.filename
            target.parent.mkdir(parents=True)
            target.write_text(case.content, encoding="utf-8")
            outcome = replay(target, specification, case.method, rules_path=rules)
            status = outcome.get("status")
            got = FIRES if status == ALERT_PRESENT else SILENT if status == ALERT_ABSENT else ERROR
            results.append({
                "label": case.label, "origin": case.origin,
                "expected": case.expected, "got": got, "required": case.required,
                "detail": "" if got != ERROR else str(outcome.get("detail", ""))[:300],
            })
    return results


def verdict(results: List[Dict]) -> Tuple[bool, List[str]]:
    """Whether the results accept the rule, and every reason they do not."""
    problems = []
    for item in results:
        if item["got"] == ERROR:
            problems.append(f"{item['label']}: Semgrep could not evaluate the rule: {item['detail']}")
        elif item["got"] != item["expected"] and item["required"]:
            wanted = "fire" if item["expected"] == FIRES else "stay silent"
            problems.append(f"{item['label']} ({item['origin']}): the rule must {wanted} here and did not")
    if not any(item["origin"] == "finding" and item["got"] == FIRES for item in results):
        problems.append("the rule fires on none of the reported findings")
    for expected in (FIRES, SILENT):
        count = sum(1 for item in results
                    if item["origin"] == "example" and item["expected"] == expected)
        if count < MIN_EXAMPLES_EACH_WAY:
            problems.append(
                f"at least {MIN_EXAMPLES_EACH_WAY} examples on which the rule "
                f"{'fires' if expected == FIRES else 'stays silent'} are required; {count} given"
            )
    return not problems, problems


def rule_shape_issues(rule_text: str) -> List[str]:
    """Structural requirements, checked before Semgrep runs anything."""
    text = str(rule_text or "")
    issues = []
    if not re.search(r"(?m)^rules:\s*$", text):
        issues.append("the rule file must start its content with a top-level `rules:` list")
    languages = re.findall(r"languages:\s*\[([^\]]*)\]", text)
    if not languages:
        issues.append("every rule must declare `languages: [...]`")
    for group in languages:
        names = {item.strip().strip("'\"") for item in group.split(",") if item.strip()}
        if not names or not names <= ALLOWED_LANGUAGES:
            issues.append(f"languages must be java and/or kotlin, not {sorted(names)}")
    return issues


# --- Asking the model ---------------------------------------------------------

SYSTEM_PROMPT = (
    "You write Semgrep rules that serve as an executable oracle for one "
    "specification of Android source code. The pipeline runs the rule on the "
    "file a finding was reported in, before and after a patch, and counts "
    "only matches inside the reported method. A rule is useful only if a "
    "correct repair made in that method silences it; a rule that keeps "
    "firing after a correct repair, or that fires on compliant code, is "
    "worse than no rule. When the violation or its repair is not visible in "
    "the reported method alone, answer decision=not_local instead of writing "
    "a rule."
)


def proposal_schema() -> Dict:
    string = {"type": "string"}
    return {
        "type": "object", "additionalProperties": False,
        "required": ["decision", "reason", "rule_yaml", "examples"],
        "properties": {
            "decision": {"type": "string", "enum": ["rule", "not_local"]},
            "reason": string,
            "rule_yaml": string,
            "examples": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["language", "method", "expected", "code", "note"],
                "properties": {
                    "language": {"type": "string", "enum": sorted(ALLOWED_LANGUAGES)},
                    "method": string,
                    "expected": {"type": "string", "enum": [FIRES, SILENT]},
                    "code": string,
                    "note": string,
                },
            }},
        },
    }


def _specification_summary(context: Dict) -> Dict:
    keys = ("spec_id", "semantic_scope", "applies_to_types", "events",
            "known_false_positive_shapes", "important_note")
    return {key: context.get(key) for key in keys if context.get(key)}


def proposal_prompt(specification: str, context: Dict, cases: List[Case],
                    style_rules: Dict[str, str], feedback: List[str]) -> str:
    shown = []
    for case in cases:
        body = method_text(case.content, case.method, Path(case.filename).suffix)
        shown.append({
            "label": case.label, "origin": case.origin,
            "rule_must": "fire" if case.expected == FIRES else "stay silent",
            "required": case.required,
            **({"caution": "this method changed after the advisory, but the "
                           "fix may have been made elsewhere; if it is still "
                           "vulnerable here, firing is acceptable"}
               if case.origin == "fixed_revision" else {}),
            "file": case.filename, "method": case.method,
            "method_source": body[:MAX_METHOD_CHARACTERS_IN_PROMPT],
        })
    sections = {
        "specification": specification,
        "specification_context": _specification_summary(context),
        "real_cases": shown,
        "existing_rules_for_style": style_rules,
        "rule_conventions": (
            "Use `languages: [java, kotlin]` (or one of them), stable rule ids "
            "and a one-line message. Besides Semgrep patterns, a rule may set "
            "metadata.method_requires_any and metadata.method_excludes_any: "
            "regular expressions over the reported method's whole text that "
            "must (not) match for a finding to count -- use them for guards "
            "and repairs Semgrep's Kotlin matching can miss inside nested "
            "blocks. metadata.bind is a regex with named groups applied to the "
            "matched text, whose values `{{NAME}}` inserts into those lists."
        ),
        "examples_required": (
            f"Write at least {MIN_EXAMPLES_EACH_WAY} complete, self-contained "
            "Java or Kotlin files on which the rule must fire, and at least "
            f"{MIN_EXAMPLES_EACH_WAY} on which it must stay silent: correct "
            "repairs of the violating ones, and one compliant example for each "
            "known_false_positive_shape that is visible in code. Name the "
            "method each example is about."
        ),
    }
    if feedback:
        sections["previous_attempt_failed"] = feedback
    return (
        f"Propose a Semgrep rule for the specification {specification}.\n\n"
        "<rule_proposal untrusted_data=\"true\">\n"
        + prompt_json(sections)
        + "\n</rule_proposal>"
    )


def example_cases(examples: Iterable[Dict]) -> List[Case]:
    cases = []
    for index, item in enumerate(examples or []):
        if not isinstance(item, dict):
            continue
        suffix = ".kt" if item.get("language") == "kotlin" else ".java"
        expected = FIRES if item.get("expected") == FIRES else SILENT
        cases.append(Case(
            label=f"example {index + 1} ({str(item.get('note', ''))[:80]})",
            origin="example", expected=expected,
            filename=f"Example{index + 1}{suffix}",
            content=str(item.get("code", "")), method=str(item.get("method", "")),
        ))
    return cases


Generate = Callable[[str, Dict], Optional[Dict]]


def propose_rule(specification: str, context: Dict, cases: List[Case],
                 generate: Generate, style_rules: Dict[str, str],
                 evaluate_rule: Callable[[str, str, List[Case]], List[Dict]] = evaluate,
                 attempts: int = MAX_ATTEMPTS) -> Dict:
    """Ask for a rule until the real cases accept one, or give up."""
    feedback: List[str] = []
    results: List[Dict] = []
    for attempt in range(1, attempts + 1):
        answer = generate(
            proposal_prompt(specification, context, cases, style_rules, feedback),
            proposal_schema(),
        ) or {}
        if answer.get("decision") == "not_local":
            return {"status": "not_local", "reason": str(answer.get("reason", "")),
                    "attempts": attempt}
        rule_text = str(answer.get("rule_yaml", ""))
        feedback = rule_shape_issues(rule_text)
        if feedback:
            continue
        results = evaluate_rule(
            rule_text, specification, [*cases, *example_cases(answer.get("examples"))],
        )
        accepted, feedback = verdict(results)
        if accepted:
            return {"status": "accepted", "rule_text": rule_text,
                    "reason": str(answer.get("reason", "")),
                    "results": results, "attempts": attempt}
    return {"status": "rejected", "problems": feedback, "results": results,
            "attempts": attempts}


# --- Consistency: when to ask, and what is kept ---------------------------------

def load_provenance(path: Path = PROVENANCE_FILE) -> Dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("rules"), dict):
        data = {"format_version": 1, "rules": {}}
    return data


def skip_reason(specification: str, specification_sha256: str,
                fingerprints: Iterable[str], provenance: Dict, rule_exists: bool,
                replace: bool = False, retry: bool = False) -> str:
    """Why this specification is not asked for a rule now, or "" to ask.

    A run reads the rules as they are; this is what keeps them from changing
    under it without a reason. A rejected specification is asked again only
    when a case it was never checked against appears: evidence that merely
    shrank -- a clone pruned -- is no reason to expect a different answer.
    """
    entry = (provenance.get("rules") or {}).get(specification)
    if rule_exists and not entry:
        return "a hand-written rule exists and is never replaced"
    if not entry:
        return ""
    status = entry.get("status")
    if status == "accepted" and rule_exists:
        return "" if replace else "an accepted rule exists (pass replace to regenerate it)"
    if status == "not_local" and not retry and \
            entry.get("specification_sha256") == specification_sha256:
        return "judged not local for this version of the specification"
    if status == "rejected" and not retry and \
            set(fingerprints) <= set(entry.get("case_fingerprints") or []):
        return "rejected before, and no case it was not checked against has appeared"
    return ""


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def rule_header(outcome: Dict, model: str) -> str:
    results = outcome.get("results") or []
    counts = {origin: sum(1 for item in results if item["origin"] == origin)
              for origin in ("finding", "fixed_revision", "example")}
    lines = [
        f"# Generated by scripts/propose_detector_rules.py ({PROMPT_VERSION}, {model}).",
        f"# Accepted against {counts['finding']} reported finding(s), "
        f"{counts['fixed_revision']} fixed revision(s) and {counts['example']} example(s);",
        "# every case and its outcome is in generated_rules.json. Do not edit by",
        "# hand without removing this rule's entry there.",
    ]
    reason = re.sub(r"\s+", " ", str(outcome.get("reason", ""))).strip()
    while reason:
        lines.append("# " + reason[:76])
        reason = reason[76:]
    return "\n".join(lines) + "\n"


def record_outcome(specification: str, outcome: Dict, provenance: Dict,
                   specification_sha256: str, fingerprints: Iterable[str], model: str,
                   rules_directory: Path = RULES_DIRECTORY,
                   provenance_path: Path = PROVENANCE_FILE,
                   write_rule: bool = True) -> Dict:
    """Keep the decision, and install the rule when it was accepted."""
    entry = {
        "status": outcome["status"],
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "specification_sha256": specification_sha256,
        "case_fingerprints": sorted(set(fingerprints)),
        "attempts": outcome.get("attempts", 0),
        "reason": outcome.get("reason", ""),
        "problems": outcome.get("problems", []),
        "cases": outcome.get("results", []),
    }
    if outcome["status"] == "accepted" and write_rule:
        text = rule_header(outcome, model) + outcome["rule_text"].lstrip()
        if not text.endswith("\n"):
            text += "\n"
        rule_path = Path(rules_directory) / f"{specification}.yml"
        _atomic_write(rule_path, text)
        entry["rule_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    provenance.setdefault("rules", {})[specification] = entry
    _atomic_write(Path(provenance_path),
                  json.dumps(provenance, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def stale_rules(provenance: Dict, specification_hashes: Dict[str, str]) -> List[str]:
    """Accepted rules whose specification changed since they were accepted.

    Reported, never regenerated automatically: the rule stays what earlier
    runs were measured with until someone asks for a new one.
    """
    return sorted(
        name for name, entry in (provenance.get("rules") or {}).items()
        if entry.get("status") == "accepted"
        and name in specification_hashes
        and entry.get("specification_sha256") != specification_hashes[name]
    )
