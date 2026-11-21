"""Option A table (docs/preregistro_opcao_a_20261116.md, "Regra de tabulação"; OP dropped by the dated deviation of
2026-11-17). Reads the v3 scores of every card (phase2_v3_k3[_patch]/ and, for SP2 r3-r4, phase2_v3_k3_sp2r34[_patch]/
written by judge_option_a_new.py), the cards_k3 key, and for TP and SP2 each PR's phase-2 record
(runs/<unit>/<arm>_r<k>/port/result.json).

Acceptability per run (final per card: both judges agree, else the principal judge opus, as tabulate.final_a):
  FALHA 0; ISSUE / NO_ACTION 1.
  C and the first block (S, O, T): as retabulate_author_rule.py (rule 4 for C's "correção não aplicável").
  TP, SP2 PR: port "ported": author r2>=2, r4>=1 and the kit/agent build at the tip succeeded; patch r2>=2 and built
    at the tip. Port "already_fixed" or "abstain" (an ALREADY_FIXED without deterministic confirmation, or a merge
    that left nothing to port, is recorded as abstain): rule 4, r2>=2. Port "failure": 0.
A per arm = mean over units of the per-unit share of acceptable runs. Also: PRs proposed, PRs acceptable, units
with a PR, classes, port outcomes, per-judge A, Cohen's kappa (acceptability, author reading, cards both judges
scored) and quadratic-weighted kappa (r2, PR cards both judges scored, author reading), the run-to-run consistency
of the delivery class, and the list-price cost per run (phase 1 + port; analyze.agent_cost).
Writes tabela_option_a.json / .md. Run (WSL, venv, PYTHONPATH=src): ARMS_TAG=k3 python tabulate_option_a.py
"""
import collections
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
TCC = OUT.parent.parent
os.environ.setdefault("ARMS_TAG", "k3")
sys.path.insert(0, str(OUT))
import analyze as an  # noqa: E402

JUDGES, PRINCIPAL = ("opus", "sol"), "opus"
FIRST, PLUS = ("S", "O", "T"), ("TP", "SP2")
ARMS = FIRST + PLUS + ("C",)
UPSTREAM = "correção não aplicável"
DIRS = {"author": ["phase2_v3_k3", "phase2_v3_k3_sp2r34"], "patch": ["phase2_v3_k3_patch", "phase2_v3_k3_sp2r34_patch"]}


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


cards = {c["card_id"]: c for c in load(HERE / "cards_k3/cards.json")["cards"]}
key = load(HERE / "cards_k3/key.json")["cards"]


def scores(view):
    out = {j: {} for j in JUDGES}
    for d in DIRS[view]:
        for j in JUDGES:
            for p in sorted((HERE / d / j).glob("*.json")):
                if p.name.endswith(".failed.json"):
                    continue
                rec = load(p)
                if view == "patch" and rec.get("view") != "patch":
                    raise SystemExit(f"{p}: not a patch-view record")
                for e in rec["output"]["cards"]:
                    cid = e["card_id"]
                    if cid in cards and rec.get("card_sha256", {}).get(cid) == cards[cid]["sha256"]:
                        out[j][cid] = e
    return out


def port_of(k):
    p = TCC / k["source"] / "port" / "result.json"
    return load(p) if p.is_file() else None


def port_class(port):
    if port is None:
        return "missing"
    o = port.get("outcome")
    if o == "ported":
        return "clean" if port.get("deterministic_merge") == "applied" else "after_conflict"
    return o  # already_fixed / abstain / failure


