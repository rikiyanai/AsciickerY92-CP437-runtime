#!/usr/bin/env python3
"""Compile the single Numabe population plan every module lowerer consumes.

Evidence-bounded join (FL-4809: candidate density is not evidence of object
count; no family-wide stamping):
- Registered observations win at their location (unchanged).
- One module per family slot per carrier, chosen by observed frequency prior
  within that family, tie-break by catalog order.
- Carrier-class compatibility from OSM tags: water/drain, rail, commercial,
  residential-boundary, and sidewalk modules are restricted to compatible
  carriers (see COMPAT rules below).
- Continuous modules (walls, hedges, fences, guardrails, curbs, gutters,
  channels, retaining edges, sidewalk surfaces, rail fences) emit ONE run
  placement per qualifying carrier edge with run:{E0,N0,E1,N1,length_m}.
- Point modules use the observed density prior:
  n = round(carrier_length_m x density_per_m), capped by a numeric
  rules_resolved spacing basis where one exists (none exists: rules_resolved
  carries no numeric spacing, so density alone governs; stated in summary).
  n = 0 emits nothing and the pair is listed empty as no_density_basis.
- DEFAULT:not_designated alone is never a basis for a point module.
- Open waterway channels ride on OSM evidence (kind 'osm'), not on rules.
- Culvert ways get no open-channel placement.

Inputs are sibling-lane files on disk only. No network access.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

from pyproj import Transformer

try:
    from world_surfaces import load_surfaces
except ImportError:  # sibling owner scripts/pipeline/world_surfaces.py
    load_surfaces = None
try:
    from plan_spatial_provenance import stamp as _stamp_spatial_provenance
except ImportError:  # sibling owner scripts/pipeline/plan_spatial_provenance.py
    _stamp_spatial_provenance = None


# ---------------------------------------------------------------------------
# NUMABE-PLAN-05 structural placement stage (FL-4813 owner split: this file
# owns placement-vs-surface; lane NUMABE-SURF-01 owns world_surfaces.py).
# A road-edge run must never be emitted on the carrier centreline again:
# road-edge runs are emitted per side from Surfaces.offset_polyline, whose
# pieces arrive already clipped out of junctions.
# ---------------------------------------------------------------------------
# PLAN-06 observation terrain bbox (EPSG:32654): the AOI ring bounds rule
# emission, not evidence. The built terrain covers this whole bbox, so an
# observation inside it is on the map and is placed at its coordinates.
# Min corner matches build_full terrain origin_easting/northing; max corner
# matches the plan aoi_bbox the terrain build covers.
OBS_TERRAIN_BBOX = {"min_e": 379699.095, "max_e": 380113.054,
                    "min_n": 3938417.877, "max_n": 3939043.068}
STRUCTURAL_POLICY_VERSION = "y92.plan05_structural.v1"
PLANNER_VERSION = "y92.population_plan.canonical_authority.v2"
SURFACES_SOURCE = ("world_surfaces.load_surfaces(plan=None): span, "
                   "measured-parallel, then class-default widths; footway/"
                   "steps/pedestrian are walk surfaces (kind footway), not "
                   "carriageways. Offsets resolve per carrier from "
                   "Surfaces.road_width_m, the same basis as measurement.")

# Lateral rule vocabulary for MODULE_SURFACE_POLICY. Road-context distances
# resolve per carrier from Surfaces.road_width_m: curb_line = half width
# (carriageway edge); curb_inset = half - 0.3 (painted edge line inside the
# edge); hardware = half + 0.3 (posts/rails clear of the carriageway, the
# same convention as the world_surfaces offset self-check); sidewalk_band =
# half + 1.0 (walk centre where a walk exists, verge path elsewhere).
# Rail/water offsets sit beyond the Surfaces DEFAULT halves (rail 5.0 m,
# water 1.5 m). roadside_or_front and context_edge resolve by carrier class;
# overhead and keep carry no move rule (a violating overhead/keep placement
# is dropped).
CURB_INSET_M = 0.3
HARDWARE_OUT_M = 0.3
SIDEWALK_BAND_OUT_M = 1.0
FACE_MARGIN_M = 0.1  # ROAD-XSECTION-01: roadside hardware to building face
# FL-4876 CORNER-BOLLARD-01: catalog placement_requirements of the corner
# point modules, and how far from a clipped piece end they may stand.
CORNER_FAMILIES = frozenset({"sidewalk_corner", "crosswalk_edge"})
CORNER_REACH_M = 3.0
ROAD_RUN_MAX_SAGITTA_M = 0.5


def _corner_ends(runs, reach_m):
    """Keep only the first and last reach_m of a piece (a list of straight
    polyline runs); a piece shorter than 2 * reach_m is kept whole."""
    pts = [runs[0][0]]
    for r in runs:
        pts.extend(r[1:])
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1]
    if total <= 2.0 * reach_m:
        return runs

    def sub(s0, s1):
        out = []
        for i in range(len(pts) - 1):
            a0, a1 = cum[i], cum[i + 1]
            if a1 < s0 or a0 > s1 or a1 == a0:
                continue
            def at(s):
                u = (s - a0) / (a1 - a0)
                return (pts[i][0] + u * (pts[i + 1][0] - pts[i][0]),
                        pts[i][1] + u * (pts[i + 1][1] - pts[i][1]))
            p0 = at(max(s0, a0)); p1 = at(min(s1, a1))
            if not out:
                out.append(p0)
            out.append(p1)
        return out

    return [r for r in (sub(0.0, reach_m), sub(total - reach_m, total))
            if len(r) >= 2]
RAIL_EDGE_OUT_M = 0.3
WATER_EDGE_OUT_M = 0.3
MIN_KEEP_M = 1.0
FACE_COHERENCE_M = 0.75  # run samples within this of their own building
# ring are the building face, not a surface violation
FACE_SNAP_M = 0.5  # point within this of its own building ring is {building}

# Domain defaults: (lateral, allowed, forbidden). allowed = surface kinds
# the footprint may lie on; forbidden = kinds that always fail. Open ground
# (point_surfaces == set()) passes any non-empty allowed set and fails only
# an empty allowed set. Explicit per-module overrides follow.
DOMAIN_SURFACE_DEFAULT = {
    "road_cross_section": (
        "centreline", {"carriageway", "junction"},
        {"building", "rail", "water", "sidewalk"}),
    "street_fixture": (
        "hardware", {"sidewalk"},
        {"carriageway", "junction", "building"}),
    "property_frontage": (
        "building_front", {"building", "sidewalk"},
        {"carriageway", "junction", "rail", "water"}),
    "rail_edge": (
        "rail_edge", {"rail"},
        {"carriageway", "junction", "building", "water", "sidewalk"}),
    "facade_archetype": (
        "building_front", {"building", "sidewalk"},
        {"carriageway", "junction", "rail", "water"}),
    "generic_storefront": (
        "building_front", {"building", "sidewalk"},
        {"carriageway", "junction", "rail", "water"}),
    "utility_network": (
        "hardware", {"sidewalk"},
        {"carriageway", "junction", "building"}),
    "vegetation": (
        "roadside_or_front", {"building", "sidewalk"},
        {"carriageway", "junction", "rail", "water"}),
}

# Explicit overrides where the domain default is wrong: module ->
# (lateral, allowed, forbidden). A second element of None keeps the domain
# allowed set; same for the third and forbidden.
MODULE_SURFACE_OVERRIDES = {
    # Road paint and ironwork that live on the carriageway itself.
    "manhole_and_grate": (
        "centreline", {"carriageway", "junction", "sidewalk"}, None),
    # Road paint that lives on the carriageway: edge tolerance admits the
    # model's sidewalk band (coarse class-default widths push true edge
    # observations one band out); rule points sit on the centreline anyway.
    "road_surface_text_marking": (
        "centreline", {"carriageway", "junction", "sidewalk"},
        {"building", "rail", "water"}),
    "road_text_arrow_30": (
        "centreline", {"carriageway", "junction", "sidewalk"},
        {"building", "rail", "water"}),
    "road_edge_dashed_marking": (
        "centreline", {"carriageway", "junction", "sidewalk"},
        {"building", "rail", "water"}),
    "zebra_crossing": (
        "centreline", {"carriageway", "junction", "sidewalk"},
        {"building", "rail", "water"}),
    "white_edge_line_shoulder": (
        "curb_inset", {"carriageway", "junction", "sidewalk"},
        {"building", "rail", "water"}),
    # Edge hardware and surfaces: off the centreline, per side.
    "curb_and_open_gutter": (
        "curb_line", {"sidewalk", "carriageway"},
        {"junction", "building", "rail", "water"}),
    "curb_edge_line": (
        "curb_line", {"sidewalk", "carriageway"},
        {"junction", "building", "rail", "water"}),
    "crosswalk_and_edge_guardrail": ("hardware", None, None),
    "sidewalk_paver_surface": (
        "sidewalk_band", {"sidewalk"},
        {"carriageway", "junction", "building", "rail", "water"}),
    "green_cycle_or_walk_band": (
        "sidewalk_band", {"sidewalk", "carriageway", "junction"},
        {"building", "rail", "water"}),
    # Open drains ride waterway carriers only (compat gate already ensures).
    "open_and_covered_drain": (
        "water_centre", {"water"},
        {"carriageway", "junction", "building", "rail", "sidewalk"}),
    # Forecourt and wall-mounted commerce: building face, never carriageway.
    "vending_white_red_blue": ("building_front", None, None),
    "exterior_ac_condenser": (
        "building_front", {"building"},
        {"carriageway", "junction", "rail", "water", "sidewalk"}),
    "residential_carport_canopy": (
        "building_front", {"building", "sidewalk"},
        {"carriageway", "junction", "rail", "water"}),
    "wall_mounted_mailbox": (
        "building_front", {"building"},
        {"carriageway", "junction", "rail", "water", "sidewalk"}),
    "parked_bicycles": (
        "building_front", {"building", "sidewalk"},
        {"carriageway", "junction", "rail", "water"}),
    "covered_waste_storage_enclosure": (
        "building_front", {"building"},
        {"carriageway", "junction", "rail", "water"}),
    "waste_collection_bin_pair": ("building_front", None, None),
    "decorative_waterwheel": (
        "keep", {"water", "building", "sidewalk"},
        {"carriageway", "junction", "rail"}),
    # Dual roadside-or-front modules: lateral resolves by carrier class.
    "notice_sign": ("roadside_or_front", None, None),
    "sidewalk_signboard": ("roadside_or_front", None, None),
    "hazard_strip_barrier": ("roadside_or_front", None, None),
    "meter_and_service_cabinet": (
        "roadside_or_front", {"building", "sidewalk"},
        {"carriageway", "junction", "rail", "water"}),
    # Rail corridor: fences at the corridor edge, track on its centre.
    "rail_track_corridor": ("rail_centre", None, None),
    "overhead_gantry_wire": (
        "overhead", {"rail", "carriageway", "junction"},
        {"building", "water"}),
    # Bridge railing spans water on a path or minor carriageway (unsure
    # row: no observed instance constrains the cross-section; the policy
    # keeps it off junctions, rails and buildings and is flagged in debt).
    "waterway_bridge_railing": (
        "water_edge", {"water", "carriageway", "sidewalk"},
        {"junction", "rail", "building"}),
    # Property edge shared with a waterway: lateral follows the carrier.
    "metal_boundary_fence": (
        "context_edge", {"building", "sidewalk", "water"},
        {"carriageway", "junction", "rail"}),
    # Water hardware.
    "water_control_structure": (
        "water_edge", {"water"},
        {"carriageway", "junction", "building", "rail", "sidewalk"}),
    "stone_lined_water_channel": (
        "water_centre", {"water"},
        {"carriageway", "junction", "building", "rail", "sidewalk"}),
    # Rear-alley archetype sits on the building ring, not the front.
    "service_alley": ("building_ring", None, None),
    # Overhead spans have no ground footprint: any surface passes.
    "wire_span": (
        "overhead",
        {"carriageway", "junction", "sidewalk", "building",
         "rail", "water"},
        set()),
}

# Custom-plane kinds (observation/custom evidence without a catalog row):
# road carriers move to the verge band; own-surface carriers keep position.
CUSTOM_POLICY_BY_CARRIER = {
    "road": ("sidewalk_band", {"sidewalk"},
               {"carriageway", "junction"}),
    "building": ("keep", {"building", "sidewalk"},
                   {"carriageway", "junction", "rail", "water"}),
    "railway": ("keep", {"rail"},
                  {"carriageway", "junction", "building", "water"}),
    "waterway": ("keep", {"water"},
                   {"carriageway", "junction", "building", "rail"}),
}

# Nominal-dimension bands per owning_domain (height/width min/max in
# metres; runs additionally carry their true length in run.length_m).
# DEFAULT (stated once): height 0.3-3.0 m (nominal 1.2), width 0.1-2.0 m
# (nominal 0.5), run length at most 1500 m. The catalog carries no
# per-module dims, so every nominal comes from its band default except
# facade heights, which use the computed facade_height_for value; a future
# catalog with real dims feeds the same check unchanged (giant-bush rule:
# an instance outside its band is a violation).
DIMENSION_BANDS_M = {
    "DEFAULT": ((0.3, 3.0, 1.2), (0.1, 2.0, 0.5), 1500.0),
    "facade_archetype": ((2.5, 45.0, 6.0), (2.0, 40.0, 8.0), 1500.0),
    "property_frontage": ((0.2, 3.5, 1.2), (0.1, 1.0, 0.3), 1500.0),
    "road_cross_section": ((0.0, 1.5, 0.2), (0.05, 3.0, 0.3), 1500.0),
    "street_fixture": ((0.3, 8.0, 1.5), (0.1, 2.0, 0.4), 1500.0),
    "rail_edge": ((0.5, 8.0, 1.5), (0.1, 3.0, 0.5), 1500.0),
    "generic_storefront": ((0.5, 5.0, 2.5), (0.3, 8.0, 2.0), 1500.0),
    "utility_network": ((1.0, 12.0, 6.0), (0.1, 2.0, 0.4), 1500.0),
    "vegetation": ((0.3, 6.0, 1.5), (0.3, 4.0, 1.0), 1500.0),
}


# Single importable handle for the surface policy table: defaults by
# owning_domain plus explicit per-module overrides. module_surface_policy
# resolves one (lateral, allowed, forbidden) row from it; the mapping is
# the table, unchanged.
MODULE_SURFACE_POLICY = {
    "defaults": DOMAIN_SURFACE_DEFAULT,
    "overrides": MODULE_SURFACE_OVERRIDES,
    "custom_by_carrier": CUSTOM_POLICY_BY_CARRIER,
}


def module_surface_policy(module_id, carrier_class, catalog_by_id):
    """Return (lateral, allowed, forbidden) for one module id."""
    if module_id is None:
        kind = "road" if carrier_class.startswith("road:") \
            else ("building" if carrier_class == "building"
                  else ("railway" if carrier_class.startswith("railway")
                        else "waterway"))
        return CUSTOM_POLICY_BY_CARRIER.get(
            kind, CUSTOM_POLICY_BY_CARRIER["road"])
    entry = catalog_by_id.get(module_id)
    domain = (entry or {}).get("owning_domain") or "DEFAULT"
    lateral, allowed, forbidden = DOMAIN_SURFACE_DEFAULT.get(
        domain, ("keep", set(), {"carriageway", "junction"}))
    if module_id in MODULE_SURFACE_OVERRIDES:
        olat, oallow, oforb = MODULE_SURFACE_OVERRIDES[module_id]
        lateral = olat
        if oallow is not None:
            allowed = oallow
        if oforb is not None:
            forbidden = oforb
    return lateral, set(allowed), set(forbidden)


def resolve_lateral(lateral, carrier_class):
    """Resolve carrier-dependent laterals to a concrete rule."""
    if lateral == "roadside_or_front":
        if carrier_class.startswith("road:"):
            return "hardware"
        if carrier_class == "building":
            return "building_front"
        return None
    if lateral == "context_edge":
        if carrier_class == "waterway":
            return "water_edge"
        if carrier_class == "building":
            return "building_front"
        return None
    return lateral


def surfaces_violate(surfaces, allowed, forbidden):
    """The stated rule for RULE placements: forbidden contact fails;
    otherwise the footprint must sit inside allowed (open ground passes
    any non-empty allowed). Empty allowed means open ground only."""
    if surfaces & forbidden:
        return True
    if not allowed:
        return bool(surfaces)
    return bool(surfaces) and not surfaces <= allowed


def own_carrier_surfaces(carrier_class):
    """Surfaces that ARE the placement's own carrier. Observations win at
    their location: contact with the recorded carrier is evidence, not a
    violation; only foreign forbidden contact fails an observation."""
    if carrier_class.startswith("road:"):
        return {"carriageway"}
    if carrier_class.startswith("railway"):
        return {"rail"}
    if carrier_class == "waterway":
        return {"water"}
    if carrier_class == "building":
        return {"building"}
    return set()


def observation_keep(surfaces, forbidden):
    """Registered observations win at their location. Only a forbidden
    carriageway/junction hit fails the point (then the lateral rule moves
    it or it is dropped): every other foreign contact is model tolerance
    against coarse halves (5.0 m rail, wide primary) and wall-mounted
    roadside truth (poles, signs, vending at the face)."""
    return not bool(surfaces & (forbidden & {"carriageway",
                                              "junction"}))


def base_cap_key(pid):
    """Collapse bilateral (-L/-R), geometry (-gNN), split (-sNN) and clip
    (-cNN) suffixes so the per-carrier cap still counts modules, not
    geometric pieces."""
    k = re.sub(r"-c\d+$", "", pid)
    k = re.sub(r"-s\d+$", "", k)
    k = re.sub(r"-g\d+$", "", k)
    k = re.sub(r"-[LR]$", "", k)
    return k


# ---------------------------------------------------------------------------
# PLAN-07 single-owner conformance export. The plan stage and the lowerers
# share exactly this definition of "conforms" (deep-cut depth, dense
# guard step, own-carrier contact for observations, marking allowance):
# the stage shapes and audits through it, the lowerers import it.
# ---------------------------------------------------------------------------
ROAD_EDGE_LATERALS = ("curb_line", "curb_inset", "hardware",
                      "sidewalk_band")
CONFORM_DEEP_M = 1.0
CONFORM_GUARD_M = 0.25
_CONFORM_FOOTWAYS = None
_CONFORM_WARNED = False


def _conform_footway_tracks(surfaces):
    """Footway centreline tracks for the crossing cut. Main registers
    its carrier-derived tracks here; otherwise they are read from the
    surfaces object itself (same zones source, same halves)."""
    global _CONFORM_WARNED
    if _CONFORM_FOOTWAYS is not None:
        return _CONFORM_FOOTWAYS
    fw = getattr(surfaces, "_footways", None) or {}
    items = fw.values() if isinstance(fw, dict) else fw
    tracks = []
    for r in items:
        try:
            pl = r["polyline"]
            half = float(r["half"])
        except (TypeError, KeyError, IndexError, ValueError):
            continue
        tracks.append(([(p[0], p[1]) for p in pl], half))
    if not tracks and not _CONFORM_WARNED:
        _CONFORM_WARNED = True
        print("[plan] conformance warning: no footway tracks available; "
              "footway-crossing cut skipped")
    return tracks


def _same_run(piece, r):
    (q0, q1) = piece
    return (math.hypot(q0[0] - r["E0"], q0[1] - r["N0"]) <= 0.25
            and math.hypot(q1[0] - r["E1"], q1[1] - r["N1"]) <= 0.25)


def reconform_mass_clip_pieces(pieces, *, module_id, carrier_class,
                               catalog_by_id, surf, tracks,
                               face_coherent=None, station_ctx=frozenset()):
    """MASSCLIP-RECONF-01. Re-run rule-run mass-clip pieces through the
    structural conformance pipeline.

    A kept piece can end at a mass-ring crossing that still sits inside a
    surface-model forbidden kind (ring-source disagreement at grazing
    corners). Pieces that already conform pass through untouched; the rest
    are replaced by their conformant remainders (>= MIN_KEEP_M) or dropped.
    Returns (kept_pieces, n_clipped_subs, n_dropped). Observations never
    reach here (evidence at their coordinates is never trimmed).
    """
    lateral, _allowed, forbidden = module_surface_policy(
        module_id, carrier_class, catalog_by_id)
    lateral = resolve_lateral(lateral, carrier_class)
    out = []
    n_clipped = 0
    n_dropped = 0
    for (q0, q1) in pieces:
        if face_coherent is not None and face_coherent(q0, q1):
            hard = forbidden - (
                {"building", "sidewalk", "footway"} | set(station_ctx))
            kept = _run_pieces(q0, q1, True, None, hard, lateral,
                               forbidden, surf, tracks)
        else:
            kept = _run_pieces(q0, q1, False, forbidden, None, lateral,
                               forbidden, surf, tracks)
        if (len(kept) == 1 and _same_run(
                kept[0], {"E0": q0[0], "N0": q0[1],
                          "E1": q1[0], "N1": q1[1]})):
            out.append((q0, q1))
            continue
        subs = [(rk0, rk1) for (rk0, rk1) in kept
                if math.hypot(rk1[0] - rk0[0], rk1[1] - rk0[1])
                >= MIN_KEEP_M - 1e-9]
        if subs:
            n_clipped += len(subs)
            out.extend(subs)
        else:
            n_dropped += 1
    return out, n_clipped, n_dropped


# FL-4876 DENSITY-SYNTH-01: operator-authorized surrounding synthesis for
# route bins the photo corpus never covered. Poles, lamps and trees exist
# only as observations near the station; the northern route has one pano
# total, so no evidence can ever place them there. The operator authorized
# pattern synthesis from surrounding spacing (pole 30 m DEFAULT, lamp/tree
# ~40 m evidenced medians), OSM road geometry and point-cloud premise
# validation. Evidenced carriers are never touched: stations keep clear of
# observed streetscape, pass the same placement_conforms gate as every rule
# point, and carry an explicit synthesis provenance no observation may cite.
SYNTH_SPACING_M = {
    "utility_pole_wire_cluster": 30.0,
    "pole_streetlight": 40.0,
    "street_tree": 40.0,
}
SYNTH_SHORT_MOD = {
    "utility_pole_wire_cluster": "pole",
    "pole_streetlight": "lamp",
    "street_tree": "tree",
}
SYNTH_ROUTE_OFFSET_M = 35.0
SYNTH_OBSERVED_CLEAR_M = 25.0
SYNTH_TREE_EXTRA_LATERAL_M = 1.5


def chainage_point(polyline, ch_m):
    """Point at chainage metres along a polyline: (E, N, bearing_deg)."""
    acc = 0.0
    for a, b in zip(polyline, polyline[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if acc + seg >= ch_m and seg > 1e-9:
            t = max(0.0, min(1.0, (ch_m - acc) / seg))
            return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]),
                    math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])))
        acc += seg
    if len(polyline) >= 2:
        a, b = polyline[-2], polyline[-1]
        return (b[0], b[1], math.degrees(math.atan2(b[1] - a[1],
                                                   b[0] - a[0])))
    return None


def select_synth_stations(carriers, route_pts, observed_pts, spacing_m,
                          phase_m=0.0,
                          max_offset_m=SYNTH_ROUTE_OFFSET_M,
                          clear_m=SYNTH_OBSERVED_CLEAR_M):
    """Chainage stations for pattern synthesis: [(key, E, N, bearing, side)].

    Only carriers near the route with no observed streetscape within
    clear_m of a station. Sides alternate L/R down the carrier. Pure
    geometry: lateral offset, conformance and attachment happen at the
    call site with the surfaces.
    """
    out = []
    for key in sorted(carriers, key=str):
        poly = carriers[key].get("polyline") or []
        if len(poly) < 2:
            continue
        length = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                     for a, b in zip(poly, poly[1:]))
        if length < spacing_m:
            continue
        near_route = False
        for a, b in zip(poly, poly[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy or 1e-12
            for rx, ry in route_pts:
                tt = max(0.0, min(1.0, ((rx - a[0]) * dx + (ry - a[1]) * dy)
                                  / L2))
                if math.hypot(rx - (a[0] + tt * dx),
                              ry - (a[1] + tt * dy)) <= max_offset_m:
                    near_route = True
                    break
            if near_route:
                break
        if not near_route:
            continue
        ch = phase_m
        k = 0
        while ch < length:
            hit = chainage_point(poly, ch)
            if hit is None:
                break
            ex, ey, bearing = hit
            if min(math.hypot(ex - rx, ey - ry) for rx, ry in route_pts
                   ) > max_offset_m:
                ch += spacing_m
                k += 1
                continue
            if any(math.hypot(ex - ox, ey - oy) <= clear_m
                   for ox, oy in observed_pts):
                ch += spacing_m
                k += 1
                continue
            out.append((key, ex, ey, bearing, "L" if k % 2 == 0 else "R"))
            ch += spacing_m
            k += 1
    return out


def _seg_cross_t(a, b, c, d):
    den = (b[0] - a[0]) * (d[1] - c[1]) - (b[1] - a[1]) * (d[0] - c[0])
    if abs(den) < 1e-12:
        return None
    t = ((c[0] - a[0]) * (d[1] - c[1])
         - (c[1] - a[1]) * (d[0] - c[0])) / den
    u = ((c[0] - a[0]) * (b[1] - a[1])
         - (c[1] - a[1]) * (b[0] - a[0])) / den
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return max(0.0, min(1.0, t))
    return None


def _merge_windows(wins):
    wins = sorted(wins)
    merged = []
    for lo, hi in wins:
        if merged and lo <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return merged


def _cut_footway_crossings(a, b, tracks):
    """Split a run around proper footway-centreline crossings
    (buffer footway half + 0.5 m each side). Parallel overlap has no
    discrete crossing and is kept: footways are walk surfaces."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-9:
        return [(a, b)]
    wins = []
    for poly, half in tracks:
        buf = (half + 0.5) / L
        for c, d in zip(poly, poly[1:]):
            t = _seg_cross_t(a, b, c, d)
            if t is not None:
                wins.append((max(0.0, t - buf), min(1.0, t + buf)))
    if not wins:
        return [(a, b)]
    merged = _merge_windows(wins)
    bounds = [0.0]
    for lo, hi in merged:
        bounds += [lo, hi]
    bounds.append(1.0)
    out = []
    for k in range(0, len(bounds) - 1, 2):
        lo, hi = bounds[k], bounds[k + 1]
        if (hi - lo) * L >= MIN_KEEP_M - 1e-9:
            q0 = (a[0] + lo * (b[0] - a[0]),
                  a[1] + lo * (b[1] - a[1]))
            q1 = (a[0] + hi * (b[0] - a[0]),
                  a[1] + hi * (b[1] - a[1]))
            out.append((q0, q1))
    return out


def _footway_windows(a, b, tracks):
    """Violating t-windows of the footway-crossing cut (informational:
    the exact cut pieces come from _cut_footway_crossings)."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-9:
        return []
    wins = []
    for poly, half in tracks:
        buf = (half + 0.5) / L
        for c, d in zip(poly, poly[1:]):
            t = _seg_cross_t(a, b, c, d)
            if t is not None:
                wins.append((max(0.0, t - buf), min(1.0, t + buf)))
    return _merge_windows(wins)


def _deep_cut(a, b, kinds, surfaces, depth=CONFORM_DEEP_M):
    """Remove run intervals lying deeper than `depth` inside the given
    kinds (via segment_conflicts negative expand). A face grazing the
    model edge survives; a face through the street is cut."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-9:
        return [(a, b)]
    wins = []
    for k in sorted(kinds):
        for cf in surfaces.segment_conflicts(a, b, -depth, {k}):
            wins.append((max(0.0, cf["t0"]), min(1.0, cf["t1"])))
    if not wins:
        return [(a, b)]
    merged = _merge_windows(wins)
    bounds = [0.0]
    for lo, hi in merged:
        bounds += [lo, hi]
    bounds.append(1.0)
    out = []
    for k in range(0, len(bounds) - 1, 2):
        lo, hi = bounds[k], bounds[k + 1]
        if (hi - lo) * L >= MIN_KEEP_M - 1e-9:
            out.append(((a[0] + lo * (b[0] - a[0]),
                         a[1] + lo * (b[1] - a[1])),
                        (a[0] + hi * (b[0] - a[0]),
                         a[1] + hi * (b[1] - a[1]))))
    return out


def _guard_cut(a, b, kinds, surfaces):
    """Densify below the 0.5 m clip sampling: cut 0.25 m samples
    inside `kinds`, keep maximal outside runs >= 1.0 m. Guarantees no
    surviving piece hides a forbidden dip (including its midpoint)
    between clip samples."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < MIN_KEEP_M - 1e-9:
        return []
    n = max(1, int(math.ceil(L / CONFORM_GUARD_M)))
    flags = []
    for i in range(n + 1):
        t = i / n
        E = a[0] + t * (b[0] - a[0])
        N = a[1] + t * (b[1] - a[1])
        flags.append(bool(set(surfaces.point_surfaces(E, N)) & kinds))
    out = []
    i = 0
    while i <= n:
        if flags[i]:
            i += 1
            continue
        j = i
        while j + 1 <= n and not flags[j + 1]:
            j += 1
        if (j - i) / n * L >= MIN_KEEP_M - 1e-9:
            t0, t1 = i / n, j / n
            out.append(((a[0] + t0 * (b[0] - a[0]),
                         a[1] + t0 * (b[1] - a[1])),
                        (a[0] + t1 * (b[0] - a[0]),
                         a[1] + t1 * (b[1] - a[1]))))
        i = j + 1
    return out


def _clip_windows(a, b, kinds, surfaces):
    """Violating t-windows of the 0.5 m clip sampling (informational:
    the exact kept pieces come from Surfaces.clip_segment)."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-9:
        return []
    n = max(1, int(math.ceil(L / 0.5)))
    wins = []
    i = 0
    while i <= n:
        t = i / n
        E = a[0] + t * (b[0] - a[0])
        N = a[1] + t * (b[1] - a[1])
        if bool(set(surfaces.point_surfaces(E, N)) & kinds):
            j = i
            while j + 1 <= n:
                t2 = (j + 1) / n
                E2 = a[0] + t2 * (b[0] - a[0])
                N2 = a[1] + t2 * (b[1] - a[1])
                if not bool(set(surfaces.point_surfaces(E2, N2)) & kinds):
                    break
                j += 1
            wins.append([i / n, j / n])
            i = j + 1
        else:
            i += 1
    return _merge_windows(wins)


