#!/bin/bash
# 2026-11-15: starts the RVSec queue of the published-PR validation (queue_prs.sh, pre-registration
# docs/preregistro_validacao_prs_20261115.md) once the comparison's TP+SP2 driver has ended (the driver
# pauses while RVSec runs, so they never overlap) and the PR builds have ended (the queue reads the
# built cases at start). Arm D may run at the same time (as arm C did with the first validation).
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
LOG=$V/logs/queue_prs.log
echo "== $(date -Iseconds) after_arms_queue_prs: waiting for the TP+SP2 driver and the PR builds" >> "$LOG"
while pgrep -f "[c]omparison_20261113/driver.py" >/dev/null || pgrep -f "[d]ynval_build_prs.sh" >/dev/null; do sleep 120; done
echo "== $(date -Iseconds) after_arms_queue_prs: starting queue_prs.sh" >> "$LOG"
tr -d '\r' < "$V/queue_prs.sh" > /tmp/rvsec_queue_dynval_prs.sh
setsid bash /tmp/rvsec_queue_dynval_prs.sh > /dev/null 2>&1 < /dev/null
