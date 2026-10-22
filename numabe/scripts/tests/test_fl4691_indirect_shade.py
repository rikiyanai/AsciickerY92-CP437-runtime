"""Offline FL-4691 dispatch coverage/ABI regression; never launches Godot."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from test_fl4691_paired_payload_contract import (
    ROOT, PRODUCT, PIPELINE, EFFECT, _expand_includes, _function_source,
    _gd_function_source,
)

ARGS = ROOT / "godot_project/render/backends/perspective/cull/perspective_probe_dispatch_args.glsl"


def test_shipping_dispatch_math_preserves_raster_args_and_survivor_coverage(tmp_path):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")
    source = ARGS.read_text()
    fields = re.search(r"buffer SurvivorArgs \{(.*?)\} args;", source, re.S).group(1)
    finalize = _function_source(source, "main").replace("void main()", "void finalize()")
    product = _expand_includes(PRODUCT.read_text())
    decoders = []
    for name in ("fl4359_texel_shade_main", "fl4359_source_effects_main"):
        body = _function_source(product, name)
        expression = re.search(r"uint index = (.*?);", body, re.S).group(1)
        assert "if (index >= fl4359_shade_survivor_args.instance_count) return;" in body
        decoders.append(f"uint {name}_index() {{ return {expression}; }}")
    cpp = tmp_path / "dispatch.cpp"
    cpp.write_text("""
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <cstddef>
#include <cstring>
#include <random>
#include <vector>
using uint = uint32_t;
using std::min; using std::max;
struct Args { FIELDS } args;
struct Uvec3 { uint x, y, z; } gl_WorkGroupID, gl_NumWorkGroups;
uint gl_LocalInvocationIndex;
FINALIZE
DECODERS
int main() {
    static_assert(offsetof(Args, groups_x) == 32);
    static_assert(sizeof(Args) == 48);
    std::mt19937 random(4691);
    std::vector<uint> counts = {0, 1, 63, 64, 65, 32767, 32768, 32769,
        18*128*128, 18*256*256, 18*384*384, 0x0fffffff};
    for (uint count = 0; count < 100000; ++count) counts.push_back(count);
    for (uint i = 0; i < 10000; ++i) counts.push_back(random() & 0x0fffffff);
    for (uint count : counts) {
        args = {6, count, 7, 11, 13, 17, 19, 23, 0xbad, 0xbad, 0xbad, 0xfeed};
        uint raster[8]; std::memcpy(raster, &args, 32);
        finalize();
        assert(std::memcmp(raster, &args, 32) == 0);
        assert(args.padding == 0xfeed);
        assert(args.groups_x > 0 && args.groups_x <= 65535);
        assert(args.groups_y > 0 && args.groups_y <= 65535);
        assert(args.groups_z == 1);
        uint capacity = args.groups_x * args.groups_y * 64;
        assert(capacity >= count && capacity - count < 32768);
    }
    // Execute both shipping shader index expressions over every dispatched
    // lane for empty, partial, multirow and full default-sized lists. Their
    // guarded prefix must visit each survivor exactly once and in order.
    for (uint count : {0u, 65u, 32769u, 18u*384u*384u}) {
        args.instance_count = count; finalize();
        gl_NumWorkGroups = {args.groups_x, args.groups_y, args.groups_z};
        uint visited = 0;
        for (uint y = 0; y < args.groups_y; ++y)
            for (uint x = 0; x < args.groups_x; ++x)
                for (uint lane = 0; lane < 64; ++lane) {
                    gl_WorkGroupID = {x, y, 0}; gl_LocalInvocationIndex = lane;
                    uint index = fl4359_texel_shade_main_index();
                    assert(index == fl4359_source_effects_main_index());
                    if (index < count) assert(index == visited++);
                }
        assert(visited == count);
    }
}
""".replace("FIELDS", fields).replace("FINALIZE", finalize)
        .replace("DECODERS", "\n".join(decoders)))
    executable = tmp_path / "dispatch"
    subprocess.run([compiler, "-std=c++17", "-O2", str(cpp), "-o", str(executable)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


def test_indirect_dispatch_is_ordered_and_has_no_full_grid_alternative():
    pipeline, effect = PIPELINE.read_text(), EFFECT.read_text()
    prepare = _gd_function_source(pipeline, "_prepare_splat_set")
    cull = prepare.index("compute_list_bind_compute_pipeline(cull, _cull_pipeline)")
    boundary = prepare.index("compute_list_end()", cull)
    front_finalize = prepare.index("compute_list_bind_uniform_set(front_args_stage, _dispatch_args_uniform_set, 0)")
    underlay_finalize = prepare.index("compute_list_bind_uniform_set(underlay_args_stage, _underlay_dispatch_args_uniform_set, 0)")
    underlay_edge = prepare.index("compute_list_bind_compute_pipeline(underlay_edge_stage, _edge_pipeline)")
    front_edge = prepare.index("compute_list_bind_compute_pipeline(front_edge_stage, _edge_pipeline)")
    assert cull < boundary < front_finalize < underlay_finalize < underlay_edge < front_edge
    assert "compute_list_dispatch_indirect(front_edge_stage, _indirect_args, SURVIVOR_DISPATCH_OFFSET)" in prepare
    assert "_underlay_indirect_args, SURVIVOR_DISPATCH_OFFSET)" in prepare
    assert "compute_list_add_barrier" not in prepare
    assert "var cull_arms := [2] if _fl4691_paired_splat_cull else [0, 1]" in prepare
    assert "_underlay_cull_uniform_set" not in pipeline
    assert "const SURVIVOR_DISPATCH_OFFSET := 32" in pipeline
    for name in ("perspective_dispatch_base_paint", "_dispatch_fl4359_source_effects_uniforms"):
        body = _gd_function_source(effect, name)
        assert "compute_list_dispatch_indirect(" in body
        assert "survivor_args, PerspectiveProbePipelineScript.SURVIVOR_DISPATCH_OFFSET" in body
        assert "(probe_size + 7) / 8, (probe_size + 7) / 8, layer_count" not in body
        assert "buffer_get_data" not in body
    effects = _gd_function_source(effect, "perspective_dispatch_display_paint")
    assert 'visible_only' not in effects
    assert 'survivor_args)' in effects
    release = _gd_function_source(pipeline, "_release_splat_draw_resources")
    for name in ("_dispatch_args_uniform_set", "_underlay_dispatch_args_uniform_set"):
        assert name in release and f"{name} = RID()" in release


@pytest.mark.parametrize("shader,define", [
    (ARGS, ""),
    (ARGS.with_name("perspective_probe_cull.glsl"), ""),
    (ARGS.with_name("perspective_probe_edge_mask.glsl"), ""),
    (ARGS.with_name("perspective_probe_visible_sources.glsl"), ""),
    (PRODUCT, ""),
    (PRODUCT, "FL4359_TEXEL_SHADE"),
    (PRODUCT, "FL4359_SOURCE_EFFECTS"),
])
def test_shader_compiles_offline(shader, define, tmp_path):
    compiler = shutil.which("glslangValidator")
    if compiler is None:
        pytest.skip("glslangValidator unavailable")
    source = _expand_includes(shader.read_text()).replace("#[compute]", "")
    path = tmp_path / "source.comp"
    path.write_text(source)
    command = [compiler, "-V", "-S", "comp", str(path), "-o", str(tmp_path / "source.spv")]
    if define:
        command.append(f"-D{define}")
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
