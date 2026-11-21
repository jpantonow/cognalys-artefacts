"""Phase 2 (port to the base tip), option A of 2026-11-16: fixes, per unit, the base tip the agents port to.

The tip is the one arm C ported to: the base_commit its k3 runs recorded (c_arm_k3, c_arm_k3_fixes). A unit
where C recorded none (it never reached the port) gets the tip of the branch C named, as of the start of
C's first k3 run of that unit (git rev-list -1 --before). Writes port_phase/tips.json and a blobless bare
clone per repository under WORK/tips (WSL disk) holding the pinned commit and the tip.
Run (WSL): python3 port_phase/prep_tips.py
"""
import collections
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import OUT, TCC, WORK, dump, load  # noqa: E402

FROZEN = TCC / "frozen_runs_20261113"
TIPS = WORK / "tips"
SHA = re.compile(r'"base_commit": *"([0-9a-f]{40})"')
BRANCH = re.compile(r'"base_branch": *"([^"]+)"')


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"git {' '.join(args)}: {r.stderr[-400:]}")
    return r.stdout.strip()


def c_records(unit_id):
    shas, branches, starts = collections.Counter(), collections.Counter(), []
    for root in ("c_arm_k3", "c_arm_k3_fixes"):
        base = FROZEN / root / unit_id
        if not base.is_dir():
            continue
        for f in base.glob("r*/**/*.json"):
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            shas.update(SHA.findall(text))
            branches.update(BRANCH.findall(text))
        for st in base.glob("r*/run_status.json"):
            starts.append(st.stat().st_mtime)
    timings = sorted(FROZEN.glob(f"c_arm_k3/{unit_id}/r1/step_timings.json"))
    first = None
    if timings:
        # When C cloned the repository in its first k3 run of the unit: the tip it would have ported to.
        steps = load(timings[0]).get("steps") or []
        first = next((s.get("finished_at") for s in steps if s.get("step") == "cloning"), None)
    return shas, branches, first, starts


def main():
    out = {"rule": __doc__.strip().splitlines()[0:4], "units": {}}
    for udir in sorted((OUT / "units").iterdir()):
        unit = load(udir / "unit.json")
        uid = unit["unit"]
        shas, branches, first, starts = c_records(uid)
        if len(shas) > 1:
            raise SystemExit(f"{uid}: C recorded more than one base_commit: {dict(shas)}")
        branch = branches.most_common(1)[0][0] if branches else None
        if not branch:
            raise SystemExit(f"{uid}: C named no base branch")
        owner_repo = unit["repo_url"].rstrip("/").split("github.com/")[-1]
        bare = TIPS / (owner_repo.replace("/", "__") + ".git")
        if not bare.is_dir():
            git("clone", "-q", "--bare", "--filter=blob:none", unit["repo_url"], str(bare))
        git("fetch", "-q", "origin", f"+refs/heads/{branch}:refs/heads/{branch}", cwd=bare)
        git("fetch", "-q", "origin", unit["pinned_commit"], cwd=bare, check=False)
        if git("cat-file", "-t", unit["pinned_commit"], cwd=bare, check=False) != "commit":
            raise SystemExit(f"{uid}: pinned commit not fetchable")
        if shas:
            tip, source = next(iter(shas)), "C k3 base_commit"
            when = None
        else:
            when = first or (datetime.fromtimestamp(min(starts), timezone.utc).isoformat() if starts else None)
            if not when:
                raise SystemExit(f"{uid}: no C run date")
            tip = git("rev-list", "-1", f"--before={when}", branch, cwd=bare)
            source = f"tip of {branch} at C's first k3 run of the unit"
        git("fetch", "-q", "origin", tip, cwd=bare, check=False)
        if git("cat-file", "-t", tip, cwd=bare, check=False) != "commit":
            raise SystemExit(f"{uid}: tip {tip} not in the clone")
        moved = git("rev-list", "--count", f"{unit['pinned_commit']}..{tip}", cwd=bare, check=False)
        out["units"][uid] = {"repo": owner_repo, "branch": branch, "pinned_commit": unit["pinned_commit"],
                             "tip": tip, "source": source, "as_of": when, "commits_after_pin": moved,
                             "bare": str(bare)}
        print(uid, branch, tip[:10], source, "commits after pin:", moved, flush=True)
    dump(HERE / "tips.json", out)


if __name__ == "__main__":
    main()
