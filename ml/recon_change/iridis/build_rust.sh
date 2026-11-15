#!/usr/bin/env bash
# Explicit Python and preinstalled toolchain; no toolchain or dependency downloads.
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${1:-$(pwd)/.venv-recon/bin/python}"
[[ "$PY" = /* && -x "$PY" ]] || { echo 'Python must be an executable absolute path' >&2; exit 1; }
command -v cargo >/dev/null
command -v rustc >/dev/null
export PATH="$(dirname "$PY"):$PATH"
export CARGO_NET_OFFLINE=true
export PYO3_PYTHON="$PY"
export MATURIN_PEP517_ARGS="--locked"
"$PY" --version
cargo --version
rustc --version
"$PY" -m pip show maturin numpy
# Build dependencies must already be installed; Cargo.lock and cached crates are required.
"$PY" -m pip install --no-deps --no-build-isolation --no-index --force-reinstall ./rust
"$PY" -c 'import recon_fast; print("recon_fast threads:", recon_fast.threads())'
"$PY" test_rust.py
"$PY" test_synthetic.py
