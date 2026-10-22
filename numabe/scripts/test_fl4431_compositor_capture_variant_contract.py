#!/usr/bin/env python3
"""Guard FL-4431's one-source compositor capture variant.

Ordinary paint compiles without binding 43, atomics, capture flags, and capture
branches. Explicit evidence compiles the same GLSL source with
FL4431_FINAL_CELL_CAPTURE, adds the proof buffer, and selects the matching host
pipeline only while a request is active. These checks are structural evidence;
they do not claim a rendered frame and do not establish the 60 FPS target.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHADER_PATH = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"
HOST_PATH = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
CAPTURE_DEFINE = "FL4431_FINAL_CELL_CAPTURE"
ORDINARY_BINDINGS = {0, 4, 5, 6, 44, 45, 46, 47, 68}
CAPTURE_BINDINGS = ORDINARY_BINDINGS | {43}


def without_comments(source: str) -> str:
    """Remove comments so explanatory prose cannot satisfy executable pins."""

    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def compact(source: str) -> str:
    """Collapse formatting while preserving token order."""

    return re.sub(r"\s+", "", without_comments(source))


def compact_gd(source: str) -> str:
    """Collapse GDScript without treating the `//` in res paths as comments."""

    return re.sub(r"\s+", "", source)


def shader_variant(source: str, capture_enabled: bool) -> str:
    """Resolve this shader's one supported compile-time capture guard."""

    output: list[str] = []
    active: list[bool] = [True]
    for line in source.splitlines():
        directive = line.strip()
        if directive == f"#ifdef {CAPTURE_DEFINE}":
            active.append(active[-1] and capture_enabled)
            continue
        if directive == "#endif":
            if len(active) == 1:
                raise AssertionError("unmatched #endif in compositor shader")
            active.pop()
            continue
        if active[-1]:
            output.append(line)
    if len(active) != 1:
        raise AssertionError("unterminated capture guard in compositor shader")
    return "\n".join(output)


def binding_set(source: str) -> set[int]:
    """Return every set-zero descriptor binding in one resolved variant."""

    clean = without_comments(source)
    return {
        int(value)
        for value in re.findall(
            r"layout\s*\([^)]*\bset\s*=\s*0[^)]*"
            r"\bbinding\s*=\s*(\d+)",
            clean,
        )
    }


def gd_function(source: str, name: str) -> str:
    """Return one top-level GDScript function through the next declaration."""

    marker = f"func {name}("
    start = source.find(marker)
    assert start >= 0, f"missing GDScript function: {name}"
    end = source.find("\nfunc ", start + len(marker))
    return source[start:] if end < 0 else source[start:end]


