#!/usr/bin/env python3
"""Compile the atomic FL-4512 U4 candidate-pool artifact.

The authored Material Look profile is compiler input, never a runtime pool
owner.  This command validates the complete source before writing a canonical
``fl4512.compiled_pool.v1`` transaction.  Any invalid row rejects the whole
artifact; no row is skipped, truncated, or partially emitted.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Any, Iterable


SCHEMA_ID = "fl4512.compiled_pool.v1"
CAPACITY = 32
# The profile mesh_bundle lane retains ADDRESS METADATA ONLY after the admission
# cutover. Any other key (candidates, candidate_admission_*, role_eligible_glyph_ids,
# legacy `kind` rows, aspect overrides) is rejected — the main compiler is the
# sole injector of the mesh_feature.bundle.v1 structural row.
MESH_ADDRESS_METADATA_KEYS = frozenset({
    "role", "route_key", "lane_id", "lane_name", "descriptor_version",
})
U7_SCORER_ID = "u7.mask.v1"
TONAL_SCORER_ID = "fl4260.shape6_weighted_l2.v1"
FILLED_SCORER_ID = "S_pf.v1"
# FL-4763. The EDGE, DIRECTION, and FLOW families claim a STRUCTURAL property of
# the cell (a step between stacked surfaces, a compass orientation, a flow axis).
# Deciding which glyph expresses that property needs evidence about the feature
# itself. This compiler currently has no structural live operand to give them:
# u7 covers only projected mesh curves and S_pf only authored filled primitives.
#
# The retired mapping handed them TONAL_SCORER_ID, whose operand the renderer
# reads back out of the pre-glyph colour raster. That silently decided structure
# from shading -- the exact anti-pattern FL-4763 exists to remove.
#
# This identity states the true situation instead of hiding it: the address is
# real and populated, and the compiler explicitly DECLINES to score it for want
# of a live structural operand. It carries a zero member-operand union, no
# policy flag, no primitive field, and no threshold. Runtime declines it to the
# tonal pair, and that decline stays countable rather than being painted over.
DECLINE_SCORER_ID = "fl4763.structural_decline.v1"
# Roles whose rows take DECLINE_SCORER_ID. These are populated addresses that
# keep every candidate; only the SCORING of them is declined.
STRUCTURAL_DECLINE_ROLES = frozenset({"edge", "direction", "flow"})
ABSENCE_POLICY_VERSION = "fl4512.absence.v1"
ROLE_WEIGHT_KEYS = ("curve", "diagonal", "horizontal", "vertical", "sparse", "dense")
EDGE_FACTS = ("flat", "rise", "fall", "ridge", "valley", "wall", "overhang")
DIRECTION_FACTS = (
    "north",
    "north_east",
    "east",
    "south_east",
    "south",
    "south_west",
    "west",
    "north_west",
)


class CompileFailure(ValueError):
    """Whole-artifact rejection carrying a stable receipt reason."""

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason


_U7_SPEC = importlib.util.spec_from_file_location(
    "fl4512_u7_mask", Path(__file__).with_name("fl4512_u7_mask.py")
)
if not _U7_SPEC or not _U7_SPEC.loader:
    raise RuntimeError("cannot load fl4512_u7_mask.py")
U7 = importlib.util.module_from_spec(_U7_SPEC)
_U7_SPEC.loader.exec_module(U7)

_PF_SPEC = importlib.util.spec_from_file_location(
    "fl4512_primitive_field", Path(__file__).with_name("fl4512_primitive_field.py")
)
if not _PF_SPEC or not _PF_SPEC.loader:
    raise RuntimeError("cannot load fl4512_primitive_field.py")
PF = importlib.util.module_from_spec(_PF_SPEC)
_PF_SPEC.loader.exec_module(PF)

_D6_SPEC = importlib.util.spec_from_file_location(
    "fl4512_d6_aspect", Path(__file__).with_name("fl4512_d6_aspect.py")
)
if not _D6_SPEC or not _D6_SPEC.loader:
    raise RuntimeError("cannot load fl4512_d6_aspect.py")
D6 = importlib.util.module_from_spec(_D6_SPEC)
_D6_SPEC.loader.exec_module(D6)

_SCALE_SPEC = importlib.util.spec_from_file_location(
    "fl4512_projected_scale", Path(__file__).with_name("fl4512_projected_scale.py")
)
if not _SCALE_SPEC or not _SCALE_SPEC.loader:
    raise RuntimeError("cannot load fl4512_projected_scale.py")
SCALE = importlib.util.module_from_spec(_SCALE_SPEC)
_SCALE_SPEC.loader.exec_module(SCALE)

_EXTENT_SPEC = importlib.util.spec_from_file_location(
    "fl4512_projected_feature_extent",
    Path(__file__).with_name("fl4512_projected_feature_extent.py"),
)
if not _EXTENT_SPEC or not _EXTENT_SPEC.loader:
    raise RuntimeError("cannot load fl4512_projected_feature_extent.py")
EXTENT = importlib.util.module_from_spec(_EXTENT_SPEC)
_EXTENT_SPEC.loader.exec_module(EXTENT)

# The STRICT offline Unicode mesh-feature admission validator. The main compiler
# is now the sole injector of the one mesh_feature.bundle.v1 structural row: it
# validates the checked-in admission artifact against the universal Unicode
# catalog + native atlas registry (validate_artifact, full_receipt=False — this
# never runs selection; admit_row, _select_legacy, and recompute_and_validate
# are never called from this compiler) and injects artifact.selected_row verbatim.
_ADMISSION_SPEC = importlib.util.spec_from_file_location(
    "compile_fl4512_mesh_feature_admission",
    Path(__file__).with_name("compile_fl4512_mesh_feature_admission.py"),
)
if not _ADMISSION_SPEC or not _ADMISSION_SPEC.loader:
    raise RuntimeError("cannot load compile_fl4512_mesh_feature_admission.py")
ADMISSION = importlib.util.module_from_spec(_ADMISSION_SPEC)
_ADMISSION_SPEC.loader.exec_module(ADMISSION)


ASPECT_AUTHORING_SCHEMA = "fl4512.aspect_pool_authoring.v1"
MESH_ASSET_METRIC_SCHEMA = "fl4512.mesh_asset_metric.v1"
ASPECT_AXIS_CONVENTION = "akm.right_handed.xyz.v1"
ASPECT_ASSET_IDENTITY_VERSION = "fl4512.aspect_asset_identity.v2"
PROJECTED_FEATURE_EXTENT_ADMISSION_SCHEMA = "fl4512.projected_feature_extent_admission.v2"
PROJECTED_FEATURE_EXTENT_QUANTIZATION_VERSION = EXTENT.EXTENT_QUANTIZATION_VERSION
ACTIVE_STROKE_TOPOLOGY_SCHEMA = "fl4512.active_stroke_topology.v2"
ACTIVE_STROKE_GAUSSIAN_SIGMA_Q16_16 = 65536
ACTIVE_STROKE_GAUSSIAN_DISTANCE2_STEP_Q16_16 = 4096
ACTIVE_STROKE_GAUSSIAN_TABLE_COUNT = 145
ACTIVE_STROKE_TOPOLOGY_OPERATION_ORDER = (
    "delete",
    "split",
    "trim",
    "extend",
    "merge",
    "insert",
)
PROJECTED_SCALE_FEATURE_CLASSES = (
    "silhouette",
    "crease",
    "material_boundary",
    "suggestive_contour",
)
ASPECT_MERGE_KEYS = (
    "profile_namespace",
    "material_key",
    "role",
    "route_key",
    "lane",
    "lane_name",
    "scorer_id",
    "bucket_state",
    "capacity",
    "candidate_glyph_ids",
    "candidate_descriptors",
)


def normalize_numbers(value: Any) -> Any:
    # FL-4589 root-cause fix (2026-07-15): the C++ loader validates every
    # embedded hash by REPRINTING the parsed JSON with cJSON and hashing
    # those bytes. cJSON prints a double with no fractional part as a bare
    # integer ("0", "1"), while python's json.dumps keeps the decimal point
    # ("0.0", "1.0"). Any integral float in a hashed payload therefore
    # makes the producer's hash and the loader's recomputed hash disagree,
    # and the loader fail-closes the WHOLE pool (artifact_hash_mismatch ->
    # every declared-profile cell renders the absence glyph). The first
    # integral floats entered the artifact with the filled-primitive
    # global6_norm arrays (commit 3fb6176e5), detonating this latent
    # contract break. Converting integral floats to ints here makes the
    # producer's bytes, the file's bytes, and cJSON's reprint identical —
    # JSON itself does not distinguish 0 from 0.0, so no consumer changes
    # meaning. Non-integral floats (e.g. 0.4562) already print identically
    # on both sides (established empirically by every pre-3fb6176e5 load).
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: normalize_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_numbers(item) for item in value]
    return value


def canonical_bytes(value: Any) -> bytes:
    # Every hash in the artifact is computed over these bytes, so the
    # normalization above MUST run here (one choke point) — a payload
    # hashed with "0.0" but written or reprinted as "0" would split the
    # producer and loader canonical forms again.
    return json.dumps(normalize_numbers(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path_text: str) -> tuple[dict[str, Any], bytes]:
    raw = sys.stdin.buffer.read() if path_text == "-" else Path(path_text).read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise CompileFailure("schema_invalid", f"{path_text} root must be an object")
    return value, raw


def current_commit(repo: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()


def parse_material_key(material_id: Any) -> tuple[str, int]:
    if not isinstance(material_id, str) or ":" not in material_id:
        raise CompileFailure("material_key_invalid", repr(material_id))
    namespace, numeric = material_id.split(":", 1)
    if namespace not in {"terrain", "mesh"} or not numeric.isdigit():
        raise CompileFailure("material_key_invalid", material_id)
    material = int(numeric)
    if not 0 <= material < 256:
        raise CompileFailure("material_key_invalid", material_id)
    return namespace, material


def require_candidates(value: Any, address: str, admitted: set[int]) -> list[int]:
    if not isinstance(value, list):
        raise CompileFailure("candidate_schema_invalid", address)
    if len(value) > CAPACITY:
        raise CompileFailure(
            "pool_capacity_exceeded", f"{address}: observed_count={len(value)} capacity={CAPACITY}"
        )
    if any(not isinstance(gid, int) or isinstance(gid, bool) or gid <= 255 for gid in value):
        raise CompileFailure("candidate_schema_invalid", address)
    if len(value) != len(set(value)):
        raise CompileFailure("duplicate_candidate", address)
    missing = [gid for gid in value if gid not in admitted]
    if missing:
        raise CompileFailure("candidate_unadmitted", f"{address}: {missing}")
    return list(value)


def row_scorer_id(role: str, address: str) -> str:
    """Name the scorer that owns one row, by role.

    FL-4763 replaced an `else TONAL_SCORER_ID` fallthrough here. That default
    was the defect: any role this compiler did not recognise -- including the
    three structural families -- silently inherited the tonal shape6 scorer and
    became a confident structural claim decided from shading.

    Every role is now named explicitly and an unrecognised one is a whole-artifact
    rejection. A new role must state which evidence answers it; it may not
    inherit tone by omission.
    """
    if role == "mesh_feature":
        return U7_SCORER_ID
    if role == "filled":
        return FILLED_SCORER_ID
    if role in STRUCTURAL_DECLINE_ROLES:
        return DECLINE_SCORER_ID
    if role == "tonal":
        return TONAL_SCORER_ID
    raise CompileFailure("row_scorer_unassigned", f"{address}: role {role!r}")


def make_row(
    namespace: str,
    material: int,
    role: str,
    route_key: str,
    lane: int,
    lane_name: str,
    candidates: Any,
    admitted: set[int],
    u7_descriptors: dict[int, dict[str, Any]],
    primitive_masks: dict[int, int] | None = None,
    filled_region_class: int | None = None,
    candidate_admission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    address = f"{namespace}:{material}/{role}/{route_key}/{lane}"
    gids = require_candidates(candidates, address, admitted)
    payload: dict[str, Any] = {
        "aspect_region_id": None,
        "bucket_state": "live" if gids else "empty_absence",
        "candidate_count": len(gids),
        # FL-4763: a declining row keeps its candidate MEMBERSHIP but carries no
        # scorer operand at all. Emitting the tonal `global6_norm` here would
        # re-seal the tonal vector into a structural row's identity, which is the
        # provenance this change removes. The descriptor is the glyph id alone,
        # and the program compiler encodes it as a zero member-operand union.
        "candidate_descriptors": [
            {"glyph_id": gid}
            if role in STRUCTURAL_DECLINE_ROLES
            else {
                "glyph_id": gid,
                **PF.descriptor(primitive_masks[gid], filled_region_class),
            }
            if role == "filled" and primitive_masks is not None and filled_region_class is not None
            else u7_descriptors[gid]
            for gid in gids
        ],
        "candidate_glyph_ids": gids,
        "capacity": CAPACITY,
        "lane": lane,
        "lane_name": lane_name,
        "material_key": material,
        "profile_namespace": namespace,
        "quantization_version": None,
        "role": role,
        "route_key": route_key,
        "scorer_id": row_scorer_id(role, address),
    }
    # Descriptor completeness is a row-admission condition, never a runtime
    # best-effort lookup. Symmetry is intentionally excluded: it is measured
    # after repeated assignments and cannot filter this upstream legal set.
    for descriptor in payload["candidate_descriptors"]:
        if role == "filled":
            required = ("mask8_u64_le_hex", "boundary8_u64_le_hex", "filled_region_class", "detail_u16")
        elif role == "mesh_feature":
            required = (
                "mask8_u64_le_hex", "skeleton_u64_le_hex", "endpoint_count",
                "junction_count", "beta0", "beta1", "euler_characteristic",
            )
        elif role in STRUCTURAL_DECLINE_ROLES:
            # FL-4763: a declining row requires NO operand field. Requiring
            # `global6_norm` here is what previously forced a tonal operand onto
            # a structural row. The membership is still admitted and checked;
            # only the operand is absent, and absent on purpose.
            required = ()
        else:
            required = ("global6_norm",)
        missing = [field for field in required if field not in descriptor]
        if missing:
            raise CompileFailure("candidate_descriptor_incomplete", f"{address}: {descriptor.get('glyph_id')} {missing}")
    if role == "mesh_feature":
        partitions: dict[tuple[int, int], list[int]] = {}
        for descriptor in payload["candidate_descriptors"]:
            signature = (descriptor["beta0"], descriptor["beta1"])
            partitions.setdefault(signature, []).append(descriptor["glyph_id"])
        payload["topology_constraint"] = {
            "background_connectivity": 4,
            "foreground_connectivity": 8,
            "match_fields": ["beta0", "beta1"],
            "symmetry_stage": "post_assignment_observation",
        }
        payload["topology_partitions"] = [
            {"beta0": beta0, "beta1": beta1, "candidate_glyph_ids": partitions[(beta0, beta1)]}
            for beta0, beta1 in sorted(partitions)
        ]
    if candidate_admission is not None:
        # Compiler-injected STRICT admission provenance (v2). Profile-authored
        # mesh candidate admission (the old "fl4512.mesh_feature_candidate_admission.v1"
        # acceptance) is DELETED: the profile no longer authors mesh candidates or
        # admission. The main compiler derives this provenance from the validated
        # offline admission artifact and stamps it here so row_sha256 seals it.
        schema = candidate_admission.get("schema")
        receipt_id = candidate_admission.get("receipt_id")
        role_eligible = candidate_admission.get("role_eligible_glyph_ids")
        artifact_sha256 = candidate_admission.get("strict_artifact_sha256")
        if schema != ADMISSION.ARTIFACT_SCHEMA:
            raise CompileFailure("candidate_role_admission_invalid", f"{address}: schema")
        # receipt_id is the strict receipt content hash; artifact_sha256 is the raw
        # admission-artifact file hash. Both are lowercase 64-hex SHA256 strings.
        if not (isinstance(receipt_id, str) and ADMISSION.HEX64_RE.match(receipt_id)):
            raise CompileFailure("candidate_role_admission_invalid", f"{address}: receipt_id")
        if not (isinstance(artifact_sha256, str) and ADMISSION.HEX64_RE.match(artifact_sha256)):
            raise CompileFailure("candidate_role_admission_invalid", f"{address}: strict_artifact_sha256")
        if (
            not isinstance(role_eligible, list)
            or any(not isinstance(gid, int) or isinstance(gid, bool) for gid in role_eligible)
            or len(role_eligible) != len(set(role_eligible))
            or any(gid not in role_eligible for gid in gids)
        ):
            raise CompileFailure(
                "candidate_role_admission_invalid",
                f"{address}: candidates must be a subset of unique role_eligible_glyph_ids",
            )
        payload["candidate_admission_schema"] = schema
        payload["candidate_admission_receipt_id"] = receipt_id
        payload["role_eligible_glyph_ids"] = list(role_eligible)
        payload["mesh_admission_artifact_sha256"] = artifact_sha256
    payload["row_sha256"] = sha256_bytes(canonical_bytes(payload))
    return payload


def indexed_rows(source: Any, expected: Iterable[str], address: str) -> list[dict[str, Any]]:
    if not isinstance(source, list):
        raise CompileFailure("lane_schema_invalid", address)
    expected_tuple = tuple(expected)
    by_fact: dict[str, dict[str, Any]] = {}
    for item in source:
        if not isinstance(item, dict):
            raise CompileFailure("lane_schema_invalid", address)
        fact = item.get("fact")
        if fact not in expected_tuple:
            raise CompileFailure("lane_fact_invalid", f"{address}: {fact!r}")
        if fact in by_fact:
            raise CompileFailure("duplicate_lane", f"{address}: {fact}")
        by_fact[fact] = item
    return [by_fact.get(fact, {"candidates": []}) for fact in expected_tuple]


def lane_candidates(item: dict[str, Any]) -> Any:
    return [] if "blocked" in item else item.get("candidates")


def _metric_source_path(repo_root: Path, path_text: Any) -> Path:
    if not isinstance(path_text, str) or not path_text or Path(path_text).is_absolute():
        raise CompileFailure("mesh_asset_metric_path_invalid", repr(path_text))
    root = repo_root.resolve()
    resolved = (root / path_text).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CompileFailure("mesh_asset_metric_path_invalid", path_text) from exc
    if not resolved.is_file():
        raise CompileFailure("mesh_asset_metric_geometry_missing", path_text)
    return resolved


def _aspect_asset_id(
    geometry_raw: bytes,
    anchor_q24_8: list[int],
    axis_convention: str,
    asset_metric_q24_8: list[int],
) -> str:
    """Hash the D6 qv3 asset-identity preimage in its paper-specified order."""
    return hashlib.sha256(
        geometry_raw
        + struct.pack("<iii", *anchor_q24_8)
        + axis_convention.encode("utf-8")
        + struct.pack("<III", *asset_metric_q24_8)
    ).hexdigest()


def compile_mesh_asset_metrics(
    registry: dict[str, Any],
    repo_root: Path,
    scale_golden_raw: bytes,
) -> tuple[list[dict[str, Any]], str, str]:
    """Validate the sole mesh-metric owner and compile radial runtime records."""
    if set(registry) != {"schema", "assets", "registry_sha256"}:
        raise CompileFailure("mesh_asset_metric_schema_invalid", "top-level fields")
    if registry.get("schema") != MESH_ASSET_METRIC_SCHEMA:
        raise CompileFailure("mesh_asset_metric_schema_invalid", repr(registry.get("schema")))
    assets = registry.get("assets")
    if not isinstance(assets, list) or not assets:
        raise CompileFailure("mesh_asset_metric_schema_invalid", "assets[] required")
    if any(not isinstance(asset, dict) for asset in assets):
        raise CompileFailure("mesh_asset_metric_schema_invalid", "asset must be object")
    canonical_assets = sorted(assets, key=lambda asset: str(asset.get("asset_id", "")))
    registry_hash = sha256_bytes(canonical_bytes(canonical_assets))
    if registry.get("registry_sha256") != registry_hash:
        raise CompileFailure("mesh_asset_metric_registry_hash_mismatch", registry_hash)
    try:
        golden = json.loads(scale_golden_raw)
        SCALE.validate_fixture(golden)
    except (json.JSONDecodeError, SCALE.ProjectedScaleError) as exc:
        raise CompileFailure("scale_golden_fixture_invalid", str(exc)) from exc
    golden_hash = sha256_bytes(scale_golden_raw)
    compiled: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    seen_basenames: set[str] = set()
    seen_geometry: set[str] = set()
    seen_ids: set[str] = set()
    required_fields = {
        "geometry_path",
        "geometry_sha256",
        "anchor_q24_8",
        "axis_convention",
        "asset_metric_q24_8",
        "asset_id",
    }
    for asset in canonical_assets:
        if set(asset) != required_fields:
            raise CompileFailure("mesh_asset_metric_schema_invalid", "asset fields")
        geometry_path_text = asset.get("geometry_path")
        geometry_path = _metric_source_path(repo_root, geometry_path_text)
        geometry_raw = geometry_path.read_bytes()
        geometry_hash = sha256_bytes(geometry_raw)
        anchor = asset.get("anchor_q24_8")
        metric = asset.get("asset_metric_q24_8")
        basename = geometry_path.name
        if (
            asset.get("geometry_sha256") != geometry_hash
            or not isinstance(anchor, list)
            or len(anchor) != 3
            or any(not isinstance(value, int) or isinstance(value, bool) for value in anchor)
            or not isinstance(metric, list)
            or len(metric) != 3
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value <= 0
                or value > 0xFFFFFFFF
                for value in metric
            )
            or asset.get("axis_convention") != ASPECT_AXIS_CONVENTION
            or geometry_path_text in seen_paths
            or basename in seen_basenames
            or geometry_hash in seen_geometry
        ):
            raise CompileFailure("mesh_asset_metric_record_invalid", str(geometry_path_text))
        asset_id = _aspect_asset_id(
            geometry_raw, anchor, ASPECT_AXIS_CONVENTION, metric
        )
        if asset.get("asset_id") != asset_id or asset_id in seen_ids:
            raise CompileFailure("mesh_asset_metric_identity_invalid", str(geometry_path_text))
        seen_paths.add(geometry_path_text)
        seen_basenames.add(basename)
        seen_geometry.add(geometry_hash)
        seen_ids.add(asset_id)
        payload = {
            "asset_id": asset_id,
            "anchor_q24_8": list(anchor),
            "asset_metric_q24_8": list(metric),
            "axis_convention": ASPECT_AXIS_CONVENTION,
            "geometry_path": geometry_path_text,
            "geometry_basename": basename,
            "geometry_sha256": geometry_hash,
            "registry_sha256": registry_hash,
            "scale_quantization_version": SCALE.SCALE_QUANTIZATION_VERSION,
        }
        payload["scale_asset_sha256"] = sha256_bytes(canonical_bytes(payload))
        compiled.append(payload)
    compiled.sort(key=lambda item: item["asset_id"])
    return compiled, golden_hash, registry_hash


def compile_projected_feature_extent_admission(profile: dict[str, Any]) -> dict[str, Any] | None:
    source = profile.get("projected_feature_extent_admission")
    if source is None:
        return None
    if (
        not isinstance(source, dict)
        or set(source) != {
            "schema", "extent_quantization_version", "mesh_thresholds",
            "filled_threshold", "suggestive_contour",
        }
        or source.get("schema") != PROJECTED_FEATURE_EXTENT_ADMISSION_SCHEMA
        or source.get("extent_quantization_version") != PROJECTED_FEATURE_EXTENT_QUANTIZATION_VERSION
    ):
        raise CompileFailure("feature_extent_admission_invalid", str(profile.get("material_id")))
    thresholds = source.get("mesh_thresholds")
    if not isinstance(thresholds, dict) or set(thresholds) != set(PROJECTED_SCALE_FEATURE_CLASSES):
        raise CompileFailure("feature_extent_admission_invalid", str(profile.get("material_id")))
    compiled_thresholds: dict[str, dict[str, int]] = {}
    try:
        for feature_class in PROJECTED_SCALE_FEATURE_CLASSES:
            enter_q, exit_q = EXTENT.validate_admission_threshold(
                thresholds.get(feature_class), feature_class
            )
            compiled_thresholds[feature_class] = {
                "enter_q": enter_q,
                "exit_q": exit_q,
            }
    except EXTENT.ProjectedFeatureExtentError as exc:
        raise CompileFailure("feature_extent_admission_invalid", str(exc)) from exc
    try:
        filled_enter_q, filled_exit_q = EXTENT.validate_admission_threshold(
            source.get("filled_threshold"), "filled"
        )
    except EXTENT.ProjectedFeatureExtentError as exc:
        raise CompileFailure("feature_extent_admission_invalid", str(exc)) from exc
    suggestive = source.get("suggestive_contour")
    if (
        not isinstance(suggestive, dict)
        or set(suggestive) != {
            "estimator_id", "derivative_enter_q16", "stability_angle_min_q16",
            "suggestive_claim_enabled",
        }
        or suggestive.get("estimator_id") != "fl4512.cubic_monge_qr.v1"
        or not isinstance(suggestive.get("derivative_enter_q16"), int)
        or isinstance(suggestive.get("derivative_enter_q16"), bool)
        or suggestive["derivative_enter_q16"] < 1
        or not isinstance(suggestive.get("stability_angle_min_q16"), int)
        or isinstance(suggestive.get("stability_angle_min_q16"), bool)
        or not 0 <= suggestive["stability_angle_min_q16"] <= 205887
        or not isinstance(suggestive.get("suggestive_claim_enabled"), bool)
    ):
        raise CompileFailure("feature_extent_admission_invalid", str(profile.get("material_id")))
    payload: dict[str, Any] = {
        "schema": PROJECTED_FEATURE_EXTENT_ADMISSION_SCHEMA,
        "extent_quantization_version": PROJECTED_FEATURE_EXTENT_QUANTIZATION_VERSION,
        "mesh_thresholds": compiled_thresholds,
        "filled_threshold": {"enter_q": filled_enter_q, "exit_q": filled_exit_q},
        "suggestive_contour": dict(suggestive),
    }
    payload["table_sha256"] = sha256_bytes(canonical_bytes(payload))
    return payload


def compile_no_match_policy(profile: dict[str, Any]) -> dict[str, Any]:
    """Validate the REQUIRED authored per-material no-match abstention policy.

    The profile document is the sole author of the runtime no-match decision
    (FL-4512 Phase 2), and the contract is ROLE-AWARE:

    - ``structural_degenerate_declines`` (must be authored true): a degenerate
      live operand — all six harri_shape6_q0_16 axes equal, epsilon exactly 0 —
      in a STRUCTURAL lane (edge/direction/flow roles and mesh structural
      rows) DECLINES the structural claim for that cell.
    - ``structural_decline_fallback`` (must be the string "tonal"): a declined
      structural claim falls back to the tonal pair; DECLARED_ABSENCE happens
      only when every legal lane declines or the profile explicitly authors
      blank.
    - ``tonal_constant_operand_legal``: a constant operand in a TONAL lane
      (ramp/density) remains legal and still selects — constancy is a valid
      tonal statement, not a defect.
    - ``max_error_q`` ({"structural": int, "tonal": int}): per-role winner
      weighted-L2 error bounds; a winner whose error exceeds its role bound
      is a no-match. Zero means unbounded because no direct scorer-unit
      evidence exists yet; positive values are measured role-scoped bounds.

    Every profile MUST carry the block — a missing policy is a
    misconfiguration, never a compiler default, because a silent default
    would become a second runtime authority over abstention.  The compiler
    carries the authored values verbatim into the compiled material entry
    (same place role_weights/colors are carried) so artifact_sha256 seals
    them.
    """

    source = profile.get("no_match_policy")
    if source is None:
        raise CompileFailure("no_match_policy_missing", str(profile.get("material_id")))
    # FL-4512 Phase 2: the four decision fields are REQUIRED and exact. Any
    # extra key is an unmodeled policy distinction and is rejected.
    if not isinstance(source, dict) or set(source) != {
        "structural_degenerate_declines",
        "structural_decline_fallback",
        "tonal_constant_operand_legal",
        "max_error_q",
    }:
        raise CompileFailure("no_match_policy_invalid", str(profile.get("material_id")))
    declines = source.get("structural_degenerate_declines")
    fallback = source.get("structural_decline_fallback")
    tonal_legal = source.get("tonal_constant_operand_legal")
    ceilings = source.get("max_error_q")
    # structural_degenerate_declines is the decline contract itself: only the
    # genuine JSON true is a legal authoring (a false or non-bool value would
    # silently re-enable nearest-glyph selection on degenerate operands).
    # The only supported fallback target is the tonal pair.
    if declines is not True or fallback != "tonal" or not isinstance(tonal_legal, bool):
        raise CompileFailure("no_match_policy_invalid", str(profile.get("material_id")))
    # Per-role winner-error bounds.  bool is a subclass of int in Python;
    # reject it explicitly so JSON true can never masquerade as an integer
    # error ceiling in the compiled ABI.
    if not isinstance(ceilings, dict) or set(ceilings) != {"structural", "tonal"}:
        raise CompileFailure("no_match_policy_invalid", str(profile.get("material_id")))
    if any(
        not isinstance(ceilings.get(role), int)
        or isinstance(ceilings.get(role), bool)
        or ceilings.get(role) < 0
        for role in ("structural", "tonal")
    ):
        raise CompileFailure("no_match_policy_invalid", str(profile.get("material_id")))
    # Verbatim carry (fresh containers so no caller aliasing can mutate the
    # compiled entry after validation).
    compiled: dict[str, Any] = {
        "structural_degenerate_declines": declines,
        "structural_decline_fallback": fallback,
        "tonal_constant_operand_legal": tonal_legal,
        "max_error_q": dict(ceilings),
    }
    return compiled


def _compile_content_hashed_block(
    source: Any,
    *,
    material_id: Any,
    expected_schema: str,
    exact_fields: set[str],
    failure_reason: str,
) -> dict[str, Any] | None:
    """Validate one authored structural block without silently normalizing it.

    These Phase-S blocks are runtime policy, not optional compiler defaults.
    The source owns every decision byte and its canonical content hash; this
    helper therefore preserves the authored field set exactly and rejects any
    missing, extra, mistyped, stale-hash, or schema-drifted member before the
    combined pool transaction can be written.
    """

    if source is None:
        return None
    if (
        not isinstance(source, dict)
        or set(source) != exact_fields
        or source.get("schema") != expected_schema
    ):
        raise CompileFailure(failure_reason, str(material_id))
    expected_hash = source.get("content_sha256")
    unhashed = {key: value for key, value in source.items() if key != "content_sha256"}
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or expected_hash != sha256_bytes(canonical_bytes(unhashed))
    ):
        raise CompileFailure(failure_reason, f"{material_id}: content_sha256")
    return dict(source)


def _active_stroke_gaussian_table_q0_16() -> list[int]:
    """Return the paper's fixed Gaussian lookup table without binary floats.

    The paper samples squared distance in sixteenth-cell increments and uses
    ``exp(-(j/16)/2)``.  Decimal arithmetic with an explicit precision and
    round-half-up rule makes the emitted integers independent of the host C
    library, CPU floating-point mode, and Python platform build.
    """

    with localcontext() as context:
        # Ninety-six decimal digits are far beyond the precision needed to
        # decide the nearest integer after multiplying by 65535.  The explicit
        # rounding mode documents the ABI instead of inheriting process state.
        context.prec = 96
        context.rounding = ROUND_HALF_UP
        scale = Decimal(65535)
        denominator = Decimal(32)
        return [
            int(
                (
                    scale * (-Decimal(index) / denominator).exp()
                ).to_integral_value(rounding=ROUND_HALF_UP)
            )
            for index in range(ACTIVE_STROKE_GAUSSIAN_TABLE_COUNT)
        ]


def compile_active_stroke_topology(
    profile: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate authored v2 snake policy and derive its immutable table.

    The Material Profile owns every scalar.  The compiler owns only the
    deterministic Gaussian expansion, the topology-operation order, and the
    canonical hashes over those compiled bytes.  Missing policy stays missing;
    callers decide whether the material role requires it, so no default can
    silently become a second runtime authority.
    """

    integer_fields = (
        "alpha_q0_16",
        "beta_q0_16",
        "attraction_lambda_q0_16",
        "temporal_lambda_q0_16",
        "step_q0_16",
        "gaussian_sigma_q16_16",
        "gaussian_distance2_step_q16_16",
        "gaussian_table_count",
        "relaxation_iterations",
        "resample_min_q16_16",
        "resample_max_q16_16",
        "coverage_radius_q16_16",
        "trim_radius_q16_16",
        "extend_radius_q16_16",
        "merge_radius_q16_16",
        "coverage_tangent_dot_min_q1_15",
        "merge_tangent_dot_min_q1_15",
        "split_tangent_dot_max_q1_15",
        "topology_balance_q0_16",
        "spring_stiffness_q1_15",
        "extension_step_q16_16",
        "extension_max_steps",
        "trim_support_min",
        "max_topology_passes",
        "max_tracks",
        "max_controls_per_track",
        "max_controls_per_owner",
        "max_samples_per_owner",
        "quantization_version",
    )
    source = _compile_content_hashed_block(
        profile.get("active_stroke_topology"),
        material_id=profile.get("material_id"),
        expected_schema=ACTIVE_STROKE_TOPOLOGY_SCHEMA,
        exact_fields={"schema", *integer_fields, "content_sha256"},
        failure_reason="active_stroke_topology_invalid",
    )
    if source is None:
        return None

    # bool is a subclass of int in Python.  Reject it explicitly so JSON true
    # can never masquerade as coefficient 1 in the compiled ABI.
    if any(
        not isinstance(source.get(field), int)
        or isinstance(source.get(field), bool)
        for field in integer_fields
    ):
        raise CompileFailure(
            "active_stroke_topology_invalid",
            f"{profile.get('material_id')}: integer domain",
        )

    q0_16_fields = (
        "alpha_q0_16",
        "beta_q0_16",
        "attraction_lambda_q0_16",
        "temporal_lambda_q0_16",
        "topology_balance_q0_16",
    )
    q1_15_fields = (
        "coverage_tangent_dot_min_q1_15",
        "merge_tangent_dot_min_q1_15",
        "split_tangent_dot_max_q1_15",
        "spring_stiffness_q1_15",
    )
    q16_16_positive_fields = (
        "resample_min_q16_16",
        "resample_max_q16_16",
        "coverage_radius_q16_16",
        "trim_radius_q16_16",
        "extend_radius_q16_16",
        "merge_radius_q16_16",
        "extension_step_q16_16",
    )
    bounds_valid = (
        all(0 <= source[field] <= 65535 for field in q0_16_fields)
        and 1 <= source["step_q0_16"] <= 65535
        and all(0 <= source[field] <= 32768 for field in q1_15_fields)
        and all(
            1 <= source[field] <= 0xFFFFFFFF
            for field in q16_16_positive_fields
        )
        and 1 <= source["relaxation_iterations"] <= 255
        and 1 <= source["extension_max_steps"] <= 255
        and 1 <= source["trim_support_min"] <= 65535
        and 1 <= source["max_topology_passes"] <= 255
        and 1 <= source["max_tracks"] <= 4096
        and 1 <= source["max_controls_per_track"] <= 4096
        and 1 <= source["max_controls_per_owner"] <= 65536
        and 1 <= source["max_samples_per_owner"] <= 65536
        and source["max_controls_per_track"]
        <= source["max_controls_per_owner"]
        and source["resample_min_q16_16"] < source["resample_max_q16_16"]
        and source["trim_radius_q16_16"] < source["coverage_radius_q16_16"]
        and source["coverage_radius_q16_16"] < source["extend_radius_q16_16"]
        and source["trim_radius_q16_16"] < source["merge_radius_q16_16"]
        and source["gaussian_sigma_q16_16"]
        == ACTIVE_STROKE_GAUSSIAN_SIGMA_Q16_16
        and source["gaussian_distance2_step_q16_16"]
        == ACTIVE_STROKE_GAUSSIAN_DISTANCE2_STEP_Q16_16
        and source["gaussian_table_count"]
        == ACTIVE_STROKE_GAUSSIAN_TABLE_COUNT
        and source["quantization_version"] == 2
    )
    if not bounds_valid:
        raise CompileFailure(
            "active_stroke_topology_invalid",
            str(profile.get("material_id")),
        )

    gaussian_table = _active_stroke_gaussian_table_q0_16()
    compiled = {
        key: value for key, value in source.items() if key != "content_sha256"
    }
    compiled["gaussian_table_q0_16"] = gaussian_table
    compiled["gaussian_table_sha256"] = sha256_bytes(
        canonical_bytes(gaussian_table)
    )
    compiled["topology_operation_order"] = list(
        ACTIVE_STROKE_TOPOLOGY_OPERATION_ORDER
    )
    compiled["content_sha256"] = sha256_bytes(canonical_bytes(compiled))
    return compiled


