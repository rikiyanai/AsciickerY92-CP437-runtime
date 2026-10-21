#!/usr/bin/env python3
"""Create and validate the complete FL-4431 Baseline A successor receipt.

The original ``asciicker.godot_baseline.v1`` receipt names the correct source
checkout, but it does not bind the capture manifest, proof tools, runtime
frames, metadata, and same-process runtime truth that make a visual comparison
reproducible.  Editing that operator-designated file in place would silently
replace its identity.  This tool therefore writes a new, closed
``asciicker.godot_baseline.v2`` receipt at a caller-selected path and never
modifies the v1 predecessor.

The tool has two subcommands:

* ``create`` copies facts from one non-promotable capture receipt, derives Git
  and file identities from disk, computes a canonical self-hash, validates the
  finished object, then creates a previously nonexistent output file.
* ``validate`` rechecks the closed JSON shape, its canonical self-hash, every
  attached file, both Git identities, and every copied capture fact.

The capture input is deliberately classified as non-promotable.  It gathers
facts; only the explicit operator designation attached to the v2 receipt can
name Baseline A.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA = "asciicker.godot_baseline.v2"
CAPTURE_SCHEMA = "fl4431.baseline_capture.non_promotable.v1"
DESIGNATION_SCHEMA = "asciicker.operator_baseline_designation.v1"
SELF_HASH_ALGORITHM = "sha256"
SELF_HASH_CANONICALIZATION = "utf8-json-sort-keys-compact-with-self_hash-excluded"
BASELINE_NAME = "Baseline A"
FULL_HEX_LENGTH = 40
SHA256_HEX_LENGTH = 64

# These eight roles are the executable front doors that designate and create
# the visual package. Their paths are fixed relative to the proof-tool checkout
# so a receipt cannot quietly substitute a similarly named helper from
# elsewhere. The receipt builder includes itself because changing the validator
# changes what the immutable receipt means. The detached launcher is included
# because it is the gate that admits the receipt to an expensive capture.
REQUIRED_TOOL_PATHS: Mapping[str, str] = {
    "baseline_receipt_builder": "scripts/build_fl4431_baseline_receipt_v2.py",
    "detached_launcher": "scripts/launch_fl4359_acceptance_package_detached.py",
    "acceptance_runner": "scripts/run_fl4359_acceptance_package.py",
    "grid_builder": "scripts/build_godot_canonical_png_grid.py",
    "capture_adapter": "scripts/fl4408_actual_godot_capture_adapter.py",
    "headed_launcher": "scripts/run_fl4359_headed_movement.py",
    "movement_driver": "scripts/adhoc/fl4359_external_movement_driver.gd",
    "dogfight_driver": "godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd",
}

# Analyzer output is evidence interpretation, not capture choreography.  These
# four files cover airplane receipt interpretation, moving-camera metrics,
# exact-key attribution, and the final side-by-side review composition.
REQUIRED_ANALYZER_PATHS: Mapping[str, str] = {
    "airplane_receipt_analyzer": "scripts/run_fl4265_airplane_proof.py",
    "moving_camera_analyzer": "scripts/analyze_fl4359_moving_camera.py",
    "exact_key_analyzer": "scripts/analyze_fl4359_exact_key.py",
    "review_grid_builder": "scripts/build_fl4359_sunday_current_review_grid.py",
}

TOP_LEVEL_KEYS = {
    "schema",
    "receipt_version",
    "title",
    "failure_log_refs",
    "created_at_utc",
    "designation_record",
    "designation",
    "predecessor_receipt",
    "baseline_source",
    "proof_tool_source",
    "proof_tools",
    "lane_manifest",
    "capture_input",
    "capture_contract",
    "baseline_build_identity",
    "source_frames",
    "self_hash",
}
DESIGNATION_KEYS = {
    "schema",
    "baseline_name",
    "successor_schema",
    "successor_receipt_path",
    "operator_identity",
    "designated_at_utc",
    "statement",
    "predecessor_receipt_path",
    "predecessor_receipt_sha256",
}
PATH_REF_KEYS = {"path", "sha256", "size_bytes"}
GIT_FILE_REF_KEYS = PATH_REF_KEYS | {"git_blob"}
BASELINE_SOURCE_KEYS = {
    "root",
    "commit",
    "tree",
    "git_object_format",
    "clean_required",
    "clean_observed",
    "project_subdir",
    "project_file",
}
PROOF_SOURCE_KEYS = {
    "root",
    "commit",
    "tree",
    "git_object_format",
    "scoped_clean_required",
    "scoped_clean_observed",
    "scoped_paths",
}
PROOF_TOOLS_KEYS = set(REQUIRED_TOOL_PATHS) | {"analyzers"}
CAPTURE_TOP_LEVEL_KEYS = {
    "schema",
    "capture_classification",
    "capture_id",
    "temporal_capture_id",
    "process_identity",
    "created_at_utc",
    "baseline_source_commit",
    "baseline_source_tree",
    "proof_tool_commit",
    "lane_manifest_path",
    "lane_manifest_sha256",
    "argument_vectors",
    "route",
    "dimensions",
    "time_profile",
    "server_identity",
    "baseline_build_identity",
    "features",
    "source_frames",
}
CAPTURE_CONTRACT_KEYS = {
    "capture_id",
    "temporal_capture_id",
    "process_identity",
    "argument_vectors",
    "route",
    "dimensions",
    "time_profile",
    "server_identity",
    "features",
}
ARGUMENT_VECTOR_KEYS = {"day", "night"}
ROUTE_KEYS = {
    "lane_id",
    "identity",
    "physical_input_action",
    "tick_zero_kind",
    "warmup_tick",
    "target_ticks",
    "tick_stride",
    "frame_labels",
    "poses",
}
POSE_KEYS = {
    "label",
    "route_tick",
    "player_position",
    "player_yaw",
    "camera_position",
    "camera_yaw",
    "camera_pitch",
    "camera_roll",
    "airplane_position",
    "airplane_yaw",
    "projection",
    "fov_degrees",
    "near",
    "far",
}
DIMENSION_KEYS = {
    "requested_width",
    "requested_height",
    "actual_width",
    "actual_height",
    "ansi_width",
    "ansi_height",
    "fullscreen",
    "ascii",
}
TIME_PROFILE_KEYS = {
    "day_cycle_value",
    "night_cycle_value",
    "day_world_hour",
    "night_world_hour",
}
SERVER_IDENTITY_KEYS = {
    "server_commit",
    "server_source_root",
    "server_runtime_scope_fingerprint",
    "server_endpoint",
    "server_process_identity",
}
BUILD_IDENTITY_KEYS = {
    "execution_kind",
    "godot_requested_path",
    "godot_resolved_binary",
    "godot_version",
    "godot_binary_format",
    "imported_source_commit",
    "imported_source_tree",
    "project_import_marker",
}
FRAME_KEYS = {
    "lane_id",
    "label",
    "arm",
    "route_tick",
    "capture_id",
    "temporal_capture_id",
    "process_identity",
    "png",
    "metadata",
    "runtime_truth",
}
SELF_HASH_KEYS = {"algorithm", "canonicalization", "excluded_field", "digest"}


class ReceiptValidationError(RuntimeError):
    """Raised when one receipt fact cannot be proven from its declared owner."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize a dictionary in the one byte order used by the self-hash.

    Sorting keys and removing optional whitespace ensures that formatting a
    receipt differently does not change the identity of its facts.
    """

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _receipt_digest(receipt: Mapping[str, Any]) -> str:
    """Hash every receipt fact except the self-referential ``self_hash`` block."""

    unsigned = dict(receipt)
    unsigned.pop("self_hash", None)
    return hashlib.sha256(_canonical_json_bytes(unsigned)).hexdigest()


def _file_sha256(path: Path) -> str:
    """Return the SHA-256 digest of one evidence file's exact bytes."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    """Load one JSON object and preserve a precise error label for the caller."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReceiptValidationError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReceiptValidationError(f"{label} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReceiptValidationError(f"{label} root must be a JSON object")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    """Reject missing plus unknown fields so the schema cannot widen silently."""

    observed = set(value)
    missing = sorted(expected - observed)
    unknown = sorted(observed - expected)
    if missing:
        raise ReceiptValidationError(f"{path} missing fields: {', '.join(missing)}")
    if unknown:
        raise ReceiptValidationError(f"{path} unknown fields: {', '.join(unknown)}")


