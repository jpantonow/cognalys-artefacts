"""Post-fix rerun of the Cognalys arm (C, k3 at fd90351) for the three units whose C runs failed.

Declared as a separate, post-hoc measurement: it never replaces arm C in the comparison. The four
C failures (2026-11-16 diagnosis) came from three pipeline defects, fixed after the arms:
  - pr_fff072cb8953 r1, r3 (NotallyX md5Hash): generated tests failed inside Robolectric's own
    frame, so failing_test_diagnostics recorded nothing and the repair step never ran;
  - co_f637c4b8467e r3 (winterhold derive): a review-only decision with publication open was
    refused by demote_incomplete_scope_to_review, so the scope check stopped it (scope_gap);
  - co_0a32f516feb2 r2 (org.cry.otp hmac_sha): reported_value_untouched blocked a patch that fixed
    the joint SecretKeySpecSpec finding.
Every replica of the three units is rerun (12 runs), not only the four that failed, so the
measurement is per unit. Same settings, findings files and leak controls as arm C; only the run
directory, the work directory, the manifest and the condition name differ.
"""
import json
from pathlib import Path

TCC = Path("/mnt/c/Users/Antonow/Documents/tcc_v3")
SRC = TCC / "outputs/comparison_20261113/cognalys_arm_k3"
HERE = TCC / "outputs/comparison_20261113/cognalys_arm_k3_fixes"
RUNS_ROOT = TCC / "frozen_runs_20261113/c_arm_k3_fixes"
WORK = "/home/antonow/cognalys_work/c_arm_k3_fixes"
UNITS = ("co_0a32f516feb2", "co_f637c4b8467e", "pr_fff072cb8953")

(HERE / "tomls").mkdir(parents=True, exist_ok=True)
(HERE / "manifests").mkdir(exist_ok=True)
runs = []
for run in json.loads((SRC / "runs.json").read_text())["runs"]:
    if run["unit"] not in UNITS:
        continue
    slug = run["slug"]
    text = Path(run["toml"]).read_text(encoding="utf-8")
    replacements = {
        f'run-dir = "{run["run_dir"]}"': f'run-dir = "{RUNS_ROOT / run["unit"] / ("r%d" % run["replica"])}"',
        f'"{run["work"]}/clones"': f'"{WORK}/{slug}/clones"',
        f'"{run["work"]}/worktrees"': f'"{WORK}/{slug}/worktrees"',
        f'"{SRC}/manifests/{slug}.json"': f'"{HERE}/manifests/{slug}.json"',
        'experiment-condition = "comparison_c_arm_k3"': 'experiment-condition = "comparison_c_arm_k3_fixes"',
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise SystemExit(f"{slug}: expected one {old!r}")
        text = text.replace(old, new)
    text = text.replace("# Cognalys arm (C)", "# Post-fix rerun of the Cognalys arm (C)", 1)
    toml = HERE / "tomls" / f"{slug}.toml"
    toml.write_text(text, encoding="utf-8")
    runs.append({**run, "toml": str(toml),
                 "run_dir": str(RUNS_ROOT / run["unit"] / f"r{run['replica']}"),
                 "work": f"{WORK}/{slug}"})
if len(runs) != 12:
    raise SystemExit(f"expected 12 runs, got {len(runs)}")
runs.sort(key=lambda r: (r["replica"], r["unit"]))
(HERE / "runs.json").write_text(json.dumps({"replicas": 4, "runs": runs}, indent=1))
(HERE / "queue.txt").write_text("".join(r["slug"] + "\n" for r in runs))
print(f"{len(runs)} runs written to {HERE}")
