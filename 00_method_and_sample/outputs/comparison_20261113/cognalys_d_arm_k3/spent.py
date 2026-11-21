"""US$ spent by the Cognalys arm (cognalys.llm.cost.usage over each run's prompt logs).

No argument: the sum over every run folder in runs.json. One argument (a run folder): that run alone.
"""
import json
import sys
from pathlib import Path

from cognalys.llm.cost import run_log_dir, usage

H = Path("/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113/cognalys_d_arm_k3")
runs = sys.argv[1:] or [r["run_dir"] for r in json.loads((H / "runs.json").read_text())["runs"]]
total = 0.0
for run in runs:
    logs = run_log_dir(Path(run))
    if logs.is_dir():
        total += usage(logs)["cost_usd"] or 0.0
print(f"{total:.4f}")