def _aspect_base_row(rows: list[dict[str, Any]], address: Any) -> dict[str, Any]:
    if not isinstance(address, dict):
        raise CompileFailure("aspect_pool_address_invalid", repr(address))
    required = {
        "profile_namespace",
        "material_key",
        "role",
        "route_key",
        "lane",
        "lane_name",
    }
    if set(address) != required:
        raise CompileFailure("aspect_pool_address_invalid", repr(address))
    matches = [
        row
        for row in rows
        if all(row.get(key) == address[key] for key in required)
        and row.get("aspect_region_id") is None
        and row.get("quantization_version") is None
    ]
    if len(matches) != 1 or matches[0].get("role") != "mesh_feature":
        raise CompileFailure("aspect_pool_address_invalid", repr(address))
    return matches[0]


def _aspect_merge_payload(row: dict[str, Any]) -> dict[str, Any]:
    if any(key not in row for key in ASPECT_MERGE_KEYS):
        raise CompileFailure("aspect_merge_payload_invalid", repr(row))
    return {key: row[key] for key in ASPECT_MERGE_KEYS}


def _aspect_pool_address(
    merge_payload: dict[str, Any], region_id: dict[str, Any]
) -> dict[str, Any]:
    return {
        "profile_namespace": merge_payload["profile_namespace"],
        "material_key": merge_payload["material_key"],
        "role": merge_payload["role"],
        "route_key": merge_payload["route_key"],
        "lane": merge_payload["lane"],
        "aspect_region_id": region_id,
    }


