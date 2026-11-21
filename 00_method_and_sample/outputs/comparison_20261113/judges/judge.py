#!/usr/bin/env python3
"""Blind judges of the 2026-11-13 comparison (ESPEC_COMPARACAO §3.7): phase 1, phase 2, blindness probe.

Judges (subscriptions, headless, the pattern of run_one.py): opus = Claude Code with
claude-opus-5-5 (principal); sol = Codex with gpt-6.1-sol (second). Effort high, no web, a
read-only checkout of the unit at its pinned commit (clone --shared of the base under
/home/antonow/comparison_20261113/base, no remote, worktree chmod a-w), a fresh HOME and CLI
config directory holding only a copy of the subscription credential (refreshed token copied
back), secret variables stripped, the prompt on stdin, JSON output against a schema
(--json-schema / --output-schema) validated again here.

Subcommands
  phase1     answer key per unit and judge (prompts/phase1.txt; closed-PR units get the
             maintainers' answer from data/pr_outcomes, prompts/phase1_maintainer.txt).
             -> phase1/<judge>/<unit>.json
  gabarito   no model call: both judges' phase-1 answers side by side -> gabarito_proposto.json/.md
             and gabarito_final.template.json (the principal's answer, fixed_by_user false). The
             user reviews it and saves gabarito_final.json with fixed_by_user true per unit.
  phase2     scores per unit and judge: every judged card of the unit (PR / ISSUE / NO_ACTION;
             FAILURE cards are A = 0 without a judge), in the judge's own seeded order, against
             gabarito_final.json (prompts/phase2.txt, rubric verbatim from §3.6).
             -> phase2/<judge>/<unit>.json. Needs cards.py's lint passed on the current cards, the
             unit complete (4 arms x 4 replicas; --allow-incomplete to override) and fixed by the user.
  blindness  40 judged cards (10 per arm, seeded), one opus session each with the card only and no
             tools: which arm wrote it (prompts/blindness.txt). -> blindness/<card>.json
JUDGE_VIEW=patch (patch view, 2026-11-14): phase2 sends only the PR cards of a unit, each shown as the
finding, the diff against the analysed commit and whether the author built it, with the title and
description withheld (prompts/phase2_patch.txt: rubric R2 verbatim from phase2.txt, no r4/r5)
-> phase2<SUFFIX>_patch/<judge>/<unit>.json; blindness samples only PR cards (seeded, --per-arm,
default 10 per arm, fewer when an arm has fewer) rendered the same way -> blindness<SUFFIX>_patch/.
JUDGE_VIEW=full: the default prompts, but blindness writes to blindness<SUFFIX>/ (unset, blindness writes
to blindness/ whatever the suffix, so an ARMS_TAG rerun, whose card ids are the first cards', is skipped).
--dry-run on phase1/phase2/blindness writes the prompts, schemas and commands it would send under
dry_run/<phase>/ and lints every prompt for the §3.7 markers (the blindness prompt names the arms by
design, so only its card is linted). It calls no model and creates no checkout.

Resumable: a task whose output exists (phase 2: with the same cards) is skipped. Subscription limit
or overload: the judge pauses for the time the CLI gave (30 min otherwise) and the other judge's
tasks go on, MAX_LIMIT_WAITS per judge (as driver.py). Authentication failure stops that judge.
Invalid JSON: 2 more attempts, then <task>.failed.json. A STOP file in this directory ends the pass.
Refuses to start while driver.py / run_one.py run (same subscriptions) unless --allow-concurrent.

Usage (WSL, venv):
  python outputs/comparison_20261113/judges/judge.py phase1 [--judges opus,sol] [--units u1,u2] [--dry-run]
  python outputs/comparison_20261113/judges/judge.py gabarito
  python outputs/comparison_20261113/judges/judge.py phase2 [--judges ...] [--units ...] [--dry-run]
  python outputs/comparison_20261113/judges/judge.py blindness [--n 40] [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cards as C  # noqa: E402  (also puts OUT, src and frozen_pr_20261113 on sys.path)
from common import (CLAUDE_BIN, CLAUDE_CRED_DIR, CODEX_BIN, CODEX_CRED_DIR, FINDING,  # noqa: E402
                    base_dir, dump, load)
import run_one  # noqa: E402  (clean_env, credentials, limit/auth patterns, transcript parsers)

TCC = C.TCC
SPEC = TCC / "outputs/design_20261110/ESPEC_COMPARACAO.md"
PROMPTS = HERE / "prompts"
JUDGES = {
    "opus": {"cli": "claude", "model": "claude-opus-5-5", "effort": "high", "family": "S", "principal": True},
    "sol": {"cli": "codex", "model": "gpt-6.1-sol", "effort": "high", "family": "O", "principal": False},
}
PRINCIPAL = "opus"
CLOSED_GROUPS = {"fechado", "our_pr_closed"}    # units whose answer key starts from the maintainers' answer
# Reading "v2" (the author's rule, 2026-11-14): the answer key is written without any maintainer's
# answer (a closed pull request's feedback is one project's preference, and the same change was merged
# elsewhere), every deliverable is acceptable, and its outputs go to phase1_v2/, phase2_v2/ and
# gabarito_v2.json, beside the protocol reading's.
READING = os.environ.get("JUDGE_READING", "")
SUFFIX = (f"_{READING}" if READING else "") + (f"_{os.environ['ARMS_TAG']}" if os.environ.get("ARMS_TAG") else "")
# JUDGE_VIEW (2026-11-14): "patch" judges PR cards on the diff alone (title and description withheld;
# ISSUE and NO_ACTION cards are not sent: the author's reading counts them 1 whatever their text) and
# probes blindness on such cards; "full" keeps the default prompts and only moves the blindness outputs
# to blindness<SUFFIX>/. Unset: the behaviour before the patch view, byte for byte.
VIEW = os.environ.get("JUDGE_VIEW", "")
if VIEW not in ("", "patch", "full"):
    raise SystemExit(f"JUDGE_VIEW={VIEW!r}: expected patch or full")
VIEW_SUFFIX = "_patch" if VIEW == "patch" else ""
# BLIND_SET=optA (2026-11-17, docs/preregistro_opcao_a_20261116.md, blindness probe of option A): the probe
# draws from arms C, TP and SP2 (OP dropped by the dated deviation), with prompts/blindness_optA.txt (the same
# prompt, the arm list replaced) and writes to blindness_optA[_patch]/. Unset: unchanged.
BLIND_SET = os.environ.get("BLIND_SET", "")
if BLIND_SET not in ("", "optA"):
    raise SystemExit(f"BLIND_SET={BLIND_SET!r}: expected optA")
BLIND_PROMPT = "blindness_optA.txt" if BLIND_SET == "optA" else "blindness.txt"
BLIND_GUESSES = ["TP", "SP2", "C"] if BLIND_SET == "optA" else ["S", "O", "T", "C", "D"]
if BLIND_SET == "optA":
    C.BLIND_ARMS = ("C", "TP", "SP2")
GAB_PROPOSED = HERE / (f"gabarito_proposto_{READING}.json" if READING else "gabarito_proposto.json")
GAB_FINAL = HERE / ({"v2": "gabarito_v2.json", "v3": "gabarito_v3.json"}.get(READING, "gabarito_final.json"))
TIMEOUT = {"phase1": int(os.environ.get("JUDGE_TIMEOUT_PHASE1", 45 * 60)),
           "phase2": int(os.environ.get("JUDGE_TIMEOUT_PHASE2", 75 * 60)),
           "blindness": int(os.environ.get("JUDGE_TIMEOUT_BLINDNESS", 10 * 60))}
MAX_LIMIT_WAITS = int(os.environ.get("MAX_LIMIT_WAITS", 24))
ATTEMPTS = 3
SUSPICIOUS = re.compile(r"comparison_20261113/(?!.*judges/checkout)|judges/cards|frozen_runs|cognalys_arm|tcc_v3|"
                        r"\.claude|\.codex|agent_baseline", re.I)
PATH_RE = re.compile(r"(?<![\w.])/(?:mnt|home|tmp|root|etc|opt|usr/local)/[^\s\"'\\,;)]*")
CLASS_FROM_JUDGE = {"PR": "PR", "ISSUE": "ISSUE", "NO_ACTION": "NENHUMA AÇÃO"}


def log(msg):
    print(time.strftime("%Y-%m-%dT%H:%M:%S%z"), msg, flush=True)


# ------------------------------------------------------------------ schemas and validation
def schema_phase1():
    return {"type": "object", "additionalProperties": False,
            "required": ["acceptable_classes", "reason", "must_change", "must_not_break"],
            "properties": {
                "acceptable_classes": {"type": "array", "minItems": 1,
                                       "items": {"type": "string", "enum": ["PR", "ISSUE", "NO_ACTION"]}},
                "reason": {"type": "string"},
                "must_change": {"type": "array", "items": {"type": "string"}},
                "must_not_break": {"type": "array", "items": {"type": "string"}}}}


def _score(top):
    return {"anyOf": [{"type": "integer", "enum": list(range(top + 1))}, {"type": "null"}]}


def schema_phase2(card_ids):
    card = {"type": "object", "additionalProperties": False,
            "required": ["card_id", "r2", "regression_scenario", "fixes_what_is_reported", "r4", "r5", "notes"],
            "properties": {"card_id": {"type": "string", "enum": sorted(card_ids)}, "r2": _score(3),
                           "regression_scenario": {"type": "string"},
                           "fixes_what_is_reported": {"type": "string", "enum": ["yes", "no", "unclear"]},
                           "r4": _score(2), "r5": _score(2), "notes": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["cards"],
            "properties": {"cards": {"type": "array", "minItems": len(card_ids), "items": card}}}


def schema_phase2_patch(card_ids):
    """Patch view: every card is a PR, scored on R2 only (r4 and r5 absent)."""
    card = {"type": "object", "additionalProperties": False,
            "required": ["card_id", "r2", "regression_scenario", "fixes_what_is_reported", "notes"],
            "properties": {"card_id": {"type": "string", "enum": sorted(card_ids)},
                           "r2": {"type": "integer", "enum": list(range(4))},
                           "regression_scenario": {"type": "string"},
                           "fixes_what_is_reported": {"type": "string", "enum": ["yes", "no", "unclear"]},
                           "notes": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["cards"],
            "properties": {"cards": {"type": "array", "minItems": len(card_ids), "items": card}}}


def schema_blindness():
    return {"type": "object", "additionalProperties": False, "required": ["guess", "confidence", "reason"],
            "properties": {"guess": {"type": "string", "enum": BLIND_GUESSES},
                           "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                           "reason": {"type": "string"}}}


def validate(value, schema, path="$"):
    """Errors of `value` against the subset of JSON Schema these schemas use (no jsonschema in the venv)."""
    errors = []
    if "anyOf" in schema:
        if all(validate(value, s, path) for s in schema["anyOf"]):
            errors.append(f"{path}: matches no alternative")
        return errors
    kind = schema.get("type")
    checks = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
              "string": lambda v: isinstance(v, str), "null": lambda v: v is None,
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
              "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)}
    if kind and not checks[kind](value):
        return [f"{path}: expected {kind}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
    if kind == "number":
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} out of range")
    if kind == "object":
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: missing")
        if schema.get("additionalProperties") is False:
            errors += [f"{path}.{k}: not allowed" for k in value if k not in schema.get("properties", {})]
        for name, sub in schema.get("properties", {}).items():
            if name in value:
                errors += validate(value[name], sub, f"{path}.{name}")
    if kind == "array":
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        for i, item in enumerate(value):
            errors += validate(item, schema.get("items", {}), f"{path}[{i}]")
    return errors


def extract_json(text: str):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except ValueError:
                return None
    return None


# ------------------------------------------------------------------ inputs of the prompts
def findings_block(unit):
    return "".join(FINDING.format(**row) for row in unit["rows"])


def maintainer_answer(unit):
    """(title, feedback text, pull request) of the closed pull request for this unit's finding, or None."""
    if unit.get("group") not in CLOSED_GROUPS or READING in ("v2", "v3"):
        return None
    owner_repo = unit["repo_url"].rstrip("/").split("github.com/")[-1]
    path = TCC / "data/pr_outcomes" / (owner_repo.replace("/", "__") + ".json")
    if not path.is_file():
        matches = [p for p in (TCC / "data/pr_outcomes").glob("*.json")
                   if p.stem.lower() == owner_repo.replace("/", "__").lower()]
        path = matches[0] if matches else path
    if not path.is_file():
        return None
    records = load(path)
    records = records if isinstance(records, list) else records.get("pull_requests", [])
    prs = set(unit.get("prs") or [])
    ids = set(unit["finding_ids"])
    triples = {(r["class"], r["method"], r["spec"]) for r in unit["rows"]}
    for rec in records:
        if rec.get("state") != "closed":
            continue
        if rec.get("pull_request") in prs or rec.get("finding_id") in ids or \
                (rec.get("csv_class"), rec.get("method_name"), rec.get("spec")) in triples:
            feedback = "\n\n".join(f"{m.get('author', 'maintainer')} ({m.get('kind', 'comment')}):\n{m.get('body', '').strip()}"
                                   for m in rec.get("maintainer_feedback") or [] if str(m.get("body", "")).strip())
            return rec.get("title", ""), feedback or "(closed without a comment)", rec.get("pull_request")
    return None


