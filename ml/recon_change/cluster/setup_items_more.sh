#!/usr/bin/env bash
# Optional extras for the lost-items detector, on a cluster login node (compute nodes have no
# internet): the weights of the models that eval/ITEMS_RESULTS.md ("Finding more") compares.
# Only the ones a worker is told to use have to be there. Nothing goes into .venv-recon.
#   bash cluster/setup_items_more.sh [folder]         # the folder cluster/setup_items.sh filled; default ./items-env
#   bash cluster/setup_items_more.sh [folder] openai/clip-vit-large-patch14     # only this one
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(realpath -m "${1:-items-env}")"
PY="${PY:-$(pwd)/.venv-recon/bin/python}"
shift || true
MODELS="${*:-openai/clip-vit-large-patch14 google/owlv2-large-patch14-ensemble IDEA-Research/grounding-dino-base}"
for model in $MODELS; do
    echo "== $model $(date +%T)"
    PYTHONPATH="$DIR/pkgs" HF_HOME="$DIR/hf" "$PY" -c "
from huggingface_hub import snapshot_download
print(snapshot_download('$model', allow_patterns=['*.json', '*.txt', 'model.safetensors']))"
done
du -sh "$DIR/hf"
echo "== ready $(date +%T)"
