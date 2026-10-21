#!/usr/bin/env python3
"""Bounded RQ-170 actual-geometry perspective-probe contracts."""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
import tempfile
import unittest
from pathlib import Path

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


REPO = Path(__file__).resolve().parents[2]
SHADERS = REPO / "godot_project"
PIPELINE = REPO / "godot_project" / "render" / "backends" / "perspective" / "PerspectiveProbeBackend.gd"
HOST = REPO / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
CAPTURE = SHADERS / "render/backends/perspective/capture/perspective_probe_capture.glsl"
CULL = SHADERS / "render/backends/perspective/cull/perspective_probe_cull.glsl"
SPLAT = SHADERS / "render/backends/perspective/splat/perspective_probe_splat.glsl"
PUBLISH = SHADERS / "render/backends/perspective/publish/perspective_probe_payload_publish.glsl"
VSB = SHADERS / "render/backends/ortho/visibility/visible_surface_builder.glsl"


def test_perspective_background_obeys_cell_color_mode():
    # Source contract only: runtime and moving-image acceptance remain separate.
    publish = PUBLISH.read_text()
    authority = (SHADERS / "render/ascii/authority/native_presentation_authority.glslinc").read_text()
    compositor = (SHADERS / "render/ascii/raster/ascii_compositor.glsl").read_text()
    cell = _glsl_function_source(publish, "publish_cell")
    assert "| ((params.eye.w & 1u) != 0u ? NATIVE_CHANNEL_BG_NATIVE_PIXEL : 0u)" in cell
    assert "| ((params.eye.w & 2u) != 0u ? NATIVE_CHANNEL_INK_NATIVE_PIXEL : 0u);" in cell
    assert "cell_presentation_result(index, true, true, true," in cell
    assert "entry.allocation = uvec4(index, 1u," in authority
    assert "NATIVE_PIXEL_FLAT_PAPER" in authority
    assert "native_presentation_paper(cell)" in authority
    assert "native_presentation_channels(front_result, cell_index, domain_valid," in compositor
    assert "var _presentation_modes_by_projection: Array[int] = [0, 0]" in HOST.read_text()
    assert "var bg_domain := \"screen_cell\" if (mode_index & 1) == 0" in HOST.read_text()



def test_renderer_optimizations_default_on_with_independent_controls() -> None:
    pipeline, host = PIPELINE.read_text(), HOST.read_text()
    assert 'var _underlay_row_gate_enabled := not OS.get_cmdline_user_args().has(UNDERLAY_ROW_GATE_DISABLE_ARGUMENT)' in pipeline
    assert 'const UNDERLAY_ROW_GATE_DISABLE_ARGUMENT := "--fl4359-no-underlay-row-gate"' in pipeline
    assert 'var _stable_raster_order_enabled := not OS.get_cmdline_user_args().has("--fl4691-no-stable-raster-order")' in pipeline
    assert 'var _fl4691_paired_eye_sky_reuse_enabled := not OS.get_cmdline_user_args().has("--fl4691-no-paired-eye-sky-reuse")' in host
    initialize = _gdscript_function_source(pipeline, "initialize")
    assert 'if _stable_raster_order_enabled:' in initialize
    assert '_raster_order.initialize(_rd, texel_bytes / 4)' in initialize
    assert '_raster_witness_enabled = OS.get_cmdline_user_args().has("--fl4359-probe-candidate-diagnostics")' in initialize


def test_candidate_resize_retires_prepared_domain_before_releasing_resources() -> None:
    source = PIPELINE.read_text()
    resize = _gdscript_function_source(source, "resize_candidate_extent")
    assert 'if _candidate_domain_ready and new_extent == _candidate_extent' in resize
    assert resize.index('_candidate_domain_ready = false') < resize.index('_retire_prepared_sources()') \
        < resize.index('_release_candidate_attachments()')
    assert '_candidate_domain_ready = _candidate_framebuffer.is_valid()' in resize
    assert 'return _candidate_domain_ready' in resize
    retire = _gdscript_function_source(source, "_retire_prepared_sources")
    for field in ('_survivors_prepared', '_source_rasterized', '_visible_sources_ready'):
        assert f'{field} = false' in retire
    assert '_prepared_active_layers = []' in retire
    assert '_prepared_cull_layers = []' in retire
    assert '_fresh_capture_mask =' not in retire
    prepare = _gdscript_function_source(source, "prepare_splat_survivors")
    assert prepare.index('if not _candidate_domain_ready:') < prepare.index('_set_splat_payloads(')


def test_failed_raster_admission_cannot_publish_or_shade_as_success() -> None:
    source = PIPELINE.read_text()
    fail_open = _gdscript_function_source(source, '_underlay_row_gate_fail_open')
    assert '-> bool:' in fail_open
    assert 'return _rd.buffer_update(_underlay_raster_row_demand, 0, bytes.size(), bytes) == OK' in fail_open
    prefilter = _gdscript_function_source(source, '_dispatch_underlay_row_prefilter')
    assert 'return _underlay_row_gate_fail_open()' in prefilter
    raster = _gdscript_function_source(source, '_rasterize_source_references')
    assert 'if not _underlay_row_gate_fail_open():' in raster
    assert 'if not _dispatch_underlay_row_prefilter():' in raster
    assert raster.index('if not _dispatch_underlay_row_prefilter():') < raster.rindex('_draw_splat_set(true)')
    for name in ('resolve_visible_sources', 'draw_and_publish'):
        consumer = _gdscript_function_source(source, name)
        if name == 'resolve_visible_sources':
            assert 'not _rasterize_source_references():' in consumer
        else:
            assert 'if not _source_rasterized or not _visible_sources_ready:' in consumer
            assert '_rasterize_source_references()' not in consumer
        assert '"absence_reason": "source raster admission failed"' in consumer
    prepare = _gdscript_function_source(source, 'prepare_splat_survivors')
    assert prepare.index('if not prepared:') < prepare.index('_survivors_prepared = true')


def _gdscript_function_source(source: str, name: str) -> str:
    match = re.search(r"(?m)^func\s+" + re.escape(name) + r"\s*\(", source)
    assert match is not None, f"missing GDScript function {name}"
    next_match = re.search(r"(?m)^func\s+\w+\s*\(", source[match.end() :])
    end = len(source) if next_match is None else match.end() + next_match.start()
    return source[match.start() : end]


def _glsl_function_source(source: str, name: str) -> str:
    match = re.search(
        # Shared shader helpers return named structs as well as scalar/vector
        # types; keep this extractor tied to a declaration-shaped identifier.
        r"\b[A-Za-z_][A-Za-z0-9_]*\s+"
        + re.escape(name)
        + r"\s*\([^;]*?\)\s*\{",
        source,
        flags=re.S,
    )
    assert match is not None, f"missing GLSL function {name}"
    opening = source.find("{", match.start())
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise AssertionError(f"unterminated GLSL function {name}")


