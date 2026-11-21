#!/usr/bin/env python3
"""Blind cards of the 2026-11-13 comparison (ESPEC_COMPARACAO §3.6, §3.7), with normalisation and lint.

One card per finished run: arms S, O, T (runs/<unit>/<arm>_r<k>/result.json), C
(cognalys_arm/runs.json -> run_dir, once its run_status.json says "complete") and D (Cognalys with
claude-sonnet-5-5, cognalys_d_arm/runs.json, 2 replicas; carded exactly as C). Runs still
being produced are skipped; rerun this script whenever more runs exist (card ids are stable).

Card (what a judge sees; cards/cards.json): card_id, unit, deliverable (PR / ISSUE / NO_ACTION /
FAILURE), title, text (PR or issue body; for NO_ACTION and FAILURE the recorded reason), diff
(unified, 3 lines of context, files in alphabetical order), and for a PR author_build (whether its
author built that exact diff, and the result: what R4 needs to check a building claim; for C the
pipeline's validation build). Nothing else: the arm, replica,
build status and raw outcome are only in cards/key.json, which no judge prompt reads.

Agents: PR.md / ISSUE.md (first line '# <title>'), NO_ACTION.md, patch.diff; the class is
result.json's delivery_class (FALHA: invalid_reason).

Cognalys (C): the unit's outcome is its best member's (frozen_pr_20261113/compare.py
unit_outcome over finding_statistics.OUTCOME_ORDER), mapped to the two readings of §3.6/§3.8:
  PR pronto                          all drafts: PR; would publish: PR when the record is
                                     publishable, else FALHA (held for review; card still judged)
  correção não aplicável: ... with a solution
                                     PR in both (the base tip already holds the change, e.g. our
                                     merged PR; the agents work at the pinned commit; as analyze.py)
  issue para o mantenedor / a biblioteca, or not applicable without a solution but with the
  issue it would have raised        ISSUE
  falso positivo, sem impacto, confirmada sem correção segura, contrato externo, evidência
  insuficiente, depende de decisão do mantenedor
                                     NENHUMA AÇÃO
  correção gerada sem PR, correção não gerada, reprovada antes de gerar, PR já aberto,
  não analisado, anything else       FALHA
C texts: PR = the pull-request record's title and body; ISSUE = the issue preview
(previews/issues/*.md), else the text issue_creator builds from the candidate
(error_solutions/issue_candidates/); NO_ACTION = the outcome phrase (in English, so the language
does not give the arm away) with its detail and the adjudication summary (cause, correct-use
reason, violation and impact).
C diff (§3.6, at the analysed commit, not the ported one): the PR record's applied
coordinated_edits re-applied to a checkout of the pinned commit, else the solution's
coordinated_edits, then `git diff`; when neither applies there, the PR record's diff (ported to
the base tip) and key.json says so (diff_source).

Normalisation (all arms, text fields only; diffs are the code): signatures and footers
("Generated with", Co-Authored-By, robot lines), a greeting alone on its line ("Hi!"),
AI-disclosure sentences, the author's self-introduction (student / university / research
project), Cognalys evidence ids (SRC-001, WEB-002, ... and detector codes
MESSAGEDIGEST-ALG-02), claim references (C1:, "(C2, C3)"),
decision names (NO_SAFE_FIX, FIX_SUPPORTED, ...), deliverable file names (PR.md, ...) and
run-folder names (S_r1), and three pipeline words rewritten (adjudication -> assessment,
behaviour review -> review, evidence packet -> evidence). Every change is counted in lint.json.

Lint (§3.7): fails (exit 1) when a card still contains Cognalys, Claude, Codex, Sonnet, GPT,
Terra, Sol, "RVSec rule", adjudicat, "behaviour review", "evidence packet", run_, C_r or agent_.
A hit inside an identifier that already exists in the project's source at the pinned commit
(git grep) is the project's own text and is allowed (listed in lint.json). Only added diff
lines are linted (removed and context lines are the project's code).

Writes cards/cards.json, cards/key.json, cards/lint.json, cards/seed.json (created once: the
salt for card ids and per-judge order; keep it).
Usage (WSL, venv): python outputs/comparison_20261113/judges/cards.py [--c-incomplete-as-failure]
"""
from __future__ import annotations

import argparse
import collections
import glob
import hashlib
import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
TCC = OUT.parent.parent
sys.path.insert(0, str(OUT))
sys.path.insert(0, str(TCC / "src"))
sys.path.insert(0, str(TCC / "outputs/frozen_pr_20261113"))

from common import RECORDS, REPLICAS, SEED, WORK, base_dir, dump, load  # noqa: E402

# ARMS_TAG=<tag>: cards of a rerun of arms C and D (cognalys_arm_<tag>, cognalys_d_arm_<tag>) in
# cards_<tag>/, with the seed (salt) of cards/ so card ids and orders stay those of the first cards.
ARMS_TAG = os.environ.get("ARMS_TAG", "")
CARDS_DIR = HERE / (f"cards_{ARMS_TAG}" if ARMS_TAG else "cards")
if ARMS_TAG and not (CARDS_DIR / "seed.json").is_file():
    CARDS_DIR.mkdir(exist_ok=True)
    shutil.copy2(HERE / "cards" / "seed.json", CARDS_DIR / "seed.json")
