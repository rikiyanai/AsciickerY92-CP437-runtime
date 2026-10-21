#!/usr/bin/env python3
# =============================================================================
# fl4359_product_cell_dump.py
# =============================================================================
# FL-4359 PRODUCT-RECORD CELL READER: the path that does NOT guess.
#
# WHY THIS MODULE EXISTS
# ----------------------
# Inferring a glyph mask by thresholding pixel luminance carries a hidden
# assumption: brighter pixels are ink. When a cell keeps its elected glyph but
# its FG and BG swap relative lightness, a luminance threshold returns the
# INVERTED mask, and a glyph that never moved scores IoU near zero. On the
# FL-4359 airplane capture that happens to 28-30% of measurable cells in every
# frame pair, and it inflated the mask-churn reading by roughly a third.
#
# A live run never has to guess. The renderer already elected one whole-cell
# glyph and published two colours per cell, and both are readable. With the
# glyph id in hand, mask polarity is not a question at all: the mask IS the
# atlas stamp of a known glyph id, and comparing two frames becomes exact glyph
# id equality plus a colour delta.
#
# THE TWO READ PATHS, AND WHY BOTH
# --------------------------------
# 1. HOST-DECODED (`cells.jsonl`). `World._write_final_render_cell_dump`
#    (World.gd:8532) writes `.run/final_render_cell_dump/<stamp>/` holding
#    `cells.jsonl` + `final.png` + `metadata.json`. Each row carries
#    `screen_cell {x,y}`, `glyph_id`, `glyph_char`, `fg_rgb`, `bg_rgb`,
#    `material_id`, `ink_coverage` (row built at
#    ASCIICompositorEffect.gd:19619-19634). This is the canonical, already-owned
#    serialization of the Product record and it is the preferred input.
# 2. RAW STATUS WORD. When a capture carries the packed status word instead of a
#    decoded glyph id, `status_is_valid`/`status_foliage_role` implement the
#    exact GLSL ABI below for validity and foliage role. The status word carries
#    no glyph (FL-4848): the elected cell glyph is the NativePresentationResult
#    record, which the host decodes into `glyph_id`.
#
# ABI, TRANSCRIBED FROM SOURCE (do not "simplify" these)
# ------------------------------------------------------
# `FinalSubcellProductRecord` (ascii_compositor.glsl:156-183) is exactly 80
# bytes = five 16-byte rows, and must stay byte-for-byte identical to
# cell_owner_product.glsl's producer struct:
#     offset  0  uvec4 r0   x=status  y=owner_kind  z=quarter glyph  w=material_family
#     offset 16  uvec4 r1   x=material_id  y=stable_id  z=entity_id  w=packed material cell
#     offset 32  vec4  fg   ink colour, .a unused
#     offset 48  vec4  bg   paper colour, .a unused
#     offset 64  vec4  anchor  xyz = semantic world anchor, w = world depth (FL-4346)
# Four subcell records make one ANSI cell. The elected whole-cell glyph is
# packed into the status word and copied to ALL FOUR records
# (cell_owner_product.glsl:248-252), so any quarter yields the same cell glyph
# while fg/bg stay per quarter.
#
# Status word bit layout (cell_owner_product.glsl:220-265, mirrored at
# ascii_compositor.glsl:89-118 — the two are one contract and move together).
#
# Exit codes when run as a tool: 0 = dump read and summarized; 1 = unreadable.
#
# EVIDENCE BOUNDARY
# -----------------
# This module has NEVER been exercised against a live capture in this lane: no
# headed run is authorized here, and the tracked 5-frame GIF fixture carries no
# Product record. The ABI decode below is unit-tested against status words built
# from the source constants, which tests the DECODE but not the wiring. Treat
# every live-path claim as untested until a real dump exists.
# =============================================================================

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

# --- Frozen ABI, transcribed from the GLSL ----------------------------------
FINAL_SUBCELL_PRODUCT_ABI_VERSION = 2
FINAL_SUBCELL_PRODUCT_RECORD_BYTES = 80        # five 16-byte rows
FINAL_SUBCELL_PRODUCT_SUBCELLS_PER_CELL = 4    # 2x2 quarters per ANSI cell
RECORD_OFFSET_R0 = 0
RECORD_OFFSET_R1 = 16
RECORD_OFFSET_FG = 32
RECORD_OFFSET_BG = 48
RECORD_OFFSET_ANCHOR = 64

FINAL_PRODUCT_STATUS_VALID = 1 << 0
FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT = 1 << 1
FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_SHIFT = 2
FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_MASK = 0x0000003C     # bits 2..5
# FL-4848: bits 6..23 are reserved. The elected cell glyph is not in the b55
# status word; it lives only in the NativePresentationResult record the host
# decodes into each row's glyph_id.
FINAL_PRODUCT_STATUS_ABI_SHIFT = 24
FINAL_PRODUCT_STATUS_ABI_MASK = 0xFF000000
FINAL_PRODUCT_STATUS_ABI_V2 = (
    FINAL_SUBCELL_PRODUCT_ABI_VERSION << FINAL_PRODUCT_STATUS_ABI_SHIFT)
