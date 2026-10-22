#!/usr/bin/env python3
"""Ingest a frozen Overpass ``.osm`` extract into a Y92 World IR document.

FL-4809 ingest owner, sibling of ``world_ir_ingest_carto.py``.  Sits
DELIBERATELY OUTSIDE ``scripts/pipeline/world_ir/`` for the reason that owner
states: the IR vocabulary names no source, so every mapping from OSM's private
tag vocabulary onto the closed class registry lives here.

WHAT THIS OWNER DOES THAT THE CARTO OWNER DOES NOT
==================================================
``world_ir_ingest_carto.py`` ingests a *bake run* - geometry that has already
been projected into one run's ``a3d_world`` frame by the stamper.  This owner
ingests the **raw frozen source**: OSM XML in EPSG:4326, before any bake exists.
That means it also owns three things the carto path never had to face:

1.  **The extract is a superset of the AOI.**  The Overpass query shape
    (``scripts/sbu_e2e_run.py:487``, reused verbatim by the frozen bundle) ends
    ``out body; >; out skel qt;``.  The ``>`` recursion pulls in every member of
    every relation that touches the bbox, so a 215 x 165 m corridor arrives with
    11116 ways, most of them tens of kilometres away.  Ingest clips to the AOI
    polygon and reports what it dropped; it does not quietly ingest Japan.

2.  **Overpass emits every element twice.**  ``out body`` writes the element
    with its tags, then ``out skel qt`` writes the SAME id again as a skeleton
    with geometry and NO tags.  A naive ``{id: tags}`` dict is last-wins, so
    every way silently loses its tags: an early pass of this ingest reported
    zero ``railway=rail`` ways in an AOI the frozen manifest says holds 19.
    ``parse_osm`` merges the two copies instead (tags unioned, longest node list
    kept) and reports the duplicate count so the trap stays visible.

3.  **Projection.**  Source geometry stays in EPSG:4326 on the observation -
    what OSM said, unaltered - and the canonical feature carries the same
    position re-expressed in EPSG:32654 metres by ``geo_frames``, whose residual
    against PROJ and GDAL is reported by its own ``--selftest``.

AUTHORITY IS EARNED BY GEOMETRIC DIMENSION, NOT BY CLASS NAME
=============================================================
A building way is a closed measured ring, so it bounds a volume and
``collision`` is justified.  A ``highway=residential`` way is a **centreline
with no width**: it cannot by itself decide where a player may stand, so it
gets ``presentation_only`` and the width has to be solved downstream from real
evidence.  Nothing synthesized claims collision or walkability anywhere in this
module - every physical authority here names a ``geometry`` claim whose
provenance is ``measured``.

THE ANCHOR SET
==============
``--anchors-out`` writes the OSM ANCHOR SET that FL-4812 registers other
evidence against.  It is a SIBLING artifact, not an IR collection, because
"anchor" is not a semantic class and the closed registry in
``world_ir/schema.py`` is owned by FL-4811.  Every anchor is a real digitised
OSM node - never an interpolated point - and carries the id of the IR feature
and observation it came from, so the two documents join without either one
having to know the other's shape.

USAGE
=====
    python3 scripts/pipeline/world_ir_ingest_osm_extract.py \
        --bundle assets/source_bundles/numabe_corridor_20260911 \
        --out numabe_osm.world_ir.json \
        --anchors-out numabe_osm.anchor_set.json \
        --summary numabe_osm.ingest.json --validate
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.world_ir import WorldIRStore, schema as S  # noqa: E402

ANCHOR_SET_VERSION = "y92.osm_anchor_set/1.0.0"

# Declared up front so an anchor type with no members is REPORTED AS ZERO rather
# than being missing from the counts.  "no wall anchors" and "we forgot to look
# for wall anchors" must not produce the same document.
ANCHOR_TYPES: tuple[str, ...] = (
    "building_corner",
    "crossing_corner",
    "railway_vertex",
    "road_intersection",
    "station_entrance",
    "wall_fence_endpoint",
)

# Why an anchor type can legitimately come back empty for one AOI.
ANCHOR_TYPE_SOURCE: dict[str, str] = {
    "building_corner": "ring vertices of kept building_footprint ways",
    "crossing_corner": "kept crossing nodes and the endpoints of kept crossing ways",
    "railway_vertex": "vertices of kept railway_boundary ways that lie inside the AOI",
    "road_intersection": "nodes shared by two or more kept road/sidewalk/crossing ways",
    "station_entrance": "kept station_entrance nodes",
    "wall_fence_endpoint": "endpoints of kept wall/fence ways",
}

# OSM node positions are a digitised vector source, not a sensor.  The spread is
# the digitising tolerance, already expressed once in this repo as
# ``world_ir_ingest_carto.py:OSM_POSITION_SIGMA = 1.5`` a3d world units.  Metres
# retain that historical metric tolerance independently of runtime presentation
# calibration. Changing player scale must never improve source accuracy.
OSM_POSITION_SIGMA_WORLD_UNITS = 1.5
OSM_POSITION_SIGMA_REFERENCE_WORLD_UNITS_PER_METRE = 2.25
OSM_POSITION_SIGMA_M = (
    OSM_POSITION_SIGMA_WORLD_UNITS / OSM_POSITION_SIGMA_REFERENCE_WORLD_UNITS_PER_METRE
)

# A corner is only a usable registration target when two wall planes actually
# meet there.  Below this turn, the vertex is a digitising kink on what a camera
# or a point cloud will see as one straight wall.
CORNER_MIN_TURN_DEG = 30.0


# ==========================================================================
# OSM XML
# ==========================================================================


class OSMExtract:
    """A parsed Overpass extract with its duplicate elements merged."""

    def __init__(self) -> None:
        self.nodes: dict[str, tuple[float, float]] = {}
        self.node_tags: dict[str, dict[str, str]] = {}
        self.ways: dict[str, list[str]] = {}
        self.way_tags: dict[str, dict[str, str]] = {}
        self.relation_tags: dict[str, dict[str, str]] = {}
        self.relation_members: dict[str, list[tuple[str, str, str]]] = {}
        self.stats: dict[str, Any] = {}

    def way_points(self, way_id: str) -> list[tuple[float, float]]:
        return [self.nodes[r] for r in self.ways.get(way_id, ()) if r in self.nodes]

    def is_closed(self, way_id: str) -> bool:
        refs = self.ways.get(way_id) or []
        return len(refs) >= 4 and refs[0] == refs[-1]


def parse_osm(path: Path) -> OSMExtract:
    """Parse Overpass XML, merging the ``out body`` and ``out skel qt`` copies.

    Merge rule: tags are UNIONED (the skeleton copy has none, so a last-wins
    dict would blank them) and the longest node list wins (the skeleton copy is
    the one guaranteed to carry geometry).
    """
    root = ET.parse(path).getroot()
    ex = OSMExtract()
    dup_nodes = dup_ways = dup_rels = 0
    node_elems = way_elems = rel_elems = 0

    for el in root:
        if el.tag == "node":
            node_elems += 1
            oid = el.get("id")
            if oid in ex.nodes:
                dup_nodes += 1
            ex.nodes[oid] = (float(el.get("lon")), float(el.get("lat")))
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            if tags:
                ex.node_tags.setdefault(oid, {}).update(tags)
        elif el.tag == "way":
            way_elems += 1
            oid = el.get("id")
            refs = [nd.get("ref") for nd in el.findall("nd")]
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            if oid in ex.ways:
                dup_ways += 1
                if len(refs) > len(ex.ways[oid]):
                    ex.ways[oid] = refs
            else:
                ex.ways[oid] = refs
            if tags:
                ex.way_tags.setdefault(oid, {}).update(tags)
        elif el.tag == "relation":
            rel_elems += 1
            oid = el.get("id")
            if oid in ex.relation_tags or oid in ex.relation_members:
                dup_rels += 1
            tags = {t.get("k"): t.get("v") for t in el.findall("tag")}
            if tags:
                ex.relation_tags.setdefault(oid, {}).update(tags)
            members = [
                (m.get("type"), m.get("ref"), m.get("role") or "")
                for m in el.findall("member")
            ]
            if members:
                ex.relation_members[oid] = members

    ex.stats = {
        "node_elements_read": node_elems,
        "way_elements_read": way_elems,
        "relation_elements_read": rel_elems,
        "unique_nodes": len(ex.nodes),
        "unique_ways": len(ex.ways),
        "unique_relations": max(len(ex.relation_tags), len(ex.relation_members)),
        "duplicate_node_elements_merged": dup_nodes,
        "duplicate_way_elements_merged": dup_ways,
        "duplicate_relation_elements_merged": dup_rels,
        "tagged_nodes": len(ex.node_tags),
        "tagged_ways": len(ex.way_tags),
        "multipolygon_relations": sum(
            1 for t in ex.relation_tags.values() if t.get("type") == "multipolygon"
        ),
    }
    return ex


# ==========================================================================
# AOI clip
# ==========================================================================


def point_in_ring(pt: Sequence[float], ring: Sequence[Sequence[float]]) -> bool:
    """Ray-cast point-in-polygon on a closed ring of [lon, lat] pairs."""
    x, y = pt[0], pt[1]
    inside = False
    for i in range(len(ring) - 1):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[i + 1][0], ring[i + 1][1]
        if (y0 > y) != (y1 > y):
            x_cross = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
            if x < x_cross:
                inside = not inside
    return inside


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def segments_cross(p1, p2, p3, p4) -> bool:
    d1, d2 = _cross(p3, p4, p1), _cross(p3, p4, p2)
    d3, d4 = _cross(p1, p2, p3), _cross(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def polyline_touches_ring(points: Sequence[Sequence[float]],
                          ring: Sequence[Sequence[float]]) -> bool:
    """True when any vertex is inside the ring OR any edge crosses it.

    The edge test is what keeps a way that passes straight through the corridor
    without placing a vertex inside it - a long rail or trunk segment - from
    being dropped.
    """
    if any(point_in_ring(p, ring) for p in points):
        return True
    for a, b in zip(points, points[1:]):
        for i in range(len(ring) - 1):
            if segments_cross(a, b, ring[i], ring[i + 1]):
                return True
    return False


def ring_centroid(points: Sequence[Sequence[float]]) -> tuple[float, float]:
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


# ==========================================================================
# OSM vocabulary -> the IR's closed semantic class registry
# ==========================================================================
#
# Ordered rules, first match wins.  Each rule is (predicate, class, why).  The
# ``why`` text is not decoration: it lands in the feature's semantic_class claim
# so a reader can see which tag decided the class without re-reading this file.

_RAILWAY_TRACK = {"rail", "light_rail", "subway", "tram", "narrow_gauge",
                  "monorail", "funicular", "preserved", "disused"}
_ROAD_HIGHWAY = {"motorway", "trunk", "primary", "secondary", "tertiary",
                 "unclassified", "residential", "service", "living_street",
                 "road", "motorway_link", "trunk_link", "primary_link",
                 "secondary_link", "tertiary_link", "busway"}
_FOOT_HIGHWAY = {"footway", "path", "steps", "pedestrian", "corridor",
                 "bridleway", "cycleway"}
_TERRAIN_LANDUSE = {"grass", "meadow", "forest", "orchard", "village_green",
                    "recreation_ground", "allotments", "farmland", "cemetery",
                    # FL-4876. A brownfield is ground, so it is terrain. It
                    # was left out because registering it would have stamped
                    # it pedestrian_area -- a walkable public plaza, which is
                    # a physical claim nobody made. The stamp planner now
                    # gives it the authored "land" role instead, so the
                    # feature can be registered without asserting that.
                    "brownfield"}
_TERRAIN_NATURAL = {"water", "wood", "scrub", "grassland", "wetland", "sand",
                    "beach", "bare_rock", "heath"}
_TERRAIN_WATERWAY = {"stream", "river", "ditch", "drain", "canal", "riverbank"}
_TERRAIN_LEISURE = {"park", "garden", "pitch", "playground", "golf_course"}
_WALL_BARRIER = {"wall", "retaining_wall", "city_wall", "jersey_barrier"}
_FENCE_BARRIER = {"fence", "guard_rail", "handrail", "railing", "chain",
                  "bollard_row", "kerb_barrier"}
_FIXTURE_AMENITY = {"post_box", "telephone", "bench", "waste_basket",
                    "vending_machine", "drinking_water", "bicycle_parking",
                    "shelter", "clock", "fountain"}
_FIXTURE_HIGHWAY_NODE = {"traffic_signals", "street_lamp", "bus_stop",
                         "elevator", "milestone", "stop"}
_FIXTURE_BARRIER_NODE = {"gate", "lift_gate", "swing_gate", "cycle_barrier",
                         "bollard", "block", "kissing_gate", "turnstile"}
_FIXTURE_RAILWAY_NODE = {"signal", "switch", "buffer_stop", "derail"}
_FIXTURE_MAN_MADE_NODE = {"flagpole", "street_cabinet"}
_CROSSING_RAILWAY_NODE = {"level_crossing", "crossing"}
_PLACE_AMENITY = {
    "bank", "bicycle_rental", "cafe", "clinic", "dentist", "doctors",
    "fast_food", "kindergarten", "library", "pharmacy", "place_of_worship",
    "post_office", "pub", "restaurant", "school",
}

# Site keys that promote a NAMED element to a place.  The class is always
# "place" and the why text names the tag that fired, so nothing in the bbox
# is silently dropped.  Unnamed elements carrying only these keys stay in
# `unmapped` with the tag, unless an earlier rule (fixture, road, terrain,
# sign) or the _PLACE_AMENITY allow-list above already claims them.
_NAMED_SITE_KEYS = ("amenity", "tourism", "leisure", "historic")

# Keys whose name identifies one specific site for triage group C.
_TRIAGE_C_NAME_KEYS = ("shop", "amenity", "tourism", "historic")


def _is_named(tags: dict) -> bool:
    """An OSM element is named when any name tag is present, in any script."""
    return bool(tags.get("name") or tags.get("name:en") or tags.get("name:ja"))


def _place_class(tags: dict) -> tuple[str, str] | None:
    """Retain a site identity without claiming that a physical sign exists."""
    if tags.get("public_transport") == "station" and tags.get("railway") == "station":
        return "place", "public_transport=station + railway=station semantic site"
    if tags.get("public_transport") == "stop_position" and tags.get("railway") == "stop":
        return "place", "public_transport=stop_position + railway=stop platform prior"
    for key in ("shop", "healthcare", "office", "craft"):
        if tags.get(key):
            return "place", f"{key}={tags[key]} semantic site"
    amenity = tags.get("amenity")
    if amenity in _PLACE_AMENITY:
        return "place", f"amenity={amenity} semantic site"
    # TRIAGE-01: the closed allow-list above stays only as the list of amenity
    # values that are sites even without a storefront (and usually without a
    # name).  A NAMED element carrying any site key is a site regardless.
    if _is_named(tags):
        for key in _NAMED_SITE_KEYS:
            if tags.get(key):
                return "place", f"{key}={tags[key]} named-site semantic site"
    return None


# FL-4811: canonical road/sidewalk/crossing/railway_boundary features carry
# the OSM cross-section evidence as a first-class property, so downstream
# width reasoning (world_surfaces.py) reads properties, not raw observations.
CROSS_SECTION_CLASSES = frozenset({
    "road", "sidewalk", "crossing", "railway_boundary",
})


def _int_or_null(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _float_or_null(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _oneway_or_null(raw: str | None) -> bool | None:
    if raw is None:
        return None
    text = raw.strip().lower()
    if text in ("yes", "1", "true", "-1"):
        return True
    if text in ("no", "0", "false"):
        return False
    return None


def _cross_section_value(tags: dict) -> dict:
    """Way tags packaged verbatim; absent tags are null, never defaulted.

    DEFAULTs belong to the consumer (world_surfaces.py), not to the ingest.
    """
    verbatim = tags.get
    return {
        "highway": verbatim("highway"),
        "lanes": _int_or_null(verbatim("lanes")),
        "width_m": _float_or_null(verbatim("width")),
        "oneway": _oneway_or_null(verbatim("oneway")),
        "sidewalk": verbatim("sidewalk"),
        "maxspeed": _int_or_null(verbatim("maxspeed")),
        "surface": verbatim("surface"),
        "lit": verbatim("lit"),
        "name": verbatim("name:en") or verbatim("name"),
    }

def _elevation_relation_value(tags: dict) -> dict | None:
    """Vertical-relation tags packaged verbatim; None when all absent.

    PLAN-02: the representation planner decides bridge-deck omission from
    this canonical claim, never by re-reading OSM tags. Absent tags are
    null, never defaulted.
    """
    verbatim = tags.get
    if verbatim("bridge") is None and verbatim("tunnel") is None \
            and verbatim("layer") is None:
        return None
    return {
        "bridge": verbatim("bridge"),
        "tunnel": verbatim("tunnel"),
        "layer": verbatim("layer"),
    }


def _way_class(tags: dict, closed: bool = False) -> tuple[str, str] | None:
    """Map one way's tags onto the registry.  None means no registry entry."""
    # FL-4876 BRIDGE-DECK-01: a closed man_made=bridge outline (here the rail
    # junction deck over the underpass) classifies as bridge_deck. An open
    # bridge way stays unmapped: a deck needs a footprint. The deck routes
    # with the ground-surface classes; layer=1 omits it from at-grade
    # stamping by stated rule while the corridor rails reconstruct above.
    # Deck sides/parapets remain unmodeled: recorded residual, not a silent
    # absence (the unmapped-physical complaint this replaces named exactly
    # this outline).
    if closed and tags.get("man_made") == "bridge":
        return "bridge_deck", "man_made=bridge closed deck outline"
    if tags.get("railway") in _RAILWAY_TRACK:
        return "railway_boundary", f"railway={tags['railway']} track centreline"
    if tags.get("landuse") == "railway":
        return "railway_boundary", "landuse=railway rail corridor land parcel"
    if tags.get("building") and tags["building"] != "no":
        return "building_footprint", f"building={tags['building']}"
    if tags.get("building:part") and tags["building:part"] != "no":
        return "building_footprint", f"building:part={tags['building:part']}"
    if tags.get("barrier") in _WALL_BARRIER:
        return "wall", f"barrier={tags['barrier']}"
    if tags.get("barrier") in _FENCE_BARRIER:
        return "fence", f"barrier={tags['barrier']}"
    if tags.get("barrier") == "hedge" or tags.get("natural") == "hedge":
        return "hedge", "barrier/natural=hedge"
    if tags.get("highway") in _FOOT_HIGHWAY and tags.get("footway") == "crossing":
        return "crossing", f"highway={tags['highway']} + footway=crossing"
    if tags.get("highway") in _FOOT_HIGHWAY:
        return "sidewalk", f"highway={tags['highway']} pedestrian way"
    if tags.get("highway") in _ROAD_HIGHWAY:
        return "road", f"highway={tags['highway']}"
    if tags.get("amenity") == "parking":
        return "road", "amenity=parking driveable surface"
    if tags.get("amenity") in _FIXTURE_AMENITY:
        return "fixture", f"amenity={tags['amenity']}"
    if tags.get("natural") == "tree_row":
        return "tree", "natural=tree_row"
    if tags.get("landuse") in _TERRAIN_LANDUSE:
        return "terrain", f"landuse={tags['landuse']}"
    if tags.get("natural") in _TERRAIN_NATURAL:
        return "terrain", f"natural={tags['natural']}"
    if tags.get("waterway") in _TERRAIN_WATERWAY:
        return "terrain", f"waterway={tags['waterway']}"
    if tags.get("leisure") in _TERRAIN_LEISURE:
        return "terrain", f"leisure={tags['leisure']}"
    if tags.get("power") == "line" or tags.get("power") == "minor_line":
        return "utility_wire", f"power={tags['power']}"
    return _place_class(tags)


