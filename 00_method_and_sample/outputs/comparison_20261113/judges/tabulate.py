#!/usr/bin/env python3
"""Scores of the blind judging (ESPEC_COMPARACAO §3.6, §3.7, §3.10 call of candidate losses).

From cards/cards.json + cards/key.json (cards.py), gabarito_final.json (fixed by the user),
phase1/<judge>/<unit>.json and phase2/<judge>/<unit>.json (judge.py) and, when present,
user_labels.csv (the user's blind form) and build_overrides.json ({card_id: pass | fail |
fails_without_patch_too}, from a later build harness; otherwise the build recorded with the run):

- A per card, per judge and per reading (§3.6): the class is in the answer key, and
    PR: R2 >= 2, the build passes or also fails without the patch, and R4 >= 1;
    ISSUE: R5 >= 1 and R4 >= 1;  NENHUMA AÇÃO: R5 >= 1;  FALHA: never.
  Two readings for C (§3.8): "todo rascunho pronto" (class_all) and "o que publicaria"
  (class_publish: a draft held for review is FALHA there). Agents have one reading (both columns).
- Final A of a card: the judges' when they agree; when they disagree, the user's (user_labels.csv)
  on the units with a candidate loss, else the principal judge's (opus); disagreements are listed.
- X[arm][unit] = share of acceptable runs; arm mean over units; candidate loss per unit (§3.10.1:
  some agent with A in >= 3/4 of its runs and C <= 1/4, reading "o que publicaria") and gain
  (the reverse); secondary measures (R1, mean R2 of PRs, build, R4, "PR ruim", "dispensa
  indevida", "issue sem ação").
- Per-judge scores by arm, and the family bias: for each judge and arm, the judge's mean minus
  the other judge's mean on the same cards; the judge's own family (opus: S; sol: O) is the cell
  that matters, and the difference-in-differences against the other arms removes plain leniency.
- Agreement: Cohen's kappa between the judges on A and on R1 (R1 with each judge's own phase-1
  answer key), quadratic-weighted kappa on R2; agreement of the two phase-1 answer keys before the
  user's review (same set, per-class kappa, mean Jaccard); user vs the models' final A on the user's
  sample (A, R1, R2) and the row of the §3.7 decision table it falls in.
Not here (later steps of the kit): paired bootstrap / Holm (§3.9), costs (analyze.py), stage
attribution (§3.10).

Subcommands:
  tabulate.py               -> tabela.json, tabela.md
  tabulate.py user-sample   -> user_sample.json, user_form.md and an empty user_labels.csv
                               (40 cards: 10 per arm, the candidate-loss units' cards first, up to 20;
                               plus the judges' disagreements on those units, for the user to decide).
                               Does not overwrite a user_labels.csv that has any filled row (--force).
  tabulate.py --self-test   synthetic cards, judges and user labels; checks every computation.
Rerun of arms C and D (2026-11-14, opt-in): CARDS_DIR=cards_<tag> PHASE2=phase2<suffix> tabulate.py reads
those cards and scores and writes tabela<suffix>.json/.md; user_labels and build_overrides take the same
suffix (their card ids are shared with the first cards). Unset, everything is as before.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cards as C  # noqa: E402
from cognalys.reporting.finding_statistics import cohen_kappa  # noqa: E402

JUDGES = ("opus", "sol")
PRINCIPAL = "opus"
# A judge's family: the arms whose model comes from its vendor (opus: Claude arms; sol: OpenAI arms).
import os as _os
_PLUS = ("TP", "SP2") if _os.environ.get("CARDS_DIR", "cards") != "cards" else ()
FAMILY = {"opus": ("S", "D") + (("SP2",) if _PLUS else ()), "sol": ("O", "T", "C") + (("TP",) if _PLUS else ())}
ARMS = ("S", "O", "T") + _PLUS + ("C", "D")
COGNALYS = ("C", "D")   # arms read twice: every ready draft ("all") and what would be published
# Opt-in inputs of a rerun (see the module docstring); they apply only to this directory, never to the
# self-test's temporary kit.
CARDS_DIR = os.environ.get("CARDS_DIR", "cards")
PHASE2 = os.environ.get("PHASE2", "phase2")
TAG = PHASE2[len("phase2"):] if PHASE2.startswith("phase2") else "_" + PHASE2
RERUN = CARDS_DIR != "cards" or PHASE2 != "phase2"


def names(root):
    """(cards dir, phase-2 dir, output/labels suffix) for a kit root."""
    if Path(root).resolve() == HERE and RERUN:
        return CARDS_DIR, PHASE2, TAG
    return "cards", "phase2", ""
READINGS = ("all", "publish")
CLASS_OF_KEY = {"PR": "PR", "ISSUE": "ISSUE", "NO_ACTION": "NENHUMA AÇÃO", "NENHUMA AÇÃO": "NENHUMA AÇÃO"}
LABEL_FIELDS = ["card_id", "unit", "class_acceptable", "r2", "regression_scenario", "fixes_what_is_reported",
                "r4", "r5", "notes"]
KAPPA_TABLE = [(0.6, "≥ 0,6: as notas dos modelos valem em todo o relatório"),
               (0.4, "0,4 a 0,6: valem, mas todos os cartões das unidades com perda candidata são julgados pelo usuário"),
               (float("-inf"), "< 0,4: o veredito sai só das notas do usuário nas unidades com perda candidata; "
                               "o julgamento por modelo não se sustentou")]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ statistics
def weighted_kappa(pairs, categories=(0, 1, 2, 3)):
    """Quadratic-weighted Cohen's kappa over integer ratings; None when undefined."""
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs:
        return None
    k = len(categories)
    index = {c: i for i, c in enumerate(categories)}
    n = len(pairs)
    w = [[((i - j) ** 2) / ((k - 1) ** 2) for j in range(k)] for i in range(k)]
    obs = [[0.0] * k for _ in range(k)]
    for a, b in pairs:
        obs[index[a]][index[b]] += 1 / n
    row = [sum(obs[i]) for i in range(k)]
    col = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    observed = sum(w[i][j] * obs[i][j] for i in range(k) for j in range(k))
    expected = sum(w[i][j] * row[i] * col[j] for i in range(k) for j in range(k))
    return None if expected == 0 else 1 - observed / expected


