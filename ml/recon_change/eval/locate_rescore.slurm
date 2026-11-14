#!/bin/bash
# Score reconstructions saved by locate_dump.slurm on CPU cores, no GPU. Copy locate_eval.py,
# run_bench.py, lookalike_sweep.py and this file into <work folder>/loclook/eval/, and the locate.py
# and recon_change.py under test into a folder of their own (say loclook/new). From the work folder:
#   sbatch loclook/eval/locate_rescore.slurm sweep loclook/new loclook/sweep     # lookalike_sweep.py
#   sbatch loclook/eval/locate_rescore.slurm eval loclook/new loclook/out_new    # locate_eval.py tables
# Arguments: what to run, the folder with the locate.py to use, the output folder (emptied first).
#SBATCH --job-name=jun-locscore
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=48G
#SBATCH --time=00:20:00
#SBATCH --output=loclook/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
PY=".venv-recon/bin/python -I -u"
echo "== $(hostname) $(date +%T) $*"
rm -rf "$3"
mkdir -p "$3"
if [ "$1" = sweep ]; then
  $PY loclook/eval/lookalike_sweep.py --dump loclook/dump --out "$3" --workers 16 --detector-dir "$2"
else
  N=15
  for i in $(seq 0 $((N - 1))); do
    $PY loclook/eval/locate_eval.py --data bench/data/PASLCD --poses loceval/poses --from-dump loclook/dump --rounds 18 \
      --detector-dir "$2" --out "$3/shard$i" --shard "$i/$N" > "$3/shard$i.log" 2>&1 &
  done
  wait
  cat "$3"/shard*/results.jsonl > "$3/results.jsonl"
  cp "$3/shard0/meta.json" "$3/meta.json"
  $PY loclook/eval/locate_eval.py --out "$3" --summarise > /dev/null
  grep -c . "$3/results.jsonl"
  grep -l FAILED "$3"/shard*.log || true
fi
echo "== done $(date +%T)"
