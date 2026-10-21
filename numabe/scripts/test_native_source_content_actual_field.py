from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from fl4359_actual_field import ActualFieldError, ActualFieldReader
from test_native_source_content_reader import empty_reflection_metadata


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

def source_record() -> bytes:
    return b"".join([
        struct.pack("<4I", 1, 0, 1, 1),
        struct.pack("<4I", 77, 0, 0, 1),
        struct.pack("<4f", 1.0, 2.0, 3.0, 1.0),
        struct.pack("<4I", 0, 0, 0, 1),
        struct.pack("<4f", 1.0, 2.0, 3.0, 1.0),
        struct.pack("<4I", (1 << 20) | (1 << 12) | (1 << 16), 0, 0, 0),
        struct.pack("<4f", 0.25, 0.5, 0.75, 1.0),
        struct.pack("<4f", 0.0, 1.0, 0.5, 1.0),
        struct.pack("<4I", 1, 2, 3, 4),
    ])


class ActualFieldNativeJoin(unittest.TestCase):
    def _trace(self, root: Path, native: bytes) -> dict:
        field_bytes = struct.pack("<12f", *([0.0] * 12)) + struct.pack(
            "<12I", 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0
        )
        field_path = root / "field.bin"
        field_path.write_bytes(field_bytes)
        native_path = root / "native.bin"
        native_path.write_bytes(native)
        native_meta = {
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
            "requested": True,
            "raw_path": native_path.name,
            "raw_sha256": hashlib.sha256(native).hexdigest(),
            "bytes": len(native), "record_bytes": 144, "record_count": 1,
            "frame_index": 8, "sequence_frame": 3, "request_id": 19,
            "probe_size": 384, "layer_count": 18,
            "render_frame_index": 44, "epoch": 5,
            "join": {
                "frame_index": 8, "sequence_frame": 3, "request_id": 19,
                "render_frame_index": 44, "probe_size": 384,
                "layer_count": 18, "epoch": 5,
            },
            "sky_context": sky_context(),
        }
        field = {
            "schema": "fl4359.final_paint_field.v2", "status": "valid",
            "raw_path": field_path.name,
            "raw_sha256": hashlib.sha256(field_bytes).hexdigest(),
            "byte_order": "little_endian", "scalar_format": "float32_uint32",
            "bytes": len(field_bytes), "record_bytes": 96, "record_count": 1,
            "actual_image_size": [1, 1], "logical_target_size": [1, 1],
            "presentation_size": [1, 1], "cell_size": [1, 1],
            "semantic_ansi_size": [1, 1],
            "record_layout": [
                "fg_rgb", "native_intensity", "bg_rgb", "effective_coverage",
                "final_rgb", "actor_alpha", "front_sources", "under_sources",
                "composition",
            ],
            "capture_id": "capture.jsonl#pid=42", "frame_index": 8,
            "sequence_frame": 3, "request_id": 19,
            "projection_kind": 1, "projection_mode": 2,
            "camera_projection_mode": 0,
            "capture_boundary": "pre_imageStore_after_compositor_actor_blend",
            "post_imageStore_display_observed": False,
            "late_overlay_observed": False,
            "native_source_content_requested": True,
            "source_capture_state": {
                "probe_size": 384, "layer_count": 18,
                "layer_content_epochs": [17] * 18,
                "layer_content_epoch_generations": [1] * 18,
            },
            "provenance": {
                "paint_owner": "ascii_compositor_same_dispatch",
                "cell_source_join": "fl4431_final_cell_capture_same_frame_request",
                "source": "unavailable", "atlas": "unavailable",
            },
            "applied_policy": {"status": "unavailable"},
        }
        return {
            "frame_index": 8, "sequence_frame": 3, "request_id": 19,
            "recorder_identity": {"capture_path": "capture.jsonl", "pid": 42},
            "final_paint_field": field, "native_source_content": native_meta,
        }

    def test_frame_retains_mmap_native_join_and_no_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reflection.bin").write_bytes(b"")
            reader = ActualFieldReader(root / "capture.jsonl")
            frame = reader.read(self._trace(root, source_record()), frame_id=3,
                                sequence_index=0)
            self.assertIsNotNone(frame.native_source_reader)
            self.assertEqual(frame.source_content(1, 0)["status"], "ok")
            self.assertEqual(frame.source_content(1, 0)["key"], (1, 0, 1, 1))
            self.assertEqual(frame.source_content(1, 0)["revisions"][3], 1)
            self.assertEqual(frame.source_content(1, 0)["colour"],
                             (0.25, 0.5, 0.75, 1.0))
            self.assertNotIn("sky_context", frame.source_content(1, 0))
            self.assertIsNone(frame.source_content(1, 0)["sky_context_layer"])
            self.assertEqual(frame.sky_source_context()["status"], "valid")
            self.assertEqual(frame.source_content(1, 1)["status"], "missing")
            reader.close()

    def test_canonical_join_mismatch_fails_before_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reflection.bin").write_bytes(b"")
            trace = self._trace(root, source_record())
            trace["native_source_content"]["request_id"] = 20
            with self.assertRaises(ActualFieldError):
                ActualFieldReader(root / "capture.jsonl").read(
                    trace, frame_id=3, sequence_index=0
                )


if __name__ == "__main__":
    unittest.main()