def assert_contract(shader: str, host: str) -> None:
    """Assert the single-source, request-dominated capture architecture."""

    ordinary = shader_variant(shader, capture_enabled=False)
    capture = shader_variant(shader, capture_enabled=True)
    ordinary_clean = compact(ordinary)
    capture_clean = compact(capture)
    shader_clean = compact(shader)
    host_clean = compact_gd(host)

    # Ordinary machine source contains exactly the shipping six descriptors.
    # Capture adds only the observation SSBO at binding 43.
    assert binding_set(ordinary) == ORDINARY_BINDINGS
    assert binding_set(capture) == CAPTURE_BINDINGS
    for forbidden in (
        "FinalCellPaintBuffer",
        "final_cell_paint",
        "record_final_cell_paint",
        "atomicAdd",
        "FINAL_CELL_RECORD_WORDS",
        "FINAL_CELL_MAGIC",
    ):
        assert forbidden not in ordinary_clean
    assert "FinalCellPaintBuffer" in capture_clean
    # Four channel/coverage counters remain plus the native mask-ink and actual
    # atlas-mask counters. FL-4359 adds capture-only terminal count and absolute
    # X/Y sums; glyph identity/presence and per-slot colour presence use
    # compare-swap/bitwise atomics instead.
    assert capture_clean.count("atomicAdd(") == 9
    assert capture_clean.count("record_final_cell_paint(") == 2
    assert "constuintFINAL_CELL_RECORD_WORDS=60u;" in capture_clean
    assert "final_cell_paint.words[base+19u]" in capture_clean
    assert "final_cell_paint.words[base+21u+safe_slot]" in capture_clean
    assert "final_cell_paint.words[base+26u]" in capture_clean
    assert "final_cell_paint.words[slot_colour_base+0u]" in capture_clean
    assert "final_cell_paint.words[slot_colour_base+1u]" in capture_clean
    assert "atomicOr(final_cell_paint.words[base+50u],1u<<safe_slot)" in capture_clean
    # FL-4740 must count the actual painter decision, not raw atlas coverage:
    # perspective threshold/pin, ortho pins, and the documented continuous
    # nonzero threshold are all required semantic branches.
    assert "boolmask_foreground" in capture_clean
    assert "if(mask_foreground)" in capture_clean
    assert "capture_mask_foreground=true" in capture_clean
    assert "capture_mask_foreground=intensity>0.001" in capture_clean
    assert "mask_intensity" not in capture_clean
    assert "final_cell_paint.words[base+18u]=" in capture_clean
    assert "constFINAL_CELL_RECORD_WORDS:=60" in host_clean
    assert "data.decode_u32(base+72)" in host_clean

    # Capture is compile-time only. The deleted proof bit and its per-pixel
    # runtime condition must not survive on either side of the host boundary.
    assert "FINAL_CELL_PAINT_PROOF_FLAG" not in shader
    assert "FINAL_CELL_PAINT_PROOF_FLAG" not in host
    assert "final_cell_paint_capture_enabled" not in shader

    # One source, one main, one final paint law, one output store.
    assert shader_clean.count("voidmain(") == 1
    assert shader_clean.count("imageStore(color_image,") == 1
    assert host.count('"res://render/ascii/raster/ascii_compositor.glsl"') == 2
    assert "ascii_compositor_capture.glsl" not in host
    assert "ascii_compositor_capture_variant.glsl" not in host

    initialize = compact_gd(gd_function(host, "_initialize_compute"))
    assert (
        '_compile_compute("res://render/ascii/raster/ascii_compositor.glsl")'
        in initialize
    )
    capture_compile = (
        '_compile_compute("res://render/ascii/raster/ascii_compositor.glsl",'
        'PackedStringArray(["FL4431_FINAL_CELL_CAPTURE"]))'
    )
    assert capture_compile in initialize
    compile_failure_start = initialize.find("ifcapture_compiled.is_empty():")
    present_compile_start = initialize.find(
        'varpresent_compiled:=_compile_compute('
    )
    assert 0 <= compile_failure_start < present_compile_start
    assert (
        "return"
        not in initialize[compile_failure_start:present_compile_start]
    )
    assert (
        '"readback_state":'
        '"final_cell_capture_pipeline_compile_failed"'
        in initialize[compile_failure_start:present_compile_start]
    )

    render = gd_function(host, "_render_callback")
    render_clean = compact_gd(render)
    common_set = (
        "varcompositor_uniforms:Array[RDUniform]=["
        "u_color,u_font,u_extended_glyph,u_morphology_glyph,"
        "u_final_subcell_product,u_compositor_actor_candidates,"
        "u_compositor_actor_rows,u_compositor_actor_atlas,"
        "u_compositor_vsb_product_frame,]"
    )
    assert common_set in render_clean
    assert render_clean.count(".binding=43") == 1
    capture_select = render_clean.find("iffinal_paint_capture_active:")
    binding_43 = render_clean.find(".binding=43")
    assert 0 <= capture_select < binding_43
    assert (
        "compositor_shader_for_frame=final_cell_capture_shader"
        in render_clean[binding_43:]
    )
    assert (
        "compositor_pipeline_for_frame=final_cell_capture_pipeline"
        in render_clean[binding_43:]
    )
    assert (
        "u_final_cell_paint.uniform_type="
        "(RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER)"
        in render_clean
    )
    assert "compositor_uniforms.append(u_final_cell_paint)" in render_clean

    # Request-active setup is the only allocation call site. Every failure
    # cancels evidence, rebuilds ordinary paint when needed, and remains named.
    prepare_call = "_prepare_final_cell_paint_capture_buffer("
    assert host.count(prepare_call) == 2  # definition plus one live call
    live_prepare = render_clean.find(prepare_call)
    assert 0 <= capture_select < live_prepare < binding_43
    assert (
        '_cancel_final_cell_paint_capture('
        '"final_cell_capture_pipeline_invalid")'
        in render_clean
    )
    assert (
        '_cancel_final_cell_paint_capture('
        '"final_cell_capture_uniform_set_invalid")'
        in render_clean
    )
    assert (
        "compositor_shader_for_frame=shader"
        "compositor_pipeline_for_frame=pipeline"
        in render_clean
    )
    for reason in (
        "final_cell_capture_pipeline_compile_failed",
        "final_cell_capture_pipeline_invalid",
        "final_cell_capture_uniform_set_invalid",
        "final_cell_capture_buffer_allocation_failed",
        "final_cell_capture_buffer_extent_invalid",
        "final_cell_capture_buffer_clear_failed",
    ):
        assert reason in host
    cancel = compact_gd(
        gd_function(host, "_cancel_final_cell_paint_capture")
    )
    assert '"readback_state":reason' in cancel
    assert "_release_final_cell_paint_capture_buffer()" in cancel

    ensure = compact_gd(
        gd_function(host, "_ensure_final_cell_paint_buffer")
    )
    prepare = compact_gd(
        gd_function(host, "_prepare_final_cell_paint_capture_buffer")
    )
    assert "storage_buffer_create(" in ensure
    assert "_ensure_final_cell_paint_buffer(" in prepare
    assert "buffer_update(" in prepare
    assert "storage_buffer_create(" not in render_clean

    # Successful readback releases the capture-only RID before metadata is
    # built. The durable CPU receipt, never RID liveness, owns the later
    # success/failure classification.
    metadata = compact_gd(
        gd_function(host, "build_final_render_dump_metadata")
    )
    assert (
        "_last_explicit_final_cell_paint_audit.get("
        '"readback_state",'
        '"explicit_final_cell_paint_not_captured")'
        in metadata
    )
    assert (
        'final_cell_paint_readback_state=='
        '"final_cell_paint_readback_ok"'
        in metadata
    )
    assert '"final_cell_paint_state":final_cell_paint_state' in metadata
    assert (
        '"final_cell_paint_readback_state":'
        "final_cell_paint_readback_state"
        in metadata
    )
    assert "final_cell_paint_rd.is_valid()" not in metadata


