#!/bin/bash
# Measure locate.py on PASLCD on one Iridis L4 GPU. Copy locate_eval.py, run_bench.py, this file
# and the locate.py and recon_change.py under test into <work folder>/loceval/, fetch the poses on
# the login node (python loceval/fetch_poses.py --out loceval/poses), then submit from the work
# folder (the PASLCD photos are the ones fetch_paslcd.py put under bench/data):
#   sbatch loceval/locate_eval.slurm --out loceval/out
# Arguments after the script name go to locate_eval.py. A second run with the same --out
# continues where the first one stopped.
#SBATCH --job-name=jun-loceval
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:45:00
#SBATCH --output=loceval/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
# Compute nodes have no internet: use the weights setup_env.sh downloaded to scratch.
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
echo "== $(hostname) $(date +%T)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# -I: the data folder is a download, so Python must not pick up anything from the environment.
.venv-recon/bin/python -I -u loceval/locate_eval.py --data bench/data/PASLCD --poses loceval/poses "$@"
echo "== done $(date +%T)"
