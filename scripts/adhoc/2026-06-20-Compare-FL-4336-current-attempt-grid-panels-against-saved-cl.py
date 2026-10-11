# Ad hoc script: Compare FL-4336 current attempt grid panels against saved clone panels and prior current panels
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

from pathlib import Path
from PIL import Image
import argparse
import json
import statistics


def stats_for(im):
    vals = []
    rg = []
    bg = []
    pixels = list(im.getdata())
    for r, g, b in pixels:
        vals.append(0.2126 * r + 0.7152 * g + 0.0722 * b)
        rg.append(r - g)
        bg.append(b - g)
    vals_sorted = sorted(vals)
    def pct(p):
        return vals_sorted[int((len(vals_sorted) - 1) * p)]
    return {
        "mean_luma": round(statistics.fmean(vals), 3),
        "p05": round(pct(0.05), 3),
        "p10": round(pct(0.10), 3),
        "p25": round(pct(0.25), 3),
        "p50": round(pct(0.50), 3),
        "p75": round(pct(0.75), 3),
        "p90": round(pct(0.90), 3),
        "p95": round(pct(0.95), 3),
        "dark_frac_lt120": round(sum(v < 120 for v in vals) / len(vals), 4),
        "bright_frac_gt230": round(sum(v > 230 for v in vals) / len(vals), 4),
        "unique_rgb": len(set(pixels)),
        "mean_r_minus_g": round(statistics.fmean(rg), 3),
        "mean_b_minus_g": round(statistics.fmean(bg), 3),
    }


def diff_stats(a, b):
    diffs = []
    exact = 0
    total = 0
    for pa, pb in zip(a.getdata(), b.getdata()):
        if pa == pb:
            exact += 1
        total += 1
        diffs.append(sum(abs(pa[i] - pb[i]) for i in range(3)) / 3.0)
    ds = sorted(diffs)
    return {
        "exact_frac": round(exact / max(total, 1), 6),
        "mean_abs": round(statistics.fmean(diffs), 3),
        "p75_abs": round(ds[int((len(ds) - 1) * 0.75)], 3),
        "p90_abs": round(ds[int((len(ds) - 1) * 0.90)], 3),
        "p95_abs": round(ds[int((len(ds) - 1) * 0.95)], 3),
    }


def open_rgb(path):
    return Image.open(path).convert("RGB")


ap = argparse.ArgumentParser()
ap.add_argument("--current-root", required=True)
ap.add_argument("--clone-root", required=True)
ap.add_argument("--previous-root")
ap.add_argument("--out", required=True)
args = ap.parse_args()
current_root = Path(args.current_root)
clone_root = Path(args.clone_root)
previous_root = Path(args.previous_root) if args.previous_root else None
rows = []
for yaw in (30, 150, 270):
    current = open_rgb(current_root / f"yaw{yaw}" / "fl4270_ortho.png")
    clone = open_rgb(clone_root / f"clone_y{yaw}" / "fl4270_ortho.png")
    rows.append({"yaw": yaw, "panel": "current", **stats_for(current)})
    rows.append({"yaw": yaw, "panel": "clone", **stats_for(clone)})
    rows.append({"yaw": yaw, "panel": "current_vs_clone", **diff_stats(current, clone)})
    if previous_root:
        previous = open_rgb(previous_root / f"yaw{yaw}" / "fl4270_ortho.png")
        rows.append({"yaw": yaw, "panel": "current_vs_previous", **diff_stats(current, previous)})
Path(args.out).write_text(json.dumps(rows, indent=2), encoding="utf-8")
print(args.out)
print(json.dumps(rows, indent=2))