def render_card(card):
    lines = [f"=== Card {card['card_id']} ===", f"Deliverable: {card['deliverable']}"]
    if card.get("title"):
        lines.append(f"Title: {card['title']}")
    if card["deliverable"] == "PR":
        lines.append(f"Author built this exact diff: {card.get('author_build') or 'no'}")
        lines += ["Description:", card.get("text") or "(empty)", "Diff:", "<<<DIFF", (card.get("diff") or "").rstrip("\n"),
                  "DIFF>>>"]
    elif card["deliverable"] == "ISSUE":
        lines += ["Issue text:", card.get("text") or "(empty)"]
    else:
        lines += ["Reason:", card.get("text") or "(empty)"]
    lines.append(f"=== End of card {card['card_id']} ===")
    return "\n".join(lines)


def render_card_patch(card):
    """A PR card in the patch view: the diff and whether the author built it; title and description withheld."""
    assert card["deliverable"] == "PR", card["card_id"]
    return "\n".join([f"=== Card {card['card_id']} ===", f"Deliverable: {card['deliverable']}",
                      f"Author built this exact diff: {card.get('author_build') or 'no'}",
                      "Title and description: withheld", "Diff:", "<<<DIFF", (card.get("diff") or "").rstrip("\n"),
                      "DIFF>>>", f"=== End of card {card['card_id']} ==="])


