"""Phase 2 of one agent run (option A, 2026-11-16): carry the phase-1 pull request onto the base tip.

Mirrors arm C's port (cognalys.solutions.solution_applicator / base_port), with the agent as the porting stage:
1. Only a phase-1 run whose class is PR has a phase 2 (runs/<unit>/<arm>_r<r>/result.json).
2. Checkout of the unit's tip (port_phase/tips.json; WORK/tip_repos/<unit>, clone --shared, detached, no
   remote). The phase-1 patch.diff and PR.md are put beside the checkout's files as PHASE1_PATCH.diff and
   PHASE1_PR.md (excluded from every diff).
3. Deterministic step, as C's three-way merge: `git apply --3way` of the phase-1 patch. When it applies
   cleanly, the kit builds it at the tip and no agent is started (C's merge costs nothing either).
4. Otherwise the same agent (CLI, model, effort) gets port_phase/port_prompt.txt, a fresh HOME and the same
   tools as phase 1 plus `git apply`, and writes PORTED.md, ALREADY_FIXED.md or ABSTAIN.md.
5. ALREADY_FIXED is kept only when a deterministic check confirms it, as C's port_already_fixed_confirmation:
   every line the phase-1 patch adds is on the tip in that file and no line it removes is (or the patched
   files no longer exist on the tip). Unconfirmed, it counts as ABSTAIN (C: PORT_ALREADY_FIXED_UNCONFIRMED).
6. PORTED with a non-empty diff: the kit builds the final diff at the tip unless the agent built that diff.
Records runs/<unit>/<arm>_r<r>/port/: transcript, deliverable, ported.diff, build calls, tokens, result.json.
Outcome (result.json "outcome"): "ported" (+ build status), "already_fixed" (confirmed), "abstain" (incl.
unconfirmed already-fixed and PORTED with an empty diff), "failure" (timeout or no deliverable).
Exit codes as run_one.py (75 limit, 76 auth, 77 infra). Usage: run_port.py <unit> <arm> <replica>
"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import run_one as r1  # noqa: E402
from common import (AGENT_TIMEOUT, ARMS, BUILD_CHECK, CLAUDE_CRED_DIR, CODEX_CRED_DIR, OUT, REAL_HOME,  # noqa: E402
                    RECORDS, WORK, dump, load)
import build_check  # noqa: E402

PORT_DELIVERABLES = ("PORTED.md", "ALREADY_FIXED.md", "ABSTAIN.md")
PORT_WORK = WORK / "port_work"
TIP_REPOS = WORK / "tip_repos"
PROMPT_FILE = HERE / "port_prompt.txt"


def sh(*args, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, **kw)


def changed_lines(diff):
    """{path: (added, removed)} of a unified diff, content lines stripped; deleted files map to None."""
    files, cur = {}, None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            cur = line.split(" b/", 1)[-1]
            files[cur] = (set(), set())
        elif cur is None or line.startswith(("+++", "---")):
            continue
        elif line.startswith("+") and line[1:].strip():
            files[cur][0].add(line[1:].strip())
        elif line.startswith("-") and line[1:].strip():
            files[cur][1].add(line[1:].strip())
    return files


def already_fixed_confirmed(repo, diff):
    """Why the tip is known to hold the phase-1 correction, or "" (see the module doc, step 5)."""
    files = changed_lines(diff)
    present = {p for p in files if (repo / p).is_file()}
    if not present:
        return "every file the phase-1 patch edits is gone from the tip"
    for path in present:
        text = {l.strip() for l in (repo / path).read_text(errors="replace").splitlines()}
        added, removed = files[path]
        if not added <= text or removed & text:
            return ""
    return "every added line is on the tip and no removed line is"


def claude_cmd(arm, prompt):
    allowed = ["Read", "Edit", "Write", "Glob", "Grep", "WebSearch", "WebFetch", *r1.READ_ONLY_SHELL,
               "Bash(git apply:*)", f"Bash({BUILD_CHECK})", f"Bash({BUILD_CHECK}:*)"]
    return [str(r1.CLAUDE_BIN), "-p", prompt, "--model", arm["model"], "--effort", arm["effort"],
            "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
            "--allowedTools", ",".join(allowed)]


def kit_build(repo, run_dir, calls_before):
    sh(str(REAL_HOME / "venv/bin/python"), str(OUT / "build_check.py"), str(repo), "--caller=kit")
    calls = [json.loads(l) for l in (run_dir / "build_calls.jsonl").read_text().splitlines()]
    return calls[-1] if len(calls) > calls_before else {}


def main():
    unit_id, arm_name, replica = sys.argv[1], sys.argv[2], int(sys.argv[3])
    arm = ARMS[arm_name]
    phase1 = RECORDS / unit_id / f"{arm_name}_r{replica}"
    p1 = load(phase1 / "result.json")
    record = phase1 / "port"
    if (record / "result.json").is_file():
        print(json.dumps({"skip": "recorded"}))
        return 0
    if p1.get("delivery_class") != "PR":
        print(json.dumps({"skip": f"phase 1 class {p1.get('delivery_class')}"}))
        return 0
    tip = load(HERE / "tips.json")["units"][unit_id]
    run_dir = PORT_WORK / unit_id / f"{arm_name}_r{replica}"
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(record, ignore_errors=True)
    run_dir.mkdir(parents=True)
    record.mkdir(parents=True)
    repo, home = run_dir / "repo", run_dir / "home"
    home.mkdir()
    try:
        sh("git", "clone", "-q", "--shared", "--no-checkout", str(TIP_REPOS / unit_id), str(repo), check=True)
        sh("git", "-C", str(repo), "checkout", "-q", "--detach", tip["tip"], check=True)
        sh("git", "-C", str(repo), "remote", "remove", "origin", check=True)
    except subprocess.CalledProcessError as exc:
        print(json.dumps({"infra": f"checkout failed: {(exc.stderr or '')[-300:]}"}))
        shutil.rmtree(record, ignore_errors=True)
        return 77
    unit = load(OUT / "units" / unit_id / "unit.json")
    (run_dir / "build_task").write_text(unit["build_task"])
    patch = (phase1 / "patch.diff").read_text(encoding="utf-8")
    shutil.copy2(phase1 / "patch.diff", repo / "PHASE1_PATCH.diff")
    shutil.copy2(phase1 / "PR.md", repo / "PHASE1_PR.md")
    started = time.time()
    result = {"unit": unit_id, "arm": arm_name, "replica": replica, "cli": arm["cli"], "model": arm["model"],
              "effort": arm["effort"], "pinned_commit": tip["pinned_commit"], "tip": tip["tip"],
              "tip_source": tip["source"], "phase1_diff_sha": p1.get("diff_sha"), "agent": False,
              "tokens": None, "web_searches": 0, "claude_reported_cost_usd": None, "model_usage": None}
    merged = sh("git", "-C", str(repo), "apply", "--3way", "--whitespace=nowarn", "PHASE1_PATCH.diff")
    conflicted = sh("git", "-C", str(repo), "diff", "--name-only", "--diff-filter=U").stdout.strip()
    if merged.returncode == 0 and not conflicted:
        result["deterministic_merge"] = "applied"
        sh("git", "-C", str(repo), "reset", "-q")              # --3way stages; keep it a working-tree change
        decision = "PORTED"
    else:
        result["deterministic_merge"] = "conflict: " + (merged.stderr.strip()[-400:] or conflicted)
        sh("git", "-C", str(repo), "reset", "-q", "--hard", tip["tip"])
        sh("git", "-C", str(repo), "clean", "-qfd", "-e", "PHASE1_PATCH.diff", "-e", "PHASE1_PR.md")
        prompt = (PROMPT_FILE.read_text(encoding="utf-8")
                  .replace("$pinned", tip["pinned_commit"]).replace("$tip", tip["tip"])
                  .replace("$branch", tip["branch"]).replace("$patch_file", "PHASE1_PATCH.diff")
                  .replace("$pr_file", "PHASE1_PR.md").replace("$build_check", str(BUILD_CHECK)))
        result["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
        env = r1.clean_env(home)
        if arm["cli"] == "claude":
            cred_master, cred_run = CLAUDE_CRED_DIR / ".credentials.json", home / ".claude" / ".credentials.json"
            env.update({"CLAUDE_CONFIG_DIR": str(home / ".claude"), "DISABLE_AUTOUPDATER": "1",
                        "BASH_DEFAULT_TIMEOUT_MS": str(r1.BUILD_TIMEOUT_MS),
                        "BASH_MAX_TIMEOUT_MS": str(r1.BUILD_TIMEOUT_MS)})
            cmd, transcript = claude_cmd(arm, prompt), record / "stream.jsonl"
        else:
            cred_master, cred_run = CODEX_CRED_DIR / "auth.json", home / ".codex" / "auth.json"
            env["CODEX_HOME"] = str(home / ".codex")
            cmd, transcript = r1.codex_cmd(arm, prompt, repo, run_dir), record / "events.jsonl"
        r1.copy_secret(cred_master, cred_run)
        status = r1.run_proc(cmd, repo, env, AGENT_TIMEOUT, transcript, record / "stderr.txt")
        r1.sync_back(cred_run, cred_master)
        parsed = r1.parse_claude(transcript) if arm["cli"] == "claude" else r1.parse_codex(transcript)
        stderr = (record / "stderr.txt").read_text(errors="replace")[-4000:]
        present = [n for n in PORT_DELIVERABLES if (repo / n).is_file()]
        failure_text = parsed["error_text"] + ("\n" + stderr if status != "exit 0" or not parsed["has_result"] else "")
        if status != "timeout" and not present and failure_text.strip():
            for regex, verdict, code in ((r1.AUTH_RE, "auth", 76), (r1.LIMIT_RE, "limit", 75)):
                if regex.search(failure_text):
                    attempts = phase1 / f"port_attempt_{verdict}_{time.strftime('%Y%m%dT%H%M%S')}"
                    shutil.move(str(record), str(attempts))
                    shutil.rmtree(run_dir, ignore_errors=True)
                    print(json.dumps({verdict: failure_text[-400:], "retry_after": r1.retry_after(failure_text)}))
                    return code
        result.update(agent=True, status=status, tokens=parsed["tokens"], web_searches=parsed["web_searches"],
                      turns=parsed["turns"], claude_reported_cost_usd=parsed.get("claude_reported_cost_usd"),
                      model_usage=parsed.get("model_usage"), error_text=parsed["error_text"][-1000:])
        for name in present:
            shutil.copy2(repo / name, record / name)
        if status == "timeout" or len(present) != 1:
            decision = "FAILURE"
            result["invalid_reason"] = "timeout" if status == "timeout" else f"deliverables: {present}"
        else:
            decision = present[0][:-3]
    diff = build_check.diff_text(repo)
    (record / "ported.diff").write_text(diff, encoding="utf-8")
    diff_sha = hashlib.sha256(diff.encode()).hexdigest()[:16]
    calls_path = run_dir / "build_calls.jsonl"
    calls = [json.loads(l) for l in calls_path.read_text().splitlines()] if calls_path.is_file() else []
    build = None
    if decision == "PORTED" and not diff.strip():
        decision, result["invalid_reason"] = "ABSTAIN", "PORTED with an empty diff"
    if decision == "PORTED":
        same = [c for c in calls if c["diff_sha"] == diff_sha]
        build = same[-1] if same else kit_build(repo, run_dir, len(calls))
        build = {"status": build.get("status", "unknown"), "from": f"{build.get('caller')} call {build.get('call')}"}
    elif decision == "ALREADY_FIXED":
        why = already_fixed_confirmed(repo, patch)
        result["already_fixed_confirmation"] = why
        if not why:
            decision, result["model_decision"] = "ABSTAIN", "ALREADY_FIXED (unconfirmed)"
    outcome = {"PORTED": "ported", "ALREADY_FIXED": "already_fixed", "ABSTAIN": "abstain",
               "FAILURE": "failure"}[decision]
    (record / "build").mkdir(exist_ok=True)
    if calls_path.is_file():
        shutil.copy2(calls_path, record / "build" / "build_calls.jsonl")
    for tail in sorted((run_dir / "build").glob("call*.tail.txt")) if (run_dir / "build").is_dir() else []:
        shutil.copy2(tail, record / "build" / tail.name)
    result.update(outcome=outcome, ported_diff_sha=diff_sha, ported_diff_lines=len(diff.splitlines()),
                  build=build, seconds=round(time.time() - started),
                  finished_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    dump(record / "result.json", result)
    shutil.rmtree(run_dir, ignore_errors=True)
    print(json.dumps({k: result.get(k) for k in ("unit", "arm", "replica", "deterministic_merge", "outcome",
                                                 "build", "seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
