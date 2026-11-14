# UNO Q validation — 2026-11-14

Connected by USB (ADB); Debian 13.1 / aarch64, Python 3.13.5.

- RAM: 3,848,212,480 bytes total; about 3.3 GB available during preflight.
- Data partition: 18 GiB mounted at /home/arduino, 17 GiB free.
- Root partition: 5.4 GiB free. No model files installed there.
- User systemd available; lingering was already enabled. Store installed as
  `junction-store.service`, listening only at 127.0.0.1:8091.
- Live service restart smoke test passed: event persistence, local artifact retrieval and export redaction.
- Five meaningful tests passed on board and Mac: persistence/idempotency/conflicts,
  evidence/retention/export, disk-full/orphan recovery, schema/path rejection,
  HTTP authentication/upload/download/incomplete body handling.
- Existing Python has no torch, VGGT, Pillow or safetensors. No inference
  dependencies or weights installed on the board.

## VGGT gate: blocked for current FP32 checkpoint

Hugging Face's model metadata for facebook/VGGT-1B reports 1,256,537,516 F32
parameters: **5,026,150,064 bytes for weights alone**. That exceeds total physical
RAM by over 1 GB before Python, activations and reconstruction arrays. The reference
uses NVIDIA CUDA; the board exposes Adreno DRM devices, not a CUDA runtime.
Source: https://huggingface.co/api/models/facebook/VGGT-1B

This is a measured hardware/metadata feasibility failure, not an inference benchmark.
No claim of working local VGGT or inference latency is made. No laptop/cloud fallback
was used. Reduced precision/quantization and accelerator support need a separate model
optimization effort with Seb. Gaussian splatting is deferred behind that gate.

The board clock currently reports April 2026 rather than the host's November date.
Retention uses ingestion time, not event timestamps, but clock corrections can change
expiry timing. Correct time before real-world retention testing; no system clock or
network configuration was changed during this work.

## Remaining integration checks

- Ed/Bartek must connect their clients to the local API via a secure local transport.
- Real before/after inference, Gaussian splatting and actual fallback filming are pending.
- Service restart persistence is tested; full power-cycle and physically disconnected
  network tests remain to be performed at rehearsal.

## Follow-up: quantized VGGT executes locally

The FP32 gate above still applies to the original checkpoint. A separate INT8
experiment now succeeds: two public kitchen images, 224x224 padded inputs, finite
depth/confidence/camera pose outputs. Checkpoint size 1,916,630,560 bytes;
load 20.10 seconds, inference 270.79 seconds, peak process RSS 2,405,060 KiB
(2.29 GiB). See `quant/two-frame-board-result.json` and `quant/README.md`.
This proves execution, not acceptable geometric accuracy or real-time operation.
Storage stayed active. No personal imagery or remote inference was used.