def out_name(phase):
    """Output directory name of a phase under the current reading, arms tag and view."""
    if phase == "blindness" and BLIND_SET == "optA":
        return "blindness_optA" + ("_patch" if VIEW == "patch" else "")
    if VIEW == "patch":
        return f"{phase}{SUFFIX}_patch"
    if phase == "blindness":
        return f"blindness{SUFFIX}" if VIEW == "full" else "blindness"
    return f"{phase}{SUFFIX}"


def listing(items):
    return "; ".join(items) if items else "(none)"


def prompt_phase1(unit):
    answer = maintainer_answer(unit)
    block = ""
    if answer:
        block = "\n" + Template((PROMPTS / "phase1_maintainer.txt").read_text(encoding="utf-8")).substitute(
            pr_title=answer[0], feedback=answer[1])
    return Template((PROMPTS / "phase1.txt").read_text(encoding="utf-8")).substitute(
        findings=findings_block(unit), maintainer_block=block), answer


def prompt_phase2(unit, gab, unit_cards, judge, salt):
    order = C.judge_order(salt, judge, unit["unit"], [c["card_id"] for c in unit_cards])
    by_id = {c["card_id"]: c for c in unit_cards}
    acceptable = ", ".join(gab["acceptable_classes"])
    return Template((PROMPTS / "phase2.txt").read_text(encoding="utf-8")).substitute(
        findings=findings_block(unit), acceptable=acceptable, reason=gab.get("reason", ""),
        must_change=listing(gab.get("must_change") or []), must_not_break=listing(gab.get("must_not_break") or []),
        n=len(order), cards="\n\n".join(render_card(by_id[i]) for i in order)), order


