#!/usr/bin/env python3
# Ad hoc script: FL-4057 probe-vs-legacy headed parity diff (mean abs diff,
# pct-near, side-by-side + heatmap).
# Created: 2026-06-29
#
# Canonical gap: a first-class "compare two PixelCamera capture PNGs at the same
# pinned pose and emit parity metrics + grid artifacts" command does not exist in
# analyze_runs.py. Until it does, this reproduces the FL-4057 A/B headed-proof
# numbers. Inputs are explicit (argv), deps/inputs/size mismatches FAIL HARD
# (nonzero exit) so a broken capture can never be silently scored.
#
# Usage:
#   python3 <this> <legacy.png> <probe.png> [out_dir]
#   (defaults: /tmp/baseline_legacy.png /tmp/probe_fixed.png /tmp)
# Exit codes: 0 = diff computed; 2 = missing deps; 3 = missing/!=size inputs.

import json
import os
import sys

LEGACY = sys.argv[1] if len(sys.argv) > 1 else "/tmp/baseline_legacy.png"
PROBE = sys.argv[2] if len(sys.argv) > 2 else "/tmp/probe_fixed.png"
OUT = sys.argv[3] if len(sys.argv) > 3 else "/tmp"

try:
    from PIL import Image, ImageDraw
    import numpy as np
except Exception as e:  # deps are required; do NOT pretend success
    print(f"DEPS_MISSING: {e} (pip install pillow numpy)", file=sys.stderr)
    sys.exit(2)


def _label(img, text):
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 8 * len(text) + 12, 22], fill=(0, 0, 0))
    d.text((6, 5), text, fill=(255, 255, 0))
    return img

for p in (LEGACY, PROBE):
    if not os.path.isfile(p):
        print(f"INPUT_MISSING: {p}", file=sys.stderr)
        sys.exit(3)

a = Image.open(LEGACY).convert("RGB")
b = Image.open(PROBE).convert("RGB")
if a.size != b.size:
    # Resizing would mask a wrong-resolution/invalid capture; fail instead.
    print(f"SIZE_MISMATCH: legacy={a.size} probe={b.size}", file=sys.stderr)
    sys.exit(3)

A = np.asarray(a, dtype=np.int16)
B = np.asarray(b, dtype=np.int16)
d = np.abs(A - B)
per_px = d.max(axis=2)
metrics = {
    "legacy": os.path.abspath(LEGACY),
    "probe": os.path.abspath(PROBE),
    "size": list(a.size),
    "mean_abs_diff_255": round(float(d.mean()), 3),
    "max_diff": int(d.max()),
    "pct_identical": round(float((per_px == 0).mean() * 100), 2),
    "pct_within_8": round(float((per_px <= 8).mean() * 100), 2),
    "pct_within_24": round(float((per_px <= 24).mean() * 100), 2),
}

heat = (np.clip(per_px, 0, 64) / 64 * 255).astype("uint8")
heat_path = os.path.join(OUT, "probe_parity_heat.png")
sbs_path = os.path.join(OUT, "probe_parity_sbs.png")
json_path = os.path.join(OUT, "probe_parity_metrics.json")
Image.fromarray(heat).save(heat_path)
sbs = Image.new("RGB", (a.size[0], a.size[1] * 2))
sbs.paste(a, (0, 0))
sbs.paste(b, (0, a.size[1]))
sbs.save(sbs_path)

# Labeled 3-panel grid (A legacy | B probe | diff heatmap) for the review package.
grid_path = os.path.join(OUT, "probe_parity_grid_labeled.png")
al = _label(a.copy(), "A: LEGACY terrain (probe OFF)")
bl = _label(b.copy(), "B: PROBE lane (probe ON, --probe-texel-splat)")
hl = _label(Image.fromarray(heat).convert("RGB"), "DIFF max/px (black=identical, bright>=64)")
grid = Image.new("RGB", (a.size[0], a.size[1] * 3), (16, 16, 16))
grid.paste(al, (0, 0))
grid.paste(bl, (0, a.size[1]))
grid.paste(hl, (0, a.size[1] * 2))
grid.save(grid_path)
with open(json_path, "w") as f:
    json.dump(metrics, f, indent=2)

print(json.dumps(metrics, indent=2))
print(f"WROTE {heat_path} {sbs_path} {json_path} {grid_path}")
