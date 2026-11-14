# recon_fast: Rust loops for the change check (JUN-28)

The change check (`recon_change.analyse`) spends its own time, outside the model, in a few
per-pixel loops. This crate does them in Rust, one pass per point and on all CPU cores, without
the temporary arrays NumPy needs. The model pass, model loading and the weights stay in PyTorch.

| Rust function | replaces | what it does |
|---|---|---|
| `depth_gap` | `recon_change.depth_gap` | project points into one camera and compare with its depth |
| `votes` | `recon_change.votes` | the same for every frame of the other clip: agree / see-through / seen |
| `unproject` | `recon_change.unproject` | depth map to world points |
| `patch_median` | the median in `recon_change.patch_table` | median trusted depth of every image patch |
| `nearest` | the distance table in `recon_change.same_place_similarity` | nearest patch without the (1500, B) table |
| `lookalike_count` | the distance and feature tables in `recon_change.has_lookalike` | look-alike patches in reach, distance first |

`recon_rust.py` picks the Rust version when it is built and the inputs are float32, and the
NumPy code otherwise, so nothing changes on a machine that has not built it. The arithmetic is
the same, in the same order: the matrix products are rounded the way NumPy's OpenBLAS rounds them
(see `dot3` in `src/lib.rs`), so `test_rust.py` finds no value that differs. With a NumPy built
on another BLAS (MKL) the last bit could differ; `test_rust.py` would show it.

## Build

Needs Rust 1.77 or later (<https://rustup.rs>) and the Python environment the worker uses.

```sh
cd ml/recon_change
pip install ./rust          # or: uv pip install --python .venv-recon/bin/python ./rust
python test_rust.py         # Rust and NumPy give the same answers
python test_synthetic.py    # the usual checks, now through Rust
```

On Iridis: `bash iridis/build_rust.sh` on a login node. It puts Rust in scratch with rustup (no
root), builds into `.venv-recon`, and runs `test_rust.py`. Restart the workers afterwards.
The build is for plain x86-64, so a build on the login node runs on the GPU and CPU nodes.

For development, `pip install maturin` then `maturin develop --release` in `rust/` rebuilds in place.

## Threads

Rust uses the CPUs the process is allowed to use (the SLURM allocation, so 4 on the GPU worker
and 32 on the CPU worker). `RAYON_NUM_THREADS=n` overrides that.

## Switch off or roll back

- `RECON_FAST=0` in the worker's environment: runs the NumPy code, no rebuild needed.
- `RECON_FAST_OFF=unproject,nearest`: switches single functions off (names as in the table).
- `pip uninstall recon_fast` (`uv pip uninstall --python .venv-recon/bin/python recon_fast`):
  removes it; the NumPy code runs from then on.

## Check speed

```sh
python bench_rust.py        # NumPy, PR 20 and all of Rust on the same synthetic frames, time and peak memory
```

## Results so far (Ed's laptop, 12 logical CPUs, Windows, 14 Nov)

`analyse()` only, on synthetic frames from `test_synthetic.py`: 24 before + 12 after, 518x294 (the
model's size for 16:9 video), median of 3 runs. With the look-alike check on, the patch features
have 256 numbers in float16, like the real ones. "PR 20" is Rust `depth_gap` and `votes` only.
Not yet measured on the cluster or on real scenes.

| look-alike check | threads | NumPy | PR 20 | all Rust | speed-up | peak extra memory, NumPy / PR 20 / all Rust |
|---|---|---|---|---|---|---|
| off | 1 | 6.35 s | 3.44 s | 3.29 s | 1.9x | 94 / 94 / 93 MB |
| off | 4 (GPU worker) | 6.22 s | 1.48 s | 1.28 s | 4.9x | 100 / 94 / 94 MB |
| off | 12 | 6.19 s | 1.21 s | 0.97 s | 6.3x | 113 / 95 / 95 MB |
| on | 1 | 6.65 s | 3.82 s | 3.33 s | 2.0x | 470 / 470 / 143 MB |
| on | 4 (GPU worker) | 6.58 s | 1.92 s | 1.34 s | 4.9x | 477 / 472 / 143 MB |
| on | 12 | 6.66 s | 1.64 s | 1.06 s | 6.3x | 493 / 475 / 144 MB |

- Same answers: every run gave the same verdict and objects. `test_rust.py` finds no value that
  differs, in 70 million, under NumPy 2.5 and NumPy 1.26 (the cluster's).
- Memory: with the look-alike check on, the peak drops by about 330 MB, because `nearest` no
  longer builds the (1500, B) float64 distance table.
- `locate.locate` (12 baseline + 3 new frames, 4 threads) goes from 0.30 s to 0.23 s through the
  same functions, with the same fix.

Left, with 4 threads and the look-alike check on (1.34 s): the Rust `votes` loop about 0.9 s
(it runs 96 times per check, over 12 to 35 frames each), then `compare`'s own NumPy indexing about
0.2 s and `valid_mask` and `measure_noise` under 0.1 s each. Outside `analyse`, the worker's
overlay pictures and splat take about 0.05 s.
