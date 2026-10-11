#!/usr/bin/env python3
"""Inspect a semantic-map anchor JSON: frames, regions, cell counts, affinity.

Reads an anchor JSON file from docs/research/ascii/semantic_maps/ and prints
a summary of frames, regions per frame, cell counts, slot affinities, and
the reference XP path. Useful for auditing semantic maps before/after edits.

Origin: Codex session history — python3 -c one-liners inspecting
  bigbee-0100.json and wolack-0101.json anchors during rider overlay research
  (codex history.jsonl, entries around bigbee/wolack semantic map work).

Generalized: replaced hardcoded anchor path with CLI arg, added --region and
  --frame filters, JSON output mode.

Usage:
  python3 scripts/adhoc/semantic_map_inspector.py docs/research/ascii/semantic_maps/bigbee-0100.json
  python3 scripts/adhoc/semantic_map_inspector.py <anchor.json> --json
  python3 scripts/adhoc/semantic_map_inspector.py <anchor.json> --frame 0
  python3 scripts/adhoc/semantic_map_inspector.py <anchor.json> --region body
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("anchor", type=Path, help="Path to anchor JSON file")
    parser.add_argument("--json", action="store_true", help="Emit full JSON dump")
    parser.add_argument("--frame", type=int, default=None, help="Show only this frame index")
    parser.add_argument("--region", type=str, default="", help="Filter regions by name substring")
    parser.add_argument("--affinity", type=str, default="", help="Filter regions by slot_affinity")
    parser.add_argument("--summary", action="store_true", help="Brief summary only (no per-region detail)")
    args = parser.parse_args()

    anchor_path = args.anchor
    if not anchor_path.is_file():
        print(f"error: anchor file not found: {anchor_path}", file=sys.stderr)
        sys.exit(1)

    with open(anchor_path) as f:
        d = json.load(f)

    if args.json:
        json.dump(d, sys.stdout, indent=2)
        print()
        return

    ref_xp = d.get("reference_xp", "N/A")
    fw = d.get("frame_w", "?")
    fh = d.get("frame_h", "?")
    frames = d.get("frames", {})

    print(f"anchor: {anchor_path.name}")
    print(f"reference_xp: {ref_xp}")
    print(f"frame_w={fw}  frame_h={fh}")
    print(f"frames: {len(frames)}")
    print()

    if args.summary:
        total_regions = sum(len(fv.get("regions", [])) for fv in frames.values())
        total_cells = sum(
            sum(len(r.get("semantic_cells", [])) for r in fv.get("regions", []))
            for fv in frames.values()
        )
        print(f"total regions: {total_regions}")
        print(f"total semantic cells: {total_cells}")
        return

    frame_keys = sorted(frames.keys(), key=int)
    if args.frame is not None:
        frame_keys = [k for k in frame_keys if int(k) == args.frame]

    for fk in frame_keys:
        fv = frames[fk]
        regions = fv.get("regions", [])
        angle = fv.get("angle", "?")

        if args.region:
            regions = [r for r in regions if args.region in r.get("name", "")]
        if args.affinity:
            regions = [r for r in regions if args.affinity in r.get("slot_affinity", "")]

        if not regions:
            continue

        print(f"Frame {fk}  angle={angle}:")
        for reg in regions:
            name = reg.get("name", "?")
            affinity = reg.get("slot_affinity", "?")
            cells = reg.get("semantic_cells", [])
            print(f"  {name:24s} affinity={affinity:10s}  cells={len(cells)}")
        print()


if __name__ == "__main__":
    main()
