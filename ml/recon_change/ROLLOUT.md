# Local preparation and Seb's live handoff

This is a provisional, local preparation. No GPU, worker, live relay or provider was touched.
JUN-28 remains open pending live acceptance. Use public fixtures only; do not upload household imagery.

## Existing Linux GPU host (including Verda)

Use an existing instance and SSH key. Verda's [GPU setup](https://docs.verda.com/cpu-and-gpu-instances/set-up-a-gpu-instance/) and [SSH connection](https://docs.verda.com/cpu-and-gpu-instances/connecting-to-your-verda-server/) instructions describe access and storage. Provisioning is outside this handoff. Put checkout, venv, queue and baselines on the same persistent filesystem; gateway and worker must use the same checkout root (worker derives its root from its file, gateway defaults to the checkout). Preserve existing baselines and avoid concurrent workers on the same queue. Keep caches persistent; set HF_HOME and RECON_ITEMS_CACHE consistently before setup and startup.

The known worker dependencies are Python 3.12, torch 2.3.1 and torchvision 0.18.1 CUDA 12.1, NumPy <2, and VGGT. The following is a concrete starting recipe for an authorized internet-connected setup, not a claim of a verified Verda installation. Use a dedicated venv, preserve the package inventory (`pip freeze`), pin its resolved transitive packages and model snapshot revisions before a live rollout, and retain that inventory for rollback. These direct pins include the existing Iridis item layer. The VGGT revision is the upstream HEAD inspected on 2026-11-14, not yet validated on this host. Do not run the Iridis setup scripts unchanged on Verda.

```sh
cd /absolute/persistent/path/ml/recon_change
python3.12 -m venv .venv-recon
P="$(pwd)/.venv-recon/bin/python"
"$P" -m pip install 'pip==25.3' 'maturin==1.16.0'
"$P" -m pip install --index-url https://download.pytorch.org/whl/cu121 'torch==2.3.1' 'torchvision==0.18.1'
"$P" -m pip install 'numpy==1.26.4' 'opencv-python-headless==4.10.0.84' 'pillow==10.4.0' 'huggingface_hub==0.26.5' 'einops==0.8.0' 'safetensors==0.4.5'
"$P" -m pip install --no-deps 'git+https://github.com/facebookresearch/vggt.git@a288dd0f14786c93483e45524328726ab7b1b4ce'
mkdir -p items/pkgs items/hf
"$P" -m pip install --target items/pkgs --no-deps 'transformers==4.46.3' 'tokenizers==0.20.3' 'huggingface_hub==0.26.5' 'regex==2024.11.6' 'requests==2.32.3' 'urllib3==2.2.3' 'certifi==2024.8.30' 'charset-normalizer==3.4.0'
"$P" -m pip freeze > installed-packages.txt
export HF_HOME=/persistent/huggingface
export PYTHONPATH="$(pwd)/items/pkgs"
"$P" -c "from huggingface_hub import snapshot_download; print(snapshot_download('facebook/VGGT-1B'))"
HF_HOME="$(pwd)/items/hf" "$P" -c "from huggingface_hub import snapshot_download; [print(snapshot_download(x)) for x in ('google/owlv2-base-patch16-ensemble','openai/clip-vit-large-patch14')]"
```

Use an already installed Rust toolchain and record resolved model snapshot revisions. Verify GPU driver/CUDA compatibility. The package and model setup above and explicit Cargo fetch below require network; the build and preflight do not.

From the persistent checkout's `ml/recon_change` directory, install the pinned environment and cache separately, then build with an already installed Rust toolchain, maturin and cached Cargo crates:

```sh
export CARGO_HOME=/persistent/cargo RUSTUP_HOME=/persistent/rustup
export PATH="$CARGO_HOME/bin:$PATH"
cargo fetch --locked --manifest-path rust/Cargo.toml
bash iridis/build_rust.sh "$(pwd)/.venv-recon/bin/python"
source iridis/worker_env.sh
export HF_HOME=/persistent/huggingface
export PYTHONPATH="$(pwd)/items/pkgs${PYTHONPATH:+:$PYTHONPATH}"
export RECON_ITEMS_CACHE="$(pwd)/items/hf/hub"
.venv-recon/bin/python iridis/preflight.py
```

Build is offline and locked; versions and equivalence test are printed. Preflight is offline, checks imports, Rust, settings, source and local model snapshot inventory; it is not a GPU, model-load or inference test. Deploy a clean committed checkout; the revision and tracked-dirty flag do not detect untracked files. Resolve failures before changing a running process. Validate the actual loaded model and item settings in `worker.json` and with public fixtures after launch.

For a non-Slurm Linux host, install this example as a user systemd unit after substituting the absolute paths and confirming the environment. Run `systemctl --user daemon-reload` and, in the authorized live window, `systemctl --user start junction-worker`; inspect `systemctl --user status junction-worker`, `journalctl --user -u junction-worker` and `worker.json`. Configure one worker per queue.

```ini
[Unit]
Description=Junction vision worker
[Service]
Type=simple
WorkingDirectory=/absolute/persistent/path/ml/recon_change
Environment=HF_HOME=/persistent/huggingface
Environment=HF_HUB_OFFLINE=1
Environment=RECON_DEVICE=cuda
Environment=PYTHONPATH=/absolute/persistent/path/ml/recon_change/items/pkgs
Environment=RECON_ITEMS_CACHE=/absolute/persistent/path/ml/recon_change/items/hf/hub
Environment=RECON_ITEMS_ENV=items
Environment=RECON_ITEMS_LIMITS=keys=0.425,glasses=0.225,wallet=0.275,phone=0.4
Environment=RECON_ITEMS_LOOK_FROM=0.25
Environment=RECON_ITEMS_JUDGE=openai/clip-vit-large-patch14
Environment="RECON_ITEMS_JUDGE_LIMITS=keys=0.7,glasses=0.1,wallet=0.1,phone=0.35,remote control=0.5"
Environment=RECON_ITEMS_CANDIDATES=0.25
ExecStartPre=/absolute/persistent/path/ml/recon_change/.venv-recon/bin/python iridis/preflight.py
ExecStart=/absolute/persistent/path/ml/recon_change/.venv-recon/bin/python -u live/worker.py
Restart=on-failure
[Install]
WantedBy=default.target
```

