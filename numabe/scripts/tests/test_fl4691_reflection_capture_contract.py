#!/usr/bin/env python3
"""FL-4691 reflection capture specialization contract.

This is an offline source/compile contract.  Ordinary front and underlay
capture omit metadata outputs; explicit recorder capture retains the full ABI.  EYE's mirrored capture writes only
the three attachments consumed by Product's reflection reader plus the same
D32 hardware depth attachment; retained CURRENT/PREVIOUS layers keep their
previous reflection payload.  The specialization must preserve the vertex
stage and the shared depth, discard, normal, and material equations.

The test never launches Godot, a renderer, a game, or a capture runner.  When
glslangValidator is installed it asks the same preprocessor for both variants;
the semantic assertions remain available with the small fallback preprocessor
so the contract is still checked in minimal CI images.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
SHADER = PROJECT / "render" / "backends" / "perspective" / "capture" / "perspective_probe_capture.glsl"
PIPELINE = PROJECT / "render" / "backends" / "perspective" / "PerspectiveProbeBackend.gd"
PRODUCT = PROJECT / "render" / "product" / "cell_owner_product.glsl"
EFFECT = PROJECT / "render" / "api" / "ASCIICompositorEffect.gd"


def _expand_includes(source: str, stack: tuple[Path, ...] = ()) -> str:
    def replace(match: re.Match[str]) -> str:
        included = PROJECT / match.group(1)
        assert included.exists(), f"missing shader include: {included}"
        assert included not in stack, f"cyclic shader include: {included}"
        return _expand_includes(included.read_text(encoding="utf-8"), (*stack, included))

    return re.sub(
        r'^\s*#include\s+"res://([^\"]+)"\s*$',
        replace,
        source,
        flags=re.MULTILINE,
    )


def _stages() -> dict[str, str]:
    source = _expand_includes(SHADER.read_text(encoding="utf-8"))
    stages: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in source.splitlines():
        marker = re.fullmatch(r"\s*#\[(vertex|fragment)\]\s*", line)
        if marker:
            if current is not None:
                stages[current] = "\n".join(lines)
            current = marker.group(1)
            lines = []
        elif current is not None:
            lines.append(line)
    assert current is not None
    stages[current] = "\n".join(lines)
    assert set(stages) == {"vertex", "fragment"}
    return stages


def _fallback_preprocess(source: str, reflection: bool) -> str:
    """Resolve the specialization's small conditional surface without GLSL."""
    enabled = {"FL4216_REFLECTION_CAPTURE"} if reflection else set()
    output: list[str] = []
    active = [True]
    for line in source.splitlines():
        directive = re.match(r"\s*#\s*(ifdef|ifndef|else|endif)\s*([A-Za-z0-9_]*)", line)
        if directive:
            kind, name = directive.group(1), directive.group(2)
            if kind == "ifdef":
                active.append(active[-1] and name in enabled)
            elif kind == "ifndef":
                active.append(active[-1] and name not in enabled)
            elif kind == "else":
                assert len(active) > 1
                parent = active[-2]
                active[-1] = parent and not active[-1]
            else:
                assert len(active) > 1
                active.pop()
            continue
        if active[-1] and not line.lstrip().startswith(("#version", "#define")):
            output.append(line)
    assert len(active) == 1
    return "\n".join(output)


def _preprocess(stage: str, reflection: bool, paint_only: bool = False) -> str:
    source = _stages()[stage]
    tool = shutil.which("glslangValidator") or shutil.which("glslang")
    if tool is None:
        if paint_only:
            pytest.skip("glslang required for paint-only specialization")
        return _fallback_preprocess(source, reflection)
    suffix = {"vertex": "vert", "fragment": "frag"}[stage]
    with tempfile.TemporaryDirectory(prefix="fl4691_capture_") as directory:
        source_path = Path(directory) / f"capture.{suffix}"
        source_path.write_text(source, encoding="utf-8")
        command = [tool, "-E", "-S", suffix]
        if reflection:
            command.append("-DFL4216_REFLECTION_CAPTURE")
        if paint_only:
            command.append("-DPROBE_CAPTURE_PAINT_ONLY")
        command.append(str(source_path))
        result = subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert result.returncode == 0, result.stdout
        return result.stdout


def _fragment_outputs(source: str) -> list[tuple[int, str, str]]:
    return [
        (int(location), value_type, name)
        for location, value_type, name in re.findall(
            r"layout\s*\(\s*location\s*=\s*(\d+)\s*\)\s*"
            r"out\s+([A-Za-z][A-Za-z0-9_]*)\s+([A-Za-z][A-Za-z0-9_]*)\s*;",
            source,
        )
    ]


def _assignment(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*=\s*[^;]+;", source)
    assert match, f"missing retained output assignment for {name}"
    return re.sub(r"\s+", " ", match.group(0)).strip()


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def test_reflection_preprocess_keeps_vertex_and_shared_retained_equations() -> None:
    normal_vertex = _preprocess("vertex", False)
    reflection_vertex = _preprocess("vertex", True)
    assert normal_vertex == reflection_vertex, "reflection must reuse the exact vertex stage"

    normal_fragment = _preprocess("fragment", False)
    reflection_fragment = _preprocess("fragment", True)
    assert _fragment_outputs(normal_fragment) == [
        (0, "uvec4", "out_identity"),
        (1, "vec4", "out_normal_depth"),
        (2, "vec4", "out_anchor_source"),
        (3, "uvec4", "out_meta"),
        (4, "uvec4", "out_revisions"),
        (5, "vec4", "out_colour"),
        (6, "vec4", "out_world"),
    ]
    assert _fragment_outputs(reflection_fragment) == [
        (0, "uvec4", "out_identity"),
        (1, "vec4", "out_normal_depth"),
        (2, "uvec4", "out_meta"),
    ]
    for output in ("out_identity", "out_normal_depth", "out_meta"):
        assert _assignment(normal_fragment, output) == _assignment(reflection_fragment, output)
    normal_compact = _compact(normal_fragment)
    reflection_compact = _compact(reflection_fragment)
    for retained_formula in (
        "vec3normal=normalize(vertex_normal);",
        "if(capture.terrain_origin_surface.w>0.5",
        "if(dot(normal,source_ray_direction)>0.0)normal=-normal;",
        "floatchebyshev_depth=max(abs(vertex_world.x-capture.probe_origin_near.x),",
        "floatauthored_shade=uintBitsToFloat(vertex_meta.y);",
    ):
        assert retained_formula in normal_compact
        assert retained_formula in reflection_compact
    for scratch_output in (
        "out_anchor_source",
        "out_revisions",
        "out_colour",
        "out_world",
    ):
        assert scratch_output not in reflection_fragment


def test_glslang_preprocessed_reflection_variant_is_valid_when_available() -> None:
    tool = shutil.which("glslangValidator") or shutil.which("glslang")
    if tool is None:
        pytest.skip("glslangValidator/glslang unavailable; semantic fallback still ran")
    # _preprocess uses -E for both stages and asserts the tool's exit status.
    assert "out_identity" in _preprocess("fragment", False)
    assert "out_meta" in _preprocess("fragment", True)


def test_host_reflection_framebuffer_has_three_consumed_color_attachments() -> None:
    pipeline = PIPELINE.read_text(encoding="utf-8")
    assert 'const CAPTURE_COLOR_COUNT := 7' in pipeline
    assert 'for name in ["identity", "normal_depth", "surface_meta", "hardware_depth"]:' in pipeline
    assert 'var format := RenderingDevice.DATA_FORMAT_R32G32B32A32_UINT if name in ["identity", "surface_meta"] else RenderingDevice.DATA_FORMAT_R32G32B32A32_SFLOAT' in pipeline
    assert '_reflection_pipeline = _create_graphics_pipeline(' in pipeline
    assert '_rd.framebuffer_get_format(_reflection_framebuffers[0]),' in pipeline
    assert 'true, 3, RenderingDevice.COMPARE_OP_LESS)' in pipeline
    assert 'var reflection := _compile_graphics_shader(SHADER_CAPTURE, "FL4216_REFLECTION_CAPTURE")' in pipeline
    assert 'clear.slice(0, 3)' in pipeline
    assert '_rd.draw_list_bind_render_pipeline(list, _reflection_pipeline)' in pipeline
    assert '_capture_shader if _capture_metadata_required else _paint_capture_shader' in pipeline

    # Front and underlay continue to use the complete eight-attachment ABI,
    # including the shared D32 depth target.
    assert 'for name in ["identity", "normal_depth", "anchor_source", "surface_meta", "source_revisions", "colour", "world", "hardware_depth"]:' in pipeline
    assert 'for attachment in CAPTURE_ATTACHMENT_ABI:' in pipeline

    # Front and underlay retain their original draw order and cadence. The
    # reflection specialization is the later, separate pass over the same
    # reflected command list.
    submit = pipeline[pipeline.index("func _submit_capture_face("):]
    underlay_draw = submit.index("underlay_framebuffer, RenderingDevice.DRAW_CLEAR_ALL")
    underlay_copy = submit.index("_copy_underlay_capture_depth(layer)")
    reflection_draw = submit.index("_reflection_framebuffers[layer], RenderingDevice.DRAW_CLEAR_ALL")
    assert underlay_draw < underlay_copy < reflection_draw
    assert "_copy_underlay_capture_layer(layer)" not in submit
    assert "RenderingDevice.DRAW_CLEAR_COLOR_0" in submit
    for color_index in range(1, 7):
        assert "RenderingDevice.DRAW_IGNORE_COLOR_%d" % color_index in submit
    # Reflection preparation and the mirror draw are EYE-owned. Retained
    # CURRENT/PREVIOUS layers keep their reflection payload untouched while
    # their underlay depth still participates in the front capture.
    assert 'var capture_reflection := _capture_face_needs_reflection(layer, underlay_commands)' in submit
    assert submit.count("if capture_reflection:") >= 2
    assert "for command_value in underlay_commands:" in submit
    assert "for command_value in commands:" in submit
    assert 'if not live_foreground or not bool(command.get("capture_direct", true)) or not bool(command.get("contains_vegetation", false)):' in submit
    assert "for command in reflected:" in submit


def test_reflection_consumers_106_107_108_keep_identity_normal_meta_mapping() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    effect = EFFECT.read_text(encoding="utf-8")
    assert 'layout(set = 0, binding = 106) uniform usampler2DArray fl4359_reflection_identity;' in product
    assert 'layout(set = 0, binding = 107) uniform sampler2DArray fl4359_reflection_normal;' in product
    assert 'layout(set = 0, binding = 108) uniform usampler2DArray fl4359_reflection_meta;' in product
    assert 'texelFetch(fl4359_reflection_identity, address, 0)' in product
    assert 'texelFetch(fl4359_reflection_normal, address, 0).xyz' in product
    assert 'texelFetch(fl4359_reflection_meta, address, 0).y' in product
    assert 'for binding in {106: "identity", 107: "normal_depth", 108: "surface_meta"}:' in effect
    assert 'var reflection: Dictionary = probe_view_receipt.get("probe_reflection_attachment_rids", {})' in effect
    assert '"probe_reflection_attachment_rids"' in effect


def test_paint_only_capture_deletes_only_recorder_outputs():
    from test_fl4691_paired_payload_contract import _function_source
    assert _preprocess("vertex", False) == _preprocess("vertex", False, True)
    full = _preprocess("fragment", False)
    paint = _preprocess("fragment", False, True)
    assert _fragment_outputs(paint) == [
        (0, "uvec4", "out_identity"), (1, "vec4", "out_normal_depth"),
        (2, "vec4", "out_anchor_source"), (3, "vec4", "out_colour"),
        (5, "uvec4", "out_revisions"), (4, "vec4", "out_world"),
    ]
    # Compare the entire fragment program after deleting exactly the one
    # unused output and its one unused scalar. Depth, discard, coverage,
    # normal, identity, captured pose, source revisions, and world equations
    # must be identical: paint-only drops the metadata recorder output while
    # the exact source-revision sidecar stays shared.
    full_main = _function_source(full, "main")
    paint_main = _function_source(paint, "main")
    for name in ("out_meta", "authored_shade"):
        full_main = re.sub(r"(?:float\s+)?\b" + name + r"\s*=[^;]+;", "", full_main)
        paint_main = re.sub(r"(?:float\s+)?\b" + name + r"\s*=[^;]+;", "", paint_main)
    assert _compact(full_main) == _compact(paint_main)
