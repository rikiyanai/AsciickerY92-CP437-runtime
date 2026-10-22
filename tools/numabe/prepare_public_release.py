#!/usr/bin/env python3
"""Make an exported Numabe workspace portable without changing original inputs.

Only manifest-owned export copies are removed. Unicode glyph-generator caches
are unrelated to Classic Numabe. Raw survey exclusions were authorised by the
operator; the saved A3D/editor neither loads nor edits those acquisitions.
"""
import hashlib
import argparse
import json
import shutil
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[2] / "numabe"


def sha(path):
    """Bind an exported artifact to its content."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    """Prune only known generated copies, then write a portable active receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Preserved workspace supplying frozen scope inputs")
    args = parser.parse_args()
    manifest_path = WORKSPACE / "transfer_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if args.source:
        prefix = Path("artifacts/experiments/fl4813_capture_review_20260915/preserved/operator_boxes_complete_20260915")
        for original in (args.source / prefix).rglob("*"):
            if not original.is_file():
                continue
            relative = original.relative_to(args.source)
            target = WORKSPACE / relative
            checksum = sha(original)
            if target.exists() and sha(target) != checksum:
                raise SystemExit(f"Refusing to overwrite changed frozen input: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            assert sha(target) == checksum
            manifest["files"][str(relative)] = {"bytes": target.stat().st_size, "sha256": checksum}
    removed = []
    unused_prefixes = ("assets/glyphs/generated/", "assets/meshes/quarantine/",
                       "web/asciiid-authoring-shell/")
    for name in list(manifest["files"]):
        if not name.startswith(unused_prefixes):
            continue
        path = WORKSPACE / name
        if path.is_file():
            if sha(path) != manifest["files"][name]["sha256"]:
                raise SystemExit(f"Refusing to prune modified export copy: {path}")
            path.unlink()
        removed.append(name)
        del manifest["files"][name]
    exclusion = "Unrelated Unicode generator caches, quarantined meshes and legacy authoring-shell handoff archives"
    if exclusion not in manifest["excluded_local_only"]:
        manifest["excluded_local_only"].append(exclusion)
    build = Path(".run/fl4876_builds/live_numabe")
    receipt_path = WORKSPACE / build / "build_receipt.json"
    relative = str(receipt_path.relative_to(WORKSPACE))
    original_sha = manifest["files"][relative].get("export_source_sha256", manifest["files"][relative]["sha256"])
    receipt = {
        "schema": "cp437.numabe.saved_map.v1", "runtime_product": "classic_cp437",
        "path_basis": "relative to numabe workspace, never the original developer checkout",
        "map_path": str(build / "output.a3d"), "mesh_root": str(build / "meshes"),
        "train_routes": str(build / "output.trains.json"),
        "map_sha256_at_export": sha(WORKSPACE / build / "output.a3d"),
        "historical_component_receipt_sha256": original_sha,
        "full_reconstruction_inputs": "Raw CityGML/point clouds remain local; source_manifest.json retains acquisition URLs and hashes",
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    manifest["files"][relative] = {"sha256": sha(receipt_path), "bytes": receipt_path.stat().st_size,
                                    "export_source_sha256": original_sha}
    manifest["portable_runtime_receipt"] = relative
    manifest["unused_generated_files_not_published"] = manifest.get("unused_generated_files_not_published", 0) + len(removed)
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")
    files = [p for p in WORKSPACE.rglob("*") if p.is_file()]
    oversized = [str(p) for p in files if p.stat().st_size >= 100_000_000]
    if oversized:
        raise SystemExit("Required files need sharding: " + json.dumps(oversized))
    print(json.dumps({"files": len(files), "bytes": sum(p.stat().st_size for p in files),
                      "largest_bytes": max(p.stat().st_size for p in files),
                      "required_shards": 0, "unused_generator_files_excluded": len(removed)}))


if __name__ == "__main__":
    main()
