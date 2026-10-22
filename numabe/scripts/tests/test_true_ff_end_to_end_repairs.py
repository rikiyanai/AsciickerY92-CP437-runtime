"""FL-4907 source-only end-to-end repair controls.

CPU compilation executes the production row prefix/pack functions. These
synthetic fixtures do not execute Godot, native GPU admission or rendering.
"""
from pathlib import Path
import re
import shutil
import subprocess
import json

import pytest


ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"


def _function(source: str, name: str) -> str:
    body = source.split(f"func {name}(", 1)[1]
    return re.split(r"\n(?:static )?func ", body, maxsplit=1)[0]


def test_attempt106_blockers_are_structurally_contained():
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    renderer = (FEATURES / "TrueFFFeatureRenderer.gd").read_text()
    part = (FEATURES / "TrueFFPartRepresentation.gd").read_text()
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    compositor = (ROOT / "godot_project/render/ascii/composition/TrueFFCompositorEffect.gd").read_text()
    gbuffer = (ROOT / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd").read_text()

    signature = _function(passes, "_frame_signature")
    assert "var ids: RID" in signature and "var uniform_ids: Array[RID]" in signature
    assert "var ids: Array[RID]" not in signature
    assert '"content_hash": str(content_hash)' in bank
    assert 'str(asset.get("content_hash", ""))' in passes
    assert 'String(asset.get("content_hash"' not in passes
    path_mask = _function(passes, "_path_family_mask")
    assert "path in OWED_STAGE_PATHS.values()" in path_mask

    assert "func can_accept_source_events() -> bool:" in frame
    assert "and _feature_bank_omission.is_empty()" in _function(frame, "can_accept_source_events")
    assert "if _renderer.can_accept_source_events():" in compositor
    assert "contained_residency_retry_due" in frame and "retry_contained_residency" in frame

    configure = _function(gbuffer, "configure")
    assert "_optional_bank_fallback = _rd.storage_buffer_create" in configure
    assert '_banks[name] = _optional_bank_fallback' in configure
    assert '_omit_program("p0.water", _terrain_omission)' in configure
    assert "_heightfield_ubo = _params" in configure  # descriptor-only; water/terrain are omitted

    publish_omissions = _function(bank, "_publish_omissions")
    assert "_omission_reason_text(group[label])" in publish_omissions
    assert "return JSON.stringify(value)" in _function(bank, "_omission_reason_text")

    setup = _function(renderer, "_setup")
    assert setup.index("for i in range(text.length()):") < setup.index(
        "if omitted_sequence_indexes.has(index):")
    assert 'product.get("omissions", {})' in setup
    assert 'timing_product.get("omissions", {})' in setup
    assert '"omissions": omissions' in part


def test_attempt109_effect_replay_and_terrain_degrade_are_explicit():
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    compositor = (ROOT / "godot_project/render/ascii/composition/TrueFFCompositorEffect.gd").read_text()
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    water = (FEATURES / "TrueFFWaterFeatures.gd").read_text()
    sky = (FEATURES / "TrueFFSkyFeatures.gd").read_text()

    for merge in (
        _function(compositor, "_merge_effect_events"),
        _function(frame, "_stash_effect_events"),
    ):
        assert "upserts.erase(object_value)" in merge
        assert "removed.erase(object_value)" in merge
        assert "removed.keys()" in merge
    begin = _function(frame, "begin_source_frame")
    assert '_bank.apply_changes("effects", _pending_effect_bank_changes)' in begin
    assert "_pending_effect_bank_changes = effect_changes.duplicate(true)" in begin
    assert '_apply_world_publication(effect_publication, "effects")' in begin

    prepare = _function(bank, "prepare_residency")
    sync = _function(bank, "_sync_terrain")
    topology = _function(bank, "terrain_topology")
    assert '"degraded": true' in prepare
    assert '"failure_reason": terrain_failure' in prepare
    assert 'not bool(_terrain_state.get("degraded", false))' in sync
    assert '"degraded": terrain_degraded()' in topology
    assert "return _bank.terrain_degraded()" in _function(
        frame, "has_contained_bank_failure")
    assert "water_plane_terrain_degraded:" in _function(water, "_retain_surface")
    assert "cloud_plane_terrain_degraded:" in _function(sky, "_prepare_cloud")


def test_attempt106_static_and_hud_failures_keep_valid_siblings():
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    renderer = (FEATURES / "TrueFFFeatureRenderer.gd").read_text()
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    visibility = (ROOT / "godot_project/render/ascii/structure/TrueFFStaticVisibility.gd").read_text()
    gbuffer = (ROOT / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd").read_text()
    static = _function(bank, "_prepare_static_hierarchy")
    assert "_static_leaf_mapping.fill(0xFF)" in static
    assert 'leaf_omissions["leaf:%d" % leaf]' in static
    assert "continue" in static and "_omit_static_hierarchy(\"static_tlas_leaf_identity_mismatch\")" not in static
    validate = _function(visibility, "_validate")
    for label in ("asset:%d", "leaf:%d", "node:%d"):
        assert label in validate
    assert 'omitted["p0.static_items"]' in _function(gbuffer, "publish")

    hud = _function(renderer, "render_hud")
    assert "_fallback_page(page)" in hud
    fallback_page = _function(renderer, "_fallback_page")
    for fallback in ("_fallback_float", "_fallback_uint", "_fallback_heightfield"):
        assert fallback in fallback_page
    render_frame = _function(frame, "render_frame")
    assert render_frame.count("_features.render_hud(") >= 3
    assert '"page_with_hud"' in render_frame


def test_malformed_optional_change_withdraws_only_its_producer():
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    apply = bank.split("func apply_changes(producer: String, changes: Dictionary) -> bool:", 1)[
        1
    ].split("\nfunc _withdraw_rejected_producer", 1)[0]
    # Only missing shared authority and an invalid producer identity remain
    # bank-wide faults. Every producer-owned envelope fault takes one boundary.
    assert apply.count("return _reject(") == 2
    assert 'return _reject("residency_not_prepared")' in apply
    assert 'return _reject("source_publication_owner_invalid")' in apply
    for reason in (
        "source_change_field_unknown",
        "source_change_dictionary_required",
        "source_change_array_required",
        "source_change_identity_invalid",
        "source_change_owner_mismatch",
        "deformation_change_invalid",
        "deformation_change_nonfinite",
        "deformation_removal_invalid",
        "bank_change_identity_invalid",
    ):
        assert f'_withdraw_rejected_producer(producer, "{reason}' in apply

    withdraw = bank.split("func _withdraw_rejected_producer(producer: String, reason: String) -> bool:", 1)[
        1
    ].split("\nfunc advance_source_tick", 1)[0]
    assert "_unset_desired(String(key_value))" in withdraw
    assert "_remove_provider(content_hash, label)" in withdraw
    assert "_invalid_shared.erase(content_hash)" in withdraw
    assert "_change_residency_owner(content_hash, producer, false)" in withdraw
    assert 'withdrawal["producer:" + producer] = reason' in withdraw
    assert "_producer_omissions[producer] = withdrawal" in withdraw
    assert "_commit_publication(scene_db) and ready()" in withdraw
    assert "_reject(" not in withdraw


def test_program_bank_omits_every_failed_feature_program():
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    gbuffer = (ROOT / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd").read_text()
    terrain_visibility = (ROOT / "godot_project/render/ascii/structure/TrueFFTerrainVisibility.gd").read_text()
    static_visibility = (ROOT / "godot_project/render/ascii/structure/TrueFFStaticVisibility.gd").read_text()
    assert "func program_omissions() -> Dictionary:" in passes
    assert "const PRIORITY_PROGRAMS := [" in passes
    assert "no programme can reject the bank" in passes
    configure = passes.split("func configure(rd: RenderingDevice) -> bool:", 1)[1].split(
        "\nfunc _prepare_family_programs", 1
    )[0]
    assert '_program_failures[path] = _omission_record(' in configure
    assert 'native pass omitted' in configure
    family_prepare = passes.split("func _prepare_family_programs(", 1)[1].split(
        "\nfunc family_program_preparation_pending", 1
    )[0]
    assert '"dependency", "native_program_omitted"' not in family_prepare
    assert '"dependency", "upstream_program_omitted:%s" % path' not in family_prepare
    assert '"withdrawal", "source_family_withdrawn"' in family_prepare
    assert "break\n" not in family_prepare
    native_set = passes.split("func _ensure_native_set(language: Dictionary) -> bool:", 1)[
        1
    ].split("\nfunc _dispatch_native_certificate", 1)[0]
    assert '_native_set_omission = _program_failures.get(NATIVE_PATH' in native_set
    assert '"uniform_set", "native_uniform_set_rejected", 0' in native_set
    reconcile = passes.split("func reconcile_publication(", 1)[1].split("\nfunc ", 1)[0]
    assert 'and not _program_failures.has(NATIVE_PATH)' in reconcile
    assert 'not _programs.has(NATIVE_PATH) or not _configure_failure' not in reconcile
    assert "func _feature_chain_available() -> bool:" not in passes
    priority = passes.split("const PRIORITY_PROGRAMS := [", 1)[1].split("\n]", 1)[0]
    for path in (
        "TRAVERSE_WITNESS_PATH",
        "TRAVERSE_SCENE_EDGES_PATH",
        "TRAVERSE_EDGES_PATH",
        "TRAVERSE_ELECTED_PATH",
        "TRAVERSE_WITNESS_GEOMETRY_PATH",
        "TRAVERSE_WITNESS_VISIBILITY_PATH",
        "TRAVERSE_WITNESS_ADVANCE_PATH",
    ):
        assert path in priority
    assert '"compile", "scene_discovery_program_missing"' in passes
    assert '"omissions": omissions' in passes
    assert '_set_omissions[path] = _omission_record(' in passes
    assert "_discovery_set_omission.is_empty()" in passes
    assert "_native_set_omission.is_empty()" in passes
    prepared = passes.split("func _prepared_sets_valid() -> bool:", 1)[1].split("\nfunc ", 1)[0]
    assert "if _set_omissions.has(path):" in prepared
    assert "and _discovery_set_omission.is_empty():" in prepared
    assert "and _native_set_omission.is_empty()" in prepared
    bind = passes.split("func _bind(", 1)[1].split("\nfunc ", 1)[0]
    assert "_discovery_set_omission.is_empty()" in bind
    assert "_set_omissions.has(path)" in bind
    assert '"omitted_programs": program_omissions()' in passes
    assert "mandatory_feature_pass_unavailable:" not in passes
    assert "func _retire_expired_sets() -> void:" in passes
    assert "_retire_expired_sets()" in passes
    assert 'passes_borrow_expired_prepare_required' not in passes
    assert "return _submission_receipt(bank, view_index, 0, 0, true)" not in passes
    assert '"residency_required": true' in frame
    assert '_prepass_facts(scene), view_index, 0' in frame
    source_view = frame.split("func prepare_source_view(", 1)[1].split("\nfunc ", 1)[0]
    assert "_features.prepared_for(" in source_view
    assert "_features.prepare(" not in source_view
    assert '"feature_chain_omitted": feature_chain_omitted' not in passes
    assert '"omitted_programs": _program_omissions()' in frame
    assert "var omissions: Dictionary = _background.program_omissions()" in frame
    assert "omissions.merge(_gbuffer.program_omissions(), true)" in frame
    assert "omissions.merge(_passes.program_omissions(), true)" in frame
    assert '_omit_program("p0.water", "gbuffer_water_compile_rejected")' in gbuffer
    assert '_omit_program("p0.shadow_merge", "gbuffer_light_merge_compile_rejected")' in gbuffer
    assert 'return _shader if _water_enabled(scene) else _dry_shader' in gbuffer
    prepare_scene = gbuffer.split("func _prepare_scene(", 1)[1].split("\nfunc ", 1)[0]
    assert "_dry_pipeline, _dry_pipeline_reverse" in prepare_scene
    assert "_pipeline, _pipeline_reverse" not in prepare_scene
    assert 'not _program_omissions.has("p0.shadow_merge")' in prepare_scene
    assert 'return _rd.buffer_clear(_camera, 0, bytes) == OK' in terrain_visibility
    assert 'allow_create: bool = false' in terrain_visibility
    assert 'terrain_visibility_prepared_set_expired' in terrain_visibility
    assert 'if not _omission_reason.is_empty():\n\t\treturn true' in terrain_visibility
    assert 'static_visibility_empty_output_clear_rejected' in static_visibility
    assert 'func _publish_empty_outputs() -> bool:' in static_visibility
    assert 'static_visibility_prepared_set_expired' in static_visibility
    assert 'if not _omission_reason.is_empty():\n\t\treturn true' in static_visibility
    assert 'if not _terrain_visibility.prepare(' in prepare_scene
    assert 'if not _light_terrain_visibility.prepare(' in prepare_scene
    assert 'or not _light_terrain_visibility.prepare(' not in prepare_scene
    assert '"gbuffer_shadow_terrain_visibility_prepare_rejected", "publication"' in prepare_scene


def test_mask_zero_sets_and_failed_programs_publish_usable_subsets():
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    ensure = gd_body(passes, "_ensure_sets")
    live_branch = ensure.split("if effective_mask != 0:", 1)[1].split(
        "\n\t\t_sets[path] = uniform_set", 1
    )[0]
    assert "_live_sets[path] = live_set" in live_branch
    assert "_sets[path] = uniform_set" not in live_branch
    assert "\n\t\t_sets[path] = uniform_set" in ensure
    prepare = gd_body(passes, "_prepare_family_programs")
    assert "_family_programs[mask] = variants" in prepare
    assert "mandatory_feature_program_rejected" not in prepare
    assert '_set_omissions[SHADOW_PATH] = _omission_record(' in ensure
    assert '"publication", "prepass_light_map_missing"' in ensure
    assert 'return _prepare_family_programs(_family_mask, compile_budget)' not in ensure
    assert "var publication_omissions: Dictionary = _set_omissions.duplicate(true)" in ensure
    assert "_set_omissions = publication_omissions" in ensure
    assert '"figure_binding_fallback": 16' in passes
    assert "var figure_bindings: RID = _figure_bindings_or_fallback(bank)" in ensure
    assert "vegetation_uniforms = _vegetation_uniforms_or_fallback(depth)" in ensure
    assert '"vegetation_fallback": maxi(int(bank.get("feature_total", 0)), 1) * 16' in passes
    vegetation_fallback = gd_body(passes, "_vegetation_uniforms_or_fallback")
    assert "for binding in [31, 32, 33, 34, 109, 110, 111, 112, 113]" in vegetation_fallback
    assert "RenderingDevice.UNIFORM_TYPE_SAMPLER_WITH_TEXTURE" in vegetation_fallback
    assert "RenderingDevice.UNIFORM_TYPE_UNIFORM_BUFFER, [counts]" in vegetation_fallback
    signature = gd_body(passes, "_frame_signature")
    assert "vegetation_valid" in signature
    assert "light_valid" in signature
    assert "figure_valid" in signature


def test_failed_p0_retry_is_targeted_and_residency_pending_is_exact():
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    gbuffer = (ROOT / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd").read_text()
    static_visibility = (
        ROOT / "godot_project/render/ascii/structure/TrueFFStaticVisibility.gd"
    ).read_text()
    retry = gd_body(gbuffer, "retry_failed_preparation")
    assert "configure(_rd" not in retry
    assert "_configuration_bank_omissions.keys()" in retry
    assert "_ensure_bank(name, MIN_BANK_BYTES)" in retry
    shadow_retry = gd_body(gbuffer, "_retry_shadow_allocation")
    assert "render_pipeline_create" not in shadow_retry
    assert "release_page" not in shadow_retry
    assert "_light_merge_set = merge_set" in shadow_retry
    assert "func has_recoverable_omission() -> bool:" in gbuffer
    step = gd_body(frame, "prepare_residency_step")
    assert "if _residency_step_pending:" in step
    assert "_passes._failure" not in step
    prepare = gd_body(frame, "prepare_residency")
    bank_prepare = prepare.index("_bank.prepare_residency")
    assert bank_prepare < prepare.index("_actors.withdraw")
    assert bank_prepare < prepare.index("_rigid.withdraw")
    assert "_pending_actor_withdrawal" in prepare
    assert "_pending_rigid_withdrawal" in prepare
    contained_retry = gd_body(frame, "retry_contained_residency")
    assert "terrain_retry: bool = _bank.terrain_degraded()" in contained_retry
    assert "not retained_bank_reason.is_empty() or terrain_retry" in contained_retry
    visibility_prepare = gd_body(static_visibility, "prepare")
    assert 'if _omission_reason not in ["static_visibility_compile_rejected",' in visibility_prepare
    assert '"static_visibility_pipeline_rejected"]:' in visibility_prepare


def test_runtime_size_and_source_failures_keep_a_current_page():
    compositor = (ROOT / "godot_project/render/ascii/composition/TrueFFCompositorEffect.gd").read_text()
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    callback = gd_body(compositor, "_render_callback")
    assert "var size: Vector2i = scene_buffers.get_internal_size()" in callback
    assert "if size != _requested_output_size:" in callback
    assert "prepare_output(size)" in callback
    assert "_renderer.paint_degraded_frame(" in callback
    render = gd_body(frame, "render_frame")
    assert 'rejected["surface_state"] = "page_background"' in render
    assert "_fail_with_rejected_surface(" not in render.split(
        "if not _render_features", 1
    )[1]


def test_every_true_ff_shader_compile_owner_has_an_explicit_residency_policy():
    ascii_root = ROOT / "godot_project/render/ascii"
    compile_owners = {
        path.relative_to(ascii_root).as_posix()
        for path in ascii_root.rglob("*.gd")
        if "Factory.compile_compute(" in path.read_text()
        or "Factory.compile_graphics(" in path.read_text()
    }
    assert compile_owners == {
        "composition/TrueFFPageBackground.gd",
        "features/TrueFFFeaturePasses.gd",
        "features/TrueFFFeatureRenderer.gd",
        "structure/TrueFFSceneGBuffer.gd",
        "structure/TrueFFStaticVisibility.gd",
        "structure/TrueFFTerrainVisibility.gd",
    }

    # Paper has a CPU-owned fallback publication. P5 and P6 publish independent
    # omissions, so either program can fail without rejecting the page owner.
    background = (ascii_root / "composition/TrueFFPageBackground.gd").read_text()
    renderer = (ascii_root / "features/TrueFFFeatureRenderer.gd").read_text()
    assert '_omit_program("true_ff_page_background_compile_rejected")' in background
    assert "func _fallback_dispatch(output: RID) -> Dictionary:" in background
    assert "_rd.texture_clear(output, FALLBACK_PAPER" in background
    assert '"page.paper": _program_omission.duplicate(true)' in background
    assert '_program_omissions["p5.layout"]' in renderer
    assert '_program_omissions["p6.glyphs"]' in renderer
    assert 'true_ff_feature_mandatory_program_rejected' not in renderer
    assert 'if _program_omissions.has("p5.layout")' in renderer
    assert 'if _program_omissions.has("p6.glyphs")' in renderer
    assert '_program_omissions.erase("p5.layout")' in renderer
    assert '_program_omissions.erase("p6.glyphs")' in renderer
    assert 'func program_omissions() -> Dictionary:' in renderer

    # P0 dry-raster loss publishes cleared attachments; enhancements omit too.
    gbuffer = (ascii_root / "structure/TrueFFSceneGBuffer.gd").read_text()
    assert '_omit_program("p0.scene", "gbuffer_dry_compile_rejected")' in gbuffer
    assert 'gbuffer_mandatory_scene_program_rejected' not in gbuffer
    assert 'gbuffer_mandatory_scene_uniform_set_rejected' not in gbuffer
    assert '"scene_raster_omitted": true' in gbuffer
    assert 'func _omit_scene_raster(reason: String, params_bytes: int) -> Dictionary:' in gbuffer
    assert 'return _omit_scene_raster("gbuffer_uniform_set_rejected", params.size())' in gbuffer
    assert '_omit_program("p0.water", "gbuffer_live_source_uniform_set_rejected")' in gbuffer
    assert '_omit_program("p0.water", "gbuffer_water_compile_rejected")' in gbuffer
    assert '_omit_program("p0.shadow_merge", "gbuffer_light_merge_compile_rejected")' in gbuffer
    publish = gbuffer.split("func publish(scene: Dictionary) -> Dictionary:", 1)[1].split(
        "\nfunc _publish_water", 1
    )[0]
    assert 'return _fail("gbuffer_rigid_primitive_upload_rejected")' not in publish
    assert 'scene["rigid_runtime_rejection"] = _rigid_primitive_omission' in publish
    assert 'return _fail("gbuffer_water_upload_rejected")' not in publish
    assert 'return _fail("gbuffer_dynamic_upload_rejected")' not in publish
    assert '_dynamic_omission = "gbuffer_dynamic_upload_rejected"' in publish
    assert 'return _fail("gbuffer_mover_bounds_publication_invalid")' not in publish
    assert '_dynamic_item_omission = "gbuffer_mover_bounds_publication_invalid"' in publish
    assert 'return _fail("gbuffer_actor_control_upload_rejected")' not in publish
    assert '_actor_control_omission = "gbuffer_actor_control_upload_rejected"' in publish
    assert '_actor_rows_omission = "gbuffer_actor_rows_upload_rejected"' in publish
    assert '_actor_instances_omission = "gbuffer_actor_instances_upload_rejected"' in publish
    assert '_actor_control_attempt_bytes = control.duplicate()' in publish
    assert '_actor_instance_attempt_bytes = actor_instances.duplicate()' in publish
    assert '_actor_control_omission = "gbuffer_actor_publication_api_missing"' in publish
    prepare = gbuffer.split("func _prepare_scene(scene: Dictionary) -> Dictionary:", 1)[1].split(
        "\nfunc prepared_for", 1
    )[0]
    assert 'return _fail("gbuffer_changing_capacity_prepare_rejected")' not in prepare
    capacity = gbuffer.split("func _changing_bank_byte_sizes(scene: Dictionary) -> Dictionary:", 1)[1].split(
        "\nfunc _preparation_stamp", 1
    )[0]
    assert 'movers = PackedByteArray()' in capacity
    assert 'actor_instances = PackedByteArray()' in capacity
    water = gbuffer.split("func _publish_water(", 1)[1].split("\nfunc _withdraw_water", 1)[0]
    assert '_withdraw_water("gbuffer_water_span_upload_rejected")' in water
    assert '_withdraw_water("gbuffer_water_backstop_upload_rejected")' in water


def test_optional_runtime_source_failure_withdraws_family_without_stopping_startup():
    world = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
    gbuffer = (ROOT / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd").read_text()
    prepare = world.split("func prepare_true_ff_world_sources() -> bool:", 1)[1].split(
        "\nfunc true_ff_scene_sources", 1
    )[0]
    assert '_true_ff_rigid_source_rejection = "rigid_mover_buffer_publication_failed"' in prepare
    assert '_true_ff_vegetation_source_rejection = vegetation_error' in prepare
    assert 'return true' in prepare
    assert 'push_error("True FF vegetation preparation:' not in prepare
    resident = world.split("func true_ff_resident_sources() -> Dictionary:", 1)[1].split(
        "\nfunc ", 1
    )[0]
    assert '"rigid_uniforms": [] if not _true_ff_rigid_source_rejection.is_empty()' in resident
    assert '"vegetation_runtime_rejection": _true_ff_vegetation_source_rejection' in resident
    assert 'if rigid_face_source and not rigid_runtime_omission.is_empty():' in gbuffer
    assert 'omitted["p0.rigid"] = _rigid_runtime_omission' in gbuffer


def function(source, name):
    start = re.search(rf"\b(?:void|uint|bool|vec4) {name}\(", source)
    assert start, name
    brace = source.index("{", start.start())
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start.start():end]


@pytest.fixture(scope="module")
def segmented_rows(tmp_path_factory):
    source = (FEATURES / "true_ff_feature_native.glsl").read_text()
    production = "\n".join(function(source, name) for name in
                           ("row_count_at", "row_list_at", "row_head_at", "row_base_at",
                            "segment_rows", "pack_row"))
    # Execute the exact uniform entry guard before an instrumented first read.
    # The DP/barrier body is not a CPU implementation of GPU synchronization.
    solve_entry = function(source, "solve").split("    while(padded<n)", 1)[0]
    production += "\n" + solve_entry + r'''
    for(uint index=0;index<n;++index)assert(row_items.items[base+index]<count());
}
'''
    code = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <numeric>
#include <vector>
using uint=uint32_t;
const uint NONE=0xffffffffu;
struct Checked {std::vector<uint> data; uint& operator[](uint at){return data.at(at);}};
struct {Checked words;} control;
struct {Checked items;} row_items;
struct Vector {uint x=0,y=0,z=0,w=0;};
struct {Vector stage,counts,native;} pc;
uint count(){return control.words[6];}
void pending(){control.words[4]=1;}
uint reported=0;void report_pending(){++reported;}
''' + production + r'''
int main(int argc,char**argv){
    assert(argc>=4);
    uint B=std::strtoul(argv[1],nullptr,10),rows=std::strtoul(argv[2],nullptr,10);
    int bad=std::atoi(argv[3]);
    pc.counts.z=B;pc.stage.y=rows;pc.native.w=2*B;
    control.words.data.assign(8+4*rows,0);
    row_items.items.data.assign(5*B,NONE);
    uint total=0,groups=0;
    std::vector<std::vector<uint>> expected(rows);
    for(uint row=0;row<rows;++row){
        uint n=std::strtoul(argv[4+row],nullptr,10);
        control.words[row_head_at(row)]=NONE;
        control.words[row_base_at(row)]=NONE;
        control.words[row_count_at(row)]=n;
        if(n)control.words[row_list_at(groups++)]=row;
        for(uint index=0;index<n;++index){
            uint site=total++;
            row_items.items[site]=control.words[row_head_at(row)];
            control.words[row_head_at(row)]=site;
            expected[row].push_back(site);
        }
    }
    assert(total<=B);control.words[3]=groups;control.words[6]=total;
    if(bad==1 && total)row_items.items[0]=0; // cycle cannot silently vanish
    if(bad==2 && groups)control.words[row_head_at(control.words[row_list_at(0)])]=NONE;
    if(bad==3){
        control.words[3]=rows+1; // bin roster/count contradiction is rejected before consumers
        segment_rows(0);pack_row(0,0);solve(0,0,false);
        assert(control.words[4]);return 0; // checked arrays reject any invalid read
    }
    if(bad==4){
        pc.native.w=4; // only the first row's padded segment fits the sort arena
        segment_rows(0);
        assert(control.words[row_base_at(control.words[row_list_at(0)])]==B);
        assert(control.words[row_base_at(control.words[row_list_at(1)])]==NONE);
        pack_row(1,0);assert(!control.words[4] && reported>0); // left out, reported
        pack_row(0,0);solve(0,0,false);solve(1,0,false);
        assert(!control.words[4]);return 0; // the fitting row is still solved
    }
    segment_rows(0);
    uint end=B;
    for(uint group=0;group<groups;++group){
        uint row=control.words[row_list_at(group)],base=control.words[row_base_at(row)];
        assert(base>=end && base>=B);
        uint padded=1;while(padded<expected[row].size())padded*=2;
        end=base+padded;assert(end<=3*B);
        assert(base+pc.native.w+padded<=5*B); // original-member segment
        pack_row(group,0);
        // Subsequent solve uses this GROUP-UNIFORM base admission. Broken
        // links must not reach its first read_site on uninitialized words.
        if(bad && row==0)assert(control.words[row_base_at(row)]==NONE);
        if(!bad){
            std::vector<uint> actual;
            for(uint index=0;index<expected[row].size();++index)
                actual.push_back(row_items.items[base+index]);
            std::sort(actual.begin(),actual.end());assert(actual==expected[row]);
        }
        solve(group,0,false); // broken row's NONE base exits before instrumented read
    }
    assert(bool(control.words[4])==bool(bad));
    std::cout<<"kept="<<total<<" padded="<<end-B<<"\n";
}
'''
    directory = tmp_path_factory.mktemp("true-ff-segmented-rows")
    cpp = directory / "production-segments.cpp"
    binary = directory / "production-segments"
    cpp.write_text(code)
    compiler = shutil.which("clang++") or shutil.which("g++")
    assert compiler
    built = subprocess.run([compiler, "-std=c++17", "-O1", str(cpp), "-o", str(binary)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return binary


@pytest.mark.parametrize("counts", [[0], [1], [13], [65537], [0, 0, 31, 0],
                                   [3, 5, 7, 9, 17, 33], [1] * 129,
                                   [0, 2, 0, 3, 0, 5, 0]])
def test_production_segments_keep_whole_concentrated_and_sparse_rows(segmented_rows, counts):
    budget = max(sum(counts), 1)
    result = subprocess.run([str(segmented_rows), str(budget), str(len(counts)), "0",
                             *map(str, counts)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f"kept={sum(counts)}" in result.stdout


@pytest.mark.parametrize("bad", [1, 2, 3])
def test_production_pack_rejects_broken_links(segmented_rows, bad):
    result = subprocess.run([str(segmented_rows), "7", "3", str(bad), "3", "0", "4"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_production_sort_arena_overflow_keeps_fitting_rows(segmented_rows):
    result = subprocess.run([str(segmented_rows), "7", "3", "4", "3", "0", "4"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_host_storage_and_every_solve_use_segmented_protocol():
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    shader = (FEATURES / "true_ff_feature_native.glsl").read_text()
    assert '"native_control": (8 + 4 * native_rows) * 4' in host
    assert '"native_rows": 5 * candidate_slots * 4' in host
    assert '2 * native_rows * native_span' not in host
    dispatch = host.split("func _dispatch_native_certificate(", 1)[1].split("\nfunc ", 1)[0]
    assert "if stage in [2, 4]:" in dispatch
    prefix, pack = dispatch.index("push.encode_u32(0, 8)"), dispatch.index("push.encode_u32(0, 9)")
    solve = dispatch.index("push.encode_u32(0, stage)")
    assert prefix < pack < solve
    assert dispatch[prefix:pack].count("compute_list_add_barrier") == 1
    assert dispatch[pack:solve].count("compute_list_add_barrier") == 1
    assert "row*pc.native.w" not in shader
    assert "pc.stage.y*pc.native.w" not in shader
    # Mutable global failure state must not split a workgroup before barriers.
    solve_body = function(shader, "solve")
    assert "if(control.words[4]!=0u) return" not in solve_body
    pack = function(shader, "pack_row")
    assert "control.words[row_base_at(row)]=NONE;" in pack
    assert "control.words[4]!=0u" not in pack


def test_native_item_omissions_do_not_suppress_survivor_rows_or_bindings():
    shader = (FEATURES / "true_ff_feature_native.glsl").read_text()
    traverse = (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    work = (FEATURES / "true_ff_feature_work.glslinc").read_text()
    segment = function(shader, "segment_rows")
    index = function(shader, "index_binding")
    sort = function(shader, "sort_bindings")
    drop_site = function(shader, "drop_site")
    drop_owner = function(shader, "drop_owner")
    validate = function(shader, "validate_binding")
    schedule = function(traverse, "schedule_round")

    assert "control.words[4]!=0u" not in segment
    assert "control.words[4]!=0u" not in index
    assert "control.words[4]!=0u" not in sort
    assert "TFF_NATIVE_CERTIFIED<<16u" in drop_site
    assert "pressure_site" not in drop_site
    assert "report_pending" not in drop_site
    assert "control.words[4]" not in drop_owner
    assert "pressure_site" not in drop_owner
    assert "TFF_DIAG_ITEM_OMISSION" in drop_site
    assert "TFF_DIAG_ITEM_OMISSION" in drop_owner
    assert "const uint TFF_DIAG_ITEM_OMISSION = 1u << 30u;" in work
    assert "!survivor_omission" in schedule
    assert "~(retry|TFF_DIAG_ITEM_OMISSION|TFF_DIAG_CAPACITY_OVERFLOW)" in schedule
    assert "drop_owner(i);" in validate


def test_owed_omission_trims_only_its_appended_suffix():
    common = (FEATURES / "true_ff_feature_owed_common.glslinc").read_text()
    entries = "\n".join(
        path.read_text()
        for path in sorted(FEATURES.glob("true_ff_feature_owed*.glsl"))
    )
    error = function(common, "owed_error")
    retire = function(common, "retire_owed_plan_suffix")

    assert "native_plan.dispatch=uvec4(0u)" not in error
    assert "owed.control.status.y" in retire
    assert "retained_total-proposals" in retire
    assert "native_plan.dispatch=uvec4(dims,1u,fine)" in retire
    assert entries.count("retire_owed_plan_suffix();") == 2


def test_program_failure_is_exact_path_and_never_rejects_the_bank():
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    prepare = gd_body(host, "_prepare_family_programs")

    assert "source_family_program_omitted:%s" not in prepare
    assert "upstream_program_omitted:%s" not in prepare
    assert "failed[path] = _omission_record" in prepare
    assert "_family_programs[mask] = variants" in prepare
    assert "mandatory_feature_program_rejected" not in prepare
    assert "func _feature_chain_available" not in host


def test_startup_backoff_retires_cached_priority_failures_before_retry():
    compositor = (ROOT / "godot_project/render/ascii/composition/TrueFFCompositorEffect.gd").read_text()
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    paper = (ROOT / "godot_project/render/ascii/composition/TrueFFPageBackground.gd").read_text()
    gbuffer = (ROOT / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd").read_text()
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text()

    retry = gd_body(compositor, "_maybe_retry_startup")
    assert "_renderer.retry_failed_preparation()" in retry
    assert retry.index("retry_failed_preparation") < retry.index('_startup_failure = ""')
    frame_retry = gd_body(frame, "retry_failed_preparation")
    for owner in ("_background", "_gbuffer", "_passes"):
        assert f"{owner}.retry_failed_preparation()" in frame_retry
    assert '_program_omission.clear()' in gd_body(paper, "retry_failed_preparation")
    gbuffer_retry = gd_body(gbuffer, "retry_failed_preparation")
    assert "configure(_rd" not in gbuffer_retry
    assert '_program_omissions.erase("p0.shadow_merge")' in gbuffer_retry
    pass_retry = gd_body(passes, "retry_failed_preparation")
    assert "_program_failures.erase(NATIVE_PATH)" in pass_retry
    assert "rejected.erase(path)" in pass_retry
    assert "_free_pass_sets()" in pass_retry


def test_generic_family_programs_use_one_fixed_pipeline_layout():
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    live = (FEATURES / "TrueFFLiveSourceBindings.gd").read_text()
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()

    path_mask = gd_body(passes, "_path_family_mask")
    assert "return 7" in path_mask
    assert "return mask\n" not in path_mask
    prepare = gd_body(passes, "_prepare_family_programs")
    assert "_family_program_cache_key(path, effective_mask)" in prepare
    assert "_family_program_failure_cache.has(cache_key)" in prepare
    status = gd_body(passes, "family_program_preparation_status")
    assert "_family_program_completion_count" not in status
    assert "func uniform_set_for_program(" in live
    fixed_set = gd_body(live, "uniform_set_for_program")
    assert "declaration_mask" in fixed_set and "active_mask" in fixed_set
    assert "_placeholder_storage" in fixed_set and "_placeholder_uniform" in fixed_set
    residency = gd_body(frame, "prepare_residency")
    assert residency.index("prepare_family_program_step") < residency.index(
        "_bank.prepare_residency")


@pytest.fixture(scope="module")
def owed_segments(tmp_path_factory):
    source = (FEATURES / "true_ff_feature_owed_common.glslinc").read_text()
    law = (FEATURES / "true_ff_coverage_law.glslinc").read_text()
    production = function(law, "tff_coverage_ink_intersects") + "\n" + function(source, "placed_owner")
    abi_clause = re.search(r"pc\.facts\.x==0u \|\| pc\.stage\.w!=2u\*pc\.facts\.x", source).group()
    code = r'''
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <vector>
using uint=uint32_t;
const uint TFF_NONE=0xffffffffu;
struct U4 {uint x=0,y=0,z=0,w=0;};
struct I4 {int x=0,y=0,z=0,w=0;};
struct F4 {float x=0,y=0,z=0,w=0;};
struct TffNativePlacement {U4 key;I4 parents1,geometry;};
template<class T> struct Checked {std::vector<T> data;T& operator[](uint i){return data.at(i);}};
struct {Checked<uint> words;} native_control;
struct {Checked<uint> items;} native_rows;
struct {Checked<TffNativePlacement> items;} native_placements;
struct {U4 stage,facts;F4 page;} pc;
struct {U4 counts;} cam;
''' + production + r'''
int main(int argc,char**argv){
    uint B=std::strtoul(argv[1],nullptr,10),R=std::strtoul(argv[2],nullptr,10);
    uint row=3;int bad=std::atoi(argv[3]);
    pc.facts.x=B;pc.stage.y=R;pc.stage.w=2*B;cam.counts.y=17;
    // Actual non-power-of-two host budgets must pass the owned ABI law.
    assert(!(''' + abi_clause + r'''));
    native_control.words.data.assign(8+4*R,0);
    native_rows.items.data.assign(5*B,TFF_NONE);
    native_placements.items.data.resize(B);
    native_control.words[6]=1;
    native_control.words[8+row]=1;native_control.words[8+3*R+row]=B;
    native_rows.items[B]=bad==3?TFF_NONE:0;
    TffNativePlacement p;p.key.w=bad==2?16:17;p.parents1.w=16;
    p.geometry.y=11;p.geometry.z=3;p.geometry.w=bad==1?5:9;
    native_placements.items[0]=p;
    // Cell22..33: whole paper16..27 overlaps, but ink19..21 does not.
    assert(placed_owner(row,22,33)==(bad?TFF_NONE:0));
    assert(placed_owner(R,22,33)==TFF_NONE);
    assert(placed_owner(0,22,33)==TFF_NONE);
}
'''
    directory = tmp_path_factory.mktemp("true-ff-owed-segments")
    cpp, binary = directory / "production-owed.cpp", directory / "production-owed"
    cpp.write_text(code)
    compiler = shutil.which("clang++") or shutil.which("g++")
    built = subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(binary)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return binary


@pytest.mark.parametrize("budget,rows", [(48, 6), (64, 8), (73, 9)])
@pytest.mark.parametrize("bad", [0, 1, 2, 3])
def test_production_owed_lookup_uses_segment_and_actual_ink(owed_segments, budget, rows, bad):
    result = subprocess.run([str(owed_segments), str(budget), str(rows), str(bad)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_every_owed_consumer_migrates_without_power_of_two_gate():
    owed = (FEATURES / "true_ff_feature_owed_common.glslinc").read_text()
    assert "pc.stage.w!=2u*pc.facts.x" in owed
    assert "pc.stage.w&(pc.stage.w-1u)" not in owed
    assert "row*pc.stage.w" not in owed
    assert "min(native_control.words[8u+row]" not in owed
    for consumer in ("placed_owner", "validate_row"):
        assert "native_control.words[8u+3u*pc.stage.y+row]" in function(owed, consumer)


def test_failed_optional_shadow_compaction_withdraws_only_shadows():
    native = (FEATURES / "true_ff_feature_native.glsl").read_text()
    publish = function(native, "publish_shadow_plan")
    failed = publish.split("if(control.words[4]!=0u)", 1)[1].split("// Every original", 1)[0]
    assert "args.words[TFF_SLOT_META*4u]=0u;" in failed
    assert "args.words[TFF_SLOT_SCENE1*4u+3u]=0u;" in failed
    assert failed.count("~TFF_PENDING_NATIVE_FEASIBILITY") == 2
    assert "TFF_SLOT_CANDIDATES" not in failed and "TFF_SLOT_SITES" not in failed


def test_descriptor_faults_drop_the_affected_leaf_without_a_fatal_slot_bit():
    traverse = (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    assert "descriptor_total != (l.z >> 1u)) {\n        tff_contract_error(TFF_SLOT_ADMITTED);" in traverse
    mismatch = function(traverse, "demand").split("descriptor_total !=", 1)[1].split(
        "for (uint ordinal", 1)[0]
    assert "return;" in mismatch
    assert "!tff_write_feature_descriptor" in traverse
    failed_write = function(traverse, "demand").split("!tff_write_feature_descriptor", 1)[1].split(
        "// A smooth feature", 1)[0]
    assert "continue;" in failed_write
    assert "tff_capacity_report(TFF_SLOT_ADMITTED);" in failed_write
    assert "total > cam.caps1.w)\n        tff_capacity_report(TFF_SLOT_ADMITTED);" in traverse
    admit = function(traverse, "admit")
    assert "l.w > cam.caps1.w" in admit
    assert "tff_capacity_report(TFF_SLOT_ADMITTED);" in admit


def test_shadow_and_scene_source_failures_are_diagnostic_only():
    shadow = (FEATURES / "true_ff_shadow_boundary.glsl").read_text()
    scene = (FEATURES / "true_ff_feature_scene.glsl").read_text()
    discover = (FEATURES / "true_ff_feature_discover.glsl").read_text()
    assert "failure & ~TFF_FRAME_FATAL_ERRORS" not in shadow
    assert "atomicOr(args[TFF_SLOT_META*4u+3u],TFF_ERROR_SCENE_SOURCE);" in shadow
    scene_failure = function(scene, "topology_failure")
    assert "tff_contract_error(TFF_SLOT_ROOTS);" in scene_failure
    assert "TFF_SLOT_META*4u+1u" not in scene_failure
    source_update = function(scene, "update_source")
    assert "topology_failure()" not in source_update
    assert "source_event_error()" in source_update
    assert "invalid?2.0:(known?0.0:1.0)" in source_update
    seed = function(discover, "seed")
    assert "scene_item_error(TFF_ERROR_SCENE_SOURCE|(1u<<5u));return;" in seed


def test_no_slot_diagnostic_can_regain_whole_page_fatal_authority():
    work = (FEATURES / "true_ff_feature_work.glslinc").read_text()
    traverse = (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    renderer = (FEATURES / "TrueFFFeatureRenderer.gd").read_text()
    offline = (ROOT / "scripts/true_ff_offline_frame.py").read_text()
    assert "const uint TFF_FRAME_FATAL_ERRORS = 0u;" in work
    finish = function(traverse, "finish_schedule")
    assert "TFF_DIAG_CAPACITY_OVERFLOW" in finish
    assert "!tff_shadow_plan_ready()" in finish
    assert "TFF_FRAME_FATAL_ERRORS" not in finish
    report = renderer.split("func _on_gpu_status(", 1)[1].split("\nfunc ", 1)[0]
    assert "true_ff_gpu_frame_rejected" not in report
    assert "true_ff_gpu_items_omitted" in report
    analyse = offline.split("def analyse(", 1)[1].split("\ndef ", 1)[0]
    assert "first_diagnostic" in analyse
    assert "first_fatal" not in analyse
    assert "shadow_plan_missing" in analyse


def test_offline_whole_frame_executes_and_observes_p6_target_writes():
    offline = (ROOT / "scripts/true_ff_offline_frame.py").read_text()
    world = (ROOT / "scripts/true_ff_offline_world.py").read_text()
    assert 'd.program("glyphs", "render/ascii/features/true_ff_feature_glyphs.glsl")' in offline
    assert 'self.d.dispatch(f"P6 glyphs[{name}]"' in offline
    assert 'd.program("page", "render/ascii/composition/true_ff_page_background.glsl")' in offline
    assert 'self.d.dispatch("page background"' in offline
    assert "layout.paint(camera, sun, epoch)" in offline
    assert 'd.dumps.extend(("page_target", "frame_target"))' in offline
    assert "painted_pixel_count" in offline
    assert '"painted_pixels": painted_pixels' in offline
    assert '"paper_pixels": paper_pixels' in offline
    assert '"P6 paint"' not in offline
    assert "def material_library(" in world
    assert 'PALETTE_GD["MAT_CELL_BYTES"]' in world
    assert '"elevations": elevations' in world
    assert 'bytes(terrain["elevations"])' in offline


def test_offline_frame_resolves_sharded_kernel_path_maps():
    from scripts import true_ff_offline_frame as offline_frame

    assert offline_frame.OWED[1].endswith("true_ff_feature_owed_stage_01.glsl")
    assert offline_frame.TRAVERSE[22].endswith("true_ff_feature_traverse_surface_stage_22.glsl")
    assert offline_frame.TRAVERSE[33].endswith("true_ff_feature_traverse_witness_geometry.glsl")
    assert offline_frame.TRAVERSE[34].endswith("true_ff_feature_traverse_witness_visibility.glsl")
    assert offline_frame.TRAVERSE[44].endswith("true_ff_feature_traverse_witness_advance.glsl")
    offline = (ROOT / "scripts/true_ff_offline_frame.py").read_text()
    assert "{TRAVERSE[stage] for stage in (26, 27, 33, 34, 44)}" in offline


def test_offline_shader_compilation_cannot_oversubscribe_backend_services():
    offline = (ROOT / "scripts/true_ff_offline_frame.py").read_text()
    assert "OFFLINE_SHADER_WORKER_CEILING = min(4, os.cpu_count() or 4)" in offline
    assert '"TRUE_FF_OFFLINE_SHADER_WORKERS"' in offline
    assert "1 <= OFFLINE_SHADER_WORKERS <= OFFLINE_SHADER_WORKER_CEILING" in offline
    assert "ThreadPoolExecutor(max_workers=OFFLINE_SHADER_WORKERS)" in offline
    assert "ThreadPoolExecutor(max_workers=min(8" not in offline


def test_runtime_gpu_receipt_reads_only_admission_headers_each_frame():
    renderer = (FEATURES / "TrueFFFeatureRenderer.gd").read_text()
    report = renderer.split("func _report_gpu_status(", 1)[1].split(
        "\nfunc _on_gpu_status_part", 1
    )[0]
    assert '"layout": [_control, CONTROL_HEADER_WORDS * 4]' in report
    assert '"args": [passes.get("args", RID()), 60 * 4 * 4]' in report
    for retired in ("native_control", "native_plan_control", "owed_control", "retry_control"):
        assert retired not in report


def test_native_fine_suffix_is_materialized_and_published_to_visibility():
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    native = (FEATURES / "true_ff_feature_native.glsl").read_text()
    shadow = (FEATURES / "true_ff_shadow_boundary.glsl").read_text()
    admission = (FEATURES / "true_ff_shadow_admission.glslinc").read_text()
    execute = host.split("func execute(", 1)[1].split("\nfunc _submission_receipt", 1)[0]
    assert "_dispatch_native_certificate(list, cut_rounds - 1, cut_rounds - 1, [14])" in execute
    assert execute.index("TRAVERSE_KERNELS[21]") < execute.index("cut_rounds - 1, cut_rounds - 1, [14]")
    assert execute.index("cut_rounds - 1, cut_rounds - 1, [14]") < execute.index("SHADOW_PATH, [3")
    dispatch = host.split("func _dispatch_native_certificate(", 1)[1].split(
        "\nstatic func _owed_stage_family", 1
    )[0]
    assert '14:\n\t\t\t\t_rd.compute_list_dispatch_indirect(list, _buffers["native_plan_control"], 0)' in dispatch
    assert "void publish_fine_plans(uint i)" in native
    main = function(native, "main")
    assert "if(pc.stage.x==14u) {publish_fine_plans(i);return;}" in main
    assert main.index("pc.stage.x==14u") < main.index("tff_native_epoch_certificate")
    publish = function(admission, "tff_shadow_publish_prefix")
    assert "+ native_plan.dispatch.w" in publish
    fallback = function(shadow, "tff_shadow_publish_certified")
    assert "TFF_DIAG_CAPACITY_OVERFLOW" in fallback
    assert "|| !tff_shadow_plan_ready()" in fallback
    assert fallback.index("tff_set_count(TFF_SLOT_CANDIDATES,0u)") < fallback.index("shadow+closed+fine")
    assert "uint fine = min(native_plan.dispatch.w" in fallback
    assert "shadow+closed+fine" in fallback


def test_instance_page_overflow_keeps_a_deterministic_row_prefix():
    layout = (FEATURES / "true_ff_feature_layout.glsl").read_text()
    renderer = (FEATURES / "TrueFFFeatureRenderer.gd").read_text()
    final = function(layout, "stage_final")
    assert "const uint TFF_LAYOUT_PAGE_FATAL=0u;" in layout
    report = renderer.split("func _on_gpu_status(", 1)[1].split("\nfunc ", 1)[0]
    assert "true_ff_gpu_frame_rejected" not in report
    assert "true_ff_gpu_items_omitted" in report
    assert "var failed: bool = upstream != 0 or layout != 0" not in report
    assert "(layout & 4) != 0" not in report
    overflow = final.split("if(first_failure!=0xffffffffu)", 1)[1].split("return;", 1)[0]
    assert "for(uint row=0u;row<first_failure;++row)" in overflow
    assert "control.words[13]=kept_rows;" in overflow
    assert "control.words[11]=kept_glyphs;" in overflow
    assert "control.words[11]=0u" not in overflow


def gd_body(source, name):
    return re.search(rf"^(?:static )?func {name}\(.*?\n(.*?)(?=^(?:static )?func |\Z)",
                     source, re.M | re.S).group(1)


def gd_syntax(body):
    """Syntax-only translation for this bounded, allocation-free host subset."""
    body = re.sub(r"var (\w+)(?:: [^=\n]+)?\s*(?::=|=)", r"\1 =", body)
    body = re.sub(r"for (\w+): [\w\[\]]+ in", r"for \1 in", body)
    body = re.sub(r"^(\s*)\"(water|effects|sky)\":", r'\1case "\2":', body, flags=re.M)
    body = re.sub(r"\btrue\b", "True", body)
    body = re.sub(r"\bfalse\b", "False", body)
    body = re.sub(r"\bnull\b", "None", body)
    body = re.sub(r"(\w+)\.size\(\)", r"len(\1)", body)
    body = re.sub(r"(\w+)\.has\(([^)]+)\)", r"(\2 in \1)", body)
    body = re.sub(r"(\w+)\.is_empty\(\)", r"(not \1)", body)
    body = body.replace("BINDINGS.find(", "BINDINGS.index(").replace("RDUniform.new()", "RDUniform()")
    return body


@pytest.fixture
def family_bindings():
    source = (FEATURES / "TrueFFLiveSourceBindings.gd").read_text()
    state = {}
    for name in ("BINDINGS", "STORAGE_BINDINGS", "WATER_KEYS", "EFFECT_KEYS", "FAMILY_BITS"):
        text = re.search(rf"^const {name}: [^=]+ = (\[.*?\]|\{{.*?\}})", source, re.M | re.S).group(1)
        state[name] = json.loads(text)
    state["P0_LAST_BINDING"] = "water_clock"

    class RID:
        def __init__(self, value=0): self.value = value
        def is_valid(self): return self.value != 0

    class Uniform:
        def __init__(self): self.ids = []
        def add_id(self, rid): self.ids.append(rid)

    class Device:
        UNIFORM_TYPE_STORAGE_BUFFER = 1
        UNIFORM_TYPE_UNIFORM_BUFFER = 2
        def __init__(self): self.created = []
        def uniform_set_create(self, uniforms, shader, slot):
            assert slot == 1 and shader.is_valid() and uniforms
            assert all(u.ids[0].is_valid() for u in uniforms)
            self.created.append([(u.binding, u.uniform_type, u.ids[0].value) for u in uniforms])
            return RID(len(self.created) + 100)

    state.update(RID=RID, RDUniform=Uniform, RenderingDevice=Device, SET=1,
                 _rd=Device(), _family_reasons={}, _rids=[RID(i + 1) for i in range(11)])
    for name, args in (("_family_keys", "family"), ("family_ready", "family"),
                       ("family_mask", ""), ("binding_keys_for_mask", "mask, p0=False"),
                       ("signature_for_mask", "mask, p0=False"),
                       ("uniform_set_for_mask", "shader, mask, p0=False")):
        exec("def " + name + "(" + args + "):\n" + gd_syntax(gd_body(source, name)), state)
    return state


@pytest.mark.parametrize("mask", range(8))
def test_production_family_selection_never_binds_withdrawn_or_neutral_resources(family_bindings, mask):
    state = family_bindings
    for family, bit in state["FAMILY_BITS"].items():
        if not mask & bit:
            state["_family_reasons"][family] = "withdrawn"
            for key in state["_family_keys"](family):
                state["_rids"][state["BINDINGS"].index(key)] = state["RID"]()
    assert state["family_mask"]() == mask
    selected = state["binding_keys_for_mask"](mask)
    assert len(state["signature_for_mask"](mask)) == len(selected)
    made = state["uniform_set_for_mask"](state["RID"](99), mask)
    assert made.is_valid() == bool(mask)
    if mask:
        bound = state["_rd"].created[-1]
        assert [u[0] for u in bound] == [state["BINDINGS"].index(k) for k in selected]
        assert [u[2] for u in bound] == [state["BINDINGS"].index(k) + 1 for k in selected]
    p0 = state["binding_keys_for_mask"](mask, True)
    assert p0 == (state["BINDINGS"][:6] if mask & 1 else [])


@pytest.mark.parametrize("family", ["water", "effects", "sky"])
def test_partial_family_loss_rejects_required_set_then_recovers_exact_bindings(family_bindings, family):
    state = family_bindings
    slot = state["BINDINGS"].index(state["_family_keys"](family)[0])
    state["_rids"][slot] = state["RID"]()
    bit = state["FAMILY_BITS"][family]
    assert state["family_mask"]() == 7 ^ bit
    assert not state["uniform_set_for_mask"](state["RID"](99), 7).is_valid()
    assert not state["_rd"].created
    state["_rids"][slot] = state["RID"](999)
    assert state["family_mask"]() == 7
    assert state["uniform_set_for_mask"](state["RID"](99), 7).is_valid()
    assert dict((binding, rid) for binding, kind, rid in state["_rd"].created[-1])[slot] == 999


@pytest.mark.parametrize("mask", range(8))
@pytest.mark.parametrize("name", [
    "discover",
    "traverse_dispatch",
    "traverse_hierarchy_dispatch",
    "traverse_surface_dispatch",
    "extract",
    "sites",
    "visibility",
    "scene",
    "owed",
    "shadow_boundary",
])
def test_actual_family_shader_variants_delete_absent_descriptors(tmp_path, mask, name):
    from scripts import fl4895_glsl_check as checker
    path = FEATURES / ("true_ff_shadow_boundary.glsl" if name == "shadow_boundary" else f"true_ff_feature_{name}.glsl")
    defines = "\n".join(f"#define TFF_WITHDRAWN_{family}" for family, bit in
                         (("WATER", 1), ("EFFECTS", 2), ("SKY", 4)) if not mask & bit)
    source = checker.stages(path)["compute"]
    glsl, spv = tmp_path / "variant.comp", tmp_path / "variant.spv"
    glsl.write_text("#version 450\n" + defines + "\n" + source)
    built = subprocess.run(["glslangValidator", "-V", "--target-env", "vulkan1.1", "-S", "comp", "-o", str(spv), str(glsl)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stdout + built.stderr
    reflected = subprocess.run(["spirv-cross", str(spv), "--reflect"], capture_output=True, text=True)
    assert reflected.returncode == 0, reflected.stderr
    facts = json.loads(reflected.stdout)
    resources = [r for key in ("ubos", "ssbos", "textures", "separate_images", "separate_samplers") for r in facts.get(key, [])]
    live = {r["binding"] for r in resources if r.get("set") == 1}
    allowed = (set(range(6)) | {9, 10} if mask & 1 else set()) | ({7, 8} if mask & 2 else set()) | ({6} if mask & 4 else set())
    assert live <= allowed
    if not mask & 1:
        assert not any(r.get("set") == 0 and r["binding"] == 39 for r in resources)


@pytest.mark.parametrize("water", [False, True])
def test_actual_p0_absent_water_graphics_variant_has_no_live_set(tmp_path, water):
    from scripts import fl4895_glsl_check as checker
    path = ROOT / "godot_project/render/ascii/structure/true_ff_gbuffer.glsl"
    for stage, source in checker.stages(path).items():
        defines = "" if water else "#define TFF_WITHDRAWN_WATER\n"
        glsl, spv = tmp_path / ("variant." + checker.STAGES[stage]), tmp_path / (stage + ".spv")
        glsl.write_text("#version 450\n" + defines + source)
        built = subprocess.run(["glslangValidator", "-V", "--target-env", "vulkan1.1", "-S", checker.STAGES[stage], "-o", str(spv), str(glsl)],
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stdout + built.stderr
        facts = json.loads(subprocess.run(["spirv-cross", str(spv), "--reflect"], capture_output=True, text=True, check=True).stdout)
        live = {r["binding"] for key in ("ubos", "ssbos") for r in facts.get(key, []) if r.get("set") == 1}
        assert live <= (set(range(6)) if water else set())
