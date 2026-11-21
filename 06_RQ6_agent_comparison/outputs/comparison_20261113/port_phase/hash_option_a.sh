#!/bin/bash
# Writes port_phase/option_a.sha256 (checked by queue_option_a.sh before any run) and prints it as a table.
cd /mnt/c/Users/Antonow/Documents/tcc_v3/outputs/comparison_20261113 || exit 1
sha256sum port_phase/port_prompt.txt port_phase/run_port.py port_phase/port_driver.py port_phase/tips.json \
  port_phase/prep_tips.py port_phase/prep_tip_repos.py port_phase/make_order_option_a.py \
  run_one.py driver.py common.py build_check.py order_plus.json prompt_plus_sp2.sha256 prompt_plus.sha256 \
  units/*/prompt_plus_sp2.txt > port_phase/option_a.sha256
grep -v 'units/' port_phase/option_a.sha256 | awk '{print "| " $2 " | " $1 " |"}'
echo "| units/*/prompt_plus_sp2.txt (20) | $(grep 'units/' port_phase/option_a.sha256 | sha256sum | cut -d' ' -f1) (sha256 of their sha256 lines) |"
