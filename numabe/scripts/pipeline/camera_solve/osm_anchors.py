"""OSM control anchors for absolute georegistration (FL-4812).

WHERE THE OSM COMES FROM
========================
The .osm file is downloaded with the SAME Overpass request shape the shipped
runner uses (``_fetch_overpass_osm_file`` at ``scripts/sbu_e2e_run.py:487``):
a bbox node/way/relation body query against ``overpass-api.de``.  That runner
is not edited and no second importer is written; this module only READS a file
produced by that request shape, so there is one Overpass contract in the repo,
not two.

WHAT AN ANCHOR IS, AND IS NOT
=============================
An anchor is a *constraint*, never a truth override.  OSM building outlines
here are tagged ``source=Bing, 2007-03`` or ``GSI/KIBAN 2500``: traced from
imagery, good to a metre or two, and not a survey.  So an anchor enters the
global solve as a weighted residual with a stated sigma alongside the capture
GPS residual and the visual reprojection residual, and it can lose.  Giving
OSM unconditional global XYZ authority is exactly the failure this weighting
exists to prevent.

TWO ANCHOR PATHS, AND WHY BOTH EXIST
====================================
1.  The WALL path (``load_buildings`` .. ``estimate_block_offset``) reads a raw
    ``.osm`` file and models a building outline as the ground trace of a
    vertical facade, which is what a street-level camera actually sees an edge
    of.  It only ever accepted ways carrying a ``building`` tag.  That is the
    CONTROL corridor's registration path and it is unchanged.

2.  The ANCHOR SET path (bottom of this file) reads the versioned World IR OSM
    anchor set that ``world_ir_ingest_osm_extract.py --anchors-out`` already
    emits, and accepts every kind it declares - building corners, station
    entrances, road intersections, crossing corners, railway vertices - each
    modelled as the geometry the map actually asserts rather than coerced into
    a wall segment.

Path 1 alone is why Numabe cannot register: the station entrance there is not
traced as a building, so a building-only reader finds nothing within range and
no anchor can be formed at all.  The earlier claim in this docstring - that
roads are not usable because a centreline's width and centring are not
observable from the kerb - is true of a centreline and NOT true of a junction
NODE, which is a single position two centrelines share.  That distinction is
what the anchor set already encodes and what path 2 consumes.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np

from .geodesy import EnuFrame

# OSM building outlines traced from aerial imagery.  Matched to the digitising
# tolerance already stated by the carto ingest owner
# (``world_ir_ingest_carto.OSM_POSITION_SIGMA``) so the two ingests do not
# assert different accuracies for the same source.
OSM_ANCHOR_SIGMA_M = 1.5


@dataclass
class BuildingOutline:
    """One OSM building way, as an ENU ground ring."""

    osm_ref: str
    tags: dict[str, str]
    ring: np.ndarray  # (N, 2) east/north metres

    @property
    def segments(self) -> list[tuple[np.ndarray, np.ndarray]]:
        return [(self.ring[i], self.ring[i + 1]) for i in range(len(self.ring) - 1)]


def _tags(elem: ET.Element) -> dict[str, str]:
    return {t.get("k", ""): t.get("v", "") for t in elem.findall("tag")}


def load_buildings(osm_path: str, frame: EnuFrame) -> list[BuildingOutline]:
    """Read every closed building way out of an Overpass bbox dump into ENU."""
    root = ET.parse(osm_path).getroot()
    nodes = {
        n.get("id"): (float(n.get("lon")), float(n.get("lat")))
        for n in root.findall("node")
        if n.get("lon") and n.get("lat")
    }
    out: list[BuildingOutline] = []
    for way in root.findall("way"):
        tags = _tags(way)
        if not tags.get("building"):
            continue
        refs = [nd.get("ref") for nd in way.findall("nd")]
        coords = [nodes[r] for r in refs if r in nodes]
        if len(coords) < 4 or coords[0] != coords[-1]:
            continue
        ring = np.array([frame.to_enu(lon, lat)[:2] for lon, lat in coords], dtype=np.float64)
        out.append(BuildingOutline(osm_ref=f"way/{way.get('id')}", tags=tags, ring=ring))
    out.sort(key=lambda b: b.osm_ref)
    return out


def point_segment_distance(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> tuple[float, np.ndarray]:
    """Distance from ``p`` to segment ``ab`` plus the closest point on it."""
    ab = b - a
    denom = float(ab @ ab)
    if denom < 1e-12:
        return float(np.linalg.norm(p - a)), a
    t = float(np.clip((p - a) @ ab / denom, 0.0, 1.0))
    foot = a + t * ab
    return float(np.linalg.norm(p - foot)), foot


@dataclass
class WallMatch:
    """A reconstructed point tied to one OSM building wall segment."""

    osm_ref: str
    tags: dict[str, str]
    seg_a: np.ndarray
    seg_b: np.ndarray
    distance_m: float
    foot: np.ndarray

    @property
    def normal(self) -> np.ndarray:
        """Unit horizontal normal of the wall; the direction the residual acts in."""
        d = self.seg_b - self.seg_a
        n = np.array([-d[1], d[0]])
        length = np.linalg.norm(n)
        return n / length if length > 1e-9 else np.array([1.0, 0.0])


def nearest_wall(point_en: np.ndarray, buildings: list[BuildingOutline]) -> WallMatch | None:
    """Closest building wall segment to a horizontal position, or None."""
    best: WallMatch | None = None
    for building in buildings:
        for a, b in building.segments:
            dist, foot = point_segment_distance(point_en, a, b)
            if best is None or dist < best.distance_m:
                best = WallMatch(
                    osm_ref=building.osm_ref, tags=building.tags,
                    seg_a=a, seg_b=b, distance_m=dist, foot=foot,
                )
    return best


def signed_wall_offset(point_en: np.ndarray, match: WallMatch) -> float:
    """Signed offset of a point from the wall line, along the wall normal.

    Signed rather than absolute because the global solve differentiates it: an
    absolute value has a kink at zero that stalls a least-squares step.
    """
    return float((point_en - match.foot) @ match.normal)


def has_class(osm_path: str, predicate) -> list[dict]:
    """Every tagged node satisfying ``predicate``; used to PROVE an absence.

    The triangulated fixture only counts as unmapped if the map really does not
    have it.  That is a claim about the source, so it is answered by reading the
    source rather than by assertion.
    """
    root = ET.parse(osm_path).getroot()
    hits = []
    for node in root.findall("node"):
        tags = _tags(node)
        if tags and predicate(tags):
            hits.append({"osm_ref": f"node/{node.get('id')}", "tags": tags})
    for way in root.findall("way"):
        tags = _tags(way)
        if tags and predicate(tags):
            hits.append({"osm_ref": f"way/{way.get('id')}", "tags": tags})
    return hits


def is_pole_like(tags: dict) -> bool:
    """Every OSM tagging a vertical street pole could plausibly carry."""
    return bool(
        tags.get("power") in {"pole", "tower", "portal"}
        or tags.get("man_made") in {"utility_pole", "mast", "pole", "street_lamp"}
        or tags.get("highway") == "street_lamp"
        or tags.get("amenity") == "street_lamp"
        or "utility_pole" in tags.get("man_made", "")
    )


def estimate_block_offset(points_en, buildings: list[BuildingOutline],
                          gate_m: float, iterations: int = 5) -> tuple[np.ndarray, dict]:
    """Robustly estimate the systematic XY shift between the block and the map.

    Before the global solve the reconstruction sits wherever the capture GPS put
    it - metres away from OSM, and in ONE direction for the whole block, because
    a GPS bias is common to the whole track.  Associating at a radius wide enough
    to absorb that shift also lets in anything standing a few metres in front of
    a wall; the first run of this pipeline duly picked a chain-link catch net
    3.9 m in front of a building and called it a facade.

    Estimating the shift first separates the two.  A real facade is explained by
    the SAME translation as every other facade; a fence in front of one wall is
    not.  Only the component along each wall's normal is observable, so this
    solves the 2x2 normal equations of

        minimise  sum_i ( n_i . (p_i - foot_i - d) )^2

    reweighted a few times to keep gross mismatches from steering it.
    """
    observations = []
    for p in points_en:
        wall = nearest_wall(p, buildings)
        if wall is None or wall.distance_m > gate_m:
            continue
        observations.append((wall.normal, p - wall.foot))
    if len(observations) < 8:
        return np.zeros(2), {"used": len(observations), "converged": False}
    d = np.zeros(2)
    weights = np.ones(len(observations))
    for _ in range(iterations):
        A = np.zeros((2, 2))
        b = np.zeros(2)
        for w, (n, delta) in zip(weights, observations):
            A += w * np.outer(n, n)
            b += w * n * float(n @ delta)
        if abs(np.linalg.det(A)) < 1e-9:
            break
        d = np.linalg.solve(A, b)
        residuals = np.array([abs(float(n @ (delta - d))) for n, delta in observations])
        scale = max(float(np.median(residuals)) * 1.4826, 0.2)
        weights = 1.0 / (1.0 + (residuals / (2.0 * scale)) ** 2)  # Cauchy
    residuals = np.array([abs(float(n @ (delta - d))) for n, delta in observations])
    return d, {
        "used": len(observations),
        "gate_m": gate_m,
        "offset_east_m": float(d[0]),
        "offset_north_m": float(d[1]),
        "offset_magnitude_m": float(np.linalg.norm(d)),
        "residual_median_m": float(np.median(residuals)),
        "converged": True,
    }


# ==========================================================================
# World IR anchor set
# ==========================================================================
# Everything above this line derives building outlines from a raw ``.osm`` file
# and models every anchor as a WALL SEGMENT.  That is right for a facade, and it
# is the whole reason the CONTROL corridor registers.  It is also the reason a
# station forecourt does not: at Numabe the entrance is not traced as a
# building, the nearest facade is 7.46 m away, so the building-only reader sees
# zero eligible ways and no anchor can be formed at all.
#
# ``scripts/pipeline/world_ir_ingest_osm_extract.py --anchors-out`` already
# emits a reviewed, versioned OSM ANCHOR SET for exactly this purpose: 765
# anchors at Numabe across five populated kinds.  This section READS that
# document.  It does not re-derive it, and it does not re-tag OSM: there is one
# OSM-to-anchor owner in the repo and it is that ingest.
#
# WHY THE KINDS ARE NOT ALL THE SAME SHAPE
# ========================================
# An anchor kind is a statement about what geometry the map actually asserts,
# and coercing all five into "wall segment" would assert geometry the source
# never carried:
#
#   building_corner      two wall planes meet at a vertical edge.  The map
#   station_entrance     asserts a POSITION, so the residual is 2-D: east and
#   road_intersection    north, both observable, both constrained.
#   crossing_corner
#   wall_fence_endpoint
#
#   railway_vertex       a vertex of a rail centreline.  The map asserts the
#                        CURVE, not which vertex a sleeper is; the digitiser
#                        put vertices wherever the curvature needed them.  So
#                        the residual is 1-D, ACROSS the track only, and the
#                        constraint lets a matched point slide freely ALONG it.
#                        Constraining it in 2-D would invent an along-track
#                        position the source does not claim.
#
# WHY THE SET'S OWN SIGMA IS USED AND NOT ``OSM_ANCHOR_SIGMA_M``
# ==============================================================
# The anchor set carries ``position_uncertainty`` and a per-anchor
# ``uncertainty`` block.  At Numabe that is 0.667 m, not the 1.5 m this module
# assumes for a wall, because the ingest states its tolerance in a3d world
# units and converts.  Two owners must not assert different accuracies for the
# same node, so the set's stated sigma wins and the disagreement is reported
# rather than averaged away.

# Kinds whose map geometry is a position: residual is 2-D (east, north).
ANCHOR_POINT_KINDS = (
    "building_corner",
    "crossing_corner",
    "road_intersection",
    "station_entrance",
    "wall_fence_endpoint",
)
# Kinds whose map geometry is a curve: residual is 1-D, across the curve.
ANCHOR_CURVE_KINDS = ("railway_vertex",)

# How many same-way neighbours estimate a curve anchor's local tangent.  The
# anchor set does not carry the way's node ORDER - anchors are sorted by
# anchor_id - so the tangent is estimated from the local neighbourhood rather
# than by guessing a chain through the vertices.  A guessed chain would be a
# fabricated topology; a local principal direction is a measurement of the
# vertices actually present.
CURVE_TANGENT_NEIGHBOURS = 4
# A curve anchor with no same-way neighbour close enough to give a direction is
# not silently demoted to a point anchor - it is dropped, and counted.
CURVE_TANGENT_MAX_NEIGHBOUR_M = 40.0


@dataclass(frozen=True)
class WorldIRAnchor:
    """One anchor from the World IR OSM anchor set, in the solve's ENU frame."""

    anchor_id: str
    anchor_type: str
    osm_ref: str
    en: np.ndarray  # (2,) east/north metres
    sigma_m: float
    inside_aoi: bool
    reason: str
    owner_feature_ids: tuple[str, ...] = ()
    # Unit east/north tangent of the curve this anchor lies on; None for a
    # point anchor.  Its presence IS the geometry declaration.
    tangent_en: np.ndarray | None = None

    @property
    def geometry_kind(self) -> str:
        return "point_on_curve" if self.tangent_en is not None else "point"

    @property
    def residual_rows(self) -> int:
        return 1 if self.tangent_en is not None else 2

    @property
    def normal_en(self) -> np.ndarray:
        """Across-curve direction; the only direction a curve anchor constrains."""
        if self.tangent_en is None:
            raise ValueError(f"{self.anchor_id} is a point anchor and has no curve normal")
        t = self.tangent_en
        return np.array([-t[1], t[0]])

    def offset(self, point_en: np.ndarray) -> np.ndarray:
        """Signed residual vector this anchor asserts about a horizontal position.

        Two components for a point anchor, one for a point-on-curve anchor.
        Signed, not absolute, because the global solve differentiates it.
        """
        delta = np.asarray(point_en, dtype=np.float64)[:2] - self.en
        if self.tangent_en is None:
            return delta
        return np.array([float(delta @ self.normal_en)])

    def miss_m(self, point_en: np.ndarray) -> float:
        """Distance in metres from a position to what this anchor constrains."""
        return float(np.linalg.norm(self.offset(point_en)))


