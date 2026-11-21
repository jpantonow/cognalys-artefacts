"""Blindness probe of option A (docs/preregistro_opcao_a_20261116.md, "prova de cegueira"): per arm (C, TP, SP2) and
view (blindness_optA/, blindness_optA_patch/), the cards drawn and answered, right arm, right family (C pipeline; TP
and SP2 agents), mean confidence, the guesses, and per guessed arm the precision (of the cards called that arm, how
many were). Writes blindness_optA_summary.json / .md. Run (WSL): python3 blindness_summary_optA.py
"""
import collections
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = ("C", "TP", "SP2")
FAMILY = {"C": "pipeline", "TP": "agent", "SP2": "agent"}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def summarise(bdir):
    key = load(HERE / "cards_k3/key.json")["cards"]
    shas = {c["card_id"]: c["sha256"] for c in load(HERE / "cards_k3/cards.json")["cards"]}
    sample = load(bdir / "sample.json")["cards"]
    rows = {a: {"drawn": 0, "answered": 0, "right_arm": 0, "right_family": 0, "confidence": [],
                "guesses": collections.Counter(), "failed": 0, "stale": 0} for a in ARMS}
    called = collections.Counter()
    called_right = collections.Counter()
    for cid in sample:
        arm = key[cid]["arm"]
        r = rows[arm]
        r["drawn"] += 1
        p = bdir / f"{cid}.json"
        if not p.is_file():
            r["failed"] += 1
            continue
        rec = load(p)
        if rec.get("card_sha256") != shas[cid]:
            r["stale"] += 1
            continue
        g = rec["output"]["guess"]
        r["answered"] += 1
        r["right_arm"] += g == arm
        r["right_family"] += FAMILY.get(g) == FAMILY[arm]
        r["confidence"].append(float(rec["output"]["confidence"]))
        r["guesses"][g] += 1
        called[g] += 1
        called_right[g] += g == arm
    out = {}
    for a, r in rows.items():
        out[a] = {"drawn": r["drawn"], "answered": r["answered"], "right_arm": r["right_arm"],
                  "right_family": r["right_family"], "failed": r["failed"], "stale": r["stale"],
                  "mean_confidence": round(sum(r["confidence"]) / len(r["confidence"]), 3) if r["confidence"] else None,
                  "guesses": dict(r["guesses"]),
                  "precision_of_guess": (f"{called_right[a]}/{called[a]}" if called[a] else "0/0")}
    return out


def main():
    res = {v: summarise(HERE / d) for v, d in (("full", "blindness_optA"), ("patch", "blindness_optA_patch"))
           if (HERE / d / "sample.json").is_file()}
    (HERE / "blindness_optA_summary.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    md = ["# Prova de cegueira da opção A (C, TP, SP2)", "",
          "| vista | braço | sorteados | respondidos | braço certo | família certa | confiança média | palpites | "
          "precisão do palpite (chamados deste braço que eram dele) |", "|---|---|---|---|---|---|---|---|---|"]
    for v, rows in res.items():
        for a, r in rows.items():
            md.append(f"| {v} | {a} | {r['drawn']} | {r['answered']} | {r['right_arm']} | {r['right_family']} | "
                      f"{r['mean_confidence']} | {r['guesses']} | {r['precision_of_guess']} |")
    (HERE / "blindness_optA_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
