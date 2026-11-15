#!/usr/bin/env bash
# One-off setup for the lost-items detector (items.py), on a cluster login node (compute nodes
# have no internet). It does NOT touch .venv-recon: `transformers` and the few packages it needs
# go into their own folder, which a worker adds with PYTHONPATH, and the OWLv2 weights (about
# 0.6 GB) go into a Hugging Face cache.
#   bash cluster/setup_items.sh                 # into ./items-env next to .venv-recon
#   bash cluster/setup_items.sh /some/folder    # somewhere else
# The worker scripts pick the default folder (./items-env) up by themselves. For another folder:
#   RECON_ITEMS_ENV=/some/folder sbatch cluster/worker.slurm
# which is the same as setting PYTHONPATH=<folder>/pkgs RECON_ITEMS_CACHE=<folder>/hf/hub yourself
# (HF_HOME stays where VGGT is; the detector has its own cache).
# RECON_ITEMS="keys, glasses" changes the list; RECON_ITEMS="" turns the feature off.
# The pins: torch here is 2.3.1, so transformers stays on 4.x; that needs huggingface_hub below
# 1.0, which then shadows the newer one in .venv-recon for this process only (VGGT loads fine with it).
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(realpath -m "${1:-items-env}")"
PY="$(pwd)/.venv-recon/bin/python"
export PATH="$HOME/.local/bin:$PATH"
if [ -d "/scratch/$USER" ]; then  # keep caches off the small home quota
    export UV_CACHE_DIR="${UV_CACHE_DIR:-/scratch/$USER/.cache/uv}" UV_LINK_MODE=copy
fi
mkdir -p "$DIR/pkgs" "$DIR/hf"

echo "== packages into $DIR/pkgs $(date +%T)"
# --no-deps: nothing may pull a second torch or numpy in; the rest of what transformers needs is in .venv-recon.
uv pip install --python "$PY" --target "$DIR/pkgs" --no-deps \
    transformers==4.46.3 tokenizers==0.20.3 huggingface_hub==0.26.5 regex requests urllib3 certifi charset-normalizer

echo "== weights into $DIR/hf $(date +%T)"
PYTHONPATH="$DIR/pkgs" HF_HOME="$DIR/hf" "$PY" -c "
from huggingface_hub import snapshot_download
print(snapshot_download('${RECON_ITEMS_MODEL:-google/owlv2-base-patch16-ensemble}', allow_patterns=['*.json', '*.txt', 'model.safetensors']))"

PYTHONPATH="$DIR/pkgs" HF_HOME="$DIR/hf" HF_HUB_OFFLINE=1 "$PY" -c "
import torch, transformers, huggingface_hub
from transformers import Owlv2ForObjectDetection
print('torch', torch.__version__, '| transformers', transformers.__version__, '| huggingface_hub', huggingface_hub.__version__)"
echo "== ready $(date +%T): export PYTHONPATH=$DIR/pkgs RECON_ITEMS_CACHE=$DIR/hf/hub"
