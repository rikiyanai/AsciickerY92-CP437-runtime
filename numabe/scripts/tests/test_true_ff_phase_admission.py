"""FL-4907: offline constraints on phase-independent early admission.

These are investigation tests, not a new production admission policy. They
exercise the production float32 density functions through the existing C++
fixture. Retaining a phase chart is insufficient if P1 budgets a different
coordinate metric. In particular, the level-29 saturation invalidates an
otherwise useful projected-count bound; do not hide that condition.
"""
import math
import sys
import re
import shutil
import subprocess
import random
from pathlib import Path

import numpy as np
import pytest

from scripts.tests.test_true_ff_density_windows import density_unit, evaluate  # noqa: F401
from scripts.tests.test_true_ff_density_windows import function_body

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import true_ff_feature_passes_ref as ref
# Every focused FF run also proves the frame drivers load and all shader
# stages compile (FL-4907; see test_true_ff_feature_seam.py).
from scripts.tests.test_true_ff_feature_seam import (  # noqa: F401,E402
    test_all_shader_stages_compile_for_metal,
    test_ff_frame_drivers_load_without_parse_or_compile_errors,
)


def _production_function(source, name):
    match = re.search(rf"(?m)^\w+\s+{name}\([^{{]+\{{", source)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


def test_production_hierarchy_cut_preserves_whole_leaf_and_epoch_contract():
    source = (Path(__file__).resolve().parents[2] /
              "godot_project/render/ascii/features/true_ff_feature_traverse.glslinc").read_text()
    bottom_up = _production_function(source, "bottom_up")
    root_budget = _production_function(source, "root_budget")
    top_down = _production_function(source, "top_down")
    seal = _production_function(source, "seal")
    assert "state.demand.w != cam.counts.y || state.demand.x == 0u" in bottom_up
    assert "charge = tff_charge_add(charge, d.x)" in bottom_up
    assert "if(n>budget)" in root_budget and "if(minimum_total>budget)" in root_budget
    assert "opened = state.assignment.w == 0u && state.assignment.z == 2u" in top_down
    assert "state.demand.x <= state.assignment.x" in top_down
    assert "tff_append(TFF_SLOT_LEAVES" in top_down
    assert "required > cam.caps1.y" in seal
    assert "tff_set_count(TFF_SLOT_ADMITTED, 0u)" in seal
    assert "tff_set_count(TFF_SLOT_ADMITTED, run)" in seal


