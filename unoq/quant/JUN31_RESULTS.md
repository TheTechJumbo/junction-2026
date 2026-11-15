# JUN-31 bounded CPU thread experiment — 2026-11-14

Four threads met the two-frame experiment's speed and numerical gates on this UNO Q.
Implementation commit `dcaf659`; the selected experimental invocation is `--threads 4`; the code's default remains
2 for compatibility, and the original board directory and service were not changed.
This is execution evidence, not a geometry or room-change quality validation.

Device `3313861562`, Linux aarch64 `6.16.7-g0dd6551ae96b`, four available CPUs;
Python 3.13.5, PyTorch 2.15.0+cpu, NumPy 2.5.4, Pillow 13.0.0 and safetensors 0.8.0.
Preflight recorded 3669 MiB RAM (3179 MiB available), approximately 14 GB free disk,
four cores and the schedutil governor. VGGT source revision
`a288dd0f14786c93483e45524328726ab7b1b4ce`; checkpoint manifest SHA-256
`c1170b8c45599ea88f0b38e730e8f0804cfe64a594cf8b8434f119f1ca969831`.
Ordered public JPEG SHA-256 for two-frame trials:
`91b3ce43aa6e0e6d44edb0fc08cf3a4d086bc94c30bfee63f3be4f64d314fafe`,
`fafce0904caf10d1ad24ce1e5b65b1ff1f63d88a08da44c30a8edddb33c42bde`.
INT8 transformer storage expands active layers to FP32 and runs FP32 linear
operations; timings do not separately isolate dequantization, GEMM or memory bandwidth.

Every run used public images, 224-square preprocessing, local CPU inference and a
fresh output directory. Supervisor limits were `MemoryMax=2800M`, `MemorySwapMax=0`,
`RuntimeMaxSec=600`; no concurrent inference. PyTorch inter-op stayed at 1; OMP and
OpenBLAS matched intra-op threads. Original run used existing scripts in
`/home/arduino/junction/unoq/quant`; experiment used the separate
`/home/arduino/junction-jun31/quant` copy. Sanitized reports and comparison results are in `JUN31_RESULTS.json`; reconstructions stay on board.

| Run | Threads | Load s | Inference s | Forward s | Supervisor wall s | Peak RSS KiB | cgroup peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Original | 2 | 14.330 | 258.240 | not measured | not measured | 2422032 | 479191040 |
| Screen | 2 | 14.847 | 262.085 | 261.848 | 287.602 | 2421260 | 466141184 |
| Screen | 1 | 13.915 | 535.103 | 534.869 | 559.580 | 2424856 | 468447232 |
| Screen | 4 | 14.721 | 133.218 | 132.982 | 158.552 | 2418296 | 468312064 |
| B1 | 2 | 14.761 | 265.850 | 265.612 | 291.263 | 2421696 | 469557248 |
| C1 | 4 | 12.810 | 134.080 | 133.852 | 156.998 | 2418368 | 467222528 |
| B2 | 2 | 14.805 | 264.108 | 263.874 | 289.614 | 2421668 | 469045248 |
| C2 | 4 | 14.132 | 136.408 | 136.169 | 161.173 | 2417948 | 460812288 |
| B3 | 2 | 14.152 | 260.459 | 260.224 | 285.248 | 2422824 | 470122496 |
| C3 | 4 | 14.211 | 137.087 | 136.857 | 161.938 | 2417008 | 468099072 |

Alternating confirmation median: 264.108 s versus 136.408 s, improvement 48.35%.
The instrumented two-thread run's forward phase was 261.848/262.085 s (99.91%);
preprocess 0.066 s, postprocess 0.008 s, output writes 0.163 s. The legacy
inference timer covers preprocessing through serialization. Supervisor wall time
includes startup and additional reporting. RSS and cgroup peak are different measures:
mapped checkpoint pages may already be charged to another cgroup, so reported cgroup
peak is not the full memory footprint. No run failed its enforced limits.

Two-thread screen and B1/B2/B3 matched all original arrays exactly. Original report
lacks frame and manifest hashes, so provenance against it is not independently verified
by the comparator. One-thread screen passed against instrumented screen (maximum
absolute differences depth 1.907e-6, confidence 1.073e-6, pose 0). Four-thread
screen and C1 were identical to instrumented screen. C2 and C3 passed against
instrumented screen and their respective B runs; maximum differences across them were
depth 1.073e-6, confidence 5.960e-7, pose 2.328e-7. All counts beyond
`rtol=1e-4, atol=1e-5` were zero; keys, shapes, finiteness and recorded provenance
matched. No comparison establishes geometric accuracy.

