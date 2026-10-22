"""Shop logos as flat wall meshes instead of billboard sprites (FL-4876 SIGN-PLANE-01).

WHY. compile_sign_sprites places every logo as a Classic map sprite. Engine sprites are camera-facing billboards
(engine/world.h), so a logo turns with the camera and, seen along the street, reads as a card sticking straight out
of the wall; SIGN-CLEAR-01 also had to stand each sprite 1 m off the wall because sprite depth is height-only
(operator 2026-10-16: "signs being perpendicular to the face of building", "card like things"). A mesh has none of
these problems: it keeps the wall's orientation and is depth-tested per pixel, so it can lie flush on the wall.

WHAT. The same half-block cell grid the sprite would show (cells_for / an operator capture band: rows top first,
(glyph, fg upper, bg lower)) becomes one non-colliding AKM per sign: every cell is two coloured quads (upper half fg,
lower half bg); runs of equal colour along a row merge into one quad; the transparency key is left out. The plane
keeps the sprite's metric size (cols / COLS_PER_WORLD_UNIT world units wide, rows * Z_PER_ROW world z tall, i.e. the
sign's metres) and stands PLANE_OUT_M in front of the wall the player sees:
  - a building drawn by a mass mesh (meshes/mass_<id>.akm) shows its wall on the canonical footprint face;
  - a terrain-box building shows the terrain wall, found by extruded_wall.wall_foot along the outward normal.
Local frame (same convention as the custom sign planes): x along the wall to the viewer's right, y = outward normal,
z up in metres * content_scale; the instance transform scales z by HEIGHT_SCALE (16).
"""
from __future__ import annotations

import math
from pathlib import Path

PLANE_OUT_M = 0.06        # in front of the visible wall: clears z-fighting, reads as painted on / fixed to it
KEY = (255, 255, 85)      # XP transparency key (compile_sign_sprites.KEY)
ALPHA_NO_COLLISION = 255  # AKM alpha 255 => presentation only, no collision


def plane_akm(cells, width_w: float, height_mz: float, comment: str) -> str:
    """ASCII-PLY AKM text for a logo plane.

    cells: rows top first of (glyph, fg_rgb, bg_rgb) half-block cells; width_w: plane width in world units;
    height_mz: plane height in mesh z units (metres * content_scale). Quads face +y (outward) and -y.
    """
    rows, cols = len(cells), len(cells[0])
    cw, hh = width_w / cols, height_mz / (2 * rows)
    verts, faces = [], []

    def quad(x0, x1, z0, z1, rgb):
        for ny, order in ((1.0, (0, 1, 2, 3)), (-1.0, (3, 2, 1, 0))):
            base = len(verts)
            y = 0.0
            for x, z in ((x0, z0), (x0, z1), (x1, z1), (x1, z0)):
                verts.append((x, y, z, 0.0, ny, 0.0, *rgb, ALPHA_NO_COLLISION))
            o = [base + k for k in order]
            faces.append((o[0], o[1], o[2]))
            faces.append((o[0], o[2], o[3]))

    for r, row in enumerate(cells):
        for half in (0, 1):  # 0 = upper half (fg), 1 = lower half (bg)
            z1 = height_mz - (2 * r + half) * hh
            z0 = z1 - hh
            run_rgb, run_x0 = None, 0
            for c in range(cols + 1):
                rgb = tuple(cells[r][c][1 + half]) if c < cols else None
                if rgb == KEY:
                    rgb = None
                if rgb != run_rgb:
                    if run_rgb is not None:
                        quad(-width_w / 2 + run_x0 * cw, -width_w / 2 + c * cw, z0, z1, run_rgb)
                    run_rgb, run_x0 = rgb, c
    lines = ["ply", "format ascii 1.0", "comment " + comment,
             "element vertex %d" % len(verts),
             "property float x", "property float y", "property float z",
             "property float nx", "property float ny", "property float nz",
             "property uchar red", "property uchar green", "property uchar blue", "property uchar alpha",
             "element face %d" % len(faces), "property list uchar uint vertex_indices", "end_header"]
    lines += ["%.4f %.4f %.4f %.1f %.1f %.1f %d %d %d %d" % v for v in verts]
    lines += ["3 %d %d %d" % f for f in faces]
    return "\n".join(lines) + "\n"


