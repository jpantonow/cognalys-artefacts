"""Go through a run's corrections one at a time: `cognalys review RUN`.

What publishing one correction took before this: open its preview, read the
patch in the worktree, find its finding id in the PR JSON, run the publish
command with that id. Here each correction is shown with its decision, build
result and explanation, and one key says what to do with it. Every answer is
written to <run>/review_decisions.json, so a rejection and its reason are
kept for the next run and for the statistics.

Publishing still goes through pr_creator with this finding's explicit
approval, and asks for a typed confirmation: nothing reaches GitHub on a
single keystroke.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from cognalys.corrections import Correction, apply, corrections, patch

DECISIONS_FILE = "review_decisions.json"
EXPLANATION_LINES = 14
MENU = "(d)iff  (a)pply to your checkout  (p)ublish PR  (r)eject  (s)kip  (q)uit"


def load_decisions(run_dir: Path) -> dict:
    path = Path(run_dir) / DECISIONS_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(run_dir: Path, correction: Correction, decision: str, note: str = "") -> None:
    decisions = load_decisions(run_dir)
    decisions[correction.finding_id] = {
        "apk": correction.apk, "method": correction.method, "spec": correction.spec,
        "decision": decision, "note": note,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (Path(run_dir) / DECISIONS_FILE).write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _summary(index: int, total: int, correction: Correction, previous: dict) -> str:
    seen = f"  (already {previous['decision']})" if previous else ""
    lines = [
        "",
        "=" * 78,
        f"[{index}/{total}] {correction.apk}  {correction.method}  [{correction.spec}]{seen}",
        f"{correction.level} · build {correction.build_status} · {correction.decision} · "
        f"id {correction.finding_id}",
    ]
    if correction.files:
        lines.append("files: " + ", ".join(correction.files))
    explanation = correction.explanation.strip().splitlines()
    if explanation:
        lines.append("-" * 78)
        lines += explanation[:EXPLANATION_LINES]
        if len(explanation) > EXPLANATION_LINES:
            lines.append(f"... ({len(explanation) - EXPLANATION_LINES} more lines in the preview)")
    return "\n".join(lines)


def review(run_dir: Path, repo: Path | None,
           publish: Callable[[str, bool], int],
           ask: Callable[[str], str] = input,
           say: Callable[[str], None] = print,
           only_pending: bool = False) -> dict:
    """Walk the corrections; returns {finding_id: decision} for this session.

    ``publish(finding_id, dry_run)`` opens the pull request (the CLI passes
    pr_creator with the finding approved); ``repo`` is where (a)pply writes.
    """
    raw_ask = ask

    def ask(prompt: str) -> str:
        # End of input (Ctrl-D, or a pipe that ran out) ends the review.
        try:
            return raw_ask(prompt)
        except EOFError:
            return "q"

    found = corrections(run_dir)
    decisions = load_decisions(run_dir)
    if only_pending:
        found = [item for item in found if item.finding_id not in decisions]
    if not found:
        say("No corrections to review in this run.")
        return {}
    session = {}
    for index, correction in enumerate(found, 1):
        say(_summary(index, len(found), correction, decisions.get(correction.finding_id, {})))
        while True:
            choice = ask(f"{MENU} > ").strip().lower()[:1]
            if choice == "d":
                try:
                    say(patch(correction))
                except (OSError, FileNotFoundError) as exc:
                    say(f"cannot show the patch: {exc}")
                continue
            if choice == "a":
                if repo is None:
                    say("give --repo PATH to apply corrections to your checkout")
                    continue
                ok, message = apply(correction, repo)
                say(message)
                if ok:
                    record(run_dir, correction, "applied", str(repo))
                    session[correction.finding_id] = "applied"
                    break
                continue
            if choice == "p":
                say("Dry run first:")
                publish(correction.finding_id, True)
                if ask("Type 'publish' to open this pull request on GitHub: ").strip() != "publish":
                    say("not published")
                    continue
                code = publish(correction.finding_id, False)
                if code == 0:
                    record(run_dir, correction, "published")
                    session[correction.finding_id] = "published"
                    break
                say(f"publication failed (exit {code}); see the output above")
                continue
            if choice == "r":
                reason = ask("Why? (recorded for the next run) ").strip()
                record(run_dir, correction, "rejected", reason)
                session[correction.finding_id] = "rejected"
                break
            if choice == "s":
                session[correction.finding_id] = "skipped"
                break
            if choice == "q":
                return session
            say("choose one of: d a p r s q")
    return session
