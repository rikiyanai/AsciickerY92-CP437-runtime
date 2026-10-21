#!/usr/bin/env python3
"""CPU contract for the bounded selected-water reflection sidecar."""

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from native_source_content_reader import (  # noqa: E402
    NativeSourceContentError,
    NativeSourceContentReader,
    validate_native_source_content_join,
)


def _water_record(address: int = 7, bank: int = 1, token: int = 1) -> bytes:
    return b"".join(
        [
            struct.pack("<4I", address, bank, token, 1),
            struct.pack("<4I", 77, 0, 0, 1),
            struct.pack("<4f", 1.0, 2.0, 3.0, 1.0),
            struct.pack("<4I", 0, 0, 0, token),
            struct.pack("<4f", 1.0, 2.0, 3.0, 1.0),
            struct.pack("<4I", (1 << 20) | (5 << 12) | (2 << 16), 0, 0, 0),
            struct.pack("<4f", 0.25, 0.5, 0.75, 1.0),
            struct.pack("<4f", 0.0, 1.0, 0.5, 1.0),
            struct.pack("<4I", 1, 2, 3, 4),
        ]
    )


def _reflection_record(
    address: int = 7,
    bank: int = 1,
    token: int = 1,
    identity: tuple[int, int, int, int] = (3, 9, 11, 13),
) -> bytes:
    return b"".join(
        [
            # bit 0: native source valid; bit 3: b106-b108 facts valid.
            struct.pack("<4I", address, bank, token, 1 | 8),
            struct.pack("<4I", *identity),
            struct.pack("<4f", 0.0, 1.0, 0.0, 1.0),
            struct.pack("<4I", 5, 0x12345678, 7, 9),
        ]
    )


def _sky_context() -> dict:
    params = {
        "params_hex": (bytes(18 * 16 + 32 + 80)).hex(),
        "probe_size": 384,
        "layer_count": 18,
        "generation": 1,
        "sky_generation_token": 0x80000001,
        "layer_origins": [[0.0, 0.0, 0.0]] * 18,
        "layer_codes": [0] * 18,
        "layer_faces": list(range(6)) * 3,
        "capture_range": [0.0, 0.0, 0.0],
    }
    raw = bytearray(18 * 16 + 32 + 80)
    for layer in range(18):
        struct.pack_into("<4f", raw, layer * 16, 0.0, 0.0, 0.0, 0.0)
    struct.pack_into("<3I", raw, 18 * 16, 384, 18, 1)
    struct.pack_into("<3f", raw, 18 * 16 + 16, 0.0, 0.0, 0.0)
    params["params_hex"] = bytes(raw).hex()
    context = {
        "schema": "fl4359.native_sky_context.v1",
        "status": "valid",
        "capture_boundary": "same_frame_paint_completion",
        "source_production_owner": "product_capture_adapter.fl4359_texel_shade",
        "source_context_join": "same_frame_frozen_source_paint_state",
        "source_generation_join": "same_frame_typed_token_from_product_dispatch",
        "render_frame_index": 44,
        "frame_index": 8,
        "sequence_frame": 3,
        "product_frame_time_seconds": 1.0,
        "frame_clock_seconds": 1.0,
        "celestial_time": 0.2,
        "night_factor": 0.1,
        "night_enabled": False,
        "sun_direction": [0.0, 1.0, 0.0],
        "moon_direction": [0.0, -1.0, 0.0],
        "moon_angular_radius": 0.004,
        "camera_ray_origin": [0.0, 0.0, 0.0],
        "scene_camera_origin": [0.0, 0.0, 0.0],
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
        "cloud_time_seconds": 1.0,
        "ambient_floor": 0.25,
        "cloud_opacity": 0.0,
        "product_observer_ray_law": "product_observer_cube_direction(face, (texel + 0.5) / probe_size)",
        "product_sky_ray_origin_law": "texel_shade_params.layer_origins[layer]",
        "product_texel_shade_params_by_bank": {"front": params, "underlay": params},
        "sky_generation_tokens_by_bank": {
            "front": [0x80000001], "underlay": [0x80000001],
        },
    }
    frozen = dict(context)
    frozen.pop("frame_index")
    frozen.pop("sequence_frame")
    frozen.pop("product_texel_shade_params_by_bank")
    frozen.pop("sky_generation_tokens_by_bank")
    frozen["source_render_frame_index"] = 44
    frozen["context_owner"] = "ASCIICompositorEffect.frozen_frame_state"
    retained = {}
    for bank in ("front", "underlay"):
        retained[bank] = {str(layer): {
            "bank": bank,
            "physical_layer": layer,
            "source_context_snapshot_id": 1,
            "selected_sky_generation_token": 0x80000001,
            "sky_generation_token": 0x80000001,
            "generation": 1,
            "selected_product_texel_shade_params": params,
            "frozen_product_inputs": frozen,
        } for layer in range(18)}
    context["sky_context_by_bank_layer"] = retained
    context["sky_context_snapshots"] = [{
        "snapshot_id": 1,
        "schema": "fl4359.native_sky_context.v1",
        "status": "valid",
        "render_frame_index": 44,
        "frame_index": 8,
        "sequence_frame": 3,
        "product_texel_shade_params_by_bank": {
            "front": params, "underlay": params,
        },
        "sky_generation_tokens_by_bank": {
            "front": [0x80000001], "underlay": [0x80000001],
        },
    }]
    return context