@dataclass
class AnchorSet:
    """A loaded ``--anchors-out`` document, converted into one ENU frame."""

    anchor_set_id: str
    anchor_set_version: str
    world_ir_id: str
    source_path: str
    declared_sigma_m: float
    anchors: list[WorldIRAnchor]
    counts_by_kind: dict[str, int]
    dropped: dict[str, int]

    def __len__(self) -> int:
        return len(self.anchors)

    @property
    def by_id(self) -> dict[str, WorldIRAnchor]:
        return {a.anchor_id: a for a in self.anchors}

    def of_kind(self, *kinds: str) -> list[WorldIRAnchor]:
        wanted = set(kinds)
        return [a for a in self.anchors if a.anchor_type in wanted]

    def provenance(self) -> dict:
        return {
            "anchor_set_id": self.anchor_set_id,
            "anchor_set_version": self.anchor_set_version,
            "world_ir_id": self.world_ir_id,
            "source_path": self.source_path,
            "declared_position_sigma_m": self.declared_sigma_m,
            "wall_path_assumed_sigma_m": OSM_ANCHOR_SIGMA_M,
            "sigma_note": (
                "the anchor set's own stated sigma is used, not this module's wall "
                "assumption; both are reported so the disagreement is visible"
            ),
            "anchors_loaded": len(self.anchors),
            "anchors_by_kind": dict(sorted(self.counts_by_kind.items())),
            "anchors_dropped": dict(sorted(self.dropped.items())),
            "point_kinds": list(ANCHOR_POINT_KINDS),
            "curve_kinds": list(ANCHOR_CURVE_KINDS),
            "curve_tangent_neighbours": CURVE_TANGENT_NEIGHBOURS,
            "curve_tangent_max_neighbour_m": CURVE_TANGENT_MAX_NEIGHBOUR_M,
        }


