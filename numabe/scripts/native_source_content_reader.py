"""Bounded reader for FL-4691 native-source-content sidecars.

The raw sidecar is a sequence of 144-byte SourceFacts records.  The file is
memory-mapped and the lookup index stores one packed integer per record; it does
not materialize a million-record Python dictionary.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import mmap
import struct
from array import array
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "fl4359.native_source_content.v1"
SKY_CONTEXT_SCHEMA = "fl4359.native_sky_context.v1"
RECORD_BYTES = 144
RECORD_LAYOUT = (
    "key", "identity", "world", "source_revisions", "anchor_source",
    "payload", "colour", "normal_depth", "surface_meta",
)
COLOUR_FIELD = "effective_geometry_bank_colour_vec4"
COLOUR_SEMANTICS = "actor_uv_coverage_subkind_or_meadow_vegetation_pose_vec4"
REFLECTION_STATUS = "captured_selected_water_reflection_facts"
REFLECTION_PLAN = (
    "water_v2_requested_bank_address_selected_reflection_bindings_106_108_"
    "identity_normal_depth_surface_meta_after_effective_geometry_election"
)
REFLECTION_PLAN_V3 = (
    "water_v3_requested_bank_address_resolves_product_owned_append_only_fact_"
    "without_native_attachment_reload"
)
# b106/b107/b108 are the Product water-reflection inputs. They are copied into
# a separate fixed-width sidecar at the same producer-retained physical layer
# join as the native source record. A missing or mismatched sidecar remains an
# explicit refusal; the reader never samples a later live attachment.
REFLECTION_FACTS_SCHEMA = "fl4359.native_reflection_facts.v1"
REFLECTION_FACTS_SCHEMA_V2 = "fl4359.native_reflection_facts.v2"
REFLECTION_FACTS_SCHEMA_V3 = "fl4359.native_reflection_facts.v3.product_lineage"
REFLECTION_FACTS_STATUS = "captured_selected_water_reflection_facts"
REFLECTION_BINDING_SOURCE = "PerspectiveProbePipeline.probe_reflection_attachment_rids"
REFLECTION_BINDING_SOURCE_V3 = "PerspectiveProbePipeline.reflection_lineage_receipt"
REFLECTION_ADDRESS_LAW = (
    "product_capture_adapter.fl4359_captured_reflected_material(address)"
)
REFLECTION_GENERATION_LIFETIME = "producer_retained_physical_layer"
REFLECTION_GENERATION_LIFETIME_V3 = "product_append_only_flight_fact"
REFLECTION_RECORD_BYTES = 64
REFLECTION_RECORD_LAYOUT = ("key", "identity_106", "normal_107", "meta_108")
REFLECTION_RECORD_LAYOUT_V3 = ("key", "identity_106", "normal_bits_107", "meta_108")
MAX_REFLECTION_RECORDS = 262_144
WATER_PAYLOAD_OWNER = 5
MAX_RECORDS = 1_048_576
TEXEL_SHADE_PARAM_BYTES = 18 * 16 + 32 + 80
TEXEL_SHADE_LAYER_COUNT = 18
_INDEX_BITS = 20
_INDEX_MASK = (1 << _INDEX_BITS) - 1


def _reflection_schema(metadata: Mapping[str, Any]) -> str:
    return str(metadata.get("reflection_facts_schema", REFLECTION_FACTS_SCHEMA))


def _reflection_plan_for_schema(schema: str) -> str:
    return REFLECTION_PLAN_V3 if schema == REFLECTION_FACTS_SCHEMA_V3 else REFLECTION_PLAN


def _reflection_binding_source_for_schema(schema: str) -> str:
    return REFLECTION_BINDING_SOURCE_V3 if schema == REFLECTION_FACTS_SCHEMA_V3 else REFLECTION_BINDING_SOURCE


def _reflection_generation_lifetime_for_schema(schema: str) -> str:
    return (
        REFLECTION_GENERATION_LIFETIME_V3
        if schema == REFLECTION_FACTS_SCHEMA_V3
        else REFLECTION_GENERATION_LIFETIME
    )


def _reflection_record_layout_for_schema(schema: str) -> tuple[str, ...]:
    return REFLECTION_RECORD_LAYOUT_V3 if schema == REFLECTION_FACTS_SCHEMA_V3 else REFLECTION_RECORD_LAYOUT


def _reflection_schema_supported(schema: str) -> bool:
    return schema in (
        REFLECTION_FACTS_SCHEMA,
        REFLECTION_FACTS_SCHEMA_V2,
        REFLECTION_FACTS_SCHEMA_V3,
    )


class NativeSourceContentError(ValueError):
    """The sidecar is malformed or its declared joins do not match."""


def _finite_vector(value: Any, label: str, length: int = 3) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise NativeSourceContentError(f"{label}_invalid")
    if any(isinstance(component, bool) or not isinstance(component, (int, float))
           for component in value):
        raise NativeSourceContentError(f"{label}_invalid")
    result = tuple(float(component) for component in value)
    if not all(math.isfinite(component) for component in result):
        raise NativeSourceContentError(f"{label}_nonfinite")
    return result


def _validate_param_redundancy(
    params: Mapping[str, Any], raw_params: bytes, prefix: str,
) -> None:
    """Require every JSON UBO summary field to agree with its packed bytes."""
    origins = params.get("layer_origins")
    codes = params.get("layer_codes")
    faces = params.get("layer_faces")
    capture_range = params.get("capture_range")
    if not isinstance(origins, (list, tuple)) or len(origins) != TEXEL_SHADE_LAYER_COUNT \
            or not isinstance(codes, (list, tuple)) or len(codes) != TEXEL_SHADE_LAYER_COUNT \
            or not isinstance(faces, (list, tuple)) or len(faces) != TEXEL_SHADE_LAYER_COUNT:
        raise NativeSourceContentError(f"{prefix}_layer_snapshot_invalid")
    for layer in range(TEXEL_SHADE_LAYER_COUNT):
        expected_origin = struct.unpack_from("<3f", raw_params, layer * 16)
        actual_origin = _finite_vector(origins[layer], f"{prefix}_layer_origin")
        if any(struct.pack("<f", float(value)) != raw_params[layer * 16 + component * 4:layer * 16 + component * 4 + 4]
               for component, value in enumerate(actual_origin)):
            raise NativeSourceContentError(f"{prefix}_origin_bytes_mismatch")
        expected_code = struct.unpack_from("<f", raw_params, layer * 16 + 12)[0]
        code = codes[layer]
        if isinstance(code, bool) or not isinstance(code, int) or code < 0 \
                or not math.isfinite(expected_code) or int(expected_code) != code \
                or float(code) != expected_code:
            raise NativeSourceContentError(f"{prefix}_code_bytes_mismatch")
        face = faces[layer]
        if isinstance(face, bool) or not isinstance(face, int) or face < 0 or face > 5 \
                or face != layer % 6:
            raise NativeSourceContentError(f"{prefix}_layer_face_invalid")
    range_offset = TEXEL_SHADE_LAYER_COUNT * 16 + 16
    expected_range = struct.unpack_from("<3f", raw_params, range_offset)
    if not isinstance(capture_range, (list, tuple)) or len(capture_range) != 3 \
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(float(value)) for value in capture_range) \
            or any(struct.pack("<f", float(value)) != raw_params[range_offset + component * 4:range_offset + component * 4 + 4]
                   for component, value in enumerate(capture_range)):
        raise NativeSourceContentError(f"{prefix}_range_bytes_mismatch")


def _validate_frozen_product_inputs(frozen: Mapping[str, Any]) -> None:
    """Validate retained sky inputs without request-specific frame joins."""
    for key, expected in (
        ("schema", SKY_CONTEXT_SCHEMA),
        ("status", "valid"),
        ("capture_boundary", "same_frame_paint_completion"),
        ("source_production_owner", "product_capture_adapter.fl4359_texel_shade"),
        ("source_context_join", "same_frame_frozen_source_paint_state"),
        ("source_generation_join", "same_frame_typed_token_from_product_dispatch"),
        ("product_observer_ray_law", "product_observer_cube_direction(face, (texel + 0.5) / probe_size)"),
        ("product_sky_ray_origin_law", "texel_shade_params.layer_origins[layer]"),
        ("context_owner", "ASCIICompositorEffect.frozen_frame_state"),
    ):
        if frozen.get(key) != expected:
            raise NativeSourceContentError(f"sky_context_retained_{key}_invalid")
    render_frame = _required_int(
        frozen.get("render_frame_index"), "sky_context_retained_render_frame_index"
    )
    source_render_frame = _required_int(
        frozen.get("source_render_frame_index"),
        "sky_context_retained_source_render_frame_index",
    )
    if render_frame < 0 or source_render_frame != render_frame:
        raise NativeSourceContentError("sky_context_retained_render_frame_invalid")
    for label in ("sun_direction", "moon_direction", "camera_ray_origin",
                  "scene_camera_origin", "camera_forward", "camera_right", "camera_up"):
        _finite_vector(frozen.get(label), f"sky_context_retained_{label}")
    for label in ("product_frame_time_seconds", "frame_clock_seconds",
                  "celestial_time", "night_factor", "moon_angular_radius",
                  "camera_fov", "camera_near", "camera_far", "cloud_plane_y",
                  "cloud_time_seconds", "ambient_floor", "cloud_opacity"):
        value = frozen.get(label)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)):
            raise NativeSourceContentError(f"sky_context_retained_{label}_invalid")
    for label in ("night_enabled", "clouds_enabled", "cloud_density_enabled",
                  "cloud_shadow_deep_enabled"):
        if not isinstance(frozen.get(label), bool):
            raise NativeSourceContentError(f"sky_context_retained_{label}_invalid")
    weather_kind = frozen.get("weather_kind")
    if isinstance(weather_kind, bool) or not isinstance(weather_kind, int) \
            or weather_kind < 0 or weather_kind > 7:
        raise NativeSourceContentError("sky_context_retained_weather_kind_invalid")
    if not isinstance(frozen.get("weather_kind_name"), str) \
            or not frozen["weather_kind_name"]:
        raise NativeSourceContentError("sky_context_retained_weather_kind_name_invalid")
    matrix = frozen.get("world_to_clip_matrix")
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 16 \
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(float(value)) for value in matrix):
        raise NativeSourceContentError("sky_context_retained_world_to_clip_matrix_invalid")


def validate_sky_context(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the frozen sky inputs that accompany native source records."""
    context = metadata.get("sky_context")
    if not isinstance(context, Mapping):
        raise NativeSourceContentError("sky_context_missing")
    if context.get("schema") != SKY_CONTEXT_SCHEMA or context.get("status") != "valid":
        raise NativeSourceContentError("sky_context_status_invalid")
    if context.get("capture_boundary") != "same_frame_paint_completion":
        raise NativeSourceContentError("sky_context_boundary_invalid")
    if context.get("source_production_owner") != "product_capture_adapter.fl4359_texel_shade":
        raise NativeSourceContentError("sky_context_source_owner_invalid")
    if context.get("source_context_join") != "same_frame_frozen_source_paint_state":
        raise NativeSourceContentError("sky_context_join_invalid")
    if context.get("source_generation_join") != "same_frame_typed_token_from_product_dispatch":
        raise NativeSourceContentError("sky_context_generation_join_invalid")
    render_frame = _required_int(metadata.get("render_frame_index"), "render_frame_index")
    if _required_int(context.get("render_frame_index"), "sky_context_render_frame_index") != render_frame:
        raise NativeSourceContentError("sky_context_render_frame_join_mismatch")
    frame_index = _required_int(metadata.get("frame_index"), "frame_index")
    sequence_frame = _required_int(metadata.get("sequence_frame"), "sequence_frame")
    if _required_int(context.get("frame_index"), "sky_context_frame_index") != frame_index:
        raise NativeSourceContentError("sky_context_frame_join_mismatch")
    if _required_int(context.get("sequence_frame"), "sky_context_sequence_frame") != sequence_frame:
        raise NativeSourceContentError("sky_context_sequence_frame_join_mismatch")
    for label in ("sun_direction", "moon_direction", "camera_ray_origin",
                  "scene_camera_origin", "camera_forward", "camera_right", "camera_up"):
        _finite_vector(context.get(label), f"sky_context_{label}")
    for label in ("product_frame_time_seconds", "frame_clock_seconds",
                  "celestial_time", "night_factor", "moon_angular_radius",
                  "camera_fov", "camera_near", "camera_far", "cloud_plane_y",
                  "cloud_time_seconds", "ambient_floor", "cloud_opacity"):
        value = context.get(label)
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(float(value)):
            raise NativeSourceContentError(f"sky_context_{label}_invalid")
    if not isinstance(context.get("night_enabled"), bool):
        raise NativeSourceContentError("sky_context_night_enabled_invalid")
    for label in ("clouds_enabled", "cloud_density_enabled", "cloud_shadow_deep_enabled"):
        if not isinstance(context.get(label), bool):
            raise NativeSourceContentError(f"sky_context_{label}_invalid")
    weather_kind = context.get("weather_kind")
    if isinstance(weather_kind, bool) or not isinstance(weather_kind, int) \
            or weather_kind < 0 or weather_kind > 7:
        raise NativeSourceContentError("sky_context_weather_kind_invalid")
    if not isinstance(context.get("weather_kind_name"), str) \
            or not context["weather_kind_name"]:
        raise NativeSourceContentError("sky_context_weather_kind_name_invalid")
    matrix = context.get("world_to_clip_matrix")
    if not isinstance(matrix, (list, tuple)) or len(matrix) != 16:
        raise NativeSourceContentError("sky_context_world_to_clip_matrix_invalid")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(float(value)) for value in matrix):
        raise NativeSourceContentError("sky_context_world_to_clip_matrix_nonfinite")
    if context.get("product_observer_ray_law") != (
        "product_observer_cube_direction(face, (texel + 0.5) / probe_size)"
    ):
        raise NativeSourceContentError("sky_context_observer_ray_law_invalid")
    if context.get("product_sky_ray_origin_law") != (
        "texel_shade_params.layer_origins[layer]"
    ):
        raise NativeSourceContentError("sky_context_ray_origin_law_invalid")
    params_by_bank = context.get("product_texel_shade_params_by_bank")
    tokens_by_bank = context.get("sky_generation_tokens_by_bank")
    if not isinstance(params_by_bank, Mapping) or not isinstance(tokens_by_bank, Mapping):
        raise NativeSourceContentError("sky_context_product_params_missing")
    expected_param_hex_length = TEXEL_SHADE_PARAM_BYTES * 2
    for bank in ("front", "underlay"):
        params = params_by_bank.get(bank)
        tokens = tokens_by_bank.get(bank)
        if not isinstance(params, Mapping) or not isinstance(tokens, (list, tuple)) \
                or not tokens:
            raise NativeSourceContentError("sky_context_product_params_missing")
        params_hex = params.get("params_hex")
        if not isinstance(params_hex, str) or len(params_hex) != expected_param_hex_length:
            raise NativeSourceContentError("sky_context_product_params_bytes_invalid")
        try:
            raw_params = bytes.fromhex(params_hex)
        except ValueError as exc:
            raise NativeSourceContentError("sky_context_product_params_bytes_invalid") from exc
        if len(raw_params) != TEXEL_SHADE_PARAM_BYTES:
            raise NativeSourceContentError("sky_context_product_params_bytes_invalid")
        probe_size, layer_count, generation = struct.unpack_from(
            "<3I", raw_params, TEXEL_SHADE_LAYER_COUNT * 16
        )
        if (
            isinstance(params.get("probe_size"), bool)
            or not isinstance(params.get("probe_size"), int)
            or int(params["probe_size"]) != probe_size
            or probe_size <= 0
            or isinstance(params.get("layer_count"), bool)
            or not isinstance(params.get("layer_count"), int)
            or int(params["layer_count"]) != TEXEL_SHADE_LAYER_COUNT
            or layer_count != TEXEL_SHADE_LAYER_COUNT
            or isinstance(params.get("generation"), bool)
            or not isinstance(params.get("generation"), int)
            or int(params["generation"]) != generation
            or generation <= 0
            or generation > 0x7FFFFFFF
        ):
            raise NativeSourceContentError("sky_context_product_params_header_invalid")
        expected_token = 0x80000000 | generation
        for token in tokens:
            if isinstance(token, bool) or not isinstance(token, int) \
                    or token < 0 or token > 0xFFFFFFFF:
                raise NativeSourceContentError("sky_context_generation_token_invalid")
        if expected_token not in tokens:
            raise NativeSourceContentError("sky_context_generation_token_mismatch")
        origins = params.get("layer_origins")
        codes = params.get("layer_codes")
        faces = params.get("layer_faces")
        capture_range = params.get("capture_range")
        if not isinstance(origins, (list, tuple)) or len(origins) != TEXEL_SHADE_LAYER_COUNT \
                or not isinstance(codes, (list, tuple)) or len(codes) != TEXEL_SHADE_LAYER_COUNT \
                or not isinstance(faces, (list, tuple)) or len(faces) != TEXEL_SHADE_LAYER_COUNT:
            raise NativeSourceContentError("sky_context_layer_snapshot_invalid")
        for origin in origins:
            _finite_vector(origin, "sky_context_layer_origin")
        for code in codes:
            if isinstance(code, bool) or not isinstance(code, int) or code < 0:
                raise NativeSourceContentError("sky_context_layer_code_invalid")
        for layer, face in enumerate(faces):
            if isinstance(face, bool) or not isinstance(face, int) or face < 0 or face > 5 \
                    or face != layer % 6:
                raise NativeSourceContentError("sky_context_layer_face_invalid")
        if not isinstance(capture_range, (list, tuple)) or len(capture_range) != 3 \
                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(float(value)) for value in capture_range):
            raise NativeSourceContentError("sky_context_capture_range_invalid")
        _validate_param_redundancy(params, raw_params, "sky_context")
    retained = context.get("sky_context_by_bank_layer")
    snapshots = context.get("sky_context_snapshots")
    if not isinstance(retained, Mapping) or not isinstance(snapshots, (list, tuple)):
        raise NativeSourceContentError("sky_context_retained_history_missing")
    if len(snapshots) > 5:
        raise NativeSourceContentError("sky_context_snapshot_capacity_exceeded")
    retained_count = 0
    for bank in ("front", "underlay"):
        bank_records = retained.get(bank)
        if not isinstance(bank_records, Mapping) or len(bank_records) > TEXEL_SHADE_LAYER_COUNT:
            raise NativeSourceContentError("sky_context_retained_bank_invalid")
        retained_count += len(bank_records)
        for layer_key, record in bank_records.items():
            if isinstance(layer_key, bool) or not isinstance(layer_key, str):
                raise NativeSourceContentError("sky_context_retained_layer_invalid")
            try:
                layer = int(layer_key, 10)
            except ValueError as exc:
                raise NativeSourceContentError("sky_context_retained_layer_invalid") from exc
            if str(layer) != layer_key or not 0 <= layer < TEXEL_SHADE_LAYER_COUNT:
                raise NativeSourceContentError("sky_context_retained_layer_invalid")
            if not isinstance(record, Mapping) or record.get("bank") != bank \
                    or record.get("physical_layer") != layer:
                raise NativeSourceContentError("sky_context_retained_record_invalid")
            if _required_int(record.get("source_context_snapshot_id"),
                             "sky_context_snapshot_id", positive=True) <= 0:
                raise NativeSourceContentError("sky_context_retained_snapshot_invalid")
            selected_token = record.get("selected_sky_generation_token")
            if isinstance(selected_token, bool) or not isinstance(selected_token, int) \
                    or selected_token <= 0 or selected_token > 0xFFFFFFFF \
                    or not (selected_token & 0x80000000) \
                    or selected_token not in tokens_by_bank[bank]:
                raise NativeSourceContentError("sky_context_retained_token_invalid")
            selected = record.get("selected_product_texel_shade_params")
            if not isinstance(selected, Mapping):
                raise NativeSourceContentError("sky_context_retained_params_missing")
            selected_hex = selected.get("params_hex")
            if not isinstance(selected_hex, str) or len(selected_hex) != expected_param_hex_length:
                raise NativeSourceContentError("sky_context_retained_params_invalid")
            try:
                selected_raw = bytes.fromhex(selected_hex)
            except ValueError as exc:
                raise NativeSourceContentError("sky_context_retained_params_invalid") from exc
            if len(selected_raw) != TEXEL_SHADE_PARAM_BYTES:
                raise NativeSourceContentError("sky_context_retained_params_invalid")
            selected_probe, selected_layers, selected_generation = struct.unpack_from(
                "<3I", selected_raw, TEXEL_SHADE_LAYER_COUNT * 16
            )
            if selected_probe <= 0 or selected_layers != TEXEL_SHADE_LAYER_COUNT \
                    or selected_probe != int(metadata.get("probe_size", 0)) \
                    or selected.get("probe_size") != selected_probe \
                    or selected.get("layer_count") != TEXEL_SHADE_LAYER_COUNT \
                    or selected.get("generation") != selected_generation \
                    or selected_generation <= 0 or selected_generation > 0x7FFFFFFF \
                    or selected_token != (0x80000000 | selected_generation):
                raise NativeSourceContentError("sky_context_retained_params_header_invalid")
            selected_origins = selected.get("layer_origins")
            selected_codes = selected.get("layer_codes")
            selected_faces = selected.get("layer_faces")
            selected_range = selected.get("capture_range")
            if not isinstance(selected_origins, (list, tuple)) \
                    or len(selected_origins) != TEXEL_SHADE_LAYER_COUNT \
                    or not isinstance(selected_codes, (list, tuple)) \
                    or len(selected_codes) != TEXEL_SHADE_LAYER_COUNT \
                    or not isinstance(selected_faces, (list, tuple)) \
                    or len(selected_faces) != TEXEL_SHADE_LAYER_COUNT:
                raise NativeSourceContentError("sky_context_retained_layer_snapshot_invalid")
            for origin in selected_origins:
                _finite_vector(origin, "sky_context_retained_layer_origin")
            for code in selected_codes:
                if isinstance(code, bool) or not isinstance(code, int) or code < 0:
                    raise NativeSourceContentError("sky_context_retained_layer_code_invalid")
            for selected_layer, face in enumerate(selected_faces):
                if isinstance(face, bool) or not isinstance(face, int) \
                        or face < 0 or face > 5 or face != selected_layer % 6:
                    raise NativeSourceContentError("sky_context_retained_layer_face_invalid")
            _validate_param_redundancy(
                selected, selected_raw, "sky_context_retained"
            )
            frozen = record.get("frozen_product_inputs")
            if not isinstance(frozen, Mapping):
                raise NativeSourceContentError("sky_context_retained_inputs_missing")
            _validate_frozen_product_inputs(frozen)
    if retained_count > 36:
        raise NativeSourceContentError("sky_context_retained_capacity_exceeded")
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping) or _required_int(
                snapshot.get("snapshot_id"), "sky_context_snapshot_id", positive=True) <= 0 \
                or snapshot.get("schema") != SKY_CONTEXT_SCHEMA \
                or snapshot.get("status") != "valid" \
                or not isinstance(snapshot.get("product_texel_shade_params_by_bank"), Mapping) \
                or not isinstance(snapshot.get("sky_generation_tokens_by_bank"), Mapping):
            raise NativeSourceContentError("sky_context_snapshot_invalid")
    return context