def _node_class(tags: dict) -> tuple[str, str] | None:
    """Map one node's tags onto the registry.  None means no registry entry."""
    if tags.get("railway") == "train_station_entrance":
        ref = tags.get("ref")
        return "station_entrance", (
            f"railway=train_station_entrance" + (f" ref={ref}" if ref else "")
        )
    if tags.get("entrance"):
        return "door", f"entrance={tags['entrance']}"
    if tags.get("highway") == "crossing":
        return "crossing", f"highway=crossing ({tags.get('crossing', 'unspecified')})"
    if tags.get("railway") in _CROSSING_RAILWAY_NODE:
        return "crossing", f"railway={tags['railway']}"
    if tags.get("highway") in _FIXTURE_HIGHWAY_NODE:
        return "fixture", f"highway={tags['highway']}"
    if tags.get("amenity") in _FIXTURE_AMENITY:
        return "fixture", f"amenity={tags['amenity']}"
    if tags.get("natural") == "tree":
        return "tree", "natural=tree"
    if tags.get("barrier") in _FIXTURE_BARRIER_NODE:
        return "fixture", f"barrier={tags['barrier']}"
    if tags.get("railway") in _FIXTURE_RAILWAY_NODE:
        return "fixture", f"railway={tags['railway']}"
    if tags.get("man_made") in _FIXTURE_MAN_MADE_NODE:
        return "fixture", f"man_made={tags['man_made']}"
    if tags.get("emergency") == "fire_hydrant":
        return "fixture", "emergency=fire_hydrant"
    if tags.get("power") in {"pole", "tower"}:
        return "utility_pole", f"power={tags['power']}"
    if tags.get("tourism") == "information" and tags.get("information") in {
        "map", "board", "guidepost", "route_marker"
    }:
        return "sign", f"tourism=information, information={tags['information']}"
    if tags.get("advertising") or tags.get("traffic_sign"):
        return "sign", "advertising/traffic_sign"
    if tags.get("historic") == "memorial":
        return "place", "historic=memorial semantic site"
    if tags.get("place") in {"neighbourhood", "quarter", "suburb", "locality"}:
        return "place", f"place={tags['place']} named locality"
    if tags.get("barrier") in _WALL_BARRIER:
        return "wall", f"barrier={tags['barrier']}"
    if tags.get("barrier") in _FENCE_BARRIER:
        return "fence", f"barrier={tags['barrier']}"
    return _place_class(tags)


