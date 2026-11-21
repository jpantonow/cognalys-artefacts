#!/usr/bin/env python3
"""How faithful each specification is to the CrySL rule it cites.

Specifications are written by a model (`--spec-model`) from the rule's text
and web research. The audit (specifications.audit_specifications) checks
that the cited rule exists and names the governed type; nothing checked what
the specification says against what the rule says. A specification is the
contract every later stage reasons with -- the adjudicator quotes its events,
protocol and constraints -- so an event it drops or a value it adds changes
what the pipeline concludes about a finding.

This compares the two, deterministically and section by section:

  methods       the API methods the rule's EVENTS call, against the methods
                the specification's events match (api_patterns)
  events        the rule's event names (aggregates and stand-alone labels)
                against the specification's event names
  order         the rule's ORDER against the specification's protocol, as
                token sequences after both are normalised
  constraint values      the literals the rule's CONSTRAINTS admit
  constraint predicates  neverTypeOf, notHardCoded, instanceOf, ...
  forbidden / requires / ensures  method and predicate names

Each dimension reports recall (what the rule states that the specification
carries) and, for methods and events, precision (what the specification
states that the rule does not). A specification that cites no CrySL rule has
no reference here and is listed as such, never scored.

It reads and reports only; it never changes a specification.

Specification versus detector (--findings, --runs): the expected value an
analyzer recorded on a finding (`exp=` in RVSec's message; the
detector_expected field of an imported row, error.detector_context.expected
of an analysed record) against what the specification admits, as the brace
sets, `a[..] || b[..]` alternatives and quoted lists of the fields that state
the rule (RULE_FIELDS; never a caveat or a quoted analyzer message).
For each (specification, code) it lists what the specification admits that
the analyzer does not accept and what the analyzer accepts that the
specification does not list; an expected value it cannot compare (a
description the specification's sets do not name) is counted as not
compared, never as agreeing. This part is report only as well: it edits no
specification, feeds no prompt, gate or decision, and changes no label.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from cognalys.workspace import library_data

SECTIONS = ("SPEC", "OBJECTS", "EVENTS", "ORDER", "CONSTRAINTS", "REQUIRES",
            "ENSURES", "NEGATES", "FORBIDDEN")
_SECTION = re.compile(r"^\s*(" + "|".join(SECTIONS) + r")\b(.*)$")
_LABELLED_EVENT = re.compile(r"^\s*(\w+)\s*:\s*(?:[\w\[\]]+\s*=\s*)?([\w$.<>]+)\s*\(")
_AGGREGATE = re.compile(r"^\s*(\w+)\s*:=\s*([^;]+);")
_STRING = re.compile(r'"([^"\\]*)"')
_PREDICATE = re.compile(r"\b([a-z]\w*)\s*\[")
_CALL = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
_ORDER_TOKEN = re.compile(r"\w+|[()|,*+?]")


def parse_crysl(text: str) -> Dict:
    """The parts of a CrySL rule this comparison needs."""
    sections: Dict[str, List[str]] = {name: [] for name in SECTIONS}
    current = ""
    for raw in str(text or "").splitlines():
        line = raw.split("//", 1)[0].rstrip()
        match = _SECTION.match(line)
        if match:
            current = match.group(1)
            rest = match.group(2).strip()
            if rest:
                sections[current].append(rest)
            continue
        if current and line.strip():
            sections[current].append(line.strip())
    labels: Dict[str, str] = {}
    aggregates: Dict[str, List[str]] = {}
    for line in sections["EVENTS"]:
        aggregate = _AGGREGATE.match(line)
        if aggregate:
            aggregates[aggregate.group(1)] = [
                part.strip() for part in aggregate.group(2).split("|") if part.strip()]
            continue
        event = _LABELLED_EVENT.match(line)
        if event:
            labels[event.group(1)] = event.group(2).rsplit(".", 1)[-1]
    in_aggregate = {label for members in aggregates.values() for label in members}
    constraints = " ".join(sections["CONSTRAINTS"])
    # What each name in ORDER stands for, as the methods it calls: a label
    # is free text (c1, g2), so ORDER is compared through what it means.
    meaning = {label: (method,) for label, method in labels.items()}
    for name, members in aggregates.items():
        meaning[name] = tuple(sorted({labels.get(member, member) for member in members}))
    return {
        "type": " ".join(sections["SPEC"]).strip(),
        "methods": sorted(set(labels.values())),
        "meaning": meaning,
        # Only aggregates carry chosen names; a rule without them has none
        # to compare against.
        "events": sorted(aggregates),
        "order": " ".join(sections["ORDER"]).strip(),
        "constraint_values": sorted(set(_STRING.findall(constraints))),
        "constraint_predicates": sorted(set(_PREDICATE.findall(constraints))),
        "forbidden": sorted({match.group(1) for line in sections["FORBIDDEN"]
                             for match in [_CALL.search(line)] if match}),
        "requires": sorted(set(_PREDICATE.findall(" ".join(sections["REQUIRES"])))),
        "ensures": sorted(set(_PREDICATE.findall(" ".join(sections["ENSURES"])))),
    }


def _norm(name: str) -> str:
    """Event names compare without case or a plural `s` (CrySL's Gets ~ Get)."""
    name = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    return name[:-1] if len(name) > 3 and name.endswith("s") else name


def specification_profile(specification: Dict) -> Dict:
    events = [event for event in specification.get("events", []) or [] if isinstance(event, dict)]
    methods = set()
    meaning = {}
    for event in events:
        called = set()
        for pattern in event.get("api_patterns", []) or []:
            calls = _CALL.findall(str(pattern))
            if calls:
                called.add(calls[-1].rsplit(".", 1)[-1])
        methods |= called
        if event.get("name"):
            meaning[str(event["name"])] = tuple(sorted(called))
    return {
        "events": sorted({str(event.get("name", "")) for event in events if event.get("name")}),
        "methods": sorted(methods),
        "meaning": meaning,
        "protocol": str(specification.get("protocol", "") or ""),
        "text": json.dumps(specification, ensure_ascii=False).lower(),
    }


def _recall_precision(reference: Iterable[str], candidate: Iterable[str],
                      key: Callable[[str], str] = str) -> Dict:
    reference_keys = {key(item): item for item in reference if item}
    candidate_keys = {key(item): item for item in candidate if item}
    hit = set(reference_keys) & set(candidate_keys)
    return {
        "recall": round(len(hit) / len(reference_keys), 3) if reference_keys else None,
        "precision": round(len(hit) / len(candidate_keys), 3) if candidate_keys else None,
        "missing": sorted(reference_keys[k] for k in set(reference_keys) - hit),
        "extra": sorted(candidate_keys[k] for k in set(candidate_keys) - hit),
    }


def _mentioned(reference: Iterable[str], text: str) -> Dict:
    items = [item for item in reference if item]
    found = [item for item in items if item.lower() in text]
    return {
        "recall": round(len(found) / len(items), 3) if items else None,
        "missing": sorted(set(items) - set(found)),
    }


def _order_tokens(expression: str, meaning: Dict[str, tuple]) -> List[str]:
    """ORDER as tokens, each name replaced by the methods it stands for."""
    tokens = []
    for token in _ORDER_TOKEN.findall(str(expression or "")):
        if not re.match(r"\w", token):
            tokens.append(token)
        elif meaning.get(token):
            tokens.append("|".join(method.lower() for method in meaning[token]))
        else:
            tokens.append(_norm(token))
    return tokens


def compare(specification: Dict, rule: Dict) -> Dict:
    """Every dimension for one specification against its parsed rule."""
    profile = specification_profile(specification)
    reference_order = _order_tokens(rule["order"], rule["meaning"])
    candidate_order = _order_tokens(profile["protocol"], profile["meaning"])
    union = set(reference_order) | set(candidate_order)
    # Events compare by what they group (the methods), not by their names:
    # CrySL's `Con` and a specification's `Create` are the same event.
    def grouped(meaning: Dict[str, tuple], names: Iterable[str]) -> List[str]:
        return ["|".join(m.lower() for m in meaning.get(name, ())) or _norm(name)
                for name in names]
    dimensions = {
        "methods": _recall_precision(rule["methods"], profile["methods"], str.lower),
        "events": _recall_precision(grouped(rule["meaning"], rule["events"]),
                                    grouped(profile["meaning"], profile["events"])),
        "order": {
            # Identical as written (CipherSpec copies the rule's ORDER) or
            # identical in what its names stand for.
            "exact": bool(reference_order) and (
                reference_order == candidate_order
                or re.sub(r"\s+", "", rule["order"]) == re.sub(r"\s+", "", profile["protocol"])),
            "token_jaccard": (round(len(set(reference_order) & set(candidate_order)) / len(union), 3)
                              if union else None),
            "rule": rule["order"], "specification": profile["protocol"],
        },
        "constraint_values": _mentioned(rule["constraint_values"], profile["text"]),
        "constraint_predicates": _mentioned(rule["constraint_predicates"], profile["text"]),
        "forbidden": _mentioned(rule["forbidden"], profile["text"]),
        "requires": _mentioned(rule["requires"], profile["text"]),
        "ensures": _mentioned(rule["ensures"], profile["text"]),
    }
    scores = [value["recall"] for value in dimensions.values()
              if isinstance(value, dict) and value.get("recall") is not None]
    if dimensions["order"]["exact"]:
        scores.append(1.0)
    elif dimensions["order"]["token_jaccard"] is not None:
        scores.append(dimensions["order"]["token_jaccard"])
    return {"dimensions": dimensions,
            "score": round(sum(scores) / len(scores), 3) if scores else None}


def evaluate(directory: Path, fetch: Optional[Callable[[str], Tuple[str, str]]] = None) -> List[Dict]:
    """One record per specification: its score, or why it has none."""
    if fetch is None:
        from cognalys.specifications.audit_specifications import fetch as fetch_url
        fetch = fetch_url
    records = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            specification = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(specification, dict):
            continue
        name = str(specification.get("name") or path.stem)
        url = str(specification.get("source_url", "") or "").strip()
        if not url.lower().endswith(".crysl"):
            records.append({"specification": name, "status": "no_reference",
                            "detail": "cites no CrySL rule"})
            continue
        text, problem = fetch(url)
        if not text:
            records.append({"specification": name, "status": "reference_unavailable",
                            "detail": problem, "source_url": url})
            continue
        rule = parse_crysl(text)
        if not rule["methods"]:
            records.append({"specification": name, "status": "reference_unparsed",
                            "source_url": url})
            continue
        records.append({"specification": name, "status": "compared", "source_url": url,
                        "rule_type": rule["type"], **compare(specification, rule)})
    return records


def summary(records: List[Dict]) -> Dict:
    compared = [record for record in records if record["status"] == "compared"]
    def mean(values):
        values = [value for value in values if value is not None]
        return round(sum(values) / len(values), 3) if values else None
    keys = ("methods", "events", "constraint_values", "constraint_predicates",
            "forbidden", "requires", "ensures")
    return {
        "specifications": len(records),
        "compared": len(compared),
        "by_status": {status: sum(1 for r in records if r["status"] == status)
                      for status in sorted({r["status"] for r in records})},
        "mean_score": mean(record["score"] for record in compared),
        "mean_recall": {key: mean(r["dimensions"][key]["recall"] for r in compared) for key in keys},
        "mean_precision": {key: mean(r["dimensions"][key].get("precision") for r in compared)
                           for key in ("methods", "events")},
        "order_exact": sum(1 for r in compared if r["dimensions"]["order"]["exact"]),
    }


def markdown(records: List[Dict], totals: Dict) -> str:
    lines = [
        "# Specification fidelity to the cited CrySL rule", "",
        f"{totals['compared']} of {totals['specifications']} specifications compared; "
        f"mean score {totals['mean_score']}; ORDER identical in {totals['order_exact']}.", "",
        "| Specification | Score | Methods R/P | Events R/P | ORDER | Values | Predicates | Ensures |",
        "|---|---:|---|---|---|---:|---:|---:|",
    ]
    for record in records:
        if record["status"] != "compared":
            continue
        d = record["dimensions"]
        lines.append(
            f"| {record['specification']} | {record['score']} | "
            f"{d['methods']['recall']}/{d['methods']['precision']} | "
            f"{d['events']['recall']}/{d['events']['precision']} | "
            f"{'=' if d['order']['exact'] else d['order']['token_jaccard']} | "
            f"{d['constraint_values']['recall']} | {d['constraint_predicates']['recall']} | "
            f"{d['ensures']['recall']} |")
    others = [r for r in records if r["status"] != "compared"]
    if others:
        lines += ["", "Not compared: " + ", ".join(
            f"{r['specification']} ({r['status']})" for r in others)]
    return "\n".join(lines) + "\n"


# ------------------------------------------- specification versus detector

DETECTOR_NOTE = (
    "Report only: what each specification admits against the expected values the "
    "analyzer recorded on findings. It edits no specification and feeds no prompt, "
    "gate or decision; correcting a specification is a reviewed decision with a "
    "rerun and a before/after count of dismissals.")
MULTIPLE_EXPECTED_NOTE = (
    "recorded with more than one expected value; findings record no analyzer "
    "version, so a different analyzer build cannot be told from an expectation "
    "that depends on the observed value")
# The fields of a specification record that state the rule itself, the
# only text whose sets are read as what the specification admits (field ->
# the keys read inside it when it is an object; None reads all of it).
# In: protocol and events (the governed calls and the condition each must
# meet), semantic_scope (what the rule observes in every finding),
# detector_semantics claim and predicates, security_semantics.detector_claim
# (what a conforming trace satisfies).
# Out, because they name values the rule does NOT list or are not the rule:
# caveats (important_note, forbidden_inferences_without_evidence,
# known_false_positive_shapes, non_security_violation_shapes, unknowns,
# detector_semantics.limitations); the API's documented behaviour and the
# runtime claim (api_semantics, security_semantics.runtime_claim: what the
# platform accepts, not what the rule admits); evidence_requirements and
# remediation_policies (what to show or change); and every field that quotes
# the analyzer back (generation_evidence, false_positive_triggers' detector
# messages). A field not named here is out, so a field added to the schema
# is read only once it is placed here.
RULE_FIELDS: Dict[str, Optional[Tuple[str, ...]]] = {
    "protocol": None,
    "events": None,
    "semantic_scope": None,
    "detector_semantics": ("claim", "predicates"),
    "security_semantics": ("detector_claim",),
}
_VALUE_SET = re.compile(r"\{([^{}]*)\}")
_SET_MEMBER = re.compile(r"[\w.\-/ ]+")
_ALTERNATIVES = re.compile(r"((?:\w+\s*\[[^\]]*\]\s*\|\|\s*)+\w+\s*\[[^\]]*\])")
_ALTERNATIVE_NAME = re.compile(r"(\w+)\s*\[")
_QUOTED_MEMBER = re.compile(r'"([\w.\-/ ]{1,60})"')
_QUOTED_LIST = re.compile(r'"[\w.\-/ ]{1,60}"(?:\s*,\s*(?:or\s+)?"[\w.\-/ ]{1,60}"){1,}')
_CALL_ITEM = re.compile(r"^[\w.$]*?(\w+)\s*\(.*\)$")
_CAMEL_WORD = re.compile(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])")
# A description names a member only through a word run this long: a short
# member (AES, EC) would be found in any prose.
_MIN_DESCRIBED_MEMBER = 6
# Statuses of one comparison, and which of them compared anything.
COMPARED_STATUSES = ("compared", "call_compared", "values_absent_from_specification")
NOT_COMPARED_STATUSES = ("no_specification", "description_not_named", "values_without_set")
# Directories that never hold findings files or runs, skipped when a
# directory is searched.
_NOT_SEARCHED = {".git", "clones", "worktrees", "finding_worktrees", "build", ".gradle",
                 "node_modules", "prompt_logs", "llm_cache", "v2_artifacts", "error_methods",
                 "error_solutions", "pull_requests", "external_research_sources",
                 "__pycache__"}