def kappa(pairs):
    return cohen_kappa([(str(a), str(b)) for a, b in pairs if a is not None and b is not None])


def mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 4) if values else None


def kappa_row(value):
    if value is None:
        return "indefinido (sem amostra do usuário)"
    return next(text for limit, text in KAPPA_TABLE if value >= limit)


# ------------------------------------------------------------------ A
def acceptable(klass, scores, gab_classes, build):
    """A (0/1) of §3.6 for a deliverable class (PR / ISSUE / NENHUMA AÇÃO / FALHA)."""
    if klass == "FALHA" or klass not in gab_classes or scores is None:
        return 0
    r2, r4, r5 = scores.get("r2"), scores.get("r4"), scores.get("r5")
    if klass == "PR":
        return int((r2 or 0) >= 2 and build in ("pass", "fails_without_patch_too") and (r4 or 0) >= 1)
    if klass == "ISSUE":
        return int((r5 or 0) >= 1 and (r4 or 0) >= 1)
    if klass == "NENHUMA AÇÃO":
        return int((r5 or 0) >= 1)
    return 0


def gab_classes(gab):
    return {CLASS_OF_KEY.get(c, c) for c in (gab or {}).get("acceptable_classes", [])}


# ------------------------------------------------------------------ loading
class Kit:
    def __init__(self, root: Path):
        self.root = Path(root)
        cards_dir, phase2, tag = names(self.root)
        self.cards = load(self.root / cards_dir / "cards.json")
        self.key = load(self.root / cards_dir / "key.json")["cards"]
        self.by_id = {c["card_id"]: c for c in self.cards["cards"]}
        gab_path = self.root / "gabarito_final.json"
        self.gab = {u: g for u, g in (load(gab_path) if gab_path.is_file() else {}).items() if g.get("fixed_by_user")}
        self.phase1 = {j: {p.stem: load(p)["output"] for p in sorted((self.root / "phase1" / j).glob("*.json"))
                           if not p.name.endswith(".failed.json")} for j in JUDGES}
        self.scores, self.stale = {j: {} for j in JUDGES}, []
        for judge in JUDGES:
            for path in sorted((self.root / phase2 / judge).glob("*.json")):
                if path.name.endswith(".failed.json"):
                    continue
                rec = load(path)
                for entry in rec["output"]["cards"]:
                    cid = entry["card_id"]
                    if cid not in self.by_id or rec.get("card_sha256", {}).get(cid) != self.by_id[cid]["sha256"]:
                        self.stale.append({"judge": judge, "card_id": cid, "unit": rec.get("unit")})
                        continue
                    self.scores[judge][cid] = entry
        overrides = self.root / f"build_overrides{tag}.json"
        self.build = {cid: k["build"] for cid, k in self.key.items()}
        if overrides.is_file():
            self.build.update(load(overrides))
        self.user = read_user_labels(self.root / f"user_labels{tag}.csv")

    def klass(self, cid, reading):
        k = self.key[cid]
        return k["class_publish"] if reading == "publish" else k["class_all"]

    def a_judge(self, judge, cid, reading, gab=None):
        unit = self.key[cid]["unit"]
        gab = self.gab.get(unit) if gab is None else gab
        klass = self.klass(cid, reading)
        if klass == "FALHA":
            return 0
        if gab is None or cid not in self.scores[judge]:
            return None
        return acceptable(klass, self.scores[judge][cid], gab_classes(gab), self.build[cid])

    def a_user(self, cid, reading):
        label = self.user.get(cid)
        gab = self.gab.get(self.key[cid]["unit"])
        if not label or gab is None:
            return None
        klass = self.klass(cid, reading)
        classes = gab_classes(gab)
        if label.get("class_acceptable") in ("yes", "no"):
            classes = (classes | {klass}) if label["class_acceptable"] == "yes" else (classes - {klass})
        return acceptable(klass, label, classes, self.build[cid])


def read_user_labels(path: Path):
    labels = {}
    if not path.is_file():
        return labels
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if not any((row.get(f) or "").strip() for f in ("class_acceptable", "r2", "r4", "r5")):
                continue
            entry = {"card_id": row["card_id"].strip(), "notes": row.get("notes", ""),
                     "regression_scenario": row.get("regression_scenario", ""),
                     "fixes_what_is_reported": (row.get("fixes_what_is_reported") or "").strip().lower(),
                     "class_acceptable": (row.get("class_acceptable") or "").strip().lower()}
            for name, top in (("r2", 3), ("r4", 2), ("r5", 2)):
                text = (row.get(name) or "").strip()
                if text == "":
                    entry[name] = None
                    continue
                value = int(text)
                if not 0 <= value <= top:
                    raise ValueError(f"user_labels.csv: {entry['card_id']} {name}={value} out of 0-{top}")
                entry[name] = value
            labels[entry["card_id"]] = entry
    return labels


