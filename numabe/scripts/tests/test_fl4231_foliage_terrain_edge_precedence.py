#!/usr/bin/env python3
"""FL-4231 foliage-versus-terrain-finalizer precedence contract.

Foliage selection happens before terrain finalization.  Once a blade or flower
wins, the two later glyph/family writers must stand down; common terrain dither
and final contrast remain active.  This is structural evidence only.  It does
not establish world-fixed identity across Q/E turns or headed visual acceptance.
"""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _without_comments(source))


def _function(source: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        source,
        flags=re.S,
    )
    assert match is not None, f"missing function: {name}"
    opening = source.find("{", match.start())
    depth = 0
    for cursor in range(opening, len(source)):
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : cursor + 1]
    raise AssertionError(f"unterminated function: {name}")


def _block_after(source: str, marker: str) -> str:
    assert source.count(marker) == 1, (marker, source.count(marker))
    opening = source.index("{", source.index(marker))
    depth = 0
    for cursor in range(opening, len(source)):
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : cursor]
    raise AssertionError(f"unterminated block after {marker!r}")


def _replace_in_function(
    source: str,
    name: str,
    old: str,
    new: str,
) -> str:
    function = _function(source, name)
    assert function.count(old) == 1, (name, old, function.count(old))
    mutated = function.replace(old, new, 1)
    assert source.count(function) == 1, name
    return source.replace(function, mutated, 1)


def _assert_contract(source: str) -> None:
    compiler = _compact(_function(source, "compile_probe_subcell_payload"))
    foliage = _compact(_function(source, "q3_probe_payload_apply_foliage"))
    material = _compact(_function(source, "q3_probe_payload_apply_material"))
    morphology = _compact(
        _function(source, "product_compile_terrain_morphology")
    )

    # Base terrain morphology finishes before foliage. A valid foliage product
    # then replaces that base glyph, and no edge or mountain glyph writer runs
    # later in the quarter compiler.
    assert compiler.count("boolfoliage_product_present=false;") == 1
    material_call = "q3_probe_payload_apply_material("
    foliage_call = "q3_probe_payload_apply_foliage("
    terrain_marker = "if(context.surface_kind==OWNER_TERRAIN){"
    assert compiler.count(material_call) == 1
    assert compiler.count(foliage_call) == 1
    assert compiler.count(terrain_marker) == 1
    assert compiler.index(material_call) < compiler.index(foliage_call)
    assert compiler.index(foliage_call) < compiler.index(terrain_marker)
    assert (
        "glyph,fg,bg,markers,foliage_product_present,"
        "foliage_product_role)" in compiler
    )
    assert foliage.count("foliage_product_present=true;") == 1
    assert foliage.index("if(!present)returntrue;") < foliage.index(
        "foliage_product_present=true;"
    )
    assert foliage.index("foliage_product_present=true;") < foliage.index(
        "glyph=foliage_glyph;"
    )

    terrain = _block_after(compiler, terrain_marker)
    dither = "q3_probe_payload_apply_surface_stable_dither("
    world_edge = "q3_probe_payload_apply_world_cell_edge("
    mountain = "product_mountain_payload_glyph("

    assert material.count("product_compile_terrain_morphology(") == 1
    assert morphology.count(world_edge) == 1
    assert morphology.count(mountain) == 1
    assert terrain.count(dither) == 1
    assert world_edge not in compiler
    assert mountain not in compiler
    assert "!foliage_product_present" in terrain
    assert "&&terrain_mountain_glyph_remapped" in terrain

    # The shared morphology owner consumes the carried dirt-mountain facts.
    assert (
        "vsb_product_selected_terrain_classification.edge_dirt_mountain,"
        "terrain.glyph);" in morphology
    )
    assert "product_dirt_mountain" in morphology
    assert "OWNER_WATER" not in terrain
    assert "OWNER_STATIC_MESH" not in terrain
    assert "OWNER_DYNAMIC_MESH" not in terrain

    # Both shared helpers remain live for non-foliage terrain; G4.3 removes
    # only their authority to overwrite an already-selected foliage product.
    edge = _compact(_function(source, "q3_probe_payload_apply_world_cell_edge"))
    assert "inoutuintglyph)" in edge
    assert "inoutvec3" not in edge
    assert "q3_probe_payload_apply_terrain_edge_paint(" not in source
    assert len(_function(source, "product_mountain_payload_glyph")) > 0


def test_foliage_winner_survives_late_terrain_glyph_writers() -> None:
    _assert_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("function", "old", "new"),
    (
        (
            "compile_probe_subcell_payload",
            "bool ortho_mountain_glyph_remapped =\n\t\t\t!foliage_product_present",
            "bool ortho_mountain_glyph_remapped =\n\t\t\tfoliage_product_present",
        ),
        (
            "compile_probe_subcell_payload",
            "&& terrain_mountain_glyph_remapped",
            "|| terrain_mountain_glyph_remapped",
        ),
        (
            "compile_probe_subcell_payload",
            "if (context.surface_kind == OWNER_TERRAIN) {",
            "if (context.surface_kind == OWNER_WATER) {",
        ),
        (
            "q3_probe_payload_apply_foliage",
            "foliage_product_present = true;",
            "foliage_product_present = false;",
        ),
        (
            "product_compile_terrain_morphology",
            "q3_probe_payload_apply_world_cell_edge(",
            "q3_probe_payload_apply_surface_stable_dither(",
        ),
    ),
)
def test_damaging_precedence_mutations_turn_red(
    function: str,
    old: str,
    new: str,
) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = _replace_in_function(source, function, old, new)
    with pytest.raises(AssertionError):
        _assert_contract(mutant)
