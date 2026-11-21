#!/bin/bash
# ORIGINAL-VERSION VARIANT (generated from queue_prs_origin_tools.sh by sed, 2026-11-19): reruns a case with the RVSec
# image (IMAGE), codes table (RVSEC_HOME_DIR), explorers (TOOLS) and budget (TIMEOUT) of the campaign that produced
# its original report, so a missing violation is not an artefact of a later rule set. Runs go to runs_version/<tag>/.
# Launch: tr -d '\r' < this > /tmp/rvsec_queue_dynval_origin.sh; TOOLS=... TIMEOUT=... ONLY_CASES=... setsid nohup bash ...
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
# --tools "$TOOLS" --timeout "$TIMEOUT", jca_android, dexlib2 instrumentation,
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
IMAGE=${IMAGE:?set IMAGE to the RVSec image of the original campaign}
RVSEC_HOME_DIR=${RVSEC_HOME_DIR:?set RVSEC_HOME_DIR to a checkout (or codes-only tree) of that campaign}
TAG=${IMAGE##*:}
MIN_FREE_GB=${MIN_FREE_GB:-8}   # 2026-11-15: 8 GB, the RVSec queues' threshold (CLAUDE.md); 12 left too little room with C: at 11 GB
REPS=${REPS:-"1"}
TOOLS=${TOOLS:?set TOOLS to the explorers that produced the original report}
TIMEOUT=${TIMEOUT:?set TIMEOUT to the original budget}
ONLY_CASES=${ONLY_CASES:-}
VARIANTS="base patched"
mkdir -p "$V/logs" "$V/runs_version/$TAG" "$WORK"
LOG=$V/logs/queue_prs_version.log
exec >> "$LOG" 2>&1

# case|repository|commit both APKs were built from|the app's own packages (origin = app)
# Built cases only, in build order, from build_status_prs.json and pr_cases_resolved.json.
CASES=$("$HOME/venv/bin/python" - "$V" "$ONLY_CASES" <<'EOF'
import json, sys
from pathlib import Path
v = Path(sys.argv[1]); only = set(sys.argv[2].split())
status = json.loads((v / "build_status_prs.json").read_text(encoding="utf-8"))
cases = {}
for name in ("pr_cases_resolved.json", "pr_cases_oldmsg.json", "pr_cases_desvio6.json"):
    path = v / name
    if path.is_file():
        for case in json.loads(path.read_text(encoding="utf-8"))["cases"]:
            if case.get("repo_url") and case.get("build_commit"):
                cases[case["case"]] = case
for case in sorted(cases.values(), key=lambda c: c.get("build_order") or 0):
    name = case["case"]
    if only and name not in only:
        continue
    # Cases built for the 14/11 validation (build_status.json) count too; the APKs' presence is checked per run.
    if not case.get("app_packages"):
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
echo "== $(date -Iseconds) dynamic-validation PR queue (image $IMAGE, tools $TOOLS, $TIMEOUT s) started (pid $$, REPS '$REPS', $(echo "$CASES" | grep -c "|") built cases, C: $(free_gb) GB free)"

for REP in $REPS; do
  echo "$CASES" | while IFS='|' read -r C REPO COMMIT PKGS; do
    [ -n "$C" ] || continue
    for VAR in $VARIANTS; do
      APKS=$V/apks/$C/$VAR
      if ! ls "$V/apks/$C/base/"*.apk >/dev/null 2>&1 || ! ls "$V/apks/$C/patched/"*.apk >/dev/null 2>&1; then
        [ "$VAR" = base ] && echo "   $C: base or patched APK missing, skipped"
        continue
      fi
      O=$V/runs_version/$TAG/$C/${VAR}_r$REP
      if [ -f "$O/.done" ] || [ -f "$O/.unexplorable" ]; then continue; fi
      F=$(free_gb)
      if [ -z "$F" ] || [ "$F" -lt "$MIN_FREE_GB" ]; then
        echo "== $(date -Iseconds) stopped: C: has ${F:-unknown} GB free (< $MIN_FREE_GB)"; exit 3
      fi
      if others=$(others_running); then
        echo "== $(date -Iseconds) stopped: something else started driving RVSec or an emulator:$others"; exit 3
      fi
      rm -rf "$O" "$WORK/version_${TAG}_$C/${VAR}_r$REP"
      mkdir -p "$O" "$WORK/version_${TAG}_$C/${VAR}_r$REP"
      APK=$(basename "$(ls "$APKS"/*.apk | head -1)")
      {
        echo "apk,repo_url,commit,package"
        echo "$APK,$REPO,$COMMIT,$PKGS"
      } > "$O/sources.csv"
      sha256sum "$APKS/$APK" > "$O/apk.sha256"
      echo "== $(date -Iseconds) $C $VAR rep $REP: rvsec-run $APK"
      START=$(date +%s)
      cognalys rvsec-run "$APKS" --sources "$O/sources.csv" --tools "$TOOLS" --timeout "$TIMEOUT" \
        --work-dir "$WORK/version_${TAG}_$C/${VAR}_r$REP" --rvsec-home "$RVSEC_HOME_DIR" --image "$IMAGE" \
        -o "$O/findings_all.jsonl" > "$O/rvsec_run.out" 2>&1 < /dev/null
      echo "$(( $(date +%s) - START ))" > "$O/seconds"
      keep_results "$WORK/version_${TAG}_$C/${VAR}_r$REP" "$O/rvsec"
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
