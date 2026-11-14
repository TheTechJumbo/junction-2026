#!/bin/bash
# Side experiment of watch_crop_features.py: close-up patch features, on CPU cores.
# Needs the output of watch_eval.slurm. Submit from the work folder, one array task per scene:
#   sbatch --array=0-9 watch/code/watch_crop.slurm
#SBATCH --job-name=jun-watch-crop
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=40G
#SBATCH --time=00:50:00
#SBATCH --output=watch/logs/slurm-%x-%A_%a.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
# Compute nodes have no internet: use the weights setup_env.sh downloaded to scratch.
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1 OMP_NUM_THREADS=16
SCENES=(Cantina Lounge Printing_area Lunch_room Meeting_room Garden Pots Zen Playground Porch)
SCENE=${SCENES[$SLURM_ARRAY_TASK_ID]}
echo "== $(hostname) $(date +%T) $SCENE"
.venv-recon/bin/python -I -u watch/code/watch_crop_features.py --eval "${1:-watch/out/eval}" --recon recall/out/base \
    --data bench/data/PASLCD --scene "$SCENE" --out watch/out/crop
echo "== done $(date +%T)"
