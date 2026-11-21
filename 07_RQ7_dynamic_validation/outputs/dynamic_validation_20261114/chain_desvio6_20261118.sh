#!/bin/bash
# Deviation 6 (2026-11-18, docs/preregistro_validacao_prs_20261115.md, "Desvio declarado (2026-11-18)"):
#   1. builds the six A cases of pr_cases_desvio6.json with tools/build_prs.sh unchanged in recipe
#      (CASEFILE=build/prs_build_cases_desvio6.txt); Gradle may run beside the app queue's `cognalys run`
#      (no emulator), build_prs.sh waits for any other Gradle build;
#   2. waits until no other RVSec queue, rvsec-run or emulator runs (one lane; queue_prs.sh's guard refuses
#      otherwise), then runs queue_prs.sh unchanged in configuration:
#        rep 1 of the built A cases; rep 3 of reader_47 intertune_96 yourlocalweather_213 owncloudnewsreader_196;
#        rep 4 of bitbanana_79 (14/11 APKs); rep 2 of the A cases inconclusive or whose base did not reproduce;
#   3. classifies (classify_prs.py) after each phase;
#   4. restarts the paused app queue (outputs/rvsec_auto/queue.sh as /tmp/rvsec_queue_auto_github_20261118_0626.sh,
#      same package list, appending to its log), unless it already runs.
# No phase starts after the deviation's cut-off (2026-11-19 18:00). Resumable: build_prs.sh skips built variants,
# queue_prs.sh skips finished runs. This file's name must not contain the RVSec queue pattern.
#   tr -d '\r' < chain_desvio6_20261118.sh > /tmp/dynval_chain_desvio6.sh
#   cd ~ && setsid nohup bash /tmp/dynval_chain_desvio6.sh >> logs/chain_desvio6.log 2>&1 < /dev/null &
set -u
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
T=/mnt/c/Users/Antonow/Documents/tcc_v3
PY=$HOME/venv/bin/python
CUTOFF=$(date -d "2026-11-19 18:00" +%s)
B_REP3="reader_47 intertune_96 yourlocalweather_213 owncloudnewsreader_196"
APPQ=/tmp/rvsec_queue_auto_github_20261118_0626.sh
echo "== $(date -Iseconds) chain (deviation 6) started (pid $$)"

before_cutoff() { [ "$(date +%s)" -lt "$CUTOFF" ] || { echo "== $(date -Iseconds) past the cut-off; $1 not started"; return 1; }; }
classify() { "$PY" "$V/classify_prs.py" > /dev/null || echo "== classify_prs.py failed"; }

# 1. Builds (never beside an emulator).
while pgrep -f "[q]emu-system|[e]mulator -avd|[e]mulator64|[r]vsec-run" >/dev/null; do sleep 120; done
echo "== $(date -Iseconds) building the deviation-6 cases"
tr -d '\r' < "$V/tools/build_prs.sh" > /tmp/dynval_build_prs_desvio6.sh
CASEFILE=$V/build/prs_build_cases_desvio6.txt bash /tmp/dynval_build_prs_desvio6.sh >> "$V/logs/build_prs_desvio6.log" 2>&1 < /dev/null
echo "== $(date -Iseconds) build ended"

cases() {  # $1 = "built": built A cases; "rep2": A cases needing a second repetition
  "$PY" - "$V" "$1" <<'EOF'
import json, sys
from pathlib import Path
v = Path(sys.argv[1]); mode = sys.argv[2]
a = [c["case"] for c in json.loads((v / "pr_cases_desvio6.json").read_text(encoding="utf-8"))["cases"] if not c.get("reuse_apks")]
if mode == "built":
    status = json.loads((v / "build_status_prs.json").read_text(encoding="utf-8"))
    print(" ".join(c for c in a if status.get(c, {}).get("case_status") == "built"))
else:
    res = json.loads((v / "results_prs.json").read_text(encoding="utf-8"))
    print(" ".join(r["case"] for r in res["prs"] if r.get("case") in a and r.get("reps") == [1]
                   and r.get("rule_result", r["status"]) in ("base não reproduz", "inconclusivo (cobertura)")))
EOF
}
A1=$(cases built)
echo "== $(date -Iseconds) built A cases: ${A1:-none}"
classify

# 2. Wait for the emulator lane: no other RVSec queue (the app queue's cognalys-run subshell included), rvsec-run
#    or emulator.
n=0
while pgrep -f "[r]vsec_queue|[r]vsec-run|[q]emu-system|[e]mulator -avd|[e]mulator64" >/dev/null; do
  [ $((n % 15)) -eq 0 ] && echo "   $(date -Iseconds) waiting for: $(pgrep -af '[r]vsec_queue|[r]vsec-run|[q]emu-system' | cut -c1-120 | tr '\n' ';')"
  n=$((n + 1)); sleep 120
done
echo "== $(date -Iseconds) emulator lane free"
tr -d '\r' < "$V/queue_prs.sh" > /tmp/rvsec_queue_dynval_prs.sh
run_q() {  # $1 = REPS, $2 = cases
  [ -n "$2" ] || return 0
  before_cutoff "REPS=$1 $2" || return 0
  echo "== $(date -Iseconds) queue_prs.sh REPS=$1 ONLY_CASES=\"$2\""
  REPS=$1 ONLY_CASES="$2" bash /tmp/rvsec_queue_dynval_prs.sh < /dev/null
  echo "== $(date -Iseconds) REPS=$1 exit $?"
  classify
}
run_q 1 "$A1"
run_q 3 "$B_REP3"
run_q 4 "bitbanana_79"
A2=$(cases rep2)
echo "== $(date -Iseconds) A cases for a second repetition: ${A2:-none}"
run_q 2 "$A2"
classify
echo "== $(date -Iseconds) chain (deviation 6) finished"

# 4. Restart the paused app queue (resumable; skips finished steps).
if pgrep -f "[r]vsec_queue_auto_github_20261118_0626" >/dev/null; then
  echo "== app queue already running; not restarted"
else
  tr -d '\r' < "$T/outputs/rvsec_auto/queue.sh" > "$APPQ"
  cd "$T" && setsid nohup bash "$APPQ" $(cat "$T/outputs/rvsec_auto/queued_github_20261118_0626.txt" | tr -d '\r') \
    >> "$T/outputs/rvsec_auto/queue_github_20261118_0626.log" 2>&1 < /dev/null &
  echo "== $(date -Iseconds) app queue restarted (pid $!)"
fi
