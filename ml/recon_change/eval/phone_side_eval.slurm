#!/bin/bash
# Accuracy of the phone-side position fix on the walking test, on CPU cores. Submit from the
# project folder on Iridis, after phone_side_recon.slurm and phone_side_fetch.sh:
#   sbatch --cpus-per-task=48 phone/code/phone_side_eval.slurm vits:16:6 vitb:16:0 xfeat:8:6 sift:8:0
# Each argument is VARIANT:CORES:DENSE. The variants run side by side, each pinned to its own
# cores: first the features of every baseline photo and query frame (and, with DENSE > 0, of every
# DENSE-th frame of the query recordings, for the replay), then match + pose for every query.
#SBATCH --job-name=jun-phone-eval
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=48
#SBATCH --mem=96G
#SBATCH --time=00:45:00
#SBATCH --output=phone/logs/slurm-%x-%j.out
set -uo pipefail
cd "$SLURM_SUBMIT_DIR"
export PYTHONPATH="$PWD/items/pkgs" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
echo "== $(hostname) $(date +%T) cores=$SLURM_CPUS_PER_TASK: $*"
lscpu | grep -E "Model name|^CPU\(s\)|Thread"
CPUS=($(.venv-recon/bin/python -c "import os; print(*sorted(os.sched_getaffinity(0)))"))
next=0
for spec in "$@"; do
    IFS=: read -r variant n dense <<< "$spec"
    slice=("${CPUS[@]:$next:$n}")
    next=$((next + n))
    list=$(IFS=,; echo "${slice[*]}")
    (
        export OMP_NUM_THREADS="$n" MKL_NUM_THREADS="$n"
        taskset -c "$list" .venv-recon/bin/python -u phone/code/phone_side_eval.py --extract "$variant" --threads "$n" --dense "${dense:-0}"
        export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
        taskset -c "$list" .venv-recon/bin/python -u phone/code/phone_side_eval.py --score "$variant" --procs "$n"
    ) > "phone/logs/eval-$variant.log" 2>&1 &
done
wait
echo "== done $(date +%T)"