def _aspect_graph_neighbors(n: int) -> list[set[int]]:
    neighbors = [set() for _ in range(n * n)]

    def connect(a: int, b: int) -> None:
        if a != b:
            neighbors[a].add(b)
            neighbors[b].add(a)

    for row in range(n):
        for col in range(n):
            cell = row * n + col
            if row + 1 < n:
                connect(cell, (row + 1) * n + col)
            if col + 1 < n:
                connect(cell, row * n + col + 1)
    for a, b in D6.canonical_seam_pairs(n):
        connect(a, b)
    return neighbors


def _aspect_components(cell_payload_hashes: list[str], n: int) -> list[list[int]]:
    neighbors = _aspect_graph_neighbors(n)
    unseen = set(range(n * n))
    components: list[list[int]] = []
    while unseen:
        start = min(unseen)
        payload_hash = cell_payload_hashes[start]
        stack = [start]
        unseen.remove(start)
        component: list[int] = []
        while stack:
            cell = stack.pop()
            component.append(cell)
            for neighbor in sorted(neighbors[cell], reverse=True):
                if neighbor in unseen and cell_payload_hashes[neighbor] == payload_hash:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(sorted(component))
    return sorted(components, key=lambda cells: cells[0])


def _aspect_variant_row(
    variant: dict[str, Any],
    rows: list[dict[str, Any]],
    admitted: set[int],
    u7_descriptors: dict[int, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    base = _aspect_base_row(rows, variant.get("source_pool_address"))
    candidates = variant.get("candidate_glyph_ids")
    if candidates is None:
        aspect_row = dict(base)
    else:
        # An aspect view-variant may select a SUBSET of the base row's candidates
        # for a view region — and ONLY a subset of THAT exact base row. Membership
        # is enforced against the base's own candidate_glyph_ids, so a non-mesh
        # base row can never reach a structural Unicode GlyphId (those live only in
        # the mesh base row), i.e. no aspect leaks the unioned structural supply
        # through a non-mesh base.
        aspect_address = str(variant.get("source_pool_address"))
        # Validate both sides before constructing a set. Raw set(...) here used
        # to let malformed nested values escape as an untyped TypeError instead
        # of the compiler's fail-shut candidate_schema_invalid reason.
        base_candidates = require_candidates(
            base.get("candidate_glyph_ids"), aspect_address + ":base", admitted
        )
        variant_candidates = require_candidates(
            candidates, aspect_address + ":variant", admitted
        )
        if not set(variant_candidates) <= set(base_candidates):
            raise CompileFailure(
                "aspect_candidate_not_base_subset",
                aspect_address,
            )
        # A mesh structural aspect subset INHERITS the exact base admission
        # provenance (schema, receipt hash, role-eligible set, raw artifact sha256)
        # and never re-runs the selector. Non-mesh base rows carry no admission.
        admission = None
        if base.get("candidate_admission_schema") is not None:
            admission = {
                "schema": base.get("candidate_admission_schema"),
                "receipt_id": base.get("candidate_admission_receipt_id"),
                "role_eligible_glyph_ids": base.get("role_eligible_glyph_ids"),
                "strict_artifact_sha256": base.get("mesh_admission_artifact_sha256"),
            }
        aspect_row = make_row(
            base["profile_namespace"],
            base["material_key"],
            base["role"],
            base["route_key"],
            base["lane"],
            base["lane_name"],
            variant_candidates,
            admitted,
            u7_descriptors,
            candidate_admission=admission,
        )
    merge_payload = _aspect_merge_payload(aspect_row)
    provenance = {
        key: aspect_row[key]
        for key in (
            "candidate_admission_schema",
            "candidate_admission_receipt_id",
            "role_eligible_glyph_ids",
            "mesh_admission_artifact_sha256",
        )
        if key in aspect_row
    }
    return merge_payload, provenance


def compile_aspect_assets(
    authoring: dict[str, Any],
    rows: list[dict[str, Any]],
    admitted: set[int],
    u7_descriptors: dict[int, dict[str, Any]],
    metric_assets: list[dict[str, Any]],
    metric_registry_sha256: str,
    golden_raw: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Compile authored view cells into exact connected aspect regions."""
    if set(authoring) != {"schema", "assets"} or authoring.get("schema") != ASPECT_AUTHORING_SCHEMA:
        raise CompileFailure("aspect_authoring_schema_invalid", repr(authoring.get("schema")))
    assets = authoring.get("assets")
    if not isinstance(assets, list) or not assets:
        raise CompileFailure("aspect_authoring_schema_invalid", "assets[] required")
    try:
        golden = json.loads(golden_raw)
        D6.validate_fixture(golden)
    except (json.JSONDecodeError, D6.D6AspectError) as exc:
        raise CompileFailure("aspect_golden_fixture_invalid", str(exc)) from exc
    golden_hash = sha256_bytes(golden_raw)

    compiled_assets: list[dict[str, Any]] = []
    aspect_rows: list[dict[str, Any]] = []
    metric_by_id = {asset["asset_id"]: asset for asset in metric_assets}
    seen_assets: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict) or set(asset) != {
            "asset_id",
            "N",
            "quantization_version",
            "pose_buckets",
        }:
            raise CompileFailure("aspect_authoring_schema_invalid", "asset must be object")
        asset_id = asset.get("asset_id")
        metric_owner = metric_by_id.get(asset_id)
        if not isinstance(metric_owner, dict) or asset_id in seen_assets:
            raise CompileFailure("aspect_metric_join_invalid", repr(asset_id))
        seen_assets.add(asset_id)
        geometry_path_text = metric_owner["geometry_path"]
        geometry_hash = metric_owner["geometry_sha256"]
        anchor = metric_owner["anchor_q24_8"]
        asset_metric = metric_owner["asset_metric_q24_8"]
        n = asset.get("N")
        quantization_version = asset.get("quantization_version")
        if (
            n != D6.GRID_SIZE
            or quantization_version != D6.QUANTIZATION_VERSION
        ):
            raise CompileFailure("aspect_authoring_schema_invalid", str(geometry_path_text))
        pose_sources = asset.get("pose_buckets")
        if not isinstance(pose_sources, list) or not pose_sources:
            raise CompileFailure("aspect_pose_table_invalid", str(geometry_path_text))

        canonical_poses: list[dict[str, Any]] = []
        staged_poses: list[dict[str, Any]] = []
        seen_buckets: set[int] = set()
        for pose in pose_sources:
            if not isinstance(pose, dict) or set(pose) != {
                "bucket_id",
                "authored_pose_label",
                "variants",
                "cell_variant_ids",
            }:
                raise CompileFailure("aspect_pose_table_invalid", str(geometry_path_text))
            bucket_id = pose.get("bucket_id")
            pose_label = pose.get("authored_pose_label")
            if (
                not isinstance(bucket_id, int)
                or isinstance(bucket_id, bool)
                or not 0 <= bucket_id <= 65535
                or bucket_id in seen_buckets
                or not isinstance(pose_label, str)
                or not pose_label
            ):
                raise CompileFailure("aspect_pose_table_invalid", str(geometry_path_text))
            seen_buckets.add(bucket_id)
            variants = pose.get("variants")
            assignments = pose.get("cell_variant_ids")
            if (
                not isinstance(variants, list)
                or not variants
                or not isinstance(assignments, list)
                or len(assignments) != n * n
            ):
                raise CompileFailure("aspect_cell_assignment_invalid", f"{geometry_path_text}:{bucket_id}")
            compiled_variants: dict[str, dict[str, Any]] = {}
            canonical_variants: list[dict[str, Any]] = []
            for variant in variants:
                if not isinstance(variant, dict) or not set(variant) <= {
                    "variant_id",
                    "source_pool_address",
                    "candidate_glyph_ids",
                } or not {"variant_id", "source_pool_address"} <= set(variant):
                    raise CompileFailure("aspect_variant_invalid", f"{geometry_path_text}:{bucket_id}")
                variant_id = variant.get("variant_id")
                if not isinstance(variant_id, str) or not variant_id or variant_id in compiled_variants:
                    raise CompileFailure("aspect_variant_invalid", f"{geometry_path_text}:{bucket_id}")
                merge_payload, provenance = _aspect_variant_row(
                    variant, rows, admitted, u7_descriptors
                )
                merge_hash = sha256_bytes(canonical_bytes(merge_payload))
                compiled_variants[variant_id] = {
                    "merge_payload_v1": merge_payload,
                    "merge_payload_sha256": merge_hash,
                    "provenance": provenance,
                }
                canonical_variants.append(
                    {
                        "variant_id": variant_id,
                        "merge_payload_v1": merge_payload,
                        "provenance": provenance,
                    }
                )
            if any(not isinstance(value, str) or value not in compiled_variants for value in assignments):
                raise CompileFailure("aspect_cell_assignment_invalid", f"{geometry_path_text}:{bucket_id}")
            canonical_poses.append(
                {
                    "bucket_id": bucket_id,
                    "authored_pose_label": pose_label,
                    "variants": sorted(canonical_variants, key=lambda item: item["variant_id"]),
                    "cell_variant_ids": list(assignments),
                }
            )
            staged_poses.append(
                {
                    "bucket_id": bucket_id,
                    "authored_pose_label": pose_label,
                    "compiled_variants": compiled_variants,
                    "cell_variant_ids": list(assignments),
                }
            )

        canonical_asset = {
            "schema": ASPECT_AUTHORING_SCHEMA,
            "asset_id": asset_id,
            "N": n,
            "quantization_version": quantization_version,
            "pose_buckets": sorted(canonical_poses, key=lambda item: item["bucket_id"]),
        }
        authoring_hash = sha256_bytes(canonical_bytes(canonical_asset))

        compiled_poses: list[dict[str, Any]] = []
        for pose in sorted(staged_poses, key=lambda item: item["bucket_id"]):
            bucket_id = pose["bucket_id"]
            assignments = pose["cell_variant_ids"]
            variants = pose["compiled_variants"]
            cell_hashes = [variants[variant_id]["merge_payload_sha256"] for variant_id in assignments]
            components = _aspect_components(cell_hashes, n)
            flat_map: list[dict[str, Any] | None] = [None] * (n * n)
            region_table: list[dict[str, Any]] = []
            for members in components:
                lowest = members[0]
                variant = variants[assignments[lowest]]
                region_id = {
                    "asset_id": asset_id,
                    "N": n,
                    "quantization_version": quantization_version,
                    "pose_bucket": bucket_id,
                    "lowest_member_cell_index": lowest,
                }
                pool_address = _aspect_pool_address(variant["merge_payload_v1"], region_id)
                for cell in members:
                    flat_map[cell] = pool_address
                region = {
                    "region_id": region_id,
                    "member_cells": members,
                    "merge_payload_sha256": variant["merge_payload_sha256"],
                    "pool_address": pool_address,
                }
                region_table.append(region)
                row = {
                    **variant["merge_payload_v1"],
                    **variant["provenance"],
                    "candidate_count": len(variant["merge_payload_v1"]["candidate_glyph_ids"]),
                    "descriptor_quantization_version": None,
                    "quantization_version": quantization_version,
                    "aspect_region_id": region_id,
                    "pose_bucket": bucket_id,
                    "member_cells": members,
                    "geometry_sha256": geometry_hash,
                    "aspect_authoring_sha256": authoring_hash,
                    "metric_registry_sha256": metric_registry_sha256,
                    "asset_id": asset_id,
                    "asset_metric_q24_8": list(asset_metric),
                    "merge_payload_sha256": variant["merge_payload_sha256"],
                }
                row["row_sha256"] = sha256_bytes(canonical_bytes(row))
                aspect_rows.append(row)
            if any(address is None for address in flat_map):
                raise CompileFailure("aspect_cell_assignment_invalid", f"{geometry_path_text}:{bucket_id}")
            compiled_poses.append(
                {
                    "bucket_id": bucket_id,
                    "authored_pose_label": pose["authored_pose_label"],
                    "flat_map": flat_map,
                    "flat_map_hash": sha256_bytes(canonical_bytes(flat_map)),
                    "region_table": region_table,
                    "region_table_hash": sha256_bytes(canonical_bytes(region_table)),
                }
            )
        compiled_assets.append(
            {
                "geometry_path": str(geometry_path_text),
                "geometry_sha256": geometry_hash,
                "aspect_authoring_sha256": authoring_hash,
                "asset_id": asset_id,
                "anchor_q24_8": list(anchor),
                "asset_metric_q24_8": list(asset_metric),
                "axis_convention": ASPECT_AXIS_CONVENTION,
                "asset_identity_version": ASPECT_ASSET_IDENTITY_VERSION,
                "metric_registry_sha256": metric_registry_sha256,
                "N": n,
                "quantization_version": quantization_version,
                "golden_fixture_sha256": golden_hash,
                "pose_buckets": compiled_poses,
            }
        )

    compiled_assets.sort(key=lambda item: item["asset_id"])
    aspect_rows.sort(
        key=lambda row: (
            row["asset_id"],
            row["pose_bucket"],
            row["aspect_region_id"]["lowest_member_cell_index"],
        )
    )
    return compiled_assets, aspect_rows, golden_hash


def compile_artifact(
    profiles: dict[str, Any],
    profile_raw: bytes,
    catalog: dict[str, Any],
    catalog_raw: bytes,
    atlas: dict[str, Any],
    atlas_raw: bytes,
    page16: dict[str, Any],
    page16_raw: bytes,
    compiler_commit: str,
    pool_revision: int = 2,
    aspect_authoring: dict[str, Any] | None = None,
    repo_root: Path | None = None,
    aspect_golden_raw: bytes | None = None,
    metric_registry: dict[str, Any] | None = None,
    scale_golden_raw: bytes | None = None,
    extent_golden_raw: bytes | None = None,
    admission_artifact: dict[str, Any] | None = None,
    admission_artifact_raw: bytes | None = None,
    universal_catalog: dict[str, Any] | None = None,
    universal_catalog_raw: bytes | None = None,
) -> dict[str, Any]:
    if not isinstance(pool_revision, int) or isinstance(pool_revision, bool) or pool_revision < 2:
        raise CompileFailure("pool_revision_invalid", repr(pool_revision))
    # The offline admission artifact + universal Unicode catalog are IMMUTABLE
    # compiler supply inputs. The mesh structural row comes ONLY from here.
    if admission_artifact is None or admission_artifact_raw is None \
            or universal_catalog is None or universal_catalog_raw is None:
        raise CompileFailure("mesh_admission_input_missing",
                             "admission artifact + universal catalog are required inputs")
    entries = catalog.get("entries")
    native_bindings = atlas.get("bindings")
    if not isinstance(entries, list) or atlas.get("schema") != "fl4482.native_atlas_registry.v2" or not isinstance(native_bindings, list):
        raise CompileFailure("admission_source_invalid", "catalog entries and exact native atlas bindings required")
    catalog_ids = {
        item.get("glyph_id")
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("glyph_id"), int)
    }
    atlas_ids = {item.get("glyph_id") for item in native_bindings if isinstance(item, dict) and isinstance(item.get("glyph_id"), int)}
    admitted = catalog_ids & atlas_ids

    # ---- FL-4482 registry-v2 mask sampling ---------------------------
    # The universal atlas registry is multi-pack: every binding names a
    # (pack_index, page_index) and the registry's own "pages" table
    # carries each page's PNG path, pixel hash, and dimensions. The mask
    # for a glyph must therefore be sampled from ITS page, resolved
    # through the registry — the old single-page16 assumption (every
    # admitted glyph lives on pack 0 of one json page) died with the v2
    # cutover and made this compiler reject the whole supply
    # (atlas_binding_invalid). Pages are decoded once and cached; every
    # page's bytes are verified against the registry's pinned sha256
    # before any tile is read, so a stale PNG can never silently feed
    # descriptor generation.
    pages_by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for page in atlas.get("pages", []):
        if isinstance(page, dict) and isinstance(page.get("pack_index"), int) \
                and isinstance(page.get("page_index"), int):
            pages_by_key[(page["pack_index"], page["page_index"])] = page
    if not pages_by_key:
        raise CompileFailure("admission_source_invalid", "registry v2 pages table missing")
    root = repo_root if repo_root is not None else Path(__file__).resolve().parents[1]
    # (rgba flat list, page width) per (pack_index, page_index).
    page_pixels: dict[tuple[int, int], tuple[list[int], int]] = {}

    def page_rgba_for(page: dict[str, Any]) -> tuple[list[int], int]:
        key = (page["pack_index"], page["page_index"])
        if key not in page_pixels:
            # PIL is imported lazily: only the v2 sampling path needs it.
            from PIL import Image
            page_path = (root / str(page.get("page16_path", ""))).resolve()
            try:
                image = Image.open(page_path).convert("RGBA")
            except OSError as exc:
                raise CompileFailure("page16_invalid", f"{page_path}: {exc}") from exc
            if (image.width, image.height) != (page.get("width_px"), page.get("height_px")):
                raise CompileFailure("page16_invalid", f"{page_path}: dimensions differ from registry")
            pixel_bytes = image.tobytes()
            # The registry pins the DECODED RGBA pixel bytes, not the PNG
            # container (container bytes vary by encoder settings while the
            # pixels are the actual contract) — hash what was decoded.
            if sha256_bytes(pixel_bytes) != page.get("page16_sha256"):
                raise CompileFailure("page16_invalid", f"{page_path}: pixel sha256 mismatch vs registry")
            page_pixels[key] = (list(pixel_bytes), image.width)
        return page_pixels[key]
    u7_descriptors: dict[int, dict[str, Any]] = {}
    primitive_masks: dict[int, int] = {}
    entries_by_gid = {
        item["glyph_id"]: item for item in entries
        if isinstance(item, dict) and isinstance(item.get("glyph_id"), int)
    }
    for glyph_id in sorted(admitted):
        binding = next((item for item in native_bindings if isinstance(item, dict) and item.get("glyph_id") == glyph_id), None)
        # v2: a binding is valid when its (pack_index, page_index) resolves
        # to a registry page — glyphs live on ANY pack tier now, not pack 0.
        if not isinstance(binding, dict):
            raise CompileFailure("atlas_binding_invalid", str(glyph_id))
        page = pages_by_key.get((binding.get("pack_index"), binding.get("page_index")))
        if page is None:
            raise CompileFailure("atlas_binding_invalid", str(glyph_id))
        page_flat, page_width = page_rgba_for(page)
        # Tile origin in pixels: bindings address 16x16 cells by column/row.
        x0 = int(binding["source_column"]) * 16
        y0 = int(binding["source_row"]) * 16
        if x0 + 16 > page_width or (y0 + 16) * page_width * 4 > len(page_flat):
            raise CompileFailure("atlas_binding_invalid", f"{glyph_id}: tile outside page")
        try:
            mask = U7.mask8_from_page16_rgba(page_flat, page_width, x0, y0)
            record = U7.descriptor(mask)
        except U7.U7MaskError as exc:
            raise CompileFailure(exc.reason, f"glyph_id={glyph_id}: {exc}") from exc
        shape6_norm = entries_by_gid[glyph_id].get("shape6_norm")
        if (
            not isinstance(shape6_norm, list) or len(shape6_norm) != 6
            or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in shape6_norm)
        ):
            raise CompileFailure("candidate_descriptor_incomplete", f"glyph_id={glyph_id}: global6_norm")
        u7_descriptors[glyph_id] = {
            "glyph_id": glyph_id,
            "global6_norm": list(shape6_norm),
            "mask8_u64_le_hex": record["mask8_u64_le_hex"],
            "skeleton_u64_le_hex": record["skeleton_u64_le_hex"],
            "endpoint_count": record["endpoint_count"],
            "junction_count": record["junction_count"],
            "beta0": record["beta0"],
            "beta1": record["beta1"],
            "euler_characteristic": record["euler_characteristic"],
        }
        primitive_masks[glyph_id] = mask

    # ---- STRICT mesh-feature admission (the sole mesh structural row source) --
    # Validate the checked-in admission artifact against the universal catalog +
    # native atlas registry WITHOUT running selection (full_receipt=False): this
    # re-derives the supply, re-projects the full receipt, and proves the
    # selected_row bindings equal the registry. The compiler never calls
    # admit_row, _select_legacy, or recompute_and_validate here.
    # Bind validation to the EXACT SUPPLIED universal catalog + native atlas
    # registry (in memory) — never re-reading a canonical path. This validates
    # the same documents whose hashes the header then stamps: it proves
    # source.catalog_source_sha256 == sha256(universal_catalog_raw) and
    # source.registry_source_sha256 == sha256(atlas_raw), rebuilds the supplied
    # supply + atlas-binding projections, and proves the receipt + every
    # selected_row binding tuple against them. The compiler never calls
    # admit_row, _select_legacy, or recompute_and_validate (full_receipt=False).
    try:
        ADMISSION.validate_artifact_documents(
            admission_artifact,
            universal_catalog, universal_catalog_raw,
            atlas, atlas_raw,
            full_receipt=False,
        )
    except ADMISSION.CompileError as exc:
        raise CompileFailure("mesh_admission_artifact_invalid",
                             f"{exc.reason}: {exc.detail}") from exc
    if admission_artifact.get("schema") != ADMISSION.ARTIFACT_SCHEMA:
        raise CompileFailure("mesh_admission_artifact_invalid", "schema")
    selected_row = admission_artifact.get("selected_row")
    if not isinstance(selected_row, list) or len(selected_row) != CAPACITY:
        raise CompileFailure("mesh_admission_artifact_invalid", "selected_row cardinality")
    selected_ids: list[int] = []
    for entry in selected_row:
        gid = entry.get("glyph_id")
        if not isinstance(gid, int) or isinstance(gid, bool):
            raise CompileFailure("mesh_admission_artifact_invalid", "selected glyph_id not int")
        selected_ids.append(gid)
    if len(set(selected_ids)) != len(selected_ids):
        raise CompileFailure("mesh_admission_artifact_invalid", "duplicate selected glyph_id")
    # Universal-catalog masks keyed by glyph_id — used to prove the natively
    # sampled descriptor equals the declared u7 mask (descriptor-mismatch guard).
    universal_entries = universal_catalog.get("entries")
    if not isinstance(universal_entries, list):
        raise CompileFailure("mesh_admission_artifact_invalid", "universal catalog entries")
    universal_mask_by_gid: dict[int, int] = {}
    for item in universal_entries:
        if isinstance(item, dict) and isinstance(item.get("glyph_id"), int) \
                and not isinstance(item.get("glyph_id"), bool) \
                and isinstance(item.get("mask8_u64"), str):
            universal_mask_by_gid[item["glyph_id"]] = int(item["mask8_u64"], 16)
    # Sample each selected Unicode glyph from ITS declared native atlas page and
    # derive its exact u7.mask.v1 descriptor. NO global6_norm is required for the
    # structural mesh role (unlike tonal/edge/direction/flow candidates above,
    # which continue to require global6_norm).
    structural_descriptors: dict[int, dict[str, Any]] = {}
    for entry in selected_row:
        gid = entry["glyph_id"]
        page = pages_by_key.get((entry.get("pack_index"), entry.get("page_index")))
        if page is None:
            raise CompileFailure("mesh_admission_atlas_binding_invalid", str(gid))
        page_flat, page_width = page_rgba_for(page)
        x0 = int(entry["source_column"]) * 16
        y0 = int(entry["source_row"]) * 16
        if x0 + 16 > page_width or (y0 + 16) * page_width * 4 > len(page_flat):
            raise CompileFailure("mesh_admission_atlas_binding_invalid", f"{gid}: tile outside page")
        try:
            mask = U7.mask8_from_page16_rgba(page_flat, page_width, x0, y0)
            record = U7.descriptor(mask)
        except U7.U7MaskError as exc:
            raise CompileFailure(exc.reason, f"structural glyph_id={gid}: {exc}") from exc
        declared_mask = universal_mask_by_gid.get(gid)
        if declared_mask is None or declared_mask != mask:
            raise CompileFailure("mesh_admission_descriptor_mismatch", str(gid))
        structural_descriptors[gid] = {
            "glyph_id": gid,
            "mask8_u64_le_hex": record["mask8_u64_le_hex"],
            "skeleton_u64_le_hex": record["skeleton_u64_le_hex"],
            "endpoint_count": record["endpoint_count"],
            "junction_count": record["junction_count"],
            "beta0": record["beta0"],
            "beta1": record["beta1"],
            "euler_characteristic": record["euler_characteristic"],
        }
    # Compiler-owned mesh supply: the additive-catalog intersection PLUS only the
    # selected structural GlyphIds, for the mesh role ONLY. No universal Unicode
    # GlyphId becomes eligible for any other role (they stay out of `admitted`).
    mesh_admitted = admitted | set(selected_ids)
    mesh_u7_descriptors = {**u7_descriptors, **structural_descriptors}
    raw_admission_artifact_sha256 = sha256_bytes(admission_artifact_raw)
    receipt_content_hash = (admission_artifact.get("receipt") or {}).get("content_hash")
    mesh_admission_provenance = {
        "schema": admission_artifact["schema"],
        "receipt_id": receipt_content_hash,
        "role_eligible_glyph_ids": list(selected_ids),
        "strict_artifact_sha256": raw_admission_artifact_sha256,
    }

    source_profiles = profiles.get("profiles")
    if not isinstance(source_profiles, list):
        raise CompileFailure("schema_invalid", "profiles[] required")

    rows: list[dict[str, Any]] = []
    materials: list[dict[str, Any]] = []
    structural_registry_rows: list[dict[str, Any]] = []
    profile_owners: set[tuple[str, int]] = set()
    for profile in source_profiles:
        if not isinstance(profile, dict):
            raise CompileFailure("schema_invalid", "profile must be an object")
        namespace, material = parse_material_key(profile.get("material_id"))
        profile_key = (namespace, material)
        if profile_key in profile_owners:
            raise CompileFailure("duplicate_profile_owner", f"{namespace}:{material}")
        profile_owners.add(profile_key)
        buckets = profile.get("role_buckets")
        if not isinstance(buckets, dict):
            raise CompileFailure("lane_schema_invalid", profile["material_id"])
        colors = profile.get("colors")
        if not isinstance(colors, dict):
            raise CompileFailure("material_state_invalid", f"{profile['material_id']}/colors")
        elevation_rows = colors.get("elevation_rows")
        thresholds = colors.get("shade_band_thresholds")
        per_row = colors.get("per_row")
        if (
            not isinstance(elevation_rows, dict)
            or not isinstance(thresholds, list)
            or len(thresholds) != 4
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in thresholds)
            or not isinstance(per_row, list)
            or len(per_row) != 4
        ):
            raise CompileFailure("material_state_invalid", f"{profile['material_id']}/colors")
        for row_name in ("fall_lower", "high", "rise", "flat_low"):
            color_row = elevation_rows.get(row_name)
            if not isinstance(color_row, dict):
                raise CompileFailure("material_state_invalid", f"{profile['material_id']}/{row_name}")
            for channel in ("fg", "bg"):
                rgb = color_row.get(channel)
                if (
                    not isinstance(rgb, list)
                    or len(rgb) != 3
                    or not all(isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255 for value in rgb)
                ):
                    raise CompileFailure(
                        "material_state_invalid", f"{profile['material_id']}/{row_name}/{channel}"
                    )

        ramp = buckets.get("ramp")
        density = buckets.get("density")
        if not isinstance(ramp, list) or len(ramp) != 4:
            raise CompileFailure("lane_schema_invalid", f"{profile['material_id']}/ramp")
        if not isinstance(density, list) or len(density) != 4:
            raise CompileFailure("lane_schema_invalid", f"{profile['material_id']}/density")
        edge = indexed_rows(buckets.get("edge", []), EDGE_FACTS, f"{profile['material_id']}/edge")
        direction = indexed_rows(
            buckets.get("direction", []), DIRECTION_FACTS, f"{profile['material_id']}/direction"
        )
        flow_source = buckets.get("flow", [])
        if (
            isinstance(flow_source, list)
            and flow_source
            and all(isinstance(item, dict) and "fact" not in item for item in flow_source)
        ):
            if any(item.get("candidates") for item in flow_source):
                raise CompileFailure("lane_fact_invalid", f"{profile['material_id']}/flow legacy class")
            flow_source = []
        flow = indexed_rows(flow_source, DIRECTION_FACTS, f"{profile['material_id']}/flow")

        for lane, item in enumerate(ramp):
            if not isinstance(item, dict) or item.get("elv") != lane:
                raise CompileFailure("lane_schema_invalid", f"{profile['material_id']}/ramp/{lane}")
            rows.append(make_row(namespace, material, "tonal", "ramp.v1", lane, f"ramp[{lane}]", lane_candidates(item), admitted, u7_descriptors))
        for lane, item in enumerate(density):
            if not isinstance(item, dict) or item.get("bucket") != f"D{lane}":
                raise CompileFailure("lane_schema_invalid", f"{profile['material_id']}/density/{lane}")
            rows.append(make_row(namespace, material, "tonal", "density.v1", lane, f"density[{lane}]", lane_candidates(item), admitted, u7_descriptors))
        for lane, item in enumerate(edge):
            rows.append(make_row(namespace, material, "edge", "edge.v1", lane, f"edge[{lane}]", lane_candidates(item), admitted, u7_descriptors))
        for lane, item in enumerate(direction):
            rows.append(make_row(namespace, material, "direction", "direction.v1", lane, f"direction[{lane}]", lane_candidates(item), admitted, u7_descriptors))
        for lane, item in enumerate(flow):
            rows.append(make_row(namespace, material, "flow", "flow.v1", lane, f"flow[{lane}]", lane_candidates(item), admitted, u7_descriptors))

        mesh_present = "mesh_bundle" in buckets
        mesh = buckets.get("mesh_bundle", {})
        if not isinstance(mesh, dict):
            raise CompileFailure("lane_schema_invalid", f"{profile['material_id']}/mesh_bundle")
        if mesh_present:
            if (
                mesh.get("role") != "mesh_feature"
                or mesh.get("route_key") != "mesh_feature.bundle.v1"
                or mesh.get("lane_id") != 0
                or mesh.get("lane_name") != "mesh_bundle"
            ):
                raise CompileFailure("mesh_bundle_address_invalid", profile["material_id"])
            # Reject an authored mesh row: candidates, role_eligible_glyph_ids,
            # candidate admission fields, legacy `kind` rows, and aspect overrides
            # all die here — the profile keeps ADDRESS METADATA ONLY.
            extra_keys = sorted(set(mesh.keys()) - MESH_ADDRESS_METADATA_KEYS)
            if extra_keys:
                raise CompileFailure(
                    "mesh_row_authored_content",
                    f"{profile['material_id']}/mesh_bundle: {extra_keys}",
                )
        # A mesh profile is a LIVE structural owner only when it carries BOTH the
        # projected-feature-extent admission AND the full active-stroke (snake v2)
        # policy. That pair is the intrinsic marker of the structural mesh material
        # (mesh:200) — no magic material number is used. Carrying exactly one of the
        # two is a misconfiguration; carrying neither is an ordinary empty-absence
        # mesh row.
        extent_admission = compile_projected_feature_extent_admission(profile)
        active_stroke_topology = compile_active_stroke_topology(profile)
        # REQUIRED for every profile (FL-4512 Phase 2 abstention): the authored
        # no-match policy, carried verbatim into the material entry below.
        no_match_policy = compile_no_match_policy(profile)
        # A mesh profile that carries one structural block but not the other is a
        # misconfiguration, reported with the same typed reasons as before the
        # cutover (a live structural row requires BOTH extent admission and the
        # active-stroke snake-v2 policy).
        if namespace == "mesh":
            if extent_admission is not None and active_stroke_topology is None:
                raise CompileFailure(
                    "active_stroke_topology_missing", f"{profile['material_id']}/mesh_bundle"
                )
            if active_stroke_topology is not None and extent_admission is None:
                raise CompileFailure(
                    "feature_extent_admission_missing", f"{profile['material_id']}/mesh_bundle"
                )
            # A live structural profile requires the EXPLICIT mesh_bundle address
            # lane PLUS both policy blocks. Structural policies without the address
            # lane fail shut — the profile is the sole explicit role address and
            # the compiler is the sole candidate injector.
            if extent_admission is not None and active_stroke_topology is not None \
                    and not mesh_present:
                raise CompileFailure(
                    "mesh_structural_policy_without_address", profile["material_id"]
                )
        structural_live = (
            namespace == "mesh"
            and mesh_present
            and extent_admission is not None
            and active_stroke_topology is not None
        )
        # The compiler injects the mesh_feature.bundle.v1 row. When structural_live,
        # it is the 32 admission-artifact GlyphIds in exact stored order with strict
        # provenance, and only those structural GlyphIds are added to the mesh
        # supply; otherwise the mesh row is empty-absence.
        if structural_live:
            mesh_candidates = list(selected_ids)
            rows.append(
                make_row(
                    namespace, material, "mesh_feature", "mesh_feature.bundle.v1", 0,
                    "mesh_bundle", mesh_candidates, mesh_admitted, mesh_u7_descriptors,
                    candidate_admission=mesh_admission_provenance,
                )
            )
        else:
            mesh_candidates = []
            rows.append(
                make_row(
                    namespace, material, "mesh_feature", "mesh_feature.bundle.v1", 0,
                    "mesh_bundle", mesh_candidates, admitted, u7_descriptors,
                )
            )

        filled = buckets.get("filled", {})
        if not isinstance(filled, dict):
            raise CompileFailure("lane_schema_invalid", f"{profile['material_id']}/filled")
        filled_present = bool(filled)
        if filled_present and (
            filled.get("role") != "filled"
            or filled.get("route_key") != "filled.field.v1"
            or filled.get("lane_id") != 0
            or filled.get("lane_name") != "filled_field"
        ):
            raise CompileFailure("filled_field_address_invalid", profile["material_id"])
        filled_region = filled.get("region_class") if filled_present else None
        filled_priority = filled.get("priority_u16") if filled_present else None
        if filled_present and (
            not isinstance(filled_region, int) or isinstance(filled_region, bool)
            or not 0 <= filled_region <= 65535
            or not isinstance(filled_priority, int) or isinstance(filled_priority, bool)
            or not 0 <= filled_priority <= 65535
        ):
            raise CompileFailure("filled_field_target_invalid", profile["material_id"])
        rows.append(
            make_row(
                namespace, material, "filled", "filled.field.v1", 0,
                "filled_field", lane_candidates(filled) if filled_present else [],
                admitted, u7_descriptors, primitive_masks, filled_region,
            )
        )

        scoring = profile.get("scoring", {})
        weights = scoring.get("role_weights", {}) if isinstance(scoring, dict) else {}
        if not isinstance(weights, dict):
            raise CompileFailure("scoring_schema_invalid", profile["material_id"])
        base_weights: list[int | float] = []
        for key in ROLE_WEIGHT_KEYS:
            weight = weights.get(key, 1)
            if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0:
                raise CompileFailure("scoring_schema_invalid", f"{profile['material_id']}/{key}")
            base_weights.append(weight)
        detail = scoring.get("detail_contrast", 0) if isinstance(scoring, dict) else 0
        tone = scoring.get("tone_contrast", 0) if isinstance(scoring, dict) else 0
        density_bias = scoring.get("density_bias", 0) if isinstance(scoring, dict) else 0
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in (detail, tone, density_bias)
        ):
            raise CompileFailure("scoring_schema_invalid", profile["material_id"])
        detail_scale = detail / 100.0
        tone_scale = tone / 100.0
        density_scale = density_bias / 100.0
        boosts = (
            tone_scale,
            detail_scale,
            tone_scale,
            detail_scale,
            -density_scale if density_scale < 0 else 0,
            density_scale if density_scale > 0 else 0,
        )
        effective_weights: list[int | float] = []
        for index in range(6):
            effective = max(0, base_weights[index] + boosts[index])
            effective_weights.append(int(effective) if float(effective).is_integer() else effective)
        # extent_admission + active_stroke_topology were computed above as the
        # structural-live gate. A live structural row cannot reach Phase S without
        # the full v2 snake policy; the retired silhouette probe block has no role
        # in this admission decision and contributes no byte to registry identity.
        if active_stroke_topology is not None:
            # Registry identity is over sorted profile keys and the complete
            # compiled v2 block hash.  Authored document order cannot change it.
            structural_registry_rows.append(
                {
                    "profile_namespace": namespace,
                    "material_key": material,
                    "active_stroke_topology_sha256":
                        active_stroke_topology["content_sha256"],
                }
            )
        materials.append(
            {
                "colors": colors,
                "material_key": material,
                "profile_namespace": namespace,
                "role_weights": effective_weights,
                "filled_region_class": filled_region,
                "filled_priority_u16": filled_priority,
                "projected_feature_extent_admission": extent_admission,
                "active_stroke_topology": active_stroke_topology,
                # Authored abstention policy, verbatim (validated above).
                "no_match_policy": no_match_policy,
            }
        )

    rows.sort(key=lambda item: (item["profile_namespace"], item["material_key"], item["role"], item["route_key"], item["lane"]))
    materials.sort(key=lambda item: (item["profile_namespace"], item["material_key"]))
    structural_registry_rows.sort(
        key=lambda item: (item["profile_namespace"], item["material_key"])
    )
    if not structural_registry_rows:
        raise CompileFailure(
            "structural_admission_registry_missing",
            "at least one complete structural profile is required",
        )
    active_stroke_topology_registry_sha256 = sha256_bytes(
        canonical_bytes(structural_registry_rows)
    )
    scale_assets: list[dict[str, Any]] = []
    scale_golden_hash: str | None = None
    metric_registry_hash: str | None = None
    if metric_registry is not None:
        if repo_root is None or scale_golden_raw is None:
            raise CompileFailure(
                "mesh_asset_metric_schema_invalid", "repo root and golden fixture required"
            )
        scale_assets, scale_golden_hash, metric_registry_hash = compile_mesh_asset_metrics(
            metric_registry, repo_root, scale_golden_raw
        )
    aspect_assets: list[dict[str, Any]] = []
    aspect_rows: list[dict[str, Any]] = []
    aspect_golden_hash: str | None = None
    # An aspect-free compile stays reachable HERE on purpose: focused unit tests
    # drive compile_artifact directly to exercise unrelated rows, and forcing the
    # aspect supply on them would couple every guard test to aspect compilation.
    # The publication boundary is what must be closed, not this in-memory call —
    # see the aspect-supply guard in main() before the artifact is written
    # (FL-4772). The CLI defaults --aspect-authoring, so no published pool can
    # arrive here with None.
    if aspect_authoring is not None:
        if aspect_golden_raw is None or metric_registry_hash is None:
            raise CompileFailure(
                "aspect_authoring_schema_invalid",
                "metric registry and golden fixture required",
            )
        aspect_assets, aspect_rows, aspect_golden_hash = compile_aspect_assets(
            aspect_authoring,
            rows,
            # Aspect view-variants of the mesh row select SUBSETS of the structural
            # admission GlyphIds, so the aspect supply must include them (the mesh
            # supersets). Non-mesh aspects never reference the extra structural ids.
            mesh_admitted,
            mesh_u7_descriptors,
            scale_assets,
            metric_registry_hash,
            aspect_golden_raw,
        )
    if extent_golden_raw is None:
        extent_golden_raw = (
            Path(__file__).resolve().parents[1]
            / "assets/glyphs/fixtures/fl4512_projected_feature_extent_golden_v2.json"
        ).read_bytes()
    try:
        EXTENT.validate_fixture(json.loads(extent_golden_raw))
    except (json.JSONDecodeError, EXTENT.ProjectedFeatureExtentError) as exc:
        raise CompileFailure("feature_extent_golden_fixture_invalid", str(exc)) from exc
    extent_golden_hash = sha256_bytes(extent_golden_raw)
    artifact: dict[str, Any] = {
        "header": {
            "absence_policy_version": ABSENCE_POLICY_VERSION,
            "atlas_hash": sha256_bytes(atlas_raw),
            "catalog_hash": sha256_bytes(catalog_raw),
            # Bind the mesh structural row's provenance into the pool identity:
            # the raw strict admission-artifact file hash and the universal
            # Unicode catalog source hash. artifact_sha256 covers the whole header,
            # so it covers both.
            "mesh_admission_artifact_sha256": raw_admission_artifact_sha256,
            "universal_catalog_sha256": sha256_bytes(universal_catalog_raw),
            "compiler_commit": compiler_commit,
            "descriptor_versions": [
                "global6.v1",
                "mesh_projected_feature.v1",
                U7.REDUCTION_VERSION,
                U7.SKELETON_VERSION,
                "fl4512.primitive_field.v1",
            ],
            "glyph_manifest_hash": atlas.get("registry_sha256"),
            "page16_hash": sha256_bytes(page16_raw),
            "pool_revision": pool_revision,
            "profile_hash": sha256_bytes(profile_raw),
            "quantization_version": D6.QUANTIZATION_VERSION if aspect_assets else None,
            "aspect_golden_fixture_sha256": aspect_golden_hash,
            "scale_quantization_version": (
                SCALE.SCALE_QUANTIZATION_VERSION if scale_assets else None
            ),
            "scale_golden_fixture_sha256": scale_golden_hash,
            "extent_quantization_version": EXTENT.EXTENT_QUANTIZATION_VERSION,
            "feature_extent_golden_fixture_sha256": extent_golden_hash,
            "mesh_asset_metric_registry_sha256": metric_registry_hash,
            # This hash is the lifecycle reset identity consumed by the
            # Active Stroke owner. Materials below retain both source hashes,
            # making the complete preimage independently reconstructable.
            "active_stroke_topology_registry_sha256":
                active_stroke_topology_registry_sha256,
            "schema_id": SCHEMA_ID,
            "schema_version": 1,
            "scorer_ids": [
                TONAL_SCORER_ID, U7_SCORER_ID, FILLED_SCORER_ID,
                # FL-4763 explicit structural decline; see DECLINE_SCORER_ID.
                DECLINE_SCORER_ID,
            ],
        },
        "aspect_assets": aspect_assets,
        "aspect_rows": aspect_rows,
        "scale_assets": scale_assets,
        "materials": materials,
        "rows": rows,
    }
    # Normalize the WHOLE tree before the final hash so the artifact object
    # (and therefore the written file) carries exactly the numbers the
    # canonical hash was computed over — the loader reparses the file and
    # must reproduce these bytes with cJSON to accept the pool (FL-4589).
    artifact = normalize_numbers(artifact)
    artifact["artifact_sha256"] = sha256_bytes(canonical_bytes(artifact))
    return artifact


def renormalize_artifact(out_path: str) -> int:
    # FL-4589 recovery mode: re-emit the EXISTING compiled pool with
    # cJSON-compatible number formatting and recomputed self-hashes.
    #
    # Why this mode exists: the artifact on disk is semantically valid (it
    # was produced by this compiler), but its embedded hashes were computed
    # over python float formatting ("0.0") that cJSON reprints as "0", so
    # the C++ loader rejects the pool and every declared-profile cell
    # renders the absence glyph. A FULL recompile is currently blocked
    # because the FL-4482 universal atlas registry moved to a multi-pack
    # v2 layout this compiler does not consume yet (pack_index tiers).
    # Renormalizing changes NO decision data — 0.0 and 0 are the same JSON
    # number — it only makes the bytes match what the loader will hash.
    #
    # Self-hashes recomputed here mirror the compile path exactly:
    #   rows[*].row_sha256                      (payload minus the field)
    #   materials[*].projected_feature_extent_admission.table_sha256
    #   scale_assets[*].scale_asset_sha256
    #   artifact_sha256                         (whole tree, last)
    # Header *_sha256 fields hash INPUT FILES on disk and stay untouched.
    artifact = json.loads(Path(out_path).read_bytes())
    if artifact.get("header", {}).get("schema_id") != "fl4512.compiled_pool.v1":
        print("FL4512_RENORMALIZE_REJECT not_a_compiled_pool")
        return 1
    # FL-4652 / FL-4512 single-owner fence: recovery predates the authored
    # silhouette and Active-Stroke policies.  Once either policy block, or
    # their registry identity, exists in an artifact, only the canonical
    # profile compiler may derive their hashes.  Reject before normalization
    # so this recovery path cannot bless a generated-only policy edit and
    # cannot change even the formatting bytes of the rejected target.
    header = artifact.get("header")
    registry_present = (
        isinstance(header, dict)
        and "active_stroke_topology_registry_sha256" in header
    )
    structural_block_present = any(
        isinstance(material, dict)
        and "active_stroke_topology" in material
        for material in artifact.get("materials", [])
    )
    if registry_present or structural_block_present:
        print(
            "FL4512_RENORMALIZE_REJECT "
            "structural_policy_requires_canonical_compile"
        )
        return 1
    artifact = normalize_numbers(artifact)

    def rehash_self(payload: dict[str, Any], field: str) -> None:
        # Recompute a self-hash the way the compiler does: hash the
        # payload WITHOUT the field, then store the result in the field.
        payload.pop(field, None)
        payload[field] = sha256_bytes(canonical_bytes(payload))

    for row in artifact.get("rows", []):
        rehash_self(row, "row_sha256")
    for material in artifact.get("materials", []):
        admission = material.get("projected_feature_extent_admission")
        if isinstance(admission, dict):
            rehash_self(admission, "table_sha256")
    for asset in artifact.get("scale_assets", []):
        rehash_self(asset, "scale_asset_sha256")
    artifact.pop("artifact_sha256", None)
    artifact["artifact_sha256"] = sha256_bytes(canonical_bytes(artifact))
    encoded = json.dumps(artifact, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    Path(out_path).write_text(encoded)
    print(
        f"FL4512_RENORMALIZE_PASS rows={len(artifact.get('rows', []))} "
        f"artifact_sha256={artifact['artifact_sha256']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--renormalize", action="store_true",
                        help="FL-4589: rewrite --out in place with cJSON-compatible "
                             "numbers and recomputed self-hashes; no recompile")
    parser.add_argument("--profiles", default="assets/glyphs/profiles/material_rendering_profiles.v1.json")
    parser.add_argument("--catalog", default="assets/glyphs/generated/material.additive.v1.shape_catalog.json")
    parser.add_argument("--atlas", default="assets/glyphs/generated/fl4482.native_atlas_registry.v1.json")
    parser.add_argument("--page16", default="assets/glyphs/atlases/material.additive.v1.page16_rgba8.json")
    # FL-4772: every other canonical supply below carries its canonical default,
    # and this one did not — so a canonical invocation that simply omitted the
    # flag produced an aspect-free pool. Default it like its siblings; the
    # compiler additionally fails closed if it is ever resolved to None.
    parser.add_argument(
        "--aspect-authoring",
        default="assets/glyphs/profiles/fl4512.aspect_pool_authoring.v1.json",
    )
    parser.add_argument(
        "--aspect-golden",
        default="assets/glyphs/fixtures/fl4512_d6_aspect_golden_v1.json",
    )
    parser.add_argument(
        "--metric-registry",
        default="assets/glyphs/profiles/fl4512.mesh_asset_metric.v1.json",
    )
    parser.add_argument(
        "--scale-golden",
        default="assets/glyphs/fixtures/fl4512_projected_scale_golden_v1.json",
    )
    parser.add_argument(
        "--extent-golden",
        default="assets/glyphs/fixtures/fl4512_projected_feature_extent_golden_v2.json",
    )
    # Immutable admission supply: the strict offline Unicode admission artifact
    # (the sole source of the mesh structural row) + the universal Unicode catalog.
    parser.add_argument(
        "--admission-artifact",
        default="assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json",
    )
    parser.add_argument(
        "--universal-catalog",
        default="assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json",
    )
    parser.add_argument("--out", default="assets/glyphs/generated/fl4512.compiled_pool.v1.json")
    parser.add_argument("--compiler-commit")
    parser.add_argument("--pool-revision", type=int, default=2)
    args = parser.parse_args()
    if args.renormalize:
        # Recovery path only: no inputs are read, nothing is recompiled.
        return renormalize_artifact(args.out)
    repo = Path(__file__).resolve().parents[1]
    try:
        profiles, profile_raw = read_json(args.profiles)
        catalog, catalog_raw = read_json(args.catalog)
        atlas, atlas_raw = read_json(args.atlas)
        page16, page16_raw = read_json(args.page16)
        # FL-4772: read unconditionally, like every other canonical supply here.
        # The old `if args.aspect_authoring:` guard was the last branch that let
        # a compile proceed with no aspect supply at all; an empty or missing
        # flag must now surface as a read error, never as a silent aspect-free
        # artifact.
        aspect_authoring, _aspect_authoring_raw = read_json(args.aspect_authoring)
        aspect_golden_raw = Path(args.aspect_golden).read_bytes()
        metric_registry, _metric_registry_raw = read_json(args.metric_registry)
        scale_golden_raw = Path(args.scale_golden).read_bytes()
        extent_golden_raw = Path(args.extent_golden).read_bytes()
        admission_artifact, admission_artifact_raw = read_json(args.admission_artifact)
        universal_catalog, universal_catalog_raw = read_json(args.universal_catalog)
        artifact = compile_artifact(
            profiles,
            profile_raw,
            catalog,
            catalog_raw,
            atlas,
            atlas_raw,
            page16,
            page16_raw,
            args.compiler_commit or current_commit(repo),
            args.pool_revision,
            aspect_authoring,
            repo,
            aspect_golden_raw,
            metric_registry,
            scale_golden_raw,
            extent_golden_raw,
            admission_artifact=admission_artifact,
            admission_artifact_raw=admission_artifact_raw,
            universal_catalog=universal_catalog,
            universal_catalog_raw=universal_catalog_raw,
        )
        # FL-4772 publication boundary. This is the door that was open: a pool
        # with no aspect content is well-formed and satisfies every other gate,
        # so nothing downstream noticed it. compile_artifact itself stays
        # permissive because focused unit tests compile aspect-free rows on
        # purpose; publication is where an aspect-free pool must be refused.
        # Raised inside the try so it reports through FL4512_COMPILE_REJECT and
        # returns non-zero before anything is written.
        if not artifact.get("aspect_assets") or not artifact.get("aspect_rows"):
            raise CompileFailure(
                "aspect_free_pool_not_publishable",
                "compiled pool carries no aspect assets or rows; refusing to "
                "publish an aspect-free canonical pool",
            )
        if artifact["header"].get("quantization_version") is None:
            raise CompileFailure(
                "aspect_free_pool_not_publishable",
                "compiled pool header carries quantization_version=null; "
                "refusing to publish an aspect-free canonical pool",
            )
    except (CompileFailure, json.JSONDecodeError, OSError, subprocess.CalledProcessError) as exc:
        print(f"FL4512_COMPILE_REJECT {exc}")
        return 2
    encoded = json.dumps(artifact, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    if args.out == "-":
        sys.stdout.write(encoded)
    else:
        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, output)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
    print(
        f"FL4512_COMPILE_PASS rows={len(artifact['rows'])} "
        f"artifact_sha256={artifact['artifact_sha256']}",
        file=sys.stderr if args.out == "-" else sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
