"""Execute selected production paint math on CPU. Not rendered acceptance."""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHADER = ROOT / "godot_project/render/product/cell_owner_product.glsl"


from scripts.tests.test_fl4359_rain_snow_unowned_bg import (
    _expand_includes as _expand_product_includes,
)


def _product_source() -> str:
    # Paint math moved to product_*.glslinc includes in the Sep-12 handoff
    # extraction. CPU oracles read the expanded translation unit.
    return _expand_product_includes(_expand_includes(SHADER.read_text()))


def extract(name):
    source = _product_source()
    match = re.search(r"(?:void|float|bool|int) " + name + r"\([^;{}]*\)\s*\{", source)
    assert match, name
    depth = 0
    for end in range(source.index("{", match.start()), len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[match.start():end + 1]
    raise AssertionError(name)


SHIM = r'''
#include <cmath>
#include <algorithm>
#include <cassert>
#include <set>
using uint = unsigned int;
float min(float a,float b) {return std::min(a,b);}
float max(float a,float b) {return std::max(a,b);}
float clamp(float a,float lo,float hi) {return min(hi,max(lo,a));}
float mix(float a,float b,float t) {return a+(b-a)*t;}
struct ivec2 {int x,y; ivec2(int a,int b):x(a),y(b) {}};
bool operator==(ivec2 a,ivec2 b) {return a.x==b.x&&a.y==b.y;}
bool operator!=(ivec2 a,ivec2 b) {return !(a==b);}
struct vec2 {float x,y; vec2(float a=0,float b=0):x(a),y(b) {}; vec2(ivec2 v):x(float(v.x)),y(float(v.y)) {}};
float length(vec2 v) {return std::sqrt(v.x*v.x+v.y*v.y);}
vec2 normalize(vec2 v) {float l=length(v); return vec2(v.x/l,v.y/l);}
struct vec3 {
    float x,y,z;
    vec3(float a=0):x(a),y(a),z(a) {}
    vec3(float a,float b,float c):x(a),y(b),z(c) {}
    vec3 operator*(float s) const {return vec3(x*s,y*s,z*s);}
    vec3 operator*(vec3 v) const {return vec3(x*v.x,y*v.y,z*v.z);}
    void operator*=(float s) {x*=s;y*=s;z*=s;}
};
vec3 clamp(vec3 a,vec3 lo,vec3 hi) {
    return vec3(clamp(a.x,lo.x,hi.x),clamp(a.y,lo.y,hi.y),clamp(a.z,lo.z,hi.z));
}
vec3 mix(vec3 a,vec3 b,float t) {return vec3(mix(a.x,b.x,t),mix(a.y,b.y,t),mix(a.z,b.z,t));}
float luma(vec3 c) {return c.x*.2126f+c.y*.7152f+c.z*.0722f;}
float rec709_luminance(vec3 c) {return luma(c);}
const uint STATIC_WORLD_TREE_TRUNK_MAT=240, STATIC_WORLD_TREE_CANOPY_MAT_FIRST=241,
    STATIC_WORLD_TREE_CANOPY_MAT_LAST=243, STATIC_WORLD_TREE_ROOT_MOSS_MAT=244,
    STATIC_WORLD_TREE_ROOT_FLOWER_MAT=245;
const int FOLIAGE_ROLE_LEAF_LEFT=3,FOLIAGE_ROLE_LEAF_RIGHT=4,FOLIAGE_ROLE_HALF_STEM=2;
struct FoliageBlade {ivec2 anchor_cell; int grammar_role;};
'''


def run_math(tmp_path, names, exercise):
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        pytest.skip("CPU shader-math test requires C++ compiler")
    functions = "\n".join(extract(name) for name in names)
    functions = re.sub(r"out (vec2|vec3|uint|float) ", r"\1 &", functions)
    functions = re.sub(r"uint\[\d+\]\(([^;]+)\)", r"{\1}", functions)
    functions = re.sub(r"vec3\[\d+\]\(([^;]+)\)", r"{\1}", functions)
    cpp = tmp_path / "math.cpp"
    cpp.write_text(SHIM + functions + exercise)
    binary = tmp_path / "math"
    result = subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(binary)],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    subprocess.run([str(binary)], check=True)


def test_canopy_roles_have_distinct_values_and_world_fixed_glyphs(tmp_path):
    run_math(tmp_path, ("stable_cell_noise", "product_foliage_floor_div",
                       "authored_stair_step_rising",
                       "authored_stair_step_falling",
                       "authored_flower_symbol",
                       "product_retained_tree_paint_pair"), r'''
int main() {
    std::set<uint> outer,inner,under;
    for(int x=-12;x<12;++x) for(int y=-12;y<12;++y) {
        uint previous[3]={0,0,0}; float previous_value[3]={-1,-1,-1};
        for(int band=0;band<16;++band) {
            vec3 ink[3],paper[3]; uint glyph[3];
            for(int role=0;role<3;++role) {
                product_retained_tree_paint_pair(241+role,band,vec3(0,1,0),ivec2(x,y),vec3(-1),
                    glyph[role],ink[role],paper[role]);
                if(band) assert(glyph[role]==previous[role]);
                assert(luma(ink[role])>previous_value[role]);
                assert(luma(ink[role])>luma(paper[role]));
                previous[role]=glyph[role]; previous_value[role]=luma(ink[role]);
            }
            assert(luma(ink[0])>luma(ink[1]));
            assert(luma(ink[0])>luma(ink[2]));
            assert(luma(paper[0])>luma(paper[1]));
            assert(paper[2].z>paper[2].x*1.5f);
            outer.insert(glyph[0]);inner.insert(glyph[1]);under.insert(glyph[2]);
        }
    }
    assert(outer.size()==12 && inner.size()==6 && under.size()==6);
    for(uint glyph:inner) assert(!outer.count(glyph) && !under.count(glyph));
}
''')


