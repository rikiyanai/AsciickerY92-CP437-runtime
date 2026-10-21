#!/usr/bin/env python3
"""FL-4359: preserve run bytes and extracted proof before optional source removal."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil


CHUNK = 1024 * 1024
PACKED = {".gz", ".zip", ".png", ".gif", ".mp4", ".mov", ".npz", ".jpg", ".webp"}
PROOF_NAMES = {
    "receipt.json", "artifact_manifest.json", "analysis_summary.json",
    "summary.json", "actual_field_stability.json", "presentation_stability.json",
    "presentation_observer_result.json", "native_sweep_receipt.json",
    "raw_archive_manifest.json", "four_mode_measurements.json",
    "presentation_artifact_manifest.json", "missing_fragment_diagnostic.json",
}


def digest(stream):
    result = hashlib.sha256()
    size = 0
    while block := stream.read(CHUNK):
        result.update(block)
        size += len(block)
    return result.hexdigest(), size


def file_digest(path):
    with path.open("rb") as stream:
        return digest(stream)


def identity(path):
    stat = path.lstat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def append_record(path, record):
    with path.open("a") as stream:
        stream.write(json.dumps(record, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def archive_file(source, relative, archive, proofs, manifest, prune=False):
    before = identity(source)
    if source.is_symlink():
        # Record the link itself; never traverse an outside evidence owner.
        row = {"source": str(relative), "kind": "symlink", "target": os.readlink(source)}
        append_record(manifest, row)
        if prune and identity(source) == before:
            source.unlink()
        return row
    packed = source.suffix.lower() in PACKED or before[2] < 65536
    destination = archive / relative
    if not packed:
        destination = destination.with_name(destination.name + ".gz")
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected, original_bytes = file_digest(source)
    if not destination.exists():
        partial = destination.with_name(destination.name + ".partial")
        with source.open("rb") as src, partial.open("wb") as out:
            if packed:
                shutil.copyfileobj(src, out, CHUNK)
            else:
                with gzip.GzipFile(filename="", mode="wb", compresslevel=3, mtime=0, fileobj=out) as zipped:
                    shutil.copyfileobj(src, zipped, CHUNK)
            out.flush()
            os.fsync(out.fileno())
        partial.rename(destination)
    opener = open if packed else gzip.open
    with opener(destination, "rb") as stored:
        recovered, recovered_bytes = digest(stored)
    if (expected, original_bytes) != (recovered, recovered_bytes):
        raise RuntimeError(f"archive verification failed: {source}")
    if identity(source) != before:
        raise RuntimeError(f"source changed while archiving: {source}")
    row = {
        "source": str(relative), "kind": "file", "sha256": expected,
        "original_bytes": original_bytes, "archive": str(destination),
        "encoding": "identity" if packed else "gzip",
        "archive_bytes": destination.stat().st_size,
        "archive_sha256": file_digest(destination)[0],
    }
    if source.suffix.lower() == ".gif" or (source.name in PROOF_NAMES and original_bytes < 5 * CHUNK):
        proof = proofs / relative
        proof.parent.mkdir(parents=True, exist_ok=True)
        if proof.exists() and file_digest(proof)[0] != expected:
            raise RuntimeError(f"different extracted proof already exists: {proof}")
        if not proof.exists():
            shutil.copy2(source, proof)
        if file_digest(proof)[0] != expected:
            raise RuntimeError(f"proof copy verification failed: {proof}")
        row["extracted_proof"] = str(proof)
    # A durable verified mapping must exist before the original can disappear.
    append_record(manifest, row)
    if prune:
        if identity(source) != before:
            raise RuntimeError(f"source changed before removal: {source}")
        source.unlink()
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--proofs", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--exclude-top", action="append", default=[])
    parser.add_argument("--prune-verified", action="store_true")
    args = parser.parse_args()
    source = args.source.resolve()
    for target in (args.archive, args.proofs, args.manifest):
        if target.resolve().is_relative_to(source):
            parser.error("archive, proofs and manifest must be outside source")
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for root, dirs, files in os.walk(source, followlinks=False):
        if Path(root) == source:
            dirs[:] = [d for d in dirs if d not in args.exclude_top]
        for name in dirs[:]:
            path = Path(root) / name
            if path.is_symlink():
                paths.append(path)
                dirs.remove(name)
        paths.extend(Path(root) / name for name in files)
    # Reclaim the large raw captures first; all files are preserved by this pass.
    paths.sort(key=lambda path: path.lstat().st_size, reverse=True)
    original = stored = 0
    for index, path in enumerate(paths):
        row = archive_file(path, path.relative_to(source), args.archive,
                           args.proofs, args.manifest, args.prune_verified)
        original += row.get("original_bytes", 0)
        stored += row.get("archive_bytes", 0)
        if index % 25 == 0 or row.get("original_bytes", 0) > 100 * CHUNK:
            print(json.dumps({"files": index + 1, "total": len(paths),
                              "original_bytes": original, "archive_bytes": stored,
                              "last": str(path)}), flush=True)
    if args.prune_verified:
        for root, dirs, _files in os.walk(source, topdown=False, followlinks=False):
            for name in dirs:
                path = Path(root) / name
                if path.relative_to(source).parts[0] not in args.exclude_top:
                    try:
                        path.rmdir()
                    except OSError:
                        pass
    print(json.dumps({"complete": True, "files": len(paths),
                      "original_bytes": original, "archive_bytes": stored,
                      "pruned": args.prune_verified}), flush=True)


if __name__ == "__main__":
    main()
