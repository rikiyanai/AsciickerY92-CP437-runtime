"""FL-4902 lane VP: vehicle part identity (race-car wheels, airplane wings).

No Godot launch or visual claim. Offline units over production text:
- part transport: the G-buffer's rigid face id (part << 20 | face offset,
  true_ff_gbuffer.glsl rigid_part_face_identity) compiled as C++, decoded by
  the role producer (true_ff_role_figure.glslinc tff_fig_part), inside
  tff_edge_rank's 24-bit face range, and unique for every baked vehicle face;
- the part lane's owners: race_car_faces.json parts[], the airframe bakes'
  wing_face_indices (re-baked offline, deterministic), World.gd registration
  and the compositor's wing pose (the airframe root);
- the retained airframe id remains row2.w; P0 receives geometry independently
  of that style id and excludes only the source-owned ink object set;
- the role rules (VEHICLE_WHEEL, VEHICLE_WING, VEHICLE_BODY) and their camera
  independence;
- the compiled run rows: TRAIN-admitted, empty interiors, every plain, joint
  and junction shape selects a run, and an unadmitted sequence fails closed.
"""
import hashlib
import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

from scripts.tests.test_fl4902_run_select import PRELUDE, run, selector_cpp
from scripts.tests.test_fl4902_true_ff_role_figure import (  # noqa: F401 (fixture)
    AIRPLANE, AMBIENT, FAMILY_ACTOR, FAMILY_DYNAMIC, FAMILY_STATIC, STATIC, producer)

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
GBUFFER = PROJECT / "render/ascii/structure/true_ff_gbuffer.glsl"
RIGID_SHADER = PROJECT / "render/scene/geometry/dynamic/rigid/rigid_mover_geometry.glslinc"
RIGID_GD = PROJECT / "render/scene/geometry/dynamic/rigid/RigidMoverGeometry.gd"
FIGURE = PROJECT / "render/ascii/composition/true_ff_role_figure.glslinc"
CONTOUR = PROJECT / "render/ascii/composition/true_ff_contour_geometry.glslinc"
COMPOSITOR = PROJECT / "render/api/ASCIICompositorEffect.gd"
WORLD = PROJECT / "scripts/World.gd"
MESHES = PROJECT / "assets/world_mesh"
GLYPHS = PROJECT / "assets/glyphs/true_ff"
LANGUAGE = GLYPHS / "outline_language.v1.json"
AUTHORING = GLYPHS / "outline_authoring.v1.json"
CATALOG = GLYPHS / "saitamaar/catalog.json"
COMPILER = ROOT / "scripts/compile_true_ff_outline_language.py"
TRAIN = Path("/Users/r/Projects/unicode-glyph-morphology-explorer/assets/glyphs/corpus/"
             "aahub_aa003_train.sjis_combos.v1.json")
NAUSICAA_SOURCE = ROOT / "assets/meshes/incoming/plane_nausicaa_decimated.akm"


def glsl_uint(path, name):
    return int(re.search(rf"const uint {name} = (\d+)u;", path.read_text())[1])


def function_text(source, signature):
    start = source.index(signature)
    return source[start:source.index("\n}\n", start) + 3]


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rigid_part_ids(path):
    """Per-face rigid part as World.gd registers it (race car: parts[] ranges;
    airframes: wing 2, then propeller 1 over it)."""
    doc = json.loads(path.read_text())
    parts = [0] * doc["face_count"]
    if "parts" in doc:
        for index, part in enumerate(doc["parts"]):
            for face in range(part["face_start"], part["face_start"] + part["face_count"]):
                parts[face] = index
    for face in doc.get("wing_face_indices", []):
        parts[face] = 2
    for face in doc.get("prop_face_indices", []):
        parts[face] = 1
    return parts


# --- part transport -------------------------------------------------------

TRANSPORT_MAIN = r'''
int main() {
    uint part, offset;
    while (std::cin >> part >> offset) {
        uint id = rigid_part_face_identity(part, offset);
        StructuralCell c{};
        c.region.z = id + 1u; c.region.w = TFF_FIG_FAMILY_DYNAMIC + 1u;
        std::cout << id << " " << tff_fig_part(c) << " " << tff_edge_rank(218u, id + 1u) << "\n";
    }
}
'''


