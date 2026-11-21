"""Findings as rows, whatever file or object they come from.

Every step that reads the findings file speaks the corpus vocabulary: one
row per finding, with columns apk, class, method, spec, message, repo_url,
commit, tool and detector_*. That vocabulary stays; what changes here is
where rows may come from. A findings file can be CSV (as always), JSON Lines
or a JSON array, chosen by its suffix, and a Python caller can hand over
Finding objects or plain dicts, which are written to a JSON Lines file the
steps then read like any other.

Nothing is dropped on the way. A JSON line that does not parse, or that is
not an object, becomes a row carrying SOURCE_PROBLEM, which
utils.corpus_rows.row_problems reports like a CSV row whose columns shifted,
so it is counted as invalid input with its reason.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Iterable, Mapping

JSON_LINES_SUFFIXES = (".jsonl", ".ndjson")
JSON_SUFFIXES = (".json",)
# A reserved column: why a record could not be read as a row at all.
SOURCE_PROBLEM = "_source_problem"


def _as_row(value: object) -> dict:
    """A record as a corpus row: string keys, string values, "" for null."""
    if is_dataclass(value) and not isinstance(value, type):
        value = value.to_row() if hasattr(value, "to_row") else asdict(value)
    if not isinstance(value, Mapping):
        return {SOURCE_PROBLEM: f"record is a {type(value).__name__}, not an object"}
    row = {}
    for key, item in value.items():
        if item is None:
            row[str(key)] = ""
        elif isinstance(item, (dict, list)):
            row[str(key)] = json.dumps(item, ensure_ascii=False, sort_keys=True)
        else:
            row[str(key)] = str(item)
    return row


def read_rows(path: Path | str) -> list[dict]:
    """The findings in ``path`` as rows, in file order.

    CSV is read as the corpus has always been read (utf-8-sig, so a byte
    order mark does not rename the first column). An empty or missing file
    raises like open() does.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in JSON_LINES_SUFFIXES:
        rows = []
        with open(path, encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    rows.append(_as_row(json.loads(line)))
                except json.JSONDecodeError as exc:
                    rows.append({SOURCE_PROBLEM: f"line {number} is not JSON: {exc.msg}"})
        return rows
    if suffix in JSON_SUFFIXES:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, list):
            raise ValueError(f"{path}: a JSON findings file holds a list of objects")
        return [_as_row(item) for item in data]
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(findings: Iterable[object], path: Path | str) -> Path:
    """Write findings (Finding objects or mappings) as JSON Lines."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for finding in findings:
            handle.write(json.dumps(_as_row(finding), ensure_ascii=False) + "\n")
    return path


def load_column_map(path: Path | str) -> dict[str, str]:
    """A column map file: {"<corpus column>": "<column in the input file>"}.

    For a findings file from another tool, whose columns are named its own
    way: {"apk": "package", "class": "className", "message": "description"}.
    """
    mapping = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(mapping, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in mapping.items()):
        raise ValueError(f"{path}: a column map is an object of column names")
    return mapping


def apply_column_map(rows: Iterable[Mapping], mapping: Mapping[str, str]) -> list[dict]:
    """Rows with the mapped columns renamed to the corpus names.

    Columns the map does not mention are kept as they are, so a detector's
    own fields still reach the analysis; a mapped column a row lacks stays
    absent rather than being invented.
    """
    renamed = {source: target for target, source in mapping.items()}
    return [{renamed.get(key, key): value for key, value in row.items()} for row in rows]