def _require_object(value: Any, path: str) -> dict[str, Any]:
    """Require one JSON object and return it with a precise validation path."""

    if not isinstance(value, dict):
        raise ReceiptValidationError(f"{path} must be an object")
    return value


def _require_string(value: Any, path: str, *, nonempty: bool = True) -> str:
    """Require a string; evidence identifiers are never coerced from numbers."""

    if not isinstance(value, str):
        raise ReceiptValidationError(f"{path} must be a string")
    if nonempty and not value:
        raise ReceiptValidationError(f"{path} must not be empty")
    return value


def _require_bool(value: Any, path: str) -> bool:
    """Require a real JSON boolean instead of accepting Python's integer subtype."""

    if type(value) is not bool:
        raise ReceiptValidationError(f"{path} must be a boolean")
    return value


def _require_int(value: Any, path: str, *, minimum: int | None = None) -> int:
    """Require a real integer and optionally enforce its smallest admitted value."""

    if type(value) is not int:
        raise ReceiptValidationError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ReceiptValidationError(f"{path} must be at least {minimum}")
    return value


def _require_number(value: Any, path: str) -> float:
    """Require a finite JSON number so NaN cannot enter pose identity."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReceiptValidationError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ReceiptValidationError(f"{path} must be finite")
    return number


def _require_string_list(
    value: Any,
    path: str,
    *,
    nonempty: bool = True,
    unique: bool = False,
) -> list[str]:
    """Require a list of nonempty strings with optional duplicate rejection."""

    if not isinstance(value, list):
        raise ReceiptValidationError(f"{path} must be an array")
    if nonempty and not value:
        raise ReceiptValidationError(f"{path} must not be empty")
    result = [_require_string(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if unique and len(set(result)) != len(result):
        raise ReceiptValidationError(f"{path} must not contain duplicates")
    return result


def _require_full_hex(value: Any, path: str, *, length: int) -> str:
    """Require a lowercase hexadecimal identity at its full published width."""

    text = _require_string(value, path)
    if len(text) != length or any(character not in "0123456789abcdef" for character in text):
        raise ReceiptValidationError(
            f"{path} must be {length} lowercase hexadecimal characters"
        )
    return text


def _require_timestamp(value: Any, path: str) -> str:
    """Require an explicit timezone-bearing ISO-8601 timestamp."""

    text = _require_string(value, path)
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReceiptValidationError(f"{path} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ReceiptValidationError(f"{path} must include a timezone")
    return text


def _require_absolute_regular_file(path_text: Any, path: str) -> Path:
    """Require an absolute, non-symlinked regular file on the current disk."""

    text = _require_string(path_text, path)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise ReceiptValidationError(f"{path} must be absolute")
    if candidate.is_symlink():
        raise ReceiptValidationError(f"{path} must not be a symlink: {candidate}")
    if not candidate.is_file():
        raise ReceiptValidationError(f"{path} is not a regular file: {candidate}")
    return candidate.resolve()


def _require_absolute_directory(path_text: Any, path: str) -> Path:
    """Require an absolute, non-symlinked directory that can own relative paths."""

    text = _require_string(path_text, path)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise ReceiptValidationError(f"{path} must be absolute")
    if candidate.is_symlink():
        raise ReceiptValidationError(f"{path} must not be a symlink: {candidate}")
    if not candidate.is_dir():
        raise ReceiptValidationError(f"{path} is not a directory: {candidate}")
    return candidate.resolve()


def _path_ref(path: Path) -> dict[str, Any]:
    """Create the closed path/size/SHA identity used for external attachments."""

    resolved = _require_absolute_regular_file(str(path), "path_ref.path")
    return {
        "path": str(resolved),
        "sha256": _file_sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _validate_path_ref(value: Any, path: str) -> Path:
    """Validate one external attachment against its recorded size and digest."""

    record = _require_object(value, path)
    _require_exact_keys(record, PATH_REF_KEYS, path)
    candidate = _require_absolute_regular_file(record["path"], f"{path}.path")
    expected_size = _require_int(record["size_bytes"], f"{path}.size_bytes", minimum=0)
    if candidate.stat().st_size != expected_size:
        raise ReceiptValidationError(
            f"{path} size mismatch: expected {expected_size}, observed {candidate.stat().st_size}"
        )
    expected_sha = _require_full_hex(
        record["sha256"], f"{path}.sha256", length=SHA256_HEX_LENGTH
    )
    observed_sha = _file_sha256(candidate)
    if observed_sha != expected_sha:
        raise ReceiptValidationError(
            f"{path} SHA-256 mismatch: expected {expected_sha}, observed {observed_sha}"
        )
    return candidate


def _run_git(root: Path, *args: str) -> str:
    """Run one read-only Git query and return its trimmed standard output."""

    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReceiptValidationError(
            f"Git query failed in {root}: git {' '.join(args)}: {detail}"
        )
    return result.stdout.strip()


def _git_status(root: Path, paths: Sequence[str] | None = None) -> list[str]:
    """Return dirty rows for the whole checkout or one explicit scoped path set."""

    command = ["status", "--porcelain", "--untracked-files=all"]
    if paths is not None:
        command.extend(["--", *paths])
    output = _run_git(root, *command)
    return [line for line in output.splitlines() if line.strip()]


def _relative_repo_path(root: Path, path: Path, field: str) -> str:
    """Return a stable POSIX path and reject files outside their declared repo."""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ReceiptValidationError(f"{field} must remain inside {root}") from exc


def _git_file_ref(root: Path, commit: str, relative_path: str) -> dict[str, Any]:
    """Bind one working file to both its SHA-256 bytes and committed Git blob."""

    absolute = root / relative_path
    record = _path_ref(absolute)
    record["git_blob"] = _require_full_hex(
        _run_git(root, "rev-parse", f"{commit}:{relative_path}"),
        f"Git blob for {relative_path}",
        length=FULL_HEX_LENGTH,
    )
    return record


def _validate_git_file_ref(
    value: Any,
    path: str,
    *,
    root: Path,
    commit: str,
    required_relative_path: str,
) -> Path:
    """Validate bytes, repository containment, committed path, and Git blob."""

    record = _require_object(value, path)
    _require_exact_keys(record, GIT_FILE_REF_KEYS, path)
    file_path = _validate_path_ref(
        {key: record[key] for key in PATH_REF_KEYS},
        path,
    )
    observed_relative = _relative_repo_path(root, file_path, f"{path}.path")
    if observed_relative != required_relative_path:
        raise ReceiptValidationError(
            f"{path}.path must be {required_relative_path}, observed {observed_relative}"
        )
    expected_blob = _require_full_hex(
        record["git_blob"], f"{path}.git_blob", length=FULL_HEX_LENGTH
    )
    observed_blob = _require_full_hex(
        _run_git(root, "rev-parse", f"{commit}:{required_relative_path}"),
        f"{path} live Git blob",
        length=FULL_HEX_LENGTH,
    )
    if observed_blob != expected_blob:
        raise ReceiptValidationError(
            f"{path} Git blob mismatch: expected {expected_blob}, observed {observed_blob}"
        )
    return file_path


def _validate_vector3(value: Any, path: str) -> None:
    """Require exactly three finite coordinates for one world-space position."""

    if not isinstance(value, list) or len(value) != 3:
        raise ReceiptValidationError(f"{path} must contain exactly three coordinates")
    for index, coordinate in enumerate(value):
        _require_number(coordinate, f"{path}[{index}]")


def _validate_designation(value: Any, path: str) -> dict[str, Any]:
    """Validate the explicit operator act that promotes this successor receipt."""

    designation = _require_object(value, path)
    _require_exact_keys(designation, DESIGNATION_KEYS, path)
    if designation["schema"] != DESIGNATION_SCHEMA:
        raise ReceiptValidationError(f"{path}.schema must be {DESIGNATION_SCHEMA}")
    if designation["baseline_name"] != BASELINE_NAME:
        raise ReceiptValidationError(f"{path}.baseline_name must be {BASELINE_NAME}")
    if designation["successor_schema"] != SCHEMA:
        raise ReceiptValidationError(f"{path}.successor_schema must be {SCHEMA}")
    successor_path = Path(
        _require_string(
            designation["successor_receipt_path"],
            f"{path}.successor_receipt_path",
        )
    ).expanduser()
    if not successor_path.is_absolute():
        raise ReceiptValidationError(f"{path}.successor_receipt_path must be absolute")
    _require_string(designation["operator_identity"], f"{path}.operator_identity")
    _require_timestamp(designation["designated_at_utc"], f"{path}.designated_at_utc")
    _require_string(designation["statement"], f"{path}.statement")
    _require_absolute_regular_file(
        designation["predecessor_receipt_path"],
        f"{path}.predecessor_receipt_path",
    )
    _require_full_hex(
        designation["predecessor_receipt_sha256"],
        f"{path}.predecessor_receipt_sha256",
        length=SHA256_HEX_LENGTH,
    )
    return designation


def _validate_predecessor_v1(path: Path, field: str) -> None:
    """Require the linked historical object to be the designated v1 schema."""

    predecessor = _load_json_object(path, label=field)
    if predecessor.get("schema") != "asciicker.godot_baseline.v1":
        raise ReceiptValidationError(
            f"{field}.schema must be asciicker.godot_baseline.v1"
        )


def _validate_pose(value: Any, path: str) -> None:
    """Validate one route frame's complete player, camera, and airplane pose."""

    pose = _require_object(value, path)
    _require_exact_keys(pose, POSE_KEYS, path)
    _require_string(pose["label"], f"{path}.label")
    _require_int(pose["route_tick"], f"{path}.route_tick", minimum=0)
    _validate_vector3(pose["player_position"], f"{path}.player_position")
    _require_number(pose["player_yaw"], f"{path}.player_yaw")
    _validate_vector3(pose["camera_position"], f"{path}.camera_position")
    _require_number(pose["camera_yaw"], f"{path}.camera_yaw")
    _require_number(pose["camera_pitch"], f"{path}.camera_pitch")
    _require_number(pose["camera_roll"], f"{path}.camera_roll")
    _validate_vector3(pose["airplane_position"], f"{path}.airplane_position")
    if float(pose["airplane_position"][1]) <= 0.0:
        raise ReceiptValidationError(f"{path}.airplane_position must be airborne")
    _require_number(pose["airplane_yaw"], f"{path}.airplane_yaw")
    if pose["projection"] not in {"ortho", "perspective"}:
        raise ReceiptValidationError(f"{path}.projection is unsupported")
    _require_number(pose["fov_degrees"], f"{path}.fov_degrees")
    near = _require_number(pose["near"], f"{path}.near")
    far = _require_number(pose["far"], f"{path}.far")
    if near <= 0.0 or far <= near:
        raise ReceiptValidationError(f"{path} near/far planes are invalid")


