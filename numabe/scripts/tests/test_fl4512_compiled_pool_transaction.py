#!/usr/bin/env python3
"""Contracts for the atomic FL-4512 U4 compiled pool artifact."""

from __future__ import annotations

import copy
import ast
import hashlib
import importlib.util
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPILER_PATH = ROOT / "scripts/compile_fl4512_pools.py"
ARTIFACT_PATH = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
CATALOG_PATH = ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
ATLAS_PATH = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
PAGE16_PATH = ROOT / "assets/glyphs/atlases/material.additive.v1.page16_rgba8.json"
ASPECT_AUTHORING_PATH = ROOT / "assets/glyphs/profiles/fl4512.aspect_pool_authoring.v1.json"
METRIC_REGISTRY_PATH = ROOT / "assets/glyphs/profiles/fl4512.mesh_asset_metric.v1.json"
ASPECT_GOLDEN_PATH = ROOT / "assets/glyphs/fixtures/fl4512_d6_aspect_golden_v1.json"
SCALE_GOLDEN_PATH = ROOT / "assets/glyphs/fixtures/fl4512_projected_scale_golden_v1.json"
RESOLVER_PATH = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
PRESENTER_PATH = ROOT / "platform/terminal_gl_present.cpp"
SEAM_MODULE_PATH = ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"
FRAME_PATH = ROOT / "engine/render/render_frame_input.h"
# Post admission cutover: the mesh row is the strict admission artifact's
# selected_row (canonical ascending GlyphId), NOT a profile-authored array.
_ADMISSION_ARTIFACT = json.loads(
    (ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json").read_text()
)
MESH_STRUCTURAL_GIDS = [row["glyph_id"] for row in _ADMISSION_ARTIFACT["selected_row"]]
MESH_ADMISSION_RECEIPT = _ADMISSION_ARTIFACT["receipt"]["content_hash"]

SPEC = importlib.util.spec_from_file_location("compile_fl4512_pools", COMPILER_PATH)
assert SPEC and SPEC.loader
COMPILER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPILER)


