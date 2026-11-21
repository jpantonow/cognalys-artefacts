#!/bin/bash
cd /mnt/c/Users/Antonow/Documents/tcc_v3
source ~/venv/bin/activate
set -a; source .env.openai; source .env.github; set +a
cognalys run outputs/exp_final_20261019/subset_revisao_lacunas_v5.toml > outputs/exp_final_20261019/subset_revisao_lacunas_v5.log 2>&1
echo "PIPELINE_EXIT=$?"
