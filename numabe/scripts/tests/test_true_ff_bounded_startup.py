#!/usr/bin/env python3
"""FL-4877: True FF startup paints and advances bounded preparation jobs.

These are offline ownership/schedule contracts. They do not claim that a
headed frame rendered; that remains the final runtime proof after this repair
boundary passes its offline gates.

Run: python3 -m pytest scripts/tests/test_true_ff_bounded_startup.py -q
"""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASCII = ROOT / "godot_project/render/ascii"
DRIVER = ASCII / "composition/TrueFFCompositorEffect.gd"
FRAME = ASCII / "composition/TrueFFFrameRenderer.gd"
PASSES = ASCII / "features/TrueFFFeaturePasses.gd"
FACTORY = ROOT / "godot_project/render/core/device/RenderPipelineFactory.gd"
WORLD = ROOT / "godot_project/scripts/World.gd"
MAIN = ROOT / "godot_project/scripts/Main.gd"
MAIN_SCENE = ROOT / "godot_project/scenes/Main.tscn"


def _function(source: str, name: str) -> str:
    start = source.index(f"func {name}(")
    end = source.find("\nfunc ", start + 10)
    return source[start:] if end < 0 else source[start:end]


def test_family_program_bank_is_bounded_but_published_atomically() -> None:
    source = PASSES.read_text(encoding="utf-8")
    prepare = _function(source, "_prepare_family_programs")
    assert "_family_program_builds.get(mask, {})" in prepare
    assert "_family_program_cache.has(cache_key)" in prepare
    assert "_family_program_cache[cache_key] = program" in prepare
    assert prepare.index("compiled >= compile_budget") < prepare.index(
        "Factory.compile_compute")
    assert "compiled += 1" in prepare
    assert prepare.index("_family_programs[mask] = variants") < prepare.rindex(
        "_family_program_builds.erase(mask)")
    assert "family_pipeline_preparation_pending" in prepare
    assert "_family_program_builds.clear()" in _function(source, "release")
    assert "for program: Dictionary in _family_program_cache.values():" in _function(
        source, "release")


def test_all_runtime_family_masks_are_warmed_before_ready() -> None:
    passes = PASSES.read_text(encoding="utf-8")
    frame = FRAME.read_text(encoding="utf-8")
    warm = _function(passes, "_prepare_all_family_programs")
    ensure = _function(passes, "_ensure_sets")
    assert "for mask in range(8):" in warm
    assert "_prepare_family_programs(mask, mask_budget)" in warm
    assert "_prepare_all_family_programs(compile_budget)" in ensure
    assert "deferred_family" not in passes
    assert "prepare_residency(device, scene, {}, 1)" in _function(
        frame, "prepare_residency_step")
    assert "prepare_residency(device, scene, {}, 0)" in _function(
        frame, "retry_contained_residency")
    assert "_passes.prepare_views(" in _function(frame, "begin_source_frame")
    assert "_prepass_facts(scene), view_count, 0" in _function(
        frame, "begin_source_frame")


def test_priority_programs_are_prepared_before_optional_enhancements() -> None:
    source = PASSES.read_text(encoding="utf-8")
    order = _function(source, "_program_preparation_order")
    assert order.index("for path: String in PRIORITY_PROGRAMS:") < order.index(
        "for path: String in PROGRAMS:"
    )
    assert "path != NATIVE_PATH" in order
    prepare = _function(source, "_prepare_family_programs")
    status = _function(source, "family_program_preparation_status")
    assert "for path: String in _program_preparation_order():" in prepare
    assert "for path: String in _program_preparation_order():" in status