# ------------------------------------------------------------------ computations
def final_a(kit, loss_units, reading):
    """{card_id: (A, source)} with source agree / principal / single / user / mechanical / unscored."""
    out = {}
    for cid, k in kit.key.items():
        if kit.klass(cid, reading) == "FALHA":
            out[cid] = (0, "mechanical")
            continue
        values = {j: kit.a_judge(j, cid, reading) for j in JUDGES}
        present = {j: v for j, v in values.items() if v is not None}
        if len(present) == 2 and len(set(present.values())) == 1:
            out[cid] = (present[PRINCIPAL], "agree")
        elif len(present) == 2:
            user = kit.a_user(cid, reading) if k["unit"] in loss_units else None
            out[cid] = (user, "user") if user is not None else (present[PRINCIPAL], "principal")
        elif present:
            j, v = next(iter(present.items()))
            out[cid] = (v, f"single:{j}")
        else:
            out[cid] = (None, "unscored")
    return out


def model_final(kit, reading):
    """Final A from the models alone (agree, else principal): what the user's sample is compared with."""
    out = {}
    for cid in kit.key:
        if kit.klass(cid, reading) == "FALHA":
            out[cid] = 0
            continue
        values = {j: kit.a_judge(j, cid, reading) for j in JUDGES}
        out[cid] = values[PRINCIPAL] if values[PRINCIPAL] is not None else values["sol"]
    return out


def per_unit(kit, finals):
    """X[(arm, reading)][unit] = share of acceptable runs (scored runs only)."""
    x = collections.defaultdict(dict)
    for reading in READINGS:
        groups = collections.defaultdict(list)
        for cid, k in kit.key.items():
            value = finals[reading][cid][0]
            if value is not None:
                groups[(k["arm"], k["unit"])].append(value)
        for (arm, unit), values in groups.items():
            x[(arm, reading)][unit] = {"acceptable": sum(values), "runs": len(values),
                                       "x": round(sum(values) / len(values), 4)}
    return x


def loss_calls(x, units):
    """Candidate losses and gains of §3.10.1 (reading "o que publicaria" for C)."""
    losses, gains = [], []
    for unit in sorted(units):
        c = x.get(("C", "publish"), {}).get(unit)
        agents = {a: x.get((a, "all"), {}).get(unit) for a in ("S", "O", "T")}
        if not c or not all(agents.values()):
            continue
        best = max(agents, key=lambda a: agents[a]["x"])
        if agents[best]["x"] >= 0.75 and c["x"] <= 0.25:
            losses.append({"unit": unit, "agent": best, "agent_x": agents[best]["x"], "c_x": c["x"]})
        worst_agent = max(a["x"] for a in agents.values())
        if c["x"] >= 0.75 and worst_agent <= 0.25:
            gains.append({"unit": unit, "c_x": c["x"], "best_agent_x": worst_agent})
    return losses, gains


def secondary(kit, finals, reading, arm):
    cids = [cid for cid, k in kit.key.items() if k["arm"] == arm]
    pr = [cid for cid in cids if kit.klass(cid, reading) == "PR"]
    scored = lambda cid, name: (kit.scores[PRINCIPAL].get(cid) or kit.scores["sol"].get(cid) or {}).get(name)  # noqa: E731
    r1 = [int(kit.klass(cid, reading) in gab_classes(kit.gab.get(kit.key[cid]["unit"])))
          for cid in cids if kit.key[cid]["unit"] in kit.gab]
    na = [cid for cid in cids if kit.klass(cid, reading) == "NENHUMA AÇÃO" and kit.key[cid]["unit"] in kit.gab]
    iss = [cid for cid in cids if kit.klass(cid, reading) == "ISSUE" and kit.key[cid]["unit"] in kit.gab]
    return {
        "runs": len(cids),
        "a_mean": mean([finals[reading][cid][0] for cid in cids]),
        "r1_class_right": mean(r1),
        "pr": len(pr),
        "r2_mean_pr": mean([scored(cid, "r2") for cid in pr]),
        "build_ok_pr": sum(kit.build[cid] in ("pass", "fails_without_patch_too") for cid in pr),
        "r4_mean": mean([scored(cid, "r4") for cid in cids if kit.klass(cid, reading) in ("PR", "ISSUE")]),
        "r5_mean": mean([scored(cid, "r5") for cid in cids if kit.klass(cid, reading) in ("ISSUE", "NENHUMA AÇÃO")]),
        "pr_ruim_r2_0": sum(scored(cid, "r2") == 0 for cid in pr),
        "dispensa_indevida": sum("NENHUMA AÇÃO" not in gab_classes(kit.gab[kit.key[cid]["unit"]]) for cid in na),
        "issue_sem_acao": sum("ISSUE" not in gab_classes(kit.gab[kit.key[cid]["unit"]]) for cid in iss),
    }


