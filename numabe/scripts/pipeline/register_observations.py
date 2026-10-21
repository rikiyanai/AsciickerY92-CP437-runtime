#!/usr/bin/env python3
"""Register reviewed Numabe observations to world positions (NUMABE-REG-01).

Reads the 727-row observation/module ledger plus the two annotation files that
carry per-annotation capture snapshots (lat/lon/heading/box_norm), casts one
bearing ray per non-discard annotation in EPSG:32654, intersects the ray with
OSM carriers parsed directly from the connected-scope .osm, clusters hits into
physical instances, and writes registered_instances.json, placement_zones.json,
registration_log.txt, and registration_plan.png.

Verification is the produced artifact set. No tests, gates, or manifests.
"""
from __future__ import annotations

import argparse
import json
import math
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------
# EPSG:32654 (UTM zone 54N, WGS84) forward projection.
# NOTE: scripts/pipeline/osm_projection.py only offers a scene-relative
# Transverse Mercator, not EPSG:32654, so the canonical UTM zone 54N forward
# series is implemented here and cross-checked against the task envelope
# bbox (E 379699.095-380113.054, N 3938417.877-3939043.068).
# --------------------------------------------------------------------------

_A = 6378137.0
_F = 1.0 / 298.257223563
_K0 = 0.9996
_LON0 = math.radians(141.0)


def lonlat_to_epsg32654(lat: float, lon: float) -> tuple[float, float]:
    e2 = 2 * _F - _F * _F
    e4 = e2 * e2
    e6 = e4 * e2
    ep2 = e2 / (1 - e2)
    lat_r = math.radians(lat)
    dlon = math.radians(lon) - _LON0
    N = _A / math.sqrt(1 - e2 * math.sin(lat_r) ** 2)
    T = math.tan(lat_r) ** 2
    C = ep2 * math.cos(lat_r) ** 2
    A_ = math.cos(lat_r) * dlon
    M = _A * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat_r
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat_r)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat_r)
        - (35 * e6 / 3072) * math.sin(6 * lat_r)
    )
    E = _K0 * N * (A_ + (1 - T + C) * A_ ** 3 / 6
                   + (5 - 18 * T + T * T + 72 * C - 58 * ep2) * A_ ** 5 / 120) + 500000.0
    Nn = _K0 * (M + N * math.tan(lat_r) * (A_ * A_ / 2
                + (5 - T + 9 * C + 4 * C * C) * A_ ** 4 / 24
                + (61 - 58 * T + T * T + 600 * C - 330 * ep2) * A_ ** 6 / 720))
    return E, Nn


# --------------------------------------------------------------------------
# 2D geometry helpers (Easting/Northing plane).
# --------------------------------------------------------------------------

def ray_segment_hit(ox, oy, dx, dy, ax, ay, bx, by):
    """Return ray parameter t (>0) of intersection, or None."""
    ex, ey = bx - ax, by - ay
    denom = dx * ey - dy * ex
    if abs(denom) < 1e-12:
        return None
    t = ((ax - ox) * ey - (ay - oy) * ex) / denom
    u = ((ax - ox) * dy - (ay - oy) * dx) / denom
    if t > 0 and 0.0 <= u <= 1.0:
        return t
    return None


def seg_azimuth(ax, ay, bx, by):
    return (math.degrees(math.atan2(by - ay, bx - ax))) % 360.0


CONF_VALUE = {"high": 0.9, "medium": 0.6, "low": 0.3}

ROAD_DEFAULT_WIDTH = {
    "motorway": 14.0, "trunk": 12.0, "primary": 12.0, "secondary": 9.0,
    "tertiary": 7.0, "residential": 6.0, "service": 4.0, "footway": 2.0,
    "pedestrian": 4.0, "steps": 2.0, "path": 2.0, "cycleway": 2.5,
    "unclassified": 5.0,
}

# FL-4876 REG-EDGE-01. Rail routing matches whole tokens of the placement
# context and module ids. The former substring keys ("rail", "crossing",
# "corridor", "station") sent 67 street rows rail-first: "rail" is inside
# guardrail/railing, "crossing" inside zebra_crossing, "corridor" inside
# roadside_utility_corridor, "station" inside station_or_storefront_forecourt.
# A rail-first row takes any rail hit within MAX_RANGE, so crosswalk guardrail
# obs-inst-0083 registered on the Shinkansen about 140 m from its camera.
RAIL_TOKENS = frozenset({"rail", "railway", "railroad", "railside", "platform",
                         "track", "tracks", "catenary", "gantry"})
RAIL_TOKEN_PAIRS = frozenset({("level", "crossing"), ("station", "name")})
# Edge-shaped modules: on a rail carrier they stand at the corridor edge, not
# on the track. They register against the measured LiDAR corridor edges.
EDGE_TOKENS = frozenset({"fence", "wall", "railing", "guardrail", "railguard",
                         "hedge", "retaining", "barrier"})
WATER_KEYS = ("water", "drain", "stream", "river", "culvert")
FACADE_KEYS = ("facad", "frontage", "building", "shop", "awn", "door", "window",
               "wall", "fence", "hedge", "boundary", "property", "commercial",
               "residential", "cultural", "kiosk", "storefront", "temple",
               "residence", "house", "apartment", "roof")


def _tokens(mapping: dict, keys: tuple[str, ...]) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", " ".join(
        str(mapping.get(k) or "") for k in keys).lower()) if t]


def is_rail_row(mapping: dict) -> bool:
    toks = _tokens(mapping, ("placement_context", "module_id", "custom_module_id"))
    return bool(RAIL_TOKENS.intersection(toks)) or bool(
        RAIL_TOKEN_PAIRS.intersection(zip(toks, toks[1:])))


def is_edge_module(mapping: dict) -> bool:
    return bool(EDGE_TOKENS.intersection(
        _tokens(mapping, ("module_id", "custom_module_id"))))


def carrier_order(mapping: dict) -> list[str]:
    s = " ".join([
        str(mapping.get("placement_context") or ""),
        str(mapping.get("carrier_type") or ""),
        str(mapping.get("module_id") or ""),
        str(mapping.get("custom_module_id") or ""),
    ]).lower()
    if is_rail_row(mapping):
        return ["railway", "road", "building", "barrier", "waterway"]
    if any(k in s for k in WATER_KEYS):
        return ["waterway", "barrier", "building", "road", "railway"]
    if any(k in s for k in FACADE_KEYS):
        return ["building", "barrier", "road", "railway", "waterway"]
    return ["road", "building", "barrier", "railway", "waterway"]


