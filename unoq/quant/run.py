"""On-device experiment, launched by bounded.sh. No remote inference."""
import argparse
import hashlib
import platform
import json
import resource
import time
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps
import torch
from model import load


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--frames', required=True)
    p.add_argument('--count', type=int, choices=(2, 3, 9), default=2)
    p.add_argument('--size', type=int, choices=(224, 336, 518), default=224)
    p.add_argument('--out', required=True)
    p.add_argument('--load-only', action='store_true')
    p.add_argument('--threads', type=int, choices=(1, 2, 4), default=2)
    args = p.parse_args()
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    start = time.monotonic()
    print('Loading verified INT8 checkpoint', flush=True)
    model = load(args.checkpoint)
    loaded = time.monotonic()
    print('Model loaded', flush=True)
    result = dict(load_seconds=loaded-start, inference='load_only', threads=torch.get_num_threads(), interop_threads=torch.get_num_interop_threads())
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not args.load_only:
        preprocess_start = time.monotonic()
        files = sorted(Path(args.frames).glob('*.jpg'))[:args.count]
        if len(files) != args.count:
            raise ValueError('insufficient frames')
        images = []
        for path in files:
            image = Image.open(path).convert('RGB')
            image.thumbnail((args.size, args.size))
            # Uniform square padding preserves aspect ratio and consistent batch dimensions.
            image = ImageOps.pad(image, (args.size, args.size), color=(127, 127, 127))
            images.append(torch.from_numpy(np.array(image)).permute(2, 0, 1).float() / 255)
        batch = torch.stack(images)
        forward_start = time.monotonic()
        with torch.inference_mode():
            prediction = model(batch)
        postprocess_start = time.monotonic()
        outputs = {k: prediction[k].float().cpu().numpy() for k in ('depth', 'depth_conf', 'pose_enc')}
        expected = {'depth': (1,args.count,args.size,args.size,1), 'depth_conf': (1,args.count,args.size,args.size), 'pose_enc': (1,args.count,9)}
        valid = all(np.isfinite(v).all() and v.shape == expected[k] for k,v in outputs.items())
        # Interoperate with Seb's existing --from-recon analyser; keep arrays local.
        from vggt.utils.pose_enc import pose_encoding_to_extri_intri
        E, K = pose_encoding_to_extri_intri(prediction['pose_enc'], (args.size, args.size))
        recon = dict(depth=outputs['depth'][0,...,0], conf=outputs['depth_conf'][0],
                     E=E[0].float().numpy(), K=K[0].float().numpy(),
                     rgb=np.stack([(image.permute(1,2,0).numpy()*255).astype(np.uint8) for image in images]),
                     n_before=6 if args.count == 9 else 0)
        write_start = time.monotonic()
        np.savez(out / 'reconstruction.npz', **outputs)
        np.savez_compressed(out / 'recon.npz', **recon)
        finished = time.monotonic()
        result.update(inference='completed', finite=bool(valid), frames=args.count, size=args.size,
                      inference_seconds=finished-loaded, preprocess_seconds=forward_start-preprocess_start,
                      forward_seconds=postprocess_start-forward_start, postprocess_seconds=write_start-postprocess_start,
                      write_seconds=finished-write_start, shapes={k:list(v.shape) for k,v in outputs.items()})
    def sha256(path):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        return digest.hexdigest()
    result['versions'] = dict(python=platform.python_version(), torch=torch.__version__, numpy=np.__version__)
    result['checkpoint_manifest_sha256'] = sha256(Path(args.checkpoint) / 'manifest.json')
    if not args.load_only:
        result['frame_sha256'] = [sha256(path) for path in files]
    result['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for line in Path('/proc/self/cgroup').read_text().splitlines():
        if line.startswith('0::'):
            cgroup = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
            peak = cgroup / 'memory.peak'
            if peak.exists(): result['cgroup_peak_bytes'] = int(peak.read_text())
    (out / 'report.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
