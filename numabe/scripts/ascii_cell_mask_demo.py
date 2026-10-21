#!/usr/bin/env python3
"""Standalone demonstrator for the stable F16 cell-mask requirement.
Context: FL-4740 cell-mask requirement and the late F16 proof gate.
Status: this is a requirements demonstrator, not current-head proof. The
visual requirement is stable: one discrete whole glyph mask belongs to each
fixed ANSI cell. Foreground/background are continuous native-pixel fields
sampled on one framebuffer-wide quarter-center lattice; they cross shared
ANSI-cell edges and never restart at cell boundaries. Subcell glyph clipping
or overlap is forbidden. Product-owned
semantic election is implemented and connected by commit 60fbc98baf. Its
current-head visual, temporal, and performance proof remains separate and
missing; this page is a requirement illustration, not runtime evidence.
This script writes one self-contained HTML page that shows, side by side,
the ALLOWED and the SUSPECT behavior of the final ASCII paint:

  ALLOWED  - the screen is a rigid terminal-cell matrix. Every cell selects
             exactly one glyph identity and one whole glyph mask. The
             foreground and background fields vary continuously across the
             whole grid and across shared cell edges. The lower loop of an
             "8" may be blue while the upper loop fades to red, without a
             per-cell gradient restart.
             The glyph silhouette itself stays whole and snapped to its cell.
  SUSPECT  - "cell-mask incoherence / subcell glyph fragmentation": geometry
             or material ownership is chosen independently at native-pixel
             or subtap granularity, each fragment brings its own glyph mask,
             and those fragments are composited inside the nominal cell
             rectangle. The result looks like a quilt or collage cut from
             patterned paper: the HTML dump still exposes a rectangular
             terminal cell, but the rendered pixels inside that rectangle no
             longer correspond to one terminal-cell glyph.

Both panels draw the SAME rectangular selection outline over the same cell
position, mirroring the HTML cell-dump selection. In the allowed panel the
rectangle contains one whole glyph; in the suspect panel the identical
rectangle contains pieces of several glyphs, which is the reported defect.

The page is pure HTML/CSS (no JavaScript, no external assets), so it can be
opened directly from disk and archived as evidence alongside the failure-log
record.

The stable requirement demonstrated here, stated precisely:

  For every terminal cell C there is exactly one selected glyph_id[C].
  For every native pixel p inside C:
      foreground(p)  iff  glyph_mask[glyph_id[C]](cell_local_coordinate(p))
  foreground(p) and background(p) are sampled from one grid-wide field on the
  framebuffer-wide quarter-center lattice; neither field restarts at C's edge.
  The mask is hard/discrete (no derivative-AA blur). Missing mask ownership
  paints one cell-wide 0xDB diagnostic and never revives a quarter-local glyph.
  Geometry, depth, material, and color fields may affect the selected cell
  payload and the colors sampled at p, but may NOT select, clip, or replace
  a different glyph mask for only part of C.
"""
from __future__ import annotations

import argparse
import random
from html import escape
from pathlib import Path

# The demo grid is deliberately small so a reviewer can see individual cells.
GRID_COLS = 12
GRID_ROWS = 5

# Characters used to fill the demo matrix. Digits and dense symbols make
# clipping visually obvious because their silhouettes are solid enclosed
# shapes.
DEMO_GLYPHS = "0839W#@%&$OQG"

# A constant seed keeps the page deterministic run-to-run so archived copies
# of the same script version are byte-comparable.
RANDOM_SEED = 4711

# The cell that receives the selection rectangle in BOTH panels. Same
# position on purpose: the point of the demo is that an identical rectangular
# selection means one whole glyph in the allowed case and a quilt of
# fragments in the suspect case.
SELECTED_COL = 5
SELECTED_ROW = 2

CELL_W_PX = 44   # native pixel width of one demo terminal cell
CELL_H_PX = 64   # native pixel height of one demo terminal cell
GRID_W_PX = GRID_COLS * CELL_W_PX
GRID_H_PX = GRID_ROWS * CELL_H_PX
GLYPH_FONT_SIZE_PX = 52
GLYPH_BASELINE_PX = 52

# Continuous color stops for the two grid-wide fields. The allowed panel uses
# one field for the complete framebuffer-sized grid; it never restarts either
# gradient at an ANSI-cell boundary.
FG_GRADIENT = ("#7f5af0", "#5ad7f0")
BG_GRADIENT = ("#241a33", "#101018")


def _rng() -> random.Random:
    return random.Random(RANDOM_SEED)


def _allowed_cell(selected: bool) -> str:
    """Transparent cell overlay; the glyph and both fields belong to the grid."""
    sel = " selected" if selected else ""
    return f'<div class="cell allowed{sel}" aria-hidden="true"></div>'


