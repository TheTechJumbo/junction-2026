#!/bin/bash
# The show-it-once test on one Iridis L4 GPU (eval/items_enrol.py detect). Before it, on a login node:
#   python code/eval/items_enrol.py masks --lvis data/lvis_v1_val.json --set data/lvis/set.json --out out/enrol
#   python code/eval/items_enrol.py make --img data/lvis/img --rooms out/eval-indoor/raw_tiles1.json --out out/enrol
# and the CLIP and DINOv2 weights (iridis/setup_items_more.sh <folder> openai/clip-vit-large-patch14 facebook/dinov2-base).
#   cd <project>/items && sbatch code/eval/items_enrol.slurm
#SBATCH --job-name=jun-items-enrol
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=00:30:00
#SBATCH --output=logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export PYTHONPATH="$PWD/pkgs" RECON_ITEMS_CACHE="$PWD/hf/hub" HF_HUB_OFFLINE=1
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}"
PY="$(dirname "$PWD")/.venv-recon/bin/python"
echo "== $(hostname) $(date +%T) | $(nvidia-smi --query-gpu=name --format=csv,noheader)"
"$PY" -u code/eval/items_enrol.py detect --out out/enrol --free out/eval/raw_tiles1.json --free out/eval-indoor/raw_tiles1.json --free-max 160 "$@"
echo "== done $(date +%T)"
