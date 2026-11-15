#!/bin/bash
# Run CPU benchmark settings on an AMD CPU node of the cluster (no GPU). Submit from the folder that holds
# recon_change.py and cpu/, with the three test clips in cpu/clips/:
#   sbatch --cpus-per-task=4 --mem=24G cpu/bench_cpu.slurm 4:cpu/plans/c4_294a.txt
#   sbatch --cpus-per-task=60 --mem=120G cpu/bench_cpu.slurm 32:cpu/plans/a.txt,cpu/plans/b.txt 16:cpu/plans/c.txt 4:cpu/plans/d.txt
# Each argument is CORES:PLAN[,PLAN...]. The job's cores are split into one pinned group per
# argument and the groups run side by side (the QoS allows only a few jobs at once). A plan
# file has one setting per line: the extra arguments for bench_cpu.py. Each line runs in its own
# process, so peak memory is per setting. Results go to cpu/results/<plan>.jsonl.
#SBATCH --job-name=jun-cpu
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:19:00
#SBATCH --output=cpu/logs/slurm-%x-%j.out
set -uo pipefail
cd "$SLURM_SUBMIT_DIR"
mkdir -p cpu/results cpu/logs
# Compute nodes have no internet: use the weights setup_env.sh downloaded to scratch.
export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}" HF_HUB_OFFLINE=1
echo "== $(hostname) $(date +%T) cores=$SLURM_CPUS_PER_TASK groups: $*"
lscpu | grep -E "Model name|Socket|Core\(s\)|Thread|^CPU\(s\)|L3"
# One logical CPU per physical core: on nodes with hyper-threading, request 2 CPUs per core wanted.
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
next=0
group=0
for spec in "$@"; do
    n="${spec%%:*}"
    plans="${spec#*:}"
    slice=("${CPUS[@]:$next:$n}")
    next=$((next + n))
    group=$((group + 1))
    list=$(IFS=,; echo "${slice[*]}")
    if [ "${#slice[@]}" -lt "$n" ]; then echo "not enough cores left for $spec"; continue; fi
    (
        export OMP_NUM_THREADS="$n" MKL_NUM_THREADS="$n"
        for plan in ${plans//,/ }; do
            out="cpu/results/$(basename "$plan" .txt).jsonl"
            while IFS= read -r line || [ -n "$line" ]; do
                [ -z "$line" ] && continue
                echo "== $(date +%T) cores $n (cpus $list): $line"
                # shellcheck disable=SC2086
                taskset -c "$list" .venv-recon/bin/python -u cpu/bench_cpu.py --baseline cpu/clips/baseline.mp4 \
                    --threads "$n" --out "$out" $line
                code=$?
                if [ $code -ne 0 ]; then  # killed (out of memory, ...): still leave a line in the results
                    echo "{\"args\": \"$line\", \"cores_used\": $n, \"error\": \"exit code $code\", \"passes\": false}" | tee -a "$out"
                fi
            done < "$plan"
        done
    ) > "cpu/logs/group-$SLURM_JOB_ID-$group-c$n.log" 2>&1 &
done
wait
echo "== done $(date +%T)"