def _validate_route(value: Any, path: str) -> None:
    """Validate ordered route ticks, labels, and exact runtime poses."""

    route = _require_object(value, path)
    _require_exact_keys(route, ROUTE_KEYS, path)
    _require_string(route["lane_id"], f"{path}.lane_id")
    _require_string(route["identity"], f"{path}.identity")
    _require_string(route["physical_input_action"], f"{path}.physical_input_action")
    _require_string(route["tick_zero_kind"], f"{path}.tick_zero_kind")
    _require_int(route["warmup_tick"], f"{path}.warmup_tick", minimum=0)
    _require_int(route["tick_stride"], f"{path}.tick_stride", minimum=1)
    labels = _require_string_list(route["frame_labels"], f"{path}.frame_labels", unique=True)
    ticks_raw = route["target_ticks"]
    if not isinstance(ticks_raw, list) or len(ticks_raw) != len(labels):
        raise ReceiptValidationError(f"{path}.target_ticks must match frame_labels")
    ticks = [
        _require_int(tick, f"{path}.target_ticks[{index}]", minimum=0)
        for index, tick in enumerate(ticks_raw)
    ]
    if ticks != sorted(set(ticks)):
        raise ReceiptValidationError(f"{path}.target_ticks must be strictly increasing")
    if len(ticks) > 1:
        observed_steps = [right - left for left, right in zip(ticks, ticks[1:])]
        if any(step != route["tick_stride"] for step in observed_steps):
            raise ReceiptValidationError(f"{path}.target_ticks do not follow tick_stride")
    poses = route["poses"]
    if not isinstance(poses, list) or len(poses) != len(labels):
        raise ReceiptValidationError(f"{path}.poses must match frame_labels")
    for index, pose in enumerate(poses):
        _validate_pose(pose, f"{path}.poses[{index}]")
        if pose["label"] != labels[index] or pose["route_tick"] != ticks[index]:
            raise ReceiptValidationError(
                f"{path}.poses[{index}] label/tick must match the ordered route"
            )


