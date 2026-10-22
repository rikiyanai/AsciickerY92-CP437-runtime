"""Execute production Figure descriptor/facing/tier functions on CPU.

Matrix fixtures cover identity/diagonal affine and positive-w projection,
not Godot, the general matrix inverse or device residency/rendering.
"""
import re
import subprocess
import copy
import json
import struct
from pathlib import Path

import pytest

from scripts.tests.test_true_ff_end_to_end_repairs import function, gd_body, gd_syntax

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"
LANGUAGE = ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json"


class D(dict):
    def duplicate(self, deep=False): return D(copy.deepcopy(self) if deep else self)
    def keys(self): return list(super().keys())
    def erase(self, key): self.pop(key, None)


class Bytes(bytearray):
    def duplicate(self): return Bytes(self)
    def is_empty(self): return not self
    def resize(self, size): self.extend(b"\0" * max(size - len(self), 0)); del self[size:]
    def fill(self, value): self[:] = bytearray([value]) * len(self)
    def encode_u32(self, at, value): struct.pack_into("<I", self, at, int(value))
    def decode_u32(self, at): return struct.unpack_from("<I", self, at)[0]
    def append_array(self, values): self.extend(values)


def test_production_immutable_binding_publisher_is_transactional_and_stable():
    source = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    body = gd_syntax(gd_body(source, "_prepare_figure_bindings"))
    body = body.replace("not product is Dictionary", "not isinstance(product, Dictionary)")
    body = body.replace('not product.get("bytes") is PackedByteArray',
                        'not isinstance(product.get("bytes"), PackedByteArray)')
    class RID:
        def __init__(self, at=0): self.at = at
        def is_valid(self): return bool(self.at)
    state = {"_figure_binding_bytes": Bytes(), "_figure_binding_offsets": D(),
             "_figure_binding_products": D(), "_figure_bindings": RID(),
             "_producer_omissions": D(), "_omissions_dirty": False,
             "String": str, "Dictionary": D, "PackedByteArray": Bytes,
             "create_ok": True, "created": [], "freed": [], "errors": []}
    def storage(data, stride):
        assert stride == 16
        if not state["create_ok"]: return RID()
        state["created"].append(Bytes(data)); return RID(len(state["created"]))
    state.update(_storage=storage, _gpu_free=lambda rid: state["freed"].append(rid.at) if rid.is_valid() else None,
                 _reject=lambda reason: state["errors"].append(reason) or False)
    exec("def publish(products):\n\tglobal _figure_binding_bytes,_figure_binding_offsets,_figure_binding_products,_figure_bindings\n" + body, state)
    def product(kind):
        data = Bytes(144 if kind == "face" else 48)
        data.encode_u32(0, 1); data.encode_u32(12, 1)
        return D(ready=True, kind=kind, bytes=data)
    face, fold = product("face"), product("fold")
    assert state["publish"](D(a=face))
    first = state["_figure_bindings"].at
    assert state["_figure_binding_offsets"] == {"a": 4}
    assert state["publish"](D(a=face)) and state["_figure_bindings"].at == first
    before = Bytes(state["_figure_binding_bytes"])
    bad = copy.deepcopy(face); bad["bytes"][16] = 1
    assert state["publish"](D(a=bad)) and state["_figure_binding_bytes"] == before
    assert state["_producer_omissions"]["figure_bindings"] == {
        "a": "figure_binding_immutable_key_changed"
    }
    state["create_ok"] = False
    assert state["publish"](D(b=fold))
    assert state["_figure_binding_offsets"] == {"a": 4} and state["_figure_bindings"].at == first
    assert state["_producer_omissions"]["figure_bindings"] == {
        "b": "figure_binding_upload_rejected"
    }
    state["create_ok"] = True
    assert state["publish"](D(b=fold))
    assert state["_figure_binding_offsets"] == {"a": 4, "b": 40}
    assert state["freed"] == [first]
    assert state["_figure_binding_bytes"].decode_u32(0) == 52
    for malformed in (D(ready=False, kind="face", bytes=face["bytes"]),
                      D(ready=True, kind="fold", bytes=Bytes(64)),
                      D(ready=True, kind="face", bytes=Bytes(48))):
        assert state["publish"](D(c=malformed))
    assert state["_figure_binding_offsets"] == {"a": 4, "b": 40}
    assert "c" in state["_producer_omissions"]["figure_bindings"]


