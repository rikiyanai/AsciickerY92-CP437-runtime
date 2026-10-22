#!/usr/bin/env python3
# =============================================================================
# fl4359_cell_dump_gifs.py
# =============================================================================
# FL-4359 CELL DUMP VISUALS: turns the per-cell dump written by
# fl4359_cell_dump_extractor.py into the four GIF families that let a human see
# WHICH of the published lanes moved.
#
# WHY FOUR FAMILIES AND NOT ONE
# -----------------------------
# A raw side-by-side of two arms shows that the screen changed; it cannot show
# WHAT changed, because a glyph swap and a colour swap look alike at 1:1. The
# measurement separates the published cell into three independent lanes — the
# elected glyph MASK, the FG (ink) colour and the BG (paper) colour — so the
# visuals separate them too:
#
#   1. ab_sidebyside   arm A next to arm B, the unmodified captures. The
#                      reference for everything below.
#   2. ab_fg_highlight the same pair with the elected FG mask tinted, so a mask
#                      flip is visible even when both colours hold.
#   3. bg_only         every pixel replaced by its cell's BG colour. All glyph
#                      shape is removed, so what remains is pure paper churn.
#   4. fg_only         BG replaced by black, ink kept. All paper is removed, so
#                      what remains is pure glyph/ink churn.
#   5. fg_glyph_change_highlight
#                      MANDATORY. The cell grid with the FG GLYPH CHANGE marked:
#                      every cell whose elected glyph id differs from the one it
#                      elected in the previous frame has its appeared/vanished
#                      ink tinted amber, and every cell that held its glyph is
#                      dimmed. Family 2 shows WHERE the ink is; this one shows
#                      WHERE THE IDENTITY MOVED, which is the operator's
#                      original ask and the visual twin of the extractor's
#                      glyph-change-delta rollup.
#
# Families 3 and 4 are the decisive pair: shimmer that survives in bg_only is a
# material/colour owner defect; shimmer that survives only in fg_only is a glyph
# identity owner defect. Family 2 localizes it to the cell.
#
# ARM B IS OPTIONAL. With one arm the panels are single-wide and the receipt
# records `is_ab_comparison: false`; the output is then a baseline visual, not
# an A/B.
#
# DETERMINISM
# -----------
# Frames are quantized with one median-cut palette and no dithering, and GIF
# carries no timestamp, so the same inputs produce byte-identical output. The
# receipt records the SHA-256 of every GIF for exactly that check.
#
# OUTPUT
#   <out>/ab_sidebyside.gif  ab_fg_highlight.gif  bg_only.gif  fg_only.gif
#   <out>/fg_glyph_change_highlight.gif   (always written)
#   <out>/fl4359_cell_dump_gifs_receipt.json   schema fl4359_cell_dump_gifs.v1
# Exit codes: 0 = every requested family written and non-empty; 1 = an input was
# missing or a family could not be built.
#
# USAGE
#   python3 scripts/fl4359_cell_dump_gifs.py \
#       --arm A:.run/fl4359_cell_dump/arm_a --arm B:.run/fl4359_cell_dump/arm_b \
#       --out .run/fl4359_cell_dump/gifs --scale 0.25
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

RECEIPT_SCHEMA = "fl4359_cell_dump_gifs.v1"
# `fg_glyph_change_highlight` is the operator's original ask — "identify the
# cell grid and highlight the FG glyph change across the frames" — so it is a
# MANDATORY family: `--family` can add to it but can never drop it. The other
# four are the lane-separation families described above.
MANDATORY_FAMILIES = ("fg_glyph_change_highlight",)
FAMILIES = ("ab_sidebyside", "ab_fg_highlight", "bg_only", "fg_only",
            "fg_glyph_change_highlight")
