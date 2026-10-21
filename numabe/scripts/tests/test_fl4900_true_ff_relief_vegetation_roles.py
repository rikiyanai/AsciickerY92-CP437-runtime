"""FL-4900 phase 2 lane R: terrain relief, vegetation and overlap-break roles.

Compiles the production role producers true_ff_role_relief.glslinc and
true_ff_role_vegetation.glslinc (with the shared role ids, the StructuralCell
layout and accessors of true_ff_frame_params.glslinc, and
true_ff_contour_geometry.glslinc for the occlusion predicate) as C++, and
checks:
- each relief rule (skyline, ridge, gully; terrain only);
- each vegetation rule (canopy, tuft; trunk versus branch by world direction;
  material 240 is trunk or branch only with the support's tree flag, geo.y
  TFF_SUPPORT_TREE (FL-4902 lane TB), and stays generic on a fixture);
- camera independence: changing every camera-dependent support field (range,
  anchor, paint address, object slot/extent, sampled face, per-cell hit
  flags, endpoint order) never changes a relief or vegetation role; the tree
  flag is a world fact of the source face and is kept;
- the overlap break: a farther contour yields within the authored reach of a
  nearer unconnected surface, the nearer (foreground) contour is never
  erased, a continuous surface of the same entity and the sky never break a
  site, and the break stops at the authored reach.

The scene model for the overlap break stands in for the G-buffer: page discs
with a range and an entity, tested with the production tff_ink_behind
predicate as the includer's ink occlusion test uses it. No Godot, GPU,
capture, replay or proof infrastructure: this establishes the arithmetic of
these helpers only, not renderer behaviour or visible depiction.
"""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
COMPOSITION = PROJECT / "render/ascii/composition"
PARAMS = PROJECT / "render/ascii/structure/true_ff_frame_params.glslinc"


def params_scalar() -> str:
    """StructuralCell, the contour-kind constants and the scalar accessors.

    The rest of the file declares the per-frame uniform block, which is not
    C++; everything taken here is production text.
    """
    text = PARAMS.read_text()
    start = text.index("struct StructuralCell")
    parts = [text[start:text.index("};", start) + 2]]
    parts += re.findall(r"^const (?:uint|float) TFF_\w+ = [^;]+;", text, flags=re.M)
    for name in ("bool tff_has_source_contour(", "uint tff_material(", "uint tff_source_kind(",
                 "uint tff_support_flags("):
        begin = text.index(name)
        parts.append(text[begin:text.index("\n}\n", begin) + 3])
    return "\n".join(parts)


