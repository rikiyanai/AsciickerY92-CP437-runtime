#!/usr/bin/env python3
"""FL-4359: verify an external run archive before removing its internal duplicate."""
import argparse
import json
import os
from pathlib import Path
from archive_run_evidence import file_digest, identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--external", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--compatibility-root", type=Path)
    parser.add_argument("--remove-verified-local", action="store_true")
    args = parser.parse_args()
    local = args.local.resolve()
    external = args.external.resolve()
    if not external.is_dir() or external == local or external.is_relative_to(local):
        parser.error("external archive must exist outside the local archive")
    rows = [json.loads(line) for line in args.manifest.read_text().splitlines()]
    verified = []
    for row in rows:
        if row["kind"] != "file":
            continue
        source = Path(row["archive"])
        destination = external / source.relative_to(local)
        before = identity(source)
        expected = row["archive_sha256"]
        if file_digest(source)[0] != expected or file_digest(destination)[0] != expected:
            raise RuntimeError(f"archive hash mismatch: {source}")
        if identity(source) != before:
            raise RuntimeError(f"local archive changed: {source}")
        verified.append((source, destination, before))
    receipt = {
        "schema": "fl4359.external_archive_relocation.v1",
        "source_manifest": str(args.manifest),
        "source_manifest_sha256": file_digest(args.manifest)[0],
        "local_root": str(local), "external_root": str(external),
        "verified_files": len(verified),
        "verified_archive_bytes": sum(row["archive_bytes"] for row in rows if row["kind"] == "file"),
        "all_archive_sha256_match": True, "local_copies_removed": False,
        "path_mapping": "external_root + path relative to local_root; source manifest retains original captured-byte and compressed-byte hashes",
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    if args.remove_verified_local:
        # Switch existing compatibility links before any old target is removed.
        if args.compatibility_root:
            for link in args.compatibility_root.iterdir():
                if link.is_symlink() and link.resolve().is_relative_to(local):
                    destination = external / link.resolve().relative_to(local)
                    temporary = link.with_name(link.name + ".relocating")
                    temporary.symlink_to(destination)
                    temporary.replace(link)
        for source, _destination, before in verified:
            if identity(source) != before:
                raise RuntimeError(f"local archive changed before removal: {source}")
            source.unlink()
        for root, dirs, _files in os.walk(local, topdown=False):
            for name in dirs:
                try:
                    (Path(root) / name).rmdir()
                except OSError:
                    pass
        try:
            local.rmdir()
        except OSError:
            pass
        receipt["local_copies_removed"] = True
        args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