CARDS = CARDS_DIR / "cards.json"
KEY = CARDS_DIR / "key.json"
LINT = CARDS_DIR / "lint.json"
SEED_FILE = CARDS_DIR / "seed.json"
JWORK = WORK / "judges"                      # WSL disk: checkouts for C diffs and for the judges
# Arms TP (T+: Codex + gpt-6.1-terra) and SP (S+: Claude Code + Sonnet 5.5), the full-process prompt,
# exist only in the rerun cards (ARMS_TAG); the blindness probes keep the five original arms.
PLUS_ARMS = ("TP", "SP2") if ARMS_TAG else ()  # SP2 replaces SP (DESVIO_SP_20261115.md)
ARMS = ("S", "O", "T") + PLUS_ARMS + ("C", "D")
BLIND_ARMS = ("S", "O", "T", "C", "D")
# Cognalys arms: letter -> folder with runs.json. D (Cognalys + Sonnet 5.5) ran 2 replicas.
COGNALYS_ARMS = {a: f + (f"_{ARMS_TAG}" if ARMS_TAG else "") for a, f in (("C", "cognalys_arm"), ("D", "cognalys_d_arm"))}
ARM_REPLICAS = {**{a: REPLICAS for a in ARMS}, "D": 2}  # SP2 r3-r4 ran in option A (2026-11-17): 4 replicas, as TP
# The four FALHA runs of C k3 came from pipeline implementation defects (fixed in 3dee64a); each is
# replaced by the same unit and replica rerun at 3dee64a (DESVIO_C_FALHAS_20261116.md). The other 76 stay.
C_REPLACED = ({("pr_fff072cb8953", 1), ("pr_fff072cb8953", 3), ("co_f637c4b8467e", 3), ("co_0a32f516feb2", 2)}
              if ARMS_TAG == "k3" else set())
C_REPLACED_ROOT = OUT.parent.parent / "frozen_runs_20261113" / "c_arm_k3_fixes"
JUDGED = ("PR", "ISSUE", "NO_ACTION")        # deliverables a judge scores; FAILURE is A = 0 mechanically
CLASS_PT = {"PR": "PR", "ISSUE": "ISSUE", "NO_ACTION": "NENHUMA AÇÃO", "FAILURE": "FALHA"}
CLASS_EN = {v: k for k, v in CLASS_PT.items()}


# ------------------------------------------------------------------ ids and order
def seed_info() -> dict:
    """The recorded seed and salt (created once). Card ids and judge orders derive from them."""
    if SEED_FILE.is_file():
        return load(SEED_FILE)
    info = {"seed": SEED, "salt": secrets.token_hex(16), "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "rule": "card_id = 'K' + sha256(salt|unit|arm|replica)[:8]; order of a unit's cards for a judge = "
                    "random.Random(salt|order|judge|unit).shuffle(sorted ids); samples use random.Random(salt|<name>)"}
    dump(SEED_FILE, info)
    return info


def card_id(salt: str, unit: str, arm: str, replica: int) -> str:
    return "K" + hashlib.sha256(f"{salt}|{unit}|{arm}|{replica}".encode()).hexdigest()[:8]


def judge_order(salt: str, judge: str, unit: str, ids) -> list:
    ids = sorted(ids)
    random.Random(f"{salt}|order|{judge}|{unit}").shuffle(ids)
    return ids


def rng(salt: str, name: str) -> random.Random:
    return random.Random(f"{salt}|{name}")


# ------------------------------------------------------------------ text normalisation
FOOTER_LINE = re.compile(r"^\s*(?:🤖.*|.*Generated with \[?(?:Claude|Codex|Cognalys).*|Co-Authored-By:.*|"
                         r"(?:Signed-off-by|Reviewed-by):.*)$", re.I)
AI_SENTENCE = re.compile(r"\bAI[- ]assist\w*|\bwith (?:the help of |the assistance of )?(?:an )?AI\b|"
                         r"\bAI (?:use|assistance|tool|tools|agent|model|coding)\b|\bAI[- ]generated\b|"
                         r"\b(?:large )?language model\b|\bLLM\b", re.I)
INTRO_SENTENCE = re.compile(r"\bstudent at\b|\bUniversity of Bras|\(UnB\)|\bresearch project\b", re.I)
EVIDENCE_ID = re.compile(r"\b(?:SRC|WEB|RUN|TEST|SPEC|FLOW|DET|CLM|SUP|CTX|BUILD|CALL|CALLEE|CALLER|SEQ|RENAME|"
                         r"BYTE|ADJ|MUT|SIZE|MAINT|DOC|HIST|REPO|LIB|DEP|FIELD|CONST|C)-\d{1,4}\b")
