#!/usr/bin/env python3
"""Stamp route-derived importance on EVERY canonical feature (FL-4809 R1).

WHY THIS EXISTS
===============
``importance`` {background, route_visible, landmark, gameplay_critical} is the
level-of-information-need axis: how much information does this feature owe the
player, given where the player walks.  The 30 m route-visibility test that
decides it already exists -- and it never sees a building.

The reason is a five-link chain, each link verified:

1. ``world_ir_ingest_osm_extract._triage_group`` branches only on ``place`` and
   ``fixture``; every other semantic class, ``building_footprint`` included,
   reaches the terminal generic return, so all 650 OSM buildings are group A
   with ``debt_owed`` null.
2. ``compile_observation_debt`` scopes itself to the 17 B/C features -- its own
   docstring says "A features are out of scope".
3. That same stage is where importance is assigned.
4. ``compile_representation_plan.importance_of`` returns the debt row's
   importance, defaulting to ``background`` when there is no row.
5. So every one of the 733 buildings is ``background``, four stages before any
   facade gate is consulted.

This stage breaks link 4 by computing importance for ALL canonical features
from the route itself, independently of which triage group a feature landed in.

WHAT IT DOES NOT DO -- AND A CORRECTION
=======================================
It decides no representation and emits no geometry.  It answers one question
per feature and records the measured distance that justifies the answer.
Consumers decide what to do with it.

Two claims made here earlier were wrong.  Both are retracted, and the retraction
is the point of this section: each one overstated how far this receipt reaches,
and a reader who believes either will look for a visual change that cannot come
from this stage.

RETRACTED 1 -- "this stage makes a building facade REACHABLE; with importance
stuck at ``background`` no facade can be selected."  False.
``compile_representation_plan`` emits one secondary ``mesh_facade`` row for
every canonical building mass UNCONDITIONALLY.  Facade eligibility never
consulted importance, so importance was never what suppressed it.

RETRACTED 2 -- "what importance reaches is the reconstruction class and the
debt policy."  The reconstruction-class half is false, and measured to be
false: the same lane inputs planned with and without this receipt produce the
identical class split, A 650 / B 83, pinned by
``test_real_planner_does_not_change_reconstruction_class``.  No rule consults
importance when assigning a class.

WHAT IT ACTUALLY REACHES
========================
Exactly two things, and nothing else today:

* the ``importance`` field written on each representation row;
* the branches that already read that field -- ``debt_policy_key_for``, which
  selects ``background_A`` against ``route_visible_B``/``route_visible_C``, and
  the R-STREET-OMIT branch, which may drop an unresolved street sighting only
  while it is background.

It does not change building eligibility, reconstruction class, facade asset
selection, facade detail, or any prioritisation order.  No stage reads this
receipt to decide WHICH asset a building gets; ``dispatch_population_plan``
copies representation identity, disposition and owner, and no importance.  A
consumer that wants importance to steer quality must still be written.

Stating it precisely matters because the difference is the difference between
"this stage unblocks the barren map" (it does not) and "this stage stops every
building claiming it owes the player nothing" (it does).

OWNERSHIP OF THE LAW
====================
The primitives stay single-owned by ``compile_observation_debt``: the route
polyline decoder, the point-segment distance, the name normaliser and
``ROUTE_VISIBLE_DISTANCE_M`` are imported from it rather than restated, so the
two stages cannot drift on the law itself -- only on scope, which is the whole
point.  Once a consumer reads this receipt, ``compile_observation_debt`` should
take its 17 rows' importance from here too, leaving one owner.

POLYGONS ARE MEASURED AS POLYGONS
=================================
A building is route-visible when any part of it comes within the threshold, not
when its centroid does.  A 40 m frontage on the route with a centroid 35 m away
is route-visible, and centroid-only sampling would miss it.  So the distance is
the minimum over both directions: every geometry vertex against every route
segment, and every route point against every geometry edge.  A bounding-box
prefilter keeps that affordable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.compile_observation_debt import (  # noqa: E402
    ROUTE_VISIBLE_DISTANCE_M,
    _decode_polyline6,
    _norm,
    _pt_seg_dist,
)
from scripts.pipeline.representation_plan_provenance import (  # noqa: E402
    # The same primitive the representation-plan receipt uses to pin THIS
    # file. One hashing law from producer inputs through to consumer pin.
    file_sha256,
)

SCHEMA = "y92.route_importance.v1"

# The declared level-of-information-need ladder. A value outside it must not
# reach a consumer: compile_representation_plan.debt_policy_key_for treats
# anything that is not "background" as promoted, so an unrecognised string --
# a typo, a tier from a future schema -- would silently select the blocking
# route_visible_C policy for every feature carrying it.
IMPORTANCE_TIERS = ("background", "route_visible", "landmark",
                    "gameplay_critical")

# Receipts produced by a CLI run are copied here so a measurement cannot exist
# only in a gitignored run directory. Content-addressed, never overwritten.
PRESERVED_DIR = REPO_ROOT / "artifacts/experiments/fl4809_route_importance"

# A feature is measured against the route only when its bounding box is within
# this margin of the route's bounding box. The margin is the visibility
# threshold plus slack, so the prefilter can never exclude a feature that the
# exact test would have accepted.
BBOX_MARGIN_M = ROUTE_VISIBLE_DISTANCE_M * 2.0


def _iter_positions(geometry):
    """Yield every (east, north) in a canonical geometry, whatever its nesting.

    Canonical geometries carry ``coordinates`` already projected into the run's
    UTM frame, as points, rings, or rings-of-rings depending on the class. This
    walks the nesting rather than assuming one shape, so a class added later
    does not silently contribute zero positions and read as background.
    """
    if not isinstance(geometry, dict):
        return
    stack = [geometry.get("coordinates")]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        if (isinstance(node, (list, tuple)) and len(node) == 2
                and all(isinstance(v, (int, float)) for v in node)):
            yield float(node[0]), float(node[1])
            continue
        if isinstance(node, (list, tuple)):
            stack.extend(node)


def _min_distance_to_route(positions, route_pts, route_bbox):
    """Minimum distance in metres from a geometry to the route polyline.

    Returns ``(distance, is_lower_bound)``, or ``None`` when the geometry
    carries no positions. Measured in both directions because a long edge can
    pass close to the route while both of its endpoints are far from it.
    """
    if not positions:
        return None
    east = [p[0] for p in positions]
    north = [p[1] for p in positions]
    # Prefiltered features still get a number. Returning a bare "too far"
    # would collapse into the same null a geometryless feature reports, and a
    # consumer could not tell "measured, distant" from "never measured".
    gap_east = max(route_bbox[0] - max(east), min(east) - route_bbox[2], 0.0)
    gap_north = max(route_bbox[1] - max(north), min(north) - route_bbox[3], 0.0)
    if gap_east > BBOX_MARGIN_M or gap_north > BBOX_MARGIN_M:
        return (math.hypot(gap_east, gap_north), True)

    best = float("inf")
    for px, py in positions:
        for (ax, ay), (bx, by) in zip(route_pts, route_pts[1:]):
            distance = _pt_seg_dist(px, py, ax, ay, bx, by)
            if distance < best:
                best = distance
                if best == 0.0:
                    return (0.0, False)
    if len(positions) > 1:
        for rx, ry in route_pts:
            for (ax, ay), (bx, by) in zip(positions, positions[1:]):
                distance = _pt_seg_dist(rx, ry, ax, ay, bx, by)
                if distance < best:
                    best = distance
                    if best == 0.0:
                        return (0.0, False)
    return (best, False)


def _feature_names(feature):
    """Normalised name strings a landmark entry could match."""
    names = set()
    properties = feature.get("properties") or {}
    for key in ("name", "name:en", "label", "brand", "operator"):
        value = properties.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        normed = _norm(value if isinstance(value, str) else None)
        if normed:
            names.add(normed)
    return names


class RouteImportanceInvalid(ValueError):
    """A route-importance receipt a consumer must not read.

    Raised rather than degraded because every failure this checks for is
    indistinguishable, downstream, from a legitimate ``background``: a wrong
    schema, a duplicated feature id whose losing row is dropped, a feature the
    receipt never scored. Each would read as "this feature owes the player
    nothing", which is the exact silent answer FL-4809 R1 exists to remove.
    """


def load_route_importance(path, *, required_feature_ids=None) -> dict:
    """Read a receipt into ``{feature_id: importance}``, or fail closed.

    ``required_feature_ids`` is the consumer's own canonical feature set. When
    supplied, the receipt's scored set must equal it EXACTLY -- both
    directions are refused, for two different reasons.

    A missing id is the silent one: a receipt compiled from a different
    canonical IR answers ``background`` by omission for the features only
    this run knows about, which is the exact default this stage exists to
    remove, and the content-hash pin cannot catch it when the caller forgets
    to pin the input.

    An unexpected id is not silent -- nothing reads it -- but it is proof the
    receipt was compiled against a different denominator, and therefore that
    the scores this run DOES read were measured in a world this run is not
    reproducing. The shared ids may carry a different geometry, a different
    route, or a different landmark set behind the same strings. Accepting the
    overlap would be accepting a measurement whose provenance has already
    been contradicted.
    """
    source = Path(path)
    try:
        doc = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RouteImportanceInvalid(
            f"route importance receipt unreadable: {source}: {exc}") from exc
    if not isinstance(doc, dict):
        raise RouteImportanceInvalid(
            f"route importance receipt is not an object: {source}")
    schema = doc.get("schema")
    if schema != SCHEMA:
        raise RouteImportanceInvalid(
            f"route importance receipt {source} declares schema "
            f"{schema!r}, expected {SCHEMA!r}")
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RouteImportanceInvalid(
            f"route importance receipt {source} carries no rows")

    table: dict[str, str] = {}
    duplicates: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RouteImportanceInvalid(
                f"route importance receipt {source} row {index} "
                "is not an object")
        fid = row.get("feature_id")
        if not fid:
            raise RouteImportanceInvalid(
                f"route importance receipt {source} row {index} "
                "carries no feature_id")
        fid = str(fid)
        importance = str(row.get("importance") or "background")
        if importance not in IMPORTANCE_TIERS:
            raise RouteImportanceInvalid(
                f"route importance receipt {source} scores {fid} as "
                f"{importance!r}, which is not one of {IMPORTANCE_TIERS}")
        if fid in table:
            # Rejected whether or not the two rows agree. A repeated feature
            # id means the receipt's producer lost track of its denominator,
            # and a consumer keying by feature id cannot tell which row won.
            duplicates.append(fid)
        table[fid] = importance
    if duplicates:
        shown = ", ".join(sorted(set(duplicates))[:5])
        raise RouteImportanceInvalid(
            f"route importance receipt {source} scores "
            f"{len(set(duplicates))} feature(s) more than once: {shown}")

    if required_feature_ids is not None:
        required = set(required_feature_ids)
        scored = set(table)
        missing = sorted(required - scored)
        unexpected = sorted(scored - required)
        if missing or unexpected:
            parts = []
            if missing:
                parts.append(f"{len(missing)} unscored, first: "
                             + ", ".join(missing[:5]))
            if unexpected:
                parts.append(f"{len(unexpected)} unexpected, first: "
                             + ", ".join(unexpected[:5]))
            raise RouteImportanceInvalid(
                f"route importance receipt {source} scores {len(scored)} "
                f"features and the consumer carries {len(required)}; "
                + "; ".join(parts))
    return table


def preserve_receipt(output: Path) -> Path:
    """Copy a produced receipt into a tracked, content-addressed location.

    ``.run/`` is gitignored, so a receipt written only there is evidence that
    disappears. This runs inside the producing execution, before the command
    reports success, and refuses to overwrite preserved bytes with different
    ones.
    """
    payload = output.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    preserved = PRESERVED_DIR / "preserved" / f"{digest[:16]}.route_importance.json"
    preserved.parent.mkdir(parents=True, exist_ok=True)
    if preserved.is_file():
        if preserved.read_bytes() != payload:
            raise RuntimeError(
                "preserved route-importance evidence disagrees with this run "
                f"at the same content address: {preserved}")
        return preserved
    preserved.write_bytes(payload)
    return preserved


def compile_route_importance(world_ir: Path, route: Path, landmarks: Path,
                             output: Path) -> dict:
    """Write one importance row per canonical feature, with its evidence."""
    ir = json.loads(world_ir.read_text(encoding="utf-8"))
    features = ir["canonical_features"]

    landmark_doc = json.loads(landmarks.read_text(encoding="utf-8"))
    landmark_texts = set()
    for entry in landmark_doc.get("landmarks", []):
        for key in ("landmark_id", "name", "source_ref",
                    "aoi_lobe_feature_id"):
            normed = _norm(entry.get(key))
            if normed:
                landmark_texts.add(normed)

    route_doc = json.loads(route.read_text(encoding="utf-8"))
    route_pts = [GF.utm_forward(lon, lat) for lon, lat
                 in _decode_polyline6(route_doc["trip"]["legs"][0]["shape"])]
    if len(route_pts) < 2:
        raise ValueError("route shape decoded to fewer than two points")
    route_bbox = (min(p[0] for p in route_pts), min(p[1] for p in route_pts),
                  max(p[0] for p in route_pts), max(p[1] for p in route_pts))

    rows = []
    for feature in features:
        fid = feature.get("feature_id")
        positions = list(_iter_positions(feature.get("geometry")))
        names = _feature_names(feature)
        measured = _min_distance_to_route(positions, route_pts, route_bbox)
        distance = None if measured is None else measured[0]
        is_lower_bound = bool(measured and measured[1])

        if names & landmark_texts:
            importance, basis = "landmark", "name matches required_landmarks"
        elif distance is None:
            importance, basis = "background", "feature carries no geometry"
        elif distance <= ROUTE_VISIBLE_DISTANCE_M:
            importance, basis = "route_visible", (
                f"within {ROUTE_VISIBLE_DISTANCE_M} m of the pedestrian route")
        else:
            importance, basis = "background", (
                f"beyond {ROUTE_VISIBLE_DISTANCE_M} m of the pedestrian route")

        rows.append({
            "feature_id": fid,
            "semantic_class": feature.get("semantic_class"),
            "importance": importance,
            "basis": basis,
            "route_distance_m": (None if distance is None
                                 else round(distance, 3)),
            "route_distance_is_lower_bound": is_lower_bound,
            "positions_measured": len(positions),
        })

    counts: dict[str, int] = {}
    by_class: dict[str, dict[str, int]] = {}
    for row in rows:
        counts[row["importance"]] = counts.get(row["importance"], 0) + 1
        cls = str(row["semantic_class"])
        by_class.setdefault(cls, {})
        by_class[cls][row["importance"]] = \
            by_class[cls].get(row["importance"], 0) + 1

    receipt = {
        "schema": SCHEMA,
        "counts": {"features": len(rows), "by_importance": counts,
                   "by_semantic_class": by_class},
        "law": {
            "route_visible_distance_m": ROUTE_VISIBLE_DISTANCE_M,
            "source": ("primitives imported from compile_observation_debt so "
                       "the two stages cannot drift on the law, only on scope"),
            "measurement": ("minimum over geometry vertices against route "
                            "segments and route points against geometry edges"),
        },
        # Paths are labels for a human; the hashes are the provenance.
        # world_ir lives under gitignored .run/, so a path alone names a file
        # that can vanish and cannot be checked when it does. The landmark
        # document in particular is the sole source of every "landmark" row
        # here, and nothing downstream re-derives it -- without its hash the
        # landmark tier would rest on an unverifiable claim.
        "inputs": {
            "world_ir": {"path": str(world_ir),
                         "sha256": file_sha256(world_ir)},
            "route": {"path": str(route), "sha256": file_sha256(route)},
            "landmarks": {"path": str(landmarks),
                          "sha256": file_sha256(landmarks)},
        },
        "rows": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--world-ir", type=Path, required=True)
    ap.add_argument("--route", type=Path, required=True)
    ap.add_argument("--landmarks", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args(argv)

    receipt = compile_route_importance(
        args.world_ir, args.route, args.landmarks, args.output)
    counts = receipt["counts"]
    print(f"features {counts['features']}  {counts['by_importance']}")
    for cls, dist in sorted(counts["by_semantic_class"].items()):
        print(f"  {cls:<22} {dist}")
    print(f"wrote {args.output}")
    # Failure to preserve a produced measurement is a run failure, not a
    # warning: the next reader must be able to find these counts after the
    # run directory is gone.
    preserved = preserve_receipt(args.output)
    print(f"preserved {preserved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
