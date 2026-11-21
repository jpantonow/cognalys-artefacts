# RERUN k3 at fd90351: same units, settings and leak controls as the 8be9f78 arm, after the fixes found by the comparison (declared as a post-fix rerun).
"""Builds the Cognalys arm (C) of the 2026-11-13 comparison: 20 units x 4 replicas.

Reads located.json (locate_rows.py): for each unit, the original RVSec rows of its finding ids, at
the unit's pinned commit. Writes, next to this script:
  findings/<unit>.<csv|jsonl>   the unit's rows only, copied unchanged from the source file
  tomls/<unit>_r<k>.toml        one cognalys run per replica (same settings as the frozen PR run,
                                outputs/frozen_pr_20261113/tomls, except the run directory)
  runs.json                     unit, replica, toml, run_dir, max_cost, source of every row
  queue.txt                     replica-major order (every unit's r1, then r2, ...) so that a
                                partial run still has whole replicas
Run directories sit under frozen_runs_20261113/c_arm/ (outside outputs/, which holds our published
pull requests: under it the pipeline would see them).
"""
import csv
import json
from pathlib import Path

TCC = Path("/mnt/c/Users/Antonow/Documents/tcc_v3")
HERE = TCC / "outputs/comparison_20261113/cognalys_arm_k3"
UNITS = TCC / "outputs/comparison_20261113/units"
# c_arm/ holds 10 runs at 66fb85f, discarded: the arm restarted at d7f414c (2026-11-13) so that it
# measures the pipeline with every fix made before any of its results was read; c_arm_d7f414c/
# holds the runs at d7f414c, discarded when it restarted again at 8be9f78 (Bookwyrm, predictable generators).
RUNS_ROOT = TCC / "frozen_runs_20261113/c_arm_k3"
WORK = "/home/antonow/cognalys_work/c_arm_k3"
REPLICAS = 4
MAX_COST = 2.00

TOML = """# Cognalys arm (C) of the agent comparison of 2026-11-13, unit {unit}, replica {k}.
# APK {apk} at {commit}; findings {fids}.
# Built by outputs/comparison_20261113/cognalys_arm_k3/build_c_arm.py; run with run_c_arm.sh.
findings = ["{findings}"]
run-dir = "{run_dir}"

llm-provider = "openai"
llm-model = "gpt-6.1-terra"
llm-endpoint = "https://api.openai.com/v1"
llm-service-tier = "flex"
llm-reasoning-effort = "high"
spec-model = "gpt-6.1-terra"
llm-max-output-tokens = 32768
llm-timeout = 600
max-cost = {max_cost:.2f}

validate-build = true
approve-review-only-for-validation = true
generate-previews = true
online-dependency-resolution = true
build-timeout = 3600
cloned-projects-dir = "{work}/clones"
worktrees-dir = "{work}/worktrees"

# Leak controls and freezing (as in the frozen PR run).
holdout-repository = true
learning-dir = "/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/frozen_pr_20261113/learning"
no-llm-cache = true
llm-research-corpus-dir = "outputs/research_corpus"
experiment-manifest = "{manifest}"
experiment-live-research = true
experiment-condition = "comparison_c_arm_k3"
"""

located = json.loads((HERE / "located.json").read_text())
(HERE / "findings").mkdir(exist_ok=True)
(HERE / "tomls").mkdir(exist_ok=True)
(HERE / "manifests").mkdir(exist_ok=True)
runs = []
for unit_dir in sorted(UNITS.iterdir()):
    unit = json.loads((unit_dir / "unit.json").read_text())
    name = unit["unit"]
    hits = located[name]
    missing = [f for f, h in hits.items() if h is None]
    if missing:
        raise SystemExit(f"{name}: no source row for {missing}")
    sources = {h["path"] for h in hits.values()}
    suffixes = {Path(p).suffix for p in sources}
    if len(suffixes) != 1:
        raise SystemExit(f"{name}: rows come from files of different formats {sources}")
    suffix = suffixes.pop()
    rows = [h["row"] for h in hits.values()]
    commits = {str(r.get("commit")) for r in rows}
    if commits != {unit["pinned_commit"]}:
        raise SystemExit(f"{name}: row commits {commits} != pinned {unit['pinned_commit']}")
    path = HERE / "findings" / f"{name}{suffix}"
    if suffix == ".jsonl":
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    else:
        header = max((h["header"] for h in hits.values()), key=len)
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    for k in range(1, REPLICAS + 1):
        slug = f"{name}_r{k}"
        run_dir = RUNS_ROOT / name / f"r{k}"
        toml = HERE / "tomls" / f"{slug}.toml"
        toml.write_text(TOML.format(
            unit=name, k=k, apk=unit["apk"], commit=unit["pinned_commit"],
            fids=", ".join(hits), findings=path, run_dir=run_dir, max_cost=MAX_COST,
            work=f"{WORK}/{slug}", manifest=HERE / "manifests" / f"{slug}.json"), encoding="utf-8")
        runs.append({"slug": slug, "unit": name, "replica": k, "toml": str(toml), "run_dir": str(run_dir),
                     "work": f"{WORK}/{slug}", "max_cost": MAX_COST, "findings_file": str(path),
                     "row_sources": sorted(sources), "finding_ids": list(hits)})

runs.sort(key=lambda r: (r["replica"], r["unit"]))
(HERE / "runs.json").write_text(json.dumps({"replicas": REPLICAS, "runs": runs}, indent=1))
(HERE / "queue.txt").write_text("".join(r["slug"] + "\n" for r in runs))
print(f"{len(runs)} runs; findings files: {len(list((HERE / 'findings').iterdir()))}")
