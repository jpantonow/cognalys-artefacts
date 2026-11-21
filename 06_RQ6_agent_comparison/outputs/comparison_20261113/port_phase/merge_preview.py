"""Preview of phase 2's deterministic step only (no agent, no build): does each phase-1 PR of the given arms
apply on the unit's tip with `git apply --3way`? Writes port_phase/merge_preview.json. Read-only for the runs.
Run (WSL): ARMS_ONLY=TP,SP2 python3 port_phase/merge_preview.py
"""
import collections
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import RECORDS, WORK, dump, load  # noqa: E402

ARMS_ONLY = os.environ.get("ARMS_ONLY", "TP,SP2").split(",")


def main():
    tips = load(HERE / "tips.json")["units"]
    tmp = WORK / "port_preview"
    out, count = [], collections.Counter()
    for res in sorted(RECORDS.glob("*/*_r*/result.json")):
        arm, rep = res.parent.name.rsplit("_r", 1)
        if arm not in ARMS_ONLY or load(res).get("delivery_class") != "PR":
            continue
        unit = res.parent.parent.name
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(WORK / "tip_repos" / unit), str(tmp)],
                       check=True)
        subprocess.run(["git", "-C", str(tmp), "checkout", "-q", "--detach", tips[unit]["tip"]], check=True)
        r = subprocess.run(["git", "-C", str(tmp), "apply", "--3way", "--whitespace=nowarn",
                            str(res.parent / "patch.diff")], capture_output=True, text=True)
        conflicted = subprocess.run(["git", "-C", str(tmp), "diff", "--name-only", "--diff-filter=U"],
                                    capture_output=True, text=True).stdout.strip()
        ok = r.returncode == 0 and not conflicted
        count[(arm, ok)] += 1
        out.append({"unit": unit, "arm": arm, "replica": int(rep), "applies": ok,
                    "detail": "" if ok else (r.stderr.strip()[-200:] or conflicted)})
    shutil.rmtree(tmp, ignore_errors=True)
    dump(HERE / "merge_preview.json", {"rows": out})
    for (arm, ok), n in sorted(count.items()):
        print(arm, "applies" if ok else "needs the agent", n)
    for row in out:
        if not row["applies"]:
            print(" ", row["unit"], row["arm"], row["replica"], row["detail"][:120].replace("\n", " "))


if __name__ == "__main__":
    main()