def canonical_sha(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def committed_profiles() -> tuple[dict, bytes]:
    raw = (ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json").read_bytes()
    return json.loads(raw), raw


def compile_doc(
    profiles: dict,
    profile_raw: bytes | None = None,
    aspect_authoring: dict | None = None,
    aspect_golden_raw: bytes | None = None,
    metric_registry: dict | None = None,
    scale_golden_raw: bytes | None = None,
) -> dict:
    catalog_raw = CATALOG_PATH.read_bytes()
    atlas_raw = ATLAS_PATH.read_bytes()
    page16_raw = PAGE16_PATH.read_bytes()
    # Admission cutover: the strict admission artifact + universal Unicode catalog
    # are now IMMUTABLE compiler inputs (the sole mesh-row source). The ~130 s
    # strict validate is stubbed to a no-op here so transaction tests stay fast;
    # the strict validation itself is proven in the admission + cutover suites.
    admission_path = ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json"
    universal_path = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json"
    admission_raw = admission_path.read_bytes()
    universal_raw = universal_path.read_bytes()
    _saved_validate = COMPILER.ADMISSION.validate_artifact_documents
    COMPILER.ADMISSION.validate_artifact_documents = lambda *a, **k: None
    try:
        return COMPILER.compile_artifact(
            profiles,
            profile_raw or json.dumps(profiles).encode(),
            json.loads(catalog_raw),
            catalog_raw,
            json.loads(atlas_raw),
            atlas_raw,
            json.loads(page16_raw),
            page16_raw,
            "test-commit",
            aspect_authoring=aspect_authoring,
            repo_root=ROOT,
            aspect_golden_raw=aspect_golden_raw,
            metric_registry=metric_registry,
            scale_golden_raw=scale_golden_raw,
            admission_artifact=json.loads(admission_raw),
            admission_artifact_raw=admission_raw,
            universal_catalog=json.loads(universal_raw),
            universal_catalog_raw=universal_raw,
        )
    finally:
        COMPILER.ADMISSION.validate_artifact_documents = _saved_validate


def capacity_sized_candidates(profiles: dict) -> list[int]:
    """Return an actually populated U4 row at the declared capacity."""
    for profile in profiles["profiles"]:
        for buckets in profile["role_buckets"].values():
            rows = buckets if isinstance(buckets, list) else [buckets]
            for row in rows:
                candidates = row.get("candidates", [])
                if len(candidates) == 32:
                    return candidates
    raise AssertionError("fixture has no capacity-sized candidate row")


def committed_aspect_authoring() -> dict:
    return json.loads(ASPECT_AUTHORING_PATH.read_text())


def committed_metric_registry() -> dict:
    return json.loads(METRIC_REGISTRY_PATH.read_text())


def compile_with_aspects(authoring: dict, registry: dict | None = None) -> dict:
    profiles, profile_raw = committed_profiles()
    return compile_doc(
        profiles,
        profile_raw,
        authoring,
        ASPECT_GOLDEN_PATH.read_bytes(),
        registry or committed_metric_registry(),
        SCALE_GOLDEN_PATH.read_bytes(),
    )


def aspect_asset(artifact: dict, geometry_path: str = "assets/meshes/pieta_shaded.akm") -> dict:
    return next(
        asset for asset in artifact["aspect_assets"]
        if asset["geometry_path"] == geometry_path
    )


def metric_asset(registry: dict, geometry_path: str) -> dict:
    return next(
        asset for asset in registry["assets"]
        if asset["geometry_path"] == geometry_path
    )


def authoring_asset(authoring: dict, asset_id: str) -> dict:
    return next(asset for asset in authoring["assets"] if asset["asset_id"] == asset_id)


def test_committed_artifact_has_complete_hashed_transaction():
    artifact = json.loads(ARTIFACT_PATH.read_text())
    assert artifact["header"]["schema_id"] == "fl4512.compiled_pool.v1"
    assert artifact["header"]["absence_policy_version"] == "fl4512.absence.v1"
    assert len(artifact["materials"]) == 8
    assert len(artifact["rows"]) == 8 * 33
    addresses = set()
    for row in artifact["rows"]:
        payload = dict(row)
        digest = payload.pop("row_sha256")
        assert digest == canonical_sha(payload)
        assert row["candidate_count"] == len(row["candidate_glyph_ids"])
        assert row["candidate_count"] == len(row["candidate_descriptors"])
        assert row["candidate_count"] <= row["capacity"] == 32
        assert "fallback_cp437" not in row
        address = (
            row["profile_namespace"],
            row["material_key"],
            row["role"],
            row["route_key"],
            row["lane"],
        )
        assert address not in addresses
        addresses.add(address)
    payload = dict(artifact)
    digest = payload.pop("artifact_sha256")
    assert digest == canonical_sha(payload)


def test_renormalize_rejects_structural_policy_without_changing_target(
    tmp_path: Path,
) -> None:
    # FL-4652 / FL-4512: a generated-only policy mutation must never gain
    # authority through the legacy numeric-format recovery lane.  Preserve
    # the exact mutated bytes so the rejection also proves no partial write.
    target = tmp_path / "fl4512.compiled_pool.v1.json"
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    mesh = next(
        material for material in artifact["materials"]
        if material["profile_namespace"] == "mesh"
        and material["material_key"] == 200
    )
    mesh["active_stroke_topology"]["alpha_q0_16"] += 1
    mutated_bytes = (
        json.dumps(artifact, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    target.write_bytes(mutated_bytes)

    result = subprocess.run(
        [
            sys.executable,
            str(COMPILER_PATH),
            "--renormalize",
            "--out",
            str(target),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert result.stdout.strip() == (
        "FL4512_RENORMALIZE_REJECT "
        "structural_policy_requires_canonical_compile"
    )
    assert target.read_bytes() == mutated_bytes


def test_default_compile_remains_null_aspect_until_runtime_cutover():
    profiles, profile_raw = committed_profiles()
    artifact = compile_doc(profiles, profile_raw)
    assert artifact["header"]["quantization_version"] is None
    assert artifact["header"]["aspect_golden_fixture_sha256"] is None
    assert artifact["aspect_assets"] == []
    assert artifact["aspect_rows"] == []


def test_d6_authoring_compiles_one_hash_closed_pieta_region():
    artifact = compile_with_aspects(committed_aspect_authoring())
    assert artifact["header"]["quantization_version"] == 3
    assert artifact["header"]["aspect_golden_fixture_sha256"] == hashlib.sha256(
        ASPECT_GOLDEN_PATH.read_bytes()
    ).hexdigest()
    assert len(artifact["aspect_assets"]) == 22
    assert len(artifact["aspect_rows"]) == 22
    asset = aspect_asset(artifact)
    assert asset["geometry_path"] == "assets/meshes/pieta_shaded.akm"
    assert asset["geometry_sha256"] == hashlib.sha256(
        (ROOT / asset["geometry_path"]).read_bytes()
    ).hexdigest()
    expected_asset_id = hashlib.sha256(
        (ROOT / asset["geometry_path"]).read_bytes()
        + struct.pack("<iii", *asset["anchor_q24_8"])
        + asset["axis_convention"].encode("utf-8")
        + struct.pack("<III", *asset["asset_metric_q24_8"])
    ).hexdigest()
    assert asset["asset_id"] == expected_asset_id
    assert asset["asset_identity_version"] == "fl4512.aspect_asset_identity.v2"
    assert asset["asset_metric_q24_8"] == [61440, 61440, 245760]
    pose = asset["pose_buckets"][0]
    assert len(pose["flat_map"]) == 64
    assert len(pose["region_table"]) == 1
    assert pose["region_table"][0]["member_cells"] == list(range(64))
    assert pose["flat_map_hash"] == canonical_sha(pose["flat_map"])
    assert pose["region_table_hash"] == canonical_sha(pose["region_table"])
    row = next(
        row for row in artifact["aspect_rows"]
        if row["geometry_sha256"] == asset["geometry_sha256"]
    )
    assert row["quantization_version"] == 3
    assert row["descriptor_quantization_version"] is None
    assert row["candidate_count"] == 32
    assert row["row_sha256"] == canonical_sha({
        key: value for key, value in row.items() if key != "row_sha256"
    })


def test_d6_runtime_loader_stages_only_hash_closed_aspect_tables():
    source = RESOLVER_PATH.read_text()
    assert "static bool Fl4512LoadAspectAssets(" in source
    assert "Fl4512HeaderFileHashMatches(header, \"aspect_golden_fixture_sha256\"" in source
    assert "Fl4512GlobalAspectGoldenPathV1()" in source
    assert "Fl4512AspectRowMatchesAddress" in source
    assert "matched_rows != 1" in source
    assert "Fl4512JsonValueHashMatches(flat_map, flat_hash)" in source
    assert "Fl4512JsonValueHashMatches(regions, region_hash)" in source
    assert "strcmp(asset_id, scale_assets[i].asset_id)" in source
    assert "aspect_asset_invalid" in source
    # Staging is deliberately not presentation: a qv3 table cannot become a
    # second mesh-pool owner before the per-cell GPU address texture exists.
    assert "per-cell GPU aspect address" in source


def test_d6_runtime_lookup_uses_pinned_qv3_integer_fold_and_compiled_map():
    source = RESOLVER_PATH.read_text()
    assert "bool Fl4512AspectRegionForMeshQ15(" in source
    assert "const int64_t nu = z > 0 ? x" in source
    assert "const int64_t nv = z > 0 ? y" in source
    assert "8 * (nu + s) - 1" in source
    assert "8 * (nv + s) - 1" in source
    assert "match->region_lowest_cell[pose_index][out->cell_index]" in source
    assert "U5 remains the sole selector/presenter owner" in source


def test_d6_seam_connects_corner_cycle_without_diagonal_shortcut():
    authoring = committed_aspect_authoring()
    pieta_metric = metric_asset(committed_metric_registry(), "assets/meshes/pieta_shaded.akm")
    pose = authoring_asset(authoring, pieta_metric["asset_id"])["pose_buckets"][0]
    base = pose["variants"][0]
    split = copy.deepcopy(base)
    split["variant_id"] = "corner_subset"
    split["candidate_glyph_ids"] = MESH_STRUCTURAL_GIDS[:16]
    pose["variants"].append(split)

    # The four corners connect through the four reflected edge seams.
    for cell in (0, 7, 56, 63):
        pose["cell_variant_ids"][cell] = "corner_subset"
    artifact = compile_with_aspects(authoring)
    regions = aspect_asset(artifact)["pose_buckets"][0]["region_table"]
    assert len(regions) == 2
    assert [0, 7, 56, 63] in [region["member_cells"] for region in regions]

    # Cells 0 and 63 only meet diagonally at the south-pole point.  Without
    # the forbidden clique they remain distinct regions under equal payload.
    authoring = committed_aspect_authoring()
    pose = authoring_asset(authoring, pieta_metric["asset_id"])["pose_buckets"][0]
    split = copy.deepcopy(pose["variants"][0])
    split["variant_id"] = "diagonal_subset"
    split["candidate_glyph_ids"] = MESH_STRUCTURAL_GIDS[:16]
    pose["variants"].append(split)
    pose["cell_variant_ids"][0] = "diagonal_subset"
    pose["cell_variant_ids"][63] = "diagonal_subset"
    artifact = compile_with_aspects(authoring)
    subset_rows = [row for row in artifact["aspect_rows"] if row["candidate_count"] == 16]
    assert len(subset_rows) == 2
    assert [row["member_cells"] for row in subset_rows] == [[0], [63]]


def test_d6_authoring_joins_metric_identity_and_rejects_redeclared_state():
    source = committed_aspect_authoring()
    baseline = aspect_asset(compile_with_aspects(source))
    pieta_geometry = "assets/meshes/pieta_shaded.akm"
    pieta_id = baseline["asset_id"]
    changed_registry = copy.deepcopy(committed_metric_registry())
    metric = metric_asset(changed_registry, pieta_geometry)
    metric["anchor_q24_8"][0] += 1
    geometry_raw = (ROOT / metric["geometry_path"]).read_bytes()
    metric["asset_id"] = COMPILER._aspect_asset_id(
        geometry_raw,
        metric["anchor_q24_8"],
        metric["axis_convention"],
        metric["asset_metric_q24_8"],
    )
    changed_registry["registry_sha256"] = canonical_sha(
        sorted(changed_registry["assets"], key=lambda item: item["asset_id"])
    )
    changed = copy.deepcopy(source)
    authoring_asset(changed, pieta_id)["asset_id"] = metric["asset_id"]
    changed_asset = aspect_asset(
        compile_with_aspects(changed, changed_registry), metric["geometry_path"]
    )
    assert changed_asset["geometry_sha256"] == baseline["geometry_sha256"]
    assert changed_asset["aspect_authoring_sha256"] != baseline["aspect_authoring_sha256"]
    assert changed_asset["asset_id"] != baseline["asset_id"]

    redeclared = copy.deepcopy(source)
    authoring_asset(redeclared, pieta_id)["asset_metric_q24_8"] = [61440, 61440, 245760]
    with pytest.raises(COMPILER.CompileFailure, match="aspect_authoring_schema_invalid"):
        compile_with_aspects(redeclared)

    broken = copy.deepcopy(source)
    authoring_asset(broken, pieta_id)["cell_variant_ids"] = []
    with pytest.raises(COMPILER.CompileFailure, match="aspect_authoring_schema_invalid"):
        compile_with_aspects(broken)

    broken = copy.deepcopy(source)
    authoring_asset(broken, pieta_id)["pose_buckets"][0]["cell_variant_ids"].pop()
    with pytest.raises(COMPILER.CompileFailure, match="aspect_cell_assignment_invalid"):
        compile_with_aspects(broken)

    broken = copy.deepcopy(source)
    authoring_asset(broken, pieta_id)["asset_id"] = "0" * 64
    with pytest.raises(COMPILER.CompileFailure, match="aspect_metric_join_invalid"):
        compile_with_aspects(broken)

    broken = copy.deepcopy(source)
    authoring_asset(broken, pieta_id)["quantization_version"] = 1
    with pytest.raises(COMPILER.CompileFailure, match="aspect_authoring_schema_invalid"):
        compile_with_aspects(broken)

    broken_registry = copy.deepcopy(committed_metric_registry())
    metric_asset(broken_registry, pieta_geometry)["asset_metric_q24_8"][0] = 0
    broken_registry["registry_sha256"] = canonical_sha(
        sorted(broken_registry["assets"], key=lambda item: item["asset_id"])
    )
    with pytest.raises(COMPILER.CompileFailure, match="mesh_asset_metric_record_invalid"):
        compile_with_aspects(source, broken_registry)


def test_filled_role_has_one_v2_row_per_profile():
    artifact = json.loads(ARTIFACT_PATH.read_text())
    rows = [row for row in artifact["rows"] if row["role"] == "filled"]
    assert len(rows) == len(artifact["materials"])
    assert all(row["route_key"] == "filled.field.v1" for row in rows)
    assert all(row["lane"] == 0 and row["lane_name"] == "filled_field" for row in rows)
    assert all(row["scorer_id"] == "S_pf.v1" for row in rows)


def test_mesh_bundle_has_one_role_level_row():
    artifact = json.loads(ARTIFACT_PATH.read_text())
    rows = [
        row
        for row in artifact["rows"]
        if row["material_key"] == 200 and row["role"] == "mesh_feature"
    ]
    assert len(rows) == 1
    assert rows[0]["profile_namespace"] == "mesh"
    assert rows[0]["route_key"] == "mesh_feature.bundle.v1"
    assert rows[0]["lane"] == 0
    assert rows[0]["lane_name"] == "mesh_bundle"
    assert rows[0]["candidate_glyph_ids"] == MESH_STRUCTURAL_GIDS
    assert rows[0]["role_eligible_glyph_ids"] == MESH_STRUCTURAL_GIDS
    assert rows[0]["candidate_admission_schema"] == COMPILER.ADMISSION.ARTIFACT_SCHEMA
    assert rows[0]["candidate_admission_receipt_id"] == MESH_ADMISSION_RECEIPT
    assert rows[0]["mesh_admission_artifact_sha256"] == hashlib.sha256(
        (ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json").read_bytes()
    ).hexdigest()
    topology = rows[0]["topology_constraint"]
    assert topology == {
        "background_connectivity": 4,
        "foreground_connectivity": 8,
        "match_fields": ["beta0", "beta1"],
        "symmetry_stage": "post_assignment_observation",
    }
    partitions = rows[0]["topology_partitions"]
    assert sum((part["candidate_glyph_ids"] for part in partitions), []) != []
    partitioned = [gid for part in partitions for gid in part["candidate_glyph_ids"]]
    assert sorted(partitioned) == sorted(MESH_STRUCTURAL_GIDS)
    # Partitions carry the (beta0, beta1) topology signature; at least one is
    # populated (specific per-glyph ids are the admission artifact's, not authored).
    assert all("beta0" in part and "beta1" in part for part in partitions)
    assert any(part["candidate_glyph_ids"] for part in partitions)


def test_active_stroke_v2_and_registry_are_one_hash_closed_transaction():
    artifact = json.loads(ARTIFACT_PATH.read_text())
    material = next(
        row for row in artifact["materials"]
        if row["profile_namespace"] == "mesh" and row["material_key"] == 200
    )
    topology = material["active_stroke_topology"]
    assert topology["schema"] == "fl4512.active_stroke_topology.v2"
    payload = dict(topology)
    digest = payload.pop("content_sha256")
    assert digest == canonical_sha(payload)
    assert topology["gaussian_table_sha256"] == canonical_sha(
        topology["gaussian_table_q0_16"]
    )

    registry_rows = [
        {
            "profile_namespace": row["profile_namespace"],
            "material_key": row["material_key"],
            "active_stroke_topology_sha256":
                row["active_stroke_topology"]["content_sha256"],
        }
        for row in artifact["materials"]
        if row["active_stroke_topology"] is not None
    ]
    registry_rows.sort(key=lambda row: (row["profile_namespace"], row["material_key"]))
    assert artifact["header"]["active_stroke_topology_registry_sha256"] == (
        canonical_sha(registry_rows)
    )


def test_active_stroke_missing_extra_and_stale_source_hash_reject():
    profiles, _ = committed_profiles()
    mesh = next(row for row in profiles["profiles"] if row["material_id"] == "mesh:200")
    del mesh["active_stroke_topology"]
    with pytest.raises(
        COMPILER.CompileFailure, match="active_stroke_topology_missing"
    ):
        compile_doc(profiles)

    profiles, _ = committed_profiles()
    mesh = next(row for row in profiles["profiles"] if row["material_id"] == "mesh:200")
    mesh["active_stroke_topology"]["unexpected"] = 1
    with pytest.raises(
        COMPILER.CompileFailure, match="active_stroke_topology_invalid"
    ):
        compile_doc(profiles)

    profiles, _ = committed_profiles()
    mesh = next(row for row in profiles["profiles"] if row["material_id"] == "mesh:200")
    mesh["active_stroke_topology"]["content_sha256"] = "0" * 64
    with pytest.raises(COMPILER.CompileFailure, match="active_stroke_topology_invalid"):
        compile_doc(profiles)


def test_structural_final_writer_consumes_the_exact_compiled_row():
    topology = (ROOT / "apps/fl4512-runtime/engine/fl4512_u7_topology.h").read_text()
    presenter = PRESENTER_PATH.read_text()
    module = SEAM_MODULE_PATH.read_text()
    assert "static inline void Fl4512U7Betti" in topology
    assert "target_beta0" not in presenter and "candidate_beta0" not in presenter
    # Seam ownership: the candidate builder moved with the seam module.
    candidate_builder = module.split(
        "static bool Fl4512BuildStructuralCandidatesV1", 1
    )[1].split("static void Fl4512Term_BridgeReset(", 1)[0]
    assert "Fl4683ExactCellCandidatesV1" in candidate_builder
    assert "view.members" in candidate_builder
    assert "mat_bucket_len" not in candidate_builder
    assert "aspect_candidate_gid" not in candidate_builder
    assert "symmetry" not in candidate_builder


def test_mesh_bundle_rejects_authored_mesh_content():
    # Post cutover the profile mesh lane keeps ADDRESS METADATA ONLY. Re-authoring
    # candidates or ANY admission field is rejected — the compiler is the sole
    # injector of the mesh_feature.bundle.v1 row.
    for key, value in (
        ("candidate_admission_schema", "x"),
        ("candidate_admission_receipt_id", "y"),
        ("role_eligible_glyph_ids", [1, 2, 3]),
        ("candidates", [512, 513]),
    ):
        profiles, _ = committed_profiles()
        mesh = next(row for row in profiles["profiles"] if row["material_id"] == "mesh:200")
        mesh["role_buckets"]["mesh_bundle"][key] = value
        with pytest.raises(COMPILER.CompileFailure, match="mesh_row_authored_content"):
            compile_doc(profiles)


def test_mesh_row_is_admission_selected_row_with_v2_provenance():
    # The compiler injects the admission artifact's selected_row verbatim, with
    # strict v2 provenance (schema, receipt content hash, role-eligible set, raw
    # artifact sha256). There is no profile-authored candidate removal path.
    profiles, _ = committed_profiles()
    artifact = compile_doc(profiles)
    row = next(
        row for row in artifact["rows"]
        if row["material_key"] == 200 and row["role"] == "mesh_feature"
    )
    assert row["candidate_glyph_ids"] == MESH_STRUCTURAL_GIDS
    assert row["role_eligible_glyph_ids"] == MESH_STRUCTURAL_GIDS
    assert row["candidate_admission_schema"] == COMPILER.ADMISSION.ARTIFACT_SCHEMA
    assert row["candidate_admission_receipt_id"] == MESH_ADMISSION_RECEIPT
    assert row["mesh_admission_artifact_sha256"] == hashlib.sha256(
        (ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json").read_bytes()
    ).hexdigest()


def test_over_capacity_rejects_whole_transaction():
    profiles, _ = committed_profiles()
    candidates = capacity_sized_candidates(profiles)
    assert len(candidates) == 32
    candidates.append(512)
    with pytest.raises(COMPILER.CompileFailure, match="pool_capacity_exceeded"):
        compile_doc(profiles)


def test_duplicate_and_unadmitted_candidates_reject():
    profiles, _ = committed_profiles()
    candidates = capacity_sized_candidates(profiles)
    candidates[-1] = candidates[0]
    with pytest.raises(COMPILER.CompileFailure, match="duplicate_candidate"):
        compile_doc(profiles)

    profiles, _ = committed_profiles()
    capacity_sized_candidates(profiles)[-1] = 999999
    with pytest.raises(COMPILER.CompileFailure, match="candidate_unadmitted"):
        compile_doc(profiles)


def test_profile_namespace_qualifies_numeric_material_key():
    profiles, _ = committed_profiles()
    duplicate = copy.deepcopy(profiles["profiles"][0])
    duplicate["material_id"] = "mesh:1"
    profiles["profiles"].append(duplicate)
    artifact = compile_doc(profiles)
    owners = {
        (row["profile_namespace"], row["material_key"])
        for row in artifact["rows"]
    }
    assert ("terrain", 1) in owners
    assert ("mesh", 1) in owners

    profiles["profiles"].append(copy.deepcopy(duplicate))
    with pytest.raises(COMPILER.CompileFailure, match="duplicate_profile_owner"):
        compile_doc(profiles)


def test_invalid_live_material_colors_reject_before_artifact_write():
    profiles, _ = committed_profiles()
    broken = next(row for row in profiles["profiles"] if row["material_id"] == "terrain:3")
    broken["colors"]["shade_band_thresholds"] = []
    broken["colors"]["elevation_rows"]["high"] = {}
    with pytest.raises(COMPILER.CompileFailure, match="material_state_invalid"):
        compile_doc(profiles)


def test_cli_rejection_does_not_replace_existing_artifact(tmp_path: Path):
    profiles, _ = committed_profiles()
    capacity_sized_candidates(profiles).append(512)
    source = tmp_path / "invalid-profile.json"
    source.write_text(json.dumps(profiles))
    output = tmp_path / "compiled.json"
    output.write_text("sentinel\n")
    proc = subprocess.run(
        [
            sys.executable,
            str(COMPILER_PATH),
            "--profiles",
            str(source),
            "--catalog",
            str(CATALOG_PATH),
            "--atlas",
            str(ATLAS_PATH),
            "--out",
            str(output),
            "--compiler-commit",
            "test-commit",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 2
    assert "pool_capacity_exceeded" in proc.stdout
    assert output.read_text() == "sentinel\n"


def test_runtime_deleted_permissive_profile_loader_owner():
    source = RESOLVER_PATH.read_text()
    assert "Fl4260LoadV1ProfileTable" not in source
    assert "kFl4260V1ProfilesPath" not in source
    assert "Fl4512LoadCompiledProfileMetadataV1" in source
    assert "Fl4512CanonicalHashMatches(root, \"artifact_sha256\")" in source
    assert "Fl4512CanonicalHashMatches(row, \"row_sha256\")" in source
    assert "pool_capacity_exceeded" not in source  # compiler rejects before runtime
    assert "memcpy(g_fl4260_v1_profiles, prepared->profiles" in source
    assert "Fl4512RejectCompiledPool(rejection, failure_domain)" in source
    assert "Fl4260CommitProfileRevisionValue(prepared->revision)" in source


def test_compiler_atomically_replaces_artifact_at_declared_revision():
    compiler = COMPILER_PATH.read_text()
    assert 'parser.add_argument("--pool-revision", type=int, default=2)' in compiler
    assert '"pool_revision": pool_revision' in compiler
    assert "tempfile.mkstemp" in compiler
    assert "os.fsync" in compiler
    assert "os.replace(temporary_name, output)" in compiler


def test_load_failure_reaches_the_canonical_final_tuple_owner():
    resolver = RESOLVER_PATH.read_text()
    presenter = PRESENTER_PATH.read_text()
    frame = FRAME_PATH.read_text()
    absence_owner = (
        ROOT / "apps/fl4512-runtime/engine/fl4512_render_product_profiles.h"
    ).read_text()
    module = SEAM_MODULE_PATH.read_text()
    adapter = (
        ROOT / "apps/fl4512-runtime/platform/fl4693_native_final_cell_adapter.cpp"
    ).read_text()
    final_owner = (ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp").read_text()
    assert "g_fl4512_compiled_pool_declared_profile" in resolver
    # Seam ownership: bridge publish (profile_load_failed + valid) runs in
    # the product extent impl behind the bridge_upload_extent slot; the
    # shared presenter observes only the published extent.
    assert "g_fl4131_harri_gpu_bridge.profile_load_failed =" in module
    assert "Fl4512CompiledPoolLoadFailed() ? 1 : 0" in module
    assert "\\\"profile_load_failed\\\":%d" in module
    assert "extent_nrt->bridge_upload_extent" in presenter
    assert "FL4512_ABSENCE_REASON_POOL_LOAD_FAILED = 4" in absence_owner
    assert "static FinalGlyphResultV2 MissingAuthorityTuple" in final_owner
    assert "FL4693_FINAL_TUPLE_FAILURE_MISSING_PROFILE_AUTHORITY" in final_owner
    assert "Fl4693FinalTupleCommitFrameV1" in adapter
    assert "Fl4512MaterializeFinalGlyphResults" not in presenter


def test_editor_save_compiles_staged_profile_before_durable_commit():
    editor = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    save = editor.split(
        "// FL4588-MATERIAL-PROFILE-URDO-CUTOVER: the ONE persistence owner for a whole",
        1,
    )[1].split("// FL4588-MATERIAL-PROFILE-URDO-CUTOVER: URDO bridge hooks", 1)[0]
    assert "Fl4512FlattenProfileKey(keys[i])" in save
    assert "mkstemp(staged_profile_path)" in save
    assert "fsync(fileno(w))" in save
    assert "Fl4512CompileAndCommitProfileArtifact(" in save
    assert "fopen(kFl4260ProfilesV1Path, \"wb\")" not in save
    transaction = editor.split(
        "static bool Fl4512CompileAndCommitProfileArtifact(", 1
    )[1].split("static void Fl4260SetReason", 1)[0]
    assert "--profiles %s --catalog %s" in transaction
    assert "--atlas %s --page16 %s" in transaction
    assert "--out %s --pool-revision %llu" in transaction
    assert "link(profile_path, profile_backup)" in transaction
    assert "link(compiled_pool_path, artifact_backup)" in transaction
    assert "Fl4512DurableReplace(staged_profile_path, profile_path)" in transaction
    assert "Fl4512DurableReplace(staged_artifact_path, compiled_pool_path)" in transaction
    assert "kFl4260ProfilesV1Path" not in editor
    assert "kFl4512CompiledPoolV1Path" not in editor


def test_u9_row_color_counterfactual_uses_pure_live_serializer():
    editor = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    serializer = editor.split(
        "static bool Fl4260BuildProfileEditBytes(ProfileKeyV1 profile_key,", 1
    )[1].split("static bool Fl4512WriteObservedProfileBytes", 1)[0]
    assert "cJSON_Print(root)" in serializer
    assert "Fl4512CompileAndCommitProfileArtifact" not in serializer
    assert "Fl4512DurableReplace" not in serializer
    assert "g_fl4260_autosave_pending_profile_key =" not in serializer
    counterfactual = editor.split(
        'strcmp(cmd, "FL4512_DUMP_PROFILE_ROW_COLOR_COUNTERFACTUAL") == 0', 1
    )[1].split('strcmp(cmd, "FL4260_DUMP_PROFILE_EDIT_COLORS") == 0', 1)[0]
    assert "counterfactual = g_fl4260_profile_edit_state_by_profile[profile_index]" in counterfactual
    assert "Fl4512ApplyRowColorEditV2(" in counterfactual
    assert "Fl4260PruneRoleBucketsToEligiblePool(counterfactual)" in counterfactual
    assert "Fl4260BuildProfileEditBytes(profile_key, counterfactual" in counterfactual
    assert "Fl4260LoadProfileEdit" not in counterfactual
    assert '"live_state_mutated\\\":false' in counterfactual
    assert "autosave_pending_unchanged" not in counterfactual
    for retired in (
        "Fl4512ServiceQueuedProfileAutosave",
        "g_fl4512_autosave_state",
        "g_fl4260_autosave_pending_profile_key",
        "FL4512_DUMP_AUTOSAVE_STATE",
    ):
        assert retired not in editor


def test_asciiid_profile_selection_is_namespace_qualified():
    editor = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    assert "g_asciiid_active_material_selection" not in editor
    assert "static ProfileKeyV1 g_asciiid_active_profile_key" in editor
    assert 'strncmp(token, "terrain:", 8)' in editor
    assert 'strncmp(token, "mesh:", 5)' in editor
    selection = editor.split(
        "static void Fl4260SelectProfileFromEdit(ProfileKeyV1 profile_key)", 1
    )[1].split("static void Fl4260SelectTerrainMaterialFromEdit", 1)[0]
    assert "Fl4260EnsureProfileEditLoaded(profile_key)" in selection
    # Selecting mesh:N edits only that qualified profile. The terrain brush
    # remains terrain-owned; mesh selection must never collapse into its
    # numeric material key.
    assert "profile_key.profile_namespace == FL4512_PROFILE_NAMESPACE_TERRAIN" in selection
    assert "active_material = profile_key.material_key" in selection
    assert "FL4260_SAVE_PROFILE_EDIT <terrain:N|mesh:N>" not in editor


def test_compiled_pool_staging_is_profile_key_qualified():
    source = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
    editor = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    loader = source.split("static bool Fl4512LoadCompiledProfileMetadataV1", 1)[1].split(
        "bool Fl4512ProjectedFeatureExtentAdmissionForFeature", 1)[0]
    assert "Fl4260V1RampDensityProfile (*staged)[kFl4131RuntimeMaxMaterials]" in loader
    assert "staged[namespace_id][material_key]" in loader
    assert "namespace_id * kFl4131RuntimeMaxMaterials + material_key" not in loader
    assert 'strcmp(cmd, "FL4512_DUMP_PROFILE_ROW_COLOR_COUNTERFACTUAL") == 0' in editor
    assert 'strcmp(cmd, "FL4512_DUMP_AUTOSAVE_STATE") == 0' not in editor
    assert "fl4512.profile_row_color_counterfactual.v2" in editor
    assert "fl4512.autosave_transaction.v1" not in editor
    assert "Fl4260SaveProfileEdit(" not in editor
    assert "static bool Fl4260CommitProfileBatchWithHistory(" in editor
    for command in (
        "FL4260_DUMP_PROFILE_EDIT_COLORS",
        "FL4260_DUMP_BUCKET_POOLS",
    ):
        block = editor.split(f'strcmp(cmd, "{command}") == 0', 1)[1]
        block = block.split("else if (strcmp(cmd,", 1)[0]
        assert "Fl4512ParseProfileKeyToken" in block, command


def test_retired_winner_shader_cannot_reintroduce_a_load_failure_owner():
    presenter = PRESENTER_PATH.read_text()
    assert "harri_winner_fs" not in presenter
    assert "HarriCompileProgram" not in presenter
    assert "uniform int profile_load_failed" not in presenter


def test_mesh_structural_policy_without_address_lane_rejected():
    # A live structural profile carries both policy blocks (projected-feature-extent
    # admission + active-stroke snake v2). Without the EXPLICIT mesh_bundle address
    # lane it fails shut — the profile is the sole explicit role address.
    profiles, _ = committed_profiles()
    mesh = next(row for row in profiles["profiles"] if row["material_id"] == "mesh:200")
    del mesh["role_buckets"]["mesh_bundle"]
    with pytest.raises(
        COMPILER.CompileFailure, match="mesh_structural_policy_without_address"
    ):
        compile_doc(profiles)