def _legacy_production_hierarchy_prefix_whole_leaf_and_representative_budget(tmp_path):
    """Execute summary reduction, root prefix, per-level cut and exact seal.

    Real production bodies, mocked summaries only. No GPU ordering, fine
    geometry, pixel, performance or temporal-correspondence claim.
    """
    source = (Path(__file__).resolve().parents[2] /
              "godot_project/render/ascii/features/true_ff_feature_traverse.glslinc").read_text()
    production = "\n".join(_production_function(source, name) for name in
                           ("tff_charge_add", "tff_group_sync", "tff_lane_range",
                            "tff_group_exclusive", "tff_frontier_base", "tff_split_scale",
                            "bottom_up", "tff_root_cell", "tff_root_live", "tff_interval_end",
                            "prefix", "top_down", "seal"))
    # GLSL `out T name` parameters are C++ references.
    production = re.sub(r"\bout (\w+) (\w+)", r"\1& \2", production)
    program = r'''
#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <iterator>
#include <mutex>
#include <thread>
#include <vector>
using uint=std::uint32_t; using std::min; using std::floor;
// One P1 workgroup: TFF_GROUP real threads meeting at a reusable barrier, so
// the group-wide steps run with the same lane interleaving freedom as the GPU.
const uint TFF_GROUP=64;
uint tff_scan_lane[64],tff_scan_total;
std::mutex tff_atomic_mutex;
struct GroupBarrier{std::mutex m;std::condition_variable cv;uint count=0,generation=0;
    void wait(){std::unique_lock<std::mutex> lock(m);uint seen=generation;
        if(++count==TFF_GROUP){count=0;++generation;cv.notify_all();}
        else cv.wait(lock,[&]{return seen!=generation;});}} tff_barrier;
void barrier(){tff_barrier.wait();}
void memoryBarrierBuffer(){}
void memoryBarrierShared(){}
struct uvec2 {uint x,y; uvec2(uint a=0,uint b=0):x(a),y(b){}};
struct uvec4 {uint x,y,z,w; uvec4(uint a=0,uint b=0,uint c=0,uint d=0):x(a),y(b),z(c),w(d){}};
struct TffFeatureInstance {uvec4 refs; uint count=0,ns=0;};
struct TffFeatureNode {uvec4 meta,links; float extent=4; uint cell=0;};
struct TffAdmissionNode {uvec4 demand,cut,assignment;};
struct Footprint {uvec2 xy; float z;};
struct Camera {uvec4 counts,caps0,caps1;} cam;
TffFeatureInstance instances[256]; TffFeatureNode nodes[512];
TffAdmissionNode admission_nodes[512]; uvec2 instance_nodes[256];
uvec4 leaves[256],admitted[256],frontier0[512],frontier1[256];
uint representative_cells[256],args[224];
const uint TFF_NONE=0xffffffffu,TFF_NODE_FLAG_LEAF=2,TFF_SLOT_META=54,TFF_SLOT_ADMITTED=17;
const uint TFF_ADMITTED_REPRESENTATIVE=1,TFF_SLOT_FRONTIER=0,TFF_SLOT_LEAVES=16,TFF_SLOT_LEAF_GROUPS=55;
uint tff_node_slot(uint instance,uint node){return instance_nodes[instance].x+node;}
uint tff_instance_source_namespace(TffFeatureInstance inst){return inst.ns;}
Footprint tff_node_footprint(TffFeatureInstance,TffFeatureNode n){return {{n.cell,0},n.extent};}
uint tff_representative_cell(uvec2 xy){return xy.x;}
void atomicOr(uint& word,uint value){std::lock_guard<std::mutex> g(tff_atomic_mutex);word|=value;}
void atomicMin(uint& word,uint value){std::lock_guard<std::mutex> g(tff_atomic_mutex);word=min(word,value);}
void atomicMax(uint& word,uint value){std::lock_guard<std::mutex> g(tff_atomic_mutex);word=std::max(word,value);}
void tff_set_count(uint slot,uint count){args[slot*4+3]=count;}
uint tff_count(uint slot,uint cap){return min(args[slot*4+3],cap);}
bool tff_append(uint slot,uint cap,uint& index){
    index=args[slot*4+3]++;if(index<cap)return true;atomicOr(args[54*4+1],1u<<slot);return false;
}
void tff_publish_dispatch_count(uint,uint,uint){}
void reset(uint budget){
    cam={uvec4(1,1,1,0),uvec4(512,budget,budget,0),uvec4(0,budget,budget,0)};
    std::fill(std::begin(args),std::end(args),0u);
    std::fill(std::begin(representative_cells),std::end(representative_cells),TFF_NONE);
    std::fill(std::begin(admission_nodes),std::end(admission_nodes),TffAdmissionNode{});
    std::fill(std::begin(instance_nodes),std::end(instance_nodes),uvec2{});
    std::fill(std::begin(nodes),std::end(nodes),TffFeatureNode{});
    instances[0]={uvec4(0,0,0,7),3,100};
    nodes[0].links=uvec4(1,2,0,0);
    nodes[1].meta=nodes[2].meta=uvec4(TFF_NODE_FLAG_LEAF);
    nodes[0].cell=0;nodes[1].cell=1%budget;nodes[2].cell=2%budget;
    admission_nodes[0].demand=uvec4(1,0,0,1);
    admission_nodes[1].demand=uvec4(2,0,0,1);
    admission_nodes[2].demand=uvec4(8,0,0,1);
    frontier0[0]=uvec4(0,0);frontier0[1]=uvec4(0,1);frontier0[2]=uvec4(0,2);
    args[3]=1;args[7]=2;
}
''' + production + r'''
// Host count 1 dispatches exactly one workgroup of TFF_GROUP lanes.
void run_group(void(*step)(uint)){
    std::vector<std::thread> lanes;
    for(uint lane=0;lane<TFF_GROUP;++lane)lanes.emplace_back(step,lane);
    for(auto& lane:lanes)lane.join();
}
void cut(){
    for(uint level=2;level>0;--level)
        for(uint i=0;i<args[(level-1)*4+3];++i)bottom_up(i,level-1);
    run_group(prefix);
    for(uint level=0;level<2;++level)
        for(uint i=0;i<args[level*4+3];++i)top_down(i,level);
    for(uint i=0;i<args[16*4+3];++i){
        auto e=leaves[i];auto& state=admission_nodes[tff_node_slot(e.x,e.y)];
        state.assignment.y=e.y==1?2:3;state.assignment.z=state.demand.x;
    }
    run_group(seal);
}
int main(){
    reset(10);cut();
    if(admission_nodes[0].demand.x!=10 || args[17*4+3]!=5 || args[54*4+2]!=10 || args[54*4+1])return 1;
    if(admission_nodes[1].assignment.x!=2 || admission_nodes[2].assignment.x!=8)return 2;
    if(!admission_nodes[1].cut.z || !admission_nodes[2].cut.z || leaves[0].w!=0 || leaves[1].w!=2)return 3;
    reset(5);cut();
    if(args[16*4+3]!=0 || args[17*4+3]!=2 || args[54*4+2]!=2 || args[54*4+1])return 4;
    if(admitted[0].w!=1 || admitted[1].w!=1 || admitted[0].y!=1 || admitted[1].y!=2)return 5;
    reset(1);cut();
    if(args[17*4+3]!=1 || admitted[0].y!=0 || admitted[0].w!=1 || admission_nodes[0].cut.z)return 6;
    // Successor to attempt-146's unsupported root-overload fixture: 200 roots,
    // three native cells, no overflow/zero-output or arrival-order selection.
    reset(3);cam.counts.x=200;cam.counts.z=0;args[3]=200;args[7]=0;
    for(uint k=0;k<200;++k){
        instances[k]={uvec4(k,0,0,k+1),1,k+100};instance_nodes[k].x=k;
        nodes[k].meta=uvec4(TFF_NODE_FLAG_LEAF);nodes[k].links=uvec4();nodes[k].cell=k%3;
        admission_nodes[k].demand=uvec4(100,0,0,1);frontier0[k]=uvec4(k,0);
    }
    run_group(prefix);
    // Reverse invocation order cannot change the stable source-cell winners.
    for(uint k=200;k>0;--k)top_down(k-1,0);
    run_group(seal);
    if(args[17*4+3]!=3 || args[54*4+2]!=3 || args[54*4+1] || args[16*4+3])return 7;
    for(uint k=0;k<3;++k)if(admitted[k].x<3 || admitted[k].x>5)return 8;
    // A folded root retains one cell mark while its neighbor spends only
    // the remaining two units on a whole fine leaf.
    reset(3);cam.counts.x=2;args[3]=2;args[7]=0;
    instances[0].count=1;instances[1]={uvec4(1,0,0,8),1,101};instance_nodes[1].x=1;
    nodes[0].meta=nodes[1].meta=uvec4(TFF_NODE_FLAG_LEAF);
    nodes[0].links=nodes[1].links=uvec4();nodes[0].cell=nodes[1].cell=0;
    admission_nodes[0].demand=uvec4(2,0,0,1);admission_nodes[1].demand=uvec4(1,0,0,1);
    frontier0[0]=uvec4(0,0);frontier0[1]=uvec4(1,0);
    run_group(prefix);top_down(0,0);top_down(1,0);
    if(admission_nodes[0].assignment.x!=2 || admission_nodes[1].assignment.x!=1)return 15;
    if(args[16*4+3]!=1 || !admission_nodes[0].cut.z || admission_nodes[1].cut.z)return 16;
    admission_nodes[0].assignment.y=admission_nodes[0].assignment.z=2;
    run_group(seal);
    if(args[17*4+3]!=3 || args[54*4+2]!=3 || args[54*4+1] || admitted[0].x!=1)return 17;
    reset(10);cam.counts.y=2;nodes[0].extent=1.5;
    admission_nodes[0].cut=uvec4(100,0,1,1);
    if(!tff_split_scale(0,0))return 9;
    admission_nodes[0].cut.z=0;if(tff_split_scale(0,0))return 10;
    admission_nodes[0].cut.z=1;nodes[0].extent=1.0;if(tff_split_scale(0,0))return 11;
    admission_nodes[0].cut.x=101;nodes[0].extent=1.5;if(tff_split_scale(0,0))return 12;
    reset(9);admission_nodes[2].demand.x=TFF_NONE;cut();
    if(args[17*4+3]>9 || args[54*4+2]>9 || args[54*4+1])return 13;
    // Stale child data never contributes to this frame's demand.
    reset(10);admission_nodes[2].demand.w=0;bottom_up(0,0);
    if(admission_nodes[0].demand.x!=2)return 14;
    return 0;
}
'''
    compiler = shutil.which("c++")
    assert compiler
    binary = tmp_path / "hierarchy_cut_unit"
    result = subprocess.run([compiler, "-std=c++17", "-O1", "-pthread", "-x", "c++", "-", "-o", str(binary)],
                            input=program, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_production_reserve_group_scan_matches_source_order(tmp_path):
    """Run production reserve() on 64 real threads against the serial order.

    Every early-admitted leaf (terrain or object) takes its tff_leaf_slots
    range in admission order; its base lands in leaves[k].w. A total above
    the slot map sets the admitted overflow bit. No GPU claim.
    """
    features = Path(__file__).resolve().parents[2] / "godot_project/render/ascii/features"
    source = (features / "true_ff_feature_traverse.glslinc").read_text()
    shared = (features / "true_ff_feature_passes.glslinc").read_text()
    production = _production_function(shared, "tff_leaf_slots") + "\n" + \
        _production_function(shared, "tff_capacity_report") + "\n" + \
        "\n".join(_production_function(source, name) for name in
                  ("tff_charge_add", "tff_group_sync", "tff_lane_range",
                   "tff_group_exclusive", "reserve"))
    production = re.sub(r"\bout (\w+) (\w+)", r"\1& \2", production)
    program = r'''
#include <algorithm>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <thread>
#include <vector>
using uint=std::uint32_t; using std::min;
const uint TFF_GROUP=64,TFF_NONE=0xffffffffu,TFF_SLOT_META=54,TFF_SLOT_ADMITTED=17,TFF_SLOT_LEAVES=16;
const uint TFF_PENDING_NATIVE_FEASIBILITY=1u<<25u,TFF_DIAG_CAPACITY_OVERFLOW=1u<<31u;
uint tff_scan_lane[64],tff_scan_total;
std::mutex tff_atomic_mutex;
struct GroupBarrier{std::mutex m;std::condition_variable cv;uint count=0,generation=0;
    void wait(){std::unique_lock<std::mutex> lock(m);uint seen=generation;
        if(++count==TFF_GROUP){count=0;++generation;cv.notify_all();}
        else cv.wait(lock,[&]{return seen!=generation;});}} tff_barrier;
void barrier(){tff_barrier.wait();}
void memoryBarrierBuffer(){}
void memoryBarrierShared(){}
struct uvec2{uint x,y;uvec2(uint a=0,uint b=0):x(a),y(b){}};
struct uvec4{uint x,y,z,w;uvec2 yz;uvec4(uint a=0,uint b=0,uint c=0,uint d=0):x(a),y(b),z(c),w(d),yz(b,c){}};
struct TffFeatureInstance{uvec4 refs;bool terrain;};
struct TffFeatureNode{uvec4 links;uint terrain_leaves;};
struct TffAdmissionNode{uvec4 demand,cut,assignment;};
struct Camera{uvec4 counts,caps0,caps1;} cam;
TffFeatureInstance instances[300];TffFeatureNode nodes[300];
TffAdmissionNode admission_nodes[300];uvec4 leaves[300];uint args[224];
uint tff_node_slot(uint,uint node){return node;}
bool tff_is_terrain(TffFeatureInstance i){return i.terrain;}
uint tff_terrain_leaf_count(TffFeatureNode n){return n.terrain_leaves;}
uint tff_count(uint slot,uint cap){return min(args[slot*4+3],cap);}
void atomicOr(uint& word,uint value){std::lock_guard<std::mutex> g(tff_atomic_mutex);word|=value;}
''' + production + r'''
int check(uint instance_count,uint leaf_count,uint slot_map){
    cam={uvec4(instance_count,9,0,0),uvec4(0,300,0,0),uvec4(0,0,0,slot_map)};
    std::fill(std::begin(args),std::end(args),0u);args[TFF_SLOT_LEAVES*4+3]=leaf_count;
    for(uint k=0;k<instance_count;++k)instances[k]={uvec4(0),k%5==0};
    // The earlier exact-charge phase publishes each complete descriptor count
    // in leaves.z; reserve scans that admitted value without rescanning geometry.
    for(uint k=0;k<300;++k)nodes[k]={uvec4(0,0,0,k%3+1),k%4+2};
    for(uint k=0;k<leaf_count;++k)leaves[k]=uvec4((k*3)%instance_count,k,((k%4)+1)*2,TFF_NONE);
    std::vector<std::thread> lanes;
    for(uint lane=0;lane<TFF_GROUP;++lane)lanes.emplace_back(reserve,lane);
    for(auto& lane:lanes)lane.join();
    uint base=0;
    for(uint k=0;k<leaf_count;++k){
        if(leaves[k].w!=base)return 1;
        base+=leaves[k].z>>1u;
    }
    uint expected=TFF_PENDING_NATIVE_FEASIBILITY|TFF_DIAG_CAPACITY_OVERFLOW
        |(1u<<TFF_SLOT_ADMITTED);
    bool overflow=(args[TFF_SLOT_META*4+1]&TFF_PENDING_NATIVE_FEASIBILITY)!=0u
        && (args[TFF_SLOT_META*4+3]&expected)==expected;
    return overflow==(base>slot_map)?0:3;
}
int main(){
    if(int r=check(150,130,100000))return r;
    if(int r=check(3,2,100000))return 10+r;    // fewer items than lanes
    if(int r=check(0,0,1))return 20+r;          // empty population
    if(int r=check(150,130,10))return 30+r;     // overflow is reported
    return 0;
}
'''
    binary = tmp_path / "reserve_group_unit"
    result = subprocess.run([shutil.which("c++"), "-std=c++17", "-O1", "-pthread", "-x", "c++", "-",
                             "-o", str(binary)], input=program, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], text=True, capture_output=True)
    assert result.returncode == 0, f"exit {result.returncode}"