def _assert_reference_cadence_contract(pipeline_source: str) -> None:
    """Pin world-owned GRID lifetime and explicit paint admission."""
    for deleted in (
        "func _recapture_plan(",
        "func _texel_shade_lighting_key(",
        "func _compute_frustum_face_mask(",
        "_state.get(\"shade_key\"",
        "lighting_changed",
        "transition_frames",
        "transition_length",
    ):
        assert deleted not in pipeline_source, deleted
    assert "const GRID_STEP := 1.0" in pipeline_source
    assert "const BLEND_DURATION := 0.5" in pipeline_source
    configure = _gdscript_function_source(pipeline_source, "configure_frame")
    _assert_ordered_markers(
        configure,
        'var grid_key := _grid_capture_content_key()',
        '_state["grid_content_key"] = grid_key',
        "var render_probe_mask := 0",
        "capture_face_masks[probe_index] = _scheduled_capture_faces(",
        "render_probe_mask |= 1 << probe_index",
        '_state["capture_face_masks"] = capture_face_masks',
    )
    schedule = _gdscript_function_source(pipeline_source, '_scheduled_capture_faces')
    assert 'if role == PROBE_EYE or isolated:' in schedule
    assert 'if role == PROBE_CURRENT:' in schedule
    assert 'return ((1 << CUBE_FACE_COUNT) - 1) & ~captured' in schedule
    assert 'probe_frame % 2' not in configure
    update = _gdscript_function_source(pipeline_source, "_update_state")
    _assert_ordered_markers(
        update,
        "var snapped := _snap_one_unit(eye_origin)",
        "if snapped != old_current:",
        "_swap_grid_capture_bands()",
        "previous_origin = old_current",
        "fade_t = 0.0",
        "blending = true",
        "var rate := maxf(1.0 / BLEND_DURATION, smoothed_speed / GRID_STEP)",
        "fade_t += rate * dt",
        '_state["transition_progress"] = clampf(fade_t, 0.0, 1.0)',
        '_state["probe_frame"] = 0',
        '_state["frame_probe_step"] = int(_state.get("probe_frame", 0))',
        '_state["active_mask"] = 7 if blending else 3',
    )
    masks = _gdscript_function_source(pipeline_source, "_build_face_masks")
    assert "_compute_face_mask(forward, EYE_CULL_COS)" in masks
    assert "_compute_face_mask(forward, GRID_CULL_COS)" in masks
    assert 'grid_mask if bool(_state.get("transition_blending", false)) else 0' in masks
    capture = _gdscript_function_source(pipeline_source, "capture_geometry")
    assert '(int(capture_masks[probe_index]) & (1 << face_index)) == 0' in capture
    assert "recapture_mask" not in capture
    shade = _gdscript_function_source(pipeline_source, "_write_texel_shade_params")
    _assert_ordered_markers(
        shade,
        'var recaptured: Array = capture_result.get("layers_recaptured_this_frame", [])',
        'var required: Array = capture_result.get("layers_requiring_paint", recaptured)',
        'var layer_code := epoch | (TEXEL_LAYER_FRESH if fresh else 0)',
        'if epoch > 0 and (fresh or active_layers.has(layer)):',
        'layer_mask |= 1 << layer',
        'if epoch > 0 and fresh:',
        'shaded_layers.append(layer)',
    )


def _assert_ordered_markers(source: str, *markers: str) -> None:
    cursor = 0
    for marker in markers:
        position = source.find(marker, cursor)
        assert position >= 0, f"missing or out-of-order marker: {marker!r}"
        cursor = position + len(marker)


def _assert_capture_origin_invariants(pipeline_source: str) -> None:
    assert '"captured_origins": [Vector3.ZERO, Vector3.ZERO, Vector3.ZERO],' in pipeline_source

    configure_source = _gdscript_function_source(pipeline_source, "configure_frame")
    _assert_ordered_markers(
        configure_source,
        'var captured_face_masks: Array = _state.get("captured_face_masks", [0, 0, 0])',
        'var captured_origins: Array = _state.get(',
        'var capture_origin_mismatch_mask := 0',
        'for probe_index in range(PROBE_COUNT):',
        'if int(captured_face_masks[probe_index]) == 0:',
        'if captured_origins[probe_index] != _probe_origin(probe_index):',
        'captured_face_masks[probe_index] = 0',
        'capture_origin_mismatch_mask |= 1 << probe_index',
        '_state["captured_face_masks"] = captured_face_masks',
        'var render_probe_mask := 0',
        '_state["capture_face_masks"] = capture_face_masks',
    )
    _assert_ordered_markers(
        configure_source,
        '"captured_origins": _captured_origin_arrays(),',
        '"capture_origin_mismatch_mask": capture_origin_mismatch_mask,',
    )

    active_source = _gdscript_function_source(pipeline_source, "_active_splat_layers")
    _assert_ordered_markers(
        active_source,
        'var captured_origins: Array = _state.get(',
        'if captured_origins[probe_index] != _probe_origin(probe_index):',
        'continue',
    )

    capture_source = _gdscript_function_source(pipeline_source, "capture_geometry")
    _assert_ordered_markers(
        capture_source,
        '_invalidate_capture_layer(layer, probe_index, face_index)',
        'if not _submit_capture_face(',
        'layer, face_index, face_draws',
        '_captured_static_packet_by_layer[layer] = String(',
        'var captured_origins: Array = _state.get(',
        'var assigned_origin := _probe_origin(probe_index)',
        'if captured_origins[probe_index] != assigned_origin:',
        'captured_masks[probe_index] = 0',
        'captured_origins[probe_index] = assigned_origin',
        'captured_masks[probe_index] = int(captured_masks[probe_index]) | (1 << face_index)',
        '_state["captured_face_masks"] = captured_masks',
        '_state["captured_origins"] = captured_origins',
    )

    update_source = _gdscript_function_source(pipeline_source, "_update_state")
    _assert_ordered_markers(
        update_source,
        'elif triggered:',
        'reason = "one_unit_grid_origin_changed"',
        'elif blending:',
        'reason = "grid_crossfade"',
        'elif old_dynamic != new_dynamic:',
        'reason = "dynamic_source_revision_changed"',
        'elif old_eye != eye_origin:',
        'reason = "eye_origin_changed"',
    )


