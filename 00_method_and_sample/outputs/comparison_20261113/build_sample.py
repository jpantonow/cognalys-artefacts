"""Draws the 20 units of the reduced agent comparison (2026-11-13) with a recorded seed.

Rule (PLANO_FABLE, "2026-11-12 (tarde)"; the user's confirmation of 2026-11-13):
- 10 PR units from outputs/frozen_pr_20261113/population.json, one unit per
  finding_id (FreeOTP #521/#524 share one), stratified by group
  (incorporado / aberto / fechado) in proportion to the group's units, by the
  largest remainder, at least 2 per group. Eligible: the original run ended in
  "PR pronto" and its PR record holds a non-empty diff (a reviewable diff, the
  reference for the gabarito). When a group has fewer eligible units than its
  quota, the rest comes from its other units.
- 10 units from the specification's 28-unit cohort
  (outputs/design_20261110/comparacao/c05_units.json), excluding every unit
  whose APK and method also appear in the PR population (the same code under
  another PR), stratified by stratum (no_pr / our_pr_closed) the same way, no
  minimum.
- Within each stratum the draw is random.Random(SEED).sample over the units
  sorted by a stable key, strata in a fixed order.

Writes outputs/comparison_20261113/sample.json and units/<unit>/unit.json
(the RVSec rows each agent receives). Reads only; no network, no model.
"""
import csv
import hashlib
import json
import random
from pathlib import Path

SEED = 20261113
TCC = Path("/mnt/c/Users/Antonow/Documents/tcc_v3")
OUT = TCC / "outputs/comparison_20261113"
POP = TCC / "outputs/frozen_pr_20261113/population.json"
CMP = TCC / "outputs/frozen_pr_20261113/compare.json"
C01 = TCC / "outputs/design_20261110/comparacao/c01_cohort.json"
C05 = TCC / "outputs/design_20261110/comparacao/c05_units.json"
EXP_CSV = TCC / "data/processed/exp_final/findings_exp_final.csv"
N_PR, N_COHORT = 10, 10
PR_GROUPS = ("incorporado", "aberto", "fechado")
COHORT_STRATA = ("no_pr", "our_pr_closed")


def fid(uid):
    return hashlib.sha256(str(uid).encode("utf-8")).hexdigest()[:12]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows_of(path):
    path = Path(path) if str(path).startswith("/") else TCC / path
    if path.suffix == ".jsonl":
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def uid_of(row):
    return str(row.get("unique_msg") or row.get("unique_error_id") or "")


def quotas(sizes, total, minimum):
    """Largest-remainder apportionment with a per-stratum minimum."""
    whole = sum(sizes.values())
    raw = {k: total * v / whole for k, v in sizes.items()}
    q = {k: max(int(raw[k]), min(minimum, sizes[k])) for k in sizes}
    for k in sorted(sizes, key=lambda k: -(raw[k] - int(raw[k]))):
        if sum(q.values()) >= total:
            break
        if q[k] < sizes[k] and q[k] <= int(raw[k]):
            q[k] += 1
    while sum(q.values()) > total:  # minimums overshot: take from the largest
        k = max(q, key=lambda k: q[k] - minimum)
        q[k] -= 1
    return q


def agent_row(row):
    return {"finding_id": fid(uid_of(row)), "unique_error_id": uid_of(row), "spec": row["spec"],
            "class": row["class"], "method": row["method"], "message": row["message"],
            "apk": row["apk"], "repo_url": row["repo_url"], "commit": row["commit"],
            "detector_source_location": row.get("detector_source_location", "")}


def pr_units():
    pop, cmp = load(POP), load(CMP)
    by_fid = {}
    for pr in pop["prs"]:
        if not pr.get("included"):
            continue
        by_fid.setdefault(pr["finding_id"], []).append(pr)
    rows_cmp = {}
    for r in cmp["rows"]:
        rows_cmp.setdefault(r["finding_id"], r)
    units = []
    for f, prs in sorted(by_fid.items()):
        pr = prs[0]
        groups = sorted({p["group"] for p in prs})
        assert len(groups) == 1, (f, groups)
        members = {m["unique_error_id"] for m in pr["members"]}
        rows = [r for r in rows_of(pr["findings_file"]) if uid_of(r) in members]
        assert len(rows) == len(members), (f, len(rows), len(members))
        commits = {r["commit"] for r in rows}
        assert commits == {pr["pinned_commit"]}, (f, commits, pr["pinned_commit"])
        diff, task = "", ""
        for p in prs:
            meta = TCC / p["pr_metadata"] if p.get("pr_metadata") else None
            if meta and meta.is_file():
                data = load(meta)
                diff = diff or (data.get("diff") or "")
                task = task or ((data.get("build_validation") or {}).get("task") or "")
        c = rows_cmp.get(f, {})
        units.append({
            "unit": f"pr_{f}", "source": "pr_population", "group": groups[0],
            "finding_ids": sorted(fid(u) for u in members), "apk": pr["apk"],
            "repo_url": pr["repository"], "pinned_commit": pr["pinned_commit"],
            "spec": sorted({r["spec"] for r in rows}), "class": rows[0]["class"], "method": pr["method"],
            "prs": [p["pr_url"] for p in prs], "original_run": pr["original_run"],
            "original_outcome": c.get("original_outcome"), "frozen_outcome": c.get("frozen_outcome"),
            "frozen_run": c.get("frozen_run"),
            "reference_pr_records": [p["pr_metadata"] for p in prs if p.get("pr_metadata")],
            "reference_diff_lines": len(diff.splitlines()), "build_task": task or "assembleDebug",
            "eligible": c.get("original_outcome") == "PR pronto" and bool(diff.strip()),
            "rows": [agent_row(r) for r in rows]})
    return units


