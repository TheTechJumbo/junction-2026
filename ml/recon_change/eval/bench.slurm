#!/bin/bash
# Score recon_change.py on PASLCD on one L4 GPU. Copy run_bench.py, this file and the
# recon_change.py under test into <work folder>/bench/, fetch the data on the login node
# (python bench/fetch_paslcd.py --out bench/data/PASLCD), then submit from the work folder:
#   sbatch bench/bench.slurm --out bench/out/paslcd --pairs changed
# Arguments after the script name go to run_bench.py.
#SBATCH --job-name=jun-bench
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:14:00
#SBATCH --output=bench/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
# Compute nodes have no internet: use the weights setup_env.sh downloaded to scratch.
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
echo "== $(hostname) $(date +%T)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# -I: the data folder is a download, so Python must not pick up anything from the environment.
.venv-recon/bin/python -I -u bench/run_bench.py --data bench/data/PASLCD "$@"
echo "== done $(date +%T)"
