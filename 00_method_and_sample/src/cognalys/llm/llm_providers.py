#!/usr/bin/env python3
"""
LLM provider clients and response cache.

Extracted from generate_error_fixes.py (Phase 1 of the
modularization plan): the Ollama HTTP client, the paid OpenAI
Responses API client, their shared content-addressed response cache, and
the structured-logging setup they rely on.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cognalys.llm.cost import (
    FAILED_RESPONSES_FIELD, PAID_DELIVERIES, RESEARCH_CORPUS_DELIVERY,
    anthropic_web_search_requests, billed_web_search_calls, web_search_actions,
)
from cognalys.llm.research_corpus import (
    MEMO_PREFIX, RESEARCH_PASS_INSTRUCTIONS, SYNTHESIS_PASS_INSTRUCTIONS,
    SYNTHESIS_RESULT_HEADER, entry_key, pass_completed, request_identity,
)


RESEARCH_EFFORT_VARIABLE = "COGNALYS_RESEARCH_REASONING_EFFORT"

# The OpenAI processing tier a call asks for (--llm-service-tier, read by
# each step's provider). Flex bills tokens at half the standard price and
# may queue or answer 429 when capacity is short; OpenAIProvider then sends
# the same request on the standard tier (see OpenAIProvider.generate), so
# the tier changes latency and price, never what the model reads. "auto"
# sends no service_tier at all, the request of before: what a provider made
# outside `cognalys run` asks for unless the variable is set (the run's own
# default, --llm-service-tier, is flex). "priority" is not offered: it bills
# above the standard prices, and cost.py prices only the tiers in
# cost.TIER_PRICE_KEYS, billing any other at the standard prices, so the
# --max-cost guard would undercount it.
SERVICE_TIER_VARIABLE = "COGNALYS_LLM_SERVICE_TIER"
SERVICE_TIERS = ("auto", "default", "flex")
DEFAULT_SERVICE_TIER = "auto"


def configured_service_tier(service_tier: Optional[str] = None) -> str:
    """The tier to ask for: the argument, else the environment, else DEFAULT_SERVICE_TIER."""
    value = str(service_tier if service_tier is not None
                else os.environ.get(SERVICE_TIER_VARIABLE, "")).strip().lower()
    if not value:
        return DEFAULT_SERVICE_TIER
    if value not in SERVICE_TIERS:
        raise ValueError(f"unknown service tier {value!r}; one of {', '.join(SERVICE_TIERS)}")
    return value


def _frozen_experiment() -> bool:
    """Whether this process must not reach the live web.

    Read from the environment on every call rather than at import, and
    without importing the evaluation package, so this module stays usable
    on its own and a run can be frozen without restarting anything. A frozen
    experiment run with live research (run_pipeline's
    --experiment-live-research, TCC_EXPERIMENT_LIVE_RESEARCH) reaches it:
    its manifest declares that and records the research corpus it started from.
    """
    def enabled(name: str) -> bool:
        return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}

    return enabled("TCC_FROZEN_EXPERIMENT") and not enabled("TCC_EXPERIMENT_LIVE_RESEARCH")


# The last call this thread traced (_finish_trace): its id, how it was
# delivered and whether its answer ended on its own. The two-pass research
# branch reads it after each pass to decide what the research corpus may keep.
_LAST_TRACE = threading.local()


PROMPT_CACHE_BREAKPOINTS_VARIABLE = "TCC_LLM_PROMPT_CACHE_BREAKPOINTS"

# Prompt-cache boundaries in the input of every non-research OpenAI call, at
# multiples of this many characters. OpenAI looks a prefix up only at the
# incoming request's own part boundaries and finds it only where an earlier
# request wrote a breakpoint at the same position (paid probe, 2026-11-09:
# splitting only the later call reused nothing; fixed offsets in both reused
# 10,128 and 8,173 of 9,360 tokens). The value comes from replaying the
# recorded calls (2026-11-09: the 18 runs of agent_baseline_20261108, 219
# calls and 3.53M input tokens, and 16 queue runs of 05-07/11, 324 calls and
# 5.22M): with a cache entry at each earlier call's last 3 offsets and end,
# in 30 minutes, same stage, schema and instructions, the estimated cached
# tokens were, for K = 5k/10k/15k/20k/30k/40k/60k characters,
# 0.29/1.16/1.11/1.09/1.07/0.97/0.79M (experiment) and
# 0.14/1.33/1.29/1.24/1.15/1.09/1.00M (queue), against 0.44M and 0.52M
# cached today. 20k is within 6-7% of the best (10k) with half its cuts
# (500 vs 1,107 in the experiment), so fewer places where a cut could ever
# change tokenization; below 10k the last 3 offsets no longer reach back to
# where consecutive prompts diverge. The probes only showed lookups at a
# later call's marked boundaries; if OpenAI looks up only there (last 3 cuts
# and end), 10k falls to 0.70M/0.75M while 20k keeps 1.04M/1.24M and 30k
# 1.07M/1.15M, so 20k is also the safer choice.
PROMPT_CACHE_BREAKPOINT_STRIDE_CHARS = 20_000
# A request writes at most 4 cache entries, the implicit one at its end
# included, so explicit breakpoints go on the parts ending at the last 3 cuts.
PROMPT_CACHE_EXPLICIT_BREAKPOINTS = 3
# A cut sits on a single space that follows a non-space and precedes an ASCII
# letter; the space opens the next part. In the probe 24 such cuts left
# input_tokens unchanged, while cuts after "," "}" or "]" added a token.
_PROMPT_CACHE_CUT = re.compile(r"(?<=\S) (?=[A-Za-z])")


def trace_input_sha256(system_prompt: Optional[str], prompt: str) -> str:
    """The input_sha256 a request trace records: instructions, NUL, input.

    cognalys decision-replay rebuilds a recorded request and compares this
    digest with the recorded one before sending it again.
    """
    return hashlib.sha256(((system_prompt or "") + "\0" + prompt).encode("utf-8")).hexdigest()


def prompt_cache_breakpoints_enabled() -> bool:
    """False when TCC_LLM_PROMPT_CACHE_BREAKPOINTS is 0/false/no/off (read per call)."""
    return str(os.environ.get(PROMPT_CACHE_BREAKPOINTS_VARIABLE, "")).strip().lower() not in {
        "0", "false", "no", "off",
    }


# A JSON answer is read as a stream while it is generated, so one that
# degenerates into whitespace (strict JSON decoding allows any amount of it
# between tokens) is stopped instead of running to max_output_tokens. Of the
# 11,842 answers recorded under outputs/ by 2026-11-09, all 13 cut at
# max_output_tokens are code-generation answers whose longest whitespace
# run is 56,896 to 192,680 characters (87-99% of the text, starting 1-15%
# into it); among the 11,829 others one completed gpt-5.6-terra adjudication
# holds a 25,525-character run and every other run is under 500. The
# threshold sits between the two, so it stops every truncated answer and
# no complete one; 2,000 would have stopped that adjudication. The stopped
# answer's text goes back to the caller, whose shape check rejects it and
# retries as it did with the truncated one. TCC_LLM_STREAM_WATCH=0 turns it
# off.
STREAM_WATCH_VARIABLE = "TCC_LLM_STREAM_WATCH"
DEGENERATE_WHITESPACE_CHARS = 30000


def stream_watch_enabled() -> bool:
    """False when TCC_LLM_STREAM_WATCH is 0/false/no/off (read per call)."""
    return str(os.environ.get(STREAM_WATCH_VARIABLE, "")).strip().lower() not in {
        "0", "false", "no", "off",
    }


class WhitespaceRunDetector:
    """Follows streamed text and says when it ends in DEGENERATE_WHITESPACE_CHARS of whitespace."""

    def __init__(self, threshold: int = DEGENERATE_WHITESPACE_CHARS):
        self.threshold = threshold
        self.run = 0
        self.text: List[str] = []

    def feed(self, delta: str) -> bool:
        """Add ``delta``; True once the trailing whitespace run reaches the threshold."""
        if not delta:
            return self.run >= self.threshold
        self.text.append(delta)
        stripped = delta.rstrip()
        if not stripped:
            self.run += len(delta)
        else:
            self.run = len(delta) - len(stripped)
        return self.run >= self.threshold

    @staticmethod
    def longest_run(text: str) -> int:
        """The longest whitespace-only run in ``text`` (the offline check's measure)."""
        return max((len(match.group()) for match in re.finditer(r"\s+", text or "")), default=0)


def prompt_cache_cuts(prompt: str,
                      stride: int = PROMPT_CACHE_BREAKPOINT_STRIDE_CHARS) -> List[int]:
    """Where ``prompt`` is split into parts: one cut per multiple of ``stride``.

    Each cut is the last qualifying space at or before its offset and after
    the previous offset, so it depends only on the text up to just past the
    offset: two prompts sharing a prefix get the same cuts inside it. An
    offset with no qualifying space in its window gets no cut.
    """
    cuts: List[int] = []
    if stride <= 0:
        return cuts
    for offset in range(stride, len(prompt), stride):
        # +2: the cut's space at ``offset`` needs its letter in the window.
        last = None
        for match in _PROMPT_CACHE_CUT.finditer(
                prompt, offset - stride + 1, min(len(prompt), offset + 2)):
            if match.start() <= offset:
                last = match.start()
        if last is not None:
            cuts.append(last)
    return cuts


class PromptWithCacheBoundaries(str):
    """A prompt that names where its caller wants prompt-cache part boundaries.

    A stage that sends a request and then the same text with something
    appended (the evidence extraction's round 2) knows where the earlier
    request ended: that end is the one cache entry the later request can be
    sure to find, whatever the length of what follows. It is still the
    prompt string everywhere else (cache key, trace, replay); only
    prompt_cache_input reads ``cache_boundaries``. Text built from it with
    ``+`` or a slice is a plain str and carries none.
    """

    cache_boundaries: Tuple[int, ...]

    def __new__(cls, text: str, boundaries=()):
        prompt = super().__new__(cls, text)
        prompt.cache_boundaries = tuple(sorted(
            {int(boundary) for boundary in boundaries
             if 0 < int(boundary) < len(text)}))
        return prompt


def prompt_cache_input(prompt: str, stride: int = PROMPT_CACHE_BREAKPOINT_STRIDE_CHARS,
                       boundaries=None) -> Tuple[Any, List[int], List[int]]:
    """The Responses API ``input`` for ``prompt``, its cuts and its breakpoints.

    Without a cut the input is the plain string, as before. Otherwise it is
    one user message whose input_text parts join exactly to the prompt (it
    tokenizes as the string does); the parts ending at the last
    PROMPT_CACHE_EXPLICIT_BREAKPOINTS cuts carry an explicit breakpoint and
    the final part none (the implicit entry covers the end).

    ``boundaries`` (default: the prompt's own ``cache_boundaries``, see
    PromptWithCacheBoundaries) are cuts the caller places where an earlier
    request of the same text ended. The stride cuts are then computed per
    segment between them, so the segment before the first boundary is cut
    exactly as the earlier request was; the boundaries take the breakpoints
    first (the last PROMPT_CACHE_EXPLICIT_BREAKPOINTS of them) and any left
    go to the last stride cuts.
    """
    if boundaries is None:
        boundaries = getattr(prompt, "cache_boundaries", ())
    boundaries = sorted({int(boundary) for boundary in boundaries or ()
                         if 0 < int(boundary) < len(prompt)})
    cuts: List[int] = []
    start = 0
    for end in [*boundaries, len(prompt)]:
        cuts.extend(start + cut for cut in prompt_cache_cuts(prompt[start:end], stride))
        if end < len(prompt):
            cuts.append(end)
        start = end
    if not cuts:
        return prompt, [], []
    marked = boundaries[-PROMPT_CACHE_EXPLICIT_BREAKPOINTS:]
    room = PROMPT_CACHE_EXPLICIT_BREAKPOINTS - len(marked)
    others = [cut for cut in cuts if cut not in marked]
    breakpoints = sorted([*marked, *(others[-room:] if room > 0 else [])])
    parts: List[Dict[str, Any]] = []
    start = 0
    for cut in cuts:
        part: Dict[str, Any] = {"type": "input_text", "text": prompt[start:cut]}
        if cut in breakpoints:
            part["prompt_cache_breakpoint"] = {"mode": "explicit"}
        parts.append(part)
        start = cut
    parts.append({"type": "input_text", "text": prompt[start:]})
    return [{"role": "user", "content": parts}], cuts, breakpoints

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ModuleNotFoundError:
    requests = None
    HTTPAdapter = None
    Retry = None


class BackgroundResponseNeverStarted(
    requests.exceptions.Timeout if requests is not None else TimeoutError
):
    """A background response reached the deadline still "queued".

    OpenAI never started it, so it produced nothing and cost nothing, and the
    same request can be sent again. A response seen "in_progress" ran and may
    be billed or nearly done; it ends as an ordinary timeout instead.
    """

    def __init__(self, message: str, response_id: str = "", polls: int = 0):
        super().__init__(message)
        self.response_id = response_id
        self.polls = polls


class BackgroundResponseTimedOut(
    requests.exceptions.Timeout if requests is not None else TimeoutError
):
    """A background response reached the attempt's deadline and was cancelled.

    Unlike BackgroundResponseNeverStarted it may have run, and a cancelled
    Flex response was billed the whole output ceiling (paid probes,
    2026-11-09): the provider settles it and charges the --max-cost guard
    what it reports, or an upper bound. ``cancel_answer`` is what the cancel
    returned (None when it failed) and ``deadline`` the seconds that applied.
    """

    def __init__(self, message: str, response_id: str = "",
                 cancel_answer: Optional[Dict] = None, deadline: float = 0.0):
        super().__init__(message)
        self.response_id = response_id
        self.cancel_answer = cancel_answer
        self.deadline = deadline


# Codes OpenAI documents as a verdict on the request's text: the same text
# meets the same verdict on every retry. Only these make a content refusal,
# in an HTTP 4xx answer and in a failed or cancelled response alike
# (provider_error_kind). Every other code is no verdict on the text: a
# failed response is sent again (a code no list knows only once, see
# KNOWN_TRANSIENT_ERROR_CODES), a coded 4xx rejects the request.
CONTENT_VERDICT_CODES = frozenset({
    "cyber_policy", "invalid_prompt", "content_policy_violation", "content_filter",
})
# Codes saying the service has no capacity for the request just now. On the
# Flex tier the same request goes at once on the standard tier, as a Flex
# 429 does. The Flex replicates of 2026-11-09 (agent_baseline_20261108
# run_v4, run_v5) had 15 responses fail with server_is_overloaded within
# seconds of their create, each then read as a content refusal: five of
# nine findings ended "não analisado" and three lost a research pass.
CAPACITY_ERROR_CODES = frozenset({"server_is_overloaded", "resource_unavailable"})
# Codes (and no code at all) OpenAI documents as the service failing: the
# same request is sent again with the whole budget of resends. Any other
# code is "unknown": it is sent again once, and the same unknown code a
# second time is taken as a rejection of the request (ProviderRequestRejected),
# so a verdict under a code no list knows yet is not sent 15 times (four
# resends in each of a run's three passes) before anyone reads it.
KNOWN_TRANSIENT_ERROR_CODES = frozenset({
    "", "server_error", "internal_error", "rate_limit_exceeded", "timeout",
})


def provider_error_kind(code: str) -> str:
    """"content_verdict", "capacity", "transient" or "unknown" for an OpenAI error code.

    The one classification of an error code, for HTTP answers and for
    failed responses, so the two cannot drift apart again.
    """
    code = str(code or "").strip()
    if code in CONTENT_VERDICT_CODES:
        return "content_verdict"
    if code in CAPACITY_ERROR_CODES:
        return "capacity"
    if code in KNOWN_TRANSIENT_ERROR_CODES:
        return "transient"
    return "unknown"


class ProviderTransientFailure(RuntimeError):
    """The provider failed to answer, for a reason of its own, after the call's retries.

    A server fault or a capacity failure says nothing about the request: the
    same request may be answered on the next pass. Callers fail the finding
    as provider_unavailable, which a resume pass analyses again, rather than
    recording a verdict on it. It stays a RuntimeError for other callers.
    """


class BackgroundResponseFailed(ProviderTransientFailure):
    """A background response ended "failed" or "cancelled" with a code that is no content verdict.

    ``response`` is the response as it ended, for the usage it reports;
    ``polls`` the polls that read it.
    """

    def __init__(self, message: str, code: str = "", status: str = "failed",
                 response: Optional[Dict] = None, polls: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status
        self.response = dict(response or {})
        self.polls = polls

    @property
    def response_id(self) -> str:
        return str(self.response.get("id", "") or "")


class BackgroundCreateFailed(ProviderTransientFailure):
    """The POST that creates a background response failed on OpenAI's side.

    An HTTP 5xx answer to the create call means no response was created:
    nothing was generated and nothing is billed, so the same request can be
    sent again. It stays a RuntimeError with the old message for callers.
    ``code`` is the answer's error.code: a capacity code on a Flex create
    goes on the standard tier at once, as a failed Flex response does.
    """

    def __init__(self, message: str, status_code: int = 0,
                 retry_after: Optional[float] = None,
                 retry_history: Tuple[Any, ...] = (), code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.retry_history = tuple(retry_history)
        self.code = code


class BackgroundPollFailed(ProviderTransientFailure):
    """Every poll of a background response failed, MAX_CONSECUTIVE_POLL_FAILURES + 1 in a row.

    Only for failures of the service or the network (an HTTP 5xx or 429, a
    connection error, a poll timeout), after urllib3's own retries of each
    poll. The response is cancelled, as at the deadline, since it may still
    run and be billed: the provider settles it and charges the guard what it
    reports, or an upper bound. Read as a plain error it sent external
    research on with repository evidence only. ``cancel_answer`` is what the
    cancel returned (None when it failed).
    """

    def __init__(self, message: str, response_id: str = "",
                 cancel_answer: Optional[Dict] = None, polls: int = 0):
        super().__init__(message)
        self.response_id = response_id
        self.cancel_answer = cancel_answer
        self.polls = polls


class ProviderRequestRejected(RuntimeError):
    """The provider rejected the request itself, with a code that is no content verdict.

    An HTTP 4xx carrying an error code (context_length_exceeded,
    invalid_request_error, ...) or a failed response whose code no list
    knows coming back a second time: the same request meets the same answer
    on every resend and every resume, so the finding ends as a failed
    analysis that fails the step, not as provider_unavailable (resumed) nor
    as a content refusal.
    """

    def __init__(self, message: str, code: str = "", status_code: int = 0):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class _PollHTTPError(RuntimeError):
    """A poll answered with an HTTP status other than 200 (the message names it)."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class BackgroundCreateRejected(RuntimeError):
    """OpenAI refused the create POST with an HTTP 400 naming prompt_cache_breakpoint.

    Or, for a request sent as input parts, a 400 objecting to their shape
    (its error.param under "input", or a message on the cache, the
    breakpoint or input_text) that carries no content-verdict code.

    It stays a RuntimeError with the usual message for callers; the status
    and body let the provider resend the prompt as the plain string. Every
    other client error is still raised as a plain RuntimeError.
    """

    def __init__(self, message: str, status_code: int = 0, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class ServiceTierRejected(RuntimeError):
    """OpenAI refused the create POST with an HTTP 400 naming service_tier.

    The model or the account does not offer the tier asked for; nothing was
    created, so the same request goes again without the field.
    """

    def __init__(self, message: str, status_code: int = 0, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class BackgroundCreateRateLimited(RuntimeError):
    """OpenAI answered the create POST with HTTP 429 after urllib3's own retries.

    ``code`` is the error's code: rate_limit_exceeded, insufficient_quota, or
    the Flex tier's resource-unavailable answer. It stays a RuntimeError with
    the old message for callers.
    """

    def __init__(self, message: str, code: str = "", status_code: int = 429,
                 retry_after: Optional[float] = None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after


class BackgroundCreateThrottled(BackgroundCreateRateLimited, ProviderTransientFailure):
    """An HTTP 429 to a create that is no exhausted quota (rate_limit_exceeded, a Flex 429).

    On Flex the same request goes on the standard tier at once; on the
    standard tier it is sent again after the create waits (Retry-After
    honoured), as a failed response with the same rate_limit_exceeded code
    is. A Flex capacity fallback sends every concurrent call to the standard
    tier together, so a burst there meets the account's limit; raised at
    once, research went on without its web evidence and other stages
    crashed. Outlasting the waits it ends the finding as provider_unavailable.
    """


class ProviderContentRefusal(RuntimeError):
    """The provider refused the request's content, not its transport.

    OpenAI answers HTTP 400 with code "cyber_policy" when its cybersecurity
    classifier flags a prompt. That is a verdict on the text, so it is
    identical on every retry: on 2026-09-07 one such refusal arrived after
    10min26s of web search, cost the whole finding, and failed the pipeline
    step. Callers need to tell it apart from a transport error so they can
    record it, skip the retry, and carry on with the evidence they have.
    """

    def __init__(self, message: str, code: str = "", status_code: int = 0):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class ProviderAuthenticationError(RuntimeError):
    """The provider rejected the credentials, not the request.

    HTTP 401 is deterministic: the next call with the same key gets the
    same answer. Retrying it per finding only repeats the failure and, with
    research passes in front of it, pays their latency first. The provider
    remembers the first one and fails every later call without a request.
    """


class CallBudgetExhausted(RuntimeError):
    """One finding has used the model calls or model time it was allowed.

    Transport retries, schema corrections, research passes and semantic
    regenerations each have a local limit, and each limit is reasonable on
    its own; nothing bounded their product. The budget is that bound. It is
    a cut-off for runaway findings, sized well above what a finding
    normally uses, not a cost optimisation.
    """


# Defaults sized from the ledger: recent runs used 5 to 10 real calls per
# finding. The limits only end a finding that has clearly stopped
# converging; 0 disables either one.
NOT_CONTENT_VERDICT_STATUSES = frozenset({401, 408, 429})
DEFAULT_FINDING_MAX_CALLS = 60
DEFAULT_FINDING_MAX_MINUTES = 120.0
_BUDGET_STATE = threading.local()


class CallBudget:
    """Real (non-cached) model calls charged to one finding."""

    def __init__(self, max_calls: int = 0, max_seconds: float = 0.0):
        self.max_calls = max(0, int(max_calls))
        self.max_seconds = max(0.0, float(max_seconds))
        self.calls = 0
        self.failed_calls = 0
        self.transport_retries = 0
        # Calls answered by an identical earlier call of the same finding
        # (finding_replay_scope): nothing sent, so never charged.
        self.replayed_calls = 0
        self.seconds = 0.0
        self.exhausted = ""

    def charge(self) -> None:
        """Account for one call about to be sent, or refuse it."""
        if self.max_calls and self.calls >= self.max_calls:
            self.exhausted = f"{self.calls} model calls (limit {self.max_calls})"
        elif self.max_seconds and self.seconds >= self.max_seconds:
            self.exhausted = (
                f"{self.seconds / 60:.1f} minutes of model time "
                f"(limit {self.max_seconds / 60:g})"
            )
        if self.exhausted:
            raise CallBudgetExhausted(
                f"this finding has used {self.exhausted}; no further model "
                "call is made for it"
            )
        self.calls += 1

    def record(self, seconds: float, retries: int = 0, failed: bool = False) -> None:
        self.seconds += max(0.0, seconds)
        self.transport_retries += max(0, int(retries))
        if failed:
            self.failed_calls += 1

    def record_replay(self) -> None:
        self.replayed_calls += 1

    def summary(self) -> Dict:
        return {
            "calls": self.calls,
            "failed_calls": self.failed_calls,
            "transport_retries": self.transport_retries,
            "replayed_calls": self.replayed_calls,
            "model_seconds": round(self.seconds, 1),
            "max_calls": self.max_calls,
            "max_seconds": self.max_seconds,
            "exhausted": self.exhausted,
        }


def budget_limits_from_environment() -> Dict:
    """The per-finding limits, from TCC_LLM_FINDING_MAX_CALLS/_MINUTES."""
    def number(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, "") or default)
        except ValueError:
            return default
    return {
        "max_calls": int(number("TCC_LLM_FINDING_MAX_CALLS", DEFAULT_FINDING_MAX_CALLS)),
        "max_seconds": number("TCC_LLM_FINDING_MAX_MINUTES",
                              DEFAULT_FINDING_MAX_MINUTES) * 60,
    }


@contextmanager
def finding_budget(max_calls: int = 0, max_seconds: float = 0.0):
    """Charge every model call made on this thread to one budget.

    Thread-local because findings are analysed by a worker pool that shares
    one provider; a budget on the provider would pool unrelated findings.
    Calls made outside any budget are not limited.
    """
    previous = getattr(_BUDGET_STATE, "budget", None)
    budget = CallBudget(max_calls, max_seconds)
    _BUDGET_STATE.budget = budget
    try:
        yield budget
    finally:
        _BUDGET_STATE.budget = previous


def active_budget() -> Optional[CallBudget]:
    return getattr(_BUDGET_STATE, "budget", None)


_CACHE_READ_STATE = threading.local()


def cache_bypass_patterns() -> List[str]:
    """Patterns from TCC_LLM_CACHE_BYPASS_FINDINGS, one per line or ';'."""
    raw = os.environ.get("TCC_LLM_CACHE_BYPASS_FINDINGS", "")
    return [part.strip() for part in re.split(r"[;\n]", raw) if part.strip()]


def finding_bypasses_cache(error: Dict, patterns: Optional[List[str]] = None) -> bool:
    """Whether a finding's id, method or APK contains one of the patterns."""
    patterns = cache_bypass_patterns() if patterns is None else patterns
    if not patterns:
        return False
    names = [
        str(error.get(field, "") or "")
        for field in ("unique_error_id", "method_name", "source_method_name",
                      "relative_method_file")
    ]
    return any(pattern in name for pattern in patterns for name in names if name)


@contextmanager
def research_reasoning_effort(provider, effort: Optional[str] = None):
    """Run the block with ``provider`` at the research reasoning effort, if one is set.

    Web research summarizes documentation; the adjudication and generation
    that follow decide and write code. In exp_final_20261019 research was 37%
    of the model cost at the same "high" effort as those. The effort comes
    from COGNALYS_RESEARCH_REASONING_EFFORT (run_pipeline's
    --llm-research-reasoning-effort) and is part of the cache key, so answers
    at different efforts are never mixed. The provider is shared, so this is
    meant for the default single-worker LLM step.
    """
    effort = (effort or os.environ.get(RESEARCH_EFFORT_VARIABLE, "")).strip().lower()
    previous = getattr(provider, "reasoning_effort", None)
    if not effort or previous is None:
        yield
        return
    provider.reasoning_effort = _ANTHROPIC_EFFORTS.get(effort, effort) \
        if getattr(provider, "provider_name", "") == "anthropic" else effort
    try:
        yield
    finally:
        provider.reasoning_effort = previous


@contextmanager
def cache_reads_bypassed(active: bool = True):
    """Skip cache reads on this thread; responses are still written.

    Thread-local for the same reason as the budget: one provider, and its
    cache, serve a pool of findings, and only some of them are refreshed.
    """
    previous = getattr(_CACHE_READ_STATE, "bypass", False)
    _CACHE_READ_STATE.bypass = bool(active) or previous
    try:
        yield
    finally:
        _CACHE_READ_STATE.bypass = previous


_REPLAY_STATE = threading.local()


class FindingReplayMemo:
    """The successful responses of one finding's model calls, by request key.

    A replan (CoordinatedGeneration._take_new_plan, replan_after_no_effect)
    adjudicates the finding again, and its research, synthesis, extraction
    and adjudication requests are usually the very ones the first pass sent.
    A cached run answers them from the persistent cache; a run without one
    (no cache directory, --no-llm-cache, reads bypassed for the finding, web
    research at TTL 0) paid for each again, about US$ 0.20 per replanned
    finding, and sampled a new answer the rest of the replan then rested on.
    """

    def __init__(self):
        self._responses: Dict[str, Tuple[str, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Tuple[str, str]]:
        """(response, call id of the call that produced it), or None."""
        with self._lock:
            return self._responses.get(key)

    def put(self, key: str, response: str, call_id: str = "") -> None:
        if not response:
            return
        with self._lock:
            self._responses.setdefault(key, (response, call_id))

    def clear(self) -> None:
        with self._lock:
            self._responses.clear()


@contextmanager
def finding_replay_scope():
    """Answer an identical request made again within this block from its first answer.

    One scope per analysed unit (FindingProcessing.analyse_one), on this
    thread only, like the call budget: the worker pool shares one provider,
    and another finding never sees this one's answers. Only successful,
    complete responses are kept, and they are dropped when the block ends.
    The persistent cache still answers first wherever it would; the memo
    serves only what it cannot (LLMResponseCache.replay).
    """
    previous = getattr(_REPLAY_STATE, "memo", None)
    memo = FindingReplayMemo()
    _REPLAY_STATE.memo = memo
    try:
        yield memo
    finally:
        memo.clear()
        _REPLAY_STATE.memo = previous


def active_replay_memo() -> Optional[FindingReplayMemo]:
    return getattr(_REPLAY_STATE, "memo", None)


class LLMResponseCache:
    """Persistent content-addressed cache for successful LLM responses.

    It also holds the open finding's replay memo (finding_replay_scope):
    replay() and remember() key it exactly as get() and put() key the disk.
    """

    FORMAT_VERSION = 1

    def __init__(self, cache_dir: Optional[Path], logger=None):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.logger = logger or logging.getLogger("fix_analysis")
        self.hits = 0
        self.misses = 0
        self.replays = 0
        self._counter_lock = threading.Lock()
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _key(self, request: Dict) -> str:
        serialized = json.dumps(
            {"format_version": self.FORMAT_VERSION, "request": request},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, request: Dict, ttl_seconds: Optional[int] = None) -> Optional[str]:
        """Read a cached response, optionally refusing one older than a TTL.

        A TTL exists for web research, whose answer is only as good as the
        day it was fetched. Without one, that stage was simply excluded from
        the cache and re-ran in full every time -- 64% of the pipeline's LLM
        wall time in the runs of 2026-09-07, including three pairs of runs
        whose research input hashed identically and was paid for twice.
        """
        if not self.cache_dir:
            return None
        key = self._key(request)
        if getattr(_CACHE_READ_STATE, "bypass", False):
            with self._counter_lock:
                self.misses += 1
            self.logger.info("LLM cache read bypassed for this finding: %s", key[:12])
            return None
        path = self.cache_dir / key[:2] / f"{key}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            response = data.get("response")
            if not isinstance(response, str) or not response:
                raise ValueError("cached response is empty or invalid")
            if ttl_seconds is not None and self._expired(data, ttl_seconds):
                with self._counter_lock:
                    self.misses += 1
                self.logger.info(
                    "LLM cache entry %s is older than %ds; refetching",
                    key[:12], ttl_seconds,
                )
                return None
            with self._counter_lock:
                self.hits += 1
            self.logger.info("LLM cache hit: %s", key[:12])
            return response
        except FileNotFoundError:
            with self._counter_lock:
                self.misses += 1
            return None
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            with self._counter_lock:
                self.misses += 1
            self.logger.warning("Ignoring invalid LLM cache entry %s: %s", path, exc)
            return None

    def _expired(self, data: Dict, ttl_seconds: int) -> bool:
        """Whether a stored entry is older than the caller's TTL.

        An entry whose timestamp cannot be read is treated as expired: the
        conservative direction is to refetch, never to serve content of
        unknown age as if it were fresh.
        """
        stamp = str(data.get("created_at", "")).strip()
        if not stamp:
            return True
        try:
            created = datetime.fromisoformat(stamp)
        except ValueError:
            return True
        if created.tzinfo is None:
            created = created.astimezone()
        age = (datetime.now().astimezone() - created).total_seconds()
        return age > ttl_seconds

    def put(self, request: Dict, response: str) -> None:
        if not self.cache_dir or not response:
            return
        key = self._key(request)
        directory = self.cache_dir / key[:2]
        path = directory / f"{key}.json"
        temporary = directory / f".{key}.{os.getpid()}.tmp"
        payload = {
            "format_version": self.FORMAT_VERSION,
            "key": key,
            "created_at": datetime.now().astimezone().isoformat(),
            "request": request,
            "response": response,
        }
        try:
            directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, path)
        except OSError as exc:
            self.logger.warning("Could not write LLM cache entry %s: %s", path, exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def replay(self, request: Dict) -> Optional[Tuple[str, str]]:
        """The open finding's earlier answer to this exact request, with its call id.

        Asked only after get() did not answer, so a cached run records its
        cache hits as before. None outside a finding's scope.
        """
        memo = active_replay_memo()
        if memo is None:
            return None
        key = self._key(request)
        found = memo.get(key)
        if found is None:
            return None
        with self._counter_lock:
            self.replays += 1
        self.logger.info("LLM call replayed within the finding: %s", key[:12])
        return found

    def remember(self, request: Dict, response: str, call_id: str = "") -> None:
        """Keep a successful, complete response for the open finding's scope."""
        memo = active_replay_memo()
        if memo is not None and response:
            memo.put(self._key(request), response, call_id)

    def stats(self) -> Dict[str, int]:
        with self._counter_lock:
            return {"hits": self.hits, "misses": self.misses, "replays": self.replays}


# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging(output_dir: Path) -> Tuple[logging.Logger, logging.Logger]:
    """Configure structured logging to files and console."""
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Main analysis logger
    analysis_logger = logging.getLogger("fix_analysis")
    analysis_logger.setLevel(logging.DEBUG)
    analysis_handler = logging.FileHandler(log_dir / "fix_analysis.log")
    analysis_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))
    analysis_logger.addHandler(analysis_handler)

    # API calls logger
    api_logger = logging.getLogger("api_calls")
    api_logger.setLevel(logging.DEBUG)
    api_handler = logging.FileHandler(log_dir / "api_calls.log")
    api_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))
    api_logger.addHandler(api_handler)

    # Console handler for both
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    analysis_logger.addHandler(console_handler)

    return analysis_logger, api_logger


