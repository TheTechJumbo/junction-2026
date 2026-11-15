# Event API + relay on the cluster (built and tested, cut-over PAUSED)

Goal: the laptop runs no server, only an SSH tunnel; the phone keeps using http://127.0.0.1:8000 and :8090 (adb reverse).

## What's built (ml/recon_change/, all new except a 3-line `--cluster` entry in run_demo.sh)
- `cluster/servers.slurm`: CPU job `jun-servers` (amd_student, account student, 8 CPUs, 24 GB, 24 h). Runs the event API
  (uvicorn, node port 28000) and the relay (node port 28090, `--local` on /scratch/ase1g23/junction-recon, so the gateway is
  in-process and uses the GPU workers' queue). Restarts either server if it dies, and queues its own replacement
  (SERVERS_LEFT=2 more). Secrets come from `.servers.env` (mode 600) as env vars only. Logs in `servers-logs/` (700), keys masked.
- `cluster/servers.sh start|stop|restart|status|node`: run on the login node. `stop` cancels the queued replacement first.
- `cluster/setup_api_env.sh`: venv `/scratch/ase1g23/junction-api/.venv-api` (uv Python 3.12, pinned runtime packages
  from requirements-dev.lock, `--no-deps`). DONE (Seb approved the 22 packages plus opentelemetry-api).
- `live/relay_cluster.py`: launcher around an unchanged relay.py. Reads the key from RELAY_KEY (on compute nodes /proc
  is not hidden, so anyone could read a command line). `--train-host local` makes remote_train's ssh/scp steps run on
  the node, submitting video_map.slurm with sbatch. `RELAY_ANONYMISE=1` runs live/anonymise.py on every arriving photo
  and frame and fails closed (off by default).
- `tunnel_cluster.sh [start|stop|status|restart|deploy]`: laptop side. `start` submits the job if it's missing, then
  forwards 0.0.0.0:8000/8090 to the job's node, follows it to a new node, and re-runs adb reverse. `deploy` copies the
  relay files, backend/contracts/dashboard and the two secrets (never printed).

## Tested (synthetic zones verify-synthetic and one other test zone only; no real data moved)
- `verify_demo.sh --relay http://127.0.0.1:18190 --api http://127.0.0.1:18100` through the tunnel: 17/17 PASS.
  Fix 4.4 s, 0.05 m / 1 deg off; instant fixes median 268 ms; /ask answered by the cluster's API.
- Tunnel cost: /health median 215 ms (155-305) vs 7 ms locally. Every phone request pays it.
- Recording -> map -> training by sbatch from the node: 363 s; splat, WebM fly-through and all keyframes came back.
- Job replaced -> the tunnel reconnects to the new node by itself (about 1 min). Pages load, including /care.
- Cluster facts: sbatch needs `--nodes`; the serial partition refuses our account; compute nodes have no internet (no
  ElevenLabs/Gemini from there); SQLite WAL works on /scratch.

## Running now
- `jun-servers-test` (a CPU job, 3 h limit, node ports 28100/28190, test state in
  junction-recon/relay-state-test and junction-api/.data-test). Stop: `ssh cluster scancel <its job id>`.
- Laptop test tunnel on 127.0.0.1:18100/18190. Stop: `SERVERS_NAME=jun-servers-test TUNNEL_API_PORT=18100 bash ml/recon_change/tunnel_cluster.sh stop`.
- No real `jun-servers` job. (jun-worker, video-map, privacy-* jobs belong to other sessions.)

## Why paused
Seb treats the GPU cluster as untrusted. This design puts the relay, which sees raw photos and keeps the evidence folder, on
the cluster. Wait for the privacy design. If the relay stays on the laptop, only the API half applies, and it would then
need a reverse tunnel (-R) to reach the laptop relay. True compute-on-encrypted options: see the privacy options notes (kept outside the repo).

## Cut-over, once unpaused (ask the parent first; the phone drops for 2-3 min)
1. Stop the test pieces (above). `bash ml/recon_change/tunnel_cluster.sh deploy`.
2. Pre-copy while the laptop still serves: `touch $TMPDIR/migrate.mark`;
   `tar -cf - -C /c/Users/User/AppData/Local/Temp beacon-evidence | ssh cluster 'tar -xf - -C /scratch/ase1g23/junction-recon/relay-state'`;
   `tar -cf - -C ~ beacon-local | ssh cluster 'mkdir -p /scratch/ase1g23/junction-recon/laptop && tar -xf - -C /scratch/ase1g23/junction-recon/laptop'`
   (kept apart: don't overwrite the live baselines/ or recordings/).
3. Stop the laptop's relay (detached python live/relay.py), its SSH gateway link, the event API on :8000 and any run_demo.sh.
4. Delta and database: `cd /c/Users/User/AppData/Local/Temp && find beacon-evidence -newer $TMPDIR/migrate.mark -type f | tar -cf - -T - | ssh cluster 'tar -xf - -C /scratch/ase1g23/junction-recon/relay-state'`;
   `tar -cf - .data | ssh cluster 'tar -xf - -C /scratch/ase1g23/junction-api'` (from the repo root; events.sqlite3 + -wal + -shm).
5. `bash ml/recon_change/tunnel_cluster.sh` (in a terminal tab). Allow ssh.exe in the Windows Firewall prompt (Wi-Fi fallback, pill box board).
6. Check through 127.0.0.1: /health, /status (workers), /room, /recording, /waypoints, /care?zone=kitchen, /locate on
   verify-synthetic with fresh=1 (not kitchen), dashboard + viewer pages. Then no python servers on the laptop, `nvidia-smi` idle.
Stop everything: `bash ml/recon_change/tunnel_cluster.sh stop`. If the job ends: it requeues itself; else re-run step 5.
