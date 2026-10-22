#!/usr/bin/env python3
# =============================================================================
# fl4359_raster_jitter_capture.py
# =============================================================================
# AUTHORITY (verbatim, repeated in the receipt this script writes):
#   FL-4359 remains OPEN, FL-4775 remains OPEN/RUNTIME_RED, the renderer remains
#   BROKEN, no acceptance claim. This file is DIAGNOSTIC machinery producing a
#   DIAGNOSTIC rendering. Nothing it emits is verified, passed, proven, fixed or
#   accepted about renderer behaviour.
#
# WHAT THIS IS
# ------------
# `scripts/fl4359_cell_dump_raster.py` rasterizes the FULL-GRID final-render cell
# dump (`final_render_cell_dump/<stamp>/cells.jsonl`): one JSON OBJECT per line,
# each object self-describing, each file one frame.
#
# The JITTER capture is a different transport of the SAME cell facts:
# `.run/.../jitter_capture.jsonl`, schema `fl4359_jitter_transport.v16`. It is a
# COLUMNAR stream, not a stream of objects:
#
#   line 1     {"row_type":"fl4359_jitter_frame","frame_id":0,"frame_trace":{...}}
#   line 2     {"row_type":"fl4359_jitter_schema",
#               "transport_schema":"fl4359_jitter_transport.v16",
#               "fields":[...297 names, sorted...]}
#   lines 3..  JSON LISTS, positional to that `fields` array, one per SAMPLED
#              SUBCELL. 32092 of them per frame = 8023 cells x 4 subcell slots.
#   then       a new `fl4359_jitter_frame` object, and the next frame's lists.
#
# The `fields` row appears ONCE, before the first frame's rows; the frame header
# repeats per frame and carries that frame's `camera_world` and pose. The file is
# 2.2 GB, so it is streamed line by line and never held whole. Only the columns
# this rasterizer actually consumes are lifted out of each list (see
# CONSUMED_COLUMNS): building a 297-key dict per row costs ~48M dict inserts over
# five frames and buys nothing.
#
# WHY IT DOES NOT RE-IMPLEMENT THE CELL DRAWING
# ----------------------------------------------
# `fl4359_cell_dump_raster.py` already owns:
#   * `_adapt_jitter_cell`  - the field-by-field map from an `fl4359_jitter_cell`
#     row to the canonical cell shape, with every substitution declared in
#     `JITTER_CELL_FIELD_MAP`;
#   * `elect_cell_glyph`    - the election that picks the DRAWN WHOLE-CELL glyph
#     from `final_paint_mask_glyphs`, and never from `final_product_glyph`
#     (the per-quarter Product identity, which disagrees with the painted glyph
#     on about half the rows and would mis-stamp half the frame);
#   * `MaskCache` / `AtlasPage` - the compositor's own native-footprint atlas
#     sampler (CP437 at 40 px read from RED, generated pages at 32 px read from
#     ALPHA), NOT the stale 12 px host replay;
#   * `rasterize`           - background from the cell BG, ink from the elected
#     mask stamped in the cell FG, magenta `UNPAINTED_SENTINEL_RGB` wherever the
#     capture published no row.
# Those are imported, not copied, so the two rasterizers cannot drift about what
# a cell looks like. This file owns exactly one thing the other does not: the
# v16 columnar transport reader.
#
# WHAT IS DRAWN, RESTATED SO A READER NEED NOT CHASE THE IMPORT
#   glyph : `final_paint_glyph` / `final_paint_mask_glyphs` - the whole-cell
#           elected mask the compositor rasterizes. NOT `final_product_glyph`.
#   fg/bg : `fg_rgb` / `bg_rgb`, the b43 paint record's own cell colours,
#           published here as 0..255 ints (high byte of each 16-bit channel) and
#           divided by 255 by the adapter to match the canonical 0..1 shape.
#   The per-subcell `final_product_glyph` / `final_paint_slot_fg_rgb` /
#   `final_paint_slot_bg_rgb` travel in the PRODUCT IDENTITY lane only. They are
#   recorded and reported; they never reach glyph election or the picture.
#
# OUTPUT
#   <out>/frame_%02d.png   one PNG per captured frame, the full ANSI cell grid
#                          (113x71 on this capture) at --cell-px per cell
#   <out>/receipt.json     schema fl4359_raster_jitter_capture_receipt.v1
#   <out>/<gif>            the first five frames at 60 ms, looping. When the
#                          capture carries one applied FG/BG policy, the GIF
#                          name ends with its explicit channel suffix, and the
#                          historical unsuffixed name is a byte-identical
#                          compatibility alias.
#
# `receipt.json` is deliberately named for `fl4359_stitch_sweep_gif.py`'s
# `_declared_poses`, which reads `frame_png_poses` from a `receipt.json` next to
# the frames to caption each panel. The poses it publishes are the ACTUAL
# `frame_trace.camera_world` read out of the capture, never a declared line.
#
# Exit codes: 0 = every requested frame rasterized; 1 = missing input, unknown
# transport schema, a missing consumed column, a grid that changes between
# frames, or an atlas page that cannot be loaded.
#
# USAGE
#   python3 scripts/fl4359_raster_jitter_capture.py \
#       --capture .run/fl4359_tier_ab_baseline/SOL-baseline-r10/jitter_capture.jsonl \
#       --out .run/fl4359_tier_ab_baseline/RASTER-baseline-r10 \
#       --cell-px 32 --frames 5 --arm-label baseline-r10
#
#   Add `--sentinel-fallback product_slot` to repaint the sentinel whole-cell
#   population from its per-quarter Product colours as a READABILITY AID. The
#   default `none` draws the capture as published; see the SENTINEL section
#   below.
# =============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# ONE OWNER FOR THE CELL DRAWING RULES. Importing rather than copying is what
# keeps this rasterizer and the cell-dump rasterizer agreeing on what a cell
# looks like; see the header.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fl4359_cell_dump_raster as _cdr  # noqa: E402
from fl4359_actual_field import (  # noqa: E402
    ACTUAL_FIELD_SCHEMA,
    ActualFieldError,
    ActualFieldReader,
    actual_field_absent_status,
    _eligibility,
)
# The rendering-family tints, mixes and dim factors are OWNED by
# fl4359_cell_dump_gifs.py, which renders the same five families off the Product
# cell dump. They are imported, never copied, so the jitter-capture route and
# the cell-dump route cannot drift into two different-looking `fg_only`s.
import fl4359_cell_dump_gifs as _cdg  # noqa: E402

RECEIPT_SCHEMA = "fl4359_raster_jitter_capture_receipt.v1"
ROOT = Path(__file__).resolve().parents[1]


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()

AUTHORITY = (
    "FL-4359 remains OPEN, FL-4775 remains OPEN/RUNTIME_RED, the renderer "
    "remains BROKEN, no acceptance claim. This is DIAGNOSTIC machinery and a "
    "DIAGNOSTIC rendering."
)

# Transport identities. A file that declares anything else is refused rather
# than read positionally against a fields array this file has not seen.
SUPPORTED_TRANSPORT_SCHEMAS = ("fl4359_jitter_transport.v16",)
FRAME_ROW_TYPE = "fl4359_jitter_frame"
SCHEMA_ROW_TYPE = "fl4359_jitter_schema"
CELL_ROW_TYPE = "fl4359_jitter_cell"

# The ONLY columns lifted out of each positional list row. Every one of them is
# consumed by `_cdr._adapt_jitter_cell`; the other 280+ columns are not read, so
# materializing them per row would be pure cost. Each name is resolved against
# the file's own `fields` array by NAME, and a missing name aborts - a positional
# read against a drifted schema would produce a confident wrong picture.
CONSUMED_COLUMNS = (
    "row_type",
    "frame_id",
    "screen_cell",
    "subcell_slot",
    # Painted whole-cell lane: the picture.
    "final_paint_glyph",
    "final_paint_mask_glyphs",
    "final_paint_mask_state",
    "final_paint_atlas_mask_dimensions",
    "final_paint_expected_atlas_mask_hash",
    "final_paint_actual_mask_hash",
    "fg_rgb",
    "bg_rgb",
    "final_ink_fg",
    # Product identity lane: recorded, reported, never elected or drawn.
    "final_product_glyph",
    "final_paint_slot_colour_present",
    "final_paint_slot_fg_rgb",
    "final_paint_slot_bg_rgb",
)

# =============================================================================
# THE SENTINEL WHOLE-CELL PAINT, AND THE READABILITY FALLBACK
# =============================================================================
# OBSERVATION FIRST, AID SECOND. On this capture a subset of cells publish a
# whole-cell paint record of glyph 219 (CP437 FULL BLOCK) with FG (255,0,255)
# on BG (255,255,0). A full block stamped in magenta fills the entire cell with
# magenta, so those cells read as a solid magenta field in the picture. Measured
# on `.run/fl4359_tier_ab_baseline/SOL-baseline-r10/jitter_capture.jsonl`, frame
# 0 carries 283 such cells out of 8023, and they cluster on foliage/tree cells.
#
# THAT POPULATION IS ITSELF A RECORDED OBSERVATION ABOUT THE RENDERER. It is
# what the capture published; it is not a defect of this rasterizer and it is
# not repaired by drawing it differently. The FALLBACK below does not change,
# correct, explain or excuse it - it only lets a reader see what the per-quarter
# Product lane recorded UNDERNEATH the sentinel, because a screen of solid
# magenta hides every other cell fact in the same picture.
#
# The same cells' per-quarter Product records carry ordinary terrain colours
# (e.g. slot 1 fg (36,61,30) on bg (18,29,15)), so the Product lane has usable
# colour where the whole-cell paint lane does not.
#
# TWO MODES, AND WHY `none` IS THE DEFAULT
#   none          Draw exactly what the whole-cell paint lane published. The
#                 rendered PNGs and GIF are byte-identical to what this script
#                 produced before the option existed. This is the honest
#                 picture of the capture and stays the default.
#   product_slot  For a sentinel cell only, paint the cell from its per-quarter
#                 Product colours (`final_paint_slot_fg_rgb` /
#                 `final_paint_slot_bg_rgb`) taken from the lowest-numbered
#                 slot that is itself NOT sentinel-coloured. Every other cell is
#                 untouched, and the ELECTED GLYPH is never changed in either
#                 mode - election reads `final_paint_mask_glyphs` and the
#                 fallback touches colour only.
#
# In both modes the receipt records the sentinel population per frame and, in
# `product_slot`, the full list of substituted screen cells. A substituted
# picture is a READABILITY AID and is never evidence about renderer behaviour.
#
# NOT `final_paint_mask_state`. Every one of the 8023 cells on this capture
# reports `final_mask_digest_mismatch`, so that field does not discriminate the
# sentinel population and is not part of the test below.
SENTINEL_WHOLE_CELL_FG = [255, 0, 255]
SENTINEL_WHOLE_CELL_BG = [255, 255, 0]
SENTINEL_WHOLE_CELL_GLYPH = 219

