"""Second reading of the judges' scores under the author's rule of 2026-11-14.

Same phase-2 scores, same cards, every arm treated alike; tabela.md (the protocol reading) is kept.
  1. The answer key is to act now: a pull request is an acceptable deliverable for every finding.
  2. No action is not marked down for its reason (a run that did not fix may have many causes).
  3. An issue is not marked down for its text.
  4. A fix the base tip already held ("correção não aplicável: código mudou upstream") is judged on the finding
     alone: its patch is scored; the moved tip's missing build and its text (never sent; 2026-11-16) do not count.
A PR is acceptable when r2 >= 2 and r4 >= 1 and it built; under rule 4, r2 >= 2 alone; a failed run is 0.
Final per card: both judges agree, else the principal judge (opus), as in tabulate.final_a.
Writes tabela_regra_autor.json / .md; with PHASE2=phase2_v2 (the v2 scores), tabela_regra_autor_v2.*.
Run: python3 retabulate_author_rule.py
READING_RULE=patch (with PHASE2=phase2<suffix>_patch, the patch-view scores of judge.py JUDGE_VIEW=patch):
the patch reading (PATCH_RULE below) -> tabela_regra_autor<suffix>_patch.json / .md. It reads only records
written by the patch view (rec["view"] == "patch", PHASE2 ending in _patch) and scored against the answer key
of PATCH_GABARITO (default gabarito_v2.json), and aborts otherwise. A PR card without a final score (no record,
.failed.json, stale card sha) is counted in unscored_prs and the table is not written unless ALLOW_UNSCORED=1;
a unit with PR cards but no patch-view record at all is left out of every arm (its issue and no-action cards
would otherwise count 1 with no PR beside them).
"""
import collections
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JUDGES, PRINCIPAL = ("opus", "sol"), "opus"
ARMS = ("S", "O", "T") + (("TP", "SP2") if os.environ.get("CARDS_DIR", "cards") != "cards" else ()) + ("C", "D")
PHASE2 = os.environ.get("PHASE2", "phase2")
TAG = PHASE2[len("phase2"):]
CARDS_DIR = os.environ.get("CARDS_DIR", "cards")  # cards_<tag> for a rerun of arms C and D
UPSTREAM = "correção não aplicável"
# READING_RULE=patch (2026-11-14): the judges saw each PR's diff without its title and description, and no
# ISSUE or NO_ACTION card (judge.py JUDGE_VIEW=patch).
READING_RULE = os.environ.get("READING_RULE", "")
if READING_RULE not in ("", "patch"):
    raise SystemExit(f"READING_RULE={READING_RULE!r}: expected patch")
PATCH_GABARITO = os.environ.get("PATCH_GABARITO", "gabarito_v2.json")
ALLOW_UNSCORED = os.environ.get("ALLOW_UNSCORED", "") == "1"
PATCH_RULE = [
    "Patch reading (2026-11-14): the judges saw each pull request's diff against the analysed commit and whether "
    "it was built, without its title and description; issue and no-action cards were not sent.",
    "A PR is acceptable when r2 >= 2 and it built (or the fix was already on the base tip: "
    "\"correção não aplicável: código mudou upstream\", judged on the finding alone).",
    "An issue or no action counts 1 whatever its text; a failed run counts 0.",
    "Final per card: both judges agree, else the principal judge (opus).",
    "Only patch-view records scored against the pre-registered answer key are read; a PR card without a final "
    "score is reported (unscored_prs) and blocks the table unless ALLOW_UNSCORED=1; a unit with PR cards but no "
    "patch-view record is left out of every arm.",
]


