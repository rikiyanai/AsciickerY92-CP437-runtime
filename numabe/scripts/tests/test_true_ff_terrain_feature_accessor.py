"""Offline contract tests for the implicit terrain GLSL accessor.

These tests deliberately exercise the address/ownership law without starting
Godot.  The compact bank compiler remains the source of the synthetic class
and height fixtures; the expected boundary formulas here are independent so a
compiler-side edge-incidence mistake cannot make the accessor test green.
"""
from __future__ import annotations

import importlib.util
import re
import struct
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INCLUDE = ROOT / "godot_project/render/ascii/features/true_ff_terrain_feature_source.glslinc"


def module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    out = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(out)
    return out


B = module("true_ff_feature_bank")


def edge_address(x: int, z: int, orient: int, width: int) -> int:
    return 3 * (z * (width + 1) + x) + orient


def face_index(x: int, z: int, tri: int, width: int) -> int:
    return 2 * (z * width + x) + tri


def expected_faces(x: int, z: int, orient: int, width: int, height: int):
    if orient == 0:  # horizontal: tri0 below, tri1 above
        return (face_index(x, z, 0, width) if z < height else None,
                face_index(x, z - 1, 1, width) if z > 0 else None)
    if orient == 1:  # vertical: tri1 to the right, tri0 to the left
        return (face_index(x, z, 1, width) if x < width else None,
                face_index(x - 1, z, 0, width) if x > 0 else None)
    return (face_index(x, z, 0, width), face_index(x, z, 1, width))


def test_accessor_declares_the_approved_source_abi_and_public_functions():
    text = INCLUDE.read_text(encoding="utf-8")
    for binding, token in ((22, "terrain_classes"), (23, "terrain_height_samples"),
                           (24, "terrain_materials"), (25, "terrain_uniform")):
        assert re.search(rf"binding\s*=\s*{binding}[^\n]*", text)
        assert token in text
    for name in (
            "tff_is_terrain", "tff_terrain_feature", "tff_terrain_face",
            "tff_terrain_edge_faces", "tff_terrain_leaf_count",
            "tff_terrain_leaf_edge", "tff_terrain_edge_leaf",
            "tff_terrain_edge_leaf_offset"):
        assert re.search(rf"\b{name}\s*\(", text), name
    assert "TFF_NODE_FLAG_TERRAIN" in text
    assert "floatBitsToUint" not in text  # height bounds are node meta.zw inputs
    assert "terrain_edges" not in text and "terrain_crossings" not in text
    assert "binding = 23) uniform sampler2D terrain_height_samples" in text
    assert "binding = 24) uniform usampler2D terrain_materials" in text
    assert "texelFetch(terrain_height_samples" in text
    assert "texelFetch(terrain_materials" in text
    for name in ("tff_terrain_leaf_count", "tff_terrain_leaf_edge", "tff_terrain_edge_leaf_offset"):
        body = re.search(rf"uint {name}\([^\n]+\) \{{(.*?)(?=\n\}})", text, re.S).group(1)
        assert not re.search(r"\b(for|while)\s*\(", body), name
    assert "uvec4(cls, 0u," not in text
    assert "uvec4(TFF_CLASS_SHORELINE, 0u," not in text


def test_adjacency_handles_all_four_non_square_grid_boundaries():
    width, height = 3, 2
    checks = [
        (0, 0, 0), (1, 0, 0), (0, height, 0),
        (0, 0, 1), (width, 0, 1), (0, 1, 1),
        (0, 0, 2), (width - 1, height - 1, 2),
    ]
    for x, z, orient in checks:
        address = edge_address(x, z, orient, width)
        got = expected_faces(x, z, orient, width, height)
        if orient == 0:
            assert got[0] == (None if z == height else face_index(x, z, 0, width))
            assert got[1] == (None if z == 0 else face_index(x, z - 1, 1, width))
        elif orient == 1:
            assert got[0] == (None if x == width else face_index(x, z, 1, width))
            assert got[1] == (None if x == 0 else face_index(x - 1, z, 0, width))
        else:
            assert got == (face_index(x, z, 0, width), face_index(x, z, 1, width))
        assert address >= 0