FINAL_PRODUCT_STATUS_KNOWN_MASK = (
    FINAL_PRODUCT_STATUS_ABI_MASK
    | FINAL_PRODUCT_STATUS_VALID
    | FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT
    | FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_MASK)

SCHEMA = "fl4359_product_cell_dump.v1"


def status_is_valid(status: int) -> bool:
    """Mirror of final_product_status_is_valid (ascii_compositor.glsl:124-129).

    All three conditions matter. Dropping the unknown-bit check would let a
    record from a future ABI read as valid with fields that moved.
    """
    return (
        (status & FINAL_PRODUCT_STATUS_VALID) != 0
        and (status & FINAL_PRODUCT_STATUS_ABI_MASK) == FINAL_PRODUCT_STATUS_ABI_V2
        and (status & ~FINAL_PRODUCT_STATUS_KNOWN_MASK) == 0
    )


def status_foliage_role(status: int) -> int:
    """Mirror of final_product_status_foliage_role. -1 means no role recorded.

    The stored value is (role + 1) so that FOLIAGE_ROLE_STEM, which is 0, cannot
    be confused with an untouched record.
    """
    if not (status_is_valid(status)
            and (status & FINAL_PRODUCT_STATUS_FOLIAGE_PRESENT) != 0):
        return -1
    stored = (status & FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_MASK) >> \
        FINAL_PRODUCT_STATUS_FOLIAGE_ROLE_SHIFT
    return -1 if stored == 0 else stored - 1


def unpack_record(buf: bytes, index: int = 0) -> dict:
    """Decode one 80-byte FinalSubcellProductRecord from a raw SSBO readback."""
    base = index * FINAL_SUBCELL_PRODUCT_RECORD_BYTES
    if len(buf) < base + FINAL_SUBCELL_PRODUCT_RECORD_BYTES:
        raise ValueError(f"record {index} runs past the end of a {len(buf)}-byte buffer")
    r0 = struct.unpack_from("<4I", buf, base + RECORD_OFFSET_R0)
    r1 = struct.unpack_from("<4I", buf, base + RECORD_OFFSET_R1)
    fg = struct.unpack_from("<4f", buf, base + RECORD_OFFSET_FG)
    bg = struct.unpack_from("<4f", buf, base + RECORD_OFFSET_BG)
    anchor = struct.unpack_from("<4f", buf, base + RECORD_OFFSET_ANCHOR)
    return {
        "status": r0[0], "owner_kind": r0[1],
        "quarter_glyph": r0[2], "material_family": r0[3],
        "material_id": r1[0], "stable_id": r1[1], "entity_id": r1[2],
        "material_cell_packed": r1[3],
        "fg_rgb": [c * 255.0 for c in fg[:3]],
        "bg_rgb": [c * 255.0 for c in bg[:3]],
        "anchor_xyz": list(anchor[:3]),
        "world_depth": anchor[3],
        "status_valid": status_is_valid(r0[0]),
        "foliage_role": status_foliage_role(r0[0]),
    }


def _rgb(value) -> list | None:
    """cells.jsonl writes colours as either [r,g,b] or {r,g,b}; accept both."""
    if value is None:
        return None
    if isinstance(value, dict):
        out = [value.get("r"), value.get("g"), value.get("b")]
    elif isinstance(value, (list, tuple)) and len(value) >= 3:
        out = list(value[:3])
    else:
        return None
    if any(c is None for c in out):
        return None
    out = [float(c) for c in out]
    # The dump writes 0..1 floats; normalize to 0..255 so a Product cell and a
    # pixel-extracted cell are on one scale and their colour deltas comparable.
    if max(out) <= 1.0:
        out = [c * 255.0 for c in out]
    return out