DETECTOR_CODE = re.compile(r"\b[A-Z]{5,}-[A-Z]{2,5}-\d{2,3}\b")
CLAIM_REF = re.compile(r"\bC\d{1,2}(?=\s*[:,;)])\s*:?")
CLAIM_LIST = re.compile(r"\s*\((?:C\d{1,2}(?:,\s*|;\s*)?)+\)")       # "(C2, C3)" of an adjudication claim list
GREETING = re.compile(r"^\s*(?:Hi|Hello|Hey)(?: there)?[!,.]?\s*$", re.I)
EVIDENCE_PHRASE = re.compile(r"\bEvidence IDs?:\s*", re.I)
FILE_NAMES = re.compile(r"`?\b(?:PR|ISSUE|NO_ACTION)\.md\b`?")
RUN_FOLDER = re.compile(r"\b[SOTC]_r\d+\b")
REWORDS = [(re.compile(r"\badjudicat(?:ion|ions)\b", re.I), "assessment"),
           (re.compile(r"\badjudicat(?:ed)\b", re.I), "assessed"),
           (re.compile(r"\badjudicat\w*", re.I), "assess"),
           (re.compile(r"\bbehaviou?r review\b", re.I), "review"),
           (re.compile(r"\bevidence packet\b", re.I), "evidence")]
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\[(\"'])")


def _decision_names() -> set:
    names = {"NO_SAFE_FIX", "FIX_SUPPORTED", "CONFIRMED_FINDING_NO_SAFE_FIX", "CONFIRMED_CAUSE_ONLY_NO_SAFE_FIX",
             "UNCONFIRMED_FINDING", "MULTI_FILE_FIX_PROPOSED_FOR_REVIEW", "SAFE_INTERNAL_DIGEST_MIGRATION",
             "NOT_APPLICABLE_TO_BASE", "ALREADY_FIXED_UPSTREAM", "EXTERNAL_DIGEST_CONTRACT_NO_CLIENT_ONLY_FIX"}
    try:
        from cognalys.evaluation.decisions import KNOWN_DECISIONS
        names |= set(KNOWN_DECISIONS)
    except Exception:  # noqa: BLE001 - the fixed list still applies
        pass
    return names


DECISIONS = _decision_names()
DECISION_RE = re.compile(r"`?\b(?:" + "|".join(sorted(map(re.escape, DECISIONS), key=len, reverse=True)) + r")\b`?")


def normalise_text(text: str, counts: collections.Counter) -> str:
    """The blinding normalisation of §3.7 on a title, body or reason. Counts what it removed."""
    if not text:
        return ""
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if FOOTER_LINE.match(line):
            counts["footer_line"] += 1
            continue
        if GREETING.match(line):
            counts["greeting_line"] += 1
            continue
        if AI_SENTENCE.search(line) or INTRO_SENTENCE.search(line):
            prefix = re.match(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+|>\s*)?", line).group(0)
            kept = []
            for sentence in SENTENCE_SPLIT.split(line[len(prefix):]):
                if AI_SENTENCE.search(sentence):
                    counts["ai_disclosure_sentence"] += 1
                elif INTRO_SENTENCE.search(sentence):
                    counts["author_intro_sentence"] += 1
                else:
                    kept.append(sentence)
            if not "".join(kept).strip() or GREETING.match(" ".join(kept)):
                continue
            line = prefix + " ".join(kept)
        lines.append(line)
    text = "\n".join(lines)
    for name, pattern, repl in (("evidence_id", EVIDENCE_ID, ""), ("detector_code", DETECTOR_CODE, ""),
                                ("decision_name", DECISION_RE, "the decision"),
                                ("file_name", FILE_NAMES, "this note"), ("run_folder", RUN_FOLDER, "")):
        text, n = pattern.subn(repl, text)
        counts[name] += n
    text, n = CLAIM_LIST.subn("", text)
    counts["claim_ref"] += n
    if counts["evidence_id"] or counts["detector_code"]:
        text, n = CLAIM_REF.subn("", text)
        counts["claim_ref"] += n
        text = EVIDENCE_PHRASE.sub("", text)
    for pattern, repl in REWORDS:
        text, n = pattern.subn(repl, text)
        counts["pipeline_word"] += n
    # Leftovers of removed ids: "( , )", "(; )", "[]", doubled separators, space before punctuation.
    text = re.sub(r"[(\[]\s*[,;:/\s]*[)\]]", "", text)
    text = re.sub(r"\(\s*[,;:]\s*", "(", text)
    text = re.sub(r"\s*[,;]\s*\)", ")", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_title(markdown: str):
    text = (markdown or "").replace("\r\n", "\n").strip("\n")
    first, _, rest = text.partition("\n")
    if first.startswith("# "):
        return first[2:].strip(), rest.strip("\n")
    return "", text


def sort_diff(diff: str) -> str:
    """A unified diff with its file blocks in alphabetical order of path."""
    blocks, current = [], []
    for line in (diff or "").splitlines():
        if line.startswith("diff --git ") and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)

    def path(block):
        m = re.match(r"diff --git a/(\S+) b/(\S+)", block[0])
        return m.group(2) if m else block[0]
    blocks.sort(key=path)
    return "\n".join("\n".join(b) for b in blocks) + ("\n" if blocks else "")


# ------------------------------------------------------------------ units
def units() -> dict:
    return {p.parent.name: load(p) for p in sorted((OUT / "units").glob("*/unit.json"))}


# ------------------------------------------------------------------ agent cards
def agent_card(unit: dict, arm: str, replica: int, rdir: Path, counts):
    result = load(rdir / "result.json")
    klass = result.get("delivery_class", "FALHA")
    deliverable = CLASS_EN.get(klass, "FAILURE")
    title, text, diff = "", "", ""
    if deliverable == "PR":
        title, text = split_title((rdir / "PR.md").read_text(encoding="utf-8", errors="replace"))
        diff = sort_diff((rdir / "patch.diff").read_text(encoding="utf-8", errors="replace"))
    elif deliverable == "ISSUE":
        title, text = split_title((rdir / "ISSUE.md").read_text(encoding="utf-8", errors="replace"))
    elif deliverable == "NO_ACTION":
        title, text = split_title((rdir / "NO_ACTION.md").read_text(encoding="utf-8", errors="replace"))
    else:
        text = f"No valid deliverable: {result.get('invalid_reason') or result.get('status')}."
    plus_failure = False
    if arm in PLUS_ARMS and deliverable == "NO_ACTION":
        # A "+" arm's NO_ACTION.md starts "Reason: <name>" (process block 10.22). "correction not generated"
        # is the pipeline's "correção não gerada", which the C arm counts as FALHA (c_class): same here.
        first = next((line.strip() for line in (rdir / "NO_ACTION.md").read_text(
            encoding="utf-8", errors="replace").splitlines() if line.strip()), "")
        if re.match(r"(?i)^(#\s*)?reason:\s*correction not generated\b", first):
            plus_failure = True
            klass, deliverable = "FALHA", "FAILURE"
            title, text = "", "No valid deliverable: correction not generated."
    build = result.get("build") or {}
    status = build.get("final_status")
    build_state = ("n/a" if deliverable != "PR" else "pass" if status == "success"
                   else "fails_without_patch_too" if build.get("baseline_status") not in (None, "success")
                   else "fail")
    key = {"unit": unit["unit"], "arm": arm, "replica": replica, "source": str(rdir.relative_to(TCC)),
           "class_all": klass, "class_publish": klass, "raw_outcome": result.get("decision", ""),
           "detail": result.get("invalid_reason", ""), "build": build_state,
           "build_detail": {k: build.get(k) for k in ("final_status", "final_from", "baseline_status")},
           "diff_source": "patch.diff" if diff else "", "consulted_upstream": bool(result.get("consulted_upstream")),
           "flags": ["plus_correction_not_generated"] if plus_failure else []}
    # What the judge needs to check a building claim (R4): did the author build this exact diff?
    author_build = ""
    if deliverable == "PR":
        author_build = (("yes, it passed" if status == "success" else "yes, it failed")
                        if str(build.get("final_from", "")).startswith("agent") else "no")
    card = {"deliverable": deliverable, "title": normalise_text(title, counts),
            "text": normalise_text(text, counts), "diff": diff, "author_build": author_build}
    return card, key


# ------------------------------------------------------------------ Cognalys cards
OUTCOME_EN = {
    "falso positivo reconhecido": "Nothing sent: in this code the reported violation does not happen (a false positive).",
    "violação sem impacto de segurança": "Nothing sent: the code does not follow the rule, but without a security impact that a change would remove.",
    "violação confirmada sem correção segura": "Nothing sent: the violation is real, but no safe change could be made.",
    "contrato externo impede a correção": "Nothing sent: a counterpart outside the repository fixes this value, so the app cannot change it alone.",
    "evidência insuficiente": "Nothing sent: the evidence did not settle whether the report is right.",
    "depende de decisão do mantenedor": "Nothing sent: the change depends on a decision only the maintainers can make.",
}
NO_ACTION_OUTCOMES = set(OUTCOME_EN)
IMPACT_EN = {"security_impact": "a security impact", "non_security": "no security impact",
             "not_established": "a security impact that was not established", "unknown": "an unknown security impact"}
VIOLATION_EN = {"confirmed": "confirmed", "not_confirmed": "not confirmed"}


def c_classes(outcome: str, record: dict, has_issue_text: bool):
    """(class_all, class_publish, flags) of a Cognalys outcome (module docstring)."""
    record = record or {}
    if outcome == "PR pronto":
        return "PR", ("PR" if record.get("pr_publishable") else "FALHA"), \
            ([] if record.get("pr_publishable") else ["held_for_review"])
    if outcome.startswith("correção não aplicável"):
        if record.get("solution"):
            return "PR", "PR", ["fix_already_on_base_tip"]
        if has_issue_text:
            return "ISSUE", "ISSUE", ["issue_about_code_gone_from_tip"]
        return "FALHA", "FALHA", ["not_applicable_without_fix"]
    if outcome.startswith("issue para"):
        return "ISSUE", "ISSUE", []
    if outcome in NO_ACTION_OUTCOMES:
        return "NENHUMA AÇÃO", "NENHUMA AÇÃO", (["maintainer_decision_as_no_action"]
                                                if outcome == "depende de decisão do mantenedor" else [])
    return "FALHA", "FALHA", ([] if outcome in {"correção gerada, sem PR", "correção não gerada", "não analisado",
                                                "correção proposta, reprovada antes de gerar"}
                              else [f"unmapped_outcome:{outcome}"])


def _matches(data: dict, unit: dict) -> bool:
    ids = {row["finding_id"] for row in unit["rows"]}
    if data.get("finding_id") in ids:
        return True
    triples = {(row["class"], row["method"], row["spec"]) for row in unit["rows"]}
    return (data.get("csv_class"), data.get("method_name"), data.get("spec")) in triples


def pr_record(run: Path, unit: dict) -> tuple:
    for path in sorted(run.glob("pull_requests/*_pr.json")):
        data = load(path)
        if _matches(data, unit) and (data.get("diff") or data.get("coordinated_edits")):
            return path, data
    return None, {}


def issue_text(run: Path, unit: dict, key: tuple):
    """(title, body, source) of the issue this run would send, or ('', '', '')."""
    apk, csv_class, method, spec = key
    for path in sorted(run.glob(f"previews/issues/{glob.escape(apk)}_{glob.escape(method)}_{glob.escape(spec)}*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        title, body = split_title("\n".join(l for l in text.splitlines() if not l.startswith("<!--")).strip())
        if "\n---\n" in body:
            body = body.split("\n---\n", 1)[1]
        return title, body.strip(), str(path.relative_to(TCC))
    candidates = sorted(run.glob("error_solutions/issue_candidates/*/*_issue.json")) + \
        sorted(run.glob("error_solutions/issue_candidates/*/*_not_applicable_to_base.json"))
    for path in candidates:
        data = load(path)
        if (data.get("csv_class"), data.get("method_name"), data.get("spec")) != (csv_class, method, spec):
            continue
        try:
            from cognalys.issues.issue_creator import IssueCreator
            from cognalys.issues.issue_text import issue_title
            body = IssueCreator.build_no_safe_fix_body("", data, style="v3")
            return issue_title(data), body, str(path.relative_to(TCC))
        except Exception as exc:  # noqa: BLE001
            content = data.get("issue_content") or {}
            body = "\n\n".join(str(content.get(k) or "") for k in
                               ("finding_summary", "why_no_pr", "maintainer_question", "proposed_next_step"))
            return str(content.get("title") or ""), body.strip(), f"{path.relative_to(TCC)} ({exc.__class__.__name__})"
    return "", "", ""


def adjudication_summary(run: Path, key: tuple) -> str:
    apk, csv_class, method, spec = key
    for path in sorted(run.glob(f"error_solutions/evidence_reviews/{glob.escape(apk)}/*_evidence.json")):
        data = load(path)
        if (data.get("csv_class"), data.get("method_name"), data.get("spec")) != (csv_class, method, spec):
            continue
        analysis = data.get("analysis") or {}
        adj = {}
        stem = path.name[:-len("_evidence.json")]
        adj_path = run / "error_solutions/v2_artifacts" / apk / f"{stem}_adjudication.json"
        if adj_path.is_file():
            adj = load(adj_path)
        violation = adj.get("detector_violation") or analysis.get("detector_violation") or ""
        impact = adj.get("runtime_security_impact") or analysis.get("runtime_security_impact") or ""
        parts = [f"Assessment: the reported violation is {VIOLATION_EN.get(violation, 'undetermined')}, with "
                 f"{IMPACT_EN.get(impact, 'an undetermined security impact')}."]
        cause = adj.get("confirmed_cause") or analysis.get("confirmed_cause") or ""
        if cause:
            parts.append(f"Cause: {cause}")
        correct = adj.get("correct_use_reason") or ""
        if correct:
            parts.append(f"Why the current use can stand: {correct}")
        return "\n\n".join(parts)
    return ""


def _sh(*args, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, **kw)


def reconstruction_checkout(unit: dict) -> Path:
    repo = JWORK / "cdiff" / unit["unit"] / "repo"
    if not (repo / ".git").exists():
        shutil.rmtree(repo.parent, ignore_errors=True)
        repo.parent.mkdir(parents=True)
        _sh("git", "clone", "-q", "--shared", "--no-checkout", str(base_dir(unit)), str(repo), check=True)
    _sh("git", "-C", str(repo), "checkout", "-q", "-f", "--detach", unit["pinned_commit"], check=True)
    _sh("git", "-C", str(repo), "reset", "-q", "--hard", unit["pinned_commit"], check=True)
    _sh("git", "-C", str(repo), "clean", "-fdq", check=True)
    return repo


def apply_edits(repo: Path, edits, path_keys) -> str:
    """Splices coordinated edits (original_code -> corrected_code) into the checkout; '' or the error."""
    for edit in edits or []:
        if not isinstance(edit, dict):
            return "edit is not an object"
        rel = next((edit.get(k) for k in path_keys if edit.get(k)), "")
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts:
            return f"bad path {rel!r}"
        target = repo / rel
        kind = str(edit.get("edit_kind") or "method")
        original = str(edit.get("original_code") or "").replace("\r\n", "\n")
        corrected = str(edit.get("corrected_code") or "").replace("\r\n", "\n")
        if kind == "create":
            if target.exists():
                return f"create target exists: {rel}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(corrected, encoding="utf-8")
            continue
        if not target.is_file():
            return f"file not at the pinned commit: {rel}"
        if kind == "delete_file":
            target.unlink()
            continue
        raw = target.read_bytes().decode("utf-8", errors="surrogateescape")
        crlf = "\r\n" in raw
        content = raw.replace("\r\n", "\n")
        count = content.count(original) if original else 0
        if count != 1:
            return f"original_code found {count} times in {rel}"
        content = content.replace(original, "" if kind == "delete" else corrected, 1)
        if crlf:
            content = content.replace("\n", "\r\n")
        target.write_bytes(content.encode("utf-8", errors="surrogateescape"))
    return ""


def c_diff_at_pinned(unit: dict, pr: dict, solution: dict):
    """(diff, diff_source, notes): the C patch at the analysed commit (§3.6), else the ported one."""
    import build_check
    attempts, notes = [], []
    if pr.get("coordinated_edits"):
        attempts.append(("pr_record_edits_at_pinned_commit", pr["coordinated_edits"], ("source_file", "source_file_path")))
    sol_edits = ((solution or {}).get("corrected_implementation") or {}).get("coordinated_edits") or []
    if sol_edits:
        attempts.append(("solution_edits_at_pinned_commit", sol_edits, ("source_file_path", "source_file")))
    if attempts:
        try:
            repo = reconstruction_checkout(unit)
        except (subprocess.CalledProcessError, OSError) as exc:
            notes.append(f"checkout failed: {getattr(exc, 'stderr', '') or exc}"[-300:])
            attempts = []
        for name, edits, keys in attempts:
            error = apply_edits(repo, edits, keys)
            diff = "" if error else build_check.diff_text(repo)
            _sh("git", "-C", str(repo), "reset", "-q", "--hard", unit["pinned_commit"])
            _sh("git", "-C", str(repo), "clean", "-fdq")
            if error:
                notes.append(f"{name}: {error}")
                continue
            if diff.strip():
                return sort_diff(diff), name, notes
            notes.append(f"{name}: empty diff")
    if pr.get("diff"):
        return sort_diff(pr["diff"]), f"pr_record_diff_ported_to_base_commit_{str(pr.get('base_commit', ''))[:12]}", notes
    return "", "none", notes


def c_card(unit: dict, replica: int, run: Path, counts, incomplete_as_failure: bool, arm="C"):
    import compare as cmp
    status = (load(run / "run_status.json") if (run / "run_status.json").is_file() else {}).get("status")
    uids = [row["unique_error_id"] for row in unit["rows"]]
    record, outcome, detail, uid = cmp.unit_outcome(run, {"apk": unit["apk"]}, uids)
    flags = []
    if status != "complete":
        if not incomplete_as_failure:
            return None, f"run_status {status!r}"
        outcome, flags = f"erro (run_status {status})", ["run_not_complete"]
    if outcome in {"não executado", "ausente na run"} and status == "complete":
        flags.append("unit_absent_from_complete_run")
    row = next((r for r in unit["rows"] if r["unique_error_id"] == uid), unit["rows"][0])
    key_tuple = (unit["apk"], row["class"], row["method"], row["spec"])
    i_title, i_body, i_source = issue_text(run, unit, key_tuple)
    class_all, class_publish, more = c_classes(outcome, record, bool(i_body))
    flags += more
    deliverable = CLASS_EN[class_all]
    title, text, diff, diff_source, notes, text_source = "", "", "", "", [], ""
    if deliverable == "PR":
        path, pr = pr_record(run, unit)
        solution = cmp.solution_of(run, set(uids))
        diff, diff_source, notes = c_diff_at_pinned(unit, pr, solution)
        if pr:
            title, text, text_source = pr.get("title", ""), pr.get("body") or pr.get("plain_body", ""), \
                str(path.relative_to(TCC))
        else:
            # A generated fix with no pull-request record (the base tip already holds it): the solution's own
            # explanation, as the pipeline wrote it for the pull request (first line '<!-- pr-title: ... -->').
            expl = str(((solution or {}).get("corrected_implementation") or {}).get("explanation") or "")
            m = re.match(r"\s*<!--\s*pr-title:\s*(.*?)\s*-->\s*", expl)
            title, text = (m.group(1), expl[m.end():]) if m else ("", expl)
            text_source = "solution explanation (no pull-request record)"
            flags.append("text_from_solution")
        if not diff.strip():
            flags.append("pr_without_diff")
    elif deliverable == "ISSUE":
        title, text, text_source = i_title, i_body, i_source
    elif deliverable == "NO_ACTION":
        parts = [OUTCOME_EN[outcome]]
        # final_outcome prefixes a joined finding's detail with Portuguese bookkeeping
        # ("analisado junto com X, sem efeito no patch"): the pipeline's, not the reason.
        reason = "; ".join(p for p in (detail or "").split("; ")
                           if not re.match(r"(analisado|resolvido) junto com|PR atribuído", p))
        if reason:
            parts.append(f"Details: {reason}")
        # A joined unit has one evidence review, under the finding the others were joined into:
        # the best member first, then the unit's other rows.
        summary = next((s for s in (adjudication_summary(run, k) for k in
                                    [key_tuple] + [(unit["apk"], r["class"], r["method"], r["spec"])
                                                   for r in unit["rows"]]) if s), "")
        if summary:
            parts.append(summary)
        text, text_source = "\n\n".join(parts), "outcome phrase + detail + adjudication"
    else:
        text = f"No deliverable: {outcome}" + (f" ({detail})" if detail else "")
    key = {"unit": unit["unit"], "arm": arm, "replica": replica, "source": str(run.relative_to(TCC)),
           "class_all": class_all, "class_publish": class_publish, "raw_outcome": outcome, "detail": detail,
           "best_member": uid, "publishable": bool((record or {}).get("pr_publishable")),
           "build": ("n/a" if deliverable != "PR" else
                     "pass" if outcome == "PR pronto" else "not_built"),
           "build_detail": {"build_status": (record or {}).get("build_status")},
           "diff_source": diff_source, "diff_notes": notes, "text_source": text_source,
           "consulted_upstream": None, "flags": flags}
    # The pipeline validated (built) the patch of a ready pull request; a fix the base tip already
    # held was not built.
    author_build = "" if deliverable != "PR" else ("yes, it passed" if outcome == "PR pronto" else "no")
    card = {"deliverable": deliverable, "title": normalise_text(title, counts),
            "text": normalise_text(text, counts), "diff": diff, "author_build": author_build}
    return (card, key), ""


# ------------------------------------------------------------------ lint
MARKERS = [("Cognalys", re.compile(r"cognalys", re.I)), ("Claude", re.compile(r"claude", re.I)),
           ("Codex", re.compile(r"codex", re.I)), ("Sonnet", re.compile(r"sonnet", re.I)),
           ("GPT", re.compile(r"gpt", re.I)), ("Terra", re.compile(r"\bTerra\b")), ("Sol", re.compile(r"\bSol\b")),
           ("RVSec rule", re.compile(r"RVSec rules?\b", re.I)), ("adjudicat", re.compile(r"adjudicat", re.I)),
           ("behaviour review", re.compile(r"behaviou?r review", re.I)),
           ("evidence packet", re.compile(r"evidence packet", re.I)),
           ("run_", re.compile(r"run_")), ("C_r", re.compile(r"C_r")), ("agent_", re.compile(r"agent_"))]
_project_words: dict = {}


def in_project(unit: dict, word: str) -> bool:
    """Whether `word` occurs in the project's tracked source at the pinned commit (its own text)."""
    cache_key = (unit["unit"], word)
    if cache_key not in _project_words:
        base = base_dir(unit)
        found = False
        if base.is_dir() and word.strip():
            r = _sh("git", "-C", str(base), "grep", "-q", "-I", "-F", "-e", word, unit["pinned_commit"], "--")
            found = r.returncode == 0
        _project_words[cache_key] = found
    return _project_words[cache_key]


def marker_hits(text: str):
    for name, pattern in MARKERS:
        for m in pattern.finditer(text or ""):
            start, end = m.start(), m.end()
            while start > 0 and re.match(r"[\w.$]", text[start - 1]):
                start -= 1
            while end < len(text) and re.match(r"\w", text[end]):
                end += 1
            yield name, text[start:end].strip(".$"), text[max(0, m.start() - 60):m.end() + 60].replace("\n", " ")


# Tokens of a repository's own pull-request template at its tip (later than the pinned commit, so
# in_project misses them). Checked on GitHub 2026-11-16: NotallyX's .github/pull_request_template.md asks
# "the role of AI (e.g., ChatGPT, Claude, GitHub Copilot) in this PR".
PROJECT_TEMPLATE_TOKENS = {("pr_fff072cb8953", "ChatGPT")}


def lint_text(text: str, unit: dict = None):
    """[(marker, token, context, allowed)] for a text; allowed when the token is the project's own."""
    out = []
    for name, token, context in marker_hits(text):
        allowed = bool(unit) and ((unit["unit"], token) in PROJECT_TEMPLATE_TOKENS or in_project(unit, token))
        out.append({"marker": name, "token": token, "context": context, "allowed_project_text": allowed})
    return out


def lint_card(card: dict, unit: dict):
    hits = []
    for field in ("title", "text"):
        for hit in lint_text(card.get(field, ""), unit):
            hits.append({"field": field, **hit})
    added = "\n".join(l[1:] for l in (card.get("diff") or "").splitlines()
                      if l.startswith("+") and not l.startswith("+++"))
    paths = "\n".join(l for l in (card.get("diff") or "").splitlines() if l.startswith("diff --git"))
    for field, text in (("diff_added_lines", added), ("diff_paths", paths)):
        for hit in lint_text(text, unit):
            hits.append({"field": field, **hit})
    return hits


# ------------------------------------------------------------------ main
def build(incomplete_as_failure: bool = False):
    info = seed_info()
    salt = info["salt"]
    all_units = units()
    cards, key, skipped = [], {}, []
    counts_by_arm = {arm: collections.Counter() for arm in ARMS}
    for unit_id, unit in all_units.items():
        for rdir in sorted((RECORDS / unit_id).glob("*_r*")):
            m = re.fullmatch(r"(S|O|T|TP|SP2|SP)_r(\d+)", rdir.name)
            if m and m.group(1) not in ARMS:
                continue
            if not m or not (rdir / "result.json").is_file():
                if m:
                    skipped.append({"unit": unit_id, "run": rdir.name, "why": "no result.json yet"})
                continue
            arm, replica = m.group(1), int(m.group(2))
            card, k = agent_card(unit, arm, replica, rdir, counts_by_arm[arm])
            cid = card_id(salt, unit_id, arm, replica)
            cards.append({"card_id": cid, "unit": unit_id, **card})
            key[cid] = k
    for c_arm, folder in COGNALYS_ARMS.items():
        runs_json = OUT / folder / "runs.json"
        if not runs_json.is_file():
            continue
        for run in load(runs_json)["runs"]:
            run_dir = Path(run["run_dir"])
            if c_arm == "C" and (run["unit"], int(run["replica"])) in C_REPLACED:
                run_dir = C_REPLACED_ROOT / run["unit"] / f"r{int(run['replica'])}"
            unit = all_units.get(run["unit"])
            if unit is None:
                continue
            if not (run_dir / "run_status.json").is_file():
                skipped.append({"unit": run["unit"], "run": f"{c_arm}_r{run['replica']}", "why": "not started"})
                continue
            made, why = c_card(unit, int(run["replica"]), run_dir, counts_by_arm[c_arm], incomplete_as_failure,
                               arm=c_arm)
            if made is None:
                skipped.append({"unit": run["unit"], "run": f"{c_arm}_r{run['replica']}", "why": why})
                continue
            card, k = made
            cid = card_id(salt, run["unit"], c_arm, int(run["replica"]))
            cards.append({"card_id": cid, "unit": run["unit"], **card})
            key[cid] = k
    if len({c["card_id"] for c in cards}) != len(cards):
        raise SystemExit("card id collision; change the salt in cards/seed.json before any judging")
    cards.sort(key=lambda c: (c["unit"], c["card_id"]))
    for card in cards:
        card["judged"] = card["deliverable"] in JUDGED
        card["sha256"] = hashlib.sha256(json.dumps({k: card[k] for k in ("deliverable", "title", "text", "diff",
                                                                         "author_build")},
                                                   sort_keys=True).encode()).hexdigest()[:16]
    # Lint.
    violations, allowed = [], []
    for card in cards:
        for hit in lint_card(card, all_units[card["unit"]]):
            entry = {"card_id": card["card_id"], "unit": card["unit"], "arm": key[card["card_id"]]["arm"], **hit}
            (allowed if hit["allowed_project_text"] else violations).append(entry)
    per_arm = collections.defaultdict(collections.Counter)
    for card in cards:
        per_arm[key[card["card_id"]]["arm"]][card["deliverable"]] += 1
    viol_arm = collections.Counter(v["arm"] for v in violations)
    expected = {(u, a, r) for u in all_units for a in ARMS for r in range(1, ARM_REPLICAS[a] + 1)}
    present = {(k["unit"], k["arm"], k["replica"]) for k in key.values()}
    complete_units = sorted(u for u in all_units if all((u, a, r) in present for a in ARMS
                                                        for r in range(1, ARM_REPLICAS[a] + 1)))
    cards_sha = hashlib.sha256("".join(c["card_id"] + c["sha256"] for c in cards).encode()).hexdigest()[:16]
    dump(CARDS, {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sha256": cards_sha, "cards": cards})
    key_doc = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cards_sha256": cards_sha, "cards": key,
               "skipped": skipped, "complete_units": complete_units,
               "missing_runs": sorted(f"{u}/{a}_r{r}" for u, a, r in expected - present)}
    dump(KEY, key_doc)
    lint = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cards_sha256": cards_sha,
            "passed": not violations, "cards_per_arm": {a: dict(per_arm[a]) for a in ARMS},
            "violations_per_arm": dict(viol_arm), "violations": violations,
            "allowed_project_text": allowed,
            "normalisation_per_arm": {a: dict(counts_by_arm[a]) for a in ARMS}}
    dump(LINT, lint)
    return lint, key_doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--c-incomplete-as-failure", action="store_true",
                    help="card a C run whose run_status is not 'complete' as FALHA (default: skip it)")
    args = ap.parse_args(argv)
    lint, key = build(args.c_incomplete_as_failure)
    print(f"cards: {sum(sum(v.values()) for v in lint['cards_per_arm'].values())} "
          f"(per arm and deliverable: {json.dumps(lint['cards_per_arm'])})")
    print(f"complete units ({len(ARMS)} arms, replicas {ARM_REPLICAS}): {len(key['complete_units'])}; "
          f"missing runs: {len(key['missing_runs'])}; skipped: {len(key['skipped'])}")
    print(f"normalisation: {json.dumps(lint['normalisation_per_arm'])}")
    print(f"allowed project-text hits: {len(lint['allowed_project_text'])}")
    if lint["violations"]:
        print(f"LINT FAILED: {len(lint['violations'])} marker(s) left ({json.dumps(lint['violations_per_arm'])}):")
        for v in lint["violations"][:40]:
            print(f"  {v['card_id']} {v['unit']} {v['arm']} {v['field']}: {v['marker']} in {v['token']!r}: "
                  f"...{v['context']}...")
        return 1
    print("lint passed: no arm-revealing marker in any card")
    return 0


if __name__ == "__main__":
    sys.exit(main())
