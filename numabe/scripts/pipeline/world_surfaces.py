"""Numabe AOI surface model: carriageway width and adjacency made explicit.

Width rule (evidence order): span fact first (a road carrier is
span-governed only when at least 80 percent of its polyline length lies
within 3.0 m of the Valhalla pedestrian spine, and its span is the one
whose span_m interval contains the carrier's along-spine midpoint; source
"span:<route_key>"); measured second for non-spine roads (parallel
footway or dual-carriageway evidence per side; building-ring evidence per
side (ring distance minus 0.3 m, floored at 1.25 m); observed
roadside-fixture evidence per side (point distance plus 0.2 m); the smallest
bounds each side; sources "measured:<footway|dual>:<id>",
"measured:building:<feature_id>", "measured:observation:<instance_id>");
 Tag evidence third (non-spine roads with OSM cross_section tags: width_m
used directly, else lanes times the rules-file lane width plus the
rules-file extra when the sidewalk tag is both/left/right; sources
"tag:width" and "tag:lanes");
DEFAULT by class last
(the rules-file carriageway table). Footway, steps and pedestrian carriers
are not carriageways: surface
kind "footway" with rules-file half-widths and no
sidewalk band; a footway used as measured sidewalk evidence additionally
classifies as "sidewalk". Sidewalk bands exist only where the governing
span states sidewalk_width_m, or by the rules-file sidewalk default on its
listed classes.
All DEFAULT cross-section facts (carriageway by class, footway halves,
sidewalk default, lane width, tag extra, explicit-sidewalk, rail and
waterway halves) are read from the rules file road_class_defaults, never
hard-coded; each carries its rules source. Junction
polygon: at every node shared by two or more road carriers (footway-only
nodes excluded), a disc of radius the largest incident carriageway
half-width. Centreline conflicts (non-junction carrier pairs whose centrelines
pass closer than the sum of their half-widths) are source-data conflicts, not
model defects. A building ring can never lie inside a carriageway: every road
carrier (span, tag, measured, DEFAULT) takes the ring bound on an affected
side, and carriers whose centreline passes through a ring are listed under
centreline_through_ring, not hidden. Pure python, no third-party dependencies.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts.pipeline import geo_frames as GF  # noqa: E402

SCHEMA = "y92.numabe_surfaces.v6"

SPINE_REL = ("assets/source_bundles/"
             "numabe_connected_neighborhood_scope_20260915/"
             "valhalla_pedestrian_route.json")
SPINE_WITHIN_M = 3.0
SPINE_MIN_FRAC = 0.8
GOV_SAMPLE_M = 1.0

DEFAULT_WIDTH_KEYS = (
    "carriageway_width_m_by_highway",
    "footway_half_width_m_by_kind",
    "sidewalk_default_width_m",
    "sidewalk_default_classes",
    "lane_width_m",
    "tag_sidewalk_extra_m",
    "explicit_sidewalk_centreline_half_m",
    "rail_half_width_m",
    "waterway_half_width_m",
    "junction_rule",
)
NODE_CLUSTER_M = 1.0
PARALLEL_WITHIN_M = 8.0
PARALLEL_MIN_FRAC = 0.5
PARALLEL_DIR_DEG = 20.0
PARALLEL_AGREE_FRAC = 0.5
CONT_END_M = 0.5
MEAS_MARGIN_M = 0.2
MEAS_HALF_MIN_M = 1.25
BUILD_SUB_M = 0.3
OBS_ADD_M = 0.2
RING_SAMPLE_M = 1.0
_COS20 = math.cos(math.radians(PARALLEL_DIR_DEG))
CANDIDATE_MERGE_M = 2.0
RAIL_BLOCK_MARGIN_M = 0.5
SAMPLE_STEP_M = 0.5
OFFSET_SAMPLE_M = 0.25
MIN_PIECE_M = 1.0

KINDS = ("carriageway", "junction", "sidewalk", "building", "rail", "water",
         "footway")


def _dseg(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / l2
    tc = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + tc * dx), py - (ay + tc * dy)), tc


def _dpoly(px, py, pl):
    best = (float("inf"), 0, 0.0)
    for i in range(len(pl) - 1):
        d, t = _dseg(px, py, pl[i][0], pl[i][1], pl[i + 1][0], pl[i + 1][1])
        if d < best[0]:
            best = (d, i, t)
    return best


# FL-4876 ROAD-XSECTION-01. A carriageway may be asymmetric about its OSM
# centreline (measured building gaps, the painted LiDAR corridor). Per-side
# halves live in r["evidence"][side]["half_m"]; r["half"] stays the mean for
# callers that need one symmetric bound.
def _road_side_half(r, side):
    e = (r.get("evidence") or {}).get(side)
    return float(e["half_m"]) if e and e.get("half_m") is not None else r["half"]


def _road_max_half(r):
    return max(_road_side_half(r, "left"), _road_side_half(r, "right"))


def _road_half_at(r, px, py, seg):
    """Half width on the side of segment seg that (px, py) lies on."""
    pl = r["polyline"]
    if seg + 1 >= len(pl):
        return r["half"]
    (ax, ay), (bx, by) = pl[seg], pl[seg + 1]
    cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
    return _road_side_half(r, "left" if cross > 0.0 else "right")


def _painted_road_halves(stamp_plan_path, roads):
    """{carrier_id: (left_half, right_half, width_source)} from the surface
    stamp plan, measured against each road carrier's own polyline.

    The stamp plan is the only owner of what the player sees as asphalt
    (OSM width tag, FL-4934 LiDAR corridor polygon, class default). A
    polyline layer is symmetric (width / 2 each side). A polygon layer is
    measured with rays along both normals every PAINT_STATION_M; each side
    takes the median exit distance."""
    from shapely.geometry import LineString, Polygon

    plan = json.loads(Path(stamp_plan_path).read_text())
    by_fid = {}
    for row in plan.get("rows", []):
        if row.get("semantic_class") != "road":
            continue
        for layer in row.get("stamp_layers") or []:
            if str(layer.get("role", "")).startswith("road"):
                by_fid[str(row["feature_id"])] = layer
                break
    out = {}
    for cid in sorted(roads, key=str):
        layer = by_fid.get(f"feature:road:way/{cid}")
        if layer is None:
            continue
        src = str(layer.get("width_source") or "")
        if layer.get("geometry_kind") == "polyline":
            h = float(layer["width_m"]) / 2.0
            out[cid] = (h, h, src)
            continue
        if layer.get("geometry_kind") != "polygon":
            continue
        poly = Polygon([p[:2] for p in layer["coordinates_epsg32654"]]).buffer(0)
        edge = poly.exterior if poly.geom_type == "Polygon" else poly.boundary
        pl = roads[cid]["polyline"]
        line = LineString(pl)
        sides = {"left": [], "right": []}
        s = PAINT_END_SKIP_M
        while s <= line.length - PAINT_END_SKIP_M:
            p = line.interpolate(s)
            a = line.interpolate(max(0.0, s - 0.25))
            b = line.interpolate(min(line.length, s + 0.25))
            tx, ty = b.x - a.x, b.y - a.y
            tn = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / tn, tx / tn
            for side, sg in (("left", 1.0), ("right", -1.0)):
                ray = LineString([(p.x, p.y), (p.x + sg * nx * PAINT_RAY_M,
                                               p.y + sg * ny * PAINT_RAY_M)])
                hit = ray.intersection(edge)
                if hit.is_empty:
                    continue
                pts = list(getattr(hit, "geoms", [hit]))
                d = min(math.hypot(q.x - p.x, q.y - p.y) for q in pts
                        if q.geom_type == "Point") if any(
                    q.geom_type == "Point" for q in pts) else None
                if d is not None:
                    sides[side].append(d)
            s += PAINT_STATION_M
        if not sides["left"] or not sides["right"]:
            h = float(layer["width_m"]) / 2.0
            out[cid] = (h, h, src)
            continue
        med = {k: sorted(v)[len(v) // 2] for k, v in sides.items()}
        out[cid] = (round(med["left"], 3), round(med["right"], 3), src)
    return out


PAINT_STATION_M = 2.0
PAINT_END_SKIP_M = 3.0
PAINT_RAY_M = 30.0


def _arc_join(V, d, n_a, n_b):
    a1 = math.atan2(n_a[1], n_a[0])
    a2 = math.atan2(n_b[1], n_b[0])
    delta = (a2 - a1 + math.pi) % (2.0 * math.pi) - math.pi
    mid_target = math.atan2(n_a[1] + n_b[1], n_a[0] + n_b[0])
    mid = a1 + delta / 2.0
    dd = abs((mid - mid_target + math.pi) % (2.0 * math.pi) - math.pi)
    if dd > math.pi / 2.0:
        delta = delta - 2.0 * math.pi if delta > 0 else delta + 2.0 * math.pi
    n = max(1, int(math.ceil(abs(delta) / 0.25)))
    return [(V[0] + d * math.cos(a1 + delta * k / n),
             V[1] + d * math.sin(a1 + delta * k / n))
            for k in range(1, n)]


def _seg_seg_closest(ax, ay, bx, by, cx, cy, dx, dy):
    hit = _seg_intersect(ax, ay, bx, by, cx, cy, dx, dy)
    if hit is not None:
        return 0.0, hit
    best_d = float("inf")
    best_p = ((ax + bx) / 2.0, (ay + by) / 2.0)
    for (px, py, qx, qy, rx, ry) in ((ax, ay, cx, cy, dx, dy),
                                     (bx, by, cx, cy, dx, dy),
                                     (cx, cy, ax, ay, bx, by),
                                     (dx, dy, ax, ay, bx, by)):
        d, t = _dseg(px, py, qx, qy, rx, ry)
        if d < best_d:
            qx2 = qx + t * (rx - qx)
            qy2 = qy + t * (ry - qy)
            best_d = d
            best_p = ((px + qx2) / 2.0, (py + qy2) / 2.0)
    return best_d, best_p


def _seg_seg_dist(ax, ay, bx, by, cx, cy, dx, dy):
    if _seg_intersect(ax, ay, bx, by, cx, cy, dx, dy) is not None:
        return 0.0
    return min(_dseg(ax, ay, cx, cy, dx, dy)[0],
               _dseg(bx, by, cx, cy, dx, dy)[0],
               _dseg(cx, cy, ax, ay, bx, by)[0],
               _dseg(dx, dy, ax, ay, bx, by)[0])


def _line_intersect(ax, ay, bx, by, cx, cy, dx, dy):
    """Infinite-line intersection as (x, y, t, u) or None if parallel."""
    denom = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
    if abs(denom) < 1e-12:
        return None
    t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / denom
    u = ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / denom
    return (ax + t * (bx - ax), ay + t * (by - ay), t, u)
    denom = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
    if abs(denom) < 1e-12:
        return None
    t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / denom
    u = ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / denom
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return (ax + t * (bx - ax), ay + t * (by - ay))
    return None


def _seg_intersect(ax, ay, bx, by, cx, cy, dx, dy):
    denom = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
    if abs(denom) < 1e-12:
        return None
    t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / denom
    u = ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / denom
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return (ax + t * (bx - ax), ay + t * (by - ay))
    return None


def _point_in_ring(px, py, ring):
    inside = False
    n = len(ring)
    for i in range(n - 1):
        x0, y0 = ring[i]
        x1, y1 = ring[i + 1]
        if (y0 > py) != (y1 > py):
            xin = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
            if px < xin:
                inside = not inside
    return inside


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _bbox_dist(px, py, bb):
    dx = 0.0 if bb[0] <= px <= bb[2] else min(abs(px - bb[0]), abs(px - bb[2]))
    dy = 0.0 if bb[1] <= py <= bb[3] else min(abs(py - bb[1]), abs(py - bb[3]))
    return math.hypot(dx, dy)


def _decode_polyline6(encoded):
    coords = []
    index = lat = lon = 0
    while index < len(encoded):
        values = []
        for _axis in range(2):
            result = shift = 0
            while True:
                value = ord(encoded[index]) - 63
                index += 1
                result |= (value & 0x1F) << shift
                shift += 5
                if value < 0x20:
                    break
            values.append(~(result >> 1) if result & 1 else result >> 1)
        lat += values[0]
        lon += values[1]
        coords.append((lat / 1_000_000.0, lon / 1_000_000.0))
    return coords


def _load_spine():
    doc = json.loads((REPO_ROOT / SPINE_REL).read_text(encoding="utf-8"))
    legs = (doc.get("trip") or {}).get("legs") or []
    shape = legs[0]["shape"]
    spine = [GF.utm_forward(lon, lat)
             for lat, lon in _decode_polyline6(shape)]
    return [(float(e), float(n)) for e, n in spine]


class _SpineIndex:
    def __init__(self, spine):
        self.pts = spine
        self.cum = [0.0]
        for i in range(len(spine) - 1):
            self.cum.append(self.cum[-1] + math.hypot(
                spine[i + 1][0] - spine[i][0],
                spine[i + 1][1] - spine[i][1]))
        self.segs = []
        for i in range(len(spine) - 1):
            a, b = spine[i], spine[i + 1]
            self.segs.append(
                (a[0], a[1], b[0], b[1],
                 (min(a[0], b[0]), min(a[1], b[1]),
                  max(a[0], b[0]), max(a[1], b[1]))))

    def project(self, px, py):
        """Closest (arclength, distance) on the spine."""
        best_s, best_d = 0.0, float("inf")
        for i, (ax, ay, bx, by, bb) in enumerate(self.segs):
            if _bbox_dist(px, py, bb) >= best_d:
                continue
            d, t = _dseg(px, py, ax, ay, bx, by)
            if d < best_d:
                seg_len = self.cum[i + 1] - self.cum[i]
                best_d = d
                best_s = self.cum[i] + t * seg_len
        return best_s, best_d

    def dist(self, px, py):
        return self.project(px, py)[1]


def _sample_polyline(pl, step):
    total = sum(math.hypot(pl[i + 1][0] - pl[i][0],
                           pl[i + 1][1] - pl[i][1])
                for i in range(len(pl) - 1))
    if total == 0.0:
        return [(0.0, pl[0])], 0.0
    n = max(1, int(math.ceil(total / step)))
    out = []
    for k in range(n + 1):
        s = min(total, k * step)
        acc = 0.0
        for i in range(len(pl) - 1):
            sl = math.hypot(pl[i + 1][0] - pl[i][0],
                            pl[i + 1][1] - pl[i][1])
            if acc + sl >= s - 1e-9:
                u = 0.0 if sl == 0.0 else (s - acc) / sl
                u = max(0.0, min(1.0, u))
                out.append((s, (pl[i][0] + u * (pl[i + 1][0] - pl[i][0]),
                                pl[i][1] + u * (pl[i + 1][1] - pl[i][1]))))
                break
            acc += sl
    return out, total


def _req(rcd, key, sub=None):
    if key not in rcd:
        raise ValueError(
            f"rules file lacks road_class_defaults.{key}")
    node = rcd[key]
    if sub is None:
        return node
    if not isinstance(node, dict) or sub not in node:
        raise ValueError(
            f"rules file lacks road_class_defaults.{key}.{sub}")
    return node[sub]


def _read_width_defaults(rules):
    """DEFAULT cross-section facts from the rules file (never hard-coded)."""
    rcd = rules.get("road_class_defaults")
    if not isinstance(rcd, dict):
        raise ValueError("rules file lacks road_class_defaults")
    for key in DEFAULT_WIDTH_KEYS:
        if key not in rcd:
            raise ValueError(
                f"rules file lacks road_class_defaults.{key}")
    cw = rcd["carriageway_width_m_by_highway"]
    fw = rcd["footway_half_width_m_by_kind"]
    return {
        "carriageway": ({k: float(_req(rcd, "carriageway_width_m_by_highway",
                                         k)["value"]) for k in cw},
                          {k: _req(rcd, "carriageway_width_m_by_highway",
                                   k)["source"] for k in cw}),
        "footway": ({k: float(_req(rcd, "footway_half_width_m_by_kind",
                                     k)["value"]) for k in fw},
                      {k: _req(rcd, "footway_half_width_m_by_kind",
                               k)["source"] for k in fw}),
        "sidewalk_width": (float(_req(rcd, "sidewalk_default_width_m")
                                   ["value"]),
                             _req(rcd, "sidewalk_default_width_m")["source"]),
        "sidewalk_classes": (tuple(_req(rcd, "sidewalk_default_classes")
                                      ["value"]),
                               _req(rcd, "sidewalk_default_classes")["source"]),
        "lane": (float(_req(rcd, "lane_width_m")["value"]),
                   _req(rcd, "lane_width_m")["source"]),
        "tag_extra": (float(_req(rcd, "tag_sidewalk_extra_m")["value"]),
                        _req(rcd, "tag_sidewalk_extra_m")["source"]),
        "esw_half": (float(_req(rcd, "explicit_sidewalk_centreline_half_m")
                              ["value"]),
                       _req(rcd, "explicit_sidewalk_centreline_half_m")
                       ["source"]),
        "rail_half": (float(_req(rcd, "rail_half_width_m")["value"]),
                        _req(rcd, "rail_half_width_m")["source"]),
        "water_half": (float(_req(rcd, "waterway_half_width_m")["value"]),
                         _req(rcd, "waterway_half_width_m")["source"]),
    }


def _segdir(pl, i):
    a, b = pl[i], pl[i + 1]
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L == 0.0:
        return (0.0, 0.0)
    return ((b[0] - a[0]) / L, (b[1] - a[1]) / L)


def _endpoints_within(pa, pb, tol):
    for a in (pa[0], pa[-1]):
        for b in (pb[0], pb[-1]):
            if math.hypot(a[0] - b[0], a[1] - b[1]) <= tol:
                return True
    return False


def _parallel_evidence(pid, P_pl, P_suffix, default_half, roads, footways,
                         fwhalf):
    """Per-side measured evidence for one carriageway.

    Returns {side: (kind, ref_id, half_m)} for side in {"left", "right"}.
    kind is "footway" (footway/steps/pedestrian reference) or "dual"
    (same-class carriageway reference).
    """
    pool = []
    for oid in sorted(footways, key=str):
        r = footways[oid]
        pool.append((oid, "footway", fwhalf[r["suffix"]],
                     r["polyline"]))
    for oid in sorted(roads, key=str):
        if oid == pid:
            continue
        r = roads[oid]
        if r["suffix"] != P_suffix:
            continue
        pool.append((oid, "dual", None, r["polyline"]))
    sided = {}
    for oid, kind, ref_half, Q_pl in pool:
        if _endpoints_within(P_pl, Q_pl, CONT_END_M):
            continue
        inrange = 0
        agree = []
        for vi in range(len(Q_pl)):
            v = Q_pl[vi]
            d, si, t = _dpoly(v[0], v[1], P_pl)
            if d > PARALLEL_WITHIN_M:
                continue
            inrange += 1
            qd = _segdir(Q_pl, min(vi, len(Q_pl) - 2))
            pd = _segdir(P_pl, si)
            if abs(qd[0] * pd[0] + qd[1] * pd[1]) >= _COS20:
                agree.append((vi, d, si, t))
        if inrange < PARALLEL_MIN_FRAC * len(Q_pl) or not agree:
            continue
        if len(agree) < PARALLEL_AGREE_FRAC * inrange:
            continue
        tot = 0.0
        for vi, d, si, t in agree:
            a = P_pl[si]
            b = P_pl[si + 1]
            L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
            dx, dy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            fx, fy = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            v = Q_pl[vi]
            tot += dx * (v[1] - fy) - dy * (v[0] - fx)
        side = "left" if tot > 0.0 else "right"
        dist = min(d for _, d, _, _ in agree)
        if kind == "footway":
            half = dist - ref_half - MEAS_MARGIN_M
        else:
            half = dist / 2.0 - MEAS_MARGIN_M
        half = max(MEAS_HALF_MIN_M, min(default_half, half))
        key = (dist, 0 if kind == "footway" else 1, str(oid))
        if side not in sided or key < sided[side][0]:
            sided[side] = (key, kind, oid, half)
    return {s: (k, o, h) for s, ((_, _, _), k, o, h) in sided.items()}


_MARKING_MODULES = None


def _marking_modules():
    """Module ids that legitimately sit on a carriageway (never bound
    width), imported from the compile owner, not copied."""
    global _MARKING_MODULES
    if _MARKING_MODULES is None:
        import importlib.util
        path = (REPO_ROOT / "scripts" / "pipeline"
                / "compile_population_plan.py")
        spec = importlib.util.spec_from_file_location(
            "cpp_owner_for_surfaces", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        marks = {mid for mid, (_lat, allow, _forbid)
                 in mod.MODULE_SURFACE_OVERRIDES.items()
                 if allow and "carriageway" in allow}
        marks.add("level_crossing_equipment")
        _MARKING_MODULES = marks
    return _MARKING_MODULES


def _ring_gap_sides(P_pl, default_half, buildings, skip_fids):
    """Exact per-side (min gap, feature_id) to building rings.
    Segment-exact (unlike the sampled _building_evidence): the minimum
    over all road-segment by ring-segment endpoint projections, with the
    side read at the closest road point. Rings the centreline crosses
    (skip_fids) are not a width question and are excluded."""
    per_side = {}
    rb = _bbox(P_pl)
    x0 = (rb[0] - default_half, rb[1] - default_half,
          rb[2] + default_half, rb[3] + default_half)
    for fid, ring, bb in buildings:
        if fid in skip_fids:
            continue
        if bb[2] < x0[0] or bb[0] > x0[2] or bb[3] < x0[1] \
                or bb[1] > x0[3]:
            continue
        for i in range(len(P_pl) - 1):
            ax, ay = P_pl[i]
            bx, by = P_pl[i + 1]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            for k in range(len(ring) - 1):
                cx, cy = ring[k]
                ex, ey = ring[k + 1]
                if min(ax, bx) > max(cx, ex) + default_half \
                        or min(cx, ex) > max(ax, bx) + default_half \
                        or min(ay, by) > max(cy, ey) + default_half \
                        or min(cy, ey) > max(ay, by) + default_half:
                    continue
                for px, py in ((cx, cy), (ex, ey)):
                    d, t = _dseg(px, py, ax, ay, bx, by)
                    s = _signed_side(P_pl, i, t, px, py)
                    key = (d, str(fid))
                    if s not in per_side or key < per_side[s][0]:
                        per_side[s] = (key, fid)
                for px, py in ((ax, ay), (bx, by)):
                    d, _t = _dseg(px, py, cx, cy, ex, ey)
                    if L2 == 0.0:
                        tr = 0.0
                    else:
                        tr = max(0.0, min(1.0, ((px - ax) * dx
                                               + (py - ay) * dy) / L2))
                    qx = cx + _t * (ex - cx)
                    qy = cy + _t * (ey - cy)
                    s = _signed_side(P_pl, i, tr, qx, qy)
                    key = (d, str(fid))
                    if s not in per_side or key < per_side[s][0]:
                        per_side[s] = (key, fid)
    return {s: (k[0], f) for s, (k, f) in per_side.items()}


def _signed_side(P_pl, si, t, E, N):
    a = P_pl[si]
    b = P_pl[si + 1]
    L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
    dx, dy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
    fx, fy = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
    return "left" if dx * (N - fy) - dy * (E - fx) > 0.0 else "right"


def _building_evidence(P_pl, default_half, buildings):
    """Per-side (min ring distance, feature_id) for rings in the buffer."""
    per_side: dict[str, tuple] = {}
    rb = _bbox(P_pl)
    ex = (rb[0] - default_half, rb[1] - default_half,
          rb[2] + default_half, rb[3] + default_half)
    for fid, ring, bb in buildings:
        if bb[2] < ex[0] or bb[0] > ex[2] or bb[3] < ex[1] or bb[1] > ex[3]:
            continue
        samples, _ = _sample_polyline(ring, RING_SAMPLE_M)
        for _, (E, N) in samples:
            d, si, t = _dpoly(E, N, P_pl)
            if d >= default_half:
                continue
            side = _signed_side(P_pl, si, t, E, N)
            key = (d, str(fid))
            if side not in per_side or key < per_side[side][0]:
                per_side[side] = (key, fid)
    return {s: (k[0], f) for s, (k, f) in per_side.items()}


def _observation_evidence(P_pl, default_half, observations):
    """Per-side (min point distance, instance_id) for points in buffer."""
    per_side: dict[str, tuple] = {}
    for ob in observations:
        E, N = ob["E"], ob["N"]
        d, si, t = _dpoly(E, N, P_pl)
        if d >= default_half:
            continue
        side = _signed_side(P_pl, si, t, E, N)
        key = (d, str(ob["instance_id"]))
        if side not in per_side or key < per_side[side][0]:
            per_side[side] = (key, ob["instance_id"])
    return {s: (k[0], i) for s, (k, i) in per_side.items()}


def _tag_widths(world_ir, lane_m, sw_extra):
    """Map OSM way id -> (width_m, source, ref) from cross_section."""
    out = {}
    for f in world_ir.get("canonical_features", []) or []:
        if f.get("semantic_class") != "road":
            continue
        fid = f.get("feature_id", "")
        if "way/" not in fid:
            continue
        key = fid.split("way/")[-1]
        if not key or key in out:
            continue
        v = ((f.get("properties") or {}).get("cross_section") or {}).get(
            "value") or {}
        try:
            if v.get("width_m") is not None:
                out[key] = (float(v["width_m"]), "tag:width",
                            "width=%s" % v["width_m"])
            elif v.get("lanes") is not None:
                lanes = int(str(v["lanes"]).split(";")[0].strip())
                w = lanes * lane_m + (
                    sw_extra
                    if v.get("sidewalk") in ("both", "left", "right")
                    else 0.0)
                out[key] = (w, "tag:lanes", "lanes=%s" % v["lanes"])
        except (ValueError, TypeError):
            continue
    return out


def _through_ring(pl, buildings):
    """Feature ids of rings the centreline passes through."""
    out = set()
    rb = _bbox(pl)
    for fid, ring, bb in buildings:
        if bb[2] < rb[0] or bb[0] > rb[2] or bb[3] < rb[1] or bb[1] > rb[3]:
            continue
        hit = False
        for v in pl:
            if _point_in_ring(v[0], v[1], ring):
                hit = True
                break
        if not hit:
            for i in range(len(pl) - 1):
                for k in range(len(ring) - 1):
                    if _seg_intersect(
                            pl[i][0], pl[i][1], pl[i + 1][0], pl[i + 1][1],
                            ring[k][0], ring[k][1],
                            ring[k + 1][0], ring[k + 1][1]) is not None:
                        hit = True
                        break
                if hit:
                    break
        if hit:
            out.add(fid)
    return sorted(out, key=str)


def _building_residue(roads, buildings):
    """Building rings intersecting any carriageway polygon: returns the
    sorted feature ids (RING-01: callers compare against the
    centreline-crossing rings)."""
    out = set()
    for _fid, ring, bb in buildings:
        hit = False
        for cid in sorted(roads, key=str):
            r = roads[cid]
            rb = r["bbox"]
            if (bb[2] < rb[0] - r["half"] or bb[0] > rb[2] + r["half"]
                    or bb[3] < rb[1] - r["half"]
                    or bb[1] > rb[3] + r["half"]):
                continue
            pl = r["polyline"]
            for i in range(len(pl) - 1):
                ax, ay = pl[i]
                bx, by = pl[i + 1]
                for k in range(len(ring) - 1):
                    d, _pt = _seg_seg_closest(
                        ax, ay, bx, by,
                        ring[k][0], ring[k][1],
                        ring[k + 1][0], ring[k + 1][1])
                    if d < r["half"]:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            out.add(_fid)
    return sorted(out, key=str)


def _rail_poly_dist(pl_a, pl_b, lim):
    """Minimum segment distance between two polylines, early-out at lim."""
    best = float("inf")
    for i in range(len(pl_a) - 1):
        ax, ay = pl_a[i]
        bx, by = pl_a[i + 1]
        for j in range(len(pl_b) - 1):
            cx, cy = pl_b[j]
            dx, dy = pl_b[j + 1]
            if (min(ax, bx) > max(cx, dx) + lim
                    or min(cx, dx) > max(ax, bx) + lim
                    or min(ay, by) > max(cy, dy) + lim
                    or min(cy, dy) > max(ay, by) + lim):
                continue
            d, _ = _seg_seg_closest(ax, ay, bx, by, cx, cy, dx, dy)
            if d < best:
                best = d
    return best


def _rail_corridors(rail, rail_half):
    """Group railway carriers whose rail bands overlap into corridors.
    Two tracks share a corridor when their centreline distance is below
    twice the rail half-width; only corridor OUTER edges take fences."""
    parent = list(range(len(rail)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx
    for i in range(len(rail)):
        for j in range(i + 1, len(rail)):
            if _rail_poly_dist(rail[i][1], rail[j][1],
                               2.0 * rail_half) < 2.0 * rail_half:
                union(i, j)
    groups = {}
    for i in range(len(rail)):
        groups.setdefault(find(i), []).append(i)
    corridors = []
    for g in sorted(groups.values(), key=lambda g: str(rail[g[0]][0])):
        corridors.append({
            "id": f"rail_corridor:{len(corridors):04d}",
            "carriers": sorted([rail[i][0] for i in g], key=str)})
    return corridors


class Surfaces:
    def __init__(self, roads, footways, junctions, buildings, rail, water,
                 explicit_sidewalks, centreline_conflicts, defaults_used,
                 spine_info, measured_sidewalks=(),
                 centreline_through_ring=(), query_halves=None):
        self._roads = roads
        self._footways = footways
        self._junctions = junctions
        self._buildings = buildings
        self._rail = rail
        self._water = water
        self._esw = explicit_sidewalks
        self._conflicts = centreline_conflicts
        self._through_ring = list(centreline_through_ring)
        self._qh = dict(query_halves or {})
        self._msw = set(measured_sidewalks)
        self._defaults_used = sorted(set(defaults_used))
        self._spine_info = spine_info
        self._rail_cids = {cid for cid, _, _ in self._rail}
        self._corridors = _rail_corridors(self._rail,
                                          self._qh["rail_half"])

    # -- width -----------------------------------------------------------
    def road_width_m(self, carrier_id):
        if carrier_id in self._roads:
            r = self._roads[carrier_id]
            return (r["width"], r["source"])
        r = self._footways[carrier_id]
        return (r["width"], r["source"])

    def rail_half_m(self):
        return self._qh["rail_half"]

    def road_side_half_m(self, carrier_id, side):
        """FL-4876 ROAD-XSECTION-01: carriageway half width on one side
        ("left" = +normal of the polyline direction, as offset_polyline)."""
        if carrier_id in self._roads:
            return _road_side_half(self._roads[carrier_id], side)
        return self._footways[carrier_id]["half"]

    def road_side_face_m(self, carrier_id, side):
        """Centreline distance of the building face that bounds this side
        (the universal building bound set half = face - BUILD_SUB_M), or
        None when no building bounds it. ROAD-XSECTION-01: roadside
        hardware must stop short of that face."""
        r = self._roads.get(carrier_id)
        e = ((r or {}).get("evidence") or {}).get(side)
        if e and e.get("kind") == "building" and e.get("half_m") is not None:
            return float(e["half_m"]) + BUILD_SUB_M
        return None

    # -- classification --------------------------------------------------
    def _detail(self, E, N, expand=0.0):
        """Map kind -> carrier_id of the nearest surface covering (E,N)."""
        out = {}
        best_road = None
        for cid in sorted(self._roads, key=str):
            r = self._roads[cid]
            if _bbox_dist(E, N, r["bbox"]) > _road_max_half(r) + r["sidewalk"] + expand:
                continue
            d, seg, _ = _dpoly(E, N, r["polyline"])
            half = _road_half_at(r, E, N, seg)
            if d <= half + expand:
                if best_road is None or d < best_road[0]:
                    best_road = (d, cid)
            elif r["sidewalk"] > 0.0 and d <= half + r["sidewalk"] + expand:
                if "sidewalk" not in out:
                    out["sidewalk"] = cid
        if best_road is not None:
            out["carriageway"] = best_road[1]
        best_fw = None
        for cid in sorted(self._footways, key=str):
            r = self._footways[cid]
            if _bbox_dist(E, N, r["bbox"]) > r["half"] + expand:
                continue
            d, _, _ = _dpoly(E, N, r["polyline"])
            if d <= r["half"] + expand:
                if best_fw is None or d < best_fw[0]:
                    best_fw = (d, cid)
        if best_fw is not None:
            out["footway"] = best_fw[1]
            if best_fw[1] in self._msw:
                out["sidewalk"] = best_fw[1]
        best_j = None
        for j in self._junctions:
            d = math.hypot(E - j["E"], N - j["N"])
            if d <= j["radius"] + expand:
                if best_j is None or d < best_j[0]:
                    best_j = (d, j["id"])
        if best_j is not None:
            out["junction"] = best_j[1]
        for fid, pl, bb in self._esw:
            if _bbox_dist(E, N, bb) > self._qh["esw_half"] + expand:
                continue
            d, _, _ = _dpoly(E, N, pl)
            if d <= self._qh["esw_half"] + expand:
                if "sidewalk" not in out:
                    out["sidewalk"] = fid
                break
        for fid, ring, bb in self._buildings:
            if _bbox_dist(E, N, bb) > expand and not (
                    bb[0] <= E <= bb[2] and bb[1] <= N <= bb[3]):
                continue
            if _point_in_ring(E, N, ring):
                out["building"] = fid
                break
            if expand > 0.0:
                d, _, _ = _dpoly(E, N, ring)
                if d <= expand:
                    out["building"] = fid
                    break
        for cid, pl, bb in self._rail:
            if _bbox_dist(E, N, bb) > self._qh["rail_half"] + expand:
                continue
            d, _, _ = _dpoly(E, N, pl)
            if d <= self._qh["rail_half"] + expand:
                out["rail"] = cid
                break
        for cid, pl, bb in self._water:
            if _bbox_dist(E, N, bb) > self._qh["water_half"] + expand:
                continue
            d, _, _ = _dpoly(E, N, pl)
            if d <= self._qh["water_half"] + expand:
                out["water"] = cid
                break
        return out

    def point_surfaces(self, E, N):
        return set(self._detail(E, N))

    # -- query helpers ---------------------------------------------------
    @staticmethod
    def _samples(a, b):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length == 0.0:
            return [(0.0, a)], 0.0
        n = max(1, int(math.ceil(length / SAMPLE_STEP_M)))
        pts = []
        for i in range(n + 1):
            t = i / n
            pts.append((t, (a[0] + t * (b[0] - a[0]),
                            a[1] + t * (b[1] - a[1]))))
        return pts, length

    def segment_conflicts(self, a, b, half_width_m, kinds):
        kinds = set(kinds)
        pts, length = self._samples(tuple(a), tuple(b))
        tagged = []
        for t, (E, N) in pts:
            det = self._detail(E, N, expand=half_width_m)
            pick = None
            for k in sorted(det):
                if k in kinds:
                    pick = (k, det[k])
                    break
            tagged.append((t, pick))
        out = []
        i = 0
        while i < len(tagged):
            key = tagged[i][1]
            if key is None:
                i += 1
                continue
            j = i
            while j + 1 < len(tagged) and tagged[j + 1][1] == key:
                j += 1
            t0, t1 = tagged[i][0], tagged[j][0]
            out.append({"kind": key[0], "carrier_id": key[1],
                        "overlap_m": (t1 - t0) * length,
                        "t0": t0, "t1": t1})
            i = j + 1
        return out

    def clip_segment(self, a, b, kinds):
        kinds = set(kinds)
        a = tuple(a)
        b = tuple(b)
        pts, _ = self._samples(a, b)
        flags = []
        for t, (E, N) in pts:
            inside = bool(set(self._detail(E, N)) & kinds)
            flags.append((t, (E, N), inside))
        out = []
        i = 0
        while i < len(flags):
            if flags[i][2]:
                i += 1
                continue
            j = i
            while j + 1 < len(flags) and not flags[j + 1][2]:
                j += 1
            out.append((flags[i][1], flags[j][1]))
            i = j + 1
        return out

    def _offset_blocked(self, E, N, own):
        for j in self._junctions:
            if math.hypot(E - j["E"], N - j["N"]) <= j["radius"]:
                return True
        for cid in sorted(self._roads, key=str):
            if cid == own:
                continue
            r = self._roads[cid]
            if _bbox_dist(E, N, r["bbox"]) > _road_max_half(r):
                continue
            d, seg, _ = _dpoly(E, N, r["polyline"])
            if d <= _road_half_at(r, E, N, seg):
                return True
        if own in self._rail_cids:
            rh = self._qh["rail_half"]
            for cid, pl, bb in self._rail:
                if cid == own:
                    continue
                # Margin over the band edge: offset samples are 0.25 m
                # apart, so a piece midpoint could otherwise dip back
                # inside a neighbouring band near crossings.
                if _bbox_dist(E, N, bb) > rh + RAIL_BLOCK_MARGIN_M:
                    continue
                d, _, _ = _dpoly(E, N, pl)
                if d <= rh + RAIL_BLOCK_MARGIN_M:
                    return True
        return False

    def offset_polyline(self, carrier_id, side, distance_m):
        if side not in ("left", "right"):
            raise ValueError("side must be 'left' or 'right'")
        if carrier_id in self._roads:
            pl = self._roads[carrier_id]["polyline"]
        elif carrier_id in self._footways:
            pl = self._footways[carrier_id]["polyline"]
        else:
            pl = None
            for _cid, _pl, _bb in self._rail:
                if _cid == carrier_id:
                    pl = _pl
                    break
            if pl is None:
                raise KeyError(carrier_id)
        sgn = 1.0 if side == "left" else -1.0
        offs = []
        offs_idx = []
        for i in range(len(pl) - 1):
            ax, ay = pl[i]
            bx, by = pl[i + 1]
            dx, dy = bx - ax, by - ay
            L = math.hypot(dx, dy)
            if L == 0.0:
                continue
            nx, ny = sgn * -dy / L, sgn * dx / L
            offs.append(((ax + nx * distance_m, ay + ny * distance_m),
                         (bx + nx * distance_m, by + ny * distance_m)))
            offs_idx.append(i)
        if not offs:
            return []
        pts = [offs[0][0]]
        for i in range(len(offs)):
            if i + 1 < len(offs):
                hit = _line_intersect(offs[i][0][0], offs[i][0][1],
                                      offs[i][1][0], offs[i][1][1],
                                      offs[i + 1][0][0], offs[i + 1][0][1],
                                      offs[i + 1][1][0], offs[i + 1][1][1])
                if (hit is not None and -1e-9 <= hit[2] <= 1.0 + 1e-9
                        and -1e-9 <= hit[3] <= 1.0 + 1e-9):
                    pts.append((hit[0], hit[1]))
                else:
                    pts.append(offs[i][1])
                    pi0 = offs_idx[i]
                    pi1 = offs_idx[i + 1]
                    ax, ay = pl[pi0]
                    bx, by = pl[pi0 + 1]
                    cx, cy = pl[pi1]
                    dx, dy = pl[pi1 + 1]
                    cross = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
                    inner = (side == "left") == (cross > 0.0)
                    if (not inner and abs(cross) > 1e-12
                            and pi1 == pi0 + 1):
                        seg_len_a = math.hypot(bx - ax, by - ay)
                        seg_len_b = math.hypot(dx - cx, dy - cy)
                        n_a = (sgn * -(by - ay) / seg_len_a,
                               sgn * (bx - ax) / seg_len_a) \
                            if seg_len_a > 0.0 else (0.0, 0.0)
                        n_b = (sgn * -(dy - cy) / seg_len_b,
                               sgn * (dx - cx) / seg_len_b) \
                            if seg_len_b > 0.0 else (0.0, 0.0)
                        pts.extend(_arc_join((cx, cy), distance_m, n_a, n_b))
                    pts.append(offs[i + 1][0])
            else:
                pts.append(offs[i][1])
        samples, total = _sample_polyline(pts, OFFSET_SAMPLE_M)
        if total == 0.0:
            return []
        cum = _cum_of(pts)
        flags = [self._offset_blocked(E, N, carrier_id) for _, (E, N) in samples]
        pieces = []
        i = 0
        while i < len(samples):
            if flags[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(samples) and not flags[j + 1]:
                j += 1
            lo = _refine_edge(samples, flags, i, True, self, carrier_id)
            hi = _refine_edge(samples, flags, j, False, self, carrier_id)
            if hi - lo >= MIN_PIECE_M - 1e-9:
                seg = [_point_at(pts, cum, lo)]
                for k in range(1, len(pts) - 1):
                    if cum[k] > lo + 1e-9 and cum[k] < hi - 1e-9:
                        seg.append(pts[k])
                seg.append(_point_at(pts, cum, hi))
                pieces.append(seg)
            i = j + 1
        return pieces

    # -- serialisation ---------------------------------------------------
    def to_json(self):
        roads = {}
        for cid in sorted(self._roads, key=str):
            r = self._roads[cid]
            ev = r.get("evidence", {})
            roads[str(cid)] = {
                "carrier_class": r["class"],
                "carriageway_width_m": r["width"],
                "half_width_m": r["half"],
                "sidewalk_width_m": r["sidewalk"],
                "source": r["source"],
                "side_evidence": {
                    side: ev.get(side) for side in ("left", "right")
                },
            }
        footways = {}
        for cid in sorted(self._footways, key=str):
            r = self._footways[cid]
            footways[str(cid)] = {
                "carrier_class": r["class"],
                "half_width_m": r["half"],
                "source": r["source"],
            }
        by_source: dict[str, int] = {}
        for r in self._roads.values():
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        res_rings = _building_residue(self._roads, self._buildings)
        return {
            "schema": SCHEMA,
            "spine": dict(self._spine_info),
            "counts": {
                "road_carriers": len(self._roads),
                "road_carriers_by_source": dict(sorted(by_source.items())),
                "footway_carriers": len(self._footways),
                "junctions": len(self._junctions),
                "sidewalk_roads": sum(1 for r in self._roads.values()
                                        if r["sidewalk"] > 0.0),
                "explicit_sidewalks": len(self._esw),
                "buildings": len(self._buildings),
                "rail_carriers": len(self._rail),
                "rail_corridors": len(self._corridors),
                "water_carriers": len(self._water),
                "centreline_conflicts": len(self._conflicts),
                "centreline_through_ring": len(self._through_ring),
                "building_residue": len(res_rings),
                "building_residue_rings": res_rings,
                "measured_roads": sum(
                    1 for r in self._roads.values()
                    if r.get("evidence")),
                "tag_roads": sum(
                    1 for r in self._roads.values()
                    if r["source"].startswith("tag:")),
                "measured_sidewalks": len(self._msw),
            },
            "roads": roads,
            "footways": footways,
            "rail_corridors": [
                {"id": corr["id"],
                 "carriers": [str(c) for c in corr["carriers"]]}
                for corr in self._corridors
            ],
            "junctions": [
                {"id": j["id"], "E": j["E"], "N": j["N"],
                 "radius_m": j["radius"],
                 "carrier_ids": [str(c) for c in j["carriers"]]}
                for j in self._junctions
            ],
            "centreline_conflicts": [
                {"a": str(c["a"]), "b": str(c["b"]),
                 "distance_m": c["distance_m"],
                 "width_a_m": c["width_a_m"],
                 "width_b_m": c["width_b_m"]}
                for c in self._conflicts
            ],
            "centreline_through_ring": [
                {"carrier": str(c["carrier"]), "feature": str(c["feature"])}
                for c in self._through_ring
            ],
            "measured_sidewalks": sorted(str(c) for c in self._msw),
            "defaults_used": list(self._defaults_used),
        }


def _width_maps(surf_ids, roads, footways):
    halves = {}
    full_width = {}
    for cid in surf_ids:
        if cid in roads:
            halves[cid] = roads[cid]["half"]
            full_width[cid] = roads[cid]["width"]
        else:
            halves[cid] = footways[cid]["half"]
            full_width[cid] = footways[cid]["width"]
    return halves, full_width


def _find_conflicts(surf_ids, polys, halves, full_width, shared_discs):
    """Centreline pairs passing closer than the sum of their halves
    with at least one approach outside every shared junction disc."""
    conflicts = []
    for a_i in range(len(surf_ids)):
        for b_i in range(a_i + 1, len(surf_ids)):
            a, b = surf_ids[a_i], surf_ids[b_i]
            pa, pb = polys[a], polys[b]
            if _endpoints_within(pa, pb, NODE_CLUSTER_M):
                continue
            lim = halves[a] + halves[b]
            best = float("inf")
            approaches = []
            for i in range(len(pa) - 1):
                ax, ay = pa[i]
                bx, by = pa[i + 1]
                for k in range(len(pb) - 1):
                    cx, cy = pb[k]
                    dx, dy = pb[k + 1]
                    if (min(ax, bx) > max(cx, dx) + lim or
                            min(cx, dx) > max(ax, bx) + lim or
                            min(ay, by) > max(cy, dy) + lim or
                            min(cy, dy) > max(ay, by) + lim):
                        continue
                    d, pt = _seg_seg_closest(ax, ay, bx, by, cx, cy,
                                             dx, dy)
                    if d < best:
                        best = d
                    if d < lim:
                        approaches.append(pt)
            if best < lim and approaches:
                discs = shared_discs.get(frozenset((a, b)), [])
                if all(any(math.hypot(pt[0] - j["E"], pt[1] - j["N"])
                           <= j["radius"] for j in discs)
                       for pt in approaches):
                    continue
                conflicts.append({"a": a, "b": b,
                                  "distance_m": round(best, 3),
                                  "width_a_m": full_width[a],
                                  "width_b_m": full_width[b]})
    conflicts.sort(key=lambda c: (str(c["a"]), str(c["b"])))
    return conflicts


def load_surfaces(zones_path: Path, world_ir_path: Path, rules_path: Path,
                  plan_path: Path | None = None,
                  painted_plan_path: Path | None = None) -> Surfaces:
    zones = json.loads(Path(zones_path).read_text())
    world_ir = json.loads(Path(world_ir_path).read_text())
    rules = json.loads(Path(rules_path).read_text())
    spans = rules["spans"]
    wd = _read_width_defaults(rules)
    cw, cw_src = wd["carriageway"]
    fwh, fwh_src = wd["footway"]
    sw_w, sw_src = wd["sidewalk_width"]
    sw_classes, sw_classes_src = wd["sidewalk_classes"]
    lane_m, lane_src = wd["lane"]
    tag_extra, tag_extra_src = wd["tag_extra"]
    spine = _SpineIndex(_load_spine())
    spine_len = spine.cum[-1]
    defaults_used: list[str] = []
    roads = {}
    footways = {}
    pending = []
    tag_widths = _tag_widths(world_ir, lane_m, tag_extra)
    for z in zones:
        cls = z["carrier_class"]
        if not cls.startswith("road:"):
            continue
        suffix = cls.split(":", 1)[1]
        cid = z["carrier_id"]
        pl = [(float(p[0]), float(p[1])) for p in z["polyline_epsg32654"]]
        if suffix in fwh:
            half = fwh[suffix]
            footways[cid] = {"class": cls, "suffix": suffix,
                             "polyline": pl,
                             "half": half, "width": half * 2.0,
                             "source": "DEFAULT:" + suffix,
                             "bbox": _bbox(pl)}
            defaults_used.append(
                f"footway_half_width_m_by_kind.{suffix} "
                f"{fwh_src[suffix]}")
            continue
        samples, total = _sample_polyline(pl, GOV_SAMPLE_M)
        near = sum(1 for _, (E, N) in samples
                   if spine.dist(E, N) <= SPINE_WITHIN_M)
        frac = (near / len(samples)) if samples else 0.0
        span_hit = None
        if frac >= SPINE_MIN_FRAC and total > 0.0:
            mid = _point_at(pl, _cum_of(pl), total / 2.0)
            s_mid, _ = spine.project(mid[0], mid[1])
            for si, sp in enumerate(spans):
                lo, hi = sp["span_m"]
                if lo <= s_mid <= hi:
                    span_hit = (si, sp)
                    break
        if span_hit is not None:
            si, sp = span_hit
            width = float(sp["subjects"]["road_structure"]["parameters"]
                            ["carriageway_width_m"]["value"])
            sw = float(sp["subjects"]["accessibility"]["parameters"]
                         ["sidewalk_width_m"]["value"])
            source = "span:" + str(sp["route_key"])
            roads[cid] = {"class": cls, "suffix": suffix, "polyline": pl,
                          "width": width, "half": width / 2.0,
                          "sidewalk": sw, "source": source,
                          "bbox": _bbox(pl), "evidence": {}}
        else:
            tag = tag_widths.get(str(cid))
            if tag is not None:
                width, source, ref = tag
                sw = (sw_w if suffix in sw_classes else 0.0)
                half = width / 2.0
                side = {"kind": "tag", "ref": ref, "half_m": half}
                roads[cid] = {"class": cls, "suffix": suffix,
                              "polyline": pl, "width": width,
                              "half": half, "sidewalk": sw,
                              "source": source, "bbox": _bbox(pl),
                              "evidence": {"left": dict(side),
                                             "right": dict(side)}}
                if source == "tag:lanes":
                    defaults_used.append(f"lane_width_m {lane_src}")
            else:
                pending.append((cid, cls, suffix, pl))
    buildings = []
    for f in world_ir["canonical_features"]:
        if f.get("semantic_class") != "building_footprint":
            continue
        ring = [(float(p[0]), float(p[1]))
                for p in f["geometry"]["coordinates"]]
        buildings.append((f["feature_id"], ring, _bbox(ring)))
    buildings.sort(key=lambda t: str(t[0]))
    observations = []
    if plan_path is not None:
        marks = _marking_modules()
        plan = json.loads(Path(plan_path).read_text())
        struct = plan.get("structural", {}) or {}
        for ob in struct.get("observation_surface_conflicts", []) or []:
            if ob.get("module") in marks:
                continue
            if ob.get("instance_id") is None:
                continue
            try:
                E, N = float(ob["E"]), float(ob["N"])
            except (KeyError, TypeError, ValueError):
                continue
            observations.append({"instance_id": ob["instance_id"],
                                 "E": E, "N": N})
        observations.sort(key=lambda o: str(o["instance_id"]))
    measured_sidewalks = set()
    for cid, cls, suffix, pl in pending:
        default_width = float(cw[suffix])
        default_half = default_width / 2.0
        sw = (sw_w if suffix in sw_classes else 0.0)
        pev = _parallel_evidence(cid, pl, suffix, default_half,
                                 roads, footways, fwh)
        bev = _building_evidence(pl, default_half, buildings)
        oev = _observation_evidence(pl, default_half, observations)
        winners = {}
        for side in ("left", "right"):
            cands = []
            if side in pev:
                kind, oid, half = pev[side]
                rank = 2 if kind == "footway" else 3
                cands.append((half, rank, kind, str(oid)))
            if side in bev:
                dist, fid = bev[side]
                cands.append((max(MEAS_HALF_MIN_M, dist - BUILD_SUB_M),
                              0, "building", str(fid)))
            if side in oev:
                dist, iid = oev[side]
                cands.append((dist + OBS_ADD_M, 1, "observation",
                              str(iid)))
            if cands:
                winners[side] = min(cands)
        for side in ("left", "right"):
            if side in pev and pev[side][0] == "footway":
                measured_sidewalks.add(pev[side][1])
        if winners:
            halves = {}
            for s in ("left", "right"):
                halves[s] = winners[s][0] if s in winners else default_half
            width = halves["left"] + halves["right"]
            kinds = []
            refs = []
            for s in ("left", "right"):
                if s in winners:
                    kinds.append(winners[s][2])
                    refs.append(winners[s][3])
            source = ("measured:" + "+".join(kinds) + ":"
                      + "+".join(refs))
        else:
            width = default_width
            source = "DEFAULT:" + suffix
            defaults_used.append(
                f"carriageway_width_m_by_highway.{suffix} "
                f"{cw_src[suffix]}")
            if sw > 0.0:
                defaults_used.append(
                    f"sidewalk_default {sw_src}")
        roads[cid] = {"class": cls, "suffix": suffix, "polyline": pl,
                      "width": width, "half": width / 2.0,
                      "sidewalk": sw, "source": source,
                      "bbox": _bbox(pl),
                      "evidence": {s: ({"kind": winners[s][2],
                                       "ref": winners[s][3],
                                       "half_m": winners[s][0]}
                                      if s in winners else None)
                                  for s in ("left", "right")}}
    # FL-4876 ROAD-XSECTION-01. Population placed road-edge hardware from
    # these span/tag/measured/DEFAULT widths while the stamper painted the
    # asphalt from OSM width, the FL-4934 LiDAR corridor or the class
    # default, so curb bollards and poles stood up to a third of the way into
    # the painted road (primary way/1059458086: curb at 2.96 m, paint edge at
    # 5.50 m). With the stamp plan supplied, the painted cross-section is the
    # carriageway on each side; the building bounds below still only narrow.
    if painted_plan_path is not None:
        for cid, (hl, hr, src) in _painted_road_halves(
                painted_plan_path, roads).items():
            r = roads[cid]
            prev = r["source"]
            r["evidence"] = {
                "left": {"kind": "painted_stamp", "ref": f"way/{cid}",
                         "half_m": hl, "replaces": prev},
                "right": {"kind": "painted_stamp", "ref": f"way/{cid}",
                          "half_m": hr, "replaces": prev}}
            r["width"] = hl + hr
            r["half"] = r["width"] / 2.0
            r["source"] = "painted:" + (src.split(";")[0][:80] or "stamp")
    # Universal building bound: no carriageway may contain a ring. Applies
    # to every road carrier (span, tag, measured, DEFAULT); each side takes
    # the smallest of its current half and the ring bound.
    through_ring = []
    for cid in sorted(roads, key=str):
        r = roads[cid]
        pl = r["polyline"]
        for fid in _through_ring(pl, buildings):
            through_ring.append({"carrier": cid, "feature": fid})
        bev = _building_evidence(pl, _road_max_half(r), buildings)
        if not bev:
            continue
        changed = False
        for side in ("left", "right"):
            if side not in bev:
                continue
            dist, fid = bev[side]
            bound = max(MEAS_HALF_MIN_M, dist - BUILD_SUB_M)
            cur = (r["evidence"].get(side) or {}).get("half_m",
                                                          r["half"])
            if bound < cur - 1e-9:
                r["evidence"][side] = {"kind": "building",
                                        "ref": str(fid),
                                        "half_m": bound}
                changed = True
        if changed:
            hl = (r["evidence"].get("left") or {}).get("half_m",
                                                            r["half"])
            hr = (r["evidence"].get("right") or {}).get("half_m",
                                                             r["half"])
            r["width"] = hl + hr
            r["half"] = r["width"] / 2.0
            kinds = []
            refs = []
            for s in ("left", "right"):
                e = r["evidence"].get(s)
                if e is not None:
                    kinds.append(e["kind"])
                    refs.append(str(e["ref"]))
            if kinds:
                r["source"] = ("measured:" + "+".join(kinds) + ":"
                                + "+".join(refs))
    through_ring.sort(key=lambda c: (str(c["carrier"]), str(c["feature"])))
    # RING-01 ring-gap bound: for every DEFAULT-width carriageway, the
    # exact measured gap to the nearest building ring on each side bounds
    # the half on that side: min(DEFAULT half, gap - sidewalk band where
    # a sidewalk exists, else gap). Rings the centreline crosses are
    # geometry conflicts (through_ring above), never a width question.
    # Span/tag/measured widths are never touched here.
    through_fids = {}
    for e in through_ring:
        through_fids.setdefault(e["carrier"], set()).add(e["feature"])
    for cid in sorted(roads, key=str):
        r = roads[cid]
        if not r["source"].startswith("DEFAULT:"):
            continue
        def_half = r["half"]
        gaps = _ring_gap_sides(r["polyline"], def_half, buildings,
                               through_fids.get(cid, set()))
        if not gaps:
            continue
        sub = r["sidewalk"] if r["sidewalk"] > 0.0 else 0.0
        prev_source = r["source"]
        for side in ("left", "right"):
            if side not in gaps:
                continue
            gap, fid = gaps[side]
            bound = min(def_half, gap - sub)
            if bound <= 1e-9:
                continue
            cur = (r["evidence"].get(side) or {}).get("half_m",
                                                          def_half)
            if bound < cur - 1e-9:
                tail = str(fid).split("way/")[-1]
                ref = "way/" + tail if "way/" in str(fid) \
                    else str(fid)
                r["evidence"][side] = {"kind": "building_ring_gap",
                                         "ref": ref,
                                         "half_m": bound,
                                         "replaces": prev_source}
        hl = (r["evidence"].get("left") or {}).get("half_m",
                                                            def_half)
        hr = (r["evidence"].get("right") or {}).get("half_m",
                                                             def_half)
        if hl < def_half - 1e-9 or hr < def_half - 1e-9:
            r["width"] = hl + hr
            r["half"] = r["width"] / 2.0
            kinds = []
            refs = []
            for s in ("left", "right"):
                e = r["evidence"].get(s)
                if e is not None:
                    kinds.append(e["kind"])
                    refs.append(str(e["ref"]))
            r["source"] = ("measured:" + "+".join(kinds) + ":"
                             + "+".join(refs))
    # junctions over carriageway + footway carriers; footway-only nodes skip
    surf_ids = sorted(list(roads) + list(footways), key=str)
    polys = {}
    for cid in surf_ids:
        polys[cid] = roads[cid]["polyline"] if cid in roads \
            else footways[cid]["polyline"]
    cands: list = []
    ends = []
    for cid in surf_ids:
        ends.append((polys[cid][0], cid))
        ends.append((polys[cid][-1], cid))
    used = [False] * len(ends)
    for i, (p, ci) in enumerate(ends):
        if used[i]:
            continue
        group = [(p, ci)]
        used[i] = True
        for j in range(i + 1, len(ends)):
            if used[j]:
                continue
            q, cj = ends[j]
            if math.hypot(p[0] - q[0], p[1] - q[1]) <= NODE_CLUSTER_M:
                group.append((ends[j][0], cj))
                used[j] = True
        cids = {c for _, c in group}
        if len(cids) >= 2:
            ex = sum(p[0] for p, _ in group) / len(group)
            ny = sum(p[1] for p, _ in group) / len(group)
            cands.append([ex, ny, cids])
    seg_index = []
    for cid in surf_ids:
        pl = polys[cid]
        for i in range(len(pl) - 1):
            a, b = pl[i], pl[i + 1]
            seg_index.append((cid, a[0], a[1], b[0], b[1]))
    for i in range(len(seg_index)):
        ci, ax, ay, bx, by = seg_index[i]
        bb1 = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
        for j in range(i + 1, len(seg_index)):
            cj, cx, cy, dx, dy = seg_index[j]
            if ci == cj:
                continue
            bb2 = (min(cx, dx), min(cy, dy), max(cx, dx), max(cy, dy))
            if bb1[2] < bb2[0] or bb2[2] < bb1[0] or bb1[3] < bb2[1] \
                    or bb2[3] < bb1[1]:
                continue
            hit = _seg_intersect(ax, ay, bx, by, cx, cy, dx, dy)
            if hit is not None:
                cands.append([hit[0], hit[1], {ci, cj}])
    parent = list(range(len(cands)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            if math.hypot(cands[i][0] - cands[j][0],
                           cands[i][1] - cands[j][1]) <= CANDIDATE_MERGE_M:
                union(i, j)
    groups: dict[int, list[int]] = {}
    for i in range(len(cands)):
        groups.setdefault(find(i), []).append(i)
    junctions = []
    for g in sorted(groups.values(), key=lambda g: min(
            (cands[i][0], cands[i][1]) for i in g)):
        ex = sum(cands[i][0] for i in g) / len(g)
        ny = sum(cands[i][1] for i in g) / len(g)
        cids = set()
        for i in g:
            cids.update(cands[i][2])
        if len(cids) < 2:
            continue
        carrs = [c for c in cids if c in roads]
        if not carrs:
            continue
        half = max(roads[c]["half"] for c in carrs)
        junctions.append({"id": f"junction:{len(junctions):04d}",
                          "E": ex, "N": ny, "radius": half,
                          "carriers": sorted(cids, key=str)})
    shared_discs: dict[frozenset, list] = {}
    for j in junctions:
        cs = j["carriers"]
        for a_i in range(len(cs)):
            for b_i in range(a_i + 1, len(cs)):
                shared_discs.setdefault(
                    frozenset((cs[a_i], cs[b_i])), []).append(j)
    halves, full_width = _width_maps(surf_ids, roads, footways)
    # RING-01 parallel-overlap narrowing: preliminary conflicts on the
    # ring-bounded widths; pairs with no shared junction disc where BOTH
    # carriageways are still DEFAULT-sourced narrow both to the midpoint
    # gap (each half becomes distance/2). Span/tag/measured widths and
    # footway-involved or junction-adjacent pairs are never touched here.
    prelim = _find_conflicts(surf_ids, polys, halves, full_width,
                             shared_discs)
    for cf in prelim:
        a, b = cf["a"], cf["b"]
        if shared_discs.get(frozenset((a, b))):
            continue
        if a not in roads or b not in roads:
            continue
        if not (roads[a]["source"].startswith("DEFAULT:")
                and roads[b]["source"].startswith("DEFAULT:")):
            continue
        d = cf["distance_m"]
        if d / 2.0 < MEAS_HALF_MIN_M:
            continue
        # 1 cm shy of the exact midpoint so the recomputed conflict test
        # (strict best < lim) sees the bands meet, not overlap, instead
        # of flickering on float noise at exact equality.
        half = d / 2.0 - 0.01
        for cid, other in ((a, b), (b, a)):
            r = roads[cid]
            prev = r["source"]
            r["width"] = half * 2.0
            r["half"] = half
            r["evidence"] = {
                s: {"kind": "parallel_carriageway_gap",
                    "ref": str(other), "half_m": half,
                    "replaces": prev}
                for s in ("left", "right")}
            r["source"] = ("measured:parallel_carriageway_gap:"
                             + str(other))
    halves, full_width = _width_maps(surf_ids, roads, footways)
    conflicts = _find_conflicts(surf_ids, polys, halves, full_width,
                                shared_discs)
    rail = []
    water = []
    for z in zones:
        cls = z["carrier_class"]
        if cls not in ("railway", "waterway"):
            continue
        pl = [(float(p[0]), float(p[1])) for p in z["polyline_epsg32654"]]
        entry = (z["carrier_id"], pl, _bbox(pl))
        if cls == "railway":
            rail.append(entry)
        else:
            water.append(entry)
    rail.sort(key=lambda t: str(t[0]))
    water.sort(key=lambda t: str(t[0]))
    esw = []
    for f in world_ir["canonical_features"]:
        if f.get("semantic_class") != "sidewalk":
            continue
        if f["geometry"]["kind"] != "polyline":
            continue
        pl = [(float(p[0]), float(p[1]))
              for p in f["geometry"]["coordinates"]]
        esw.append((f["feature_id"], pl, _bbox(pl)))
    esw.sort(key=lambda t: str(t[0]))
    if rail:
        _rh, _rs = wd["rail_half"]
        defaults_used.append(f"rail_half_width_m {_rs}")
    if water:
        _wh, _ws = wd["water_half"]
        defaults_used.append(f"waterway_half_width_m {_ws}")
    if esw:
        _eh, _es = wd["esw_half"]
        defaults_used.append(
            f"explicit_sidewalk_centreline_half_m {_es}")
    _rh, _rs = wd["rail_half"]
    _wh, _ws = wd["water_half"]
    _eh, _es = wd["esw_half"]
    s = Surfaces(roads, footways, junctions, buildings, rail, water, esw,
                 conflicts, defaults_used,
                 {"points": len(spine.pts), "length_m": round(spine_len, 3)},
                 measured_sidewalks, through_ring,
                 {"esw_half": _eh, "rail_half": _rh, "water_half": _wh})
    return s


def _point_at(pts, cum, s):
    for i in range(len(pts) - 1):
        if cum[i + 1] >= s - 1e-9:
            span = cum[i + 1] - cum[i]
            u = 0.0 if span == 0.0 else (s - cum[i]) / span
            u = max(0.0, min(1.0, u))
            return (pts[i][0] + u * (pts[i + 1][0] - pts[i][0]),
                    pts[i][1] + u * (pts[i + 1][1] - pts[i][1]))
    return pts[-1]


def _refine_edge(samples, flags, idx, is_start, s, own):
    si, sj = (idx - 1, idx) if is_start else (idx, idx + 1)
    if si < 0 or sj >= len(samples):
        return samples[idx][0]
    a = samples[si][0]
    b = samples[sj][0]
    pa = samples[si][1]
    pb = samples[sj][1]
    for _ in range(24):
        m = (a + b) / 2.0
        u = 0.0 if b == a else (m - samples[si][0]) / (samples[sj][0] - samples[si][0])
        pm = (pa[0] + u * (pb[0] - pa[0]), pa[1] + u * (pb[1] - pa[1]))
        blocked = s._offset_blocked(pm[0], pm[1], own)
        if blocked == flags[idx]:
            b, pb = m, pm
        else:
            a, pa = m, pm
    return (a + b) / 2.0


def _piece_mid(piece):
    total = sum(math.hypot(piece[i + 1][0] - piece[i][0],
                           piece[i + 1][1] - piece[i][1])
                for i in range(len(piece) - 1))
    target = total / 2.0
    acc = 0.0
    for i in range(len(piece) - 1):
        sl = math.hypot(piece[i + 1][0] - piece[i][0],
                        piece[i + 1][1] - piece[i][1])
        if acc + sl >= target:
            u = 0.0 if sl == 0.0 else (target - acc) / sl
            return (piece[i][0] + u * (piece[i + 1][0] - piece[i][0]),
                    piece[i][1] + u * (piece[i + 1][1] - piece[i][1]))
        acc += sl
    return piece[-1]


def _cum_of(pl):
    cum = [0.0]
    for i in range(len(pl) - 1):
        cum.append(cum[-1] + math.hypot(pl[i + 1][0] - pl[i][0],
                                        pl[i + 1][1] - pl[i][1]))
    return cum


def _self_check(s: Surfaces):
    fails = []
    checked = 0
    empty = 0
    for cid in sorted(list(s._roads) + list(s._footways), key=str):
        width, _ = s.road_width_m(cid)
        pieces = s.offset_polyline(cid, "left", width / 2.0 + 0.3)
        if not pieces:
            empty += 1
            continue
        checked += 1
        reasons = []
        for pi, piece in enumerate(pieces):
            mid = _piece_mid(piece)
            det = s._detail(mid[0], mid[1])
            bad = []
            if "carriageway" in det:
                other = det["carriageway"]
                bad.append(f"carriageway of {other}"
                             + (" (own)" if other == cid else ""))
            if "junction" in det:
                bad.append(f"junction {det['junction']}")
            if bad:
                reasons.append(f"piece {pi} mid "
                               f"({mid[0]:.2f},{mid[1]:.2f}): "
                               + "; ".join(bad))
        if reasons:
            fails.append((cid, reasons))
    covered = set()
    for c in s._conflicts:
        covered.add(str(c["a"]))
        covered.add(str(c["b"]))
    uncovered = [(cid, r) for cid, r in fails if str(cid) not in covered]
    return fails, checked, empty, uncovered


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build Numabe surface model")
    ap.add_argument("--zones", required=True)
    ap.add_argument("--world-ir", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--plan", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    s = load_surfaces(Path(args.zones), Path(args.world_ir),
                      Path(args.rules),
                      Path(args.plan) if args.plan else None)
    data = s.to_json()
    Path(args.out).write_text(json.dumps(data, indent=1, sort_keys=True)
                              + "\n")
    c = data["counts"]
    by_source = c["road_carriers_by_source"]
    n_span = sum(n for k, n in by_source.items() if k.startswith("span:"))
    n_meas = sum(n for k, n in by_source.items()
                 if k.startswith("measured:"))
    n_tag = sum(n for k, n in by_source.items()
                if k.startswith("tag:"))
    n_default = sum(n for k, n in by_source.items()
                    if k.startswith("DEFAULT:"))
    print(f"road carriers with width: {c['road_carriers']} "
          f"{{span: {n_span}, measured: {n_meas}, tag: {n_tag}, "
          f"default: {n_default}}}")
    n_tagev = sum(1 for r in data["roads"].values()
                  if any(((r.get("side_evidence") or {}).get(s) or {})
                           .get("kind") == "tag"
                           for s in ("left", "right")))
    print(f"tag-evidence carriers: {n_tagev}")
    for cid in sorted(data["roads"], key=str):
        r = data["roads"][cid]
        if r["source"].startswith(("measured:", "tag:")):
            print(f"  {r['source'].split(':')[0]} {cid} "
                  f"{r['carrier_class']} "
                  f"{r['carriageway_width_m']}m {r['source']} "
                  f"{r['side_evidence']}")
    print(f"footway carriers: {c['footway_carriers']} "
          f"(measured sidewalks: {c['measured_sidewalks']})")
    print(f"junctions: {c['junctions']}")
    print(f"sidewalk roads: {c['sidewalk_roads']} "
          f"explicit sidewalks: {c['explicit_sidewalks']}")
    print(f"buildings: {c['buildings']}")
    print(f"rail carriers: {c['rail_carriers']}")
    print(f"water carriers: {c['water_carriers']}")
    fails, checked, empty, uncovered = _self_check(s)
    print(f"offset self-check: {len(fails)} failing carriers "
          f"of {checked} checked ({empty} fully clipped), "
          f"{len(uncovered)} uncovered by centreline conflicts")
    for cid, reasons in fails:
        print(f"  fail {cid}: {reasons[0]}" +
              (f" +{len(reasons) - 1} more" if len(reasons) > 1 else ""))
    print(f"centreline conflicts: {len(data['centreline_conflicts'])}")
    print(f"building residue (rings in carriageway): "
          f"{c['building_residue']}")
    for fid in data.get("building_residue_rings", []):
        print(f"  residue-ring {fid}")
    print(f"centreline through ring: {c['centreline_through_ring']}")
    for tr in data["centreline_through_ring"]:
        print(f"  through-ring {tr['carrier']} {tr['feature']}")
    shared = {}
    for j in data["junctions"]:
        cs = [str(x) for x in j["carrier_ids"]]
        for a_i in range(len(cs)):
            for b_i in range(a_i + 1, len(cs)):
                shared.setdefault(frozenset((cs[a_i], cs[b_i])),
                                  []).append(j["id"])
    for cf in data["centreline_conflicts"]:
        a, b = str(cf["a"]), str(cf["b"])
        ra = (data["roads"].get(a) or {})
        rb = (data["roads"].get(b) or {})
        sa, sb = str(ra.get("source", "")), str(rb.get("source", ""))
        narrowed_pair = (
            "parallel_carriageway_gap:" + b in sa
            and "parallel_carriageway_gap:" + a in sb)
        if frozenset((a, b)) in shared:
            kind = ("junction-adjacent (expected) "
                    + "+".join(shared[frozenset((a, b))]))
        elif narrowed_pair:
            kind = "parallel-overlap narrowed this run"
        else:
            kind = "parallel-overlap listed (untouched)"
        print(f"  conflict {cf['a']} {cf['b']} {cf['distance_m']}m "
              f"widths {cf['width_a_m']}/{cf['width_b_m']} {kind}")
    print(f"defaults used: {data['defaults_used']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
