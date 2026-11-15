#!/bin/bash
# People, hands and arms over the test photos and the item-free photos on one Iridis L4 GPU
# (eval/people_eval.py detect). Work folder as for items_eval.slurm:
#   cd <project>/items && sbatch code/eval/people_eval.slurm
#SBATCH --job-name=jun-people
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=00:20:00
#SBATCH --output=logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export PYTHONPATH="$PWD/pkgs" RECON_ITEMS_CACHE="$PWD/hf/hub" HF_HUB_OFFLINE=1
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}"
PY="$(dirname "$PWD")/.venv-recon/bin/python"
echo "== $(hostname) $(date +%T) | $(nvidia-smi --query-gpu=name --format=csv,noheader)"
"$PY" -u code/eval/people_eval.py detect --set data/lvis/set.json --img data/lvis/img --free out/eval/raw_tiles1.json \
    --free out/eval-indoor/raw_tiles1.json --out out/people "$@"
echo "== done $(date +%T)"
