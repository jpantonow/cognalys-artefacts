#!/usr/bin/env python3
"""Turn a runtime-verification misuse package into the pipeline's findings CSV.

The package (e.g. data/raw/exp_novo, campaign estudo02-20260916) holds one
row per misuse ``(apk, class, method, spec)`` in ``*misuses-selected.csv``,
the raw monitor reports behind each one, and the corpus sheet that names the
repository and the exact commit each APK was built from. The pipeline reads a
flat CSV of ``apk, rep, timeout, tool, time, spec, class, method, message,
unique_msg, repo_url`` instead, so this script writes one such row per misuse.
Every misuse is kept, whatever its ``outcome``; nothing is ranked or sampled.

Two columns are rewritten to the shape the pipeline already parses:

* ``message`` is the plain ``msg='...'`` of the monitor envelope
  (``v=1 code=... val=... exp=... msg='expecting one of X but found Y'``).
  applied_solutions.parse_algorithm_predicate reads "expecting one of X but
  found Y" up to the end of the text, so the whole envelope would leave the
  closing quote and any trailing ``vcls=``/``vfp=`` inside the found value.
* ``unique_msg`` is ``class:::method:::spec:::ErrorType:::message``. The
  package writes seven parts (code and event in the middle), and the readers
  that split it (pipeline_common, migration_policy, the previews) take part 3
  as the violation type and part 4 as the message.

``commit`` is the corpus sheet's ``head_pinned_commit``, which extraction
checks out through revision_pin. ``pinned_commit`` belongs to an earlier
build and its line numbers do not match the reports.

The rest of what the detector recorded goes into ``detector_*`` columns,
which extraction carries to the analysis as the detector's own context: the
report code, its site kind and label with what the label means, the value
seen and the values admitted, the source location, and what the runs observed
(the value fingerprint across runs, the trust manager class, and the
package's mechanical severe-site check with its evidence).

A misuse whose join fails (no report, no corpus row, no commit) stops the
conversion: a finding without its repository or revision would be cloned at
the wrong code and analysed as if it were right.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from cognalys.utils.detector_labels import detector_tables

MISUSES_GLOB = "*misuses-selected.csv"
ACCUSATION_FILES = ("primary-accusations.csv", "severe-accusations.csv")
SEVERE_SITES = "severe-sites.csv"
CODES = "codes.csv"
CORPUS = "dataset.csv"

# Misuses without a raw report in the package ("undecided": material seen in a
# single run) still become rows; the detector's own word for an absent
# message is "unknown", which is what the older corpus carries.
NO_MESSAGE = "unknown"

COMMIT = re.compile(r"^[0-9a-fA-F]{7,40}$")
# key='quoted value' or key=bare, each preceded by start or a space. A quoted
# value ends at the quote that is followed by the next key or the end, so a
# quote inside the value (e.g. "found 'x'") does not cut it short.
ENVELOPE_FIELD = re.compile(
    r"(?:^|\s)(?P<key>\w+)=(?:'(?P<quoted>.*?)'(?=\s+\w+=|\s*$)|(?P<bare>\S*))",
    re.DOTALL,
)

OUTPUT_COLUMNS = (
    "apk", "rep", "timeout", "tool", "time", "spec", "class", "method",
    "message", "unique_msg", "repo_url", "repo", "commit", "apk_url",
    "outcome", "origin", "runs", "misuse_id",
    # Carried to the analysis as the detector's own context (see
    # utils.corpus_rows.detector_context).
    "detector_code", "detector_site_kind", "detector_label",
    "detector_label_meaning", "detector_value", "detector_expected",
    "detector_source_location", "detector_runtime_observation",
)

# What each site kind and label means, from the package's group guide
# (20260924_mop_errors_group_guide.md, sections 4.1, 4.2 and 5). The
# message alone does not say it: "not observed" reports read like violations
# and an analysis without these meanings asked for "the detector's
# undocumented predicate-generation semantics" and stopped. The texts are
# data (data/detector_labels.json, read by utils.detector_labels), with the
# kind of statement each label makes.
_RVSEC_TABLES = detector_tables("rvsec")
SITE_KINDS: Dict[str, str] = _RVSEC_TABLES["site_kinds"]  # type: ignore[assignment]
NOT_OBSERVED: str = _RVSEC_TABLES["not_observed"]  # type: ignore[assignment]
LABEL_MEANINGS: Dict[str, str] = _RVSEC_TABLES["label_meanings"]  # type: ignore[assignment]
# A disassembled checkServerTrusted is a few hundred characters; this only
# stops an unexpected cell from flooding the analysis packet.
EVIDENCE_LIMIT = 4000

Key = Tuple[str, str, str, str]


class ConversionError(RuntimeError):
    """The package cannot be converted without guessing."""


def read_rows(path: Path) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_envelope(message: str) -> Dict[str, str]:
    """The ``key=value`` fields of a monitor message; {} if it is not one."""
    fields: Dict[str, str] = {}
    for match in ENVELOPE_FIELD.finditer(message or ""):
        value = match.group("quoted")
        if value is None:
            value = match.group("bare") or ""
        fields.setdefault(match.group("key"), value)
    return fields


def misuse_key(row: Dict[str, str]) -> Key:
    return (row["apk"], row["class"], row["method"], row["spec"])


def read_reports(paths: Iterable[Path]) -> Dict[Key, List[Dict[str, str]]]:
    """Every report of each misuse, in file order, across the files."""
    reports: Dict[Key, List[Dict[str, str]]] = {}
    for path in paths:
        if not path.is_file():
            continue
        for row in read_rows(path):
            reports.setdefault(misuse_key(row), []).append(row)
    return reports


def runtime_observation(misuse: Dict[str, str], reports: List[Dict[str, str]],
                        sites: List[Dict[str, str]]) -> str:
    """What the runs observed about one misuse, as sentences of fact.

    The value fingerprint (vfp) is the strongest of these: every run starts
    from a clean, read-only emulator, so bytes with one fingerprint in every
    run are a constant or derived from constants, and varying ones were
    generated or read at run time.
    """
    parts: List[str] = []
    runs = (misuse.get("runs") or "").strip()
    if runs:
        every = (misuse.get("every_run") or "").strip().lower() == "true"
        parts.append(
            f"Reported in {runs} run(s) of the app"
            + (", every run of the campaign" if every else "")
            + "; each run starts from a clean, read-only emulator."
        )
    fingerprints: Dict[str, set] = {}
    classes: List[str] = []
    for report in reports:
        envelope = parse_envelope(report.get("message", ""))
        run = (report.get("rep", ""), report.get("timeout", ""), report.get("tool", ""))
        if envelope.get("vfp"):
            fingerprints.setdefault(envelope["vfp"], set()).add(run)
        if envelope.get("vcls") and envelope["vcls"] not in classes:
            classes.append(envelope["vcls"])
    if fingerprints:
        fingerprinted_runs = set().union(*fingerprints.values())
        parts.append(
            f"Fingerprint of the byte[] material (vfp; the value itself is not "
            f"logged): {len(fingerprints)} distinct value(s) over "
            f"{len(fingerprinted_runs)} reporting run(s). One value across runs "
            "means constant material on this one emulated device; varying "
            "values mean bytes generated or read at run time."
        )
    if classes:
        parts.append("Trust manager class(es) in the array: " + ", ".join(classes) + ".")
    for site in sites:
        criterion = site.get("criterion", "")
        text = (
            f"Mechanical {criterion} check of this site ({site.get('code', '')}): "
            f"verdict={site.get('verdict', '')}; evidence: "
            f"{(site.get('evidence') or '').strip()[:EVIDENCE_LIMIT]}"
        )
        if criterion == "trust-all":
            text += (
                " (the trust-all check has false negatives: a no-op manager "
                "that also logs is counted as not-trust-all)"
            )
        parts.append(text)
    return " ".join(parts)


def repo_slug(repo_url: str) -> str:
    parts = repo_url.rstrip("/").removesuffix(".git").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else ""


def find_misuses_sheet(package: Path) -> Path:
    sheets = sorted(package.glob(MISUSES_GLOB))
    if len(sheets) != 1:
        raise ConversionError(
            f"expected exactly one {MISUSES_GLOB} in {package}, "
            f"found {len(sheets)}"
        )
    return sheets[0]


def convert(package: Path, apk_dir: Optional[Path] = None) -> List[Dict[str, str]]:
    """Every misuse of the package as a findings row.

    ``apk_dir`` (default ``<package>/apks``) may hold the APKs the campaign
    instrumented, named like the ``apk`` column. When one is there, its path
    becomes ``apk_url`` and extraction reads the reported method's bytecode
    from the build the findings were observed on. A store release is never
    used in its place: it is another build.
    """
    apk_dir = Path(apk_dir) if apk_dir else package / "apks"
    for name in (CODES, CORPUS):
        if not (package / name).is_file():
            raise ConversionError(f"{name} not found in {package}")
    misuses = read_rows(find_misuses_sheet(package))
    codes = {row["code"]: row for row in read_rows(package / CODES)}
    corpus = {row["apk"]: row for row in read_rows(package / CORPUS)}
    reports = read_reports(package / name for name in ACCUSATION_FILES)
    sites: Dict[Key, List[Dict[str, str]]] = {}
    if (package / SEVERE_SITES).is_file():
        for row in read_rows(package / SEVERE_SITES):
            sites.setdefault(misuse_key(row), []).append(row)

    output: List[Dict[str, str]] = []
    problems: List[str] = []
    for misuse in misuses:
        key = misuse_key(misuse)
        label = "/".join(key)
        sheet = corpus.get(misuse["apk"])
        if sheet is None:
            problems.append(f"{label}: apk not in {CORPUS}")
            continue
        repo_url = (sheet.get("repo_url") or "").strip()
        commit = (sheet.get("head_pinned_commit") or "").strip()
        if not repo_url:
            problems.append(f"{label}: no repo_url in {CORPUS}")
            continue
        if not COMMIT.match(commit):
            problems.append(f"{label}: head_pinned_commit is not a commit: {commit!r}")
            continue

        misuse_reports = reports.get(key, [])
        misuse_sites = sites.get(key, [])
        if misuse_reports:
            report = misuse_reports[0]
            envelope = parse_envelope(report.get("message", ""))
            code = report.get("code") or envelope.get("code", "")
            message = envelope.get("msg") or NO_MESSAGE
            id_parts = (report.get("unique_msg") or "").split(":::")
            error_type = id_parts[3] if len(id_parts) > 4 else ""
            run = {column: report.get(column, "") for column in ("rep", "timeout", "tool", "time")}
            source = report.get("source", "")
        elif misuse_sites:
            envelope, message, source = {}, NO_MESSAGE, ""
            code = misuse_sites[0].get("code", "")
            error_type = ""
            run = {"rep": "", "timeout": "", "tool": "", "time": ""}
        else:
            problems.append(
                f"{label}: no report in {', '.join(ACCUSATION_FILES)} "
                f"and no site in {SEVERE_SITES}"
            )
            continue
        code_row = codes.get(code, {})
        error_type = error_type or code_row.get("error_type", "")
        site_kind = code_row.get("site_kind", "")
        code_label = code_row.get("label", "")

        output.append({
            "apk": misuse["apk"],
            **run,
            "spec": misuse["spec"],
            "class": misuse["class"],
            "method": misuse["method"],
            "message": message,
            "unique_msg": ":::".join(
                (misuse["class"], misuse["method"], misuse["spec"], error_type, message)
            ),
            "repo_url": repo_url,
            "repo": repo_slug(repo_url),
            "commit": commit,
            "apk_url": (
                str((apk_dir / misuse["apk"]).resolve())
                if (apk_dir / misuse["apk"]).is_file() else ""
            ),
            "outcome": misuse.get("outcome", ""),
            "origin": misuse.get("origin", ""),
            "runs": misuse.get("runs", ""),
            "misuse_id": misuse.get("id", ""),
            "detector_code": code,
            "detector_site_kind": (
                f"{site_kind}: {SITE_KINDS[site_kind]}" if site_kind in SITE_KINDS
                else site_kind
            ),
            "detector_label": code_label,
            "detector_label_meaning": LABEL_MEANINGS.get(code_label, ""),
            "detector_value": envelope.get("val", ""),
            "detector_expected": envelope.get("exp", ""),
            "detector_source_location": source,
            "detector_runtime_observation": runtime_observation(
                misuse, misuse_reports, misuse_sites,
            ),
        })
    if problems:
        raise ConversionError(
            f"{len(problems)} misuse(s) cannot be converted:\n  "
            + "\n  ".join(problems)
        )
    return output


def write_rows(rows: List[Dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path, help="Directory of the misuse package.")
    parser.add_argument("--output", type=Path, required=True, help="Findings CSV to write.")
    parser.add_argument("--apk-dir", type=Path,
                        help="APKs the campaign analysed (default <package>/apks).")
    args = parser.parse_args(argv)
    try:
        rows = convert(args.package, args.apk_dir)
    except ConversionError as error:
        print(f"rv_import: {error}", file=sys.stderr)
        return 2
    write_rows(rows, args.output)
    outcomes: Dict[str, int] = {}
    for row in rows:
        outcomes[row["outcome"] or "(none)"] = outcomes.get(row["outcome"] or "(none)", 0) + 1
    summary = ", ".join(f"{name} {count}" for name, count in sorted(outcomes.items()))
    print(
        f"rv_import: {len(rows)} misuses in {len({row['apk'] for row in rows})} apps "
        f"({summary}) -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
