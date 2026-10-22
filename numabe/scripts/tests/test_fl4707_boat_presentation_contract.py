"""F12 boat presentation and water-integration source contract."""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "godot_project/scripts/World.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
VSB = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"


def section(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


def function(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", source, re.S)
    assert match is not None, name
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


def compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def assert_boat_water_contract(product: str, vsb: str) -> None:
    code = compact(product)
    adapter = compact(function(product, "product_fl4216_reflected_source_paint"))
    mass_colour = compact(function(product, "product_fl4707_boat_contact_mass_colour"))
    pair = compact(function(product, "product_fl4216_apply_reflection_pair"))
    night = compact(function(product, "product_apply_night_water_pair"))

    for token in (
        "constuintFL4707_BOAT_MAT_HULL=222u;",
        "constuintFL4707_BOAT_MAT_RIM=224u;",
        "constfloatFL4707_BOAT_CONTACT_MIN_VALUE_SEPARATION=0.12;",
    ):
        assert token in code
    assert "FL4707_BOAT_MAT_SAIL" not in code
    assert (
        "boolboat_contact_mass=reflected.owner_kind==OWNER_DYNAMIC_MESH"
        "&&(reflected.material==FL4707_BOAT_MAT_HULL"
        "||reflected.material==FL4707_BOAT_MAT_RIM);" in adapter
    )
    assert "mix(water_colour,WATER_DEEP_BG,0.55)*0.78" in mass_colour
    for forbidden in ("scene_query", "camera", "projection", "glyph", "alpha"):
        assert forbidden not in mass_colour

    # The exception changes only reflected hull/rim paint. Direct boat contact
    # now uses the same universal animated F11 break owner as every other solid.
    assert pair.count("product_fl4707_boat_contact_mass_colour(paper)") == 2
    assert "product_fl4216_diffuse_reflection_colour(reflected.fg,paper)" in pair
    assert "product_fl4216_diffuse_reflection_colour(authored_pair_axis,paper)" in pair
    assert "mix(WATER_FOAM,paper,0.22)" not in pair
    assert "direct_water_solid_contact" not in pair
    assert "direct_boat_contact" not in pair
    assert "product_fl4707_boat_contact_mass_colour" not in night
    assert "FL4707_BOAT_MAT_" not in compact(vsb)


def replace_once_in_function(source: str, name: str, old: str, new: str) -> str:
    body = function(source, name)
    assert body.count(old) == 1, (name, old, body.count(old))
    return source.replace(body, body.replace(old, new, 1), 1)


def assert_retired_superstructure_contract(source: str) -> None:
    ids = {
        name: int(value)
        for name, value in re.findall(r"const (BOAT_MAT_[A-Z_]+) := (\d+)", source)
    }
    assert ids == {"BOAT_MAT_HULL": 222, "BOAT_MAT_RIM": 224}
    assert "const BOAT_PRESENTATION_HULL_TOP_Y := 0.77" in source

    boat = section(source, "func _ensure_boat_airframe_rows(", "func _append_boat_source_facts(")
    calls = [line for line in boat.splitlines() if "_append_boat_face(" in line]
    assert len(calls) == 9
    executable = compact(
        "\n".join(line for line in boat.splitlines() if not line.lstrip().startswith("#"))
    )
    for retired in ("mast", "sail", "BOAT_MAT_SAIL", "3.05"):
        assert retired not in executable
    y_values = [
        float(match.group(1))
        for match in re.finditer(
            r"Vector3\([^,]+,\s*(-?\d+(?:\.\d+)?),\s*[^)]+\)", boat
        )
    ]
    assert y_values and set(y_values) == {-0.52}
    assert boat.count("BOAT_PRESENTATION_HULL_TOP_Y") == 5

    publication = section(source, "func _append_boat_source_facts(", "func _append_one_airplane_source_fact(")
    assert '"local_aabb_max": Vector3(3.0, BOAT_PRESENTATION_HULL_TOP_Y, 1.12)' in publication


def test_boat_material_ids_are_distinct_exact_and_byte_safe() -> None:
    source = WORLD.read_text(encoding="utf-8")
    ids = {
        name: int(value)
        for name, value in re.findall(r"const (BOAT_MAT_[A-Z_]+) := (\d+)", source)
    }
    assert ids == {"BOAT_MAT_HULL": 222, "BOAT_MAT_RIM": 224}
    assert len(set(ids.values())) == 2
    assert all(0 <= value <= 255 for value in ids.values())
    # Dynamic rows carry the integer as an exactly representable float; Product
    # later indexes the existing 256-column material library with that value.
    append = section(source, "func _append_boat_face(", "func _ensure_boat_airframe_rows(")
    assert "float(mat_id)" in append


def test_closed_hull_is_exactly_two_rim_and_seven_hull_faces() -> None:
    source = WORLD.read_text(encoding="utf-8")
    assert_retired_superstructure_contract(source)
    boat = section(source, "func _ensure_boat_airframe_rows(", "func _append_boat_source_facts(")
    calls = [line.strip() for line in boat.splitlines() if "_append_boat_face(" in line]
    assert len(calls) == 9
    assert all("BOAT_MAT_RIM" in line for line in calls[:2])
    assert sum("BOAT_MAT_RIM" in line for line in calls) == 2
    assert sum("BOAT_MAT_HULL" in line for line in calls) == 7

    executable = compact(
        "\n".join(line for line in boat.splitlines() if not line.lstrip().startswith("#"))
    )
    for retired in ("mast", "sail", "BOAT_MAT_SAIL", "3.05"):
        assert retired not in executable
    y_values = [
        float(match.group(1))
        for match in re.finditer(
            r"Vector3\([^,]+,\s*(-?\d+(?:\.\d+)?),\s*[^)]+\)", boat
        )
    ]
    assert y_values
    assert set(y_values) == {-0.52}
    assert boat.count("BOAT_PRESENTATION_HULL_TOP_Y") == 5
    assert "const BOAT_PRESENTATION_HULL_TOP_Y := 0.77" in source

    publication = section(source, "func _append_boat_source_facts(", "func _append_one_airplane_source_fact(")
    assert '"local_aabb_max": Vector3(3.0, BOAT_PRESENTATION_HULL_TOP_Y, 1.12)' in publication
    assert '"airframe_face_count": face_count' in publication


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ("const BOAT_MAT_RIM := 224", "const BOAT_MAT_RIM := 223"),
        (
            "const BOAT_PRESENTATION_HULL_TOP_Y := 0.77",
            "const BOAT_PRESENTATION_HULL_TOP_Y := 0.30",
        ),
        (
            "\t_boat_airframe_rows = rows",
            "\tvar mast_y1 := 3.05\n\t_boat_airframe_rows = rows",
        ),
        (
            '"local_aabb_max": Vector3(3.0, BOAT_PRESENTATION_HULL_TOP_Y, 1.12)',
            '"local_aabb_max": Vector3(3.0, 3.05, 1.12)',
        ),
    ),
)
def test_retired_superstructure_mutations_turn_red(old: str, new: str) -> None:
    source = WORLD.read_text(encoding="utf-8")
    assert source.count(old) == 1, old
    with pytest.raises(AssertionError):
        assert_retired_superstructure_contract(source.replace(old, new, 1))


