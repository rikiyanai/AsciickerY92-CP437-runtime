"""FL-4876 ROADSIDE-CLEAR-01: no standing object on the painted carriageway.

One pass over the merged population plan, before dispatch. Every point
placement attached to a road carrier by ``carrier_chainage`` is resolved
exactly as the dispatcher will resolve it (``resolve_span`` on the canonical
index). When that pose lies on painted carriageway (``world_surfaces``
``point_surfaces``, which unions every carrier's paint) and the module is not a
road-surface item, the attachment's lateral offset and side are moved to the
nearest legal site:

* off the painted carriageway, at the pose and ``CLEAR_M`` further out;
* the declared physical footprint overlaps no canonical mass;
* at most ``MAX_MOVE_M`` from the pose it had.

A placement with no legal site within ``MAX_MOVE_M`` becomes a rule-decided
``intentional_omission`` terminal and is listed in the receipt by id, never
left standing in the road.

This replaces ROADSIDE-SNAP-01's per-carrier median painted edge
(compile_representation_fixture_placements._roadside_lateral). That test
measured one carrier's median half-width, so a fixture past its own carrier's
median edge still stood in a wider stretch or in a crossing carrier's paint:
build_reg_22 measured 43 representation fixtures and 24 observed fixtures on
the carriageway, 34 of them more than 1 m inside it.
"""
from __future__ import annotations

import copy
import math

from scripts.pipeline.canonical_attachment import (
    CanonicalIndex,
    footprint_mass_conflicts,
)
from scripts.pipeline.plan_spatial_provenance import resolve_span

SCHEMA = "y92.roadside_clearance.v1"
CLEAR_M = 0.3
STEP_M = 0.25
MAX_MOVE_M = 8.0
# A standing object is a fixture or custom placement whose declared physical
# envelope rises above this. Road decals and covers (zebra crossing, arrows,
# edge lines, manholes, grates: 0.2 m or less in the pack) belong on the
# asphalt and are left where they are; linear runs are not point objects.
STANDING_MIN_M = 0.25
# "linear" covers point placements the linear lowerer owns -- the observed
# utility poles (build_reg_22: 40 pole tiles on the paint, up to 4.3 m in).
# Runs (placement["run"]) are skipped below; they are not point objects.
STANDING_OWNERS = ("fixtures", "custom", "linear")
# Hung above the road by design, not standing on it.
OVERHEAD_MODULES = frozenset({"wire_span", "overhead_gantry_wire"})
CROSS_SECTION_DOMAINS = frozenset({"road_cross_section"})


def _on_carriageway(surf, e: float, n: float) -> bool:
    s = surf.point_surfaces(e, n)
    return "carriageway" in s or RAIL_ENVELOPE in s


# FL-4876 RAIL-CLEAR-01: nothing stands in the train's path either. Level
# crossing barrier poles, a pole transformer and sign plates were attached to
# the rail centreline at lateral 0 (build_reg_34: 10 instances 0.0 m from a
# train route; the operator saw a pole clip the train). The rail centrelines
# are the same canonical railway_boundary polylines tagged railway=rail that
# compile_classic_train_routes chains into the train routes. Within
# RAIL_CLEAR_M of one (Tokyu 7000 half width ~1.4 m + margin) a standing
# object is as illegal as on the road paint.
RAIL_ENVELOPE = "rail_envelope"
# 2.2 m, not the car half width alone: at 1.7 m (build_reg_35) two road traffic
# signals, a pole transformer and station sign plates were left standing in
# the space between the two tracks (1.74-2.08 m from each centreline, track
# spacing ~3.6-4 m), visibly poles in the track and arms over the train. 2.2 m
# covers the inter-track way and a signal arm's overhang.
RAIL_CLEAR_M = 2.2  # legacy fallback when no vehicle width is supplied
# FL-4876 RAIL-CLEAR-02: the envelope is the vehicle's swept half width plus a
# kinematic/structure margin (the vehicle's own width comes from the train
# asset manifest), around AT-GRADE rail only -- an elevated viaduct
# (bridge=*, layer>0) does not clear the street under it -- unioned with the
# rail corridor measured by LiDAR (left/right edge offsets per station) where
# it was measured. Rail infrastructure itself is exempt (RAIL_SURFACE_ITEMS and
# the rail_edge domain).
RAIL_KINEMATIC_MARGIN_M = 0.5
# Rail-surface items that belong on the track: the ballasted track itself and
# the level-crossing deck the road crosses it on.
RAIL_SURFACE_ITEMS = frozenset({("rail_track_corridor", None),
                                ("level_crossing_equipment", "roadway_over_double_track")})