def test_frame_residency_base_is_not_replayed_for_each_shader() -> None:
    source = FRAME.read_text(encoding="utf-8")
    prepare = _function(source, "prepare_residency")
    assert prepare.index("requested_stamp == _residency_build_stamp") < prepare.index(
        "_figures.prepare(figure_scene)")
    assert "return _continue_residency_build(scene, compile_budget)" in prepare
    step = _function(source, "prepare_residency_step")
    assert "prepare_residency(device, scene, {}, 1)" in step
    continuation = _function(source, "_continue_residency_build")
    for replayed_base_work in ("_figures.prepare(", "_bank.prepare_residency(",
                               "_gbuffer.prepare_scene("):
        assert replayed_base_work not in continuation


def test_only_true_ff_driver_paints_startup_before_scheduling_work() -> None:
    source = DRIVER.read_text(encoding="utf-8")
    request = _function(source, "prepare_output")
    assert "RenderingServer.call_on_render_thread" not in request
    assert "_page_preparation_pending = true" in request
    callback = _function(source, "_render_callback")
    assert callback.index("_paint_startup(") < callback.index("_queue_page_preparation()")
    assert callback.index("_paint_startup(") < callback.index("_queue_residency_step()")
    assert "_renderer.prepare_residency" not in callback
    paint = _function(source, "_paint_startup")
    assert "_paint_surface(rd, scene_buffers" in paint
    page_schedule = _function(source, "_schedule_page_preparation")
    assert "RenderingServer.call_on_render_thread(_prepare_output.bind(" in page_schedule
    schedule = _function(source, "_schedule_residency_step")
    assert "RenderingServer.call_on_render_thread(_prepare_scene_residency_step)" in schedule
    output = _function(source, "_prepare_output")
    assert "_renderer.prepare_page(rd, output_size)" in output
    assert "generation != _page_request_generation" in output
    assert "_prepare_scene_residency()" not in output


def test_rejected_frames_preserve_a_current_page_or_paint_an_explicit_surface() -> None:
    source = DRIVER.read_text(encoding="utf-8")
    callback = _function(source, "_render_callback")
    assert callback.count("_paint_rejected_surface(") == 4
    assert "_renderer.paint_degraded_frame(" in callback
    assert "_paint_paper_surface(" not in callback
    assert callback.count("_paint_rejected_view(") == 2
    assert 'if not bool(result.get("page_painted", false)):' in callback
    surface = _function(source, "_paint_rejected_surface")
    view = _function(source, "_paint_rejected_view")
    assert "PageBackground.FAILURE_SURFACE" in surface
    assert "PageBackground.FAILURE_SURFACE" in view
    assert "_clear_color_image" in view
    frame = FRAME.read_text(encoding="utf-8")
    render = _function(frame, "render_frame")
    assert render.count("_fail_with_rejected_surface(") == 7
    assert "_fail_with_paper(" not in render
    assert 'rejected["page_painted"] = true' in render
    assert '"surface_state": "page_with_features"' in render
    fail = _function(frame, "_fail")
    assert '"page_painted": false' in fail
    signal = _function(frame, "_fail_with_rejected_surface")
    assert "PageBackground.FAILURE_SURFACE" in signal
    assert '"surface_state"] = "failure_surface"' in signal


def test_startup_failure_and_progress_are_operator_visible() -> None:
    source = DRIVER.read_text(encoding="utf-8")
    report = _function(source, "_publish_startup_status")
    assert "TRUE_FF_STARTUP" in report
    assert 'status.get("completed"' in report
    assert 'status.get("total"' in report
    assert 'status.get("cache_hits"' in report
    assert 'status.get("fresh_builds"' in report
    assert 'status.get("next_path"' in report
    failure = _function(source, "_fail_startup")
    assert "push_error" in failure
    assert '"failed": true' in failure


