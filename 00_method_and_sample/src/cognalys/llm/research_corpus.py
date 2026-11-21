"""Answers to shared research requests, kept per request across runs.

The shared part of external research (the questions about the governed API
and the rule, asked without the finding) is the same request for every
finding of one specification and message. The response cache keeps it for
a week, and a run without that cache (--no-llm-cache, a TTL of 0: the
benchmark's settings) pays for it every time: US$ 0.0566 per finding in the
Flex replicas v4 and v5 of agent_baseline_20261108, 29% of their cost.

The corpus keeps the answer of a request researched live with both passes
(research and synthesis) complete, under a key made of the request itself:
provider, endpoint, model, reasoning effort, output limit, both prompts, the
schema, the two pass instructions and the web search tool. Any changed byte
is another entry; nothing matches "similar" scopes or groups by
specification, the case part is never served, and a miss researches in full
at the run's effort. An entry is served while younger than the maximum age
and is never replaced by a run (only an expired one is superseded).

A leaf module: it imports cognalys.llm.cost and the standard library only,
so llm_providers can import it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from cognalys.llm import cost

FORMAT_VERSION = 1
SUPERSEDED_DIRECTORY = "superseded"
MEMO_PREFIX = "research_corpus:"
# The two passes of a research request (generate with json_mode and web_search).
RESEARCH_PASS_INSTRUCTIONS = (
    "\n\nFor this research pass, use web search as needed. "
    "Return a factual research brief and include direct source "
    "URLs in the text. Do not omit uncertainty or source limits."
)
SYNTHESIS_RESULT_HEADER = "\n\nWEB RESEARCH RESULT:\n"
SYNTHESIS_PASS_INSTRUCTIONS = (
    "\n\nProduce the originally requested JSON using the "
    "research above as additional evidence. Preserve direct "
    "source URLs and do not add claims unsupported by either "
    "the original input or the research result."
)
IDENTITY_FIELDS = ("provider", "endpoint", "model", "reasoning_effort", "max_output_tokens",
                   "system_prompt", "prompt", "json_schema", "schema_name",
                   "research_instructions", "synthesis_header", "synthesis_instructions",
                   "web_search_tool")
# The tool a provider sent before a research call's trace recorded it (request
# field "web_search_tool"). OpenAI's is unchanged in the file's history; the
# Anthropic tool appeared in de1c4d3 (2026-10-28), and no Anthropic research
# pass recorded before the field was completed.
TOOL_BEFORE_TRACE_FIELD = {
    "openai": {"type": "web_search_preview", "search_context_size": "medium"},
    "anthropic": {"type": "web_search_20260209", "name": "web_search"},
}
# Stop reasons of an Anthropic answer that ended on its own.
ANTHROPIC_COMPLETE_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})
# Deliveries that already answered as a response (the TTL cache, the
# finding's own earlier call): complete for the finding's memo, never stored.
_ANSWERED_DELIVERIES = frozenset({"cache_hit", "finding_replay"})
_PROVIDER_OF_DELIVERY = {"sent_to_openai": "openai", "sent_to_anthropic": "anthropic"}
SHARED_STAGE = "external_research.shared"
DEFAULT_MAX_AGE_DAYS = 90.0
_STAMP_FORMAT = "%Y%m%dT%H%M%S"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def request_identity(**fields) -> dict:
    """The request a corpus entry answers, exactly IDENTITY_FIELDS.

    The round trip through JSON turns tuples into lists, so an identity
    computed at run time equals the one read back from disk.
    """
    missing = [name for name in IDENTITY_FIELDS if name not in fields]
    extra = sorted(set(fields) - set(IDENTITY_FIELDS))
    if missing or extra:
        raise TypeError(f"request_identity: missing {missing}, unexpected {extra}")
    return json.loads(canonical(fields))


def entry_key(identity: dict) -> str:
    return _sha256(canonical({"format_version": FORMAT_VERSION, "request": identity}))


def pass_completed(metadata: dict) -> bool:
    """Whether a call's metadata records an answer that ended on its own.

    An OpenAI response "completed" and not stopped as degenerate; an
    Anthropic answer whose stop reason is end_turn or stop_sequence; or an
    answer already delivered as one (cache hit, the finding's replay), which
    counts as complete for the finding's memo and is never stored.
    """
    metadata = metadata or {}
    delivery = str(metadata.get("delivery", "") or "")
    if delivery == "sent_to_openai":
        return (metadata.get("response_status") == "completed"
                and not metadata.get("degenerate_output_stopped"))
    if delivery == "sent_to_anthropic":
        return str(metadata.get("stop_reason", "") or "") in ANTHROPIC_COMPLETE_STOP_REASONS
    return delivery in _ANSWERED_DELIVERIES


def _sources(response: str) -> Optional[list]:
    try:
        parsed = json.loads(response)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("sources"), list):
        return None
    return parsed["sources"]


def storable(response: str) -> bool:
    """A JSON object whose ``sources`` has an object with a non-empty url (form, not content)."""
    sources = _sources(response) or []
    return any(isinstance(source, dict) and str(source.get("url", "") or "").strip()
               for source in sources)


def recorded_response_text(record: dict) -> str:
    """The text of a *_response.json record, read as the provider returned it."""
    data = (record or {}).get("response")
    if not isinstance(data, dict):
        return ""
    if data.get("output_text"):
        return str(data["output_text"])
    texts = [content.get("text", "")
             for item in data.get("output", []) or [] if isinstance(item, dict)
             for content in item.get("content", []) or [] if isinstance(content, dict)
             if content.get("type") == "output_text"]
    if texts:
        return "\n".join(texts)
    parts = [block.get("text", "") for block in data.get("content", []) or []
             if isinstance(block, dict) and block.get("type") == "text"]
    return "".join(parts).strip()


def recorded_pass_identity(research_request: dict, synthesis_request: dict,
                           research_metadata: dict) -> Optional[Tuple[dict, str]]:
    """(identity, research text) of a recorded two-pass request, or None.

    The research text is the part of the synthesis input between its prefix
    and suffix: what the synthesis read.
    """
    try:
        provider = _PROVIDER_OF_DELIVERY.get(str((research_metadata or {}).get("delivery", "")))
        if provider is None:
            return None
        synthesis_instructions = str(synthesis_request["instructions"])
        if research_request["instructions"] != synthesis_instructions + RESEARCH_PASS_INSTRUCTIONS:
            return None
        prompt = str(research_request["input"])
        prefix = prompt + SYNTHESIS_RESULT_HEADER
        synthesis_input = synthesis_request["input"]
        if not isinstance(synthesis_input, str):
            return None
        if (not synthesis_input.startswith(prefix)
                or not synthesis_input.endswith(SYNTHESIS_PASS_INSTRUCTIONS)
                or len(synthesis_input) < len(prefix) + len(SYNTHESIS_PASS_INSTRUCTIONS)):
            return None
        for name in ("endpoint", "model", "reasoning_effort", "max_output_tokens"):
            if research_request.get(name) != synthesis_request.get(name):
                return None
        tool = research_request.get("web_search_tool") or TOOL_BEFORE_TRACE_FIELD[provider]
        identity = request_identity(
            provider=provider, endpoint=research_request["endpoint"],
            model=research_request["model"],
            reasoning_effort=research_request["reasoning_effort"],
            max_output_tokens=research_request["max_output_tokens"],
            system_prompt=synthesis_instructions, prompt=prompt,
            json_schema=synthesis_request.get("json_schema"),
            schema_name=synthesis_request.get("schema_name"),
            research_instructions=RESEARCH_PASS_INSTRUCTIONS,
            synthesis_header=SYNTHESIS_RESULT_HEADER,
            synthesis_instructions=SYNTHESIS_PASS_INSTRUCTIONS,
            web_search_tool=dict(tool),
        )
    except (KeyError, TypeError, AttributeError):
        return None
    research_text = synthesis_input[len(prefix):len(synthesis_input) - len(SYNTHESIS_PASS_INSTRUCTIONS)]
    return identity, research_text


def _read_json(path: Path) -> Optional[dict]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _structurally_valid(entry: Optional[dict]) -> bool:
    """Format, key and text hash consistent with the entry's own request (age not read)."""
    if not isinstance(entry, dict) or entry.get("format_version") != FORMAT_VERSION:
        return False
    request = entry.get("request")
    response = entry.get("response")
    if not isinstance(request, dict) or not isinstance(response, str) or not response:
        return False
    return (entry.get("key") == entry_key(json.loads(canonical(request)))
            and _sha256(response) == entry.get("response_sha256"))


def _entry_files(directory: Path) -> List[Path]:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())


