from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def EmitAppearanceCompileReport(
    bundle: dict[str, Any],
    ids_lock: dict[str, Any],
    bundle_src: Path,
    asset_cache: dict[str, dict[str, Any]],
    contract_version: int,
    bundle_slug: str,
    reachable_state_space: dict[str, Any],
    mounted_closure: dict[str, Any],
    deps: dict[str, Any],
) -> dict[str, Any]:
    _canonical_json_bytes: Callable[..., bytes] = deps["_canonical_json_bytes"]
    _sha256_bytes: Callable[..., str] = deps["_sha256_bytes"]
    _sha256_file: Callable[..., str] = deps["_sha256_file"]
    _repo_relative: Callable[..., str] = deps["_repo_relative"]
    compiler_version: str = deps["COMPILER_VERSION"]

    bundle_bytes = _canonical_json_bytes(bundle)
    ids_lock_bytes = _canonical_json_bytes(ids_lock)
    source_fingerprints = {
        "manifest_sha256": _sha256_file(bundle_src),
        "asset_sha256": {
            value["path"]: value["sha256"]
            for value in sorted(asset_cache.values(), key=lambda item: item["path"])
        },
    }
    return {
        "bundle_contract_version": contract_version,
        "bundle_slug": bundle_slug,
        "compiler_version": compiler_version,
        "source_manifest": _repo_relative(bundle_src),
        "source_fingerprints": source_fingerprints,
        "bundle_hash": _sha256_bytes(bundle_bytes),
        "ids_lock_hash": _sha256_bytes(ids_lock_bytes),
        "generated_files": [
            "appearance_bundle.json",
            "ids.lock.json",
            "compile_report.json",
        ],
        "appearance_emission_scopes": reachable_state_space["appearance_emission_scopes"],
        "reachable_state_space": {
            "actor_mounted_scope_filter": reachable_state_space["actor_mounted_scope_filter"],
            "reachable_skin_definitions": reachable_state_space["reachable_skin_definitions"],
            "reachable_equipped_appearance_rows": reachable_state_space["reachable_equipped_appearance_rows"],
            "reachable_default_injected_rows": reachable_state_space["reachable_default_injected_rows"],
            "reachable_mounted_family_keys": reachable_state_space["reachable_mounted_family_keys"],
        },
        "rule_sets": reachable_state_space["rule_sets"],
        "mounted_closure_proof": {
            "mounted_admission": mounted_closure["mounted_admission"],
            "admitted_mount_qualified_body_layers": mounted_closure["admitted_mount_qualified_body_layers"],
            "admitted_mount_qualified_item_layers": mounted_closure["admitted_mount_qualified_item_layers"],
            "raw_mounted_authoring_rows": mounted_closure["raw_mounted_authoring_rows"],
        },
        "rejects": [],
        "reject_set": [],
    }