@pytest.fixture(scope="module")
def transport(tmp_path_factory):
    from scripts.tests.test_fl4902_true_ff_role_figure import HARNESS, structural_cell
    gbuffer = GBUFFER.read_text()
    figure = FIGURE.read_text()
    contour = CONTOUR.read_text()
    source = "\n".join([
        HARNESS, structural_cell(),
        "const uint FF_RIGID_PART_SHIFT = %du;" % glsl_uint(GBUFFER, "FF_RIGID_PART_SHIFT"),
        "const uint FF_RIGID_PART_MAX = %du;" % glsl_uint(GBUFFER, "FF_RIGID_PART_MAX"),
        function_text(gbuffer, "uint rigid_part_face_identity("),
        *(line for line in figure.splitlines() if line.startswith("const uint TFF_FIG_")),
        function_text(figure, "uint tff_fig_part("),
        function_text(contour, "uint tff_edge_rank("),
        TRANSPORT_MAIN])
    directory = tmp_path_factory.mktemp("ff-vehicle-part-transport")
    (directory / "t.cpp").write_text(source)
    subprocess.run(["c++", "-std=c++17", str(directory / "t.cpp"), "-o", str(directory / "t")],
                   check=True, capture_output=True, text=True)

    def encode(pairs):
        out = subprocess.run([str(directory / "t")], input="".join(f"{p} {o}\n" for p, o in pairs),
                             capture_output=True, text=True, check=True).stdout.split("\n")
        return [tuple(int(v) for v in line.split()) for line in out if line]

    return encode


def test_part_round_trips_through_the_structure_region(transport):
    pairs = [(part, offset) for part in range(16) for offset in (0, 1, 623, 1951, 0xFFFFE)]
    for (part, offset), (identity, decoded, rank) in zip(pairs, transport(pairs)):
        assert identity == (part << 20) | offset
        assert decoded == part
        # face + 1 stays inside tff_edge_rank's 24-bit face range: no clamp.
        assert rank & 0xFFFFFF == identity + 1
    # A part past 15 keeps the id unique (the offset is) and reads as 15.
    assert transport([(17, 5)]) == [((15 << 20) | 5, 15, (218 << 24) | ((15 << 20) | 6))]


def test_every_baked_vehicle_face_has_a_unique_rank_and_its_own_part(transport):
    for name in ("race_car_faces", "airplane_airframe_faces", "nausicaa_airframe_faces",
                 "macchi_airframe_faces"):
        parts = rigid_part_ids(MESHES / f"{name}.json")
        result = transport(list(zip(parts, range(len(parts)))))
        assert [decoded for _, decoded, _ in result] == parts, name
        ranks = [rank for _, _, rank in result]
        assert len(set(ranks)) == len(ranks), name
    assert set(rigid_part_ids(MESHES / "race_car_faces.json")) == {0, 1, 2, 3, 4}


def test_gbuffer_publishes_the_local_row_part_for_rigid_faces_only():
    source = GBUFFER.read_text()
    assert glsl_uint(GBUFFER, "FF_RIGID_PART_SHIFT") == glsl_uint(FIGURE, "TFF_FIG_PART_SHIFT") == 20
    assert glsl_uint(GBUFFER, "FF_RIGID_PART_MAX") == glsl_uint(FIGURE, "TFF_FIG_PART_MAX") == 15
    assert glsl_uint(GBUFFER, "FAMILY_DYNAMIC") == glsl_uint(FIGURE, "TFF_FIG_FAMILY_DYNAMIC")
    # The part is the immutable local row 2 w (register_mesh's lane), never
    # the rigid_face_rows output (whose row2.w is the airframe id).
    part = re.sub(r"\s+", "", function_text(source, "uint rigid_face_part("))
    assert "rigid_local.rows[(face_index&0xfffffu)*4u+2u].w" in part
    assert "retained[face * 16 + 11] = float(part)" in RIGID_GD.read_text()
    main = re.sub(r"\s+", "", source[source.index("void main() {\n\tuint family = pc.draw.x;"):])
    assert ("ok=dynamic_vertex(pc.draw.y,element,vertex_index,world,material,entity);"
            "DynMoverid_mover=dynamic_movers.values[pc.draw.y];"
            "uintmover_face=id_mover.head.y+element;"
            "if(ok&&rigid_face_address(mover_face)){"
            "face_id=rigid_part_face_identity(rigid_face_part(mover_face),element);") in main
    assert "primitive_face=rigid_primitive_faces.values[mover_face&0xfffffu];" in main
    # The P0 identity attachment carries the face id in ids.z.
    assert "v_ids=uvec4(entity,material,face_id,feature_class);" in main