def _age_days(stamp: str) -> Optional[float]:
    try:
        created = datetime.fromisoformat(str(stamp or "").strip())
    except ValueError:
        return None
    if created.tzinfo is not None:
        now = datetime.now().astimezone()
    else:
        now = datetime.now()
    return (now - created).total_seconds() / 86400.0


def corpus_digest(directory: Path) -> dict:
    """{"path", "entries", "sha256"} over the valid entries' "<key>:<response_sha256>" lines."""
    lines = []
    for path in _entry_files(directory):
        entry = _read_json(path)
        if _structurally_valid(entry) and path.stem == entry["key"]:
            lines.append(f"{entry['key']}:{entry['response_sha256']}\n")
    lines.sort()
    if not lines:
        return {"path": str(directory), "entries": 0, "sha256": ""}
    return {"path": str(directory), "entries": len(lines),
            "sha256": _sha256("".join(lines))}


def corpus_entry_digests(directory: Path) -> Dict[str, str]:
    """key -> response_sha256 of the valid entries (what a live-research manifest checks)."""
    found = {}
    for path in _entry_files(directory):
        entry = _read_json(path)
        if _structurally_valid(entry) and path.stem == entry["key"]:
            found[entry["key"]] = entry["response_sha256"]
    return dict(sorted(found.items()))


