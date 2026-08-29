#!/usr/bin/env python3
"""List all XP sprite files with dimensions, layer counts, and inferred type.

Scans assets/sprites/ (or --sprite-dir) for .xp files and prints a table of
sheet dimensions, layer counts, and sprite type classification. Useful for
quickly surveying what sprites exist and their basic geometry.

Origin: Codex session history — multiple python3 -c one-liners that read XP
  headers via gzip+struct to enumerate player-*.xp, bigbee-*.xp, wolack-*.xp
  dimensions (codex history.jsonl, various entries around wolack/bigbee/player
  sprite research).

Generalized: replaced hardcoded glob patterns with --filter/--prefix flags,
  added type inference from xp_raw_layer_inspector._infer_sprite_type logic.

Usage:
  python3 scripts/adhoc/sprite_inventory.py                    # all sprites
  python3 scripts/adhoc/sprite_inventory.py --prefix player    # player only
  python3 scripts/adhoc/sprite_inventory.py --prefix bigbee    # bigbee only
  python3 scripts/adhoc/sprite_inventory.py --filter attack    # attack sprites
  python3 scripts/adhoc/sprite_inventory.py --prefix player --json  # JSON output
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPRITE_DIR = REPO_ROOT / "assets" / "sprites"


def _infer_sprite_type(name: str) -> str:
    """Infer sprite type from filename (mirrors xp_raw_layer_inspector logic)."""
    lower = name.lower()
    if lower.startswith("wolack") or "attack" in lower:
        return "attack"
    if lower.startswith("plydie-") or "death" in lower or "corpse" in lower:
        return "plydie"
    if lower.startswith("wolfie"):
        return "wolfie"
    if lower.startswith("bigbee") and "attack" not in lower:
        return "bigbee"
    return "player"


def read_xp_header(path: Path) -> tuple[int | None, int | None, int | None]:
    """Read XP file header: returns (width, height, num_layers) or (None,None,None)."""
    try:
        with gzip.open(path, "rb") as f:
            data = f.read(16)  # enough for version + layer_count + w + h
        offset = 0
        version = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        num_layers = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        w = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        h = struct.unpack_from("<i", data, offset)[0]
        return w, h, num_layers
    except Exception:
        return None, None, None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sprite-dir", type=Path, default=DEFAULT_SPRITE_DIR,
                        help="Sprite directory (default: assets/sprites)")
    parser.add_argument("--prefix", type=str, default="",
                        help="Filter by filename prefix (e.g. player, bigbee, wolack)")
    parser.add_argument("--filter", type=str, default="",
                        help="Filter by substring in filename (e.g. attack, armor, body)")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON array instead of table")
    args = parser.parse_args()

    sprite_dir = args.sprite_dir
    if not sprite_dir.is_dir():
        print(f"error: sprite dir not found: {sprite_dir}", file=sys.stderr)
        sys.exit(1)

    xp_files = sorted(
        p for p in sprite_dir.iterdir()
        if p.suffix == ".xp"
        and (not args.prefix or p.stem.startswith(args.prefix))
        and (not args.filter or args.filter in p.stem)
    )

    results: list[dict] = []
    for path in xp_files:
        w, h, nl = read_xp_header(path)
        sprite_type = _infer_sprite_type(path.stem)
        results.append({
            "name": path.name,
            "stem": path.stem,
            "sheet_w": w,
            "sheet_h": h,
            "layers": nl,
            "type": sprite_type,
            "path": str(path.relative_to(REPO_ROOT)),
        })

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        print()
        return

    # Table output
    print(f"{'Name':40s} {'Type':8s} {'Sheet':12s} {'Layers':>6s}")
    print("-" * 70)
    for r in results:
        sheet = f"{r['sheet_w']}x{r['sheet_h']}" if r['sheet_w'] else "ERR"
        layers = str(r['layers']) if r['layers'] is not None else "ERR"
        print(f"{r['name']:40s} {r['type']:8s} {sheet:12s} {layers:>6s}")

    print(f"\nTotal: {len(results)} sprites")

    # Summary by type
    from collections import Counter
    type_counts = Counter(r["type"] for r in results)
    print("By type:", ", ".join(f"{t}={c}" for t, c in sorted(type_counts.items())))


if __name__ == "__main__":
    main()
