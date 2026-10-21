"""Rain dispatch source contracts, not temporal or rendered rain acceptance."""
import hashlib
from pathlib import Path
import re


SOURCE = Path(__file__).resolve().parents[2] / "godot_project/render/product/cell_owner_product.glsl"
# Display math at the observer-free handoff. The display member selector takes
# captured inputs only (root, receiver, wind front and bend); the retired
# live-observer stamp is the shape this hash rejects. Keep the rest pinned.
SOURCE_EFFECTS_APPLY_HASH = "74ffbf0cbabd3b887e5fe0f9dcec52401d7af799c5b6a2e3ec583871bb0484b0"


def _function_body(source, name):
    start = source.index("{", source.index(name + "("))
    depth = 1
    end = start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return re.sub(r"//[^\n]*", "", source[start + 1:end - 1])


def body(name):
    from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes
    return _function_body(_expand_includes(SOURCE.read_text()), name)


def _compact(source):
    return re.sub(r"\s+", "", source)


def _normalized_hash(source):
    return hashlib.sha256(_compact(source).encode()).hexdigest()


def test_outer_finalizer_admits_ground_rain():
    # The ground-rain admission lives in product_present_surface, called on
    # the outer finalizer's tail path for every non-retained-weather quarter.
    # The guard follows the call: admission here, delegation there.
    presentation = body("product_present_surface")
    assert re.search(
        r"if\s*\(cell_owner_weather_is_rain\(weather_kind\)\s*"
        r"\|\|\s*\(record.r0.y != OWNER_TERRAIN && record.r0.y != OWNER_WATER\)\)",
        presentation,
    )
    assert "cell_owner_apply_weather_paint(" in presentation
    finalizer = body("vsb_product_finalize_record")
    assert "return product_present_surface(observer, record, input_value," in finalizer


def test_air_dispatch_does_not_repeat_ground_wetting_or_skip_ground():
    rain = body("cell_owner_apply_weather_paint").split(
        "if ((weather_flags & PAINT_FLAG_RAIN) != 0u)", 1
    )[1].split("if ((weather_flags & PAINT_FLAG_SNOW)", 1)[0]
    assert "return" not in rain
    assert "cell_owner_apply_ground_weather_paint" not in rain
    # Air rain is retained dynamic geometry, not a second screen-ray painter.
    assert "cell_owner_apply_rain_air(" not in rain
    assert "float ripple = 0.0" not in rain
    assert "paint_kind == OWNER_STATIC_MESH || paint_kind == OWNER_DYNAMIC_MESH" in rain
    assert rain.count("cell_owner_apply_rain_wetting(") == 1


def test_ground_and_mesh_share_time_independent_wetting():
    for name in ("q3_probe_payload_apply_weather", "cell_owner_apply_ground_weather_paint",
                 "cell_owner_apply_weather_paint"):
        assert body(name).count("cell_owner_apply_rain_wetting(") == 1
    wet = body("cell_owner_apply_rain_wetting")
    for forbidden in ("params.", "glyph", "ray", "depth", "time"):
        assert forbidden not in wet
    assert "kind == 4u || kind == 5u" in body("cell_owner_weather_is_rain")


def test_compiled_entry_points_separate_capture_from_temporal_paint(tmp_path):
    """Inspect actual compiled function calls, not an unused helper's presence."""
    import shutil
    import subprocess
    import pytest

    compiler = shutil.which("glslangValidator")
    disassembler = shutil.which("spirv-dis")
    if not compiler or not disassembler:
        pytest.skip("GLSL/SPIR-V tools required for compiled call-graph check")
    from scripts.tests.test_fl4359_rain_snow_unowned_bg import _expand_includes
    source = _expand_includes(SOURCE.read_text()).split("\n", 1)[1]
    modules = {}
    for variant in ("FL4359_TEXEL_SHADE", "FL4359_SOURCE_EFFECTS"):
        output = tmp_path / (variant + ".spv")
        subprocess.run([compiler, "--stdin", "-S", "comp", "-V", "-D" + variant + "=1",
                        "-o", str(output)], input=source, text=True, capture_output=True, check=True)
        assembly = subprocess.run([disassembler, str(output)], text=True,
                                  capture_output=True, check=True).stdout
        assert _descriptor_collisions(assembly) == {}, variant
        names = dict(re.findall(r'OpName\s+(%\S+)\s+"([^"(]+)', assembly))
        calls = re.findall(r"OpFunctionCall\s+%\S+\s+(%\S+)", assembly)
        modules[variant] = {names.get(call, call) for call in calls}
        if variant == "FL4359_SOURCE_EFFECTS":
            main_id = next(key for key, name in names.items()
                           if name == "fl4359_source_effects_main")
            helper_id = next(key for key, name in names.items()
                             if name == "fl4359_source_effects_apply")
            main_body = assembly.split(main_id + " = OpFunction ", 1)[1].split(
                "OpFunctionEnd", 1)[0]
            main_calls = [names.get(call, call) for call in re.findall(
                r"OpFunctionCall\s+%\S+\s+(%\S+)", main_body)]
            assert main_calls == ["source_display_publish"]
            assert helper_id in assembly
        if variant == "FL4359_TEXEL_SHADE":
            water_id = next(key for key, name in names.items()
                            if name == "vsb_product_prepare_water")
            water_body = assembly.split(water_id + " = OpFunction ", 1)[1].split(
                "OpFunctionEnd", 1)[0]
            water_calls = re.findall(r"OpFunctionCall\s+%\S+\s+(%\S+)", water_body)
            # EYE and retained probes must not choose different shoreline
            # sample positions because of their source epoch.
            assert "fl4359_visible_source_epoch" not in {
                names.get(call, call) for call in water_calls}
    captured = modules["FL4359_TEXEL_SHADE"]
    temporal = modules["FL4359_SOURCE_EFFECTS"]
    # Rain/drop geometry has its retained capture owner. Neither effects
    # specialization may resurrect the retired procedural screen-ray rain.
    for name in ("cell_owner_rain_segment_hit", "cell_owner_rain_ripple",
                 "cell_owner_rain_air_drop"):
        assert name not in captured, (name, "retired procedural rain in capture")
        assert name not in temporal, (name, "retired procedural rain in effects")
    assert "product_vegetation_fog_pair" not in captured
    assert "product_vegetation_fog_pair" in temporal
    assert "cell_owner_apply_rain_wetting" in captured
    assert "cell_owner_apply_rain_wetting" not in temporal
    assert "product_vegetation_exposure_pair" not in temporal
    assert not any(name.startswith(("vsb_", "q3_", "scene_query")) for name in temporal)


