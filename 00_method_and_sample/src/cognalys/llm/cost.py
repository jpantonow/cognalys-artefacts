"""What a run's model calls cost, and a ceiling on it: `cognalys cost`, `--max-cost`.

Every call to the paid provider leaves a trace (openai_prompt_logs/
*_metadata.json) with its model, stage and token counts; nothing added them
up, so the cost of a run was known only from the provider's dashboard, a
day later and for the whole account. This adds them up per stage and per
model, and prices them with data/llm_prices.json, which the user keeps
(prices change and differ per account; none is guessed here). A model
without a price is reported in tokens, not given a cost of zero.

--max-cost sets COGNALYS_MAX_COST_USD for the run's steps. Each provider
starts from what the run's traces already cost and refuses the call that
would follow once the ceiling is reached; the refusal is a
CallBudgetExhausted, so the finding being analysed ends as
llm_budget_exhausted and so does every later one, without a request.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, Iterable, Optional

from cognalys.workspace import library_data

PRICES_FILE = "llm_prices.json"
MAX_COST_VARIABLE = "COGNALYS_MAX_COST_USD"
PRICES_VARIABLE = "COGNALYS_LLM_PRICES"
# cache_write_input_tokens: input tokens the provider wrote to its prompt
# cache, part of input_tokens like the cached ones. A trace written before it
# was recorded lacks it: the reports read it from that call's recorded
# response (_recorded_cache_writes) and, failing that, say it was not
# recorded; the --max-cost guard counts none, as it did.
_TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
                 "cache_write_input_tokens")
_CACHE_WRITES = "cache_write_input_tokens"
# The deliveries whose traces record a call a provider was paid for.
PAID_DELIVERIES = ("sent_to_openai", "sent_to_anthropic")
# A call the finding's own identical earlier call answered
# (llm_providers.finding_replay_scope): nothing was sent or billed, and the
# reports count it apart, like a cache hit.
REPLAY_DELIVERY = "finding_replay"
# A research answer served from the research corpus: nothing was sent or
# billed; counted apart from the response cache's hits.
RESEARCH_CORPUS_DELIVERY = "research_corpus"
# What the "análise interrompida" marking (reporting.finding_statistics)
# counted before Anthropic traces were summed. It keeps it, so no outcome
# moves with a reporting change. The --max-cost guard read it too until
# 2026-11-12 (PLANO_CORRECOES_DEFESA A6): a run resumed after Anthropic calls
# started from less than it had spent.
OPENAI_DELIVERIES = ("sent_to_openai",)

# The web search tool's fee, in USD per 1,000 billed calls, a model entry's
# key in llm_prices.json beside its token prices. Search content tokens are
# already in input_tokens; the fee comes on top and has no Flex discount.
WEB_SEARCH_FEE_KEY = "web_search_per_1k_calls"
# The web_search_call actions billed as a tool call. OpenAI's pricing page
# says "$10.00 / 1k calls" without defining a call, and its tool guide says
# that search actions incur the tool call cost; open_page and find_in_page
# read a page a search already returned. Over the 18 uncached finding-runs
# of 2026-11-08 (two replicas of 9 findings) that is 3.56 / 3.22 "search"
# actions per finding against 6.0 / 5.1 web_search_call items (replica 1 /
# replica 2), so billing every item would charge
# about 70% more than the documented base. An item whose action type is not
# recorded is billed, so a trace never undercounts.
BILLED_WEB_SEARCH_ACTIONS = frozenset({"search"})
# A model entry's tier prices: "flex": {input, cached_input, output,
# cache_write_input} for calls the provider served on that tier
# (response.service_tier). A tier without its own entry is billed at the
# standard prices: right for "default", an overcount for Flex without its
# block, an undercount for a tier priced above standard such as "priority",
# which is why --llm-service-tier does not offer it.
TIER_PRICE_KEYS = ("flex",)
# The trace fields that say how a call is billed beyond its tokens.
SERVICE_TIER_FIELD = "service_tier"
WEB_SEARCH_CALLS_FIELD = "web_search_calls"
# The upper bound the provider charged the --max-cost guard for a call that
# ran but reported no usage (llm_providers._unreported_usage_estimate): its
# input_tokens and output_tokens bill that call wherever the trace's own
# token fields are both 0, so a guard rebuilt from the traces by a later
# step, and `cognalys cost`, charge what the in-process guard charged.
USAGE_ESTIMATE_FIELD = "guard_usage_estimate"
# The responses a call sent again because they ended failed or cancelled
# without a content verdict (llm_providers._charge_failed_response): each
# record's token counts, tier and web search calls. What they report is
# billed on top of the call's own counts, in the reports and in a guard
# rebuilt from the traces, as the in-process guard charged it; it adds no call.
FAILED_RESPONSES_FIELD = "failed_responses"


def anthropic_web_search_requests(usage) -> int:
    """The web searches a Messages API usage reports (server_tool_use.web_search_requests).

    ``usage`` is the SDK's usage object or its dict. Anthropic bills each
    search ($10 per 1,000 on its pricing page) beside the result tokens and
    reports the count here, searches its own code execution ran (the
    dynamic filtering of web_search_20260209) included; web_fetch_requests
    carry no fee.
    """
    tools = (usage.get("server_tool_use") if isinstance(usage, dict)
             else getattr(usage, "server_tool_use", None))
    count = (tools.get("web_search_requests") if isinstance(tools, dict)
             else getattr(tools, "web_search_requests", None))
    try:
        return max(0, int(count or 0))
    except (TypeError, ValueError):
        return 0


def web_search_actions(response) -> Dict[str, int]:
    """action type -> how many web_search_call items a Responses API answer holds.

    An item without a recorded action type counts under "unknown". A
    recorded Anthropic message (a usage and no "output" list) gives its
    web_search_requests as "search" actions: Anthropic traces recorded no
    web_search_calls before 2026-11-14, so a report rebuilt from them billed
    no search fee (the D arm of 2026-11-13: 319 searches, US$ 3.19).
    """
    found: Dict[str, int] = {}
    output = response.get("output") if isinstance(response, dict) else None
    if isinstance(response, dict) and not isinstance(output, list):
        searches = anthropic_web_search_requests(response.get("usage"))
        if searches:
            found["search"] = searches
        return found
    for item in output if isinstance(output, list) else ():
        if not isinstance(item, dict) or item.get("type") != "web_search_call":
            continue
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        kind = str(action.get("type") or "unknown")
        found[kind] = found.get(kind, 0) + 1
    return found


def billed_web_search_calls(actions: Dict[str, int]) -> int:
    """The calls of ``actions`` (web_search_actions) the fee is charged on."""
    return sum(count for kind, count in actions.items()
               if kind in BILLED_WEB_SEARCH_ACTIONS or kind == "unknown")


def _price_entry(entry: dict) -> Dict[str, float]:
    price = {"input": float(entry["input"]), "output": float(entry["output"]),
             "cached_input": float(entry.get("cached_input", entry["input"]))}
    if "cache_write_input" in entry:
        price["cache_write_input"] = float(entry["cache_write_input"])
    return price


def load_prices(path: Optional[Path] = None) -> Dict[str, Dict[str, float]]:
    """Model -> USD per million input, cached_input and output tokens.

    cache_write_input (tokens written to the provider's prompt cache) is kept
    only where the entry gives it; without it writes cost the input price.
    So are the web search fee (WEB_SEARCH_FEE_KEY) and the tier prices
    (TIER_PRICE_KEYS), each kept only when it can be read whole.
    """
    path = Path(path or os.environ.get(PRICES_VARIABLE) or library_data(PRICES_FILE))
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    models = document.get("models", document) if isinstance(document, dict) else {}
    prices = {}
    for model, entry in models.items():
        if model.startswith("_") or not isinstance(entry, dict):
            continue
        try:
            prices[model] = _price_entry(entry)
        except (KeyError, TypeError, ValueError):
            continue
        try:
            if WEB_SEARCH_FEE_KEY in entry:
                prices[model][WEB_SEARCH_FEE_KEY] = float(entry[WEB_SEARCH_FEE_KEY])
        except (TypeError, ValueError):
            pass
        for tier in TIER_PRICE_KEYS:
            if isinstance(entry.get(tier), dict):
                try:
                    prices[model][tier] = _price_entry(entry[tier])
                except (KeyError, TypeError, ValueError):
                    pass
    return prices


def price_of(model: str, prices: Dict[str, Dict[str, float]]) -> Optional[Dict[str, float]]:
    """The model's price; a dated snapshot takes its family's (longest prefix)."""
    if model in prices:
        return prices[model]
    family = max((name for name in prices if model.startswith(name)), key=len, default="")
    return prices.get(family)


def tokens_cost(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int,
                prices: Dict[str, Dict[str, float]],
                cache_write_tokens: int = 0, service_tier: str = "") -> Optional[float]:
    """USD of one call's tokens; cached and written tokens are part of input_tokens.

    Writes are billed at cache_write_input, the input price when the model
    has none, so a call costs the same with or without its writes counted.
    ``service_tier`` is the tier that served the call: one with its own
    prices in the entry (TIER_PRICE_KEYS) is billed at them, any other at
    the standard prices.
    """
    price = price_of(model, prices)
    if price is None:
        return None
    tier = str(service_tier or "").strip().lower()
    if tier in TIER_PRICE_KEYS and isinstance(price.get(tier), dict):
        price = price[tier]
    uncached = max(0, input_tokens - cached_input_tokens)
    written = min(max(0, cache_write_tokens), uncached)
    return ((uncached - written) * price["input"]
            + written * price.get("cache_write_input", price["input"])
            + cached_input_tokens * price["cached_input"]
            + output_tokens * price["output"]) / 1_000_000


def web_search_cost(model: str, calls: int, prices: Dict[str, Dict[str, float]]) -> float:
    """USD of the web search fee on ``calls`` billed calls; 0 where the model has no fee."""
    price = price_of(model, prices) or {}
    return max(0, int(calls or 0)) * float(price.get(WEB_SEARCH_FEE_KEY, 0.0)) / 1000


def call_cost(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int,
              prices: Dict[str, Dict[str, float]], cache_write_tokens: int = 0,
              service_tier: str = "", web_search_calls: int = 0) -> Optional[float]:
    """tokens_cost at the served tier plus the web search fee, whose rate no tier changes."""
    tokens = tokens_cost(model, input_tokens, cached_input_tokens, output_tokens, prices,
                         cache_write_tokens=cache_write_tokens, service_tier=service_tier)
    if tokens is None:
        return None
    return tokens + web_search_cost(model, web_search_calls, prices)


def _trace_cost(model: str, counts: list, prices: Dict[str, Dict[str, float]],
                service_tier: str = "", web_search_calls: int = 0) -> Optional[float]:
    """call_cost of a trace's counts, in _TOKEN_FIELDS order."""
    input_tokens, cached_input_tokens, output_tokens, written = counts
    return call_cost(model, input_tokens, cached_input_tokens, output_tokens, prices,
                     cache_write_tokens=written, service_tier=service_tier,
                     web_search_calls=web_search_calls)


def _trace_items(log_dir: Path) -> Iterable[tuple]:
    """(metadata path, record) of each call trace in ``log_dir``."""
    for path in sorted(Path(log_dir).glob("*_metadata.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            yield path, record


def _traces(log_dir: Path) -> Iterable[dict]:
    for _path, record in _trace_items(log_dir):
        yield record


def _recorded_response(metadata_path: Path) -> Optional[dict]:
    """The provider's answer recorded beside a call's metadata, or None."""
    response_path = metadata_path.with_name(
        metadata_path.name[:-len("_metadata.json")] + "_response.json")
    try:
        document = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    response = document.get("response") if isinstance(document, dict) else None
    return response if isinstance(response, dict) else None


def _recorded_cache_writes(response: Optional[dict]) -> Optional[int]:
    """The cache writes a recorded response reports, None when it reports none.

    The OpenAI usage gives them as input_tokens_details.cache_write_tokens
    since before the trace metadata recorded them (6e278de).
    """
    usage = response.get("usage") if isinstance(response, dict) else None
    details = usage.get("input_tokens_details") if isinstance(usage, dict) else None
    if not isinstance(details, dict) or "cache_write_tokens" not in details:
        return None
    try:
        return max(0, int(details["cache_write_tokens"] or 0))
    except (TypeError, ValueError):
        return None


class _TraceBilling:
    """What a trace bills: its token counts, served tier and web search calls."""

    def __init__(self, counts: list, writes_recorded: bool, service_tier: str,
                 web_search_calls: int, estimated: bool = False):
        self.counts = counts
        self.writes_recorded = writes_recorded
        self.service_tier = service_tier
        self.web_search_calls = web_search_calls
        # The counts are the recorded upper bound (USAGE_ESTIMATE_FIELD).
        self.estimated = estimated


def _usage_estimate(record: dict, counts: list) -> Optional[tuple]:
    """(input, output) of a trace's recorded upper bound, when its own token counts are both 0."""
    estimate = record.get(USAGE_ESTIMATE_FIELD)
    if not isinstance(estimate, dict):
        return None
    if counts[_TOKEN_FIELDS.index("input_tokens")] or counts[_TOKEN_FIELDS.index("output_tokens")]:
        return None
    try:
        return (max(0, int(estimate.get("input_tokens") or 0)),
                max(0, int(estimate.get("output_tokens") or 0)))
    except (TypeError, ValueError):
        return None


def _trace_counts(path: Path, record: dict, read_responses: bool) -> _TraceBilling:
    """A trace's counts in _TOKEN_FIELDS order, whether its cache writes were recorded,
    the tier that served it and its billed web search calls.

    A trace without the cache-write field takes them from its response when
    ``read_responses``; otherwise, or when the response has none either,
    they count 0 and are reported as not recorded. A call that recorded no
    input tokens (a timeout or an error before any usage) wrote nothing to
    the cache, since writes are part of input_tokens: its 0 is recorded.
    The tier and the web search calls come from the trace's own fields; a
    trace older than them reads them from its response when
    ``read_responses`` (no tier: the standard prices; no response: no fee).
    A call that reported no tokens but whose trace records the upper bound
    the guard charged (USAGE_ESTIMATE_FIELD) is billed at that bound, in
    both modes, with no cached input and no writes.
    """
    counts = [int(record.get(name) or 0) for name in _TOKEN_FIELDS]
    estimate = _usage_estimate(record, counts)
    if estimate is not None:
        counts[_TOKEN_FIELDS.index("input_tokens")] = estimate[0]
        counts[_TOKEN_FIELDS.index("output_tokens")] = estimate[1]
        counts[_TOKEN_FIELDS.index("cached_input_tokens")] = 0
        counts[_TOKEN_FIELDS.index(_CACHE_WRITES)] = 0
        return _TraceBilling(counts, True, str(record.get(SERVICE_TIER_FIELD) or ""),
                             int(record.get(WEB_SEARCH_CALLS_FIELD) or 0), estimated=True)
    loaded: list = []

    def response() -> Optional[dict]:
        if not loaded:
            loaded.append(_recorded_response(path) if read_responses else None)
        return loaded[0]

    tier = str(record.get(SERVICE_TIER_FIELD) or "")
    if SERVICE_TIER_FIELD not in record and response() is not None:
        tier = str(response().get("service_tier") or "")
    if WEB_SEARCH_CALLS_FIELD in record:
        searches = int(record.get(WEB_SEARCH_CALLS_FIELD) or 0)
    else:
        searches = billed_web_search_calls(web_search_actions(response()))
    if _CACHE_WRITES in record:
        return _TraceBilling(counts, True, tier, searches)
    written = _recorded_cache_writes(response())
    if written is None:
        return _TraceBilling(counts, counts[_TOKEN_FIELDS.index("input_tokens")] == 0,
                             tier, searches)
    counts[_TOKEN_FIELDS.index(_CACHE_WRITES)] = written
    return _TraceBilling(counts, True, tier, searches)


def _failed_response_billings(record: dict) -> list:
    """A _TraceBilling per failed response of the trace that reported any usage.

    A response cancelled outside the call that reported none records the
    upper bound the guard charged (USAGE_ESTIMATE_FIELD), billed as a
    trace's own bound is.
    """
    billings = []
    for item in record.get(FAILED_RESPONSES_FIELD) or ():
        if not isinstance(item, dict):
            continue
        try:
            counts = [max(0, int(item.get(name) or 0)) for name in _TOKEN_FIELDS]
            searches = max(0, int(item.get(WEB_SEARCH_CALLS_FIELD) or 0))
        except (TypeError, ValueError):
            continue
        estimate = _usage_estimate(item, counts)
        if estimate is not None:
            counts = [0] * len(_TOKEN_FIELDS)
            counts[_TOKEN_FIELDS.index("input_tokens")] = estimate[0]
            counts[_TOKEN_FIELDS.index("output_tokens")] = estimate[1]
        if any(counts) or searches:
            billings.append(_TraceBilling(counts, True, str(item.get(SERVICE_TIER_FIELD) or ""),
                                          searches, estimated=estimate is not None))
    return billings


def _add_failed_responses(entries, model: str, record: dict,
                          prices: Dict[str, Dict[str, float]], unpriced: Optional[set] = None
                          ) -> None:
    """Add what a trace's failed responses used to ``entries``, as no extra call."""
    for billing in _failed_response_billings(record):
        cost = _billing_cost(model, billing, prices)
        if cost is None and unpriced is not None:
            unpriced.add(model)
        for entry in entries:
            for name, count in zip(_TOKEN_FIELDS, billing.counts):
                entry[name] += count
            entry["web_search_calls"] = entry.get("web_search_calls", 0) + billing.web_search_calls
            entry["failed_responses_billed"] = entry.get("failed_responses_billed", 0) + 1
            if entry["cost_usd"] is not None:
                entry["cost_usd"] = None if cost is None else entry["cost_usd"] + cost


def _empty() -> dict:
    # cache_writes_not_recorded: calls with input tokens whose trace and
    # response give no cache writes (traces older than the field), counted
    # as 0 written. web_search_calls: the billed ones (BILLED_WEB_SEARCH_ACTIONS).
    return {"calls": 0, "cache_hits": 0, "replays": 0, "input_tokens": 0, "cached_input_tokens": 0,
            "cache_write_input_tokens": 0, "cache_writes_not_recorded": 0,
            "output_tokens": 0, "web_search_calls": 0, "cost_usd": 0.0}


def _add_call(entry: dict, billing: _TraceBilling, cost: Optional[float]) -> None:
    entry["calls"] += 1
    for name, count in zip(_TOKEN_FIELDS, billing.counts):
        entry[name] += count
    entry["web_search_calls"] = entry.get("web_search_calls", 0) + billing.web_search_calls
    if billing.service_tier:
        tiers = entry.setdefault("service_tiers", {})
        tiers[billing.service_tier] = tiers.get(billing.service_tier, 0) + 1
    if not billing.writes_recorded:
        entry["cache_writes_not_recorded"] += 1
    if billing.estimated:
        # Calls billed at the recorded upper bound (USAGE_ESTIMATE_FIELD).
        entry["usage_estimated"] = entry.get("usage_estimated", 0) + 1
    if entry["cost_usd"] is not None:
        entry["cost_usd"] = None if cost is None else entry["cost_usd"] + cost


def _billing_cost(model: str, billing: _TraceBilling,
                  prices: Dict[str, Dict[str, float]]) -> Optional[float]:
    return _trace_cost(model, billing.counts, prices, service_tier=billing.service_tier,
                       web_search_calls=billing.web_search_calls)


def trace_call_cost(path: Path, record: dict,
                    prices: Optional[Dict[str, Dict[str, float]]] = None) -> Optional[float]:
    """What one trace's call cost, its failed responses included; None without a price."""
    prices = load_prices() if prices is None else prices
    model = str(record.get("model", "") or "unknown")
    total = _billing_cost(model, _trace_counts(Path(path), record, True), prices)
    for billing in _failed_response_billings(record):
        cost = _billing_cost(model, billing, prices)
        if total is None or cost is None:
            return None
        total += cost
    return total


def usage(log_dir: Path, prices: Optional[Dict[str, Dict[str, float]]] = None,
          deliveries: Iterable[str] = PAID_DELIVERIES, read_responses: bool = True) -> dict:
    """Calls, tokens and cost of the traces in ``log_dir``, in total, per stage and per model.

    cost_usd is None where some call's model has no price, so a partial sum
    is never shown as the cost. ``deliveries`` are the traces counted (every
    paid provider's by default); ``read_responses`` reads an older trace's
    cache writes from its response (_trace_counts).
    """
    prices = load_prices() if prices is None else prices
    deliveries = frozenset(deliveries)
    total, by_stage, by_model, unpriced = _empty(), {}, {}, set()
    for path, record in _trace_items(log_dir):
        model = str(record.get("model", "") or "unknown")
        stage = str(record.get("stage", "") or "unknown").split(".")[0]
        entries = (total, by_stage.setdefault(stage, _empty()), by_model.setdefault(model, _empty()))
        if record.get("status") == "cache_hit":
            for entry in entries:
                entry["cache_hits"] += 1
            continue
        if record.get("delivery") == REPLAY_DELIVERY:
            for entry in entries:
                entry["replays"] += 1
            continue
        if record.get("delivery") == RESEARCH_CORPUS_DELIVERY:
            for entry in entries:
                entry["corpus_hits"] = entry.get("corpus_hits", 0) + 1
            continue
        if record.get("delivery") not in deliveries or record.get("status") == "prepared":
            continue
        billing = _trace_counts(path, record, read_responses)
        cost = _billing_cost(model, billing, prices)
        if cost is None:
            unpriced.add(model)
        for entry in entries:
            _add_call(entry, billing, cost)
        _add_failed_responses(entries, model, record, prices, unpriced)
    return {**total, "by_stage": by_stage, "by_model": by_model, "unpriced_models": sorted(unpriced)}


def usage_by_finding(log_dir: Path, prices: Optional[Dict[str, Dict[str, float]]] = None,
                     deliveries: Iterable[str] = PAID_DELIVERIES,
                     read_responses: bool = True) -> dict:
    """(class, method, spec) -> calls, tokens and cost of the calls made for that finding.

    The traces name a finding by its unique id, class:::method:::spec:::...;
    calls made for no finding (a run-wide step) are left out. ``deliveries``
    and ``read_responses`` as in usage().
    """
    prices = load_prices() if prices is None else prices
    deliveries = frozenset(deliveries)
    found: dict = {}
    for path, record in _trace_items(log_dir):
        if record.get("delivery") not in deliveries or record.get("status") in {"prepared", "cache_hit"}:
            continue
        parts = str(record.get("finding_id", "")).split(":::")
        if len(parts) < 3 or not all(parts[:3]):
            continue
        entry = found.setdefault(tuple(parts[:3]), _empty())
        billing = _trace_counts(path, record, read_responses)
        _add_call(entry, billing, _billing_cost(str(record.get("model", "")), billing, prices))
        _add_failed_responses((entry,), str(record.get("model", "")), record, prices)
    return found


# What the calls' traces record about the code and the model behind them:
# the stages' prompt, schema and context versions (adjudication's
# _trace_context, issue_text) and the snapshot a provider answered with.
RECORDED_VERSION_FIELDS = ("prompt_version", "schema_version", "policy_version",
                           "context_builder_version", "semantic_resolver_version",
                           "response_model")


def recorded_versions(log_dir: Path) -> Dict[str, list]:
    """field -> the distinct values the traces in ``log_dir`` recorded for it, sorted.

    Fields no trace recorded are left out; an older trace without a field
    adds nothing to it.
    """
    found: Dict[str, set] = {}
    for record in _traces(log_dir):
        for name in RECORDED_VERSION_FIELDS:
            value = record.get(name)
            if value not in (None, ""):
                found.setdefault(name, set()).add(str(value))
    return {name: sorted(found[name]) for name in RECORDED_VERSION_FIELDS if name in found}


# The stages whose prompt version is stamped on their artifact, not on the
# call trace: field -> the artifacts (under the run's error_solutions) that
# carry it. finding_adjudication's ADJUDICATION_PROMPT_VERSION and
# PLAN_PROMPT_VERSION, coordinated_generation's REVIEW_CONTRACT_VERSION.
ARTIFACT_VERSION_FIELDS = {
    "adjudication_prompt_version": ("v2_artifacts/*/*_adjudication.json",),
    "plan_prompt_version": ("v2_artifacts/*/*_patch_plan.json",),
    "review_contract_version": ("coordinated_drafts/*/*/behavior_review.json",),
}


def recorded_artifact_versions(solutions_dir: Path) -> Dict[str, list]:
    """field -> the distinct versions the run's stage artifacts recorded, in numeric order.

    Only what the artifacts say: a run that recorded none (artifacts written
    before the stamp) lists nothing, never the current constant.
    """
    found: Dict[str, set] = {}
    for name, patterns in ARTIFACT_VERSION_FIELDS.items():
        for pattern in patterns:
            for path in sorted(Path(solutions_dir).glob(pattern)):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                value = record.get(name) if isinstance(record, dict) else None
                if value not in (None, ""):
                    found.setdefault(name, set()).add(str(value))

    def order(value: str):
        return (0, int(value), "") if value.isdigit() else (1, 0, value)

    return {name: sorted(found[name], key=order)
            for name in ARTIFACT_VERSION_FIELDS if name in found}


def short_usage(entry: dict) -> str:
    """'52 calls, 1.2M tokens, US$ 3.40' (or 'no price')."""
    tokens = entry["input_tokens"] + entry["output_tokens"]
    size = f"{tokens / 1e6:.1f}M" if tokens >= 1e6 else f"{tokens / 1e3:.0f}k"
    shared = f", shared by {entry['shared_by']} findings" if entry.get("shared_by", 1) > 1 else ""
    return f"{entry['calls']} calls, {size} tokens, {_money(entry['cost_usd'])}{shared}"


def run_log_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "application_results" / "openai_prompt_logs"


def _money(value: Optional[float]) -> str:
    return "no price" if value is None else f"US$ {value:.2f}"


def _cache_counts(entry: dict) -> str:
    """'1,200 cached, 300 written': input read from and written to the provider's cache.

    Writes no trace or response recorded (calls before the field) are 'n/r',
    not 0: '1,200 cached, n/r written', or '..., 300 written (n/r in 4 calls)'
    when only some calls lack them.
    """
    cached = f"{entry['cached_input_tokens']:,} cached, "
    missing = entry.get("cache_writes_not_recorded", 0)
    if missing and missing >= entry.get("calls", 0):
        return cached + "n/r written"
    written = f"{entry.get('cache_write_input_tokens', 0):,} written"
    return cached + written + (f" (n/r in {missing} calls)" if missing else "")


def _tiers(tiers: dict) -> str:
    """'flex 40, default 3': how many calls each provider tier served."""
    return ", ".join(f"{name} {count}" for name, count in
                     sorted(tiers.items(), key=lambda item: (-item[1], item[0])))


def summary_lines(report: dict) -> list:
    lines = []
    for stage, entry in sorted(report["by_stage"].items(), key=lambda item: -item[1]["input_tokens"]):
        lines.append(f"  {stage:28} {entry['calls']:5} calls  {entry['input_tokens']:>11,} in "
                     f"({_cache_counts(entry)})  {entry['output_tokens']:>10,} out  "
                     f"{_money(entry['cost_usd'])}")
    lines.append(f"  {'total':28} {report['calls']:5} calls  {report['input_tokens']:>11,} in "
                 f"({_cache_counts(report)})  {report['output_tokens']:>10,} out  "
                 f"{_money(report['cost_usd'])}; {report['cache_hits']} answered from the cache"
                 + (f", {report['replays']} replayed within their finding"
                    if report.get("replays") else "")
                 + (f", {report['corpus_hits']} served by the research corpus"
                    if report.get("corpus_hits") else "")
                 + (f"; {report['web_search_calls']} web search calls billed"
                    if report.get("web_search_calls") else "")
                 + (f"; served tiers {_tiers(report['service_tiers'])}"
                    if set(report.get("service_tiers") or {}) - {"default"} else "")
                 + (f"; {report['usage_estimated']} calls reported no usage and are "
                    "billed at an upper bound" if report.get("usage_estimated") else ""))
    if report["unpriced_models"]:
        lines.append(f"  no price for {', '.join(report['unpriced_models'])}: add it to "
                     f"data/{PRICES_FILE} (USD per million tokens)")
    return lines


# Characters per input token assumed when a call's most is reserved before it is
# sent (B9): fewer characters per token than English text averages, so the
# input is overestimated.
RESERVATION_CHARS_PER_TOKEN = 3.5
# Billed web searches a research call is reserved for: the 95th percentile over
# the 1355 research calls the recorded runs sent (measured 2026-11-12).
RESERVED_WEB_SEARCH_CALLS = 4
# The output a call is reserved for when it sends no ceiling (-1).
UNBOUNDED_OUTPUT_RESERVATION = 32768


class RunCostGuard:
    """Refuses model calls once a run's traces have reached the ceiling.

    Before each call the provider reserves the most it can cost
    (``reserve``): its input at RESERVATION_CHARS_PER_TOKEN characters a
    token, its whole output ceiling and, for a research call,
    RESERVED_WEB_SEARCH_CALLS searches, at the tier asked for. The call is
    made only when what was spent, what other calls hold and this reserve fit
    under the ceiling; the reserves other workers hold count, so a run stops
    before the ceiling, never after it, unless a call costs more than its
    reserve (more input tokens than the character estimate gives).
    ``add`` charges what the call cost and frees its reserve (B9, 2026-11-12).
    The ceiling used to be checked only against what was already spent, so
    the call that crossed it still ran.
    """

    def __init__(self, ceiling: float, model: str, spent: float,
                 prices: Dict[str, Dict[str, float]]):
        self.ceiling = ceiling
        self.model = model
        self.spent = spent
        self.prices = prices
        self.reserved = 0.0
        self._lock = threading.Lock()
        self._held = threading.local()

    def call_bound(self, input_characters: int, max_output_tokens: int,
                   web_search: bool = False, service_tier: str = "") -> float:
        """The most one call can cost: its input estimate, its output ceiling, its searches."""
        output = int(max_output_tokens or 0)
        cost = call_cost(
            self.model, int(input_characters / RESERVATION_CHARS_PER_TOKEN) + 1, 0,
            output if output > 0 else UNBOUNDED_OUTPUT_RESERVATION, self.prices,
            service_tier=service_tier,
            web_search_calls=RESERVED_WEB_SEARCH_CALLS if web_search else 0)
        return cost or 0.0

    def reserve(self, input_characters: int, max_output_tokens: int,
                web_search: bool = False, service_tier: str = "") -> float:
        """Hold the call's most, or refuse the call when it could pass the ceiling.

        A reserve this thread still held (its call failed before any charge)
        is freed first: a thread makes one call at a time.
        """
        from cognalys.llm.llm_providers import CallBudgetExhausted

        bound = self.call_bound(input_characters, max_output_tokens, web_search, service_tier)
        with self._lock:
            self.reserved = max(0.0, self.reserved - getattr(self._held, "amount", 0.0))
            self._held.amount = 0.0
            if self.spent + self.reserved + bound > self.ceiling:
                raise CallBudgetExhausted(
                    f"the run has spent {_money(self.spent)} of its {_money(self.ceiling)} "
                    f"ceiling (--max-cost) and the next call may cost up to {_money(bound)}; "
                    "no further model call is made")
            self.reserved += bound
            self._held.amount = bound
        return bound

    def release(self) -> None:
        """Free this thread's reserve, if it holds one."""
        with self._lock:
            self.reserved = max(0.0, self.reserved - getattr(self._held, "amount", 0.0))
            self._held.amount = 0.0

    @classmethod
    def from_environment(cls, log_dir: Optional[Path], model: str) -> Optional["RunCostGuard"]:
        raw = os.environ.get(MAX_COST_VARIABLE, "").strip()
        if not raw:
            return None
        ceiling = float(raw)
        prices = load_prices()
        if price_of(model, prices) is None:
            raise ValueError(f"--max-cost needs the price of {model} in data/{PRICES_FILE}")
        spent = 0.0
        if log_dir:
            # What `cognalys cost` reports for the same folder: every paid
            # provider's traces, an older trace's cache writes read from its
            # response. Calls of a model without a price count nothing rather
            # than making the whole sum unknown. Until 2026-11-12 only the
            # OpenAI traces' own fields were summed, so a run resumed after
            # Anthropic calls started below what it had spent.
            spent = sum(entry["cost_usd"] or 0.0
                        for entry in usage(log_dir, prices)["by_model"].values())
        return cls(ceiling, model, spent, prices)

    def check(self) -> None:
        from cognalys.llm.llm_providers import CallBudgetExhausted

        with self._lock:
            if self.spent >= self.ceiling:
                raise CallBudgetExhausted(
                    f"the run has spent {_money(self.spent)} of its {_money(self.ceiling)} "
                    "ceiling (--max-cost); no further model call is made")

    def add(self, input_tokens: int, cached_input_tokens: int, output_tokens: int,
            cache_write_tokens: int = 0, service_tier: str = "",
            web_search_calls: int = 0) -> None:
        """Charge one answered call: its tokens at the tier that served it, plus the search fee."""
        cost = call_cost(self.model, input_tokens, cached_input_tokens, output_tokens,
                         self.prices, cache_write_tokens=cache_write_tokens,
                         service_tier=service_tier, web_search_calls=web_search_calls) or 0.0
        with self._lock:
            self.spent += cost
            # The call is charged: its reserve is not held any longer.
            self.reserved = max(0.0, self.reserved - getattr(self._held, "amount", 0.0))
            self._held.amount = 0.0
