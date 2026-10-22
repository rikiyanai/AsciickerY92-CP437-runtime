from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
POSTPROCESS = ROOT / "godot_project/render/post/PostProcessPipeline.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
BLUR = ROOT / "godot_project/render/post/blur/tilt_shift_blur.glsl"
CONTROLS = ROOT / "godot_project/scripts/ControlsRegistry.gd"
WORLD = ROOT / "godot_project/scripts/World.gd"
MENU = ROOT / "godot_project/scripts/DebugPaletteMenu.gd"
GODOT_CONTROL_TEST = (
    ROOT / "godot_project/scripts/tests/test_fl4784_tilt_shift_controls.gd"
)


def test_tilt_shift_is_a_connected_spatial_postprocess() -> None:
    host = HOST.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    blur = BLUR.read_text(encoding="utf-8")

    # The retired same-cell Product mix was not spatial blur.
    assert "TiltShiftControl" not in product
    assert "product_apply_tilt_shift_defocus" not in product
    assert "binding = 56" not in product

    # The replacement reads neighboring completed pixels into a distinct image.
    assert "sampler2D completed_ascii_tex" in blur
    assert "writeonly image2D out_color_image" in blur
    assert "for (int ky = -2; ky <= 2; ++ky)" in blur
    assert "for (int kx = -2; kx <= 2; ++kx)" in blur
    assert "pixel + offset" in blur
    assert "texelFetch(completed_ascii_tex, source_pixel, 0)" in blur

    # Ordinary presentation must execute the pass; it cannot be nested under
    # the optional sparse-capture callback again. Program ownership lives in
    # the extracted postprocess pipeline; the host keeps only feature wiring.
    post = POSTPROCESS.read_text(encoding="utf-8")
    assert '"res://render/post/blur/tilt_shift_blur.glsl"' in post
    assert '"res://render/post/blur/tilt_shift_blur.glsl"' not in host
    assert (
        'baseline_feature_tilt_shift = not selected_baseline_features.has('
        '"tilt_shift_off")' in host
    )
    assert '"tilt_shift_off":' in host
    assert "baseline_feature_tilt_shift = not enabled" in host
    assert "u_out.add_id(output_rd)" in post
    assert "var output_rd := destination_rd" in post
    assert "u_source.add_id(source_rd)" in post
    assert "_dispatch_pixel_camera_present" not in host
    assert "pixel_camera_present.glsl" not in host


def test_tilt_shift_intensity_has_one_runtime_owner_and_visible_controls() -> None:
    host = HOST.read_text(encoding="utf-8")
    controls = CONTROLS.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    menu = MENU.read_text(encoding="utf-8")

    # The rejected subtle setting reached about 6.8 px (8 * 0.85). The shipped
    # owner now resolves 1.50 * 12 = 18 px without changing focus geometry.
    # Tuning constants live in the pipeline owner; the host keeps the live
    # scalar, its facade, and the receipt mirror.
    post = POSTPROCESS.read_text(encoding="utf-8")
    assert "const TILT_SHIFT_BASE_BLUR_RADIUS_PX := 12.0" in post
    assert "const TILT_SHIFT_BASE_BLUR_RADIUS_PX := 12.0" not in host
    assert "var tilt_shift_intensity: float = 1.50" in host
    assert "effective_radius_px := TILT_SHIFT_BASE_BLUR_RADIUS_PX * tilt_shift_intensity" in post
    assert "func set_tilt_shift_intensity(value: float) -> float:" in host
    assert '"tilt_shift_intensity": tilt_shift_intensity' in host

    # Brackets have one registry owner. The retired render-distance actions are
    # absent from the dispatch surface, and World forwards only to compositor.
    assert '"id": "tilt_shift_decrease", "key": KEY_BRACKETLEFT' in controls
    assert '"id": "tilt_shift_increase", "key": KEY_BRACKETRIGHT' in controls
    assert '"id": "render_near"' not in controls
    assert '"id": "render_far"' not in controls
    assert '"tilt_shift_decrease":\n\t\t\t_call_ascii_tilt_shift_control(-1)' in world
    assert '"tilt_shift_increase":\n\t\t\t_call_ascii_tilt_shift_control(1)' in world

    # The menu reads and writes the same compositor scalar. Reopening it
    # refreshes bracket-key changes instead of displaying stale local state.
    assert "var tilt_shift_apply_callback: Callable" in menu
    assert "var tilt_shift_read_callback: Callable" in menu
    assert 'label.text = "Tilt shift intensity  [ / ]"' in menu
    assert "_sync_tilt_shift_from_owner()" in menu
    assert 'menu.tilt_shift_apply_callback = func(intensity: float)' in world
    assert "effect.call(\"set_tilt_shift_intensity\", intensity)" in world
    assert GODOT_CONTROL_TEST.is_file()


