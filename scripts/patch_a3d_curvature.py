#!/usr/bin/env python3
"""FL-4257 Phase 1 S5 — flip the curvature kind on an existing .a3d file.

The .a3d binary format is documented in engine/terrain.cpp::SaveTerrain.
The 16-byte FileHeader is:
    uint32 file_sign      "AS3D"      offset  0
    uint32 header_size    16          offset  4
    uint32 num_patches    <count>     offset  8
    uint32 reserved       <bits>      offset 12   (low byte = curvature kind)

Phase 0 ReadA3DCurvatureKind treats the low byte of `reserved` as the
curvature kind:
    0  Euclidean
    1  spherical
    2  hyperbolic
The upper 24 bits are reserved for future schema use; this tool preserves
them so a file produced by SaveTerrain(t, f, kind) that someone augmented
with extra reserved bits later does not lose those bits when re-patched.

Usage:
    # Preview the current curvature byte
    scripts/patch_a3d_curvature.py path/to/map.a3d --show

    # Flip to spherical (backs the original up to .bak unless --no-backup)
    scripts/patch_a3d_curvature.py path/to/map.a3d --kind spherical

    # Flip back to Euclidean
    scripts/patch_a3d_curvature.py path/to/map.a3d --kind euclidean

This is the canonical fastest path to a renderable spherical map: pick any
existing .a3d, patch the byte, and the server-side intake + render dispatch
will pick it up. The writer overload SaveTerrain(t, f, kind) in
engine/terrain.cpp is for code that produces fresh spherical maps; this
tool is for in-place migration of existing content.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path


# Must match engine/curvature.h ASCK_CURVATURE_KIND_*.
KIND_BY_NAME = {
    "euclidean":  0,
    "spherical":  1,
    "hyperbolic": 2,
}
NAME_BY_KIND = {v: k for k, v in KIND_BY_NAME.items()}
ABSENT_KIND = 0xFF


def kind_name(byte: int) -> str:
    if byte in NAME_BY_KIND:
        return NAME_BY_KIND[byte]
    if byte == ABSENT_KIND:
        return "absent (0xFF)"
    return f"unknown (0x{byte:02x})"


def read_header(path: Path) -> tuple[bytes, int, int, int]:
    with path.open("rb") as f:
        sig = f.read(4)
        if sig != b"AS3D":
            sys.exit(f"{path}: not an A3D file (magic={sig!r})")
        header_size_b = f.read(4)
        num_patches_b = f.read(4)
        reserved_b = f.read(4)
        if len(header_size_b) != 4 or len(num_patches_b) != 4 or len(reserved_b) != 4:
            sys.exit(f"{path}: truncated header")
        header_size = struct.unpack("<I", header_size_b)[0]
        num_patches = struct.unpack("<I", num_patches_b)[0]
        reserved = struct.unpack("<I", reserved_b)[0]
    if header_size != 16:
        sys.exit(f"{path}: unexpected header_size {header_size} (only 16-byte FileHeader is supported)")
    return sig, header_size, num_patches, reserved


def show(path: Path) -> None:
    sig, header_size, num_patches, reserved = read_header(path)
    low = reserved & 0xFF
    print(f"file:         {path}")
    print(f"magic:        {sig.decode()}")
    print(f"header_size:  {header_size}")
    print(f"num_patches:  {num_patches}")
    print(f"reserved:     0x{reserved:08x}")
    print(f"curvature:    {kind_name(low)} (low byte = 0x{low:02x})")


def patch(path: Path, kind_value: int, *, no_backup: bool, dry_run: bool) -> None:
    sig, header_size, num_patches, reserved = read_header(path)
    old_low = reserved & 0xFF
    new_reserved = (reserved & 0xFFFFFF00) | (kind_value & 0xFF)
    if old_low == (kind_value & 0xFF):
        print(f"{path}: curvature already {kind_name(kind_value)}; no change")
        return
    if dry_run:
        print(f"{path}: would flip {kind_name(old_low)} -> {kind_name(kind_value)}")
        print(f"   reserved 0x{reserved:08x} -> 0x{new_reserved:08x}")
        return

    if not no_backup:
        backup = path.with_suffix(path.suffix + ".bak")
        # Preserve only one .bak — re-patching should not chain .bak.bak.
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
            print(f"backup:  {backup}")

    with path.open("r+b") as f:
        f.seek(12)
        f.write(struct.pack("<I", new_reserved))

    # Re-verify by reading back.
    _, _, _, verify_reserved = read_header(path)
    if (verify_reserved & 0xFF) != (kind_value & 0xFF):
        sys.exit(
            f"{path}: post-write verification failed; "
            f"read 0x{verify_reserved:08x}"
        )
    print(f"{path}: curvature {kind_name(old_low)} -> {kind_name(kind_value)}")
    print(f"   reserved 0x{reserved:08x} -> 0x{new_reserved:08x}")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("path", type=Path, help="Path to .a3d file")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--show", action="store_true",
                   help="Display current curvature byte without modifying the file")
    g.add_argument("--kind", choices=sorted(KIND_BY_NAME.keys()),
                   help="Target curvature kind to write into the FileHeader low byte")
    p.add_argument("--no-backup", action="store_true",
                   help="Skip the .bak side-copy that --kind would otherwise create")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the change that would be made; do not modify the file")
    args = p.parse_args(argv)

    if not args.path.exists():
        sys.exit(f"{args.path}: not found")

    if args.show:
        show(args.path)
        return 0

    patch(args.path, KIND_BY_NAME[args.kind],
          no_backup=args.no_backup, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