class ResearchCorpus:
    """Shared research answers by request, in ``directory/<key>.json``."""

    def __init__(self, directory: Path, max_age_days: float = DEFAULT_MAX_AGE_DAYS,
                 logger=None):
        self.directory = Path(directory)
        self.max_age_days = float(max_age_days or 0)
        self.logger = logger or logging.getLogger("fix_analysis")
        self._lock = threading.Lock()
        self._stats = {"hits": 0, "misses": 0, "expired": 0, "stored": 0, "kept": 0,
                       "refused": 0}

    def _count(self, name: str) -> None:
        with self._lock:
            self._stats[name] += 1

    def path_of(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def _expired(self, entry: dict) -> bool:
        if self.max_age_days <= 0:
            return False
        age = _age_days(entry.get("created_at", ""))
        return age is None or age > self.max_age_days

    def valid(self, entry: Optional[dict], identity: dict) -> bool:
        """The single predicate get and put use: same request, same text, not too old."""
        return self._valid_except_age(entry, identity) and not self._expired(entry)

    @staticmethod
    def _valid_except_age(entry: Optional[dict], identity: dict) -> bool:
        if not isinstance(entry, dict) or entry.get("format_version") != FORMAT_VERSION:
            return False
        if entry.get("key") != entry_key(identity):
            return False
        if canonical(entry.get("request")) != canonical(identity):
            return False
        response = entry.get("response")
        if not isinstance(response, str) or not response:
            return False
        return _sha256(response) == entry.get("response_sha256")

    def get(self, identity: dict) -> Optional[dict]:
        key = entry_key(identity)
        entry = _read_json(self.path_of(key))
        if self.valid(entry, identity):
            self._count("hits")
            self.logger.info("Research corpus hit: %s", key[:12])
            return entry
        self._count("misses")
        if self._valid_except_age(entry, identity):
            self._count("expired")
            self.logger.info("Research corpus entry %s is older than %s days; researching",
                             key[:12], f"{self.max_age_days:g}")
        return None

    def put(self, identity: dict, scope: Any, response: str, research_text: str,
            provenance: Optional[dict], origin: str = "run",
            created_at: Optional[str] = None, replace: bool = False) -> str:
        """"stored", "kept" (a valid entry stays) or "refused" (no source)."""
        if not storable(response):
            self._count("refused")
            return "refused"
        key = entry_key(identity)
        path = self.path_of(key)
        with self._lock:
            existing = _read_json(path)
            if self.valid(existing, identity) and not replace:
                self._stats["kept"] += 1
                return "kept"
            entry = {
                "format_version": FORMAT_VERSION, "key": key,
                "created_at": created_at or datetime.now().isoformat(timespec="seconds"),
                "origin": origin, "scope": scope, "request": identity,
                "response": response, "response_sha256": _sha256(response),
                "research_text": research_text, "provenance": dict(provenance or {}),
            }
            temporary = self.directory / f".{key}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(entry, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                if existing is None and not path.exists():
                    # A new entry is created exclusively: another process that
                    # stored the same key first keeps its entry.
                    try:
                        os.link(temporary, path)
                        temporary.unlink()
                    except FileExistsError:
                        if self.valid(_read_json(path), identity) and not replace:
                            temporary.unlink()
                            self._stats["kept"] += 1
                            return "kept"
                        self._supersede(path, _read_json(path) or {})
                        os.replace(temporary, path)
                    except OSError:
                        # A file system without hard links: the plain rename.
                        os.replace(temporary, path)
                else:
                    if path.exists():
                        self._supersede(path, existing or {})
                    os.replace(temporary, path)
            except OSError as exc:
                self.logger.warning("Could not write research corpus entry %s: %s", path, exc)
                try:
                    temporary.unlink()
                except OSError:
                    pass
                return "refused"
            self._stats["stored"] += 1
        self.logger.info("Research corpus stored: %s", key[:12])
        return "stored"

    def _supersede(self, path: Path, existing: dict) -> None:
        try:
            stamp = datetime.fromisoformat(str(existing.get("created_at", ""))).strftime(_STAMP_FORMAT)
        except ValueError:
            stamp = "unknown"
        target = self.directory / SUPERSEDED_DIRECTORY / f"{path.stem}_{stamp}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(path, target)

    def stats(self) -> dict:
        with self._lock:
            return dict(self._stats)


def _shared_scope(prompt: str) -> Any:
    """The question_scope a recorded shared research prompt carries, or {}."""
    start = prompt.find("\n", prompt.find("<research_input"))
    end = prompt.rfind("</research_input>")
    if start < 0 or end <= start:
        return {}
    try:
        sections = json.loads(prompt[start:end].strip())
    except ValueError:
        return {}
    return sections.get("question_scope", {}) if isinstance(sections, dict) else {}


def _log_dirs(path: Path) -> List[Path]:
    path = Path(path)
    if not path.exists():
        raise ValueError(f"no such run or log folder: {path}")
    if any(path.glob("*_metadata.json")):
        return [path]
    logs = cost.run_log_dir(path)
    return [logs] if logs.is_dir() else []


def _provenance(log_dir: Path, research: dict, synthesis: dict,
                prices: Optional[dict] = None) -> dict:
    """Where an entry's answer came from, and what its two calls cost (None without a price)."""
    total: Optional[float] = 0.0
    for record in (research, synthesis):
        path = Path(log_dir) / f"{record.get('call_id', '')}_metadata.json"
        call = cost.trace_call_cost(path, record, prices)
        total = None if call is None or total is None else total + call
    return {
        "research_call_id": research.get("call_id", ""),
        "synthesis_call_id": synthesis.get("call_id", ""),
        "source": str(log_dir),
        "response_model": str(research.get("response_model", "") or ""),
        "web_search_calls": int(research.get("web_search_calls") or 0),
        "cost_usd": None if total is None else round(total, 6),
    }


def _recorded_passes(log_dir: Path) -> List[Tuple[dict, Optional[dict]]]:
    """(research metadata, synthesis metadata or None) of each two-pass request in a log folder."""
    groups: Dict[str, Dict[str, dict]] = {}
    for path in sorted(Path(log_dir).glob("*_metadata.json")):
        record = _read_json(path)
        parent = str((record or {}).get("parent_call_id", "") or "")
        if not parent:
            continue
        stage = str(record.get("stage", ""))
        for suffix in ("research", "synthesis"):
            if stage.endswith("." + suffix):
                groups.setdefault(parent, {})[suffix] = record
    return [(calls["research"], calls.get("synthesis"))
            for _parent, calls in sorted(groups.items()) if "research" in calls]


def _importable_pass(log_dir: Path, research: dict, synthesis: Optional[dict],
                     prices: dict) -> Tuple[str, Optional[dict]]:
    """("", candidate entry) for a pass the corpus may hold, else (reason, None)."""
    log_dir = Path(log_dir)
    research_request = _read_json(log_dir / f"{research.get('call_id', '')}_request.json")
    if research_request is None:
        return "missing_record", None
    if (research_request.get("context") or {}).get("stage") != SHARED_STAGE:
        return "not_shared", None
    if (research.get("status") != "success"
            or research.get("delivery") not in cost.PAID_DELIVERIES):
        return "not_paid", None
    if synthesis is None or synthesis.get("status") != "success":
        return "failed", None
    if not (pass_completed(research) and pass_completed(synthesis)):
        return "not_completed", None
    synthesis_id = str(synthesis.get("call_id", ""))
    synthesis_request = _read_json(log_dir / f"{synthesis_id}_request.json")
    synthesis_response = _read_json(log_dir / f"{synthesis_id}_response.json")
    if synthesis_request is None or synthesis_response is None:
        return "missing_record", None
    identified = recorded_pass_identity(research_request, synthesis_request, research)
    if identified is None:
        return "identity_mismatch", None
    identity, research_text = identified
    response = recorded_response_text(synthesis_response)
    if not storable(response):
        return "not_storable", None
    return "", {
        "key": entry_key(identity), "identity": identity, "research_text": research_text,
        "response": response, "completed_at": str(synthesis.get("completed_at", "") or ""),
        "scope": _shared_scope(str(research_request.get("input", ""))),
        "provenance": _provenance(log_dir, research, synthesis, prices),
    }


def import_runs(directory: Path, paths: Iterable[Path], replace: bool = False,
                dry_run: bool = False, max_age_days: float = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Entries from the complete shared passes recorded runs paid for.

    One sample per key: the pass with the latest synthesis completed_at,
    over every path given (the same call read twice counts once).
    """
    prices = cost.load_prices()
    report = {"runs": 0, "passes_seen": 0, "passes_importable": 0, "keys": 0,
              "stored": 0, "kept": 0, "skipped": {}}
    candidates: Dict[str, dict] = {}
    seen_calls: set = set()

    def skip(reason: str) -> None:
        report["skipped"][reason] = report["skipped"].get(reason, 0) + 1

    for given in paths:
        for log_dir in _log_dirs(Path(given)):
            report["runs"] += 1
            for research, synthesis in _recorded_passes(log_dir):
                call_id = str(research.get("call_id", ""))
                if call_id in seen_calls:
                    continue
                seen_calls.add(call_id)
                report["passes_seen"] += 1
                reason, candidate = _importable_pass(log_dir, research, synthesis, prices)
                if reason:
                    skip(reason)
                    continue
                report["passes_importable"] += 1
                held = candidates.get(candidate["key"])
                if held is None or held["completed_at"] < candidate["completed_at"]:
                    candidates[candidate["key"]] = candidate
    report["keys"] = len(candidates)
    corpus = ResearchCorpus(directory, max_age_days)
    for key, item in sorted(candidates.items()):
        if dry_run:
            existing = _read_json(corpus.path_of(key))
            report["kept" if corpus.valid(existing, item["identity"]) and not replace
                   else "stored"] += 1
            continue
        result = corpus.put(item["identity"], item["scope"], item["response"],
                            item["research_text"], item["provenance"], origin="import",
                            created_at=item["completed_at"] or None, replace=replace)
        if result in ("stored", "kept"):
            report[result] += 1
        else:
            skip("not_storable")
    return report


def listing(directory: Path) -> List[dict]:
    """key, created_at, age_days, origin, scope, sources (count) and cost_usd of each entry."""
    rows = []
    for path in _entry_files(directory):
        entry = _read_json(path)
        if not _structurally_valid(entry):
            continue
        age = _age_days(entry.get("created_at", ""))
        rows.append({
            "key": entry["key"],
            "created_at": entry.get("created_at", ""),
            "age_days": None if age is None else round(age, 2),
            "origin": entry.get("origin", ""),
            "scope": entry.get("scope", {}),
            "sources": len(_sources(entry["response"]) or []),
            "cost_usd": (entry.get("provenance") or {}).get("cost_usd"),
        })
    return rows
