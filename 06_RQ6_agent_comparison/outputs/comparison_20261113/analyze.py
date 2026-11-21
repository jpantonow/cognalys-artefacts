#!/usr/bin/env python3
"""Descriptive summary of the 2026-11-13 comparison: agent arms S, O, T, the "+" arms TP, SP, and the
Cognalys arms C, D.

Per unit and arm, over the replicas: delivery class (PR / ISSUE / NENHUMA AÇÃO), whether a PR
built, diff size, seconds, tokens and API-equivalent US$.

- Agents (runs/<unit>/<arm>_r<k>/result.json, written by run_one.py). TP and SP are T and S with the
  "+" prompt (docs/preregistro_bracos_mais_20261114.md).
- Cost columns (row fields; the summary sums them):
  * cost_usd (unchanged meaning, columns "US$ total" / "US$/run"): Claude Code arms (S, SP): the cost
    Claude Code reports (list price); Codex arms (O, T, TP): tokens priced with data/llm_prices.json
    at the arm's model, standard tier, without the web search fee; a model with no price there gets
    null (no invented price). Cognalys: the unit's prompt logs (real spend: C at Flex, D at list),
    web search fee included.
  * search_fee_usd: the web search fee, billed searches x web_search_per_1k_calls / 1000 where the
    price table has the fee. Codex: the events.jsonl web_search items whose action is billed
    (cost.BILLED_WEB_SEARCH_ACTIONS, "search"; an item with no action type is billed), the rule
    cost.py applies to Cognalys's traces; result.json web_searches (every item) when there is no
    transcript. Claude Code: its reported cost already holds the fee (modelUsage costUSD = tokens +
    US$ 0.01 x webSearchRequests; checked on S co_3c8e59dbbfb4 r2, Haiku 0.0115241 = 0.0015241 +
    0.01), so it is shown, not added again. Cognalys: already in cost_usd.
  * cost_usd_api (columns "US$ API c/ busca"): cost_usd plus the fee where it was not already in;
    agents at list price, Cognalys at its real tier. Agent and Cognalys costs count the same items.
  * cost_usd_list / cost_usd_flex: every arm at the same tier, both ways. List: agents as
    cost_usd_api; C's tokens repriced at gpt-6.1-terra standard + fee; D as recorded. Flex: tokens at
    the model's flex prices + fee (the fee has no tier discount); C as recorded; null where the
    model has no Flex prices (Anthropic offers none).
  They run on subscriptions: the agents' US$ is what the same tokens would cost on the API.
- Cognalys (cognalys_arm/runs.json -> run_dir): the unit's best member outcome
  (frozen_pr_20261113/compare.py unit_outcome, OUTCOME_ORDER) mapped to a class: "PR pronto" -> PR;
  a generated fix the base tip already holds ("correção gerada, sem PR", or "correção não
  aplicável" with a solution) -> PR as well, since the agents work at the pinned commit and
  Cognalys ports to the tip, where our merged PR may already sit (raw outcome kept);
  "issue para o mantenedor do app" -> ISSUE; anything else -> NENHUMA AÇÃO. A PR there built
  (the pipeline publishes only a building draft). US$: the unit's prompt logs (Flex, real spend).

Nothing here judges quality: that is the blind judges' job. Writes analysis.json and analysis.md.
Usage (WSL): python outputs/comparison_20261113/analyze.py
Env ARMS_TAG=<tag>: Cognalys arms read from cognalys_arm_<tag> / cognalys_d_arm_<tag> (as judges/cards.py),
and the outputs are analysis_<tag>.json / .md.
"""
from __future__ import annotations

import collections
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path("/mnt/c/Users/Antonow/Documents/tcc_v3")
OUT = ROOT / "outputs/comparison_20261113"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "outputs/frozen_pr_20261113"))

import compare as cmp  # noqa: E402
from cognalys.llm.cost import BILLED_WEB_SEARCH_ACTIONS, price_of, tokens_cost, web_search_cost  # noqa: E402

ARMS = ("S", "O", "T", "TP", "SP", "C", "D")
MODELS = {"S": "claude-sonnet-5-5", "O": "gpt-6.1-sol", "T": "gpt-6.1-terra",
          "TP": "gpt-6.1-terra (+ prompt)", "SP": "claude-sonnet-5-5 (+ prompt)",
          "C": "gpt-6.1-terra (Flex)", "D": "claude-sonnet-5-5 (Cognalys)"}