def test_fixed_page_arena_removes_current_frame_readback_and_precedes_p2():
    features = Path(__file__).resolve().parents[2] / "godot_project/render/ascii/features"
    host = (features / "TrueFFFeaturePasses.gd").read_text()
    run = host.split("func execute(", 1)[1].split("\n\n##", 1)[0]
    assert "buffer_get_data(" not in run and "buffer_get_data_async" not in run
    assert "_reserve_candidates(" not in run
    record = host.split("func _record_cut_round(", 1)[1].split("\n\n##", 1)[0]
    assert record.index("TRAVERSE_KERNELS[8]") < record.index("TRAVERSE_KERNELS[13]") \
        < record.index("TRAVERSE_KERNELS[9]") < record.index("TRAVERSE_KERNELS[6]") \
        < record.index("for kernel in [2, 37, 38, 39]:") < record.index("TRAVERSE_KERNELS[3]") \
        < record.index("TRAVERSE_KERNELS[4]") < record.index("EXTRACT_KERNELS[0]")
    assert "var candidate_slots: int = _page_candidate_capacity(_page_budget(camera_facts))" in host
    assert '"admitted": candidate_slots' in host and '"crossings": candidate_slots' in host
    source = (features / "true_ff_feature_traverse.glslinc").read_text()
    assert "bool bypass = (n.meta.x & TFF_NODE_FLAG_BYPASS) != 0u" in source
    assert "if (!bypass && tff_node_occluded" in source
    assert "if (!tff_split_scale(instance, node)) {" in source
    visit = _production_function(source, "tff_prepare_node")
    assert "tff_feature_demand" not in visit and "tff_leaf_address" not in visit
    assert "tff_append(TFF_SLOT_LEAVES" not in visit
    reduction = _production_function(source, "bottom_up")
    cut = _production_function(source, "top_down")
    assert "tff_instance_node_count" not in reduction + cut
    assert "for (uint instance" not in reduction + cut
    assert "admitted[base++]" in source and "atomicAdd(inst_state[l.x].x" not in source