# The two families whose whole point is a PAIR of arms. With one arm they still
# render two panels, the second being an explicit ABSENT placeholder — never a
# copy of arm A, which would look like an A/B that agreed.
AB_FAMILIES = ("ab_sidebyside", "ab_fg_highlight")
ABSENT_PANEL_RGB = (28, 28, 28)
# Mask highlight tint. Magenta is already the FL-4359 diagnostic colour for
# "this cell's owner is under question" (see fl4359_backstop_oscillation_analyzer)
# and it does not occur in the terrain palette, so a tinted mask cannot be
# mistaken for published content.
HIGHLIGHT_RGB = np.array([255.0, 0.0, 255.0], dtype=np.float32)
# Must equal fl4359_cell_dump_raster.UNPAINTED_SENTINEL_RGB: both files paint
# the same "the capture published nothing here" cell and they must agree.
UNPAINTED_SENTINEL_RGB = (255, 0, 255)
HIGHLIGHT_MIX = 0.55
# The glyph-change family uses a SECOND colour so the two highlight families can
# never be confused when they sit next to each other in a review grid: magenta
# means "this is the elected ink", amber means "this cell elected a DIFFERENT
# glyph than it did in the previous frame".
GLYPH_CHANGE_RGB = np.array([255.0, 176.0, 0.0], dtype=np.float32)
GLYPH_CHANGE_MIX = 0.80
# Cells that held their glyph are dimmed rather than left untouched, so the
# changed set reads as a figure against a ground instead of as scattered noise.
GLYPH_HOLD_DIM = 0.35
DEFAULT_FRAME_MS = 400


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Arm:
    """One measured arm: its receipt, its packed cell dump, its source frames."""

    def __init__(self, label: str, dump_dir: Path, method: str):
        self.label = label
        self.dir = dump_dir
        receipt_path = dump_dir / "fl4359_cell_dump_receipt.json"
        if not receipt_path.exists():
            raise SystemExit(f"ABORT: {receipt_path} missing; run the extractor first")
        self.receipt = json.loads(receipt_path.read_text())
        # PREFERRED FIRST. cell_dump_product.npz is written by
        # fl4359_cell_dump_raster.py from the Product cell dump, where the glyph
        # identity is the Product's own election and the frames were rasterized
        # from that same record. cell_dump_<method>.npz is the pixel-threshold
        # fallback written by the extractor when only screenshots exist.
        product_path = dump_dir / "cell_dump_product.npz"
        fallback_path = dump_dir / f"cell_dump_{method}.npz"
        npz_path = product_path if product_path.exists() else fallback_path
        if not npz_path.exists():
            raise SystemExit(
                f"ABORT: neither {product_path} nor {fallback_path} exists. Rasterize "
                f"the cell dump with fl4359_cell_dump_raster.py, or run the extractor "
                f"with --mask-method {method} and without --no-save-npz")
        self.npz_path = npz_path
        self.measurement_path = self.receipt.get("measurement_path", "unknown")
        z = np.load(npz_path)
        shape = tuple(int(v) for v in z["mask_shape"])
        self.masks = np.unpackbits(
            z["mask_packed"], axis=-1, count=shape[-1]).astype(bool).reshape(shape)
        self.fg = z["fg"]
        self.bg = z["bg"]
        # WHICH CELLS THE CAPTURE ACTUALLY PUBLISHED. The `bg_only` / `fg_only`
        # lanes repaint every cell box, so without this they would fill an
        # unsampled cell with `nan_to_num(NaN)` = black and a 6%-coverage frame
        # would read as 94% black paper. A dump written before this lane existed
        # has no array; every cell is then treated as published, which is the
        # correct reading for the full-grid dumps that predate it.
        self.published = (z["published"].astype(bool) if "published" in z
                          else np.ones(self.fg.shape[:3], dtype=bool))
        # Refuse a dump written before the glyph-change lane existed.
        # Re-deriving the election here would create a SECOND owner of "which
        # glyph did this cell elect", which is exactly the defect class this
        # lane exists to measure.
        if "glyph_changed" not in z:
            raise SystemExit(
                f"ABORT: {npz_path} carries no `glyph_changed` array. It was written "
                "by an extractor without the glyph-change-delta lane; re-run "
                "fl4359_cell_dump_extractor.py to produce it. This builder never "
                "re-elects glyphs itself.")
        # (frames - 1, rows, cols): entry i is the change from frame i to i+1.
        self.glyph_changed = z["glyph_changed"].astype(bool)
        self.grid = self.receipt["grid"]
        # F2 COVERAGE, BURNED INTO THE PICTURE. A stride-sampled capture
        # publishes a small fraction of the grid, and a viewer who meets the GIF
        # without the receipt has no way to know that. The percentage therefore
        # travels in the panel label, not only in the JSON beside it. `None`
        # means the dump predates the coverage lane; the label then says so
        # rather than implying full coverage.
        self.coverage = self.receipt.get("coverage")
        self.coverage_pct = (self.coverage or {}).get("coverage_pct")
        self.frames = self._load_source_frames()

    @property
    def coverage_label(self) -> str:
        if self.coverage_pct is None:
            return "coverage unstated"
        return f"coverage {self.coverage_pct:g}%"

    def _load_source_frames(self) -> list[np.ndarray]:
        out = []
        for spec in self.receipt["capture"]["frame_paths"]:
            if "#" in spec:
                path, _, index = spec.rpartition("#")
                gif = Image.open(path)
                gif.seek(int(index))
                out.append(np.asarray(gif.convert("RGB")).astype(np.uint8))
            else:
                out.append(np.asarray(Image.open(spec).convert("RGB")).astype(np.uint8))
        return out

    @property
    def n_frames(self) -> int:
        return len(self.frames)


