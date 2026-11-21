"""One run of one agent arm (S, O, T, or the "+" arms TP, SP) on one unit and replica.

Fresh checkout (`clone --shared` of the unit's base, detached at the pinned
commit, no remote), fresh HOME and CLI config directory holding only a copy of
the subscription credential, no secret variables, the spec's prompt. Records,
under outputs/comparison_20261113/runs/<unit>/<arm>_r<r>/: the transcript
(stream.jsonl for Claude Code, events.jsonl for Codex), stderr, the deliverable
(PR.md / ISSUE.md / NO_ACTION.md), patch.diff (new files included, the three
deliverables and build outputs excluded), untracked.txt, the agent's
build_check calls, the kit's build of the final diff when the agent did not
build that exact diff, tokens, web searches, URLs the tools touched, the
"consulted upstream" flag, seconds, and result.json last. Then deletes the
run's work directory (local only).

Exit codes: 0 recorded; 75 subscription limit or overload (nothing recorded,
retry later; stdout has {"retry_after": s}); 76 authentication failure;
77 infrastructure error (nothing recorded).

The arm's prompt is units/<unit>/<prompt_file> (common.arm_prompt_file: prompt.txt for S, O, T;
prompt_plus.txt for TP, SP); result.json records which file and its sha256. The "+" prompt asks for
working notes in ANALYSIS.md (common.NOTES_FILE) at the repository root: build_check.diff_text leaves it
out of patch.diff (and of the diff_sha the build calls record), it is left out of untracked.txt, and it
is copied to working_notes.md beside the run (not a deliverable; the judges' cards read PR.md /
ISSUE.md / NO_ACTION.md and patch.diff only).

Usage: run_one.py <unit> <arm> <replica> [--dry-run]
--dry-run prints the agent command (prompt shown as its file and sha256) and the environment keys the
run would set, without a checkout, a credential copy or any file written.
"""
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

sys.path.insert(0, str(Path(__file__).parent))
from common import (AGENT_TIMEOUT, ARMS, BUILD_CHECK, CLAUDE_BIN, CLAUDE_CRED_DIR, CODEX_BIN,  # noqa: E402
                    CODEX_CRED_DIR, DELIVERABLES, GRADLE_HOME, KONAN_HOME, NOTES_FILE, OUT, REAL_HOME, RECORDS,
                    RUNS_WORK, SECRET_VARS, WORK, arm_prompt_file, base_dir, dump, load)
import build_check  # noqa: E402

READ_ONLY_SHELL = ["Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(grep:*)", "Bash(rg:*)",
                   "Bash(find:*)", "Bash(wc:*)", "Bash(sed -n:*)", "Bash(git diff:*)", "Bash(git log:*)",
                   "Bash(git show:*)", "Bash(git status:*)", "Bash(git grep:*)", "Bash(git blame:*)",
                   "Bash(pwd)", "Bash(tree:*)", "Bash(file:*)"]
LIMIT_RE = re.compile(r"usage limit|limit reached|hit your (usage )?limit|rate[ _-]?limit|too many requests|\b429\b|"
                      r"quota|overloaded|\b529\b|try again later|limit will reset|resets? at", re.I)
AUTH_RE = re.compile(r"\b401\b|unauthori[sz]ed|not logged in|please (log ?in|run .*login)|invalid api key|"
                     r"authentication (failed|error)|token (has )?expired|refresh token", re.I)
UPSTREAM_RE = r"github\.com/{owner}/{repo}/(pull|pulls|issues|commit|commits|compare)\b"
URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")


def sh(*args, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, **kw)


