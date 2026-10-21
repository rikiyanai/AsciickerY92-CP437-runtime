"""Offline units for True FF preparation's source-owned site arithmetic.

FL-4512 Lane A. Compiles the production GLSL helpers in
true_ff_contour_geometry.glslinc as C++ (scalar shims only) and checks the
owner rank, page clipping and site-ordinal ranges. Retired discovery-hash
tests were deleted; retained source identity is covered by the feature tests.
No Godot, GPU, capture, replay or proof infrastructure: this establishes the
arithmetic of these helpers only, not renderer behaviour or temporal quality.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

CONTOUR_GEOMETRY = ROOT / "godot_project/render/ascii/composition/true_ff_contour_geometry.glslinc"

PRELUDE = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <set>
#include <utility>
#include <vector>
typedef uint32_t uint;
using std::abs; using std::sqrt; using std::ceil; using std::floor;
using std::min; using std::max; using std::exp2;
float atan(float y,float x) { return std::atan2(y,x); }
'''


def run_unit(body: str) -> str:
    source = PRELUDE + f'#include "{CONTOUR_GEOMETRY}"\n' + body
    with tempfile.TemporaryDirectory(prefix="ff-site-owner-") as directory:
        binary = str(Path(directory) / "unit")
        subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", binary],
                       input=source, text=True, check=True, capture_output=True)
        return subprocess.check_output([binary], text=True)


class PrepareSiteOwner(unittest.TestCase):
    def test_perspective_density_bands_bound_work_without_moving_world_sites(self):
        out = run_unit(r'''
struct Sites { std::set<int> ids; int candidates=0; };
const float Q=1.f/256.f;
Sites enumerate(float n,float units,float wa,float wb,float lo,float hi,int cap) {
    Sites result;
    for(int level=0;level<=cap;++level) {
        auto band=tff_arc_level_window(n,units,wa,wb,Q,level,cap);
        if(band.lo>band.hi) continue;
        float left=max(lo,n*band.lo),right=min(hi,n*band.hi);
        if(left>right) continue;
        float spacing=tff_arc_spacing(level,Q);
        auto r=tff_arc_range(left-spacing*.0001f,right+spacing*.0001f,spacing);
        for(int k=r.first;k<r.first+r.count;++k) {
            ++result.candidates;
            float s=float(k)*spacing;
            int exact=min(tff_arc_level(tff_arc_step_at(n,units,wa,wb,s/n),Q),cap);
            if(s<lo || s>=hi || s<=0.f || exact!=level) continue;
            int ordinal=int(uint(k)<<uint(level));
            assert(result.ids.insert(ordinal).second); // thresholds never double-publish
            assert(float(ordinal)*Q==s);               // stored identity is the world point
        }
    }
    return result;
}
int main() {
    // Receding edge with the near-plane end far off page. Mean density
    // allocates >100,000 sites into a page only 1,800 pixels wide.
    float n=1000.f,wa=.05f,wb=1000.f,p=-10000000.f,q=990.f;
    float units=(q-p)/16.f;
    auto page=tff_page_interval(p,30.f,q,30.f,1800.f,80.f);
    float lo=n*page.lo*wa/(wb+(wa-wb)*page.lo),hi=n;
    int cap=tff_arc_level_within(n,Q);
    auto old=tff_arc_interior_range(lo,hi,tff_arc_spacing(tff_arc_level(n/units,Q),Q));
    assert(old.count>100000);
    Sites current=enumerate(n,units,wa,wb,lo,hi,cap);
    assert(!current.ids.empty());
    assert(current.candidates<256);  // expensive SITE work follows visible native detail
    // Brute-force the finest world lattice: no eligible site was dropped
    // by window boundaries, and no off-level site slipped in.
    std::set<int> expected;
    for(int ordinal=int(std::ceil(lo/Q));float(ordinal)*Q<hi;++ordinal) {
        float s=float(ordinal)*Q;
        int level=min(tff_arc_level(tff_arc_step_at(n,units,wa,wb,s/n),Q),cap);
        if((uint(ordinal)&((1u<<uint(level))-1u))==0u) expected.insert(ordinal);
    }
    assert(current.ids==expected);
    // Grow/shrink apparent scale: nested world identities, never a slide.
    Sites larger=enumerate(n,units*2.f,wa,wb,lo,hi,cap);
    for(int id:current.ids) assert(larger.ids.count(id));
    for(int id:larger.ids) assert(float(id)*Q>0.f && float(id)*Q<n);
    // Exact dyadic thresholds, reversed perspective depth and orthographic w.
    for(auto depths: {std::pair<float,float>{1.f,9.f},{9.f,1.f},{1.f,1.f}}) {
        auto actual=enumerate(32.f,32.f,depths.first,depths.second,0.f,32.f,12);
        std::set<int> want;
        for(int id=1;id<8192;++id) {
            int level=min(tff_arc_level(tff_arc_step_at(32.f,32.f,depths.first,depths.second,
                float(id)*Q/32.f),Q),12);
            if((uint(id)&((1u<<uint(level))-1u))==0u) want.insert(id);
        }
        assert(actual.ids==want);
    }
    // Near-equal w may straddle a level threshold: do not turn it into an
    // approximate orthographic branch that silently drops the other level.
    for(float wb2: {1.000000119f,0.99999988f,2.f,16.f,256.f}) {
        for(float density: {4.f,31.75f,32.f,64.f,257.f}) {
            auto actual=enumerate(32.f,density,1.f,wb2,3.f,29.f,12);
            std::set<int> want;
            for(int id=768;id<7424;++id) {
                int level=min(tff_arc_level(tff_arc_step_at(32.f,density,1.f,wb2,
                    float(id)*Q/32.f),Q),12);
                if((uint(id)&((1u<<uint(level))-1u))==0u) want.insert(id);
            }
            assert(actual.ids==want);
        }
    }
    std::cout<<"perspective density ok\n";
}
''')
        self.assertEqual(out, "perspective density ok\n")

    def test_owner_rank_prefers_material_then_face(self):
        out = run_unit(r'''
int main() {
    assert(tff_edge_rank(1,900)<tff_edge_rank(2,1));   // material first
    assert(tff_edge_rank(3,4)<tff_edge_rank(3,5));      // then sampled face
    assert(tff_edge_rank(999,1)==tff_edge_rank(255,1)); // clamped, still ordered
    assert(tff_edge_rank(0,0x2000000u)==tff_edge_rank(0,0xffffffu));
    // Complement + atomicMax elects the lowest rank; a cleared slot (0) is
    // below every complemented rank.
    uint best=0u;
    for(uint r: {tff_edge_rank(4,9),tff_edge_rank(2,7),tff_edge_rank(2,3)}) best=max(best,~r);
    assert(~best==tff_edge_rank(2,3));
    std::cout<<"rank ok\n";
}
''')
        self.assertEqual(out, "rank ok\n")

    def test_page_interval_clips_whole_edge_to_page(self):
        out = run_unit(r'''
bool near(float a,float b) { return std::fabs(a-b)<1e-5f; }
int main() {
    TFFInterval inside=tff_page_interval(10,10,90,50,100,60);
    assert(inside.lo==0 && inside.hi==1);
    // A horizontal edge from x=-100 to x=300 on a 100 px page: [0.25, 0.5].
    TFFInterval wide=tff_page_interval(-100,30,300,30,100,60);
    assert(near(wide.lo,0.25f) && near(wide.hi,0.5f));
    // Reversed direction gives the mirrored interval.
    TFFInterval back=tff_page_interval(300,30,-100,30,100,60);
    assert(near(back.lo,0.5f) && near(back.hi,0.75f));
    // Entirely above the page: empty.
    TFFInterval above=tff_page_interval(10,-20,90,-5,100,60);
    assert(above.lo>above.hi);
    // Degenerate direction on a page line stays inside.
    TFFInterval vertical=tff_page_interval(50,-60,50,120,100,60);
    assert(near(vertical.lo,1.f/3.f) && near(vertical.hi,2.f/3.f));
    std::cout<<"page ok\n";
}
''')
        self.assertEqual(out, "page ok\n")

    def test_site_range_enumerates_every_site_once(self):
        out = run_unit(r'''
int main() {
    // Sites at 5.5 + 11k along a 100 px edge; the whole edge is 0..100.
    TFFSiteRange all=tff_edge_site_range(5.5f,11.f,0.f,100.f);
    assert(all.first==0 && all.count==9);             // 5.5 .. 93.5
    // Splitting the edge's visible interval at any point partitions the
    // same ordinals: no site is lost and none is enumerated twice.
    for(int cut=0;cut<=100;++cut) {
        TFFSiteRange a=tff_edge_site_range(5.5f,11.f,0.f,float(cut));
        TFFSiteRange b=tff_edge_site_range(5.5f,11.f,float(cut)+1e-4f,100.f);
        assert(a.count+b.count==all.count);
        if(a.count>0 && b.count>0) assert(b.first==a.first+a.count);
    }
    // A page window inside the edge keeps the whole-edge ordinals (phase
    // does not restart at the page border).
    TFFSiteRange window=tff_edge_site_range(5.5f,11.f,40.f,70.f);
    assert(window.first==4 && window.count==2);       // 49.5, 60.5; 38.5 and 71.5 outside
    TFFSiteRange none=tff_edge_site_range(5.5f,11.f,6.f,16.f);
    assert(none.count==0);
    std::cout<<"sites ok\n";
}
''')
        self.assertEqual(out, "sites ok\n")

    def test_ink_occlusion_tolerates_own_surface_layer_only(self):
        out = run_unit(r'''
int main() {
    const float gate=0.15f;                           // TFF_RANGE_GATE
    // A different entity 5 percent nearer than the edge hides the ink.
    assert(tff_ink_behind(95.f,100.f,false,gate));
    // Within the 3 percent tolerance it does not.
    assert(!tff_ink_behind(98.f,100.f,false,gate));
    // The edge's own entity (a ridge's front slope) may be nearer by one
    // depth layer of one surface ...
    assert(!tff_ink_behind(90.f,100.f,true,gate));
    // ... but a nearer layer of the same entity (a hill in front) hides it.
    assert(tff_ink_behind(80.f,100.f,true,gate));
    // Surfaces behind the edge never hide it; near the camera the absolute
    // floor applies.
    assert(!tff_ink_behind(120.f,100.f,false,gate));
    assert(!tff_ink_behind(0.19f,0.2f,false,gate));
    assert(tff_ink_behind(0.17f,0.2f,false,gate));
    std::cout<<"occlusion ok\n";
}
''')
        self.assertEqual(out, "occlusion ok\n")

    def test_site_units_and_lattice(self):
        out = run_unit(r'''
int main() {
    assert(tff_site_units(32.f,-5.f,16.f,17.f)==2.f);   // mostly horizontal: advances
    assert(tff_site_units(-3.f,34.f,16.f,17.f)==2.f);   // mostly vertical: rows
    TFFSiteRange r=tff_arc_range(-1.2f,2.6f,1.f);        // s = -1, 0, 1, 2
    assert(r.first==-1 && r.count==4);
    TFFSiteRange half=tff_arc_range(1.f,2.f,1.f);        // half-open: 1 only
    assert(half.first==1 && half.count==1);
    assert(tff_arc_range(1.1f,1.9f,1.f).count==0);
    // A lone edge's sites exclude endpoint a (ordinal 0) and endpoint b.
    TFFSiteRange inner=tff_arc_interior_range(0.f,3.f,1.f); // s = 1, 2
    assert(inner.first==1 && inner.count==2);
    assert(tff_arc_interior_range(0.f,1.f,1.f).count==0);
    assert(tff_arc_interior_range(1.5f,3.f,1.f).first==2);
    // Coarsest level still below a world length (-1: none).
    const float q=1.f/256.f;
    assert(tff_arc_level_within(q,q)==-1);
    assert(tff_arc_level_within(3.f*q,q)==1);           // 2q < 3q <= 4q
    assert(tff_arc_level_within(4.f*q,q)==1);
    std::cout<<"lattice ok\n";
}
''')
        self.assertEqual(out, "lattice ok\n")

    def test_chain_places_partition_one_world_owned_lattice(self):
        # Each edge of a skyline places itself from its own endpoint order and
        # the shared origin vertex (tff_chain_place). The edges' half-open
        # site ranges must partition one lattice anchored at the origin, for
        # any mix of edge orientations, for an open chain and for a loop.
        out = run_unit(r'''
#include <map>
#include <cstdlib>
std::map<int,int> own(const std::vector<float>& U,const std::vector<int>& flip,int m,
        float loop,std::vector<float>& lo_hi) {
    std::map<int,int> seen;
    for(size_t i=0;i+1<U.size();++i) {
        float n=U[i+1]-U[i];
        // Endpoint a is vertex i (x grows with U) unless the edge is flipped.
        bool f=flip[i]!=0;
        float x_m=f?U[i+1]-U[m]:U[m]-U[i];
        int sign=f?-1:1;                     // canonical direction: growing U
        TFFChainPlace place=tff_chain_place(x_m,n,sign,loop);
        // Canonical start must be vertex i, whatever the endpoint order.
        assert(place.reversed==f);
        float lo=place.u0,hi=place.u0+n;
        float want=U[i]-U[m];
        if(loop>0) want-=loop*std::floor(want/loop);
        assert(std::fabs(lo-want)<1e-4f);
        TFFSiteRange r=tff_arc_range(std::max(lo,lo_hi[0]),std::min(hi,lo_hi[1]),0.3713f);
        for(int k=r.first;k<r.first+r.count;++k) seen[k]++;
        if(loop>0 && hi>loop) {
            TFFSiteRange w=tff_arc_interior_range(0.f,hi-loop,0.3713f); // as plan_edge
            for(int k=w.first;k<w.first+w.count;++k) seen[k]++;
        }
    }
    return seen;
}
int main() {
    std::srand(20260928);
    for(int trial=0;trial<200;++trial) {
        int edges=2+std::rand()%12;
        std::vector<float> U(1,0.f);
        for(int i=0;i<edges;++i) U.push_back(U.back()+0.05f+float(std::rand()%400)/100.f);
        std::vector<int> flip(edges);
        for(int& f:flip) f=std::rand()%2;
        int m=std::rand()%(edges+1);          // origin: any vertex of the chain
        std::vector<float> bounds={U.front()-U[m],U.back()-U[m]};
        auto seen=own(U,flip,m,0.f,bounds);
        TFFSiteRange all=tff_arc_range(bounds[0],bounds[1],0.3713f);
        assert(int(seen.size())==all.count);
        for(auto& kv:seen) {
            assert(kv.second==1);             // never two edges on one site
            assert(kv.first>=all.first && kv.first<all.first+all.count);
        }
        // Loop: the last vertex closes on the first; the origin is any vertex.
        if(m==edges) continue;
        float L=U.back();
        std::vector<float> loop_bounds={0.f,L};
        auto ring=own(U,flip,m,L,loop_bounds);
        TFFSiteRange around=tff_arc_range(0.f,L,0.3713f);
        assert(int(ring.size())==around.count);
        for(auto& kv:ring) assert(kv.second==1);
    }
    std::cout<<"chain ok\n";
}
''')
        self.assertEqual(out, "chain ok\n")

    def test_world_arc_lattice_is_nested_and_never_slides(self):
        out = run_unit(r'''
int main() {
    const float q=1.f/256.f;
    // Least level whose spacing covers one native step.
    assert(tff_arc_level(q*0.5f,q)==0);
    assert(tff_arc_level(q*3.f,q)==2);                  // 4 quanta >= 3
    assert(tff_arc_spacing(2,q)==4.f*q);
    // A camera move changes only the level, never a point's arc position:
    // every level-(L+1) point is a level-L point with the same s / quantum.
    for(int level=0;level<8;++level) {
        float coarse=tff_arc_spacing(level+1,q),fine=tff_arc_spacing(level,q);
        TFFSiteRange c=tff_arc_range(-3.f,5.f,coarse),f=tff_arc_range(-3.f,5.f,fine);
        std::set<long> fine_ids;
        for(int j=f.first;j<f.first+f.count;++j) fine_ids.insert(long(j)<<level);
        for(int j=c.first;j<c.first+c.count;++j) assert(fine_ids.count(long(j)<<(level+1)));
        assert(f.count>=2*c.count-1);
    }
    // Half-open ranges of consecutive edges partition the chain's points.
    float s=0.f,spacing=tff_arc_spacing(3,q);
    int total=0;
    float cuts[5]={0.f,0.37f,0.5f,1.9f,2.25f};
    for(int i=0;i+1<5;++i) total+=tff_arc_range(cuts[i],cuts[i+1],spacing).count;
    assert(total==tff_arc_range(0.f,2.25f,spacing).count);
    (void)s;
    std::cout<<"arc ok\n";
}
''')
        self.assertEqual(out, "arc ok\n")

    def test_every_edge_keeps_world_sites_under_camera_motion(self):
        # FL-4512 shimmer (SHIMMER_SITE_ID_SURFACE_KEY_20260928): the lone-edge
        # uniform-density lattice helpers over a camera sequence (dolly in and out, orbit,
        # tilt) and a rigid mover. Per frame: level from the visible edge's
        # projected density (tff_site_units, tff_arc_level, capped by
        # tff_arc_level_within), sites from tff_arc_interior_range, world point
        # = a + (b - a) * s / n, id = (j << level) | 0x40000000. Checks: an id
        # always names one world point (object frame for the mover); from one
        # frame to the next the ids are equal, or a level change of one adds
        # or removes exactly every other site; spacing lies in [1, 2) native
        # steps of the edge's mean projected density. This retains coverage
        # of the uniform special case; the perspective-density-band unit
        # above covers the current per-point density and its enumeration.
        out = run_unit(r"""
