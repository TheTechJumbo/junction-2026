#!/bin/bash
# A recording's frames anonymised with OWLv2 and anonymise.py (eval/venue_anon.py), then a splat trained from them
# (video_map.py), for publishing. On one L4:   sbatch cluster/venue_anon.slurm FRAMES OUT [video_map.py options]
# OUT/frames: the anonymised frames, OUT/anon_report.json, OUT/out: the splat. Environment as video_map.slurm's.
#SBATCH --job-name=venue-anon
#SBATCH --account=ecsstudents
#SBATCH --partition=ecsstudents_l4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=venue-anon-%j.out
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
ITEMS_ENV="$(realpath -m "${RECON_ITEMS_ENV:-items}")"
if [ -d "$ITEMS_ENV/pkgs" ]; then
    export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$ITEMS_ENV/pkgs" RECON_ITEMS_CACHE="${RECON_ITEMS_CACHE:-$ITEMS_ENV/hf/hub}"
fi
FRAMES="$1"; OUT="$2"; shift 2
echo "== $(hostname) $(date +%T) $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"
"$PY" -u eval/venue_anon.py "$FRAMES" "$OUT/frames"
"$PY" -u video_map.py "$OUT/frames" "$OUT/out" "$@"
echo "== done $(date +%T)"
