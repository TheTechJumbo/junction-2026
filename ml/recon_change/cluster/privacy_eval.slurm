#!/bin/bash
# Does VGGT and gsplat still work on garbled frames (eval/privacy_eval.py, PRIVACY.md)? On one L4, on a recording that is
# already on the cluster:   sbatch cluster/privacy_eval.slurm FRAMES OUT [--keyframes 64] [--fixes 10] [--steps 7000]
# The environment is video_map.slurm's (gsplat from GSPLAT_ENV).
#SBATCH --job-name=privacy-eval
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=privacy-eval-%j.out
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
echo "== $(hostname) $(date +%T) $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
"$PY" -u eval/privacy_eval.py "$@"
echo "== done $(date +%T)"