ARMS_TAG = os.environ.get("ARMS_TAG", "")
# Cognalys arms: arm letter -> folder holding runs.json (arm D: Cognalys with Sonnet 5.5, 2 replicas).
COGNALYS_ARMS = {a: f + (f"_{ARMS_TAG}" if ARMS_TAG else "") for a, f in (("C", "cognalys_arm"), ("D", "cognalys_d_arm"))}
CLASSES = ("PR", "ISSUE", "NENHUMA AÇÃO")


def _round(value):
    return None if value is None else round(value, 6)


def billed_searches(r: dict, run_dir: Path):
    """(searches the fee is charged on, how they were counted) for one agent run."""
    if r.get("cli") == "codex" and (run_dir / "events.jsonl").is_file():
        billed = 0
        for line in (run_dir / "events.jsonl").read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            item = e.get("item") or {}
            if e.get("type") == "item.completed" and item.get("type") == "web_search":
                kind = (item.get("action") or {}).get("type") or "unknown"
                billed += kind in BILLED_WEB_SEARCH_ACTIONS or kind == "unknown"
        return billed, "events.jsonl search actions"
    if r.get("cli") == "claude":
        usage = r.get("model_usage") or {}
        if usage:
            return sum(int(u.get("webSearchRequests") or 0) for u in usage.values()), "modelUsage webSearchRequests"
    return int(r.get("web_searches") or 0), "result.json web_searches"


def agent_cost(r: dict, run_dir: Path | None = None) -> dict:
    """cost_usd (old meaning), search_fee_usd, cost_usd_api, cost_usd_list, cost_usd_flex of one agent run."""
    searches, counted = billed_searches(r, run_dir or Path("/nonexistent"))
    fee = web_search_cost(r["model"], searches, cmp.PRICES) if price_of(r["model"], cmp.PRICES) else None
    if r.get("cli", "claude" if r["arm"] == "S" else "codex") == "claude":
        reported = r.get("claude_reported_cost_usd")
        return {"cost_usd": reported, "billed_searches": searches, "searches_counted_from": counted,
                "search_fee_usd": _round(fee), "search_fee_in_reported_cost": True,
                "cost_usd_api": reported, "cost_usd_list": reported, "cost_usd_flex": None}
    t = r.get("tokens") or {}
    counts = (int(t.get("input_tokens") or 0), int(t.get("cached_input_tokens") or 0), int(t.get("output_tokens") or 0))
    standard = tokens_cost(r["model"], *counts, cmp.PRICES)
    has_flex = isinstance((price_of(r["model"], cmp.PRICES) or {}).get("flex"), dict)
    flex = tokens_cost(r["model"], *counts, cmp.PRICES, service_tier="flex") if has_flex else None
    api = None if standard is None else standard + (fee or 0.0)
    return {"cost_usd": standard, "billed_searches": searches, "searches_counted_from": counted,
            "search_fee_usd": _round(fee), "search_fee_in_reported_cost": False,
            "cost_usd_api": _round(api), "cost_usd_list": _round(api),
            "cost_usd_flex": None if flex is None else _round(flex + (fee or 0.0))}


