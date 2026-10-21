#!/usr/bin/env python3
"""FL-4336 mountain identity and classification ownership contract."""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import tempfile
from pathlib import Path

from scripts import test_fl4057_probe_pipeline_contract as probe_contract


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_SHADER = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", source, re.S)
    assert match is not None, name
    brace = source.find("{", match.start())
    depth = 0
    for position in range(brace, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : position]
    raise AssertionError(f"unterminated function: {name}")


def test_mountain_terrain_resolve_uses_vsb_authored_cell_identity() -> None:
    source = _expand_includes(PRODUCT_SHADER.read_text(encoding="utf-8"))
    prepare = compact(function_body(source, "vsb_product_prepare_terrain"))

    # The retired screen-projected subcell helper is gone. VSB's primitive id
    # now derives the one authored terrain cell used by every Product phase.
    assert "terrain_subcell_stable_cell(" not in source
    assert (
        "uvec2unsigned_cell=uvec2("
        "local_primitive%dims.x,local_primitive/dims.x);"
        in prepare
    )
    assert "ivec2cell=ivec2(unsigned_cell);" in prepare
    assert (
        "vsb_product_selected_terrain_classification="
        "product_terrain_classification_facts("
        "cell,product_terrain_family(int(record.material)),"
        "input_value.selected_corner_heights);"
        in prepare
    )


def test_mountain_edge_owner_consumes_carried_precise_role() -> None:
    source = _expand_includes(PRODUCT_SHADER.read_text(encoding="utf-8"))
    edge = compact(
        function_body(source, "q3_probe_payload_apply_world_cell_edge")
    )
    morphology = compact(
        function_body(source, "product_compile_terrain_morphology")
    )
    center = compact(
        function_body(source, "product_compile_center_terrain_presentation")
    )

    # The deleted edge-role helper was replaced by one immutable classification
    # fact. The glyph-only edge phase consumes the precise boolean without
    # recomputation and cannot repaint the grouped terrain value.
    assert "q3_probe_payload_dirt_mountain_role(" not in source
    signature_start = source.index("bool q3_probe_payload_apply_world_cell_edge(")
    signature = compact(source[signature_start:source.index("{", signature_start)])
    assert "intfamily,booldirt_mountain,inoutuintglyph," not in signature
    assert "intfamily,booldirt_mountain,inoutuintglyph)" in signature
    assert "inoutvec3" not in edge
    assert "if(dirt_mountain){" in edge
    assert "terrain_cell_elevation_class(" not in edge
    assert (
        "vsb_product_selected_terrain_classification.edge_dirt_mountain,"
        "terrain.glyph);" in morphology
    )
    assert "q3_probe_payload_apply_world_cell_edge(" not in center
    assert "product_compile_terrain_morphology(" in center


def test_dirt_mountain_product_role_keeps_dirt_family_payload() -> None:
    source = _expand_includes(PRODUCT_SHADER.read_text(encoding="utf-8"))
    resolver = compact(
        function_body(source, "product_compile_terrain_product_morphology")
    )
    morphology = compact(
        function_body(source, "product_compile_terrain_morphology")
    )
    family = compact(function_body(source, "product_terrain_family"))

    # Product's deleted role helper is replaced by the ordinary-arithmetic
    # boolean carried into the resolver and final mountain glyph remap.
    assert "product_dirt_mountain_role(" not in source
    assert "product_material_family_for_visible_terrain(" not in source
    assert "q3_probe_payload_family(" not in source
    assert "intelv=elevation_center;" in resolver
    assert "terrain_cell_elevation_class(" not in resolver
    assert "if(dirt_mountain){" in resolver
    assert "r.family=TERRAIN_FAMILY_CLIFF_ROCK;" not in resolver
    assert "if(mat==2||mat==4)returnTERRAIN_FAMILY_DIRT;" in family
    assert "if(mat==5)returnTERRAIN_FAMILY_CLIFF_ROCK;" in family
    assert (
        "vsb_product_selected_terrain_classification."
        "product_dirt_mountain;" in morphology
    )


def assert_water_skip_cannot_enter_mountain_morphology(source: str) -> None:
    """Pin water exclusion at both terrain-classification boundaries."""
    family = compact(function_body(source, "product_terrain_family"))
    classification = compact(
        function_body(source, "product_terrain_classification_facts")
    )
    mountain_glyph = compact(
        function_body(source, "product_mountain_payload_glyph")
    )

    assert (
        "if(mat==int(VISIBLE_WATER_MAT))"
        "returnTERRAIN_FAMILY_WATER_SKIP;" in family
    )
    # Only DIRT may compute dirt-mountain classification. WATER_SKIP returns
    # the initialized false facts before the extra height reads.
    assert "if(family!=TERRAIN_FAMILY_DIRT)returnfacts;" in classification
    assert classification.index("if(family!=TERRAIN_FAMILY_DIRT)returnfacts;") < (
        classification.index("floath20=")
    )
    # Mountain glyph remapping admits only DIRT and CLIFF_ROCK. WATER_SKIP
    # keeps its incoming Product glyph unchanged.
    assert (
        "if(family!=TERRAIN_FAMILY_DIRT&&"
        "family!=TERRAIN_FAMILY_CLIFF_ROCK)returnglyph;" in mountain_glyph
    )


