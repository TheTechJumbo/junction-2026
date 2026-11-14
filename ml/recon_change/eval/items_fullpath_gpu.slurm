#!/bin/bash
# One whole position fix with lost items on one Iridis L4 GPU (eval/items_fullpath.py), as the GPU
# worker runs it: 12 baseline photos, long side 518. Work folder as for items_eval.slurm:
#   cd <project>/items && sbatch code/eval/items_fullpath_gpu.slurm
#SBATCH --job-name=jun-items-full
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:15:00
#SBATCH --output=logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export PYTHONPATH="$PWD/pkgs" RECON_ITEMS_CACHE="$PWD/hf/hub" HF_HUB_OFFLINE=1
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}"
PROJECT="$(dirname "$PWD")"
OTHER="$(ls "$PROJECT"/bench/data/PASLCD/Lounge/Instance_1/images/*.JPG | head -1)"
echo "== $(hostname) $(date +%T) | $(nvidia-smi --query-gpu=name --format=csv,noheader)"
"$PROJECT/.venv-recon/bin/python" -u code/eval/items_fullpath.py --baselines "$PROJECT/baselines" --zone kitchen     --originals "$PROJECT/vggt-examples/examples/kitchen/images" --set data/lvis/set.json --img data/lvis/img --other "$OTHER"     --work out/full-gpu "$@"
echo "== done $(date +%T)"
