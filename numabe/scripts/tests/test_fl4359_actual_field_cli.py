"""CLI integration checks for the native final-paint GIF consumer.

These fixtures use the producer's real v16 transport shape and one 48-byte
little-endian float sidecar per frame.  They exercise the Python consumer only;
no Godot process, headed driver, or proof capture is launched here.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
RASTER = ROOT / "scripts" / "fl4359_raster_jitter_capture.py"
FIELDS = (
    "row_type",
    "frame_id",
    "screen_cell",
    "subcell_slot",
    "final_paint_glyph",
    "final_paint_mask_glyphs",
    "final_paint_mask_state",
    "final_paint_atlas_mask_dimensions",
    "final_paint_expected_atlas_mask_hash",
    "final_paint_actual_mask_hash",
    "fg_rgb",
    "bg_rgb",
    "final_ink_fg",
    "final_product_glyph",
    "final_paint_slot_colour_present",
    "final_paint_slot_fg_rgb",
    "final_paint_slot_bg_rgb",
)
RECORD_LAYOUT = [
    "fg_rgb", "native_intensity", "bg_rgb", "effective_coverage",
    "final_rgb", "actor_alpha",
]


def test_production_final_field_records_the_exact_post_debug_compositor_color() -> None:
    """Structural guard only; runtime color behavior remains GPU-owned."""
    shader = (ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl").read_text()
    # The final field (v2, the only format since FL-4907 removed compact v3)
    # records the post-debug colour and the debug view bits before the store.
    debug_bits = shader.index("(fl4359_debug_view << 10u)")
    final_record = shader.index("record_final_pixel_paint(", debug_bits)
    image_store = shader.index("imageStore(color_image, pos, vec4(color, 1.0));", final_record)
    assert debug_bits < final_record < image_store
    assert "effective_coverage, color," in shader[final_record:image_store]

    host = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
    assert '"pre_imageStore_after_compositor_debug_stage"' in host
    assert '"final_pixel_field_debug_view": get_fl4359_debug_view()' in host


def _field_records(frame_index: int) -> np.ndarray:
    """Make a nonuniform field, including independent FG and BG variation."""
    width = height = 32
    records = np.zeros((height, width, 12), dtype="<f4")
    for y in range(height):
        for x in range(width):
            fg = np.array([
                0.15 + x / 256.0,
                0.20 + y / 256.0,
                0.10 + ((x + y + frame_index) % 16) / 128.0,
            ], dtype=np.float32)
            bg = np.array([
                0.60 + y / 512.0,
                0.30 + x / 512.0,
                0.12 + ((x * 3 + y + frame_index) % 20) / 256.0,
            ], dtype=np.float32)
            native = 0.10 + ((x + frame_index) % 8) / 16.0
            effective = 0.20 + ((x + y + frame_index) % 5) / 10.0
            final = bg * (1.0 - effective) + fg * effective
            records[y, x, 0:3] = fg
            records[y, x, 3] = native
            records[y, x, 4:7] = bg
            records[y, x, 7] = effective
            records[y, x, 8:11] = final
            records[y, x, 11] = 0.0
    return records


def _field_metadata(path: Path, frame_index: int) -> dict:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    return {
        "schema": "fl4359.final_paint_field.v1",
        "status": "valid",
        "raw_path": str(path.relative_to(path.parents[1])),
        "raw_sha256": digest,
        "bytes": len(raw),
        "record_bytes": 48,
        "record_count": 32 * 32,
        "actual_image_size": [32, 32],
        "logical_target_size": [32, 32],
        "presentation_size": [32, 32],
        "cell_size": [32, 32],
        "semantic_ansi_size": [1, 1],
        "frame_index": 100 + frame_index,
        "sequence_frame": frame_index,
        "request_id": 9001,
        "capture_id": "cli-final-paint-capture",
        "projection_kind": 1,
        "projection_mode": 2,
        "camera_projection_mode": 0,
        "byte_order": "little_endian",
        "scalar_format": "float32",
        "record_layout": RECORD_LAYOUT,
        "capture_boundary": "pre_imageStore_after_compositor_actor_blend",
        "post_imageStore_display_observed": False,
        "late_overlay_observed": True,
        "applied_policy": "unavailable",
        "provenance": {
            "paint_owner": "ascii_compositor_same_dispatch",
            "cell_source_join": "fl4431_final_cell_capture_same_frame_request",
            "source": "unavailable",
            "atlas": "unavailable",
        },
    }


def _capture(tmp_path: Path, *, not_requested: bool = False) -> Path:
    fields_dir = tmp_path / "fields"
    fields_dir.mkdir()
    lines: list[dict | list] = [{
        "row_type": "fl4359_jitter_schema",
        "transport_schema": "fl4359_jitter_transport.v16",
        "fields": list(FIELDS),
    }]
    digest = "a" * 64
    for frame_index in range(5):
        sidecar = fields_dir / f"field-{frame_index}.bin"
        _field_records(frame_index).tofile(sidecar)
        trace_field = _field_metadata(sidecar, frame_index)
        if not_requested:
            trace_field = {
                "schema": "fl4359.final_paint_field.v1",
                "status": "not_requested",
                "reason": "legacy capture did not arm field readback",
            }
        lines.append({
            "row_type": "fl4359_jitter_frame",
            "frame_id": frame_index,
            "frame_trace": {
                "frame_index": 100 + frame_index,
                "sequence_frame": frame_index,
                "request_id": 9001,
                "semantic_ansi_size": [1, 1],
                "cell_size": [32, 32],
                "viewport": {"cell_size": [32, 32]},
                "pose": {
                    "camera_origin": [float(frame_index), 0.0, 0.0],
                    "camera_yaw": 0.0,
                    "camera_pitch": 0.0,
                },
                "camera_world": {
                    "x": float(frame_index), "y": 0.0, "z": 0.0,
                },
                "final_paint_field": trace_field,
            },
        })
        # The rasterizer consumes positional rows by the schema names above.
        lines.append([
            "fl4359_jitter_cell", frame_index, {"x": 0, "y": 0}, 0,
            65, [65], "complete", [32, 32], digest, digest,
            [128, 128, 128], [64, 96, 64], 512,
            65, True, [128, 128, 128], [64, 96, 64],
        ])
    capture = tmp_path / "jitter_capture.jsonl"
    with capture.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
    return capture


def _run(capture: Path, output: Path, *, required: bool = True) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable, str(RASTER),
        "--capture", str(capture),
        "--out", str(output),
        "--cell-px", "32",
        "--frames", "5",
        "--arm-label", "cli-test",
    ]
    if required:
        command.append("--actual-field-required")
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def test_actual_field_cli_writes_all_six_roles_and_rejects_stale_native(
    tmp_path: Path,
) -> None:
    capture = _capture(tmp_path)
    arm_root = tmp_path / "arm"
    output = arm_root / "RASTER"
    arm_root.mkdir()
    # A sibling from an earlier run must not be attached to this capture merely
    # because its conventional filename exists. No native receipt is written.
    Image.new("RGB", (8, 8), (1, 2, 3)).save(
        arm_root / "sweep_5frames.gif", save_all=True, loop=0)

    result = _run(capture, output)
    assert result.returncode == 0, result.stderr

    expected_roles = (
        "jitter_capture_5frames.gif",
        "bg_only/bg_only_5frames.gif",
        "fg_only/fg_only_5frames.gif",
        "fg_highlight/fg_highlight_5frames.gif",
        "glyph_change_highlight/glyph_change_highlight_5frames.gif",
        "fg_glyph_change_highlight/fg_glyph_change_highlight_5frames.gif",
    )
    for relative in expected_roles:
        artifact = output / relative
        assert artifact.is_file(), relative
        with Image.open(artifact) as image:
            assert image.format == "GIF"
            assert image.n_frames == 5

    manifest = json.loads(
        (output / "presentation_artifact_manifest.json").read_text())
    assert manifest["eligibility"] == "actual_field"
    assert manifest["source"]["native_binding"]["admitted"] is False
    assert manifest["source"]["native_binding"]["reason"] == (
        "missing native_sweep_receipt.json")
    roles = [row["role"] for row in manifest["arms"]["single"]["artifacts"]]
    assert roles == ["full", "bg_only", "fg_only", "fg_highlight",
                     "glyph_change_highlight", "fg_glyph_change_highlight"]
    assert "native" not in roles
    sys.path.insert(0, str(ROOT / "scripts"))
    from run_fl4359_headed_movement import _presentation_artifact_manifest_gate
    gate = _presentation_artifact_manifest_gate({"presentation_artifact_manifest": manifest}, {})
    assert gate["failed_checks"] == ["single_missing_role_native"]
    assert gate["mode_comparison_eligible"] is False

    receipt = json.loads((output / "receipt.json").read_text())
    assert receipt["actual_field"]["all_frames_observed"] is True
    assert receipt["actual_field"]["presentation_mode_eligible"] is False
    assert receipt["capture_sha256"]
    traces = [json.loads(line) for line in capture.read_text().splitlines()
              if line.startswith("{") and "frame_trace" in line]
    assert all("capture_sha256" not in row["frame_trace"]["final_paint_field"]
               for row in traces)


def test_not_requested_sidecars_are_legacy_diagnostic_and_required_fails(
    tmp_path: Path,
) -> None:
    capture = _capture(tmp_path, not_requested=True)
    diagnostic = _run(capture, tmp_path / "diagnostic", required=False)
    assert diagnostic.returncode == 0, diagnostic.stderr
    receipt = json.loads(
        (tmp_path / "diagnostic" / "receipt.json").read_text())
    assert receipt["actual_field"]["mode"] == "legacy_pair_diagnostic"
    assert receipt["actual_field"]["frames_absent"] == 5
    manifest = json.loads(
        (tmp_path / "diagnostic" / "presentation_artifact_manifest.json").read_text())
    assert manifest["eligibility"] == "legacy_pair_diagnostic"

    rejected = _run(capture, tmp_path / "required", required=True)
    assert rejected.returncode != 0
    assert "not_requested" in rejected.stderr


def test_separate_native_capture_admits_fields_without_relabeling_boundary(tmp_path):
    """Exercise the producer-to-CLI join, including all five field hashes."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from fl4359_native_frame_artifact import publish_native_gif, native_presentation_provenance, sha256

    capture = _capture(tmp_path)
    policy = {"status": "applied", "mode": "CC", "fg_sampling": "continuous",
              "bg_sampling": "continuous", "family": "all", "owner": "test",
              "bg_owner": "family", "sha256": "a" * 64}
    lines = [json.loads(line) for line in capture.read_text().splitlines()]
    fields, native = [], []
    for row in lines:
        if not isinstance(row, dict) or "frame_trace" not in row:
            continue
        trace = row["frame_trace"]
        field = trace["final_paint_field"]
        field["applied_policy"] = policy
        field["late_overlay_observed"] = False
        index = row["frame_id"]
        fields.append({"frame_id": index, "mode": "CC", "camera_world": trace["camera_world"],
                       "field_sha256": field["raw_sha256"]})
        path = tmp_path / f"native-{index}.png"
        Image.new("RGB", (32, 32), (index * 20, 30, 40)).save(path)
        native.append({"sequence_frame": index, "path": str(path), "sha256": sha256(path)})
    capture.write_text("".join(json.dumps(line) + "\n" for line in lines))
    atlas = [{"path": native[0]["path"], "sha256": native[0]["sha256"]}]
    runtime = {"run_identity": {"source_identity_complete": True, "build_head": "test",
                                "build_worktree_diff_sha256": "b" * 64}}
    provenance = native_presentation_provenance(runtime, atlas, fields, policy)
    publish_native_gif(capture, native, fields, "cli-final-paint-capture", provenance=provenance)
    result = _run(capture, tmp_path / "RASTER")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((tmp_path / "RASTER/presentation_artifact_manifest.json").read_text())
    assert manifest["source"]["native_binding"]["admitted"] is True
    assert manifest["arms"]["single"]["field_observations_mode_eligible"] is True
    assert len(manifest["arms"]["single"]["artifacts"]) == 7
    receipt = json.loads((tmp_path / "RASTER/receipt.json").read_text())
    assert receipt["actual_field"]["presentation_mode_eligible"] is False
    path = tmp_path / "native_sweep_receipt.json"
    changed = json.loads(path.read_text())
    changed["presentation_provenance"]["field_sha256"].reverse()
    path.write_text(json.dumps(changed))
    rejected = _run(capture, tmp_path / "RASTER")
    assert rejected.returncode != 0
    assert "five field observations" in rejected.stderr


