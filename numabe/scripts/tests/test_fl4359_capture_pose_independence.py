"""FL-4359 CPU parameter regression, not rendered shimmer acceptance.

Instantiate the complete production owner and inspect its parameter bytes with
no RenderingDevice attached. Upload must fail closed. No extracted method body,
mock production members, scene, display, server, screenshot or FPS run is used.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
HOST = "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd"
BAD_COMMIT = "bb39a9de5188d472203e777a78ce37858d308c4c"


def fixture_source():
    return '''extends SceneTree
const Pipeline = preload("res://render/backends/perspective/PerspectiveProbeBackend.gd")
var pipeline = Pipeline.new()
func _init() -> void:
	var mismatches := 0
	var cases := 0
	for bands in [[0, 1, 2], [0, 2, 1]]:
		pipeline._state = {"capture_bands": bands, "generation": 17,
			"captured_origins": [Vector3(1, 2, 3), Vector3(8, 0, 8), Vector3(4, 0, 8)]}
		var schedules: Array = [[], range(18), [0, 5, 6, 11, 12, 17]]
		for layer in range(18):
			schedules.append([layer])
		for size in [80, 192, 384]:
			pipeline._probe_size = size
			for schedule in schedules:
				var baseline := PackedByteArray()
				# Includes turn-away/return and changed display FOV/aspect, while
				# capture origins, range, generation and freshness remain fixed.
				for forward in [Vector4(0, 0, 1, 512), Vector4(1, 0, 0, 512),
					Vector4(0, 0, -1, 512), Vector4(0, 1, 0, 512), Vector4(0, 0, 1, 512)]:
					for tangent in [Vector4(1, 1, 0, 0), Vector4(2, 0.5, 0, 0)]:
						pipeline._active_camera = {"camera_forward_far": forward,
							"perspective_tan_xy": tangent, "probe_near": 0.01, "probe_far": 512.0}
						var receipt: Dictionary = pipeline._write_texel_shade_params({"layers_recaptured_this_frame": schedule})
						assert(not receipt.valid and not receipt.params_upload_ok)
						var params_bytes: PackedByteArray = receipt.params_bytes
						if baseline.is_empty():
							baseline = params_bytes.duplicate()
						elif baseline != params_bytes:
							mismatches += 1
						var expected_mask := 0
						for layer in range(18):
							var logical: int = bands.find(layer / 6)
							var fresh: bool = schedule.has(layer)
							var expected_code: int = logical + 1 + 4 if fresh else 0
							if int(params_bytes.decode_float(layer * 16 + 12)) != expected_code:
								push_error("FRESHNESS_OR_BAND_MISMATCH")
								quit(3)
								return
							if fresh:
								expected_mask |= 1 << layer
							var origin: Vector3 = pipeline._state.captured_origins[logical]
							if Vector3(params_bytes.decode_float(layer * 16),
								params_bytes.decode_float(layer * 16 + 4),
								params_bytes.decode_float(layer * 16 + 8)) != origin:
								push_error("CAPTURE_ORIGIN_MISMATCH")
								quit(4)
								return
						if receipt.layer_mask != expected_mask or receipt.shaded_layers.size() != schedule.size():
							push_error("SHADE_SCHEDULE_MISMATCH")
							quit(5)
							return
						cases += 1
	print("CAPTURE_POSE_CASES=%d POSE_DEPENDENT_PARAMS=%d" % [cases, mismatches])
	quit(0 if mismatches == 0 else 2)
'''


def execute_writer(tmp_path):
    binary = os.environ.get("GODOT_BIN") or shutil.which("godot")
    assert binary, "Godot required: set GODOT_BIN (this is a CPU fixture, not a game run)"
    binary = str(Path(binary).resolve())
    script = tmp_path / "capture_params.gd"
    script.write_text(fixture_source())
    return subprocess.run([binary, "--headless", "--path", str(ROOT / "godot_project"),
                           "--script", str(script)], capture_output=True,
                          text=True, timeout=30)


def test_complete_owner_capture_params_are_pose_independent(tmp_path):
    result = execute_writer(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CAPTURE_POSE_CASES=1260 POSE_DEPENDENT_PARAMS=0" in result.stdout, result.stderr




def assert_cull_inputs(source):
    source = re.sub(r"//[^\n]*|/\*.*?\*/", "", source, flags=re.S)
    body = source.split("void main()", 1)[1]
    # Check the inputs actually read, not the name of a removed helper. Face
    # activity, identity and captured range are legal; painted payload and
    # display sources are not a retained-texel admission oracle. The
    # eye/grid/prev lanes are the frozen capture origins selected by band
    # role (the same SplatParams the splat consumes) for the per-role
    # frustum test -- never live display state.
    assert set(re.findall(r"params\.(\w+)", body)) == {
        "probe", "bands", "eye", "prev", "grid"}
    # Capture geometry only: identity, depth, and anchor position. Anchor
    # joins the admission tuple for the actor waterline check and the
    # front/underlay shared-source comparison; it carries no paint.
    assert set(re.findall(r"uniform\s+[ui]?sampler\w+\s+(\w+)", source)) == {
        "identity", "normal_depth", "capture_anchor"}
    for forbidden in ("display_sources", "texel_payload", "inv_view_proj",
                      "fl4359_source_effects", "visible_sample"):
        assert forbidden not in source, forbidden


def test_cull_has_no_payload_or_display_visibility_input():
    assert_cull_inputs((ROOT / "godot_project/render/backends/perspective/cull/perspective_probe_cull.glsl").read_text())


def test_historical_payload_cull_is_rejected():
    old = subprocess.run(["git", "show", f"{BAD_COMMIT}:godot_project/render/backends/perspective/cull/perspective_probe_cull.glsl"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    with pytest.raises(AssertionError):
        assert_cull_inputs(old)
