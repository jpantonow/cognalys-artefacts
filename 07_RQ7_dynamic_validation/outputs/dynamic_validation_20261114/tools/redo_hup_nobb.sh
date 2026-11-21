#!/bin/bash
# 2026-11-15: networksurvey_114 base_nobb_r1 died after 21 s with "RVSec wrote no reports (exit 129)":
# SIGHUP from the WSL session that launched the queue ending (nohup covered the queue, not the docker
# client). queue.sh's end test took it as finished (.done). This waits for the nobb queue to end, keeps
# the killed run's output under base_nobb_r1.hup_20261115/, and reruns the queue (setsid, so no session
# HUP reaches it), which then runs only base_nobb_r1.
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
while pgrep -f "[r]vsec_queue_dynval_nobb.sh" >/dev/null; do sleep 60; done
O=$V/runs/networksurvey_114/base_nobb_r1
if grep -q "exit 129" "$O/rvsec_run.out" 2>/dev/null; then
  rm -rf "$O.hup_20261115"; mv "$O" "$O.hup_20261115"
  echo "== $(date -Iseconds) base_nobb_r1 (SIGHUP, exit 129) moved to base_nobb_r1.hup_20261115; rerunning" >> $V/logs/queue_nobb.log
  setsid bash /tmp/rvsec_queue_dynval_nobb.sh > /dev/null 2>&1 < /dev/null
fi
