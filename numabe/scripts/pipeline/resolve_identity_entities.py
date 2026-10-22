#!/usr/bin/env python3
"""Generic entity and host resolution (FL-4876 ENTITY-01).

Pipeline position:

    raw sources -> immutable observations (register_observations.py)
    -> **resolve_identity_entities.py** -> canonical semantic entities
    -> representation planning (compile_population_plan.py, place fronts,
       station assembly) -> A3D lowering -> identity invariant gate

Rule: observations never directly create geometry. This stage is the only
consumer of registered observations that representation planning sees. It
emits canonical semantic entities; each entity consumes zero, one or many
observations, and every observation is either consumed by exactly one entity
or listed unresolved with a reason. Representation planning receives entity
records only (``entity_instances.json``); an unresolved observation can never
reach geometry because it is not in that file.

Entity kinds
  mapped_place      a canonical OSM place with an identity tag (shop=,
                    amenity=, office=, tourism=, healthcare=, craft=,
                    railway=, public_transport=) and a name or brand.
                    Its representation (place front, station assembly) is
                    owned by the stages that already own map places; this
                    stage records which observations support or contest it.
  observed_place    an identity seen in photographs that no mapped place
                    explains; it may create identity geometry only when its
                    host resolves.
  generic_fixture   a catalog module observation (pole, vending machine);
                    not identity-bearing, one entity per registered cluster.

Identity matching is by whole name tokens: a place matches an observation
subject when the place's name/brand tokens occur as a contiguous run of the
subject's tokens (or their concatenation equals a contiguous run, so
"Brand Mart" and "brandmart" agree). There is no substring matching.

Host resolution
  mapped_place: the footprint containing the place node; else the nearest
  footprint when it is clearly nearest (the second-nearest is at least twice
  as far); else ambiguous. An identity-matched observation SUPPORTS the entity
  when its registered point lies within its own position uncertainty of the
  host footprint, and CONTESTS it otherwise. Contesting observations are kept
  as evidence; they create nothing. The map node stays the location prior: a
  contest is recorded (host_contested) for review, not silently obeyed.
  observed_place: observations of one subject belong to one entity when each
  pair is within the sum of their uncertainties; the entity position is the
  inverse-variance mean and its uncertainty 1/sqrt(sum w). Its host is the
  nearest footprint, resolved only when the entity uncertainty is smaller than
  the distance to the next footprint.

Level (storey) is recorded as observed only from an OSM level tag; otherwise
it is unknown here. Any later storey choice must carry its own basis.

usage: resolve_identity_entities.py --instances REG.json --world-ir CANON.json
           --out-dir DIR
writes DIR/identity_entities.json and DIR/entity_instances.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

SCHEMA = "y92.identity_entities.v1"
IDENTITY_KEYS = ("shop", "amenity", "office", "tourism", "healthcare", "craft",
                 "railway", "public_transport")
TRANSIT = {("railway", "station"), ("railway", "stop"), ("railway", "halt"),
           ("public_transport", "station")}
NAME_TAG = re.compile(r"^(name|brand|official_name|alt_name|short_name)(:[a-z_-]+)?$")
MIN_IDENTITY_CHARS = 4
UNCERTAINTY_FLOOR_M = 0.25  # registration reports at least this (review-box quantum)
TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "assets" / "world_templates"
# Storefront signage in the capture-review carrier vocabulary
# (observation_module_mapping generic_carrier_module_id). A sighting whose
# reviewed carrier is one of these shows a shop sign; the crop IS the sign, so
# its business name need not be transcribed for the sign to be shown.
SIGN_CARRIERS = frozenset({"storefront_signboard", "storefront_pedestal_sign",
                           "small_commercial_awning", "attached_storefront_sign",
                           "sidewalk_signboard"})


def tokens(s) -> list[str]:
    return [t for t in re.split(r"[\W_]+", str(s).lower()) if t]


def identity_match(name_tokens: list[str], subject_tokens: list[str]) -> int:
    """Characters matched when name tokens are a contiguous run of subject
    tokens (or concatenate to one); 0 when they do not."""
    if not name_tokens:
        return 0
    joined = "".join(name_tokens)
    if len(joined) < MIN_IDENTITY_CHARS:
        return 0
    n = len(name_tokens)
    for i in range(len(subject_tokens)):
        if subject_tokens[i:i + n] == name_tokens:
            return len(joined)
        acc = ""
        for t in subject_tokens[i:]:
            acc += t
            if acc == joined:
                return len(joined)
            if len(acc) >= len(joined):
                break
    return 0


def _seg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy or 1e-12
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def ring_distance(p, ring) -> float:
    return min((_seg(p, a, b) for a, b in zip(ring, ring[1:])), default=math.inf)


def inside(p, ring) -> bool:
    c = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def _unc(inst) -> float:
    u = inst.get("position_uncertainty_m")
    return max(UNCERTAINTY_FLOOR_M, float(u)) if u is not None else math.inf


class World:
    def __init__(self, world_ir: dict):
        obs = {o.get("observation_id"): o for o in world_ir.get("source_observations", [])}
        self.rings = {f["feature_id"]: f["geometry"]["coordinates"]
                      for f in world_ir.get("canonical_features", [])
                      if f.get("semantic_class") == "building_footprint"
                      and (f.get("geometry") or {}).get("kind") == "polygon"}
        self.places = []
        for f in world_ir.get("canonical_features", []):
            g = f.get("geometry") or {}
            if f.get("semantic_class") != "place" or g.get("kind") != "point":
                continue
            tags = {}
            for oid in f.get("supporting_observation_ids", []) or []:
                tags.update(((obs.get(oid) or {}).get("attributes") or {}).get("tags") or {})
            if not any(k in tags for k in IDENTITY_KEYS):
                continue
            names = sorted({tuple(tokens(v)) for k, v in tags.items() if NAME_TAG.match(k) and v})
            names = [list(n) for n in names if len("".join(n)) >= MIN_IDENTITY_CHARS]
            if not names:
                continue
            self.places.append({
                "place_id": f["feature_id"], "names": names, "tags": tags,
                "xy": tuple(g["coordinates"][:2]),
                "transit": any((k, tags.get(k)) in TRANSIT for k in ("railway", "public_transport"))})

    def ranked(self, xy):
        return sorted(self.rings, key=lambda k: ring_distance(xy, self.rings[k]))

    def host_of_observation(self, xy):
        """(host, next-building distance) for an observed point: the footprint
        that contains it, else the nearest ring; the competing distance is to
        the nearest OTHER ring, so a point inside a building is not handed to
        a neighbour whose wall happens to be closer."""
        if not self.rings:
            return None, math.inf
        host = next((k for k, r in self.rings.items() if inside(xy, r)), None)
        r = self.ranked(xy)
        if host is None:
            host = r[0]
        others = [k for k in r[:3] if k != host]
        nb = ring_distance(xy, self.rings[others[0]]) if others else math.inf
        return host, nb

    def host_for_point(self, xy):
        """Host of a mapped node: containing footprint, else clearly-nearest."""
        if not self.rings:
            return None, "no_footprints", None
        for k, r in self.rings.items():
            if inside(xy, r):
                return k, "contains_node", 0.0
        r = self.ranked(xy)
        d1 = ring_distance(xy, self.rings[r[0]])
        d2 = ring_distance(xy, self.rings[r[1]]) if len(r) > 1 else math.inf
        if d2 >= 2.0 * d1:
            return r[0], "clearly_nearest_to_node", d1
        return None, "ambiguous_between_" + r[0] + "_and_" + r[1], d1


def _annotation_authority(mapping_rows: dict, annotation_ids) -> tuple[bool, list]:
    """An observation may stand for an identity only when its review resolved
    the reference (authoritative role, or lawful_reference_state resolved)."""
    states = []
    ok = False
    for a in annotation_ids or []:
        m = (mapping_rows.get(str(a)) or {}).get("mapping") or {}
        st = m.get("lawful_reference_state")
        states.append(st)
        if m.get("population_role_authoritative") is True or st == "resolved":
            ok = True
    return ok, sorted({str(s) for s in states})


def _review_carriers(mapping_rows: dict, annotation_ids) -> set:
    """Reviewed generic carrier module ids of an observation's annotations."""
    return {str(((mapping_rows.get(str(a)) or {}).get("mapping") or {}).get("generic_carrier_module_id"))
            for a in annotation_ids or []} - {"None"}