# Why a tagged element that reaches no rule has no registry entry.  Reported,
# never silently dropped: an unmapped feature is a decision someone has to make,
# not a gap to paper over by picking the nearest-looking class.
UNMAPPED_REASONS: tuple[tuple[frozenset[str], str], ...] = (
    (frozenset({"public_transport", "railway"}),
     "transit topology outside the retained station/stop-position vocabulary; "
     "do not convert an unknown service concept into physical infrastructure"),
    (frozenset({"shop", "amenity", "healthcare", "office", "craft", "tourism"}),
     "site tag outside the retained place/fixture vocabulary; keep it reported "
     "rather than converting an unrecognized tenancy into a physical sign"),
    (frozenset({"boundary", "admin_level"}),
     "administrative boundary: an legal line, not a physical world feature"),
    (frozenset({"man_made"}),
     "man_made structure outline with no registry class (e.g. bridge deck): "
     "belongs to a structure vocabulary the registry does not yet carry"),
    (frozenset({"embankment", "cutting"}),
     "terrain-modifier annotation on a way that carries no primary feature key"),
    (frozenset({"junction"}),
     "junction=yes topology annotation on a shared node"),
)


def unmapped_reason(tags: dict) -> str:
    keys = set(tags)
    for probe, why in UNMAPPED_REASONS:
        if probe & keys:
            return why
    return "no rule in this ingest owner matches these tags"


# --------------------------------------------------------------------------
# triage authoring groups (TRIAGE-01; FL-4809 fix-attempt points 1 and 3)
# --------------------------------------------------------------------------
#
# Every emitted feature carries a `triage_group` claim so the bbox becomes a
# work queue instead of a silent fallback:
#   C = site-specific identity (brand/operator, or a name on a place key).
#       The pack chain owes a front/facade capture keyed to that identity.
#   B = the class is one the pack chain declares supported (common_global
#       pack support_tier generic_source_derived; tokyo place_front_profiles;
#       tokyo building_fallback_rules) but no template, facade profile,
#       place-front profile or fixture archetype resolves for it.
#   A = generic: a template, archetype, fallback rule or lowerer already
#       covers it, so nothing is owed.
# B and C features additionally carry `debt_owed` naming the owed work.
# The C identity rule is scoped to place features: a brand/operator tag on a
# fixture (e.g. operator=Japan Post on a post_box) names the network, not the
# site, and the fixture archetype already resolves, so those stay A.

# Read-only resolution mirror of the archetype table owned by
# compile_world_ir_fixtures.py:FIXTURE_SUBTYPES (REPRESENT-01 owns that file;
# triage never imports it so a concurrent edit there cannot break ingest).
# Only the (tag key, tag value) pairs are mirrored: the subtype names stay
# with their owner.
_FIXTURE_ARCHETYPE_KEYS = frozenset({
    ("highway", "traffic_signals"), ("amenity", "post_box"),
    ("amenity", "telephone"), ("amenity", "bicycle_parking"),
    ("amenity", "bench"), ("amenity", "vending_machine"),
    ("amenity", "waste_basket"), ("amenity", "drinking_water"),
    ("amenity", "shelter"), ("amenity", "clock"), ("amenity", "fountain"),
    ("highway", "street_lamp"), ("highway", "bus_stop"),
    ("highway", "stop"), ("power", "pole"), ("barrier", "gate"),
    ("barrier", "lift_gate"), ("barrier", "swing_gate"),
    ("barrier", "cycle_barrier"), ("barrier", "bollard"),
    ("barrier", "block"), ("barrier", "kissing_gate"),
    ("barrier", "turnstile"), ("railway", "signal"), ("railway", "switch"),
    ("railway", "buffer_stop"), ("railway", "derail"),
    ("man_made", "flagpole"), ("man_made", "street_cabinet"),
    ("emergency", "fire_hydrant"), ("natural", "tree"),
    # FL-4876. Surveyed information board; its template is authored.
    ("tourism", "information"),
})