def cell_boxes(grid: dict) -> tuple[np.ndarray, np.ndarray, int, int]:
    xs = np.round(grid["phase_x"] + np.arange(grid["cols"]) * grid["pitch_x"]).astype(int)
    ys = np.round(grid["phase_y"] + np.arange(grid["rows"]) * grid["pitch_y"]).astype(int)
    bw, bh = grid["sample_box_px"]
    return xs, ys, bw, bh


def paint(arm: Arm, frame: int, mode: str) -> np.ndarray:
    """Rebuild one frame in the requested lane.

    `raw` returns the capture untouched. Every other mode paints ONLY the cell
    boxes the measurement actually covers; pixels outside the lattice keep the
    capture, so nothing is invented where nothing was measured.
    """
    img = arm.frames[frame].astype(np.float32).copy()
    if mode == "raw":
        return img.astype(np.uint8)
    xs, ys, bw, bh = cell_boxes(arm.grid)
    if mode in ("bg_only", "fg_only"):
        # Start from the unpainted sentinel, not black. Black is a publishable
        # BG; the sentinel is not, so a cell the capture never sampled stays
        # distinguishable from one it sampled as dark.
        img[:] = np.array(UNPAINTED_SENTINEL_RGB, dtype=np.float32)
    # Frame 0 has no predecessor, so nothing changed relative to it. An
    # all-false plane is used rather than skipping the frame, because the family
    # must stay frame-aligned with the other four.
    changed_plane = (arm.glyph_changed[frame - 1] if mode == "glyph_change" and frame > 0
                     else None)
    for r, y in enumerate(ys):
        for c, x in enumerate(xs):
            # Nothing was captured here, so bg_only/fg_only must leave the
            # sentinel in place rather than write `nan_to_num(NaN)` = black.
            # `glyph_change` still dims the cell, so the unsampled ground stays
            # a dim field behind the highlighted figure.
            published = arm.published[frame, r, c]
            if not published and mode in ("bg_only", "fg_only", "highlight"):
                continue
            mask = arm.masks[frame, r, c]
            fg = arm.fg[frame, r, c]
            bg = arm.bg[frame, r, c]
            if mode == "glyph_change":
                box = img[y:y + bh, x:x + bw]
                if changed_plane is None or not changed_plane[r, c]:
                    # Held its glyph: dim the whole cell so the changed set is
                    # the only thing at full contrast.
                    box *= GLYPH_HOLD_DIM
                    continue
                # Changed: tint the SYMMETRIC DIFFERENCE of the two elected
                # bitmaps, which is exactly the ink that appeared or vanished,
                # and leave the shared ink alone so the shape stays readable.
                delta = mask ^ arm.masks[frame - 1, r, c]
                box *= GLYPH_HOLD_DIM
                box[delta] = (box[delta] * (1.0 - GLYPH_CHANGE_MIX)
                              + GLYPH_CHANGE_RGB * GLYPH_CHANGE_MIX)
            elif mode == "highlight":
                box = img[y:y + bh, x:x + bw]
                box[mask] = box[mask] * (1.0 - HIGHLIGHT_MIX) + HIGHLIGHT_RGB * HIGHLIGHT_MIX
            elif mode == "bg_only":
                # Paper only: the whole cell takes the BG colour the renderer
                # published, so every glyph shape is erased by construction.
                img[y:y + bh, x:x + bw] = np.nan_to_num(bg)
            elif mode == "fg_only":
                # Ink only: FG colour inside the elected mask, black outside.
                img[y:y + bh, x:x + bw][mask] = np.nan_to_num(fg)
    return np.clip(img, 0, 255).astype(np.uint8)