# ============================================================================
# PHASE 2: CODELLAMA PROVIDER
# ============================================================================

class OllamaProvider:
    """HTTP client for a model served by Ollama.

    codellama:7b is only the default; the model is a constructor argument
    and nothing in this class is specific to it.
    """

    provider_name = "ollama"

    def __init__(self, endpoint: str = "http://localhost:11434",
                 model: str = "codellama:7b",
                 timeout: int = 120,
                 context_length: int = 16384,
                 max_output_tokens: int = -1,
                 cache_dir: Optional[Path] = None,
                 logger: Optional[logging.Logger] = None):
        """Initialize the Ollama provider."""
        if requests is None:
            raise RuntimeError(
                "Missing dependency: requests. Install it with "
                "`python -m pip install requests`."
            )

        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        if context_length <= 0:
            raise ValueError("context_length must be greater than zero")
        self.requested_context_length = context_length
        self.context_length = context_length
        self.last_context_length = context_length
        self.max_output_tokens = max_output_tokens
        self.logger = logger or logging.getLogger("fix_analysis")
        self.cache = LLMResponseCache(cache_dir, self.logger)
        self.session = self._create_session()
        self.token_usage = {"input": 0, "output": 0}

    def _create_session(self) -> requests.Session:
        """Create requests session with retry logic."""
        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            backoff_jitter=1.0,
            # HTTP 500 is handled by generate() so it can retry with a
            # smaller context window and preserve Ollama's error message.
            status_forcelist=[429, 502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def test_connection(self) -> bool:
        """Test if Ollama is running and accessible."""
        try:
            response = self.session.get(
                f"{self.endpoint}/api/tags",
                timeout=5
            )
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"Connection test failed: {e}")
            return False

    def list_available_models(self) -> List[str]:
        """List available models in Ollama."""
        try:
            response = self.session.get(
                f"{self.endpoint}/api/tags",
                timeout=5
            )
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            self.logger.error(f"Failed to list models: {e}")
            return []

    def generate(
        self,
        prompt: str,
        system_prompt: str = None,
        json_mode: bool = False,
        json_schema: Optional[Dict] = None,
        schema_name: str = "pipeline_response",
    ) -> str:
        """
        Generate a response from the Ollama-served model.

        Args:
            prompt: User prompt
            system_prompt: System instructions

        Returns:
            Full response text
        """
        cache_request = {
            "provider": self.provider_name,
            "endpoint": self.endpoint.rstrip("/"),
            "model": self.model,
            "prompt": prompt,
            "system_prompt": system_prompt or "",
            "json_mode": json_mode,
            "json_schema": json_schema,
            "schema_name": schema_name if json_schema else None,
            "context_length": self.requested_context_length,
            "max_output_tokens": self.max_output_tokens,
            "temperature": 0.0 if json_mode else 0.7,
        }
        cached = self.cache.get(cache_request)
        if cached is not None:
            return cached
        replayed = self.cache.replay(cache_request)
        if replayed is not None:
            budget = active_budget()
            if budget is not None:
                budget.record_replay()
            return replayed[0]

        # Outside the try below, which rewraps everything as RuntimeError.
        budget = active_budget()
        if budget is not None:
            budget.charge()
        started = time.monotonic()
        failed = True
        try:
            response_text = self._generate_uncached(
                prompt, system_prompt, json_mode, json_schema, cache_request,
            )
            failed = False
            return response_text
        finally:
            if budget is not None:
                budget.record(time.monotonic() - started, failed=failed)

    def _generate_uncached(self, prompt, system_prompt, json_mode, json_schema,
                           cache_request) -> str:
        try:
            context_candidates = []
            for candidate in (
                self.context_length,
                self.context_length // 2,
                4096,
            ):
                if (
                    0 < candidate <= self.context_length
                    and candidate not in context_candidates
                ):
                    context_candidates.append(candidate)

            last_error = "unknown Ollama error"
            for index, context_length in enumerate(context_candidates):
                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "system": system_prompt or "",
                    "stream": False,
                    "options": {
                        "num_ctx": context_length,
                        "num_predict": self.max_output_tokens,
                        "temperature": 0.0 if json_mode else 0.7,
                    },
                }
                if json_mode:
                    # Modern Ollama accepts a JSON Schema in format; retain
                    # plain JSON mode as a compatibility fallback.
                    payload["format"] = json_schema or "json"
                response = self.session.post(
                    f"{self.endpoint}/api/generate",
                    json=payload,
                    timeout=self.timeout,
                )

                if response.status_code == 200:
                    self.last_context_length = context_length
                    if context_length != self.context_length:
                        self.logger.warning(
                            "Ollama succeeded after reducing context to %d tokens",
                            context_length,
                        )
                        # Keep the working size for subsequent methods instead
                        # of crashing the runner again at the original size.
                        self.context_length = context_length
                    data = response.json()
                    response_text = data.get("response", "")

                    if "eval_count" in data:
                        self.token_usage["output"] += data.get("eval_count", 0)
                    if "prompt_eval_count" in data:
                        self.token_usage["input"] += data.get("prompt_eval_count", 0)
                    self.cache.put(cache_request, response_text)
                    if data.get("done_reason", "stop") != "length":
                        self.cache.remember(cache_request, response_text)
                    return response_text

                last_error = (
                    f"API error HTTP {response.status_code}: "
                    f"{response.text.strip() or response.reason}"
                )
                has_fallback = index + 1 < len(context_candidates)
                if response.status_code == 500 and has_fallback:
                    next_context = context_candidates[index + 1]
                    self.logger.warning(
                        "Ollama returned HTTP 500 with %d-token context; "
                        "retrying with %d tokens. Response: %s",
                        context_length,
                        next_context,
                        response.text.strip() or response.reason,
                    )
                    time.sleep(2)
                    continue
                raise ValueError(last_error)

            raise ValueError(last_error)

        except requests.exceptions.Timeout:
            raise TimeoutError(f"Ollama request timed out after {self.timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise ConnectionError(f"Failed to connect to Ollama: {e}")
        except Exception as e:
            raise RuntimeError(f"Ollama generation failed: {e}")

    def get_token_usage(self) -> Dict:
        """Get accumulated token usage."""
        return self.token_usage.copy()

    def get_cache_stats(self) -> Dict:
        return self.cache.stats()


class OpenAIProvider:
    """HTTP client for paid OpenAI models through the Responses API."""

    provider_name = "openai"
    # A json_schema given with json_mode is sent as a strict text.format,
    # which the API enforces whole: a caller need not paste it in the prompt.
    enforces_json_schema = True
    # Shared research answers kept across runs (research_corpus.ResearchCorpus),
    # set by the LLM step when --research-corpus-dir is given; None: no corpus.
    research_corpus = None
    # The web search tool a research call sends; recorded in its trace
    # (web_search_tool) and part of a research corpus entry's key.
    WEB_SEARCH_TOOL = {"type": "web_search_preview", "search_context_size": "medium"}

    def __init__(self, endpoint="https://api.openai.com/v1",
                 model="gpt-6.1-terra", timeout=600,
                 max_output_tokens=32768, reasoning_effort="high",
                 cache_dir: Optional[Path] = None,
                 prompt_log_dir: Optional[Path] = None, logger=None,
                 web_search_cache_ttl_seconds: int = 7 * 24 * 3600,
                 web_search_retries: int = 1, retries: int = 2,
                 service_tier: Optional[str] = None):
        if requests is None:
            raise RuntimeError("Missing dependency: requests")
        self.api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is missing; configure .env.openai")
        # The tier every call asks for (configured_service_tier), and the
        # API's refusal of it once seen: later calls then ask for none.
        self.service_tier = configured_service_tier(service_tier)
        self._service_tier_rejected = ""
        # The API's refusal of prompt_cache_breakpoint, once seen: every later
        # call of this provider sends its prompt as the plain string.
        self._prompt_cache_breakpoints_rejected = ""
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.reasoning_effort = reasoning_effort
        self.logger = logger or logging.getLogger("fix_analysis")
        self.cache = LLMResponseCache(cache_dir, self.logger)
        self.prompt_log_dir = (
            Path(prompt_log_dir).expanduser().resolve()
            if prompt_log_dir else None
        )
        self.prompt_log_sequence = 0
        # Findings may be analysed concurrently, and every one of them shares
        # this provider. The lock covers the three pieces of provider state
        # that a call mutates: the trace sequence that names its log files,
        # the token totals, and the cache counters.
        self._state_lock = threading.Lock()
        # Named so a test can wait without waiting.
        self._sleep = time.sleep
        self.requested_context_length = None
        self.context_length = None
        self.last_context_length = None
        self.token_usage = {"input": 0, "output": 0}
        self.web_search_cache_ttl_seconds = max(0, int(web_search_cache_ttl_seconds))
        self.web_search_retries = max(0, int(web_search_retries))
        self.retries = max(0, int(retries))
        # Set by the first 401; every later call fails without a request.
        self._authentication_failure = ""
        # The run's cost ceiling (--max-cost), or None.
        from cognalys.llm.cost import RunCostGuard
        self._cost_guard = RunCostGuard.from_environment(self.prompt_log_dir, self.model)
        # Every call here is expensive: a retry re-runs a full generation at
        # the configured reasoning effort. One read failure on a WordPress
        # evidence_extraction on 2026-09-07 turned a call that took 37s on
        # its second, larger run into 626s, because a budget of four blind
        # retries treats an expensive generation like a cheap GET. Retry-After
        # is honoured here too, so a rate limit waits as long as the service
        # asks rather than as long as the backoff curve guesses.
        self.session = self._build_session(
            total=self.retries, respect_retry_after=True,
        )
        # A retried web-search call is not a cheap retry: it re-runs the
        # whole research pass at the configured reasoning effort. Measured
        # over the runs of 2026-09-07, research calls that never retried
        # averaged 62s and those that did averaged 693s -- an order of
        # magnitude, on 5 of 9 calls. This session therefore retries far
        # less, and respects a served Retry-After instead of a blind
        # exponential backoff.
        self.web_search_session = self._build_session(
            total=self.web_search_retries, respect_retry_after=True,
        )
        self._session_last_used: Dict[int, float] = {}
        self._session_in_flight: Dict[int, int] = {}

    # A pooled HTTPS connection left idle between findings is dropped by the
    # network path (WSL's NAT, a proxy) without either end being told. The
    # next POST is written into that dead socket and waits out the whole read
    # timeout before urllib3 retries on a fresh connection. In
    # run_20260913_140913 every research and synthesis call after the first
    # finding was created on the server exactly 600 s after it was sent --
    # eight hung attempts, 80 of the run's 118 minutes -- while calls made
    # seconds after a previous one were never affected.
    IDLE_CONNECTION_SECONDS = 60

    # A non-streaming Responses call sends nothing at all until it is
    # finished, so requests' read timeout is really a budget for the whole
    # generation and any silent network path costs that whole budget.
    # run_20260917_140228 lost its first 20 minutes exactly that way: a
    # web-search research pass received no byte for 600s, urllib3 retried it
    # on a fresh connection, and the second attempt received none either;
    # three more calls that run (adjudication, evidence_extraction,
    # patch_planning) each lost ten minutes to the same shape.
    #
    # Sending the call as a background response makes the POST return as soon
    # as the job is created and moves progress onto short polls: no
    # connection stays idle, a dropped poll costs one poll instead of the
    # whole generation, and self.timeout becomes a deadline on the call
    # itself rather than on silence between bytes.
    CREATE_TIMEOUT_SECONDS = 60
    POLL_TIMEOUT_SECONDS = 30
    # Grows so a fast answer is noticed quickly without polling a long
    # research call hundreds of times; the last value repeats.
    POLL_INTERVAL_SECONDS = (2, 3, 5, 10)
    # A poll failing is normal (that is the condition this whole mechanism
    # exists for); only an unbroken run of them means the call is lost.
    MAX_CONSECUTIVE_POLL_FAILURES = 5
    _PENDING_STATUSES = frozenset({"queued", "in_progress"})

    # How many times a call whose background response never left "queued" is
    # sent again (see BackgroundResponseNeverStarted). Each attempt has its
    # own self.timeout, so the call can wait (1 + this) timeouts: 1200s at
    # the default 600s, inside the llm step's 1800s stall watch, and the
    # resubmission writes a log line and a trace update besides.
    QUEUED_RESUBMISSIONS = 1

    # The deadline of an attempt on the Flex tier, when self.timeout is
    # shorter. OpenAI's Flex guide warns that Flex is slower and sets 15
    # minutes in its examples; the longest recorded call (367 s) fits twice.
    # A Flex response still "queued" at it was never started: it is
    # cancelled and the same request goes on the standard tier with
    # self.timeout, a fallback that does not count as a queued
    # resubmission. 900 s plus the standard attempt stays inside the llm
    # step's 1800 s stall watch, and each fallback writes a log line.
    FLEX_DEADLINE_SECONDS = 900

    # Waits before the same request is sent on Flex again after Flex said it
    # had no capacity (a capacity code, CAPACITY_ERROR_CODES, on a 5xx create
    # or a failed response); only after them does it go on the standard
    # tier. A capacity failure reports no usage, so a resend costs nothing,
    # and the standard tier bills twice the Flex price: in the C arm of
    # 2026-11-13, 30 of 750 calls fell back at once (US$ 0.867 against 0.448
    # on Flex), and another Flex call succeeded within 180 s for 26 of them
    # (50 of 51 on agent_baseline_20261108). A resend goes only while the
    # call's time on Flex plus the wait stays inside FLEX_DEADLINE_SECONDS;
    # the wait is not model time (abandoned_flex_seconds), and each one
    # writes a log line and a trace update (flex_retries).
    FLEX_RETRY_WAITS_SECONDS = (60, 120)

    # Waits before each new create POST after an HTTP 5xx or a connection
    # failure that urllib3's own two retries (about 50 s) did not outlast.
    # Salty (rvsec_salty_20261022/run_v4, 2026-10-31 18:45): one HTTP 500
    # burst of about 50 s on an adjudication create crashed the joined unit
    # and left both findings "não analisado". A 5xx answer to the create
    # made no response, so it costs nothing to send again. A connection
    # failure usually means the POST never arrived (tofshare run, 2026-10-31:
    # a DNS failure crashed its only finding); one dropped after the server
    # read it could leave a background response running that the cost guard
    # never sees, a risk taken for a failure seen so far only as a connection
    # never made. The waits (225 s in all,
    # plus up to a quarter of jitter each) stay well inside the llm step's
    # 1800 s stall watch, and each one writes a log line and a trace update.
    CREATE_RETRY_WAITS_SECONDS = (15, 30, 60, 120)
    # A served Retry-After longer than this is not waited out in full.
    CREATE_RETRY_MAX_WAIT_SECONDS = 300

    def _create_background_response(self, session, payload: Dict):
        """POST ``payload`` to /responses; the HTTP response, or the error it carries."""
        self._before_request(session)
        try:
            extra = {"stream": True} if payload.get("stream") else {}
            response = session.post(
                f"{self.endpoint}/responses", headers=self.headers,
                json=payload, timeout=self.CREATE_TIMEOUT_SECONDS, **extra)
        finally:
            self._after_request(session)
        if response.status_code != 200:
            body = str(getattr(response, "text", "") or "")
            if (response.status_code == 400 and payload.get("service_tier")
                    and self._names_service_tier(response)):
                raise ServiceTierRejected(
                    f"OpenAI API HTTP 400: {body[:1000]}", status_code=400, body=body,
                )
            if response.status_code == 400 and (
                    "prompt_cache_breakpoint" in body
                    or (isinstance(payload.get("input"), list)
                        and self._rejects_input_parts(response))):
                # A field of the request, not a verdict on its text: its
                # error code (unknown_parameter) would read as a refusal.
                raise BackgroundCreateRejected(
                    f"OpenAI API HTTP 400: {body[:1000]}", status_code=400, body=body,
                )
            refusal_code = self._content_refusal_code(response)
            if refusal_code:
                raise ProviderContentRefusal(
                    f"OpenAI refused this request's content "
                    f"({refusal_code}): {response.text[:1000]}",
                    code=refusal_code, status_code=response.status_code,
                )
            if response.status_code == 401:
                with self._state_lock:
                    self._authentication_failure = (
                        "OpenAI rejected the API key (HTTP 401); no "
                        "further call is made in this process: "
                        + response.text[:300]
                    )
                raise ProviderAuthenticationError(self._authentication_failure)
            message = (f"OpenAI API HTTP {response.status_code}: "
                       f"{response.text[:1000]}")
            if response.status_code >= 500:
                retry_state = getattr(getattr(response, "raw", None), "retries", None)
                raise BackgroundCreateFailed(
                    message, status_code=response.status_code,
                    retry_after=self._retry_after_seconds(response),
                    retry_history=getattr(retry_state, "history", ()) or (),
                    code=self._error_code(response),
                )
            code = self._error_code(response)
            if response.status_code == 429:
                # An exhausted quota is the account's and stays as it is; any
                # other 429 is the service throttling, resent (generate).
                limited = (BackgroundCreateRateLimited if code == "insufficient_quota"
                           else BackgroundCreateThrottled)
                raise limited(message, code=code, status_code=429,
                              retry_after=self._retry_after_seconds(response))
            if code and response.status_code not in NOT_CONTENT_VERDICT_STATUSES:
                # A coded rejection of the request (context_length_exceeded,
                # ...): no verdict on the text, yet the same request meets it
                # on every resume. It was a content refusal (a concluded
                # finding) before 2026-11-09; read as a crash it would be
                # resumed twice for the same answer.
                raise ProviderRequestRejected(
                    message, code=code, status_code=response.status_code)
            # Any other client error stays the plain RuntimeError it was, so
            # crash records keep naming it as before.
            raise RuntimeError(message)
        return response

    @staticmethod
    def _error_code(response) -> str:
        """The error.code of an HTTP error answer, or ""."""
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return ""
        error = payload.get("error") if isinstance(payload, dict) else None
        return str(error.get("code", "") or "").strip() if isinstance(error, dict) else ""

    @classmethod
    def _names_service_tier(cls, response) -> bool:
        """Whether a 400 objects to the service_tier field rather than to the text."""
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return False
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return False
        if str(error.get("code", "") or "").strip() in cls._CONTENT_VERDICT_CODES:
            return False
        return (str(error.get("param", "") or "").strip() == "service_tier"
                or "service_tier" in str(error.get("message", "") or ""))

    # A 400 carrying a content-verdict code is a refusal even when it names
    # the input, and is never resent plain.
    _CONTENT_VERDICT_CODES = CONTENT_VERDICT_CODES
    _INPUT_SHAPE_WORDS = ("cache", "breakpoint", "input_text")

    @classmethod
    def _rejects_input_parts(cls, response) -> bool:
        """Whether a 400 to a request sent as input parts objects to their shape.

        The API names the offending field in error.param ("input[0].content")
        or its message (the cache breakpoint, the input_text type). Read as a
        refusal, such a rejection recorded the finding as "the provider
        refused this content" although the plain string would have gone
        through. A content-verdict code is a refusal whatever field it names.
        """
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return False
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return False
        if str(error.get("code", "") or "").strip() in cls._CONTENT_VERDICT_CODES:
            return False
        if str(error.get("param", "") or "").strip().startswith("input"):
            return True
        message = str(error.get("message", "") or "").lower()
        return any(word in message for word in cls._INPUT_SHAPE_WORDS)

    @staticmethod
    def _retry_after_seconds(response) -> Optional[float]:
        """The response's Retry-After in seconds (delta form only), or None."""
        headers = getattr(response, "headers", None) or {}
        try:
            value = float(str(headers.get("Retry-After", "")).strip())
        except ValueError:
            return None
        return value if value >= 0 else None

    def _create_retry_wait(self, attempt: int, retry_after: Optional[float]) -> float:
        """Seconds to wait before create attempt ``attempt + 1`` (jittered, bounded)."""
        base = self.CREATE_RETRY_WAITS_SECONDS[attempt]
        wait = base * (1.0 + random.random() * 0.25)
        if retry_after is not None:
            wait = max(wait, retry_after)
        return min(wait, float(self.CREATE_RETRY_MAX_WAIT_SECONDS))

    def _note_create_retry(self, call_id: str, exc: Exception,
                           create_retries: List[Dict], stage: str) -> None:
        """Log and trace a create sent again after a server fault, so the step shows progress.

        A response that failed without a content verdict is sent again the
        same way and counts against the same limit.
        """
        what = ("did not answer the request" if isinstance(exc, BackgroundResponseFailed)
                else "could not create the response")
        self.logger.warning(
            "OpenAI %s (%s); sending the same "
            "request again in %.0fs (stage=%s, create retry %d of %d)",
            what, str(exc)[:300], create_retries[-1]["waited_s"], stage or "unknown",
            len(create_retries), len(self.CREATE_RETRY_WAITS_SECONDS),
        )
        try:
            self._update_trace_metadata(call_id, {"create_retries": list(create_retries)})
        except (OSError, json.JSONDecodeError) as error:
            self.logger.warning("Could not update OpenAI trace: %s", error)

    @staticmethod
    def _retry_trace(retry_history: List[Any]) -> Dict[str, Any]:
        """The trace fields describing urllib3's discarded attempts."""
        return {
            "retry_count": len(retry_history),
            # The count alone said a call had cost ten times its usual
            # latency without saying what provoked it. The status of each
            # discarded attempt is what distinguishes a rate limit worth
            # pacing from a server fault worth reporting.
            "retry_statuses": [
                getattr(item, "status", None) for item in retry_history
            ],
            # A retry provoked by a connection or read failure carries no
            # HTTP status, so the status list alone said only "not an
            # HTTP code" for the one that cost 9.6 minutes. The error is
            # what names it.
            "retry_errors": [
                str(getattr(item, "error", "") or "")
                for item in retry_history
            ],
        }

    def _note_queued_resubmission(self, call_id: str,
                                  exc: "BackgroundResponseNeverStarted",
                                  cancelled: List[str], stage: str) -> None:
        """Log and trace a resubmission, so the step shows progress while it waits."""
        self.logger.warning(
            "OpenAI never started background response %s (%s); cancelled it "
            "and resubmitted the same request (stage=%s, resubmission %d)",
            exc.response_id, exc, stage or "unknown", len(cancelled),
        )
        # The status stays "prepared" until the call ends: cost.usage counts
        # a trace as a call only then, so a process stopped during the wait
        # is not counted as a call with no tokens.
        try:
            self._update_trace_metadata(call_id, {
                "queued_resubmissions": len(cancelled),
                "queued_cancelled_response_ids": list(cancelled),
            })
        except (OSError, json.JSONDecodeError) as error:
            self.logger.warning("Could not update OpenAI trace: %s", error)

    @staticmethod
    def _is_event_stream(response) -> bool:
        headers = getattr(response, "headers", None) or {}
        content_type = ""
        for name, value in dict(headers).items():
            if str(name).lower() == "content-type":
                content_type = str(value)
        return "text/event-stream" in content_type.lower()

    # The events whose data carries the whole response in its final state.
    _TERMINAL_STREAM_EVENTS = frozenset({
        "response.completed", "response.incomplete", "response.failed",
        "response.cancelled",
    })

    def _read_output_stream(self, response, request_started: float, deadline: float
                            ) -> Tuple[Dict, Optional[WhitespaceRunDetector], bool]:
        """Follow a streamed background create until it ends, degenerates or the deadline.

        Returns (data, detector, read_whole): data is the final response when
        a terminal event came, else {"id", "status"} as last seen, for the
        polls to follow; the detector is returned when the text ended in a
        whitespace run of DEGENERATE_WHITESPACE_CHARS, None otherwise;
        read_whole is False when reading failed, so the last status seen is
        not proof that it still holds. Any failure to read the stream hands
        the response to the polls; a stream that never named its response is
        an error, as a create without an id is.
        """
        detector = WhitespaceRunDetector()
        data: Dict = {}
        event = None
        lines: List[str] = []
        read_whole = True
        try:
            for raw in response.iter_lines(decode_unicode=True):
                # On every line read, not only after an event: a stream kept
                # alive by comment lines (": keepalive") or blank lines would
                # otherwise never reach the deadline while Flex queues it.
                if time.monotonic() - request_started >= deadline:
                    break
                if raw is None:
                    continue
                if raw.startswith("event:"):
                    event = raw[6:].strip()
                    continue
                if raw.startswith("data:"):
                    lines.append(raw[5:].strip())
                    continue
                if raw != "" or not lines:
                    continue
                try:
                    payload = json.loads("\n".join(lines))
                except ValueError:
                    payload = {}
                lines = []
                kind = event or str(payload.get("type", "") or "")
                event = None
                body = payload.get("response") if isinstance(payload.get("response"), dict) else None
                if body is not None:
                    data = {**data, "id": body.get("id") or data.get("id"),
                            "status": body.get("status") or data.get("status")}
                    if kind in self._TERMINAL_STREAM_EVENTS:
                        return body, None, True
                elif kind in ("response.queued", "response.in_progress"):
                    data = {**data, "status": kind.split(".", 1)[1]}
                if kind == "response.output_text.delta":
                    data = {**data, "status": "in_progress"}
                    if detector.feed(str(payload.get("delta", "") or "")):
                        return data, detector, True
                if time.monotonic() - request_started >= deadline:
                    break
        except Exception as exc:  # noqa: BLE001
            read_whole = False
            self.logger.warning(
                "Stream of background response %s broke (%s); following it by polls",
                data.get("id") or "?", exc)
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass
        if not data.get("id"):
            raise RuntimeError("OpenAI accepted a background response without an id")
        if data.get("status") not in self._PENDING_STATUSES:
            # The stream stopped before its terminal event: the polls read it.
            data = {**data, "status": "in_progress"}
            read_whole = False
        return data, None, read_whole

    def _settle_cancelled(self, session, response_id: str,
                          cancel_answer: Optional[Dict]) -> Dict:
        """The cancelled response once it reports its final state (and usage), or the cancel's answer.

        A cancel answers before the response's usage is final (paid probe,
        2026-11-09: usage null in the answer, the tokens billed a few seconds
        later); a few short polls read it.
        """
        answer = cancel_answer if isinstance(cancel_answer, dict) else {}
        for interval in (2, 3, 5, 10):
            if answer and answer.get("status") not in self._PENDING_STATUSES \
                    and not self._reports_no_usage(answer):
                return answer
            self._sleep(interval)
            try:
                self._before_request(session)
                try:
                    poll = session.get(f"{self.endpoint}/responses/{response_id}",
                                       headers=self.headers, timeout=self.POLL_TIMEOUT_SECONDS)
                finally:
                    self._after_request(session)
                if getattr(poll, "status_code", None) == 200:
                    fresh = poll.json()
                    if isinstance(fresh, dict):
                        answer = fresh
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Could not read cancelled response %s: %s",
                                    response_id, exc)
        return answer or {"id": response_id, "status": "cancelled"}

    @staticmethod
    def _reports_no_usage(data: Dict) -> bool:
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        return not any((usage.get(key) or 0) for key in ("input_tokens", "output_tokens"))

    def _with_final_usage(self, session, data: Dict) -> Dict:
        """An "incomplete" answer without usage, read once more for the usage it ends with.

        The poll that saw the final status can precede the usage; one GET
        more costs nothing. Any failure keeps the answer as it was.
        """
        if (str(data.get("status", "")) != "incomplete" or not data.get("id")
                or not self._reports_no_usage(data)):
            return data
        try:
            self._before_request(session)
            try:
                again = session.get(f"{self.endpoint}/responses/{data['id']}",
                                    headers=self.headers, timeout=self.POLL_TIMEOUT_SECONDS)
            finally:
                self._after_request(session)
            fresh = again.json() if getattr(again, "status_code", None) == 200 else None
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Could not read the usage of response %s: %s",
                                data.get("id"), exc)
            return data
        if isinstance(fresh, dict) and not self._reports_no_usage(fresh):
            return fresh
        return data

    def _unreported_usage_estimate(self, data: Dict, prompt: str,
                                   system_prompt: Optional[str],
                                   stopped: bool = False) -> Dict[str, int]:
        """An upper bound on what an answer that ran without reporting usage was billed, or {}.

        An "incomplete" answer (it stopped on a limit or a filter), or one
        this call ``stopped`` (cancelled for degenerate output), ran yet may
        report no tokens, and counting it as 0 would let the --max-cost
        guard fall behind the bill. The bound is a token per two characters
        of input and the whole output ceiling (a cancelled Flex response was
        billed the whole ceiling in the paid probes of 2026-11-09); the
        trace keeps the reported zeros.
        """
        if not (stopped or str(data.get("status", "")) == "incomplete") \
                or not self._reports_no_usage(data):
            return {}
        characters = len(prompt or "") + len(system_prompt or "")
        # -1 (the run's "no limit") sends no ceiling: 32,768 stands for it.
        ceiling = int(self.max_output_tokens or 0)
        return {"input_tokens": (characters + 1) // 2,
                "output_tokens": ceiling if ceiling > 0 else 32768}

    def _requested_service_tier(self) -> str:
        """The service_tier to send, "" for none ("auto", or the tier was refused once)."""
        tier = getattr(self, "service_tier", "auto") or "auto"
        if tier == "auto" or getattr(self, "_service_tier_rejected", ""):
            return ""
        return tier

    def _note_service_tier_fallback(self, call_id: str, fallbacks: List[Dict],
                                    stage: str) -> None:
        """Log and trace a call sent again on the standard tier, so the step shows progress."""
        last = fallbacks[-1]
        self.logger.warning(
            "OpenAI %s tier did not take the request (%s%s); sending the same "
            "request on the standard tier (stage=%s)",
            last.get("from", ""), last.get("reason", ""),
            f": {last['detail']}" if last.get("detail") else "", stage or "unknown",
        )
        try:
            self._update_trace_metadata(call_id, {"service_tier_fallbacks": list(fallbacks)})
        except (OSError, json.JSONDecodeError) as error:
            self.logger.warning("Could not update OpenAI trace: %s", error)

    def _flex_retry_wait(self, retries_made: int, seconds_on_flex: float) -> Optional[float]:
        """The wait before resending on Flex after a capacity failure, None to leave Flex.

        The next of FLEX_RETRY_WAITS_SECONDS, while the call's time on Flex
        plus that wait stays inside FLEX_DEADLINE_SECONDS.
        """
        if retries_made >= len(self.FLEX_RETRY_WAITS_SECONDS):
            return None
        wait = float(self.FLEX_RETRY_WAITS_SECONDS[retries_made])
        if seconds_on_flex + wait > float(self.FLEX_DEADLINE_SECONDS):
            return None
        return wait

    def _note_flex_retry(self, call_id: str, retries: List[Dict], stage: str) -> None:
        """Log and trace a call sent on Flex again after a wait, so the step shows progress."""
        last = retries[-1]
        self.logger.warning(
            "OpenAI flex tier had no capacity (%s); sending the same request on "
            "flex again in %.0fs (stage=%s, flex retry %d of %d)",
            last.get("detail", ""), last.get("waited_s", 0), stage or "unknown",
            len(retries), len(self.FLEX_RETRY_WAITS_SECONDS),
        )
        try:
            self._update_trace_metadata(call_id, {"flex_retries": list(retries)})
        except (OSError, json.JSONDecodeError) as error:
            self.logger.warning("Could not update OpenAI trace: %s", error)

    def _cancel_background_response(self, session, response_id: str) -> Optional[Dict]:
        """Best-effort stop of a background response this call abandons.

        Returns OpenAI's answer (the response as it is now) when the cancel
        was accepted, None when it failed.
        """
        if not response_id:
            return None
        try:
            answer = session.post(
                f"{self.endpoint}/responses/{response_id}/cancel",
                headers=self.headers, timeout=self.POLL_TIMEOUT_SECONDS,
            )
            if getattr(answer, "status_code", None) != 200:
                raise RuntimeError(
                    f"HTTP {getattr(answer, 'status_code', None)}: "
                    f"{str(getattr(answer, 'text', ''))[:300]}"
                )
            payload = answer.json()
            return payload if isinstance(payload, dict) else None
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "Could not cancel background response %s: %s", response_id, exc
            )
            return None

    @staticmethod
    def _cancelled_before_starting(answer: Optional[Dict]) -> bool:
        """Whether a cancel's answer shows a response that is stopped and never ran.

        Tokn (run_v5, 2026-10-31): a queued response is resubmitted only once
        it is certainly dead. A failed cancel, or one answering anything but
        "cancelled" without tokens, leaves a response that may still run and
        be billed while its resubmission runs too, a cost the guard never sees.
        """
        if not isinstance(answer, dict) or answer.get("status") != "cancelled":
            return False
        usage = answer.get("usage") if isinstance(answer.get("usage"), dict) else {}
        return not any(
            (usage.get(key) or 0) for key in ("input_tokens", "output_tokens")
        )

    def _await_background_response(
        self, session, created: Dict, request_started: float,
        deadline_seconds: Optional[float] = None, streamed_status: bool = False,
    ) -> tuple[Dict, int]:
        """Poll a queued background response until it reaches a final state.

        Returns (response_payload, poll_count). The payload is the same shape
        the synchronous call returned, so every caller below is unchanged.
        ``deadline_seconds`` replaces self.timeout as the attempt's deadline;
        ``streamed_status`` says the status in ``created`` was read whole from
        the response's stream, as good as a poll's answer.
        """
        deadline = float(self.timeout if deadline_seconds is None else deadline_seconds)
        response_id = str(created.get("id", ""))
        if not response_id:
            raise RuntimeError(
                "OpenAI accepted a background response without an id"
            )
        data = created
        polls = 0
        failures = 0
        started = False
        try:
            while str(data.get("status", "")) in self._PENDING_STATUSES:
                status = str(data.get("status", ""))
                if status != "queued":
                    started = True
                elapsed = time.monotonic() - request_started
                if elapsed >= deadline:
                    cancel_answer = self._cancel_background_response(
                        session, response_id,
                    )
                    message = (
                        f"background response {response_id} was still "
                        f"{status} after {round(elapsed)}s"
                    )
                    # "Never started" needs the last poll to have answered
                    # "queued": when polls are failing, the status is only
                    # the last one seen and the response may be running. It
                    # also needs the cancel to have stopped it before it ran.
                    if (not started and (polls or streamed_status) and not failures
                            and self._cancelled_before_starting(cancel_answer)):
                        raise BackgroundResponseNeverStarted(
                            message, response_id=response_id, polls=polls,
                        )
                    raise BackgroundResponseTimedOut(
                        message, response_id=response_id,
                        cancel_answer=cancel_answer, deadline=deadline,
                    )
                interval = self.POLL_INTERVAL_SECONDS[
                    min(polls, len(self.POLL_INTERVAL_SECONDS) - 1)
                ]
                self._sleep(min(interval, max(0.0, deadline - elapsed)))
                polls += 1
                try:
                    self._before_request(session)
                    try:
                        poll = session.get(
                            f"{self.endpoint}/responses/{response_id}",
                            headers=self.headers,
                            timeout=self.POLL_TIMEOUT_SECONDS,
                        )
                    finally:
                        self._after_request(session)
                    if poll.status_code != 200:
                        raise _PollHTTPError(
                            f"OpenAI API HTTP {poll.status_code}: "
                            f"{poll.text[:1000]}", status_code=poll.status_code,
                        )
                    data = poll.json()
                    failures = 0
                except Exception as exc:  # noqa: BLE001
                    failures += 1
                    if failures > self.MAX_CONSECUTIVE_POLL_FAILURES:
                        if not self._service_poll_failure(exc):
                            raise
                        # The service or the network, not this request: the
                        # response is abandoned (it may still run and be
                        # billed, so it is cancelled as at the deadline) and
                        # the finding left for a resume pass.
                        cancel_answer = self._cancel_background_response(
                            session, response_id)
                        raise BackgroundPollFailed(
                            f"{failures} polls in a row of background response "
                            f"{response_id} failed; the last: {exc}",
                            response_id=response_id, cancel_answer=cancel_answer,
                            polls=polls,
                        ) from exc
                    self.logger.warning(
                        "Poll %d of background response %s failed (%s); "
                        "retrying", polls, response_id, exc,
                    )
        except KeyboardInterrupt:
            self._cancel_background_response(session, response_id)
            raise
        try:
            self._raise_for_failed_response(data)
        except BackgroundResponseFailed as exc:
            exc.polls = polls
            raise
        return data, polls

    @staticmethod
    def _service_poll_failure(exc: Exception) -> bool:
        """Whether a poll failed for the service or the network (5xx, 429, connection, timeout)."""
        if isinstance(exc, _PollHTTPError):
            return exc.status_code >= 500 or exc.status_code == 429
        return requests is not None and isinstance(exc, requests.exceptions.RequestException)

    def _raise_for_failed_response(self, data: Dict) -> None:
        """Raise for a final "failed" or "cancelled" response; nothing otherwise.

        Every final answer goes through it, polled or read whole from the
        stream's terminal event, except a response this call cancelled
        itself for degenerate output. A content-verdict code
        (provider_error_kind, as for an HTTP 4xx) is a ProviderContentRefusal;
        any other code, or none, is BackgroundResponseFailed, which the call
        sends again (on the standard tier at once for a Flex capacity failure).
        """
        status = str(data.get("status", ""))
        if status not in {"failed", "cancelled"}:
            return
        error = data.get("error") if isinstance(data.get("error"), dict) else {}
        code = str(error.get("code", "") or "").strip()
        message = str(error.get("message", "")) or status
        if provider_error_kind(code) == "content_verdict":
            raise ProviderContentRefusal(
                f"OpenAI refused this request's content ({code}): {message}",
                code=code, status_code=200,
            )
        raise BackgroundResponseFailed(
            f"OpenAI background response {status}"
            + (f" ({code})" if code else "") + f": {message}",
            code=code, status=status, response=data,
        )

    def _charge_failed_response(self, session, exc: "BackgroundResponseFailed",
                                attempt_tier: str, prompt: str = "",
                                system_prompt: Optional[str] = None) -> Dict[str, Any]:
        """Charge the guard what a failed response used; its trace record.

        A response can fail after it ran (a server fault mid-generation) and
        be billed for what it generated: a failed one is charged the usage
        it reports (a capacity failure seconds after the create reports
        none). A cancelled one (cancelled outside this call, which no longer
        raises) reports its usage only seconds after the cancel, as the paid
        probes of 2026-11-09 showed: it is read until it settles
        (_settle_cancelled), and one that still reports nothing is charged
        the upper bound of _unreported_usage_estimate, since a cancelled
        Flex response was billed the whole output ceiling. The bound goes in
        the record as guard_usage_estimate, which cost.usage and a guard
        rebuilt from the traces bill too.
        """
        data = exc.response
        if exc.status == "cancelled" and exc.response_id and self._reports_no_usage(data):
            data = self._settle_cancelled(session, exc.response_id, data)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        details = usage.get("input_tokens_details")
        details = details if isinstance(details, dict) else {}
        tier = str(data.get("service_tier", "") or attempt_tier or "")
        search_calls = billed_web_search_calls(web_search_actions(data))
        record: Dict[str, Any] = {
            "response_id": exc.response_id,
            "status": exc.status,
            "code": exc.code,
            "service_tier": tier,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "cached_input_tokens": int(details.get("cached_tokens", 0) or 0),
            "cache_write_input_tokens": int(details.get("cache_write_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "web_search_calls": search_calls,
        }
        guard = getattr(self, "_cost_guard", None)
        estimate = (self._unreported_usage_estimate(data, prompt, system_prompt, stopped=True)
                    if exc.status == "cancelled" else {})
        if estimate:
            record["guard_usage_estimate"] = estimate
            if guard is not None:
                guard.add(estimate["input_tokens"], 0, estimate["output_tokens"],
                          service_tier=tier, web_search_calls=search_calls)
            return record
        if not (record["input_tokens"] or record["output_tokens"] or search_calls):
            return record
        with self._state_lock:
            self.token_usage["input"] += record["input_tokens"]
            self.token_usage["output"] += record["output_tokens"]
        if guard is not None:
            guard.add(record["input_tokens"], record["cached_input_tokens"],
                      record["output_tokens"],
                      cache_write_tokens=record["cache_write_input_tokens"],
                      service_tier=tier, web_search_calls=search_calls)
        return record

    def _charge_timed_out_response(self, session, exc: "BackgroundResponseTimedOut",
                                   prompt: str, system_prompt: Optional[str],
                                   attempt_tier: str) -> Dict[str, Any]:
        """Settle a response cancelled at the deadline and charge the guard for it.

        It may have run: the settled response's usage is charged when it
        reports one, else the upper bound of _unreported_usage_estimate. The
        returned trace fields put either in the timeout trace, so a guard
        rebuilt from the traces and `cognalys cost` count it too.
        """
        settled = self._settle_cancelled(session, exc.response_id, exc.cancel_answer)
        tier = str(settled.get("service_tier", "") or attempt_tier or "")
        search_actions = web_search_actions(settled)
        search_calls = billed_web_search_calls(search_actions)
        fields: Dict[str, Any] = {
            "openai_response_id": exc.response_id,
            "response_status": str(settled.get("status", "") or ""),
            "service_tier": tier,
            "web_search_actions": search_actions,
            "web_search_calls": search_calls,
        }
        estimate = self._unreported_usage_estimate(settled, prompt, system_prompt, stopped=True)
        guard = getattr(self, "_cost_guard", None)
        if estimate:
            fields["guard_usage_estimate"] = estimate
            if guard is not None:
                guard.add(estimate["input_tokens"], 0, estimate["output_tokens"],
                          service_tier=tier, web_search_calls=search_calls)
            return fields
        usage = settled.get("usage") if isinstance(settled.get("usage"), dict) else {}
        details = usage.get("input_tokens_details")
        details = details if isinstance(details, dict) else {}
        counts = {
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "cached_input_tokens": int(details.get("cached_tokens", 0) or 0),
            "cache_write_input_tokens": int(details.get("cache_write_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
        }
        with self._state_lock:
            self.token_usage["input"] += counts["input_tokens"]
            self.token_usage["output"] += counts["output_tokens"]
        if guard is not None:
            guard.add(counts["input_tokens"], counts["cached_input_tokens"],
                      counts["output_tokens"],
                      cache_write_tokens=counts["cache_write_input_tokens"],
                      service_tier=tier, web_search_calls=search_calls)
        return {**fields, **counts}

    def _before_request(self, session) -> None:
        key = id(session)
        with self._state_lock:
            last = self._session_last_used.get(key)
            if (last is not None and not self._session_in_flight.get(key)
                    and time.monotonic() - last > self.IDLE_CONNECTION_SECONDS):
                # Closes the pooled connections only; the session and its
                # retry adapters stay mounted and open new connections.
                session.close()
            self._session_in_flight[key] = self._session_in_flight.get(key, 0) + 1

    def _after_request(self, session) -> None:
        key = id(session)
        with self._state_lock:
            self._session_in_flight[key] = max(0, self._session_in_flight.get(key, 1) - 1)
            self._session_last_used[key] = time.monotonic()

    def _build_session(self, total: int,
                       respect_retry_after: bool = False) -> "requests.Session":
        session = requests.Session()
        # Jitter so that concurrent workers rate-limited together do not
        # retry together and meet the same limit again.
        retry = Retry(total=total, backoff_factor=2, backoff_jitter=1.0,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["POST", "GET"],
                      respect_retry_after_header=respect_retry_after,
                      raise_on_status=False)
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    @staticmethod
    def _content_refusal_code(response) -> str:
        """The provider's own code for a content refusal, or "".

        Only 4xx carries a verdict on the text; a 5xx is the service
        failing and stays an ordinary, retryable error. Not every 4xx does,
        though: 401 (invalid_api_key) is about the credentials, 408 and 429
        (rate_limit_exceeded, insufficient_quota) about the transport or the
        account. All three also carry an error code, and read as refusals
        they were recorded per finding as "the provider refused this
        content" while the step reported success. Nor does every code: only
        a content-verdict one (provider_error_kind, the classification a
        failed response gets too); any other coded 4xx is a
        ProviderRequestRejected (_create_background_response), and one
        without a code the plain client error.
        """
        status = getattr(response, "status_code", 0)
        if not 400 <= status < 500 or status in NOT_CONTENT_VERDICT_STATUSES:
            return ""
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return ""
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return ""
        code = str(error.get("code", "") or "").strip()
        return code if provider_error_kind(code) == "content_verdict" else ""

    def _write_trace_json(self, path: Path, value: Dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        # default=str: an SDK response (e.g. a web search result's
        # retrieval time) can hold datetimes, which a trace keeps as text.
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _start_trace(self, prompt, system_prompt, json_mode, web_search,
                     json_schema, schema_name, delivery: str,
                     trace_stage: str = "unspecified",
                     trace_context: Optional[Dict] = None,
                     parent_call_id: str = "",
                     request_fields: Optional[Dict] = None) -> str:
        """Persist a versioned request trace and return its stable call ID.

        ``request_fields`` adds how the request was sent (its input parts and
        breakpoints) to the request record.
        """
        # What this call asks with, read by _finish_trace into _LAST_TRACE: a
        # research corpus entry is stored only under the model and effort its
        # passes were sent with (research_reasoning_effort changes the shared
        # provider, so another worker may change the effort meanwhile).
        _LAST_TRACE.started = {"model": self.model, "reasoning_effort": self.reasoning_effort}
        if self.prompt_log_dir is None:
            return ""
        try:
            self.prompt_log_dir.mkdir(parents=True, exist_ok=True)
            with self._state_lock:
                self.prompt_log_sequence += 1
                sequence = self.prompt_log_sequence
            timestamp = datetime.now().strftime("%Y%m%dT%H%M%S_%f")
            safe_stage = re.sub(
                r"[^A-Za-z0-9_.-]+", "-", trace_stage
            )[:60] or "unspecified"
            call_id = (
                f"{timestamp}_{os.getpid()}_{sequence:04d}_"
                f"{safe_stage}"
            )
            context = dict(trace_context or {})
            record = {
                "trace_version": 2,
                "call_id": call_id,
                "parent_call_id": parent_call_id,
                "stage": trace_stage,
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
                "delivery": delivery,
                "endpoint": self.endpoint,
                "model": self.model,
                "timeout_seconds": self.timeout,
                "max_output_tokens": self.max_output_tokens,
                "reasoning_effort": self.reasoning_effort,
                "web_search": web_search,
                "json_mode": json_mode,
                "schema_name": schema_name if json_schema else None,
                "json_schema": json_schema,
                "instructions": system_prompt or "",
                "input": prompt,
                "context": context,
                "input_sha256": trace_input_sha256(system_prompt, prompt),
            }
            record.update(request_fields or {})
            self._write_trace_json(
                self.prompt_log_dir / f"{call_id}_request.json", record
            )
            self._write_trace_json(
                self.prompt_log_dir / f"{call_id}_metadata.json",
                {
                    "trace_version": 2,
                    "call_id": call_id,
                    "parent_call_id": parent_call_id,
                    "finding_id": context.get("finding_id", ""),
                    "stage": trace_stage,
                    "status": "prepared",
                    "delivery": delivery,
                    "prompt_version": context.get("prompt_version", ""),
                    "schema_version": context.get("schema_version", ""),
                    "policy_version": context.get("policy_version", ""),
                    # Already in the request's context; here too so the run
                    # summary lists them from the small metadata records.
                    "context_builder_version": context.get("context_builder_version", ""),
                    "semantic_resolver_version": context.get("semantic_resolver_version", ""),
                    "model": self.model,
                    "reasoning_effort": self.reasoning_effort,
                    "input_characters": len(prompt) + len(system_prompt or ""),
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 0,
                    "latency_ms": 0,
                    "retry_count": 0,
                    "request_id": "",
                    "section_telemetry": context.get("section_telemetry", {}),
                    **(request_fields or {}),
                },
            )
            return call_id
        except OSError as exc:
            self.logger.warning("Could not write OpenAI prompt log: %s", exc)
            return ""

    def _finish_trace(self, call_id: str, response: Any, metadata: Dict) -> None:
        _LAST_TRACE.call = {"call_id": call_id, "delivery": str(metadata.get("delivery", "")),
                            "completed": pass_completed(metadata),
                            **(getattr(_LAST_TRACE, "started", None) or {})}
        _LAST_TRACE.started = {}
        if not call_id or self.prompt_log_dir is None:
            return
        try:
            response_record = {
                "trace_version": 2,
                "call_id": call_id,
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
                "response": response,
            }
            self._write_trace_json(
                self.prompt_log_dir / f"{call_id}_response.json",
                response_record,
            )
            self._update_trace_metadata(call_id, {
                **metadata,
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            })
        except (OSError, json.JSONDecodeError) as exc:
            self.logger.warning("Could not complete OpenAI trace: %s", exc)

    def _update_trace_metadata(self, call_id: str, metadata: Dict) -> None:
        """Merge ``metadata`` into a call's metadata record (OSError/JSON errors propagate)."""
        if not call_id or self.prompt_log_dir is None:
            return
        metadata_path = self.prompt_log_dir / f"{call_id}_metadata.json"
        existing = {}
        if metadata_path.is_file():
            existing = json.loads(metadata_path.read_text(encoding="utf-8"))
        existing.update(metadata)
        self._write_trace_json(metadata_path, existing)

    def _update_trace_request(self, call_id: str, fields: Dict) -> None:
        """Merge ``fields`` into a call's request record, when it exists."""
        if not call_id or self.prompt_log_dir is None:
            return
        request_path = self.prompt_log_dir / f"{call_id}_request.json"
        if not request_path.is_file():
            return
        existing = json.loads(request_path.read_text(encoding="utf-8"))
        existing.update(fields)
        self._write_trace_json(request_path, existing)

    def _log_prompt(self, prompt, system_prompt, json_mode, web_search,
                    json_schema, schema_name, delivery: str) -> None:
        """Backward-compatible request-only logger used by older tests/tools."""
        self._start_trace(
            prompt, system_prompt, json_mode, web_search, json_schema,
            schema_name, delivery,
        )

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def test_connection(self):
        try:
            response = self.session.get(
                f"{self.endpoint}/models/{self.model}", headers=self.headers,
                timeout=min(self.timeout, 30))
            if response.status_code != 200:
                self.logger.error("OpenAI HTTP %s: %s", response.status_code,
                                  response.text[:500])
            return response.status_code == 200
        except Exception as exc:
            self.logger.error("OpenAI connection failed: %s", exc)
            return False

    def list_available_models(self):
        return [self.model] if self.test_connection() else []

    def _replay(self, cache_request, prompt, system_prompt, json_mode, web_search,
                json_schema, schema_name, trace_stage, trace_context,
                parent_call_id) -> Optional[str]:
        """The open finding's earlier answer to this exact request, traced as a replay.

        The text is the one stored, byte for byte, so the prompts built on it
        are identical too and replay in turn. Nothing is sent: no tokens, no
        cost-guard charge, and the call budget counts it apart.
        """
        found = self.cache.replay(cache_request)
        if found is None:
            return None
        response, original_call_id = found
        call_id = self._start_trace(
            prompt, system_prompt, json_mode, web_search, json_schema,
            schema_name, "finding_replay", trace_stage, trace_context,
            parent_call_id,
        )
        self._finish_trace(call_id, {"output_text": response}, {
            "status": "finding_replay", "delivery": "finding_replay",
            "latency_ms": 0, "replayed_call_id": original_call_id,
        })
        budget = active_budget()
        if budget is not None:
            budget.record_replay()
        return response

    def _prompt_cache_request_input(self, prompt: str, web_search: bool
                                    ) -> Tuple[Any, List[int], List[int], str]:
        """The ``input`` to send, its cuts, its breakpoints, and why it went whole.

        A prompt cut at fixed offsets lets a later call of the same stage
        read the shared prefix from the prompt cache (see
        PROMPT_CACHE_BREAKPOINT_STRIDE_CHARS). Research calls are sent as
        before, and so is every prompt when TCC_LLM_PROMPT_CACHE_BREAKPOINTS
        is 0 or after the API refused the field once.
        """
        if web_search:
            return prompt, [], [], ""
        request_input, cuts, breakpoints = prompt_cache_input(prompt)
        if not cuts:
            return prompt, [], [], ""
        if not prompt_cache_breakpoints_enabled():
            return prompt, [], [], "environment"
        if getattr(self, "_prompt_cache_breakpoints_rejected", ""):
            return prompt, [], [], "rejected"
        return request_input, cuts, breakpoints, ""

    @staticmethod
    def _rejects_prompt_cache_breakpoints(exc: BackgroundCreateRejected) -> bool:
        """Whether an HTTP 400 is the API refusing how the prompt was sent.

        _create_background_response raises BackgroundCreateRejected only for
        a 400 naming prompt_cache_breakpoint or, to a request sent as input
        parts, one objecting to their shape (_rejects_input_parts).
        """
        return exc.status_code == 400

    def _note_prompt_cache_breakpoints_rejected(self, call_id: str, exc: Exception,
                                                stage: str, cuts: List[int],
                                                breakpoints: List[int]) -> None:
        """Trace the refusal on the call, whose resend goes as the plain string.

        The call's request and metadata records say so (no input parts or
        breakpoints, off because rejected) and keep the refused cuts as
        planned_input_parts and planned_breakpoints. The provider remembers
        the refusal only once the plain resend is accepted
        (_remember_prompt_cache_breakpoints_rejected).
        """
        message = str(exc)[:500]
        sent = {
            "input_parts": [], "breakpoints": [],
            "prompt_cache_breakpoints_off": "rejected",
            "planned_input_parts": list(cuts),
            "planned_breakpoints": list(breakpoints),
        }
        try:
            self._update_trace_metadata(
                call_id, {**sent, "prompt_cache_breakpoints_rejected": message})
            self._update_trace_request(call_id, sent)
        except (OSError, json.JSONDecodeError) as error:
            self.logger.warning("Could not update OpenAI trace: %s", error)

    def _remember_prompt_cache_breakpoints_rejected(self, exc: Exception,
                                                    stage: str) -> None:
        """Send every later call as the plain string, and log why.

        Called once the plain resend was accepted: a 400 that meets the plain
        string again (the input too long for the context window, a verdict
        under a code not listed as one) was not caused by the parts, and
        turning them off for the whole run would only lose the prompt cache.
        """
        message = str(exc)[:500]
        with self._state_lock:
            self._prompt_cache_breakpoints_rejected = message
        self.logger.warning(
            "OpenAI refused the prompt sent as input parts "
            "(prompt_cache_breakpoint); sending the prompt as "
            "the plain string from now on (stage=%s): %s",
            stage or "unknown", message[:300],
        )

    def _research_identity(self, prompt, system_prompt, json_schema, schema_name) -> dict:
        """The research corpus identity of this provider's two-pass request."""
        return request_identity(
            provider=self.provider_name, endpoint=self.endpoint, model=self.model,
            reasoning_effort=self.reasoning_effort, max_output_tokens=self.max_output_tokens,
            system_prompt=system_prompt or "", prompt=str(prompt), json_schema=json_schema,
            schema_name=schema_name if json_schema else None,
            research_instructions=RESEARCH_PASS_INSTRUCTIONS,
            synthesis_header=SYNTHESIS_RESULT_HEADER,
            synthesis_instructions=SYNTHESIS_PASS_INSTRUCTIONS,
            web_search_tool=dict(self.WEB_SEARCH_TOOL))

    def _generate_two_pass(self, prompt, system_prompt, json_schema, schema_name,
                           trace_stage, trace_context, parent_call_id,
                           research_corpus_scope):
        """Research with web search, then the JSON over it, in two requests.

        The Responses API rejects web search combined with JSON mode (and an
        Anthropic structured output cannot carry cited results). With a
        research corpus and a scope (the shared part of external research),
        the finding's memo answers a request it already answered, then the
        corpus; a miss researches live and, when both passes completed and
        the research was paid for in this pass, is stored there.
        """
        research_instructions = (system_prompt or "") + RESEARCH_PASS_INSTRUCTIONS
        corpus = self.research_corpus if research_corpus_scope is not None else None
        identity = memo_key = None
        memo = active_replay_memo()
        if corpus is not None:
            identity = self._research_identity(prompt, system_prompt, json_schema, schema_name)
            memo_key = MEMO_PREFIX + entry_key(identity)
            # Within one finding the first shared answer wins (a replan reads
            # what the first pass read), whether it was served or researched.
            found = memo.get(memo_key) if memo is not None else None
            if found is not None:
                return self._replay_two_pass(found, prompt, system_prompt, json_schema,
                                             schema_name, trace_stage, trace_context,
                                             parent_call_id)
            entry = None if getattr(_CACHE_READ_STATE, "bypass", False) else corpus.get(identity)
            if entry is not None:
                call_id = self._start_trace(
                    prompt, system_prompt, True, True, json_schema, schema_name,
                    RESEARCH_CORPUS_DELIVERY, trace_stage, trace_context, parent_call_id)
                self._finish_trace(call_id, {"output_text": entry["response"]}, {
                    "status": RESEARCH_CORPUS_DELIVERY, "delivery": RESEARCH_CORPUS_DELIVERY,
                    "latency_ms": 0,
                    "research_corpus": {"key": entry["key"], "created_at": entry["created_at"],
                                        "origin": entry.get("origin", ""),
                                        "response_sha256": entry["response_sha256"]}})
                if memo is not None:
                    memo.put(memo_key, entry["response"], call_id)
                return entry["response"]
        trace_group = (
            f"group-{datetime.now().strftime('%Y%m%dT%H%M%S_%f')}-"
            f"{os.getpid()}-{self.prompt_log_sequence + 1}"
        )
        _LAST_TRACE.call = {}
        research = self.generate(
            prompt,
            research_instructions,
            json_mode=False,
            web_search=True,
            trace_stage=f"{trace_stage}.research",
            trace_context=trace_context,
            parent_call_id=trace_group,
        )
        research_call = dict(getattr(_LAST_TRACE, "call", {}) or {})
        synthesis_prompt = prompt + SYNTHESIS_RESULT_HEADER + research + SYNTHESIS_PASS_INSTRUCTIONS
        _LAST_TRACE.call = {}
        answer = self.generate(
            synthesis_prompt,
            system_prompt,
            json_mode=True,
            web_search=False,
            json_schema=json_schema,
            schema_name=schema_name,
            trace_stage=f"{trace_stage}.synthesis",
            trace_context=trace_context,
            parent_call_id=trace_group,
        )
        synthesis_call = dict(getattr(_LAST_TRACE, "call", {}) or {})
        sent_as_keyed = all(
            call.get("model") == identity["model"]
            and call.get("reasoning_effort") == identity["reasoning_effort"]
            for call in (research_call, synthesis_call)) if identity is not None else False
        if (corpus is not None and research and answer and sent_as_keyed
                and research_call.get("completed") and synthesis_call.get("completed")):
            if memo is not None:
                memo.put(memo_key, answer, synthesis_call.get("call_id", ""))
            if research_call.get("delivery") in PAID_DELIVERIES and not _frozen_experiment():
                corpus.put(identity, research_corpus_scope, answer, research,
                           self._research_provenance(research_call, synthesis_call))
        return answer

    def _replay_two_pass(self, found, prompt, system_prompt, json_schema, schema_name,
                         trace_stage, trace_context, parent_call_id) -> str:
        """The finding's first shared research answer, traced as a replay of the two passes."""
        response, original_call_id = found
        call_id = self._start_trace(
            prompt, system_prompt, True, True, json_schema, schema_name,
            "finding_replay", trace_stage, trace_context, parent_call_id,
        )
        self._finish_trace(call_id, {"output_text": response}, {
            "status": "finding_replay", "delivery": "finding_replay",
            "latency_ms": 0, "replayed_call_id": original_call_id,
        })
        budget = active_budget()
        if budget is not None:
            budget.record_replay()
        return response

    def _research_provenance(self, research_call: Dict, synthesis_call: Dict) -> Dict:
        """Where a corpus entry's answer came from, read from the two calls' traces."""
        from cognalys.llm.cost import trace_call_cost

        provenance: Dict[str, Any] = {
            "research_call_id": research_call.get("call_id", ""),
            "synthesis_call_id": synthesis_call.get("call_id", ""),
            "source": str(self.prompt_log_dir or ""),
            "response_model": "", "web_search_calls": 0, "cost_usd": None,
        }
        if self.prompt_log_dir is None:
            return provenance
        total: Optional[float] = 0.0
        for role, call in (("research", research_call), ("synthesis", synthesis_call)):
            path = self.prompt_log_dir / f"{call.get('call_id', '')}_metadata.json"
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                total = None
                continue
            if role == "research":
                provenance["response_model"] = str(record.get("response_model", "")
                                                   or record.get("served_by_model", "") or "")
                provenance["web_search_calls"] = int(record.get("web_search_calls") or 0)
            cost = trace_call_cost(path, record)
            total = None if cost is None or total is None else total + cost
        provenance["cost_usd"] = None if total is None else round(total, 6)
        return provenance

    def generate(self, prompt, system_prompt=None, json_mode=False,
                 web_search=False, json_schema=None,
                 schema_name="pipeline_response", trace_stage="unspecified",
                 trace_context=None, parent_call_id="", research_corpus_scope=None):
        if json_mode and web_search:
            return self._generate_two_pass(
                prompt, system_prompt, json_schema, schema_name, trace_stage,
                trace_context, parent_call_id, research_corpus_scope)

        cache_request = {
            "provider": self.provider_name,
            "endpoint": self.endpoint,
            "model": self.model,
            "prompt": prompt,
            "system_prompt": system_prompt or "",
            "json_mode": json_mode,
            "json_schema": json_schema,
            "schema_name": schema_name if json_schema else None,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": self.max_output_tokens,
        }
        if web_search:
            # A research answer and a plain answer to the same prompt are
            # different artifacts and must not share a key. The field is
            # added only on this branch so that entries written before web
            # search became cacheable keep hashing to the same key.
            cache_request["web_search"] = True
        # Web results are time-sensitive, so research is cached under a TTL
        # rather than excluded: an identical question asked again within the
        # window reuses the answer, and a stale one refetches. A later
        # synthesis call remains cacheable indefinitely because its prompt
        # already contains the researched evidence.
        ttl = self.web_search_cache_ttl_seconds if web_search else None
        if not web_search or ttl > 0:
            cached = self.cache.get(cache_request, ttl if web_search else None)
            if cached is not None:
                call_id = self._start_trace(
                    prompt, system_prompt, json_mode, web_search, json_schema,
                    schema_name, "cache_hit", trace_stage, trace_context,
                    parent_call_id,
                )
                self._finish_trace(call_id, {"output_text": cached}, {
                    "status": "cache_hit", "delivery": "cache_hit",
                    "latency_ms": 0,
                })
                return cached
        # What the persistent cache could not answer (none, reads bypassed,
        # research at TTL 0) the finding's own earlier identical call can,
        # TTL or not: it was fetched minutes ago for this same analysis.
        replayed = self._replay(
            cache_request, prompt, system_prompt, json_mode, web_search,
            json_schema, schema_name, trace_stage, trace_context, parent_call_id,
        )
        if replayed is not None:
            return replayed

        if web_search and _frozen_experiment():
            # A frozen experiment may use research it already archived and
            # may not fetch more: a live answer is neither repeatable nor
            # recorded, so a result resting on one cannot be reproduced. The
            # caller sees an empty answer, which its own evidence rules
            # already handle as "no external source was available".
            self.logger.warning(
                "frozen experiment: web research not in the cache was not "
                "fetched (stage=%s)", trace_stage or "unknown",
            )
            return ""

        if self._authentication_failure:
            raise ProviderAuthenticationError(self._authentication_failure)
        guard = getattr(self, "_cost_guard", None)
        if guard is not None:
            guard.check()
            if hasattr(guard, "reserve"):
                # B9: the call goes only if its most still fits the ceiling.
                guard.reserve(len(prompt or "") + len(system_prompt or ""),
                              self.max_output_tokens, web_search,
                              "flex" if self._requested_service_tier() == "flex" else "")
        budget = active_budget()
        if budget is not None:
            budget.charge()

        request_input, input_parts, breakpoints, whole_because = (
            self._prompt_cache_request_input(prompt, web_search)
        )
        request_fields: Dict[str, Any] = {
            "input_parts": input_parts, "breakpoints": breakpoints,
        }
        if web_search:
            # The tool the research call sends, so a research corpus entry
            # made from this trace is keyed by it (research_corpus).
            request_fields["web_search_tool"] = dict(self.WEB_SEARCH_TOOL)
        if whole_because:
            request_fields["prompt_cache_breakpoints_off"] = whole_because
        if getattr(prompt, "cache_boundaries", ()):
            # Where the caller said an earlier request of this text ended.
            request_fields["caller_boundaries"] = list(prompt.cache_boundaries)
        call_id = self._start_trace(
            prompt, system_prompt, json_mode, web_search, json_schema,
            schema_name, "sent_to_openai", trace_stage, trace_context,
            parent_call_id, request_fields=request_fields,
        )

        payload = {"model": self.model, "instructions": system_prompt or "",
                   "input": request_input,
                   "reasoning": {"effort": self.reasoning_effort}}
        sending_parts = bool(input_parts)
        if self.max_output_tokens > 0:
            payload["max_output_tokens"] = self.max_output_tokens
        if json_mode:
            if json_schema:
                payload["text"] = {"format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                }}
            else:
                payload["text"] = {"format": {"type": "json_object"}}
        if web_search:
            payload["tools"] = [dict(self.WEB_SEARCH_TOOL)]
        payload["background"] = True
        # A JSON answer is followed as a stream, to stop one that degenerates
        # into whitespace (see STREAM_WATCH_VARIABLE); research is polled.
        if json_mode and not web_search and stream_watch_enabled():
            payload["stream"] = True
        # The answer stopped for a whitespace run: its streamed text, the
        # response it was and what the cancel left.
        degenerate: Optional[Dict[str, Any]] = None
        # How the request is processed, not what it asks: outside
        # cache_request, so the local cache and the finding's replays answer
        # whatever tier produced the text.
        requested_tier = self._requested_service_tier()
        if requested_tier:
            payload["service_tier"] = requested_tier
        # Each time the call left the Flex tier (or a refused tier) for the
        # standard one: why, and the response it abandoned.
        tier_fallbacks: List[Dict] = []
        request_started = time.monotonic()
        request_wall_started = time.time()
        charged = False
        session = self.web_search_session if web_search else self.session
        # Responses cancelled at the deadline while still "queued", each
        # followed by one resubmission of the same request.
        queued_resubmissions: List[str] = []
        retry_history: List[Any] = []
        # Create POSTs sent again after a server fault: what failed and how
        # long the call then waited.
        create_retries: List[Dict] = []
        # Responses that ended failed or cancelled with no content verdict,
        # each sent again: id, code, tier and the usage charged for it.
        failed_responses: List[Dict] = []
        # Codes no list knows (provider_error_kind "unknown") already met by
        # a failed response of this call: the same one again rejects it.
        unknown_codes_met: set = set()
        # What a response abandoned after its polls failed was charged
        # (BackgroundPollFailed), for the error trace.
        poll_failure_charge: Dict[str, Any] = {}
        polls = 0
        # The 400 that refused the parts, until the plain resend is accepted.
        parts_rejection: Optional[BackgroundCreateRejected] = None
        # The deadline of the attempt under way, named by a timeout.
        attempt_deadline = float(self.timeout)
        # Seconds spent on Flex attempts the call abandoned for the standard
        # tier (a 429, a response still queued at the Flex deadline): waiting
        # for capacity, not model time, so the finding's CallBudget (sized on
        # standard-tier latencies) is not charged for them.
        abandoned_flex_seconds = 0.0
        # Flex capacity failures answered by a wait and the same request on
        # Flex again (FLEX_RETRY_WAITS_SECONDS): the code, the response it
        # abandoned and the wait.
        flex_retries: List[Dict] = []

        def model_seconds() -> float:
            return time.monotonic() - request_started - abandoned_flex_seconds

        def resend_on_flex(code: str, response_id: str, attempt_started: float) -> bool:
            """After a Flex capacity failure, wait and say to resend on Flex; False to leave it.

            The failed attempt and the wait are capacity, not model time.
            """
            nonlocal abandoned_flex_seconds
            wait = self._flex_retry_wait(len(flex_retries), time.monotonic() - request_started)
            if wait is None:
                return False
            record: Dict[str, Any] = {"reason": "flex_retry", "detail": code or "",
                                      "waited_s": round(wait, 1)}
            if response_id:
                record["response_id"] = response_id
            flex_retries.append(record)
            self._note_flex_retry(call_id, flex_retries, trace_stage)
            abandoned_flex_seconds += time.monotonic() - attempt_started
            slept_from = time.monotonic()
            self._sleep(wait)
            abandoned_flex_seconds += time.monotonic() - slept_from
            return True

        def wait_to_resend(exc: Exception, record: Dict[str, Any]) -> None:
            """Wait the next create wait (Retry-After honoured) before sending again."""
            wait = self._create_retry_wait(
                len(create_retries), getattr(exc, "retry_after", None))
            create_retries.append({**record, "waited_s": round(wait, 1)})
            self._note_create_retry(call_id, exc, create_retries, trace_stage)
            self._sleep(wait)

        try:
            attempt_started = request_started
            while True:
                try:
                    response = self._create_background_response(session, payload)
                except ServiceTierRejected as exc:
                    # The model or account has no such tier: nothing was
                    # created, and no later call of this provider asks again.
                    with self._state_lock:
                        self._service_tier_rejected = str(exc)[:500]
                    tier_fallbacks.append({"from": payload.pop("service_tier", ""),
                                           "reason": "rejected", "detail": str(exc)[:300]})
                    self._note_service_tier_fallback(call_id, tier_fallbacks, trace_stage)
                    continue
                except BackgroundCreateRateLimited as exc:
                    # Flex answers 429 when it has no capacity, and that is
                    # not billed; OpenAI's guide says to resend on the
                    # standard tier. An exhausted quota is the account's
                    # and would fail there too. A 429 to a standard request
                    # (BackgroundCreateThrottled) is sent again after the
                    # create waits, sharing their limit.
                    if not isinstance(exc, BackgroundCreateThrottled):
                        raise
                    if payload.get("service_tier") != "flex":
                        if len(create_retries) >= len(self.CREATE_RETRY_WAITS_SECONDS):
                            raise
                        wait_to_resend(exc, {"status": 429,
                                             "error": exc.code or type(exc).__name__})
                        if guard is not None:
                            guard.check()
                        attempt_started = time.monotonic()
                        request_wall_started = time.time()
                        continue
                    tier_fallbacks.append({"from": payload.pop("service_tier"),
                                           "reason": "rate_limited",
                                           "detail": exc.code or "HTTP 429"})
                    self._note_service_tier_fallback(call_id, tier_fallbacks, trace_stage)
                    if guard is not None:
                        guard.check()
                    abandoned_flex_seconds += time.monotonic() - attempt_started
                    attempt_started = time.monotonic()
                    request_wall_started = time.time()
                    continue
                except BackgroundCreateRejected as exc:
                    if (not sending_parts
                            or not self._rejects_prompt_cache_breakpoints(exc)):
                        raise
                    # Nothing was created; the same prompt goes once more as
                    # the plain string, and so does every later call once
                    # that resend is accepted.
                    payload["input"] = prompt
                    sending_parts = False
                    parts_rejection = exc
                    self._note_prompt_cache_breakpoints_rejected(
                        call_id, exc, trace_stage, input_parts, breakpoints)
                    continue
                except (BackgroundCreateFailed,
                        requests.exceptions.ConnectionError) as exc:
                    retry_history.extend(getattr(exc, "retry_history", ()) or ())
                    if (payload.get("service_tier") == "flex"
                            and provider_error_kind(getattr(exc, "code", "")) == "capacity"):
                        # A 5xx saying Flex has no capacity (server_is_overloaded):
                        # sent on Flex again after the Flex waits, as after a
                        # failed Flex response with that code, then on the
                        # standard tier, using no create wait.
                        if resend_on_flex(getattr(exc, "code", ""), "", attempt_started):
                            if guard is not None:
                                guard.check()
                            attempt_started = time.monotonic()
                            request_wall_started = time.time()
                            continue
                        tier_fallbacks.append({"from": payload.pop("service_tier"),
                                               "reason": "overloaded", "detail": exc.code})
                        self._note_service_tier_fallback(call_id, tier_fallbacks, trace_stage)
                        if guard is not None:
                            guard.check()
                        abandoned_flex_seconds += time.monotonic() - attempt_started
                        attempt_started = time.monotonic()
                        request_wall_started = time.time()
                        continue
                    if len(create_retries) >= len(self.CREATE_RETRY_WAITS_SECONDS):
                        raise
                    wait_to_resend(exc, {"status": getattr(exc, "status_code", None),
                                         "error": type(exc).__name__})
                    # Other findings may have reached the run's ceiling meanwhile.
                    if guard is not None:
                        guard.check()
                    attempt_started = time.monotonic()
                    request_wall_started = time.time()
                    continue
                if parts_rejection is not None:
                    self._remember_prompt_cache_breakpoints_rejected(
                        parts_rejection, trace_stage)
                    parts_rejection = None
                retry_state = getattr(getattr(response, "raw", None), "retries", None)
                retry_history.extend(getattr(retry_state, "history", ()) or ())
                on_flex = payload.get("service_tier") == "flex"
                attempt_deadline = (max(float(self.timeout), float(self.FLEX_DEADLINE_SECONDS))
                                    if on_flex else float(self.timeout))
                streamed_status = False
                if self._is_event_stream(response):
                    data, detector, streamed_status = self._read_output_stream(
                        response, attempt_started, attempt_deadline)
                    if detector is not None:
                        # Billed for what it generated until the cancel; the
                        # settled response says how much.
                        response_id = str(data.get("id", ""))
                        cancel_answer = self._cancel_background_response(session, response_id)
                        data = self._settle_cancelled(session, response_id, cancel_answer)
                        degenerate = {"text": "".join(detector.text),
                                      "whitespace_run": detector.run,
                                      "response_id": response_id}
                        self.logger.warning(
                            "OpenAI response %s degenerated into %d characters of "
                            "whitespace after %d characters of text; cancelled it "
                            "(stage=%s, status now %s)", response_id, detector.run,
                            len(degenerate["text"]) - detector.run, trace_stage or "unknown",
                            data.get("status", ""))
                        break
                else:
                    data = response.json()
                try:
                    if str(data.get("status", "")) not in self._PENDING_STATUSES:
                        # A final answer read whole (a stream's terminal event,
                        # a create that answered at once): a failure carries
                        # its error code, a content verdict included, as the
                        # polls do.
                        self._raise_for_failed_response(data)
                        break
                    data, attempt_polls = self._await_background_response(
                        session, data, attempt_started,
                        deadline_seconds=attempt_deadline if on_flex else None,
                        streamed_status=streamed_status,
                    )
                    polls += attempt_polls
                    break
                except BackgroundResponseFailed as exc:
                    # The service failed to answer (server_is_overloaded,
                    # server_error, a code no list knows): no verdict on the
                    # text, so the same request goes again. What the failed
                    # response reports it used is charged first.
                    polls += exc.polls
                    failed_responses.append(self._charge_failed_response(
                        session, exc, str(payload.get("service_tier", "") or ""),
                        prompt, system_prompt))
                    if provider_error_kind(exc.code) == "unknown":
                        if exc.code in unknown_codes_met:
                            # The same code no list knows, twice: an answer
                            # about the request, not the service failing.
                            raise ProviderRequestRejected(
                                f"OpenAI failed this request twice with the same "
                                f"unknown code ({exc.code}): {exc}",
                                code=exc.code, status_code=200,
                            ) from exc
                        unknown_codes_met.add(exc.code)
                        self.logger.warning(
                            "OpenAI response %s failed with a code no list knows (%s); "
                            "sending it once more (stage=%s)", exc.response_id, exc.code,
                            trace_stage or "unknown")
                    if on_flex and provider_error_kind(exc.code) == "capacity":
                        # Flex had no capacity: the same request goes on Flex
                        # again after the Flex waits, and only then on the
                        # standard tier.
                        if not resend_on_flex(exc.code, exc.response_id, attempt_started):
                            tier_fallbacks.append({"from": payload.pop("service_tier"),
                                                   "reason": "overloaded", "detail": exc.code,
                                                   "response_id": exc.response_id})
                            self._note_service_tier_fallback(
                                call_id, tier_fallbacks, trace_stage)
                            abandoned_flex_seconds += time.monotonic() - attempt_started
                    else:
                        # Otherwise after the waits of a create that failed
                        # on OpenAI's side, sharing their limit.
                        if len(create_retries) >= len(self.CREATE_RETRY_WAITS_SECONDS):
                            raise
                        wait_to_resend(exc, {"status": exc.status,
                                             "error": exc.code or type(exc).__name__,
                                             "response_id": exc.response_id})
                    if guard is not None:
                        guard.check()
                    attempt_started = time.monotonic()
                    request_wall_started = time.time()
                    continue
                except BackgroundPollFailed as exc:
                    # Abandoned and cancelled: it may have run, so it is
                    # settled and charged as a response cancelled at the
                    # deadline is, and the call ends (provider_unavailable).
                    polls += exc.polls
                    poll_failure_charge = self._charge_timed_out_response(
                        session, exc, prompt, system_prompt,
                        str(payload.get("service_tier", "") or ""))
                    raise
                except BackgroundResponseNeverStarted as exc:
                    polls += exc.polls
                    if on_flex:
                        # Flex never started it within its deadline, and the
                        # cancel confirmed it ran nothing: the standard tier
                        # takes the same request, under self.timeout.
                        tier_fallbacks.append({"from": payload.pop("service_tier"),
                                               "reason": "queued",
                                               "detail": exc.response_id})
                        self._note_service_tier_fallback(
                            call_id, tier_fallbacks, trace_stage)
                        if guard is not None:
                            guard.check()
                        abandoned_flex_seconds += time.monotonic() - attempt_started
                        attempt_started = time.monotonic()
                        request_wall_started = time.time()
                        continue
                    # Tokn (rvsec_tokn_20261028/run_v5, 2026-10-31): an
                    # evidence extraction sat "queued" for 600s, the timeout
                    # crashed the whole analysis (Analyzed 0/4), and Grocy's
                    # research (rvsec_grocy_20261022) lost its web evidence
                    # the same way. Every such timeout in the run logs was
                    # "queued": OpenAI never started the response, so it
                    # produced and cost nothing and the same request can go
                    # again, once, under a fresh deadline. A second stuck
                    # one ends as before.
                    if len(queued_resubmissions) >= self.QUEUED_RESUBMISSIONS:
                        raise
                    queued_resubmissions.append(exc.response_id)
                    self._note_queued_resubmission(
                        call_id, exc, queued_resubmissions, trace_stage,
                    )
                    # Nothing was spent on the cancelled attempt, but other
                    # findings may have reached the run's ceiling meanwhile.
                    if guard is not None:
                        guard.check()
                    attempt_started = time.monotonic()
                    request_wall_started = time.time()
            data = self._with_final_usage(session, data)
            usage = data.get("usage") or {}
            if not isinstance(usage, dict):
                usage = {}
            with self._state_lock:
                self.token_usage["input"] += usage.get("input_tokens", 0)
                self.token_usage["output"] += usage.get("output_tokens", 0)
            input_details = usage.get("input_tokens_details", {})
            if not isinstance(input_details, dict):
                input_details = {}
            cached_tokens = input_details.get("cached_tokens", 0)
            # Tokens written to the prompt cache, part of input_tokens; a
            # response without the field wrote none that it reports.
            cache_write_tokens = input_details.get("cache_write_tokens", 0) or 0
            # The tier that served the call, and the web search tool calls
            # it made by action type; cost.py bills the "search" ones.
            served_tier = str(data.get("service_tier", "") or "")
            search_actions = web_search_actions(data)
            search_calls = billed_web_search_calls(search_actions)
            guard_estimate = self._unreported_usage_estimate(
                data, prompt, system_prompt, stopped=degenerate is not None)
            if getattr(self, "_cost_guard", None) is not None:
                if guard_estimate:
                    # An answer that ran but reports no usage still cost
                    # something: the guard charges an upper bound.
                    self._cost_guard.add(guard_estimate["input_tokens"], 0,
                                         guard_estimate["output_tokens"],
                                         service_tier=served_tier,
                                         web_search_calls=search_calls)
                else:
                    self._cost_guard.add(usage.get("input_tokens", 0) or 0, cached_tokens or 0,
                                         usage.get("output_tokens", 0) or 0,
                                         cache_write_tokens=cache_write_tokens,
                                         service_tier=served_tier,
                                         web_search_calls=search_calls)
            trace_metadata = {
                "status": "success",
                "delivery": "sent_to_openai",
                "input_tokens": usage.get("input_tokens", 0),
                "cached_input_tokens": cached_tokens,
                "cache_write_input_tokens": cache_write_tokens,
                "output_tokens": usage.get("output_tokens", 0),
                "service_tier": served_tier,
                "web_search_actions": search_actions,
                "web_search_calls": search_calls,
                "latency_ms": round((time.monotonic() - request_started) * 1000),
                **self._retry_trace(retry_history),
                "create_retries": list(create_retries),
                # How long after sending the server created the response
                # that succeeded. A retry after a hung connection records no
                # status and no error, so this is the only trace of it: a
                # value near the timeout means an attempt was lost.
                "server_created_after_ms": (
                    max(0, round((float(data["created_at"]) - request_wall_started) * 1000))
                    if isinstance(data.get("created_at"), (int, float)) else None
                ),
                "request_id": getattr(response, "headers", {}).get(
                    "x-request-id", ""
                ),
                "openai_response_id": data.get("id", ""),
                # The snapshot that answered ("model" above is the name
                # asked for); recorded only, nothing decides on it.
                "response_model": str(data.get("model", "") or ""),
                "response_status": data.get("status", ""),
                "background_polls": polls,
                "queued_resubmissions": len(queued_resubmissions),
                "queued_cancelled_response_ids": list(queued_resubmissions),
                "requested_service_tier": requested_tier,
                "service_tier_fallbacks": list(tier_fallbacks),
                "flex_retries": list(flex_retries),
                FAILED_RESPONSES_FIELD: list(failed_responses),
            }
            if guard_estimate:
                trace_metadata["guard_usage_estimate"] = guard_estimate
            if budget is not None:
                budget.record(model_seconds(), retries=len(retry_history))
                charged = True
            if degenerate is not None:
                # Returned so the caller's shape check rejects it and retries,
                # as it did with the truncated answer; never cached or
                # replayed, since it is no answer.
                trace_metadata["degenerate_output_stopped"] = {
                    "whitespace_run": degenerate["whitespace_run"],
                    "characters_streamed": len(degenerate["text"]),
                    "response_id": degenerate["response_id"],
                }
                self._finish_trace(call_id, {**data, "streamed_output_text": degenerate["text"]},
                                   trace_metadata)
                return degenerate["text"]
            if data.get("output_text"):
                response_text = data["output_text"]
                if not web_search or self.web_search_cache_ttl_seconds > 0:
                    self.cache.put(cache_request, response_text)
                # Only a completed answer is replayed: an incomplete,
                # failed or cancelled one holds whatever text it reached.
                if data.get("status") == "completed":
                    self.cache.remember(cache_request, response_text, call_id)
                self._finish_trace(call_id, data, trace_metadata)
                return response_text
            texts = [content.get("text", "")
                     for item in data.get("output", [])
                     for content in item.get("content", [])
                     if content.get("type") == "output_text"]
            if texts:
                response_text = "\n".join(texts)
                if not web_search or self.web_search_cache_ttl_seconds > 0:
                    self.cache.put(cache_request, response_text)
                if data.get("status") == "completed":
                    self.cache.remember(cache_request, response_text, call_id)
                self._finish_trace(call_id, data, trace_metadata)
                return response_text
            self._finish_trace(call_id, data, {
                **trace_metadata, "status": "missing_output_text",
            })
            raise RuntimeError("OpenAI returned no output_text")
        except requests.exceptions.Timeout as exc:
            if budget is not None:
                budget.record(model_seconds(), failed=True)
            # A response cancelled at the deadline may have run (Flex bills a
            # cancelled response the whole output ceiling): charged here, and
            # recorded in the trace for the guards and reports rebuilt from it.
            charged_fields: Dict[str, Any] = {}
            if isinstance(exc, BackgroundResponseTimedOut) and exc.response_id:
                charged_fields = self._charge_timed_out_response(
                    session, exc, prompt, system_prompt,
                    str(payload.get("service_tier", "") or ""))
            self._finish_trace(call_id, {"error": "timeout"}, {
                "status": "timeout",
                "latency_ms": round((time.monotonic() - request_started) * 1000),
                "queued_resubmissions": len(queued_resubmissions),
                "queued_cancelled_response_ids": list(queued_resubmissions),
                "requested_service_tier": requested_tier,
                "service_tier_fallbacks": list(tier_fallbacks),
                "flex_retries": list(flex_retries),
                FAILED_RESPONSES_FIELD: list(failed_responses),
                **charged_fields,
            })
            resubmitted = (
                f" (resubmitted after {', '.join(queued_resubmissions)} "
                "was still queued)" if queued_resubmissions else ""
            )
            # The deadline that applied: the Flex one when the attempt was on
            # Flex, self.timeout otherwise.
            deadline = (exc.deadline if isinstance(exc, BackgroundResponseTimedOut)
                        and exc.deadline else attempt_deadline)
            on_flex_deadline = (isinstance(exc, BackgroundResponseTimedOut)
                                and payload.get("service_tier") == "flex"
                                and deadline > float(self.timeout))
            raise TimeoutError(
                f"OpenAI request timed out after {deadline:g}s"
                + (" (the Flex tier's deadline)" if on_flex_deadline else "")
                + f": {exc}{resubmitted}"
            ) from exc
        except Exception as exc:
            # A response that arrived and then failed (no output_text) was
            # already charged its latency above; count it as failed only.
            if budget is not None:
                if charged:
                    budget.failed_calls += 1
                else:
                    budget.record(model_seconds(), failed=True)
            self._finish_trace(call_id, {
                "error": type(exc).__name__,
                "message": str(exc),
            }, {
                "status": "error",
                "latency_ms": round((time.monotonic() - request_started) * 1000),
                "error_type": type(exc).__name__,
                # The provider's own code (a failed response's, a refusal's),
                # so a survey of the traces need not parse the message.
                "error_code": str(getattr(exc, "code", "") or ""),
                # Salty run_v4's HTTP 500 trace had no retry_statuses: the
                # error path dropped what urllib3 had already retried.
                **self._retry_trace(retry_history),
                "create_retries": list(create_retries),
                "queued_resubmissions": len(queued_resubmissions),
                "queued_cancelled_response_ids": list(queued_resubmissions),
                "requested_service_tier": requested_tier,
                "service_tier_fallbacks": list(tier_fallbacks),
                "flex_retries": list(flex_retries),
                FAILED_RESPONSES_FIELD: list(failed_responses),
                **poll_failure_charge,
            })
            raise

    def get_token_usage(self):
        return self.token_usage.copy()

    def get_cache_stats(self):
        return self.cache.stats()


# Keywords the Anthropic structured-output schema compiler rejects. The
# pipeline validates every answer itself afterwards, so dropping a bound
# from the request loses no check.
_ANTHROPIC_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "pattern", "maxItems", "uniqueItems",
    "minProperties", "maxProperties",
})
_ANTHROPIC_SCHEMA_FORMATS = frozenset({
    "date-time", "time", "date", "duration", "email", "hostname", "uri",
    "ipv4", "ipv6", "uuid",
})
# The Responses API's reasoning efforts that have no Anthropic counterpart.
_ANTHROPIC_EFFORTS = {"minimal": "low", "none": "low"}


def anthropic_json_schema(schema: Any) -> Any:
    """``schema`` as Anthropic's structured outputs accept it.

    Bounds it does not support are removed (see the set above), a minItems
    above 1 likewise, and every object gets additionalProperties false,
    which the API requires.
    """
    if isinstance(schema, list):
        return [anthropic_json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned = {}
    for key, value in schema.items():
        if key in _ANTHROPIC_UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key == "minItems" and not (isinstance(value, int) and value <= 1):
            continue
        if key == "format" and value not in _ANTHROPIC_SCHEMA_FORMATS:
            continue
        if key in {"properties", "$defs", "definitions"} and isinstance(value, dict):
            cleaned[key] = {name: anthropic_json_schema(item) for name, item in value.items()}
        else:
            cleaned[key] = anthropic_json_schema(value)
    if cleaned.get("type") == "object" or "properties" in cleaned:
        cleaned["additionalProperties"] = False
    return cleaned


class AnthropicProvider(OpenAIProvider):
    """Claude models through the Anthropic Messages API (official SDK).

    Same contract as OpenAIProvider -- generate() with JSON mode, a JSON
    schema and web search, the response cache, prompt traces, the
    per-finding call budget and the run's cost ceiling -- so every step
    takes either one. Selected with llm-provider = "anthropic" (see
    remote_provider below); the key comes from ANTHROPIC_API_KEY
    (.env.anthropic).

    Requests are streamed (long generations at the configured
    max_output_tokens would otherwise hit HTTP timeouts), use adaptive
    thinking with llm-reasoning-effort as the effort, and opt into the
    server-side refusal fallback, so a prompt a safety classifier declines
    is answered by the model the API routes it to instead of failing the
    finding (COGNALYS_ANTHROPIC_FALLBACK=0 turns that off).
    """

    provider_name = "anthropic"
    # Its structured outputs drop the bounds anthropic_json_schema removes,
    # so a prompt keeps the schema's text for it.
    enforces_json_schema = False
    WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}
    FALLBACK_BETA = "server-side-fallback-2026-07-01"
    # A web-search turn the server pauses (its own tool loop hit its limit)
    # is resumed at most this many times.
    MAX_PAUSE_CONTINUATIONS = 5
    DEFAULT_MAX_TOKENS = 64000

    def __init__(self, endpoint="https://api.anthropic.com",
                 model="claude-opus-5-5", timeout=600,
                 max_output_tokens=32768, reasoning_effort="high",
                 cache_dir: Optional[Path] = None,
                 prompt_log_dir: Optional[Path] = None, logger=None,
                 web_search_cache_ttl_seconds: int = 7 * 24 * 3600,
                 web_search_retries: int = 1, retries: int = 2,
                 refusal_fallback: Optional[bool] = None):
        try:
            import anthropic
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing dependency: anthropic (pip install 'anthropic>=1.15,<2')"
            ) from exc
        self._sdk = anthropic
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is missing; configure .env.anthropic")
        # An OpenAI endpoint left in a run file, or one ending in /v1, still
        # names the API root the SDK expects.
        endpoint = str(endpoint or "https://api.anthropic.com").rstrip("/")
        if "openai.com" in endpoint:
            endpoint = "https://api.anthropic.com"
        if endpoint.endswith("/v1"):
            endpoint = endpoint[:-3]
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        effort = str(reasoning_effort or "high").strip().lower()
        self.reasoning_effort = _ANTHROPIC_EFFORTS.get(effort, effort)
        if refusal_fallback is None:
            refusal_fallback = str(os.environ.get("COGNALYS_ANTHROPIC_FALLBACK", "1")
                                   ).strip().lower() not in {"0", "false", "no", "off"}
        self.refusal_fallback = bool(refusal_fallback)
        self.logger = logger or logging.getLogger("fix_analysis")
        self.cache = LLMResponseCache(cache_dir, self.logger)
        self.prompt_log_dir = (
            Path(prompt_log_dir).expanduser().resolve() if prompt_log_dir else None
        )
        self.prompt_log_sequence = 0
        self._state_lock = threading.Lock()
        self._sleep = time.sleep
        self.requested_context_length = None
        self.context_length = None
        self.last_context_length = None
        self.token_usage = {"input": 0, "output": 0}
        self.web_search_cache_ttl_seconds = max(0, int(web_search_cache_ttl_seconds))
        self.web_search_retries = max(0, int(web_search_retries))
        self.retries = max(0, int(retries))
        self._authentication_failure = ""
        from cognalys.llm.cost import RunCostGuard
        self._cost_guard = RunCostGuard.from_environment(self.prompt_log_dir, self.model)
        # The SDK retries 408/409/429/5xx and connection errors itself,
        # honouring Retry-After.
        self.client = anthropic.Anthropic(
            api_key=self.api_key, base_url=self.endpoint,
            timeout=float(timeout), max_retries=self.retries,
        )

    @property
    def headers(self):
        return {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"}

    def test_connection(self):
        try:
            self.client.models.retrieve(self.model)
            return True
        except self._sdk.AuthenticationError as exc:
            self.logger.error("Anthropic rejected the API key: %s", exc)
        except self._sdk.NotFoundError:
            self.logger.error("Anthropic has no model named %s", self.model)
        except Exception as exc:
            self.logger.error("Anthropic connection failed: %s", exc)
        return False

    def _request(self, prompt, system_prompt, json_mode, web_search, json_schema) -> Dict:
        params: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": (self.max_output_tokens if self.max_output_tokens > 0
                           else self.DEFAULT_MAX_TOKENS),
            "messages": [{"role": "user", "content": prompt}],
            "thinking": {"type": "adaptive"},
        }
        output_config: Dict[str, Any] = {"effort": self.reasoning_effort}
        system = system_prompt or ""
        if json_mode:
            if json_schema:
                output_config["format"] = {
                    "type": "json_schema", "schema": anthropic_json_schema(json_schema),
                }
            else:
                # There is no schema-free JSON mode; the instruction and the
                # pipeline's tolerant parser take its place.
                system = (system + "\n\nRespond with a single JSON object and "
                          "nothing else.").strip()
        params["output_config"] = output_config
        if system:
            # The system prompt is the stable prefix many calls share.
            params["system"] = [{"type": "text", "text": system,
                                 "cache_control": {"type": "ephemeral"}}]
        if web_search:
            params["tools"] = [dict(self.WEB_SEARCH_TOOL)]
        if self.refusal_fallback:
            params["betas"] = [self.FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _stream(self, params: Dict, web_search: bool):
        client = (self.client.with_options(max_retries=self.web_search_retries)
                  if web_search else self.client)
        with client.beta.messages.stream(**params) as stream:
            return stream.get_final_message()

    @staticmethod
    def _text_of(message) -> Tuple[str, List[str]]:
        """The answer text (thinking left out) and the URLs it cites."""
        parts, urls = [], []
        for block in getattr(message, "content", None) or []:
            if getattr(block, "type", "") != "text":
                continue
            parts.append(block.text)
            for citation in getattr(block, "citations", None) or []:
                url = getattr(citation, "url", "")
                if url and url not in urls:
                    urls.append(url)
        return "".join(parts), urls

    def generate(self, prompt, system_prompt=None, json_mode=False,
                 web_search=False, json_schema=None,
                 schema_name="pipeline_response", trace_stage="unspecified",
                 trace_context=None, parent_call_id="", research_corpus_scope=None):
        if json_mode and web_search:
            # The same two passes as OpenAIProvider (research, then the JSON
            # over it); its branch only recurses into self.generate. Cited
            # web results and a structured output cannot share a request.
            return super().generate(
                prompt, system_prompt, json_mode=json_mode, web_search=web_search,
                json_schema=json_schema, schema_name=schema_name,
                trace_stage=trace_stage, trace_context=trace_context,
                parent_call_id=parent_call_id,
                research_corpus_scope=research_corpus_scope,
            )

        cache_request = {
            "provider": self.provider_name,
            "endpoint": self.endpoint,
            "model": self.model,
            "prompt": prompt,
            "system_prompt": system_prompt or "",
            "json_mode": json_mode,
            "json_schema": json_schema,
            "schema_name": schema_name if json_schema else None,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": self.max_output_tokens,
        }
        if web_search:
            cache_request["web_search"] = True
        ttl = self.web_search_cache_ttl_seconds if web_search else None
        if not web_search or ttl > 0:
            cached = self.cache.get(cache_request, ttl if web_search else None)
            if cached is not None:
                call_id = self._start_trace(
                    prompt, system_prompt, json_mode, web_search, json_schema,
                    schema_name, "cache_hit", trace_stage, trace_context,
                    parent_call_id,
                )
                self._finish_trace(call_id, {"output_text": cached}, {
                    "status": "cache_hit", "delivery": "cache_hit", "latency_ms": 0,
                })
                return cached
        replayed = self._replay(
            cache_request, prompt, system_prompt, json_mode, web_search,
            json_schema, schema_name, trace_stage, trace_context, parent_call_id,
        )
        if replayed is not None:
            return replayed

        if web_search and _frozen_experiment():
            self.logger.warning(
                "frozen experiment: web research not in the cache was not "
                "fetched (stage=%s)", trace_stage or "unknown",
            )
            return ""

        if self._authentication_failure:
            raise ProviderAuthenticationError(self._authentication_failure)
        guard = getattr(self, "_cost_guard", None)
        if guard is not None:
            guard.check()
            if hasattr(guard, "reserve"):
                # B9: the call goes only if its most still fits the ceiling.
                guard.reserve(len(prompt or "") + len(system_prompt or ""),
                              self.max_output_tokens, web_search)
        budget = active_budget()
        if budget is not None:
            budget.charge()

        call_id = self._start_trace(
            prompt, system_prompt, json_mode, web_search, json_schema,
            schema_name, "sent_to_anthropic", trace_stage, trace_context,
            parent_call_id,
            request_fields={"web_search_tool": dict(self.WEB_SEARCH_TOOL)} if web_search else None,
        )
        sdk = self._sdk
        params = self._request(prompt, system_prompt, json_mode, web_search, json_schema)
        request_started = time.monotonic()
        # web_searches: the billed searches of every streamed message (a
        # paused turn's resumption reports only its own).
        totals = {"input": 0, "cached": 0, "cache_write": 0, "output": 0,
                  "web_searches": 0}
        texts: List[str] = []
        urls: List[str] = []
        message = None
        continuations = 0
        try:
            while True:
                message = self._stream(params, web_search)
                usage = getattr(message, "usage", None)
                totals["input"] += getattr(usage, "input_tokens", 0) or 0
                totals["cached"] += getattr(usage, "cache_read_input_tokens", 0) or 0
                totals["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
                totals["output"] += getattr(usage, "output_tokens", 0) or 0
                totals["web_searches"] += anthropic_web_search_requests(usage)
                text, cited = self._text_of(message)
                texts.append(text)
                urls.extend(url for url in cited if url not in urls)
                if (message.stop_reason != "pause_turn"
                        or continuations >= self.MAX_PAUSE_CONTINUATIONS):
                    break
                # The server stopped its own web-search loop mid-turn; sending
                # the turn back resumes it (no "continue" message is added).
                continuations += 1
                params = {**params, "messages": [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": message.content},
                ]}
        except sdk.AuthenticationError as exc:
            with self._state_lock:
                self._authentication_failure = (
                    "Anthropic rejected the API key (HTTP 401); no further call "
                    "is made in this process: " + str(exc)[:300]
                )
            self._record_failure(call_id, budget, request_started, exc)
            raise ProviderAuthenticationError(self._authentication_failure) from exc
        except sdk.APITimeoutError as exc:
            self._record_failure(call_id, budget, request_started, exc, status="timeout")
            raise TimeoutError(
                f"Anthropic request timed out after {self.timeout}s: {exc}"
            ) from exc
        except Exception as exc:
            self._record_failure(call_id, budget, request_started, exc)
            raise

        # The API reports cache reads and writes apart from input_tokens; the
        # pipeline's ledger counts input as the total, cached included.
        input_total = totals["input"] + totals["cached"] + totals["cache_write"]
        with self._state_lock:
            self.token_usage["input"] += input_total
            self.token_usage["output"] += totals["output"]
        # The search fee, charged on top of the tokens as OpenAI's is: the
        # --max-cost guard counted none of it before 2026-11-14 (12.6% of
        # the D arm's spend of 2026-11-13).
        search_calls = totals["web_searches"]
        if guard is not None:
            guard.add(input_total, totals["cached"], totals["output"],
                      cache_write_tokens=totals["cache_write"],
                      web_search_calls=search_calls)
        if budget is not None:
            budget.record(time.monotonic() - request_started)
        stop_reason = str(getattr(message, "stop_reason", "") or "")
        served_by = str(getattr(message, "model", "") or self.model)
        trace_metadata = {
            "status": "success",
            "delivery": "sent_to_anthropic",
            "input_tokens": input_total,
            "cached_input_tokens": totals["cached"],
            "cache_write_input_tokens": totals["cache_write"],
            "output_tokens": totals["output"],
            "web_search_actions": {"search": search_calls} if search_calls else {},
            "web_search_calls": search_calls,
            "latency_ms": round((time.monotonic() - request_started) * 1000),
            "retry_count": 0,
            "request_id": str(getattr(message, "_request_id", "") or ""),
            "anthropic_message_id": str(getattr(message, "id", "") or ""),
            "stop_reason": stop_reason,
            "served_by_model": served_by,
            # As the response gave it, empty when it gave none (served_by
            # falls back to the model asked for).
            "response_model": str(getattr(message, "model", "") or ""),
            "pause_continuations": continuations,
        }
        try:
            response_record = message.to_dict()
        except Exception:
            response_record = {"output_text": "".join(texts)}

        if stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = str(getattr(details, "category", "") or "refusal")
            self._finish_trace(call_id, response_record, {
                **trace_metadata, "status": "refused", "refusal_category": category,
            })
            raise ProviderContentRefusal(
                (f"Anthropic declined this request's content ({category}): "
                 + str(getattr(details, "explanation", "") or ""))[:1000],
                code=category, status_code=200,
            )
        response_text = "".join(texts).strip()
        if web_search and urls and not all(url in response_text for url in urls):
            # A research brief must carry its source URLs in the text; cited
            # results arrive as citations on the text blocks instead.
            response_text += "\n\nSources:\n" + "\n".join(f"- {url}" for url in urls)
        if not response_text:
            self._finish_trace(call_id, response_record, {
                **trace_metadata, "status": "missing_output_text",
            })
            raise RuntimeError(f"Anthropic returned no text (stop_reason={stop_reason})")
        if stop_reason == "max_tokens":
            self.logger.warning(
                "Anthropic answer for %s stopped at max_tokens (%s); it may be "
                "incomplete", trace_stage, params["max_tokens"],
            )
        if served_by != self.model:
            self.logger.warning("%s was answered by %s (refusal fallback)",
                                trace_stage, served_by)
        if not web_search or self.web_search_cache_ttl_seconds > 0:
            self.cache.put(cache_request, response_text)
        if stop_reason != "max_tokens":
            # A truncated answer may be completed by the next try.
            self.cache.remember(cache_request, response_text, call_id)
        self._finish_trace(call_id, response_record, trace_metadata)
        return response_text

    def _record_failure(self, call_id, budget, request_started, exc, status="error"):
        if budget is not None:
            budget.record(time.monotonic() - request_started, failed=True)
        self._finish_trace(call_id, {
            "error": type(exc).__name__, "message": str(exc)[:2000],
        }, {
            "status": status,
            "latency_ms": round((time.monotonic() - request_started) * 1000),
            "error_type": type(exc).__name__,
        })


PROVIDER_VARIABLE = "COGNALYS_LLM_PROVIDER"
REMOTE_PROVIDERS = ("openai", "anthropic")


def remote_provider_name(model: str = "", provider: str = "") -> str:
    """The paid provider a step uses: "anthropic" or "openai".

    The pipeline sets COGNALYS_LLM_PROVIDER from llm-provider for every
    step it starts; a step run on its own goes by the model's name.
    """
    chosen = str(provider or os.environ.get(PROVIDER_VARIABLE, "")).strip().lower()
    if chosen in REMOTE_PROVIDERS:
        return chosen
    return "anthropic" if str(model or "").startswith("claude-") else "openai"


def remote_provider(provider: str = "", openai_class=None, **kwargs):
    """The paid provider client: AnthropicProvider or OpenAIProvider.

    ``openai_class`` is the caller's own OpenAIProvider name, passed so a
    test that patches it in that module still replaces the client.
    """
    name = remote_provider_name(str(kwargs.get("model", "")), provider)
    if name == "anthropic":
        return AnthropicProvider(**kwargs)
    return (openai_class or OpenAIProvider)(**kwargs)
