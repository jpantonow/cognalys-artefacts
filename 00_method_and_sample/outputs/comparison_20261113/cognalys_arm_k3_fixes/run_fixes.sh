# POST-FIX RERUN of arm C (k3) for the three units whose C runs failed; see build_fixes.py. Separate from arm C.
# RERUN k3 at fd90351: same units, settings and leak controls as the 8be9f78 arm, after the fixes found by the comparison (declared as a post-fix rerun).
# Cognalys arm (C) of the 2026-11-13 agent comparison: 20 units x 4 replicas, in queue.txt order
# (every unit's replica 1, then 2, ...). Adapted from outputs/frozen_pr_20261113/run_frozen.sh.
#
# Waits for the agent queue (arms S, O, T) to end: it takes that queue's lock before starting.
# Money: nothing is published. US$ 30 for the whole arm. Each run gets --max-cost =
# min(2.00, what it has spent + what is left of the 30); no run starts with less than
# MIN_START_USD left. Resumable: rerun it and it skips runs `cognalys status --resume-check` calls
# complete. Stops: C: under 8 GB free; src/ is not EXPECTED_COMMIT's; manifest mismatch; the
# repository lock held by another run. Each finished run's ~/cognalys_work/c_arm/<slug> is removed.
#
# Start (from Windows), copy + run in one go because WSL's /tmp is wiped on restart:
#   wsl.exe bash -lc "cp /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113/cognalys_arm_k3/run_c_arm.sh /tmp/run_c_arm.sh && bash /tmp/run_c_arm.sh >> /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113/cognalys_arm_k3/logs/queue.log 2>&1"
set -u
W=/mnt/c/Users/Antonow/Documents/tcc_v3
H=$W/outputs/comparison_20261113/cognalys_arm_k3_fixes
EXPECTED_COMMIT=${EXPECTED_COMMIT:?set EXPECTED_COMMIT to the merged fix commit}
CEILING=${CEILING:-5}
MIN_START_USD=${MIN_START_USD:-0.75}
STOP_FREE_GB=${STOP_FREE_GB:-8}
RUN_TIMEOUT=${RUN_TIMEOUT:-6h}
cd "$W" || exit 1
mkdir -p "$H/logs" "$H/state"

exec 9> "$H/state/queue.lock"
flock -n 9 || { echo "== another run_c_arm.sh holds $H/state/queue.lock"; exit 1; }

if [ "${CHECK_ONLY:-0}" != 1 ]; then
  echo "== $(date -Iseconds) waiting for the agent comparison queue (arms S, O, T) to end"
  exec 8> /tmp/comparison_20261113.lock
  flock 8
  flock -u 8
  echo "== $(date -Iseconds) agent queue ended"
fi

source ~/venv/bin/activate
set -a; . "$W/.env.openai" > /dev/null 2>&1; set +a
[ -n "${OPENAI_API_KEY:-}" ] || { echo "== no OPENAI_API_KEY after loading .env.openai"; exit 1; }
export COGNALYS_WORKSPACE=$W
export PYTHONPATH=$W/src
export TCC_LEARNING_DIR=$W/outputs/frozen_pr_20261113/learning

python - <<PY || { echo "== cognalys is not imported from $W/src"; exit 3; }
import cognalys, sys
sys.exit(0 if cognalys.__file__.startswith("$W/src/") else 1)
PY
head=$(git -C "$W" rev-parse HEAD)
if [ "$head" != "$EXPECTED_COMMIT" ] || ! git -C "$W" diff --quiet "$EXPECTED_COMMIT" -- src; then
  echo "== stop: src/ is not $EXPECTED_COMMIT's (HEAD $head)"
  exit 3
fi

free_gb() { df -BG --output=avail /mnt/c | tail -1 | tr -dc 0-9; }
spent_total() { python "$H/spent.py"; }
spent_run() { python "$H/spent.py" "$1"; }
field() { python - "$1" "$2" <<'PY'
import json, sys
runs = {r["slug"]: r for r in json.load(open("/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113/cognalys_arm_k3_fixes/runs.json"))["runs"]}
print(runs[sys.argv[1]][sys.argv[2]])
PY
}

verdict() {
  [ -f "$1/run_status.json" ] || { check=14; line="not started"; return; }
  line=$(python -m cognalys.cli status "$1" --resume-check 2>&1 < /dev/null); check=$?
}