def agent_rows():
    rows = []
    for path in sorted((OUT / "runs").glob("*/*/result.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        if r.get("arm") not in ARMS:
            continue
        t = r.get("tokens") or {}
        cost = agent_cost(r, path.parent)
        rows.append({"unit": r["unit"], "arm": r["arm"], "replica": r["replica"], "class": r["delivery_class"],
                     "raw": r.get("decision", ""), "built": (r.get("build") or {}).get("final_status") == "success",
                     "diff_lines": r.get("diff_lines", 0), "seconds": r.get("seconds"),
                     "input_tokens": int(t.get("input_tokens") or 0) + int(t.get("cache_creation_input_tokens") or 0)
                     + int(t.get("cache_read_input_tokens") or 0),
                     "output_tokens": int(t.get("output_tokens") or 0), **cost,
                     "web_searches": r.get("web_searches", 0), "consulted_upstream": r.get("consulted_upstream"),
                     "prompt_file": r.get("prompt_file", "prompt.txt")})
    return rows


def c_rows(arm="C"):
    path = OUT / COGNALYS_ARMS[arm] / "runs.json"
    if not path.is_file():
        return []
    units = {p.parent.name: json.loads(p.read_text()) for p in (OUT / "units").glob("*/unit.json")}
    rows = []
    for run in json.loads(path.read_text())["runs"]:
        run_dir = Path(run["run_dir"])
        if not (run_dir / "run_status.json").is_file():
            continue
        unit = units[run["unit"]]
        uids = [row["unique_error_id"] for row in unit["rows"]]
        record, outcome, _detail, _uid = cmp.unit_outcome(run_dir, {"apk": unit["apk"]}, uids)
        if outcome in {"não executado", "ausente na run"}:
            continue
        generated = outcome in cmp.GENERATED or bool(record and record.get("solution")
                                                     and outcome.startswith("correção não aplicável"))
        klass = ("PR" if outcome == "PR pronto" or generated else
                 "ISSUE" if outcome.startswith("issue") else "NENHUMA AÇÃO")
        ids = {row["finding_id"] for row in unit["rows"]}
        _src, lines = cmp.change_of(run_dir, "", ids, uids)
        cost = cmp.finding_cost(run_dir, uids)
        timings = cmp.read(run_dir / "step_timings.json") or {}
        seconds = timings.get("total_seconds") if isinstance(timings, dict) else None
        rows.append({"unit": run["unit"], "arm": arm, "replica": run["replica"], "class": klass, "raw": outcome,
                     "built": klass == "PR", "diff_lines": len(lines), "seconds": seconds,
                     "input_tokens": cost.get("input_tokens"), "output_tokens": cost.get("output_tokens"),
                     "cost_usd": cost.get("cost_usd"), **cognalys_tiers(cost),
                     "web_searches": cost.get("web_search_calls"), "consulted_upstream": None})
    return rows


def cognalys_tiers(cost: dict) -> dict:
    """search_fee_usd, cost_usd_api, cost_usd_list, cost_usd_flex of one Cognalys unit (cost_usd = real spend,
    fee included). A model with Flex prices ran at Flex (arm C): list = its tokens repriced at the standard
    prices + fee. A model without them (arm D) ran at list: flex = null."""
    real = cost.get("cost_usd")
    models = cost.get("models") or []
    out = {"billed_searches": cost.get("web_search_calls"), "searches_counted_from": "prompt traces",
           "search_fee_usd": None, "search_fee_in_reported_cost": True,
           "cost_usd_api": real, "cost_usd_list": None, "cost_usd_flex": None}
    if len(models) != 1 or price_of(models[0], cmp.PRICES) is None:
        return out
    model = models[0]
    fee = web_search_cost(model, int(cost.get("web_search_calls") or 0), cmp.PRICES)
    out["search_fee_usd"] = _round(fee)
    if isinstance(price_of(model, cmp.PRICES).get("flex"), dict):
        standard = tokens_cost(model, int(cost.get("input_tokens") or 0), int(cost.get("cached_input_tokens") or 0),
                               int(cost.get("output_tokens") or 0), cmp.PRICES,
                               cache_write_tokens=int(cost.get("cache_write_input_tokens") or 0))
        out["cost_usd_list"] = None if standard is None else _round(standard + fee)
        out["cost_usd_flex"] = real
    else:
        out["cost_usd_list"] = real
    return out


def _total(rows, field):
    values = [r.get(field) for r in rows if r.get(field) is not None]
    return (round(sum(values), 4) if values else None, len(values),
            round(sum(values) / len(values), 4) if values else None)


def summary(rows):
    out = {}
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        if not mine:
            continue
        costs = [r["cost_usd"] for r in mine if r["cost_usd"] is not None]
        secs = [r["seconds"] for r in mine if r["seconds"] is not None]
        by_unit = collections.defaultdict(list)
        for r in mine:
            by_unit[r["unit"]].append(r["class"])
        out[arm] = {
            "model": MODELS[arm], "runs": len(mine),
            "replicas": dict(sorted(collections.Counter(r["replica"] for r in mine).items())),
            "classes": {c: sum(r["class"] == c for r in mine) for c in CLASSES},
            "pr_built": sum(r["class"] == "PR" and r["built"] for r in mine),
            "units_with_any_pr": sum("PR" in v for v in by_unit.values()),
            "units_same_class_all_replicas": sum(len(set(v)) == 1 and len(v) > 1 for v in by_unit.values()),
            "units_seen": len(by_unit),
            "median_diff_lines_pr": statistics.median([r["diff_lines"] for r in mine if r["class"] == "PR"] or [0]),
            "median_seconds": statistics.median(secs) if secs else None,
            "cost_usd_total": round(sum(costs), 4) if costs else None,
            "cost_usd_priced_runs": len(costs),
            "cost_usd_mean_per_run": round(sum(costs) / len(costs), 4) if costs else None,
            "output_tokens_total": sum(int(r["output_tokens"] or 0) for r in mine),
            "web_searches": sum(int(r["web_searches"] or 0) for r in mine),
            "billed_searches": sum(int(r.get("billed_searches") or 0) for r in mine),
            "search_fee_usd_total": _total(mine, "search_fee_usd")[0],
        }
        for field in ("cost_usd_api", "cost_usd_list", "cost_usd_flex"):
            total, n, mean = _total(mine, field)
            out[arm].update({f"{field}_total": total, f"{field}_priced_runs": n, f"{field}_mean_per_run": mean})
    return out


def main() -> int:
    rows = agent_rows() + [r for arm in COGNALYS_ARMS for r in c_rows(arm)]
    summ = summary(rows)
    units = sorted({r["unit"] for r in rows})
    table = {u: {a: [r["class"][0] if r["class"] != "NENHUMA AÇÃO" else "-"
                     for r in sorted((x for x in rows if x["unit"] == u and x["arm"] == a), key=lambda x: x["replica"])]
                 for a in ARMS} for u in units}
    suffix = f"_{ARMS_TAG}" if ARMS_TAG else ""
    (OUT / f"analysis{suffix}.json").write_text(json.dumps({"summary": summ, "rows": rows}, indent=1, ensure_ascii=False),
                                       encoding="utf-8")
    md = ["# Comparação 2026-11-13: agentes (S, O, T, TP, SP) e Cognalys (C, D)", "",
          "Descritivo; qualidade fica para os juízes cegos. US$ dos agentes = equivalente de API (rodaram em "
          "assinatura); US$ do C = gasto real (Flex), do D = gasto real (lista). O sem preço na tabela: custo nulo. "
          "TP e SP = T e S com o prompt \"+\" (pré-registro docs/preregistro_bracos_mais_20261114.md).", "",
          "US$ total / US$/run: significado de antes (Codex: só tokens, sem a taxa de busca; Claude Code: custo que "
          "ele informa, que já inclui a taxa; Cognalys: gasto real com a taxa). US$ API c/ busca: o mesmo mais a "
          "taxa de busca na web onde faltava, para agentes e Cognalys contarem os mesmos itens. US$ lista/run e "
          "US$ Flex/run: todos os braços no mesmo nível de preço (Flex: nulo onde o modelo não tem preço Flex).", "",
          "| braço | modelo | runs | PR | ISSUE | sem ação | PR compila | unid. c/ algum PR | unid. estáveis | "
          "mediana linhas PR | mediana s | US$ total | US$/run | buscas cobradas | US$ API c/ busca | "
          "US$ API c/ busca/run | US$ lista/run | US$ Flex/run |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, s in summ.items():
        md.append(f"| {arm} | {s['model']} | {s['runs']} | {s['classes']['PR']} | {s['classes']['ISSUE']} | "
                  f"{s['classes']['NENHUMA AÇÃO']} | {s['pr_built']} | {s['units_with_any_pr']}/{s['units_seen']} | "
                  f"{s['units_same_class_all_replicas']} | {s['median_diff_lines_pr']} | {s['median_seconds']} | "
                  f"{s['cost_usd_total']} | {s['cost_usd_mean_per_run']} | {s['billed_searches']} | "
                  f"{s['cost_usd_api_total']} | {s['cost_usd_api_mean_per_run']} | {s['cost_usd_list_mean_per_run']} | "
                  f"{s['cost_usd_flex_mean_per_run']} |")
    md += ["", "Por unidade (réplicas em ordem; P = PR, I = issue, - = sem ação):", "",
           "| unidade | " + " | ".join(ARMS) + " |", "|---|" + "---|" * len(ARMS)]
    for u in units:
        md.append(f"| {u} | " + " | ".join("".join(table[u][a]) or "·" for a in ARMS) + " |")
    (OUT / f"analysis{suffix}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md[:18]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
