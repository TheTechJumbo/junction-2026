#!/bin/bash
# Save the reconstruction of every locate benchmark pass once, on one L4 GPU, so that
# changes to locate.py can be scored afterwards on CPU cores (locate_rescore.slurm).
# Copy locate_eval.py, run_bench.py, locate.py, recon_change.py and this file into
# <work folder>/loclook/, then submit from the work folder:
#   sbatch loclook/locate_dump.slurm --dump loclook/dump --out loclook/out_dump
# A second run with the same --dump continues. About 30 GB for all 885 passes.
#SBATCH --job-name=jun-locdump
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=01:00:00
#SBATCH --output=loclook/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
# Compute nodes have no internet: use the weights setup_env.sh downloaded to scratch.
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
echo "== $(hostname) $(date +%T)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# -I: the data folder is a download, so Python must not pick up anything from the environment.
.venv-recon/bin/python -I -u loclook/locate_eval.py --data bench/data/PASLCD --poses loceval/poses --minutes 55 "$@"
echo "== done $(date +%T)"