def _curve_tangent(target: np.ndarray, siblings: np.ndarray) -> np.ndarray | None:
    """Unit local direction of a curve through ``target``, from its neighbours.

    Principal direction of the nearest same-way vertices.  Returns None when no
    sibling is close enough to say anything, so an undirected vertex is dropped
    instead of being quietly treated as a position the map never asserted.
    """
    if len(siblings) == 0:
        return None
    d = np.linalg.norm(siblings - target, axis=1)
    order = np.argsort(d)[:CURVE_TANGENT_NEIGHBOURS]
    near = siblings[order][d[order] <= CURVE_TANGENT_MAX_NEIGHBOUR_M]
    if len(near) < 1:
        return None
    centred = np.vstack([near, target]) - target
    principal = np.linalg.svd(centred, full_matrices=False)[2][0]
    norm = float(np.linalg.norm(principal))
    if norm < 1e-9:
        return None
    return principal / norm


def load_anchor_set(anchors_path: str, frame: EnuFrame, *,
                    kinds: tuple[str, ...] | None = None) -> AnchorSet:
    """Read a World IR OSM anchor set into the solve's ENU frame.

    ``kinds`` restricts what is loaded.  The default is every populated kind the
    document declares, because the caller that wanted only buildings is the
    caller this function exists to replace.
    """
    import json

    doc = json.loads(open(anchors_path, encoding="utf-8").read())
    declared = float(doc.get("position_uncertainty", {}).get("sigma", OSM_ANCHOR_SIGMA_M))
    wanted = set(kinds) if kinds is not None else set(ANCHOR_POINT_KINDS) | set(ANCHOR_CURVE_KINDS)

    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    # Pass 1: every requested anchor as a position.
    raw: list[dict] = []
    for record in doc.get("anchors", []):
        kind = record.get("anchor_type", "")
        if kind not in wanted:
            continue
        lonlat = record.get("epsg4326")
        if not lonlat or len(lonlat) != 2:
            drop("no epsg4326 position")
            continue
        east, north, _ = frame.to_enu(float(lonlat[0]), float(lonlat[1]), 0.0)
        raw.append({"record": record, "en": np.array([east, north])})

    # Pass 2: curve anchors get a local tangent from their own way's vertices.
    same_way: dict[str, list[np.ndarray]] = {}
    for item in raw:
        if item["record"]["anchor_type"] not in ANCHOR_CURVE_KINDS:
            continue
        for owner in item["record"].get("owner_feature_ids", []):
            same_way.setdefault(owner, []).append(item["en"])

    out: list[WorldIRAnchor] = []
    for item in raw:
        record, en = item["record"], item["en"]
        kind = record["anchor_type"]
        tangent = None
        if kind in ANCHOR_CURVE_KINDS:
            siblings = [
                p for owner in record.get("owner_feature_ids", [])
                for p in same_way.get(owner, [])
                if not np.allclose(p, en)
            ]
            tangent = _curve_tangent(en, np.array(siblings) if siblings else np.zeros((0, 2)))
            if tangent is None:
                drop(f"{kind}: no same-way neighbour within "
                     f"{CURVE_TANGENT_MAX_NEIGHBOUR_M:.0f} m to give a tangent")
                continue
        sigma = float(record.get("uncertainty", {}).get("sigma", declared))
        out.append(WorldIRAnchor(
            anchor_id=record["anchor_id"],
            anchor_type=kind,
            osm_ref=record.get("osm_ref", ""),
            en=en,
            sigma_m=sigma,
            inside_aoi=bool(record.get("inside_aoi", True)),
            reason=record.get("reason", ""),
            owner_feature_ids=tuple(record.get("owner_feature_ids", [])),
            tangent_en=tangent,
        ))
    out.sort(key=lambda a: a.anchor_id)
    counts: dict[str, int] = {}
    for a in out:
        counts[a.anchor_type] = counts.get(a.anchor_type, 0) + 1
    return AnchorSet(
        anchor_set_id=doc.get("anchor_set_id", ""),
        anchor_set_version=doc.get("anchor_set_version", ""),
        world_ir_id=doc.get("world_ir_id", ""),
        source_path=str(anchors_path),
        declared_sigma_m=declared,
        anchors=out,
        counts_by_kind=counts,
        dropped=dropped,
    )


