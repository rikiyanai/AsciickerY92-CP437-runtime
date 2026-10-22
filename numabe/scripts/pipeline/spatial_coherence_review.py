#!/usr/bin/env python3
"""Authored spatial adjudications and bounded JEV review input (FL-4876).

The operator owns accepted placement corrections.  JEV may choose a bounded
review action, but its output is only a proposal: deterministic pipeline code
must turn an accepted action into geometry and a fresh visual check must review
the rebuilt result.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


DECISIONS_SCHEMA = "y92.spatial_coherence_decisions.v1"
JEV_INPUT_SCHEMA = "y92.spatial_coherence_jev_input.v1"

ACTION_OPTIONS = {
    "accept_current": "Placement is visually correct; classify the named check as inapplicable.",
    "orientation_axis_180": "Treat the object's long axis as undirected modulo 180 degrees.",
    "set_carrier_lateral_offset": "Use an evidence-backed signed distance from the named carrier.",
    "set_minimum_span_endpoint_lateral": "Move the span endpoint nearest its carrier to a reviewed roadside offset.",
    "offset_from_mass_boundary": "Move a ground object outward from a named building boundary.",
    "offset_facade_normal": "Move a facade outward along its already-authored normal.",
    "change_host": "Bind the object to a different reviewed canonical host.",
    "reclassify": "Change the semantic module before placement is recomputed.",
    "delete_duplicate": "Remove a proven duplicate while preserving its source evidence.",
    "unresolved": "Evidence is insufficient; require human review or more evidence.",
}

# Edge-shaped observations are expected to sit beside a carrier, not on its
# centre line.  Zero is only a review trigger, never proof of error: some
# guardrails legitimately follow a mapped footpath or edge line.
EDGE_OFFSET_MODULES = frozenset({
    "concrete_block_wall",
    "concrete_retaining_edge",
    "curb_and_open_gutter",
    "ivy_wall",
    "lattice_boundary_fence",
    "mesh_fence",
    "metal_boundary_fence",
    "open_and_covered_drain",
    "pedestrian_guardrail_continuous",
    "pedestrian_guardrail_vertical_bar_continuous",
    "platform_railing",
    "rail_corridor_fence",
    "rail_corridor_mesh_fence",
    "stone_lined_water_channel",
    "trimmed_hedge",
    "white_railguard",
})

APPLYABLE_ACTIONS = frozenset({
    "orientation_axis_180",
    "set_carrier_lateral_offset",
    "set_minimum_span_endpoint_lateral",
    "offset_from_mass_boundary",
    "offset_facade_normal",
})
DECIDED_ACTIONS = APPLYABLE_ACTIONS | {"accept_current"}

_REQUIRED_TEXT = (
    "decision_id", "target_kind", "target_id", "action", "status",
    "decided_by", "decided_on", "decision_record", "rationale",
)


class SpatialDecisionInvalid(ValueError):
    """The authored decision document cannot safely drive placement."""


def _number_in(params: dict, name: str, low: float, high: float,
               *, source: Path, index: int) -> float:
    try:
        value = float(params[name])
    except (KeyError, TypeError, ValueError) as exc:
        raise SpatialDecisionInvalid(
            f"{source} row {index} parameter {name!r} must be numeric") \
            from exc
    if not low < value <= high:
        raise SpatialDecisionInvalid(
            f"{source} row {index} parameter {name!r} must be in "
            f"({low}, {high}]")
    return value


def _validate_decided_parameters(row: dict, params: dict, source: Path,
                                 index: int) -> None:
    action = row["action"]
    target_kind = row["target_kind"]
    if action not in DECIDED_ACTIONS:
        raise SpatialDecisionInvalid(
            f"{source} row {index} action {action!r} has no deterministic "
            "accepted-decision consumer")
    expected_kind = {
        "orientation_axis_180": "instance",
        "set_carrier_lateral_offset": "placement",
        "set_minimum_span_endpoint_lateral": "instance",
        "offset_from_mass_boundary": "placement",
        "offset_facade_normal": "instance",
        "accept_current": "instance",
    }[action]
    # FL-4876 JEV-VISUAL-01: the zero-lateral edge census excludes a
    # placement only through decision_for(decisions, "placement", id), so a
    # reviewed "zero is correct" verdict must be recordable on a placement.
    if action == "accept_current" and target_kind == "placement":
        expected_kind = "placement"
    if target_kind != expected_kind:
        raise SpatialDecisionInvalid(
            f"{source} row {index} action {action!r} requires target_kind "
            f"{expected_kind!r}")
    if action == "set_carrier_lateral_offset":
        _number_in(params, "target_lateral_m", 0.0, 50.0,
                   source=source, index=index)
        if params.get("side") not in ("L", "R"):
            raise SpatialDecisionInvalid(
                f"{source} row {index} carrier offset side must be L or R")
    elif action == "set_minimum_span_endpoint_lateral":
        endpoints = params.get("endpoint_placement_ids")
        if (not isinstance(endpoints, list) or len(endpoints) != 2
                or len({str(value) for value in endpoints}) != 2
                or any(not str(value).strip() for value in endpoints)):
            raise SpatialDecisionInvalid(
                f"{source} row {index} must name two distinct span endpoints")
        # Optional stable identity of the same two endpoints (canonical pole
        # features).  Sequence-numbered obs-pole ids shift when another
        # placement adopts a canonical pole; the canonical id does not.
        cfids = params.get("endpoint_canonical_feature_ids")
        if cfids is not None and (
                not isinstance(cfids, list) or len(cfids) != 2
                or len({str(value) for value in cfids}) != 2
                or any(not str(value).startswith("feature:") for value in cfids)):
            raise SpatialDecisionInvalid(
                f"{source} row {index} endpoint canonical ids must be two distinct feature ids")
        _number_in(params, "target_lateral_m", 0.0, 20.0,
                   source=source, index=index)
    elif action == "offset_from_mass_boundary":
        if not str(params.get("building_feature_id") or "").startswith(
                "feature:building_footprint:"):
            raise SpatialDecisionInvalid(
                f"{source} row {index} must name a building footprint")
        _number_in(params, "outward_distance_m", 0.0, 10.0,
                   source=source, index=index)
    elif action == "offset_facade_normal":
        # FL-4876 COHERENCE-ZERO-01: 6.0 m (was 3.0 m). The station entrance
        # needs a measured 4.4 m along its outward normal to clear the PLATEAU
        # canopy footprint that the OSM entrance node sits inside; the bound
        # still refuses an implausible facade move.
        _number_in(params, "outward_offset_m", 0.0, 6.0,
                   source=source, index=index)
        # FL-4876 STATION-RAIL-01: optional signed slide along the plane's own
        # horizontal tangent (metres), so a plane that the reconstruction put
        # on the rail centreline can be moved to the platform side without
        # changing its facing.  Zero or absent means no slide.
        if "lateral_shift_m" in params:
            try:
                shift = float(params["lateral_shift_m"])
            except (TypeError, ValueError) as exc:
                raise SpatialDecisionInvalid(
                    f"{source} row {index} parameter 'lateral_shift_m' "
                    "must be numeric") from exc
            if not -8.0 <= shift <= 8.0:
                raise SpatialDecisionInvalid(
                    f"{source} row {index} parameter 'lateral_shift_m' "
                    "must be within [-8, 8] metres")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_decisions(path: Path | str | None) -> dict:
    """Load and validate decided rows, indexed by exact target identity."""
    if path is None:
        return {"by_target": {}, "rows": [], "path": None, "sha256": None}
    source = Path(path)
    if not source.is_file():
        raise SpatialDecisionInvalid(
            f"authored spatial decision document is absent: {source}")
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpatialDecisionInvalid(f"{source} is not JSON: {exc}") from exc
    if document.get("schema") != DECISIONS_SCHEMA:
        raise SpatialDecisionInvalid(
            f"{source} schema {document.get('schema')!r}; expected "
            f"{DECISIONS_SCHEMA!r}")
    rows = document.get("decisions")
    if not isinstance(rows, list):
        raise SpatialDecisionInvalid(f"{source} has no decisions list")
    by_target: dict[tuple[str, str], dict] = {}
    decided = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SpatialDecisionInvalid(f"{source} row {index} is not an object")
        for field in _REQUIRED_TEXT:
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise SpatialDecisionInvalid(
                    f"{source} row {index} field {field!r} must be non-empty text")
        if row["action"] not in ACTION_OPTIONS:
            raise SpatialDecisionInvalid(
                f"{source} row {index} action {row['action']!r} is not bounded")
        if row["status"] not in ("decided", "proposed", "superseded"):
            raise SpatialDecisionInvalid(
                f"{source} row {index} has unsupported status {row['status']!r}")
        params = row.get("parameters", {})
        if not isinstance(params, dict):
            raise SpatialDecisionInvalid(
                f"{source} row {index} parameters must be an object")
        if row["status"] != "decided":
            continue
        _validate_decided_parameters(row, params, source, index)
        key = (row["target_kind"], row["target_id"])
        if key in by_target:
            raise SpatialDecisionInvalid(
                f"{source} decides target {key!r} more than once")
        by_target[key] = row
        decided.append(row)
    return {
        "by_target": by_target,
        "rows": decided,
        "path": str(source.resolve()),
        "sha256": _sha256(source),
    }


def decision_for(decisions: dict, target_kind: str,
                 target_id: str | None) -> dict | None:
    if target_id is None:
        return None
    return decisions.get("by_target", {}).get((target_kind, str(target_id)))


def apply_authored_adjudications(rows: list[dict], decisions: dict,
                                 orientation_tolerance_deg: float
                                 ) -> list[dict]:
    """Apply only the exact failure mode named by an operator decision.

    This function deliberately cannot move geometry.  It handles the two
    cases where visual review established that a checker rule is inapplicable
    to one exact emitted instance.  A decision for one failure mode must not
    conceal a different failure on the same instance.
    """
    applied_rows = []
    for row in rows:
        decision = decision_for(
            decisions, "instance", row.get("inst_name"))
        if decision is None:
            continue
        action = decision["action"]
        applied = False
        if action == "orientation_axis_180":
            old = row.get("orientation_residual_deg")
            if old is not None:
                axial = min(abs(float(old)), abs(180.0 - abs(float(old))))
                row["orientation_residual_deg"] = round(axial, 3)
                if (row.get("verdict") == "FAIL"
                        and str(row.get("reason") or "").startswith(
                            "orientation residual ")
                        and axial <= orientation_tolerance_deg):
                    row["verdict"] = "PASS"
                    row["reason"] = (
                        f"axial orientation residual {axial:.3f}deg within "
                        f"{orientation_tolerance_deg}deg; authored decision "
                        f"{decision['decision_id']}")
                    applied = True
        elif action == "accept_current":
            expected = (decision.get("parameters") or {}).get(
                "accepted_failure_kind")
            if (expected is not None and row.get("verdict") == "FAIL"
                    and row.get("collision_state") == expected):
                row["verdict"] = "PASS"
                row["reason"] = (
                    f"{expected} is inapplicable to this exact instance; "
                    f"authored decision {decision['decision_id']}")
                applied = True
        row["authored_spatial_adjudication"] = {
            "decision_id": decision["decision_id"],
            "action": action,
            "applied_to_verdict": applied,
        }
        applied_rows.append({
            "decision_id": decision["decision_id"],
            "inst_name": row.get("inst_name"),
            "action": action,
            "applied_to_verdict": applied,
        })
    return applied_rows


def _options_for(row: dict) -> list[str]:
    collision = str(row.get("collision_state") or "")
    reason = str(row.get("reason") or "").lower()
    if collision.startswith("inside_mass"):
        return ["offset_from_mass_boundary", "change_host", "reclassify",
                "accept_current", "unresolved"]
    if "orientation residual" in reason:
        return ["orientation_axis_180", "change_host", "reclassify",
                "accept_current", "unresolved"]
    if collision in {"span_off_carrier", "span_endpoint_drift"}:
        return ["set_minimum_span_endpoint_lateral", "accept_current",
                "change_host", "unresolved"]
    if row.get("anchor_kind") in {"carrier_chainage", "carrier_span"}:
        return ["set_carrier_lateral_offset", "change_host", "reclassify",
                "accept_current", "unresolved"]
    return ["accept_current", "change_host", "reclassify", "unresolved"]


def _edge_evidence_by_review(document: dict | None) -> dict[str, dict]:
    """Index deterministic photo/LiDAR candidates without treating them as truth."""
    if document is None:
        return {}
    records = document.get("records")
    if not isinstance(records, list):
        raise SpatialDecisionInvalid(
            "spatial candidate evidence must contain a records list")
    indexed = {}
    for index, row in enumerate(records):
        if not isinstance(row, dict) or not str(row.get("review_id") or ""):
            raise SpatialDecisionInvalid(
                f"spatial candidate evidence row {index} has no review_id")
        review_id = str(row["review_id"])
        if review_id in indexed:
            raise SpatialDecisionInvalid(
                f"spatial candidate evidence repeats {review_id}")
        indexed[review_id] = row
    return indexed


def _edge_review_options(candidate: dict | None) -> list[dict]:
    """Offer concrete measured hypotheses rather than an instruction to guess."""
    options = []
    if candidate is not None:
        seen: set[tuple[str, float]] = set()
        preferred = candidate.get("candidate_lateral_m")
        ordered = []
        if preferred is not None:
            ordered.append(("measured_primary", float(preferred)))
        for side in ("L", "R"):
            edge = (candidate.get("inner_edges") or {}).get(side) or {}
            if edge.get("lat_m") is not None:
                ordered.append((f"measured_{side.lower()}_edge",
                                float(edge["lat_m"])))
        for choice, signed_offset in ordered:
            side = "L" if signed_offset < 0.0 else "R"
            distance = round(abs(signed_offset), 3)
            key = (side, distance)
            if distance <= 0.05 or key in seen:
                continue
            seen.add(key)
            edge = (candidate.get("inner_edges") or {}).get(side) or {}
            options.append({
                "choice": choice,
                "action": "set_carrier_lateral_offset",
                "parameters": {
                    "side": side,
                    "target_lateral_m": distance,
                },
                "criterion": (
                    f"Place the edge object {distance:.2f} m on the {side} "
                    "side of the named carrier and keep its long axis parallel "
                    "to that carrier. This candidate comes from the measured "
                    f"photo/LiDAR cross-section; continuity="
                    f"{float(edge.get('continuity') or 0.0):.2f}, "
                    f"photo-box share="
                    f"{float(edge.get('current_run_share_in_photo_box') or 0.0):.2f}."
                ),
            })
    options.extend({"choice": action, "action": action,
                    "criterion": ACTION_OPTIONS[action]}
                   for action in ("accept_current", "change_host",
                                  "reclassify", "unresolved"))
    return options


def build_jev_input(coherence: dict, *, coherence_path: Path,
                    decisions: dict | None = None,
                    population_plan: dict | None = None,
                    population_plan_path: Path | None = None,
                    spatial_candidate_evidence: dict | None = None,
                    spatial_candidate_evidence_path: Path | None = None) -> dict:
    """Project checker failures and plan anomalies onto bounded choices."""
    decisions = decisions or {"by_target": {}, "rows": []}
    jobs = []
    excluded_decision_ids = []
    queued_placement_ids: set[str] = set()
    edge_evidence = _edge_evidence_by_review(spatial_candidate_evidence)
    coherence_jobs = 0
    for row in coherence.get("rows", []):
        if row.get("verdict") != "FAIL":
            continue
        inst_name = row.get("inst_name")
        existing = decision_for(decisions, "instance", inst_name)
        if existing is None:
            for placement_id in row.get("source_placement_ids") or []:
                existing = decision_for(
                    decisions, "placement", str(placement_id))
                if existing is not None:
                    break
        if existing is not None:
            excluded_decision_ids.append(existing["decision_id"])
            continue
        options = _options_for(row)
        jobs.append({
            "review_id": "spatial:" + str(inst_name or row.get("feature_id")),
            "feature_id": row.get("feature_id"),
            "instance_id": inst_name,
            "placement_ids": list(row.get("source_placement_ids") or []),
            "facts": {
                key: row.get(key) for key in (
                    "canonical_host_id", "anchor_kind", "collision_state",
                    "host_distance_m", "attachment_residual_m",
                    "orientation_residual_deg", "reason", "class_group")
            },
            "options": [
                {"choice": action, "criterion": ACTION_OPTIONS[action]}
                for action in options
            ],
            "existing_authored_decision_id": None,
            "visual_evidence": {
                "source_crop_paths": [],
                "plan_overlay_path": None,
                "rebuilt_map_path": None,
                "state": "needs_evidence_renderer",
            },
            "decision_contract": {
                "model_output_is_proposal": True,
                "geometry_output_forbidden": True,
                "human_acceptance_required_before_apply": True,
            },
            "post_jev_visual_verification": {
                "required": True,
                "state": "pending",
                "requires_fresh_rebuild": True,
                "required_views": ["source_crop", "plan_overlay", "rebuilt_map"],
            },
        })
        queued_placement_ids.update(
            str(value) for value in row.get("source_placement_ids") or [])
        coherence_jobs += 1

    plan_anomaly_jobs = 0
    for carrier in (population_plan or {}).get("carriers", []):
        for placement in carrier.get("placements") or []:
            attachment = placement.get("attachment") or {}
            placement_id = str(placement.get("placement_id") or "")
            module_id = str(placement.get("module_id") or "")
            if (not placement_id or placement_id in queued_placement_ids
                    or module_id not in EDGE_OFFSET_MODULES
                    or (placement.get("evidence") or {}).get("kind")
                    != "observation"
                    or attachment.get("record_type") != "attachment"
                    or attachment.get("anchor_kind") not in (
                        "carrier_chainage", "carrier_span")
                    or abs(float(attachment.get("lateral_offset_m") or 0.0))
                    >= 0.05):
                continue
            existing = decision_for(
                decisions, "placement", placement_id)
            if existing is not None:
                excluded_decision_ids.append(existing["decision_id"])
                continue
            review_id = "spatial-plan-zero-offset:" + placement_id
            candidate = edge_evidence.get(review_id)
            measured = None
            if candidate is not None:
                measured = {
                    key: candidate.get(key) for key in (
                        "candidate_lateral_m", "candidate_basis",
                        "carrier_band_continuity", "photo_box_az_deg",
                        "photo_ray_deg", "planned_side")
                }
                measured["inner_edges"] = candidate.get("inner_edges")
            evidence_panel = None
            if spatial_candidate_evidence_path is not None:
                possible_panel = (
                    spatial_candidate_evidence_path.parent
                    / f"{placement_id}.jpg")
                if possible_panel.is_file():
                    evidence_panel = str(possible_panel.resolve())
            jobs.append({
                "review_id": review_id,
                "feature_id": placement.get("canonical_feature_id"),
                "instance_id": None,
                "placement_ids": [placement_id],
                "facts": {
                    "candidate_source": "zero_lateral_edge_census",
                    "module_id": module_id,
                    "canonical_host_id": attachment.get(
                        "canonical_carrier_id"),
                    "anchor_kind": attachment.get("anchor_kind"),
                    "current_lateral_offset_m": float(
                        attachment.get("lateral_offset_m") or 0.0),
                    "physical_context": (
                        "This is an edge-shaped object on the centreline of "
                        "its carrier. A fence, wall, hedge, railing, or "
                        "guardrail normally runs parallel at a road, path, "
                        "platform, or rail-corridor edge, not through an "
                        "active carriageway or track; centreline placement "
                        "is plausible only for an explicitly observed median, "
                        "divider, or carrier that itself maps the boundary."),
                    "measured_spatial_candidates": measured,
                    "reason": (
                        "edge-shaped observation is authored on the carrier "
                        "centre line; visual review must decide whether zero "
                        "is correct"),
                },
                "options": _edge_review_options(candidate),
                "existing_authored_decision_id": None,
                "visual_evidence": {
                    "source_crop_paths": (
                        [evidence_panel] if evidence_panel else []),
                    "plan_overlay_path": evidence_panel,
                    "rebuilt_map_path": None,
                    "state": (
                        "rendered_for_human_review" if evidence_panel
                        else "needs_evidence_renderer"),
                },
                "decision_contract": {
                    "model_output_is_proposal": True,
                    "model_generated_geometry_forbidden": True,
                    "supplied_geometry_candidate_selection_allowed": True,
                    "human_acceptance_required_before_apply": True,
                },
                "post_jev_visual_verification": {
                    "required": True,
                    "state": "pending",
                    "requires_fresh_rebuild": True,
                    "required_views": [
                        "source_crop", "plan_overlay", "rebuilt_map"],
                },
            })
            queued_placement_ids.add(placement_id)
            plan_anomaly_jobs += 1
    jobs.sort(key=lambda row: (str(row["feature_id"]), str(row["instance_id"])))
    return {
        "schema": JEV_INPUT_SCHEMA,
        "inputs": {
            "spatial_coherence": str(coherence_path.resolve()),
            "spatial_coherence_sha256": _sha256(coherence_path),
            "authored_decisions": decisions.get("path"),
            "authored_decisions_sha256": decisions.get("sha256"),
            "population_plan": (
                str(population_plan_path.resolve())
                if population_plan_path is not None else None),
            "population_plan_sha256": (
                _sha256(population_plan_path)
                if population_plan_path is not None else None),
            "spatial_candidate_evidence": (
                str(spatial_candidate_evidence_path.resolve())
                if spatial_candidate_evidence_path is not None else None),
            "spatial_candidate_evidence_sha256": (
                _sha256(spatial_candidate_evidence_path)
                if spatial_candidate_evidence_path is not None else None),
        },
        "action_vocabulary": ACTION_OPTIONS,
        "counts": {
            "jobs": len(jobs),
            "coherence_jobs": coherence_jobs,
            "plan_anomaly_jobs": plan_anomaly_jobs,
            "authored_decisions_excluded": len(excluded_decision_ids),
        },
        "excluded_authored_decision_ids": sorted(excluded_decision_ids),
        "jobs": jobs,
    }


def write_jev_input(coherence: dict, *, coherence_path: Path,
                    out_path: Path, decisions: dict | None = None,
                    population_plan: dict | None = None,
                    population_plan_path: Path | None = None,
                    spatial_candidate_evidence: dict | None = None,
                    spatial_candidate_evidence_path: Path | None = None) -> Path:
    document = build_jev_input(
        coherence, coherence_path=coherence_path, decisions=decisions,
        population_plan=population_plan,
        population_plan_path=population_plan_path,
        spatial_candidate_evidence=spatial_candidate_evidence,
        spatial_candidate_evidence_path=spatial_candidate_evidence_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return out_path