def wall_distance_m(terrain, rings_world: dict, mass_ids: set, x: float, y: float, nx: float, ny: float,
                    sc: float):
    """Signed distance (m, along +normal from (x, y)) of the wall the player sees behind a sign, and its basis.

    rings_world: building id -> footprint ring in world units; mass_ids: building ids whose visible front is the
    footprint face (drawn by a mass mesh, or carrying a facade panel / place front mounted on the face).
    The nearest footprint face within 2 m whose outward normal agrees with (nx, ny) decides the host; such a host's
    wall is that face, any other host's wall is the terrain wall_foot along the normal.
    """
    best = None
    for bid, ring in rings_world.items():
        n = len(ring)
        area = sum(ring[k][0] * ring[(k + 1) % n][1] - ring[(k + 1) % n][0] * ring[k][1] for k in range(n))
        for k in range(n):
            p, q = ring[k], ring[(k + 1) % n]
            dx, dy = q[0] - p[0], q[1] - p[1]
            L = math.hypot(dx, dy)
            if L < 1e-6:
                continue
            fx, fy = (dy / L, -dx / L) if area > 0 else (-dy / L, dx / L)
            if fx * nx + fy * ny < 0.7:
                continue
            t = max(0.0, min(L, (x - p[0]) * dx / L + (y - p[1]) * dy / L))
            d = math.hypot(x - p[0] - dx / L * t, y - p[1] - dy / L * t) / sc
            if d <= 2.0 and (best is None or d < best[0]):
                off = ((p[0] - x) * fx + (p[1] - y) * fy) / sc  # face line along +normal from the point
                best = (d, bid, off)
    host = best[1] if best is not None else None
    if best is not None and best[1] in mass_ids:
        return best[2], MASS_WALL_BASIS + " / facade panel on footprint face of %s" % best[1], host
    from scripts.pipeline.extruded_wall import wall_foot
    hit = wall_foot(terrain, x, y, (nx, ny), sc)
    if hit is None:
        if best is not None:
            return best[2], "footprint face of %s (no terrain wall found)" % best[1], host
        return None, "no wall behind the sign", host
    return float(hit["foot_m"]), "terrain wall foot" + (" of %s" % best[1] if best else ""), host


ANY_HOST = "*"  # facade_meshes key for meshes with no recorded host (building attachments): found by geometry


def load_facade_meshes(run_root: Path, mesh_dir: Path, attachments: list = ()) -> dict:
    """host building id -> [(inst_name, world triangles [((x, y, z) * 3)])] for every mesh mounted on a building face.

    FL-4876 CARD-FIX-01: ANY module counts, not only the sign-surface ones (street_fixture signboards, poster
    carriers, vending machines, awnings sat 0.5-0.94 m in front of 11 of 15 logo planes in build_reg_87).
    Sources: place fronts, facade_attached rows of lower_fixtures / lower_custom, and `attachments`
    ((inst_name, mesh_name, transform) of the bake's attach_*.akm building attachments, which carry no host in the
    map: they are filed under ANY_HOST and found by overlap alone).
    """
    import json
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from decimate_akm import parse_akm
    rows = []
    for fname, host_key in (("place_front_instances.json", "parent_building_feature_id"),
                            ("lower_fixtures_instances.json", "parent"), ("lower_custom_instances.json", "parent")):
        p = run_root / fname
        for r in (json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []):
            host = r.get(host_key)
            if fname != "place_front_instances.json" and not r.get("facade_attached"):
                continue
            if host and r.get("mesh_name") and r.get("transform"):
                rows.append((str(host), r.get("inst_name"), r["mesh_name"], r["transform"]))
    rows += [(ANY_HOST, inst, mesh, T) for inst, mesh, T in attachments]
    out, cache = {}, {}
    for host, inst, mesh, T in rows:
        if mesh not in cache:
            f = mesh_dir / mesh
            cache[mesh] = parse_akm(str(f)) if f.is_file() else ([], [])
        mv, mf = cache[mesh]
        verts = [(T[0] * v[0] + T[4] * v[1] + T[8] * v[2] + T[12], T[1] * v[0] + T[5] * v[1] + T[9] * v[2] + T[13],
                  T[2] * v[0] + T[6] * v[1] + T[10] * v[2] + T[14]) for v in mv]
        out.setdefault(host, []).append((inst, [(verts[i], verts[j], verts[k]) for i, j, k in mf]))
    return out


