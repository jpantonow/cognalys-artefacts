#!/usr/bin/env python3
"""Export a Markdown inventory by PR status without deleting repositories."""
from cognalys.workspace import workspace_root
from pathlib import Path
import argparse
import sys

ROOT = workspace_root()
from cognalys.maintenance.repository_pruning import prune  # noqa: E402
from cognalys.utils.repository_lock import RepositoryUsageLock, RepositoryBusyError  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("report", "preview", "apply"), default="report",
                        help="Report only; preview/apply are legacy aliases and never delete directories.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cloned-projects-dir", type=Path, default=Path("outputs/cloned_projects"))
    parser.add_argument("--worktrees-dir", type=Path, default=Path("outputs/finding_worktrees"))
    parser.add_argument("--outputs-root", type=Path, default=Path("outputs"))
    parser.add_argument("--project", default="")
    parser.add_argument("--estimate-size", action="store_true",
                        help="Also walk clone/build files to estimate disk space (slow on WSL /mnt/c).")
    args = parser.parse_args(argv)
    try:
        with RepositoryUsageLock(ROOT):
            code, _ = prune(
                ROOT, ROOT / args.cloned_projects_dir, ROOT / args.worktrees_dir,
                ROOT / args.outputs_root, args.mode,
                args.project, estimate_size=args.estimate_size,
            )
            return code
    except (RepositoryBusyError, OSError) as exc:
        print(f"Report stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