_FINDINGS_SUFFIXES = (".csv", ".jsonl")


def _value_key(value: object) -> str:
    """A value as compared: lower case, letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def expected_items(expected: str) -> List[str]:
    """The expected value split on commas outside parentheses, so a call
    signature `f(a, b)` stays one item."""
    items, depth, current = [], 0, ""
    for character in str(expected or ""):
        if character == "(":
            depth += 1
        elif character == ")":
            depth = max(0, depth - 1)
        if character == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += character
    items.append(current.strip())
    return [item for item in items if item]


def expected_kind(items: Sequence[str]) -> str:
    """`call` (every item a call), `values` (every item a word or two) or
    `description` (prose about the value)."""
    if items and all(_CALL_ITEM.match(item) for item in items):
        return "call"
    if items and all(len(item.split()) <= 2 for item in items):
        return "values"
    return "description"


def _strings(value) -> Iterable[str]:
    if isinstance(value, dict):
        for inner in value.values():
            yield from _strings(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _strings(inner)
    elif isinstance(value, str):
        yield value


def _rule_statement(specification: Dict) -> Dict:
    """The parts of the record that state the rule (RULE_FIELDS)."""
    body = {}
    for field, keys in RULE_FIELDS.items():
        value = specification.get(field)
        if value is None:
            continue
        if keys is not None and isinstance(value, dict):
            value = {key: value[key] for key in keys if key in value}
        body[field] = value
    return body


def specification_groups(specification: Dict) -> List[Tuple[str, ...]]:
    """The alternatives the rule-stating fields spell out (RULE_FIELDS):
    brace sets, `a[..] || b[..]` chains and quoted lists of two or more
    values."""
    groups = set()
    for text in _strings(_rule_statement(specification)):
        for match in _VALUE_SET.finditer(text):
            members = [item.strip().strip("\"'` ") for item in match.group(1).split(",")]
            members = [item for item in members if item]
            if members and all(_SET_MEMBER.fullmatch(item) for item in members):
                groups.add(tuple(members))
        for match in _ALTERNATIVES.finditer(text):
            groups.add(tuple(_ALTERNATIVE_NAME.findall(match.group(1))))
        for match in _QUOTED_LIST.finditer(text):
            groups.add(tuple(_QUOTED_MEMBER.findall(match.group(0))))
    return sorted(groups)


def _described_members(items: Sequence[str], groups: Iterable[Tuple[str, ...]]) -> set:
    """The group members a description names: a member's camelCase words in
    order ("prepared key material" names preparedKeyMaterial)."""
    words = re.findall(r"[a-z0-9]+", " ".join(items).lower())
    named = set()
    for group in groups:
        for member in group:
            member_words = [word.lower() for word in _CAMEL_WORD.findall(member)]
            if (len(_value_key(member)) >= _MIN_DESCRIBED_MEMBER and member_words
                    and any(words[start:start + len(member_words)] == member_words
                            for start in range(len(words)))):
                named.add(_value_key(member))
    return named


def compare_expected(specification: Optional[Dict], expected: str) -> Dict:
    """One recorded expected value against one specification.

    spec_only: what a matching alternative group admits that the analyzer
    does not accept; detector_only: what the analyzer accepts that the
    specification does not list. Only a value list fills detector_only (a
    description enumerates nothing); a description is compared only through
    the members it names.
    """
    items = expected_items(expected)
    kind = expected_kind(items)
    result = {"kind": kind, "status": "", "spec_only": [], "detector_only": [], "groups": []}
    if specification is None:
        result["status"] = "no_specification"
        return result
    # A value counts as listed only where the rule is stated: a caveat that
    # names it as absent from the rule does not list it.
    text = _value_key(json.dumps(_rule_statement(specification), ensure_ascii=False))
    if kind == "call":
        # The call the analyzer expects instead (a forbidden method's
        # replacement), compared by method name with the specification's text.
        result["status"] = "call_compared"
        result["detector_only"] = sorted(
            item for item in items if _value_key(_CALL_ITEM.match(item).group(1)) not in text)
        return result
    accepted = {_value_key(item) for item in items}
    groups = specification_groups(specification)
    if kind == "description":
        accepted |= _described_members(items, groups)
    matched, members, spec_only = [], set(), set()
    for group in groups:
        keys = {_value_key(member) for member in group}
        if keys & accepted:
            matched.append(list(group))
            members |= keys
            spec_only |= {member for member in group if _value_key(member) not in accepted}
    if matched:
        result.update(status="compared", spec_only=sorted(spec_only), groups=matched,
                      detector_only=sorted({item for item in items
                                            if _value_key(item) not in members})
                      if kind == "values" else [])
    elif kind == "values":
        absent = sorted({item for item in items if _value_key(item) not in text})
        # No set to tell what else the specification admits: only what the
        # analyzer accepts and the specification never mentions is reported.
        result.update(status="values_absent_from_specification" if absent
                      else "values_without_set", detector_only=absent)
    else:
        result["status"] = "description_not_named"
    return result


def _searched_files(path: Path, suffixes: Sequence[str]) -> List[Path]:
    if path.is_file():
        return [path]
    found = []
    for directory, subdirectories, names in os.walk(path):
        subdirectories[:] = sorted(name for name in subdirectories if name not in _NOT_SEARCHED)
        found += [Path(directory, name) for name in sorted(names)
                  if name.lower().endswith(tuple(suffixes))]
    return found


def _run_directories(path: Path) -> List[Path]:
    """`path` when it is a run (it has error_methods/), else the runs below it."""
    if (path / "error_methods").is_dir():
        return [path]
    runs = []
    for directory, subdirectories, _names in os.walk(path):
        if "error_methods" in subdirectories:
            runs.append(Path(directory))
            subdirectories[:] = []
            continue
        subdirectories[:] = sorted(name for name in subdirectories if name not in _NOT_SEARCHED)
    return runs


def detector_rows_from_findings(paths: Iterable[Path]) -> Tuple[List[Dict], int, int]:
    """Imported rows that carry the analyzer's expectation field, how many
    files held such rows, and how many files could not be read (skipped). A
    directory is searched for .csv/.jsonl files; a file named directly is
    read whatever its findings format."""
    from cognalys.sources import read_rows

    rows, files, unreadable = [], 0, 0
    for path in paths:
        for file in _searched_files(Path(path), _FINDINGS_SUFFIXES):
            try:
                read = read_rows(file)
            except (OSError, ValueError, UnicodeDecodeError, csv.Error):
                # csv.Error: a field over the csv module's size limit, or a
                # malformed quote, in a file that need not be a findings file.
                unreadable += 1
                continue
            held = [row for row in read if isinstance(row, dict)]
            # A file holds analyzer expectations when any row has the field;
            # its rows without one are counted as rows without a value.
            if not any("detector_expected" in row for row in held):
                continue
            files += 1
            for row in held:
                rows.append({
                    "source": "imported", "file": str(file),
                    "apk": row.get("apk", "") or "", "class": row.get("class", "") or "",
                    "method": row.get("method", "") or "", "spec": row.get("spec", "") or "",
                    "code": row.get("detector_code", "") or row.get("code", "") or "",
                    "expected": str(row.get("detector_expected", "") or "").strip(),
                    "origin": row.get("origin", "") or "",
                })
    return rows, files, unreadable


def _final_outcomes(run: Path) -> Dict[Tuple[str, str, str, str], str]:
    from cognalys.reporting import finding_statistics

    try:
        records = finding_statistics.read_run(run)
    except Exception as exc:  # a report: say the run could not be read
        return {"__unreadable__": f"(run unreadable: {type(exc).__name__})"}
    outcomes = {}
    for key, record in records.items():
        try:
            outcomes[key] = finding_statistics.final_outcome(record)[0]
        except Exception as exc:
            outcomes[key] = f"(outcome unreadable: {type(exc).__name__})"
    return outcomes


def detector_rows_from_runs(paths: Iterable[Path]) -> Tuple[List[Dict], int]:
    """Analysed records that carry the analyzer's context, each with the
    finding's final outcome in its run (finding_statistics.final_outcome)."""
    rows, runs = [], 0
    for path in paths:
        for run in _run_directories(Path(path)):
            runs += 1
            found = []
            for file in sorted(run.glob("error_methods/*/*.json")):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict) or not isinstance(data.get("error"), dict):
                    continue
                error = data["error"]
                context = error.get("detector_context")
                if not isinstance(context, dict) or not ("expected" in context or "code" in context):
                    continue
                meta = data.get("extraction_metadata") or {}
                found.append({
                    "source": "analysed", "run": str(run), "file": str(file),
                    "apk": str(meta.get("apk_name", "") or ""),
                    "class": str(error.get("csv_class", "") or ""),
                    "method": str(error.get("csv_method", "") or ""),
                    "spec": str(error.get("spec", "") or ""),
                    "code": str(context.get("code", "") or ""),
                    "expected": str(context.get("expected", "") or "").strip(),
                })
            if any(row["expected"] for row in found):
                outcomes = _final_outcomes(run)
                for row in found:
                    key = (row["apk"], row["class"], row["method"], row["spec"])
                    row["outcome"] = outcomes.get(key) or outcomes.get(
                        "__unreadable__", "(no record)")
            rows += found
    return rows, runs