def _assert_double_buffer_lifecycle_contract(pipeline_source: str, cull_source: str) -> None:
    """Prove the logical/physical permutation and the EYE family split as a contract."""

    assert '"capture_bands": [0, 1, 2],' in pipeline_source
    swap_source = _gdscript_function_source(pipeline_source, "_swap_grid_capture_bands")
    _assert_ordered_markers(
        swap_source,
        'var bands: Array = (_state.get("capture_bands", [0, 1, 2]) as Array).duplicate()',
        'var masks: Array = (_state.get("captured_face_masks", [0, 0, 0]) as Array).duplicate()',
        'var origins: Array = (_state.get(',
        'var current_band := int(bands[1])',
        'bands[1] = bands[2]',
        'bands[2] = current_band',
        'var current_mask := int(masks[1])',
        'masks[1] = int(masks[2])',
        'masks[2] = current_mask',
        'var current_capture_origin: Vector3 = origins[1]',
        'origins[1] = origins[2]',
        'origins[2] = current_capture_origin',
        '_state["capture_bands"] = bands',
        '_state["captured_face_masks"] = masks',
        '_state["captured_origins"] = origins',
    )

    capture_source = _gdscript_function_source(pipeline_source, "capture_geometry")
    active_source = _gdscript_function_source(pipeline_source, "_active_splat_layers")
    splat_params_source = _gdscript_function_source(pipeline_source, "_splat_param_bytes")
    assert "var layer := _physical_capture_band(probe_index) * CUBE_FACE_COUNT + face_index" in capture_source
    assert "var layer := _physical_capture_band(probe_index) * CUBE_FACE_COUNT + face_index" in active_source
    assert "result.append(layer)" in active_source
    assert "var role := _logical_probe_for_physical_band(layer / CUBE_FACE_COUNT)" in splat_params_source
    cull_main = split_stages(cull_source)["compute"]
    cull_main = cull_main[cull_main.index("void main()") :]
    assert "uint local = gl_GlobalInvocationID.x, layer_index = gl_GlobalInvocationID.y;" in cull_main
    assert "uint role = band == params.bands.x ? 0u : (band == params.bands.y ? 1u : 2u);" in cull_main
    assert "uint arm = admitted_layers.layers[4].w;" in cull_main
    assert "if (arm > CULL_ARM_PAIRED) return;" in cull_main
    # The face mask is shared by the paired traversal but remains a per-surface
    # admission gate inside raster_geometry_admitted.
    assert "if ((params.face_masks[role] & (1u << (layer % 6u))) == 0u) return false;" in cull_source

    # Two successive snaps: each swap is a permutation, and only the newly
    # assigned CURRENT mask/origin is cleared/rebuilt. The old CURRENT payload
    # remains in logical PREVIOUS for the transition fallback.
    bands = [0, 1, 2]
    masks = [0x3F, 0x2A, 0x15]
    origins = [(99, 99, 99), (0, 0, 0), (-1, 0, 0)]

    def swap() -> None:
        bands[1], bands[2] = bands[2], bands[1]
        masks[1], masks[2] = masks[2], masks[1]
        origins[1], origins[2] = origins[2], origins[1]

    swap()
    assert bands == [0, 2, 1] and sorted(bands) == [0, 1, 2]
    assert masks[2] == 0x2A and origins[2] == (0, 0, 0)
    masks[1] = 0
    origins[1] = (1, 0, 0)
    masks[1] = 0x11  # the new CURRENT capture completes on its assigned band

    swap()
    assert bands == [0, 1, 2] and sorted(bands) == [0, 1, 2]
    assert masks[2] == 0x11 and origins[2] == (1, 0, 0)
    masks[1] = 0
    origins[1] = (2, 0, 0)
    assert masks[2] != 0 and origins[2] == (1, 0, 0)

    # EYE is the live dynamic owner; retained static/terrain plans remain
    # available to the logical grid bands and are submitted per face.
    # FL-4359 reference law: every probe still captures the visible static world;
    # EYE fills the parallax holes the retained probes cannot see.
    assert "for draw in ordered_dynamic:" in capture_source
    dynamic_gate = (
        "if probe_index == PROBE_EYE and "
        "_owner_isolation_keeps_family(FAMILY_DYNAMIC):"
    )
    assert dynamic_gate in capture_source
    assert capture_source.find(dynamic_gate) < capture_source.find(
        "for draw in ordered_dynamic:"
    )
    # Static admission moved into a retained per-face plan (FL-4691).
    assert "_static_capture_plan(ordered_static, layer, face_index" in capture_source
    static_plan = _gdscript_function_source(pipeline_source, "_static_capture_plan")
    static_candidates = _gdscript_function_source(pipeline_source, "_static_capture_candidates")
    assert "_static_capture_candidates(ordered, layer, face, origin," in static_plan
    assert "_owner_isolation_keeps_family(FAMILY_STATIC)" in static_candidates
    assert "_aabb_intersects_face(face, origin, low, high, far_distance)" in static_plan
    assert "_reflection_bounds_intersect_face(" in static_plan
    assert 'admitted["capture_direct"] = direct' in static_plan
    assert 'admitted["capture_reflection"] = reflected' in static_plan
    assert capture_source.count(dynamic_gate) >= 2


def _assert_cull_admission_contract(source: str) -> None:
    """Keep paired surface reads, owner admission, and PREVIOUS/EYE checks."""

    compute = split_stages(source)["compute"]
    main = compute[compute.index("void main()") :]
    assert "uint local = gl_GlobalInvocationID.x, layer_index = gl_GlobalInvocationID.y;" in main
    assert "uint layer = admitted_layers.layers[layer_index / 4u][layer_index % 4u];" in main
    assert "uint arm = admitted_layers.layers[4].w;" in main
    assert "if (arm > CULL_ARM_PAIRED) return;" in main
    assert "uint role = band == params.bands.x ? 0u : (band == params.bands.y ? 1u : 2u);" in main

    # Paired fallback shares exact source reads, classification and projection.
    # Separate-arm controls retain independent raster lists. Paint ownership is
    # deliberately absent here and begins only after hardware visibility.
    assert "if (arm != CULL_ARM_PAIRED)" in main
    assert "classify_surface(read_surface(is_underlay, p))" in main
    assert "SurfaceSample underlay_sample = read_surface(true, p);" in main
    assert "uvec4 front_identity = texelFetch(identity, p, 0);" in main
    assert "if (front_identity.x == 0u) {" in main
    assert "front_sample = underlay_sample;" in main
    assert "front_sample.foreground = 0u;" in main
    assert "SurfaceFacts front = classify_surface(front_sample);" in main
    assert "bool shared_source = same_source_sample(front_sample, underlay_sample);" in main
    assert "if (shared_source) underlay = front;" in main
    assert "if (shared_source) underlay_geometry = front_geometry;" in main
    assert "underlay = classify_surface(underlay_sample);" in main
    assert "bool same_source_sample(SurfaceSample front, SurfaceSample underlay)" in compute
    assert "front.foreground == 0u" in compute
    assert "floatBitsToUint(front.normal_depth)" in compute
    assert "floatBitsToUint(front.anchor)" in compute
    assert "append_shade_front" not in compute
    assert "append_shade_underlay" not in compute
    assert "fresh_grid_geometry" not in compute
    assert "append_raster_front(id)" in main
    assert "append_raster_underlay(id)" in main

    # Owner and captured-depth admission are evaluated per surface. The
    # retained geometry lane rejects dynamic owners and non-finite/out-of-range
    # radial depth before raster work is appended.
    classify = _glsl_function_source(compute, "classify_surface")
    assert "facts.owner = source_sample.identity.x;" in classify
    assert "facts.cheb = source_sample.normal_depth.w;" in classify
    assert "facts.radial = (facts.cheb - params.range.x) * params.range.z;" in classify
    raster = _glsl_function_source(compute, "raster_geometry_admitted")
    assert "if ((params.face_masks[role] & (1u << (layer % 6u))) == 0u) return false;" in raster
    assert "if (facts.background != 0u) return false;" in raster
    assert "if (role != 0u && (facts.owner == 3u || facts.owner == 4u || facts.owner == 5u)) return false;" in raster
    assert "if (facts.finite_radial == 0u || facts.radial < 0.0 || facts.radial >= 0.999) return false;" in raster

    # A moving EYE identity cannot revoke an immutable PREVIOUS snapshot.
    # Live occluders compete in hardware depth just as they do with CURRENT.
    assert "previous_eye_conflict" not in compute
    assert "if (front_geometry)" in main
    assert "if (underlay_geometry)" in main
    assert "outside_view(layer % 6u, address.xy, size," in raster


