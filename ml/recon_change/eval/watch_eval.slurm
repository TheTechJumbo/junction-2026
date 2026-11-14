#!/bin/bash
# Measure watch.py on PASLCD from the reconstructions the recall work saved: CPU cores only.
# Copy watch.py, recon_change.py, eval/run_bench.py, eval/watch_eval.py and this file into
# <work folder>/watch/code/, then submit from the work folder, one array task per scene:
#   sbatch --array=0-9 watch/code/watch_eval.slurm 1 recall/out/base
#   sbatch --array=0-4 watch/code/watch_eval.slurm 2 recall/out/base_inst2
# Arguments: the PASLCD instance (1 = same lighting, 2 = other lighting) and the folder of saved
# reconstructions. Instance 2 exists for the five indoor scenes only (array 0-4). A third
# argument names another output folder (default watch/out/eval).
#SBATCH --job-name=jun-watch
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=00:50:00
#SBATCH --output=watch/logs/slurm-%x-%A_%a.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
SCENES=(Cantina Lounge Printing_area Lunch_room Meeting_room Garden Pots Zen Playground Porch)
SCENE=${SCENES[$SLURM_ARRAY_TASK_ID]}
echo "== $(hostname) $(date +%T) $SCENE instance $1"
# -I: the data folder is a download, so Python must not pick up anything from the environment.
.venv-recon/bin/python -I -u watch/code/watch_eval.py --recon "$2" --data bench/data/PASLCD --scene "$SCENE" \
    --instance "$1" --out "${3:-watch/out/eval}" --whole-room recall
echo "== done $(date +%T)"