def load_specifications(directory: Path) -> Dict[str, Dict]:
    specifications = {}
    for path in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            specifications[str(data.get("name") or path.stem)] = data
    return specifications


def compare_detector(rows: List[Dict], specifications: Dict[str, Dict],
                     sources: Optional[Dict] = None) -> Dict:
    """The `detector` block: one comparison per (specification, code,
    expected value), grouped by (specification, code)."""
    combinations: Dict[Tuple[str, str, str], Dict] = {}
    for row in rows:
        if not row.get("expected"):
            continue
        key = (row["spec"], row["code"], row["expected"])
        entry = combinations.setdefault(key, {
            "imported_rows": 0, "analysed_records": 0, "findings": set(),
            "app_findings": set(), "analysed_findings": set(), "outcomes": Counter()})
        finding = (row.get("apk", ""), row.get("class", ""), row.get("method", ""))
        entry["findings"].add(finding)
        if row["source"] == "imported":
            entry["imported_rows"] += 1
            if row.get("origin") == "app":
                entry["app_findings"].add(finding)
        else:
            entry["analysed_records"] += 1
            entry["analysed_findings"].add(finding)
            if row.get("outcome"):
                entry["outcomes"][row["outcome"]] += 1
    by_code: Dict[Tuple[str, str], List[Dict]] = defaultdict(list)
    for (spec, code, expected), entry in sorted(combinations.items()):
        result = compare_expected(specifications.get(spec), expected)
        by_code[(spec, code)].append({
            "expected": expected, **result,
            "compared": result["status"] in COMPARED_STATUSES,
            "mismatch": bool(result["spec_only"] or result["detector_only"]),
            "imported_rows": entry["imported_rows"],
            "analysed_records": entry["analysed_records"],
            "findings": len(entry["findings"]),
            "app_findings": len(entry["app_findings"]),
            "analysed_findings": len(entry["analysed_findings"]),
            "outcomes": dict(sorted(entry["outcomes"].items())),
        })
    codes = []
    for (spec, code), comparisons in sorted(by_code.items()):
        if any(item["mismatch"] for item in comparisons):
            status = "mismatch"
        elif all(item["compared"] for item in comparisons):
            status = "no_mismatch"
        elif not any(item["compared"] for item in comparisons):
            status = "not_compared"
        else:
            status = "partly_compared"
        codes.append({
            "specification": spec, "code": code, "status": status,
            "multiple_expected": len(comparisons) > 1,
            "spec_only": sorted({value for item in comparisons for value in item["spec_only"]}),
            "detector_only": sorted({value for item in comparisons
                                     for value in item["detector_only"]}),
            "expected": comparisons,
        })
    comparisons = [item for code in codes for item in code["expected"]]
    with_expected = [row for row in rows if row.get("expected")]
    totals = {
        "combinations": len(comparisons),
        "codes": len(codes),
        "specifications": len({code["specification"] for code in codes}),
        "by_status": dict(sorted(Counter(item["status"] for item in comparisons).items())),
        "mismatching_combinations": sum(1 for item in comparisons if item["mismatch"]),
        "mismatching_codes": sum(1 for code in codes if code["status"] == "mismatch"),
        "not_compared_combinations": sum(1 for item in comparisons if not item["compared"]),
        "multiple_expected_codes": sum(1 for code in codes if code["multiple_expected"]),
    }
    return {
        "note": DETECTOR_NOTE,
        "sources": dict(sources or {}),
        "rows": {
            "imported": sum(1 for row in rows if row["source"] == "imported"),
            "imported_with_expected": sum(1 for row in with_expected
                                          if row["source"] == "imported"),
            "analysed": sum(1 for row in rows if row["source"] == "analysed"),
            "analysed_with_expected": sum(1 for row in with_expected
                                          if row["source"] == "analysed"),
        },
        "totals": totals,
        "codes": codes,
    }


