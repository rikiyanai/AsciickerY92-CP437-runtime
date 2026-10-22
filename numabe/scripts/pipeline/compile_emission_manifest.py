#!/usr/bin/env python3
"""Compile the single emitted/terminal instance manifest for FL-4809.

This is the provenance join between population dispatch and mechanical
lowering.  It does not infer representation, placement, or geometry.  Every
dispatched logical placement must terminate exactly once as emitted,
or as an explicit pre-lowering terminal decision. Missing and contradictory
terminal states fail closed; lowerer skips are not accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


SCHEMA = "y92.emission_manifest.v1"


def _load(path: Path):
    if not path.is_file():
        raise ValueError(f"required emission input is absent: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _placements(plan: dict) -> list[dict]:
    if isinstance(plan.get("placements"), list):
        return list(plan["placements"])
    return [
        placement
        for carrier in plan.get("carriers", [])
        for placement in carrier.get("placements", [])
    ]


def _instance_placement_ids(instance: dict) -> list[str]:
    ids = []
    for value in instance.get("source_placement_ids") or []:
        if value:
            ids.append(str(value))
    for key in ("placement_id", "run_id", "span_from", "span_to"):
        value = instance.get(key)
        if value:
            ids.append(str(value))
    return list(dict.fromkeys(ids))


def build(build_dir: Path) -> dict:
    build_dir = Path(build_dir)
    plan_dir = build_dir / "plan" / "population"
    owner_plans = {
        owner: _load(plan_dir / f"population_plan.{owner}.json")
        for owner in ("linear", "fixtures", "custom", "terminal")
    }
    owner_plans["station_facade"] = _load(
        build_dir / "plan" / "station_facade_plan.json")
    dispatch_manifest = _load(plan_dir / "population_dispatch.json")

    instance_files = {
        "linear": build_dir / "lower_linear_instances.json",
        "fixtures": build_dir / "lower_fixtures_instances.json",
        "custom": build_dir / "lower_custom_instances.json",
        "station_facade": build_dir / "station_facade_instances.json",
    }
    instances = {owner: _load(path) for owner, path in instance_files.items()}

    emitted_by_placement: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    rows: list[dict] = []
    for owner, owner_instances in instances.items():
        if not isinstance(owner_instances, list):
            raise ValueError(f"{owner} instance file must contain a list")
        for instance in owner_instances:
            placement_ids = _instance_placement_ids(instance)
            if not placement_ids:
                raise ValueError(
                    f"{owner} emitted instance has no placement identity: "
                    f"{instance.get('inst_name')}")
            mesh_name = instance.get("mesh_name")
            mesh_path = build_dir / "meshes" / str(mesh_name) if mesh_name else None
            if mesh_path is not None and (
                    not mesh_path.is_file() or mesh_path.stat().st_size == 0):
                raise ValueError(
                    f"{owner} emitted instance references absent/empty asset: "
                    f"{mesh_path}")
            for placement_id in placement_ids:
                emitted_by_placement[placement_id].append((owner, instance))
            rows.append({
                "record_kind": "emitted_instance",
                "owner": owner,
                "placement_ids": placement_ids,
                "feature_id": instance.get("feature_id"),
                "module_id": instance.get("module_id"),
                "mount_kind": instance.get("mount_kind"),
                "catalog_domain": instance.get("catalog_domain"),
                "custom_identity_subject": instance.get("subject"),
                "representation_id": instance.get("representation_id"),
                "attachment_id": instance.get("attachment_id"),
                "inst_name": instance.get("inst_name"),
                "mesh_name": mesh_name,
                "asset_sha256": _sha256(mesh_path) if mesh_path is not None else None,
                "transform": instance.get("transform"),
                "terminal_state": "emitted",
            })

    population_rows: list[dict] = []
    errors: list[dict] = []
    seen_placements: set[str] = set()
    for owner, plan in owner_plans.items():
        for placement in _placements(plan):
            placement_id = str(placement["placement_id"])
            if placement_id in seen_placements:
                errors.append({"placement_id": placement_id,
                               "reason": "dispatched_to_multiple_owners"})
                continue
            seen_placements.add(placement_id)
            emitted = emitted_by_placement.get(placement_id, [])
            terminal = placement.get("terminal_state") if owner == "terminal" else None
            states = int(bool(emitted)) + int(terminal is not None)
            if states != 1:
                errors.append({
                    "placement_id": placement_id,
                    "owner": owner,
                    "reason": ("missing_terminal_state" if states == 0
                               else "multiple_terminal_states"),
                    "emitted_instances": [row.get("inst_name") for _, row in emitted],
                    "terminal": terminal,
                })
                state = "invalid"
                reason = errors[-1]["reason"]
            elif emitted:
                if any(emitted_owner != owner for emitted_owner, _ in emitted):
                    errors.append({
                        "placement_id": placement_id,
                        "owner": owner,
                        "reason": "emitted_by_wrong_owner",
                        "emitted_owners": sorted({o for o, _ in emitted}),
                    })
                    state, reason = "invalid", "emitted_by_wrong_owner"
                else:
                    state, reason = "emitted", "lowering instance present"
            else:
                state = str(terminal.get("state") or "intentional_omission")
                reason = str(terminal.get("reason") or state)
            population_rows.append({
                "record_kind": "population_terminal",
                "placement_id": placement_id,
                "owner": owner,
                "module_id": placement.get("module_id"),
                "custom_identity_subject": placement.get(
                    "custom_identity_subject"),
                "feature_id": (
                    placement.get("canonical_feature_id")
                    or (placement.get("evidence") or {}).get(
                        "canonical_feature_id")
                    or (placement.get("attachment") or {}).get(
                        "canonical_carrier_id")
                    or (placement.get("attachment") or {}).get(
                        "canonical_building_id")),
                "representation_decision": placement.get(
                    "representation_decision"),
                "attachment_id": (placement.get("attachment") or {}).get(
                    "attachment_id"),
                "terminal_state": state,
                "reason": reason,
                "emitted_instances": [row.get("inst_name") for _, row in emitted],
            })

    for placement_id, emitted in sorted(emitted_by_placement.items()):
        if placement_id in seen_placements:
            continue
        errors.append({
            "placement_id": placement_id,
            "reason": "emitted_without_planned_placement",
            "emitted_owners": sorted({owner for owner, _ in emitted}),
            "emitted_instances": [row.get("inst_name") for _, row in emitted],
        })

    declared_total = sum(
        int(value) for value in
        dispatch_manifest.get("placements_by_owner", {}).values()) + len(
            _placements(owner_plans["station_facade"]))
    if declared_total != len(seen_placements):
        errors.append({
            "reason": "dispatch_manifest_denominator_mismatch",
            "declared": declared_total,
            "observed": len(seen_placements),
        })

    # Non-population physical representations join the same manifest but do
    # not participate in the population terminal denominator.
    building_instances = _load(build_dir / "building_instances.json")
    for instance in building_instances:
        rows.append({
            "record_kind": "emitted_instance",
            "owner": "building_mass",
            "placement_ids": [],
            "feature_id": instance.get("feature_id"),
            "representation_id": None,
            "attachment_id": None,
            "inst_name": instance.get("inst_name") or instance.get("name"),
            "mesh_name": instance.get("mesh_name"),
            "asset_sha256": None,
            "transform": instance.get("transform"),
            "terminal_state": "emitted",
        })
    surface = _load(build_dir / "surface_receipt.json")
    for feature in surface.get("features", []):
        rows.append({
            "record_kind": "emitted_surface",
            "owner": "surface",
            "placement_ids": [],
            "feature_id": feature.get("feature_id"),
            "representation_id": feature.get("representation_id"),
            "attachment_id": feature.get("attachment_id"),
            "inst_name": None,
            "mesh_name": None,
            "asset_sha256": None,
            "transform": None,
            "terminal_state": feature.get("terminal_state") or "emitted",
        })

    # FL-4876. place_fronts is a representation-plan owner_stage, not one of
    # the four population dispatch owners, so it joins here beside surface and
    # building_mass rather than in the population terminal denominator.  Its
    # obligation is the plan slice the runner hands its lowerer; its
    # fulfilment is that lowerer's own instances file.  A planned front with
    # no emitted instance is a named debt, never an absence.
    place_front_rows: list[dict] = []
    front_plan_path = build_dir / "plan" / "plan_by_owner" / "place_fronts.json"
    front_plan_features: list[str] = []
    if front_plan_path.is_file():
        front_plan_doc = json.loads(front_plan_path.read_text(encoding="utf-8"))
        front_plan_features = sorted({
            str(row.get("feature_id"))
            for row in front_plan_doc.get("rows", [])
            if row.get("feature_id")
        })
    if front_plan_features:
        front_instances_path = build_dir / "place_front_instances.json"
        front_receipt_path = build_dir / "place_front_receipt.json"
        if not front_instances_path.is_file():
            raise ValueError(
                f"{len(front_plan_features)} place-front representations are "
                "planned but their owner emitted no instance file: "
                f"{front_instances_path}; the place-front lowerer "
                "(compile_world_ir_place_fronts) did not run for this build")
        front_instances = json.loads(
            front_instances_path.read_text(encoding="utf-8"))
        if not isinstance(front_instances, list):
            raise ValueError("place_front instance file must contain a list")
        front_skips: dict[str, str] = {}
        if front_receipt_path.is_file():
            front_receipt = json.loads(
                front_receipt_path.read_text(encoding="utf-8"))
            for skip in (front_receipt.get("plan", {}).get(
                    "skipped_by_state") or []):
                front_skips.setdefault(
                    str(skip.get("feature_id")),
                    str(skip.get("reason") or "skipped without a reason"))
        emitted_fronts: dict[str, dict] = {}
        for instance in front_instances:
            mesh_name = instance.get("mesh_name")
            mesh_path = build_dir / "meshes" / str(mesh_name) if mesh_name else None
            if mesh_path is not None and (
                    not mesh_path.is_file() or mesh_path.stat().st_size == 0):
                raise ValueError(
                    "place_front emitted instance references absent/empty "
                    f"asset: {mesh_path}")
            emitted_fronts[str(instance.get("feature_id"))] = instance
            rows.append({
                "record_kind": "emitted_instance",
                "owner": "place_front",
                "placement_ids": [],
                "feature_id": instance.get("feature_id"),
                "module_id": instance.get("resolved_profile_id"),
                "mount_kind": "building_face_panel",
                "representation_id": instance.get("representation_id"),
                "attachment_id": None,
                "inst_name": instance.get("inst_name"),
                "mesh_name": mesh_name,
                "asset_sha256": _sha256(mesh_path) if mesh_path is not None else None,
                "transform": instance.get("transform"),
                "terminal_state": "emitted",
            })
        for feature_id in front_plan_features:
            instance = emitted_fronts.get(feature_id)
            state = "emitted" if instance is not None else "representation_debt"
            reason = ("lowering instance present" if instance is not None
                      else front_skips.get(
                          feature_id,
                          "planned place front has no emitted instance and the "
                          "lowerer recorded no reason"))
            row = {
                "record_kind": "representation_terminal",
                "owner": "place_front",
                "feature_id": feature_id,
                "semantic_class": "place",
                "source_state": state,
                "terminal_state": state,
                "reason": reason,
            }
            place_front_rows.append(row)
            if state != "emitted":
                errors.append({
                    "feature_id": feature_id,
                    "owner": "place_front",
                    "reason": "place_front_representation_not_emitted",
                    "detail": reason,
                })
        for feature_id in sorted(emitted_fronts):
            if feature_id not in set(front_plan_features):
                errors.append({
                    "feature_id": feature_id,
                    "owner": "place_front",
                    "reason": "emitted_without_planned_representation",
                })

    # FL-4876 INFRA-EMIT-01. The infrastructure owner_stage (station
    # platform, entrances, rail fence, grade crossing) emits through
    # compile_world_ir_infrastructure, whose instances live in
    # infrastructure_receipt.json. This manifest never read them, so the four
    # planned grade-crossing rows read as "emitting plan row without emission"
    # in the route inventory although one crossing assembly, built from all
    # four source crossing points, was emitted. Obligation: the plan slice
    # plan_by_owner/infrastructure.json. Fulfilment: a receipt instance that
    # names the planned feature as its feature_id or in evidence_feature_ids.
    # Only planned features are credited: an instance's other evidence (for
    # example the omitted elevated rail ways under the corridor fence) is not
    # an emission of those features.
    infra_rows: list[dict] = []
    infra_plan_path = build_dir / "plan" / "plan_by_owner" / "infrastructure.json"
    infra_plan_features: list[str] = []
    if infra_plan_path.is_file():
        infra_plan_doc = json.loads(infra_plan_path.read_text(encoding="utf-8"))
        infra_plan_features = sorted({
            str(row.get("feature_id"))
            for row in infra_plan_doc.get("rows", [])
            if row.get("feature_id")
        })
    if infra_plan_features:
        infra_receipt_path = build_dir / "infrastructure_receipt.json"
        if not infra_receipt_path.is_file():
            raise ValueError(
                f"{len(infra_plan_features)} infrastructure representations "
                "are planned but their owner wrote no receipt: "
                f"{infra_receipt_path}")
        infra_receipt = json.loads(infra_receipt_path.read_text(encoding="utf-8"))
        by_feature: dict[str, dict] = {}
        # Every emitted infrastructure instance is a physical emission with
        # its semantic class (the requirements gate counts platform,
        # entrances, rail fence and crossing by semantic_class).
        for instance in infra_receipt.get("instances") or []:
            mesh_name = instance.get("mesh_name")
            mesh_path = build_dir / "meshes" / str(mesh_name) if mesh_name else None
            if mesh_path is not None and (
                    not mesh_path.is_file() or mesh_path.stat().st_size == 0):
                raise ValueError(
                    "infrastructure emitted instance references absent/empty "
                    f"asset: {mesh_path}")
            rows.append({
                "record_kind": "emitted_instance",
                "owner": "infrastructure",
                "placement_ids": [],
                "feature_id": instance.get("feature_id"),
                "semantic_class": instance.get("semantic_class"),
                # An infrastructure assembly's module identity is its
                # semantic class (required_infrastructure_counts keys).
                "module_id": instance.get("semantic_class"),
                "resolved_profile_id": (instance.get("dimensions") or {}).get(
                    "resolved_profile_id"),
                "representation_id": instance.get("representation_id"),
                "attachment_id": None,
                "inst_name": instance.get("inst_name"),
                "mesh_name": mesh_name,
                "asset_sha256": _sha256(mesh_path) if mesh_path is not None else None,
                "transform": instance.get("transform"),
                "terminal_state": "emitted",
            })
        for instance in infra_receipt.get("instances") or []:
            named = [instance.get("feature_id"), *(
                instance.get("evidence_feature_ids") or [])]
            for feature_id in named:
                if feature_id:
                    by_feature.setdefault(str(feature_id), instance)
        for feature_id in infra_plan_features:
            instance = by_feature.get(feature_id)
            if instance is None:
                row = {
                    "record_kind": "representation_terminal",
                    "owner": "infrastructure",
                    "feature_id": feature_id,
                    "source_state": "representation_debt",
                    "terminal_state": "representation_debt",
                    "reason": "planned infrastructure representation has no "
                              "emitted instance naming it",
                }
                infra_rows.append(row)
                errors.append({"feature_id": feature_id,
                               "owner": "infrastructure",
                               "reason": "infrastructure_representation_not_emitted"})
                continue
            if str(instance.get("feature_id")) == feature_id:
                continue  # the instance row above already names it
            mesh_name = instance.get("mesh_name")
            mesh_path = build_dir / "meshes" / str(mesh_name) if mesh_name else None
            rows.append({
                # One assembly built from several source points: each other
                # planned source feature is credited to it, never counted as a
                # second physical instance.
                "record_kind": "emitted_evidence_credit",
                "owner": "infrastructure",
                "placement_ids": [],
                "feature_id": feature_id,
                "module_id": (instance.get("dimensions") or {}).get(
                    "resolved_profile_id"),
                "representation_id": instance.get("representation_id"),
                "attachment_id": None,
                "inst_name": instance.get("inst_name"),
                "mesh_name": mesh_name,
                "asset_sha256": _sha256(mesh_path) if mesh_path is not None else None,
                "transform": instance.get("transform"),
                "terminal_state": "emitted",
            })

    representation_rows: list[dict] = list(place_front_rows) + infra_rows
    for terminal in surface.get("terminal_states", []):
        source_state = str(terminal.get("state") or "silent")
        state = "emitted" if source_state == "painted" \
            else "representation_debt"
        row = {
            "record_kind": "representation_terminal",
            "owner": "surface",
            "feature_id": terminal.get("feature_id"),
            "semantic_class": terminal.get("semantic_class"),
            "source_state": source_state,
            "terminal_state": state,
            "reason": terminal.get("reason") or source_state,
        }
        representation_rows.append(row)
        if state != "emitted":
            errors.append({
                "feature_id": terminal.get("feature_id"),
                "owner": "surface",
                "reason": "surface_representation_not_emitted",
                "source_state": source_state,
                "detail": terminal.get("reason"),
            })
    rows.extend(representation_rows)

    terminal_counts = Counter(
        row["terminal_state"] for row in population_rows)
    blocking_terminal_states = [
        row for row in population_rows
        if row["terminal_state"] not in {"emitted", "intentional_omission"}
    ] + [row for row in representation_rows
         if row["terminal_state"] == "representation_debt"]
    return {
        "schema": SCHEMA,
        "inputs": {
            "dispatch_manifest": str(plan_dir / "population_dispatch.json"),
            "instances": {
                **{owner: str(path)
                   for owner, path in sorted(instance_files.items())},
                **({"place_front": str(build_dir / "place_front_instances.json")}
                   if front_plan_features else {}),
            },
            "place_front_plan": (str(front_plan_path)
                                 if front_plan_features else None),
        },
        "counts": {
            "population_placements": len(population_rows),
            "population_by_terminal_state": dict(sorted(terminal_counts.items())),
            "emission_records": len(rows),
            "emitted_instances": sum(
                row.get("record_kind") == "emitted_instance"
                and row.get("terminal_state") == "emitted"
                for row in rows),
            "emitted_surfaces": sum(
                row.get("record_kind") == "emitted_surface"
                and row.get("terminal_state") == "emitted"
                for row in rows),
            "representation_terminals": len(representation_rows),
            "place_front_representations": len(place_front_rows),
            "place_fronts_emitted": sum(
                row["terminal_state"] == "emitted" for row in place_front_rows),
            "errors": len(errors),
            "blocking_terminal_states": len(blocking_terminal_states),
        },
        "errors": errors,
        "blocking_terminal_states": blocking_terminal_states,
        "population": sorted(population_rows,
                             key=lambda row: row["placement_id"]),
        "emissions": sorted(
            rows,
            key=lambda row: (str(row.get("owner")),
                             str(row.get("inst_name")),
                             str(row.get("feature_id")))),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = build(args.build_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return 1 if (document["errors"]
                 or document["blocking_terminal_states"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