def _clip_rect(poly: list, u0: float, u1: float, w0: float, w1: float) -> list:
    """Sutherland-Hodgman clip of a polygon of (u, w, d) points to the rectangle u0..u1 x w0..w1 (d interpolated)."""
    for axis, bound, keep_ge in ((0, u0, True), (0, u1, False), (1, w0, True), (1, w1, False)):
        if not poly:
            break
        out = []
        for k, cur in enumerate(poly):
            prev = poly[k - 1]
            cin = (cur[axis] >= bound) if keep_ge else (cur[axis] <= bound)
            pin = (prev[axis] >= bound) if keep_ge else (prev[axis] <= bound)
            if cin != pin:
                t = (bound - prev[axis]) / (cur[axis] - prev[axis])
                out.append(tuple(prev[i] + t * (cur[i] - prev[i]) for i in range(3)))
            if cin:
                out.append(cur)
        poly = out
    return poly


def _area(poly: list) -> float:
    n = len(poly)
    return abs(sum(poly[k][0] * poly[(k + 1) % n][1] - poly[(k + 1) % n][0] * poly[k][1] for k in range(n))) / 2.0


def front_depth_m(facade_meshes: dict, host, x, y, nx, ny, half_w_w, z0, z1, sc):
    """Furthest outward reach (m along +normal from (x, y)) of any facade mesh SURFACE that overlaps the logo
    rectangle (|along| <= half width, world z in [z0, z1]) seen along the wall normal, and that mesh's name;
    (None, None) if none. Triangles are clipped to the rectangle, so a board whose vertices all lie outside the
    rectangle but whose face covers it still counts (FL-4876 CARD-FIX-01; the vertex test missed those)."""
    rx, ry = -ny, nx
    best, who = None, None
    for key in (host, ANY_HOST):
        if key is None:
            continue
        for inst, tris in facade_meshes.get(key, []):
            for tri in tris:
                pts = [((vx - x) * rx + (vy - y) * ry, vz, ((vx - x) * nx + (vy - y) * ny) / sc)
                       for vx, vy, vz in tri]
                if (max(p[0] for p in pts) < -half_w_w or min(p[0] for p in pts) > half_w_w
                        or max(p[1] for p in pts) < z0 or min(p[1] for p in pts) > z1):
                    continue
                poly = _clip_rect(pts, -half_w_w, half_w_w, z0, z1)
                if len(poly) < 3 or _area(poly) < 1e-6:
                    continue  # edge-on to the wall: no surface faces the viewer here
                for _, _, d in poly:
                    # 3 m: ignore deep canopies that only clip the span edge
                    if d < 3.0 and (best is None or d > best):
                        best, who = d, inst
    return best, who


def xp_visual(path: Path) -> list:
    """Image layer (layer 2) of a gzip .xp sheet as rows top first of (glyph, fg, bg) -- inverse of xp_bytes."""
    import gzip
    import struct
    data = gzip.decompress(Path(path).read_bytes())
    o = 4
    (layers,) = struct.unpack_from("<I", data, o)
    o += 4
    grid = None
    for li in range(layers):
        cols, rows = struct.unpack_from("<ii", data, o)
        o += 8
        g = [[None] * cols for _ in range(rows)]
        for x in range(cols):
            for y in range(rows):
                (gl,) = struct.unpack_from("<I", data, o)
                fg, bg = tuple(data[o + 4:o + 7]), tuple(data[o + 7:o + 10])
                g[y][x] = (gl, fg, bg)
                o += 10
        if li == 2:
            grid = g
    if grid is None:
        raise ValueError("xp sheet %s has no image layer" % path)
    return grid