def _validate_dimensions(value: Any, path: str) -> None:
    """Validate the exact requested, actual, and ANSI image dimensions."""

    dimensions = _require_object(value, path)
    _require_exact_keys(dimensions, DIMENSION_KEYS, path)
    for key in (
        "requested_width",
        "requested_height",
        "actual_width",
        "actual_height",
        "ansi_width",
        "ansi_height",
    ):
        _require_int(dimensions[key], f"{path}.{key}", minimum=1)
    if dimensions["actual_width"] < dimensions["ansi_width"]:
        raise ReceiptValidationError(f"{path}.actual_width is smaller than ansi_width")
    if dimensions["actual_height"] < dimensions["ansi_height"]:
        raise ReceiptValidationError(f"{path}.actual_height is smaller than ansi_height")
    if _require_bool(dimensions["fullscreen"], f"{path}.fullscreen") is not True:
        raise ReceiptValidationError(f"{path}.fullscreen must be true")
    if _require_bool(dimensions["ascii"], f"{path}.ascii") is not True:
        raise ReceiptValidationError(f"{path}.ascii must be true")


def _validate_time_profile(value: Any, path: str) -> None:
    """Validate deterministic day and night clocks used by both renderer arms."""

    profile = _require_object(value, path)
    _require_exact_keys(profile, TIME_PROFILE_KEYS, path)
    day = _require_number(profile["day_cycle_value"], f"{path}.day_cycle_value")
    night = _require_number(profile["night_cycle_value"], f"{path}.night_cycle_value")
    _require_number(profile["day_world_hour"], f"{path}.day_world_hour")
    _require_number(profile["night_world_hour"], f"{path}.night_world_hour")
    if not 0.0 <= day <= 1.0 or not 0.0 <= night <= 1.0:
        raise ReceiptValidationError(f"{path} normalized cycle values must be within [0, 1]")
    if day == night:
        raise ReceiptValidationError(f"{path} day and night cycle values must differ")


def _validate_server_identity(value: Any, path: str) -> None:
    """Validate the shared server generation that owned all captured gameplay."""

    identity = _require_object(value, path)
    _require_exact_keys(identity, SERVER_IDENTITY_KEYS, path)
    _require_full_hex(
        identity["server_commit"], f"{path}.server_commit", length=FULL_HEX_LENGTH
    )
    _require_absolute_directory(
        identity["server_source_root"], f"{path}.server_source_root"
    )
    _require_full_hex(
        identity["server_runtime_scope_fingerprint"],
        f"{path}.server_runtime_scope_fingerprint",
        length=SHA256_HEX_LENGTH,
    )
    _require_string(identity["server_endpoint"], f"{path}.server_endpoint")
    _require_string(
        identity["server_process_identity"], f"{path}.server_process_identity"
    )


def _validate_build_identity(value: Any, path: str) -> None:
    """Validate the installed Godot binary and Baseline import marker bytes."""

    identity = _require_object(value, path)
    _require_exact_keys(identity, BUILD_IDENTITY_KEYS, path)
    _require_string(identity["execution_kind"], f"{path}.execution_kind")
    requested = Path(
        _require_string(identity["godot_requested_path"], f"{path}.godot_requested_path")
    ).expanduser()
    if not requested.is_absolute() or not requested.exists():
        raise ReceiptValidationError(f"{path}.godot_requested_path is unavailable")
    _validate_path_ref(identity["godot_resolved_binary"], f"{path}.godot_resolved_binary")
    _require_string(identity["godot_version"], f"{path}.godot_version")
    _require_string(identity["godot_binary_format"], f"{path}.godot_binary_format")
    _require_full_hex(
        identity["imported_source_commit"],
        f"{path}.imported_source_commit",
        length=FULL_HEX_LENGTH,
    )
    _require_full_hex(
        identity["imported_source_tree"],
        f"{path}.imported_source_tree",
        length=FULL_HEX_LENGTH,
    )
    _validate_path_ref(identity["project_import_marker"], f"{path}.project_import_marker")


def _validate_source_frames(
    value: Any,
    path: str,
    *,
    capture_id: str,
    temporal_capture_id: str,
    route: Mapping[str, Any],
) -> None:
    """Validate all files and each frame's same-process truth attachment.

    A full package can launch several child Godot processes across different
    lanes.  The package-level capture id remains shared, while each frame row
    carries its own child-process identity.  The metadata plus runtime-truth
    JSON attached to that PNG must repeat the same identity.
    """

    if not isinstance(value, list) or not value:
        raise ReceiptValidationError(f"{path} must be a nonempty array")
    expected_labels = list(route["frame_labels"])
    expected_ticks = list(route["target_ticks"])
    route_lane = str(route["lane_id"])
    observed_route_labels: list[str] = []
    used_paths: set[str] = set()
    used_cells: set[tuple[str, str]] = set()
    for index, raw_frame in enumerate(value):
        frame_path = f"{path}[{index}]"
        frame = _require_object(raw_frame, frame_path)
        _require_exact_keys(frame, FRAME_KEYS, frame_path)
        lane_id = _require_string(frame["lane_id"], f"{frame_path}.lane_id")
        label = _require_string(frame["label"], f"{frame_path}.label")
        cell = (lane_id, label)
        if cell in used_cells:
            raise ReceiptValidationError(
                f"{frame_path} duplicates lane/label cell {lane_id}/{label}"
            )
        used_cells.add(cell)
        if frame["arm"] != "baseline":
            raise ReceiptValidationError(f"{frame_path}.arm must be baseline")
        tick = _require_int(frame["route_tick"], f"{frame_path}.route_tick", minimum=0)
        if frame["capture_id"] != capture_id:
            raise ReceiptValidationError(f"{frame_path}.capture_id mismatches capture")
        frame_temporal_id = _require_string(
            frame["temporal_capture_id"], f"{frame_path}.temporal_capture_id"
        )
        frame_process = _require_string(
            frame["process_identity"], f"{frame_path}.process_identity"
        )
        if lane_id == route_lane:
            route_index = len(observed_route_labels)
            if frame_temporal_id != temporal_capture_id:
                raise ReceiptValidationError(
                    f"{frame_path}.temporal_capture_id mismatches named route"
                )
            if route_index >= len(expected_ticks) or tick != expected_ticks[route_index]:
                raise ReceiptValidationError(f"{frame_path}.route_tick mismatches route")
            observed_route_labels.append(label)
        for evidence_key in ("png", "metadata", "runtime_truth"):
            evidence_path = _validate_path_ref(
                frame[evidence_key], f"{frame_path}.{evidence_key}"
            )
            canonical = str(evidence_path)
            if canonical in used_paths:
                raise ReceiptValidationError(
                    f"{frame_path}.{evidence_key} reuses another evidence file"
                )
            used_paths.add(canonical)
            if evidence_key in {"metadata", "runtime_truth"}:
                evidence_document = _load_json_object(
                    evidence_path,
                    label=f"{frame_path}.{evidence_key}",
                )
                if evidence_document.get("process_identity") != frame_process:
                    raise ReceiptValidationError(
                        f"{frame_path}.{evidence_key} process_identity "
                        "mismatches the PNG process owner"
                    )
                if evidence_document.get("capture_id") != capture_id:
                    raise ReceiptValidationError(
                        f"{frame_path}.{evidence_key} capture_id mismatches capture"
                    )
                if evidence_document.get("temporal_capture_id") != frame_temporal_id:
                    raise ReceiptValidationError(
                        f"{frame_path}.{evidence_key} temporal_capture_id "
                        "mismatches frame"
                    )
                if evidence_document.get("label") != label:
                    raise ReceiptValidationError(
                        f"{frame_path}.{evidence_key} label mismatches frame"
                    )
    if observed_route_labels != expected_labels:
        raise ReceiptValidationError(
            f"{path} named-route labels must exactly match route.frame_labels"
        )


