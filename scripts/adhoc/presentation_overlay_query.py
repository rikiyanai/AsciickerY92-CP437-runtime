#!/usr/bin/env python3
"""Query presentation_overlay_dump JSON output with structured filters.

Wraps ./output/presentation_overlay_dump <file.xp> and provides structured
output for common queries: frame metadata, cell statistics, non-transparent
cell counts, and per-frame summaries. Avoids repeating the same python3 -c
pipe commands for every sprite inspection.

Requires: ./output/presentation_overlay_dump (compiled binary in repo root).

Origin: Codex agent transcript — repeated patterns of
  ./output/presentation_overlay_dump <file.xp> | python3 -c "import json,sys; ..."
  during wolack/bigbee attack rider overlay generation research.

Generalized: wrapped the binary + JSON parse + query logic into a single CLI
  with --angles, --frames, --frame N, --cells, --non-empty flags.

Usage:
  python3 scripts/adhoc/presentation_overlay_query.py assets/sprites/wolack-attack-body.xp
  python3 scripts/adhoc/presentation_overlay_query.py <file.xp> --angles --summary
  python3 scripts/adhoc/presentation_overlay_query.py <file.xp> --frame 0 --detail
  python3 scripts/adhoc/presentation_overlay_query.py <file.xp> --non-empty
  python3 scripts/adhoc/presentation_overlay_query.py <file.xp> --json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DUMP_BIN = REPO_ROOT / "output" / "presentation_overlay_dump"


def run_dump(xp_path: Path) -> dict:
    """Run presentation_overlay_dump and return parsed JSON."""
    if not DUMP_BIN.is_file():
        print(f"error: dump binary not found: {DUMP_BIN}", file=sys.stderr)
        sys.exit(1)
    if not xp_path.is_file():
        print(f"error: XP file not found: {xp_path}", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run(
        [str(DUMP_BIN), str(xp_path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        print(f"error: dump failed: {stderr}", file=sys.stderr)
        sys.exit(1)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"error: invalid JSON from dump: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xp_file", type=Path, help="XP file to inspect")
    parser.add_argument("--json", action="store_true",
                        help="Emit raw dump JSON")
    parser.add_argument("--summary", action="store_true",
                        help="Brief summary only")
    parser.add_argument("--angles", action="store_true",
                        help="Show angles/projs/anims config")
    parser.add_argument("--frames", action="store_true",
                        help="Show all frame metadata")
    parser.add_argument("--frame", type=int, default=None,
                        help="Show detail for specific frame index")
    parser.add_argument("--non-empty", action="store_true",
                        help="Count non-transparent cells per frame")
    parser.add_argument("--detail", action="store_true",
                        help="Full detail: metadata + cell stats")
    args = parser.parse_args()

    data = run_dump(args.xp_file)

    if args.json:
        json.dump(data, sys.stdout, indent=2)
        print()
        return

    angles = data.get("angles", "?")
    projs = data.get("projs", "?")
    anims = data.get("anims", "?")
    frames = data.get("frames", [])
    n_frames = len(frames)

    # Always show header
    print(f"file: {args.xp_file}")
    print(f"angles={angles}  projs={projs}  anims={anims}  frames={n_frames}")
    print()

    if args.summary:
        return

    if args.angles:
        # Already shown in header
        return

    if args.non_empty or args.detail:
        print(f"{'Frame':>5s} {'ref':24s} {'meta':12s} {'w':>4s} {'h':>4s} {'cells':>6s} {'non-empty':>10s}")
        print("-" * 72)
        for i, f in enumerate(frames):
            ref = f.get("ref", "?")
            meta = f.get("meta", "?")
            w = f.get("width", "?")
            h = f.get("height", "?")
            cells = f.get("cells", [])
            non_empty = sum(1 for c in cells if len(c) > 1 and (c[1] != 255 or c[0] != 32))
            print(f"{i:5d} {str(ref):24s} {str(meta):12s} {str(w):>4s} {str(h):>4s} {len(cells):6d} {non_empty:10d}")
        print()

    if args.frames:
        for i, f in enumerate(frames):
            print(f"Frame {i}: ref={f.get('ref', '?')}  meta={f.get('meta', '?')}  "
                  f"size={f.get('width', '?')}x{f.get('height', '?')}")

    if args.frame is not None:
        if args.frame >= len(frames):
            print(f"error: frame {args.frame} out of range (0-{len(frames)-1})", file=sys.stderr)
            sys.exit(1)
        f = frames[args.frame]
        print(f"Frame {args.frame}:")
        print(f"  ref={f.get('ref', '?')}")
        print(f"  meta={f.get('meta', '?')}")
        print(f"  size={f.get('width', '?')}x{f.get('height', '?')}")
        cells = f.get("cells", [])
        print(f"  cells={len(cells)}")
        non_empty = [(ci, c) for ci, c in enumerate(cells) if len(c) > 1 and (c[1] != 255 or c[0] != 32)]
        print(f"  non-transparent={len(non_empty)}")
        if non_empty:
            print(f"  first non-empty: idx={non_empty[0][0]} glyph={non_empty[0][1][0]} "
                  f"fg=#{non_empty[0][1][1]:02x}{non_empty[0][1][2]:02x}{non_empty[0][1][3]:02x}"
                  if len(non_empty[0][1]) > 3 else f"  first non-empty: {non_empty[0]}")


if __name__ == "__main__":
    main()
