#!/usr/bin/env python3
"""FL-4831 (RQ-226): bake the dedicated car_race flat platform map.

SINGLE AUTHORITY for the race platform surface. The server
(`colyseus_server/src/sim/terrain.ts` RACE_FLAT_MAP_ID) and the Godot client
(`A3DMapLoader` via `RuntimeMapCatalog.RACE_FLAT_MAP_STEM`) both read the file
this script writes:

    godot_project/assets/maps/car_race_flat.a3d

DESIGN (why this shape, what is frozen):
- The patch (x, y) footprint is copied verbatim from `game_map_y8.a3d`, so the
  loader center-offset is identical on both sides and world coordinates do not
  move. The race grid and gate law can retain their coordinates without adding
  any walls or decorative geometry to the platform.
- Every height sample is FLAT_Y = 4.0 (encoded 64 at HEIGHT_SCALE 16). 4.0 is
  above WATER_SURFACE_Y (3.0625), so the whole platform is dry, and inside
  the surveyed y8 plateau band (3.3-5.4) the S5 authority comment cites.
- Every visual word is 0x0000 (mat 0 "Grass A", shade/row/elev 0): zero
  terrain features, one blank material. The material block is copied verbatim
  from y8 so the palette owner is unchanged.
- All diagonal-split bits are 0 (nothing to split on a plane).

Rerun: `python3 scripts/bake_fl4831_flat_map.py` (deterministic; diff must be
empty unless y8's footprint changed, in which case re-verify the extents).

FL-4881 PROFILES. The same bake writes every flat map; a profile only varies
the output stem, the visual word per cell, and the diagonal bits. Heights,
footprint, and the material block stay shared, so every flat map keeps y8
world coordinates (default spawns and debug vehicles land on the plane).

- `race` (default): `car_race_flat.a3d`, bytes unchanged from FL-4831.
- `sandbox`: `sandbox_flat.a3d`, the rig/combat/vehicle debugging floor. It
  is NOT a race map: no race contract, no forced race room. One-patch checker
  of y8 ground materials 0 and 2 (shade-0 glyphs ',' and '.'; material 4
  has glyph 0 at shade 0, which failed the headed glyph-diversity check)
  gives a motion and scale reference that a blank plane cannot, and the non-zero diagonal bits let it pass the generic
  map contract (material, glyph, and diagonal diversity) without an exemption.

  `python3 scripts/bake_fl4831_flat_map.py --profile sandbox`
  `python3 scripts/bake_fl4831_flat_map.py --profile sandbox --check`
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "godot_project" / "assets" / "maps" / "game_map_y8.a3d"
MAPS_DIR = REPO_ROOT / "godot_project" / "assets" / "maps"

HEADER_BYTES = 16
PATCH_BYTES = 188
VISUAL_CELLS = 8
HEIGHT_SAMPLES = 25
MATERIAL_BLOCK_BYTES = 256 * 512

FLAT_Y = 4.0
FLAT_HEIGHT_CODE = int(FLAT_Y * 16.0)
FLAT_VISUAL = 0x0000

# FL-4881: one row per flat map. `checker` is (material_a, material_b,
# cells_per_square) or None for a single FLAT_VISUAL material.
PROFILES = {
    "race": {"stem": "car_race_flat", "checker": None, "diag": 0x0000},
    "sandbox": {"stem": "sandbox_flat", "checker": (0, 2, VISUAL_CELLS), "diag": 0x5555},
}


def _visual_words(px: int, py: int, checker: tuple[int, int, int] | None) -> list[int]:
    if checker is None:
        return [FLAT_VISUAL] * 64
    mat_a, mat_b, square = checker
    words = []
    for vy in range(VISUAL_CELLS):
        for vx in range(VISUAL_CELLS):
            gx = (px * VISUAL_CELLS + vx) // square
            gy = (py * VISUAL_CELLS + vy) // square
            words.append(mat_a if (gx + gy) % 2 == 0 else mat_b)
    return words


def bake(profile_name: str) -> tuple[bytes, str]:
    profile = PROFILES[profile_name]
    data = SRC.read_bytes()
    if data[:4] != b"AS3D":
        raise SystemExit(f"bake_flat_map: bad magic in {SRC}")
    patch_count = struct.unpack("<I", data[8:12])[0]
    patches_off = HEADER_BYTES
    material_off = patches_off + patch_count * PATCH_BYTES
    material_block = data[material_off:material_off + MATERIAL_BLOCK_BYTES]
    if len(material_block) != MATERIAL_BLOCK_BYTES:
        raise SystemExit("bake_flat_map: y8 material block truncated")

    out = bytearray()
    out += data[:HEADER_BYTES]  # magic + header size + patch count + reserved
    min_px = min_py = 10**9
    max_px = max_py = -(10**9)
    for i in range(patch_count):
        off = patches_off + i * PATCH_BYTES
        px = struct.unpack("<i", data[off:off + 4])[0]
        py = struct.unpack("<i", data[off + 4:off + 8])[0]
        min_px = min(min_px, px)
        max_px = max(max_px, px)
        min_py = min(min_py, py)
        max_py = max(max_py, py)
        out += struct.pack("<i", px)
        out += struct.pack("<i", py)
        out += struct.pack("<" + "H" * 64, *_visual_words(px, py, profile["checker"]))
        out += struct.pack("<" + "H" * HEIGHT_SAMPLES, *([FLAT_HEIGHT_CODE] * HEIGHT_SAMPLES))
        # diag: a plane has no geometric split, so these bits only feed the
        # loader's diagonal-diversity check (race keeps 0, FL-4831 bytes).
        out += struct.pack("<H", profile["diag"])
    out += material_block
    summary = (
        f"profile={profile_name} patches={patch_count} "
        f"footprint_x=[{min_px},{max_px}] footprint_y=[{min_py},{max_py}] "
        f"flat_y={FLAT_Y} checker={profile['checker']} diag=0x{profile['diag']:04x}"
    )
    return bytes(out), summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Bake a flat runtime map from the y8 footprint.")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="race")
    parser.add_argument("--check", action="store_true", help="fail if the shipped map differs from a fresh bake")
    args = parser.parse_args()
    out_path = MAPS_DIR / f"{PROFILES[args.profile]['stem']}.a3d"
    baked, summary = bake(args.profile)
    digest = hashlib.sha256(baked).hexdigest()
    if args.check:
        if not out_path.is_file() or out_path.read_bytes() != baked:
            print(f"bake_flat_map: STALE {out_path.relative_to(REPO_ROOT)} {summary} expected_sha256={digest}")
            return 1
        print(f"bake_flat_map: OK {out_path.relative_to(REPO_ROOT)} {summary} sha256={digest}")
        return 0
    out_path.write_bytes(baked)
    print(f"bake_flat_map: wrote {out_path.relative_to(REPO_ROOT)} {summary} sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