# Commercial front families, after the geometry_family values of the tokyo
# pack place_front_profiles (the `cf.` profile is the one the lowering
# already resolves for that category; the debt is capturing the real
# facade/front to replace the synthesized prior).
_COMMERCIAL_FRONT_FAMILY = {
    "shop=convenience": ("convenience_store_front", "tokyo.familymart_front"),
    "shop=supermarket": ("neighborhood_supermarket_front", "tokyo.mybasket_front"),
    "shop=chemist": ("pharmacy_front", "tokyo.pharmacy_front"),
    "amenity=pharmacy": ("pharmacy_front", "tokyo.pharmacy_front"),
    "amenity=place_of_worship": ("buddhist_place_entry_gate", "jp.urban.buddhist_place_entry_gate"),
}
_DEFAULT_FRONT_FAMILY = ("small_commercial_front", "tokyo.generic_small_commercial_front")

_PLACE_LOCALITY_VALUES = {"neighbourhood", "quarter", "suburb", "locality"}

_TRIAGE_A_WHY = {
    "building_footprint": "generic: tokyo building_fallback_rules and facade_profiles "
                           "cover it; no site-specific authoring owed",
    "road": "generic: linear-centreline lowering covers it (presentation-only); "
             "no site-specific authoring owed",
    "sidewalk": "generic: linear-centreline lowering covers it (presentation-only); "
                 "no site-specific authoring owed",
    "crossing": "generic: linear-centreline lowering covers it (presentation-only); "
                 "no site-specific authoring owed",
    "railway_boundary": "generic: rail-centreline lowering covers it; "
                         "no site-specific authoring owed",
    "terrain": "generic: terrain-surface lowering covers it; no site-specific authoring owed",
    "sign": "generic: the common_global sign template covers it; "
             "no site-specific authoring owed",
    "station_entrance": "generic: entrance-anchor lowering covers it; "
                         "no site-specific authoring owed",
}


def _fixture_archetype_key(tags: dict) -> tuple[str, str] | None:
    """The (tag key, tag value) pair the fixture archetype table resolves."""
    matches = sorted(
        pair for pair in _FIXTURE_ARCHETYPE_KEYS if tags.get(pair[0]) == pair[1]
    )
    return matches[0] if matches else None


def _place_use_class(tags: dict) -> str | None:
    """Facade use class from the retained site tag (claim, not a tag read
    at plan time). Retail, civic-counter and craft tenancies present a
    commercial front; visitor attractions read as mixed streetscape;
    memorial and worship sites are institutional (their markers and gates
    resolve before any shopfront module is considered)."""
    if tags.get("shop") or tags.get("healthcare") or tags.get("office") \
            or tags.get("craft"):
        return "commercial"
    if tags.get("amenity") == "place_of_worship":
        return "institutional"
    if tags.get("amenity") is not None:
        return "commercial"
    if tags.get("tourism") is not None or tags.get("leisure") is not None:
        return "mixed"
    if tags.get("historic") is not None:
        return "institutional"
    if tags.get("public_transport") is not None:
        return "commercial"
    return None


def _place_site_value(tags: dict) -> str:
    for key in ("shop", "healthcare", "office", "craft", "amenity",
                "tourism", "leisure", "historic", "public_transport"):
        if tags.get(key):
            return f"{key}={tags[key]}"
    return "no site key"


def _place_debt(tags: dict, identity: str | None) -> str:
    """What the pack chain owes one B or C place feature."""
    site_val = _place_site_value(tags)
    owed_identity = (
        f"identity fixed by {identity}" if identity is not None
        else "site-identity capture still owed (no brand/operator/tenancy name)"
    )
    if tags.get("tourism") == "attraction":
        return ("place-identity landmark profile for tourism=attraction "
                "(template family route_landmark; no storefront front owed: a named "
                f"slope, not a tenancy; no facade capture owed: no building face; {owed_identity})")
    if tags.get("historic") == "memorial":
        return ("landmark marker profile for historic=memorial "
                "(template family landmark_marker; no commercial front owed: minimap "
                f"landmark, not a storefront; no facade capture owed: no building face; {owed_identity})")
    site_key = site_val.split("=", 1)[0]
    if site_key in ("public_transport", "no site key"):
        return ("template family station_stop_place_front (no match in tokyo/common_global "
                f"place_front_profiles); facade capture of the station/stop face; "
                f"place-front profile for {site_val}; {owed_identity}")
    family, hint = _COMMERCIAL_FRONT_FAMILY.get(site_val, _DEFAULT_FRONT_FAMILY)
    return (f"template family {family} (cf. {hint}); facade capture of the host "
            f"building face; place-front profile for {site_val}; {owed_identity}")


def _triage_group(semantic_class: str, tags: dict) -> tuple[str, str, str | None]:
    """Authoring group, why text, and debt owed (None for group A)."""
    if semantic_class == "place":
        brand, operator = tags.get("brand"), tags.get("operator")
        if brand:
            identity: str | None = f"brand={brand}"
        elif operator:
            identity = f"operator={operator}"
        elif _is_named(tags):
            identity = next(
                (f"{key}={tags[key]}" for key in _TRIAGE_C_NAME_KEYS if tags.get(key)),
                None,
            )
        else:
            identity = None
        if identity is not None:
            return ("C", f"{identity} identifies one specific site",
                    _place_debt(tags, identity))
        if tags.get("place") in _PLACE_LOCALITY_VALUES:
            return ("A", "named locality: the minimap-landmark lowerer covers it; "
                         "no storefront front is owed", None)
        site_val = _place_site_value(tags)
        return ("B", f"supported place class with no resolving front profile ({site_val})",
                _place_debt(tags, None))
    if semantic_class == "fixture":
        pair = _fixture_archetype_key(tags)
        if pair is not None:
            return ("A", f"fixture archetype resolves for {pair[0]}={pair[1]}; "
                         "generic lowering covers it", None)
        gap = next(
            (f"{key}={tags[key]}" for key in (
                "amenity", "highway", "barrier", "railway", "man_made",
                "emergency", "power", "natural",
            ) if tags.get(key)),
            f"keys {sorted(tags)}",
        )
        return ("B", f"no fixture archetype resolves for {gap}",
                f"fixture archetype for {gap} (no entry in the fixture archetype "
                "table); placement + orientation capture for the new subtype")
    return ("A", _TRIAGE_A_WHY.get(
        semantic_class,
        f"generic: the pack chain already resolves {semantic_class}; "
        "no site-specific authoring owed",
    ), None)


def _rule_gap(tags: dict) -> str:
    """The exact rule gap behind one unmapped element."""
    site = sorted(key for key in _NAMED_SITE_KEYS if key in tags)
    if "junction" in tags and not site:
        return ("junction=yes is topology annotation on a shared node; no site key, "
                "so _place_class does not fire")
    if tags.get("man_made"):
        return (f"man_made={tags['man_made']} has no registry class: the structure "
                "vocabulary the registry does not yet carry")
    if "boundary" in tags:
        return ("boundary=administrative is a legal line, not a physical world "
                "feature; no registry class")
    if tags.get("landuse"):
        return (f"landuse={tags['landuse']} is outside the retained terrain "
                "vocabulary and no site key is present")
    if site:
        return (f"{'/'.join(site)} present but the element is unnamed and outside "
                "the fixture/road/terrain precedence rules, so no place rule fires")
    return f"no rule matches keys {sorted(tags)}"


# --------------------------------------------------------------------------
# authority
# --------------------------------------------------------------------------
#
# Authority is decided by (class, geometric dimension), never by class alone.
# A centreline has no width, so it cannot decide where a player may stand.

_AREA_AUTHORITY = {
    "building_footprint": S.AUTHORITY_COLLISION,
    "road": S.AUTHORITY_WALKABILITY,
    "sidewalk": S.AUTHORITY_WALKABILITY,
    "crossing": S.AUTHORITY_WALKABILITY,
    "terrain": S.AUTHORITY_WALKABILITY,
    "railway_boundary": S.AUTHORITY_PRESENTATION_ONLY,
    "wall": S.AUTHORITY_COLLISION,
    "fence": S.AUTHORITY_COLLISION,
}

AUTHORITY_NOTE_LINEAR = (
    "linear OSM geometry is a centreline with no recorded width, so it cannot "
    "decide where a player may stand; walkable extent must be solved downstream "
    "from evidence that measures width"
)
AUTHORITY_NOTE_POINT = (
    "a point has no extent; a physical authority would have to invent one"
)


