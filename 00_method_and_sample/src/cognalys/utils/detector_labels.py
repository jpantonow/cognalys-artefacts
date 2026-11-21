"""What a detector's labels and site kinds mean, read from data/detector_labels.json.

A runtime detector tags each report with a code (RVSec's
SECRETKEYSPEC-NOBS-03), a site kind inside the code (NOBS) and a label
(random-key-material). The label says what kind of statement the report is,
and code needs to know that in a few places: an echo of a report already
made is folded into the finding it echoes, and only a report that the
analyzer saw no producer can be answered by an origin it cannot see (a
provider-generated IV). The catalogue gives each label a report kind and a
meaning. The current meaning, the IV-origin gate (extraction.iv_provenance)
and reported_by_detector ask the report kind (value_check, order, echo,
unobserved_origin, unadmitted_origin), never a label name or its wording;
echo folding (integrations.rvsec.ECHO_LABELS, used by the generic join), the
value-check gates of the semantic validation and rvsec.LABEL_PRIORITY still
name RVSec's labels in code.

CometOTP EncryptionHelper.loadOrGenerateWrappedKey (agent_baseline_20261108):
the meaning stored with the finding called a SecureRandom key "the normal way
to make a key" under a "NOT OBSERVED" prefix, while the checker that ran
recorded that origin and does not admit it. Seven adjudications in seven runs
dismissed the finding on the published rule's "randomized" alternative. The
analysis now reads the catalogue's current meaning of a known label instead of
the text stored with the finding (current_detector_context), and a report kind
the catalogue flags outranks_rule_text_alternative is shown to extraction and
adjudication as the analyzer's own record (reported_by_detector); why each
flag is set is the catalogue's rationale.

This module is a leaf: the standard library and cognalys.workspace only.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from cognalys.workspace import library_data

CATALOG = library_data("detector_labels.json")


class DetectorCatalogError(RuntimeError):
    """The label catalogue is missing or unreadable.

    Raised rather than read as empty: empty meanings would silently change
    every finding's prompt.
    """


# Report kinds that state no fact the analyzer observed: an echo repeats
# another report, and an unobserved origin says the analyzer saw no producer,
# which an origin it cannot see may answer (a provider-generated IV). The
# flag outranks_rule_text_alternative tells extraction and adjudication that
# the listed record "recorded a fact and does not admit it", and that a
# report of no producer is never listed; on these kinds both statements
# would be false, so a catalogue that flags one is refused.
_KINDS_WITHOUT_AN_OBSERVED_FACT = ("unobserved_origin", "echo")


@lru_cache(maxsize=None)
def _load(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DetectorCatalogError(f"cannot read the detector label catalogue {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("detectors"), list):
        raise DetectorCatalogError(f"the detector label catalogue {path} has no detectors list")
    kinds = data.get("report_kinds") or {}
    for kind in _KINDS_WITHOUT_AN_OBSERVED_FACT:
        entry = kinds.get(kind) if isinstance(kinds, dict) else None
        if isinstance(entry, dict) and entry.get("outranks_rule_text_alternative"):
            raise DetectorCatalogError(
                f"the detector label catalogue {path} flags report kind {kind!r} "
                "outranks_rule_text_alternative, which is valid only on a report "
                "kind that records an observed fact")
    return data


def load_catalog(path: Optional[Path] = None) -> dict:
    """The parsed catalogue (cached per path)."""
    return _load(str(path or CATALOG))


def _detectors(catalog: Optional[dict] = None) -> List[dict]:
    return [entry for entry in (catalog or load_catalog()).get("detectors", [])
            if isinstance(entry, dict)]


def report_kinds(catalog: Optional[dict] = None) -> Dict[str, dict]:
    return dict((catalog or load_catalog()).get("report_kinds") or {})


def detector_tables(name: str, catalog: Optional[dict] = None) -> Dict[str, object]:
    """One detector's tables: site_kinds, label_meanings, not_observed, labels_by_report."""
    for entry in _detectors(catalog):
        if entry.get("name") != name:
            continue
        labels = entry.get("labels") or {}
        by_report: Dict[str, List[str]] = {}
        for label, item in labels.items():
            by_report.setdefault(str(item.get("report", "")), []).append(label)
        return {
            "site_kinds": dict(entry.get("site_kinds") or {}),
            "label_meanings": {label: str(item.get("meaning", "")) for label, item in labels.items()},
            "not_observed": str(entry.get("not_observed_statement", "")),
            "labels_by_report": by_report,
        }
    raise DetectorCatalogError(f"the detector label catalogue names no detector {name!r}")


def _detector_of(context: Mapping, catalog: Optional[dict] = None,
                 by_label: bool = True) -> Optional[dict]:
    """The detector that wrote the record: the first whose code_pattern matches
    its code; with no code and by_label, the first that defines its label."""
    code = str(context.get("code", "") or "").strip()
    label = str(context.get("label", "") or "").strip()
    for entry in _detectors(catalog):
        if code:
            if re.fullmatch(str(entry.get("code_pattern", "")), code):
                return entry
        elif by_label and label and label in (entry.get("labels") or {}):
            return entry
    return None