def evaluate_detector(specifications_dir: Path, findings: Iterable[Path] = (),
                      runs: Iterable[Path] = ()) -> Dict:
    imported, files, unreadable = detector_rows_from_findings(findings)
    analysed, run_count = detector_rows_from_runs(runs)
    return compare_detector(imported + analysed, load_specifications(specifications_dir),
                            {"findings_files": files, "unreadable_findings_files": unreadable,
                             "runs": run_count})


def _cell(values: Iterable[str]) -> str:
    text = ", ".join(str(value) for value in values)
    return text.replace("|", "\\|") or "-"


def detector_markdown(block: Dict) -> str:
    totals, rows = block["totals"], block["rows"]
    lines = [
        "# Specification versus detector (report only)", "",
        block["note"], "",
        f"{totals['combinations']} (specification, code, expected value) combinations over "
        f"{totals['codes']} codes and {totals['specifications']} specifications, from "
        f"{rows['imported_with_expected']} imported rows and {rows['analysed_with_expected']} "
        f"analysed records with an expected value; {totals['mismatching_combinations']} with a "
        f"mismatch; {totals['not_compared_combinations']} not compared (no specification, a "
        f"description its sets do not name, or values it holds no set for); "
        f"{totals['multiple_expected_codes']} codes {MULTIPLE_EXPECTED_NOTE}.", "",
    ]
    unreadable = block.get("sources", {}).get("unreadable_findings_files", 0)
    if unreadable:
        lines += [f"{unreadable} findings files could not be read and were skipped.", ""]
    mismatches = [(code, item) for code in block["codes"] for item in code["expected"]
                  if item["mismatch"]]
    compared = sum(1 for code in block["codes"] for item in code["expected"]
                   if item["compared"])
    if not compared:
        # Nothing read, or nothing the specifications' sets could be held
        # against: never worded as agreement.
        lines.append("Nothing was compared: no recorded expected value could be held "
                     "against a specification.")
    elif mismatches:
        lines += [
            "| Specification | Code | Expected | Specification admits, analyzer rejects "
            "| Analyzer accepts, specification omits | Findings (app) | Analysed records "
            "| Outcomes |",
            "|---|---|---|---|---|---:|---:|---|",
        ]
        for code, item in mismatches:
            outcomes = ", ".join(f"{name}: {count}" for name, count in item["outcomes"].items())
            lines.append(
                f"| {code['specification']} | {code['code']}"
                f"{' (several expected values)' if code['multiple_expected'] else ''} | "
                f"{_cell([item['expected']])} | {_cell(item['spec_only'])} | "
                f"{_cell(item['detector_only'])} | {item['findings']} ({item['app_findings']}) | "
                f"{item['analysed_records']} | {outcomes or '-'} |")
    else:
        lines.append("No mismatch among the compared values.")
    not_compared = [(code, item) for code in block["codes"] for item in code["expected"]
                    if not item["compared"]]
    if not_compared:
        lines += ["", "Not compared: " + "; ".join(
            f"{code['specification']} {code['code']} ({item['status']})"
            for code, item in not_compared)]
    several = [code for code in block["codes"] if code["multiple_expected"]]
    if several:
        lines += ["", "Codes with more than one expected value: " + "; ".join(
            f"{code['specification']} {code['code']} ({len(code['expected'])})"
            for code in several)]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--specifications-dir", type=Path,
                        default=library_data("specifications"))
    parser.add_argument("-o", "--output", type=Path,
                        help="write the records and summary as JSON here (and a .md next to it)")
    parser.add_argument("--findings", nargs="+", type=Path, metavar="PATH",
                        help="findings files or directories (searched for .csv/.jsonl): compare "
                             "the expected value the analyzer recorded on each row with what "
                             "the specification admits (report only)")
    parser.add_argument("--runs", nargs="+", type=Path, metavar="PATH",
                        help="run directories (or directories holding runs): the same "
                             "comparison over the analysed records, with each finding's "
                             "final outcome (report only)")
    parser.add_argument("--no-rule", action="store_true",
                        help="skip the comparison with the published CrySL rule (no network)")
    args = parser.parse_args(argv)
    if args.no_rule and not (args.findings or args.runs):
        parser.error("--no-rule leaves nothing to report without --findings or --runs")
    for option, paths in (("--findings", args.findings), ("--runs", args.runs)):
        missing = [str(path) for path in paths or [] if not path.exists()]
        if missing:
            parser.error(f"{option}: no such file or directory: {', '.join(missing)}")
    detector = (evaluate_detector(args.specifications_dir, args.findings or [], args.runs or [])
                if args.findings or args.runs else None)
    payload: Dict = {}
    report = ""
    if not args.no_rule:
        records = evaluate(args.specifications_dir)
        totals = summary(records)
        report = markdown(records, totals)
        payload = {"summary": totals, "records": records}
    if detector is not None:
        report += ("\n" if report else "") + detector_markdown(detector)
        payload["detector"] = detector
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                               encoding="utf-8")
        args.output.with_suffix(".md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
