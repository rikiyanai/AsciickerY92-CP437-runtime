"""FL-4359: bind post-flight viewport images to same-dispatch field observations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from fl4359_actual_field import ActualFieldError, _eligibility, _validate_policy
from fl4359_stitch_sweep_gif import _write_gif


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_native_frame(frame, field):
    dispatch = frame.get("dispatch", {})
    expected = {"sequence_frame": field.sequence_frame,
                "capture_frame_index": field.actual_frame_index,
                "request_id": field.request_id}
    if any(dispatch.get(key) != value for key, value in expected.items()):
        raise ActualFieldError("native viewport/field dispatch identity mismatch")
    # PixelCameraContext names the source frame; frames_drawn names the render
    # completion clock. They differ by one in the live engine. Join each to its
    # own owner instead of conflating the counters or accepting an arbitrary lag.
    if dispatch.get("pixel_camera_contract", {}).get("frame_index") != field.actual_frame_index:
        raise ActualFieldError("native source camera frame mismatch")
    if dispatch.get("frame_index") != frame.get("post_draw_frame"):
        raise ActualFieldError("native compositor render completion frame mismatch")
    if dispatch.get("tilt_shift_enabled") is not False or dispatch.get("tilt_shift_intensity") != 0:
        raise ActualFieldError("native viewport tilt shift must be off")
    if dispatch.get("policy") != field.policy:
        raise ActualFieldError("native viewport presentation policy mismatch")
    path = Path(frame["path"])
    if sha256(path) != frame.get("sha256"):
        raise ActualFieldError("native viewport PNG hash mismatch")
    with Image.open(path) as image:
        if list(image.size) != frame.get("size") or image.size != (field.width, field.height):
            raise ActualFieldError("native viewport/field extent mismatch")
    return {"sequence_frame": field.sequence_frame,
            "engine_frame": field.actual_frame_index, "request_id": field.request_id,
            "path": str(path), "sha256": frame["sha256"],
            "post_draw_frame": frame.get("post_draw_frame")}


def native_presentation_provenance(runtime, atlas_sources, field_frames, policy):
    """Join recorded build and bound-atlas facts without relabeling raw fields as UI captures."""
    identity = runtime.get("run_identity", {})
    source = {"status": "available", "build_id": identity.get("build_head"),
              "dirty_diff_sha256": identity.get("build_worktree_diff_sha256")}
    atlas = {"status": "available", "sources": atlas_sources}
    if identity.get("source_identity_complete") is not True:
        raise ActualFieldError("native capture build identity is incomplete")
    _eligibility(source, "native.source")
    _eligibility(atlas, "native.atlas")
    if not _validate_policy(policy)[1]:
        raise ActualFieldError("native presentation requires an applied policy")
    for page in atlas_sources:
        if sha256(Path(page["path"])) != page["sha256"]:
            raise ActualFieldError("native presentation atlas hash mismatch")
    return {"source": source, "atlas": atlas, "applied_policy": policy,
            "field_sha256": [frame["field_sha256"] for frame in field_frames]}


def publish_native_gif(capture, native_frames, field_frames, capture_id, *, provenance=None):
    if len(native_frames) != 5 or [f["sequence_frame"] for f in native_frames] != list(range(5)):
        raise ActualFieldError("exactly five joined native viewport frames required")
    mode = field_frames[0]["mode"]
    suffix = {"FF": "FLAT_BG_FLAT_FG", "FC": "CONTINUOUS_BG_FLAT_FG",
              "CF": "FLAT_BG_CONTINUOUS_FG", "CC": "CONTINUOUS_BG_CONTINUOUS_FG"}[mode]
    frames = []
    for frame in native_frames:
        with Image.open(frame["path"]) as image:
            frames.append(image.convert("RGB"))
    named = capture.parent / f"native_5frames_{suffix}.gif"
    _write_gif(frames, named, 60)
    with Image.open(named) as check:
        if check.n_frames != 5:
            raise ActualFieldError("native GIF encoder did not retain five frames")
    # Canonical consumer alias plus the explicit user-facing mode filename.
    alias = capture.parent / "sweep_5frames.gif"
    alias.write_bytes(named.read_bytes())
    receipt = {"schema": "fl4359.native_sweep_receipt.v1", "status": "available",
               "owner": "same_frame_viewport_capture", "capture_id": capture_id,
               "frame_ids": [f["frame_id"] for f in field_frames],
               "poses": [f["camera_world"] for f in field_frames],
               "capture_sha256": sha256(capture), "path": str(alias.resolve()),
               "named_path": str(named.resolve()), "sha256": sha256(alias),
               "native_frames": native_frames, "tilt_shift_enabled": False,
               "presentation_provenance": provenance,
               "claim": "Same-dispatch final viewport capture; PNGs are lossless, GIF is an indexed-colour viewing copy."}
    (capture.parent / "native_sweep_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt
