#!/bin/bash
# Dynamic validation of the published PRs, RVSec step
# (docs/preregistro_validacao_prs_20261115.md). A copy of queue.sh; only the case
# list, the default repetitions and the log differ:
#   - cases: those of pr_cases_resolved.json whose base and patched APKs were
#     built (build_status_prs.json case_status "built"), in build order
#     (accepted, open, closed_by_maintainer; pr_cases.json order within a group);
#     repository, commit and app packages (origin = app) from pr_cases_resolved.json;
#   - REPS default "1" (one repetition per case, base then patched); a second
#     repetition, only for the cases inconclusive after the first, is started
#     later with REPS=2 ONLY_CASES="case1 case2 ..." after every case has rep 1;
#   - runs/<case>/{base,patched}_r<rep>/, log logs/queue_prs.log.
# Everything else is queue.sh unchanged: `cognalys rvsec-run` on the single APK,
# --tools monkey:fast,ape:sata --timeout 600, jca_android, dexlib2 instrumentation,
# a clean emulator each run; refuses to start while another rvsec-run, RVSec
# container, emulator or RVSec queue runs; stops below 8 GB free on C:.
#
# Launch (not started by the build step):
#   tr -d '\r' < /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/queue_prs.sh > /tmp/rvsec_queue_dynval_prs.sh
#   cd ~ && setsid nohup bash /tmp/rvsec_queue_dynval_prs.sh > /dev/null 2>&1 < /dev/null &
# Progress: outputs/dynamic_validation_20261114/logs/queue_prs.log
# Classify: python outputs/dynamic_validation_20261114/classify_prs.py
#
# Resumable: a run with runs/<case>/<variant>_r<rep>/.done (or .unexplorable)
# is skipped; an interrupted one is started again from scratch.
set -u
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
T=/mnt/c/Users/Antonow/Documents/tcc_v3
WORK=$HOME/dynval_runs
IMAGE=phtcosta/rvandroid:0.9.5
MIN_FREE_GB=${MIN_FREE_GB:-8}   # 2026-11-15: 8 GB, the RVSec queues' threshold (CLAUDE.md); 12 left too little room with C: at 11 GB
REPS=${REPS:-"1"}
ONLY_CASES=${ONLY_CASES:-}
VARIANTS="base patched"
mkdir -p "$V/logs" "$V/runs" "$WORK"
LOG=$V/logs/queue_prs.log
exec >> "$LOG" 2>&1

# case|repository|commit both APKs were built from|the app's own packages (origin = app)
# Built cases only, in build order, from build_status_prs.json and pr_cases_resolved.json.
CASES=$("$HOME/venv/bin/python" - "$V" "$ONLY_CASES" <<'EOF'
import json, sys
from pathlib import Path
v = Path(sys.argv[1]); only = set(sys.argv[2].split())
status = json.loads((v / "build_status_prs.json").read_text(encoding="utf-8"))
resolved = json.loads((v / "pr_cases_resolved.json").read_text(encoding="utf-8"))
# Deviation 5 (2026-11-17): the old-message cases of pr_cases_oldmsg.json (build order after the 28).
if (v / "pr_cases_oldmsg.json").is_file():
    resolved["cases"] += json.loads((v / "pr_cases_oldmsg.json").read_text(encoding="utf-8"))["cases"]
# Deviation 6 (2026-11-18): the cases of pr_cases_desvio6.json; its `reuse_apks` entry (bitbanana_79, rep 4)
# runs on the APKs already in apks/<case>/ (built 14/11), with queue.sh's repository, commit and packages.
if (v / "pr_cases_desvio6.json").is_file():
    resolved["cases"] += json.loads((v / "pr_cases_desvio6.json").read_text(encoding="utf-8"))["cases"]
for case in sorted(resolved["cases"], key=lambda c: c["build_order"]):
    name = case["case"]
    if only and name not in only:
        continue
    reuse = case.get("reuse_apks") and all(list((v / "apks" / name / x).glob("*.apk")) for x in ("base", "patched"))
    if (status.get(name, {}).get("case_status") != "built" and not reuse) or not case.get("app_packages"):
        continue
    print("|".join([name, case["repo_url"], case["build_commit"], ";".join(case["app_packages"])]))
EOF
)

free_gb() { df -BG --output=avail /mnt/c | tail -1 | tr -dc 0-9; }

# Anything else driving RVSec or an emulator: another rvsec-run, an RVSec
# container, an emulator process, or another RVSec queue script.
others_running() {
  local pid cmd found=""
  for pid in $(pgrep -f "[r]vsec-run|[r]vsec_queue|[r]v-experiment|qemu-system|[e]mulator -avd|[e]mulator64"); do
    [ "$pid" = "$$" ] || [ "$pid" = "$PPID" ] && continue
    cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null)
    case "$cmd" in ""|*"$0"*) continue ;; esac
    found="$found\n   pid $pid: $(echo "$cmd" | cut -c1-200)"
  done
  if command -v docker >/dev/null; then
    local containers
    containers=$(docker ps --filter "ancestor=$IMAGE" --format '{{.Names}}' 2>/dev/null;
                 docker ps --format '{{.Names}}' 2>/dev/null | grep '^rvsec_')
    [ -n "$containers" ] && found="$found\n   containers: $(echo "$containers" | sort -u | tr '\n' ' ')"
  fi
  [ -n "$found" ] && { echo -e "$found"; return 0; }
  return 1
}

# The RVSec queues' own end test (outputs/rvsec_auto/queue.sh: rvsec_finished).
UNEXPLORABLE="RVSec could not instrument it|RVSec cannot install it|RVSec not run: no APK"
rvsec_finished() {
  grep -q "report(s) ->" "$1" 2>/dev/null && return 0
  grep -q "RVSec wrote no reports" "$1" 2>/dev/null && ! grep -qE "$UNEXPLORABLE" "$1"
}