def clean_env(home):
    env = {k: v for k, v in os.environ.items()
           if k not in SECRET_VARS and not re.search(r"TOKEN|API_KEY|SECRET|PASSWORD", k)
           and not k.startswith(("VIRTUAL_ENV", "CLAUDE", "CODEX", "ANTHROPIC", "OPENAI"))}
    env.update({"HOME": str(home), "GRADLE_USER_HOME": str(GRADLE_HOME), "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": f"{REAL_HOME}/.local/node22/bin:{REAL_HOME}/.npm-global/bin:/usr/local/sbin:"
                        "/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "ANDROID_HOME": str(REAL_HOME / "Android/Sdk"), "ANDROID_SDK_ROOT": str(REAL_HOME / "Android/Sdk")})
    if KONAN_HOME.is_dir():
        env["KONAN_DATA_DIR"] = str(KONAN_HOME)
    return env


def copy_secret(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    os.chmod(dst, 0o600)


def sync_back(run_copy, master):
    """A refreshed subscription token goes back to the master copy (refresh tokens rotate)."""
    try:
        if run_copy.is_file() and run_copy.read_bytes() != master.read_bytes() \
                and run_copy.stat().st_mtime > master.stat().st_mtime:
            tmp = master.with_name(master.name + ".sync")
            shutil.copy2(run_copy, tmp)
            os.chmod(tmp, 0o600)
            tmp.replace(master)
            return True
    except OSError:
        pass
    return False


def run_proc(cmd, cwd, env, timeout, stdout_path, stderr_path):
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        status = None
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
                time.sleep(3)
    return status


def claude_cmd(arm, prompt):
    allowed = ["Read", "Edit", "Write", "Glob", "Grep", "WebSearch", "WebFetch", *READ_ONLY_SHELL,
               f"Bash({BUILD_CHECK})", f"Bash({BUILD_CHECK}:*)"]
    return [str(CLAUDE_BIN), "-p", prompt, "--model", arm["model"], "--effort", arm["effort"],
            "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits",
            "--allowedTools", ",".join(allowed)]


def codex_cmd(arm, prompt, repo, run_dir):
    cmd = [str(CODEX_BIN), "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
           "-m", arm["model"], "-c", f'model_reasoning_effort="{arm["effort"]}"', "-s", "workspace-write",
           "-c", "sandbox_workspace_write.network_access=true", "-c", 'web_search="live"',
           "--add-dir", str(run_dir), "--add-dir", str(GRADLE_HOME)]
    if KONAN_HOME.is_dir():
        cmd += ["--add-dir", str(KONAN_HOME)]
    return cmd + ["-C", str(repo), "-o", str(run_dir / "last_message.txt"), prompt]


def parse_claude(path):
    events, result, tool_inputs, searches = [], {}, [], 0
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        events.append(e)
        if e.get("type") == "result":
            result = e
        if e.get("type") == "assistant":
            for block in (e.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") in ("tool_use", "server_tool_use"):
                    tool_inputs.append({"tool": block.get("name"), "input": block.get("input")})
                    if block.get("name") in ("WebSearch", "web_search"):
                        searches += 1
    usage = result.get("usage") or {}
    server = usage.get("server_tool_use") or {}
    tokens = {"input_tokens": int(usage.get("input_tokens") or 0),
              "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens") or 0),
              "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
              "output_tokens": int(usage.get("output_tokens") or 0)}
    error_text = ""
    if not result or result.get("is_error") or result.get("subtype") != "success":
        error_text = json.dumps(result)[-2000:] if result else ""
    return {"tokens": tokens, "web_searches": max(searches, int(server.get("web_search_requests") or 0)),
            "web_fetches": sum(1 for t in tool_inputs if t["tool"] == "WebFetch"),
            "turns": result.get("num_turns"), "claude_reported_cost_usd": result.get("total_cost_usd"),
            "model_usage": result.get("modelUsage"), "tool_inputs": tool_inputs, "error_text": error_text,
            "has_result": bool(result)}


def parse_codex(path):
    tokens = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    turns, tool_inputs, searches, errors = 0, [], 0, []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        kind = e.get("type")
        if kind == "turn.completed":
            turns += 1
            for k in tokens:
                tokens[k] += int((e.get("usage") or {}).get(k) or 0)
        elif kind in ("error", "turn.failed"):
            errors.append(json.dumps(e)[-1000:])
        item = e.get("item") or {}
        if kind == "item.completed" and item.get("type") not in (None, "agent_message", "reasoning"):
            tool_inputs.append({"tool": item.get("type"),
                                "input": {k: v for k, v in item.items() if k not in ("aggregated_output", "id")}})
            if item.get("type") == "web_search":
                searches += 1
    return {"tokens": tokens, "web_searches": searches, "turns": turns, "tool_inputs": tool_inputs,
            "error_text": " | ".join(errors)[-3000:], "has_result": turns > 0}


def retry_after(text):
    m = re.search(r"\|(\d{10})\b", text)              # Claude: "...usage limit reached|<epoch>"
    if m:
        return max(300, min(6 * 3600, int(m.group(1)) - int(time.time()) + 120))
    if re.search(r"overloaded|\b529\b", text, re.I):
        return 600
    return 1800


def baseline_build(unit):
    """Build of the unpatched commit, once per unit (a failure the project already had is not the patch's)."""
    path = OUT / "baseline_builds" / f"{unit['unit']}.json"
    if path.is_file():
        return load(path)
    run_dir = WORK / "baseline" / unit["unit"]
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    repo = run_dir / "repo"
    sh("git", "clone", "-q", "--shared", "--no-checkout", str(base_dir(unit)), str(repo), check=True)
    sh("git", "-C", str(repo), "checkout", "-q", "--detach", unit["pinned_commit"], check=True)
    (run_dir / "build_task").write_text(unit["build_task"])
    sh(str(REAL_HOME / "venv/bin/python"), str(OUT / "build_check.py"), str(repo), "--caller=kit")
    calls = [json.loads(l) for l in (run_dir / "build_calls.jsonl").read_text().splitlines()]
    result = dict(calls[-1], unit=unit["unit"])
    tail = run_dir / "build" / f"call{calls[-1]['call']}.tail.txt"
    if tail.is_file():
        result["log_tail"] = tail.read_text(errors="replace")[-6000:]
    dump(path, result)
    shutil.rmtree(run_dir, ignore_errors=True)
    return result


def agent_setup(arm, prompt, repo, run_dir, home, record):
    """(cmd, transcript, env additions, credential master, credential copy) of one run of ``arm``."""
    if arm["cli"] == "claude":
        cred_master, cred_run = CLAUDE_CRED_DIR / ".credentials.json", home / ".claude" / ".credentials.json"
        extra = {"CLAUDE_CONFIG_DIR": str(home / ".claude"), "DISABLE_AUTOUPDATER": "1",
                 "BASH_DEFAULT_TIMEOUT_MS": str(BUILD_TIMEOUT_MS), "BASH_MAX_TIMEOUT_MS": str(BUILD_TIMEOUT_MS)}
        return claude_cmd(arm, prompt), record / "stream.jsonl", extra, cred_master, cred_run
    cred_master, cred_run = CODEX_CRED_DIR / "auth.json", home / ".codex" / "auth.json"
    return (codex_cmd(arm, prompt, repo, run_dir), record / "events.jsonl", {"CODEX_HOME": str(home / ".codex")},
            cred_master, cred_run)


def dry_run(unit_id, arm_name, replica, arm, prompt_file, prompt):
    run_dir = RUNS_WORK / unit_id / f"{arm_name}_r{replica}"
    record = RECORDS / unit_id / f"{arm_name}_r{replica}"
    cmd, transcript, extra, _master, cred_run = agent_setup(arm, prompt, run_dir / "repo", run_dir,
                                                            run_dir / "home", record)
    shown = [f"<{prompt_file} sha256={hashlib.sha256(prompt.encode()).hexdigest()[:16]} chars={len(prompt)}>"
             if part == prompt else part for part in cmd]
    print(json.dumps({"unit": unit_id, "arm": arm_name, "replica": replica, "model": arm["model"],
                      "effort": arm["effort"], "prompt_file": prompt_file, "recorded": (record / "result.json").is_file(),
                      "cwd": str(run_dir / "repo"), "transcript": str(transcript), "timeout_s": AGENT_TIMEOUT,
                      "credential_copy": str(cred_run), "env_set": sorted(["HOME", "PATH", *extra]),
                      "cmd": shown}, indent=1))
    return 0


def main():
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    unit_id, arm_name, replica = args[0], args[1], int(args[2])
    arm = ARMS[arm_name]
    unit = load(OUT / "units" / unit_id / "unit.json")
    prompt_file = arm_prompt_file(arm_name)
    prompt = (OUT / "units" / unit_id / prompt_file).read_text(encoding="utf-8")
    if "--dry-run" in sys.argv:
        return dry_run(unit_id, arm_name, replica, arm, prompt_file, prompt)
    record = RECORDS / unit_id / f"{arm_name}_r{replica}"
    if (record / "result.json").is_file():
        print(json.dumps({"skip": "recorded"}))
        return 0
    run_dir = RUNS_WORK / unit_id / f"{arm_name}_r{replica}"
    shutil.rmtree(run_dir, ignore_errors=True)
    shutil.rmtree(record, ignore_errors=True)
    run_dir.mkdir(parents=True)
    record.mkdir(parents=True)
    repo, home = run_dir / "repo", run_dir / "home"
    home.mkdir()
    sha = unit["pinned_commit"]
    try:
        sh("git", "clone", "-q", "--shared", "--no-checkout", str(base_dir(unit)), str(repo), check=True)
        sh("git", "-C", str(repo), "checkout", "-q", "--detach", sha, check=True)
        sh("git", "-C", str(repo), "remote", "remove", "origin", check=True)
        sh("git", "-C", str(repo), "branch", "-D", "pinned")
    except subprocess.CalledProcessError as exc:
        print(json.dumps({"infra": f"checkout failed: {(exc.stderr or '')[-300:]}"}))
        shutil.rmtree(record, ignore_errors=True)
        return 77
    (run_dir / "build_task").write_text(unit["build_task"])
    env = clean_env(home)
    cmd, transcript, extra, cred_master, cred_run = agent_setup(arm, prompt, repo, run_dir, home, record)
    copy_secret(cred_master, cred_run)
    env.update(extra)
    started = time.time()
    status = run_proc(cmd, repo, env, AGENT_TIMEOUT, transcript, record / "stderr.txt")
    seconds = round(time.time() - started)
    synced = sync_back(cred_run, cred_master)
    parsed = parse_claude(transcript) if arm["cli"] == "claude" else parse_codex(transcript)
    stderr = (record / "stderr.txt").read_text(errors="replace")[-4000:]
    if (run_dir / "last_message.txt").is_file():
        shutil.copy2(run_dir / "last_message.txt", record / "last_message.txt")
    failure_text = parsed["error_text"] + ("\n" + stderr if status != "exit 0" or not parsed["has_result"] else "")
    completed = status == "exit 0" and any((repo / n).is_file() for n in DELIVERABLES)
    if status != "timeout" and not completed and failure_text.strip():
        verdict = None
        if AUTH_RE.search(failure_text):
            verdict, code = "auth", 76
        elif LIMIT_RE.search(failure_text):
            verdict, code = "limit", 75
        if verdict:
            attempts = RECORDS / unit_id / "attempts" / f"{arm_name}_r{replica}_{verdict}_{time.strftime('%Y%m%dT%H%M%S')}"
            attempts.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(record), str(attempts))
            shutil.rmtree(run_dir, ignore_errors=True)
            print(json.dumps({verdict: failure_text[-400:], "retry_after": retry_after(failure_text),
                              "attempt_dir": str(attempts)}))
            return code
    if not parsed["has_result"] and seconds < 30 and status != "timeout":
        attempts = RECORDS / unit_id / "attempts" / f"{arm_name}_r{replica}_infra_{time.strftime('%Y%m%dT%H%M%S')}"
        attempts.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(record), str(attempts))
        shutil.rmtree(run_dir, ignore_errors=True)
        print(json.dumps({"infra": f"{status}; no result after {seconds} s: {failure_text[-300:]}"}))
        return 77
    present = [n for n in DELIVERABLES if (repo / n).is_file()]
    for name in present:
        shutil.copy2(repo / name, record / name)
    diff = build_check.diff_text(repo)
    (record / "patch.diff").write_text(diff, encoding="utf-8")
    diff_sha = hashlib.sha256(diff.encode()).hexdigest()[:16]
    notes = repo / NOTES_FILE
    notes_bytes = None
    if notes.is_file() and not sh("git", "-C", str(repo), "ls-files", "--error-unmatch", NOTES_FILE).returncode == 0:
        shutil.copy2(notes, record / "working_notes.md")
        notes_bytes = notes.stat().st_size
    untracked = [u for u in sh("git", "-C", str(repo), "ls-files", "--others", "--exclude-standard").stdout.split()
                 if u not in DELIVERABLES and u != NOTES_FILE]
    (record / "untracked.txt").write_text("\n".join(untracked) + ("\n" if untracked else ""))
    if len(present) != 1:
        decision = "invalid"
        delivery_class = "FALHA"
        why = "no deliverable" if not present else "more than one deliverable"
    else:
        decision = {"PR.md": "pr", "ISSUE.md": "issue", "NO_ACTION.md": "no_action"}[present[0]]
        delivery_class = {"pr": "PR", "issue": "ISSUE", "no_action": "NENHUMA AÇÃO"}[decision]
        why = ""
        if decision == "pr" and not diff.strip():
            delivery_class, why = "FALHA", "PR.md with an empty diff"
    if status == "timeout":
        delivery_class, why = "FALHA", "timeout" + (f"; {why}" if why else "")
    calls_path = run_dir / "build_calls.jsonl"
    agent_calls = [json.loads(l) for l in calls_path.read_text().splitlines()] if calls_path.is_file() else []
    build = {"agent_calls": len(agent_calls), "agent_last_status": agent_calls[-1]["status"] if agent_calls else None}
    if delivery_class == "PR":
        same = [c for c in agent_calls if c["diff_sha"] == diff_sha]
        if same:
            build.update(final_status=same[-1]["status"], final_from=f"agent call {same[-1]['call']}")
        else:
            sh(str(REAL_HOME / "venv/bin/python"), str(OUT / "build_check.py"), str(repo), "--caller=kit")
            kit = [json.loads(l) for l in calls_path.read_text().splitlines()][-1] if calls_path.is_file() else {}
            build.update(final_status=kit.get("status", "unknown"), final_from=f"kit call {kit.get('call')}")
        if build["final_status"] != "success":
            base = baseline_build(unit)
            build["baseline_status"] = base.get("status")
    (record / "build").mkdir(exist_ok=True)
    if calls_path.is_file():
        shutil.copy2(calls_path, record / "build" / "build_calls.jsonl")
    for tail in sorted((run_dir / "build").glob("call*.tail.txt")) if (run_dir / "build").is_dir() else []:
        shutil.copy2(tail, record / "build" / tail.name)
    owner, name = unit["repo_url"].rstrip("/").split("github.com/")[-1].split("/")[:2]
    tool_text = json.dumps(parsed["tool_inputs"])
    urls = sorted(set(URL_RE.findall(tool_text)))
    upstream = sorted({u for u in urls if re.search(UPSTREAM_RE.format(owner=re.escape(owner), repo=re.escape(name)),
                                                     u, re.I)})
    result = {"unit": unit_id, "arm": arm_name, "replica": replica, "cli": arm["cli"], "model": arm["model"],
              "effort": arm["effort"], "prompt_file": prompt_file,
              "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "working_notes_bytes": notes_bytes,
              "pinned_commit": sha, "status": status, "seconds": seconds,
              "decision": decision, "delivery_class": delivery_class, "invalid_reason": why,
              "deliverables": present, "diff_sha": diff_sha, "diff_lines": len(diff.splitlines()),
              "files_changed": sum(1 for l in diff.splitlines() if l.startswith("diff --git")),
              "untracked": untracked, "build": build, "turns": parsed["turns"], "tokens": parsed["tokens"],
              "web_searches": parsed["web_searches"], "web_fetches": parsed.get("web_fetches"),
              "claude_reported_cost_usd": parsed.get("claude_reported_cost_usd"),
              "model_usage": parsed.get("model_usage"), "tool_calls": len(parsed["tool_inputs"]),
              "urls_touched": urls[:200], "consulted_upstream": bool(upstream), "upstream_urls": upstream,
              "credential_refreshed": synced, "error_text": parsed["error_text"][-1000:],
              "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    dump(record / "result.json", result)
    shutil.rmtree(run_dir, ignore_errors=True)
    print(json.dumps({k: result[k] for k in ("unit", "arm", "replica", "status", "seconds", "delivery_class",
                                             "diff_lines", "build", "web_searches", "consulted_upstream")}))
    return 0


BUILD_TIMEOUT_MS = 40 * 60 * 1000

if __name__ == "__main__":
    sys.exit(main())