def test_summary_charge_bounds_production_density_including_saturation(tmp_path):
    """Execute early summary and exact density bodies against 5,008 spans.

    This checks conservative arithmetic, not the bank's sphere containment
    for every source, GPU execution, visual detail quality, or frame cost.
    """
    root = Path(__file__).resolve().parents[2]
    geometry = (root / "godot_project/render/ascii/composition/true_ff_contour_geometry.glslinc").read_text()
    density = (root / "godot_project/render/ascii/features/true_ff_feature_density.glslinc").read_text()
    traverse = (root / "godot_project/render/ascii/features/true_ff_feature_traverse.glslinc").read_text()
    functions = "\n".join(function_body(geometry, name) for name in
                          ("tff_arc_level", "tff_arc_spacing", "tff_arc_step_at",
                           "tff_arc_level_window", "tff_arc_range"))
    summary = "\n".join(_production_function(traverse, name) for name in
                        ("tff_charge_mul", "tff_summary_feature_charge", "tff_summary_arc_length"))
    program = r'''
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
using uint=std::uint32_t;
using std::min;using std::max;using std::ceil;using std::sqrt;using std::exp2;
using std::isnan;using std::isinf;
float clamp(float x,double a,double b){return std::clamp(x,float(a),float(b));}
float max(float x,double y){return std::max(x,float(y));}
float min(float x,double y){return std::min(x,float(y));}
uint floatBitsToUint(float x){uint b;std::memcpy(&b,&x,4);return b;}
float uintBitsToFloat(uint b){float x;std::memcpy(&x,&b,4);return x;}
struct TFFInterval{float lo,hi;};
struct TFFSiteRange{int first,count;};
struct{struct{float x;}arc;}cam;
const uint TFF_NONE=0xffffffffu;
''' + functions + density + summary + r'''
int main(){
    float length,units,wa,wb,length_bound,units_bound;
    cam.arc.x=1.0f/1024.0f;
    // Tiny geometry at a large cumulative fixed-class chart may jump by
    // one float32 ULP. Retained root/feature-count allowance must cover it.
    float chart=1125899906842624.0f;
    float chart_jump=uintBitsToFloat(floatBitsToUint(chart)+1u)-chart;
    float support_length=tff_summary_arc_length(0.5f,100000000.0f,10000000u);
    TffDensity chart_density=tff_density_make(chart_jump,0.25f,1.0f,1.0f);
    uint chart_exact=tff_density_bound(chart_density,tff_density_plan_mask(chart_density,cam.arc.x),cam.arc.x);
    if(tff_summary_feature_charge(0.25f,1.0f,1.0f,true)>=chart_exact)return 2; // old diameter alone fails
    if(tff_summary_feature_charge(0.25f,support_length,1.0f,true)<chart_exact)return 3;
    while(std::cin>>length>>units>>wa>>wb>>length_bound>>units_bound){
        TffDensity d=tff_density_make(length,units,wa,wb);
        uint exact=tff_density_bound(d,tff_density_plan_mask(d,cam.arc.x),cam.arc.x);
        uint early=tff_summary_feature_charge(units_bound,length_bound,max(wa,wb)/min(wa,wb),wa==wb);
        if(early<max(exact,1u))return 1;
    }
}
'''
    rng = random.Random(4907)
    cases = []
    for _ in range(5000):
        length = 2 ** rng.uniform(-12, 36)
        units = 2 ** rng.uniform(-12, 22)
        wa = 2 ** rng.uniform(-12, 12)
        wb = wa if rng.random() < .5 else 2 ** rng.uniform(-12, 12)
        cases.append((length, units, wa, wb, length * rng.uniform(1, 4),
                      units * rng.uniform(1, 4)))
    for exponent in (29, 30, 34, 40):
        length = (1 / 1024) * (1 << exponent)
        cases.extend([(length, .25, 1, 1, length, .25),
                      (length, .25, 1, 32, length, .25)])
    binary = tmp_path / "summary_charge_unit"
    result = subprocess.run([shutil.which("c++"), "-std=c++17", "-O1", "-x", "c++", "-", "-o", str(binary)],
                            input=program, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], input="\n".join(" ".join(map(str, case)) for case in cases),
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def projected_bound(units, wa, wb):
    # Each disjoint level interval owns at most integral du/dt + one site.
    # Local desired spacing is proportional to w(t)^2. Its log2 range is
    # 2*abs(log2(wb/wa)); equal w gives exactly one interval.
    intervals = 1 if wa == wb else math.ceil(2 * abs(math.log2(wb / wa))) + 2
    return math.ceil(units) + intervals