def check_patch_record(p, rec, gab):
    """Patch reading: a record must come from the patch view and carry the pre-registered answer key."""
    if rec.get("view") != "patch":
        raise SystemExit(f"{p}: not a patch-view record (view={rec.get('view')!r}); READING_RULE=patch reads only "
                         f"judge.py JUDGE_VIEW=patch output")
    if rec.get("phase") != "phase2":
        raise SystemExit(f"{p}: phase={rec.get('phase')!r}, expected phase2")
    unit = rec.get("unit")
    if unit not in gab or rec.get("gabarito") != gab[unit]:
        raise SystemExit(f"{p}: the answer key in the record is not {PATCH_GABARITO}[{unit!r}]")


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main():
    cards = {c["card_id"]: c for c in load(ROOT / CARDS_DIR / "cards.json")["cards"]}
    key = load(ROOT / CARDS_DIR / "key.json")["cards"]
    scores = {j: {} for j in JUDGES}
    patch = READING_RULE == "patch"
    recorded_units = set()                     # patch reading: units with at least one patch-view record
    if patch:
        if not PHASE2.endswith("_patch"):
            raise SystemExit(f"PHASE2={PHASE2!r}: READING_RULE=patch reads a patch-view directory "
                             f"(phase2<suffix>_patch)")
        gab = load(ROOT / PATCH_GABARITO)
    for j in JUDGES:
        for p in sorted((ROOT / PHASE2 / j).glob("*.json")):
            if p.name.endswith(".failed.json"):
                continue
            rec = load(p)
            if patch:
                check_patch_record(p, rec, gab)
                recorded_units.add(rec["unit"])
            for e in rec["output"]["cards"]:
                cid = e["card_id"]
                if cid in cards and rec.get("card_sha256", {}).get(cid) == cards[cid]["sha256"]:
                    scores[j][cid] = e

    def a(judge, cid, reading):
        k = key[cid]
        klass = k["class_publish"] if reading == "publish" else k["class_all"]
        if klass == "FALHA":
            return 0
        if READING_RULE == "patch":
            if klass in ("NENHUMA AÇÃO", "ISSUE"):
                return 1                       # not sent to the judges in the patch view
            s = scores[judge].get(cid)
            if s is None:
                return None
            built = k["build"] in ("pass", "fails_without_patch_too") or k["raw_outcome"].startswith(UPSTREAM)
            return int((s.get("r2") or 0) >= 2 and built)
        s = scores[judge].get(cid)
        if s is None:
            return None
        if klass in ("NENHUMA AÇÃO", "ISSUE"):
            return 1
        if k["raw_outcome"].startswith(UPSTREAM):
            # Rule 4 (2026-11-16, declared in docs/preregistro_gabarito_v3_20261116.md): the base tip already held
            # the fix, so nothing is sent; the PR text a maintainer never reads is not scored.
            return int((s.get("r2") or 0) >= 2)
        built = k["build"] in ("pass", "fails_without_patch_too")
        return int((s.get("r2") or 0) >= 2 and (s.get("r4") or 0) >= 1 and built)

    out = {"rule": PATCH_RULE if patch else __doc__.strip().splitlines()[2:8], "arms": {}}
    # Patch reading: a unit with PR cards but no patch-view record is left out of every arm (judge.py skips a
    # unit without PR cards, so such a unit legitimately has no record and still counts).
    pr_units = {k["unit"] for k in key.values() if "PR" in (k["class_all"], k["class_publish"])}
    dropped_units = sorted(pr_units - recorded_units) if patch else []
    unscored_total = 0
    for arm in ARMS:
        for reading in (("all", "publish") if arm == "C" else ("all",)):
            units = collections.defaultdict(list)
            prs = good = bad = 0
            unscored = 0
            good_units = set()
            for cid, k in key.items():
                if k["arm"] != arm:
                    continue
                v = {j: a(j, cid, reading) for j in JUDGES}
                final = v[PRINCIPAL] if v[PRINCIPAL] is not None else v["sol"]
                if patch:
                    if final is None and (k["class_publish"] if reading == "publish" else k["class_all"]) == "PR":
                        unscored += 1
                    if k["unit"] in dropped_units:
                        continue
                if final is None:
                    continue
                units[k["unit"]].append(final)
                klass = k["class_publish"] if reading == "publish" else k["class_all"]
                if klass == "PR":
                    prs += 1
                    good += final
                    if final:
                        good_units.add(k["unit"])
                    bad += 1 - final
            x = {u: sum(v) / len(v) for u, v in units.items()}
            good_pr_units = len(good_units)
            row = {"A": round(sum(x.values()) / len(x), 2), "units": len(x), "runs": sum(map(len, units.values())),
                   "prs": prs, "good_prs": good, "rejected_prs": bad, "units_with_good_pr": good_pr_units}
            if patch:
                row["unscored_prs"] = unscored
                unscored_total += unscored
            out["arms"][arm + ("" if arm != "C" else "_" + reading)] = row
    name = f"tabela_regra_autor{TAG}"
    if patch:
        out["phase2"], out["cards_dir"], out["gabarito"] = PHASE2, CARDS_DIR, PATCH_GABARITO
        out["units_without_patch_record"] = dropped_units
        out["unscored_prs"] = unscored_total
        out["allow_unscored"] = ALLOW_UNSCORED
        name = f"tabela_regra_autor{TAG[:-len('_patch')]}_patch"
        if unscored_total and not ALLOW_UNSCORED:
            per_arm = ", ".join(f"{arm} {r['unscored_prs']}" for arm, r in out["arms"].items())
            raise SystemExit(
                f"{unscored_total} PR card(s) without a final patch-view score in {PHASE2} (per arm: {per_arm}; "
                f"units without any patch-view record: {', '.join(dropped_units) or 'none'}). "
                f"Not writing {name}.*; ALLOW_UNSCORED=1 writes it with the unscored_prs column.")
    (ROOT / f"{name}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    md = ["# Leitura do patch (2026-11-14)" if patch else "# Leitura com a regra do autor (2026-11-14)", "", *("- " + l.strip() for l in out["rule"]), ""]
    if patch:
        md += [f"- Notas: `{PHASE2}`, cartões `{CARDS_DIR}`, gabarito `{PATCH_GABARITO}`; PRs sem nota final: "
               f"{unscored_total}; unidades sem registro da visão do patch (fora de todos os braços): "
               f"{', '.join(dropped_units) or 'nenhuma'}.", "",
               "| braço | A | runs | PRs | PRs aceitos | PRs recusados | unidades com PR aceito | PRs sem nota |",
               "|---|---|---|---|---|---|---|---|"]
    else:
        md += ["| braço | A | runs | PRs | PRs aceitos | PRs recusados | unidades com PR aceito |", "|---|---|---|---|---|---|---|"]
    for arm, r in out["arms"].items():
        md.append(f"| {arm} | {r['A']:.2f} | {r['runs']} | {r['prs']} | {r['good_prs']} | {r['rejected_prs']} | {r['units_with_good_pr']} |"
                  + (f" {r['unscored_prs']} |" if patch else ""))
    (ROOT / f"{name}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
