#!/usr/bin/env python3
"""Set each mesh instance's Z to the live terrain height under it.

FL-4787 support utility.

The canonical FL-1144 probe used to shell out to the old editor binary and silently fell
back to ``Z = 128`` (world z = 0) when that binary is absent. On a map whose
terrain sits at 17-32 world units that fallback buries every fixture and tree
underground, so a map assembled without a built presenter looks like it has no
props at all.

This reads the terrain heightfield straight out of the .a3d and interpolates it,
which needs no binary and no engine. Convention confirmed against the shipped
map: ``transform[14]`` holds the RAW patch height value (bake units, i.e.
``128 + z_world * 16``) at the instance's XY, which is what the shipped fixtures
carry to within rounding.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

PATCH_SIZE = 188
HEADER_SIZE = 16
PATCH_WORLD = 8.0
HEIGHT_STEP_WORLD = 2.0
HEIGHT_CELLS = 4


def load_height_grid(map_path: Path) -> dict[tuple[int, int], tuple[int, ...]]:
    data = map_path.read_bytes()
    sig, _hdr, npatch = struct.unpack_from("<4sII", data, 0)
    if sig != b"AS3D":
        raise SystemExit(f"{map_path} is not an A3D file")
    grid: dict[tuple[int, int], tuple[int, ...]] = {}
    for i in range(npatch):
        base = HEADER_SIZE + i * PATCH_SIZE
        px, py = struct.unpack_from("<ii", data, base)
        grid[(px, py)] = struct.unpack_from("<25H", data, base + 8 + 128)
    return grid


def terrain_z(grid, wx: float, wy: float) -> float | None:
    """Bilinear sample of the 5x5 height vertices, in raw bake units."""
    px = int(wx // PATCH_WORLD)
    py = int(wy // PATCH_WORLD)
    h = grid.get((px, py))
    if h is None:
        return None
    fx = (wx - px * PATCH_WORLD) / HEIGHT_STEP_WORLD
    fy = (wy - py * PATCH_WORLD) / HEIGHT_STEP_WORLD
    ix = min(HEIGHT_CELLS - 1, max(0, int(fx)))
    iy = min(HEIGHT_CELLS - 1, max(0, int(fy)))
    tx, ty = fx - ix, fy - iy

    def g(a: int, b: int) -> int:
        return h[min(HEIGHT_CELLS, b) * (HEIGHT_CELLS + 1) + min(HEIGHT_CELLS, a)]

    return (g(ix, iy) * (1 - tx) * (1 - ty)
            + g(ix + 1, iy) * tx * (1 - ty)
            + g(ix, iy + 1) * (1 - tx) * ty
            + g(ix + 1, iy + 1) * tx * ty)


def probe(map_path: Path, instances_path: Path, z_offset: float = 0.0) -> dict:
    grid = load_height_grid(map_path)
    rows = json.loads(instances_path.read_text(encoding="utf-8"))

    probed = 0
    off_map = 0
    kept: list[dict] = []
    for row in rows:
        t = row.get("transform")
        if not t or len(t) < 16:
            continue
        z = terrain_z(grid, float(t[12]), float(t[13]))
        if z is None:
            off_map += 1
            continue
        t[14] = round(z + z_offset, 3)
        probed += 1
        kept.append(row)

    instances_path.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")
    return {
        "input": len(rows),
        "probed": probed,
        "dropped_off_map": off_map,
        "map": str(map_path),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--instances", type=Path, required=True,
                    help="instance JSON; rewritten in place with live Z")
    ap.add_argument("--z-offset", type=float, default=0.0,
                    help="raw bake units added on top of terrain")
    args = ap.parse_args(argv)

    summary = probe(args.map, args.instances, args.z_offset)
    print(f"[probe-z] {args.instances.name}: probed={summary['probed']}/{summary['input']} "
          f"dropped_off_map={summary['dropped_off_map']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
