#!/usr/bin/env python3
"""FL-4257 Phase 1 S5 — A3D writer-side curvature metadata.

S5 adds two ways to author a spherical .a3d:
  - SaveTerrain(t, f, curvature_kind) C++ overload in engine/terrain.cpp
  - scripts/patch_a3d_curvature.py CLI to patch an existing .a3d in place

These tests assert:
- The SaveTerrain header declares both the 2-arg (Euclidean baseline) and
  3-arg (explicit curvature) overloads
- The 2-arg form is implemented as a thin wrapper over the 3-arg form so
  there's a single owner of the byte layout (Law 1)
- The CLI tool round-trips: show -> patch spherical -> show -> patch
  back to euclidean -> show; the produced bytes match what
  ReadA3DCurvatureKind expects (low byte of FileHeader.reserved)
- The CLI tool preserves the upper 24 bits of reserved so a future schema
  extension that uses them does not get clobbered
- The CLI tool's --show and --dry-run modes do not modify the file
- The CLI rejects non-A3D files

These tests synthesize tiny in-memory A3D headers; they do not exercise
the real SaveTerrain compiled into the engine. That's covered by the
integration when the engine builds against the modified terrain.cpp.
"""
from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "scripts" / "patch_a3d_curvature.py"


def _make_minimal_a3d(path: Path, reserved: int = 0) -> None:
    """Write a 16-byte FileHeader-only fake A3D (no patches). Enough for
    the CLI's header-only operations to work; not loadable by the engine."""
    with path.open("wb") as f:
        f.write(b"AS3D")
        f.write(struct.pack("<I", 16))    # header_size
        f.write(struct.pack("<I", 0))     # num_patches
        f.write(struct.pack("<I", reserved))


def test_terrain_h_declares_both_overloads() -> None:
    text = (REPO_ROOT / "engine" / "terrain.h").read_text()
    assert "bool SaveTerrain(const Terrain* t, FILE* f);" in text, (
        "two-argument SaveTerrain must remain declared for backwards compat"
    )
    assert "bool SaveTerrain(const Terrain* t, FILE* f, unsigned char curvature_kind);" in text, (
        "three-argument SaveTerrain overload must be declared"
    )


def test_terrain_cpp_two_arg_form_delegates_to_three_arg() -> None:
    text = (REPO_ROOT / "engine" / "terrain.cpp").read_text()
    # The two-arg form must call SaveTerrain(t, f, 0) — that is the single
    # owner of the on-disk byte layout. If the two-arg form re-implements
    # the byte writes, Law 1 single-owner is gone and the writer can drift.
    import re
    two_arg = re.search(
        r"bool\s+SaveTerrain\s*\(\s*const\s+Terrain\s*\*\s*t\s*,\s*FILE\s*\*\s*f\s*\)\s*\{[^}]*?\}",
        text,
        re.DOTALL,
    )
    assert two_arg, "two-arg SaveTerrain definition not found"
    body = two_arg.group(0)
    assert "SaveTerrain(t, f, 0)" in body, (
        "two-arg SaveTerrain must delegate to SaveTerrain(t, f, 0); body was:\n"
        + body
    )