PRELUDE = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <random>
#include <vector>
typedef uint32_t uint;
using std::abs; using std::sqrt; using std::ceil; using std::floor;
using std::min; using std::max; using std::exp2;
float atan(float y,float x) { return std::atan2(y,x); }
struct vec4 { float x=0,y=0,z=0,w=0; };
struct uvec4 { uint x=0,y=0,z=0,w=0; };
float uintBitsToFloat(uint u) { float f;std::memcpy(&f,&u,4);return f; }
uint floatBitsToUint(float f) { uint u;std::memcpy(&u,&f,4);return u; }
'''

HARNESS = r'''
// ---- overlap scene: page discs standing in for G-buffer texels.
struct Disc { float x,y,radius,range; uint entity; };
std::vector<Disc> scene;
// The includer's ink occlusion test: the surface under the point
// (nearest disc covering it; none is sky) against the site's own range.
bool tff_overlap_nearer(float x,float y,StructuralCell owner) {
    int best=-1;
    for(int i=0;i<int(scene.size());++i) {
        float dx=x-scene[i].x,dy=y-scene[i].y;
        if(dx*dx+dy*dy>scene[i].radius*scene[i].radius) continue;
        if(best<0 || scene[i].range<scene[best].range) best=i;
    }
    if(best<0) return false;
    return tff_ink_behind(scene[best].range,owner.geo.x,scene[best].entity==owner.region.x,TFF_RANGE_GATE);
}
StructuralCell support(uint source_kind,uint material,uint kind,float relief,
        float ax,float ay,float az,float bx,float by,float bz,uint entity) {
    StructuralCell c;
    c.meta.x=kind;
    c.meta.y=TFF_FLAG_VALID|(material<<TFF_MATERIAL_SHIFT)|(source_kind<<TFF_SOURCE_KIND_SHIFT);
    c.world.w=relief;
    c.contour_a.x=floatBitsToUint(ax);c.contour_a.y=floatBitsToUint(ay);c.contour_a.z=floatBitsToUint(az);
    c.contour_b.x=floatBitsToUint(bx);c.contour_b.y=floatBitsToUint(by);c.contour_b.z=floatBitsToUint(bz);
    c.contour_a.w=0x1234567u;c.contour_b.w=5u;
    c.region.x=entity;c.region.y=material;c.region.z=3u;c.region.w=2u;
    c.geo.x=50.0f;
    return c;
}
// Every camera-dependent lane of a support changes; world facts stay.
StructuralCell recamera(StructuralCell c,std::mt19937& rng) {
    std::uniform_real_distribution<float> r(0.1f,900.0f);
    std::uniform_int_distribution<uint> u(0u,0xffffffu);
    // geo.y is the source face's tree flag (world fact, FL-4902 lane TB): kept.
    c.geo.x=r(rng);c.geo.z=r(rng);c.geo.w=r(rng);
    c.tangent.x=r(rng);c.tangent.y=r(rng);c.tangent.z=r(rng);c.tangent.w=r(rng);
    c.world.x=r(rng);c.world.y=r(rng);c.world.z=r(rng);
    c.meta.y=(c.meta.y&~(TFF_FLAG_ANY_HIT|TFF_FLAG_ANY_MISS|TFF_FLAG_CENTER_MISS))
        |(u(rng)&(TFF_FLAG_ANY_HIT|TFF_FLAG_ANY_MISS));
    c.meta.z=u(rng);c.meta.w=0u;
    c.region.z=u(rng)+1u;
    if(u(rng)&1u) { uvec4 t=c.contour_a;c.contour_a.x=c.contour_b.x;c.contour_a.y=c.contour_b.y;
        c.contour_a.z=c.contour_b.z;c.contour_b.x=t.x;c.contour_b.y=t.y;c.contour_b.z=t.z; }
    return c;
}
'''


def run_unit(body: str) -> str:
    compiler = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    assert compiler, "C++ compiler required"
    source = (PRELUDE + params_scalar() + "\n"
              + (COMPOSITION / "true_ff_contour_geometry.glslinc").read_text() + "\n"
              + (COMPOSITION / "true_ff_roles.glslinc").read_text() + "\n"
              + (COMPOSITION / "true_ff_role_relief.glslinc").read_text() + "\n"
              + (COMPOSITION / "true_ff_role_vegetation.glslinc").read_text() + "\n"
              + HARNESS + body)
    with tempfile.TemporaryDirectory(prefix="ff-role-relief-") as directory:
        binary = str(Path(directory) / "unit")
        built = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                               input=source, text=True, capture_output=True)
        assert built.returncode == 0, built.stderr[-4000:]
        return subprocess.check_output([binary], text=True)


class ReliefRoles(unittest.TestCase):
    def test_relief_rule_table(self):
        out = run_unit(r'''
int main() {
    const uint T=TFF_RELIEF_SOURCE_TERRAIN;
    // Terrain: silhouette is always skyline (convex by construction).
    assert(tff_relief_role_of(T,TFF_KIND_SILHOUETTE,0.0f)==TFF_ROLE_SKYLINE);
    assert(tff_relief_role_of(T,TFF_KIND_SILHOUETTE,1.0f)==TFF_ROLE_SKYLINE);
    // Boundary: against sky it is the skyline; a material-family seam is not relief.
    assert(tff_relief_role_of(T,TFF_KIND_BOUNDARY,1.0f)==TFF_ROLE_SKYLINE);
    assert(tff_relief_role_of(T,TFF_KIND_BOUNDARY,0.0f)==TFF_ROLE_GENERIC);
    // Crease: the world dihedral sign decides ridge versus gully.
    assert(tff_relief_role_of(T,TFF_KIND_CREASE,1.0f)==TFF_ROLE_RIDGE);
    assert(tff_relief_role_of(T,TFF_KIND_CREASE,-1.0f)==TFF_ROLE_GULLY);
    assert(tff_relief_role_of(T,TFF_KIND_CREASE,0.0f)==TFF_ROLE_GENERIC);
    // Lighting and empty kinds are never relief.
    assert(tff_relief_role_of(T,TFF_KIND_SHADOW,1.0f)==TFF_ROLE_GENERIC);
    assert(tff_relief_role_of(T,TFF_KIND_NONE,1.0f)==TFF_ROLE_GENERIC);
    // Only terrain carries relief: static (rocks, fixtures), water, movers.
    for(uint source=0u;source<8u;++source) if(source!=T)
        for(uint kind=0u;kind<=TFF_KIND_SHADOW;++kind)
            for(float sign=-1.0f;sign<=1.0f;sign+=1.0f)
                assert(tff_relief_role_of(source,kind,sign)==TFF_ROLE_GENERIC);
    // The StructuralCell entry reads the same facts; no source edge: generic.
    StructuralCell ridge=support(T,7u,TFF_KIND_CREASE,1.0f,0,0,0,1,0.2f,0,42u);
    assert(tff_role_relief(ridge)==TFF_ROLE_RIDGE);
    StructuralCell gully=support(T,7u,TFF_KIND_CREASE,-1.0f,0,0,0,1,0.2f,0,42u);
    assert(tff_role_relief(gully)==TFF_ROLE_GULLY);
    StructuralCell bare=ridge;bare.contour_a.w=0u;
    assert(tff_role_relief(bare)==TFF_ROLE_GENERIC);
    std::printf("relief rules ok\n");
}
''')
        self.assertIn("relief rules ok", out)


class VegetationRoles(unittest.TestCase):
    def test_vegetation_rule_table(self):
        out = run_unit(r'''