def cohort_units(pr_keys):
    c01, c05 = load(C01), load(C05)
    exp_rows = rows_of(EXP_CSV)
    units = []
    for u in c05:
        key = (u["apk"], u["method"])
        cohort_rows = [r for r in c01 if (r["apk"], r["class"], r["method"]) == (u["apk"], u["class"], u["method"])
                       and r["stratum"] in COHORT_STRATA]
        rows = []
        for cr in cohort_rows:
            if cr["part"] == "bench":
                run = TCC / "outputs" / cr["latest_from"].split("/statistics/")[0]
                inputs = load(run / "run_inputs.json")
                source = inputs.get("findings_csv") or inputs.get("findings")
                if isinstance(source, list):
                    source = source[0]
                candidates = rows_of(source)
            else:
                candidates = exp_rows
            match = [r for r in candidates if (r["apk"], r["class"], r["method"], r["spec"])
                     == (cr["apk"], cr["class"], cr["method"], cr["spec"])]
            if not match:  # bench rows keep the bytecode method name (name$suffix) and may sit under a nested class
                match = [r for r in candidates if r["spec"] == cr["spec"] and r["method"].split("$")[0] == cr["method"]
                         and r["class"].split("$")[0] == cr["class"].split("$")[0]]
            assert len(match) == 1, (cr["apk"], cr["method"], cr["spec"], len(match),
                                     [(r["class"], r["method"]) for r in candidates if r["spec"] == cr["spec"]][:5])
            rows.append(match[0])
        commits = {r["commit"] for r in rows}
        assert len(commits) == 1 and all(commits), (u["apk"], commits)
        stratum = u["stratum"][0]
        units.append({
            "unit": "co_" + fid("|".join(sorted(uid_of(r) for r in rows))), "source": "spec_cohort",
            "group": stratum, "finding_ids": sorted(fid(uid_of(r)) for r in rows), "apk": u["apk"],
            "repo_url": rows[0]["repo_url"], "pinned_commit": rows[0]["commit"],
            "spec": sorted({r["spec"] for r in rows}), "class": u["class"], "method": u["method"],
            "latest_outcome": u["latest"], "bench_id": u.get("bench_id") or [],
            "build_task": "assembleDebug",
            "overlaps_pr_population": key in pr_keys, "eligible": key not in pr_keys,
            "rows": [agent_row(r) for r in rows]})
    return units


def draw(units, strata, total, minimum, rng):
    sizes = {s: sum(1 for u in units if u["group"] == s) for s in strata}
    q = quotas(sizes, total, minimum)
    chosen = []
    for s in strata:
        pool = sorted((u for u in units if u["group"] == s), key=lambda u: u["unit"])
        good = [u for u in pool if u["eligible"]]
        pick = rng.sample(good, min(q[s], len(good)))
        if len(pick) < q[s]:
            rest = [u for u in pool if u not in pick]
            pick += rng.sample(rest, q[s] - len(pick))
        chosen += pick
    return q, sizes, chosen


def main():
    rng = random.Random(SEED)
    prs = pr_units()
    pr_keys = {(u["apk"], u["method"]) for u in prs}
    cohort = cohort_units(pr_keys)
    q_pr, s_pr, chosen_pr = draw(prs, PR_GROUPS, N_PR, 2, rng)
    eligible_cohort = [u for u in cohort if u["eligible"]]
    q_co, s_co, chosen_co = draw(eligible_cohort, COHORT_STRATA, N_COHORT, 0, rng)
    units = chosen_pr + chosen_co
    sample = {
        "seed": SEED, "drawn_with": "random.Random(seed); PR strata incorporado, aberto, fechado, then cohort "
                                    "strata no_pr, our_pr_closed; each stratum's pool sorted by unit id, then rng.sample",
        "rule": __doc__.split("Writes")[0].strip(),
        "pr_population": {"units": len(prs), "by_group": s_pr, "quota": q_pr,
                          "eligible_by_group": {g: sum(1 for u in prs if u["group"] == g and u["eligible"]) for g in PR_GROUPS}},
        "spec_cohort": {"units": len(cohort), "excluded_overlap": [f'{u["apk"]} {u["method"]}' for u in cohort if not u["eligible"]],
                        "eligible_by_stratum": s_co, "quota": q_co},
        "arms": {"S": "Claude Code + claude-sonnet-5-5, effort high", "O": "Codex CLI + gpt-6.1-sol, reasoning high",
                 "T": "Codex CLI + gpt-6.1-terra, reasoning high", "C": "Cognalys (later, not in this queue)"},
        "replicas": 4,
        "units": [{k: v for k, v in u.items() if k != "eligible"} for u in units],
    }
    (OUT / "sample.json").write_text(json.dumps(sample, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for u in units:
        d = OUT / "units" / u["unit"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "unit.json").write_text(json.dumps(u, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("PR quota", q_pr, "of", s_pr, "| cohort quota", q_co, "of", s_co)
    for u in units:
        print(f'{u["unit"]:18} {u["source"]:13} {u["group"]:13} {u["apk"][:44]:44} {u["method"][:24]:24} '
              f'{",".join(s.replace("Spec", "") for s in u["spec"]):26} {u["pinned_commit"][:10]}')


if __name__ == "__main__":
    main()
