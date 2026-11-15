#!/bin/bash
# Restart both live workers so they load the newly deployed files. Run on the cluster login node,
# from /scratch/$USER/junction-recon. Other pending GPU jobs are held for the minute this takes, so the
# live worker gets its GPU back first, and are released again at the end.
source /etc/profile
cd /scratch/$USER/junction-recon || exit 1
source cluster/worker_env.sh

running_workers() { squeue -u "$USER" -h -n jun-worker,jun-worker-cpu -t RUNNING -o '%i'; }

# 1. The replacements queued behind the current workers would start the moment those stop, and exit at once
#    because the STOP file is still there. Cancel them; fresh ones are queued at the end.
queued=$(squeue -u "$USER" -h -n jun-worker,jun-worker-cpu -t PENDING -o '%i' | tr '\n' ' ')
[ -n "$queued" ] && scancel $queued && echo "cancelled queued replacements: $queued"

# 2. Hold the other pending GPU jobs.
held=$(squeue -u "$USER" -h -p ecsstudents_l4 -t PENDING -o '%i %j' | grep -v ' jun-worker$' | cut -d' ' -f1 | tr '\n' ' ')
[ -n "$held" ] && scontrol hold $held && echo "held for a moment: $held"

# 3. Ask the workers to stop, and wait for them.
touch STOP STOP-cpu
for i in $(seq 1 60); do [ -z "$(running_workers)" ] && break; sleep 2; done
left=$(running_workers | tr '\n' ' ')
[ -n "$left" ] && scancel $left && echo "did not stop in time, cancelled: $left" && sleep 3
rm -f STOP STOP-cpu

# 4. Start new workers with the item finder, and queue their replacements.
gpu=$(sbatch --parsable cluster/worker.slurm)
cpu=$(sbatch --parsable cluster/worker_cpu.slurm)
echo "new workers: GPU $gpu, CPU $cpu"
for i in $(seq 1 30); do [ "$(squeue -h -j "$gpu" -o '%T')" = "RUNNING" ] && break; sleep 2; done
echo "GPU worker state: $(squeue -h -j "$gpu" -o '%T %N')"

# 5. Let the other jobs go again, then queue the replacements.
[ -n "$held" ] && scontrol release $held && echo "released: $held"
sbatch --dependency=afterany:"$gpu" cluster/worker.slurm
sbatch --dependency=afterany:"$cpu" cluster/worker_cpu.slurm
squeue -u "$USER" -h -o '%i %j %T %M %L' | grep -E 'jun-worker'
