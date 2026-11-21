#!/bin/bash
# Option A without OP (deviation of 2026-11-17, docs/preregistro_opcao_a_20261116.md): OP dropped after 25 of 80
# phase-1 runs (Codex subscription quota), before any OP run was judged. SP2 r3-r4 phase 1 is complete.
# Phase 2 (port to the tip) of every TP and SP2 PR only. One lane: port_driver.py waits while an RVSec queue or a
# Cognalys run is active. Resumable. Copy to /tmp with tr -d '\r' and run with setsid.
set -u
KIT=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113
PY=/home/antonow/venv/bin/python
export MIN_FREE_GB=${MIN_FREE_GB:-8}
cd "$KIT" || exit 1
echo "== $(date -Is) option A (no OP) queue start"
if ! sha256sum --quiet -c port_phase/option_a.sha256; then echo "== $(date -Is) stop: option A hash mismatch"; exit 5; fi
if ! sha256sum --quiet -c prompt_plus_sp2.sha256; then echo "== $(date -Is) stop: prompt_plus_sp2 hash mismatch"; exit 5; fi
echo "== $(date -Is) hashes match the pre-registration"
unset OPENAI_API_KEY ANTHROPIC_API_KEY GH_TOKEN GITHUB_TOKEN
exec 9> /tmp/comparison_20261113.lock
flock 9
ARMS_ONLY=TP,SP2 "$PY" "$KIT/port_phase/port_driver.py"
echo "== $(date -Is) phase 2 driver exit $?"
flock -u 9
echo "== $(date -Is) option A (no OP) queue end"
