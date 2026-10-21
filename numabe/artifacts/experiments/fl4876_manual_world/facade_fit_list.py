#!/usr/bin/env python3
"""facade_fit_list.py BUILD_DIR [OUT_JSON] -- FL-4876 listing aid (measures, decides nothing).

For every object a build mounts on a building wall, measure how it sits against the nearest wall face of its host
footprint (canonical.world_ir.json, EPSG:32654 metres):
  angle_deg     angle between the panel's plane and the wall line (0 = flat on the wall, 90 = sticking straight out)
  offset_m      signed distance of the panel origin from the wall line (+ = outside the building, - = inside)
  overhang_m    how far the panel's half-width runs past the ends of that wall face (0 = within the face)
Sources:
  lower_fixtures_instances.json   facade_attached rows (building_face_panel); transform is column-major 4x4, column 0 =
                                  local X (along the panel, scaled by width), column 1 = local Y (panel normal axis)
  lower_custom_instances.json     facade_attached custom storefront signs (same transform convention)
  place_front_instances.json      front_outward_normal_xy + front_anchor_world_xy (A3D units) + fitted_width_m
  sign_sprites_receipt.json       XP sign sprites: normal + pos (A3D units) + slot_m width
A3D units -> metres via terrain_metadata.json (E = origin_E + (x - offset_x) / content_scale).
Blade signs (projecting by design) also read ~90 deg; the module id is printed so a person can tell them apart.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

B = Path(sys.argv[1])
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else None
tm = json.loads((B / "terrain_metadata.json").read_text())
CS, OX, OY, E0, N0 = tm["content_scale"], tm["offset_x"], tm["offset_y"], tm["origin_easting"], tm["origin_northing"]


def to_en(x, y):
    """A3D world units -> UTM metres (inverse of the classic_base affine)."""
    return E0 + (x - OX) / CS, N0 + (y - OY) / CS


ir = json.loads((B / "canonical.world_ir.json").read_text())
FOOT = {}
for f in ir["canonical_features"]:
    fid = f.get("feature_id", "")
    if "building_footprint" in fid:
        pts = f["geometry"]["coordinates"]
        while isinstance(pts[0][0], list):
            pts = pts[0]
        FOOT[fid] = [tuple(p[:2]) for p in pts]


def nearest_face(fid, e, n):
    """(face index, signed outward offset m, along position m, face length m, unit tangent, unit outward normal)."""
    ring = FOOT.get(fid)
    if not ring:
        return None
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
    best = None
    for i in range(len(ring)):
        p, q = ring[i], ring[(i + 1) % len(ring)]
        dx, dy = q[0] - p[0], q[1] - p[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        tx, ty = dx / L, dy / L
        nx, ny = (ty, -tx) if area > 0 else (-ty, tx)  # outward normal for CCW (area>0) or CW ring
        t = (e - p[0]) * tx + (n - p[1]) * ty
        off = (e - p[0]) * nx + (n - p[1]) * ny
        tc = min(max(t, 0.0), L)
        d = math.hypot(e - (p[0] + tx * tc), n - (p[1] + ty * tc))
        if best is None or d < best[0]:
            best = (d, i, off, t, L, (tx, ty), (nx, ny))
    return best[1:] + (best[0],)  # ..., clamped distance to the face segment (m)


rows = []


def add(src, iid, module, fid, e, n, along_xy, width_m):
    nf = nearest_face(fid, e, n)
    if nf is None:
        rows.append({"source": src, "id": iid, "module": module, "host": fid, "error": "host footprint not found"})
        return
    i, off, t, L, (tx, ty), _, dseg = nf
    ax, ay = along_xy
    al = math.hypot(ax, ay) or 1.0
    cosang = abs((ax * tx + ay * ty) / al)
    ang = math.degrees(math.acos(min(1.0, cosang)))
    half = width_m / 2.0 * cosang
    over = max(0.0, (t + half) - L, -(t - half))
    rows.append({"source": src, "id": iid, "module": module, "host": fid.split(":", 2)[-1], "face": i,
                 "angle_deg": round(ang, 1), "offset_m": round(off, 2), "overhang_m": round(over, 2),
                 "face_len_m": round(L, 2), "width_m": round(width_m, 2), "dist_to_face_m": round(dseg, 2),
                 "E": round(e, 2), "N": round(n, 2)})


for fname, src in (("lower_fixtures_instances.json", "fixture"), ("lower_custom_instances.json", "custom")):
    for r in json.loads((B / fname).read_text()):
        if not r.get("facade_attached"):
            continue
        fid = r.get("feature_id") or r.get("parent") or ""
        if "building_footprint" not in fid:
            continue
        T = r["transform"]
        ax, ay = T[0], T[1]
        mc = r.get("metric_contract") or {}
        ac = mc.get("asset_contract") or {}
        ext = (mc.get("intended_native_extent_m") or [None])[0]
        # built width = |local X column| x native mesh width; planned_width_m is only the catalog default (8 m for
        # facade modules that the lowering then fits to the face)
        width = (math.hypot(ax, ay) * ext if ext else None) or r.get("plane_width_m") or ac.get("planned_width_m") or 0.0
        module = ac.get("module_id") or r.get("module_id") or r.get("subject") or r.get("mesh_name")
        # measure where the panel is MOUNTED (transform translation; FACE-FIT-01 may slide it along the face),
        # not the observed point r["E"], r["N"]
        me, mn = to_en(T[12], T[13])
        add(src, r.get("placement_id"), module, fid, me, mn, (ax, ay), float(width))

for r in json.loads((B / "place_front_instances.json").read_text()):
    fid = r.get("parent") or r.get("host_building_id") or r.get("building_feature_id") or ""
    if "building_footprint" not in str(fid):
        for k, v in r.items():
            if isinstance(v, str) and "building_footprint" in v:
                fid = v
                break
    nx, ny = r["front_outward_normal_xy"]
    e, n = to_en(*r["front_anchor_world_xy"])
    add("place_front", r.get("feature_id"), r.get("asset_match"), fid, e, n, (ny, -nx), float(r.get("fitted_width_m") or 0))

sp = json.loads((B / "sign_sprites_receipt.json").read_text())
for s in sp["signs"]:
    fid = s.get("host_building") or s.get("building_feature_id") or ""
    if s.get("plane"):  # SIGN-PLANE-01 builds: the logo is a wall mesh at plane.pos with plane.normal
        s = {**s, "pos": s["plane"]["pos"], "normal": s["plane"]["normal"], "slot_m": [s["plane"]["width_m"]]}
    e, n = to_en(s["pos"][0], s["pos"][1])
    if "building_footprint" not in str(fid):
        best = None
        for k, ring in FOOT.items():
            cx = sum(p[0] for p in ring) / len(ring)
            cy = sum(p[1] for p in ring) / len(ring)
            dd = (cx - e) ** 2 + (cy - n) ** 2
            if dd < 60 ** 2:
                nf = nearest_face(k, e, n)
                dist = nf[-1] if nf else 1e9  # distance to the face segment, not to its infinite line
                if best is None or dist < best[0]:
                    best = (dist, k)
        fid = best[1] if best else ""
    nx, ny = s["normal"]
    width = (s.get("slot_m") or [None])[0] or (s.get("cells") or [0])[0] * 0.25  # no slot: ~0.25 m per sign cell
    add("sign_sprite", s["label"], s.get("kind"), fid, e, n, (ny, -nx), float(width))

rows.sort(key=lambda r: (-(r.get("angle_deg") or 0), -abs(r.get("offset_m") or 0)))
perp = [r for r in rows if r.get("angle_deg", 0) >= 45]
off = [r for r in rows if abs(r.get("offset_m", 0)) > 0.6]
over = [r for r in rows if r.get("overhang_m", 0) > 0.3]
print(f"{len(rows)} wall-mounted objects; angle>=45 deg: {len(perp)}; |offset|>0.6 m: {len(off)}; overhang>0.3 m: {len(over)}")
by = {}
for r in rows:
    by.setdefault(r["source"], [0, 0, 0, 0])
    b = by[r["source"]]
    b[0] += 1
    b[1] += r.get("angle_deg", 0) >= 45
    b[2] += abs(r.get("offset_m", 0)) > 0.6
    b[3] += r.get("overhang_m", 0) > 0.3
for k, v in by.items():
    print(f"  {k}: n={v[0]} perpendicular={v[1]} off-wall={v[2]} past-face-end={v[3]}")
if OUT:
    OUT.write_text(json.dumps({"schema": "y92.fl4876.facade_fit_list.v1", "build": str(B), "rows": rows}, ensure_ascii=False, indent=1) + "\n")
    print("wrote", OUT)
