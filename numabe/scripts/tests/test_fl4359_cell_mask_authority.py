"""FL-4359 cell-local publication contracts; not viewport acceptance."""

from pathlib import Path

import pytest

from scripts.tests.test_fl4057_perspective_probe_pipeline_contract import (
    HOST, PIPELINE, PUBLISH, SHADERS, _glsl_function_source,
    compile_stage, compiler, split_stages,
)


def test_publication_cannot_consume_another_cell():
    source = PUBLISH.read_text()
    cell = _glsl_function_source(source, "publish_cell")
    assert "cell_presentation_result(index, true, true, true," in cell
    assert "front_presentation.records[index] = entry;" in cell
    for removed in ("publish_span", "front_next", "underlay_next", "index + 1u",
                    "underlay_presentation", "cell_underlay_required"):
        assert removed not in source
    main = _glsl_function_source(source, "main")
    assert "uvec2 cell = gl_WorkGroupID.xy;" in main
    assert "publish_cell(index, cell, span, center_local);" in main
    assert "_rd.compute_list_dispatch(compute_list, cells.x, cells.y, 1)" in PIPELINE.read_text()


def test_pixel_authority_has_no_mask_decisions():
    source = (SHADERS / "render/ascii/authority/native_presentation_authority.glslinc").read_text()
    pixel = _glsl_function_source(PUBLISH.read_text(), "publish_pixel_authority")
    compositor = (SHADERS / "render/ascii/raster/ascii_compositor.glsl").read_text()
    for removed in ("NATIVE_PIXEL_SUPPRESS_INK", "underlay_glyph", "has_underlay",
                    "suppress_ink", "NATIVE_PIXEL_UNDERLAY"):
        assert removed not in source + pixel + compositor
    # FL-4359 (2026-09-16): an invalid cell yields glyph 0, never a stand-in.
    assert "result.glyph = result.valid ? cell.allocation.z : 0u;" in source
    assert "63u" not in source
    assert "cell.allocation.x == cell_index" in source
    assert "NATIVE_PIXEL_VALID" not in pixel
    assert "foliage_center_pin" not in compositor
    assert "vec3 color = mix(resolved.bg, resolved.fg, intensity);" in compositor
    assert "float effective_coverage = intensity;" in compositor


def test_flat_and_continuous_color_domains_are_independent():
    source = PUBLISH.read_text()
    cell = _glsl_function_source(source, "publish_cell")
    pixel = _glsl_function_source(source, "publish_pixel_authority")
    assert "((params.eye.w & 1u) != 0u ? NATIVE_CHANNEL_BG_NATIVE_PIXEL : 0u)" in cell
    assert "((params.eye.w & 2u) != 0u ? NATIVE_CHANNEL_INK_NATIVE_PIXEL : 0u)" in cell
    assert "bool flat_paper = (front.channels.z & NATIVE_CHANNEL_BG_NATIVE_PIXEL) == 0u;" in pixel
    assert "bool deeper_paper = !flat_paper && ink_only;" in pixel
    assert "NATIVE_PIXEL_FLAT_PAPER" in pixel
    assert "var _presentation_modes_by_projection: Array[int] = [0, 0]" in HOST.read_text()


def test_native_mask_sampling_is_complete_and_discrete():
    source = (SHADERS / "render/ascii/raster/ascii_compositor.glsl").read_text()
    sampler = _glsl_function_source(source, "sample_compositor_glyph_intensity_native_footprint")
    assert "float scale = min(" in sampler
    assert "vec2 padding =" in sampler
    assert "native_source_width * 16u" in sampler
    assert "ivec2 at = base + ivec2(floor(source_pos));" in sampler
    footprint = _glsl_function_source(source, "sample_compositor_page_footprint")
    assert "texelFetch(" in footprint
    assert "mix(" not in footprint
    assert source.count("sample_compositor_glyph_intensity_native_footprint(") == 2


def test_retired_underlay_presentation_has_no_live_binding():
    pipeline, host = PIPELINE.read_text(), HOST.read_text()
    for removed in ("_native_underlay", "underlay_span", "u_underlay_layout"):
        assert removed not in pipeline + host
    shader = (SHADERS / "render/ascii/raster/ascii_compositor.glsl").read_text()
    assert "grass_underlay_layout" not in shader


@pytest.mark.parametrize("name,defines", [
    ("perspective_probe_payload_publish.glsl", ""),
    ("ascii_compositor.glsl", ""),
    ("ascii_compositor.glsl", "#define FL4431_FINAL_CELL_CAPTURE\n#define FL4431_FINAL_PIXEL_PAINT_CAPTURE\n"),
    ("ascii_compositor.glsl", "#define FL4431_FINAL_CELL_CAPTURE\n#define FL4431_FINAL_PIXEL_PAINT_CAPTURE\n#define FL4431_FINAL_PIXEL_PAINT_COMPACT_CAPTURE\n"),
])
def test_production_presentation_program_compiles(name: str, defines: str, tmp_path: Path):
    shader_path = PUBLISH if name == "perspective_probe_payload_publish.glsl" else SHADERS / "render/ascii/raster/ascii_compositor.glsl"
    source = shader_path.read_text()
    source = source.replace("#version 450", "#version 450\n" + defines, 1)
    for stage, body in split_stages(source).items():
        compile_stage(compiler(), stage, body, tmp_path)
