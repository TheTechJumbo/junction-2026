#!/bin/bash
# The live vision worker on CPU cores only, no GPU. Same script as the GPU worker; it serves its
# own queue (queue-cpu/), so a check can be sent to either. Fewer frames at full size keep it
# quick and reliable: the CPU benchmark measured 8+4 frames at long side 518 at 34 s per check on
# 32 EPYC cores with the GPU's answer, while smaller frames made the depth too noisy.
# Override with RECON_FRAMES="baseline,new" and RECON_LONG_SIDE.
#   sbatch iridis/worker_cpu.slurm            # start
#   touch STOP-cpu   or   scancel <job id>     # stop
#SBATCH --job-name=jun-worker-cpu
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=48G
#SBATCH --time=02:00:00
#SBATCH --output=slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
export RECON_DEVICE=cpu RECON_FRAMES="${RECON_FRAMES:-8,4}" RECON_LONG_SIDE="${RECON_LONG_SIDE:-518}"
export CUDA_VISIBLE_DEVICES=""
echo "== $(hostname) $(date +%T) | $(lscpu | grep 'Model name' | sed 's/.*: *//') | $SLURM_CPUS_PER_TASK cores | frames $RECON_FRAMES at $RECON_LONG_SIDE px"
.venv-recon/bin/python -u live/worker.py
echo "== done $(date +%T)"
