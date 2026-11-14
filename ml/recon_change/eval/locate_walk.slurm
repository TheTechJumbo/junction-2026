#!/bin/bash
# Measure locate.py on TUM RGB-D (a person walking with a handheld camera) on one Iridis L4 GPU.
# In <work folder>/walk/: code/ holds locate_walk_eval.py and the locate.py and recon_change.py
# under test, data/tum/ holds the recordings (fetch_tum.py), out/plan.json comes from
#   python code/locate_walk_eval.py --data data/tum --plan out/plan.json --make-plan --shared ...
# Submit from walk/:
#   sbatch code/locate_walk.slurm --out out
# Arguments after the script name go to locate_walk_eval.py. A second run with the same --out
# continues where the first one stopped.
#SBATCH --job-name=jun-walk
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
../.venv-recon/bin/python -I -u code/locate_walk_eval.py --data data/tum --plan out/plan.json --detector-dir code "$@"
echo "== done $(date +%T)"
