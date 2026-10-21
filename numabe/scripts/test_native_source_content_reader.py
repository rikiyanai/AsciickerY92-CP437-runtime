from __future__ import annotations

import copy
import struct
import tempfile
import unittest
import hashlib
from pathlib import Path

from native_source_content_reader import (
    NativeSourceContentError,
    NativeSourceContentReader,
    validate_native_source_content_join,
)



def empty_reflection_metadata() -> dict:
    # Current non-water fixture still carries the empty, immutable reflection lane.
    return {'reflection_status': 'captured_selected_water_reflection_facts',
     'reflection_plan': 'water_v2_requested_bank_address_selected_reflection_bindings_106_108_identity_normal_depth_surface_meta_after_effective_geometry_election',
     'reflection_facts_schema': 'fl4359.native_reflection_facts.v1',
     'reflection_facts_status': 'captured_selected_water_reflection_facts',
     'reflection_binding_source': 'PerspectiveProbePipeline.probe_reflection_attachment_rids',
     'reflection_address_law': 'product_capture_adapter.fl4359_captured_reflected_material(address)',
     'reflection_generation_lifetime': 'producer_retained_physical_layer',
     'reflection_record_bytes': 64,
     'reflection_record_count': 0,
     'reflection_bytes': 0,
     'reflection_path': 'reflection.bin',
     'reflection_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
     'reflection_record_layout': ['key', 'identity_106', 'normal_107', 'meta_108'],
     'reflection_capacity_records': 262144,
     'reflection_layer_witness_valid': [True,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False,
                                        False],
     'reflection_layer_content_epochs': [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     'reflection_layer_content_generations': [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     'reflection_layer_render_frames': [44, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     'reflection_layer_shade_witness_valid_front': [True,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False,
                                                    False],
     'reflection_layer_shade_witness_valid_underlay': [True,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False,
                                                       False],
     'reflection_layer_shade_submission_status_front': 1,
     'reflection_layer_shade_submission_status_underlay': 1,
     'reflection_layer_shade_generations_front': [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
     'reflection_layer_shade_generations_underlay': [1,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0,
                                                     0],
     'reflection_layer_shade_render_frames_front': [44,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0,
                                                    0],
     'reflection_layer_shade_render_frames_underlay': [44,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0,
                                                       0],
     'reflection_layer_shade_params_hex_front': ['00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000080010000120000000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 '',
                                                 ''],
     'reflection_layer_shade_params_hex_underlay': ['00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000080010000120000000100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '',
                                                    '']}

def _product_params(*, probe_size: int = 384, generation: int = 1) -> dict:
    raw = bytearray(18 * 16 + 32 + 80)
    for layer in range(18):
        struct.pack_into("<4f", raw, layer * 16, 1.0, 2.0, 3.0, 0.0)
    struct.pack_into("<3I", raw, 18 * 16, probe_size, 18, generation)
    struct.pack_into("<3f", raw, 18 * 16 + 16, 0.1, 1000.0, 0.001)
    return {
        "params_hex": bytes(raw).hex(),
        "probe_size": probe_size,
        "layer_count": 18,
        "generation": generation,
        "sky_generation_token": 0x80000000 | generation,
        "layer_origins": [[1.0, 2.0, 3.0]] * 18,
        "layer_codes": [0] * 18,
        "layer_faces": list(range(6)) * 3,
        "capture_range": [0.1, 1000.0, 0.001],
    }


def sky_context(*, render_frame_index: int = 44,
                frame_index: int = 8, sequence_frame: int = 3,
                generation: int = 1) -> dict:
    params = {
        "front": _product_params(generation=generation),
        "underlay": _product_params(generation=generation),
    }
    context = {
        "schema": "fl4359.native_sky_context.v1",
        "status": "valid",
        "capture_boundary": "same_frame_paint_completion",
        "source_production_owner": "product_capture_adapter.fl4359_texel_shade",
        "source_context_join": "same_frame_frozen_source_paint_state",
        "source_generation_join": "same_frame_typed_token_from_product_dispatch",
        "render_frame_index": render_frame_index,
        "frame_index": frame_index,
        "sequence_frame": sequence_frame,
        "product_frame_time_seconds": 11.5,
        "frame_clock_seconds": 11.5,
        "celestial_time": 0.25,
        "night_factor": 0.1,
        "night_enabled": False,
        "sun_direction": [0.0, 1.0, 0.0],
        "moon_direction": [0.0, -1.0, 0.0],
        "moon_angular_radius": 0.004,
        "camera_ray_origin": [1.0, 2.0, 3.0],
        "scene_camera_origin": [1.0, 2.0, 3.0],
        "camera_forward": [0.0, 0.0, -1.0],
        "camera_right": [1.0, 0.0, 0.0],
        "camera_up": [0.0, 1.0, 0.0],
        "camera_fov": 1.0,
        "camera_near": 0.1,
        "camera_far": 1000.0,
        "world_to_clip_matrix": [1.0] * 16,
        "cloud_plane_y": 38.0,
        "clouds_enabled": True,
        "cloud_density_enabled": False,
        "cloud_shadow_deep_enabled": False,
        "weather_kind": 0,
        "weather_kind_name": "clear",
        "cloud_time_seconds": 11.5,
        "ambient_floor": 0.25,
        "cloud_opacity": 0.0,
        "product_observer_ray_law":
            "product_observer_cube_direction(face, (texel + 0.5) / probe_size)",
        "product_sky_ray_origin_law": "texel_shade_params.layer_origins[layer]",
        "product_texel_shade_params_by_bank": params,
        "sky_generation_tokens_by_bank": {
            "front": [0x80000000 | generation],
            "underlay": [0x80000000 | generation],
        },
    }
    frozen = {
        key: value for key, value in context.items()
        if key not in {"frame_index", "sequence_frame",
                       "product_texel_shade_params_by_bank",
                       "sky_generation_tokens_by_bank"}
    }
    frozen["source_render_frame_index"] = render_frame_index
    frozen["context_owner"] = "ASCIICompositorEffect.frozen_frame_state"
    retained = {
        bank: {"0": {
            "bank": bank,
            "physical_layer": 0,
            "source_context_snapshot_id": 1,
            "selected_sky_generation_token": 0x80000000 | generation,
            "sky_generation_token": 0x80000000 | generation,
            "generation": generation,
            "selected_product_texel_shade_params": params[bank],
            "frozen_product_inputs": dict(frozen),
        }} for bank in ("front", "underlay")
    }
    context["sky_context_by_bank_layer"] = retained
    context["sky_context_snapshots"] = [{
        "snapshot_id": 1, "schema": "fl4359.native_sky_context.v1", "status": "valid",
        "render_frame_index": render_frame_index, "frame_index": frame_index,
        "sequence_frame": sequence_frame,
        "product_texel_shade_params_by_bank": params,
        "sky_generation_tokens_by_bank": {
            "front": [0x80000000 | generation],
            "underlay": [0x80000000 | generation],
        },
    }]
    return context

def record(address: int, bank: int, token: int = 1, flags: int = 1) -> bytes:
    parts = [
        struct.pack("<4I", address, bank, token, flags),
        struct.pack("<4I", 77, 0, 0, 1),
        struct.pack("<4f", 1.0, 2.0, 3.0, 1.0),
        struct.pack("<4I", 0, 0, 0, token),
        struct.pack("<4f", 1.0, 2.0, 3.0, 1.0),
        struct.pack("<4I", (1 << 20) | (1 << 12) | (1 << 16), 0, 0, 0),
        struct.pack("<4f", 0.25, 0.5, 0.75, 1.0),
        struct.pack("<4f", 0.0, 1.0, 0.5, 1.0),
        struct.pack("<4I", 1, 2, 3, 4),
    ]
    return b"".join(parts)


def sky_record(address: int, bank: int, token: int) -> bytes:
    raw = bytearray(record(address, bank, token=token, flags=5))
    raw[16:32] = struct.pack("<4I", 0, 0, 0, 0)
    raw[32:48] = struct.pack("<4f", 0.0, 0.0, 0.0, 0.0)
    raw[48:64] = struct.pack("<4I", 0, 0, 0, 0)
    struct.pack_into("<I", raw, 80, (1 << 20) | (6 << 12) | 0x600)
    return bytes(raw)


def metadata(payload: bytes, *, frame_index: int = 8, sequence_frame: int = 3,
             request_id: int = 19, probe_size: int = 384) -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "schema": "fl4359.native_source_content.v1",
        "status": "valid",
        "payload_binding": "post_effect_display_payloads",
        "record_layout": [
            "key", "identity", "world", "source_revisions", "anchor_source",
            "payload", "colour", "normal_depth", "surface_meta",
        ],
        "colour_field": "effective_geometry_bank_colour_vec4",
        "colour_semantics": "actor_uv_coverage_subkind_or_meadow_vegetation_pose_vec4",
        **empty_reflection_metadata(),
        "record_bytes": 144,
        "record_count": len(payload) // 144,
        "bytes": len(payload),
        "raw_sha256": digest,
        "frame_index": frame_index,
        "sequence_frame": sequence_frame,
        "request_id": request_id,
        "probe_size": probe_size,
        "layer_count": 18,
        "render_frame_index": 44,
        "epoch": 5,
        "join": {
            "frame_index": frame_index,
            "sequence_frame": sequence_frame,
            "request_id": request_id,
            "render_frame_index": 44,
            "probe_size": probe_size,
            "layer_count": 18,
            "epoch": 5,
        },
        "sky_context": sky_context(
            render_frame_index=44, frame_index=frame_index,
            sequence_frame=sequence_frame),
    }


class ReaderContract(unittest.TestCase):
    def test_sorted_bank_address_lookup_and_explicit_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "reflection.bin").write_bytes(b"")
            path = Path(temp) / "source.bin"
            # Deliberately reverse physical record order; lookup is sorted by key.
            path.write_bytes(record(9, 1) + record(3, 0))
            with NativeSourceContentReader(path) as reader:
                self.assertEqual(reader.record_count, 2)
                self.assertEqual(reader.source_content(3, 0)["status"], "ok")
                self.assertEqual(reader.source_content(9, 1)["revisions"][3], 1)
                self.assertEqual(reader.source_content(9, 1)["colour"],
                                 (0.25, 0.5, 0.75, 1.0))
                self.assertEqual(reader.source_content(9, 0)["status"], "missing")
                self.assertEqual(reader.source_content(11, 0)["status"], "missing")
                self.assertEqual(reader.source_content(True, 0)["status"], "invalid_reference")
                self.assertEqual(reader.source_content(3, False)["status"], "invalid_reference")

    def test_duplicate_and_alignment_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "reflection.bin").write_bytes(b"")
            duplicate = Path(temp) / "duplicate.bin"
            duplicate.write_bytes(record(3, 0) + record(3, 0))
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(duplicate)
            malformed = Path(temp) / "malformed.bin"
            malformed.write_bytes(b"x")
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(malformed)

    def test_join_rejects_hash_frame_request_sequence_and_probe_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "reflection.bin").write_bytes(b"")
            payload = record(3, 0)
            path = Path(temp) / "source.bin"
            path.write_bytes(payload)
            expected = metadata(payload)
            for key, value in (
                ("raw_sha256", "0" * 64),
                ("frame_index", 9),
                ("request_id", 20),
                ("sequence_frame", 4),
                ("probe_size", 8),
            ):
                candidate = dict(expected)
                candidate[key] = value
                if key in {"frame_index", "request_id", "sequence_frame", "probe_size"}:
                    candidate["join"] = dict(expected["join"])
                with self.assertRaises(NativeSourceContentError):
                    if key == "raw_sha256":
                        NativeSourceContentReader(path, candidate)
                    else:
                        validate_native_source_content_join(
                            candidate, frame_index=8, sequence_frame=3,
                            request_id=19, probe_size=384,
                        )

    def test_colour_abi_and_water_coverage_metadata_are_required(self) -> None:
        payload = record(3, 0)
        expected = metadata(payload)
        for key, value in (
            ("record_layout", ["key"]),
            ("colour_field", "payload_output_colour"),
            ("colour_semantics", "untyped"),
            ("reflection_status", "captured_without_bank_join"),
            ("reflection_plan", "inferred_from_final_rgb"),
        ):
            candidate = dict(expected)
            candidate[key] = value
            with self.assertRaises(NativeSourceContentError):
                validate_native_source_content_join(
                    candidate, frame_index=8, sequence_frame=3,
                    request_id=19, probe_size=384,
                )

    def test_key_and_join_duplicates_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "reflection.bin").write_bytes(b"")
            malformed_flags = bytearray(record(3, 0))
            struct.pack_into("<I", malformed_flags, 12, 0)
            unknown_flags = bytearray(record(3, 0))
            struct.pack_into("<I", unknown_flags, 12, 9)
            for payload in (
                record(0, 0), record(0x10000000, 0), record(3, 2),
                bytes(malformed_flags), bytes(unknown_flags),
            ):
                path = Path(temp) / "bad-key.bin"
                path.write_bytes(payload)
                with self.assertRaises(NativeSourceContentError):
                    NativeSourceContentReader(path)
            payload = record(3, 0)
            candidate = metadata(payload)
            candidate["join"] = dict(candidate["join"])
            candidate["join"]["sequence_frame"] = 99
            with self.assertRaises(NativeSourceContentError):
                validate_native_source_content_join(
                    candidate, frame_index=8, sequence_frame=3,
                    request_id=19, probe_size=384,
                )

    def test_join_requires_same_frame_sky_context(self) -> None:
        payload = record(3, 0)
        expected = metadata(payload)
        for context in (
            {},
            {**sky_context(), "render_frame_index": 45},
            {**sky_context(), "world_to_clip_matrix": [float("nan")] * 16},
        ):
            candidate = dict(expected)
            candidate["sky_context"] = context
            with self.assertRaises(NativeSourceContentError):
                validate_native_source_content_join(
                    candidate, frame_index=8, sequence_frame=3,
                    request_id=19, probe_size=384,
                )

    def test_typed_sky_allows_empty_geometry_and_stale_geometry_token_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "reflection.bin").write_bytes(b"")
            sky = bytearray(record(3, 0, token=0x80000011, flags=5))
            sky[16:32] = struct.pack("<4I", 0, 0, 0, 0)
            sky[32:48] = struct.pack("<4f", 0.0, 0.0, 0.0, 0.0)
            sky[48:64] = struct.pack("<4I", 0, 0, 0, 0)
            struct.pack_into("<I", sky, 80, (1 << 20) | (6 << 12) | 0x600)
            path = Path(temp) / "sky.bin"
            path.write_bytes(sky)
            with NativeSourceContentReader(path) as reader:
                self.assertEqual(reader.source_content(3, 0)["status"], "ok")
            canonical = metadata(bytes(sky))
            canonical["sky_context"] = sky_context(generation=0x11)
            with NativeSourceContentReader(path, canonical) as reader:
                self.assertEqual(reader.source_content(3, 0)["status"], "ok")
            mismatched = metadata(bytes(sky))
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(path, mismatched)
            zero_token = bytearray(sky)
            struct.pack_into("<I", zero_token, 8, 0x80000000)
            path.write_bytes(zero_token)
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(path)
            stale = bytearray(record(3, 0, token=1, flags=1))
            struct.pack_into("<I", stale, 60, 2)
            path.write_bytes(stale)
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(path)

    def test_redundant_product_params_must_match_packed_ubo(self) -> None:
        candidate = metadata(record(3, 0))
        candidate["sky_context"] = sky_context()
        candidate["sky_context"]["product_texel_shade_params_by_bank"][
            "front"
        ]["layer_origins"][0][0] = 9.0
        with self.assertRaises(NativeSourceContentError):
            validate_native_source_content_join(
                candidate, frame_index=8, sequence_frame=3,
                request_id=19, probe_size=384,
            )
        candidate = metadata(record(3, 0))
        candidate["sky_context"] = sky_context()
        candidate["sky_context"]["product_texel_shade_params_by_bank"][
            "underlay"
        ]["capture_range"][1] = 999.0
        with self.assertRaises(NativeSourceContentError):
            validate_native_source_content_join(
                candidate, frame_index=8, sequence_frame=3,
                request_id=19, probe_size=384,
            )


    def test_payload_valid_bit_and_owner_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "reflection.bin").write_bytes(b"")
            for payload_word in (1 << 12, (1 << 20) | (8 << 12)):
                malformed = bytearray(record(3, 0))
                struct.pack_into("<I", malformed, 80, payload_word)
                path = Path(temp) / "bad-payload.bin"
                path.write_bytes(malformed)
                with self.assertRaises(NativeSourceContentError):
                    NativeSourceContentReader(path)

    def test_retained_layer_context_survives_generation_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "reflection.bin").write_bytes(b"")
            token1 = 0x80000001
            token2 = 0x80000002
            payload = sky_record(1, 0, token1) + sky_record(384 * 384 + 1, 0, token2)
            path = Path(temp) / "rotating-sky.bin"
            path.write_bytes(payload)
            context = sky_context(generation=2)
            context["sky_generation_tokens_by_bank"]["front"] = [token1, token2]
            context["sky_context_by_bank_layer"]["front"]["0"] = {
                "bank": "front", "physical_layer": 0,
                "source_context_snapshot_id": 1,
                "selected_sky_generation_token": token1,
                "sky_generation_token": token1, "generation": 1,
                "selected_product_texel_shade_params": _product_params(generation=1),
                "frozen_product_inputs": copy.deepcopy(
                    context["sky_context_by_bank_layer"]["front"]["0"][
                        "frozen_product_inputs"]
                ),
            }
            context["sky_context_by_bank_layer"]["front"]["1"] = {
                "bank": "front", "physical_layer": 1,
                "source_context_snapshot_id": 2,
                "selected_sky_generation_token": token2,
                "sky_generation_token": token2, "generation": 2,
                "selected_product_texel_shade_params": _product_params(generation=2),
                "frozen_product_inputs": copy.deepcopy(
                    context["sky_context_by_bank_layer"]["front"]["0"][
                        "frozen_product_inputs"]
                ),
            }
            joined = metadata(payload)
            joined["sky_context"] = context
            with NativeSourceContentReader(path, joined) as reader:
                self.assertEqual(reader.source_content(1, 0)["sky_context_layer"][
                    "selected_sky_generation_token"], token1)
                self.assertEqual(reader.source_content(384 * 384 + 1, 0)[
                    "sky_context_layer"]["selected_sky_generation_token"], token2)
            stale = copy.deepcopy(joined)
            stale["sky_context"]["sky_context_by_bank_layer"]["front"]["0"] = \
                copy.deepcopy(stale["sky_context"]["sky_context_by_bank_layer"]["front"]["1"])
            stale["sky_context"]["sky_context_by_bank_layer"]["front"]["0"]["physical_layer"] = 0
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(path, stale)
            missing = copy.deepcopy(joined)
            del missing["sky_context"]["sky_context_by_bank_layer"]["front"]["0"]
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(path, missing)

    def test_retained_history_capacity_is_bounded(self) -> None:
        context = sky_context()
        context["sky_context_snapshots"] = [{"snapshot_id": index} for index in range(6)]
        candidate = metadata(record(3, 0))
        candidate["sky_context"] = context
        with self.assertRaises(NativeSourceContentError):
            validate_native_source_content_join(
                candidate, frame_index=8, sequence_frame=3,
                request_id=19, probe_size=384,
            )


if __name__ == "__main__":
    unittest.main()
