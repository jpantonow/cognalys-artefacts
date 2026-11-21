V=/mnt/c/Users/Antonow/Documents/tcc_v3/outputs/dynamic_validation_20261114
cd ~
pgrep -f "tools/build_all.sh" >/dev/null && { echo "already running"; exit 1; }
setsid nohup bash $V/tools/build_all.sh >> $V/logs/build_all.log 2>&1 < /dev/null &
sleep 3
setsid nohup bash $V/tools/disk_guard.sh >> $V/logs/disk_guard.log 2>&1 < /dev/null &
sleep 2
pgrep -af 'build_all|disk_guard' | cut -c1-150