def test_airframe_id_consumers_are_unaffected(tmp_path):
    # rigid_face_rows still hands every reader (visibility, capture, Product,
    # the FF G-buffer) the airframe id in row2.w and the effect kind in row3.w.
    rigid = re.sub(r"\s+", "", RIGID_SHADER.read_text())
    assert "c=vec4(points[2],float(instance.materials.w));d=vec4(points[3],float(instance.range.w));" in rigid
    assert "uintpose=(instance.range.x+uint(c.w))*3u;" in rigid
    # P0 classification moved to source ownership (FL-4917), not an airframe
    # guess. Execute the actual vertex consumer with different style ids.
    dynamic = function_text(GBUFFER.read_text(), "bool dynamic_vertex(")
    dynamic = re.sub(r"\bout (\w+) (\w+)", r"\1& \2", dynamic)
    dynamic = re.sub(r"vec3 corners\[4\] = vec3\[4\]\(([^;]+)\);", r"vec3 corners[4] = {\1};", dynamic)
    for row in range(4):
        dynamic = dynamic.replace(f"row{row}.xyz", f"xyz(row{row})")
    program = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
using uint=uint32_t;using std::max;
float max(float a,double b){return std::max(a,float(b));}
struct vec3{float x,y,z;};struct vec4{float x,y,z,w;};
vec3 xyz(vec4 v){return {v.x,v.y,v.z};}
struct U{uint x,y,z,w;};struct DynMover{U head;};
struct {DynMover values[1];}dynamic_movers;
struct {vec4 rows[4];}dynamic_face_rows;
float airframe=0;bool rigid=true;
bool rigid_face_address(uint){return rigid;}
uint corner_index(uint v){const uint indices[6]={0,1,2,0,2,3};return indices[v];}
void rigid_face_rows(uint,vec4& a,vec4& b,vec4& c,vec4& d){
 a={1,2,3,218};b={4,5,6,0};c={7,8,9,airframe};d={10,11,12,9};}