def _at_grade(feature) -> bool:
    rel = ((feature.get("properties") or {}).get("elevation_relation") or {})
    rel = rel.get("value", rel) if isinstance(rel, dict) else {}
    rel = rel or {}
    if rel.get("bridge") not in (None, "no") or rel.get("tunnel") not in (None, "no"):
        return False
    try:
        return int(float(str(rel.get("layer") or 0))) == 0
    except ValueError:
        return True


class _RailGuardedSurfaces:
    """Surfaces wrapper: adds RAIL_ENVELOPE inside the at-grade rail swept
    envelope and the LiDAR-measured rail corridor."""

    def __init__(self, surf, canonical: dict, vehicle_width_m: float | None = None,
                 corridor_edges: dict | None = None):
        from shapely.geometry import LineString, Point, Polygon
        from shapely.ops import unary_union
        from shapely.prepared import prep
        from scripts.pipeline.compile_classic_train_routes import _tags
        self._surf, self._Point = surf, Point
        obs = {o["observation_id"]: o for o in canonical.get("source_observations", [])}
        rails = [f for f in canonical.get("canonical_features", [])
                 if f.get("semantic_class") == "railway_boundary"
                 and (f.get("geometry") or {}).get("kind") == "polyline"
                 and _tags(f, obs).get("railway") == "rail"]
        grade = [f for f in rails if _at_grade(f)]
        self.rail_lines = len(grade)
        self.elevated_rail_lines = len(rails) - len(grade)
        self.half_width_m = (vehicle_width_m / 2.0 + RAIL_KINEMATIC_MARGIN_M
                             if vehicle_width_m else RAIL_CLEAR_M)
        parts = [LineString([p[:2] for p in f["geometry"]["coordinates"]]).buffer(self.half_width_m)
                 for f in grade]
        # LiDAR corridor: quads between consecutive stations' measured edges.
        grade_ids = {str(f["feature_id"]).rsplit("/", 1)[-1] for f in grade}
        self.corridor_quads = 0
        for wid, way in ((corridor_edges or {}).get("ways") or {}).items():
            if str(wid) not in grade_ids:
                continue
            st = way.get("stations") or []
            for a, b in zip(st, st[1:]):
                if None in (a[5], a[6], b[5], b[6]):
                    continue
                def side(s, sign, d):  # left normal (-ty, tx); right (ty, -tx)
                    tx, ty = s[3], s[4]
                    return (s[1] - sign * ty * d, s[2] + sign * tx * d)
                quad = [side(a, 1, a[5]), side(b, 1, b[5]), side(b, -1, b[6]), side(a, -1, a[6])]
                poly = Polygon(quad)
                if poly.is_valid and poly.area > 0:
                    parts.append(poly); self.corridor_quads += 1
        self._area = prep(unary_union(parts)) if parts else None

    def point_surfaces(self, e, n):
        s = set(self._surf.point_surfaces(e, n))
        if self._area is not None and self._area.contains(self._Point(e, n)):
            s.add(RAIL_ENVELOPE)
        return s


def _legal(surf, index, pose, out_x, out_y, envelope) -> bool:
    e, n = float(pose["E"]), float(pose["N"])
    if _on_carriageway(surf, e, n):
        return False
    if _on_carriageway(surf, e + out_x * CLEAR_M, n + out_y * CLEAR_M):
        return False
    lon = float(envelope.get("longitudinal_extent_m") or 0.0)
    lat = float(envelope.get("lateral_extent_m") or 0.0)
    return not footprint_mass_conflicts(e, n, float(pose.get("yaw_deg", 0.0)),
                                        lon, lat, index)


RADIAL_DIRECTIONS = 32
RESOLVE_AGREE_M = 0.05


def _project_onto_carrier(index, carrier_id, e, n):
    """(chainage_m, lateral_m, side) of point (e, n) on its carrier polyline.

    Orthogonal projection onto the nearest segment, in resolve()'s own
    convention: "R" is the +90deg normal of the travel direction, "L" its
    mirror. Returns None for a carrier without polyline geometry.
    """
    poly = ((index.by_id.get(carrier_id) or {}).get("geometry") or {}).get("coordinates") or []
    best, acc = None, 0.0
    for a, b in zip(poly, poly[1:]):
        ax, ay, bx, by = float(a[0]), float(a[1]), float(b[0]), float(b[1])
        dx, dy = bx - ax, by - ay
        seg = math.hypot(dx, dy)
        if seg <= 0:
            continue
        t = max(0.0, min(1.0, ((e - ax) * dx + (n - ay) * dy) / (seg * seg)))
        fx, fy = ax + t * dx, ay + t * dy
        d = math.hypot(e - fx, n - fy)
        if best is None or d < best[0]:
            nx, ny = -dy / seg, dx / seg            # bearing + 90deg
            s = (e - fx) * nx + (n - fy) * ny
            best = (d, acc + t * seg, abs(s), "R" if s >= 0 else "L")
        acc += seg
    return None if best is None else best[1:]


