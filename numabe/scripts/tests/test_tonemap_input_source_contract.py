from pathlib import Path
import unittest

HERE = Path(__file__).resolve()
# Resolve both the disposable candidate layout (candidate/project/...) and the
# repository layout (repo/godot_project/...). The committed test is placed in
# scripts/tests, while the temporary copy remains runnable beside candidate/tests.
if (HERE.parents[1] / "project/scripts/render").is_dir():
    PROJECT = HERE.parents[1] / "project"
else:
    PROJECT = HERE.parents[2] / "godot_project"
EFFECT = (PROJECT / "render/api/ASCIICompositorEffect.gd").read_text()
OBSERVER = (PROJECT / "render/diagnostics/frame/NativeFrameObserver.gd").read_text()
READER = (PROJECT / "render/diagnostics/provenance/CanvasProvenanceReader.gd").read_text()
OWNER = (PROJECT / "render/diagnostics/capture/ToneMapInputCapture.gd").read_text()
SHADER = (PROJECT / "render/post/tonemap/tonemap_input_snapshot.glsl").read_text()
LIFECYCLE_FIXTURE = (PROJECT / "scripts/tests/test_fl4691_tonemap_input_capture_lifecycle.gd").read_text()


class ToneMapInputSourceContractTests(unittest.TestCase):
    def test_owner_has_five_gpu_one_cpu_one_transfer_peak(self):
        self.assertIn("SLOT_COUNT := 5", OWNER)
        self.assertIn("required := frame_bytes * (SLOT_COUNT + 1 + 1)", OWNER)
        self.assertIn('"gpu_slots": SLOT_COUNT', OWNER)
        self.assertIn('"cpu_readback_slots": 1', OWNER)
        self.assertIn('"transfer_envelope_slots": 1', OWNER)
        self.assertIn('"required_peak_formula": "frame_bytes*(5 GPU + 1 CPU + 1 transfer)"', OWNER)
        self.assertIn("_requests.size() >= SLOT_COUNT", OWNER)
        self.assertNotIn("texture_get_data_async", OWNER)
        self.assertNotIn("call_deferred", OWNER)
        self.assertNotIn("FileAccess.get_sha256", OWNER)
        self.assertIn("HashingContext.new()", OWNER)
        self.assertIn("release_after_export", OWNER)
        self.assertIn("_postflight", OWNER)

    def test_seal_does_not_read_and_readback_is_one_request(self):
        seal = OWNER.index("func seal_after_flight")
        read = OWNER.index("func read_one_after_flight")
        read_callback = OWNER.index("func _read_one_on_render_thread")
        retire = OWNER.index("func _retire_one_after_export")
        self.assertNotIn("texture_get_data", OWNER[seal:read])
        self.assertIn("texture_get_data", OWNER[read_callback:retire])
        self.assertIn("RenderingServer.call_on_render_thread", OWNER[read:read_callback])
        self.assertIn("waiter.wait()", OWNER[read:read_callback])
        self.assertIn("_retire_one_after_export(request_id)", OWNER)
        self.assertIn("row.erase(\"bytes\")", OWNER)
        self.assertIn("_slots[slot_index] = RID()", OWNER)

    def test_owner_does_not_reuse_or_retire_before_postflight(self):
        self.assertIn("not _requests.is_empty() or _postflight or _release_requested", OWNER)
        self.assertIn("_schedule_retire(true)", OWNER)
        self.assertIn("if not allowed or _rd == null", OWNER)
        self.assertIn('row["status"] = "discarded"', OWNER)
        self.assertNotIn("ring slot is immediately reusable", OWNER)

    def test_effect_arms_snapshot_before_exposing_canvas_capture(self):
        configure = EFFECT.index("func configure_canvas_provenance")
        ready = EFFECT.index("snapshot_ready =", configure)
        enabled = EFFECT.index("_canvas_capture_enabled =", ready)
        self.assertLess(ready, enabled)
        self.assertIn("return _canvas_capture_enabled", EFFECT)
        self.assertIn('"queue", color_image, size', EFFECT)
        self.assertIn("source_snapshot_request_id", EFFECT)
        self.assertIn("source_snapshot_rid", EFFECT)
        self.assertIn("custom_presentation_output", EFFECT)
        self.assertNotIn("rd.submit()", EFFECT)
        self.assertNotIn("rd.sync()", EFFECT)
        self.assertNotIn("call_deferred", EFFECT)

    def test_observer_seals_then_reads_exports_and_retires_serially(self):
        self.assertIn("tonemap_input_capture_budget", OBSERVER)
        self.assertIn("combined_required_bytes", OBSERVER)
        self.assertIn("tonemap_input_capture_seal_after_flight", OBSERVER)
        self.assertIn("read_tonemap_input_snapshot", OBSERVER)
        self.assertIn("_tonemap_input_capture_requested", OBSERVER)
        self.assertIn("export_tonemap_input_snapshot", OBSERVER)
        self.assertIn("release_tonemap_input_snapshots", OBSERVER)
        readback = OBSERVER.index("func _read_gpu_after_flight")
        self.assertLess(
            OBSERVER.index("tonemap_input_capture_seal_after_flight", readback),
            OBSERVER.index("_release_gpu()", readback),
        )
        export = OBSERVER.index("func export_pngs")
        read = OBSERVER.index("read_tonemap_input_snapshot", export)
        write = OBSERVER.index("export_tonemap_input_snapshot", export)
        self.assertLess(read, write)
        self.assertLess(write, OBSERVER.rindex("release_tonemap_input_snapshots"))
        self.assertIn("source_snapshot_request_id", OBSERVER)
        self.assertIn(".tonemap_input_rgba16f.bin", OBSERVER)

    def test_failure_paths_discard_unexported_requests(self):
        owner_discard = OWNER.index("func discard_unexported_after_postflight")
        self.assertIn('row_status in ["queued", "ready", "written"]', OWNER[owner_discard:])
        self.assertIn("_retire_one_after_export(request_id, true)", OWNER[owner_discard:])
        observer_export = OBSERVER[OBSERVER.index("func export_pngs"):]
        self.assertIn("discard_unexported_tonemap_input_snapshots", observer_export)
        self.assertIn("png_status", observer_export)
        self.assertNotIn("native_png_write_failed\n\t\t\tcontinue", observer_export)
        self.assertIn('input_readback.erase("bytes")', observer_export)

    def test_observer_drops_readback_dictionary_before_next_request(self):
        export = OBSERVER[OBSERVER.index("func export_pngs"):]
        read = export.index("read_tonemap_input_snapshot")
        drop = export.index('input_readback.erase("bytes")')
        next_effect_use = export.index("export_tonemap_input_snapshot", drop)
        self.assertLess(read, drop)
        self.assertLess(drop, next_effect_use)
        self.assertIn("discard_unexported_after_postflight", OWNER)

    def test_gpu_fixture_uses_production_global_rd_lifecycle(self):
        self.assertIn('preload("res://render/diagnostics/capture/ToneMapInputCapture.gd")', LIFECYCLE_FIXTURE)
        self.assertIn("RenderingServer.get_rendering_device()", LIFECYCLE_FIXTURE)
        self.assertIn("RenderingServer.call_on_render_thread", LIFECYCLE_FIXTURE)
        self.assertIn("func _process", LIFECYCLE_FIXTURE)
        self.assertIn("MAX_SECONDS := 20", LIFECYCLE_FIXTURE)
        self.assertIn('"discard_unexported_after_postflight"', LIFECYCLE_FIXTURE)
        self.assertIn('"release_after_export"', LIFECYCLE_FIXTURE)
        self.assertIn("second_configure_after_retirement", LIFECYCLE_FIXTURE)
        self.assertNotIn("create_local_rendering_device", LIFECYCLE_FIXTURE)
        self.assertNotIn("_rd.submit()", LIFECYCLE_FIXTURE)
        self.assertNotIn("_rd.sync()", LIFECYCLE_FIXTURE)
        self.assertNotIn("call_deferred", LIFECYCLE_FIXTURE)

    def test_shader_is_integer_texel_copy_to_rgba16f(self):
        for marker in (
            "sampler2D source_texture",
            "image2D snapshot_image",
            "texelFetch(source_texture, p, 0)",
            "imageStore(snapshot_image, p",
        ):
            self.assertIn(marker, SHADER)

    def test_reader_requires_alias_push_and_effect_context(self):
        for marker in (
            "validate_tonemap_input_snapshot",
            "source_texture_aliases",
            "tonemap_actual_push_record_missing_or_mismatched",
            "tonemap_active_operator_unsupported_%s",
            "tonemap_input_snapshot_payload_mismatch",
            "tonemap_input_snapshot_sidecar_hash_mismatch",
        ):
            self.assertIn(marker, READER)
        self.assertNotIn("use_color_correction", READER)


if __name__ == "__main__":
    unittest.main()
