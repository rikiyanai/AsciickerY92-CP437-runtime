"""F13 terrain family-edge contract; direct viewport review remains required."""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        depth += source[i] == "{"
        depth -= source[i] == "}"
        if depth == 0:
            return source[start : i + 1]
    raise AssertionError(signature)


def without_comments(source: str) -> str:
    """Drop GLSL block and line comments so prose can never read as code.

    Ownership-count assertions below ask "how many times does the shader CALL
    this function". Comment text is not a call. Without this pass, `compact()`
    would delete the whitespace in a documentation line such as
    `// ... product_apply_terrain_plane_palette (ink 0.62 / ...` and glue the
    name to the following parenthesis, producing a phantom
    `product_apply_terrain_plane_palette(` token that inflates the count. That
    is exactly how this suite flipped red at 6361aa6a9, which only added
    explanatory comments and moved no call. Counts must track code, never prose.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def compact(value: str) -> str:
    return re.sub(r"\s+", "", without_comments(value))


def replace_in_function(source: str, signature: str, old: str, new: str) -> str:
    function = body(source, signature)
    assert function.count(old) == 1, (signature, old, function.count(old))
    return source.replace(function, function.replace(old, new, 1), 1)


def assert_grouped_palette_does_not_move_terrain_edge_ownership(source: str) -> None:
    # Every read below runs on the comment-stripped shader: prose (and any
    # braces inside it) must not reach either the brace matcher or the counters.
    executable = without_comments(source)
    edge = compact(body(executable, "bool q3_probe_payload_apply_world_cell_edge("))
    palette = compact(body(executable, "void product_apply_terrain_plane_palette("))
    material = compact(body(executable, "bool q3_probe_payload_apply_material("))

    assert "q3_probe_payload_outline_ink" not in executable
    assert "q3_probe_payload_apply_terrain_edge_paint" not in executable
    assert "inoutuintglyph)" in edge
    assert "inoutvec3" not in edge
    assert "fg=" not in edge
    assert "bg=" not in edge

    # The late edge stage remains glyph-only. The sole-baseline terrain palette
    # owns grouped role/ramp value and explicit cloud response before that edge.
    assert "product_terrain_hue_preserve_luma(" not in executable
    assert "terrain.fg=clamp(product_terrain_hue_at_group_luma(" in palette
    assert "terrain.bg=clamp(product_terrain_hue_at_group_luma(" in palette
    assert palette.count("product_terrain_plane_group_luma(") == 2
    assert "product_day_cloud_light_attenuation(cloud_shadow,family)" in palette
    # The semantic palette does not own the black endpoint. Its final visible
    # placement is a runtime presentation concern and is not source-asserted.
    assert "terrain_apply_nonblack_floor" not in palette
    assert material.count("product_apply_terrain_plane_palette(") == 1
    assert compact(executable).count("product_apply_terrain_plane_palette(") == 3


def test_glsl_comments_never_count_as_calls() -> None:
    """Regression witness for the 6361aa6a9 false red.

    A documentation comment that merely NAMES an owner -- with or without a
    space before the parenthesis, on one line or inside a block comment -- must
    leave every call count untouched. Whitespace-only compaction used to glue
    `name (` into `name(`, so prose incremented the counter.
    """
    # 1. Unit level: the comment forms that broke the suite contribute nothing.
    assert compact("// product_apply_terrain_plane_palette (ink 0.62 / 0.50)") == ""
    assert compact("/* calls product_apply_terrain_plane_palette(a); */") == ""
    assert compact("f(x); // see product_apply_terrain_plane_palette (ink)") == "f(x);"

    # 2. Contract level: injecting the real 6361aa6a9-shaped comment into the
    #    calling function keeps the whole ownership contract green.
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    material_signature = "bool q3_probe_payload_apply_material("
    anchor = body(source, material_signature)
    assert anchor.rstrip().endswith("}")
    phantom = (
        "\t// Grouped luma is finished by "
        "product_apply_terrain_plane_palette (ink 0.62 / 0.50 / 0.38);\n"
        "\t// the glyph-only edge stage is q3_probe_payload_apply_world_cell_edge ().\n"
    )
    injected = source.replace(anchor, anchor[:-1] + phantom + "}", 1)
    assert injected != source
    assert_grouped_palette_does_not_move_terrain_edge_ownership(injected)

    # 3. And the comment-blind helper still counts the real calls it should.
    assert compact(without_comments(injected)).count(
        "product_apply_terrain_plane_palette("
    ) == 3


def test_absolute_outline_and_edge_colour_owners_are_deleted() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert_grouped_palette_does_not_move_terrain_edge_ownership(source)


def test_family_edge_admission_and_continuous_value_are_preserved() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    edge = compact(body(source, "bool q3_probe_payload_apply_world_cell_edge("))
    assert "family!=TERRAIN_FAMILY_DIRT&&family!=TERRAIN_FAMILY_CLIFF_ROCK" in edge
    for token in (
        "Q3_EDGE_SCORE_EPS=1.15",
        "score<=Q3_EDGE_SCORE_EPS&&sidewall<=0.0",
        "max(sx,sidewall_x*1.22)",
        "max(sy,sidewall_y*1.22)",
    ):
        assert token in edge
    for glyph in ("0x2Fu", "0x3Bu", "0x27u", "0x2Eu", "0x2Cu", "0x3Au", "0x3Du", "0x23u"):
        assert glyph in edge
    assert_grouped_palette_does_not_move_terrain_edge_ownership(source)


@pytest.mark.parametrize(
    ("signature", "old", "new"),
    (
        (
            "bool q3_probe_payload_apply_world_cell_edge(",
            "inout uint glyph\n)",
            "inout uint glyph,\n\tinout vec3 fg\n)",
        ),
        (
            "void product_apply_terrain_plane_palette(",
            "terrain.fg = clamp(",
            "terrain.fg = vec3(0.05) + clamp(",
        ),
    ),
)
def test_continuous_value_ownership_mutations_turn_red(
    signature: str,
    old: str,
    new: str,
) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = replace_in_function(source, signature, old, new)
    with pytest.raises((AssertionError, ValueError)):
        assert_grouped_palette_does_not_move_terrain_edge_ownership(mutant)


def test_height_classified_water_neighbors_cannot_receive_terrain_lips() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    fact = compact(body(source, "Q3ProbePayloadCellEdgeFact q3_probe_payload_clone_cell_edge_fact("))
    average_gate = "neighbor_height<=WATER_SURFACE_Y"
    exact_gate = "cell_is_water_height(nbr_cell)"
    admission = (
        "center_material==int(VISIBLE_WATER_MAT)||"
        "neighbor_material==int(VISIBLE_WATER_MAT)||height_water_neighbor"
    )
    assert fact.count(average_gate) == 1
    assert fact.count(exact_gate) == 1
    assert (
        "boolheight_water_neighbor=" + average_gate + "&&" + exact_gate + ";"
        in fact
    )
    assert admission in fact
    assert fact.index(average_gate) < fact.index(exact_gate) < fact.index(admission)


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            "neighbor_height <= WATER_SURFACE_Y\n\t\t&& cell_is_water_height(nbr_cell)",
            "cell_is_water_height(nbr_cell)",
        ),
        (
            "neighbor_height <= WATER_SURFACE_Y\n\t\t&& cell_is_water_height(nbr_cell)",
            "neighbor_height <= WATER_SURFACE_Y",
        ),
        (
            "|| height_water_neighbor",
            "|| false",
        ),
    ),
)
def test_height_water_exclusion_mutations_turn_red(old: str, new: str) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = replace_in_function(
        source,
        "Q3ProbePayloadCellEdgeFact q3_probe_payload_clone_cell_edge_fact(",
        old,
        new,
    )
    with pytest.raises((AssertionError, ValueError)):
        test_source = compact(
            body(
                mutant,
                "Q3ProbePayloadCellEdgeFact q3_probe_payload_clone_cell_edge_fact(",
            )
        )
        assert "neighbor_height<=WATER_SURFACE_Y" in test_source
        assert "cell_is_water_height(nbr_cell)" in test_source
        assert (
            "center_material==int(VISIBLE_WATER_MAT)||"
            "neighbor_material==int(VISIBLE_WATER_MAT)||height_water_neighbor"
            in test_source
        )


def test_edge_change_does_not_enter_other_family_painters() -> None:
    # Comment-stripped: a doc line naming the helper is prose, not a call site.
    executable = compact(_expand_includes(PRODUCT.read_text(encoding="utf-8")))
    deleted_colour_helper = "q3_probe_payload_apply_terrain_edge_paint("
    glyph_helper = "q3_probe_payload_apply_world_cell_edge("
    assert deleted_colour_helper not in executable
    # One definition has one discrete-morphology consumer. Quarter paint and
    # exact-center presentation both call that shared owner; neither may apply
    # an edge glyph independently.
    assert executable.count(glyph_helper) == 2
    assert body(
        executable,
        "TerrainMorphologyproduct_compile_terrain_morphology(",
    ).count(glyph_helper) == 1
    for signature in (
        "ProbePayloadCompileResultcompile_probe_subcell_payload(",
        "TerrainCellPresentationproduct_compile_center_terrain_presentation(",
        "voidq3_probe_payload_apply_water(",
        "boolq3_probe_payload_apply_foliage(",
        "boolq3_probe_payload_apply_material(",
        "voidproduct_fl4216_apply_reflection_pair(",
    ):
        assert glyph_helper not in body(executable, signature)
