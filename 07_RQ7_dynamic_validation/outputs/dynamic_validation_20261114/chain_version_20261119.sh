#!/bin/bash
# 2026-11-19: after the deviation-6 chain, rerun cases with the RVSec version of the campaign that reported them
# (estudo02-20260916: image phtcosta/rvandroid:0.9.4, rvsec fc51ab64; codes table = data/raw/exp_novo/codes.csv).
# The app queue's package list was emptied before the chain's end so that the chain's restart does nothing;
# it is restored here (queued_github_20261118_0626.txt.hold_20261119).
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
A=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/rvsec_auto
L=$V/logs/chain_desvio6.log
echo "== $(date -Iseconds) version chain waiting for the deviation-6 chain"
until tail -3 $L | grep -qE 'app queue (restarted|already)'; do
  pgrep -f '[d]ynval_chain_desvio6' >/dev/null || break
  sleep 60
done
sleep 30
pkill -f '[r]vsec_queue_auto_github_20261118_0626' 2>/dev/null
cp $A/queued_github_20261118_0626.txt.hold_20261119 $A/queued_github_20261118_0626.txt
echo "== $(date -Iseconds) app queue list restored; starting the estudo02-version reruns"
tr -d '\r' < $V/queue_prs_version.sh > /tmp/rvsec_queue_dynval_version.sh
export IMAGE=phtcosta/rvandroid:0.9.4 RVSEC_HOME_DIR=$HOME/rvsec_home_estudo02
cd ~
# keepassvault #347: estudo02 reports by ape (180/300 s) and monkey.
REPS="1 2 3" TOOLS="ape,monkey" TIMEOUT=300 ONLY_CASES="passnotes_11700_pr347" bash /tmp/rvsec_queue_dynval_version.sh < /dev/null
echo "== $(date -Iseconds) passnotes_11700_pr347 exit $?"
# BitBanana #184: estudo02 reports by fastbot and ape (300 s).
REPS="1 2 3" TOOLS="fastbot,ape" TIMEOUT=300 ONLY_CASES="bitbanana_79" bash /tmp/rvsec_queue_dynval_version.sh < /dev/null
echo "== $(date -Iseconds) bitbanana_79 exit $?"
echo "== $(date -Iseconds) version chain finished"
