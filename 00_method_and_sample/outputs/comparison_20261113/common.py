"""Shared paths and constants of the 2026-11-13 agent comparison kit (arms S, O, T; "+" arms TP, SP).

Arms TP and SP (added 2026-11-14, pre-registered in docs/preregistro_bracos_mais_20261114.md) are T and S
with the same CLI, model and effort, and the "+" prompt (units/<unit>/prompt_plus.txt): prompt.txt with
PLUS_BLOCK inserted before 'Decide what, if anything'. Their run order is order_plus.json; order.json
(arms S, O, T) is left as it was.
"""
import json
import os
from pathlib import Path

TCC = Path("/mnt/c/Users/Antonow/Documents/tcc_v3")
OUT = TCC / "outputs/comparison_20261113"
REAL_HOME = Path("/home/antonow")
WORK = REAL_HOME / "comparison_20261113"          # WSL disk: bases, per-run checkouts, homes, builds
BASES = WORK / "base"
RUNS_WORK = WORK / "work"
BIN = WORK / "bin"
BUILD_CHECK = BIN / "build_check"
RECORDS = OUT / "runs"
LOGS = OUT / "logs"
VENV_PY = REAL_HOME / "venv/bin/python"
ANDROID_SDK = REAL_HOME / "Android/Sdk"
GRADLE_HOME = REAL_HOME / ".gradle"
KONAN_HOME = REAL_HOME / ".konan"
CLAUDE_BIN = REAL_HOME / ".local/node22/bin/claude"
CODEX_BIN = REAL_HOME / ".npm-global/bin/codex"
# Subscription credentials (never printed). Per run, only the credential file is copied into a fresh
# config directory; a refreshed token is copied back after the run (rotating refresh tokens).
CLAUDE_CRED_DIR = REAL_HOME / "agent_baseline/.claude"
CODEX_CRED_DIR = REAL_HOME / "agent_baseline/.codex"

SEED = 20261113
REPLICAS = 4
ARMS = {
    "S": {"cli": "claude", "model": "claude-sonnet-5-5", "effort": "high"},
    "O": {"cli": "codex", "model": "gpt-6.1-sol", "effort": "high"},
    "T": {"cli": "codex", "model": "gpt-6.1-terra", "effort": "high"},
    # "+" arms: the same settings as T and S, the "+" prompt, their own replica count.
    "TP": {"cli": "codex", "model": "gpt-6.1-terra", "effort": "high", "prompt_file": "prompt_plus.txt",
           "replicas": 4},
    "SP": {"cli": "claude", "model": "claude-sonnet-5-5", "effort": "high", "prompt_file": "prompt_plus.txt",
           "replicas": 2},
    # 2026-11-15, declared deviation (DESVIO_SP_20261115.md): SP was refused by Claude Code's safeguards
    # ("[reasoning_extraction]") in 8 of 9 runs; SP2 is SP with the request for a written record of the
    # stages removed (make_prompt_sp2.py writes prompt_plus_sp2.txt and appends SP2 to order_plus.json).
    # 2026-11-16, option A (docs/preregistro_opcao_a_20261116.md): SP2 completed to 4 replicas (r3, r4 added
    # to order_plus.json by port_phase/make_order_option_a.py); OP = O (Codex + gpt-6.1-sol) with SP2's prompt.
    "SP2": {"cli": "claude", "model": "claude-sonnet-5-5", "effort": "high", "prompt_file": "prompt_plus_sp2.txt",
            "replicas": 4},
    "OP": {"cli": "codex", "model": "gpt-6.1-sol", "effort": "high", "prompt_file": "prompt_plus_sp2.txt",
           "replicas": 4},
}
ORIGINAL_ARMS = ("S", "O", "T")        # order.json; complete, never re-run
PLUS_ARMS = ("TP", "SP")               # order_plus.json
PLUS_SEED_OFFSET = 1000                # order_plus.json shuffles with random.Random(SEED + 1000 + replica)
# The "+" arms' working notes: written by the agent at the repository root, kept out of patch.diff, the
# untracked list and the deliverable, and recorded beside the run as working_notes.md.
NOTES_FILE = "ANALYSIS.md"
AGENT_TIMEOUT = int(os.environ.get("AGENT_TIMEOUT", 60 * 60))   # spec §3.3: 60 min
BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", 1800))
DELIVERABLES = ("PR.md", "ISSUE.md", "NO_ACTION.md")
# Environment variables never passed to an agent (spec §3.3).
SECRET_VARS = ("GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_ORG_ID",
               "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CODEX_API_KEY", "AZURE_OPENAI_API_KEY")

