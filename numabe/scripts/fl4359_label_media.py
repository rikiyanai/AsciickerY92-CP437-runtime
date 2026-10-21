#!/usr/bin/env python3
"""Name delivered FL-4359 media from receipts without changing image bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from PIL import Image

MODE_DIRS = {
    "FLAT_BG_FLAT_FG": "FF", "CONTINUOUS_BG_FLAT_FG": "FC",
    "FLAT_BG_CONTINUOUS_FG": "CF", "CONTINUOUS_BG_CONTINUOUS_FG": "CC",
}
MAP_NAME = "media_name_map.json"
PUBLICATION_MANIFEST_NAME = "godot_proof_publication.json"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def receipt_for(path, root):
    fallback = ({}, None)
    for parent in path.parents:
        if not parent.is_relative_to(root):
            break
        for name in ("source/receipt.json", "source/arm_receipt.json",
                     "receipt.json", "RAYMARCH_BASELINEreceipt.json"):
            candidate = parent / name
            if candidate.is_file():
                data = read_json(candidate)
                if data.get("run_identity"):
                    return data, candidate
                if fallback[1] is None:
                    fallback = (data, candidate)
    return fallback


def attributes(path, root):
    receipt, evidence = receipt_for(path, root)
    identity = receipt.get("run_identity", {})
    current_set = any(p.startswith(("14_2026-09-08_four-mode", "15_2026-09-08_native"))
                      for p in path.parts)
    baseline = "RAYMARCH_BASELINE" in path.parts
    architecture = "RAYMARCH_BASELINE" if baseline else (
        "TEXEL_SPLAT" if current_set else "ARCH_UNKNOWN")
    mode = next((MODE_DIRS[p] for p in path.parts if p in MODE_DIRS), "MODE_UNKNOWN")
    if mode == "MODE_UNKNOWN":
        mode = next((p.split("-")[0] for p in reversed(path.parts)
                     if re.fullmatch(r"(?:FF|FC|CF|CC)(?:-(?:r|native)\d+)?", p)), mode)
    for selection in receipt.get("selections", []):
        if Path(selection.get("image", "")).name == path.name:
            observed = selection.get("dispatch", {}).get("policy", {}).get("mode")
            if observed in MODE_DIRS.values():
                mode = observed
    native = receipt.get("native_frame_capture", {}).get("frames", [])
    observed_modes = {f.get("dispatch", {}).get("policy", {}).get("mode") for f in native}
    observed_modes.discard(None)
    if len(observed_modes) == 1 and next(iter(observed_modes)) in MODE_DIRS.values():
        mode = next(iter(observed_modes))
    elif len(observed_modes) > 1:
        mode = "MODE_MIXED"
    elif receipt.get("presentation_mode") in MODE_DIRS.values():
        mode = receipt["presentation_mode"]
    cell = identity.get("glyph_matrix_cell_px")
    if cell is None and (current_set or baseline
                        or receipt.get("presentation_profile") == "fl4359_native_square32_perspective_v1"):
        cell = 32
    cell_label = f"{cell}x{cell}" if isinstance(cell, int) and cell > 0 else "UNKNOWN"
    head = identity.get("build_head")
    dirty = identity.get("build_dirty")
    state = "DIRTY" if dirty is True else "CLEAN" if dirty is False else "GIT_UNKNOWN"
    diff = identity.get("build_worktree_diff_sha256") or receipt.get("worktree_diff_sha256")
    sources = [str(evidence)] if evidence else []
    source = "SRC-" + (head[:12] if head else "UNKNOWN")
    if dirty is True:
        source += "__D-" + (diff[:12] if diff else "UNKNOWN")
    patch_sha = None
    if baseline:
        owner = next(p for p in path.parents if (p / "source_provenance.json").is_file())
        provenance = owner / ("source_provenance_r2.json" if "r2" in path.parts else "source_provenance.json")
        data = read_json(provenance)
        head, patch_sha = data["base_commit"], data["patch_sha256"]
        state, mode = "PATCHED", "FF"
        source = f"BASE-{head[:12]}__PATCH-{patch_sha[:12]}"
        sources.append(str(provenance))
    atlas = "atlas_sources" in path.parts
    glyph_source = "UNKNOWN"
    if atlas and re.search(r"(?:page[_-]?0|font)", path.stem, re.I):
        glyph_source = "40x40"
    elif atlas and re.search(r"(?:page[_-]?[12]|additive|morphology)", path.stem, re.I):
        glyph_source = "32x32"
    elif cell == 32 and architecture != "ARCH_UNKNOWN":
        glyph_source = "40-32"
    with Image.open(path) as image:
        dimensions = image.size
        frames = getattr(image, "n_frames", 1)
    return {"architecture": architecture, "mode_fg_bg": mode, "git_state": state,
            "source_commit": head, "dirty_diff_sha256": None if baseline else diff,
            "runner_dirty_diff_sha256": diff if baseline else None, "patch_sha256": patch_sha,
            "source_label": source, "cell_px": cell_label, "glyph_source_px": glyph_source,
            "image_px": list(dimensions), "frame_count": frames, "evidence": sources}


def name_for(path, attrs):
    stem = path.stem.removeprefix("RAYMARCH_BASELINE")
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("_-")
    width, height = attrs["image_px"]
    prefix = (f'{attrs["architecture"]}__{attrs["mode_fg_bg"]}__{attrs["git_state"]}__'
              f'{attrs["source_label"]}__CELL-{attrs["cell_px"]}__'
              f'GLYPHSRC-{attrs["glyph_source_px"]}__IMG-{width}x{height}__')
    suffix = f'__{attrs["frame_count"]}f{path.suffix.lower()}'
    if len((prefix + stem + suffix).encode()) > 220:
        stem = stem[:max(8, 220 - len(prefix) - len(suffix) - 13)] + "-" + sha(path)[:12]
    return prefix + stem + suffix


def label_root(root, apply=False):
    root = root.resolve()
    manifest_path = root / MAP_NAME
    existing = read_json(manifest_path) if manifest_path.is_file() else {"renames": []}
    if "renames" not in existing:
        raise ValueError("This root has a specialized naming map; do not relabel it")
    known_paths = {root / row[key] for row in existing["renames"] for key in ("old_path", "new_path")}
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".gif", ".png") or path in known_paths:
            continue
        attrs = attributes(path, root)
        target = path.with_name(name_for(path, attrs))
        if target.exists() and target != path:
            raise RuntimeError(f"refusing destination collision: {target}")
        rows.append({"old_path": str(path.relative_to(root)), "new_path": str(target.relative_to(root)),
                     "sha256": sha(path), "bytes": path.stat().st_size, "attributes": attrs})
    for row in existing["renames"]:
        current = root / row["new_path"]
        if not current.exists():
            current = root / row["old_path"]
        if sha(current) != row["sha256"]:
            raise RuntimeError("previously named media changed")
    targets = [row["new_path"] for row in existing["renames"] + rows]
    if len(set(targets)) != len(targets):
        raise RuntimeError("multiple sources map to one destination")
    if apply:
        manifest = {"schema": "fl4359.media_names.v1", "root": str(root),
                    "mode_order": "FG/BG; FC means flat FG and continuous BG",
                    "unknown_policy": "UNKNOWN means receipt does not establish this attribute",
                    "original_receipts_modified": False, "image_bytes_modified": False,
                    "renames": existing["renames"] + rows}
        def save_manifest():
            temporary = manifest_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(manifest, indent=2) + "\n")
            temporary.replace(manifest_path)
        # Record every planned path/hash before changing the first filename.
        save_manifest()
        for row in manifest["renames"]:
            source, target = root / row["old_path"], root / row["new_path"]
            if source.exists() and source != target:
                if target.exists():
                    raise RuntimeError("refusing destination collision during apply")
                if sha(source) != row["sha256"]:
                    raise RuntimeError("source changed since planning")
                source.rename(target)
            if sha(target) != row["sha256"]:
                raise RuntimeError("renamed image hash mismatch")
            row["status"] = "renamed_verified"
            save_manifest()
    return {"root": str(root), "new_renames": len(rows), "previous_renames": len(existing["renames"]),
            "apply": apply, "planned": rows if not apply else []}


def resolve_media_path(path):
    """Resolve immutable receipt paths through delivery aliases without changing bytes."""
    path = Path(path)
    if path.exists():
        return path
    requested = path.resolve(strict=False)
    for parent in requested.parents:
        publication = parent / PUBLICATION_MANIFEST_NAME
        if publication.is_file():
            data = read_json(publication)
            if data.get("schema") != "godot-proof-publication.v1":
                raise RuntimeError("published media alias has an unsupported manifest schema")
            root_value = data.get("artifact_root", ".")
            if not isinstance(root_value, str) or not root_value or Path(root_value).is_absolute():
                raise RuntimeError("published media alias has an invalid artifact_root")
            artifact_root = (parent / root_value).resolve()
            if not artifact_root.is_relative_to(parent.resolve()):
                raise RuntimeError("published media alias artifact_root escapes manifest directory")
            try:
                old = str(requested.relative_to(artifact_root))
            except ValueError:
                continue
            for row in data.get("artifacts", []):
                if not isinstance(row, dict):
                    continue
                aliases = row.get("aliases", [])
                if not isinstance(aliases, list):
                    continue
                if old not in aliases:
                    continue
                primary = row.get("path")
                if not isinstance(primary, str) or not primary:
                    raise RuntimeError("published media alias has no canonical path")
                result = (artifact_root / primary).resolve()
                if not result.is_relative_to(artifact_root):
                    raise RuntimeError("published media canonical path escapes artifact_root")
                if not result.is_file():
                    raise FileNotFoundError(result)
                expected_sha = row.get("sha256")
                if not isinstance(expected_sha, str) or re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha) is None:
                    raise RuntimeError("published media alias has no valid canonical hash")
                if sha(result) != expected_sha:
                    raise RuntimeError("published media bytes changed")
                return result
        mapping = parent / MAP_NAME
        if not mapping.is_file():
            continue
        try:
            old = str(requested.relative_to(parent))
        except ValueError:
            continue
        data = read_json(mapping)
        for row in data.get("renames", data.get("mappings", [])):
            if row.get("old_path", row.get("old_relative")) == old:
                result = parent / row.get("new_path", row.get("new_relative"))
                if sha(result) != row["sha256"]:
                    raise RuntimeError("mapped media bytes changed")
                return result
    raise FileNotFoundError(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    for root in args.root:
        print(json.dumps(label_root(root, args.apply)), flush=True)


if __name__ == "__main__":
    main()
