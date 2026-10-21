from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/fl4482_coverage_census.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fl4482_coverage_census", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ratified_constants_and_inputs() -> None:
    module = load_module()
    assert module.PACK_ORDER == ["material.morphology.v2", "unicode16.onecell.v1"]
    assert module.CAPACITY == 1216
    assert module.UNIVERSAL_GLYPH_ID_BASE == 0x01000000
    assert len(module.PINNED_FONT_CHAIN) == 8
    for name, digest in module.UCD_FILES.items():
        assert module.sha256_file(module.UCD_DIR / name) == digest


def test_registry_is_immutable_sequential_allocation() -> None:
    registry = json.loads((ROOT / "assets/glyphs/generated/unicode16.onecell.v1.scalar_registry.json").read_text())
    allocations = registry["allocations"]
    assert [row["allocation_ordinal"] for row in allocations] == list(range(len(allocations)))
    assert [row["glyph_id"] for row in allocations] == [
        0x01000000 + row["unicode_scalar"] for row in allocations
    ]
    assert [row["unicode_scalar"] for row in allocations] == sorted(
        row["unicode_scalar"] for row in allocations
    )
    assert all(row["page_index"] == row["allocation_ordinal"] // 1216 for row in allocations)
    assert all(row["tile_index"] == row["allocation_ordinal"] % 1216 for row in allocations)

def test_receipt_and_binding_contract() -> None:
    receipt = json.loads((ROOT / "docs/research/ascii/verification/fl4482/coverage_receipt.json").read_text())
    bindings = json.loads((ROOT / "assets/glyphs/generated/unicode16.onecell.v1.runtime_bindings.json").read_text())
    assert receipt["schema"] == "fl4482.unicode_coverage.v2"
    assert receipt["overall_verdict"] == "PASS"
    assert receipt["missing_binding_count"] == 0
    assert all(row["verdict"] == "PASS" for row in receipt["range_coverage"])
    assert receipt["selected_binding_count"] == receipt["universe_count"]
    assert len(bindings["bindings"]) == receipt["universe_count"]
    assert all(row["runtime_binding_declared"] for row in bindings["bindings"])
    assert bindings["runtime_consumed"] is True
    assert receipt["denominator_predicate"]["bmp_only"] is False
    assert receipt["denominator_predicate"]["emoji_presentation_excluded"] is True
    assert len(receipt["font_chain"]) == 8


def test_generated_pack_has_exact_descriptor_and_page_joins() -> None:
    manifest = json.loads((ROOT / "assets/glyphs/fixtures/extended_glyph_unicode16_onecell_v1.json").read_text())
    catalog = json.loads((ROOT / "assets/glyphs/generated/unicode16.onecell.v1.shape_catalog.json").read_text())
    aoa = json.loads((ROOT / "assets/glyphs/atlases/unicode16.onecell.v1.atlas_of_atlases.json").read_text())
    admitted = set(manifest["admission_set"])
    assert admitted == {row["glyph_id"] for row in manifest["entries"]}
    assert admitted == {row["glyph_id"] for row in catalog["entries"]}
    assert admitted == {int(gid) for gid in aoa["glyph_index"]}
    assert all(row["scorer_id"] == "u7.mask.v1" for row in catalog["entries"])
    assert all(row["mask8_u64"].startswith("0x") for row in catalog["entries"])


def test_native_registry_assigns_one_physical_layer_per_page16_page() -> None:
    module = load_module()
    first_path = ROOT / "assets/glyphs/atlases/test.first.aoa.json"
    second_path = ROOT / "assets/glyphs/atlases/test.second.aoa.json"
    first = {
        "pages": [
            {"cell_px": 8, "width_px": 128, "height_px": 8, "url": "ignored.json", "page_hash": "0" * 64},
            {"cell_px": 16, "width_px": 256, "height_px": 16, "url": "first-p1.json", "page_hash": "1" * 64},
        ],
        "glyph_index": {"10": [0, 0, 0, 16, 16]},
    }
    second = {
        "pages": [
            {"cell_px": 16, "width_px": 256, "height_px": 16, "url": "second-p0.json", "page_hash": "2" * 64},
            {"cell_px": 16, "width_px": 256, "height_px": 16, "url": "second-p1.json", "page_hash": "3" * 64},
        ],
        "glyph_index": {
            "20": [0, 0, 0, 16, 16],
            "21": [1, 16, 0, 32, 16],
        },
    }
    registry, header = module.native_atlas_registry_and_header(
        [("first", first_path, first), ("second", second_path, second)]
    )
    assert [(page["pack_index"], page["page_index"], page["layer_index"]) for page in registry["pages"]] == [
        (0, 0, 0),
        (1, 0, 1),
        (1, 1, 2),
    ]
    assert [(row["glyph_id"], row["layer_index"], row["page_index"]) for row in registry["bindings"]] == [
        (10, 0, 0),
        (20, 1, 0),
        (21, 2, 1),
    ]
    assert "#define FL4482_NATIVE_ATLAS_PAGE_COUNT 3u" in header