int main() {
    const uint S=TFF_VEG_SOURCE_STATIC;
    const uint kinds[3]={TFF_KIND_SILHOUETTE,TFF_KIND_BOUNDARY,TFF_KIND_CREASE};
    for(int k=0;k<3;++k) {
        for(uint m=241u;m<=243u;++m) assert(tff_vegetation_role_of(S,m,kinds[k],false,0,1,0)==TFF_ROLE_CANOPY);
        assert(tff_vegetation_role_of(S,244u,kinds[k],false,0,1,0)==TFF_ROLE_TUFT);
        assert(tff_vegetation_role_of(S,245u,kinds[k],false,0,1,0)==TFF_ROLE_TUFT);
        assert(tff_vegetation_role_of(S,255u,kinds[k],false,0,1,0)==TFF_ROLE_TUFT);
        // Retained tree bark: world direction within 30 degrees of vertical is trunk.
        assert(tff_vegetation_role_of(S,240u,kinds[k],true,0,1,0)==TFF_ROLE_TRUNK);
        assert(tff_vegetation_role_of(S,240u,kinds[k],true,0,-3,0)==TFF_ROLE_TRUNK);
        assert(tff_vegetation_role_of(S,240u,kinds[k],true,0.45f,1,0.2f)==TFF_ROLE_TRUNK);
        assert(tff_vegetation_role_of(S,240u,kinds[k],true,1,1,0)==TFF_ROLE_BRANCH);
        assert(tff_vegetation_role_of(S,240u,kinds[k],true,1,0,0)==TFF_ROLE_BRANCH);
        assert(tff_vegetation_role_of(S,240u,kinds[k],true,0,0,0)==TFF_ROLE_GENERIC);
        // 30 degrees from vertical is the boundary; just past it is branch.
        float t=std::tan(30.0f*3.14159265f/180.0f);
        assert(tff_vegetation_role_of(S,240u,kinds[k],true,t*0.999f,1,0)==TFF_ROLE_TRUNK);
        assert(tff_vegetation_role_of(S,240u,kinds[k],true,t*1.001f,1,0)==TFF_ROLE_BRANCH);
        // Material 240 without the tree fact (fixtures share it): generic.
        assert(tff_vegetation_role_of(S,240u,kinds[k],false,0,1,0)==TFF_ROLE_GENERIC);
        // Other materials and other sources are not vegetation.
        assert(tff_vegetation_role_of(S,7u,kinds[k],true,0,1,0)==TFF_ROLE_GENERIC);
        for(uint source=0u;source<8u;++source) if(source!=S)
            for(uint m=240u;m<=255u;++m)
                assert(tff_vegetation_role_of(source,m,kinds[k],true,0,1,0)==TFF_ROLE_GENERIC);
    }
    // Shadow chords and empty kinds are lighting, never vegetation.
    for(uint m=240u;m<=255u;++m) {
        assert(tff_vegetation_role_of(S,m,TFF_KIND_SHADOW,true,0,1,0)==TFF_ROLE_GENERIC);
        assert(tff_vegetation_role_of(S,m,TFF_KIND_NONE,true,0,1,0)==TFF_ROLE_GENERIC);
    }
    // StructuralCell entry: the source face's material (region.y), not the anchor's.
    StructuralCell leaf=support(S,242u,TFF_KIND_SILHOUETTE,0,0,0,0,0,1,0,9u);
    assert(tff_role_vegetation(leaf)==TFF_ROLE_CANOPY);
    leaf.meta.y=(leaf.meta.y&~(TFF_MATERIAL_MASK<<TFF_MATERIAL_SHIFT))|(7u<<TFF_MATERIAL_SHIFT);
    assert(tff_role_vegetation(leaf)==TFF_ROLE_CANOPY);
    StructuralCell blade=support(S,255u,TFF_KIND_BOUNDARY,0,0,0,0,0.1f,1,0,9u);
    assert(tff_role_vegetation(blade)==TFF_ROLE_TUFT);
    StructuralCell bark=support(S,240u,TFF_KIND_SILHOUETTE,0,0,0,0,0,1,0,9u);
    assert(tff_role_vegetation(bark)==TFF_ROLE_GENERIC);   // fixture (no tree flag): generic
    bark.geo.y=float(TFF_SUPPORT_TREE);                    // tree flag of the source face
    assert(tff_role_vegetation(bark)==TFF_ROLE_TRUNK);
    StructuralCell limb=support(S,240u,TFF_KIND_BOUNDARY,0,0,0,0,1,0.5f,0,9u);
    limb.geo.y=float(TFF_SUPPORT_TREE);
    assert(tff_role_vegetation(limb)==TFF_ROLE_BRANCH);
    StructuralCell crown=support(S,241u,TFF_KIND_SILHOUETTE,0,0,0,0,0,1,0,9u);
    crown.geo.y=float(TFF_SUPPORT_TREE);
    assert(tff_role_vegetation(crown)==TFF_ROLE_CANOPY);   // the flag never overrides material
    StructuralCell bare=blade;bare.contour_b.w=0u;
    assert(tff_role_vegetation(bare)==TFF_ROLE_GENERIC);
    std::printf("vegetation rules ok\n");
}
''')
        self.assertIn("vegetation rules ok", out)


class CameraIndependence(unittest.TestCase):
    def test_roles_depend_on_world_facts_only(self):
        out = run_unit(r'''
int main() {
    std::mt19937 rng(4900);
    std::uniform_real_distribution<float> r(-50.0f,50.0f);
    int checked=0;
    const uint kinds[5]={TFF_KIND_NONE,TFF_KIND_SILHOUETTE,TFF_KIND_BOUNDARY,TFF_KIND_CREASE,TFF_KIND_SHADOW};
    const uint materials[9]={1u,7u,240u,241u,242u,243u,244u,245u,255u};
    for(uint source=0u;source<6u;++source)
    for(int k=0;k<5;++k)
    for(int m=0;m<9;++m)
    for(int s=-1;s<=1;++s)
    for(int trial=0;trial<6;++trial) {
        StructuralCell c=support(source,materials[m],kinds[k],float(s),
            r(rng),r(rng),r(rng),r(rng),r(rng),r(rng),77u);
        uint relief=tff_role_relief(c),vegetation=tff_role_vegetation(c);
        for(int view=0;view<8;++view) {
            StructuralCell moved=recamera(c,rng);
            assert(tff_role_relief(moved)==relief);
            assert(tff_role_vegetation(moved)==vegetation);
            ++checked;
        }
        // Trunk versus branch (tree fact supplied) ignores endpoint order.
        float dx=r(rng),dy=r(rng),dz=r(rng);
        assert(tff_vegetation_role_of(1u,240u,kinds[k],true,dx,dy,dz)
            ==tff_vegetation_role_of(1u,240u,kinds[k],true,-dx,-dy,-dz));
    }
    std::printf("camera independent %d\n",checked);
}
''')
        self.assertRegex(out, r"camera independent \d+")


class OverlapBreak(unittest.TestCase):
    def test_farther_yields_and_foreground_is_never_erased(self):
        out = run_unit(r'''