def per_judge(kit):
    out = {}
    for judge in JUDGES:
        out[judge] = {}
        for arm in ARMS:
            for reading in (READINGS if arm in COGNALYS else ("all",)):
                cids = [cid for cid, k in kit.key.items() if k["arm"] == arm]
                label = arm if arm not in COGNALYS else f"{arm}_{reading}"
                out[judge][label] = {
                    "a_mean": mean([kit.a_judge(judge, cid, reading) for cid in cids]),
                    "scored_cards": sum(cid in kit.scores[judge] for cid in cids),
                    "r2_mean": mean([kit.scores[judge].get(cid, {}).get("r2") for cid in cids]),
                    "r4_mean": mean([kit.scores[judge].get(cid, {}).get("r4") for cid in cids]),
                    "r5_mean": mean([kit.scores[judge].get(cid, {}).get("r5") for cid in cids])}
    return out


def family_bias(kit):
    """judge mean minus the other judge's mean on the cards both scored, per arm; own family flagged."""
    out = {}
    for judge in JUDGES:
        other = next(j for j in JUDGES if j != judge)
        rows = {}
        for arm in ARMS:
            both = [cid for cid, k in kit.key.items() if k["arm"] == arm
                    and cid in kit.scores[judge] and cid in kit.scores[other]]
            row = {"cards": len(both)}
            for metric in ("A", "r2"):
                if metric == "A":
                    mine = [kit.a_judge(judge, cid, "all") for cid in both]
                    theirs = [kit.a_judge(other, cid, "all") for cid in both]
                else:
                    pairs = [(kit.scores[judge][cid]["r2"], kit.scores[other][cid]["r2"]) for cid in both
                             if kit.scores[judge][cid]["r2"] is not None and kit.scores[other][cid]["r2"] is not None]
                    mine, theirs = [p[0] for p in pairs], [p[1] for p in pairs]
                m, t = mean(mine), mean(theirs)
                row[metric] = None if m is None or t is None else round(m - t, 4)
            rows[arm] = row
        fam = FAMILY[judge]
        did = {}
        for metric in ("A", "r2"):
            mine = [rows[a][metric] for a in ARMS if a in fam and rows[a][metric] is not None]
            others = [rows[a][metric] for a in ARMS if a not in fam and rows[a][metric] is not None]
            did[metric] = (None if not mine or not others
                           else round(statistics.mean(mine) - statistics.mean(others), 4))
        out[judge] = {"family_arm": list(fam), "by_arm": rows, "family_minus_other_arms": did}
    return out


def agreement(kit, user_sample):
    both = [cid for cid in kit.key if cid in kit.scores["opus"] and cid in kit.scores["sol"]]
    a_pairs = [(kit.a_judge("opus", cid, "all"), kit.a_judge("sol", cid, "all")) for cid in both]

    def r1(judge, cid):
        own = kit.phase1[judge].get(kit.key[cid]["unit"]) or kit.gab.get(kit.key[cid]["unit"])
        return None if own is None else int(kit.klass(cid, "all") in gab_classes(own))
    r1_pairs = [(r1("opus", cid), r1("sol", cid)) for cid in kit.key]
    r2_pairs = [(kit.scores["opus"][cid]["r2"], kit.scores["sol"][cid]["r2"]) for cid in both]
    units = sorted(set(kit.phase1["opus"]) & set(kit.phase1["sol"]))
    gab_same = [set(kit.phase1["opus"][u]["acceptable_classes"]) == set(kit.phase1["sol"][u]["acceptable_classes"])
                for u in units]
    per_class = {}
    for klass in ("PR", "ISSUE", "NO_ACTION"):
        per_class[klass] = kappa([(klass in kit.phase1["opus"][u]["acceptable_classes"],
                                   klass in kit.phase1["sol"][u]["acceptable_classes"]) for u in units])
    jacc = [len(set(kit.phase1["opus"][u]["acceptable_classes"]) & set(kit.phase1["sol"][u]["acceptable_classes"]))
            / len(set(kit.phase1["opus"][u]["acceptable_classes"]) | set(kit.phase1["sol"][u]["acceptable_classes"]))
            for u in units]
    model = model_final(kit, "all")
    sample = [cid for cid in user_sample if cid in kit.user and cid in kit.key]
    user_a = [(kit.a_user(cid, "all"), model[cid]) for cid in sample]

    def user_r1(cid):
        label = kit.user[cid]
        if label.get("class_acceptable") in ("yes", "no"):
            return int(label["class_acceptable"] == "yes")
        return int(kit.klass(cid, "all") in gab_classes(kit.gab.get(kit.key[cid]["unit"])))
    user_r1_pairs = [(user_r1(cid), int(kit.klass(cid, "all") in gab_classes(kit.gab.get(kit.key[cid]["unit"]))))
                     for cid in sample if kit.key[cid]["unit"] in kit.gab]
    final_r2 = lambda cid: (kit.scores[PRINCIPAL].get(cid) or kit.scores["sol"].get(cid) or {}).get("r2")  # noqa: E731
    user_r2 = [(kit.user[cid]["r2"], final_r2(cid)) for cid in sample]
    k_user = kappa(user_a)
    return {
        "judges": {"cards_both": len(both), "kappa_A": kappa(a_pairs),
                   "kappa_R1_own_answer_keys": kappa(r1_pairs),
                   "weighted_kappa_R2_quadratic": weighted_kappa(r2_pairs),
                   "A_disagreements": sum(a != b for a, b in a_pairs if a is not None and b is not None)},
        "answer_keys": {"units": len(units), "same_set": sum(gab_same), "kappa_per_class": per_class,
                        "mean_jaccard": mean(jacc)},
        "user": {"cards": len(sample), "kappa_A_vs_model_final": k_user,
                 "kappa_R1": kappa(user_r1_pairs), "weighted_kappa_R2": weighted_kappa(user_r2),
                 "decision": kappa_row(k_user)},
    }


