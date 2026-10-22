"""Production ordinary-representative sharing controls.

This is an offline source test.  It extracts the native grouping/merge
functions and the P5 certificate consumer from the checked-in shaders, then
compiles those exact bodies as small C++ programs.  It proves the negative
controls which keep source facts and whole-cell coverage out of an ordinary
share; it does not claim a Godot or GPU render run.
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"
NATIVE = FEATURES / "true_ff_feature_native.glsl"
LAYOUT = FEATURES / "true_ff_feature_layout.glsl"


def function(source: str, name: str, kind: str = "bool") -> str:
    """Extract one production GLSL definition, including its braces."""
    match = re.search(rf"\b{kind}\s+{name}\s*\([^;{{]*\)\s*\{{", source)
    if not match:
        raise AssertionError(f"missing production function {name}")
    start = match.start()
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unterminated production function {name}")


COMMON = r'''
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <vector>
typedef uint32_t uint;
using std::abs; using std::ceil; using std::max; using std::min;
struct ivec2 { int x,y; ivec2(int a=0,int b=0):x(a),y(b) {} };
struct ivec4 { int x,y,z,w; ivec4(int a=0,int b=0,int c=0,int d=0):x(a),y(b),z(c),w(d) {}
    int operator[](int i) const { return i==0?x:(i==1?y:(i==2?z:w)); } };
struct uvec2 { uint x,y; };
struct uvec4 { uint x,y,z,w; uvec2 xy;
    uvec4(uint a=0,uint b=0,uint c=0,uint d=0):x(a),y(b),z(c),w(d),xy{a,b} {}
    uvec4(uvec2 a,uint c,uint d):x(a.x),y(a.y),z(c),w(d),xy{a.x,a.y} {}
    uint operator[](int i) const { return i==0?x:(i==1?y:(i==2?z:w)); } };
struct vec4 { float x,y,z,w; vec4(float a=0,float b=0,float c=0,float d=0):x(a),y(b),z(c),w(d) {} };
bool equal(const uvec4&a,const uvec4&b) { return a.x==b.x&&a.y==b.y&&a.z==b.z&&a.w==b.w; }
bool equal(const uvec2&a,const uvec2&b) { return a.x==b.x&&a.y==b.y; }
bool equal(const vec4&a,const vec4&b) { return a.x==b.x&&a.y==b.y&&a.z==b.z&&a.w==b.w; }
bool all(bool value) { return value; }
bool any(bool value) { return value; }
bool notEqual(const uvec4&a,const uvec4&b) { return !equal(a,b); }
const uint TFF_CLASS_AUTHORED_STROKE=6u, TFF_ROLE_DRAPERY_FOLD=11u;
const uint TFF_NATIVE_CERTIFIED=8u, TFF_NATIVE_SHARED_MEMBER=16u;
const uint TFF_NATIVE_OCCLUDED_MEMBER=32u, TFF_NATIVE_OCCLUSION_OWNER=64u;
const uint TFF_NATIVE_CERTIFICATE_TAG=1u<<31u, TFF_FEATURE_ORIGINS=7u, TFF_NONE=0xffffffffu;
const uint TFF_OWED_IDENTITY_CLASS=0x20000000u;
bool tff_feature_figure_site(uvec4 source) { return (source.y&(1u<<23u))!=0u; }
uint tff_feature_source_kind(uvec4 source) { return tff_feature_figure_site(source)?5u:(source.z>>8u)&255u; }
bool tff_feature_is_representative(uint key) { return (key&(4u<<16u))!=0u; }
bool tff_feature_authored(uint) { return false; }
'''


NATIVE_TYPES = r'''
struct NativeSite { vec4 position; uvec4 key; vec4 tangent; vec4 source_world;
    vec4 source_normal; uvec4 source; uvec4 identity; };
struct TffNativePlacement { uvec4 key; ivec4 geometry; ivec4 costs1; };
struct TffNativeCoverage { ivec4 range; uvec4 identity; };
struct NativePlacements { TffNativePlacement items[2]; };
struct NativeCoverage { TffNativeCoverage items[2]; };
struct { uvec4 stage; uvec4 counts; vec4 page; uvec4 native; } pc;
NativeSite native_sites[2]; NativePlacements placements;
NativeCoverage native_coverage; bool prepare_ok=true;
NativeSite read_site(uint at) { return native_sites[at]; }
uint site_language(NativeSite) { return 0u; }
int select_idiom(NativeSite,float,uint) { return 3; }
bool prepare(uint site,uint) {
    if(!prepare_ok) return false;
    placements.items[site].key=uvec4(site,0,3,0);
    placements.items[site].geometry=ivec4(0,11,0,11);
    placements.items[site].costs1=ivec4(0,0,0,0);
    return true;
}
'''


def native_source() -> str:
    text = NATIVE.read_text()
    return "\n".join(function(text, name) for name in (
        "shadow_site", "ordinary_representative_site", "owed_representative_site",
        "same_representative_semantics", "same_owed_semantics", "grouped_site", "same_group"))


def compile_native(test: unittest.TestCase, body: str) -> str:
    if shutil.which("c++") is None:
        test.skipTest("no C++ compiler")
    source = COMMON + NATIVE_TYPES + native_source() + "\n" + body
    with tempfile.TemporaryDirectory(prefix="ff-ordinary-native-") as directory:
        binary = str(Path(directory) / "unit")
        result = subprocess.run(["c++", "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                                input=source, text=True, capture_output=True)
        test.assertEqual(result.returncode, 0, result.stderr[-5000:])
        run = subprocess.run([binary], capture_output=True, text=True)
        test.assertEqual(run.returncode, 0, run.stderr[-2000:])
        return run.stdout


P5_TYPES = r'''
struct FeatureSite { vec4 position; uvec4 key; vec4 tangent; vec4 source_world;
    vec4 source_normal; uvec4 source; uvec4 identity; };
struct { vec4 lattice; } pc;
bool native_certified(FeatureSite s) { return ((s.key.z>>16u)&TFF_NATIVE_CERTIFIED)!=0u; }
int tff_feature_row(float y,float) { return int(y); }
bool fold_site(FeatureSite) { return false; }
bool same_fold(FeatureSite a,FeatureSite b) { return false; }
bool native_member(FeatureSite s) { return ((s.key.z>>16u)&TFF_NATIVE_SHARED_MEMBER)!=0u; }
struct FeatureSites { std::vector<FeatureSite> items; } sites;
struct FeatureRowSites { std::vector<uint> items; } row_sites;
struct FeatureSlots { std::vector<uvec2> items; } site_slots;
const uint LANES=64u;
uint row_incomplete=0u;
void atomicOr(uint& target,uint value) { target|=value; }
void layout_sync() {}
bool site_less(uint a,uint b) { return a<b; }
'''


def p5_source() -> str:
    text = LAYOUT.read_text()
    kinds = {"certificate_group_key": "uvec4", "group_certificates": "void"}
    return "\n".join(function(text, name, kinds.get(name, "bool")) for name in (
        "shadow_site", "ordinary_representative_site", "same_representative_semantics",
        "same_certificate", "certificate_group_key", "certificate_group_less", "group_certificates"))


def compile_p5(test: unittest.TestCase, body: str) -> str:
    if shutil.which("c++") is None:
        test.skipTest("no C++ compiler")
    source = COMMON + P5_TYPES + p5_source() + "\n" + body
    with tempfile.TemporaryDirectory(prefix="ff-ordinary-p5-") as directory:
        binary = str(Path(directory) / "unit")
        result = subprocess.run(["c++", "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                                input=source, text=True, capture_output=True)
        test.assertEqual(result.returncode, 0, result.stderr[-5000:])
        run = subprocess.run([binary], capture_output=True, text=True)
        test.assertEqual(run.returncode, 0, run.stderr[-2000:])
        return run.stdout


NATIVE_BODY = r'''
NativeSite base() {
    NativeSite s; s.position=vec4(10,4,7,0);s.key=uvec4(3,0,4u<<16u,19);
    s.tangent=vec4(1,0,0,12);s.source_world=vec4(1,2,3,4);
    s.source_normal=vec4(0,1,0,0x3f800000u);s.source=uvec4(205,0,0,0);
    s.identity=uvec4(3,22,0x80000000u,0);return s;
}
void require(bool ok) { if(!ok) std::abort(); }
int main() {
    native_sites[0]=base(); native_sites[1]=base();
    require(ordinary_representative_site(native_sites[0]));
    require(grouped_site(native_sites[0]) && same_group(native_sites[0],native_sites[1]));
    native_sites[1].position.z+=1; require(!same_group(native_sites[0],native_sites[1]));
    native_sites[1]=base(); native_sites[1].source_normal.w=0x40000000u;
    require(!same_group(native_sites[0],native_sites[1]));
    native_sites[1]=base(); native_sites[1].identity.y++;native_sites[1].key.y++;
    native_sites[1].source_world.w+=10;native_sites[0].identity.w=1;native_sites[1].identity.w=2;
    require(same_group(native_sites[0],native_sites[1]));
    native_sites[1]=base(); native_sites[1].source_world.x+=1;
    require(!same_group(native_sites[0],native_sites[1]));
    native_sites[1]=base(); native_sites[1].source.x++;
    require(!same_group(native_sites[0],native_sites[1]));
    native_sites[1]=base(); native_sites[1].key.w++;
    require(!same_group(native_sites[0],native_sites[1]));
    native_sites[0]=base();native_sites[1]=base();
    native_sites[0].identity.z=0x20000000u;native_sites[1].identity.z=0x20000000u;
    require(owed_representative_site(native_sites[0]));
    require(grouped_site(native_sites[0]) && same_group(native_sites[0],native_sites[1]));
    native_sites[1].position.x+=1;require(!same_group(native_sites[0],native_sites[1]));
}
'''


P5_BODY = r'''
FeatureSite base() {
    FeatureSite s; s.position=vec4(10,4,7,0);s.key=uvec4(3,3,(4u|8u)<<16u,19);
    s.tangent=vec4(1,0,5,12);s.source_world=vec4(1,2,3,4);
    s.source_normal=vec4(0,1,0,0x3f800000u);s.source=uvec4(205,0,0,0);
    s.identity=uvec4(3,22,0x80000000u,1);return s;
}
void require(bool ok) { if(!ok) std::abort(); }
int main() {
    FeatureSite owner=base(),member=base();member.key.z|=TFF_NATIVE_SHARED_MEMBER<<16u;
    member.identity.y++;member.source_world.w+=10;
    require(same_certificate(owner,member));
    require(equal(certificate_group_key(owner),certificate_group_key(member)));
    member.position.z+=1; require(!same_certificate(owner,member));
    member=base();member.source_normal.w=0x40000000u; require(!same_certificate(owner,member));
    member=base();member.identity.x++; require(!same_certificate(owner,member));
    member=base();member.source_world.x+=1; require(!same_certificate(owner,member));
    member=base();member.source.x++; require(!same_certificate(owner,member));
    member=base();member.identity.w=2; require(!same_certificate(owner,member));
    member=base();member.key.y++;require(!same_certificate(owner,member));
    owner=base();owner.identity.y=100;
    member=base();member.key.z|=TFF_NATIVE_SHARED_MEMBER<<16u;
    member.identity.y=1;member.source_world.w+=10;
    sites.items={member,owner};row_sites.items={1u,0u};site_slots.items={{0u,0u},{0u,1u}};
    require(certificate_group_less(1u,0u,0));
    row_incomplete=0u;group_certificates(0u,2u,0u);
    require(row_incomplete==0u && site_slots.items[0].y==1u);
}
'''


class OrdinarySharing(unittest.TestCase):
    def test_native_group_and_whole_cell_merge_controls(self):
        compile_native(self, NATIVE_BODY)
        source = NATIVE.read_text()
        merge = function(source, "merge_coverage")
        self.assertIn("if(!same_group(a,b)) return false;", merge)
        self.assertIn("required.range.x=min(required.range.x,other.range.x);", merge)
        self.assertIn("required.range.y=max(required.range.y,other.range.y);", merge)
        self.assertIn("retained_coverage_origins(primary,required)", merge)
        self.assertIn("if((primary_origins|compact_origins)==0u) return false;", merge)

    def test_p5_consumes_the_same_source_semantics_certificate(self):
        compile_p5(self, P5_BODY)


if __name__ == "__main__":
    unittest.main()
