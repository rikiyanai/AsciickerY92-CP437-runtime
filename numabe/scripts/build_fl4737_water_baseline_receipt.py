#!/usr/bin/env python3
"""Create and validate the immutable FL-4737 water Baseline A receipt.

This is the sole owner of ``asciicker.godot_water_baseline.v1``.  It consumes
one committed operator designation plus one non-promotable capture manifest.
Neither input can name Baseline A by itself.  ``create`` validates every source
fact and artifact before exclusively creating a content-addressed receipt;
``validate`` rechecks the closed schema, live files, Git identities, and
canonical self-hash without mutating anything.

The FL-4431 airplane receipt is deliberately not imported or widened here.
FL-4737 has different frame roles, authority records, and rejection laws.
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
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA = "asciicker.godot_water_baseline.v1"
DESIGNATION_SCHEMA = "asciicker.godot_water_baseline_designation.v1"
CAPTURE_MANIFEST_SCHEMA = "asciicker.godot_water_baseline_capture_manifest.v1"
BASELINE_NAME = "Baseline A"
SELECTED_SOURCE_COMMIT = "6616e12c17895bb3725c70c5daa080b1b3063f99"
SELECTED_SOURCE_TREE = "552f2f9955147aef594d3fad8b457f32e698c758"
PROOF_TOOL_PATH = "scripts/build_fl4737_water_baseline_receipt.py"
DESIGNATION_ROOT = "docs/research/ascii/verification/fl4737/baseline-designations"
RECEIPT_ROOT = "docs/research/ascii/verification/fl4737/baseline-receipts"
SELF_HASH_CANONICALIZATION = "utf8-json-sort-keys-compact-with-receipt_sha256-excluded"

FRAME_ROLES = (
    "p1_deep_reflection_perspective",
    "p1_deep_reflection_orthographic",
    "shallow_contact_perspective",
    "shallow_contact_orthographic",
    "p2_night_suppression_perspective",
    "p2_night_suppression_orthographic",
)
ROLE_PROJECTION = {
    role: ("orthographic" if role.endswith("orthographic") else "perspective")
    for role in FRAME_ROLES
}
ARTIFACT_ROLES = (
    "viewport_png",
    "render_metadata_json",
    "analysis_summary_json",
    "final_cells_jsonl",
    "dump_html",
    "runner_receipt_json",
    "runner_log",
)
BANNED_AUTHORITY_TOKENS = (
    "13205b307",
    "fl4737_baseline_candidate",
    "fd6f16266",
    "checkpoint",
    "diagnostic",
    "historical",
    "build_fl4431_baseline_receipt_v2.py",
)
FUTURE_RECEIPT_TOKENS = (
    RECEIPT_ROOT,
    "receipt_sha256",
    SCHEMA,
)

# FL-4739 reuses the permanent FL-4722 locks rather than accepting rounded
# coordinates or a nearby camera as the same proof surface.  Projection-only
# fields are deliberately outside these tuples because each lock has one
# perspective frame and one orthographic frame.
LOCKED_ROLE_POSES = {
    "p1_deep_reflection": {
        "player_position": [127.110328674316, 0.0, -3.92257308959961],
        "camera_position": [124.412208557129, 19.5625, 28.9669418334961],
        "camera_yaw": -0.0818525847650367,
        "camera_pitch": -0.463647669653925,
    },
    "p2_night_suppression": {
        "player_position": [290.134185791016, 0.0, 43.566234588623],
        "camera_position": [267.390045166016, 19.5625, 19.6559104919434],
        "camera_yaw": -2.38118544577727,
        "camera_pitch": -0.463647736293947,
    },
}

TOP_LEVEL_KEYS = {
    "schema",
    "baseline_name",
    "failure_log_refs",
    "designation_record",
    "capture_manifest",
    "transport_witness",
    "baseline_source",
    "proof_tool",
    "build_identity",
    "launch_configuration",
    "predecessor_receipt_sha256",
    "frame_bundles",
    "receipt_sha256",
}
FILE_REF_KEYS = {"path", "sha256", "size_bytes"}
GIT_FILE_REF_KEYS = FILE_REF_KEYS | {"git_blob"}
BASELINE_SOURCE_KEYS = {"commit", "tree", "clean_required"}
PROOF_TOOL_KEYS = {
    "root",
    "commit",
    "tree",
    "script",
    "scoped_clean_required",
    "scoped_clean_observed",
}
DESIGNATION_KEYS = {
    "schema",
    "baseline_name",
    "receipt_schema",
    "source_commit",
    "source_tree",
    "operator_identity",
    "designated_at_utc",
    "statement",
    "capture_manifest_sha256",
    "frames",
    "predecessor_receipt_sha256",
}
DESIGNATED_FRAME_KEYS = {"role", "projection", "pose_sha256"}
MANIFEST_KEYS = {
    "schema",
    "capture_id",
    "baseline_source",
    "transport_witness",
    "build_identity",
    "launch_configuration",
    "frame_bundles",
}
BUILD_IDENTITY_KEYS = {
    "godot_version",
    "godot_binary",
    "project_build_manifest",
    "capture_tool_commit",
    "capture_tool_tree",
    "capture_tool",
}
LAUNCH_CONFIGURATION_KEYS = {
    "argument_vector",
    "environment",
    "renderer",
    "feature_flags",
}
FRAME_BUNDLE_KEYS = {
    "role",
    "pose",
    "wrapper_identity",
    "runtime_identity",
    "run_identity",
    "artifacts",
}
POSE_KEYS = {
    "player_position",
    "player_yaw",
    "camera_position",
    "camera_yaw",
    "camera_pitch",
    "camera_roll",
    "projection",
    "fov_degrees",
    "orthographic_size",
    "day_cycle_value",
    "weather_kind",
    "weather_intensity",
    "viewport_width",
    "viewport_height",
}
SOURCE_IDENTITY_KEYS = {"commit", "tree", "dirty"}
RUN_IDENTITY_KEYS = {"capture_id", "process_id"}


class ReceiptValidationError(RuntimeError):
    """Raised when the declared baseline authority cannot be proven."""


def _closed_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReceiptValidationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _receipt_digest(receipt: Mapping[str, Any]) -> str:
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    return _sha256_bytes(_canonical_json_bytes(unsigned))


def _pose_digest(pose: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json_bytes(pose))


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReceiptValidationError(f"{field} must be an object")
    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReceiptValidationError(f"{field} must be an array")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        unknown = sorted(observed - expected)
        raise ReceiptValidationError(
            f"{field} keys mismatch missing={missing} unknown={unknown}"
        )


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReceiptValidationError(f"{field} must be a non-empty string")
    return value


def _require_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ReceiptValidationError(f"{field} must be boolean")
    return value


def _require_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReceiptValidationError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ReceiptValidationError(f"{field} must be finite")
    return result


def _require_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReceiptValidationError(f"{field} must be a positive integer")
    return value


def _require_hex(value: Any, field: str, length: int) -> str:
    text = _require_string(value, field)
    if len(text) != length or any(ch not in "0123456789abcdef" for ch in text):
        raise ReceiptValidationError(f"{field} must be {length} lowercase hex characters")
    return text


def _require_optional_digest(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _require_hex(value, field, 64)


def _require_utc_timestamp(value: Any, field: str) -> str:
    text = _require_string(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReceiptValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReceiptValidationError(f"{field} must include a UTC offset")
    if parsed.utcoffset().total_seconds() != 0:
        raise ReceiptValidationError(f"{field} must be UTC")
    return text


def _load_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_closed_json_object
        )
    except FileNotFoundError as exc:
        raise ReceiptValidationError(f"{field} does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptValidationError(f"{field} is not valid UTF-8 JSON: {path}: {exc}") from exc
    return _require_object(value, field)


def _load_json_bytes(value: bytes, field: str) -> dict[str, Any]:
    try:
        decoded = value.decode("utf-8")
        parsed = json.loads(decoded, object_pairs_hook=_closed_json_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptValidationError(f"{field} is not valid UTF-8 JSON: {exc}") from exc
    return _require_object(parsed, field)


def _regular_file(path_value: Any, field: str) -> Path:
    unresolved = Path(_require_string(path_value, f"{field}.path")).expanduser()
    if unresolved.is_symlink():
        raise ReceiptValidationError(f"{field}.path must not be a symbolic link")
    path = unresolved.resolve()
    if not path.is_file():
        raise ReceiptValidationError(f"{field}.path is not a regular file: {path}")
    return path


def _path_ref(path: Path) -> dict[str, Any]:
    unresolved = path.expanduser()
    if unresolved.is_symlink():
        raise ReceiptValidationError(f"evidence file must not be a symbolic link: {unresolved}")
    resolved = unresolved.resolve()
    if not resolved.is_file():
        raise ReceiptValidationError(f"evidence file does not exist: {resolved}")
    return {
        "path": str(resolved),
        "sha256": _file_sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _validate_file_ref(value: Any, field: str) -> Path:
    ref = _require_object(value, field)
    _require_exact_keys(ref, FILE_REF_KEYS, field)
    path = _regular_file(ref["path"], field)
    _reject_banned_authority_text(str(path), f"{field}.path")
    expected_sha = _require_hex(ref["sha256"], f"{field}.sha256", 64)
    expected_size = _require_positive_int(ref["size_bytes"], f"{field}.size_bytes")
    observed_size = path.stat().st_size
    if observed_size != expected_size:
        raise ReceiptValidationError(
            f"{field}.size_bytes mismatch expected={expected_size} observed={observed_size}"
        )
    observed_sha = _file_sha256(path)
    if observed_sha != expected_sha:
        raise ReceiptValidationError(
            f"{field}.sha256 mismatch expected={expected_sha} observed={observed_sha}"
        )
    return path


def _reject_banned_authority_text(value: str, field: str) -> None:
    """Reject only identity-bearing fields, never explanatory operator prose."""

    lowered = value.lower()
    for banned in BANNED_AUTHORITY_TOKENS:
        if banned in lowered:
            raise ReceiptValidationError(
                f"{field} contains banned authority token: {banned}"
            )


def _reject_future_receipt_references(value: Any, field: str) -> None:
    """Keep the source-bound capture manifest one-way toward its future receipt."""

    if isinstance(value, dict):
        for key, item in value.items():
            _reject_future_receipt_references(item, f"{field}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_future_receipt_references(item, f"{field}[{index}]")
        return
    if not isinstance(value, str):
        return
    lowered = value.lower()
    for token in FUTURE_RECEIPT_TOKENS:
        if token.lower() in lowered:
            raise ReceiptValidationError(
                f"{field} illegally references the future baseline receipt: {token}"
            )


def _run_git(root: Path, args: Sequence[str], field: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise ReceiptValidationError(f"{field} Git query failed: {detail}")
    return process.stdout.strip()


def _run_git_bytes(root: Path, args: Sequence[str], field: str) -> bytes:
    process = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace").strip()
        raise ReceiptValidationError(f"{field} Git query failed: {detail}")
    return process.stdout


def _validate_selected_source(root: Path, value: Any) -> dict[str, Any]:
    source = _require_object(value, "baseline_source")
    _require_exact_keys(source, BASELINE_SOURCE_KEYS, "baseline_source")
    commit = _require_hex(source["commit"], "baseline_source.commit", 40)
    tree = _require_hex(source["tree"], "baseline_source.tree", 40)
    if commit != SELECTED_SOURCE_COMMIT or tree != SELECTED_SOURCE_TREE:
        raise ReceiptValidationError("baseline_source is not the operator-selected 6616 commit/tree")
    if _require_bool(source["clean_required"], "baseline_source.clean_required") is not True:
        raise ReceiptValidationError("baseline_source.clean_required must be true")
    observed_tree = _run_git(root, ["rev-parse", f"{commit}^{{tree}}"], "baseline_source")
    if observed_tree != tree:
        raise ReceiptValidationError(
            f"baseline source tree mismatch expected={tree} observed={observed_tree}"
        )
    return source


def _relative_repo_path(root: Path, path: Path, field: str) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ReceiptValidationError(f"{field} must live under proof_tool.root") from exc


def _proof_tool_record(root_value: Path) -> dict[str, Any]:
    root = root_value.expanduser().resolve()
    commit = _require_hex(_run_git(root, ["rev-parse", "HEAD"], "proof_tool"), "proof_tool.commit", 40)
    tree = _require_hex(_run_git(root, ["rev-parse", "HEAD^{tree}"], "proof_tool"), "proof_tool.tree", 40)
    script_path = root / PROOF_TOOL_PATH
    if not script_path.is_file():
        raise ReceiptValidationError(f"proof tool missing: {script_path}")
    dirty = _run_git(root, ["status", "--porcelain", "--", PROOF_TOOL_PATH], "proof_tool")
    if dirty:
        raise ReceiptValidationError(f"proof tool is dirty or untracked: {dirty}")
    blob = _require_hex(
        _run_git(root, ["rev-parse", f"HEAD:{PROOF_TOOL_PATH}"], "proof_tool"),
        "proof_tool.script.git_blob",
        40,
    )
    script = _path_ref(script_path)
    script["git_blob"] = blob
    return {
        "root": str(root),
        "commit": commit,
        "tree": tree,
        "script": script,
        "scoped_clean_required": True,
        "scoped_clean_observed": True,
    }


def _git_file_ref(root: Path, path: Path, commit: str, field: str) -> dict[str, Any]:
    relative = _relative_repo_path(root, path, field)
    dirty = _run_git(root, ["status", "--porcelain", "--", relative], field)
    if dirty:
        raise ReceiptValidationError(f"{field} is dirty or untracked: {dirty}")
    blob = _require_hex(
        _run_git(root, ["rev-parse", f"{commit}:{relative}"], field),
        f"{field}.git_blob",
        40,
    )
    ref = _path_ref(path)
    ref["git_blob"] = blob
    return ref


def _validate_git_file_ref(
    value: Any,
    field: str,
    *,
    root: Path,
    commit: str,
    required_root: str,
) -> bytes:
    ref = _require_object(value, field)
    _require_exact_keys(ref, GIT_FILE_REF_KEYS, field)
    path = Path(_require_string(ref["path"], f"{field}.path")).expanduser().resolve()
    relative = _relative_repo_path(root, path, field)
    if not relative.startswith(required_root.rstrip("/") + "/"):
        raise ReceiptValidationError(f"{field} must live under {required_root}")
    if not relative.endswith(".json"):
        raise ReceiptValidationError(f"{field} must be a JSON document")
    expected_blob = _require_hex(ref["git_blob"], f"{field}.git_blob", 40)
    observed_blob = _run_git(root, ["rev-parse", f"{commit}:{relative}"], field)
    if observed_blob != expected_blob:
        raise ReceiptValidationError(f"{field}.git_blob mismatch")
    blob_bytes = _run_git_bytes(root, ["show", f"{commit}:{relative}"], field)
    expected_sha = _require_hex(ref["sha256"], f"{field}.sha256", 64)
    expected_size = _require_positive_int(ref["size_bytes"], f"{field}.size_bytes")
    if len(blob_bytes) != expected_size:
        raise ReceiptValidationError(f"{field}.size_bytes differs from recorded Git blob")
    if _sha256_bytes(blob_bytes) != expected_sha:
        raise ReceiptValidationError(f"{field}.sha256 differs from recorded Git blob")
    return blob_bytes


def _validate_proof_tool(value: Any) -> dict[str, Any]:
    record = _require_object(value, "proof_tool")
    _require_exact_keys(record, PROOF_TOOL_KEYS, "proof_tool")
    root = Path(_require_string(record["root"], "proof_tool.root")).resolve()
    commit = _require_hex(record["commit"], "proof_tool.commit", 40)
    tree = _require_hex(record["tree"], "proof_tool.tree", 40)
    if _run_git(root, ["rev-parse", f"{commit}^{{tree}}"], "proof_tool") != tree:
        raise ReceiptValidationError("proof_tool.tree differs from its recorded commit")
    if _require_bool(record["scoped_clean_required"], "proof_tool.scoped_clean_required") is not True:
        raise ReceiptValidationError("proof_tool.scoped_clean_required must be true")
    if _require_bool(record["scoped_clean_observed"], "proof_tool.scoped_clean_observed") is not True:
        raise ReceiptValidationError("proof_tool.scoped_clean_observed must be true")
    script = _require_object(record["script"], "proof_tool.script")
    _require_exact_keys(script, GIT_FILE_REF_KEYS, "proof_tool.script")
    path = Path(_require_string(script["path"], "proof_tool.script.path")).resolve()
    if _relative_repo_path(root, path, "proof_tool.script") != PROOF_TOOL_PATH:
        raise ReceiptValidationError("proof_tool.script path differs from selected sole owner")
    blob = _require_hex(script["git_blob"], "proof_tool.script.git_blob", 40)
    observed_blob = _run_git(root, ["rev-parse", f"{commit}:{PROOF_TOOL_PATH}"], "proof_tool")
    if observed_blob != blob:
        raise ReceiptValidationError("proof_tool.script.git_blob mismatch")
    blob_bytes = _run_git_bytes(
        root, ["show", f"{commit}:{PROOF_TOOL_PATH}"], "proof_tool"
    )
    if len(blob_bytes) != _require_positive_int(
        script["size_bytes"], "proof_tool.script.size_bytes"
    ):
        raise ReceiptValidationError("proof_tool.script size differs from recorded Git blob")
    if _sha256_bytes(blob_bytes) != _require_hex(
        script["sha256"], "proof_tool.script.sha256", 64
    ):
        raise ReceiptValidationError("proof_tool.script SHA differs from recorded Git blob")
    return record


def _validate_pose(value: Any, field: str) -> dict[str, Any]:
    pose = _require_object(value, field)
    _require_exact_keys(pose, POSE_KEYS, field)
    for vector_field in ("player_position", "camera_position"):
        vector = _require_list(pose[vector_field], f"{field}.{vector_field}")
        if len(vector) != 3:
            raise ReceiptValidationError(f"{field}.{vector_field} must contain three numbers")
        for index, component in enumerate(vector):
            _require_number(component, f"{field}.{vector_field}[{index}]")
    for scalar in (
        "player_yaw",
        "camera_yaw",
        "camera_pitch",
        "camera_roll",
        "day_cycle_value",
        "weather_intensity",
    ):
        _require_number(pose[scalar], f"{field}.{scalar}")
    projection = _require_string(pose["projection"], f"{field}.projection")
    if projection not in {"perspective", "orthographic"}:
        raise ReceiptValidationError(f"{field}.projection is unsupported")
    for optional_scalar in ("fov_degrees", "orthographic_size"):
        if pose[optional_scalar] is not None:
            _require_number(pose[optional_scalar], f"{field}.{optional_scalar}")
    if projection == "perspective" and pose["fov_degrees"] is None:
        raise ReceiptValidationError(f"{field}.fov_degrees is required for perspective")
    if projection == "orthographic" and pose["orthographic_size"] is None:
        raise ReceiptValidationError(f"{field}.orthographic_size is required for orthographic")
    if projection == "perspective" and pose["orthographic_size"] is not None:
        raise ReceiptValidationError(f"{field}.orthographic_size must be null for perspective")
    if projection == "orthographic" and pose["fov_degrees"] is not None:
        raise ReceiptValidationError(f"{field}.fov_degrees must be null for orthographic")
    _require_string(pose["weather_kind"], f"{field}.weather_kind")
    _require_positive_int(pose["viewport_width"], f"{field}.viewport_width")
    _require_positive_int(pose["viewport_height"], f"{field}.viewport_height")
    return pose


def _validate_source_identity(value: Any, field: str) -> dict[str, Any]:
    identity = _require_object(value, field)
    _require_exact_keys(identity, SOURCE_IDENTITY_KEYS, field)
    commit = _require_hex(identity["commit"], f"{field}.commit", 40)
    tree = _require_hex(identity["tree"], f"{field}.tree", 40)
    if commit != SELECTED_SOURCE_COMMIT or tree != SELECTED_SOURCE_TREE:
        raise ReceiptValidationError(f"{field} differs from selected baseline source")
    if _require_bool(identity["dirty"], f"{field}.dirty") is not False:
        raise ReceiptValidationError(f"{field}.dirty must be false")
    return identity


def _validate_build_identity(value: Any, field: str) -> dict[str, Any]:
    build = _require_object(value, field)
    _require_exact_keys(build, BUILD_IDENTITY_KEYS, field)
    _require_string(build["godot_version"], f"{field}.godot_version")
    _validate_file_ref(build["godot_binary"], f"{field}.godot_binary")
    _validate_file_ref(build["project_build_manifest"], f"{field}.project_build_manifest")
    _require_hex(build["capture_tool_commit"], f"{field}.capture_tool_commit", 40)
    _require_hex(build["capture_tool_tree"], f"{field}.capture_tool_tree", 40)
    _validate_file_ref(build["capture_tool"], f"{field}.capture_tool")
    return build


def _validate_launch_configuration(value: Any, field: str) -> dict[str, Any]:
    launch = _require_object(value, field)
    _require_exact_keys(launch, LAUNCH_CONFIGURATION_KEYS, field)
    arguments = _require_list(launch["argument_vector"], f"{field}.argument_vector")
    if not arguments:
        raise ReceiptValidationError(f"{field}.argument_vector must not be empty")
    for index, argument in enumerate(arguments):
        _require_string(argument, f"{field}.argument_vector[{index}]")
    environment = _require_object(launch["environment"], f"{field}.environment")
    for key, item in environment.items():
        _require_string(key, f"{field}.environment key")
        if not isinstance(item, str):
            raise ReceiptValidationError(f"{field}.environment.{key} must be a string")
    _require_string(launch["renderer"], f"{field}.renderer")
    flags = _require_object(launch["feature_flags"], f"{field}.feature_flags")
    for key, item in flags.items():
        _require_string(key, f"{field}.feature_flags key")
        _require_bool(item, f"{field}.feature_flags.{key}")
    return launch


def _validate_frame_bundle(value: Any, field: str) -> dict[str, Any]:
    frame = _require_object(value, field)
    _require_exact_keys(frame, FRAME_BUNDLE_KEYS, field)
    role = _require_string(frame["role"], f"{field}.role")
    if role not in FRAME_ROLES:
        raise ReceiptValidationError(f"{field}.role is unsupported: {role}")
    pose = _validate_pose(frame["pose"], f"{field}.pose")
    if pose["projection"] != ROLE_PROJECTION[role]:
        raise ReceiptValidationError(f"{field}.pose.projection mismatches role {role}")
    locked_role = next(
        (name for name in LOCKED_ROLE_POSES if role.startswith(name + "_")),
        None,
    )
    if locked_role is not None:
        for pose_field, expected in LOCKED_ROLE_POSES[locked_role].items():
            if pose[pose_field] != expected:
                raise ReceiptValidationError(
                    f"{field}.pose.{pose_field} differs from permanent {locked_role} lock"
                )
    if role.startswith("p1_deep_reflection_") and pose["day_cycle_value"] != 0.5:
        raise ReceiptValidationError(f"{field}.pose must capture P1 at noon")
    if role.startswith("shallow_contact_") and (
        pose["player_position"] == LOCKED_ROLE_POSES["p1_deep_reflection"]["player_position"]
        or pose["camera_position"] == LOCKED_ROLE_POSES["p1_deep_reflection"]["camera_position"]
    ):
        raise ReceiptValidationError(f"{field}.pose illegally substitutes P1 for shallow contact")
    if role.startswith("shallow_contact_") and pose["day_cycle_value"] != 0.5:
        raise ReceiptValidationError(f"{field}.pose must capture shallow contact at noon")
    if role.startswith("p2_night_suppression_") and 0.1 < float(
        pose["day_cycle_value"]
    ) < 0.9:
        raise ReceiptValidationError(f"{field}.pose is not in the night cycle")
    wrapper = _validate_source_identity(frame["wrapper_identity"], f"{field}.wrapper_identity")
    runtime = _validate_source_identity(frame["runtime_identity"], f"{field}.runtime_identity")
    if wrapper != runtime:
        raise ReceiptValidationError(f"{field} wrapper/runtime identity diverges")
    run_identity = _require_object(frame["run_identity"], f"{field}.run_identity")
    _require_exact_keys(run_identity, RUN_IDENTITY_KEYS, f"{field}.run_identity")
    capture_id = _require_string(
        run_identity["capture_id"], f"{field}.run_identity.capture_id"
    )
    process_id = _require_string(
        run_identity["process_id"], f"{field}.run_identity.process_id"
    )
    _reject_banned_authority_text(capture_id, f"{field}.run_identity.capture_id")
    _reject_banned_authority_text(process_id, f"{field}.run_identity.process_id")
    artifacts = _require_object(frame["artifacts"], f"{field}.artifacts")
    _require_exact_keys(artifacts, set(ARTIFACT_ROLES), f"{field}.artifacts")
    for artifact_role in ARTIFACT_ROLES:
        _validate_file_ref(artifacts[artifact_role], f"{field}.artifacts.{artifact_role}")
    return frame


def _validate_manifest(value: Any, field: str = "capture_manifest") -> dict[str, Any]:
    manifest = _require_object(value, field)
    _require_exact_keys(manifest, MANIFEST_KEYS, field)
    if manifest["schema"] != CAPTURE_MANIFEST_SCHEMA:
        raise ReceiptValidationError(f"{field}.schema must be {CAPTURE_MANIFEST_SCHEMA}")
    capture_id = _require_string(manifest["capture_id"], f"{field}.capture_id")
    _reject_banned_authority_text(capture_id, f"{field}.capture_id")
    source = _require_object(manifest["baseline_source"], f"{field}.baseline_source")
    _require_exact_keys(source, BASELINE_SOURCE_KEYS, f"{field}.baseline_source")
    if source != {
        "commit": SELECTED_SOURCE_COMMIT,
        "tree": SELECTED_SOURCE_TREE,
        "clean_required": True,
    }:
        raise ReceiptValidationError(f"{field}.baseline_source differs from selected source")
    witness = _require_object(manifest["transport_witness"], f"{field}.transport_witness")
    _require_exact_keys(witness, FILE_REF_KEYS | {"read_only"}, f"{field}.transport_witness")
    _validate_file_ref(
        {key: witness[key] for key in FILE_REF_KEYS}, f"{field}.transport_witness"
    )
    if _require_bool(witness["read_only"], f"{field}.transport_witness.read_only") is not True:
        raise ReceiptValidationError(f"{field}.transport_witness.read_only must be true")
    _validate_build_identity(manifest["build_identity"], f"{field}.build_identity")
    _validate_launch_configuration(
        manifest["launch_configuration"], f"{field}.launch_configuration"
    )
    frames = _require_list(manifest["frame_bundles"], f"{field}.frame_bundles")
    if len(frames) != len(FRAME_ROLES):
        raise ReceiptValidationError(f"{field}.frame_bundles must contain exactly six frames")
    observed_roles: list[str] = []
    artifact_paths: set[Path] = set()
    for index, frame_value in enumerate(frames):
        frame = _validate_frame_bundle(frame_value, f"{field}.frame_bundles[{index}]")
        observed_roles.append(frame["role"])
        if frame["run_identity"]["capture_id"] != capture_id:
            raise ReceiptValidationError(
                f"{field}.frame_bundles[{index}] capture_id mismatches manifest"
            )
        for artifact_role in ARTIFACT_ROLES:
            artifact_path = Path(
                frame["artifacts"][artifact_role]["path"]
            ).expanduser().resolve()
            if artifact_path in artifact_paths:
                raise ReceiptValidationError(
                    f"{field}.frame_bundles reuses artifact path: {artifact_path}"
                )
            artifact_paths.add(artifact_path)
    if tuple(observed_roles) != FRAME_ROLES:
        raise ReceiptValidationError(
            f"{field}.frame_bundles roles must match the closed order {FRAME_ROLES}"
        )
    for perspective_index, orthographic_index in ((0, 1), (2, 3), (4, 5)):
        perspective_pose = dict(frames[perspective_index]["pose"])
        orthographic_pose = dict(frames[orthographic_index]["pose"])
        for key in ("projection", "fov_degrees", "orthographic_size"):
            perspective_pose.pop(key)
            orthographic_pose.pop(key)
        if perspective_pose != orthographic_pose:
            raise ReceiptValidationError(
                f"{field}.frame_bundles projection pair pose/config mismatch at "
                f"{perspective_index}/{orthographic_index}"
            )
    _reject_future_receipt_references(manifest, field)
    return manifest


def _validate_designation(value: Any, manifest: Mapping[str, Any], manifest_sha: str) -> dict[str, Any]:
    designation = _require_object(value, "designation")
    _require_exact_keys(designation, DESIGNATION_KEYS, "designation")
    if designation["schema"] != DESIGNATION_SCHEMA:
        raise ReceiptValidationError(f"designation.schema must be {DESIGNATION_SCHEMA}")
    if designation["baseline_name"] != BASELINE_NAME:
        raise ReceiptValidationError("designation.baseline_name must be Baseline A")
    if designation["receipt_schema"] != SCHEMA:
        raise ReceiptValidationError(f"designation.receipt_schema must be {SCHEMA}")
    if designation["source_commit"] != SELECTED_SOURCE_COMMIT:
        raise ReceiptValidationError("designation.source_commit differs from selected source")
    if designation["source_tree"] != SELECTED_SOURCE_TREE:
        raise ReceiptValidationError("designation.source_tree differs from selected source")
    _require_string(designation["operator_identity"], "designation.operator_identity")
    _require_utc_timestamp(designation["designated_at_utc"], "designation.designated_at_utc")
    _require_string(designation["statement"], "designation.statement")
    if _require_hex(
        designation["capture_manifest_sha256"],
        "designation.capture_manifest_sha256",
        64,
    ) != manifest_sha:
        raise ReceiptValidationError("designation does not bind the supplied capture manifest")
    predecessor = _require_optional_digest(
        designation["predecessor_receipt_sha256"],
        "designation.predecessor_receipt_sha256",
    )
    frames = _require_list(designation["frames"], "designation.frames")
    if len(frames) != len(FRAME_ROLES):
        raise ReceiptValidationError("designation.frames must contain exactly six frames")
    for index, (frame_value, manifest_frame) in enumerate(
        zip(frames, manifest["frame_bundles"], strict=True)
    ):
        frame = _require_object(frame_value, f"designation.frames[{index}]")
        _require_exact_keys(frame, DESIGNATED_FRAME_KEYS, f"designation.frames[{index}]")
        role = FRAME_ROLES[index]
        if frame["role"] != role or frame["projection"] != ROLE_PROJECTION[role]:
            raise ReceiptValidationError(f"designation.frames[{index}] role/projection mismatch")
        expected_pose = _pose_digest(manifest_frame["pose"])
        if _require_hex(
            frame["pose_sha256"], f"designation.frames[{index}].pose_sha256", 64
        ) != expected_pose:
            raise ReceiptValidationError(f"designation.frames[{index}] pose digest mismatch")
    designation["predecessor_receipt_sha256"] = predecessor
    return designation


def _build_receipt(
    *,
    designation_path: Path,
    manifest_path: Path,
    source_root: Path,
    proof_tool_root: Path,
) -> dict[str, Any]:
    _reject_banned_authority_text(str(manifest_path.resolve()), "capture_manifest.path")
    if RECEIPT_ROOT in manifest_path.resolve().as_posix():
        raise ReceiptValidationError(
            "capture_manifest.path must not live under the future receipt root"
        )
    manifest_ref = _path_ref(manifest_path)
    manifest = _validate_manifest(_load_json(manifest_path, "capture manifest"))
    source = _validate_selected_source(source_root.resolve(), manifest["baseline_source"])
    proof_tool = _proof_tool_record(proof_tool_root)
    designation_ref = _git_file_ref(
        proof_tool_root.resolve(),
        designation_path,
        proof_tool["commit"],
        "operator designation",
    )
    designation = _validate_designation(
        _load_json(designation_path, "operator designation"),
        manifest,
        manifest_ref["sha256"],
    )
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "baseline_name": BASELINE_NAME,
        "failure_log_refs": ["FL-4737", "FL-4739", "RQ-195", "RQ-204"],
        "designation_record": designation_ref,
        "capture_manifest": manifest_ref,
        "transport_witness": {
            key: manifest["transport_witness"][key] for key in FILE_REF_KEYS
        },
        "baseline_source": deepcopy(source),
        "proof_tool": proof_tool,
        "build_identity": deepcopy(manifest["build_identity"]),
        "launch_configuration": deepcopy(manifest["launch_configuration"]),
        "predecessor_receipt_sha256": designation["predecessor_receipt_sha256"],
        "frame_bundles": deepcopy(manifest["frame_bundles"]),
        "receipt_sha256": "",
    }
    receipt["receipt_sha256"] = _receipt_digest(receipt)
    return receipt


def _validate_predecessor_receipt(
    digest: str | None,
    *,
    proof_root: Path,
    source_root: Path,
) -> None:
    if digest is None:
        return
    predecessor_path = proof_root / RECEIPT_ROOT / digest / "receipt.json"
    if not predecessor_path.is_file():
        raise ReceiptValidationError(
            "receipt predecessor is not present at its canonical immutable path: "
            f"{predecessor_path}"
        )
    predecessor = validate_receipt(predecessor_path, source_root=source_root)
    if predecessor["receipt_sha256"] != digest:
        raise ReceiptValidationError("receipt predecessor digest differs from its path")


def validate_receipt_object(
    receipt: Any,
    *,
    source_root: Path,
) -> dict[str, Any]:
    value = _require_object(receipt, "receipt")
    _require_exact_keys(value, TOP_LEVEL_KEYS, "receipt")
    if value["schema"] != SCHEMA:
        raise ReceiptValidationError(f"receipt.schema must be {SCHEMA}")
    if value["baseline_name"] != BASELINE_NAME:
        raise ReceiptValidationError("receipt.baseline_name must be Baseline A")
    if value["failure_log_refs"] != ["FL-4737", "FL-4739", "RQ-195", "RQ-204"]:
        raise ReceiptValidationError("receipt.failure_log_refs differs from the closed order")
    expected_digest = _require_hex(value["receipt_sha256"], "receipt.receipt_sha256", 64)
    observed_digest = _receipt_digest(value)
    if expected_digest != observed_digest:
        raise ReceiptValidationError(
            f"receipt self-hash mismatch expected={expected_digest} observed={observed_digest}"
        )
    proof_tool = _validate_proof_tool(value["proof_tool"])
    proof_root = Path(proof_tool["root"]).resolve()
    designation_bytes = _validate_git_file_ref(
        value["designation_record"],
        "receipt.designation_record",
        root=proof_root,
        commit=proof_tool["commit"],
        required_root=DESIGNATION_ROOT,
    )
    manifest_path = _validate_file_ref(value["capture_manifest"], "receipt.capture_manifest")
    manifest = _validate_manifest(_load_json(manifest_path, "capture manifest"))
    designation = _validate_designation(
        _load_json_bytes(designation_bytes, "operator designation"),
        manifest,
        value["capture_manifest"]["sha256"],
    )
    _validate_selected_source(source_root.resolve(), value["baseline_source"])
    _validate_file_ref(value["transport_witness"], "receipt.transport_witness")
    if value["transport_witness"] != {
        key: manifest["transport_witness"][key] for key in FILE_REF_KEYS
    }:
        raise ReceiptValidationError("receipt.transport_witness differs from capture manifest")
    _validate_build_identity(value["build_identity"], "receipt.build_identity")
    if value["build_identity"] != manifest["build_identity"]:
        raise ReceiptValidationError("receipt.build_identity differs from capture manifest")
    _validate_launch_configuration(
        value["launch_configuration"], "receipt.launch_configuration"
    )
    if value["launch_configuration"] != manifest["launch_configuration"]:
        raise ReceiptValidationError("receipt.launch_configuration differs from capture manifest")
    predecessor = _require_optional_digest(
        value["predecessor_receipt_sha256"],
        "receipt.predecessor_receipt_sha256",
    )
    if predecessor != designation["predecessor_receipt_sha256"]:
        raise ReceiptValidationError("receipt predecessor differs from operator designation")
    if predecessor == expected_digest:
        raise ReceiptValidationError("receipt cannot name itself as predecessor")
    _validate_predecessor_receipt(
        predecessor,
        proof_root=proof_root,
        source_root=source_root.resolve(),
    )
    if value["frame_bundles"] != manifest["frame_bundles"]:
        raise ReceiptValidationError("receipt.frame_bundles differs from capture manifest")
    for index, frame in enumerate(value["frame_bundles"]):
        _validate_frame_bundle(frame, f"receipt.frame_bundles[{index}]")
    return value


def create_receipt(
    *,
    output_root: Path,
    designation_path: Path,
    manifest_path: Path,
    source_root: Path,
    proof_tool_root: Path,
) -> tuple[Path, dict[str, Any]]:
    """Validate all owners, then exclusively create one content-addressed receipt."""

    receipt = _build_receipt(
        designation_path=designation_path.resolve(),
        manifest_path=manifest_path.resolve(),
        source_root=source_root.resolve(),
        proof_tool_root=proof_tool_root.resolve(),
    )
    validate_receipt_object(receipt, source_root=source_root)
    digest = receipt["receipt_sha256"]
    root = output_root.expanduser().resolve()
    expected_root = proof_tool_root.resolve() / RECEIPT_ROOT
    if root != expected_root:
        raise ReceiptValidationError(
            f"output_root must be the canonical receipt root: {expected_root}"
        )
    root.mkdir(parents=True, exist_ok=True)
    destination_dir = root / digest
    destination = destination_dir / "receipt.json"
    if destination.exists():
        raise ReceiptValidationError(
            f"immutable receipt already exists: {destination}"
        )
    if receipt["predecessor_receipt_sha256"] is None and any(
        candidate.is_file() for candidate in root.glob("*/receipt.json")
    ):
        raise ReceiptValidationError(
            "a later baseline must name an existing predecessor receipt digest"
        )
    try:
        destination_dir.mkdir()
    except FileExistsError as exc:
        raise ReceiptValidationError(
            f"immutable receipt directory already exists: {destination_dir}"
        ) from exc
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination_dir,
            prefix=".receipt.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            # The stored document is deterministic canonical JSON too; the
            # self-hash applies the same encoding after excluding its field.
            handle.write(_canonical_json_bytes(receipt))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication cannot overwrite a competing final name.
        os.link(temporary, destination)
        temporary.unlink()
        temporary = None
        directory_fd = os.open(destination_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        try:
            destination_dir.rmdir()
        except OSError:
            pass
        raise ReceiptValidationError(
            f"immutable receipt already exists: {destination}"
        ) from exc
    except Exception:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        try:
            destination_dir.rmdir()
        except OSError:
            pass
        raise
    return destination, receipt


def validate_receipt(path: Path, *, source_root: Path) -> dict[str, Any]:
    receipt_path = path.expanduser().resolve()
    receipt = validate_receipt_object(
        _load_json(receipt_path, "water baseline receipt"),
        source_root=source_root,
    )
    if receipt_path.name != "receipt.json":
        raise ReceiptValidationError("receipt filename must be receipt.json")
    if receipt_path.parent.name != receipt["receipt_sha256"]:
        raise ReceiptValidationError("receipt directory does not match receipt_sha256")
    expected_root = Path(receipt["proof_tool"]["root"]).resolve() / RECEIPT_ROOT
    if receipt_path.parent.parent != expected_root:
        raise ReceiptValidationError(f"receipt must live under {expected_root}")
    return receipt


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or validate the immutable FL-4737 water Baseline A receipt."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--output-root", required=True, type=Path)
    create.add_argument("--designation", required=True, type=Path)
    create.add_argument("--capture-manifest", required=True, type=Path)
    create.add_argument("--source-root", required=True, type=Path)
    create.add_argument("--proof-tool-root", required=True, type=Path)
    validate = commands.add_parser("validate")
    validate.add_argument("receipt", type=Path)
    validate.add_argument("--source-root", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "create":
            path, receipt = create_receipt(
                output_root=args.output_root,
                designation_path=args.designation,
                manifest_path=args.capture_manifest,
                source_root=args.source_root,
                proof_tool_root=args.proof_tool_root,
            )
            print(
                "FL4739_WATER_BASELINE_RECEIPT_CREATED "
                f"path={path} digest={receipt['receipt_sha256']}"
            )
            return 0
        receipt = validate_receipt(args.receipt, source_root=args.source_root)
        print(
            "FL4739_WATER_BASELINE_RECEIPT_VALID "
            f"path={args.receipt.resolve()} digest={receipt['receipt_sha256']}"
        )
        return 0
    except ReceiptValidationError as exc:
        print(f"FL4739_WATER_BASELINE_RECEIPT_REJECTED {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
