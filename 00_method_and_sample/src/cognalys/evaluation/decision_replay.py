"""Send one recorded decision request again, n times: `cognalys decision-replay`.

Whether a finding's outcome is stable was measured by rerunning whole
findings (about US$ 0.39 each in the benchmark of 2026-11-08), and the
stages that decide (the adjudication and the behaviour review) were sampled
once per rerun, after a research and an extraction that also changed. This
command takes one recorded adjudication, behaviour-review or
evidence-extraction request of a finding from its run's traces (application_results/openai_prompt_logs),
rebuilds it from the request record (the same instructions, input, JSON
schema, model, reasoning effort and output ceiling) and sends that request
alone, ``samples`` times, without the local LLM cache, on the tier asked for.
Only the decision fields are compared across the answers and with the
recorded one: the adjudication's decision and current_code_is_correct_use
(and its generation, publication and cause statuses), the review's verdict
(regression, no effect, clean) and its own flags.

With --current-prompt (current_prompt_request) a behaviour-review or
extraction request is sent with the recorded input under the instructions and
JSON schema this code builds today, optionally with a section appended to the
review's data block, on the recorded model: the report says what it is a
variant of.

The rebuilt request is sent only when its input_sha256 (instructions, NUL,
input: llm_providers.trace_input_sha256) equals the recorded one, and every
sample's own trace is checked against the record again. The samples are paid
calls: the number must be given, and the run stops before a sample whose
estimated cost would pass --max-cost (the provider's own guard, charged
with each answer's usage, stops it as well).
"""
from __future__ import annotations

import dataclasses
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cognalys.llm.cost import call_cost, load_prices, price_of, run_log_dir

# The stages a replay may send, by the name the command takes, and the trace
# stage each one is recorded under.
REPLAY_STAGES = {
    "adjudication": "adjudication",
    "behavior_review": "code_generation.behavior_review",
    "evidence_extraction": "evidence_extraction",
}

# The stages whose instructions and JSON schema this code can build again
# (current_prompt_request). An extraction request recorded before schema
# names were taken from the schema's content was named
# <family>_evidence_extraction_v2; one recorded since is named
# evidence_extraction_<digest> and its family is read from the request's
# first line (finding_adjudication.extraction_request_family).
CURRENT_PROMPT_STAGES = ("behavior_review", "evidence_extraction")
_EXTRACTION_SCHEMA_SUFFIX = "_evidence_extraction_v2"
# What closes a review's recorded data block (review_patch_behavior).
_REVIEW_DATA_END = "\n</patch_and_source>"

# The fields compared across samples, per stage. "verdict" is derived
# (decision_fields); a list field is compared by its length, and
# concern_verdicts by the verdict of each concern.
DECISION_FIELDS = {
    "adjudication": ("decision", "current_code_is_correct_use", "generation_status",
                     "publication_status", "cause_status", "detector_violation",
                     "runtime_security_impact"),
    "behavior_review": ("verdict", "patch_has_no_effect", "regressions",
                        "maintainer_decision_required", "maintainer_decision_kind",
                        "current_code_is_correct_use", "concern_verdicts"),
    # violated_predicates is derived (the predicates whose status is
    # violated); function_contract is compared as dependence/counterpart/activation.
    "evidence_extraction": ("function_contract", "violated_predicates", "needs_more_evidence",
                            "requested_evidence"),
}

# What the replay's own traces are kept under, inside its output folder: not
# "openai_prompt_logs", so nothing that sums a run's calls counts them.
REPLAY_LOG_DIR = "replay_prompt_logs"
REPORT_FILE = "decision_replay.json"
# The trace stage of a sample: the recorded stage under this prefix.
SAMPLE_STAGE_PREFIX = "decision_replay."
# Input characters per token assumed when a recorded call has no usage (a
# cache hit, a replay): fewer than the 5.0-5.3 measured, so the estimate is
# high.
_CHARS_PER_TOKEN_BOUND = 3


@dataclass
class RecordedRequest:
    """One recorded model request, as its trace holds it."""

    path: Path
    call_id: str
    stage: str
    finding_id: str
    endpoint: str
    model: str
    reasoning_effort: str
    max_output_tokens: int
    timeout_seconds: int
    instructions: str
    input: str
    json_mode: bool
    json_schema: Any
    schema_name: Optional[str]
    context: Dict[str, Any]
    recorded_sha256: str
    answer: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    # Set by current_prompt_request: what this request is a variant of.
    variant: Dict[str, Any] = field(default_factory=dict)

    @property
    def rebuilt_sha256(self) -> str:
        from cognalys.llm.llm_providers import trace_input_sha256

        return trace_input_sha256(self.instructions, self.input)

    @property
    def matches_record(self) -> bool:
        return bool(self.recorded_sha256) and self.rebuilt_sha256 == self.recorded_sha256


