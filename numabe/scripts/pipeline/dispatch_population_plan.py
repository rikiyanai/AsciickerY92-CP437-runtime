#!/usr/bin/env python3
"""Partition one population plan into exclusive lowering-owner inputs.

The population planner owns which logical placements exist.  This module owns
the only transition from those logical placements to lowering lanes.  A
lowerer never receives the full heterogeneous plan and therefore never has to
classify a placement as "foreign" or "sibling".

Every placement is assigned exactly once to one of:

* ``linear`` -- repeated or continuous public-realm modules;
* ``fixtures`` -- point fixtures and facade/storefront modules;
* ``custom`` -- observed custom identities other than Numabe station;
* ``terminal`` -- a representation decision explicitly omitted the logical
  placement before lowering.

Unknown modules fail here.  They do not disappear in a lowerer skip count.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from scripts.pipeline.population_representation_contract import (
    FIXTURE_DOMAINS,
    custom_plane_contract,
    finalize_linear_realizations,
    fixture_asset_contract,
    linear_asset_contract,
    lowering_owner,
    logical_realizations,
    span_building_index,
)
from scripts.pipeline.canonical_attachment import (
    CanonicalIndex,
    resolve_span_endpoints,
)
from scripts.pipeline.plan_spatial_provenance import resolve_span


SCHEMA = "y92.population_dispatch.v1"
OWNERS = ("linear", "fixtures", "custom", "terminal")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _owner_for(placement: dict, catalog: dict[str, dict]) -> tuple[str, str]:
    disposition = placement.get("population_disposition")
    if isinstance(disposition, dict):
        return "terminal", (
            "population_disposition:"
            + str(disposition.get("category") or "terminal"))
    subject = str(placement.get("custom_identity_subject") or "")
    if subject:
        return "custom", "custom_identity_subject"

    module_id = str(placement.get("module_id") or "")
    if not module_id:
        raise ValueError(
            f"population placement {placement.get('placement_id')} has no module_id "
            "or custom_identity_subject"
        )
    module = catalog.get(module_id)
    if module is None:
        # These are accepted planner vocabulary, not asset fallbacks.  The
        # dispatcher still gives them one explicit owner.
        if module_id in FIXTURE_DOMAINS:
            return "fixtures", f"planner_domain_literal:{module_id}"
        raise ValueError(
            f"population placement {placement.get('placement_id')} names "
            f"uncatalogued module {module_id!r}"
        )
    return lowering_owner(module_id, module)


def partition(plan: dict, catalog_doc: dict, representation_plan: dict,
              rules: dict, canonical_world: dict, *,
              source_plan_sha256: str,
              representation_plan_sha256: str) -> dict[str, dict]:
    catalog_rows = catalog_doc.get("modules", catalog_doc)
    if not isinstance(catalog_rows, list):
        raise ValueError("module catalog must be a list or contain modules[]")
    catalog = {str(row["module_id"]): row for row in catalog_rows}
    representation_rows_by_feature: dict[str, list[dict]] = {}
    for row in representation_plan.get("rows", []):
        if isinstance(row, dict) and row.get("feature_id"):
            representation_rows_by_feature.setdefault(
                str(row["feature_id"]), []).append(row)
    attachment_index = CanonicalIndex(canonical_world)

    owner_carriers: dict[str, list[dict]] = {owner: [] for owner in OWNERS}
    counts: Counter[str] = Counter()
    placement_ids: set[str] = set()

    for carrier in plan.get("carriers", []):
        per_owner: dict[str, list[dict]] = {owner: [] for owner in OWNERS}
        for original in carrier.get("placements", []):
            placement = copy.deepcopy(original)
            placement_id = str(placement.get("placement_id") or "")
            if not placement_id:
                raise ValueError("population placement has no placement_id")
            if placement_id in placement_ids:
                raise ValueError(f"population placement appears twice: {placement_id}")
            placement_ids.add(placement_id)
            module_id = str(
                placement.get("module_id")
                or placement.get("custom_identity_subject")
                or "")
            owner, basis = _owner_for(placement, catalog)
            attachment = placement.get("attachment") or {}
            represented_feature_id = (
                placement.get("canonical_feature_id")
                or (placement.get("evidence") or {}).get(
                    "canonical_feature_id")
                or attachment.get("canonical_carrier_id")
                or attachment.get("canonical_building_id")
                or attachment.get("canonical_mass_id")
            )
            decisions = (representation_rows_by_feature.get(
                str(represented_feature_id), [])
                if represented_feature_id else [])
            module_domain = str((catalog.get(module_id) or {}).get(
                "owning_domain") or "")
            # FL-4876 FACADE-C1. An observed storefront on a building face is
            # that building's facade, whichever owner builds it.
            #
            # This test used to require owner == "fixtures", which a
            # custom-identity placement can never be -- _owner_for routes
            # every custom_identity_subject to "custom" before module domain
            # is even consulted. So five measured storefront panels standing
            # on named canonical faces bound to their building's primary
            # terrain_mass row, and the gate reported those buildings as
            # having no facade at all.
            #
            # The planner stamps the placement it has already identified as
            # filling the slot; this reads the stamp instead of re-deriving
            # the fact from owner and module domain. The building_face anchor
            # is still required, so a freestanding custom object cannot claim
            # to be a facade.
            facade_slot = placement.get("facade_slot")
            observed_facade = (
                isinstance(facade_slot, dict)
                and facade_slot.get("role") == "observed_facade")
            wants_facade = (
                attachment.get("anchor_kind") == "building_face"
                and (observed_facade
                     or (owner == "fixtures"
                         and module_domain in {"facade_archetype",
                                               "generic_storefront"}))
            )
            if wants_facade:
                decision = next((row for row in decisions
                                 if row.get("disposition") == "mesh_facade"), None)
            else:
                decision = next((row for row in decisions
                                 if row.get("primary")), None)
            if decision is not None:
                placement["representation_decision"] = {
                    key: decision.get(key) for key in (
                        "feature_id", "representation_id", "disposition",
                        "representation_kind", "owner_stage", "policy_rule",
                        "reason")
                }
            else:
                placement["representation_decision"] = {
                    "feature_id": represented_feature_id,
                    "disposition": "not_applicable_to_generated_requirement",
                    "reason": (
                        "logical requirement has no independent canonical "
                        "feature representation row"),
                }
            if owner == "terminal":
                disposition = placement.get("population_disposition") or {}
                placement["terminal_state"] = {
                    "state": str(
                        disposition.get("state") or "intentional_omission"),
                    "category": disposition.get("category"),
                    "reason": disposition.get("reason"),
                }
            if ((placement.get("evidence") or {}).get("kind") == "rule"
                    and decision is not None
                    and decision.get("disposition") == "intentional_omission"):
                owner = "terminal"
                basis = "representation:intentional_omission"
                placement["terminal_state"] = {
                    "state": "intentional_omission",
                    "representation_id": decision.get("representation_id"),
                    "policy_rule": decision.get("policy_rule"),
                    "reason": decision.get("reason"),
                }
            # Representation and its physical envelope precede final spatial
            # resolution. Placement is not allowed to freeze an anchor and
            # discover the selected asset footprint afterward.
            placement["lowering_owner"] = owner
            placement["lowering_owner_basis"] = basis
            if owner == "custom":
                placement["asset_contract"] = custom_plane_contract(
                    placement.get("custom_identity_subject"))
            elif owner == "fixtures":
                placement["asset_contract"] = fixture_asset_contract(
                    module_id, placement, rules)
                placement["asset_contract"]["catalog_domain"] = (
                    (catalog.get(module_id) or {}).get("owning_domain")
                    or module_id)
            elif owner == "linear":
                placement["asset_contract"] = linear_asset_contract(
                    carrier, rules)
            if owner != "terminal":
                envelope = placement.get("asset_physical_envelope")
                if not isinstance(envelope, dict):
                    raise ValueError(
                        f"population placement {placement_id} reached "
                        "dispatch before representation-envelope selection")
                if envelope.get("selection_stage") != \
                        "population_before_dispatch":
                    raise ValueError(
                        f"population placement {placement_id} has a late or "
                        "unowned physical envelope")
            if owner != "terminal":
                attachment = placement.get("attachment")
                if not isinstance(attachment, dict):
                    raise ValueError(
                        f"population placement {placement_id} has no canonical "
                        "attachment before dispatch")
                if attachment.get("record_type") != "attachment":
                    raise ValueError(
                        f"population placement {placement_id} reaches dispatch "
                        f"with unresolved attachment debt: "
                        f"{attachment.get('reason', 'unnamed')}")
                try:
                    resolved_pose = resolve_span(attachment, attachment_index)
                    if attachment.get("anchor_kind") == "carrier_span":
                        resolved_pose = {
                            **resolved_pose,
                            **resolve_span_endpoints(attachment, attachment_index),
                        }
                    run = placement.get("run")
                    if isinstance(run, dict) and all(
                            run.get(key) is not None
                            for key in ("E0", "N0", "E1", "N1")):
                        resolved_pose = {
                            **resolved_pose,
                            "E0": float(run["E0"]),
                            "N0": float(run["N0"]),
                            "E1": float(run["E1"]),
                            "N1": float(run["N1"]),
                        }
                except (KeyError, TypeError, ValueError, IndexError) as exc:
                    raise ValueError(
                        f"population placement {placement_id} attachment cannot "
                        f"resolve before lowering: {exc}") from exc
                placement["resolved_pose"] = resolved_pose
                yaw = math.radians(float(resolved_pose.get("yaw_deg", 0.0)))
                if attachment.get("anchor_kind") == "building_face":
                    normal = [math.cos(yaw), math.sin(yaw)]
                    tangent = [normal[1], -normal[0]]
                    frame_basis = "building_face_outward_normal"
                else:
                    tangent = [math.cos(yaw), math.sin(yaw)]
                    normal = [-tangent[1], tangent[0]]
                    frame_basis = "resolved_pose_tangent"
                placement["resolved_frame"] = {
                    "tangent": tangent,
                    "normal": normal,
                    "basis": frame_basis,
                }
                host = (
                    attachment.get("canonical_building_id")
                    or attachment.get("canonical_mass_id")
                    or attachment.get("canonical_carrier_id")
                    or attachment.get("nearest_carrier_id")
                    or attachment.get("ground_owner")
                )
                feature_id = (
                    placement.get("canonical_feature_id")
                    or (placement.get("evidence") or {}).get(
                        "canonical_feature_id")
                    or attachment.get("canonical_point")
                    or host
                )
                parent = host or feature_id
                if not feature_id or feature_id not in attachment_index.by_id:
                    raise ValueError(
                        f"population placement {placement_id} has no canonical "
                        "feature identity after attachment resolution")
                if not parent or parent not in attachment_index.by_id:
                    raise ValueError(
                        f"population placement {placement_id} has no canonical "
                        "host/parent after attachment resolution")
                placement["planned_feature_id"] = feature_id
                placement["planned_parent_feature_id"] = parent
                placement["planned_host_feature_id"] = host
            if owner != "terminal":
                placement["logical_realizations"] = logical_realizations(
                    module_id, placement, rules)
            per_owner[owner].append(placement)
            counts[owner] += 1
        for owner, placements in per_owner.items():
            if not placements:
                continue
            carrier_copy = {
                key: copy.deepcopy(value)
                for key, value in carrier.items()
                if key != "placements"
            }
            carrier_copy["placements"] = placements
            owner_carriers[owner].append(carrier_copy)

    # FL-4876 COHERENCE-ZERO-01: wire spans are not chained through buildings.
    finalize_linear_realizations(
        owner_carriers["linear"], rules,
        buildings=span_building_index(attachment_index.by_id.values()))

    if sum(counts.values()) != len(placement_ids):
        raise AssertionError("population dispatch lost or duplicated placements")

    outputs: dict[str, dict] = {}
    for owner in OWNERS:
        document = copy.deepcopy(plan)
        document["carriers"] = owner_carriers[owner]
        document["dispatch"] = {
            "schema": SCHEMA,
            "owner": owner,
            "source_population_plan_sha256": source_plan_sha256,
            "representation_plan_sha256": representation_plan_sha256,
            "placements_total": len(placement_ids),
            "placements_for_owner": counts[owner],
            "placements_by_owner": dict(sorted(counts.items())),
        }
        outputs[owner] = document
    return outputs


def write_dispatch(plan_path: Path, catalog_path: Path,
                   representation_plan_path: Path,
                   rules_path: Path,
                   canonical_world_path: Path,
                   out_dir: Path) -> dict[str, Path]:
    plan_path = Path(plan_path)
    catalog_path = Path(catalog_path)
    representation_plan_path = Path(representation_plan_path)
    rules_path = Path(rules_path)
    canonical_world_path = Path(canonical_world_path)
    out_dir = Path(out_dir)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    representation_plan = json.loads(
        representation_plan_path.read_text(encoding="utf-8"))
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    canonical_world = json.loads(
        canonical_world_path.read_text(encoding="utf-8"))
    documents = partition(
        plan, catalog, representation_plan, rules, canonical_world,
        source_plan_sha256=_sha256(plan_path),
        representation_plan_sha256=_sha256(representation_plan_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for owner, document in documents.items():
        path = out_dir / f"population_plan.{owner}.json"
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=1) + "\n",
            encoding="utf-8",
        )
        paths[owner] = path
    manifest = {
        "schema": SCHEMA,
        "source_population_plan": str(plan_path),
        "source_population_plan_sha256": _sha256(plan_path),
        "catalog": str(catalog_path),
        "representation_plan": str(representation_plan_path),
        "representation_plan_sha256": _sha256(representation_plan_path),
        "rules": str(rules_path),
        "rules_sha256": _sha256(rules_path),
        "canonical_world": str(canonical_world_path),
        "canonical_world_sha256": _sha256(canonical_world_path),
        "outputs": {owner: str(path) for owner, path in sorted(paths.items())},
        "placements_by_owner": documents["linear"]["dispatch"]["placements_by_owner"],
    }
    (out_dir / "population_dispatch.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return paths


def require_owner(plan: dict, expected_owner: str) -> dict:
    dispatch = plan.get("dispatch")
    if not isinstance(dispatch, dict) or dispatch.get("schema") != SCHEMA:
        raise ValueError(
            f"{expected_owner} lowerer requires a {SCHEMA} owner slice; "
            "the heterogeneous population plan is not a lowering input"
        )
    actual_owner = dispatch.get("owner")
    if actual_owner != expected_owner:
        raise ValueError(
            f"population dispatch owner mismatch: expected {expected_owner}, "
            f"got {actual_owner}"
        )
    seen: set[str] = set()
    for carrier in plan.get("carriers", []):
        for placement in carrier.get("placements", []):
            placement_id = str(placement.get("placement_id") or "")
            if not placement_id or placement_id in seen:
                raise ValueError(
                    f"invalid placement identity in {expected_owner} slice: {placement_id!r}"
                )
            seen.add(placement_id)
            if placement.get("lowering_owner") != expected_owner:
                raise ValueError(
                    f"placement {placement_id} belongs to "
                    f"{placement.get('lowering_owner')!r}, not {expected_owner!r}"
                )
            envelope = placement.get("asset_physical_envelope")
            if not isinstance(envelope, dict):
                raise ValueError(
                    f"placement {placement_id} has no upstream physical envelope"
                )
            if float(envelope.get("max_longitudinal_overhang_m", -1.0)) != 0.0:
                raise ValueError(
                    f"placement {placement_id} permits undeclared longitudinal "
                    "overhang"
                )
            if (not isinstance(placement.get("resolved_pose"), dict)
                    or not isinstance(placement.get("resolved_frame"), dict)
                    or not placement.get("planned_feature_id")
                    or not placement.get("planned_parent_feature_id")):
                raise ValueError(
                    f"placement {placement_id} lacks its pre-lowering "
                    "resolved pose or canonical parent")
            if not isinstance(placement.get("asset_contract"), dict):
                raise ValueError(
                    f"placement {placement_id} has no upstream asset contract")
            if (expected_owner == "linear"
                    and not isinstance(
                        placement.get("logical_realizations"), list)):
                raise ValueError(
                    f"linear placement {placement_id} has no upstream "
                    "logical realizations")
    expected_count = int(dispatch.get("placements_for_owner", -1))
    if expected_count != len(seen):
        raise ValueError(
            f"{expected_owner} dispatch count mismatch: header={expected_count} rows={len(seen)}"
        )
    return dispatch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--representation-plan", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--canonical-world", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    write_dispatch(
        args.plan, args.catalog, args.representation_plan, args.rules,
        args.canonical_world,
        args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
