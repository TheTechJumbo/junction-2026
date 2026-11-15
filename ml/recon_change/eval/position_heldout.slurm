#!/bin/bash
# Held-out position test (eval/position_heldout.py, results in eval/POSITION_RESULTS.md) on one L4.
# Layout on the cluster (nothing is written to the live zone): a folder with
#   code/splat_locate.py  code/live/care_space.py  code/eval/position_heldout.py   (from this checkout)
#   room.json             the relay's rooms.json entry for the zone + "matrix" from GET /care/space
#   data/baselines/kitchen  a copy of junction-recon/baselines/kitchen
# then, from that folder:
#   TAG=_gpu EXTRA="--methods today,vggt,quick" sbatch position_heldout.slurm
#   TAG=_cpu EXTRA="--methods kfquick,pnp" sbatch --account=student --partition=amd_student --gres=none position_heldout.slurm
#SBATCH --job-name=jun-poseval
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=01:30:00
#SBATCH --output=slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
ROOT=/scratch/$USER/junction-recon
SESSION=20261115_025911_0a9dfa62
mkdir -p out
echo "== $(hostname) $(date +%T)"
$ROOT/.venv-recon/bin/python -u code/eval/position_heldout.py --root $ROOT --frames $ROOT/recordings/kitchen/$SESSION/frames \
  --trained $ROOT/trained/kitchen-$SESSION/out --room room.json --zone-data data --out out/heldout${TAG:-}.json \
  --hard 45 ${EXTRA:-}
rm -rf data/jobs
echo "== done $(date +%T)"
