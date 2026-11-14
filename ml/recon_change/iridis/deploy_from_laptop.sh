#!/bin/bash
# Deploy the working branch's vision stage to the cluster and restart both workers.
# Run on the laptop from ml/recon_change, with the VPN connected:   bash <this file>
set -e
R=iridis:/scratch/ase1g23/junction-recon
scp -q -o BatchMode=yes recon_change.py recon_rust.py locate.py items.py watch.py objects.py splat.py "$R/"
scp -q -o BatchMode=yes live/worker.py live/gateway.py "$R/live/"
scp -q -o BatchMode=yes iridis/worker.slurm iridis/worker_cpu.slurm iridis/worker_env.sh iridis/restart_workers.sh "$R/iridis/"
ssh -o BatchMode=yes iridis "cd /scratch/ase1g23/junction-recon && \
  sed -i 's/\r\$//' recon_change.py recon_rust.py locate.py items.py watch.py objects.py splat.py live/worker.py live/gateway.py iridis/worker.slurm iridis/worker_cpu.slurm iridis/worker_env.sh iridis/restart_workers.sh && \
  .venv-recon/bin/python -m py_compile recon_change.py recon_rust.py locate.py items.py watch.py objects.py splat.py live/worker.py live/gateway.py && \
  .venv-recon/bin/python -c 'import recon_change, locate, items, watch, objects; print(\"imports work on the cluster\")' && \
  bash iridis/restart_workers.sh"