def label_entry(context: Mapping, catalog: Optional[dict] = None) -> dict:
    """The catalogue entry ({report, meaning}) of the record's label, or {}."""
    if not isinstance(context, Mapping):
        return {}
    label = str(context.get("label", "") or "").strip()
    detector = _detector_of(context, catalog) if label else None
    if not detector:
        return {}
    return dict((detector.get("labels") or {}).get(label) or {})


def report_kind(context: Mapping, catalog: Optional[dict] = None) -> str:
    """The record's report kind (value_check, order, echo, unobserved_origin,
    unadmitted_origin, ...), or "" for an unknown label."""
    return str(label_entry(context, catalog).get("report", "") or "")


def current_detector_context(context: Mapping, catalog: Optional[dict] = None) -> Dict:
    """The record with the catalogue's current texts for a known label.

    label_meaning is replaced when the record stored one, and site_kind
    ("<KIND>: <text>") when it starts with a kind the detector defines.
    Anything else, and any record of an unknown detector or label, comes back
    unchanged; no key is added.

    Texts are replaced only when the record's code matches a catalogue
    detector's code_pattern: a record with no code, or a code of another
    shape, may come from another analyzer that uses the same label word
    ("violation") with a meaning of its own, and keeps its stored texts.
    report_kind still reads a label-only record by its label.
    """
    if not isinstance(context, Mapping):
        return context  # type: ignore[return-value]
    current = dict(context)
    label = str(context.get("label", "") or "").strip()
    detector = _detector_of(context, catalog, by_label=False) if label else None
    entry = (detector.get("labels") or {}).get(label) if detector else None
    if not entry:
        return current
    meaning = str(entry.get("meaning", "") or "")
    if str(context.get("label_meaning", "") or "").strip() and meaning:
        current["label_meaning"] = meaning
    site_kind = str(context.get("site_kind", "") or "")
    kind, colon, _ = site_kind.partition(":")
    kinds = detector.get("site_kinds") or {}
    if colon and kind.strip() in kinds:
        current["site_kind"] = f"{kind.strip()}: {kinds[kind.strip()]}"
    return current


def outranking_record(context: Mapping, specification: str = "", method: str = "",
                      catalog: Optional[dict] = None) -> Dict:
    """The record as shown to extraction and adjudication, or {}.

    Only for a label whose report kind the catalogue flags
    outranks_rule_text_alternative: what the analyzer recorded and does not
    admit. Every other record gives {}, so those findings' prompts do not
    change. The flag is valid only on a report kind that records an observed
    fact: load_catalog refuses a catalogue that sets it on unobserved_origin
    or echo, since the prompt sentences say a listed record recorded a fact
    and that a report of no producer is never listed.

    The record carries the flagged code only, never the record's codes
    summary: a finding's label is the highest-priority one among its codes,
    so the summary can also count reports of another kind (Session
    LogFile$Writer.writeEntry: "SECRETKEYSPEC-NOBS-00 [not-observed] (4),
    SECRETKEYSPEC-NOBS-03 [random-key-material] (3)"), and those must not
    reach the prompts under this record.
    """
    entry = label_entry(context, catalog)
    kind = str(entry.get("report", "") or "")
    kind_entry = report_kinds(catalog).get(kind) or {}
    if not kind_entry.get("outranks_rule_text_alternative"):
        return {}
    record = {
        "specification": specification,
        "method": method,
        "code": context.get("code", ""),
        "label": context.get("label", ""),
        "report": kind,
        "report_meaning": kind_entry.get("statement", ""),
        "meaning": entry.get("meaning", ""),
        "expected": context.get("expected", ""),
        "value": context.get("value", ""),
    }
    return {key: value for key, value in record.items() if value}


def reported_by_detector(error: Mapping, catalog: Optional[dict] = None) -> Dict:
    """{"reported_by_detector": [records]} for the finding's own record and each
    joint finding's (joint_detector_contexts), or {} when none is flagged."""
    if not isinstance(error, Mapping):
        return {}
    records = []
    own = outranking_record(error.get("detector_context") or {},
                            str(error.get("spec", "") or ""),
                            str(error.get("method_name", "") or ""), catalog)
    if own:
        records.append(own)
    contexts = error.get("joint_detector_contexts") or {}
    for item in error.get("joint_findings") or []:
        if not isinstance(item, Mapping):
            continue
        context = contexts.get(item.get("unique_error_id", "")) if isinstance(contexts, Mapping) else None
        record = outranking_record(context or {}, str(item.get("specification", "") or ""),
                                   str(item.get("method", "") or ""), catalog)
        if record:
            records.append(record)
    return {"reported_by_detector": records} if records else {}
