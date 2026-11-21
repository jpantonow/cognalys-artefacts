"""RQ5: model cost of the 34 findings of the October 2026 snapshot.

The snapshot is outputs/exp_final_20261019/consolidated/findings.json: 34
distinct findings, each with the outcome of its latest analysis (run2 plus
the targeted reruns, assembled on 2026-10-22). The cost of a finding is read
from the prompt traces of every run that made calls for it, with
cognalys.llm.cost.usage_by_finding (list prices of data/llm_prices.json,
calls delivered to a paid provider, cache hits and replays left out).

A run is a directory D under outputs/ with D/application_results/
openai_prompt_logs (cost.run_log_dir). Its start is the earliest timestamp
in its trace file names (YYYYMMDDTHHMMSS, local time, the prefix every call
trace carries); a log dir whose names carry none falls back to the earliest
"completed_at" of its traces.

Windows (fixed by the user):
  window 1 (snapshot): runs with calls for the 34 that started on
    2026-10-19 or 2026-10-20, up to and including the start of
    exp_final_20261019/subset_bitbanana_scope6;
  window 2: runs with calls for the 34 started from 2026-10-19 up to
    2026-11-03 23:59 (includes window 1). The runs before 2026-10-19 with
    calls for the same (class, method, spec) are reported apart
    ("before_window2"), not added to window 2.

usage_by_finding attributes a call to a finding only through a trace
finding_id of the form class:::method:::spec:::...; traces that name the
finding by a 12-hex key (the validation_fix steps) or name none (run-wide
steps such as solutions.base_port) are not counted by it. The script
reports both apart: the hex-keyed calls it can map to one of the 34 through
the run's findings_index.json / validation_fixes, and the unattributed calls
of the window's runs, so the size of what the method leaves out is known.

Median convention: over all 34 findings, a finding without any call in the
window counting US$ 0; with 34 values, the mean of the 17th and 18th.

Run (WSL, project venv):
  PYTHONPATH=src python scripts/rq5_cost_snapshot.py
Writes outputs/exp_final_20261019/consolidated/rq5_cost.json.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

from cognalys.llm import cost

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
SNAPSHOT = OUTPUTS / "exp_final_20261019" / "consolidated"
FINDINGS = SNAPSHOT / "findings.json"
RESULT = SNAPSHOT / "rq5_cost.json"
LAST_SNAPSHOT_RUN = OUTPUTS / "exp_final_20261019" / "subset_bitbanana_scope6"

WINDOW1_FIRST_DAY = datetime(2026, 10, 19)
WINDOW1_LAST_DAY_END = datetime(2026, 10, 20, 23, 59, 59)
WINDOW2_END = datetime(2026, 11, 3, 23, 59, 59)

OUTCOME_PR = "PR pronto"
OUTCOME_ISSUE = "issue para o mantenedor do app"
OUTCOME_FP = "falso positivo reconhecido"
OUTCOME_IE = "evidência insuficiente"

STAMP = re.compile(r"^(\d{8}T\d{6})")
HEX_KEY = re.compile(r"^[0-9a-f]{12}$")
PRUNE = {".git", ".gradle", "build", "node_modules", "__pycache__"}


def snapshot_findings():
    records = json.loads(FINDINGS.read_text(encoding="utf-8"))
    keys = [(r["class"], r["method"], r["spec"]) for r in records]
    if len(set(keys)) != len(records):
        sys.exit("findings.json: (class, method, spec) is not unique")
    return records


def run_dirs():
    """Every D under outputs/ that holds D/application_results/openai_prompt_logs."""
    found = []
    for current, dirs, _files in os.walk(OUTPUTS):
        dirs[:] = [d for d in dirs if d not in PRUNE]
        path = Path(current)
        if path.name == "openai_prompt_logs" and path.parent.name == "application_results":
            run = path.parent.parent
            if cost.run_log_dir(run) == path:
                found.append(run)
            dirs[:] = []
    return sorted(found)


def run_start(log_dir: Path):
    """(start, source): earliest trace-name stamp, else earliest completed_at."""
    names = [p.name for p in log_dir.glob("*_metadata.json")]
    stamps = [STAMP.match(n).group(1) for n in names if STAMP.match(n)]
    if stamps:
        return datetime.strptime(min(stamps), "%Y%m%dT%H%M%S"), "trace_file_name"
    completed = []
    for p in log_dir.glob("*_metadata.json"):
        try:
            value = json.loads(p.read_text(encoding="utf-8")).get("completed_at")
            completed.append(datetime.fromisoformat(str(value)[:19]))
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return (min(completed), "completed_at") if completed else (None, "none")


def hex_key_map(run: Path) -> dict:
    """12-hex finding key -> (apk, method, spec) as the run's own files record it."""
    keys = {}
    index = run / "findings_index.json"
    try:
        for item in json.loads(index.read_text(encoding="utf-8")).get("findings", []):
            keys[item["finding_key"]] = (item.get("apk"), item.get("method"), item.get("spec"))
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    for path in (run / "application_results" / "validation_fixes").glob("*_validation_fix.json"):
        key = path.name.split("_", 1)[0]
        if key in keys:
            continue
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = item.get("finding") if isinstance(item.get("finding"), dict) else item
        apk, method, spec = (found.get("apk"), found.get("method") or found.get("method_name"),
                             found.get("spec") or found.get("specification"))
        if apk and method and spec:
            keys[key] = (apk, method, spec)
    return keys