PROMPT = """You are contributing to this open-source Android app as an outside contributor. RVSec, a runtime checker for how Android apps use the Java cryptography and TLS APIs, ran the APK built from the current commit and reported:

{findings}
Decide what, if anything, the maintainers should receive, and write exactly one of these files at the repository root:
- PR.md, when a code change should be proposed. Edit the source files directly (do not commit). Put the pull request's title on the first line as '# <title>', followed by the description you would submit.
- ISSUE.md, when the maintainers should be told but you should not change the code. Put the issue's title on the first line as '# <title>', followed by the issue text.
- NO_ACTION.md, when nothing should be sent to the maintainers. Explain why.

Write for the maintainers: short plain paragraphs that describe only what your change does and why. Do not use the names of the checker's rules or of compiled classes. If the repository has a pull request template, fill it in and tick only what is true. If it asks about AI use, answer that the contribution is AI-assisted. Say that the project builds only if you ran the build and it passed.

To build the app, run {build_check}. It prints the result and the end of the build log. You may search the web. Do not commit, push, open pull requests or issues, or contact anyone.
"""
# The "+" arms' block, inserted into PROMPT right before PLUS_ANCHOR (its only occurrence).
PLUS_ANCHOR = "Decide what, if anything"
PLUS_BLOCK = """Work in these steps, and keep your working notes in ANALYSIS.md at the repository root. ANALYSIS.md is for you only: it is not one of the files described below, it is not sent to the maintainers, and what you write for them should not refer to it.
1. Research what the rule requires and why, in the platform documentation and on the web.
2. Find every place in this repository that produces, stores, reads or depends on the reported value or object: callers, stored data and how it is read back, servers or peers, tests.
3. Decide whether the report is a real violation, whether it has security impact in this app, and who would be affected by a change.
4. If a change is warranted, plan the smallest change that removes what the report describes while keeping existing data, protocols and supported setups working.
5. Make the change.
6. Review your own diff. For each changed line, look for a concrete scenario through the app's own code where it breaks something: stored data that can no longer be read, a peer that can no longer connect, behaviour the user relies on. Also check that the change does not merely hide the reported call from the checker while the code keeps using the same value. If it does either, fix the change or abandon it.
7. Build with {build_check} and fix any failure.
8. Write one of the files described below.

"""
FINDING = """- Rule: {spec}
  Class: {class}
  Method: {method}
  Message: {message}
"""


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def prompt_for(unit):
    findings = "".join(FINDING.format(**row) for row in unit["rows"])
    return PROMPT.format(findings=findings, build_check=str(BUILD_CHECK))


# The full-process block (2026-11-14, user's request: the same architecture as the pipeline): written from an
# inventory of every stage and check of the library, reviewed for completeness and leaks
# (plus_prompt/mapping.md). "$unit_inputs" is replaced by plus_prompt/inputs/<unit>.txt: the published rule
# behind each report and the other repositories' maintainer responses, as the Cognalys arm received them.
# PLUS_BLOCK above is the short first block, kept for the record and no longer used.
PLUS_DIR = Path(__file__).resolve().parent / "plus_prompt"


def plus_block(unit):
    block = (PLUS_DIR / "process_block.txt").read_text(encoding="utf-8")
    assert block.count("$unit_inputs") == 1 and "{" not in block and "}" not in block
    inputs = (PLUS_DIR / "inputs" / f"{unit['unit']}.txt").read_text(encoding="utf-8").rstrip("\n") + "\n"
    block = block.replace("$unit_inputs", inputs)
    return block.rstrip("\n") + "\n\n"


def prompt_plus_for(unit):
    """prompt_for(unit) with the "+" block inserted before PLUS_ANCHOR; nothing else differs."""
    base = prompt_for(unit)
    assert base.count(PLUS_ANCHOR) == 1, "the anchor must occur exactly once in the prompt"
    at = base.index(PLUS_ANCHOR)
    return base[:at] + plus_block(unit) + base[at:]


def arm_prompt_file(arm_name):
    return ARMS[arm_name].get("prompt_file", "prompt.txt")


def arm_replicas(arm_name):
    return int(ARMS[arm_name].get("replicas", REPLICAS))


def base_dir(unit):
    owner_repo = unit["repo_url"].rstrip("/").split("github.com/")[-1].replace("/", "__")
    return BASES / f"{owner_repo}@{unit['pinned_commit'][:12]}"


def run_id(unit_id, arm, replica):
    return f"{unit_id}/{arm}_r{replica}"