def _guard_windows(a, b, kinds, surfaces):
    """Violating t-windows of the dense guard sampling."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-9:
        return []
    n = max(1, int(math.ceil(L / CONFORM_GUARD_M)))
    wins = []
    i = 0
    while i <= n:
        t = i / n
        E = a[0] + t * (b[0] - a[0])
        N = a[1] + t * (b[1] - a[1])
        if bool(set(surfaces.point_surfaces(E, N)) & kinds):
            j = i
            while j + 1 <= n:
                t2 = (j + 1) / n
                E2 = a[0] + t2 * (b[0] - a[0])
                N2 = a[1] + t2 * (b[1] - a[1])
                if not bool(set(surfaces.point_surfaces(E2, N2)) & kinds):
                    break
                j += 1
            wins.append([i / n, j / n])
            i = j + 1
        else:
            i += 1
    return _merge_windows(wins)


def _map_windows(wins, lo, hi):
    """Map sub-piece t-windows into the parent run frame."""
    span = hi - lo
    return _merge_windows(
        [[lo + s0 * span, lo + s1 * span] for s0, s1 in wins])


def _run_pieces(a, b, coherent, clip_kinds, hard, lateral, forb, surfaces,
                tracks):
    """Full run pipeline shared by the structural stage and the
    conformance export: deep cut (face-coherent) or
    Surfaces.clip_segment, then the dense guard, then the
    footway-crossing cut for road-edge laterals. Every returned piece
    is >= 1.0 m."""
    out = []
    if coherent:
        for pc in _deep_cut(a, b, hard, surfaces):
            out.extend(_guard_cut(pc[0], pc[1], hard, surfaces))
    else:
        for pc in surfaces.clip_segment(a, b, clip_kinds):
            if math.hypot(pc[1][0] - pc[0][0],
                         pc[1][1] - pc[0][1]) < MIN_KEEP_M - 1e-9:
                continue
            out.extend(_guard_cut(pc[0], pc[1], clip_kinds, surfaces))
    if lateral in ROAD_EDGE_LATERALS and "carriageway" in forb:
        cut = []
        for pc in out:
            cut.extend(_cut_footway_crossings(pc[0], pc[1], tracks))
        out = cut
    return out


def _run_kinds(a, b, surfaces):
    """Surface kinds touched along a run (informational hit list)."""
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    n = max(1, int(math.ceil(L / 0.5)))
    out = set()
    for i in range(n + 1):
        t = i / n
        out |= set(surfaces.point_surfaces(a[0] + t * (b[0] - a[0]),
                                           a[1] + t * (b[1] - a[1])))
    return sorted(out)


ALIGNED_LATERALS = frozenset({
    "curb_line", "curb_inset", "hardware", "sidewalk_band",
    "rail_edge", "rail_centre", "building_front", "building_ring",
    "water_edge",
})
RUN_YAW_TOL_DEG = 2.0
POINT_YAW_TOL_DEG = 10.0


def _yaw_err_deg(yaw, ref):
    d = abs(float(yaw) - float(ref)) % 180.0
    return min(d, 180.0 - d)


def _carrier_bearings_at(poly, E, N):
    """Candidate carrier bearings at the nearest point: the nearest
    segment plus, when the foot clamps at a shared vertex, the incident
    neighbour (a polyline vertex genuinely has two bearings)."""
    best = None
    for i, (a, b) in enumerate(zip(poly, poly[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 == 0.0:
            continue
        t = ((E - a[0]) * dx + (N - a[1]) * dy) / L2
        tc = max(0.0, min(1.0, t))
        d2 = (E - (a[0] + tc * dx)) ** 2 + (N - (a[1] + tc * dy)) ** 2
        if best is None or d2 < best[0]:
            best = (d2, i, tc)
    if best is None:
        return []
    _d2, i, tc = best
    out = []
    for j in (i - 1, i, i + 1):
        if 0 <= j < len(poly) - 1:
            a, b = poly[j], poly[j + 1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            if dx == 0.0 and dy == 0.0:
                continue
            if j != i and not (tc <= 0.0 and j == i - 1) and \
                    not (tc >= 1.0 and j == i + 1):
                continue
            out.append(math.degrees(math.atan2(dy, dx)) % 360.0)
    return out


def _carrier_bearing_at(poly, E, N):
    best = None
    for a, b in zip(poly, poly[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 == 0.0:
            continue
        t = max(0.0, min(1.0, ((E - a[0]) * dx + (N - a[1]) * dy) / L2))
        d2 = (E - (a[0] + t * dx)) ** 2 + (N - (a[1] + t * dy)) ** 2
        if best is None or d2 < best[0]:
            best = (d2, math.degrees(math.atan2(dy, dx)) % 360.0)
    return None if best is None else best[1]


def audit_orientation(placements_by_key, carriers, catalog_by_id):
    """Yaw convention audit (YAW-01 owner): yaw_deg is degrees CCW from
    +E along the run/carrier. Run placements must match their run bearing
    (mod 180, 2 deg); point placements with a carrier-aligned lateral must
    match the carrier bearing at the nearest point (mod 180, 10 deg).
    Unanchored (unzoned-carrier) observations are exempt and counted."""
    out = {"checked": 0, "violations": 0, "exempt_observations": 0,
           "anchor_leg_saves": 0, "by_module": {}, "samples": []}

    def bump(mod, bad):
        d = out["by_module"].setdefault(mod, {"checked": 0,
                                                "violations": 0})
        d["checked"] += 1
        if bad:
            d["violations"] += 1

    cat = catalog_by_id if catalog_by_id is not None else {}
    for key, plist in placements_by_key.items():
        c = carriers.get(key)
        if c is None:
            c = carriers.get(str(key))
        cclass = (c or {}).get("carrier_class", "")
        poly = (c or {}).get("polyline") or []
        for p in plist:
            yaw = p.get("yaw_deg")
            if yaw is None:
                continue
            mod = p.get("module_id") or p.get(
                "custom_identity_subject", "unknown")
            if cclass == "unzoned" and (p.get("evidence") or {}).get(
                    "kind") == "observation":
                out["exempt_observations"] += 1
                continue
            r = p.get("run")
            if r is not None:
                L = math.hypot(r["E1"] - r["E0"], r["N1"] - r["N0"])
                if L <= 1e-9:
                    continue
                ref = math.degrees(math.atan2(r["N1"] - r["N0"],
                                              r["E1"] - r["E0"])) % 360.0
                err = _yaw_err_deg(yaw, ref)
                bad = err > RUN_YAW_TOL_DEG + 1e-9
                kind = "run"
            else:
                lat, _al, _fo = module_surface_policy(
                    p.get("module_id"), cclass, cat)
                lat = resolve_lateral(lat, cclass)
                if lat not in ALIGNED_LATERALS:
                    continue
                # FL-4876 FACING-01: an owner-authored facing (reviewer's
                # orientation_basis) is the owner's decision, not a carrier
                # alignment; it is counted, not judged against the carrier.
                if "authored_orientation_basis" in (p.get("evidence") or {}):
                    out["exempt_authored_facing"] = out.get(
                        "exempt_authored_facing", 0) + 1
                    continue
                refs = _carrier_bearings_at(poly, p["E"], p["N"])
                if not refs:
                    continue
                # Anchor evidence leg (POPPLAN-01): an observation
                # carrying its own measured anchor segment is referenced
                # against the anchor bearing too. A self-consistent
                # observation (yaw agrees with its anchor) is not a yaw
                # defect when the re-sited ring corner disagrees; a
                # yaw-vs-anchor mismatch still violates via the carrier
                # refs. Thresholds unchanged; passes can only stay passing
                # (min over a superset), so OSM-compiled plans are
                # unaffected.
                base_bad = min(
                    _yaw_err_deg(yaw, _r) for _r in refs) > POINT_YAW_TOL_DEG + 1e-9
                _anc = p.get("anchor_segment")
                if (isinstance(_anc, dict)
                        and (p.get("evidence") or {}).get("kind") == "observation"):
                    try:
                        _ab = math.degrees(math.atan2(
                            _anc["N1"] - _anc["N0"],
                            _anc["E1"] - _anc["E0"])) % 360.0
                    except (KeyError, TypeError, ValueError):
                        _ab = None
                    if _ab is not None:
                        refs = list(refs) + [_ab]
                kind = "point"
                ref = min(refs, key=lambda _r: _yaw_err_deg(yaw, _r))
                err = _yaw_err_deg(yaw, ref)
                bad = err > POINT_YAW_TOL_DEG + 1e-9
                if base_bad and not bad:
                    out["anchor_leg_saves"] += 1
            out["checked"] += 1
            bump(mod, bad)
            if bad:
                out["violations"] += 1
                if len(out["samples"]) < 20:
                    out["samples"].append({
                        "placement_id": p["placement_id"], "module": mod,
                        "E": round(p["E"], 3), "N": round(p["N"], 3),
                        "yaw_deg": round(float(yaw), 3),
                        "ref_deg": round(ref, 3),
                        "err_deg": round(err, 3), "kind": kind})
    out["by_module"] = dict(sorted(out["by_module"].items()))
    return out


def placement_conforms(surfaces, *, module_id, carrier_class,
                       evidence_kind, point=None, run=None,
                       half_width_m=0.0, catalog_by_id=None,
                       own_context=()) -> dict:
    """Judge one placement against the plan surface policy: the single
    owner of conformance semantics for the plan stage and the lowerers.
    Observations are ok by definition (kinds_hit still reported); marking
    and other carriageway-allowed modules are ok on carriageway/junction;
    face-coherent building runs take the 1.0 m deep cut, all other runs
    the clip plus 0.25 m dense guard plus the footway-crossing cut.
    Nonzero half_width_m re-checks the run footprint at that expand
    (positive) or deep-cut depth (negative) instead of the stage pipeline.
    Returns ok, kinds_hit, violating run intervals [[t0, t1]], and the
    deciding rule name."""
    cat = catalog_by_id if catalog_by_id is not None else {}
    # FL-4876 STATION-01. Surfaces that are this placement's own context in
    # addition to the standard building/sidewalk/footway set. A station
    # building's face legitimately stands against the rail corridor, and
    # this is the single owner of conformance semantics for both the plan
    # stage and the lowerers, so the exemption has to live here rather than
    # as a local patch in one of them. Empty for every ordinary placement.
    _own_ctx = set(own_context or ())
    _face_own = {"building", "sidewalk", "footway"} | _own_ctx
    lateral, allowed, forbidden = module_surface_policy(
        module_id, carrier_class, cat)
    lateral = resolve_lateral(lateral, carrier_class)
    if (point is None) == (run is None):
        return {"ok": False, "kinds_hit": [], "intervals": [],
                "rule": "no_geometry"}
    own = own_carrier_surfaces(carrier_class)
    if point is not None:
        kinds_hit = sorted(
            set(surfaces.point_surfaces(point[0], point[1])))
        s = set(kinds_hit)
        if evidence_kind == "observation":
            # Observations are evidence at their coordinates: ok by
            # definition (the stage records disagreements as
            # observation_surface_conflicts instead of failing them).
            ok = True
            rule = "observation_ok"
        else:
            ok = not surfaces_violate(s, allowed, forbidden)
            rule = "rule_point_strict"
        return {"ok": ok, "kinds_hit": kinds_hit, "intervals": [],
                "rule": rule}
    (E0, N0), (E1, N1) = run
    a = (float(E0), float(N0))
    b = (float(E1), float(N1))
    kinds_hit = _run_kinds(a, b, surfaces)
    tracks = _conform_footway_tracks(surfaces)
    if half_width_m != 0.0:
        if lateral in ("building_front", "building_ring"):
            kinds = forbidden - _face_own
        elif evidence_kind == "observation":
            kinds = forbidden - own
        else:
            kinds = forbidden
        wins = []
        for k in sorted(kinds):
            for cf in surfaces.segment_conflicts(a, b, half_width_m, {k}):
                wins.append([max(0.0, cf["t0"]), min(1.0, cf["t1"])])
        intervals = _merge_windows(wins)
        return {"ok": not intervals, "kinds_hit": kinds_hit,
                "intervals": intervals,
                "rule": "deep_cut" if half_width_m < 0 else "expand"}
    coherent = lateral in ("building_front", "building_ring")
    hard = forbidden - _face_own
    if coherent:
        kept = _run_pieces(a, b, True, None, hard, lateral, forbidden,
                           surfaces, tracks)
        r = {"E0": a[0], "N0": a[1], "E1": b[0], "N1": b[1]}
        ok = True if evidence_kind == "observation" else (
            len(kept) == 1 and _same_run(kept[0], r))
        rule = ("observation_ok" if evidence_kind == "observation"
                else "face_deep_cut")
    else:
        kinds = forbidden - own if evidence_kind == "observation" \
            else forbidden
        kept = _run_pieces(a, b, False, kinds, None, lateral, forbidden,
                           surfaces, tracks)
        r = {"E0": a[0], "N0": a[1], "E1": b[0], "N1": b[1]}
        ok = True if evidence_kind == "observation" else (
            len(kept) == 1 and _same_run(kept[0], r))
        rule = ("observation_ok" if evidence_kind == "observation"
                else "rule_run_strict")
    intervals = _run_intervals(a, b, coherent, kinds if not coherent
                               else hard, hard, lateral, forbidden,
                               surfaces, tracks)
    return {"ok": ok, "kinds_hit": kinds_hit, "intervals": intervals,
            "rule": rule}


def _run_intervals(a, b, coherent, kinds, hard, lateral, forb, surfaces,
                   tracks):
    """Violating t-windows of the stage pipeline (informational: the
    exact kept pieces come from _run_pieces)."""
    if coherent:
        wins = []
        for lo, hi in _deep_windows(a, b, hard, surfaces):
            wins.append([lo, hi])
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-9:
            return _merge_windows(wins)
        for pc in _deep_cut(a, b, hard, surfaces):
            lo = ((pc[0][0] - a[0]) * (b[0] - a[0])
                  + (pc[0][1] - a[1]) * (b[1] - a[1])) / (L * L)
            hi = ((pc[1][0] - a[0]) * (b[0] - a[0])
                  + (pc[1][1] - a[1]) * (b[1] - a[1])) / (L * L)
            wins.extend(_map_windows(
                _guard_windows(pc[0], pc[1], hard, surfaces), lo, hi))
        return _merge_windows(wins)
    wins = _clip_windows(a, b, kinds, surfaces)
    L = math.hypot(b[0] - a[0], b[1] - a[1])
    if L < 1e-9:
        return _merge_windows(wins)
    for pc in surfaces.clip_segment(a, b, kinds):
        if math.hypot(pc[1][0] - pc[0][0],
                     pc[1][1] - pc[0][1]) < MIN_KEEP_M - 1e-9:
            continue
        lo = ((pc[0][0] - a[0]) * (b[0] - a[0])
              + (pc[0][1] - a[1]) * (b[1] - a[1])) / (L * L)
        hi = ((pc[1][0] - a[0]) * (b[0] - a[0])
              + (pc[1][1] - a[1]) * (b[1] - a[1])) / (L * L)
        wins.extend(_map_windows(
            _guard_windows(pc[0], pc[1], kinds, surfaces), lo, hi))
    if lateral in ROAD_EDGE_LATERALS and "carriageway" in forb:
        wins.extend(_footway_windows(a, b, tracks))
    return _merge_windows(wins)


def _deep_windows(a, b, kinds, surfaces):
    """Violating t-windows of the deep cut (segment_conflicts form)."""
    wins = []
    for k in sorted(kinds):
        for cf in surfaces.segment_conflicts(a, b, -CONFORM_DEEP_M, {k}):
            wins.append([max(0.0, cf["t0"]), min(1.0, cf["t1"])])
    return _merge_windows(wins)

# Family -> rules subject providing a placement basis. Ordered; first hit wins.
# 'waterway' families have no rule basis (NONE): open channels are emitted
# from OSM evidence instead. Commercial forecourt families ride on occupation
# (shopfront occupation objects); other setback families keep setback.
BASIS_ORDER = [
    ("railway_boundary", ("rail", "track", "platform", "level_crossing")),
    ("marking_form", ("cross", "marking", "stop_line")),
    ("sign_form", ("sign",)),
    ("occupation", ("pole", "utility", "wire", "overhead", "cantilever", "gantry",
                    "storefront_forecourt", "station_or_storefront_forecourt")),
    ("accessibility", ("tactile",)),
    ("setback", ("frontage", "forecourt", "alley", "commercial_", "corner_",
                 "cultural_", "facade", "property_boundary")),
    ("road_structure", ("carriageway", "sidewalk", "pedestrian_route", "walkway",
                        "footway", "path_side", "curb", "gutter", "drainage",
                        "roadside", "street_edge", "shoulder")),
]

SUBJECT_CARRIERS = {
    "road_structure": ("road:",),
    "marking_form": ("road:",),
    "sign_form": ("road:",),
    "railway_boundary": ("railway",),
    "setback": ("building",),
    "occupation": ("road:", "building"),
    "accessibility": ("road:",),
}

SUBJECT_PARAM = {
    "road_structure": "carriageway_width_m",
    "accessibility": "tactile_paving_width_m",
    "marking_form": "crosswalk_marking",
    "sign_form": "sign_mounting_height_m",
    "setback": "setback_distance_from_centerline_m",
    "railway_boundary": "rail_boundary_fence_height_m",
    "occupation": "utility_pole_spacing_basis",
}

# Subject descriptions for rule_source strings.
SUBJECT_DESC = {
    "road_structure": "JP-ROAD-STRUCT-ORDER base class",
    "marking_form": "JP-SIGN-MARK-ORDER-MARKINGS",
    "sign_form": "JP-MLIT-SIGN-INSTALL-STD",
    "railway_boundary": "JP-RAILWAY-TECH-STD",
    "setback": "OTA-ART42-SETBACK",
    "occupation": "JP-ROAD-STRUCT-ORDER base class",
    "accessibility": "JP-ACCESSIBILITY-STD",
}

OVERHEAD_KEYS = ("wire", "gantry", "overhead", "cantilever")
SIGN_KEYS = ("sign", "storefront", "facade", "awning", "banner", "face_",
             "notice", "poster", "wayfinding", "merchandise", "display",
             "noren", "shutter", "menu")

# Module compatibility classes.
RAIL_MODS = {"platform_railing", "level_crossing_equipment",
             "rail_corridor_fence", "overhead_gantry_wire",
             "rail_track_corridor", "rail_corridor_mesh_fence",
             "white_railguard"}
COMMERCIAL_KEYS = ("vending", "storefront", "awning")
RESIDENTIAL_WALL_MODS = {"concrete_block_wall", "stone_cobble_wall",
                         "ivy_wall", "trimmed_hedge"}
SIDEWALK_MODS = {"sidewalk_paver_surface"}

# Road-edge hardware: road/footway carriers only, never buildings.
GUARDRAIL_MODS = {"pedestrian_guardrail_continuous",
                  "pedestrian_guardrail_vertical_bar_continuous",
                  "crosswalk_and_edge_guardrail"}
ROAD_EDGE_MODS = GUARDRAIL_MODS | {
    "curb_edge_line", "curb_and_open_gutter", "sidewalk_paver_surface",
    "white_edge_line_shoulder", "road_edge_dashed_marking",
    "green_cycle_or_walk_band"}

GATE_KEYS = ("gate", "entrance", "entry")

CONTINUOUS_KEYS = ("wall", "hedge", "fence", "guardrail", "curb", "gutter",
                   "channel", "drain", "retaining", "paver", "railing",
                   "track_corridor")

# FL-4876 GUARDRAIL-OBSERVED-01: modules that rules never synthesize; only observations and hand-authored
# fixtures place them (see the rule-family loop).
OBSERVED_ONLY_MODULES = frozenset({"pedestrian_guardrail_continuous",
                                   "pedestrian_guardrail_vertical_bar_continuous"})

SPINE_THRESHOLD_M = 15.0
OBS_WIN_M = 2.0
COMMERCIAL_RADIUS_M = 15.0
STREET_EDGE_RADIUS_M = 15.0
CROSSING_RADIUS_M = 60.0

# Facade-slot configuration (NUMABE-PLAN-04). Every building carrier receives
# one facade_archetype run on its street-facing front edge. The 8 catalog
# archetypes split by context: commercial/mixed fronts take the top-prior
# mixed-use module, residential fronts take the top-prior residential module.
FACADE_ARCHETYPE_MODS = (
    "concrete_wall_column", "lowrise_roof_edge", "narrow_mixed_use",
    "residential_facade_roofed_entry",
    "residential_facade_with_boundary_sign", "roller_shutter_facade",
    "rooftop_safety_railing", "service_alley")
STOREFRONT_RUN_MOD = "small_commercial_awning"
SIGN_POINT_MOD = "storefront_signboard"
FRONT_SEARCH_RADIUS_M = 12.0
FACADE_DEFAULT_HEIGHT_M = 6.0
LEVEL_HEIGHT_M = 3.0

# Physical clearance from a canonical building face for observed frontage
# point modules.  The observation owns presence and along-face location; its
# registered point is not allowed to put the emitted footprint inside the
# host mass.  Values are the actual half-extents authored by the lowerer.
# FL-4876 SNAP-CAP-01: furthest (m) a declared-frontage or custom-face point may move to reach its host's nearest
# face. Equal to the spatial-coherence attachment tolerance; beyond it the authored point and yaw are kept.
FACE_POINT_SNAP_MAX_M = 1.5


def _face_snap_limit_m(placement: dict) -> float:
    """SNAP-CAP-01 limit for one placement: FACE_POINT_SNAP_MAX_M, widened to twice the observation's own position
    uncertainty (a camera-ray point with +-3 m uncertainty may be 4.7 m off its host face: build_reg_89
    SF-4876-S401-080). A refusal additionally requires the authored point to be clear of every building mass;
    an authored point inside another building is not a site, so the declared host face is the better estimate."""
    unc = ((placement.get("evidence") or {}).get("position_uncertainty_m"))
    try:
        unc = float(unc)
    except (TypeError, ValueError):
        unc = 0.0
    return max(FACE_POINT_SNAP_MAX_M, 2.0 * unc)

FRONTAGE_POINT_OUTWARD_M = {
    "glass_entry": 0.05,
    "independent_storefront_awning": 0.18,
    "meter_and_service_cabinet": 0.30,
    "noren_or_shutter_entry": 0.08,
    "parked_bicycles": 0.45,
    "potted_frontage_plant": 0.28,
    "small_tree": 0.85,
    "storefront_banner": 0.08,
    "storefront_pedestal_sign": 0.24,
    "vending_white_red_blue": 0.50,
    "exterior_ac_condenser": 0.18,
    "residential_carport_canopy": 1.80,
    "wall_mounted_mailbox": 0.18,
    "covered_waste_storage_enclosure": 0.35,
    "waste_collection_bin_pair": 0.38,
}
CUSTOM_IDENTITY_OUTWARD_M = 0.15


def family_basis(family):
    f = family.lower()
    for subject, keys in BASIS_ORDER:
        if any(k in f for k in keys):
            return subject
    return None


def subject_applies(subject, carrier_class):
    return carrier_class.startswith(SUBJECT_CARRIERS[subject])


def is_continuous(module_id):
    m = module_id.lower()
    return any(k in m for k in CONTINUOUS_KEYS)


def is_drain_module(module_id):
    m = module_id.lower()
    return ("channel" in m) or ("drain" in m)


def is_commercial_module(module_id):
    m = module_id.lower()
    return any(k in m for k in COMMERCIAL_KEYS)


def is_boundary_run_mod(module_id):
    m = module_id.lower()
    return module_id in RESIDENTIAL_WALL_MODS or (
        "fence" in m and module_id not in RAIL_MODS
        and not module_id.startswith("rail_"))


def is_gate_mod(module_id):
    m = module_id.lower()
    return any(k in m for k in GATE_KEYS)


def is_storefront_slot_mod(module_id):
    m = module_id.lower()
    return ("storefront" in m) or ("awning" in m)


def is_sign_slot_mod(module_id):
    m = module_id.lower()
    return ("sign" in m) and ("storefront" not in m)


def plan_height(module_id, subject_text, mount_height):
    t = " ".join(x for x in (module_id, subject_text) if x).lower()
    if any(k in t for k in OVERHEAD_KEYS):
        return 5.0
    if any(k in t for k in SIGN_KEYS):
        return mount_height
    return 0.0


def point_in_ring(px, py, ring):
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > py) != (yj > py):
            if px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def seg_dist2(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2


def poly_length(poly):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in zip(poly, poly[1:]))


def _orient(ax, ay, bx, by, cx, cy):
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _on_seg(ax, ay, bx, by, cx, cy):
    return (min(ax, bx) <= cx <= max(ax, bx)
            and min(ay, by) <= cy <= max(ay, by))


def seg_seg_dist2(a, b, c, d):
    """Squared distance between segments ab and cd (exact, 2D)."""
    o1 = _orient(a[0], a[1], b[0], b[1], c[0], c[1])
    o2 = _orient(a[0], a[1], b[0], b[1], d[0], d[1])
    o3 = _orient(c[0], c[1], d[0], d[1], a[0], a[1])
    o4 = _orient(c[0], c[1], d[0], d[1], b[0], b[1])
    if o1 * o2 < 0 and o3 * o4 < 0:
        return 0.0
    if o1 == 0 and _on_seg(a[0], a[1], b[0], b[1], c[0], c[1]):
        return 0.0
    if o2 == 0 and _on_seg(a[0], a[1], b[0], b[1], d[0], d[1]):
        return 0.0
    if o3 == 0 and _on_seg(c[0], c[1], d[0], d[1], a[0], a[1]):
        return 0.0
    if o4 == 0 and _on_seg(c[0], c[1], d[0], d[1], b[0], b[1]):
        return 0.0
    return min(seg_dist2(a[0], a[1], c[0], c[1], d[0], d[1]),
               seg_dist2(b[0], b[1], c[0], c[1], d[0], d[1]),
               seg_dist2(c[0], c[1], a[0], a[1], b[0], b[1]),
               seg_dist2(d[0], d[1], a[0], a[1], b[0], b[1]))


def facade_height_for(tags):
    """Facade height: OSM building:levels x 3 m, else height tag, else
    rules DEFAULT. Returns (height_m, basis)."""
    try:
        n = int(float(str(tags.get("building:levels", "")).strip()))
        if n > 0:
            return n * LEVEL_HEIGHT_M, "building:levels"
    except (TypeError, ValueError):
        pass
    try:
        v = float(str(tags.get("height", "")).strip().lower()
                  .rstrip("m").strip())
        if v > 0:
            return v, "height_tag"
    except (TypeError, ValueError):
        pass
    return FACADE_DEFAULT_HEIGHT_M, "rules_default"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world-ir", required=True)
    ap.add_argument("--zones", required=True)
    ap.add_argument("--instances", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--entities-path", default=None)
    ap.add_argument("--terrain-metadata-path", default=None)
    ap.add_argument("--surface-stamp-plan", default=None,
                    help="FL-4876 ROAD-XSECTION-01: the painted road "
                         "cross-section that road-edge laterals follow")
    ap.add_argument("--route-path", default=None,
                    help="FL-4876 DENSITY-SYNTH-01: valhalla pedestrian route "
                         "for operator-authorized surrounding synthesis")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--authored-decisions", default=None,
                    help="operator decisions that settle an owed "
                         "placement into a decided omission")
    ap.add_argument("--authored-spatial-decisions", default=None,
                    help="operator-reviewed spatial corrections; JEV "
                         "proposals are not accepted here")
    args = ap.parse_args()
    globals()['_AUTHORED_DECISIONS_PATH'] = args.authored_decisions

    from scripts.pipeline.spatial_coherence_review import (
        decision_for as _spatial_decision_for,
        load_decisions as _load_spatial_decisions,
    )
    _authored_spatial_decisions = _load_spatial_decisions(
        args.authored_spatial_decisions)

    with open(args.zones, encoding="utf-8") as f:
        zones = json.load(f)
    with open(args.instances, encoding="utf-8") as f:
        instances = json.load(f)
    with open(args.rules, encoding="utf-8") as f:
        rules = json.load(f)
    with open(args.catalog, encoding="utf-8") as f:
        catalog = json.load(f)["modules"]
    with open(args.world_ir, encoding="utf-8") as f:
        world_ir = json.load(f)
    entities_doc = {"entities": [], "dispositions": []}
    if args.entities_path:
        with open(args.entities_path, encoding="utf-8") as f:
            entities_doc = json.load(f)
    with open("assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
              "official_road_classification.json", encoding="utf-8") as f:
        classification = json.load(f)
    with open("assets/source_bundles/numabe_connected_neighborhood_scope_20260915/"
              "connected_scope.geojson", encoding="utf-8") as f:
        scope = json.load(f)

    # Carrier semantics are canonical-first (FL-4809 POPPLAN-01): place
    # names/categories, road cross-sections, and building usage come from
    # canonical claims; OSM tags apply only to features that carry OSM
    # observations. Vocabularies are not conflated (CONFLATE-01): a PLATEAU
    # usage code is recorded verbatim and never mapped to a use class.
    # Commercial place claims (place_category "shop=/amenity=/office=")
    # mark shopfront localities with or without OSM tags.
    COMMERCIAL_PLACE_KEYS = ("shop", "amenity", "office")
    canon_by_id = {f.get("feature_id"): f
                   for f in world_ir["canonical_features"]}
    obs_by_id = {o.get("observation_id"): o
                 for o in world_ir["source_observations"]}

    def _feat_osm_tags(f):
        """OSM tags, only when the feature carries an OSM observation."""
        for oid in f.get("supporting_observation_ids", []) or []:
            a = (obs_by_id.get(oid) or {}).get("attributes", {}) or {}
            if a.get("osm_id") is not None:
                return a.get("tags", {}) or {}
        return {}

    def _feat_usage_code(f):
        """Verbatim PLATEAU usage code, or None (never mapped to tags)."""
        for oid in f.get("supporting_observation_ids", []) or []:
            a = (obs_by_id.get(oid) or {}).get("attributes", {}) or {}
            u = a.get("usage")
            if isinstance(u, dict) and u.get("value") is not None:
                return u["value"]
        return None

    def _feat_place_claim(f):
        """(key, value) from the place_category claim, else (None, None)."""
        props = f.get("properties", {}) or {}
        pc = props.get("place_category", {}) or {}
        v = pc.get("value") if isinstance(pc, dict) else None
        if isinstance(v, str) and "=" in v:
            k, vv = v.split("=", 1)
            return k, vv
        return None, None

    def _feat_road_claim(f):
        """Cross-section claim dict (highway/sidewalk/...), else {}."""
        props = f.get("properties", {}) or {}
        cs = props.get("cross_section", {}) or {}
        v = cs.get("value") if isinstance(cs, dict) else None
        return v if isinstance(v, dict) else {}

    tagmap = {}
    for o in world_ir["source_observations"]:
        a = o.get("attributes", {}) or {}
        if a.get("osm_id") is not None:
            tagmap[str(a.get("osm_id"))] = a.get("tags", {}) or {}
    place_pts = []
    claim_place_pts = []
    place_claim_only = 0
    for f in world_ir["canonical_features"]:
        m = re.search(r"(\d+)$", f.get("feature_id", ""))
        tags = tagmap.get(m.group(1), {}) if m else {}
        claim_key, _claim_value = _feat_place_claim(f)
        tag_commercial = ("shop" in tags or "amenity" in tags)
        if (f.get("semantic_class") == "place" or tag_commercial
                or claim_key in COMMERCIAL_PLACE_KEYS):
            g = f["geometry"].get("coordinates")
            flat = []

            def _collect(c):
                if isinstance(c, (int, float)):
                    flat.append(c)
                else:
                    for q in c:
                        _collect(q)

            _collect(g)
            if len(flat) >= 2:
                xs = flat[0::2]
                ys = flat[1::2]
                place_pts.append((sum(xs) / len(xs), sum(ys) / len(ys)))
                if claim_key in COMMERCIAL_PLACE_KEYS:
                    claim_place_pts.append((sum(xs) / len(xs), sum(ys) / len(ys)))
                    if not (f.get("semantic_class") == "place" or tag_commercial):
                        place_claim_only += 1
    crossing_pts = []
    for f in world_ir["canonical_features"]:
        m = re.search(r"(2145028052|2145028053)$", f.get("feature_id", ""))
        if m:
            g = f["geometry"].get("coordinates")
            crossing_pts.append((g[0], g[1]))
    assert len(crossing_pts) == 2, f"expected 2 crossing nodes, got {len(crossing_pts)}"
    # Canonical building and road authority (POPPLAN-01). OSM building
    # carriers re-site from these rings; plateau-only buildings (no
    # way/node/relation id) become carriers keyed by canonical feature id.
    # Rings are EPSG:32654 like the zone polylines, used directly.
    canon_building_by_osm = {}
    canon_plateau_buildings = []
    canon_road_claim = {}
    for _cf in world_ir["canonical_features"]:
        _fid = _cf.get("feature_id", "")
        if _cf.get("semantic_class") == "building_footprint":
            _coords = _cf["geometry"]["coordinates"]
            _ring = [(_p[0], _p[1]) for _p in _coords]
            _m = re.search(r"[:/](way|node|relation)/(\d+)$", _fid)
            if _m:
                canon_building_by_osm[int(_m.group(2))] = (_cf, _ring)
            else:
                canon_plateau_buildings.append((_cf, _ring))
        elif _cf.get("semantic_class") == "road":
            _m = re.search(r"[:/](way|node|relation)/(\d+)$", _fid)
            if _m:
                canon_road_claim[int(_m.group(2))] = _feat_road_claim(_cf)
    canon_plateau_buildings.sort(key=lambda _fr: _fr[0].get("feature_id", ""))

    to32654 = Transformer.from_crs("EPSG:4326", "EPSG:32654", always_xy=True)
    spine_ll = ring_ll = None
    for feat in scope["features"]:
        role = feat.get("properties", {}).get("role")
        if role == "connected_playable_route":
            spine_ll = feat["geometry"]["coordinates"]
        elif role == "aoi":
            ring_ll = feat["geometry"]["coordinates"][0]
    assert spine_ll and ring_ll, "spine and AOI ring required"
    spine = [to32654.transform(*p) for p in spine_ll]
    ring = [to32654.transform(*p) for p in ring_ll]

    cum = [0.0]
    for a, b in zip(spine, spine[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    route_len = classification["route"]["length_m"]
    scale = route_len / cum[-1]
    cum = [c * scale for c in cum]
    span_bounds = [(s["start_m"], s["end_m"])
                   for s in classification["management_spans"]]
    assert len(span_bounds) == 13, len(span_bounds)

    def nearest_spine(px, py):
        best_d2, best_s = float("inf"), 0.0
        for i, (a, b) in enumerate(zip(spine, spine[1:])):
            d2 = seg_dist2(px, py, a[0], a[1], b[0], b[1])
            if d2 < best_d2:
                best_d2 = d2
                best_s = cum[i] if abs(px - a[0]) + abs(py - a[1]) < \
                    abs(px - b[0]) + abs(py - b[1]) else cum[i + 1]
        span_index = next(i for i, (s0, s1) in enumerate(span_bounds)
                          if best_s < s1 or i == len(span_bounds) - 1)
        return span_index, math.sqrt(best_d2)

    catalog_by_id = {m["module_id"]: m for m in catalog}
    family_modules = defaultdict(list)
    for m in catalog:
        for req in m.get("placement_requirements") or []:
            family_modules[req].append(m["module_id"])
    for v in family_modules.values():
        v.sort()

    # Observed frequency prior per module.
    freq = Counter(x["module_id"] for x in instances if x.get("module_id"))
    # Density prior: instances of module / total length of distinct carriers
    # that received any observation.
    carriers = {}
    for z in zones:
        cid = z["carrier_id"]
        poly = z["polyline_epsg32654"] or []
        carriers[cid] = {
            "carrier_id": cid,
            "carrier_class": z["carrier_class"],
            "families": sorted(set(z["accepted_module_families"])),
            "polyline": [(p[0], p[1]) for p in poly],
        }
    # Canonical carrier authority (POPPLAN-01). Zone polylines are OSM
    # rings; the lowerers check against canonical masses, so OSM-sited
    # placements collide (2160 in build_final4). Re-site OSM building
    # carriers from the canonical footprint, add plateau-only buildings
    # as carriers keyed by canonical feature id, and resolve road class
    # from the cross_section claim first (zone class on disagreement,
    # since surface widths are zoned).
    building_families = sorted({
        fam for z in zones if z.get("carrier_class") == "building"
        for fam in (z.get("accepted_module_families") or [])})
    resited_buildings = 0
    for _ckey, _cc in carriers.items():
        if _cc["carrier_class"] != "building":
            continue
        _hit = canon_building_by_osm.get(_cc["carrier_id"])
        if _hit is not None:
            _cfeat, _cring = _hit
            _cc["polyline"] = [(_p[0], _p[1]) for _p in _cring]
            _cc["canonical_feature_id"] = _cfeat.get("feature_id")
            resited_buildings += 1
    added_plateau = 0
    for _cfeat, _cring in canon_plateau_buildings:
        _ckey = _cfeat.get("feature_id")
        if _ckey in carriers:
            continue
        carriers[_ckey] = {
            "carrier_id": _ckey,
            "carrier_class": "building",
            "families": list(building_families),
            "polyline": [(_p[0], _p[1]) for _p in _cring],
            "canonical_feature_id": _ckey,
        }
        added_plateau += 1
    road_claim_agree = road_claim_missing = road_claim_disagree = 0
    for _ckey, _cc in carriers.items():
        if not _cc["carrier_class"].startswith("road:"):
            continue
        if not isinstance(_cc["carrier_id"], int):
            continue
        _hw = canon_road_claim.get(_cc["carrier_id"], {}).get("highway")
        if not (isinstance(_hw, str) and _hw):
            road_claim_missing += 1
        elif "road:" + _hw == _cc["carrier_class"]:
            road_claim_agree += 1
        else:
            road_claim_disagree += 1
    for inst in instances:
        cid = inst.get("carrier_id")
        key = cid if cid is not None else f"unzoned-{inst['instance_id']}"
        if key not in carriers:
            carriers[key] = {
                "carrier_id": key,
                "carrier_class": "unzoned",
                "families": [],
                "polyline": [(inst["E"], inst["N"])],
            }
    carrier_len = {k: poly_length(c["polyline"]) for k, c in carriers.items()}
    obs_carrier_keys = set()
    for inst in instances:
        cid = inst.get("carrier_id")
        obs_carrier_keys.add(cid if cid is not None
                             else f"unzoned-{inst['instance_id']}")
    obs_length_total = sum(carrier_len.get(k, 0.0) for k in obs_carrier_keys)
    assert obs_length_total > 0
    mod_obs_len = defaultdict(float)
    for inst in instances:
        if not inst.get("module_id"):
            continue
        cid = inst.get("carrier_id")
        key = cid if cid is not None else f"unzoned-{inst['instance_id']}"
        mod_obs_len[inst["module_id"]] += carrier_len.get(key, 0.0)
    density = {m: freq[m] / obs_length_total for m in freq}

    def winner(family):
        cands = family_modules.get(family, [])
        if not cands:
            return None
        return max(cands, key=lambda m: (freq.get(m, 0), -cands.index(m)))

    # Per-carrier compatibility context.
    road_verts = []
    rail_verts = []
    for c in carriers.values():
        if c["carrier_class"].startswith("road:"):
            road_verts.extend(c["polyline"])
        elif c["carrier_class"].startswith("railway"):
            rail_verts.extend(c["polyline"])

    def near_any(px, py, pts, radius):
        r2 = radius * radius
        return any((px - qx) ** 2 + (py - qy) ** 2 < r2 for qx, qy in pts)

    # FL-4876 USECLASS-01. PLATEAU's free-text note, per canonical feature.
    # _feat_usage_code has sat 240 lines above this since it was written and
    # is called by nothing, so the one attribute with real coverage on this
    # lane never reached the facade decision at all.
    plateau_note_by_fid = {}
    for _pf in world_ir["canonical_features"]:
        for _oid in _pf.get("supporting_observation_ids", []) or []:
            _attrs = (obs_by_id.get(_oid) or {}).get("attributes", {}) or {}
            _notes = _attrs.get("source_notes")
            if isinstance(_notes, dict) and isinstance(_notes.get("value"), dict):
                plateau_note_by_fid[str(_pf.get("feature_id"))] = _notes["value"]
                break
    def _station_own_context(_key):
        """Extra own-context surfaces for a mass the source calls a station.

        FL-4876 STATION-01. PLATEAU labels both Numabe station masses
        gen:説明注記 = 駅舎, "station building", and they lie 0.13 m and
        0.105 m from the railway boundary because that is what a station
        is. The face-coherence rule already exempts a building's own
        contact; for these the rail corridor is own contact too. Narrow by
        construction, and empty for every other carrier --
        rail_edge.min_own_offset_m is untouched.
        """
        _notes = (carrier_geo.get(_key, {}) or {}).get("plateau_notes") or {}
        if any("駅舎" in str(_v) for _v in _notes.values()):
            return {"rail"}
        return set()

    carrier_geo = {}
    for key, c in carriers.items():
        tags = tagmap.get(str(c["carrier_id"]), {})
        if c["polyline"]:
            xs = [p[0] for p in c["polyline"]]
            ys = [p[1] for p in c["polyline"]]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            span_index, spine_d = nearest_spine(cx, cy)
        else:
            cx = cy = None
            span_index, spine_d = None, None
        is_waterway = tags.get("waterway") is not None
        is_culvert = tags.get("tunnel") == "culvert"
        tag_shop = any(k in tags for k in ("shop", "amenity", "office"))
        # Claim-first shopfront (POPPLAN-01): a commercial place_category
        # claim inside the carrier ring marks the shop even when OSM tags
        # are absent (plateau-only buildings carry no OSM observations).
        claim_shop = False
        if cx is not None and c["carrier_class"] == "building" and c["polyline"]:
            _bring = [(p[0], p[1]) for p in c["polyline"]]
            if _bring[0] != _bring[-1]:
                _bring = _bring + [_bring[0]]
            for (_px, _py) in claim_place_pts:
                if point_in_ring(_px, _py, _bring):
                    claim_shop = True
                    break
        has_shop = tag_shop or claim_shop
        near_place = cx is not None and near_any(cx, cy, place_pts,
                                                 COMMERCIAL_RADIUS_M)
        street_facing = (cx is not None and near_any(cx, cy, road_verts,
                                                     STREET_EDGE_RADIUS_M))
        near_crossing = (cx is not None and near_any(cx, cy, crossing_pts,
                                                     COMMERCIAL_RADIUS_M))
        rail_adjacent = (cx is not None and near_any(cx, cy, rail_verts,
                                                     STREET_EDGE_RADIUS_M))
        carrier_geo[key] = {
            "cx": cx, "cy": cy, "span_index": span_index, "spine_d": spine_d,
            "off_spine": spine_d is None or spine_d > SPINE_THRESHOLD_M,
            "tags": tags, "is_waterway": is_waterway, "is_culvert": is_culvert,
            "has_shop": has_shop, "tag_shop": tag_shop,
            "claim_shop": claim_shop, "near_place": near_place,
            "street_facing": street_facing, "near_crossing": near_crossing,
            "rail_adjacent": rail_adjacent,
            "plateau_notes": plateau_note_by_fid.get(str(c["carrier_id"]), {}),
        }

    rule_spans = rules["spans"]
    _ext_node = ((rules.get("road_class_defaults") or {}).get(
        "observation_run_extent_m") or {})
    try:
        obs_run_extent_m = float(_ext_node.get("value"))
    except (TypeError, ValueError):
        obs_run_extent_m = None
    obs_run_extent_src = _ext_node.get("source", "")
    obs_runs_created = 0
    obs_runs_anchor_clipped = 0
    obs_runs_kept_as_point = 0
    obs_point_yaw_rederived = 0
    # OBSRUN run-form module set (COVER-01): exactly the union of the canonical
    # representation contract (every linear-owned module except point-kind) and the
    # fixtures lane's run-panel modules (generic_storefront + facade_archetype
    # members, plus the two literal panel ids). Point modules never get a run.
    # The continuity conjunct at the gate stays: markings, wire endpoints and
    # storefront points keep the point form their lanes lower. Dropping it
    # would flip ~200 point-form observations into runs across lanes, which
    # the frozen lanes (fixtures owned by FACEALIGN-01) cannot emit as runs.
    from scripts.pipeline.population_representation_contract import (
        custom_mount_kind, custom_plane_contract, linear_module_ids,
        finalize_wire_span_dispositions, physical_envelope,
        LINEAR_RUN_POINT_MODULES)
    _OBS_RUN_LINEAR = linear_module_ids(catalog, include_points=False)
    _OBS_RUN_FACADE = {
        m.get("module_id") for m in catalog
        if m.get("owning_domain") in ("generic_storefront", "facade_archetype")}
    OBS_RUN_FORM = (_OBS_RUN_LINEAR | _OBS_RUN_FACADE
                    | {"facade_archetype", "generic_storefront"})
    # FL-4876 FACING-01: modules the linear serializer lowers (hedges, rails,
    # poles, manholes, ...) are carrier-aligned by construction and keep the
    # carrier bearing; an owner-authored facing is kept for everything else.
    _LINEAR_LANE_MODULES = linear_module_ids(catalog, include_points=True)
    facade_defaults = rules.get("facade_archetype_defaults_by_use") or {}

    # Structural surfaces (PLAN-05). Class-default widths by construction
    # (plan=None): the root re-measurement uses class default half-widths,
    # and span widths (uniformly 4.0 m) would under-size primary/tertiary
    # carriers against those defaults.
    if load_surfaces is None:
        raise SystemExit(
            "structural stage needs scripts/pipeline/world_surfaces.py")
    from pathlib import Path as _Path
    # FL-4876 ROAD-XSECTION-01: with the stamp plan, road-edge laterals
    # are measured from the painted asphalt edge on each side.
    surf = load_surfaces(_Path(args.zones), _Path(args.world_ir),
                         _Path(args.rules), None,
                         painted_plan_path=(_Path(args.surface_stamp_plan)
                                            if args.surface_stamp_plan else None))

    def road_half_width_m(cid, side=None):
        if side is not None:
            return surf.road_side_half_m(cid, side)
        width, _source = surf.road_width_m(cid)
        return width / 2.0

    def road_lateral_distance(cid, lateral, side=None):
        half = road_half_width_m(cid, side)
        # ROAD-XSECTION-01: on a building-bounded side the face stands at
        # half + BUILD_SUB_M; hardware and sidewalk-band objects stop
        # FACE_MARGIN_M short of it instead of touching or entering it.
        face = surf.road_side_face_m(cid, side) if side is not None else None
        if face is not None and lateral in ("hardware", "sidewalk_band"):
            return max(half, min(
                half + (HARDWARE_OUT_M if lateral == "hardware"
                        else SIDEWALK_BAND_OUT_M),
                face - FACE_MARGIN_M))
        if lateral == "curb_line":
            return half
        if lateral == "curb_inset":
            return max(half - CURB_INSET_M, 0.1)
        if lateral == "hardware":
            return half + HARDWARE_OUT_M
        if lateral == "sidewalk_band":
            return half + SIDEWALK_BAND_OUT_M
        return None

    # In-scope building set: canonical building_footprint features (POPPLAN-01).
    # OSM-linked footprints key by OSM id; plateau-only footprints (UUID,
    # no way/node/relation id) key by canonical feature id. The old
    # trailing-digits match would also catch digit-ending UUIDs.
    bld_ids = set()
    for f in world_ir["canonical_features"]:
        if f.get("semantic_class") != "building_footprint":
            continue
        m = re.search(r"[:/](way|node|relation)/(\d+)$", f.get("feature_id", ""))
        if m:
            bld_ids.add(int(m.group(2)))
        else:
            bld_ids.add(f.get("feature_id", ""))

    # Road centerline segments with a 10 m spatial grid for front search.
    road_segs = []
    for c in carriers.values():
        if not c["carrier_class"].startswith("road:"):
            continue
        for a, b in zip(c["polyline"], c["polyline"][1:]):
            if a != b:
                road_segs.append((a, b))
    GRID_CELL = 10.0
    road_grid = defaultdict(list)
    for i, (a, b) in enumerate(road_segs):
        x0 = int(min(a[0], b[0]) // GRID_CELL)
        x1 = int(max(a[0], b[0]) // GRID_CELL)
        y0 = int(min(a[1], b[1]) // GRID_CELL)
        y1 = int(max(a[1], b[1]) // GRID_CELL)
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                road_grid[(gx, gy)].append(i)

    def segs_near(x0, y0, x1, y1, pad):
        gx0 = int((min(x0, x1) - pad) // GRID_CELL)
        gx1 = int((max(x0, x1) + pad) // GRID_CELL)
        gy0 = int((min(y0, y1) - pad) // GRID_CELL)
        gy1 = int((max(y0, y1) + pad) // GRID_CELL)
        out = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                out.update(road_grid.get((gx, gy), ()))
        return out

    def edge_road_dist(ax, ay, bx, by):
        idx = segs_near(ax, ay, bx, by, FRONT_SEARCH_RADIUS_M)
        if not idx:
            return float("inf")
        a, b = (ax, ay), (bx, by)
        return math.sqrt(min(seg_seg_dist2(a, b, road_segs[i][0],
                                           road_segs[i][1]) for i in idx))

    def ring_clip(a, b):
        """Clip segment ab to the AOI ring. Both endpoints in-ring passes
        through; one endpoint out is replaced by a bisected boundary point.
        Returns (p, q) or None when no in-ring point exists."""
        ain = point_in_ring(a[0], a[1], ring)
        bin_ = point_in_ring(b[0], b[1], ring)
        if ain and bin_:
            return a, b
        if not ain and not bin_:
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            if not point_in_ring(mid[0], mid[1], ring):
                return None
            # Midpoint in-ring but endpoints out: bisect each half.
            lo, hi = mid, a
            for _ in range(50):
                m = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
                if point_in_ring(m[0], m[1], ring):
                    lo = m
                else:
                    hi = m
            pa = lo
            lo, hi = mid, b
            for _ in range(50):
                m = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
                if point_in_ring(m[0], m[1], ring):
                    lo = m
                else:
                    hi = m
            return pa, lo
        pin, pout = (a, b) if ain else (b, a)
        lo, hi = pin, pout
        for _ in range(50):
            m = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
            if point_in_ring(m[0], m[1], ring):
                lo = m
            else:
                hi = m
        return (pin, lo) if ain else (lo, pin)

    # Street-facing front per building: longest polygon edge whose distance
    # to the nearest road carrier is within 12 m. Interior lots (no road
    # within 12 m) take the edge nearest any road. Edges whose midpoint is
    # outside the AOI ring never carry a run; runs are clipped to the ring.
    front_edge = {}
    building_edges = {}
    interior_lots = set()
    for key, c in carriers.items():
        if c["carrier_class"] != "building" or not c["polyline"]:
            continue
        poly = c["polyline"]
        edges = []
        for a, b in zip(poly, poly[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L < 1e-9:
                continue
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            edges.append({
                "a": a, "b": b, "len": L, "mid": mid,
                "d": edge_road_dist(a[0], a[1], b[0], b[1]),
                "mid_in": point_in_ring(mid[0], mid[1], ring),
            })
        if not edges:
            continue
        usable = [e for e in edges if e["mid_in"]]
        qual = [e for e in usable if e["d"] <= FRONT_SEARCH_RADIUS_M]
        if qual:
            front = max(qual, key=lambda e: e["len"])
        else:
            if usable:
                front = min(usable, key=lambda e: (e["d"], -e["len"]))
            else:
                qall = [e for e in edges
                        if e["d"] <= FRONT_SEARCH_RADIUS_M]
                if qall:
                    front = max(qall, key=lambda e: e["len"])
                else:
                    front = min(edges, key=lambda e: (e["d"], -e["len"]))
            if c["carrier_id"] in bld_ids:
                interior_lots.add(c["carrier_id"])
        front_edge[key] = front
        building_edges[key] = edges

    # Use-class resolution source per building carrier (POPPLAN-01):
    # "claim" (commercial place claim or canonical place proximity),
    # "tags" (OSM tags, only for features with OSM observations),
    # "default" (residential fallback, only from the facade DEFAULT fact),
    # "excluded" (no claim, no tags, no default fact: excluded row only).
    use_class_source = {}
    use_class_excluded = set()

    def facade_use_class(key):
        """Building use class: canonical claims first, then OSM tags."""
        g = carrier_geo[key]
        tags = g["tags"]
        if g.get("claim_shop"):
            use_class_source[key] = "claim"
            return "commercial"
        if g["near_place"]:
            use_class_source[key] = "claim"
            return "commercial"
        if g.get("tag_shop"):
            use_class_source[key] = "tags"
            return "commercial"
        # FL-4876 USECLASS-01. PLATEAU's own free-text description of the
        # building, read only where the word is unambiguous. This is the
        # same kind of normalization world_ir_ingest_osm_extract already
        # performs on OSM site tags ("normalized use class from the
        # retained OSM site tag"); the source here is verbatim Japanese
        # rather than a tag, and nothing is inferred from the numeric
        # bldg:usage code, whose codelist this bundle does not ship.
        #
        # A garage, a storage shed, a bicycle shed and a warehouse are
        # outbuildings, and this table already calls a warehouse "service".
        # Before this, every one of them was given a house front with a
        # roofed entry, because an untyped building falls through to the
        # residential default. Measured on this tile: 240 storage sheds,
        # 212 garages, 61 bicycle sheds, 28 warehouses.
        _note = " ".join(str(v) for v in (g.get("plateau_notes") or {}).values())
        if _note:
            if any(word in _note for word in
                   ("車庫", "物置", "倉庫", "自転車置場", "ガレージ",
                    "プレハブ", "給水タンク", "配電室")):
                use_class_source[key] = "plateau_note"
                return "service"
            if any(word in _note for word in ("売店", "店舗")):
                use_class_source[key] = "plateau_note"
                return "commercial"
        b = (tags.get("building") or "")
        if b in ("warehouse", "industrial"):
            use_class_source[key] = "tags"
            return "service"
        if b == "apartments":
            use_class_source[key] = "tags"
            return "mixed"
        if b in ("commercial", "retail"):
            use_class_source[key] = "tags"
            return "commercial"
        by_use = (facade_defaults or {}).get("by_use", {})
        if (by_use.get("residential") or {}).get("value") is not None:
            use_class_source[key] = "default"
            return "residential"
        use_class_excluded.add(key)
        use_class_source[key] = "excluded"
        return "residential"

    def facade_module_for(key):
        """Archetype from facade_archetype_defaults_by_use (FL-4809).

        Fail-closed like road_class_defaults.bollard_spacing_m: a missing
        DEFAULT fact raises naming the fact instead of silently changing
        archetype selection through hard-coded literals.
        """
        use_class = facade_use_class(key)
        table = (facade_defaults or {}).get("by_use", {})
        if not table:
            raise ValueError(
                "rules lack facade_archetype_defaults_by_use.by_use: "
                "no archetype selection without the DEFAULT fact")
        entry = table.get(use_class) or table.get("residential", {})
        value = entry.get("value")
        if value is None:
            raise ValueError(
                "rules facade_archetype_defaults_by_use.by_use has no value "
                f"for use class {use_class!r} (or its residential fallback)")
        return value, use_class

    def facade_variant_for(module_id, seg_len_m):
        """Variant by measured front-run length tier (rules table)."""
        tiers = ((facade_defaults or {})
                 .get("variant_length_tiers_m", {}).get(module_id))
        if tiers:
            for tier in tiers:
                mx = tier["max_length_m"]
                if mx == "inf" or seg_len_m < float(mx):
                    return tier["variant"]
        return (catalog_by_id.get(module_id, {}).get("variants")
                or ["default"])[0]

    def facade_rule_source(span_index, module_id, use_class):
        """Evidence string naming the facade DEFAULT table."""
        entry = ((facade_defaults or {}).get("by_use", {})
                 .get(use_class, {}))
        return (f"facade_archetype_defaults_by_use {use_class}->"
                f"{module_id} [{entry.get('source', 'DEFAULT:authoring')}]; "
                f"{rule_source(span_index, 'setback', module_id)}")
    # Use-class pre-pass (POPPLAN-01): resolve every in-scope building so
    # the receipt counts claim/tag/default/excluded sources even for
    # observation-filled slots that never call facade_module_for.
    for _pkey in sorted(carriers, key=str):
        if carriers[_pkey]["carrier_class"] == "building":
            facade_use_class(_pkey)

    def span_params(span_index, subject):
        return rule_spans[span_index]["subjects"][subject]["parameters"]

    def rule_source(span_index, subject, module_id):
        params = span_params(span_index, subject)
        pname = {"road_structure": "carriageway_width_m",
                 "accessibility": "tactile_paving_width_m",
                 "marking_form": "crosswalk_marking",
                 "sign_form": "sign_mounting_height_m",
                 "setback": "setback_distance_from_centerline_m",
                 "railway_boundary": "rail_boundary_fence_height_m",
                 "occupation": "utility_pole_spacing_basis"}[subject]
        p = params[pname]
        d = density.get(module_id, 0.0)
        return (f"{SUBJECT_DESC[subject]} {pname} {p['value']} {p['unit']} "
                f"[{p['source']}]; prior {d:.6f}/m")

    def basis_source(span_index, subject):
        """Granting source string, or None if DEFAULT:not_designated alone."""
        params = span_params(span_index, subject)
        pname = {"road_structure": "carriageway_width_m",
                 "accessibility": "tactile_paving_width_m",
                 "marking_form": "crosswalk_marking",
                 "sign_form": "sign_mounting_height_m",
                 "setback": "setback_distance_from_centerline_m",
                 "railway_boundary": "rail_boundary_fence_height_m",
                 "occupation": "utility_pole_spacing_basis"}[subject]
        return params[pname]["source"]

    def mount_height(span_index):
        try:
            return float(span_params(span_index, "sign_form")
                         ["sign_mounting_height_m"]["value"])
        except (KeyError, TypeError, ValueError):
            return 2.5

    def setback_positive(span_index):
        try:
            ps = span_params(span_index, "setback")
            return any(float(ps[k]["value"]) > 0 for k in ps)
        except (KeyError, TypeError, ValueError):
            return False

    def sidewalk_positive(span_index):
        try:
            return float(span_params(span_index, "road_structure")
                         ["sidewalk_width_m"]["value"]) > 0
        except (KeyError, TypeError, ValueError):
            return False

    def compatible(module_id, key):
        """Carrier-class compatibility gate. Returns (ok, reason_if_not)."""
        g = carrier_geo[key]
        cclass = carriers[key]["carrier_class"]
        if module_id in ROAD_EDGE_MODS:
            if not cclass.startswith("road:"):
                return False, "road_edge_module_road_only"
            if module_id in GUARDRAIL_MODS and not (
                    sidewalk_positive(g["span_index"] or 0)
                    or g["near_crossing"] or g["rail_adjacent"]):
                return False, "guardrail_needs_sidewalk_or_crossing_or_rail"
        if module_id in RAIL_MODS or module_id.startswith("rail_"):
            if not cclass.startswith("railway"):
                return False, "rail_module_requires_railway_carrier"
        if module_id == "rail_track_corridor":
            # FL-4876 RAIL-TRACK-01: compile_world_ir_infrastructure models
            # every rail centreline as one continuous track. A rule run here
            # was a second, differently drawn track on top of it (4 runs, up
            # to 74 m). Observed runs are untouched by this gate.
            return False, "rail_track_owned_by_infrastructure"
        if module_id == "platform_railing":
            # RAILEDGE-01: no platform carrier class exists in this AOI
            # (building, road, railway, waterway, barrier only); rule
            # tiles require a station_platform* carrier, so none emit.
            # Observations are untouched by this gate.
            return False, "platform_railing_needs_platform_carrier"
        if is_drain_module(module_id):
            if not g["is_waterway"]:
                return False, "drain_module_waterway_only"
            if g["is_culvert"]:
                return False, "culvert_gets_no_open_channel"
        if is_commercial_module(module_id):
            if cclass != "building":
                return False, "commercial_module_building_only"
            if not (g["has_shop"] or g["near_place"]):
                return False, "commercial_module_needs_shop_or_place"
        if module_id in RESIDENTIAL_WALL_MODS or (
                "fence" in module_id.lower() and module_id not in RAIL_MODS
                and not module_id.startswith("rail_")):
            if cclass != "building":
                return False, "boundary_module_building_only"
            if g["has_shop"]:
                return False, "boundary_module_non_shop_only"
            if not setback_positive(g["span_index"] or 0):
                return False, "boundary_module_needs_positive_setback"
            if not g["street_facing"]:
                return False, "boundary_module_street_edge_only"
        if module_id in SIDEWALK_MODS or any(
                k in module_id.lower() for k in ("curb", "gutter", "tactile")):
            if not (cclass.startswith("road:") or cclass == "sidewalk"):
                return False, "sidewalk_module_road_only"
            if not sidewalk_positive(g["span_index"] or 0):
                return False, "sidewalk_module_needs_positive_width"
        return True, ""

    out_carriers = {key: [] for key in carriers}
    obs_anyfam_at = defaultdict(list)
    dropped_outside = []
    obs_count = 0

    def inst_family(inst, accepted):
        mid = inst.get("module_id")
        if mid and mid in catalog_by_id:
            reqs = catalog_by_id[mid].get("placement_requirements") or []
            hit = sorted(set(reqs) & set(accepted))
            if hit:
                return hit[0]
            if reqs:
                return sorted(reqs)[0]
            return "unclassified"
        return "custom_identity"

    # 1. Entity records only (FL-4876 ENTITY-01). Observations never create
    # geometry: --instances is resolve_identity_entities.py's
    # entity_instances.json, one record per canonical entity. Observation
    # conservation is that stage's invariant (identity_entities.json), not
    # this one's; every ENTITY record appears exactly once in the plan.
    _not_entities = [i.get("instance_id") for i in instances if not i.get("entity_id")]
    if _not_entities:
        raise SystemExit(
            "FL-4876 ENTITY-01: population input holds raw observations "
            f"({len(_not_entities)}, e.g. {_not_entities[:3]}); pass "
            "resolve_identity_entities.py entity_instances.json")
    # PLAN-06: the ring bounds rule emission, not evidence. An observation
    # inside the terrain bbox is on the map and is placed at its
    # coordinates; only observations outside the terrain bbox are listed
    # as dropped (with coordinates in the summary).
    for inst in instances:
        cid = inst.get("carrier_id")
        key = cid if cid is not None else f"unzoned-{inst['instance_id']}"
        if not (OBS_TERRAIN_BBOX["min_e"] <= inst["E"]
                <= OBS_TERRAIN_BBOX["max_e"]
                and OBS_TERRAIN_BBOX["min_n"] <= inst["N"]
                <= OBS_TERRAIN_BBOX["max_n"]):
            dropped_outside.append(inst["instance_id"])
            continue
        mid = inst.get("module_id")
        subj = inst.get("custom_identity_subject")
        if mid and mid in catalog_by_id:
            mod_key, cus_key = mid, None
        else:
            mod_key = None
            cus_key = subj or mid or \
                f"unresolved_{inst.get('population_role', 'instance')}_{inst['instance_id']}"
        text = mid or subj or ""
        height = plan_height(text, subj,
                             mount_height(carrier_geo[key]["span_index"] or 0))
        placement = {"placement_id": f"obs-{inst['instance_id']}"}
        if mod_key is not None:
            placement["module_id"] = mod_key
        else:
            placement["custom_identity_subject"] = cus_key
            if str(cus_key).startswith("unresolved_"):
                placement["population_disposition"] = {
                    "state": "intentional_omission",
                    "category": "insufficient_identity_evidence",
                    "reason": (
                        "unresolved reviewed observation is retained as "
                        "evidence debt; generic placeholder geometry is not a "
                        "lawful reconstruction"),
                }
        placement.update({
            "variant": inst.get("variant") or "default",
            "E": inst["E"], "N": inst["N"],
            "height_m": height, "yaw_deg": inst.get("yaw_deg") or 0.0,
            "evidence": {"kind": "observation",
                         "annotation_ids": inst.get("annotation_ids") or [],
                         "instance_id": inst["instance_id"],
                         "entity_id": inst.get("entity_id"),
                         "consumed_instance_ids": inst.get("consumed_instance_ids") or [],
                         "position_uncertainty_m": inst.get(
                             "position_uncertainty_m"),
                         "position_uncertainty_basis": inst.get(
                             "position_uncertainty_basis")},
        })
        # FL-4876 FACING-01: an authored fixture's reviewer recorded which way
        # it faces (manual_review.orientation_basis). That facing is the
        # owner's, so the carrier-bearing rederivations below and the
        # attachment pose keep it.
        _authored_facing = (inst.get("manual_review") or {}).get(
            "orientation_basis")
        if (_authored_facing and inst.get("yaw_deg") is not None
                and mod_key not in _LINEAR_LANE_MODULES):
            placement["evidence"]["authored_orientation_basis"] = str(
                _authored_facing)
        _manual_spatial = _spatial_decision_for(
            _authored_spatial_decisions, "placement",
            placement["placement_id"])
        if _manual_spatial is not None:
            placement["evidence"]["authored_spatial_decision"] = {
                "decision_id": _manual_spatial["decision_id"],
                "action": _manual_spatial["action"],
                "rationale": _manual_spatial["rationale"],
            }
        anchor = inst.get("anchor_segment")
        authored_run_extent_m = inst.get("run_length_m")
        run_extent_m = (float(authored_run_extent_m)
                        if authored_run_extent_m is not None
                        else obs_run_extent_m)
        if (run_extent_m and mod_key is not None
                and mod_key in OBS_RUN_FORM and is_continuous(mod_key)
                and isinstance(anchor, dict)):
            try:
                ax0, ay0 = float(anchor["E0"]), float(anchor["N0"])
                ax1, ay1 = float(anchor["E1"]), float(anchor["N1"])
            except (KeyError, TypeError, ValueError):
                ax0 = ay0 = ax1 = ay1 = None
            if ax0 is not None and (ax0, ay0) != (ax1, ay1):
                adx, ady = ax1 - ax0, ay1 - ay0
                alen = math.hypot(adx, ady)
                hx = adx / alen * run_extent_m / 2.0
                hy = ady / alen * run_extent_m / 2.0

                def _anchor_clamp(px, py):
                    t = ((px - ax0) * adx + (py - ay0) * ady) / (alen * alen)
                    t = max(0.0, min(1.0, t))
                    return (ax0 + t * adx, ay0 + t * ady)

                c0 = _anchor_clamp(inst["E"] - hx, inst["N"] - hy)
                c1 = _anchor_clamp(inst["E"] + hx, inst["N"] + hy)
                clen = math.hypot(c1[0] - c0[0], c1[1] - c0[1])
                if clen >= 1.0:
                    placement["run"] = {
                        "E0": c0[0], "N0": c0[1],
                        "E1": c1[0], "N1": c1[1],
                        "length_m": round(clen, 3)}
                    placement["yaw_deg"] = math.degrees(math.atan2(
                        c1[1] - c0[1], c1[0] - c0[0]))
                    placement["anchor_segment"] = {
                        "E0": ax0, "N0": ay0, "E1": ax1, "N1": ay1}
                    obs_runs_created += 1
                    if clen < run_extent_m - 1e-9:
                        obs_runs_anchor_clipped += 1
        # OBSYAW-02: observation POINT placements of carrier-aligned
        # modules take the attached carrier bearing at the nearest point
        # (mod 180, observed sense kept), mirroring the OBSRUN-02 run
        # re-derivation. Free-orientation modules (poles, trees, cabinets)
        # and custom-identity subjects keep the registered yaw. E/N never
        # change here.
        if ("run" not in placement and mod_key is not None
                and "authored_orientation_basis" not in placement["evidence"]):
            _cc = carriers[key]["carrier_class"]
            _lat, _al, _fo = module_surface_policy(
                mod_key, _cc, catalog_by_id)
            if resolve_lateral(_lat, _cc) in ALIGNED_LATERALS:
                _refs = _carrier_bearings_at(
                    carriers[key]["polyline"], inst["E"], inst["N"])
                if _refs:
                    _reg = float(inst.get("yaw_deg") or 0.0)
                    _best = min(_refs,
                                key=lambda _r: _yaw_err_deg(_reg, _r))
                    while _best - _reg > 90.0:
                        _best -= 180.0
                    while _reg - _best > 90.0:
                        _best += 180.0
                    placement["yaw_deg"] = _best
                    obs_point_yaw_rederived += 1
        out_carriers[key].append(placement)
        if mod_key is not None:
            reqs = frozenset(catalog_by_id[mod_key].get("placement_requirements") or [])
        else:
            reqs = frozenset({"custom_identity"})
        obs_anyfam_at[key].append((inst["E"], inst["N"], reqs))
        obs_count += 1
    assert obs_count + len(dropped_outside) == len(instances), \
        "every registered instance is placed or listed as dropped"

    # Buildings whose facade slot is already filled by an in-ring
    # observation: a custom-identity placement or a facade_archetype module.
    # The archetype run is skipped there (no double facade); the slot still
    # counts as filled.
    obs_facade_filled = set()
    for key, plist in out_carriers.items():
        for p in plist:
            if p["evidence"]["kind"] != "observation":
                continue
            if p.get("population_disposition"):
                continue
            if ("custom_identity_subject" in p
                    or p.get("module_id") in FACADE_ARCHETYPE_MODS):
                obs_facade_filled.add(key)
                # FL-4876 FACADE-C1. Say WHICH placement fills the slot.
                #
                # This set already knew a building's facade slot was filled by
                # an observation, and suppressed the rule run accordingly --
                # but it kept that knowledge to itself. Downstream, dispatch
                # routes any custom_identity_subject to owner "custom", and
                # its wants_facade test requires owner == "fixtures", so the
                # placement bound to the building's PRIMARY representation row
                # (terrain_mass) instead of its mesh_facade row. The gate then
                # reported five buildings as having no facade while a measured
                # storefront panel stood on the named face: Cocokara Fine,
                # FamilyMart, and three storefront sign clusters, each with an
                # anchor_kind of building_face and a real canonical_face_id.
                #
                # The stamp carries the fact to the stage that needs it rather
                # than asking that stage to re-derive it from owner and module
                # domain, which is what got it wrong.
                #
                # The stamp deliberately does NOT test the anchor here:
                # attachments are not resolved until far later in this
                # function, so at this line every placement's attachment is
                # still absent and an anchor test could only ever be false.
                # (It was, in the first version of this repair: zero
                # placements were stamped and the plan came back unchanged.)
                # dispatch_population_plan enforces the building_face
                # requirement at the point the anchor actually exists, so a
                # freestanding custom object still cannot claim a facade.
                if "custom_identity_subject" in p:
                    p["facade_slot"] = {
                        "role": "observed_facade",
                        "host_id": key,
                        "basis": "observation fills the building facade slot; "
                                 "the archetype run is suppressed here",
                        "anchor_checked_by": "dispatch_population_plan."
                                             "partition",
                    }
                break

    # 2. Evidence-bounded rule placements: one module per family slot.
    empty_pairs = []
    rule_count = 0
    rule_dropped = 0
    run_count = 0
    run_length_total = 0.0
    point_count = 0
    # PLAN-05 structural bookkeeping (filled by bilateral emission, then by
    # the structural stage after run normalisation).
    bilateral_sides = {}
    structural_short_skipped = Counter()
    per_carrier_n = Counter()
    module_rule_counts = Counter()
    emitted_module_per_carrier = defaultdict(set)

    def poly_in_ring(poly):
        return [p for p in poly if point_in_ring(p[0], p[1], ring)]

    # Crossing equipment: one placement per crossing node on the nearest
    # railway carrier within range.
    for nxi, (nx, ny) in enumerate(crossing_pts):
        if not point_in_ring(nx, ny, ring):
            rule_dropped += 1
            continue
        best, best_d2 = None, CROSSING_RADIUS_M ** 2
        for key, c in carriers.items():
            if not c["carrier_class"].startswith("railway"):
                continue
            for vx, vy in c["polyline"]:
                d2 = (nx - vx) ** 2 + (ny - vy) ** 2
                if d2 < best_d2:
                    best, best_d2 = key, d2
        if best is None:
            rule_dropped += 1
            continue
        sp = carrier_geo[best]["span_index"] or 0
        xreqs = set(catalog_by_id["level_crossing_equipment"].get(
            "placement_requirements") or [])
        if any(xreqs & reqs and (nx - ox) ** 2 + (ny - oy) ** 2 < OBS_WIN_M ** 2
               for ox, oy, reqs in obs_anyfam_at.get(best, [])):
            rule_dropped += 1  # observation already evidences this node
            continue
        out_carriers[best].append({
            "placement_id": f"rule-crossing-node-{nxi}",
            "module_id": "level_crossing_equipment",
            "variant": (catalog_by_id["level_crossing_equipment"].get("variants")
                        or ["default"])[0],
            "E": nx, "N": ny, "height_m": 0.0,
            "yaw_deg": _carrier_bearing_at(
                carriers[best]["polyline"], nx, ny) or 0.0,
            "evidence": {"kind": "rule",
                         "rule_source": rule_source(sp, "marking_form",
                                                    "level_crossing_equipment"),
                         "instance_id": None},
        })
        rule_count += 1
        module_rule_counts["level_crossing_equipment"] += 1
        per_carrier_n[best] += 1
        emitted_module_per_carrier[best].add("level_crossing_equipment")

    facade_stats = {"runs": 0, "clipped": 0, "dropped": 0,
                    "obs_filled": 0, "fallback_edge": 0,
                    "in_scope_unclipped": 0}
    # Boundary straddlers with no >= 1 m in-ring face: unfillable slots,
    # falsifier-exempt and counted in the summary (keys of `carriers`).
    facade_unfillable = set()
    facade_class_counts = Counter()
    facade_height_basis = Counter()
    boundary_run_count = 0
    storefront_run_count = 0
    sign_point_count = 0

    def emit_building_slots(key, c, span_index):
        """Slot rule per building carrier (NUMABE-PLAN-04).

        Slot 1 (facade): exactly one facade_archetype run on the
        street-facing front edge, clipped to the AOI ring. Buildings with
        an in-ring custom-identity or facade_archetype observation keep
        the observed facade and count as filled (no double facade).
        Slot 2 (boundary): residential buildings with positive setback get
        one boundary-family run as today. Slot 3 (commercial):
        shop/amenity/office-tagged buildings get one generic_storefront
        run on the front edge plus one sign point. Side/rear edges get
        nothing. Every in-scope building ends with a filled facade slot.
        """
        nonlocal rule_count, run_count, run_length_total, point_count
        nonlocal rule_dropped, module_rule_counts
        nonlocal boundary_run_count, storefront_run_count, sign_point_count
        g = carrier_geo[key]
        cid = c["carrier_id"]
        sp = span_index or 0
        front = front_edge.get(key)
        if key in obs_facade_filled:
            facade_stats["obs_filled"] += 1
        elif front is not None:
            mod, cls = facade_module_for(key)
            # Longest clippable piece wins: the street-facing front first,
            # then other edges longest-first. A sub-1.0 m stub never fills
            # the slot (the structural stage would drop it); boundary
            # straddlers thus land on the longest in-ring edge piece.
            cands = [front] + sorted(
                (e for e in building_edges.get(key, []) if e is not front),
                key=lambda e: -e["len"])
            seg = None
            seg_edge = front
            for e in cands:
                cand = ring_clip(e["a"], e["b"])
                if cand is None:
                    continue
                cand_len = math.hypot(cand[1][0] - cand[0][0],
                                      cand[1][1] - cand[0][1])
                if cand_len >= MIN_KEEP_M:
                    seg, seg_edge = cand, e
                    break
                if seg is None:
                    seg, seg_edge = cand, e
            if seg is not None and seg_edge is not front:
                facade_stats["fallback_edge"] += 1
            seg_len = 0.0
            if seg is not None:
                seg_len = math.hypot(seg[1][0] - seg[0][0],
                                     seg[1][1] - seg[0][1])
            if (seg is None or seg_len < MIN_KEEP_M) and cid in bld_ids:
                # FL-4876 AOISCOPE-01. The AOI ring selects which buildings
                # are in the product; it is not a knife applied to one that
                # is. Measured on this lane: all five straddlers here carry
                # bake_height and a bake row, and their baked footprint is
                # the *whole* canonical ring, unclipped -- the mass already
                # stands at full size in the world. Clipping only the facade
                # left five buildings whose walls exist and whose fronts do
                # not, and the blocker said the facade was unbuildable when
                # what was unbuildable was a sliver nobody needed.
                #
                # MIN_KEEP_M is untouched and the ring is untouched. The
                # facade is simply measured on the same geometry the mass
                # was baked from.
                _full = max([front] + list(cands), key=lambda e: e["len"])
                if _full["len"] >= MIN_KEEP_M - 1e-9:
                    seg, seg_edge = (_full["a"], _full["b"]), _full
                    seg_len = _full["len"]
                    facade_stats["in_scope_unclipped"] += 1
            if seg is None or seg_len < MIN_KEEP_M:
                # Boundary straddler with no fillable in-ring face: an
                # in-scope slot cannot be built with >= 1 m of real
                # geometry, so it is recorded unfillable
                # (falsifier-exempt, counted below) instead of papered
                # with a sliver run the structural stage would drop.
                # Out-of-scope carriers keep the existing dropped path.
                if cid in bld_ids:
                    facade_unfillable.add(key)
                facade_stats["dropped"] += 1
                rule_dropped += 1
                empty_pairs.append({
                    "carrier_id": cid,
                    "module_family": "facade_archetype",
                    "reason": "no_fillable_facade_face_in_ring"})
            else:
                (e0, n0), (e1, n1) = seg
                if seg[0] != seg_edge["a"] or seg[1] != seg_edge["b"]:
                    facade_stats["clipped"] += 1
                h, hbasis = facade_height_for(g["tags"])
                mx, my = (e0 + e1) / 2, (n0 + n1) / 2
                yaw = math.degrees(math.atan2(n1 - n0, e1 - e0))
                out_carriers[key].append({
                    "placement_id": f"rule-facade-{cid}",
                    "module_id": mod,
                    "variant": facade_variant_for(mod, seg_len),
                    "E": mx, "N": my,
                    "height_m": h, "yaw_deg": yaw,
                    "run": {"E0": e0, "N0": n0, "E1": e1, "N1": n1,
                            "length_m": round(seg_len, 3)},
                    "evidence": {
                        "kind": "rule",
                        "rule_source": facade_rule_source(sp, mod, cls),
                        "instance_id": None},
                })
                rule_count += 1
                run_count += 1
                run_length_total += seg_len
                module_rule_counts[mod] += 1
                per_carrier_n[key] += 1
                emitted_module_per_carrier[key].add(mod)
                facade_stats["runs"] += 1
                facade_class_counts[cls] += 1
                facade_height_basis[hbasis] += 1
        cands = []
        for family in c["families"]:
            subject = family_basis(family)
            if subject is None or not subject_applies(
                    subject, c["carrier_class"]):
                continue
            mod = winner(family)
            if mod is None or mod == "level_crossing_equipment":
                continue
            if mod in emitted_module_per_carrier[key]:
                continue
            if not (is_boundary_run_mod(mod) and is_continuous(mod)):
                continue
            ok, _ = compatible(mod, key)
            if not ok:
                continue
            # A default setback constrains where a boundary object could go;
            # it is not evidence that a hedge, wall, or fence exists.  Only a
            # designated rule fact may synthesize a boundary run.  Directly
            # observed boundary runs were already admitted upstream.
            if basis_source(sp, subject) == "DEFAULT:not_designated":
                empty_pairs.append({
                    "carrier_id": cid,
                    "module_family": family,
                    "reason": "default_not_designated_no_boundary_presence"})
                continue
            if not c["polyline"]:
                continue
            cands.append((family, mod))
        if cands:
            cands.sort(key=lambda fm: (-freq.get(fm[1], 0), fm[1]))
            family, mod = cands[0]
            inring = poly_in_ring(c["polyline"])
            if len(inring) < 2:
                rule_dropped += 1
            else:
                e0, n0 = inring[0]
                e1, n1 = inring[-1]
                length = poly_length(inring)
                mid = inring[len(inring) // 2]
                run_reqs = set(catalog_by_id[mod].get(
                    "placement_requirements") or [])
                if any(run_reqs & reqs
                       and (mid[0] - ox) ** 2 + (mid[1] - oy) ** 2
                       < OBS_WIN_M ** 2
                       for ox, oy, reqs in obs_anyfam_at.get(key, [])):
                    rule_dropped += 1
                    empty_pairs.append({
                        "carrier_id": cid, "module_family": family,
                        "reason": "observation_covers_edge"})
                else:
                    i = min(len(inring) // 2, len(inring) - 2)
                    a, b = inring[i], inring[i + 1]
                    yaw = math.degrees(math.atan2(n1 - n0, e1 - e0)) \
                        if (e0, n0) != (e1, n1) else 0.0
                    out_carriers[key].append({
                        "placement_id": f"rule-{cid}-{family}",
                        "module_id": mod,
                        "variant": (catalog_by_id[mod].get("variants")
                                    or ["default"])[0],
                        "E": mid[0], "N": mid[1],
                        "height_m": plan_height(mod, None,
                                                mount_height(sp)),
                        "yaw_deg": yaw,
                        "run": {"E0": e0, "N0": n0, "E1": e1,
                                "N1": n1,
                                "length_m": round(length, 3)},
                        "evidence": {
                            "kind": "rule",
                            "rule_source": rule_source(
                                sp, "setback", mod),
                            "instance_id": None},
                    })
                    rule_count += 1
                    run_count += 1
                    run_length_total += length
                    module_rule_counts[mod] += 1
                    per_carrier_n[key] += 1
                    emitted_module_per_carrier[key].add(mod)
                    boundary_run_count += 1
        tags = g["tags"] or {}
        if any(k in tags for k in ("shop", "amenity", "office")):
            if front is not None:
                seg = ring_clip(front["a"], front["b"])
                seg_len = (math.hypot(seg[1][0] - seg[0][0],
                                      seg[1][1] - seg[0][1])
                           if seg is not None else 0.0)
                if seg is not None and seg_len >= 1e-9:
                    (e0, n0), (e1, n1) = seg
                    mx, my = (e0 + e1) / 2, (n0 + n1) / 2
                    yaw = math.degrees(math.atan2(n1 - n0, e1 - e0))
                    out_carriers[key].append({
                        "placement_id": f"rule-storefront-{cid}",
                        "module_id": STOREFRONT_RUN_MOD,
                        "variant": (catalog_by_id[STOREFRONT_RUN_MOD].get(
                            "variants") or ["default"])[0],
                        "E": mx, "N": my,
                        "height_m": plan_height(STOREFRONT_RUN_MOD, None,
                                                mount_height(sp)),
                        "yaw_deg": yaw,
                        "run": {"E0": e0, "N0": n0, "E1": e1,
                                "N1": n1,
                                "length_m": round(seg_len, 3)},
                        "evidence": {
                            "kind": "rule",
                            "rule_source": rule_source(
                                sp, "setback", STOREFRONT_RUN_MOD),
                            "instance_id": None},
                    })
                    rule_count += 1
                    run_count += 1
                    run_length_total += seg_len
                    module_rule_counts[STOREFRONT_RUN_MOD] += 1
                    per_carrier_n[key] += 1
                    emitted_module_per_carrier[key].add(STOREFRONT_RUN_MOD)
                    storefront_run_count += 1
                    out_carriers[key].append({
                        "placement_id": f"rule-sign-{cid}-0",
                        "module_id": SIGN_POINT_MOD,
                        "variant": (catalog_by_id[SIGN_POINT_MOD].get(
                            "variants") or ["default"])[0],
                        "E": mx, "N": my,
                        "height_m": plan_height(SIGN_POINT_MOD, None,
                                                mount_height(sp)),
                        "yaw_deg": yaw,
                        "evidence": {
                            "kind": "rule",
                            "rule_source": rule_source(
                                sp, "sign_form", SIGN_POINT_MOD),
                            "instance_id": None},
                    })
                    rule_count += 1
                    point_count += 1
                    module_rule_counts[SIGN_POINT_MOD] += 1
                    per_carrier_n[key] += 1
                    emitted_module_per_carrier[key].add(SIGN_POINT_MOD)
                    sign_point_count += 1

    pole_generic_bypassed = 0
    for key in sorted(carriers, key=str):
        c = carriers[key]
        span_index = carrier_geo[key]["span_index"]
        if c["carrier_class"] == "building":
            if key in use_class_excluded:
                # No claim, no tags, no residential DEFAULT fact: an
                # excluded row only (POPPLAN-01). Observations still win
                # through their own loop; only rule slots are skipped.
                empty_pairs.append({
                    "carrier_id": c["carrier_id"],
                    "module_family": "facade_archetype",
                    "reason": "no_use_class_claim_tag_or_default"})
                rule_dropped += 1
            else:
                emit_building_slots(key, c, span_index)
            continue
        for family in c["families"]:
            subject = family_basis(family)
            if subject is None or not subject_applies(subject, c["carrier_class"]):
                reason = ("no_rule_basis_for_family"
                          if subject is None
                          else "subject_not_applicable_to_carrier_class")
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family, "reason": reason})
                continue
            mod = winner(family)
            if mod is None:
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family,
                                    "reason": "no_catalog_module_for_family"})
                continue
            if mod == "level_crossing_equipment":
                continue  # handled at crossing nodes only
            if mod == "utility_pole_wire_cluster":
                pole_generic_bypassed += 1
                continue  # POLE-01: observed-first + gap fill own this module
            # FL-4876 GUARDRAIL-OBSERVED-01: pedestrian guardrails exist only where a photo shows them.
            # Continuous modules skip the designation-basis test below, so the two road-edge families
            # (sidewalk_and_ramp_edge, crosswalk_and_carriageway_edge) put a synthesized guardrail on
            # nearly every road side, twice per span (build_reg_75: 544 rule runs against ~14 observed;
            # operator 2026-10-16 "hand rails everywhere ... including in front of my home"). Observed
            # and hand-authored guardrails still come through the observation path.
            if mod in OBSERVED_ONLY_MODULES:
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family,
                                    "reason": "observed_only_module"})
                continue
            if mod in emitted_module_per_carrier[key]:
                continue  # one module per carrier served by shared placement
            ok, reason = compatible(mod, key)
            if not ok:
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family, "reason": reason})
                continue
            src = basis_source(span_index or 0, subject)
            if src == "DEFAULT:not_designated" and not is_continuous(mod):
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family,
                                    "reason": "default_not_designated_no_basis"})
                continue
            if not c["polyline"]:
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family,
                                    "reason": "carrier_has_no_geometry"})
                continue
            if is_continuous(mod):
                inring = poly_in_ring(c["polyline"])
                if len(inring) < 2:
                    rule_dropped += 1
                    continue
                e0, n0 = inring[0]
                e1, n1 = inring[-1]
                length = poly_length(inring)
                mid = inring[len(inring) // 2]
                run_reqs = set(catalog_by_id[mod].get("placement_requirements")
                               or [])
                if any(run_reqs & reqs
                       and (mid[0] - ox) ** 2 + (mid[1] - oy) ** 2 < OBS_WIN_M ** 2
                       for ox, oy, reqs in obs_anyfam_at.get(key, [])):
                    rule_dropped += 1
                    empty_pairs.append({"carrier_id": c["carrier_id"],
                                        "module_family": family,
                                        "reason": "observation_covers_edge"})
                    continue
                # PLAN-05: road-edge laterals leave the centreline. A
                # centreline-lateral road run (centre dashes) keeps one
                # centreline run; every other road lateral is emitted per
                # side from Surfaces.offset_polyline (pieces already clipped
                # out of junctions) as straight >= 1.0 m sub-segments, so
                # the run-normalisation pass sees chord == length and keeps
                # the offset geometry untouched.
                lateral, _allow, _forbid = module_surface_policy(
                    mod, c["carrier_class"], catalog_by_id)
                lateral = resolve_lateral(lateral, c["carrier_class"])
                bilateral = (c["carrier_class"].startswith("road:")
                             and lateral in ("curb_line", "curb_inset",
                                             "hardware", "sidewalk_band"))
                # RAILEDGE-01: rail_edge rule runs leave the carrier
                # centreline exactly like curb_line runs: per side from
                # Surfaces.offset_polyline at the rail half-width, whose
                # pieces arrive already clipped out of every other
                # track's band, so only corridor outer edges survive.
                bilateral_rail = (
                    c["carrier_class"].startswith("railway")
                    and lateral == "rail_edge")
                if bilateral or bilateral_rail:
                    if bilateral_rail:
                        dist = surf.rail_half_m()
                    else:
                        dist = road_lateral_distance(
                            c["carrier_id"], lateral)
                    made_runs = 0
                    side_pieces = {"L": 0, "R": 0}
                    for side, tag in (("left", "L"), ("right", "R")):
                        # ROAD-XSECTION-01: each side from its own edge.
                        side_dist = dist if bilateral_rail else \
                            road_lateral_distance(c["carrier_id"], lateral,
                                                  side)
                        try:
                            pieces = surf.offset_polyline(
                                c["carrier_id"], side, side_dist)
                        except KeyError:
                            pieces = []
                        gi = 0
                        for piece in pieces:
                            # Greedy straight merge: accumulate vertices
                            # while the chord stays within 0.5 m of path
                            # length, so the normalisation pass (which keeps
                            # chord == length runs untouched) preserves the
                            # offset geometry instead of re-cutting chords.
                            merged = []
                            acc = [piece[0]]
                            acc_len = 0.0
                            for vi in range(len(piece) - 1):
                                a, b = piece[vi], piece[vi + 1]
                                step = math.hypot(b[0] - a[0], b[1] - a[1])
                                acc.append(b)
                                acc_len += step
                                chord = math.hypot(
                                    acc[-1][0] - acc[0][0],
                                    acc[-1][1] - acc[0][1])
                                # RAILEDGE-01: rail runs hold a tighter
                                # chord (lateral sagitta <= 0.25 m) so the
                                # emitted run cannot dip inside
                                # rail_half - 0.5 m on inner curves. Road
                                # behaviour is untouched (dev stays 0.0).
                                # FL-4876 ROAD-XSECTION-01: road runs also
                                # cap the sagitta (ROAD_RUN_MAX_SAGITTA_M); a
                                # 41 m chord on way/37661731 passed the 0.5 m
                                # length test while its midpoint stood 2.2 m
                                # inside the bend.
                                dev = 0.0
                                if len(acc) > 2:
                                    x0, y0 = acc[0]
                                    x1, y1 = acc[-1]
                                    L2 = (x1 - x0) ** 2 + (y1 - y0) ** 2
                                    if L2 > 1e-12:
                                        inv = 1.0 / math.sqrt(L2)
                                        for qx, qy in acc[1:-1]:
                                            dd = abs((qy - y0) * (x1 - x0)
                                                     - (qx - x0) * (y1 - y0)) \
                                                * inv
                                            if dd > dev:
                                                dev = dd
                                if (acc_len - chord > 0.5
                                        and len(acc) > 2) or dev > (
                                            0.25 if bilateral_rail
                                            else ROAD_RUN_MAX_SAGITTA_M):
                                    merged.append(acc[:-1])
                                    acc = [a, b]
                                    acc_len = step
                            if len(acc) >= 2:
                                merged.append(acc)
                            # FL-4876 CORNER-BOLLARD-01: a corner/crosswalk
                            # point module stands at the piece ends (pieces
                            # arrive clipped out of junction discs), not
                            # along the whole curb: 3880 chevron bollards
                            # lined every street about 1.7 m apart.
                            if (family in CORNER_FAMILIES
                                    and mod in LINEAR_RUN_POINT_MODULES):
                                merged = _corner_ends(merged, CORNER_REACH_M)
                            for pts in merged:
                                a, b = pts[0], pts[-1]
                                seg_l = math.hypot(b[0] - a[0],
                                                   b[1] - a[1])
                                if seg_l < MIN_KEEP_M:
                                    structural_short_skipped[mod] += 1
                                    continue
                                seg = ring_clip(a, b)
                                if seg is None:
                                    rule_dropped += 1
                                    continue
                                (sa0, sn0), (sa1, sn1) = seg
                                seg_l = math.hypot(sa1 - sa0, sn1 - sn0)
                                if seg_l < MIN_KEEP_M:
                                    structural_short_skipped[mod] += 1
                                    continue
                                mx, my = (sa0 + sa1) / 2, (sn0 + sn1) / 2
                                yaw = math.degrees(math.atan2(
                                    sn1 - sn0, sa1 - sa0))
                                out_carriers[key].append({
                                    "placement_id":
                                        f"rule-{c['carrier_id']}-{family}-"
                                        f"{tag}-g{gi:02d}",
                                    "module_id": mod,
                                    "variant": (catalog_by_id[mod].get(
                                        "variants") or ["default"])[0],
                                    "E": mx, "N": my,
                                    "height_m": plan_height(
                                        mod, None,
                                        mount_height(span_index or 0)),
                                    "yaw_deg": yaw,
                                    "run": {
                                        "E0": sa0, "N0": sn0,
                                        "E1": sa1, "N1": sn1,
                                        "length_m": round(seg_l, 3)},
                                    "evidence": {
                                        "kind": "rule",
                                        "rule_source": rule_source(
                                            span_index or 0, subject, mod),
                                        "instance_id": None},
                                })
                                gi += 1
                                made_runs += 1
                                side_pieces[tag] += 1
                                run_length_total += seg_l
                                module_rule_counts[mod] += 1
                    if made_runs == 0:
                        rule_dropped += 1
                        empty_pairs.append({
                            "carrier_id": c["carrier_id"],
                            "module_family": family,
                            "reason": "offset_run_fully_clipped"})
                        continue
                    rule_count += made_runs
                    run_count += made_runs
                    per_carrier_n[key] += 1
                    emitted_module_per_carrier[key].add(mod)
                    bilateral_sides[(c["carrier_id"], mod)] = {
                        "family": family,
                        "L": side_pieces["L"], "R": side_pieces["R"]}
                    continue
                i = min(len(inring) // 2, len(inring) - 2)
                a, b = inring[i], inring[i + 1]
                yaw = math.degrees(math.atan2(n1 - n0, e1 - e0)) \
                    if (e0, n0) != (e1, n1) else 0.0
                placement = {
                    "placement_id": f"rule-{c['carrier_id']}-{family}",
                    "module_id": mod,
                    "variant": (catalog_by_id[mod].get("variants")
                                or ["default"])[0],
                    "E": mid[0], "N": mid[1],
                    "height_m": plan_height(mod, None,
                                            mount_height(span_index or 0)),
                    "yaw_deg": yaw,
                    "run": {"E0": e0, "N0": n0, "E1": e1, "N1": n1,
                            "length_m": round(length, 3)},
                    "evidence": {
                        "kind": "rule",
                        "rule_source": rule_source(span_index or 0, subject, mod),
                        "instance_id": None},
                }
                out_carriers[key].append(placement)
                rule_count += 1
                run_count += 1
                run_length_total += length
                module_rule_counts[mod] += 1
                per_carrier_n[key] += 1
                emitted_module_per_carrier[key].add(mod)
                continue
            # Point module: density-bounded count along the carrier.
            d = density.get(mod, 0.0)
            if d <= 0:
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family,
                                    "reason": "no_density_basis"})
                continue
            n = round(carrier_len[key] * d)
            if n <= 0:
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family,
                                    "reason": "no_density_basis"})
                continue
            mod_reqs = set(catalog_by_id[mod].get("placement_requirements") or [])
            made = 0
            inring = poly_in_ring(c["polyline"])
            inL = poly_length(inring) if len(inring) >= 2 else 0.0
            if inL <= 0:
                rule_dropped += 1
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family,
                                    "reason": "observation_wins_or_outside_ring"})
                emitted_module_per_carrier[key].add(mod)
                continue
            for i in range(1, n + 1):
                base = (i / (n + 1)) * inL
                placed_pt = False
                for off in (0.0, 0.02 * inL, -0.02 * inL, 0.05 * inL,
                            -0.05 * inL, 0.10 * inL, -0.10 * inL,
                            0.25 * inL, -0.25 * inL):
                    target = min(max(base + off, 0.0), inL)
                    acc, ex, ey, yaw = 0.0, None, None, 0.0
                    for a, b in zip(inring, inring[1:]):
                        seg = math.hypot(b[0] - a[0], b[1] - a[1])
                        if acc + seg >= target:
                            t = 0.0 if seg == 0 else (target - acc) / seg
                            ex = a[0] + t * (b[0] - a[0])
                            ey = a[1] + t * (b[1] - a[1])
                            yaw = math.degrees(math.atan2(b[1] - a[1],
                                                          b[0] - a[0]))
                            break
                        acc += seg
                    if ex is None:
                        continue
                    clash = any(
                        mod_reqs & reqs
                        and (ex - ox) ** 2 + (ey - oy) ** 2 < OBS_WIN_M ** 2
                        for ox, oy, reqs in obs_anyfam_at.get(key, []))
                    if clash:
                        continue
                    out_carriers[key].append({
                        "placement_id": f"rule-{c['carrier_id']}-{family}-{made}",
                        "module_id": mod,
                        "variant": (catalog_by_id[mod].get("variants")
                                    or ["default"])[0],
                        "E": ex, "N": ey,
                        "height_m": plan_height(mod, None,
                                                mount_height(span_index or 0)),
                        "yaw_deg": yaw,
                        "evidence": {
                            "kind": "rule",
                            "rule_source": rule_source(span_index or 0, subject, mod),
                            "instance_id": None},
                    })
                    rule_count += 1
                    point_count += 1
                    module_rule_counts[mod] += 1
                    per_carrier_n[key] += 1
                    made += 1
                    placed_pt = True
                    break
                if not placed_pt:
                    rule_dropped += 1
            emitted_module_per_carrier[key].add(mod)
            if made == 0:
                empty_pairs.append({"carrier_id": c["carrier_id"],
                                    "module_family": family,
                                    "reason": "observation_wins_or_outside_ring"})

    # 3. OSM evidence: open waterway channels as runs; presence for carriers
    # with geometry but no other placement is accounted as a non-population
    # disposition.  Carrier existence is canonical topology, not a request to
    # author a generic sign-shaped object. Culverts get no open channel.
    osm_count = 0
    carrier_presence_only = []
    for key in sorted(carriers, key=str):
        g = carrier_geo[key]
        if g["is_waterway"] and not g["is_culvert"] and carriers[key]["polyline"]:
            inring = poly_in_ring(carriers[key]["polyline"])
            if len(inring) >= 2 and not any(
                    p.get("module_id") == "stone_lined_water_channel"
                    for p in out_carriers[key]):
                e0, n0 = inring[0]
                e1, n1 = inring[-1]
                length = poly_length(inring)
                mid = inring[len(inring) // 2]
                out_carriers[key].append({
                    "placement_id": f"osm-{carriers[key]['carrier_id']}-channel",
                    "module_id": "stone_lined_water_channel",
                    "variant": "default",
                    "E": mid[0], "N": mid[1], "height_m": 0.0,
                    "yaw_deg": math.degrees(math.atan2(n1 - n0, e1 - e0)),
                    "run": {"E0": e0, "N0": n0, "E1": e1, "N1": n1,
                            "length_m": round(length, 3)},
                    "evidence": {"kind": "osm",
                                 "osm_id": str(carriers[key]["carrier_id"]),
                                 "instance_id": None},
                })
                osm_count += 1
                per_carrier_n[key] += 1
    for key in sorted(carriers, key=str):
        if out_carriers[key] or not carriers[key]["polyline"]:
            continue
        xs = [p[0] for p in carriers[key]["polyline"]]
        ys = [p[1] for p in carriers[key]["polyline"]]
        ex, ey = sum(xs) / len(xs), sum(ys) / len(ys)
        if not point_in_ring(ex, ey, ring):
            rule_dropped += 1
            continue
        carrier_presence_only.append({
            "carrier_id": carriers[key]["carrier_id"],
            "carrier_class": carriers[key]["carrier_class"],
            "state": "canonical_topology_only",
            "reason": "no independent population requirement",
        })

    # FL-4813 run normalisation (root, 2026-09-17): a run record is ONE straight
    # segment. Boundary runs on buildings take the street-facing front edge
    # (never the closed ring, which lowered as a straight hedge through the
    # block); runs built from multi-vertex polylines (roads, water, rail) are
    # split per polyline segment so no run cuts a chord across a curve.
    def _seg_place(p, a, b, idx=None):
        q = dict(p)
        e0, n0 = a
        e1, n1 = b
        seg_l = math.hypot(e1 - e0, n1 - n0)
        q["E"], q["N"] = (e0 + e1) / 2, (n0 + n1) / 2
        q["yaw_deg"] = math.degrees(math.atan2(n1 - n0, e1 - e0))
        q["run"] = {"E0": e0, "N0": n0, "E1": e1, "N1": n1,
                    "length_m": round(seg_l, 3)}
        if idx is not None:
            q["placement_id"] = f"{p['placement_id']}-s{idx:02d}"
        return q
    run_split_segments = 0
    run_front_fixed = 0
    for key, plist in list(out_carriers.items()):
        cc = carriers[key]
        rebuilt = []
        for p in plist:
            r = p.get("run")
            if not r:
                rebuilt.append(p)
                continue
            chord = math.hypot(r["E1"] - r["E0"], r["N1"] - r["N0"])
            if abs(chord - float(r["length_m"])) <= 0.5 and chord >= 1e-9:
                rebuilt.append(p)
                continue
            if cc["carrier_class"] == "building":
                fe = front_edge.get(key)
                seg = ring_clip(fe["a"], fe["b"]) if fe else None
                if seg is None or math.hypot(seg[1][0] - seg[0][0],
                                             seg[1][1] - seg[0][1]) < 1e-9:
                    rule_dropped += 1
                    empty_pairs.append({"carrier_id": cc["carrier_id"],
                                        "module_family": p.get("module_id"),
                                        "reason": "no_front_edge_for_run"})
                    continue
                rebuilt.append(_seg_place(p, seg[0], seg[1]))
                run_front_fixed += 1
            else:
                pts = poly_in_ring(cc["polyline"]) or []
                segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)
                        if math.hypot(pts[i + 1][0] - pts[i][0],
                                      pts[i + 1][1] - pts[i][1]) >= 1.0]
                if not segs:
                    rule_dropped += 1
                    continue
                for i, (a, b) in enumerate(segs):
                    rebuilt.append(_seg_place(p, a, b, i))
                run_split_segments += len(segs)
        out_carriers[key] = rebuilt
        # per-carrier cap counts the modules, not geometric pieces:
        # bilateral (-L/-R), geometry (-gNN), split (-sNN) suffixes collapse
        per_carrier_n[key] = len({base_cap_key(pp["placement_id"])
                                   for pp in rebuilt
                                   if pp["evidence"]["kind"] != "observation"})
    print(f"[plan] run normalisation: front-edge fixes {run_front_fixed}, "
          f"split segments {run_split_segments}")

    # PLAN-05 structural stage: every placement is checked against the
    # surfaces around it. Runs pass through Surfaces.clip_segment against
    # their forbidden set and only surviving sub-segments (>= 1.0 m) are
    # kept; a point intersecting its forbidden set moves along its lateral
    # rule or is dropped. A building-face run whose samples sit on its own
    # carrier ring coheres to the face (the face IS the building); same for
    # building-context points near their own ring.
    structural = {
        "policy_version": STRUCTURAL_POLICY_VERSION,
        "surfaces_source": SURFACES_SOURCE,
        "checked": 0, "moved": 0, "clipped_segments": 0, "dropped": 0,
        "by_module": {}, "residual_violations": 0, "residual_detail": [],
        "dropped_observation_ids": [],
        "observation_surface_conflicts": [],
        "observation_runs": {"created": 0, "kept_as_point": 0,
                               "clipped": 0, "extent_source": ""},
        "scale_band_violations": 0, "scale_detail": [],
        "short_segments_skipped": {},
    }

    def _stat(mod, field, n=1):
        d = structural["by_module"].setdefault(
            mod, {"moved": 0, "clipped": 0, "dropped": 0,
                  "residual": 0})
        d[field] += n

    def _modname(p):
        return p.get("module_id") or p.get(
            "custom_identity_subject", "unknown")

    def _near_own_face(E, N, key, tol):
        if carriers[key]["carrier_class"] != "building":
            return False
        ring = carriers[key]["polyline"]
        if not ring or len(ring) < 2:
            return False
        tol2 = tol * tol
        return min(seg_dist2(E, N, a[0], a[1], b[0], b[1])
                   for a, b in zip(ring, ring[1:])) <= tol2

    def _placement_surfaces(p, key, lateral):
        raw = set(surf.point_surfaces(p["E"], p["N"]))
        # Own-carrier coherence: the zones ring is the carrier's own
        # geometry, so near-face contact reads as the building itself -
        # but never over a carriageway/junction/rail/water hit, which the
        # root re-measurement sees raw.
        if carriers[key]["carrier_class"] == "building" and \
                _near_own_face(p["E"], p["N"], key, FACE_SNAP_M) and \
                not (raw & {"carriageway", "junction", "rail",
                            "water"}):
            return {"building"}
        return raw

    # Footway tracks for the crossing cut come from the shared export
    # (surfaces-backed); no second copy lives here.
    tracks = _conform_footway_tracks(surf)

    def _policy_of(p, key):
        cclass = carriers[key]["carrier_class"]
        lateral, allowed, forbidden = module_surface_policy(
            p.get("module_id"), cclass, catalog_by_id)
        return resolve_lateral(lateral, cclass), allowed, forbidden

    def _nearest_on_polyline(E, N, poly):
        best = None
        for a, b in zip(poly, poly[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if L2 == 0.0:
                continue
            t = max(0.0, min(1.0, ((E - a[0]) * dx + (N - a[1]) * dy) / L2))
            cx, cy = a[0] + t * dx, a[1] + t * dy
            d2 = (E - cx) ** 2 + (N - cy) ** 2
            inv = 1.0 / math.sqrt(L2)
            if best is None or d2 < best[0]:
                best = (d2, cx, cy, -dy * inv, dx * inv)
        return best

    def _along_polyline(poly, E, N, delta):
        """Point delta metres along poly from the vertex nearest (E,N)."""
        cum = [0.0]
        for a, b in zip(poly, poly[1:]):
            cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        hit = _nearest_on_polyline(E, N, poly)
        if hit is None:
            return None
        _d2, cx, cy, nx, ny = hit
        # arclength of the nearest point: project hit onto segments
        best_s, best_d2 = 0.0, float("inf")
        for i, (a, b) in enumerate(zip(poly, poly[1:])):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if L2 == 0.0:
                continue
            t = max(0.0, min(1.0, ((cx - a[0]) * dx
                                   + (cy - a[1]) * dy) / L2))
            qx, qy = a[0] + t * dx, a[1] + t * dy
            d2 = (qx - cx) ** 2 + (qy - cy) ** 2
            if d2 < best_d2:
                best_d2, best_s = d2, cum[i] + t * math.sqrt(L2)
        target = min(max(best_s + delta, 0.0), cum[-1])
        for i, (a, b) in enumerate(zip(poly, poly[1:])):
            if cum[i + 1] >= target:
                span = cum[i + 1] - cum[i]
                u = 0.0 if span == 0.0 else (target - cum[i]) / span
                dx, dy = b[0] - a[0], b[1] - a[1]
                L = math.hypot(dx, dy)
                if L == 0.0:
                    return (a[0], a[1], 0.0, 0.0)
                return (a[0] + u * dx, a[1] + u * dy,
                        -dy / L, dx / L)
        return None

    def _centreline_snap(p):
        """Nearest true-road centreline point within 4 m, else None."""
        best = None
        for occ in carriers.values():
            if occ["carrier_class"] not in ("road:primary",
                    "road:tertiary", "road:residential",
                    "road:unclassified", "road:service"):
                continue
            hit = _nearest_on_polyline(p["E"], p["N"], occ["polyline"])
            if hit is None:
                continue
            if best is None or hit[0] < best[0]:
                best = hit
        if best is None or best[0] > 16.0:
            return None
        return (best[1], best[2])

    def _move_point(p, key, lateral, allowed, forbidden, is_obs=False):
        """Candidate positions along the lateral rule; first passing
        candidate wins, else None (drop)."""
        cclass = carriers[key]["carrier_class"]
        cid = carriers[key]["carrier_id"]
        cands = []
        # Building interior first: a face swallowed by a wide carriageway
        # band escapes inward while staying on the recorded carrier.
        interior = []
        if cclass == "building":
            ring = carriers[key]["polyline"]
            if ring:
                gx = sum(q[0] for q in ring) / len(ring)
                gy = sum(q[1] for q in ring) / len(ring)
                dx, dy = gx - p["E"], gy - p["N"]
                L = math.hypot(dx, dy)
                if L > 1e-9:
                    step = min(0.75, L * 0.9)
                    interior = [(p["E"] + dx / L * step,
                                 p["N"] + dy / L * step)]
        if cclass.startswith("road:") and lateral in (
                "hardware", "sidewalk_band", "curb_line", "curb_inset"):
            try:
                # ROAD-XSECTION-01: +normal is the left side.
                dist_l = road_lateral_distance(cid, lateral, "left")
                dist_r = road_lateral_distance(cid, lateral, "right")
            except KeyError:
                return None
            hit = _nearest_on_polyline(p["E"], p["N"],
                                       carriers[key]["polyline"])
            if hit is None:
                return None
            _d2, cx, cy, nx, ny = hit
            bases = [(cx, cy, nx, ny)]
            # Along-carrier bases walk out of junction discs while
            # staying on the recorded side of the recorded carrier.
            for delta in (8.0, -8.0):
                ab = _along_polyline(carriers[key]["polyline"],
                                     p["E"], p["N"], delta)
                if ab is not None:
                    bases.append(ab)
            cands = []
            # Near junctions the first offset can stay inside the disc;
            # wider trials keep the recorded side while clearing it.
            for bx, by, bnx, bny in bases:
                for factor in (1.0, 1.75, 2.5):
                    cands.append((bx + bnx * dist_l * factor,
                                  by + bny * dist_l * factor))
                    cands.append((bx - bnx * dist_r * factor,
                                  by - bny * dist_r * factor))
        elif cclass == "building" and lateral in (
                "building_front", "hardware", "sidewalk_band",
                "curb_line", "curb_inset"):
            # Roadside laterals zoned to a building resolve to the
            # recorded face (fixtures stand against the wall).
            fe = front_edge.get(key)
            if fe is None:
                return None
            (ax, ay), (bx, by) = fe["a"], fe["b"]
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            if L2 == 0.0:
                return None
            t = max(0.0, min(1.0, ((p["E"] - ax) * dx
                                   + (p["N"] - ay) * dy) / L2))
            cands = interior + [(ax + t * dx, ay + t * dy)]
        elif cclass == "building" and lateral == "building_ring":
            hit = _nearest_on_polyline(p["E"], p["N"],
                                       carriers[key]["polyline"])
            if hit is None:
                return None
            cands = interior + [(hit[1], hit[2])]
        elif cclass == "building" and lateral in ("building_ring", "keep") \
                and p.get("module_id") is None:
            # Custom-presence observations on buildings snap to their own
            # ring (the recorded carrier face), never dropped for position.
            hit = _nearest_on_polyline(p["E"], p["N"],
                                       carriers[key]["polyline"])
            if hit is None:
                return None
            cands = interior + [(hit[1], hit[2])]
        elif lateral == "centreline":
            # Paint and ironwork that live on the carriageway: snap to the
            # nearest true-road centreline within 4 m (survey tolerance /
            # footway mis-zoning); farther than that the position is not a
            # carriageway marking and the placement is dropped.
            snap = _centreline_snap(p)
            if snap is None:
                return None
            cands = [snap]
        elif cclass.startswith("railway") and lateral == "rail_edge":
            # RAILEDGE-01: rule rail_edge geometry comes from offset
            # emission now; the point-only ±out move is deleted for rule
            # placements (observations keep position via the conflict
            # path and never reach here).
            if not is_obs:
                return None
            hit = _nearest_on_polyline(p["E"], p["N"],
                                       carriers[key]["polyline"])
            if hit is None:
                return None
            out = 5.0 + RAIL_EDGE_OUT_M  # Surfaces DEFAULT rail half
            bases = [(hit[1], hit[2], hit[3], hit[4])]
            for delta in (8.0, -8.0):
                ab = _along_polyline(carriers[key]["polyline"],
                                     p["E"], p["N"], delta)
                if ab is not None:
                    bases.append(ab)
            cands = []
            for bx, by, bnx, bny in bases:
                cands.append((bx + bnx * out, by + bny * out))
                cands.append((bx - bnx * out, by - bny * out))
        elif cclass.startswith("railway") and lateral == "rail_centre":
            hit = _nearest_on_polyline(p["E"], p["N"],
                                       carriers[key]["polyline"])
            if hit is None:
                return None
            cands = [(hit[1], hit[2])]
        elif cclass == "waterway" and lateral == "water_edge":
            hit = _nearest_on_polyline(p["E"], p["N"],
                                       carriers[key]["polyline"])
            if hit is None:
                return None
            _d2, cx, cy, nx, ny = hit
            out = 1.5 + WATER_EDGE_OUT_M  # Surfaces DEFAULT water half
            cands = [(cx + nx * out, cy + ny * out),
                     (cx - nx * out, cy - ny * out)]
        elif cclass == "waterway" and lateral == "water_centre":
            hit = _nearest_on_polyline(p["E"], p["N"],
                                       carriers[key]["polyline"])
            if hit is None:
                return None
            cands = [(hit[1], hit[2])]
        else:
            return None  # overhead / keep / unresolvable: no move rule
        for qx, qy in cands:
            q = {"E": qx, "N": qy}
            s = _placement_surfaces(q, key, lateral)
            if is_obs:
                keep = observation_keep(s, forbidden)
            else:
                keep = not surfaces_violate(s, allowed, forbidden)
            if keep:
                return qx, qy
        return None

    def _face_coherent(p, key, lateral):
        if carriers[key]["carrier_class"] != "building":
            return False
        if lateral not in ("building_front", "building_ring"):
            return False
        r = p["run"]
        pts = ((r["E0"], r["N0"]), (r["E1"], r["N1"]),
               ((r["E0"] + r["E1"]) / 2, (r["N0"] + r["N1"]) / 2))
        return all(_near_own_face(q[0], q[1], key, FACE_COHERENCE_M)
                   for q in pts)

    def _set_run(p, q0, q1):
        seg_l = math.hypot(q1[0] - q0[0], q1[1] - q0[1])
        p["E"], p["N"] = (q0[0] + q1[0]) / 2, (q0[1] + q1[1]) / 2
        p["yaw_deg"] = math.degrees(math.atan2(q1[1] - q0[1],
                                                 q1[0] - q0[0]))
        p["run"] = {"E0": q0[0], "N0": q0[1], "E1": q1[0],
                      "N1": q1[1], "length_m": round(seg_l, 3)}

    def _obs_run_pieces(p, lateral, kinds, forbidden, a, b):
        """Rebuild one observation run (OBSRUN-02 owner): clip like a rule
        run, keep E/N at the observation (the run may be asymmetric), keep
        only pieces containing the observation point."""
        kept = _run_pieces(a, b, False, kinds, None, lateral, forbidden,
                           surf, tracks)
        E, N = p["E"], p["N"]
        out = []
        for (q0, q1) in kept:
            dx, dy = q1[0] - q0[0], q1[1] - q0[1]
            L2 = dx * dx + dy * dy
            if L2 == 0.0:
                inside = math.hypot(E - q0[0], N - q0[1]) <= 1e-6
            else:
                t = ((E - q0[0]) * dx + (N - q0[1]) * dy) / L2
                if t < -1e-9 or t > 1.0 + 1e-9:
                    continue
                cx, cy = q0[0] + t * dx, q0[1] + t * dy
                inside = math.hypot(E - cx, N - cy) <= 1e-6
            if not inside:
                continue
            q = dict(p)
            if out:
                q["placement_id"] = f"{p['placement_id']}-c{len(out):02d}"
            _set_run(q, q0, q1)
            q["E"], q["N"] = E, N
            out.append(q)
        return out

    for key, plist in list(out_carriers.items()):
        rebuilt = []
        cclass = carriers[key]["carrier_class"]
        own = own_carrier_surfaces(cclass)
        for p in plist:
            mod = _modname(p)
            lateral, allowed, forbidden = _policy_of(p, key)
            structural["checked"] += 1
            is_obs = p["evidence"]["kind"] == "observation"
            if "run" in p:
                r = p["run"]
                a, b = (r["E0"], r["N0"]), (r["E1"], r["N1"])
                if is_obs and isinstance(p.get("anchor_segment"), dict):
                    _pieces = _obs_run_pieces(p, lateral, forbidden - own,
                                              forbidden, a, b)
                    if _pieces:
                        structural["clipped_segments"] += len(_pieces)
                        _stat(mod, "clipped", len(_pieces))
                        rebuilt.extend(_pieces)
                    else:
                        _anc = p.get("anchor_segment") or {}
                        _qy = dict(p)
                        _qy.pop("run", None)
                        _qy["yaw_deg"] = math.degrees(math.atan2(
                            _anc["N1"] - _anc["N0"],
                            _anc["E1"] - _anc["E0"]))
                        rebuilt.append(_qy)
                        structural["observation_surface_conflicts"].append({
                            "instance_id": p["evidence"].get("instance_id"),
                            "module": mod, "E": p["E"], "N": p["N"],
                            "surfaces": sorted(surf.point_surfaces(
                                p["E"], p["N"]))})
                        obs_runs_kept_as_point += 1
                    continue
                if _face_coherent(p, key, lateral):
                    # The face is the building: own building/sidewalk/
                    # footway contact passes, carriageway/junction/rail/
                    # water only cuts where the face lies deeper than 1 m
                    # inside (model-edge graze survives), then the dense
                    # guard clears sub-sample dips.
                    # FL-4876 STATION-01. The exemption below already says
                    # the face is the building: its own contact passes. For
                    # a station building the rail corridor is its own
                    # context too -- PLATEAU labels both of these masses
                    # gen:説明注記 = 駅舎, "station building", and they lie
                    # 0.13 m and 0.105 m from the railway boundary because
                    # that is what a station is. Cutting their faces on rail
                    # contact left the station with no frontage at all.
                    #
                    # Narrow by construction: only a mass the source itself
                    # calls a station building, and only for its own face.
                    # rail_edge.min_own_offset_m is untouched, and every
                    # other carrier keeps the rule unchanged.
                    hard = forbidden - ({"building", "sidewalk", "footway"}
                                        | _station_own_context(key))
                    kept = _run_pieces(a, b, True, None, hard, lateral,
                                       forbidden, surf, tracks)
                    if len(kept) == 1 and _same_run(kept[0], r):
                        rebuilt.append(p)
                        continue
                    if not kept:
                        # Source conflict (face inside a measured/wide
                        # band): nudge toward the carrier interior just
                        # enough to exit, capped at 2.5 m, else drop.
                        # Candidates validate with the exact pipeline the
                        # residual check applies to the nudged geometry.
                        _bring = carriers[key]["polyline"]
                        gx = sum(q[0] for q in _bring) / len(_bring)
                        gy = sum(q[1] for q in _bring) / len(_bring)
                        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
                        dx, dy = gx - mx, gy - my
                        L = math.hypot(dx, dy)
                        if L > 1e-9:
                            ux, uy = dx / L, dy / L
                            for step in (1.0, 1.5, 2.0, 2.5):
                                na = (a[0] + ux * step, a[1] + uy * step)
                                nb = (b[0] + ux * step, b[1] + uy * step)
                                cand = _run_pieces(na, nb, False,
                                                   forbidden, hard,
                                                   lateral, forbidden,
                                                   surf, tracks)
                                if len(cand) == 1 and _same_run(
                                        cand[0],
                                        {"E0": na[0], "N0": na[1],
                                         "E1": nb[0], "N1": nb[1]}):
                                    a, b = cand[0]
                                    _set_run(p, a, b)
                                    rebuilt.append(p)
                                    structural["moved"] += 1
                                    _stat(mod, "moved")
                                    break
                            else:
                                structural["dropped"] += 1
                                _stat(mod, "dropped")
                                rule_dropped += 1
                            continue
                        structural["dropped"] += 1
                        _stat(mod, "dropped")
                        rule_dropped += 1
                        continue
                    if len(kept) == 1 and _same_run(kept[0], r):
                        rebuilt.append(p)
                        continue
                    if not kept:
                        structural["dropped"] += 1
                        _stat(mod, "dropped")
                        rule_dropped += 1
                        continue
                    structural["clipped_segments"] += len(kept)
                    _stat(mod, "clipped", len(kept))
                    for ci, (q0, q1) in enumerate(kept):
                        q = p if ci == 0 else dict(p)
                        if ci > 0:
                            q["placement_id"] = \
                                f"{p['placement_id']}-c{ci:02d}"
                        _set_run(q, q0, q1)
                        rebuilt.append(q)
                    continue
                elif is_obs:
                    # Observations: own-carrier contact is evidence, so
                    # only foreign forbidden kinds clip the run.
                    kinds = forbidden - own
                else:
                    kinds = forbidden
                kept = _run_pieces(a, b, False, kinds, None, lateral,
                                   forbidden, surf, tracks)
                if len(kept) == 1 and _same_run(kept[0], r):
                    rebuilt.append(p)
                    continue
                if not kept:
                    structural["dropped"] += 1
                    _stat(mod, "dropped")
                    rule_dropped += 1
                    continue
                structural["clipped_segments"] += len(kept)
                _stat(mod, "clipped", len(kept))
                for ci, (q0, q1) in enumerate(kept):
                    q = p if ci == 0 else dict(p)
                    if ci > 0:
                        q["placement_id"] = \
                            f"{p['placement_id']}-c{ci:02d}"
                    _set_run(q, q0, q1)
                    rebuilt.append(q)
            else:
                s = _placement_surfaces(p, key, lateral)
                if is_obs:
                    keep = observation_keep(s, forbidden)
                else:
                    keep = not surfaces_violate(s, allowed, forbidden)
                if keep:
                    # Markings live on the carriageway: an otherwise-kept
                    # RULE marking outside every carriageway snaps to the
                    # nearest true-road centreline (moved). Observations
                    # are evidence at their coordinates and are never
                    # moved (root patch: disagreements become
                    # observation_surface_conflicts instead).
                    if lateral == "centreline" and not is_obs and not (
                            s & {"carriageway", "junction"}):
                        snap = _centreline_snap(p)
                        if snap is not None:
                            p["E"], p["N"] = snap
                            structural["moved"] += 1
                            _stat(mod, "moved")
                    rebuilt.append(p)
                    continue
                if is_obs:
                    # An observation is evidence at its coordinates: it is
                    # never moved or dropped by the surface model. The
                    # disagreement is recorded against the model instead.
                    structural["observation_surface_conflicts"].append({
                        "instance_id": p["evidence"].get("instance_id"),
                        "module": mod, "E": p["E"], "N": p["N"],
                        "surfaces": sorted(s)})
                    rebuilt.append(p)
                    continue
                mv = _move_point(p, key, lateral, allowed, forbidden,
                                   is_obs)
                if mv is None:
                    structural["dropped"] += 1
                    _stat(mod, "dropped")
                    rule_dropped += 1
                    if is_obs:
                        structural["dropped_observation_ids"].append(
                            p["evidence"].get("instance_id"))
                    continue
                p["E"], p["N"] = mv
                structural["moved"] += 1
                _stat(mod, "moved")
                rebuilt.append(p)
        out_carriers[key] = rebuilt
    # Canonical mass clip (POPPLAN-02 DELIVER 2): rule runs that would
    # cross a canonical mass ring are clipped at the ring with named gap
    # rows.  Observed continuous placements are also clipped because their
    # three-metre run extent is synthesized from a point observation; the
    # observation proves presence, not either invented endpoint.  The source
    # run remains in evidence.spatial_reconciliation.  A fully swallowed
    # observed run becomes explicit attachment debt later, never a silent
    # drop.  Facade runs keep their face-specific attachment owner.
    _mass_rings = {}
    for _oid, (_ofeat, _oring) in canon_building_by_osm.items():
        _mass_rings[_ofeat.get("feature_id")] = _oring
    for _ofeat, _oring in canon_plateau_buildings:
        _mass_rings[_ofeat.get("feature_id")] = _oring
    _mass_order = sorted(_mass_rings)
    _mass_bbox = {}
    for _mfid in _mass_order:
        _mring = _mass_rings[_mfid]
        _mass_bbox[_mfid] = (
            min(p[0] for p in _mring), min(p[1] for p in _mring),
            max(p[0] for p in _mring), max(p[1] for p in _mring))
    def _mass_clip_windows(ax, ay, bx, by, skip_fid):
        """Kept subsegments of ab outside every non-host mass ring."""
        ts = [0.0, 1.0]
        abx0, abx1 = (ax, bx) if ax <= bx else (bx, ax)
        aby0, aby1 = (ay, by) if ay <= by else (by, ay)
        for _mfid in _mass_order:
            if _mfid == skip_fid:
                continue
            _x0, _y0, _x1, _y1 = _mass_bbox[_mfid]
            if abx1 < _x0 or abx0 > _x1 or aby1 < _y0 or aby0 > _y1:
                continue
            _ring = _mass_rings[_mfid]
            _cl = _ring if _ring[0] == _ring[-1] else _ring + [_ring[0]]
            for _ra, _rb in zip(_cl, _cl[1:]):
                _t = _seg_cross_t((ax, ay), (bx, by), _ra, _rb)
                if _t is not None and 0.0 < _t < 1.0:
                    ts.append(_t)
        ts = sorted(set(ts))
        kept, gaps = [], []
        for _t0, _t1 in zip(ts, ts[1:]):
            _mx = ax + (bx - ax) * (_t0 + _t1) / 2.0
            _my = ay + (by - ay) * (_t0 + _t1) / 2.0
            _hit = None
            for _mfid in _mass_order:
                if _mfid == skip_fid:
                    continue
                if point_in_ring(_mx, _my, _mass_rings[_mfid]):
                    _hit = _mfid
                    break
            if _hit is None:
                kept.append((_t0, _t1))
            else:
                gaps.append((_hit, _t0, _t1))
        return kept, gaps

    mass_clip_gaps = 0
    for _ckey in sorted(carriers, key=str):
        _cc = carriers[_ckey]
        _own_mass = None
        if _cc["carrier_class"] == "building":
            if isinstance(_cc["carrier_id"], int):
                _hit0 = canon_building_by_osm.get(_cc["carrier_id"])
                _own_mass = _hit0[0].get("feature_id") if _hit0 else None
            elif _cc["carrier_id"] in _mass_rings:
                _own_mass = _cc["carrier_id"]
        _new_list = []
        for _pl in out_carriers.get(_ckey, []):
            _run = _pl.get("run")
            _mod = _pl.get("module_id")
            _ev_kind = (_pl.get("evidence") or {}).get("kind")
            if (_ev_kind not in {"rule", "observation"}
                    or not isinstance(_run, dict)
                    or _mod in FACADE_ARCHETYPE_MODS):
                _new_list.append(_pl)
                continue
            _ax, _ay = float(_run["E0"]), float(_run["N0"])
            _bx, _by = float(_run["E1"]), float(_run["N1"])
            _source_run = dict(_run)
            if math.hypot(_bx - _ax, _by - _ay) <= 1e-9:
                _new_list.append(_pl)
                continue
            _kept, _gaps = _mass_clip_windows(_ax, _ay, _bx, _by, _own_mass)
            if not _gaps:
                _new_list.append(_pl)
                continue
            for _gmid, _gt0, _gt1 in _gaps:
                empty_pairs.append({
                    "carrier_id": _cc["carrier_id"],
                    "module_family": (catalog_by_id.get(_mod, {}).get("owning_domain")
                                      or "rule_run"),
                    "reason": (
                        f"mass_clip_gap:{_gmid}:canonical_mass_exclusion")})
                mass_clip_gaps += 1
                if _ev_kind == "rule":
                    rule_dropped += 1
            _pieces = []
            for _t0, _t1 in _kept:
                _qx0 = _ax + (_bx - _ax) * _t0
                _qy0 = _ay + (_by - _ay) * _t0
                _qx1 = _ax + (_bx - _ax) * _t1
                _qy1 = _ay + (_by - _ay) * _t1
                if math.hypot(_qx1 - _qx0, _qy1 - _qy0) >= MIN_KEEP_M:
                    _pieces.append(((_qx0, _qy0), (_qx1, _qy1)))
            # MASSCLIP-RECONF-01: a kept piece can end at a mass-ring
            # crossing that still sits inside a surface-model forbidden
            # kind (the two ring sources disagree at grazing corners;
            # observed as a 0.09 m building graze at the end of a curb run
            # the structural stage had already conformed). Rule pieces cut
            # by a mass gap are re-run through the structural conformance
            # pipeline so the final gate never sees a run the stage's own
            # _run_pieces would trim. Observations are evidence at their
            # coordinates and are never trimmed here.
            if _gaps and _ev_kind == "rule" and _pieces:
                _mc_lat, _, _ = module_surface_policy(
                    _mod, _cc["carrier_class"], catalog_by_id)
                _mc_lat = resolve_lateral(_mc_lat, _cc["carrier_class"])
                _rec, _re_clip, _re_dropped = reconform_mass_clip_pieces(
                    _pieces, module_id=_mod,
                    carrier_class=_cc["carrier_class"],
                    catalog_by_id=catalog_by_id, surf=surf, tracks=tracks,
                    face_coherent=(lambda _a, _b: _face_coherent(
                        {"E": (_a[0] + _b[0]) / 2, "N": (_a[1] + _b[1]) / 2,
                         "run": {"E0": _a[0], "N0": _a[1],
                                 "E1": _b[0], "N1": _b[1]}}, _ckey, _mc_lat)),
                    station_ctx=_station_own_context(_ckey))
                if _re_clip:
                    structural["clipped_segments"] += _re_clip
                    _stat(_mod, "clipped", _re_clip)
                if _re_dropped:
                    rule_dropped += _re_dropped
                    empty_pairs.append({
                        "carrier_id": _cc["carrier_id"],
                        "module_family": (catalog_by_id.get(_mod, {}).get(
                            "owning_domain") or "rule_run"),
                        "reason": "mass_clip_piece_nonconformant"})
                _pieces = _rec
            if not _pieces:
                if _ev_kind == "observation":
                    _pl["_attachment_debt_reason"] = \
                        "observed_synthesized_run_fully_inside_canonical_mass"
                    _pl.setdefault("evidence", {})["spatial_reconciliation"] = {
                        "kind": "canonical_mass_clip",
                        "source_run": _source_run,
                        "conflicting_mass_ids": sorted({g[0] for g in _gaps}),
                        "result": "debt:no_mass_clear_extent",
                    }
                    _new_list.append(_pl)
                continue
            for _pi, ((_qx0, _qy0), (_qx1, _qy1)) in enumerate(_pieces):
                _seglen = math.hypot(_qx1 - _qx0, _qy1 - _qy0)
                _qyaw = math.degrees(math.atan2(_qy1 - _qy0, _qx1 - _qx0))
                _qp = _pl if _pi == 0 else dict(_pl)
                _qp["run"] = dict(_source_run)
                if _pi > 0:
                    _qp["placement_id"] = f"{_pl['placement_id']}-c{_pi:02d}"
                _qp["run"]["E0"], _qp["run"]["N0"] = _qx0, _qy0
                _qp["run"]["E1"], _qp["run"]["N1"] = _qx1, _qy1
                _qp["run"]["length_m"] = round(_seglen, 3)
                _qp["E"], _qp["N"] = (_qx0 + _qx1) / 2.0, (_qy0 + _qy1) / 2.0
                _qp["yaw_deg"] = round(_qyaw, 3)
                if _ev_kind == "observation":
                    _qp.setdefault("evidence", {})["spatial_reconciliation"] = {
                        "kind": "canonical_mass_clip",
                        "source_run": _source_run,
                        "conflicting_mass_ids": sorted({g[0] for g in _gaps}),
                        "result": "clipped_synthesized_extent",
                    }
                _new_list.append(_qp)
    # Facade runs the structural stage dropped entirely (face on rail /
    # carriageway with no surviving piece and no successful nudge, e.g.
    # plateau-only rail-corridor footprints) leave an empty slot: record
    # unfillable like the emit-time boundary straddlers (POPPLAN-01).
    # Falsifier-exempt and counted via facade_unfillable_slots plus an
    # empty row each; never silent, never a sliver run. No-op when every
    # in-scope building keeps its archetype run (all OSM-compiled plans).
    for _skey in sorted(carriers, key=str):
        _scc = carriers[_skey]
        if _scc["carrier_class"] != "building":
            continue
        if _scc["carrier_id"] not in bld_ids:
            continue
        _splist = out_carriers.get(_skey, [])
        _sarch = any(
            p.get("module_id") in FACADE_ARCHETYPE_MODS and "run" in p
            and p.get("placement_id", "").startswith("rule-facade-")
            for p in _splist)
        if (not _sarch and _skey not in obs_facade_filled
                and _skey not in facade_unfillable):
            facade_unfillable.add(_skey)
            empty_pairs.append({
                "carrier_id": _scc["carrier_id"],
                "module_family": "facade_archetype",
                "reason": "structural_facade_run_dropped"})
    # Nominal dims and the scale-band (giant-bush) check. The catalog
    # carries no per-module dims, so nominals come from the band defaults
    # except facade heights, which use the computed facade value.
    for plist in out_carriers.values():
        for p in plist:
            entry = catalog_by_id.get(p.get("module_id") or "") or {}
            domain = entry.get("owning_domain") or "DEFAULT"
            band = DIMENSION_BANDS_M.get(domain, DIMENSION_BANDS_M["DEFAULT"])
            (hmin, hmax, hnom), (wmin, wmax, wnom), lmax = band
            if (domain == "facade_archetype" and "run" in p
                    and p["evidence"]["kind"] != "observation"):
                h = p["height_m"]
            else:
                h = hnom
            p["dims_m"] = {"height_m": h, "width_m": wnom}
            bad = not (hmin <= h <= hmax) or not (wmin <= wnom <= wmax)
            if "run" in p and p["run"]["length_m"] > lmax:
                bad = True
            if bad:
                structural["scale_band_violations"] += 1
                if len(structural["scale_detail"]) < 20:
                    structural["scale_detail"].append({
                        "placement_id": p["placement_id"],
                        "module": _modname(p), "dims_m": p["dims_m"]})
    # Residual verification through the shared export: the stage audits
    # with the same function the lowerers import. A bush in a carriageway
    # fails here, not in an eyeball.
    for key, plist in out_carriers.items():
        cclass = carriers[key]["carrier_class"]
        for p in plist:
            mod = _modname(p)
            p["carrier_class"] = cclass
            p["evidence_kind"] = p["evidence"]["kind"]
            if "run" in p:
                r = p["run"]
                v = placement_conforms(
                    surf, module_id=p.get("module_id"),
                    carrier_class=cclass,
                    evidence_kind=p["evidence"]["kind"],
                    run=((r["E0"], r["N0"]), (r["E1"], r["N1"])),
                    catalog_by_id=catalog_by_id,
                    own_context=_station_own_context(key))
            else:
                v = placement_conforms(
                    surf, module_id=p.get("module_id"),
                    carrier_class=cclass,
                    evidence_kind=p["evidence"]["kind"],
                    point=(p["E"], p["N"]),
                    catalog_by_id=catalog_by_id,
                    own_context=_station_own_context(key))
            ok = v["ok"]
            if not ok:
                structural["residual_violations"] += 1
                _stat(mod, "residual")
                if len(structural["residual_detail"]) < 20:
                    structural["residual_detail"].append({
                        "placement_id": p["placement_id"],
                        "module": mod,
                        "E": p["E"], "N": p["N"]})
    structural["short_segments_skipped"] = dict(
        sorted(structural_short_skipped.items()))
    structural["observation_runs"] = {
        "created": obs_runs_created,
        "kept_as_point": obs_runs_kept_as_point,
        "clipped": obs_runs_anchor_clipped,
        "extent_source": obs_run_extent_src,
        "point_yaw_rederived": obs_point_yaw_rederived,
    }
    orientation = audit_orientation(out_carriers, carriers, catalog_by_id)
    structural["orientation"] = orientation
    if orientation["violations"]:
        structural["residual_violations"] += orientation["violations"]
        for mod, d in sorted(orientation["by_module"].items()):
            for _ in range(d["violations"]):
                _stat(mod, "residual")
        for s in orientation["samples"]:
            if len(structural["residual_detail"]) < 20:
                structural["residual_detail"].append({
                    "placement_id": s["placement_id"],
                    "module": s["module"], "E": s["E"], "N": s["N"],
                    "orientation_err_deg": s["err_deg"]})
    print(f"[plan] structural: checked={structural['checked']} "
          f"moved={structural['moved']} "
          f"clipped_segments={structural['clipped_segments']} "
          f"dropped={structural['dropped']} "
          f"residual={structural['residual_violations']} "
          f"scale={structural['scale_band_violations']} "
          f"orientation_checked={orientation['checked']} "
          f"orientation_violations={orientation['violations']} "
          f"orientation_exempt={orientation['exempt_observations']} "
          f"orientation_anchor_saves={orientation.get('anchor_leg_saves', 0)}")
    for mod in sorted(structural["by_module"]):
        d = structural["by_module"][mod]
        if d["moved"] or d["clipped"] or d["dropped"] or d["residual"]:
            print(f"[plan] structural {mod}: moved={d['moved']} "
                  f"clipped={d['clipped']} dropped={d['dropped']} "
                  f"residual={d['residual']}")
    n_bi = len(bilateral_sides)
    n_both = sum(1 for v in bilateral_sides.values()
                 if v["L"] > 0 and v["R"] > 0)
    n_bl = sum(v["L"] for v in bilateral_sides.values())
    n_br = sum(v["R"] for v in bilateral_sides.values())
    print(f"[plan] bilateral road-edge: carrier_modules={n_bi} "
          f"both_sides={n_both} L_runs={n_bl} R_runs={n_br}")
    # Post-clip bilateral audit: per (carrier, module) side counts in the
    # final plan, with a cause for each one-sided pair (emission side
    # fully blocked by junctions/foreign carriageways, or every emitted
    # piece clipped away by buildings/junctions/short splits).
    import re as _re
    final_sides = defaultdict(lambda: {"L": 0, "R": 0})
    for key, plist in out_carriers.items():
        for pp in plist:
            if "run" not in pp:
                continue
            mod = pp.get("module_id")
            if mod not in ROAD_EDGE_MODS:
                continue
            cclass = carriers[key]["carrier_class"]
            lat, _a, _f = module_surface_policy(
                mod, cclass, catalog_by_id)
            if resolve_lateral(lat, cclass) not in ROAD_EDGE_LATERALS:
                continue
            m = _re.search(r"-(L|R)-g\d+(-c\d+)?$",
                           pp["placement_id"])
            if m:
                final_sides[(carriers[key]["carrier_id"],
                             mod)][m.group(1)] += 1
    bilateral_final_one_sided = []
    cid_to_key = {carriers[key]["carrier_id"]: key for key in carriers}
    for (cid, mod), v in sorted(final_sides.items(), key=str):
        if v["L"] > 0 and v["R"] > 0:
            continue
        missing = "R" if v["L"] > 0 else "L"
        side = "right" if missing == "R" else "left"
        try:
            key = cid_to_key[cid]
            cclass = carriers[key]["carrier_class"]
            lat, _a, _f = module_surface_policy(
                mod, cclass, catalog_by_id)
            lat = resolve_lateral(lat, cclass)
            dist = road_lateral_distance(cid, lat, side)
            emp = surf.offset_polyline(cid, side, dist)
            cause = ("emission_blocked" if not emp else "clipped_away")
        except KeyError:
            cause = "no_surface_width"
        bilateral_final_one_sided.append(
            {"carrier_id": cid, "module": mod,
             "missing_side": missing, "cause": cause,
             "kept": dict(v)})
    n_fboth = (len(final_sides) - len(bilateral_final_one_sided))
    print(f"[plan] bilateral final: pairs={len(final_sides)} "
          f"both_sides={n_fboth} "
          f"one_sided={len(bilateral_final_one_sided)}")
    # Recount module slots after clipping/drops (suffixes collapse).
    for key, plist in out_carriers.items():
        per_carrier_n[key] = len(
            {base_cap_key(pp["placement_id"]) for pp in plist
             if pp["evidence"]["kind"] != "observation"})
    assert structural["residual_violations"] == 0, \
        f"residual {structural['residual_detail'][:5]}"
    assert structural["scale_band_violations"] == 0, \
        f"scale {structural['scale_detail'][:5]}"

    # Falsifier gates.
    for key, plist in out_carriers.items():
        for p in plist:
            assert p["evidence"]["kind"] in ("observation", "osm", "rule"), p
            if p["evidence"]["kind"] == "rule" and "run" not in p:
                assert "; prior " in p["evidence"]["rule_source"], p
            if p["evidence"]["kind"] == "rule" and p.get("module_id") in ROAD_EDGE_MODS:
                assert carriers[key]["carrier_class"].startswith("road:"), p
    # Runaway-generation falsifier. Written when every carrier took one slot per module family (a flat 12);
    # point modules now take round(length x observed density) instances, so a flat cap fails long roads
    # for normal density (FIXTURE-PASS4: way/126233940, 1371 m, 20 rule slots = one per 69 m). The bound
    # scales with the carrier: at least 12, else one non-observation slot per 30 m of carrier.
    def _cap(k):
        return max(12, math.ceil(carrier_len.get(k, 0.0) / 30.0))
    over = sorted(((n, k) for k, n in per_carrier_n.items() if n > _cap(k)), reverse=True)
    max_per_carrier = max(per_carrier_n.values(), default=0)
    if over:  # name the carrier and its module slots so the cause is visible
        n, worst = over[0]
        slots = sorted({(base_cap_key(pp["placement_id"]), pp.get("module_id"), pp["evidence"]["kind"])
                        for pp in out_carriers[worst] if pp["evidence"]["kind"] != "observation"})
        raise AssertionError(f"{len(over)} carrier(s) over the length-scaled cap; worst {worst} "
                             f"({carriers[worst]['carrier_class']}, {carrier_len.get(worst, 0):.0f} m, "
                             f"cap {_cap(worst)}): {n} slots {slots[:30]}")
    # Facade-slot falsifiers: every in-scope building ends with exactly one
    # archetype facade run or an observation-filled facade, never two
    # archetype runs on one carrier, and every archetype run carries run
    # geometry on its front edge.
    bld_keys = [k for k, cc in carriers.items()
                if cc["carrier_class"] == "building"
                and cc["carrier_id"] in bld_ids]
    assert len(bld_keys) == len(bld_ids), \
        f"in-scope buildings {len(bld_keys)} != {len(bld_ids)}"
    for key in bld_keys:
        plist = out_carriers[key]
        n_arch = sum(1 for p in plist
                     if p.get("module_id") in FACADE_ARCHETYPE_MODS
                     and "run" in p
                     and p["placement_id"].startswith("rule-facade-"))
        assert n_arch <= 1, \
            f"building {carriers[key]['carrier_id']} has {n_arch} facade runs"
        assert n_arch == 1 or key in obs_facade_filled \
            or key in facade_unfillable, \
            f"building {carriers[key]['carrier_id']} has no facade slot: " \
            f"placements={[(pp['placement_id'], pp.get('module_id'), 'run' in pp) for pp in plist]}"
        for p in plist:
            if p["placement_id"].startswith("rule-facade-"):
                assert "run" in p, p
                assert p.get("module_id") in FACADE_ARCHETYPE_MODS, p
    n_bld_guard = sum(
        1 for key, plist in out_carriers.items()
        if carriers[key]["carrier_class"] == "building"
        for p in plist
        if p["evidence"]["kind"] == "rule" and p.get("module_id") in GUARDRAIL_MODS)
    assert n_bld_guard == 0, f"guardrail runs on buildings: {n_bld_guard}"

    # RAILEDGE-01 structural rail_edge block: rule rail_edge runs on
    # corridor outer edges (offset emission above), audited here. A run
    # is "between tracks" when its midpoint lies within rail_half of
    # two or more railway centrelines.
    rail_half = surf.rail_half_m()
    rail_cids = [c["carrier_id"] for c in carriers.values()
                 if c["carrier_class"] == "railway"]
    rail_runs = []
    platform_rule_tiles = 0
    for _key, _plist in out_carriers.items():
        for _p in _plist:
            if _p["evidence"]["kind"] != "rule" \
                    or _p.get("module_id") is None:
                continue
            if _p.get("module_id") == "platform_railing":
                platform_rule_tiles += 1
                continue
            if "run" not in _p:
                continue
            _lat, _a, _f = module_surface_policy(
                _p["module_id"],
                carriers[_key]["carrier_class"], catalog_by_id)
            if resolve_lateral(
                    _lat, carriers[_key]["carrier_class"]) \
                    != "rail_edge":
                continue
            rail_runs.append((_key, _p))

    def _rail_seg_dist2(px, py, ax, ay, bx, by):
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 == 0.0:
            return (px - ax) ** 2 + (py - ay) ** 2
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
        cx, cy = ax + t * dx, ay + t * dy
        return (px - cx) ** 2 + (py - cy) ** 2

    def _rail_poly_dist(px, py, poly):
        return math.sqrt(min(_rail_seg_dist2(px, py, a[0], a[1], b[0], b[1])
                             for a, b in zip(poly, poly[1:])))

    rail_min_own = None
    rail_between = 0
    for _key, _p in rail_runs:
        _r = _p["run"]
        _pts = [(_r["E0"], _r["N0"]),
                ((_r["E0"] + _r["E1"]) / 2,
                 (_r["N0"] + _r["N1"]) / 2),
                (_r["E1"], _r["N1"])]
        _own = min(_rail_poly_dist(q[0], q[1],
                                   carriers[_key]["polyline"])
                   for q in _pts)
        rail_min_own = _own if rail_min_own is None else min(
            rail_min_own, _own)
        _near = sum(
            1 for _cid in rail_cids
            if _rail_poly_dist(
                (_r["E0"] + _r["E1"]) / 2,
                (_r["N0"] + _r["N1"]) / 2,
                carriers[_cid]["polyline"]) < rail_half)
        if _near >= 2:
            rail_between += 1
    structural["rail_edge"] = {
        "runs": len(rail_runs),
        "min_own_offset_m": round(rail_min_own, 3)
        if rail_min_own is not None else None,
        "pieces_between_tracks": rail_between,
        "platform_rule_tiles": platform_rule_tiles,
    }
    print(f"[plan] rail_edge: runs={len(rail_runs)} "
          f"min_own_offset_m={structural['rail_edge']['min_own_offset_m']} "
          f"between_tracks={rail_between} "
          f"platform_rule_tiles={platform_rule_tiles}")

    bbox = {"min_e": 379699.095, "max_e": 380113.054,
            "min_n": 3938417.877, "max_n": 3939043.068}
    # Recompute run/point counters after normalisation + structural stage so
    # the summary continuous_runs line reflects emitted geometry, not the
    # pre-split accumulation.
    run_count = sum(1 for plist in out_carriers.values()
                    for p in plist if "run" in p)
    point_count = sum(1 for plist in out_carriers.values()
                      for p in plist if "run" not in p)
    run_length_total = sum(p["run"]["length_m"]
                           for plist in out_carriers.values()
                           for p in plist if "run" in p)
    _in_scope_keys = [k for k in carriers
                      if carriers[k]["carrier_class"] == "building"
                      and carriers[k]["carrier_id"] in bld_ids]
    _use_counts = Counter(use_class_source.get(k, "unresolved")
                          for k in _in_scope_keys)
    use_class_resolution = {
        "in_scope_buildings": len(_in_scope_keys),
        "by_source": dict(sorted(_use_counts.items())),
        "resited_osm_rings": resited_buildings,
        "added_plateau_carriers": added_plateau,
        "road_claim_agree": road_claim_agree,
        "road_claim_missing": road_claim_missing,
        "road_claim_disagree": road_claim_disagree,
        "commercial_place_claim_points": len(claim_place_pts),
        "place_claim_only_points": place_claim_only,
        "excluded": sorted(use_class_excluded, key=str),
    }
    # Canonical attachment records (POPPLAN-02 DELIVER 1): every placement
    # carries the canonical ids plus relative measures it was sited from,
    # so lowerers resolve() against the verified doc instead of re-deriving
    # geometry. carrier_span for linear runs (start/end chainage + lateral
    # offset + side), building_face for facade modules, freestanding for
    # others. Debt records (not silent drops) for degenerate geometry.
    # A resolve() round-trip validates resolvability; position drift is
    # diagnostic (coherence remains the position backstop).
    from scripts.pipeline.authored_population_decisions import (
        decision_for as _authored_decision_for,
        load_authored_decisions as _load_authored_decisions,
    )
    from scripts.pipeline.canonical_attachment import (
        CanonicalIndex as _AttachmentIndex,
        attach_freestanding as _attach_freestanding,
        resolve as _resolve_attachment,
        resolve_span_endpoints as _resolve_span_endpoints,
        ring_edges as _canonical_ring_edges,
    )
    _attach_index = _AttachmentIndex(world_ir)
    # FL-4876 DECIDED-OMISSION-01. Operator decisions that settle an owed
    # placement. Empty when none is supplied; a proposed row settles
    # nothing; a row decided against a different cause settles nothing.
    _authored_decisions = _load_authored_decisions(
        globals().get('_AUTHORED_DECISIONS_PATH'))
    _FACADE_ATTACH_MODULES = (set(FACADE_ARCHETYPE_MODS)
                              | {"facade_archetype", "generic_storefront",
                                 STOREFRONT_RUN_MOD, SIGN_POINT_MOD})
    # FL-4876 Repair 2. _FACADE_ATTACH_MODULES decides which modules are
    # face-tested. This decides which modules are facades at all, and the two
    # are not the same set: independent_storefront_awning owns a
    # generic_storefront domain, is absent from the module list above, and so
    # was never face-tested before falling through to a freestanding record.
    # The invariant below is written against the domain for that reason.
    _FACADE_OWNING_DOMAINS = frozenset({"facade_archetype",
                                        "generic_storefront"})
    # FL-4876 TRACKSIDE-01. Modules that only exist beside a railway. The
    # observation registration ray (register_observations.py) prefers rail
    # carriers for these, but a camera looking ALONG a track never crosses the
    # rail line, so the ray falls back to the first road or footway it hits
    # and the nearest-carrier attachment followed: build_road_01 attached
    # rail_track_corridor obs-inst-0459 to road way/196858614 and
    # rail_corridor_fence obs-inst-0113 to a road. The catalog owning_domain
    # "rail_edge" is too broad for this rule (it also labels waterway bridge
    # railings 68-85 m from any rail), so the set is explicit. Level-crossing
    # equipment is excluded: it legitimately stands beside the road.
    _TRACKSIDE_MODULES = frozenset({
        "rail_track_corridor", "rail_corridor_fence",
        "rail_corridor_mesh_fence", "platform_railing",
        "overhead_gantry_wire"})
    _canon_feat_by_osmid = {}
    for _ff in world_ir["canonical_features"]:
        _mm = re.search(r"[:/](way|node|relation)/(\d+)$",
                        _ff.get("feature_id", ""))
        if _mm:
            _canon_feat_by_osmid.setdefault(
                (_ff.get("semantic_class"), int(_mm.group(2))), _ff)

    def _attach_carrier_feature(_cc):
        _cid = _cc.get("carrier_id")
        if isinstance(_cid, str) and _cid in _attach_index.by_id:
            return _attach_index.by_id[_cid]
        if isinstance(_cid, int):
            _cclass = _cc.get("carrier_class", "")
            if _cclass == "building":
                _hit = canon_building_by_osm.get(_cid)
                return _hit[0] if _hit else None
            _want = ("road" if _cclass.startswith("road:") else
                     "railway_boundary" if _cclass.startswith("railway") else None)
            if _cclass in ("road:footway", "road:steps", "road:pedestrian"):
                _want = "sidewalk"
            if _want is not None:
                _hit = _canon_feat_by_osmid.get((_want, _cid))
                if _hit is not None:
                    return _hit
            _cands = sorted(
                (f for (s, i), f in _canon_feat_by_osmid.items()
                 if i == _cid and s in ("road", "railway_boundary",
                                        "building_footprint", "sidewalk",
                                        "crossing", "terrain")),
                key=lambda f: f.get("feature_id", ""))
            return _cands[0] if _cands else None
        return None

    def _attach_chainage(px, py, poly):
        best = None
        acc = 0.0
        for _a, _b in zip(poly, poly[1:]):
            _dx, _dy = _b[0] - _a[0], _b[1] - _a[1]
            _seg = math.hypot(_dx, _dy)
            _t = ((px - _a[0]) * _dx + (py - _a[1]) * _dy) / (_dx * _dx + _dy * _dy) if _seg > 0 else 0.0
            _tc = max(0.0, min(1.0, _t))
            _qx, _qy = _a[0] + _tc * _dx, _a[1] + _tc * _dy
            _dist = math.hypot(px - _qx, py - _qy)
            if best is None or _dist < best[0]:
                _bearing = math.degrees(math.atan2(_dy, _dx)) if _seg > 0 else 0.0
                best = (_dist, acc + _tc * _seg, _qx, _qy, _bearing)
            acc += _seg
        return best

    def _attach_side(px, py, qx, qy, bearing):
        _s = (px - qx) * math.cos(math.radians(bearing + 90.0)) + (py - qy) * math.sin(math.radians(bearing + 90.0))
        return "R" if _s >= 0 else "L"
    def _attach_ring_edges(ring):
        # PLAN-07: face identity lives in canonical_attachment.ring_edges
        # (n-1 edges, closing duplicate never an edge). Nothing in this
        # planner computes faces any other way.
        return _canonical_ring_edges(ring)

    try:
        _face_tol_m = float(rules["attachment_tolerances"]["face_resolve_m"]["value"])
        _carrier_tol_m = float(
            rules["attachment_tolerances"]["carrier_association_m"]["value"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "rules lack attachment_tolerances face_resolve_m or "
            "carrier_association_m: no attachment gate without DEFAULT facts")

    def _nearest_rail_carrier(_px, _py):
        """nearest_carrier() restricted to railway_boundary features (TRACKSIDE-01)."""
        _best = None
        for _rfid, _rpoly in _attach_index.carriers:
            if ":railway_boundary:" not in _rfid or len(_rpoly) < 2:
                continue
            _hit = _attach_chainage(_px, _py, _rpoly)
            if _hit is not None and (_best is None or _hit[0] < _best[0]):
                _best = (_hit[0], _rfid, _hit[1], (_hit[2], _hit[3]), _hit[4])
        return _best

    def _freestanding_rec(_pid, _aid, _ex, _ey, _module=None):
        _carr = (_nearest_rail_carrier(_ex, _ey) if _module in _TRACKSIDE_MODULES
                 else _attach_index.nearest_carrier(_ex, _ey))
        if _carr is None:
            return {"record_type": "spatial_reconciliation_debt",
                    "anchor_kind": "carrier_chainage",
                    "attachment_id": _aid,
                    "reason": "missing_host:no_canonical_carrier",
                    "residual_m": None}
        if float(_carr[0]) > _carrier_tol_m:
            return {"record_type": "spatial_reconciliation_debt",
                    "anchor_kind": "carrier_chainage",
                    "attachment_id": _aid,
                    "reason": "carrier_beyond_tolerance",
                    "residual_m": round(float(_carr[0]), 3)}
        _side = _attach_side(_ex, _ey, _carr[3][0], _carr[3][1], _carr[4])
        return {"record_type": "attachment",
                "anchor_kind": "carrier_chainage",
                "attachment_id": _aid,
                "canonical_carrier_id": _carr[1],
                "chainage_m": round(float(_carr[2]), 3),
                "lateral_offset_m": round(float(_carr[0]), 3),
                "side": _side,
                "orientation_basis": "carrier_bearing_at_chainage"}

    def _snap_refusal_site(_pid, _aid, _ex, _ey, _module=None):
        """Attachment that keeps an observed point where it was observed when
        its face snap is refused for distance (SNAP-CAP-01): a carrier
        chainage within tolerance, else the terrain that contains the point.
        None when neither exists (the snap then stands). A face-wide module
        (shutter, roof railing, wall column, roofed entry) exists only on a
        face, so its snap always stands."""
        from scripts.pipeline.population_asset_geometry import (
            FULL_FACE_UNIT_MODULES)
        if _module in FULL_FACE_UNIT_MODULES:
            return None
        _site = _freestanding_rec(_pid, _aid, _ex, _ey, _module=_module)
        if _site.get("record_type") == "attachment":
            return _site
        _ground = _attach_index.containing_terrain(_ex, _ey)
        if _ground is None:
            return None
        return {"record_type": "attachment", "anchor_kind": "freestanding",
                "attachment_id": _aid, "E": round(_ex, 3),
                "N": round(_ey, 3), "ground_owner": _ground,
                "nearest_carrier_id": None, "offset_m": None}

    def _mass_conflicts(_px, _py, _skip_fid=None):
        _hits = []
        for _mfid in _mass_order:
            # Only the explicitly declared host may be excluded.  Semantic
            # exceptions belong in an upstream placement contract; this
            # planner must not inherit verifier-local mass exemptions.
            if _mfid == _skip_fid:
                continue
            _mx0, _my0, _mx1, _my1 = _mass_bbox[_mfid]
            _mring = _mass_rings[_mfid]
            # Mass occupation is boundary-inclusive, matching the baked-mass
            # and SPATIAL-04 contract.  A facade on a neighbouring shared
            # wall is not an exposed face merely because ray casting assigns
            # the exact boundary to one polygon and not the other.
            _on_boundary = any(
                seg_dist2(_px, _py, _a[0], _a[1], _b[0], _b[1])
                <= 1e-12
                for _a, _b in _attach_ring_edges(_mring))
            if (_mx0 <= _px <= _mx1 and _my0 <= _py <= _my1
                    and (point_in_ring(_px, _py, _mring)
                         or _on_boundary)):
                _hits.append(_mfid)
        return _hits

    def _mass_debt(
        _pl, _aid, _hits, _candidate=None, *,
        _reason="observed_point_inside_canonical_mass",
        _resolution_policy=(
            "preserve observed coordinate; require registration, mass, "
            "or classification authority to resolve contradiction"),
    ):
        _rec = {
            "record_type": "spatial_reconciliation_debt",
            "attachment_id": _aid,
            "anchor_kind": (_candidate or {}).get(
                "anchor_kind", "freestanding"),
            "reason": _reason,
            "residual_m": 0.0,
            "conflicting_mass_ids": sorted(_hits),
            "source_position_epsg32654": [
                round(float(_pl["E"]), 6), round(float(_pl["N"]), 6)],
            "resolution_policy": _resolution_policy,
        }
        if _candidate:
            for _key in ("canonical_carrier_id", "chainage_m",
                         "lateral_offset_m", "side", "orientation_basis",
                         "canonical_building_id", "canonical_face_id",
                         "face_index", "along_face_fraction",
                         "outward_offset_m"):
                if _key in _candidate:
                    _rec[_key] = _candidate[_key]
        _pl["attachment"] = _rec
        _pl["attachment_state"] = f"debt:{_reason}"
        _pl["attachment_id"] = _aid
        return _rec

    def _position_uncertainty_m(_pl):
        """Return the source-declared horizontal uncertainty, if any."""
        _evidence = _pl.get("evidence") or {}
        try:
            _direct = float(_evidence.get("position_uncertainty_m"))
            if _direct > 0.0:
                return _direct, str(
                    _evidence.get("position_uncertainty_basis")
                    or "observation.position_uncertainty_m")
        except (TypeError, ValueError):
            pass
        _fid = (_pl.get("canonical_feature_id")
                or _evidence.get("canonical_feature_id"))
        _feature = canon_by_id.get(str(_fid)) if _fid else None
        _claim = (((_feature or {}).get("properties") or {}).get(
            "geometry") or {})
        _uncertainty = _claim.get("uncertainty") or {}
        if (_uncertainty.get("model") == "isotropic_sigma"
                and _uncertainty.get("units") == "metre"):
            try:
                _sigma = float(_uncertainty["sigma"])
                if _sigma > 0.0:
                    return _sigma, (
                        "canonical.geometry.uncertainty.isotropic_sigma")
            except (KeyError, TypeError, ValueError):
                pass
        return None, None

    def _reconcile_observed_point(_pl, _aid, _skip_mass=None):
        """Move an uncertain observation to the nearest legal carrier site.

        The source coordinate remains evidence.  Only observations carrying a
        numeric uncertainty may move, and the displacement is bounded by that
        uncertainty.  This turns presentation-grade image coordinates into a
        canonical carrier attachment without pretending they were surveyed.
        """
        _sigma, _basis = _position_uncertainty_m(_pl)
        if _sigma is None:
            return None
        _sx, _sy = float(_pl["E"]), float(_pl["N"])
        _directions = 32
        _step = max(0.05, min(0.25, _sigma / 40.0))
        _radius = _step
        while _radius <= _sigma + 1e-9:
            _candidates = []
            for _index in range(_directions):
                _angle = 2.0 * math.pi * _index / _directions
                _cx = _sx + math.cos(_angle) * _radius
                _cy = _sy + math.sin(_angle) * _radius
                if not point_in_ring(_cx, _cy, ring):
                    continue
                if _mass_conflicts(_cx, _cy, _skip_mass):
                    continue
                _candidate = _freestanding_rec(
                    _pl.get("placement_id"), _aid, _cx, _cy,
                    _module=_pl.get("module_id"))
                if _candidate.get("record_type") != "attachment":
                    continue
                _candidates.append((_candidate, _cx, _cy))
            if _candidates:
                _candidate, _cx, _cy = min(
                    _candidates,
                    key=lambda row: float(
                        row[0].get("lateral_offset_m") or 0.0))
                _pl.setdefault("evidence", {})[
                    "registered_position_epsg32654"] = [
                        round(_sx, 6), round(_sy, 6)]
                _pl["evidence"]["spatial_reconciliation"] = {
                    "kind": "uncertain_observation_to_legal_carrier_site",
                    "position_uncertainty_m": round(_sigma, 3),
                    "position_uncertainty_basis": _basis,
                    "adjustment_m": round(math.hypot(
                        _cx - _sx, _cy - _sy), 3),
                    "canonical_carrier_id": _candidate.get(
                        "canonical_carrier_id"),
                }
                _pl["E"], _pl["N"] = _cx, _cy
                return _candidate
            _radius += _step
        return None
    # POLE-01 observed-first utility poles + rule gap fill. Every canonical
    # utility_pole feature in the AOI becomes a placement at its observed
    # position with a carrier_chainage attachment (Mapillary owns presence);
    # rule poles fill only inter-observed gaps longer than the spacing
    # budget; a rule candidate within the merge budget of an observed pole
    # is dropped, never both. Pole placements sort by chainage per carrier
    # so the lowerer's span chaining wires them in order.
    try:
        _pole_spacing = float(rules["road_class_defaults"]["utility_pole_spacing_m"]["value"])
        _pole_merge = float(rules["road_class_defaults"]["utility_pole_merge_m"]["value"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("rules lack road_class_defaults.utility_pole_spacing_m/_merge_m: no pole gaps without the DEFAULT facts")
    _POLE_MOD = "utility_pole_wire_cluster"
    _pole_variant = (catalog_by_id.get(_POLE_MOD, {}).get("variants") or ["default"])[0]
    _pole_stats = {"canonical_poles": 0, "outside_aoi": 0, "observed_adopted": 0,
                   "observed_new": 0, "observed_unhosted": 0,
                   "rule_filled": 0, "carriers_with_poles": 0, "gaps": []}
    _RAIL_POLE_CLEAR_M = 3.0
    _rail_polys = [carriers[_rk].get("polyline") or [] for _rk in carriers
                   if str(carriers[_rk].get("carrier_class", "")).startswith("rail")]

    def _pole_near_track(_px, _py):
        for _rp in _rail_polys:
            for _ra, _rb in zip(_rp, _rp[1:]):
                _dx, _dy = _rb[0] - _ra[0], _rb[1] - _ra[1]
                _l2 = _dx * _dx + _dy * _dy or 1e-12
                _t = max(0.0, min(1.0, ((_px - _ra[0]) * _dx + (_py - _ra[1]) * _dy) / _l2))
                if math.hypot(_px - _ra[0] - _t * _dx, _py - _ra[1] - _t * _dy) < _RAIL_POLE_CLEAR_M:
                    return True
        return False

    def _pole_host(_px, _py):
        # Chainage runs on the canonical carrier polyline (the frame
        # resolve() uses), never the zone polyline, so round-trip drift
        # stays near zero and joins hit the exact feature.
        _best = None
        for _hkey, _hc in carriers.items():
            _hclass = _hc.get("carrier_class", "")
            if not (_hclass.startswith("road") or _hclass.startswith("railway") or _hclass.startswith("sidewalk")):
                continue
            _hfeat = _attach_carrier_feature(_hc)
            if _hfeat is None:
                continue
            _hg = _hfeat.get("geometry", {})
            if _hg.get("kind") != "polyline" or not isinstance(_hg.get("coordinates"), list):
                continue
            _hpoly = _hg.get("coordinates")
            if len(_hpoly) < 2:
                continue
            _hit = _attach_chainage(_px, _py, _hpoly)
            if _hit is None:
                continue
            if _best is None or _hit[0] < _best[0]:
                _best = (_hit[0], _hkey, _hit[1], _hit[2], _hit[3], _hit[4],
                         _hfeat["feature_id"], _hpoly)
        return _best
    def _pole_chainage_rec(_aid2, _feat2, _ch2, _lat2, _side2):
        return {"record_type": "attachment", "anchor_kind": "carrier_chainage",
                "attachment_id": _aid2, "canonical_carrier_id": _feat2,
                "chainage_m": round(_ch2, 3), "lateral_offset_m": round(_lat2, 3),
                "side": _side2, "orientation_basis": "carrier_bearing_at_chainage"}
    def _pole_point_at(_poly2, _ch2):
        _acc2 = 0.0
        for _pa, _pb in zip(_poly2, _poly2[1:]):
            _sg = math.hypot(_pb[0] - _pa[0], _pb[1] - _pa[1])
            if _acc2 + _sg >= _ch2:
                _t2 = 0.0 if _sg == 0 else (_ch2 - _acc2) / _sg
                _dx2, _dy2 = _pb[0] - _pa[0], _pb[1] - _pa[1]
                return (_pa[0] + _t2 * _dx2, _pa[1] + _t2 * _dy2,
                        math.degrees(math.atan2(_dy2, _dx2)))
            _acc2 += _sg
        _pa, _pb = _poly2[-2], _poly2[-1]
        return (_pb[0], _pb[1], math.degrees(math.atan2(_pb[1] - _pa[1], _pb[0] - _pa[0])))
    def _pole_freestanding(_pl):
        _canonical_point = (_pl.get("canonical_feature_id")
                            or (_pl.get("evidence") or {}).get(
                                "canonical_feature_id"))
        if _canonical_point not in _attach_index.by_id:
            _pl["attachment"] = {
                "record_type": "spatial_reconciliation_debt",
                "anchor_kind": "freestanding",
                "attachment_id": f"att-{_pl['placement_id']}",
                "reason": "missing_host:no_canonical_point",
                "residual_m": None}
            _pl["attachment_state"] = "debt:missing_host:no_canonical_point"
            _pl["_pole_authored"] = True
            return
        _ground = _attach_index.containing_terrain(
            float(_pl["E"]), float(_pl["N"]))
        # FL-4876 COHERENCE-ZERO-01: carry the planner's position. resolve()
        # honours an explicit E/N on a freestanding record (FREESTAND-01);
        # without it a pole moved out of a mass within its Mapillary sigma
        # resolved back to the raw point inside that mass (obs-pole-0001:
        # 3.92 m reconciliation discarded, hardware 0.96 m inside a building).
        _pl["attachment"] = {"record_type": "attachment", "anchor_kind": "freestanding",
                             "attachment_id": f"att-{_pl['placement_id']}",
                             "canonical_point": _canonical_point,
                             "E": round(float(_pl["E"]), 3),
                             "N": round(float(_pl["N"]), 3),
                             "ground_owner": _ground,
                             "nearest_carrier_id": None,
                             "offset_m": None}
        _pl["attachment_state"] = "attached"
        _pl["_pole_authored"] = True
    _pole_reg = []
    for _rkey in sorted(carriers, key=str):
        for _rpl in out_carriers.get(_rkey, []):
            if _rpl.get("module_id") == _POLE_MOD and (_rpl.get("evidence") or {}).get("kind") == "observation":
                _pole_reg.append((_rkey, _rpl))
    _pole_cha = defaultdict(list)
    _pole_poly = {}
    for _pf in world_ir["canonical_features"]:
        if _pf.get("semantic_class") != "utility_pole":
            continue
        _pole_stats["canonical_poles"] += 1
        _fid = _pf["feature_id"]
        _g = _pf.get("geometry", {})
        _px, _py = float(_g["coordinates"][0]), float(_g["coordinates"][1])
        if not point_in_ring(_px, _py, ring):
            _pole_stats["outside_aoi"] += 1
            continue
        _sk = "mapillary" if "mapillary" in _fid else "osm"
        _adopt_key, _adopt, _ad = None, None, None
        for _rkey, _rpl in _pole_reg:
            if "canonical_feature_id" in (_rpl.get("evidence") or {}):
                continue
            _dd = math.hypot(float(_rpl["E"]) - _px, float(_rpl["N"]) - _py)
            if _dd <= _pole_merge and (_ad is None or _dd < _ad):
                _adopt_key, _adopt, _ad = _rkey, _rpl, _dd
        if _adopt is not None:
            _pl = _adopt
            _pl["evidence"]["canonical_feature_id"] = _fid
            _pl["evidence"]["source_kind"] = _sk
            _pl["representation_id"] = f"mesh_fixture:{_fid}"
            _pl["canonical_feature_id"] = _fid
            _pl["asserts_physical_presence"] = True
            _pole_stats["observed_adopted"] += 1
        else:
            _pl = {"placement_id": f"obs-pole-{_pole_stats['observed_new']:04d}",
                   "module_id": _POLE_MOD, "variant": _pole_variant,
                   "E": _px, "N": _py, "height_m": 5.0, "yaw_deg": 0.0,
                   "evidence": {"kind": "observation", "canonical_feature_id": _fid,
                                "source_kind": _sk, "instance_id": None},
                   "representation_id": f"mesh_fixture:{_fid}",
                   "canonical_feature_id": _fid,
                   "asserts_physical_presence": True}
            _pole_stats["observed_new"] += 1
        # Adopted registered observations keep their image-derived E/N.  The
        # attachment must describe that same emitted position; the canonical
        # Mapillary feature point is a separate, lower-precision claim and
        # cannot own chainage or lateral offset for the adopted placement.
        _site_x, _site_y = float(_pl["E"]), float(_pl["N"])
        _host = _pole_host(_site_x, _site_y)
        _mass_hits = _mass_conflicts(_site_x, _site_y)
        # Mapillary pole coordinates are explicitly presentation-only with a
        # 5 m isotropic sigma.  Presence is observed; the raw point is not a
        # surveyed foundation.  When that uncertain point falls inside an
        # authoritative canonical mass, reconcile it to the closest legal
        # position on the same carrier side within the declared sigma.  Keep
        # the served coordinate as evidence and never apply this rule to a
        # source lacking an uncertainty contract.
        _geom_claim = ((_pf.get("properties") or {}).get("geometry") or {})
        _uncertainty = _geom_claim.get("uncertainty") or {}
        _sigma = None
        if (_uncertainty.get("model") == "isotropic_sigma"
                and _uncertainty.get("units") == "metre"):
            try:
                _sigma = float(_uncertainty["sigma"])
            except (KeyError, TypeError, ValueError):
                _sigma = None
        # FL-4876 ROADSIDE-SNAP-01: a pole standing on the painted
        # carriageway is the same illegal site as one inside a mass (22 of 89
        # observed poles stood over a third of the way into the asphalt).
        _on_carriageway = (
            _host is not None
            and carriers[_host[1]]["carrier_class"].startswith("road")
            and "carriageway" in surf.point_surfaces(_site_x, _site_y))
        # FL-4876 RAIL-CLEAR-01: a pole within 3.0 m of a railway centreline
        # stands in the train envelope (car half-width ~1.4 m + clearance);
        # Numabe's photographed rail-side poles stand at the corridor edge.
        _in_track_bed = _pole_near_track(_site_x, _site_y)
        if ((_mass_hits or _on_carriageway or _in_track_bed) and _host is not None
                and _sigma is not None and _sigma > 0.0):
            _, _rhkey, _rch, _rqx, _rqy, _rbear, _rhfid, _rhpoly = _host
            _rside = _attach_side(
                _site_x, _site_y, _rqx, _rqy, _rbear)
            _rnx = math.cos(math.radians(
                _rbear + (90.0 if _rside == "R" else -90.0)))
            _rny = math.sin(math.radians(
                _rbear + (90.0 if _rside == "R" else -90.0)))
            _max_lateral = max(6.0, float(_host[0]) + _sigma)
            _legal = []
            _lat = 0.25
            while _lat <= _max_lateral + 1e-9:
                _cx, _cy = _rqx + _rnx * _lat, _rqy + _rny * _lat
                _delta = math.hypot(_cx - _site_x, _cy - _site_y)
                if (_delta <= _sigma + 1e-9
                        and not _mass_conflicts(_cx, _cy)
                        and not _pole_near_track(_cx, _cy)
                        and point_in_ring(_cx, _cy, ring)):
                    _conf = placement_conforms(
                        surf, module_id=_POLE_MOD,
                        carrier_class=carriers[_rhkey]["carrier_class"],
                        evidence_kind="rule", point=(_cx, _cy),
                        catalog_by_id=catalog_by_id)
                    if _conf.get("ok"):
                        _legal.append((_delta, _lat, _cx, _cy))
                _lat = round(_lat + 0.25, 3)
            if not _legal and _in_track_bed:
                # RAIL-CLEAR-01 fallback: the host-normal search found no site
                # that also passes the carrier's surface conformance (a rail
                # carrier admits no pole surface beside it).  Step straight away
                # from the nearest track centreline to 3.2 m, same side, still
                # within sigma, outside every mass and inside the AOI.
                _tb = None
                for _rp in _rail_polys:
                    for _ra, _rb in zip(_rp, _rp[1:]):
                        _dx, _dy = _rb[0] - _ra[0], _rb[1] - _ra[1]
                        _l2 = _dx * _dx + _dy * _dy or 1e-12
                        _t = max(0.0, min(1.0, ((_site_x - _ra[0]) * _dx + (_site_y - _ra[1]) * _dy) / _l2))
                        _fx, _fy = _ra[0] + _t * _dx, _ra[1] + _t * _dy
                        _dd = math.hypot(_site_x - _fx, _site_y - _fy)
                        if _tb is None or _dd < _tb[0]:
                            _tb = (_dd, _fx, _fy, _dx, _dy)
                if _tb is not None:
                    _dd, _fx, _fy, _dx, _dy = _tb
                    _ll = math.hypot(_dx, _dy) or 1.0
                    _ux, _uy = ((_site_x - _fx) / _dd, (_site_y - _fy) / _dd) if _dd > 1e-6 else (-_dy / _ll, _dx / _ll)
                    # Away from the nearest track first; a pole standing BETWEEN
                    # two tracks (build_reg_59 obs-pole-0041/0000: 0.6 m off one
                    # centreline) has no clear site on that side, so also try
                    # the outer side of the nearest track.
                    for _dir in (1.0, -1.0):
                        for _clear in (3.2, 3.6, 4.0, 4.5):
                            _cx, _cy = _fx + _dir * _ux * _clear, _fy + _dir * _uy * _clear
                            _delta = math.hypot(_cx - _site_x, _cy - _site_y)
                            if (_delta <= _sigma + 1e-9 and not _mass_conflicts(_cx, _cy)
                                    and not _pole_near_track(_cx, _cy)
                                    and point_in_ring(_cx, _cy, ring)):
                                _legal.append((_delta, float(_host[0]), _cx, _cy))
                                break
            if _legal:
                _delta, _new_lat, _site_x, _site_y = min(_legal)
                _pl.setdefault("evidence", {})["spatial_reconciliation"] = {
                    "kind": "uncertain_mapillary_point_to_legal_carrier_site",
                    "trigger": ("canonical_mass" if _mass_hits
                                else "painted_carriageway" if _on_carriageway
                                else "rail_track_bed"),
                    "source_position_epsg32654": [
                        round(float(_pl["E"]), 6),
                        round(float(_pl["N"]), 6)],
                    "position_sigma_m": _sigma,
                    "adjustment_m": round(_delta, 3),
                    "canonical_carrier_id": _rhfid,
                }
                _pl["E"], _pl["N"] = _site_x, _site_y
                _host = (_new_lat, _rhkey, _rch, _rqx, _rqy, _rbear,
                         _rhfid, _rhpoly)
                _mass_hits = []
        if _host is None or _host[0] > 15.0:
            if _mass_hits:
                _mass_debt(
                    _pl, f"att-{_pl['placement_id']}", _mass_hits)
                _pl["_pole_authored"] = True
            else:
                _pole_freestanding(_pl)
            _pole_stats["observed_unhosted"] += 1
            if _adopt is None:
                _near = min((k for k in carriers if (carriers[k].get("polyline") or [])),
                            key=lambda k: math.hypot(float(carriers[k]["polyline"][0][0]) - _px, float(carriers[k]["polyline"][0][1]) - _py),
                            default=None)
                if _near is not None:
                    out_carriers.setdefault(_near, []).append(_pl)
                    per_carrier_n[_near] += 1
            continue
        _, _hkey, _ch, _qx, _qy, _bear, _hfid, _hpoly = _host
        _side = _attach_side(_site_x, _site_y, _qx, _qy, _bear)
        if _adopt is None:
            # New placements take the carrier bearing at the chainage point
            # (full 0-360 sense like OBSYAW, never folded: coherence reads
            # yaw mod 360); adopted placements keep registered yaw.
            _pl["yaw_deg"] = round(_bear, 3)
        _aid = f"att-{_pl['placement_id']}"
        _candidate_attachment = _pole_chainage_rec(
            _aid, _hfid, _ch, _host[0], _side)
        # FL-4876 COHERENCE-ZERO-01: a chainage attachment must reproduce the
        # planned pole point. A pole beyond its carrier's end projects onto
        # the end vertex; its distance is then diagonal, and chainage+lateral
        # resolves elsewhere (obs-pole-0068: 10.9 m off, inside a building).
        # Such a pole is freestanding at its planned point.
        if not _mass_hits:
            try:
                _rp = _resolve_attachment(_candidate_attachment, _attach_index)
                _repro = math.hypot(float(_rp["E"]) - _site_x, float(_rp["N"]) - _site_y)
            except Exception:  # noqa: BLE001 - unresolvable record is a mismatch
                _repro = float("inf")
            if _repro > 0.5:
                _pl["E"], _pl["N"] = _site_x, _site_y
                _pole_freestanding(_pl)
                _pole_stats["observed_unhosted"] += 1
                if _adopt is None:
                    out_carriers.setdefault(_hkey, []).append(_pl)
                    per_carrier_n[_hkey] += 1
                continue
        if _mass_hits:
            _mass_debt(
                _pl, _aid, _mass_hits, _candidate_attachment)
        else:
            _pl["attachment"] = _candidate_attachment
            _pl["attachment_state"] = "attached"
            _pl["attachment_id"] = _aid
        _pl["_pole_authored"] = True
        _pole_poly[_hkey] = (_hfid, _hpoly)
        if _adopt is None:
            out_carriers.setdefault(_hkey, []).append(_pl)
            per_carrier_n[_hkey] += 1
        elif _adopt_key != _hkey:
            # Span topology follows the same canonical carrier that owns the
            # attachment, not the older registration-zone carrier.  Preserve
            # the source carrier in evidence before rehoming the placement.
            _pl.setdefault("evidence", {})["registered_carrier_id"] = \
                carriers[_adopt_key].get("carrier_id")
            _pl["evidence"]["resolved_carrier_id"] = \
                carriers[_hkey].get("carrier_id")
            out_carriers[_adopt_key] = [
                p for p in out_carriers.get(_adopt_key, []) if p is not _pl]
            out_carriers.setdefault(_hkey, []).append(_pl)
            per_carrier_n[_adopt_key] = max(
                0, per_carrier_n[_adopt_key] - 1)
            per_carrier_n[_hkey] += 1
        _pole_cha[_hkey].append((_ch, _pl))
    for _hkey in sorted(_pole_cha, key=str):
        _ordered = sorted(_pole_cha[_hkey], key=lambda t: t[0])
        for (_c0, _p0), (_c1, _p1) in zip(_ordered, _ordered[1:]):
            if _c1 - _c0 <= _pole_spacing:
                continue
            _a0, _a1 = (_p0.get("attachment") or {}), (_p1.get("attachment") or {})
            _s0, _s1 = _a0.get("side"), _a1.get("side")
            _l0, _l1 = _a0.get("lateral_offset_m"), _a1.get("lateral_offset_m")
            _lat2 = (_l0 + _l1) / 2.0 if isinstance(_l0, (int, float)) and isinstance(_l1, (int, float)) else 2.0
            _hfid2, _poly2 = _pole_poly.get(_hkey, (None, []))
            _sp2 = carrier_geo[_hkey]["span_index"]
            if _hfid2 is None or len(_poly2) < 2:
                continue
            _pos = _c0 + _pole_spacing
            while _pos < _c1 - _pole_merge:
                _ex2, _ey2, _byaw = _pole_point_at(_poly2, _pos)
                _sd = _s0 if _s0 == _s1 and _s0 in ("L", "R") else (_s0 if abs(_pos - _c0) <= abs(_pos - _c1) else _s1)
                if _sd not in ("L", "R"):
                    _sd = "R"
                _nx = math.cos(math.radians(_byaw + (90.0 if _sd == "R" else -90.0)))
                _ny = math.sin(math.radians(_byaw + (90.0 if _sd == "R" else -90.0)))
                _found = None
                _latry = _lat2
                while _latry <= 6.0:
                    _tfx, _tfy = _ex2 + _nx * _latry, _ey2 + _ny * _latry
                    if not point_in_ring(_tfx, _tfy, ring):
                        break
                    # Rule-generated poles have no observation authority that
                    # can justify occupying a building.  Surface conformance
                    # alone is insufficient here: a sidewalk/road surface may
                    # overlap a newer canonical PLATEAU mass.  Reject the
                    # candidate at the population owner and continue the
                    # bounded lateral search; observed poles are handled above
                    # and are never moved by this rule.
                    _inside_mass = False
                    for _mfid in _mass_order:
                        _mx0, _my0, _mx1, _my1 = _mass_bbox[_mfid]
                        if not (_mx0 <= _tfx <= _mx1 and _my0 <= _tfy <= _my1):
                            continue
                        if point_in_ring(_tfx, _tfy, _mass_rings[_mfid]):
                            _inside_mass = True
                            break
                    if _inside_mass:
                        _latry = round(_latry + 0.5, 3)
                        continue
                    _conf = placement_conforms(
                        surf, module_id=_POLE_MOD,
                        carrier_class=carriers[_hkey]["carrier_class"],
                        evidence_kind="rule", point=(_tfx, _tfy),
                        catalog_by_id=catalog_by_id)
                    if _conf.get("ok"):
                        _found = (_tfx, _tfy, _latry)
                        break
                    _latry = round(_latry + 0.5, 3)
                if _found is None:
                    _pole_stats.setdefault("unfilled", []).append({
                        "carrier_id": carriers[_hkey]["carrier_id"],
                        "from_m": round(_c0, 3), "to_m": round(_c1, 3),
                        "rule_chainage_m": round(_pos, 3),
                        "reason": "mass_or_surface_nonconformant:rule_point_strict"})
                    _pos += _pole_spacing
                    continue
                _fx, _fy, _latf = _found
                _rid = f"rule-pole-{carriers[_hkey]['carrier_id']}-{int(round(_pos)):d}"
                _raid = f"att-{_rid}"
                _rpl = {"placement_id": _rid, "module_id": _POLE_MOD, "variant": _pole_variant,
                        "E": _fx, "N": _fy,
                        "yaw_deg": round(_byaw, 3),
                        "evidence": {"kind": "rule",
                                     "rule_source": rule_source(_sp2, "occupation", _POLE_MOD),
                                     "instance_id": None,
                                     "fill_gap_from_m": round(_c0, 3),
                                     "fill_gap_to_m": round(_c1, 3)},
                        "attachment": _pole_chainage_rec(_raid, _hfid2, _pos, _latf, _sd),
                        "attachment_state": "attached", "attachment_id": _raid,
                        "_pole_authored": True}
                out_carriers[_hkey].append(_rpl)
                per_carrier_n[_hkey] += 1
                module_rule_counts[_POLE_MOD] += 1
                rule_count += 1
                point_count += 1
                _pole_stats["rule_filled"] += 1
                _pole_stats["gaps"].append({"carrier_id": carriers[_hkey]["carrier_id"],
                                           "from_m": round(_c0, 3), "to_m": round(_c1, 3),
                                           "rule_chainage_m": round(_pos, 3)})
                _pos += _pole_spacing
    for _hkey in [k for k, _lst in out_carriers.items()
                  if any(_p.get("module_id") == _POLE_MOD for _p in _lst)]:
        _ppoles = [_p for _p in out_carriers[_hkey] if _p.get("module_id") == _POLE_MOD]
        _prest = [_p for _p in out_carriers[_hkey] if _p.get("module_id") != _POLE_MOD]
        def _pchkey(_p):
            _cc = (_p.get("attachment") or {}).get("chainage_m")
            return (0, _cc) if isinstance(_cc, (int, float)) else (1, 0.0)
        out_carriers[_hkey] = _prest + sorted(_ppoles, key=_pchkey)
    _pole_stats["carriers_with_poles"] = sum(
        1 for _lst in out_carriers.values()
        if any(_p.get("module_id") == _POLE_MOD for _p in _lst))
    # FL-4876 DENSITY-SYNTH-01: operator-authorized surrounding synthesis.
    # Route bins the photo corpus never covered cannot earn lamp/tree/pole
    # coverage from evidence. Poles every 30 m (DEFAULT spacing), lamps and
    # trees every ~40 m (evidenced medians), on road carriers near the route
    # with no observed streetscape. Every candidate passes the same
    # placement_conforms gate as any rule point; failures are dropped, never
    # terminal, and the synthesis provenance marks every emitted placement.
    _synth_stats = {"stations": 0, "kept": 0, "dropped_conformance": 0,
                    "dropped_mass": 0, "dropped_ring": 0}
    _synth_route = None
    if getattr(args, "route_path", None):
        from world_surfaces import _decode_polyline6 as _synth_decode
        from geo_frames import utm_forward as _synth_utm
        _rt = json.loads(
            _Path(args.route_path).read_text(encoding="utf-8"))
        _synth_route = []
        for _leg in _rt["trip"]["legs"]:
            for _lat, _lon in _synth_decode(_leg.get("shape", "")):
                _E, _N = _synth_utm(_lon, _lat)
                _synth_route.append((float(_E), float(_N)))
    if _synth_route:
        _SYNTH_SET = {"utility_pole_wire_cluster", "transformer_drum_cluster",
                      "pole_streetlight", "street_tree", "small_tree",
                      "potted_frontage_plant"}
        _synth_observed = []
        for _lst in out_carriers.values():
            for _p in _lst:
                if ((_p.get("evidence") or {}).get("kind") == "observation"
                        and _p.get("module_id") in _SYNTH_SET
                        and _p.get("E") is not None):
                    _synth_observed.append(
                        (float(_p["E"]), float(_p["N"])))
        _synth_roads = {k: v for k, v in carriers.items()
                        if str(v.get("carrier_class", ""))
                        .startswith("road:")}
        for _mod, _phase in (("utility_pole_wire_cluster", 15.0),
                             ("pole_streetlight", 0.0),
                             ("street_tree", 20.0)):
            _variant = (catalog_by_id.get(_mod, {}).get("variants")
                        or ["default"])[0]
            _short = SYNTH_SHORT_MOD[_mod]
            _stations = select_synth_stations(
                _synth_roads, _synth_route, _synth_observed,
                SYNTH_SPACING_M[_mod], phase_m=_phase)
            _synth_stats["stations"] += len(_stations)
            _k = 0
            for (_key, _ex, _ey, _bearing, _side) in _stations:
                _cid = carriers[_key]["carrier_id"]
                _cclass = carriers[_key]["carrier_class"]
                _wside = "left" if _side == "L" else "right"
                _base_lat = road_lateral_distance(
                    _cid, "hardware", _wside)
                if _base_lat is None:
                    continue
                if _mod == "street_tree":
                    _base_lat += SYNTH_TREE_EXTRA_LATERAL_M
                _br = math.radians(_bearing)
                _nx, _ny = (-math.cos(_br), math.sin(_br)) \
                    if _side == "L" else (math.cos(_br), -math.sin(_br))
                _found = None
                for _dl in (_base_lat, _base_lat - 0.5, _base_lat + 0.5,
                            _base_lat - 1.0, _base_lat + 1.0):
                    if _dl < 0.1:
                        continue
                    _cx, _cy = _ex + _nx * _dl, _ey + _ny * _dl
                    if not point_in_ring(_cx, _cy, ring):
                        _synth_stats["dropped_ring"] += 1
                        continue
                    if _mass_conflicts(_cx, _cy):
                        _synth_stats["dropped_mass"] += 1
                        continue
                    _conf = placement_conforms(
                        surf, module_id=_mod, carrier_class=_cclass,
                        evidence_kind="rule", point=(_cx, _cy),
                        catalog_by_id=catalog_by_id)
                    if _conf.get("ok"):
                        _found = (_cx, _cy)
                        break
                    _synth_stats["dropped_conformance"] += 1
                if _found is None:
                    continue
                _fx, _fy = _found
                # A synthesized point nearer another road than its own sits at
                # a junction corner: the attachment refile hands it to that
                # road while its yaw follows the generating road (build_reg_55:
                # six trees/lamps/poles 91-180 deg off).  A pattern station has
                # no photo to settle which road it belongs to, so drop it.
                def _dpoly(_poly):
                    _b = 1e9
                    for _a, _c in zip(_poly, _poly[1:]):
                        _ddx, _ddy = _c[0] - _a[0], _c[1] - _a[1]
                        _ll = _ddx * _ddx + _ddy * _ddy or 1e-12
                        _tt = max(0.0, min(1.0, ((_fx - _a[0]) * _ddx + (_fy - _a[1]) * _ddy) / _ll))
                        _b = min(_b, math.hypot(_fx - _a[0] - _tt * _ddx, _fy - _a[1] - _tt * _ddy))
                    return _b
                _own_d = _dpoly(carriers[_key].get("polyline") or [])
                if any(_dpoly(_rv.get("polyline") or []) + 0.3 < _own_d
                       for _rk, _rv in _synth_roads.items() if _rk != _key):
                    _synth_stats["dropped_junction"] = _synth_stats.get("dropped_junction", 0) + 1
                    continue
                _rid = (f"rule-{_cid}-synth-{_short}-g{_k:02d}")
                out_carriers[_key].append({
                    "placement_id": _rid,
                    "module_id": _mod,
                    "variant": _variant,
                    "E": _fx, "N": _fy,
                    "height_m": plan_height(
                        _mod, None, mount_height(0)),
                    "yaw_deg": round(_bearing, 3),
                    "evidence": {
                        "kind": "rule",
                        "rule_source": (
                            "operator_authorized_surrounding_synthesis: "
                            f"{_mod} pattern spacing "
                            f"{SYNTH_SPACING_M[_mod]} m along OSM road "
                            "carriers near the route where the photo corpus "
                            "holds no streetscape; LiDAR shows canopy in "
                            "the corridor"),
                        "instance_id": None,
                        "position_uncertainty_m": 2.0,
                        "position_uncertainty_basis":
                            "synthesis_pattern_station"},
                })
                per_carrier_n[_key] += 1
                module_rule_counts[_mod] += 1
                rule_count += 1
                point_count += 1
                _synth_stats["kept"] += 1
                _k += 1
    print(f"[plan] synth_density: stations={_synth_stats['stations']} "
          f"kept={_synth_stats['kept']} "
          f"dropped_conformance={_synth_stats['dropped_conformance']} "
          f"dropped_mass={_synth_stats['dropped_mass']} "
          f"dropped_ring={_synth_stats['dropped_ring']} "
          f"dropped_junction={_synth_stats.get('dropped_junction', 0)}", flush=True)
    # A point observation of overhead wire is evidence of presence, not two
    # endpoints.  Close duplicate sightings through the authored pole network
    # and retain unsupported sightings as explicit non-emitting terminals.
    # This is a population decision; dispatch and lowering may not invent a
    # chord merely to make the representation buildable.
    _wire_span_dispositions = finalize_wire_span_dispositions([
        {
            "carrier_id": carriers[_key]["carrier_id"],
            "carrier_class": carriers[_key]["carrier_class"],
            "placements": out_carriers[_key],
        }
        for _key in sorted(carriers, key=str)
    ])

    _attach_stats = Counter()
    _terminal_population_stats = Counter()
    _attach_debts = []
    _attach_max_drift = 0.0
    _attach_over_50mm = 0
    for _akey in sorted(carriers, key=str):
        _cc = carriers[_akey]
        for _apl in out_carriers.get(_akey, []):
            _pid = _apl.get("placement_id")
            _aid = f"att-{_pid}"
            _mod = _apl.get("module_id")
            # FL-4876 Repair 2. Carries the measured reason a facade could
            # not reach a building face, so the unfilled slot can name it.
            _facade_face_miss = None
            _face_snap_refused = False
            _run = _apl.get("run")
            # FL-4876 Repair 2 (completion). The invariant at the bottom of
            # this loop asks "is this a facade?" of the catalog's
            # owning_domain. This predicate decides whether a facade is
            # face-tested at all. Asking the two questions of two different
            # owners is how independent_storefront_awning reached the
            # invariant reporting no_building_face_candidate_was_tested: the
            # report was right, the test never ran. One owner answers both.
            # The 6.0 m face_resolve tolerance is untouched; this only lets
            # the measurement happen.
            _is_facade_mod = (
                _mod in _FACADE_ATTACH_MODULES
                or (catalog_by_id.get(str(_mod or "")) or {}).get(
                    "owning_domain") in _FACADE_OWNING_DOMAINS)
            _population_disposition = _apl.get("population_disposition")
            if isinstance(_population_disposition, dict):
                _category = str(
                    _population_disposition.get("category") or "terminal")
                _apl["attachment"] = None
                _apl["attachment_state"] = "not_required:" + _category
                _apl["attachment_id"] = None
                _terminal_population_stats[_category] += 1
                continue
            # Representation footprint precedes attachment.  This provisional
            # envelope is the input to face fitting and span selection; it is
            # finalized again after canonical attachment resolution, before
            # dispatch.  Lowerers are never allowed to discover dimensions.
            if _apl.get("custom_identity_subject"):
                _custom_contract = custom_plane_contract(
                    _apl.get("custom_identity_subject"))
                _custom_dims = _apl.get("dims_m") or {}
                _apl["asset_physical_envelope"] = {
                    "anchor": "placement_anchor",
                    "longitudinal_extent_m": float(
                        _custom_dims.get("width_m")
                        or _custom_contract["width_m"]["min"]),
                    "depth_m": float(_custom_contract["depth_m"]),
                    "lateral_extent_m": float(_custom_contract["depth_m"]),
                    "vertical_extent_m": float(
                        _custom_dims.get("height_m")
                        or _custom_contract["height_m"]["min"]),
                    "face_clearance_m": max(
                        CUSTOM_IDENTITY_OUTWARD_M,
                        float(_custom_contract["depth_m"]) / 2.0),
                    "max_longitudinal_overhang_m": 0.0,
                    "fit_policy": "representation_band_before_attachment",
                }
            else:
                _apl["asset_physical_envelope"] = physical_envelope(
                    str(_mod or ""), _apl.get("variant"), _apl, rules)
                if (_run is None
                        and _mod in FRONTAGE_POINT_OUTWARD_M):
                    _apl["asset_physical_envelope"][
                        "face_clearance_m"] = \
                        FRONTAGE_POINT_OUTWARD_M[_mod]
            _apl["asset_physical_envelope"]["selection_stage"] = (
                "population_before_dispatch")
            _pre = _apl.pop("_pole_authored", False) and _apl.get("attachment")
            _rec = _pre if isinstance(_pre, dict) else None
            _state = ("attached" if (_rec or {}).get("record_type")
                      in (None, "attachment") else
                      f"debt:{_rec.get('reason', 'unattached')}")
            _cfeat = _attach_carrier_feature(_cc)
            if (_mod in _TRACKSIDE_MODULES and isinstance(_run, dict)
                    and _cfeat is not None
                    and _cfeat.get("semantic_class") != "railway_boundary"):
                # FL-4876 TRACKSIDE-01: a trackside run spans a railway, not
                # the road or building its registration ray happened to hit.
                _rail = _nearest_rail_carrier(
                    (float(_run["E0"]) + float(_run["E1"])) / 2.0,
                    (float(_run["N0"]) + float(_run["N1"])) / 2.0)
                if _rail is not None and float(_rail[0]) <= _carrier_tol_m \
                        and _rail[1] in _attach_index.by_id:
                    _cfeat = _attach_index.by_id[_rail[1]]
            _module_lateral = None
            if _mod is not None:
                _module_lateral = resolve_lateral(
                    module_surface_policy(
                        _mod, _cc.get("carrier_class", ""),
                        catalog_by_id)[0],
                    _cc.get("carrier_class", ""))
            _is_frontage_point = (
                _run is None
                and _mod is not None
                and _module_lateral == "building_front"
                and _cc.get("carrier_class") == "building")
            _is_rail_notice_point = (
                _run is None
                and _apl.get("custom_identity_subject") ==
                "candidate_rail_corridor_notice_sign")
            _is_custom_face_point = (
                _run is None
                and bool(_apl.get("custom_identity_subject"))
                and custom_mount_kind(
                    _apl.get("custom_identity_subject"))
                == "building_face_panel"
                and not _is_rail_notice_point)
            _is_face_point = _is_frontage_point or _is_custom_face_point
            _attachment_own_mass = None
            if _cc.get("carrier_class") == "building":
                if isinstance(_cc.get("carrier_id"), int):
                    _own_hit = canon_building_by_osm.get(_cc["carrier_id"])
                    _attachment_own_mass = (
                        _own_hit[0].get("feature_id") if _own_hit else None)
                elif _cc.get("carrier_id") in _mass_rings:
                    _attachment_own_mass = _cc.get("carrier_id")
            _pre_debt_reason = _apl.pop("_attachment_debt_reason", None)
            if (_rec is not None
                    and _rec.get("record_type") ==
                    "spatial_reconciliation_debt"
                    and _rec.get("reason") ==
                    "observed_point_inside_canonical_mass"):
                _reconciled = _reconcile_observed_point(
                    _apl, _aid, _attachment_own_mass)
                if _reconciled is not None:
                    _rec = _reconciled
                    _state = "attached"
            if _rec is None and _pre_debt_reason:
                _pre_conflicts = (((_apl.get("evidence") or {}).get(
                    "spatial_reconciliation") or {}).get(
                        "conflicting_mass_ids") or ["fully_clipped_run"])
                _rec = _mass_debt(
                    _apl, _aid, _pre_conflicts)
                _rec["reason"] = _pre_debt_reason
                _state = f"debt:{_pre_debt_reason}"
            if _rec is not None:
                _aid = _rec.get("attachment_id") or _aid
            if _rec is None and isinstance(_run, dict) and _is_facade_mod:
                _fring = None
                if _cfeat is not None and _cfeat.get("semantic_class") == "building_footprint":
                    _fring = _cfeat["geometry"]["coordinates"]
                else:
                    _best, _bring = None, None
                    _selected_bfid = None
                    _mx, _my = (_run["E0"] + _run["E1"]) / 2.0, (_run["N0"] + _run["N1"]) / 2.0
                    for _candidate_bfid, _bring0 in _mass_rings.items():
                        for _ea, _eb in _attach_ring_edges(_bring0):
                            # FL-4876 FACEDIST-01. Host selection for a run
                            # uses the same metric as everywhere else: the
                            # distance from the run midpoint to the nearest
                            # point on the edge, never to the edge midpoint.
                            _dd = math.sqrt(seg_dist2(
                                _mx, _my, _ea[0], _ea[1], _eb[0], _eb[1]))
                            if _best is None or _dd < _best:
                                _best = _dd
                                _bring = _bring0
                                _selected_bfid = _candidate_bfid
                    _fring = _bring
                    _cfeat = (canon_by_id.get(_selected_bfid)
                              if _selected_bfid else None)
                    # Same-doc rule (PLAN-07): face indices resolve against
                    # the canonical feature ring, never a drifted mass-ring
                    # copy. The mass ring only selects the host.
                    if _cfeat is not None and _cfeat.get("semantic_class") == "building_footprint":
                        _fring = _cfeat["geometry"]["coordinates"]
                if _fring is None:
                    _rec = {"record_type": "spatial_reconciliation_debt",
                            "attachment_id": _aid, "anchor_kind": "building_face",
                            "reason": "missing_host:no_building_ring", "residual_m": None}
                    _state = "debt:missing_host:no_building_ring"
                else:
                    _mx, _my = (_run["E0"] + _run["E1"]) / 2.0, (_run["N0"] + _run["N1"]) / 2.0
                    _edges = _attach_ring_edges(_fring)
                    _bfid2 = _cfeat.get("feature_id") if _cfeat else None
                    _wanted_len = max(0.01, float(
                        _run.get("length_m") or math.hypot(
                            _run["E1"] - _run["E0"],
                            _run["N1"] - _run["N0"])))

                    def _facade_face_candidate(_ei):
                        _ea, _eb = _edges[_ei]
                        _dx, _dy = _eb[0] - _ea[0], _eb[1] - _ea[1]
                        _seg = math.hypot(_dx, _dy)
                        if _seg <= 1e-9:
                            return None
                        _raw_t = ((_mx - _ea[0]) * _dx
                                  + (_my - _ea[1]) * _dy) / (_seg * _seg)
                        _fit_len = min(_wanted_len, _seg)
                        _half_t = _fit_len / (2.0 * _seg)
                        _t = min(1.0 - _half_t,
                                 max(_half_t, _raw_t))
                        _s0, _s1 = _t - _half_t, _t + _half_t
                        _e0, _n0 = _ea[0] + _s0 * _dx, _ea[1] + _s0 * _dy
                        _e1, _n1 = _ea[0] + _s1 * _dx, _ea[1] + _s1 * _dy
                        _bearing = math.degrees(math.atan2(_dy, _dx))
                        _candidate = {
                            "record_type": "attachment",
                            "anchor_kind": "building_face",
                            "attachment_id": _aid,
                            "canonical_building_id": _bfid2,
                            "canonical_face_id": f"{_bfid2}#f{_ei}",
                            "face_index": _ei,
                            "along_face_fraction": round(_t, 6),
                            "height_m": 0.0,
                            "outward_offset_m": 0.0,
                            "orientation_basis":
                                "outward_normal_of_canonical_face",
                            "edge_bearing_deg": round(_bearing, 3),
                        }
                        _pose = _resolve_attachment(
                            _candidate, _attach_index)
                        _conflicts = _mass_conflicts(
                            float(_pose["E"]), float(_pose["N"]), _bfid2)
                        _distance = math.sqrt(seg_dist2(
                            _mx, _my, _ea[0], _ea[1], _eb[0], _eb[1]))
                        return {
                            "attachment": _candidate,
                            "conflicts": _conflicts,
                            "distance": _distance,
                            "run": {"E0": _e0, "N0": _n0,
                                    "E1": _e1, "N1": _n1,
                                    "length_m": round(_fit_len, 3)},
                            "bearing": _bearing,
                        }

                    _candidates = sorted(
                        (row for row in (
                            _facade_face_candidate(_ei)
                            for _ei in range(len(_edges))) if row is not None),
                        key=lambda row: row["distance"])
                    _ev_kind = ((_apl.get("evidence") or {}).get(
                        "kind") or "rule")
                    _chosen = next(
                        (row for row in _candidates
                         if not row["conflicts"]), None)
                    if _chosen is None and _ev_kind != "observation":
                        _apl["population_disposition"] = {
                            "state": "intentional_omission",
                            "category": "no_exposed_canonical_facade_face",
                            "reason": (
                                "synthesized facade has no exposed host face; "
                                "party-wall geometry is not emitted"),
                        }
                        _apl["attachment"] = None
                        _apl["attachment_state"] = (
                            "not_required:no_exposed_canonical_facade_face")
                        _apl["attachment_id"] = None
                        _terminal_population_stats[
                            "no_exposed_canonical_facade_face"] += 1
                        continue
                    if _chosen is None:
                        # An empty candidate list and a list where every face
                        # is occluded by a mass are different measurements.
                        # Collapsing them into one reason string hides which
                        # one happened, so name the cause and carry the
                        # nearest face that was actually tested.
                        _near = _candidates[0] if _candidates else None
                        _cause = ("no_face_candidate_survived_mass_conflict"
                                  if _near is not None else
                                  "degenerate_geometry:no_usable_face")
                        _rec = {"record_type": "spatial_reconciliation_debt",
                                "attachment_id": _aid,
                                "anchor_kind": "building_face",
                                "reason": _cause,
                                "nearest_canonical_face_id": (
                                    _near["attachment"]["canonical_face_id"]
                                    if _near is not None else None),
                                "nearest_face_distance_m": (
                                    round(_near["distance"], 3)
                                    if _near is not None else None),
                                "conflicting_mass_ids": sorted({
                                    _c for _row in _candidates
                                    for _c in _row["conflicts"]}),
                                "residual_m": (
                                    round(_near["distance"], 3)
                                    if _near is not None else None)}
                        _state = "debt:" + _cause
                    else:
                        _rec = _chosen["attachment"]
                        _apl["run"] = _chosen["run"]
                        _run = _apl["run"]
                        _apl["E"] = ((_chosen["run"]["E0"]
                                      + _chosen["run"]["E1"]) / 2.0)
                        _apl["N"] = ((_chosen["run"]["N0"]
                                      + _chosen["run"]["N1"]) / 2.0)
                        _apl["yaw_deg"] = _chosen["bearing"]
                        if (_ev_kind == "observation"
                                or (_candidates
                                    and _chosen is not _candidates[0])):
                            _apl.setdefault("evidence", {})[
                                "spatial_reconciliation"] = {
                                    "kind": (
                                        "observed_facade_to_exposed_canonical_face"
                                        if _ev_kind == "observation" else
                                        "rule_facade_exposed_face_selection"),
                                    "canonical_face_id": _rec[
                                        "canonical_face_id"],
                                    "source_face_distance_m": round(
                                        _chosen["distance"], 3),
                                }
            elif _rec is None and isinstance(_run, dict):
                if _cfeat is None:
                    _evk = (_apl.get("evidence") or {}).get("kind")
                    if _evk == "observation":
                        _rec = _freestanding_rec(
                            _pid, _aid,
                            float((_run["E0"] + _run["E1"]) / 2.0),
                            float((_run["N0"] + _run["N1"]) / 2.0),
                            _module=_mod)
                        if _rec.get("record_type") != "attachment":
                            _state = f"debt:{_rec['reason']}"
                    else:
                        _rec = {"record_type": "spatial_reconciliation_debt",
                                "attachment_id": _aid, "anchor_kind": "carrier_span",
                                "reason": "missing_host:no_carrier_feature", "residual_m": None}
                        _state = "debt:missing_host:no_carrier_feature"
                else:
                    _poly = _cfeat["geometry"]["coordinates"]
                    _r0 = _attach_chainage(float(_run["E0"]), float(_run["N0"]), _poly)
                    _r1 = _attach_chainage(float(_run["E1"]), float(_run["N1"]), _poly)
                    if _r0 is None or _r1 is None:
                        _rec = {"record_type": "spatial_reconciliation_debt",
                                "attachment_id": _aid, "anchor_kind": "carrier_span",
                                "reason": "degenerate_geometry:unusable_carrier", "residual_m": None}
                        _state = "debt:degenerate_geometry:unusable_carrier"
                    else:
                        # Chord-frame side/lateral (POPPLAN-02): resolve()
                        # applies the offset along the chord normal, so the
                        # side and lateral are measured in that frame. The
                        # old E0-segment frame flips sides on curved
                        # carriers (10 m errors observed).
                        _q0x, _q0y, _q1x, _q1y = _r0[2], _r0[3], _r1[2], _r1[3]
                        _chx, _chy = _q1x - _q0x, _q1y - _q0y
                        _chl = math.hypot(_chx, _chy)
                        _pmx = (float(_run["E0"]) + float(_run["E1"])) / 2.0
                        _pmy = (float(_run["N0"]) + float(_run["N1"])) / 2.0
                        if _chl <= 1e-9:
                            _cb = _r0[4]
                            _mcx, _mcy = _q0x, _q0y
                        else:
                            _cb = math.degrees(math.atan2(_chy, _chx))
                            _mcx, _mcy = (_q0x + _q1x) / 2.0, (_q0y + _q1y) / 2.0
                        _nx = math.cos(math.radians(_cb + 90.0))
                        _ny = math.sin(math.radians(_cb + 90.0))
                        _s = (_pmx - _mcx) * _nx + (_pmy - _mcy) * _ny
                        _side = "R" if _s >= 0 else "L"
                        # Preserve both authored endpoints.  A single chord
                        # offset is lossy on a curved carrier and can move an
                        # already mass-clipped run back through the mass when
                        # the lowerer re-resolves it.
                        _n0x = math.cos(math.radians(_r0[4] + 90.0))
                        _n0y = math.sin(math.radians(_r0[4] + 90.0))
                        _n1x = math.cos(math.radians(_r1[4] + 90.0))
                        _n1y = math.sin(math.radians(_r1[4] + 90.0))
                        _s0 = ((float(_run["E0"]) - _r0[2]) * _n0x
                               + (float(_run["N0"]) - _r0[3]) * _n0y)
                        _s1 = ((float(_run["E1"]) - _r1[2]) * _n1x
                               + (float(_run["N1"]) - _r1[3]) * _n1y)
                        _rec = {"record_type": "attachment", "anchor_kind": "carrier_span",
                                "attachment_id": _aid,
                                "canonical_carrier_id": _cfeat.get("feature_id"),
                                "start_chainage_m": round(_r0[1], 3),
                                "end_chainage_m": round(_r1[1], 3),
                                "lateral_offset_m": round(abs(_s), 3),
                                "side": _side,
                                "start_lateral_offset_m": round(abs(_s0), 3),
                                "start_side": "R" if _s0 >= 0 else "L",
                                "end_lateral_offset_m": round(abs(_s1), 3),
                                "end_side": "R" if _s1 >= 0 else "L"}
            elif _rec is None and (_is_facade_mod or _is_face_point):
                _fx, _fy = float(_apl["E"]), float(_apl["N"])
                _fring = None
                _bfid3 = None
                if _cfeat is not None and _cfeat.get("semantic_class") == "building_footprint":
                    _fring = _cfeat["geometry"]["coordinates"]
                    _bfid3 = _cfeat.get("feature_id")
                else:
                    _best, _bring = None, None
                    for _bbfid, _bring0 in _mass_rings.items():
                        for _ea, _eb in _attach_ring_edges(_bring0):
                            # FL-4876 FACEDIST-01. How far a placement is from
                            # a wall is the distance to the nearest point ON
                            # that wall, for every placement class. The edge
                            # midpoint is a different quantity: on a long
                            # facade it reports metres of error even when the
                            # wall is directly alongside. It was applied to
                            # everything except a declared face point, so one
                            # question had two answers chosen by a flag that
                            # carries no geometric meaning.
                            _dd = math.sqrt(seg_dist2(
                                _fx, _fy, _ea[0], _ea[1],
                                _eb[0], _eb[1]))
                            if _best is None or _dd < _best:
                                _best, _bring, _bfid3 = _dd, _bring0, _bbfid
                    _fring = _bring
                if _fring is None:
                    _rec = {"record_type": "spatial_reconciliation_debt",
                            "attachment_id": _aid, "anchor_kind": "building_face",
                            "reason": "missing_host:no_building_ring", "residual_m": None}
                    _state = "debt:missing_host:no_building_ring"
                else:
                    _edges = _attach_ring_edges(_fring)
                    _face_candidates = []
                    for _ei, (_ea, _eb) in enumerate(_edges):
                        # FL-4876 FACEDIST-01. Same metric as the host
                        # selection above. This value both orders the
                        # candidates and decides the beyond-tolerance
                        # refusal, so the midpoint form refused placements
                        # that were inside the tolerance against the wall it
                        # then named. face_resolve_m stays 6.0.
                        _dd = math.sqrt(seg_dist2(
                            _fx, _fy, _ea[0], _ea[1],
                            _eb[0], _eb[1]))
                        _seg = math.hypot(
                            _eb[0] - _ea[0], _eb[1] - _ea[1]) or 1.0
                        _t = ((_fx - _ea[0]) * (_eb[0] - _ea[0])
                              + (_fy - _ea[1]) * (_eb[1] - _ea[1])) \
                            / (_seg * _seg)
                        _bearing = math.degrees(math.atan2(
                            _eb[1] - _ea[1], _eb[0] - _ea[0]))
                        _candidate = {
                            "record_type": "attachment",
                            "anchor_kind": "building_face",
                            "attachment_id": _aid,
                            "canonical_building_id": _bfid3,
                            "canonical_face_id": f"{_bfid3}#f{_ei}",
                            "face_index": _ei,
                            "along_face_fraction": round(
                                min(1.0, max(0.0, _t)), 6),
                            "height_m": 0.0,
                            "outward_offset_m": (
                                float((_apl.get("asset_physical_envelope")
                                       or {}).get("face_clearance_m", 0.15))
                                if _is_frontage_point else
                                float((_apl.get("asset_physical_envelope")
                                       or {}).get(
                                           "face_clearance_m",
                                           CUSTOM_IDENTITY_OUTWARD_M))
                                if _is_custom_face_point else 0.0),
                            "orientation_basis": (
                                "outward_normal_of_canonical_face"),
                            "edge_bearing_deg": round(_bearing, 3),
                        }
                        _pose = _resolve_attachment(
                            _candidate, _attach_index)
                        _face_candidates.append({
                            "attachment": _candidate,
                            "distance": _dd,
                            "pose": _pose,
                            "conflicts": _mass_conflicts(
                                float(_pose["E"]), float(_pose["N"]),
                                _bfid3),
                        })
                    _face_candidates.sort(key=lambda row: row["distance"])
                    _selected_face = next(
                        (row for row in _face_candidates
                         if not row["conflicts"]), None)
                    if _selected_face is None:
                        _rec = _mass_debt(
                            _apl, _aid,
                            sorted({
                                conflict
                                for row in _face_candidates
                                for conflict in row["conflicts"]
                            }) or [_bfid3 or "unresolved_host"])
                        _rec["anchor_kind"] = "building_face"
                        _rec["reason"] = "no_exposed_canonical_facade_face"
                        _state = f"debt:{_rec['reason']}"
                        _apl["attachment"] = _rec
                        _apl["attachment_state"] = _state
                        _apl["attachment_id"] = _aid
                    else:
                        _rec = _selected_face["attachment"]
                        _bd = _selected_face["distance"]
                        _pose = _selected_face["pose"]
                        if _bd > _face_tol_m and not _is_face_point:
                            _facade_face_miss = {
                                "cause": "nearest_face_beyond_resolve_tolerance",
                                "nearest_canonical_face_id": _rec.get(
                                    "canonical_face_id"),
                                "nearest_face_distance_m": round(_bd, 3),
                                "face_resolve_tolerance_m": _face_tol_m,
                            }
                            _rec = _freestanding_rec(
                                _pid, _aid, _fx, _fy, _module=_mod)
                            if _rec.get("record_type") != "attachment":
                                _state = f"debt:{_rec['reason']}"
                        elif (_is_face_point
                              and _bd > _face_snap_limit_m(_apl)
                              and not _mass_conflicts(_fx, _fy, _bfid3)
                              and _snap_refusal_site(_pid, _aid, _fx, _fy,
                                                     _mod) is not None):
                            # FL-4876 SNAP-CAP-01. A declared host does not
                            # license moving a point to its nearest face from
                            # any distance (build_reg_87: 29.5 m recorded, and
                            # the snap also overwrote yaw with the face
                            # normal). Past FACE_POINT_SNAP_MAX_M the authored
                            # point and yaw stay; the placement is realized
                            # where it was observed (on the carrier, else on
                            # its terrain) and the refusal is recorded on the
                            # placement. With no such site the snap stands.
                            _face_snap_refused = True
                            _apl.setdefault("evidence", {})[
                                "spatial_reconciliation"] = {
                                    "kind": "face_snap_refused_beyond_limit",
                                    "host_id": _bfid3,
                                    "nearest_canonical_face_id": _rec.get(
                                        "canonical_face_id"),
                                    "nearest_face_distance_m": round(_bd, 3),
                                    "snap_limit_m": round(
                                        _face_snap_limit_m(_apl), 3),
                                    "kept": "authored_point_and_yaw",
                                }
                            _rec = _snap_refusal_site(
                                _pid, _aid, _fx, _fy, _mod)
                        elif _is_face_point:
                            # Registered image geometry remains in evidence.
                            # The emitted point follows the declared frontage
                            # host so its footprint cannot sit in the mass.
                            _source_position = [
                                round(_fx, 6), round(_fy, 6)]
                            _apl.setdefault("evidence", {})[
                                "registered_position_epsg32654"] = \
                                _source_position
                            _apl["evidence"]["spatial_reconciliation"] = {
                                "kind": ("custom_identity_face_snap"
                                         if _is_custom_face_point else
                                         "building_frontage_face_snap"),
                                "host_id": _bfid3,
                                "canonical_face_id": _rec[
                                    "canonical_face_id"],
                                "source_face_distance_m": round(_bd, 3),
                                "outward_offset_m": _rec[
                                    "outward_offset_m"],
                            }
                            if _bd > FACE_POINT_SNAP_MAX_M:
                                # SNAP-CAP-01: no carrier or terrain site to
                                # keep the point on, so the snap stands; say so
                                _apl["evidence"]["spatial_reconciliation"][
                                    "snap_limit_exceeded_no_site"] = \
                                    FACE_POINT_SNAP_MAX_M
                            _apl["E"], _apl["N"] = (
                                float(_pose["E"]), float(_pose["N"]))
                            _apl["yaw_deg"] = float(_pose["yaw_deg"])
            elif _rec is None:
                _fx, _fy = float(_apl["E"]), float(_apl["N"])
                _hits = _mass_conflicts(
                    _fx, _fy, _attachment_own_mass)
                if ((_apl.get("evidence") or {}).get("kind") == "observation"
                        and _hits):
                    _rec = _reconcile_observed_point(
                        _apl, _aid, _attachment_own_mass)
                    if _rec is None:
                        _rec = _mass_debt(_apl, _aid, _hits)
                        _state = f"debt:{_rec['reason']}"
                else:
                    _rec = _freestanding_rec(_pid, _aid, _fx, _fy, _module=_mod)
                if _rec.get("record_type") != "attachment":
                    _state = f"debt:{_rec.get('reason', 'freestanding_debt')}"
            # A freestanding result may have been produced by an earlier
            # facade/custom fallback branch.  Observations inside a canonical
            # mass must terminate as evidence debt no matter which branch
            # produced that freestanding record; otherwise they silently
            # bypass the mass reconciliation performed in the final fallback.
            if (_rec is not None
                    and _rec.get("record_type") == "attachment"
                    and _rec.get("anchor_kind") == "freestanding"
                    and ((_apl.get("evidence") or {}).get("kind")
                         == "observation")):
                _free_hits = _mass_conflicts(
                    float(_apl["E"]), float(_apl["N"]), None)
                if _free_hits:
                    _reconciled = _reconcile_observed_point(
                        _apl, _aid, None)
                    if _reconciled is None:
                        _rec = _mass_debt(
                            _apl, _aid, _free_hits, _rec)
                        _state = f"debt:{_rec['reason']}"
                    else:
                        _rec = _reconciled
            # FL-4876 Repair 2. A facade is geometry on a building face.
            # When no canonical face resolves within the authoring tolerance
            # the honest outcome is an unfilled facade slot, not a facade
            # standing in the street. Demoting it to a freestanding fixture
            # set attachment_state to "attached", so the failure became
            # invisible to every downstream gate, and then stopped asset
            # authoring anyway because no freestanding archetype exists for a
            # facade module -- which is correct by design, not a gap.
            #
            # Routed through population_disposition so dispatch sends it to
            # the terminal owner, which authors no asset. The building's
            # mesh_facade row stays unsatisfied and the preassembly gate sees
            # it. Nothing here invents an attachment, widens a tolerance, or
            # asserts a shape nobody observed.
            # The test is anchor_kind != "building_face", not
            # anchor_kind == "freestanding". fixture_asset_contract derives
            # mount_kind as building_face_panel iff the anchor is
            # building_face and freestanding_fixture for everything else, so
            # a facade that lands on carrier_chainage -- which is what all
            # three of these do -- is already a freestanding fixture by the
            # time geometry is chosen, while never carrying that anchor name.
            if (_rec is not None
                    and not _face_snap_refused
                    and _rec.get("record_type") == "attachment"
                    and _rec.get("anchor_kind") != "building_face"
                    and ((catalog_by_id.get(str(_mod or "")) or {}).get(
                        "owning_domain") in _FACADE_OWNING_DOMAINS)):
                _miss = _facade_face_miss or {
                    "cause": "no_building_face_candidate_was_tested",
                    "nearest_canonical_face_id": None,
                    "nearest_face_distance_m": None,
                    "face_resolve_tolerance_m": _face_tol_m,
                }
                _apl["population_disposition"] = {
                    "state": "observation_owed",
                    "category": "unfilled_facade_slot",
                    "reason": (
                        "a facade module resolved to no canonical building "
                        "face; a facade demoted to a freestanding fixture "
                        "would assert a wall nobody observed and would "
                        "report itself as attached"),
                    "module_id": _mod,
                    "owning_domain": (catalog_by_id.get(str(_mod or ""))
                                      or {}).get("owning_domain"),
                    **_miss,
                }
                _rec = {
                    "record_type": "spatial_reconciliation_debt",
                    "attachment_id": _aid,
                    "anchor_kind": "building_face",
                    "reason": "unfilled_facade_slot:" + str(_miss["cause"]),
                    "residual_m": _miss.get("nearest_face_distance_m"),
                    **_miss,
                }
                _state = "debt:" + str(_rec["reason"])
            if _rec is not None and _rec.get("record_type") == "attachment":
                _akind = _rec.get("anchor_kind")
                if _akind == "freestanding":
                    pass
                else:
                    try:
                        from scripts.pipeline.plan_spatial_provenance import resolve_span as _rt_resolve
                        _pose = _rt_resolve(_rec, _attach_index)
                        if isinstance(_run, dict):
                            _pmx = (float(_run["E0"]) + float(_run["E1"])) / 2.0
                            _pmy = (float(_run["N0"]) + float(_run["N1"])) / 2.0
                        else:
                            _pmx, _pmy = float(_apl["E"]), float(_apl["N"])
                        _drift = math.hypot(float(_pose["E"]) - _pmx, float(_pose["N"]) - _pmy)
                        _attach_max_drift = max(_attach_max_drift, _drift)
                        if _drift > 0.05:
                            _attach_over_50mm += 1
                    except (ValueError, KeyError) as _exc:
                        _rec = {"record_type": "spatial_reconciliation_debt",
                                "attachment_id": _aid,
                                "anchor_kind": _rec.get("anchor_kind"),
                                "reason": f"resolve_failed:{_exc}", "residual_m": None}
                        _state = f"debt:resolve_failed:{_exc}"
            # FL-4876 UNPLACEABLE-01. Resolution is over. If it ended in an
            # unresolved debt and nothing has claimed the outcome, claim it
            # here, once, for every producer.
            #
            # _mass_debt() is a shared helper whose DEFAULT reason is
            # observed_point_inside_canonical_mass, and it is called from
            # several sites. Patching one of them left obs-inst-0110 --
            # a street light standing inside a building with no lawful
            # position inside its own sigma -- still carrying a debt record
            # and no population_disposition. Dispatch then routed it to a
            # GEOMETRY owner and refused the whole build:
            #
            #   population placement obs-inst-0110 reaches dispatch with
            #   unresolved attachment debt
            #
            # One unplaceable street light stopped 3010 resolved placements
            # from being authored. This is the single place where every
            # attachment outcome is finally written, so it is the owner.
            #
            # A disposition routes the placement to the terminal owner, which
            # authors nothing, and the preassembly gate still counts it. No
            # position is invented, nothing is moved, no obligation is
            # cleared, and dispatch's guard is untouched.
            if (isinstance(_rec, dict)
                    and _rec.get("record_type") == "spatial_reconciliation_debt"
                    and not isinstance(
                        _apl.get("population_disposition"), dict)):
                _cause = _rec.get("reason", "unnamed")
                _decided = _authored_decision_for(
                    _authored_decisions, _pid, _cause)
                _apl["population_disposition"] = {
                    "state": ("intentional_omission" if _decided
                              else "observation_owed"),
                    "category": ("operator_decided_omission" if _decided
                                 else "unresolved_attachment_debt"),
                    "reason": (
                        "attachment resolution ended in unresolved debt; the "
                        "placement authors no geometry and its contradiction "
                        "is recorded rather than resolved by invention"),
                    "cause": _cause,
                    "module_id": _mod,
                    "anchor_kind": _rec.get("anchor_kind"),
                    "residual_m": _rec.get("residual_m"),
                }
                if _decided:
                    # Who decided, when, and in whose words. A settled outcome
                    # that cannot name its author is indistinguishable from a
                    # rule that quietly swallowed an obligation.
                    _apl["population_disposition"].update({
                        "reason": _decided["reason"],
                        "decision_id": _decided["decision_id"],
                        "decided_by": _decided["decided_by"],
                        "decided_on": _decided["decided_on"],
                        "decision_record": _decided["decision_record"],
                        "decided_against_cause": _decided["against_cause"],
                    })
            # FL-4876 DECIDED-OMISSION-01. A disposition can also arrive
            # from an earlier owner -- the unfilled-facade-slot invariant sets
            # observation_owed long before this point. An operator decision
            # must settle those too, so the conversion is applied to whatever
            # owed disposition the row is carrying, against ITS OWN cause.
            _disp = _apl.get("population_disposition")
            if (isinstance(_disp, dict)
                    and _disp.get("state") == "observation_owed"
                    and "decision_id" not in _disp):
                _owed_cause = str(_disp.get("cause")
                                  or _disp.get("category") or "unnamed")
                _d2 = _authored_decision_for(
                    _authored_decisions, _pid, _owed_cause)
                if _d2 is not None:
                    _disp["state"] = "intentional_omission"
                    _disp["decided_from_state"] = "observation_owed"
                    _disp["decision_id"] = _d2["decision_id"]
                    _disp["decided_by"] = _d2["decided_by"]
                    _disp["decided_on"] = _d2["decided_on"]
                    _disp["decision_record"] = _d2["decision_record"]
                    _disp["decided_against_cause"] = _d2["against_cause"]
                    _disp["decision_reason"] = _d2["reason"]
            # FL-4876 FACING-01: a chainage attachment of a point whose owner
            # authored its facing carries that yaw, so resolve() does not
            # replace it with the carrier bearing.
            if (isinstance(_rec, dict)
                    and _rec.get("record_type") == "attachment"
                    and _rec.get("anchor_kind") in ("carrier_chainage",
                                                    "freestanding")
                    and not isinstance(_apl.get("run"), dict)
                    and ("authored_orientation_basis" in (
                        _apl.get("evidence") or {}) or _face_snap_refused)
                    and _apl.get("yaw_deg") is not None):
                _rec = {**_rec,
                        "authored_yaw_deg": round(float(_apl["yaw_deg"]), 3),
                        "authored_yaw_basis": (
                            _apl["evidence"].get("authored_orientation_basis")
                            or "face_snap_refused:registered_yaw_kept")}
            _apl["attachment"] = _rec
            _apl["attachment_state"] = _state
            _apl["attachment_id"] = _aid
            if _state == "attached":
                _final_envelope = physical_envelope(
                    str(_mod or ""), _apl.get("variant"), _apl, rules)
                _final_envelope.update({
                    key: value for key, value in
                    (_apl.get("asset_physical_envelope") or {}).items()
                    if key in ("depth_m", "face_clearance_m")
                })
                _final_envelope["selection_stage"] = (
                    "population_before_dispatch")
                _apl["asset_physical_envelope"] = _final_envelope
                _attach_stats[_rec.get("anchor_kind", "unknown")] += 1
            else:
                _attach_debts.append({"placement_id": _pid,
                                      "carrier_id": _cc.get("carrier_id"),
                                      "module_id": _apl.get("module_id"),
                                      "reason": _state})
    # FL-4876 MANUAL-SPATIAL-01.  A coherence check cannot repair a plan
    # which consistently emits the wrong zero offset.  Apply only exact,
    # operator-decided rows from the frozen bundle.  Source coordinates and
    # the before attachment remain in evidence; model/JEV proposals never
    # enter this path.
    _spatial_by_pid = {
        str(_p.get("placement_id")): _p
        for _plist in out_carriers.values() for _p in _plist
        if _p.get("placement_id") is not None
    }
    _spatial_by_cfid = {}
    for _plist in out_carriers.values():
        for _p in _plist:
            _cf = (_p.get("canonical_feature_id")
                   or (_p.get("evidence") or {}).get("canonical_feature_id"))
            if _cf and _p.get("module_id") == _POLE_MOD:
                _spatial_by_cfid.setdefault(str(_cf), _p)
    _spatial_applications = []

    def _spatial_before(_pl):
        _att = _pl.get("attachment") or {}
        return {
            "E": round(float(_pl["E"]), 6),
            "N": round(float(_pl["N"]), 6),
            "yaw_deg": round(float(_pl.get("yaw_deg") or 0.0), 6),
            "attachment": dict(_att),
            "run": dict(_pl["run"]) if isinstance(
                _pl.get("run"), dict) else None,
        }

    def _finish_spatial(_pl, _decision, _before):
        _after = {
            "E": round(float(_pl["E"]), 6),
            "N": round(float(_pl["N"]), 6),
            "yaw_deg": round(float(_pl.get("yaw_deg") or 0.0), 6),
            "attachment": dict(_pl.get("attachment") or {}),
            "run": dict(_pl["run"]) if isinstance(
                _pl.get("run"), dict) else None,
        }
        _pl.setdefault("evidence", {})["authored_spatial_application"] = {
            "decision_id": _decision["decision_id"],
            "action": _decision["action"],
            "before": _before,
            "after": _after,
        }
        _spatial_applications.append({
            "decision_id": _decision["decision_id"],
            "target_id": _decision["target_id"],
            "placement_id": _pl["placement_id"],
            "action": _decision["action"],
        })

    def _set_attachment_offset(_pl, _decision, _offset, _side=None):
        _att = _pl.get("attachment") or {}
        if _att.get("record_type") != "attachment" or _att.get(
                "anchor_kind") not in ("carrier_chainage", "carrier_span"):
            raise ValueError(
                f"authored spatial decision {_decision['decision_id']} needs "
                f"an attached carrier placement, got {_pl.get('placement_id')}")
        _before = _spatial_before(_pl)
        _side = _side or _att.get("side") or "R"
        if _side not in ("L", "R"):
            raise ValueError(
                f"authored spatial decision {_decision['decision_id']} has "
                f"invalid side {_side!r}")
        if _att["anchor_kind"] == "carrier_span":
            _att["lateral_offset_m"] = round(float(_offset), 3)
            for _prefix in ("start", "end"):
                _att[f"{_prefix}_lateral_offset_m"] = round(
                    float(_offset), 3)
                _att[f"{_prefix}_side"] = _side
            _att["side"] = _side
            _pose = _resolve_span_endpoints(_att, _attach_index)
            _pl["E"], _pl["N"] = _pose["E"], _pose["N"]
            _pl["yaw_deg"] = _pose["yaw_deg"]
            _pl["run"] = {
                "E0": _pose["E0"], "N0": _pose["N0"],
                "E1": _pose["E1"], "N1": _pose["N1"],
                "length_m": round(math.hypot(
                    _pose["E1"] - _pose["E0"],
                    _pose["N1"] - _pose["N0"]), 3),
            }
        else:
            _att["lateral_offset_m"] = round(float(_offset), 3)
            _att["side"] = _side
            _pose = _resolve_attachment(_att, _attach_index)
            _pl["E"], _pl["N"] = _pose["E"], _pose["N"]
            _pl["yaw_deg"] = _pose["yaw_deg"]
        _finish_spatial(_pl, _decision, _before)

    for _decision in _authored_spatial_decisions["rows"]:
        _action = _decision["action"]
        _params = _decision.get("parameters") or {}
        if _decision["target_kind"] == "placement" and _action in {
                "set_carrier_lateral_offset", "offset_from_mass_boundary"}:
            _target = _spatial_by_pid.get(_decision["target_id"])
            if _target is None:
                raise ValueError(
                    f"authored spatial target absent: {_decision['target_id']}")
            if _action == "set_carrier_lateral_offset":
                _set_attachment_offset(
                    _target, _decision,
                    float(_params["target_lateral_m"]),
                    str(_params.get("side") or
                        (_target.get("attachment") or {}).get("side") or "R"))
                continue
            _bfid = str(_params["building_feature_id"])
            _bfeat = _attach_index.by_id.get(_bfid)
            _bring = ((_bfeat or {}).get("geometry") or {}).get(
                "coordinates")
            if not isinstance(_bring, list) or len(_bring) < 3:
                raise ValueError(
                    f"authored spatial building absent or degenerate: {_bfid}")
            _best_edge = None
            _px, _py = float(_target["E"]), float(_target["N"])
            _cx = sum(float(_q[0]) for _q in _bring) / len(_bring)
            _cy = sum(float(_q[1]) for _q in _bring) / len(_bring)
            for _a, _b in _canonical_ring_edges(_bring):
                _dx, _dy = float(_b[0]) - float(_a[0]), float(_b[1]) - float(_a[1])
                _len2 = _dx * _dx + _dy * _dy
                if _len2 <= 1e-12:
                    continue
                _t = max(0.0, min(1.0, ((_px - float(_a[0])) * _dx
                                        + (_py - float(_a[1])) * _dy) / _len2))
                _qx = float(_a[0]) + _t * _dx
                _qy = float(_a[1]) + _t * _dy
                _d2 = (_px - _qx) ** 2 + (_py - _qy) ** 2
                if _best_edge is None or _d2 < _best_edge[0]:
                    _length = math.sqrt(_len2)
                    _nx, _ny = -_dy / _length, _dx / _length
                    _mx, _my = ((float(_a[0]) + float(_b[0])) / 2.0,
                                (float(_a[1]) + float(_b[1])) / 2.0)
                    if _nx * (_mx - _cx) + _ny * (_my - _cy) < 0.0:
                        _nx, _ny = -_nx, -_ny
                    _best_edge = (_d2, _qx, _qy, _nx, _ny)
            if _best_edge is None:
                raise ValueError(
                    f"authored spatial building has no usable edge: {_bfid}")
            _before = _spatial_before(_target)
            _out = float(_params["outward_distance_m"])
            _target["E"] = _best_edge[1] + _best_edge[3] * _out
            _target["N"] = _best_edge[2] + _best_edge[4] * _out
            _att = _target.get("attachment") or {}
            _cfid = _att.get("canonical_carrier_id")
            _cfeat = _attach_index.by_id.get(_cfid)
            _cpoly = ((_cfeat or {}).get("geometry") or {}).get("coordinates")
            _hit = _attach_chainage(_target["E"], _target["N"], _cpoly or [])
            if _hit is None:
                raise ValueError(
                    f"authored spatial target lost carrier {_cfid}")
            _att["chainage_m"] = round(float(_hit[1]), 3)
            _att["lateral_offset_m"] = round(float(_hit[0]), 3)
            _att["side"] = _attach_side(
                _target["E"], _target["N"], _hit[2], _hit[3], _hit[4])
            _target["yaw_deg"] = round(float(_hit[4]), 3)
            _finish_spatial(_target, _decision, _before)
            continue
        if (_decision["target_kind"] == "instance"
                and _action == "set_minimum_span_endpoint_lateral"):
            _endpoint_ids = list(_params.get("endpoint_placement_ids") or [])
            # Sequence-numbered obs-pole ids shift when an authored fixture
            # adopts the canonical pole; fall back to the decision's stable
            # canonical feature id for the same endpoint.
            _endpoint_cfids = list(_params.get("endpoint_canonical_feature_ids") or [None, None])
            _endpoints = [_spatial_by_pid.get(str(_pid))
                          or (_spatial_by_cfid.get(str(_cf)) if _cf else None)
                          for _pid, _cf in zip(_endpoint_ids, _endpoint_cfids)]
            if len(_endpoints) != 2 or any(_p is None for _p in _endpoints):
                raise ValueError(
                    f"authored span endpoints absent for {_decision['decision_id']}: "
                    f"{_endpoint_ids}")
            _selected = min(
                _endpoints,
                key=lambda _p: abs(float(
                    (_p.get("attachment") or {}).get("lateral_offset_m")
                    or 0.0)))
            _set_attachment_offset(
                _selected, _decision, float(_params["target_lateral_m"]),
                str((_selected.get("attachment") or {}).get("side") or "R"))
    # FL-4876 REFILE-01. One owner per placement: the canonical carrier its
    # attachment resolves against. Observations were filed under the carrier
    # their registration ray hit (register_observations.py) while the
    # attachment chose the nearest canonical carrier, so 96 of 304
    # chainage-attached observations in build_road_01 were filed under one
    # line and built along another, with a stored yaw from the first (fence
    # 0152: footway way/1087430627, 15.3 deg; attached to and emitted along
    # rail way/1038001322, 129 deg). Re-file each attached observation under
    # its attachment carrier and store the resolved yaw; keep the
    # registration carrier and yaw as evidence. Utility poles stay put: span
    # chaining orders them by chainage within their own host carrier.
    _key_by_feature = {}
    for _fkey in carriers:
        _ffeat = _attach_carrier_feature(carriers[_fkey])
        if _ffeat is not None:
            _key_by_feature.setdefault(_ffeat.get("feature_id"), _fkey)
    _refile_moves = []
    _refile_unmapped = Counter()
    for _fkey in sorted(carriers, key=str):
        _keep = []
        for _fpl in out_carriers.get(_fkey, []):
            _frec = _fpl.get("attachment") or {}
            _ftarget_fid = _frec.get("canonical_carrier_id")
            _movable = (
                (_fpl.get("evidence") or {}).get("kind") == "observation"
                and _fpl.get("module_id") != _POLE_MOD
                and _frec.get("record_type") == "attachment"
                and _frec.get("anchor_kind") in ("carrier_chainage", "carrier_span")
                and _ftarget_fid is not None)
            _ftarget = _key_by_feature.get(_ftarget_fid) if _movable else None
            if _movable and _ftarget is None:
                _refile_unmapped[str(_ftarget_fid).split(":")[1]] += 1
            if _ftarget is None or _ftarget == _fkey:
                _keep.append(_fpl)
                continue
            _fyaw = float(_resolve_attachment(_frec, _attach_index).get("yaw_deg") or 0.0)
            _fpl.setdefault("evidence", {})["registration_filing"] = {
                "carrier_id": carriers[_fkey]["carrier_id"],
                "carrier_class": carriers[_fkey]["carrier_class"],
                "yaw_deg": _fpl.get("yaw_deg"),
                "basis": ("observation registration ray hit; re-filed under the "
                          "attachment carrier by FL-4876 REFILE-01"),
            }
            if (_frec.get("anchor_kind") == "carrier_chainage"
                    and not isinstance(_fpl.get("run"), dict)
                    and "authored_orientation_basis" not in (_fpl.get("evidence") or {})):
                # Chainage units take the resolved carrier bearing downstream
                # (dispatch resolved_pose); run placements keep their chord.
                _fpl["yaw_deg"] = _fyaw
            _refile_moves.append({
                "placement_id": _fpl.get("placement_id"),
                "module_id": _fpl.get("module_id"),
                "from_carrier": carriers[_fkey]["carrier_id"],
                "from_class": carriers[_fkey]["carrier_class"],
                "to_carrier": carriers[_ftarget]["carrier_id"],
                "to_class": carriers[_ftarget]["carrier_class"],
            })
            out_carriers.setdefault(_ftarget, []).append(_fpl)
        out_carriers[_fkey] = _keep
    attachment_resolution = {
        "refiled_to_attachment_carrier": {
            "moved": len(_refile_moves),
            "unmapped_attachment_carriers": dict(sorted(_refile_unmapped.items())),
            "moves": sorted(_refile_moves, key=lambda r: str(r["placement_id"])),
        },
        "authored_spatial_applications": {
            "decisions_path": _authored_spatial_decisions["path"],
            "decisions_sha256": _authored_spatial_decisions["sha256"],
            "applied": sorted(
                _spatial_applications,
                key=lambda r: (str(r["decision_id"]), str(r["placement_id"]))),
        },
        "attached_total": sum(_attach_stats.values()),
        "by_kind": dict(sorted(_attach_stats.items())),
        "debts": sorted(_attach_debts, key=lambda r: str(r["placement_id"])),
        "terminal_without_attachment": dict(
            sorted(_terminal_population_stats.items())),
        "max_resolve_drift_m": round(_attach_max_drift, 4),
        "over_50mm": _attach_over_50mm,
        "mass_clip_gaps": mass_clip_gaps,
    }
    # verify this pin at entry and fail closed (PlanWorldMismatch) on
    # any drift instead of lowering best-effort. Timestamps are not
    # spatial generation (stripped by the hash rule).
    if _stamp_spatial_provenance is None:
        raise SystemExit(
            "spatial provenance needs scripts/pipeline/plan_spatial_provenance.py")
    _sp_frames = world_ir.get("coordinate_frames", {}) or {}
    _sp_frame = _sp_frames.get("epsg32654", {}) or {}
    _sp_crs = str(_sp_frame.get("crs") or "EPSG:32654")
    _sp_zone = "unknown"
    _sp_code = re.search(r":(\d+)$", _sp_crs)
    if _sp_code and 32600 < int(_sp_code.group(1)) < 32700:
        _sp_zone = f"{int(_sp_code.group(1)) - 32600}N"
    _sp_scale = None
    if args.terrain_metadata_path is not None:
        with open(args.terrain_metadata_path, encoding="utf-8") as _fh:
            _sp_scale = float(json.load(_fh)["content_scale"])
    spatial_provenance = _stamp_spatial_provenance(
        canonical_world_ir_path=args.world_ir,
        entities_path=args.entities_path,
        terrain_metadata_path=args.terrain_metadata_path,
        rules_path=args.rules,
        catalog_path=args.catalog,
        coordinate_frame={"name": _sp_crs, "utm_zone": _sp_zone,
                          "units": str(_sp_frame.get("units") or "metre")},
        world_units_per_metre=_sp_scale,
        planner_version=PLANNER_VERSION)
    doc = {
        "schema": "y92.numabe_population_plan.v1",
        "task_id": "NUMABE-PLAN-05",
        "aoi_bbox_epsg32654": bbox,
        "structural": {
            "policy_version": structural["policy_version"],
            "surfaces_source": structural["surfaces_source"],
            "checked": structural["checked"],
            "moved": structural["moved"],
            "clipped_segments": structural["clipped_segments"],
            "dropped": structural["dropped"],
            "by_module": structural["by_module"],
            "scale_band_violations": structural["scale_band_violations"],
            "residual_violations": structural["residual_violations"],
            "short_segments_skipped": structural["short_segments_skipped"],
            "dropped_observation_ids": structural[
                "dropped_observation_ids"],
            "observation_surface_conflicts": structural[
                "observation_surface_conflicts"],
            "observation_runs": structural["observation_runs"],
            "rail_edge": structural["rail_edge"],
            "orientation": structural["orientation"],
        },
        "aoi_bbox_epsg32654": bbox,
        "carriers": [{
            "carrier_id": carriers[key]["carrier_id"],
            "carrier_class": carriers[key]["carrier_class"],
            "span_index": carrier_geo[key]["span_index"],
            "placements": out_carriers[key],
        } for key in sorted(carriers, key=str)],
        "empty": empty_pairs,
        "carrier_population_dispositions": carrier_presence_only,
        "use_class_resolution": use_class_resolution,
        "attachment_resolution": attachment_resolution,
        "wire_span_dispositions": _wire_span_dispositions,
        "spatial_provenance": spatial_provenance,
    }

    os.makedirs(args.out_dir, exist_ok=True)
    plan_path = os.path.join(args.out_dir, "population_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")

    by_kind = Counter()
    by_domain = Counter()
    carriers_with = 0
    spine_n = off_n = 0
    for key in carriers:
        plist = out_carriers[key]
        if plist:
            carriers_with += 1
        for p in plist:
            by_kind[p["evidence"]["kind"]] += 1
            mid = p.get("module_id")
            if mid and mid in catalog_by_id:
                by_domain[catalog_by_id[mid]["owning_domain"]] += 1
            else:
                by_domain["custom_identity"] += 1
            if carrier_geo[key]["off_spine"]:
                off_n += 1
            else:
                spine_n += 1
    summary = [
        f"carriers_total: {len(carriers)}",
        f"carriers_with_any_placement: {carriers_with}",
        f"placements_total: {sum(by_kind.values())}",
    ]
    for k in ("observation", "rule", "osm"):
        summary.append(f"placements_evidence_{k}: {by_kind.get(k, 0)}")
    for dom, n in sorted(by_domain.items()):
        summary.append(f"placements_domain_{dom}: {n}")
    summary.append(f"empty_pairs: {len(empty_pairs)}")
    summary.append(
        "canonical_carriers_without_population_requirement: "
        f"{len(carrier_presence_only)}")
    summary.append(f"spine_placements_within_{SPINE_THRESHOLD_M:g}m: {spine_n}")
    summary.append(f"off_spine_placements: {off_n}")
    summary.append(f"dropped_outside_aoi_ring_rule_or_osm: {rule_dropped}")
    summary.append(f"dropped_obs_outside_terrain_bbox: {len(dropped_outside)}")
    summary.append(f"dropped_outside_aoi_ring_observations: {len(dropped_outside)}")
    inst_by_id = {i["instance_id"]: i for i in instances}
    for iid in sorted(dropped_outside):
        _oi = inst_by_id.get(iid, {})
        summary.append(f"dropped_observation_outside_ring: {iid} "
                       f"E={_oi.get('E')} N={_oi.get('N')}")
    summary.append(f"continuous_runs: {run_count}")
    summary.append(f"continuous_run_length_total_m: {round(run_length_total, 1)}")
    summary.append(f"point_placements: {point_count}")
    summary.append(f"pole_canonical_features: {_pole_stats['canonical_poles']}")
    summary.append(f"pole_observed_adopted: {_pole_stats['observed_adopted']} pole_observed_new: {_pole_stats['observed_new']} pole_unhosted: {_pole_stats['observed_unhosted']} pole_outside_aoi: {_pole_stats['outside_aoi']}")
    summary.append(f"pole_rule_filled: {_pole_stats['rule_filled']} pole_generic_bypassed: {pole_generic_bypassed} pole_carriers: {_pole_stats['carriers_with_poles']}")
    for _gp in _pole_stats["gaps"]:
        summary.append(f"pole_gap_fill: carrier {_gp['carrier_id']} from {_gp['from_m']} to {_gp['to_m']} rule_at {_gp['rule_chainage_m']}")
    for _gu in _pole_stats.get("unfilled", []):
        summary.append(f"pole_gap_unfilled: carrier {_gu['carrier_id']} from {_gu['from_m']} to {_gu['to_m']} rule_at {_gu['rule_chainage_m']} reason={_gu['reason']}")
    summary.append(f"structural_policy: {STRUCTURAL_POLICY_VERSION}")
    summary.append(f"structural_checked: {structural['checked']}")
    summary.append(f"structural_moved: {structural['moved']}")
    summary.append(
        f"structural_clipped_segments: {structural['clipped_segments']}")
    summary.append(f"structural_dropped: {structural['dropped']}")
    summary.append(
        f"structural_residual_violations: "
        f"{structural['residual_violations']}")
    summary.append(
        f"structural_scale_band_violations: "
        f"{structural['scale_band_violations']}")
    summary.append(
        f"structural_observation_runs: created="
        f"{structural['observation_runs']['created']} kept_as_point="
        f"{structural['observation_runs']['kept_as_point']} extent_source="
        f"{structural['observation_runs']['extent_source']} clipped="
        f"{structural['observation_runs']['clipped']} "
        f"point_yaw_rederived="
        f"{structural['observation_runs']['point_yaw_rederived']}")
    summary.append(
        f"structural_orientation: checked="
        f"{structural['orientation']['checked']} violations="
        f"{structural['orientation']['violations']} exempt_observations="
        f"{structural['orientation']['exempt_observations']}")
    summary.append("structural_by_module:")
    for mod in sorted(structural["by_module"]):
        d = structural["by_module"][mod]
        summary.append(f"  {mod}: moved={d['moved']} clipped={d['clipped']} "
                       f"dropped={d['dropped']} residual={d['residual']}")
    summary.append(f"bilateral_carrier_modules: {n_bi}")
    summary.append(f"bilateral_both_sides: {n_both}")
    summary.append(f"bilateral_L_runs: {n_bl} bilateral_R_runs: {n_br}")
    summary.append(f"bilateral_final_pairs: {len(final_sides)}")
    summary.append(f"bilateral_final_both_sides: {n_fboth}")
    summary.append(f"bilateral_final_one_sided: "
                   f"{len(bilateral_final_one_sided)}")
    for e in bilateral_final_one_sided:
        summary.append(f"  one_sided: carrier {e['carrier_id']} "
                       f"{e['module']} missing_{e['missing_side']} "
                       f"cause={e['cause']} kept={e['kept']}")
    summary.append(f"max_placements_any_carrier: {max_per_carrier}")
    road_keys = [k for k, c in carriers.items()
                 if c["carrier_class"].startswith("road:")]
    g_sw = sum(1 for k in road_keys
               if sidewalk_positive(carrier_geo[k]["span_index"] or 0))
    g_x = sum(1 for k in road_keys if carrier_geo[k]["near_crossing"])
    g_rail = sum(1 for k in road_keys if carrier_geo[k]["rail_adjacent"])
    g_all = sum(1 for k in road_keys
                if sidewalk_positive(carrier_geo[k]["span_index"] or 0)
                or carrier_geo[k]["near_crossing"]
                or carrier_geo[k]["rail_adjacent"])
    summary.append(f"guardrail_qualifying_road_carriers: {g_all} of {len(road_keys)} "
                   f"(sidewalk_width_gt_0 {g_sw}; crossing_approach_15m {g_x}; "
                   f"railway_adjacent_footway {g_rail})")
    summary.append(f"guardrail_runs_on_building_carriers: {n_bld_guard}")
    summary.append(f"buildings_total: {len(bld_ids)}")
    n_filled = sum(1 for k in bld_keys
                   if k in obs_facade_filled or any(
                       p["placement_id"].startswith("rule-facade-")
                       for p in out_carriers[k]))
    summary.append(f"buildings_with_facade_slot: {n_filled} "
                   f"(archetype_run {facade_stats['runs']} + "
                   f"observation_filled {facade_stats['obs_filled']})")
    summary.append(f"facade_archetype_rule_runs: {facade_stats['runs']}")
    summary.append("facade_rule_per_module:")
    for mod in sorted(FACADE_ARCHETYPE_MODS):
        summary.append(f"  {mod}: {module_rule_counts.get(mod, 0)}")
    summary.append("facade_class_split:")
    for cls in ("residential", "commercial", "mixed"):
        summary.append(f"  {cls}: {facade_class_counts.get(cls, 0)}")
    summary.append("facade_height_basis:")
    for basis in ("building:levels", "height_tag", "rules_default"):
        summary.append(f"  {basis}: {facade_height_basis.get(basis, 0)}")
    summary.append(f"facade_height_default_m: {FACADE_DEFAULT_HEIGHT_M} "
                   f"(rules DEFAULT; levels x {LEVEL_HEIGHT_M} m where tagged)")
    summary.append(f"storefront_rule_runs: {storefront_run_count}")
    summary.append(f"sign_rule_points: {sign_point_count}")
    summary.append(f"boundary_rule_runs: {boundary_run_count}")
    summary.append(f"interior_lots: {len(interior_lots)}")
    summary.append(f"facade_run_ring_clipped: {facade_stats['clipped']}")
    summary.append(f"facade_fallback_edge: {facade_stats['fallback_edge']} "
                   f"(true front outside ring; run on nearest clippable edge)")
    summary.append(f"facade_dropped_outside_ring: {facade_stats['dropped']}")
    summary.append(f"facade_unfillable_slots: {len(facade_unfillable)} "
                   f"(boundary straddlers with no >= 1 m in-ring face; "
                   f"falsifier-exempt, slot cannot be built)")
    for key in sorted(facade_unfillable, key=str):
        summary.append(f"  unfillable_facade_carrier: "
                       f"{carriers[key]['carrier_id']}")
    summary.append("rule_placements_per_module:")
    for mod, n in sorted(module_rule_counts.items()):
        summary.append(f"  {mod}: {n}")
    summary.append("density_prior_per_module_per_m:")
    for mod in sorted(density):
        summary.append(f"  {mod}: {density[mod]:.6f}")
    summary.append("observed_density_denominator_m: "
                   f"{round(obs_length_total, 1)} over {len(obs_carrier_keys)} carriers")
    summary.append("spacing_cap: none (rules_resolved carries no numeric spacing basis; density governs)")
    summary.append("band_statement: rule total below 2000 because guardrail and "
                   "road-edge families were removed from building carriers, each "
                   "building carrier carries slot placements only (one facade "
                   "run, one boundary run, one storefront run plus one sign "
                   "point), and point modules are density-bounded with no "
                   "numeric spacing cap")
    summary.append("below_band_reason_per_module:")
    for mod in sorted(set(module_rule_counts) | set(density)):
        n = module_rule_counts.get(mod, 0)
        dom = (catalog_by_id.get(mod) or {}).get("owning_domain")
        if mod in FACADE_ARCHETYPE_MODS:
            why = ("facade slot: one archetype run per building front edge "
                   "(commercial/mixed take narrow_mixed_use, residential takes "
                   "residential_facade_roofed_entry); observations fill the "
                   "rest")
        elif dom == "generic_storefront":
            why = ("commercial slot: shop/amenity/office-tagged buildings "
                   "only (none tagged in this AOI)")
        elif mod in GUARDRAIL_MODS:
            why = "guardrail road-edge hardware: road/footway carriers only, never buildings"
        elif mod in ROAD_EDGE_MODS:
            why = "road-edge family: road carriers only"
        elif is_drain_module(mod):
            why = "drain/channel: open waterway carriers only; culverts excluded"
        elif mod in RAIL_MODS or mod.startswith("rail_"):
            why = "rail hardware: railway carriers only; crossing equipment at crossing nodes"
        elif is_commercial_module(mod):
            why = "commercial: shop-tagged or place-proximate buildings only"
        elif mod in RESIDENTIAL_WALL_MODS or "fence" in mod.lower():
            why = "one boundary run per residential street-facing building"
        else:
            why = "density-bounded points and one module per family slot"
        summary.append(f"  {mod}: rule={n} prior={density.get(mod, 0.0):.6f} {why}")
    summary.append("family_basis_table:")
    basis_table = defaultdict(set)
    for key in carriers:
        for f in carriers[key]["families"]:
            s = family_basis(f)
            basis_table[f].add(s or "NONE")
    for fam in sorted(basis_table):
        summary.append(f"  {fam}: {sorted(basis_table[fam])[0]}")
    summary_path = os.path.join(args.out_dir, "evidence_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary) + "\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    fig, ax = plt.subplots(figsize=(12, 12))
    rx = [p[0] for p in ring] + [ring[0][0]]
    ry = [p[1] for p in ring] + [ring[0][1]]
    ax.plot(rx, ry, color="black", linewidth=1.2, label="AOI ring")
    for key in carriers:
        poly = carriers[key]["polyline"]
        if len(poly) > 1:
            ax.plot([p[0] for p in poly], [p[1] for p in poly],
                    color="#bbbbbb", linewidth=0.4)
    segs = []
    facade_segs = []
    for plist in out_carriers.values():
        for p in plist:
            if "run" in p:
                r = p["run"]
                seg = [(r["E0"], r["N0"]), (r["E1"], r["N1"])]
                if p["placement_id"].startswith("rule-facade-"):
                    facade_segs.append(seg)
                else:
                    segs.append(seg)
    if segs:
        ax.add_collection(LineCollection(segs, colors="tab:orange",
                                         linewidths=1.0, alpha=0.8,
                                         label=f"runs ({len(segs)})"))
    if facade_segs:
        ax.add_collection(LineCollection(facade_segs, colors="tab:blue",
                                         linewidths=1.2, alpha=0.9,
                                         label=f"facade runs ({len(facade_segs)})"))
    colors = {"observation": "tab:blue", "rule": "tab:orange", "osm": "tab:green"}
    for kind in ("osm", "rule", "observation"):
        xs = [p["E"] for plist in out_carriers.values() for p in plist
              if p["evidence"]["kind"] == kind and "run" not in p]
        ys = [p["N"] for plist in out_carriers.values() for p in plist
              if p["evidence"]["kind"] == kind and "run" not in p]
        ax.scatter(xs, ys, s=6 if kind != "osm" else 14, color=colors[kind],
                   label=f"{kind} pts ({len(xs)})", alpha=0.7)
    ax.set_aspect("equal")
    ax.set_xlabel("E (EPSG:32654)")
    ax.set_ylabel("N (EPSG:32654)")
    ax.set_title(f"Numabe population plan: {sum(by_kind.values())} placements, "
                 f"{len(empty_pairs)} empty pairs")
    ax.legend(markerscale=2)
    png_path = os.path.join(args.out_dir, "population_plan.png")
    fig.savefig(png_path, dpi=150)
    plt.close(fig)

    print(f"carriers={len(carriers)} with_placement={carriers_with} "
          f"obs={by_kind.get('observation', 0)} rule={rule_count} "
          f"osm={osm_count} runs={run_count} points={point_count} "
          f"maxcarrier={max_per_carrier} empty={len(empty_pairs)} "
          f"dropped_obs={len(dropped_outside)} dropped_other={rule_dropped}")
    print(f"attachment={attachment_resolution['by_kind']} "
          f"debts={len(attachment_resolution['debts'])} "
          f"maxdrift={attachment_resolution['max_resolve_drift_m']} "
          f"over50mm={attachment_resolution['over_50mm']} "
          f"gaps={attachment_resolution['mass_clip_gaps']}")
    print(f"use_class={use_class_resolution['by_source']} "
          f"resited={use_class_resolution['resited_osm_rings']} "
          f"added_plateau={use_class_resolution['added_plateau_carriers']} "
          f"road_claim={use_class_resolution['road_claim_agree']}/"
          f"{use_class_resolution['road_claim_missing']}/"
          f"{use_class_resolution['road_claim_disagree']} "
          f"excluded={len(use_class_resolution['excluded'])}")
    print(f"wrote {plan_path}")
    print(f"wrote {png_path}")
    print(f"wrote {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