# ---- association ---------------------------------------------------------
# A reconstructed point is only allowed to name an anchor when the naming is
# UNAMBIGUOUS.  678 building corners inside a 200 m box means the nearest
# corner to an arbitrary point is nearly always within a few metres of a
# SECOND corner, and a correspondence that could equally have been its
# neighbour is not evidence - it is a coin toss that the solve would then
# weight as though it were a measurement.
ANCHOR_AMBIGUITY_RATIO = 2.0


@dataclass
class AnchorMatch:
    """One reconstructed point tied to one World IR anchor, with its rivals named."""

    track_index: int
    anchor: WorldIRAnchor
    distance_m: float
    runner_up: WorldIRAnchor | None
    runner_up_distance_m: float

    @property
    def ambiguity_ratio(self) -> float:
        """How much nearer the winner is than its nearest rival."""
        if self.runner_up is None:
            return float("inf")
        if self.distance_m < 1e-9:
            return float("inf")
        return self.runner_up_distance_m / self.distance_m

    def as_record(self) -> dict:
        return {
            "track_index": int(self.track_index),
            "anchor_id": self.anchor.anchor_id,
            "anchor_type": self.anchor.anchor_type,
            "geometry_kind": self.anchor.geometry_kind,
            "osm_ref": self.anchor.osm_ref,
            "distance_m": self.distance_m,
            "runner_up_anchor_id": self.runner_up.anchor_id if self.runner_up else None,
            "runner_up_distance_m": self.runner_up_distance_m,
            "ambiguity_ratio": self.ambiguity_ratio,
            "sigma_m": self.anchor.sigma_m,
            "residual_rows": self.anchor.residual_rows,
            "inside_aoi": self.anchor.inside_aoi,
        }