def prompt_phase2_patch(unit, gab, unit_cards, judge, salt):
    order = C.judge_order(salt, judge, unit["unit"], [c["card_id"] for c in unit_cards])
    by_id = {c["card_id"]: c for c in unit_cards}
    acceptable = ", ".join(gab["acceptable_classes"])
    return Template((PROMPTS / "phase2_patch.txt").read_text(encoding="utf-8")).substitute(
        findings=findings_block(unit), acceptable=acceptable, reason=gab.get("reason", ""),
        must_change=listing(gab.get("must_change") or []), must_not_break=listing(gab.get("must_not_break") or []),
        n=len(order), cards="\n\n".join(render_card_patch(by_id[i]) for i in order)), order


def prompt_blindness(card):
    rendered = render_card_patch(card) if VIEW == "patch" else render_card(card)
    return Template((PROMPTS / BLIND_PROMPT).read_text(encoding="utf-8")).substitute(card=rendered)


def rubric_is_verbatim():
    spec = SPEC.read_text(encoding="utf-8")
    m = re.search(r"Notas por cartão \(fase 2\).*?```\n(.*?)```", spec, re.S)
    return bool(m) and m.group(1).strip("\n") in (PROMPTS / "phase2.txt").read_text(encoding="utf-8")


def r2_is_verbatim():
    """Patch view: the R2 block of phase2.txt (itself verbatim from §3.6) appears unchanged in phase2_patch.txt."""
    full = (PROMPTS / "phase2.txt").read_text(encoding="utf-8")
    m = re.search(r"^R2 PATCH.*?(?=^R4 TEXT)", full, re.S | re.M)
    return bool(m) and rubric_is_verbatim() and \
        m.group(0).strip("\n") in (PROMPTS / "phase2_patch.txt").read_text(encoding="utf-8")


# ------------------------------------------------------------------ checkouts and sessions
def checkout(unit) -> Path:
    """A read-only checkout of the unit at its pinned commit (kept between sessions; local only)."""
    repo = C.JWORK / "checkout" / unit["unit"] / "repo"
    marker = repo.parent / "ready"
    if marker.is_file() and (repo / ".git").exists():
        return repo
    if repo.parent.exists():
        subprocess.run(["chmod", "-R", "u+w", str(repo.parent)], capture_output=True)
        shutil.rmtree(repo.parent, ignore_errors=True)
    repo.parent.mkdir(parents=True)
    sh = run_one.sh
    sh("git", "clone", "-q", "--shared", "--no-checkout", str(base_dir(unit)), str(repo), check=True)
    sh("git", "-C", str(repo), "checkout", "-q", "--detach", unit["pinned_commit"], check=True)
    sh("git", "-C", str(repo), "remote", "remove", "origin")
    sh("git", "-C", str(repo), "branch", "-D", "pinned")
    subprocess.run(["chmod", "-R", "a-w", str(repo)], check=True)
    subprocess.run(["chmod", "-R", "u+w", str(repo / ".git")], check=True)   # git log/show may write locks
    marker.write_text(unit["pinned_commit"] + "\n")
    return repo


def command(judge, schema_path, schema, cwd, session, tools):
    j = JUDGES[judge]
    if j["cli"] == "claude":
        cmd = [str(CLAUDE_BIN), "-p", "--model", j["model"], "--effort", j["effort"],
               "--output-format", "stream-json", "--verbose", "--permission-mode", "default",
               "--json-schema", json.dumps(schema, separators=(",", ":"))]
        if tools == "repo":
            cmd += ["--tools", "Read,Glob,Grep,Bash",
                    "--allowedTools", ",".join(["Read", "Glob", "Grep", *run_one.READ_ONLY_SHELL]),
                    "--disallowedTools", "WebSearch,WebFetch,Edit,Write,NotebookEdit"]
        else:
            cmd += ["--tools", ""]
        return cmd
    return [str(CODEX_BIN), "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
            "-m", j["model"], "-c", f'model_reasoning_effort="{j["effort"]}"', "-s", "read-only",
            "-c", 'web_search="disabled"', "--output-schema", str(schema_path), "-C", str(cwd),
            "-o", str(session / "last_message.txt"), "-"]


def run_proc(cmd, cwd, env, timeout, stdout_path, stderr_path, stdin_text):
    stdin_path = Path(stdout_path).with_suffix(".stdin.txt")
    stdin_path.write_text(stdin_text, encoding="utf-8")
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err, open(stdin_path) as inp:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=err, stdin=inp, start_new_session=True)
        try:
            proc.wait(timeout=timeout)
            status = f"exit {proc.returncode}"
        except subprocess.TimeoutExpired:
            status = "timeout"
        finally:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                time.sleep(2)
    stdin_path.unlink(missing_ok=True)
    return status


def access_report(tool_inputs, cwd):
    text = json.dumps(tool_inputs)
    paths = sorted({p for p in PATH_RE.findall(text) if not p.startswith(str(cwd))})
    web = [t for t in tool_inputs if str(t.get("tool")) in ("WebSearch", "WebFetch", "web_search", "web_fetch")]
    return {"paths_outside_checkout": paths[:100], "suspicious_paths": [p for p in paths if SUSPICIOUS.search(p)],
            "web_tool_calls": len(web)}