Disc disc(float x,float y,float radius,float range,uint entity) {
    Disc d;d.x=x;d.y=y;d.radius=radius;d.range=range;d.entity=entity;return d;
}
StructuralCell site(float range,uint entity) {
    StructuralCell c=support(2u,7u,TFF_KIND_CREASE,1.0f,0,0,0,1,0,0,entity);
    c.geo.x=range;return c;
}
int main() {
    // A far ridge (terrain entity 1, range 80) whose site sits at (100, 100),
    // on its own slope; a nearer tree trunk (entity 9, range 20) stands d px
    // to its right; the trunk's own contour site lies on the trunk's edge.
    for(float d=0.0f;d<=30.0f;d+=0.5f) {
        scene.clear();
        scene.push_back(disc(100,100,400,80,1u));          // far slope, own surface
        scene.push_back(disc(100+d+6,100,6,20,9u));        // trunk: left edge d px away
        bool far_break=tff_overlap_break(100,100,site(80,1u));
        bool near_break=tff_overlap_break(100+d,100,site(20,9u));
        assert(!near_break);                       // the foreground is never erased
        if(d<=TFF_OVERLAP_REACH_PX-0.5f) assert(far_break);   // farther yields in reach
        if(d>TFF_OVERLAP_REACH_PX) assert(!far_break);        // and keeps its line beyond
        assert(tff_role_overlap(100,100,site(80,1u))==(far_break?TFF_ROLE_OVERLAP_BREAK:TFF_ROLE_GENERIC));
    }
    // The yielded gap is at least one U+0020 wider than the reach of a glyph.
    assert(TFF_OVERLAP_REACH_PX>=8.0f+TFF_OVERLAP_GAP_PX);
    // Same entity: a continuous own surface (front slope 5 percent nearer)
    // never breaks the site; a nearer layer of the same terrain (a hill 40
    // percent nearer) does.
    scene.clear();
    scene.push_back(disc(100,110,12,76,1u));
    assert(!tff_overlap_break(100,100,site(80,1u)));
    scene.clear();
    scene.push_back(disc(100,110,12,48,1u));
    assert(tff_overlap_break(100,100,site(80,1u)));
    // Farther surfaces and the sky never break a site.
    scene.clear();
    scene.push_back(disc(110,100,20,200,3u));
    assert(!tff_overlap_break(100,100,site(80,1u)));
    scene.clear();
    assert(!tff_overlap_break(100,100,site(80,1u)));
    // Symmetry of depth order: of two unconnected crossing contours only
    // the farther one yields, whichever entity id is smaller.
    for(uint a=1u;a<=2u;++a) {
        uint b=3u-a;
        scene.clear();
        scene.push_back(disc(100,100,5,30,a));   // nearer form
        scene.push_back(disc(100,100,300,60,b)); // farther form behind it
        assert(!tff_overlap_break(96,100,site(30,a)));
        assert(tff_overlap_break(92,100,site(60,b)));
    }
    // No source edge: never a break.
    StructuralCell bare=site(80,1u);bare.contour_a.w=0u;
    scene.clear();scene.push_back(disc(105,100,6,20,9u));
    assert(!tff_overlap_break(100,100,bare));
    std::printf("overlap ok\n");
}
''')
        self.assertIn("overlap ok", out)


if __name__ == "__main__":
    unittest.main()