MASS_WALL_BASIS = "mass mesh wall"  # wall_distance_m basis prefix: the visible wall is the footprint face


def mass_wall_ids(rings_en: dict, mesh_dir: Path, paneled_ids=frozenset()) -> set:
    """Building ids whose visible wall is the footprint face: a mass mesh (meshes/mass_<id>.akm) draws it, or a
    facade panel / place front stands on it. Every other host shows the terrain wall (wall_distance_m)."""
    out = set(paneled_ids)
    for bid in rings_en:
        src = bid.split(":", 2)[-1]
        if (mesh_dir / ("mass_" + src.replace("/", "_") + ".akm")).is_file():
            out.add(bid)
    return out


def planes_for_signs(signs: list, sprite_dir: Path, mesh_dir: Path, terrain, meta: dict, rings_en: dict,
                     cols_per_world_unit: float, z_per_row: float, height_scale: float,
                     paneled_ids: set = frozenset(), facade_meshes: dict = None):
    """One flush wall mesh per sign record of compile_sign_sprites.build.

    Each sign needs "sprite" (the .xp in sprite_dir), "pos" (sprite anchor: bottom centre, world units) and an
    outward normal ("normal", else the face of "building_id"/"face_index"). Writes mesh_dir/<plane>.akm and returns
    (instances, records): instances as dicts (mesh_name, inst_name, transform) for the caller to add to the map,
    records as {placement_id, plane: {...}} for the receipt, or {placement_id, skipped: reason}.
    """
    sc = float(meta["content_scale"])
    oe, on = float(meta["origin_easting"]), float(meta["origin_northing"])
    ox, oy = float(meta["offset_x"]), float(meta["offset_y"])
    rings_world = {bid: [((e - oe) * sc + ox, (n - on) * sc + oy) for e, n in ring] for bid, ring in rings_en.items()}
    # a facade panel (rule house front, place front, observed shop front) stands on the footprint face, in front
    # of the terrain wall; a logo behind it would be hidden (paneled_ids: building ids carrying one)
    mass_ids = mass_wall_ids(rings_en, mesh_dir, paneled_ids)
    instances, records = [], []
    for s in signs:
        pid = s.get("placement_id")
        normal = s.get("normal")
        if not normal and s.get("building_id") in rings_en:
            ring = rings_en[s["building_id"]]
            i, n = int(s["face_index"]), len(ring)
            area = sum(ring[k][0] * ring[(k + 1) % n][1] - ring[(k + 1) % n][0] * ring[k][1] for k in range(n))
            p, q = ring[i], ring[(i + 1) % n]
            de, dn = q[0] - p[0], q[1] - p[1]
            L = math.hypot(de, dn) or 1e-9
            normal = (dn / L, -de / L) if area > 0 else (-dn / L, de / L)
        if not normal:
            records.append({"placement_id": pid, "skipped": "no outward normal"})
            continue
        nx, ny = float(normal[0]), float(normal[1])
        nn = math.hypot(nx, ny) or 1.0
        nx, ny = nx / nn, ny / nn
        cells = xp_visual(sprite_dir / s["sprite"])
        rows, cols = len(cells), len(cells[0])
        width_w = cols / cols_per_world_unit
        height_mz = rows * z_per_row / height_scale
        if s.get("size_m"):  # an authored card (FL-4876 CARD-FIX-01) states its own size and density
            width_w, height_mz = float(s["size_m"][0]) * sc, float(s["size_m"][1]) * sc
        x, y, z = (float(v) for v in s["pos"])
        face_o = s.get("plane_face_o_m")
        if face_o is None:
            d_m, basis, host = wall_distance_m(terrain, rings_world, mass_ids, x, y, nx, ny, sc)
        if face_o is not None:
            # a place front's flat band face is the emitter's own number (identity_sign.fascia_face_o_m); the block
            # text glyphs stand 0.04 m in front of it and are covered by this plane, so nothing is depth-tested
            d_m, basis, host = float(face_o), "place-front band face (identity_sign.fascia_face_o_m)", None
            facade_meshes_s = None
        else:
            facade_meshes_s = facade_meshes
        if d_m is None:
            records.append({"placement_id": pid, "skipped": basis})
            continue
        # the logo goes on the front surface the player sees: in front of the wall AND of any shop front, house
        # front panel or sign plate on the same face within the logo's span and height (build_reg_82: shop fronts
        # reach 0.58 m out, so logos 0.11-0.31 m out were hidden inside them)
        wall_d, front_who = d_m, "wall"
        if facade_meshes_s is not None:
            fd, who = front_depth_m(facade_meshes_s, host, x, y, nx, ny, width_w / 2.0, z,
                                    z + height_mz * height_scale, sc)
            if fd is not None and fd > d_m:
                d_m, basis, front_who = fd, "front surface of %s on %s" % (who, host), who
        px, py = x + nx * (d_m + PLANE_OUT_M) * sc, y + ny * (d_m + PLANE_OUT_M) * sc
        name = "signplane_" + Path(s["sprite"]).stem[len("sign_"):] + ".akm"
        (mesh_dir / name).write_text(plane_akm(cells, width_w, height_mz,
                                               "FL-4876 SIGN-PLANE-01 logo plane from %s; alpha=255 => no collision"
                                               % s["sprite"]), encoding="utf-8")
        instances.append({"mesh_name": name, "inst_name": name[:-4],
                          "transform": plane_transform(px, py, z, nx, ny, height_scale)})
        records.append({"placement_id": pid, "plane": {
            "mesh": name, "pos": [round(px, 3), round(py, 3), round(z, 3)], "normal": [round(nx, 4), round(ny, 4)],
            "width_m": round(width_w / sc, 3), "height_m": round(height_mz / sc, 3), "cells": [cols, rows],
            "moved_from_sprite_m": round(d_m + PLANE_OUT_M, 3), "wall_basis": basis,
            "wall_m": round(wall_d, 3), "frontmost_m": round(d_m, 3), "frontmost_surface": front_who,
            "clearance_m": round(d_m + PLANE_OUT_M - d_m, 3)}})
    return instances, records