def test_water_skip_non_mountain_contract_rejects_regressions() -> None:
    source = _expand_includes(PRODUCT_SHADER.read_text(encoding="utf-8"))
    assert_water_skip_cannot_enter_mountain_morphology(source)

    mutants = {
        "water_enters_dirt_mountain_classification": source.replace(
            "\tif (family != TERRAIN_FAMILY_DIRT) return facts;",
            "\tif (family != TERRAIN_FAMILY_DIRT\n"
            "\t\t\t&& family != TERRAIN_FAMILY_WATER_SKIP) return facts;",
            1,
        ),
        "water_enters_mountain_glyph_remap": source.replace(
            "\tif (family != TERRAIN_FAMILY_DIRT "
            "&& family != TERRAIN_FAMILY_CLIFF_ROCK) return glyph;",
            "\tif (family != TERRAIN_FAMILY_DIRT\n"
            "\t\t\t&& family != TERRAIN_FAMILY_CLIFF_ROCK\n"
            "\t\t\t&& family != TERRAIN_FAMILY_WATER_SKIP) return glyph;",
            1,
        ),
    }
    assert all(mutant != source for mutant in mutants.values())
    compiler = probe_contract.glslang_bin()
    assert compiler is not None, "glslangValidator/glslang is required"
    with tempfile.TemporaryDirectory(prefix="fl4336-water-skip-mutants-") as tmp:
        for name, mutant in mutants.items():
            stages = probe_contract.split_stages(mutant)
            ok, detail = probe_contract.compile_stage(
                compiler, "compute", stages["compute"], Path(tmp)
            )
            assert ok, f"mutation is not compile-valid: {name}: {detail}"
            try:
                assert_water_skip_cannot_enter_mountain_morphology(mutant)
            except AssertionError:
                continue
            raise AssertionError(f"mutation stayed green: {name}")


def assert_dirt_mountain_role_precedence(source: str) -> None:
    resolver = compact(
        function_body(source, "product_compile_terrain_product_morphology")
    )
    role_law = (
        "p.role=PRODUCT_TERRAIN_ROLE_FLAT;"
        "if(p.ridge_continuity!=0u){"
        "p.role=PRODUCT_TERRAIN_ROLE_RIDGE;"
        "}elseif(step_amount>=0.55){"
        "p.role=PRODUCT_TERRAIN_ROLE_SLOPE_FACE;"
        "}elseif(boundary!=0u){"
        "p.role=PRODUCT_TERRAIN_ROLE_RAMP_BAND;"
        "}"
    )
    assert resolver.count("if(family==TERRAIN_FAMILY_DIRT){") == 1
    assert resolver.count("if(dirt_mountain){" + role_law) == 1
    assert resolver.index("if(dirt_mountain){" + role_law) < resolver.index(
        "p.glyph=product_terrain_glyph_for_product(",
        resolver.index("if(dirt_mountain){"),
    )


def test_dirt_mountain_role_precedence_rejects_regressions() -> None:
    source = _expand_includes(PRODUCT_SHADER.read_text(encoding="utf-8"))
    assert_dirt_mountain_role_precedence(source)

    # Every mutation remains valid GLSL. Each one changes a distinct visual
    # responsibility: the formation top, crest precedence, measured face
    # threshold, non-steep terrace admission, or the exclusive branch chain.
    mutants = {
        "flat_top_deleted": source.replace(
            "\t\t\tp.role = PRODUCT_TERRAIN_ROLE_FLAT;\n"
            "\t\t\tif (p.ridge_continuity != 0u) {",
            "\t\t\tp.role = PRODUCT_TERRAIN_ROLE_SLOPE_FACE;\n"
            "\t\t\tif (p.ridge_continuity != 0u) {",
            1,
        ),
        "ridge_loses_precedence": source.replace(
            "if (p.ridge_continuity != 0u) {\n"
            "\t\t\t\tp.role = PRODUCT_TERRAIN_ROLE_RIDGE;",
            "if (boundary != 0u) {\n"
            "\t\t\t\tp.role = PRODUCT_TERRAIN_ROLE_RIDGE;",
            1,
        ),
        "measured_face_threshold_drift": source.replace(
            "else if (step_amount >= 0.55) {",
            "else if (step_amount >= 1.15) {",
            1,
        ),
        "terrace_lip_admission_deleted": source.replace(
            "else if (boundary != 0u) {\n"
            "\t\t\t\tp.role = PRODUCT_TERRAIN_ROLE_RAMP_BAND;",
            "else if (false) {\n"
            "\t\t\t\tp.role = PRODUCT_TERRAIN_ROLE_RAMP_BAND;",
            1,
        ),
        "terrace_can_override_slope": source.replace(
            "} else if (boundary != 0u) {\n"
            "\t\t\t\tp.role = PRODUCT_TERRAIN_ROLE_RAMP_BAND;",
            "} if (boundary != 0u) {\n"
            "\t\t\t\tp.role = PRODUCT_TERRAIN_ROLE_RAMP_BAND;",
            1,
        ),
    }
    assert all(mutant != source for mutant in mutants.values())
    compiler = probe_contract.glslang_bin()
    assert compiler is not None, "glslangValidator/glslang is required"
    with tempfile.TemporaryDirectory(prefix="fl4231-mountain-role-mutants-") as tmp:
        for name, mutant in mutants.items():
            stages = probe_contract.split_stages(mutant)
            ok, detail = probe_contract.compile_stage(
                compiler, "compute", stages["compute"], Path(tmp)
            )
            assert ok, f"mutation is not compile-valid: {name}: {detail}"
            try:
                assert_dirt_mountain_role_precedence(mutant)
            except AssertionError:
                continue
            raise AssertionError(f"mutation stayed green: {name}")