def _validate_capture_input(capture: Any, path: str) -> dict[str, Any]:
    """Validate the normalized non-promotable capture receipt."""

    value = _require_object(capture, path)
    _require_exact_keys(value, CAPTURE_TOP_LEVEL_KEYS, path)
    if value["schema"] != CAPTURE_SCHEMA:
        raise ReceiptValidationError(f"{path}.schema must be {CAPTURE_SCHEMA}")
    if value["capture_classification"] != "non_promotable_diagnostic":
        raise ReceiptValidationError(
            f"{path}.capture_classification must be non_promotable_diagnostic"
        )
    capture_id = _require_string(value["capture_id"], f"{path}.capture_id")
    temporal_capture_id = _require_string(
        value["temporal_capture_id"], f"{path}.temporal_capture_id"
    )
    process_identity = _require_string(
        value["process_identity"], f"{path}.process_identity"
    )
    _require_timestamp(value["created_at_utc"], f"{path}.created_at_utc")
    _require_full_hex(
        value["baseline_source_commit"],
        f"{path}.baseline_source_commit",
        length=FULL_HEX_LENGTH,
    )
    _require_full_hex(
        value["baseline_source_tree"],
        f"{path}.baseline_source_tree",
        length=FULL_HEX_LENGTH,
    )
    _require_full_hex(
        value["proof_tool_commit"],
        f"{path}.proof_tool_commit",
        length=FULL_HEX_LENGTH,
    )
    _require_absolute_regular_file(
        value["lane_manifest_path"], f"{path}.lane_manifest_path"
    )
    _require_full_hex(
        value["lane_manifest_sha256"],
        f"{path}.lane_manifest_sha256",
        length=SHA256_HEX_LENGTH,
    )

    arguments = _require_object(value["argument_vectors"], f"{path}.argument_vectors")
    _require_exact_keys(arguments, ARGUMENT_VECTOR_KEYS, f"{path}.argument_vectors")
    _require_string_list(arguments["day"], f"{path}.argument_vectors.day")
    _require_string_list(arguments["night"], f"{path}.argument_vectors.night")
    _validate_route(value["route"], f"{path}.route")
    _validate_dimensions(value["dimensions"], f"{path}.dimensions")
    _validate_time_profile(value["time_profile"], f"{path}.time_profile")
    _validate_server_identity(value["server_identity"], f"{path}.server_identity")
    _validate_build_identity(
        value["baseline_build_identity"], f"{path}.baseline_build_identity"
    )
    _require_string_list(value["features"], f"{path}.features", unique=True)
    _validate_source_frames(
        value["source_frames"],
        f"{path}.source_frames",
        capture_id=capture_id,
        temporal_capture_id=temporal_capture_id,
        route=value["route"],
    )
    return value