''' + dynamic + r'''
int main(){dynamic_movers.values[0].head={91,0,1,0};
 for(float id:{0.f,3.f,11.f,99.f}){airframe=id;for(uint v=0;v<6;++v){vec3 p;uint m,e;
 assert(dynamic_vertex(0,0,v,p,m,e));assert(m==218&&e==91);
 assert(p.x==1.f+3.f*corner_index(v));}}
 vec3 p;uint m,e;assert(!dynamic_vertex(0,1,0,p,m,e));
 rigid=false;dynamic_face_rows.rows[0]={21,22,23,219};dynamic_face_rows.rows[1]={24,25,26,0};
 dynamic_face_rows.rows[2]={27,28,29,99};dynamic_face_rows.rows[3]={30,31,32,0};
 assert(dynamic_vertex(0,0,2,p,m,e)&&p.x==27&&m==219&&e==91);
}
'''
    binary = tmp_path / "p0-airframe-consumer"
    built = subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", str(binary)],
                           input=program, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)
    owner = (PROJECT / "render/ascii/structure/TrueFFSceneGBuffer.gd").read_text()
    assert re.search(r"if p0_ink_objects\.has\(object_id\):\s+continue", owner)
    assert owner.index("if p0_ink_objects.has(object_id):") < owner.index("_mover_draws.append(index)")


# --- part lane owners -----------------------------------------------------

def test_airframe_bakes_are_deterministic_and_carry_disjoint_wing_parts(tmp_path):
    for script, asset in (("bake_airplane_airframe.py", "airplane_airframe_faces.json"),
                          ("bake_macchi_airframe.py", "macchi_airframe_faces.json")):
        module = load_module(ROOT / "scripts" / script, script[:-3])
        module.OUT_PATH = str(tmp_path / asset)
        module.main()
        assert (tmp_path / asset).read_text() == (MESHES / asset).read_text(), script
        doc = json.loads((MESHES / asset).read_text())
        wing, prop = set(doc["wing_face_indices"]), set(doc["prop_face_indices"])
        assert wing and prop and not wing & prop
        assert max(wing) < doc["face_count"]
        # Wings are lifting surfaces: every wing face lies off the centreline.
        rows = doc["rows"]
        for face in wing:
            corners = [rows[face * 16 + k * 4: face * 16 + k * 4 + 3] for k in range(4)]
            assert max(abs(c[2]) for c in corners) >= 0.2, (script, face)


def test_nausicaa_wing_lane_is_derived_at_bake_time(tmp_path):
    doc = json.loads((MESHES / "nausicaa_airframe_faces.json").read_text())
    assert doc["wing_rule"] == {"lateral_axis": "+z", "abs_centroid_gt": 0.75}
    rows = doc["rows"]
    lateral = [sum(rows[f * 16 + k * 4 + 2] for k in range(3)) / 3 for f in range(doc["face_count"])]
    assert doc["wing_face_indices"] == [f for f, z in enumerate(lateral) if abs(z) > 0.75]
    # The rule sits in an empty band: body centroids reach 0.49, wings start at 1.35.
    assert max(abs(z) for z in lateral if abs(z) <= 0.75) < 0.5
    assert min(abs(z) for z in lateral if abs(z) > 0.75) > 1.3
    if not NAUSICAA_SOURCE.exists():
        pytest.skip("Nausicaa source mesh not present")
    bake = load_module(ROOT / "scripts/bake_akm_to_static_world_faces.py", "bake_akm")
    out = tmp_path / "n.json"
    bake.bake(str(NAUSICAA_SOURCE), str(out), 2.6, 0.85, "airframe", forward_axis="+z",
              airframe_mat=221, wing_root_z=0.75)
    assert json.loads(out.read_text()) == doc


def test_world_registers_wing_and_prop_parts_and_the_wing_pose_is_the_root():
    world = WORLD.read_text()
    assert int(re.search(r"const AIRPLANE_WING_PART := (\d+)", world)[1]) == glsl_uint(
        FIGURE, "TFF_FIG_AIRPLANE_WING_PART") == 2
    assert int(re.search(r"const AIRPLANE_PROP_PART := (\d+)", world)[1]) == 1
    for cache in ("_airplane", "_nausicaa", "_macchi"):
        assert f"{cache}_wing_face_indices = wing_indices if wing_indices is Array else []" in world
    register = world[world.index("func _append_one_airplane_source_fact("):]
    register = register[:register.index("register_mesh(mesh_key, airframe_rows, parts)")]
    # Wing first, propeller over it: a face is never both.
    assert register.index("parts[int(index)] = AIRPLANE_WING_PART") < register.index(
        "parts[int(index)] = AIRPLANE_PROP_PART")
    descriptor = COMPOSITOR.read_text()
    descriptor = descriptor[descriptor.index("func _rigid_mover_descriptor("):]
    assert "poses = [root, root * Transform3D(rotation, pivot - rotation * pivot), root]" in descriptor
    car = json.loads((MESHES / "race_car_faces.json").read_text())
    names = [part["name"] for part in car["parts"]]
    first = glsl_uint(FIGURE, "TFF_FIG_RACE_CAR_FIRST_WHEEL")
    last = glsl_uint(FIGURE, "TFF_FIG_RACE_CAR_LAST_WHEEL")
    assert names[0] == "body" and all(name.startswith("wheel_") for name in names[first:last + 1])
    assert len(names) == last + 1


# --- role rules -------------------------------------------------------------

def rigid_region(entity, material, part, offset=7):
    return (entity, material, ((part << 20) | offset) + 1, FAMILY_DYNAMIC)


EDGES = ("SILHOUETTE", "BOUNDARY", "CREASE", "SHADOW")


def test_race_car_wheels_are_rings_on_their_outline(producer):  # noqa: F811
    cells = [{"region": rigid_region(AMBIENT, material, part), "edge": edge}
             for part in (1, 2, 3, 4) for material in (218, 219) for edge in EDGES]
    assert producer(cells) == ["VEHICLE_WHEEL", "VEHICLE_WHEEL", "GENERIC", "GENERIC"] * 8
    body = [{"region": rigid_region(AMBIENT, 218, 0), "edge": edge} for edge in EDGES]
    assert producer(body) == ["VEHICLE_BODY"] * 3 + ["GENERIC"]


def test_airplane_wings_are_wing_strokes_and_the_rest_stays_body(producer):  # noqa: F811
    wing = [{"region": rigid_region(AIRPLANE, material, 2), "edge": edge}
            for material in (200, 201, 221) for edge in EDGES]
    assert producer(wing) == (["VEHICLE_WING"] * 3 + ["GENERIC"]) * 3
    rest = [{"region": rigid_region(AIRPLANE, 200, part), "edge": edge}
            for part in (0, 1, 3, 15) for edge in EDGES]
    assert producer(rest) == (["VEHICLE_BODY"] * 3 + ["GENERIC"]) * 4


def test_part_bits_mean_nothing_off_the_vehicle_rigid_path(producer):  # noqa: F811
    cells = [
        # Airplane kind with wheel parts is not a wheel; an ambient-mesh wing
        # part is not a wing (the race car has no part 2 wing; part 2 is a wheel).
        {"region": rigid_region(AIRPLANE, 200, 4)},
        {"region": rigid_region(AMBIENT, 218, 2)},
        # Non race-car ambient meshes stay generic whatever the part.
        {"region": rigid_region(AMBIENT, 237, 2)},
        # A static face whose id has the same high bits is not a vehicle.
        {"region": (STATIC, 218, ((2 << 20) | 7) + 1, FAMILY_STATIC)},
        # Airplane entity with a non-dynamic family carries no part.
        {"region": (AIRPLANE, 200, ((2 << 20) | 7) + 1, FAMILY_ACTOR)},
    ]
    assert producer(cells) == ["VEHICLE_BODY", "VEHICLE_WHEEL", "GENERIC", "GENERIC",
                               "VEHICLE_BODY"]


def test_part_roles_are_anchored_on_world_identity_not_screen_facts(producer):  # noqa: F811
    base = [rigid_region(AMBIENT, 218, 3, 1300), rigid_region(AMBIENT, 219, 0, 12),
            rigid_region(AIRPLANE, 200, 2, 40), rigid_region(AIRPLANE, 208, 1, 130)]
    frames = [
        {"paint": 0, "world": (0.0, 0.0, 0.0), "geo": (1.0, 0.0), "contour": (1, 1)},
        {"paint": 99999, "world": (-120.0, 4.0, 88.0), "geo": (300.0, 5.0), "contour": (77, 3)},
        {"paint": 5, "world": (1.5, -2.0, 0.25), "geo": (2.0, 31.0), "contour": (0xFFFFFFF0, 9)},
    ]
    results = [producer([{"region": region, **frame} for region in base]) for frame in frames]
    assert results[0] == ["VEHICLE_WHEEL", "VEHICLE_BODY", "VEHICLE_WING", "VEHICLE_BODY"]
    assert all(result == results[0] for result in results)


# --- run rows ---------------------------------------------------------------

class VehicleRunRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.language = json.loads(LANGUAGE.read_text())
        cls.rows = cls.language["run_table"]["rows"]

    def part_rows(self, role):
        return [row for row in self.rows if row["role"] == role]

    def test_language_records_the_authoring_blob(self):
        self.assertEqual(self.language["provenance"]["authoring_sha256"],
                         hashlib.sha256(AUTHORING.read_bytes()).hexdigest())
        self.assertEqual(self.language["vehicle_part_authoring"],
                         json.loads(AUTHORING.read_text())["vehicle_part_authoring"])

    def test_rows_are_admitted_hard_rows_with_empty_interiors(self):
        roles = {name: int(value) for name, value in re.findall(
            r"const uint TFF_ROLE_(\w+) = (\d+)u;",
            (PROJECT / "render/ascii/composition/true_ff_roles.glslinc").read_text())}
        admitted = {text for group in self.language["groups"] if group["mode"] == "empty"
                    for text in group["sequences"]}
        generic = [row for row in self.rows if row["role"] == "generic" and row["language"] == "hard"]
        key = ("track", "flags", "detail", "sequence", "substitute", "lo", "hi", "phase_advance_px")
        outline = {"|￣", "￣|", "|＿", "＿|", "／￣", "＿／", "￣＼", "＼＿"}
        for role in ("vehicle_wheel", "vehicle_wing"):
            rows = self.part_rows(role)
            self.assertTrue(rows)
            for row in rows:
                self.assertEqual(row["role_id"], roles[role.upper()])
                self.assertEqual((row["language"], row["decorations"], row["mode"]), ("hard", [], "empty"))
                self.assertIn(row["sequence"], admitted)
                self.assertNotIn(row["sequence"], outline)
            # No plain bowed rows: SITE has only a straight source edge.
            # Connected turns use the existing source_joint branch geometry.
            self.assertTrue(all(row["flags"] != 2 for row in rows))
            # Straight rows and junction rows are the generic rows verbatim.
            for row in rows:
                if row["flags"] in (0, 4):
                    self.assertTrue(any(all(g[k] == row[k] for k in key) for g in generic), row["track"])
        wheel = {row["sequence"] for row in self.part_rows("vehicle_wheel") if row["flags"] != 0}
        self.assertTrue({"(", ")", "⌒", "＿", "ヽ", "ノ"} <= wheel)
        wing = {row["sequence"] for row in self.part_rows("vehicle_wing") if row["flags"] == 3}
        self.assertTrue({"＜", "＞", "∧", "∨"} <= wing)

    def test_every_plain_joint_and_junction_shape_selects_a_run(self):
        sequences = []
        for row in self.rows:
            for text in (row["sequence"], row["substitute"]):
                if text and text not in sequences:
                    sequences.append(text)
        languages = self.language["languages"]
        lines = []
        for row in self.rows:
            k = [row["role_id"], languages[row["language"]], row["flags"], row["detail"]]
            lines.append("{{%s},{%s},{%s},ivec4(%d,%d,%d,0)}" % (
                ",".join(f"{v}u" for v in k), ",".join(repr(float(v)) + "f" for v in row["lo"]),
                ",".join(repr(float(v)) + "f" for v in row["hi"]),
                sequences.index(row["sequence"]),
                sequences.index(row["substitute"]) if row["substitute"] else -1, row["phase_advance_px"]))
        table = ("std::vector<TFFRunRow> table={\n" + ",\n".join(lines) + "};\n"
                 "int tff_run_row_count() { return int(table.size()); }\n"
                 "TFFRunRow tff_run_row(int index) { return table[index]; }\n")
        junction_masks = sorted({int(row["lo"][0]) for row in self.rows
                                 if row["role"] == "generic" and row["flags"] == 4
                                 and row["language"] == "hard"})
        index = {name: sequences.index(text) for name, text in (
            ("L", "("), ("R", ")"), ("TOP", "⌒"), ("BOT", "＿"), ("LT", "＜"), ("GT", "＞"),
            ("UP", "∧"), ("DOWN", "∨"))}
        hard = languages["hard"]
        source = PRELUDE + "\nusing std::abs; using std::sqrt; using std::ceil; using std::floor; using std::exp2;\n" \
            + "float atan(float y,float x) {return std::atan2(y,x);}\n" \
            + f'#include "{CONTOUR}"\n' + selector_cpp() + table + "".join(
            f"const int IDX_{n}={v};\n" for n, v in index.items()) + (
            "const uint MASKS[]={%s};\n" % ",".join(f"{m}u" for m in junction_masks)) + r'''