def test_startup_has_a_named_canvas_screen_and_reports_persistent_cache_reuse() -> None:
    driver = DRIVER.read_text(encoding="utf-8")
    passes = PASSES.read_text(encoding="utf-8")
    factory = FACTORY.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    main = MAIN.read_text(encoding="utf-8")
    scene = MAIN_SCENE.read_text(encoding="utf-8")

    publish = _function(driver, "_publish_startup_status")
    status = _function(passes, "family_program_preparation_status")
    sync = _function(main, "_sync_true_ff_loading_screen")
    assert "signal startup_status_changed(status: Dictionary)" in driver
    assert 'call_deferred("_emit_startup_status", status.duplicate(true))' in publish
    assert 'effect.connect("startup_status_changed", _accept_true_ff_startup_status)' in world
    assert "func true_ff_startup_status() -> Dictionary:" in world
    assert '"persistent_cache_hit": cache_hit' in factory
    for field in ('"cache_hits"', '"fresh_builds"', '"remaining"'):
        assert field in status
    assert "$TrueFFLoading/Panel/Progress" in main
    assert "PERSISTENT CACHE" in sync
    assert '[node name="TrueFFLoading" type="CanvasLayer" parent="."]' in scene
    assert '[node name="Progress" type="ProgressBar" parent="TrueFFLoading/Panel"]' in scene
    startup_paint = _function(driver, "_paint_startup")
    assert "0.18 - 0.10 * progress" not in startup_paint


def test_optional_owed_and_shadow_banks_publish_typed_zero_output() -> None:
    host = PASSES.read_text(encoding="utf-8")
    owed_abi = (ASCII / "features/true_ff_owed_admission.glslinc").read_text(
        encoding="utf-8")
    shared = (ASCII / "features/true_ff_feature_passes.glslinc").read_text(
        encoding="utf-8")
    discover = (ASCII / "features/true_ff_feature_discover.glsl").read_text(
        encoding="utf-8")
    native = (ASCII / "features/true_ff_feature_native.glsl").read_text(
        encoding="utf-8")
    owed_common = (ASCII / "features/true_ff_feature_owed_common.glslinc").read_text(
        encoding="utf-8")
    owed_dispatch = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ASCII / "features").glob("true_ff_feature_owed*.glsl"))
    )

    assert "const TFF_OWED_OMITTED: int = 5" in host
    assert "_owed_backstop_omitted = not _owed_programs_available()" in host
    assert "if _owed_backstop_omitted or not _owed_stage_active(stage):" in host
    assert "TFF_OWED_OMITTED, 0, 0, 0" in host
    assert '"owed_backstop_omitted": _owed_backstop_omitted' in host
    assert "const uint TFF_OWED_OMITTED=5u;" in owed_abi
    assert "if(omitted) {reserve=0u;return true;}" in shared
    assert "if(omitted) reserve=0u;" in discover
    assert "bool owed_omitted=owed.control.status.x==TFF_OWED_OMITTED;" in native
    assert "owed.control.status=uvec4(TFF_OWED_OMITTED,0u,0u,0u);" in owed_common
    capture_error = owed_common.split("void owed_capture_error", 1)[1].split("\n}", 1)[0]
    assert "TFF_SLOT_META*4u+1u" not in capture_error
    assert "TFF_SLOT_META*4u+3u" in capture_error
    late_error = owed_common.split("void owed_error", 1)[1].split("\n}", 1)[0]
    assert "native_plan.dispatch=uvec4(0u);" not in late_error
    assert "atomicOr(owed.control.status.z,1u);" in late_error
    assert "owed.control.status=uvec4(TFF_OWED_OMITTED,0u,0u,0u);" not in late_error
    assert "atomicExchange(owed.control.status.x,TFF_OWED_OMITTED)" in late_error
    assert "owed.control.status.y=" not in late_error
    assert late_error.index("if((bit&TFF_ERROR_SCENE_SOURCE)!=0u)") < late_error.index(
        "atomicOr(owed.control.status.w,bit)")
    assert "owed.control.status.x!=TFF_OWED_OMITTED" in owed_dispatch

    assert "_shadow_boundary_omitted = not _pass_available(SHADOW_PATH)" in host
    assert "args_bytes.encode_u32(SHADOW_PLAN_BYTE_OFFSET, 0x80000000 | _epoch)" in host
    assert '"shadow_boundary_omitted": _shadow_boundary_omitted' in host

    renderer = (ASCII / "features/TrueFFFeatureRenderer.gd").read_text(encoding="utf-8")
    assert "var reportable: bool = upstream != 0 or layout != 0 or obligations != 0" in renderer
    assert "true_ff_gpu_items_omitted:" in renderer

    scene = (ASCII / "features/true_ff_feature_discover.glsl").read_text(
        encoding="utf-8")
    scene_admission = (ASCII / "features/true_ff_scene_admission.glslinc").read_text(
        encoding="utf-8")
    item_error = scene.split("void scene_item_error", 1)[1].split("\n}", 1)[0]
    assert "TFF_SLOT_META * 4u + 1u" not in item_error
    assert "TFF_SLOT_META * 4u + 3u" in item_error
    assert scene.count("scene_item_error(TFF_ERROR_SCENE_SOURCE") >= 10
    append = scene.split("bool append(uint slot", 1)[1].split("\n}", 1)[0]
    assert "atomicMin(args[slot * 4u + 3u],cam.caps0.x);" in append
    assert "scene_capacity_error(slot);" in append
    assert "TFF_SLOT_META*4u+1u],TFF_ERROR_SCENE_SOURCE" not in scene_admission


