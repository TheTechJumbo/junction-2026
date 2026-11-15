#!/bin/bash
# A private training (live/private_job.py, PRIVACY.md): sealed, garbled frames in, sealed results out; the key reaches
# this job's memory through srun (vault.push_key) and is never written down. Submitted by live/private_train.py:
#   sbatch cluster/private_train.slurm DATA [video_map.py options]
# Environment as video_map.slurm's (gsplat from GSPLAT_ENV).
#SBATCH --job-name=private-train
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=private-train-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
PY="${VIDEO_MAP_PYTHON:-$SLURM_SUBMIT_DIR/.venv-recon/bin/python}"
GS="${GSPLAT_ENV:-/scratch/$USER/gsplat-env}"
if [ -d "$GS/pkgs" ]; then
    module load gcc/11.5.0 cuda/12.2.0
    export CC=gcc CXX=g++
    export PYTHONPATH="$GS/pkgs${PYTHONPATH:+:$PYTHONPATH}" PATH="$GS/pkgs/bin:$PATH" TORCH_CUDA_ARCH_LIST="8.9" MAX_JOBS=4
fi
# Labels (OWLv2 + CLIP, as video_map.slurm finds them). They see garbled keyframes: how well they work then is unmeasured.
ITEMS_ENV="$(realpath -m "${RECON_ITEMS_ENV:-items}")"
if [ -d "$ITEMS_ENV/pkgs" ]; then
    export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$ITEMS_ENV/pkgs" RECON_ITEMS_CACHE="${RECON_ITEMS_CACHE:-$ITEMS_ENV/hf/hub}"
fi
echo "== $(hostname) $(date +%T) $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
"$PY" -u live/private_job.py "$@"
echo "== done $(date +%T)"
