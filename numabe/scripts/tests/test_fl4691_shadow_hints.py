"""Offline FL-4691 blocker-hint contract; never starts Godot or captures.

Compile the shipping ray, candidate and density functions as C++ against a
small retained-scene adapter. Compare hinted density to exhaustive current-ray
triangle selection across receiver/light/membership changes. This proves only
the bounded arithmetic contract, not GPU execution or frame rate.
"""
from pathlib import Path
import shutil
import subprocess
import pytest

from test_fl4691_paired_payload_contract import _function_source, _gd_function_source, _expand_includes

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
EFFECT = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"


@pytest.mark.parametrize("query_cache", [False, True])
def test_current_ray_retest_matches_exhaustive_selection(tmp_path, query_cache):
    source = _expand_includes(PRODUCT.read_text())
    functions = "\n".join(_function_source(source, name) for name in (
        "product_ray_triangle_t",
        "vsb_retained_static_legacy_candidate_admitted",
        "vsb_retained_consider_static_caster",
    ))
    functions = functions.replace("inout VsbRetainedStaticCasterSelection selected",
                                  "VsbRetainedStaticCasterSelection &selected")
    density = _function_source(source, "q3_probe_payload_static_shadow_density")
    harness = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <random>
#include <array>
#include <map>
#include <cstring>
#define FL4359_TEXEL_SHADE
#define precise
using uint = uint32_t;
using std::abs; using std::min;
struct vec3 {
    float x,y,z;
    vec3(float a=0,float b=0,float c=0):x(a),y(b),z(c){}
    vec3 operator+(vec3 b)const{return {x+b.x,y+b.y,z+b.z};}
    vec3 operator-(vec3 b)const{return {x-b.x,y-b.y,z-b.z};}
    vec3 operator-()const{return {-x,-y,-z};}
    vec3 operator*(float b)const{return {x*b,y*b,z*b};}
};
float dot(vec3 a,vec3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
vec3 cross(vec3 a,vec3 b){return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
float length(vec3 a){return std::sqrt(dot(a,a));}
vec3 normalize(vec3 a){return a*(1.0f/length(a));}
struct vec4 {vec3 xyz;};
struct uvec2 {uint x,y;uvec2(uint a=0):x(a),y(a){}uvec2(uint a,uint b):x(a),y(b){}};
struct uvec3 {uint x,y,z;};
uvec3 floatBitsToUint(vec3 v){uvec3 result;std::memcpy(&result,&v,12);return result;}
struct uvec4 {
    uint x=0,y=0,z=0,w=0;
    uvec4()=default;
    uvec4(uvec3 a,uint b):x(a.x),y(a.y),z(a.z),w(b){}
};
struct ViewRay {vec3 near_world,dir;float len;};
struct ProbePayloadFlags {};
struct VsbRetainedStaticCasterSelection {uvec2 blocker;bool valid=false;uint legacy_face_index=0;float depth=0;};
struct VsbSemanticStaticInstance {uvec4 refs;};
struct VsbRetainedFaceFacts {vec4 row_0,row_1,row_2,row_3;uint material_id=1,legacy_face_index=0;};
struct {uvec4 static_counts_0;} vsb_semantic_geometry_counts;
struct {VsbSemanticStaticInstance values[4];} vsb_semantic_static_instances;
struct {uvec4 view_identity;} product_vsb_frame;
struct {uvec2 blockers[1];} fl4691_static_shadow_hints;
// Wrapper integration adapter. The separate actual-GPU cache fixture tests
// bounded hashing, collisions and publication; this map isolates which ray
// words and registry invalidation the shipping lighting wrapper consumes.
struct {struct {uint x=256;} control;} product_static_shadow_cache;
std::map<std::array<uint,8>,float> query_results;
std::array<uint,8> query_key(uvec4 a,uvec4 b){return {a.x,a.y,a.z,a.w,b.x,b.y,b.z,b.w};}
uint product_static_shadow_cache_slot(uvec4,uvec4){return 0;}
bool product_static_shadow_cache_read(uint,uvec4 a,uvec4 b,float& density){
    auto found=query_results.find(query_key(a,b));
    if(found==query_results.end())return false;density=found->second;return true;
}
void product_static_shadow_cache_write(uint,uvec4 a,uvec4 b,float density){
    query_results.emplace(query_key(a,b),density);
}


constexpr uint OWNER_SKY=6,OWNER_ACTOR_SPRITE=4;
bool use_hint=true,active[4]={true,true,true,true};
int traversals=0;
VsbRetainedFaceFacts faces[4];

VsbRetainedStaticCasterSelection vsb_retained_empty_static_selection(){return {};}
bool vsb_retained_static_face_by_indices(uint o,uint p,VsbRetainedFaceFacts& f,vec4&,bool){
    if(o>=4 || p!=0)return false;f=faces[o];return true;
}
'''
    traversal = r'''
VsbRetainedStaticCasterSelection vsb_retained_select_q3_static_shadow_caster(ViewRay ray,int receiver){
    ++traversals;
    auto result=vsb_retained_empty_static_selection();
    for(uint i=0;i<4;++i)if(active[i]
        && vsb_retained_static_legacy_candidate_admitted(vsb_semantic_static_instances.values[i],0,receiver,product_vsb_frame.view_identity.w,result)
        && vsb_retained_consider_static_caster(ray,i,0,true,result)){
        result.valid=true;return result;
    }
    return result;
}
'''
    main = r'''
int main(){
    vsb_semantic_geometry_counts.static_counts_0.x=4;
    product_vsb_frame.view_identity.w=4;
    for(uint i=0;i<4;++i){
        vsb_semantic_static_instances.values[i].refs.z=i;
        vsb_semantic_static_instances.values[i].refs.w=1;
        faces[i].legacy_face_index=i;
        float z=2.0f+i*3.0f;
        faces[i].row_0.xyz={-2,-2,z};faces[i].row_1.xyz={2,-2,z};
        faces[i].row_2.xyz={2,2,z};faces[i].row_3.xyz={-2,2,z};
    }
    vec3 effective_sun_dir={0,0,1};
    assert(q3_probe_payload_static_shadow_density(effective_sun_dir,{0,0,0},{0,1,0},1,-1,0u)==1);
    traversals=0;
    assert(q3_probe_payload_static_shadow_density(effective_sun_dir,{0,0,0},{0,1,0},1,-1,0u)==1);
    assert(traversals==0); // A repeated actual blocker avoids the tree.
#ifdef FL4691_STATIC_SHADOW_QUERY_CACHE
    traversals=0;
    assert(q3_probe_payload_static_shadow_density(effective_sun_dir,{20,0,0},{0,1,0},1,-1,0u)==0);
    assert(traversals==1);
    traversals=0;
    assert(q3_probe_payload_static_shadow_density(effective_sun_dir,{20,0,0},{0,1,0},1,-1,0u)==0);
    assert(traversals==0); // An exact cached miss avoids the tree as well.
#endif
    std::mt19937 rng(4691);std::uniform_real_distribution<float> pos(-4,4),sun(-1,1);
    int reuses=0;
    for(int frame=0;frame<12000;++frame){
        // Host clears on membership/immutable-content changes, never on light.
        if(frame%31==0){active[(frame/31)%4]=!active[(frame/31)%4];fl4691_static_shadow_hints.blockers[0]={0};}
        faces[3].material_id=frame%2?250:1; // Current material rejection is retested.
        query_results.clear(); // This fixture changes immutable material bytes every frame.
        vec3 receiver={pos(rng),pos(rng),pos(rng)};
        effective_sun_dir=normalize({sun(rng),sun(rng),1});
        int self=frame%5-1;
        traversals=0;use_hint=true;
        float hinted=q3_probe_payload_static_shadow_density(effective_sun_dir,receiver,{0,1,0},1,self,use_hint?0u:0xffffffffu);
#ifdef FL4691_STATIC_SHADOW_QUERY_CACHE
        traversals=0;
        float repeated=q3_probe_payload_static_shadow_density(effective_sun_dir,receiver,{0,1,0},1,self,0u);
        assert(repeated==hinted && traversals==0);
#endif
        if(!traversals)++reuses;
        use_hint=false;
        float reference=q3_probe_payload_static_shadow_density(effective_sun_dir,receiver,{0,1,0},1,self,use_hint?0u:0xffffffffu);
        assert(hinted==reference);
    }
    assert(reuses>0);
    std::cout<<"12000 changing-ray comparisons passed; hint hits="<<reuses<<"\n";
}
'''
    cpp = tmp_path / "shadow.cpp"
    define = "#define FL4691_STATIC_SHADOW_QUERY_CACHE\n" if query_cache else ""
    cpp.write_text(define + harness + functions + traversal + density + main)
    compiler = shutil.which("clang++") or shutil.which("c++")
    assert compiler, "C++ compiler required for the offline arithmetic check"
    exe = tmp_path / "shadow"
    result = subprocess.run([compiler, "-std=c++17", "-O0", str(cpp), "-o", str(exe)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(exe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "12000 changing-ray comparisons passed" in result.stdout


def test_hint_lifetime_and_dispatch_binding():
    source = EFFECT.read_text()
    ensure = _gd_function_source(source, "_ensure_fl4691_static_shadow_hints")
    for dependency in ("probe_size, layer_count", "_vsb_static_publication_cache_content_digest",
                       "_vsb_static_publication_cache_db_instance_id",
                       "_vsb_semantic_immutable_generation_id", "_vsb_semantic_asset_revision",
                       '_vsb_semantic_bank_snapshots["static_tlas_ids"]'):
        assert dependency in ensure
    assert "rd.buffer_clear(" in ensure
    assert ensure.index("rd.buffer_clear(") < ensure.index("_fl4691_static_shadow_hints_key = key")
    assert "buffer_get_data" not in ensure
    dispatch = _gd_function_source(source, "perspective_dispatch_base_paint")
    assert '_probe_u_storage(119, _fl4691_static_shadow_hints_rd)' in dispatch
    assert '_probe_u_storage(133, _fl4691_static_shadow_queries_rd)' in dispatch
    assert "query_key := key.duplicate()" in ensure
    assert "query_key.append(_vegetation_motion_generation)" in ensure
    assert "_vegetation_motion_generation += 1" in _gd_function_source(source, "_dispatch_vegetation_clumps")
    assert "_fl4691_static_shadow_queries_key = query_key" in ensure
    assert "_release_fl4691_static_shadow_hints()" in _gd_function_source(source, "release_rendering_resources")
    main = _function_source(_expand_includes(PRODUCT.read_text()), "fl4359_texel_shade_main")
    assert "input_value.context.shadow_hint_address" in main
    assert "if (fl4359_texel_shade.dims.w == 1u)" in main
    source = _expand_includes(PRODUCT.read_text())
    assert "fl4691_shadow_source_address" not in source
    assert "fl4691_static_shadow_hit" not in source


def test_stone_admission_is_used_by_every_active_projection():
    source = (ROOT / "godot_project/scripts/world/WorldPartition.gd").read_text()
    for name in ("configure", "_chunk_registry_slots", "_instance_rows_for_chunks", "active_asset_records"):
        assert "_is_removed_stone(" in _gd_function_source(source, name)
    assert "_is_removed_stone(" not in _gd_function_source(source, "all_asset_records")
    assert "_admitted_chunk_face_counts" in _gd_function_source(source, "_desired_selection")
    assert "_admitted_chunk_face_counts" in _gd_function_source(source, "_new_residency_record")
