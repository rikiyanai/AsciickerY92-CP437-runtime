"""FL-4876 SIGN-WALL-01: where the extruded building wall actually stands.

Buildings are extruded into the A3D terrain, and the bake raises a height
vertex only when every incident cell is inside the footprint
(bake_osm_building_footprints.terrain_support_inside_footprint, FL-4814, "never
relax"). With height vertices 2 world units apart, the visible wall therefore
stands 0.8-1.8 m inside the canonical footprint face that place fronts, custom
sign planes and sign sprites are anchored to. Measured on build_reg_29's own
terrain: 13/13 place fronts 1.0-1.8 m in front of their wall, 59/59 custom
planes 0.5-1.7 m, 0/36 sign sprites hugging.

This module is the one owner of "where is the wall this facade item faces":
it reads the baked map's terrain (the grid Classic and ASCIIID render) and
walks the item's outward normal.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "docs" / "agent" / "cli-anything") not in sys.path:
    sys.path.insert(0, str(ROOT / "docs" / "agent" / "cli-anything"))

HEIGHT_SCALE = 16.0
PROBE_M = (-6.0, 4.0)
STEP_M = 0.1
MIN_HEIGHT_M = 1.5        # a sample this far above street level is building
FOOT_TOL_M = 0.3          # the foot of the wall: within this of street level
STREET_WINDOW_M = 2.0     # street level = lowest sample this far past the wall


class Terrain:
    """Height of a baked map's terrain at world (x, y), bilinear in its patch."""

    def __init__(self, map_path: Path):
        import a3d_edit as A
        fmt = A._load_a3d_format()
        with open(map_path, "rb") as f:
            if f.read(4) != b"AS3D":
                raise ValueError(f"not an A3D map: {map_path}")
            header = struct.unpack("<I", f.read(4))[0]
            count = struct.unpack("<I", f.read(4))[0]
            f.seek(header)
            self.patches = {(p.x, p.y): p for p in (fmt.A3DPatch.from_file(f) for _ in range(count))}
        self.vc, self.hc = float(A.VISUAL_CELLS), int(A.HEIGHT_CELLS)

    def height(self, x: float, y: float):
        px, py = math.floor(x / self.vc), math.floor(y / self.vc)
        p = self.patches.get((px, py))
        if p is None:
            return None
        lx = (x - px * self.vc) / (self.vc / self.hc)
        ly = (y - py * self.vc) / (self.vc / self.hc)
        x0, y0 = min(self.hc - 1, int(lx)), min(self.hc - 1, int(ly))
        tx, ty = lx - x0, ly - y0
        h = p.height
        a = h[y0][x0] + (h[y0][x0 + 1] - h[y0][x0]) * tx
        b = h[y0 + 1][x0] + (h[y0 + 1][x0 + 1] - h[y0 + 1][x0]) * tx
        return a + (b - a) * ty


def wall_foot(terrain: Terrain, x: float, y: float, normal, sc: float):
    """Foot of the wall a facade item at (x, y) faces, along its outward normal.

    Returns None when no extruded wall lies behind the item, else a dict:
    ``foot_m`` (offset along the normal from (x, y) to the foot of the wall;
    negative = the wall is behind), ``wall_m`` (outermost building sample),
    ``street_h`` (terrain height at street level beyond the wall, map units).

    The wall is the outer edge of the building run the item stands in, or of
    the first run behind it. A building across a narrow alley (street side)
    is never this item's wall.
    """
    z_per_m = sc * HEIGHT_SCALE
    nx, ny = normal
    n = math.hypot(nx, ny) or 1.0
    nx, ny = nx / n, ny / n
    k0, k1 = (int(round(v / STEP_M)) for v in PROBE_M)
    offs = [k * STEP_M for k in range(k0, k1 + 1)]
    hs = [terrain.height(x + nx * o * sc, y + ny * o * sc) for o in offs]
    if any(h is None for h in hs):
        return None
    ground = min(hs)
    bld = [h > ground + MIN_HEIGHT_M * z_per_m for h in hs]
    i0 = offs.index(0.0)
    if bld[i0]:
        w = i0
        while w + 1 < len(offs) and bld[w + 1]:
            w += 1
    else:
        w = next((j for j in range(i0, -1, -1) if bld[j]), None)
        if w is None:
            return None
    after = hs[w + 1: w + 1 + int(round(STREET_WINDOW_M / STEP_M))] or [hs[-1]]
    street = min(after)
    foot = next((i for i in range(w + 1, len(offs))
                 if hs[i] <= street + FOOT_TOL_M * z_per_m), len(offs) - 1)
    return {"foot_m": round(offs[foot], 2), "wall_m": round(offs[w], 2),
            "street_h": hs[foot]}
