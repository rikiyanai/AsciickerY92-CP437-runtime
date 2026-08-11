"""FL-4208 Gate T1 — mask-side topology regression.

Locks the round-6 contract correction for `_enclosed_holes`:

  * `_enclosed_holes(grid)` returns β₁ᵣₐw — the number of bounded,
    border-unreachable, **4-connected** background components (= holes) —
    NOT enclosed-background pixel area (the pre-Gate-T1 behavior).
  * `fill_state` / `topo_class.has_loop` gate on `> 0` holes, not `> 2` px.

This is Gate T1 ONLY. It does not touch the graph constructor, the schema-2
cache, or any FL-4260/runtime path (those are Gate T2 / blocked lanes).

Run: `python3 -m pytest scripts/test_glyph_topology_gate_t1.py -q`
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from glyph_skeleton import _enclosed_holes, fill_state, axis_vector  # noqa: E402

UNIFONT = REPO_ROOT / "assets" / "fonts" / "unifont-17.0.04.otf"
INK_THRESHOLD = 96  # mirror glyph_skeleton.INK_THRESHOLD


# ---------------------------------------------------------------------------
# Synthetic masks — fully font-independent, deterministic.
# ---------------------------------------------------------------------------
def _ones(h, w):
    return np.ones((h, w), np.uint8)


def _empty_grid():
    return np.zeros((9, 9), np.uint8)


def _solid_block():
    return _ones(9, 9)


def _single_pixel_hole():
    """1 enclosed background pixel, fully ink-surrounded. Old area-test (>2)
    called this 'no hole'; β₁ᵣₐw counts it as one hole."""
    g = _ones(7, 7)
    g[3, 3] = 0
    return g


def _ring():
    """Ink square with one 3x3 background hole, set in a background field
    (outer bg border-reachable, inner bg enclosed) -> exactly 1 hole."""
    g = np.zeros((11, 11), np.uint8)
    g[2:9, 2:9] = 1
    g[4:7, 4:7] = 0
    return g


def _two_rings():
    """One ink slab with two separate enclosed holes -> 2 holes."""
    g = np.zeros((11, 15), np.uint8)
    g[2:9, 2:13] = 1
    g[4:7, 3:6] = 0
    g[4:7, 8:11] = 0
    return g


def _diagonal_pockets():
    """Two single-pixel enclosed background cells that touch ONLY at a corner.
    Under 4-connectivity they are two distinct holes (contract); under
    8-connectivity they would merge into one. This is the discriminating test
    that proves the background flood is 4-connected, not 8-connected."""
    g = _ones(7, 7)
    g[2, 2] = 0
    g[3, 3] = 0
    return g


def _adjacent_pockets():
    """Same two cells but 4-adjacent -> a single hole (positive control)."""
    g = _ones(7, 7)
    g[2, 2] = 0
    g[2, 3] = 0
    return g


def test_enclosed_holes_counts_components_not_area():
    assert _enclosed_holes(_empty_grid()) == 0
    assert _enclosed_holes(_solid_block()) == 0
    assert _enclosed_holes(_single_pixel_hole()) == 1   # area=1, count=1
    assert _enclosed_holes(_ring()) == 1                # area=9, count=1
    assert _enclosed_holes(_two_rings()) == 2


def test_background_flood_is_4_connected():
    # The crux of the (8,4) contract: corner-touching pockets are SEPARATE holes.
    assert _enclosed_holes(_diagonal_pockets()) == 2
    assert _enclosed_holes(_adjacent_pockets()) == 1


def test_fill_state_single_hole_is_hollow_regression():
    # Pre-Gate-T1 (`area > 2`) classified a 1px hole as solid. β₁ᵣₐw (`> 0`)
    # makes any enclosed hole hollow.
    assert fill_state(_single_pixel_hole()) == "hollow"
    assert fill_state(_ring()) == "hollow"
    assert fill_state(_solid_block()) == "solid"
    assert fill_state(np.eye(10, dtype=np.uint8)) == "open"


def test_loop_class_arc_loop_for_pure_ring():
    av = axis_vector(_ring())
    # A pure ring (no tails) has an enclosed hole and zero endpoints -> arc_loop.
    assert av["topo_class"] == "arc_loop"


# ---------------------------------------------------------------------------
# Frozen Unifont fixtures — pinned font, real glyph topology.
# ---------------------------------------------------------------------------
def _unifont_grid(cp: int, size: int = 96, font_px: int = 192) -> np.ndarray:
    """Render `cp` from the pinned Unifont OTF (crop-to-ink, fit-centered),
    matching glyph_skeleton.HiRenderer.grid but locked to one font for a frozen
    fixture."""
    font = ImageFont.truetype(str(UNIFONT), font_px)
    ch = chr(cp)
    bbox = font.getbbox(ch)
    pad = 8
    canvas = Image.new("L", (bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2), 0)
    ImageDraw.Draw(canvas).text((pad - bbox[0], pad - bbox[1]), ch, fill=255, font=font)
    ink = canvas.getbbox()
    crop = canvas.crop(ink)
    cw, chh = crop.size
    scale = min(size / cw, size / chh)
    nw, nh = max(1, round(cw * scale)), max(1, round(chh * scale))
    rs = crop.resize((nw, nh), Image.LANCZOS)
    out = Image.new("L", (size, size), 0)
    out.paste(rs, ((size - nw) // 2, (size - nh) // 2))
    return (np.asarray(out) > INK_THRESHOLD).astype(np.uint8)


# codepoint -> expected β₁ᵣₐw (hole count). Topologically unambiguous glyphs.
UNIFONT_FIXTURES = {
    0x0049: 0,   # I  — no hole
    0x004C: 0,   # L  — no hole
    0x004F: 1,   # O  — one hole
    0x0041: 1,   # A  — one hole
    0x0044: 1,   # D  — one hole
    0x0038: 2,   # 8  — two holes
    0x0042: 2,   # B  — two holes
}


def test_unifont_frozen_hole_counts():
    assert UNIFONT.exists(), f"frozen Unifont fixture missing: {UNIFONT}"
    for cp, expected in UNIFONT_FIXTURES.items():
        observed = _enclosed_holes(_unifont_grid(cp))
        assert observed == expected, (
            f"U+{cp:04X} ({chr(cp)}): expected {expected} holes, got {observed}")


def unifont_sha256() -> str:
    return hashlib.sha256(UNIFONT.read_bytes()).hexdigest()


if __name__ == "__main__":
    # Self-run helper: print the observed table + font hash for the receipt.
    print(f"unifont sha256: {unifont_sha256()}")
    for cp, expected in UNIFONT_FIXTURES.items():
        obs = _enclosed_holes(_unifont_grid(cp))
        print(f"  U+{cp:04X} {chr(cp)}: expected={expected} observed={obs} "
              f"{'OK' if obs == expected else 'MISMATCH'}")
