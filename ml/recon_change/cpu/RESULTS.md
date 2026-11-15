# VGGT on a CPU: how fast, which settings, what they cost

Measured on 14 November 2026 with `bench_cpu.py` on the cluster's CPU nodes. No run had a GPU.

## Short answer

- **It runs on a CPU and gives the same answer as the GPU.** At the default setting (24 baseline
  + 12 new frames, long side 518) the detector reports no change for `after.mp4` and one
  "appeared" cluster for `after_box.mp4`, as on the GPU. One check takes 145 s on 32 cores
  (196 s with the sliced attention that most rows here used) and 330 s on 16 cores. The L4 GPU
  takes about 6 s.
- **Save time with fewer frames, not smaller frames.** At long side 518 every frame count down to
  6+3 passes with a wide margin. Below long side 448 the depth gets noisy, and the detector either
  misses the cube or finds it at the smallest size it reports.
- **A 4-core machine needs about 2 minutes per check.** 6+3 frames at long side 518 took 133 s on
  4 cores of the older server CPU (the closer match to the demo laptop) and 54 s on 4 cores of the
  newer one. Peak memory was 7.3 GB. The laptop itself was not measured.
- **One trick helps, and only on a newer server CPU.** bfloat16 is 2.3 times faster and still
  passes at long side 518, but it needs AVX-512 BF16 instructions, which the demo laptop does not
  have. int8 misses the cube in four of six runs, `torch.compile` does not run in this
  environment, and `torch.inference_mode` changes nothing.

## Recommendations

| Goal | Setting | Measured time per check | Peak memory |
| --- | --- | --- | --- |
| Best quality | 24+12 frames, long side 518, float32 (the GPU default) | 145 s on 32 EPYC cores with `--attn-chunk-mb 0`. With the default slices: 196 s on 32, 330 s on 16 | 8.4 to 8.5 GB |
| Fastest that still passes | 12+6 frames, long side 294, float32. **Not recommended**, see below | 12 s on 32 EPYC cores, 20 s on 16, 34 s on 8, 70 to 77 s on 4; 29 s on 4 Xeon cores | 6.2 to 6.5 GB |
| Fastest that passes with a margin | 6+3 frames, long side 518, float32 (8+4 where 6+3 was not run) | 6+3: 133 s on 4 EPYC cores, 54 s on 4 Xeon cores. 8+4: 34 s on 32 EPYC cores, 94 s on 8, and 33 s on 4 Xeon cores with bfloat16 | 7.3 to 8.7 GB |
| 4-core, 16 GB laptop | 6+3 frames, long side 518, float32. Use 8+4 if you can wait | 6+3: 133 s on 4 EPYC cores. 8+4: 193 s on 4 EPYC cores | 7.3 to 7.4 GB |

- **Why not 12+6 at 294.** It passes, but the cube comes out as a cluster of 8 voxels, and 8 is
  the smallest cluster the detector reports (the GPU reference finds 169). Its neighbours fail:
  8+4, 16+8 and 24+12 at long side 294 all miss the cube. Treat this pass as luck.
- **The laptop.** The demo laptop (Intel i5-10300H, 4 cores, 16 GB) has AVX2 but not AVX-512, like
  the EPYC here, so the 4-core EPYC rows are the better guide. Expect roughly 2 minutes per check
  at 6+3 and 3 minutes at 8+4, plus 10 to 30 s once to load the model. Run it with
  `--attn-chunk-mb 0`. This is an estimate from a different CPU: nothing was run on the laptop.
- **Memory on the laptop.** 16 GB is enough. 8+4 at long side 518 also ran to the end inside a
  hard 8 GB limit (208 to 211 s on 4 EPYC cores, pass), so about 8 GB free is enough too.

## How it was measured

- **Script.** `bench_cpu.py` runs one setting per process and prints one JSON line. It loads the
  model in float32, reconstructs the baseline frames together with the new frames of each test
  clip, and runs `rc.analyse` with its defaults.
