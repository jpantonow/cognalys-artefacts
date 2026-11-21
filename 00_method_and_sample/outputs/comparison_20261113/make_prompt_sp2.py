"""Arm SP2 (2026-11-15): the "+" prompt without the written record of the stages.

Arm SP (Claude Code + claude-sonnet-5-5, prompt_plus.txt) was refused by Claude Code in 8 of its first 9 runs:
"Sonnet 5.5's safeguards flagged this message ... [reasoning_extraction]" (DESVIO_SP_20261115.md). The
prompt asks the agent to write every stage of its analysis into ANALYSIS.md; arms S (prompt.txt, no
such request) and TP (Codex, same prompt_plus.txt) were never refused. SP2 is SP with that request removed:
the ten stages, their rules and the deliverables are unchanged; only the six sentences that ask for the
record (or refer to it) are removed or changed so that no file of notes is asked for. The judges never
read the notes (run_one.py: the cards read PR.md / ISSUE.md / NO_ACTION.md and patch.diff only).

Writes units/<unit>/prompt_plus_sp2.txt and prompt_plus_sp2.sha256 (same format as prompt_plus.sha256),
and appends the SP2 runs to order_plus.json (SP's runs, in SP's order, arm renamed; the TP and SP
entries are not touched). Each replacement must match exactly once per unit, or nothing is written.
"""
import hashlib
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent
REPLACEMENTS = [
    ("Work through the ten stages below, in order. Record each stage in ANALYSIS.md at the repository root, "
     "one section per stage: Evidence, Research, Facts and decision, Plan, Drafts, Checks, Review, Redrafts, "
     "Build, Text. Write each section before you start the next stage. If you skip a step, say so and why. "
     "ANALYSIS.md is for you only. It is not one of the files described below, it is not sent to the "
     "maintainers, and what you write for them must not refer to it or copy its wording. ",
     "Work through the ten stages below, in order: Evidence, Research, Facts and decision, Plan, Drafts, "
     "Checks, Review, Redrafts, Build, Text. No record of the stages is needed; only the files described "
     "below are read. "),
    ("Such a policy in this repository is not a reason to stop either; note it in ANALYSIS.md.",
     "Such a policy in this repository is not a reason to stop either."),
    ("4.1. Write the plan in ANALYSIS.md:", "4.1. Make a plan:"),
    (" If your tools cannot delete a file, name it in ANALYSIS.md.", ""),
    ("8.9. Record each draft in ANALYSIS.md with its failures and their source (plan or draft).",
     "8.9. Keep track of each draft's failures and their source (plan or draft)."),
    (" Do not copy sentences from ANALYSIS.md.", ""),
]


def main():
    units = sorted(p.parent for p in OUT.glob("units/*/prompt_plus.txt"))
    out = {}
    for d in units:
        text = (d / "prompt_plus.txt").read_text(encoding="utf-8")
        for old, new in REPLACEMENTS:
            n = text.count(old)
            if n != 1:
                raise SystemExit(f"{d.name}: expected one match, found {n}: {old[:60]!r}")
            text = text.replace(old, new)
        if "ANALYSIS" in text:
            raise SystemExit(f"{d.name}: ANALYSIS still mentioned")
        out[d] = text
    lines = []
    for d, text in out.items():
        (d / "prompt_plus_sp2.txt").write_text(text, encoding="utf-8")
        lines.append(f"{hashlib.sha256(text.encode()).hexdigest()} *units/{d.name}/prompt_plus_sp2.txt\n")
    (OUT / "prompt_plus_sp2.sha256").write_text("".join(lines), encoding="utf-8")
    order_path = OUT / "order_plus.json"
    order = json.loads(order_path.read_text(encoding="utf-8"))
    if not any(r["arm"] == "SP2" for r in order["runs"]):
        sp2 = [dict(r, arm="SP2") for r in order["runs"] if r["arm"] == "SP"]
        order["runs"] += sp2
        order.setdefault("replicas", {})["SP2"] = order["replicas"]["SP"]
        order["sp2_note"] = "SP2 appended 2026-11-15 by make_prompt_sp2.py (SP's runs, SP's order)"
        order_path.write_text(json.dumps(order, indent=2) + "\n", encoding="utf-8")
    print(f"{len(out)} prompts written; SP2 runs in order_plus.json: "
          f"{sum(r['arm'] == 'SP2' for r in order['runs'])}")


if __name__ == "__main__":
    main()
