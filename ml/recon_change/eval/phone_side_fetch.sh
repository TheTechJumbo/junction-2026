#!/bin/bash
# Fetch the small models of the phone-side position fix. Run on a LOGIN node (compute nodes have no
# internet), from the project folder:  bash phone/code/phone_side_fetch.sh phone
# Both are Apache-2.0 and need no sign-up:
#   facebook/dinov2-small   ViT-S/14, 22 M parameters (Hugging Face)
#   verlab/accelerated_features   XFeat, 0.66 M parameters, weights inside the repository (GitHub)
# facebook/dinov2-base is already in items/hf/hub.
set -euo pipefail
DIR="${1:-phone}"
PY="${PY:-.venv-recon/bin/python}"
mkdir -p "$DIR/hf"
PYTHONPATH="items/pkgs" HF_HOME="$DIR/hf" "$PY" -c "
from huggingface_hub import snapshot_download
print(snapshot_download('facebook/dinov2-small', allow_patterns=['*.json', '*.safetensors']))
"
if [ ! -d "$DIR/xfeat" ]; then
    git clone --depth 1 https://github.com/verlab/accelerated_features "$DIR/xfeat"
fi
head -3 "$DIR/xfeat/LICENSE"
ls -la "$DIR/xfeat/weights"