def _nearest_legal_site(surf, index, att, e0, n0, envelope):
    """Nearest legal standing site to (e0, n0), searched radially.

    The search is in the ground plane, not along the attachment carrier's
    normal: a pole attached to a zebra-crossing line has that line's normal
    running ALONG the road, and stepping along it moved one pole 7.25 m down
    the carriageway (build_reg_29 obs-pole-0026). The chosen point is written
    back as chainage / lateral / side by orthogonal projection onto the same
    carrier and accepted only when resolve_span reproduces it, so the plan,
    dispatch and coherence all resolve one position.
    """
    carrier_id = str(att.get("canonical_carrier_id") or "")
    k = 1
    while k * STEP_M <= MAX_MOVE_M + 1e-9:
        r = k * STEP_M
        ring = []
        for j in range(RADIAL_DIRECTIONS):
            a = 2.0 * math.pi * j / RADIAL_DIRECTIONS
            ux, uy = math.cos(a), math.sin(a)
            ce, cn = e0 + ux * r, n0 + uy * r
            proj = _project_onto_carrier(index, carrier_id, ce, cn)
            if proj is None:
                return None
            chainage, lateral, side = proj
            cand = dict(att, chainage_m=round(chainage, 3),
                        lateral_offset_m=round(lateral, 3), side=side)
            try:
                cp = resolve_span(cand, index)
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if math.hypot(float(cp["E"]) - ce, float(cp["N"]) - cn) > RESOLVE_AGREE_M:
                continue
            if _legal(surf, index, cp, ux, uy, envelope):
                ring.append((math.hypot(float(cp["E"]) - e0, float(cp["N"]) - n0), j, cand, cp))
        if ring:
            ring.sort(key=lambda row: (row[0], row[1]))
            dist, _, cand, cp = ring[0]
            return dist, cand, cp
        k += 1
    return None


RUN_SAMPLES = 9


def _dist_to_carrier(index, carrier_id, e, n) -> float:
    poly = ((index.by_id.get(carrier_id) or {}).get("geometry") or {}).get("coordinates") or []
    best = float("inf")
    for a, b in zip(poly, poly[1:]):
        ax, ay, bx, by = float(a[0]), float(a[1]), float(b[0]), float(b[1])
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((e - ax) * dx + (n - ay) * dy) / L2))
        best = min(best, math.hypot(e - (ax + t * dx), n - (ay + t * dy)))
    return best


