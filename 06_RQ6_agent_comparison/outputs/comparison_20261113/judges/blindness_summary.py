#!/usr/bin/env python3
"""Per arm, how many probed cards the blindness judge attributed to the right arm (2026-11-14).

Reads a blindness directory written by judge.py (blindness/, blindness<suffix>/ or blindness<suffix>_patch/):
its sample.json and one <card>.json per probed card, and the arms from <cards dir>/key.json. Reports, per arm,
the cards drawn, the answers, the answers naming the right arm (share), the answers naming the right family
(C/D, the two pipeline arms, against S/O/T, the three agents), the mean confidence and the guesses. No
threshold is applied: the share is reported as measured. A card whose output was recorded for another
version of the card (card_sha256) is counted apart as stale.

Usage (WSL, venv):
  python outputs/comparison_20261113/judges/blindness_summary.py [blindness_dir] [--cards cards_k3] [--json out.json]
blindness_dir and --cards are relative to this directory (defaults: blindness, $CARDS_DIR or cards).
"""
import argparse
import collections
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ("S", "O", "T", "C", "D")
FAMILY = {"S": "agent", "O": "agent", "T": "agent", "C": "pipeline", "D": "pipeline"}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def summarise(bdir: Path, cards_dir: Path) -> dict:
    key = load(cards_dir / "key.json")["cards"]
    shas = {c["card_id"]: c["sha256"] for c in load(cards_dir / "cards.json")["cards"]}
    sample = load(bdir / "sample.json")["cards"] if (bdir / "sample.json").is_file() else \
        sorted(p.stem for p in bdir.glob("K*.json") if not p.name.endswith(".failed.json"))
    rows = {a: {"drawn": 0, "answered": 0, "right_arm": 0, "right_family": 0, "stale": 0, "failed": 0,
                "confidence": [], "guesses": collections.Counter()} for a in ARMS}
    unknown = []
    for cid in sample:
        if cid not in key:
            unknown.append(cid)
            continue
        arm = key[cid]["arm"]
        r = rows[arm]
        r["drawn"] += 1
        out = bdir / f"{cid}.json"
        if not out.is_file():
            r["failed"] += (bdir / f"{cid}.failed.json").is_file()
            continue
        rec = load(out)
        if rec.get("card_sha256") != shas.get(cid):
            r["stale"] += 1
            continue
        guess = rec["output"]["guess"]
        r["answered"] += 1
        r["right_arm"] += guess == arm
        r["right_family"] += FAMILY[guess] == FAMILY[arm]
        r["confidence"].append(rec["output"].get("confidence", 0))
        r["guesses"][guess] += 1
    result = {"blindness_dir": str(bdir.relative_to(HERE)), "cards_dir": str(cards_dir.relative_to(HERE)),
              "unknown_card_ids": unknown, "arms": {}}
    for arm, r in rows.items():
        n = r["answered"]
        result["arms"][arm] = {
            "drawn": r["drawn"], "answered": n, "right_arm": r["right_arm"],
            "share_right_arm": round(r["right_arm"] / n, 2) if n else None,
            "right_family": r["right_family"], "share_right_family": round(r["right_family"] / n, 2) if n else None,
            "mean_confidence": round(sum(r["confidence"]) / n, 2) if n else None,
            "guesses": dict(sorted(r["guesses"].items())), "stale": r["stale"], "failed": r["failed"]}
    return result


def markdown(result: dict) -> str:
    md = [f"# Prova de cegueira: {result['blindness_dir']} (cartões de {result['cards_dir']})", "",
          "| braço | sorteados | respondidos | braço certo | fração | família certa | fração | confiança média | palpites | obsoletos |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    fmt = lambda v: "-" if v is None else f"{v:.2f}"
    for arm, r in result["arms"].items():
        guesses = ", ".join(f"{g} {n}" for g, n in r["guesses"].items()) or "-"
        md.append(f"| {arm} | {r['drawn']} | {r['answered']} | {r['right_arm']} | {fmt(r['share_right_arm'])} | "
                  f"{r['right_family']} | {fmt(r['share_right_family'])} | {fmt(r['mean_confidence'])} | {guesses} | "
                  f"{r['stale']} |")
    if result["unknown_card_ids"]:
        md += ["", f"Ids do sorteio ausentes de key.json: {', '.join(result['unknown_card_ids'])}"]
    return "\n".join(md) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Per-arm attribution of a blindness probe.")
    ap.add_argument("blindness_dir", nargs="?", default="blindness")
    ap.add_argument("--cards", default=os.environ.get("CARDS_DIR", "cards"))
    ap.add_argument("--json", help="also write the summary as JSON to this path")
    args = ap.parse_args(argv)
    bdir, cards_dir = HERE / args.blindness_dir, HERE / args.cards
    if not bdir.is_dir():
        raise SystemExit(f"no blindness directory {bdir}")
    result = summarise(bdir, cards_dir)
    sys.stdout.write(markdown(result))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
