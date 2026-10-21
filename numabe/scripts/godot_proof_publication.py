#!/usr/bin/env python3
"""FL-4359: publish and verify provenance-safe Godot proof artifacts.

This module owns a publication envelope, not rendering acceptance.  It creates
deterministic names from attested capture metadata, records byte identities,
and restores the Finder tags which Git intentionally does not retain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys
from typing import Any, Iterable, Mapping, Sequence


SCHEMA = "godot-proof-publication.v1"
TAG_ATTRIBUTE = "com.apple.metadata:_kMDItemUserTags"
TAG_NAMESPACE = "GODOT_PROOF:"
UNKNOWN = "UNKNOWN"
NOT_APPLICABLE = "N/A"
ATTESTED = "ATTESTED"
HISTORICAL_UNKNOWN = "HISTORICAL_UNKNOWN"
INCOMPLETE_CURRENT_CAPTURE = "INCOMPLETE_CURRENT_CAPTURE"
_VISUAL_SUFFIXES = {".gif", ".jpeg", ".jpg", ".mov", ".mp4", ".png", ".webm", ".webp"}
_CAPTURE_STATES = {"CLEAN", "DIRTY", "PATCHED"}
_MODES = {"FLAT", "CONTINUOUS", "MIXED", UNKNOWN, NOT_APPLICABLE}
_STORAGE_STATUSES = {"PENDING", "TRACKED", "ARCHIVED"}
_HEX_40_OR_64 = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$", re.IGNORECASE)
_HEX_64 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_REQUIRED_IDENTITY_FIELDS = (
    "fl_id", "run_id", "proof_kind", "architecture", "foreground_mode", "background_mode",
    "capture_source_state", "source_commit", "effective_tree_fingerprint",
)


class PublicationError(ValueError):
    """Raised when an artifact cannot be safely published."""


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 of file bytes without decoding the artifact."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(value: Any, *, limit: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value).strip()).strip("-").upper()
    if not text:
        raise PublicationError("a publication name field is empty")
    return text[:limit]


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PublicationError(f"{field} must be a positive integer")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        result = int(value)
    else:
        raise PublicationError(f"{field} must be a positive integer")
    if result <= 0:
        raise PublicationError(f"{field} must be a positive integer")
    return result


def _dimensions(value: Mapping[str, Any] | None, field: str) -> tuple[int, int]:
    if not isinstance(value, Mapping):
        raise PublicationError(f"{field} must provide width and height")
    width = value.get("width", value.get("w"))
    height = value.get("height", value.get("h"))
    return _integer(width, f"{field}.width"), _integer(height, f"{field}.height")


def _artifact_dimensions(artifact: Mapping[str, Any]) -> tuple[int, int]:
    dimensions = artifact.get("image_dimensions", artifact.get("dimensions"))
    if dimensions is None:
        dimensions = {"width": artifact.get("image_width"), "height": artifact.get("image_height")}
    return _dimensions(dimensions, "artifact image_dimensions")


def _render_dimensions(metadata: Mapping[str, Any], key: str, *, multiple: bool = False) -> tuple[int, int] | list[tuple[int, int]] | None:
    value = metadata.get(key)
    if isinstance(value, str) and value.upper() == UNKNOWN:
        if metadata.get("provenance_status") == ATTESTED:
            raise PublicationError(f"attested metadata.{key} cannot be UNKNOWN")
        return None
    if not multiple:
        return _dimensions(value, f"metadata.{key}")
    values = value if isinstance(value, list) else [value]
    if not values:
        raise PublicationError(f"metadata.{key} must be a non-empty resolution list")
    dimensions = [_dimensions(item, f"metadata.{key}[{index}]") for index, item in enumerate(values)]
    return sorted(set(dimensions), reverse=True)


def _resolution_label(value: tuple[int, int] | list[tuple[int, int]] | None) -> str:
    if value is None:
        return UNKNOWN
    if isinstance(value, tuple):
        return f"{value[0]}x{value[1]}"
    return "+".join(f"{width}x{height}" for width, height in value)


def _is_visual(path: Path, artifact: Mapping[str, Any]) -> bool:
    inferred = path.suffix.lower() in _VISUAL_SUFFIXES
    if "visual" in artifact and bool(artifact["visual"]) != inferred:
        raise PublicationError(f"artifact.visual contradicts path suffix for {path.name}")
    return inferred


def inspect_visual(path: str | Path) -> tuple[int, int, int]:
    """Decode the actual visual dimensions and frame count for publication checks."""
    artifact = Path(path)
    suffix = artifact.suffix.lower()
    if suffix in {".gif", ".jpeg", ".jpg", ".png", ".webp"}:
        try:
            from PIL import Image
        except ImportError as error:
            raise PublicationError("Pillow is required to inspect image proof artifacts") from error
        try:
            with Image.open(artifact) as image:
                return int(image.width), int(image.height), int(getattr(image, "n_frames", 1))
        except (OSError, ValueError) as error:
            raise PublicationError(f"cannot decode visual artifact {artifact}: {error}") from error
    if suffix in {".mov", ".mp4", ".webm"}:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,nb_read_frames", "-of", "json", str(artifact)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            streams = json.loads(result.stdout).get("streams", [])
            stream = streams[0]
            frames = stream.get("nb_read_frames")
            return _integer(stream.get("width"), "ffprobe width"), _integer(stream.get("height"), "ffprobe height"), _integer(frames, "ffprobe frame count")
        except (FileNotFoundError, subprocess.CalledProcessError, IndexError, KeyError, json.JSONDecodeError, PublicationError) as error:
            raise PublicationError(f"cannot inspect video artifact {artifact} with ffprobe: {error}") from error
    raise PublicationError(f"unsupported visual artifact extension: {suffix}")


def _required_text(metadata: Mapping[str, Any], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PublicationError(f"metadata.{key} is required")
    return value.strip()


def _attested_execution_identity(metadata: Mapping[str, Any]) -> None:
    """Require the executed sources/build/workload, not merely host HEAD state."""
    sources = metadata.get("executed_sources")
    if not isinstance(sources, list) or not sources:
        raise PublicationError("attested metadata.executed_sources must list every executed source identity")
    for index, source in enumerate(sources):
        if not isinstance(source, Mapping):
            raise PublicationError(f"executed_sources[{index}] must be an object")
        for key in ("path", "sha256"):
            value = source.get(key)
            if not isinstance(value, str) or not value.strip() or value.upper() == UNKNOWN:
                raise PublicationError(f"executed_sources[{index}].{key} is required")
        if not _HEX_64.fullmatch(str(source["sha256"])):
            raise PublicationError(f"executed_sources[{index}].sha256 must be a SHA-256")
    build = metadata.get("build_identity")
    runtime = metadata.get("runtime_identity")
    workload = metadata.get("workload_identity")
    if not isinstance(build, Mapping):
        raise PublicationError("attested metadata.build_identity is required")
    if not isinstance(runtime, Mapping):
        raise PublicationError("attested metadata.runtime_identity is required")
    if not isinstance(workload, Mapping):
        raise PublicationError("attested metadata.workload_identity is required")
    for name, value, keys in (
        ("build_identity", build, ("kind", "path", "sha256")),
        ("runtime_identity", runtime, ("target", "engine_version")),
        ("workload_identity", workload, ("runner", "scene", "map", "pose", "camera", "timing")),
    ):
        for key in keys:
            field = value.get(key)
            if not isinstance(field, str) or not field.strip() or field.upper() in {UNKNOWN, NOT_APPLICABLE}:
                raise PublicationError(f"attested metadata.{name}.{key} is required")
    if not _HEX_64.fullmatch(str(build["sha256"])):
        raise PublicationError("attested metadata.build_identity.sha256 must be a SHA-256")


def _metadata_for_validation(metadata: Mapping[str, Any], *, allow_mixed: bool = True) -> None:
    status = _required_text(metadata, "provenance_status")
    if status not in {ATTESTED, HISTORICAL_UNKNOWN, INCOMPLETE_CURRENT_CAPTURE}:
        raise PublicationError(
            "metadata.provenance_status must be ATTESTED, HISTORICAL_UNKNOWN, "
            "or INCOMPLETE_CURRENT_CAPTURE"
        )
    for key in ("fl_id", "run_id", "proof_kind"):
        _required_text(metadata, key)
    proof_kind = _required_text(metadata, "proof_kind").upper()
    for key in ("architecture", "foreground_mode", "background_mode",
                "capture_source_state", "source_commit", "effective_tree_fingerprint"):
        _required_text(metadata, key)
    foreground = str(metadata["foreground_mode"]).upper()
    background = str(metadata["background_mode"]).upper()
    if foreground not in _MODES or background not in _MODES:
        raise PublicationError("foreground_mode and background_mode must be FLAT, CONTINUOUS, MIXED, or N/A")
    mixed = foreground == "MIXED" or background == "MIXED" or str(metadata["architecture"]).upper() == "MIXED"
    if mixed and not allow_mixed:
        raise PublicationError("a pane cannot itself be MIXED")
    if proof_kind == "RENDER" and (foreground == NOT_APPLICABLE or background == NOT_APPLICABLE):
        raise PublicationError("RENDER metadata requires explicit foreground and background modes")
    if proof_kind == "RENDER":
        _render_dimensions(metadata, "cell_resolution")
        _render_dimensions(metadata, "glyph_source_resolution", multiple=True)
    if status == ATTESTED:
        prohibited = {UNKNOWN, NOT_APPLICABLE, ""}
        for key in ("architecture", "capture_source_state", "source_commit", "effective_tree_fingerprint"):
            if str(metadata[key]).upper() in prohibited:
                raise PublicationError(f"attested metadata.{key} cannot be UNKNOWN or N/A")
        if proof_kind == "RENDER" and (foreground == UNKNOWN or background == UNKNOWN):
            raise PublicationError("attested RENDER metadata cannot use UNKNOWN foreground or background modes")
        if str(metadata["capture_source_state"]).upper() not in _CAPTURE_STATES:
            raise PublicationError("attested capture_source_state must be CLEAN, DIRTY, or PATCHED")
        if not _HEX_40_OR_64.fullmatch(str(metadata["source_commit"])):
            raise PublicationError("attested source_commit must be a 40- or 64-character hex commit")
        if not _HEX_64.fullmatch(str(metadata["effective_tree_fingerprint"])):
            raise PublicationError("attested effective_tree_fingerprint must be a SHA-256")
        _attested_execution_identity(metadata)
    else:
        # Historical records are preservation metadata, never reconstructed proof.
        for key in ("architecture", "capture_source_state", "source_commit", "effective_tree_fingerprint"):
            if str(metadata[key]).upper() == NOT_APPLICABLE:
                raise PublicationError(f"unattested metadata.{key} must be UNKNOWN, not N/A")
    if mixed:
        panes = metadata.get("panes")
        if not isinstance(panes, list) or not panes:
            raise PublicationError("mixed metadata requires a non-empty panes list")
        for index, pane in enumerate(panes):
            if not isinstance(pane, Mapping):
                raise PublicationError(f"panes[{index}] must be an object")
            _required_text(pane, "pane_id")
            missing = [key for key in _REQUIRED_IDENTITY_FIELDS if key not in pane]
            if missing:
                raise PublicationError(f"panes[{index}] must state its own metadata: {', '.join(missing)}")
            merged = dict(pane)
            merged["provenance_status"] = metadata["provenance_status"]
            merged["panes"] = []
            _metadata_for_validation(merged, allow_mixed=False)


def _metadata_tags(metadata: Mapping[str, Any], *, role: str, frame_count: int,
                   width: int | None, height: int | None) -> list[str]:
    tags = [
        f"{TAG_NAMESPACE}{_slug(metadata['fl_id'], limit=32)}",
        f"{TAG_NAMESPACE}ARCH-{_slug(metadata['architecture'])}",
        f"{TAG_NAMESPACE}FG-{_slug(metadata['foreground_mode'])}",
        f"{TAG_NAMESPACE}BG-{_slug(metadata['background_mode'])}",
        f"{TAG_NAMESPACE}STATE-{_slug(metadata['capture_source_state'])}",
        f"{TAG_NAMESPACE}SRC-{_slug(metadata['source_commit'], limit=12)}",
        f"{TAG_NAMESPACE}TREE-{_slug(metadata['effective_tree_fingerprint'], limit=12)}",
        f"{TAG_NAMESPACE}ROLE-{_slug(role)}",
        f"{TAG_NAMESPACE}FRAMES-{frame_count}",
        f"{TAG_NAMESPACE}RUN-{_slug(metadata['run_id'], limit=32)}",
    ]
    if str(metadata["foreground_mode"]).upper() == "MIXED" or str(metadata["background_mode"]).upper() == "MIXED":
        tags.append(f"{TAG_NAMESPACE}MODE-MIXED")
    if str(metadata.get("proof_kind", "")).upper() == "RENDER":
        cell = _render_dimensions(metadata, "cell_resolution")
        glyph = _render_dimensions(metadata, "glyph_source_resolution", multiple=True)
        tags.extend((
            f"{TAG_NAMESPACE}CELL-{_resolution_label(cell).upper()}",
            f"{TAG_NAMESPACE}GLYPH-{_resolution_label(glyph).upper()}",
        ))
    if width is not None and height is not None:
        tags.append(f"{TAG_NAMESPACE}IMG-{width}X{height}")
    return tags


def deterministic_artifact_name(metadata: Mapping[str, Any], artifact: Mapping[str, Any]) -> str:
    """Generate the canonical filename for one finalized artifact.

    This function has no file-system side effects.  Producers write the returned
    name; legacy consumers use separately recorded aliases.
    """
    _metadata_for_validation(metadata)
    role = _slug(_required_text(artifact, "role"))
    frame_count = _integer(artifact.get("frame_count"), "artifact.frame_count")
    extension = str(artifact.get("extension") or Path(str(artifact.get("path", ""))).suffix).lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
        raise PublicationError("artifact must provide a conventional lowercase extension")
    if _is_visual(Path("x" + extension), artifact):
        width, height = _artifact_dimensions(artifact)
        dimensions = f"__IMG-{width}x{height}"
    else:
        dimensions = ""
    if str(metadata["proof_kind"]).upper() == "RENDER":
        cell = _render_dimensions(metadata, "cell_resolution")
        glyph = _render_dimensions(metadata, "glyph_source_resolution", multiple=True)
        cell_label = _resolution_label(cell)
        glyph_label = _resolution_label(glyph)
        render_fields = f"__CELL-{cell_label}__GLYPH-{glyph_label}"
    else:
        render_fields = ""
    name = (
        f"{_slug(metadata['fl_id'], limit=32)}__RUN-{_slug(metadata['run_id'], limit=32)}"
        f"__ARCH-{_slug(metadata['architecture'])}__FG-{_slug(metadata['foreground_mode'])}"
        f"__BG-{_slug(metadata['background_mode'])}__STATE-{_slug(metadata['capture_source_state'])}"
        f"__SRC-{_slug(metadata['source_commit'], limit=12)}"
        f"__TREE-{_slug(metadata['effective_tree_fingerprint'], limit=12)}{render_fields}{dimensions}"
        f"__ROLE-{role}__{frame_count}f{extension}"
    )
    encoded = name.encode("utf-8")
    if len(encoded) <= 240:
        return name
    raise PublicationError("canonical artifact name exceeds 240 bytes; shorten run_id or role without dropping provenance fields")


def _effective_metadata(base: Mapping[str, Any], artifact: Mapping[str, Any]) -> dict[str, Any]:
    override = artifact.get("metadata")
    if override is None:
        return dict(base)
    if not isinstance(override, Mapping):
        raise PublicationError("artifact.metadata must be an object")
    missing = [key for key in _REQUIRED_IDENTITY_FIELDS if key not in override]
    if missing:
        raise PublicationError("artifact.metadata must be complete, without inherited capture identity: " + ", ".join(missing))
    effective = dict(override)
    # Storage is delivery metadata, not capture identity, and may be shared.
    if "storage" not in effective and "storage" in base:
        effective["storage"] = base["storage"]
    _metadata_for_validation(effective)
    return effective


def _artifact_record(metadata: Mapping[str, Any], artifact: Mapping[str, Any], root: Path,
                     enforce_names: bool) -> dict[str, Any]:
    metadata = _effective_metadata(metadata, artifact)
    path_value = artifact.get("path")
    if not isinstance(path_value, (str, Path)):
        raise PublicationError("artifact.path is required")
    path = Path(path_value).resolve()
    if not path.is_file():
        raise PublicationError(f"artifact does not exist: {path}")
    try:
        relative_path = path.relative_to(root)
    except ValueError as error:
        raise PublicationError(f"artifact is outside artifact_root: {path}") from error
    visual = _is_visual(path, artifact)
    width = height = None
    if visual:
        width, height = _artifact_dimensions(artifact)
        actual_width, actual_height, actual_frames = inspect_visual(path)
        if (width, height) != (actual_width, actual_height):
            raise PublicationError(f"artifact image_dimensions do not match decoded bytes: {path}")
        if _integer(artifact.get("frame_count"), "artifact.frame_count") != actual_frames:
            raise PublicationError(f"artifact frame_count does not match decoded bytes: {path}")
    elif "image_dimensions" in artifact or "dimensions" in artifact:
        raise PublicationError("nonvisual artifact cannot claim image dimensions")
    expected_name = deterministic_artifact_name(metadata, artifact)
    policy = str(artifact.get("filename_policy", "CANONICAL"))
    if policy not in {"CANONICAL", "HISTORICAL_PRESERVED"}:
        raise PublicationError("artifact.filename_policy must be CANONICAL or HISTORICAL_PRESERVED")
    if policy == "HISTORICAL_PRESERVED" and metadata["provenance_status"] != HISTORICAL_UNKNOWN:
        raise PublicationError("HISTORICAL_PRESERVED is only valid for HISTORICAL_UNKNOWN artifacts")
    if enforce_names and policy == "CANONICAL" and path.name != expected_name:
        raise PublicationError(f"artifact path must use canonical filename: expected {expected_name}")
    aliases: list[str] = []
    aliases_value = artifact.get("aliases", [])
    if not isinstance(aliases_value, list):
        raise PublicationError("artifact.aliases must be a list")
    canonical_sha256 = sha256_file(path)
    for alias_value in aliases_value:
        if not isinstance(alias_value, (str, Path)):
            raise PublicationError("artifact aliases must be paths")
        alias = Path(alias_value).resolve()
        try:
            alias_relative = alias.relative_to(root)
        except ValueError as error:
            raise PublicationError(f"alias is outside artifact_root: {alias}") from error
        if alias == path:
            raise PublicationError("artifact alias duplicates its canonical path")
        if not alias.is_file():
            raise PublicationError(f"artifact alias is missing: {alias}")
        if sha256_file(alias) != canonical_sha256:
            raise PublicationError(f"artifact alias differs from canonical bytes: {alias}")
        aliases.append(str(alias_relative))
    role = _required_text(artifact, "role")
    frames = _integer(artifact.get("frame_count"), "artifact.frame_count")
    record: dict[str, Any] = {
        "path": str(relative_path),
        "filename": path.name,
        "expected_filename": expected_name,
        "filename_policy": policy,
        "role": role,
        "visual": visual,
        "frame_count": frames,
        "size_bytes": path.stat().st_size,
        "sha256": canonical_sha256,
        "aliases": aliases,
        "capture_metadata": metadata,
        "expected_finder_tags": _metadata_tags(metadata, role=role, frame_count=frames,
                                                 width=width, height=height),
    }
    if visual:
        record["image_dimensions"] = {"width": width, "height": height}
    return record


def build_manifest(metadata: Mapping[str, Any], artifacts: Sequence[Mapping[str, Any]], *,
                   artifact_root: str | Path, enforce_names: bool | None = None) -> dict[str, Any]:
    """Build a manifest from finalized files; this does not write or rename files."""
    _metadata_for_validation(metadata)
    if not artifacts:
        raise PublicationError("at least one artifact is required")
    root = Path(artifact_root).resolve()
    if not root.is_dir():
        raise PublicationError(f"artifact_root is not a directory: {root}")
    if enforce_names is None:
        enforce_names = metadata["provenance_status"] != HISTORICAL_UNKNOWN
    records = [_artifact_record(metadata, artifact, root, enforce_names) for artifact in artifacts]
    paths = [record["path"] for record in records]
    if len(paths) != len(set(paths)):
        raise PublicationError("an artifact is listed more than once")
    return {
        "schema": SCHEMA,
        "publication_scope": "artifact_provenance_and_delivery",
        "renderer_acceptance": "NOT_EVALUATED",
        "metadata": dict(metadata),
        "artifact_root": ".",
        "artifacts": records,
    }


def write_manifest(path: str | Path, manifest: Mapping[str, Any]) -> Path:
    """Atomically write a deterministic JSON manifest."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    return output


