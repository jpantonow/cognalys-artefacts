#!/usr/bin/env python3
"""Attach versioned specification semantics to extracted error JSON files."""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from cognalys.sources import read_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich extraction JSONs with local specification context."
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--specifications-dir", type=Path, required=True)
    parser.add_argument("--project", default="")
    parser.add_argument("--required-specs-csv", type=Path)
    return parser.parse_args()


def load_registry(directory: Path) -> dict[str, dict]:
    registry = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError(f"Specification without name: {path}")
        data["registry_file"] = path.name
        registry[name] = data
    return registry


def required_specifications(csv_file: Path, project: str = "") -> set[str]:
    return {
        str(row.get("spec", "")).strip()
        for row in read_rows(csv_file)
        if (
            str(row.get("spec", "")).strip()
            and (
                not project
                or str(row.get("apk", "")).strip() == project
            )
        )
    }


def assess_spec_applicability(data: dict, spec: dict) -> dict:
    """Require source evidence before attaching type-specific API semantics."""
    applies_to = [
        str(item).strip() for item in spec.get("applies_to_types", [])
        if str(item).strip()
    ]
    if not applies_to:
        return {
            "status": "legacy_unchecked",
            "evidence": "The legacy specification has no applies_to_types metadata.",
        }
    source = data.get("source_code", {})
    searchable = "\n".join(str(source.get(field, "")) for field in (
        "method_body", "imports", "surrounding_context", "call_sites"
    )).lower()
    matched = []
    for qualified in applies_to:
        simple = qualified.rsplit(".", 1)[-1].replace("$", ".").split(".")[-1]
        if qualified.lower() in searchable or re.search(
            rf"\b{re.escape(simple.lower())}\b", searchable
        ):
            matched.append(qualified)
    if matched:
        return {
            "status": "supported_by_source",
            "matched_types": matched,
            "evidence": "A governed API type occurs in the extracted source context.",
        }
    if not searchable.strip():
        reason = "No source was extracted for the external class."
    else:
        reason = "None of the governed API types occurs in the extracted source context."
    return {
        "status": "unverified",
        "matched_types": [],
        "evidence": reason,
    }


def observed_events(
    method_body: str,
    spec: dict,
    implicit_receiver_calls: list[dict] | None = None,
) -> list[dict]:
    events = []
    implicit_receiver_calls = implicit_receiver_calls or []
    for event in spec.get("events", []):
        occurrences = []
        for pattern in event.get("api_patterns", []):
            start = 0
            while True:
                index = method_body.find(pattern, start)
                if index < 0:
                    break
                line = method_body.count("\n", 0, index) + 1
                occurrences.append({"pattern": pattern, "method_line": line})
                start = index + len(pattern)
        expected_calls = {
            match.group(1)
            for pattern in event.get("api_patterns", [])
            for match in [re.search(r"([A-Za-z_]\w*)\s*\($", pattern)]
            if match
        }
        for implicit in implicit_receiver_calls:
            if implicit.get("call") not in expected_calls:
                continue
            occurrences.append(
                {
                    "pattern": f"implicit receiver: {implicit.get('call')}(...)" ,
                    "method_line": implicit.get("method_line"),
                    "detection": "kotlin_implicit_receiver",
                    "scope_function": implicit.get("scope_function"),
                    "receiver_expression": implicit.get("receiver_expression"),
                    "normalized_call": implicit.get("normalized_call"),
                }
            )
        events.append(
            {
                "event": event.get("name", ""),
                "occurrences": occurrences,
                "observed": bool(occurrences),
                "predicate_status": "unknown" if occurrences else "not_observed",
            }
        )
    return events


def unresolved_parameters(method_body: str) -> list[dict]:
    header = method_body.split("{", 1)[0]
    match = re.search(r"\((.*)\)", header, re.DOTALL)
    if not match:
        return []
    parameters = []
    for raw_parameter in match.group(1).split(","):
        clean = raw_parameter.strip()
        if not clean:
            continue
        if ":" in clean:
            name, param_type = clean.split(":", 1)
            parameters.append(
                {
                    "name": name.strip(),
                    "type": param_type.split("=", 1)[0].strip(),
                }
            )
        else:
            tokens = clean.replace("...", "[] ").split()
            if len(tokens) >= 2:
                parameters.append(
                    {"name": tokens[-1], "type": " ".join(tokens[:-1])}
                )
    return parameters


def main() -> int:
    args = parse_args()
    registry = load_registry(args.specifications_dir)
    if args.required_specs_csv:
        required = required_specifications(args.required_specs_csv, args.project)
        missing_registry = sorted(required - set(registry))
        if missing_registry:
            print(
                "Specification registry coverage failed; missing: "
                + ", ".join(missing_registry)
            )
            return 2
        print(
            f"Specification registry coverage: {len(required)}/{len(required)} "
            "sample specs available"
        )
    input_dir = args.input_dir / args.project if args.project else args.input_dir
    files = sorted(input_dir.rglob("*.json"))
    enriched = 0
    missing = 0

    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        error = data.get("error", {})
        source = data.get("source_code", {})
        spec_name = str(error.get("spec", "")).strip()
        spec = registry.get(spec_name)
        if spec:
            applicability = assess_spec_applicability(data, spec)
            parameter_resolution = source.get("method_parameter_resolution", [])
            unresolved = [
                {"name": item.get("name", ""), "type": item.get("type", "")}
                for item in parameter_resolution
                if item.get("resolution_status") == "unresolved"
            ] if parameter_resolution else unresolved_parameters(
                str(source.get("method_body", ""))
            )
            context = {
                "status": (
                    "available" if applicability["status"] != "unverified"
                    else "applicability_unverified"
                ),
                **spec,
                "applicability": applicability,
                "observed_source_events": observed_events(
                    str(source.get("method_body", "")),
                    spec,
                    source.get("implicit_receiver_calls", []),
                ),
                "unresolved_method_parameters": unresolved,
                "method_parameter_resolution": parameter_resolution,
            }
            enriched += 1
        else:
            context = {
                "status": "missing",
                "name": spec_name,
                "important_note": (
                    "No verified local definition is available. The LLM must "
                    "not infer semantics from the specification name."
                ),
            }
            missing += 1
        data["specification_context"] = context
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(
        f"Specification context: {enriched} enriched, {missing} missing, "
        f"{len(files)} total"
    )
    return 2 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