- **Clips.** `baseline.mp4`, `after.mp4` (unchanged) and `after_box.mp4` (a cube painted into every
  frame), 518x350 pixels. Frames come from `rc.sample_frames`.
- **Pass.** `after.mp4`: no appeared and no gone cluster. `after_box.mp4`: exactly one appeared
  cluster and none gone. Both must hold.
- **CPUs.**
  - AMD EPYC 7452 (2 x 32 cores, AVX2, no AVX-512), nodes of the `amd_student` partition.
  - Intel Xeon Gold 5418Y (AVX-512 with VNNI, BF16 and AMX), 4 cores of a GPU node used through
    `scavenger_l4` with no GPU requested, because the queue for `amd_student` was long.
- **Cores.** Each run is pinned to that many physical cores and `torch.set_num_threads` matches.
- **Inference s** is the model's forward pass for one check (all baseline and new frames in one
  pass). It leaves out loading the model and reading the video. The two numbers are the two clips.
- **Detect s** is `rc.analyse`.
- **Peak GB** is the largest resident memory of the process during inference and detection.
- **Cube voxels** is the size of the cluster found for the cube. The detector drops clusters under
  8 voxels, so 8 is a pass with nothing to spare.
- **Depth noise** is the detector's own measure: how much two halves of the baseline clip disagree
  about depth. The GPU reference has 0.7%.
- **Spread.** Other jobs ran on the same nodes, mine included. Six runs of the same maths
  (12+6 at 294 on 4 EPYC cores, differing only in options that turned out not to matter) took
  between 69.6 and 76.8 s, so read times as give or take 5%.
- **Not in the inference time:** loading the model (7 to 13 s when the file is already in the
  page cache, 27 to 43 s on a first read from scratch storage) and detection (0.1 to 2.8 s up to
  24 frames, 6.3 s for 36 frames at long side 518).
- **Two differences from the GPU code**, neither of which changes the maths. The point-map head
  is skipped, because the detector only reads depth and cameras. And unless a row says
  "attention whole", the attention matrix is built in slices of 256 MB. That was meant to save
  memory and turned out to be a mistake: it saves none and costs time at long side 518 (see
  Tricks). Use `--attn-chunk-mb 0`. Rows at long side 518 without it are up to a quarter slower
  than they need to be.

## What each setting does

### Frames (long side 518)

| Frames | 32 EPYC cores | 4 EPYC cores | 4 Xeon cores | Cube voxels | Result |
| --- | --- | --- | --- | --- | --- |
| 24+12 | 196 s | not measured | 556 s, unchanged clip only | 161 | pass |
| 16+8 | 105 s | not measured | 250 s | 133 | pass |
| 12+6 | 57 s | not measured | 146 s | 100 | pass |
| 8+4 | 34 s | 193 s | 76 s | 67 | pass |
| 6+3 | not measured | 133 s | 54 s | 61 | pass |

Time falls faster than the frame count, because every frame attends to every other frame. Depth
noise stays at 0.6 to 0.8% throughout. 6+3 is the smallest clip `recon_change.py` accepts.

### Long side

| Long side | Depth noise | What the detector did |
| --- | --- | --- |
| 518 | 0.6 to 0.8% | Passes at every frame count, cube 61 to 161 voxels |
| 448 | 1.1% | 8+4 passes on both CPUs, cube 65 voxels (the only frame count tried). 134 s on 4 EPYC cores, 54 s on 4 Xeon cores: the same cost as 6+3 at 518 |
| 392 | 2.5 to 4.9% | Unreliable. 12+6 fails with a false "gone" cluster in the unchanged clip. 24+12 passes on the EPYC and fails on the Xeon. 8+4 passes at 8 voxels |
| 294 | 5.7 to 9.2% | Only 12+6 passes, at 8 voxels. 8+4, 16+8 and 24+12 miss the cube |
| 224 | 8.5 to 9.0% | Every frame count misses the cube |

Smaller frames are much cheaper (24+12 at long side 224 takes 16 s on 32 cores), but the depth
is too noisy for this detector. Shrinking the detector's 15-pixel search window in step with the
image (`--scale-window`) did not help: the five settings at 224 and 294 that missed the cube still
missed it, and 12+6 at 294 still passed at 8 voxels.

