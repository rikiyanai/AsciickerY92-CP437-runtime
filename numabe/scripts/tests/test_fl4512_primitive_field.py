from scripts.fl4512_primitive_field import (
    MAX_SCORE_NUM, PrimitiveFieldError, boundary4, build_target,
    descriptor, detail_u16, score,
)
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "assets/glyphs/fixtures/fl4512_primitive_field_v1.json"
COMPILER_PATH = ROOT / "scripts/compile_fl4512_pools.py"
PROFILE_PATH = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
CATALOG_PATH = ROOT / "assets/glyphs/generated/material.additive.v1.shape_catalog.json"
ATLAS_PATH = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
PAGE16_PATH = ROOT / "assets/glyphs/atlases/material.additive.v1.page16_rgba8.json"
ADMISSION_PATH = ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json"
UNIVERSAL_PATH = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json"

SPEC = importlib.util.spec_from_file_location("compile_fl4512_pools", COMPILER_PATH)
assert SPEC and SPEC.loader
COMPILER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPILER)


def compile_doc(profiles: dict) -> dict:
    catalog_raw = CATALOG_PATH.read_bytes()
    atlas_raw = ATLAS_PATH.read_bytes()
    page16_raw = PAGE16_PATH.read_bytes()
    admission_raw = ADMISSION_PATH.read_bytes()
    universal_raw = UNIVERSAL_PATH.read_bytes()
    saved_validate = COMPILER.ADMISSION.validate_artifact_documents
    COMPILER.ADMISSION.validate_artifact_documents = lambda *args, **kwargs: None
    try:
        return COMPILER.compile_artifact(
            profiles,
            json.dumps(profiles, sort_keys=True).encode(),
            json.loads(catalog_raw), catalog_raw,
            json.loads(atlas_raw), atlas_raw,
            json.loads(page16_raw), page16_raw,
            "test-primitive-field",
            admission_artifact=json.loads(admission_raw),
            admission_artifact_raw=admission_raw,
            universal_catalog=json.loads(universal_raw),
            universal_catalog_raw=universal_raw,
        )
    finally:
        COMPILER.ADMISSION.validate_artifact_documents = saved_validate


def committed_profiles() -> dict:
    return json.loads(PROFILE_PATH.read_text())


def test_boundary_uses_outside_zero_and_four_neighbor_erosion():
    full = (1 << 64) - 1
    assert boundary4(full).bit_count() == 28
    center_block = sum(1 << (8 * y + x) for y in range(2, 6) for x in range(2, 6))
    assert boundary4(center_block).bit_count() == 12


def test_identical_descriptor_scores_zero_when_priority_matches_detail():
    mask = sum(1 << (8 * y + x) for y in range(1, 7) for x in range(1, 7))
    detail = detail_u16(mask)
    result = score(mask, mask, 7, 7, detail, detail)
    assert result["score_num"] == 0
    assert result["d_occ"] == result["d_sil"] == result["d_reg"] == result["d_pri"] == 0


def test_score_is_exact_bounded_and_region_mismatch_is_binary():
    a = 1
    b = 1 << 63
    result = score(a, b, 1, 2, 0, 65535)
    assert 0 < result["score_num"] <= MAX_SCORE_NUM
    assert result["d_reg"] == 65535


def test_descriptor_serializes_little_endian_mask_and_detail():
    record = descriptor(0x0102, 9)
    assert record["mask8_u64_le_hex"] == "0201000000000000"
    assert record["filled_region_class"] == 9
    assert 0 <= record["detail_u16"] <= 65535


def test_canonical_primitive_ref_fixture_builds_depth_owned_target():
    fixture = json.loads(FIXTURE.read_text())
    target = build_target(fixture["primitive_refs"])
    assert target == fixture["expected_target"]


def test_primitive_target_is_order_independent_and_lower_id_wins_depth_ties():
    refs = [
        {
            "primitive_ref_id": 30, "owner_surface_id": 3, "occupancy_mask8": 1,
            "region_class": 2, "priority_u16": 5, "depth_q16": 17, "visible": True,
        },
        {
            "primitive_ref_id": 20, "owner_surface_id": 2, "occupancy_mask8": 1,
            "region_class": 1, "priority_u16": 9, "depth_q16": 17, "visible": True,
        },
    ]
    target = build_target(list(reversed(refs)))
    assert target["state"] == "live"
    assert target["primitive_ref_ids"] == [20]
    assert target["filled_region_class"] == 1
    assert target["target_priority_u16"] == 9


def test_primitive_target_fails_closed_for_empty_and_region_conflict():
    assert build_target([])["state"] == "not_claimed"
    hidden = [{
        "primitive_ref_id": 1, "owner_surface_id": 1, "occupancy_mask8": 1,
        "region_class": 1, "priority_u16": 1, "depth_q16": 1, "visible": False,
    }]
    assert build_target(hidden)["state"] == "not_claimed"
    conflict = [
        {
            "primitive_ref_id": 1, "owner_surface_id": 1, "occupancy_mask8": 1,
            "region_class": 1, "priority_u16": 1, "depth_q16": 1, "visible": True,
        },
        {
            "primitive_ref_id": 2, "owner_surface_id": 2, "occupancy_mask8": 2,
            "region_class": 2, "priority_u16": 2, "depth_q16": 2, "visible": True,
        },
    ]
    result = build_target(conflict)
    assert result["state"] == "region_conflict"
    assert result["reason"] == "region_conflict"
    assert "target_priority_u16" not in result


