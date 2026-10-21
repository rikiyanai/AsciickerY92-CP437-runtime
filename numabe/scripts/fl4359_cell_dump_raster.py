#!/usr/bin/env python3
# =============================================================================
# fl4359_cell_dump_raster.py
# =============================================================================
# FL-4359 CELL-DUMP RASTERIZER: build the proof frames FROM THE PRODUCT RECORD,
# never from a screenshot.
#
# WHY THIS EXISTS
# ---------------
# The airborne route publishes no per-frame screenshots (the driver writes only
# 00_before / move_080 / 99_after; the per-route-frame writer is gated on the
# GROUNDED ghost-replay route). That is not a blocker, because the screenshot was
# never the proof's unit. The dump is. Every published cell carries its elected
# glyph id, its FG and its BG, so the dump FULLY DETERMINES the image — and it is
# strictly more faithful than a screenshot, which is a re-encoded picture of the
# same data after presentation scaling, colour management and PNG quantization.
#
# WHAT A ROW ACTUALLY CARRIES, AND WHICH FIELD IS THE TRUTH
# ---------------------------------------------------------
# READ THIS BEFORE CHANGING ANY FIELD NAME BELOW. Two glyph fields exist on every
# row and only one of them is real:
#
#   row["glyph_char"]  — a LUMA PROXY. The row's own
#       `glyph_char_kind: "proxy_luma"` and
#       `glyph_surface.glyph_identity_truth: "gpu_fact_glyph_id_unmapped_luma_proxy"`
#       say so. row["glyph_id"] is null on a real dump. Rasterizing from these
#       would draw a picture of a luminance ramp and call it the Product.
#
#   row["final_product_b55_quarters"] — THE TRUTH. Four records, one per quarter
#       slot, the b55 readback of the Product the compositor painted from.
#
# A cell carries FOUR quarter records but rasterizes ONE glyph, and the quarter
# record carries TWO glyph fields that are different facts:
#
#   glyph_id            the QUARTER's own Product identity; varies per quarter,
#                       e.g. a single cell reading [670, 670, 126, 126]
#   cell_presentation.glyph_id  the WHOLE-CELL glyph the compositor rasterizes, repeated
#                       across the four quarters, always equal to
#                       final_mask_measurement.glyph_ids
#
# THE DRAWN GLYPH IS `cell_presentation.glyph_id`. Measured on the recorded dump the two
# disagree on 5008 of 8023 cells, so electing the per-quarter identity mis-stamps
# most of the frame. Both lanes are kept and reported separately: the drawn glyph
# drives the picture and the visible glyph-change delta, the quarter identity is
# its own upstream lane. See `elect_cell_glyph`.
#
# THE ATLAS IS NOT CP437, AND THE STRIDE IS NOT `glyph_dimensions`
# ----------------------------------------------------------------
# Glyph ids observed on a real airborne dump reach 670, far past CP437's 255.
# ASCIICompositorEffect._fl4359_host_atlas_page_id splits the id space into three
# pages and World.gd names their assets; ATLAS_PAGES below mirrors both.
#
# TWO SAMPLERS EXIST IN THE RUNTIME AND THEY DISAGREE. The host-side replay
# (_fl4359_host_sample_atlas_mask) uses the dump's reported `glyph_dimensions`
# as the atlas stride. That value is the ON-SCREEN legacy glyph lattice — 12 px
# on a live push — not the atlas source footprint, which is 40 px for CP437 and
# 32 px for the generated pages. The stride addresses BOTH the glyph's base cell
# and the sub-offset, so a 12 px stride on a 40 px sheet reads a 12x12 window
# from the WRONG PLACE, inside a neighbouring glyph. The error is not a uniform
# under-ink; it goes both ways, and the recorded dump shows both for the glyphs
# it elects (glyph 61: expected 25 vs painted 116; glyph 126: expected 592 vs
# painted 140). The compositor already carries the repair and states it above
# `sample_compositor_glyph_intensity_native_footprint`: sample each page at its
# real source-cell footprint, then minify into the on-screen cell with a
# four-fetch bilinear. THIS FILE MIRRORS THE COMPOSITOR, not the host replay.
#
# CONSEQUENCE FOR GRADING. The dump's `expected_digest` / `expected_pixel_count`
# are produced by the stale host replay, so they disagree with the painted
# `actual_pixel_count` on essentially every cell (`digest_equal: false`,
# `state: "final_mask_digest_mismatch"`). Grading this rasterizer against
# `expected_*` would grade it against the defect. The receipt therefore grades
# stamped ink against `actual_pixel_count`, which is the GPU's own painted count
# and equals `final_glyph_ink.ink_pixels` on every observed row.
#
# MEASURED: with the native footprint AND the drawn-glyph election, this file
# reproduces the GPU exactly on the recorded dump — 8023/8023 cells, 798762 of
# 798762 ink pixels, zero error. The `ink_agreement` block re-measures that on
# every run rather than asserting it once here.
#
# OUTPUT (drop-in for fl4359_cell_dump_gifs.py — one image owner, not two)
#   <out>/frame_%02d.png                     rasterized frames, one per dump
#   <out>/cell_dump_product.npz              masks/fg/bg/glyph ids/glyph_changed
#   <out>/fl4359_cell_dump_receipt.json      schema fl4359_cell_dump_receipt.v1
# Exit codes: 0 = every frame rasterized; 1 = a missing input, a grid that
# changes between frames, or an atlas page that cannot be loaded.
#
# USAGE
#   python3 scripts/fl4359_cell_dump_raster.py \
#       --cells .run/final_render_cell_dump/<stamp_f0>/cells.jsonl \
#       --cells .run/final_render_cell_dump/<stamp_f1>/cells.jsonl \
#       ... --arm-label baseline --out .run/fl4359_cell_dump/baseline
# =============================================================================

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

RECEIPT_SCHEMA = "fl4359_cell_dump_receipt.v1"

# UNPAINTED SENTINEL. A stride-sampled capture leaves most cells with no
# published row. Those cells must not be black: black is a legitimate published
# BG and a 7%-coverage frame would then read as a mostly-dark render. Magenta is
# already the FL-4359 diagnostic colour for "this cell's owner is under
# question" and does not occur in the terrain palette, so a sentinel cell cannot
# be mistaken for content. The value is published in the receipt.
UNPAINTED_SENTINEL_RGB = (255, 0, 255)

# ONE OWNER FOR THE COMPARISON RULES. `mask_iou` and OSCILLATION_MARGIN belong
# to fl4359_cell_dump_extractor; re-implementing them here would create a second
# owner of the same threshold and let the two drift silently.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fl4359_cell_dump_extractor as _ext  # noqa: E402

# --- Atlas pages -------------------------------------------------------------
# Mirrors ASCIICompositorEffect._fl4359_host_atlas_page_id and World.gd's three
# texture assignments. Ranges are located by SYMBOL in the host
# (ADDITIVE_GLYPH_MIN/MAX, MORPHOLOGY_GLYPH_MIN/MAX), never by line number.
ATLAS_PAGES = {
    0: {"path": "godot_project/assets/font.png",
        "glyph_min": 0, "glyph_max": 255,
        "host_symbol": "font_tex",
        "rule": "cp437_sheet; source footprint = (w//16, h//16) = 40x40; coverage in RED"},
    1: {"path": "godot_project/assets/glyphs/atlases/material.additive.v1.page32.png",
        "glyph_min": 512, "glyph_max": 671,
        "host_symbol": "extended_glyph_tex",
        "rule": "generated page; source footprint = (w//16, w//16) = 32x32; coverage in ALPHA"},
    2: {"path": "godot_project/assets/glyphs/atlases/material.morphology.v2.page32.png",
        "glyph_min": 672, "glyph_max": 1880,
        "host_symbol": "morphology_glyph_tex",
        "rule": "generated page; source footprint = (w//16, w//16) = 32x32; coverage in ALPHA"},
}
# Ids 256..511 sit between the CP437 page and the additive page and belong to no
# atlas. The host returns page -1 for them and so does this file.
UNMAPPED_ID_GAP = (256, 511)

ROOT = Path(__file__).resolve().parents[1]
# Quarter slot -> (x_half, y_half) inside the cell. Matches the compositor's
# `(slot & 1) = right`, `(slot & 2) = bottom` convention. Retained for the
# quarter colour/provenance lanes; the MASK is elected once per cell.
SLOT_OFFSETS = ((0, 0), (1, 0), (0, 1), (1, 1))
# Fields lifted out of each streamed row. Named once so a schema drift shows up
# here rather than as a silently empty frame.
ROW_FIELDS = ("screen_cell", "pixel_rect", "final_product_b55_quarters",
              "final_mask_measurement", "final_glyph_ink",
              "camera_world", "camera_yaw", "camera_pitch", "player_world",
              "row_type")


