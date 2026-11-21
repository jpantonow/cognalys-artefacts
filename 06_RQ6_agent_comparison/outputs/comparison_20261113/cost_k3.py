"""Cost per arm for the k3 comparison as judged: SP2 instead of SP, C with its 4 FALHA runs swapped
for the post-fix reruns (same set as judges/cards.py C_REPLACED). Wraps analyze.py without editing it."""
import json, os, sys
from pathlib import Path
os.environ["ARMS_TAG"] = "k3"
OUT = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113")
sys.path.insert(0, str(OUT))
import analyze as an

an.ARMS = ("S", "O", "T", "TP", "SP2", "C", "D")
an.MODELS["SP2"] = "claude-sonnet-5-5 (+ prompt sp2)"
REPL = {("pr_fff072cb8953", 1), ("pr_fff072cb8953", 3), ("co_f637c4b8467e", 3), ("co_0a32f516feb2", 2)}
ROOT = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/frozen_runs_20261113/c_arm_k3_fixes")
scratch = OUT / "cognalys_arm_k3_swapped"  # runs.json of C as judged (C_REPLACED swapped)
scratch.mkdir(exist_ok=True)
runs = json.loads((OUT / "cognalys_arm_k3/runs.json").read_text())
n = 0
for r in runs["runs"]:
    if (r["unit"], int(r["replica"])) in REPL:
        r["run_dir"] = str(ROOT / r["unit"] / f"r{int(r['replica'])}")
        n += 1
assert n == 4, n
(scratch / "runs.json").write_text(json.dumps(runs))
an.COGNALYS_ARMS["C"] = str(scratch)

rows = an.agent_rows() + [r for arm in an.COGNALYS_ARMS for r in an.c_rows(arm)]
s = an.summary(rows)
res = {}
for arm, v in s.items():
    res[arm] = {k: v.get(k) for k in ("model", "runs", "cost_usd_priced_runs", "cost_usd_total", "cost_usd_mean_per_run",
                                      "cost_usd_api_total", "cost_usd_api_mean_per_run", "cost_usd_list_mean_per_run",
                                      "cost_usd_flex_mean_per_run", "median_seconds", "median_diff_lines_pr")}
    print(arm, json.dumps(res[arm]))
(OUT / "cost_k3.json").write_text(json.dumps(res, indent=1))
