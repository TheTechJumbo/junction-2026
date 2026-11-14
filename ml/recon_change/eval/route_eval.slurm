#!/bin/bash
# Measure route.py against the true obstacles of the TUM RGB-D rooms (eval/route_eval.py). CPU only.
# It reads the walking test's folder (plan.json, results.jsonl, fix/*.npz, data/tum) and writes
# only into its own. In <work folder>/route/: code/ holds route_eval.py, locate_walk_eval.py and
# route.py. Submit from route/:
#   sbatch code/route_eval.slurm --out out
# Arguments after the script name go to route_eval.py.
#SBATCH --job-name=jun-route
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=12G
#SBATCH --time=00:40:00
#SBATCH --output=logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
echo "== $(hostname) $(date +%T)"
# -I: the data folder is a download, so Python must not pick up anything from the environment.
../.venv-recon/bin/python -I -u code/route_eval.py --walk ../walk "$@"
echo "== done $(date +%T)"
