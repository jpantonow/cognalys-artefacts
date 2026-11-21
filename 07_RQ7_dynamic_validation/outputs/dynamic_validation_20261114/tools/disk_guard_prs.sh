#!/bin/bash
# Stops the PR build step (build_prs.sh, run as /tmp/dynval_build_prs.sh) and the
# processes working in ~/dynval_builds when C: falls below 10 GB free (the
# pre-registration's limit). Touches nothing else. Mirrors disk_guard.sh.
V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
LOG=$V/logs/build_prs.log
sleep 5
while pgrep -f "[d]ynval_build_prs.sh" >/dev/null; do
  f=$(df -BM --output=avail /mnt/c | tail -1 | tr -dc 0-9)
  if [ -n "$f" ] && [ "$f" -lt 10240 ]; then
    echo "== $(date -Iseconds) STOP (disk_guard_prs): C: has ${f} MB free (< 10 GB); stopping the PR builds" >> $LOG
    pkill -f "[d]ynval_build_prs.sh"
    for p in $(pgrep -f "java|gradle|apk_build"); do
      case "$(readlink /proc/$p/cwd 2>/dev/null) $(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)" in
        *dynval_builds*) kill $p ;;
      esac
    done
    exit 0
  fi
  sleep 15
done