def test_primitive_ref_schema_rejects_duplicate_identity_and_empty_coverage():
    ref = {
        "primitive_ref_id": 1, "owner_surface_id": 1, "occupancy_mask8": 1,
        "region_class": 1, "priority_u16": 1, "depth_q16": 1, "visible": True,
    }
    with pytest.raises(PrimitiveFieldError, match="primitive_ref_duplicate_id"):
        build_target([ref, dict(ref)])
    ref["occupancy_mask8"] = 0
    with pytest.raises(PrimitiveFieldError, match="primitive_ref_mask8_empty"):
        build_target([ref])


def test_compiler_emits_admitted_filled_row_with_primitive_descriptors():
    profiles = committed_profiles()
    mesh = next(item for item in profiles["profiles"] if item["material_id"] == "mesh:200")
    mesh["role_buckets"]["filled"] = {
        "role": "filled",
        "route_key": "filled.field.v1",
        "lane_id": 0,
        "lane_name": "filled_field",
        "region_class": 7,
        "priority_u16": 140,
        "candidates": [520, 521],
    }
    artifact = compile_doc(profiles)
    row = next(
        item for item in artifact["rows"]
        if item["profile_namespace"] == "mesh"
        and item["material_key"] == 200
        and item["role"] == "filled"
    )
    assert row["bucket_state"] == "live"
    assert row["scorer_id"] == "S_pf.v1"
    assert row["candidate_glyph_ids"] == [520, 521]
    assert all(item["filled_region_class"] == 7 for item in row["candidate_descriptors"])
    assert all("detail_u16" in item for item in row["candidate_descriptors"])


def test_compiler_rejects_unadmitted_filled_candidate():
    profiles = committed_profiles()
    mesh = next(item for item in profiles["profiles"] if item["material_id"] == "mesh:200")
    catalog_ids = {
        item["glyph_id"] for item in json.loads(CATALOG_PATH.read_text())["entries"]
        if isinstance(item, dict) and isinstance(item.get("glyph_id"), int)
    }
    atlas_ids = {
        item["glyph_id"] for item in json.loads(ATLAS_PATH.read_text())["bindings"]
        if isinstance(item, dict) and isinstance(item.get("glyph_id"), int)
    }
    unadmitted = next(gid for gid in range(256, 10000) if gid not in catalog_ids & atlas_ids)
    mesh["role_buckets"]["filled"] = {
        "role": "filled",
        "route_key": "filled.field.v1",
        "lane_id": 0,
        "lane_name": "filled_field",
        "region_class": 7,
        "priority_u16": 140,
        "candidates": [unadmitted],
    }
    with pytest.raises(COMPILER.CompileFailure, match="candidate_unadmitted"):
        compile_doc(profiles)


def test_product_runtime_does_not_synthesize_a_filled_primitive_from_cell_occupancy():
    frame = (ROOT / "engine/render/render_frame_input.h").read_text()
    render_resolve = (ROOT / "engine/render/render_resolve.cpp").read_text()
    resolver = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
    presenter = (ROOT / "platform/terminal_gl_present.cpp").read_text()
    assert "FL4512_ROLE_FILLED = 5" in frame
    assert "FL4512_ROLE_TONAL = 6" in frame
    assert "profile_bucket_count = 33" in frame
    assert "profile_candidate_global6_width = 6" in frame
    assert "18-float positional descriptor" in frame
    assert "kFl4512ProfileFilledBucket = 32" in resolver
    assert "Fl4512PickBestPrimitiveFieldV1" in resolver
    assert "filled_primitive_valid" in frame
    assert "Fl4512BuildOwnerClippedPrimitiveRefV1" not in render_resolve
    dispatch = render_resolve.split("static inline bool DispatchShape6ResolverHook", 1)[1]
    dispatch = dispatch.split("static inline uint8_t ResolveGlyphDispatchSurfaceFromSamples", 1)[0]
    assert "Generic same-material raster occupancy is not a structural primitive" in dispatch
    assert "in.filled_primitive_valid =" not in dispatch
    assert "fact_mask |= FL4512_CELL_FACT_FILLED" in resolver
    assert "filled_target_valid[filled_index] = 1" in resolver
    assert '"S_pf.v1"' in resolver
    assert "kFl4512RolePriorityV1Hash" not in presenter
    assert "kFl4512RolePriorityV2Hash" in presenter
    assert "PF(pf.rg,cm,pf.b,cr,pf.a,cd)" in presenter
    assert "role=5; primary=32" in presenter
    assert "premature live filled/Score_pf claim deleted" not in presenter
    assert "primitive_field" in presenter
    temporal = presenter.split("static void Fl4512TemporalSelectFrameV1", 1)[1]
    temporal = temporal.split("static void Fl4512TemporalCommitMissingFrameV1", 1)[0]
    assert "b.filled_target_mask8[idx]" in temporal
    assert "b.filled_target_region_class[idx]" in temporal
    assert "b.filled_target_priority_u16[idx]" in temporal


def test_mesh_profile_compiles_one_live_filled_row():
    doc = json.loads((ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json").read_text())
    mesh = next(m for m in doc["profiles"] if m["material_id"] == "mesh:200")
    filled = mesh["role_buckets"]["filled"]
    assert filled["route_key"] == "filled.field.v1"
    assert filled["candidates"] == [520, 521]
    compiled = compile_doc(doc)
    row = next(r for r in compiled["rows"] if r["profile_namespace"] == "mesh"
               and r["material_key"] == 200 and r["role"] == "filled")
    assert row["candidate_glyph_ids"] == [520, 521]
    assert row["candidate_count"] == 2
    material = next(m for m in compiled["materials"] if m["profile_namespace"] == "mesh"
                    and m["material_key"] == 200)
    assert material["filled_region_class"] == 7
    assert material["filled_priority_u16"] == 140