def load_cells_jsonl(path: Path) -> dict:
    """Read a final_render_cell_dump cells.jsonl into {(row, col): cell facts}.

    `screen_cell` is {x: col, y: row}. Rows without one are skipped and counted;
    a dump whose rows lack a screen cell cannot be compared per cell and the
    caller must see that rather than get a silently short grid.
    """
    cells = {}
    skipped = 0
    rows = 0
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            sc = row.get("screen_cell")
            if not isinstance(sc, dict) or "x" not in sc or "y" not in sc:
                skipped += 1
                continue
            status = row.get("product_status")
            # The host decodes the elected glyph from the NativePresentationResult
            # record; the b55 status word carries no glyph (FL-4848).
            glyph = row.get("glyph_id")
            glyph = None if glyph is None else int(glyph)
            glyph_source = "cells_jsonl_glyph_id"
            decoded = None
            if status is not None:
                decoded = {
                    "status": int(status),
                    "valid": status_is_valid(int(status)),
                    "foliage_role": status_foliage_role(int(status)),
                }
            cells[(int(sc["y"]), int(sc["x"]))] = {
                "glyph_id": glyph,
                "glyph_source": glyph_source,
                "glyph_char": row.get("glyph_char"),
                "fg_rgb": _rgb(row.get("fg_rgb")),
                "bg_rgb": _rgb(row.get("bg_rgb")),
                "material_id": row.get("material_id"),
                "material_family": row.get("material_family"),
                "ink_coverage": row.get("ink_coverage"),
                "owner_kind": row.get("owner_kind"),
                "status_decoded": decoded,
            }
    return {"cells": cells, "rows": rows, "skipped_rows": skipped,
            "source": str(path)}


def load_product_frames(dirs: list[Path]) -> dict:
    """Load one product dump per frame, in the given order."""
    frames = []
    for d in dirs:
        path = d / "cells.jsonl" if d.is_dir() else d
        if not path.exists():
            raise SystemExit(f"ABORT: {path} missing; a Product arm needs a "
                             f"final_render_cell_dump per frame")
        frames.append(load_cells_jsonl(path))
    return {"schema": SCHEMA, "frames": frames}


def compare_product_frames(frames: list[dict]) -> dict:
    """Per-cell, per-pair comparison with NO polarity question anywhere.

    Two cells match when their elected glyph ids are equal. That is an exact
    identity test, not a shape-similarity score, so an FG/BG lightness swap
    cannot masquerade as a glyph change and a glyph change cannot hide behind a
    similar-looking mask.
    """
    if len(frames) < 2:
        raise SystemExit("ABORT: need at least two frames to compare")
    shared = set(frames[0]["cells"])
    for f in frames[1:]:
        shared &= set(f["cells"])
    pairs = []
    for i in range(len(frames) - 1):
        a, b = frames[i]["cells"], frames[i + 1]["cells"]
        changed = held = colour_moved = 0
        for key in shared:
            ga, gb = a[key]["glyph_id"], b[key]["glyph_id"]
            if ga is None or gb is None:
                continue
            if ga == gb:
                held += 1
            else:
                changed += 1
            fa, fb = a[key]["fg_rgb"], b[key]["fg_rgb"]
            ba, bb = a[key]["bg_rgb"], b[key]["bg_rgb"]
            if fa and fb and ba and bb:
                d_fg = sum((x - y) ** 2 for x, y in zip(fa, fb)) ** 0.5
                d_bg = sum((x - y) ** 2 for x, y in zip(ba, bb)) ** 0.5
                if max(d_fg, d_bg) > 30.0:
                    colour_moved += 1
        total = max(held + changed, 1)
        pairs.append({
            "pair": [i, i + 1],
            "cells_compared": held + changed,
            "glyph_id_held": held,
            "glyph_id_changed": changed,
            "glyph_id_changed_pct": round(100.0 * changed / total, 2),
            "colour_delta_gt30": colour_moved,
        })
    # Oscillation with exact identity: a cell that returns to an earlier glyph
    # id. Under monotone translation with held orientation this cannot happen
    # from camera motion, and unlike the pixel path there is no threshold and no
    # margin to argue about.
    oscillating = 0
    for key in shared:
        ids = [f["cells"][key]["glyph_id"] for f in frames]
        for t in range(len(ids) - 2):
            trio = ids[t:t + 3]
            if None in trio:
                continue
            if trio[0] == trio[2] and trio[0] != trio[1]:
                oscillating += 1
                break
    return {
        "schema": SCHEMA,
        "frames": len(frames),
        "cells_present_in_every_frame": len(shared),
        "comparison_basis": "exact Product glyph id equality; mask polarity is not inferred",
        "pairs": pairs,
        "oscillating_cells_glyph_id_return": oscillating,
        "oscillating_rule": "glyph_id(N) == glyph_id(N+2) != glyph_id(N+1)",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="FL-4359 Product-record cell dump reader (glyph id, no inference)")
    ap.add_argument("--dump", type=Path, action="append", required=True,
                    help="final_render_cell_dump directory (or a cells.jsonl), one per frame")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    loaded = load_product_frames(args.dump)
    report = compare_product_frames(loaded["frames"])
    report["sources"] = [f["source"] for f in loaded["frames"]]
    report["rows_read"] = [f["rows"] for f in loaded["frames"]]
    report["rows_skipped"] = [f["skipped_rows"] for f in loaded["frames"]]
    report["evidence_boundary"] = (
        "this reader has not been exercised against a live capture in the lane that "
        "wrote it; the ABI decode is unit-tested from source constants only")
    text = json.dumps(report, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
