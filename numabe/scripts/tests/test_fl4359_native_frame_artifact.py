from pathlib import Path
from types import SimpleNamespace
import copy
import sys

from PIL import Image
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_actual_field import ActualFieldError
from fl4359_native_frame_artifact import sha256, validate_native_frame
from fl4359_native_frame_artifact import native_presentation_provenance
from fl4359_raster_jitter_capture import _joined_native_provenance_eligible


def fixture(tmp_path):
    path = tmp_path / "native.png"
    Image.new("RGB", (32, 32), "green").save(path)
    policy = {"mode": "CC"}
    field = SimpleNamespace(sequence_frame=2, actual_frame_index=50, request_id=3,
                            policy=policy, width=32, height=32)
    frame = {"path": str(path), "sha256": sha256(path), "size": [32, 32],
             "post_draw_frame": 51, "dispatch": {"sequence_frame": 2,
             "capture_frame_index": 50, "frame_index": 51, "request_id": 3,
             "pixel_camera_contract": {"frame_index": 50},
             "tilt_shift_enabled": False, "tilt_shift_intensity": 0, "policy": policy}}
    return frame, field


def test_native_image_requires_matching_frame_and_original_pixels(tmp_path):
    frame, field = fixture(tmp_path)
    assert validate_native_frame(frame, field)["engine_frame"] == 50
    Image.new("RGB", (32, 32), "red").save(frame["path"])
    with pytest.raises(ActualFieldError, match="hash mismatch"):
        validate_native_frame(frame, field)


@pytest.mark.parametrize("key,value", [("sequence_frame", 1), ("capture_frame_index", 49),
                         ("frame_index", 49), ("request_id", 2),
                         ("pixel_camera_contract", {"frame_index": 49}),
                         ("tilt_shift_enabled", True), ("tilt_shift_intensity", 1.5),
                         ("policy", {"mode": "FF"})])
def test_native_join_rejects_stale_frame_wrong_mode_or_tilt(tmp_path, key, value):
    frame, field = fixture(tmp_path)
    frame = copy.deepcopy(frame)
    frame["dispatch"][key] = value
    with pytest.raises(ActualFieldError):
        validate_native_frame(frame, field)


def test_separate_native_provenance_must_bind_the_exact_fields_and_atlases(tmp_path):
    frame, _ = fixture(tmp_path)
    atlas = [{"path": frame["path"], "sha256": frame["sha256"]}]
    policy = {"status": "applied", "mode": "CC", "fg_sampling": "continuous",
              "bg_sampling": "continuous", "family": "all", "owner": "test",
              "bg_owner": "family", "sha256": "a" * 64}
    runtime = {"run_identity": {"build_head": "test-build", "source_identity_complete": True,
                                "build_worktree_diff_sha256": "b" * 64}}
    fields = [{"field_sha256": str(i) * 64} for i in range(5)]
    p = native_presentation_provenance(runtime, atlas, fields, policy)
    native = {"presentation_provenance": p}
    hashes = [f["field_sha256"] for f in fields]
    assert _joined_native_provenance_eligible(native, policy, hashes)
    assert not _joined_native_provenance_eligible({}, policy, hashes)
    with pytest.raises(ActualFieldError, match="five field"):
        _joined_native_provenance_eligible(native, policy, hashes[::-1])
    with pytest.raises(ActualFieldError, match="five field"):
        _joined_native_provenance_eligible(native, {**policy, "mode": "FF"}, hashes)
    Image.new("RGB", (32, 32), "red").save(frame["path"])
    with pytest.raises(ActualFieldError, match="atlas snapshot hash"):
        _joined_native_provenance_eligible(native, policy, hashes)
