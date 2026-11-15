#!/bin/bash
# Run the reconstruction + change test on one L4 GPU (24 GB). Submit from the folder
# that holds recon_change.py, after `bash cluster/setup_env.sh` has run once on a login node:
#   sbatch cluster/recon_change.slurm --before before.mp4 --after after.mp4 --out out
# Arguments after the script name go to recon_change.py.
#SBATCH --job-name=jun-recon
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:15:00
#SBATCH --output=slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
# Compute nodes have no internet: use the weights setup_env.sh downloaded to scratch.
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
echo "== $(hostname) $(date +%T)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
.venv-recon/bin/python -u recon_change.py "$@"
echo "== done $(date +%T)"