FACE_PANEL_MODULES = ("residential_facade_roofed_entry", "narrow_mixed_use", "roller_shutter_facade")


def paneled_buildings(run_root: Path) -> set:
    """Building ids whose front is a panel on the footprint face: rule/observed facade panels and place fronts."""
    import json
    out = set()
    fx = run_root / "lower_fixtures_instances.json"
    for r in (json.loads(fx.read_text(encoding="utf-8")) if fx.is_file() else []):
        mid = r.get("module_id") or ((r.get("metric_contract") or {}).get("asset_contract") or {}).get("module_id")
        if r.get("facade_attached") and mid in FACE_PANEL_MODULES and "building_footprint" in str(r.get("parent")):
            out.add(r["parent"])
    pf = run_root / "place_front_instances.json"
    for r in (json.loads(pf.read_text(encoding="utf-8")) if pf.is_file() else []):
        for v in r.values():
            if isinstance(v, str) and v.startswith("feature:building_footprint:"):
                out.add(v)
    return out


def plane_transform(x: float, y: float, z: float, nx: float, ny: float, height_scale: float) -> list:
    """Column-major 4x4: local x = viewer's right along the wall, local y = outward normal, z scaled by HEIGHT_SCALE."""
    rx, ry = -ny, nx  # facing the wall (forward = -normal), the viewer's right
    return [rx, ry, 0.0, 0.0, nx, ny, 0.0, 0.0, 0.0, 0.0, height_scale, 0.0, x, y, z, 1.0]
