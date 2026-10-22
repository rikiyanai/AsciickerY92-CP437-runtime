from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
MORPH_MANIFEST = ROOT / "assets/glyphs/fixtures/extended_glyph_material_morphology_v2.json"
MORPH_AOA = ROOT / "assets/glyphs/atlases/material.morphology.v2.atlas_of_atlases.json"
MORPH_PAGE16 = ROOT / "assets/glyphs/atlases/material.morphology.v2.page16.png"
MORPH_PAGE32 = ROOT / "assets/glyphs/atlases/material.morphology.v2.page32.png"
MORPH_CATALOG = ROOT / "assets/glyphs/generated/material.morphology.v2.shape_catalog.json"
RUNTIME_MORPH_PAGE16 = ROOT / "godot_project/assets/glyphs/atlases/material.morphology.v2.page16.png"
RUNTIME_MORPH_PAGE32 = ROOT / "godot_project/assets/glyphs/atlases/material.morphology.v2.page32.png"
RUNTIME_MORPH_CATALOG = ROOT / "godot_project/assets/glyphs/generated/material.morphology.v2.shape_catalog.json"
NATIVE_MANIFEST = ROOT / "godot_project/assets/glyphs/terminal/manifest.json"
VOCABULARY = ROOT / "godot_project/render/ascii/glyphs/authored_glyph_vocabulary.glslinc"
NATIVE_REGISTRY = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
PRODUCT_SHADER = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


from scripts.tests.test_fl4359_rain_snow_unowned_bg import (
    _expand_includes as _expand_product_includes,
)


def _product_source() -> str:
    # Outline glyph owners moved to product_*.glslinc includes in the Sep-12
    # handoff extraction. Guards read the expanded translation unit.
    return _expand_product_includes(_expand_includes(PRODUCT_SHADER.read_text(encoding="utf-8")))


def _block(source: str, start: str, end: str) -> str:
    start_at = source.index(start)
    end_at = source.index(end, start_at)
    return source[start_at:end_at]


def test_u203e_is_admitted_as_compact_morphology_slot() -> None:
    manifest = _json(MORPH_MANIFEST)
    entry = next(row for row in manifest["entries"] if row["glyph_id"] == 1880)
    assert entry["unicode_scalar"] == 0x203E
    # The manifest packs [NW, NE, SW, SE] from most to least significant
    # nibble. U+203E is ink only in the two top quadrants.
    assert entry["coverage_quadrants"] == 0x3300
    assert entry["glyph_id"] in manifest["admission_set"]
    assert manifest["admission_set"] == sorted(manifest["admission_set"])

    aoa = _json(MORPH_AOA)
    # IDs 1876..1879 belong to the additive absence pack, so morphology 1880
    # occupies compact entry 1204 rather than native sparse slot 1208.
    assert aoa["glyph_index"]["1880"] == [0, 64, 1200, 80, 1216]
    assert "1876" not in aoa["glyph_index"]
    assert "1879" not in aoa["glyph_index"]


def test_u203e_bitmap_is_top_weighted_and_catalogued() -> None:
    assert RUNTIME_MORPH_PAGE16.read_bytes() == MORPH_PAGE16.read_bytes()
    assert RUNTIME_MORPH_PAGE32.read_bytes() == MORPH_PAGE32.read_bytes()
    assert RUNTIME_MORPH_CATALOG.read_bytes() == MORPH_CATALOG.read_bytes()
    for catalog_path in (MORPH_CATALOG, RUNTIME_MORPH_CATALOG):
        catalog = _json(catalog_path)
        row = next(entry for entry in catalog["entries"] if entry["glyph_id"] == 1880)
        assert row["unicode_scalar"] == 0x203E
        assert row["stroke_class"] == "line"
        assert row["top_weight"] > 0.0
        assert row["bottom_weight"] == 0.0

    for atlas_path, cell_px in (
            (MORPH_PAGE16, 16), (RUNTIME_MORPH_PAGE16, 16),
            (MORPH_PAGE32, 32), (RUNTIME_MORPH_PAGE32, 32)):
        with Image.open(atlas_path) as image:
            cell = image.crop((4 * cell_px, 75 * cell_px,
                               5 * cell_px, 76 * cell_px)).convert("RGBA")
            alpha = cell.getchannel("A")
            bbox = alpha.getbbox()
            assert bbox is not None
            assert bbox[1] < cell_px // 4
            assert bbox[3] <= cell_px // 4
            assert all(alpha.crop((0, row, cell_px, row + 1)).getbbox() is None
                       for row in range(cell_px // 2, cell_px))


def test_native_terminal_preserves_absence_gaps_and_maps_u203e() -> None:
    manifest = _json(NATIVE_MANIFEST)
    assert len(manifest["widths"]) == 1881
    assert manifest["widths"][1880] == 1
    assert manifest["widths"][1876:1880] == [0, 0, 0, 0]
    page = next(page for page in manifest["pages"] if page["page"] == 2)
    assert page["glyph_min"] == 672
    assert page["glyph_max"] == 1880
    assert page["size"] == [512, 2432]

    vocabulary = VOCABULARY.read_text(encoding="utf-8")
    assert "if (member == ivec2(0, 1)) return 1880u;" in vocabulary
    assert "native atlas lacks [8254]" not in vocabulary


def test_native_registry_binds_u203e_to_morphology_compact_cell() -> None:
    registry = _json(NATIVE_REGISTRY)
    binding = next(row for row in registry["bindings"] if row["glyph_id"] == 1880)
    assert binding == {
        "glyph_id": 1880,
        "layer_index": 2,
        "pack_index": 2,
        "page_index": 0,
        "source_column": 4,
        "source_row": 75,
    }


def test_mesh_outline_paths_use_authored_geometry_families() -> None:
    source = _product_source()
    stone = _block(source, "uint product_stone_facet_glyph(",
                   "// FL-4203/FL-4216:")
    trunk = _block(source, "void product_retained_tree_paint_pair(",
                   "// FL-4708 root-zone")
    edge = _block(source, "uint product_stone_authored_edge_glyph(",
                  "// FL-4359: geometric creases")

    # A static mesh may not manufacture a repeated bar from projected normal
    # or a default trunk glyph. Both paths must retain their authored world
    # course and select a reviewed contour family.
    assert "0x7Cu" not in stone
    assert "authored_stair_step_rising" in stone
    assert "authored_stair_step_falling" in stone
    assert "0x7Cu" not in trunk
    assert "surface_world" in trunk
    assert "authored_stair_step_rising" in trunk
    assert "authored_stair_step_falling" in trunk

    assert "authored_boundary_line_pair" in edge
    assert "authored_aa_dot_ladder" in edge
    assert "authored_slope_rising_short" in edge
    assert "authored_slope_falling_short" in edge
    assert "authored_stair_step_rising" in edge
    assert "authored_stair_step_falling" in edge
    assert "authored_fan_open" in edge
    assert "authored_fan_collapsed" not in edge


def test_plant_stem_bars_are_scoped_to_authored_plant_vocabulary() -> None:
    vocabulary = VOCABULARY.read_text(encoding="utf-8")
    stem = _block(vocabulary, "uint authored_lateletter_stem_cycle(",
                  "// Late Letter plants.py")
    assert "return 124u;" in stem
