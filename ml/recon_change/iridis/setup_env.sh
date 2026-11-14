#!/usr/bin/env bash
# One-off setup on an Iridis login node (compute nodes have no internet).
# Builds .venv-recon in this folder's parent and downloads the VGGT weights (5 GB) to scratch.
#   bash iridis/setup_env.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
if [ -d "/scratch/$USER" ]; then  # keep caches off the small home quota
    export UV_CACHE_DIR="${UV_CACHE_DIR:-/scratch/$USER/.cache/uv}"
    export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/scratch/$USER/.local/share/uv/python}"
    export UV_LINK_MODE=copy
    export HF_HOME="${HF_HOME:-/scratch/$USER/.cache/huggingface}"
fi
PY=.venv-recon/bin/python

echo "== venv $(date +%T)"
# --managed-python: use uv's own Python on scratch. The login node's /usr/bin/python3.11 does
# not exist on the GPU nodes, so a venv built on it fails there with "No such file".
uv venv --clear --managed-python --python 3.12 .venv-recon
# VGGT pins torch 2.3.1; cu121 wheels bundle their own CUDA runtime.
uv pip install --python $PY --index-url https://download.pytorch.org/whl/cu121 torch==2.3.1 torchvision==0.18.1
uv pip install --python $PY "numpy<2" opencv-python-headless pillow huggingface_hub einops safetensors
# --no-deps: VGGT asks for opencv-python, which needs libGL that compute nodes may not have.
uv pip install --python $PY --no-deps "git+https://github.com/facebookresearch/vggt.git"

echo "== weights $(date +%T)"
$PY -c "
from huggingface_hub import snapshot_download
print(snapshot_download('facebook/VGGT-1B', allow_patterns=['config.json', 'model.safetensors']))"

$PY -c "import torch, cv2, numpy, vggt; print('torch', torch.__version__, '| cv2', cv2.__version__, '| numpy', numpy.__version__)"
echo "== ready $(date +%T)"
