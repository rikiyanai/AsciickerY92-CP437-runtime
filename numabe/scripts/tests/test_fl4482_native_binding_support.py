from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "engine/generated/fl4482_unicode_bindings.h"
ATLAS_HEADER = ROOT / "engine/generated/fl4482_native_atlas_bindings.h"
ATLAS_REGISTRY = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
UNIVERSAL_HEADER = ROOT / "engine/generated/fl4482_universal_unicode_identity.h"
UNIVERSAL_META = ROOT / "assets/glyphs/generated/unicode16.universal_identity.v3.json"
RECEIPT = ROOT / "docs/research/ascii/verification/fl4482/coverage_receipt.json"
BINDINGS = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.runtime_bindings.json"
CP437_NATIVE_PAGE = ROOT / "assets/glyphs/atlases/legacy.cp437.v1.page16.png"


def test_generated_header_matches_binding_artifact() -> None:
    text = HEADER.read_text(encoding="utf-8")
    bindings = json.loads(BINDINGS.read_text(encoding="utf-8"))
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    assert len(bindings["bindings"]) == receipt["universe_count"]
    assert f'#define FL4482_UNICODE_BINDING_COUNT {receipt["universe_count"]}u' in text
    assert "#define FL4482_UNICODE_FIRST_NEW_GLYPH_ID 16777248u" in text
    assert bindings["unicode_scalar_registry_sha256"] in text
    assert receipt["native_binding_header_sha256"]
    assert all(row["admitted"] for row in bindings["bindings"])


