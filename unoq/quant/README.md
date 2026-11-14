# Experimental INT8 VGGT on UNO Q

This is a memory-first CPU experiment, not a real-time vision service. It leaves the
existing household store untouched. No images are sent to a laptop or remote GPU.
Only the public weights are converted on the Mac.

## Reproduce

Use VGGT source revision `a288dd0f14786c93483e45524328726ab7b1b4ce` from
https://github.com/facebookresearch/vggt. This revision retains just the four feature
layers needed by the depth/camera heads. Keep its license with the source checkout.
Use the original public `facebook/VGGT-1B/model.safetensors` checkpoint; its license
and any subsequent model-use restrictions still apply to derived weights.

Converter dependencies are pinned in converter-requirements.txt. On Linux ARM use
the matching CPU wheels for that platform (not the Mac torch wheel), in an isolated
virtual environment. Do not modify Debian's system Python.

```
PYTHONPATH=/path/to/pinned/vggt python unoq/quant/convert.py \
  --weights /path/to/model.safetensors --out /path/to/new-int8-directory
```

The output directory must not already exist. The converter streams one layer at a
time into separate safetensors shards. The manifest records hashes, formats and
source revision. Point and track heads are omitted; transformer linear layers use
symmetric per-output-channel INT8. Biases, normalization, embeddings and geometry
heads stay FP32. A portable linear module expands only the active layer to FP32;
it does not require ARM dot-product instructions or cache an expanded model.
This saves weights but is not expected to accelerate this CPU substantially.

Transfer shards, this code and the pinned source to the board, not via cloud sync:
- `/home/arduino/junction/unoq/quant`: runner and modules
- `/home/arduino/junction-vggt`: pinned upstream source
- `/home/arduino/junction-vggt-env`: isolated ARM Python environment
- `/home/arduino/junction-vggt-int8`: checkpoint shards and manifest

Run on board:

```
sh /home/arduino/junction/unoq/quant/bounded.sh \
  --checkpoint /home/arduino/junction-vggt-int8 \
  --frames /home/arduino/public-frames --count 2 --size 224 \
  --out /home/arduino/.local/share/junction/quant-probe-2
```

Start with `--load-only`, then two public/demo JPEGs. Progress to three and nine
frames only if the earlier run succeeds. Long edge is scaled with aspect ratio
preserved and padded to a uniform square divisible by 14. The probe saves only
depth, confidence and pose outputs, with a JSON report of timings, shapes and RSS.
It never launches a server or changes /health to claim production inference.

The supervisor is a separate user systemd job with MemoryMax=2800M,
MemorySwapMax=0, RuntimeMaxSec=600. It deliberately uses resident-memory accounting,
not RLIMIT_AS, which would reject large virtual mappings. systemd enforces limits
on the job including its descendants. A supervisor outside the inference cgroup records failures in report.json and
supervisor.log (OOM, timeout or dependency failure). Use a fresh output directory
for every invocation. The limit includes file cache charged
to the cgroup; RSS alone is not the full accounted memory.

## Tests and acceptance

`python -m unittest discover -s unoq/quant -p 'test_*.py' -v`

Layer tests check bounded quantization error, serialization, bias and absence of a
full-weight cache. The loader verifies every shard hash, tensor shapes and that no
parameters remain uninitialized on meta. Corrupted/incomplete artifacts fail closed.

A finite two-frame output establishes execution only, not accurate geometry. Before
calling the room-change feature functional: compare trusted FP32 reference outputs
on public scenes, report scale-aligned depth relative errors and camera rotation
errors, then run six-before/three-after unchanged and moved-object scenes. Until
those checks pass, keep the experimental outputs out of production answers/alerts.
No household image should be used for calibration or debugging on another machine.

INT4 is conditional: add it only if measured INT8 exceeds this memory envelope.
Gaussian splatting remains behind successful reconstruction and quality checks.

Compare supplied reference outputs without running inference elsewhere:
`python unoq/quant/compare.py --reference reference.npz --candidate reconstruction.npz`.
Use identical frames and preprocessing; its errors are diagnostics, not a calibration
or geometry acceptance threshold. Real scene-change checks remain mandatory.

## Measured checkpoint size

The converted artifact is 1,916,630,560 bytes, including 911,341,440 bytes of
INT8 transformer shards and 1,005,289,120 bytes of remaining FP32 tensors/shard
headers. This supersedes the earlier 1.27 GB estimate, which assumed matrix weights
in geometry heads would also be quantized. Source SHA256:
`f164acf60724910d8fe1578bb499d800850c7bb0948db7555c413f9fbe60467e`.

## Board result

Two public kitchen frames at 224x224 completed: 20.10 s loading, 270.79 s
inference, peak RSS 2,405,060 KiB (2.29 GiB). All requested depth/confidence/pose
outputs were finite and had the expected shapes. See two-frame-board-result.json.
A full model copy was not allocated: quantized layers map their checkpoint shards,
then materialize only the current layer's FP32 working weights. Loading alone
peaked at 369,404 KiB RSS and is not representative of inference memory.

Runtime dependencies were installed offline from host-downloaded ARM CPU wheels;
board-requirements.txt captures the installed versions. The default PyPI ARM torch
wheel unexpectedly required CUDA; use the explicit +cpu builds instead.

A separate 64 MiB allocation test was killed by its cgroup memory limit; a separate
1-second test was stopped by its runtime limit. The household store remained active
after both. systemd cgroup charge and process RSS can differ because the public
checkpoint's mapped file pages may be shared with caches charged elsewhere.
Both limits and RSS must be considered; fitting weights alone is insufficient.

Three frames also completed: load 14.59 s, inference 400.14 s, peak RSS
2,423,860 KiB (2.31 GiB). Finite camera/depth outputs and the local recon.npz
adapter were validated. Full room-change quality remains unvalidated.

Experimental output directories are not enrolled in the store's artifact retention
sweep. Use public/synthetic inputs only until the inference path is integrated with
the artifact API and its retention lifecycle. The bounded job uses a private output
directory and owner-only creation mask. Geometry-quality comparisons are still
pending; do not use these experiments as evidence for user-facing safety claims.

Nine frames at 224x224 were stopped by the 600-second runtime limit, with no
completed reconstruction. See nine-frame-board-result.json. This was a timeout,
not an OOM result. INT4 was not added because the observed limitation was time,
not weight memory. No claim of a working full room-change pipeline is made.