# Keeps Windows from idle-sleeping (outputs/rvsec_auto/run_settle.sh: start_keep_awake).
start_keep_awake() {
  local ps=/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
  local script=$T/outputs/rvsec_auto/keep_awake.ps1 win
  [ -f "$script" ] && [ -x "$ps" ] || return 0
  win=$(wslpath -w "$script") || return 0
  "$ps" -NoProfile -NonInteractive -Command \
    "Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File','$win'" \
    > /dev/null 2>&1 < /dev/null || echo "== keep_awake.ps1 could not be started"
}

# The RVSec lines of a task's logcat, under the logcat's own name, so that
# cognalys.integrations.rvsec.logcat_rows / reached_methods read them as is.
keep_results() {  # $1 = rvsec-run work dir, $2 = destination
  local src=$1 dst=$2 f rel
  [ -d "$src" ] || return
  mkdir -p "$dst"
  (cd "$src" && find . \( -name tasks.json -o -name errors.csv -o -name 'instrument_*.json' \
      -o -name rvsec.log \) -type f) | while read -r rel; do
    mkdir -p "$dst/$(dirname "$rel")"; cp "$src/$rel" "$dst/$rel"
  done
  (cd "$src" && find . -name '*.logcat' -type f) | while read -r rel; do
    mkdir -p "$dst/$(dirname "$rel")"
    # Each executed method once (its first RVSEC-COV line); every report line.
    awk '/RVSEC-COV/ { k = substr($0, index($0, "RVSEC-COV")); if (k in s) next; s[k] = 1 } /RVSEC/' \
      "$src/$rel" > "$dst/$rel" || true
  done
}

# One queue of ours at a time.
exec 9> "$WORK/.queue.lock"
if ! flock -n 9; then echo "== $(date -Iseconds) another dynamic-validation queue holds $WORK/.queue.lock; not started"; exit 1; fi
if others=$(others_running); then
  echo "== $(date -Iseconds) refused: RVSec or an emulator is already running:$others"; exit 1
fi
cd "$T" || exit 1
source ~/venv/bin/activate
start_keep_awake
echo "== $(date -Iseconds) dynamic-validation PR queue started (pid $$, REPS '$REPS', $(echo "$CASES" | grep -c "|") built cases, C: $(free_gb) GB free)"

for REP in $REPS; do
  echo "$CASES" | while IFS='|' read -r C REPO COMMIT PKGS; do
    [ -n "$C" ] || continue
    for VAR in $VARIANTS; do
      APKS=$V/apks/$C/$VAR
      if ! ls "$V/apks/$C/base/"*.apk >/dev/null 2>&1 || ! ls "$V/apks/$C/patched/"*.apk >/dev/null 2>&1; then
        [ "$VAR" = base ] && echo "   $C: base or patched APK missing, skipped"
        continue
      fi
      O=$V/runs/$C/${VAR}_r$REP
      if [ -f "$O/.done" ] || [ -f "$O/.unexplorable" ]; then continue; fi
      F=$(free_gb)
      if [ -z "$F" ] || [ "$F" -lt "$MIN_FREE_GB" ]; then
        echo "== $(date -Iseconds) stopped: C: has ${F:-unknown} GB free (< $MIN_FREE_GB)"; exit 3
      fi
      if others=$(others_running); then
        echo "== $(date -Iseconds) stopped: something else started driving RVSec or an emulator:$others"; exit 3
      fi
      rm -rf "$O" "$WORK/$C/${VAR}_r$REP"
      mkdir -p "$O" "$WORK/$C/${VAR}_r$REP"
      APK=$(basename "$(ls "$APKS"/*.apk | head -1)")
      {
        echo "apk,repo_url,commit,package"
        echo "$APK,$REPO,$COMMIT,$PKGS"
      } > "$O/sources.csv"
      sha256sum "$APKS/$APK" > "$O/apk.sha256"
      echo "== $(date -Iseconds) $C $VAR rep $REP: rvsec-run $APK"
      START=$(date +%s)
      cognalys rvsec-run "$APKS" --sources "$O/sources.csv" --tools monkey:fast,ape:sata --timeout 600 \
        --work-dir "$WORK/$C/${VAR}_r$REP" --rvsec-home ~/external/rvsec \
        -o "$O/findings_all.jsonl" > "$O/rvsec_run.out" 2>&1 < /dev/null
      echo "$(( $(date +%s) - START ))" > "$O/seconds"
      keep_results "$WORK/$C/${VAR}_r$REP" "$O/rvsec"
      if rvsec_finished "$O/rvsec_run.out"; then
        date -Iseconds > "$O/.done"
        echo "   done in $(cat "$O/seconds") s: $(grep -E '^task ' "$O/rvsec_run.out" | cut -c1-200 | tr '\n' ';')"
      elif grep -qE "$UNEXPLORABLE" "$O/rvsec_run.out"; then
        grep -E "$UNEXPLORABLE" "$O/rvsec_run.out" > "$O/.unexplorable"
        echo "   RVSec could not explore it: $(head -1 "$O/.unexplorable" | cut -c1-240)"
      else
        echo "   not finished (rerun the queue to retry): $(tail -2 "$O/rvsec_run.out" | tr '\n' ' ' | cut -c1-240)"
      fi
    done
  done
  [ $? -eq 3 ] && exit 3
done
echo "== $(date -Iseconds) dynamic-validation PR queue finished; classify with:"
echo "   python $V/classify_prs.py"
