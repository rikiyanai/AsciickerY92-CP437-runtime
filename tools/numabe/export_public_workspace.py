#!/usr/bin/env python3
"""Export Numabe's runnable/editable closure, not raw survey/build archives.

Operator 2026-10-21 permits excluding inputs unnecessary for the saved map and
editors. Raw PLATEAU/airborne point-cloud acquisitions are rebuild inputs, not
runtime dependencies. Originals remain untouched. This utility performs no Git
operations and fails before copying if a required file needs sharding.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

CACHE_SUFFIXES = {".o", ".a", ".bc", ".ii", ".s", ".i", ".pyc", ".pyo", ".log"}
CACHE_PARTS = {"__pycache__", ".git", "node_modules", ".pytest_cache", "obj"}
ROOT_TREES = (
    "engine", "platform", "server", "net", "shared", "vendor", "editor", "web",
    "apps/classic-runtime", "apps/game-classic", "addons/io_asciicker/scene",
    "assets/meshes", "assets/sprites", "assets/palettes", "assets/images",
    "assets/fonts", "assets/glyphs", "assets/actor_visual_profiles", "assets/appearance_bundle",
    "assets/world_templates", "assets/world_mesh_src", "assets/a3d",
    "assets/public_realm_corpora", "config", "scripts/pipeline",
)


def sha(path):
    """Hash bounded chunks, without retaining complete large assets in memory."""
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(8 << 20), b""):
            h.update(data)
    return h.hexdigest()


def main():
    """Select the supported closure, check limits, copy, and bind exported bytes."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path)
    ap.add_argument("destination", type=Path)
    args = ap.parse_args()
    source, destination = args.source.resolve(), args.destination.resolve()
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite existing export: {destination}")
    selected = set()

    def add(path):
        if not path.is_file() or path.suffix in CACHE_SUFFIXES or set(path.parts) & CACHE_PARTS:
            return
        name = str(path.relative_to(source))
        if name == "scripts/recover_ephemeral_agent_docs.py":
            return  # Private maintenance utility, never a map/editor dependency.
        if name.startswith(("assets/glyphs/generated/", "assets/meshes/quarantine/",
                            "web/asciiid-authoring-shell/")):
            return
        selected.add(path.relative_to(source))

    def tree(path):
        for item in path.rglob("*"):
            add(item)

    for root in ROOT_TREES:
        tree(source / root)
    for root in sorted((source / "assets/source_bundles").glob("numabe*")):
        for item in root.rglob("*"):
            # These are acquisition/reconstruction inputs. The saved native
            # map, meshes, heights and metadata no longer read these datasets.
            relative = item.relative_to(root)
            if relative.parts and relative.parts[0] in {"plateau", "tmg_pointcloud"}:
                continue
            add(item)
    for item in (source / "assets/source_bundles").glob("*/*survey*.json"):
        add(item)
    manual = source / "artifacts/experiments/fl4876_manual_world"
    for item in manual.iterdir():
        if item.is_file() and item.suffix in {".py", ".json"}:
            add(item)
    for folder in ("map_editor_ui", "facade_capture_checks", "aerial", "map_edits"):
        tree(manual / folder)
    for item in (manual / "operator_map_review").iterdir():
        if item.is_file() and item.suffix in {".json", ".jsonl"}:
            add(item)
    # Current Mapillary panorama/crop references are licensed source material;
    # private operator-pasted photos and historic assessment screenshots are
    # not part of the public runtime/editor closure.
    for folder in ("fl4810_aoi_street_network_boxing_20260915", "fl4813_capture_review_20260915"):
        base = source / "artifacts/experiments" / folder
        for item in base.glob("*.json"):
            add(item)
        for leaf in ("captures", "crops"):
            tree(base / leaf)
        if folder == "fl4813_capture_review_20260915":
            tree(base / "preserved/operator_boxes_complete_20260915")
    build = source / ".run/fl4876_builds/live_numabe"
    for item in build.iterdir():
        if item.is_file() and (item.suffix in {".json", ".geojson"}
                               or item.name in {"output.a3d", "output.a3d.pre_edit_d40d35d4"}):
            add(item)
    for leaf in ("meshes", "output.sprites", "plan"):
        tree(build / leaf)
    for name in ("index.js", "index.wasm", "index.data", "index.html"):
        add(source / ".web-classic" / name)
    for item in (source / "scripts").glob("*.py"):
        add(item)
    for item in (source / "scripts/tests").glob("*.py"):
        add(item)
    tree(source / "docs/agent/cli-anything")
    tree(source / "docs/research/ascii/semantic_maps/upstream_xp_cell_contract")
    for name in ("Makefile", "makefile_server", ".emscripten-version", "requirements.txt",
                 "requirements-dev.txt", "requirements-test.txt", "requirements-optional.txt"):
        add(source / name)
    large = [(str(path), (source / path).stat().st_size) for path in selected
             if (source / path).stat().st_size >= 100 * 1024 * 1024]
    if large:
        raise SystemExit("Required files need <100MiB sharding before export: " + json.dumps(large))
    records = {}
    for relative in sorted(selected):
        original, target = source / relative, destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        checksum = sha(original)
        if sha(target) != checksum:
            raise SystemExit(f"Export checksum failure: {relative}")
        records[str(relative)] = {"bytes": original.stat().st_size, "sha256": checksum}
    manifest = {
        "schema": "cp437.numabe.public_workspace.v1", "source_originals_preserved": True,
        "scope": "saved map, HTML/native Classic editors/runtime, authoring sources and licensed live references",
        "excluded_local_only": [
            "PLATEAU raw CityGML and airborne point-cloud archives (only needed for full reconstruction)",
            "compiled object/assembly caches and native binaries (rebuild from included source)",
            "historic assessment screenshots and private operator-pasted reference photos",
            "old map undo snapshots except the immediately preceding verified map",
        ],
        "full_survey_rebuild": "requires reacquiring local raw datasets from source_manifest.json URLs/hashes",
        "files": records,
    }
    (destination / "transfer_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"destination": str(destination), "files": len(records),
                      "bytes": sum(row["bytes"] for row in records.values()),
                      "largest": max(row["bytes"] for row in records.values()),
                      "required_shards": 0}, indent=2))


if __name__ == "__main__":
    main()
