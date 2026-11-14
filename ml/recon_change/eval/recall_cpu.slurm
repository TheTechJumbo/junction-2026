#!/bin/bash
# Score the detector again from reconstructions saved by recall_gpu.slurm: CPU cores only.
# Submit from the work folder:
#   sbatch recall/recall_cpu.slurm recall/run_bench.py --from-recon recall/out/base --out recall/out/try1
# The first argument is the script to run, the rest are its arguments.
#SBATCH --job-name=jun-recall-cpu
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=00:19:00
#SBATCH --output=recall/logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
echo "== $(hostname) $(date +%T)"
.venv-recon/bin/python -I -u "$@"
echo "== done $(date +%T)"
