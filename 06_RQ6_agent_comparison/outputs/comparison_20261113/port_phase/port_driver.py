"""Runs phase 2 (run_port.py) for every phase-1 PR run of the given arms, one at a time. Resumable.

Order: unit, then arm, then replica (sorted). Waits while a Cognalys run or an RVSec queue is active (one lane);
stops when C: has less than MIN_FREE_GB free or a STOP file exists in port_phase/. Exit 75 of run_port: that
arm pauses for retry_after; 76: that arm stops; 77: retried twice. Env: ARMS_ONLY (default TP,SP2,OP).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import RECORDS, VENV_PY, load  # noqa: E402
from driver import other_runs  # noqa: E402

ARMS_ONLY = [a for a in os.environ.get("ARMS_ONLY", "TP,SP2,OP").split(",") if a]
MIN_FREE_GB = float(os.environ.get("MIN_FREE_GB", 8))


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%S%z"), msg, flush=True)


def pending():
    out = []
    for res in sorted(RECORDS.glob("*/*_r*/result.json")):
        arm, rep = res.parent.name.rsplit("_r", 1)
        if arm not in ARMS_ONLY or (res.parent / "port" / "result.json").is_file():
            continue
        if load(res).get("delivery_class") == "PR":
            out.append((res.parent.parent.name, arm, int(rep)))
    return out


def main():
    paused, disabled, infra = {}, set(), {}
    while True:
        if (HERE / "STOP").exists():
            log("STOP file: ending"); return 0
        if shutil.disk_usage("/mnt/c").free / 1e9 < MIN_FREE_GB:
            log("C: below MIN_FREE_GB: stopping"); return 3
        if other_runs():
            log("another run is active; waiting 120 s"); time.sleep(120); continue
        now = time.time()
        todo = [p for p in pending() if p[1] not in disabled and paused.get(p[1], 0) <= now
                and infra.get(p, 0) < 3]
        if not todo:
            waiting = [t for a, t in paused.items() if t > now and a not in disabled]
            if waiting and any(p[1] in paused for p in pending()):
                time.sleep(max(60, min(waiting) - now)); continue
            log(f"done: {len(pending())} pending left (disabled {sorted(disabled)})"); return 0
        unit, arm, rep = todo[0]
        log(f"port {unit} {arm} r{rep}")
        p = subprocess.run([str(VENV_PY), str(HERE / "run_port.py"), unit, arm, str(rep)],
                           capture_output=True, text=True)
        log(f"  exit {p.returncode}: {(p.stdout or '').strip()[-400:]} {(p.stderr or '').strip()[-400:]}")
        if p.returncode == 75:
            try:
                wait = json.loads(p.stdout.strip().splitlines()[-1]).get("retry_after", 1800)
            except (ValueError, IndexError):
                wait = 1800
            paused[arm] = time.time() + wait
        elif p.returncode == 76:
            disabled.add(arm)
        elif p.returncode != 0:
            infra[(unit, arm, rep)] = infra.get((unit, arm, rep), 0) + 1


if __name__ == "__main__":
    sys.exit(main())
