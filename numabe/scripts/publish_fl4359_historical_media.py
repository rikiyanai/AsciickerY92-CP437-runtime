#!/usr/bin/env python3
"""Import reviewed FL-4359 media maps without renaming immutable artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from godot_proof_publication import (
    PublicationError, apply_finder_tags, publish_artifacts, sha256_file,
    validate_manifest, verify_finder_tags,
)


def resolution(value):
    """Decode map fields, never filename tokens or guessed default dimensions."""
    if value in (None, "UNKNOWN"):
        return "UNKNOWN"
    if isinstance(value, list) and len(value) == 2:
        return {"width": value[0], "height": value[1]}
    match = re.fullmatch(r"([1-9][0-9]*)x([1-9][0-9]*)", str(value))
    if not match:
        raise PublicationError(f"unsupported historical resolution: {value!r}")
    return {"width": int(match[1]), "height": int(match[2])}


def historical_metadata(attributes: dict) -> dict:
    """Preserve map-attributed facts while keeping executed-tree gaps explicit."""
    mode = attributes.get("mode_fg_bg", "UNKNOWN")
    modes = {"F": "FLAT", "C": "CONTINUOUS"}
    foreground, background = (
        (modes[mode[0]], modes[mode[1]])
        if mode in {"FF", "FC", "CF", "CC"} else ("UNKNOWN", "UNKNOWN")
    )
    glyphs = attributes.get("glyph_source_px", "UNKNOWN")
    # The reviewed MEDIA_NAMES contract defines two square source atlases.
    glyphs = (
        [{"width": 40, "height": 40}, {"width": 32, "height": 32}]
        if glyphs == "40-32" else resolution(glyphs)
    )
    return {
        "provenance_status": "HISTORICAL_UNKNOWN",
        "fl_id": "FL-4359", "run_id": "UNKNOWN", "proof_kind": "RENDER",
        "architecture": attributes.get("architecture", "UNKNOWN"),
        "foreground_mode": foreground, "background_mode": background,
        "capture_source_state": attributes.get("git_state", "UNKNOWN"),
        "source_commit": attributes.get("source_commit") or "UNKNOWN",
        "effective_tree_fingerprint": "UNKNOWN",
        "cell_resolution": resolution(attributes.get("cell_px")),
        "glyph_source_resolution": glyphs,
        "historical_attributes": attributes,
        "provenance_note": (
            "Imported from the reviewed media map, not inferred from filenames. "
            "A dirty diff or baseline patch is not a complete executed-tree fingerprint. "
            "Capture run identity and runtime build are not reconstructed."
        ),
        "storage": {"status": "PENDING"},
    }


def prepare_map(map_path: Path) -> list[tuple[dict, list[dict]]]:
    """Verify every map target before creating any publication or applying tags."""
    data = json.loads(map_path.read_text())
    groups: dict[str, tuple[dict, list[dict]]] = {}
    if data.get("schema") not in {"fl4359.media_names.v1", "fl4359.grid_media_name_map.v1"}:
        raise PublicationError(f"unsupported map schema: {map_path}")
    grid = data["schema"] == "fl4359.grid_media_name_map.v1"
    if grid:
        for pane in data["panes"].values():
            receipt = Path(pane["receipt_path"])
            if not receipt.is_absolute():
                receipt = map_path.parent / receipt
            if sha256_file(receipt) != pane["receipt_sha256"]:
                raise PublicationError(f"grid pane receipt bytes changed: {receipt}")
    for row in data.get("mappings" if grid else "renames", []):
        target = (map_path.parent / row["new_relative" if grid else "new_path"]).resolve()
        if not target.is_relative_to(map_path.parent.resolve()):
            raise PublicationError(f"map target escapes collection: {target}")
        if sha256_file(target) != row["sha256"] or target.stat().st_size != row["bytes"]:
            raise PublicationError(f"map target bytes changed: {target}")
        if grid:
            attrs = {
                "architecture": data["architecture"], "git_state": "MIXED",
                "cell_px": data["cell_px"], "glyph_source_px": data["glyph_source"],
            }
            metadata = historical_metadata(attrs)
            metadata.update(foreground_mode="MIXED", background_mode="MIXED")
            metadata["panes"] = []
            for mode, pane in data["panes"].items():
                pane_metadata = historical_metadata({
                    **attrs, "mode_fg_bg": mode,
                    "git_state": "DIRTY" if pane["source_dirty"] else "CLEAN",
                    "source_commit": pane["source_commit"],
                })
                pane_metadata.update(pane_id=mode, position=data["pane_positions"][mode],
                                     historical_pane=pane)
                metadata["panes"].append(pane_metadata)
            dimensions = row["dimensions"]
            role = row["role"]
        else:
            attrs = row["attributes"]
            metadata = historical_metadata(attrs)
            dimensions = attrs["image_px"]
            role = Path(row["old_path"]).stem
        metadata["lineage"] = {"media_map": map_path.name,
                               "media_map_sha256": sha256_file(map_path)}
        key = json.dumps(metadata, sort_keys=True)
        if key not in groups:
            groups[key] = (metadata, [])
        groups[key][1].append({
            "path": target, "role": role,
            "frame_count": row.get("frame_count", attrs.get("frame_count")),
            "image_dimensions": {"width": dimensions[0], "height": dimensions[1]},
            "filename_policy": "HISTORICAL_PRESERVED",
        })
    if not groups:
        raise PublicationError(f"empty media map: {map_path}")
    return list(groups.values())


def publish_map(map_path: Path, *, apply_tags: bool = False) -> list[Path]:
    """Create supplemental manifests; original maps, receipts and images stay intact."""
    groups = prepare_map(map_path)
    combined = []
    for metadata, artifacts in groups:
        combined.extend({**artifact, "metadata": metadata} for artifact in artifacts)
    output = map_path.parent / "godot_proof_publication.json"
    collection = historical_metadata({})
    collection["collection_scope"] = "Exact media-map targets; per-artifact capture_metadata owns attributes"
    publish_artifacts(output, collection, combined, artifact_root=map_path.parent,
                      enforce_names=False)
    errors = validate_manifest(output)
    if errors:
        raise PublicationError("; ".join(errors))
    if apply_tags:
        apply_finder_tags(output)
        errors = verify_finder_tags(output)
        if errors:
            raise PublicationError("; ".join(errors))
    return [output]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("maps", nargs="+", type=Path)
    parser.add_argument("--apply-tags", action="store_true")
    args = parser.parse_args()
    try:
        for path in args.maps:
            for manifest in publish_map(path, apply_tags=args.apply_tags):
                print(manifest)
    except (PublicationError, OSError, KeyError, ValueError) as error:
        parser.exit(1, f"historical publication failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
