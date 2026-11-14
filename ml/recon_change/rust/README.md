# recon_fast: Rust loops for the change check (JUN-28)

`recon_change.py` spends most of its own time (outside the model) in two loops: `depth_gap`
projects every flagged pixel into another camera, and `votes` does that for every frame of the
other clip. This crate does both in Rust, one pass per point and on all CPU cores, with no
temporary arrays. The model pass, model loading and the weights stay in PyTorch.

`recon_rust.py` picks the Rust version when it is built and the inputs are float32, and the
NumPy code otherwise, so nothing changes on a machine that has not built it.

## Build

Needs Rust 1.77 or later (<https://rustup.rs>) and the Python environment the worker uses.

```sh
cd ml/recon_change
pip install ./rust          # builds the release version and installs recon_fast
python test_rust.py         # Rust and NumPy give the same reports
python test_synthetic.py    # the usual checks, now through Rust
```

For development, `pip install maturin` then `maturin develop --release` in `rust/` rebuilds in place.

On the cluster, build once in the worker's environment on a login node, with the same Python.
The build goes into that environment's site-packages, so the GPU and CPU workers pick it up the
next time they start.

## Threads

Rust uses the CPUs the process is allowed to use (the SLURM allocation, so 4 on the GPU worker
and 32 on the CPU worker). `RAYON_NUM_THREADS=n` overrides that.

## Switch off or roll back

- `RECON_FAST=0` in the worker's environment: runs the NumPy code, no rebuild needed.
- `pip uninstall recon_fast`: removes it; the NumPy code runs from then on.

## Check speed

```sh
python bench_rust.py        # NumPy against Rust on the same synthetic frames, time and peak memory
```

## Results so far (Ed's laptop, 12 logical CPUs, Windows, 14 Nov)

`analyse()` only, on synthetic frames (24 before + 12 after, 518x294 as the model gives for 16:9
video), median of 3 runs. Not yet measured on the cluster or on real scenes.

| look-alike check | threads | NumPy | Rust | speed-up | peak extra memory, NumPy / Rust |
|---|---|---|---|---|---|
| off | 1 | 6.29 s | 3.05 s | 2.1x | 94 / 94 MB |
| off | 4 (GPU worker) | 6.29 s | 1.38 s | 4.6x | 100 / 94 MB |
| off | 12 | 6.34 s | 1.12 s | 5.6x | 113 / 95 MB |
| on | 1 | 6.78 s | 3.40 s | 2.0x | 394 / 394 MB |
| on | 4 (GPU worker) | 6.44 s | 1.79 s | 3.6x | 404 / 395 MB |
| on | 12 | 6.50 s | 1.53 s | 4.3x | 413 / 396 MB |

Every run gave the same verdict and the same objects both ways (`test_rust.py` checks the flagged
pixels too). Memory hardly changes: the peak comes from the per-frame masks and the look-alike
distance matrix, not from these loops. The synthetic look-alike features have 3 dimensions
instead of 256, so the look-alike part is cheaper here than on real scenes.

Left in Python, with 4 threads and the look-alike check on (1.8 s in total): the Rust loops
themselves take about 0.7 s, then `compare`'s own array work, `unproject`, `patch_table`,
`measure_noise` and the look-alike `distances` take about 0.2 s each. These are the next
candidates if the check needs to be faster still.