def test_projected_count_bound_under_phase_metric_changes(density_unit):
    cases = [(length, units, wa, wb, 1 / 1024, origin)
             for length in (1 / 16, 1 / 4, 1, 4, 16)
             for units in (.125, 1, 3.9, 31.9, 100, 512)
             for wa, wb in ((1, 1), (1, 2), (2, 1), (1, 32), (32, 1),
                            (1, 1.0000001192092896), (1.0000001192092896, 1))
             for origin in (-.31, 0, .17)]
    sets = evaluate(density_unit, cases)
    for case, sites in zip(cases, sets):
        _, units, wa, wb, _, _ = case
        assert len(sites) <= projected_bound(units, wa, wb), (case, len(sites))


def test_physical_mask_cannot_authorize_a_transported_phase_metric():
    # The concrete FL-4907 attempt-71 counterexample, now executable.
    physical = ref.Density(2., 100., 1., 1.)
    transported = ref.Density(1., 100., 1., 1.)
    quantum = 1 / 1024
    mask = ref.density_plan_mask(physical, quantum)
    phase_level = ref.density_point_level(transported, .5, quantum)
    assert mask == 32 and phase_level == 4
    assert not (mask & (1 << phase_level))


def test_projected_bound_is_not_valid_after_current_level_saturation():
    # This is a counterexample, not a passing acceptance test for the bound.
    # tff_arc_level stops at 29 even when spacing is still too fine; a P1
    # projected-only budget would underallocate and silently lose sites.
    quantum = 1 / 1024
    phase_length = quantum * (1 << 34)
    d = ref.Density(phase_length, .25, 1., 1.)
    level = ref.density_point_level(d, .5, quantum)
    assert level == 29
    emitted = math.ceil(phase_length / (quantum * (1 << level)))
    assert emitted == 32 > projected_bound(.25, 1., 1.) == 2
    assert ref.density_bound(d, ref.density_plan_mask(d, quantum), quantum) >= emitted


