# Ad hoc script: FL-4208 Path-2 audit: mat-id histogram on game_map_y8_original to pick synthetic water baseline slot
# Created: 2026-06-03
# Canonical gap: <describe what tool should own this>

"""FL-4208 Path-2 audit: which mat-id slot is unused on the canonical map?

Path 2 reserves an in-range matlib row (0..255) for the engine-authored
synthetic water baseline. Before routing water cells to that slot, prove
the slot is not live terrain-owned anywhere on game_map_y8_original_game_map.a3d.

This is the canonical map's mat-id histogram. The lowest-id slot with
zero hits AND zero authored-bytes is the safest choice.

Canonical gap: scripts/inspect_a3d.py should expose a --patches-only flag
so format_version<3 maps can run terrain histograms without crashing on
item-record parsing. Today the canonical tool throws ValueError at
addons/io_asciicker/scene/a3d_format.py:415 on this map.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

A3D = Path("/Users/r/Downloads/asciicker-Y9-2/assets/a3d/game_map_y8_original_game_map.a3d")
PATCH_BYTES = 188
VISUAL_CELLS = 8
MAT_CELL_BYTES = 8
MATERIAL_BYTES = 4 * 16 * 8  # 512
MATLIB_SLOTS = 256


def main() -> int:
    data = A3D.read_bytes()
    assert data[:4] == b"AS3D", f"bad magic {data[:4]!r}"
    header_size = struct.unpack_from("<I", data, 4)[0]
    patch_count = struct.unpack_from("<I", data, 8)[0]
    print(f"file: {A3D}")
    print(f"size: {len(data)} bytes")
    print(f"header_size={header_size} patch_count={patch_count}")

    # Patch block starts at offset 16; each patch is 188 bytes.
    # Visual cells are u16 at offset 8 + index*2 inside each patch.
    # mat_id = visual & 0xff (per A3DMapLoader.gd:93).
    mat_hist: Counter[int] = Counter()
    for i in range(patch_count):
        patch_off = 16 + i * PATCH_BYTES
        for cell_idx in range(VISUAL_CELLS * VISUAL_CELLS):
            v_off = patch_off + 8 + cell_idx * 2
            visual = struct.unpack_from("<H", data, v_off)[0]
            mat_hist[visual & 0xff] += 1

    matblock_off = 16 + patch_count * PATCH_BYTES
    matblock_end = matblock_off + MATLIB_SLOTS * MATERIAL_BYTES
    assert matblock_end <= len(data), f"file truncated at {matblock_end}"

    # For each matlib slot, count non-zero bytes in its 512-byte block.
    # An entirely-zero slot is "unused in matlib data" — completely safe.
    mat_authored: dict[int, int] = {}
    for mid in range(MATLIB_SLOTS):
        off = matblock_off + mid * MATERIAL_BYTES
        nonzero = sum(1 for b in data[off:off + MATERIAL_BYTES] if b != 0)
        mat_authored[mid] = nonzero

    total_cells = sum(mat_hist.values())
    print(f"\ntotal terrain cells scanned: {total_cells} ({patch_count * 64} = {patch_count}*8*8)")
    print(f"distinct mat_ids that appear in terrain: {len(mat_hist)}")
    print(f"\nTop 20 mat-ids in terrain:")
    print(f"  {'mat':>5} {'count':>8} {'pct':>6} {'authored':>9}")
    for mid, cnt in mat_hist.most_common(20):
        print(f"  {mid:>5} {cnt:>8} {cnt/total_cells*100:>5.2f}% {mat_authored[mid]:>9}")

    print(f"\nmat-ids ABSENT from terrain (candidates for synthetic slot):")
    absent = [mid for mid in range(MATLIB_SLOTS) if mid not in mat_hist]
    print(f"  {len(absent)} candidates total")

    # Triage by matlib authored-bytes count: prefer slots that are also
    # all-zeros in the matlib (no risk of "shared" use by some other system).
    fully_unused = [mid for mid in absent if mat_authored[mid] == 0]
    lightly_authored = [(mid, mat_authored[mid]) for mid in absent if 0 < mat_authored[mid] < 50]
    print(f"\n  ABSENT in terrain AND matlib slot all-zeros ({len(fully_unused)}):")
    for mid in fully_unused[:20]:
        print(f"    mat={mid}")
    if len(fully_unused) > 20:
        print(f"    ... and {len(fully_unused) - 20} more")
    print(f"\n  ABSENT in terrain but matlib has <50 nonzero bytes ({len(lightly_authored)}):")
    for mid, nz in lightly_authored[:10]:
        print(f"    mat={mid} authored={nz} bytes")

    # The original asciicker source uses mat_id 0xFF (255) as a sentinel
    # in some places. Verify it's not in use here.
    print(f"\nsentinel checks:")
    print(f"  mat=0   in_terrain={0 in mat_hist} authored_bytes={mat_authored[0]}")
    print(f"  mat=255 in_terrain={255 in mat_hist} authored_bytes={mat_authored[255]}")
    print(f"  mat=140 in_terrain={140 in mat_hist} authored_bytes={mat_authored[140]} (water mat per FL-4103)")

    # Recommend the highest-id fully-unused slot to maximize distance
    # from low-id terrain materials.
    if fully_unused:
        recommended = max(fully_unused)
        print(f"\nRECOMMENDED SYNTHETIC SLOT: mat={recommended}")
        print(f"  Rationale: highest in-range mat-id (0..255) with zero terrain hits AND")
        print(f"  zero authored matlib bytes. Maximally distant from low-id terrain materials.")
    else:
        print(f"\nWARNING: no fully-unused slot found; picking lowest lightly-authored absent slot.")
        if lightly_authored:
            recommended = lightly_authored[0][0]
            print(f"  candidate: mat={recommended} ({lightly_authored[0][1]} authored bytes)")
        else:
            print(f"  NONE — every matlib slot is touched. Path 2 needs different strategy.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