At long side 392 the same float32 setting gave a different answer on the two CPUs. That is how
close to the edge these settings are.

### Cores (AMD EPYC 7452)

| Setting | 4 cores | 8 cores | 16 cores | 32 cores |
| --- | --- | --- | --- | --- |
| 12+6 at 294 | 70 to 77 s | 34 s | 20 s | 12 s |
| 8+4 at 518 | 193 s | 94 s | not measured | 34 s |
| 24+12 at 518 | not measured (556 s on 4 Xeon cores) | not measured | 330 s | 196 s (145 s with `--attn-chunk-mb 0`) |

Doubling from 4 to 8 cores halves the time. Each doubling after that gives about 1.65 times.
Four cores of the Xeon are 2.5 times as fast as four cores of the EPYC.

### Tricks, each against plain float32 on the same CPU and setting

| Trick | Where | Float32 | With the trick | Still passes? |
| --- | --- | --- | --- | --- |
| bfloat16 autocast | 4 Xeon cores, 12+6 at 294 | 29 s | 11 s | **No**: a false "appeared" in the unchanged clip, and the cube missed. Float32 only just passes here |
| bfloat16 autocast | 4 Xeon cores, 8+4 at 518 | 76 s | 33 s | **Yes**: cube 65 voxels (66 in float32), depth noise 0.7% |
| bfloat16 autocast | 4 EPYC cores, 12+6 at 294 | 70 to 77 s | first clip not finished after 16 minutes | Not usable: this CPU has no bfloat16 instructions |
| int8 dynamic quantisation | 4 Xeon cores, 12+6 at 294 | 29 s | 17 s | Yes, cube 9 voxels |
| int8 dynamic quantisation | 4 EPYC cores, 12+6 at 294 | 70 to 77 s | 49 s | **No**: cube missed |
| int8 dynamic quantisation | 4 Xeon cores, 8+4 at 518 | 76 s | 64 s | **No**: cube missed, depth noise 2.8% |
| int8 dynamic quantisation | 4 EPYC cores, 8+4 at 518 | 193 s | 150 s | Yes, but weakly: cube 14 voxels instead of 67, depth noise 2.5% instead of 0.7% |
| int8 dynamic quantisation | 4 EPYC cores, 6+3 at 518 | 133 s | 100 s | **No**: cube missed, depth noise 3.5% |
| int8 dynamic quantisation | 4 Xeon cores, 6+3 at 518 | 54 s | 41 s | **No**: cube missed, depth noise 2.4% |
| `torch.inference_mode` | 4 cores, 12+6 at 294 | 29 s Xeon, 70 to 77 s EPYC | 29 s Xeon, 70 s EPYC | Yes. No gain beyond the spread |
| `torch.compile` | 4 Xeon cores | | does not run | torch 2.3.1 refuses: "Dynamo is not supported on Python 3.12+" |
| Skipping the point-map head | 4 Xeon cores, 12+6 at 294 | 33 s with the head | 29 s without | Yes, same clusters. Already the default in `bench_cpu.py` |
| Attention in slices | 4 cores, 12+6 at 294 | 29 s Xeon, 70 s EPYC with the library's attention | 29 to 31 s Xeon, 72 to 77 s EPYC in slices | Yes. No gain |
| Attention in slices | 4 EPYC cores, 8+4 at 518 | 178 s with the library's attention, peak 7.42 GB | 193 s in slices, peak 7.38 GB | Yes. A loss: no memory saved, 8% slower |
| Attention in slices | 32 EPYC cores, 24+12 at 518 | 145 s with the library's attention, peak 8.36 GB | 196 s in slices, peak 8.51 GB | Yes. A loss: no memory saved, 35% slower. The sliced run shared its node with another 32-core job of mine, so part of the gap may be that |

bfloat16 is what the GPU code already uses. On the CPU it only pays off where the CPU has
AVX-512 BF16 or AMX (Intel Sapphire Rapids and newer). The EPYC and the demo laptop do not.