def _response_text(response: Any) -> str:
    """The answer text of a recorded response record."""
    if not isinstance(response, dict):
        return ""
    if isinstance(response.get("output_text"), str) and response["output_text"]:
        return response["output_text"]
    texts = [content.get("text", "")
             for item in response.get("output") or [] if isinstance(item, dict)
             for content in item.get("content") or []
             if isinstance(content, dict) and content.get("type") == "output_text"]
    return "\n".join(text for text in texts if text)


def load_request(path: Path) -> RecordedRequest:
    """The request recorded at ``path`` (a *_request.json), with its answer and usage."""
    path = Path(path)
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or not isinstance(record.get("input"), str):
        raise ValueError(f"{path.name}: no recorded input")
    base = path.name[:-len("_request.json")]
    metadata: Dict[str, Any] = {}
    answer = ""
    try:
        metadata = json.loads((path.parent / f"{base}_metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    try:
        document = json.loads((path.parent / f"{base}_response.json").read_text(encoding="utf-8"))
        answer = _response_text(document.get("response") if isinstance(document, dict) else None)
    except (OSError, ValueError):
        answer = ""
    usage = {}
    if isinstance(metadata, dict) and metadata.get("delivery") in {"sent_to_openai"}:
        usage = {name: int(metadata.get(name) or 0)
                 for name in ("input_tokens", "cached_input_tokens", "output_tokens")}
    context = record.get("context") if isinstance(record.get("context"), dict) else {}
    return RecordedRequest(
        path=path, call_id=str(record.get("call_id") or base),
        stage=str(record.get("stage") or ""),
        finding_id=str(context.get("finding_id") or (metadata or {}).get("finding_id") or ""),
        endpoint=str(record.get("endpoint") or "https://api.openai.com/v1"),
        model=str(record.get("model") or ""),
        reasoning_effort=str(record.get("reasoning_effort") or ""),
        max_output_tokens=int(record.get("max_output_tokens") or 0),
        timeout_seconds=int(record.get("timeout_seconds") or 600),
        instructions=str(record.get("instructions") or ""),
        input=record["input"], json_mode=bool(record.get("json_mode")),
        json_schema=record.get("json_schema"), schema_name=record.get("schema_name"),
        context=dict(context), recorded_sha256=str(record.get("input_sha256") or ""),
        answer=answer, usage=usage,
    )


def recorded_requests(run_dir: Path, stage: str, finding: str = "") -> List[RecordedRequest]:
    """The run's recorded requests of ``stage`` (a REPLAY_STAGES name), oldest first.

    ``finding`` keeps those whose finding id equals it or, when none does,
    contains it.
    """
    if stage not in REPLAY_STAGES:
        raise ValueError(f"unknown stage {stage!r}; one of {', '.join(REPLAY_STAGES)}")
    log_dir = run_log_dir(Path(run_dir))
    found = []
    for path in sorted(log_dir.glob(f"*_{REPLAY_STAGES[stage]}_request.json")):
        try:
            request = load_request(path)
        except (OSError, ValueError):
            continue
        if request.stage == REPLAY_STAGES[stage]:
            found.append(request)
    if not finding:
        return found
    exact = [request for request in found if request.finding_id == finding]
    return exact or [request for request in found if finding in request.finding_id]


def select_request(run_dir: Path, stage: str, finding: str, call_id: str = "") -> RecordedRequest:
    """The one request a replay sends: ``call_id``, else the finding's last of the stage.

    Refuses when the finding matches more than one finding id: a replay is
    of one finding.
    """
    candidates = recorded_requests(run_dir, stage, finding)
    if call_id:
        candidates = [request for request in candidates if request.call_id == call_id]
    if not candidates:
        raise ValueError(f"no recorded {stage} request of {finding!r}"
                         + (f" with call id {call_id}" if call_id else "")
                         + f" in {run_log_dir(Path(run_dir))}")
    findings = sorted({request.finding_id for request in candidates})
    if len(findings) > 1:
        raise ValueError(f"{finding!r} names {len(findings)} findings; give one of: "
                         + "; ".join(findings))
    return candidates[-1]


def decision_fields(stage: str, answer: str) -> Dict[str, Any]:
    """The DECISION_FIELDS of one answer; {"unreadable": ...} when it is not a JSON object."""
    try:
        data = json.loads(answer)
    except (TypeError, ValueError):
        return {"unreadable": (answer or "")[:200]}
    if not isinstance(data, dict):
        return {"unreadable": str(answer)[:200]}
    fields: Dict[str, Any] = {}
    for name in DECISION_FIELDS[stage]:
        if name == "verdict":
            regressions = data.get("regressions")
            fields[name] = ("no_effect" if data.get("patch_has_no_effect")
                            else "regression" if regressions else "clean")
        elif name == "concern_verdicts":
            if isinstance(data.get(name), list):
                fields[name] = sorted(str(item.get("verdict", "")) if isinstance(item, dict)
                                      else str(item) for item in data[name])
        elif name == "function_contract":
            contract = data.get(name)
            if isinstance(contract, dict):
                fields[name] = "/".join(str(contract.get(key, "")) for key in (
                    "depends_on_reported_behavior", "counterpart", "activation"))
        elif name == "violated_predicates":
            if isinstance(data.get("predicate_assessment"), list):
                fields[name] = sum(1 for item in data["predicate_assessment"]
                                   if isinstance(item, dict) and item.get("status") == "violated")
        elif name in data:
            value = data[name]
            fields[name] = len(value) if isinstance(value, list) else value
    return fields


def agreement(counts: Counter) -> Dict[str, Any]:
    """How much the samples agree with one another, whatever the record says.

    ``pairwise``: the share of sample pairs that give the same value (Simpson's
    index; None below two samples); ``entropy_bits``: the Shannon entropy of
    the values, 0 when every sample agrees.
    """
    import math

    total = sum(counts.values())
    if not total:
        return {"pairwise": None, "entropy_bits": None}
    pairs = total * (total - 1)
    return {
        "pairwise": (round(sum(count * (count - 1) for count in counts.values()) / pairs, 4)
                     if pairs else None),
        "entropy_bits": round(abs(-sum((count / total) * math.log2(count / total)
                                       for count in counts.values())), 4),
    }


def summarize(stage: str, original: Dict[str, Any], samples: List[Dict[str, Any]]) -> Dict:
    """Per decision field: the recorded value, each sampled value's count, and whether they vary.

    With a recorded value, ``same_as_recorded_share`` is the share of samples
    that reproduce it with its Wilson 95% interval: three samples that all
    agree leave the share anywhere from 0.44 to 1, which a count alone did not
    say (PLANO_CORRECOES_DEFESA A10). ``agreement`` is how much the samples
    agree among themselves.

    A sample whose own trace did not confirm the recorded request
    (request_differs) is left out: its answer is not a replay."""
    from cognalys.reporting.finding_statistics import wilson

    answered = [sample["fields"] for sample in samples
                if "fields" in sample and not sample.get("request_differs")]
    summary = {}
    for name in DECISION_FIELDS[stage]:
        values = [fields[name] for fields in answered if name in fields]
        if not values and name not in original:
            continue
        counts = Counter(json.dumps(value, ensure_ascii=False) for value in values)
        recorded = json.dumps(original.get(name), ensure_ascii=False) if name in original else None
        same = counts.get(recorded, 0) if recorded is not None else None
        share = None
        if same is not None and values:
            low, high = wilson(same, len(values))
            share = {"share": round(same / len(values), 4), "samples": len(values),
                     "wilson95": [round(low, 4), round(high, 4)]}
        summary[name] = {
            "recorded": original.get(name),
            "samples": {key: count for key, count in counts.most_common()},
            "varies": len(counts) > 1,
            "same_as_recorded": same,
            "same_as_recorded_share": share,
            "agreement": agreement(counts),
        }
    return summary


def current_prompt_request(request: RecordedRequest, stage: str,
                           add_sections: Optional[List[Tuple[str, Any]]] = None) -> RecordedRequest:
    """``request`` under the instructions and JSON schema this code builds today for ``stage``.

    The recorded input is kept, with each of ``add_sections`` (name, value)
    appended as the last key of the review's recorded data block, in the bytes
    review_patch_behavior writes when it adds a section last; the model,
    effort and output ceiling stay the recorded ones. The record must first
    rebuild to its own digest. The variant's digest is its own, so every
    sample is checked against what is sent, and ``variant`` says what it is a
    variant of. Refuses (ValueError) a stage without a builder, sections
    outside a review, a data block that does not end in a JSON object right
    before its closing tag, and a section the block already has.
    """
    from cognalys.llm.llm_providers import trace_input_sha256
    from cognalys.llm.pipeline_common import prompt_json

    if not request.matches_record:
        raise ValueError(f"{request.call_id}: the record does not rebuild to its own "
                         "input_sha256; no variant of it is sent")
    if stage not in CURRENT_PROMPT_STAGES:
        raise ValueError(f"--current-prompt builds the instructions of "
                         f"{', '.join(CURRENT_PROMPT_STAGES)} only, not {stage!r}")
    sections = list(add_sections or [])
    if sections and stage != "behavior_review":
        raise ValueError("--add-section applies to a behavior_review request only")
    if stage == "behavior_review":
        from cognalys.llm.coordinated_generation import behavior_review_system_prompt
        from cognalys.llm.llm_schemas import behavior_review_schema

        instructions = behavior_review_system_prompt()
        schema, schema_name = behavior_review_schema(), "behavior_review"
    else:
        from cognalys.llm.finding_adjudication import (
            _REPORTED_BY_DETECTOR_EXTRACTION_RULE, evidence_extraction_system_prompt,
            extraction_request_family)
        from cognalys.llm.llm_contracts import (
            EXTRACTION_SCHEMA_STEM, evidence_extraction_schema, schema_name_by_content)

        recorded_name = str(request.schema_name or "")
        if recorded_name.endswith(_EXTRACTION_SCHEMA_SUFFIX):
            family = recorded_name[:-len(_EXTRACTION_SCHEMA_SUFFIX)]
        elif recorded_name.startswith(EXTRACTION_SCHEMA_STEM + "_"):
            family = extraction_request_family(request.input)
        else:
            family = ""
        if not family:
            raise ValueError(f"{request.call_id}: schema name {recorded_name!r} does not name "
                             "an extraction family")
        instructions = evidence_extraction_system_prompt(
            _REPORTED_BY_DETECTOR_EXTRACTION_RULE in request.instructions)
        schema = evidence_extraction_schema(family)
        schema_name = schema_name_by_content(EXTRACTION_SCHEMA_STEM, schema)
    text = request.input
    if sections:
        cut = text.rfind(_REVIEW_DATA_END)
        head = text[:cut] if cut >= 0 else ""
        if cut < 0 or not head.endswith("}"):
            raise ValueError(f"{request.call_id}: the recorded data block does not end in a "
                             "JSON object right before </patch_and_source>")
        # prompt_json writes no raw line break: the block is the last line.
        try:
            block = json.loads(head[head.rfind("\n") + 1:])
        except ValueError:
            block = None
        if not isinstance(block, dict):
            raise ValueError(f"{request.call_id}: the recorded data block is not one JSON object")
        for name, value in sections:
            if name in block:
                raise ValueError(f"{request.call_id}: the recorded data block already has a "
                                 f"section {name!r}")
            head = head[:-1] + "," + prompt_json(name) + ":" + prompt_json(value) + "}"
            block[name] = value
        text = head + text[cut:]
    return dataclasses.replace(
        request, instructions=instructions, json_schema=schema, schema_name=schema_name,
        input=text, recorded_sha256=trace_input_sha256(instructions, text),
        variant={"of_input_sha256": request.recorded_sha256, "prompt": "current",
                 "added_sections": [name for name, _ in sections]})


def estimated_sample_cost(request: RecordedRequest, prices: Dict, service_tier: str) -> float:
    """What one sample is expected to cost: the recorded call's usage, none of it cached,
    at ``service_tier``; a call without recorded usage is bounded by its characters and
    its output ceiling."""
    tier = "flex" if service_tier == "flex" else ""
    usage = request.usage
    if usage.get("input_tokens") or usage.get("output_tokens"):
        input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    else:
        input_tokens = (len(request.instructions) + len(request.input)) // _CHARS_PER_TOKEN_BOUND
        output_tokens = request.max_output_tokens or 32768
    return call_cost(request.model, input_tokens, 0, output_tokens, prices,
                     service_tier=tier) or 0.0


def _sample_trace(log_dir: Path, before: set) -> Dict[str, Any]:
    """The request record the provider wrote for the sample just sent, or {}."""
    new = sorted(path for path in log_dir.glob("*_request.json") if path not in before)
    if not new:
        return {}
    try:
        record = json.loads(new[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


def sample_cost_bound(request: RecordedRequest, prices: Dict, service_tier: str) -> float:
    """The most one sample may cost: the estimate at the standard price whatever tier
    is asked, because the provider resends a Flex request on the standard tier when
    Flex has no capacity (429) or leaves it queued past its deadline."""
    return max(estimated_sample_cost(request, prices, service_tier),
               estimated_sample_cost(request, prices, ""))


def _differs_from_record(trace: Dict[str, Any], request: RecordedRequest) -> List[str]:
    """The request fields in which a sample's own trace differs from the recorded request;
    ["trace"] when the sample wrote none, so nothing confirms what was sent."""
    if not trace:
        return ["trace"]
    expected = {"input_sha256": request.recorded_sha256, "model": request.model,
                "reasoning_effort": request.reasoning_effort,
                "max_output_tokens": request.max_output_tokens,
                "json_schema": request.json_schema, "schema_name": request.schema_name,
                "json_mode": request.json_mode}
    return [name for name, value in expected.items() if trace.get(name) != value]


def default_provider(request: RecordedRequest, log_dir: Path, service_tier: str):
    """The OpenAI client a replay sends with: the recorded model, effort, ceiling and
    endpoint, no local cache, its traces in ``log_dir``."""
    from cognalys.llm.llm_providers import OpenAIProvider

    if "anthropic" in request.endpoint:
        raise ValueError("decision-replay sends OpenAI requests only")
    return OpenAIProvider(endpoint=request.endpoint, model=request.model,
                          timeout=request.timeout_seconds,
                          max_output_tokens=request.max_output_tokens,
                          reasoning_effort=request.reasoning_effort,
                          cache_dir=None, prompt_log_dir=log_dir,
                          service_tier=service_tier)


def replay(request: RecordedRequest, stage: str, samples: int, max_cost: float,
           service_tier: str = "flex", output_dir: Optional[Path] = None,
           dry_run: bool = False, prices: Optional[Dict] = None,
           provider_factory: Optional[Callable] = None) -> Dict[str, Any]:
    """Send ``request`` ``samples`` times and report its decision fields across the answers.

    Refuses (ValueError) a request whose rebuilt input_sha256 differs from the
    recorded one, a sample count below 1, a ceiling that is not positive and a
    model without a price. A dry run checks and estimates, and sends nothing.
    """
    from cognalys.llm.cost import RunCostGuard
    from cognalys.llm.llm_providers import CallBudgetExhausted

    if stage not in REPLAY_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    if not isinstance(samples, int) or samples < 1:
        raise ValueError("--samples must be a positive number of paid calls")
    if not max_cost or max_cost <= 0:
        raise ValueError("--max-cost must be a positive number of US dollars")
    if not request.matches_record:
        raise ValueError(
            f"{request.call_id}: the rebuilt request's input_sha256 {request.rebuilt_sha256[:12]} "
            f"differs from the recorded {request.recorded_sha256[:12] or '(none)'}; not sent")
    prices = load_prices() if prices is None else prices
    if price_of(request.model, prices) is None:
        raise ValueError(f"--max-cost needs the price of {request.model} in data/llm_prices.json")
    estimate = estimated_sample_cost(request, prices, service_tier)
    bound = sample_cost_bound(request, prices, service_tier)
    original = decision_fields(stage, request.answer) if request.answer else {}
    report: Dict[str, Any] = {
        "stage": stage, "finding_id": request.finding_id, "call_id": request.call_id,
        "request": str(request.path), "model": request.model,
        "reasoning_effort": request.reasoning_effort,
        "max_output_tokens": request.max_output_tokens, "service_tier": service_tier,
        "input_sha256": request.recorded_sha256, "rebuilt_input_sha256": request.rebuilt_sha256,
        "samples_asked": samples, "max_cost_usd": max_cost,
        "estimated_cost_per_sample_usd": round(estimate, 6),
        "cost_bound_per_sample_usd": round(bound, 6),
        "recorded": original, "samples": [], "cost_usd": 0.0, "stopped": "",
    }
    if request.variant:
        report["variant"] = dict(request.variant)
    if dry_run:
        report["stopped"] = "dry run: nothing sent"
        report["summary"] = summarize(stage, original, [])
        return report
    output_dir = Path(output_dir or Path(request.path).parents[2] / "decision_replay" / (
        f"{datetime.now().strftime('%Y%m%dT%H%M%S')}_{stage}"))
    log_dir = output_dir / REPLAY_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    provider = (provider_factory or default_provider)(request, log_dir, service_tier)
    guard = RunCostGuard(max_cost, request.model, 0.0, prices)
    provider._cost_guard = guard
    context = {**request.context, "decision_replay_of": request.call_id}
    for index in range(1, samples + 1):
        # Bounded at the standard price: a Flex sample the provider resends
        # on the standard tier costs that much.
        expected = max([bound] + [item.get("cost_usd", 0.0) for item in report["samples"]])
        if guard.spent + expected > max_cost:
            report["stopped"] = (f"--max-cost: US$ {guard.spent:.4f} spent, the next sample "
                                 f"may cost up to US$ {expected:.4f}")
            break
        before = set(log_dir.glob("*_request.json"))
        spent = guard.spent
        sample: Dict[str, Any] = {"sample": index}
        try:
            answer = provider.generate(
                request.input, request.instructions, json_mode=request.json_mode,
                json_schema=request.json_schema,
                schema_name=request.schema_name or "pipeline_response",
                trace_stage=SAMPLE_STAGE_PREFIX + request.stage, trace_context=context)
            sample["fields"] = decision_fields(stage, answer)
        except CallBudgetExhausted as exc:
            report["stopped"] = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 -- one failed sample is reported, not fatal
            sample["error"] = f"{type(exc).__name__}: {exc}"[:300]
        sample["cost_usd"] = round(guard.spent - spent, 6)
        trace = _sample_trace(log_dir, before)
        if trace.get("call_id"):
            sample["call_id"] = trace["call_id"]
        differs = _differs_from_record(trace, request)
        if differs:
            # Its answer is not a replay of the recorded request: reported
            # and paid, never counted in the summary.
            sample["request_differs"] = differs
        report["samples"].append(sample)
        if differs == ["trace"]:
            report["stopped"] = ("the sample wrote no request trace, so nothing confirms "
                                 "it was the recorded request")
            break
        if differs:
            report["stopped"] = ("the sent request differs from the recorded one in "
                                 + ", ".join(differs))
            break
    report["cost_usd"] = round(guard.spent, 6)
    report["summary"] = summarize(stage, original, report["samples"])
    report["output_dir"] = str(output_dir)
    (output_dir / REPORT_FILE).write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def report_lines(report: Dict[str, Any]) -> List[str]:
    """The report as the command prints it."""
    sent = list(report["samples"])
    lines = [
        f"{report['stage']} of {report['finding_id']}",
        f"  request {report['call_id']} ({report['model']}, effort {report['reasoning_effort']}, "
        f"tier {report['service_tier']}); input_sha256 {report['input_sha256'][:12]} matches "
        "the record",
        f"  {len(sent)} of {report['samples_asked']} samples sent, US$ {report['cost_usd']:.4f} "
        f"(estimated US$ {report['estimated_cost_per_sample_usd']:.4f} each, at most "
        f"US$ {report.get('cost_bound_per_sample_usd', 0.0):.4f} on the standard tier; "
        f"ceiling US$ {report['max_cost_usd']:.2f})",
    ]
    variant = report.get("variant")
    if variant:
        added = ", ".join(variant.get("added_sections") or []) or "none"
        lines.append(
            f"  variant: this code's instructions and schema on the recorded input (recorded "
            f"input_sha256 {str(variant.get('of_input_sha256', ''))[:12]}; added sections "
            f"{added}); the recorded answer was given under the earlier prompt")
    if report.get("stopped"):
        lines.append(f"  stopped: {report['stopped']}")
    errors = [sample for sample in sent if "error" in sample]
    if errors:
        lines.append(f"  {len(errors)} sample(s) failed: {errors[0]['error']}")
    for name, entry in (report.get("summary") or {}).items():
        recorded = json.dumps(entry["recorded"], ensure_ascii=False)
        if not entry["samples"]:
            lines.append(f"  {name}: recorded {recorded}")
            continue
        values = ", ".join(f"{value} x{count}" for value, count in entry["samples"].items())
        flag = "VARIES" if entry["varies"] else "stable"
        share = entry.get("same_as_recorded_share")
        detail = ""
        if share:
            low, high = share["wilson95"]
            detail = (f"; {entry['same_as_recorded']}/{share['samples']} as recorded "
                      f"(95% CI {low:.2f}-{high:.2f})")
        pairwise = (entry.get("agreement") or {}).get("pairwise")
        if pairwise is not None:
            detail += f"; pairwise agreement {pairwise:.2f}"
        lines.append(f"  {name}: recorded {recorded}; samples {values} ({flag}){detail}")
    if report.get("output_dir"):
        lines.append(f"  written to {report['output_dir']}")
    return lines