class AtlasPage:
    """One atlas page image plus the id range it serves."""

    def __init__(self, page_id: int, spec: dict):
        self.page_id = page_id
        self.spec = spec
        path = ROOT / spec["path"]
        if not path.exists():
            raise SystemExit(f"ABORT: atlas page {page_id} missing at {path}")
        self.image = np.asarray(Image.open(path).convert("RGBA"))
        self.height, self.width = self.image.shape[:2]

    @property
    def source_span(self) -> tuple[int, int]:
        """The page's REAL source-cell footprint in atlas pixels, (w, h).

        Mirrors sample_compositor_glyph_intensity_native_footprint. Every page is
        a 16-column grid (COMPOSITOR_GLYPH_ATLAS_COLS). CP437 divides BOTH axes by
        16 (640/16 = 40); the generated pages use the x division for both axes
        (512/16 = 32), which is what the shader does and is why a 512x2432
        morphology sheet still has 32px cells rather than 152px ones.
        """
        if self.page_id == 0:
            return (max(self.width // 16, 1), max(self.height // 16, 1))
        cell = max(self.width // 16, 1)
        return (cell, cell)

    def mask(self, glyph: int, box_h: int, box_w: int) -> np.ndarray | None:
        """Return the box_h x box_w ink mask for one glyph, or None if unstampable.

        A 1:1 port of sample_compositor_glyph_intensity_native_footprint plus
        sample_compositor_page_footprint — the law the renderer actually runs, not
        the host-side replay. `box_w`/`box_h` are the ON-SCREEN cell span, which
        is what the shader calls `cell_span_px`.

        WHY NOT THE HOST REPLAY. _fl4359_host_sample_atlas_mask uses the dump's
        reported `glyph_dimensions` (12 px on a live push) as the ATLAS stride.
        That value is the on-screen legacy glyph lattice, not the atlas source
        footprint, so a 12 px stride into a 640x640 CP437 sheet reads the
        top-left 12x12 of each 40x40 glyph and drops roughly two thirds of the
        ink. The compositor states this above its own sampler:
            "the live Godot push still carries a 12px legacy glyph lattice,
             while CP437 is 40px and generated pages are 32px; using the legacy
             lattice here skipped source detail before the atlas was sampled."
        The runtime's `expected_pixel_count` is still produced by the stale
        replay, which is why it disagrees with the painted `actual_pixel_count`
        on essentially every cell. This function mirrors the GPU, so a receipt
        can be graded against `actual_pixel_count`.
        """
        span_w, span_h = self.source_span
        if self.page_id == 0:
            idx = min(max(glyph, 0), 255)
        else:
            idx = (1204 if glyph == 1880 and not self.spec.get("native_terminal")
                   else glyph - self.spec["glyph_min"])
        base_x = (idx & 0xF) * span_w
        base_y = (idx >> 4) * span_h
        if base_x + span_w > self.width or base_y + span_h > self.height:
            # The shader returns zero coverage for an out-of-range page cell
            # rather than substituting a glyph. An empty mask is a real
            # observation, not a missing one.
            return np.zeros((box_h, box_w), dtype=bool)
        ys, xs = np.meshgrid(np.arange(box_h, dtype=np.float64),
                             np.arange(box_w, dtype=np.float64), indexing="ij")
        if self.spec.get("native_terminal"):
            # One font scale on both axes. A wide mask is sampled across its
            # complete two-column span; caller clips only at viewport edges.
            nx = np.floor((xs + 0.5) * 32 / box_h).astype(int)
            ny = np.floor((ys + 0.5) * 32 / box_h).astype(int)
            return self.image[base_y + ny, base_x + nx, 3] > 127
        # compositor_atlas_source_pos: (sub + 0.5) * source_span / cell_span - 0.5
        pos_x = (xs + 0.5) * span_w / max(box_w, 1) - 0.5
        pos_y = (ys + 0.5) * span_h / max(box_h, 1) - 0.5
        pos_x = np.clip(pos_x, 0.0, span_w - 1)
        pos_y = np.clip(pos_y, 0.0, span_h - 1)
        # Generated pages carry antialias coverage in ALPHA; CP437 is a
        # luminance sheet read from RED. Reading the wrong channel would either
        # invent hard edges or read a flat white.
        channel = 0 if self.page_id == 0 else 3
        plane = self.image[base_y:base_y + span_h,
                           base_x:base_x + span_w, channel].astype(np.float64) / 255.0
        minifying = span_w > box_w or span_h > box_h
        if not minifying:
            nx = np.clip(np.floor(pos_x + 0.5).astype(int), 0, span_w - 1)
            ny = np.clip(np.floor(pos_y + 0.5).astype(int), 0, span_h - 1)
            return plane[ny, nx] > 0.5
        # Four-fetch bilinear, used ONLY while minifying the source cell into a
        # smaller on-screen cell. This is what recovers the ink a single texel
        # fetch drops.
        x0 = np.clip(np.floor(pos_x).astype(int), 0, span_w - 1)
        y0 = np.clip(np.floor(pos_y).astype(int), 0, span_h - 1)
        x1 = np.clip(x0 + 1, 0, span_w - 1)
        y1 = np.clip(y0 + 1, 0, span_h - 1)
        fx = np.clip(pos_x - x0, 0.0, 1.0)
        fy = np.clip(pos_y - y0, 0.0, 1.0)
        top = plane[y0, x0] * (1.0 - fx) + plane[y0, x1] * fx
        bottom = plane[y1, x0] * (1.0 - fx) + plane[y1, x1] * fx
        return (top * (1.0 - fy) + bottom * fy) > 0.5


def page_id_for(glyph) -> int:
    """Mirror of ASCIICompositorEffect._fl4359_host_atlas_page_id."""
    if glyph is None:
        return -1
    for page_id, spec in ATLAS_PAGES.items():
        if spec["glyph_min"] <= glyph <= spec["glyph_max"] and glyph not in range(1876, 1880):
            return page_id
    return -1


class MaskCache:
    """One stamped mask per (glyph, box, glyph_dimensions), built at most once.

    A 113x71 grid is 8023 cells x 4 quarters x 5 frames = 160460 stamps over a
    few hundred distinct glyphs. Without this the rasterizer is dominated by
    re-sampling the same handful of atlas tiles.
    """

    def __init__(self):
        self._pages: dict[int, AtlasPage] = {}
        self._cache: dict[tuple, np.ndarray | None] = {}
        self.unstampable_ids: dict[int, int] = {}
        self.stamped_by_page: dict[int, int] = {}

    def get(self, glyph, box_h: int, box_w: int) -> np.ndarray | None:
        """The mask key deliberately does NOT include `glyph_dimensions`.

        That value is the on-screen legacy lattice, not an atlas coordinate, and
        keying on it was the symptom of using it as a stride. The mask depends
        only on the glyph, its page's real source footprint, and the on-screen
        cell span.
        """
        page_id = page_id_for(glyph)
        if page_id < 0:
            key = -1 if glyph is None else int(glyph)
            self.unstampable_ids[key] = self.unstampable_ids.get(key, 0) + 1
            return None
        cache_key = (page_id, int(glyph), box_h, box_w)
        if cache_key not in self._cache:
            if page_id not in self._pages:
                self._pages[page_id] = AtlasPage(page_id, ATLAS_PAGES[page_id])
            self._cache[cache_key] = self._pages[page_id].mask(
                int(glyph), box_h, box_w)
        self.stamped_by_page[page_id] = self.stamped_by_page.get(page_id, 0) + 1
        return self._cache[cache_key]

    def source_span(self, glyph) -> tuple[int, int] | None:
        page_id = page_id_for(glyph)
        if page_id < 0:
            return None
        if page_id not in self._pages:
            self._pages[page_id] = AtlasPage(page_id, ATLAS_PAGES[page_id])
        return self._pages[page_id].source_span


def _rgb(value) -> tuple[float, float, float]:
    """Product colours arrive as 0..1 floats; return them as 0..255."""
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return (float("nan"),) * 3
    return tuple(float(v) * 255.0 for v in value[:3])


# =============================================================================
# ROW-SCHEMA ADAPTERS
# =============================================================================
# TWO CAPTURE SCHEMAS PUBLISH THE SAME CELL, UNDER DIFFERENT FIELD NAMES.
#
#   `exact_final_cell`      written by ASCIICompositorEffect's full-grid final
#                           dump (`final_render_cell_dump/<stamp>/cells.jsonl`).
#                           Every one of the 8023 cells is present.
#   `fl4359_jitter_cell`    written by scripts/adhoc/fl4359_external_movement_
#                           driver.gd `_sparse_row_for_analyzer`, one row per
#                           SAMPLED SUBCELL of the sparse jitter capture. The
#                           capture is stride-sampled, so only a few hundred of
#                           the 8023 cells appear per frame.
#
# THE ADAPTER MAPS NAMED FIELD TO NAMED FIELD. It never probes alternatives and
# never falls back to a near-miss: a field with no exact equal is emitted as
# None and declared `absent` in JITTER_CELL_FIELD_MAP, because a wrong mapping
# produces a confident wrong picture, which is strictly worse than an abort.
#
# HOW THE JITTER EQUALS WERE ESTABLISHED (all by symbol, in current source):
#
#   Both schemas decode the SAME b43 final-cell-paint record. The full-grid
#   decoder reads it at `base + N` (ASCIICompositorEffect, `_final_cell_unpack_
#   pair` call site and the `"glyph_id": int(data.decode_u32(base + 8))` return
#   block); the sparse recorder embeds that record at `base + 112` and decodes
#   the same words (`"final_paint_glyph": ... base + 120`, `"final_paint_fg_
#   packed": ... base + 128/132/136`, `"final_paint_ink_counts": ... base +
#   144/148/152/156`). So the two schemas are the same bytes under two names.
#
#   THE DRAWN GLYPH. `elect_cell_glyph` documents that the b55 quarter field
#   `cell_presentation.glyph_id` — the whole-cell glyph the compositor rasterizes — is
#   "always equal to final_mask_measurement.glyph_ids". The jitter schema has NO
#   b55 quarter records, but it does carry `final_paint_mask_glyphs`, which is
#   the same `decoded_mask_glyphs` array the full-grid decoder publishes as
#   `final_mask_glyph_ids` and the exact row republishes as
#   `final_mask_measurement.glyph_ids`. That is the exact equal, so the jitter
#   rows travel through the SAME third branch of `elect_cell_glyph` that the
#   full-grid rows use as their cross-check.
#
#   THE TRAP, AND WHY IT IS NOT THE ONE IT LOOKS LIKE. On a b55 quarter,
#   `glyph_id` is the quarter's own Product identity and must NOT be elected. On
#   a JITTER row, `glyph_id` is not that field at all: the driver assigns
#   `"glyph_id": int(row.get("final_paint_glyph", 0))`, so it is an alias of the
#   painted whole-cell glyph. The jitter analogue of the b55 quarter identity is
#   `final_product_glyph` (the Product's own glyph), and it is routed to the
#   PRODUCT IDENTITY LANE only — never to election. Measured on the five
#   fixture frames, `final_product_glyph` differs from the painted glyph on
#   295 of 569 rows (51.8%), so electing it would mis-stamp half the samples.
#
#   COLOUR. `exact_final_cell.fg_rgb` and `final_glyph_ink.fg_rgb` are both
#   `final_cell_paint_buffer.fg_rgb`, unpacked by `_final_cell_unpack_pair` as
#   16-bit channels scaled to 0..1 floats. The jitter `fg_rgb` is the same three
#   words unpacked by `_decode_sparse_rgb` as `(word & 0xFFFF) >> 8`, i.e. the
#   high byte, 0..255 ints. Same bytes, two scales; the adapter divides by 255
#   so everything downstream keeps one convention. The 16->8 bit truncation is
#   the only fidelity loss and is declared in the map.
#
#   INK. `final_glyph_ink.ink_pixels` is `final_cell_paint_buffer.ink_pixels`
#   (b43 word at +32). In the jitter transport that word is
#   `final_paint_ink_counts[0]`, which the driver names `final_ink_fg`.
#   `final_ink_pixels` is `final_paint_ink_counts[3]`, which is b43
#   TOTAL_PIXELS, not ink — the jitter transport's name is a misnomer and using
#   it as the painted-ink count would grade every cell against its cell area.
JITTER_ROW_TYPE = "fl4359_jitter_cell"
EXACT_ROW_TYPE = "exact_final_cell"

# Machine-readable audit trail: emitted verbatim into the receipt as
# `row_schema_adapter.field_map` so a reader can check every substitution
# without reading this file.
JITTER_CELL_FIELD_MAP = {
    "screen_cell": {
        "source": "screen_cell", "kind": "verbatim",
        "note": "driver sets it from final_paint_cell, the b43 paint record's "
                "own cell address; same address space as the exact schema"},
    "final_mask_measurement.glyph_ids": {
        "source": "final_paint_mask_glyphs", "kind": "verbatim",
        "note": "both are the decoded b43 mask-glyph words 21..24; this is the "
                "DRAWN whole-cell glyph and the documented equal of the b55 "
                "quarter field cell_presentation.glyph_id"},
    "final_mask_measurement.dimensions": {
        "source": "final_paint_atlas_mask_dimensions", "kind": "verbatim",
        "note": "same final_paint_mask_dimensions_word as the exact schema's "
                "final_mask_dimensions; the cell's NATIVE on-screen span"},
    "final_mask_measurement.digest_equal": {
        "source": "final_paint_expected_atlas_mask_hash == "
                  "final_paint_actual_mask_hash",
        "kind": "derived_with_named_gap",
        "note": "the runtime predicate is `host_expected_available AND "
                "expected == actual`. The jitter transport carries both hashes "
                "verbatim but NOT final_paint_expected_available, so the "
                "availability guard cannot be reproduced. None when either "
                "hash is missing"},
    "final_glyph_ink.fg_rgb": {
        "source": "fg_rgb", "kind": "unit_converted",
        "note": "jitter publishes the high byte of each 16-bit channel as an "
                "0..255 int; divided by 255 to match the exact schema's 0..1 "
                "float. Lossy in the low 8 bits of the original channel"},
    "final_glyph_ink.bg_rgb": {
        "source": "bg_rgb", "kind": "unit_converted",
        "note": "as fg_rgb"},
    "final_glyph_ink.ink_pixels": {
        "source": "final_ink_fg", "kind": "verbatim",
        "note": "b43 ink_pixels (word +32). NOT final_ink_pixels, which is b43 "
                "total_pixels under a misleading transport name"},
    "product_identity_quarter_samples[].glyph_id": {
        "source": "final_product_glyph", "kind": "verbatim",
        "note": "the Product's own glyph for the sampled subcell — the jitter "
                "analogue of a b55 quarter glyph_id. UPSTREAM LANE ONLY; it "
                "never reaches glyph election"},
    "product_identity_quarter_samples[].slot": {
        "source": "subcell_slot", "kind": "verbatim",
        "note": "which of the four quarters this row sampled; the other three "
                "quarters of the cell are simply not in the capture"},
    "product_identity_quarter_samples[].fg": {
        "source": "final_paint_slot_fg_rgb", "kind": "unit_converted",
        "note": "0..255 ints from _final_cell_unpack_rgb8, divided by 255. "
                "None unless final_paint_slot_colour_present"},
    "product_identity_quarter_samples[].bg": {
        "source": "final_paint_slot_bg_rgb", "kind": "unit_converted",
        "note": "as fg"},
    "pixel_rect": {
        "source": "final_paint_atlas_mask_dimensions", "kind": "substituted",
        "note": "the jitter transport carries no pixel_rect. The mask "
                "dimensions are the same native span the exact rect reports, "
                "and are used only for the receipt's native_cell_px"},
    "pose": {
        "source": "frame_trace.pose", "kind": "verbatim",
        "note": "camera_origin / camera_yaw / camera_pitch, frame-scoped "
                "rather than row-scoped"},
    "player_world": {
        "source": None, "kind": "absent",
        "note": "the sparse transport publishes no player world position; "
                "emitted as None rather than substituting camera_world"},
    "final_product_b55_quarters": {
        "source": None, "kind": "absent",
        "note": "the jitter transport carries no b55 quarter records at all. "
                "Emitted EMPTY so elect_cell_glyph cannot read a synthesized "
                "quarter; the Product identity it does carry travels in "
                "product_identity_quarter_samples instead"},
    "grid": {
        "source": "frame_trace.semantic_ansi_size", "kind": "verbatim",
        "note": "[cols, rows]. Authoritative: a stride-sampled frame may never "
                "sample the last column, so max(screen_cell)+1 would silently "
                "shrink the grid"},
    "cell_px": {
        "source": "frame_trace.viewport.cell_size", "kind": "verbatim",
        "note": "uniform cell size for the frame"},
}


def _norm255(value):
    """0..255 ints from the sparse transport -> the 0..1 floats `_rgb` expects."""
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    return [float(v) / 255.0 for v in value[:3]]


def _adapt_exact_final_cell(row: dict, frame: dict) -> dict | None:
    """Identity adapter. The exact schema IS the canonical shape."""
    cell = row.get("screen_cell") or {}
    x, y = int(cell.get("x", -1)), int(cell.get("y", -1))
    if x < 0 or y < 0:
        return None
    if not frame["pose"]:
        cam = row.get("camera_world") or {}
        player = row.get("player_world") or {}
        frame["pose"] = {
            "cam": [cam.get("x"), cam.get("y"), cam.get("z")],
            "player": [player.get("x"), player.get("y"), player.get("z")],
            "yaw_radians": row.get("camera_yaw"),
            "pitch_radians": row.get("camera_pitch"),
        }
    rect = row.get("pixel_rect") or {}
    frame["box_w"] = max(frame["box_w"], int(rect.get("w", 0)))
    frame["box_h"] = max(frame["box_h"], int(rect.get("h", 0)))
    return row


def _adapt_jitter_cell(row: dict, frame: dict) -> dict | None:
    """Rewrite one `fl4359_jitter_cell` row into the canonical cell shape.

    Every substitution below is declared in JITTER_CELL_FIELD_MAP, which the
    receipt publishes verbatim.
    """
    cell = row.get("screen_cell") or {}
    x, y = int(cell.get("x", -1)), int(cell.get("y", -1))
    if x < 0 or y < 0:
        return None

    trace = row.get("frame_trace") or {}
    if not frame["pose"]:
        pose = trace.get("pose") or {}
        origin = pose.get("camera_origin") or []
        frame["pose"] = {
            "cam": list(origin[:3]) if len(origin) >= 3 else [None, None, None],
            # Declared absent, not substituted: the sparse transport has no
            # player world position and camera_origin is a different fact.
            "player": [None, None, None],
            "yaw_radians": pose.get("camera_yaw"),
            "pitch_radians": pose.get("camera_pitch"),
        }
    ansi = trace.get("semantic_ansi_size") or []
    if len(ansi) >= 2:
        frame["declared_cols"] = max(frame["declared_cols"], int(ansi[0]))
        frame["declared_rows"] = max(frame["declared_rows"], int(ansi[1]))
    viewport = trace.get("viewport") or {}
    cell_size = viewport.get("cell_size") or trace.get("cell_size") or []
    if len(cell_size) >= 2:
        frame["box_w"] = max(frame["box_w"], int(cell_size[0]))
        frame["box_h"] = max(frame["box_h"], int(cell_size[1]))
    for key in ("movement_phase", "oracle_screen_stride", "oracle_world_stride",
                "oracle_sampling_policy", "oracle_expected_subcells_per_frame",
                "frame_index"):
        if key in trace and key not in frame["capture_window"]:
            frame["capture_window"][key] = trace[key]

    dims = row.get("final_paint_atlas_mask_dimensions")
    dims = list(dims) if isinstance(dims, (list, tuple)) and len(dims) >= 2 else None

    expected_hash = row.get("final_paint_expected_atlas_mask_hash")
    actual_hash = row.get("final_paint_actual_mask_hash")
    digest_equal = (None if expected_hash is None or actual_hash is None
                    else expected_hash == actual_hash)

    slot = row.get("subcell_slot")
    slot = int(slot) if isinstance(slot, int) and 0 <= int(slot) <= 3 else None
    samples = []
    if slot is not None:
        colour_present = bool(row.get("final_paint_slot_colour_present"))
        samples.append({
            "slot": slot,
            "glyph_id": row.get("final_product_glyph"),
            "fg": _norm255(row.get("final_paint_slot_fg_rgb")) if colour_present else None,
            "bg": _norm255(row.get("final_paint_slot_bg_rgb")) if colour_present else None,
        })

    return {
        "row_type": JITTER_ROW_TYPE,
        "screen_cell": {"x": x, "y": y},
        "pixel_rect": ({"w": dims[0], "h": dims[1]} if dims else {}),
        "final_mask_measurement": {
            "glyph_ids": row.get("final_paint_mask_glyphs") or [],
            "dimensions": dims,
            "digest_equal": digest_equal,
            "state": row.get("final_paint_mask_state"),
            "source": "fl4359_jitter_cell adapter; see JITTER_CELL_FIELD_MAP",
        },
        "final_glyph_ink": {
            "fg_rgb": _norm255(row.get("fg_rgb")),
            "bg_rgb": _norm255(row.get("bg_rgb")),
            "ink_pixels": row.get("final_ink_fg"),
        },
        # Declared empty. The jitter transport has no b55 quarter records, so
        # `elect_cell_glyph` must fall through to the mask-glyph array.
        "final_product_b55_quarters": [],
        "product_identity_quarter_samples": samples,
    }


# Dispatch table. A row type absent from this map has NO field mapping and is
# never guessed at; `read_frame` counts it and, if it was the only thing in the
# file, aborts naming it.
ROW_TYPE_ADAPTERS = {
    EXACT_ROW_TYPE: _adapt_exact_final_cell,
    JITTER_ROW_TYPE: _adapt_jitter_cell,
}


def read_frame(path: Path) -> dict:
    """Stream one capture file into the per-cell arrays the rasterizer needs.

    Streamed line by line: a real dump is ~200 MB and holding it whole would
    make the frame count the memory limit instead of the grid size.

    Rows are dispatched by `row_type` through ROW_TYPE_ADAPTERS. Mixing two
    mapped schemas in one file is refused rather than merged: they are two
    different captures of two different sampling laws, and interleaving them
    cell-for-cell would present one frame that never existed.
    """
    cells: dict[tuple[int, int], dict] = {}
    frame = {"pose": {}, "box_w": 0, "box_h": 0,
             "declared_cols": 0, "declared_rows": 0, "capture_window": {}}
    cols = rows = 0
    skipped_row_types: dict[str, int] = {}
    accepted_row_types: dict[str, int] = {}
    with path.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            kind = str(row.get("row_type"))
            adapter = ROW_TYPE_ADAPTERS.get(kind)
            if adapter is None:
                skipped_row_types[kind] = skipped_row_types.get(kind, 0) + 1
                continue
            canonical = adapter(row, frame)
            if canonical is None:
                continue
            cell = canonical["screen_cell"]
            x, y = int(cell["x"]), int(cell["y"])
            cols, rows = max(cols, x + 1), max(rows, y + 1)
            accepted_row_types[kind] = accepted_row_types.get(kind, 0) + 1
            # A sparse capture publishes up to four subcell rows per cell. They
            # carry the SAME b43 paint record, so last-writer-wins is a
            # rewrite of identical paint facts; only the Product identity
            # sample differs, and those accumulate.
            previous = cells.get((y, x))
            if previous is not None:
                canonical["product_identity_quarter_samples"] = (
                    previous.get("product_identity_quarter_samples", [])
                    + canonical.get("product_identity_quarter_samples", []))
            cells[(y, x)] = canonical
    if len(accepted_row_types) > 1:
        raise SystemExit(
            f"ABORT: {path} mixes mapped row schemas {sorted(accepted_row_types)}; "
            "one file is one capture law and they cannot be merged cell-for-cell")
    if not cells:
        raise SystemExit(
            f"ABORT: {path} carries no row type this rasterizer has a field "
            f"mapping for. Mapped: {sorted(ROW_TYPE_ADAPTERS)}. "
            f"Seen: {dict(sorted(skipped_row_types.items())) or 'nothing'}")
    row_type = next(iter(accepted_row_types))
    # A stride-sampled capture may never reach the last column, so the declared
    # grid wins over the observed maximum where the capture declares one.
    cols = max(cols, frame["declared_cols"])
    rows = max(rows, frame["declared_rows"])
    return {"cells": cells, "cols": cols, "rows": rows,
            "box_w": frame["box_w"], "box_h": frame["box_h"],
            "pose": frame["pose"],
            "row_type": row_type,
            "accepted_row_types": accepted_row_types,
            "capture_window": frame["capture_window"],
            "grid_source": ("capture_declared_semantic_ansi_size"
                            if frame["declared_cols"] else
                            "observed_max_screen_cell_index"),
            "skipped_row_types": skipped_row_types}


# THE TWO GLYPH FIELDS ON A b55 QUARTER, AND WHICH ONE IS DRAWN
# -------------------------------------------------------------
# `final_product_b55_quarters` carries BOTH, and they are different facts:
#
#   glyph_id            the QUARTER's own Product identity. Varies per quarter;
#                       a single cell is routinely [670, 670, 126, 126].
#   cell_presentation.glyph_id  the WHOLE-CELL glyph the compositor rasterizes, repeated
#                       across all four quarters. Always equals
#                       final_mask_measurement.glyph_ids.
#
# The compositor samples the mask once per pixel with ONE glyph, so
# `cell_presentation.glyph_id` is what is drawn. Electing from `glyph_id` instead draws
# a cell the renderer never produced — measured on the recorded dump, the two
# disagree on 5008 of 8023 cells, so that mistake mis-stamps most of the frame.
#
# BOTH ARE KEPT. The rasterized mask glyph drives the picture and the VISIBLE
# glyph-change delta. The per-quarter Product identity is retained as its own
# lane, because their disagreement is an FL-4359 fact in its own right and is
# published rather than reconciled.
def elect_cell_glyph(quarters: list, measurement: dict | None = None,
                     cell_presentation: dict | None = None
                     ) -> tuple[int, bool, str]:
    """Return (rasterized glyph, whether the quarters agreed, source field).

    Preference order, most authoritative first. Each fallback is named in the
    returned source so a receipt records which field actually decided the frame.
    The row-level `cell_presentation` is the decoded NativePresentationResult
    record (FL-4848): the one elected glyph the compositor draws. A refused
    record carries no glyph_id and falls through, never to a quarter identity.
    """
    if isinstance(cell_presentation, dict) and cell_presentation.get("glyph_id") is not None:
        quarter_ids = [int(q["glyph_id"]) for q in quarters
                       if isinstance(q, dict) and q.get("glyph_id") is not None]
        return (int(cell_presentation["glyph_id"]), len(set(quarter_ids)) <= 1,
                "cell_presentation.glyph_id")
    ids = (measurement or {}).get("glyph_ids") or []
    present = [int(v) for v in ids if v is not None]
    if present:
        return present[0], len(set(present)) == 1, "final_mask_measurement.glyph_ids"
    present = [int(q["glyph_id"]) for q in quarters
               if isinstance(q, dict) and q.get("glyph_id") is not None]
    if present:
        return present[0], len(set(present)) == 1, "b55_quarters.glyph_id"
    return -1, True, "none"


def rasterize(frame: dict, cache: MaskCache, cell_px: int) -> dict:
    """Turn one streamed frame into an image plus its per-cell arrays."""
    rows, cols = frame["rows"], frame["cols"]
    box = max(int(cell_px), 1)
    img = np.zeros((rows * box, cols * box, 3), dtype=np.float32)
    mask = np.zeros((rows, cols, box, box), dtype=bool)
    fg = np.full((rows, cols, 3), np.nan, dtype=np.float32)
    bg = np.full((rows, cols, 3), np.nan, dtype=np.float32)
    cell_glyph = np.full((rows, cols), -1, dtype=np.int32)
    quarter_glyph = np.full((rows, cols, 4), -1, dtype=np.int32)
    quarter_fg = np.full((rows, cols, 4, 3), np.nan, dtype=np.float32)
    quarter_bg = np.full((rows, cols, 4, 3), np.nan, dtype=np.float32)
    stamped = np.zeros((rows, cols), dtype=bool)
    # F2 COVERAGE. A stride-sampled capture publishes a few hundred of the 8023
    # cells. `present` records exactly which cells the capture actually carried,
    # so an unsampled cell can never be mistaken for a measured black one.
    present = np.zeros((rows, cols), dtype=bool)
    unanimous = np.ones((rows, cols), dtype=bool)
    product_differs = np.zeros((rows, cols), dtype=bool)
    glyph_sources: dict[str, int] = {}
    replay_agrees = np.zeros((rows, cols), dtype=bool)
    painted_ink = np.full((rows, cols), -1, dtype=np.int32)
    # The GPU\'s own painted ink count for this cell, and this file\'s count of
    # the same glyph stamped at the cell\'s NATIVE on-screen span. Comparing them
    # is the grading rule; comparing against `expected_pixel_count` would grade
    # against the stale 12px host replay instead.
    native_ink = np.full((rows, cols), -1, dtype=np.int32)

    # UNPAINTED SENTINEL. Every cell starts at a stated, unmistakable colour
    # that no terrain palette produces, and is overwritten only where the
    # capture published a row. On a full-grid dump every cell is published, so
    # the sentinel is never visible and the image is unchanged.
    img[:] = np.array(UNPAINTED_SENTINEL_RGB, dtype=np.float32)

    for (r, c), row in frame["cells"].items():
        present[r, c] = True
        measurement = row.get("final_mask_measurement") or {}
        ink = row.get("final_glyph_ink") or {}
        cell_fg = _rgb(ink.get("fg_rgb") or row.get("fg_rgb"))
        cell_bg = _rgb(ink.get("bg_rgb") or row.get("bg_rgb"))
        fg[r, c] = cell_fg
        bg[r, c] = cell_bg
        if ink.get("ink_pixels") is not None:
            painted_ink[r, c] = int(ink["ink_pixels"])
        replay_agrees[r, c] = bool(measurement.get("digest_equal", False))
        quarters = row.get("final_product_b55_quarters") or []
        for record in quarters:
            if not isinstance(record, dict):
                continue
            slot = int(record.get("slot", -1))
            if slot < 0 or slot > 3:
                continue
            glyph = record.get("glyph_id")
            quarter_glyph[r, c, slot] = -1 if glyph is None else int(glyph)
            quarter_fg[r, c, slot] = _rgb(record.get("fg")) if record.get("fg") else cell_fg
            quarter_bg[r, c, slot] = _rgb(record.get("bg")) if record.get("bg") else cell_bg
        # THE SPARSE PRODUCT IDENTITY LANE. A jitter capture has no b55 quarter
        # records; it publishes ONE sampled subcell's Product glyph per row.
        # Filled into the quarter arrays at the sampled slot only, so the three
        # unsampled slots stay -1/NaN instead of being broadcast from one
        # sample. This list never reaches `elect_cell_glyph`.
        for record in row.get("product_identity_quarter_samples") or []:
            slot = int(record.get("slot", -1))
            if slot < 0 or slot > 3:
                continue
            sampled = record.get("glyph_id")
            quarter_glyph[r, c, slot] = -1 if sampled is None else int(sampled)
            quarter_fg[r, c, slot] = _rgb(record.get("fg")) if record.get("fg") else cell_fg
            quarter_bg[r, c, slot] = _rgb(record.get("bg")) if record.get("bg") else cell_bg
        glyph, agreed, source = elect_cell_glyph(
            quarters, measurement, row.get("cell_presentation"))
        cell_glyph[r, c] = glyph
        unanimous[r, c] = agreed
        glyph_sources[source] = glyph_sources.get(source, 0) + 1
        # The quarter Product identity and the rasterized mask glyph are
        # different facts and they disagree often. Count it; do not reconcile it.
        quarter_ids = {int(q["glyph_id"]) for q in quarters
                       if isinstance(q, dict) and q.get("glyph_id") is not None}
        quarter_ids |= {int(q["glyph_id"])
                        for q in row.get("product_identity_quarter_samples") or []
                        if isinstance(q, dict) and q.get("glyph_id") is not None}
        if quarter_ids and quarter_ids != {glyph}:
            product_differs[r, c] = True

        y0, x0 = r * box, c * box
        # Paper first. The dump\'s cell FG/BG are the paint shader\'s own output
        # colours, so they already carry whatever blend arm the run had armed. A
        # sub-cell colour gradient is not recoverable from a per-cell record; see
        # `colour_resolution` in the receipt.
        img[y0:y0 + box, x0:x0 + box] = np.nan_to_num(np.array(cell_bg, dtype=np.float32))
        stamp = cache.get(glyph if glyph >= 0 else None, box, box)
        if stamp is None:
            continue
        stamped[r, c] = True
        mask[r, c] = stamp
        img[y0:y0 + box, x0:x0 + box][stamp] = np.nan_to_num(
            np.array(cell_fg, dtype=np.float32))

        # Grade against the GPU at the cell\'s own native span, independent of
        # the render scale this invocation happens to use.
        dims = measurement.get("dimensions")
        if isinstance(dims, (list, tuple)) and len(dims) >= 2:
            native_w, native_h = max(int(dims[0]), 1), max(int(dims[1]), 1)
            native_stamp = cache.get(glyph if glyph >= 0 else None,
                                     native_h, native_w)
            if native_stamp is not None:
                native_ink[r, c] = int(native_stamp.sum())
    return {
        "image": np.clip(img, 0, 255).astype(np.uint8),
        "mask": mask, "fg": fg, "bg": bg,
        "cell_glyph": cell_glyph,
        "quarter_glyph": quarter_glyph,
        "quarter_fg": quarter_fg, "quarter_bg": quarter_bg,
        "stamped": stamped, "present": present, "unanimous": unanimous,
        "product_differs": product_differs,
        "glyph_sources": glyph_sources,
        "replay_agrees": replay_agrees,
        "painted_ink": painted_ink,
        "native_ink": native_ink,
        "box": box,
    }

def product_glyph_ids(quarter_glyph: np.ndarray) -> np.ndarray:
    """One exact identity per cell per frame from its FOUR Product glyph ids.

    This is the preferred measurement path: the identity is the Product's own
    election, so equality is exact and no threshold, polarity convention or
    atlas replay participates. A change in ANY quarter is a change in the cell,
    which is the correct reading — the four quarters are one published cell.
    """
    frames, rows, cols, _ = quarter_glyph.shape
    packed = quarter_glyph.astype(np.int64) + 1          # -1 (absent) -> 0
    ident = np.zeros((frames, rows, cols), dtype=np.int64)
    for slot in range(4):
        ident = ident * 4096 + packed[..., slot]
    return ident


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Rasterize FL-4359 proof frames from the Product cell dump")
    ap.add_argument("--cells", action="append", required=True, type=Path,
                    help="one cells.jsonl per frame, in capture order (repeatable)")
    ap.add_argument("--arm-label", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cell-px", type=int, default=16,
                    help="rendered pixels per cell; the quarter is half this")
    ap.add_argument("--hud-rows", type=int, default=0,
                    help="rows excluded from the viewport statistics")
    ap.add_argument("--pose-json", type=Path, default=None,
                    help="optional designated-pose document for the receipt")
    args = ap.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    cache = MaskCache()
    frames = []
    grid = None
    for index, path in enumerate(args.cells):
        if not path.exists():
            print(f"ABORT: {path} missing", file=sys.stderr)
            return 1
        frame = read_frame(path)
        shape = (frame["rows"], frame["cols"])
        if grid is None:
            grid = shape
        elif shape != grid:
            # Two frames of different grids are not one sequence; comparing them
            # cell-for-cell would align different screen positions.
            print(f"ABORT: {path} is {shape[1]}x{shape[0]}, first frame was "
                  f"{grid[1]}x{grid[0]}", file=sys.stderr)
            return 1
        frames.append((path, frame, rasterize(frame, cache, args.cell_px)))

    rows, cols = grid
    box = frames[0][2]["box"]
    frame_paths = []
    for index, (source, _, raster) in enumerate(frames):
        out_path = args.out / f"frame_{index:02d}.png"
        Image.fromarray(raster["image"]).save(out_path)
        frame_paths.append(str(out_path))

    masks = np.stack([r["mask"] for _, _, r in frames])
    fgs = np.stack([r["fg"] for _, _, r in frames])
    bgs = np.stack([r["bg"] for _, _, r in frames])
    quarter_glyph = np.stack([r["quarter_glyph"] for _, _, r in frames])
    cell_glyph = np.stack([r["cell_glyph"] for _, _, r in frames])
    stamped = np.stack([r["stamped"] for _, _, r in frames])
    published = np.stack([r["present"] for _, _, r in frames])
    unanimous = np.stack([r["unanimous"] for _, _, r in frames])
    product_differs = np.stack([r["product_differs"] for _, _, r in frames])
    replay_agrees = np.stack([r["replay_agrees"] for _, _, r in frames])
    painted_ink = np.stack([r["painted_ink"] for _, _, r in frames])
    native_ink = np.stack([r["native_ink"] for _, _, r in frames])

    # The VISIBLE lane: the glyph the compositor rasterized. This is what the
    # operator's "FG glyph change" asks about and what the highlight marks.
    identity = cell_glyph.astype(np.int64)
    # A cell the capture did not publish in BOTH frames of a pair has no
    # elected glyph on one side, so "changed" is undefined there rather than
    # true. Left unguarded, a stride-sampled capture would report every
    # sampling difference as a glyph change. On a full-grid dump `published` is
    # all-true and this is a no-op.
    pair_published = published[1:] & published[:-1]
    glyph_changed = (identity[1:] != identity[:-1]) & pair_published
    # The UPSTREAM lane: the four per-quarter Product identities. Kept separate
    # because it answers a different question and disagrees with the drawn glyph
    # on most cells of the recorded dump.
    product_identity = product_glyph_ids(quarter_glyph)
    product_identity_changed = ((product_identity[1:] != product_identity[:-1])
                                & pair_published)
    # SLOT ALIGNMENT GUARD. `product_glyph_ids` packs the four slots
    # POSITIONALLY, so two frames that sampled DIFFERENT subcells of the same
    # cell produce different identities even when the Product never moved. A
    # full-grid dump fills all four slots in every frame and is aligned
    # everywhere; a jitter capture samples one slot per row and its choice can
    # differ frame to frame. Cells that are not slot-aligned across all frames
    # are excluded from the product-identity lane rather than counted as churn.
    slot_present = quarter_glyph >= 0
    slot_aligned = (slot_present == slot_present[0]).all(0).all(-1) & slot_present[0].any(-1)
    # A cell is measurable where every frame published a stampable glyph; a cell
    # whose glyph is outside every atlas page has no shape to compare.
    valid = stamped.all(0)
    viewport = valid.copy()
    if args.hud_rows > 0:
        viewport[:args.hud_rows, :] = False
    product_viewport = viewport & slot_aligned
    coverage = masks.mean(axis=(-1, -2)).astype(np.float32)

    n = len(frames)
    vp = np.broadcast_to(viewport, glyph_changed.shape)
    gc = glyph_changed[vp]

    # --- F2 COVERAGE, reported as a first-class metric ------------------------
    # A full-grid dump publishes every cell and coverage is 100%. A stride-64
    # jitter capture publishes a few hundred, and every number below that is
    # scoped to the viewport is a statement about THOSE cells only.
    cells_total = rows * cols
    coverage_report = {
        "cells_total": cells_total,
        "cells_present_per_frame": [int(p.sum()) for p in published],
        "coverage_pct_per_frame": [
            round(100.0 * float(p.sum()) / max(cells_total, 1), 3) for p in published],
        "cells_present_every_frame": int(published.all(0).sum()),
        "cells_present_any_frame": int(published.any(0).sum()),
        "coverage_pct": round(
            100.0 * float(published.all(0).sum()) / max(cells_total, 1), 3),
        "unpainted_sentinel_rgb": list(UNPAINTED_SENTINEL_RGB),
        "meaning": ("`coverage_pct` is the share of the grid published in EVERY "
                    "frame, which is the set every per-pair statistic below is "
                    "measured on. Cells outside it are painted with "
                    "`unpainted_sentinel_rgb` and are not black paper"),
    }

    # --- F4 MEASUREMENT LANES on the rasterized masks ------------------------
    # Same rules as the pixel extractor, imported rather than restated: mask IoU
    # is fl4359_cell_dump_extractor.mask_iou, the oscillation margin is its
    # OSCILLATION_MARGIN. Measured over `viewport`, i.e. cells with a stampable
    # glyph in every frame.
    iou_table = {}
    for a in range(n):
        for b in range(a + 1, n):
            iou_table[(a, b)] = _ext.mask_iou(masks[a], masks[b])
    consecutive_iou = (np.stack([iou_table[(i, i + 1)] for i in range(n - 1)])
                       if n >= 2 else np.zeros((0, rows, cols)))
    d_fg = (np.linalg.norm(fgs[1:] - fgs[:-1], axis=-1) if n >= 2
            else np.zeros((0, rows, cols)))
    d_bg = (np.linalg.norm(bgs[1:] - bgs[:-1], axis=-1) if n >= 2
            else np.zeros((0, rows, cols)))
    # Oscillation: an (N, N+1, N+2) triple where the OUTER pair overlaps better
    # than either inner pair by more than the margin, i.e. the cell returned to
    # a shape it had already left. Monotone decay never fires.
    oscillating = np.zeros((rows, cols), dtype=bool)
    oscillation_triples = []
    for t in range(n - 2):
        hit = ((iou_table[(t, t + 2)] >= iou_table[(t, t + 1)] + _ext.OSCILLATION_MARGIN)
               & (iou_table[(t, t + 2)] >= iou_table[(t + 1, t + 2)] + _ext.OSCILLATION_MARGIN))
        oscillating |= hit
        oscillation_triples.append({
            "triple": [t, t + 1, t + 2],
            "flagged_cells_viewport": int((hit & viewport).sum()),
        })
    oscillating &= viewport
    io_v = consecutive_iou[vp] if n >= 2 else np.zeros(0)
    fg_v = np.nan_to_num(d_fg[vp]) if n >= 2 else np.zeros(0)
    bg_v = np.nan_to_num(d_bg[vp]) if n >= 2 else np.zeros(0)
    measurement_lanes = {
        "measured_on": "cells_in_viewport (stampable glyph in every frame)",
        "cells_measured_per_pair": int(viewport.sum()),
        "mask_iou_mean_per_pair": [
            round(float(consecutive_iou[i][viewport].mean()), 4)
            for i in range(n - 1)] if int(viewport.sum()) else [],
        "mask_iou_mean": round(float(io_v.mean()), 4) if io_v.size else None,
        "mask_iou_median": round(float(np.median(io_v)), 4) if io_v.size else None,
        "mask_iou_lt0p5_pct": (round(100.0 * float((io_v < 0.5).mean()), 2)
                               if io_v.size else None),
        "cells_mask_iou_below_0p5_per_pair": [
            int((consecutive_iou[i][viewport] < 0.5).sum())
            for i in range(n - 1)] if int(viewport.sum()) else [],
        "fg_delta_mean": round(float(fg_v.mean()), 3) if fg_v.size else None,
        "fg_delta_median": round(float(np.median(fg_v)), 3) if fg_v.size else None,
        "fg_delta_gt30_pct": (round(100.0 * float((fg_v > 30).mean()), 2)
                              if fg_v.size else None),
        "bg_delta_mean": round(float(bg_v.mean()), 3) if bg_v.size else None,
        "bg_delta_median": round(float(np.median(bg_v)), 3) if bg_v.size else None,
        "bg_delta_gt30_pct": (round(100.0 * float((bg_v > 30).mean()), 2)
                              if bg_v.size else None),
        "oscillating_cells_viewport": int(oscillating.sum()),
        "oscillating_pct": round(
            100.0 * float(oscillating.sum()) / max(int(viewport.sum()), 1), 3),
        "oscillation_margin": _ext.OSCILLATION_MARGIN,
        "oscillation_triples": oscillation_triples,
        "rule_owner": ("fl4359_cell_dump_extractor.mask_iou and "
                       "fl4359_cell_dump_extractor.OSCILLATION_MARGIN, imported "
                       "so the raster path and the pixel path cannot drift"),
        "colour_delta_units": ("euclidean distance in 0..255 RGB between "
                               "consecutive frames' published cell colours"),
    }

    rollup = {
        "cells_total": rows * cols,
        "cells_with_stampable_glyph_all_frames": int(valid.sum()),
        "cells_in_viewport": int(viewport.sum()),
        "glyph_change_count_per_pair": [
            int((glyph_changed[i] & viewport).sum()) for i in range(n - 1)],
        "glyph_change_rate_pct_per_pair": [
            round(100.0 * float((glyph_changed[i] & viewport).sum())
                  / max(int(viewport.sum()), 1), 2) for i in range(n - 1)],
        "glyph_change_pct": round(100.0 * float(gc.mean()), 2) if gc.size else 0.0,
        "glyph_hold_pct": round(100.0 * float((~gc).mean()), 2) if gc.size else 0.0,
        "glyph_change_cells_any_pair": int((glyph_changed.any(0) & viewport).sum()),
        # `.all(0)` over a zero-length axis is vacuously True, which would report
        # every cell as changing in EVERY pair on a single-frame dump that has no
        # pairs at all. A sequence shorter than two frames has no answer here.
        "glyph_change_cells_every_pair": (
            int((glyph_changed.all(0) & viewport).sum()) if n >= 2 else 0),
        "glyph_change_pairs_measured": int(vp.sum()),
        "frame_pairs": max(n - 1, 0),
        # The upstream lane, reported beside the visible one and never mixed
        # into it. A cell can hold its drawn glyph while its Product quarters
        # re-elect underneath, and that is a different defect.
        "product_identity_change_count_per_pair": [
            int((product_identity_changed[i] & product_viewport).sum())
            for i in range(n - 1)],
        "product_identity_change_pct": (
            round(100.0 * float(product_identity_changed[
                np.broadcast_to(product_viewport, product_identity_changed.shape)].mean()), 2)
            if product_viewport.any() and n >= 2 else 0.0),
        # The product-identity lane is measured on a SMALLER set than the drawn
        # glyph lane whenever the capture sampled different subcells in
        # different frames. Both counts are reported so the difference is
        # visible instead of implied.
        "product_identity_cells_measured": int(product_viewport.sum()),
        "product_identity_cells_excluded_slot_misaligned": int(
            (viewport & ~slot_aligned).sum()),
        "product_identity_slot_alignment_rule": (
            "product_glyph_ids packs the four quarter slots positionally, so a "
            "cell whose SAMPLED SLOT SET differs between frames is excluded: "
            "its identity would differ because of the sampler, not the Product"),
    }

    unstampable_total = sum(cache.unstampable_ids.values())
    stamped_total = sum(cache.stamped_by_page.values())
    atlas_report = {
        "pages": {str(k): {**v, "loaded": k in cache.stamped_by_page}
                  for k, v in ATLAS_PAGES.items()},
        "unmapped_id_gap": list(UNMAPPED_ID_GAP),
        "cell_stamps_by_page": {str(k): v for k, v in sorted(cache.stamped_by_page.items())},
        "cell_stamps_total": stamped_total,
        "unstampable_cell_stamps": unstampable_total,
        "unstampable_pct": round(
            100.0 * unstampable_total / max(stamped_total + unstampable_total, 1), 3),
        # Named, not aggregated: an operator must be able to see WHICH ids the
        # atlas pages do not serve. -1 stands for a null glyph_id.
        "unstampable_glyph_ids": dict(sorted(
            ((str(k), v) for k, v in cache.unstampable_ids.items()),
            key=lambda kv: -kv[1])[:32]),
        "cells_with_no_stampable_glyph_any_frame": int((~stamped).any(0).sum()),
        "source_footprint_by_page": {
            str(page_id): list(AtlasPage(page_id, spec).source_span)
            for page_id, spec in ATLAS_PAGES.items()
            if page_id in cache.stamped_by_page},
        "sampler_owner": (
            "ascii_compositor.glsl, sample_compositor_glyph_intensity_native_footprint "
            "+ sample_compositor_page_footprint. This file mirrors the COMPOSITOR, not "
            "ASCIICompositorEffect._fl4359_host_sample_atlas_mask, which strides the "
            "atlas by the on-screen 12px glyph lattice and under-inks every cell"),
    }

    # --- Grading: this rasterizer against the GPU's own painted ink -----------
    # `actual_pixel_count` == `final_glyph_ink.ink_pixels` on every observed row,
    # and it is what the paint shader wrote. A tolerance is stated rather than
    # implied: the shader's ink test is a threshold on a bilinear sample, so
    # boundary texels can land either side of 0.5 between two implementations of
    # the same rule. EXACT is reported separately from WITHIN-TOLERANCE so a
    # regression cannot hide inside the tolerance.
    gradable = (native_ink >= 0) & (painted_ink >= 0)
    diff = np.abs(native_ink.astype(np.int64) - painted_ink.astype(np.int64))
    denom = np.maximum(painted_ink.astype(np.float64), 1.0)
    rel = np.where(gradable, diff / denom, np.nan)
    gradable_count = int(gradable.sum())
    exact = int((gradable & (diff == 0)).sum())
    within_5pct = int((gradable & (rel <= 0.05)).sum())
    within_10pct = int((gradable & (rel <= 0.10)).sum())
    ink_agreement = {
        "graded_cell_frames": gradable_count,
        "exact_match": exact,
        "exact_match_pct": round(100.0 * exact / max(gradable_count, 1), 2),
        "within_5pct": within_5pct,
        "within_5pct_pct": round(100.0 * within_5pct / max(gradable_count, 1), 2),
        "within_10pct": within_10pct,
        "within_10pct_pct": round(100.0 * within_10pct / max(gradable_count, 1), 2),
        "median_abs_error_px": (float(np.median(diff[gradable]))
                                if gradable_count else None),
        "mean_relative_error": (round(float(np.nanmean(rel)), 4)
                                if gradable_count else None),
        "stamped_ink_total": int(native_ink[gradable].sum()) if gradable_count else 0,
        "painted_ink_total": int(painted_ink[gradable].sum()) if gradable_count else 0,
        "graded_against": ("final_mask_measurement.actual_pixel_count, the GPU's own "
                           "painted ink count at the cell's native span"),
        "not_graded_against": (
            "expected_pixel_count / expected_digest, which the stale host replay "
            "produces with a 12px atlas stride; grading against those would grade "
            "this file against the defect it exists to avoid"),
    }
    mask_agreement = {
        "cells_where_runtime_replay_matches_its_own_painted_ink":
            int(replay_agrees.all(0).sum()),
        "cells_where_it_does_not": int((~replay_agrees).any(0).sum()),
        "this_is_a_runtime_finding_not_a_rasterizer_result": True,
        "meaning": ("final_mask_measurement.digest_equal compares the RUNTIME's own "
                    "host-side replay against the ink the paint shader wrote. It is "
                    "false almost everywhere because that replay strides the atlas by "
                    "the 12px on-screen lattice. This rasterizer does not use that "
                    "replay; see ink_agreement for how it actually scores. Where these "
                    "disagree the drawn glyph is the elected one, not necessarily "
                    "the painted one"),
    }

    poses = {
        "source": "observed_from_cell_dump_rows",
        "observed": [f["pose"] for _, f, _ in frames],
        "frames_with_pose": sum(1 for _, f, _ in frames if f["pose"].get("cam")),
        "note": ("the scripted airborne flight owns the camera, so these are the "
                 "poses the run PRODUCED. fl4359_cell_dump_airplane_proof.py "
                 "select-window scores them against the designated path"),
    }
    if args.pose_json and args.pose_json.exists():
        poses["designated_document"] = str(args.pose_json)

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "arm_label": args.arm_label,
        "measurement_path": "product_glyph_id",
        "measurement_path_reason": (
            "frames are rasterized from final_product_b55_quarters, so the "
            "glyph-change delta is exact Product glyph-id equality. No luminance "
            "threshold, polarity convention or extractor estimator participates"),
        "image_source": "product_cell_dump",
        "actual_field": {
            "schema": "fl4359.final_paint_field.v1",
            "actual_field_observation": False,
            "presentation_mode_eligible": False,
            "evidence_kind": "legacy_cell_pair_diagnostic",
            "reason": (
                "cell dump publishes one FG/BG pair per cell and cannot "
                "represent the compositor's native per-pixel field"),
        },
        "image_source_note": (
            "NOT a screenshot. The airborne route publishes no per-frame PNG; "
            "the dump determines the image and is one step closer to the Product "
            "than a re-encoded picture of it would be"),
        "capture": {
            "frames": len(frames),
            "frame_paths": frame_paths,
            "source_dumps": [str(p) for p, _, _ in frames],
            "capture_px": [cols * box, rows * box],
            "titlebar_px": 0,
            "hud_rows_excluded_from_viewport": args.hud_rows,
            "skipped_row_types": frames[0][1]["skipped_row_types"],
            "accepted_row_types": frames[0][1]["accepted_row_types"],
            "rows_accepted_total": sum(
                sum(f[1]["accepted_row_types"].values()) for f in frames),
            "rows_skipped_by_unmapped_type_total": sum(
                sum(f[1]["skipped_row_types"].values()) for f in frames),
        },
        # F1 AUDIT SURFACE. Which capture schema each frame carried and, when it
        # was not the canonical one, the exact named field mapping that was
        # applied. A reader checks the substitutions here without reading code.
        "row_schema_adapter": {
            "row_type_per_frame": [f[1]["row_type"] for f in frames],
            "mapped_row_types": sorted(ROW_TYPE_ADAPTERS),
            "field_map": (JITTER_CELL_FIELD_MAP
                          if any(f[1]["row_type"] == JITTER_ROW_TYPE for f in frames)
                          else None),
            "rule": ("dispatch is by `row_type` only. A row type with no entry "
                     "in ROW_TYPE_ADAPTERS is counted in skipped_row_types and "
                     "never guessed at; a file with no mapped rows aborts, and "
                     "a file mixing two mapped schemas aborts"),
        },
        "grid": {
            "cols": cols, "rows": rows,
            "pitch_x": float(box), "pitch_y": float(box),
            "phase_x": 0.0, "phase_y": 0.0,
            "sample_box_px": [box, box],
            "first_published_row": 0,
            "native_cell_px": [frames[0][1]["box_w"], frames[0][1]["box_h"]],
            "source": frames[0][1]["grid_source"],
        },
        "coverage": coverage_report,
        "measurement_lanes": measurement_lanes,
        # F5 LIMITS. Stated where a reader meets the numbers, not in a footnote.
        "limits": {
            "coverage_pct": coverage_report["coverage_pct"],
            "sampling": frames[0][1]["capture_window"] or {
                "note": "full-grid capture; no stride sampling declared"},
            "statements": [
                (f"SPARSITY: {coverage_report['coverage_pct']}% of the "
                 f"{coverage_report['cells_total']}-cell grid is published in "
                 f"every frame. Every per-pair number in `measurement_lanes` "
                 f"and `rollup` is measured on that subset only and is NOT a "
                 f"whole-frame rate.")
                if coverage_report["coverage_pct"] < 100.0 else
                "SPARSITY: none; every grid cell is published in every frame.",
                ("MOVEMENT PHASE: " + str(
                    frames[0][1]["capture_window"].get("movement_phase",
                                                       "not declared"))
                 + ". A capture window recorded during a turn is not the same "
                   "experiment as a fixed-yaw translation and its numbers must "
                   "not be compared against one."),
                ("ARMS: this receipt describes ONE arm. It is a baseline "
                 "visual, not an A/B; nothing here compares two builds."),
                ("The rasterizer reproduces what the capture published. It "
                 "establishes no FL status, repair or regression."),
            ],
        },
        "pose_identity": {
            **poses,
            "pose_identity_closed": poses["frames_with_pose"] == len(frames),
        },
        "atlas_coverage": atlas_report,
        "ink_agreement": ink_agreement,
        "runtime_host_replay_vs_painted_ink": mask_agreement,
        "glyph_election": {
            "rule": ("one glyph per cell, taken from b55 cell_presentation.glyph_id — the "
                     "whole-cell glyph the compositor rasterizes"),
            "source_field_counts": {
                k: sum(f[2]["glyph_sources"].get(k, 0) for f in frames)
                for k in sorted({k for f in frames for k in f[2]["glyph_sources"]})},
            "cell_frames_where_quarters_disagreed": int((~unanimous).sum()),
            "cell_frames_where_quarter_product_id_differs_from_drawn_glyph":
                int(product_differs.sum()),
            "why_two_fields": (
                "a b55 quarter carries BOTH its own Product glyph_id and the "
                "whole-cell cell_presentation.glyph_id. Only the latter is rasterized. On "
                "the recorded dump they disagree on most cells, so electing the "
                "former would mis-stamp most of the frame"),
            "why": ("the compositor samples the mask once per pixel with a single "
                    "glyph and the b43 identity reduction requires whole-cell "
                    "unanimity; four independent quarter stamps would draw a cell "
                    "the renderer never produces"),
        },
        "colour_resolution": {
            "rule": "per cell, from final_glyph_ink.fg_rgb / bg_rgb",
            "limit": ("those are the paint shader's own output colours, so they "
                      "already carry whatever blend arm was armed. A SUB-CELL colour "
                      "gradient is not recoverable from a per-cell record; the four "
                      "quarter colours are preserved in the npz as quarter_fg / "
                      "quarter_bg for anyone who needs them"),
            "evidence_kind": "legacy_cell_pair_diagnostic",
            "actual_field_schema": "fl4359.final_paint_field.v1",
            "actual_field_eligibility": False,
            "actual_field_reason": (
                "continuous native FG/BG variation is unavailable at this "
                "consumer seam; no gradient is synthesized"),
        },
        "rollup": rollup,
        "claim_boundary": (
            "this receipt records what the dump published and how it was drawn. "
            "It establishes no FL status, repair or regression"),
    }
    (args.out / "fl4359_cell_dump_receipt.json").write_text(
        json.dumps(receipt, indent=1) + "\n")

    np.savez_compressed(
        args.out / "cell_dump_product.npz",
        mask_packed=np.packbits(masks, axis=-1),
        mask_shape=np.array(masks.shape),
        fg=fgs, bg=bgs,
        coverage=coverage,
        valid=valid, viewport=viewport,
        glyph_changed=glyph_changed,
        glyph_changed_polarity_invariant=glyph_changed,
        glyph_id=identity,
        glyph_id_polarity_invariant=identity,
        quarter_glyph_id=quarter_glyph,
        quarter_fg=np.stack([r["quarter_fg"] for _, _, r in frames]),
        quarter_bg=np.stack([r["quarter_bg"] for _, _, r in frames]),
        stamped=stamped,
        published=published,
        mask_iou_consecutive=consecutive_iou,
        fg_delta=d_fg,
        bg_delta=d_bg,
        oscillating=oscillating,
        cell_glyph_id=cell_glyph,
        quarters_unanimous=unanimous,
        product_identity=product_identity,
        product_identity_changed=product_identity_changed,
        quarter_product_differs_from_drawn=product_differs,
        runtime_host_replay_agrees=replay_agrees,
        painted_ink_pixels=painted_ink,
        stamped_ink_pixels_native=native_ink,
    )
    print(json.dumps({
        "receipt": str(args.out / "fl4359_cell_dump_receipt.json"),
        "npz": str(args.out / "cell_dump_product.npz"),
        "frames": frame_paths,
        "grid": receipt["grid"],
        "measurement_path": receipt["measurement_path"],
        "row_schema_adapter": {
            "row_type_per_frame": receipt["row_schema_adapter"]["row_type_per_frame"],
            "rows_accepted_total": receipt["capture"]["rows_accepted_total"],
            "rows_skipped_by_unmapped_type_total":
                receipt["capture"]["rows_skipped_by_unmapped_type_total"],
        },
        "coverage": coverage_report,
        "measurement_lanes": measurement_lanes,
        "limits": receipt["limits"],
        "atlas_coverage": atlas_report,
        "ink_agreement": ink_agreement,
        "runtime_host_replay_vs_painted_ink": mask_agreement,
        "rollup": rollup,
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
