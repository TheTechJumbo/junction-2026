#!/bin/bash
# A room as a Gaussian splat from one video or a recording's frames (video_map.py), on one L4. Deployed with the
# workers (cluster/deploy_from_laptop.sh); live/remote_train.py on the laptop submits it from the deployed folder:
#   sbatch cluster/video_map.slurm VIDEO_OR_FRAMES OUT [--keyframes 96] [--steps 7000]
# gsplat (training) comes from GSPLAT_ENV (default /scratch/$USER/gsplat-env, made with uv pip install --target ...
# --no-deps gsplat==1.4.0 jaxtyping==0.2.34 typeguard==2.13.3 rich markdown-it-py mdurl pygments ninja). It builds
# its CUDA code on its first run (a few minutes, kept in ~/.cache/torch_extensions). Without it: no training.
#SBATCH --job-name=video-map
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=video-map-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
PY="${VIDEO_MAP_PYTHON:-$SLURM_SUBMIT_DIR/.venv-recon/bin/python}"
GS="${GSPLAT_ENV:-/scratch/$USER/gsplat-env}"
if [ -d "$GS/pkgs" ]; then
    module load gcc/11.5.0 cuda/12.2.0  # nvcc for gsplat's first build; PyTorch needs GCC 9 or later (the system's is 8.5)
    export CC=gcc CXX=g++
    export PYTHONPATH="$GS/pkgs${PYTHONPATH:+:$PYTHONPATH}" PATH="$GS/pkgs/bin:$PATH" TORCH_CUDA_ARCH_LIST="8.9" MAX_JOBS=4
fi
# Labels (OWLv2, the workers' item finder, proposes; CLIP ViT-L/14 checks): packages and weights as worker.slurm finds them.
# Label a finished map again: sbatch cluster/video_map.slurm FRAMES --labels-only OUT
ITEMS_ENV="$(realpath -m "${RECON_ITEMS_ENV:-items}")"
if [ -d "$ITEMS_ENV/pkgs" ]; then
    export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$ITEMS_ENV/pkgs" RECON_ITEMS_CACHE="${RECON_ITEMS_CACHE:-$ITEMS_ENV/hf/hub}"
fi
echo "== $(hostname) $(date +%T) $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
"$PY" -u video_map.py "$@"
echo "== done $(date +%T)"
