# Ad hoc script: FL-4260 RQ-154: author an A3D proof fixture by overwriting terrain visual-map cells of a base map with a deterministic patchwork of admitted material ids {1,2,3,4,5,6,8}, so every camera view contains all admitted PROFILE materials. Byte-level visual-block patch only; height/diag/materials/instances/markers preserved. Output tracked fixture consumed by the real terrain ingestion path.
# Created: 2026-06-15
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4260 RQ-154 fixture authoring tool.

Authors a tracked A3D proof fixture from a base map by overwriting ONLY the
per-patch terrain visual-map cells with a deterministic cycle of the admitted
PROFILE material ids. Runtime material_id == visual & 0xFF (render_scene.cpp:79
-> scene.material_id -> harri_material_id_tex), so writing these values forces
every admitted material to appear in any camera view. Everything else in the
.a3d (height, diag, materials, instances, enemy_gens, markers) is left
byte-identical: this is an authored fixture map, not a synthetic bridge owner.

Usage:
  python3 scripts/adhoc/<this>.py <base.a3d> <out_fixture.a3d>
"""
import struct, sys

HEADER_SIZE = 16
PATCH_SIZE = 188
VISUAL_OFFSET_IN_PATCH = 8           # after x(int32),y(int32)
VISUAL_BYTES = 8 * 8 * 2             # 64 uint16
ADMITTED = [1, 2, 3, 4, 5, 6, 8]     # admitted terrain:N profiles in v1.json

def main():
    if len(sys.argv) != 3:
        print("usage: fixture.py <base.a3d> <out.a3d>", file=sys.stderr)
        return 2
    base, out = sys.argv[1], sys.argv[2]
    with open(base, "rb") as f:
        buf = bytearray(f.read())
    if buf[0:4] != b"AS3D":
        print(f"bad signature: {buf[0:4]!r}", file=sys.stderr)
        return 1
    header_size, num_patches, reserved = struct.unpack_from("<III", buf, 4)
    end = HEADER_SIZE + num_patches * PATCH_SIZE
    if end > len(buf):
        print(f"patch region overruns file: end={end} len={len(buf)}", file=sys.stderr)
        return 1
    counts = {m: 0 for m in ADMITTED}
    for i in range(num_patches):
        vbase = HEADER_SIZE + i * PATCH_SIZE + VISUAL_OFFSET_IN_PATCH
        for cell in range(64):
            mat = ADMITTED[cell % len(ADMITTED)]
            struct.pack_into("<H", buf, vbase + cell * 2, mat)
            counts[mat] += 1
    with open(out, "wb") as f:
        f.write(buf)
    print(f"wrote {out}: num_patches={num_patches} bytes={len(buf)}")
    print(f"per-material visual cell counts: {counts}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