Confirmation workload samples: B1 43.7°C/~144% CPU, C1 47.5°C/~249%, B2
44.0°C/~139% early, C2 47.8°C/~225%, B3 45.0°C/~141%, C3 46.8°C/~226%.
Sampled CPU frequency was 2.016 GHz. These are snapshots, not continuous telemetry.
During B2 a background fwupd process sampled at 8.5% CPU; it was left untouched.
The store stayed active and authenticated local `/health` returned 200 with
`database: ok` after each confirmation. `/health` reports inference unavailable,
which is expected for this experimental path.

Original load-only run: 25.089 s load, peak RSS 366272 KiB, cgroup peak
2227458048 bytes. Original baseline command:

```sh
/opt/homebrew/bin/adb -s 3313861562 shell 'sh /home/arduino/junction/unoq/quant/bounded.sh --checkpoint /home/arduino/junction-vggt-int8 --frames /home/arduino/public-frames --count 2 --size 224 --out /home/arduino/.local/share/junction/jun31-baseline-original-20261114-1'
```

## Three-frame smoke test

The four-thread run completed in 200.456 s inference (200.167 s forward),
14.608 s load and 224.959 s supervisor wall; RSS peak 2494296 KiB and cgroup
peak 543993856 bytes. Depth `[1,3,224,224,1]`, confidence `[1,3,224,224]` and
pose `[1,3,9]` were finite. The third public frame hash was
`63ca03514d4b4307b65a0fbd7344ae7229239752a0b5feb131e5ef20a6d80e01`.
Sample: 49.3°C, 2.016 GHz, ~272% CPU; afterward 41.5°C and authenticated
store health 200, database ok. No matching three-frame baseline comparison was
performed. This smoke test is not a geometry or quality validation.

```sh
adb -s 3313861562 shell 'sh /home/arduino/junction-jun31/quant/bounded.sh --checkpoint /home/arduino/junction-vggt-int8 --frames /home/arduino/public-frames --count 3 --size 224 --threads 4 --out /home/arduino/.local/share/junction/jun31-three-frame-4'
```

## Commands

On the host, substitute B1/B2/B3, C1/C2/C3 or screen output names and 2/4/1 as
appropriate; runs were sequential in the order shown above:

```sh
adb -s 3313861562 shell 'sh /home/arduino/junction-jun31/quant/bounded.sh \
  --checkpoint /home/arduino/junction-vggt-int8 --frames /home/arduino/public-frames \
  --count 2 --size 224 --threads 4 --out /home/arduino/.local/share/junction/jun31-confirm-c1'
adb -s 3313861562 shell '/home/arduino/junction-vggt-env/bin/python \
  /home/arduino/junction-jun31/quant/compare_threads.py \
  --reference /home/arduino/.local/share/junction/jun31-confirm-b1 \
  --candidate /home/arduino/.local/share/junction/jun31-confirm-c1'
adb -s 3313861562 shell 'cd /home/arduino/junction-jun31/quant && \
  /home/arduino/junction-vggt-env/bin/python -m unittest discover -s . -p "test_*.py" -v'
```

The same comparator was run for every B against the original and every C against
both instrumented screen 2 and its paired B. Board quant/comparator tests: 9 passed;
local `/tmp/junction-quant-venv/bin/python -m unittest discover -s unoq/quant -p
'test_*.py' -v`: 9 passed; local store tests: 5 passed; `git diff --check` passed.
Root's separate foundation workspace `make check` passed 470 tests, 12 deselected,
lint and schema checks; the initial missing-venv failure was resolved with `make setup`
using Python 3.12. The test run emitted an httpx deprecation warning. The quant branch
itself has no Makefile. Astra High provided diagnosis and review; Terra High performed
implementation and board experiments. No credentials, input images, weights or output
arrays are committed. No production deployment, remote inference, browser, database
restart, audible-answer latency or room-change quality claim is made by this experiment.
Rollback: omit `--threads 4` to use the retained 2-thread default, or continue using the
untouched original board directory; experimental output directories can be removed when
no longer needed.
