#!/usr/bin/env python3
"""Compile one feature ledger joining every stage's truth per feature (FL-4809 LEDGER-01).

One row per World IR feature (812) plus one row per unmapped element (5),
each with eight classification axes:

- reconstruction_class (A|B|C from the triage table; unmapped elements are
  B: vocabulary/template work is owed for them);
- importance (observation debt when present, else the route-distance rule
  reused from compile_observation_debt by import, else background);
- evidence_state (canonical-entity height/footprint claims for buildings;
  directly_observed when a debt frame or box observes it; derived for
  OSM-tags-only features; unknown for unmapped elements);
- debt_state (waiver > observation_owed > reconstruction_owed >
  representation_owed > complete, in pipeline order);
- visual_owner (mesh for fixture/infrastructure/front emissions, decal for
  surface-stamped features, terrain for baked masses and ground, none when
  no lowerer emitted it);
- collision_owner (terrain for baked masses and stamped surfaces, none
  otherwise; the 5 mesh-collision masses in building_mass_receipt are not
  per-feature attributable, so they read terrain with the mass present);
- navigation_owner (terrain for walkable stamped surfaces, none otherwise,
  per the route-walkability authority of DEM ground + Valhalla route);
- disposition (conflate disposition for the source object, retained_unmatched
  for IR features the conflator never saw, excluded_with_reason for unmapped
  elements carrying their rule gap).

Every axis value names its backing receipt in stage_refs. Deterministic:
sorted iteration, rounded floats, sorted-key JSON, no randomness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline import compile_observation_debt as OD  # noqa: E402

SCHEMA = "y92.feature_ledger.v1"

AXES = (
    "reconstruction_class",
    "importance",
    "evidence_state",
    "debt_state",
    "visual_owner",
    "collision_owner",
    "navigation_owner",
    "disposition",
)

RULES = {
    "reconstruction_class": "triage_group from triage01/osm_ingest_receipt.json triage.by_group; canonical-only features take the entity's authoritative footprint+height as observed geometry (A); anything else is B with ledger_default_reason (vocabulary/template work owed)",
    "importance": "frames01/observation_debt.json when present, else within 30 m of the Valhalla route (rule reused from compile_observation_debt), else background",
    "evidence_state": "conflate01/canonical_entities.json height_m claim for buildings; directly_observed when a debt frame or box_ref observes it; derived for OSM-tags-only features; unknown for unmapped elements",
    "debt_state": "waiver when waiver_reason present, else observation_owed from the debt file, else reconstruction_owed for B/C without a resolved template/profile, else representation_owed when no lowering receipt emitted the IR feature, else complete",
    "visual_owner": "mesh for fixture/infrastructure/front emissions, decal for surface-stamped features, terrain for baked building masses and ground, none when no lowerer emitted it",
    "collision_owner": "terrain for baked masses and stamped walkable surfaces, none otherwise (the 5 mesh-collision masses are not per-feature attributable)",
    "navigation_owner": "terrain for walkable stamped surfaces per the route-walkability authority (DEM ground, Valhalla route), none otherwise",
    "disposition": "conflate disposition for the source object; retained_unmatched for IR features the conflator never saw; excluded_with_reason with the rule gap for unmapped elements",
}

_MESH_INFRA_CLASSES = {"railway_boundary", "railway_crossing_equipment_proposal"}
_WALKABLE_CLASSES = {"road", "sidewalk", "crossing", "terrain"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _feature_point(geometry: dict) -> tuple[float, float]:
    kind = geometry.get("kind")
    coords = geometry["coordinates"]
    if kind == "point":
        return float(coords[0]), float(coords[1])
    pts = coords if kind == "polyline" else coords
    n = len(pts)
    return sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n


def build(triage_receipt: Path, world_ir: Path, entities: Path,
          debt: Path, route: Path, build_dir: Path,
          generated_at: str) -> tuple[dict, dict]:
    triage = json.loads(triage_receipt.read_text(encoding="utf-8"))
    triage_group = {row["feature_id"]: row["group"]
                    for row in triage["triage"]["by_group"]}
    unmapped = sorted(triage["triage"]["unmapped_with_reason"],
                      key=lambda u: u["source_ref"])

    doc = json.loads(world_ir.read_text(encoding="utf-8"))
    features = sorted(doc["canonical_features"],
                      key=lambda f: f["feature_id"])
    ref_of = {}
    for feat in features:
        fid = feat["feature_id"]
        ref = fid.split(":", 1)[1].split(":", 1)[1] \
            if fid.count(":") >= 2 else fid
        ref_of[fid] = ref

    ent_doc = json.loads(entities.read_text(encoding="utf-8"))
    entity_by_canonical = {e["canonical_id"]: e
                           for e in ent_doc["entities"]}
    disp_by_ref: dict[str, dict] = {}
    for row in ent_doc["dispositions"]:
        if row.get("source") == "osm":
            disp_by_ref[row["source_object_id"]] = row
    # Canonical-only features (PLATEAU-only masses) have no OSM triage
    # row; they resolve through their own entity below.
    disp_by_plateau: dict[str, dict] = {}
    for row in ent_doc["dispositions"]:
        if row.get("source") == "plateau":
            disp_by_plateau[row["source_object_id"]] = row

    debt_doc = json.loads(debt.read_text(encoding="utf-8"))
    debt_by_id = {f["feature_id"]: f for f in debt_doc["features"]}

    route_doc = json.loads(route.read_text(encoding="utf-8"))
    route_pts = [GF.utm_forward(lon, lat) for lon, lat in
                 OD._decode_polyline6(route_doc["trip"]["legs"][0]["shape"])]

    def near_route(pt: tuple[float, float]) -> bool:
        return min(OD._pt_seg_dist(pt[0], pt[1], ax, ay, bx, by)
                   for (ax, ay), (bx, by)
                   in zip(route_pts, route_pts[1:])) \
            <= OD.ROUTE_VISIBLE_DISTANCE_M

    manifest_path = build_dir / "emission_manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"emission manifest is absent: {manifest_path}")
    emission_manifest = json.loads(
        manifest_path.read_text(encoding="utf-8"))
    if emission_manifest.get("errors"):
        raise ValueError("emission manifest contains terminal-state errors")
    inputs_absent = []
    emissions = [
        row for row in emission_manifest.get("emissions", [])
        if row.get("terminal_state") == "emitted" and row.get("feature_id")
    ]
    emitted_by_owner: dict[str, set[str]] = {}
    for emission in emissions:
        emitted_by_owner.setdefault(str(emission.get("owner")), set()).add(
            str(emission["feature_id"]))
    surface_ids = emitted_by_owner.get("surface", set())
    baked_ids = emitted_by_owner.get("building_mass", set())
    physical_ids = set().union(*(
        emitted_by_owner.get(owner, set())
        for owner in ("linear", "fixtures", "custom", "station_facade")))
    resolved_front = physical_ids

    triage_receipt_ref = str(triage_receipt)
    entities_ref = str(entities)
    debt_ref = str(debt)
    route_ref = str(route)
    emission_ref = str(manifest_path)
    surface_ref = emission_ref
    fixture_ref = emission_ref
    infra_ref = emission_ref
    fronts_ref = emission_ref
    bake_ref = emission_ref
    walk_ref = str(build_dir / "route_walkability_receipt.json")

    rows = []
    ledger_claim_rows = 0
    ledger_default_rows = 0
    for feat in features:
        fid = feat["feature_id"]
        cls = feat["semantic_class"]
        label = (feat.get("labels") or {}).get("name:en") \
            or (feat.get("labels") or {}).get("name")
        pt = _feature_point(feat["geometry"])
        ledger_default_reason = None
        if fid in triage_group:
            group = triage_group[fid]
            refs: dict[str, str] = {"reconstruction_class": triage_receipt_ref}
        else:
            # Canonical-only feature (PLATEAU-only mass): the entity's own
            # authoritative footprint+height claims are observed geometry
            # (A), not vocabulary debt. Anything else takes the documented
            # B default with a named reason. Never raises, never silent.
            _disp = disp_by_plateau.get(fid.rsplit("/", 1)[-1], {})
            _ent = entity_by_canonical.get(_disp.get("canonical_id"), {})
            _claims = _ent.get("claims", {}) if isinstance(_ent, dict) else {}
            _fp = _claims.get("footprint", {})
            _ht = _claims.get("height_m", {})
            if (_fp.get("evidence_state") == "authoritative_observed"
                    and _ht.get("evidence_state") == "authoritative_observed"):
                group = "A"
                refs = {"reconstruction_class": entities_ref}
                ledger_claim_rows += 1
            else:
                group = "B"
                ledger_default_reason = (
                    "no triage row and no authoritative footprint+height claims")
                refs = {"reconstruction_class": triage_receipt_ref}
                ledger_default_rows += 1

        debt_row = debt_by_id.get(fid)
        if debt_row is not None:
            importance = debt_row["importance"]
            refs["importance"] = debt_ref
        elif near_route(pt):
            importance = "route_visible"
            refs["importance"] = route_ref
        else:
            importance = "background"
            refs["importance"] = route_ref

        if cls == "building_footprint":
            disp = disp_by_ref.get(ref_of[fid])
            if disp is not None:
                entity = entity_by_canonical.get(disp["canonical_id"], {})
                height = (entity.get("claims") or {}).get("height_m", {})
                evidence = height.get("evidence_state", "unknown")
            else:
                evidence = "unknown"
            refs["evidence_state"] = entities_ref
        elif debt_row is not None and \
                (debt_row.get("frames") or debt_row.get("box_refs")):
            evidence = "directly_observed"
            refs["evidence_state"] = debt_ref
        else:
            evidence = "derived"
            refs["evidence_state"] = triage_receipt_ref

        if debt_row is not None and debt_row.get("waiver_reason"):
            debt_state = "waiver"
            refs["debt_state"] = debt_ref
        elif debt_row is not None and \
                debt_row.get("observation_state") == "observation_owed":
            debt_state = "observation_owed"
            refs["debt_state"] = debt_ref
        elif group in ("B", "C") and fid not in resolved_front:
            debt_state = "reconstruction_owed"
            refs["debt_state"] = fronts_ref
        else:
            debt_state = None

        if fid in physical_ids:
            visual = "mesh"
            refs["visual_owner"] = emission_ref
        elif fid in surface_ids:
            visual = "decal"
            refs["visual_owner"] = surface_ref
        elif cls == "building_footprint" and fid in baked_ids:
            visual = "terrain"
            refs["visual_owner"] = bake_ref
        elif cls == "terrain":
            visual = "terrain"
            refs["visual_owner"] = walk_ref
        else:
            visual = "none"
            refs["visual_owner"] = surface_ref
        if debt_state is None:
            # representation_owed is an absence claim backed by the same
            # emission check as the visual owner; complete is backed by the
            # receipt that owns the feature's pixels.
            debt_state = "representation_owed" if visual == "none" \
                else "complete"
            refs["debt_state"] = refs["visual_owner"]
            if debt_state == "representation_owed" and inputs_absent:
                _absent = ("not_emitted_in_build: %s receipt(s) absent"
                           % ", ".join(sorted({e["stage"] for e in inputs_absent})))
                ledger_default_reason = _absent if ledger_default_reason is None \
                    else ledger_default_reason + "; " + _absent

        if cls == "building_footprint" or \
                (cls in _WALKABLE_CLASSES and fid in surface_ids):
            collision = "terrain"
            refs["collision_owner"] = bake_ref if cls == \
                "building_footprint" else surface_ref
        else:
            collision = "none"
            refs["collision_owner"] = bake_ref

        if cls in _WALKABLE_CLASSES and fid in surface_ids:
            navigation = "terrain"
            refs["navigation_owner"] = walk_ref
        else:
            navigation = "none"
            refs["navigation_owner"] = walk_ref

        disp = disp_by_ref.get(ref_of[fid])
        if disp is not None:
            disposition = disp["disposition"]
            refs["disposition"] = entities_ref
        else:
            disposition = "retained_unmatched"
            refs["disposition"] = entities_ref

        rows.append({
            "feature_id": fid,
            "semantic_class": cls,
            "label": label,
            "reconstruction_class": group,
            "ledger_default_reason": ledger_default_reason,
            "importance": importance,
            "evidence_state": evidence,
            "debt_state": debt_state,
            "visual_owner": visual,
            "collision_owner": collision,
            "navigation_owner": navigation,
            "disposition": disposition,
            "stage_refs": {axis: refs[axis] for axis in AXES},
        })

    for item in unmapped:
        rows.append({
            "feature_id": item["source_ref"],
            "semantic_class": "unmapped",
            "label": item["tags"].get("name:en")
            or item["tags"].get("name"),
            "reconstruction_class": "B",
            "importance": "background",
            "evidence_state": "unknown",
            "debt_state": "reconstruction_owed",
            "visual_owner": "none",
            "collision_owner": "none",
            "navigation_owner": "none",
            "disposition": "excluded_with_reason",
            "stage_refs": {axis: triage_receipt_ref for axis in AXES},
        })

    rows.sort(key=lambda r: r["feature_id"])

    histograms = {axis: {} for axis in AXES}
    for row in rows:
        for axis in AXES:
            bucket = histograms[axis]
            bucket[row[axis]] = bucket.get(row[axis], 0) + 1
    histograms = {axis: dict(sorted(bucket.items()))
                  for axis, bucket in histograms.items()}

    cross_tab: dict[str, dict[str, int]] = {}
    for row in rows:
        cell = cross_tab.setdefault(row["reconstruction_class"], {})
        cell[row["debt_state"]] = cell.get(row["debt_state"], 0) + 1
    cross_tab = {group: dict(sorted(cell.items()))
                 for group, cell in sorted(cross_tab.items())}

    no_visual = sorted(row["feature_id"] for row in rows
                       if row["visual_owner"] == "none")
    unresolved = sorted(row["feature_id"] for row in rows
                        if row["disposition"] == "unresolved")

    document = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "inputs": {
            "triage_receipt": str(triage_receipt),
            "world_ir": str(world_ir),
            "entities": str(entities),
            "debt": str(debt),
            "route": str(route),
            "build_dir": str(build_dir),
        },
        "counts": {"rows": len(rows),
                   "ledger_claim_rows": ledger_claim_rows,
                   "ledger_default_rows": ledger_default_rows},
        "rows": rows,
    }
    receipt = {
        "schema": "y92.feature_ledger_receipt.v1",
        "generated_at": generated_at,
        "inputs": {
            str(path): {"sha256": _sha256(path),
                        "size_bytes": path.stat().st_size}
            for path in (triage_receipt, world_ir, entities, debt,
                         route)},
        "build_dir": str(build_dir),
        "rules": RULES,
        "histograms": histograms,
        "ledger_claim_rows": ledger_claim_rows,
        "ledger_default_rows": ledger_default_rows,
        "cross_tab": cross_tab,
        "unresolved": unresolved,
        "no_visual_owner": no_visual,
    }
    # New keys appear only when something was absent: with all inputs
    # present the receipt is byte-identical to before the change (F1).
    if inputs_absent:
        receipt["inputs_absent"] = inputs_absent
        receipt["representation_owed_rows"] = sum(
            1 for row in rows if row["debt_state"] == "representation_owed")
    return document, receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--triage-receipt", type=Path, required=True)
    ap.add_argument("--world-ir", type=Path, required=True)
    ap.add_argument("--entities", type=Path, required=True)
    ap.add_argument("--debt", type=Path, required=True)
    ap.add_argument("--route", type=Path, required=True)
    ap.add_argument("--build-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--receipt-out", type=Path, required=True)
    ap.add_argument("--generated-at", default="2026-09-19T00:00:00Z")
    args = ap.parse_args(argv)

    document, receipt = build(
        args.triage_receipt, args.world_ir, args.entities, args.debt,
        args.route, args.build_dir, args.generated_at,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n", encoding="utf-8",
    )
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n", encoding="utf-8",
    )
    print(f"[feature-ledger] rows={document['counts']['rows']} "
          f"unresolved={len(receipt['unresolved'])} "
          f"no_visual_owner={len(receipt['no_visual_owner'])}")
    print(f"[feature-ledger] wrote {args.out} and {args.receipt_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
