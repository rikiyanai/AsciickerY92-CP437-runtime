from __future__ import annotations

import re
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import native_source_content_reader as native_reader_module
from native_source_content_reader import NativeSourceContentReader


_CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
_EFFECT = (
    _CANDIDATE_ROOT
    / "godot_project/render/api/ASCIICompositorEffect.gd"
).read_text(encoding="utf-8")
_DRIVER = (
    _CANDIDATE_ROOT
    / "scripts/adhoc/fl4359_external_movement_driver.gd"
).read_text(encoding="utf-8")


def _effect_function(name: str, next_name: str) -> str:
    return _EFFECT[
        _EFFECT.index(f"func {name}") : _EFFECT.index(f"func {next_name}")
    ]


def _geometry_record(address: int = 1, bank: int = 0) -> bytes:
    """One valid 144-byte non-sky record for the mmap lifecycle checks."""
    return b"".join(
        [
            struct.pack("<4I", address, bank, 1, 1),
            struct.pack("<4I", 77, 0, 0, 1),
            struct.pack("<4f", 1.0, 2.0, 3.0, 1.0),
            struct.pack("<4I", 0, 0, 0, 1),
            struct.pack("<4f", 1.0, 2.0, 3.0, 1.0),
            struct.pack("<4I", (1 << 20) | (1 << 12), 0, 0, 0),
            struct.pack("<4f", 0.25, 0.5, 0.75, 1.0),
            struct.pack("<4f", 0.0, 1.0, 0.5, 1.0),
            struct.pack("<4I", 1, 2, 3, 4),
        ]
    )


class NativeSourceIntegrationContract(unittest.TestCase):
    def test_semantic_sparse_count_and_native_image_count_are_distinct(self) -> None:
        """The two pools must receive their own dimensions at the setup seam."""
        observer = re.search(
            r"_prepare_final_pixel_paint_observer\(\s*"
            r"final_pixel_image_size,\s*"
            r"pixel_ctx\.semantic_ansi_size\.x\s*\*\s*"
            r"pixel_ctx\.semantic_ansi_size\.y\s*\*\s*4\s*,",
            _EFFECT,
            re.DOTALL,
        )
        self.assertIsNotNone(observer)
        native = re.search(
            r"reason\s*=\s*_prepare_native_source_content_capture\(\s*"
            r"actual_image_size\.x\s*\*\s*actual_image_size\.y\s*\)",
            _EFFECT,
            re.DOTALL,
        )
        self.assertIsNotNone(native)
        sparse_call = _EFFECT.index(
            "reason = _prepare_fl4359_deferred_sparse_slots(record_count)"
        )
        native_call = _EFFECT.index(
            "reason = _prepare_native_source_content_capture("
        )
        self.assertLess(sparse_call, native_call)

        semantic_records = 225 * 71 * 4
        native_pixels = 3600 * 2252
        self.assertEqual(semantic_records, 63_900)
        self.assertEqual(native_pixels, 8_107_200)
        self.assertGreater(native_pixels, semantic_records)

        sparse_record_bytes = 604 * 4
        sparse_capacity = 160 * 1024 * 1024
        sparse_bytes = 16 + semantic_records * sparse_record_bytes
        self.assertEqual(sparse_bytes, 154_382_416)
        self.assertLessEqual(sparse_bytes, sparse_capacity)
        self.assertGreater(16 + native_pixels * sparse_record_bytes, sparse_capacity)

    def test_continuations_do_not_write_second_native_sidecar(self) -> None:
        """Native sidecar publication belongs to the first completed chunk only."""
        batch = _DRIVER[
            _DRIVER.index("func _write_sparse_capture_batch") : _DRIVER.index(
                "func _arm_moving_dump_before_active_route"
            )
        ]
        continuation = batch.index(
            "if chunked_result and chunk_index > 0:"
        )
        sidecar = batch.index("_write_native_source_content_sidecar(")
        self.assertLess(continuation, sidecar)
        self.assertEqual(batch.count("_write_native_source_content_sidecar("), 1)
        self.assertIn(
            '"native_source_content/frame_%05d_request_%08d.bin"',
            _DRIVER,
        )
        self.assertIn(
            '_native_source_content_sidecars.append(native_source_content)',
            _DRIVER,
        )
        sidecar = _DRIVER[
            _DRIVER.index("func _write_native_source_content_sidecar") : _DRIVER.index(
                "func _final_paint_field_capture_ok"
            )
        ]
        self.assertIn('"probe_size": common["probe_size"]', sidecar)
        self.assertIn('"layer_count": common["layer_count"]', sidecar)

    def test_native_payload_has_one_chunk_owner(self) -> None:
        take = _effect_function(
            "_take_native_source_content_for_request",
            "_queue_fl4359_sparse_capture_readback",
        )
        self.assertIn("var native_receipt := result.duplicate(false)", take)
        self.assertIn('native_receipt.erase("bytes")', take)
        materialize = _effect_function(
            "materialize_fl4359_sparse_capture",
            "take_fl4359_sparse_capture_ready",
        )
        payload_drop = materialize.index(
            'result.erase("native_source_content_bytes")'
        )
        continuation_branch = materialize.index(
            "if not chunk_final:"
        )
        self.assertLess(payload_drop, continuation_branch)
        self.assertIn(
            'request.erase("native_source_content_bytes")',
            materialize[continuation_branch:],
        )

    def test_reader_closes_file_when_mmap_construction_fails(self) -> None:
        class TrackedFile:
            def __init__(self) -> None:
                self.closed = False

            def fileno(self) -> int:
                return 42

            def close(self) -> None:
                self.closed = True

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.bin"
            path.write_bytes(_geometry_record())
            tracked = TrackedFile()
            with patch.object(Path, "open", return_value=tracked), patch.object(
                native_reader_module.mmap,
                "mmap",
                side_effect=OSError("synthetic mmap failure"),
            ):
                with self.assertRaises(OSError):
                    NativeSourceContentReader(path)
            self.assertTrue(tracked.closed)

    def test_reader_context_close_is_idempotent_and_preserves_missing_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.bin"
            path.write_bytes(_geometry_record(3, 0) + _geometry_record(9, 1))
            with NativeSourceContentReader(path) as reader:
                self.assertEqual(reader.source_content(3, 0)["status"], "ok")
                self.assertEqual(reader.source_content(9, 0)["status"], "missing")
            self.assertIsNone(reader._mmap)
            self.assertIsNone(reader._file)
            reader.close()
            self.assertIsNone(reader._mmap)
            self.assertIsNone(reader._file)


if __name__ == "__main__":
    unittest.main()
