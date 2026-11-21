#!/bin/bash
# RERUN k3 at fd90351: same units, settings and leak controls as the 8be9f78 arm, after the fixes found by the comparison (declared as a post-fix rerun).
# Arm D of the 2026-11-13 agent comparison: Cognalys with claude-sonnet-5-5 (Anthropic API, key in
# .env.anthropic, never printed), 20 units x 2 replicas, in queue.txt order. Derived from
# cognalys_arm/run_c_arm.sh; same commit (8be9f78), settings and stops, only provider and model differ.
#
# Waits for arm C to end (takes arm C's queue lock). Then a smoke test: SMOKE_SLUG alone (default
# pr_2b8cb7f6653b_r1, which reached "PR pronto" in arm C for US$ 0.31, so it exercises research,
# adjudication, generation, review and build). The arm continues only if the smoke run completes with
# pipeline exit 0; otherwise it stops (exit 7) and nothing else runs, since src/ cannot be changed
# mid-comparison. Money: nothing is published. US$ 25 for the whole arm (Anthropic has no Flex tier;
# the web search fee is not in data/llm_prices.json for Sonnet, so the ceiling counts tokens only).
set -u
W=/mnt/c/Users/Antonow/Documents/tcc_v3
H=$W/outputs/comparison_20261113/cognalys_d_arm_k3
C_LOCK=$W/outputs/comparison_20261113/cognalys_arm_k3/state/queue.lock
EXPECTED_COMMIT=${EXPECTED_COMMIT:-fd9035152009a60ec54daebc8bd00755c8efb574}
CEILING=${CEILING:-22}  # US$ 25 of credits; 3 left for the web search fee, which the ceiling does not count
SMOKE_SLUG=${SMOKE_SLUG:-pr_2b8cb7f6653b_r1}
MIN_START_USD=${MIN_START_USD:-0.75}
STOP_FREE_GB=${STOP_FREE_GB:-8}
RUN_TIMEOUT=${RUN_TIMEOUT:-6h}
cd "$W" || exit 1
mkdir -p "$H/logs" "$H/state"

exec 9> "$H/state/queue.lock"
flock -n 9 || { echo "== another run_d_arm.sh holds $H/state/queue.lock"; exit 1; }

if [ "${CHECK_ONLY:-0}" != 1 ]; then
  echo "== $(date -Iseconds) waiting for arm C (its queue lock) to end"
  exec 8>> "$C_LOCK"
  flock 8
  flock -u 8
  echo "== $(date -Iseconds) arm C ended"
fi

# Sonnet 5.5 prices, checked on 2026-11-13 against platform.claude.com/docs/en/about-claude/pricing:
# cache hits 0.10 (0.05x input; the entry had 0.20), 5-minute cache writes 2.50, web search US$ 10 per
# 1,000 searches. Applied here, after arm C ended, because data/llm_prices.json is tracked and editing it
# mid-arm would change the recorded identity of an interrupted C run. Idempotent; prices only.
if [ "${CHECK_ONLY:-0}" != 1 ]; then
python3 - "$W/data/llm_prices.json" <<'PY' || { echo "== stop: could not update Sonnet prices"; exit 8; }
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
entry = data["models"]["claude-sonnet-5-5"]
wanted = {"input": 2.0, "cached_input": 0.10, "cache_write_input": 2.50, "output": 10.0,
          "web_search_per_1k_calls": 10.0}
if entry != wanted:
    data["models"]["claude-sonnet-5-5"] = wanted
    data["_claude"] = ("Sonnet 5.5, checked on 2026-11-13 against platform.claude.com/docs/en/about-claude/pricing: "
                       "2.00 input, 0.10 cache hits (0.05x input), 2.50 for 5-minute cache writes, 10.00 output; "
                       "web search $10.00 / 1k searches plus result tokens at the input price.")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("== Sonnet 5.5 prices updated in data/llm_prices.json")
else:
    print("== Sonnet 5.5 prices already current")
PY
fi
source ~/venv/bin/activate
set -a; . "$W/.env.anthropic" > /dev/null 2>&1; set +a
[ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "== no ANTHROPIC_API_KEY after loading .env.anthropic"; exit 1; }
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
runs = {r["slug"]: r for r in json.load(open("/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113/cognalys_d_arm_k3/runs.json"))["runs"]}
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
  if grep -q "credit balance is too low" "$log"; then
    echo "== stop: Anthropic credits exhausted during $slug; arm D ends here (partial)"
    touch "$H/state/credits_exhausted"
    exit 8
  fi
  if grep -q "Repository use is locked" "$log" && [ ! -f "$run/run_status.json" ]; then
    echo "== stop: the repository lock (outputs/.repository_usage.lock) is held by another run"
    exit 5
  fi
  return 0
}

mapfile -t QUEUE < <(echo "$SMOKE_SLUG"; grep -vx "$SMOKE_SLUG" "$H/queue.txt")
echo "== $(date -Iseconds) arm D (Cognalys + Sonnet 5.5): ${#QUEUE[@]} runs, ceiling US\$ $CEILING, spent so far US\$ $(spent_total), C: $(free_gb) GB free, uptime: $(uptime -p)"
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
  echo "== cumulative arm-D cost: US\$ $(spent_total) of $CEILING"
  if [ "$slug" = "$SMOKE_SLUG" ]; then
    last_exit=$(grep -F "   $slug: pipeline exit" "$H/logs/queue.log" | tail -1 | awk '{print $NF}')
    if [ -f "$H/state/done_$slug" ] && [ "${last_exit:-0}" = 0 ]; then
      echo "== $(date -Iseconds) smoke test passed ($slug complete); continuing"
    else
      echo "== $(date -Iseconds) stop: smoke test failed ($slug not complete or nonzero exit; see $log)"
      exit 7
    fi
  fi
  if [ -f "$H/state/done_$slug" ] && [ "${KEEP_WORK:-0}" != 1 ] && [ -d "$work" ]; then
    rm -rf "$work" && echo "   removed $work (local clones and worktrees)"
  fi
done
echo "== $(date -Iseconds) queue pass finished; cumulative US\$ $(spent_total) of $CEILING"
ls "$H"/state/needs_rerun_* 2>/dev/null | sed 's/^/   needs rerun: /'
