#!/usr/bin/env python3
"""Split computing for VGGT (PRIVACY.md): run its first stage, the DINOv2 ViT-L/14 backbone, on the trusted side and send
patch tokens instead of pixels. How much does that cost on a CPU, how much bigger is what is sent, and how well can the
picture be rebuilt from the tokens (the attack someone holding the public VGGT weights could make)?

    python eval/split_eval.py PICTURE [PICTURE ...] [--blocks 0,4,12,24] [--steps 400] [--out FOLDER]

1. CPU time for a 518 x 518 picture through the patch embedding and the first k blocks (float32, 4 threads).
2. Size of k-block tokens (1369 patches x 1024 numbers, float16) next to a JPEG of the picture.
3. Inversion: for k = 0 the patch embedding is a linear map of each 14 x 14 x 3 patch into 1024 numbers, so a least-squares
   solve gives the picture back. For k > 0 an attacker optimises a picture until its tokens match (Adam, --steps steps,
   on the GPU if there is one, a small smoothness prior): PSNR against the original. Rebuilt pictures go to --out.
Uses VGGT-1B from the local Hugging Face cache (nothing is downloaded).
"""
import argparse
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
SIDE = 518


def load_backbone(model_id="facebook/VGGT-1B"):
    """VGGT's DINOv2 ViT-L/14 alone (its aggregator.patch_embed), read from the cached weights: only these 0.3 B of the
    1.2 B parameters are loaded, so it fits a laptop's memory."""
    import torch
    from huggingface_hub import hf_hub_download
    from safetensors import safe_open
    from vggt.layers.vision_transformer import vit_large
    from vggt.models.aggregator import _RESNET_MEAN, _RESNET_STD

    dino = vit_large(img_size=518, patch_size=14, num_register_tokens=4, interpolate_antialias=True, interpolate_offset=0.0,
                     block_chunks=0, init_values=1.0)
    prefix, state = "aggregator.patch_embed.", {}
    with safe_open(hf_hub_download(model_id, "model.safetensors"), framework="pt") as f:
        for name in f.keys():
            if name.startswith(prefix):
                state[name[len(prefix):]] = f.get_tensor(name).float()
    missing, unexpected = dino.load_state_dict(state, strict=False)
    if [m for m in missing if "mask_token" not in m] or unexpected:
        raise RuntimeError(f"backbone weights do not fit: missing {missing[:5]}, unexpected {unexpected[:5]}")
    dino.eval()
    for p in dino.parameters():
        p.requires_grad_(False)
    mean = torch.tensor(_RESNET_MEAN, dtype=torch.float32).reshape(1, 3, 1, 1)
    std = torch.tensor(_RESNET_STD, dtype=torch.float32).reshape(1, 3, 1, 1)
    return dino, mean, std, torch


def tokens(dino, x, k, mean, std):
    """Patch tokens after k blocks (k = len(blocks): after the final norm, as VGGT takes them). x: (1, 3, H, W) in [0, 1]."""
    import torch.utils.checkpoint as cp
    t = dino.prepare_tokens_with_masks((x - mean) / std)
    for blk in dino.blocks[:k]:  # with gradients (the inversion): recompute each block's insides, so 24 fit a 6 GB card
        t = cp.checkpoint(blk, t, use_reentrant=False) if t.requires_grad else blk(t)
    if k >= len(dino.blocks):
        t = dino.norm(t)
    return t[:, 1 + dino.num_register_tokens:]


def psnr(a, b):
    return 10 * math.log10(1 / max(float(((a - b) ** 2).mean()), 1e-12))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pictures", nargs="+")
    ap.add_argument("--blocks", default="0,4,12,24")
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--out", default=None)
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    dino, mean, std, torch = load_backbone()
    torch.set_num_threads(args.threads)
    blocks = [int(b) for b in args.blocks.split(",")]
    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    pictures = []
    for p in args.pictures:
        bgr = cv2.imread(p)
        h, w = bgr.shape[:2]
        side = min(h, w)
        bgr = cv2.resize(bgr[(h - side) // 2:(h - side) // 2 + side, (w - side) // 2:(w - side) // 2 + side], (SIDE, SIDE), interpolation=cv2.INTER_AREA)
        pictures.append(bgr)
    x0 = torch.from_numpy(cv2.cvtColor(pictures[0], cv2.COLOR_BGR2RGB)).permute(2, 0, 1)[None].float() / 255

    print(f"1. CPU time, float32, {args.threads} threads, one 518 x 518 picture")
    with torch.no_grad():
        tokens(dino, x0, 1, mean, std)
        for k in sorted(set(blocks + [1, 2])):
            times = []
            for _ in range(3):
                t0 = time.perf_counter()
                tokens(dino, x0, k, mean, std)
                times.append(1000 * (time.perf_counter() - t0))
            print(f"   patch embedding + {k:2d} blocks: {np.median(times):7.0f} ms")
    jpeg = len(cv2.imencode(".jpg", pictures[0], [cv2.IMWRITE_JPEG_QUALITY, 90])[1])
    n = (SIDE // 14) ** 2
    print(f"2. size: tokens {n} x 1024 float16 = {n * 1024 * 2 / 1e6:.2f} MB a picture; the JPEG (quality 90) is {jpeg / 1e3:.0f} kB")

    print("3. inversion (attacker with the public weights)")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dino = dino.to(device)
    mean, std = mean.to(device), std.to(device)
    for i, bgr in enumerate(pictures):
        x = torch.from_numpy(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).permute(2, 0, 1)[None].float().div(255).to(device)
        for k in blocks:
            with torch.no_grad():
                target = tokens(dino, x, k, mean, std).half().float()  # what would be sent
            t0 = time.time()
            if k == 0:  # linear: solve each patch from its 1024 numbers
                conv = dino.patch_embed.proj
                Wm = conv.weight.reshape(conv.weight.shape[0], -1).double()  # (1024, 3 * 14 * 14)
                pos = dino.interpolate_pos_encoding(torch.zeros(1, n + 1, Wm.shape[0], device=device), SIDE, SIDE)[:, 1:]
                y = (target - pos.float()).double()[0] - conv.bias.double()
                patches = torch.linalg.lstsq(Wm, y.T).solution.T  # (n, 588) normalised pixels
                g = SIDE // 14
                img = patches.reshape(g, g, 3, 14, 14).permute(2, 0, 3, 1, 4).reshape(1, 3, SIDE, SIDE).float()
                rebuilt = (img * std + mean).clamp(0, 1)
            else:
                y = torch.full_like(x, 0.5).requires_grad_(True)
                opt = torch.optim.Adam([y], lr=0.03)
                for step in range(args.steps):
                    opt.zero_grad()
                    loss = ((tokens(dino, y.clamp(0, 1), k, mean, std) - target) ** 2).mean()
                    tv = (y[..., 1:, :] - y[..., :-1, :]).abs().mean() + (y[..., :, 1:] - y[..., :, :-1]).abs().mean()
                    (loss / target.var() + 0.02 * tv).backward()
                    opt.step()
                rebuilt = y.detach().clamp(0, 1)
            p = psnr(rebuilt, x)
            print(f"   picture {i}, after {k:2d} blocks: rebuilt at {p:5.1f} dB PSNR ({time.time() - t0:.0f} s)", flush=True)
            if out:
                img = (rebuilt[0].permute(1, 2, 0).cpu().numpy() * 255).astype(np.uint8)
                cv2.imwrite(str(out / f"pic{i}_k{k:02d}.jpg"), cv2.cvtColor(np.concatenate([cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), img], 1), cv2.COLOR_RGB2BGR))


if __name__ == "__main__":
    main()
