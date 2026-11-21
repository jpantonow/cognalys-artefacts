"""Read-only refresh of the RVSec issue survey (outputs/prs_reports/<date>/issues_summary.json).

Takes the issue list of an earlier survey (its url, kind, origin and title are
kept) and asks the GitHub API, for each issue, for its state, who closed it and
whether anyone other than the author or a bot commented. Writes the same
fields as the survey of 2026-11-08, so the paper's counts read either one.

    python scripts/levantar_issues.py --from 20261108 --date 20261113
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from levantar_prs import TOKEN  # noqa: E402

AUTHOR = "jpantonow"
REPORTS = ROOT / "outputs/prs_reports"


def get(url: str):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "tcc-survey",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def human_other(login: str) -> bool:
    return bool(login) and login.lower() != AUTHOR and not login.endswith("[bot]")


def refresh(item: dict) -> dict:
    owner_repo, number = item["url"].split("github.com/", 1)[1].split("/issues/")
    api = f"https://api.github.com/repos/{owner_repo}/issues/{number}"
    issue = get(api)
    comments = get(api + "/comments?per_page=100")
    closed_by = (issue.get("closed_by") or {}).get("login", "") if issue.get("state") == "closed" else ""
    owner = owner_repo.split("/")[0]
    replied = any(human_other((c.get("user") or {}).get("login", "")) for c in comments)
    return {**item, "state": issue["state"], "closed_by": closed_by,
            # A closure by anyone but the author or a bot is the maintainers' decision.
            "closed_by_maintainer": human_other(closed_by),
            "maintainer_replied": replied or human_other(closed_by),
            "owner": owner}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    earlier = json.loads((REPORTS / args.source / "issues_summary.json").read_text(encoding="utf-8"))
    rows = [refresh(item) for item in earlier["issues"]]
    for row in rows:
        row.pop("owner", None)
    out = REPORTS / args.date / "issues_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    date = f"{args.date[:4]}-{args.date[4:6]}-{args.date[6:]}"
    out.write_text(json.dumps({**{k: v for k, v in earlier.items() if k != "issues"},
                               "collected": date, "issues": rows}, indent=1, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    before = {i["url"]: i for i in earlier["issues"]}
    for row in rows:
        old = before[row["url"]]
        changed = {k: (old.get(k), row[k]) for k in ("state", "closed_by", "closed_by_maintainer", "maintainer_replied")
                   if old.get(k) != row[k]}
        if changed:
            print(row["url"], changed)
    print(out, len(rows), "issues")


if __name__ == "__main__":
    main()