def test_segment_local_physical_lattice_is_not_phase_transport():
    # Falsifier for the reviewed proposal, NOT a successor implementation.
    # One unchanged straight line [0,1] has a moving interior source crossing.
    # Resetting physical s=0 at that crossing moves its downstream sites even
    # if a separate retained phase is correctly interpolated onto each site.
    # A metadata label cannot move those positions back onto the joined run.
    quantum = 1 / 1024
    outputs = []
    for crossing in (320 / 1024, 384 / 1024):
        length = 1 - crossing
        d = ref.Density(length, 4 * length, 1., 1.)
        level = ref.density_point_level(d, .5, quantum)
        assert level == 8  # same local projected density, no LOD event
        spacing = quantum * (1 << level)
        outputs.append([crossing + n * spacing
                        for n in range(math.ceil(length / spacing))])
    assert outputs == [[.3125, .5625, .8125], [.375, .625, .875]]
    assert all(new - old == .0625 for old, new in zip(*outputs))


def test_normalized_edge_delta_is_not_a_transported_phase_gradient():
    # Falsifier for phi += sign * delta(rho), NOT a production tracker.
    # rho is dimensionless position on a retained edge; phi is the joined
    # contour coordinate. A change of chart units must scale the update too.
    # Reversing an edge supplies a sign, not the missing phase Jacobian.
    rho_before, rho_after = .25, .375
    phase_before = .5
    delta = rho_after - rho_before
    base_proposal = phase_before + delta
    scaled_proposal = 2 * phase_before + delta
    assert base_proposal == .625 and scaled_proposal == 1.125
    assert scaled_proposal != 2 * base_proposal
    # A scalar chart restricted to an edge has a derivative d(phi)/d(rho).
    # This necessary unit law alone is not a sufficient correspondence rule:
    # face transitions, deformation and chart lifecycle remain separate.
    derivative = 3.
    base_chart = phase_before + derivative * delta
    scaled_chart = 2 * phase_before + 2 * derivative * delta
    assert scaled_chart == 2 * base_chart