def accept(view, s, cid):
    """1/0 for one judge's score s (None: not scored) of card cid."""
    k = key[cid]
    klass = k["class_all"]
    if klass == "FALHA":
        return 0
    if klass in ("NENHUMA AÇÃO", "ISSUE"):
        return 1 if (view == "patch" or s is not None) else None
    if s is None:
        return None
    r2, r4 = (s.get("r2") or 0), (s.get("r4") or 0)
    if k["arm"] in PLUS:
        port = port_of(k)
        pc = port_class(port)
        if pc in ("already_fixed", "abstain"):
            return int(r2 >= 2)
        if pc in ("clean", "after_conflict"):
            built = ((port.get("build") or {}).get("status") == "success")
            return int(r2 >= 2 and built and (view == "patch" or r4 >= 1))
        return 0
    if k["raw_outcome"].startswith(UPSTREAM):
        return int(r2 >= 2)
    built = k["build"] in ("pass", "fails_without_patch_too")
    return int(r2 >= 2 and built and (view == "patch" or r4 >= 1))


def final(view, sc, cid):
    v = {j: accept(view, sc[j].get(cid), cid) for j in JUDGES}
    return v[PRINCIPAL] if v[PRINCIPAL] is not None else v["sol"]


def arm_rows(view, sc, chooser):
    rows = {}
    for arm in ARMS:
        units = collections.defaultdict(list)
        prs = good = unscored = 0
        good_units = set()
        for cid, k in key.items():
            if k["arm"] != arm:
                continue
            f = chooser(view, sc, cid)
            if k["class_all"] == "PR" and f is None:
                unscored += 1
            if f is None:
                continue
            units[k["unit"]].append(f)
            if k["class_all"] == "PR":
                prs += 1
                good += f
                if f:
                    good_units.add(k["unit"])
        x = {u: sum(v) / len(v) for u, v in units.items()}
        rows[arm] = {"A": round(sum(x.values()) / len(x), 3) if x else None, "units": len(x),
                     "runs": sum(map(len, units.values())), "prs_scored": prs, "prs_acceptable": good,
                     "units_with_acceptable_pr": len(good_units), "prs_unscored": unscored}
    return rows


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    cats = sorted({a for a, _ in pairs} | {b for _, b in pairs})
    po = sum(a == b for a, b in pairs) / n
    pa = collections.Counter(a for a, _ in pairs)
    pb = collections.Counter(b for _, b in pairs)
    pe = sum(pa[c] * pb[c] for c in cats) / n / n
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def qw_kappa(pairs, k=4):
    n = len(pairs)
    if not n:
        return None
    obs = [[0] * k for _ in range(k)]
    for a, b in pairs:
        obs[a][b] += 1
    ra = [sum(obs[i]) for i in range(k)]
    cb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = sum((i - j) ** 2 * obs[i][j] for i in range(k) for j in range(k))
    den = sum((i - j) ** 2 * ra[i] * cb[j] / n for i in range(k) for j in range(k))
    return round(1 - num / den, 3) if den else None