def _assert_splat_geometry_contract(source: str) -> None:
    """Keep reference geometry, depth conversion, and Bayer raster behavior."""

    stages = split_stages(source)
    vertex = stages["vertex"]
    fragment = stages["fragment"]
    main = vertex[vertex.index("void main()") :]
    assert "uint tid = instances.texel_ids[gl_InstanceIndex];" in main
    assert "uint layer = tid / face_texels, local = tid % face_texels;" in main
    assert "uint role = band == splat.bands.x ? 0u : (band == splat.bands.y ? 1u : 2u);" in main
    assert "ProbeCaptureSource captured_source = probe_capture_source(capture_identity, address);" in main
    assert "float cheb = probe_capture_fetch(captured_source.foreground, capture_normal_depth, probe_underlay_normal_depth, address).w;" in main
    assert "v_identity = captured_source.identity;" in main
    assert "v_provenance = uvec4(role, face * face_texels + local" in main

    # Every rasterized corner uses the captured Chebyshev distance and the
    # same role-selected origin; fragment output stores one source address.
    assert "vec3 dir = cube_direction(face, corner);" in main
    assert "vec3 world = origin + dir * (cheb / max(max(abs(dir.x), abs(dir.y)), abs(dir.z)));" in main
    assert "vec4 clip = splat.camera_view_projection * vec4(world, 1.0);" in main
    assert "clip.z = clip.w - clip.z;" in main
    assert "clip.z = min(clip.z, clip.w);" in main
    assert "layout(location = 0) out uint out_source;" in fragment
    assert "out_source = (v_provenance.w * size * size + local + 1u)" in fragment
    assert "if (splat.lifecycle.z != 0u)" in fragment
    assert "if (v_probe == 1u && threshold >= fade) discard;" in fragment
    assert "if (v_probe == 2u && threshold < fade) discard;" in fragment


def _transition_thresholds(remaining: int, length: int) -> tuple[int, int]:
    progress = 1.0 - float(remaining) / float(max(length, 1))
    current = max(0, min(16, int(round(progress * 16.0))))
    return current, 16 - current


def _assert_transition_lifecycle_contract(
    pipeline_source: str, splat_source: str
) -> None:
    """Keep CURRENT authoritative and PREVIOUS limited to depth-tested holes."""

    assert '"transition_progress": 1.0' in pipeline_source
    update = _gdscript_function_source(pipeline_source, "_update_state")
    assert "fade_t += rate * dt" in update
    assert '_state["bayer_phase"]' not in update
    assert '_state["transition_progress"] = clampf(fade_t, 0.0, 1.0)' in update
    params = _gdscript_function_source(pipeline_source, "_splat_param_bytes")
    assert 'int(_state.get("bayer_phase", 0))' not in params
    assert '_encode_uvec4(bytes, 112, _probe_size, LAYER_COUNT, int(_state["active_mask"]),\n\t\t0 if _fl4691_disable_splat_view_cull else 1)' in params
    assert 'current_identity_threshold' not in params
    assert 'previous_identity_threshold' not in params
    assert '_encode_uvec4(bytes, 128, int(_state.get("generation", 0)),' in params
    # The host and fragment use the configured native-cell partition.
    assert '\t\t_float_bits(fade_t), 1 if crossfading else 0, 1)' in params
    assert 'var fade_t := float(_state.get("transition_progress", 1.0))' in params
    assert "var crossfading := _crossfade_active()" in params

    # FL-4359 RECURRENCE GUARD: candidate-level Bayer and identity partitions
    # both make a retained handover mutate the input to hard cell election.
    # CURRENT/PREVIOUS admission therefore has no transition writer here.
    fragment = split_stages(splat_source)["fragment"]
    for retired in (
        "fl4359_stable_identity_transition_key",
        "fl4359_retained_transition_key",
        "current_identity_threshold",
        "previous_identity_cutoff",
        "splat.lifecycle.w",
    ):
        assert retired not in fragment
    assert "bayer4(cell)" in fragment
    assert "bayer4(pixel + cell)" not in fragment
    assert '_encode_uvec4(bytes, 192, _cell_pixel_size.x,' in params
    assert '_cell_pixel_size.y, _splat_extent.x, _splat_extent.y)' in params
    assert "float fade = clamp(uintBitsToFloat(splat.lifecycle.y), 0.0, 1.0);" in fragment
    assert "if (v_probe == 1u && threshold >= fade) discard;" in fragment
    assert "if (v_probe == 2u && threshold < fade) discard;" in fragment
    vertex = split_stages(splat_source)["vertex"]
    # FL-4880: EYE tie bias skips water and EYE-only movers/actors; capped at 2% of view distance.
    assert "const float EYE_RETAINED_TIE_RELATIVE = 0.02;" in vertex
    assert ("if (role == 0u && v_identity.x != CAPTURE_OWNER_WATER\n"
        "            && v_identity.x != CAPTURE_OWNER_DYNAMIC && v_identity.x != CAPTURE_OWNER_ACTOR)\n"
        "        clip.z += min(0.001 * clip.w, (clip.w - clip.z) * EYE_RETAINED_TIE_RELATIVE);") in vertex


def _assert_transition_host_mutation_is_rejected(
    source: str, needle: str, replacement: str
) -> None:
    mutant, count = source.replace(needle, replacement, 1), source.count(needle)
    assert count == 1, needle
    try:
        _assert_transition_lifecycle_contract(mutant, SPLAT.read_text(encoding="utf-8"))
    except AssertionError:
        return
    raise AssertionError(f"mutation escaped transition host contract: {needle!r}")


def _assert_transition_shader_mutation_is_rejected(
    source: str, needle: str, replacement: str
) -> None:
    mutant, count = source.replace(needle, replacement, 1), source.count(needle)
    assert count == 1, needle
    try:
        _assert_transition_lifecycle_contract(
            PIPELINE.read_text(encoding="utf-8"), mutant
        )
    except AssertionError:
        return
    raise AssertionError(f"mutation escaped transition shader contract: {needle!r}")


def _assert_splat_owner_split_contract(vertex: str) -> None:
    """Require one reference Chebyshev geometry owner for every probe family."""

    main = vertex[vertex.index("void main()") :]
    assert "vec3 origin = probe_origin(probe_index);" in main
    assert main.count("normal_depth.w / max(max") == 4
    assert "reconstruct_tangent" not in vertex
    assert "reconstruct_capture_depth" not in vertex


def test_reference_radial_continuity_is_not_decoded_depth_continuity() -> None:
    """The affine near plane can move a pair across the 0.2% edge boundary."""

    near = 0.1
    far = 200.0
    radial_a = 0.001
    radial_b = 0.0010021
    depth_a = near + radial_a * (far - near)
    depth_b = near + radial_b * (far - near)

    radial_relative = abs(radial_b - radial_a) / max(radial_a, radial_b)
    decoded_relative = abs(depth_b - depth_a) / max(depth_a, depth_b)

    assert radial_relative >= 0.002
    assert decoded_relative < 0.002


def _assert_splat_owner_split_mutation_is_rejected(
    source: str, needle: str, replacement: str
) -> None:
    mutant, count = source.replace(needle, replacement, 1), source.count(needle)
    assert count == 1, needle
    try:
        _assert_splat_owner_split_contract(split_stages(mutant)["vertex"])
    except AssertionError:
        return
    raise AssertionError(f"mutation escaped the owner split contract: {needle!r}")


def _assert_cull_mutation_is_rejected(
    source: str, needle: str, replacement: str
) -> None:
    mutant, count = source.replace(needle, replacement, 1), source.count(needle)
    assert count == 1, needle
    try:
        _assert_cull_admission_contract(mutant)
    except AssertionError:
        return
    raise AssertionError(f"mutation escaped the cull admission contract: {needle!r}")


