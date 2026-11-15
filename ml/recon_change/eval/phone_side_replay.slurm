#!/bin/bash
# The phone-side fix on every 6th frame of the query recordings (see phone_side_replay.py). Needs the
# features of phone_side_eval.slurm run with DENSE = 6 and phone/out/chosen.json of phone_side_tables.py.
#   sbatch phone/code/phone_side_replay.slurm vits xfeat
#SBATCH --job-name=jun-phone-replay
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=phone/logs/slurm-%x-%j.out
set -uo pipefail
cd "$SLURM_SUBMIT_DIR"
export PYTHONPATH="$PWD/items/pkgs" OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
echo "== $(hostname) $(date +%T): $*"
for variant in "$@"; do
    .venv-recon/bin/python -u phone/code/phone_side_replay.py --run "$variant" --chosen phone/out/chosen.json > "phone/logs/replay-$variant.log" 2>&1 &
done
wait
tail -n 3 phone/logs/replay-*.log
echo "== done $(date +%T)"
