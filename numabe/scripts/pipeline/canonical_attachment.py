#!/usr/bin/env python3
"""Canonical attachment contract (FL-4809 ATTACH-01, REVIEW v4 re-anchor).

Typed placement records derived from canonical IR geometry ONLY. A record
names canonical ids plus relative measures (fractions, chainages, offsets);
it carries no absolute position, so a host ring switch re-resolves through
resolve() instead of silently dragging the placement. Tolerances come from
the rules attachment_tolerances DEFAULT fact, never literals here.

Anchor kinds: building_face | carrier_chainage | carrier_span |
freestanding | terrain_mass. attach() returns an attachment record or a
spatial_reconciliation_debt dict; resolve() returns {E, N, yaw_deg}.
"""

from __future__ import annotations

import math
from pathlib import Path  # noqa: F401 (stable import surface for consumers)

ANCHOR_KINDS = (
    "building_face",
    "carrier_chainage",
    "carrier_span",
    "freestanding",
    "terrain_mass",
)

_CHAINAGE_CARRIERS = ("road", "railway_boundary")

# Shared physical paint footprint.  The planner uses it to prove a resolved
# point decal clears masses; the stamper uses the same value to rasterize it.
# A point observation does not justify a more detailed stripe/arrow polygon.
SURFACE_DECAL_DIAMETER_M = {
    "crosswalk": 2.5,
    "stop_line": 2.0,
    "arrow": 2.0,
    "other": 1.5,
    "fixture": 2.0,
}


def _closed_ring(ring: list) -> list:
    return ring if len(ring) > 1 and ring[0] == ring[-1] else list(ring)


def _edges(ring: list):
    """Ring edges, 0-based: a closed n-point ring has n-1 edges, indices
    0..n-2. The closing duplicate point never forms an edge (it would be
    zero-length and unresolvable downstream)."""
    pts = _closed_ring(ring)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return list(zip(pts, pts[1:] + pts[:1]))


def ring_edges(ring: list) -> list:
    """Canonical edge list: the single owner of face identity (REVIEW v4).

    Both planners must index faces through this function over the same
    canonical ring. A closed n-point ring exposes n-1 edges, 0-based;
    the closing duplicate never forms an edge."""
    return _edges(ring)


def _ring_area(ring: list) -> float:
    area = 0.0
    for (ax, ay), (bx, by) in _edges(ring):
        area += ax * by - bx * ay
    return abs(area) / 2.0


def _centroid(ring: list) -> tuple[float, float]:
    pts = list(ring)
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


def _point_in_ring(px: float, py: float, ring: list) -> bool:
    inside = False
    for (ax, ay), (bx, by) in _edges(ring):
        if ((ay > py) != (by > py)) and (
                px < (bx - ax) * (py - ay) / (by - ay) + ax):
            inside = not inside
    return inside


def _project(px: float, py: float, a: list, b: list):
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / den)) \
        if den > 0 else 0.0
    return (a[0] + t * dx, a[1] + t * dy), t, math.dist((px, py),
                                                       (a[0] + t * dx,
                                                        a[1] + t * dy))


def _poly_length(poly: list) -> float:
    return sum(math.dist(a, b) for a, b in zip(poly, poly[1:]))


def _point_at_chainage(poly: list, chainage: float):
    """(point, bearing_deg) at arc-length chainage along a polyline."""
    acc = 0.0
    for a, b in zip(poly, poly[1:]):
        seg = math.dist(a, b)
        if acc + seg >= chainage or b == poly[-1]:
            t = 0.0 if seg <= 0 else min(1.0, max(0.0, (chainage - acc)
                                                  / seg))
            pt = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            yaw = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) \
                if seg > 0 else 0.0
            return pt, yaw
        acc += seg
    last, prev = poly[-1], poly[-2]
    return last, math.degrees(math.atan2(last[1] - prev[1],
                                         last[0] - prev[0]))


class CanonicalIndex:
    """Indexed canonical IR: id map, mass rings, carrier polylines, terrain."""

    def __init__(self, canonical_doc: dict, route_xy=None):
        self.by_id = {f["feature_id"]: f
                      for f in canonical_doc["canonical_features"]}
        self.buildings: list[tuple[str, list]] = []
        self.carriers: list[tuple[str, list]] = []
        self.terrains: list[tuple[str, list]] = []
        for fid in sorted(self.by_id):
            feat = self.by_id[fid]
            cls = feat.get("semantic_class")
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates")
            if not isinstance(coords, list) or not coords:
                continue
            if cls == "building_footprint" and geom.get("kind") == "polygon":
                self.buildings.append((fid, coords))
            elif cls in _CHAINAGE_CARRIERS and geom.get("kind") == "polyline" \
                    and len(coords) >= 2:
                self.carriers.append((fid, coords))
            elif cls == "terrain" and geom.get("kind") == "polygon":
                self.terrains.append((fid, coords))
        self.route_xy = list(route_xy) if route_xy else None
        # FL-4876 CLEARANCE-PERF-01. A bounding box per mass ring, built once.
        # mass_clearance_m and the lattice search below ask "how far is this
        # point from any mass" thousands of times per placement, and every ask
        # was walking all 733 rings and every edge of each. The box gives an
        # exact lower bound on a ring's distance, so a ring that cannot beat
        # the best distance so far is skipped without touching its edges.
        # This prunes work; it never changes an answer.
        self.building_bboxes: list[tuple[float, float, float, float]] = []
        for _fid, _ring in self.buildings:
            _xs = [float(q[0]) for q in _ring]
            _ys = [float(q[1]) for q in _ring]
            self.building_bboxes.append(
                (min(_xs), min(_ys), max(_xs), max(_ys)))

    def nearest_face(self, px: float, py: float):
        """(dist, building_fid, edge_index, a, b, q) over all mass rings."""
        best = None
        for fid, ring in self.buildings:
            for i, (a, b) in enumerate(_edges(ring)):
                q, _, dist = _project(px, py, a, b)
                if best is None or dist < best[0]:
                    best = (dist, fid, i, a, b, q)
        return best

    def nearest_carrier(self, px: float, py: float):
        """(dist, carrier_fid, chainage, q, bearing) over carrier ways."""
        best = None
        for fid, poly in self.carriers:
            acc = 0.0
            for a, b in zip(poly, poly[1:]):
                q, t, dist = _project(px, py, a, b)
                seg = math.dist(a, b)
                if best is None or dist < best[0]:
                    bearing = math.degrees(math.atan2(
                        b[1] - a[1], b[0] - a[0])) if seg > 0 else 0.0
                    best = (dist, fid, acc + t * seg, q, bearing)
                acc += seg
        return best

    def containing_building(self, px: float, py: float):
        """Smallest-area mass ring containing the point, or None."""
        inside = [(fid, ring) for fid, ring in self.buildings
                  if _point_in_ring(px, py, ring)]
        if not inside:
            return None
        return sorted(inside, key=lambda fr: (_ring_area(fr[1]),
                                              fr[0]))[0]

    def containing_terrain(self, px: float, py: float):
        inside = [(fid, ring) for fid, ring in self.terrains
                  if _point_in_ring(px, py, ring)]
        if not inside:
            return None
        return sorted(inside, key=lambda fr: (_ring_area(fr[1]),
                                              fr[0]))[0][0]


