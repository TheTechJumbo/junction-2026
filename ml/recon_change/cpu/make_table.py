#!/usr/bin/env python3
"""Turn the JSON lines bench_cpu.py wrote into the markdown table in RESULTS.md.

    python cpu/make_table.py cpu/results/*.jsonl
"""
import json
import sys
from pathlib import Path


def trick(s, r_tag=""):
    parts = [s["precision"]]
    if s["grad"] != "no_grad":
        parts.append(s["grad"])
    if s["compile"]:
        parts.append("torch.compile")
    if s["point_head"]:
        parts.append("with point head")
    if s["attn_chunk_mb"] != 256:
        parts.append("attention whole" if s["attn_chunk_mb"] == 0 else f"attention {s['attn_chunk_mb']} MB slices")
    if s.get("scale_window"):
        parts.append("scaled window")
    if r_tag:
        parts.append(r_tag)
    return ", ".join(parts)


def cpu_name(r):
    model = r.get("cpu", {}).get("model", "?")
    for key, short in (("7452", "EPYC 7452"), ("5418Y", "Xeon Gold 5418Y")):
        if key in model:
            return short
    return model


def main():
    rows = []
    for path in sys.argv[1:]:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                rows.append((Path(path).stem, json.loads(line)))
    print("| CPU | Cores | Frames | Input | Variant | Load s | Inference s (after / after_box) | Detect s | Peak GB | "
          "after: appeared, gone | after_box: appeared, gone | Cube voxels | Depth noise | Result |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")

    def key(item):
        r = item[1]
        s = r.get("settings", {})
        return (cpu_name(r), r.get("cores_used", 0), -s.get("long_side", 0), -int(s.get("frames", "0+0").split("+")[0]), trick(s, r.get("tag", "")) if s else "")

    for plan, r in sorted(rows, key=key):
        if "settings" not in r:  # the process was killed before it could report
            print(f"| ? | {r.get('cores_used', '?')} | | | `{r.get('args', plan)}` | | | | | | | | | KILLED ({r.get('error')}) |")
            continue
        s, clips = r["settings"], r.get("clips", {})

        def cell(name, field):
            return str(clips[name][field]) if name in clips else "-"

        box = clips.get("after_box", {})
        voxels = ", ".join(map(str, box.get("appeared_voxels", []))) or "-"  # size of the cluster found for the cube
        noise = f"{100 * box['depth_noise']:.1f}%" if box else "-"  # depth noise inside the baseline clip
        outcome = {True: "PASS", False: "FAIL", None: "one clip only"}[r.get("passes")]
        if "error" in r:
            outcome = f"ERROR: {r['error'][:90]}"
        print(f"| {cpu_name(r)} | {r['cores_used']} | {s['frames']} | {s.get('input', s['long_side'])} | {trick(s, r.get('tag', ''))} | {r.get('load_s', '-')} | "
              f"{cell('after', 'inference_s')} / {cell('after_box', 'inference_s')} | {r.get('detect_s', '-')} | "
              f"{r.get('peak_rss_gb', '-')} | {cell('after', 'appeared')}, {cell('after', 'gone')} | "
              f"{cell('after_box', 'appeared')}, {cell('after_box', 'gone')} | {voxels} | {noise} | {outcome} |")


if __name__ == "__main__":
    main()
