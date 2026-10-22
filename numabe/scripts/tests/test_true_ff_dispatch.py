"""Offline red-first checks for the True FF count dispatch law.

The helper bodies are syntax-translated from TrueFFFeaturePasses.gd by the
existing lifetime harness.  No Godot device or runtime capture is involved.
"""

import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import pytest


_LIFETIME = Path(__file__).with_name("test_true_ff_pass_buffer_lifetime.py")
_SPEC = importlib.util.spec_from_file_location("true_ff_lifetime", _LIFETIME)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MODULE)
candidate_host = _MODULE.candidate_host
FEATURES = _MODULE.SOURCE.parent


@pytest.fixture(scope="module")
def dispatch_unit(tmp_path_factory):
    compiler = shutil.which("c++")
    assert compiler, "production dispatch arithmetic requires an offline C++ compiler"
    passes = (FEATURES / "true_ff_feature_passes.glslinc").read_text()
    publish = re.search(r"void tff_publish_dispatch_count\([^}]+\}", passes).group()
    program = r'''
#include <algorithm>
#include <cstdint>
#include <iostream>
using uint=std::uint32_t;
using std::min; using std::max;
struct uvec2 { uint x,y; uvec2(uint a,uint b):x(a),y(b){} };
struct Id { uint x=0,y=0; } gl_WorkGroupID,gl_NumWorkGroups,gl_LocalInvocationID;
struct Camera { struct Counts { uint w; } counts; } cam;
uint args[8];
void atomicMax(uint &word,uint value) { word=std::max(word,value); }
''' + (FEATURES / "true_ff_dispatch.glslinc").read_text() + publish + r'''
int main() {
    uint count,lanes,tile;
    while(std::cin>>count>>lanes>>tile) {
        cam.counts.w=tile; args[0]=0; args[1]=1;
        // Completion order does not change the conservative published shape.
        tff_publish_dispatch_count(0,count,lanes);
        tff_publish_dispatch_count(0,count/2,lanes);
        if(count) tff_publish_dispatch_count(0,1,lanes);
        gl_NumWorkGroups.x=args[0]; gl_NumWorkGroups.y=args[1];
        std::uint64_t coverage=std::uint64_t(args[0])*args[1]*lanes;
        uint invalid=0;
        for(uint y=0;y<args[1];++y) {
            if(!args[0]) continue;
            for(uint x: {0u,args[0]-1}) {
                gl_WorkGroupID={x,y}; gl_LocalInvocationID.x=lanes-1;
                std::uint64_t expected=(std::uint64_t(y)*args[0]+x)*lanes+lanes-1;
                if(tff_dispatch_linear_invocation_id(lanes)!=expected) ++invalid;
            }
        }
        std::cout<<args[0]<<' '<<args[1]<<' '<<coverage<<' '<<invalid<<'\n';
    }
}
'''
    binary = tmp_path_factory.mktemp("true-ff-dispatch") / "dispatch_unit"
    built = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", str(binary)],
                           input=program, text=True, capture_output=True)
    assert built.returncode == 0, built.stderr
    return binary