def test_cli_show_emits_curvature(tmp_path: Path) -> None:
    p = tmp_path / "fake.a3d"
    _make_minimal_a3d(p, reserved=0)
    proc = subprocess.run(
        [sys.executable, str(CLI), str(p), "--show"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "curvature:    euclidean" in proc.stdout, proc.stdout


def test_cli_patch_round_trip_preserves_upper_bits(tmp_path: Path) -> None:
    """Round-trip: euclidean -> spherical -> hyperbolic -> euclidean. The
    upper 24 bits of `reserved` must remain whatever they were at start so
    a future schema extension is not clobbered.
    """
    p = tmp_path / "fake.a3d"
    # Start with non-zero upper bits + curvature=euclidean so we can detect
    # accidental upper-bit clobbering.
    sentinel_upper = 0xAABBCC
    initial_reserved = (sentinel_upper << 8) | 0x00
    _make_minimal_a3d(p, reserved=initial_reserved)

    def cur_reserved() -> int:
        with p.open("rb") as f:
            f.seek(12)
            return struct.unpack("<I", f.read(4))[0]

    assert cur_reserved() == initial_reserved

    for kind in ("spherical", "hyperbolic", "euclidean"):
        proc = subprocess.run(
            [sys.executable, str(CLI), str(p), "--kind", kind, "--no-backup"],
            capture_output=True, text=True, check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        reserved = cur_reserved()
        # Upper 24 bits must be preserved every step.
        assert (reserved >> 8) == sentinel_upper, (
            f"upper-bit clobber on patch to {kind}: reserved=0x{reserved:08x}, "
            f"expected upper=0x{sentinel_upper:06x}"
        )
        # Low byte matches the patched kind.
        expected = {"euclidean": 0, "spherical": 1, "hyperbolic": 2}[kind]
        assert (reserved & 0xFF) == expected, (
            f"patch to {kind} did not produce low byte {expected}: "
            f"reserved=0x{reserved:08x}"
        )


def test_cli_show_does_not_modify_file(tmp_path: Path) -> None:
    p = tmp_path / "fake.a3d"
    _make_minimal_a3d(p, reserved=0x000000)
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    proc = subprocess.run(
        [sys.executable, str(CLI), str(p), "--show"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    after = hashlib.sha256(p.read_bytes()).hexdigest()
    assert before == after, "--show must not modify the file"


def test_cli_dry_run_does_not_modify_file(tmp_path: Path) -> None:
    p = tmp_path / "fake.a3d"
    _make_minimal_a3d(p, reserved=0)
    before = hashlib.sha256(p.read_bytes()).hexdigest()
    proc = subprocess.run(
        [sys.executable, str(CLI), str(p), "--kind", "spherical", "--dry-run"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    after = hashlib.sha256(p.read_bytes()).hexdigest()
    assert before == after, "--dry-run must not modify the file"
    assert "would flip" in proc.stdout


def test_cli_rejects_non_a3d_file(tmp_path: Path) -> None:
    p = tmp_path / "bogus.a3d"
    p.write_bytes(b"NOPE" + struct.pack("<I", 16) + b"\x00" * 8)
    proc = subprocess.run(
        [sys.executable, str(CLI), str(p), "--show"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode != 0, "CLI should reject files without AS3D magic"
    assert "not an A3D file" in (proc.stdout + proc.stderr)


def test_cli_creates_backup_by_default(tmp_path: Path) -> None:
    p = tmp_path / "fake.a3d"
    _make_minimal_a3d(p, reserved=0)
    proc = subprocess.run(
        [sys.executable, str(CLI), str(p), "--kind", "spherical"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    backup = p.with_suffix(p.suffix + ".bak")
    assert backup.exists(), ".bak side-copy must be created by default"
    # Backup contains the original byte (reserved low byte = 0).
    with backup.open("rb") as f:
        f.seek(12)
        backup_reserved = struct.unpack("<I", f.read(4))[0]
    assert (backup_reserved & 0xFF) == 0, "backup must hold pre-patch bytes"


def test_cli_compatibility_with_real_a3d(tmp_path: Path) -> None:
    """End-to-end: copy a real engine .a3d into a tempdir, patch it
    spherical via the CLI, confirm the byte changed and the rest of the
    file is byte-identical to the original. Without this, a bug that
    accidentally truncates or shifts the file past the FileHeader would
    not be caught by the synthetic fake-A3D tests above.
    """
    src = REPO_ROOT / "assets" / "a3d" / "sandbox_20x20.a3d"
    if not src.exists():
        import pytest
        pytest.skip(f"reference a3d {src} not available")
    p = tmp_path / "real.a3d"
    shutil.copyfile(src, p)
    original_bytes = p.read_bytes()
    proc = subprocess.run(
        [sys.executable, str(CLI), str(p), "--kind", "spherical", "--no-backup"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    patched_bytes = p.read_bytes()
    # Same length (no truncation, no growth).
    assert len(patched_bytes) == len(original_bytes), (
        "patch must not change file size"
    )
    # Only the 4 bytes at offset 12 differ.
    assert patched_bytes[:12] == original_bytes[:12]
    assert patched_bytes[16:] == original_bytes[16:]
    # The differing bytes match the documented layout.
    new_reserved = struct.unpack("<I", patched_bytes[12:16])[0]
    assert (new_reserved & 0xFF) == 1, "low byte must be spherical=1"