def _fragment(ch: str, left_pct: int, top_pct: int, w_pct: int, h_pct: int,
              dx_px: int, dy_px: int) -> str:
    """One suspect subcell fragment.

    The fragment window (left/top/width/height, in percent of the cell) shows
    a DIFFERENT glyph, offset by (dx, dy) native pixels, clipped by the
    window. This reproduces the reported failure mode: each subcell region
    brings its own glyph mask, so the nominal cell rectangle is filled with
    cut pieces of several glyphs instead of one whole silhouette.
    """
    return (
        f'<div class="frag" style="left:{left_pct}%;top:{top_pct}%;'
        f'width:{w_pct}%;height:{h_pct}%;">'
        f'<span class="frag-ink" style="transform:translate({dx_px}px,{dy_px}px);">{escape(ch)}</span>'
        f"</div>"
    )


def _suspect_cell(ch: str, rng: random.Random, selected: bool) -> str:
    """One defect-mimicking cell: the base glyph plus 2-3 overlapping foreign fragments."""
    sel = " selected" if selected else ""
    parts = [f'<span class="ink">{escape(ch)}</span>']
    # Split the cell into a few arbitrary windows, each showing a clipped
    # piece of some other glyph. Offsets are non-cell-aligned on purpose.
    for _ in range(rng.randint(2, 3)):
        other = rng.choice([g for g in DEMO_GLYPHS if g != ch])
        left = rng.randint(0, 55)
        top = rng.randint(0, 55)
        w = rng.randint(35, 70)
        h = rng.randint(30, 60)
        dx = rng.randint(-18, 6)
        dy = rng.randint(-26, 8)
        parts.append(_fragment(other, left, top, w, h, dx, dy))
    return f'<div class="cell suspect{sel}">' + "".join(parts) + "</div>"


def _grid(kind: str, rng: random.Random) -> str:
    cells = []
    glyphs = []
    glyph_rng = random.Random(RANDOM_SEED)  # same glyph layout in both panels
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            ch = glyph_rng.choice(DEMO_GLYPHS)
            selected = (col == SELECTED_COL and row == SELECTED_ROW)
            if kind == "allowed":
                glyphs.append((row, col, ch))
                cells.append(_allowed_cell(selected))
            else:
                cells.append(_suspect_cell(ch, rng, selected))
    if kind == "allowed":
        glyph_layer = [
            f'<svg class="global-ink" data-field-owner="allowed-fg-grid-svg" '
            f'aria-label="one grid-wide foreground field" '
            f'viewBox="0 0 {GRID_W_PX} {GRID_H_PX}" '
            f'width="{GRID_W_PX}" height="{GRID_H_PX}" '
            f'preserveAspectRatio="none">',
            '<defs>',
            f'<linearGradient id="allowed-fg-field" gradientUnits="userSpaceOnUse" '
            f'x1="0" y1="0" x2="0" y2="{GRID_H_PX}">',
            f'<stop offset="0%" stop-color="{FG_GRADIENT[0]}"/>',
            f'<stop offset="100%" stop-color="{FG_GRADIENT[1]}"/>',
            '</linearGradient>',
            '</defs>',
        ]
        for row, col, ch in glyphs:
            cell_x = col * CELL_W_PX
            cell_y = row * CELL_H_PX
            glyph_layer.append(
                f'<text class="allowed-glyph" '
                f'x="{cell_x}" y="{cell_y + GLYPH_BASELINE_PX}" '
                f'textLength="{CELL_W_PX}" lengthAdjust="spacingAndGlyphs" '
                f'data-cell-col="{col}" data-cell-row="{row}" '
                f'data-cell-x="{cell_x}" data-cell-y="{cell_y}" '
                f'data-cell-width="{CELL_W_PX}" data-cell-height="{CELL_H_PX}">'
                f'{escape(ch)}</text>'
            )
        glyph_layer.append('</svg>')
        return (
            f'<div class="grid {kind}" data-fg-owner="allowed-fg-grid-svg" '
            f'data-bg-owner="allowed-bg-grid">'
            + "".join(glyph_layer)
            + "".join(cells)
            + "</div>"
        )
    return f'<div class="grid {kind}">' + "".join(cells) + "</div>"


