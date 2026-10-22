"""Focused FL-4708 leaf Product grammar contract.

This verifies the source-level Product seam only.  It does not substitute for
the required headed near/mid/far Y8-map review.
"""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def _body(source: str, name: str) -> str:
    start = source.index(f"void {name}(")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:index]
    raise AssertionError(f"unterminated {name}")


def test_leaf_stroke_roles_are_stable_and_assembled_crown_driven() -> None:
    body = _body(_expand_includes(PRODUCT.read_text(encoding="utf-8")), "product_retained_tree_paint_pair")

    # Fixed retained facts create several stable leaf values.  There is no
    # clock/random/camera input in the selection key.
    assert "uint glyph_slot = world_key % 6u;" in body
    assert "uint outer_sweep[6]" in body
    assert "uint outer_angle[6]" in body
    assert "uint dense_stroke[6]" in body
    code = re.sub(r"//[^\n]*", "", body)
    for forbidden in ("TIME", "rand", "camera", "screen", "feature_params.x"):
        assert forbidden not in code

    # Material 241 is the compiler-published assembled-crown contour role;
    # material 242 remains its interior.  Product changes morphology without
    # changing the static-mesh owner.
    assert "bool assembled_crown_contour = material == STATIC_WORLD_TREE_CANOPY_MAT_FIRST;" in body
    assert "else if (assembled_crown_contour)" in body
    assert "glyph = (world_key & 1u) == 0u" in body
    assert "glyph = dense_stroke[glyph_slot];" in body


def test_leaf_values_monotonically_follow_existing_quantised_lighting() -> None:
    body = _body(_expand_includes(PRODUCT.read_text(encoding="utf-8")), "product_retained_tree_paint_pair")

    # `shade_band` is supplied from the existing normal/sun/shadow attenuation
    # path before this owner.  Its 0..15 mapping therefore darkens foreground
    # values without inventing a new light fact.
    assert "float tree_band = float(clamp(shade_band, 0, 15)) / 15.0;" in body
    assert "mix(0.32, 0.54, tree_band)" in body
    assert "mix(0.46, 0.70, tree_band)" in body
    assert "mix(0.72, 1.20, tree_band)" in body


def test_vegetation_has_one_exposure_writer_before_capture() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    body = _body(source, "product_vegetation_exposure_pair")
    assert "point_irradiance" in body
    assert "effective_sun_dir()" in body
    assert "floor(clamp(exposure" not in body
    assert "floor(local_luma * 4.0 + 0.5)" in body
    assert "product_ground_sun_transmission(world + n * 0.35" in body
    assert "product_apply_scene_land_daynight_pair(night, celestial, celestial_paper)" in body
    # The shared curve clamps its output. Apply the >1 sun-facing role gain
    # afterward so leaving its noon no-op does not clip that gain to 1.
    assert "vec3 celestial = vec3(1.0);" in body
    assert body.index("product_apply_scene_land_daynight_pair(") < body.index(
        "celestial *= (0.64 + 0.48 * facing * sun_visibility) * role_exposure;")
    for forbidden in ("depth", "air.", "exp("):
        assert forbidden not in re.sub(r"//[^\n]*", "", body)
    for forbidden in ("texelFetch", "SceneQuery", "glyph =", "sky", "gl_FragCoord"):
        assert forbidden not in re.sub(r"//[^\n]*", "", body)
    assert "night > 0.0001 && !bonfire_emissive && !vegetation_receiver" in source
    assert "record.r0.y != OWNER_SKY && !bonfire_emissive && !vegetation_receiver" in source
    assert "facts.point_irradiance = accumulated;" in source


def test_grass_uses_habitat_without_per_blade_flower_promotion() -> None:
    source = _expand_includes(PRODUCT.read_text())
    assert "FOLIAGE_FLOWER_FREQUENCY" not in source
    assert "slot_role == FOLIAGE_ROLE_FLOWER ||" not in source
    assert "vec3 petal = flower_cols[int(b.instance_id & 7u)]" not in source
    assert "float bloom_budget = (cluster > 0.72 && habitat.b > 0.32)" in source
    assert "density *= habitat.a * object_edge;" in source
    assert "b.height_cells = (7.0 + height_band * 3.0)" in source


def test_vegetation_fog_arithmetic_preserves_signed_contrast() -> None:
    # Arithmetic oracle only, not a GPU execution or visual acceptance claim.
    body = _body(_expand_includes(PRODUCT.read_text(encoding="utf-8")), "product_vegetation_fog_pair")
    assert "0.20 + 0.80 * exp(-max(depth, 0.0) * extinction)" in body
    assert "ink = ink * transmission + scatter * (1.0 - transmission);" in body
    assert "paper = paper * transmission + scatter * (1.0 - transmission);" in body
    for ink, paper in ((0.7, 0.2), (0.1, 0.6), (0.3, 0.3)):
        previous = abs(ink - paper)
        for depth in (0, 10, 60, 200, 512, 10000):
            transmission = 0.20 + 0.80 * math.exp(-depth * 0.0052)
            scatter = 0.02
            delta = (ink * transmission + scatter * (1 - transmission)) - (
                paper * transmission + scatter * (1 - transmission))
            assert math.isclose(delta, (ink - paper) * transmission, abs_tol=1e-12)
            assert abs(delta) <= previous + 1e-12
            assert abs(delta) >= abs(ink - paper) * 0.20 - 1e-12
            previous = abs(delta)