def test_grass_roles_share_a_root_fan_not_independent_card_rotations(tmp_path):
    run_math(tmp_path, ("stable_cell_noise", "foliage_billboard_basis"), r'''
int main() {
    for(int x=-10;x<10;++x) {
        vec2 right[3],up[3];
        for(int i=0;i<3;++i) {
            FoliageBlade b{ivec2(x*4,4),i==0?0:(i==1?3:4)};
            foliage_billboard_basis(b,right[i],up[i]);
            assert(std::abs(length(right[i])-1)<1e-5);
            assert(std::abs(right[i].x*up[i].x+right[i].y*up[i].y)<1e-5);
        }
        for(int i=1;i<3;++i) {
            float angle=std::atan2(right[0].x*right[i].y-right[0].y*right[i].x,
                                   right[0].x*right[i].x+right[0].y*right[i].y);
            assert(std::abs(angle-(i==1?-.65f:.65f))<1e-5);
        }
    }
}
''')


def test_rain_moves_continuously_and_depth_clips_horizontal_and_vertical_streaks():
    # Procedural screen-ray rain (layer offsets, segment hits) is retired:
    # rain is retained weather geometry with source-id fall-speed selection
    # (see the compiled call-graph test pinning its absence from capture and
    # display). This tombstone keeps the retirement explicit in the math
    # oracle file that used to execute it.
    source = _product_source()
    for retired in ("cell_owner_rain_layer_offset", "cell_owner_rain_segment_hit",
                    "cell_owner_rain_ripple", "cell_owner_rain_air_drop"):
        assert retired not in source, retired


def baker():
    spec = importlib.util.spec_from_file_location("stone_bake", ROOT / "scripts/bake_akm_to_static_world_faces.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stone_adjacency_distinguishes_seams_folds_and_boundaries():
    compile_roles = baker().stone_edge_roles
    def roles(points, faces):
        return compile_roles([{"pos": p} for p in points], faces)
    # Oppositely traversed shared edge, coplanar triangles, including UV aliases.
    points = [(0,0,0),(1,0,0),(0,1,0),(0,-1,0),(0,0,0),(1,0,0)]
    assert all((r & 3)==0 and r>>2==15 for r in roles(points, [(0,1,2),(5,4,3)]))
    for z, expected in [(-1,1),(1,2)]:
        assert all(r&3==expected for r in roles([(0,0,0),(1,0,0),(0,1,0),(0,0,z)],
                                              [(0,1,2),(1,0,3)]))
    # More than two incident faces cannot author a unique geometric crease.
    assert all(r&3==0 for r in roles(points, [(0,1,2),(1,0,3),(0,1,3)]))


def test_statue_topology_changes_no_authored_position_or_material(tmp_path):
    module = baker()
    output = tmp_path / "plain.json"
    module.bake(ROOT / "assets/meshes/pieta.akm", output, 30.0, .82, "statue",
                moss_threshold=.15, up_axis="+z")
    plain = json.loads(output.read_text())
    tagged = json.loads((ROOT / "godot_project/assets/world_mesh/pieta_statue_mossy_faces.json").read_text())
    assert len(plain["rows"]) == len(tagged["rows"]) == 10922*16
    assert all(a==b for i,(a,b) in enumerate(zip(plain["rows"],tagged["rows"])) if i%16 not in (11,15))
    assert all(v==4359 for v in tagged["rows"][15::16])
    assert any(v for v in tagged["rows"][11::16])
    assert all(0<=v<64 for v in tagged["rows"][11::16])


def test_foliage_query_bound_keeps_every_reachable_card(tmp_path):
    """Exercise the production dimensions and broad phase against card support."""
    source = _product_source().split("FoliageBlade foliage_blade_from_anchor(", 1)[1]
    start = source.index("float height_band =")
    dimensions = source[start:source.index("\tb.variant        =", start)]
    run_math(tmp_path, ("foliage_clump_can_reach_cell",), r'''
const int FOLIAGE_ROLE_ROOT=0,FOLIAGE_ROLE_FILL=1;
int main() {
    struct Blade {float height_cells,width_cells;} b;
    struct Habitat {float b;} habitat;
    int supported=0, rejected=0;
    for(int slot_role=0;slot_role<6;++slot_role)
    for(float clump_height: {0.0f,0.5f,1.0f})
    for(float cover: {0.0f,0.5f,1.0f})
    for(float seed3: {0.0f,1.0f})
    for(float seed5: {0.0f,1.0f}) {
        habitat.b=cover;
''' + dimensions + r'''
        for(int angle=0;angle<360;angle+=3) {
            float radians=angle*3.14159265358979323846f/180;
            float rx=std::cos(radians),rz=std::sin(radians);
            for(int x=-7;x<=7;++x) for(int z=-7;z<=7;++z) {
                bool admitted=foliage_clump_can_reach_cell(ivec2(x-400,z+170),ivec2(-400,170));
                if(!admitted) ++rejected;
                for(float jx: {-0.15f,0.15f}) for(float jz: {-0.15f,0.15f}) {
                    float dx=x-jx,dz=z-jz;
                    float v=(-dx*rz+dz*rx)/max(1.15f,b.height_cells*.48f);
                    if(v<0 || v>1) continue;
                    float squash=clamp(1-v*.46f,.42f,1.0f);
                    float u=(dx*rx+dz*rz)/max(.28f,b.width_cells*squash)+.5f;
                    if(u<-.12f || u>1.12f) continue;
                    ++supported;
                    assert(admitted);
                }
            }
        }
    }
    assert(supported>1000 && rejected>1000);
}
''')
