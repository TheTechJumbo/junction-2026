#!/bin/bash
# Contact sheets of watched spots against the labels (watch_sheets.py), on CPU cores.
# Submit from the work folder; arguments go to watch_sheets.py after the fixed ones:
#   sbatch watch/code/watch_sheets.slurm --scene Lounge --live
#SBATCH --job-name=jun-watch-sheet
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:20:00
#SBATCH --output=watch/logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
echo "== $(hostname) $(date +%T)"
.venv-recon/bin/python -I -u watch/code/watch_sheets.py --recon recall/out/base --data bench/data/PASLCD --out watch/out "$@"
echo "== done $(date +%T)"
