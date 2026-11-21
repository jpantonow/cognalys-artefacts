# Stops this step's own builds (build_all.sh and the processes working in
# ~/dynval_builds) when C: falls below 12 GB. Touches nothing else.
LOG=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114/logs/build_all.log
while pgrep -f "dynamic_validation_20261114/tools/build_all.sh" >/dev/null; do
  f=$(df -BM --output=avail /mnt/c | tail -1 | tr -dc 0-9)
  if [ "$f" -lt 12288 ]; then
    echo "== $(date -Iseconds) STOP (disk_guard): C: has ${f} MB free (< 12 GB); stopping this step's builds" >> $LOG
    pkill -f "dynamic_validation_20261114/tools/build_all.sh"
    for p in $(pgrep -f "java|gradle|apk_build"); do
      case "$(readlink /proc/$p/cwd 2>/dev/null) $(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null)" in
        *dynval_builds*) kill $p ;;
      esac
    done
    echo "STOPPED at ${f} MB"; exit 0
  fi
  sleep 15
done
echo "guard: build_all.sh no longer running"
