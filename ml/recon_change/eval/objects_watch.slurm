#!/bin/bash
# Object map evaluation, optional step on CPU cores: the watched-spot verdict for every inventory object
# in every held-out and after photo (eval/objects_watch.py). Needs watch.py (branch jun-22-watch-spots)
# next to objects.py in objects/code, and the dump of eval/objects_dump.slurm.
#   cd <project>/objects && sbatch --array=0-4 code/eval/objects_watch.slurm
#SBATCH --job-name=jun-objects-watch
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=48G
#SBATCH --time=00:25:00
#SBATCH --output=logs/slurm-%x-%A_%a.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
PROJECT="$(dirname "$PWD")"
export HF_HUB_OFFLINE=1 HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" CUDA_VISIBLE_DEVICES=""
SCENES=(Lounge Meeting_room Cantina Printing_area Lunch_room)
SCENE="${SCENES[${SLURM_ARRAY_TASK_ID:-0}]}"
echo "== $(hostname) $(date +%T) | $SLURM_CPUS_PER_TASK cores | $SCENE"
"$PROJECT/.venv-recon/bin/python" -u code/eval/objects_watch.py --dump dump --scene "$SCENE" --device cpu "$@"
echo "== done $(date +%T)"
