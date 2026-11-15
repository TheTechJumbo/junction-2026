#!/bin/bash
# Seconds per frame and peak memory of the phone side on 2, 4 and 8 pinned CPU cores, one run
# after the other so they do not disturb each other. Submit from the project folder on Iridis:
#   sbatch phone/code/phone_side_bench.slurm phone/code/bench_plan.txt
# The plan file has one run per line: VARIANT then the JSON of the settings, for example
#   vits {"k": 3, "reproj": 12, "dim": 0}
# Results: phone/out/bench.jsonl (one line per variant and core count).
#SBATCH --job-name=jun-phone-bench
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G
#SBATCH --time=00:40:00
#SBATCH --output=phone/logs/slurm-%x-%j.out
set -uo pipefail
cd "$SLURM_SUBMIT_DIR"
export PYTHONPATH="$PWD/items/pkgs" HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
echo "== $(hostname) $(date +%T) cores=$SLURM_CPUS_PER_TASK"
lscpu | grep -E "Model name|Socket|Core\(s\)|Thread|^CPU\(s\)|L3|MHz"
# One logical CPU per physical core (as cpu/bench_cpu.slurm).
CPUS=($(.venv-recon/bin/python - <<'PYEOF'
import os
seen, cores = set(), []
for c in sorted(os.sched_getaffinity(0)):
    try:
        siblings = open(f"/sys/devices/system/cpu/cpu{c}/topology/thread_siblings_list").read().strip()
    except OSError:
        siblings = str(c)
    if siblings not in seen:
        seen.add(siblings)
        cores.append(c)
print(*cores)
PYEOF
))
echo "physical cores in this job: ${#CPUS[@]}"
OUT="${BENCH_OUT:-phone/out/bench.jsonl}"
for n in ${BENCH_CORES:-2 4 8}; do
    slice=("${CPUS[@]:0:$n}")
    if [ "${#slice[@]}" -lt "$n" ]; then echo "not enough cores for $n"; continue; fi
    list=$(IFS=,; echo "${slice[*]}")
    export OMP_NUM_THREADS="$n" MKL_NUM_THREADS="$n" OPENBLAS_NUM_THREADS="$n"
    while IFS= read -r line || [ -n "$line" ]; do
        [ -z "$line" ] && continue
        variant="${line%% *}"
        settings="${line#* }"
        echo "== $(date +%T) cores $n (cpus $list): $variant $settings"
        taskset -c "$list" .venv-recon/bin/python -u phone/code/phone_side_bench.py --variant "$variant" --threads "$n" \
            --settings "$settings" --out "$OUT" 2>&1 | tail -n 3
    done < "$1"
done
echo "== done $(date +%T)"
