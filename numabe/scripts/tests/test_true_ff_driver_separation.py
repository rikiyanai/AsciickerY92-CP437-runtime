#!/usr/bin/env python3
"""FL-4907: True FF owns its frame driver; the non-FF compositor never branches on FF.

The goal requires a dedicated FF path with no competing selectors and no
cross-path contamination. True FF used to run inside
ASCIICompositorEffect._render_callback behind `strict_ff_frame`, so every FF
frame paid for the non-FF host and the FF path kept growing workarounds there.
This guard fails the commit if either side reaches into the other again.
Source-level check only; no Godot process starts.

Run: python3 -m pytest scripts/tests/test_true_ff_driver_separation.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSITOR = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
DRIVER = ROOT / "godot_project/render/ascii/composition/TrueFFCompositorEffect.gd"
BOOT_FAILURE_DRIVER = ROOT / "godot_project/render/ascii/composition/TrueFFBootFailureEffect.gd"
DRIVER_FACTORY = ROOT / "godot_project/render/api/PresentationDriverFactory.gd"
WORLD = ROOT / "godot_project/scripts/World.gd"
MODES = ROOT / "godot_project/render/api/PresentationModes.gd"
MENU = ROOT / "godot_project/scripts/DebugPaletteMenu.gd"


def _code(path: Path) -> str:
    # Comments may name the other driver; code may not use it.
    return "\n".join(re.sub(r"#.*$", "", line) for line in
                     path.read_text(encoding="utf-8").splitlines())


def _function(source: str, name: str) -> str:
    start = source.index(f"func {name}(")
    nxt = source.find("\nfunc ", start + 10)
    return source[start:] if nxt < 0 else source[start:nxt]


def test_non_ff_compositor_has_no_true_ff_frame_path() -> None:
    code = _code(COMPOSITOR)
    for symbol in ("strict_ff", "_true_ff_renderer", "_direct_ff",
                   "TrueFFFrameRenderer", "TrueFFPageBackground",
                   "_build_true_ff_scene_context", "render_frame(",
                   "publish_true_ff_actor_sprite_rows_for_frame",
                   "_pending_true_ff_actor", "compact_policy_admitted"):
        assert symbol not in code, symbol


def test_non_ff_callback_never_selects_ff() -> None:
    # The callback may pass FC/CF/CC mode bits to its own shader; it may not
    # test for FF (index 0) or choose a frame family.
    callback = _function(_code(COMPOSITOR), "_render_callback")
    assert not re.search(r"_presentation_mode_index\(\)\s*[!=]=\s*0", callback)
    assert "get_presentation_mode()" not in callback
    assert '"FF"' not in callback


def test_true_ff_driver_reads_no_non_ff_owner() -> None:
    code = _code(DRIVER).lower()
    for symbol in ("splat", "probe", "vsb", "product", "texel",
                   "_build_pixel_camera_context", "pixel_target",
                   "final_pixel", "cell_presentation"):
        assert symbol not in code, symbol
    assert "trueffframerenderer.gd" in code


def test_world_enables_exactly_one_driver() -> None:
    world = _code(WORLD)
    assert "_create_true_ff_effect(_ascii_effect)" in world
    assert "PresentationDriverFactoryScript.create_true_ff(ascii_effect)" in world
    assert "res://render/ascii/composition/" not in world
    assert "_true_ff_effect.enabled = true_ff" in world
    assert "_ascii_effect.enabled = not true_ff" in world
    assert "set_true_ff_display_scale" not in world


def test_shared_ff_has_explicit_selection_but_flat_flat_shader_bits() -> None:
    import ast

    modes = _code(MODES)
    selections = ast.literal_eval(re.search(r"const SELECTIONS := (\[[^\n]+\])", modes).group(1))
    bits = ast.literal_eval(re.search(r"const SAMPLE_BITS := (\[[^\n]+\])", modes).group(1))
    assert dict(zip(selections, bits, strict=True)) == {
        "FF": 0, "FC": 1, "CF": 2, "CC": 3, "FF_SHARED": 0}
    compositor = _code(COMPOSITOR)
    assert "PresentationModes.SAMPLE_BITS[" in _function(compositor, "_presentation_mode_index")
    assert "_presentation_mode_index()" not in _function(compositor, "get_presentation_mode")
    for key in ('"sampling_mode": PresentationModes.sampling_mode(mode)',
                '"driver": PresentationModes.driver(mode)'):
        assert key in _function(compositor, "get_presentation_policy")
    world = _code(WORLD)
    assert 'String(_ascii_effect.call("get_presentation_mode")) == "FF"' in world
    assert 'String(ascii_effect.call("get_presentation_mode")) == "FF"' in world
    assert "mode not in ASCIICompositorEffectScript.PRESENTATION_MODES" in world
    menu = _code(MENU)
    assert "PresentationModes.LABELS" in menu
    assert "PresentationModes.SELECTIONS[index]" in menu
    assert "PresentationModes.SELECTIONS.find(" in menu


def test_shared_factory_is_the_single_private_driver_composition_root() -> None:
    factory = _code(DRIVER_FACTORY)
    private_path = "res://render/ascii/composition/TrueFFCompositorEffect.gd"
    assert private_path in factory
    assert "load(TRUE_FF_DRIVER_PATH) as Script" in factory
    assert "driver.new() as CompositorEffect" in factory
    assert 'effect.set("world", world_sources)' in factory


def test_driver_load_failure_keeps_an_independent_visible_presentation_owner() -> None:
    factory = _code(DRIVER_FACTORY)
    fallback = _code(BOOT_FAILURE_DRIVER)
    assert "_create_true_ff_boot_failure" in factory
    assert '"true_ff_driver_unavailable_on_web"' in factory
    assert '"true_ff_driver_load_failed"' in factory
    assert '"true_ff_driver_instantiation_failed"' in factory
    assert "TrueFFCompositorEffect.gd" not in fallback
    assert "TrueFFFrameRenderer" not in fallback
    assert "texture_clear" in _function(fallback, "_render_callback")
    assert "FAILURE_COLOR" in fallback
    assert "func prepare_output(" in fallback
    assert "static func set_display_scale(" in fallback
    assert "return null" not in _function(factory, "create_true_ff")


def test_driver_dependency_failures_paint_after_color_target_acquisition() -> None:
    callback = _function(_code(DRIVER), "_render_callback")
    color_owner = callback.index("get_render_scene_buffers()")
    for reason in ("world_source_owner_missing", "true_ff_renderer_released",
                   "scene_data_missing"):
        branch = callback.index(f'if {"world == null" if reason == "world_source_owner_missing" else "_renderer == null" if reason == "true_ff_renderer_released" else "scene_data == null"}:')
        assert color_owner < branch
        tail = callback[branch:]
        assert f'_paint_rejected_surface(rd, scene_buffers, view_count,\n\t\t\t"{reason}")' in tail


@pytest.mark.parametrize("instance_flags", range(8))
def test_export_translates_instance_flags_without_hiding_inexact_ink(instance_flags: int) -> None:
    # Evaluate the production scalar expression against the existing parser.
    # This is an offline ABI check, not a captured temporal result.
    sys.path.insert(0, str(ROOT / "scripts"))
    import measure_true_ff_text_flips as detector

    code = _code(DRIVER)
    constants = {name: int(value) for name, value in re.findall(
        r"const (INSTANCE_HUD_FLAG|TEXT_FRAME_HUD_FLAG): int = (\d+)", code)}
    expression = re.search(r"\breturn ([^\n]+)", _function(code, "_text_export_flags"))
    assert expression
    flags = eval(expression.group(1), {"__builtins__": {}},
                 {**constants, "instance_flags": instance_flags})
    layout = (ROOT / "godot_project/render/ascii/features/true_ff_feature_layout.glsl").read_text()
    instance_hud = int(re.search(r"const uint TFF_INSTANCE_HUD=(\d+)u", layout).group(1))
    assert constants["INSTANCE_HUD_FLAG"] == instance_hud
    assert constants["TEXT_FRAME_HUD_FLAG"] == detector.HUD_TEXT_FLAG
    parsed = detector.parse_frame({"schema": detector.SCHEMA, "frame": 0,
        "row_pitch_px": 17, "chars": [[ord("/"), 22, 17, 5, flags, 0, 0]]}, ROOT, None)
    assert len(parsed.glyphs) == 1  # INEXACT (bit 2) never becomes SPACE.
    assert parsed.glyphs[0].hud == bool(instance_flags & instance_hud)
    assert "entry[4] = _text_export_flags(int(entry[4]))" in _function(code, "_write_text_frame")
