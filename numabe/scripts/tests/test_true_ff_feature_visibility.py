"""FL-4907 offline unit of production P3 GLSL, not GPU/visual evidence.

Depth reconstruction is a synthetic projection fixture, not the production
inverse-matrix implementation. These cases test the visibility predicate only.
"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHADER = ROOT / "godot_project/render/ascii/features/true_ff_feature_visibility.glsl"
FEATURES = ROOT / "godot_project/render/ascii/features"
LIVE_SOURCE = FEATURES / "true_ff_live_feature_source.glslinc"
TERRAIN_SOURCE = FEATURES / "true_ff_terrain_feature_source.glslinc"
STRUCTURE_ABI = ROOT / "godot_project/render/ascii/structure/TrueFFStructureABI.gd"
GBUFFER_HOST = ROOT / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd"


def _function(source, name):
    match = re.search(rf"(?m)^\w+\s+{name}\([^{{]+\{{", source)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def _glsl_terrain_object():
    match = re.search(r"const uint TFF_P0_TERRAIN_OBJECT = (0x[0-9A-Fa-f]+)u;",
                      TERRAIN_SOURCE.read_text())
    assert match, "TFF_P0_TERRAIN_OBJECT missing"
    return int(match.group(1), 16)


def test_p0_terrain_object_is_one_encoding_distinct_from_static_slot_zero():
    """FL-4907 P0_STATIC_SLOT0_TERRAIN_ID_COLLISION_20261002 (source-level)."""
    gd = re.search(r"const P0_TERRAIN_OBJECT_ID := (0x[0-9A-Fa-f]+)", STRUCTURE_ABI.read_text())
    assert gd and int(gd.group(1), 16) == _glsl_terrain_object()
    terrain = _glsl_terrain_object()
    # Not a static slot (0..0x1FFFF), mover kind 1..13, actor 128, sky, water.
    assert terrain > 0x1FFFF and terrain >> 24 not in set(range(1, 14)) | {128, 0xFD, 0xFE}
    host = GBUFFER_HOST.read_text()
    assert re.search(r"build_heightfield_descriptor\(origin, dims, 0\.001,\s*\n\s*StructureABI\.P0_TERRAIN_OBJECT_ID,", host)
    # FL-4907 GPU_SHADOW_BOUNDARY_PASS_20261002: the CPU shadow producer is
    # deleted; shadow sites take their receiver id straight from P0 ids.x.
    assert not (FEATURES / "TrueFFShadowFeatures.gd").exists()
    shadow = (FEATURES / "true_ff_shadow_boundary.glsl").read_text()
    assert "site.ids = uvec4(seed, seed," in shadow and "pair.x);" in shadow
    assert "site.identity = tff_shadow_site_identity(pair.x, pair.y, shadow.z)" in shadow
    # Every P0 ids.x comparison against a feature instance uses one accessor.
    assert "ids.x == tff_instance_p0_object(inst)" in (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    extract = (FEATURES / "true_ff_feature_extract_common.glslinc").read_text()
    assert "tff_live_semantic_object(inst, f)" in extract
    live = LIVE_SOURCE.read_text()
    assert "return tff_instance_p0_object(inst);" in live and "==8u) return 0u;" not in live
    for path in FEATURES.glob("*.glsl*"):
        assert not re.search(r"ids\.x *== *inst\.refs\.w", path.read_text()), path

PRELUDE = r'''
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
using uint = uint32_t;
using std::min; using std::max; using std::abs;
struct vec2 { float x,y; vec2(float a=0):x(a),y(a){} vec2(float a,float b):x(a),y(b){} };
struct ivec2 { int x,y; ivec2(int a=0):x(a),y(a){} ivec2(int a,int b):x(a),y(b){} };
struct vec4 { float x,y,z,w; vec2 xy() const {return vec2(x,y);} };
struct uvec2 {uint x,y;};
struct uvec4 {uint x,y,z,w;uvec2 xy()const{return {x,y};}};
struct bvec2 {bool x,y;};
bool any(bvec2 a){return a.x || a.y;}
bool all(bool value){return value;}
template<class V> bvec2 lessThan(V a,V b){return {a.x<b.x,a.y<b.y};}
template<class V> bvec2 greaterThanEqual(V a,V b){return {a.x>=b.x,a.y>=b.y};}
ivec2 min(ivec2 a,ivec2 b){return {min(a.x,b.x),min(a.y,b.y)};}
ivec2 operator+(ivec2 a,ivec2 b){return {a.x+b.x,a.y+b.y};}
ivec2 operator-(ivec2 a,int b){return {a.x-b,a.y-b};}
ivec2 operator*(ivec2 a,int b){return {a.x*b,a.y*b};}
uint floatBitsToUint(float f){uint u;std::memcpy(&u,&f,4);return u;}
float uintBitsToFloat(uint u){float f;std::memcpy(&f,&u,4);return f;}
struct TffSite {vec4 pos;uvec4 ids;uvec4 source;};
struct Camera {vec4 page;} cam={{10,10,0,0}};
bool perspective=false, reversed=false;
uint g_object=7;
float depths[5][5];
// P0 ids attachment: x object id, w family + 1 (0 = nothing drawn).
const uint P0_TERRAIN=TERRAIN_ID, P0_TERRAIN_FAMILY=1, P0_STATIC_FAMILY=3;
uvec4 p0_ids[5][5];
int prepass_ids=0;
uvec4 texelFetch(int sampler,ivec2 p,int){return sampler==0?p0_ids[p.y][p.x]:uvec4{0,0,0,0};}
int prepass_primitive=1;
bool equal(uvec2 a,uvec2 b){return a.x==b.x&&a.y==b.y;}
void ids_fill(uint object,uint family){for(auto &row:p0_ids)for(uvec4 &t:row)t={object,0,0,family};}
ivec2 tff_prepass_size(){return {5,5};}
ivec2 tff_prepass_texel(vec2 p){return {int(std::floor(p.x/2)),int(std::floor(p.y/2))};}
vec2 tff_prepass_texel_centre_px(ivec2 p){return {p.x*2.0f+1,p.y*2.0f+1};}
float tff_prepass_depth(ivec2 p){return depths[p.y][p.x];}
float tff_view_depth_of_ndc(vec2,float z){
    float forward=reversed ? 1-z : z;
    return perspective ? 1/(1-forward) : forward*100;
}
float ndc(float d){float z=perspective ? 1-1/d : d/100;return reversed ? 1-z : z;}
void flat(float d){for(auto &row:depths)for(float &z:row)z=ndc(d);}
TffSite site(float d,float x=5,float y=5,uint object=7){return {{x,y,d,0},{0,0,0,object},{0,0,0,0}};}
'''

CASES = r'''
int main(){
    for(int p=0;p<2;++p)for(int r=0;r<2;++r){
        perspective=p;reversed=r;
        ids_fill(7,P0_STATIC_FAMILY);
        flat(2);
        if(site_visible(site(4)))return 1;
        if(!site_visible(site(1.9f)))return 2;
        if(!site_visible(site(2)))return 3;
        // Identity authorizes before depth: another object never passes.
        if(site_visible(site(1.9f,5,5,8)))return 9;
        flat(2);depths[2][2]=ndc(4);
        if(!site_visible(site(4)))return 4;
        if(site_visible(site(4,3,5)))return 5;
        if(site_visible(site(4,7,5)))return 6;
        if(site_visible(site(1,-0.01f,5)))return 7;
        if(site_visible(site(1,10,5)))return 8;
        // FL-4907 P0_STATIC_SLOT0_TERRAIN_ID_COLLISION_20261002: static
        // registry slot 0 and terrain are different P0 objects.
        flat(2);
        ids_fill(P0_TERRAIN,P0_TERRAIN_FAMILY);
        if(site_visible(site(2,5,5,0)))return 10;
        if(!site_visible(site(2,5,5,P0_TERRAIN)))return 11;
        ids_fill(0,P0_STATIC_FAMILY);
        if(!site_visible(site(2,5,5,0)))return 12;
        if(site_visible(site(2,5,5,P0_TERRAIN)))return 13;
        ids_fill(0,0);
        if(site_visible(site(2,5,5,0)))return 14;
    }
    return 0;
}
'''


@pytest.mark.parametrize("restore_bad_shortcut,old_terrain_zero,expected",
                         [(False, False, 0), (True, False, 1), (False, True, 10)])
def test_production_visibility_and_old_object_shortcut_falsifier(
        tmp_path, restore_bad_shortcut, old_terrain_zero, expected):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable for offline GLSL unit")
    shader = (FEATURES / "true_ff_feature_passes.glslinc").read_text()
    live = LIVE_SOURCE.read_text()
    identity = "\n".join(_function(live, name) for name in (
        "tff_live_figure_metadata", "tff_live_visibility", "tff_live_kind",
        "tff_live_visibility_domain", "tff_live_depth_required"))
    body = shader[shader.index("bool tff_same_p0_surface("):shader.index("#ifndef TFF_WITNESS_COMMIT")]
    body = body.replace(".xy", ".xy()").replace("tff_site_visible", "site_visible")
    if restore_bad_shortcut:
        body = body.replace("bool site_visible(TffSite s) {",
                            "bool site_visible(TffSite s) { if(s.ids.w == g_object) return true;")
    # The old encoding (terrain P0 id 0) must make static slot 0 pass as terrain.
    terrain = 0 if old_terrain_zero else _glsl_terrain_object()
    prelude = PRELUDE.replace("TERRAIN_ID", hex(terrain) + "u")
    binary = tmp_path / "visibility_unit"
    result = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", str(binary)],
                            input=prelude + identity + body + CASES, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == expected, result.stderr
