#!/usr/bin/env python3
"""Numabe stage viewer export (FL-4809 VIEWER-01/02, restored FL-4876 TOOL-ROUTE-01).

Read-only over one build run root. Writes into ``--out`` (default
``<run-root>/viewer``):

  index.html           the page template from this directory, with the static
                       chain summary injected (the source template is never
                       rewritten)
  viewer_data.json     canonical payload, lon/lat, deterministic byte-for-byte
  viewer_data.js       the same payload wrapped as ``window.VIEWER_DATA``
  viewer_lidar.js      decimated TMG LiDAR heights, ``window.VIEWER_LIDAR``
                       (optional; ``--lidar-grid-m 0`` disables it)
  export_receipt.json  input digests plus the emitted counts

History: the original viewer lived under ``.run/numabe_connected/viewer/``
(last present at f97f8dffc3) and was deleted with the runtime-artifact offload
in 8cc5f64382. Its inputs were pinned to ``build_final14``. This copy takes the
run root as an argument, maps the stage files that were renamed since then,
and adds the layers the Classic map work needs before any runtime capture:

  signs        custom-identity planes (lower_custom_instances) with their real
               outward normal, plus the XP sign sprite position and source crop
               (sign_sprites_receipt, SIGN-XP-01)
  trains       compiled Classic train routes (output.trains.json) with the
               stopped set envelope from the train mesh manifest
  rail_edges   TMG LiDAR rail-corridor edges (rail_corridor_edges.v1.json)
  lidar        TMG LiDAR heights above local ground on a decimation grid

Authority limits (FL-4929/FL-4930): OSM/PLATEAU stay the horizontal
footprint authorities; LiDAR supplies measured heights and clearances only.
The LiDAR layer is a diagnostic overlay, never geometry.

Projection. Source geometry is EPSG:32654 metres; lowered instances are Y9
world units. Both reach EPSG:4326 through ``scripts/pipeline/geo_frames``.
World units invert the classic-base affine recorded in terrain_metadata.json:

    E = origin_easting  + (x - offset_x) / content_scale
    N = origin_northing + (y - offset_y) / content_scale

LiDAR sheets are EPSG:6677 and go to EPSG:4326 through pyproj, as the roof
field owner (compile_lidar_roof_field.py) does; the arrays come from that
owner's ``_load_las_arrays``.

usage: export_viewer_data.py --run-root RUN [--out DIR] [--lidar-grid-m 2]
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from typing import Any


def _repo_root() -> str:
    """Locate the repository root from the working directory or this file."""
    marker = os.path.join("scripts", "pipeline", "geo_frames.py")
    seeds = [os.path.abspath(os.curdir)]
    try:
        seeds.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:  # pragma: no cover - interactive use
        pass
    for seed in seeds:
        node = seed
        while True:
            if os.path.exists(os.path.join(node, marker)):
                return node
            parent = os.path.dirname(node)
            if parent == node:
                break
            node = parent
    raise SystemExit("cannot locate the repository root that owns %s" % marker)


REPO = _repo_root()
TEMPLATE_DIR = os.path.join(REPO, "scripts", "pipeline", "numabe_stage_viewer")

sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts", "pipeline"))
from scripts.pipeline.geo_frames import utm_forward, utm_inverse  # noqa: E402
from scripts.pipeline.canonical_attachment import CanonicalIndex, resolve  # noqa: E402

UTM_ZONE = 54
BUNDLE = "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
LIDAR_DIR = BUNDLE + "/tmg_pointcloud/las"
LIDAR_CRS = "EPSG:6677"
TRAIN_MANIFEST = "assets/meshes/tokyu/tokyu_7000_3car.json"

# Each key resolves to the first candidate that exists under the run root.
# The second name is the build_final14 spelling the original viewer read.
RUN_INPUTS = {
    "terrain_metadata": ["terrain_metadata.json"],
    "canonical": ["canonical.world_ir.json"],
    "plan": ["plan/representation_plan.json"],
    "building_instances": ["building_instances.json"],
    "fixture_instances": ["lower_fixtures_instances.json", "fixture_instances.json"],
    "procedural_facade_instances": ["station_facade_instances.json",
                                    "procedural_facade_instances.json"],
    "lower_linear_instances": ["lower_linear_instances.json"],
    "spatial": ["spatial_coherence.json", "spatialfix01.json"],
    "metric": ["object_metric.json"],
    "inventory": ["route_streetscape_inventory.json"],
    "footprints": ["canonical.world_ir.json"],
    "entities": ["canonical_entities.json"],
    "ledger": ["feature_ledger.json"],
    "observation_debt": ["observation_debt.json"],
    "fixture_receipt": ["lower_fixtures_lowering_receipt.json", "fixture_lowering_receipt.json"],
    "front_receipt": ["place_front_receipt.json"],
    "source_palette": ["source_palette.json"],
    "surface_receipt": ["surface_receipt.json"],
    "rules_resolved": ["plan/rules_resolved.json"],
    "custom_instances": ["lower_custom_instances.json"],
    "custom_plan": ["plan/custom_asset_plan.json"],
    "sign_sprites": ["sign_sprites_receipt.json"],
    "trains": ["output.trains.json"],
}
REPO_INPUTS = {
    "mapillary": ".run/numabe_connected/observe03/mapillary_objects.world_ir.json",
    "survey": BUNDLE + "/added_route_imagery_survey.json",
    "route": BUNDLE + "/valhalla_pedestrian_route.json",
    "rail_edges": BUNDLE + "/tmg_pointcloud/rail_corridor_edges.v1.json",
    "train_manifest": TRAIN_MANIFEST,
    "pack_tokyo": "assets/world_templates/tokyo/pack.json",
    "pack_japan_urban": "assets/world_templates/japan_urban/pack.json",
    "pack_common_global": "assets/world_templates/common_global/pack.json",
}
# An absent input is a named state, never a crash (NUMABE-VERIFY-01).
OPTIONAL_DEFAULTS = {
    "lower_linear_instances": [],
    "fixture_instances": [],
    "procedural_facade_instances": [],
    "spatial": {"fail_rows": []},
    "fixture_receipt": {"instances_emitted": 0},
    "front_receipt": {"instances": [], "excluded": [], "counts": {"place_fronts_emitted": 0}},
    "ledger": {"rows": [], "counts": {"rows": 0}},
    "inventory": {"bins": [], "features": []},
    "mapillary": {"source_observations": [], "canonical_features": []},
    "custom_instances": [],
    "custom_plan": {"rows": []},
    "sign_sprites": {"signs": []},
    "trains": {"routes": []},
    "rail_edges": {"ways": {}},
    "train_manifest": {},
}

CAPTURE_DIR_ABS = os.path.join(REPO, BUNDLE, "captures", "mapillary")
SHEET_DIR_ABS = os.path.join(REPO, ".run", "numabe_connected", "frames01", "sheets")

# Mapillary carries no focal for any surveyed picture in this bundle; the
# observation-debt stage declares 60 degrees and the viewer reuses that number.
DEFAULT_HFOV_DEG = 60.0
DEFAULT_CONE_LEN_M = 25.0


# ---------------------------------------------------------------------------
# io helpers
# ---------------------------------------------------------------------------

def load(path: str) -> Any:
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def _display(path: str) -> str:
    try:
        rel = os.path.relpath(path, REPO)
    except ValueError:
        return path
    return path if rel.startswith("..") else rel


def digest(path: str) -> dict:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return {"path": _display(path), "sha256": h.hexdigest(), "bytes": os.path.getsize(path)}


def r6(v: float) -> float:
    return round(float(v), 6)


def r2(v: float | None) -> float | None:
    return None if v is None else round(float(v), 2)


def r3(v: float | None) -> float | None:
    return None if v is None else round(float(v), 3)


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------

class Projection:
    """UTM 54N metres and Y9 world units -> EPSG:4326 lon/lat."""

    def __init__(self, meta: dict) -> None:
        self.scale = float(meta["content_scale"])
        self.off_x = float(meta["offset_x"])
        self.off_y = float(meta["offset_y"])
        self.origin_e = float(meta["origin_easting"])
        self.origin_n = float(meta["origin_northing"])

    def utm(self, easting: float, northing: float) -> list:
        lon, lat = utm_inverse(easting, northing, UTM_ZONE)
        return [r6(lon), r6(lat)]

    def world_utm(self, x: float, y: float) -> tuple[float, float]:
        return (self.origin_e + (x - self.off_x) / self.scale,
                self.origin_n + (y - self.off_y) / self.scale)

    def world(self, x: float, y: float) -> list:
        return self.utm(*self.world_utm(x, y))


def identity_and_walls(run_root: str, proj: Projection, fronts: list) -> tuple[list, list]:
    """FL-4876 VIEWER-02 layers.

    baked_walls   lon/lat of every terrain height vertex on the boundary of the
                  baked building mass (baked map minus unbaked DEM > 1 m): the
                  walls Classic actually renders, which stand inside the
                  canonical footprint the walls layer extrudes.
    conflicts     every observation an identity entity consumed, with its
                  relation (supporting / contesting / debt) and a line to the
                  mapped place it names.
    Each front also gains ``entity`` (semantic owner) and ``wall_gap_m``.
    """
    import pathlib
    root = pathlib.Path(run_root)
    sys.path.insert(0, _repo_root())
    baked_pts: list = []
    mp, dem = root / "output.a3d", root / "dem_terrain.a3d"
    if mp.is_file() and dem.is_file():
        from scripts.pipeline.extruded_wall import Terrain, wall_foot
        bt, gt = Terrain(mp), Terrain(dem)
        thr = proj.scale * 16.0 * 1.0
        step = bt.vc / bt.hc
        raised = {}
        for (px, py), p in bt.patches.items():
            g = gt.patches.get((px, py))
            if g is None:
                continue
            for j in range(bt.hc + 1):
                for i in range(bt.hc + 1):
                    if p.height[j][i] - g.height[j][i] > thr:
                        raised[(px * bt.hc + i, py * bt.hc + j)] = True
        for (ix, iy) in raised:
            if any((ix + dx, iy + dy) not in raised for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                baked_pts.append(proj.world(ix * step, iy * step))
        baked_pts.sort()
        # wall gap per front: along its outward normal, from the anchor to the baked wall foot
        fr_src = root / "place_front_instances.json"
        if fr_src.is_file():
            by_id = {r["feature_id"]: r for r in json.loads(fr_src.read_text(encoding="utf-8"))}
            for f in fronts:
                r = by_id.get(f["id"])
                if r:
                    hit = wall_foot(bt, *r["front_anchor_world_xy"], r["front_outward_normal_xy"], proj.scale)
                    f["wall_gap_m"] = None if hit is None else r3(-hit["foot_m"])
    conflicts: list = []
    ent_path = root / "observation_registration" / "identity_entities.json"
    reg_path = root / "observation_registration" / "registered_instances.json"
    canon_path = root / "canonical.world_ir.json"
    if ent_path.is_file() and reg_path.is_file():
        ents = json.loads(ent_path.read_text(encoding="utf-8"))["entities"]
        reg = {r["instance_id"]: r for r in json.loads(reg_path.read_text(encoding="utf-8"))}
        places = {}
        if canon_path.is_file():
            for feat in json.loads(canon_path.read_text(encoding="utf-8"))["canonical_features"]:
                if feat.get("semantic_class") == "place" and feat["geometry"]["kind"] == "point":
                    places[feat["feature_id"]] = proj.utm(*feat["geometry"]["coordinates"][:2])
        by_place = {e.get("place_id"): e for e in ents if e.get("kind") == "mapped_place"}
        for f in fronts:
            e = by_place.get(f["id"])
            if e:
                f["entity"] = {"entity_id": e["entity_id"], "state": e["state"],
                               "host": e["host"]["building_id"], "host_basis": e["host"]["basis"],
                               "supporting": e["observations"]["supporting"],
                               "contesting": e["observations"]["contesting"],
                               "crop_eligible": e.get("crop_eligible_observations") or []}
        for e in ents:
            rel = ([("supporting", i) for i in e["observations"]["supporting"]] +
                   [("contesting", i) for i in e["observations"]["contesting"]])
            if e["kind"] == "observed_place" and e["state"] != "entity":
                rel = [("debt", i) for i in e["observations"]["supporting"]]
            for kind, iid in rel:
                o = reg.get(iid)
                if not o:
                    continue
                conflicts.append({
                    "instance_id": iid, "subject": o.get("custom_identity_subject"), "kind": kind,
                    "entity_id": e["entity_id"], "entity_state": e["state"],
                    "position_uncertainty_m": o.get("position_uncertainty_m"),
                    "host_distance_m": None,
                    "observation_lonlat": proj.utm(o["E"], o["N"]),
                    "mapped_lonlat": places.get(e.get("place_id")),
                })
    return baked_pts, conflicts


def decode_valhalla(shape: str, precision: int = 6) -> list:
    """Decode an encoded Valhalla/Google polyline into [[lon, lat], ...]."""
    factor = float(10 ** precision)
    out, index, lat, lon = [], 0, 0, 0
    while index < len(shape):
        for target in ("lat", "lon"):
            shift, result = 0, 0
            while True:
                byte = ord(shape[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if target == "lat":
                lat += delta
            else:
                lon += delta
        out.append([r6(lon / factor), r6(lat / factor)])
    return out


# ---------------------------------------------------------------------------
# new layers (TOOL-ROUTE-01)
# ---------------------------------------------------------------------------

def sign_layer(src: dict, proj: Projection, out_dir: str) -> list[dict]:
    """Custom-identity planes with their outward normal and the XP sign sprite.

    lower_plan_custom writes the plane tangent in transform column 0 and the
    face normal in column 1 (compile_sign_sprites.py uses the same basis).
    """
    plan_rows = {r.get("placement_id"): r for r in src["custom_plan"].get("rows", [])}
    sprites = {s.get("placement_id"): s for s in src["sign_sprites"].get("signs", [])}
    out = []
    for inst in sorted(src["custom_instances"], key=lambda i: str(i.get("placement_id"))):
        t = inst.get("transform") or []
        if len(t) < 15:
            continue
        pid = inst.get("placement_id")
        row = plan_rows.get(pid) or {}
        spr = sprites.get(pid) or {}
        tx, ty = float(t[0]), float(t[1])
        nx, ny = float(t[4]), float(t[5])
        crop = spr.get("crop")
        out.append({
            "id": pid,
            "label": row.get("label") or inst.get("subject"),
            "semantic_family": inst.get("semantic_family"),
            "parent": inst.get("parent"),
            "rendered_text": inst.get("rendered_text"),
            "lonlat": proj.world(float(t[12]), float(t[13])),
            "tangent_deg": r3(math.degrees(math.atan2(ty, tx))),
            "normal_deg": r3(math.degrees(math.atan2(ny, nx))),
            "width_m": r3(inst.get("plane_width_m")),
            "height_m": r3(inst.get("plane_height_m")),
            "mesh": inst.get("mesh_name"),
            "colour_basis": row.get("colour_basis"),
            "background_rgb": row.get("background_rgb"),
            "annotation_ids": row.get("annotation_ids"),
            "capture_ids": row.get("capture_ids"),
            "sprite": spr.get("sprite"),
            "sprite_cells": spr.get("cells"),
            "sprite_lonlat": (proj.world(float(spr["pos"][0]), float(spr["pos"][1]))
                              if spr.get("pos") else None),
            "crop": (os.path.relpath(os.path.join(REPO, crop), out_dir) if crop else None),
        })
    # FL-4876: canonical place-front logos (compile_sign_sprites kind
    # brand_front_logo) have no custom plane; show the XP sprite itself at its
    # wall-snapped position with its outward normal so it can be inspected.
    for spr in src["sign_sprites"].get("signs", []):
        if spr.get("kind") != "brand_front_logo" or not spr.get("pos"):
            continue
        nrm = spr.get("normal") or [1.0, 0.0]
        slot = spr.get("slot_m") or [None, None, None]
        crop = spr.get("crop")
        lonlat = proj.world(float(spr["pos"][0]), float(spr["pos"][1]))
        out.append({
            "id": spr.get("placement_id"),
            "label": spr.get("label"),
            "semantic_family": "place_front_logo",
            "parent": spr.get("placement_id"),
            "rendered_text": spr.get("label"),
            "lonlat": lonlat,
            "tangent_deg": r3(math.degrees(math.atan2(float(nrm[0]), -float(nrm[1])))),
            "normal_deg": r3(math.degrees(math.atan2(float(nrm[1]), float(nrm[0])))),
            "width_m": r3(slot[0]),
            "height_m": r3(slot[2] - slot[1]) if slot[1] is not None and slot[2] is not None else None,
            "mesh": None,
            "colour_basis": "capture-review crop (XP sprite)",
            "background_rgb": None,
            "annotation_ids": [spr.get("annotation_id")],
            "capture_ids": None,
            "sprite": spr.get("sprite"),
            "sprite_cells": spr.get("cells"),
            "sprite_lonlat": lonlat,
            "wall_snap": spr.get("wall_snap"),
            "crop": (os.path.relpath(os.path.join(REPO, crop), out_dir) if crop else None),
        })
    return out


def train_layer(src: dict, proj: Projection) -> list[dict]:
    """Compiled Classic train routes plus the stopped set envelope."""
    dims = (src["train_manifest"] or {}).get("dimensions_m") or {}
    set_len = float(dims.get("set_length") or 54.2)
    out = []
    for r in src["trains"].get("routes", []):
        upm = float(r.get("units_per_metre") or proj.scale)
        poly = r.get("polyline") or []
        if len(poly) < 2:
            continue
        pts = [proj.world(float(p[0]), float(p[1])) for p in poly]
        # arc length along the polyline in metres, to cut the stopped envelope
        s = [0.0]
        for a, b in zip(poly, poly[1:]):
            s.append(s[-1] + math.hypot(b[0] - a[0], b[1] - a[1]) / upm)
        stop = float(r.get("stop_s_m") or 0.0)
        lo, hi = stop - set_len / 2.0, stop + set_len / 2.0
        env = [pts[k] for k in range(len(pts)) if lo <= s[k] <= hi]
        out.append({
            "id": r.get("id"),
            "direction": r.get("direction"),
            "feature_ids": r.get("feature_ids"),
            "length_m": r3(r.get("length_m")),
            "stop_s_m": r3(stop),
            "stop_station_distance_m": r3(r.get("stop_station_distance_m")),
            "timetable": r.get("timetable"),
            "polyline": pts,
            "stopped_envelope": env,
            "set_length_m": set_len,
            "width_m": float(dims.get("width") or 2.8),
            "height_m": float(dims.get("height") or 4.05),
            "mesh": (src["trains"].get("mesh")),
        })
    return out


def rail_edge_layer(src: dict, proj: Projection, step: int = 3) -> list[dict]:
    """LiDAR-measured rail-corridor edges, every ``step``-th 2 m station."""
    out = []
    ways = src["rail_edges"].get("ways") or {}
    for wid in sorted(ways):
        st = ways[wid].get("stations") or []
        left, right = [], []
        for k in range(0, len(st), step):
            _s, e, n, tx, ty, lm, rm = st[k][:7]
            left.append(proj.utm(e - ty * lm, n + tx * lm) if lm is not None else None)
            right.append(proj.utm(e + ty * rm, n - tx * rm) if rm is not None else None)
        out.append({"id": wid, "left": left, "right": right,
                    "measured_left": sum(1 for p in left if p),
                    "measured_right": sum(1 for p in right if p)})
    return out


def lidar_layer(bbox_lonlat: tuple, grid_m: float) -> dict:
    """Decimated TMG LiDAR: per grid cell, the highest return above local ground.

    Ground is the lowest class-2 return in the surrounding 10 m cell. Points
    under 0.5 m above ground are dropped (they are the street surface).
    """
    import numpy as np
    from pyproj import Transformer
    from compile_lidar_roof_field import _load_las_arrays

    paths = sorted(os.path.join(REPO, LIDAR_DIR, n) for n in os.listdir(os.path.join(REPO, LIDAR_DIR))
                   if n.lower().endswith((".las", ".laz")))
    X, Y, Z, cls, _ret, _nret, total = _load_las_arrays(paths)
    to_src = Transformer.from_crs("EPSG:4326", LIDAR_CRS, always_xy=True)
    to_ll = Transformer.from_crs(LIDAR_CRS, "EPSG:4326", always_xy=True)
    (lo0, la0, lo1, la1) = bbox_lonlat
    xs, ys = to_src.transform([lo0, lo1, lo0, lo1], [la0, la0, la1, la1])
    box = (X >= min(xs)) & (X <= max(xs)) & (Y >= min(ys)) & (Y <= max(ys))
    X, Y, Z, cls = X[box], Y[box], Z[box], cls[box]
    gx0, gy0 = float(X.min()), float(Y.min())
    # local ground on a 10 m grid
    gi = ((X - gx0) // 10.0).astype(np.int64)
    gj = ((Y - gy0) // 10.0).astype(np.int64)
    gkey = gi * 100000 + gj
    ground = {}
    gm = cls == 2
    for k, z in zip(gkey[gm].tolist(), Z[gm].tolist()):
        if k not in ground or z < ground[k]:
            ground[k] = z
    # decimate: highest return per grid cell
    ci = ((X - gx0) // grid_m).astype(np.int64)
    cj = ((Y - gy0) // grid_m).astype(np.int64)
    ckey = ci * 1000000 + cj
    order = np.lexsort((-Z, ckey))
    first = np.ones(order.size, dtype=bool)
    first[1:] = ckey[order][1:] != ckey[order][:-1]
    sel = order[first]
    rows = []
    gz = [ground.get(int(k)) for k in gkey[sel].tolist()]
    lon, lat = to_ll.transform(X[sel], Y[sel])
    for k in range(sel.size):
        if gz[k] is None:
            continue
        h = float(Z[sel[k]]) - gz[k]
        if h < 0.5:
            continue
        rows.append([r6(lon[k]), r6(lat[k]), round(h, 1), int(cls[sel[k]])])
    rows.sort()
    return {"columns": ["lon", "lat", "height_above_ground_m", "las_class"],
            "rows": rows, "grid_m": grid_m, "crs_source": LIDAR_CRS,
            "points_in_bbox": int(box.sum()), "points_total": int(total),
            "sheets": [os.path.basename(p) for p in paths],
            "authority": "diagnostic heights/clearances only; OSM/PLATEAU own footprints"}


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="export the Numabe stage viewer payload")
    p.add_argument("--run-root", required=True, help="build run root (has terrain_metadata.json)")
    p.add_argument("--out", default=None, help="output directory (default <run-root>/viewer)")
    p.add_argument("--lidar-grid-m", type=float, default=2.0,
                   help="LiDAR decimation grid in metres; 0 disables the layer")
    a = p.parse_args(argv)
    run_root = os.path.abspath(a.run_root)
    out_dir = os.path.abspath(a.out or os.path.join(run_root, "viewer"))
    os.makedirs(out_dir, exist_ok=True)

    paths: dict[str, str] = {}
    for key, names in RUN_INPUTS.items():
        found = next((os.path.join(run_root, n) for n in names
                      if os.path.exists(os.path.join(run_root, n))), None)
        paths[key] = found or os.path.join(run_root, names[0])
    for key, rel in REPO_INPUTS.items():
        paths[key] = os.path.join(REPO, rel)

    src: dict[str, Any] = {}
    absent_inputs: list[str] = []
    for name, path in paths.items():
        if not os.path.exists(path):
            if name not in OPTIONAL_DEFAULTS:
                raise SystemExit("required input absent: %s (%s)" % (name, path))
            src[name] = OPTIONAL_DEFAULTS[name]
            absent_inputs.append(path)
            continue
        src[name] = load(path)
    if isinstance(src["spatial"].get("fail_rows"), int):
        src["spatial"]["fail_rows"] = []
    proj = Projection(src["terrain_metadata"])
    omitted: list[dict] = []

    capture_dir_rel = os.path.relpath(CAPTURE_DIR_ABS, out_dir)
    sheet_dir_rel = os.path.relpath(SHEET_DIR_ABS, out_dir)

    emitting_dispositions = {"terrain_mass", "mesh_fixture", "surface_decal", "mesh_facade"}
    view_plan_by_fid: dict[str, dict] = {}
    for plan_row in src["plan"].get("rows", []):
        fid = str(plan_row.get("feature_id"))
        old = view_plan_by_fid.get(fid)
        if old is None or (old.get("disposition") not in emitting_dispositions
                           and plan_row.get("disposition") in emitting_dispositions):
            view_plan_by_fid[fid] = plan_row

    def omit(layer: str, item: str, reason: str) -> None:
        omitted.append({"item": item, "layer": layer, "reason": reason})

    # -- ledger ------------------------------------------------------------
    ledger_rows = sorted(src["ledger"]["rows"], key=lambda r: r["feature_id"])
    for r in ledger_rows:
        r.setdefault("synthetic", False)
    ledger_index = {r["feature_id"]: i for i, r in enumerate(ledger_rows)}
    ledger01_row_count = len(ledger_rows)
    synthetic_rows: list[dict] = []

    # -- canonical entities ------------------------------------------------
    entity_by_id = {e["canonical_id"]: e for e in src["entities"]["entities"]}
    canonical_of_object: dict[str, str] = {}
    disposition_of_object: dict[str, dict] = {}
    for d in src["entities"]["dispositions"]:
        key = "%s:%s" % (d.get("source"), d.get("source_object_id"))
        canonical_of_object[key] = d["canonical_id"]
        disposition_of_object[key] = d

    mass_by_feature = {b["feature_id"]: b for b in src["building_instances"]}

    # -- footprints plus context geometry ----------------------------------
    footprints: list[dict] = []
    context: list[dict] = []
    place_lonlat: dict[str, list] = {}

    for feat in sorted(src["footprints"]["canonical_features"], key=lambda f: f["feature_id"]):
        fid = feat["feature_id"]
        cls = feat["semantic_class"]
        geom = feat["geometry"]
        props = feat.get("properties", {})

        def claim(name: str, default=None):
            node = props.get(name)
            return default if not isinstance(node, dict) else node.get("value", default)

        if geom["kind"] == "point":
            coords = proj.utm(*geom["coordinates"])
        elif geom["kind"] in ("polygon", "polyline"):
            coords = [proj.utm(*pt) for pt in geom["coordinates"]]
        else:
            omit("geometry", fid, "unsupported geometry kind %r" % geom["kind"])
            continue

        labels = feat.get("labels") or {}
        label = labels.get("name:en") or labels.get("name") or claim("name")

        if cls == "place":
            place_lonlat[fid] = coords

        if cls != "building_footprint":
            context.append({
                "authority": feat.get("gameplay_authority"),
                "class": cls,
                "coords": coords,
                "id": fid,
                "kind": geom["kind"],
                "label": label,
                "label_local": labels.get("name"),
                "ledger": ledger_index.get(fid),
                "triage_group": claim("triage_group"),
            })
            continue

        tail = fid.split(":")[-1]
        source_key = ("plateau:" + tail.split("/", 1)[1]) if tail.startswith("plateau/") else ("osm:" + tail)
        canonical_id = canonical_of_object.get(source_key)
        entity = entity_by_id.get(canonical_id) if canonical_id else None
        claims = (entity or {}).get("claims", {})

        def claim_field(name: str) -> dict | None:
            node = claims.get(name)
            if not isinstance(node, dict):
                return None
            return {
                "confidence": r3(node.get("confidence")),
                "evidence_state": node.get("evidence_state"),
                "method": node.get("method"),
                "source": node.get("source"),
                "value": node.get("value"),
            }

        mass = mass_by_feature.get(fid)
        height = None
        height_basis = None
        if mass and mass.get("roof_height_m") is not None:
            height = r2(mass["roof_height_m"])
            height_basis = mass.get("height_method")
        elif isinstance(claims.get("height_m"), dict):
            height = r2(claims["height_m"].get("value"))
            height_basis = claims["height_m"].get("method")
        elif claim("height_m") is not None:
            height = r2(claim("height_m"))
            height_basis = "canonical.world_ir height_m property"
        if height is None:
            omit("footprints", fid, "no height claim and no lowered mass; drawn flat")

        evidence = claim("evidence_state")
        if evidence is None and isinstance(claims.get("height_m"), dict):
            evidence = claims["height_m"].get("evidence_state")
        row = ledger_rows[ledger_index[fid]] if fid in ledger_index else None
        if row is None:
            disp = disposition_of_object.get(source_key) or {}
            synthetic_rows.append({
                "collision_owner": None,
                "debt_state": "representation_owed",
                "disposition": disp.get("disposition"),
                "evidence_state": "authoritative_observed",
                "feature_id": fid,
                "importance": None,
                "label": label,
                "navigation_owner": None,
                "reconstruction_class": None,
                "semantic_class": cls,
                "stage_refs": {"geometry": _display(paths["footprints"])},
                "synthetic": True,
                "synthetic_reason": "footprint has no feature_ledger row in this build",
                "synthetic_source_reason": disp.get("reason"),
                "visual_owner": None,
            })
            omit("footprints", fid, "no feature_ledger row; synthetic row emitted")

        footprints.append({
            "canonical_id": canonical_id,
            "claims": {k: v for k, v in sorted((n, claim_field(n)) for n in claims) if v is not None},
            "disposition": (disposition_of_object.get(source_key) or {}).get("disposition"),
            "evidence_state": evidence or (row or {}).get("evidence_state"),
            "footprint_iou": r3(claim("footprint_iou")),
            "footprint_prev_source": claim("footprint_prev_source"),
            "footprint_source": claim("footprint_source") or "osm",
            "height_basis": height_basis,
            "height_m": height,
            "id": fid,
            "label": label,
            "ledger": ledger_index.get(fid),
            "mass_family": (mass or {}).get("building_fallback", {}).get("family")
            if isinstance((mass or {}).get("building_fallback"), dict) else None,
            "area_m2": r2((mass or {}).get("footprint_area_m2")),
            "ring": coords,
            "storeys": claim("storeys") if claim("storeys") is not None else (mass or {}).get("storeys"),
            "triage_group": claim("triage_group"),
        })

    if synthetic_rows:
        ledger_rows = sorted(ledger_rows + synthetic_rows, key=lambda r: r["feature_id"])
        ledger_index = {r["feature_id"]: i for i, r in enumerate(ledger_rows)}
        for f in footprints:
            f["ledger"] = ledger_index.get(f["id"])
        for c in context:
            c["ledger"] = ledger_index.get(c["id"])

    # -- palette -----------------------------------------------------------
    palette = src["source_palette"]
    materials = palette["materials"]
    swatches = []
    for role, mat_id in sorted(palette["role_material"].items()):
        mat = materials.get(str(mat_id)) or {}
        swatches.append({
            "bg": mat.get("bg"), "fg": mat.get("fg"), "key": mat.get("key"),
            "material_id": int(mat_id), "provenance_class": mat.get("provenance_class"),
            "role": role, "source": mat.get("source"),
        })

    # -- front profiles from the locale pack chain -------------------------
    front_profiles: dict[str, dict] = {}
    for pack_name in ("pack_common_global", "pack_japan_urban", "pack_tokyo"):
        for profile in src[pack_name].get("place_front_profiles", []) or []:
            front_profiles[profile["id"]] = {
                "accent_rgb": profile.get("accent_rgb"),
                "body_rgb": profile.get("body_rgb"),
                "geometry_family": profile.get("geometry_family"),
                "id": profile["id"],
                "pack": src[pack_name].get("pack_id", pack_name),
                "primary_rgb": profile.get("primary_rgb"),
                "provenance": profile.get("provenance"),
            }

    # -- fronts ------------------------------------------------------------
    fronts = []
    for inst in sorted(src["front_receipt"]["instances"], key=lambda i: i["feature_id"]):
        profile = front_profiles.get(inst.get("resolved_profile_id") or "", {})
        if not profile:
            omit("fronts", inst["feature_id"],
                 "resolved_profile_id %r not in the loaded pack chain" % inst.get("resolved_profile_id"))
        yaw = float(inst["front_yaw_rad"])
        fronts.append({
            "association": inst.get("association"),
            "body_rgb": profile.get("body_rgb"),
            "category": inst.get("category"),
            "clearance_cells": r3(inst.get("clearance_cells")),
            "heading_deg": r3(math.degrees(yaw) % 360.0),
            "id": inst["feature_id"],
            "label": inst.get("label"),
            "lonlat": proj.world(*inst["front_anchor_world_xy"]),
            "parent": inst.get("parent_building_feature_id"),
            "fitted_width_m": r3(inst.get("fitted_width_m")),
            "profile_id": inst.get("resolved_profile_id"),
            "profile_pack": inst.get("resolved_profile_pack"),
            "proposal_status": inst.get("proposal_status"),
            "rendered_text": inst.get("rendered_text"),
            "representation_kind": inst.get("representation_kind"),
            "route_progress_m": r2(inst.get("route_progress_m")),
            "yaw_rad": r6(yaw),
        })
    for row in src["front_receipt"].get("excluded", []) or []:
        omit("fronts", row.get("feature_id", "?"), row.get("reason", "excluded by place_front_receipt"))

    # -- fixtures ----------------------------------------------------------
    fixtures = []
    for inst in sorted(src["fixture_instances"], key=lambda i: i["inst_name"]):
        t = inst["transform"]
        plan_row = view_plan_by_fid.get(str(inst["feature_id"])) or {}
        attachment = plan_row.get("attachment") or {}
        source_xy = inst.get("source_control_world_xy")
        fixtures.append({
            "id": inst["feature_id"],
            "inst_name": inst["inst_name"],
            "lonlat": proj.world(float(t[12]), float(t[13])),
            "source_lonlat": (proj.world(*source_xy) if source_xy else None),
            "host": (attachment.get("canonical_mass_id")
                     or attachment.get("canonical_building_id")
                     or attachment.get("canonical_carrier_id")),
            "anchor_kind": attachment.get("anchor_kind") or inst.get("mount_kind"),
            "mesh": inst.get("mesh_name"),
            "orientation_basis": (inst.get("provenance") or {}).get("orientation"),
            "road_clearance_m": r3(inst.get("road_centerline_clearance_m")),
            "subtype": inst.get("semantic_subtype") or inst.get("module_id"),
            "template_id": inst.get("template_id") or inst.get("placement_id"),
            "carrier_class": inst.get("carrier_class"),
            "yaw_rad": r6(math.atan2(t[1], t[0])),
        })

    # -- lowered linear physical instances --------------------------------
    linear_instances = []
    for inst in sorted(src["lower_linear_instances"], key=lambda i: str(i.get("inst_name"))):
        t = inst.get("transform") or []
        if len(t) < 14:
            omit("linear_instances", str(inst.get("inst_name")), "emitted transform absent")
            continue
        linear_instances.append({
            "id": str(inst.get("feature_id")),
            "inst_name": str(inst.get("inst_name")),
            "lonlat": proj.world(float(t[12]), float(t[13])),
            "module_id": inst.get("module_id"),
            "span_record": bool(inst.get("span_record")),
            "yaw_deg": r3(inst.get("yaw_deg")),
        })

    # -- new layers --------------------------------------------------------
    signs = sign_layer(src, proj, out_dir)
    trains = train_layer(src, proj)
    rail_edges = rail_edge_layer(src, proj)

    # -- camera poses ------------------------------------------------------
    on_disk = set()
    if os.path.isdir(CAPTURE_DIR_ABS):
        on_disk = {n for n in os.listdir(CAPTURE_DIR_ABS) if n.lower().endswith(".jpg")}

    def local_capture(picture_id: str) -> str | None:
        for name in ("%s.jpg" % picture_id, "mapillary_%s.jpg" % picture_id):
            if name in on_disk:
                return capture_dir_rel + "/" + name
        return None

    pictures = src["survey"]["providers"]["mapillary"]["pictures"]
    poses_rows = []
    downloaded_ids = set()
    for pic in sorted(pictures, key=lambda p: str(p["picture_id"])):
        if pic.get("lon") is None or pic.get("lat") is None:
            omit("poses", str(pic.get("picture_id")), "survey picture carries no position")
            continue
        heading = pic.get("heading_deg")
        if heading is None:
            omit("poses", str(pic["picture_id"]), "survey picture carries no compass heading")
        focal = pic.get("focal_35mm")
        if focal:
            hfov = r3(math.degrees(2.0 * math.atan(36.0 / (2.0 * float(focal)))))
            basis = "focal_35mm"
        else:
            hfov = DEFAULT_HFOV_DEG
            basis = "declared_default_no_source_focal"
        path = local_capture(str(pic["picture_id"]))
        if path:
            downloaded_ids.add(str(pic["picture_id"]))
        poses_rows.append([
            r6(pic["lon"]), r6(pic["lat"]),
            None if heading is None else r3(heading),
            hfov, basis, str(pic["picture_id"]),
            pic.get("capture_date"), pic.get("sequence_id"), path,
        ])

    # -- observation debt --------------------------------------------------
    debt_features = []
    debt_frames = []
    absent_frames: list[dict] = []
    for feat in sorted(src["observation_debt"]["features"], key=lambda f: f["feature_id"]):
        fid = feat["feature_id"]
        sheet = feat.get("sheet")
        sheet_rel = None
        if sheet:
            base = os.path.basename(sheet)
            if os.path.exists(os.path.join(SHEET_DIR_ABS, base)):
                sheet_rel = sheet_dir_rel + "/" + base
            else:
                omit("observation_debt", fid, "declared contact sheet %r is not on disk" % base)
        frame_ids = []
        for frame in sorted(feat.get("frames", []), key=lambda f: str(f["frame_id"])):
            pid = str(frame["frame_id"])
            frame_ids.append(pid)
            path = local_capture(pid)
            declared = frame.get("on_disk_path")
            if path:
                downloaded_ids.add(pid)
                frame_state = "on_disk"
            elif declared:
                frame_state = "declared_absent"
                absent_frames.append({
                    "declared_path": declared, "feature_id": fid, "label": feat.get("label"),
                    "observation_state": feat.get("observation_state"), "picture_id": pid,
                })
                omit("debt_frames", pid, "declared on_disk_path %r is absent" % declared)
            else:
                frame_state = "not_downloaded"
            debt_frames.append({
                "declared_path": declared,
                "frame_state": frame_state,
                "bearing_residual_deg": r3(frame.get("bearing_residual_deg")),
                "capture_time": frame.get("capture_time"),
                "distance_m": r3(frame.get("distance_m")),
                "feature_id": fid,
                "heading_deg": r3(frame.get("heading_deg")),
                "hfov_basis": frame.get("hfov_basis") or "declared_default_no_source_focal",
                "hfov_deg": r3(frame.get("hfov_deg") or DEFAULT_HFOV_DEG),
                "local_path": path,
                "lonlat": [r6(frame["lon"]), r6(frame["lat"])],
                "los_clear": frame.get("los_clear"),
                "picture_id": pid,
            })
        debt_features.append({
            "absent_frame_ids": sorted(x["picture_id"] for x in absent_frames if x["feature_id"] == fid),
            "frame_ids": frame_ids,
            "group": feat.get("group"),
            "id": fid,
            "importance": feat.get("importance"),
            "label": feat.get("label"),
            "ledger": ledger_index.get(fid),
            "lonlat": place_lonlat.get(fid),
            "observation_state": feat.get("observation_state"),
            "sheet": sheet_rel,
            "waiver": feat.get("waiver"),
            "waiver_reason": feat.get("waiver_reason"),
        })
        if fid not in place_lonlat:
            omit("observation_debt", fid, "no canonical place geometry to anchor the debt marker")

    referenced_files = {os.path.basename(row[8]) for row in poses_rows if row[8]}
    referenced_files |= {os.path.basename(f["local_path"]) for f in debt_frames if f["local_path"]}
    for name in sorted(on_disk - referenced_files):
        omit("captures", name, "downloaded frame matches no survey picture_id and no debt frame_id")

    by_content: dict[str, list] = {}
    for name in sorted(referenced_files):
        h = hashlib.sha256()
        with open(os.path.join(CAPTURE_DIR_ABS, name), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        by_content.setdefault(h.hexdigest(), []).append(name)
    duplicate_assets = [{"names": names, "sha256": dh}
                        for dh, names in sorted(by_content.items()) if len(names) > 1]

    # -- route -------------------------------------------------------------
    trip = src["route"]["trip"]
    route_shape: list[list[float]] = []
    for leg in trip["legs"]:
        route_shape.extend(decode_valhalla(leg["shape"]))
    route = {
        "locations": [{"lat": r6(l["lat"]), "lon": r6(l["lon"]), "type": l.get("type")}
                      for l in trip["locations"]],
        "shape": route_shape,
        "summary": {"length_km": r3(trip["summary"]["length"]), "time_s": r2(trip["summary"]["time"])},
    }

    # -- bounds ------------------------------------------------------------
    lons: list[float] = []
    lats: list[float] = []

    def note(coords) -> None:
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            lons.append(coords[0]); lats.append(coords[1])
        else:
            for c in coords:
                note(c)

    for f in footprints:
        note(f["ring"])
    for c in context:
        note(c["coords"])
    note(route_shape)
    for f in fronts + fixtures + linear_instances + signs:
        note(f["lonlat"])
    for t in trains:
        note(t["polyline"])
    for row in poses_rows:
        lons.append(row[0]); lats.append(row[1])

    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    mid_lat = (min_lat + max_lat) / 2.0
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * math.radians(mid_lat)) \
        + 1.175 * math.cos(4 * math.radians(mid_lat))
    m_per_deg_lon = 111412.84 * math.cos(math.radians(mid_lat)) \
        - 93.5 * math.cos(3 * math.radians(mid_lat))

    # -- acceptance chain (VIEWER-02) --------------------------------------
    ROUTE_VISIBLE_M = 20.0
    EMITTING = ("terrain_mass", "mesh_fixture", "surface_decal", "mesh_facade")
    plan_by_fid = {}
    for _pr in src["plan"].get("rows", []):
        _fid = str(_pr.get("feature_id"))
        _old = plan_by_fid.get(_fid)
        if _old is None or (_old.get("disposition") not in EMITTING
                            and _pr.get("disposition") in EMITTING):
            plan_by_fid[_fid] = _pr
    canon_by_fid = {str(f.get("feature_id")): f for f in src["canonical"].get("canonical_features", [])}
    canon_index = CanonicalIndex(src["canonical"])
    emitted_ids: set[str] = set()
    for _inst in src["fixture_instances"]:
        emitted_ids.add(str(_inst.get("feature_id")))
    for _inst in src["lower_linear_instances"]:
        if not _inst.get("span_record"):
            emitted_ids.add(str(_inst.get("feature_id")))
    for _inst in src["procedural_facade_instances"]:
        emitted_ids.add(str(_inst.get("feature_id")))
    for _inst in src["custom_instances"]:
        emitted_ids.add(str(_inst.get("feature_id")))
    for _b in src["building_instances"]:
        emitted_ids.add(str(_b.get("feature_id")))
    for _row in src["front_receipt"].get("instances", []):
        emitted_ids.add(str(_row.get("feature_id")))
    for _link in src["surface_receipt"].get("features", []):
        emitted_ids.add(str(_link.get("feature_id")))
    route_utm = [utm_forward(lon, lat) for lon, lat in route_shape]

    def _near_route(easting: float, northing: float) -> bool:
        return min(math.hypot(easting - s[0], northing - s[1]) for s in route_utm) <= ROUTE_VISIBLE_M

    def _canon_utm_lonlat(fid: str):
        feat = canon_by_fid.get(fid)
        if feat is None:
            return None, None
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates")
        if not coords:
            return None, None
        if geom.get("kind") == "point":
            e, n = float(coords[0]), float(coords[1])
        else:
            pts = coords[0] if isinstance(coords[0][0], (list, tuple)) else coords
            e = sum(float(q[0]) for q in pts) / len(pts)
            n = sum(float(q[1]) for q in pts) / len(pts)
        return (e, n), proj.utm(e, n)

    chain: dict[str, list] = {
        "emitted_physical": [], "emitted_proxy": [], "planned_not_emitted": [],
        "owed": [], "omitted": [], "spatial_fail": [], "mapillary": [], "bins": [],
    }
    for fid in sorted(plan_by_fid):
        row = plan_by_fid[fid]
        utm_ll = _canon_utm_lonlat(fid)
        if utm_ll[0] is None:
            continue
        (e, n), ll = utm_ll
        if not _near_route(e, n):
            continue
        disp = row.get("disposition")
        kind = row.get("representation_kind")
        emitted = fid in emitted_ids
        base = {"id": fid, "representation_id": row.get("representation_id"),
                "semantic_class": row.get("semantic_class"), "lonlat": ll}
        if disp in EMITTING and emitted:
            layer = "emitted_proxy" if kind == "synthetic_identity_proxy" else "emitted_physical"
            base["detail"] = {"asset_match": row.get("asset_match"),
                              "attachment_state": row.get("attachment_state")}
            chain[layer].append(base)
        elif disp in EMITTING and not emitted:
            ghost = None
            att = row.get("attachment")
            if att:
                try:
                    pose = resolve(att, canon_index)
                    ghost = proj.utm(float(pose["E"]), float(pose["N"]))
                except Exception:
                    ghost = None
            base["detail"] = {"ghost_lonlat": ghost, "attachment_state": row.get("attachment_state")}
            chain["planned_not_emitted"].append(base)
        elif disp in ("observation_owed", "representation_owed"):
            base["detail"] = {"jev_route": row.get("jev_route"), "evidence_state": row.get("evidence_state")}
            chain["owed"].append(base)
        elif disp in ("intentional_omission", "minimap_only"):
            base["detail"] = {"reason": row.get("reason")}
            chain["omitted"].append(base)
    for fail in src["spatial"].get("fail_rows", []):
        pos = fail.get("planned_position") or fail.get("emitted_position")
        if not pos:
            continue
        chain["spatial_fail"].append({
            "id": fail.get("feature_id"), "lonlat": proj.world(float(pos[0]), float(pos[1])),
            "detail": {"host": fail.get("canonical_host_id"), "collision": fail.get("collision_state"),
                       "reason": fail.get("reason"), "inst_name": fail.get("inst_name")}})
    map_obs = {str(o["observation_id"]): o for o in src["mapillary"].get("source_observations", [])}
    for feat in sorted(src["mapillary"].get("canonical_features", []), key=lambda f: str(f.get("feature_id"))):
        oids = [str(i) for i in feat.get("supporting_observation_ids", [])]
        obs = map_obs.get(oids[0]) if oids else None
        if obs is None:
            continue
        coords = (obs.get("geometry") or {}).get("coordinates")
        if not coords or len(coords) < 2:
            continue
        if not _near_route(*utm_forward(float(coords[0]), float(coords[1]))):
            continue
        frames = (feat.get("properties") or {}).get("frames_seen", {})
        chain["mapillary"].append({
            "id": str(feat.get("feature_id")),
            "lonlat": [r6(float(coords[0])), r6(float(coords[1]))],
            "detail": {"object_value": (feat.get("labels") or {}).get("object_value"),
                       "semantic_class": feat.get("semantic_class"),
                       "frames_seen": len(frames.get("value", []) or [])}})
    for b in src["inventory"].get("bins", []):
        endpoints = b.get("endpoints", [])
        if len(endpoints) != 2:
            continue
        groups = b.get("groups", {})
        chain["bins"].append({
            "id": "bin_%d" % int(b.get("bin", 0)),
            "lonlat": proj.utm((endpoints[0][0] + endpoints[1][0]) / 2.0,
                               (endpoints[0][1] + endpoints[1][1]) / 2.0),
            "detail": {"from_m": b.get("from_m"), "to_m": b.get("to_m"),
                       "observed": sum(g.get("observed", 0) for g in groups.values()),
                       "represented": sum(g.get("represented", 0) for g in groups.values())}})
    for _layer_rows in chain.values():
        _layer_rows.sort(key=lambda r: str(r["id"]))
    chain_counts = {layer: len(rows) for layer, rows in chain.items()}

    # -- LiDAR overlay (separate payload) ----------------------------------
    lidar = None
    if a.lidar_grid_m > 0 and os.path.isdir(os.path.join(REPO, LIDAR_DIR)):
        lidar = lidar_layer((min_lon, min_lat, max_lon, max_lat), a.lidar_grid_m)

    baked_walls, identity_conflicts = identity_and_walls(a.run_root, proj, fronts)

    counts = {
        "absent_frames": len(absent_frames),
        "baked_wall_points": len(baked_walls),
        "identity_conflicts": len(identity_conflicts),
        "captures_on_disk": len(on_disk),
        "captures_referenced": len(referenced_files),
        "context_features": len(context),
        "chain_emitted_physical": chain_counts["emitted_physical"],
        "chain_emitted_proxy": chain_counts["emitted_proxy"],
        "chain_planned_not_emitted": chain_counts["planned_not_emitted"],
        "chain_owed": chain_counts["owed"],
        "chain_omitted": chain_counts["omitted"],
        "chain_spatial_fail": chain_counts["spatial_fail"],
        "chain_mapillary": chain_counts["mapillary"],
        "chain_bins": chain_counts["bins"],
        "duplicate_assets": len(duplicate_assets),
        "debt_features": len(debt_features),
        "debt_frames": len(debt_frames),
        "fixtures": len(fixtures),
        "linear_instances": len(linear_instances),
        "footprints": len(footprints),
        "frames_on_disk": len(downloaded_ids),
        "fronts": len(fronts),
        "ledger_rows": ledger01_row_count,
        "ledger_rows_total": len(ledger_rows),
        "lidar_points": len(lidar["rows"]) if lidar else 0,
        "omitted": len(omitted),
        "palette_swatches": len(swatches),
        "poses": len(poses_rows),
        "rail_edge_ways": len(rail_edges),
        "route_vertices": len(route_shape),
        "signs": len(signs),
        "signs_with_sprite": sum(1 for s in signs if s["sprite"]),
        "synthetic_ledger_rows": len(synthetic_rows),
        "train_routes": len(trains),
    }

    def _fixture_count() -> int:
        fr = src["fixture_receipt"]
        return int((fr.get("counts") or {}).get("fixtures_emitted", fr.get("instances_emitted", 0)) or 0)

    payload = {
        "baked_walls": baked_walls,
        "identity_conflicts": identity_conflicts,
        "absent_frames": sorted(absent_frames, key=lambda x: (x["feature_id"], x["picture_id"])),
        "bounds": {"max_lat": r6(max_lat), "max_lon": r6(max_lon),
                   "min_lat": r6(min_lat), "min_lon": r6(min_lon)},
        "context": context,
        "chain": chain,
        "counts": counts,
        "debt_features": debt_features,
        "debt_frames": debt_frames,
        "duplicate_assets": duplicate_assets,
        "fixtures": fixtures,
        "linear_instances": linear_instances,
        "footprints": footprints,
        "front_profiles": front_profiles,
        "fronts": fronts,
        "ledger": ledger_rows,
        "meta": {
            "capture_dir_rel": capture_dir_rel,
            "default_cone_length_m": DEFAULT_CONE_LEN_M,
            "default_hfov_deg": DEFAULT_HFOV_DEG,
            "generated_from": [digest(pth) if pth not in absent_inputs else
                               {"path": _display(pth), "sha256": "absent", "bytes": 0,
                                "reason": "not_emitted_in_build"}
                               for _, pth in sorted(paths.items())],
            "m_per_deg_lat": r6(m_per_deg_lat),
            "m_per_deg_lon": r6(m_per_deg_lon),
            "origin_lat": r6(mid_lat),
            "origin_lon": r6((min_lon + max_lon) / 2.0),
            "picture_page_uri_template": "https://www.mapillary.com/app/?pKey={picture_id}",
            "projection": {
                "geographic": "EPSG:4326 lon/lat",
                "owner": "scripts/pipeline/geo_frames.utm_inverse (Kruger n-series, 6th order)",
                "projected": "EPSG:32654 (WGS84 / UTM zone 54N) metres",
                "world_affine": {k: src["terrain_metadata"][k] for k in
                                 ("content_scale", "offset_x", "offset_y",
                                  "origin_easting", "origin_northing")},
            },
            "run": os.path.basename(run_root),
            "schema": "y92.viewer_data.v2",
            "sheet_dir_rel": sheet_dir_rel,
            "task": "FL-4809 VIEWER-01, FL-4876 TOOL-ROUTE-01",
        },
        "omitted": sorted(omitted, key=lambda o: (o["layer"], o["item"], o["reason"])),
        "palette": swatches,
        "poses": {
            "columns": ["lon", "lat", "heading_deg", "hfov_deg", "hfov_basis",
                        "picture_id", "capture_date", "sequence_id", "local_path"],
            "rows": poses_rows,
        },
        "rail_edges": rail_edges,
        "receipt_counts": {
            "fixtures_emitted": _fixture_count(),
            "ledger_rows": src["ledger"]["counts"]["rows"],
            "place_fronts_emitted": src["front_receipt"]["counts"]["place_fronts_emitted"],
            "survey_pictures": src["observation_debt"]["frame_stats"]["survey_pictures"],
        },
        "route": route,
        "signs": signs,
        "trains": trains,
    }

    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    json_path = os.path.join(out_dir, "viewer_data.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(out_dir, "viewer_data.js"), "w", encoding="utf-8") as fh:
        fh.write("// generated by export_viewer_data.py - do not edit\n")
        fh.write("window.VIEWER_DATA = " + text.rstrip("\n") + ";\n")
    lidar_path = os.path.join(out_dir, "viewer_lidar.js")
    with open(lidar_path, "w", encoding="utf-8") as fh:
        fh.write("// generated by export_viewer_data.py - do not edit\n")
        fh.write("window.VIEWER_LIDAR = " + json.dumps(lidar, sort_keys=True, separators=(",", ":")) + ";\n")

    # static chain summary for headless proof, injected into the output copy
    with open(os.path.join(TEMPLATE_DIR, "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    start_marker, end_marker = "<!-- CHAIN-SUMMARY-START -->", "<!-- CHAIN-SUMMARY-END -->"
    start = html.index(start_marker) + len(start_marker)
    end = html.index(end_marker)
    summary_rows = "".join("<div>chain_%s %d</div>" % (k, chain_counts[k]) for k in sorted(chain_counts))
    absent_rows = "".join("<div>absent %s</div>" % _display(pth) for pth in sorted(absent_inputs))
    html = (html[:start] + "\n      <div id=\"chain-summary\">" + summary_rows + "</div>\n      "
            + "<div id=\"inputs-absent\">inputs_absent %d%s</div>\n      " % (len(absent_inputs), absent_rows)
            + html[end:])
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)

    receipt = {
        "counts": counts,
        "inputs": payload["meta"]["generated_from"],
        "lidar": None if lidar is None else {k: v for k, v in lidar.items() if k != "rows"},
        "outputs": {
            "index.html": os.path.getsize(os.path.join(out_dir, "index.html")),
            "viewer_data.json": os.path.getsize(json_path),
            "viewer_lidar.js": os.path.getsize(lidar_path),
        },
        "out_dir": out_dir,
        "receipt_counts": payload["receipt_counts"],
        "run_root": run_root,
        "schema": "y92.viewer_export.v2",
        "task": "FL-4809 VIEWER-01, FL-4876 TOOL-ROUTE-01",
    }
    with open(os.path.join(out_dir, "export_receipt.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    size_mb = os.path.getsize(json_path) / (1024.0 * 1024.0)
    print("viewer_data.json  %.2f MB   viewer_lidar.js  %.2f MB"
          % (size_mb, os.path.getsize(lidar_path) / (1024.0 * 1024.0)))
    for key in sorted(counts):
        print("  %-22s %d" % (key, counts[key]))
    print("open %s" % os.path.join(out_dir, "index.html"))
    if size_mb > 8.0:
        print("FAIL: payload exceeds the 8 MB budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