def _capture_contract(capture: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only the capture facts the successor receipt owns directly."""

    return {
        "capture_id": capture["capture_id"],
        "temporal_capture_id": capture["temporal_capture_id"],
        "process_identity": capture["process_identity"],
        "argument_vectors": deepcopy(capture["argument_vectors"]),
        "route": deepcopy(capture["route"]),
        "dimensions": deepcopy(capture["dimensions"]),
        "time_profile": deepcopy(capture["time_profile"]),
        "server_identity": deepcopy(capture["server_identity"]),
        "features": deepcopy(capture["features"]),
    }


def _validate_no_unavailable(value: Any, path: str = "receipt") -> None:
    """Reject audit placeholders before an object can become designated evidence."""

    if isinstance(value, dict):
        for key, child in value.items():
            _validate_no_unavailable(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_no_unavailable(child, f"{path}[{index}]")
    elif isinstance(value, str) and value.strip().upper() == "NOT AVAILABLE":
        raise ReceiptValidationError(f"{path} still contains NOT AVAILABLE")


def _source_identity(source_root: Path) -> dict[str, Any]:
    """Derive the clean Baseline source checkout identity from Git and disk."""

    root = _require_absolute_directory(str(source_root), "source_root")
    commit = _require_full_hex(
        _run_git(root, "rev-parse", "HEAD"),
        "source_root HEAD",
        length=FULL_HEX_LENGTH,
    )
    tree = _require_full_hex(
        _run_git(root, "rev-parse", "HEAD^{tree}"),
        "source_root tree",
        length=FULL_HEX_LENGTH,
    )
    object_format = _require_string(
        _run_git(root, "rev-parse", "--show-object-format"),
        "source_root object format",
    )
    dirty = _git_status(root)
    if dirty:
        raise ReceiptValidationError(
            "Baseline source checkout must be clean: " + "; ".join(dirty)
        )
    project_relative = "godot_project/project.godot"
    return {
        "root": str(root),
        "commit": commit,
        "tree": tree,
        "git_object_format": object_format,
        "clean_required": True,
        "clean_observed": True,
        "project_subdir": "godot_project",
        "project_file": _git_file_ref(root, commit, project_relative),
    }


def _proof_identity(
    proof_root: Path,
    lane_manifest: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Derive committed proof-tool files and the exact manifest attachment."""

    root = _require_absolute_directory(str(proof_root), "proof_tool_root")
    commit = _require_full_hex(
        _run_git(root, "rev-parse", "HEAD"),
        "proof_tool_root HEAD",
        length=FULL_HEX_LENGTH,
    )
    tree = _require_full_hex(
        _run_git(root, "rev-parse", "HEAD^{tree}"),
        "proof_tool_root tree",
        length=FULL_HEX_LENGTH,
    )
    object_format = _require_string(
        _run_git(root, "rev-parse", "--show-object-format"),
        "proof_tool_root object format",
    )

    manifest_relative = _relative_repo_path(root, lane_manifest, "lane_manifest")
    scoped_paths = [
        *REQUIRED_TOOL_PATHS.values(),
        *REQUIRED_ANALYZER_PATHS.values(),
        manifest_relative,
    ]
    if len(set(scoped_paths)) != len(scoped_paths):
        raise ReceiptValidationError("proof-tool scoped paths must be unique")
    dirty = _git_status(root, scoped_paths)
    if dirty:
        raise ReceiptValidationError(
            "proof-tool scoped paths must be clean: " + "; ".join(dirty)
        )

    tools: dict[str, Any] = {
        role: _git_file_ref(root, commit, relative)
        for role, relative in REQUIRED_TOOL_PATHS.items()
    }
    tools["analyzers"] = {
        role: _git_file_ref(root, commit, relative)
        for role, relative in REQUIRED_ANALYZER_PATHS.items()
    }
    source = {
        "root": str(root),
        "commit": commit,
        "tree": tree,
        "git_object_format": object_format,
        "scoped_clean_required": True,
        "scoped_clean_observed": True,
        "scoped_paths": scoped_paths,
    }
    manifest = _git_file_ref(root, commit, manifest_relative)
    return source, tools, manifest


def create_receipt(
    *,
    output_path: Path,
    designation_record_path: Path,
    capture_receipt_path: Path,
    lane_manifest_path: Path,
    source_root: Path,
    proof_tool_root: Path,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Create one complete v2 receipt without overwriting any existing identity."""

    output = output_path.expanduser()
    if not output.is_absolute():
        raise ReceiptValidationError("output path must be absolute")
    output = output.absolute()
    if output.exists():
        raise ReceiptValidationError(f"refusing to overwrite existing receipt: {output}")

    designation_path = _require_absolute_regular_file(
        str(designation_record_path), "designation_record"
    )
    designation = _validate_designation(
        _load_json_object(designation_path, label="designation record"),
        "designation",
    )
    if Path(designation["successor_receipt_path"]).expanduser().absolute() != output:
        raise ReceiptValidationError(
            "designation successor_receipt_path differs from requested output"
        )
    predecessor = _require_absolute_regular_file(
        designation["predecessor_receipt_path"],
        "designation.predecessor_receipt_path",
    )
    predecessor_sha = _file_sha256(predecessor)
    if predecessor_sha != designation["predecessor_receipt_sha256"]:
        raise ReceiptValidationError(
            "designation predecessor receipt SHA-256 does not match disk"
        )
    _validate_predecessor_v1(predecessor, "designation predecessor receipt")

    capture_path = _require_absolute_regular_file(
        str(capture_receipt_path), "capture_receipt"
    )
    capture = _validate_capture_input(
        _load_json_object(capture_path, label="capture receipt"),
        "capture_input_document",
    )
    manifest_path = _require_absolute_regular_file(
        str(lane_manifest_path), "lane_manifest"
    )
    manifest_sha = _file_sha256(manifest_path)
    if Path(capture["lane_manifest_path"]).resolve() != manifest_path:
        raise ReceiptValidationError("capture receipt names a different lane manifest path")
    if capture["lane_manifest_sha256"] != manifest_sha:
        raise ReceiptValidationError("capture receipt lane manifest SHA-256 mismatches disk")

    baseline_source = _source_identity(source_root)
    if capture["baseline_source_commit"] != baseline_source["commit"]:
        raise ReceiptValidationError("capture receipt Baseline source commit mismatches disk")
    if capture["baseline_source_tree"] != baseline_source["tree"]:
        raise ReceiptValidationError("capture receipt Baseline source tree mismatches disk")

    proof_source, proof_tools, lane_manifest = _proof_identity(
        proof_tool_root,
        manifest_path,
    )
    if capture["proof_tool_commit"] != proof_source["commit"]:
        raise ReceiptValidationError("capture receipt proof-tool commit mismatches disk")

    build_identity = deepcopy(capture["baseline_build_identity"])
    if build_identity["imported_source_commit"] != baseline_source["commit"]:
        raise ReceiptValidationError("Baseline build imported_source_commit mismatches source")
    if build_identity["imported_source_tree"] != baseline_source["tree"]:
        raise ReceiptValidationError("Baseline build imported_source_tree mismatches source")

    created_at = (
        created_at_utc
        if created_at_utc is not None
        else datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    _require_timestamp(created_at, "created_at_utc")
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "receipt_version": 2,
        "title": "BASELINE A FL-4431 operator-designated immutable successor",
        "failure_log_refs": ["FL-4431", "FL-4408", "FL-4519"],
        "created_at_utc": created_at,
        "designation_record": _path_ref(designation_path),
        "designation": deepcopy(designation),
        "predecessor_receipt": _path_ref(predecessor),
        "baseline_source": baseline_source,
        "proof_tool_source": proof_source,
        "proof_tools": proof_tools,
        "lane_manifest": lane_manifest,
        "capture_input": _path_ref(capture_path),
        "capture_contract": _capture_contract(capture),
        "baseline_build_identity": build_identity,
        "source_frames": deepcopy(capture["source_frames"]),
    }
    receipt["self_hash"] = {
        "algorithm": SELF_HASH_ALGORITHM,
        "canonicalization": SELF_HASH_CANONICALIZATION,
        "excluded_field": "self_hash",
        "digest": _receipt_digest(receipt),
    }

    # Validate the in-memory object before creating the caller's immutable path.
    validate_receipt_object(receipt)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(receipt, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        # A hard link creates the final name only when that name is absent.  It
        # therefore preserves the "never overwrite a designated receipt" law
        # even if another process creates the caller's pathname after the first
        # existence check but before this publication step.
        os.link(temporary, output)
    except FileExistsError as exc:
        raise ReceiptValidationError(
            f"refusing to overwrite existing receipt: {output}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def _validate_baseline_source(value: Any, path: str) -> dict[str, Any]:
    """Recheck the immutable Baseline checkout and project-file identity."""

    source = _require_object(value, path)
    _require_exact_keys(source, BASELINE_SOURCE_KEYS, path)
    root = _require_absolute_directory(source["root"], f"{path}.root")
    commit = _require_full_hex(source["commit"], f"{path}.commit", length=FULL_HEX_LENGTH)
    tree = _require_full_hex(source["tree"], f"{path}.tree", length=FULL_HEX_LENGTH)
    object_format = _require_string(
        source["git_object_format"], f"{path}.git_object_format"
    )
    if _run_git(root, "rev-parse", "HEAD") != commit:
        raise ReceiptValidationError(f"{path}.commit mismatches live HEAD")
    if _run_git(root, "rev-parse", "HEAD^{tree}") != tree:
        raise ReceiptValidationError(f"{path}.tree mismatches live tree")
    if _run_git(root, "rev-parse", "--show-object-format") != object_format:
        raise ReceiptValidationError(f"{path}.git_object_format mismatches Git")
    if _require_bool(source["clean_required"], f"{path}.clean_required") is not True:
        raise ReceiptValidationError(f"{path}.clean_required must be true")
    if _require_bool(source["clean_observed"], f"{path}.clean_observed") is not True:
        raise ReceiptValidationError(f"{path}.clean_observed must be true")
    dirty = _git_status(root)
    if dirty:
        raise ReceiptValidationError(f"{path} checkout is dirty: {'; '.join(dirty)}")
    if source["project_subdir"] != "godot_project":
        raise ReceiptValidationError(f"{path}.project_subdir must be godot_project")
    _validate_git_file_ref(
        source["project_file"],
        f"{path}.project_file",
        root=root,
        commit=commit,
        required_relative_path="godot_project/project.godot",
    )
    return source


def _validate_proof_tools(
    source_value: Any,
    tools_value: Any,
    manifest_value: Any,
) -> tuple[dict[str, Any], Path]:
    """Recheck clean proof-tool bytes, analyzers, and the executable manifest."""

    source = _require_object(source_value, "receipt.proof_tool_source")
    _require_exact_keys(source, PROOF_SOURCE_KEYS, "receipt.proof_tool_source")
    root = _require_absolute_directory(source["root"], "receipt.proof_tool_source.root")
    commit = _require_full_hex(
        source["commit"],
        "receipt.proof_tool_source.commit",
        length=FULL_HEX_LENGTH,
    )
    tree = _require_full_hex(
        source["tree"],
        "receipt.proof_tool_source.tree",
        length=FULL_HEX_LENGTH,
    )
    object_format = _require_string(
        source["git_object_format"],
        "receipt.proof_tool_source.git_object_format",
    )
    if _run_git(root, "rev-parse", "HEAD") != commit:
        raise ReceiptValidationError("receipt.proof_tool_source.commit mismatches live HEAD")
    if _run_git(root, "rev-parse", "HEAD^{tree}") != tree:
        raise ReceiptValidationError("receipt.proof_tool_source.tree mismatches live tree")
    if _run_git(root, "rev-parse", "--show-object-format") != object_format:
        raise ReceiptValidationError(
            "receipt.proof_tool_source.git_object_format mismatches Git"
        )
    if (
        _require_bool(
            source["scoped_clean_required"],
            "receipt.proof_tool_source.scoped_clean_required",
        )
        is not True
    ):
        raise ReceiptValidationError(
            "receipt.proof_tool_source.scoped_clean_required must be true"
        )
    if (
        _require_bool(
            source["scoped_clean_observed"],
            "receipt.proof_tool_source.scoped_clean_observed",
        )
        is not True
    ):
        raise ReceiptValidationError(
            "receipt.proof_tool_source.scoped_clean_observed must be true"
        )
    scoped_paths = _require_string_list(
        source["scoped_paths"],
        "receipt.proof_tool_source.scoped_paths",
        unique=True,
    )

    tools = _require_object(tools_value, "receipt.proof_tools")
    _require_exact_keys(tools, PROOF_TOOLS_KEYS, "receipt.proof_tools")
    for role, relative in REQUIRED_TOOL_PATHS.items():
        _validate_git_file_ref(
            tools[role],
            f"receipt.proof_tools.{role}",
            root=root,
            commit=commit,
            required_relative_path=relative,
        )
    analyzers = _require_object(tools["analyzers"], "receipt.proof_tools.analyzers")
    _require_exact_keys(
        analyzers,
        set(REQUIRED_ANALYZER_PATHS),
        "receipt.proof_tools.analyzers",
    )
    for role, relative in REQUIRED_ANALYZER_PATHS.items():
        _validate_git_file_ref(
            analyzers[role],
            f"receipt.proof_tools.analyzers.{role}",
            root=root,
            commit=commit,
            required_relative_path=relative,
        )

    manifest_record = _require_object(manifest_value, "receipt.lane_manifest")
    _require_exact_keys(
        manifest_record,
        GIT_FILE_REF_KEYS,
        "receipt.lane_manifest",
    )
    manifest_path = _validate_path_ref(
        {key: manifest_record[key] for key in PATH_REF_KEYS},
        "receipt.lane_manifest",
    )
    manifest_relative = _relative_repo_path(root, manifest_path, "receipt.lane_manifest")
    _validate_git_file_ref(
        manifest_record,
        "receipt.lane_manifest",
        root=root,
        commit=commit,
        required_relative_path=manifest_relative,
    )
    expected_scoped_paths = [
        *REQUIRED_TOOL_PATHS.values(),
        *REQUIRED_ANALYZER_PATHS.values(),
        manifest_relative,
    ]
    if scoped_paths != expected_scoped_paths:
        raise ReceiptValidationError(
            "receipt.proof_tool_source.scoped_paths does not match the closed tool set"
        )
    dirty = _git_status(root, scoped_paths)
    if dirty:
        raise ReceiptValidationError(
            "receipt.proof_tool_source scoped files are dirty: " + "; ".join(dirty)
        )
    return source, manifest_path


def validate_receipt_object(receipt: Any) -> dict[str, Any]:
    """Validate one already-parsed successor receipt against live disk state."""

    value = _require_object(receipt, "receipt")
    _require_exact_keys(value, TOP_LEVEL_KEYS, "receipt")
    if value["schema"] != SCHEMA:
        raise ReceiptValidationError(f"receipt.schema must be {SCHEMA}")
    if value["receipt_version"] != 2:
        raise ReceiptValidationError("receipt.receipt_version must be 2")
    title = _require_string(value["title"], "receipt.title")
    if not title.startswith("BASELINE A FL-4431"):
        raise ReceiptValidationError("receipt.title must begin with BASELINE A FL-4431")
    if value["failure_log_refs"] != ["FL-4431", "FL-4408", "FL-4519"]:
        raise ReceiptValidationError("receipt.failure_log_refs must match the closed order")
    _require_timestamp(value["created_at_utc"], "receipt.created_at_utc")

    self_hash = _require_object(value["self_hash"], "receipt.self_hash")
    _require_exact_keys(self_hash, SELF_HASH_KEYS, "receipt.self_hash")
    if self_hash["algorithm"] != SELF_HASH_ALGORITHM:
        raise ReceiptValidationError("receipt.self_hash.algorithm must be sha256")
    if self_hash["canonicalization"] != SELF_HASH_CANONICALIZATION:
        raise ReceiptValidationError("receipt.self_hash.canonicalization mismatch")
    if self_hash["excluded_field"] != "self_hash":
        raise ReceiptValidationError("receipt.self_hash.excluded_field must be self_hash")
    expected_self_digest = _require_full_hex(
        self_hash["digest"],
        "receipt.self_hash.digest",
        length=SHA256_HEX_LENGTH,
    )
    observed_self_digest = _receipt_digest(value)
    if observed_self_digest != expected_self_digest:
        raise ReceiptValidationError(
            "receipt.self_hash.digest mismatch: "
            f"expected {expected_self_digest}, observed {observed_self_digest}"
        )

    designation_path = _validate_path_ref(
        value["designation_record"], "receipt.designation_record"
    )
    designation = _validate_designation(value["designation"], "receipt.designation")
    attached_designation = _validate_designation(
        _load_json_object(designation_path, label="designation record"),
        "designation_record_document",
    )
    if attached_designation != designation:
        raise ReceiptValidationError("receipt.designation differs from designation record")
    predecessor_path = _validate_path_ref(
        value["predecessor_receipt"], "receipt.predecessor_receipt"
    )
    _validate_predecessor_v1(predecessor_path, "receipt.predecessor_receipt")
    if predecessor_path != Path(designation["predecessor_receipt_path"]).resolve():
        raise ReceiptValidationError("receipt.predecessor_receipt path mismatches designation")
    if value["predecessor_receipt"]["sha256"] != designation["predecessor_receipt_sha256"]:
        raise ReceiptValidationError("receipt.predecessor_receipt SHA mismatches designation")

    baseline_source = _validate_baseline_source(
        value["baseline_source"], "receipt.baseline_source"
    )
    proof_source, manifest_path = _validate_proof_tools(
        value["proof_tool_source"],
        value["proof_tools"],
        value["lane_manifest"],
    )
    capture_path = _validate_path_ref(value["capture_input"], "receipt.capture_input")
    capture = _validate_capture_input(
        _load_json_object(capture_path, label="capture input"),
        "capture_input_document",
    )
    if capture["baseline_source_commit"] != baseline_source["commit"]:
        raise ReceiptValidationError("capture Baseline commit mismatches receipt")
    if capture["baseline_source_tree"] != baseline_source["tree"]:
        raise ReceiptValidationError("capture Baseline tree mismatches receipt")
    if capture["proof_tool_commit"] != proof_source["commit"]:
        raise ReceiptValidationError("capture proof-tool commit mismatches receipt")
    if Path(capture["lane_manifest_path"]).resolve() != manifest_path:
        raise ReceiptValidationError("capture lane manifest path mismatches receipt")
    if capture["lane_manifest_sha256"] != value["lane_manifest"]["sha256"]:
        raise ReceiptValidationError("capture lane manifest SHA mismatches receipt")

    contract = _require_object(value["capture_contract"], "receipt.capture_contract")
    _require_exact_keys(contract, CAPTURE_CONTRACT_KEYS, "receipt.capture_contract")
    expected_contract = _capture_contract(capture)
    if contract != expected_contract:
        raise ReceiptValidationError("receipt.capture_contract differs from capture input")
    _validate_route(contract["route"], "receipt.capture_contract.route")
    _validate_dimensions(contract["dimensions"], "receipt.capture_contract.dimensions")
    _validate_time_profile(
        contract["time_profile"], "receipt.capture_contract.time_profile"
    )
    _validate_server_identity(
        contract["server_identity"], "receipt.capture_contract.server_identity"
    )

    _validate_build_identity(
        value["baseline_build_identity"], "receipt.baseline_build_identity"
    )
    if value["baseline_build_identity"] != capture["baseline_build_identity"]:
        raise ReceiptValidationError(
            "receipt.baseline_build_identity differs from capture input"
        )
    if value["baseline_build_identity"]["imported_source_commit"] != baseline_source["commit"]:
        raise ReceiptValidationError("Baseline build commit mismatches Baseline source")
    if value["baseline_build_identity"]["imported_source_tree"] != baseline_source["tree"]:
        raise ReceiptValidationError("Baseline build tree mismatches Baseline source")

    if value["source_frames"] != capture["source_frames"]:
        raise ReceiptValidationError("receipt.source_frames differs from capture input")
    _validate_source_frames(
        value["source_frames"],
        "receipt.source_frames",
        capture_id=contract["capture_id"],
        temporal_capture_id=contract["temporal_capture_id"],
        route=contract["route"],
    )
    _validate_no_unavailable(value)
    return value


def validate_receipt(path: Path) -> dict[str, Any]:
    """Load and validate one receipt path without changing any file."""

    receipt_path = _require_absolute_regular_file(str(path), "receipt_path")
    receipt = _load_json_object(receipt_path, label="Baseline A v2 receipt")
    validated = validate_receipt_object(receipt)
    designation_path = (
        Path(validated["designation"]["successor_receipt_path"])
        .expanduser()
        .absolute()
    )
    if designation_path != receipt_path:
        raise ReceiptValidationError(
            "receipt path differs from designation.successor_receipt_path"
        )
    return validated


def _build_parser() -> argparse.ArgumentParser:
    """Build the two-command interface used by capture automation and operators."""

    parser = argparse.ArgumentParser(
        description="Create plus validate the immutable FL-4431 Baseline A v2 receipt."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    create_parser = subcommands.add_parser(
        "create",
        help="Create a new receipt from one non-promotable capture input.",
    )
    create_parser.add_argument("--output", required=True, type=Path)
    create_parser.add_argument("--designation-record", required=True, type=Path)
    create_parser.add_argument("--capture-receipt", required=True, type=Path)
    create_parser.add_argument("--lane-manifest", required=True, type=Path)
    create_parser.add_argument("--source-root", required=True, type=Path)
    create_parser.add_argument("--proof-tool-root", required=True, type=Path)
    create_parser.add_argument(
        "--created-at-utc",
        default=None,
        help="Optional deterministic ISO-8601 creation time; defaults to current UTC.",
    )

    validate_parser = subcommands.add_parser(
        "validate",
        help="Validate an existing v2 receipt and every attached file.",
    )
    validate_parser.add_argument("receipt", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI command and return a stable process status for automation."""

    args = _build_parser().parse_args(argv)
    try:
        if args.command == "create":
            receipt = create_receipt(
                output_path=args.output,
                designation_record_path=args.designation_record,
                capture_receipt_path=args.capture_receipt,
                lane_manifest_path=args.lane_manifest,
                source_root=args.source_root,
                proof_tool_root=args.proof_tool_root,
                created_at_utc=args.created_at_utc,
            )
            print(
                "FL4431_BASELINE_RECEIPT_V2_CREATED "
                f"path={args.output.resolve()} digest={receipt['self_hash']['digest']}"
            )
            return 0
        receipt = validate_receipt(args.receipt)
        print(
            "FL4431_BASELINE_RECEIPT_V2_VALID "
            f"path={args.receipt.resolve()} digest={receipt['self_hash']['digest']}"
        )
        return 0
    except ReceiptValidationError as exc:
        print(f"FL4431_BASELINE_RECEIPT_V2_REJECTED {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