def _metadata(native: bytes, reflection: bytes, reflection_path: str) -> dict:
    context = _sky_context()
    shade_params = bytearray(18 * 16 + 32 + 80)
    struct.pack_into("<III", shade_params, 18 * 16, 384, 18, 1)
    shade_params_hex = bytes(shade_params).hex()
    return {
        "schema": "fl4359.native_source_content.v1",
        "status": "valid",
        "payload_binding": "post_effect_display_payloads",
        "record_layout": ["key", "identity", "world", "source_revisions",
                           "anchor_source", "payload", "colour", "normal_depth",
                           "surface_meta"],
        "colour_field": "effective_geometry_bank_colour_vec4",
        "colour_semantics": "actor_uv_coverage_subkind_or_meadow_vegetation_pose_vec4",
        "reflection_status": "captured_selected_water_reflection_facts",
        "reflection_plan": "water_v2_requested_bank_address_selected_reflection_bindings_106_108_identity_normal_depth_surface_meta_after_effective_geometry_election",
        "reflection_facts_schema": "fl4359.native_reflection_facts.v1",
        "reflection_facts_status": "captured_selected_water_reflection_facts",
        "reflection_binding_source": "PerspectiveProbePipeline.probe_reflection_attachment_rids",
        "reflection_address_law": "product_capture_adapter.fl4359_captured_reflected_material(address)",
        "reflection_generation_lifetime": "producer_retained_physical_layer",
        "reflection_record_bytes": 64,
        "reflection_record_count": len(reflection) // 64,
        "reflection_bytes": len(reflection),
        "reflection_path": reflection_path,
        "reflection_sha256": hashlib.sha256(reflection).hexdigest(),
        "reflection_record_layout": ["key", "identity_106", "normal_107", "meta_108"],
        "reflection_capacity_records": 262144,
        "reflection_layer_witness_valid": [True] + [False] * 17,
        "reflection_layer_content_epochs": [1] + [0] * 17,
        "reflection_layer_content_generations": [1] + [0] * 17,
        "reflection_layer_render_frames": [44] + [0] * 17,
        "reflection_layer_shade_witness_valid_front": [True] + [False] * 17,
        "reflection_layer_shade_witness_valid_underlay": [True] + [False] * 17,
        "reflection_layer_shade_submission_status_front": 1,
        "reflection_layer_shade_submission_status_underlay": 1,
        "reflection_layer_shade_generations_front": [1] + [0] * 17,
        "reflection_layer_shade_generations_underlay": [1] + [0] * 17,
        "reflection_layer_shade_render_frames_front": [44] + [0] * 17,
        "reflection_layer_shade_render_frames_underlay": [44] + [0] * 17,
        "reflection_layer_shade_params_hex_front": [shade_params_hex] + [""] * 17,
        "reflection_layer_shade_params_hex_underlay": [shade_params_hex] + [""] * 17,
        "record_bytes": 144,
        "record_count": len(native) // 144,
        "bytes": len(native),
        "raw_sha256": hashlib.sha256(native).hexdigest(),
        "frame_index": 8,
        "sequence_frame": 3,
        "request_id": 19,
        "probe_size": 384,
        "layer_count": 18,
        "render_frame_index": 44,
        "epoch": 5,
        "join": {
            "frame_index": 8, "sequence_frame": 3, "request_id": 19,
            "render_frame_index": 44, "probe_size": 384, "layer_count": 18,
            "epoch": 5,
        },
        "sky_context": context,
    }