def publish_artifacts(manifest_path: str | Path, metadata: Mapping[str, Any],
                      artifacts: Sequence[Mapping[str, Any]], *, artifact_root: str | Path | None = None,
                      enforce_names: bool | None = None) -> dict[str, Any]:
    """Write the publication manifest for finalized paths without moving or tagging them."""
    manifest_path = Path(manifest_path)
    root = Path(artifact_root) if artifact_root is not None else manifest_path.parent
    manifest = build_manifest(metadata, artifacts, artifact_root=root, enforce_names=enforce_names)
    manifest["artifact_root"] = os.path.relpath(Path(root).resolve(), manifest_path.parent.resolve())
    write_manifest(manifest_path, manifest)
    return manifest


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PublicationError(f"cannot read manifest {manifest_path}: {error}") from error
    if not isinstance(value, dict):
        raise PublicationError("manifest root must be an object")
    return value


def _storage_errors(metadata: Mapping[str, Any], require_storage: bool) -> list[str]:
    storage = metadata.get("storage")
    if not isinstance(storage, Mapping):
        return ["metadata.storage is required"] if require_storage else []
    status = storage.get("status")
    if status not in _STORAGE_STATUSES:
        return ["metadata.storage.status must be PENDING, TRACKED, or ARCHIVED"]
    if require_storage and status == "PENDING":
        return ["storage is still PENDING"]
    if status != "PENDING":
        errors = []
        for key in ("location", "failure_log", "godot_goal", "receipt_path", "receipt_sha256"):
            if not isinstance(storage.get(key), str) or not storage[key].strip():
                errors.append(f"metadata.storage.{key} is required for {status}")
        if isinstance(storage.get("receipt_sha256"), str) and not _HEX_64.fullmatch(storage["receipt_sha256"]):
            errors.append("metadata.storage.receipt_sha256 must be a SHA-256")
        return errors
    return []


