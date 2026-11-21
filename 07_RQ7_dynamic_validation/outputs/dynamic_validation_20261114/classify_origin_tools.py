"""Classify the FreeOTP cases rerun with the original report's explorers (runs_origin_tools/, declared deviation of
2026-11-17) with classify.py's functions unchanged, the same rule as classify_prs.py. Writes results_origin_tools.json."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import classify  # noqa: E402
import classify_prs  # noqa: E402

resolved = {c["case"]: c for c in json.loads((HERE / "pr_cases_resolved.json").read_text(encoding="utf-8"))["cases"]}
out = {}
for folder in sorted((HERE / "runs_origin_tools").iterdir()):
    name = folder.name
    rc = resolved[name]
    config = classify_prs.case_config(rc)
    reps = sorted({int(m.group(1)) for c in folder.iterdir() if (m := re.fullmatch(r"(?:base|patched)_r(\d+)", c.name))})
    runs = {f"{v}_r{r}": classify.read_run(folder / f"{v}_r{r}", config, v) for r in reps for v in classify.VARIANTS}
    classify.REPS = tuple(reps)
    summary = classify.classify(config, runs)
    out[name] = {"reps": reps, "summary": summary, "cells": {k: classify.cell(r) for k, r in runs.items()},
                 "site_reports": {k: r.get("messages", [])[:3] for k, r in runs.items()}}
    print(name, summary["result"], {k: classify.cell(r) for k, r in sorted(runs.items())})
(HERE / "results_origin_tools.json").write_text(json.dumps(out, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