def _tolerances(rules: dict) -> dict:
    try:
        block = rules["attachment_tolerances"]
        return {"carrier_association_m": float(
                    block["carrier_association_m"]["value"]),
                "face_resolve_m": float(block["face_resolve_m"]["value"])}
    except KeyError as exc:
        raise ValueError(
            "attachment_tolerances DEFAULT fact missing from rules "
            f"(absent key {exc}); regenerate rules_resolved.json with "
            "compile_rules_resolved.py") from exc


def _outward_offset_m(rules: dict) -> float:
    try:
        return float(rules["road_class_defaults"]
                     ["place_front_outward_offset_m"]["value"])
    except KeyError as exc:
        raise ValueError(
            "road_class_defaults.place_front_outward_offset_m missing "
            f"from rules (absent key {exc})") from exc


def _debt(anchor_kind: str, reason: str, residual_m: float | None) -> dict:
    return {"record_type": "spatial_reconciliation_debt",
            "anchor_kind": anchor_kind, "reason": reason,
            "residual_m": residual_m}


def _geometry_sigma_m(feature: dict) -> float | None:
    """Return the feature's declared isotropic geometry uncertainty.

    Street observations carry this as a canonical claim.  It is the only
    lawful budget for reconciling an impossible point against newer mass
    geometry; attachment tolerances describe association, not permission to
    move evidence.
    """
    claim = (feature.get("properties") or {}).get("geometry") or {}
    uncertainty = claim.get("uncertainty") or {}
    if uncertainty.get("model") != "isotropic_sigma":
        return None
    try:
        sigma = float(uncertainty["sigma"])
    except (KeyError, TypeError, ValueError):
        return None
    return sigma if sigma >= 0.0 else None


def _inside_any_building(px: float, py: float,
                         index: "CanonicalIndex") -> bool:
    return any(_point_in_ring(px, py, ring) for _, ring in index.buildings)


def mass_clearance_m(px: float, py: float, index: "CanonicalIndex") -> float:
    """Signed distance from a point to the nearest canonical mass ring.

    Negative inside a mass, positive outside, ``inf`` where no mass exists.

    FL-4876 FREESTAND-01. This is the measurement the boolean predicate below
    is built from, made public because the attachment stage does not know an
    object's asset and can only test a point. A downstream consumer that has
    resolved the actual archetype knows the real footprint, and has to be
    able to re-test the chosen position against it and report the shortfall
    by identity. A point being clear is not the same fact as the mesh at that
    point being clear.
    """
    # FL-4876 CLEARANCE-PERF-01. Same value as the exhaustive scan, reached
    # by skipping rings whose bounding box already lies farther than the best
    # distance found so far, and by testing containment only for rings whose
    # box actually contains the point.
    boxes = getattr(index, "building_bboxes", None)
    if boxes is None or len(boxes) != len(index.buildings):
        nearest = min((_dist_point_to_ring(px, py, ring)
                       for _, ring in index.buildings),
                      default=float("inf"))
        return -nearest if _inside_any_building(px, py, index) else nearest
    nearest = float("inf")
    inside = False
    for i, (_, ring) in enumerate(index.buildings):
        bb = boxes[i]
        if _dist_point_to_bbox(px, py, bb) >= nearest:
            # Its box is already farther than the best edge found, and no part
            # of the ring escapes its box. Containment is impossible too,
            # because a contained point has a box distance of zero.
            continue
        d = _dist_point_to_ring(px, py, ring)
        if d < nearest:
            nearest = d
        if not inside and bb[0] <= px <= bb[2] and bb[1] <= py <= bb[3]:
            if _point_in_ring(px, py, ring):
                inside = True
    return -nearest if inside else nearest


def _clear_of_buildings(px: float, py: float, clearance_m: float,
                        index: "CanonicalIndex") -> bool:
    """Is this point farther than ``clearance_m`` from every mass.

    FL-4876 CLEARANCE-PERF-01. This asks a THRESHOLD question, so it does not
    need the nearest distance; it needs to know whether anything is nearer
    than the threshold. It answers False on the first ring that is, instead of
    measuring all 733 rings and comparing at the end.

    A point inside a mass has a NEGATIVE clearance, so containment alone does
    not settle it: inside a ring the clearance is ``-nearest``, and against a
    negative threshold that can still pass. The short-circuit is sound only
    for a threshold of zero or more, and a negative one falls through to the
    exact measurement. The first version of this function claimed otherwise
    and was caught by
    ``test_the_threshold_predicate_matches_the_measurement[-1.0]``.
    """
    boxes = getattr(index, "building_bboxes", None)
    if (boxes is None or len(boxes) != len(index.buildings)
            or clearance_m < 0.0):
        return mass_clearance_m(px, py, index) > clearance_m
    for i, (_, ring) in enumerate(index.buildings):
        bb = boxes[i]
        if _dist_point_to_bbox(px, py, bb) > clearance_m:
            continue
        if bb[0] <= px <= bb[2] and bb[1] <= py <= bb[3]:
            if _point_in_ring(px, py, ring):
                # Inside a mass the clearance is at most zero, which cannot
                # exceed a threshold of zero or more.
                return False
        if _dist_point_to_ring(px, py, ring) <= clearance_m:
            return False
    return True


def footprint_mass_conflicts(east: float, north: float, yaw_deg: float,
                             longitudinal_m: float, lateral_m: float,
                             index: "CanonicalIndex",
                             exclude_mass_ids=()) -> list:
    """Mass ids whose ring the oriented asset footprint actually overlaps.

    The footprint is the asset's declared longitudinal-by-lateral rectangle,
    centred on the placement anchor and rotated by the placement yaw. Every
    template in the pack declares ``anchor_origin: ground_center`` and
    ``orientation_basis: yaw_only``, so both of those are read, not assumed.

    FL-4876 FREESTAND-01. The first version of this check used the
    footprint's circumscribing radius, and measurement refuted it: a
    1.6 x 0.12 m shop signboard standing 0.7 m off its wall has a 0.802 m
    half-diagonal, so a radius test calls that a wall conflict -- for an
    asset whose 1.6 m dimension runs *along* the wall and which protrudes
    0.06 m toward it. 96 of 102 radius hits on this lane were that false
    positive. A radius is only the right bound for a square footprint, and
    almost no street asset has one.

    ``exclude_mass_ids`` drops the asset's own host. A facade fixture is
    mounted on its building; the building it is mounted on is not a
    collision, it is the thing holding it up.
    """
    half_l, half_w = abs(longitudinal_m) / 2.0, abs(lateral_m) / 2.0
    theta = math.radians(yaw_deg or 0.0)
    ux, uy = math.cos(theta), math.sin(theta)
    vx, vy = -uy, ux
    corners = [[east + ux * sl * half_l + vx * sw * half_w,
                north + uy * sl * half_l + vy * sw * half_w]
               for sl, sw in ((1, 1), (1, -1), (-1, -1), (-1, 1))]
    footprint_ring = corners + [corners[0]]
    reach = math.hypot(half_l, half_w)
    excluded = set(exclude_mass_ids or ())
    hits = []
    for fid, ring in index.buildings:
        if fid in excluded:
            continue
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if (east + reach < min(xs) or east - reach > max(xs)
                or north + reach < min(ys) or north - reach > max(ys)):
            continue
        # polygons_overlap is the module's owner of ring-versus-ring overlap
        # and already covers all three ways two rings can meet: one inside
        # the other, either way round, and edges that cross. A hand-rolled
        # trio here was measurably worse -- two of its three branches were
        # redundant and one real case (a mass smaller than the footprint)
        # was missing from all of them.
        if polygons_overlap(footprint_ring, ring):
            hits.append(fid)
    return hits