def authority_for(semantic_class: str, geometry_kind: str) -> tuple[str, str]:
    if geometry_kind == "polygon":
        authority = _AREA_AUTHORITY.get(semantic_class, S.AUTHORITY_PRESENTATION_ONLY)
        if authority == S.AUTHORITY_COLLISION:
            return authority, (
                "closed measured ring bounds a volume that blocks movement"
            )
        if authority == S.AUTHORITY_WALKABILITY:
            return authority, "closed measured ring bounds a walkable surface"
        return authority, "closed ring, but the class does not bound a physical surface"
    if geometry_kind == "polyline":
        return S.AUTHORITY_PRESENTATION_ONLY, AUTHORITY_NOTE_LINEAR
    return S.AUTHORITY_PRESENTATION_ONLY, AUTHORITY_NOTE_POINT


_SILHOUETTE_CLASSES = {"building_footprint", "tree", "wall", "fence",
                       "utility_pole", "sign", "station_entrance"}
_LANDMARK_CLASSES = {"building_footprint", "station_entrance"}
_NAVIGATIONAL_CLASSES = {"crossing", "sign", "station_entrance", "place"}


def presentation_for(semantic_class: str, authority: str) -> dict:
    if semantic_class in _NAVIGATIONAL_CLASSES:
        legibility = "navigational_cue"
    elif semantic_class in _LANDMARK_CLASSES:
        legibility = "landmark"
    elif semantic_class in {"terrain", "railway_boundary"}:
        legibility = "background"
    else:
        legibility = "secondary"
    return S.presentation_requirements(
        visible=True,
        silhouette_required=semantic_class in _SILHOUETTE_CLASSES,
        # A feature that owns no physical authority must not be DRAWN as if it
        # did: this is the presentation half of the same rule.
        must_not_imply_collision=authority not in S.PHYSICAL_AUTHORITIES,
        legibility_class=legibility,
    )


# ==========================================================================
# ingest
# ==========================================================================


def _bundle_source_record(bundle_dir: Path) -> dict:
    manifest = json.loads((bundle_dir / "source_manifest.json").read_text(encoding="utf-8"))
    for rec in manifest["records"]:
        if rec["record_id"] in ("osm_overpass_aoi_extract",
                                "connected_neighborhood_osm_extract"):
            return rec
    raise SystemExit(
        f"[osm-ingest] {bundle_dir}/source_manifest.json has no "
        "'osm_overpass_aoi_extract' record; refusing to ingest unprovenanced bytes"
    )


def _bundle_aoi_path(bundle_dir: Path) -> Path:
    """Resolve the AOI artifact from the source manifest.

    FL-4810 originally hard-coded the first 150 m bundle's filename.  The AOI
    is a source-manifest product, so expanded route bundles must be able to
    supply their own stable artifact name without masquerading as the old
    corridor.
    """
    manifest = json.loads((bundle_dir / "source_manifest.json").read_text(encoding="utf-8"))
    for rec in manifest["records"]:
        if rec["record_id"] in ("aoi_definition_geojson", "connected_scope_geojson") and rec.get("artifact_path"):
            return bundle_dir / rec["artifact_path"]
    raise SystemExit(
        f"[osm-ingest] {bundle_dir}/source_manifest.json has no retained "
        "'aoi_definition_geojson' artifact"
    )


def _aoi_ring(bundle_dir: Path) -> tuple[list[list[float]], dict]:
    geojson = json.loads(_bundle_aoi_path(bundle_dir).read_text(encoding="utf-8"))
    for feat in geojson["features"]:
        if feat.get("id") == "aoi_polygon" or feat.get("properties", {}).get("role") == "aoi":
            return feat["geometry"]["coordinates"][0], geojson
    raise SystemExit("[osm-ingest] the AOI geojson has no 'aoi_polygon' feature")


def _entrance_lonlat(geojson: dict) -> tuple[float, float, str]:
    for feat in geojson["features"]:
        if (feat.get("id") == "selected_entrance" or
                feat.get("properties", {}).get("role") == "selected_station_entrance"):
            lon, lat = feat["geometry"]["coordinates"]
            props = feat["properties"]
            return lon, lat, f"{props['osm_type']}/{props['osm_id']}"
    for feat in geojson["features"]:
        props = feat.get("properties", {})
        if props.get("landmark_id") == "numabe_station_selected_entrance":
            lon, lat = feat["geometry"]["coordinates"]
            return lon, lat, f"{props['osm_type']}/{props['osm_id']}"
    raise SystemExit("[osm-ingest] the AOI geojson has no 'selected_entrance' feature")


def _turn_angle_deg(prev, here, nxt) -> float:
    """Deviation from straight, in degrees: 0 is a straight wall, 90 a corner."""
    v1 = (here[0] - prev[0], here[1] - prev[1])
    v2 = (nxt[0] - here[0], nxt[1] - here[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    cosang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cosang))


