"""Phase 2: one local repository per unit at its tip (port_phase/tips.json), the source of every port run's
checkout. Blobless clone from GitHub, checked out (detached) at the tip, the blobs of every file that changed
between the pinned commit and the tip fetched (so `git diff <pinned> HEAD` and `git show <pinned>:<file>` work
offline), then the remote removed and the clone made self-contained. WORK/tip_repos/<unit>.
Run (WSL): python3 port_phase/prep_tip_repos.py [unit ...]
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import WORK, load  # noqa: E402

DEST = WORK / "tip_repos"


def git(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"git {' '.join(args)} ({cwd}): {r.stderr[-500:]}")
    return r.stdout


def main():
    tips = load(HERE / "tips.json")["units"]
    only = sys.argv[1:]
    DEST.mkdir(parents=True, exist_ok=True)
    for uid, t in tips.items():
        if only and uid not in only:
            continue
        repo = DEST / uid
        if (repo / ".ready").is_file():
            print(uid, "ready"); continue
        subprocess.run(["rm", "-rf", str(repo)])
        git("clone", "-q", "--filter=blob:none", "--no-checkout", f"https://github.com/{t['repo']}.git", str(repo))
        git("fetch", "-q", "origin", t["pinned_commit"], t["tip"], cwd=repo)
        git("checkout", "-q", "--detach", t["tip"], cwd=repo)
        # Hydrate the pinned side of every changed file, then every remaining promised object at both commits.
        git("diff", "--quiet", t["pinned_commit"], t["tip"], cwd=repo) if False else None
        subprocess.run(["git", "diff", "--stat", t["pinned_commit"], t["tip"]], cwd=repo, capture_output=True)
        subprocess.run(["git", "archive", "-o", "/dev/null", t["pinned_commit"]], cwd=repo, capture_output=True)
        # Self-contained: fetch nothing more later, no remote, no promisor.
        git("repack", "-a", "-d", "-q", cwd=repo)
        git("remote", "remove", "origin", cwd=repo)
        git("config", "--unset-all", "remote.origin.promisor", cwd=repo) if False else None
        subprocess.run(["git", "config", "--remove-section", "extensions"], cwd=repo, capture_output=True)
        subprocess.run(["git", "config", "--unset", "core.repositoryformatversion"], cwd=repo, capture_output=True)
        # Check: both trees readable offline.
        for rev in (t["pinned_commit"], t["tip"]):
            r = subprocess.run(["git", "archive", "-o", "/dev/null", rev], cwd=repo, capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f"{uid}: {rev[:10]} not readable offline: {r.stderr[-300:]}")
        (repo / ".ready").write_text(t["tip"] + "\n")
        print(uid, "ok", t["tip"][:10], flush=True)


if __name__ == "__main__":
    main()