def nearest_anchors(point_en: np.ndarray, anchors: list[WorldIRAnchor]
                    ) -> tuple[WorldIRAnchor | None, float, WorldIRAnchor | None, float]:
    """Best and second-best anchor for a horizontal position, by its own metric.

    "Its own metric" matters: a point-on-curve anchor is scored on across-curve
    distance only, so a point 30 m along the track from the vertex is still a
    0 m match to the rail, which is exactly what the map asserts.
    """
    best: WorldIRAnchor | None = None
    best_d = float("inf")
    second: WorldIRAnchor | None = None
    second_d = float("inf")
    for anchor in anchors:
        d = anchor.miss_m(point_en)
        if d < best_d:
            second, second_d = best, best_d
            best, best_d = anchor, d
        elif d < second_d:
            second, second_d = anchor, d
    return best, best_d, second, second_d


def associate_anchors(points_en: dict[int, np.ndarray], anchors: list[WorldIRAnchor],
                      *, gate_m: float, offset_en: np.ndarray | None = None,
                      ambiguity_ratio: float = ANCHOR_AMBIGUITY_RATIO
                      ) -> tuple[list[AnchorMatch], dict]:
    """Match reconstructed points to anchors, and report the whole funnel.

    Every stage of the funnel is counted, because "no anchor was formed" and
    "we never looked" must not produce the same receipt.  ``offset_en`` is the
    block's already-estimated systematic shift; it is REMOVED before matching
    and is not a correction applied to the solve.
    """
    shift = np.zeros(2) if offset_en is None else np.asarray(offset_en, dtype=np.float64)[:2]
    considered = 0
    inside_gate = 0
    rejected_ambiguous = 0
    matches: list[AnchorMatch] = []
    for track_index in sorted(points_en):
        p = np.asarray(points_en[track_index], dtype=np.float64)[:2] - shift
        considered += 1
        best, best_d, second, second_d = nearest_anchors(p, anchors)
        if best is None or best_d > gate_m:
            continue
        inside_gate += 1
        match = AnchorMatch(track_index=track_index, anchor=best, distance_m=best_d,
                            runner_up=second, runner_up_distance_m=second_d)
        if match.ambiguity_ratio < ambiguity_ratio:
            rejected_ambiguous += 1
            continue
        matches.append(match)
    per_kind: dict[str, int] = {}
    for m in matches:
        per_kind[m.anchor.anchor_type] = per_kind.get(m.anchor.anchor_type, 0) + 1
    report = {
        "points_considered": considered,
        "anchors_available": len(anchors),
        "association_gate_m": gate_m,
        "block_offset_removed_m": [float(shift[0]), float(shift[1])],
        "ambiguity_ratio_required": ambiguity_ratio,
        "points_inside_gate": inside_gate,
        "points_rejected_ambiguous": rejected_ambiguous,
        "matches": len(matches),
        "matches_by_kind": dict(sorted(per_kind.items())),
        "distance_m": {
            "min": float(min(m.distance_m for m in matches)) if matches else None,
            "median": float(np.median([m.distance_m for m in matches])) if matches else None,
            "max": float(max(m.distance_m for m in matches)) if matches else None,
        },
    }
    return matches, report


def anchors_within(point_en: np.ndarray, anchors: list[WorldIRAnchor],
                   radius_m: float) -> dict[str, int]:
    """Census of anchors within ``radius_m`` of a position, by kind.

    Answers "is there anything to register against at all" separately from
    "did a constraint form", so an empty solve can say which of the two it was.
    """
    out: dict[str, int] = {}
    for anchor in anchors:
        if anchor.miss_m(point_en) <= radius_m:
            out[anchor.anchor_type] = out.get(anchor.anchor_type, 0) + 1
    return dict(sorted(out.items()))
