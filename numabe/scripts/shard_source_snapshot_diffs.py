#!/usr/bin/env python3
"""Migrate oversized runtime-source tracked diffs to verified Git-safe shards."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import launch_fl4359_manual_play as snapshots


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT_ROOT = ROOT / "artifacts" / "run_receipts" / "source_snapshots"


def _write_once(path: Path, data: bytes) -> None:
    if path.exists() or path.is_symlink():
        if not path.is_file() or path.read_bytes() != data:
            raise snapshots.SourceSnapshotError(f"refusing_to_overwrite_preserved_evidence: {path}")
        return
    temporary = path.with_name(path.name + ".partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _replace_json(path: Path, document: dict) -> None:
    data = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _manifests(root: Path) -> list[Path]:
    return sorted(path for path in root.glob("*/snapshot.json") if path.is_file())


def migrate(root: Path, max_bytes: int) -> tuple[int, int]:
    migrated = 0
    checked = 0
    for manifest_path in _manifests(root):
        checked += 1
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        snapshot_dir = manifest_path.parent
        tracked = document.get("tracked_diff") or {}
        data = snapshots._tracked_diff_bytes(document, snapshot_dir)
        if len(data) <= max_bytes or tracked.get("parts") is not None:
            continue
        original = snapshot_dir / str(tracked.get("file") or "tracked.diff")
        parts = []
        for index, start in enumerate(range(0, len(data), max_bytes)):
            chunk = data[start:start + max_bytes]
            name = f"tracked.diff.part-{index:03d}"
            _write_once(snapshot_dir / name, chunk)
            parts.append({"file": name, "bytes": len(chunk),
                          "sha256": hashlib.sha256(chunk).hexdigest()})
        document["tracked_diff"] = {
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "parts": parts,
        }
        document["rebuild"] = [
            "git checkout <head>",
            "concatenate tracked_diff.parts in order when present; otherwise use tracked_diff.file",
            "git apply --binary <reconstructed tracked diff>  (skip when bytes == 0)",
            "copy untracked/<path> to <path>",
        ]
        _replace_json(manifest_path, document)
        snapshots.verify_snapshot_address(manifest_path)
        if original.is_file():
            original.unlink()
        migrated += 1
        print(f"SHARDED {snapshot_dir.relative_to(ROOT)} bytes={len(data)} parts={len(parts)}")
    return migrated, checked


def check(root: Path, max_bytes: int) -> tuple[int, int]:
    checked = 0
    failures = 0
    for manifest_path in _manifests(root):
        checked += 1
        try:
            document = json.loads(manifest_path.read_text(encoding="utf-8"))
            data = snapshots._tracked_diff_bytes(document, manifest_path.parent)
            snapshots.verify_snapshot_address(manifest_path)
            tracked = document.get("tracked_diff") or {}
            if len(data) > max_bytes and tracked.get("parts") is None:
                raise snapshots.SourceSnapshotError("oversized_unsharded_tracked_diff")
            for row in tracked.get("parts") or []:
                if int(row.get("bytes", max_bytes + 1)) > max_bytes:
                    raise snapshots.SourceSnapshotError(f"oversized_tracked_diff_part: {row.get('file')}")
        except (OSError, ValueError, TypeError, snapshots.SourceSnapshotError) as error:
            failures += 1
            print(f"INVALID {manifest_path.relative_to(ROOT)}: {error}", file=sys.stderr)
    return failures, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("migrate", "check"))
    parser.add_argument("--root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--max-bytes", type=int, default=snapshots.SOURCE_SNAPSHOT_SHARD_BYTES)
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    if args.max_bytes <= 0:
        parser.error("--max-bytes must be positive")
    if args.mode == "migrate":
        migrated, checked = migrate(root, args.max_bytes)
        print(f"SOURCE_SNAPSHOT_SHARD_RESULT mode=migrate checked={checked} migrated={migrated} failed=0")
        return 0
    failures, checked = check(root, args.max_bytes)
    print(f"SOURCE_SNAPSHOT_SHARD_RESULT mode=check checked={checked} migrated=0 failed={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