def absent_panel(shape: tuple[int, ...]) -> np.ndarray:
    """A flat placeholder for an arm that was never captured."""
    panel = np.zeros(shape, dtype=np.uint8)
    panel[:] = np.array(ABSENT_PANEL_RGB, dtype=np.uint8)
    return panel


def compose(panels: list[np.ndarray], labels: list[str], scale: float,
            crop: tuple[int, int, int, int] | None, grid: dict) -> Image.Image:
    if crop is not None:
        r0, c0, nr, nc = crop
        xs, ys, bw, bh = cell_boxes(grid)
        x0, y0 = int(xs[c0]), int(ys[r0])
        x1 = int(xs[min(c0 + nc, len(xs)) - 1]) + bw
        y1 = int(ys[min(r0 + nr, len(ys)) - 1]) + bh
        panels = [p[y0:y1, x0:x1] for p in panels]
    images = [Image.fromarray(p) for p in panels]
    if scale != 1.0:
        images = [im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))),
                            Image.LANCZOS) for im in images]
    gap = 8
    width = sum(im.width for im in images) + gap * (len(images) - 1)
    height = max(im.height for im in images)
    canvas = Image.new("RGB", (width, height + 18), (16, 16, 16))
    x = 0
    draw = ImageDraw.Draw(canvas)
    for im, label in zip(images, labels):
        canvas.paste(im, (x, 18))
        draw.text((x + 4, 4), label, fill=(235, 235, 235))
        x += im.width + gap
    return canvas


