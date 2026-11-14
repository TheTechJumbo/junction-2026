#!/bin/bash
# Reconstruct every PASLCD pair once on an Iridis L4 and keep the reconstructions, so the
# detector can be scored again on CPU cores (recall_cpu.slurm). Copy run_bench.py, the
# recon_change.py under test and both slurm files into <work folder>/recall/, then submit from
# the work folder (PASLCD must already be in bench/data/PASLCD, see RESULTS.md):
#   sbatch recall/recall_gpu.slurm --out recall/out/base --save-recon
# Arguments after the script name go to run_bench.py.
#SBATCH --job-name=jun-recall
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:14:00
#SBATCH --output=recall/logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
# Compute nodes have no internet: use the weights setup_env.sh downloaded to scratch.
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
echo "== $(hostname) $(date +%T)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# -I: the data folder is a download, so Python must not pick up anything from the environment.
.venv-recon/bin/python -I -u recall/run_bench.py --data bench/data/PASLCD "$@"
echo "== done $(date +%T)"