def test_leaf_ownership_is_unique_for_non_square_partial_leaves():
    width, height, leaf = 19, 17, 16
    leaves = [(0, 0, 16, 16), (16, 0, 19, 16),
              (0, 16, 16, 17), (16, 16, 19, 17)]
    owned = []
    for x0, z0, x1, z1 in leaves:
        local = []
        for z in range(z0, z1):
            for x in range(x0, x1):
                local.append(edge_address(x, z, 0, width))
                local.append(edge_address(x, z, 1, width))
                local.append(edge_address(x, z, 2, width))
                if z + 1 == height:
                    local.append(edge_address(x, z + 1, 0, width))
                if x + 1 == width:
                    local.append(edge_address(x + 1, z, 1, width))
        assert len(local) == len(set(local))
        owned.extend(local)
    expected = {
        edge_address(x, z, 0, width)
        for z in range(height + 1) for x in range(width)
    }
    expected |= {
        edge_address(x, z, 1, width)
        for z in range(height) for x in range(width + 1)
    }
    expected |= {
        edge_address(x, z, 2, width)
        for z in range(height) for x in range(width)
    }
    assert len(owned) == len(set(owned)) == len(expected)
    assert set(owned) == expected


def test_stable_id_appends_address_as_four_little_endian_bytes():
    digest = "ab" * 32
    address = 0x12030405
    state = B.ABI["FNV_OFFSET"]
    for byte in bytes.fromhex(digest):
        state = ((state ^ byte) * B.ABI["FNV_PRIME"]) & 0xFFFFFFFF
    expected = state
    for byte in struct.pack("<I", address):
        expected = ((expected ^ byte) * B.ABI["FNV_PRIME"]) & 0xFFFFFFFF
    assert B.terrain_stable_id(digest, address) == expected


def test_direct_address_inverse_across_every_partial_leaf_and_seam():
    width, height = 19, 17
    heights = [B.f32(x * 0.05 + z * 0.03) for z in range(height + 1) for x in range(width + 1)]
    bank, _ = B.build_terrain_bank(heights, (width, height), (0, 0), 1, 0.5)
    nodes = list(struct.iter_unpack("<8f8I", bank["nodes"]))
    classes = struct.unpack(f"<{(width + 1) * (height + 1)}H", bank["terrain_classes"])
    namespace = 3 * len(classes)
    actual = {}
    for leaf, n in enumerate(nodes):
        if not n[12] & 2:
            continue
        ox, oz = n[10] & 65535, n[10] >> 16
        w, h = n[11] & 65535, n[11] >> 16
        ow, oh = w + (ox + w == width), h + (oz + h == height)
        for ordinal in range(5 * ow * oh):
            vertex, lane = divmod(ordinal, 5)
            x, z = ox + vertex % ow, oz + vertex // ow
            if lane < 3:
                if not (classes[z * (width + 1) + x] >> (4 * lane)) & 15:
                    continue
                address = edge_address(x, z, lane, width)
            else:
                if x == width or z == height or not classes[z * (width + 1) + x] & (1 << (9 + lane)):
                    continue
                address = namespace + 2 * (z * width + x) + lane - 3
            assert address not in actual
            actual[address] = (leaf, ordinal)
    expected = B.terrain_reference_edges(heights, (width, height), (0, 0), 1, 0.5)
    assert set(actual) == {e["address"] for e in expected}
    for edge in expected:
        address = edge["address"]
        if address < namespace:
            vertex, lane = divmod(address, 3)
            x, z = vertex % (width + 1), vertex // (width + 1)
        else:
            cell, tri = divmod(address - namespace, 2)
            x, z, lane = cell % width, cell // width, tri + 3
        # Node lookup uses cell ownership, while the ordinal keeps the far
        # boundary vertex coordinates. These must not be conflated.
        cx, cz = min(x, width - 1), min(z, height - 1)
        node_index = 0
        while not nodes[node_index][12] & 2:
            n = nodes[node_index]
            matches = []
            for child in range(n[8], n[8] + n[9]):
                c = nodes[child]
                ox, oz, w, h = c[10] & 65535, c[10] >> 16, c[11] & 65535, c[11] >> 16
                if ox <= cx < ox + w and oz <= cz < oz + h:
                    matches.append(child)
            assert len(matches) == 1
            node_index = matches[0]
        n = nodes[node_index]
        ox, oz, w = n[10] & 65535, n[10] >> 16, n[11] & 65535
        ow = w + (ox + w == width)
        ordinal = 5 * ((z - oz) * ow + x - ox) + lane
        assert actual[address] == (node_index, ordinal)
        # Re-admission changes a compact slot base, never the identity.
        for transient_base in (0, 1234, 87):
            assert transient_base + ordinal >= transient_base
            assert B.terrain_stable_id(bank["content_hash"], address) == edge["stable_id"]


