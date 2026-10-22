#!/usr/bin/env python3
"""Canonical read-only extraction of mesh references from an A3D scene."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO


PATCH_SIZE_BYTES = 8 + (8 * 8 * 2) + (5 * 5 * 2) + 2
MATERIALS_SIZE_BYTES = 256 * 512


class A3DMeshReferenceError(RuntimeError):
    """The scene cannot supply a complete canonical mesh-reference set."""


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise A3DMeshReferenceError("truncated A3D while reading mesh references")
    return data


def _read_i32(stream: BinaryIO) -> int:
    return struct.unpack("<i", _read_exact(stream, 4))[0]


def referenced_meshes(a3d_path: Path) -> list[str]:
    """Return the sorted, duplicate-free non-terrain mesh names in one scene."""
    with a3d_path.open("rb") as stream:
        if _read_exact(stream, 4) != b"AS3D":
            raise A3DMeshReferenceError(f"invalid A3D signature: {a3d_path}")
        header_size = struct.unpack("<I", _read_exact(stream, 4))[0]
        patch_count = struct.unpack("<I", _read_exact(stream, 4))[0]
        _read_exact(stream, 4)
        stream.seek(header_size + patch_count * PATCH_SIZE_BYTES + MATERIALS_SIZE_BYTES)

        first = _read_i32(stream)
        if first < 0:
            format_version = -first
            instance_count = _read_i32(stream)
        else:
            format_version = 0
            instance_count = first
        if instance_count < 0:
            raise A3DMeshReferenceError("negative A3D instance count")

        meshes: set[str] = set()
        for _ in range(instance_count):
            mesh_id_len = _read_i32(stream)
            if mesh_id_len >= 0:
                mesh_id = (
                    _read_exact(stream, mesh_id_len).decode("utf-8")
                    if mesh_id_len
                    else ""
                )
                if mesh_id.endswith(".ply"):
                    mesh_id = mesh_id[:-4] + ".akm"
                inst_name_len = _read_i32(stream)
                if inst_name_len < 0:
                    raise A3DMeshReferenceError("negative A3D instance-name length")
                _read_exact(stream, inst_name_len)
                _read_exact(stream, 16 * 8)
                _read_exact(stream, 4)
                if format_version > 0:
                    _read_exact(stream, 4)
                if mesh_id:
                    meshes.add(mesh_id)
            elif mesh_id_len == -1:
                inst_name_len = _read_i32(stream)
                if inst_name_len < 0:
                    raise A3DMeshReferenceError("negative A3D sprite-name length")
                _read_exact(stream, inst_name_len)
                _read_exact(stream, 12 + 4 + 8 + 16 + 4)
                if format_version > 0:
                    _read_exact(stream, 4)
            elif mesh_id_len == -2:
                if format_version < 3:
                    raise A3DMeshReferenceError(
                        "A3D item record requires format_version >= 3"
                    )
                _read_exact(stream, 16 + 12 + 4 + 4)
                _read_exact(stream, 4)
            else:
                raise A3DMeshReferenceError(
                    f"unknown A3D instance discriminant: {mesh_id_len}"
                )

    return sorted(meshes)
