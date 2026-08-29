#!/usr/bin/env python3
"""Validate Roblox reference intake manifest for FL-4254.

Validates assets/reference/roblox_sbu/source_manifest.json (or any v2
manifest at a custom path). Does NOT scrape, rip, or automate any
protected-content extraction. Read-only validation of imported geometry
artifacts produced by an authorized workflow.

Exits 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


SCHEMA_VERSION = "2"

REQUIRED_TOP_LEVEL = [
    "source",
    "acquisition",
    "files",
    "coordinate_assumptions",
    "normalization",
    "known_anchors",
    "known_limitations",
    "proof_state",
    "fl_ref",
]

ACQUISITION_METHODS = {
    "roblox_studio_gltf_authorized",
    "externally_provided_gltf",
    "externally_provided_obj",
    "research_gpu_capture_obj",
}

FILE_KINDS = {"gltf", "obj", "fbx", "blend", "texture", "manifest", "other"}
FILE_STAGES = {"raw", "normalized", "comparison"}
FILE_ROLES = {
    "scene",
    "terrain",
    "building_meshes",
    "roads",
    "plazas",
    "landmarks",
    "materials",
    "unknown",
}

PROOF_STATES = {
    "no_geometry_acquired",
    "raw_only",
    "normalized",
    "comparison_ready",
}


def _err(errors: list, code: str, message: str) -> None:
    errors.append({"code": code, "message": message})


def _validate_source(manifest: dict, errors: list) -> None:
    source = manifest.get("source")
    if not isinstance(source, dict):
        _err(errors, "source_missing", "source must be an object")
        return
    if not source.get("platform"):
        _err(errors, "source_platform_missing", "source.platform required")
    if not source.get("game_id"):
        _err(errors, "source_game_id_missing", "source.game_id required")


def _validate_acquisition(manifest: dict, errors: list, strict: bool) -> None:
    acq = manifest.get("acquisition")
    if not isinstance(acq, dict):
        _err(errors, "acquisition_missing", "acquisition must be an object")
        return

    proof_state = manifest.get("proof_state")
    has_geometry = proof_state != "no_geometry_acquired"

    if has_geometry:
        method = acq.get("method")
        if method not in ACQUISITION_METHODS:
            _err(
                errors,
                "acquisition_method_invalid",
                f"acquisition.method must be one of {sorted(ACQUISITION_METHODS)}; got {method!r}",
            )

        authorized = acq.get("authorized")
        research_only = acq.get("research_only")
        if authorized is None and research_only is None:
            _err(
                errors,
                "acquisition_auth_state_unset",
                "acquisition.authorized or acquisition.research_only must be set when geometry is acquired",
            )
        if authorized is False and research_only is not True:
            _err(
                errors,
                "acquisition_unauthorized_not_research_only",
                "unauthorized geometry must be marked research_only=true; otherwise refuse to track",
            )

        if not acq.get("date_acquired"):
            _err(errors, "acquisition_date_missing", "acquisition.date_acquired required for tracked geometry")
        if not acq.get("acquired_by"):
            _err(errors, "acquisition_acquired_by_missing", "acquisition.acquired_by required for tracked geometry")


def _validate_files(manifest: dict, manifest_path: Path, errors: list, strict_files: bool) -> None:
    files = manifest.get("files")
    if not isinstance(files, list):
        _err(errors, "files_not_list", "files must be a list")
        return

    base = manifest_path.parent

    for idx, entry in enumerate(files):
        if not isinstance(entry, dict):
            _err(errors, "file_entry_not_object", f"files[{idx}] must be an object")
            continue
        prefix = f"files[{idx}]"

        for field in ("path", "kind", "stage", "role", "sha256", "bytes"):
            if field not in entry:
                _err(errors, f"file_field_missing", f"{prefix}.{field} required")

        kind = entry.get("kind")
        if kind is not None and kind not in FILE_KINDS:
            _err(errors, "file_kind_invalid", f"{prefix}.kind must be one of {sorted(FILE_KINDS)}; got {kind!r}")

        stage = entry.get("stage")
        if stage is not None and stage not in FILE_STAGES:
            _err(errors, "file_stage_invalid", f"{prefix}.stage must be one of {sorted(FILE_STAGES)}; got {stage!r}")

        role = entry.get("role")
        if role is not None and role not in FILE_ROLES:
            _err(errors, "file_role_invalid", f"{prefix}.role must be one of {sorted(FILE_ROLES)}; got {role!r}")

        path_str = entry.get("path")
        if not path_str:
            continue

        if strict_files:
            target = (base / path_str).resolve()
            if not target.is_file():
                _err(errors, "file_missing_on_disk", f"{prefix}.path does not exist: {path_str}")
                continue
            actual_bytes = target.stat().st_size
            declared_bytes = entry.get("bytes")
            if isinstance(declared_bytes, int) and declared_bytes != actual_bytes:
                _err(
                    errors,
                    "file_bytes_mismatch",
                    f"{prefix}.bytes={declared_bytes} but on disk={actual_bytes}",
                )
            declared_sha = entry.get("sha256")
            if isinstance(declared_sha, str) and declared_sha:
                actual_sha = hashlib.sha256(target.read_bytes()).hexdigest()
                if declared_sha != actual_sha:
                    _err(
                        errors,
                        "file_sha256_mismatch",
                        f"{prefix}.sha256={declared_sha[:12]}... but on disk={actual_sha[:12]}...",
                    )


def _validate_coordinate_assumptions(manifest: dict, errors: list) -> None:
    coord = manifest.get("coordinate_assumptions")
    if not isinstance(coord, dict):
        _err(errors, "coordinate_assumptions_missing", "coordinate_assumptions must be an object")
        return
    for field in ("origin_lat", "origin_lon"):
        if coord.get(field) is None:
            _err(errors, "coordinate_origin_missing", f"coordinate_assumptions.{field} required")


def _validate_normalization(manifest: dict, errors: list) -> None:
    norm = manifest.get("normalization")
    if not isinstance(norm, dict):
        _err(errors, "normalization_missing", "normalization must be an object")
        return

    proof_state = manifest.get("proof_state")
    if proof_state in {"normalized", "comparison_ready"}:
        for field in ("roblox_units_per_meter", "a3d_units_per_meter", "transform_matrix_to_a3d"):
            if norm.get(field) is None:
                _err(
                    errors,
                    "normalization_scale_missing",
                    f"normalization.{field} required when proof_state={proof_state!r}",
                )
        anchors_used = norm.get("anchors_used")
        if not isinstance(anchors_used, list) or not anchors_used:
            _err(
                errors,
                "normalization_anchors_used_missing",
                "normalization.anchors_used must be a non-empty list when normalized",
            )


def _validate_anchors(manifest: dict, errors: list) -> None:
    anchors = manifest.get("known_anchors")
    if not isinstance(anchors, list):
        _err(errors, "known_anchors_not_list", "known_anchors must be a list")
        return
    for idx, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            _err(errors, "anchor_not_object", f"known_anchors[{idx}] must be an object")
            continue
        if not anchor.get("name"):
            _err(errors, "anchor_name_missing", f"known_anchors[{idx}].name required")


def _validate_proof_state(manifest: dict, errors: list) -> None:
    state = manifest.get("proof_state")
    if state not in PROOF_STATES:
        _err(errors, "proof_state_invalid", f"proof_state must be one of {sorted(PROOF_STATES)}; got {state!r}")


def validate_manifest(manifest_path: Path, strict_files: bool) -> dict:
    errors: list = []

    if not manifest_path.is_file():
        return {"ok": False, "errors": [{"code": "manifest_missing", "message": str(manifest_path)}]}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "errors": [{"code": "manifest_json_invalid", "message": str(exc)}]}

    if not isinstance(manifest, dict):
        return {"ok": False, "errors": [{"code": "manifest_not_object", "message": "manifest root must be an object"}]}

    schema_version = manifest.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        _err(
            errors,
            "schema_version_mismatch",
            f"schema_version must be {SCHEMA_VERSION!r}; got {schema_version!r}",
        )

    for key in REQUIRED_TOP_LEVEL:
        if key not in manifest:
            _err(errors, "top_level_field_missing", f"required top-level field missing: {key}")

    _validate_source(manifest, errors)
    _validate_acquisition(manifest, errors, strict_files)
    _validate_files(manifest, manifest_path, errors, strict_files)
    _validate_coordinate_assumptions(manifest, errors)
    _validate_normalization(manifest, errors)
    _validate_anchors(manifest, errors)
    _validate_proof_state(manifest, errors)

    proof_state = manifest.get("proof_state")
    file_count = len(manifest.get("files", []) or [])

    return {
        "ok": not errors,
        "errors": errors,
        "manifest_path": str(manifest_path),
        "schema_version": schema_version,
        "proof_state": proof_state,
        "file_count": file_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Roblox reference intake manifest (FL-4254)")
    parser.add_argument(
        "--manifest",
        default="assets/reference/roblox_sbu/source_manifest.json",
        help="Path to manifest JSON",
    )
    parser.add_argument(
        "--strict-files",
        action="store_true",
        help="Verify each file entry exists on disk with matching bytes/sha256",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    args = parser.parse_args(argv)

    result = validate_manifest(Path(args.manifest), args.strict_files)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        verdict = "PASS" if result["ok"] else "FAIL"
        print(f"[{verdict}] {result.get('manifest_path', args.manifest)}")
        if result.get("schema_version") is not None:
            print(f"  schema_version: {result['schema_version']}")
            print(f"  proof_state:    {result.get('proof_state')!r}")
            print(f"  file_count:     {result.get('file_count')}")
        if not result["ok"]:
            print(f"  errors ({len(result['errors'])}):")
            for err in result["errors"]:
                print(f"    [{err['code']}] {err['message']}")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
