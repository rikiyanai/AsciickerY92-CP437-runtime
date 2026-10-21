"""FL-4907: the P5 coverage row solve must terminate and keep idioms whole.

Executes the production GLSL bodies of tff_space_counts, coverage_gap,
coverage_cell_domain, coverage_existing_group and coverage_solve as C++.
On 2026-10-02 an insertion point inside a multi-glyph idiom made
`while(at<count || pending)` step past `insertion` and loop forever on the
GPU (first FF frame never completed). Offline only: no Godot, no GPU.
"""
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASCII = ROOT / "godot_project/render/ascii"


def _function(source: str, name: str) -> str:
    match = re.search(rf"(?m)^\w+\s+{name}\([^{{]+\{{", source)
    assert match, name
    end, depth = match.end(), 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


PRELUDE = r'''
#include <algorithm>
#include <cstdint>
#include <cstdlib>
using uint=std::uint32_t; using std::min; using std::max; using std::abs;
template<int A,int B> struct Swz2 { int v[4]; Swz2& operator=(const struct ivec2& o); };
template<int A,int B,int C> struct Swz3 { int v[4]; Swz3& operator=(const struct ivec3& o); };
struct ivec2 { int x,y; ivec2(int a=0,int b=0):x(a),y(b){} };
struct ivec3 { int x,y,z; ivec3(int a=0,int b=0,int c=0):x(a),y(b),z(c){} };
struct ivec4 { union { struct { int x,y,z,w; }; Swz2<0,1> xy; Swz3<0,1,2> xyz; };
    ivec4(int a=0,int b=0,int c=0,int d=0){x=a;y=b;z=c;w=d;}
    ivec4(ivec2 a,int c,int d){x=a.x;y=a.y;z=c;w=d;}
    ivec4(const ivec4& o){x=o.x;y=o.y;z=o.z;w=o.w;}
    ivec4& operator=(const ivec4& o){x=o.x;y=o.y;z=o.z;w=o.w;return *this;} };
template<int A,int B> Swz2<A,B>& Swz2<A,B>::operator=(const ivec2& o){v[A]=o.x;v[B]=o.y;return *this;}
template<int A,int B,int C> Swz3<A,B,C>& Swz3<A,B,C>::operator=(const ivec3& o){v[A]=o.x;v[B]=o.y;v[C]=o.z;return *this;}
struct uvec4 { uint x,y,z,w; uvec4(uint a=0,uint b=0,uint c=0,uint d=0):x(a),y(b),z(c),w(d){} };
struct vec4 { float x,y,z,w; vec4(float a=0,float b=0,float c=0,float d=0):x(a),y(b),z(c),w(d){} };
struct FeatureGlyph { ivec4 metric; ivec4 atlas; ivec4 ink; };
struct GlyphInstance { vec4 placement; uvec4 paint; };
const int COVERAGE_ORIGINS=12, TFF_FEATURE_ORIGINS=7, TFF_FEATURE_PLACE_COST=16, TFF_FEATURE_ROW_PITCH=17;
const uint TFF_INSTANCE_FIRST_MEMBER=1u;
struct CoverageRowNode { ivec4 members; ivec4 geometry; ivec4 domain;
    int costs[COVERAGE_ORIGINS]; int parents[COVERAGE_ORIGINS]; };
struct FeatureSite { vec4 position; uvec4 key; };
int clamp(int v,int lo,int hi){return v<lo?lo:(v>hi?hi:v);}
bool tff_feature_is_representative(uint){return false;}
struct FeatureNode { ivec4 geometry; ivec4 parent1; uvec4 meta; };
struct { ivec4 stage, sizes, advances; vec4 lattice; } pc;
struct { FeatureGlyph items[64]; } glyphs;
struct { GlyphInstance items[256]; } instances;
struct { CoverageRowNode items[256]; } coverage_rows;
struct { FeatureSite items[4]; } sites;
struct { FeatureNode items[4]; } nodes;
struct { uint words[64]; } control, representative_glyphs;
bool face_site(FeatureSite) { return true; }
uint site_language(FeatureSite) { return 0u; }
uint row_offset_word(int) { return 0u; }
// Bound the solve so a non-terminating loop fails the test instead of hanging.
static int guard_steps=0;
#define while(c) while(((++guard_steps<100000)||(std::exit(42),false)) && (c))
'''

def test_coverage_is_one_lane_per_cell_and_one_row_solve():
    """HVIC bound: coverage work is per cell (parallel lanes), one solve per row.

    The deleted coverage_place ran up to seven full-row refits per empty cell on
    a single lane per row; the first FF frame never completed (FL-4907).
    """
    coverage = (ASCII / "features/true_ff_feature_coverage.glslinc").read_text(encoding="utf-8")
    layout = (ASCII / "features/true_ff_feature_layout.glsl").read_text(encoding="utf-8")
    assert "coverage_place" not in coverage
    stage = _function(coverage, "stage_coverage")
    assert "void stage_coverage(uint row_index,uint lane)" in stage
    assert "for(int cell=int(lane);cell<columns;cell+=LANES)" in stage
    # P5 validates P1's admitted page and reports an uncovered owed cell for
    # its row; the row's certified glyphs still paint. It never refits,
    # inserts, or deletes glyphs, and never withdraws the whole row.
    assert "coverage_refit(" not in stage and "coverage_solve(" not in stage
    assert "report_row(row,TFF_LAYOUT_STAGE_COVERAGE_GLYPH)" in stage
    assert "reject_row(" not in stage
    assert "stage_coverage(tff_dispatch_linear_group_id(),lane);" in layout
    assert "if(lane==0u) stage_coverage" not in layout


def test_coverage_validation_never_splits_an_idiom():
    coverage = (ASCII / "features/true_ff_feature_coverage.glslinc").read_text(encoding="utf-8")
    stage = _function(coverage, "stage_coverage")
    assert "coverage_insertion" not in coverage
    assert "instances.items[" not in stage
    assert "coverage_count=control.words[row_count_word(row)]" in stage
    assert "control.words[row_count_word(row)] =" not in stage
    assert "coverage_cell_has_mark" in stage and "coverage_cell_owes_mark" in stage