def main():
    out = {"rule": __doc__.strip().splitlines()[3:13], "arms": {}}
    sc = {v: scores(v) for v in ("author", "patch")}
    for view in ("author", "patch"):
        out[f"A_{view}"] = arm_rows(view, sc[view], final)
        for j in JUDGES:
            out[f"A_{view}_{j}"] = arm_rows(view, sc[view], lambda v, s, cid, j=j: accept(v, s[j].get(cid), cid))
    # classes, units with a PR, port outcomes, consistency, cost
    for arm in ARMS:
        ks = [k for k in key.values() if k["arm"] == arm]
        cls = collections.Counter(k["class_all"] for k in ks)
        units_pr = {k["unit"] for k in ks if k["class_all"] == "PR"}
        per_unit = collections.defaultdict(set)
        for k in ks:
            per_unit[k["unit"]].add(k["class_all"])
        row = {"runs": len(ks), "classes": dict(cls), "units_with_pr": len(units_pr),
               "units_same_class_all_replicas": sum(len(v) == 1 for v in per_unit.values()), "units": len(per_unit)}
        if arm in PLUS:
            row["port"] = dict(collections.Counter(port_class(port_of(k)) for k in ks if k["class_all"] == "PR"))
            row["port_build_failed"] = sorted(f"{k['unit']}/{arm}_r{k['replica']}" for k in ks if k["class_all"] == "PR"
                                              and port_class(port_of(k)) in ("clean", "after_conflict")
                                              and (port_of(k).get("build") or {}).get("status") != "success")
        if arm != "C":
            p1, p2 = [], []
            for k in ks:
                rdir = TCC / k["source"]
                r = load(rdir / "result.json")
                p1.append(an.agent_cost(r, rdir).get("cost_usd_list"))
                port = port_of(k) if arm in PLUS and k["class_all"] == "PR" else None
                if port and port.get("agent"):
                    p2.append(an.agent_cost(port, rdir / "port").get("cost_usd_list"))
                else:
                    p2.append(0.0)
            ok = all(v is not None for v in p1 + p2)
            row["cost_list_phase1_per_run"] = round(sum(p1) / len(p1), 4) if ok else None
            row["cost_list_port_per_run"] = round(sum(p2) / len(p2), 4) if ok else None
            row["cost_list_total_per_run"] = round((sum(p1) + sum(p2)) / len(p1), 4) if ok else None
        out["arms"][arm] = row
    # agreement, author reading
    a_pairs, r2_pairs = [], []
    for cid in key:
        a = [accept("author", sc["author"][j].get(cid), cid) for j in JUDGES]
        if key[cid]["class_all"] in ("PR", "ISSUE", "NENHUMA AÇÃO") and None not in a and \
                all(sc["author"][j].get(cid) is not None for j in JUDGES):
            a_pairs.append(tuple(a))
        if key[cid]["class_all"] == "PR" and all(sc["author"][j].get(cid) is not None for j in JUDGES):
            r2_pairs.append(tuple(int(sc["author"][j][cid].get("r2") or 0) for j in JUDGES))
    out["agreement_author"] = {"cohen_kappa_acceptability": kappa(a_pairs), "cards": len(a_pairs),
                               "quadratic_kappa_r2": qw_kappa(r2_pairs), "pr_cards": len(r2_pairs),
                               "raw_agreement_acceptability": round(sum(a == b for a, b in a_pairs) / len(a_pairs), 3)}
    (HERE / "tabela_option_a.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    md = ["# Opção A: tabela (leituras v3)", "", *("- " + l.strip() for l in out["rule"]), "",
          "| braço | runs | PR/ISSUE/NADA/FALHA | unid. com PR | A autor | A patch | PRs | PRs aceitos autor/patch | "
          "porte | US$/run lista (fase 1 + porte) | classe igual nas 4 réplicas |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm in ARMS:
        r, aa, ap = out["arms"][arm], out["A_author"][arm], out["A_patch"][arm]
        c = r["classes"]
        md.append(f"| {arm} | {r['runs']} | {c.get('PR', 0)}/{c.get('ISSUE', 0)}/{c.get('NENHUMA AÇÃO', 0)}/"
                  f"{c.get('FALHA', 0)} | {r['units_with_pr']} | {aa['A']} | {ap['A']} | {aa['prs_scored']} "
                  f"({aa['prs_unscored']} sem nota) | {aa['prs_acceptable']} / {ap['prs_acceptable']} | "
                  f"{r.get('port', '—')} | {r.get('cost_list_total_per_run', '—')} "
                  f"({r.get('cost_list_phase1_per_run', '—')} + {r.get('cost_list_port_per_run', '—')}) | "
                  f"{r['units_same_class_all_replicas']}/{r['units']} |")
    md += ["", "Por juiz (A autor / A patch): " + "; ".join(
        f"{arm}: opus {out['A_author_opus'][arm]['A']}/{out['A_patch_opus'][arm]['A']}, sol "
        f"{out['A_author_sol'][arm]['A']}/{out['A_patch_sol'][arm]['A']}" for arm in ARMS),
           "", f"Concordância (autor v3): {json.dumps(out['agreement_author'])}"]
    (HERE / "tabela_option_a.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