def side_calls(run: Path, keys34: set, by_apk_method_spec: dict, prices) -> dict:
    """Calls usage_by_finding leaves out: hex-keyed (mapped to the 34 or not) and unattributed."""
    mapping = hex_key_map(run)
    result = {"hex_mapped": {}, "hex_unmapped_calls": 0, "hex_unmapped_cost": 0.0,
              "no_finding_calls": 0, "no_finding_cost": 0.0, "no_finding_stages": {}}
    for path, record in cost._trace_items(cost.run_log_dir(run)):
        if (record.get("delivery") not in cost.PAID_DELIVERIES
                or record.get("status") in {"prepared", "cache_hit"}):
            continue
        fid = str(record.get("finding_id", ""))
        parts = fid.split(":::")
        if len(parts) >= 3 and all(parts[:3]):
            continue
        value = cost.trace_call_cost(path, record, prices) or 0.0
        if HEX_KEY.match(fid):
            target = by_apk_method_spec.get(mapping.get(fid))
            if target is None:
                result["hex_unmapped_calls"] += 1
                result["hex_unmapped_cost"] += value
            else:
                entry = result["hex_mapped"].setdefault(":::".join(target), {"calls": 0, "cost_usd": 0.0})
                entry["calls"] += 1
                entry["cost_usd"] += value
        else:
            stage = str(record.get("stage", "") or "unknown")
            result["no_finding_calls"] += 1
            result["no_finding_cost"] += value
            result["no_finding_stages"][stage] = result["no_finding_stages"].get(stage, 0) + 1
    return result


def rounded(value, places=4):
    return None if value is None else round(value, places)


