#!/usr/bin/env python3
"""Parity check: collapse mount wrapper outputs and compare against the
upstream mounted monolith's rendered state, per (angle, anim, frame).

For each frame of wolfie-0100.xp (mounted monolith with helmet+rider+wolf):
  expected_visible = visible cells of wolfie-0100.xp L2 (mounted composite)

For each frame of the wrapper triple at the same atlas position:
  produced_visible = (
        visible cells of wolfie-body-rear.xp L2
      | visible cells of wolfie-mounted-idle-rider-body.xp L2
      | visible cells of wolfie-body-front.xp L2
  )

Parity: every cell of expected_visible must be in produced_visible AND
the produced glyph/fg/bg must match the expected glyph/fg/bg at that cell.

Reports per-frame counts: expected_total, produced_total, matched,
missing_from_produced, extra_in_produced, mismatched_color.

Exits 0 if all frames match; non-zero if any frame has mismatches.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.pipeline import mounted_rider_offset as mro
from scripts.pipeline.xp_core import XPFile


def collect_frame_cells(xp: XPFile, layout, *, layer_index: int):
    """Return dict[(angle, anim_index, frame_index, proj)] -> list[VisibleCell]."""
    out = {}
    for angle in range(layout.angles):
        for anim_index, anim_len in enumerate(layout.anims):
            for frame_index in range(anim_len):
                for proj in range(layout.projs):
                    cells = list(mro.frame_cells(
                        xp, layout,
                        angle=angle,
                        anim_index=anim_index,
                        frame_index=frame_index,
                        proj=proj,
                        layer_index=layer_index,
                    ))
                    out[(angle, anim_index, frame_index, proj)] = cells
    return out


def cells_to_map(cells):
    return {(c.x, c.y): (c.glyph, c.fg, c.bg) for c in cells}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--monolith", default="assets/sprites/wolfie-0100.xp")
    ap.add_argument("--monolith-layer", type=int, default=2,
                    help="Layer index in monolith to treat as expected rendered state (L2)")
    ap.add_argument("--rear", default="assets/sprites/wolfie-body-rear.xp")
    ap.add_argument("--rider", default="assets/sprites/wolfie-mounted-idle-rider-body.xp")
    ap.add_argument("--front", default="assets/sprites/wolfie-body-front.xp")
    ap.add_argument("--max-print", type=int, default=20,
                    help="Maximum mismatched cells to print per category")
    ap.add_argument("--abort-after", type=int, default=0,
                    help="Abort scan after this many mismatched frames (0=scan all)")
    args = ap.parse_args()

    monolith_xp = mro._load_xp(Path(args.monolith))
    rear_xp = mro._load_xp(Path(args.rear))
    rider_xp = mro._load_xp(Path(args.rider))
    front_xp = mro._load_xp(Path(args.front))

    mon_layout = mro._parse_layout(monolith_xp)
    rear_layout = mro._parse_layout(rear_xp)
    rider_layout = mro._parse_layout(rider_xp)
    front_layout = mro._parse_layout(front_xp)

    print(f"monolith {args.monolith}: angles={mon_layout.angles} anims={mon_layout.anims} projs={mon_layout.projs} frame={mon_layout.frame_width}x{mon_layout.frame_height}")
    print(f"rear     {args.rear}: angles={rear_layout.angles} anims={rear_layout.anims} projs={rear_layout.projs} frame={rear_layout.frame_width}x{rear_layout.frame_height}")
    print(f"rider    {args.rider}: angles={rider_layout.angles} anims={rider_layout.anims} projs={rider_layout.projs} frame={rider_layout.frame_width}x{rider_layout.frame_height}")
    print(f"front    {args.front}: angles={front_layout.angles} anims={front_layout.anims} projs={front_layout.projs} frame={front_layout.frame_width}x{front_layout.frame_height}")
    print()

    # Quick topology gate. All four must share the same per-frame grid.
    bad_topology = False
    for name, lay in [("rear", rear_layout), ("rider", rider_layout), ("front", front_layout)]:
        if (lay.angles, lay.anims, lay.projs, lay.frame_width, lay.frame_height) != \
           (mon_layout.angles, mon_layout.anims, mon_layout.projs, mon_layout.frame_width, mon_layout.frame_height):
            print(f"TOPOLOGY MISMATCH on {name}:")
            print(f"  monolith: angles={mon_layout.angles} anims={mon_layout.anims} projs={mon_layout.projs} frame={mon_layout.frame_width}x{mon_layout.frame_height}")
            print(f"  {name}:     angles={lay.angles} anims={lay.anims} projs={lay.projs} frame={lay.frame_width}x{lay.frame_height}")
            bad_topology = True
    if bad_topology:
        print("\nABORT: cannot do parity with topology mismatch")
        sys.exit(2)

    # Gather per-frame cells
    mon_cells = collect_frame_cells(monolith_xp, mon_layout, layer_index=args.monolith_layer)
    rear_cells = collect_frame_cells(rear_xp, rear_layout, layer_index=2)
    rider_cells = collect_frame_cells(rider_xp, rider_layout, layer_index=2)
    front_cells = collect_frame_cells(front_xp, front_layout, layer_index=2)

    total_frames = len(mon_cells)
    perfect_frames = 0
    mismatched_frames = 0
    missing_total = 0
    extra_total = 0
    color_mismatch_total = 0

    sample_missing = []
    sample_extra = []
    sample_color = []

    keys = sorted(mon_cells.keys())
    for key in keys:
        ang, anim, frame, proj = key
        expected = cells_to_map(mon_cells.get(key, []))
        produced_rear = cells_to_map(rear_cells.get(key, []))
        produced_rider = cells_to_map(rider_cells.get(key, []))
        produced_front = cells_to_map(front_cells.get(key, []))
        # Produced cells = union, paste order rear -> rider -> front (later wins)
        produced = {}
        produced.update(produced_rear)
        produced.update(produced_rider)
        produced.update(produced_front)

        missing = [(xy, expected[xy]) for xy in expected if xy not in produced]
        extra = [(xy, produced[xy]) for xy in produced if xy not in expected]
        color_diff = [
            (xy, expected[xy], produced[xy])
            for xy in expected
            if xy in produced and expected[xy] != produced[xy]
        ]

        if not missing and not extra and not color_diff:
            perfect_frames += 1
        else:
            mismatched_frames += 1
            missing_total += len(missing)
            extra_total += len(extra)
            color_mismatch_total += len(color_diff)
            if len(sample_missing) < args.max_print:
                for xy, val in missing[:args.max_print - len(sample_missing)]:
                    sample_missing.append((key, xy, val))
            if len(sample_extra) < args.max_print:
                for xy, val in extra[:args.max_print - len(sample_extra)]:
                    sample_extra.append((key, xy, val))
            if len(sample_color) < args.max_print:
                for xy, exp, got in color_diff[:args.max_print - len(sample_color)]:
                    sample_color.append((key, xy, exp, got))
            if args.abort_after > 0 and mismatched_frames >= args.abort_after:
                print(f"\n(scan aborted after {args.abort_after} mismatched frames)")
                break

    print()
    print(f"frames scanned:      {len(keys)}")
    print(f"perfect frames:      {perfect_frames}")
    print(f"mismatched frames:   {mismatched_frames}")
    print(f"missing total cells: {missing_total}  (in monolith, NOT in wrappers — wrapper has GAPS)")
    print(f"extra total cells:   {extra_total}    (in wrappers, NOT in monolith — wrapper has STRAYS)")
    print(f"color mismatch:      {color_mismatch_total} (cell present in both but glyph/fg/bg differ)")
    print()

    if sample_missing:
        print(f"=== Sample missing cells (first {len(sample_missing)}): (key, (x,y), expected) ===")
        for key, xy, val in sample_missing[:args.max_print]:
            print(f"  frame ang={key[0]} anim={key[1]} f={key[2]} proj={key[3]} (x,y)={xy} expected={val}")
    if sample_extra:
        print(f"\n=== Sample extra cells (first {len(sample_extra)}): (key, (x,y), got) ===")
        for key, xy, val in sample_extra[:args.max_print]:
            print(f"  frame ang={key[0]} anim={key[1]} f={key[2]} proj={key[3]} (x,y)={xy} got={val}")
    if sample_color:
        print(f"\n=== Sample color mismatch (first {len(sample_color)}): (key, (x,y), expected, got) ===")
        for key, xy, exp, got in sample_color[:args.max_print]:
            print(f"  frame ang={key[0]} anim={key[1]} f={key[2]} proj={key[3]} (x,y)={xy} expected={exp} got={got}")

    if mismatched_frames == 0:
        print("\nPARITY: PASS")
        sys.exit(0)
    else:
        print(f"\nPARITY: FAIL ({mismatched_frames}/{len(keys)} frames)")
        sys.exit(1)


if __name__ == "__main__":
    main()