def require_rejected(name: str, shader: str, host: str) -> None:
    """Require one destructive source mutation to make the contract red."""

    try:
        assert_contract(shader, host)
    except (AssertionError, ValueError):
        return
    raise AssertionError(f"destructive mutant escaped: {name}")


def replace_once(source: str, old: str, new: str, name: str) -> str:
    """Replace one unique mutation anchor and fail if source drifted."""

    count = source.count(old)
    assert count == 1, f"{name}: mutation anchor count={count}"
    return source.replace(old, new, 1)


def compile_variant(source: str, *, capture_enabled: bool) -> None:
    """Compile the exact ordinary or capture source after guard resolution."""

    compiler = shutil.which("glslangValidator")
    assert compiler is not None, "glslangValidator is required"
    resolved = shader_variant(source, capture_enabled).removeprefix("#[compute]\n")
    with tempfile.NamedTemporaryFile("w", suffix=".comp", delete=False) as handle:
        handle.write(resolved)
        source_path = Path(handle.name)
    spirv_path = source_path.with_suffix(".spv")
    try:
        result = subprocess.run(
            [
                compiler,
                "-V",
                "--target-env",
                "vulkan1.2",
                "-S",
                "comp",
                str(source_path),
                "-o",
                str(spirv_path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        validator = shutil.which("spirv-val")
        if validator is not None:
            validation = subprocess.run(
                [validator, "--target-env", "vulkan1.2", str(spirv_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert validation.returncode == 0, validation.stdout + validation.stderr
    finally:
        source_path.unlink(missing_ok=True)
        spirv_path.unlink(missing_ok=True)


def main() -> int:
    shader = SHADER_PATH.read_text(encoding="utf-8", errors="replace")
    host = HOST_PATH.read_text(encoding="utf-8", errors="replace")
    assert_contract(shader, host)
    compile_variant(shader, capture_enabled=False)
    compile_variant(shader, capture_enabled=True)

    # Keep both FL-4740 semantic cases mutation-sensitive: the shared painter
    # uses its threshold/pin decision, while a center pin counts even at zero
    # raw atlas intensity. These are source guards, not headed proof.
    require_rejected(
        "painter mask count falls back to raw atlas coverage",
        replace_once(
            shader,
            "capture_mask_foreground = intensity > 0.001;",
            "capture_mask_foreground = atlas_mask_foreground;",
            "painter raw coverage fallback",
        ),
        host,
    )
    require_rejected(
        "orthographic center pin loses foreground decision",
        replace_once(
            shader,
            "capture_mask_foreground = true;",
            "capture_mask_foreground = intensity > 0.001;",
            "orthographic center pin fallback",
        ),
        host,
    )

    binding_guard = (
        f"#ifdef {CAPTURE_DEFINE}\n"
        "\tlayout(set = 0, binding = 43, std430) buffer FinalCellPaintBuffer {\n"
        "\t\tuint words[];\n"
        "\t} final_cell_paint;\n"
        "#endif"
    )
    require_rejected(
        "binding guard removed",
        replace_once(
            shader,
            binding_guard,
            binding_guard.replace(f"#ifdef {CAPTURE_DEFINE}\n", "").replace(
                "\n#endif", ""
            ),
            "binding guard removed",
        ),
        host,
    )

    paint_section = shader.index("// 4. Record what was actually painted")
    call_guard_start = shader.index(
        "\t#ifdef FL4431_FINAL_CELL_CAPTURE", paint_section
    )
    call_guard_end = shader.index("\n\t#endif", call_guard_start) + len("\n\t#endif")
    call_guard = shader[call_guard_start:call_guard_end]
    require_rejected(
        "shipping capture call exposed",
        replace_once(
            shader,
            call_guard,
            call_guard.replace(
                "\t#ifdef FL4431_FINAL_CELL_CAPTURE\n", ""
            ).replace("\n\t#endif", ""),
            "shipping capture call exposed",
        ),
        host,
    )

    require_rejected(
        "shader record stride regressed",
        replace_once(
            shader,
            "const uint FINAL_CELL_RECORD_WORDS = 60u;",
            "const uint FINAL_CELL_RECORD_WORDS = 59u;",
            "shader record stride regressed",
        ),
        host,
    )

    require_rejected(
        "host record stride regressed",
        shader,
        replace_once(
            host,
            "const FINAL_CELL_RECORD_WORDS := 60",
            "const FINAL_CELL_RECORD_WORDS := 59",
            "host record stride regressed",
        ),
    )

    shipping_43 = (
        "\t\tvar u_shipping_capture := RDUniform.new()\n"
        "\t\tu_shipping_capture.uniform_type = "
        "RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER\n"
        "\t\tu_shipping_capture.binding = 43\n"
        "\t\tu_shipping_capture.add_id(final_cell_paint_rd)\n"
        "\t\tcompositor_uniforms.append(u_shipping_capture)\n"
    )
    common_anchor = (
        "\t\tvar compositor_shader_for_frame: RID = shader\n"
    )
    require_rejected(
        "ordinary host binding 43 restored",
        shader,
        replace_once(
            host,
            common_anchor,
            shipping_43 + common_anchor,
            "ordinary host binding 43 restored",
        ),
    )

    require_rejected(
        "second GLSL source introduced",
        shader,
        replace_once(
            host,
            '"res://render/ascii/raster/ascii_compositor.glsl",\n'
            '\t\tPackedStringArray(["FL4431_FINAL_CELL_CAPTURE"])',
            '"res://shaders/ascii_compositor_capture.glsl",\n'
            '\t\tPackedStringArray(["FL4431_FINAL_CELL_CAPTURE"])',
            "second GLSL source introduced",
        ),
    )

    require_rejected(
        "capture selected ordinary pipeline",
        shader,
        replace_once(
            host,
            "compositor_pipeline_for_frame = final_cell_capture_pipeline",
            "compositor_pipeline_for_frame = pipeline",
            "capture selected ordinary pipeline",
        ),
    )

    require_rejected(
        "capture define omitted",
        shader,
        replace_once(
            host,
            'PackedStringArray(["FL4431_FINAL_CELL_CAPTURE"])',
            "PackedStringArray()",
            "capture define omitted",
        ),
    )

    require_rejected(
        "runtime proof flag restored",
        replace_once(
            shader,
            "const int ANSI_CELL_SIZE = 8;",
            "const int FINAL_CELL_PAINT_PROOF_FLAG = 268435456;\n"
            "const int ANSI_CELL_SIZE = 8;",
            "runtime proof flag restored",
        ),
        host,
    )

    require_rejected(
        "capture slot drift",
        shader,
        replace_once(
            host,
            "u_final_cell_paint.binding = 43",
            "u_final_cell_paint.binding = 42",
            "capture slot drift",
        ),
    )

    require_rejected(
        "capture descriptor class drift",
        shader,
        replace_once(
            host,
            "RenderingDevice.UNIFORM_TYPE_STORAGE_BUFFER\n"
            "\t\t\t)",
            "RenderingDevice.UNIFORM_TYPE_UNIFORM_BUFFER\n"
            "\t\t\t)",
            "capture descriptor class drift",
        ),
    )

    require_rejected(
        "capture failure claimed success",
        shader,
        replace_once(
            host,
            '"final_cell_capture_buffer_allocation_failed"',
            '"final_cell_paint_readback_ok"',
            "capture failure claimed success",
        ),
    )

    prepare_anchor = (
        "\t\tvar final_paint_capture_active := "
        "_final_cell_paint_capture_active()\n"
    )
    require_rejected(
        "capture allocation exposed to ordinary paint",
        shader,
        replace_once(
            host,
            prepare_anchor,
            "\t\t_prepare_final_cell_paint_capture_buffer("
            "pixel_target_size, pixel_ctx.cell_size)\n"
            + prepare_anchor,
            "capture allocation exposed to ordinary paint",
        ),
    )

    store = "imageStore(color_image, pos, vec4(color, 1.0));"
    require_rejected(
        "paint law duplicated",
        replace_once(
            shader,
            store,
            store + "\n\t" + store,
            "paint law duplicated",
        ),
        host,
    )

    require_rejected(
        "successful readback re-coupled to freed RID",
        shader,
        replace_once(
            host,
            (
                'final_cell_paint_readback_state == '
                '"final_cell_paint_readback_ok"'
            ),
            "final_cell_paint_rd.is_valid()",
            "successful readback re-coupled to freed RID",
        ),
    )

    print("FL4431_COMPOSITOR_CAPTURE_VARIANT_CONTRACT_PASS mutants=17")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
