if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSITOR = ROOT / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def test_final_compositor_has_no_post_product_edge_owner() -> None:
    src = COMPOSITOR.read_text(encoding="utf-8")

    forbidden = [
        "apply_screen_space_edge",
        "screen_edge_score",
        "sidewall_step_score",
        "fl4231_edge_fact_flags",
        "cell_owner_foliage_present_at",
        "cell_owner_foliage_soft_score_at",
        "splat_winner_at",
        "splat_depth_bucket",
        "native_depth_bucket_u",
        "sun_screen_neighbor_step",
        "fl4231_dirt_mountain_role",
        "project_world_to_pixel",
        "classify_terrain_material_family",
        "fl4102_grass_contour_punctuation",
        "fl4102_grass_punctuation_ink",
    ]
    for token in forbidden:
        assert token not in src, token


def test_product_remains_the_edge_and_foliage_fact_owner() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    # The retired pre-cutover edge/enforcement helpers are physically absent.
    # Product now owns one classification fact plus one typed edge fact, and
    # the protected compiler consumes both before final glyph publication.
    for retired in (
        "product_apply_mountain_edge_owner",
        "product_apply_fl4102_mountain_family_enforcement",
        "product_edge_score_to_neighbor",
        "product_sidewall_to_neighbor",
    ):
        assert retired not in product
    assert "struct ProductTerrainClassificationFacts" in product
    assert "product_terrain_classification_facts(" in product
    assert "q3_probe_payload_clone_cell_edge_fact(" in product
    assert "q3_probe_payload_apply_world_cell_edge(" in product
    assert "compute_product_foliage_fact" in product
    assert "foliage_product_present = true;" in product
    assert "final_product_status(foliage_present)" in product
    assert "context.entity, context.matcell, foliage_product_present," in product


def test_final_compositor_presents_product_foliage_glyph_when_present() -> None:
    src = COMPOSITOR.read_text(encoding="utf-8")
    # The retired r16/r17 compositor loader is gone. ABI-v2 Product now carries
    # the already-final glyph plus one status bit used only for the centre pin.
    assert "resolve_cell_owner_product(" not in src
    assert "final_product_status_has_foliage(product_status)" in src
    assert "int glyph = clamp(int(product.r0.z)" in src
    assert "bool foliage_center_pin = foliage_product_present" in src
    assert "owner.r16" not in src


def test_saved_foliage_patterns_are_additive_product_owned_static_cycles() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "int foliage_base_glyph_for_coverage(FoliageCoverage c)" in product
    assert "int foliage_saved_static_cycle_glyph(FoliageCoverage c)" in product
    assert "uint foliage_clump_cycle_ticks()" in product
    assert "uint foliage_static_cycle_phase_from_ticks(" in product
    assert "return uint(floor(params.feature_params.x * 5.0));" in product
    assert "uint seed_phase = clump_id % family_size;" in product
    assert "if (!c.present || c.role == FOLIAGE_ROLE_FLOWER)" in product
    assert "return cycle_glyph != GLYPH_ID_NONE ? cycle_glyph : base_glyph;" in product
    assert "foliage_static_cycle_phase(uint clump_id" not in product

    selector_start = product.index(
        "int foliage_glyph_for_coverage(FoliageCoverage c)"
    )
    selector_end = product.index("\n}\n", selector_start) + 3
    selector = product[selector_start:selector_end]
    assert selector.count("foliage_saved_static_cycle_glyph(c)") == 1
    for forbidden in ("VSB_PROJECTION_", "view_identity", "camera", "screen"):
        assert forbidden not in selector

    # Operator-saved foliage families from saved_families.jsonl must remain
    # explicit product candidates, not replace the older June-14 foliage grammar.
    for family in [
        "1842u, 1843u, 1844u",  # ไใโ
        "1845u, 1846u, 1847u",  # ᣗᣘᣖ
        "894u, 1734u, 1735u, 1736u, 1737u, 1738u",  # ثٺٽٿﭞﭢ
        "930u, 1833u, 1834u",  # ژڙڑ
        "1848u, 1849u, 936u",  # ؠىی
    ]:
        assert family in product

    for base_token in [
        "dense_flowers[8]",
        "FOLIAGE_ROLE_HALF_STEM",
        "FOLIAGE_ROLE_ROOT",
        "int denseray[4]",
    ]:
        assert base_token in product


def test_rain_uses_vertical_blades_and_original_o_shaped_ripples() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "uint cell_owner_rain_blade_glyph(uint phase)" in product
    assert "uint blades[4] = uint[4](0x7Cu, 0xB3u, 0xBAu, 0xB3u);" in product
    assert "float period = mix(3.2, 1.05, intensity);" in product
    assert "float fall = mix(5.2, 13.0, intensity);" in product

    ripple_body = product[
        product.index("float cell_owner_rain_ripple("):
        product.index("\n}\n\nuint cell_owner_rain_drop_glyph", product.index("float cell_owner_rain_ripple("))
    ]
    drop_body = product[
        product.index("uint cell_owner_rain_drop_glyph("):
        product.index("\n}\n\n\nbool cell_owner_rain_air_drop", product.index("uint cell_owner_rain_drop_glyph("))
    ]
    assert "cell_owner_rain_blade_glyph(blade_phase)" in drop_body
    assert "float rr = ring_r / SITE;" in ripple_body
    assert "ripple_glyph = 0x2Eu;" in ripple_body
    assert "ripple_glyph = 0x6Fu;" in ripple_body
    assert "ripple_glyph = 0x4Fu;" in ripple_body
    assert "ripple_glyph = 0xB0u;" in ripple_body
    assert "1872u" not in ripple_body
    assert "1873u" not in ripple_body
    assert "1874u" not in ripple_body
    assert "1875u" not in ripple_body
