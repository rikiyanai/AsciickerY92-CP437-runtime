"""FL-4359 (2026-09-15) absence guard: no stand-in diagnostic glyph anywhere.

Seven writers once emitted CP437 0xDB / 219 with five different meanings
(missing cell mask, VSB failure record, layout refusal, mesh material
fallback, ortho decline sidecar, host prefill). A refused cell now carries
glyph 0 and no colour; status bits are the only marker. This guard asserts the
retired writers stay retired. It is a negative source guard, not behavioural
proof; the explicit b43 final-cell audit owns runtime refusal accounting.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHADERS = ROOT / "godot_project"
HOST = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"


def _code(path: Path) -> str:
    text = path.read_text()
    return "\n".join(line.split("//", 1)[0] for line in text.splitlines())


def test_painter_owns_no_glyph():
    code = _code(SHADERS / "render/ascii/raster/ascii_compositor.glsl")
    assert "0xDB" not in code
    assert "int glyph = 0;" in code
    # The decoder may remain for the b43 provenance join; it may not seed paint.
    assert "int(final_product_status_cell_mask_glyph(product_status))" not in code


def test_product_failure_record_is_all_zero():
    code = _code(SHADERS / "render/product/cell_owner_product.glsl")
    assert "0xDBu" not in code
    assert "record.r0 = uvec4(0u);" in code


def test_material_has_no_fallback_glyph_or_palette():
    code = _code(SHADERS / "render/product/payload/product_payload_compiler.glslinc")
    assert "0xDBu" not in code
    assert "q3_probe_payload_fallback_" not in code
    seed = _code(SHADERS / "render/product/material/product_material.glslinc")
    assert "q3_probe_payload_fallback_" not in seed
    assert "0u, uvec4(0u), true, family, vec3(0.0), vec3(0.0)," in seed


def test_ortho_decline_sidecar_has_no_glyph():
    code = _code(SHADERS / "render/product/adapters/ortho/product_screen_adapter.glslinc")
    assert "FL4216_DECLINE_DIAGNOSTIC_GLYPH" not in code
    assert "0xDB" not in code


def test_actor_window_glyph_is_named_not_diagnostic():
    code = _code(SHADERS / "render/product/adapters/capture/product_capture_adapter.glslinc")
    assert "const uint FL4359_ACTOR_SPRITE_WINDOW_GLYPH = 219u;" in code
    assert "uvec4(219u" not in code


def test_host_prefill_has_no_marker_glyph_or_colour():
    text = HOST.read_text()
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert "VSB_FINAL_FAILURE_GLYPH" not in code
    assert "_write_ivec4(bytes, base, 0, 0, 0, 0)" in code
    assert "_write_vec4(bytes, base + 32, 0.0, 0.0, 0.0, 0.0)" in code
    assert "_write_vec4(bytes, base + 48, 0.0, 0.0, 0.0, 0.0)" in code


# 2026-09-16 correction: the 2026-09-15 cut missed three surviving writers of
# the same truth. Perspective publication substituted glyph 63 and magenta ink
# for a refused cell, native_presentation_channels() substituted 63/magenta for
# any invalid input, and the layout seeded magenta ink. The painter also set
# b43 word-19 mask presence for every painted slot regardless of validity.


def test_perspective_publication_refusal_carries_no_stand_in():
    code = _code(SHADERS / "render/backends/perspective/publish/perspective_probe_payload_publish.glsl")
    assert "63u" not in code
    assert "vec3(1,0,1)" not in code and "vec3(1, 0, 1)" not in code
    # FL-4848: the publisher gathers candidates and stores the shared
    # authority's result; it packs no glyph, ink, or paper of its own.
    assert "cell_presentation_result(index, true, true, true," in code
    assert "front_presentation.records[index] = entry;" in code
    assert "packUnorm4x8" not in code.split("void publish_cell", 1)[1].split("bool valid_color_sample", 1)[0]


def test_native_channels_substitute_nothing():
    code = _code(SHADERS / "render/ascii/authority/native_presentation_authority.glslinc")
    assert "63u" not in code
    assert "vec3(1, 0, 1)" not in code and "vec3(1,0,1)" not in code
    assert "result.valid ? cell.allocation.z : 0u" in code


def test_shared_authority_refusal_carries_no_stand_in():
    # FL-4848: one result builder for both projections. A refused cell is
    # glyph 0 and black in both channels; the retired layout program is gone.
    code = _code(SHADERS / "render/ascii/authority/native_presentation_authority.glslinc")
    assert "vec3(1.0, 0.0, 1.0)" not in code
    assert "valid ? winner.glyph : 0u" in code
    assert "valid ? winner.ink.rgb : vec3(0.0)" in code
    assert "valid ? paper.paper.rgb : vec3(0.0)" in code
    assert not (SHADERS / "native_glyph_layout.glsl").exists()


def test_mask_presence_follows_elected_validity_only():
    code = _code(SHADERS / "render/ascii/raster/ascii_compositor.glsl")
    assert "final_product_status_has_cell_mask(product_status)" not in code
    assert "bool cell_mask_present = false;" in code
    assert code.count("cell_mask_present = channels.valid;") == 1
    assert "cell_mask_present = front_result_valid;" not in code
    assert "if (mask_present) {" in code
    assert "cell_mask_foliage_present" not in code
