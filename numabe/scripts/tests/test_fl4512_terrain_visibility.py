"""Offline laws for FF pre-draw terrain culling; no engine or GPU execution."""
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
STRUCTURE = ROOT / "godot_project/render/ascii/structure"


class TerrainVisibility(unittest.TestCase):
    def test_production_bounds_and_tree_laws(self):
        # Compile the production scalar law unchanged. C++ supplies only the
        # GLSL scalar builtins; this does not emulate a GPU or prove barriers.
        law = (STRUCTURE / "true_ff_terrain_visibility.glslinc").read_text()
        code = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <limits>
#include <random>
#include <vector>
using uint=unsigned int;
using std::abs; using std::max; using std::min;
using std::isnan; using std::isinf;
static std::vector<uint>* active_counts;
uint tff_visible_node_count(uint i) { return active_counts->at(i); }
''' + law + r'''
int main() {
    std::mt19937 rng(4512);
    std::uniform_real_distribution<float> coord(-1000,1000);
    for(int trial=0;trial<20000;++trial) {
        float lo[3],hi[3],p[4];
        for(int k=0;k<3;++k) { lo[k]=coord(rng);hi[k]=lo[k]+abs(coord(rng)); }
        for(float& v:p) v=coord(rng);
        bool outside=tff_box_outside_plane(p[0],p[1],p[2],p[3],
            lo[0],lo[1],lo[2],hi[0],hi[1],hi[2]);
        double brute=-std::numeric_limits<double>::infinity();
        for(int corner=0;corner<8;++corner) {
            double value=p[3];
            for(int k=0;k<3;++k) value+=double(p[k])*((corner&(1<<k))?hi[k]:lo[k]);
            brute=max(brute,value);
        }
        if(outside) assert(brute<0); // no intersection falsely culled
    }
    assert(!tff_box_outside_plane(1,0,0,0,0,-1,-1,0,1,1));
    assert(tff_box_outside_plane(1,0,0,0,-2,-1,-1,-1,1,1));
    assert(!tff_box_outside_plane(1,0,0,0,-1,-1,-1,1,1,1));
    assert(!tff_box_outside_plane(0,1,0,-100,0,-20,0,16,101,16)); // interior peak
    assert(!tff_box_outside_plane(1,0,0,0,NAN,0,0,NAN,1,1));
    assert(!tff_box_outside_plane(1,0,0,0,-INFINITY,0,0,INFINITY,1,1));
    for(uint width: {0u,1u,15u,16u,17u,31u,47u,128u})
    for(uint height: {0u,1u,16u,19u,33u,71u}) {
        uint side=1;
        while(side<(max(width,height)+15u)/16u) side*=2u;
        uint total=(4u*side*side-1u)/3u;
        std::vector<uint> counts(total,0u),seen(width*height,0u);
        std::vector<uint> visible(side*side,0u);
        for(uint i=0;i<side*side;++i) {
            // A sparse deterministic visibility set including padding leaves.
            visible[i]=(i%3u)!=1u;
            counts[i]=visible[i]?tff_leaf_cell_count(width,height,i%side,i/side):0u;
        }
        for(uint s=side/2u;s;s/=2u)
            for(uint y=0;y<s;++y) for(uint x=0;x<s;++x)
                for(uint c=0;c<4;++c)
                    counts[tff_tree_index(side,s,x,y)] += counts[tff_tree_index(side,s*2u,x*2u+(c&1u),y*2u+(c>>1u))];
        uint command_count=counts[tff_tree_offset(side,1u)];
        active_counts=&counts;
        std::vector<uint> output(command_count,UINT32_MAX);
        for(uint y=0;y<side;++y) for(uint x=0;x<side;++x) {
            if(!counts[y*side+x]) continue;
            uint base=tff_tree_prefix(side,x,y);
            for(uint cy=y*16u;cy<min(height,(y+1u)*16u);++cy)
                for(uint cx=x*16u;cx<min(width,(x+1u)*16u);++cx) {
                    assert(base<output.size() && output[base]==UINT32_MAX);
                    output[base++]=cy*width+cx;
                }
        }
        for(uint cell:output) { assert(cell<seen.size()); ++seen[cell]; }
        for(uint y=0;y<height;++y) for(uint x=0;x<width;++x)
            assert(seen[y*width+x]==visible[(y/16u)*side+x/16u]);
    }
}
'''
        with tempfile.TemporaryDirectory(prefix="fl4512_terrain_unit_") as directory:
            binary = pathlib.Path(directory) / "terrain_unit"
            subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", str(binary)],
                           input=code, text=True, check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_only_compacted_original_cells_reach_terrain_vertex(self):
        shader = (STRUCTURE / "true_ff_gbuffer.glsl").read_text()
        main = shader[shader.index("void main() {"):]
        self.assertLess(main.index("element = visible_terrain.cells[element]"),
                        main.index("uint face_id ="))
        host = (STRUCTURE / "TrueFFSceneGBuffer.gd").read_text()
        self.assertNotIn("_push_draw(list, push, FAMILY_TERRAIN", host)
        # FL-4907 GPU_SHADOW_BOUNDARY_PASS_20261002: camera and light views
        # share _draw_geometry; each passes its own compacted cell list.
        self.assertIn("_rd.draw_list_draw_indirect(list, false, terrain_cells)", host)
        self.assertIn("_draw_geometry(list, push, _terrain_visibility.cells())", host)
        self.assertIn("_draw_geometry(list, push, _light_terrain_visibility.cells())", host)
        owner = (STRUCTURE / "TrueFFTerrainVisibility.gd").read_text()
        self.assertNotIn("buffer_get_data", owner)
        self.assertIn("STORAGE_BUFFER_USAGE_DISPATCH_INDIRECT", owner)
        compute = (STRUCTURE / "true_ff_terrain_visibility.glsl").read_text()
        self.assertIn("y <= end.y", compute)
        self.assertIn("x <= end.x", compute)
        self.assertNotIn("atomicAdd", compute)
        self.assertIn("visible.cells[base++]=y*hf.dims_identity.x+x", compute)

if __name__ == "__main__":
    unittest.main()
