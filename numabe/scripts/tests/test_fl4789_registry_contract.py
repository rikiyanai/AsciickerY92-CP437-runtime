from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VSB = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
WORLD = ROOT / "godot_project/scripts/World.gd"


def test_static_tlas_is_active_subset_of_sealed_registry() -> None:
    vsb = VSB.read_text()
    product = _expand_includes(PRODUCT.read_text())

    vsb_tlas = vsb.split("bool scene_query_static_tlas", 1)[1].split(
        "// Validate one b80 mover record", 1
    )[0]
    assert "leaf_count > push_data.scene_counts.x" in vsb_tlas
    assert "leaf_count != push_data.scene_counts.x" not in vsb_tlas
    assert "An empty TLAS is valid while immutable registry rows remain" in vsb_tlas

    product_tlas = product.split(
        "VsbRetainedStaticCasterSelection vsb_retained_select_static_shadow_caster", 1
    )[1].split("VsbRetainedStaticCasterSelection vsb_retained_select_point_light_caster", 1)[0]
    assert "leaf_count > vsb_semantic_geometry_counts.static_counts_0.x" in product_tlas
    assert "leaf_count != vsb_semantic_geometry_counts.static_counts_0.x" not in product_tlas


def test_static_face_identity_is_not_compact_residency_order() -> None:
    product = _expand_includes(PRODUCT.read_text())
    materialize = product.split("bool vsb_retained_static_face_by_indices", 1)[1].split(
        "bool vsb_retained_static_face_local_facts", 1
    )[0]
    admission = product.split(
        "bool vsb_retained_static_legacy_candidate_admitted", 1
    )[1].split("bool vsb_retained_consider_static_caster", 1)[0]
    prepare = product.split("bool vsb_product_prepare_static", 1)[1].split(
        "bool vsb_product_prepare_dynamic", 1
    )[0]

    assert "facts.legacy_face_index = instance.refs.z + primitive_id" in materialize
    assert "legacy_face_index >= retained_face_total" not in admission
    assert "legacy_face >= retained_static_face_total" not in prepare
    assert "retained_static_face_total == 0u" in prepare


def test_world_production_path_has_no_runtime_tree_compiler() -> None:
    world = WORLD.read_text()
    startup = world.split("func _publish_partitioned_static_world_mesh", 1)[1].split(
        "# RQ-141:", 1
    )[0]
    crossing = world.split("func _process_world_partition_rebuild", 1)[1].split(
        "func _publish_partitioned_static_world_mesh", 1
    )[0]

    # This gate intentionally starts red until World consumes compiler payloads.
    assert "_append_fl4708_retained_tree_instances" not in startup
    assert "_preload_fl4789_authored_tree_assets" not in startup
    assert "_append_fl4708_retained_tree(" not in startup
    assert "SceneGeometryDBScript.new" not in crossing
    assert "face_rows" not in crossing
