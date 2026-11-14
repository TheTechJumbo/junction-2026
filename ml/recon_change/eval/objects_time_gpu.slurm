#!/bin/bash
# Object map: seconds for an inventory and for one update on the L4 GPU (eval/objects_dump.py --timing).
#   cd <project>/objects && sbatch code/eval/objects_time_gpu.slurm
#SBATCH --job-name=jun-objects-gpu
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:10:00
#SBATCH --output=logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
PROJECT="$(dirname "$PWD")"
export PYTHONPATH="$PROJECT/items/pkgs" RECON_ITEMS_CACHE="$PROJECT/items/hf/hub" HF_HUB_OFFLINE=1
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}"
echo "== $(hostname) $(date +%T) | $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
"$PROJECT/.venv-recon/bin/python" -u code/eval/objects_dump.py --data "$PROJECT/bench/data/PASLCD" --scene Lounge --out dump --timing "$@"
echo "== done $(date +%T)"