For the item limit containing a space, quote the entire systemd Environment assignment: `Environment="RECON_ITEMS_JUDGE_LIMITS=keys=0.7,glasses=0.1,wallet=0.1,phone=0.35,remote control=0.5"`. The gateway stays SSH stdio: on the relay machine run `python live/relay.py --ssh-host HOST --remote-root /absolute/persistent/path/ml/recon_change` (without `--remote-root` it retains the legacy Iridis path). Host key verification and authorized SSH account remain host responsibilities. The gateway and worker share checkout queue/baselines. Avoid BEACON_ROOT overrides because the worker uses its checkout root.

On Iridis, **do not use** the legacy `deploy_from_laptop.sh` or `restart_workers.sh`: the latter holds unrelated jobs. Run preflight first. Inspect `squeue -u "$USER" -o '%i %j %T %Z'` and verify the working directory of each current and queued `jun-worker`/`jun-worker-cpu` job belongs to this checkout. In the authorized live window, cancel only those explicitly verified IDs (`scancel ID ...`); wait until all are absent from `squeue` and verify no worker remains. Remove any stale STOP and STOP-cpu markers only after confirming the old workers exited, then `source iridis/worker_env.sh` and submit one each with `sbatch iridis/worker.slurm` and `sbatch iridis/worker_cpu.slurm`. Inspect job state, logs, worker heartbeat and worker.json before considering replacement jobs. Do not hold or cancel unrelated jobs.

For public fixtures, set `RELAY_TOKEN` privately and run `python live/benchmark.py --base-url http://127.0.0.1:8090 --zone PUBLIC_ZONE --check /path/public-check.zip --locate /path/public-locate.jpg --repeat 3 --expect-rust on`. Baseline the public zone beforehand through the existing relay protocol. The tool requests a fresh locate, rejects reused results, writes only timing and selected runtime metadata to stdout, follows no redirects, and sends the token in a header. Check reported `rust_enabled`, device and settings; missing metadata cannot prove which worker ran. Record hardware, software versions, model/cache revisions, frame counts, client wall time and server timings for both Rust on and off. Do not interpret local synthetic timings as live GPU timings.

Rollback: stop only the new worker(s) using the supervisor or selected scheduler IDs, restore the prior checkout and matching environment, or set `RECON_FAST=0` to use NumPy. For the systemd unit, add `Environment=RECON_FAST=0` and change the preflight command to append `--expect-rust off`, then daemon-reload; perform the equivalent environment and preflight changes for other supervisors. Restart those project workers and verify their heartbeat, settings and public-fixture checks. Do not delete persistent baselines or caches. The previously running code/environment should be retained before rollout so rollback is possible.

## Local synthetic measurement (2026-11-14)

Apple Silicon Mac, 8 logical CPUs, Python 3.12.13, NumPy 2.5.4, Rust 1.100.0, maturin 1.16.0; `bench_rust.py --sizes 294x518 --threads 4 --repeat 3`. With 24+12 synthetic frames and look-alike off: median NumPy 5.55 s, all Rust 0.94 s; on: 7.33 s versus 0.84 s. Reported peak extra RSS off: 176 versus 173 MB; on: 473 versus 251 MB. Verdict and object counts matched. Each mode runs in a fresh process on the same generated fixture; peak RSS minus current RSS after loading is an estimate, not allocation tracing. Three trials are exploratory and exclude VGGT, network, queue and GPU time. NumPy 2.5.4 on this Mac differs from the intended Linux NumPy 1.26.4 environment; rerun equivalence and performance there. The equivalence suite additionally checked the intermediate arrays.

## Local acceptance record

Implementation reviewed by Astra; Terra implemented and tested in isolated branch `codex/rust-rollout-prep`.

- `bash iridis/build_rust.sh /tmp/junction-rollout-venv/bin/python`: offline locked build, `test_rust.py` and `test_synthetic.py` passed after explicit dependency/cache preparation.
- `python -m unittest discover -s ml/recon_change -p test_rollout.py -v`: 7 passed, including missing-helper, missing/ABI-incompatible extension, enabled/disabled modes, absent settings/source/Rust and quoted remote path.
- `python -m unittest discover -s ml/recon_change/live -p test_benchmark.py -v`: passed, including real local HTTP success, fresh locate, invalid metadata, reused response and redirect refusal.
- `live/test_care_flow.py`, `live/test_day_flow.py`, `live/test_setup_flow.py`, `live/test_view_gate.py`: passed in the isolated local environment.
- `test_watch.py`, `eval/test_locate_lookalike.py`, `test_synthetic.py`: passed with both `RECON_FAST=1` and `RECON_FAST=0`, four threads.
- `make check VENV=/Users/ayman/Desktop/junction-2026/.venv/bin`: lint and schemas passed; 470 tests passed, 12 deselected; existing httpx deprecation warning.
- `git diff --check`: passed.

The dependency/build environment is local to this Mac. The positive full model-cache preflight, Linux/CUDA installation, GPU correctness, actual service start/rollback and live HTTP performance have not been run. Seb must complete those gates on the chosen host. PR #37's missing-helper behavior is incorporated with a narrower exception handler; its unrelated dependency import failures remain visible.
