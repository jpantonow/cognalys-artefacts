#!/bin/bash
# Option A (docs/preregistro_opcao_a_20261116.md): phase 1 of SP2 r3-r4 and OP r1-r4, then phase 2 (port to the
# tip) of every TP, SP2 and OP PR. One lane: driver.py and port_driver.py wait while an RVSec queue or a
# Cognalys run is active. Resumable (finished runs are skipped). Copy to /tmp with tr -d '\r' and run with setsid.
set -u
KIT=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113
PY=/home/antonow/venv/bin/python
export MIN_FREE_GB=${MIN_FREE_GB:-8}
cd "$KIT" || exit 1
echo "== $(date -Is) option A queue start"
if ! sha256sum --quiet -c port_phase/option_a.sha256; then echo "== $(date -Is) stop: option A hash mismatch"; exit 5; fi
if ! sha256sum --quiet -c prompt_plus_sp2.sha256; then echo "== $(date -Is) stop: prompt_plus_sp2 hash mismatch"; exit 5; fi
echo "== $(date -Is) hashes match the pre-registration"
while pgrep -f "[r]vsec_queue_dynval" >/dev/null; do sleep 120; done
echo "== $(date -Is) RVSec queue ended; phase 1 (SP2 r3-r4, OP)"
unset OPENAI_API_KEY ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN
exec 9> /tmp/comparison_20261113.lock
flock 9
ARMS_ONLY=SP2,OP PROGRESS_FILE=progress_option_a.json "$PY" "$KIT/driver.py"
echo "== $(date -Is) phase 1 driver exit $?"
ARMS_ONLY=TP,SP2,OP "$PY" "$KIT/port_phase/port_driver.py"
echo "== $(date -Is) phase 2 driver exit $?"
flock -u 9
echo "== $(date -Is) option A queue end"
