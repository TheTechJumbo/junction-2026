#!/bin/bash
# Save the reconstruction of every walking-test fix once, on one L4 GPU, so that changes to
# locate.py can be scored afterwards on CPU cores (locate_walk_rescore.slurm). No scoring here.
# In <work folder>/walk2/: code/ holds locate_walk_eval.py and recon_change.py and locate.py; the
# recordings and the plan are read from ../walk (see locate_walk.slurm). Submit from walk2/:
#   sbatch code/locate_walk_dump.slurm --frames 12+1
# A second run continues where the first one stopped. About 28 MB per fix at 12 + 1 frames.
#SBATCH --job-name=jun-walk-dump
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:30:00
#SBATCH --output=logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
# Compute nodes have no internet: use the weights setup_env.sh downloaded to scratch.
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
echo "== $(hostname) $(date +%T)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# -I: the data folder is a download, so Python must not pick up anything from the environment.
../.venv-recon/bin/python -I -u code/locate_walk_eval.py --data ../walk/data/tum --plan ../walk/out/plan.json \
    --detector-dir code --dump dump --out out_dump --minutes 27 "$@"
echo "== done $(date +%T)"