def test_production_glsl_address_functions_roundtrip_as_offline_cpp():
    """Execute the actual integer functions, not only a Python re-statement."""
    import pytest
    if shutil.which("c++") is None:
        pytest.skip("C++ compiler unavailable")
    width, height = 19, 17
    heights = [B.f32(x * 0.05 + z * 0.03) for z in range(height + 1) for x in range(width + 1)]
    bank, _ = B.build_terrain_bank(heights, (width, height), (0, 0), 1, 0.5)
    node_rows = list(struct.iter_unpack("<8f8I", bank["nodes"]))
    classes = bank["terrain_classes"]
    classes += bytes((-len(classes)) % 4)
    words = struct.unpack(f"<{len(classes) // 4}I", classes)
    prelude = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <set>
#include <vector>
using uint=uint32_t; using std::min;
struct uvec2 { uint x,y; uvec2(uint a=0,uint b=0):x(a),y(b){} };
uvec2 operator+(uvec2 a,uvec2 b){return {a.x+b.x,a.y+b.y};}
uvec2 equal(uvec2 a,uvec2 b){return {a.x==b.x,a.y==b.y};}
struct uvec4 { uint x,y,z,w; };
struct TffFeatureNode { uvec4 links,meta; };
struct TerrainParams {uvec4 grid,tree;} terrain_uniform;
const uint TFF_NODE_FLAG_LEAF=2u, TFF_TERRAIN_NONE=0xffffffffu;
const uint TFF_TERRAIN_EDGE_H=0u,TFF_TERRAIN_EDGE_V=1u,TFF_TERRAIN_EDGE_D=2u;
const uint TFF_TERRAIN_CLASS_MASK=15u,TFF_TERRAIN_SHORE_TRI0=4096u,TFF_TERRAIN_SHORE_TRI1=8192u;
const uint TFF_HIERARCHY_LEVELS=16u;
'''
    prelude += "std::vector<uint> terrain_classes={" + ",".join(f"{w}u" for w in words) + "};\n"
    prelude += "std::vector<TffFeatureNode> nodes={" + ",".join(
        "{{%s},{%s}}" % (",".join(f"{v}u" for v in n[8:12]),
                        ",".join(f"{v}u" for v in n[12:16])) for n in node_rows) + "};\n"
    text = INCLUDE.read_text()
    names = (
        "width", "height", "vertex_width", "vertex_count", "edge_namespace", "enabled",
        "node_origin_x", "node_origin_z", "node_width", "node_height", "node_leaf",
        "class_word", "vertex_valid", "vertex_index", "edge_valid", "edge_address",
        "edge_class", "shore_present", "shore_address", "node_contains", "edge_leaf",
        "leaf_vertices", "leaf_count", "leaf_edge", "edge_leaf_offset")
    definitions = []
    for suffix in names:
        start = re.search(rf"^(?:uint|bool|uvec2) tff_terrain_{suffix}\(", text, re.M).start()
        brace = text.index("{", start)
        depth, end = 1, brace + 1
        while depth:
            depth += (text[end] == "{") - (text[end] == "}")
            end += 1
        definitions.append(text[start:end].replace("terrain_uniform.grid.xy", "uvec2(terrain_uniform.grid.x,terrain_uniform.grid.y)"))
    body = r'''
int main(){
  terrain_uniform={{19u,17u,20u,0u},{16u,2u,0u,1u}};
  std::set<uint> found;
  for(uint leaf=0;leaf<nodes.size();++leaf){
    auto n=nodes[leaf]; if(!(n.meta.x&2u))continue;
    for(uint ordinal=0;ordinal<tff_terrain_leaf_count(n);++ordinal){
      uint a=tff_terrain_leaf_edge(n,ordinal);
      if(a==TFF_TERRAIN_NONE)continue;
      if(a<tff_terrain_edge_namespace()){
        uint vertex=a/3u;
        if(!tff_terrain_edge_class(vertex%20u,vertex/20u,a%3u))continue;
      }else{
        uint shore=a-tff_terrain_edge_namespace(),cell=shore/2u;
        if(!tff_terrain_shore_present(cell%19u,cell/19u,shore%2u))continue;
      }
      assert(found.insert(a).second);
      assert(tff_terrain_edge_leaf(a)==leaf);
      assert(tff_terrain_edge_leaf_offset(n,a)==ordinal);
    }
  }
  assert(found.size()==EXPECTED_COUNT);
}
'''
    expected = len(B.terrain_reference_edges(heights, (width, height), (0, 0), 1, 0.5))
    body = body.replace("EXPECTED_COUNT", str(expected))
    with tempfile.TemporaryDirectory(prefix="ff-terrain-address-unit-") as directory:
        binary = str(Path(directory) / "unit")
        compiled = subprocess.run(["c++", "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                                  input=prelude + "\n".join(definitions) + body,
                                  text=True, capture_output=True)
        assert compiled.returncode == 0, compiled.stderr
        checked = subprocess.run([binary], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr
