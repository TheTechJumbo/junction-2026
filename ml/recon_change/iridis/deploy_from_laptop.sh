#!/bin/bash
# Deploy the working branch's vision stage to the GPU machine and restart both workers. The splat training of
# recordings (video_map.py, iridis/video_map.slurm, started by live/remote_train.py) runs from this same folder.
# Run on the laptop from ml/recon_change, with the VPN connected:   bash <this file>
# Another machine later (a Verda GPU machine, say): BEACON_TRAIN_HOST=its SSH alias BEACON_REMOTE_ROOT=its folder;
# the restart at the end uses Slurm (restart_workers.sh), so on a machine without it start live/worker.py there yourself.
set -e
HOST="${BEACON_TRAIN_HOST:-iridis}"
ROOT="${BEACON_REMOTE_ROOT:-/scratch/ase1g23/junction-recon}"
R="$HOST:$ROOT"
PYFILES="recon_change.py recon_rust.py locate.py items.py watch.py objects.py splat.py video_map.py"
SCRIPTS="iridis/worker.slurm iridis/worker_cpu.slurm iridis/worker_env.sh iridis/restart_workers.sh iridis/video_map.slurm"
scp -q -o BatchMode=yes $PYFILES "$R/"
scp -q -o BatchMode=yes live/worker.py live/gateway.py "$R/live/"
scp -q -o BatchMode=yes $SCRIPTS "$R/iridis/"
ssh -o BatchMode=yes "$HOST" "cd $ROOT && \
  sed -i 's/\r\$//' $PYFILES live/worker.py live/gateway.py $SCRIPTS && \
  .venv-recon/bin/python -m py_compile $PYFILES live/worker.py live/gateway.py && \
  .venv-recon/bin/python -c 'import recon_change, locate, items, watch, objects; print(\"imports work on the cluster\")' && \
  bash iridis/restart_workers.sh"