def test_item_contract_failures_cannot_poison_the_frame_certificate() -> None:
    shared = (ASCII / "features/true_ff_feature_passes.glslinc").read_text(
        encoding="utf-8")
    contract = shared.split("void tff_contract_error", 1)[1].split("\n}", 1)[0]
    append = shared.split("bool tff_append", 1)[1].split("\n}", 1)[0]
    assert "TFF_SLOT_META * 4u + 1u" not in contract
    assert "TFF_SLOT_META * 4u + 3u" in contract
    assert "TFF_ERROR_SCENE_SOURCE" in contract
    assert "slot < 24u ? (1u << slot) : 0u" in contract
    capacity = shared.split("void tff_capacity_report", 1)[1].split("\n}", 1)[0]
    assert "slot < 24u ? (1u << slot) : 0u" in capacity
    assert "atomicMin(args[slot * 4u + 3u], cap);" in append
    assert "bool tff_current_normal" in shared
    assert "bool tff_source_local_at" in shared

    direct_global_scene_error = "TFF_SLOT_META*4u+1u],TFF_ERROR_SCENE_SOURCE"
    for path in (ASCII / "features").glob("true_ff_*.glsl*"):
        assert direct_global_scene_error not in path.read_text(encoding="utf-8"), path.name

    visibility = (ASCII / "features/true_ff_feature_visibility.glsl").read_text(
        encoding="utf-8")
    native = (ASCII / "features/true_ff_feature_native.glsl").read_text(
        encoding="utf-8")
    assert "tff_contract_error(TFF_SLOT_SITES);return;" in visibility
    duplicate = native.split("// A duplicated source", 1)[1].split("owners=kept", 1)[0]
    assert "TFF_SLOT_META*4u+1u" not in duplicate
    assert "drop_owner(site);continue;" in duplicate

    discover = (ASCII / "features/true_ff_feature_discover.glsl").read_text(
        encoding="utf-8")
    scene_capacity = discover.split("void scene_capacity_error", 1)[1].split(
        "\n}", 1)[0]
    assert "slot < 24u ? (1u << slot) : 0u" in scene_capacity

    scene_ingress = (ASCII / "features/true_ff_feature_scene.glsl").read_text(
        encoding="utf-8")
    update_source = scene_ingress.split("void update_source", 1)[1].split(
        "\n}", 1)[0]
    assert "topology_failure()" not in update_source
    assert "source_event_error()" in update_source
    assert "invalid?2.0:(known?0.0:1.0)" in update_source
