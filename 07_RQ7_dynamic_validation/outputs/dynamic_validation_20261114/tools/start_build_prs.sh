#!/bin/bash
# Starts the PR build step detached from the session (setsid nohup), with its disk guard.
# Run from WSL: bash outputs/dynamic_validation_20261114/tools/start_build_prs.sh
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
cd ~
pgrep -f "[d]ynval_build_prs.sh" >/dev/null && { echo "already running"; exit 1; }
tr -d '\r' < $V/tools/build_prs.sh > /tmp/dynval_build_prs.sh
tr -d '\r' < $V/tools/disk_guard_prs.sh > /tmp/dynval_disk_guard_prs.sh
setsid nohup bash /tmp/dynval_build_prs.sh "$@" >> $V/logs/build_prs.log 2>&1 < /dev/null &
sleep 2
setsid nohup bash /tmp/dynval_disk_guard_prs.sh >> $V/logs/disk_guard_prs.log 2>&1 < /dev/null &
sleep 2
pgrep -af '[d]ynval_build_prs|[d]ynval_disk_guard_prs' | cut -c1-150