def test_effects_helper_preserves_reviewed_math_and_rooted_ink_transport():
    helper = body("fl4359_source_effects_apply")
    # Display member selection is a pure function of captured inputs. No
    # observer, raster extent, projection, or screen mapping may enter the
    # glyph decision: the retired live-observer stamp re-selected strokes on
    # camera-only movement while base paint was unchanged.
    meadow = helper.split("if (meadow)", 1)[1].split(
        "if (needs_atmospheric_transport)", 1)[0]
    assert "glyph = product_meadow_member(root, world," in meadow
    assert "vegetation_pose.w, vegetation_pose.xyz" in meadow
    for forbidden in ("observer", "native_extent", "raster", "inv_view_proj",
                      "world_to_pixel", "projection", "extent", "camera_"):
        assert forbidden not in meadow, forbidden
    # The author returns structural decision provenance. This display caller
    # still consumes exactly the same glyph/role result and paint math.
    comparison = helper.replace("uvec3 stamp_decision;", "")
    comparison = comparison.replace(
        "glyph = product_meadow_member(root, world,\n\t\t\tvegetation_pose.w, vegetation_pose.xyz, colour_role, stamp_decision);",
        "glyph = 0u;")
    assert _normalized_hash(comparison) == SOURCE_EFFECTS_APPLY_HASH
    assert "vec3 receiver_delta = world - ray.near_world;" in helper
    assert "receiver_delta / receiver_distance" in helper
    assert helper.index("receiver_delta / receiver_distance") < helper.index(
        "product_cloud_air_column_fog_fact(ray, world)")
    assert "dot(world - ray.near_world, ray.dir)" not in helper
    assert "SampleEffectsCell" not in helper
    assert "imageStore(" not in helper
    # Source ink axes travel in the alpha bytes until splat projects them.
    for channel in ("y", "z"):
        assert f"payload.{channel} & 0xff000000u" in helper


def test_paired_effects_compare_all_inputs_and_store_both_results():
    # The executable publisher contract checks every tuple lane and demand mask.
    source = (SOURCE.parent / "probe_source_display.glslinc").read_text()
    for binding in (26, 27, 28, 29):
        assert f"binding = {binding}" in source
    comparison = body("fl4359_source_display_equal")
    for field in ("paint", "world", "anchor", "vegetation_pose"):
        assert f"a.{field}" in comparison and f"b.{field}" in comparison
    publish = body("source_display_publish")
    assert "fl4359_source_display_equal(front, under)" in publish
    assert "imageStore(source_effects_output, address, front_paint);" in publish
    assert "imageStore(source_effects_under_output, address, under_paint);" in publish
    assert "imageLoad(" not in source


