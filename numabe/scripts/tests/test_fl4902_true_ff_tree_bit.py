"""FL-4902 lane TB (map FL-4900): the True FF retained-tree bit, end to end offline.

Material 240 is shared by tree bark and every authored fixture
(compile_fl4789_chunk_catalog.py AUTHORED_FIXTURE_MAT = TREE_TRUNK_MAT). The
chunk compiler gives every fixture face the neutral growth code
FIXTURE_GROWTH_CODE in its 4708 tag and never gives it to bark; the P0
pre-pass static path publishes the tree bit as feature-class bit 4
(P0_CLASS_TREE); a support carries it in geo.y (TFF_SUPPORT_TREE, the harness
copies it as the support's producer does); tff_role_vegetation turns tree
bark into TRUNK or BRANCH.

Compiled as C++ from production text: the P0 pre-pass retained-face block
(true_ff_gbuffer.glsl), the StructuralCell layout and accessors
(true_ff_frame_params.glslinc), the role ids and the vegetation producer.
Checks:
- tree versus fixture on both committed chunk catalogs: every tagged face of
  an authored_y8 or lab_grove record carries the bit, no authored_fixture face
  does, and the producer ranks a fixture's vertical edge GENERIC;
- the feature class: family bits or the ribbon bit never read as a tree;
- the lean rule through the whole path (30 degrees from vertical);
- camera independence: only world facts of the source face decide;
- the compiler guard: a bark face whose growth code collides with the
  fixture code is moved off it;
- TRAIN fail-closed for the TRUNK/BRANCH run rows, and the rows themselves
  (thin bar, no box corner, full angle coverage, fork junctions).
No Godot, GPU, capture, replay or proof infrastructure: this establishes the
arithmetic of these owners over the committed data only, not renderer
behaviour or visible depiction.
"""
import copy
import json
import math
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import compile_fl4789_chunk_catalog as catalog_compiler
from scripts.compile_true_ff_outline_language import compile_run_table

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
COMPOSITION = PROJECT / "render/ascii/composition"
STRUCTURE = PROJECT / "render/ascii/structure"
PARAMS = STRUCTURE / "true_ff_frame_params.glslinc"
PARTITION = PROJECT / "assets/world_partition"
CATALOGS = (
    ("fl4789_chunk_catalog.v1.json", "fl4789_chunk_assets.v1.bin"),
    ("sbu_carto_scale225_20260601.fl4789_chunk_catalog.v1.json",
     "sbu_carto_scale225_20260601.fl4789_chunk_assets.v1.bin"),
)
LANGUAGE = PROJECT / "assets/glyphs/true_ff/outline_language.v1.json"
CATALOG = PROJECT / "assets/glyphs/true_ff/saitamaar/catalog.json"
TREE_KINDS = {"authored_y8", "lab_grove"}


def _function(text: str, signature: str) -> str:
    begin = text.index(signature)
    return text[begin:text.index("\n}\n", begin) + 3]


def params_scalar() -> str:
    text = PARAMS.read_text()
    start = text.index("struct StructuralCell")
    parts = [text[start:text.index("};", start) + 2]]
    parts += re.findall(r"^const (?:uint|float) TFF_\w+ = [^;]+;", text, flags=re.M)
    for name in ("bool tff_has_source_contour(", "uint tff_material(", "uint tff_source_kind(",
                 "uint tff_support_flags("):
        parts.append(_function(text, name))
    return "\n".join(parts)


def gbuffer_retained() -> str:
    """The P0 static path's retained-face constants and tree block, as one function."""
    text = (STRUCTURE / "true_ff_gbuffer.glsl").read_text()
    consts = re.findall(r"^const (?:uint|float) (?:RETAINED|P0_CLASS)_\w+ = [^;]+;", text, flags=re.M)
    begin = text.index("	tree = false;\n	if (material >= RETAINED_MAT_FIRST")
    end = text.index("	}\n", begin) + len("	}\n")
    return ("\n".join(consts) + "\nuint p0_feature_class(uint material, vec4 row3) {\n"
            "	bool tree;\n" + text[begin:end]
            + "	return tree ? P0_CLASS_TREE : 0u;\n}\n")