def _assert_splat_mutation_is_rejected(
    source: str, needle: str, replacement: str
) -> None:
    mutant, count = source.replace(needle, replacement, 1), source.count(needle)
    assert count == 1, needle
    try:
        _assert_splat_geometry_contract(mutant)
    except AssertionError:
        return
    raise AssertionError(f"mutation escaped the splat geometry contract: {needle!r}")


class ReferenceCadenceTests(unittest.TestCase):
    def test_reference_cadence_and_origin_invalidation(self) -> None:
        source = PIPELINE.read_text(encoding="utf-8")
        _assert_reference_cadence_contract(source)
        _assert_capture_origin_invariants(source)
        _assert_double_buffer_lifecycle_contract(
            source, CULL.read_text(encoding="utf-8")
        )

        cadence_mutation_markers = (
            "return ((1 << CUBE_FACE_COUNT) - 1) & ~captured",
            "var rate := maxf(1.0 / BLEND_DURATION, smoothed_speed / GRID_STEP)",
            'var required: Array = capture_result.get("layers_requiring_paint", recaptured)',
            "_compute_face_mask(forward, GRID_CULL_COS)",
        )
        for marker in cadence_mutation_markers:
            mutated_source = source.replace(marker, "", 1)
            self.assertGreater(source.count(marker), 0, marker)
            with self.assertRaises(AssertionError):
                _assert_reference_cadence_contract(mutated_source)

        origin_mutation_markers = (
            '"captured_origins": [Vector3.ZERO, Vector3.ZERO, Vector3.ZERO],',
            'captured_face_masks[probe_index] = 0',
            'if captured_origins[probe_index] != _probe_origin(probe_index):',
            'captured_origins[probe_index] = assigned_origin',
            '"captured_origins": _captured_origin_arrays(),',
            '"capture_origin_mismatch_mask": capture_origin_mismatch_mask,',
            'reason = "one_unit_grid_origin_changed"',
            'reason = "dynamic_source_revision_changed"',
            'reason = "eye_origin_changed"',
        )
        for marker in origin_mutation_markers:
            mutated_source, count = source.replace(marker, "", 1), source.count(marker)
            self.assertGreater(count, 0, marker)
            with self.assertRaises(AssertionError):
                _assert_capture_origin_invariants(mutated_source)


def split_stages(source: str) -> dict[str, str]:
    # Match the runtime source loader and Godot importer for the shared law.
    source = source.replace('#include "res://render/effects/vegetation/vegetation_deformation.glslinc"',
                            (SHADERS / "render/effects/vegetation/vegetation_deformation.glslinc").read_text())
    stages: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in source.splitlines():
        marker = re.fullmatch(r"\s*#\[(compute|vertex|fragment)\]\s*", line)
        if marker:
            if current is not None:
                stages[current] = "\n".join(lines)
            current = marker.group(1)
            lines = []
        else:
            lines.append(line)
    if current is not None:
        stages[current] = "\n".join(lines)
    return stages


def compiler() -> str:
    result = shutil.which("glslangValidator") or shutil.which("glslang")
    assert result, "glslangValidator/glslang is required"
    return result


def compile_stage(tool: str, stage: str, source: str, directory: Path) -> None:
    # FL-4359: Godot expands res:// includes before compiling a stage. Feed the
    # same sources to glslang instead of rejecting valid runtime includes.
    def expand_includes(text: str, stack: tuple[Path, ...] = ()) -> str:
        def include(match):
            path = REPO / "godot_project" / match.group(1)
            assert path not in stack, f"cyclic shader include: {path}"
            return expand_includes(path.read_text(encoding="utf-8"), (*stack, path))
        return re.sub(r'^\s*#include\s+"res://([^"]+)"\s*$', include, text, flags=re.MULTILINE)
    source = expand_includes(source)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = {"vertex": "vert", "fragment": "frag", "compute": "comp"}[stage]
    source_path = directory / f"probe.{suffix}"
    output_path = directory / f"probe.{suffix}.spv"
    source_path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [tool, "--target-env", "vulkan1.2", "-S", suffix, "-o", str(output_path), str(source_path)],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    validator = shutil.which("spirv-val")
    if validator:
        checked = subprocess.run([validator, str(output_path)], capture_output=True, text=True, check=False)
        assert checked.returncode == 0, checked.stdout + checked.stderr