SENTINEL_FALLBACK_MODES = ("none", "product_slot")

SENTINEL_FALLBACK_NOTE = (
    "SUBSTITUTION IS A READABILITY AID, NOT A CORRECTION. The sentinel "
    "whole-cell paint population (glyph 219 full block, FG (255,0,255) on BG "
    "(255,255,0)) is itself a RECORDED OBSERVATION about the renderer, "
    "published by the capture. Repainting those cells from their per-quarter "
    "Product colours only makes the surrounding cell facts visible; it does "
    "not change, correct or explain what the renderer published, and it is "
    "not a verified, passed, proven, fixed or accepted claim about renderer "
    "behaviour. Mode `none` draws the capture as published.")


def _is_sentinel_pair(fg, bg) -> bool:
    """True when an FG/BG pair is exactly the sentinel magenta-on-yellow pair.

    Exact equality on the raw 0..255 transport ints. A near-miss is a different
    colour and is left alone; guessing at 'close enough' magenta would silently
    repaint real terrain.
    """
    return (isinstance(fg, (list, tuple)) and list(fg[:3]) == SENTINEL_WHOLE_CELL_FG
            and isinstance(bg, (list, tuple))
            and list(bg[:3]) == SENTINEL_WHOLE_CELL_BG)


def is_sentinel_whole_cell_row(row: dict) -> bool:
    """True when this row's WHOLE-CELL paint record is the sentinel triple.

    All four subcell rows of one cell carry the same whole-cell paint record, so
    any one of them answers for the cell.
    """
    return (_is_sentinel_pair(row.get("fg_rgb"), row.get("bg_rgb"))
            and row.get("final_paint_glyph") == SENTINEL_WHOLE_CELL_GLYPH)


# =============================================================================
# RENDERING FAMILIES
# =============================================================================
# One capture, six renderings. A single composed picture cannot answer "did the
# GLYPH change or did the PAINT change", because a changed glyph and a changed
# FG both move ink. Splitting the two channels answers it by construction:
#
#   full      the composed cell exactly as this script drew it before these
#             families existed - BG paper, elected glyph stamped in FG ink.
#   bg_only   the whole cell filled with its published BG. Every glyph shape is
#             erased by construction, so anything that moves here moved in the
#             PAINT field and not in the mask election.
#   fg_only   FG ink inside the elected mask, nothing outside. Anything that
#             moves here moved in the ink or in the mask.
#   fg_highlight        the composed cell with the elected mask tinted, so the
#             mask the election actually chose is visible against its own paint.
#   glyph_change_highlight   the full composed cell dimmed, with every cell that
#             elected a different glyph than the previous frame tinted amber.
#   fg_glyph_change_highlight   the operator's original ask: cells that elected a
#             DIFFERENT glyph than they did in the previous frame, tinted on the
#             symmetric difference of the two bitmaps - exactly the ink that
#             appeared or vanished - with every holding cell dimmed behind them.
#
# NONE OF THESE IS A VERDICT. They are five ways of looking at the same captured
# rows. The per-frame counts published beside them are the measurement; the
# pictures are for a human, and are never the thing that decides anything.
RENDER_FAMILIES = ("full", "bg_only", "fg_only", "fg_highlight",
                   "glyph_change_highlight",
                   "fg_glyph_change_highlight")

# The families other than `full` are written to `<out>/<family>/`, so `full`
# keeps the exact paths - `<out>/frame_NN.png`, `<out>/receipt.json` - that the
# stitcher and every existing artifact of this script already use. Exported
# GIFs receive the applied FG/BG channel suffix; unsuffixed GIFs remain as
# byte-identical aliases for existing consumers.
FAMILY_RULES = {
    "full": (
        "BG paper filled from the published bg_rgb, then the elected whole-cell "
        "mask stamped in the published fg_rgb. Byte-identical to this script's "
        "output before the families existed."),
    "bg_only": (
        "Every published cell filled edge to edge with its published bg_rgb; no "
        "mask is stamped. Cells the capture published no record for keep the "
        "unpainted sentinel rather than going black, because black is a "
        "publishable BG and the sentinel is not."),
    "fg_only": (
        "The published fg_rgb inside the elected mask only; everything outside "
        "the mask, and every cell without a record, keeps the unpainted "
        "sentinel."),
    "fg_highlight": (
        "The `full` composition with the elected mask mixed "
        f"{_cdg.HIGHLIGHT_MIX:.2f} toward {list(_cdg.HIGHLIGHT_RGB.astype(int))}. "
        "Cells without a record are left as composed."),
    "glyph_change_highlight": (
        "The `full` composition dimmed to "
        f"{_cdg.GLYPH_HOLD_DIM:.2f}; every complete cell whose elected glyph "
        "differs from its glyph in the previous frame is mixed "
        f"{_cdg.GLYPH_CHANGE_MIX:.2f} toward "
        f"{list(_cdg.GLYPH_CHANGE_RGB.astype(int))}. Frame 0 has no predecessor "
        "and highlights nothing."),
    "fg_glyph_change_highlight": (
        "The `full` composition dimmed to "
        f"{_cdg.GLYPH_HOLD_DIM:.2f}, then, in each cell whose elected glyph "
        "differs from its own glyph in the previous frame, the SYMMETRIC "
        "DIFFERENCE of the two elected bitmaps mixed "
        f"{_cdg.GLYPH_CHANGE_MIX:.2f} toward "
        f"{list(_cdg.GLYPH_CHANGE_RGB.astype(int))}. Frame 0 has no predecessor "
        "and highlights nothing; it is still written, so the family stays "
        "frame-aligned with the other four."),
}

# THE TINT COLLIDES WITH THE DATA ON THIS CAPTURE, AND THAT IS STATED, NOT FIXED.
# fl4359_cell_dump_gifs.py picked magenta for `fg_highlight` on the reasoning
# that magenta does not occur in the terrain palette. On an FL-4359 JITTER
# capture that reasoning does not hold: magenta is also the unpainted sentinel
# AND the renderer publishes magenta whole-cell paint of its own (FL-4796). The
# constant is still imported rather than forked - two files drawing the same
# family in two different colours would be worse - and the collision is declared
# in the receipt so no one reads a magenta pixel in that family as a tint.
FAMILY_HIGHLIGHT_COLLISION_NOTE = (
    "In `fg_highlight` the tint is magenta (255,0,255), which is ALSO the "
    "unpainted-cell sentinel and is ALSO published as real whole-cell paint by "
    "this renderer (FL-4796). A magenta pixel in that family is therefore not "
    "self-identifying. Read the tinted set from cells_tinted / the `full` "
    "family beside it, never from the colour. `fg_glyph_change_highlight` uses "
    "amber (255,176,0) and has no such collision on this capture.")


# The GIF the deliverable names: first five frames, 60 ms each, looping forever.
GIF_FRAME_COUNT = 5
GIF_FRAME_MS = 60
GIF_NAME = "jitter_capture_5frames.gif"
MODE_POSTFIXES = (
    "FLAT_BG_FLAT_FG",
    "CONTINUOUS_BG_FLAT_FG",
    "FLAT_BG_CONTINUOUS_FG",
    "CONTINUOUS_BG_CONTINUOUS_FG",
)
MODE_POSTFIX_BY_SAMPLING = {
    ("flat", "flat"): "FLAT_BG_FLAT_FG",
    ("continuous", "flat"): "CONTINUOUS_BG_FLAT_FG",
    ("flat", "continuous"): "FLAT_BG_CONTINUOUS_FG",
    ("continuous", "continuous"): "CONTINUOUS_BG_CONTINUOUS_FG",
}
NATIVE_RECEIPT_NAME = "native_sweep_receipt.json"
NATIVE_RECEIPT_SCHEMA = "fl4359.native_sweep_receipt.v1"


def _mode_postfix_from_frame_records(frame_records: list[dict]) -> str | None:
    """Return the stable user-facing channel suffix from applied policy receipts.

    Legacy pair captures have no applied presentation policy and retain the
    historical canonical filenames. An actual-field capture must report one
    consistent applied policy across all frames; a mixed policy is refused so
    the exported filename cannot mislabel one frame sequence.
    """
    policies = [
        rec.get("actual_field", {}).get("applied_policy")
        for rec in frame_records
    ]
    applied = [policy for policy in policies if isinstance(policy, dict)
               and policy.get("status") == "applied"]
    if not applied:
        return None
    postfixes = {
        MODE_POSTFIX_BY_SAMPLING.get(
            (policy.get("bg_sampling"), policy.get("fg_sampling")))
        for policy in applied
    }
    if None in postfixes or len(postfixes) != 1 or len(applied) != len(policies):
        raise ActualFieldError(
            "captured frames do not carry one consistent applied FG/BG "
            "sampling policy; refusing a mode-labelled GIF")
    return next(iter(postfixes))


def _gif_name(base_name: str, mode_postfix: str | None) -> str:
    """Add a channel-explicit suffix while retaining the base role name."""
    if not mode_postfix:
        return base_name
    stem, suffix = base_name.rsplit(".", 1)
    return f"{stem}_{mode_postfix}.{suffix}"


def _write_canonical_alias(export_path: Path, canonical_path: Path) -> None:
    """Keep the old internal filename as a byte-identical compatibility alias."""
    if export_path.resolve() == canonical_path.resolve():
        return
    shutil.copyfile(export_path, canonical_path)


def _joined_native_provenance_eligible(native_witness, policy, field_sha) -> bool:
    """A native receipt can supply the observed display boundary for raw fields.

    Never change a pre-imageStore sidecar's late-overlay flag. Bind its five
    hashes to the independently captured viewport and recorded source/atlases.
    """
    provenance = (native_witness or {}).get("presentation_provenance")
    if not isinstance(provenance, dict):
        return False
    if provenance.get("applied_policy") != policy or provenance.get("field_sha256") != field_sha:
        raise ActualFieldError("native presentation provenance does not match the five field observations")
    source = provenance.get("source")
    atlas = provenance.get("atlas")
    if not _eligibility(source, "native.source") or not _eligibility(atlas, "native.atlas"):
        return False
    for page in atlas["sources"]:
        if _sha256_path(Path(page["path"])) != page["sha256"]:
            raise ActualFieldError("native presentation atlas snapshot hash mismatch")
    return True


# =============================================================================
# TRANSPORT
# =============================================================================
class TransportError(RuntimeError):
    """A refusal to read the stream, never a silent degrade to a partial frame."""


