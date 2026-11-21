#!/usr/bin/env python3
"""Propose, validate and install Semgrep rules for specifications without one.

    python -m cognalys.validation.propose_detector_rules \
        --required-specs-csv data/processed/errors.csv [--runs 'outputs/run_*']

For each specification named in the CSV (or, without it, every
specification with an extracted finding) that has no rule yet, the model
proposes one and it is kept only if Semgrep agrees with the reported
findings, the advisories' fixed revisions and the model's own examples (see
src/validation/rule_proposal.py). Decisions are recorded in
data/detector_rules/semgrep/generated_rules.json so the next run neither
asks again nor gets a different rule without a reason.

Run it before the steps that read the rules, never alongside a run: the
pipeline's `detector_rules` step does exactly that. Never fails the run for
a specification it could not cover; exits 1 only when it could not work at
all (no Semgrep, no model).
"""
from __future__ import annotations

from cognalys.sources import read_rows
from cognalys.workspace import library_data, workspace_root
import argparse
import csv
import glob
import hashlib
import json
import sys
from pathlib import Path

ROOT = workspace_root()

from cognalys.validation import rule_proposal  # noqa: E402
from cognalys.validation import semgrep_rules  # noqa: E402

STYLE_RULES = ("WebViewDebuggingSpec", "ZipSlipSpec")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", default="outputs/run_*",
                        help="Glob of run directories whose extracted findings are the cases.")
    parser.add_argument("--required-specs-csv", type=Path,
                        help="Only the specifications this findings CSV names.")
    parser.add_argument("--specifications-dir", type=Path,
                        default=library_data("specifications"))
    parser.add_argument("--spec", action="append", default=[],
                        help="Only this specification (repeatable).")
    parser.add_argument("--replace", action="store_true",
                        help="Regenerate rules this script accepted before.")
    parser.add_argument("--retry", action="store_true",
                        help="Ask again for specifications judged not local or rejected.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Evaluate proposals but write nothing.")
    parser.add_argument("--model", default="gpt-6.1-terra")
    parser.add_argument("--endpoint", default="https://api.openai.com/v1")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--max-output-tokens", type=int, default=32768)
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--prompt-log-dir", type=Path)
    return parser.parse_args()


def required_specifications(csv_path: Path | None) -> set[str]:
    if not csv_path:
        return set()
    return {str(row.get("spec", "")).strip() for row in read_rows(csv_path)} - {""}


def extraction_files(pattern: str) -> list[Path]:
    runs = pattern if Path(pattern).is_absolute() else str(ROOT / pattern)
    return sorted(Path(path) for path in glob.glob(str(Path(runs) / "error_methods" / "*" / "*.json")))


def specifications_with_findings(files: list[Path]) -> set[str]:
    names = set()
    for path in files:
        try:
            names.add(str((json.loads(path.read_text(encoding="utf-8")).get("error") or {}).get("spec", "")))
        except (OSError, ValueError):
            continue
    return names - {""}


def main() -> int:
    args = parse_args()
    files = extraction_files(args.runs)
    wanted = set(args.spec) or required_specifications(args.required_specs_csv) \
        or specifications_with_findings(files)
    specification_files = {
        name: args.specifications_dir / f"{name}.json" for name in sorted(wanted)
        if (args.specifications_dir / f"{name}.json").is_file()
    }
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest()
              for name, path in specification_files.items()}
    provenance = rule_proposal.load_provenance()
    for name in rule_proposal.stale_rules(provenance, hashes):
        print(f"{name}: its specification changed after its rule was accepted; "
              f"the rule is kept as is (rerun with --spec {name} --replace to regenerate)")

    pending = []
    for name, path in specification_files.items():
        cases = rule_proposal.finding_cases(name, files)
        fingerprints = rule_proposal.case_fingerprints(cases)
        reason = rule_proposal.skip_reason(
            name, hashes[name], fingerprints, provenance,
            rule_exists=semgrep_rules.rule_file(name) is not None,
            replace=args.replace, retry=args.retry,
        )
        if not reason and not cases:
            reason = "no extracted finding of it to check a rule against"
        if reason:
            print(f"{name}: skipped, {reason}")
            continue
        pending.append((name, path, cases, fingerprints))
    if not pending:
        print("Detector rules: nothing to propose")
        return 0
    if not semgrep_rules.find_semgrep():
        print("Detector rules: no semgrep executable, so no proposal can be checked",
              file=sys.stderr)
        return 1

    from cognalys.llm.llm_providers import remote_provider
    from cognalys.llm.response_parsing import ResponseParser

    try:
        provider = remote_provider(
            endpoint=args.endpoint, model=args.model, timeout=args.timeout,
            max_output_tokens=args.max_output_tokens,
            reasoning_effort=args.reasoning_effort,
            prompt_log_dir=args.prompt_log_dir,
        )
    except Exception as exc:  # the provider reports a missing key this way
        print(f"Detector rules unavailable: {exc}", file=sys.stderr)
        return 1

    def generate(prompt: str, schema: dict):
        response = provider.generate(
            prompt, rule_proposal.SYSTEM_PROMPT, json_mode=True,
            json_schema=schema, schema_name="detector_rule_proposal",
            trace_stage="detector_rule_proposal",
        )
        return ResponseParser.parse_json_object(response)

    style = {
        name: path.read_text(encoding="utf-8")
        for name in STYLE_RULES
        if (path := semgrep_rules.rule_file(name)) is not None
    }
    summary = {"accepted": 0, "not_local": 0, "rejected": 0}
    for name, path, cases, fingerprints in pending:
        context = json.loads(path.read_text(encoding="utf-8"))
        vouched = sum(1 for case in cases if case.origin == "finding" and case.required)
        print(f"{name}: proposing a rule against {len(cases)} real case(s), "
              f"{vouched} of them vouched for", flush=True)
        outcome = rule_proposal.propose_rule(name, context, cases, generate, style)
        summary[outcome["status"]] += 1
        detail = outcome.get("reason") or "; ".join(outcome.get("problems", [])[:3])
        print(f"{name}: {outcome['status']} after {outcome.get('attempts', 0)} attempt(s)"
              + (f": {detail[:300]}" if detail else ""), flush=True)
        if args.dry_run:
            # Nothing is written, so what would have been is shown instead.
            for item in outcome.get("results", []):
                print(f"    [{item['origin']}] expected {item['expected']}, got "
                      f"{item['got']}{'' if item['required'] else ' (not required)'}: "
                      f"{item['label']}")
            if outcome.get("rule_text"):
                print("    rule:\n" + "\n".join(
                    "      " + line for line in outcome["rule_text"].splitlines()))
            continue
        rule_proposal.record_outcome(
            name, outcome, provenance, hashes[name], fingerprints, args.model,
        )
    print("Detector rules: " + ", ".join(f"{key} {value}" for key, value in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