def test_actual_geometry_sources_and_persistent_resource_contract() -> None:
    pipeline = PIPELINE.read_text(encoding="utf-8")
    host = HOST.read_text(encoding="utf-8")
    capture = CAPTURE.read_text(encoding="utf-8")
    cull = CULL.read_text(encoding="utf-8")
    splat = SPLAT.read_text(encoding="utf-8")
    publish = PUBLISH.read_text(encoding="utf-8")
    vsb = VSB.read_text(encoding="utf-8")
    assert "class_name PerspectiveProbePipeline" in pipeline
    for token in ("RenderingDevice", "capture_geometry", "prepare_splat_survivors", "resolve_visible_sources", "draw_and_publish", "get_rids", "resize_candidate_extent", "dispose"):
        assert token in pipeline
    for token in ("eye_origin", "current_origin", "previous_origin", "dynamic_revision", "static_revision", "water_revision", "roundf"):
        assert token in pipeline
    assert "_build_face_matrices(camera_facts)" in pipeline
    assert "_build_face_masks(camera_facts)" in pipeline
    assert "perspective PixelCamera facts are required" in pipeline
    assert "_splat_param_bytes()" in pipeline
    assert "_publish_uniform_set" in pipeline
    # Diagnostic witness epochs occupy control.w; ordinary rendering keeps zero.
    assert "_encode_uvec4(bytes, 80, _candidate_extent.x * _candidate_extent.y, _probe_size," in pipeline
    assert "LAYER_COUNT, _vertex_witness_epoch if _raster_witness_enabled else 0)" in pipeline
    assert "D32_SFLOAT" in pipeline
    assert "DRAW_CLEAR_ALL" in pipeline and "COMPARE_OP_LESS" in pipeline
    assert "TEXTURE_USAGE_CAN_COPY_FROM_BIT" in pipeline
    assert "CANDIDATE_COLOR_COUNT := 2" in pipeline
    # Reference renderProbe law: every probe captures movers (dynamic faces and
    # actor pose faces); only sprite cards stay EYE-band (their own splat set).
    assert pipeline.count("if probe_index == PROBE_EYE and _owner_isolation_keeps_family(FAMILY_DYNAMIC):") == 3
    # Freshness remains provenance in the parameter ABI; visibility and the
    # completed-generation maps own whether an address enters Product work.
    assert "const TEXEL_LAYER_FRESH := 4" in pipeline
    assert "var layer_code := epoch | (TEXEL_LAYER_FRESH if fresh else 0)" in pipeline
    product = _expand_includes((SHADERS / "render/product/cell_owner_product.glsl").read_text(encoding="utf-8"))
    # Inspect the assembled Product across its production include boundaries.
    product_sources = _expand_includes(product)
    assert "void fl4359_texel_refuse(ivec3 address, uint code) {" in product_sources
    assert "if (epoch < VSB_STATUS_SOURCE_EPOCH_EYE" in product_sources
    # FL-4691 deferred shade: final native visibility owns the worklist. A
    # retained address can be painted when first exposed and an exact completed
    # generation is idempotent. Paired reuse additionally requires its source
    # witness to match.
    assert "if (layer_generation == 0u" in product_sources
    assert "fl4359_shaded_generation.values[survivor_id] == layer_generation" in product_sources
    visible = (SHADERS / "render/backends/perspective/cull/perspective_probe_visible_sources.glsl").read_text(encoding="utf-8")
    assert "append_missing_paint(front_bits" in visible
    assert "append_missing_paint(underlay_bits" in visible
    assert "front_paint_generation.values[source_id]" in visible
    assert "underlay_paint_generation.values[source_id]" in visible
    assert "append_shade_front" not in cull
    assert "append_shade_underlay" not in cull
    # Both dispatch modes require exact shade AND capture-source witnesses.
    # The former full-grid bypass is the stale-reflection defect, not a law.
    assert "if (!deferred" not in product_sources
    assert "fl4359_paired_shaded_generation.values[captured_index] == layer_generation" in product_sources
    assert "fl4359_paired_source_generation.values[captured_index] == captured_generation" in product_sources
    assert "bool vsb_product_lattice_anchor_matches(vec4 expected_anchor, vec4 selected_anchor) {" in product_sources
    assert 'const CANDIDATE_ATTACHMENT_NAMES := ["source_reference", "glyph_cell"]' in pipeline
    for deleted_lane in ("_previous_candidate", "ACTOR_SET_SUPERSAMPLE", "previous_set_active"):
        assert deleted_lane not in pipeline
    assert "capture_generation" in pipeline
    assert "steady_reuse" in pipeline and "recaptured_layers" in pipeline
    assert "_capture_draw_slots" in pipeline and "_release_capture_draw_resources" in pipeline
    assert "_create_persistent_splat_draw_resources" in pipeline
    assert "_active_splat_layers" in pipeline
    assert "_prepare_capture_draw" in pipeline and "_submit_capture_face" in pipeline
    assert "_submit_capture_family" not in pipeline
    assert '"capture_draw_lists": recaptured_faces' in pipeline
    assert "func _capture_tile_rect" in pipeline
    assert "terrain_rect.get_area() if bounded_rect else terrain_count" in pipeline
    # b1 owns the 32-byte range block; the recorder reconstructs it at byte96.
    assert "_encode_uvec4(bytes, 16, terrain_rect.position.x, terrain_rect.position.y" in pipeline
    assert "bytes.encode_u64(96 + offset, draw_bytes.decode_u64(offset))" in pipeline
    assert '"terrain_instances_submitted"' in pipeline
    assert '"terrain_instances_full"' in pipeline
    assert '"terrain_tiles_tested"' in pipeline
    assert '"terrain_tiles_submitted"' in pipeline
    assert "func _compact_terrain_tiles" in pipeline
    assert "func _terrain_tile_intersects_face" in pipeline
    assert "_terrain_visible_tile_ids" in pipeline
    assert "TERRAIN_COMPACT_TILE_MASK := 0x80000000" in pipeline
    assert "const TERRAIN_CONSUMER_DIRECT := 0" in pipeline
    assert "const TERRAIN_CONSUMER_REFLECTION := 1" in pipeline
    assert "const TERRAIN_CONSUMER_COUNT := 2" in pipeline
    assert "LAYER_COUNT * TERRAIN_CONSUMER_COUNT * tile_count" in pipeline
    assert "func _terrain_compact_bank_layer" in pipeline
    assert "layer + (LAYER_COUNT if consumer == TERRAIN_CONSUMER_REFLECTION else 0)" in pipeline
    assert "direct_required: bool = true" in pipeline
    terrain_prepare = pipeline[pipeline.index('var terrain_count := int(sources.get("terrain_cells", 0))') : pipeline.index('phase_us["terrain_prepare"]')]
    assert '"consumer": consumer_label' in terrain_prepare
    assert "reflection_eligible := consumer == TERRAIN_CONSUMER_REFLECTION" in terrain_prepare
    assert "capture_direct := consumer == TERRAIN_CONSUMER_DIRECT" in terrain_prepare
    assert "capture_reflection" not in terrain_prepare
    assert '"%d:%d:compact" % [layer, FAMILY_TERRAIN]' in terrain_prepare
    assert "DRAW_RECEIPT_SCHEMA_VERSION := 2" in pipeline
    assert "immutable probe draw receipt schema mismatch" in pipeline
    assert "binding = 84" in capture
    assert "TerrainVisibleTileIds" in capture
    assert "capture_draw.terrain.w & TERRAIN_COMPACT_TILE_MASK" in capture
    assert "tile_id = terrain_visible_tile_ids.ids" in capture
    assert "cell_x >= width || cell_y >= height" in capture
    assert 'var water_count := int(sources.get("water_spans", 0))' in pipeline
    assert "if water_authored_count > 0 else 0" in pipeline
    assert '"frame_resource_allocations"' in pipeline
    assert '"payload_clear_bytes": 0' in pipeline
    prepared = _gdscript_function_source(pipeline, "prepare_splat_survivors")
    assert "_release_splat_draw_resources()" not in prepared
    assert "uniform_buffer_create" not in prepared
    assert "if probe_index == 0" in pipeline
    assert "func _submit_capture_face" in pipeline and "return false" in pipeline
    assert "if not _submit_capture_face" in pipeline
    assert "state_before := _state.duplicate(true)" in pipeline
    assert "_state = state_before" in pipeline
    assert '_state["capture_face_masks"] = capture_face_masks' in pipeline
    assert "FAMILY_DYNAMIC" in pipeline
    assert "captured_face_masks" in pipeline
    for removed in (
        "CAPTURE_PAYLOAD_RECORD_BYTES", "_capture_payload_buffer",
        "CapturePayloadRecord", "capture_payload",
    ):
        assert removed not in pipeline
        assert removed not in capture
        assert removed not in splat
    # Binding 13 was repurposed for conservative underlay row demand. Keep the
    # removed payload bank absent without banning its successor's ABI slot.
    assert "binding = 13, std430) readonly buffer UnderlayRasterRowDemand" in splat
    # FL-4691 cutover: the splat carries one immutable source address in its
    # raster output; publish resolves that address from the source tables.
    assert "layout(location = 0) out uint out_source" in splat
    assert "out_source = (v_provenance.w * size * size + local + 1u)" in splat
    assert "flat out uvec4 v_identity" in splat
    assert "source_reference" in pipeline
    assert "capture_colour" not in splat and "candidate_colour" not in splat
    # The record ABI is declared by the shared presentation include, while
    # publication owns the buffer and pixel election at this root.
    publish_presentation = (SHADERS / "render/ascii/authority/probe_cell_presentation.glslinc").read_text(
        encoding="utf-8"
    )
    publish_sources = publish + "\n" + publish_presentation
    assert "struct ProbeCellResolveRecord" in publish_sources
    assert "writeonly buffer Records" in publish_sources
    # Pixel-lattice resolve: every pixel stores its own winner (or the EYE
    # background); the cell record is the cell centre pixel's election.
    assert "uint reference = sample_reference_at(pixel)" in publish_sources
    assert "cached_payload = sample_payload_from_reference(reference, payload_extent);" in publish_sources
    assert "uvec4 candidate = cached_payload;" in publish_sources
    assert "uvec4 payload = publish_center_payload" in publish_sources
    assert "probe_source_fetch(publish_center_reference, source_payload)" not in publish
    assert "bayer4" not in publish and "previous_payload" not in publish
    assert "unpackUnorm4x8(payload.y)" in publish
    assert "uint centre_background = payload.z" in publish
    assert "unpackUnorm4x8(paper_background)" in publish
    assert "mix(" not in publish
    assert "texelFetch" in publish
    for removed in ("cell_resolve", "candidate_pack", "SPLAT_SUBSAMPLES", "glyph_atlas"):
        assert removed not in pipeline
    for token in (
        "heightfield_height_tex", "texelFetch", "SceneLocalFaceRows", "SceneInstances",
        "DynamicFaceRows", "DynamicMoverInstances", "SceneWaterSpans", "FAMILY_WATER",
    ):
        assert token in capture
    for token in (
        "sampler2DArray", "capture_identity", "capture_normal_depth",
        "capture_revisions", "direction_address", "v_provenance",
        "vec3 dir = cube_direction(face, corner)",
        "cheb / max(max(abs(dir.x), abs(dir.y)), abs(dir.z))",
        "face * face_texels + local", "probe_capture_identity",
        "probe_capture_fetch(captured_source.foreground, capture_normal_depth, probe_underlay_normal_depth",
    ):
        assert token in splat
    for token in (
        "front_sample = underlay_sample;",
        "front_sample.normal_depth = texelFetch(normal_depth, p, 0);",
        "SurfaceSample underlay_sample = read_surface(true, p);",
        "result.identity = texelFetch(probe_underlay_identity, address, 0);",
        "ProbeCaptureSource source = probe_capture_source(identity, address);",
    ):
        assert token in cull
    assert "capture_world" not in splat
    for source in (pipeline, capture, splat, publish):
        # "ray" is a geometric parameter term in the finite tangent solve;
        # reject ray-marching owners without banning that ABI description.
        assert not re.search(r"\b(?:sphere|march|b64|b55)\b", source, re.IGNORECASE)
        assert "display_vp" not in source
        assert "Y-row" not in source
    assert "owner_kind = family == FAMILY_STATIC ? 1u" in capture
    assert "family == FAMILY_TERRAIN ? 2u" in capture
    assert "family == FAMILY_WATER ? 3u" in capture
    assert "family + 1u" not in capture
    assert "const uint indices[6] = uint[6](0u, 1u, 2u, 0u, 2u, 3u)" in capture
    assert "heightfield_descriptor.state.z + capture_draw.draw.w + source" in capture
    assert "if (capture_draw.terrain.z > 0u)" in capture
    assert "cell_x = capture_draw.terrain.x + cell_index % capture_draw.terrain.z" in capture
    assert "cell_y = capture_draw.terrain.y + cell_index / capture_draw.terrain.z" in capture
    assert "source = cell_y * width + cell_x" in capture
    assert "max(abs(vertex_world.x - capture.probe_origin_near.x)" in capture
    # Static ABI guard only. The full-grid runtime oracle owns the claim that
    # capture and same-source-ray SceneQuery normals actually agree.
    assert "vec3 source_origin = capture.probe_origin_near.xyz" in capture
    assert "vec3 source_ray_delta = vertex_world - source_origin" in capture
    assert "if (dot(normal, source_ray_direction) > 0.0) normal = -normal;" in capture
    _assert_cull_admission_contract(cull)
    _assert_splat_geometry_contract(splat)
    _assert_transition_lifecycle_contract(pipeline, splat)
    _assert_transition_host_mutation_is_rejected(
        pipeline,
        "fade_t += rate * dt",
        "fade_t = 1.0",
    )
    _assert_transition_host_mutation_is_rejected(
        pipeline,
        "\t\t_float_bits(fade_t), 1 if crossfading else 0, 1)",
        "\t\t_float_bits(1.0), 0, 1)",
    )
    _assert_transition_host_mutation_is_rejected(
        pipeline,
        "_encode_uvec4(bytes, 128, int(_state.get(\"generation\", 0)),",
        "_encode_uvec4(bytes, 128, 0,",
    )
    _assert_cull_mutation_is_rejected(
        cull,
        "if (front_geometry)",
        "if (front_geometry && !previous_eye_conflict())",
    )
    _assert_cull_mutation_is_rejected(
        cull,
        "bool shared_source = same_source_sample(front_sample, underlay_sample);",
        "bool shared_source = true;",
    )
    _assert_cull_mutation_is_rejected(
        cull,
        "facts.cheb = source_sample.normal_depth.w;",
        "facts.cheb = 0.0;",
    )
    _assert_cull_mutation_is_rejected(
        cull,
        "uint role = band == params.bands.x ? 0u : (band == params.bands.y ? 1u : 2u);",
        "uint role = 0u;",
    )
    _assert_splat_mutation_is_rejected(
        splat,
        "vec3 dir = cube_direction(face, corner);",
        "vec3 dir = cube_direction(face, vec2(0.5));",
    )
    assert "previous_eye_conflict" not in cull
    assert "append_raster_front(id)" in cull
    assert "append_raster_underlay(id)" in cull
    assert "fl4359_stable_identity_transition_key" not in splat
    assert "current_identity_threshold" not in splat
    assert "previous_identity_cutoff" not in splat
    assert "float cheb = probe_capture_fetch(captured_source.foreground, capture_normal_depth, probe_underlay_normal_depth, address).w;" in splat
    assert "vec3 world = origin + dir * (cheb / max(max(abs(dir.x), abs(dir.y)), abs(dir.z)));" in splat
    assert "candidate_probe" not in splat
    assert "candidate_probe == PROBE_PREVIOUS ? 8u : 0u" not in splat
    # FL-4880: EYE tie bias skips water and EYE-only movers/actors; capped at 2% of view distance.
    assert "const float EYE_RETAINED_TIE_RELATIVE = 0.02;" in splat
    assert ("if (role == 0u && v_identity.x != CAPTURE_OWNER_WATER\n"
        "            && v_identity.x != CAPTURE_OWNER_DYNAMIC && v_identity.x != CAPTURE_OWNER_ACTOR)\n"
        "        clip.z += min(0.001 * clip.w, (clip.w - clip.z) * EYE_RETAINED_TIE_RELATIVE);") in splat
    assert "clip.z = min(clip.z, clip.w)" in splat
    assert "layout(early_fragment_tests) in;" not in capture
    assert "if (coverage <= 0.05) discard;" in capture
    assert "GRID_CULL_COS" in pipeline or "-0.224951054343865" in pipeline
    assert "EYE_CULL_COS" in pipeline or "-0.13917310096006535" in pipeline
    assert "gl_FragCoord.z" not in publish
    extent = _gdscript_function_source(pipeline, "_set_candidate_extent")
    assert "_splat_extent = raster_extent" in extent
    assert "raster_extent = logical_extent" in extent
    assert "(raster_extent.x + cell_pixel_size.x - 1) / cell_pixel_size.x" in extent
    assert "(raster_extent.y + cell_pixel_size.y - 1) / cell_pixel_size.y" in extent
    assert "_build_publish_uniform_set" in pipeline and "_write_publish_params" in pipeline
    assert '"source_reference"' in pipeline
    assert "is never screen-space crossfaded" not in splat
    assert "transition_remaining" in pipeline
    assert '"transition_remaining"' in host
    splat_param_source = _gdscript_function_source(pipeline, "_splat_param_bytes")
    assert '_encode_uvec4(bytes, 128, int(_state.get("generation", 0)),' in splat_param_source
    assert "current_identity_threshold" not in splat_param_source
    assert "previous_identity_threshold" not in splat_param_source
    assert 'var probe_near := maxf(float(_active_camera.get("probe_near", 0.01))' in splat_param_source
    assert 'var probe_far := maxf(float(_active_camera.get("probe_far", 512.0))' in splat_param_source
    assert "1.0 / (probe_far - probe_near)" in splat_param_source
    assert "2.0 / float(maxi(_probe_size, 1))" not in splat_param_source
    assert "bayer_phase" not in host
    assert host.count('"transition_remaining":') == 1
    assert host.count("var _perspective_probe_pipeline") == 1
    assert "bayer4(ivec2(gl_FragCoord.xy) / 2)" not in splat
    assert "quad_scale" not in splat
    assert splat.count("vec4 capture_range;") == 2
    assert "direction_address" in splat
    assert "half_clip" not in splat and "clip.xy +=" not in splat
    assert "floatBitsToUint(1.0)" in capture
    assert "span_count = water_descriptor.counts_revision.x" in capture
    assert "bool water_span_record" in capture
    assert "water_cell_covered" not in capture
    assert "for (uint candidate = 0u; candidate < span_count; candidate++)" not in capture
    assert "span_index == 0xffffffffu" in capture
    assert "source = dims.x * dims.y" in capture
    assert "source = cell_y * dims.x + start_x" in capture
    assert "dynamic_geometry_descriptor.static_binding_counts_1.y" in capture
    assert "identity.z = water_descriptor.identity_flags.z + source" in capture
    assert "uintBitsToFloat(source)" in capture
    for source in (capture, splat, publish):
        assert "*= -1" not in source