def _segments_intersect(a, b, c, d) -> bool:
    """True when segments ab and cd share at least one point, touching included.

    FL-4876 FREESTAND-01. This body is the predicate ``polygons_overlap``
    carried as a local closure, lifted out unchanged so there is one owner
    instead of a copy per caller. Its tolerances are load-bearing and are
    kept exactly as the production path had them.
    """
    def _cross(p, q, r):
        return ((q[0] - p[0]) * (r[1] - p[1])
                - (q[1] - p[1]) * (r[0] - p[0]))

    def _on(p, q, r):
        return (min(p[0], q[0]) - 1e-9 <= r[0] <= max(p[0], q[0]) + 1e-9
                and min(p[1], q[1]) - 1e-9 <= r[1] <= max(p[1], q[1]) + 1e-9)

    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    if ((ab_c > 1e-9 and ab_d < -1e-9 or ab_c < -1e-9 and ab_d > 1e-9)
            and (cd_a > 1e-9 and cd_b < -1e-9
                 or cd_a < -1e-9 and cd_b > 1e-9)):
        return True
    return ((abs(ab_c) <= 1e-9 and _on(a, b, c))
            or (abs(ab_d) <= 1e-9 and _on(a, b, d))
            or (abs(cd_a) <= 1e-9 and _on(c, d, a))
            or (abs(cd_b) <= 1e-9 and _on(c, d, b)))


def _dist_point_to_bbox(px: float, py: float, bb: tuple) -> float:
    """Exact lower bound on the distance from a point to anything in the box.

    FL-4876 CLEARANCE-PERF-01. Zero when the point is inside the box. Every
    vertex and edge of a ring lies inside that ring's box, so no part of the
    ring can be nearer than this. That makes it a sound pruning bound.
    """
    dx = bb[0] - px if px < bb[0] else (px - bb[2] if px > bb[2] else 0.0)
    dy = bb[1] - py if py < bb[1] else (py - bb[3] if py > bb[3] else 0.0)
    if dx == 0.0:
        return dy
    if dy == 0.0:
        return dx
    return math.hypot(dx, dy)


def _dist_point_to_ring(px: float, py: float, ring: list) -> float:
    return min((_project(px, py, a, b)[2] for a, b in _edges(ring)),
               default=float("inf"))


def _nearest_clear_point_within_sigma(px: float, py: float, clearance_m: float,
                                      index: "CanonicalIndex",
                                      sigma: float,
                                      origin: tuple | None = None
                                      ) -> tuple | None:
    """Closest mass-clear position within the observation's own sigma.

    FL-4876 SPATIAL-A2/A3. Two places asked this question and neither could
    answer it: the ``freestanding`` branch of ``reconcile_surface_decal``
    only tested the point it was handed, and ``attach`` returned
    ``carrier_beyond_tolerance`` debt the moment no carrier fell inside
    ``carrier_association_m`` -- both before anything looked for a clear
    position that the evidence's own uncertainty already permits.

    Same discipline as the chainage sweep: a quarter-metre lattice anchored
    on the observation, ordered deterministically by distance then by offset,
    accepting only a candidate within sigma. Sigma is the sole budget; no
    association tolerance is consulted, because a tolerance describes which
    host an object may be attributed to, not how far evidence may move.

    ``origin`` is the position the sigma budget is measured from -- the
    feature's *original* observation, not wherever a previous stage left it.
    FL-4876 FREESTAND-01: without it, a feature an earlier stage already
    moved could spend a second full sigma here, so two stages each staying
    "within sigma" would sum to 2 sigma of total displacement that no single
    piece of evidence ever licensed. The lattice is therefore anchored on the
    origin and ordered by displacement from it, which makes the returned move
    cumulative by construction rather than per-stage.

    Returns ``(x, y, total_move_from_origin_m)`` or None.
    """
    if sigma is None or sigma <= 0.0:
        return None
    ox, oy = (px, py) if origin is None else (float(origin[0]),
                                              float(origin[1]))
    if _clear_of_buildings(px, py, clearance_m, index):
        held = math.hypot(px - ox, py - oy)
        # A position already spent past its own budget is not made lawful by
        # being clear; it is debt, and returning None hands it back as debt.
        return (px, py, held) if held <= sigma + 1e-9 else None
    step = 0.25
    span = int(math.floor(sigma / step))
    candidates = []
    for ix in range(-span, span + 1):
        for iy in range(-span, span + 1):
            dx, dy = ix * step, iy * step
            move = math.hypot(dx, dy)
            if move > sigma + 1e-9:
                continue
            candidates.append((round(move, 6), ix, iy))
    for move, ix, iy in sorted(candidates):
        cx, cy = ox + ix * step, oy + iy * step
        if _clear_of_buildings(cx, cy, clearance_m, index):
            return (cx, cy, move)
    return None


def _observation_origin(feature: dict, record: dict) -> tuple | None:
    """The originally observed position a freestanding record started from.

    A record that has already been reconciled records where it came from in
    ``source_E``/``source_N``; that is the origin, and it survives further
    moves. Otherwise the feature's own geometry is the origin: a point
    directly, an area through its centroid.
    """
    if record.get("source_E") is not None \
            and record.get("source_N") is not None:
        return (float(record["source_E"]), float(record["source_N"]))
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates")
    if not isinstance(coords, list) or not coords:
        return None
    if geom.get("kind") == "point" and len(coords) >= 2:
        return (float(coords[0]), float(coords[1]))
    if geom.get("kind") == "polygon":
        return _centroid(_closed_ring(coords))
    return None