def session(judge, phase, task, prompt, schema, cwd, tools, raw_dir):
    """One judge session. Returns (verdict, payload): ok / invalid / limit / auth / timeout / infra."""
    j = JUDGES[judge]
    sess = C.JWORK / "sessions" / judge / phase / task
    if sess.exists():
        subprocess.run(["chmod", "-R", "u+w", str(sess)], capture_output=True)
    shutil.rmtree(sess, ignore_errors=True)
    home = sess / "home"
    home.mkdir(parents=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    schema_path = sess / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    if cwd is None:
        cwd = sess / "empty"
        cwd.mkdir()
    env = run_one.clean_env(home)
    if j["cli"] == "claude":
        master, run_copy = CLAUDE_CRED_DIR / ".credentials.json", home / ".claude" / ".credentials.json"
        env.update({"CLAUDE_CONFIG_DIR": str(home / ".claude"), "DISABLE_AUTOUPDATER": "1"})
    else:
        master, run_copy = CODEX_CRED_DIR / "auth.json", home / ".codex" / "auth.json"
        env["CODEX_HOME"] = str(home / ".codex")
    run_one.copy_secret(master, run_copy)
    cmd = command(judge, schema_path, schema, cwd, sess, tools)
    transcript, stderr_path = raw_dir / f"{task}.jsonl", raw_dir / f"{task}.stderr.txt"
    started = time.time()
    status = run_proc(cmd, cwd, env, TIMEOUT[phase], transcript, stderr_path, prompt)
    seconds = round(time.time() - started)
    run_one.sync_back(run_copy, master)
    if j["cli"] == "claude":
        parsed = run_one.parse_claude(transcript)
        result_event = {}
        for line in transcript.read_text(errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == "result":
                result_event = e
        output = result_event.get("structured_output")
        if output is None:
            output = extract_json(result_event.get("result", ""))
    else:
        parsed = run_one.parse_codex(transcript)
        last = sess / "last_message.txt"
        output = extract_json(last.read_text(errors="replace")) if last.is_file() else None
        if last.is_file():
            shutil.copy2(last, raw_dir / f"{task}.last_message.txt")
    stderr = stderr_path.read_text(errors="replace")[-4000:]
    meta = {"judge": judge, "cli": j["cli"], "model": j["model"], "effort": j["effort"], "status": status,
            "seconds": seconds, "tokens": parsed.get("tokens"), "turns": parsed.get("turns"),
            "tool_calls": len(parsed.get("tool_inputs") or []),
            "claude_reported_cost_usd": parsed.get("claude_reported_cost_usd"),
            "access": access_report(parsed.get("tool_inputs") or [], cwd),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()[:16],
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    subprocess.run(["chmod", "-R", "u+w", str(sess)], capture_output=True)
    shutil.rmtree(sess, ignore_errors=True)
    if status == "timeout":
        return "timeout", meta
    errors = ["no JSON output"] if output is None else validate(output, schema)
    if not errors:
        return "ok", {**meta, "output": output}
    failure = (parsed.get("error_text") or "") + "\n" + stderr
    if run_one.AUTH_RE.search(failure) and output is None:
        return "auth", {**meta, "error": failure[-600:]}
    if run_one.LIMIT_RE.search(failure) and output is None:
        return "limit", {**meta, "error": failure[-600:], "retry_after": run_one.retry_after(failure)}
    if output is None and not parsed.get("has_result") and seconds < 30:
        return "infra", {**meta, "error": failure[-600:]}
    return "invalid", {**meta, "errors": errors[:20], "raw_output": output}


# ------------------------------------------------------------------ tasks
class Task:
    def __init__(self, judge, phase, name, prompt, schema, unit, tools, out, post=None, extra=None):
        self.judge, self.phase, self.name, self.prompt, self.schema = judge, phase, name, prompt, schema
        self.unit, self.tools, self.out, self.post, self.extra = unit, tools, out, post, extra or {}


def post_phase2(task, output):
    """Score-placement checks the schema cannot express. Returns (output, problems that need a retry)."""
    by_id = task.extra["deliverables"]
    seen = collections.Counter(c["card_id"] for c in output["cards"])
    problems = [f"card {i} missing" for i in by_id if not seen[i]] + \
               [f"card {i} given {n} times" for i, n in seen.items() if n > 1]
    dropped = []
    for entry in output["cards"]:
        deliverable = by_id.get(entry["card_id"])
        allowed = {"PR": ("r2", "r4"), "ISSUE": ("r4", "r5"), "NO_ACTION": ("r5",)}[deliverable]
        for score in ("r2", "r4", "r5"):
            if score in allowed and entry[score] is None:
                problems.append(f"{entry['card_id']}: {score} missing for a {deliverable} card")
            elif score not in allowed and entry[score] is not None:
                dropped.append(f"{entry['card_id']}.{score}={entry[score]}")
                entry[score] = None
    output["dropped_scores"] = dropped
    return output, problems


def post_phase2_patch(task, output):
    """Patch view: one entry per PR card (the schema requires r2; r4 and r5 do not exist)."""
    seen = collections.Counter(c["card_id"] for c in output["cards"])
    problems = [f"card {i} missing" for i in task.extra["deliverables"] if not seen[i]] + \
               [f"card {i} given {n} times" for i, n in seen.items() if n > 1]
    output["dropped_scores"] = []
    return output, problems


def busy():
    r = subprocess.run(["pgrep", "-af", r"comparison_20261113/(driver|run_one)\.py"], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if "pgrep" not in l]


def execute(tasks, allow_concurrent):
    if not tasks:
        log("nothing to do")
        return 0
    if busy() and not allow_concurrent:
        log("the agent queue (driver.py/run_one.py) is running on the same subscriptions; refusing "
            "(--allow-concurrent to override): " + busy()[0][:160])
        return 2
    paused = {j: 0.0 for j in JUDGES}
    waits, disabled, attempts = collections.Counter(), set(), collections.Counter()
    pending = list(tasks)
    log(f"{len(pending)} task(s): " + ", ".join(f"{t.judge}/{t.name}" for t in pending[:12]) +
        (" ..." if len(pending) > 12 else ""))
    while pending:
        if (HERE / "STOP").exists():
            log("STOP file found; ending")
            return 0
        pending = [t for t in pending if t.judge not in disabled]
        if not pending:
            break
        now = time.time()
        ready = [t for t in pending if paused[t.judge] <= now]
        if not ready:
            wake = min(paused[t.judge] for t in pending)
            log(f"every pending judge is paused; sleeping {int(wake - now)} s")
            time.sleep(max(60, wake - now))
            continue
        task = ready[0]
        cwd = checkout(task.unit) if task.tools == "repo" else None
        raw = HERE / "raw" / f"{task.phase}{SUFFIX}{VIEW_SUFFIX}" / task.judge
        log(f"{task.phase} {task.judge} {task.name}")
        verdict, payload = session(task.judge, task.phase, task.name, task.prompt, task.schema, cwd, task.tools, raw)
        if verdict == "ok" and task.post:
            payload["output"], problems = task.post(task, payload["output"])
            if problems:
                verdict, payload = "invalid", {**payload, "errors": problems[:20], "raw_output": payload["output"]}
        if verdict == "ok":
            dump(task.out, {**task.extra.get("record", {}), **payload})
            pending.remove(task)
            log(f"  recorded {task.out.relative_to(HERE)} ({payload['seconds']} s; access "
                f"{payload['access']['suspicious_paths'] or 'clean'})")
        elif verdict == "limit":
            waits[task.judge] += 1
            if waits[task.judge] > MAX_LIMIT_WAITS:
                disabled.add(task.judge)
                log(f"  judge {task.judge}: limit hit {waits[task.judge]} times; stopped for this pass")
            else:
                paused[task.judge] = time.time() + payload["retry_after"]
                log(f"  judge {task.judge}: subscription limit/overload; paused {payload['retry_after']} s")
        elif verdict == "auth":
            disabled.add(task.judge)
            log(f"  judge {task.judge}: AUTHENTICATION FAILURE; stopped (log in again, rerun)")
        else:
            attempts[(task.judge, task.name)] += 1
            log(f"  {verdict} (attempt {attempts[(task.judge, task.name)]}/{ATTEMPTS}): "
                f"{json.dumps(payload.get('errors') or payload.get('error') or payload.get('status'))[:300]}")
            if attempts[(task.judge, task.name)] >= ATTEMPTS:
                dump(task.out.with_suffix(".failed.json"), {**task.extra.get("record", {}), **payload,
                                                            "verdict": verdict})
                pending.remove(task)
            elif verdict == "infra":
                time.sleep(30)
    left = [f"{t.judge}/{t.name}" for t in pending]
    log("pass finished" + (f"; not done: {left}" if left else ""))
    return 0 if not left else 3


# ------------------------------------------------------------------ phases
def selected_units(args):
    all_units = C.units()
    wanted = [u for u in (args.units or "").split(",") if u]
    missing = [u for u in wanted if u not in all_units]
    if missing:
        raise SystemExit(f"unknown units: {missing}")
    return {u: all_units[u] for u in (wanted or all_units)}


def selected_judges(args):
    judges = [j for j in (args.judges or "opus,sol").split(",") if j]
    bad = [j for j in judges if j not in JUDGES]
    if bad:
        raise SystemExit(f"unknown judges: {bad}")
    return judges


def tasks_phase1(args):
    tasks = []
    for unit_id, unit in selected_units(args).items():
        prompt, answer = prompt_phase1(unit)
        for judge in selected_judges(args):
            out = HERE / f"phase1{SUFFIX}" / judge / f"{unit_id}.json"
            if out.is_file() and not args.redo:
                continue
            record = {"unit": unit_id, "phase": "phase1", "pinned_commit": unit["pinned_commit"],
                      "maintainer_answer_from": answer[2] if answer else None}
            tasks.append(Task(judge, "phase1", unit_id, prompt, schema_phase1(), unit, "repo", out,
                              extra={"record": record}))
    return tasks


def current_cards(require_lint=True):
    if not C.CARDS.is_file():
        raise SystemExit("no cards: run cards.py first")
    cards = load(C.CARDS)
    key = load(C.KEY)
    lint = load(C.LINT) if C.LINT.is_file() else {}
    if require_lint and (not lint.get("passed") or lint.get("cards_sha256") != cards["sha256"]):
        raise SystemExit("cards.py lint has not passed on the current cards; run cards.py and fix what it reports")
    return cards, key


def tasks_phase2(args, dry=False):
    cards, key = current_cards(require_lint=not dry)
    salt = C.seed_info()["salt"]
    gabs = load(GAB_FINAL) if GAB_FINAL.is_file() else {}
    proposed = load(GAB_PROPOSED) if GAB_PROPOSED.is_file() else {}
    tasks, skipped = [], []
    for unit_id, unit in selected_units(args).items():
        gab = gabs.get(unit_id)
        if not (gab and gab.get("fixed_by_user")):
            if not dry:
                skipped.append(f"{unit_id}: no answer key fixed by the user in gabarito_final.json")
                continue
            gab = (proposed.get(unit_id) or {}).get("proposed") or {
                "acceptable_classes": ["(answer key pending: phase 1 not run)"], "reason": "(pending)",
                "must_change": [], "must_not_break": []}
        if unit_id not in key["complete_units"] and not (args.allow_incomplete or dry):
            skipped.append(f"{unit_id}: not every arm and replica has a card yet")
            continue
        unit_cards = [c for c in cards["cards"] if c["unit"] == unit_id and c["judged"]]
        patch = VIEW == "patch"
        if patch:
            unit_cards = [c for c in unit_cards if c["deliverable"] == "PR"]
            if not unit_cards:
                skipped.append(f"{unit_id}: no PR card (patch view)")
                continue
        if not unit_cards:
            skipped.append(f"{unit_id}: no judged card")
            continue
        for judge in selected_judges(args):
            if patch:
                prompt, order = prompt_phase2_patch(unit, gab, unit_cards, judge, salt)
                out = HERE / out_name("phase2") / judge / f"{unit_id}.json"
            else:
                prompt, order = prompt_phase2(unit, gab, unit_cards, judge, salt)
                out = HERE / f"phase2{SUFFIX}" / judge / f"{unit_id}.json"
            shas = {c["card_id"]: c["sha256"] for c in unit_cards}
            if out.is_file() and not args.redo and load(out).get("card_sha256") == shas:
                continue
            record = {"unit": unit_id, "phase": "phase2", "order": order, "card_sha256": shas,
                      "gabarito": gab, "cards_file_sha256": cards["sha256"]}
            if patch:
                record["view"] = "patch"
            tasks.append(Task(judge, "phase2", unit_id, prompt,
                              (schema_phase2_patch if patch else schema_phase2)(list(shas)), unit, "repo", out,
                              post=post_phase2_patch if patch else post_phase2,
                              extra={"record": record, "deliverables": {c["card_id"]: c["deliverable"]
                                                                        for c in unit_cards}}))
    for s in skipped:
        log("skip " + s)
    return tasks


def blindness_sample(cards, key, n, salt):
    per_arm = n // len(C.BLIND_ARMS)
    sample = []
    for arm in C.BLIND_ARMS:
        pool = sorted(c["card_id"] for c in cards["cards"] if c["judged"] and key["cards"][c["card_id"]]["arm"] == arm)
        C.rng(salt, f"blindness|{arm}").shuffle(pool)
        sample += pool[:per_arm]
    C.rng(salt, "blindness|order").shuffle(sample)
    return sample


def blindness_sample_patch(cards, key, per_arm, salt):
    """Patch view: up to per_arm PR cards per arm (seeded); what was not drawn is reported and logged."""
    sample, report = [], {}
    for arm in C.BLIND_ARMS:
        arm_cards = [c for c in cards["cards"] if c["judged"] and key["cards"][c["card_id"]]["arm"] == arm]
        pool = sorted(c["card_id"] for c in arm_cards if c["deliverable"] == "PR")
        C.rng(salt, f"blindness_patch|{arm}").shuffle(pool)
        drawn = min(per_arm, len(pool))
        sample += pool[:drawn]
        report[arm] = {"pr_cards": len(pool), "drawn": drawn, "short_of_per_arm": per_arm - drawn,
                       "pr_cards_not_drawn": len(pool) - drawn, "non_pr_cards_left_out": len(arm_cards) - len(pool)}
        log(f"blindness patch view, arm {arm}: {drawn} of {len(pool)} PR card(s) drawn"
            + (f", {per_arm - drawn} short of {per_arm}" if drawn < per_arm else "")
            + f"; {len(arm_cards) - len(pool)} ISSUE/NO_ACTION card(s) left out")
    C.rng(salt, "blindness_patch|order").shuffle(sample)
    return sample, report


def tasks_blindness(args, dry=False):
    cards, key = current_cards(require_lint=not dry)
    salt = C.seed_info()["salt"]
    name = out_name("blindness")
    if VIEW == "patch":
        per_arm = args.per_arm or 10
        sample, report = blindness_sample_patch(cards, key, per_arm, salt)
        info = {"view": "patch", "per_arm": per_arm, "cards": sample, "per_arm_report": report}
    else:
        sample = blindness_sample(cards, key, args.n, salt)
        info = {"n": args.n, "per_arm": args.n // len(C.BLIND_ARMS), "cards": sample}
    info["per_arm_drawn"] = {a: sum(key["cards"][i]["arm"] == a for i in sample) for a in C.BLIND_ARMS}
    by_id = {c["card_id"]: c for c in cards["cards"]}
    dump((HERE / "dry_run" / f"{name}_sample.json") if dry else (HERE / name / "sample.json"), info)
    tasks, all_units = [], C.units()
    for cid in sample:
        out = HERE / name / f"{cid}.json"
        if out.is_file() and not args.redo:
            continue
        tasks.append(Task(PRINCIPAL, "blindness", cid, prompt_blindness(by_id[cid]), schema_blindness(),
                          all_units[by_id[cid]["unit"]], "none", out,
                          extra={"record": {"card_id": cid, "card_sha256": by_id[cid]["sha256"],
                                            **({"view": "patch"} if VIEW == "patch" else {})}}))
    return tasks


# ------------------------------------------------------------------ dry run
def dry_run(phase, tasks):
    root = HERE / "dry_run" / (out_name(phase) if VIEW else phase)
    if root.exists():
        shutil.rmtree(root)
    rubric = (r2_is_verbatim() if VIEW == "patch" else rubric_is_verbatim()) if phase == "phase2" else None
    summary = {"phase": phase, "tasks": [], "rubric_verbatim": rubric}
    if VIEW:
        summary["view"] = VIEW
    for t in tasks:
        d = root / t.judge
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{t.name}.prompt.txt").write_text(t.prompt, encoding="utf-8")
        (d / f"{t.name}.schema.json").write_text(json.dumps(t.schema, indent=1), encoding="utf-8")
        cwd = (C.JWORK / "checkout" / t.unit["unit"] / "repo") if t.tools == "repo" else "<empty session dir>"
        sess = C.JWORK / "sessions" / t.judge / t.phase / t.name
        cmd = command(t.judge, sess / "schema.json", t.schema, cwd, sess, t.tools)
        cmd = [("<schema>" if a.startswith("{") else a) for a in cmd]
        (d / f"{t.name}.cmd.json").write_text(json.dumps({"cwd": str(cwd), "argv": cmd, "stdin": "the prompt file",
                                                          "timeout_s": TIMEOUT[t.phase]}, indent=1), encoding="utf-8")
        linted = t.prompt
        if phase == "blindness":
            linted = t.prompt[t.prompt.index("=== Card "):]
        hits = C.lint_text(linted, t.unit)
        base_ok = run_one.sh("git", "-C", str(base_dir(t.unit)), "cat-file", "-e",
                             t.unit["pinned_commit"] + "^{commit}").returncode == 0
        summary["tasks"].append({"judge": t.judge, "task": t.name, "prompt_chars": len(t.prompt),
                                 "base_has_pinned_commit": base_ok,
                                 "markers": [h for h in hits if not h["allowed_project_text"]],
                                 "allowed_project_text": [h for h in hits if h["allowed_project_text"]]})
    dump(root / "summary.json", summary)
    bad = [s for s in summary["tasks"] if s["markers"] or not s["base_has_pinned_commit"]]
    log(f"dry run {phase}: {len(tasks)} prompt(s) in {root.relative_to(TCC)}; "
        f"marker-free: {len(tasks) - sum(bool(s['markers']) for s in summary['tasks'])}/{len(tasks)}; "
        f"bases ok: {sum(s['base_has_pinned_commit'] for s in summary['tasks'])}/{len(tasks)}"
        + ("" if summary["rubric_verbatim"] is None else f"; rubric verbatim from §3.6: {summary['rubric_verbatim']}"))
    for s in bad:
        log(f"  {s['judge']}/{s['task']}: markers {[(h['marker'], h['token']) for h in s['markers']]} "
            f"base {s['base_has_pinned_commit']}")
    return 1 if bad or summary["rubric_verbatim"] is False else 0


# ------------------------------------------------------------------ gabarito proposal
def gabarito():
    units = C.units()
    proposal, md = {}, ["# Gabarito proposto (fase 1)", "",
                        "Respostas dos dois juízes por unidade. O usuário revisa e grava `gabarito_final.json` "
                        "(ponto de partida: `gabarito_final.template.json`, com a resposta do juiz principal) com "
                        "`fixed_by_user: true` em cada unidade. A fase 2 só roda nas unidades fixadas.", ""]
    template = {}
    for unit_id, unit in units.items():
        answers = {j: load(p)["output"] for j in JUDGES
                   if (p := HERE / "phase1" / j / f"{unit_id}.json").is_file()}
        if not answers:
            continue
        classes = {j: sorted(a["acceptable_classes"]) for j, a in answers.items()}
        agree = len(answers) == len(JUDGES) and len({tuple(v) for v in classes.values()}) == 1
        chosen = answers.get(PRINCIPAL) or next(iter(answers.values()))
        proposal[unit_id] = {"judges": answers, "same_classes": agree, "proposed": chosen,
                             "maintainer_answer": bool(maintainer_answer(unit))}
        template[unit_id] = {**chosen, "fixed_by_user": False,
                             "judges_agree_on_classes": agree}
        md += [f"## {unit_id} ({unit['group']}; {', '.join(unit['spec'])}; {unit['class']}.{unit['method']})", "",
               f"Classes iguais nos dois juízes: {'sim' if agree else 'NÃO'}"
               + ("; parte da resposta do mantenedor" if proposal[unit_id]["maintainer_answer"] else ""), ""]
        for j, a in answers.items():
            md += [f"**{j}**: {', '.join(a['acceptable_classes'])}", "", f"- razão: {a['reason']}",
                   f"- tem de mudar: {listing(a['must_change'])}", f"- não pode quebrar: {listing(a['must_not_break'])}", ""]
    dump(GAB_PROPOSED, proposal)
    (HERE / "gabarito_proposto.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    dump(HERE / "gabarito_final.template.json", template)
    log(f"gabarito: {len(proposal)} unit(s); judges agree on the classes in "
        f"{sum(p['same_classes'] for p in proposal.values())}; wrote gabarito_proposto.json/.md and "
        f"gabarito_final.template.json")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Blind judges (phase 1, phase 2, blindness probe).")
    ap.add_argument("phase", choices=("phase1", "gabarito", "phase2", "blindness"))
    ap.add_argument("--judges", help="comma list of opus,sol (default both)")
    ap.add_argument("--units", help="comma list of unit ids (default all)")
    ap.add_argument("--dry-run", action="store_true", help="write the prompts only; no model call")
    ap.add_argument("--redo", action="store_true", help="rerun tasks that already have an output")
    ap.add_argument("--allow-incomplete", action="store_true", help="phase 2 on a unit missing some runs")
    ap.add_argument("--allow-concurrent", action="store_true", help="run while the agent queue runs")
    ap.add_argument("--n", type=int, default=40, help="blindness sample size (default 40, 10 per arm)")
    ap.add_argument("--per-arm", type=int, help="patch-view blindness: PR cards per arm (default 10)")
    args = ap.parse_args(argv)
    if VIEW == "patch" and args.phase == "phase1":
        raise SystemExit("JUDGE_VIEW=patch applies to phase2 and blindness only")
    if args.phase == "gabarito":
        return gabarito()
    if args.phase == "phase1":
        tasks = tasks_phase1(args)
    elif args.phase == "phase2":
        tasks = tasks_phase2(args, dry=args.dry_run)
    else:
        tasks = tasks_blindness(args, dry=args.dry_run)
    if args.dry_run:
        return dry_run(args.phase, tasks)
    return execute(tasks, args.allow_concurrent)


if __name__ == "__main__":
    sys.exit(main())