def _interval_min_abs(lo: float, hi: float) -> float:
    if lo <= 0.0 <= hi:
        return 0.0
    return min(abs(lo), abs(hi))


def _positive_face_admits(
    forward: tuple[float, float],
    lateral_a: tuple[float, float],
    lateral_b: tuple[float, float],
    far_distance: float,
    margin: float = 2.0,
) -> bool:
    return (
        forward[1] >= -margin
        and forward[0] <= far_distance + margin
        and _interval_min_abs(*lateral_a) <= forward[1] + margin
        and _interval_min_abs(*lateral_b) <= forward[1] + margin
    )


def test_compact_tile_face_bound_is_conservative_for_sampled_intersections() -> None:
    """Every sampled point inside a +axis cube face keeps its containing AABB."""

    far_distance = 512.0
    for forward in (0.0, 1.0, 8.0, 64.0, 511.0):
        for lateral_a in (-forward, -0.5 * forward, 0.0, 0.5 * forward, forward):
            for lateral_b in (-forward, 0.0, forward):
                # Give the tile a deliberately asymmetric extent around the
                # visible point. The conservative bound may retain more tiles,
                # but it must never discard this one.
                forward_interval = (forward - 3.0, forward + 5.0)
                lateral_a_interval = (lateral_a - 4.0, lateral_a + 2.0)
                lateral_b_interval = (lateral_b - 1.0, lateral_b + 6.0)
                assert _positive_face_admits(
                    forward_interval,
                    lateral_a_interval,
                    lateral_b_interval,
                    far_distance,
                )

    assert not _positive_face_admits(
        (600.0, 608.0), (-1.0, 1.0), (-1.0, 1.0), far_distance
    )
    assert not _positive_face_admits(
        (8.0, 16.0), (40.0, 48.0), (-1.0, 1.0), far_distance
    )


