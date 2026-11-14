#!/bin/bash
# One whole position fix with lost items on CPU cores only (eval/items_fullpath.py), with the CPU
# worker's settings: 32 cores, 8 baseline photos, long side 518. Work folder as for items_eval.slurm:
#   cd <project>/items && sbatch code/eval/items_fullpath_cpu.slurm
#SBATCH --job-name=jun-items-full-cpu
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=48G
#SBATCH --time=00:30:00
#SBATCH --output=logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export PYTHONPATH="$PWD/pkgs" RECON_ITEMS_CACHE="$PWD/hf/hub" HF_HUB_OFFLINE=1
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}"
PROJECT="$(dirname "$PWD")"
OTHER="$(ls "$PROJECT"/bench/data/PASLCD/Lounge/Instance_1/images/*.jpg | head -1)"
export RECON_DEVICE=cpu RECON_FRAMES="${RECON_FRAMES:-8,4}" CUDA_VISIBLE_DEVICES=""
echo "== $(hostname) $(date +%T) | $(lscpu | grep 'Model name' | sed 's/.*: *//') | $SLURM_CPUS_PER_TASK cores"
"$PROJECT/.venv-recon/bin/python" -u code/eval/items_fullpath.py --baselines "$PROJECT/baselines" --zone kitchen     --originals "$PROJECT/vggt-examples/examples/kitchen/images" --set data/lvis/set.json --img data/lvis/img --other "$OTHER"     --work out/full-cpu --device cpu --repeat 2 "$@"
echo "== done $(date +%T)"