def test_production_figure_pending_delta_survives_failed_bank_consumption():
    source = (FEATURES / "TrueFFFigureFeatures.gd").read_text()
    pending = D(upserts=D(face=D(object_id=7)), removals=[], banks=D(bank=D()))
    state = {"_prepared": True, "_preparation_key": [1], "_pending_changes": pending,
             "_pending_sources": D(face=D(object_id=7)), "_published": D(),
             "_published_banks": D(), "_static_annotations": D(), "_bindings": D(),
             "_rejections": D(), "receipt": lambda: {}, "D": D}
    cached = gd_body(source, "prepare").split("\tif _prepared and key == _preparation_key:", 1)[1].split("\tif _bank_owner", 1)[0]
    exec("def replay():\n" + gd_syntax(cached), state)
    # Failed bank submission does not acknowledge: every unchanged retry
    # returns the actual producer delta, and accepted provenance stays empty.
    assert state["replay"]()["changes"] == pending
    assert state["replay"]()["changes"] == pending and state["_published"] == {}
    applied = gd_syntax(gd_body(source, "publication_applied")).replace(" as Dictionary", "").replace("= {}", "= D()")
    exec("def applied():\n\tglobal _published,_pending_changes,_pending_sources\n" + applied, state)
    state["applied"]()
    assert state["_published"] == {"face": {"object_id": 7}} and state["_published_banks"] == {"bank": True}
    assert state["replay"]()["changes"] == {}
    # A withdrawal also remains owed until the actual bank confirms it.
    withdraw = gd_syntax(gd_body(source, "withdraw")).replace("= {}", "= D()")
    exec("def withdraw(reason):\n\tglobal _pending_sources,_pending_changes,_prepared\n" + withdraw, state)
    assert state["withdraw"]("owner_lost") == {"removals": ["face"], "bank_removals": ["bank"]}
    assert state["_published"] and state["_published_banks"]
    state["applied"]()
    assert state["_published"] == {} and state["_published_banks"] == {}
    frame = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    assert frame.index('_bank.apply_changes("figures"') < frame.index("_figures.publication_applied()")


@pytest.fixture(scope="module")
def figure(tmp_path_factory):
    source = (FEATURES / "true_ff_figure_binding.glslinc").read_text()
    marks = (ROOT / "godot_project/render/ascii/composition/true_ff_face_marks.glslinc").read_text()
    production = "\n".join(function(marks, name) for name in ("tff_face_toward", "tff_face_tier_fits"))
    production += "\n" + "\n".join(function(source, name) for name in
        ("tff_bound_figure", "tff_figure_vector", "tff_figure_binding_valid", "tff_figure_project", "tff_figure_select"))
    production = re.sub(r"\bout (\w+) (\w+)", r"\1& \2", production)
    shim = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <vector>