def build_html() -> str:
    rng = _rng()
    allowed = _grid("allowed", rng)
    suspect = _grid("suspect", rng)
    fg_a, fg_b = FG_GRADIENT
    bg_a, bg_b = BG_GRADIENT
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cell-mask coherence demonstrator (stable requirement, FL-4740)</title>
<style>
  body {{ background:#0b0b10; color:#d8d8e2; font-family: -apple-system, sans-serif; margin:24px; }}
  h1 {{ font-size:20px; }}
  h2 {{ font-size:16px; margin:28px 0 8px; }}
  p  {{ max-width:72em; line-height:1.45; }}
  .grid {{
    position:relative;
    display:grid;
    grid-template-columns: repeat({GRID_COLS}, {CELL_W_PX}px);
    grid-auto-rows: {CELL_H_PX}px;
    gap:0; width:max-content; border:1px solid #333;
  }}
  .grid.allowed {{
    /* One framebuffer-wide background field. It crosses every shared ANSI
       edge; no allowed cell owns a local background gradient. */
    background:linear-gradient(180deg, {bg_a}, {bg_b});
    background-size:100% 100%;
  }}
  .cell {{
    position:relative; overflow:hidden;
    width:{CELL_W_PX}px; height:{CELL_H_PX}px;
    display:flex; align-items:center; justify-content:center;
  }}
  .grid.allowed .cell {{ background:transparent; z-index:2; }}
  /* One whole glyph mask per cell. One SVG text layer owns the complete
     grid-wide foreground paint server, with each glyph's origin, 44px
     advance, and 64px row encoded against the same cell grid. */
  .global-ink {{
    position:absolute; inset:0; z-index:1; pointer-events:none;
    width:100%; height:100%; overflow:visible;
  }}
  .allowed-glyph {{
    fill:url(#allowed-fg-field); font:700 {GLYPH_FONT_SIZE_PX}px/1
      "Menlo", "Consolas", monospace;
    text-rendering:geometricPrecision; shape-rendering:geometricPrecision;
  }}
  /* Suspect panel retains deliberately local ink/background owners so its
     fragmented quilt remains a separate defect illustration. */
  .grid.suspect .cell {{ background:linear-gradient(180deg, {bg_a}, {bg_b}); }}
  .grid.suspect .ink {{
    font: 700 52px/1 "Menlo", "Consolas", monospace;
    background:linear-gradient(180deg, {fg_a}, {fg_b});
    -webkit-background-clip:text; background-clip:text; color:transparent;
  }}
  /* Suspect subcell fragment: an arbitrary window inside the cell showing
     a clipped, offset piece of a DIFFERENT glyph. */
  .frag {{ position:absolute; overflow:hidden; }}
  .frag-ink {{
    position:absolute; left:0; top:0;
    font: 700 52px/1 "Menlo", "Consolas", monospace;
    background:linear-gradient(180deg, {fg_b}, {fg_a});
    -webkit-background-clip:text; background-clip:text; color:transparent;
  }}
  /* The same rectangular HTML-dump selection in both panels. */
  .selected {{ outline:3px dashed #ffd166; outline-offset:-3px; z-index:2; }}
  .caption {{ font-size:13px; color:#9a9ab0; margin-top:6px; }}
  .status {{ font-size:13px; color:#c9a227; }}
</style>
</head>
<body>
<h1>Cell-mask coherence / subcell glyph fragmentation demonstrator</h1>
<p class="status">
STABLE REQUIREMENT under FL-4740 - this is not current-head proof. The page
defines the required whole-cell mask and the permitted continuous global FG/BG
fields. The allowed SVG encodes every glyph origin, 44px advance, and 64px
row against the exact overlay cells. Commit d959bc8c27 is the superseded compositor-modal implementation.
Commit 60fbc98baf is the current Product semantic-election implementation.
Current-head visual, temporal, and performance proof remain separate and
missing; this demonstrator is not proof.
</p>
<p>
Stable requirement: for every terminal cell C there is exactly one selected
 glyph_id[C]; a native pixel inside C is foreground iff the whole glyph
 mask of glyph_id[C] covers it in cell-local coordinates. One continuous
 framebuffer-wide FG field and one continuous framebuffer-wide BG field are
 sampled on the quarter-center lattice across shared ANSI-cell edges; neither
 field restarts at a cell boundary. The mask is hard/discrete, with no
 derivative-AA blur. Mask absence paints one cell-wide 0xDB diagnostic and
 never revives a quarter-local glyph.
The dashed rectangle is the same HTML-dump cell selection in both panels.
</p>

<h2>ALLOWED - one whole glyph per cell, global FG/BG fields across cell edges</h2>
{allowed}
<div class="caption">The selected rectangle contains exactly one whole glyph silhouette. One grid-wide FG field and one grid-wide BG field cross its shared ANSI edges without restarting at this cell; the SVG glyph origin and 44x64 advance are exact cell coordinates.</div>

<h2>SUSPECT - subcell fragments each bringing their own glyph mask (quilt / cut patterned paper)</h2>
{suspect}
<div class="caption">The identical selected rectangle now contains cut pieces of several glyphs: geometry/material ownership chosen per subcell region replaced parts of the selected mask. This is the reported defect appearance.</div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out",
        default=".run/ascii_cell_mask_demo/index.html",
        help="Output HTML path (default: .run/ascii_cell_mask_demo/index.html)",
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_html(), encoding="utf-8")
    print(f"[ascii-cell-mask-demo] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