def write_gif(frames: list[Image.Image], path: Path, frame_ms: int) -> None:
    # One median-cut palette, dithering off: identical inputs -> identical bytes.
    quantized = [f.quantize(colors=256, method=Image.MEDIANCUT, dither=Image.NONE)
                 for f in frames]
    quantized[0].save(path, save_all=True, append_images=quantized[1:],
                      duration=frame_ms, loop=0, optimize=False, disposal=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FL-4359 cell dump GIF families")
    ap.add_argument("--arm", action="append", required=True,
                    help="LABEL:DUMP_DIR (repeatable; the first is arm A)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mask-method", default="otsu")
    ap.add_argument("--scale", type=float, default=0.25)
    ap.add_argument("--crop", default=None,
                    help="ROW,COL,NROWS,NCOLS cell window to zoom into")
    ap.add_argument("--frame-ms", type=int, default=DEFAULT_FRAME_MS)
    ap.add_argument("--family", action="append", choices=FAMILIES, default=None)
    args = ap.parse_args(argv)

    # The mandatory family is appended, never selected away: a run that omitted
    # it would silently drop the operator's original ask.
    families = list(args.family or FAMILIES)
    for family in MANDATORY_FAMILIES:
        if family not in families:
            families.append(family)
    crop = tuple(int(v) for v in args.crop.split(",")) if args.crop else None

    arms: list[Arm] = []
    for spec in args.arm:
        label, _, path = spec.partition(":")
        if not path:
            print(f"ABORT: --arm expects LABEL:DUMP_DIR, got {spec!r}", file=sys.stderr)
            return 1
        arms.append(Arm(label, Path(path), args.mask_method))

    n_frames = min(a.n_frames for a in arms)
    if n_frames < 1:
        print("ABORT: no frames to render", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)

    mode_for = {"ab_sidebyside": "raw", "ab_fg_highlight": "highlight",
                "bg_only": "bg_only", "fg_only": "fg_only",
                "fg_glyph_change_highlight": "glyph_change"}
    written = []
    for family in families:
        mode = mode_for[family]
        frames = []
        for f in range(n_frames):
            panels = [paint(a, f, mode) for a in arms]
            labels = [f"{a.label}  frame {f}  [{family}]  {a.coverage_label}"
                      for a in arms]
            if family in AB_FAMILIES and len(arms) == 1:
                # ARM B IS ABSENT, NOT EQUAL TO ARM A. Duplicating arm A into
                # the second panel would read as an A/B whose two arms agree,
                # which is a claim this run cannot make. The placeholder is
                # blank and says so.
                panels.append(absent_panel(panels[0].shape))
                labels.append("B  ABSENT - single-arm capture, not an A/B")
            frames.append(compose(panels, labels, args.scale, crop, arms[0].grid))
        path = args.out / f"{family}.gif"
        write_gif(frames, path, args.frame_ms)
        row = {
            "family": family, "path": str(path), "bytes": path.stat().st_size,
            "frames": n_frames, "panel_px": list(frames[0].size),
            "mandatory": family in MANDATORY_FAMILIES,
            "sha256": sha256(path),
        }
        if mode == "glyph_change":
            # The picture and the number come from ONE array, so a reader can
            # check the highlight against the count without trusting the image.
            row["highlighted_cells_per_frame_pair"] = {
                a.label: [int(a.glyph_changed[i].sum())
                          for i in range(min(n_frames - 1, len(a.glyph_changed)))]
                for a in arms
            }
            row["highlight_rule"] = (
                "amber = the symmetric difference of the elected ink bitmaps in a "
                "cell whose elected glyph id changed from the previous frame; every "
                "other cell is dimmed. Frame 0 has no predecessor and highlights "
                "nothing")
        written.append(row)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "arms": [{"label": a.label, "dump_dir": str(a.dir),
                  "arm_label_in_dump": a.receipt.get("arm_label"),
                  "grid": a.grid,
                  # Which evidence the picture was drawn from. `product_glyph_id`
                  # means the frames came from the Product cell dump;
                  # `pixel_threshold_fallback` means they came from screenshots.
                  "measurement_path": a.measurement_path,
                  "image_source": a.receipt.get("image_source", "screenshot"),
                  "npz": str(a.npz_path),
                  # Carried through verbatim from the raster receipt so the GIF
                  # receipt states the sparsity its pictures were drawn under.
                  "coverage": a.coverage,
                  "limits": a.receipt.get("limits"),
                  "pose_identity_closed": a.receipt["pose_identity"]["pose_identity_closed"]}
                 for a in arms],
        "rendered_from_product_cell_dump": all(
            a.receipt.get("image_source") == "product_cell_dump" for a in arms),
        "actual_field": {
            "schema": "fl4359.final_paint_field.v1",
            "actual_field_observation": False,
            "presentation_mode_eligible": False,
            "evidence_kind": "legacy_cell_pair_diagnostic",
            "reason": (
                "cell-dump GIFs consume per-cell pairs and cannot qualify "
                "continuous native FG/BG evidence; use the jitter capture "
                "consumer when a final_paint_field sidecar is present"),
        },
        "is_ab_comparison": len(arms) >= 2,
        "single_arm_note": (None if len(arms) >= 2 else
                            "one arm supplied: these are baseline visuals, not an A/B. "
                            "The A/B families render a blank ABSENT panel in place of "
                            "arm B rather than duplicating arm A"),
        "coverage_labelled_in_panels": True,
        "mask_method": args.mask_method,
        "scale": args.scale,
        "crop_cells": list(crop) if crop else None,
        "frame_ms": args.frame_ms,
        "gifs": written,
    }
    out_path = args.out / "fl4359_cell_dump_gifs_receipt.json"
    out_path.write_text(json.dumps(receipt, indent=1) + "\n")
    print(json.dumps(receipt, indent=1))
    if any(g["bytes"] == 0 for g in written):
        print("FAIL: a GIF family produced an empty file", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
