#!/bin/bash
# Second looks at weak item candidates, and whole-picture passes with more phrases, on one Iridis
# L4 GPU (eval/items_second_look.py). Needs the boxes eval/items_eval.slurm saved, and the CLIP
# weights from iridis/setup_items_more.sh. Work folder as for items_eval.slurm:
#   cd <project>/items && sbatch code/eval/items_second_look.slurm
#SBATCH --job-name=jun-items-look
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
[ -f out/recall/second_look.json ] || "$PY" -u code/eval/items_second_look.py candidates --raw out/eval/raw_tiles1.json \
    --raw out/eval-indoor/raw_tiles1.json --set data/lvis/set.json --img data/lvis/img --out out/recall --clip openai/clip-vit-large-patch14
echo "== more phrases $(date +%T)"
"$PY" -u code/eval/items_second_look.py detect --raw out/eval/raw_tiles1.json --raw out/eval-indoor/raw_tiles1.json \
    --set data/lvis/set.json --img data/lvis/img --out out/recall --tiles 1 "$@"
echo "== done $(date +%T)"