PRELUDE = r'''
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <random>
typedef uint32_t uint;
using std::abs; using std::floor; using std::round; using std::min; using std::max;
struct vec4 { float x=0,y=0,z=0,w=0; };
struct uvec4 { uint x=0,y=0,z=0,w=0; };
float fract(float v) { return v-std::floor(v); }
float sign(float v) { return v>0.0f?1.0f:(v<0.0f?-1.0f:0.0f); }
float uintBitsToFloat(uint u) { float f;std::memcpy(&f,&u,4);return f; }
uint floatBitsToUint(float f) { uint u;std::memcpy(&u,&f,4);return u; }
'''

HARNESS = r'''
bool tagged(vec4 row3) { return std::floor(std::abs(row3.w))==4708.0f; }
bool ff_retained_tree_face(uvec4 ids) { return (ids.w&P0_CLASS_TREE)!=0u; }
// A support's producer: region from the source ids, the P0 tree bit into geo.y.
StructuralCell support_from(uvec4 source_ids,uint kind,float ax,float ay,float az,
        float bx,float by,float bz) {
    StructuralCell c;
    c.meta.x=kind;
    c.meta.y=TFF_FLAG_VALID|((source_ids.y&TFF_MATERIAL_MASK)<<TFF_MATERIAL_SHIFT)|(1u<<TFF_SOURCE_KIND_SHIFT);
    c.contour_a.x=floatBitsToUint(ax);c.contour_a.y=floatBitsToUint(ay);c.contour_a.z=floatBitsToUint(az);
    c.contour_b.x=floatBitsToUint(bx);c.contour_b.y=floatBitsToUint(by);c.contour_b.z=floatBitsToUint(bz);
    c.contour_a.w=0x1234567u;c.contour_b.w=5u;
    c.region.x=source_ids.x;c.region.y=source_ids.y;c.region.z=3u;c.region.w=2u;
    c.geo.x=50.0f;
    if(ff_retained_tree_face(source_ids)) c.geo.y=float(TFF_SUPPORT_TREE);
    return c;
}
uvec4 ids_of(uint material,uint feature_class) { uvec4 v;v.x=77u;v.y=material;v.w=feature_class;return v; }
vec4 tag_row(float w) { vec4 v;v.w=w;return v; }
'''


def run_unit(body: str, stdin: str = "") -> str:
    compiler = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    assert compiler, "C++ compiler required"
    source = (PRELUDE + params_scalar() + "\n" + gbuffer_retained()
              + (COMPOSITION / "true_ff_roles.glslinc").read_text() + "\n"
              + (COMPOSITION / "true_ff_role_vegetation.glslinc").read_text() + "\n"
              + HARNESS + body)
    with tempfile.TemporaryDirectory(prefix="ff-tree-bit-") as directory:
        binary = str(Path(directory) / "unit")
        built = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                               input=source, text=True, capture_output=True)
        assert built.returncode == 0, built.stderr[-4000:]
        return subprocess.run([binary], input=stdin, text=True, capture_output=True,
                              check=True).stdout


def retained_faces():
    """(source kind, material, row2.w bits, row3.w bits) of every retained face."""
    faces = []
    for catalog_name, payload_name in CATALOGS:
        catalog = json.loads((PARTITION / catalog_name).read_text())
        payload = (PARTITION / payload_name).read_bytes()
        for asset in catalog["asset_records"]:
            (kind,) = asset["source_kinds"]
            count = asset["rows_length"] // 4
            words = struct.unpack_from("<%dI" % count, payload, asset["rows_offset"])
            for face in range(0, count, 16):
                material = int(struct.unpack("<f", struct.pack("<I", words[face + 3]))[0])
                if 240 <= material <= 245:
                    faces.append((kind, material, words[face + 11], words[face + 15]))
    return faces