using uint=uint32_t;using std::max;
struct vec2{float x,y;vec2(float a=0):x(a),y(a){}vec2(float a,float b):x(a),y(b){}};
struct vec3{float x,y,z;vec3(float a=0):x(a),y(a),z(a){}vec3(float a,float b,float c):x(a),y(b),z(c){}};
struct vec4{float x,y,z,w;vec2 xy;vec3 xyz;
vec4(float a=0,float b=0,float c=0,float d=0):x(a),y(b),z(c),w(d),xy(a,b),xyz(a,b,c){}
vec4(vec3 a,float b):vec4(a.x,a.y,a.z,b){}vec4(vec2 a,float b,float c):vec4(a.x,a.y,b,c){}};
struct uvec4{uint x,y,z,w;uvec4(uint a=0,uint b=0,uint c=0,uint d=0):x(a),y(b),z(c),w(d){}};
vec2 operator/(vec2 a,float b){return vec2(a.x/b,a.y/b);}vec2 operator*(vec2 a,float b){return vec2(a.x*b,a.y*b);}
vec2 operator*(vec2 a,vec2 b){return vec2(a.x*b.x,a.y*b.y);}vec2 operator+(vec2 a,float b){return vec2(a.x+b,a.y+b);}
vec3 operator-(vec3 a,vec3 b){return vec3(a.x-b.x,a.y-b.y,a.z-b.z);}vec3 operator-(vec3 a){return vec3(-a.x,-a.y,-a.z);}
float dot(vec3 a,vec3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
struct mat4{vec3 scale=vec3(1);bool perspective=false;};
struct mat3{vec3 scale;mat3(mat4 m):scale(m.scale){}};
mat3 inverse(mat3 m){m.scale=vec3(1/m.scale.x,1/m.scale.y,1/m.scale.z);return m;}
mat3 transpose(mat3 m){return m;}
vec3 operator*(mat3 m,vec3 p){return vec3(m.scale.x*p.x,m.scale.y*p.y,m.scale.z*p.z);}
vec4 operator*(mat4 m,vec4 p){return vec4(m.scale.x*p.x,m.scale.y*p.y,m.scale.z*p.z,m.perspective?p.z:p.w);}
bool tff_source_finite(vec4 p){return std::isfinite(p.x)&&std::isfinite(p.y)&&std::isfinite(p.z)&&std::isfinite(p.w);}
vec4 uintBitsToFloat(uvec4 p){float v[4];uint u[4]={p.x,p.y,p.z,p.w};memcpy(v,u,16);return vec4(v[0],v[1],v[2],v[3]);}
float uintBitsToFloat(uint p){float v;memcpy(&v,&p,4);return v;}
uint floatBitsToUint(float p){uint v;memcpy(&v,&p,4);return v;}
struct Checked{std::vector<uint> data;uint& operator[](uint i){return data.at(i);}};
struct{Checked words;}figure_bindings;
struct TffFeatureInstance{uvec4 identity,paint;mat4 local_to_world;};
struct{vec4 eye,forward,page,native_origin;mat4 world_to_clip;}cam;
void f(uint at,float v){memcpy(&figure_bindings.words[at],&v,4);}
void anchor(uint at,float x,float y,float z,float w){f(at,x);f(at+1,y);f(at+2,z);f(at+3,w);}
'''
    main = r'''
int main(){figure_bindings.words.data.assign(52,0);auto& b=figure_bindings.words;uint at=4;
b[0]=52;b[1]=1;b[at]=1;b[at+1]=22;b[at+2]=15;b[at+3]=2;
anchor(at+4,0,0,1,0.2);anchor(at+8,-.4,-.2,.5,1);anchor(at+12,.4,-.2,.5,1);
anchor(at+16,0,.05,.5,1);anchor(at+20,0,.4,.5,1);
for(uint t=0;t<2;t++){uint base=at+24+t*12;for(uint i=0;i<4;i++)b[base+i]=64|((240+i)<<8)|(t<<16);
anchor(base+4,8,8,t+1,11);b[base+8]=t==0?11:15;}
TffFeatureInstance inst;inst.identity=uvec4(1,0,5|(1<<19),123);inst.paint=uvec4(0,5,0,1);
cam.eye=vec4(0,0,2,0);cam.forward=vec4(0,0,-1,0);cam.page=vec4(200,170,11,17);cam.native_origin=vec4(2,0,uintBitsToFloat(52u),0);
uint word,mask;assert(tff_figure_binding_valid(inst));assert(tff_figure_select(inst,word,mask)&&mask==15&&(word>>16)==1);
// Untrusted header counts can never expand the physical allocation bound.
b[0]=1000;inst.paint.y=500;assert(!tff_figure_binding_valid(inst));b[0]=52;inst.paint.y=5;
cam.native_origin.z=uintBitsToFloat(0u);assert(!tff_figure_binding_valid(inst));
cam.native_origin.z=uintBitsToFloat(52u);
// Every member observes the same whole face tier/mask, not its own size.
for(uint ordinal=1;ordinal<=4;ordinal++){inst.paint.w=ordinal;assert(tff_figure_select(inst,word,mask)&&mask==15&&(word>>16)==1);}
cam.page=vec4(200,34,11,17);inst.paint.w=1;assert(tff_figure_select(inst,word,mask)&&mask==11&&(word>>16)==0);
inst.paint.w=3;assert(!tff_figure_select(inst,word,mask));inst.paint.w=4;assert(tff_figure_select(inst,word,mask)&&mask==11);
cam.page=vec4(200,10,11,17);inst.paint.w=1;assert(!tff_figure_select(inst,word,mask));
cam.page=vec4(200,170,11,17);cam.eye=vec4(0,0,-2,0);assert(!tff_figure_select(inst,word,mask));
cam.eye=vec4(0,0,-2,1);assert(tff_figure_select(inst,word,mask)); // ortho uses direction, not eye position
cam.forward=vec4(0,0,1,0);assert(!tff_figure_select(inst,word,mask));
cam.eye=vec4(0,0,2,0);cam.forward=vec4(0,0,-1,0);cam.world_to_clip.perspective=true;assert(tff_figure_select(inst,word,mask));
cam.world_to_clip.perspective=false;inst.local_to_world.scale=vec3(2,.5,1);assert(tff_figure_select(inst,word,mask));
inst.local_to_world.scale=vec3(1);b[at+3]=3;assert(!tff_figure_binding_valid(inst));b[at+3]=2;
inst.paint.y=0;assert(!tff_figure_binding_valid(inst));inst.paint.y=5;
inst.paint.w=5;assert(!tff_figure_binding_valid(inst));inst.paint.w=1;
b[at+2]=11;assert(!tff_figure_binding_valid(inst));b[at+2]=15;
f(at+5,std::numeric_limits<float>::quiet_NaN());assert(!tff_figure_binding_valid(inst));f(at+5,0);
f(at+4,0);f(at+6,0);assert(!tff_figure_binding_valid(inst));f(at+6,1);
// Fold bindings use the same facing law and genuine stroke word, no tier.
b[at+1]=25;inst.paint.w=0;assert(tff_figure_binding_valid(inst));assert(tff_figure_select(inst,word,mask)&&word==123&&mask==0);
cam.eye=vec4(0,0,-2,0);assert(!tff_figure_select(inst,word,mask));
}
'''
    binary = tmp_path_factory.mktemp("ff-figure") / "figure"
    built = subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", str(binary)],
                           input=shim + production + main, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return binary


def test_production_descriptor_facing_tier_and_partial_face_controls(figure):
    result = subprocess.run([str(figure)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_production_figure_frame_omissions_survive_ready_machinery():
    source = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
    body = gd_body(source, "prepare_residency")
    start = body.index("\tvar figure_omissions:")
    branch = body[start:body.index('\tscene["true_ff_terrain_topology"]', start)]
    class Producer:
        def __init__(self): self.errors = D(asset="figure_live_mesh_content_mismatch")
        def receipt(self): return {"rejection_reasons": self.errors}
    producer = Producer()
    state = {"_figures": producer, "_preparation_omissions": D(), "figures": D(ready=True)}
    exec("def reconcile():\n" + gd_syntax(branch), state)
    state["reconcile"]()
    assert state["_preparation_omissions"] == {"figures.sources": {"asset": "figure_live_mesh_content_mismatch"}}
    producer.errors = D(); state["reconcile"]()
    assert state["_preparation_omissions"] == {}
    assert source.count('"figures": _figures.receipt()') == 3
    host = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
    bank = (FEATURES / "TrueFFFeatureBank.gd").read_text()
    assert 'out.encode_u32(248, int(bank.get("figure_binding_words", 0)))' in host
    assert '"figure_binding_words": _figure_binding_bytes.size() / 4' in bank


def test_body_outline_roles_have_complete_hard_recipes():
    language = json.loads(LANGUAGE.read_text())
    rows = language["run_table"]["rows"]
    generic = {(row["track"], row["flags"], row["detail"], row["sequence"])
               for row in rows if row["role"] == "generic" and row["language"] == "hard"}
    assert generic
    for role in ("body_outline", "vehicle_body"):
        authored = {(row["track"], row["flags"], row["detail"], row["sequence"])
                    for row in rows if row["role"] == role and row["language"] == "hard"}
        assert authored == generic
    bindings = {entry["material"]: entry["language"] for entry in language["material_languages"]}
    # Bird limbs plus both spider variants' seat, legs, and bodies must select
    # the hard role rows above instead of figure-kind 5's soft default.
    for material in (213, 238, 239, 246, 247, 248):
        assert bindings[material] == "hard"