def _clear_run(p: dict, surf, index):
    """Shift a straight run (placement["run"] E0,N0 -> E1,N1) off the paint.

    Only shifts away from the run's carrier centreline (the run keeps its
    side); every sample along the run must be off the carriageway, with
    CLEAR_M further out also off, and no sample footprint in a mass. Returns
    None when the run is already clear, else (shift_m or None, record).
    """
    run = p["run"]
    e0, n0, e1, n1 = (float(run[k]) for k in ("E0", "N0", "E1", "N1"))
    length = math.hypot(e1 - e0, n1 - n0)
    if length <= 1e-6:
        return None
    ts = [k / (RUN_SAMPLES - 1) for k in range(RUN_SAMPLES)]
    pts = [(e0 + (e1 - e0) * t, n0 + (n1 - n0) * t) for t in ts]
    on = sum(_on_carriageway(surf, e, n) for e, n in pts)
    if on == 0:
        return None
    ux, uy = (e1 - e0) / length, (n1 - n0) / length
    carrier_id = str((p.get("attachment") or {}).get("canonical_carrier_id") or "")
    me, mn = pts[RUN_SAMPLES // 2]
    d_mid = _dist_to_carrier(index, carrier_id, me, mn)
    yaw = math.degrees(math.atan2(uy, ux))
    envelope = p.get("asset_physical_envelope") or {}
    lat = float(envelope.get("lateral_extent_m") or 0.4)
    record = {"placement_id": p.get("placement_id"), "module_id": p.get("module_id"),
              "canonical_carrier_id": carrier_id, "kind": "run",
              "samples_on_carriageway": on, "samples": RUN_SAMPLES,
              "source_run_epsg32654": [e0, n0, e1, n1]}
    for sign in (1.0, -1.0):
        nx, ny = -uy * sign, ux * sign
        if _dist_to_carrier(index, carrier_id, me + nx, mn + ny) <= d_mid:
            continue  # this normal points toward the carrier; keep the side
        k = 1
        while k * STEP_M <= MAX_MOVE_M:
            d = k * STEP_M
            ok = True
            for e, n in pts:
                ce, cn = e + nx * d, n + ny * d
                if (_on_carriageway(surf, ce, cn)
                        or _on_carriageway(surf, ce + nx * CLEAR_M, cn + ny * CLEAR_M)
                        or footprint_mass_conflicts(ce, cn, yaw, STEP_M, lat, index)):
                    ok = False
                    break
            if ok:
                return d, dict(record, adjustment_m=d, normal=[round(nx, 6), round(ny, 6)])
            k += 1
    return None, record


def _apply_run_shift(p: dict, d: float, nx: float, ny: float) -> None:
    run = dict(p["run"])
    for ek, nk in (("E0", "N0"), ("E1", "N1")):
        run[ek] = float(run[ek]) + nx * d
        run[nk] = float(run[nk]) + ny * d
    p["run"] = run
    for key in ("anchor_segment",):
        seg = p.get(key)
        if isinstance(seg, dict) and all(k in seg for k in ("E0", "N0", "E1", "N1")):
            p[key] = dict(seg, E0=float(seg["E0"]) + nx * d, N0=float(seg["N0"]) + ny * d,
                          E1=float(seg["E1"]) + nx * d, N1=float(seg["N1"]) + ny * d)
    if "E" in p and "N" in p:
        p["E"], p["N"] = float(p["E"]) + nx * d, float(p["N"]) + ny * d
    att = p.get("attachment")
    if isinstance(att, dict):
        # The shift is away from the carrier on the run's own side.
        att = dict(att)
        for k in ("lateral_offset_m", "start_lateral_offset_m", "end_lateral_offset_m"):
            if att.get(k) is not None:
                att[k] = round(float(att[k]) + d, 3)
        p["attachment"] = att


def _standing(p: dict, catalog: dict) -> bool:
    from scripts.pipeline.dispatch_population_plan import _owner_for
    if p.get("module_id") in OVERHEAD_MODULES:
        return False
    if ((p.get("module_id"), p.get("variant")) in RAIL_SURFACE_ITEMS
            or (p.get("module_id"), None) in RAIL_SURFACE_ITEMS):
        return False
    # The road cross-section (kerb line, gutter, paver, crosswalk edge) is
    # the edge of the paint itself; its catalog envelope height is a default.
    if (catalog.get(str(p.get("module_id"))) or {}).get("owning_domain") in CROSS_SECTION_DOMAINS:
        return False
    try:
        owner, _ = _owner_for(p, catalog)
    except ValueError:
        return False
    vertical = (p.get("asset_physical_envelope") or {}).get("vertical_extent_m")
    return (owner in STANDING_OWNERS and vertical is not None
            and float(vertical) > STANDING_MIN_M)


def clear(plan: dict, canonical: dict, surf, catalog_doc: dict,
          vehicle_width_m: float | None = None, corridor_edges: dict | None = None) -> dict:
    """Move or withdraw road-attached standing placements on carriageway.

    Mutates ``plan`` in place and returns the receipt. ``catalog_doc`` is the
    module catalog the dispatcher partitions with, so ownership is decided by
    the same function that will route the placement.
    """
    rows = catalog_doc.get("modules", catalog_doc)
    catalog = {str(r["module_id"]): r for r in rows}
    index = CanonicalIndex(canonical)
    surf = _RailGuardedSurfaces(surf, canonical, vehicle_width_m, corridor_edges)  # RAIL-CLEAR-02
    moved, withdrawn, checked = [], [], 0
    for carrier in plan.get("carriers", []):
        keep = []
        for p in carrier.get("placements", []):
            att = p.get("attachment")
            if (not isinstance(att, dict)
                    or att.get("record_type") != "attachment"
                    or not _standing(p, catalog)):
                keep.append(p)
                continue
            if isinstance(p.get("run"), dict) and all(
                    k in p["run"] for k in ("E0", "N0", "E1", "N1")):
                if att.get("anchor_kind") not in ("carrier_span", "carrier_chainage"):
                    keep.append(p)  # facade runs belong to their building face
                    continue
                checked += 1
                result = _clear_run(p, surf, index)
                if result is None:
                    keep.append(p)
                    continue
                d, row = result
                if d is None:
                    reason = (f"run stands on painted carriageway and no "
                              f"legal kerbside offset exists within {MAX_MOVE_M} m")
                    withdrawn.append(dict(row, reason=reason))
                    p["population_disposition"] = {
                        "state": "intentional_omission",
                        "policy_rule": "FL-4876 ROADSIDE-CLEAR-01",
                        "category": "no_kerbside_site_off_carriageway",
                        "reason": reason, "module_id": p.get("module_id")}
                else:
                    _apply_run_shift(p, d, *row["normal"])
                    p["evidence"] = dict(p.get("evidence") or {}, roadside_clearance={
                        "kind": "run_moved_off_painted_carriageway",
                        "adjustment_m": d})
                    moved.append(row)
                keep.append(p)
                continue
            if (att.get("anchor_kind") != "carrier_chainage"
                    or p.get("run")):
                keep.append(p)
                continue
            checked += 1
            try:
                pose = resolve_span(att, index)
            except (KeyError, TypeError, ValueError, IndexError):
                keep.append(p)
                continue
            e0, n0 = float(pose["E"]), float(pose["N"])
            if not _on_carriageway(surf, e0, n0):
                keep.append(p)
                continue
            envelope = p.get("asset_physical_envelope") or {}
            best = _nearest_legal_site(surf, index, att, e0, n0, envelope)
            row = {"placement_id": p.get("placement_id"),
                   "module_id": p.get("module_id"),
                   "canonical_carrier_id": att.get("canonical_carrier_id"),
                   "source_lateral_offset_m": att.get("lateral_offset_m"),
                   "source_side": att.get("side"),
                   "source_pose_epsg32654": [e0, n0]}
            if best is None:
                reason = (f"resolved pose stands on painted carriageway and no "
                          f"legal kerbside site exists within {MAX_MOVE_M} m")
                withdrawn.append(dict(row, reason=reason))
                # Kept in the plan as an explicit terminal (dispatch routes a
                # population_disposition to "terminal"), so it is counted as a
                # rule-decided omission rather than a vanished feature. The
                # emission manifest admits only emitted / intentional_omission;
                # standing an object in the road is the alternative refused.
                p["population_disposition"] = {
                    "state": "intentional_omission",
                    "policy_rule": "FL-4876 ROADSIDE-CLEAR-01",
                    "category": "no_kerbside_site_off_carriageway",
                    "reason": reason,
                    "module_id": p.get("module_id"),
                    "source_pose_epsg32654": [e0, n0],
                }
                keep.append(p)
                continue
            dist, cand, cp = best
            p["attachment"] = cand
            for key in ("E", "N"):
                if key in p:
                    p[key] = float(cp[key])
            if isinstance(p.get("resolved_pose"), dict):
                p["resolved_pose"] = dict(p["resolved_pose"], E=cp["E"], N=cp["N"])
            p.setdefault("evidence", {})
            p["evidence"] = dict(p["evidence"], roadside_clearance={
                "kind": "moved_off_painted_carriageway",
                "adjustment_m": round(dist, 3), "chainage_m": cand["chainage_m"],
                "lateral_offset_m": cand["lateral_offset_m"], "side": cand["side"]})
            moved.append(dict(row, adjustment_m=round(dist, 3), chainage_m=cand["chainage_m"],
                              lateral_offset_m=cand["lateral_offset_m"], side=cand["side"]))
            keep.append(p)
        carrier["placements"] = keep
    return {"schema": SCHEMA, "clear_m": CLEAR_M, "max_move_m": MAX_MOVE_M,
            "standing_min_m": STANDING_MIN_M,
            "standing_owners": list(STANDING_OWNERS),
            "overhead_modules": sorted(OVERHEAD_MODULES),
            "rail_clear_m": surf.half_width_m, "rail_lines": surf.rail_lines,
            "elevated_rail_lines_excluded": surf.elevated_rail_lines,
            "lidar_corridor_quads": surf.corridor_quads,
            "rail_surface_items": sorted(f"{m}:{v}" for m, v in RAIL_SURFACE_ITEMS),
            "checked": checked, "moved": moved, "withdrawn": withdrawn,
            "counts": {"checked": checked, "moved": len(moved),
                       "withdrawn": len(withdrawn)}}


def dry_run(plan_path, canonical_path, surf, catalog_path):
    import json
    plan = copy.deepcopy(json.loads(open(plan_path, encoding="utf-8").read()))
    canonical = json.loads(open(canonical_path, encoding="utf-8").read())
    catalog = json.loads(open(catalog_path, encoding="utf-8").read())
    return clear(plan, canonical, surf, catalog)
