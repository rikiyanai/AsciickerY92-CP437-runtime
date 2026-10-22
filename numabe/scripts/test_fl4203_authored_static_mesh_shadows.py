from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
RESOLVE = ROOT / "godot_project/shaders/ascii_probe_resolve.glsl"


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_rejected_analytic_static_shadow_owners_are_deleted() -> None:
    combined = source(PRODUCT) + source(RESOLVE)
    for retired in (
        "static_tree_shadow_fact_at_world",
        "resolve_static_tree_cast_shadow_density",
        "product_lighthouse_column_shadow_density",
        "resolve_lighthouse_column_shadow_density",
        "PRODUCT_LIGHTHOUSE_XZ",
        "RESOLVE_LIGHTHOUSE_XZ",
    ):
        assert retired not in combined


def test_product_lane_uses_complete_authored_face_any_hit() -> None:
    product = source(PRODUCT)
    normal_start = product.index("vec3 product_static_face_geometric_normal(")
    normal_end = product.index("\n}\n\nShadowFact static_mesh_shadow_fact_at_world", normal_start)
    normal_body = product[normal_start:normal_end]
    start = product.index("ShadowFact static_mesh_shadow_fact_at_world(")
    end = product.index("\n}\n\nShadowFact product_shadow_fact_at_world", start)
    body = product[start:end]

    assert "cross(p1 - p0, p3 - p0)" in normal_body
    assert "if (product_is_perspective()) return fact;" in body
    assert "FL4270_STATIC_FACE_SCAN_MAX" in body
    assert "if (fi == receiver_face_idx) continue;" in body
    assert "receiver_world + light_side_n * 0.02 + sun * 0.001" in body
    assert "product_ray_triangle_t(shadow_ray, fr0.xyz, fr1.xyz, fr3.xyz)" in body
    assert "product_ray_triangle_t(shadow_ray, fr0.xyz, fr3.xyz, fr2.xyz)" in body
    assert "== 250u" in body
    assert "STATIC_WORLD_TREE_LEAF_MAT" not in body
    assert "STATIC_WORLD_ROCK_MAT" not in body
    assert "STATIC_WORLD_TREE_BARK_MAT" not in body


def test_perspective_resolve_uses_same_complete_authored_face_any_hit() -> None:
    resolve = source(RESOLVE)
    linear_start = resolve.index("float resolve_static_mesh_cast_shadow_density_linear(")
    linear_end = resolve.index("\n}\n\n// FL-4431 BVH shadow leaf visit", linear_start)
    linear_body = resolve[linear_start:linear_end]
    leaf_start = resolve.index("bool resolve_bvh_shadow_test_leaf(")
    leaf_end = resolve.index("\n}\n\n// FL-4431 BVH any-hit shadow traversal", leaf_start)
    leaf_body = resolve[leaf_start:leaf_end]
    dispatch_start = resolve.index("float resolve_static_mesh_cast_shadow_density(")
    dispatch_end = resolve.index("\n}\n\n// Combined cast-shadow attenuation", dispatch_start)
    dispatch_body = resolve[dispatch_start:dispatch_end]

    assert "RESOLVE_STATIC_SHADOW_MAX_FACE_CASTERS = 32768" in resolve
    assert "if (fi >= 2048) return true;" in resolve
    assert "if (fi == receiver_face_idx) continue;" in linear_body
    assert "receiver_world + light_side_n * 0.02 + sun * 0.001" in linear_body
    assert "resolve_static_shadow_triangle_t(origin, sun, fr0.xyz, fr1.xyz, fr3.xyz)" in linear_body
    assert "resolve_static_shadow_triangle_t(origin, sun, fr0.xyz, fr3.xyz, fr2.xyz)" in linear_body
    assert "== 250u" in linear_body

    assert "if (fi == receiver_face_idx) return false;" in leaf_body
    assert "resolve_face_shadow_admitted(fi)" in leaf_body
    assert "resolve_static_shadow_triangle_t(origin, sun, fr0.xyz, fr1.xyz, fr3.xyz)" in leaf_body
    assert "resolve_static_shadow_triangle_t(origin, sun, fr0.xyz, fr3.xyz, fr2.xyz)" in leaf_body
    assert "== 250u" in leaf_body

    assert "resolve_static_mesh_cast_shadow_density_bvh(" in dispatch_body
    assert "resolve_static_mesh_cast_shadow_density_linear(" in dispatch_body
    for retired_material_owner in (
        "STATIC_WORLD_TREE_LEAF_MAT",
        "STATIC_WORLD_ROCK_MAT",
        "STATIC_WORLD_TREE_BARK_MAT",
    ):
        assert retired_material_owner not in linear_body
        assert retired_material_owner not in leaf_body


def test_each_projection_has_one_static_shadow_owner() -> None:
    product = source(PRODUCT)
    resolve = source(RESOLVE)

    assert product.count("static_mesh_shadow_fact_at_world(") == 2
    assert resolve.count("resolve_static_mesh_cast_shadow_density(") == 2
    assert "product_shadow_fact_at_world(\n\t\t\t\tproduct_surface_receiver_world" in product
    assert "resolve_cast_shadow_attenuation(\n\t\t\t\tshadow_receiver" in resolve


def test_authored_shadow_removes_direct_light_without_crushing_ambient() -> None:
    product = source(PRODUCT)
    resolve = source(RESOLVE)

    assert "product_static_shadow_light_attenuation" in product
    assert "product_shadow_caster_is_static_mesh(shadow_caster_id)" in product
    assert "ambient_value / lit_value" in product
    assert "resolve_static_shadow_light_attenuation" in resolve
    assert "ambient_value / lit_value" in resolve
    assert "return min(blob_attenuation, static_attenuation);" in resolve