def report(root: Path):
    kit = Kit(root)
    first = {r: final_a(kit, set(), r) for r in READINGS}
    x0 = per_unit(kit, first)
    losses, gains = loss_calls(x0, {k["unit"] for k in kit.key.values()})
    loss_units = {l["unit"] for l in losses}
    finals = {r: final_a(kit, loss_units, r) for r in READINGS}
    x = per_unit(kit, finals)
    losses, gains = loss_calls(x, {k["unit"] for k in kit.key.values()})
    sample_path = root / "user_sample.json"
    sample = load(sample_path)["sample"] if sample_path.is_file() else []
    arms = {}
    for arm in ARMS:
        for reading in (READINGS if arm in COGNALYS else ("all",)):
            label = arm if arm not in COGNALYS else f"{arm}_{reading}"
            units = x.get((arm, reading), {})
            arms[label] = {"units": len(units), "x_mean_over_units": mean([v["x"] for v in units.values()]),
                           **secondary(kit, finals, reading, arm)}
    disagreements = [{"card_id": cid, "unit": kit.key[cid]["unit"], "in_loss_unit": kit.key[cid]["unit"] in loss_units,
                      "decided_by": src} for cid, (_, src) in finals["all"].items() if src in ("principal", "user")]
    result = {
        "answer_keys_fixed": len(kit.gab), "stale_scores": kit.stale,
        "cards": {"total": len(kit.key), "judged_deliverables": sum(c["judged"] for c in kit.cards["cards"]),
                  "scored_by": {j: len(kit.scores[j]) for j in JUDGES}},
        "final_a": {r: {cid: {"A": v, "source": s} for cid, (v, s) in finals[r].items()} for r in READINGS},
        "x": {f"{a}_{r}": v for (a, r), v in x.items()},
        "arms": arms, "candidate_losses": losses, "candidate_gains": gains,
        "disagreements": disagreements, "per_judge": per_judge(kit), "family_bias": family_bias(kit),
        "agreement": agreement(kit, sample),
    }
    tag = names(root)[2]
    dump(root / f"tabela{tag}.json", result)
    (root / f"tabela{tag}.md").write_text(markdown(result), encoding="utf-8")
    return result


def fmt(v):
    return "—" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def markdown(r):
    md = ["# Julgamento cego: notas (§3.6, §3.7)", "",
          f"Gabaritos fixados pelo usuário: {r['answer_keys_fixed']}. Cartões: {r['cards']['total']} "
          f"({r['cards']['judged_deliverables']} julgáveis); notas de opus {r['cards']['scored_by']['opus']}, "
          f"de sol {r['cards']['scored_by']['sol']}; notas obsoletas (cartão mudou): {len(r['stale_scores'])}.", "",
          "| braço | unidades | média de X | A médio | R1 | PRs | R2 médio | build ok | R4 | R5 | PR ruim | "
          "dispensa indevida | issue sem ação |", "|---|" + "---|" * 12]
    for label, s in r["arms"].items():
        md.append(f"| {label} | {s['units']} | {fmt(s['x_mean_over_units'])} | {fmt(s['a_mean'])} | "
                  f"{fmt(s['r1_class_right'])} | {s['pr']} | {fmt(s['r2_mean_pr'])} | {s['build_ok_pr']} | "
                  f"{fmt(s['r4_mean'])} | {fmt(s['r5_mean'])} | {s['pr_ruim_r2_0']} | {s['dispensa_indevida']} | "
                  f"{s['issue_sem_acao']} |")
    md += ["", "C_all = leitura \"todo rascunho pronto\"; C_publish = \"o que publicaria\".", "",
           f"Perdas candidatas: {', '.join(l['unit'] + ' (' + l['agent'] + ')' for l in r['candidate_losses']) or 'nenhuma'}. "
           f"Ganhos candidatos: {', '.join(g['unit'] for g in r['candidate_gains']) or 'nenhum'}.", "",
           f"Discordâncias em A entre os juízes: {len(r['disagreements'])} "
           f"({sum(d['in_loss_unit'] for d in r['disagreements'])} em unidades com perda candidata).", "",
           "## Notas por juiz", "", "| juiz | braço | cartões | A | R2 | R4 | R5 |", "|---|---|---|---|---|---|---|"]
    for judge, rows in r["per_judge"].items():
        for arm, s in rows.items():
            md.append(f"| {judge} | {arm} | {s['scored_cards']} | {fmt(s['a_mean'])} | {fmt(s['r2_mean'])} | "
                      f"{fmt(s['r4_mean'])} | {fmt(s['r5_mean'])} |")
    md += ["", "## Viés de família", "",
           "Diferença entre a média do juiz e a do outro juiz nos mesmos cartões; a linha da família do juiz é a que "
           "importa; \"menos os outros braços\" desconta a leniência geral.", "",
           "| juiz | braço | cartões | ΔA | ΔR2 |", "|---|---|---|---|---|"]
    for judge, b in r["family_bias"].items():
        for arm, row in b["by_arm"].items():
            mark = " (família)" if arm in b["family_arm"] else ""
            md.append(f"| {judge} | {arm}{mark} | {row['cards']} | {fmt(row['A'])} | {fmt(row['r2'])} |")
        md.append(f"| {judge} | família menos os outros braços | | {fmt(b['family_minus_other_arms']['A'])} | "
                  f"{fmt(b['family_minus_other_arms']['r2'])} |")
    ag = r["agreement"]
    md += ["", "## Concordância", "",
           f"- Juízes ({ag['judges']['cards_both']} cartões): kappa em A {fmt(ag['judges']['kappa_A'])}; "
           f"em R1 (gabarito de cada juiz) {fmt(ag['judges']['kappa_R1_own_answer_keys'])}; "
           f"kappa ponderado quadrático em R2 {fmt(ag['judges']['weighted_kappa_R2_quadratic'])}.",
           f"- Gabaritos da fase 1 ({ag['answer_keys']['units']} unidades): mesmo conjunto em "
           f"{ag['answer_keys']['same_set']}; kappa por classe {json.dumps(ag['answer_keys']['kappa_per_class'])}; "
           f"Jaccard médio {fmt(ag['answer_keys']['mean_jaccard'])}.",
           f"- Usuário contra a nota final dos modelos ({ag['user']['cards']} cartões): kappa em A "
           f"{fmt(ag['user']['kappa_A_vs_model_final'])}; em R1 {fmt(ag['user']['kappa_R1'])}; ponderado em R2 "
           f"{fmt(ag['user']['weighted_kappa_R2'])}. Linha da tabela da §3.7: {ag['user']['decision']}.", ""]
    return "\n".join(md) + "\n"