int pick(uint role,float angle,float extent,float bow,float height,uint sectors) {
    ivec4 run;
    return tff_select_run(role,''' + f"{hard}u" + r''',vec4{angle,extent,bow,height},
        sectors,TFF_BANK_UNKNOWN,uvec2{3u,5u},run)?run.x:-2;
}
// The same complete-branch shape and bow as source_joint(), not a manually
// injected nonzero SITE bow. Equal arms have real source endpoints around
// their shared vertex. Winding, translation and scaling do not rename a turn.
int branch(float angle,float ratio,bool reverse,float scale,float tx,float ty,float height=8.f) {
    float radians=angle*0.017453292519943295f;
    float dx=20.f*scale*std::cos(radians),dy=20.f*scale*std::sin(radians);
    float ax=tx-dx*.5f,ay=ty-dy*.5f,bx=tx+dx*.5f,by=ty+dy*.5f;
    float jx=tx-dy*ratio,jy=ty+dx*ratio;
    if(reverse){std::swap(ax,bx);std::swap(ay,by);}
    auto shape=tff_joint_shape(ax,ay,jx,jy,bx,by);assert(shape.valid);
    auto geometry=tff_contour_geometry(shape.px,shape.py,shape.qx,shape.qy,jx,jy);
    float bow=tff_source_bow(shape.px,shape.py,jx,jy,shape.qx,shape.qy);
    return pick(TFF_ROLE_VEHICLE_WHEEL,geometry.angle,geometry.extent,bow,height,TFF_RUN_JOINT_ARMS);
}
int main() {
    long states=0;
    for(uint role:{TFF_ROLE_VEHICLE_WHEEL,TFF_ROLE_VEHICLE_WING}) {
        for(float angle=-89.75f;angle<=90.0f;angle+=0.5f)
        for(float extent:{3.0f,8.0f,20.0f,40.0f,90.0f})
        for(float height:{0.0f,3.0f,6.0f,9.0f,12.0f,16.9f})
        for(float ratio:{-3.0f,-0.5f,-0.21f,-0.15f,-0.05f,0.0f,0.05f,0.15f,0.21f,0.5f,3.0f}) {
            assert(pick(role,angle,extent,ratio*extent,height,0u)>=0);
            assert(pick(role,angle,extent,ratio*extent,height,TFF_RUN_JOINT_ARMS)>=0);
            states+=2;
        }
        // Junctions: the same idiom as the generic hard junction row.
        for(uint mask:MASKS) {
            int own=pick(role,0.0f,0.0f,0.0f,0.0f,mask);
            assert(own>=0 && own==pick(TFF_ROLE_GENERIC,0.0f,0.0f,0.0f,0.0f,mask));
        }
    }
    // All ten fine quadrant turns have a real producer mode. These checks
    // establish selector reachability, not a closed rendered ring or fit.
    const float angles[]={0,0,45,45,-45,-45,80,80,-80,-80};
    const float ratios[]={-.15f,.15f,-.15f,.15f,-.15f,.15f,.15f,-.15f,.15f,-.15f};
    const int want[]={IDX_TOP,IDX_BOT,''' + ",".join(str(sequences.index(t)) for t in ("ヽ", "ヽ", "／", "ノ")) + r''',IDX_L,IDX_R,IDX_R,IDX_L};
    for(int i=0;i<10;++i) for(bool reverse:{false,true}) for(float scale:{.5f,1.f,5.f}) {
        assert(branch(angles[i],ratios[i],reverse,scale,123.f,-87.f)==want[i]);
        // Legacy plain-mode bow injection must not unlock an arc row.
        assert(pick(TFF_ROLE_VEHICLE_WHEEL,angles[i],20.f,ratios[i]*20.f,8.f,0u)
            ==pick(TFF_ROLE_VEHICLE_WHEEL,angles[i],20.f,0.f,8.f,0u));
    }
    assert(pick(TFF_ROLE_VEHICLE_WHEEL,0.0f,20.0f,-8.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_TOP);
    assert(pick(TFF_ROLE_VEHICLE_WHEEL,80.0f,20.0f,8.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_L);
    // A wing tip closes at one turn glyph; the generic role keeps its corner.
    assert(pick(TFF_ROLE_VEHICLE_WING,80.0f,40.0f,20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_LT);
    assert(pick(TFF_ROLE_VEHICLE_WING,80.0f,40.0f,-20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_GT);
    assert(pick(TFF_ROLE_VEHICLE_WING,0.0f,40.0f,-20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_UP);
    assert(pick(TFF_ROLE_VEHICLE_WING,0.0f,40.0f,20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_DOWN);
    int corner=pick(TFF_ROLE_GENERIC,-40.0f,40.0f,-20.0f,8.0f,TFF_RUN_JOINT_ARMS);
    assert(corner>=0 && corner!=pick(TFF_ROLE_VEHICLE_WING,-40.0f,40.0f,-20.0f,8.0f,TFF_RUN_JOINT_ARMS));
    // Tag rows, not glyphs, to catch shadowed duplicate intervals even when
    // their spellings match. Every remaining wheel joint needs a positive
    // witness from non-collinear source arms (source_joint rejects zero bow).
    for(int i=0;i<int(table.size());++i) table[i].run.x=i;
    int witnesses=0;
    for(int i=0;i<int(table.size());++i) {
        auto row=table[i];
        if(row.key.x!=TFF_ROLE_VEHICLE_WHEEL || row.key.z!=3u) continue;
        float angle=(row.lo.x+row.hi.x)*.5f;
        float ratio=(std::max(row.lo.z,-.5f)+std::min(row.hi.z,.5f))*.5f;
        if(ratio==0.f) ratio=.05f;
        float extent=row.hi.y<100.f?(row.lo.y+row.hi.y)*.5f:20.f;
        float height=(row.lo.w+row.hi.w)*.5f;
        assert(branch(angle,ratio,false,extent/20.f,0.f,0.f,height)==i);++witnesses;
    }
    assert(witnesses==22);
    std::cout<<states<<" vehicle part states select a run\n";
}
'''
        print(run(self, source, "vehicle part states select a run").strip())

    def test_unadmitted_part_sequence_fails_closed(self):
        if not TRAIN.exists():
            self.skipTest("pinned TRAIN aggregate not present")
        compiler = load_module(COMPILER, "compile_true_ff_outline_language")
        catalog, atlas = CATALOG.read_bytes(), (GLYPHS / "saitamaar/atlas.png").read_bytes()
        policy = json.loads(AUTHORING.read_text())
        for mutate, message in (
                (lambda p: p["vehicle_part_authoring"]["roles"][0].update(arcs=[]),
                 "plain-arc rows were removed"),
                (lambda p: p["vehicle_part_authoring"]["roles"][0]["joints"][0].update(sequence="◎"),
                 "not admitted by TRAIN"),
                (lambda p: p["vehicle_part_authoring"]["roles"][1]["joints"][0].update(sequence="く"),
                 "not admitted by TRAIN"),
                (lambda p: p["vehicle_part_authoring"]["roles"][1]["junctions"].append("nope"),
                 "not a generic junction"),
                (lambda p: p["vehicle_part_authoring"]["roles"][0]["joints"][-4].update(sequence="―"),
                 "competing sequence and height track"),
                (lambda p: p["vehicle_part_authoring"]["roles"][0]["joints"][-4].update(height_track="soft_arch"),
                 "not a straight generic track"),
                (lambda p: p["vehicle_part_authoring"]["roles"][0]["joints"][-4].update(height_track="rising_diagonal"),
                 "height track angle differs"),
                (lambda p: p["vehicle_part_authoring"]["roles"][0]["joints"][-4].pop("height_variant"),
                 "needs an authored width variant"),
                (lambda p: next(t for t in p["geometry_keyframes"]["tracks"] if t["name"]=="level")
                 .update(joint_height_thresholds_px=[4, 4, 17]), "Invalid joint height thresholds")):
            changed = json.loads(json.dumps(policy))
            mutate(changed)
            with self.assertRaisesRegex(ValueError, message):
                compiler.compile_language(TRAIN.read_bytes(),
                                          json.dumps(changed, ensure_ascii=False).encode(),
                                          catalog, atlas)

    def test_committed_language_is_the_compiler_output(self):
        if not TRAIN.exists():
            self.skipTest("pinned TRAIN aggregate not present")
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "language.json"
            subprocess.run(["python3", str(COMPILER), str(TRAIN), "--output", str(out)],
                           cwd=ROOT, check=True, capture_output=True, text=True)
            self.assertEqual(out.read_bytes(), LANGUAGE.read_bytes())


if __name__ == "__main__":
    unittest.main()