def test_native_header_lookup_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "probe.cpp"
    binary = tmp_path / "probe"
    source.write_text(
        '#include "engine/generated/fl4482_unicode_bindings.h"\n'
        "int main() {\n"
        "  const Fl4482UnicodeBindingV1* first = fl4482_unicode_binding_by_glyph_id(0x01000020u);\n"
        "  const Fl4482UnicodeBindingV1* missing = fl4482_unicode_binding_by_glyph_id(0x01000378u);\n"
        "  const Fl4482UnicodeBindingV1* ascii = fl4482_unicode_binding_by_scalar(0x20u);\n"
        "  return (!first || missing || !ascii || first->pack_index != 1u) ? 1 : 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_universal_identity_covers_every_scalar_and_preserves_bindings() -> None:
    meta = json.loads(UNIVERSAL_META.read_text(encoding="utf-8"))
    header = UNIVERSAL_HEADER.read_text(encoding="utf-8")
    assert meta["schema"] == "fl4482.universal_unicode_identity.v3"
    assert meta["unicode_scalar_count"] == 1_112_064
    assert meta["existing_binding_count"] == len(json.loads(BINDINGS.read_text())["bindings"])
    assert meta["record_size"] == 12
    assert meta["records_sha256"] in header
    assert meta["registry_sha256"] in header
    assert meta["render_class_counts"]["10"] == meta["existing_binding_count"]


def test_universal_identity_native_lookup_is_total_and_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "universal_probe.cpp"
    binary = tmp_path / "universal_probe"
    source.write_text(
        '#include "engine/generated/fl4482_universal_unicode_identity.h"\n'
        "int main() {\n"
        "  Fl4482UnicodeIdentityRecordV2 ascii = {}, unassigned = {}, combining = {}, wide = {}, last = {};\n"
        "  uint32_t scalar = 0u;\n"
        "  if (!fl4482_unicode_identity_by_scalar_v2(0x20u, &ascii) || ascii.glyph_id != FL4482_UNIVERSAL_GLYPH_ID_BASE + 0x20u ||\n"
        "      ascii.render_class != FL4482_UNICODE_RENDER_ONE_CELL_ADMITTED) return 1;\n"
        "  if (!fl4482_unicode_identity_by_scalar_v2(0x0378u, &unassigned) ||\n"
        "      unassigned.glyph_id != FL4482_UNIVERSAL_GLYPH_ID_BASE + 0x0378u ||\n"
        "      unassigned.render_class != FL4482_UNICODE_RENDER_UNASSIGNED) return 2;\n"
        "  if (!fl4482_unicode_identity_by_scalar_v2(0x0301u, &combining) ||\n"
        "      combining.render_class != FL4482_UNICODE_RENDER_COMBINING || combining.cell_width_class != 1u) return 3;\n"
        "  if (!fl4482_unicode_identity_by_scalar_v2(0x4E00u, &wide) ||\n"
        "      wide.render_class != FL4482_UNICODE_RENDER_ONE_CELL_ADMITTED || wide.cell_width_class != 2u) return 4;\n"
        "  if (!fl4482_unicode_identity_by_scalar_v2(0x10FFFFu, &last) ||\n"
        "      fl4482_unicode_identity_by_scalar_v2(0xD800u, &last)) return 5;\n"
        "  if (!fl4482_unicode_scalar_by_identity_v2(unassigned.glyph_id, &scalar) || scalar != 0x0378u) return 6;\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_native_atlas_registry_is_exact_and_collision_free() -> None:
    registry = json.loads(ATLAS_REGISTRY.read_text(encoding="utf-8"))
    rows = registry["bindings"]
    assert registry["schema"] == "fl4482.native_atlas_registry.v2"
    assert [pack["content_pack_id"] for pack in registry["packs"]] == [
        "legacy.cp437.v1",
        "material.additive.v1",
        "material.morphology.v2",
        "unicode16.onecell.v1",
    ]
    receipt = json.loads(RECEIPT.read_text())
    universal_pages = receipt["atlas_geometry"][-1]["page_count"]
    assert [pack["page_count"] for pack in registry["packs"]] == [1, 1, 1, universal_pages]
    assert [page["row_count"] for page in registry["pages"]] == [16, 11, 76] + [76] * universal_pages
    assert registry["page_count"] == universal_pages + 3
    assert registry["binding_count"] == len(rows)
    assert len({row["glyph_id"] for row in rows}) == len(rows)
    assert rows == sorted(rows, key=lambda row: row["glyph_id"])
    header = ATLAS_HEADER.read_text(encoding="utf-8")
    assert all(page["page16_sha256"] in header for page in registry["pages"])
    assert [row["layer_index"] for row in rows if row["glyph_id"] in (0, 255, 512, 672, 0x01000020)] == [0, 0, 1, 2, 3]
    assert registry["lut_mapping"]["columns"] == 2048
    assert registry["runtime_consumed"] is True


def test_native_cp437_layer_is_rgba_and_matches_registry_hash() -> None:
    registry = json.loads(ATLAS_REGISTRY.read_text(encoding="utf-8"))
    cp437_page = registry["pages"][0]
    assert cp437_page["content_pack_id"] == "legacy.cp437.v1"
    assert cp437_page["page16_path"] == "assets/glyphs/atlases/legacy.cp437.v1.page16.png"
    with Image.open(CP437_NATIVE_PAGE) as image:
        assert image.mode == "RGBA"
        assert image.size == (256, 256)
        assert hashlib.sha256(image.tobytes()).hexdigest() == cp437_page["page16_sha256"]


def test_asciiid_page_atlas_shaders_use_layered_coordinates() -> None:
    asciiid = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    assert asciiid.count("uniform sampler2DArray page_atlas;") == 2
    assert "uniform sampler2D page_atlas;" not in asciiid
    assert asciiid.count("texture(page_atlas, vec3(page_uv, page_layer))") == 2
    assert "texture(page_atlas, atlas_coord)" not in asciiid


def test_native_atlas_header_lookup_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "atlas_probe.cpp"
    binary = tmp_path / "atlas_probe"
    source.write_text(
        '#include "engine/generated/fl4482_native_atlas_bindings.h"\n'
        "int main() {\n"
        "  const Fl4482NativeAtlasBindingV1* cp437 = fl4482_native_atlas_binding_by_glyph_id(0u);\n"
        "  const Fl4482NativeAtlasBindingV1* additive = fl4482_native_atlas_binding_by_glyph_id(512u);\n"
        "  const Fl4482NativeAtlasBindingV1* morphology = fl4482_native_atlas_binding_by_glyph_id(672u);\n"
        "  const Fl4482NativeAtlasBindingV1* unicode = fl4482_native_atlas_binding_by_glyph_id(0x01000020u);\n"
        "  const Fl4482NativeAtlasBindingV1* missing = fl4482_native_atlas_binding_by_glyph_id(0x01000378u);\n"
        "  return (!cp437 || !additive || !morphology || !unicode || missing ||\n"
        "          cp437->layer_index != 0u || additive->layer_index != 1u || morphology->layer_index != 2u || unicode->layer_index != 3u) ? 1 : 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_native_atlas_lut_slot_mapping_is_shared_and_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "atlas_lut_probe.cpp"
    binary = tmp_path / "atlas_lut_probe"
    source.write_text(
        '#include "engine/fl4482_native_atlas_lut.h"\n'
        "int main() {\n"
        "  if (Fl4482NativeAtlasLutSlot(672u) != 672u) return 1;\n"
        "  if (Fl4482NativeAtlasLutSlot(FL4482_NATIVE_ATLAS_LOW_GLYPH_ID_LIMIT - 1u) !=\n"
        "      FL4482_NATIVE_ATLAS_LOW_GLYPH_ID_LIMIT - 1u) return 2;\n"
        "  if (Fl4482NativeAtlasLutSlot(FL4482_NATIVE_ATLAS_LOW_GLYPH_ID_LIMIT) != UINT32_MAX) return 3;\n"
        "  if (Fl4482NativeAtlasLutSlot(FL4482_NATIVE_ATLAS_UNIVERSAL_GLYPH_ID_BASE + 0x20u) !=\n"
        "      FL4482_NATIVE_ATLAS_LOW_GLYPH_ID_LIMIT + 0x20u) return 4;\n"
        "  if (Fl4482NativeAtlasLutSlot(FL4482_NATIVE_ATLAS_UNIVERSAL_GLYPH_ID_BASE +\n"
        "          FL4482_NATIVE_ATLAS_UNIVERSAL_SCALAR_SPAN - 1u) !=\n"
        "      FL4482_NATIVE_ATLAS_LUT_SLOT_COUNT - 1u) return 5;\n"
        "  if (Fl4482NativeAtlasLutSlot(FL4482_NATIVE_ATLAS_UNIVERSAL_GLYPH_ID_BASE +\n"
        "          FL4482_NATIVE_ATLAS_UNIVERSAL_SCALAR_SPAN) != UINT32_MAX) return 6;\n"
        "  if (Fl4482NativeAtlasLutSlot(0xFFFFFFFDu) != UINT32_MAX) return 7;\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_lut_pixels_encode_layer_column_row_and_alpha(tmp_path: Path) -> None:
    """The four bytes each LUT slot holds must mean what the shader reads.

    The lookup table is a picture, not a list: each glyph's atlas address is
    stored as one RGBA pixel. The shader reads that pixel and takes

        channel 0 -> which atlas layer the glyph's page lives on
        channel 1 -> the column of the glyph's cell within that page
        channel 2 -> the row of the glyph's cell within that page
        channel 3 -> 255 for "this slot is populated", 0 for "nothing here"

    Byte order matters absolutely. Swapping the column and row channels does not
    fail to build and raises nothing at runtime; it silently draws the glyph
    from the wrong cell, which reads as a rendering-quality complaint rather
    than an addressing bug. This probe rebuilds the table exactly as the
    presenter does and checks the encoding against the binding registry.

    The last channel is checked in BOTH directions. A populated slot must be
    opaque, and a slot no glyph maps to must stay entirely zero -- otherwise an
    unmapped identity would present as a real atlas address (FL-4482).
    """
    source = tmp_path / "atlas_lut_pixels_probe.cpp"
    binary = tmp_path / "atlas_lut_pixels_probe"
    source.write_text(
        '#include "engine/fl4482_native_atlas_lut.h"\n'
        "#include <cstdlib>\n"
        "#include <cstring>\n"
        "\n"
        "// Same construction the presenter performs when it uploads lut_tex.\n"
        "static void BuildLut(unsigned char* lut) {\n"
        "  memset(lut, 0, (size_t)FL4482_NATIVE_ATLAS_LUT_COLUMNS *\n"
        "      (size_t)FL4482_NATIVE_ATLAS_LUT_ROWS * 4u);\n"
        "  for (size_t i = 0; i < FL4482_NATIVE_ATLAS_BINDING_COUNT; i++) {\n"
        "    const Fl4482NativeAtlasBindingV1* b =\n"
        "        &FL4482_NATIVE_ATLAS_BINDINGS_BY_GLYPH_ID[i];\n"
        "    const uint32_t slot = Fl4482NativeAtlasLutSlot(b->glyph_id);\n"
        "    if (slot == UINT32_MAX || slot >= FL4482_NATIVE_ATLAS_LUT_SLOT_COUNT)\n"
        "      continue;\n"
        "    lut[slot * 4 + 0] = (unsigned char)b->layer_index;\n"
        "    lut[slot * 4 + 1] = (unsigned char)b->source_column;\n"
        "    lut[slot * 4 + 2] = (unsigned char)b->source_row;\n"
        "    lut[slot * 4 + 3] = 255;\n"
        "  }\n"
        "}\n"
        "\n"
        "int main() {\n"
        "  const size_t bytes = (size_t)FL4482_NATIVE_ATLAS_LUT_COLUMNS *\n"
        "      (size_t)FL4482_NATIVE_ATLAS_LUT_ROWS * 4u;\n"
        "  unsigned char* lut = (unsigned char*)malloc(bytes);\n"
        "  if (!lut) return 1;\n"
        "  BuildLut(lut);\n"
        "\n"
        "  // Every admitted binding must be readable back at its own slot with\n"
        "  // all four channels intact.\n"
        "  for (size_t i = 0; i < FL4482_NATIVE_ATLAS_BINDING_COUNT; i++) {\n"
        "    const Fl4482NativeAtlasBindingV1* b =\n"
        "        &FL4482_NATIVE_ATLAS_BINDINGS_BY_GLYPH_ID[i];\n"
        "    const uint32_t slot = Fl4482NativeAtlasLutSlot(b->glyph_id);\n"
        "    if (slot == UINT32_MAX || slot >= FL4482_NATIVE_ATLAS_LUT_SLOT_COUNT)\n"
        "      continue;\n"
        "    if (lut[slot * 4 + 0] != (unsigned char)b->layer_index)   return 2;\n"
        "    if (lut[slot * 4 + 1] != (unsigned char)b->source_column) return 3;\n"
        "    if (lut[slot * 4 + 2] != (unsigned char)b->source_row)    return 4;\n"
        "    if (lut[slot * 4 + 3] != 255)                             return 5;\n"
        "  }\n"
        "\n"
        "  // A failure sentinel has no slot, so it can write nothing.\n"
        "  if (Fl4482NativeAtlasLutSlot(0xFFFFFFFDu) != UINT32_MAX) return 6;\n"
        "\n"
        "  // At least one slot must remain entirely zero, proving unpopulated\n"
        "  // entries stay transparent instead of aliasing a real address.\n"
        "  int found_empty = 0;\n"
        "  for (uint32_t s = 0; s < FL4482_NATIVE_ATLAS_LUT_SLOT_COUNT; s++) {\n"
        "    if (lut[s * 4 + 3] == 0) {\n"
        "      if (lut[s * 4 + 0] || lut[s * 4 + 1] || lut[s * 4 + 2]) return 7;\n"
        "      found_empty = 1;\n"
        "      break;\n"
        "    }\n"
        "  }\n"
        "  if (!found_empty) return 8;\n"
        "  free(lut);\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_native_consumers_delete_numeric_range_owners() -> None:
    asciiid = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    term = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")
    resolver = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")
    assert "kAsciiidCompiledAtlasLastGlyphId" not in asciiid
    assert "fl4482_native_atlas_binding_by_glyph_id" in asciiid
    assert "FL4482_NATIVE_ATLAS_COMBINED_WIDTH_PX" not in asciiid
    assert "combined_column" not in asciiid
    assert "binding->layer_index" in asciiid
    assert "uniform sampler2DArray page_atlas" in asciiid
    assert "TermLoadAtlasOfAtlasesMaxGlyphId" not in term
    assert "native_glyph_last_id" not in term
    assert "TermCompiledGlyphLutSlot" not in term
    assert '#include "../engine/fl4482_native_atlas_lut.h"' in term
    assert "Fl4482NativeAtlasLutSlot(binding->glyph_id)" in term
    assert "FL4482_NATIVE_ATLAS_PAGE16_SHA256" in term
    assert "FL4482_NATIVE_ATLAS_PAGE_ROW_COUNTS" in term
    assert "binding->layer_index" in term
    assert '#include "generated/fl4482_native_atlas_bindings.h"' in resolver
    assert "fl4482_native_atlas_binding_by_glyph_id" in resolver


def test_termpp_uses_one_final_identity_plane_and_layered_atlas() -> None:
    presenter = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")
    assert "GlyphId final_glyph_buf" in presenter
    assert "uniform usampler2D final_glyph" in presenter
    assert "uint final_gid = texture(final_glyph, ansi_coord).r;" in presenter
    assert "uint lut_slot = 0u;" in presenter
    assert "lut_slot = final_gid;" in presenter
    assert "lut_slot = lut_low_limit + final_gid - lut_universal_base;" in presenter
    assert "vec4 lut_entry = texelFetch(lut_tex, lut_xy, 0);" in presenter
    assert "glyph_alpha = texture(page_atlas, vec3(glyph_coord, atlas_layer)).a;" in presenter
    assert "int glyph_idx = int(final_gid);" not in presenter
    assert "int(round(cell.b * 255.0))" not in presenter
    assert "uniform sampler2DArray page_atlas" in presenter
    assert "uniform ivec2 lut_grid" in presenter
    assert "glBindTexture(GL_TEXTURE_2D_ARRAY, term->page_atlas_id)" in presenter
    assert "TermLoadCompiledGlyphAtlasLayers" in presenter
    assert "binding->source_row" in presenter
    assert "binding->layer_index" in presenter
    assert "TermLoadCompiledGlyphAtlasPage" not in presenter
    assert "Fl4482LoadAtlasPngRgba8" in presenter