#include <map>
struct V3 { float x,y,z; };
V3 operator+(V3 a,V3 b){return {a.x+b.x,a.y+b.y,a.z+b.z};}
V3 operator-(V3 a,V3 b){return {a.x-b.x,a.y-b.y,a.z-b.z};}
V3 operator*(V3 a,float s){return {a.x*s,a.y*s,a.z*s};}
float dot3(V3 a,V3 b){return a.x*b.x+a.y*b.y+a.z*b.z;}
float len3(V3 a){return std::sqrt(dot3(a,a));}
V3 cross3(V3 a,V3 b){return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x};}
V3 unit3(V3 a){return a*(1.f/len3(a));}
struct Camera { V3 eye,forward,right,up; float focal; };
Camera look(V3 eye,V3 at,float focal) {
    Camera c;c.eye=eye;c.forward=unit3(at-eye);
    c.right=unit3(cross3(c.forward,V3{0,1,0}));c.up=cross3(c.right,c.forward);c.focal=focal;
    return c;
}
bool project(const Camera& c,V3 p,float& x,float& y) {
    V3 d=p-c.eye;float z=dot3(d,c.forward);
    if(z<=0.05f) return false;
    x=800.f+c.focal*dot3(d,c.right)/z;y=500.f-c.focal*dot3(d,c.up)/z;
    return true;
}
const float Q=1.f/256.f,ADVANCE=16.f,PITCH=17.f;
struct Frame { int level; float spacing,step; std::map<uint,V3> sites; };
// One lone edge, one frame; endpoints in the order they are delivered.
Frame lay(const Camera& c,V3 a,V3 b) {
    Frame f;f.level=-1;
    float px,py,qx,qy;
    bool ok=project(c,a,px,py)&&project(c,b,qx,qy);
    assert(ok);
    float n=len3(b-a);
    f.step=n/std::max(tff_site_units(qx-px,qy-py,ADVANCE,PITCH),1e-6f);
    f.level=std::min(tff_arc_level(f.step,Q),tff_arc_level_within(n,Q));
    assert(f.level>=0);
    f.spacing=tff_arc_spacing(f.level,Q);
    TFFSiteRange r=tff_arc_interior_range(0.f,n,f.spacing);
    for(int j=r.first;j<r.first+r.count;++j) {
        float s=float(j)*f.spacing;
        assert(s>0.f && s<n);
        uint id=((uint(j)<<uint(f.level))&0x3fffffffu)|0x40000000u;
        f.sites[id]=a+(b-a)*(s/n);
    }
    return f;
}
void compare(const Frame& p,const Frame& c,std::map<uint,V3>& world,
        V3 origin_p,V3 origin_c,int& level_changes) {
    for(auto& kv:c.sites) {
        V3 local=kv.second-origin_c;
        auto seen=world.find(kv.first);
        if(seen==world.end()) world[kv.first]=local;
        else assert(len3(seen->second-local)<1e-4f);   // one id, one point
    }
    if(p.level==c.level) {
        assert(p.sites.size()==c.sites.size());
        for(auto& kv:p.sites) assert(c.sites.count(kv.first));
        return;
    }
    // One level step: the coarse set is the fine set's every other site.
    assert(std::abs(p.level-c.level)==1);
    ++level_changes;
    const Frame& fine=p.level<c.level?p:c;
    const Frame& coarse=p.level<c.level?c:p;
    int kept=0;
    for(auto& kv:coarse.sites) { assert(fine.sites.count(kv.first));++kept; }
    for(auto& kv:fine.sites) {
        uint j=(kv.first&0x3fffffffu)>>uint(fine.level);
        assert((coarse.sites.count(kv.first)!=0)==(j%2u==0u));
    }
    assert(kept==int(coarse.sites.size()));
    (void)origin_p;
}
void spacing_ok(const Frame& f,float n) {
    // [1, 2) native steps, unless the world-length cap left a single site.
    if(f.spacing<n) assert(f.sites.size()==1 || (f.spacing>=f.step && f.spacing<2.f*f.step));
    if(f.sites.size()>1) assert(f.spacing>=f.step && f.spacing<2.f*f.step);
}
int main() {
    V3 a{-3.f,1.f,2.f},b{5.f,1.5f,-1.f};            // world-fixed edge
    float n=len3(b-a);
    std::map<uint,V3> world;
    Frame previous;bool first=true;int changes=0,steps=0;
    for(int i=0;i<=600;++i) {
        float t=float(i)/600.f;
        float angle=6.2831853f*t,distance=6.f+40.f*(0.5f-0.5f*std::cos(6.2831853f*2.f*t));
        V3 eye{distance*std::cos(angle),2.f+6.f*t,distance*std::sin(angle)};
        Frame f=lay(look(eye,V3{0,0,0},900.f),a,b);
        spacing_ok(f,n);
        if(!first) compare(previous,f,world,V3{0,0,0},V3{0,0,0},changes);
        previous=f;first=false;++steps;
    }
    assert(changes>0);                                 // the sequence crosses levels
    // A rigid mover: endpoints delivered as current world positions, a first.
    std::map<uint,V3> object;
    first=true;int mover_changes=0;
    V3 la{-1.f,0.f,0.f},lb{1.2f,0.4f,0.3f};
    for(int i=0;i<=400;++i) {
        float t=float(i)/400.f,yaw=3.f*t;
        V3 at{20.f*t-10.f,0.5f,-4.f+30.f*t};
        auto place=[&](V3 l){ return V3{at.x+l.x*std::cos(yaw)-l.z*std::sin(yaw),at.y+l.y,
            at.z+l.x*std::sin(yaw)+l.z*std::cos(yaw)}; };
        V3 wa=place(la),wb=place(lb);
        Frame f=lay(look(V3{0.f,6.f,-30.f},at,900.f),wa,wb);
        // Measure each site in the object's frame: rotate back about its origin.
        Frame local=f;
        for(auto& kv:local.sites) {
            V3 d=kv.second-at;
            kv.second=V3{d.x*std::cos(yaw)+d.z*std::sin(yaw),d.y,-d.x*std::sin(yaw)+d.z*std::cos(yaw)};
        }
        spacing_ok(f,len3(wb-wa));
        if(!first) compare(previous,local,object,V3{0,0,0},V3{0,0,0},mover_changes);
        previous=local;first=false;
    }
    assert(mover_changes>0);
    std::cout<<"world sites ok "<<steps<<"\n";
}
""")
        self.assertEqual(out, "world sites ok 601\n")

    def test_joint_shape_reads_the_complete_branch_symmetrically(self):
        out = run_unit(r'''
bool near(float a,float b) { return std::fabs(a-b)<1e-4f; }
int main() {
    // An upper-left corner: an arm down and an arm right from (10, 10).
    // A long horizontal arm must not tilt the chord: the shape is that of
    // two equal arms with the complete branch length (40 + 8 = 48 -> 24).
    TFFJointShape s=tff_joint_shape(10.f,18.f,10.f,10.f,50.f,10.f);
    assert(s.valid && near(s.px,10.f) && near(s.py,34.f) && near(s.qx,34.f) && near(s.qy,10.f));
    TFFJointShape e=tff_joint_shape(10.f,34.f,10.f,10.f,34.f,10.f);
    assert(near(s.px,e.px) && near(s.py,e.py) && near(s.qx,e.qx) && near(s.qy,e.qy));
    auto g=tff_contour_geometry(s.px,s.py,s.qx,s.qy,10.f,10.f);
    assert(near(g.angle,-45.f));
    float bow=tff_source_bow(s.px,s.py,10.f,10.f,s.qx,s.qy);
    assert(near(std::fabs(bow)/g.extent,0.5f));      // relative bow of a right angle
    // Swapping the arms changes nothing (winding-free).
    TFFJointShape r=tff_joint_shape(50.f,10.f,10.f,10.f,10.f,18.f);
    assert(near(r.px,s.qx) && near(r.qy,s.py));
    assert(!tff_joint_shape(10.f,10.f,10.f,10.f,20.f,10.f).valid);
    std::cout<<"joint ok\n";
}
''')
        self.assertEqual(out, "joint ok\n")

if __name__ == "__main__":
    unittest.main()
