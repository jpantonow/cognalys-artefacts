#!/usr/bin/env python3
"""Whether a row of a findings CSV was parsed into the columns it claims.

A findings CSV that escapes a quote with a backslash (`\\"TLS\\"`) instead
of doubling it (`""TLS""`, as RFC 4180 and Python's csv module expect) is
read without any error: the reader closes the field early, and every later
value shifts one or more columns. The row then carries a plausible-looking
but wrong `spec`, `class` or `method` -- one such row in
errors_amostragem_priorizada.csv read as spec="35", which is the value of
its `time` column -- and the pipeline spends a clone, an extraction and a
model call on a finding that was never reported.

The checks here are structural, not about any detector: the row has the
header's number of fields, the APK column names an APK, and the columns the
corpus records as numbers parse as numbers. A row that fails is kept in the
inventory with its original values and the reason, never dropped silently
and never repaired by guessing where the columns should have split.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from cognalys.utils.detector_labels import current_detector_context

# Columns the corpus writers (ape, scan_fdroid_pmd) always fill with numbers.
NUMERIC_COLUMNS = ("rep", "timeout", "time")
INPUT_INVALID = "input_invalid"


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def row_problems(row: Dict[Optional[str], object]) -> List[str]:
    """What is structurally wrong with one csv.DictReader row; [] if nothing."""
    problems: List[str] = []
    # A record that could not be read as a row at all: an unparsable JSON
    # line, or a JSON value that is not an object (see cognalys.sources).
    source_problem = row.get("_source_problem")
    if source_problem:
        return [str(source_problem)]
    # DictReader puts surplus fields under the key None and fills missing
    # ones with None: both mean the row did not split where the header does.
    extra = row.get(None)
    if extra:
        problems.append(f"{len(extra)} more field(s) than the header")
    missing = [key for key, value in row.items() if key is not None and value is None]
    if missing:
        problems.append(f"{len(missing)} fewer field(s) than the header")
    apk = str(row.get("apk") or "").strip()
    if apk and not apk.lower().endswith(".apk"):
        problems.append(f"apk column does not name an APK: {apk[:60]!r}")
    for column in NUMERIC_COLUMNS:
        value = row.get(column)
        if isinstance(value, str) and value.strip() and not _is_number(value.strip()):
            problems.append(f"{column} column is not a number: {value.strip()[:40]!r}")
    if problems and any(
        isinstance(value, str) and ("\\\"" in value or value.rstrip().endswith("\\"))
        for value in row.values()
    ):
        problems.append(
            "a field contains a backslash-escaped quote; CSV escapes a quote "
            "by doubling it, so the columns after it are shifted"
        )
    return problems


DETECTOR_CONTEXT_PREFIX = "detector_"


def detector_context(row: Dict[Optional[str], object]) -> Dict[str, str]:
    """The row's ``detector_*`` columns: what the detector itself recorded.

    A findings CSV may carry more of the detector's output than the message:
    the report code, its label and what the label means, the value seen, the
    source location, observations across runs. Any column named
    ``detector_<name>`` is carried through to the analysis as ``<name>``,
    only when filled, so a new detector needs no change here. The values are
    kept as written except the texts the label catalogue owns: for a label it
    knows, the label's meaning and the site kind's text are its current ones
    (utils.detector_labels.current_detector_context), so a meaning reworded
    since the row was written does not reach the analysis in its old words.
    """
    context: Dict[str, str] = {}
    for key, value in row.items():
        if not isinstance(key, str) or not key.startswith(DETECTOR_CONTEXT_PREFIX):
            continue
        text = str(value or "").strip()
        if text:
            context[key[len(DETECTOR_CONTEXT_PREFIX):]] = text
    return current_detector_context(context)


def invalid_row_record(row_index: int, row: Dict, problems: List[str]) -> Dict:
    """The inventory entry for a row that cannot be trusted."""
    original = {str(key): value for key, value in row.items() if key is not None}
    if row.get(None):
        original["__extra_fields__"] = list(row[None])
    return {
        "status": "skipped",
        "reason": INPUT_INVALID,
        "row_index": row_index,
        "problems": problems,
        "original_row": original,
    }