def _reconcile_chainage_out_of_masses(
        feature: dict, record: dict, carrier: tuple,
        index: "CanonicalIndex", clearance_m: float = 0.0,
        conflict_name: str = "building_mass_conflict") -> dict:
    """Keep observed chainage but repair an impossible lateral claim.

    Mapillary point geometry is an uncertain sighting, not an authority to
    bury a street object in a canonical building.  Search the same carrier
    cross-section for the closest mass-clear point within the observation's
    own sigma.  Both sides are eligible because a single-frame point does not
    establish road side reliably.  If the evidence supplies no movement
    budget, or no clear point exists within it, preserve the contradiction as
    explicit spatial debt.
    """
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    px, py = float(coords[0]), float(coords[1])
    current_side = record.get("side") if record.get("side") in ("L", "R") \
        else "R"
    current_sign = 1.0 if current_side == "R" else -1.0
    _, _, _, q, bearing = carrier
    nx = math.cos(math.radians(bearing + 90.0))
    ny = math.sin(math.radians(bearing + 90.0))
    current_lateral = float(record.get("lateral_offset_m") or 0.0)
    current_x = q[0] + nx * current_sign * current_lateral
    current_y = q[1] + ny * current_sign * current_lateral
    if _clear_of_buildings(current_x, current_y, clearance_m, index):
        return record
    sigma = _geometry_sigma_m(feature)
    if sigma is None:
        return _debt("carrier_chainage",
                     f"{conflict_name}:no_geometry_uncertainty", None)
    source_lateral = float(record.get("lateral_offset_m") or 0.0)
    source_side = current_side
    # FL-4876 SPATIAL-A1.  The sweep is centred on the observation, not on
    # the carrier centreline.
    #
    # It used to enumerate absolute laterals 0 .. sigma measured from the
    # centreline, and then accept a candidate only when it lay within sigma
    # of the observed point.  Those two constraints are disjoint by
    # construction as soon as the object's own lateral offset exceeds sigma:
    # every candidate is then further than sigma from the observation, so the
    # function returned beyond_geometry_uncertainty whatever the surrounding
    # geometry looked like.  Measured on this lane, the shipped candidate set
    # accepted nothing in 12 of 12 cases, at source laterals of 2.59-16.17 m
    # against a 5.0 m sigma.  The docstring above already described the
    # intended behaviour -- "the closest mass-clear point within the
    # observation's own sigma" -- so this is the enumeration catching up with
    # the contract, not a new rule.
    #
    # Nothing here relaxes a budget: sigma is unchanged, the clearance test is
    # unchanged, and the acceptance test below is unchanged.  Only the region
    # searched is corrected to cover the region acceptance can admit.  The
    # quarter-metre lattice stays anchored on the centreline, so a candidate
    # that the old sweep could reach is still enumerated at the same offset.
    signed_source = current_sign * source_lateral
    lattice_lo = signed_source - sigma
    lattice_hi = signed_source + sigma
    first_step = int(math.ceil((lattice_lo - 1e-9) / 0.25))
    last_step = int(math.floor((lattice_hi + 1e-9) / 0.25))
    signed_offsets = [round(step * 0.25, 3)
                      for step in range(first_step, last_step + 1)]
    # Both exact endpoints, so the sigma boundary itself remains reachable.
    for endpoint in (lattice_lo, lattice_hi):
        if round(endpoint, 3) not in signed_offsets:
            signed_offsets.append(round(endpoint, 3))
    candidates = []
    for signed_lateral in sorted(signed_offsets):
        side = "R" if signed_lateral >= 0.0 else "L"
        lateral = round(abs(signed_lateral), 3)
        cx = q[0] + nx * signed_lateral
        cy = q[1] + ny * signed_lateral
        if not _clear_of_buildings(cx, cy, clearance_m, index):
            continue
        movement = math.hypot(cx - px, cy - py)
        if movement <= sigma + 1e-9:
            candidates.append((movement, side != source_side,
                               lateral, side, cx, cy))
    if not candidates:
        return _debt(
            "carrier_chainage",
            f"{conflict_name}:beyond_geometry_uncertainty",
            round(sigma, 3))
    movement, _, lateral, side, _, _ = min(candidates)
    resolved = dict(record)
    resolved["source_lateral_offset_m"] = round(source_lateral, 3)
    resolved["source_side"] = source_side
    resolved["lateral_offset_m"] = round(lateral, 3)
    resolved["side"] = side
    resolved["spatial_reconciliation"] = \
        f"{conflict_name}_within_geometry_uncertainty"
    resolved["spatial_reconciliation_m"] = round(movement, 3)
    resolved["geometry_uncertainty_sigma_m"] = round(sigma, 3)
    if clearance_m > 0.0:
        resolved["footprint_clearance_m"] = round(clearance_m, 3)
    return resolved


def reconcile_mesh_fixture_footprint(feature: dict, record: dict,
                                     index: "CanonicalIndex",
                                     longitudinal_m: float,
                                     lateral_m: float) -> dict:
    """Reconcile a fixture's real oriented footprint against canonical masses.

    FL-4876 FOOTPRINT-01. The sibling of ``reconcile_surface_decal``, and it
    exists for the same stated reason: *"a point decal is a physical painted
    disk, not a zero-area centre... reconcile the complete footprint after
    its family/diameter is known."* A street lamp is not a zero-area centre
    either. ``attach`` places fixtures with a clearance of 0.0 because it
    does not know which asset will land there, and the planner resolves the
    template forty-five lines before it calls ``attach``, so the extent was
    available the whole time and nobody asked for it.

    Measured on the Numabe lane before this existed: 38 placements whose
    declared footprint overlapped a canonical mass that was not their host.

    The asset's own host is never a conflict -- a facade fixture is mounted
    on its building. Movement is bounded by the observation's own sigma,
    measured cumulatively from the original observation, and a row with no
    lawful position keeps its contradiction as explicit debt rather than
    being emitted into a wall.
    """
    if record.get("record_type") != "attachment":
        return record
    anchor = record.get("anchor_kind")
    if anchor not in ("carrier_chainage", "freestanding"):
        # building_face is mounted on its host by construction, and
        # carrier_span/terrain_mass are not point assets.
        return record
    try:
        pose = resolve(record, index)
    except (KeyError, ValueError, IndexError, TypeError):
        return record
    host = record.get("canonical_building_id")
    excluded = (host,) if host else ()
    if not footprint_mass_conflicts(pose["E"], pose["N"], pose["yaw_deg"],
                                    longitudinal_m, lateral_m, index):
        return record
    # The circumscribing radius is the clearance a yaw-free point search can
    # promise, because the sweep moves the anchor and the asset turns with
    # the carrier. It is the conservative bound, never a widened one.
    radius = 0.5 * math.hypot(float(longitudinal_m), float(lateral_m))
    if anchor == "carrier_chainage":
        carrier_id = record.get("canonical_carrier_id")
        carrier_feature = index.by_id.get(carrier_id)
        if carrier_feature is None:
            return record
        poly = (carrier_feature.get("geometry") or {}).get("coordinates") or []
        if len(poly) < 2:
            return record
        q, bearing = _point_at_chainage(poly, float(record["chainage_m"]))
        carrier = (0.0, carrier_id, float(record["chainage_m"]), q, bearing)
        return _reconcile_chainage_out_of_masses(
            feature, record, carrier, index, clearance_m=radius,
            conflict_name="mesh_fixture_footprint_mass_conflict")
    sigma = _geometry_sigma_m(feature)
    if sigma is None:
        return _debt("freestanding",
                     "mesh_fixture_footprint_mass_conflict:"
                     "no_geometry_uncertainty", None)
    origin = _observation_origin(feature, record) or (pose["E"], pose["N"])
    found = _nearest_clear_point_within_sigma(
        pose["E"], pose["N"], radius, index, sigma, origin=origin)
    if found is None:
        return _debt("freestanding",
                     "mesh_fixture_footprint_mass_conflict:"
                     "beyond_geometry_uncertainty", round(sigma, 3))
    cx, cy, move = found
    resolved_record = dict(record)
    resolved_record["source_E"], resolved_record["source_N"] = origin
    resolved_record["E"], resolved_record["N"] = cx, cy
    resolved_record["spatial_reconciliation"] = \
        "mesh_fixture_footprint_mass_conflict_within_geometry_uncertainty"
    resolved_record["spatial_reconciliation_m"] = round(move, 3)
    resolved_record["spatial_reconciliation_basis"] = \
        "cumulative_displacement_from_observation"
    resolved_record["geometry_uncertainty_m"] = round(sigma, 3)
    resolved_record["footprint_clearance_m"] = round(radius, 3)
    return resolved_record


