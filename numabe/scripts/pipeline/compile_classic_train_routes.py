"""Compile Classic train routes from canonical rail centrelines (FL-4876 TRAIN-ROUTE-01).

The Classic runtime moves one train mesh per route along a polyline in map
units.  This owner selects the canonical railway centrelines whose OSM line
identity matches the configured operator line, chains them by shared end
points into running tracks, samples ground height from the DEM-only terrain
(so baked buildings and platform canopies never lift the train), and projects
the station anchor onto each track as the stop chainage.

Output schema ``y92.classic_train_routes.v1`` (sibling of the map,
``<map stem>.trains.json``)::

    {"schema": ..., "mesh": "<akm name>", "units": "classic_map",
     "routes": [{"id", "feature_ids", "polyline": [[x, y, z], ...],
                 "length_m", "stop_s_m", "units_per_metre",
                 "timetable": {"period_s", "phase_s", "dwell_s",
                               "cruise_mps", "accel_mps2"}}]}

usage: compile_classic_train_routes.py --canonical C --terrain-metadata T
       --dem-a3d D --out OUT [--line-name-en NAME] [--mesh NAME]
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "docs" / "agent" / "cli-anything")]
from a3d_edit import _load_a3d_format  # noqa: E402  (same reader as classic_traversal_capture)

SCHEMA = "y92.classic_train_routes.v1"
CHAIN_JOIN_M = 1.0
DENSIFY_M = 2.0
SMOOTH_HALF_M = 15.0
# Tokyu Tamagawa Line: 3-car sets, about 6 minutes headway off-peak.  The
# runtime period is compressed for presence (one pass per direction every
# PERIOD_S); the stop, dwell and speeds stay plausible for a branch line.
PERIOD_S = 150.0
DWELL_S = 20.0
CRUISE_MPS = 12.0
ACCEL_MPS2 = 0.8


class LineAbsent(Exception):
    """The configured line, or a station to stop at, is not in canonical."""


def _tags(feature: dict, observations: dict) -> dict:
    tags: dict = {}
    for oid in feature.get("supporting_observation_ids") or []:
        tags.update(((observations.get(oid) or {}).get("attributes") or {})
                    .get("tags") or {})
    return tags


def _chains(lines: list[tuple[str, list]]) -> list[tuple[list[str], list]]:
    """Join polylines whose end points meet within CHAIN_JOIN_M."""
    pending = [(fid, [tuple(p[:2]) for p in pl]) for fid, pl in lines]
    out = []
    while pending:
        fids, pl = [pending[0][0]], list(pending[0][1])
        pending.pop(0)
        grown = True
        while grown:
            grown = False
            for i, (fid, q) in enumerate(pending):
                if math.dist(pl[-1], q[0]) <= CHAIN_JOIN_M:
                    pl += q[1:]
                elif math.dist(pl[-1], q[-1]) <= CHAIN_JOIN_M:
                    pl += q[::-1][1:]
                elif math.dist(pl[0], q[-1]) <= CHAIN_JOIN_M:
                    pl = q[:-1] + pl
                elif math.dist(pl[0], q[0]) <= CHAIN_JOIN_M:
                    pl = q[::-1][:-1] + pl
                else:
                    continue
                fids.append(fid)
                pending.pop(i)
                grown = True
                break
        out.append((fids, pl))
    return out


def _length(pl: list) -> float:
    return sum(math.dist(a[:2], b[:2]) for a, b in zip(pl, pl[1:]))


def _project_s(pl: list, pt: tuple) -> tuple[float, float]:
    """(chainage, distance) of the nearest point of pl to pt."""
    best = (0.0, float("inf"))
    s0 = 0.0
    for a, b in zip(pl, pl[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        L = math.sqrt(L2)
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L2))
        d = math.dist(pt, (a[0] + t * dx, a[1] + t * dy))
        if d < best[1]:
            best = (s0 + t * L, d)
        s0 += L
    return best


class _Ground:
    """Bilinear ground height from an A3D terrain (same arithmetic as the
    player-start embed in classic_traversal_capture.embed_player_start)."""

    def __init__(self, a3d: Path):
        fmt = _load_a3d_format()
        with open(a3d, "rb") as f:
            if f.read(4) != b"AS3D":
                raise ValueError(f"not an A3D file: {a3d}")
            header = struct.unpack("<I", f.read(4))[0]
            n = struct.unpack("<I", f.read(4))[0]
            f.seek(header)
            self.patches = {(p.x, p.y): p for p in (fmt.A3DPatch.from_file(f) for _ in range(n))}
        self.world = float(fmt.VISUAL_CELLS)
        self.hc = int(fmt.HEIGHT_CELLS)

    def __call__(self, x: float, y: float) -> float | None:
        key = (int(x // self.world), int(y // self.world))
        p = self.patches.get(key)
        if p is None:
            return None
        lx, ly = x - key[0] * self.world, y - key[1] * self.world
        step = self.world / self.hc
        fx, fy = lx / step, ly / step
        x0, y0 = min(self.hc - 1, int(fx)), min(self.hc - 1, int(fy))
        x1, y1 = min(self.hc, x0 + 1), min(self.hc, y0 + 1)
        tx, ty = fx - x0, fy - y0
        h = p.height
        a = h[y0][x0] + (h[y0][x1] - h[y0][x0]) * tx
        b = h[y1][x0] + (h[y1][x1] - h[y1][x0]) * tx
        return float(a + (b - a) * ty)


def build(canonical: Path, terrain_metadata: Path, dem_a3d: Path,
          line_name_en: str, mesh: str) -> dict:
    canon = json.loads(canonical.read_text(encoding="utf-8"))
    meta = json.loads(terrain_metadata.read_text(encoding="utf-8"))
    sc = float(meta["content_scale"])
    E0, N0 = float(meta["origin_easting"]), float(meta["origin_northing"])
    ox, oy = float(meta["offset_x"]), float(meta["offset_y"])
    obs = {o["observation_id"]: o for o in canon.get("source_observations", [])}
    lines, entrances = [], []
    for f in canon["canonical_features"]:
        g = f.get("geometry") or {}
        if f.get("semantic_class") == "station_entrance" and g.get("kind") == "point":
            entrances.append(tuple(g["coordinates"][:2]))
        if f.get("semantic_class") != "railway_boundary" or g.get("kind") != "polyline":
            continue
        t = _tags(f, obs)
        if t.get("name:en") == line_name_en and t.get("railway") == "rail":
            lines.append((f["feature_id"], g["coordinates"]))
    if not lines:
        raise LineAbsent(f"no canonical rail centreline named {line_name_en!r}")
    if not entrances:
        raise LineAbsent("no canonical station_entrance anchors the stop")
    station = (sum(p[0] for p in entrances) / len(entrances),
               sum(p[1] for p in entrances) / len(entrances))
    ground = _Ground(dem_a3d)
    chains = sorted(_chains(lines), key=lambda c: -_length(c[1]))
    routes = []
    for k, (fids, pl) in enumerate(chains[:2]):
        # Canonical tracks run past the compiled terrain: densify every
        # DENSIFY_M and keep the longest contiguous run that has ground.
        dense = [pl[0]]
        for a, b in zip(pl, pl[1:]):
            n = max(1, int(math.ceil(math.dist(a, b) / DENSIFY_M)))
            dense += [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n)
                      for i in range(1, n + 1)]
        runs, cur = [], []
        for E, N in dense:
            x, y = ox + (E - E0) * sc, oy + (N - N0) * sc
            z = ground(x, y)
            if z is None:
                if cur:
                    runs.append(cur)
                cur = []
                continue
            cur.append(((E, N), [round(x, 3), round(y, 3), round(z, 3)]))
        if cur:
            runs.append(cur)
        if not runs:
            raise SystemExit(f"route has no vertex on the terrain: {fids}")
        best = max(runs, key=len)
        pl = [en for en, _ in best]
        poly = [xyz for _, xyz in best]
        # Track grade, not DEM noise: median over +-SMOOTH_HALF_M drops
        # edge spikes and baked structure, then a box mean of the same span.
        half_n = max(1, int(SMOOTH_HALF_M / DENSIFY_M))
        zs = [p[2] for p in poly]
        med = [sorted(zs[max(0, i - half_n):i + half_n + 1])[len(zs[max(0, i - half_n):i + half_n + 1]) // 2]
               for i in range(len(zs))]
        for i, p in enumerate(poly):
            win = med[max(0, i - half_n):i + half_n + 1]
            p[2] = round(sum(win) / len(win), 3)
        stop_s, stop_d = _project_s(pl, station)
        L = _length(pl)
        routes.append({
            "id": f"tokyu_tamagawa_{k}",
            "feature_ids": fids,
            "polyline": poly,
            "length_m": round(L, 3),
            "stop_s_m": round(stop_s, 3),
            "stop_station_distance_m": round(stop_d, 3),
            "units_per_metre": sc,
            # Opposite tracks run in opposite directions; the second route
            # starts half a period later so the two trains alternate.
            "direction": 1 if k == 0 else -1,
            "timetable": {"period_s": PERIOD_S, "phase_s": 0.0 if k == 0 else PERIOD_S / 2,
                          "dwell_s": DWELL_S, "cruise_mps": CRUISE_MPS,
                          "accel_mps2": ACCEL_MPS2},
        })
    return {"schema": SCHEMA, "mesh": mesh, "units": "classic_map",
            "line_name_en": line_name_en,
            "ground_source": str(dem_a3d.name),
            "station_anchor_epsg32654": [round(station[0], 3), round(station[1], 3)],
            "provenance": {"geometry": "canonical OSM railway centrelines chained by end point",
                           "ground": "DEM-only terrain bilinear height",
                           "timetable": "synthesized presence timetable (not an observed schedule)"},
            "routes": routes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--canonical", type=Path, required=True)
    ap.add_argument("--terrain-metadata", type=Path, required=True)
    ap.add_argument("--dem-a3d", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--line-name-en", default="Tokyu Tamagawa Line")
    ap.add_argument("--mesh", default="tokyu_7000_3car.akm")
    ap.add_argument("--allow-absent", action="store_true",
                    help="exit 0 without output when canonical has no such line")
    a = ap.parse_args(argv)
    if a.out.exists():
        a.out.unlink()
    try:
        doc = build(a.canonical, a.terrain_metadata, a.dem_a3d, a.line_name_en, a.mesh)
    except LineAbsent as exc:
        if a.allow_absent:
            print(f"[trains] no routes: {exc}")
            return 0
        raise SystemExit(str(exc))
    a.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for r in doc["routes"]:
        print(f"[trains] {r['id']} {len(r['polyline'])} vertices {r['length_m']} m "
              f"stop at {r['stop_s_m']} m ({r['stop_station_distance_m']} m from station)")
    print(f"[trains] wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