def main():
    prices = cost.load_prices()
    records = snapshot_findings()
    keys34 = {(r["class"], r["method"], r["spec"]) for r in records}
    by_apk_method_spec = {(r["apk"], r["method"], r["spec"]): (r["class"], r["method"], r["spec"])
                          for r in records}

    scope6_start, _ = run_start(cost.run_log_dir(LAST_SNAPSHOT_RUN))
    window1_end = scope6_start

    runs = []
    for run in run_dirs():
        log_dir = cost.run_log_dir(run)
        start, source = run_start(log_dir)
        if start is None or start > WINDOW2_END:
            continue
        named = {str(rec.get("finding_id", "")) for _p, rec in cost._trace_items(log_dir)}
        if not any(tuple(n.split(":::")[:3]) in keys34 or HEX_KEY.match(n) for n in named):
            continue
        per_finding = {k: v for k, v in cost.usage_by_finding(log_dir, prices).items() if k in keys34}
        relative = run.relative_to(ROOT).as_posix()
        in_window1 = WINDOW1_FIRST_DAY <= start <= min(window1_end, WINDOW1_LAST_DAY_END)
        in_window2 = WINDOW1_FIRST_DAY <= start <= WINDOW2_END
        side = side_calls(run, keys34, by_apk_method_spec, prices) if in_window2 else None
        if not per_finding and not (side and side["hex_mapped"]):
            continue
        unpriced = [":::".join(k) for k, v in per_finding.items() if v["cost_usd"] is None]
        if unpriced:
            sys.exit(f"{relative}: unpriced calls for {unpriced}")
        runs.append({
            "run": relative, "start": start.isoformat(), "start_source": source,
            "window1": in_window1, "window2": in_window2,
            "calls": sum(v["calls"] for v in per_finding.values()),
            "cost_usd": sum(v["cost_usd"] for v in per_finding.values()),
            "findings": {":::".join(k): {"calls": v["calls"], "cost_usd": v["cost_usd"]}
                         for k, v in sorted(per_finding.items())},
            "side_calls": side,
        })
    runs.sort(key=lambda r: (r["start"], r["run"]))

    def totals(selected):
        per = {":::".join(k): {"calls": 0, "cost_usd": 0.0} for k in sorted(keys34)}
        for run in selected:
            for key, value in run["findings"].items():
                per[key]["calls"] += value["calls"]
                per[key]["cost_usd"] += value["cost_usd"]
        return per

    window1 = [r for r in runs if r["window1"] and r["calls"]]
    window2 = [r for r in runs if r["window2"] and r["calls"]]
    extra = [r for r in window2 if not r["window1"]]
    before = [r for r in runs if not r["window2"] and r["calls"]]
    w1, w2 = totals(window1), totals(window2)

    def key_of(record):
        return f'{record["class"]}:::{record["method"]}:::{record["spec"]}'

    def group(name, predicate, per):
        members = [r for r in records if predicate(r)]
        values = [per[key_of(r)]["cost_usd"] for r in members]
        return {"name": name, "count": len(members), "total_usd": sum(values),
                "mean_usd": (sum(values) / len(values)) if values else None,
                "findings": [key_of(r) for r in members]}

    def is_pr(r):
        return bool(r.get("pr_committed")) and str(r.get("build_status", "")).startswith("success") and r.get("pr_ready")

    total1 = sum(v["cost_usd"] for v in w1.values())
    calls1 = sum(v["calls"] for v in w1.values())
    total2 = sum(v["cost_usd"] for v in w2.values())
    pr = group("PR (committed draft, build success)", is_pr, w1)
    if sorted(pr["findings"]) != sorted(key_of(r) for r in records if r["outcome"] == OUTCOME_PR):
        sys.exit("committed-and-built drafts differ from the 'PR pronto' outcome")
    issue = group("issue proposal", lambda r: r["outcome"] == OUTCOME_ISSUE, w1)
    fp = group("recognised false positive", lambda r: r["outcome"] == OUTCOME_FP, w1)
    ie = group("insufficient evidence", lambda r: r["outcome"] == OUTCOME_IE, w1)
    bitbanana = group("BitBanana", lambda r: r["apk"].startswith("app.michaelwuensch.bitbanana"), w1)
    freeotp = [r for r in records if r["apk"].startswith("org.fedorahosted.freeotp") and r["spec"] == "CipherSpec"]
    if len(freeotp) != 1:
        sys.exit("expected one FreeOTP CipherSpec finding")
    freeotp_key = key_of(freeotp[0])

    values1 = sorted(v["cost_usd"] for v in w1.values())
    hex_w1 = {}
    for run in window1 + [r for r in runs if r["window1"] and not r["calls"]]:
        for key, value in (run["side_calls"] or {}).get("hex_mapped", {}).items():
            entry = hex_w1.setdefault(key, {"calls": 0, "cost_usd": 0.0})
            entry["calls"] += value["calls"]
            entry["cost_usd"] += value["cost_usd"]
    window1_hex_only = [r["run"] for r in runs if r["window1"] and not r["calls"]]

    result = {
        "generated_by": "scripts/rq5_cost_snapshot.py",
        "prices_file": str(cost.library_data(cost.PRICES_FILE)),
        "run_start": "earliest YYYYMMDDTHHMMSS prefix of the run's trace file names (local time)",
        "window1_end_start_of": LAST_SNAPSHOT_RUN.relative_to(ROOT).as_posix(),
        "window1_end": window1_end.isoformat(),
        "window2": "runs started 2026-10-19T00:00 .. 2026-11-03T23:59:59",
        "median_convention": "over the 34 findings, US$ 0 for a finding without calls; mean of the 17th and 18th values",
        "values": {
            "N": len(window1), "CALLS": calls1, "TOTAL": total1,
            "MED": statistics.median(values1),
            "PR": pr["total_usd"], "PR_share": pr["total_usd"] / total1,
            "ISS": issue["total_usd"], "ISS_share": issue["total_usd"] / total1,
            "FP": fp["mean_usd"], "IE": ie["mean_usd"], "BITBANANA": bitbanana["total_usd"],
            "K": len(extra), "ALL": total2, "FREEOTP": w2[freeotp_key]["cost_usd"],
            "CALLS_window2": sum(v["calls"] for v in w2.values()),
            "findings_without_calls_window1": sum(1 for v in w1.values() if not v["calls"]),
        },
        "groups_window1": [pr, issue, fp, ie, bitbanana],
        "runs_window1": [r["run"] for r in window1],
        "runs_window1_with_only_hex_keyed_calls": window1_hex_only,
        "runs_window2_extra": [r["run"] for r in extra],
        "before_window2": {"runs": [r["run"] for r in before],
                           "calls": sum(r["calls"] for r in before),
                           "cost_usd": sum(r["cost_usd"] for r in before)},
        "left_out_by_usage_by_finding": {
            "window1_hex_keyed_calls_mapped_to_the_34": hex_w1,
            "window1_no_finding_calls": sum((r["side_calls"] or {}).get("no_finding_calls", 0)
                                            for r in runs if r["window1"]),
            "window1_no_finding_cost_usd": sum((r["side_calls"] or {}).get("no_finding_cost", 0.0)
                                               for r in runs if r["window1"]),
        },
        "per_finding_window1": w1,
        "per_finding_window2": w2,
        "runs": runs,
    }
    RESULT.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=rounded) + "\n",
                      encoding="utf-8")
    v = result["values"]
    print(json.dumps({k: (round(x, 4) if isinstance(x, float) else x) for k, x in v.items()}, indent=1))
    print("window1:", *[f'{r["start"]} {r["run"]} calls={r["calls"]} ${r["cost_usd"]:.2f}' for r in window1], sep="\n  ")
    print("window1 hex-only:", window1_hex_only)
    print("window2 extra:", *[f'{r["start"]} {r["run"]} calls={r["calls"]} ${r["cost_usd"]:.2f}' for r in extra], sep="\n  ")
    print("before window2:", len(before), f'{result["before_window2"]["cost_usd"]:.2f}')
    print("left out:", json.dumps(result["left_out_by_usage_by_finding"], default=rounded))
    print("wrote", RESULT.relative_to(ROOT))


if __name__ == "__main__":
    main()