def reconcile_surface_decal(feature: dict, record: dict,
                            index: "CanonicalIndex",
                            decal_family: str | None = None) -> dict:
    """Reconcile the complete circular point-decal footprint against masses."""
    geom = feature.get("geometry") or {}
    if geom.get("kind") != "point" or record.get("record_type") != "attachment":
        return record
    semantic = feature.get("semantic_class")
    key = "fixture" if semantic == "fixture" else (decal_family or "other")
    radius = SURFACE_DECAL_DIAMETER_M.get(
        key, SURFACE_DECAL_DIAMETER_M["other"]) / 2.0
    if record.get("anchor_kind") == "carrier_chainage":
        carrier_id = record.get("canonical_carrier_id")
        carrier_feature = index.by_id.get(carrier_id)
        if carrier_feature is None:
            return _debt("carrier_chainage",
                         "surface_decal_footprint:missing_carrier", None)
        poly = (carrier_feature.get("geometry") or {}).get("coordinates") or []
        if len(poly) < 2:
            return _debt("carrier_chainage",
                         "surface_decal_footprint:degenerate_carrier", None)
        q, bearing = _point_at_chainage(poly, float(record["chainage_m"]))
        carrier = (0.0, carrier_id, float(record["chainage_m"]), q, bearing)
        return _reconcile_chainage_out_of_masses(
            feature, record, carrier, index, clearance_m=radius,
            conflict_name="surface_decal_footprint_mass_conflict")
    if record.get("anchor_kind") == "freestanding":
        pose = resolve(record, index)
        px, py = float(pose["E"]), float(pose["N"])
        if _clear_of_buildings(px, py, radius, index):
            return record
        # FL-4876 SPATIAL-A3. This branch used to test the one position it
        # was handed and return debt. The chainage branch above searches;
        # this one did not, so a decal whose centre is outside every mass and
        # whose footprint clears 1.0 m away was refused only because nothing
        # looked. Same sigma, same lattice, same acceptance rule.
        sigma = _geometry_sigma_m(feature)
        if sigma is None:
            return _debt(
                "freestanding",
                "surface_decal_footprint_mass_conflict:no_geometry_uncertainty",
                None)
        # FL-4876 FREESTAND-01. The budget is spent against the *original*
        # observation. This used to pass the current resolved position as
        # both search centre and budget origin, so a feature an earlier stage
        # had already moved would have been allowed a second full sigma.
        origin = _observation_origin(feature, record) or (px, py)
        found = _nearest_clear_point_within_sigma(px, py, radius, index, sigma,
                                                  origin=origin)
        if found is None:
            return _debt(
                "freestanding",
                "surface_decal_footprint_mass_conflict:"
                "beyond_geometry_uncertainty",
                round(sigma, 3))
        cx, cy, move = found
        resolved_record = dict(record)
        resolved_record["source_E"] = origin[0]
        resolved_record["source_N"] = origin[1]
        resolved_record["E"], resolved_record["N"] = cx, cy
        resolved_record["spatial_reconciliation"] = (
            "surface_decal_footprint_mass_conflict_within_geometry_uncertainty")
        resolved_record["spatial_reconciliation_m"] = round(move, 3)
        resolved_record["spatial_reconciliation_basis"] = \
            "cumulative_displacement_from_observation"
        resolved_record["geometry_uncertainty_m"] = round(sigma, 3)
        resolved_record["footprint_clearance_m"] = round(radius, 3)
        return resolved_record
    return record


def _seg_seg_dist(a: list, b: list, c: list, d: list) -> float:
    """Exact distance between segments ab and cd.

    FL-4876 FREESTAND-01. The endpoint-projection minimum below is the right
    answer only for segments that do not cross; for a crossing pair the true
    distance is 0 and that formula returns a positive number (measured: 1.414
    for the unit diagonals). ``world_surfaces._seg_seg_dist`` already tested
    intersection first -- two implementations of one question, and this was
    the one that was wrong.
    """
    if _segments_intersect(a, b, c, d):
        return 0.0

    def _pt(q, s, t):
        dx, dy = t[0] - s[0], t[1] - s[1]
        den = dx * dx + dy * dy
        k = max(0.0, min(1.0, ((q[0] - s[0]) * dx + (q[1] - s[1]) * dy)
                            / den)) if den > 0 else 0.0
        return math.dist(q, (s[0] + k * dx, s[1] + k * dy))
    return min(_pt(a, c, d), _pt(b, c, d), _pt(c, a, b), _pt(d, a, b))


def polygons_overlap(first: list, second: list) -> bool:
    """Topological overlap for simple canonical rings, including touching."""
    if not first or not second:
        return False
    first_points = first[:-1] if len(first) > 1 and first[0] == first[-1] \
        else first
    second_points = second[:-1] if len(second) > 1 and second[0] == second[-1] \
        else second
    if any(_point_in_ring(float(p[0]), float(p[1]), second)
           for p in first_points if len(p) >= 2):
        return True
    if any(_point_in_ring(float(p[0]), float(p[1]), first)
           for p in second_points if len(p) >= 2):
        return True
    return any(_segments_intersect(a, b, c, d)
               for a, b in _edges(first) for c, d in _edges(second))


def _face_route_distance(a: list, b: list,
                         route_xy: list | None) -> float | None:
    """Min distance from edge ab to the route polyline, or None."""
    if not route_xy or len(route_xy) < 2:
        return None
    segs = list(zip(route_xy, route_xy[1:]))
    return min(_seg_seg_dist(a, b, c, d) for c, d in segs)


def _face_carrier_distance(a: list, b: list, index: CanonicalIndex):
    """(distance, carrier_id) from edge midpoint to the nearest carrier."""
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    hit = index.nearest_carrier(mx, my)
    if hit is None:
        return None, None
    return hit[0], hit[1]