def _metadata_v3(native: bytes, reflection: bytes, reflection_path: str) -> dict:
    metadata = _metadata(native, reflection, reflection_path)
    metadata.update({
        "reflection_plan": "water_v3_requested_bank_address_resolves_product_owned_append_only_fact_without_native_attachment_reload",
        "reflection_facts_schema": "fl4359.native_reflection_facts.v3.product_lineage",
        "reflection_binding_source": "PerspectiveProbePipeline.reflection_lineage_receipt",
        "reflection_generation_lifetime": "product_append_only_flight_fact",
        "reflection_record_layout": ["key", "identity_106", "normal_bits_107", "meta_108"],
        "reflection_lineage_epoch": 3,
        "reflection_lineage_capacity": 262144,
    })
    for key in list(metadata):
        if key.startswith("reflection_layer_"):
            metadata.pop(key)
    return metadata


class ReflectionAbiContract(unittest.TestCase):
    def test_production_source_and_host_wire_the_bounded_snapshot(self) -> None:
        shader = (ROOT / "godot_project/render/diagnostics/capture/native_source_capture.glsl").read_text()
        capture = (ROOT / "godot_project/render/diagnostics/capture/NativeSourceCapture.gd").read_text()
        pipeline = (ROOT / "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd").read_text()
        effect = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
        driver = (ROOT / "scripts/adhoc/fl4359_external_movement_driver.gd").read_text()
        product = (ROOT / "godot_project/render/product/adapters/capture/product_capture_adapter.glslinc").read_text()
        for marker in (
            "struct ReflectionFacts",
            "binding=7,std430",
            "ProductReflectionLineage",
            "binding=26,std430",
            "binding=27,std430",
            "binding=28,std430",
            "binding=29,std430",
            "binding=30,std430",
            "fact.key.z == expected_layer_generation",
            "void capture_reflection",
            "payload_owner != 5u",
            "params.frame.w",
        ):
            self.assertIn(marker, shader)
        self.assertNotIn("texelFetch(reflection_identity", shader)
        for marker in (
            "layout(set = 0, binding = 130, std430) buffer Fl4691ReflectionLineageTable",
            "layout(set = 0, binding = 131, std430) buffer Fl4691ReflectionLineageMap",
            "layout(set = 0, binding = 132, std430) readonly buffer Fl4691PairedReflectionLineageMap",
            "fl4691_reflection_lineage_append",
            "fl4691_reflection_lineage_alias_paired",
            "floatBitsToUint(normal)",
        ):
            self.assertIn(marker, product)
        for marker in (
            'REFLECTION_FACTS_SCHEMA := "fl4359.native_reflection_facts.v3.product_lineage"',
            "REFLECTION_RECORD_BYTES := 64",
            "MAX_REFLECTION_RECORDS := 262144",
            "native_source_capture_reflection_lineage_missing",
            '"reflection_plan": "water_v3_requested_bank_address_resolves_product_owned_append_only_fact_without_native_attachment_reload"',
            '"reflection_binding_source": "PerspectiveProbePipeline.reflection_lineage_receipt"',
            '"reflection_record_layout": ["key", "identity_106", "normal_bits_107", "meta_108"]',
            '"reflection_facts_status": "captured_selected_water_reflection_facts"',
        ):
            self.assertIn(marker, capture)
        for marker in (
            "REFLECTION_LINEAGE_FACT_CAPACITY := 262144",
            "func prepare_reflection_lineage",
            "func release_reflection_lineage",
            "func reflection_lineage_receipt",
        ):
            self.assertIn(marker, pipeline)
        for marker in (
            "prepare_reflection_lineage",
            "_native_source_reflection_shade_witness_receipt",
            '"native_source_content_reflection_bytes"',
            '"native_source_content_reflection_record_count"',
        ):
            self.assertIn(marker, effect)
        self.assertIn(".reflection.bin", driver)

    def test_product_lineage_v3_captures_without_legacy_layer_witness(self) -> None:
        native = _water_record()
        reflection = _reflection_record()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native_path = root / "native.bin"
            reflection_path = root / "reflection.bin"
            native_path.write_bytes(native)
            reflection_path.write_bytes(reflection)
            with NativeSourceContentReader(
                native_path,
                _metadata_v3(native, reflection, reflection_path.name),
            ) as reader:
                fact = reader.source_content(7, 1)
        self.assertEqual(fact["status"], "missing_water_shading_inputs")
        reflection_facts = fact["reflection_facts"]
        self.assertEqual(reflection_facts["schema"], "fl4359.native_reflection_facts.v3.product_lineage")
        self.assertEqual(reflection_facts["status"], "captured")
        self.assertEqual(reflection_facts["binding_source"], "PerspectiveProbePipeline.reflection_lineage_receipt")
        self.assertEqual(reflection_facts["source_generation_lifetime"], "product_append_only_flight_fact")
        self.assertEqual(reflection_facts["identity_106"], (3, 9, 11, 13))
        self.assertEqual(reflection_facts["normal_bits_107"], struct.unpack("<4I", struct.pack("<4f", 0.0, 1.0, 0.0, 1.0)))
        self.assertEqual(reflection_facts["per_texel_generation_status"], "verified_by_product_lineage_shader")
        self.assertEqual(reflection_facts["lineage_witness"]["epoch"], 3)

    def test_product_lineage_v3_missing_and_stale_fail_closed(self) -> None:
        native = _water_record()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native_path = root / "native.bin"
            reflection_path = root / "reflection.bin"
            native_path.write_bytes(native)
            reflection_path.write_bytes(b"")
            with NativeSourceContentReader(
                native_path,
                _metadata_v3(native, b"", reflection_path.name),
            ) as reader:
                self.assertEqual(reader.source_content(7, 1)["status"], "missing_reflection_facts")
            stale = _reflection_record(token=2)
            reflection_path.write_bytes(stale)
            with NativeSourceContentReader(
                native_path,
                _metadata_v3(native, stale, reflection_path.name),
            ) as reader:
                with self.assertRaises(NativeSourceContentError):
                    reader.source_content(7, 1)

    def test_captured_water_facts_join_source_token_and_lifetime(self) -> None:
        native = _water_record()
        reflection = _reflection_record()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native_path = root / "native.bin"
            reflection_path = root / "reflection.bin"
            native_path.write_bytes(native)
            reflection_path.write_bytes(reflection)
            with NativeSourceContentReader(
                native_path,
                _metadata(native, reflection, reflection_path.name),
            ) as reader:
                fact = reader.source_content(7, 1)
        self.assertEqual(fact["status"], "missing_water_shading_inputs")
        reflection_facts = fact["reflection_facts"]
        self.assertEqual(reflection_facts["status"], "captured")
        self.assertEqual(reflection_facts["requested_bank"], 1)
        self.assertEqual(reflection_facts["effective_geometry_bank"], 0)
        self.assertEqual(reflection_facts["source_generation_token"], 1)
        self.assertEqual(reflection_facts["identity_106"], (3, 9, 11, 13))
        self.assertEqual(reflection_facts["normal_107"], (0.0, 1.0, 0.0, 1.0))
        self.assertEqual(reflection_facts["meta_108"], (5, 0x12345678, 7, 9))
        self.assertEqual(reflection_facts["layer_witness"]["physical_layer"], 0)
        self.assertEqual(reflection_facts["layer_witness"]["shade_generation"], 1)
        self.assertEqual(len(reflection_facts["layer_witness"]["shade_params_hex"]), 800)
        self.assertEqual(fact["temporal_comparison"]["status"], "refused")
        self.assertEqual(fact["temporal_comparison"]["reason"], "retained_water_shading_inputs_not_captured")

    def test_same_address_front_and_underlay_remain_distinct(self) -> None:
        # Geometry may pair-reuse the same physical texel, but native source
        # ownership is still the requested bank. The sidecar must preserve
        # both water facts rather than collapsing front into underlay.
        native = _water_record(bank=0) + _water_record(bank=1)
        reflection = (
            _reflection_record(bank=0, identity=(21, 0, 0, 1))
            + _reflection_record(bank=1, identity=(31, 0, 0, 1))
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native_path = root / "native.bin"
            reflection_path = root / "reflection.bin"
            native_path.write_bytes(native)
            reflection_path.write_bytes(reflection)
            with NativeSourceContentReader(
                native_path, _metadata(native, reflection, reflection_path.name)
            ) as reader:
                front = reader.source_content(7, 0)
                underlay = reader.source_content(7, 1)
        self.assertEqual(front["status"], "missing_water_shading_inputs")
        self.assertEqual(underlay["status"], "missing_water_shading_inputs")
        self.assertEqual(front["reflection_facts"]["requested_bank"], 0)
        self.assertEqual(underlay["reflection_facts"]["requested_bank"], 1)
        self.assertEqual(front["reflection_facts"]["identity_106"], (21, 0, 0, 1))
        self.assertEqual(underlay["reflection_facts"]["identity_106"], (31, 0, 0, 1))

    def test_missing_or_stale_reflection_facts_fail_closed(self) -> None:
        native = _water_record()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native_path = root / "native.bin"
            reflection_path = root / "reflection.bin"
            native_path.write_bytes(native)
            reflection_path.write_bytes(b"")
            empty = _metadata(native, b"", reflection_path.name)
            empty["reflection_layer_witness_valid"] = [False] * 18
            empty["reflection_layer_shade_witness_valid_underlay"] = [False] * 18
            empty["reflection_layer_shade_submission_status_underlay"] = 3
            with NativeSourceContentReader(native_path, empty) as reader:
                self.assertEqual(reader.source_content(7, 1)["status"], "missing_reflection_facts")
            stale = _reflection_record(token=2)
            reflection_path.write_bytes(stale)
            stale_metadata = _metadata(native, stale, reflection_path.name)
            with NativeSourceContentReader(native_path, stale_metadata) as reader:
                with self.assertRaises(NativeSourceContentError):
                    reader.source_content(7, 1)


    def test_failed_or_newer_product_shade_refuses_reflection_lifetime(self) -> None:
        native = _water_record()
        reflection = _reflection_record()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native_path = root / "native.bin"
            reflection_path = root / "reflection.bin"
            native_path.write_bytes(native)
            reflection_path.write_bytes(reflection)
            failed = _metadata(native, reflection, reflection_path.name)
            failed["reflection_layer_shade_witness_valid_underlay"] = [False] * 18
            failed["reflection_layer_shade_submission_status_underlay"] = 3
            with NativeSourceContentReader(native_path, failed) as reader:
                self.assertEqual(reader.source_content(7, 1)["status"], "missing_reflection_facts")
            newer = _metadata(native, reflection, reflection_path.name)
            # The reflection attachment advanced without its retained Product
            # shade packet. Equal host/layer epochs are insufficient; this must
            # refuse the old shade join.
            newer["reflection_layer_content_generations"] = [2] + [0] * 17
            with NativeSourceContentReader(native_path, newer) as reader:
                with self.assertRaises(NativeSourceContentError):
                    reader.source_content(7, 1)

            # A valid recompute can keep source token 1 while Product repaints
            # from a newer immutable source/material/light packet (generation 2).
            # The reflection attachment and shade packet agree with each other,
            # so reflection facts remain valid while whole-water causality is refused.
            recompute = _metadata(native, reflection, reflection_path.name)
            recompute["reflection_layer_content_generations"] = [2] + [0] * 17
            recompute["reflection_layer_shade_generations_underlay"] = [2] + [0] * 17
            recompute_params = bytearray(18 * 16 + 32 + 80)
            struct.pack_into("<III", recompute_params, 18 * 16, 384, 18, 2)
            recompute["reflection_layer_shade_params_hex_underlay"] = [bytes(recompute_params).hex()] + [""] * 17
            with NativeSourceContentReader(native_path, recompute) as reader:
                fact = reader.source_content(7, 1)
            self.assertEqual(fact["status"], "missing_water_shading_inputs")
            self.assertEqual(fact["reflection_facts"]["source_generation_token"], 1)
            self.assertEqual(fact["reflection_facts"]["layer_witness"]["content_generation"], 2)
            self.assertEqual(fact["reflection_facts"]["layer_witness"]["shade_generation"], 2)

            contradictory = _metadata(native, reflection, reflection_path.name)
            contradictory["reflection_layer_shade_params_hex_underlay"] = [bytes(400).hex()] + [""] * 17
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(native_path, contradictory)

    def test_sidecar_join_hash_key_and_duplicate_guards(self) -> None:
        native = _water_record()
        reflection = _reflection_record()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            native_path = root / "native.bin"
            reflection_path = root / "reflection.bin"
            native_path.write_bytes(native)
            reflection_path.write_bytes(reflection)
            metadata = _metadata(native, reflection, reflection_path.name)
            for field, value in (
                ("frame_index", 9),
                ("sequence_frame", 4),
                ("request_id", 20),
                ("probe_size", 128),
            ):
                joined = dict(metadata)
                joined[field] = value
                with self.assertRaises(NativeSourceContentError):
                    validate_native_source_content_join(
                        joined, frame_index=8, sequence_frame=3,
                        request_id=19, probe_size=384,
                    )
            bad_hash = dict(metadata)
            bad_hash["reflection_sha256"] = "0" * 64
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(native_path, bad_hash)
            invalid_key = struct.pack("<4I", 0, 1, 1, 1 | 8) + reflection[16:]
            reflection_path.write_bytes(invalid_key)
            invalid_metadata = _metadata(native, invalid_key, reflection_path.name)
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(native_path, invalid_metadata)
            duplicate = reflection + reflection
            reflection_path.write_bytes(duplicate)
            duplicate_metadata = _metadata(native, duplicate, reflection_path.name)
            with self.assertRaises(NativeSourceContentError):
                NativeSourceContentReader(native_path, duplicate_metadata)


if __name__ == "__main__":
    unittest.main()