def test_all_probe_shader_stages_compile() -> None:
    tool = compiler()
    with tempfile.TemporaryDirectory(prefix="fl4057-perspective-probe-") as temporary:
        directory = Path(temporary)
        for shader in (CAPTURE, SPLAT, PUBLISH, VSB, SHADERS / "render/ascii/raster/ascii_compositor.glsl"):
            stages = split_stages(shader.read_text(encoding="utf-8"))
            for stage, source in stages.items():
                compile_stage(tool, stage, source, directory / shader.stem)


def test_native_admission_uses_actual_raster_and_rejects_stale_cell_grid():
    source = (SHADERS / "render/ascii/raster/ascii_compositor.glsl").read_text()
    assert "bool probe_raster_matches = probe_native_raster_matches(" in source
    assert "textureSize(fl4359_visible_sample_payload, 0), size, ansi_size, cell_size_px" in source
    assert "&& probe_raster_matches)" in source
    assert "else if (!probe_raster_matches)" in source
    assert "all(equal(textureSize(grass_underlay_payload, 0), size))" in source
    assert "ansi_size * cell_size_px" not in source
    # The guard is not an extent relaxation: both the source raster and its
    # logical cell partition must match. The GPU suite executes this function.
    native = (SHADERS / "probe_native_cell_read.glslinc").read_text()
    assert "all(equal(sample_size, output_size))" in native
    assert "(output_size + cell_size - ivec2(1)) / cell_size, cells" in native


def test_snapped_sampling_lattice_is_shared_and_perspective_stays_unsnapped() -> None:
    """FL-4359: divergence belongs at sampling/projection, not a late fix stage.

    Ortho (snapped view lattice, direct visibility) and perspective (retained
    probe capture/splat) are legitimately different sampling inputs and may
    produce different answers. What they must share is the meaning of world
    identity, material, glyph family, lighting/effects inputs, glyph/fg-bg
    ownership, depth-aware composition, and provenance -- not independent
    definitions of a tree, blade, or transparent glyph. PixelCameraContext
    stays the single projection owner; perspective stays unsnapped
    (snap_world 0.0); probe capture keys stay world-space rigid content.
    This test endorses NO persistent-cell-history requirement: missing
    previous-cell-owner input does not establish hysteresis as root cause,
    RQ-170 rejects retired identity-history substitution and unapproved
    holds, and the reference method's stated limitation is disoccluded EYE
    content, not live-camera projection.
    """
    host = HOST.read_text(encoding="utf-8")
    assert "PixelCameraContext is the single projection owner" in host
    snap = _gdscript_function_source(host, "_pixel_camera_snap_state_from_projection")
    assert "Delete-first: do not snap perspective" in snap
    assert '"snap_world": 0.0' in snap
    assert "not projection.is_orthogonal()" in snap
    assert "_atsaturn_grid_snap_state(cam_transform, _pixel_camera_grid, snap_world)" in snap
    pipeline = PIPELINE.read_text(encoding="utf-8")
    key = _gdscript_function_source(pipeline, "_grid_capture_content_key")
    assert "Actor, water," in key
    assert "wind, view, frame clocks and aggregate scene receipts do not own this bank" in key
    assert "for binding in range(65, 75):" in key
    assert 'int(_active_receipt.get("terrain_revision", -1))' in key
    assert "origin_world_xz" in key and "cell_size_xz" in key
    assert "probe_near" in key and "probe_far" in key
    # Frustum range (probe_near/far) may enter; camera pose, view, clocks,
    # actor, water and wind must not enter the rigid key body.
    key_body = key.split("return [", 1)[1].lower()
    for banned in ("yaw", "pitch", "tick", "clock", "actor", "wind",
                   "cam_transform", "view_matrix", "water_surface"):
        assert banned not in key_body, banned


def test_godot_check_only_if_available() -> None:
    godot = shutil.which("godot") or shutil.which("godot4")
    if not godot:
        return
    result = subprocess.run(
        [godot, "--headless", "--path", str(REPO / "godot_project"), "--editor", "--quit", "--check-only"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
