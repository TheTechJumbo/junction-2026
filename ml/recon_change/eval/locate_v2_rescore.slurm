#!/bin/bash
# Score saved reconstructions with a new locate.py on CPU cores, no GPU. Two jobs in one file:
#   paslcd  the PASLCD passes saved under loclook/dump (locate_dump.slurm), with locate_eval.py
#   walk    the walking-test fixes saved under walk2/dump (locate_walk_dump.slurm), with
#           locate_walk_eval.py; extra arguments go to it (--locate-args, --known-pair)
# In <work folder>/walk2/: code/ holds the eval scripts (locate_eval.py, run_bench.py,
# locate_walk_eval.py) and the locate.py and recon_change.py under test. Submit from the work folder:
#   sbatch walk2/code/locate_v2_rescore.slurm paslcd walk2/paslcd_v2
#   sbatch walk2/code/locate_v2_rescore.slurm walk walk2/out_dump_v2
#   sbatch walk2/code/locate_v2_rescore.slurm walk walk2/out_dump_known --known-pair
# Arguments: which job, the output folder (emptied first), then extra arguments.
#SBATCH --job-name=jun-locate-v2
#SBATCH --account=student
#SBATCH --partition=amd_student
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=64G
#SBATCH --time=00:45:00
#SBATCH --output=walk2/logs/slurm-%x-%j.out
set -euo pipefail
cd "$SLURM_SUBMIT_DIR"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
PY="$PWD/.venv-recon/bin/python -I -u"
WHAT="$1"
OUT="$2"
shift 2
echo "== $(hostname) $(date +%T) $WHAT $OUT $*"
rm -rf "$OUT"
mkdir -p "$OUT"
N=30
for i in $(seq 0 $((N - 1))); do
  if [ "$WHAT" = paslcd ]; then
    $PY walk2/code/locate_eval.py --data bench/data/PASLCD --poses loceval/poses --from-dump loclook/dump --rounds 18 \
      --detector-dir walk2/code --out "$OUT/shard$i" --shard "$i/$N" > "$OUT/shard$i.log" 2>&1 &
  else
    $PY walk2/code/locate_walk_eval.py --data walk/data/tum --plan walk/out/plan.json --from-dump walk2/dump --frames 12+1 \
      --detector-dir walk2/code --out "$OUT/shard$i" --shard "$i/$N" --minutes 40 "$@" > "$OUT/shard$i.log" 2>&1 &
  fi
done
wait
cat "$OUT"/shard*/results.jsonl > "$OUT/results.jsonl"
cp "$OUT/shard0/meta.json" "$OUT/meta.json"
echo "$(grep -c . "$OUT/results.jsonl") rows"
grep -l "FAILED\|Traceback" "$OUT"/shard*.log || true
echo "== done $(date +%T)"