def face_projection(ring, p):
    """(face_index, along_m, face_len, foot_point, outward_normal, distance) of
    p onto the nearest face of a closed ring."""
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
    best = None
    for i, (a, b) in enumerate(zip(ring, ring[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (L * L)))
        q = (a[0] + t * dx, a[1] + t * dy)
        d = math.hypot(p[0] - q[0], p[1] - q[1])
        if best is None or d < best[5]:
            # right-hand normal of a CCW ring points outward
            n = (dy / L, -dx / L) if area > 0 else (-dy / L, dx / L)
            best = (i, t * L, L, q, n, d)
    return best


def _norm_addr(a: str) -> str:
    """Japanese address key: NFKC, no postcode/country, dashes unified, 番/号 as dashes."""
    import unicodedata
    s = unicodedata.normalize("NFKC", str(a or ""))
    s = re.sub(r"日本、?|〒?\d{3}-\d{4}", "", s)
    s = s.replace("−", "-").replace("ー", "-").replace("番地", "-").replace("番", "-").replace("号", "")
    s = re.sub(r"\s+", "", s)
    m = re.search(r"([^\d]*?[町村丁目])(\d+(?:-\d+)*)", s)
    return (m.group(1) + m.group(2)).strip("-") if m else s


def _canonical_place_faces(representation_plan: dict | None) -> dict:
    """place feature id -> (building id, face index, along fraction) from the
    representation plan's resolved building_face attachment."""
    out = {}
    for r in (representation_plan or {}).get("rows", []):
        att = r.get("attachment") or {}
        fid = str(r.get("feature_id") or "")
        if (fid.startswith("feature:place:") and r.get("attachment_state") == "resolved"
                and att.get("anchor_kind") == "building_face" and att.get("face_index") is not None):
            if fid not in out or r.get("primary"):
                out[fid] = (att.get("canonical_building_id"), int(att["face_index"]),
                            float(att.get("along_face_fraction", 0.5)))
    return out


def max_front_width_m(template_root: Path) -> float:
    """Widest place front any template pack profile may fit (the slot a
    mapped place may claim on its face)."""
    widths = []
    for pack in sorted(Path(template_root).glob("*/pack.json")):
        doc = json.loads(pack.read_text(encoding="utf-8"))
        widths += [float(p["max_width_m"]) for p in doc.get("place_front_profiles", []) or []
                   if p.get("max_width_m") is not None]
    if not widths:
        raise SystemExit(f"no place_front_profiles max_width_m under {template_root}")
    return max(widths)


def _canonical_place_hosts(representation_plan: dict | None) -> dict:
    """place feature id -> (building id, basis) from the representation plan's
    resolved building_face attachment (the canonical attachment owner)."""
    out = {}
    for r in (representation_plan or {}).get("rows", []):
        att = r.get("attachment") or {}
        fid = str(r.get("feature_id") or "")
        if (fid.startswith("feature:place:") and r.get("attachment_state") == "resolved"
                and att.get("anchor_kind") == "building_face" and att.get("canonical_building_id")):
            if fid not in out or r.get("primary"):
                out[fid] = (att["canonical_building_id"],
                            "canonical_attachment:" + str(att.get("face_selection_reason")))
    return out


ADJ_PRIORITY = {("mapped_place", "logo"): 0, ("business", "logo"): 1, ("mapped_place", "evidence"): 2,
                ("business", "evidence"): 3, ("station", None): 4, ("not_identity", None): 5, ("illegible", None): 6}


def _primary_decision(adj: dict, annotation_ids):
    """The decision that owns an observation whose annotations were visually
    adjudicated (a registration cluster may merge annotations of different
    things, e.g. a shop fascia and the upper-floor sign above it)."""
    ds = [adj[a] for a in annotation_ids or [] if a in adj]
    if not ds:
        return None
    return min(ds, key=lambda d: ADJ_PRIORITY.get((d["kind"], d.get("role")), 9))


def _face_for_business(ring, p, u, width_m):
    """Face of a host ring for an unmapped business sign near p: the nearest
    face, unless it is shorter than the sign, then the longest face within the
    sighting uncertainty (a rounded corner is several short faces)."""
    fi, s, L, q, n, d = face_projection(ring, p)
    if L < width_m:
        best = None
        for i, (a, b) in enumerate(zip(ring, ring[1:])):
            Li = math.dist(a, b)
            # radius measured beyond the nearest wall: the point itself may sit off the ring
            if Li < 1e-6 or _seg(p, a, b) > d + max(u, 2.5):
                continue
            if best is None or Li > best[1]:
                best = (i, Li)
        if best is not None and best[0] != fi:
            fi, L = best
            a, b = ring[fi], ring[fi + 1]
            t = max(0.0, min(1.0, ((p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])) / (L * L)))
            s = t * L
            n = face_projection(ring, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))[4]
    # keep the whole sign on its face
    a, b = ring[fi], ring[fi + 1]
    s = min(max(s, min(width_m / 2.0, L / 2.0)), max(L - width_m / 2.0, L / 2.0))
    t = s / L if L > 1e-9 else 0.0
    q = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
    return fi, s, L, q, n


