"""Offline source/ABI and conservative-allocation checks, not runtime proof."""
from pathlib import Path
import importlib.util
import re
import struct

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"


def planes(projection):
    # GDScript reservation quantizes the Projection's columns to the exact
    # float32 camera facts before making a deliberately conservative bound.
    r = np.asarray(projection, dtype=np.float32).astype(float)
    return (r[3] + r[0], r[3] - r[0], r[3] + r[1], r[3] - r[1], r[2], r[3] - r[2])


def reserved(sphere, projection):
    c, radius = np.asarray(sphere[:3]), sphere[3]
    for plane in planes(projection):
        extent = radius * np.linalg.norm(plane[:3])
        error = 0.00001 * (1 + np.abs(plane[:3] * c).sum() + abs(plane[3]) + extent)
        if np.dot(plane[:3], c) + plane[3] < -extent - error:
            return False
    return True


def gpu_kept(sphere, projection):
    # GPU's normalized-plane float32 form: cull only a fully outside sphere.
    c = np.asarray(sphere[:3], dtype=np.float32)
    for p in planes(projection):
        p = np.asarray(p, dtype=np.float32)
        p /= np.maximum(np.linalg.norm(p[:3]), np.float32(1e-30))
        if np.dot(p[:3], c) + p[3] < -np.float32(sphere[3]):
            return False
    return True


def test_reservation_contains_gpu_frustum_survivors_at_tangent_planes():
    rng = np.random.default_rng(4907)
    matrices = [np.eye(4)]
    for _ in range(12):
        matrix = rng.normal(size=(4, 4))
        matrix[3] = [0, 0, -1, 0]
        matrices.append(matrix)
    for matrix in matrices:
        for _ in range(500):
            sphere = np.r_[rng.uniform(-2000, 2000, 3), rng.uniform(0, 1000)]
            if gpu_kept(sphere, matrix):
                assert reserved(sphere, matrix)
        # Construct contact with each plane, then perturb across its boundary.
        for p in planes(matrix):
            n = p[:3]
            if np.linalg.norm(n) < 1e-12:
                continue
            radius = 20.0
            centre = n * (-p[3] - radius * np.linalg.norm(n)) / np.dot(n, n)
            for epsilon in (-1e-5, 0, 1e-5):
                sphere = np.r_[centre + epsilon * n, radius]
                if gpu_kept(sphere, matrix):
                    assert reserved(sphere, matrix)


def test_reservation_does_not_follow_total_grid_area():
    assert reserved((0, 0, 0.5, 0.1), np.eye(4))
    assert not reserved((100, 0, 0.5, 0.1), np.eye(4))
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    assert "_page_candidate_capacity(_page_budget(camera_facts))" in host
    capacity = host.split("func _page_candidate_capacity(", 1)[1].split("\nfunc ", 1)[0]
    assert "MAX_BUFFER_BYTES / SITE_BYTES" in capacity
    assert "MAX_BUFFER_BYTES / WITNESS_LANE_BYTES" in capacity
    assert "supports_dispatch_count(required)" in capacity
    assert "width" not in capacity and "height" not in capacity
    assert "(width + 1) * (height + 1) * 5" not in host


def test_compact_bank_rejects_old_explicit_terrain_encoding():
    source = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    assert 'header.get("encoding", "") != ABI.TERRAIN_ENCODING' in source
    assert 'counts["features"] != 0 or counts["faces"] != 0' in source
    assert 'counts["terrain_classes"] = (int(dims[0]) + 1) * (int(dims[1]) + 1)' in source
    assert "_upload_terrain_source(bank" in source
    assert "digest_state ^ byte" in source
    assert 'terrain_bindings.get("heights")' in source
    assert 'terrain_bindings.get("materials")' in source
    assert '_terrain_source = {"dims": dims, "origin": origin, "cell": cell}' in source
    assert "_terrain_heights = _storage" not in source
    assert "_terrain_materials = _storage" not in source


