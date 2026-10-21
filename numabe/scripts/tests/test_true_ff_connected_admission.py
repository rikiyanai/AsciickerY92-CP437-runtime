"""Production-function CPU controls; no Godot/device/runtime proof."""
import random
import re
import subprocess
import math
from pathlib import Path

import pytest

from scripts.tests.test_true_ff_end_to_end_repairs import function, gd_body, gd_syntax

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"


@pytest.fixture(scope="module")
def allocation(tmp_path_factory):
    source = (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    work = (FEATURES / "true_ff_feature_work.glslinc").read_text()
    production = "\n".join((function(work, "tff_interval_prefix"),
                              function(source, "tff_charge_add"),
                              function(source, "tff_root_extra"),
                              function(source, "root_budget")))
    shim = r'''
#include <algorithm>
#include <cstdint>
#include <iostream>
#include <vector>
using uint=uint32_t;using std::min;using std::max;
const uint TFF_NONE=0xffffffffu,TFF_PENDING_NATIVE_FEASIBILITY=1u<<25,TFF_SLOT_META=54,TFF_SLOT_FRONTIER=0;
struct V {uint x=0,y=0,z=0,w=0;};struct TffAdmissionNode {V demand,assignment;};
std::vector<TffAdmissionNode> admission_nodes;std::vector<V> frontier0;uint args[240]={};
void atomicOr(uint& a,uint b){a|=b;}
struct {V caps0;} cam;uint budget;
bool tff_p1_geometry_budget(uint& b){b=budget;return true;}
uint tff_count(uint,uint){return frontier0.size();}uint entry_slot(V v){return v.x;}
uint root_minimum(V){return 1u;}
'''
    main = r'''
int main(){std::string op;while(std::cin>>op){if(op=="prefix"){uint a,b,d;std::cin>>a>>b>>d;std::cout<<tff_interval_prefix(a,b,d)<<"\n";}
else{uint n;std::cin>>budget>>n;cam.caps0.x=budget;frontier0.assign(n,V{});admission_nodes.assign(n,TffAdmissionNode{});args[TFF_SLOT_META*4+1]=0;
for(uint k=0;k<n;k++){frontier0[k].x=k;std::cin>>admission_nodes[k].demand.x;}root_budget(0);
std::cout<<args[TFF_SLOT_META*4+1];for(auto v:admission_nodes)std::cout<<" "<<v.assignment.x;std::cout<<"\n";}}}
'''
    binary = tmp_path_factory.mktemp("ff-allocation") / "allocation"
    built = subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", str(binary)],
                           input=shim + production + main, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return binary


def query(binary, lines):
    result = subprocess.run([str(binary)], input="\n".join(lines) + "\n", capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return [[int(v) for v in line.split()] for line in result.stdout.splitlines()]


def test_production_prefix_is_exact_without_uint64_or_float(allocation):
    rng = random.Random(4907)
    cases = [(0, 0, 0), (0xffffffff, 0xffffffff, 0xffffffff), (0xffffffff, 1, 2)]
    for _ in range(2000):
        a, d = rng.randrange(2**32), rng.randrange(1, 2**32)
        cases.append((a, rng.randrange(d + 1), d))
    actual = query(allocation, [f"prefix {a} {b} {d}" for a, b, d in cases])
    assert actual == [[a * b // d if d else 0] for a, b, d in cases]


@pytest.mark.parametrize("budget,demands", [(17, [1, 20, 3]), (7, [2, 2]), (8, [0, 0, 100]),
    (100, [0xffffffff, 0xffffffff, 1]), (20, []), (2, [1, 1, 1])])
def test_production_root_intervals_use_demand_and_preserve_each_root(allocation, budget, demands):
    actual, = query(allocation, ["budget " + " ".join(map(str, [budget, len(demands)] + demands))])
    if len(demands) > budget:
        assert actual[0] == 1 << 25 and actual[1:] == [0] * len(demands)
        return
    weights = [min(max(v, 1), 0xffffffff // max(len(demands), 1)) - 1 for v in demands]
    extra, prefix = sum(weights), 0
    spare = min(budget - len(demands), extra)
    expected = []
    for weight in weights:
        end = prefix + weight
        expected.append(1 + (spare * end // extra - spare * prefix // extra if extra else 0))
        prefix = end
    assert actual == [0] + expected
    assert sum(expected) <= budget and all(v > 0 for v in expected)
    if demands == [1, 20, 3]:
        assert expected[1] > expected[2] > expected[0]


@pytest.fixture(scope="module")
def schedule(tmp_path_factory):
    source = (FEATURES / "true_ff_feature_traverse.glslinc").read_text()
    production = "\n".join(function(source, n) for n in ("schedule_round", "finish_schedule"))
    production = re.sub(r"uint counts\[6\]=uint\[6\]\(([^;]+)\);", r"uint counts[6]={\1};", production)
    shim = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <vector>
using uint=uint32_t;
struct V{uint x=0,y=0,z=0,w=0;V(uint a=0,uint b=0,uint c=0,uint d=0):x(a),y(b),z(c),w(d){}uint& operator[](uint i){assert(i<4);return (&x)[i];}};
using uvec4=V;struct uvec2{uint x,y;};
const uint TFF_SLOT_COUNT=60,TFF_SLOT_META=54,TFF_SLOT_CANDIDATES=20,TFF_SLOT_SITES=21;
const uint TFF_NATIVE_CERTIFICATE_WORD=236,TFF_NATIVE_BINDING_COUNT_WORD=239,TFF_NATIVE_CERTIFICATE_TAG=0x80000000u;
const uint TFF_PENDING_CLOSED_WITNESS=1u<<24,TFF_PENDING_NATIVE_FEASIBILITY=1u<<25,TFF_PENDING_SCENE_WITNESS=1u<<26,TFF_PENDING_SHADOW_ADMISSION=1u<<27;
const uint TFF_DIAG_ITEM_OMISSION=1u<<30,TFF_DIAG_CAPACITY_OVERFLOW=1u<<31;
const uint TFF_FRAME_FATAL_ERRORS=0u;
uint args[240]={};struct{std::vector<uint> words=std::vector<uint>(208);}retry_dispatch,retry_native_control;
struct{V dispatch;}native_plan;struct{V counts;struct{float x=220;}page;}cam;struct{V v;}pc;
bool ready=true;bool tff_shadow_plan_ready(){return ready;}
bool tff_native_epoch_certificate(uint word,uint epoch){return word==(TFF_NATIVE_CERTIFICATE_TAG|epoch);}
void atomicAnd(uint& a,uint b){a&=b;}void atomicOr(uint& a,uint b){a|=b;}
uvec2 tff_dispatch_dimensions(uint n,uint group,uint tile){uint x=(n+group-1)/group;return {x,1};}
void tff_set_count(uint slot,uint count){args[slot*4]=count;args[slot*4+1]=1;args[slot*4+2]=1;args[slot*4+3]=count;}
'''
    main = r'''
int main(){cam.counts=V(0,7,0,65535);pc.v=V(20,200,100,10);native_plan.dispatch=V(3,4,1,17);
args[217]=TFF_PENDING_CLOSED_WITNESS|TFF_PENDING_NATIVE_FEASIBILITY|TFF_PENDING_SHADOW_ADMISSION;
args[219]=TFF_DIAG_CAPACITY_OVERFLOW;
schedule_round(0);assert(args[217]==TFF_PENDING_SHADOW_ADMISSION);assert(args[219]==0);assert(retry_dispatch.words[4]==1);
for(uint s=0;s<60;s++)if(s!=54&&s!=59){args[s*4]=s+2;args[s*4+1]=2;args[s*4+2]=1;args[s*4+3]=s+5;}
args[217]=0;args[236]=TFF_NATIVE_CERTIFICATE_TAG|7;retry_native_control.words[0]=5;retry_native_control.words[1]=2;retry_native_control.words[2]=1;
auto saved=std::vector<uint>(args,args+240);schedule_round(0);assert(args[236]==saved[236]);assert(args[217]==0);assert(args[80]==0);
auto retained=retry_dispatch.words;schedule_round(0);assert(retained==retry_dispatch.words);assert(retry_dispatch.words[4]==0);
finish_schedule(0);assert(std::vector<uint>(args,args+240)==saved);assert(native_plan.dispatch.x==3&&native_plan.dispatch.y==4);assert(retry_native_control.words[0]==5);
cam.counts.y=8;args[236]=0;args[217]=0;schedule_round(0);assert(retry_dispatch.words[0]==0);assert(retry_dispatch.words[4]==1);
// A scene error is reported and retried, never a blank page: exhaustion keeps
// the round's cut published (never the saved prior one) and names the failure.
args[217]=(1u<<29)|TFF_PENDING_NATIVE_FEASIBILITY;schedule_round(0);assert(args[217]==(1u<<29));assert(retry_dispatch.words[4]==1);
finish_schedule(0);assert(args[217]&(1u<<29));assert(args[217]&TFF_PENDING_NATIVE_FEASIBILITY);assert(args[236]==0&&args[80]==22&&args[83]==25&&native_plan.dispatch.w==17);
args[217]=0;tff_set_count(20,12);finish_schedule(0);assert(args[217]==TFF_PENDING_NATIVE_FEASIBILITY&&args[83]==12);
// Exhausted capacity never publishes an arrival-selected prefix.
args[217]=0;args[219]=TFF_DIAG_CAPACITY_OVERFLOW;tff_set_count(20,7);native_plan.dispatch.w=3;
finish_schedule(0);assert(args[83]==0&&native_plan.dispatch.w==0);
args[219]=0;tff_set_count(20,12);native_plan.dispatch.w=17;
// A positive-looking token cannot authorize a simultaneous source/ABI error.
args[217]=1u<<29;args[236]=TFF_NATIVE_CERTIFICATE_TAG|8;schedule_round(0);
assert(retry_dispatch.words[4]==1);finish_schedule(0);
assert(args[217]&(1u<<29));assert(args[83]==12&&native_plan.dispatch.w==17);
// A legacy low slot diagnostic cannot regain whole-page authority.
args[217]=1u<<20;args[236]=0;schedule_round(0);assert(retry_dispatch.words[4]==1);
finish_schedule(0);assert(args[217]&(1u<<20));assert(args[83]==12&&native_plan.dispatch.w==17);
// Only a capacity failure, or a missing shadow plan, publishes nothing.
args[217]=0;tff_set_count(20,9);native_plan.dispatch.w=4;ready=false;schedule_round(0);assert(retry_dispatch.words[4]==0);
finish_schedule(0);assert(args[83]==0&&native_plan.dispatch.w==0);ready=true;
}
'''
    binary = tmp_path_factory.mktemp("ff-schedule") / "schedule"
    built = subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", str(binary)],
                           input=shim + production + main, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return binary


def test_production_schedule_never_replays_old_output_and_keeps_failed_cut(schedule):
    result = subprocess.run([str(schedule)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_transport_consumers_and_no_ff_cosmetic_pose_hold_are_connected():
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    world = (ROOT / "godot_project/scripts/World.gd").read_text()
    assert frame.index("_figures.prepare(figure_scene)") < frame.index("_bank.prepare_residency(device, scene_db")
    assert frame.index('_bank.apply_changes("figures"') > frame.index("_bank.prepare_residency(device, scene_db")
    assert 'scene_db.call("get_static_tlas")' in bank and "scene_db.get_static_instance(i)" in bank
    assert "figure_binding, figure_ordinal" in bank and '"figure_bindings": _figure_bindings' in bank
    assert "CAMERA_BYTES := 256" in host and "out.encode_float(244, native_origin.y)" in host
    assert "_uniform(64," in host and "[figure_bindings]" in host
    assert "_uniform(65," in host and '[_buffers["retry_dispatch"]]' in host
    assert "_dispatch_retry_shape" in host and '[20, int(_caps["candidates"])' in host
    assert re.search(r"if _true_ff_effect == null or not _true_ff_effect.enabled:\n\s+instance = actor_feedback.hold_pose", world)


def test_production_source_residency_stamp_changes_only_at_owned_ingress():
    source = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    class Obj:
        def __init__(self, identity): self.identity, self.values, self.calls = identity, {}, []
        def get_instance_id(self): return self.identity
        def has_method(self, name): return name in self.values
        def call(self, name): self.calls.append(name); return self.values[name]
        def get(self, name): return self.values.get(name)
    class RID:
        def __init__(self, value=0): self.value = value
        def get_id(self): return self.value
    state = {"Object": Obj, "RID": RID, "is_instance_valid": lambda v: isinstance(v, Obj)}
    for name, args in (("_owner_id", "value"), ("_source_residency_stamp", "scene")):
        body = gd_syntax(gd_body(source, name)).replace(" as Object", "")
        body = body.replace("value is Object", "isinstance(value, Object)")
        exec(f"def {name}({args}):\n" + body, state)
    db, actor, rigid = Obj(1), Obj(2), Obj(3)
    db.values.update(true_ff_static_residency_generation=4, rigid_meshes=rigid)
    scene = {"scene_db": db, "actor_pose_db": actor, "terrain_height_rid": RID(6),
             "terrain_material_rid": RID(7), "waterfall_site_field": Obj(8),
             "water_flow_runtime": Obj(9), "wake_trail_field": Obj(10)}
    stamp = state["_source_residency_stamp"]
    initial = stamp(scene)
    assert initial == [1, 4, 2, 3, 6, 7, 8, 9, 10]
    scene.update(camera="moving", view_index=7, frame_clock=99.0)
    db.values["revision"] = 200  # active-camera roster is not registry ownership
    assert stamp(scene) == initial
    assert set(db.calls) == {"true_ff_static_residency_generation"}
    for key, value in (("true_ff_static_residency_generation", 11), ("rigid_meshes", Obj(13))):
        old = db.values[key]; db.values[key] = value
        assert stamp(scene) != initial
        db.values[key] = old
    for key in ("actor_pose_db", "waterfall_site_field", "water_flow_runtime", "wake_trail_field"):
        old = scene[key]; scene[key] = Obj(14)
        assert stamp(scene) != initial
        scene[key] = old
    scene["terrain_height_rid"] = RID(15)
    assert stamp(scene) != initial
    ingress = gd_body(source, "begin_source_frame")
    assert ingress.index("_residency_stamp != _source_residency_stamp(scene)") < ingress.index("if not residency_ready()")
    assert ingress.index('"source_residency_changed"') < ingress.index("_source_frame_open = true")
    assert "prepare_residency(_rd, scene)" not in ingress
    assert 'accepted_effect_event["residency"] = scene' in ingress
    preparation = gd_body(source, "prepare_residency")
    assert '_actors.withdraw("actor_owner_residency_changed")' in preparation
    assert '_rigid.withdraw("rigid_owner_residency_changed")' in preparation
    assert 'residency_events[family] = {"residency": scene}' in preparation


def test_production_world_source_readiness_contains_missing_scene_or_rigid_owner():
    source = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
    class Obj: pass
    class RID:
        def __init__(self, valid=True): self.valid = valid
        def is_valid(self): return self.valid
    db = Obj(); db.rigid_meshes = Obj()
    state = {"rd": Obj(), "sampler_rd": RID(), "matlib_rd": RID(),
             "_scene_geometry_db": db, "_rigid_buffers": Obj(),
             "is_instance_valid": lambda v: isinstance(v, Obj)}
    exec("def reason():\n" + gd_syntax(gd_body(source, "world_source_readiness_reason")), state)
    assert state["reason"]() == ""
    state["_scene_geometry_db"] = None
    assert state["reason"]() == "world_scene_geometry_owner_missing"
    state["_scene_geometry_db"] = db; db.rigid_meshes = None
    assert state["reason"]() == "world_rigid_geometry_owner_missing"
    db.rigid_meshes = Obj(); state["_rigid_buffers"] = None
    assert state["reason"]() == "world_rigid_geometry_owner_missing"
    state["_rigid_buffers"] = Obj(); state["rd"] = None
    assert state["reason"]() == "world_device_sampler_or_material_missing"
    begin = gd_body(source, "begin_world_frame")
    assert begin.index("world_source_readiness_reason()") < begin.index("_publish_dynamic_mover_source_facts_for_pixel_context(ctx)")
    assert '"coverage_complete": false' in begin
    sync = gd_body(source, "_sync_rigid_world_sources")
    assert sync.index("if not is_instance_valid(_scene_geometry_db)") < sync.index("_scene_geometry_db.get_instance_id()")


def test_production_reticle_basis_rolls_once_and_stays_orthogonal():
    """Execute the source-owned aiming-plane maths, not a guessed basis."""
    source = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
    body = source.split("func _append_dynamic_reticle(", 1)[1].split("\nfunc ", 1)[0]
    body = body[body.index("\tvar pos:"):body.index("\t_append_dynamic_mover_face(")]
    body = re.sub(r"\bvar (\w+)(?:: \w+)?\s*(?::=|=)", r"\1 =", body)
    # Godot Vector3 operations are the only adapter; extracted statements
    # retain the production roll/cross order and near-vertical fallback.
    class V:
        def __init__(self, x=0.0, y=0.0, z=0.0): self.x, self.y, self.z = x, y, z
        def __add__(self, o): return V(self.x + o.x, self.y + o.y, self.z + o.z)
        def __sub__(self, o): return V(self.x - o.x, self.y - o.y, self.z - o.z)
        def __mul__(self, s): return V(self.x * s, self.y * s, self.z * s)
        def dot(self, o): return self.x * o.x + self.y * o.y + self.z * o.z
        def cross(self, o): return V(self.y * o.z - self.z * o.y, self.z * o.x - self.x * o.z, self.x * o.y - self.y * o.x)
        def length_squared(self): return self.dot(self)
        def normalized(self): return self * (1.0 / math.sqrt(self.length_squared()))
        def rotated(self, axis, angle):
            c, s = math.cos(angle), math.sin(angle)
            return self * c + axis.cross(self) * s + axis * (axis.dot(self) * (1.0 - c))
    V.ZERO, V.UP, V.RIGHT = V(), V(0, 1, 0), V(1, 0, 0)
    for yaw in (0.0, 0.7, 2.9):
        for pitch in (0.0, -0.8, math.pi / 2, math.pi / 2 - 0.003):
            values = []
            for roll in (0.0, 0.4, math.pi / 2, -2.1):
                env = {"fact": {"facing_yaw": yaw, "pitch": pitch, "roll": roll, "radius": 0.42},
                       "Vector3": V, "sin": math.sin, "cos": math.cos}
                exec("if True:\n" + body, env)
                right, up, fwd = env["right"], env["up"], env["fwd"]
                assert abs(right.dot(up)) < 1e-12
                assert abs(right.dot(fwd)) < 1e-12 and abs(up.dot(fwd)) < 1e-12
                assert math.isclose(right.length_squared(), 0.42**2)
                assert math.isclose(up.length_squared(), 0.42**2)
                if values:
                    zero_right, zero_up = values[0]
                    expected_right = zero_right.rotated(fwd, roll)
                    expected_up = zero_up.rotated(fwd, roll)
                    assert (right - expected_right).length_squared() < 1e-24
                    assert (up - expected_up).length_squared() < 1e-24
                values.append((right, up))