def test_effects_are_shared_before_front_and_underlay_splat():
    host = (SOURCE.parents[1] / "render/api/ASCIICompositorEffect.gd").read_text()
    pipeline = (SOURCE.parents[1] / "render/backends/perspective/PerspectiveProbeBackend.gd").read_text()
    transaction = pipeline.split("func execute_frame_transaction(", 1)[1].split("\nfunc ", 1)[0]
    effects = transaction.index('"perspective_dispatch_display_paint"')
    splat = transaction.index("draw_and_publish()", effects)
    assert 'call("splat_candidates",' not in host
    assert effects < splat
    assert host.count('"execute_frame_transaction"') == 1
    dispatch = host.split("func perspective_dispatch_display_paint(", 1)[1].split("\nfunc ", 1)[0]
    assert "_ensure_fl4359_source_effects_outputs(probe_size, layer_count)" not in dispatch
    prepare = host.split("func perspective_prepare_display_outputs(", 1)[1].split("\nfunc ", 1)[0]
    assert "_ensure_fl4359_source_effects_outputs(probe_size, layer_count)" in prepare
    assert 'receipt.get("active_splat_layers", [])' in dispatch
    allocator = host.split("func _allocate_fl4359_source_effects_outputs(", 1)[1].split("\nfunc ", 1)[0]
    assert "_release_fl4359_source_effects_outputs()" in allocator
    assert "TEXTURE_TYPE_2D_ARRAY" in allocator
    ensure = host.split("func _ensure_fl4359_source_effects_outputs(", 1)[1].split("\nfunc ", 1)[0]
    assert "extent == _fl4359_source_effects_extent" in ensure
    assert 'receipt.get("probe_underlay_texel_shade", {})' in dispatch
    for marker in (
        '_fl4359_source_effects_payloads.size() != 2',
        '_fl4359_source_effects_payloads[0] == _fl4359_source_effects_payloads[1]',
    ):
        assert marker in dispatch, marker
    # Front and underlay share the survivor-driven uniform builder.
    arms = host.split("func _dispatch_fl4359_source_effects_uniforms(", 1)[1].split("\nfunc ", 1)[0]
    for marker in (
        '26 if underlay else 92',
        '27 if underlay else 94',
        '28 if underlay else 98',
        '29 if underlay else 95',
        'for arm in range(2):',
        'if not texture.is_valid() or _fl4359_source_effects_payloads.has(texture)',
    ):
        assert marker in arms, marker
    assert 'for field in ["probe_size", "layer_count", "generation", "layer_mask"]:' in dispatch
    assert 'int(shade.get(field, -1)) != int(under_shade.get(field, -2))' in dispatch
    assert 'not bool(shade.get("valid", false))' in dispatch
    assert 'not bool(under_shade.get("valid", false))' in dispatch
    assert '"probe_underlay_texel_shade" if underlay else "probe_texel_shade"' in arms
    assert 'receipt["probe_resolve_rid"] =' not in dispatch
    assert "compute_list_dispatch_indirect(" in arms
    assert "compute_list_dispatch(" not in arms
    arm_loop = arms.index("for arm in range(2):")
    frame_uniform = arms.index("var frame_uniform := RDUniform.new()")
    compute_begin = arms.index("var compute := rd.compute_list_begin()")
    assert arm_loop < frame_uniform < compute_begin
    assert arms.count("rd.compute_list_begin()") == 1
    assert arms.count("rd.compute_list_dispatch_indirect(") == 1
    assert dispatch.count("rd.compute_list_begin()") == 0


def _descriptor_collisions(assembly):
    """Godot reflects declared descriptors, including unused shared includes."""
    names = dict(re.findall(r'OpName\s+(%\S+)\s+"([^"\n]+)"', assembly))
    locations = {}
    for variable, kind, number in re.findall(
        r"OpDecorate\s+(%\S+)\s+(DescriptorSet|Binding)\s+(\d+)", assembly
    ):
        locations.setdefault(variable, {})[kind] = int(number)
    owners = {}
    for variable, location in locations.items():
        if "DescriptorSet" in location and "Binding" in location:
            key = (location["DescriptorSet"], location["Binding"])
            owners.setdefault(key, []).append(names.get(variable, variable))
    return {key: value for key, value in owners.items() if len(value) > 1}


def test_paired_effects_descriptors_do_not_alias_retained_geometry(tmp_path):
    """Reproduce the 20260911-221505 manual-run shader reflection rejection."""
    import shutil
    import subprocess
    import pytest
    compiler = shutil.which("glslangValidator")
    disassembler = shutil.which("spirv-dis")
    if not compiler or not disassembler:
        pytest.skip("GLSL/SPIR-V tools required for descriptor reflection check")
    from scripts.tests.test_fl4359_rain_snow_unowned_bg import _expand_includes
    source = _expand_includes(SOURCE.read_text()).split("\n", 1)[1]
    bad = source
    for current, rejected in ((26, 115), (27, 116), (28, 117), (29, 118)):
        bad = bad.replace(f"binding = {current}", f"binding = {rejected}")
    modules = {}
    for label, text in (("current", source), ("rejected", bad)):
        output = tmp_path / (label + ".spv")
        subprocess.run([compiler, "--stdin", "-S", "comp", "-V",
                        "-DFL4359_SOURCE_EFFECTS=1", "-o", str(output)],
                       input=text, text=True, capture_output=True, check=True)
        assembly = subprocess.run([disassembler, str(output)], text=True,
                                  capture_output=True, check=True).stdout
        modules[label] = _descriptor_collisions(assembly)
    assert modules["current"] == {}
    for binding, owner in ((115, "rigid_local"), (116, "rigid_poses"),
                           (117, "rigid_instances")):
        assert owner in modules["rejected"][(0, binding)]