class OSMIngest:
    """One frozen extract -> one World IR document plus one anchor set."""

    def __init__(self, bundle_dir: Path, *, ir_id: str, generated_at: str) -> None:
        self.bundle_dir = Path(bundle_dir)
        self.source_record = _bundle_source_record(self.bundle_dir)
        self.ring, self.geojson = _aoi_ring(self.bundle_dir)
        lon, lat, entrance_ref = _entrance_lonlat(self.geojson)
        self.frame = GF.Y9WorldFrame.anchored_at_lonlat(
            lon, lat, f"OSM {entrance_ref} (selected station entrance)"
        )
        self.entrance_ref = entrance_ref
        self.observed_at = self.source_record.get("capture_date") or "unknown"
        self.extract = parse_osm(self.bundle_dir / self.source_record["artifact_path"])
        self.store = WorldIRStore.new(ir_id, generated_at=generated_at)
        GF.declare_frames(self.store, self.frame)

        self.kept_ways: dict[str, str] = {}       # way id -> feature id
        self.kept_nodes: dict[str, str] = {}      # node id -> feature id
        self.observation_of: dict[str, str] = {}  # osm ref -> observation id
        self.class_of: dict[str, str] = {}        # osm ref -> semantic class
        self.by_class: Counter = Counter()
        self.by_authority: Counter = Counter()
        self.unmapped: list[dict] = []
        self.triage_rows: list[dict] = []
        self.clip: dict[str, Any] = {}

    # -- source attribution ------------------------------------------------

    def provenance_block(self) -> dict:
        rec = self.source_record
        return {
            "source_id": rec["record_id"],
            "provider": rec["provider"],
            "source_uri": rec["source_uri"],
            "artifact_path": rec["artifact_path"],
            "sha256": rec["sha256"],
            "size_bytes": rec["size_bytes"],
            "licence": rec["licence"],
            "licence_uri": rec["licence_uri"],
            "attribution": rec["attribution"],
            "capture_date": rec["capture_date"],
            "acquired_utc": rec["acquired_utc"],
            "crs": rec["crs"],
        }

    # -- clip --------------------------------------------------------------

    def clip_to_aoi(self) -> tuple[list[str], list[str]]:
        ways_in: list[str] = []
        ways_out = 0
        ways_no_geom = 0
        centroid_in = 0
        for way_id in self.extract.ways:
            pts = self.extract.way_points(way_id)
            if not pts:
                ways_no_geom += 1
                continue
            if polyline_touches_ring(pts, self.ring):
                ways_in.append(way_id)
                if point_in_ring(ring_centroid(pts), self.ring):
                    centroid_in += 1
            else:
                ways_out += 1

        nodes_in = [n for n in self.extract.node_tags if point_in_ring(self.extract.nodes[n], self.ring)]
        tagged_nodes_out = len(self.extract.node_tags) - len(nodes_in)
        untagged_nodes = len(self.extract.nodes) - len(self.extract.node_tags)

        self.clip = {
            "predicate": "a way is in the AOI when any vertex is inside the AOI ring "
                         "or any edge crosses it; a node is in when it is inside the ring",
            "aoi_ring_vertices": len(self.ring),
            "ways_kept": len(ways_in),
            "ways_clipped_away": ways_out,
            "ways_with_no_resolvable_geometry": ways_no_geom,
            "ways_kept_with_centroid_inside_aoi": centroid_in,
            "tagged_nodes_kept": len(nodes_in),
            "tagged_nodes_clipped_away": tagged_nodes_out,
            "untagged_geometry_carrier_nodes": untagged_nodes,
            "clipped_away_fraction_of_ways": round(
                ways_out / max(1, len(self.extract.ways)), 6
            ),
            "relations_read": self.extract.stats["unique_relations"],
            "relations_contributing_area_features": self.extract.stats["multipolygon_relations"],
            "relations_note": "the extract carries no type=multipolygon relation, so no AOI "
                              "area feature is relation-built and no building hole is lost",
        }
        return ways_in, nodes_in

    # -- records -----------------------------------------------------------

    def _observation(self, osm_type: str, osm_id: str, tags: dict, geometry_4326: dict,
                     extra: dict) -> str:
        ref = f"{osm_type}/{osm_id}"
        obs_id = f"obs:osm:{ref}"
        attributes = {
            "source_ref": ref,
            "osm_type": osm_type,
            "osm_id": osm_id,
            "tags": dict(sorted(tags.items())),
            "osm_base": self.source_record.get("capture_date"),
            "attribution": self.source_record["attribution"],
            "licence": self.source_record["licence"],
        }
        attributes.update(extra)
        self.store.add_observation(
            S.source_observation(
                observation_id=obs_id,
                source_id=self.source_record["record_id"],
                source_kind="vector_map",
                observed_at=self.observed_at,
                attributes=attributes,
                geometry=geometry_4326,
                quality={
                    "position_sigma_m": OSM_POSITION_SIGMA_M,
                    "position_sigma_basis": "OSM digitising tolerance, carried over from "
                                            "world_ir_ingest_carto.OSM_POSITION_SIGMA "
                                            f"({OSM_POSITION_SIGMA_WORLD_UNITS} world units) "
                                            f"/ historical {OSM_POSITION_SIGMA_REFERENCE_WORLD_UNITS_PER_METRE} world units per metre",
                },
                digest=self.source_record["sha256"],
            )
        )
        self.observation_of[ref] = obs_id
        return obs_id

    def _feature(self, ref: str, semantic_class: str, why: str, geometry: dict,
                 obs_id: str, tags: dict) -> dict:
        authority, authority_why = authority_for(semantic_class, geometry["kind"])
        uncertainty = S.uncertainty_isotropic(OSM_POSITION_SIGMA_M, "metre")
        properties = {
            "geometry": S.property_claim(
                value=None,
                provenance=S.PROVENANCE_MEASURED,
                method="OSM digitised coordinates, projected EPSG:4326 -> EPSG:32654 by "
                       "scripts/pipeline/geo_frames.py (Kruger n-series, 6th order); the "
                       "projection re-expresses the measured position, it does not derive "
                       "a new one",
                observation_ids=[obs_id],
                uncertainty=uncertainty,
            ),
            "semantic_class": S.property_claim(
                value=semantic_class,
                provenance=S.PROVENANCE_DERIVED,
                method=f"OSM tag rule: {why}",
                observation_ids=[obs_id],
                uncertainty=S.uncertainty_categorical({semantic_class: 1.0}),
            ),
            "gameplay_authority_basis": S.property_claim(
                value=authority,
                provenance=S.PROVENANCE_DERIVED,
                method=authority_why,
                observation_ids=[obs_id],
            ),
        }
        name = tags.get("name:en") or tags.get("name")
        if name:
            properties["name"] = S.property_claim(
                value=name,
                provenance=S.PROVENANCE_MEASURED,
                method="OSM name tag, verbatim",
                observation_ids=[obs_id],
            )
        if semantic_class in CROSS_SECTION_CLASSES:
            properties["cross_section"] = S.property_claim(
                value=_cross_section_value(tags),
                provenance=S.PROVENANCE_MEASURED,
                method="OSM cross-section tags, verbatim; absent tags are null, "
                       "defaults belong to the consumer",
                observation_ids=[obs_id],
            )
        elevation = _elevation_relation_value(tags)
        if elevation is not None:
            properties["elevation_relation"] = S.property_claim(
                value=elevation,
                provenance=S.PROVENANCE_MEASURED,
                method="OSM bridge/tunnel/layer tags, verbatim; absent tags "
                       "are null, defaults belong to the consumer",
                observation_ids=[obs_id],
            )
        archetype_pair = _fixture_archetype_key(tags)
        if archetype_pair is not None:
            archetype_value = "%s=%s" % archetype_pair
            properties["fixture_archetype"] = S.property_claim(
                value=archetype_value,
                provenance=S.PROVENANCE_DERIVED,
                method="normalized from the retained fixture tag via "
                       "the fixture archetype table",
                observation_ids=[obs_id],
                uncertainty=S.uncertainty_categorical(
                    {archetype_value: 1.0}),
            )
        if semantic_class == "place":
            category_key = next(
                (key for key in (
                    "shop", "healthcare", "office", "craft", "amenity",
                    "tourism", "leisure", "historic",
                    "public_transport",
                )
                 if tags.get(key)),
                None,
            )
            if category_key is not None:
                properties["place_category"] = S.property_claim(
                    value=f"{category_key}={tags[category_key]}",
                    provenance=S.PROVENANCE_DERIVED,
                    method="normalized from the retained OSM site tag",
                    observation_ids=[obs_id],
                    uncertainty=S.uncertainty_categorical(
                        {f"{category_key}={tags[category_key]}": 1.0}
                    ),
                )
            for key in ("brand", "operator", "branch"):
                if tags.get(key):
                    properties[key] = S.property_claim(
                        value=tags[key],
                        provenance=S.PROVENANCE_MEASURED,
                        method=f"OSM {key} tag, verbatim",
                        observation_ids=[obs_id],
                    )
            use_class = _place_use_class(tags)
            if use_class is not None:
                properties["use_class"] = S.property_claim(
                    value=use_class,
                    provenance=S.PROVENANCE_DERIVED,
                    method="normalized use class from the retained OSM "
                           "site tag (facade archetype selection basis)",
                    observation_ids=[obs_id],
                    uncertainty=S.uncertainty_categorical(
                        {use_class: 1.0}),
                )
            if tags.get("religion"):
                properties["religion"] = S.property_claim(
                    value=tags["religion"],
                    provenance=S.PROVENANCE_MEASURED,
                    method="OSM religion tag, verbatim",
                    observation_ids=[obs_id],
                )
        # TRIAGE-01: every feature carries its authoring group; B and C
        # features additionally carry the work they are owed.  Both claims use
        # the same claim shape as semantic_class, so the schema needs no widening.
        group, group_why, debt = _triage_group(semantic_class, tags)
        properties["triage_group"] = S.property_claim(
            value=group,
            provenance=S.PROVENANCE_DERIVED,
            method=f"triage authoring group: {group_why}",
            observation_ids=[obs_id],
            uncertainty=S.uncertainty_categorical({group: 1.0}),
        )
        if debt is not None:
            properties["debt_owed"] = S.property_claim(
                value=debt,
                provenance=S.PROVENANCE_DERIVED,
                method="TRIAGE-01 work queue: what the pack chain still owes this feature",
                observation_ids=[obs_id],
            )
        self.triage_rows.append({
            "feature_id": f"feature:{semantic_class}:{ref}",
            "class": semantic_class,
            "label": tags.get("name:en") or tags.get("name"),
            "group": group,
            "debt_owed": debt,
        })
        labels = {k: v for k, v in tags.items() if k in ("name", "name:en", "ref")}
        feature = self.store.add_feature(
            S.canonical_feature(
                feature_id=f"feature:{semantic_class}:{ref}",
                semantic_class=semantic_class,
                geometry=geometry,
                properties=properties,
                confidence=0.9,
                gameplay_authority=authority,
                presentation=presentation_for(semantic_class, authority),
                # Physical authority must name the claims that earn it.  Both
                # named claims here are measured/derived from the extract, never
                # synthesized.
                authority_supporting_properties=["geometry", "semantic_class"],
                labels=labels or None,
            )
        )
        self.by_class[semantic_class] += 1
        self.by_authority[feature["gameplay_authority"]] += 1
        self.class_of[ref] = semantic_class
        return feature

    def ingest_ways(self, way_ids: Iterable[str]) -> None:
        for way_id in way_ids:
            tags = self.extract.way_tags.get(way_id) or {}
            ref = f"way/{way_id}"
            if not tags:
                continue
            mapped = _way_class(tags, closed=self.extract.is_closed(way_id))
            pts = self.extract.way_points(way_id)
            if mapped is None:
                self.unmapped.append({
                    "source_ref": ref,
                    "element": "way",
                    "tags": dict(sorted(tags.items())),
                    "reason": unmapped_reason(tags),
                })
                continue
            semantic_class, why = mapped
            closed = self.extract.is_closed(way_id)
            projected = [list(GF.utm_forward(lon, lat)) for lon, lat in pts]
            if closed and len(projected) >= 4:
                geometry = S.geometry_polygon(GF.FRAME_PROJECTED, projected)
            elif len(projected) >= 2:
                geometry = S.geometry_polyline(GF.FRAME_PROJECTED, projected)
            elif projected:
                geometry = S.geometry_point(GF.FRAME_PROJECTED, projected[0])
            else:
                self.unmapped.append({
                    "source_ref": ref, "element": "way",
                    "tags": dict(sorted(tags.items())),
                    "reason": "no member node of this way is present in the extract",
                })
                continue
            obs_id = self._observation(
                "way", way_id, tags,
                S.geometry_polygon(GF.FRAME_GEOGRAPHIC, [list(p) for p in pts])
                if closed and len(pts) >= 4
                else S.geometry_polyline(GF.FRAME_GEOGRAPHIC, [list(p) for p in pts]),
                {
                    "node_refs": list(self.extract.ways[way_id]),
                    "is_closed_way": closed,
                    "vertex_count": len(pts),
                },
            )
            feature = self._feature(ref, semantic_class, why, geometry, obs_id, tags)
            self.kept_ways[way_id] = feature["feature_id"]

    def ingest_nodes(self, node_ids: Iterable[str]) -> None:
        for node_id in node_ids:
            tags = self.extract.node_tags[node_id]
            ref = f"node/{node_id}"
            mapped = _node_class(tags)
            if mapped is None:
                self.unmapped.append({
                    "source_ref": ref,
                    "element": "node",
                    "tags": dict(sorted(tags.items())),
                    "reason": unmapped_reason(tags),
                })
                continue
            semantic_class, why = mapped
            lon, lat = self.extract.nodes[node_id]
            obs_id = self._observation(
                "node", node_id, tags,
                S.geometry_point(GF.FRAME_GEOGRAPHIC, [lon, lat]),
                {},
            )
            geometry = S.geometry_point(GF.FRAME_PROJECTED, list(GF.utm_forward(lon, lat)))
            feature = self._feature(ref, semantic_class, why, geometry, obs_id, tags)
            self.kept_nodes[node_id] = feature["feature_id"]

    # -- anchor set --------------------------------------------------------

    def _anchor(self, node_id: str, anchor_type: str, reason: str,
                owner_refs: Sequence[str]) -> dict:
        lon, lat = self.extract.nodes[node_id]
        easting, northing = GF.utm_forward(lon, lat)
        wx, wy = self.frame.utm_to_world(easting, northing)
        owners = [r for r in owner_refs if r in self.observation_of]
        return {
            "anchor_id": f"anchor:{anchor_type}:node/{node_id}",
            "anchor_type": anchor_type,
            "osm_ref": f"node/{node_id}",
            "epsg4326": [lon, lat],
            "epsg32654": [easting, northing],
            "y9_world": [wx, wy],
            # A kept feature can reach past the AOI ring - a building is kept
            # when ANY vertex is inside, so its far corners are outside.  Those
            # corners are still usable anchors (the building is visible from the
            # corridor), but a registration solver has to be able to tell, so the
            # flag is recorded per anchor instead of the anchor being dropped.
            "inside_aoi": point_in_ring((lon, lat), self.ring),
            "uncertainty": S.uncertainty_isotropic(OSM_POSITION_SIGMA_M, "metre"),
            "owner_feature_ids": sorted({
                fid for ref in owners
                for fid in [f"feature:{self.class_of[ref]}:{ref}"]
            }),
            "owner_observation_ids": sorted({self.observation_of[r] for r in owners}),
            "reason": reason,
        }

    def build_anchor_set(self, generated_at: str) -> dict:
        anchors: dict[str, dict] = {}

        def put(anchor: dict) -> None:
            # A node can qualify twice (a crossing node on a road intersection).
            # First type wins, and the second reason is appended rather than lost.
            existing = anchors.get(anchor["anchor_id"])
            if existing is None:
                anchors[anchor["anchor_id"]] = anchor
            else:
                existing["reason"] += " | " + anchor["reason"]

        # 1. building corners: ring vertices where two wall planes actually meet
        for way_id, _fid in self.kept_ways.items():
            if self.class_of.get(f"way/{way_id}") != "building_footprint":
                continue
            refs = self.extract.ways[way_id]
            if not self.extract.is_closed(way_id):
                continue
            loop = refs[:-1]
            pts = [self.extract.nodes[r] for r in loop if r in self.extract.nodes]
            if len(pts) != len(loop) or len(loop) < 3:
                continue
            proj = [GF.utm_forward(lon, lat) for lon, lat in pts]
            for i, node_id in enumerate(loop):
                turn = _turn_angle_deg(proj[i - 1], proj[i], proj[(i + 1) % len(proj)])
                if turn < CORNER_MIN_TURN_DEG:
                    continue
                put(self._anchor(
                    node_id, "building_corner",
                    f"corner of building way/{way_id}: the footprint turns "
                    f"{turn:.1f} deg here, so two wall planes meet at a vertical "
                    "edge that a facade observation can register against",
                    [f"way/{way_id}"],
                ))

        # 2. road intersections: a node shared by two or more kept carriageways
        carriage = {"road", "sidewalk", "crossing"}
        node_ways: dict[str, list[str]] = {}
        for way_id in self.kept_ways:
            if self.class_of.get(f"way/{way_id}") not in carriage:
                continue
            for node_id in self.extract.ways[way_id]:
                node_ways.setdefault(node_id, []).append(way_id)
        for node_id, owners in node_ways.items():
            if len(set(owners)) < 2 or node_id not in self.extract.nodes:
                continue
            if not point_in_ring(self.extract.nodes[node_id], self.ring):
                continue
            put(self._anchor(
                node_id, "road_intersection",
                "shared junction node of kept ways " + ", ".join(
                    f"way/{w}" for w in sorted(set(owners))
                ) + "; road centrelines meet here, so the junction is visible from "
                "any direction of approach",
                [f"way/{w}" for w in sorted(set(owners))],
            ))

        # 3. wall / fence endpoints
        for way_id in self.kept_ways:
            if self.class_of.get(f"way/{way_id}") not in {"wall", "fence"}:
                continue
            refs = self.extract.ways[way_id]
            for node_id in {refs[0], refs[-1]}:
                if node_id in self.extract.nodes:
                    put(self._anchor(
                        node_id, "wall_fence_endpoint",
                        f"free end of barrier way/{way_id}: a wall or fence terminates "
                        "in a vertical edge with a sharp silhouette",
                        [f"way/{way_id}"],
                    ))

        # 4. railway geometry inside the AOI
        for way_id in self.kept_ways:
            if self.class_of.get(f"way/{way_id}") != "railway_boundary":
                continue
            for node_id in self.extract.ways[way_id]:
                if node_id not in self.extract.nodes:
                    continue
                if not point_in_ring(self.extract.nodes[node_id], self.ring):
                    continue
                put(self._anchor(
                    node_id, "railway_vertex",
                    f"vertex of rail centreline way/{way_id} inside the AOI: track "
                    "geometry is surveyed rather than sketched, so it is the most "
                    "rigid linear reference the extract carries",
                    [f"way/{way_id}"],
                ))

        # 5. station entrances
        for node_id, _fid in self.kept_nodes.items():
            if self.class_of.get(f"node/{node_id}") != "station_entrance":
                continue
            tags = self.extract.node_tags[node_id]
            selected = f"node/{node_id}" == self.entrance_ref
            put(self._anchor(
                node_id, "station_entrance",
                ("SELECTED corridor entrance" if selected else "alternate entrance")
                + f" (railway=train_station_entrance ref={tags.get('ref')}): a doorway "
                "is a single unambiguous point that appears in every observation of "
                "the corridor's end",
                [f"node/{node_id}"],
            ))

        # 6. crossing corners
        for node_id in self.kept_nodes:
            if self.class_of.get(f"node/{node_id}") != "crossing":
                continue
            tags = self.extract.node_tags[node_id]
            put(self._anchor(
                node_id, "crossing_corner",
                "crossing node ("
                + ", ".join(f"{k}={v}" for k, v in sorted(tags.items())
                            if k in ("highway", "railway", "crossing", "crossing:markings"))
                + "): road markings put a high-contrast, ground-plane feature here",
                [f"node/{node_id}"],
            ))
        for way_id in self.kept_ways:
            if self.class_of.get(f"way/{way_id}") != "crossing":
                continue
            refs = self.extract.ways[way_id]
            for node_id in {refs[0], refs[-1]}:
                if node_id in self.extract.nodes:
                    put(self._anchor(
                        node_id, "crossing_corner",
                        f"end of crossing way/{way_id}: the kerb where the marked "
                        "crossing meets the footway",
                        [f"way/{way_id}"],
                    ))

        ordered = sorted(anchors.values(), key=lambda a: a["anchor_id"])
        found = Counter(a["anchor_type"] for a in ordered)
        by_type = {t: found.get(t, 0) for t in ANCHOR_TYPES}
        empty = {
            t: f"the AOI extract yields none: candidates come from "
               f"{ANCHOR_TYPE_SOURCE[t]}, and the clipped extract has no such element"
            for t in ANCHOR_TYPES if by_type[t] == 0
        }
        eastings = [a["epsg32654"][0] for a in ordered]
        northings = [a["epsg32654"][1] for a in ordered]
        inside = [a for a in ordered if a["inside_aoi"]]
        in_e = [a["epsg32654"][0] for a in inside]
        in_n = [a["epsg32654"][1] for a in inside]
        return {
            "anchor_set_version": ANCHOR_SET_VERSION,
            "anchor_set_id": f"{self.store.to_dict()['ir_id']}.osm_anchors",
            "generated_at": generated_at,
            "world_ir_id": self.store.to_dict()["ir_id"],
            "world_ir_schema_version": S.SCHEMA_VERSION,
            "source": self.provenance_block(),
            "coordinate_frames": dict(self.store.frames),
            "y9_frame": self.frame.as_metadata(),
            "position_uncertainty": {
                "model": "isotropic_sigma",
                "sigma": OSM_POSITION_SIGMA_M,
                "units": "metre",
                "basis": "OSM digitising tolerance; "
                         f"{OSM_POSITION_SIGMA_WORLD_UNITS} a3d world units "
                         "(world_ir_ingest_carto.OSM_POSITION_SIGMA) divided by "
                         f"historical {OSM_POSITION_SIGMA_REFERENCE_WORLD_UNITS_PER_METRE} world units per metre. Every anchor "
                         "is a real digitised OSM node, never an interpolated point, so "
                         "one tolerance covers the whole set.",
            },
            "corner_min_turn_deg": CORNER_MIN_TURN_DEG,
            "anchor_types_declared": list(ANCHOR_TYPES),
            "anchor_type_candidate_source": dict(sorted(ANCHOR_TYPE_SOURCE.items())),
            "anchor_count": len(ordered),
            "anchor_count_by_type": dict(sorted(by_type.items())),
            "anchor_types_with_no_members": dict(sorted(empty.items())),
            "anchor_count_inside_aoi": len(inside),
            "anchor_count_outside_aoi": len(ordered) - len(inside),
            "anchor_outside_aoi_note": "an anchor outside the AOI ring belongs to a kept "
                                       "AOI feature that reaches past it (a building is kept "
                                       "when any vertex is inside); it is reported, not "
                                       "dropped, and carries inside_aoi=false",
            "anchor_bounds_epsg32654": {
                "min": [min(eastings), min(northings)] if ordered else None,
                "max": [max(eastings), max(northings)] if ordered else None,
            },
            "anchor_bounds_inside_aoi_epsg32654": {
                "min": [min(in_e), min(in_n)] if inside else None,
                "max": [max(in_e), max(in_n)] if inside else None,
            },
            "anchors": ordered,
        }

    # -- drive -------------------------------------------------------------

    def run(self, generated_at: str) -> tuple[WorldIRStore, dict, dict]:
        way_ids, node_ids = self.clip_to_aoi()
        self.ingest_ways(way_ids)
        self.ingest_nodes(node_ids)
        anchor_set = self.build_anchor_set(generated_at)
        unmapped_by_reason = Counter(u["reason"] for u in self.unmapped)
        receipt = {
            "bundle": str(self.bundle_dir),
            "source": self.provenance_block(),
            "extract": self.extract.stats,
            "aoi_clip": self.clip,
            "features_emitted": len(self.store.features),
            "observations_emitted": len(self.store.observations),
            "features_by_class": dict(sorted(self.by_class.items())),
            "features_by_gameplay_authority": dict(sorted(self.by_authority.items())),
            "unmapped_count": len(self.unmapped),
            "unmapped_by_reason": dict(sorted(unmapped_by_reason.items())),
            "unmapped": sorted(self.unmapped, key=lambda u: u["source_ref"]),
            # TRIAGE-01: the work queue.  counts_by_group tallies the
            # triage_group claims; by_group is one row per emitted feature;
            # unmapped_with_reason carries every unmapped element with its tags
            # and the exact rule gap, so nothing in the bbox is silently dropped.
            "triage": {
                "counts_by_group": {
                    group: sum(1 for row in self.triage_rows if row["group"] == group)
                    for group in ("A", "B", "C")
                },
                "by_group": sorted(
                    self.triage_rows,
                    key=lambda row: (row["group"], row["class"], row["feature_id"]),
                ),
                "unmapped_with_reason": [
                    {
                        "source_ref": u["source_ref"],
                        "element": u["element"],
                        "tags": u["tags"],
                        "reason": u["reason"],
                        "rule_gap": _rule_gap(u["tags"]),
                    }
                    for u in sorted(self.unmapped, key=lambda u: u["source_ref"])
                ],
            },
            "anchor_count": anchor_set["anchor_count"],
            "anchor_count_by_type": anchor_set["anchor_count_by_type"],
            "anchor_count_inside_aoi": anchor_set["anchor_count_inside_aoi"],
            "anchor_count_outside_aoi": anchor_set["anchor_count_outside_aoi"],
            "anchor_types_with_no_members": anchor_set["anchor_types_with_no_members"],
            "y9_frame": self.frame.as_metadata(),
            # Counted here, not only in main(), so an importing caller sees the
            # same receipt the CLI prints.
            "validation_findings": len(self.store.validate()),
        }
        return self.store, anchor_set, receipt


