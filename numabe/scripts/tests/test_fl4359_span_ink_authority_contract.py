#!/usr/bin/env python3
"""FL-4359 / FL-4848 presentation ABI and allocation contracts.

Both projections publish one NativePresentationResult per cell through the
shared authority; allocation is cell-local (span 1) everywhere. The behaviour
is proven by test_fl4359_cell_presentation_gpu.gd; this file pins the ABI.
"""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHADERS = ROOT / "godot_project"

def test_authority_record_names_cell_and_color_authority():
    source = (SHADERS / "render/ascii/authority/native_presentation_authority.glslinc").read_text()
    assert "NATIVE_PRESENTATION_AUTHORITY_ABI = 5u" in source
    assert "NATIVE_PRESENTATION_AUTHORITY_BYTES = 64u" in source
    for field in ("uvec4 allocation;", "uvec4 channels;", "uvec4 authority;", "uvec4 paint;"):
        assert field in source
    assert "&& record.allocation.y == 1u;" in source
    for status in ("NATIVE_SPAN_SOURCE_VALID", "NATIVE_SPAN_GENERATION_VALID",
                   "NATIVE_SPAN_GENERATION_INAPPLICABLE",
                   "NATIVE_SPAN_REFUSAL_SOURCE", "NATIVE_SPAN_REFUSAL_GENERATION",
                   "NATIVE_SPAN_REFUSAL_GLYPH", "NATIVE_SPAN_REFUSAL_AMBIGUOUS",
                   "NATIVE_SPAN_REFUSAL_BLANK", "NATIVE_SPAN_REFUSAL_INCOMPLETE",
                   "NATIVE_SPAN_REFUSAL_PAPER"):
        assert status in source

def test_one_election_law_is_shared_by_both_projections():
    authority = (SHADERS / "render/ascii/authority/native_presentation_authority.glslinc").read_text()
    for law in ("CellPresentationCandidate cell_presentation_merge(",
                "CellPresentationCandidate cell_presentation_paper_merge(",
                "NativePresentationResult cell_presentation_result("):
        assert law in authority
    publish = (SHADERS / "render/backends/perspective/publish/perspective_probe_payload_publish.glsl").read_text()
    product = (SHADERS / "render/product/adapters/ortho/product_screen_adapter.glslinc").read_text()
    for caller in (publish, product):
        assert "cell_presentation_merge(" in caller
        assert "cell_presentation_paper_merge(" in caller
        assert "cell_presentation_result(" in caller
    # No projection-private comparator, mask packing, or allocator survives.
    assert not (SHADERS / "native_glyph_layout.glsl").exists()
    for removed in ("product_fl4740_semantic_key_precedes", "final_product_status_pack_cell_mask",
                    "FINAL_PRODUCT_STATUS_CELL_MASK", "fl4740_winner", "fl4359_center_transport_slot"):
        assert removed not in product
        assert removed not in _expand_includes((SHADERS / "render/product/cell_owner_product.glsl").read_text())
    assert "publish_paper_distance" not in publish and "selected_record" not in publish

def test_host_owns_immutable_atlas_assets_only():
    source = (ROOT / "godot_project/render/ascii/glyphs/NativeGlyphLayout.gd").read_text()
    assert "var widths: RID" in source
    for removed in ("func dispatch(", "compute_list_dispatch", "_pipeline", "SPAN_RECORD_BYTES"):
        assert removed not in source
    host = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
    assert "FL4359_CELL_PRESENTATION_RECORD_BYTES := 64" in host
    assert "136, selected_cell_presentation_rid" in host
    assert "137, _native_glyph_layout.widths" in host
    assert "native_glyph_layout.glsl" not in host

if __name__ == "__main__":
    test_authority_record_names_cell_and_color_authority()
    test_one_election_law_is_shared_by_both_projections()
    test_host_owns_immutable_atlas_assets_only()
    print("FL4359_PRESENTATION_ABI_CONTRACT PASS checks=3")