def test_tall_actual_field_cli_keeps_native_extent_and_checks_requested_width(tmp_path):
    capture = _capture(tmp_path)
    revised = []
    for line in capture.read_text().splitlines():
        row = json.loads(line)
        if isinstance(row, dict) and 'frame_trace' in row:
            trace = row['frame_trace']
            for target in (trace, trace['final_paint_field']):
                target['semantic_ansi_size'] = [2, 1]
                target['cell_size'] = [16, 32]
            trace['viewport']['cell_size'] = [16, 32]
        revised.append(row)
        if isinstance(row, list):
            second = list(row)
            second[2] = {'x': 1, 'y': 0}
            revised.append(second)
    capture.write_text(''.join(json.dumps(row) + '\n' for row in revised))
    wrong = _run(capture, tmp_path / 'wrong')
    assert wrong.returncode != 0
    output = tmp_path / 'tall'
    result = subprocess.run([sys.executable, str(RASTER), '--capture', str(capture),
                            '--out', str(output), '--cell-px', '32', '--cell-width', '16',
                            '--frames', '5', '--arm-label', 'native-tall-test',
                            '--actual-field-required'], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((output / 'receipt.json').read_text())
    assert receipt['native_cell_size'] == [16, 32]
    with Image.open(output / 'frame_00.png') as image:
        assert image.size == (32, 32)


def test_v2_preserves_integer_source_references_and_v1_colour_prefix(tmp_path):
    from fl4359_actual_field import ActualFieldReader
    capture = _capture(tmp_path)
    rows = [json.loads(line) for line in capture.read_text().splitlines()]
    trace = rows[1]["frame_trace"]
    metadata = trace["final_paint_field"]
    path = tmp_path / metadata["raw_path"]
    legacy = _field_records(0)
    records = np.zeros((32, 32, 24), dtype="<f4")
    records[..., :12] = legacy
    words = records.view("<u4")
    words[..., 12:20] = [0xffffffff, 0x70000123, 17, 19, 23, 29, 31, 37]
    words[..., 20] = 764
    words[..., 21] = 65
    records[..., 22] = 0.375
    words[..., 23] = 0x131
    records.tofile(path)
    metadata.update(schema="fl4359.final_paint_field.v2", record_bytes=96,
                    bytes=path.stat().st_size, raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    scalar_format="float32_uint32",
                    record_layout=RECORD_LAYOUT + ["front_sources", "under_sources", "composition"])
    frame = ActualFieldReader(capture).read(trace, frame_id=0, sequence_index=0)
    np.testing.assert_array_equal(frame.fields["final_rgb"], legacy[..., 8:11])
    assert frame.fields["front_sources"].dtype == np.dtype("<u4")
    np.testing.assert_array_equal(frame.fields["front_sources"][0, 0], words[0, 0, 12:16])
    assert frame.fields["composition"][0, 0, 2].view("<f4") == 0.375


def test_v2_pixel_provenance_requires_actual_source_generation(tmp_path):
    from fl4359_actual_field import ActualFieldReader, ActualFieldError
    import pytest
    capture = _capture(tmp_path)
    rows = [json.loads(line) for line in capture.read_text().splitlines()]
    trace = rows[1]["frame_trace"]
    metadata = trace["final_paint_field"]
    path = tmp_path / metadata["raw_path"]
    records = np.zeros((32, 32, 24), dtype="<f4")
    records[..., :12] = _field_records(0)
    words = records.view("<u4")
    # A CURRENT reference to texel(1,2) in physical layer7 of a4x4 capture.
    reference = (1 << 28) | (7 * 16 + 2 * 4 + 1 + 1)
    words[..., 12:16] = reference
    words[..., 16:20] = reference
    words[..., 20:22] = [65, 66]
    records[..., 22] = 0.25
    # admission=1, presentation=3, underlay used, and clipped two-column
    # front/underlay spans whose origin is this only cell.
    words[..., 23] = 0x4171
    records.tofile(path)
    metadata.update(schema="fl4359.final_paint_field.v2", record_bytes=96,
                    bytes=path.stat().st_size, raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    scalar_format="float32_uint32", projection_kind=1,
                    camera_projection_mode=0, projection_mode=2,
                    source_capture_state={"probe_size":4, "layer_content_epoch_generations":[11]*18},
                    record_layout=RECORD_LAYOUT + ["front_sources", "under_sources", "composition"])
    frame = ActualFieldReader(capture).read(trace, frame_id=0, sequence_index=0)
    with pytest.raises(ActualFieldError, match="source-generation bank"):
        frame.pixel_provenance(1, 2)
    # Historical layer epoch 11 cannot attest a captured texel. Only its
    # immutable bank record establishes actual generation 1.
    from dataclasses import replace
    front_generations = np.ones((18, 4, 4), dtype="<u4")
    under_generations = np.full((18, 4, 4), 2, dtype="<u4")
    frame = replace(frame, source_generation_maps={
        "front": front_generations, "underlay": under_generations})
    result = frame.pixel_provenance(1, 2)
    assert result["front"]["foreground"]["texel"] == [1, 2]
    assert result["front"]["foreground"]["layer"] == 7
    assert result["front"]["foreground"]["capture_generation"] == 1
    assert result["underlay"]["foreground"]["capture_generation"] == 2
    assert result["underlay_used"] and result["underlay_intensity"] == 0.25
    assert result["admission_code"] == 1
    assert result["admission_reason"] == "admitted"
    explanation = frame.displayed_result_explanation(1, 2)
    assert explanation["schema"] == "fl4359.displayed_result_explanation.v1"
    assert explanation["native_visibility"]["front_source"] == reference
    assert explanation["span_channel_authority"] == {
        "front_span": {
            "origin_cell": [0, 0], "columns": 2, "continuation": False},
        "underlay_span": {
            "origin_cell": [0, 0], "columns": 2, "continuation": False},
        "glyph_source": reference,
        "flat_ink_source": reference,
        "native_background_source": reference,
        "underlay_used": True,
        "background_and_deeper_layer_domain": "native_pixel",
        "valid": True,
        "refusal": None,
    }
    assert explanation["capture"]["front"]["glyph"]["status"] == "unavailable"
    assert explanation["native_paint"]["debug_view"] == 0

    changed_fields = {name: value.copy() for name, value in frame.fields.items()}
    changed_fields["composition"][2, 1, 0] = 67
    changed = replace(frame, fields=changed_fields, actual_frame_index=2)
    change = changed.compare_displayed_result(frame, 1, 2)
    assert change["schema"] == "fl4359.displayed_result_change.v1"
    assert change["first_divergence"] == "cell_election"
    assert change["changes"][0]["before"]["displayed_glyph"] == 65
    assert change["changes"][0]["after"]["displayed_glyph"] == 67

    front_generations[7, 2, 1] = 0
    with pytest.raises(ActualFieldError, match="captured generation"):
        frame.pixel_provenance(1, 2)
