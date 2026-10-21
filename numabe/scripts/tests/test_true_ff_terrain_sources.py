"""Offline source/ABI checks; no Godot execution or visual acceptance."""
import importlib.util
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    out = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(out)
    return out


B = module("true_ff_feature_bank")
T = module("true_ff_terrain_sources")


def test_a3d_far_edge_owner_and_exact_rf_r8_bytes(tmp_path):
    # Two adjacent source patches with a deliberate shared-edge discrepancy:
    # loader's down/right ownership must select the second patch, not average.
    patches = []
    for x, elevation, material in ((0, 16, 7), (1, 48, 140)):
        visual = material | (0x8000 if x == 1 else 0)
        patches.append(struct.pack("<ii64H25HH", x, 0, *([visual] * 64),
                                   *([elevation] * 25), 0))
    path = tmp_path / "terrain.a3d"
    path.write_bytes(struct.pack("<4sIII", b"AS3D", 16, 2, 0)
                     + b"".join(patches) + bytes(256 * 512))
    source = T.map_source(path, B)
    assert source["dims"] == [16, 8]
    assert source["origin"] == [-8.0, -4.0]
    heights = source["heights"]
    assert heights[7] == 1 and heights[8] == 3 and heights[16] == 3
    assert heights[-1] == 3
    assert source["materials"][:16] == [7] * 8 + [140] * 8
    assert source["elevations"][:16] == [3] * 7 + [1] * 9
    payload = struct.pack("<2i4f", 16, 8, -8, -4, 1, source["water_level"])
    payload += struct.pack("<%df" % len(heights), *heights) + bytes(source["materials"])
    import hashlib
    assert B.terrain_content_hash(heights, (16, 8), (-8, -4), 1,
                                  source["water_level"], source["materials"]) == hashlib.sha256(payload).hexdigest()


def test_smooth_terrain_bakes_classes_for_on_demand_topology():
    # Gentle convex hill below the fixed crease threshold keeps smooth edge
    # classes in the compact grid; runtime reconstructs its triangles.
    width = height = 4
    heights = [1 - ((x - 2) ** 2 + (y - 2) ** 2) * 0.04
               for y in range(5) for x in range(5)]
    bank, _ = B.build_terrain_bank(heights, (width, height), (0, 0), 1, -10, [9] * 16)
    assert bank["face_count"] == 0 and bank["feature_count"] == 0
    assert bank["class_counts"].get(str(B.ABI["CLASS_SMOOTH_CANDIDATE"]), 0) > 0
    assert len(bank["terrain_classes"]) == 2 * 25
    reference = B.terrain_reference_edges(heights, (width, height), (0, 0), 1, -10, [9] * 16)
    smooth = [row for row in reference if row.get("class") == B.ABI["CLASS_SMOOTH_CANDIDATE"]]
    assert smooth and all(row["vertex0"][1] > 0 and row["vertex1"][1] > 0 for row in smooth)


def test_material_revision_changes_terrain_content_key():
    common = ([0.0] * 4, (1, 1), (0, 0), 1, 3)
    assert B.terrain_content_hash(*common, [9]) != B.terrain_content_hash(*common, [140])


def test_same_rf_digest_means_same_baked_shoreline_bits_and_nodes():
    # These doubles straddle the water plane, but every actual RF sample is
    # exactly on it. Baking before rounding invents two shoreline segments.
    heights = [3.06249999, 3.06250001, 3.06249999, 3.06250001]
    origin = (0.10000000001, -0.20000000001)
    cell, water = 1.000000001, 3.06250001
    a, _ = B.build_terrain_bank(heights, (1, 1), origin, cell, water, [9])
    b, _ = B.build_terrain_bank([B.f32(h) for h in heights], (1, 1),
                              tuple(B.f32(v) for v in origin), B.f32(cell), B.f32(water), [9])
    assert a["content_hash"] == b["content_hash"]
    assert a["class_counts"].get(str(B.ABI["CLASS_SHORELINE"]), 0) == 0
    assert B.encode_file(a, ["RF-parity"]) == B.encode_file(b, ["RF-parity"])


def test_canonical_runtime_map_uses_loader_grid_and_centered_origin():
    source = T.map_source(ROOT / "godot_project/assets/maps/game_map_y8.a3d", B)
    assert source["dims"] == [880, 896]
    assert source["origin"] == [-440.0, -448.0]
    assert source["cell"] == 1.0
    assert len(source["heights"]) == (880 + 1) * (896 + 1)
    assert len(source["materials"]) == 880 * 896
    assert source["water_level"] == B.terrain_water_surface_y()