# v17 appends executed-vertex evidence to each sparse row. These columns are
# optional at the transport boundary so v16 artifacts remain readable with an
# explicit absent value; every pre-existing consumed column remains mandatory.
OPTIONAL_HISTORICAL_COLUMNS = frozenset({
    "probe_actual_vertex_witness_valid",
    "probe_actual_vertex_witness_available",
    "probe_actual_vertex_witness_enabled",
    "probe_raster_witness_enabled",
    "probe_actual_vertex_witness_surface",
    "probe_actual_vertex_witness_instance",
    "probe_actual_vertex_witness_primitive",
    "probe_actual_vertex_clip_corners",
})


def _column_index(fields: list, name: str) -> int:
    try:
        return fields.index(name)
    except ValueError as exc:
        raise TransportError(
            f"transport fields array has no column {name!r}; this rasterizer "
            f"reads list rows POSITIONALLY and cannot guess a substitute"
        ) from exc


def stream_frames(path: Path, max_frames: int | None = None,
                  *, columns=CONSUMED_COLUMNS):
    """Yield one `(frame_id, frame_trace, rows, raw_line_count)` per frame.

    Streamed: a 2.2 GB capture is never held whole, and each frame's rows are
    released before the next frame's header is read.

    `rows` are dicts carrying only CONSUMED_COLUMNS plus the frame's
    `frame_trace`, which the v16 transport publishes once per frame rather than
    per row. `_cdr._adapt_jitter_cell` reads pose, grid and cell size off that
    trace, so attaching the frame's own trace to each row is what lets the
    canonical adapter be reused verbatim.
    """
    fields: list | None = None
    picks: list[tuple[str, int]] = []
    frame_id: int | None = None
    trace: dict | None = None
    rows: list[dict] = []
    raw_count = 0
    emitted = 0

    with path.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            # Cheap discrimination before parsing: `{` is a control object,
            # `[` is a positional cell row. Only the control objects are rare.
            if raw[:1] == b"{":
                obj = json.loads(raw)
                kind = obj.get("row_type")
                if kind == SCHEMA_ROW_TYPE:
                    declared = obj.get("transport_schema")
                    if declared not in SUPPORTED_TRANSPORT_SCHEMAS:
                        raise TransportError(
                            f"unsupported transport_schema {declared!r}; "
                            f"supported: {list(SUPPORTED_TRANSPORT_SCHEMAS)}")
                    fields = obj.get("fields")
                    if not isinstance(fields, list) or not fields:
                        raise TransportError(
                            "schema row carries no usable `fields` array")
                    picks = []
                    for name in columns:
                        if name in fields:
                            picks.append((name, fields.index(name)))
                        elif name in OPTIONAL_HISTORICAL_COLUMNS:
                            picks.append((name, -1))
                        else:
                            # Preserve mutation rejection for every existing
                            # v16 transport contract column.
                            picks.append((name, _column_index(fields, name)))
                    continue
                if kind == FRAME_ROW_TYPE:
                    if frame_id is not None:
                        yield frame_id, trace, rows, raw_count
                        emitted += 1
                        if max_frames is not None and emitted >= max_frames:
                            return
                    frame_id = int(obj.get("frame_id", -1))
                    trace = obj.get("frame_trace") or {}
                    rows = []
                    raw_count = 0
                    continue
                # An unknown control object is counted by the caller through the
                # frame's row total staying short; it is never merged in.
                continue
            if fields is None:
                raise TransportError(
                    "a positional list row appeared before the `fields` row; "
                    "the column order is unknown and cannot be inferred")
            if frame_id is None:
                raise TransportError(
                    "a positional list row appeared before any frame header")
            values = json.loads(raw)
            raw_count += 1
            row = {name: (values[index] if index >= 0 else None)
                   for name, index in picks}
            row["frame_trace"] = trace
            rows.append(row)

    if frame_id is not None:
        yield frame_id, trace, rows, raw_count


def build_frame(rows: list[dict], sentinel_fallback: str = "none") -> dict:
    """Adapt the streamed rows into the canonical frame shape `rasterize` takes.

    This mirrors `_cdr.read_frame`'s accumulation exactly - including its
    last-writer-wins on the paint record and its accumulation of the per-subcell
    Product identity samples - but sources its rows from the columnar transport
    instead of one-object-per-line JSONL.

    `sentinel_fallback` selects what happens to a cell whose WHOLE-CELL paint
    record is the sentinel triple (see SENTINEL_FALLBACK_NOTE):
      `none`          leave it exactly as captured - the default, and the mode
                      whose images are byte-identical to this script's output
                      before the option existed;
      `product_slot`  repaint that cell's FG/BG from its per-quarter Product
                      colours. Colour only: the elected glyph is untouched.
    The sentinel population is COUNTED and LISTED in both modes, because the
    population is the observation and the repaint is only a readability aid.
    """
    if sentinel_fallback not in SENTINEL_FALLBACK_MODES:
        raise TransportError(
            f"unknown sentinel fallback mode {sentinel_fallback!r}; "
            f"supported: {list(SENTINEL_FALLBACK_MODES)}")

    cells: dict[tuple[int, int], dict] = {}
    frame = {"pose": {}, "box_w": 0, "box_h": 0,
             "declared_cols": 0, "declared_rows": 0, "capture_window": {}}
    cols = rows_max = 0
    accepted = 0
    skipped_row_types: dict[str, int] = {}
    # Sentinel bookkeeping is read off the RAW transport rows, where the
    # whole-cell `final_paint_glyph` and the 0..255 `fg_rgb`/`bg_rgb` still
    # exist under their own names. The canonical cell shape keeps neither.
    sentinel_cells: set[tuple[int, int]] = set()
    # Per cell: the slot samples that are usable as a substitute, in slot order.
    slot_candidates: dict[tuple[int, int], list[dict]] = {}

    for row in rows:
        kind = str(row.get("row_type"))
        if kind != CELL_ROW_TYPE:
            skipped_row_types[kind] = skipped_row_types.get(kind, 0) + 1
            continue
        canonical = _cdr._adapt_jitter_cell(row, frame)
        if canonical is None:
            continue
        accepted += 1
        cell = canonical["screen_cell"]
        x, y = int(cell["x"]), int(cell["y"])
        cols, rows_max = max(cols, x + 1), max(rows_max, y + 1)

        if is_sentinel_whole_cell_row(row):
            sentinel_cells.add((y, x))
        # A slot is a substitution candidate only when the capture says the slot
        # carried colour AND that colour is not itself the sentinel pair. A
        # sentinel-coloured slot substituted for a sentinel cell would repaint
        # magenta with magenta and report a substitution that changed nothing.
        slot_fg = row.get("final_paint_slot_fg_rgb")
        slot_bg = row.get("final_paint_slot_bg_rgb")
        if (row.get("final_paint_slot_colour_present")
                and isinstance(slot_fg, (list, tuple))
                and isinstance(slot_bg, (list, tuple))
                and not _is_sentinel_pair(slot_fg, slot_bg)):
            slot = row.get("subcell_slot")
            if isinstance(slot, int) and 0 <= slot <= 3:
                slot_candidates.setdefault((y, x), []).append({
                    "slot": slot,
                    "fg_rgb": list(slot_fg[:3]),
                    "bg_rgb": list(slot_bg[:3]),
                })

        previous = cells.get((y, x))
        if previous is not None:
            # The four subcell rows of one cell carry the SAME b43 paint record,
            # so overwriting it is a rewrite of identical paint facts. Only the
            # Product identity sample differs per slot, so those accumulate.
            canonical["product_identity_quarter_samples"] = (
                previous.get("product_identity_quarter_samples", [])
                + canonical.get("product_identity_quarter_samples", []))
        cells[(y, x)] = canonical

    # ---- sentinel fallback -------------------------------------------------
    # Applied AFTER accumulation, so the substitution sees every one of a cell's
    # four slot samples and can pick the lowest-numbered usable one
    # deterministically, rather than depending on row order.
    substitutions: list[dict] = []
    unsubstituted: list[list[int]] = []
    for (y, x) in sorted(sentinel_cells):
        candidates = sorted(slot_candidates.get((y, x), []),
                            key=lambda record: record["slot"])
        if not candidates:
            # Declared, not silently skipped: a sentinel cell with no usable
            # Product colour has nothing to substitute and stays as captured.
            unsubstituted.append([x, y])
            continue
        chosen = candidates[0]
        substitutions.append({
            "screen_cell": [x, y],
            "from_fg_rgb": list(SENTINEL_WHOLE_CELL_FG),
            "from_bg_rgb": list(SENTINEL_WHOLE_CELL_BG),
            "from_whole_cell_glyph": SENTINEL_WHOLE_CELL_GLYPH,
            "slot": chosen["slot"],
            "to_fg_rgb": chosen["fg_rgb"],
            "to_bg_rgb": chosen["bg_rgb"],
            "usable_slot_count": len(candidates),
        })
        if sentinel_fallback == "product_slot":
            # Colour only. `final_mask_measurement.glyph_ids` is untouched, so
            # `elect_cell_glyph` still elects the captured whole-cell mask.
            ink = cells[(y, x)]["final_glyph_ink"]
            ink["fg_rgb"] = _cdr._norm255(chosen["fg_rgb"])
            ink["bg_rgb"] = _cdr._norm255(chosen["bg_rgb"])
            ink["sentinel_fallback_applied"] = True
    if sentinel_fallback != "product_slot":
        # In `none` the candidates are still resolved and reported, but nothing
        # is repainted, so nothing is claimed as substituted.
        applied = []
    else:
        applied = substitutions

    # The capture declares its own grid. A declared grid wins over the observed
    # maximum so a frame that never sampled the last column cannot silently
    # shrink the image.
    cols = max(cols, frame["declared_cols"])
    rows_max = max(rows_max, frame["declared_rows"])
    return {"cells": cells, "cols": cols, "rows": rows_max,
            "box_w": frame["box_w"], "box_h": frame["box_h"],
            "pose": frame["pose"],
            "row_type": CELL_ROW_TYPE,
            "accepted_rows": accepted,
            "capture_window": frame["capture_window"],
            "grid_source": ("capture_declared_semantic_ansi_size"
                            if frame["declared_cols"]
                            else "observed_max_screen_cell_index"),
            "skipped_row_types": skipped_row_types,
            "sentinel_fallback_mode": sentinel_fallback,
            "sentinel_cells": [[x, y] for (y, x) in sorted(sentinel_cells)],
            "sentinel_cell_count": len(sentinel_cells),
            "sentinel_substitution_candidates": substitutions,
            "sentinel_cells_substituted": applied,
            "sentinel_cells_without_usable_slot": unsubstituted}


