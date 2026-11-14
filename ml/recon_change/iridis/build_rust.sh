#!/usr/bin/env bash
# Build the Rust part of the change check (rust/, JUN-28) into .venv-recon. No root needed:
# Rust goes into scratch (or the home directory) with rustup. Run once on a login node, after
# setup_env.sh, and again after rust/ changes. Restart the workers afterwards to pick it up.
#   bash iridis/build_rust.sh
# Undo: .venv-recon/bin/python -m uv pip uninstall recon_fast, or RECON_FAST=0 in the worker job.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d "/scratch/$USER" ]; then  # keep the toolchain and build files off the small home quota
    export RUSTUP_HOME="${RUSTUP_HOME:-/scratch/$USER/.rustup}"
    export CARGO_HOME="${CARGO_HOME:-/scratch/$USER/.cargo}"
    export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-/scratch/$USER/.cache/recon_fast-target}"
    export UV_CACHE_DIR="${UV_CACHE_DIR:-/scratch/$USER/.cache/uv}"
fi
export CARGO_HOME="${CARGO_HOME:-$HOME/.cargo}"
export PATH="$CARGO_HOME/bin:$HOME/.local/bin:$PATH"
PY=.venv-recon/bin/python

echo "== rust $(date +%T)"
if ! command -v cargo >/dev/null; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path
fi
cargo --version

echo "== build $(date +%T)"
# --no-cache: always rebuild from the current rust/ folder instead of a cached wheel.
uv pip install --python $PY --no-cache ./rust

echo "== check $(date +%T)"
$PY -c "import recon_fast; print('recon_fast loaded,', recon_fast.threads(), 'threads on this node')"
$PY test_rust.py
echo "== ready $(date +%T): restart the workers (scancel, then sbatch iridis/worker.slurm) to use it"
