#!/usr/bin/env python3
"""a3d_terrain_extend.py -- append terrain patches to a built map and save it (FL-4876 MAP-EDIT-01). No rebuild.

Operator 2026-10-16: "TAMA RIVER STILL MISSING"; a finite set of changes is made to the map the way the asciiid
editor does it, never by regenerating the world. a3d_edit.py changes instance records; this tool adds TERRAIN:

  info    MAP.a3d                     patch range, projection, materials in use
  extend  MAP.a3d --bbox E0 N0 E1 N1 --dem DIR [--dem DIR ...] --osm FILE.osm --by WHO --basis TEXT
          [--bbox-world X0 Y0 X1 Y1 ...] [--water-dem-below-m 1.0] [--floodplain-below-m M] [--blend-m 80] [--dry-run]
          [--journal FILE] [--materials water=34,grass=14,land=16,road=28,path=12]
  (--bbox / --bbox-world repeat: the union of the boxes is added. Raw height < 0 cannot be stored; such vertices clamp to 0.)

What it does (engine/terrain.cpp SaveTerrain / LoadTerrain, the same 188-byte records):
  * every patch whose 8 x 8 world-unit square lies in the E/N bbox and is not already in the map is appended.
    Patch coordinates may be negative (the engine quadtree grows in all directions: AddTerrainPatch, terrain.cpp:863;
    the loader accepts |coord| <= 2^24, terrain.cpp:3868). Existing patch records are copied byte for byte and
    no coordinate moves: the projection (terrain_metadata.json) is untouched, only terrain_bounds may grow.
  * heights: GSI DEM5A text tiles (z15 web-mercator, 256 x 256, metres), sampled bilinearly at each vertex
    (vertex (hx, hy) of patch (px, py) is world (8 px + 2 hx, 8 py + 2 hy)), converted with the map's own bake
    (raw = baseline 128 + (elevation - datum) * content_scale * 16, datum from dem_lowering_receipt.json, 6.5 m),
    clamped to uint16. A vertex shared with an existing patch keeps the existing height (no seam), and the
    difference to the DEM is blended away over --blend-m metres so there is no cliff at the old edge.
  * visual cells (uint16 = material id; the engine reads the low byte, the high byte stays 0 as in the map):
    OSM polygons (water, riverbank, multipolygon relations) -> water; grass-like landuse -> grass; roads and
    paths as lines of a class width; everything else -> land. Optional --floodplain-below-m paints land lower than the
    given elevation as grass (the river bed between the levees has no OSM polygon).
  * water has NO level field in the file. It is a material (the carto 'water' role) on a flat patch of terrain; the
    session water level (game.cpp InitGame, ASCIICKER_WATER_LEVEL) is a runtime setting, not map data. Cells
    painted as water therefore have their vertices flattened to the local DEM water surface.
  * diag = 0 like every patch in the map; materials, instances and everything after them are copied verbatim.
Saving: temp file + fsync + os.replace (a3d_edit.save_atomic), previous map kept as <map>.pre_edit_<sha8>, exclusive
fcntl lock on <map>.lock, one record appended to map_edits/journal.jsonl (op "terrain_extend").
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from a3d_edit import keep_pre_edit  # noqa: E402  one owner of the capped pre-edit copies (also used by the terrain tools)

JOURNAL = HERE / "map_edits" / "journal.jsonl"
PATCH_BYTES = 8 + 8 * 8 * 2 + 5 * 5 * 2 + 2  # 188
HDR = 16
PATCH_UNITS = 8      # world units per patch edge (VISUAL_CELLS)
HEIGHT_CELLS = 4
VISUAL_CELLS = 8
MAX_COORD = 16777216  # loader range, terrain.cpp:3868
REC = np.dtype([("x", "<i4"), ("y", "<i4"), ("visual", "<u2", (8, 8)), ("height", "<u2", (5, 5)), ("diag", "<u2")])
assert REC.itemsize == PATCH_BYTES


# --------------------------------------------------------------------------------------------------------------
# geodesy (same series as map_editor.ll_to_utm / utm_to_ll, UTM 54N WGS84; vectorised)
# --------------------------------------------------------------------------------------------------------------
_K0, _A, _F = 0.9996, 6378137.0, 1 / 298.257223563
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)


def ll_to_utm(lon, lat):
    phi, lam = np.radians(lat), np.radians(np.asarray(lon, dtype=np.float64) - 141.0)
    Nn = _A / np.sqrt(1 - _E2 * np.sin(phi) ** 2)
    T = np.tan(phi) ** 2
    C = _EP2 * np.cos(phi) ** 2
    A = np.cos(phi) * lam
    M = _A * ((1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256) * phi - (3 * _E2 / 8 + 3 * _E2 ** 2 / 32 + 45 * _E2 ** 3 / 1024) * np.sin(2 * phi)
              + (15 * _E2 ** 2 / 256 + 45 * _E2 ** 3 / 1024) * np.sin(4 * phi) - (35 * _E2 ** 3 / 3072) * np.sin(6 * phi))
    E = 500000.0 + _K0 * Nn * (A + (1 - T + C) * A ** 3 / 6 + (5 - 18 * T + T * T + 72 * C - 58 * _EP2) * A ** 5 / 120)
    N = _K0 * (M + Nn * np.tan(phi) * (A * A / 2 + (5 - T + 9 * C + 4 * C * C) * A ** 4 / 24
                                       + (61 - 58 * T + T * T + 600 * C - 330 * _EP2) * A ** 6 / 720))
    return E, N


def utm_to_ll(E, N):
    x, y = np.asarray(E, dtype=np.float64) - 500000.0, np.asarray(N, dtype=np.float64)
    mu = y / _K0 / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    p = mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * np.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * np.sin(4 * mu) \
        + (151 * e1 ** 3 / 96) * np.sin(6 * mu)
    C = _EP2 * np.cos(p) ** 2
    T = np.tan(p) ** 2
    Nn = _A / np.sqrt(1 - _E2 * np.sin(p) ** 2)
    R = _A * (1 - _E2) / (1 - _E2 * np.sin(p) ** 2) ** 1.5
    D = x / (Nn * _K0)
    lat = p - (Nn * np.tan(p) / R) * (D ** 2 / 2 - (5 + 3 * T + 10 * C - 4 * C ** 2 - 9 * _EP2) * D ** 4 / 24)
    lon = (D - (1 + 2 * T + C) * D ** 3 / 6) / np.cos(p)
    return np.degrees(lon) + 141.0, np.degrees(lat)


class Projection:
    """world = (E - origin_easting) * content_scale + offset (terrain_metadata.json, schema y92.classic_base_projection.v1)."""

    def __init__(self, meta_path: Path):
        self.path = meta_path
        self.meta = json.loads(meta_path.read_text())
        m = self.meta
        self.cs, self.E0, self.N0 = float(m["content_scale"]), float(m["origin_easting"]), float(m["origin_northing"])
        self.ox, self.oy = float(m["offset_x"]), float(m["offset_y"])

    def world(self, E, N):
        return (np.asarray(E) - self.E0) * self.cs + self.ox, (np.asarray(N) - self.N0) * self.cs + self.oy

    def utm(self, x, y):
        return (np.asarray(x) - self.ox) / self.cs + self.E0, (np.asarray(y) - self.oy) / self.cs + self.N0

    def lonlat_of_world(self, x, y):
        return utm_to_ll(*self.utm(x, y))


# --------------------------------------------------------------------------------------------------------------
# DEM tiles (GSI DEM5A, z15 XYZ, 256 x 256 comma separated metres)
# --------------------------------------------------------------------------------------------------------------
class Dem:
    ZOOM = 15

    def __init__(self, dirs):
        self.tiles = {}
        self.sources = []
        for d in dirs:
            for p in sorted(Path(d).glob("dem5a_15_*_*.txt")):
                _, _, tx, ty = p.stem.split("_")
                arr = np.array([[float(v) if v.strip() not in ("", "e") else np.nan for v in row.split(",")]
                                for row in p.read_text().strip().split("\n")], dtype=np.float32)
                if arr.shape != (256, 256):
                    raise SystemExit(f"{p}: DEM tile is {arr.shape}, expected 256 x 256")
                self.tiles[(int(tx), int(ty))] = arr
                self.sources.append({"file": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        if not self.tiles:
            raise SystemExit("no dem5a_15_X_Y.txt tiles found in --dem directories")

    def sample(self, lon, lat):
        """Bilinear elevation in metres (pixel centres at +0.5); NaN outside the tiles."""
        n = 2 ** self.ZOOM
        tx = (np.asarray(lon) + 180.0) / 360.0 * n
        ty = (1 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2 * n
        fx, fy = tx * 256 - 0.5, ty * 256 - 0.5
        x0, y0 = np.floor(fx).astype(np.int64), np.floor(fy).astype(np.int64)
        ax, ay = fx - x0, fy - y0

        def px(ix, iy):
            out = np.full(ix.shape, np.nan, np.float32)
            tix, tiy = ix // 256, iy // 256
            for (a, b), arr in self.tiles.items():
                m = (tix == a) & (tiy == b)
                if m.any():
                    out[m] = arr[iy[m] - 256 * b, ix[m] - 256 * a]
            return out
        v00, v10, v01, v11 = px(x0, y0), px(x0 + 1, y0), px(x0, y0 + 1), px(x0 + 1, y0 + 1)
        return (v00 * (1 - ax) * (1 - ay) + v10 * ax * (1 - ay) + v01 * (1 - ax) * ay + v11 * ax * ay).astype(np.float64)


# --------------------------------------------------------------------------------------------------------------
# OSM land cover -> raster
# --------------------------------------------------------------------------------------------------------------
WATER_TAGS = lambda t: (t.get("natural") == "water" or t.get("waterway") in ("riverbank", "dock") or t.get("landuse") in ("reservoir", "basin")
                        or t.get("water") in ("river", "lake", "pond", "reservoir", "canal"))
GRASS_LANDUSE = {"grass", "recreation_ground", "meadow", "village_green", "cemetery", "orchard", "farmland", "allotments", "forest"}
GRASS_LEISURE = {"park", "pitch", "golf_course", "playground", "garden", "recreation_ground", "common"}
GRASS_NATURAL = {"wood", "grassland", "scrub", "heath", "wetland"}
ROAD_WIDTH_M = {"primary": 9.0, "secondary": 8.0, "tertiary": 7.0, "unclassified": 5.0, "residential": 5.0, "service": 3.5, "track": 3.5,
                "cycleway": 3.0, "footway": 1.6, "path": 1.4, "pedestrian": 4.0, "steps": 1.6, "living_street": 4.0}
PATH_CLASSES = {"cycleway", "footway", "path", "pedestrian", "steps"}


def assemble_rings(ways):
    """Join way node-id lists end to end into closed rings (multipolygon members come as open segments)."""
    segs = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while segs:
        ring = segs.pop(0)
        grown = True
        while ring[0] != ring[-1] and grown:
            grown = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[-2::-1]
                elif s[-1] == ring[0]:
                    ring = s[:-1] + ring
                elif s[0] == ring[0]:
                    ring = s[:0:-1] + ring
                else:
                    continue
                segs.pop(i)
                grown = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


class Raster:
    """Material raster over a world-unit cell window [u0, u0 + nu) x [v0, v0 + nv); cell (u, v) = world [u, u+1) x [v, v+1)."""

    def __init__(self, u0, v0, nu, nv, fill):
        self.u0, self.v0, self.nu, self.nv = u0, v0, nu, nv
        self.mat = np.full((nv, nu), fill, np.uint8)

    def fill_polygon(self, rings_xy, value, holes_xy=()):
        """Even-odd scanline fill of rings (list of (x, y) world coordinates) at cell centres."""
        mask = np.zeros((self.nv, self.nu), bool)
        for sign, rings in ((True, rings_xy), (False, holes_xy)):
            for ring in rings:
                pts = np.asarray(ring, dtype=np.float64)
                if len(pts) < 3:
                    continue
                m = self._scan(pts)
                if sign:
                    mask |= m
                else:
                    mask &= ~m
        self.mat[mask] = value
        return int(mask.sum())

    def _scan(self, pts):
        out = np.zeros((self.nv, self.nu), bool)
        ymin, ymax = pts[:, 1].min(), pts[:, 1].max()
        r0 = max(0, int(math.floor(ymin - self.v0 - 0.5)))
        r1 = min(self.nv - 1, int(math.ceil(ymax - self.v0 - 0.5)))
        a, b = pts, np.roll(pts, -1, axis=0)
        for r in range(r0, r1 + 1):
            yc = self.v0 + r + 0.5
            m = (a[:, 1] > yc) != (b[:, 1] > yc)
            if not m.any():
                continue
            xs = a[m, 0] + (yc - a[m, 1]) * (b[m, 0] - a[m, 0]) / (b[m, 1] - a[m, 1])
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                c0 = int(math.ceil(xs[k] - self.u0 - 0.5))
                c1 = int(math.floor(xs[k + 1] - self.u0 - 0.5))
                if c1 >= 0 and c0 < self.nu and c1 >= c0:
                    out[r, max(c0, 0):min(c1, self.nu - 1) + 1] = True
        return out

    def stroke(self, pts, half_width, value):
        """Paint cells whose centre lies within half_width (world units) of the polyline."""
        pts = np.asarray(pts, dtype=np.float64)
        for p, q in zip(pts[:-1], pts[1:]):
            lo = np.minimum(p, q) - half_width - 1
            hi = np.maximum(p, q) + half_width + 1
            u_a, u_b = max(0, int(lo[0] - self.u0)), min(self.nu, int(hi[0] - self.u0) + 1)
            v_a, v_b = max(0, int(lo[1] - self.v0)), min(self.nv, int(hi[1] - self.v0) + 1)
            if u_a >= u_b or v_a >= v_b:
                continue
            uu = self.u0 + np.arange(u_a, u_b) + 0.5
            vv = self.v0 + np.arange(v_a, v_b) + 0.5
            gx, gy = np.meshgrid(uu, vv)
            d = q - p
            L2 = float(d @ d)
            t = np.clip(((gx - p[0]) * d[0] + (gy - p[1]) * d[1]) / L2, 0, 1) if L2 > 0 else np.zeros_like(gx)
            dist = np.hypot(gx - (p[0] + t * d[0]), gy - (p[1] + t * d[1]))
            sub = self.mat[v_a:v_b, u_a:u_b]
            sub[dist <= half_width] = value


def paint_osm(raster: Raster, osm_path: Path, proj: Projection, mats: dict):
    """Paint OSM land cover into the raster in a fixed order: grass, water, then roads and paths on top."""
    root = ET.parse(osm_path).getroot()
    nid = {}
    lon, lat, ids = [], [], []
    for n in root.iter("node"):
        ids.append(n.get("id")); lon.append(float(n.get("lon"))); lat.append(float(n.get("lat")))
    E, N = ll_to_utm(np.array(lon), np.array(lat))
    X, Y = proj.world(E, N)
    for i, k in enumerate(ids):
        nid[k] = (float(X[i]), float(Y[i]))
    ways, wtags = {}, {}
    for w in root.iter("way"):
        ways[w.get("id")] = [r.get("ref") for r in w.iter("nd") if r.get("ref") in nid]
        wtags[w.get("id")] = {t.get("k"): t.get("v") for t in w.iter("tag")}
    painted = {"grass_cells": 0, "water_cells": 0, "road_cells_ways": 0, "path_ways": 0, "water_features": []}

    def ring_xy(ring):
        return [nid[k] for k in ring]

    def is_grass(t):
        return (t.get("landuse") in GRASS_LANDUSE or t.get("leisure") in GRASS_LEISURE or t.get("natural") in GRASS_NATURAL)

    for wid, ids_ in ways.items():  # closed grass-like areas
        t = wtags[wid]
        if len(ids_) >= 4 and ids_[0] == ids_[-1] and is_grass(t) and not t.get("building"):
            painted["grass_cells"] += raster.fill_polygon([ring_xy(ids_)], mats["grass"])
    used_in_rel = set()
    for rel in root.iter("relation"):
        t = {x.get("k"): x.get("v") for x in rel.iter("tag")}
        if t.get("type") != "multipolygon" or not (WATER_TAGS(t) or is_grass(t)):
            continue
        outer = [ways[m.get("ref")] for m in rel.iter("member") if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("ref") in ways]
        inner = [ways[m.get("ref")] for m in rel.iter("member") if m.get("type") == "way" and m.get("role") == "inner" and m.get("ref") in ways]
        orings, irings = assemble_rings(outer), assemble_rings(inner)
        used_in_rel.update(m.get("ref") for m in rel.iter("member"))
        water = WATER_TAGS(t)
        n = raster.fill_polygon([ring_xy(r) for r in orings], mats["water"] if water else mats["grass"], [ring_xy(r) for r in irings])
        painted["water_cells" if water else "grass_cells"] += n
        if water:
            painted["water_features"].append({"relation": rel.get("id"), "rings": len(orings), "cells": n, "name": t.get("name")})
    for wid, ids_ in ways.items():  # plain closed water ways (riverbank / natural=water)
        t = wtags[wid]
        if len(ids_) >= 4 and ids_[0] == ids_[-1] and WATER_TAGS(t):
            n = raster.fill_polygon([ring_xy(ids_)], mats["water"])
            painted["water_cells"] += n
            painted["water_features"].append({"way": wid, "cells": n, "name": t.get("name")})
    order = sorted((wid for wid, t in wtags.items() if t.get("highway") in ROAD_WIDTH_M and t.get("area") != "yes" and len(ways[wid]) >= 2),
                   key=lambda w: -ROAD_WIDTH_M[wtags[w]["highway"]])
    for wid in order:
        t = wtags[wid]
        cls = t["highway"]
        raster.stroke(ring_xy(ways[wid]), ROAD_WIDTH_M[cls] * proj.cs / 2.0, mats["path"] if cls in PATH_CLASSES else mats["road"])
        painted["path_ways" if cls in PATH_CLASSES else "road_cells_ways"] += 1
    return painted


# --------------------------------------------------------------------------------------------------------------
# A3D terrain section
# --------------------------------------------------------------------------------------------------------------
def read_terrain(data: bytes):
    if data[:4] != b"AS3D":
        raise SystemExit("not an A3D file")
    hs, n, reserved = struct.unpack_from("<III", data, 4)
    if hs != HDR:
        raise SystemExit(f"unexpected A3D header size {hs}")
    if len(data) < hs + n * PATCH_BYTES:
        raise SystemExit("truncated A3D patch table")
    return n, reserved, np.frombuffer(data, dtype=REC, count=n, offset=hs)


def datum_for(map_path: Path, override):
    if override is not None:
        return float(override), "--datum-m"
    r = map_path.parent / "dem_lowering_receipt.json"
    if r.is_file():
        j = json.loads(r.read_text())
        return float(j["elevation"]["local_bake_datum_m"]), f"{r.name} elevation.local_bake_datum_m"
    raise SystemExit("no dem_lowering_receipt.json beside the map; pass --datum-m")


def smooth_water(h, water_vtx, gj0):
    """Flatten water vertices across the river (per row) to the median of their row band, linear along the flow."""
    out = h.copy()
    rows = np.where(water_vtx.any(axis=1))[0]
    if not len(rows):
        return out
    band = 24
    centres, meds = [], []
    for s in range(0, h.shape[0], band):
        sub = water_vtx[s:s + band]
        if sub.any():
            centres.append(s + (min(band, h.shape[0] - s) - 1) / 2.0)
            meds.append(float(np.median(h[s:s + band][sub])))
    centres, meds = np.array(centres), np.array(meds)
    if len(centres) > 2:  # light smoothing along the flow
        meds = np.convolve(np.pad(meds, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")
    lvl = np.interp(np.arange(h.shape[0]), centres, meds)
    out[water_vtx] = np.broadcast_to(lvl[:, None], h.shape)[water_vtx]
    return out


def blend_to_pinned(delta_at_pinned, pinned, dist_vtx):
    """Spread (existing - DEM) at pinned vertices into free vertices: each free vertex takes the delta of the nearest pinned
    vertex in its row/column in each of four directions, weighted linearly to zero at dist_vtx vertices."""
    H, W = pinned.shape
    acc = np.zeros((H, W)); wsum = np.zeros((H, W))
    for transpose in (False, True):
        P, D = (pinned.T, delta_at_pinned.T) if transpose else (pinned, delta_at_pinned)
        h, w = P.shape
        idx = np.arange(w)[None, :]
        for reverse in (False, True):
            Pp, Dd = (P[:, ::-1], D[:, ::-1]) if reverse else (P, D)
            last = np.where(Pp, idx, -10 ** 9)
            last = np.maximum.accumulate(last, axis=1)
            dist = idx - last
            ok = (last >= 0) & (dist <= dist_vtx)
            val = np.take_along_axis(Dd, np.clip(last, 0, w - 1), axis=1)
            wt = np.where(ok, 1.0 - dist / float(dist_vtx + 1), 0.0)
            if reverse:
                wt, val = wt[:, ::-1], val[:, ::-1]
            if transpose:
                wt, val = wt.T, val.T
            acc += wt * val; wsum += wt
    return np.where(wsum > 0, acc / np.maximum(wsum, 1.0), 0.0) * np.minimum(wsum, 1.0)


def patch_window(proj, boxes_en):
    """boxes_en: list of (E0, N0, E1, N1). -> list of (px0, py0, px1, py1) patch-index boxes (inclusive)."""
    out = []
    for E0, N0, E1, N1 in boxes_en:
        xs, ys = proj.world(np.array([E0, E1]), np.array([N0, N1]))
        xs, ys = np.round(xs, 4), np.round(ys, 4)  # a box given in world units survives the E/N round trip
        out.append((int(math.floor(min(xs) / PATCH_UNITS)), int(math.floor(min(ys) / PATCH_UNITS)),
                    int(math.ceil(max(xs) / PATCH_UNITS)) - 1, int(math.ceil(max(ys) / PATCH_UNITS)) - 1))
    if max(abs(v) for b in out for v in b) > MAX_COORD:
        raise SystemExit("bbox beyond the loader's patch coordinate range")
    return out


def build_patches(data, proj, boxes_en, dem, osm, mats, datum, blend_m, floodplain_below_m, water_below_m=None,
                  land_floor_raw=None, replace_below_x=None):
    n, reserved, ex = read_terrain(data)
    if replace_below_x is not None:  # regenerate an earlier extension: patches west of this x are treated as absent
        ex = ex[ex["x"] >= replace_below_x]
    existing = {(int(x), int(y)) for x, y in zip(ex["x"], ex["y"])}
    boxes = patch_window(proj, boxes_en)
    px0, py0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    px1, py1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    npx, npy = px1 - px0 + 1, py1 - py0 + 1
    wanted = np.zeros((npy, npx), bool)  # union of the boxes inside the bounding window
    for a, b, c, d in boxes:
        wanted[b - py0:d - py0 + 1, a - px0:c - px0 + 1] = True
    # vertex grid of the window, one extra row/column so shared edges are included: global vertex gi = 4 px + hx
    gi0, gj0 = HEIGHT_CELLS * px0, HEIGHT_CELLS * py0
    gw, gh = HEIGHT_CELLS * npx + 1, HEIGHT_CELLS * npy + 1
    gx = 2.0 * (gi0 + np.arange(gw))
    gy = 2.0 * (gj0 + np.arange(gh))
    GX, GY = np.meshgrid(gx, gy)
    lon, lat = proj.lonlat_of_world(GX, GY)
    elev = dem.sample(lon, lat)
    if np.isnan(elev).any():
        miss = int(np.isnan(elev).sum())
        raise SystemExit(f"{miss} of {elev.size} vertices lie outside the DEM tiles; supply more --dem tiles or shrink --bbox")
    per_m = proj.cs * 16.0
    raw = 128.0 + (elev - datum) * per_m
    # visual raster over the window: world cell (u, v), u = 8 px + vx
    u0, v0 = VISUAL_CELLS * px0, VISUAL_CELLS * py0
    ras = Raster(u0, v0, VISUAL_CELLS * npx, VISUAL_CELLS * npy, mats["land"])
    painted = paint_osm(ras, osm, proj, mats) if osm else {}
    if floodplain_below_m is not None or water_below_m is not None:
        cu = (u0 + np.arange(ras.nu) + 0.5)
        cv = (v0 + np.arange(ras.nv) + 0.5)
        CU, CV = np.meshgrid(cu, cv)
        lo, la = proj.lonlat_of_world(CU, CV)
        ce = dem.sample(lo, la)
        del CU, CV, lo, la
        # DEM5A reports the river surface as a flat ~0.2 m; the OSM river relation in the extract is an open ring,
        # so the water mask is the union of the OSM polygons and "DEM below water_below_m" (roads/bridges stay roads)
        if water_below_m is not None:
            sel = (ce < water_below_m) & (ras.mat != mats["road"]) & (ras.mat != mats["path"])
            ras.mat[sel] = mats["water"]
            painted["dem_water_cells"] = int(sel.sum())
        # floodplain: land lower than the given elevation (river bed between the levees) is grass
        if floodplain_below_m is not None:
            sel = (ras.mat == mats["land"]) & (ce < floodplain_below_m)
            ras.mat[sel] = mats["grass"]
            painted["floodplain_cells"] = int(sel.sum())
        del ce
    # water vertices: all four neighbouring cells are water
    wcell = ras.mat == mats["water"]
    # vertex (i, j) at world (2 gi, 2 gj) touches cells (2i-1..2i, 2j-1..2j) of the window
    w4 = np.ones((gh, gw), bool)
    for dv in (-1, 0):
        for du in (-1, 0):
            sub = np.zeros((gh, gw), bool)
            ii = 2 * np.arange(gw) + du
            jj = 2 * np.arange(gh) + dv
            okI = (ii >= 0) & (ii < ras.nu); okJ = (jj >= 0) & (jj < ras.nv)
            sub[np.ix_(okJ, okI)] = wcell[np.ix_(jj[okJ], ii[okI])]
            w4 &= sub
    wv = w4
    h = smooth_water(raw, wv, gj0)
    # pin to existing heights where the new window shares vertices with existing patches
    pinned = np.zeros((gh, gw), bool)
    pin_val = np.zeros((gh, gw))
    near = ex[(ex["x"] >= px0 - 1) & (ex["x"] <= px1 + 1) & (ex["y"] >= py0 - 1) & (ex["y"] <= py1 + 1)]
    for hy in range(5):
        for hx in range(5):
            i = HEIGHT_CELLS * near["x"].astype(np.int64) + hx - gi0
            j = HEIGHT_CELLS * near["y"].astype(np.int64) + hy - gj0
            ok = (i >= 0) & (i < gw) & (j >= 0) & (j < gh)
            pinned[j[ok], i[ok]] = True
            pin_val[j[ok], i[ok]] = near["height"][ok, hy, hx]
    delta = np.where(pinned, pin_val - h, 0.0)
    blend_vtx = max(1, int(round(blend_m * proj.cs / 2.0)))
    h = h + blend_to_pinned(delta, pinned, blend_vtx)
    if land_floor_raw is not None:
        # The engine draws terrain below the session water level (default 55 height units) as water. Land must stay above it:
        # floor every non-water vertex (the map's own lowest land is the bake baseline 128), water vertices stay as flattened.
        h = np.where(wv, h, np.maximum(h, float(land_floor_raw)))
    h = np.where(pinned, pin_val, h)
    clamped = int(((h < 0) | (h > 65535)).sum())
    hq = np.clip(np.rint(h), 0, 65535).astype(np.uint16)
    new = []
    for py in range(py0, py1 + 1):
        for px in range(px0, px1 + 1):
            if (px, py) in existing or not wanted[py - py0, px - px0]:
                continue
            rec = np.zeros((), REC)
            rec["x"], rec["y"] = px, py
            vx0, vy0 = VISUAL_CELLS * (px - px0), VISUAL_CELLS * (py - py0)
            rec["visual"] = ras.mat[vy0:vy0 + 8, vx0:vx0 + 8].astype(np.uint16)
            hx0, hy0 = HEIGHT_CELLS * (px - px0), HEIGHT_CELLS * (py - py0)
            rec["height"] = hq[hy0:hy0 + 5, hx0:hx0 + 5]
            rec["diag"] = 0
            new.append(rec)
    stats = {"patch_window": [px0, py0, px1, py1], "new_patches": len(new), "already_present": int(wanted.sum()) - len(new),
             "elevation_m": [float(np.nanmin(elev)), float(np.nanmax(elev))], "raw_min_max": [int(hq.min()), int(hq.max())],
             "clamped_vertices": clamped, "pinned_vertices": int(pinned.sum()), "datum_m": datum, "water_vertices": int(wv.sum()),
             "painted": painted, "cells_by_material": {int(k): int(v) for k, v in zip(*np.unique(ras.mat, return_counts=True))}}
    return new, stats


def merge_patches(data: bytes, new, replace_below_x=None):
    """New patch table = existing + new records in the engine's canonical (y, x) order; the rest of the file is untouched.
    replace_below_x: existing records with x below it are dropped first (regenerating an earlier extension)."""
    n, reserved, ex = read_terrain(data)
    if replace_below_x is not None:
        ex = ex[ex["x"] >= replace_below_x]
    allp = np.concatenate([np.array(ex), np.array(new, dtype=REC)]) if new else np.array(ex)
    order = np.lexsort((allp["x"], allp["y"]))
    # existing records keep their bytes exactly: only records, not values, are reordered
    body = allp[order].tobytes()
    tail = data[HDR + n * PATCH_BYTES:]
    head = data[:4] + struct.pack("<III", HDR, len(allp), reserved)
    return head + body + tail


def save_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".saving")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def parse_materials(s):
    mats = {"water": 34, "grass": 14, "land": 16, "road": 28, "path": 12}
    if s:
        for kv in s.split(","):
            k, v = kv.split("=")
            if k not in mats:
                raise SystemExit(f"unknown material role {k}")
            mats[k] = int(v)
    return mats


def extend_map(map_path: Path, bbox_en, dem_dirs, osm_path, by, basis, mats, blend_m=80.0, datum_m=None, floodplain_below_m=None,
               dry_run=False, journal=JOURNAL, water_below_m=None, land_floor_raw=None, replace_below_x=None):
    import fcntl
    map_path = map_path.resolve()
    proj = Projection(map_path.parent / "terrain_metadata.json")
    dem = Dem(dem_dirs)
    lock = open(str(map_path) + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # read-modify-write under the lock shared with a3d_edit.py
    data = map_path.read_bytes()
    datum, datum_src = datum_for(map_path, datum_m)
    new, stats = build_patches(data, proj, bbox_en, dem, osm_path, mats, datum, blend_m, floodplain_below_m, water_below_m,
                               land_floor_raw, replace_below_x)
    if not new:
        print("nothing to add: every patch of the bbox already exists")
        return stats
    out = merge_patches(data, new, replace_below_x)
    # guards: round trip, existing records byte identical, instance table untouched
    n0, _, ex0 = read_terrain(data)
    n1, _, ex1 = read_terrain(out)
    if replace_below_x is not None:
        ex0 = ex0[ex0["x"] >= replace_below_x]  # patches west of replace_below_x were regenerated on purpose
    n0_kept = len(ex0)
    if n1 != n0_kept + len(new):
        raise SystemExit("patch count mismatch after merge; map not written")
    old_keys = {(int(r["x"]), int(r["y"])): r.tobytes() for r in ex0}
    for r in ex1:
        k = (int(r["x"]), int(r["y"]))
        if k in old_keys and old_keys[k] != r.tobytes():
            raise SystemExit(f"existing patch {k} changed; map not written")
    n_old = struct.unpack_from("<I", data, 8)[0]
    if out[HDR + n1 * PATCH_BYTES:] != data[HDR + n_old * PATCH_BYTES:]:
        raise SystemExit("materials/instances changed; map not written")
    old_sha, new_sha = hashlib.sha256(data).hexdigest(), hashlib.sha256(out).hexdigest()
    px0, py0, px1, py1 = stats["patch_window"]
    allx = np.concatenate([ex0["x"], [int(r["x"]) for r in new]]); ally = np.concatenate([ex0["y"], [int(r["y"]) for r in new]])
    bounds = {"min_x": float(min(0, allx.min()) * PATCH_UNITS), "min_y": float(min(0, ally.min()) * PATCH_UNITS),
              "max_x": float((allx.max() + 1) * PATCH_UNITS), "max_y": float((ally.max() + 1) * PATCH_UNITS)}
    old_bounds = proj.meta.get("terrain_bounds")
    meta_changed = old_bounds != bounds
    print(f"{len(new)} patches added (window {stats['patch_window']}); sha {old_sha[:12]} -> {new_sha[:12]}"
          f"{' (dry run, not written)' if dry_run else ''}; terrain_bounds {old_bounds} -> {bounds}")
    print("stats", json.dumps(stats, ensure_ascii=False))
    if dry_run:
        return {**stats, "sha256_before": old_sha, "sha256_after": new_sha}
    keep = keep_pre_edit(map_path, data, old_sha)
    save_atomic(map_path, out)
    if meta_changed:
        m = dict(proj.meta); m["terrain_bounds"] = bounds
        save_atomic(proj.path, (json.dumps(m, indent=2, sort_keys=True) + "\n").encode())
    rec = {"at": datetime.now(timezone.utc).isoformat(), "map": str(map_path), "op": "terrain_extend",
           "sha256_before": old_sha, "sha256_after": new_sha, "kept": str(keep), "by": by, "basis": basis,
           "bbox_utm_EN": [list(b) for b in bbox_en], "patch_window": stats["patch_window"], "new_patches": len(new),
           "sources": {"dem": dem.sources, "osm": ({"file": str(osm_path), "sha256": hashlib.sha256(Path(osm_path).read_bytes()).hexdigest()} if osm_path else None),
                       "datum": {"m": datum, "from": datum_src}},
           "terrain_bounds_before": old_bounds, "terrain_bounds_after": bounds if meta_changed else old_bounds,
           "materials": mats, "blend_m": blend_m, "floodplain_below_m": floodplain_below_m, "water_dem_below_m": water_below_m, "land_floor_raw": land_floor_raw,
           "replaced_patches_west_of_x": replace_below_x, "stats": stats,
           "edits": []}  # 'edits' kept empty so a3d_edit.py replay (which reads line["edits"]) skips this record
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("saved", map_path, "; journal", journal)
    return {**stats, "sha256_before": old_sha, "sha256_after": new_sha}


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("info"); pi.add_argument("map")
    pe = sub.add_parser("extend")
    pe.add_argument("map")
    pe.add_argument("--bbox", nargs=4, type=float, action="append", default=[], metavar=("E0", "N0", "E1", "N1"),
                    help="UTM 54N metres; repeat for a union of boxes")
    pe.add_argument("--bbox-world", nargs=4, type=float, action="append", default=[], metavar=("X0", "Y0", "X1", "Y1"),
                    help="map world units (terrain_metadata projection); repeat for a union of boxes")
    pe.add_argument("--dem", action="append", required=True)
    pe.add_argument("--osm")
    pe.add_argument("--by", required=True)
    pe.add_argument("--basis", required=True)
    pe.add_argument("--blend-m", type=float, default=80.0)
    pe.add_argument("--datum-m", type=float)
    pe.add_argument("--floodplain-below-m", type=float)
    pe.add_argument("--land-floor-raw", type=float,
                    help="floor for non-water vertices (128 = the map's bake baseline); the engine draws terrain under its water level (55) as water")
    pe.add_argument("--replace-west-of-patch-x", type=int,
                    help="regenerate earlier extension patches with x below this (0 = every patch at x < 0); patches at x >= it are never touched")
    pe.add_argument("--water-dem-below-m", type=float,
                    help="also paint cells whose DEM elevation is below this as water (GSI DEM5A shows the river surface flat at ~0.2 m)")
    pe.add_argument("--materials")
    pe.add_argument("--dry-run", action="store_true")
    pe.add_argument("--journal", help="journal file (default: map_edits/journal.jsonl beside this tool; tests and scratch copies override it)")
    a = ap.parse_args(argv)
    mp = Path(a.map)
    if a.cmd == "info":
        n, _, ex = read_terrain(mp.read_bytes())
        print("patches", n, "x", int(ex["x"].min()), int(ex["x"].max()), "y", int(ex["y"].min()), int(ex["y"].max()))
        return 0
    proj = Projection(mp.resolve().parent / "terrain_metadata.json")
    boxes = [tuple(b) for b in a.bbox]
    for x0, y0, x1, y1 in a.bbox_world:
        (e0, e1), (n0, n1) = (proj.utm(np.array([x0, x1]), np.array([y0, y1])))
        boxes.append((float(e0), float(n0), float(e1), float(n1)))
    if not boxes:
        raise SystemExit("give at least one --bbox or --bbox-world")
    extend_map(mp, boxes, a.dem, Path(a.osm) if a.osm else None, a.by, a.basis, parse_materials(a.materials), a.blend_m, a.datum_m,
               a.floodplain_below_m, a.dry_run, Path(a.journal) if a.journal else JOURNAL, water_below_m=a.water_dem_below_m,
               land_floor_raw=a.land_floor_raw, replace_below_x=a.replace_west_of_patch_x)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