def test_both_projections_use_one_fused_postprocess() -> None:
    from scripts.tests.test_fl4057_perspective_probe_pipeline_contract import _gdscript_function_source
    host = HOST.read_text()
    post = POSTPROCESS.read_text()
    render = _gdscript_function_source(host, '_render_callback')
    assert render.count('post_process.dispatch(') == 1
    assert '_dispatch_tilt_shift_blur' not in host
    assert 'var postprocess_required := presentation_compensation_active' in render
    assert 'pp_frame.source_rd = completed_paint_rd' in render
    assert 'pp_frame.destination_rd = color_image' in render
    assert 'pp_frame.output_size = size' in render
    assert 'pixel_ctx.style_post_shift_px if presentation_compensation_active else Vector2.ZERO' in render
    assert 'direct_tilt_shift' not in render
    assert '_dispatch_pixel_camera_present' not in host
    assert not (BLUR.parent / 'pixel_camera_present.glsl').exists()
    dispatch = _gdscript_function_source(post, 'dispatch')
    assert dispatch.index('if output_rd == source_rd:') < dispatch.index('_rd.compute_list_begin()')
    assert 'rd.compute_list_dispatch(cl, (output_size.x + 7) / 8, (output_size.y + 7) / 8, 1)' in dispatch
    assert 'push.resize(64 if trace else 48)' in post
    shader = BLUR.read_text()
    assert '(vec2(dst) + vec2(0.5) - params.present.xy) / extent' in shader
    assert 'float row01 = (float(pixel.y) + 0.5)' in shader
    assert 'imageStore(out_color_image, dst,' in shader


def test_fused_postprocess_and_trace_compile(tmp_path) -> None:
    from scripts.tests.test_fl4057_perspective_probe_pipeline_contract import compiler, compile_stage, split_stages
    tool = compiler()
    source = BLUR.read_text()
    for trace in (False, True):
        variant = source.replace('#version 450', '#version 450\n#define FL4784_POSTPROCESS_TRACE') if trace else source
        for stage, body in split_stages(variant).items():
            compile_stage(tool, stage, body, tmp_path)


def test_removed_zero_shift_present_maps_each_pixel_to_itself() -> None:
    import numpy as np
    # Execute the existing present operator's coordinate math in float32.
    # The direct path is limited to equal extents and zero shift. Include the
    # live odd-height raster and clipped native-cell boundaries.
    for extent in (1, 7, 71, 2252, 2272, 3600, 8191):
        positions = np.arange(extent, dtype=np.float32)
        half = np.float32(0.5) / np.float32(extent)
        uv = np.clip((positions + np.float32(0.5)) / np.float32(extent), half, 1 - half)
        sampled = np.floor(uv * np.float32(extent)).astype(np.int64)
        assert np.array_equal(sampled, np.arange(extent))
    # A nearest copy between equal rgba16f images adds no finite paint values.
    bits = np.arange(65536, dtype=np.uint16)
    values = bits.view(np.float16)
    finite = np.isfinite(values)
    copied = values[finite].astype(np.float32).astype(np.float16).view(np.uint16)
    assert np.array_equal(copied, bits[finite])