def attach_building_facade(feature: dict, index: CanonicalIndex,
                           rules: dict) -> dict:
    """Select one exposed, route-facing face for a building facade.

    ``attach()`` deliberately returns ``terrain_mass`` for a building because
    that is its primary representation.  A facade is a second representation
    of the same canonical building and must ask for a face explicitly rather
    than changing or replacing mass ownership.
    """
    fid = str(feature.get("feature_id") or "?")
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates")
    if feature.get("semantic_class") != "building_footprint" \
            or geom.get("kind") != "polygon" \
            or not isinstance(coords, list) or len(coords) < 3:
        return _debt("building_face", "missing_host:not_building_polygon", None)

    ring = _closed_ring(coords)
    cx, cy = _centroid(ring)
    offset = _outward_offset_m(rules)
    candidates = []
    for face_index, (a, b) in enumerate(_edges(ring)):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        if length <= 1e-9:
            continue
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        nx, ny = -uy, ux
        if nx * (mx - cx) + ny * (my - cy) < 0:
            nx, ny = -nx, -ny
        anchor = (mx + nx * offset, my + ny * offset)
        conflicts = [
            other_id for other_id, other_ring in index.buildings
            if other_id != fid and _point_in_ring(
                anchor[0], anchor[1], other_ring)
        ]
        if conflicts:
            continue
        route_distance = _face_route_distance(a, b, index.route_xy)
        carrier_distance, carrier_id = _face_carrier_distance(a, b, index)
        score = route_distance if route_distance is not None else (
            carrier_distance if carrier_distance is not None else float("inf"))
        candidates.append((score, -length, face_index, a, b,
                           route_distance, carrier_distance, carrier_id))

    if not candidates:
        return _debt(
            "building_face", "missing_host:no_exposed_building_face", None)
    (score, _neg_length, face_index, _a, _b, route_distance,
     carrier_distance, carrier_id) = min(candidates)
    reason = ("building_route_facing_exposed_face"
              if route_distance is not None
              else "building_carrier_facing_exposed_face")
    return {
        "record_type": "attachment",
        "anchor_kind": "building_face",
        "canonical_building_id": fid,
        "canonical_face_id": f"{fid}#f{face_index}",
        "face_index": face_index,
        "along_face_fraction": 0.5,
        "height_m": 0.0,
        "outward_offset_m": offset,
        "orientation_basis": "outward_normal_of_canonical_face",
        "face_selection_reason": reason,
        "route_distance_m": (round(route_distance, 3)
                             if route_distance is not None else None),
        "carrier_distance_m": (round(carrier_distance, 3)
                               if carrier_distance is not None else None),
        "nearest_carrier_id": carrier_id,
        "selection_score_m": round(score, 3) if score != float("inf") else None,
        "residual_m": 0.0,
    }


