#!/bin/bash
# VGGT once over each room's baseline photos (12 and 24), on CPU cores: the "map made once on the
# server" of the phone-side position fix. Submit from the project folder on Iridis:
#   sbatch phone/code/phone_side_recon.slurm
#SBATCH --job-name=jun-phone-recon
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=96G
#SBATCH --time=00:40:00
#SBATCH --output=phone/logs/slurm-%x-%j.out
set -uo pipefail
cd "$SLURM_SUBMIT_DIR"
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
export OMP_NUM_THREADS="$SLURM_CPUS_PER_TASK" MKL_NUM_THREADS="$SLURM_CPUS_PER_TASK"
echo "== $(hostname) $(date +%T) cores=$SLURM_CPUS_PER_TASK"
.venv-recon/bin/python -u phone/code/phone_side_recon.py --data walk/data/tum --plan walk/out/plan.json \
    --out phone/recon --threads "$SLURM_CPUS_PER_TASK" --detector-dir walk2/code
echo "== done $(date +%T)"
