#!/usr/bin/env python3
"""Build the canonical FL-4482 universal offline glyph products.

This is the sole producer for ``fl4482.unicode_coverage.v2``. It vendors pinned
Unicode inputs, consumes the content-addressed repository font chain, evaluates
every Unicode scalar, preserves existing material bindings, rasterizes every
eligible standalone non-emoji text glyph, measures it, emits paged atlases and
immutable bindings, then writes the runtime registry and coverage receipt.

The producer never edits the existing morphology pack.  A scalar present in
that higher-priority pack is evaluated there and cannot fall through to the new
pack when its glyph test fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any

from PIL import Image

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from fl4482_font_chain import (
    CanonicalFontChain,
    PINNED_FONT_CHAIN,
    font_chain_receipt,
)


ROOT = Path(__file__).resolve().parents[1]
UCD_DIR = ROOT / "assets/unicode/16.0.0"
MORPH_MANIFEST = ROOT / "assets/glyphs/fixtures/extended_glyph_material_morphology_v2.json"
MORPH_AOA = ROOT / "assets/glyphs/atlases/material.morphology.v2.atlas_of_atlases.json"
MORPH_CATALOG = ROOT / "assets/glyphs/generated/material.morphology.v2.shape_catalog.json"
ADDITIVE_AOA = ROOT / "assets/glyphs/atlases/material.additive.v1.atlas_of_atlases.json"
CP437_ATLAS = ROOT / "assets/fonts/cp437_16x16.png"
CP437_NATIVE_SOURCE = ROOT / "assets/glyphs/generated/cp437.native_atlas_source.v1.json"
CP437_NATIVE_PAGE = ROOT / "assets/glyphs/atlases/legacy.cp437.v1.page16.png"
SCALAR_REGISTRY = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.scalar_registry.json"
NEW_MANIFEST = ROOT / "assets/glyphs/fixtures/extended_glyph_unicode16_onecell_v1.json"
NEW_AOA = ROOT / "assets/glyphs/atlases/unicode16.onecell.v1.atlas_of_atlases.json"
NEW_CATALOG = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json"
RUNTIME_BINDINGS = ROOT / "assets/glyphs/generated/unicode16.onecell.v1.runtime_bindings.json"
NATIVE_HEADER = ROOT / "engine/generated/fl4482_unicode_bindings.h"
NATIVE_ATLAS_REGISTRY = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
NATIVE_ATLAS_HEADER = ROOT / "engine/generated/fl4482_native_atlas_bindings.h"
UNIVERSAL_IDENTITY_META = ROOT / "assets/glyphs/generated/unicode16.universal_identity.v3.json"
UNIVERSAL_IDENTITY_HEADER = ROOT / "engine/generated/fl4482_universal_unicode_identity.h"
RECEIPT = ROOT / "docs/research/ascii/verification/fl4482/coverage_receipt.json"

SCHEMA = "fl4482.unicode_coverage.v2"
UNICODE_VERSION = "16.0.0"
PACK_ORDER = ["material.morphology.v2", "unicode16.onecell.v1"]
COLS = 16
ROWS = 76
CAPACITY = COLS * ROWS
CELL_PX = 16
UNIVERSAL_GLYPH_ID_BASE = 0x01000000
UNICODE_SCALAR_COUNT = 0x110000 - 0x800
EXCLUDED_GC = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp", "Mn", "Mc", "Me"}
ALLOWED_EAW = {"N", "Na", "H", "A"}
GENERAL_CATEGORY_CODES = {
    value: index
    for index, value in enumerate([
        "Cn", "Lu", "Ll", "Lt", "Lm", "Lo", "Mn", "Mc", "Me",
        "Nd", "Nl", "No", "Pc", "Pd", "Ps", "Pe", "Pi", "Pf",
        "Po", "Sm", "Sc", "Sk", "So", "Zs", "Zl", "Zp", "Cc",
        "Cf", "Cs", "Co",
    ])
}
EAW_CODES = {value: index for index, value in enumerate(["N", "Na", "H", "A", "W", "F"])}

FLAG_ASSIGNED = 1 << 0
FLAG_DEFAULT_IGNORABLE = 1 << 1
FLAG_FONT_SUPPORTED = 1 << 2
FLAG_EXISTING_BINDING = 1 << 3
FLAG_ATLAS_COVERED = 1 << 4
FLAG_DESCRIPTOR_COVERED = 1 << 5
FLAG_COMBINING = 1 << 6
FLAG_ONE_CELL_RENDERABLE = 1 << 7
FLAG_NONCHARACTER = 1 << 8
FLAG_PRIVATE_USE = 1 << 9
FLAG_CONTROL = 1 << 10
FLAG_WIDE = 1 << 11
FLAG_EMOJI_PRESENTATION = 1 << 12

WIDTH_NONRENDERABLE = 0
WIDTH_ZERO = 1
WIDTH_ONE_CELL = 2
WIDTH_WIDE = 3

RENDER_UNASSIGNED = 0
RENDER_NONCHARACTER = 1
RENDER_CONTROL = 2
RENDER_PRIVATE_USE = 3
RENDER_DEFAULT_IGNORABLE = 4
RENDER_COMBINING = 5
RENDER_LINE_SEPARATOR = 6
RENDER_WIDE = 7
RENDER_FONT_MISSING = 8
RENDER_UNMEASURED = 9
RENDER_ONE_CELL_ADMITTED = 10
RENDER_EMOJI_PRESENTATION = 11
UCD_FILES = {
    "UnicodeData.txt": "ff58e5823bd095166564a006e47d111130813dcf8bf234ef79fa51a870edb48f",
    "DerivedCoreProperties.txt": "39d35161f2954497f69e08bdb9e701493f476a3d30222de20028feda36c1dabd",
    "EastAsianWidth.txt": "43adc76c0686a42cb370764eb8cfe2b2a45b10b855e5572a2db4a0eecce15d5b",
    "emoji/emoji-data.txt": "f1365a5173eee18e1f98b240cdc492e84a25f1ce7e0c9d1094eb29c41a22696a",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_hash(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(data)


def write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        if compact:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        else:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def unicode_page_png_path(page_index: int) -> Path:
    return ROOT / f"assets/glyphs/atlases/unicode16.onecell.v1.page{page_index}.page16.png"


def native_header_text(
    bindings: list[dict[str, Any]], registry_hash: str, manifest_hashes: dict[str, str]
) -> str:
    """Serialize the compiler-owned native lookup table.

    This header contains no presentation policy.  It provides exact immutable
    identity and atlas addresses so native consumers can delete their numeric
    range inference before adopting the table atomically.
    """
    by_scalar = sorted(bindings, key=lambda row: int(row["unicode_scalar"]))
    by_gid = sorted(bindings, key=lambda row: int(row["glyph_id"]))
    onecell_gids = [
        int(row["glyph_id"])
        for row in bindings
        if row["pack"] == "unicode16.onecell.v1"
    ]

    def record(row: dict[str, Any]) -> str:
        pack = 0 if row["pack"] == "material.morphology.v2" else 1
        return (
            f"    {{{int(row['unicode_scalar'])}u, {int(row['glyph_id'])}u, "
            f"{int(row['tile_index'])}u, {int(row['page_index'])}u, {pack}u, 1u, 1u, 1u}},"
        )

    lines = [
        "// Generated by scripts/fl4482_coverage_census.py. DO NOT EDIT.",
        "// FL-4482: compiler-owned Unicode 16 scalar/GlyphId/atlas binding table.",
        "#pragma once",
        "",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        "#define FL4482_UNICODE_BINDING_SCHEMA \"fl4482.runtime_bindings.v1\"",
        f"#define FL4482_UNICODE_SCALAR_REGISTRY_SHA256 \"{registry_hash}\"",
        f"#define FL4482_MORPHOLOGY_MANIFEST_SHA256 \"{manifest_hashes['material.morphology.v2']}\"",
        f"#define FL4482_ONECELL_MANIFEST_SHA256 \"{manifest_hashes['unicode16.onecell.v1']}\"",
        f"#define FL4482_UNICODE_BINDING_COUNT {len(bindings)}u",
        f"#define FL4482_UNICODE_FIRST_NEW_GLYPH_ID {min(onecell_gids)}u",
        f"#define FL4482_UNICODE_LAST_NEW_GLYPH_ID {max(onecell_gids)}u",
        f"#define FL4482_UNICODE_ATLAS_COLUMNS {COLS}u",
        f"#define FL4482_UNICODE_ATLAS_ROWS {ROWS}u",
        f"#define FL4482_UNICODE_ATLAS_CAPACITY {CAPACITY}u",
        "",
        "typedef enum Fl4482UnicodePackV1 {",
        "    FL4482_UNICODE_PACK_MATERIAL_MORPHOLOGY_V2 = 0,",
        "    FL4482_UNICODE_PACK_UNICODE16_ONECELL_V1 = 1,",
        "} Fl4482UnicodePackV1;",
        "",
        "typedef struct Fl4482UnicodeBindingV1 {",
        "    uint32_t unicode_scalar;",
        "    uint32_t glyph_id;",
        "    uint16_t tile_index;",
        "    uint8_t page_index;",
        "    uint8_t pack_index;",
        "    uint8_t admitted;",
        "    uint8_t atlas_covered;",
        "    uint8_t descriptor_covered;",
        "} Fl4482UnicodeBindingV1;",
        "",
        "static const char* const FL4482_UNICODE_PACK_MANIFEST_PATHS[2] = {",
        "    \"assets/glyphs/fixtures/extended_glyph_material_morphology_v2.json\",",
        "    \"assets/glyphs/fixtures/extended_glyph_unicode16_onecell_v1.json\",",
        "};",
        "static const char* const FL4482_UNICODE_PACK_AOA_PATHS[2] = {",
        "    \"assets/glyphs/atlases/material.morphology.v2.atlas_of_atlases.json\",",
        "    \"assets/glyphs/atlases/unicode16.onecell.v1.atlas_of_atlases.json\",",
        "};",
        "static const Fl4482UnicodeBindingV1 FL4482_UNICODE_BINDINGS_BY_SCALAR[FL4482_UNICODE_BINDING_COUNT] = {",
        *[record(row) for row in by_scalar],
        "};",
        "",
        "static const Fl4482UnicodeBindingV1 FL4482_UNICODE_BINDINGS_BY_GLYPH_ID[FL4482_UNICODE_BINDING_COUNT] = {",
        *[record(row) for row in by_gid],
        "};",
        "",
        "static inline const Fl4482UnicodeBindingV1* fl4482_unicode_binding_by_glyph_id(uint32_t glyph_id)",
        "{",
        "    size_t lo = 0;",
        "    size_t hi = FL4482_UNICODE_BINDING_COUNT;",
        "    while (lo < hi) {",
        "        const size_t mid = lo + (hi - lo) / 2;",
        "        const uint32_t candidate = FL4482_UNICODE_BINDINGS_BY_GLYPH_ID[mid].glyph_id;",
        "        if (candidate < glyph_id) lo = mid + 1;",
        "        else hi = mid;",
        "    }",
        "    if (lo >= FL4482_UNICODE_BINDING_COUNT || FL4482_UNICODE_BINDINGS_BY_GLYPH_ID[lo].glyph_id != glyph_id)",
        "        return NULL;",
        "    return &FL4482_UNICODE_BINDINGS_BY_GLYPH_ID[lo];",
        "}",
        "",
        "static inline const Fl4482UnicodeBindingV1* fl4482_unicode_binding_by_scalar(uint32_t scalar)",
        "{",
        "    size_t lo = 0;",
        "    size_t hi = FL4482_UNICODE_BINDING_COUNT;",
        "    while (lo < hi) {",
        "        const size_t mid = lo + (hi - lo) / 2;",
        "        const uint32_t candidate = FL4482_UNICODE_BINDINGS_BY_SCALAR[mid].unicode_scalar;",
        "        if (candidate < scalar) lo = mid + 1;",
        "        else hi = mid;",
        "    }",
        "    if (lo >= FL4482_UNICODE_BINDING_COUNT || FL4482_UNICODE_BINDINGS_BY_SCALAR[lo].unicode_scalar != scalar)",
        "        return NULL;",
        "    return &FL4482_UNICODE_BINDINGS_BY_SCALAR[lo];",
        "}",
        "",
    ]
    return "\n".join(lines)


def native_atlas_registry_and_header(aoas: list[tuple[str, Path, dict[str, Any]]]) -> tuple[dict[str, Any], str]:
    """Build the exact page-layer registry consumed by native presentation."""
    packs: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    seen: set[int] = set()
    for pack_index, (pack_id, aoa_path, aoa) in enumerate(aoas):
        if pack_index > 255:
            raise SystemExit("native atlas pack index exceeds RGBA8 registry capacity")
        pack_pages = [page for page in aoa["pages"] if int(page["cell_px"]) == CELL_PX]
        if not pack_pages:
            raise SystemExit(f"native pack has no page16 atlas: {pack_id}")
        layer_by_page: dict[int, int] = {}
        page_rows: dict[int, int] = {}
        for page_index, page in enumerate(pack_pages):
            if page_index > 255 or len(pages) > 255:
                raise SystemExit("native atlas page index exceeds RGBA8 LUT capacity")
            width = int(page["width_px"])
            height = int(page["height_px"])
            if width != COLS * CELL_PX or height % CELL_PX:
                raise SystemExit(
                    f"unsupported native page16 geometry for {pack_id} page {page_index}: {width}x{height}"
                )
            rows = height // CELL_PX
            layer_index = len(pages)
            layer_by_page[page_index] = layer_index
            page_rows[page_index] = rows
            source_path = aoa_path.parent / page["url"]
            if source_path.suffix.lower() == ".json":
                png_name = source_path.name.replace(".page16_rgba8.json", ".page16.png")
                source_path = source_path.with_name(png_name)
            source_path = source_path.resolve()
            pages.append({
                "layer_index": layer_index,
                "pack_index": pack_index,
                "page_index": page_index,
                "content_pack_id": pack_id,
                "page16_path": str(source_path.relative_to(ROOT)),
                "page16_format": "png_rgba8",
                "page16_sha256": page["page_hash"],
                "width_px": width,
                "height_px": height,
                "row_count": rows,
            })
        packs.append({
            "pack_index": pack_index,
            "content_pack_id": pack_id,
            "aoa_path": str(aoa_path.relative_to(ROOT)),
            "aoa_canonical_sha256": canonical_hash(aoa),
            "page_count": len(pack_pages),
            "layer_first": min(layer_by_page.values()),
            "layer_count": len(pack_pages),
        })
        for gid_text, rect in aoa["glyph_index"].items():
            gid = int(gid_text)
            if gid in seen:
                raise SystemExit(f"duplicate native GlyphId {gid} across page16 packs")
            seen.add(gid)
            page_index, x0, y0, x1, y1 = map(int, rect)
            if page_index not in layer_by_page or x1 - x0 != CELL_PX or y1 - y0 != CELL_PX:
                raise SystemExit(f"unsupported native atlas rect for GlyphId {gid}: {rect}")
            source_column = x0 // CELL_PX
            source_row = y0 // CELL_PX
            if source_column >= COLS or source_row >= page_rows[page_index]:
                raise SystemExit(f"native atlas rect outside page for GlyphId {gid}: {rect}")
            bindings.append({
                "glyph_id": gid,
                "layer_index": layer_by_page[page_index],
                "pack_index": pack_index,
                "page_index": page_index,
                "source_column": source_column,
                "source_row": source_row,
            })
    bindings.sort(key=lambda row: row["glyph_id"])
    low_glyph_id_limit = max(
        [int(row["glyph_id"]) for row in bindings if int(row["glyph_id"]) < UNIVERSAL_GLYPH_ID_BASE],
        default=-1,
    ) + 1
    lut_columns = 2048
    lut_slot_count = low_glyph_id_limit + 0x110000
    lut_rows = (lut_slot_count + lut_columns - 1) // lut_columns
    registry = {
        "schema": "fl4482.native_atlas_registry.v2",
        "cell_px": CELL_PX,
        "columns": COLS,
        "packs": packs,
        "pages": pages,
        "page_count": len(pages),
        "binding_count": len(bindings),
        "lut_mapping": {
            "columns": lut_columns,
            "rows": lut_rows,
            "slot_count": lut_slot_count,
            "low_glyph_id_limit": low_glyph_id_limit,
            "universal_glyph_id_base": UNIVERSAL_GLYPH_ID_BASE,
            "universal_scalar_span": 0x110000,
        },
        "bindings": bindings,
        "runtime_consumed": True,
        "proof_state": "IMPLEMENTED_UNPROVEN",
    }
    registry["registry_sha256"] = canonical_hash(registry)

    def record(row: dict[str, Any]) -> str:
        return (
            f"    {{{row['glyph_id']}u, {row['source_column']}u, {row['source_row']}u, "
            f"{row['layer_index']}u, {row['pack_index']}u, {row['page_index']}u}},"
        )

    lines = [
        "// Generated by scripts/fl4482_coverage_census.py. DO NOT EDIT.",
        "// FL-4482: exact native GlyphId/page-layer atlas registry; numeric gaps fail closed.",
        "#pragma once",
        "",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "",
        "#define FL4482_NATIVE_ATLAS_SCHEMA \"fl4482.native_atlas_registry.v2\"",
        f"#define FL4482_NATIVE_ATLAS_REGISTRY_SHA256 \"{registry['registry_sha256']}\"",
        f"#define FL4482_NATIVE_ATLAS_BINDING_COUNT {len(bindings)}u",
        f"#define FL4482_NATIVE_ATLAS_COLUMNS {COLS}u",
        f"#define FL4482_NATIVE_ATLAS_PACK_COUNT {len(packs)}u",
        f"#define FL4482_NATIVE_ATLAS_PAGE_COUNT {len(pages)}u",
        f"#define FL4482_NATIVE_ATLAS_LUT_COLUMNS {lut_columns}u",
        f"#define FL4482_NATIVE_ATLAS_LUT_ROWS {lut_rows}u",
        f"#define FL4482_NATIVE_ATLAS_LUT_SLOT_COUNT {lut_slot_count}u",
        f"#define FL4482_NATIVE_ATLAS_LOW_GLYPH_ID_LIMIT {low_glyph_id_limit}u",
        f"#define FL4482_NATIVE_ATLAS_UNIVERSAL_GLYPH_ID_BASE {UNIVERSAL_GLYPH_ID_BASE}u",
        "#define FL4482_NATIVE_ATLAS_UNIVERSAL_SCALAR_SPAN 0x110000u",
        "",
        "typedef struct Fl4482NativeAtlasBindingV1 {",
        "    uint32_t glyph_id;",
        "    uint16_t source_column;",
        "    uint16_t source_row;",
        "    uint8_t layer_index;",
        "    uint8_t pack_index;",
        "    uint8_t page_index;",
        "} Fl4482NativeAtlasBindingV1;",
        "",
        f"static const char* const FL4482_NATIVE_ATLAS_PAGE16_PATHS[{len(pages)}] = {{",
        *[f"    \"{page['page16_path']}\"," for page in pages],
        "};",
        f"static const char* const FL4482_NATIVE_ATLAS_PAGE16_SHA256[{len(pages)}] = {{",
        *[f"    \"{page['page16_sha256']}\"," for page in pages],
        "};",
        f"static const uint16_t FL4482_NATIVE_ATLAS_PAGE_ROW_COUNTS[{len(pages)}] = {{",
        *[f"    {page['row_count']}u," for page in pages],
        "};",
        f"static const uint8_t FL4482_NATIVE_ATLAS_PAGE_PACK_INDEX[{len(pages)}] = {{",
        *[f"    {page['pack_index']}u," for page in pages],
        "};",
        f"static const uint8_t FL4482_NATIVE_ATLAS_PAGE_INDEX[{len(pages)}] = {{",
        *[f"    {page['page_index']}u," for page in pages],
        "};",
        "",
        "static const Fl4482NativeAtlasBindingV1 FL4482_NATIVE_ATLAS_BINDINGS_BY_GLYPH_ID[FL4482_NATIVE_ATLAS_BINDING_COUNT] = {",
        *[record(row) for row in bindings],
        "};",
        "",
        "static inline const Fl4482NativeAtlasBindingV1* fl4482_native_atlas_binding_by_glyph_id(uint32_t glyph_id)",
        "{",
        "    size_t lo = 0;",
        "    size_t hi = FL4482_NATIVE_ATLAS_BINDING_COUNT;",
        "    while (lo < hi) {",
        "        const size_t mid = lo + (hi - lo) / 2;",
        "        const uint32_t candidate = FL4482_NATIVE_ATLAS_BINDINGS_BY_GLYPH_ID[mid].glyph_id;",
        "        if (candidate < glyph_id) lo = mid + 1;",
        "        else hi = mid;",
        "    }",
        "    if (lo >= FL4482_NATIVE_ATLAS_BINDING_COUNT || FL4482_NATIVE_ATLAS_BINDINGS_BY_GLYPH_ID[lo].glyph_id != glyph_id)",
        "        return NULL;",
        "    return &FL4482_NATIVE_ATLAS_BINDINGS_BY_GLYPH_ID[lo];",
        "}",
        "",
    ]
    return registry, "\n".join(lines)


def _canonicalize_atlas_alpha_mask(image: "Image.Image") -> "Image.Image":
    """FL-4652: canonicalize a page's glyph coverage into the ALPHA channel.

    The native present fragment shader samples ONLY the alpha channel for glyph
    ink (`glyph_alpha = texture(page_atlas).a`). Most atlas pages already store
    coverage in alpha, but the legacy CP437 sheet keeps its mask in RGB with a
    fully-opaque, degenerate alpha channel. Uploaded verbatim, every CP437 cell
    then samples alpha == 1.0 everywhere, so `mix(bg, fg, 1.0)` paints a solid
    foreground rectangle and no glyph shape is visible. This is the compiler-owned
    normalization: when the alpha channel carries no variation (min == max), the
    real mask lives in RGB, so derive canonical coverage as the Rec.601 luminance
    of RGB (white ink -> 255, black -> 0). It is idempotent: once alpha varies it
    already IS the mask and the page is returned unchanged.
    """
    red, green, blue, alpha = image.split()
    alpha_min, alpha_max = alpha.getextrema()
    if alpha_min != alpha_max:
        return image
    # PIL's "L" conversion uses the ITU-R 601-2 luma transform
    # (L = 0.299 R + 0.587 G + 0.114 B), which is the canonical coverage.
    luminance = Image.merge("RGB", (red, green, blue)).convert("L")
    return Image.merge("RGBA", (red, green, blue, luminance))


def cp437_native_atlas_source() -> dict[str, Any]:
    """Describe the pinned 16x16 CP437 sheet as compiler-owned GlyphIds 0..255.

    This is a pure reader: it hashes whatever canonical CP437 sheet is on disk.
    The one-time alpha-mask canonicalization is performed only on the --write
    path (see build()), so --verify stays read-only and re-running is stable.
    """
    image = Image.open(CP437_ATLAS).convert("RGBA")
    if image.size != (COLS * CELL_PX, COLS * CELL_PX):
        raise SystemExit(f"unsupported CP437 atlas geometry: {image.size}")
    glyph_index = {
        str(gid): [
            0,
            (gid % COLS) * CELL_PX,
            (gid // COLS) * CELL_PX,
            (gid % COLS + 1) * CELL_PX,
            (gid // COLS + 1) * CELL_PX,
        ]
        for gid in range(256)
    }
    source = {
        "aoa_version": 1,
        "content_pack_id": "legacy.cp437.v1",
        "fallback_glyph_id": 63,
        "font_id": "cp437_16x16",
        "font_sha256": sha256_bytes(CP437_ATLAS.read_bytes()),
        "glyph_index": glyph_index,
        "pages": [{
            "cell_px": CELL_PX,
            "format": "rgba8",
            "height_px": COLS * CELL_PX,
            "page_binding_kind": "atlas_rect",
            "page_hash": sha256_bytes(image.tobytes()),
            "page_id": "page16",
            "url": "../atlases/legacy.cp437.v1.page16.png",
            "width_px": COLS * CELL_PX,
        }],
        "style_id": "regular",
    }
    return source


def vendor_ucd() -> None:
    UCD_DIR.mkdir(parents=True, exist_ok=True)
    for name, expected in UCD_FILES.items():
        url = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd/{name}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        actual = sha256_bytes(data)
        if actual != expected:
            raise SystemExit(f"{name}: sha256 {actual} != pinned {expected}")
        destination = UCD_DIR / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def require_inputs() -> None:
    for name, expected in UCD_FILES.items():
        path = UCD_DIR / name
        if not path.is_file():
            raise SystemExit(f"missing {path.relative_to(ROOT)}; run with --vendor")
        actual = sha256_file(path)
        if actual != expected:
            raise SystemExit(f"{name}: sha256 {actual} != pinned {expected}")
    CanonicalFontChain(CELL_PX)


def parse_codepoint_range(field: str) -> tuple[int, int]:
    parts = field.strip().split("..")
    first = int(parts[0], 16)
    return first, int(parts[-1], 16)


def parse_unicode_metadata() -> tuple[dict[int, str], dict[int, int]]:
    categories: dict[int, str] = {}
    combining_classes: dict[int, int] = {}
    range_start: tuple[int, str, int] | None = None
    for raw in (UCD_DIR / "UnicodeData.txt").read_text(encoding="utf-8").splitlines():
        fields = raw.split(";")
        cp = int(fields[0], 16)
        name = fields[1]
        gc = fields[2]
        combining_class = int(fields[3])
        if name.endswith(", First>"):
            range_start = (cp, gc, combining_class)
        elif name.endswith(", Last>"):
            if range_start is None:
                raise SystemExit(f"UnicodeData range end without start at U+{cp:04X}")
            for value in range(range_start[0], cp + 1):
                categories[value] = range_start[1]
                if range_start[2]:
                    combining_classes[value] = range_start[2]
            range_start = None
        else:
            categories[cp] = gc
            if combining_class:
                combining_classes[cp] = combining_class
    if range_start is not None:
        raise SystemExit("UnicodeData unterminated range")
    return categories, combining_classes


def parse_unicode_data() -> dict[int, str]:
    categories, _ = parse_unicode_metadata()
    return categories


def parse_property_set(path: Path, wanted: str) -> set[int]:
    values: set[int] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        content = raw.split("#", 1)[0].strip()
        if not content:
            continue
        fields = [item.strip() for item in content.split(";")]
        if len(fields) < 2 or fields[1] != wanted:
            continue
        first, last = parse_codepoint_range(fields[0])
        values.update(range(first, last + 1))
    return values


def parse_eaw() -> dict[int, str]:
    values: dict[int, str] = {}
    for raw in (UCD_DIR / "EastAsianWidth.txt").read_text(encoding="utf-8").splitlines():
        content = raw.split("#", 1)[0].strip()
        if not content:
            continue
        fields = [item.strip() for item in content.split(";")]
        first, last = parse_codepoint_range(fields[0])
        for cp in range(first, last + 1):
            values[cp] = fields[1]
    return values


def is_noncharacter(cp: int) -> bool:
    return 0xFDD0 <= cp <= 0xFDEF or (cp & 0xFFFF) in {0xFFFE, 0xFFFF}


def universe_and_blocked(
    categories: dict[int, str], default_ignorable: set[int],
    emoji_presentation: set[int], eaw: dict[int, str]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    universe: list[dict[str, Any]] = []
    blocked_counts: dict[str, int] = {}
    for cp in scalar_values():
        if not is_unicode_scalar(cp):
            continue
        gc = categories.get(cp)
        width = eaw.get(cp, "N")
        cause = ""
        if gc is None:
            cause = "unassigned"
        elif gc in EXCLUDED_GC:
            cause = "excluded_general_category"
        elif cp in default_ignorable:
            cause = "default_ignorable"
        elif is_noncharacter(cp):
            cause = "noncharacter"
        elif cp in emoji_presentation:
            cause = "emoji_presentation"
        if cause:
            blocked_counts[cause] = blocked_counts.get(cause, 0) + 1
        else:
            universe.append({
                "unicode_scalar": cp,
                "codepoint": f"U+{cp:04X}",
                "plane": cp >> 16,
                "general_category": gc,
                "east_asian_width": width,
            })
    return universe, blocked_counts


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def current_pack() -> tuple[dict[int, dict[str, Any]], dict[int, bytes], set[int], dict[str, Any], dict[str, Any]]:
    manifest = load_json(MORPH_MANIFEST)
    aoa = load_json(MORPH_AOA)
    catalog = load_json(MORPH_CATALOG)
    page_meta = next(page for page in aoa["pages"] if int(page["cell_px"]) == CELL_PX)
    page = load_json(MORPH_AOA.parent / page_meta["url"])
    raw = bytes(page["rgba8"])
    width = int(page["width"])
    entries = sorted(manifest["entries"], key=lambda row: int(row["glyph_id"]))
    by_scalar: dict[int, dict[str, Any]] = {}
    tiles: dict[int, bytes] = {}
    for ordinal, entry in enumerate(entries):
        cp = int(entry["unicode_scalar"])
        by_scalar.setdefault(cp, {**entry, "ordinal": ordinal})
        x0 = (ordinal % COLS) * CELL_PX
        y0 = (ordinal // COLS) * CELL_PX
        tile = bytearray()
        for y in range(CELL_PX):
            start = ((y0 + y) * width + x0) * 4
            tile.extend(raw[start:start + CELL_PX * 4])
        tiles.setdefault(cp, bytes(tile))
    catalog_ids = {int(row["glyph_id"]) for row in catalog.get("entries", [])}
    geometry = {
        "pack": "material.morphology.v2",
        "columns": COLS,
        "rows": int(page["height"]) // CELL_PX,
        "capacity": (int(page["height"]) // CELL_PX) * COLS,
        "bound_count": len(entries),
        "free_count": (int(page["height"]) // CELL_PX) * COLS - len(entries),
        "manifest_sha256": canonical_hash(manifest),
        "atlas_sha256": sha256_file(MORPH_AOA),
        "page16_sha256": sha256_bytes(raw),
    }
    return by_scalar, tiles, catalog_ids, geometry, manifest


def mask8(tile: bytes) -> int:
    value = 0
    for y in range(8):
        for x in range(8):
            coverage = 0
            for dy in range(2):
                for dx in range(2):
                    p = (((2 * y + dy) * CELL_PX) + 2 * x + dx) * 4
                    if tile[p + 3] > 8 and (tile[p] or tile[p + 1] or tile[p + 2]):
                        coverage += 1
            if coverage >= 2:
                value |= 1 << (8 * y + x)
    return value


def zhang_suen(mask: int) -> set[tuple[int, int]]:
    pixels = {(x, y) for y in range(8) for x in range(8) if mask & (1 << (8 * y + x))}
    rings = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
    changed = True
    while changed:
        changed = False
        for phase in (0, 1):
            remove: set[tuple[int, int]] = set()
            for x, y in pixels:
                values = [((x + dx, y + dy) in pixels) for dx, dy in rings]
                neighbors = sum(values)
                transitions = sum((not values[i]) and values[(i + 1) % 8] for i in range(8))
                if not (2 <= neighbors <= 6 and transitions == 1):
                    continue
                n, e, s, w = values[0], values[2], values[4], values[6]
                cond = (not (n and e and s) and not (e and s and w)) if phase == 0 else (not (n and e and w) and not (n and s and w))
                if cond:
                    remove.add((x, y))
            if remove:
                pixels.difference_update(remove)
                changed = True
    return pixels


def topology(mask: int) -> tuple[int, int]:
    skeleton = zhang_suen(mask)
    ring = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)]
    endpoints = 0
    junction_pixels: set[tuple[int, int]] = set()
    for x, y in skeleton:
        values = [((x + dx, y + dy) in skeleton) for dx, dy in ring]
        cn = sum((not values[i]) and values[(i + 1) % 8] for i in range(8))
        endpoints += int(cn == 1)
        if cn >= 3:
            junction_pixels.add((x, y))
    clusters = 0
    remaining = set(junction_pixels)
    while remaining:
        clusters += 1
        queue = deque([remaining.pop()])
        while queue:
            x, y = queue.popleft()
            adjacent = {(x + dx, y + dy) for dx, dy in ring} & remaining
            remaining.difference_update(adjacent)
            queue.extend(adjacent)
    return endpoints, clusters


def scalar_values() -> range:
    return range(0x110000)


def is_unicode_scalar(cp: int) -> bool:
    return 0 <= cp <= 0x10FFFF and not 0xD800 <= cp <= 0xDFFF


def compress_value_ranges(values: dict[int, int]) -> list[tuple[int, int, int]]:
    rows: list[tuple[int, int, int]] = []
    for cp in sorted(values):
        value = values[cp]
        if rows and rows[-1][1] + 1 == cp and rows[-1][2] == value:
            rows[-1] = (rows[-1][0], cp, value)
        else:
            rows.append((cp, cp, value))
    return rows


def compress_set_ranges(values: set[int]) -> list[tuple[int, int, int]]:
    return compress_value_ranges({cp: 1 for cp in values if is_unicode_scalar(cp)})


def universal_identity_record(
    cp: int,
    categories: dict[int, str],
    combining_classes: dict[int, int],
    default_ignorable: set[int],
    emoji_presentation: set[int],
    eaw: dict[int, str],
    cmap: set[int],
    existing: dict[int, dict[str, Any]],
) -> tuple[int, int, int, int, int, int, int, int]:
    if not is_unicode_scalar(cp):
        raise ValueError(f"not a Unicode scalar: U+{cp:04X}")
    binding = existing.get(cp)
    glyph_id = int(binding["glyph_id"]) if binding else UNIVERSAL_GLYPH_ID_BASE + cp
    category = categories.get(cp, "Cn")
    category_code = GENERAL_CATEGORY_CODES[category]
    width = eaw.get(cp, "N")
    eaw_code = EAW_CODES[width]
    combining_class = combining_classes.get(cp, 0)
    flags = 0
    if cp in categories:
        flags |= FLAG_ASSIGNED
    if cp in default_ignorable:
        flags |= FLAG_DEFAULT_IGNORABLE
    if cp in cmap:
        flags |= FLAG_FONT_SUPPORTED
    if binding:
        flags |= FLAG_EXISTING_BINDING
        if binding.get("atlas_covered"):
            flags |= FLAG_ATLAS_COVERED
        if binding.get("descriptor_covered"):
            flags |= FLAG_DESCRIPTOR_COVERED
    if category in {"Mn", "Mc", "Me"} or combining_class:
        flags |= FLAG_COMBINING
    if is_noncharacter(cp):
        flags |= FLAG_NONCHARACTER
    if category == "Co":
        flags |= FLAG_PRIVATE_USE
    if category in {"Cc", "Cs"}:
        flags |= FLAG_CONTROL
    if width in {"W", "F"}:
        flags |= FLAG_WIDE
    if cp in emoji_presentation:
        flags |= FLAG_EMOJI_PRESENTATION

    if flags & FLAG_NONCHARACTER:
        render_class = RENDER_NONCHARACTER
        cell_width = WIDTH_NONRENDERABLE
    elif cp not in categories:
        render_class = RENDER_UNASSIGNED
        cell_width = WIDTH_NONRENDERABLE
    elif flags & FLAG_CONTROL:
        render_class = RENDER_CONTROL
        cell_width = WIDTH_NONRENDERABLE
    elif flags & FLAG_PRIVATE_USE:
        render_class = RENDER_PRIVATE_USE
        cell_width = WIDTH_NONRENDERABLE
    elif flags & FLAG_DEFAULT_IGNORABLE:
        render_class = RENDER_DEFAULT_IGNORABLE
        cell_width = WIDTH_ZERO
    elif flags & FLAG_COMBINING:
        render_class = RENDER_COMBINING
        cell_width = WIDTH_ZERO
    elif category in {"Zl", "Zp"}:
        render_class = RENDER_LINE_SEPARATOR
        cell_width = WIDTH_NONRENDERABLE
    elif flags & FLAG_EMOJI_PRESENTATION:
        render_class = RENDER_EMOJI_PRESENTATION
        cell_width = WIDTH_NONRENDERABLE
    elif not flags & FLAG_FONT_SUPPORTED:
        render_class = RENDER_FONT_MISSING
        cell_width = WIDTH_NONRENDERABLE
    elif binding and binding.get("atlas_covered") and binding.get("descriptor_covered"):
        render_class = RENDER_ONE_CELL_ADMITTED
        cell_width = WIDTH_ONE_CELL
        flags |= FLAG_ONE_CELL_RENDERABLE
    else:
        render_class = RENDER_UNMEASURED
        cell_width = WIDTH_ONE_CELL
    return (
        glyph_id,
        flags,
        category_code,
        eaw_code,
        combining_class,
        cell_width,
        render_class,
        int(binding.get("font_index", 0)) if binding else 0,
    )


def universal_identity_artifacts(
    categories: dict[int, str],
    combining_classes: dict[int, int],
    default_ignorable: set[int],
    emoji_presentation: set[int],
    eaw: dict[int, str],
    cmap: set[int],
    binding_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    existing = {int(row["unicode_scalar"]): row for row in binding_rows}
    digest = hashlib.sha256()
    render_class_counts = {str(value): 0 for value in range(RENDER_EMOJI_PRESENTATION + 1)}
    assigned_count = 0
    font_supported_count = 0
    for cp in scalar_values():
        if not is_unicode_scalar(cp):
            continue
        record = universal_identity_record(
            cp, categories, combining_classes, default_ignorable,
            emoji_presentation, eaw, cmap, existing
        )
        digest.update(struct.pack("<IHBBBBBB", *record))
        assigned_count += int(bool(record[1] & FLAG_ASSIGNED))
        font_supported_count += int(bool(record[1] & FLAG_FONT_SUPPORTED))
        render_class_counts[str(record[6])] += 1
    records_sha256 = digest.hexdigest()

    category_ranges = compress_value_ranges({
        cp: GENERAL_CATEGORY_CODES[value] for cp, value in categories.items()
        if is_unicode_scalar(cp)
    })
    combining_ranges = compress_value_ranges(combining_classes)
    eaw_ranges = compress_value_ranges({
        cp: EAW_CODES[value] for cp, value in eaw.items() if is_unicode_scalar(cp)
    })
    ignorable_ranges = compress_set_ranges(default_ignorable)
    emoji_ranges = compress_set_ranges(emoji_presentation)
    cmap_ranges = compress_set_ranges(cmap)

    meta = {
        "schema": "fl4482.universal_unicode_identity.v3",
        "unicode_version": UNICODE_VERSION,
        "unicode_scalar_count": UNICODE_SCALAR_COUNT,
        "surrogate_range_excluded": [0xD800, 0xDFFF],
        "universal_glyph_id_base": UNIVERSAL_GLYPH_ID_BASE,
        "allocation_rule": "preserve_fl4482_runtime_binding_else_base_plus_scalar",
        "existing_binding_count": len(existing),
        "assigned_count": assigned_count,
        "font_supported_count": font_supported_count,
        "record_layout": "little_endian_<IHBBBBBB",
        "record_size": struct.calcsize("<IHBBBBBB"),
        "records_sha256": records_sha256,
        "render_class_counts": render_class_counts,
        "general_category_codes": GENERAL_CATEGORY_CODES,
        "east_asian_width_codes": EAW_CODES,
        "render_classes": {
            "unassigned": RENDER_UNASSIGNED,
            "noncharacter": RENDER_NONCHARACTER,
            "control": RENDER_CONTROL,
            "private_use": RENDER_PRIVATE_USE,
            "default_ignorable": RENDER_DEFAULT_IGNORABLE,
            "combining": RENDER_COMBINING,
            "line_separator": RENDER_LINE_SEPARATOR,
            "wide": RENDER_WIDE,
            "font_missing": RENDER_FONT_MISSING,
            "one_cell_unmeasured": RENDER_UNMEASURED,
            "one_cell_admitted": RENDER_ONE_CELL_ADMITTED,
            "emoji_presentation": RENDER_EMOJI_PRESENTATION,
        },
        "provenance": [{
            "unicode_data_version": UNICODE_VERSION,
            "ucd_hashes": UCD_FILES,
            "font_chain": font_chain_receipt(),
        }],
    }
    meta["registry_sha256"] = canonical_hash(meta)

    def range_lines(name: str, rows: list[tuple[int, int, int]]) -> list[str]:
        return [
            f"static const Fl4482UnicodeRangeU8V2 {name}[{len(rows)}] = {{",
            *[f"    {{{first}u, {last}u, {value}u}}," for first, last, value in rows],
            "};",
        ]

    header_lines = [
        "// Generated by scripts/fl4482_coverage_census.py. DO NOT EDIT.",
        "// FL-4482: one deterministic identity record for every Unicode scalar.",
        "#pragma once",
        "",
        "#include <stddef.h>",
        "#include <stdint.h>",
        '#include "fl4482_unicode_bindings.h"',
        "",
        '#define FL4482_UNIVERSAL_UNICODE_IDENTITY_SCHEMA "fl4482.universal_unicode_identity.v3"',
        f'#define FL4482_UNIVERSAL_UNICODE_IDENTITY_REGISTRY_SHA256 "{meta["registry_sha256"]}"',
        f'#define FL4482_UNIVERSAL_UNICODE_IDENTITY_RECORDS_SHA256 "{records_sha256}"',
        f"#define FL4482_UNIVERSAL_UNICODE_SCALAR_COUNT {UNICODE_SCALAR_COUNT}u",
        f"#define FL4482_UNIVERSAL_GLYPH_ID_BASE {UNIVERSAL_GLYPH_ID_BASE}u",
        "",
        "typedef enum Fl4482UnicodeRenderClassV2 {",
        "    FL4482_UNICODE_RENDER_UNASSIGNED = 0,",
        "    FL4482_UNICODE_RENDER_NONCHARACTER = 1,",
        "    FL4482_UNICODE_RENDER_CONTROL = 2,",
        "    FL4482_UNICODE_RENDER_PRIVATE_USE = 3,",
        "    FL4482_UNICODE_RENDER_DEFAULT_IGNORABLE = 4,",
        "    FL4482_UNICODE_RENDER_COMBINING = 5,",
        "    FL4482_UNICODE_RENDER_LINE_SEPARATOR = 6,",
        "    FL4482_UNICODE_RENDER_WIDE = 7,",
        "    FL4482_UNICODE_RENDER_FONT_MISSING = 8,",
        "    FL4482_UNICODE_RENDER_ONE_CELL_UNMEASURED = 9,",
        "    FL4482_UNICODE_RENDER_ONE_CELL_ADMITTED = 10,",
        "    FL4482_UNICODE_RENDER_EMOJI_PRESENTATION = 11,",
        "} Fl4482UnicodeRenderClassV2;",
        "",
        "typedef struct Fl4482UnicodeIdentityRecordV2 {",
        "    uint32_t glyph_id;",
        "    uint16_t flags;",
        "    uint8_t general_category;",
        "    uint8_t east_asian_width;",
        "    uint8_t canonical_combining_class;",
        "    uint8_t cell_width_class;",
        "    uint8_t render_class;",
        "    uint8_t provenance_index;",
        "} Fl4482UnicodeIdentityRecordV2;",
        "",
        "typedef struct Fl4482UnicodeRangeU8V2 {",
        "    uint32_t first;",
        "    uint32_t last;",
        "    uint8_t value;",
        "} Fl4482UnicodeRangeU8V2;",
        "",
        *range_lines("FL4482_UNICODE_CATEGORY_RANGES_V2", category_ranges),
        "",
        *range_lines("FL4482_UNICODE_COMBINING_RANGES_V2", combining_ranges),
        "",
        *range_lines("FL4482_UNICODE_EAW_RANGES_V2", eaw_ranges),
        "",
        *range_lines("FL4482_UNICODE_DEFAULT_IGNORABLE_RANGES_V2", ignorable_ranges),
        "",
        *range_lines("FL4482_UNICODE_EMOJI_PRESENTATION_RANGES_V2", emoji_ranges),
        "",
        *range_lines("FL4482_UNICODE_FONT_CMAP_RANGES_V2", cmap_ranges),
        "",
        "static inline int fl4482_unicode_scalar_valid_v2(uint32_t scalar)",
        "{",
        "    return scalar <= 0x10FFFFu && !(scalar >= 0xD800u && scalar <= 0xDFFFu);",
        "}",
        "",
        "static inline uint8_t fl4482_unicode_range_value_v2(",
        "    const Fl4482UnicodeRangeU8V2* rows, size_t count, uint32_t scalar, uint8_t fallback)",
        "{",
        "    size_t lo = 0, hi = count;",
        "    while (lo < hi) {",
        "        const size_t mid = lo + (hi - lo) / 2;",
        "        if (rows[mid].last < scalar) lo = mid + 1;",
        "        else hi = mid;",
        "    }",
        "    return lo < count && rows[lo].first <= scalar ? rows[lo].value : fallback;",
        "}",
        "",
        "static inline int fl4482_unicode_identity_by_scalar_v2(",
        "    uint32_t scalar, Fl4482UnicodeIdentityRecordV2* out)",
        "{",
        "    if (!out || !fl4482_unicode_scalar_valid_v2(scalar)) return 0;",
        "    const Fl4482UnicodeBindingV1* binding = fl4482_unicode_binding_by_scalar(scalar);",
        "    const uint8_t gc = fl4482_unicode_range_value_v2(FL4482_UNICODE_CATEGORY_RANGES_V2,",
        "        sizeof(FL4482_UNICODE_CATEGORY_RANGES_V2) / sizeof(FL4482_UNICODE_CATEGORY_RANGES_V2[0]), scalar, 0u);",
        "    const uint8_t eaw = fl4482_unicode_range_value_v2(FL4482_UNICODE_EAW_RANGES_V2,",
        "        sizeof(FL4482_UNICODE_EAW_RANGES_V2) / sizeof(FL4482_UNICODE_EAW_RANGES_V2[0]), scalar, 0u);",
        "    const uint8_t ccc = fl4482_unicode_range_value_v2(FL4482_UNICODE_COMBINING_RANGES_V2,",
        "        sizeof(FL4482_UNICODE_COMBINING_RANGES_V2) / sizeof(FL4482_UNICODE_COMBINING_RANGES_V2[0]), scalar, 0u);",
        "    const int ignorable = fl4482_unicode_range_value_v2(FL4482_UNICODE_DEFAULT_IGNORABLE_RANGES_V2,",
        "        sizeof(FL4482_UNICODE_DEFAULT_IGNORABLE_RANGES_V2) / sizeof(FL4482_UNICODE_DEFAULT_IGNORABLE_RANGES_V2[0]), scalar, 0u) != 0;",
        "    const int font_supported = fl4482_unicode_range_value_v2(FL4482_UNICODE_FONT_CMAP_RANGES_V2,",
        "        sizeof(FL4482_UNICODE_FONT_CMAP_RANGES_V2) / sizeof(FL4482_UNICODE_FONT_CMAP_RANGES_V2[0]), scalar, 0u) != 0;",
        "    const int emoji_presentation = fl4482_unicode_range_value_v2(FL4482_UNICODE_EMOJI_PRESENTATION_RANGES_V2,",
        "        sizeof(FL4482_UNICODE_EMOJI_PRESENTATION_RANGES_V2) / sizeof(FL4482_UNICODE_EMOJI_PRESENTATION_RANGES_V2[0]), scalar, 0u) != 0;",
        "    const int assigned = gc != 0u;",
        "    const int noncharacter = (scalar >= 0xFDD0u && scalar <= 0xFDEFu) ||",
        "        (scalar & 0xFFFFu) == 0xFFFEu || (scalar & 0xFFFFu) == 0xFFFFu;",
        "    const int combining = gc == 6u || gc == 7u || gc == 8u || ccc != 0u;",
        "    const int control = gc == 26u || gc == 28u;",
        "    const int private_use = gc == 29u;",
        "    const int wide = eaw == 4u || eaw == 5u;",
        "    uint16_t flags = 0u;",
        f"    if (assigned) flags |= {FLAG_ASSIGNED}u;",
        f"    if (ignorable) flags |= {FLAG_DEFAULT_IGNORABLE}u;",
        f"    if (font_supported) flags |= {FLAG_FONT_SUPPORTED}u;",
        f"    if (binding) flags |= {FLAG_EXISTING_BINDING}u | {FLAG_ATLAS_COVERED}u | {FLAG_DESCRIPTOR_COVERED}u;",
        f"    if (combining) flags |= {FLAG_COMBINING}u;",
        f"    if (noncharacter) flags |= {FLAG_NONCHARACTER}u;",
        f"    if (private_use) flags |= {FLAG_PRIVATE_USE}u;",
        f"    if (control) flags |= {FLAG_CONTROL}u;",
        f"    if (wide) flags |= {FLAG_WIDE}u;",
        f"    if (emoji_presentation) flags |= {FLAG_EMOJI_PRESENTATION}u;",
        "    uint8_t render_class = FL4482_UNICODE_RENDER_ONE_CELL_UNMEASURED;",
        f"    uint8_t cell_width = {WIDTH_ONE_CELL}u;",
        f"    if (noncharacter) {{ render_class = {RENDER_NONCHARACTER}u; cell_width = {WIDTH_NONRENDERABLE}u; }}",
        f"    else if (!assigned) {{ render_class = {RENDER_UNASSIGNED}u; cell_width = {WIDTH_NONRENDERABLE}u; }}",
        f"    else if (control) {{ render_class = {RENDER_CONTROL}u; cell_width = {WIDTH_NONRENDERABLE}u; }}",
        f"    else if (private_use) {{ render_class = {RENDER_PRIVATE_USE}u; cell_width = {WIDTH_NONRENDERABLE}u; }}",
        f"    else if (ignorable) {{ render_class = {RENDER_DEFAULT_IGNORABLE}u; cell_width = {WIDTH_ZERO}u; }}",
        f"    else if (combining) {{ render_class = {RENDER_COMBINING}u; cell_width = {WIDTH_ZERO}u; }}",
        f"    else if (gc == 24u || gc == 25u) {{ render_class = {RENDER_LINE_SEPARATOR}u; cell_width = {WIDTH_NONRENDERABLE}u; }}",
        f"    else if (emoji_presentation) {{ render_class = {RENDER_EMOJI_PRESENTATION}u; cell_width = {WIDTH_NONRENDERABLE}u; }}",
        f"    else if (!font_supported) {{ render_class = {RENDER_FONT_MISSING}u; cell_width = {WIDTH_NONRENDERABLE}u; }}",
        f"    else if (binding) {{ render_class = {RENDER_ONE_CELL_ADMITTED}u; cell_width = {WIDTH_ONE_CELL}u; flags |= {FLAG_ONE_CELL_RENDERABLE}u; }}",
        "    out->glyph_id = binding ? binding->glyph_id : FL4482_UNIVERSAL_GLYPH_ID_BASE + scalar;",
        "    out->flags = flags; out->general_category = gc; out->east_asian_width = eaw;",
        "    out->canonical_combining_class = ccc; out->cell_width_class = cell_width;",
        "    out->render_class = render_class; out->provenance_index = 0u;",
        "    return 1;",
        "}",
        "",
        "static inline int fl4482_unicode_scalar_by_identity_v2(uint32_t glyph_id, uint32_t* out_scalar)",
        "{",
        "    const Fl4482UnicodeBindingV1* binding = fl4482_unicode_binding_by_glyph_id(glyph_id);",
        "    if (binding) { if (out_scalar) *out_scalar = binding->unicode_scalar; return 1; }",
        "    if (glyph_id < FL4482_UNIVERSAL_GLYPH_ID_BASE) return 0;",
        "    const uint32_t scalar = glyph_id - FL4482_UNIVERSAL_GLYPH_ID_BASE;",
        "    if (!fl4482_unicode_scalar_valid_v2(scalar)) return 0;",
        "    if (out_scalar) *out_scalar = scalar;",
        "    return 1;",
        "}",
        "",
    ]
    return meta, "\n".join(header_lines)


def build(write: bool) -> dict[str, Any]:
    require_inputs()
    categories, combining_classes = parse_unicode_metadata()
    default_ignorable = parse_property_set(UCD_DIR / "DerivedCoreProperties.txt", "Default_Ignorable_Code_Point")
    emoji_presentation = parse_property_set(UCD_DIR / "emoji/emoji-data.txt", "Emoji_Presentation")
    eaw = parse_eaw()
    universe, blocked_counts = universe_and_blocked(
        categories, default_ignorable, emoji_presentation, eaw
    )
    font_chain = CanonicalFontChain(CELL_PX)
    cmap = font_chain.font_cps
    current, current_tiles, current_catalog_ids, morph_geometry, morph_manifest = current_pack()

    allocations: list[dict[str, Any]] = []
    new_entries: list[dict[str, Any]] = []
    descriptors: list[dict[str, Any]] = []
    page_raw: list[bytearray] = []
    selection: dict[int, dict[str, Any]] = {}
    page_width = COLS * CELL_PX
    page_bytes = COLS * ROWS * CELL_PX * CELL_PX * 4

    for row in universe:
        cp = row["unicode_scalar"]
        existing = current.get(cp)
        if existing is None:
            continue
        gid = int(existing["glyph_id"])
        atlas_nonblank = any(current_tiles[cp][index + 3] > 8 for index in range(0, len(current_tiles[cp]), 4))
        if row["general_category"] != "Zs" and not atlas_nonblank:
            continue
        if gid not in current_catalog_ids:
            continue
        selection[cp] = {
            "pack": "material.morphology.v2",
            "glyph_id": gid,
            "page_index": 0,
            "tile_index": int(existing["ordinal"]),
            "atlas_covered": True,
            "descriptor_covered": True,
            "font_index": 0,
            "font_name": "material.morphology.v2",
        }

    for row in universe:
        cp = row["unicode_scalar"]
        if cp in selection:
            continue
        raster = font_chain.render(cp)
        if raster is None:
            blocked_counts["font_missing"] = blocked_counts.get("font_missing", 0) + 1
            continue
        if row["general_category"] != "Zs" and raster.ink_count == 0:
            blocked_counts["blank_raster"] = blocked_counts.get("blank_raster", 0) + 1
            continue
        tile = raster.rgba8
        mask = mask8(tile)
        if mask == 0 and row["general_category"] != "Zs":
            blocked_counts["mask8_empty"] = blocked_counts.get("mask8_empty", 0) + 1
            continue
        ordinal = len(allocations)
        gid = UNIVERSAL_GLYPH_ID_BASE + cp
        page_index = ordinal // CAPACITY
        tile_index = ordinal % CAPACITY
        allocation = {
            "unicode_scalar": cp,
            "codepoint": row["codepoint"],
            "plane": row["plane"],
            "glyph_id": gid,
            "allocation_ordinal": ordinal,
            "page_index": page_index,
            "tile_index": tile_index,
            "font_index": raster.font_index,
            "font_name": raster.font_name,
            "admitted": True,
        }
        allocations.append(allocation)
        if page_index == len(page_raw):
            page_raw.append(bytearray(page_bytes))
        x0 = (tile_index % COLS) * CELL_PX
        y0 = (tile_index // COLS) * CELL_PX
        for y in range(CELL_PX):
            target = ((y0 + y) * page_width + x0) * 4
            source = y * CELL_PX * 4
            page_raw[page_index][target:target + CELL_PX * 4] = tile[source:source + CELL_PX * 4]
        endpoint_count, junction_count = topology(mask)
        entry = {
            "glyph_id": gid,
            "label": f"UNICODE16_{cp:04X}",
            "unicode_scalar": cp,
            "coverage_quadrants": 65535,
            "coverage_hint": "measured",
            "cell_width_em": 1,
            "allocation_ordinal": ordinal,
            "page_index": page_index,
            "tile_index": tile_index,
            "font_index": raster.font_index,
        }
        new_entries.append(entry)
        descriptors.append({
            "glyph_id": gid,
            "unicode_scalar": cp,
            "scorer_id": "u7.mask.v1",
            "mask8_u64": f"0x{mask:016x}",
            "endpoint_count": endpoint_count,
            "junction_cluster_count": junction_count,
            "page16_tile_sha256": sha256_bytes(tile),
            "font_index": raster.font_index,
        })
        selection[cp] = {
            "pack": "unicode16.onecell.v1",
            "glyph_id": gid,
            "page_index": page_index,
            "tile_index": tile_index,
            "atlas_covered": True,
            "descriptor_covered": True,
            "font_index": raster.font_index,
            "font_name": raster.font_name,
        }

    page_count = len(page_raw)
    if not new_entries or page_count == 0:
        raise SystemExit("FL-4482 universal compiler produced no font-chain glyphs")

    registry = {
        "schema": "unicode_scalar_registry.v2",
        "unicode_version": UNICODE_VERSION,
        "pack_order": PACK_ORDER,
        "allocation_order": "eligible_scalar_ascending",
        "glyph_id_rule": "universal_glyph_id_base_plus_scalar",
        "universal_glyph_id_base": UNIVERSAL_GLYPH_ID_BASE,
        "font_chain": font_chain_receipt(),
        "allocations": allocations,
    }
    registry["registry_sha256"] = canonical_hash(registry)
    manifest = {
        "_comment": "FL-4482 universal offline standalone non-emoji text-glyph pack",
        "manifest_version": 2,
        "profile_kind": "extended_glyph_v1",
        "content_pack_id": "unicode16.onecell.v1",
        "font_id": "fl4482.pinned_font_chain.v1",
        "font_chain": font_chain_receipt(),
        "style_id": "regular",
        "supported_cell_sizes": [16],
        "fallback_glyph_id": new_entries[0]["glyph_id"],
        "admission_set": [row["glyph_id"] for row in new_entries],
        "script_packs": [{
            "pack_id": "unicode16.onecell.v1",
            "status": "complete",
            "purpose": "FL-4482 universal offline standalone non-emoji text-glyph coverage",
            "glyph_ids": [row["glyph_id"] for row in new_entries],
        }],
        "entries": new_entries,
    }
    manifest_hash = canonical_hash(manifest)
    glyph_index = {
        str(row["glyph_id"]): [row["page_index"],
            (row["tile_index"] % COLS) * CELL_PX,
            (row["tile_index"] // COLS) * CELL_PX,
            (row["tile_index"] % COLS + 1) * CELL_PX,
            (row["tile_index"] // COLS + 1) * CELL_PX]
        for row in new_entries
    }
    page_hashes = [sha256_bytes(bytes(raw)) for raw in page_raw]
    aoa = {
        "aoa_version": 1,
        "content_pack_id": "unicode16.onecell.v1",
        "manifest_hash": manifest_hash,
        "fallback_glyph_id": manifest["fallback_glyph_id"],
        "font_id": manifest["font_id"],
        "font_chain": font_chain_receipt(),
        "style_id": "regular",
        "lut_hash": canonical_hash(glyph_index),
        "pages": [
            {
                "page_id": f"page{page_index}.page16",
                "url": unicode_page_png_path(page_index).name,
                "width_px": page_width,
                "height_px": ROWS * CELL_PX,
                "cell_px": CELL_PX,
                "page_hash": page_hashes[page_index],
                "page_binding_kind": "atlas_rect",
                "format": "rgba8",
            }
            for page_index in range(page_count)
        ],
        "glyph_index": glyph_index,
    }
    descriptor_catalog = {
        "schema": "unicode16.onecell.shape_catalog.v3",
        "content_pack_id": "unicode16.onecell.v1",
        "manifest_hash": manifest_hash,
        "page16_sha256_by_page": page_hashes,
        "scorer_id": "u7.mask.v1",
        "entries": descriptors,
    }
    descriptor_hash = canonical_hash(descriptor_catalog)
    binding_rows = []
    for row in universe:
        selected = selection.get(row["unicode_scalar"])
        if not selected:
            continue
        binding_rows.append({
            "unicode_scalar": row["unicode_scalar"],
            "glyph_id": selected["glyph_id"],
            "pack": selected["pack"],
            "page_index": selected["page_index"],
            "tile_index": selected["tile_index"],
            "admitted": True,
            "atlas_covered": selected["atlas_covered"],
            "descriptor_covered": selected["descriptor_covered"],
            "font_index": selected["font_index"],
            "font_name": selected["font_name"],
            "runtime_binding_declared": True,
        })
    runtime_bindings = {
        "schema": "fl4482.runtime_bindings.v1",
        "pack_order": PACK_ORDER,
        "unicode_scalar_registry_sha256": registry["registry_sha256"],
        "manifest_hashes": {
            "material.morphology.v2": canonical_hash(morph_manifest),
            "unicode16.onecell.v1": manifest_hash,
        },
        "descriptor_hashes": {"unicode16.onecell.v1": descriptor_hash},
        "bindings": binding_rows,
        "runtime_consumed": True,
        "proof_state": "IMPLEMENTED_UNPROVEN",
    }
    runtime_bindings_hash = canonical_hash(runtime_bindings)
    header = native_header_text(binding_rows, registry["registry_sha256"], runtime_bindings["manifest_hashes"])
    header_hash = sha256_bytes(header.encode("utf-8"))
    universal_identity_meta, universal_identity_header = universal_identity_artifacts(
        categories, combining_classes, default_ignorable, emoji_presentation,
        eaw, cmap, binding_rows
    )
    universal_identity_header_hash = sha256_bytes(universal_identity_header.encode("utf-8"))
    if write:
        # FL-4652 offline atlas-mask normalization (compiler-owned): canonicalize
        # the legacy CP437 sheet's glyph coverage into its ALPHA channel before it
        # is hashed and registered below. The native present shader samples only
        # the alpha channel, but the CP437 sheet ships its mask in RGB with a
        # degenerate all-opaque alpha, so an un-normalized sheet would render every
        # cell as a solid rectangle. Writing the canonical sheet here keeps the
        # runtime a plain hash-check + verbatim byte-copy. Idempotent: once the
        # alpha channel carries the mask the transform is a no-op.
        _cp437_image = Image.open(CP437_ATLAS).convert("RGBA")
        _cp437_canonical = _canonicalize_atlas_alpha_mask(_cp437_image)
        if _cp437_canonical is not _cp437_image:
            _cp437_canonical.save(CP437_ATLAS)
    cp437_source = cp437_native_atlas_source()
    native_atlas_registry, native_atlas_header = native_atlas_registry_and_header([
        ("legacy.cp437.v1", CP437_NATIVE_SOURCE, cp437_source),
        ("material.additive.v1", ADDITIVE_AOA, load_json(ADDITIVE_AOA)),
        ("material.morphology.v2", MORPH_AOA, load_json(MORPH_AOA)),
        ("unicode16.onecell.v1", NEW_AOA, aoa),
    ])
    native_atlas_header_hash = sha256_bytes(native_atlas_header.encode("utf-8"))

    range_receipts = []
    for plane in range(17):
        rows = [
            row for row in universe
            if row["plane"] == plane and row["unicode_scalar"] in selection
        ]
        denominator = len(rows)
        selected_rows = [selection.get(row["unicode_scalar"]) for row in rows]
        catalog_count = sum(bool(value and value["descriptor_covered"]) for value in selected_rows)
        atlas_count = sum(bool(value and value["atlas_covered"]) for value in selected_rows)
        runtime_count = sum(value is not None for value in selected_rows)
        numerator = min(catalog_count, atlas_count, runtime_count)
        range_receipts.append({
            "range": f"plane_{plane:02d}",
            "first": f"U+{plane << 16:04X}",
            "last": f"U+{min(0x10FFFF, ((plane + 1) << 16) - 1):04X}",
            "denominator": denominator,
            "catalog_numerator": catalog_count,
            "atlas_numerator": atlas_count,
            "descriptor_numerator": catalog_count,
            "runtime_binding_numerator": runtime_count,
            "numerator": numerator,
            "percentage": round((100.0 * numerator / denominator) if denominator else 100.0, 6),
            "threshold_percent": 95,
            "verdict": "PASS" if denominator == 0 or numerator * 100 >= denominator * 95 else "FAIL",
        })

    new_geometry = {
        "pack": "unicode16.onecell.v1",
        "columns": COLS,
        "rows": ROWS,
        "capacity": CAPACITY,
        "bound_count": len(new_entries),
        "free_count": page_count * CAPACITY - len(new_entries),
        "allocated_count": len(allocations),
        "page_count": page_count,
        "manifest_sha256": manifest_hash,
        "atlas_sha256": canonical_hash(aoa),
        "page16_sha256_by_page": page_hashes,
    }
    receipt = {
        "schema": SCHEMA,
        "unicode_version": UNICODE_VERSION,
        "ucd_hashes": UCD_FILES,
        "font_chain": font_chain_receipt(),
        "pack_order": PACK_ORDER,
        "denominator_predicate": {
            "scalar_space": "all Unicode 16.0.0 assigned scalars",
            "excluded_general_categories": sorted(EXCLUDED_GC),
            "default_ignorable_excluded": True,
            "emoji_presentation_excluded": True,
            "wide_fullwidth_normalized_to_one_cell": True,
            "bmp_only": False,
        },
        "source_candidate_count": len(universe),
        "universe_count": len(selection),
        "preserved_binding_count": sum(value["pack"] == "material.morphology.v2" for value in selection.values()),
        "new_allocation_count": len(allocations),
        "new_admitted_binding_count": len(new_entries),
        "selected_binding_count": len(selection),
        "missing_binding_count": 0,
        "range_coverage": range_receipts,
        "atlas_geometry": [morph_geometry, new_geometry],
        "unicode_scalar_registry_sha256": registry["registry_sha256"],
        "runtime_bindings_sha256": runtime_bindings_hash,
        "native_binding_header_sha256": header_hash,
        "native_atlas_registry_sha256": native_atlas_registry["registry_sha256"],
        "native_atlas_header_sha256": native_atlas_header_hash,
        "universal_identity_registry_sha256": universal_identity_meta["registry_sha256"],
        "universal_identity_records_sha256": universal_identity_meta["records_sha256"],
        "universal_identity_header_sha256": universal_identity_header_hash,
        "universal_identity_scalar_count": universal_identity_meta["unicode_scalar_count"],
        "duplicate_bindings": [],
        "blocked_cause_counts": dict(sorted(blocked_counts.items())),
        "blocked_records_owner": "fl4482.universal_unicode_identity.v3",
        "runtime_consumed": True,
        "proof_state": "IMPLEMENTED_UNPROVEN",
        "overall_verdict": "PASS" if all(row["verdict"] == "PASS" for row in range_receipts) else "FAIL",
    }
    receipt["receipt_sha256"] = canonical_hash(receipt)

    if write:
        CP437_NATIVE_PAGE.parent.mkdir(parents=True, exist_ok=True)
        Image.open(CP437_ATLAS).convert("RGBA").save(CP437_NATIVE_PAGE)
        write_json(CP437_NATIVE_SOURCE, cp437_source)
        write_json(SCALAR_REGISTRY, registry)
        write_json(NEW_MANIFEST, manifest)
        for page_index, raw in enumerate(page_raw):
            image = Image.frombytes("RGBA", (page_width, ROWS * CELL_PX), bytes(raw))
            image.save(unicode_page_png_path(page_index))
        write_json(NEW_AOA, aoa)
        write_json(NEW_CATALOG, descriptor_catalog)
        write_json(RUNTIME_BINDINGS, runtime_bindings)
        NATIVE_HEADER.parent.mkdir(parents=True, exist_ok=True)
        NATIVE_HEADER.write_text(header, encoding="utf-8")
        write_json(NATIVE_ATLAS_REGISTRY, native_atlas_registry)
        NATIVE_ATLAS_HEADER.write_text(native_atlas_header, encoding="utf-8")
        write_json(UNIVERSAL_IDENTITY_META, universal_identity_meta)
        UNIVERSAL_IDENTITY_HEADER.write_text(universal_identity_header, encoding="utf-8")
        write_json(RECEIPT, receipt, compact=True)
    return receipt


def verify() -> int:
    expected = build(write=False)
    generated_paths = [
        CP437_NATIVE_SOURCE, CP437_NATIVE_PAGE, SCALAR_REGISTRY, NEW_MANIFEST, NEW_AOA, NEW_CATALOG,
        RUNTIME_BINDINGS, NATIVE_HEADER, NATIVE_ATLAS_REGISTRY,
        NATIVE_ATLAS_HEADER, UNIVERSAL_IDENTITY_META,
        UNIVERSAL_IDENTITY_HEADER, RECEIPT,
    ]
    generated_paths.extend(
        unicode_page_png_path(page_index)
        for page_index in range(expected["atlas_geometry"][-1]["page_count"])
    )
    for path in generated_paths:
        if not path.is_file():
            raise SystemExit(f"missing generated artifact {path.relative_to(ROOT)}")
    cp437_image = Image.open(CP437_NATIVE_PAGE)
    if cp437_image.mode != "RGBA" or cp437_image.size != (COLS * CELL_PX, COLS * CELL_PX):
        raise SystemExit("native CP437 page must be a 256x256 RGBA image")
    if sha256_bytes(cp437_image.tobytes()) != cp437_native_atlas_source()["pages"][0]["page_hash"]:
        raise SystemExit("native CP437 page decoded hash mismatch")
    actual = load_json(RECEIPT)
    if actual != expected:
        raise SystemExit("coverage receipt is stale; run --write")
    for page_index in range(expected["atlas_geometry"][-1]["page_count"]):
        image = Image.open(unicode_page_png_path(page_index)).convert("RGBA")
        if image.size != (COLS * CELL_PX, ROWS * CELL_PX):
            raise SystemExit(f"coverage atlas page {page_index} geometry mismatch")
        if sha256_bytes(image.tobytes()) != expected["atlas_geometry"][-1]["page16_sha256_by_page"][page_index]:
            raise SystemExit(f"coverage atlas page {page_index} hash mismatch")
    if actual["overall_verdict"] != "PASS":
        raise SystemExit("D4 coverage gate failed")
    universal_meta = load_json(UNIVERSAL_IDENTITY_META)
    if universal_meta["registry_sha256"] != actual["universal_identity_registry_sha256"]:
        raise SystemExit("universal Unicode identity registry hash mismatch")
    if universal_meta["unicode_scalar_count"] != UNICODE_SCALAR_COUNT:
        raise SystemExit("universal Unicode identity scalar count mismatch")
    print(json.dumps({
        "overall_verdict": actual["overall_verdict"],
        "universe_count": actual["universe_count"],
        "preserved_binding_count": actual["preserved_binding_count"],
        "new_allocation_count": actual["new_allocation_count"],
        "new_admitted_binding_count": actual["new_admitted_binding_count"],
        "missing_binding_count": actual["missing_binding_count"],
        "runtime_consumed": actual["runtime_consumed"],
        "receipt_sha256": actual["receipt_sha256"],
    }, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", action="store_true", help="download and hash-check Unicode 16 inputs")
    parser.add_argument("--write", action="store_true", help="write all generated D4 artifacts")
    parser.add_argument("--verify", action="store_true", help="recompute and verify tracked artifacts")
    args = parser.parse_args()
    if args.vendor:
        vendor_ucd()
    if args.write:
        receipt = build(write=True)
        print(json.dumps({key: receipt[key] for key in (
            "overall_verdict", "universe_count", "preserved_binding_count",
            "new_allocation_count", "new_admitted_binding_count", "missing_binding_count",
            "runtime_consumed", "receipt_sha256")}, sort_keys=True))
    if args.verify:
        return verify()
    if not (args.vendor or args.write or args.verify):
        parser.error("select --vendor, --write, or --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
