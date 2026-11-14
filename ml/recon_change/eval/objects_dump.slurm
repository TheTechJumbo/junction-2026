#!/bin/bash
# Object map evaluation, step 1 on CPU cores: one array task per indoor PASLCD scene (eval/objects_dump.py).
# Work folder: <project>/objects with this branch's ml/recon_change copied to objects/code.
#   cd <project>/objects && sbatch --array=0-4 code/eval/objects_dump.slurm
#SBATCH --job-name=jun-objects
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=48G
#SBATCH --time=00:45:00
#SBATCH --output=logs/slurm-%x-%A_%a.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
PROJECT="$(dirname "$PWD")"
export PYTHONPATH="$PROJECT/items/pkgs" RECON_ITEMS_CACHE="$PROJECT/items/hf/hub" HF_HUB_OFFLINE=1
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" CUDA_VISIBLE_DEVICES=""
SCENES=(Lounge Meeting_room Cantina Printing_area Lunch_room)
SCENE="${SCENES[${SLURM_ARRAY_TASK_ID:-0}]}"
echo "== $(hostname) $(date +%T) | $(lscpu | grep 'Model name' | sed 's/.*: *//') | $SLURM_CPUS_PER_TASK cores | $SCENE"
"$PROJECT/.venv-recon/bin/python" -u code/eval/objects_dump.py --data "$PROJECT/bench/data/PASLCD" --scene "$SCENE" --out dump --device cpu "$@"
echo "== done $(date +%T)"