# =============================================================================
# DESCRIPTION OF THE DATA (not a verdict)
# =============================================================================
def _colour_key(fg: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Per-cell FG/BG as a comparable integer key; NaN (absent) becomes -1."""
    stacked = np.concatenate([fg, bg], axis=-1)
    filled = np.where(np.isnan(stacked), -1.0, np.rint(stacked))
    return filled.astype(np.int32)


def frame_delta(previous: dict, current: dict) -> dict:
    """Count cells whose elected glyph or whose FG/BG differs from the previous
    frame.

    THIS IS A DESCRIPTION OF THE CAPTURED DATA. It is not a shimmer verdict, not
    a jitter measurement, and not an acceptance claim. It answers exactly one
    question: do two consecutive captured frames carry different cell facts, and
    for how many cells.

    A cell absent from either frame has no defined difference and is excluded
    (`undefined_pair_cells`) rather than counted as changed.
    """
    pair = previous["present"] & current["present"]
    glyph_changed = (previous["cell_glyph"] != current["cell_glyph"]) & pair
    fg_changed = np.any(previous["fg"] != current["fg"], axis=-1) & pair
    bg_changed = np.any(previous["bg"] != current["bg"], axis=-1) & pair
    colour_changed = np.any(previous["colour_key"] != current["colour_key"],
                            axis=-1) & pair
    any_changed = glyph_changed | colour_changed
    return {
        "compared_cells": int(pair.sum()),
        "undefined_pair_cells": int((~pair).sum()),
        "cells_with_glyph_change": int(glyph_changed.sum()),
        "cells_with_fg_change": int(fg_changed.sum()),
        "cells_with_bg_change": int(bg_changed.sum()),
        "cells_with_colour_change": int(colour_changed.sum()),
        "cells_with_any_change": int(any_changed.sum()),
    }


# =============================================================================
# MAIN
# =============================================================================
def _camera(trace: dict) -> dict | None:
    cam = (trace or {}).get("camera_world")
    if not isinstance(cam, dict):
        return None
    return {"x": cam.get("x"), "y": cam.get("y"), "z": cam.get("z")}


def compose_family(raster: dict, family: str,
                   previous: dict | None) -> tuple[np.ndarray, dict]:
    """Draw one rendering family from an already-rasterized frame.

    Takes the arrays `fl4359_cell_dump_raster.rasterize` already produced - the
    per-cell elected `mask`, `fg`, `bg`, `cell_glyph` and `present` planes - so
    no family re-elects a glyph or re-reads the transport. Every family is
    therefore a different VIEW of one election, never a second election.

    `previous` is the previous frame's raster, needed by the two glyph-change
    families; None on frame 0.

    Returns the image and a stats dict. The stats are the measurement; the image
    is the illustration.
    """
    if family not in RENDER_FAMILIES:
        raise TransportError(
            f"unknown rendering family {family!r}; supported: "
            f"{list(RENDER_FAMILIES)}")
    present = raster["present"]
    rows, cols = present.shape
    box = raster["box"]
    stats = {"family": family, "rule": FAMILY_RULES[family],
             "cells_published": int(present.sum()),
             "cells_without_record": int((~present).sum())}

    if family == "full":
        return raster["image"].copy(), stats

    sentinel = np.array(_cdr.UNPAINTED_SENTINEL_RGB, dtype=np.float32)
    if family in ("bg_only", "fg_only"):
        img = np.empty((rows * box, cols * box, 3), dtype=np.float32)
        img[:] = sentinel
    else:
        img = raster["image"].astype(np.float32).copy()

    changed = None
    glyph_change_family = family in (
        "glyph_change_highlight", "fg_glyph_change_highlight")
    if glyph_change_family:
        if previous is None:
            # An all-false plane, not a skipped frame: the family must stay
            # frame-aligned with the other four.
            changed = np.zeros((rows, cols), dtype=bool)
        else:
            pair = present & previous["present"]
            changed = (raster["cell_glyph"] != previous["cell_glyph"]) & pair
            stats["cells_with_undefined_pair"] = int((~pair).sum())
        # Dimming the whole plane first - including the cells without a record -
        # is what makes the changed set read as a figure against a ground.
        img *= _cdg.GLYPH_HOLD_DIM

    tinted = 0
    for r in range(rows):
        y0 = r * box
        for c in range(cols):
            x0 = c * box
            published = bool(present[r, c])
            if not published and not glyph_change_family:
                continue
            cell = img[y0:y0 + box, x0:x0 + box]
            if family == "bg_only":
                cell[:] = np.nan_to_num(raster["bg"][r, c])
            elif family == "fg_only":
                cell[raster["mask"][r, c]] = np.nan_to_num(raster["fg"][r, c])
            elif family == "fg_highlight":
                mask = raster["mask"][r, c]
                if not mask.any():
                    continue
                cell[mask] = (cell[mask] * (1.0 - _cdg.HIGHLIGHT_MIX)
                              + _cdg.HIGHLIGHT_RGB * _cdg.HIGHLIGHT_MIX)
                tinted += 1
            elif family == "glyph_change_highlight":
                if not published or not changed[r, c]:
                    continue
                cell[:] = (cell * (1.0 - _cdg.GLYPH_CHANGE_MIX)
                           + _cdg.GLYPH_CHANGE_RGB * _cdg.GLYPH_CHANGE_MIX)
                tinted += 1
            else:  # fg_glyph_change_highlight
                if not published or not changed[r, c]:
                    continue
                delta = raster["mask"][r, c] ^ previous["mask"][r, c]
                if not delta.any():
                    # The glyph id changed but the two bitmaps are equal, so
                    # there is no ink to tint. Counted, not drawn.
                    stats["cells_changed_with_identical_bitmaps"] = (
                        stats.get("cells_changed_with_identical_bitmaps", 0) + 1)
                    continue
                cell[delta] = (cell[delta] * (1.0 - _cdg.GLYPH_CHANGE_MIX)
                               + _cdg.GLYPH_CHANGE_RGB * _cdg.GLYPH_CHANGE_MIX)
                tinted += 1

    # WHY fg_only IS MOSTLY GROUND, STATED AS A NUMBER.
    # The elected masks cover roughly a ninth of the grid, so `fg_only` is
    # overwhelmingly ground by construction and that is not a fault in the
    # picture. The ground stays the unpainted sentinel rather than black,
    # because this capture publishes ink as dark as the darkest value reported
    # below - a black ground would swallow it. Both facts are published so the
    # composition is explained by the receipt and never by looking at it.
    if family == "fg_only":
        flat = img.reshape(-1, 3)
        is_ground = np.all(flat == sentinel, axis=-1)
        ink = flat[~is_ground]
        stats["ink_pixel_fraction"] = float(round(1.0 - is_ground.mean(), 6))
        stats["ground_pixel_fraction"] = float(round(is_ground.mean(), 6))
        stats["ground_rgb"] = list(_cdr.UNPAINTED_SENTINEL_RGB)
        if ink.size:
            darkest = ink[ink.max(axis=-1).argmin()]
            stats["darkest_published_ink_rgb"] = [int(v) for v in darkest]
            stats["ink_pixels_darker_than_16"] = int(
                (ink.max(axis=-1) <= 16).sum())
        stats["ground_choice_note"] = (
            "The ground is the unpainted sentinel, not black: this capture "
            "publishes ink down to the darkest value above, so a black ground "
            "would make real ink indistinguishable from empty. The ground "
            "fraction is a property of the mask coverage, not a defect.")
    elif family == "bg_only":
        published_bg = raster["bg"][present]
        if published_bg.size:
            stats["distinct_published_bg_colours"] = int(len(np.unique(
                np.nan_to_num(published_bg).round().astype(np.int32), axis=0)))

    if family == "fg_highlight":
        stats["cells_tinted"] = tinted
        stats["highlight_colour_collision"] = FAMILY_HIGHLIGHT_COLLISION_NOTE
    elif glyph_change_family:
        stats["cells_tinted"] = tinted
        stats["cells_with_glyph_change"] = (
            0 if changed is None else int(changed.sum()))
        stats["has_predecessor"] = previous is not None
    return np.clip(img, 0, 255).astype(np.uint8), stats


def _actual_rgb_u8(value: np.ndarray) -> np.ndarray:
    """Convert a validated unit-RGB field to the rasterizer's byte domain."""
    return np.clip(np.asarray(value, dtype=np.float32) * 255.0, 0.0, 255.0)


def _actual_field_delta(previous: dict | None, current: dict) -> dict | None:
    """Describe changes in the actual fields without inventing missing pixels."""
    if previous is None:
        return None
    if current["fg_rgb"].shape != previous["fg_rgb"].shape:
        raise ActualFieldError("actual-field frame dimensions changed within one capture")
    pair = np.ones(current["effective_coverage"].shape, dtype=bool)
    fg_changed = np.any(current["fg_rgb"] != previous["fg_rgb"], axis=-1)
    bg_changed = np.any(current["bg_rgb"] != previous["bg_rgb"], axis=-1)
    native_changed = current["native_intensity"] != previous["native_intensity"]
    effective_changed = current["effective_coverage"] != previous["effective_coverage"]
    final_changed = np.any(current["final_rgb"] != previous["final_rgb"], axis=-1)
    return {
        "field_pixels_compared": int(pair.sum()),
        "field_pixels_undefined": 0,
        "fg_pixels_changed": int(fg_changed.sum()),
        "bg_pixels_changed": int(bg_changed.sum()),
        "native_intensity_pixels_changed": int(native_changed.sum()),
        "effective_coverage_pixels_changed": int(effective_changed.sum()),
        "final_pixels_changed": int(final_changed.sum()),
    }


def _actual_cell_identity_snapshot(frame: dict, cell_px: int) -> dict:
    """Join transport glyph identity without replaying an atlas page.

    Native final-paint fields already provide the colour/coverage image.  The
    only cell-level fact the highlight families still need is the compositor's
    elected glyph, which is carried by ``final_mask_measurement`` in the cell
    transport.  Keep that join separate from ``_cdr.rasterize`` so actual-field
    captures do not load or resample an atlas merely to obtain glyph IDs.
    """
    rows, cols = frame["rows"], frame["cols"]
    present = np.zeros((rows, cols), dtype=bool)
    cell_glyph = np.full((rows, cols), -1, dtype=np.int32)
    fg = np.full((rows, cols, 3), np.nan, dtype=np.float32)
    bg = np.full((rows, cols, 3), np.nan, dtype=np.float32)
    for (row_index, col_index), row in frame["cells"].items():
        present[row_index, col_index] = True
        measurement = row.get("final_mask_measurement") or {}
        quarters = row.get("final_product_b55_quarters") or []
        glyph, _, _ = _cdr.elect_cell_glyph(
            quarters, measurement, row.get("cell_presentation"))
        cell_glyph[row_index, col_index] = glyph
        ink = row.get("final_glyph_ink") or {}
        fg[row_index, col_index] = _cdr._rgb(ink.get("fg_rgb"))
        bg[row_index, col_index] = _cdr._rgb(ink.get("bg_rgb"))
    return {
        # These legacy replay planes are deliberately absent.  Actual-field
        # family composition never consumes them; keeping ``mask`` out of the
        # two-frame window avoids an atlas-sized allocation per native frame.
        "mask": None,
        "cell_glyph": cell_glyph,
        "present": present,
        "fg": fg,
        "bg": bg,
        "stamped": np.zeros((rows, cols), dtype=bool),
        "box": int(cell_px),
    }


def _actual_changed_pixel_plane(current: dict, previous: dict | None,
                                cell_px: tuple[int, int]) -> tuple[np.ndarray, np.ndarray | None]:
    """Expand cell glyph transitions to pixels using the declared cell layout."""
    height, width = current["effective_coverage"].shape
    if previous is None:
        return np.zeros((height, width), dtype=bool), None
    current_glyphs = current["cell_glyph_id"]
    previous_glyphs = previous["cell_glyph_id"]
    changed_cells = ((current_glyphs != previous_glyphs)
                     & (current_glyphs >= 0) & (previous_glyphs >= 0))
    cell_w, cell_h = cell_px
    rows, cols = changed_cells.shape
    y_cells = np.minimum(np.arange(height) // cell_h, rows - 1)
    x_cells = np.minimum(np.arange(width) // cell_w, cols - 1)
    changed = changed_cells[y_cells[:, None], x_cells[None, :]]
    return changed, changed_cells


def compose_actual_family(actual: dict, family: str, previous: dict | None,
                          cell_px: tuple[int, int]) -> tuple[np.ndarray, dict]:
    """Compose a view from the observed final-paint FG/BG fields.

    This is the only actual-field adapter.  The field arrays are already the
    compositor's output, so this function applies no world sampling, glyph
    election, atlas replay, interpolation, or fallback.  final_rgb is used
    for the full view because a downstream overlay or residual may make the
    effective-coverage mix incomplete.
    """
    if family not in RENDER_FAMILIES:
        raise ActualFieldError(
            f"unknown rendering family {family!r}; supported: {list(RENDER_FAMILIES)}")
    fg = _actual_rgb_u8(actual["fg_rgb"])
    bg = _actual_rgb_u8(actual["bg_rgb"])
    final = _actual_rgb_u8(actual["final_rgb"])
    native = np.asarray(actual["native_intensity"], dtype=np.float32)
    effective = np.asarray(actual["effective_coverage"], dtype=np.float32)
    sentinel = np.asarray(_cdr.UNPAINTED_SENTINEL_RGB, dtype=np.float32)
    height, width = effective.shape
    stats = {
        "family": family,
        "rule": FAMILY_RULES[family],
        "actual_field": True,
        "evidence_kind": "actual_final_paint_field",
        "field_source": ACTUAL_FIELD_SCHEMA,
        "final_boundary": "pre_image_store",
        "base_formula": "mix(bg_rgb,fg_rgb,effective_coverage)",
        "native_coverage_key": "native_intensity",
        "effective_coverage_key": "effective_coverage",
        "pixels_total": int(effective.size),
        "pixels_valid": int(effective.size),
        "pixels_invalid": 0,
        "field_complete": True,
        "presentation_mode_eligible": bool(actual.get("presentation_mode_eligible", False)),
    }
    stats["rule"] = {
        "full": "observed final_rgb before imageStore",
        "bg_only": "observed bg_rgb field edge to edge",
        "fg_only": (
            "observed fg_rgb weighted by effective_coverage over the "
            "unpainted sentinel; native_intensity remains a separate mask field"
        ),
        "fg_highlight": (
            "observed final_rgb with native_intensity-selected pixels tinted"
        ),
        "glyph_change_highlight": (
            "observed final_rgb dimmed and changed-cell pixels tinted from "
            "cell transport glyph identity"
        ),
        "fg_glyph_change_highlight": (
            "observed final_rgb dimmed and native-intensity changes tinted "
            "inside changed-cell pixels"
        ),
    }[family]
    if family == "full":
        image = final.copy()
    elif family == "bg_only":
        image = bg.copy()
    elif family == "fg_only":
        # This is a view of the observed FG channel, retaining the effective
        # pin.  The native intensity is not substituted for effective coverage.
        effective_rgb = effective[..., None]
        image = fg * effective_rgb + sentinel * (1.0 - effective_rgb)
    else:
        image = final.copy()

    changed_pixels, changed_cells = _actual_changed_pixel_plane(
        actual, previous, cell_px)
    if family in ("glyph_change_highlight", "fg_glyph_change_highlight"):
        image *= _cdg.GLYPH_HOLD_DIM
        stats["has_predecessor"] = previous is not None
        stats["pixels_with_glyph_change"] = int(changed_pixels.sum())
        stats["cells_with_glyph_change"] = (
            0 if changed_cells is None else int(changed_cells.sum()))
        if family == "glyph_change_highlight":
            image[changed_pixels] = (
                image[changed_pixels] * (1.0 - _cdg.GLYPH_CHANGE_MIX)
                + _cdg.GLYPH_CHANGE_RGB * _cdg.GLYPH_CHANGE_MIX)
        elif previous is not None:
            delta = ((np.abs(native - previous["native_intensity"]) > 1e-7)
                     & changed_pixels)
            stats["pixels_with_mask_delta"] = int(delta.sum())
            image[delta] = (
                image[delta] * (1.0 - _cdg.GLYPH_CHANGE_MIX)
                + _cdg.GLYPH_CHANGE_RGB * _cdg.GLYPH_CHANGE_MIX)
    elif family == "fg_highlight":
        # Native coverage identifies the selected atlas mask. This tint is a
        # display aid; it does not turn fractional coverage into a binary mask.
        highlight = (native > 0.0)
        image[highlight] = (
            image[highlight] * (1.0 - _cdg.HIGHLIGHT_MIX)
            + _cdg.HIGHLIGHT_RGB * _cdg.HIGHLIGHT_MIX)
        stats["pixels_tinted"] = int(highlight.sum())

    # Report the residual between the channel formula and the actual displayed
    # final field.  A nonzero residual is expected for downstream overlays and
    # must remain visible in the receipt rather than being silently discarded.
    base = bg * (1.0 - effective[..., None]) + fg * effective[..., None]
    residual = np.any(np.abs(final - base) > (1.0 / 255.0), axis=-1)
    stats["base_mix_residual_pixels"] = int(residual.sum())
    stats["base_mix_residual_complete"] = bool(actual.get("composite_eligible", False))
    stats["residual_accounting"] = (
        "final_rgb is observed pre-imageStore compositor output; residual is "
        "reported and never backfilled with the base mix")
    return np.clip(image, 0, 255).astype(np.uint8), stats


# PILLOW COLLAPSES BYTE-IDENTICAL CONSECUTIVE FRAMES, AND CANNOT BE ASKED NOT TO.
# GifImagePlugin._write_multiple_frames drops a frame whose delta bbox is empty
# and folds its duration into the frame before it (Pillow 12 removed the older
# disposal-differs escape). Total playback time survives; the GIF's frame COUNT
# does not. On an FL-4359 rendering "how many frames does this animation
# contain" is itself reported, so the count is never asserted from the input
# list - it is always read back off the written file and published as observed.
#
# In practice the stitcher's caption band, which names the arm, the frame index
# and that frame's captured camera position, makes every panel distinct by
# construction, so the collapse does not reach a stitched GIF. The direct writer
# below has no caption band and therefore does collapse when two rasterized
# frames are byte-identical; when that happens the receipt says so rather than
# claiming five frames the file does not hold.
def _write_gif_directly(png_paths: list[Path], out_path: Path,
                        frame_ms: int) -> dict:
    """Fallback GIF writer, used only when the stitcher does not fit."""
    from fl4359_stitch_sweep_gif import _write_gif

    images = [Image.open(p).convert("RGB") for p in png_paths]
    _write_gif(images, out_path, frame_ms)
    return {"writer": "pillow_direct",
            "palette": "shared_sequence_no_dither",
            "path": str(out_path),
            "frames_submitted": len(images),
            "frame_collapse_note": (
                "Pillow merges byte-identical consecutive frames; the written "
                "frame count is read back below, not assumed")}


def _presentation_artifact_manifest(
    args: argparse.Namespace,
    frame_records: list[dict],
    gif_info: dict,
    family_gifs: dict[str, dict],
    mode_postfix: str | None,
) -> dict:
    """Name the seven same-sequence artifacts for the runner's strict gate.

    The native GIF is owned by the headed driver and lives beside RASTER. The
    six derived roles are owned by this consumer. Missing native output or
    missing sidecar provenance is represented as a legacy diagnostic manifest;
    unrelated existing GIFs are never counted.
    """
    frame_ids = [int(rec["frame_id"]) for rec in frame_records]
    canonical_five = (
        len(frame_records) == GIF_FRAME_COUNT
        and frame_ids == list(range(GIF_FRAME_COUNT))
    )
    observed = canonical_five and all(
        rec.get("actual_field", {}).get("status") == "observed"
        for rec in frame_records
    )
    statuses = [rec.get("actual_field", {}) for rec in frame_records]
    field_sha = [
        str(status.get("sha256"))
        for status in statuses
        if status.get("status") == "observed"
    ]
    capture_ids = {
        status.get("capture_id")
        for status in statuses
        if status.get("status") == "observed"
    }
    profiles = {
        status.get("profile")
        for status in statuses
        if status.get("status") == "observed"
    }
    policies = [
        status.get("applied_policy", {})
        for status in statuses
        if status.get("status") == "observed"
    ]
    policy = policies[0] if policies and all(item == policies[0] for item in policies) else {}
    capture_id = next(iter(capture_ids), "") if len(capture_ids) == 1 else ""
    profile_id = next(iter(profiles), "") if len(profiles) == 1 else ""
    poses = [rec.get("camera_world") for rec in frame_records]
    artifacts: list[dict] = []

    native_path = args.out.parent / "sweep_5frames.gif"

    def native_binding() -> tuple[bool, str, dict[str, object] | None]:
        """Accept a native GIF only with a same-capture identity receipt."""
        if not native_path.is_file():
            return False, "native GIF is missing", None
        receipt_path = args.out.parent / NATIVE_RECEIPT_NAME
        if not receipt_path.is_file():
            return False, f"missing {NATIVE_RECEIPT_NAME}", None
        try:
            native_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False, "native receipt is unreadable", None
        if not isinstance(native_receipt, dict):
            return False, "native receipt is not an object", None
        if native_receipt.get("schema") != NATIVE_RECEIPT_SCHEMA:
            return False, "native receipt schema mismatch", None
        if native_receipt.get("status") != "available":
            return False, "native receipt status is not available", None
        if native_receipt.get("owner") != "same_frame_viewport_capture":
            return False, "native receipt owner mismatch", None
        if native_receipt.get("capture_id") != capture_id:
            return False, "native receipt capture_id mismatch", None
        if native_receipt.get("frame_ids") != frame_ids:
            return False, "native receipt frame_ids mismatch", None
        if native_receipt.get("poses") != poses:
            return False, "native receipt poses mismatch", None
        if native_receipt.get("capture_sha256") != _sha256_path(args.capture):
            return False, "native receipt capture SHA mismatch", None
        receipt_path_value = native_receipt.get("path")
        if not isinstance(receipt_path_value, str) or not receipt_path_value:
            return False, "native receipt GIF path is missing", None
        receipt_gif_path = Path(receipt_path_value)
        if not receipt_gif_path.is_absolute():
            receipt_gif_path = args.out.parent / receipt_gif_path
        if receipt_gif_path.resolve() != native_path.resolve():
            return False, "native receipt GIF path mismatch", None
        if native_receipt.get("sha256") != _sha256_path(native_path):
            return False, "native receipt GIF SHA mismatch", None
        return True, "same-capture native receipt bound", {
            **native_receipt,
            "artifact_path": str(native_path),
        }

    def add(role: str, path_value: object, *, read_back: dict | None = None) -> None:
        if not isinstance(path_value, str) or not path_value:
            return
        path = Path(path_value)
        if not path.is_file():
            return
        facts = read_back or {}
        artifacts.append({
            "role": role,
            "path": str(path),
            "sha256": _sha256_path(path),
            "bytes": path.stat().st_size,
            "frame_count": facts.get("n_frames", 0),
            "dimensions": facts.get("size", []),
            "frame_ids": frame_ids,
            "poses": poses,
            "capture_id": capture_id,
            "profile_id": profile_id,
            "mode_id": policy.get("mode", "unavailable"),
            "mode_postfix": mode_postfix,
            "field_sha256": field_sha,
        })

    native_ok, native_reason, native_witness = native_binding()
    if native_ok:
        with Image.open(native_path) as native:
            add("native", str(native_path), read_back={
                "n_frames": getattr(native, "n_frames", 1),
                "size": list(native.size),
            })
    add("full", gif_info.get("path"), read_back=gif_info.get("read_back"))
    for name in RENDER_FAMILIES:
        if name == "full":
            continue
        info = family_gifs.get(name, {})
        add(name, info.get("path"), read_back=info.get("read_back"))
    arm = {
        "capture_id": capture_id,
        "frame_ids": frame_ids,
        "poses": poses,
        "profile_id": profile_id,
        "mode_id": policy.get("mode", "unavailable"),
        "mode_postfix": mode_postfix,
        "applied_policy": policy,
        "field_observations_mode_eligible": (all(
            status.get("presentation_mode_eligible") is True for status in statuses
        ) and len(statuses) == GIF_FRAME_COUNT) or (
            observed and native_ok and _joined_native_provenance_eligible(native_witness, policy, field_sha)),
        "field_sha256": field_sha,
        "artifacts": artifacts,
        "native_viewport_source": (
            native_witness if native_witness is not None else {
                "status": "unavailable",
                "owner": "same_frame_viewport_capture",
                "reason": native_reason,
            }
        ),
    }
    return {
        "schema": "fl4359.presentation_artifact_manifest.v1",
        "eligibility": "actual_field" if observed else "legacy_pair_diagnostic",
        "eligibility_reason": (
            "canonical five frame actual sidecars"
            if observed else
            "requires exactly frame IDs 0..4 with an observed sidecar on every frame"
        ),
        "arm_kind": "single_arm",
        "arms": {"single": arm},
        "comparisons": [],
        "source": {
            "actual_field_schema": ACTUAL_FIELD_SCHEMA,
            "same_five_frame_identity": observed,
            "native_path": str(native_path),
            "native_binding": {
                "admitted": native_ok,
                "receipt": str(args.out.parent / NATIVE_RECEIPT_NAME),
                "reason": native_reason,
            },
            "derived_from": "per_frame_trace.final_paint_field",
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=("Rasterize an FL-4359 jitter capture (transport v16) into "
                     "per-frame PNGs and a five-frame GIF. DIAGNOSTIC ONLY."))
    ap.add_argument("--capture", required=True, type=Path,
                    help="jitter_capture.jsonl written by the FL-4359 driver")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cell-px", type=int, default=32,
                    help="native cell height; square width unless --cell-width is supplied")
    ap.add_argument("--cell-width", type=int, help="native cell width for actual-field tall profiles")
    ap.add_argument("--frames", type=int, default=0,
                    help="rasterize at most N frames; 0 = every frame")
    ap.add_argument("--arm-label", required=True)
    ap.add_argument("--mode-postfix", choices=MODE_POSTFIXES,
                    help=("explicit channel suffix fallback for a legacy "
                          "capture without an applied policy receipt; an "
                          "observed applied policy must agree"))
    ap.add_argument("--sentinel-fallback", choices=SENTINEL_FALLBACK_MODES,
                    default="none",
                    help=("what to draw for a cell whose whole-cell paint is "
                          "the sentinel triple (glyph 219, FG (255,0,255) on "
                          "BG (255,255,0)). `none` (default) draws it as "
                          "captured and is byte-identical to this script's "
                          "output before the option existed; `product_slot` "
                          "repaints it from its per-quarter Product colours as "
                          "a READABILITY AID only. The sentinel population is "
                          "counted and listed in the receipt in both modes."))
    ap.add_argument("--actual-field-required", action="store_true",
                    help=("require every captured frame trace to carry a "
                          "validated final_paint_field sidecar. Without this "
                          "flag, a legacy cell-pair capture is rendered as a "
                          "clearly diagnostic fallback."))
    ap.add_argument("--render-family", action="append",
                    choices=RENDER_FAMILIES, default=None,
                    help=("repeatable; default is every family. `full` is "
                          "written to <out>/frame_NN.png as before; every "
                          "other family is written to <out>/<family>/ with its "
                          "own five-frame GIF. Applied FG/BG policy adds the "
                          "explicit channel suffix and preserves the old "
                          "filename as an alias. See RENDER_FAMILIES."))
    args = ap.parse_args(argv)

    # `full` is always drawn: every other family is composed from its raster,
    # and the receipt/stitcher contract expects <out>/frame_NN.png to exist.
    families = list(dict.fromkeys(["full"] + list(
        args.render_family if args.render_family else RENDER_FAMILIES)))

    if not args.capture.is_file():
        print(f"ABORT: capture {args.capture} missing", file=sys.stderr)
        return 1
    if args.cell_px < 1 or (args.cell_width is not None and args.cell_width < 1):
        print("ABORT: --cell-px must be >= 1", file=sys.stderr)
        return 1
    limit = args.frames if args.frames and args.frames > 0 else None
    args.out.mkdir(parents=True, exist_ok=True)

    cache = _cdr.MaskCache()
    grid: tuple[int, int] | None = None
    frame_records: list[dict] = []
    png_paths: list[Path] = []
    previous_state: dict | None = None
    actual_reader = ActualFieldReader(args.capture)
    previous_actual: dict | None = None
    actual_field_seen = False
    legacy_field_seen = False
    # `fg_glyph_change_highlight` needs the previous frame's elected bitmaps, so
    # one raster is carried forward. Only the planes that family reads are kept;
    # holding the whole raster would carry the composed image too.
    previous_raster: dict | None = None
    family_pngs: dict[str, list[Path]] = {name: [] for name in families}
    for name in families:
        if name != "full":
            (args.out / name).mkdir(parents=True, exist_ok=True)

    try:
        stream = stream_frames(args.capture, limit)
        for index, (frame_id, trace, rows, raw_count) in enumerate(stream):
            frame = build_frame(rows, args.sentinel_fallback)
            shape = (frame["rows"], frame["cols"])
            if grid is None:
                grid = shape
            elif shape != grid:
                # Two frames of different grids are not one sequence; comparing
                # them cell-for-cell would align different screen positions.
                print(f"ABORT: frame {frame_id} is {shape[1]}x{shape[0]}, the "
                      f"first frame was {grid[1]}x{grid[0]}", file=sys.stderr)
                return 1
            family_stats: dict[str, dict] = {}
            actual: dict | None = None
            actual_frame_status: dict[str, object]
            field_trace_value = (
                trace.get("final_paint_field")
                if isinstance(trace, dict) else None
            )
            if (
                isinstance(field_trace_value, dict)
                and field_trace_value.get("status") == "not_requested"
            ):
                # The producer explicitly states that this capture did not
                # arm the native field observer. Preserve the legacy pair
                # output as a diagnostic and do not silently treat it as an
                # empty or flat actual field.
                legacy_field_seen = True
                if args.cell_width is not None and args.cell_width != args.cell_px:
                    raise ActualFieldError("tall profiles require actual-field capture")
                actual_frame_status = actual_field_absent_status(trace or {})
                actual_frame_status["reason"] = (
                    "producer final_paint_field status is not_requested"
                )
                if args.actual_field_required:
                    raise ActualFieldError(
                        f"frame {index} final_paint_field was not_requested")
            elif isinstance(trace, dict) and "final_paint_field" in trace:
                actual_frame = actual_reader.read(
                    trace, frame_id=frame_id, sequence_index=index)
                if actual_frame.cell_px != (args.cell_width or args.cell_px, args.cell_px):
                    raise ActualFieldError(
                        "final_paint_field.cell_px disagrees with --cell-px")
                actual = actual_frame.as_dict()
                actual_field_seen = True
                field_meta = actual_frame.metadata
                actual_frame_status = {
                    "status": "observed",
                    "schema": ACTUAL_FIELD_SCHEMA,
                    "path": str(actual_frame.path),
                    "sha256": field_meta.get("sha256"),
                    "bytes": field_meta.get("bytes"),
                    "record_bytes": field_meta.get("record_bytes"),
                    "field_size_px": list(actual_frame.field_size_px),
                    "logical_target_size": list(actual_frame.logical_target_size),
                    "presentation_size": list(actual_frame.presentation_size),
                    "capture_id": actual_frame.capture_id,
                    "actual_frame_index": actual_frame.actual_frame_index,
                    "sequence_frame": actual_frame.sequence_frame,
                    "request_id": actual_frame.request_id,
                    "profile": actual_frame.profile,
                    "projection": actual_frame.projection,
                    "applied_policy": actual_frame.policy,
                    "source_eligible": actual_frame.source_eligible,
                    "atlas_eligible": actual_frame.atlas_eligible,
                    "composite_eligible": actual_frame.composite_eligible,
                    "presentation_mode_eligible": actual_frame.presentation_mode_eligible,
                    "actor_alpha_nonzero_pixels": int(
                        np.count_nonzero(actual["actor_alpha"] > 0.0)),
                    "actor_alpha_max": float(np.max(actual["actor_alpha"])),
                    "actor_alpha_provenance": (
                        "generic overlay provenance only; this flight does not "
                        "require visible actor/plant overlap"),
                }
            else:
                legacy_field_seen = True
                actual_frame_status = actual_field_absent_status(trace or {})
                if args.actual_field_required:
                    raise ActualFieldError(
                        f"frame {index} has no final_paint_field sidecar")
            if actual_field_seen and legacy_field_seen:
                raise ActualFieldError(
                    "capture mixes actual-field and legacy cell-pair frames")

            # A native field capture already has the compositor's final pixel
            # colours. Join only the cell glyph identity for its highlight
            # families; the legacy rasterizer (and its atlas replay) remains on
            # the diagnostic path where it is actually needed.
            raster = (_actual_cell_identity_snapshot(frame, args.cell_px)
                      if actual is not None else
                      _cdr.rasterize(frame, cache, args.cell_px))
            if actual is not None:
                # Glyph/source identity remains owned by the cell transport;
                # only the actual colours and coverages come from this field.
                actual["cell_glyph_id"] = raster["cell_glyph"].copy()

            if actual is not None:
                out_image, full_stats = compose_actual_family(
                    actual, "full", previous_actual, (args.cell_width or args.cell_px, args.cell_px))
            else:
                out_image, full_stats = raster["image"].copy(), {
                    "family": "full",
                    "rule": FAMILY_RULES["full"],
                    "actual_field": False,
                    "evidence_kind": "legacy_cell_pair_diagnostic",
                }
            out_png = args.out / f"frame_{index:02d}.png"
            Image.fromarray(out_image).save(out_png)
            png_paths.append(out_png)
            family_pngs["full"].append(out_png)
            full_stats["png"] = str(out_png)
            family_stats["full"] = full_stats

            for name in families:
                if name == "full":
                    continue
                if actual is not None:
                    image, stats = compose_actual_family(
                        actual, name, previous_actual, (args.cell_width or args.cell_px, args.cell_px))
                else:
                    image, stats = compose_family(raster, name, previous_raster)
                    stats["evidence_kind"] = "legacy_cell_pair_diagnostic"
                family_png = args.out / name / f"frame_{index:02d}.png"
                Image.fromarray(image).save(family_png)
                family_pngs[name].append(family_png)
                stats["png"] = str(family_png)
                family_stats[name] = stats
            previous_raster = {"mask": raster["mask"],
                               "cell_glyph": raster["cell_glyph"].copy(),
                               "present": raster["present"].copy()}

            present = raster["present"]
            missing = [[int(c), int(r)]
                       for r, c in zip(*np.nonzero(~present))]
            # SENTINEL COLLISION, MEASURED PER FRAME.
            # `UNPAINTED_SENTINEL_RGB` is magenta, and this renderer ALSO
            # publishes magenta as its own diagnostic ink (FL-4796) - on this
            # capture, 283 cells of frame 0 carry glyph 219 (CP437 full block)
            # with FG (255,0,255) on BG (255,255,0), which fills the whole cell
            # with the same magenta the sentinel uses. Those pixels are CAPTURED
            # CONTENT, not unpainted cells. The two are still distinguishable
            # without looking at the picture: a sentinel pixel can only exist
            # where `present` is False, and this count says how much magenta the
            # capture itself published.
            sentinel = np.array(_cdr.UNPAINTED_SENTINEL_RGB, dtype=np.float32)
            fg_at_sentinel = np.all(raster["fg"] == sentinel, axis=-1) & present
            bg_at_sentinel = np.all(raster["bg"] == sentinel, axis=-1) & present
            state = {
                "present": present,
                "cell_glyph": raster["cell_glyph"].copy(),
                "fg": raster["fg"].copy(),
                "bg": raster["bg"].copy(),
                "colour_key": _colour_key(raster["fg"], raster["bg"]),
            }
            record = {
                "index": index,
                "frame_id": frame_id,
                "png": str(out_png),
                # The camera pose EXACTLY as the capture published it. Nothing
                # here is adjusted toward any declared movement line.
                "camera_world": _camera(trace),
                "camera_world_unsnapped": (trace or {}).get(
                    "camera_world_unsnapped"),
                "frame_trace_pose": (trace or {}).get("pose"),
                "frame_trace_frame_index": (trace or {}).get("frame_index"),
                "frame_trace_movement_phase": (trace or {}).get(
                    "movement_phase"),
                "authority_server_tick": (trace or {}).get(
                    "authority_server_tick"),
                "records_consumed": raw_count,
                "records_adapted": frame["accepted_rows"],
                "declared_rows_in_frame": (trace or {}).get(
                    "oracle_admitted_rows_in_frame"),
                "grid_cols": frame["cols"],
                "grid_rows": frame["rows"],
                "grid_source": frame["grid_source"],
                "cells_published": int(present.sum()),
                "cells_without_record_count": len(missing),
                "cells_without_record": missing,
                "cells_stamped": int(raster["stamped"].sum()),
                "cells_published_with_sentinel_fg": int(fg_at_sentinel.sum()),
                "cells_published_with_sentinel_bg": int(bg_at_sentinel.sum()),
                # SENTINEL WHOLE-CELL PAINT, PER FRAME. The count and the cell
                # list are the OBSERVATION and are published in both modes. The
                # substituted list is what the readability aid actually
                # repainted, and is empty in `none`.
                "sentinel_fallback_mode": frame["sentinel_fallback_mode"],
                "sentinel_whole_cell_paint_count": frame["sentinel_cell_count"],
                "sentinel_whole_cell_paint_cells": frame["sentinel_cells"],
                "sentinel_cells_substituted_count": len(
                    frame["sentinel_cells_substituted"]),
                "sentinel_cells_substituted": frame[
                    "sentinel_cells_substituted"],
                "sentinel_cells_without_usable_slot": frame[
                    "sentinel_cells_without_usable_slot"],
                "actual_field": actual_frame_status,
                "skipped_row_types": frame["skipped_row_types"],
                "delta_vs_previous_frame": (
                    None if previous_state is None
                    else frame_delta(previous_state, state)),
                "actual_field_delta_vs_previous_frame": (
                    None if actual is None
                    else _actual_field_delta(previous_actual, actual)),
                "render_families": family_stats,
            }
            # CROSS-CHECK, NOT A CLAIM. The highlight family counts changed
            # cells by walking the grid; frame_delta counts them with numpy off
            # the same two elections. They are two independent counts of one
            # fact and they must agree, so a disagreement is a bug in this file
            # and is refused rather than published.
            highlight = family_stats.get("fg_glyph_change_highlight")
            if highlight is not None and record["delta_vs_previous_frame"]:
                expected = record["delta_vs_previous_frame"][
                    "cells_with_glyph_change"]
                if highlight["cells_with_glyph_change"] != expected:
                    print(f"ABORT: frame {index} highlight counted "
                          f"{highlight['cells_with_glyph_change']} changed "
                          f"cells, frame_delta counted {expected}",
                          file=sys.stderr)
                    return 1
            frame_records.append(record)
            previous_state = state
            if actual is not None:
                # Advance the bounded two-frame actual-field window only after
                # the current record has measured its delta against the prior
                # frame.
                previous_actual = actual
            print(f"[fl4359-raster] frame {index:02d} frame_id={frame_id} "
                  f"cam={record['camera_world']} rows={raw_count} "
                  f"grid={frame['cols']}x{frame['rows']} "
                  f"sentinel={record['sentinel_whole_cell_paint_count']} "
                  f"substituted={record['sentinel_cells_substituted_count']} "
                  f"-> {out_png.name}")
    except (TransportError, ActualFieldError) as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return 1

    if not frame_records:
        print("ABORT: the capture yielded no frames", file=sys.stderr)
        return 1

    try:
        derived_mode_postfix = _mode_postfix_from_frame_records(frame_records)
    except ActualFieldError as exc:
        print(f"ABORT: {exc}", file=sys.stderr)
        return 1
    if (args.mode_postfix and derived_mode_postfix
            and args.mode_postfix != derived_mode_postfix):
        print(
            "ABORT: --mode-postfix disagrees with the applied FG/BG "
            f"sampling policy ({derived_mode_postfix})",
            file=sys.stderr,
        )
        return 1
    mode_postfix = args.mode_postfix or derived_mode_postfix
    mode_postfix_source = (
        "applied_policy" if derived_mode_postfix else
        "explicit_legacy_fallback" if args.mode_postfix else
        "unavailable")

    # ---- receipt -----------------------------------------------------------
    # Named `receipt.json` on purpose: fl4359_stitch_sweep_gif.py's
    # `_declared_poses` reads `frame_png_poses` from a receipt.json beside the
    # frames to caption each panel with that frame's pose. The poses below are
    # the ACTUAL captured `frame_trace.camera_world`, never a declared line.
    #
    # WRITTEN BEFORE THE GIF, ON PURPOSE. The stitcher READS this file, so a
    # receipt written only after it would caption this run's GIF from whatever
    # receipt an EARLIER run left behind - a caption describing a different
    # capture. The same path is rewritten once with the GIF facts added, so the
    # file still has one owner and one final content.
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "authority": AUTHORITY,
        "claim_boundary": (
            "This receipt describes the CAPTURED DATA and the images this "
            "script drew from it. It makes no verified, passed, proven, fixed "
            "or accepted claim about renderer behaviour."),
        "arm_label": args.arm_label,
        "mode_postfix": mode_postfix,
        "mode_postfix_source": mode_postfix_source,
        "capture": str(args.capture),
        "capture_bytes": args.capture.stat().st_size,
        "capture_sha256": _sha256_path(args.capture),
        "actual_field": {
            "schema": ACTUAL_FIELD_SCHEMA,
            "observation": "per_frame_trace_sidecar",
            "mode": (
                "actual_final_paint_field"
                if actual_field_seen else "legacy_pair_diagnostic"
            ),
            "frames_observed": sum(
                rec["actual_field"]["status"] == "observed"
                for rec in frame_records),
            "frames_absent": sum(
                rec["actual_field"]["status"] == "absent"
                for rec in frame_records),
            "all_frames_observed": all(
                rec["actual_field"]["status"] == "observed"
                for rec in frame_records),
            "presentation_mode_eligible": bool(frame_records) and all(
                rec["actual_field"]["status"] == "observed"
                and rec["actual_field"].get("presentation_mode_eligible", False)
                for rec in frame_records),
            "presentation_mode_eligibility_requires_all_frames": True,
            "required": args.actual_field_required,
            "legacy_pair_is_diagnostic_only": True,
            "capture_jsonl_sha256_binding": (
                "The JSONL hash is bound by the post-run manifest/receipt; "
                "it is deliberately absent from every frame trace and field "
                "metadata to avoid circular hashing."),
        },
        "transport_schema_supported": list(SUPPORTED_TRANSPORT_SCHEMAS),
        "consumed_columns": list(CONSUMED_COLUMNS),
        "cell_px": args.cell_px,
        "native_cell_size": [args.cell_width or args.cell_px, args.cell_px],
        "frames_requested": args.frames,
        "frames_rasterized": len(frame_records),
        "grid_cols": grid[1],
        "grid_rows": grid[0],
        "unpainted_sentinel_rgb": list(_cdr.UNPAINTED_SENTINEL_RGB),
        "unpainted_sentinel_note": (
            "Magenta marks a cell the capture published no record for. This "
            "renderer also publishes magenta ink of its own (FL-4796), so the "
            "per-frame cells_published_with_sentinel_fg / _bg counts say how "
            "much of the magenta in an image came from the capture. A sentinel "
            "pixel can only occur where cells_without_record_count > 0."),
        "sentinel_whole_cell_paint": {
            "mode": args.sentinel_fallback,
            "modes_supported": list(SENTINEL_FALLBACK_MODES),
            "definition": {
                "fg_rgb": list(SENTINEL_WHOLE_CELL_FG),
                "bg_rgb": list(SENTINEL_WHOLE_CELL_BG),
                "final_paint_glyph": SENTINEL_WHOLE_CELL_GLYPH,
                "test": ("exact equality on the raw 0..255 transport "
                         "fg_rgb/bg_rgb AND final_paint_glyph"),
            },
            "substitution_source": (
                "final_paint_slot_fg_rgb / final_paint_slot_bg_rgb of the "
                "lowest-numbered subcell slot with "
                "final_paint_slot_colour_present true whose own colour pair is "
                "not the sentinel pair. Colour only: the elected glyph is "
                "never changed."),
            "readability_note": SENTINEL_FALLBACK_NOTE,
            "population_is_an_observation": (
                "The sentinel cell count and cell list published per frame "
                "below are a DESCRIPTION of what the capture recorded about "
                "the renderer. They are published in both modes and are not "
                "produced by the fallback."),
            "final_paint_mask_state_is_not_the_discriminator": (
                "Every cell on this capture reports "
                "final_mask_digest_mismatch, so final_paint_mask_state does "
                "not discriminate the sentinel population and is not part of "
                "the test above."),
            "totals": {
                "sentinel_whole_cell_paint_count": [
                    rec["sentinel_whole_cell_paint_count"]
                    for rec in frame_records],
                "sentinel_cells_substituted_count": [
                    rec["sentinel_cells_substituted_count"]
                    for rec in frame_records],
            },
        },
        "glyph_election": (
            "fl4359_cell_dump_raster.elect_cell_glyph over "
            "final_paint_mask_glyphs (the drawn whole-cell mask). "
            "final_product_glyph is the per-subcell Product identity and is "
            "recorded in the identity lane only, never elected."),
        "atlas_sampler": (
            "fl4359_cell_dump_raster.AtlasPage - the compositor's native "
            "source footprint, not the 12px host replay"),
        "row_schema_adapter": {
            "owner": "fl4359_cell_dump_raster._adapt_jitter_cell",
            "field_map": _cdr.JITTER_CELL_FIELD_MAP,
        },
        "render_families": {
            "requested": families,
            "supported": list(RENDER_FAMILIES),
            "rules": {name: FAMILY_RULES[name] for name in families},
            "layout": (
                "`full` is written to <out>/frame_NN.png; every other family to "
                "<out>/<family>/frame_NN.png with its own <family>_5frames.gif "
                "beside it. All families share this receipt, so the stitcher "
                "captions every family's panels with the same captured poses."),
            "constants_owner": (
                "fl4359_cell_dump_gifs.py owns HIGHLIGHT_RGB, HIGHLIGHT_MIX, "
                "GLYPH_CHANGE_RGB, GLYPH_CHANGE_MIX and GLYPH_HOLD_DIM; this "
                "file imports them so the two rendering routes cannot drift."),
            "highlight_colour_collision": FAMILY_HIGHLIGHT_COLLISION_NOTE,
            "claim_boundary": (
                "Five views of ONE election, drawn from one capture. No family "
                "is a verdict, a shimmer measurement or an acceptance claim."),
            "actual_field_source": (
                "When present, all six families are derived from the same "
                "fl4359.final_paint_field.v1 per-frame native field. A capture "
                "without those sidecars uses the legacy cell-pair diagnostic "
                "path and cannot qualify continuous FG/BG evidence."),
            "gifs": None,
            "mode_postfix": mode_postfix,
            "mode_postfix_source": mode_postfix_source,
        },
        "frames": frame_records,
        # For fl4359_stitch_sweep_gif.py's caption band.
        "frame_png_poses": [
            {"sequence_frame": rec["index"],
             "camera_position": rec["camera_world"]}
            for rec in frame_records if rec["camera_world"]
        ],
        "gif": None,
    }
    receipt_path = args.out / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))

    # ---- GIF ---------------------------------------------------------------
    # `fl4359_stitch_sweep_gif.py` fits when there are at least the five frames
    # it is written for: it wants frame_00..frame_04 in one directory and emits
    # a looping 60 ms GIF with a per-panel caption band. Fewer than five frames
    # is outside its contract, so the PIL fallback is used instead.
    gif_path = args.out / _gif_name(GIF_NAME, mode_postfix)
    canonical_gif_path = args.out / GIF_NAME
    gif_info: dict
    if len(png_paths) >= GIF_FRAME_COUNT:
        cmd = [sys.executable,
               str(ROOT / "scripts" / "fl4359_stitch_sweep_gif.py"),
               "--arm", f"{args.arm_label}={args.out}",
               "--out", str(gif_path),
               "--frame-ms", str(GIF_FRAME_MS)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        gif_info = {"writer": "fl4359_stitch_sweep_gif.py",
                    "command": cmd,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout.strip(),
                    "stderr": proc.stderr.strip(),
                    "path": str(gif_path)}
        if proc.returncode != 0 or not gif_path.is_file():
            gif_info["fallback_reason"] = (
                "stitcher exited non-zero or wrote no file")
            gif_info.update(_write_gif_directly(
                png_paths[:GIF_FRAME_COUNT], gif_path, GIF_FRAME_MS))
    else:
        gif_info = {"fallback_reason":
                    f"only {len(png_paths)} frames; the stitcher requires "
                    f"{GIF_FRAME_COUNT}"}
        gif_info.update(_write_gif_directly(png_paths, gif_path, GIF_FRAME_MS))

    submitted = min(len(png_paths), GIF_FRAME_COUNT)
    with Image.open(gif_path) as gif:
        gif_info["frames_submitted"] = submitted
        gif_info["read_back"] = {
            "n_frames": getattr(gif, "n_frames", 1),
            "size": list(gif.size),
            "duration_ms": gif.info.get("duration"),
            "loop": gif.info.get("loop"),
            "bytes": gif_path.stat().st_size,
        }
    _write_canonical_alias(gif_path, canonical_gif_path)
    if gif_path.resolve() != canonical_gif_path.resolve():
        gif_info["canonical_alias_path"] = str(canonical_gif_path)
    if gif_info["read_back"]["n_frames"] < submitted:
        # Reported, never papered over: see the Pillow note above.
        gif_info["read_back"]["frames_collapsed"] = (
            submitted - gif_info["read_back"]["n_frames"])

    # ---- one GIF per extra family -----------------------------------------
    # Same writer, same 60 ms, same five frames as the `full` GIF above, so the
    # families can be played side by side against each other without any of them
    # having been timed differently.
    family_gifs: dict[str, dict] = {}
    for name in families:
        if name == "full":
            family_gifs[name] = {"path": str(gif_path), "shared_with": "gif"}
            continue
        paths = family_pngs[name]
        target = args.out / name / _gif_name(f"{name}_5frames.gif",
                                             mode_postfix)
        canonical_target = args.out / name / f"{name}_5frames.gif"
        if len(paths) >= GIF_FRAME_COUNT:
            cmd = [sys.executable,
                   str(ROOT / "scripts" / "fl4359_stitch_sweep_gif.py"),
                   "--arm", f"{args.arm_label}-{name}={args.out / name}",
                   "--out", str(target),
                   "--frame-ms", str(GIF_FRAME_MS)]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            info = {"writer": "fl4359_stitch_sweep_gif.py",
                    "command": cmd,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout.strip(),
                    "stderr": proc.stderr.strip(),
                    "path": str(target)}
            if proc.returncode != 0 or not target.is_file():
                info["fallback_reason"] = (
                    "stitcher exited non-zero or wrote no file")
                info.update(_write_gif_directly(
                    paths[:GIF_FRAME_COUNT], target, GIF_FRAME_MS))
        else:
            info = {"fallback_reason":
                    f"only {len(paths)} frames; the stitcher requires "
                    f"{GIF_FRAME_COUNT}"}
            info.update(_write_gif_directly(paths, target, GIF_FRAME_MS))
        submitted_family = min(len(paths), GIF_FRAME_COUNT)
        with Image.open(target) as gif:
            info["frames_submitted"] = submitted_family
            info["read_back"] = {
                "n_frames": getattr(gif, "n_frames", 1),
                "size": list(gif.size),
                "duration_ms": gif.info.get("duration"),
                "loop": gif.info.get("loop"),
                "bytes": target.stat().st_size,
            }
        _write_canonical_alias(target, canonical_target)
        if target.resolve() != canonical_target.resolve():
            info["canonical_alias_path"] = str(canonical_target)
        if info["read_back"]["n_frames"] < submitted_family:
            info["read_back"]["frames_collapsed"] = (
                submitted_family - info["read_back"]["n_frames"])
        family_gifs[name] = info
        print(f"[fl4359-raster] family {name} gif {target} "
              f"{info['read_back']}")

    # ---- receipt, rewritten with the GIF facts -----------------------------
    receipt["render_families"]["gifs"] = family_gifs
    receipt["gif"] = gif_info
    presentation_manifest = _presentation_artifact_manifest(
        args, frame_records, gif_info, family_gifs, mode_postfix)
    presentation_manifest_path = args.out / "presentation_artifact_manifest.json"
    presentation_manifest_path.write_text(
        json.dumps(presentation_manifest, indent=2, sort_keys=True) + "\n")
    receipt["presentation_artifact_manifest"] = presentation_manifest
    receipt["presentation_artifact_manifest_path"] = str(
        presentation_manifest_path)
    receipt_path = args.out / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
    print(f"[fl4359-raster] receipt {receipt_path}")
    print(f"[fl4359-raster] gif {gif_path} {gif_info['read_back']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