pass() {  # slug toml run_dir log
  local slug=$1 toml=$2 run=$3 log=$4 cap left mine per code
  cap=$(field "$slug" max_cost)
  left=$(python -c "print(round($CEILING - $(spent_total), 4))")
  mine=$(spent_run "$run")
  per=$(python -c "print(round(min($cap, $mine + $left), 2))")
  if ! python -c "import sys; sys.exit(0 if $left >= $MIN_START_USD else 1)"; then
    echo "== stop: US\$ $left left of $CEILING (under $MIN_START_USD); $slug not started"
    exit 4
  fi
  echo "== $(date -Iseconds) $slug: cognalys run (max-cost $per; run spent $mine; left $left)"
  echo "=== $(date -Iseconds) cognalys run --max-cost $per" >> "$log"
  timeout "$RUN_TIMEOUT" python -m cognalys.cli run "$toml" --max-cost "$per" >> "$log" 2>&1 < /dev/null
  code=$?
  echo "   $slug: pipeline exit $code"
  if grep -q "does not match .*manifests/$slug.json" "$log"; then
    echo "== stop: $slug's run does not match its experiment manifest (see $log)"
    exit 2
  fi
  if grep -q "Repository use is locked" "$log" && [ ! -f "$run/run_status.json" ]; then
    echo "== stop: the repository lock (outputs/.repository_usage.lock) is held by another run"
    exit 5
  fi
  return 0
}

mapfile -t QUEUE < "$H/queue.txt"
echo "== $(date -Iseconds) Cognalys arm: ${#QUEUE[@]} runs, ceiling US\$ $CEILING, spent so far US\$ $(spent_total), C: $(free_gb) GB free, uptime: $(uptime -p)"
if [ "${CHECK_ONLY:-0}" = 1 ]; then
  for slug in "${QUEUE[@]}"; do
    run=$(field "$slug" run_dir); verdict "$run"
    echo "   $slug: status $check (${line#"$run": })"
  done
  exit 0
fi
for slug in "${QUEUE[@]}"; do
  [ -n "$slug" ] || continue
  toml=$(field "$slug" toml)
  run=$(field "$slug" run_dir)
  work=$(field "$slug" work)
  log=$H/logs/$slug.log
  [ -f "$H/state/done_$slug" ] && continue
  [ -f "$H/state/needs_rerun_$slug" ] && { echo "   $slug: needs a new run ($(cat "$H/state/needs_rerun_$slug")), skipped"; continue; }
  free=$(free_gb); free=${free:-0}
  if [ "$free" -lt "$STOP_FREE_GB" ]; then
    echo "== stop: C: has ${free} GB free (under $STOP_FREE_GB)"; exit 6
  fi
  passes=0
  while :; do
    verdict "$run"
    case $check in
      0) touch "$H/state/done_$slug"; echo "   $slug: ${line#"$run": }"; break ;;
      13) echo "   $slug: another process runs it, left alone"; break ;;
      11|12) echo "${line#"$run": }" > "$H/state/needs_rerun_$slug"
             echo "   $slug: ${line#"$run": }"; break ;;
      10|14) if [ $passes -ge 3 ]; then echo "   $slug: not complete after $passes passes, left for the next invocation"; break; fi
             if [ $passes -ge 1 ] && python -c "import sys; sys.exit(0 if $(spent_run "$run") >= $(field "$slug" max_cost) - 0.25 else 1)"; then
               echo "spent its cap" > "$H/state/cap_reached_$slug"
               echo "   $slug: incomplete at its cap (US\$ $(spent_run "$run")), not resumed"; break
             fi
             passes=$((passes + 1))
             pass "$slug" "$toml" "$run" "$log" ;;
    esac
  done
  echo "   $slug cost: $(python -m cognalys.cli cost "$run" 2>&1 < /dev/null | tail -1)"
  echo "== cumulative Cognalys-arm cost: US\$ $(spent_total) of $CEILING"
  if [ -f "$H/state/done_$slug" ] && [ "${KEEP_WORK:-0}" != 1 ] && [ -d "$work" ]; then
    rm -rf "$work" && echo "   removed $work (local clones and worktrees)"
  fi
done
echo "== $(date -Iseconds) queue pass finished; cumulative US\$ $(spent_total) of $CEILING"
ls "$H"/state/needs_rerun_* 2>/dev/null | sed 's/^/   needs rerun: /'