def _required_int(value: Any, label: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NativeSourceContentError(f"{label}_invalid")
    result = int(value)
    if positive and result <= 0:
        raise NativeSourceContentError(f"{label}_invalid")
    return result


def validate_native_source_content_join(
    metadata: Mapping[str, Any], *, frame_index: int, sequence_frame: int,
    request_id: int, probe_size: int,
) -> None:
    """Validate the producer's frame/request/sequence/probe join before mmap.

    This is deliberately separate from raw record decoding: a valid byte file
    with a stale receipt is still unusable temporal evidence.
    """
    if metadata.get("schema") != SCHEMA or metadata.get("status") != "valid":
        raise NativeSourceContentError("native_source_content_status_invalid")
    if metadata.get("payload_binding") != "post_effect_display_payloads":
        raise NativeSourceContentError("payload_binding_mismatch")
    if tuple(metadata.get("record_layout", ())) != RECORD_LAYOUT:
        raise NativeSourceContentError("record_layout_mismatch")
    if metadata.get("colour_field") != COLOUR_FIELD:
        raise NativeSourceContentError("colour_field_mismatch")
    if metadata.get("colour_semantics") != COLOUR_SEMANTICS:
        raise NativeSourceContentError("colour_semantics_mismatch")
    if metadata.get("reflection_status") != REFLECTION_STATUS:
        raise NativeSourceContentError("reflection_status_mismatch")
    reflection_schema = _reflection_schema(metadata)
    if not _reflection_schema_supported(reflection_schema):
        raise NativeSourceContentError("reflection_facts_schema_mismatch")
    if metadata.get("reflection_plan") != _reflection_plan_for_schema(reflection_schema):
        raise NativeSourceContentError("reflection_plan_mismatch")
    if metadata.get("reflection_facts_status") != REFLECTION_FACTS_STATUS:
        raise NativeSourceContentError("reflection_facts_status_mismatch")
    if metadata.get("reflection_binding_source") != _reflection_binding_source_for_schema(reflection_schema):
        raise NativeSourceContentError("reflection_binding_source_mismatch")
    if metadata.get("reflection_address_law") != REFLECTION_ADDRESS_LAW:
        raise NativeSourceContentError("reflection_address_law_mismatch")
    if metadata.get("reflection_generation_lifetime") != _reflection_generation_lifetime_for_schema(reflection_schema):
        raise NativeSourceContentError("reflection_generation_lifetime_mismatch")
    if _required_int(metadata.get("record_bytes"), "record_bytes") != RECORD_BYTES:
        raise NativeSourceContentError("record_bytes_mismatch")
    count = _required_int(metadata.get("record_count"), "record_count")
    declared_bytes = _required_int(metadata.get("bytes"), "bytes")
    if count <= 0 or count > MAX_RECORDS or declared_bytes != count * RECORD_BYTES:
        raise NativeSourceContentError("record_count_bytes_mismatch")
    if _required_int(metadata.get("frame_index"), "frame_index") != int(frame_index):
        raise NativeSourceContentError("frame_index_join_mismatch")
    if _required_int(metadata.get("sequence_frame"), "sequence_frame") != int(sequence_frame):
        raise NativeSourceContentError("sequence_frame_join_mismatch")
    if _required_int(metadata.get("request_id"), "request_id", positive=True) != int(request_id):
        raise NativeSourceContentError("request_id_join_mismatch")
    if _required_int(metadata.get("probe_size"), "probe_size", positive=True) != int(probe_size):
        raise NativeSourceContentError("probe_size_join_mismatch")
    layer_count = _required_int(metadata.get("layer_count"), "layer_count", positive=True)
    if _required_int(metadata.get("render_frame_index"), "render_frame_index") < 0:
        raise NativeSourceContentError("render_frame_index_invalid")
    if _required_int(metadata.get("epoch"), "epoch", positive=True) <= 0:
        raise NativeSourceContentError("epoch_invalid")
    join = metadata.get("join")
    if not isinstance(join, Mapping):
        raise NativeSourceContentError("join_missing")
    for key, expected in (
        ("frame_index", frame_index), ("sequence_frame", sequence_frame),
        ("request_id", request_id), ("render_frame_index", metadata["render_frame_index"]),
        ("probe_size", probe_size), ("layer_count", layer_count),
        ("epoch", metadata["epoch"]),
    ):
        if _required_int(join.get(key), f"join_{key}") != int(expected):
            raise NativeSourceContentError(f"join_{key}_mismatch")
    # A typed sky record is useful only with the same frozen sky inputs that
    # painted it.  Validate this before opening the mmap so an otherwise sound
    # raw record cannot silently become a temporal source fact.
    validate_sky_context(metadata)


class NativeSourceContentReader:
    def __init__(self, path: str | Path, metadata: Mapping[str, Any] | None = None):
        self.path = Path(path)
        self._file = None
        self._mmap = None
        self._reflection_file = None
        self._reflection_mmap = None
        self.reflection_record_count = 0
        self.reflection_record_bytes = REFLECTION_RECORD_BYTES
        self.reflection_record_layout = REFLECTION_RECORD_LAYOUT
        self._packed_reflection_index = array("Q")
        try:
            self._file = self.path.open("rb")
            size = self.path.stat().st_size
            if size == 0 or size % RECORD_BYTES:
                raise NativeSourceContentError("record_bytes_alignment_invalid")
            self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            self.record_count = size // RECORD_BYTES
            if self.record_count > MAX_RECORDS:
                raise NativeSourceContentError("record_count_exceeds_capacity")
            self.metadata = dict(metadata or {})
            reflection_schema = _reflection_schema(self.metadata)
            if reflection_schema == REFLECTION_FACTS_SCHEMA_V2:
                self.reflection_record_bytes = 80
                self.reflection_record_layout = REFLECTION_RECORD_LAYOUT + ("shade_reflection_lifetime",)
            elif reflection_schema == REFLECTION_FACTS_SCHEMA_V3:
                self.reflection_record_layout = REFLECTION_RECORD_LAYOUT_V3
            self._validate_metadata()
            self._packed_index = self._build_index()
            self._open_reflection_sidecar()
        except Exception:
            self.close()
            raise

    def _validate_metadata(self) -> None:
        if self.metadata.get("schema", SCHEMA) != SCHEMA:
            raise NativeSourceContentError("schema_mismatch")
        declared_bytes = self.metadata.get("bytes")
        if declared_bytes is not None and _required_int(declared_bytes, "bytes") != len(self._mmap):
            raise NativeSourceContentError("metadata_byte_count_mismatch")
        declared_count = self.metadata.get("record_count")
        if declared_count is not None and _required_int(declared_count, "record_count") != self.record_count:
            raise NativeSourceContentError("metadata_record_count_mismatch")
        declared_record_bytes = self.metadata.get("record_bytes")
        if declared_record_bytes is not None and _required_int(declared_record_bytes, "record_bytes") != RECORD_BYTES:
            raise NativeSourceContentError("metadata_record_bytes_mismatch")
        declared_status = self.metadata.get("status")
        if declared_status is not None and declared_status != "valid":
            raise NativeSourceContentError("sidecar_status_not_valid")
        if declared_status == "valid":
            reflection_schema = _reflection_schema(self.metadata)
            if not _reflection_schema_supported(reflection_schema):
                raise NativeSourceContentError("reflection_facts_schema_mismatch")
            if self.metadata.get("reflection_facts_status") != REFLECTION_FACTS_STATUS:
                raise NativeSourceContentError("reflection_facts_status_mismatch")
            if self.metadata.get("reflection_plan") != _reflection_plan_for_schema(reflection_schema):
                raise NativeSourceContentError("reflection_plan_mismatch")
            if self.metadata.get("reflection_binding_source") != _reflection_binding_source_for_schema(reflection_schema):
                raise NativeSourceContentError("reflection_binding_source_mismatch")
            if self.metadata.get("reflection_address_law") != REFLECTION_ADDRESS_LAW:
                raise NativeSourceContentError("reflection_address_law_mismatch")
            if self.metadata.get("reflection_generation_lifetime") != _reflection_generation_lifetime_for_schema(reflection_schema):
                raise NativeSourceContentError("reflection_generation_lifetime_mismatch")
            if tuple(self.metadata.get("reflection_record_layout", ())) != self.reflection_record_layout:
                raise NativeSourceContentError("reflection_record_layout_mismatch")
            if _required_int(self.metadata.get("reflection_record_bytes"),
                             "reflection_record_bytes") != self.reflection_record_bytes:
                raise NativeSourceContentError("reflection_record_bytes_mismatch")
            reflection_count = _required_int(
                self.metadata.get("reflection_record_count"),
                "reflection_record_count",
            )
            if reflection_count < 0 or reflection_count > MAX_REFLECTION_RECORDS:
                raise NativeSourceContentError("reflection_record_count_invalid")
            if _required_int(self.metadata.get("reflection_bytes"), "reflection_bytes") \
                    != reflection_count * self.reflection_record_bytes:
                raise NativeSourceContentError("reflection_count_bytes_mismatch")
            reflection_path = self.metadata.get("reflection_path")
            if not isinstance(reflection_path, str) or not reflection_path:
                raise NativeSourceContentError("reflection_path_missing")
            if reflection_schema == REFLECTION_FACTS_SCHEMA_V3:
                if _required_int(self.metadata.get("reflection_lineage_epoch"),
                                 "reflection_lineage_epoch", positive=True) <= 0:
                    raise NativeSourceContentError("reflection_lineage_epoch_invalid")
                if _required_int(self.metadata.get("reflection_lineage_capacity"),
                                 "reflection_lineage_capacity", positive=True) != MAX_REFLECTION_RECORDS:
                    raise NativeSourceContentError("reflection_lineage_capacity_mismatch")
            else:
                for key in (
                    "reflection_layer_witness_valid",
                    "reflection_layer_content_epochs",
                    "reflection_layer_content_generations",
                    "reflection_layer_render_frames",
                ):
                    value = self.metadata.get(key)
                    if not isinstance(value, list) or len(value) != TEXEL_SHADE_LAYER_COUNT:
                        raise NativeSourceContentError(f"{key}_invalid")
                if any(not isinstance(value, bool)
                       for value in self.metadata["reflection_layer_witness_valid"]):
                    raise NativeSourceContentError("reflection_layer_witness_valid_invalid")
                for key in (
                    "reflection_layer_content_epochs",
                    "reflection_layer_content_generations",
                    "reflection_layer_render_frames",
                ):
                    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
                           for value in self.metadata[key]):
                        raise NativeSourceContentError(f"{key}_invalid")
                for bank_name in ("front", "underlay"):
                    for key in (
                        f"reflection_layer_shade_witness_valid_{bank_name}",
                        f"reflection_layer_shade_generations_{bank_name}",
                        f"reflection_layer_shade_render_frames_{bank_name}",
                        f"reflection_layer_shade_params_hex_{bank_name}",
                    ):
                        value = self.metadata.get(key)
                        if not isinstance(value, list) or len(value) != TEXEL_SHADE_LAYER_COUNT:
                            raise NativeSourceContentError(f"{key}_invalid")
                    status_key = f"reflection_layer_shade_submission_status_{bank_name}"
                    status = self.metadata.get(status_key)
                    if isinstance(status, bool) or not isinstance(status, int) or status not in (1, 2, 3):
                        raise NativeSourceContentError(f"{status_key}_invalid")
                    valid_key = f"reflection_layer_shade_witness_valid_{bank_name}"
                    if any(not isinstance(value, bool) for value in self.metadata[valid_key]):
                        raise NativeSourceContentError(f"{valid_key}_invalid")
                    for key in (f"reflection_layer_shade_generations_{bank_name}", f"reflection_layer_shade_render_frames_{bank_name}"):
                        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in self.metadata[key]):
                            raise NativeSourceContentError(f"{key}_invalid")
                    params_key = f"reflection_layer_shade_params_hex_{bank_name}"
                    generation_key = f"reflection_layer_shade_generations_{bank_name}"
                    for layer, params_hex in enumerate(self.metadata[params_key]):
                        if not isinstance(params_hex, str):
                            raise NativeSourceContentError(f"{params_key}_invalid")
                        if self.metadata[valid_key][layer]:
                            if len(params_hex) != 800:
                                raise NativeSourceContentError(f"{params_key}_invalid")
                            try:
                                params_bytes = bytes.fromhex(params_hex)
                            except ValueError as exc:
                                raise NativeSourceContentError(f"{params_key}_invalid") from exc
                            if len(params_bytes) != 400:
                                raise NativeSourceContentError(f"{params_key}_invalid")
                            generation = self.metadata[generation_key][layer]
                            if struct.unpack_from("<I", params_bytes, TEXEL_SHADE_LAYER_COUNT * 16 + 8)[0] != generation:
                                raise NativeSourceContentError(f"{params_key}_generation_mismatch")
            validate_sky_context(self.metadata)
            if "probe_size" not in self.metadata or "layer_count" not in self.metadata:
                raise NativeSourceContentError("canonical_probe_extent_missing")
        declared_hash = self.metadata.get("raw_sha256")
        if declared_hash is not None:
            digest = hashlib.sha256(self._mmap).hexdigest()
            if digest != declared_hash:
                raise NativeSourceContentError("sidecar_sha256_mismatch")
        declared_probe_size = self.metadata.get("probe_size")
        declared_layer_count = self.metadata.get("layer_count")
        if declared_probe_size is not None:
            _required_int(declared_probe_size, "probe_size", positive=True)
        if declared_layer_count is not None:
            if _required_int(declared_layer_count, "layer_count", positive=True) != TEXEL_SHADE_LAYER_COUNT:
                raise NativeSourceContentError("layer_count_mismatch")

    def _build_index(self) -> array:
        # Fixed 20 low bits carry the record ordinal; the upper bits carry
        # (bank << 32) | address.  Sorting these packed values groups lookups
        # by bank/address while retaining only 8 bytes per record.
        encoded = array("Q")
        for ordinal in range(self.record_count):
            offset = ordinal * RECORD_BYTES
            address, bank, token, flags = struct.unpack_from("<4I", self._mmap, offset)
            if (
                address == 0 or address > 0x0FFFFFFF or bank > 1
                or token == 0 or (token & 0x7FFFFFFF) == 0
                or (flags & 1) == 0 or (flags & ~7) != 0
            ):
                self.close()
                raise NativeSourceContentError("record_key_invalid")
            probe_size = self.metadata.get("probe_size")
            layer_count = self.metadata.get("layer_count")
            if probe_size is not None and layer_count is not None:
                if address > int(probe_size) * int(probe_size) * int(layer_count):
                    self.close()
                    raise NativeSourceContentError("record_address_outside_probe")
            identity = struct.unpack_from("<4I", self._mmap, offset + 16)
            world = struct.unpack_from("<4f", self._mmap, offset + 32)
            revisions = struct.unpack_from("<4I", self._mmap, offset + 48)
            anchor = struct.unpack_from("<4f", self._mmap, offset + 64)
            payload = struct.unpack_from("<4I", self._mmap, offset + 80)
            colour = struct.unpack_from("<4f", self._mmap, offset + 96)
            normal_depth = struct.unpack_from("<4f", self._mmap, offset + 112)
            sky = (token & 0x80000000) != 0
            payload_word = payload[0]
            payload_owner = (payload_word >> 12) & 15
            payload_valid = (
                (payload_word & (1 << 20)) != 0 and 1 <= payload_owner <= 7
            )
            if (
                not all(math.isfinite(value) for value in world + anchor + colour + normal_depth)
                or not payload_valid
                or sky != ((flags & 4) != 0)
                or (sky and payload_owner != 6)
                or (not sky and (
                    identity[0] == 0
                    or world[3] <= 0.0
                    or revisions[3] == 0
                    or token != revisions[3]
                ))
            ):
                self.close()
                raise NativeSourceContentError("record_facts_invalid")
            if sky and self.metadata.get("status") == "valid":
                context = self.metadata.get("sky_context", {})
                tokens_by_bank = context.get("sky_generation_tokens_by_bank", {}) \
                    if isinstance(context, Mapping) else {}
                bank_name = "front" if bank == 0 else "underlay" if bank == 1 else ""
                allowed = tokens_by_bank.get(bank_name, []) \
                    if isinstance(tokens_by_bank, Mapping) else []
                layer_span = int(self.metadata.get("probe_size", 0)) ** 2
                physical_layer = (address - 1) // layer_span if layer_span > 0 else -1
                retained = context.get("sky_context_by_bank_layer", {}) \
                    if isinstance(context, Mapping) else {}
                bank_records = retained.get(bank_name, {}) \
                    if isinstance(retained, Mapping) else {}
                layer_record = bank_records.get(str(physical_layer), {}) \
                    if isinstance(bank_records, Mapping) else {}
                if bank_name == "" or token not in allowed \
                        or not isinstance(layer_record, Mapping) \
                        or layer_record.get("selected_sky_generation_token") != token \
                        or layer_record.get("physical_layer") != physical_layer:
                    self.close()
                    raise NativeSourceContentError("sky_generation_token_join_mismatch")
            encoded.append((((bank << 32) | address) << _INDEX_BITS) | ordinal)
        encoded = array("Q", sorted(encoded))
        for previous, current in zip(encoded, encoded[1:]):
            if (previous >> _INDEX_BITS) == (current >> _INDEX_BITS):
                self.close()
                raise NativeSourceContentError("duplicate_bank_address_record")
        return encoded

    def _open_reflection_sidecar(self) -> None:
        """Open and index the immutable selected-water facts sidecar."""
        if self.metadata.get("status") != "valid":
            return
        reflection_path = Path(str(self.metadata["reflection_path"]))
        if not reflection_path.is_absolute():
            reflection_path = self.path.parent / reflection_path
        self._reflection_file = reflection_path.open("rb")
        size = reflection_path.stat().st_size
        expected = int(self.metadata["reflection_bytes"])
        if size != expected or size % self.reflection_record_bytes:
            raise NativeSourceContentError("reflection_sidecar_size_invalid")
        self._reflection_mmap = mmap.mmap(
            self._reflection_file.fileno(), 0, access=mmap.ACCESS_READ
        ) if size else None
        if expected:
            digest = hashlib.sha256(self._reflection_mmap).hexdigest()
            if digest != self.metadata.get("reflection_sha256"):
                raise NativeSourceContentError("reflection_sidecar_sha256_mismatch")
        elif self.metadata.get("reflection_sha256") != hashlib.sha256(b"").hexdigest():
            raise NativeSourceContentError("reflection_sidecar_sha256_mismatch")
        self.reflection_record_count = expected // self.reflection_record_bytes
        self._packed_reflection_index = self._build_reflection_index()

    def _build_reflection_index(self) -> array:
        if self._reflection_mmap is None:
            return array("Q")
        encoded = array("Q")
        probe_size = int(self.metadata.get("probe_size", 0))
        layer_count = int(self.metadata.get("layer_count", 0))
        extent = probe_size * probe_size * layer_count
        for ordinal in range(self.reflection_record_count):
            offset = ordinal * self.reflection_record_bytes
            address, bank, token, flags = struct.unpack_from(
                "<4I", self._reflection_mmap, offset
            )
            if (
                address == 0 or bank > 1 or token == 0
                or (flags & 1) == 0
                or (flags & ~15) != 0
                or (extent > 0 and address > extent)
            ):
                raise NativeSourceContentError("reflection_record_key_invalid")
            identity = struct.unpack_from("<4I", self._reflection_mmap, offset + 16)
            normal = struct.unpack_from("<4f", self._reflection_mmap, offset + 32)
            meta = struct.unpack_from("<4I", self._reflection_mmap, offset + 48)
            if flags & 8:
                if identity[0] == 0 or not all(math.isfinite(value) for value in normal):
                    raise NativeSourceContentError("reflection_record_facts_invalid")
            elif self._reflection_mmap[offset + 16:offset + 64] != bytes(48):
                # Absence is exactly the producer's cleared attachment tuple.
                # Unknown/malformed facts cannot masquerade as a clean no-hit.
                raise NativeSourceContentError("reflection_record_absence_not_clear")
            self._reflection_lifetime(offset, address, bank)
            encoded.append((((bank << 32) | address) << _INDEX_BITS) | ordinal)
        encoded = array("Q", sorted(encoded))
        for previous, current in zip(encoded, encoded[1:]):
            if (previous >> _INDEX_BITS) == (current >> _INDEX_BITS):
                raise NativeSourceContentError("duplicate_reflection_bank_address_record")
        return encoded

    def _reflection_lifetime(self, offset: int, address: int, bank: int) -> dict[str, int] | None:
        if self.reflection_record_bytes == REFLECTION_RECORD_BYTES:
            return None
        actual, mirror, mirror_frame, shade_frame = struct.unpack_from(
            "<4I", self._reflection_mmap, offset + 64
        )
        witness = self._reflection_layer_witness(address, bank)
        if witness is None or actual == 0 or actual != mirror \
                or actual != witness["shade_generation"] \
                or mirror != witness["content_generation"] \
                or mirror_frame != witness["render_frame_index"] \
                or shade_frame != witness["shade_render_frame_index"]:
            raise NativeSourceContentError("reflection_per_texel_shade_generation_mismatch")
        return {"shade_generation": actual, "reflection_generation": mirror,
                "reflection_render_frame": mirror_frame, "shade_render_frame": shade_frame}

    def _reflection_record_for(self, bank: int, address: int) -> tuple | None:
        if self._reflection_mmap is None:
            return None
        key = (bank << 32) | address
        low = bisect.bisect_left(self._packed_reflection_index, key << _INDEX_BITS)
        high = bisect.bisect_left(self._packed_reflection_index, (key + 1) << _INDEX_BITS)
        if low == high:
            return None
        ordinal = self._packed_reflection_index[low] & _INDEX_MASK
        offset = ordinal * self.reflection_record_bytes
        key_words = struct.unpack_from("<4I", self._reflection_mmap, offset)
        identity = struct.unpack_from("<4I", self._reflection_mmap, offset + 16)
        normal_bits = struct.unpack_from("<4I", self._reflection_mmap, offset + 32)
        normal = struct.unpack_from("<4f", self._reflection_mmap, offset + 32)
        meta = struct.unpack_from("<4I", self._reflection_mmap, offset + 48)
        return (
            *key_words,
            identity,
            normal,
            meta,
            self._reflection_lifetime(offset, address, bank),
            normal_bits,
        )

    def _reflection_layer_witness(self, address: int, bank: int) -> dict[str, Any] | None:
        probe_size = int(self.metadata.get("probe_size", 0))
        if probe_size <= 0:
            return None
        layer = (address - 1) // (probe_size * probe_size)
        if layer < 0 or layer >= TEXEL_SHADE_LAYER_COUNT:
            return None
        valid = self.metadata["reflection_layer_witness_valid"][layer]
        epoch = self.metadata["reflection_layer_content_epochs"][layer]
        generation = self.metadata["reflection_layer_content_generations"][layer]
        render_frame = self.metadata["reflection_layer_render_frames"][layer]
        bank_name = "front" if bank == 0 else "underlay"
        shade_valid = self.metadata[f"reflection_layer_shade_witness_valid_{bank_name}"][layer]
        shade_status = self.metadata[f"reflection_layer_shade_submission_status_{bank_name}"]
        shade_generation = self.metadata[f"reflection_layer_shade_generations_{bank_name}"][layer]
        shade_render_frame = self.metadata[f"reflection_layer_shade_render_frames_{bank_name}"][layer]
        shade_params_hex = self.metadata[f"reflection_layer_shade_params_hex_{bank_name}"][layer]
        if not valid or epoch <= 0 or generation <= 0 or render_frame <= 0 \
                or not shade_valid or shade_status not in (1, 2) \
                or shade_generation <= 0 or shade_render_frame <= 0 \
                or not shade_params_hex:
            return None
        # Source revision tokens and Product shade generations are separate
        # namespaces. The immutable b106-b108 attachment is usable only when
        # its retained producer generation is exactly the generation of the
        # retained shade packet selected for this bank.
        if generation != shade_generation:
            raise NativeSourceContentError(
                "reflection_shade_generation_join_mismatch"
            )
        return {
            "physical_layer": layer,
            "content_epoch": epoch,
            "content_generation": generation,
            "render_frame_index": render_frame,
            "shade_submission_status": shade_status,
            "shade_generation": shade_generation,
            "shade_render_frame_index": shade_render_frame,
            "shade_params_hex": shade_params_hex,
        }

    def source_content(self, reference: int, bank: int) -> dict[str, Any]:
        """Return facts for a native reference, preserving explicit absence."""
        if isinstance(reference, bool) or not isinstance(reference, int) \
                or isinstance(bank, bool) or not isinstance(bank, int):
            return {"status": "invalid_reference", "reference": reference, "bank": bank}
        reference = int(reference)
        bank = int(bank)
        address = reference & 0x0FFFFFFF
        if (
            address == 0
            or bank not in (0, 1)
            or reference & 0x80000000
            or ((reference >> 28) & 3) >= 3
        ):
            return {"status": "invalid_reference", "reference": reference, "bank": bank}
        key = (bank << 32) | address
        low = bisect.bisect_left(self._packed_index, key << _INDEX_BITS)
        high = bisect.bisect_left(self._packed_index, (key + 1) << _INDEX_BITS)
        if low == high:
            return {"status": "missing", "reference": reference, "bank": bank, "address": address}
        packed = self._packed_index[low]
        ordinal = packed & _INDEX_MASK
        offset = ordinal * RECORD_BYTES
        effective_bank = struct.unpack_from("<I", self._mmap, offset + 4)[0]
        token = struct.unpack_from("<I", self._mmap, offset + 8)[0]
        flags = struct.unpack_from("<I", self._mmap, offset + 12)[0]
        sky_context_layer: dict[str, Any] | None = None
        if flags & 4 and self.metadata.get("status") == "valid":
            context = self.metadata.get("sky_context", {})
            probe_size = int(self.metadata.get("probe_size", 0))
            physical_layer = (address - 1) // (probe_size * probe_size) \
                if probe_size > 0 else -1
            bank_name = "front" if bank == 0 else "underlay"
            retained = context.get("sky_context_by_bank_layer", {}) \
                if isinstance(context, Mapping) else {}
            bank_records = retained.get(bank_name, {}) \
                if isinstance(retained, Mapping) else {}
            selected = bank_records.get(str(physical_layer), {}) \
                if isinstance(bank_records, Mapping) else {}
            if not isinstance(selected, Mapping) \
                    or selected.get("selected_sky_generation_token") != token:
                raise NativeSourceContentError("sky_generation_token_join_mismatch")
            sky_context_layer = dict(selected)
        payload_word = struct.unpack_from("<I", self._mmap, offset + 80)[0]
        payload_owner = (payload_word >> 12) & 15
        effective_geometry_bank = (flags >> 1) & 1
        reflection_schema = _reflection_schema(self.metadata)
        reflection_binding_source = _reflection_binding_source_for_schema(reflection_schema)
        reflection_generation_lifetime = _reflection_generation_lifetime_for_schema(reflection_schema)
        if payload_owner == WATER_PAYLOAD_OWNER:
            reflection_record = self._reflection_record_for(bank, address)
            witness = None if reflection_schema == REFLECTION_FACTS_SCHEMA_V3 \
                else self._reflection_layer_witness(address, bank)
            reflection_facts = {
                "schema": reflection_schema,
                "status": "missing",
                "reason": "selected_water_reflection_facts_not_captured",
                "requested_bank": bank,
                "effective_geometry_bank": effective_geometry_bank,
                "address": address,
                "source_generation_token": token,
                "source_generation_value": token & 0x7FFFFFFF,
                "source_generation_lifetime": reflection_generation_lifetime,
                "requested_bindings": {
                    "106": "identity", "107": "normal_depth", "108": "surface_meta",
                },
                "binding_source": reflection_binding_source,
                "address_law": REFLECTION_ADDRESS_LAW,
                "current_attachment_fallback": False,
            }
            temporal_comparison = {
                "status": "refused",
                "reason": "selected_water_reflection_facts_not_captured",
                "required": "b106_b107_b108",
            }
            if reflection_record is not None and (
                    reflection_schema == REFLECTION_FACTS_SCHEMA_V3 or witness is not None):
                reflection_address, reflection_bank, reflection_token, reflection_flags, \
                    identity_106, normal_107, meta_108, lifetime, normal_bits_107 = reflection_record
                if reflection_address != address or reflection_bank != bank \
                        or reflection_token != token \
                        or (reflection_flags & 2) != (flags & 2):
                    raise NativeSourceContentError("reflection_generation_join_mismatch")
                if reflection_schema != REFLECTION_FACTS_SCHEMA_V3 \
                        and witness["content_generation"] != witness["shade_generation"]:
                    raise NativeSourceContentError("reflection_generation_join_mismatch")
                reflection_facts = {
                    "schema": reflection_schema,
                    "status": "captured",
                    "requested_bank": bank,
                    "effective_geometry_bank": effective_geometry_bank,
                    "address": address,
                    "source_generation_token": token,
                    "source_generation_value": token & 0x7FFFFFFF,
                    "source_generation_lifetime": reflection_generation_lifetime,
                    "requested_bindings": {
                        "106": "identity", "107": "normal_depth", "108": "surface_meta",
                    },
                    "binding_source": reflection_binding_source,
                    "address_law": REFLECTION_ADDRESS_LAW,
                    "current_attachment_fallback": False,
                    "present": bool(reflection_flags & 8),
                    "identity_106": identity_106,
                    "normal_107": normal_107,
                    "normal_bits_107": normal_bits_107,
                    "meta_108": meta_108,
                    "layer_witness": witness,
                    "per_texel_lifetime": lifetime,
                    "per_texel_generation_status": (
                        "verified_by_product_lineage_shader"
                        if reflection_schema == REFLECTION_FACTS_SCHEMA_V3
                        else "verified" if lifetime is not None
                        else "missing_legacy_v1"
                    ),
                }
                if reflection_schema == REFLECTION_FACTS_SCHEMA_V3:
                    reflection_facts["lineage_witness"] = {
                        "epoch": int(self.metadata.get("reflection_lineage_epoch", 0)),
                        "capacity": int(self.metadata.get("reflection_lineage_capacity", 0)),
                        "record_layout": list(self.reflection_record_layout),
                        "producer": "ProductReflectionLineage",
                    }
                temporal_comparison = {
                    "status": "refused",
                    "reason": "retained_water_shading_inputs_not_captured",
                    "reflection_facts": "captured",
                    "required": ["depth_bed_refraction", "flow_wake", "weather_light_camera"],
                }
        else:
            reflection_facts = {
                "schema": reflection_schema,
                "status": "not_applicable",
                "owner": payload_owner,
                "requested_bank": bank,
                "effective_geometry_bank": effective_geometry_bank,
                "address": address,
            }
            temporal_comparison = {"status": "eligible_without_water_reflection_inputs"}
        # A source record can be structurally valid while still lacking the
        # water-only material facts used by temporal comparison.  Make that
        # distinction visible at the top-level API: existing callers that
        # gate on status == "ok" fail closed, while the exact native source
        # fields remain available for diagnostics and future ABI joins.
        source_status = "ok"
        if payload_owner == WATER_PAYLOAD_OWNER:
            source_status = (
                "missing_water_shading_inputs"
                if reflection_facts.get("status") == "captured"
                else "missing_reflection_facts"
            )
        return {
            "status": source_status,
            "source_facts_status": "ok",
            "reference": reference,
            "bank": bank,
            "native_bank": effective_bank,
            "effective_geometry_bank": (struct.unpack_from("<I", self._mmap, offset + 12)[0] >> 1) & 1,
            "address": address,
            "key": struct.unpack_from("<4I", self._mmap, offset),
            "identity": struct.unpack_from("<4I", self._mmap, offset + 16),
            "world": struct.unpack_from("<4f", self._mmap, offset + 32),
            "revisions": struct.unpack_from("<4I", self._mmap, offset + 48),
            "anchor": struct.unpack_from("<4f", self._mmap, offset + 64),
            "payload": struct.unpack_from("<4I", self._mmap, offset + 80),
            "colour": struct.unpack_from("<4f", self._mmap, offset + 96),
            "normal_depth": struct.unpack_from("<4f", self._mmap, offset + 112),
            "surface_meta": struct.unpack_from("<4I", self._mmap, offset + 128),
            # Water carries a producer-owned immutable reflection snapshot;
            # non-water source records avoid copying an irrelevant sidecar.
            "reflection_facts": reflection_facts,
            "temporal_comparison": temporal_comparison,
            # The full snapshot is exposed by sky_context(); returning it for
            # every native pixel would copy the retained history on each lookup.
            "sky_context_layer": sky_context_layer,
        }

    def sky_context(self) -> dict[str, Any]:
        """Return the validated same-frame typed-sky input snapshot."""
        if not self.metadata:
            raise NativeSourceContentError("sky_context_unavailable")
        return dict(validate_sky_context(self.metadata))

    def __enter__(self) -> "NativeSourceContentReader":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if getattr(self, "_reflection_mmap", None) is not None:
            self._reflection_mmap.close()
            self._reflection_mmap = None
        if getattr(self, "_reflection_file", None) is not None:
            self._reflection_file.close()
            self._reflection_file = None
        if getattr(self, "_mmap", None) is not None:
            self._mmap.close()
            self._mmap = None
        if getattr(self, "_file", None) is not None:
            self._file.close()
            self._file = None