class TreeBitPath(unittest.TestCase):
    def test_constants_agree_across_owners(self):
        gbuffer = (STRUCTURE / "true_ff_gbuffer.glsl").read_text()
        self.assertEqual(catalog_compiler.FIXTURE_GROWTH_CODE, 1024)
        self.assertIn("const uint RETAINED_FIXTURE_GROWTH_CODE = %du;" % catalog_compiler.FIXTURE_GROWTH_CODE,
                      gbuffer)
        self.assertIn("const uint P0_CLASS_TREE = 1u << 4u;", gbuffer)
        self.assertEqual(catalog_compiler.AUTHORED_FIXTURE_MAT, catalog_compiler.TREE_TRUNK_MAT)

    def test_committed_catalogs_tree_versus_fixture(self):
        faces = retained_faces()
        kinds = {kind for kind, *_ in faces}
        self.assertTrue({"authored_fixture", "authored_y8"} <= kinds, kinds)
        stdin = "".join("%d %d %d\n" % (material, row2, row3) for _, material, row2, row3 in faces)
        out = run_unit(r'''
int main() {
    uint material,row2,row3;
    while(std::scanf("%u %u %u",&material,&row2,&row3)==3) {
        (void)row2;
        uint cls=p0_feature_class(material,tag_row(uintBitsToFloat(row3)));
        StructuralCell c=support_from(ids_of(material,cls),TFF_KIND_SILHOUETTE,0,0,0,0,1,0);
        std::printf("%u %u %u\n",tagged(tag_row(uintBitsToFloat(row3)))?1u:0u,
            (tff_support_flags(c)&TFF_SUPPORT_TREE)!=0u?1u:0u,
            tff_role_vegetation(c));
    }
}
''', stdin)
        results = [tuple(int(v) for v in line.split()) for line in out.splitlines()]
        self.assertEqual(len(results), len(faces))
        counts = {"tree": 0, "fixture": 0, "untagged": 0}
        for (kind, material, _, _), (present, tree, role) in zip(faces, results):
            if not present:
                # 244/245 root cards carry no 4708 tag.
                self.assertIn(material, (244, 245))
                self.assertEqual(tree, 0)
                counts["untagged"] += 1
            elif kind in TREE_KINDS:
                self.assertEqual(tree, 1, (kind, material))
                if material == 240:
                    self.assertEqual(role, 12)  # TFF_ROLE_TRUNK: vertical world edge
                counts["tree"] += 1
            else:
                self.assertEqual(kind, "authored_fixture")
                self.assertEqual((material, tree, role), (240, 0, 0))  # generic hard outline
                counts["fixture"] += 1
        self.assertGreater(counts["tree"], 1000)
        self.assertGreater(counts["fixture"], 1000)

    def test_feature_class_and_lean_rule(self):
        out = run_unit(r'''
int main() {
    const float fixture=4708.0f+1024.0f/2048.0f;
    // Bark (any code but the fixture code) and crown faces are trees, in either winding.
    for(uint code=0u;code<2048u;++code) for(float s=-1.0f;s<=1.0f;s+=2.0f) {
        float tag=s*(4708.0f+float(code)/2048.0f);
        uint bark=p0_feature_class(240u,tag_row(tag));
        assert(((bark&P0_CLASS_TREE)!=0u)==(code!=1024u));
        for(uint m=241u;m<=243u;++m)
            assert((p0_feature_class(m,tag_row(tag))&P0_CLASS_TREE)!=0u);
    }
    // Untagged faces and faces outside the retained range are never trees.
    assert(p0_feature_class(240u,tag_row(0.0f))==0u);
    assert(p0_feature_class(200u,tag_row(4708.25f))==0u);
    // Feature class: family bits and the ribbon bit never read as a tree.
    for(uint bits:{0u,1u,2u,3u,4u,6u,1u<<5u,(1u<<5u)|2u}) {
        assert(!ff_retained_tree_face(ids_of(140u,bits)));
        assert(!ff_retained_tree_face(ids_of(240u,bits)));
    }
    assert(ff_retained_tree_face(ids_of(240u,P0_CLASS_TREE|3u)));
    // Lean rule end to end: 30 degrees from vertical splits trunk from branch.
    uint bark=p0_feature_class(240u,tag_row(4708.0f+7.0f/2048.0f));
    uint house=p0_feature_class(240u,tag_row(fixture));
    float t=std::tan(30.0f*3.14159265f/180.0f);
    const uint kinds[3]={TFF_KIND_SILHOUETTE,TFF_KIND_BOUNDARY,TFF_KIND_CREASE};
    for(int k=0;k<3;++k) {
        assert(tff_role_vegetation(support_from(ids_of(240u,bark),kinds[k],0,0,0,t*0.999f,1,0))==TFF_ROLE_TRUNK);
        assert(tff_role_vegetation(support_from(ids_of(240u,bark),kinds[k],0,0,0,0,1,t*1.001f))==TFF_ROLE_BRANCH);
        assert(tff_role_vegetation(support_from(ids_of(240u,bark),kinds[k],0,0,0,1,0,0))==TFF_ROLE_BRANCH);
        assert(tff_role_vegetation(support_from(ids_of(240u,house),kinds[k],0,0,0,0,1,0))==TFF_ROLE_GENERIC);
        assert(tff_role_vegetation(support_from(ids_of(240u,house),kinds[k],0,0,0,1,0,0))==TFF_ROLE_GENERIC);
    }
    // Shadow chords on bark stay generic.
    assert(tff_role_vegetation(support_from(ids_of(240u,bark),TFF_KIND_SHADOW,0,0,0,0,1,0))==TFF_ROLE_GENERIC);
    std::printf("union and lean ok\n");
}
''')
        self.assertIn("union and lean ok", out)

    def test_camera_independence(self):
        out = run_unit(r'''
int main() {
    std::mt19937 rng(4902);
    std::uniform_real_distribution<float> r(-50.0f,50.0f),range(0.1f,900.0f);
    std::uniform_int_distribution<uint> u(0u,0xffffffu);
    int checked=0;
    for(uint tag_code:{3u,1024u,2047u}) for(int trial=0;trial<200;++trial) {
        uint authored=p0_feature_class(240u,tag_row(4708.0f+float(tag_code)/2048.0f));
        float ax=r(rng),ay=r(rng),az=r(rng),bx=r(rng),by=r(rng),bz=r(rng);
        StructuralCell c=support_from(ids_of(240u,authored),TFF_KIND_SILHOUETTE,ax,ay,az,bx,by,bz);
        uint role=tff_role_vegetation(c);
        for(int view=0;view<6;++view) {
            StructuralCell moved=c;
            // Every camera lane changes: range, anchor, paint address, object
            // slot and extent, flags, endpoint order. geo.y is the source
            // face's tree flag (world) and is kept.
            moved.geo.x=range(rng);moved.geo.z=range(rng);moved.geo.w=range(rng);
            moved.world.x=r(rng);moved.world.y=r(rng);moved.world.z=r(rng);
            moved.tangent.x=r(rng);moved.tangent.z=r(rng);
            moved.meta.z=u(rng);moved.region.z=u(rng)+1u;
            moved.meta.y^=u(rng)&(TFF_FLAG_ANY_HIT|TFF_FLAG_ANY_MISS);
            if(u(rng)&1u) { uvec4 a=moved.contour_a;
                moved.contour_a.x=moved.contour_b.x;moved.contour_a.y=moved.contour_b.y;moved.contour_a.z=moved.contour_b.z;
                moved.contour_b.x=a.x;moved.contour_b.y=a.y;moved.contour_b.z=a.z; }
            assert(tff_role_vegetation(moved)==role);
            ++checked;
        }
        if(tag_code==1024u) assert(role==TFF_ROLE_GENERIC);
        else assert(role==TFF_ROLE_TRUNK || role==TFF_ROLE_BRANCH || role==TFF_ROLE_GENERIC);
    }
    std::printf("camera independent %d\n",checked);
}
''')
        self.assertRegex(out, r"camera independent \d+")

    def test_compiler_moves_colliding_bark_code(self):
        # One bark face whose growth (centroid minus nearest bark vertex) is
        # +X with a level elevation: exactly the fixture's neutral code.
        mesh = {
            "positions": [(0.0, 0.0, 0.0), (-3.0, 0.0, 0.0), (-3.0, 0.06, 0.0)],
            "colours": [(90.0, 60.0, 30.0)] * 3,
            "triangles": [(0, 1, 2)],
            "face_materials": [catalog_compiler.TREE_TRUNK_MAT],
            "face_shades": [catalog_compiler.TREE_TRUNK_SHADE],
            "face_count": 1,
        }
        tree = {"source_mesh_name": "synthetic.akm", "yaw_radians": 0.0, "procedural_scale": 1.0,
                "authored_transform_column_major_f64": [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                                                        0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]}
        with mock.patch.object(catalog_compiler, "load_tree_mesh", return_value=mesh):
            rows = catalog_compiler.build_tree_local_rows(tree)
        code = round((abs(rows[15]) - 4708.0) * 2048.0)
        self.assertEqual(math.floor(abs(rows[15])), 4708)
        self.assertNotEqual(code, catalog_compiler.FIXTURE_GROWTH_CODE)
        self.assertEqual(code, catalog_compiler.FIXTURE_GROWTH_CODE ^ 1)
        # A canopy face may keep any code: its material alone makes it a tree.
        canopy = copy.deepcopy(mesh)
        canopy["face_materials"] = [catalog_compiler.TREE_CANOPY_CONTOUR_MAT]
        with mock.patch.object(catalog_compiler, "load_tree_mesh", return_value=canopy):
            rows = catalog_compiler.build_tree_local_rows(tree)
        self.assertEqual(math.floor(abs(rows[15])), 4708)


class TreeRunRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authored = json.loads(LANGUAGE.read_text())
        cls.glyphs = {row["char"]: row for row in json.loads(CATALOG.read_text())["glyphs"]}
        cls.rows = cls.authored["run_table"]["rows"]
        cls.admitted = {(group["mode"], text): {} for group in cls.authored["groups"]
                        for text in group["sequences"]}

    def compile(self, policy=None, admitted=None):
        return compile_run_table(policy or self.authored, admitted or self.admitted, self.glyphs,
                                 self.authored["water_role_admissions"],
                                 self.authored["material_interiors"])["rows"]

    def test_rows_are_the_compilers_and_cover_every_angle(self):
        self.assertEqual(self.compile(), self.rows)
        for role in ("trunk", "branch"):
            rows = [row for row in self.rows if row["role"] == role]
            self.assertTrue(rows)
            self.assertEqual({row["language"] for row in rows}, {"hard"})
            plain = [row for row in rows if row["flags"] == 0]
            # A role with rows never falls back: plain sites cover [-90, 91).
            for angle in range(-90, 91):
                for extent in (4.0, 40.0):
                    for height in (1.0, 8.0, 16.0):
                        self.assertTrue(any(row["lo"][0] <= angle < row["hi"][0]
                                            and row["lo"][1] <= extent < row["hi"][1]
                                            and row["lo"][3] <= height < row["hi"][3] for row in plain),
                                        (role, angle, extent, height))
            # No box idiom: no corner, shoulder or heel sequence.
            self.assertFalse({row["sequence"] for row in rows}
                             & {"|￣", "￣|", "|＿", "＿|", "／￣", "＿／", "￣＼", "＼＿"})
            junctions = {row["sequence"]: int(row["lo"][0]) for row in rows if row["flags"] == 4}
            self.assertEqual(junctions, {"Y": (1 << 1) | (1 << 3) | (1 << 6),
                                         "人": (1 << 2) | (1 << 5) | (1 << 7)})
            joints = {row["sequence"] for row in rows if row["flags"] == 3}
            self.assertEqual(joints, {"(", ")"})
        # A trunk side is a thin bar: the vertical rows never take the full-width post bar.
        trunk_vertical = [row for row in self.rows if row["role"] == "trunk" and row["flags"] == 0
                          and (row["hi"][0] <= -75 or row["lo"][0] >= 75)]
        self.assertTrue(trunk_vertical)
        self.assertEqual({row["sequence"] for row in trunk_vertical}, {"|"})
        self.assertEqual({row["substitute"] for row in trunk_vertical}, {""})

    def test_train_fail_closed(self):
        for missing in (("empty", "("), ("empty", ")"), ("empty", "Y"), ("empty", "人")):
            broken = dict(self.admitted)
            del broken[missing]
            with self.assertRaises(ValueError, msg=missing):
                self.compile(admitted=broken)
        policy = copy.deepcopy(self.authored)
        policy["tree_authoring"]["joints"][0]["sequence"] = "く"   # not TRAIN-admitted
        with self.assertRaisesRegex(ValueError, "not admitted by TRAIN"):
            self.compile(policy)
        policy = copy.deepcopy(self.authored)
        policy["tree_authoring"]["roles"][0]["tracks"][0]["heights"][0][2] = "ｿ"
        with self.assertRaisesRegex(ValueError, "not admitted by TRAIN"):
            self.compile(policy)
        policy = copy.deepcopy(self.authored)
        del policy["tree_authoring"]["roles"][1]
        with self.assertRaises(ValueError):
            self.compile(policy)
        policy = copy.deepcopy(self.authored)
        policy["tree_authoring"]["junctions"].append("tee_down_missing")
        with self.assertRaises(ValueError):
            self.compile(policy)


if __name__ == "__main__":
    unittest.main()
