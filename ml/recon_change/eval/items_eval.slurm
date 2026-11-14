#!/bin/bash
# Run the lost-items detector over the test photos on one Iridis L4 GPU (eval/items_eval.py detect).
# Work folder: <project>/items, with this branch's files under code/, the packages and weights from
# iridis/setup_items.sh under pkgs/ and hf/, and the photos from eval/items_fetch.py under data/lvis.
#   cd <project>/items && sbatch code/eval/items_eval.slurm
#SBATCH --job-name=jun-items
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
echo "== $(hostname) $(date +%T)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
PROJECT="$(dirname "$PWD")"
# -I is not used here: PYTHONPATH must be read. The photos are only ever decoded by OpenCV.
"$PROJECT/.venv-recon/bin/python" -u code/eval/items_eval.py detect --set data/lvis/set.json --img data/lvis/img --out out/eval \
    --clean "kitchen=$PROJECT/baselines/kitchen/*.jpg" --clean "paslcd=$PROJECT/bench/data/PASLCD/*/Instance_1/images/*.JPG" --clean-max 150 "$@"
echo "== done $(date +%T)"
