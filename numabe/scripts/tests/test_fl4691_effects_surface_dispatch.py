"""Execute production display scheduling and publication without a renderer launch."""
import shutil
import subprocess

import pytest
from test_fl4691_paired_payload_contract import PRODUCT, PIPELINE, _function_source, _expand_includes


def _run_cpp(tmp_path, source):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")
    path = tmp_path / "display.cpp"
    path.write_text(source)
    result = subprocess.run([compiler, "-std=c++17", "-O2", str(path), "-o", str(tmp_path / "display")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    subprocess.run([str(tmp_path / "display")], check=True)


def test_display_publisher_writes_only_demanded_surfaces_and_reuses_exact_inputs(tmp_path):
    shader = _expand_includes(PRODUCT.read_text())
    functions = "\n".join(_function_source(shader, name) for name in
        ("fl4359_source_display_equal", "source_display_publish"))
    _run_cpp(tmp_path, r'''
#include <array>
#include <cassert>
#include <cstdint>
#include <cstring>
using uint = uint32_t;
struct Value { uint x,y,z,w; };
using uvec4 = Value; using ivec3 = int;
bool equal(Value a, Value b) { return std::memcmp(&a,&b,sizeof(a))==0; }
bool all(bool v) { return v; }
Value floatBitsToUint(Value a) { return a; }
struct SourceDisplayInput { Value paint,world; uint capture_generation,source_generation; bool paired=false,underlay=false; };
SourceDisplayInput inputs[2]; Value outputs[2]; int reads[2],writes[2],calls;
constexpr int source_effects_output=0,source_effects_under_output=1;
SourceDisplayInput fl4359_source_display_read(int address, bool under) {
    assert(address==7); ++reads[under]; return inputs[under];
}
SourceDisplayInput fl4359_source_display_read_paint(int address, bool under) {
    return fl4359_source_display_read(address,under);
}
SourceDisplayInput fl4359_source_display_read_geometry(SourceDisplayInput a) { return a; }
Value fl4359_source_display_evaluate(SourceDisplayInput a) {
    ++calls; return {a.paint.x^a.world.x,a.paint.y^a.world.y,
        a.paint.z^a.world.z,a.paint.w};
}
void imageStore(int output, int address, Value a) {
    assert(address==7); ++writes[output]; outputs[output]=a;
}
FUNCTIONS
int main() {
    for (uint demand=0; demand<4; ++demand)
    for (uint changed=0; changed<=10; ++changed) {
        uint words[10]; for(uint i=0;i<10;++i) words[i]=i*43;
        std::memcpy(&inputs[0],words,sizeof(words));
        if(changed<10) words[changed]^=0x80000000;
        std::memcpy(&inputs[1],words,sizeof(words));
        auto saved0=inputs[0], saved1=inputs[1];
        calls=reads[0]=reads[1]=writes[0]=writes[1]=0;
        outputs[0]=outputs[1]={0xdead,0xbeef,0xfeed,0xcafe};
        source_display_publish(7,demand&1,demand&2);
        assert(reads[0]==bool(demand&1) && reads[1]==bool(demand&2));
        assert(writes[0]==bool(demand&1) && writes[1]==bool(demand&2));
        assert(calls==(demand==0?0:(demand==3 && changed<10?2:1)));
        for(uint arm=0;arm<2;++arm) {
            if(demand&(1u<<arm)) assert(equal(outputs[arm],fl4359_source_display_evaluate(inputs[arm])));
            else assert(outputs[arm].x==0xdead);
        }
        assert(std::memcmp(&saved0,&inputs[0],sizeof(saved0))==0);
        assert(std::memcmp(&saved1,&inputs[1],sizeof(saved1))==0);
    }
}
'''.replace("FUNCTIONS", functions))
    host = PIPELINE.read_text()
    assert 'survivor_role' not in _function_source_gd(host)


def _function_source_gd(host):
    return host.split("func _perspective_dispatch_display_paint(",1)[1].split("\nfunc ",1)[0]


@pytest.mark.parametrize("mutation", [None, "visibility_only", "late_retained_paint"])
def test_visibility_only_schedules_display_and_eye_not_retained_base_paint(tmp_path, mutation):
    source = (PRODUCT.parent / "perspective_probe_visible_sources.glsl").read_text()
    contract = (PRODUCT.parent / "render_source_contract.glslinc").read_text()
    functions = "\n".join(_function_source(source,name) for name in
        ("face_texel_count", "source_capacity", "valid_word_mask", "required_generation",
         "missing_paint_bit_count", "append_missing_paint", "base_paint_demand", "phase_compact_visible_words"))
    if mutation == "visibility_only":
        old = "eye ? (visible_bits & selected) != 0u : fresh"
        assert old in functions
        functions = functions.replace(old, "(visible_bits & selected) != 0u")
    elif mutation == "late_retained_paint":
        old = "eye ? (visible_bits & selected) != 0u : fresh"
        assert old in functions
        functions = functions.replace(old, "eye ? (visible_bits & selected) != 0u : true")
    harness = r'''
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdint>
#include <random>
#include <vector>
using uint = uint32_t;
struct uvec4 {
    uint x,y,z,w;
    uint &operator[](uint i) { return (&x)[i]; }
    const uint &operator[](uint i) const { return (&x)[i]; }
};
CONTRACT
uint atomicAdd(uint &target,uint value) { uint old=target;target+=value;return old; }
uint bitCount(uint v) { return __builtin_popcount(v); }
struct Push {
    uint phase,width,height,capacity,face_texels,eye_band,fresh_mask,word_count;
    uvec4 layer_generation[5];
} push_data;
struct Bits { uint words[128]; } front_visibility,underlay_visibility;
struct Generation { uint values[4096]; } front_paint_generation,underlay_paint_generation;
struct List { uint texel_ids[4096]; } front_shade_output,underlay_shade_output;
struct Requests { uint requests[4096]; } display_requests;
struct Arg { struct { uint instance_count; } args; } front_shade_output_args,
    underlay_shade_output_args,display_request_args;
FUNCTIONS
int main() {
    std::mt19937 random(4691);
    for(uint iteration=0;iteration<1000;++iteration) {
        push_data={};
        // Exercise words straddling layers as well as full 64-texel faces.
        uint face_count=1+random()%192,capacity=18*face_count-random()%face_count;
        push_data.capacity=capacity;push_data.face_texels=face_count;
        push_data.eye_band=random()%3;push_data.fresh_mask=random()&0x3ffffu;
        push_data.word_count=(capacity+31)/32;
        for(uint layer=0;layer<20;++layer)
            push_data.layer_generation[layer/4][layer%4]=1000+layer;
        front_shade_output_args={};underlay_shade_output_args={};display_request_args={};
        std::vector<uint> expected,front_shade,under_shade;
        for(uint word=0;word<push_data.word_count;++word) {
            uint f=random(),u=random();
            if(iteration%3==0)u=f; if(iteration%3==1)u=~f;
            front_visibility.words[word]=f;underlay_visibility.words[word]=u;
            for(uint bit=0;bit<32;++bit) {
                uint id=word*32+bit; if(id>=capacity)break;
                bool front=f&(1u<<bit),under=u&(1u<<bit);
                if(front||under) expected.push_back(id|(front?1u<<30:0u)|(under?1u<<31:0u));
                uint layer=id/face_count;
                uint generation=push_data.layer_generation[layer/4][layer%4];
                front_paint_generation.values[id]=(random()%3==0)?generation:generation-1;
                underlay_paint_generation.values[id]=(random()%3==0)?generation:generation-1;
                bool eye=layer/6==push_data.eye_band;
                bool fresh=push_data.fresh_mask&(1u<<layer);
                if((eye?front:fresh)&&front_paint_generation.values[id]!=generation)front_shade.push_back(id);
                if((eye?under:fresh)&&underlay_paint_generation.values[id]!=generation)under_shade.push_back(id);
            }
            phase_compact_visible_words(word);
        }
        assert(display_request_args.args.instance_count==expected.size());
        assert(std::equal(expected.begin(),expected.end(),display_requests.requests));
        assert(front_shade_output_args.args.instance_count==front_shade.size());
        assert(underlay_shade_output_args.args.instance_count==under_shade.size());
        assert(std::equal(front_shade.begin(),front_shade.end(),front_shade_output.texel_ids));
        assert(std::equal(under_shade.begin(),under_shade.end(),underlay_shade_output.texel_ids));
    }
}
'''.replace("CONTRACT",contract).replace("FUNCTIONS",functions)
    if mutation is None:
        _run_cpp(tmp_path, harness)
    else:
        with pytest.raises(subprocess.CalledProcessError):
            _run_cpp(tmp_path, harness)


@pytest.mark.parametrize("mutation", [None, "source_revision", "zero_shade", "raw_front"])
def test_display_requires_the_generation_committed_with_each_surface(tmp_path, mutation):
    shader = _expand_includes(PRODUCT.read_text())
    # Execute the production stores, admission, geometry reader and publisher.
    # Only texture/buffer access and effects math are supplied by this harness.
    functions = "\n".join(_function_source(shader, name) for name in
        ("fl4359_texel_store", "fl4359_texel_reuse_paired",
         "fl4359_source_display_read_paint", "fl4359_source_display_read_geometry",
         "fl4359_source_display_read", "fl4359_source_display_equal", "source_display_publish"))
    if mutation == "source_revision":
        old = "source.source_generation != captured_revision"
        assert old in functions
        functions = functions.replace(old, "false")
    elif mutation == "zero_shade":
        old = "expected_generation != 0u"
        assert old in functions
        functions = functions.replace(old, "true")
    elif mutation == "raw_front":
        old = "front.paired && fl4359_source_display_equal(front, under)"
        assert old in functions
        functions = functions.replace(old, "all(equal(front.paint, under.paint))")
    harness = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <initializer_list>
using uint = uint32_t;
struct Value {
    uint x,y,z,w;
    Value(uint a=0):x(a),y(a),z(a),w(a){}
    Value(uint a,uint b,uint c,uint d):x(a),y(b),z(c),w(d){}
    uint &operator[](uint i){return (&x)[i];}
};
using uvec4=Value; using vec4=Value;
bool equal(Value a,Value b){return std::memcmp(&a,&b,sizeof(a))==0;}
bool all(bool v){return v;}
Value floatBitsToUint(Value v){return v;}
struct ivec3 { int x,y,z; ivec3(int a,int b,int c):x(a),y(b),z(c){} };
struct Frame { Value dims; Value layer_generation[5]; } source_effects_frame,fl4359_texel_shade;
struct Bank { uint values[72]; } source_display_front_generation,source_display_under_generation;
Bank source_display_front_source_generation,source_display_under_source_generation;
Bank *write_generation=&source_display_front_generation;
#define fl4359_shaded_generation (*write_generation)
#define fl4359_source_generation (*(write_generation==&source_display_front_generation ? &source_display_front_source_generation : &source_display_under_source_generation))
#define fl4359_paired_source_generation source_display_under_source_generation
Value tables[14][72];
uint reads[14],calls=0;
int source_effects_base=0,source_effects_under_base=1;
int source_effects_world=2,source_effects_under_world=3;
int source_effects_anchor=4,source_effects_under_anchor=5;
int source_effects_family_facts=6,probe_underlay_colour=7,source_effects_raw_front_identity=8;
int source_effects_revisions=10,probe_underlay_source_revisions=11;
int source_effects_output=12,source_effects_under_output=13;
int fl4359_texel_payload=0,fl4359_texel_paired_payload=1;
int fl4359_texel_identity=8,fl4359_texel_revisions=10;
constexpr uint OWNER_SKY=6,OWNER_STATIC_MESH=2,STATIC_WORLD_MEADOW_BLADE_MAT=246;
constexpr uint FL4359_TEXEL_PAYLOAD_VALID=1u<<20,FL4359_PAYLOAD_OWNER_SKY=6;
constexpr uint FL4359_SOURCE_GENERATION_SKY=0x80000000u,FL4359_SOURCE_GENERATION_VALUE_MASK=0x7fffffffu;
uint address_index(ivec3 a){return (a.z*2+a.y)*2+a.x;}
Value texelFetch(int table,ivec3 a,int){++reads[table];return tables[table][address_index(a)];}
Value imageLoad(int table,ivec3 a){return texelFetch(table,a,0);}
void imageStore(int table,ivec3 a,Value v){tables[table][address_index(a)]=v;}
struct ProbeCaptureSource { bool foreground; };
ProbeCaptureSource probe_capture_source(int table,ivec3 a){return {texelFetch(table,a,0).x!=0};}
Value probe_capture_fetch(bool front,int table,int paired,ivec3 a){return texelFetch(front?table:paired,a,0);}
void fl4691_reflection_lineage_alias_paired(ivec3){}
struct SourceDisplayInput {
    ivec3 address; bool underlay,paired; uint capture_generation,source_generation;
    Value paint,world;
    SourceDisplayInput():address(0,0,0){}
};
Value fl4359_source_display_evaluate(SourceDisplayInput s){
    ++calls;
    if(!(s.paint.x&(1u<<20)))return s.paint;
    return Value(s.paint.x,s.paint.y^s.world.x,s.paint.z^s.world.y,s.paint.w^s.world.z);
}
void reset_reads(){for(auto &v:reads)v=0;calls=0;}
FUNCTIONS
int main(){
    source_effects_frame.dims=fl4359_texel_shade.dims=Value(2,18,0,0);
    fl4359_texel_shade.dims.z=31;
    for(uint layer=0;layer<18;++layer){
        source_effects_frame.layer_generation[layer/4][layer%4]=layer+7;
        fl4359_texel_shade.layer_generation[layer/4][layer%4]=layer+7;
        for(int y=0;y<2;++y)for(int x=0;x<2;++x){
            ivec3 a(x,y,layer);uint i=address_index(a),generation=layer+7;
            Value paint((1u<<20)|(OWNER_SKY<<12)|45u,i+1,37,19);
            tables[0][i]=tables[1][i]=paint;
            source_display_front_generation.values[i]=generation-1;
            source_display_under_generation.values[i]=generation-1;
            assert(fl4359_source_display_read(a,false).paint.x==0);
            assert(fl4359_source_display_read(a,true).paint.x==0);
            fl4359_texel_payload=0;write_generation=&source_display_front_generation;
            fl4359_texel_store(a,paint);
            auto front=fl4359_source_display_read(a,false);
            assert(front.paint.x==paint.x && front.paint.y==paint.y);
            assert(front.capture_generation==generation && !front.underlay);
            assert(front.source_generation==(0x80000000u|31u));
            assert(front.address.z==int(layer));
            assert(fl4359_source_display_read(a,true).paint.x==0);
            fl4359_texel_payload=1;write_generation=&source_display_under_generation;
            fl4359_texel_store(a,paint);
            assert(fl4359_source_display_read(a,true).paint.x==paint.x);
            fl4359_texel_payload=0;write_generation=&source_display_front_generation;
            fl4359_texel_reuse_paired(a);
            assert(fl4359_source_display_read(a,false).paint.y==paint.y);
            ++source_effects_frame.layer_generation[layer/4][layer%4];
            assert(fl4359_source_display_read(a,false).paint.x==0);
            assert(fl4359_source_display_read(a,true).paint.x==0);
            --source_effects_frame.layer_generation[layer/4][layer%4];
            fl4359_texel_store(a,Value(0));
            assert(source_display_front_generation.values[i]==generation);
            assert(fl4359_source_display_read(a,false).paint.x==0);
            // Geometry tokens are exact revisions, not shade/layer epochs.
            // An absent raw front must read underlay even when stale front
            // attachments contain plausible world and revision bytes.
            paint=Value((1u<<20)|(OWNER_STATIC_MESH<<12)|45u,19,37,246);
            tables[0][i]=tables[1][i]=paint;
            tables[8][i]=Value(0);
            tables[10][i]=Value(0,0,0,22);
            tables[11][i]=Value(0,0,0,11);
            tables[2][i]=Value(500,501,502,1);
            tables[3][i]=Value(100,101,102,1);
            tables[4][i]=Value(600,601,602,1);
            tables[5][i]=Value(200,201,202,1);
            tables[6][i]=Value(700,701,702,1);
            tables[7][i]=Value(300,301,302,1);
            source_display_front_source_generation.values[i]=11;
            source_display_under_source_generation.values[i]=11;
            auto expected_front=fl4359_source_display_evaluate(fl4359_source_display_read(a,false));
            auto expected_under=fl4359_source_display_evaluate(fl4359_source_display_read(a,true));
            reset_reads();
            source_display_publish(a,true,true);
            assert(equal(tables[12][i],expected_front)&&equal(tables[13][i],expected_under));
            assert(reads[2]==0&&reads[3]==1&&reads[4]==0&&reads[5]==0&&reads[6]==0&&reads[7]==0);
            assert(calls==1);
            for(uint bad: {0u,10u,12u,0x8000000bu,0xffffffffu}){
                source_display_front_source_generation.values[i]=bad;
                reset_reads();source_display_publish(a,true,true);
                assert(tables[12][i].x==0&&equal(tables[13][i],expected_under));
                assert(reads[3]==1&&reads[5]==0&&reads[7]==0);
            }
            source_display_front_source_generation.values[i]=11;
            source_display_under_source_generation.values[i]=0;
            source_display_publish(a,true,true);
            assert(equal(tables[12][i],expected_front)&&tables[13][i].x==0);
            source_display_under_source_generation.values[i]=11;
            // A raw foreground with equal paint still owns different geometry.
            tables[8][i]=Value(1);
            source_display_front_source_generation.values[i]=22;
            expected_front=fl4359_source_display_evaluate(fl4359_source_display_read(a,false));
            reset_reads();source_display_publish(a,true,true);
            assert(equal(tables[12][i],expected_front)&&equal(tables[13][i],expected_under));
            assert(!equal(expected_front,expected_under)&&calls==2);
            assert(reads[2]==1&&reads[3]==1&&reads[4]==0&&reads[5]==0&&reads[6]==0&&reads[7]==0);
            for(uint bad: {0u,0x80000016u}){
                tables[10][i].w=bad;source_display_front_source_generation.values[i]=bad;
                reset_reads();assert(fl4359_source_display_read(a,false).paint.x==0);
                assert(reads[2]==0&&reads[4]==0&&reads[6]==0);
            }
            // Zero shade generations must not authenticate each other.
            tables[10][i].w=22;
            source_display_front_source_generation.values[i]=22;
            source_effects_frame.layer_generation[layer/4][layer%4]=0;
            source_display_front_generation.values[i]=0;
            assert(fl4359_source_display_read(a,false).paint.x==0);
            source_effects_frame.layer_generation[layer/4][layer%4]=generation;
            source_display_front_generation.values[i]=generation;
            // A sky token must be typed and nonzero, but retains its original
            // value when this dispatch's mutable paint epoch changes.
            tables[0][i]=Value((1u<<20)|(OWNER_SKY<<12)|45u,1,2,3);
            for(uint bad: {0u,31u,0x80000000u}){
                source_display_front_source_generation.values[i]=bad;
                assert(fl4359_source_display_read(a,false).paint.x==0);
            }
            source_display_front_source_generation.values[i]=0x8000001fu;
            fl4359_texel_shade.dims.z=32;
            assert(fl4359_source_display_read(a,false).source_generation==0x8000001fu);
            fl4359_texel_shade.dims.z=31;
        }
    }
}
'''.replace("FUNCTIONS", functions)
    if mutation is None:
        _run_cpp(tmp_path, harness)
    else:
        # Compile succeeds; the executable contract must reject the weakened
        # production gate rather than silently validating the mutation.
        with pytest.raises(subprocess.CalledProcessError):
            _run_cpp(tmp_path, harness)


def test_display_host_binds_both_exact_source_banks_and_front_capture_revision():
    host = PIPELINE.read_text().split("func _dispatch_fl4359_source_effects_uniforms(", 1)[1].split("\nfunc ", 1)[0]
    assert 'source_shade.get("source_generation_rid", RID())' in host
    assert "not generation_rid.is_valid() or not source_generation_rid.is_valid()" in host
    assert "_paint_storage(35 if underlay else 34, source_generation_rid)" in host
    assert 'textures[96] = capture.get("source_revisions", RID())' in host


def test_display_has_no_member_or_flower_ink_writer_or_raster_inputs():
    # Absence guard only. The GPU test executes the payload-preserving interface.
    shader = _expand_includes(PRODUCT.read_text())
    effect = _function_source(shader, "fl4359_source_effects_apply")
    assert "product_meadow_member(" not in effect
    assert "foliage_flower_species_ink(" not in effect
    assert "source_effects_frame.raster" not in effect
    assert "product_world_to_pixel" not in effect
    member = _function_source(shader, "product_meadow_member")
    assert "observer" not in member
    assert "product_world_to_pixel" not in member
    host = PIPELINE.read_text()
    dispatch = _function_source_gd(host)
    assert 'source_effects_frame.raster' not in shader
    assert 'frame_bytes.resize(16 + 5 * 16)' in dispatch
    allocator = host.split("func _allocate_fl4359_source_effects_outputs(", 1)[1].split("\nfunc ", 1)[0]
    assert 'rd.uniform_buffer_create(16 + 5 * 16)' in allocator
