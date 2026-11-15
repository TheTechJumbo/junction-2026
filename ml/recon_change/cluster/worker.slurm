#!/bin/bash
# The live vision worker: one L4 GPU, VGGT kept loaded, serving requests from queue/ (see
# live/worker.py). It holds the GPU until it stops, so it is capped at 2 hours per start.
#   sbatch cluster/worker.slurm          # start
#   touch STOP   or   scancel <job id>   # stop
#SBATCH --job-name=jun-worker
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=02:00:00
#SBATCH --output=slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
# Lost items (items.py): the packages and weights that cluster/setup_items.sh put in a folder, if it is there.
ITEMS_ENV="$(realpath -m "${RECON_ITEMS_ENV:-items-env}")"
if [ -d "$ITEMS_ENV/pkgs" ]; then
    export PYTHONPATH="$ITEMS_ENV/pkgs${PYTHONPATH:+:$PYTHONPATH}" RECON_ITEMS_CACHE="${RECON_ITEMS_CACHE:-$ITEMS_ENV/hf/hub}"
fi
echo "== $(hostname) $(date +%T)"
.venv-recon/bin/python -u live/worker.py
echo "== done $(date +%T)"