# ------------------------------------------------------------------ user sample
def user_sample(root: Path, force=False, salt=None, units_info=None, rubric=None):
    kit = Kit(root)
    salt = salt or C.seed_info()["salt"]
    first = {rd: final_a(kit, set(), rd) for rd in READINGS}
    losses, _ = loss_calls(per_unit(kit, first), {k["unit"] for k in kit.key.values()})
    loss_units = {l["unit"] for l in losses}
    judged = sorted(c["card_id"] for c in kit.cards["cards"] if c["judged"])
    loss_cards = [cid for cid in judged if kit.key[cid]["unit"] in loss_units]
    C.rng(salt, "user|loss").shuffle(loss_cards)
    per_arm, chosen = collections.Counter(), []
    for cid in loss_cards:
        if len(chosen) >= 20:
            break
        if per_arm[kit.key[cid]["arm"]] < 10:
            chosen.append(cid)
            per_arm[kit.key[cid]["arm"]] += 1
    rest = [cid for cid in judged if cid not in chosen]
    C.rng(salt, "user|rest").shuffle(rest)
    for cid in rest:
        if per_arm[kit.key[cid]["arm"]] < 10:
            chosen.append(cid)
            per_arm[kit.key[cid]["arm"]] += 1
    decide = [cid for cid, (_, src) in final_a(kit, set(), "all").items()
              if src == "principal" and kit.key[cid]["unit"] in loss_units and cid not in chosen]
    form_ids = chosen + decide
    C.rng(salt, "user|form").shuffle(form_ids)
    dump(root / "user_sample.json", {"sample": chosen, "decide_disagreements": decide,
                                     "loss_units": sorted(loss_units), "per_arm": dict(per_arm),
                                     "rule": "10 per arm; candidate-loss units' cards first, up to 20; seeded"})
    labels = root / "user_labels.csv"
    if labels.is_file() and read_user_labels(labels) and not force:
        print(f"{labels} already has filled rows; not overwritten (--force)")
    else:
        with open(labels, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=LABEL_FIELDS)
            writer.writeheader()
            for cid in sorted(form_ids, key=lambda c: (kit.key[c]["unit"], form_ids.index(c))):
                writer.writerow({"card_id": cid, "unit": kit.key[cid]["unit"]})
    import judge as J  # the same card rendering and rubric the judges get
    units_info = units_info or C.units()
    rubric = rubric or J.SPEC.read_text(encoding="utf-8").split("Notas por cartão (fase 2)")[1].split("```")[1].strip("\n")
    md = ["# Formulário do usuário (julgamento cego)", "",
          "Para cada cartão, preencha a linha dele em `user_labels.csv`: `r2` (0-3, só PR), `r4` (0-2, PR ou ISSUE), "
          "`r5` (0-2, ISSUE ou NO_ACTION), `fixes_what_is_reported` (yes/no/unclear), `regression_scenario` (quando "
          "r2 é 0 ou 1 por regressão), `notes`; opcional `class_acceptable` (yes/no) se você discorda do gabarito "
          "quanto à classe deste cartão. Nota que não se aplica fica em branco. A rubrica é a mesma dos juízes:", "",
          "```", rubric, "```", ""]
    by_unit = collections.defaultdict(list)
    for cid in form_ids:
        by_unit[kit.key[cid]["unit"]].append(cid)
    for unit in sorted(by_unit):
        info, gab = units_info.get(unit, {}), kit.gab.get(unit)
        md += [f"## Unidade {unit}", "", "Achados do RVSec:", "", "```",
               J.findings_block(info).rstrip() if info else "(unidade desconhecida)", "```", ""]
        if gab:
            md += [f"Gabarito: {', '.join(gab['acceptable_classes'])}. {gab.get('reason', '')}",
                   f"Tem de mudar: {J.listing(gab.get('must_change') or [])}",
                   f"Não pode quebrar: {J.listing(gab.get('must_not_break') or [])}", ""]
        else:
            md += ["Gabarito: ainda não fixado.", ""]
        for cid in by_unit[unit]:
            md += ["```", J.render_card(kit.by_id[cid]), "```", ""]
    (root / "user_form.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return {"sample": chosen, "decide": decide, "per_arm": dict(per_arm), "loss_units": sorted(loss_units)}


# ------------------------------------------------------------------ self-test
def self_test():
    failures = []

    def check(name, got, want):
        ok = (abs(got - want) < 1e-9) if isinstance(want, float) and got is not None else got == want
        print(("ok   " if ok else "FAIL ") + f"{name}: {got!r}" + ("" if ok else f" (want {want!r})"))
        if not ok:
            failures.append(name)

    check("kappa nominal", kappa([(1, 1), (1, 0), (0, 0), (0, 0)]), 0.5)
    check("kappa perfect", kappa([(1, 1), (0, 0), (1, 1)]), 1.0)
    check("weighted kappa example", weighted_kappa([(0, 0), (1, 2), (2, 2)]), 0.8)
    check("weighted kappa perfect", weighted_kappa([(0, 0), (3, 3), (1, 1)]), 1.0)
    check("weighted kappa independent", weighted_kappa([(0, 0), (0, 1), (1, 0), (1, 1)]), 0.0)
    g = {"PR", "NENHUMA AÇÃO"}
    check("A PR ok", acceptable("PR", {"r2": 2, "r4": 1, "r5": None}, g, "pass"), 1)
    check("A PR r2=1", acceptable("PR", {"r2": 1, "r4": 2, "r5": None}, g, "pass"), 0)
    check("A PR build fail", acceptable("PR", {"r2": 3, "r4": 2, "r5": None}, g, "fail"), 0)
    check("A PR fails without patch too", acceptable("PR", {"r2": 3, "r4": 1, "r5": None}, g,
                                                     "fails_without_patch_too"), 1)
    check("A PR r4=0", acceptable("PR", {"r2": 3, "r4": 0, "r5": None}, g, "pass"), 0)
    check("A PR not built", acceptable("PR", {"r2": 3, "r4": 2, "r5": None}, g, "not_built"), 0)
    check("A ISSUE outside key", acceptable("ISSUE", {"r2": None, "r4": 2, "r5": 2}, g, "n/a"), 0)
    check("A ISSUE ok", acceptable("ISSUE", {"r2": None, "r4": 1, "r5": 1}, {"ISSUE"}, "n/a"), 1)
    check("A NO_ACTION r5=0", acceptable("NENHUMA AÇÃO", {"r2": None, "r4": None, "r5": 0}, g, "n/a"), 0)
    check("A FALHA", acceptable("FALHA", {"r2": 3, "r4": 2, "r5": 2}, {"FALHA", "PR"}, "pass"), 0)
    check("kappa row", kappa_row(0.65)[:5], "≥ 0,6")
    check("kappa row low", kappa_row(0.39)[:5], "< 0,4")

    tmp = Path(tempfile.mkdtemp(prefix="tabulate_selftest_"))
    try:
        # Units u1 (agents good, C held for review: a candidate loss in "o que publicaria") and u2 (C 3 of 4,
        # one FAILURE: no loss); 4 replicas per arm.
        cards, key, p2 = [], {}, {j: collections.defaultdict(list) for j in JUDGES}
        sha = {}

        def add(unit, arm, r, deliverable, cls_all, cls_pub, build, opus, sol):
            cid = f"K{unit}{arm}{r}"
            cards.append({"card_id": cid, "unit": unit, "deliverable": deliverable, "title": "t", "text": "x",
                          "diff": "", "author_build": "", "judged": deliverable != "FAILURE", "sha256": "s" + cid})
            key[cid] = {"unit": unit, "arm": arm, "replica": r, "class_all": cls_all, "class_publish": cls_pub,
                        "build": build}
            for j, sc in (("opus", opus), ("sol", sol)):
                if sc is not None:
                    p2[j][unit].append({"card_id": cid, "r2": sc[0], "regression_scenario": "",
                                        "fixes_what_is_reported": "yes", "r4": sc[1], "r5": sc[2], "notes": ""})
            sha.setdefault(unit, {})[cid] = "s" + cid
            return cid

        for r in range(1, 5):
            add("u1", "S", r, "PR", "PR", "PR", "pass", (3, 2, None), (0, 2, None))     # opus loves S, sol not
            add("u1", "O", r, "PR", "PR", "PR", "pass", (2, 1, None), (2, 1, None))
            add("u1", "T", r, "PR", "PR", "PR", "pass", (3, 2, None), (3, 2, None))
            add("u1", "C", r, "PR", "PR", "FALHA", "pass", (3, 2, None), (3, 2, None))  # held for review
            add("u2", "S", r, "NO_ACTION", "NENHUMA AÇÃO", "NENHUMA AÇÃO", "n/a", (None, None, 2), (None, None, 1))
            add("u2", "O", r, "ISSUE", "ISSUE", "ISSUE", "n/a", (None, 1, 1), (None, 1, 1))
            add("u2", "T", r, "NO_ACTION", "NENHUMA AÇÃO", "NENHUMA AÇÃO", "n/a", (None, None, 1), (None, None, 1))
            if r == 1:
                add("u2", "C", r, "FAILURE", "FALHA", "FALHA", "n/a", None, None)
            else:
                add("u2", "C", r, "ISSUE", "ISSUE", "ISSUE", "n/a", (None, 1, 1), (None, 1, 1))
        dump(tmp / "cards/cards.json", {"sha256": "x", "cards": cards})
        dump(tmp / "cards/key.json", {"cards": key})
        dump(tmp / "gabarito_final.json", {
            "u1": {"acceptable_classes": ["PR"], "reason": "r", "must_change": [], "must_not_break": [],
                   "fixed_by_user": True},
            "u2": {"acceptable_classes": ["NO_ACTION", "ISSUE"], "reason": "r", "must_change": [],
                   "must_not_break": [], "fixed_by_user": True}})
        for j in JUDGES:
            dump(tmp / "phase1" / j / "u1.json", {"output": {"acceptable_classes": ["PR"]}})
            dump(tmp / "phase1" / j / "u2.json", {"output": {"acceptable_classes": ["NO_ACTION"] if j == "sol"
                                                             else ["NO_ACTION", "ISSUE"]}})
            for unit, entries in p2[j].items():
                dump(tmp / "phase2" / j / f"{unit}.json", {"unit": unit, "card_sha256": sha[unit],
                                                           "output": {"cards": entries}})
        res = report(tmp)
        check("C_all x on u1", res["x"]["C_all"]["u1"]["x"], 1.0)
        check("C_publish x on u1 (held drafts are FALHA)", res["x"]["C_publish"]["u1"]["x"], 0.0)
        check("candidate loss u1", [l["unit"] for l in res["candidate_losses"]], ["u1"])
        check("S disagreement decided by principal", res["final_a"]["all"]["Ku1S1"]["source"], "principal")
        check("S final A = principal's", res["final_a"]["all"]["Ku1S1"]["A"], 1)
        check("FAILURE mechanical", res["final_a"]["all"]["Ku2C1"]["source"], "mechanical")
        check("opus family bias on S (ΔA: +1 on u1, 0 on u2)", res["family_bias"]["opus"]["by_arm"]["S"]["A"], 0.5)
        check("sol family bias on O (ΔA)", res["family_bias"]["sol"]["by_arm"]["O"]["A"], 0.0)
        check("opus family minus others (ΔA)", res["family_bias"]["opus"]["family_minus_other_arms"]["A"], 0.5)
        check("kappa A judges", res["agreement"]["judges"]["kappa_A"] is not None, True)
        check("answer keys same set", res["agreement"]["answer_keys"]["same_set"], 1)
        check("dispensa indevida S on u2", res["arms"]["S"]["dispensa_indevida"], 0)
        check("C_publish A mean (u1 held 0 of 4, u2 3 of 4)", res["arms"]["C_publish"]["a_mean"], 0.375)
        # User sample and labels round trip: the user decides u1's S disagreements (A = 0 there).
        units_info = {u: {"rows": [{"spec": "MacSpec", "class": "a.B", "method": "m", "message": "msg"}]}
                      for u in ("u1", "u2")}
        out = user_sample(tmp, salt="selftest", units_info=units_info, rubric="R2 ...")
        check("sample takes the loss unit u1 cards first (all 16 fit under 20)",
              sum(key[c]["unit"] == "u1" for c in out["sample"]), 16)
        check("sample per arm <= 10", max(out["per_arm"].values()) <= 10, True)
        rows = list(csv.DictReader(open(tmp / "user_labels.csv", encoding="utf-8")))
        with open(tmp / "user_labels.csv", "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=LABEL_FIELDS)
            writer.writeheader()
            for row in rows:
                cid = row["card_id"]
                if key[cid]["unit"] == "u1":
                    row.update(r2="0" if key[cid]["arm"] == "S" else "3", r4="2")
                else:
                    row.update(r5="2", r4="1" if key[cid]["class_all"] == "ISSUE" else "")
                writer.writerow(row)
        res = report(tmp)
        check("user decides S disagreement in loss unit", res["final_a"]["all"]["Ku1S1"]["source"], "user")
        check("user A applied", res["final_a"]["all"]["Ku1S1"]["A"], 0)
        check("user sample kappa computed", res["agreement"]["user"]["cards"] > 0, True)
        check("form written", "=== Card" in (tmp / "user_form.md").read_text(encoding="utf-8"), True)
        # A changed card makes its old scores stale.
        cards[0]["sha256"] = "changed"
        dump(tmp / "cards/cards.json", {"sha256": "y", "cards": cards})
        check("stale scores detected", len(Kit(tmp).stale), 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"self-test: {'passed' if not failures else 'FAILED: ' + ', '.join(failures)}")
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scores of the blind judging.")
    ap.add_argument("command", nargs="?", default="report", choices=("report", "user-sample"))
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--force", action="store_true", help="user-sample: overwrite a filled user_labels.csv")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.command == "user-sample":
        if RERUN:
            raise SystemExit("user-sample reads the first cards only; unset CARDS_DIR and PHASE2")
        out = user_sample(HERE, force=args.force)
        print(f"user sample: {len(out['sample'])} cards {out['per_arm']}; loss units {out['loss_units']}; "
              f"{len(out['decide'])} disagreement card(s) to decide; wrote user_form.md, user_labels.csv")
        return 0
    res = report(HERE)
    print((HERE / f"tabela{names(HERE)[2]}.md").read_text(encoding="utf-8"))
    return 0 if res else 1


if __name__ == "__main__":
    sys.exit(main())