def group_key(mapping: dict) -> str:
    if mapping.get("module_id"):
        return "module:" + str(mapping["module_id"])
    if mapping.get("custom_subject_id"):
        return "custom_subject:" + str(mapping["custom_subject_id"])
    if mapping.get("custom_module_id"):
        return "custom_module:" + str(mapping["custom_module_id"])
    return "unresolved:" + str(mapping.get("carrier_type") or "unknown")


def parse_width(tags: dict, hwy: str) -> float:
    w = tags.get("width")
    if w:
        try:
            return float(str(w).strip().rstrip("m").strip())
        except ValueError:
            pass
    lanes = tags.get("lanes")
    if lanes:
        try:
            return float(str(lanes).split(";")[0].strip()) * 3.0
        except ValueError:
            pass
    return ROAD_DEFAULT_WIDTH.get(hwy, 6.0)


def offset_edges(centre: list[tuple[float, float]], half: float):
    """Build left/right offset polylines for a centreline via averaged normals."""
    n = len(centre)
    if n < 2:
        return [], []
    normals = []
    for i in range(n):
        a = centre[max(0, i - 1)]
        b = centre[min(n - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        normals.append((-dy / L, dx / L))
    left = [(x + nx * half, y + ny * half) for (x, y), (nx, ny) in zip(centre, normals)]
    right = [(x - nx * half, y - ny * half) for (x, y), (nx, ny) in zip(centre, normals)]
    return left, right


def poly_segments(pts):
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def authored_carriers(doc: dict) -> list[dict]:
    """Load explicitly reviewed carriers absent from the OSM carrier set.

    Some real attachment surfaces, notably the Numabe station building, are
    measured PLATEAU buildings with no OSM building way.  A fixture on such a
    wall must not borrow the nearest road merely so it can pass registration.
    These rows are complete hand-authored polylines with their source and
    basis recorded; this function performs validation only and never derives
    a carrier from a panorama ray.
    """
    result = []
    seen = set()
    for row in doc.get("authored_carriers", []) or []:
        required = ("carrier_id", "carrier_class", "polyline_epsg32654",
                    "source_feature_id", "basis")
        missing = [key for key in required if not row.get(key)]
        if missing:
            raise ValueError(
                f"authored carrier {row.get('carrier_id')} lacks {missing}")
        carrier_id = row["carrier_id"]
        if carrier_id in seen:
            raise ValueError(f"duplicate authored carrier {carrier_id}")
        points = [tuple(map(float, point))
                  for point in row["polyline_epsg32654"]]
        if len(points) < 2 or any(len(point) != 2 for point in points):
            raise ValueError(
                f"authored carrier {carrier_id} needs a 2D polyline")
        if str(row["carrier_class"]) not in {
                "building", "road", "railway", "waterway", "barrier"}:
            raise ValueError(
                f"authored carrier {carrier_id} has unsupported class "
                f"{row['carrier_class']}")
        seen.add(carrier_id)
        result.append({
            "id": carrier_id,
            "class": str(row["carrier_class"]),
            "segments": poly_segments(points),
            "polyline": points,
            "source_feature_id": str(row["source_feature_id"]),
            "authored_basis": str(row["basis"]),
        })
    return result


DEFAULT_AUTHORED_POSITIONS = ("assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
                              "/authored_observation_positions.json")
AUTHORED_POSITIONS_SCHEMA = "y92.fl4876.authored_observation_positions.v1"


def _seg_dist(px, py, a, b):
    ex, ey = b[0] - a[0], b[1] - a[1]
    L2 = ex * ex + ey * ey or 1e-12
    t = max(0.0, min(1.0, ((px - a[0]) * ex + (py - a[1]) * ey) / L2))
    return math.hypot(px - a[0] - ex * t, py - a[1] - ey * t)


DEFAULT_ID_LEDGER = ("assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
                     "/observation_instance_ids.v1.json")
ID_LEDGER_SCHEMA = "y92.fl4876.observation_instance_ids.v1"


def assign_instance_ids(clusters: list[list[str]], ledger: dict | None) -> list[str]:
    """FL-4876 REG-EDGE-01: keep instance ids stable across registration changes.

    Authored decisions key on obs-inst-NNNN. Sequential numbering by cluster
    order renumbers every later instance when one cluster splits or merges,
    which silently retargets those decisions. With a ledger (annotation id ->
    instance id), each cluster keeps the id most of its annotations held,
    largest cluster first, each id at most once; a cluster with no free
    ledger id takes the next id after the ledger maximum. Without a ledger,
    ids stay sequential in cluster order.
    """
    if not ledger:
        return [f"inst-{k:04d}" for k in range(len(clusters))]
    a2i = ledger["annotation_to_instance"]
    taken: set[str] = set()
    out: list[str | None] = [None] * len(clusters)
    nxt = 1 + max(int(v.split("-")[1]) for v in a2i.values())
    for k in sorted(range(len(clusters)), key=lambda k: (-len(clusters[k]), min(clusters[k]))):
        votes = Counter(a2i[a] for a in clusters[k] if a in a2i)
        for cand, _ in sorted(votes.items(), key=lambda kv: (-kv[1], kv[0])):
            if cand not in taken:
                out[k] = cand
                break
        if out[k] is None:
            out[k] = f"inst-{nxt:04d}"
            nxt += 1
        taken.add(out[k])
    return out


def id_ledger_doc(instances: list[dict], source: str) -> dict:
    return {"schema": ID_LEDGER_SCHEMA, "source": source,
            "annotation_to_instance": {a: i["instance_id"] for i in instances
                                       for a in i["annotation_ids"]}}


# Bundle root, not tmg_pointcloud/ (gitignored LAS cache): the measured edges
# must reach a clone that has no LAS tiles.
DEFAULT_RAIL_EDGES = ("assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
                      "/rail_corridor_edges.v1.json")


GROUND_CAM_HEIGHT_M = 2.0   # Mapillary street cameras: helmet, backpack, car roof
GROUND_MIN_DEPRESSION_DEG = 3.0
GROUND_WINDOW = 2.5         # accepted range factor around the ground-contact range
RAIL_INSIDE_DEFAULT_M = 6.0
RAIL_EDGE_GAP_FILL_M = 10.0
RAIL_MIN_CROSSING_DEG = 20.0


class RailCorridorEdges:
    """FL-4876 REG-EDGE-01: measured corridor edges beside each OSM rail way.

    Produced offline by compile_lidar_rail_corridor_edges.py from the TMG
    cloud. Stations carry left/right edge distances (None = not measured);
    left is along the way's left normal (-ty, tx).
    """

    def __init__(self, doc: dict):
        self.step = float(doc["parameters"]["step"])
        self.ways = {int(w): v["stations"] for w, v in doc["ways"].items()}
        self.segments: list[tuple[int, tuple, tuple]] = []
        for wid, st in self.ways.items():
            for col, sgn in ((5, 1.0), (6, -1.0)):
                for s0, s1 in zip(st, st[1:]):
                    if s0[col] is None or s1[col] is None or s1[0] - s0[0] > 2.5 * self.step:
                        continue
                    self.segments.append((wid, self._offset(s0, sgn * s0[col]),
                                          self._offset(s1, sgn * s1[col])))

    @staticmethod
    def _offset(st, d):
        return (st[1] - st[4] * d, st[2] + st[3] * d)

    def station(self, wid: int, e: float, n: float, max_dist: float = 25.0):
        st = self.ways.get(int(wid))
        if not st:
            return None
        best = min(st, key=lambda s: (s[1] - e) ** 2 + (s[2] - n) ** 2)
        return best if math.hypot(best[1] - e, best[2] - n) <= max_dist else None

    def side_near(self, wid: int, s_m: float, col: int, max_gap_m: float):
        """Measured edge distance on one side at chainage s_m, else the nearest
        measured station within max_gap_m along the way, else None."""
        best = None
        for st in self.ways.get(int(wid), []):
            if st[col] is not None and abs(st[0] - s_m) <= max_gap_m:
                if best is None or abs(st[0] - s_m) < abs(best[0] - s_m):
                    best = st
        return None if best is None else best[col]

    def nearest_edge_hit(self, ox, oy, dx, dy, max_range):
        best = None
        for wid, a, b in self.segments:
            t = ray_segment_hit(ox, oy, dx, dy, a[0], a[1], b[0], b[1])
            if t is not None and t <= max_range and (best is None or t < best[0]):
                best = (t, wid)
        return best


def rail_edge_position(edges: RailCorridorEdges, cam_E, cam_N, dx, dy, t_c, way_id,
                       max_range, rel_bearing_deg: float, heading_deg: float):
    """Corridor-edge position for an edge-shaped rail observation.

    Returns (t, E, N, way_id, rule) or None to keep the centreline hit.
    A camera inside the corridor (cab, platform) sees along the tracks: its
    ray meets the centreline at a shallow angle, so the along position of
    that hit is kept and the point moves perpendicular to the measured edge
    on the side the ray turns to. A camera outside the corridor sees the
    first edge its ray crosses.

    Inside the corridor the side comes from the box position relative to the
    image centre (rel_bearing_deg, + = right of the camera heading), mapped
    onto the way through the heading. The absolute ray bearing is not used
    for the side: a cab heading can be 9 deg off the track (obs-inst-0150),
    and a ray nearly parallel to the track then points to the wrong side.
    """
    hx, hy = cam_E + dx * t_c, cam_N + dy * t_c
    st_cam = edges.station(way_id, cam_E, cam_N)
    inside = False
    if st_cam is not None:
        lat = (cam_E - st_cam[1]) * -st_cam[4] + (cam_N - st_cam[2]) * st_cam[3]
        lim = st_cam[5] if lat >= 0 else st_cam[6]
        # an unmeasured side counts as RAIL_INSIDE_DEFAULT_M wide (cab GPS sits on the track)
        inside = abs(lat) <= (lim if lim is not None else RAIL_INSIDE_DEFAULT_M)
    if inside:
        st = edges.station(way_id, hx, hy, max_dist=3.0)
        if st is None:
            return None
        hx_, hy_ = math.sin(math.radians(heading_deg)), math.cos(math.radians(heading_deg))
        forward = (hx_ * st[3] + hy_ * st[4]) >= 0.0
        right_of_heading = math.sin(math.radians(rel_bearing_deg)) > 0.0
        # facing along the way, right of the heading is the way's right (sgn -1)
        sgn = -1.0 if right_of_heading == forward else 1.0
        d = edges.side_near(way_id, st[0], 5 if sgn > 0 else 6, RAIL_EDGE_GAP_FILL_M)
        if d is None:
            return None
        E = st[1] - st[4] * sgn * d + st[3] * ((hx - st[1]) * st[3] + (hy - st[2]) * st[4])
        N = st[2] + st[3] * sgn * d + st[4] * ((hx - st[1]) * st[3] + (hy - st[2]) * st[4])
        return (math.hypot(E - cam_E, N - cam_N), E, N, way_id, "inside_corridor_perpendicular")
    # from outside, the ray meets a corridor edge before the centreline it hit
    hit = edges.nearest_edge_hit(cam_E, cam_N, dx, dy, min(max_range, t_c + 1.0))
    if hit is None:
        return None
    t, wid = hit
    E, N = cam_E + dx * t, cam_N + dy * t
    st = edges.station(wid, E, N, max_dist=15.0)
    if st is None or abs(dx * -st[4] + dy * st[3]) < math.sin(math.radians(RAIL_MIN_CROSSING_DEG)):
        return None  # a shallow crossing fixes no along position; keep the centreline hit
    return (t, E, N, wid, "outside_corridor_first_edge")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--osm", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--rail-corridor-edges", default=None,
                    help="compile_lidar_rail_corridor_edges.py output; default: the bundled "
                         f"{DEFAULT_RAIL_EDGES} when present; 'none' disables")
    ap.add_argument("--authored-positions", default=None,
                    help="hand placements from visual review; default: the bundled "
                         f"{DEFAULT_AUTHORED_POSITIONS} when present; 'none' disables")
    ap.add_argument("--id-ledger", default=None,
                    help="annotation->instance id ledger that keeps instance ids stable; default: "
                         f"the bundled {DEFAULT_ID_LEDGER} when present; 'none' disables")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    # --- ledger -----------------------------------------------------------
    rows = [json.loads(line) for line in open(args.ledger) if line.strip()]
    live = [r for r in rows
            if (r.get("mapping") or {}).get("population_role") != "discard"]
    discards = len(rows) - len(live)
    # FL-4876 COHERENCE-ZERO-01: authored omissions. A visually reviewed
    # annotation that has no defensible playable position (camera registered
    # inside the building it photographs) or whose mapping names the wrong
    # object class is skipped like a reviewed discard. The observation stays in
    # the ledger; the authored file records who decided it and why.
    _ap_path = args.authored_positions
    if _ap_path is None:
        _cand = Path(__file__).resolve().parents[2] / DEFAULT_AUTHORED_POSITIONS
        _ap_path = str(_cand) if _cand.is_file() else None
    authored_omitted: dict[str, str] = {}
    if _ap_path and _ap_path != "none":
        for _om in json.loads(Path(_ap_path).read_text(encoding="utf-8")).get("omissions", []):
            for _aid in _om["annotation_ids"]:
                authored_omitted[_aid] = _om["decision_id"]
    live = [r for r in live if r["annotation_id"] not in authored_omitted]

    # --- annotations (box + snapshot) -------------------------------------
    # Old + new annotation files resolved from ledger crop_path provenance:
    #   old .../fl4813_capture_review_20260915/crops/ann-178943*.png
    #   new .../fl4810_aoi_street_network_boxing_20260915/crops/ann-178950*.png
    repo = str(Path(__file__).resolve().parents[2])
    new_ann_path = ("artifacts/experiments/fl4810_aoi_street_network_boxing_20260915"
                    "/annotations.json")
    old_ann_path = "artifacts/experiments/fl4813_capture_review_20260915/annotations.json"
    ann_by_id: dict[str, dict] = {}
    for p in (os.path.join(repo, new_ann_path), os.path.join(repo, old_ann_path)):
        d = json.load(open(p))
        for a in d["annotations"]:
            ann_by_id[a["annotation_id"]] = a

    missing = [r["annotation_id"] for r in live if r["annotation_id"] not in ann_by_id]
    if missing:
        print(f"MISSING annotations: {len(missing)} e.g. {missing[:3]}", file=sys.stderr)
        return 2

    # --- OSM carriers ------------------------------------------------------
    tree = ET.parse(args.osm)
    root = tree.getroot()
    node_xy: dict[str, tuple[float, float]] = {}
    for nd in root.iter("node"):
        try:
            lat, lon = float(nd.get("lat")), float(nd.get("lon"))
        except (TypeError, ValueError):
            continue
        node_xy[nd.get("id")] = lonlat_to_epsg32654(lat, lon)

    carriers: list[dict] = []  # {id, class, segments, polyline}
    for w in root.iter("way"):
        wid = int(w.get("id"))
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        refs = [n.get("ref") for n in w.iter("nd")]
        pts = [node_xy[r] for r in refs if r in node_xy]
        if len(pts) < 2:
            continue
        if "building" in tags:
            if pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            carriers.append({"id": wid, "class": "building",
                             "segments": poly_segments(pts), "polyline": pts})
        elif "highway" in tags:
            hwy = str(tags["highway"])
            half = parse_width(tags, hwy) / 2.0
            left, right = offset_edges(pts, half)
            segs = poly_segments(left) + poly_segments(right)
            carriers.append({"id": wid, "class": "road:" + hwy,
                             "segments": segs, "polyline": pts})
        elif tags.get("railway") == "rail":
            carriers.append({"id": wid, "class": "railway",
                             "segments": poly_segments(pts), "polyline": pts})
        elif "waterway" in tags:
            carriers.append({"id": wid, "class": "waterway",
                             "segments": poly_segments(pts), "polyline": pts})
        elif "barrier" in tags:
            carriers.append({"id": wid, "class": "barrier",
                             "segments": poly_segments(pts), "polyline": pts})

    by_class: dict[str, list[dict]] = defaultdict(list)
    for c in carriers:
        top = c["class"].split(":")[0]
        by_class[top].append(c)

    # --- measured rail corridor edges (FL-4876 REG-EDGE-01) -----------------
    rail_edges = None
    rail_edges_path = args.rail_corridor_edges
    if rail_edges_path is None and os.path.isfile(os.path.join(repo, DEFAULT_RAIL_EDGES)):
        rail_edges_path = os.path.join(repo, DEFAULT_RAIL_EDGES)
    rail_edges_sha = None
    if rail_edges_path and rail_edges_path != "none":
        with open(rail_edges_path, "rb") as fh:
            raw = fh.read()
        rail_edges_sha = hashlib.sha256(raw).hexdigest()
        rail_edges = RailCorridorEdges(json.loads(raw))
    edge_rule_counts = Counter()
    ground_rule_counts = Counter()

    # --- authored hand placements (FL-4876 MANUAL-PLACE-01) -----------------
    carrier_by_id = {c["id"]: c for c in carriers}
    authored_path = args.authored_positions
    if authored_path is None and os.path.isfile(os.path.join(repo, DEFAULT_AUTHORED_POSITIONS)):
        authored_path = os.path.join(repo, DEFAULT_AUTHORED_POSITIONS)
    authored_by_ann, authored_sha, authored_applied = {}, None, set()
    authored_objects: list[dict] = []
    if authored_path and authored_path != "none":
        with open(authored_path, "rb") as fh:
            raw = fh.read()
        authored_sha = hashlib.sha256(raw).hexdigest()
        adoc = json.loads(raw)
        if adoc.get("schema") != AUTHORED_POSITIONS_SCHEMA:
            print(f"authored positions schema mismatch: {authored_path}", file=sys.stderr)
            return 2
        try:
            extra_carriers = authored_carriers(adoc)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        for carrier in extra_carriers:
            if carrier["id"] in carrier_by_id:
                print(f"authored carrier duplicates source carrier {carrier['id']}",
                      file=sys.stderr)
                return 2
            carriers.append(carrier)
            carrier_by_id[carrier["id"]] = carrier
            by_class[carrier["class"].split(":")[0]].append(carrier)
        for d in adoc["positions"]:
            if int(d["carrier_id"]) not in carrier_by_id:
                print(f"authored position {d['decision_id']} names unknown carrier {d['carrier_id']}",
                      file=sys.stderr)
                return 2
            for aid in d["annotation_ids"]:
                authored_by_ann[aid] = d
        for d in adoc.get("authored_objects", []) or []:
            required = ("decision_id", "instance_id", "module_id", "E", "N",
                        "height_m", "yaw_deg", "carrier_id", "capture_ids", "basis")
            missing_fields = [key for key in required if d.get(key) is None]
            if missing_fields:
                print(f"authored object {d.get('decision_id')} lacks {missing_fields}",
                      file=sys.stderr)
                return 2
            if int(d["carrier_id"]) not in carrier_by_id:
                print(f"authored object {d['decision_id']} names unknown carrier {d['carrier_id']}",
                      file=sys.stderr)
                return 2
            authored_objects.append(d)

    # MANUAL-WORLD-03: a photograph proves that an object was visible; it
    # does not survey that object's world position.  Therefore an annotation
    # may enter the instance set only after an explicit authored placement
    # names its E/N and carrier.  The old fallback below ray-cast every review
    # box onto whichever building/road/rail segment happened to intersect the
    # camera ray.  That produced duplicated signs and fixtures on unrelated
    # buildings.  Keep those rows as evidence/debt, never as geometry.
    identity_delegated = [
        {
            "annotation_id": r["annotation_id"],
            "capture_id": r["capture_id"],
            "mapping": r.get("mapping") or {},
            "reason": "identity_owned_by_mapped_place_manual_face_resolver",
        }
        for r in live
        if (r.get("mapping") or {}).get("population_role") == "custom_identity"
        and r["annotation_id"] not in authored_by_ann
    ]
    manual_pending = [
        {
            "annotation_id": r["annotation_id"],
            "capture_id": r["capture_id"],
            "mapping": r.get("mapping") or {},
            "reason": "manual_world_placement_required",
        }
        for r in live
        if (r.get("mapping") or {}).get("population_role") != "custom_identity"
        and r["annotation_id"] not in authored_by_ann
    ]
    # MANUAL-WORLD-03 admits an annotation once an explicit authored
    # placement names its E/N and carrier -- identity annotations included.
    # A hand-placed identity sighting registers with the reviewed subject
    # so the mapped-place resolver can claim it; unauthored identity rows
    # stay delegated above and never become geometry on their own.
    live = [
        r for r in live
        if r["annotation_id"] in authored_by_ann
    ]

    # --- ray casting -------------------------------------------------------
    DEFAULT_CAM_ALT = 10.7  # median AMSL of the 9 captures carrying altitude_m
    MAX_RANGE = 150.0
    FALLBACK_RANGE = 8.0

    obs: list[dict] = []
    no_carrier_detail = Counter()
    for r in live:
        ann = ann_by_id[r["annotation_id"]]
        snap = ann.get("capture_snapshot") or {}
        box = ann.get("box_norm") or [0.45, 0.45, 0.1, 0.1]
        # FL-4876 REG-EDGE-01: box_norm is [left, top, width, height]
        # (capture_review_server.py validation and crop). The ray goes through
        # the box centre; it went through the top-left corner before, a
        # bearing error of half the box width (60 deg for a third-wide box in
        # a 360 deg panorama).
        cx = float(box[0]) + float(box[2]) / 2.0
        cy = float(box[1]) + float(box[3]) / 2.0
        cam_E, cam_N = lonlat_to_epsg32654(float(snap["lat"]), float(snap["lon"]))
        heading = float(snap["heading_deg"])
        proj = snap.get("projection_model") or "perspective"
        W = float(snap.get("width") or 1024)
        H = float(snap.get("height") or 768)
        hfov = snap.get("horizontal_fov_deg") or (360.0 if proj == "equirectangular" else 60.0)
        hfov = float(hfov)
        bearing = (heading + (cx - 0.5) * hfov) % 360.0
        br = math.radians(bearing)
        dx, dy = math.sin(br), math.cos(br)
        if proj == "equirectangular":
            # 2:1 equirectangular: 180 deg down (was 180 * H / W = 90 deg,
            # which halved the elevation behind height_m)
            vfov = 360.0 * (H / W)
        else:
            vfov = 2.0 * math.degrees(math.atan(math.tan(math.radians(hfov / 2)) * (H / W)))
        voff = math.radians((0.5 - cy) * vfov)
        cam_alt = float(snap["altitude_m"]) if snap.get("altitude_m") is not None else DEFAULT_CAM_ALT
        mapping = r.get("mapping") or {}
        # FL-4876 JEV-VISUAL-01: a reviewed reclassification. The photo shows
        # a different object than the review label; the authored row names
        # the module the photo shows, and grouping follows it.
        _reclass = (authored_by_ann.get(r["annotation_id"]) or {}).get(
            "module_id_override")
        if _reclass:
            mapping = dict(mapping, module_id=str(_reclass))
        order = carrier_order(mapping)
        best_by_class = {}
        for cls in order:
            best = None
            for c in by_class.get(cls, []):
                for (a, b) in c["segments"]:
                    t = ray_segment_hit(cam_E, cam_N, dx, dy, a[0], a[1], b[0], b[1])
                    if t is not None and t <= MAX_RANGE and (best is None or t < best[0]):
                        best = (t, c, a, b)
            if best is not None:
                best_by_class[cls] = best
        hit = next((best_by_class[cls] for cls in order if cls in best_by_class), None)
        # FL-4876 REG-EDGE-01 ground-contact window. A box whose bottom lies
        # below the horizon shows where the object meets the ground, so a
        # level camera GROUND_CAM_HEIGHT_M high sees it at about
        # h / tan(depression). A preferred-class hit far beyond that range is
        # a ray that missed the object's real carrier (utility pole
        # obs-inst-0417 stands just outside a corner; its ray grazed the
        # corner and hit a building 22 m away). Then the hit nearest that
        # range from any class wins, when one lies inside the window.
        depression = (float(box[1]) + float(box[3]) - 0.5) * vfov
        ground_range = None
        if depression >= GROUND_MIN_DEPRESSION_DEG:
            ground_range = GROUND_CAM_HEIGHT_M / math.tan(math.radians(depression))
        if hit is not None and ground_range is not None and hit[0] > GROUND_WINDOW * ground_range + 3.0:
            lo, hi = ground_range / GROUND_WINDOW, GROUND_WINDOW * ground_range + 3.0
            near = [b for b in best_by_class.values() if lo <= b[0] <= hi]
            if near:
                hit = min(near, key=lambda b: abs(math.log(b[0] / ground_range)))
                ground_rule_counts["reselected_hit_in_ground_window"] += 1
            else:
                ground_rule_counts["far_hit_kept_no_hit_in_window"] += 1
        if hit is not None:
            t, c, a, b = hit
            E, N = cam_E + dx * t, cam_N + dy * t
            yaw = seg_azimuth(a[0], a[1], b[0], b[1])
            anchored, carrier_id = True, c["id"]
            anchor = [list(a), list(b)]
            if (rail_edges is not None and c["class"] == "railway"
                    and is_edge_module(mapping)):
                moved = rail_edge_position(rail_edges, cam_E, cam_N, dx, dy, t,
                                           c["id"], MAX_RANGE, (cx - 0.5) * hfov, heading)
                if moved is None:
                    edge_rule_counts["centreline_kept_no_measured_edge"] += 1
                else:
                    t, E, N, carrier_id, rule = moved
                    edge_rule_counts[rule] += 1
                    st = rail_edges.station(carrier_id, E, N, max_dist=15.0)
                    if st is not None:
                        a = (st[1] - st[3], st[2] - st[4])
                        b = (st[1] + st[3], st[2] + st[4])
                        yaw = seg_azimuth(a[0], a[1], b[0], b[1])
                        anchor = [list(a), list(b)]
            height = cam_alt + t * math.tan(voff)
            rng = t
        else:
            E, N = cam_E + dx * FALLBACK_RANGE, cam_N + dy * FALLBACK_RANGE
            yaw = (90.0 - bearing) % 360.0
            anchored, carrier_id = False, None
            anchor = None
            height = cam_alt + FALLBACK_RANGE * math.tan(voff)
            rng = FALLBACK_RANGE
            no_carrier_detail[str(mapping.get("placement_context") or "?")] += 1
        # FL-4876 MANUAL-PLACE-01: a hand placement from visual review of the
        # photo and plan overrides the ray result for its annotations.
        authored = authored_by_ann.get(r["annotation_id"])
        if authored is not None:
            c = carrier_by_id[int(authored["carrier_id"])]
            E, N = float(authored["E"]), float(authored["N"])
            a, b = min(c["segments"], key=lambda ab: _seg_dist(E, N, ab[0], ab[1]))
            yaw = seg_azimuth(a[0], a[1], b[0], b[1])
            anchored, carrier_id, anchor = True, c["id"], [list(a), list(b)]
            rng = math.hypot(E - cam_E, N - cam_N)
            height = cam_alt + rng * math.tan(voff)
            authored_applied.add(authored["decision_id"])
        # The reviewed box constrains a bearing interval, not a surveyed
        # world point.  Preserve that interval as a metric placement
        # uncertainty so canonical placement may reconcile an OSM-ray hit
        # with a newer authoritative building footprint without pretending
        # the raw intersection was exact.
        box_half_angle = math.radians(
            max(0.0, float(box[2])) * hfov / 2.0)
        position_uncertainty_m = max(
            0.25, abs(rng * math.tan(box_half_angle)))
        obs.append({
            "annotation_id": r["annotation_id"], "capture_id": r["capture_id"],
            "key": group_key(mapping), "module_id": mapping.get("module_id"),
            "custom_module_id": mapping.get("custom_module_id"),
            "custom_subject_id": mapping.get("custom_subject_id"),
            "variant": mapping.get("variant_id"),
            "population_role": mapping.get("population_role"),
            "confidence": CONF_VALUE.get(str(mapping.get("confidence") or "").lower(), 0.5),
            "E": E, "N": N, "height": height, "yaw": yaw,
            "carrier_id": carrier_id, "range_m": rng, "anchored": anchored,
            "position_uncertainty_m": position_uncertainty_m,
            "position_uncertainty_basis":
                "review_box_horizontal_angular_half_extent_at_ray_range",
            "anchor": anchor,
            "bearing": bearing,
        })

    # --- clustering: same key + same carrier within 2.0 m ------------------
    parent = list(range(len(obs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    buckets: dict[tuple, list[int]] = defaultdict(list)
    for i, o in enumerate(obs):
        buckets[(o["key"], o["carrier_id"])].append(i)
    for idxs in buckets.values():
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                i, j = idxs[a], idxs[b]
                if math.hypot(obs[i]["E"] - obs[j]["E"], obs[i]["N"] - obs[j]["N"]) <= 2.0:
                    union(i, j)
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(obs)):
        clusters[find(i)].append(i)

    id_ledger = None
    id_ledger_path = args.id_ledger
    if id_ledger_path is None and os.path.isfile(os.path.join(repo, DEFAULT_ID_LEDGER)):
        id_ledger_path = os.path.join(repo, DEFAULT_ID_LEDGER)
    id_ledger_sha = None
    if id_ledger_path and id_ledger_path != "none":
        with open(id_ledger_path, "rb") as fh:
            raw = fh.read()
        id_ledger_sha = hashlib.sha256(raw).hexdigest()
        id_ledger = json.loads(raw)
        if id_ledger.get("schema") != ID_LEDGER_SCHEMA:
            print(f"id ledger schema mismatch: {id_ledger_path}", file=sys.stderr)
            return 2
    ordered = sorted(clusters.items())
    inst_ids = assign_instance_ids(
        [sorted(obs[i]["annotation_id"] for i in idxs) for _, idxs in ordered], id_ledger)

    instances = []
    for k, (root_i, idxs) in enumerate(ordered):
        members = [obs[i] for i in idxs]
        first = members[0]
        E = sum(m["E"] for m in members) / len(members)
        N = sum(m["N"] for m in members) / len(members)
        H = sum(m["height"] for m in members) / len(members)
        anchored = any(m["anchored"] for m in members)
        yaw_src = next((m for m in members if m["anchored"]), first)
        carrier_ids = sorted({m["carrier_id"] for m in members if m["carrier_id"] is not None})
        anchor = yaw_src.get("anchor")
        instances.append({
            "instance_id": inst_ids[k],
            "module_id": first["module_id"] or first["custom_module_id"],
            "custom_identity_subject": first["custom_subject_id"],
            "variant": first["variant"],
            "population_role": first["population_role"],
            "E": E, "N": N, "height_m": H,
            "yaw_deg": yaw_src["yaw"],
            "carrier_id": carrier_ids[0] if len(carrier_ids) == 1 else (carrier_ids if carrier_ids else None),
            "anchor_segment": ({"E0": anchor[0][0], "N0": anchor[0][1],
                                  "E1": anchor[1][0], "N1": anchor[1][1]}
                                 if anchor else None),
            "annotation_ids": sorted(m["annotation_id"] for m in members),
            "capture_ids": sorted({m["capture_id"] for m in members}),
            "confidence": round(sum(m["confidence"] for m in members) / len(members), 3),
            "position_uncertainty_m": round(
                max(m["position_uncertainty_m"] for m in members), 3),
            "position_uncertainty_basis":
                "max_member_review_box_horizontal_angular_half_extent",
            "anchored": anchored,
        })

    # MANUAL-WORLD-02: visible things that were never boxed can still enter
    # the map, but only through an explicit hand-authored object row.  Unlike
    # annotation registration, this path performs no ray cast, clustering,
    # nearest-feature selection, or class inference.
    for d in authored_objects:
        carrier = carrier_by_id[int(d["carrier_id"])]
        E, N = float(d["E"]), float(d["N"])
        a, b = min(carrier["segments"], key=lambda ab: _seg_dist(E, N, ab[0], ab[1]))
        instances.append({
            "instance_id": str(d["instance_id"]),
            "module_id": str(d["module_id"]),
            "custom_identity_subject": None,
            "variant": d.get("variant"),
            "population_role": str(d.get("population_role") or "generic_fixture"),
            "E": E, "N": N, "height_m": float(d["height_m"]),
            "yaw_deg": float(d["yaw_deg"]),
            "carrier_id": int(d["carrier_id"]),
            "anchor_segment": {"E0": a[0], "N0": a[1], "E1": b[0], "N1": b[1]},
            "annotation_ids": [],
            "capture_ids": sorted({str(x) for x in d["capture_ids"]}),
            "confidence": 1.0,
            "position_uncertainty_m": float(d.get("position_uncertainty_m", 0.25)),
            "position_uncertainty_basis": "manual_authored_object",
            "anchored": True,
            "authored_decision_id": str(d["decision_id"]),
            "authored_basis": str(d["basis"]),
        })
        authored_applied.add(str(d["decision_id"]))

    instances.sort(key=lambda i: i["instance_id"])
    assert len({i["instance_id"] for i in instances}) == len(instances), "duplicate instance id"

    # --- conservation + falsifier ------------------------------------------
    covered = [a for inst in instances for a in inst["annotation_ids"]]
    assert len(covered) == len(live), f"conservation: {len(covered)} != {len(live)}"
    assert len(set(covered)) == len(live), "duplicate annotation assignment"
    n_unanch = sum(1 for inst in instances for _ in [0]
                   for m in [inst] if not m["anchored"])
    # unanchored share is measured over annotations (ray/carrier model quality)
    unanch_ann = sum(1 for o in obs if not o["anchored"])
    unanch_frac = unanch_ann / max(1, len(obs))
    if unanch_frac > 0.25:
        print(f"FALSIFIED: unanchored fraction {unanch_frac:.3f} > 0.25", file=sys.stderr)
        return 3

    # --- placement zones ----------------------------------------------------
    with open(os.path.join(
            repo, "artifacts/experiments/fl4813_capture_review_20260915/preserved"
                  "/numabe_full_727_mapping_20260915/generic_module_catalog.json")) as f:
        catalog = json.load(f)
    tokens = set()
    for m in catalog["modules"]:
        tokens.update(m.get("placement_requirements", []))

    def fam(cls: str) -> list[str]:
        tl = [t.lower() for t in tokens]
        if cls == "building":
            keep = [t for t in tokens if any(k in t.lower() for k in
                    ("frontage", "facade", "building", "boundary", "wall", "property",
                     "shop", "entrance", "door", "window", "awning", "commercial",
                     "residential", "cultural", "forecourt", "alley", "roof"))]
        elif cls == "road":
            keep = [t for t in tokens if any(k in t.lower() for k in
                    ("road", "street", "sidewalk", "crosswalk", "carriageway", "curb",
                     "pole", "walkway", "path", "corner", "crossing", "intersection",
                     "verge", "parking", "stairs", "ramp", "route", "junction",
                     "public_edge", "grade_change"))]
        elif cls == "railway":
            keep = [t for t in tokens if any(k in t.lower() for k in
                    ("rail", "station", "platform", "track", "crossing", "corridor"))]
        elif cls == "waterway":
            keep = [t for t in tokens if "water" in t.lower() or "drain" in t.lower()]
        else:  # barrier
            keep = [t for t in tokens if any(k in t.lower() for k in
                    ("boundary", "fence", "wall", "planting", "vegetat", "hedge"))]
        return sorted(keep)

    zones = [{"carrier_id": c["id"], "carrier_class": c["class"],
              "accepted_module_families": fam(c["class"].split(":")[0]),
              "polyline_epsg32654": [[round(x, 3), round(y, 3)] for x, y in c["polyline"]],
              **({"source_feature_id": c["source_feature_id"],
                  "authored_basis": c["authored_basis"]}
                 if c.get("authored_basis") else {})}
             for c in carriers]

    # --- write artifacts -----------------------------------------------------
    with open(os.path.join(args.out_dir, "registered_instances.json"), "w") as f:
        json.dump(instances, f, indent=1)
    with open(os.path.join(args.out_dir, "manual_placement_debt.json"), "w") as f:
        json.dump({
            "schema": "y92.fl4876.manual_placement_debt.v1",
            "rule": ("Photo observations are evidence only. Geometry requires an explicit "
                     "row in authored_observation_positions.json."),
            "rows": manual_pending,
            "identity_rows_delegated": identity_delegated,
        }, f, indent=1)
    with open(os.path.join(args.out_dir, "placement_zones.json"), "w") as f:
        json.dump(zones, f)

    per_module = Counter(o["key"] for o in obs)
    per_role = Counter(o["population_role"] for o in obs)
    per_custom = Counter(o["custom_subject_id"] for o in obs if o["custom_subject_id"])
    per_carrier = Counter(
        (next((c["class"] for c in carriers if c["id"] == o["carrier_id"]), "?"))
        for o in obs if o["anchored"])
    n_anch_inst = sum(1 for i in instances if i["anchored"])
    log_lines = [
        "NUMABE-REG-01 registration log",
        f"annotations in: {len(rows)}",
        f"discards skipped: {discards}",
        f"annotations registered from explicit authored positions: {len(live)}",
        f"annotations withheld pending manual placement: {len(manual_pending)}",
        f"identity annotations delegated to mapped-place resolver: {len(identity_delegated)}",
        f"instances out: {len(instances)}",
        f"annotations anchored: {len(obs) - unanch_ann}",
        f"annotations unanchored (8 m fallback): {unanch_ann}",
        f"instances anchored: {n_anch_inst}",
        f"instances unanchored: {len(instances) - n_anch_inst}",
        f"unanchored annotation fraction: {unanch_frac:.4f} (limit 0.25)",
        f"carriers parsed: {len(carriers)} "
        f"(building {len(by_class['building'])}, road {len(by_class['road'])}, "
        f"railway {len(by_class['railway'])}, waterway {len(by_class['waterway'])}, "
        f"barrier {len(by_class['barrier'])})",
        "anchors by carrier class:",
    ]
    for k, v in per_carrier.most_common():
        log_lines.append(f"  {k}: {v}")
    log_lines.append("per population_role annotation counts:")
    for k, v in per_role.most_common():
        log_lines.append(f"  {k}: {v}")
    log_lines.append("per-module-key annotation counts:")
    for k, v in per_module.most_common():
        log_lines.append(f"  {k}: {v}")
    log_lines.append("per-custom-subject annotation counts:")
    for k, v in per_custom.most_common():
        log_lines.append(f"  {k}: {v}")
    if no_carrier_detail:
        log_lines.append("unanchored by placement_context:")
        for k, v in no_carrier_detail.most_common():
            log_lines.append(f"  {k}: {v}")
    log_lines.append(
        f"rail corridor edges: {rail_edges_path or 'none'} sha256 {rail_edges_sha or '-'}")
    if id_ledger:
        known = set(id_ledger["annotation_to_instance"].values())
        now = {i["instance_id"] for i in instances}
        log_lines.append(
            f"instance id ledger: {id_ledger_path} sha256 {id_ledger_sha}; "
            f"kept {len(now & known)}, new {sorted(now - known)}, retired {sorted(known - now)}")
    else:
        log_lines.append("instance id ledger: none (sequential ids in cluster order)")
    log_lines.append(
        f"authored positions: {authored_path or 'none'} sha256 {authored_sha or '-'}; "
        f"applied {sorted(authored_applied)}")
    log_lines.append(
        f"authored omissions (skipped like reviewed discards): {len(authored_omitted)} "
        f"{sorted(set(authored_omitted.values()))}")
    log_lines.append("ground-contact range window (far preferred-class hits):")
    for k, v in sorted(ground_rule_counts.items()):
        log_lines.append(f"  {k}: {v}")
    log_lines.append("edge-shaped rail observations by rule:")
    for k, v in sorted(edge_rule_counts.items()):
        log_lines.append(f"  {k}: {v}")
    log_lines.append("policy: photo annotations without explicit authored E/N + carrier "
                     "are evidence/debt only and emit no geometry.")
    log_lines.append("assumptions for explicitly authored annotation height/orientation: "
                     "perspective hfov default 60 deg; equirectangular 360/180 deg; "
                     "camera altitude default 10.7 m AMSL; cluster radius 2.0 m "
                     "same key+carrier.")
    with open(os.path.join(args.out_dir, "registration_log.txt"), "w") as f:
        f.write("\n".join(log_lines) + "\n")

    # --- plan view -----------------------------------------------------------
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPolygon

    aoi = json.load(open(os.path.join(
        repo, "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
              "/connected_scope.geojson")))
    ring = next(f["geometry"]["coordinates"][0] for f in aoi["features"]
                if f.get("properties", {}).get("role") == "aoi")
    ring_en = [lonlat_to_epsg32654(lat, lon) for lon, lat in ring]

    ROLE_COLORS = {"generic_fixture": "#1f77b4", "generic_morphology": "#2ca02c",
                   "custom_identity": "#d62728"}
    fig, ax = plt.subplots(figsize=(12, 12))
    for c in carriers:
        xs = [p[0] for p in c["polyline"]]
        ys = [p[1] for p in c["polyline"]]
        top = c["class"].split(":")[0]
        if top == "building":
            ax.add_patch(MplPolygon(list(zip(xs, ys)), closed=True, facecolor="#bbbbbb",
                                    edgecolor="#888888", linewidth=0.4))
        elif top == "road":
            ax.plot(xs, ys, color="#888888", linewidth=0.8)
        elif top == "railway":
            ax.plot(xs, ys, color="#222222", linewidth=1.4)
        elif top == "waterway":
            ax.plot(xs, ys, color="#3399ff", linewidth=1.2)
        else:
            ax.plot(xs, ys, color="#996633", linewidth=0.8)
    for role, color in ROLE_COLORS.items():
        pts = [(i["E"], i["N"]) for i in instances if i["population_role"] == role]
        if pts:
            ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=9, c=color, label=f"{role}")
    for i in instances:
        if i["population_role"] == "custom_identity" and i["custom_identity_subject"]:
            ax.text(i["E"], i["N"], str(i["custom_identity_subject"])[:28], fontsize=3.5)
    rx, ry = zip(*ring_en)
    ax.plot(list(rx) + [rx[0]], list(ry) + [ry[0]], color="red", linewidth=1.5)
    ax.set_aspect("equal")
    ax.legend(markerscale=3, fontsize=8)
    ax.set_xlabel("E (EPSG:32654)")
    ax.set_ylabel("N (EPSG:32654)")
    ax.set_title("Numabe connected AOI: registered instances by population role")
    fig.tight_layout()
    fig.savefig(os.path.join(args.out_dir, "registration_plan.png"), dpi=150)
    print(f"OK instances={len(instances)} unanch_frac={unanch_frac:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
