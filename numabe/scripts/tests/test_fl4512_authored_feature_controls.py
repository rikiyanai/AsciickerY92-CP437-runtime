from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
PROFILE_SCHEMA_PATH = (
    ROOT / "assets/glyphs/schema/material_rendering_profiles.v1.schema.json"
)
REGISTRY_PATH = ROOT / "assets/asciiid/ui/asciiid.ui_registry.v1.json"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _compiler_module():
    path = ROOT / "scripts/compile_fl4512_pools.py"
    spec = importlib.util.spec_from_file_location("compile_fl4512_pools", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rehash(block: dict) -> None:
    unhashed = {key: value for key, value in block.items() if key != "content_sha256"}
    canonical = json.dumps(
        unhashed, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    block["content_sha256"] = hashlib.sha256(canonical).hexdigest()


def test_direct_visual_progression_owner_is_physically_absent() -> None:
    paths = [
        "engine/render/render_stages.h",
        "engine/render/render_scene.cpp",
        "engine/render/render_internal.h",
        "engine/render/render_resolve.cpp",
        "engine/render/render_frame_input.h",
        "platform/terminal_gl_present.cpp",
        "apps/asciicker-presenter/asciiid.cpp",
        "apps/asciicker-presenter/asciiid_application_service.cpp",
        "apps/asciicker-presenter/asciiid_application_service.h",
        "web/asciiid-authoring-shell/src/ControlWidget.tsx",
        "web/asciiid-authoring-shell/src/transport.ts",
    ]
    source = "\n".join(_read(path) for path in paths)
    retired = (
        "CommitMeshFeatureProgressionStage",
        "inspect.pipeline.mesh_feature_progression",
        "Fl4652MeshFeatureProgressionStageV1",
        "mesh_feature_progression_stage",
        "mesh_feature_admitted_kind_mask",
        "progression_presentation_packed",
        "admitted_kind_mask",
    )
    for symbol in retired:
        assert symbol not in source
    assert "0x01u : (stage == 2 ? 0x03u : 0x0bu)" not in source


def test_profile_schema_and_compiler_accept_authored_active_stroke_values() -> None:
    document = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    schema = json.loads(PROFILE_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator(schema).validate(document)
    mesh = next(row for row in document["profiles"] if row["material_id"] == "mesh:200")
    authored = copy.deepcopy(mesh)
    active = authored["active_stroke_topology"]
    active["relaxation_iterations"] = 7
    active["extension_max_steps"] = 3
    active["max_topology_passes"] = 7
    _rehash(active)
    compiler = _compiler_module()
    compiled = compiler.compile_active_stroke_topology(authored)
    assert compiled is not None
    assert compiled["relaxation_iterations"] == 7
    assert compiled["extension_max_steps"] == 3
    assert compiled["max_topology_passes"] == 7
    assert compiled["gaussian_table_q0_16"][:5] == [
        65535,
        63519,
        61564,
        59670,
        57834,
    ]
    assert compiled["topology_operation_order"] == [
        "delete",
        "split",
        "trim",
        "extend",
        "merge",
        "insert",
    ]
    assert "expected_values" not in _read("scripts/compile_fl4512_pools.py")


def test_profile_compiler_rejects_stale_hash_and_invalid_cross_field_extent() -> None:
    document = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    mesh = next(row for row in document["profiles"] if row["material_id"] == "mesh:200")
    compiler = _compiler_module()

    stale = copy.deepcopy(mesh)
    stale["active_stroke_topology"]["relaxation_iterations"] += 1
    with pytest.raises(compiler.CompileFailure):
        compiler.compile_active_stroke_topology(stale)

    invalid_extent = copy.deepcopy(mesh)
    row = invalid_extent["projected_feature_extent_admission"]["mesh_thresholds"][
        "silhouette"
    ]
    row["exit_q"] = row["enter_q"] + 1
    with pytest.raises(compiler.CompileFailure):
        compiler.compile_projected_feature_extent_admission(invalid_extent)


def test_html_controls_are_parameter_controls_on_the_normal_commit_path() -> None:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    leaves = [
        leaf for leaf in registry["leaves"]
        if leaf["leaf_id"].startswith("material.feature.")
    ]
    assert len(leaves) == 8
    assert all(leaf["control_kind"] == "number" for leaf in leaves)
    assert all(
        leaf["action_schema"] == "asciiid.set_material_feature_parameter.v1"
        for leaf in leaves
    )
    assert all(
        leaf["application_service_handler"] == "CommitMaterialFeatureParameter"
        for leaf in leaves
    )
    labels = {leaf["leaf_id"] for leaf in leaves}
    assert labels == {
        "material.feature.silhouette.extent_enter",
        "material.feature.silhouette.extent_exit",
        "material.feature.crease.extent_enter",
        "material.feature.crease.extent_exit",
        "material.feature.suggestive.extent_enter",
        "material.feature.suggestive.extent_exit",
        "material.feature.suggestive.derivative",
        "material.feature.suggestive.stability",
    }

    transport = _read("web/asciiid-authoring-shell/src/transport.ts")
    recorder = _read("web/asciiid-authoring-shell/src/uiEventRecorder.ts")
    widget = _read("web/asciiid-authoring-shell/src/ControlWidget.tsx")
    service = _read("apps/asciicker-presenter/asciiid_application_service.cpp")
    editor = _read("apps/asciicker-presenter/asciiid.cpp")
    assert 'action_kind: "set_material_feature_parameter"' in transport
    assert 'NATIVE_ACTION_RESULT_PREFIX = "ASCIIID_NATIVE_ACTION_RESULT "' in recorder
    assert "set_material_feature_parameter" in service
    assert "CommitAsciiidMaterialFeatureParameter" in editor
    assert "Fl4260CommitProfileBatchWithHistory" in editor
    assert "set_material_feature_parameter" in widget
    # The eight surviving controls author only the projected-feature admission
    # block. Active Stroke v2 is compiler-owned and never enters this write set.
    feature_commit = service.split(
        "AsciiidApplicationService::CommitMaterialFeatureParameter(", 1
    )[1].split("AsciiidApplicationResultV1\nAsciiidApplicationService::CommitMaterialColorParameter(", 1)[0]
    assert "/structural_features/" not in feature_commit
    assert "projected_feature_extent_admission" in feature_commit
    assert "active_stroke_topology" not in feature_commit
    for forbidden in ("glyph_id", "palette_id", "selected_role", "final_gid"):
        feature_routes = transport.split("const featureLeafRoutes", 1)[1].split(
            "const featureRoute", 1
        )[0]
        assert forbidden not in feature_routes
    direct_commit = transport.split("export async function commitDirectAction(", 1)[
        1
    ].split("export type MaterialColorActionResult", 1)[0]
    assert "postObservedNativeActionCommit(" in direct_commit
    assert "ASCIIID_BROWSER_ACTION_RESULT" not in direct_commit


def test_retired_active_stroke_v1_editor_owners_are_physically_absent() -> None:
    product_sources = "\n".join(
        _read(path)
        for path in (
            "apps/asciicker-presenter/asciiid.cpp",
            "apps/asciicker-presenter/asciiid_application_service.cpp",
            "web/asciiid-authoring-shell/src/transport.ts",
        )
    )
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry_text = json.dumps(registry, sort_keys=True)
    retired_parameters = (
        "endpoint_projected_tolerance_q16_16",
        "endpoint_object_tolerance_q24_8",
        "depth_link_max_height_q8_8",
        "degree2_tangent_dot_min_q1_15",
        "smoothing_iterations",
        "persistence_projected_max_q16_16",
        "persistence_object_max_q24_8",
        "arc_landmark_count",
        "retire_after_missing_frames",
    )
    for name in retired_parameters:
        assert name not in product_sources
        assert name not in registry_text
    assert not any(
        leaf["leaf_id"].startswith("material.feature.active_stroke.")
        for leaf in registry["leaves"]
    )


def test_extent_commit_serializer_cannot_rewrite_active_stroke_v2() -> None:
    editor = _read("apps/asciicker-presenter/asciiid.cpp")
    serializer = editor.split(
        "static bool Fl4260ApplyProfileStateToRoot(", 1
    )[1].split(
        "// FL4588-MATERIAL-PROFILE-URDO-CUTOVER: serialize a WHOLE batch", 1
    )[0]
    loader = editor.split(
        "static bool Fl4260LoadProfileEdit(ProfileKeyV1 profile_key)", 2
    )[2].split(
        "// FL-4260 RQ-149 DELETE-FIRST", 1
    )[0]
    commit = editor.split(
        "static AsciiidMaterialFeatureCommitV1 CommitAsciiidMaterialFeatureParameter(",
        1,
    )[1].split(
        "// FL-4588 /material/colors cutover", 1
    )[0]
    assert "projected_feature_extent_admission" in serializer
    assert "active_stroke_topology" not in serializer
    assert "projected_feature_extent_admission" in loader
    assert "active_stroke_topology" not in loader
    assert "active_stroke_topology" not in commit
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    mesh = next(row for row in profile["profiles"] if row["material_id"] == "mesh:200")
    assert mesh["active_stroke_topology"]["schema"] == "fl4512.active_stroke_topology.v2"


def test_runtime_consumes_compiled_ranges_without_a_second_value_tuple() -> None:
    resolver = _read("apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp")
    active = _read("apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp")
    resolve = _read("engine/render/render_resolve.cpp")
    assert "Fl4512CanonicalHashMatches(admission, \"content_sha256\")" in resolver
    # Active Stroke v2 deleted the v1 landmark/smoothing/retirement owner.
    # Runtime topology now consumes only the compiler-authored snake document.
    assert "admission->gaussian_table_count" in active
    assert "admission->relaxation_iterations" in active
    assert "admission->max_topology_passes" in active
    assert "admission->topology_operation_order" in active
    assert "admission.resample_min_q16_16" in active
    assert "admission.resample_max_q16_16" in active
    for retired in (
        "arc_landmark_count",
        "smoothing_iterations",
        "retire_after_missing_frames",
    ):
        assert retired not in active
    assert "if (!f.eligible)" in resolve
    assert "admitted_kind_mask" not in resolve


def test_u6_live_mesh_tonal_partition_and_single_tuple_writer_survive() -> None:
    owner = _read("apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp")
    finalizer = owner.split(
        "bool Fl4693ProfileTupleFinalizeV1", 1
    )[1].split("const FinalGlyphResultV2* Fl4693ProfileTupleReadV1", 1)[0]
    assert owner.count("bool Fl4693ProfileTupleFinalizeV1") == 1
    assert "current.pool_revision == 0u" not in finalizer
    assert "current.pool_revision > 0u" not in finalizer
    assert "else if (input->mesh_profile_cell)" in finalizer
    assert "tuple.selected_role == FL4512_ROLE_MESH_FEATURE" in finalizer
    assert "tuple.final_fg = input->ink;" in finalizer
    assert "tuple.final_fg = input->source->fg;" in finalizer
    assert "frame_revision" not in finalizer
    assert "frame_stage" not in finalizer


def test_terrain_and_nonmesh_remain_on_the_preexisting_generic_tuple_route() -> None:
    owner = _read("apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp")
    finalizer = owner.split(
        "bool Fl4693ProfileTupleFinalizeV1", 1
    )[1].split("const FinalGlyphResultV2* Fl4693ProfileTupleReadV1", 1)[0]
    assert "else if (input->mesh_profile_cell)" in finalizer
    assert "else\n\t{\n\t\ttuple.final_gid = tuple.glyph_id;" in finalizer
    assert "if (input->profile_color_valid)" in finalizer
    assert "tuple.final_fg = input->paper;" in finalizer
    assert "tuple.final_bg = input->ink;" in finalizer
    assert "tuple.final_gid = tuple.glyph_id;" in finalizer