int8 and bfloat16 both need more memory, not less, when applied after loading in float32:
7.5 to 8.7 GB peak against 6.2 to 7.4 GB.

### Memory

- Loading: resident memory peaks at 10.5 GB for a moment and settles at 5.3 GB. The model still
  loaded and ran inside a hard 8 GB limit (three settings, all passed).
- Inference adds 0.5 to 3.2 GB: 5.7 GB peak for 8+4 at 224, 7.3 to 7.5 GB for 6+3 and 8+4 at 518,
  8.5 GB for 24+12 at 518.

## All runs

<!-- table:start -->
| CPU | Cores | Frames | Input | Variant | Load s | Inference s (after / after_box) | Detect s | Peak GB | after: appeared, gone | after_box: appeared, gone | Cube voxels | Depth noise | Result |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| EPYC 7452 | 4 | 8+4 | 518x350 | fp32 | 32.0 | 194.2 / 191.9 | 0.6 | 7.38 | 0, 0 | 1, 0 | 67 | 0.7% | PASS |
| EPYC 7452 | 4 | 8+4 | 518x350 | fp32, attention 64 MB slices, mem8G | 9.2 | 208.0 / 211.2 | 0.6 | 7.37 | 0, 0 | 1, 0 | 67 | 0.7% | PASS |
| EPYC 7452 | 4 | 8+4 | 518x350 | fp32, attention whole | 8.9 | 178.5 / 176.8 | 0.6 | 7.42 | 0, 0 | 1, 0 | 67 | 0.7% | PASS |
| EPYC 7452 | 4 | 8+4 | 518x350 | int8 | 9.0 | 150.1 / 149.1 | 0.7 | 8.68 | 0, 0 | 1, 0 | 14 | 2.5% | PASS |
| EPYC 7452 | 4 | 6+3 | 518x350 | fp32 | 9.4 | 132.6 / 132.5 | 0.5 | 7.3 | 0, 0 | 1, 0 | 61 | 0.7% | PASS |
| EPYC 7452 | 4 | 6+3 | 518x350 | int8 | 9.1 | 99.7 / 99.4 | 0.5 | 8.56 | 0, 0 | 0, 0 | - | 3.5% | FAIL |
| EPYC 7452 | 4 | 8+4 | 448x308 | fp32 | 9.4 | 133.9 / 133.4 | 0.5 | 6.89 | 0, 0 | 1, 0 | 65 | 1.1% | PASS |
| EPYC 7452 | 4 | 12+6 | 392x266 | fp32 | 9.1 | 158.8 / 157.2 | 0.8 | 6.58 | 0, 1 | 1, 1 | 20 | 2.8% | FAIL |
| EPYC 7452 | 4 | 8+4 | 392x266 | fp32 | 9.4 | 98.7 / 95.2 | 0.4 | 6.77 | 0, 0 | 1, 0 | 8 | 2.5% | PASS |
| EPYC 7452 | 4 | 8+4 | 392x266 | fp32, mem8G | 15.2 | 91.4 / 89.1 | 0.4 | 6.77 | 0, 0 | 1, 0 | 8 | 2.5% | PASS |
| EPYC 7452 | 4 | 24+12 | 294x196 | fp32 | 32.1 | 185.5 / 187.4 | 1.4 | 6.18 | 0, 0 | 0, 0 | - | 8.4% | FAIL |
| EPYC 7452 | 4 | 16+8 | 294x196 | fp32 | 9.2 | 108.3 / 106.2 | 0.6 | 6.59 | 0, 0 | 0, 0 | - | 9.2% | FAIL |
| EPYC 7452 | 4 | 12+6 | 294x196 | fp32 | 32.1 | 76.7 / 76.9 | 0.4 | 6.24 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 4 | 12+6 | 294x196 | fp32, attention 1024 MB slices | 9.1 | 71.5 / 71.5 | 0.3 | 6.19 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 4 | 12+6 | 294x196 | fp32, attention 64 MB slices | 32.1 | 75.9 / 75.6 | 0.3 | 6.1 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 4 | 12+6 | 294x196 | fp32, attention whole | 11.6 | 69.7 / 69.5 | 0.3 | 6.17 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 4 | 12+6 | 294x196 | fp32, inference_mode | 9.0 | 70.0 / 70.9 | 0.3 | 6.2 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 4 | 12+6 | 294x196 | fp32, mem8G | 12.9 | 72.4 / 71.9 | 0.3 | 6.27 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 4 | 12+6 | 294x196 | fp32, with point head | 9.1 | 77.3 / 77.0 | 0.3 | 6.41 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 4 | 12+6 | 294x196 | int8 | 9.2 | 49.6 / 49.2 | 0.5 | 7.51 | 0, 0 | 0, 0 | - | 8.0% | FAIL |
| EPYC 7452 | 4 | 8+4 | 294x196 | fp32 | 9.3 | 48.7 / 49.0 | 0.2 | 5.96 | 0, 0 | 0, 0 | - | 5.7% | FAIL |
| EPYC 7452 | 4 | 24+12 | 224x154 | fp32 | 32.1 | 98.5 / 98.7 | 0.9 | 6.08 | 0, 0 | 0, 0 | - | 9.0% | FAIL |
| EPYC 7452 | 4 | 16+8 | 224x154 | fp32 | 9.2 | 59.9 / 59.6 | 0.4 | 5.8 | 0, 0 | 0, 0 | - | 8.7% | FAIL |
| EPYC 7452 | 4 | 12+6 | 224x154 | fp32 | 9.3 | 43.4 / 43.1 | 0.3 | 5.81 | 0, 0 | 0, 0 | - | 8.8% | FAIL |
| EPYC 7452 | 4 | 8+4 | 224x154 | fp32 | 9.4 | 27.4 / 27.1 | 0.1 | 5.74 | 0, 0 | 0, 0 | - | 8.5% | FAIL |
| EPYC 7452 | 8 | 8+4 | 518x350 | fp32 | 8.8 | 93.9 / 93.3 | 0.6 | 7.38 | 0, 0 | 1, 0 | 67 | 0.7% | PASS |
| EPYC 7452 | 8 | 12+6 | 294x196 | fp32 | 32.1 | 33.6 / 33.5 | 0.3 | 6.21 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 8 | 24+12 | 224x154 | fp32 | 8.9 | 43.4 / 43.4 | 0.9 | 6.03 | 0, 0 | 0, 0 | - | 9.0% | FAIL |
| EPYC 7452 | 16 | 24+12 | 518x350 | fp32 | 8.8 | 332.6 / 326.4 | 6.2 | 8.49 | 0, 0 | 1, 0 | 161 | 0.7% | PASS |
| EPYC 7452 | 16 | 12+6 | 294x196 | fp32 | 32.1 | 20.5 / 20.4 | 0.4 | 6.21 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 16 | 24+12 | 224x154 | fp32 | 8.6 | 26.4 / 26.2 | 0.9 | 6.12 | 0, 0 | 0, 0 | - | 9.0% | FAIL |
| EPYC 7452 | 32 | 24+12 | 518x350 | fp32 | 42.7 | 195.6 / 195.4 | 6.3 | 8.51 | 0, 0 | 1, 0 | 161 | 0.7% | PASS |
| EPYC 7452 | 32 | 24+12 | 518x350 | fp32, attention whole | 26.6 | 143.6 / 145.9 | 6.3 | 8.36 | 0, 0 | 1, 0 | 161 | 0.7% | PASS |
| EPYC 7452 | 32 | 16+8 | 518x350 | fp32 | 8.8 | 105.4 / 104.1 | 2.8 | 8.04 | 0, 0 | 1, 0 | 133 | 0.8% | PASS |
| EPYC 7452 | 32 | 12+6 | 518x350 | fp32 | 8.5 | 57.2 / 57.3 | 1.7 | 7.85 | 0, 0 | 1, 0 | 100 | 0.7% | PASS |
| EPYC 7452 | 32 | 8+4 | 518x350 | fp32 | 8.6 | 34.0 / 34.0 | 0.7 | 7.5 | 0, 0 | 1, 0 | 67 | 0.7% | PASS |
| EPYC 7452 | 32 | 24+12 | 392x266 | fp32 | 42.8 | 74.3 / 72.5 | 2.5 | 7.1 | 0, 0 | 1, 0 | 15 | 4.5% | PASS |
| EPYC 7452 | 32 | 16+8 | 392x266 | fp32 | 8.5 | 41.3 / 41.1 | 1.1 | 6.78 | 0, 0 | 1, 0 | 19 | 4.9% | PASS |
| EPYC 7452 | 32 | 12+6 | 392x266 | fp32 | 8.5 | 27.7 / 28.0 | 0.8 | 6.68 | 0, 1 | 1, 1 | 20 | 2.8% | FAIL |
| EPYC 7452 | 32 | 8+4 | 392x266 | fp32 | 8.5 | 16.2 / 16.2 | 0.4 | 6.79 | 0, 0 | 1, 0 | 8 | 2.5% | PASS |
| EPYC 7452 | 32 | 24+12 | 294x196 | fp32 | 8.5 | 33.3 / 32.9 | 1.4 | 6.27 | 0, 0 | 0, 0 | - | 8.4% | FAIL |
| EPYC 7452 | 32 | 16+8 | 294x196 | fp32 | 8.5 | 18.3 / 17.3 | 0.6 | 6.7 | 0, 0 | 0, 0 | - | 9.2% | FAIL |
| EPYC 7452 | 32 | 12+6 | 294x196 | fp32 | 8.7 | 13.0 / 11.9 | 0.4 | 6.45 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| EPYC 7452 | 32 | 8+4 | 294x196 | fp32 | 8.7 | 7.9 / 7.7 | 0.2 | 6.16 | 0, 0 | 0, 0 | - | 5.7% | FAIL |
| EPYC 7452 | 32 | 24+12 | 224x154 | fp32 | 8.6 | 16.2 / 16.3 | 0.9 | 6.08 | 0, 0 | 0, 0 | - | 9.0% | FAIL |
| EPYC 7452 | 32 | 16+8 | 224x154 | fp32 | 8.6 | 9.6 / 9.4 | 0.4 | 5.91 | 0, 0 | 0, 0 | - | 8.7% | FAIL |
| EPYC 7452 | 32 | 12+6 | 224x154 | fp32 | 8.5 | 7.1 / 7.0 | 0.3 | 5.87 | 0, 0 | 0, 0 | - | 8.8% | FAIL |
| EPYC 7452 | 32 | 8+4 | 224x154 | fp32 | 8.6 | 4.3 / 4.4 | 0.1 | 5.79 | 0, 0 | 0, 0 | - | 8.5% | FAIL |
| Xeon Gold 5418Y | 4 | 24+12 | 518x350 | fp32 | 18.0 | 555.5 / - | 4.6 | 8.14 | 0, 0 | -, - | - | - | one clip only |
| Xeon Gold 5418Y | 4 | 16+8 | 518x350 | fp32 | 7.4 | 248.9 / 250.7 | 2.0 | 7.81 | 0, 0 | 1, 0 | 133 | 0.8% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 518x350 | fp32 | 7.5 | 148.2 / 143.3 | 1.4 | 7.65 | 0, 0 | 1, 0 | 100 | 0.7% | PASS |
| Xeon Gold 5418Y | 4 | 8+4 | 518x350 | bf16 | 13.1 | 34.3 / 32.0 | 0.5 | 8.69 | 0, 0 | 1, 0 | 65 | 0.7% | PASS |
| Xeon Gold 5418Y | 4 | 8+4 | 518x350 | fp32 | 8.6 | 75.4 / 75.6 | 0.5 | 7.44 | 0, 0 | 1, 0 | 66 | 0.7% | PASS |
| Xeon Gold 5418Y | 4 | 8+4 | 518x350 | int8 | 13.3 | 65.1 / 62.8 | 0.6 | 8.68 | 0, 0 | 0, 0 | - | 2.8% | FAIL |
| Xeon Gold 5418Y | 4 | 6+3 | 518x350 | fp32 | 18.0 | 53.5 / 55.3 | 0.4 | 7.26 | 0, 0 | 1, 0 | 61 | 0.6% | PASS |
| Xeon Gold 5418Y | 4 | 6+3 | 518x350 | int8 | 8.9 | 41.3 / 39.9 | 0.4 | 8.43 | 0, 0 | 0, 0 | - | 2.4% | FAIL |
| Xeon Gold 5418Y | 4 | 8+4 | 448x308 | fp32 | 9.3 | 53.9 / 53.0 | 0.4 | 6.89 | 0, 0 | 1, 0 | 65 | 1.1% | PASS |
| Xeon Gold 5418Y | 4 | 24+12 | 392x266 | fp32 | 13.0 | 182.8 / 174.9 | 1.9 | 7.09 | 0, 0 | 0, 0 | - | 4.5% | FAIL |
| Xeon Gold 5418Y | 4 | 16+8 | 392x266 | fp32 | 12.6 | 91.4 / 88.8 | 0.9 | 6.73 | 0, 0 | 1, 0 | 19 | 4.9% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 392x266 | fp32 | 10.4 | 57.0 / 58.2 | 0.6 | 6.63 | 0, 1 | 1, 1 | 20 | 2.8% | FAIL |
| Xeon Gold 5418Y | 4 | 8+4 | 392x266 | fp32 | 10.9 | 32.6 / 30.7 | 0.2 | 6.83 | 0, 0 | 1, 0 | 8 | 2.5% | PASS |
| Xeon Gold 5418Y | 4 | 24+12 | 294x196 | fp32 | 27.2 | 76.6 / 71.8 | 1.2 | 6.25 | 0, 0 | 0, 0 | - | 8.4% | FAIL |
| Xeon Gold 5418Y | 4 | 24+12 | 294x196 | fp32, scaled window | 7.3 | 82.5 / 80.6 | 1.3 | 6.29 | 0, 0 | 0, 0 | - | 8.4% | FAIL |
| Xeon Gold 5418Y | 4 | 16+8 | 294x196 | fp32 | 12.7 | 38.0 / 35.2 | 0.4 | 6.41 | 0, 0 | 0, 0 | - | 9.2% | FAIL |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | bf16 | 7.1 | 11.4 / 9.9 | 0.3 | 7.67 | 1, 0 | 0, 0 | - | 6.9% | FAIL |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | fp32 | 27.2 | 30.6 / 26.9 | 0.3 | 6.31 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | fp32, attention 1024 MB slices | 9.5 | 28.5 / 27.6 | 0.3 | 6.08 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | fp32, attention 64 MB slices | 13.2 | 31.5 / 31.3 | 0.3 | 6.21 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | fp32, attention whole | 8.3 | 29.4 / 27.9 | 0.3 | 6.28 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | fp32, inference_mode | 9.2 | 29.3 / 28.3 | 0.3 | 6.27 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | fp32, scaled window | 12.8 | 27.9 / 27.9 | 0.3 | 6.26 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 294 | fp32, torch.compile | - | - / - | - | 10.46 | -, - | -, - | - | - | ERROR: RuntimeError: Dynamo is not supported on Python 3.12+ |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | fp32, with point head | 7.5 | 32.6 / 32.7 | 0.3 | 6.45 | 0, 0 | 1, 0 | 8 | 6.8% | PASS |
| Xeon Gold 5418Y | 4 | 12+6 | 294x196 | int8 | 9.0 | 17.3 / 16.0 | 0.3 | 7.45 | 0, 0 | 1, 0 | 9 | 7.3% | PASS |
| Xeon Gold 5418Y | 4 | 8+4 | 294x196 | fp32 | 13.0 | 16.8 / 16.8 | 0.1 | 6.03 | 0, 0 | 0, 0 | - | 5.7% | FAIL |
| Xeon Gold 5418Y | 4 | 8+4 | 294x196 | fp32, scaled window | 8.0 | 18.3 / 17.9 | 0.2 | 6.11 | 0, 0 | 0, 0 | - | 5.7% | FAIL |
| Xeon Gold 5418Y | 4 | 24+12 | 224x154 | fp32 | 27.2 | 38.4 / 34.8 | 0.8 | 6.12 | 0, 0 | 0, 0 | - | 9.0% | FAIL |
| Xeon Gold 5418Y | 4 | 24+12 | 224x154 | fp32, scaled window | 7.3 | 35.1 / 36.8 | 0.9 | 6.02 | 0, 0 | 0, 0 | - | 9.0% | FAIL |
| Xeon Gold 5418Y | 4 | 16+8 | 224x154 | fp32 | 7.3 | 21.1 / 19.6 | 0.3 | 5.9 | 0, 0 | 0, 0 | - | 8.7% | FAIL |
| Xeon Gold 5418Y | 4 | 12+6 | 224x154 | fp32 | 7.2 | 15.0 / 14.4 | 0.2 | 5.82 | 0, 0 | 0, 0 | - | 8.8% | FAIL |
| Xeon Gold 5418Y | 4 | 12+6 | 224x154 | fp32, scaled window | 9.3 | 14.4 / 13.8 | 0.2 | 5.86 | 0, 0 | 0, 0 | - | 8.8% | FAIL |
| Xeon Gold 5418Y | 4 | 8+4 | 224x154 | fp32 | 10.1 | 10.1 / 8.6 | 0.1 | 5.77 | 0, 0 | 0, 0 | - | 8.5% | FAIL |
| Xeon Gold 5418Y | 4 | 8+4 | 224x154 | fp32, scaled window | 17.3 | 11.4 / 10.1 | 0.1 | 5.83 | 0, 0 | 0, 0 | - | 8.5% | FAIL |
<!-- table:end -->