def build(bundle_dir: Path, *, ir_id: str = "numabe_corridor_osm",
          generated_at: str = "2026-09-11T00:00:00Z") -> tuple[WorldIRStore, dict, dict]:
    return OSMIngest(bundle_dir, ir_id=ir_id, generated_at=generated_at).run(generated_at)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bundle", type=Path, required=True,
                    help="frozen source bundle directory (holds the .osm, the AOI "
                         "geojson and source_manifest.json)")
    ap.add_argument("--out", type=Path, required=True, help="World IR document to write")
    ap.add_argument("--anchors-out", type=Path, help="OSM anchor set to write")
    ap.add_argument("--summary", type=Path, help="write the ingest receipt here")
    ap.add_argument("--ir-id", default="numabe_corridor_osm")
    ap.add_argument("--generated-at", default="2026-09-11T00:00:00Z")
    ap.add_argument("--validate", action="store_true",
                    help="validate before writing and fail on any finding")
    args = ap.parse_args(argv)

    store, anchor_set, receipt = build(
        args.bundle, ir_id=args.ir_id, generated_at=args.generated_at
    )

    findings = store.validate()
    assert receipt["validation_findings"] == len(findings)
    if args.validate and findings:
        for finding in findings[:20]:
            print(f"  FAIL {finding}", file=sys.stderr)
        print(f"[osm-ingest] {len(findings)} finding(s); nothing written", file=sys.stderr)
        return 1

    store.save(args.out)
    if args.anchors_out:
        args.anchors_out.parent.mkdir(parents=True, exist_ok=True)
        args.anchors_out.write_bytes(S.canonical_bytes(anchor_set))
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    clip = receipt["aoi_clip"]
    print(f"[osm-ingest] extract: {receipt['extract']['unique_ways']} ways, "
          f"{receipt['extract']['unique_nodes']} nodes "
          f"({receipt['extract']['duplicate_way_elements_merged']} duplicate way / "
          f"{receipt['extract']['duplicate_node_elements_merged']} duplicate node "
          "elements merged)")
    print(f"[osm-ingest] AOI clip: kept {clip['ways_kept']} ways, dropped "
          f"{clip['ways_clipped_away']} "
          f"({clip['clipped_away_fraction_of_ways'] * 100:.2f}% of ways); "
          f"kept {clip['tagged_nodes_kept']} tagged nodes")
    print(f"[osm-ingest] features={receipt['features_emitted']} "
          f"observations={receipt['observations_emitted']} findings={len(findings)}")
    print(f"[osm-ingest] by class: {receipt['features_by_class']}")
    print(f"[osm-ingest] by authority: {receipt['features_by_gameplay_authority']}")
    if receipt["unmapped"]:
        print(f"[osm-ingest] UNMAPPED (in AOI, no registry class): "
              f"{receipt['unmapped_count']}")
        for reason, count in sorted(receipt["unmapped_by_reason"].items()):
            print(f"[osm-ingest]   {count}x {reason}")
    print(f"[osm-ingest] anchors: {receipt['anchor_count']} "
          f"{receipt['anchor_count_by_type']}")
    print(f"[osm-ingest] wrote {args.out}"
          + (f" and {args.anchors_out}" if args.anchors_out else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