def test_node_to_compact_crossing_map_is_epoch_guarded():
    terrain = (FEATURES / "true_ff_terrain_feature_source.glslinc").read_text()
    extract = (FEATURES / "true_ff_feature_extract_common.glslinc").read_text()
    assert "uint tff_terrain_edge_leaf(uint address)" in terrain
    assert "uint tff_terrain_edge_leaf_offset(TffFeatureNode node, uint address)" in terrain
    assert "tff_terrain_edge_leaf_offset(source_node, feature_local)" in extract
    assert "state.native_row.w != cam.counts.y" in extract
    assert "slot_keys[leaf.w + middle].z < ordinal" in extract
    assert "return e.x == cam.counts.y ? e.y : TFF_NONE" in extract
    assert "terrain_leaf_slots" not in terrain + extract
    assert "inst_state[instance].w + feature_local" not in extract


def test_leaf_work_is_parallel_and_uses_indirect_surviving_leaf_count():
    shader = (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    assert "void demand(uint i)" in shader
    assert "void admit(uint i)" in shader
    assert "tff_lane_range(slots, gl_LocalInvocationID.x, begin, end)" in shader
    assert "tff_group_exclusive(gl_LocalInvocationID.x, descriptors, descriptor_total)" in shader
    assert "tff_lane_range(descriptors, lane, begin, end)" in shader
    assert "tff_group_exclusive(lane, kept, kept_total)" in shader
    assert "for (uint ordinal = begin; ordinal < end; ++ordinal)" in shader
    assert "barrier();" in shader  # contiguous deterministic admission prefix
    assert "tff_publish_dispatch_count(TFF_SLOT_LEAF_GROUPS, slot + 1u, 1u)" in shader
    assert "for kernel in [2, 37, 38, 39]:" in host
    assert "TRAVERSE_KERNELS[kernel], [kernel, 0, 0, revision], SLOT_LEAF_GROUPS" in host
    assert "TRAVERSE_KERNELS[4], [4, 0, 0, revision], SLOT_LEAF_GROUPS" in host
    assert "tff_leaf_feature_count" not in shader


def test_terrain_uniform_bindings_are_disjoint_from_existing_passes():
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    assert "_uniform(22 + i, kind, [terrain_rids[i]])" in host
    assert "_uniform(22 + i, sampled, [_sampler, terrain_rids[i]])" in host
    terrain = (FEATURES / "true_ff_terrain_feature_source.glslinc").read_text()
    assert "binding = 22" in terrain and "terrain_classes" in terrain
    assert "binding = 23" in terrain and "terrain_height_samples" in terrain
    assert "binding = 24" in terrain and "terrain_materials" in terrain
    assert "binding = 25" in terrain and "terrain_uniform" in terrain
    work = (FEATURES / "true_ff_feature_work.glslinc").read_text()
    assert "TFF_SLOT_COUNT = 60u" in work
    assert "const SLOT_COUNT := 60" in host


def test_serialized_compact_bank_matches_runtime_field_contract():
    spec = importlib.util.spec_from_file_location("terrain_bank_contract",
                                                ROOT / "scripts/true_ff_feature_bank.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    width, height = 17, 3
    source = [0.02 * x * z for z in range(height + 1) for x in range(width + 1)]
    bank, _ = compiler.build_terrain_bank(source, (width, height), (-4, -2), 1, 0.4)
    encoded = compiler.encode_file(bank, ["offline-test"])
    decoded = compiler.decode_file(encoded)
    h = decoded["header"]
    assert h["encoding"] == "implicit_grid_v1"
    assert h["feature_count"] == h["face_count"] == 0
    assert not decoded["features"] and not decoded["faces"]
    assert h["leaf_cells"] == 16
    assert h["terrain_class_count"] == (width + 1) * (height + 1)
    assert len(decoded["terrain_classes"]) == 2 * h["terrain_class_count"]
    nodes = list(struct.iter_unpack("<8f8I", decoded["nodes"]))
    for n in nodes:
        assert n[12] & 4  # implicit-terrain discriminator at meta.x
        assert (n[11] & 65535) > 0 and (n[11] >> 16) > 0
        lo, hi = struct.unpack("<2f", struct.pack("<2I", n[14], n[15]))
        assert lo <= hi
        if n[12] & 2:
            assert n[9] == 0
        else:
            assert n[9] > 0 and n[8] + n[9] <= len(nodes)
