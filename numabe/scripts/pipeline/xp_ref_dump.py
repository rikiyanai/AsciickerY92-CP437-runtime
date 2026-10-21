#!/usr/bin/env python3
"""xp_ref_dump.py -- dump ref[0..2] metadata from .xp sprite Layer 0.

Usage:
    python3 scripts/pipeline/xp_ref_dump.py assets/sprites/wolfie-body.xp
    python3 scripts/pipeline/xp_ref_dump.py assets/sprites/*.xp --json
    python3 scripts/pipeline/xp_ref_dump.py assets/sprites/wolfie-body.xp assets/sprites/player-body.xp --compare

Layer 0 metadata encoding (matches sprite.cpp GetDigit):
    Row 0: angles (col 0), anim frame counts (col 1..N)
    Row 1: ref[1] per anim (Y projection offset)
    Row 2: ref[2] per anim (Z depth offset -- the one MergeSpriteFrameOnto checks)

The ref[2] values are what the runtime uses for mounted composition gating.
If mount ref[2] != rider ref[2], CompositeSpriteFrameOnto rejects the merge.

Digit glyph encoding: '0'-'9' -> 0-9, 'A'-'Z'/'a'-'z' -> 10-35, else None.
"""

from __future__ import annotations

import argparse
import contextlib
import io as _io
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))

try:
    from scripts.pipeline.xp_core import XPFile
except ImportError:
    from xp_core import XPFile  # type: ignore


def _decode_digit(glyph: int) -> int | None:
    if 48 <= glyph <= 57:
        return glyph - 48
    if 65 <= glyph <= 90:
        return glyph + 10 - 65
    if 97 <= glyph <= 122:
        return glyph + 10 - 97
    return None


def _glyph_label(glyph: int) -> str:
    if 32 <= glyph < 127:
        return chr(glyph)
    return f"0x{glyph:02x}"


def _load_quiet(path: str | Path) -> XPFile:
    """Load an XPFile suppressing constructor stdout."""
    with contextlib.redirect_stdout(_io.StringIO()):
        return XPFile(str(path))


def extract_refs(filepath: str | Path) -> dict:
    """Extract ref metadata from a single .xp file.

    Returns dict with keys: path, size, angles, anims, ref1, ref2, raw.
    ref1/ref2 are lists of decoded ints (one per anim + idle).
    """
    xp = _load_quiet(filepath)

    if not xp.layers:
        return {"path": str(filepath), "error": "no layers"}

    l0 = xp.layers[0]
    w, h = l0.width, l0.height

    # Row 0: angles at col 0, anim frame counts at col 1..N
    angles_glyph = l0.data[0][0][0]
    angles = _decode_digit(angles_glyph)

    anims = []
    for col in range(1, w):
        g = l0.data[0][col][0]
        d = _decode_digit(g)
        if d is None or d == 0:
            break
        anims.append(d)

    # Row 1: ref[1] values (Y projection offset)
    ref1 = []
    ref1_raw = []
    for col in range(min(len(anims) + 1, w)):
        g = l0.data[1][col][0] if 1 < h else 0
        d = _decode_digit(g)
        ref1.append(d)
        ref1_raw.append({"col": col, "glyph": g, "label": _glyph_label(g), "value": d})

    # Row 2: ref[2] values (Z depth offset -- composition gate)
    ref2 = []
    ref2_raw = []
    for col in range(min(len(anims) + 1, w)):
        g = l0.data[2][col][0] if 2 < h else 0
        d = _decode_digit(g)
        ref2.append(d)
        ref2_raw.append({"col": col, "glyph": g, "label": _glyph_label(g), "value": d})

    return {
        "path": str(filepath),
        "size": f"{w}x{h}",
        "angles": angles,
        "anims": anims,
        "ref1": ref1,
        "ref2": ref2,
        "raw": {
            "row1": ref1_raw,
            "row2": ref2_raw,
        },
    }


def print_table(results: list[dict]) -> None:
    """Print a human-readable table of ref metadata."""
    name_width = max((len(Path(r["path"]).name) for r in results if "error" not in r), default=8)
    name_width = max(name_width, 8)

    header = f"{'FILE':<{name_width}}  {'SIZE':>9}  {'ANG':>3}  {'ANIMS':>8}  {'ref[1]':>12}  {'ref[2]':>12}"
    print(header)
    print("-" * len(header))

    for r in results:
        name = Path(r["path"]).name
        if "error" in r:
            print(f"{name:<{name_width}}  ERROR: {r['error']}")
            continue
        anims_str = ",".join(str(a) for a in r["anims"])
        ref1_str = ",".join(str(v) if v is not None else "?" for v in r["ref1"])
        ref2_str = ",".join(str(v) if v is not None else "?" for v in r["ref2"])
        print(
            f"{name:<{name_width}}  {r['size']:>9}  {r['angles'] or '?':>3}  "
            f"{anims_str:>8}  {ref1_str:>12}  {ref2_str:>12}"
        )


def print_compare(results: list[dict]) -> None:
    """Print comparison showing ref[2] match/mismatch across files."""
    valid = [r for r in results if "error" not in r]
    if len(valid) < 2:
        print("Need at least 2 valid files to compare.")
        return

    print_table(results)
    print()

    ref2_sets = {}
    for r in valid:
        key = tuple(r["ref2"])
        ref2_sets.setdefault(key, []).append(Path(r["path"]).name)

    if len(ref2_sets) == 1:
        vals = list(ref2_sets.keys())[0]
        print(f"REF[2] MATCH: all files share ref[2]={list(vals)}")
    else:
        print("REF[2] MISMATCH:")
        for vals, files in sorted(ref2_sets.items()):
            print(f"  ref[2]={list(vals)}: {', '.join(files)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump ref[0..2] metadata from .xp sprite Layer 0.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[0],
    )
    parser.add_argument("files", nargs="+", help=".xp file paths")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    parser.add_argument("--compare", action="store_true", help="Compare ref[2] across files")
    args = parser.parse_args(argv)

    missing = [f for f in args.files if not os.path.exists(f)]
    if missing:
        for m in missing:
            print(f"not found: {m}", file=sys.stderr)
        return 1

    results = [extract_refs(f) for f in args.files]

    if args.json:
        print(json.dumps(results, indent=2))
    elif args.compare:
        print_compare(results)
    else:
        print_table(results)

    if args.compare and not args.json:
        ref2_sets = {tuple(r["ref2"]) for r in results if "error" not in r}
        return 0 if len(ref2_sets) <= 1 else 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