def attach(feature: dict, index: CanonicalIndex, rules: dict) -> dict:
    """Derive an attachment record from canonical geometry only.

    Returns an attachment record, or a spatial_reconciliation_debt dict
    naming the missing host or the breached tolerance with its residual.
    """
    tol = _tolerances(rules)
    fid = feature.get("feature_id", "?")
    cls = feature.get("semantic_class")
    geom = feature.get("geometry", {})
    kind_geo = geom.get("kind")
    coords = geom.get("coordinates")

    if cls == "building_footprint" and kind_geo == "polygon" \
            and isinstance(coords, list) and len(coords) >= 3:
        return {"record_type": "attachment", "anchor_kind": "terrain_mass",
                "canonical_mass_id": fid}

    if kind_geo == "polygon" and isinstance(coords, list) and coords:
        # Area feature that is not a mass (lots, yards): ground-placed,
        # no face or carrier dependency.
        return attach_freestanding(feature, index)

    if kind_geo == "point" and isinstance(coords, list) \
            and len(coords) >= 2:
        px, py = float(coords[0]), float(coords[1])
        # Sites and building-mounted signs belong to masses (face first);
        # ground fixtures and regulatory hardware belong to carriers.  A
        # contained sign observation is facade evidence, not permission to
        # stand a sign mesh inside the extrusion.
        # FL-4876 SPATIAL-A4. A station entrance is a door on a building
        # face, so it resolves against a face before a street carrier.
        #
        # It was absent from this set, so both entrance nodes took the
        # carrier path and were then refused for sitting inside a building --
        # which they do, because a door is in a wall. The correct host face
        # existed the whole time, 0.694 m and 0.968 m away against a 6.0 m
        # face_resolve_m. A carrier was selected when a face was available:
        # a selection defect, not a tolerance problem.
        #
        # Measured on this lane, each of the two entrance nodes lies inside
        # exactly one of the two PLATEAU masses that sit 0.13 m and 0.105 m
        # from the railway boundary -- which is what identifies those masses
        # as the station structure.
        face_first = cls in {"place", "sign", "station_entrance"}
        face = index.nearest_face(px, py)
        carrier = index.nearest_carrier(px, py)

        def _face_record():
            _, bfid, edge_index, a, b, q = face
            host_ring = next(r for f, r in index.buildings if f == bfid)
            cx, cy = _centroid(host_ring)
            assert 0 <= edge_index < len(_edges(host_ring)), (
                bfid, edge_index)
            seg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
            ux, uy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
            mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
            nx, ny = -uy, ux
            if nx * (mx - cx) + ny * (my - cy) < 0:
                nx, ny = -nx, -ny
            along = ((q[0] - a[0]) * ux + (q[1] - a[1]) * uy) / seg
            return {"record_type": "attachment",
                    "anchor_kind": "building_face",
                    "canonical_building_id": bfid,
                    "canonical_face_id": "%s#f%d" % (bfid, edge_index),
                    "face_index": edge_index,
                    "along_face_fraction": round(min(1.0, max(0.0, along)),
                                                 6),
                    "height_m": 0.0,
                    "outward_offset_m": _outward_offset_m(rules)
                    if face_first else 0.0,
                    "orientation_basis": "outward_normal_of_canonical_face"}

        def _chainage_record():
            dist, cfid, chainage, _, bearing = carrier
            side = "R" if ((px - carrier[3][0]) * math.cos(
                math.radians(bearing + 90.0)) + (py - carrier[3][1])
                * math.sin(math.radians(bearing + 90.0))) >= 0 else "L"
            return {"record_type": "attachment",
                    "anchor_kind": "carrier_chainage",
                    "canonical_carrier_id": cfid,
                    "chainage_m": round(chainage, 3),
                    "lateral_offset_m": round(dist, 3),
                    "side": side,
                    "orientation_basis": "carrier_bearing_at_chainage"}

        if cls == "road_marking" and carrier is not None and carrier[0] <= \
                tol["carrier_association_m"]:
            # Identity and chainage come from the observation; canonical
            # masses constrain its uncertain lateral claim.  Full paint-footprint
            # clearance is applied after the representation family is known.
            return _reconcile_chainage_out_of_masses(
                feature, _chainage_record(), carrier, index)

        if face_first:
            # REVIEW v4: a place node inside its host is the normal case
            # for a large store. The host is the containing ring; the face
            # is the one nearest the pedestrian route, else nearest a
            # road/sidewalk carrier. Node-to-face distance is provenance,
            # not a residual: resolved records carry residual 0 by rule.
            # The face tolerance applies only outside every ring.
            contained = index.containing_building(px, py)
            if contained is not None:
                bfid, host_ring = contained
                host_cx, host_cy = _centroid(host_ring)
                scored = []
                blocked = []
                for i, (a, b) in enumerate(_edges(host_ring)):
                    if cls == "sign":
                        q, _, face_dist = _project(px, py, a, b)
                        candidate = (face_dist, i, a, b,
                                     "containing_ring_nearest_face")
                    else:
                        route_dist = _face_route_distance(a, b, index.route_xy)
                        if route_dist is not None:
                            candidate = (route_dist, i, a, b,
                                         "containing_ring_route_face")
                        else:
                            carrier_dist, _ = _face_carrier_distance(
                                a, b, index)
                            candidate = (
                                carrier_dist
                                if carrier_dist is not None else float("inf"),
                                i, a, b, "containing_ring_carrier_face")
                    # A selected facade anchor cannot lawfully sit inside a
                    # second emitted mass.  Prefer the nearest route-facing
                    # edge whose midpoint-plus-outward-offset is topologically
                    # clear; fall back to the full ranking only when the
                    # source masses leave no clear edge at all.
                    seg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
                    ux, uy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
                    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
                    nx, ny = -uy, ux
                    if nx * (mx - host_cx) + ny * (my - host_cy) < 0:
                        nx, ny = -nx, -ny
                    offset = _outward_offset_m(rules)
                    base = q if cls == "sign" else (mx, my)
                    anchor = (base[0] + nx * offset,
                              base[1] + ny * offset)
                    target = blocked if any(
                        other_id != bfid and _point_in_ring(
                            anchor[0], anchor[1], other_ring)
                        for other_id, other_ring in index.buildings) else scored
                    target.append(candidate)
                if not scored:
                    scored = blocked
                scored.sort(key=lambda s: (s[0], s[1]))
                best_dist, edge_index, a, b, reason = scored[0]
                assert 0 <= edge_index < len(_edges(host_ring)), (
                    bfid, edge_index)
                seg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
                ux, uy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
                mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
                nx, ny = -uy, ux
                if nx * (mx - host_cx) + ny * (my - host_cy) < 0:
                    nx, ny = -nx, -ny
                if cls == "place":
                    # A POI point proves tenancy, not lateral storefront
                    # position. Route/carrier evidence owns its face; absent
                    # a measured facade position, use the face midpoint.
                    along = 0.5
                else:
                    # A sign observation is itself approximate facade-position
                    # evidence, so preserve its along-face coordinate while
                    # snapping it out of the extrusion.
                    q, _, _ = _project(px, py, a, b)
                    along = ((q[0] - a[0]) * ux + (q[1] - a[1]) * uy) / seg
                return {"record_type": "attachment",
                        "anchor_kind": "building_face",
                        "canonical_building_id": bfid,
                        "canonical_face_id": "%s#f%d" % (bfid, edge_index),
                        "face_index": edge_index,
                        "along_face_fraction": round(
                            min(1.0, max(0.0, along)), 6),
                        "height_m": 0.0,
                        "outward_offset_m": _outward_offset_m(rules),
                        "orientation_basis":
                            "outward_normal_of_canonical_face",
                        "face_selection_reason": reason,
                        "route_distance_m": round(best_dist, 3)
                        if best_dist != float("inf") else None,
                        "residual_m": 0.0}
            if face is not None and face[0] <= tol["face_resolve_m"]:
                record = _face_record()
                record["face_selection_reason"] = \
                    "nearest_face_within_tolerance"
                route_dist = _face_route_distance(
                    face[3], face[4], index.route_xy)
                record["route_distance_m"] = round(route_dist, 3) \
                    if route_dist is not None else None
                record["residual_m"] = 0.0
                return record
        if carrier is not None and carrier[0] <= \
                tol["carrier_association_m"]:
            return _reconcile_chainage_out_of_masses(
                feature, _chainage_record(), carrier, index)
        if carrier is not None:
            # FL-4876 SPATIAL-A2. No carrier inside carrier_association_m is
            # not the same fact as no lawful position. This used to return
            # debt immediately, before anything asked whether the object
            # stands clear of every mass -- which is all a freestanding
            # attachment needs. On this lane the three subjects in this class
            # miss the 12.0 m association tolerance by 0.29, 0.43 and 3.08 m,
            # and their clearing moves are 2.75, 3.00 and 1.00 m, inside the
            # same 5.0 m sigma the chainage path already accepts.
            #
            # The association tolerance is untouched: it still decides that
            # no carrier may host these. What changes is that a standalone
            # object is allowed to be standalone.
            sigma = _geometry_sigma_m(feature)
            found = (_nearest_clear_point_within_sigma(px, py, 0.0, index,
                                                       sigma)
                     if sigma is not None else None)
            if found is not None:
                cx, cy, move = found
                record = {
                    "record_type": "attachment",
                    "anchor_kind": "freestanding",
                    "E": cx, "N": cy,
                    # FL-4876 FREESTAND-01. These two fields are not
                    # decoration. Every other freestanding record carries
                    # them (attach_freestanding), and resolve() reads
                    # canonical_point to find the feature. Omitting them
                    # made a record that looked attached and raised
                    # KeyError in the one consumer that had to resolve it:
                    # a vocabulary the producer declared and the consumer
                    # did not share, which is the defect this whole repair
                    # series is about.
                    "canonical_point": fid,
                    "ground_owner": index.containing_terrain(cx, cy),
                    "orientation_basis": "unoriented_point_observation",
                    "residual_m": 0.0,
                    "nearest_carrier_id": carrier[1],
                    "nearest_carrier_distance_m": round(carrier[0], 3),
                    "carrier_association_tolerance_m":
                        tol["carrier_association_m"],
                    "host_selection_reason":
                        "no_carrier_within_association_tolerance;"
                        "_standalone_position_clear_of_all_masses",
                }
                if move > 0.0:
                    record["source_E"], record["source_N"] = px, py
                    record["spatial_reconciliation"] = \
                        "carrier_beyond_tolerance_within_geometry_uncertainty"
                    record["spatial_reconciliation_m"] = round(move, 3)
                    record["geometry_uncertainty_m"] = round(sigma, 3)
                return record
            return _debt("carrier_chainage", "carrier_beyond_tolerance",
                         round(carrier[0], 3))
        return _debt("freestanding", "missing_host:no_carrier", None)

    if kind_geo == "polyline" and isinstance(coords, list) \
            and len(coords) >= 2:
        length = _poly_length(coords)
        if length <= 1e-9:
            return _debt("carrier_span", "degenerate_geometry:zero_length",
                         0.0)
        return {"record_type": "attachment", "anchor_kind": "carrier_span",
                "canonical_carrier_id": fid, "start_chainage_m": 0.0,
                "end_chainage_m": round(length, 3),
                "lateral_offset_m": 0.0}

    return _debt("freestanding",
                 "degenerate_geometry:unusable_%s" % kind_geo,
                 None)


def attach_freestanding(feature: dict, index: CanonicalIndex,
                        self_grounded: bool = False) -> dict:
    """Carrier-independent anchor: labels and self-grounded terrain patches."""
    fid = feature.get("feature_id", "?")
    geom = feature.get("geometry", {})
    coords = geom.get("coordinates")
    if geom.get("kind") == "point" and isinstance(coords, list) \
            and len(coords) >= 2:
        px, py = float(coords[0]), float(coords[1])
    elif geom.get("kind") == "polygon" and isinstance(coords, list) \
            and coords:
        cx, cy = _centroid(_closed_ring(coords))
        px, py = cx, cy
    else:
        return _debt("freestanding", "degenerate_geometry:unusable_point",
                     None)
    if self_grounded:
        ground, nearest, offset = fid, None, 0.0
    else:
        ground = index.containing_terrain(px, py)
        carrier = index.nearest_carrier(px, py)
        nearest = carrier[1] if carrier else None
        offset = round(carrier[0], 3) if carrier else None
    return {"record_type": "attachment", "anchor_kind": "freestanding",
            "canonical_point": fid, "ground_owner": ground,
            "nearest_carrier_id": nearest, "offset_m": offset}