## Not done or not verified

- **The demo laptop was not measured.** All laptop figures are from 4 cores of server CPUs.
- **One scene.** Pass or fail rests on one toy scene with a painted cube. A real moved object, a
  bigger room or a smaller object could shift where the settings stop working.
- **OpenVINO export does not work out of the box, and ONNX was not tried.** One attempt with
  `export_probe.py` (OpenVINO 2026.4.1, `ov.convert_model` on the traced model, 8+4 frames at
  long side 392, 4 EPYC cores) failed after about 8 minutes with "Model wasn't fully converted":
  it stops at the shape list of a LayerNorm and at a string constant. The attempt peaked at
  36.5 GB. So there is no speed number for a faster CPU runtime.
- **4 EPYC cores at 24+12 and long side 518** was not run: it would not fit in a 20-minute job.
  On 4 Xeon cores the unchanged clip took 556 s and came out right (no change); the cube clip
  was not run at that setting on 4 cores.
- **bfloat16 on the EPYC** gave no number: the job hit its time limit before the first clip ended.
- **Saving a quantised model to disk** and loading it directly, which might cut int8 memory, was
  not tried. int8 loses on quality first: it fails four of six runs.
- **Most rows were not re-run with `--attn-chunk-mb 0`**, which is the faster way to run it at
  long side 518. Only three settings have both.
- Each setting ran once per CPU, apart from the repeats listed under "Spread".

## Run it again

On the cluster, from the folder that holds `recon_change.py`, with this folder as `cpu/` and the three
clips in `cpu/clips/`:

```bash
sbatch --cpus-per-task=4 --mem=24G cpu/bench_cpu.slurm 4:cpu/plans/c4_few_a.txt
```

Each line of a plan file is one setting. Results are appended to `cpu/results/<plan>.jsonl`:

```bash
python cpu/make_table.py cpu/results/*.jsonl
```

On a laptop with the same Python packages and the model already downloaded:

```bash
python cpu/bench_cpu.py --baseline baseline.mp4 --clips after.mp4 after_box.mp4 --frames 6+3 --long-side 518 --threads 4 --attn-chunk-mb 0
```