def _face_toward_cameras(ring, cams, rays, width_m):
    """Street face of a host for a sign photographed from cams (FACE-CAM-01):
    the face whose outward side looks at the cameras and that the bearing rays
    cross (else the nearest such face to the cameras); along-face position is
    the median ray crossing, else the foot of the mean camera. None if no face
    of the host looks at any camera."""
    cx = sum(c[0] for c in cams) / len(cams)
    cy = sum(c[1] for c in cams) / len(cams)
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))
    best = None
    for i, (a, b) in enumerate(zip(ring, ring[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        n = (dy / L, -dx / L) if area > 0 else (-dy / L, dx / L)
        hits = []
        for (ox, oy), brg in rays:
            rx, ry = math.sin(math.radians(brg)), math.cos(math.radians(brg))
            den = rx * dy - ry * dx
            if abs(den) < 1e-9:
                continue
            t = ((a[0] - ox) * dy - (a[1] - oy) * dx) / den          # along the ray
            s = ((a[0] - ox) * ry - (a[1] - oy) * rx) / den          # along the face, 0..1
            if t > 0 and 0.0 <= s <= 1.0 and (rx * n[0] + ry * n[1]) < 0:   # ray meets the face from outside
                hits.append(s * L)
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        facing = (cx - mx) * n[0] + (cy - my) * n[1] > 0
        if not facing:
            continue
        dist = _seg((cx, cy), a, b)
        key = (0 if hits else 1, -len(hits), dist)
        if best is None or key < best[0]:
            best = (key, i, L, n, hits)
    if best is None:
        return None
    _, i, L, n, hits = best
    a, b = ring[i], ring[i + 1]
    if hits:
        s = sorted(hits)[len(hits) // 2]
    else:
        t = max(0.0, min(1.0, ((cx - a[0]) * (b[0] - a[0]) + (cy - a[1]) * (b[1] - a[1])) / (L * L)))
        s = t * L
    s = min(max(s, min(width_m / 2.0, L / 2.0)), max(L - width_m / 2.0, L / 2.0))
    q = (a[0] + s / L * (b[0] - a[0]), a[1] + s / L * (b[1] - a[1]))
    return i, s, L, q, n, len(hits)


def resolve(instances: list[dict], world_ir: dict, representation_plan: dict | None = None,
            mapping_rows: dict | None = None, explicit_links: dict | None = None,
            slot_half_m: float | None = None, adjudication: dict | None = None) -> dict:
    """explicit_links: annotation id -> place feature id (reviewed bindings).
    slot_half_m: half the widest place front (max_front_width_m / 2).
    adjudication: {"identities": {annotation id: decision}, "placements":
    {business key: placement}} from the manual identity authoring record
    (authored_observation_positions.json "identities"/"business_placements").
    Photos may identify what a sign shows, but never choose its building or
    facade slot.  An unmapped identity emits only when both were authored
    explicitly; unadjudicated identities remain non-emitting debt."""
    if slot_half_m is None:
        slot_half_m = max_front_width_m(TEMPLATE_ROOT) / 2.0
    adj = (adjudication or {}).get("identities") or {}
    placements = (adjudication or {}).get("placements") or {}
    W = World(world_ir)
    mapping_rows = mapping_rows or {}
    explicit_links = explicit_links or {}
    canon_hosts = _canonical_place_hosts(representation_plan)
    entities, unresolved, consumed, records = [], [], {}, []

    def consume(iid, eid):
        assert iid not in consumed, f"observation {iid} consumed twice"
        consumed[iid] = eid

    # An adjudicated observation is identity evidence whatever its review
    # subject (several reviewed custom identities carry no subject id).
    def _is_ident(i):
        d = _primary_decision(adj, i.get("annotation_ids"))
        return bool(i.get("custom_identity_subject")) or (d is not None and d["kind"] != "station")
    ident_obs = [i for i in instances if _is_ident(i)]
    fixtures = [i for i in instances if not _is_ident(i)]

    # 1. Mapped places claim the observations whose subject names them.
    claims = defaultdict(list)
    link_basis = {}
    free = []
    place_ids = {p["place_id"] for p in W.places}
    business_obs = defaultdict(list)
    for inst in list(ident_obs):
        d = _primary_decision(adj, inst.get("annotation_ids"))
        if d is None or d["kind"] == "station":
            continue  # unadjudicated, or the station assembly's name match below
        ident_obs.remove(inst)
        if d["kind"] == "mapped_place" and d.get("place_feature_id") in place_ids:
            claims[d["place_feature_id"]].append(inst)
            link_basis[inst["instance_id"]] = "visual_adjudication:" + d["decision_id"]
        elif d["kind"] == "business":
            business_obs[d["business_key"]].append(inst)
        else:
            # MANUAL-FIXTURE-01: a photographed non-identity object the person
            # assigned a catalog module becomes a generic fixture (one per
            # decision; objects sharing one registration are spaced by the
            # person's hand offset), otherwise it stays evidence only.
            fx_decs = [adj[a] for a in inst.get("annotation_ids") or []
                       if a in adj and adj[a].get("fixture_module_id")]
            owner_fx = d.get("owned_by_scene_fixture")
            if owner_fx and owner_fx in {str(f.get("fixture_id")) for f in
                                         (adjudication or {}).get("scene_fixtures") or []}:
                # The photographed object is already an accepted hand-authored
                # scene fixture (MANUAL-FIXTURE-02): this observation is its
                # evidence, not a second object and not debt.
                consume(inst["instance_id"], "entity:fixture:authored:" + str(owner_fx))
            elif fx_decs:
                for k, fd in enumerate(fx_decs):
                    off = fd.get("fixture_offset_m") or [0.0, 0.0]
                    fixtures.append(dict(
                        inst, instance_id=inst["instance_id"] + ("" if k == 0 else "#" + fd["decision_id"]),
                        module_id=fd["fixture_module_id"], custom_identity_subject=None,
                        population_role="generic_fixture", E=inst["E"] + float(off[0]), N=inst["N"] + float(off[1]),
                        annotation_ids=list(fd["annotation_ids"]),
                        manual_fixture_decision=fd["decision_id"], _extra_record=k > 0))
            else:
                unresolved.append({"instance_id": inst["instance_id"],
                                   "reason": "adjudicated %s: %s (%s)" % (d["kind"], d.get("shows"), d["decision_id"])})
    for inst in ident_obs:
        bound = next((explicit_links[a] for a in inst.get("annotation_ids") or []
                      if explicit_links.get(a) in place_ids), None)
        if bound:
            claims[bound].append(inst); link_basis[inst["instance_id"]] = "explicit_binding"
            continue
        st = tokens(inst["custom_identity_subject"])
        best = max(((identity_match(n, st), p["place_id"]) for p in W.places for n in p["names"]),
                   default=(0, None))
        if best[0]:
            claims[best[1]].append(inst); link_basis[inst["instance_id"]] = "name_token_candidate"
        else:
            free.append(inst)
    supporting_ids = set()

    def _crop_ok(a, inst, place_id):
        if explicit_links.get(a) != place_id:
            return False
        d = adj.get(a)
        if d is not None:
            return (d["kind"] == "mapped_place" and d.get("place_feature_id") == place_id
                    and d.get("role") == "logo")
        return (inst["instance_id"] in supporting_ids
                and link_basis.get(inst["instance_id"]) == "explicit_binding")

    for p in W.places:
        # Host is resolved independently from the source node (containment,
        # else clearly-nearest). The representation plan's attachment is only
        # audited against it: a disagreement is a conflict, never adopted.
        host, basis, node_d = W.host_for_point(p["xy"])
        plan_host, plan_basis = canon_hosts.get(p["place_id"], (None, None))
        host_conflict = bool(plan_host and host and plan_host != host)
        eid = "entity:" + p["place_id"]
        support, contest = [], []
        for inst in claims.get(p["place_id"], []):
            if p["transit"]:
                support.append(inst)  # the station assembly owns transit identity
                continue
            d = ring_distance((inst["E"], inst["N"]), W.rings[host]) if host else math.inf
            (support if d <= _unc(inst) else contest).append(
                inst | {"_host_distance_m": d})
        for inst in support + contest:
            consume(inst["instance_id"], eid)
        supporting_ids.update(i["instance_id"] for i in support)
        lvl = p["tags"].get("level")
        entities.append({
            "entity_id": eid, "kind": "mapped_place", "place_id": p["place_id"],
            # The station assembly locates a transit place from its own
            # accepted reconstruction; every other mapped place needs a host.
            "state": ("entity" if p["transit"] else
                      "debt:host_conflict_with_plan_attachment" if host_conflict else
                      "entity" if host else "debt:host_ambiguous"),
            "plan_attachment_host": {"building_id": plan_host, "basis": plan_basis,
                                     "agrees": None if not plan_host else not host_conflict},
            "identity": {"names": [" ".join(n) for n in p["names"]],
                         "category": next((f"{k}={p['tags'][k]}" for k in IDENTITY_KEYS
                                           if k in p["tags"]), None)},
            "owner": "station_assembly" if p["transit"] else "place_fronts",
            "host": {"building_id": host, "state": "resolved" if host else "ambiguous",
                     "basis": basis, "node_distance_m": None if node_d is None else round(node_d, 3)},
            "level": ({"value": lvl, "state": "observed", "basis": "OSM level tag"} if lvl
                      else {"value": None, "state": "unknown", "basis": "no OSM level tag"}),
            "observations": {
                "supporting": sorted(i["instance_id"] for i in support),
                "contesting": sorted(i["instance_id"] for i in contest),
                "link_basis": {i["instance_id"]: link_basis.get(i["instance_id"])
                               for i in support + contest},
            },
            "host_contested": bool(contest) and not support and not p["transit"],
            # A crop may present the place only when it is explicitly bound to
            # it AND either the visual adjudication says the crop shows this
            # place as its logo (identity is read from the photo; the place's
            # position stays the map's), or, unadjudicated, its observation
            # supports the entity's host.
            "crop_eligible_observations": sorted({
                i["instance_id"] for i in support + contest
                for a in i.get("annotation_ids") or []
                if _crop_ok(a, i, p["place_id"])}),
            "crop_eligible_annotations": sorted({
                a for i in support + contest for a in i.get("annotation_ids") or []
                if _crop_ok(a, i, p["place_id"])} | {
                # MANUAL-WORLD-03: an adjudicated logo explicitly bound to this
                # place keeps presenting it even though registration withholds
                # the annotation (no observation instance carries it).
                a for a, dd in adj.items()
                if dd["kind"] == "mapped_place" and dd.get("place_feature_id") == p["place_id"]
                and dd.get("role") == "logo" and explicit_links.get(a) == p["place_id"]}),
            "adjudicated_annotations": sorted(
                a for a, d in adj.items()
                if d["kind"] == "mapped_place" and d.get("place_feature_id") == p["place_id"]),
            "emits_population_record": False,
        })

    # 2. Shop-sign sightings that no mapped place NAMES (SIGN-FIRST-01).
    #    Identity order: mapped places first. A mapped place owns a street
    #    slot on its attached host face (attachment point +/- the widest
    #    front a profile may fit); an unnamed sign sighting that lands in
    #    that slot is evidence FOR that place and creates nothing. Only the
    #    remaining sightings may stand for an unmapped shop, and they are
    #    merged by WHERE THEY ARE on the face (overlap within combined
    #    uncertainty), never by the per-photo subject label, so several
    #    photos of one sign become one sign.
    mapped_by_place = {e["place_id"]: e for e in entities if e["kind"] == "mapped_place"}
    plan_faces = _canonical_place_faces(representation_plan)
    slots = defaultdict(list)  # (host, face_index) -> [(along_m, place_id)]
    for e in mapped_by_place.values():
        host = (e.get("host") or {}).get("building_id")
        if e["state"] != "entity" or not host or e["owner"] != "place_fronts":
            continue
        ring = W.rings[host]
        pf = plan_faces.get(e["place_id"])
        if pf and pf[0] == host and 0 <= pf[1] < len(ring) - 1:
            a, b = ring[pf[1]], ring[pf[1] + 1]
            slots[(host, pf[1])].append((pf[2] * math.dist(a, b), e["place_id"], "plan_attachment"))
        else:
            fi, s, _L, _q, _n, _d = face_projection(ring, next(p["xy"] for p in W.places
                                                              if p["place_id"] == e["place_id"]))
            slots[(host, fi)].append((s, e["place_id"], "node_projection"))
    # 2a. Manually adjudicated unmapped businesses (ID-ADJ-01): one entity per
    #     business, never per photo.  A public address and the canonical map
    #     are used by the author to record an exact host building and facade
    #     slot.  This resolver validates that record; it does not infer either
    #     value from geocodes, observation positions, or camera rays.
    for key in sorted(set(business_obs)
                      | {d["business_key"] for d in adj.values() if d["kind"] == "business"}
                      | set(placements)):
        members = business_obs.get(key, [])
        anns = sorted(a for a, d in adj.items() if d["kind"] == "business" and d["business_key"] == key)
        logo = next((a for a in anns if adj[a].get("role") == "logo"), anns[0] if anns else None)
        eid = "entity:business:" + key
        ids = sorted(m["instance_id"] for m in members)
        for m in members:
            consume(m["instance_id"], eid)
        pl = placements.get(key) or {}
        host = pl.get("host_building_id")
        host_basis = pl.get("basis")
        authored_captures = sorted({str(c) for c in pl.get("source_capture_ids", [])})
        ent = {"entity_id": eid, "kind": "observed_place", "presentation": "storefront_sign",
               "identity": {"subject": key, "business_key": key, "reference": "visual_adjudication",
                            "shows": [adj[a]["shows"] for a in anns]
                                     + ([str(pl["shows"])] if pl.get("shows") else [])},
               "owner": None, "level": {"value": None, "state": "unknown", "basis": "not observed"},
               "observations": {"supporting": ids, "contesting": [],
                                "link_basis": {i: "visual_adjudication" for i in ids}},
               "adjudicated_annotations": anns,
               "crop_eligible_observations": [], "crop_eligible_annotations": [logo] if logo else [],
               "emits_population_record": False}
        if not host or host not in W.rings or (not members and not authored_captures):
            ent.update({"state": "debt:business_unplaced",
                        "host": {"building_id": None, "state": "unresolved",
                                 "basis": pl.get("unplaced_reason") or
                                          "no manually authored host building"}})
            entities.append(ent)
            continue
        ring = W.rings[host]
        width = float(pl.get("width_m") or 2.4)
        if (pl.get("face_index") is None or pl.get("along_face_m") is None
                or pl.get("face_host", host) != host):
            ent.update({"state": "debt:business_face_unresolved",
                        "host": {"building_id": host, "state": "resolved",
                                 "basis": host_basis,
                                 "address": (pl.get("address_geocode") or {}).get("address"),
                                 "face_state": "unresolved",
                                 "face_basis": "no manually authored facade slot"}})
            entities.append(ent)
            continue
        try:
            fi = int(pl["face_index"])
            s = float(pl["along_face_m"])
        except (TypeError, ValueError):
            fi, s = -1, -1.0
        if not (0 <= fi < len(ring) - 1):
            ent.update({"state": "debt:business_face_invalid",
                        "host": {"building_id": host, "state": "resolved", "basis": host_basis,
                                 "face_index": pl.get("face_index"),
                                 "face_basis": "authored face index is outside the host ring"}})
            entities.append(ent)
            continue
        a, b = ring[fi], ring[fi + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if not (L > 0 and 0 <= s <= L and 0 < width <= L):
            ent.update({"state": "debt:business_face_invalid",
                        "host": {"building_id": host, "state": "resolved", "basis": host_basis,
                                 "face_index": fi, "along_face_m": s, "face_length_m": L,
                                 "width_m": width,
                                 "face_basis": "authored slot or width is outside the host face"}})
            entities.append(ent)
            continue
        t = s / L
        q = (a[0] + t * dx, a[1] + t * dy)
        area = sum(x[0] * y[1] - y[0] * x[1] for x, y in zip(ring, ring[1:]))
        n = (dy / L, -dx / L) if area > 0 else (-dy / L, dx / L)
        face_basis = pl.get("face_basis") or "manual facade authoring"
        clash = next((pid for (s0, pid, _b) in slots.get((host, fi), [])
                      if abs(s - s0) < slot_half_m + width / 2.0), None)
        ent["host"] = {"building_id": host, "state": "resolved", "basis": host_basis,
                       "address": (pl.get("address_geocode") or {}).get("address"),
                       "face_index": fi, "along_face_m": round(s, 3), "width_m": width,
                       "face_basis": face_basis}
        if clash:
            ent["state"] = "debt:overlaps_mapped_place_slot"
            ent["host"]["overlaps"] = clash
            entities.append(ent)
            continue
        rep = (next((m for m in members if logo in (m.get("annotation_ids") or [])), members[0])
               if members else {
                   "instance_id": "manual-business:" + key,
                   "module_id": None,
                   "custom_identity_subject": "storefront_sign_" + key,
                   "population_role": "custom_identity",
                   "annotation_ids": [],
                   "capture_ids": authored_captures,
                   "confidence": 1.0,
               })
        ent.update({"state": "entity", "owner": "population_custom", "emits_population_record": True,
                    "crop_eligible_observations": [rep["instance_id"]],
                    "fused_position_epsg32654": [round(q[0], 3), round(q[1], 3)]})
        entities.append(ent)
        rec = dict(rep)
        hid = host.rsplit("/", 1)[-1]
        rec.update({
            "E": q[0], "N": q[1], "yaw_deg": math.degrees(math.atan2(n[1], n[0])),
            "custom_identity_subject": "storefront_sign_" + key,
            "carrier_id": int(hid) if host.startswith("feature:building_footprint:way/") and hid.isdigit()
            else rep.get("carrier_id"),
            "position_uncertainty_m": 0.25,
            "position_uncertainty_basis": "manual_authored_building_face_slot",
            "annotation_ids": (([logo] + [a for a in anns if a != logo]) if logo else []),
            "capture_ids": sorted({c for m in members for c in m.get("capture_ids") or []}
                                  | set(authored_captures)),
            "crop_annotation_ids": [logo] if logo else [],
            "entity_id": eid, "entity_kind": "observed_place", "entity_presentation": "storefront_sign",
            "entity_host_id": host, "consumed_instance_ids": ids, "sign_width_m": width,
        })
        if pl.get("logo_crop"):
            ent["logo_crop"] = pl["logo_crop"]
            rec["logo_crop"] = pl["logo_crop"]
        elif logo and adj[logo].get("logo_crop"):
            ent["logo_crop"] = adj[logo]["logo_crop"]
            rec["logo_crop"] = adj[logo]["logo_crop"]
        ent["sign_width_m"] = width
        records.append(rec)

    # Unadjudicated identities are deliberately non-emitting.  Earlier code
    # projected observation points onto nearby faces, merged them, and thereby
    # let photo registration silently author buildings and slots.  Manual map
    # completion requires an explicit identity decision and placement row.
    for inst in free:
        eid = "entity:observed:" + inst["instance_id"]
        consume(inst["instance_id"], eid)
        entities.append({
            "entity_id": eid, "kind": "observed_place",
            "identity": {"subject": inst["custom_identity_subject"],
                         "reference": "unadjudicated"},
            "state": "debt:manual_identity_placement_required", "owner": None,
            "host": {"building_id": None, "state": "unresolved",
                     "basis": "photo position is not building authority"},
            "level": {"value": None, "state": "unknown", "basis": "not observed"},
            "observations": {"supporting": [inst["instance_id"]], "contesting": []},
            "emits_population_record": False,
        })

    # 3. No automatic clustering/placement of other unmapped identities.
    by_subj = defaultdict(list)
    for subj, group in sorted(by_subj.items()):
        parent = list(range(len(group)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for a in range(len(group)):
            for b in range(a + 1, len(group)):
                ga, gb = group[a], group[b]
                if math.hypot(ga["E"] - gb["E"], ga["N"] - gb["N"]) <= _unc(ga) + _unc(gb):
                    parent[find(a)] = find(b)
        clusters = defaultdict(list)
        for i in range(len(group)):
            clusters[find(i)].append(group[i])
        for members in clusters.values():
            w = [1.0 / _unc(m) ** 2 for m in members]
            E = sum(m["E"] * wi for m, wi in zip(members, w)) / sum(w)
            N = sum(m["N"] * wi for m, wi in zip(members, w)) / sum(w)
            u = 1.0 / math.sqrt(sum(w))
            rep = min(members, key=_unc)
            eid = "entity:observed:" + rep["instance_id"]
            h0, nb = W.host_of_observation((E, N))
            r = [h0] if h0 else []
            host_ok = bool(r) and u < nb
            ref_ok, ref_states = _annotation_authority(
                mapping_rows, [a for m in members for a in m.get("annotation_ids") or []])
            # Unresolved unmapped identities are debt, never automatic entities.
            resolved = host_ok and ref_ok
            for m in members:
                consume(m["instance_id"], eid)
            ent = {
                "entity_id": eid, "kind": "observed_place",
                "identity": {"subject": subj,
                             "reference": "resolved" if ref_ok else "unresolved",
                             "review_reference_states": ref_states},
                "state": ("entity" if resolved else
                          "debt:identity_reference_unresolved" if host_ok else
                          "debt:host_ambiguous"),
                "owner": "population_custom" if resolved else None,
                "host": {"building_id": r[0] if r else None,
                         "state": "resolved" if host_ok else "ambiguous",
                         "basis": ("fused uncertainty below next-building distance" if host_ok
                                   else "fused uncertainty exceeds next-building distance"),
                         "fused_uncertainty_m": round(u, 3),
                         "next_building_distance_m": None if not math.isfinite(nb) else round(nb, 3)},
                "level": {"value": None, "state": "unknown", "basis": "not observed"},
                "observations": {"supporting": sorted(m["instance_id"] for m in members),
                                 "contesting": []},
                "fused_position_epsg32654": [round(E, 3), round(N, 3)],
                "emits_population_record": resolved,
            }
            entities.append(ent)
            if resolved:
                rec = dict(rep)
                rec.update({
                    "E": E, "N": N, "position_uncertainty_m": round(u, 3),
                    "position_uncertainty_basis": "inverse_variance_fusion_of_member_observations",
                    "annotation_ids": sorted({a for m in members for a in m.get("annotation_ids") or []}),
                    "capture_ids": sorted({c for m in members for c in m.get("capture_ids") or []}),
                    "entity_id": eid, "entity_kind": "observed_place",
                    "entity_host_id": r[0], "consumed_instance_ids": ent["observations"]["supporting"],
                })
                records.append(rec)

    # 4. Generic fixtures: one entity per registered cluster (not identity-bearing).
    for inst in fixtures:
        if not inst.get("module_id"):
            unresolved.append({"instance_id": inst["instance_id"],
                               "reason": "no module and no identity subject"})
            continue
        eid = "entity:fixture:" + inst["instance_id"]
        extra = inst.pop("_extra_record", False)
        if not extra:
            consume(inst["instance_id"], eid)
        records.append(dict(inst, entity_id=eid, entity_kind="generic_fixture",
                            consumed_instance_ids=[] if extra else [inst["instance_id"]]))

    # 5. Hand-authored scene fixtures (MANUAL-FIXTURE-02).  Panorama observations are evidence,
    #    not placement authority.  Only an explicitly accepted manual review may provide the final
    #    module, point, carrier and orientation.  The resolver must not infer a carrier by proximity.
    for fx in (adjudication or {}).get("scene_fixture_debt") or []:
        unresolved.append({
            "instance_id": fx.get("fixture_id", "SF-unknown"),
            "reason": fx.get("reason", "authored fixture is pending manual review"),
        })
    for fx in (adjudication or {}).get("scene_fixtures") or []:
        review = fx.get("manual_review") or {}
        required = ("fixture_id", "module_id", "E", "N", "carrier_id", "yaw_deg",
                    "position_uncertainty_m")
        missing = [k for k in required if fx.get(k) is None]
        missing += ["manual_review." + k for k in
                    ("reviewed_by", "reviewed_on", "module_basis", "position_basis",
                     "carrier_basis", "orientation_basis") if not review.get(k)]
        if missing:
            unresolved.append({
                "instance_id": fx.get("fixture_id", "SF-unknown"),
                "reason": "accepted manual fixture record lacks " + ", ".join(missing),
            })
            continue
        mod = str(fx["module_id"])
        p = (float(fx["E"]), float(fx["N"]))
        run_length_m = fx.get("run_length_m")
        if run_length_m is not None:
            try:
                run_length_m = float(run_length_m)
            except (TypeError, ValueError):
                run_length_m = 0.0
            if not math.isfinite(run_length_m) or run_length_m < 1.0:
                unresolved.append({
                    "instance_id": fx["fixture_id"],
                    "reason": "manual fixture run_length_m must be a finite value >= 1.0",
                })
                continue
        carrier_id = str(fx["carrier_id"])
        best = None
        for z in (adjudication or {}).get("zones") or []:
            if str(z.get("carrier_id")) != carrier_id:
                continue
            pl = z.get("polyline_epsg32654") or []
            for a, b in zip(pl, pl[1:]):
                dd = _seg(p, a, b)
                if best is None or dd < best[0]:
                    best = (dd, z["carrier_id"], a, b)
        if best is None:
            unresolved.append({"instance_id": fx["fixture_id"],
                               "reason": "manually selected carrier is absent from placement zones: " + carrier_id})
            continue
        cid = best[1]
        record = {
            "instance_id": "authored-" + fx["fixture_id"], "module_id": mod,
            "variant": fx.get("variant"),
            "population_role": "generic_fixture", "E": p[0], "N": p[1], "height_m": fx.get("height_m"),
            "yaw_deg": float(fx["yaw_deg"]),
            "carrier_id": int(cid) if str(cid).isdigit() else cid,
            "anchor_segment": {"E0": best[2][0], "N0": best[2][1], "E1": best[3][0], "N1": best[3][1]},
            "anchored": True, "annotation_ids": [],
            "capture_ids": sorted({s["capture_id"] for s in fx.get("sightings") or []
                                   if not str(s.get("disposition", "")).startswith("excluded")}),
            "confidence": float(fx.get("confidence", 0.8)),
            "position_uncertainty_m": float(fx["position_uncertainty_m"]),
            "position_uncertainty_basis": review["position_basis"],
            "entity_id": "entity:fixture:authored:" + fx["fixture_id"], "entity_kind": "generic_fixture",
            "consumed_instance_ids": [], "authored_fixture_id": fx["fixture_id"],
            "manual_review": review}
        if run_length_m is not None:
            record["run_length_m"] = run_length_m
        records.append(record)

    n = len(instances)
    assert len(consumed) + len(unresolved) - sum(
        1 for u in unresolved if str(u["instance_id"]).startswith("SF-")) == n, \
        "every observation is consumed or unresolved"
    counts = defaultdict(int)
    for e in entities:
        counts[e["kind"] + ":" + e["state"]] += 1
    return {
        "schema": SCHEMA,
        "conservation": {"observations": n, "consumed": len(consumed),
                         "unresolved": len(unresolved)},
        "counts": dict(sorted(counts.items())),
        "entities": entities, "unresolved": unresolved,
        "observation_to_entity": dict(sorted(consumed.items())),
        "population_records": records,
        "rejected_scene_fixtures": list((adjudication or {}).get("scene_fixture_rejections") or []),
    }


def load_mapping(path: Path | None) -> dict:
    rows = {}
    if path and Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[str(r["annotation_id"])] = r
    return rows


def load_explicit_links(path: Path | None) -> dict:
    """annotation id -> place feature id from reviewed place bindings."""
    if not path or not Path(path).is_file():
        return {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return {str(b["annotation_id"]): str(b["place_feature_id"])
            for b in doc.get("bindings", []) if b.get("annotation_id") and b.get("place_feature_id")}


def _with_zones(adj: dict, registration_dir: Path) -> dict:
    """Placement-zone carriers (for hand-authored scene fixtures' carrier choice)."""
    zp = Path(registration_dir) / "placement_zones.json"
    if adj and zp.is_file():
        adj = dict(adj, zones=json.loads(zp.read_text(encoding="utf-8")))
    return adj


def load_adjudication(path: Path | None) -> dict:
    """Visual identity adjudication from the registration-stage authored file
    ("identities" per annotation, "business_placements" per business)."""
    if not path or not Path(path).is_file():
        return {}
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = {}
    for d in doc.get("identities", []) or []:
        for a in d.get("annotation_ids") or []:
            ids[str(a)] = d
    fx_path = Path(path).parent / "authored_scene_fixtures.json"
    scene = json.loads(fx_path.read_text(encoding="utf-8")).get("fixtures", []) if fx_path.is_file() else []
    accepted = []
    debt = []
    rejected = []
    for fx in scene:
        review = fx.get("manual_review") or {}
        if fx.get("emission") == "authored_scene_mesh":
            # FL-4876 AUTHORED-SCENE-MESH-01: terrain-grounded scenery with no carrier (the temple graveyard). The
            # building bake emits it from this same owner file; the population planner never sees it.
            continue
        if review.get("status") == "accepted":
            accepted.append(fx)
        elif review.get("status") == "rejected" and review.get("reviewed_by") and review.get("reject_reason"):
            # A signed manual rejection is a decision (duplicate, wrong object, unplaceable), not debt.
            rejected.append({"fixture_id": fx.get("fixture_id", "SF-unknown"),
                             "reject_reason": review["reject_reason"], "reviewed_by": review["reviewed_by"]})
        else:
            debt.append({"fixture_id": fx.get("fixture_id", "SF-unknown"),
                         "reason": "projected fixture candidate is not manually accepted"})
    return {"identities": ids, "scene_fixtures": accepted, "scene_fixture_debt": debt,
            "scene_fixture_rejections": rejected,
            "placements": {p["business_key"]: p for p in doc.get("business_placements", []) or []},
            "mapped_addresses": {a["place_feature_id"]: a for a in doc.get("mapped_place_addresses", []) or []}}


def write(instances_path: Path, world_ir_path: Path, out_dir: Path,
          representation_plan_path: Path | None = None, mapping_path: Path | None = None,
          explicit_links_path: Path | None = None, adjudication_path: Path | None = None) -> dict:
    instances = json.loads(Path(instances_path).read_text(encoding="utf-8"))
    world = json.loads(Path(world_ir_path).read_text(encoding="utf-8"))
    rep = (json.loads(Path(representation_plan_path).read_text(encoding="utf-8"))
           if representation_plan_path else None)
    doc = resolve(instances, world, rep, load_mapping(mapping_path),
                  load_explicit_links(explicit_links_path),
                  adjudication=_with_zones(load_adjudication(adjudication_path), Path(instances_path).parent))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = doc.pop("population_records")
    (out_dir / "identity_entities.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "entity_instances.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    doc["population_records"] = len(records)
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instances", required=True)
    ap.add_argument("--world-ir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--representation-plan")
    ap.add_argument("--mapping", help="reviewed observation mapping ledger (jsonl)")
    ap.add_argument("--explicit-links", help="reviewed place bindings (annotation -> place)")
    ap.add_argument("--adjudication", help="visual identity adjudication (authored_observation_positions.json)")
    a = ap.parse_args(argv)
    opt = lambda v: Path(v) if v else None
    d = write(Path(a.instances), Path(a.world_ir), Path(a.out_dir),
              opt(a.representation_plan), opt(a.mapping), opt(a.explicit_links), opt(a.adjudication))
    print("identity entities", d["counts"], "conservation", d["conservation"],
          "population records", d["population_records"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
