"""FL-4907 WATER_CLOUD_RETAINED_HIERARCHY_20261002: retained world-plane banks.

Offline only (no Godot, no GPU). The water surface and the cloud layer are
bounded quadtree banks built once per terrain digest by
scripts/true_ff_feature_bank.py. These tests pin the implicit cell addressing
(leaf_seq * PLANE_LEAF_FEATURES + (cz * PLANE_LEAF_CELLS + cx) * 2 + part),
the conservative node bounds (zero animated sheet displacement), the dry-node
pruning, the bounded far domain, the GD/GLSL/Python mirrors, and that the
deleted per-frame tile path stays deleted.
"""
import hashlib
import math
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import true_ff_feature_bank as bank  # noqa: E402

ABI = bank.ABI
FEATURES = ROOT / "godot_project/render/ascii/features"
BANK_DIR = ROOT / "godot_project/assets/world_partition/true_ff_feature_bank"
LEAF_CELLS = ABI["PLANE_LEAF_CELLS"]
LEAF_FEATURES = ABI["PLANE_LEAF_FEATURES"]


def _terrain(W=64, H=48, dry_block=True):
    """Synthetic map: sea everywhere except a high dry block in one corner."""
    cols = W + 1
    heights = [1.0] * (cols * (H + 1))
    if dry_block:
        for z in range(H // 2, H + 1):
            for x in range(W // 2, W + 1):
                heights[z * cols + x] = 10.0
    materials = [0] * (W * H)
    return heights, (W, H), (-W / 2.0, -H / 2.0), 1.0, 3.0625, materials


def _build(plane, **kw):
    heights, dims, origin, cell, water, materials = _terrain(**kw)
    th = bank.terrain_content_hash(heights, dims, origin, cell, water, materials)
    return bank.build_world_plane_bank(plane, th, heights, dims, origin, cell, water, materials), dims, origin


def _nodes(b):
    out = []
    for i in range(b["node_count"]):
        v = struct.unpack_from("<4f4f4I4I", b["nodes"], i * 64)
        ox = struct.unpack("<f", struct.pack("<I", v[14]))[0]
        oz = struct.unpack("<f", struct.pack("<I", v[15]))[0]
        out.append({"centre": v[0:3], "radius": v[3], "cone": v[7], "first_child": v[8],
                    "child_count": v[9], "first": v[10], "count": v[11], "flags": v[12],
                    "level": v[13], "origin": (ox, oz), "side": 2.0 * (v[0] - ox)})
    return out


def _records(b):
    return [struct.unpack_from("<4f4f4I4I", b["features"], i * 64) for i in range(b["feature_count"])]


@pytest.mark.parametrize("plane", ["water", "cloud"])
def test_implicit_addressing_is_contiguous_per_subtree_and_leaf_records_name_their_leaf(plane):
    b, _, _ = _build(plane)
    nodes, records = _nodes(b), _records(b)
    assert 2 * LEAF_CELLS * LEAF_CELLS == LEAF_FEATURES
    seen = set()
    for i, n in enumerate(nodes):
        assert n["flags"] & ABI["NODE_FLAG_PLANE"] and not n["flags"] & ABI["NODE_FLAG_TERRAIN"]
        leaf = bool(n["flags"] & ABI["NODE_FLAG_LEAF"])
        assert leaf == (n["child_count"] == 0) and n["child_count"] <= 4
        assert n["first"] % LEAF_FEATURES == 0 and n["count"] % LEAF_FEATURES == 0 and n["count"] > 0
        if leaf:
            seq = n["first"] // LEAF_FEATURES
            assert n["count"] == LEAF_FEATURES and seq not in seen
            seen.add(seq)
            rec = records[seq]
            assert rec[13] == i  # ident.y = leaf node
            assert (rec[0], rec[2]) == n["origin"] and rec[3] == n["side"] / LEAF_CELLS
            continue
        kids = [nodes[c] for c in range(n["first_child"], n["first_child"] + n["child_count"])]
        assert all(k["level"] == n["level"] + 1 for k in kids)
        # Children are contiguous in address space and tile the parent range.
        assert kids[0]["first"] == n["first"]
        for a, c in zip(kids, kids[1:]):
            assert a["first"] + a["count"] == c["first"]
        assert sum(k["count"] for k in kids) == n["count"]
    assert seen == set(range(b["feature_count"]))
    assert nodes[0]["count"] == b["feature_count"] * LEAF_FEATURES


@pytest.mark.parametrize("plane", ["water", "cloud"])
def test_address_maps_to_exact_cell_geometry_and_stable_id(plane):
    b, _, _ = _build(plane)
    records = _records(b)
    seed = bank.fnv1a32(bytes.fromhex(b["content_hash"]))
    for seq in (0, len(records) // 2, len(records) - 1):
        rec = records[seq]
        assert rec[14] == seed  # ident.z: FNV state after digest bytes
        for cell in (0, 9, LEAF_CELLS * LEAF_CELLS - 1):
            for part in (0, 1):
                address = seq * LEAF_FEATURES + cell * 2 + part
                cx, cz = cell % LEAF_CELLS, cell // LEAF_CELLS
                lo = (rec[0] + cx * rec[3], rec[2] + cz * rec[3])
                assert lo[0] < rec[4] and lo[1] < rec[6]  # inside the leaf rect
                # Mirror of tff_plane_cell's stable id == ABI.stable_id(digest, address).
                h = seed
                for k in range(4):
                    h = ((h ^ ((address >> (8 * k)) & 0xFF)) * ABI["FNV_PRIME"]) & 0xFFFFFFFF
                assert h == bank.stable_id(b["content_hash"], address)


@pytest.mark.parametrize("plane", ["water", "cloud"])
def test_node_spheres_bound_every_cell_and_child_with_zero_sheet_displacement(plane):
    b, _, _ = _build(plane)
    assert b["plane_max_displacement"] == 0.0
    nodes = _nodes(b)
    for n in nodes:
        assert n["centre"][1] == 0.0 and n["cone"] == ABI["CONE_UNDEFINED"]
        ox, oz = n["origin"]
        for corner in ((ox, oz), (ox + n["side"], oz), (ox, oz + n["side"]), (ox + n["side"], oz + n["side"])):
            assert math.hypot(corner[0] - n["centre"][0], corner[1] - n["centre"][2]) <= n["radius"]
        for c in range(n["first_child"], n["first_child"] + n["child_count"]):
            k = nodes[c]
            d = math.hypot(k["centre"][0] - n["centre"][0], k["centre"][2] - n["centre"][2])
            assert d + k["radius"] <= n["radius"] * (1 + 1e-6)


def test_dry_land_nodes_are_pruned_and_outside_water_is_retained():
    wet, dims, origin = _build("water", dry_block=False)
    dry, _, _ = _build("water", dry_block=True)
    assert dry["feature_count"] < wet["feature_count"]
    W, H = dims
    for n in _nodes(dry):
        if not n["flags"] & ABI["NODE_FLAG_LEAF"]:
            continue
        ox, oz = n["origin"]
        inside = ox >= origin[0] and oz >= origin[1] and ox + n["side"] <= origin[0] + W \
            and oz + n["side"] <= origin[1] + H
        # A retained in-map leaf is never wholly inside the high dry block
        # (whose corners are all strictly above the sea plane).
        if inside:
            assert not (ox >= origin[0] + W / 2 + 1 and oz >= origin[1] + H / 2 + 1)
    cloud, _, _ = _build("cloud", dry_block=True)
    assert len(cloud["nodes"]) // 64 == cloud["node_count"]  # clouds are never pruned by land


@pytest.mark.parametrize("plane", ["water", "cloud"])
def test_bounded_domain_has_fine_cells_near_the_map_and_coarse_far_leaves(plane):
    b, dims, origin = _build(plane)
    rx, rz, extent = b["plane_domain"]
    assert extent == ABI["PLANE_ROOT_EXTENT"] and rx <= origin[0] - 4000 and rz <= origin[1] - 4000
    finest = b["plane_cell"]
    sides = {}
    for n in _nodes(b):
        if n["flags"] & ABI["NODE_FLAG_LEAF"]:
            sides.setdefault(n["side"] / LEAF_CELLS, 0)
            sides[n["side"] / LEAF_CELLS] += 1
            ox, oz = n["origin"]
            gap = max(origin[0] - (ox + n["side"]), ox - (origin[0] + dims[0]),
                      origin[1] - (oz + n["side"]), oz - (origin[1] + dims[1]), 0.0)
            if n["side"] / LEAF_CELLS > finest:
                assert gap >= ABI["PLANE_SUBDIVISION"] * n["side"]  # only far leaves are coarse
    assert min(sides) == finest and max(sides) > finest
    assert b["depth"] < ABI["HIERARCHY_LEVELS"]


def test_content_hash_mirror_and_committed_banks_decode():
    gd = (FEATURES / "TrueFFFeatureBank.gd").read_text(encoding="utf-8")
    assert '"%s:%s:%s" % [ABI.PLANE_ENCODING, plane, terrain_hash]' in gd
    expect = hashlib.sha256(f"{ABI['PLANE_ENCODING']}:water:{'ab' * 32}".encode()).hexdigest()
    assert bank.world_plane_content_hash("water", "ab" * 32) == expect
    index = __import__("json").loads((BANK_DIR / "index.v1.json").read_text(encoding="utf-8"))["banks"]
    planes = {v["plane"]: (k, v) for k, v in index.items() if v.get("kind") == "world_plane"}
    assert set(planes) == {"water", "cloud"}
    for plane, (h, entry) in planes.items():
        assert h == bank.world_plane_content_hash(plane, entry["plane_terrain_hash"])
        assert index[entry["plane_terrain_hash"]]["kind"] == "terrain"
        decoded = bank.decode_file((BANK_DIR / entry["file"]).read_bytes())
        header = decoded["header"]
        assert header["kind"] == "world_plane" and header["encoding"] == ABI["PLANE_ENCODING"]
        assert header["node_count"] == entry["node_count"] and header["face_count"] == 0
        assert len(decoded["features"]) == 64 * header["feature_count"]


def test_glsl_and_gd_mirror_plane_constants():
    inc = (FEATURES / "true_ff_feature_abi.glslinc").read_text(encoding="utf-8")
    for gd_name, gl_name in (("NODE_FLAG_PLANE", "TFF_NODE_FLAG_PLANE"),
                             ("PLANE_LEAF_CELLS", "TFF_PLANE_LEAF_CELLS"),
                             ("PLANE_LEAF_FEATURES", "TFF_PLANE_LEAF_FEATURES")):
        m = re.search(rf"const uint {gl_name} = (\d+)u;", inc)
        assert m and int(m.group(1)) == ABI[gd_name], gd_name


def test_per_frame_tile_enumeration_is_deleted():
    for name in ("TrueFFWaterFeatures.gd", "TrueFFSkyFeatures.gd", "TrueFFSourceDomain.gd",
                 "TrueFFWorldFeatureSources.gd", "TrueFFFeatureBank.gd"):
        text = (FEATURES / name).read_text(encoding="utf-8")
        for banned in ("Domain.tiles", "plane_window", "world_tile", "WATER_CREST_TILE_CELLS",
                       "source_tile_cell", "water_lookup", "WATER_SURFACE_ENCODING"):
            assert banned not in text, (name, banned)
    reps = (FEATURES / "true_ff_surface_representatives.glslinc").read_text(encoding="utf-8")
    assert "tff_source_tile_ordinal" not in reps and "TFF_WATER_CREST_TILE_CELLS" not in reps
    assert "tff_plane_cell_at(" in reps