def face_heading(index: CanonicalIndex, building_id: str,
                 face_index: int) -> float:
    """Edge bearing of one canonical face (degrees atan2(dN, dE))."""
    ring = [r for f, r in index.buildings if f == building_id][0]
    a, b = _edges(ring)[face_index]
    return round(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])), 3)


def resolve_span_endpoints(record: dict, index: CanonicalIndex) -> dict:
    """Resolve a carrier span without collapsing it to one chord offset.

    Offset carriers bend.  One midpoint normal cannot reconstruct both
    authored endpoints and can move an already mass-clipped run back into a
    building.  New records therefore carry endpoint-specific side/lateral
    values; older records fall back to their shared values.
    """
    poly = dict(index.by_id[record["canonical_carrier_id"]]
                ["geometry"])["coordinates"]
    p0, bearing0 = _point_at_chainage(
        poly, float(record["start_chainage_m"]))
    p1, bearing1 = _point_at_chainage(
        poly, float(record["end_chainage_m"]))

    def _offset(point, bearing, prefix):
        side = record.get(f"{prefix}_side", record.get("side", "R"))
        off = float(record.get(
            f"{prefix}_lateral_offset_m",
            record.get("lateral_offset_m", 0.0)))
        nx = math.cos(math.radians(bearing + 90.0))
        ny = math.sin(math.radians(bearing + 90.0))
        if side == "L":
            nx, ny = -nx, -ny
        return (point[0] + nx * off, point[1] + ny * off)

    q0 = _offset(p0, bearing0, "start")
    q1 = _offset(p1, bearing1, "end")
    yaw = math.degrees(math.atan2(q1[1] - q0[1], q1[0] - q0[0])) \
        if math.dist(q0, q1) > 0 else bearing0
    return {
        "E0": round(q0[0], 3), "N0": round(q0[1], 3),
        "E1": round(q1[0], 3), "N1": round(q1[1], 3),
        "E": round((q0[0] + q1[0]) / 2.0, 3),
        "N": round((q0[1] + q1[1]) / 2.0, 3),
        "yaw_deg": round(yaw, 3),
    }


def resolve(record: dict, index: CanonicalIndex) -> dict:
    """Absolute world transform {E, N, yaw_deg} from current geometry."""
    kind = record["anchor_kind"]
    if kind == "terrain_mass":
        ring = dict(index.by_id[record["canonical_mass_id"]]
                    ["geometry"])["coordinates"]
        cx, cy = _centroid(_closed_ring(ring))
        return {"E": round(cx, 3), "N": round(cy, 3), "yaw_deg": 0.0}
    if kind == "building_face":
        bfid = record["canonical_building_id"]
        ring = [r for f, r in index.buildings if f == bfid][0]
        edges = _edges(ring)
        if not 0 <= record["face_index"] < len(edges):
            raise ValueError(
                "canonical face %s#f%d out of range on a %d-edge ring "
                "(ring switch); re-attach, do not invent an index"
                % (bfid, record["face_index"], len(edges)))
        a, b = edges[record["face_index"]]
        t = min(1.0, max(0.0, float(record["along_face_fraction"])))
        px, py = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
        seg = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        ux, uy = (b[0] - a[0]) / seg, (b[1] - a[1]) / seg
        cx, cy = _centroid(ring)
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        nx, ny = -uy, ux
        if nx * (mx - cx) + ny * (my - cy) < 0:
            nx, ny = -nx, -ny
        off = float(record["outward_offset_m"])
        # FL-4876 FACE-FIT-01: the face's own end points (outset like the pose) travel with the pose so the
        # lowering can keep a wall panel inside its face; an 8 m roller shutter on a 3 m face hung 2.5 m
        # past each corner (build_reg_75: 34 wall pieces past a face end).
        return {"E": round(px + nx * off, 3), "N": round(py + ny * off, 3),
                "yaw_deg": round(math.degrees(math.atan2(ny, nx)), 3),
                "face_end_a": [round(a[0] + nx * off, 3), round(a[1] + ny * off, 3)],
                "face_end_b": [round(b[0] + nx * off, 3), round(b[1] + ny * off, 3)]}
    if kind == "carrier_chainage":
        poly = dict(index.by_id[record["canonical_carrier_id"]]
                    ["geometry"])["coordinates"]
        pt, bearing = _point_at_chainage(poly, float(record["chainage_m"]))
        nx, ny = math.cos(math.radians(bearing + 90.0)), math.sin(
            math.radians(bearing + 90.0))
        if record["side"] == "L":
            nx, ny = -nx, -ny
        off = float(record["lateral_offset_m"])
        # FL-4876 FACING-01. Where the owner of the observation recorded which
        # way the object faces (authored fixtures carry a manual_review
        # orientation_basis), the record carries it as authored_yaw_deg and it
        # is the pose yaw. The carrier bearing is still returned, as
        # carrier_bearing_deg, for callers that need the carrier direction.
        # Before this, ~20 platform and track signs were turned 90-180 deg
        # off their authored facing because this branch always answered with
        # the carrier bearing.
        pose = {"E": round(pt[0] + nx * off, 3),
                "N": round(pt[1] + ny * off, 3),
                "yaw_deg": round(bearing, 3)}
        if record.get("authored_yaw_deg") is not None:
            pose["yaw_deg"] = round(float(record["authored_yaw_deg"]), 3)
            pose["carrier_bearing_deg"] = round(bearing, 3)
        return pose
    if kind == "carrier_span":
        span = resolve_span_endpoints(record, index)
        return {key: span[key] for key in ("E", "N", "yaw_deg")}
    if kind == "freestanding":
        # FL-4876 FREESTAND-01. A reconciled record carries the position the
        # sweep chose, and re-deriving from the raw feature geometry threw
        # that choice away -- putting the object back inside the mass it was
        # moved out of. Measured on this lane before the repair: one decal
        # record whose 0.901 m reconciliation resolve() silently discarded.
        # An explicit position is the record's own decision and wins.
        if record.get("E") is not None and record.get("N") is not None:
            px, py = float(record["E"]), float(record["N"])
        else:
            feat = index.by_id[record["canonical_point"]]
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates")
            if geom.get("kind") == "point":
                px, py = float(coords[0]), float(coords[1])
            else:
                # Area to point is the centroid of the closed ring. Written
                # down rather than assumed: a bicycle-parking or yard
                # polygon has no authored anchor node, and the centroid is
                # the only reduction the canonical geometry itself supports.
                px, py = _centroid(_closed_ring(coords))
        # Yaw source, written down: a freestanding object carries no authored
        # orientation, so it takes the bearing of the nearest canonical
        # carrier when the record names one, and 0.0 when it does not. This
        # is the pre-existing rule in this branch and is unchanged.
        carrier_id = record.get("nearest_carrier_id")
        if record.get("authored_yaw_deg") is not None:
            # FL-4876 FACING-01: the owner's recorded facing (or the
            # registered yaw kept when a face snap was refused) is the pose.
            yaw = float(record["authored_yaw_deg"])
        elif carrier_id is not None and carrier_id in index.by_id:
            nearest = index.nearest_carrier(px, py)
            yaw = nearest[4] if nearest else 0.0
        else:
            yaw = 0.0
        return {"E": round(px, 3), "N": round(py, 3),
                "yaw_deg": round(yaw, 3)}
    raise ValueError("unknown anchor_kind %r" % kind)
