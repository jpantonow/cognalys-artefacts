"""What earlier runs verified, kept for the runs that follow.

A run of this pipeline ends with far more than its PRs and issues: it knows
which import a passing build of a repository accepted, and how every finding
it touched ended. None of that reached the next run. Gallery's AsyncTask
migration guessed `org.fossify.commons.extensions.ensureBackgroundThread`
twice, in two runs, after a build had already confirmed
`org.fossify.commons.helpers.ensureBackgroundThread`; and each rerun of the
same finding started without knowing that the last one had stopped on an
empty draft or an unplannable test.

Only facts a deterministic signal confirmed are stored here, each with where
it came from: an import is recorded because a build that contained it
passed, never because a model proposed it. Nothing in this directory changes
code or prompts; consumers read it as data, inside the untrusted sections of
their payloads.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from cognalys.workspace import data_path


DEFAULT_LEARNING_DIR = data_path("pipeline_learning")
LEDGER_FILE = "failure_ledger.json"
KNOWLEDGE_FILE = "verified_knowledge.json"
MAX_HISTORY_FOR_PROMPTS = 3
# Where an attempt ended when it produced a draft that built (or was
# published): its lesson is the draft itself, not a failure.
VALIDATED_STAGES = frozenset({"validated", "published"})
# Where an attempt ended when it went on to a patch. Only these teach a plan
# or a draft anything: an attempt that stopped at the adjudication or at the
# plan is a decision on the finding taken on that run's inputs, which the
# current run takes again, and one that crashed, was refused by the provider
# or could not be analysed says nothing about a patch. CometOTP run_v6..v8
# (2026-11-10): three dismissals written under an older label meaning reached
# the planner and the drafts, which repeated them against the adjudication
# that had just confirmed the finding; Melox run_v4's planner was told
# "OpenAI refused this request's content (server_is_overloaded)".
PATCH_STAGES = frozenset({
    "generation", "generated", "apply", "build", "repair", "validated",
    "published", "revision",
})
# The rule finding_history applies, recorded in run_inputs.json and in an
# experiment's manifest so runs read under different rules can be told apart.
# 2: only attempts that went on to a patch; draft-check refusals retired by
#    DRAFT_CHECK_CHANGES are left out.
HISTORY_RULES_VERSION = 2
# Bumped when a deterministic draft check (llm.semantic_validation,
# llm.contract_gates) changes what it refuses. DRAFT_CHECK_CHANGES names, per
# check code, the version at which it last changed and the detail pattern of
# the refusals that change can affect: a refusal recorded under an older
# version whose detail matches is no longer told to planners and drafts.
# 2: changed_stored_derivations no longer refuses a constructor whose inputs
#    are an algorithm name and freshly generated random material (CometOTP
#    run_v6, 2026-11-10). Only refusals naming a constructor can have changed;
#    those naming a factory (getInstance) or an algorithm literal stand.
# 3: changed_stored_derivations no longer refuses an algorithm literal given
#    only to a random generator factory (KeyGenerator, KeyPairGenerator,
#    SecureRandom; ShortMessageSecure's generateMacSecret, frozen run
#    2026-11-13). Only refusals naming a literal such a factory takes (a MAC,
#    cipher or key-pair algorithm, no transformation) can have changed; a
#    digest or password-based name stands. A refusal does not record where
#    its literal went, so one of those names given to a Mac is retired too.
# A code's changes are a list when it changed more than once; each retires
# only the refusals recorded before its own version.
DRAFT_CHECK_RULES_VERSION = 3
DRAFT_CHECK_CHANGES: dict[str, dict | list[dict]] = {
    "STORED_DERIVED_VALUE_CHANGED": [
        {
            "changed_at": 2,
            "detail": r"the patch changes [A-Za-z_$][\w$]*\(",
        },
        {
            "changed_at": 3,
            "detail": (r'the patch changes the algorithm literal '
                       r'"(?i:Hmac[\w-]+|AES|DESede|DES|Blowfish|RC4|RSA|EC)"'),
        },
    ],
}

_CACHE: dict[str, tuple[float, dict]] = {}


def learning_dir(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit)
    environment = os.environ.get("TCC_LEARNING_DIR", "").strip()
    return Path(environment) if environment else DEFAULT_LEARNING_DIR


def read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def finding_key_for(record: dict) -> str:
    """The same 12-character identity worktree_manager gives a finding."""
    identity = str(record.get("unique_error_id", "")) or "|".join(
        str(record.get(key, "")) for key in ("apk_name", "csv_class", "method_name", "spec")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


def load_knowledge(directory: str | Path | None = None) -> dict:
    path = learning_dir(directory) / KNOWLEDGE_FILE
    try:
        modified = path.stat().st_mtime
    except OSError:
        return {}
    cached = _CACHE.get(str(path))
    if cached and cached[0] == modified:
        return cached[1]
    knowledge = read_json(path)
    _CACHE[str(path)] = (modified, knowledge)
    return knowledge


def verified_imports(knowledge: dict, repository: str) -> list[dict]:
    """Import lines a passing build of `repository` contained, per symbol."""
    entries = (
        ((knowledge or {}).get("repositories") or {}).get(str(repository)) or {}
    ).get("verified_imports") or {}
    return [
        {
            "symbol": symbol,
            "import": entry.get("import", ""),
            "verified_in_run": entry.get("run", ""),
        }
        for symbol, entry in sorted(entries.items())
        if isinstance(entry, dict) and entry.get("import")
    ]


def _without_stale_rename_signals(attempt: dict) -> dict | None:
    """`attempt` without the rename failures recorded under older rename rules.

    NotallyX's rerun was told "adjudication:rename_safety_authorization_missing"
    from the run before, a rule that no longer exists (renames are a
    repository check, never an authorization). Signals about renames from an
    attempt stamped before RENAME_RULES_VERSION are dropped; an attempt left
    with nothing to say is dropped whole.
    """
    from cognalys.extraction.rename_safety import RENAME_RULES_VERSION

    if int(attempt.get("rename_rules_version") or 0) >= RENAME_RULES_VERSION:
        return attempt

    def stale(text: str) -> bool:
        return "rename" in str(text).lower()

    signatures = [item for item in attempt.get("signatures") or [] if not stale(item)]
    details = [
        item for item in attempt.get("details") or []
        if not (isinstance(item, dict) and (stale(item.get("signature", ""))
                                            or stale(item.get("detail", ""))))
    ]
    if (attempt.get("signatures") or attempt.get("details")) and not signatures and not details:
        return None
    return {**attempt, "signatures": signatures, "details": details}


def _without_stale_check_signals(entry: dict, recorded_version: int) -> dict | None:
    """A history entry without draft-check refusals a later check change retired.

    ``recorded_version`` is the attempt's draft_check_rules_version (1 when the
    attempt predates the stamp). A detail is retired when its check changed
    after that version and the detail matches the change's pattern; the
    signature goes too once no detail of that check remains. An entry left
    with nothing to say and no validated draft is dropped whole.
    """
    details = list(entry.get("details") or [])
    signatures = list(entry.get("failure_signatures") or [])
    for code, changes in DRAFT_CHECK_CHANGES.items():
        patterns = [re.compile(change["detail"])
                    for change in (changes if isinstance(changes, list) else [changes])
                    if recorded_version < int(change["changed_at"])]
        if not patterns:
            continue
        signature = f"generation:semantic:{code.lower()}"
        details = [item for item in details
                   if not (isinstance(item, dict) and item.get("signature") == signature
                           and any(pattern.search(str(item.get("detail", "")))
                                   for pattern in patterns))]
        if not any(isinstance(item, dict) and item.get("signature") == signature
                   for item in details):
            signatures = [item for item in signatures if item != signature]
    if ((entry.get("failure_signatures") or entry.get("details"))
            and not signatures and not details and not entry.get("validated_draft")):
        return None
    return {**entry, "failure_signatures": signatures, "details": details}


def seeded_store(source: str | Path, target: str | Path) -> Path:
    """A learning store at ``target`` holding only ``source``'s verified imports.

    For executions compared with each other (replicates, benchmark arms, a
    frozen run): each reads no earlier attempt of its findings, and all read
    the same repository facts (import lines a passing build contained). An
    existing store at ``target`` is left as it is, so a resumed execution
    keeps what it recorded.
    """
    path = Path(target) / KNOWLEDGE_FILE
    if not path.exists():
        repositories = read_json(Path(source) / KNOWLEDGE_FILE).get("repositories") or {}
        write_json_atomic(path, {"repositories": repositories, "findings": {}})
    return path.parent


def finding_history(knowledge: dict, key: str,
                    limit: int = MAX_HISTORY_FOR_PROMPTS) -> list[dict]:
    """How the most recent earlier runs of the same finding ended.

    The latest ``limit`` failures and the latest ``limit`` validated drafts,
    in run order. BlueGate (2026-10-31): the window was the last three
    attempts of any kind, and the content-free "validated" entries of
    run_v7 and run_v8 pushed out the build failures of run_v5/v6 ("actual
    type is 'KeyStore.PrivateKeyEntry', but 'KeyPair' was expected", at
    AdminDashboardActivity and MainActivity). run_v7's planner saw three
    such lessons and planned Authenticator.kt; run_v9's saw one and did not.
    A validated entry carries its draft's title and files, so it says what
    worked; a failure keeps its details (a build error with file:line).

    Only attempts that went on to a patch (PATCH_STAGES) are returned; the
    window is taken over all attempts first, so a later stop at the
    adjudication can leave fewer than ``limit`` patch lessons (the window
    measured on 2026-11-10; taking it after the filter brought back 19
    lessons of the October pipeline).
    """
    attempts = (((knowledge or {}).get("findings") or {}).get(str(key)) or {}).get("attempts") or []
    eligible = [
        current for current in (
            _without_stale_rename_signals(attempt) for attempt in attempts
            if isinstance(attempt, dict))
        if current is not None
    ]
    validated = [index for index, attempt in enumerate(eligible)
                 if str(attempt.get("terminal_stage", "")) in VALIDATED_STAGES]
    failed = [index for index in range(len(eligible)) if index not in set(validated)]
    kept = sorted({*validated[-limit:], *failed[-limit:]}) if limit > 0 else []

    def entry(attempt: dict) -> dict:
        record = {
            "run": attempt.get("run", ""),
            "ended_at": attempt.get("terminal_stage", ""),
            "failure_signatures": attempt.get("signatures", []),
            "details": attempt.get("details", []),
        }
        draft = {name: attempt[key] for name, key in (("title", "draft_title"),
                                                       ("files", "draft_files"))
                 if attempt.get(key)}
        if draft:
            record["validated_draft"] = draft
        return record

    history = []
    for index in kept:
        record = entry(eligible[index])
        if str(record.get("ended_at", "")) not in PATCH_STAGES:
            continue
        record = _without_stale_check_signals(
            record, int(eligible[index].get("draft_check_rules_version") or 1))
        if record is not None:
            history.append(record)
    return history