def _storage_evidence_errors(manifest_path: Path, metadata: Mapping[str, Any],
                             artifacts: Sequence[Mapping[str, Any]], root: Path) -> list[str]:
    storage = metadata.get("storage")
    if not isinstance(storage, Mapping) or storage.get("status") == "PENDING":
        return []
    errors: list[str] = []
    receipt = Path(str(storage.get("receipt_path", "")))
    if not receipt.is_absolute():
        receipt = (manifest_path.parent / receipt).resolve()
    if not receipt.is_file():
        errors.append(f"storage receipt is missing: {receipt}")
    elif sha256_file(receipt) != storage.get("receipt_sha256"):
        errors.append(f"storage receipt hash differs: {receipt}")
    location = Path(str(storage.get("location", "")))
    if not location.is_absolute():
        location = (manifest_path.parent / location).resolve()
    if not location.exists():
        errors.append(f"storage location is missing: {location}")
    if storage.get("status") == "ARCHIVED":
        members = storage.get("archive_members")
        if not isinstance(members, list) or len(members) != len(artifacts):
            errors.append("ARCHIVED storage requires one verified archive_members entry per artifact")
        else:
            by_artifact = {str(artifact.get("path", "")): artifact for artifact in artifacts}
            seen_members: set[str] = set()
            for index, member in enumerate(members):
                prefix = f"archive_members[{index}]"
                if not isinstance(member, Mapping):
                    errors.append(f"{prefix} must be an object")
                    continue
                artifact_path = member.get("artifact_path")
                archived_path = member.get("archived_path")
                if not isinstance(artifact_path, str) or artifact_path not in by_artifact:
                    errors.append(f"{prefix}.artifact_path is not a manifest artifact")
                    continue
                if not isinstance(archived_path, str) or not archived_path:
                    errors.append(f"{prefix}.archived_path is required")
                    continue
                if artifact_path in seen_members:
                    errors.append(f"{prefix}.artifact_path is duplicated")
                    continue
                seen_members.add(artifact_path)
                archived_file = (location / archived_path).resolve()
                try:
                    archived_file.relative_to(location)
                except ValueError:
                    errors.append(f"{prefix}.archived_path escapes storage location")
                    continue
                source = by_artifact[artifact_path]
                if not archived_file.is_file():
                    errors.append(f"{prefix}.archived artifact is missing: {archived_path}")
                elif sha256_file(archived_file) != source.get("sha256"):
                    errors.append(f"{prefix}.archived artifact hash differs: {archived_path}")
    if storage.get("status") == "TRACKED":
        repo = Path(__file__).resolve().parents[1]
        tracked_paths = [manifest_path]
        tracked_paths.extend((root / str(artifact.get("path", ""))).resolve() for artifact in artifacts)
        for candidate in tracked_paths:
            try:
                repository_path = candidate.relative_to(repo)
            except ValueError:
                errors.append(f"tracked artifact is outside repository: {candidate}")
                continue
            result = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", str(repository_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                errors.append(f"artifact is not tracked by Git: {repository_path}")
                continue
            index = subprocess.run(
                ["git", "-C", str(repo), "ls-files", "-s", "--", str(repository_path)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            indexed_blob = index.stdout.split()[1] if len(index.stdout.split()) >= 2 else ""
            worktree_blob = subprocess.run(
                ["git", "-C", str(repo), "hash-object", str(repository_path)],
                check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ).stdout.strip()
            if indexed_blob != worktree_blob:
                errors.append(f"tracked artifact bytes differ from indexed Git blob: {repository_path}")
    return errors


def validate_manifest(manifest_path: str | Path, *, require_storage: bool = False,
                      allow_incomplete: bool = False) -> list[str]:
    """Return publication errors only; renderer acceptance is intentionally excluded."""
    errors: list[str] = []
    path = Path(manifest_path).resolve()
    try:
        manifest = load_manifest(path)
    except PublicationError as error:
        return [str(error)]
    if manifest.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if manifest.get("renderer_acceptance") != "NOT_EVALUATED":
        errors.append("publication manifest must not assert renderer acceptance")
    metadata = manifest.get("metadata")
    if not isinstance(metadata, Mapping):
        return errors + ["metadata must be an object"]
    try:
        _metadata_for_validation(metadata)
    except PublicationError as error:
        errors.append(str(error))
    if metadata.get("provenance_status") == INCOMPLETE_CURRENT_CAPTURE and not allow_incomplete:
        errors.append("INCOMPLETE_CURRENT_CAPTURE is diagnostic only, not a publishable proof")
    errors.extend(_storage_errors(metadata, require_storage))
    root_value = manifest.get("artifact_root", ".")
    if not isinstance(root_value, str) or Path(root_value).is_absolute():
        return errors + ["artifact_root must be a relative path"]
    root = (path.parent / root_value).resolve()
    if not root.is_dir():
        return errors + [f"artifact_root does not exist: {root}"]
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return errors + ["artifacts must be a non-empty list"]
    seen: set[str] = set()
    for index, artifact in enumerate(artifacts):
        prefix = f"artifacts[{index}]"
        if not isinstance(artifact, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        relative = artifact.get("path")
        if not isinstance(relative, str) or not relative:
            errors.append(f"{prefix}.path is required")
            continue
        if relative in seen:
            errors.append(f"{prefix}.path is duplicated")
            continue
        seen.add(relative)
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"{prefix}.path escapes artifact_root")
            continue
        if not candidate.is_file():
            errors.append(f"{prefix}.path is missing: {relative}")
            continue
        if candidate.stat().st_size != artifact.get("size_bytes"):
            errors.append(f"{prefix}.size_bytes does not match {relative}")
        if sha256_file(candidate) != artifact.get("sha256"):
            errors.append(f"{prefix}.sha256 does not match {relative}")
        if not isinstance(artifact.get("capture_metadata"), Mapping):
            errors.append(f"{prefix}.capture_metadata must be a complete object")
            continue
        try:
            capture_metadata = _effective_metadata({}, {"metadata": artifact["capture_metadata"]})
        except PublicationError as error:
            errors.append(f"{prefix}.capture_metadata invalid: {error}")
            continue
        if capture_metadata.get("provenance_status") == INCOMPLETE_CURRENT_CAPTURE and not allow_incomplete:
            errors.append(f"{prefix}.capture_metadata is INCOMPLETE_CURRENT_CAPTURE, not publishable proof")
        try:
            expected_name = deterministic_artifact_name(capture_metadata, artifact)
            actual_visual = _is_visual(candidate, artifact)
        except PublicationError as error:
            errors.append(f"{prefix} identity invalid: {error}")
            continue
        if artifact.get("expected_filename") != expected_name:
            errors.append(f"{prefix}.expected_filename is not recomputed from metadata")
        if artifact.get("filename") != candidate.name:
            errors.append(f"{prefix}.filename does not match actual path")
        if actual_visual:
            try:
                actual_width, actual_height, actual_frames = inspect_visual(candidate)
            except PublicationError as error:
                errors.append(f"{prefix} visual inspection failed: {error}")
            else:
                if artifact.get("image_dimensions") != {"width": actual_width, "height": actual_height}:
                    errors.append(f"{prefix}.image_dimensions do not match decoded bytes")
                if artifact.get("frame_count") != actual_frames:
                    errors.append(f"{prefix}.frame_count does not match decoded bytes")
        elif "image_dimensions" in artifact:
            errors.append(f"{prefix}.image_dimensions declared for nonvisual artifact")
        try:
            width, height = _artifact_dimensions(artifact) if actual_visual else (None, None)
            expected_tags = _metadata_tags(
                capture_metadata,
                role=_required_text(artifact, "role"),
                frame_count=_integer(artifact.get("frame_count"), f"{prefix}.frame_count"),
                width=width,
                height=height,
            )
            if artifact.get("expected_finder_tags") != expected_tags:
                errors.append(f"{prefix}.expected_finder_tags are not recomputed from metadata")
        except PublicationError as error:
            errors.append(f"{prefix} tag identity invalid: {error}")
        policy = artifact.get("filename_policy")
        if policy not in {"CANONICAL", "HISTORICAL_PRESERVED"}:
            errors.append(f"{prefix}.filename_policy is invalid")
        if policy == "CANONICAL" and candidate.name != artifact.get("expected_filename"):
            errors.append(f"{prefix}.filename is not canonical")
        if policy == "HISTORICAL_PRESERVED" and capture_metadata.get("provenance_status") != HISTORICAL_UNKNOWN:
            errors.append(f"{prefix}.HISTORICAL_PRESERVED requires HISTORICAL_UNKNOWN")
        if not isinstance(artifact.get("expected_finder_tags"), list):
            errors.append(f"{prefix}.expected_finder_tags must be a list")
        aliases = artifact.get("aliases", [])
        if not isinstance(aliases, list):
            errors.append(f"{prefix}.aliases must be a list")
        else:
            for alias in aliases:
                if not isinstance(alias, str):
                    errors.append(f"{prefix}.alias must be a relative string")
                    continue
                alias_path = (root / alias).resolve()
                try:
                    alias_path.relative_to(root)
                except ValueError:
                    errors.append(f"{prefix}.alias escapes artifact_root: {alias}")
                    continue
                if not alias_path.is_file():
                    errors.append(f"{prefix}.alias is missing: {alias}")
                elif sha256_file(alias_path) != artifact.get("sha256"):
                    errors.append(f"{prefix}.alias differs from canonical bytes: {alias}")
    if require_storage:
        errors.extend(_storage_evidence_errors(path, metadata, artifacts, root))
    return errors


def _read_finder_tag_values(path: Path) -> list[str]:
    if hasattr(os, "getxattr"):
        try:
            raw = os.getxattr(path, TAG_ATTRIBUTE)
        except OSError as error:
            # ENODATA is 61 on macOS; some file systems report ENOATTR instead.
            if error.errno in {getattr(os, "ENOATTR", 93), 61}:
                return []
            raise PublicationError(f"cannot read Finder tags from {path}: {error}") from error
    else:
        result = subprocess.run(
            ["xattr", "-px", TAG_ATTRIBUTE, str(path)], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            if "No such xattr" in result.stderr or "not found" in result.stderr.lower():
                return []
            raise PublicationError(f"cannot read Finder tags from {path}: {result.stderr.strip()}")
        try:
            raw = bytes.fromhex("".join(result.stdout.split()))
        except ValueError as error:
            raise PublicationError(f"xattr returned non-hex Finder tags for {path}") from error
    try:
        values = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError) as error:
        raise PublicationError(f"Finder tags on {path} are not a plist; refusing to overwrite them") from error
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise PublicationError(f"Finder tags on {path} are not a string array; refusing to overwrite them")
    return list(values)


def _tag_name(value: str) -> str:
    return value.split("\n", 1)[0]


def finder_tags(path: str | Path) -> list[str]:
    """Read Finder tag names, omitting Finder's private color suffix."""
    return [_tag_name(value) for value in _read_finder_tag_values(Path(path))]


def _write_finder_tags(path: Path, values: Iterable[str]) -> None:
    encoded = plistlib.dumps(list(values), fmt=plistlib.FMT_BINARY)
    if not hasattr(os, "setxattr"):
        result = subprocess.run(
            ["xattr", "-wx", TAG_ATTRIBUTE, encoded.hex(), str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        if result.returncode != 0:
            raise PublicationError(f"cannot write Finder tags to {path}: {result.stderr.strip()}")
        return
    try:
        os.setxattr(path, TAG_ATTRIBUTE, encoded)
    except OSError as error:
        raise PublicationError(f"cannot write Finder tags to {path}: {error}") from error


def _manifest_artifacts(manifest_path: Path, selected: Sequence[str | Path] | None) -> list[tuple[Path, Mapping[str, Any]]]:
    """Return physical canonical and alias paths selected from a valid manifest.

    Git does not preserve Finder tags and an alias can become an independent copy
    after checkout or archive extraction.  It therefore needs the same tag
    restore/readback treatment as its canonical sibling.  A supplied path selects
    that one physical file; no selection restores every canonical and alias path.
    """
    manifest = load_manifest(manifest_path)
    root = (manifest_path.parent / str(manifest.get("artifact_root", "."))).resolve()
    candidates: list[tuple[str, Path, Mapping[str, Any]]] = []
    seen_paths: set[Path] = set()
    for artifact in manifest.get("artifacts", []):
        if not isinstance(artifact, Mapping):
            continue
        relative_paths = [artifact.get("path"), *artifact.get("aliases", [])]
        for relative in relative_paths:
            if not isinstance(relative, str) or not relative:
                continue
            artifact_path = (root / relative).resolve()
            # `validate_manifest` performs the authoritative containment and
            # byte checks before this helper is used by tag apply/verify.
            if artifact_path in seen_paths:
                continue
            seen_paths.add(artifact_path)
            candidates.append((relative, artifact_path, artifact))
    if selected is None:
        return [(artifact_path, artifact) for _, artifact_path, artifact in candidates]

    result: list[tuple[Path, Mapping[str, Any]]] = []
    selected_paths: set[Path] = set()
    for requested_value in selected:
        requested = Path(requested_value)
        requested_candidates = {str(requested), requested.as_posix()}
        if requested.is_absolute():
            requested_candidates.add(str(requested.resolve()))
        else:
            requested_candidates.add(str((root / requested).resolve()))
        matches = [
            (artifact_path, artifact)
            for relative, artifact_path, artifact in candidates
            if relative in requested_candidates
            or relative.replace("\\", "/") in requested_candidates
            or str(artifact_path) in requested_candidates
        ]
        if not matches:
            raise PublicationError(f"requested artifact is absent from manifest: {requested_value}")
        for artifact_path, artifact in matches:
            if artifact_path not in selected_paths:
                selected_paths.add(artifact_path)
                result.append((artifact_path, artifact))
    return result


def apply_finder_tags(manifest_path: str | Path, *, artifact_paths: Sequence[str | Path] | None = None) -> dict[str, list[str]]:
    """Apply only this publisher's tags, preserving all unrelated Finder tags."""
    path = Path(manifest_path)
    # Tagging is a preservation operation. An explicitly incomplete capture still
    # gets its tags, but cannot pass normal publication validation.
    errors = validate_manifest(path, allow_incomplete=True)
    if errors:
        raise PublicationError("cannot tag invalid publication manifest: " + "; ".join(errors))
    result: dict[str, list[str]] = {}
    for artifact_path, artifact in _manifest_artifacts(path, artifact_paths):
        before_hash = sha256_file(artifact_path)
        existing = _read_finder_tag_values(artifact_path)
        unrelated = [tag for tag in existing if not _tag_name(tag).startswith(TAG_NAMESPACE)]
        expected = artifact["expected_finder_tags"]
        _write_finder_tags(artifact_path, unrelated + [f"{tag}\n0" for tag in expected])
        if sha256_file(artifact_path) != before_hash:
            raise PublicationError(f"tagging unexpectedly changed file bytes: {artifact_path}")
        observed = finder_tags(artifact_path)
        missing = [tag for tag in expected if tag not in observed]
        if missing:
            raise PublicationError(f"Finder tag readback failed for {artifact_path}: {missing}")
        result[str(artifact_path)] = observed
    return result


def verify_finder_tags(manifest_path: str | Path, *, artifact_paths: Sequence[str | Path] | None = None) -> list[str]:
    """Return missing-tag or changed-byte errors after checkout/archive restoration."""
    path = Path(manifest_path)
    errors = validate_manifest(path, allow_incomplete=True)
    if errors:
        return errors
    for artifact_path, artifact in _manifest_artifacts(path, artifact_paths):
        if not artifact_path.is_file():
            errors.append(f"artifact is missing: {artifact_path}")
            continue
        expected = artifact.get("expected_finder_tags", [])
        observed = finder_tags(artifact_path)
        for tag in expected:
            if tag not in observed:
                errors.append(f"Finder tag missing from {artifact_path}: {tag}")
        if sha256_file(artifact_path) != artifact.get("sha256"):
            errors.append(f"file bytes differ from manifest after tag operation: {artifact_path}")
    return errors


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("action", choices=("validate", "tag-apply", "tag-verify"))
    parser.add_argument("--require-storage", action="store_true")
    parser.add_argument("--allow-incomplete", action="store_true",
                        help="inspect an incomplete current capture without treating it as proof")
    parser.add_argument("--artifact", action="append", default=None,
                        help="relative artifact path in the manifest; may be repeated")
    args = parser.parse_args()
    if args.action == "validate":
        errors = validate_manifest(
            args.manifest,
            require_storage=args.require_storage,
            allow_incomplete=args.allow_incomplete,
        )
        print(json.dumps({"manifest": str(args.manifest), "valid": not errors, "errors": errors}, indent=2))
        return 0 if not errors else 1
    if args.action == "tag-apply":
        try:
            result = apply_finder_tags(args.manifest, artifact_paths=args.artifact)
        except PublicationError as error:
            print(json.dumps({"applied": False, "error": str(error)}, indent=2))
            return 1
        print(json.dumps({"applied": True, "artifacts": result}, indent=2))
        return 0
    errors = verify_finder_tags(args.manifest, artifact_paths=args.artifact)
    print(json.dumps({"verified": not errors, "errors": errors}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(_main())