def test_palette_and_boat_only_ramp_match_approved_slice() -> None:
    source = WORLD.read_text(encoding="utf-8")
    matlib = section(source, "func _inject_world_mesh_matlib_columns(", "func _placeholder_matlib_texture(")
    assert "var boat_hull := Color(0.78, 0.91, 0.84)" in matlib
    assert "var boat_rim := Color(0.56, 0.39, 0.50)" in matlib
    assert "var boat_sail" not in matlib
    assert "BOAT_MAT_SAIL" not in matlib
    assert (
        r"varis_boat:bool=mat_id==BOAT_MAT_HULL\ormat_id==BOAT_MAT_RIM"
        in compact(matlib)
    )
    assert "0.72 + 0.28 * (float(shade) / 15.0) if is_boat else f" in matlib
    assert "img.set_pixel(mat_id, 0 * 64 + sub_y" in matlib
    assert "img.set_pixel(mat_id, 1 * 64 + sub_y" in matlib
    assert "img.set_pixel(mat_id, 2 * 64 + sub_y" in matlib


def test_boat_only_water_contact_mass_preserves_generic_reflection() -> None:
    assert_boat_water_contract(
        _expand_includes(PRODUCT.read_text(encoding="utf-8")),
        VSB.read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize(
    ("name", "old", "new"),
    (
        (
            "product_fl4216_reflected_source_paint",
            "reflected.owner_kind == OWNER_DYNAMIC_MESH",
            "reflected.owner_kind != OWNER_TERRAIN",
        ),
        (
            "product_fl4216_reflected_source_paint",
            "reflected.material == FL4707_BOAT_MAT_RIM",
            "reflected.material == 255u",
        ),
        (
            "product_fl4707_boat_contact_mass_colour",
            "mix(water_colour, WATER_DEEP_BG, 0.55) * 0.78",
            "mix(water_colour, vec3(1.0), 0.55)",
        ),
        (
            "product_fl4216_apply_reflection_pair",
            ": product_fl4216_diffuse_reflection_colour(reflected.fg, paper)",
            ": product_fl4707_boat_contact_mass_colour(paper)",
        ),
    ),
)
def test_boat_water_mutations_turn_red(name: str, old: str, new: str) -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = replace_once_in_function(product, name, old, new)
    with pytest.raises(AssertionError):
        assert_boat_water_contract(mutant, VSB.read_text(encoding="utf-8"))
