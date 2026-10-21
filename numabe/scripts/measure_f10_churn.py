#!/usr/bin/env python3
"""Measure F10 churn/vocabulary/density over an F9/F10 frames directory.

Reproduces the FF-DPR-26 pixel-domain method (FL-4359 measurement block):
per-cell 8x16 binary signatures by luminance threshold over the frame PNGs
in manifest order, then density / changed / motion-vs-flip / blank-toggle /
connected-component / vocabulary / Hamming-16-cluster numbers.

Reads manifest.txt `frame:` lines with the same rule as
scripts/build_f10_frames_html.py (first token `frame:`, remaining tokens
key=value; the `png` value names the frame file, `matrix=[C,R]` and
`cell=WxH` are geometry hints). Falls back to sorted frame_*.png when no
manifest frame lines exist. Never modifies the input directory.

Method choices (documented because the FL prose does not pin them):
- Each frame is converted to luma and reduced to (cols*sig_cols,
  rows*sig_rows) with PIL BOX (exact area means) by default or BILINEAR
  under --resample. When the manifest names the raster cell pitch
  (cell=WxH:...) and that pitch reproduces the matrix (ceil(image/pitch) ==
  matrix), the frame is first placed on a black (cols*W, rows*H) canvas
  anchored top-left, because the FF raster addresses cell = pixel // pitch
  and clips the last row/column at the image edge (FL-4873: 225x71 cells of
  16x32 over a 3600x2252 image; row 70 is 12 px). Without that pitch the
  image is spread proportionally as before. Default signature grid is 8x16, so a 16x32 cell
  becomes exact 2x2 block means. Non-integer cell pitch is handled by the
  same resize.
- Main pass: bit = (block mean >= threshold, default 1, i.e. any ink).
  Blank cell = all-zero signature.
- Control pass (intensity-invariant re-binarization): bit = (block mean >
  this cell's own mean over its sig grid). Only changed/flip medians are
  emitted for the control. Uniform cells stay blank under strict >.
- Flip rule for a change A->B at pair (t, t+1): flip when the signature
  returns to A at t+2 (A-B-A) or t+3 (A-B-C-A); otherwise motion. Pairs
  whose return window runs past the last frame count as motion.
- Blank toggles count cells whose blank status differs between frames
  (either direction); the median over pairs is emitted.
- Vocabulary, coverage and Hamming clustering run over non-blank cells
  only. Hamming-16 clusters are greedy in (count desc, hex asc) order:
  each signature joins the first center within distance 16, else it
  becomes a center.
- Components use 4-connectivity over non-blank cells.
- Quantiles: median is the middle value (mean of the two middle values
  for even counts); p25/p75 use linear interpolation at (n-1)*q.

Output: exactly one JSON object on stdout. Diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

SIG_COLS_DEFAULT = 8
SIG_ROWS_DEFAULT = 16
THRESHOLD = 1
FLIP_LOOKAHEAD = (2, 3)


def parse_cell_px(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", text)
    if not match:
        raise ValueError(f"expected WxH, got {text!r}")
    return (int(match.group(1)), int(match.group(2)))


def manifest_frame_pngs(frames_dir: Path) -> tuple[list[str], dict]:
    """Return (ordered png names, geometry hints) from manifest.txt."""
    hints: dict = {}
    manifest = frames_dir / "manifest.txt"
    if not manifest.exists():
        return ([], hints)
    pngs: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[0] != "frame:":
            continue
        meta = {}
        for token in parts[1:]:
            if "=" in token:
                key, value = token.split("=", 1)
                meta[key] = value
        if "png" not in meta:
            continue
        pngs.append(meta["png"])
        for key in ("matrix", "cell"):
            if key in meta and key not in hints:
                hints[key] = meta[key]
    return (pngs, hints)


def raster_pitch(hints: dict, cols: int, rows: int, size: tuple[int, int]) -> tuple[int, int] | None:
    """Raster cell pitch from the manifest when it reproduces the matrix."""
    match = re.match(r"\s*(\d+)\s*[xX]\s*(\d+)", hints.get("cell", ""))
    if not match:
        return None
    cw, ch = int(match.group(1)), int(match.group(2))
    w, h = size
    if cw <= 0 or ch <= 0 or -(-w // cw) != cols or -(-h // ch) != rows:
        return None
    return (cw, ch)


def resolve_grid(args, hints: dict) -> tuple[int, int]:
    cols = args.cols
    rows = args.rows
    if cols is None or rows is None:
        matrix = hints.get("matrix", "")
        match = re.fullmatch(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", matrix)
        if match:
            cols = cols if cols is not None else int(match.group(1))
            rows = rows if rows is not None else int(match.group(2))
    if cols is None or rows is None:
        raise SystemExit(
            "error: need --cols/--rows (no matrix=[C,R] hint in manifest.txt)"
        )
    return (cols, rows)


def frame_signatures(
    gray_small: bytes, cols: int, rows: int, sig_cols: int, sig_rows: int,
    control: bool, threshold: int = THRESHOLD,
) -> list[int]:
    """Pack one downsampled frame into per-cell integer signatures."""
    bits_per_cell = sig_cols * sig_rows
    sigs: list[int] = []
    for cy in range(rows):
        for cx in range(cols):
            base_x = cx * sig_cols
            base_y = cy * sig_rows
            stride = cols * sig_cols
            vals = []
            for sy in range(sig_rows):
                off = (base_y + sy) * stride + base_x
                vals.extend(gray_small[off:off + sig_cols])
            if control:
                mean = sum(vals) / bits_per_cell
                sig = 0
                for k, v in enumerate(vals):
                    if v > mean:
                        sig |= 1 << k
            else:
                sig = 0
                for k, v in enumerate(vals):
                    if v >= THRESHOLD:
                        sig |= 1 << k
            sigs.append(sig)
    return sigs


def popcount(sig: int) -> int:
    return bin(sig).count("1")


def median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def quantile_linear(values: list[float], q: float) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 1:
        return float(ordered[0])
    pos = (n - 1) * q
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return float(ordered[lo])
    return ordered[lo] + (ordered[lo + 1] - ordered[lo]) * frac


def connected_components(nonblank: list[bool], cols: int, rows: int) -> list[int]:
    """4-connectivity component sizes over the non-blank mask."""
    seen = [False] * len(nonblank)
    sizes: list[int] = []
    for start in range(len(nonblank)):
        if not nonblank[start] or seen[start]:
            continue
        stack = [start]
        seen[start] = True
        size = 0
        while stack:
            cell = stack.pop()
            size += 1
            x = cell % cols
            y = cell // cols
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if 0 <= nx < cols and 0 <= ny < rows:
                    ncell = ny * cols + nx
                    if nonblank[ncell] and not seen[ncell]:
                        seen[ncell] = True
                        stack.append(ncell)
        sizes.append(size)
    return sizes


def hamming_clusters(signatures: list[int]) -> int:
    """Greedy Hamming-16 cluster count over distinct signatures."""
    counts = Counter(signatures)
    ordered = sorted(counts, key=lambda s: (-counts[s], s))
    centers: list[int] = []
    for sig in ordered:
        for center in centers:
            if popcount(sig ^ center) <= 16:
                break
        else:
            centers.append(sig)
    return len(centers)


def render_signature(sig: int, sig_cols: int, sig_rows: int) -> list[str]:
    art = []
    for sy in range(sig_rows):
        row = "".join(
            "#" if (sig >> (sy * sig_cols + sx)) & 1 else " "
            for sx in range(sig_cols)
        )
        art.append(row)
    return art


def measure(
    frames_dir: Path, cols: int, rows: int, sig_cols: int, sig_rows: int,
    component_frame: int, threshold: int = THRESHOLD,
    resample: int = Image.BOX,
) -> dict:
    pngs, hints = manifest_frame_pngs(frames_dir)
    if not pngs:
        pngs = sorted(p.name for p in frames_dir.glob("frame_*.png"))
    if not pngs:
        raise SystemExit(f"error: no frames in {frames_dir}")
    if not 0 <= component_frame < len(pngs):
        raise SystemExit(
            f"error: --component-frame {component_frame} outside 0..{len(pngs) - 1}"
        )
    n_cells = cols * rows
    bits_per_cell = sig_cols * sig_rows

    densities: list[float] = []
    ink_fractions: list[float] = []
    all_sigs: list[list[int]] = []
    control_sigs: list[list[int]] = []
    for name in pngs:
        with Image.open(frames_dir / name) as im:
            gray = im.convert("L")
            pitch = raster_pitch(hints, cols, rows, gray.size)
            if pitch is not None:
                canvas = Image.new("L", (cols * pitch[0], rows * pitch[1]), 0)
                canvas.paste(gray, (0, 0))
                gray = canvas
            gray = gray.resize(
                (cols * sig_cols, rows * sig_rows), resample
            )
            raw = gray.tobytes()
        sigs = frame_signatures(
            raw, cols, rows, sig_cols, sig_rows, False, threshold=threshold
        )
        all_sigs.append(sigs)
        control_sigs.append(
            frame_signatures(raw, cols, rows, sig_cols, sig_rows, True)
        )
        nonblank = sum(1 for s in sigs if s != 0)
        densities.append(nonblank / n_cells)
        for s in sigs:
            if s != 0:
                ink_fractions.append(popcount(s) / bits_per_cell)

    def pair_stats(frames: list[list[int]]) -> tuple[list[float], list[float]]:
        changed: list[float] = []
        flips: list[float] = []
        n_pairs = len(frames) - 1
        for t in range(n_pairs):
            prev = frames[t]
            cur = frames[t + 1]
            changed_cells = [c for c in range(n_cells) if cur[c] != prev[c]]
            changed.append(len(changed_cells) / n_cells)
            n_flip = 0
            for c in changed_cells:
                before = prev[c]
                if (t + 2 < len(frames) and frames[t + 2][c] == before) or (
                    t + 3 < len(frames) and frames[t + 3][c] == before
                ):
                    n_flip += 1
            flips.append(n_flip / n_cells)
        return (changed, flips)
    changed, flips = pair_stats(all_sigs)
    motion = [c - f for c, f in zip(changed, flips)]
    if not changed:
        changed = flips = motion = [0.0]
    blank_toggles = []
    for t in range(len(all_sigs) - 1):
        prev = all_sigs[t]
        cur = all_sigs[t + 1]
        toggles = sum(
            1
            for c in range(n_cells)
            if (prev[c] == 0) != (cur[c] == 0)
        )
        blank_toggles.append(toggles / n_cells)
    if not blank_toggles:
        blank_toggles = [0.0]
    control_changed, control_flips = pair_stats(control_sigs)
    if not control_changed:
        control_changed = control_flips = [0.0]

    comp_sigs = all_sigs[component_frame]
    mask = [s != 0 for s in comp_sigs]
    comp_sizes = connected_components(mask, cols, rows)
    nonblank_comp = sum(mask)

    vocab = Counter(s for frame in all_sigs for s in frame if s != 0)
    samples = sum(vocab.values())
    ranked = sorted(vocab.items(), key=lambda kv: (-kv[1], kv[0]))
    n50 = n90 = 0
    running = 0
    for i, (_, count) in enumerate(ranked, start=1):
        running += count
        if n50 == 0 and running >= samples / 2:
            n50 = i
        if running >= samples * 0.9:
            n90 = i
            break

    top = [
        {
            "hex": format(sig, f"0{bits_per_cell // 4}x"),
            "count": count,
            "art": render_signature(sig, sig_cols, sig_rows),
        }
        for sig, count in ranked[:24]
    ]

    total_changed = sum(
        1
        for t in range(len(all_sigs) - 1)
        for c in range(n_cells)
        if all_sigs[t + 1][c] != all_sigs[t][c]
    )
    total_flip = sum(
        1
        for t in range(len(all_sigs) - 1)
        for c in range(n_cells)
        if all_sigs[t + 1][c] != all_sigs[t][c]
        and (
            (t + 2 < len(all_sigs) and all_sigs[t + 2][c] == all_sigs[t][c])
            or (t + 3 < len(all_sigs) and all_sigs[t + 3][c] == all_sigs[t][c])
        )
    )

    return {
        "frames": len(pngs),
        "cols": cols,
        "rows": rows,
        "threshold": threshold,
        "resample": {Image.BOX: "box", Image.BILINEAR: "bilinear",
                     Image.NEAREST: "nearest"}[resample],
        "density": {
            "median": round(median(densities), 4),
            "min": round(min(densities), 4),
            "max": round(max(densities), 4),
        },
        "ink_coverage_p50": round(median(ink_fractions), 4)
        if ink_fractions else 0.0,
        "changed": {
            "median": round(median(changed), 4),
            "p25": round(quantile_linear(changed, 0.25), 4),
            "p75": round(quantile_linear(changed, 0.75), 4),
            "max": round(max(changed), 4),
        },
        "motion_median": round(median(motion), 4),
        "flip": {
            "median": round(median(flips), 4),
            "max": round(max(flips), 4),
        },
        "flip_share_of_changes": round(total_flip / total_changed, 4)
        if total_changed
        else 0.0,
        "blank_toggle_rate": round(median(blank_toggles), 4),
        "control": {
            "changed_median": round(median(control_changed), 4),
            "flip_median": round(median(control_flips), 4),
        },
        "components": {
            "frame": component_frame,
            "nonblank_cells": nonblank_comp,
            "components": len(comp_sizes),
            "largest_share": round(max(comp_sizes) / nonblank_comp, 4)
            if nonblank_comp
            else 0.0,
        },
        "vocabulary": {
            "exact_signatures": len(vocab),
            "samples": samples,
            "n_for_50pct": n50,
            "n_for_90pct": n90,
        },
        "hamming16_clusters": hamming_clusters(list(vocab)),
        "top_signatures": top,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FF-DPR-26 churn/vocabulary/density meter over frame PNGs."
    )
    parser.add_argument("frames_dir", type=Path)
    parser.add_argument("--cols", type=int, default=None)
    parser.add_argument("--rows", type=int, default=None)
    parser.add_argument(
        "--cell-px", default=None,
        help="signature sub-grid WxH per cell (default 8x16)",
    )
    parser.add_argument(
        "--threshold", type=int, default=THRESHOLD,
        help="main-pass luma threshold for ink (default %(default)s)",
    )
    parser.add_argument(
        "--resample", choices=("box", "bilinear", "nearest"), default="box",
        help="area-mean (box), bilinear, or nearest downsampling to the signature grid",
    )
    parser.add_argument("--component-frame", type=int, default=None)
    args = parser.parse_args(argv)
    if not args.frames_dir.is_dir():
        print(f"error: not a directory: {args.frames_dir}", file=sys.stderr)
        return 2
    _, hints = manifest_frame_pngs(args.frames_dir)
    cols, rows = resolve_grid(args, hints)
    if args.cell_px is None:
        sig_cols, sig_rows = SIG_COLS_DEFAULT, SIG_ROWS_DEFAULT
    else:
        sig_cols, sig_rows = parse_cell_px(args.cell_px)

    png_count = len(manifest_frame_pngs(args.frames_dir)[0]) or len(
        sorted(args.frames_dir.glob("frame_*.png"))
    )
    component_frame = (
        args.component_frame if args.component_frame is not None
        else png_count // 2
    )
    try:
        result = measure(
            args.frames_dir, cols, rows, sig_cols, sig_rows, component_frame,
            threshold=args.threshold,
            resample={"box": Image.BOX, "bilinear": Image.BILINEAR,
                      "nearest": Image.NEAREST}[args.resample],
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
