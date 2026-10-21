#!/usr/bin/env python3
"""Refuse A3D assembly until the Numabe reconstruction plan is complete.

This gate operates only on semantic, representation, population, asset, and
placement plans.  It deliberately runs before Classic base compilation.  A
failed candidate therefore cannot consume an A3D build number or create a map
that looks complete merely because terrain and building masses were serialized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

from scripts.pipeline.audit_route_view_composition import (
    _nearest_route,
)
from scripts.pipeline.compile_representation_plan import (
    NON_POPULATION_OWNER_SLICES,
    NON_POPULATION_OWNER_STAGES,
    WIRED_NON_POPULATION_OWNERS,
    UNMAPPED_NON_PHYSICAL,
    UNMAPPED_PHYSICAL,
    POPULATION_OWNER_STAGES,
    UNMAPPED_FEATURE_PREFIX,
    UNMAPPED_ROW_CONTRACT,
)
from scripts.pipeline.authored_waivers import (
    AuthoredWaiverInvalid,
    load_waiver_document,
)
from scripts.pipeline.compile_route_walkability import _project_route
from scripts.pipeline.dispatch_population_plan import OWNERS, require_owner
from scripts.pipeline.population_representation_contract import (
    INFRASTRUCTURE_REQUIREMENT_MODULES,
    STREETSCAPE_REQUIREMENT_ARCHETYPES,
    STREETSCAPE_REQUIREMENT_MODULES,
)
from scripts.pipeline.population_asset_contract import load_asset_plan


SCHEMA = "y92.preassembly_candidate.v1"
EMITTING_DISPOSITIONS = frozenset({
    "terrain_mass", "mesh_fixture", "surface_decal", "mesh_facade",
})
DEBT_DISPOSITIONS = frozenset({"observation_owed", "representation_owed"})

# FL-4876. The only terminal state that discharges a population obligation
# without authoring anything. A terminal placement is excluded from asset
# coverage by design -- it emits no geometry -- so routing a placement to
# terminal removes its asset obligation. Before this, nothing then asked
# whether that omission had been decided or merely deferred, which meant a
# repair that moved an unattachable facade out of "falsely attached" moved it
# into "silently omitted" instead. An omission is permitted only when
# something decided it.
TERMINAL_DISCHARGING_STATES = frozenset({"intentional_omission"})
TERMINAL_DEBT_KIND = "terminal_population_debt"
NON_POPULATION_DISPOSITIONS = frozenset({
    "terrain_mass", "surface_decal", "minimap_only", "intentional_omission",
})

# FL-4809 R2.  ``compile_representation_plan.emit_waived`` writes
# ``policy_rule = "authored_waiver:" + waiver["approval_record"]`` so that a
# reader can ask who approved the omission.  Until now this gate read that
# string as a fact.  It is a claim: it is produced by the same stage whose
# output the gate exists to doubt, and nothing downstream re-derived it from
# the waiver document.  A plan naming an approval record that this run's
# document does not carry -- or carries only as a proposal, or for a different
# feature, or against different conditions -- passed straight through.
#
# The same planner also writes
# ``representation_id = "authored_waiver:" + fid``.
# That field carries a feature id, not an approval record, so reading it as a
# claim would invent a record named after the feature.  It is deliberately
# absent from the tuple below.
AUTHORED_WAIVER_CLAIM_PREFIX = "authored_waiver:"
AUTHORED_WAIVER_CLAIM_FIELDS = ("policy_rule", "evidence_state", "evidence")
WAIVER_PROVENANCE_KIND = "waiver_approval_provenance"

# FL-4876 INFRA-01. Infrastructure semantics that no population module can
# ever satisfy, because their geometry is built by
# compile_world_ir_infrastructure from authored profiles
# (station_composition_profiles, railway_crossing_profiles) rather than from
# population placements. INFRASTRUCTURE_REQUIREMENT_MODULES maps both to the
# empty frozenset for exactly that reason, so before this the gate summed two
# empty sets and blocked forever on a count only a post-gate stage produces.
#
# This is not a waiver. The gate still refuses unless that owner is a wired
# non-population owner -- the same proof it demands of place_fronts -- and
# the stage itself raises if any mesh it planned is missing.
INFRASTRUCTURE_OWNER_SEMANTICS = {
    "station_platform_proposal": "infrastructure",
    "station_entrance_assembly_proposal": "infrastructure",
}


def _load(path: Path) -> dict | list:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _building_ids(document: dict | list) -> set[str]:
    rows = document if isinstance(document, list) else document.get("instances", [])
    return {str(row["feature_id"]) for row in rows if row.get("feature_id")}


def _dispatch_documents(dispatch_dir: Path) -> dict[str, dict]:
    documents = {}
    for owner in OWNERS:
        path = Path(dispatch_dir) / f"population_plan.{owner}.json"
        if not path.is_file():
            raise ValueError(f"population dispatch owner slice is absent: {path}")
        document = _load(path)
        if not isinstance(document, dict):
            raise ValueError(f"population dispatch owner slice is not an object: {path}")
        documents[owner] = document
    return documents


def _world_xy(pose: dict, metadata: dict) -> tuple[float, float]:
    scale = float(metadata["content_scale"])
    return (
        (float(pose["E"]) - float(metadata["origin_easting"])) * scale
        + float(metadata.get("offset_x", 0.0)),
        (float(pose["N"]) - float(metadata["origin_northing"])) * scale
        + float(metadata.get("offset_y", 0.0)),
    )


def _sample_placement_world_points(placement: dict, metadata: dict,
                                   step_m: float) -> list[tuple[float, float]]:
    """Sample the planned physical span densely enough for route-bin coverage."""
    pose = placement.get("resolved_pose") or {}
    scale = float(metadata["content_scale"])
    if all(pose.get(key) is not None for key in ("E0", "N0", "E1", "N1")):
        length_m = math.hypot(
            float(pose["E1"]) - float(pose["E0"]),
            float(pose["N1"]) - float(pose["N0"]))
        sample_count = max(1, int(math.ceil(length_m / max(step_m, 1e-9))))
        return [_world_xy({
            "E": float(pose["E0"]) + (
                float(pose["E1"]) - float(pose["E0"])) * index / sample_count,
            "N": float(pose["N0"]) + (
                float(pose["N1"]) - float(pose["N0"])) * index / sample_count,
        }, metadata) for index in range(sample_count + 1)]
    logical_points = []
    for logical in placement.get("logical_realizations", []):
        logical_pose = logical.get("pose") or {}
        if logical_pose.get("E") is not None and logical_pose.get("N") is not None:
            logical_points.append(_world_xy(logical_pose, metadata))
    if logical_points:
        return logical_points
    if pose.get("E") is not None and pose.get("N") is not None:
        return [_world_xy(pose, metadata)]
    return []


def _authored_waiver_claims(
        representation: dict) -> list[tuple[str, str, dict]]:
    """Every ``authored_waiver:<approval_record>`` claim the plan makes.

    Returns ``(feature_id, approval_record, row)``.  Secondary rows are read
    too: a claim of approval is a claim of approval wherever it is written,
    and a gate that only inspected primaries would leave a second door open.
    """
    claims: list[tuple[str, str, dict]] = []
    for row in representation.get("rows", []):
        if not isinstance(row, dict):
            continue
        feature_id = str(row.get("feature_id") or "<missing>")
        records: set[str] = set()
        for field in AUTHORED_WAIVER_CLAIM_FIELDS:
            value = row.get(field)
            if not isinstance(value, str):
                continue
            if not value.startswith(AUTHORED_WAIVER_CLAIM_PREFIX):
                continue
            record = value[len(AUTHORED_WAIVER_CLAIM_PREFIX):].strip()
            if record:
                records.add(record)
        for record in sorted(records):
            claims.append((feature_id, record, row))
    return claims


def build(*, canonical_path: Path, representation_plan_path: Path,
          dispatch_dir: Path, custom_asset_plan_path: Path,
          population_asset_plan_path: Path,
          source_palette_path: Path,
          surface_stamp_plan_path: Path, station_facade_plan_path: Path,
          building_specs_path: Path, requirements_path: Path,
          route_path: Path, terrain_metadata_path: Path,
          authored_waivers_path: Path | None = None,
          plan_by_owner_dir: Path | None = None) -> dict:
    canonical = _load(canonical_path)
    representation = _load(representation_plan_path)
    custom_assets = _load(custom_asset_plan_path)
    population_assets_raw = _load(population_asset_plan_path)
    source_palette = _load(source_palette_path)
    surfaces = _load(surface_stamp_plan_path)
    station = _load(station_facade_plan_path)
    building_specs = _load(building_specs_path)
    requirements = _load(requirements_path)
    terrain_metadata = _load(terrain_metadata_path)
    if not all(isinstance(doc, dict) for doc in (
            canonical, representation, custom_assets, population_assets_raw,
            source_palette, surfaces, station,
            requirements)):
        raise ValueError("pre-assembly candidate inputs have invalid document shapes")

    blockers: list[dict] = []

    def block(kind: str, subject: str, reason: str) -> None:
        blockers.append({"kind": kind, "subject": subject, "reason": reason})

    # FL-4876 PLACE-FRONT-01. What a non-population owner stage is actually
    # handed. This gate runs before emission and cannot watch the lowerer
    # run, but it can verify the handoff the lowerer depends on: the owner's
    # plan-by-owner slice must carry the row, and the stage must be one the
    # production runner is proven to invoke. Without the slice directory this
    # check cannot be made at all, so the row keeps refusing.
    owner_slice_features: dict[str, set[str]] = {}
    owner_slice_absent: set[str] = set()
    for _stage, _slice_name in NON_POPULATION_OWNER_SLICES.items():
        if plan_by_owner_dir is None:
            owner_slice_absent.add(_stage)
            continue
        _slice_path = Path(plan_by_owner_dir) / _slice_name
        if not _slice_path.is_file():
            owner_slice_absent.add(_stage)
            continue
        _slice_doc = _load(_slice_path)
        owner_slice_features[_stage] = {
            str(_srow.get("feature_id"))
            for _srow in (_slice_doc.get("rows", [])
                          if isinstance(_slice_doc, dict) else [])
            if _srow.get("feature_id")
        }

    canonical_ids = {
        str(row["feature_id"])
        for row in canonical.get("canonical_features", [])
        if row.get("feature_id")
    }
    primary_rows: dict[str, dict] = {}
    unmapped_rows: list[str] = []
    representation_rows_by_feature: dict[str, list[dict]] = defaultdict(list)
    for row in representation.get("rows", []):
        feature_id = str(row.get("feature_id") or "")
        if feature_id:
            representation_rows_by_feature[feature_id].append(row)
        if not row.get("primary"):
            continue
        if not feature_id:
            block("representation_identity", "<missing>",
                  "primary representation has no feature_id")
            continue
        if feature_id in primary_rows:
            block("representation_identity", feature_id,
                  "feature has more than one primary representation")
            continue
        primary_rows[feature_id] = row
        if feature_id not in canonical_ids:
            # FL-4876. The planner deliberately mints one primary row per
            # triage-unmapped source_ref, so that a thing triage could not map
            # is recorded rather than silently absent. Its expected_primary is
            # len(by_fid) + len(unmapped). Those rows are not dangling
            # references and never were; this gate simply held a contract the
            # planner did not share.
            #
            # The exemption is the planner's own declared shape, imported
            # rather than guessed, and every field must match. An
            # "unmapped:"-prefixed id that does not carry the whole contract
            # is still an identity error, so this narrows the check rather
            # than loosening it: a row that merely claims to be an omission
            # gets nothing.
            if (feature_id.startswith(UNMAPPED_FEATURE_PREFIX)
                    and all(row.get(field) == value for field, value
                            in UNMAPPED_ROW_CONTRACT.items())):
                # FL-4876. Only an annotation or a legal line is nothing.
                # This lane's five include man_made=bridge and
                # landuse=brownfield; exempting those silenced the only
                # complaint about a bridge missing from the world.
                if row.get("unmapped_kind") == UNMAPPED_NON_PHYSICAL:
                    unmapped_rows.append(feature_id)
                    continue
                block("unregistered_physical_feature", feature_id,
                      "triage could not map a physical feature and the "
                      "registry carries no class for it, so nothing will "
                      "reconstruct it; tags="
                      + json.dumps(row.get("unmapped_tags") or {},
                                   sort_keys=True, ensure_ascii=False))
                continue
            block("representation_identity", feature_id,
                  "primary representation feature is absent from canonical world")

    # FL-4809 R2 -- the approval cross-check.  Every refusal below exists
    # because the plan row and the waiver document are two different
    # documents, and the gate's job is to refuse to take the first one's word
    # about the second.  A missing document is a blocker rather than a pass:
    # "no document supplied" is precisely the state in which an unbacked claim
    # is invisible, so treating it as clean would make the check optional for
    # exactly the run that needs it.
    waiver_claims = _authored_waiver_claims(representation)
    waiver_rows: list[dict] | None = None
    if waiver_claims and authored_waivers_path is None:
        claiming = sorted({feature_id for feature_id, _, _ in waiver_claims})
        block(WAIVER_PROVENANCE_KIND, claiming[0] if len(claiming) == 1
              else "authored_waivers",
              "WAIVER_DOCUMENT_ABSENT: "
              f"{len(waiver_claims)} representation row(s) claim an authored "
              "waiver but no authored-waiver document was supplied to this "
              "run, so no approval can be resolved; claiming feature(s): "
              + ", ".join(claiming)
              + "; claimed approval_record(s): "
              + ", ".join(sorted({record for _, record, _ in waiver_claims})))
    elif waiver_claims:
        try:
            waiver_rows = load_waiver_document(authored_waivers_path)
        except (AuthoredWaiverInvalid, KeyError, OSError, TypeError,
                ValueError) as exc:
            block(WAIVER_PROVENANCE_KIND, str(authored_waivers_path),
                  "WAIVER_DOCUMENT_UNREADABLE: the authored-waiver document "
                  "supplied to this run cannot be validated, so no claimed "
                  f"approval can be resolved: {exc}")

    if waiver_rows is not None:
        waiver_by_record: dict[str, dict] = {}
        waiver_by_feature: dict[str, list[dict]] = defaultdict(list)
        for waiver_row in waiver_rows:
            if not isinstance(waiver_row, dict):
                continue
            record = waiver_row.get("approval_record")
            if isinstance(record, str) and record:
                waiver_by_record.setdefault(record, waiver_row)
            waiver_by_feature[str(waiver_row.get("feature_id") or "")].append(
                waiver_row)
        for feature_id, record, row in waiver_claims:
            waiver = waiver_by_record.get(record)
            if waiver is None:
                # A proposal carries no approval_record -- the loader refuses
                # one -- so a plan claiming an approval for a feature whose
                # only waiver is a proposal lands here.  Name the proposal
                # rather than reporting an unknown record: "you invented this
                # record" and "you promoted a proposal" are different faults
                # and the operator has to fix different things.
                undecided = [
                    candidate_row
                    for candidate_row in waiver_by_feature.get(feature_id, [])
                    if candidate_row.get("status") != "approved"]
                if undecided:
                    block(WAIVER_PROVENANCE_KIND, feature_id,
                          "WAIVER_APPROVAL_NOT_APPROVED: representation "
                          f"row for {feature_id} claims approval_record "
                          f"{record!r}, but waiver "
                          f"{str(undecided[0].get('waiver_id'))!r} is the "
                          "only waiver this document carries for "
                          f"{feature_id} and its status is "
                          f"{str(undecided[0].get('status'))!r}; a waiver "
                          "that is not approved discharges nothing")
                else:
                    block(WAIVER_PROVENANCE_KIND, feature_id,
                          "WAIVER_APPROVAL_RECORD_UNKNOWN: representation "
                          f"row for {feature_id} claims approval_record "
                          f"{record!r}, which resolves to no waiver in the "
                          "authored-waiver document supplied to this run")
                continue
            if waiver.get("status") != "approved":
                block(WAIVER_PROVENANCE_KIND, feature_id,
                      "WAIVER_APPROVAL_NOT_APPROVED: representation row for "
                      f"{feature_id} claims approval_record {record!r}, which "
                      f"resolves to waiver {str(waiver.get('waiver_id'))!r} "
                      f"with status {str(waiver.get('status'))!r}; only an "
                      "approved waiver discharges an omission")
                continue
            waived_feature = str(waiver.get("feature_id") or "")
            if waived_feature != feature_id:
                block(WAIVER_PROVENANCE_KIND, feature_id,
                      "WAIVER_FEATURE_MISMATCH: representation row for "
                      f"{feature_id} claims approval_record {record!r}, which "
                      f"resolves to waiver {str(waiver.get('waiver_id'))!r} "
                      f"approved for {waived_feature!r}; an approval to omit "
                      "one feature is not an approval to omit another")
                continue
            claimed_digest = row.get("waiver_conditions_sha256")
            if claimed_digest is None:
                # emit_waived always writes this field next to the claim, so a
                # row carrying the claim without it did not come from that
                # code path.  Skipping the binding here would let the one
                # check that ties an approval to the world it was approved
                # against be removed by deleting a field.
                block(WAIVER_PROVENANCE_KIND, feature_id,
                      "WAIVER_CONDITIONS_UNBOUND: representation row for "
                      f"{feature_id} claims approval_record {record!r} but "
                      "carries no waiver_conditions_sha256, so the approval "
                      "cannot be bound to the conditions it was given under")
                continue
            approved_digest = waiver.get("conditions_sha256")
            if str(claimed_digest) != str(approved_digest):
                block(WAIVER_PROVENANCE_KIND, feature_id,
                      "WAIVER_CONDITIONS_DIGEST_MISMATCH: representation row "
                      f"for {feature_id} claims approval_record {record!r} "
                      f"under conditions {str(claimed_digest)!r}, but waiver "
                      f"{str(waiver.get('waiver_id'))!r} was approved under "
                      f"{str(approved_digest)!r}; the approver agreed to a "
                      "different world than the one this plan presents")

    dispatch = _dispatch_documents(dispatch_dir)
    for owner in ("linear", "fixtures", "custom"):
        try:
            require_owner(dispatch[owner], owner)
        except (KeyError, TypeError, ValueError) as exc:
            block("dispatch_contract", owner, str(exc))

    try:
        population_assets, asset_by_placement, asset_by_logical = \
            load_asset_plan(
                population_asset_plan_path,
                Path(str(population_assets_raw.get("asset_root") or "")))
    except (KeyError, OSError, TypeError, ValueError) as exc:
        population_assets = population_assets_raw
        asset_by_placement = {}
        asset_by_logical = {}
        block("population_asset_contract", "population", str(exc))
    asset_inputs = population_assets.get("inputs") or {}
    declared_dispatch_hashes = asset_inputs.get("dispatch_sha256") or {}
    for owner in OWNERS:
        dispatch_path = Path(dispatch_dir) / f"population_plan.{owner}.json"
        if declared_dispatch_hashes.get(owner) != _sha256(dispatch_path):
            block("population_asset_provenance", owner,
                  "asset plan was not authored from this dispatch slice")
    if asset_inputs.get("custom_asset_plan_sha256") != _sha256(
            custom_asset_plan_path):
        block("population_asset_provenance", "custom",
              "asset plan was not authored from this custom specification")
    if asset_inputs.get("source_palette_sha256") != _sha256(
            source_palette_path):
        block("population_asset_provenance", "facade_appearance",
              "asset plan was not authored from this source palette")

    placements_by_feature: dict[str, list[dict]] = defaultdict(list)
    placement_ids: set[str] = set()
    owner_counts: Counter[str] = Counter()
    for owner, document in dispatch.items():
        for carrier in document.get("carriers", []):
            for placement in carrier.get("placements", []):
                placement_id = str(placement.get("placement_id") or "")
                if not placement_id or placement_id in placement_ids:
                    block("dispatch_identity", placement_id or "<missing>",
                          "placement identity is missing or duplicated")
                    continue
                placement_ids.add(placement_id)
                owner_counts[owner] += 1
                decision = placement.get("representation_decision") or {}
                feature_id = str(decision.get("feature_id") or "")
                if feature_id:
                    placements_by_feature[feature_id].append(placement)

    def placements_for_representation(row: dict) -> list[dict]:
        representation_id = str(row.get("representation_id") or "")
        feature_id = str(row.get("feature_id") or "")
        return [
            placement for placement in placements_by_feature.get(feature_id, [])
            if str((placement.get("representation_decision") or {}).get(
                "representation_id") or "") == representation_id
        ]

    def owner_gap(row: dict) -> tuple[str, str] | None:
        """``(blocker_kind, reason)`` when nothing is known to build this row.

        FL-4876 PLACE-FRONT-01. One test, two callers. The primary sweep and
        the required-place check both asked the same question and only one of
        them had been taught that ``place_fronts`` is not a population owner,
        so the required-place blocker went on reporting "no matching
        population placement" for rows population is never handed. Two copies
        of a question is how one of them stays wrong.
        """
        feature_id = str(row.get("feature_id") or "")
        owner_stage = str(row.get("owner_stage") or "")
        if owner_stage in NON_POPULATION_OWNER_STAGES:
            # The row is owned by an emission stage. This gate runs before
            # emission, so it verifies the handoff the emitter depends on:
            # the stage must be one the production runner is proven to
            # invoke, and the owner's plan slice -- the exact rows the runner
            # passes that lowerer -- must carry this row. Neither condition
            # is a rename: remove the runner call and the wiring test fails;
            # drop the row from the slice and this blocker returns.
            if owner_stage not in WIRED_NON_POPULATION_OWNERS:
                return ("unwired_representation_owner",
                        f"representation is owned by {owner_stage!r}, whose "
                        f"emitter is {NON_POPULATION_OWNER_STAGES[owner_stage]}; "
                        "no production runner invocation of that emitter is "
                        "declared, so nothing is known to emit this row")
            if owner_stage in owner_slice_absent:
                return ("unwired_representation_owner",
                        f"representation is owned by {owner_stage!r}, but its "
                        f"plan slice ({NON_POPULATION_OWNER_SLICES[owner_stage]}) "
                        "was not supplied to this gate, so the handoff to "
                        f"{NON_POPULATION_OWNER_STAGES[owner_stage]} cannot "
                        "be verified")
            if feature_id not in owner_slice_features.get(owner_stage, set()):
                return ("unwired_representation_owner",
                        f"representation is owned by {owner_stage!r} but does "
                        f"not appear in the {NON_POPULATION_OWNER_SLICES[owner_stage]} "
                        "slice the runner hands that owner, so its emitter "
                        "will never see this row")
            return None
        if owner_stage and owner_stage not in POPULATION_OWNER_STAGES:
            return ("unwired_representation_owner",
                    f"representation names owner_stage {owner_stage!r}, "
                    "which is neither a population owner nor a declared "
                    "emission owner, so no stage is known to emit it")
        if not placements_for_representation(row):
            return ("population_owner",
                    "emitting representation has no matching logical "
                    "population placement")
        return None

    expected_asset_placements = {
        str(placement["placement_id"])
        for owner in ("fixtures", "custom")
        for carrier in dispatch[owner].get("carriers", [])
        for placement in carrier.get("placements", [])
    }
    expected_logical_assets = {
        str(logical["logical_instance_id"])
        for carrier in dispatch["linear"].get("carriers", [])
        for placement in carrier.get("placements", [])
        for logical in placement.get("logical_realizations", [])
    }
    for placement_id in sorted(expected_asset_placements - set(asset_by_placement)):
        block("population_asset_coverage", placement_id,
              "fixture/custom placement has no authored AKM")
    for placement_id in sorted(set(asset_by_placement) - expected_asset_placements):
        block("population_asset_coverage", placement_id,
              "authored AKM has no fixture/custom placement")
    for logical_id in sorted(expected_logical_assets - set(asset_by_logical)):
        block("population_asset_coverage", logical_id,
              "linear logical instance has no authored AKM")
    for logical_id in sorted(set(asset_by_logical) - expected_logical_assets):
        block("population_asset_coverage", logical_id,
              "authored AKM has no linear logical instance")

    # Terminal placements author nothing, so the coverage sets above skip
    # them entirely. That exclusion is correct and is also the whole risk:
    # it means terminal is the one owner where a placement can disappear
    # without any gate noticing. Ask each one whether its omission was
    # decided. Unknown states are refused rather than assumed permitted --
    # a state this gate has never heard of is the case most likely to be a
    # new way of losing a placement quietly.
    for carrier in dispatch["terminal"].get("carriers", []):
        for placement in carrier.get("placements", []):
            placement_id = str(placement.get("placement_id") or "<missing>")
            terminal_state = placement.get("terminal_state")
            if not isinstance(terminal_state, dict):
                block(TERMINAL_DEBT_KIND, placement_id,
                      "terminal placement carries no terminal_state, so its "
                      "omission is unexplained and authors nothing")
                continue
            state = str(terminal_state.get("state") or "")
            if state in TERMINAL_DISCHARGING_STATES:
                continue
            detail = (f"category={terminal_state.get('category')!r} "
                      f"reason={terminal_state.get('reason')!r} "
                      f"module_id={placement.get('module_id')!r}")
            if state in DEBT_DISPOSITIONS:
                block(TERMINAL_DEBT_KIND, placement_id,
                      f"terminal placement carries unresolved {state} debt "
                      "and authors no geometry; an owed observation is not a "
                      "decided omission: " + detail)
            else:
                block(TERMINAL_DEBT_KIND, placement_id,
                      f"terminal placement has unknown terminal state "
                      f"{state!r}, which this gate cannot treat as a decided "
                      "omission: " + detail)
    declared_totals = {
        int((document.get("dispatch") or {}).get("placements_total", -1))
        for document in dispatch.values()
    }
    if declared_totals != {len(placement_ids)}:
        block("dispatch_identity", "population",
              "owner slices disagree with the complete placement denominator: "
              f"declared={sorted(declared_totals)} actual={len(placement_ids)}")
    declared_owner_counts = {
        owner: int(((dispatch[owner].get("dispatch") or {}).get(
            "placements_for_owner", -1)))
        for owner in OWNERS
    }
    if declared_owner_counts != {
            owner: owner_counts.get(owner, 0) for owner in OWNERS}:
        block("dispatch_identity", "population",
              "owner slice counts differ from contained placements")

    custom_asset_ids = {
        str(row["placement_id"])
        for row in custom_assets.get("rows", [])
        if row.get("placement_id")
    }
    custom_dispatch_ids = {
        str(placement["placement_id"])
        for carrier in dispatch["custom"].get("carriers", [])
        for placement in carrier.get("placements", [])
    }
    for placement_id in sorted(custom_dispatch_ids - custom_asset_ids):
        block("asset_contract", placement_id,
              "custom placement has no authored asset specification")
    for placement_id in sorted(custom_asset_ids - custom_dispatch_ids):
        block("asset_contract", placement_id,
              "custom asset specification has no dispatched placement")

    surface_by_feature = {
        str(row["feature_id"]): row
        for row in surfaces.get("rows", []) if row.get("feature_id")
    }
    buildings = _building_ids(building_specs)
    disposition_counts: Counter[str] = Counter()
    for feature_id, row in primary_rows.items():
        disposition = str(row.get("disposition") or "")
        disposition_counts[disposition] += 1
        if row.get("evidence_complete") is False:
            block("evidence_debt", feature_id,
                  "primary representation evidence is incomplete")
        if row.get("attachment_state") == "spatial_reconciliation_debt":
            block("attachment_debt", feature_id,
                  "primary representation has spatial reconciliation debt")
        if disposition in DEBT_DISPOSITIONS:
            block("representation_debt", feature_id,
                  f"primary disposition is {disposition}")
            continue
        if disposition == "terrain_mass":
            if feature_id not in buildings:
                block("building_owner", feature_id,
                      "terrain-mass representation has no building specification")
        elif disposition == "surface_decal":
            surface = surface_by_feature.get(feature_id)
            if surface is None:
                block("surface_owner", feature_id,
                      "surface representation has no stamp plan row")
            elif surface.get("terminal_state") != "painted":
                block("surface_owner", feature_id,
                      "surface plan is not serialization-ready: "
                      + str(surface.get("terminal_state")))
        elif disposition in {"mesh_fixture", "mesh_facade"}:
            gap = owner_gap(row)
            if gap is not None:
                block(gap[0], feature_id, gap[1])
        elif disposition not in NON_POPULATION_DISPOSITIONS:
            block("representation_disposition", feature_id,
                  f"unknown or unowned disposition {disposition!r}")

    # A building mass without its secondary facade representation is the
    # barren-map regression.  Gate the complete building denominator before
    # A3D exists; the HTML viewer and Classic are not allowed to discover it.
    building_facades = {
        feature_id: [
            row for row in representation_rows_by_feature.get(feature_id, [])
            if not row.get("primary")
            and row.get("disposition") == "mesh_facade"]
        for feature_id in buildings
    }
    for feature_id, facade_rows in sorted(building_facades.items()):
        if len(facade_rows) != 1:
            block("building_facade_denominator", feature_id,
                  f"building requires exactly one secondary mesh_facade; "
                  f"found {len(facade_rows)}")
            continue
        facade = facade_rows[0]
        if facade.get("attachment_state") != "resolved" or \
                (facade.get("attachment") or {}).get("anchor_kind") != \
                "building_face":
            block("building_facade_attachment", feature_id,
                  "secondary facade has no resolved canonical building face")
        facade_placements = placements_for_representation(facade)
        if not facade_placements:
            block("building_facade_population", feature_id,
                  "secondary facade has no matching dispatched logical "
                  "placement")
        for placement in facade_placements:
            placement_id = str(placement.get("placement_id") or "")
            asset = asset_by_placement.get(placement_id) or {}
            appearance = asset.get("appearance_contract")
            if not isinstance(appearance, dict):
                block("building_facade_appearance", placement_id,
                      "facade AKM has no source/synthesized appearance "
                      "contract")
            elif not appearance.get("colour_method"):
                block("building_facade_appearance", placement_id,
                      "facade appearance has no provenance method")

    for row in surfaces.get("rows", []):
        if row.get("terminal_state") != "painted":
            block("surface_debt", str(row.get("feature_id") or "<missing>"),
                  str(row.get("reason") or row.get("terminal_state")))

    for entry in requirements.get("hero_facades", []):
        hero_id = str(entry.get("id") or "<missing>")
        requirement = str(entry.get("requirement") or "")
        feature_id = str(entry.get("feature_id") or "")
        if not feature_id:
            block("required_facade_identity", hero_id,
                  "hero facade has no canonical feature_id; source coverage "
                  "alone cannot prove a planned facade")
            continue
        if requirement == "accepted_station_reconstruction":
            station_ids = {
                str(placement.get("feature_id") or "")
                for placement in station.get("placements") or []}
            if feature_id not in station_ids:
                block("required_facade", hero_id,
                      f"station plan has no accepted placement for {feature_id}")
            continue
        if requirement == "street_derived" \
                and entry.get("source_coverage") is not True:
            block("required_facade", hero_id,
                  "street-derived hero lacks accepted source coverage")
        if requirement not in {
                "street_derived", "rule_backed_reconstruction"}:
            block("required_facade", hero_id,
                  f"unknown hero reconstruction requirement {requirement!r}")
            continue
        facade_rows = [
            row for row in representation_rows_by_feature.get(feature_id, [])
            if row.get("disposition") == "mesh_facade"]
        if not facade_rows:
            block("required_facade", hero_id,
                  f"canonical hero {feature_id} has no mesh_facade representation")
        elif not any(placements_for_representation(row)
                     for row in facade_rows):
            block("required_facade", hero_id,
                  f"canonical hero {feature_id} has no matching population "
                  "placement")

    composition = requirements.get("route_composition_requirements") or {}
    required_places = list(composition.get("required_places") or [])
    required_fronts = list((requirements.get("required_resolved_archetypes")
                            or {}).get("place_fronts") or [])
    # FL-4876. One requirement per place, not one per list it appears in.
    # required_places and required_resolved_archetypes.place_fronts overlap on
    # this lane, and concatenating them produced two identical blockers for
    # each shared id -- 6 records for 4 places. A duplicated record inflates
    # the refusal without naming a second thing to fix.
    required_place_ids: list[str] = []
    for entry in required_places + required_fronts:
        feature_id = str(entry.get("feature_id") or "")
        if feature_id not in required_place_ids:
            required_place_ids.append(feature_id)
    for feature_id in required_place_ids:
        row = primary_rows.get(feature_id)
        if row is None:
            block("required_place", feature_id or "<missing>",
                  "required place has no primary representation")
        elif row.get("disposition") not in EMITTING_DISPOSITIONS:
            block("required_place", feature_id,
                  "required place does not have an emitting representation")
        else:
            # Ask the row's real owner, through the same test the primary
            # sweep uses. Demanding a population placement here is what made
            # a place front owned by an emission stage report a population
            # gap it could never have.
            gap = owner_gap(row)
            if gap is not None:
                block("required_place", feature_id,
                      "required place is unsatisfied: " + gap[1])

    for entry in (requirements.get("required_resolved_archetypes")
                  or {}).get("fixtures", []):
        template_id = str(entry.get("template_id") or "")
        matches = [
            row for feature_id, row in primary_rows.items()
            if row.get("asset_match") == f"catalog:{template_id}"
            and row.get("disposition") == "mesh_fixture"
            and placements_by_feature.get(feature_id)
        ]
        if not matches:
            block("required_fixture_archetype", template_id or "<missing>",
                  "required fixture template has no planned physical instance")

    station_placements = station.get("placements") or []
    if station.get("lowering_owner") != "station_facade" \
            or len(station_placements) != 1:
        block("station_facade", "station_facade",
              "station facade plan is absent or does not have one placement")
    station_asset = station.get("asset") or {}
    station_mesh = Path(str(station_asset.get("mesh_path") or ""))
    if (not station_mesh.is_file()
            or station_mesh.stat().st_size <= 0
            or _sha256(station_mesh) != station_asset.get("mesh_sha256")):
        block("station_facade_asset", "station_facade",
              "station facade mesh is absent, empty, or differs from its "
              "preassembly asset contract")

    infrastructure_owned: list[dict] = []
    planned_module_counts = Counter(
        str(placement.get("module_id") or "")
        for document in dispatch.values()
        for carrier in document.get("carriers", [])
        for placement in carrier.get("placements", [])
    )
    station_component_counts = Counter(
        str(component.get("semantic_class") or "")
        for placement in station_placements
        for component in placement.get("components", []))
    for semantic, minimum_value in sorted(
            (composition.get("required_infrastructure_counts") or {}).items()):
        minimum = int(minimum_value)
        modules = INFRASTRUCTURE_REQUIREMENT_MODULES.get(semantic)
        if modules is None:
            block("infrastructure_contract", semantic,
                  "required infrastructure semantic has no module mapping")
            continue
        observed = sum(planned_module_counts[module] for module in modules)
        observed += station_component_counts[semantic]
        owner_stage = INFRASTRUCTURE_OWNER_SEMANTICS.get(semantic)
        if observed < minimum and owner_stage is not None:
            if owner_stage in WIRED_NON_POPULATION_OWNERS:
                infrastructure_owned.append({
                    "semantic": semantic, "required": minimum,
                    "owner_stage": owner_stage,
                    "emitter": NON_POPULATION_OWNER_STAGES[owner_stage],
                })
                continue
            block("unwired_representation_owner", semantic,
                  f"required infrastructure semantic is owned by "
                  f"{owner_stage!r}, whose emitter is "
                  f"{NON_POPULATION_OWNER_STAGES.get(owner_stage)}; no "
                  "production runner invocation of that emitter is declared")
            continue
        if observed < minimum:
            block("required_infrastructure", semantic,
                  f"planned count {observed} is below required {minimum}")

    route, _route_receipt = _project_route(route_path, terrain_metadata_path)
    scale = float(terrain_metadata["content_scale"])
    route_length_m = sum(
        math.dist(a, b) for a, b in zip(route, route[1:])) / scale
    coverage_rows = []
    coverage_requirements = composition.get("streetscape_route_coverage") or {}
    population_placements = [
        placement
        for document in dispatch.values()
        for carrier in document.get("carriers", [])
        for placement in carrier.get("placements", [])
    ]
    for semantic, rule in sorted(coverage_requirements.items()):
        modules = STREETSCAPE_REQUIREMENT_MODULES.get(semantic)
        if modules is None:
            block("route_density_contract", semantic,
                  "required streetscape semantic has no representation-module mapping")
            continue
        bin_m = float(rule["bin_m"])
        max_offset_m = float(rule["max_route_offset_m"])
        minimum_fraction = float(rule["minimum_covered_fraction"])
        bin_count = max(1, math.ceil(route_length_m / bin_m))
        covered: set[int] = set()
        candidate_count = 0
        for placement in population_placements:
            archetypes = STREETSCAPE_REQUIREMENT_ARCHETYPES.get(
                semantic, frozenset())
            resolved = placement.get("resolved_archetype") or {}
            if (placement.get("module_id") not in modules
                    and str(resolved.get("archetype_subtype") or "")
                    not in archetypes):
                continue
            for point in _sample_placement_world_points(
                    placement, terrain_metadata, bin_m / 2.0):
                hit = _nearest_route(point, route, scale)
                if hit["offset_m"] > max_offset_m:
                    continue
                candidate_count += 1
                covered.add(min(
                    bin_count - 1, int(hit["progress_m"] // bin_m)))
        fraction = len(covered) / bin_count
        coverage_rows.append({
            "semantic_class": semantic,
            "module_ids": sorted(modules),
            "route_bins": bin_count,
            "covered_bins": sorted(covered),
            "covered_fraction": round(fraction, 6),
            "minimum_covered_fraction": minimum_fraction,
            "candidate_count_near_route": candidate_count,
        })
        if fraction < minimum_fraction:
            block("route_density", semantic,
                  f"planned coverage {fraction:.6f} is below required "
                  f"{minimum_fraction:.6f}")

    blockers.sort(key=lambda row: (
        str(row["kind"]), str(row["subject"]), str(row["reason"])))
    return {
        "schema": SCHEMA,
        "infrastructure_owned_requirements": infrastructure_owned,
        "inputs": {
            "canonical_sha256": _sha256(canonical_path),
            "representation_plan_sha256": _sha256(representation_plan_path),
            "custom_asset_plan_sha256": _sha256(custom_asset_plan_path),
            "population_asset_plan_sha256": _sha256(
                population_asset_plan_path),
            "source_palette_sha256": _sha256(source_palette_path),
            "surface_stamp_plan_sha256": _sha256(surface_stamp_plan_path),
            "station_facade_plan_sha256": _sha256(station_facade_plan_path),
            "building_specs_sha256": _sha256(building_specs_path),
            "requirements_sha256": _sha256(requirements_path),
            "route_sha256": _sha256(route_path),
            "terrain_metadata_sha256": _sha256(terrain_metadata_path),
            **({"authored_waivers_sha256": _sha256(authored_waivers_path)}
               if authored_waivers_path is not None else {}),
        },
        "counts": {
            "canonical_features": len(canonical_ids),
            "primary_representations": len(primary_rows),
            "representation_by_disposition": dict(sorted(
                disposition_counts.items())),
            "population_placements": len(placement_ids),
            "population_by_owner": dict(sorted(owner_counts.items())),
            "surface_rows": len(surface_by_feature),
            "building_specs": len(buildings),
            "building_facades": sum(
                len(rows) for rows in building_facades.values()),
            "authored_waiver_claims": len(waiver_claims),
            "triage_unmapped_rows": len(unmapped_rows),
            "terminal_placements": sum(
                len(carrier.get("placements", []))
                for carrier in dispatch["terminal"].get("carriers", [])),
            "blockers": len(blockers),
        },
        "streetscape_route_coverage": coverage_rows,
        "blockers": blockers,
        "ready_for_a3d_assembly": not blockers,
    }


PRESERVED_DIR = (Path(__file__).resolve().parents[2]
                 / "artifacts/experiments/fl4809_preassembly_candidate")


def preserve_receipt(output: Path) -> Path:
    """Copy a produced candidate into a tracked, content-addressed location.

    ``.run/`` is gitignored, so a gate result written only there is evidence
    that disappears with the working tree -- and this is the receipt that
    says which blockers stopped assembly, which is exactly what a later
    reader needs and cannot reconstruct. Runs inside the producing
    execution, before the command reports success, and refuses to overwrite
    preserved bytes with different ones. Same law as
    compile_route_importance.preserve_receipt; one hashing rule, not two.
    """
    payload = output.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    preserved = (PRESERVED_DIR / "preserved"
                 / f"{digest[:16]}.preassembly_candidate.json")
    preserved.parent.mkdir(parents=True, exist_ok=True)
    if preserved.is_file():
        if preserved.read_bytes() != payload:
            raise RuntimeError(
                "preserved preassembly evidence disagrees with this run at "
                f"the same content address: {preserved}")
        return preserved
    preserved.write_bytes(payload)
    return preserved


def write(*, out_path: Path, **kwargs) -> dict:
    document = build(**kwargs)
    # FL-4876. The receipt hashed every input but never recorded the command
    # that produced it, so reproducing a preserved measurement meant digging
    # the invocation out of an old adhoc script. A receipt that cannot say
    # how it was made is not a receipt anyone else can act on.
    document["invocation"] = {
        "argv": list(sys.argv),
        "cwd": str(Path.cwd()),
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    # A refused gate is the case where the receipt matters most, so this
    # preserves on both outcomes and before reporting either.
    document["preserved_path"] = str(preserve_receipt(out_path))
    return document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--representation-plan", type=Path, required=True)
    parser.add_argument("--dispatch-dir", type=Path, required=True)
    parser.add_argument("--custom-asset-plan", type=Path, required=True)
    parser.add_argument("--population-asset-plan", type=Path, required=True)
    parser.add_argument("--source-palette", type=Path, required=True)
    parser.add_argument("--surface-stamp-plan", type=Path, required=True)
    parser.add_argument("--station-facade-plan", type=Path, required=True)
    parser.add_argument("--building-specs", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--terrain-metadata", type=Path, required=True)
    # Optional for the same reason the planner's flag is: a run may carry no
    # authored waiver at all. It is not optional for a run whose plan claims
    # one -- that combination is a blocker, not a pass.
    parser.add_argument("--authored-waivers", type=Path)
    # FL-4876 PLACE-FRONT-01. The plan-by-owner slices the runner hands the
    # non-population emitters. Optional only so a lane with no such owner can
    # omit it; a lane that has one and omits this keeps refusing, because the
    # handoff cannot be verified without the bytes that constitute it.
    parser.add_argument("--plan-by-owner-dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = write(
        canonical_path=args.canonical,
        representation_plan_path=args.representation_plan,
        dispatch_dir=args.dispatch_dir,
        custom_asset_plan_path=args.custom_asset_plan,
        population_asset_plan_path=args.population_asset_plan,
        source_palette_path=args.source_palette,
        surface_stamp_plan_path=args.surface_stamp_plan,
        station_facade_plan_path=args.station_facade_plan,
        building_specs_path=args.building_specs,
        requirements_path=args.requirements,
        route_path=args.route,
        terrain_metadata_path=args.terrain_metadata,
        authored_waivers_path=args.authored_waivers,
        plan_by_owner_dir=args.plan_by_owner_dir,
        out_path=args.out,
    )
    return 0 if document["ready_for_a3d_assembly"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