def test_frozen_crossing_phase_is_not_a_source_face_chart():
    # A canonical span length has units, but freezing phase at a moving
    # source-edge crossing still slides sites along an unchanged line.
    # Correspondence must evaluate a chart at the crossing's current source
    # coordinate, not attach a constant to its retained edge address.
    phase_at_crossing, phase_at_end = .3125, 1.
    q = .5
    t = (q - phase_at_crossing) / (phase_at_end - phase_at_crossing)
    old_crossing, new_crossing = .3125, .375
    old_site = old_crossing + (1. - old_crossing) * t
    new_site = new_crossing + (1. - new_crossing) * t
    assert old_site == .5
    assert new_site == pytest.approx(6. / 11.)
    assert new_site != old_site
    # Restricting the same source field phi(x)=x gives the necessary behavior
    # in this fixture. It is not a general surface-chart implementation.
    corrected_t = (q - new_crossing) / (phase_at_end - new_crossing)
    assert new_crossing + (1. - new_crossing) * corrected_t == old_site


def joined_sites(cam, points, phases):
    """Exercise existing P1 span planning and P4, not a phase tracker.

    The fixture supplies a valid joined source chart explicitly. Production
    still lacks its correspondence owner: these tests establish the sampling
    contract that owner must satisfy, not end-to-end temporal recovery.
    """
    instance = ref.Instance(np.eye(4), 0, 0, 0, 7)
    sites = {}
    plans = []
    for a, b, lo, hi in zip(points, points[1:], phases, phases[1:]):
        plan = ref.segment_density(cam, instance, a, b, lo, hi)
        assert plan is not None
        plans.append(plan)
        segment = ref.Segment(a, b, lo, hi, 1, ref.SMOOTH, 7, 0,
                              level_mask=plan.level_mask)
        emitted = ref.segment_sites(cam, segment)
        assert len(emitted) <= ref.density_bound(plan.density, plan.level_mask, cam.quantum)
        for site in emitted:
            assert site.q not in sites, "a joined endpoint acquired two owners"
            sites[site.q] = site.source_world
    return sites, plans