def test_production_shader_publication_and_flattening(dispatch_unit):
    cases = [(count, lanes, tile) for lanes in (1, 64) for tile in (2, 65535)
             for count in (0, 1, lanes, lanes + 1, tile * lanes, tile * lanes + 1)]
    result = subprocess.run([str(dispatch_unit)], text=True, capture_output=True,
                            input="\n".join(" ".join(map(str, case)) for case in cases))
    assert result.returncode == 0, result.stderr
    rows = [tuple(map(int, line.split())) for line in result.stdout.splitlines()]
    assert len(rows) == len(cases)
    for (count, lanes, tile), (x, y, coverage, invalid) in zip(cases, rows):
        groups = (count + lanes - 1) // lanes
        assert (x, y) == (min(groups, tile), max(1, (groups + tile - 1) // tile))
        assert coverage >= count and invalid == 0


def test_leaf_producer_and_reservation_use_one_group_per_leaf():
    source = (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    assert "tff_publish_dispatch_count(TFF_SLOT_LEAF_GROUPS, slot + 1u, 1u)" in source
    assert "supports_dispatch_count(candidate_slots, 1)" in _MODULE.body("_ensure_buffers")
    host = _MODULE.SOURCE.read_text()
    assert "for kernel in [2, 37, 38, 39]:" in host
    assert "TRAVERSE_KERNELS[kernel], [kernel, 0, 0, revision], SLOT_LEAF_GROUPS" in host
    assert "TRAVERSE_KERNELS[4], [4, 0, 0, revision], SLOT_LEAF_GROUPS" in host


def test_invalid_lane_sizes_and_padded_u32_overflow_are_rejected():
    state, _, _ = candidate_host()
    state["_dispatch_groups_x"] = 65535
    state["_dispatch_groups_y"] = 65535
    supports = state["supports_dispatch_count"]
    assert not supports(1, 0)
    assert not supports(1, -1)
    # 2-D rectangular padding must not wrap the linear invocation ID into
    # the valid prefix. That would process some features twice.
    assert not supports(0xffffffff, 64)


def test_negative_bank_counts_are_not_normalized_to_empty_arenas():
    code = _MODULE.body("_valid_bank_counts")
    code = re.sub(r"var (\w+)(?:: \w+)?\s*(?::=|=)", r"\1 =", code)
    code = re.sub(r"\btrue\b", "True", code)
    code = re.sub(r"\bfalse\b", "False", code)
    scope = {"MAX_LEVELS": 16}
    exec("def valid(bank):\n" + code, scope)
    valid = scope["valid"]
    assert valid({})
    for field in ("instance_count", "instance_node_total", "instance_feature_total", "feature_total"):
        assert valid({field: 0})
        assert not valid({field: -1})
        assert not valid({field: 0x100000000})
    for field in ("face_count", "face_base", "feature_count", "feature_base"):
        asset = dict(face_count=0, face_base=0, feature_count=0, feature_base=0)
        assert valid({"assets": [asset]})
        assert not valid({"assets": [asset | {field: -1}]})
    assert not valid({"max_level": -1})
    assert not valid({"max_level": 16})
    ensure = _MODULE.body("_ensure_buffers")
    assert "_valid_bank_scalars(bank)" in ensure
    assert 'for field in ["face_count", "face_base", "feature_count", "feature_base"]:' in ensure
    assert 'asset.get("content_hash", "")' in ensure
    assert "if not supports_dispatch_count(face_count):" in _MODULE.body("_build_adjacency")
    direct = _MODULE.body("_dispatch_host_count")
    assert direct.index("if not supports_dispatch_count(count)") < direct.index("if count == 0")
    assert '"host_dispatch_count_outside_device_abi"' in direct


def test_empty_and_boundary_counts_are_covered_once():
    state, _, _ = candidate_host()
    state["_dispatch_groups_x"] = 65535
    state["_dispatch_groups_y"] = 4
    dimensions = state["_dispatch_dimensions"]

    assert (dimensions(0, 64, 65535).x, dimensions(0, 64, 65535).y) == (0, 1)
    assert (dimensions(64, 64, 65535).x, dimensions(64, 64, 65535).y) == (1, 1)
    assert (dimensions(65, 64, 65535).x, dimensions(65, 64, 65535).y) == (2, 1)
    boundary = 65535 * 64
    assert (dimensions(boundary, 64, 65535).x,
            dimensions(boundary, 64, 65535).y) == (65535, 1)
    assert (dimensions(boundary + 1, 64, 65535).x,
            dimensions(boundary + 1, 64, 65535).y) == (65535, 2)

    count = boundary + 1
    dims = dimensions(count, 64, 65535)
    covered = sum(64 for _ in range(dims.x * dims.y))
    assert covered >= count
    assert dims.x * dims.y == 65535 * 2


def test_device_x_and_y_limits_reject_before_indirect_dispatch():
    state, _, _ = candidate_host()
    state["_dispatch_groups_x"] = 2
    state["_dispatch_groups_y"] = 2
    supports = state["supports_dispatch_count"]

    assert supports(256, 64)
    assert not supports(257, 64)
    assert state["supports_tiled_dispatch"](0, 64, 65535)
    assert not state["supports_tiled_dispatch"](1, 64, 3)


def test_large_dispatch_reservation_uses_real_byte_size():
    state, rd, _ = candidate_host()
    state["WITNESS_LANE_BYTES"] = 336
    state["_dispatch_groups_x"] = 65535
    state["_dispatch_groups_y"] = 2
    demand = 65535 * 64 + 1

    capacity = state["_page_candidate_capacity"](demand)
    assert capacity == demand
    expected_bytes = capacity * state["SITE_BYTES"]
    assert state["resize"]({"candidates": expected_bytes, "sites": expected_bytes})
    assert state["_buffer_sizes"]["candidates"] == expected_bytes
    assert state["_buffer_sizes"]["sites"] == expected_bytes
    physical = {rid: size for rid, size, _ in rd.created}
    assert physical[state["_buffers"]["candidates"]] == expected_bytes
    assert physical[state["_buffers"]["sites"]] == expected_bytes