@pytest.mark.parametrize("ortho", [False, True])
def test_joined_phase_sampling_preserves_stationary_downstream_span(ortho):
    cam = ref.make_camera((0., 0., 4.), (0., 0., 0.), ortho=ortho)
    b, c = np.array((0., 0., 0.)), np.array((0., .3, 0.))
    outputs = []
    for ax in (-.21, -.26):
        # Valid retained phase at B and C must not be remeasured from A-B.
        points = [np.array((ax, 0., 0.)), b, c]
        sites, _ = joined_sites(cam, points, [0., .21, .51])
        outputs.append({q: p for q, p in sites.items() if .21 <= q * cam.quantum < .51})
    assert outputs[0] and outputs[0].keys() == outputs[1].keys()
    for q in outputs[0]:
        np.testing.assert_array_equal(outputs[0][q], outputs[1][q])


@pytest.mark.parametrize("ortho", [False, True])
@pytest.mark.parametrize("phase_scale", [.5, 1., 2.])
def test_joined_phase_inversion_survives_moving_internal_crossing(ortho, phase_scale, density_unit):
    cam = ref.make_camera((0., 0., 4.), (0., 0., 0.), ortho=ortho)
    a, b = np.array((-.5, -.25, 0.)), np.array((.7, .3, 1.1))
    phase_origin = .125
    full, _ = joined_sites(cam, [a, b], [phase_origin, phase_origin + phase_scale])
    for t in (.3125, .375):
        points = [a, a + (b - a) * t, b]
        phases = [phase_origin, phase_origin + phase_scale * t, phase_origin + phase_scale]
        split, plans = joined_sites(cam, points, phases)
        assert split.keys() == full.keys()
        for q in full:
            np.testing.assert_allclose(split[q], full[q], atol=1e-12, rtol=0)
        # Execute the actual shared GLSL density owner as float32 C++ too.
        # P1 and P4 must agree on the phase-coordinate span, not just length
        # in metres with a new label attached after physical enumeration.
        cases = [(p.density.length, p.density.units, p.density.wa, p.density.wb,
                  cam.quantum, p.arc_lo) for p in plans]
        sets = evaluate(density_unit, cases)
        assert not (sets[0] & sets[1])
        assert sets[0] | sets[1] == set(full)


def test_transported_coordinate_requires_its_own_shared_admission_mask(density_unit):
    # Unlike the rejected P4-only replacement, both phases use the same
    # parameter length. Geometry is still two units long; phi spans one.
    quantum = 1 / 1024
    transported = ref.Density(1., 100., 1., 1.)
    mask = ref.density_plan_mask(transported, quantum)
    assert mask == 1 << 4
    ordinals, = evaluate(density_unit, [(1., 100., 1., 1., quantum, 0.)])
    assert ordinals == set(range(0, 1024, 16))
    world_positions = [2 * q * quantum for q in sorted(ordinals)]
    assert world_positions == [i / 32 for i in range(64)]


def test_joined_phase_survives_near_plane_clipping(density_unit):
    cam = ref.make_camera((0., 0., 4.), (0., 0., 0.))
    a, b = np.array((-.1, 0., 3.95)), np.array((.3, .4, 0.))
    phases = (.125, 2.125)
    interval = ref.clip_source_interval(ref.clip_of(cam, a), ref.clip_of(cam, b))
    assert interval is not None and 0.0 < interval[0] < interval[1]
    # Clipping must trim the same coordinate; it must not restart the phase
    # at a newly visible endpoint. This checks P1/P4 together, not a tracker.
    clipped_points = [a + (b - a) * t for t in interval]
    clipped_phases = [phases[0] + (phases[1] - phases[0]) * t for t in interval]
    full, plans = joined_sites(cam, [a, b], phases)
    clipped, _ = joined_sites(cam, clipped_points, clipped_phases)
    assert full and full.keys() == clipped.keys()
    for q in full:
        np.testing.assert_allclose(full[q], clipped[q], atol=1e-12, rtol=0)
    plan, = plans
    ordinals, = evaluate(density_unit, [(plan.density.length, plan.density.units,
                                       plan.density.wa, plan.density.wb,
                                       cam.quantum, plan.arc_lo)])
    assert ordinals == set(full)
