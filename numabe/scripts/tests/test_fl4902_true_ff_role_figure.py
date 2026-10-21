"""FL-4902 lane F: the figure/vehicle role producer, executing production GLSL as C++.

No Godot launch or visual claim. The scalar harness compiles
true_ff_role_figure.glslinc (with the shared role ids and the source contract
it includes) and checks each role rule, that the role is anchored on the
source face's world identity only, and that its wire constants still match
their GDScript owners. A glslangValidator compile checks the include as GLSL.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
FIGURE = PROJECT / "render/ascii/composition/true_ff_role_figure.glslinc"
ROLES = PROJECT / "render/ascii/composition/true_ff_roles.glslinc"
PARAMS = PROJECT / "render/ascii/structure/true_ff_frame_params.glslinc"
SCENE_DB = PROJECT / "render/scene/SceneGeometryDB.gd"
WORLD = PROJECT / "scripts/World.gd"
RACE_CAR = PROJECT / "assets/world_mesh/race_car_faces.json"


def expand(path):
    return re.sub(r'^\s*#include "res://([^\"]+)"\s*$',
                  lambda m: expand(PROJECT / m[1]), path.read_text(), flags=re.M)


def role_ids():
    return {name: int(value) for name, value in re.findall(
        r"const uint TFF_ROLE_(\w+) = (\d+)u;", ROLES.read_text())}


def kind_ids():
    return {name: int(value) for name, value in re.findall(
        r"const uint TFF_KIND_(\w+) = (\d+)u;", PARAMS.read_text())}


def structural_cell():
    text = PARAMS.read_text()
    start = text.index("struct StructuralCell")
    return text[start:text.index("};", start) + 2]


# The scalar C++ shim shared with the limb run-row unit.
from scripts.tests.test_fl4902_true_ff_limb_stroke import VEC_SHIM  # noqa: E402

HARNESS = VEC_SHIM

MAIN = r'''
int main() {
    StructuralCell c;
    while (std::cin >> c.region.x >> c.region.y >> c.region.z >> c.region.w
            >> c.meta.x >> c.meta.y >> c.meta.z >> c.world.x >> c.world.y >> c.world.z
            >> c.geo.x >> c.geo.z >> c.contour_a.w >> c.contour_b.w) {
        uint role = tff_role_figure(c);
        if (role != tff_figure_role_of(c.region.x, c.region.y, c.meta.x, tff_fig_part(c)))
            return 2;
        std::cout << role << "\n";
    }
}
'''


@pytest.fixture(scope="module")
def producer(tmp_path_factory):
    compiler = shutil.which("clang++") or shutil.which("g++")
    assert compiler, "C++ compiler required"
    kinds = "\n".join(f"const uint TFF_KIND_{name} = {value}u;" for name, value in kind_ids().items())
    source = (HARNESS + structural_cell() + "\n" + kinds + "\n" + ROLES.read_text()
              + "\n" + re.sub(r"\bout (\w+) (\w+)", r"\1& \2", expand(FIGURE)) + MAIN)
    directory = tmp_path_factory.mktemp("ff-role-figure")
    path = directory / "figure.cpp"
    binary = directory / "figure"
    path.write_text(source)
    subprocess.run([compiler, "-std=c++17", str(path), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    roles = {value: name for name, value in role_ids().items()}
    kind = kind_ids()

    def run(cells):
        rows = []
        for cell in cells:
            region = cell.get("region", (0, 0, 0, 0))
            edge = kind[cell.get("edge", "SILHOUETTE")]
            anchor_meta = cell.get("meta_y", 0)
            paint = cell.get("paint", 0)
            world = cell.get("world", (0.0, 0.0, 0.0))
            geo = cell.get("geo", (0.0, 0.0))
            contour = cell.get("contour", (0, 0))
            rows.append(" ".join(map(str, (*region, edge, anchor_meta, paint, *world, *geo, *contour))))
        result = subprocess.run([str(binary)], input="\n".join(rows) + "\n",
                                capture_output=True, text=True, check=True)
        return [roles[int(value)] for value in result.stdout.split()]

    return run


ACTOR = (128 << 24) | 7       # render_source_actor_pose_id(7)
AIRPLANE = (1 << 24) | 42     # VSB_DYNAMIC_KIND_IDS &"airplane"
AMBIENT = (11 << 24) | 9001   # VSB_DYNAMIC_KIND_IDS &"ambient_mesh"
STATIC = 1234                 # kind byte 0: retained static object
ACTOR_FACE = (3 << 20) | (5 * 8192 + 17)  # generation 3, Head bone part word
FAMILY_ACTOR, FAMILY_DYNAMIC, FAMILY_STATIC = 5 + 1, 3 + 1, 2 + 1


def test_actor_outline_is_silhouette_and_object_boundary_only(producer):
    region = (ACTOR, 237, ACTOR_FACE + 1, FAMILY_ACTOR)
    assert producer([{"region": region, "edge": edge}
                     for edge in ("SILHOUETTE", "BOUNDARY", "CREASE", "SHADOW")]) == [
        "BODY_OUTLINE", "BODY_OUTLINE", "GENERIC", "GENERIC"]


def test_vehicle_body_for_airplane_kind_and_race_car_materials(producer):
    cells = []
    for region in ((AIRPLANE, 200, 5, FAMILY_DYNAMIC), (AIRPLANE, 202, 9, FAMILY_DYNAMIC),
                   (AMBIENT, 218, 1, FAMILY_DYNAMIC), (AMBIENT, 219, 700, FAMILY_DYNAMIC)):
        for edge in ("SILHOUETTE", "BOUNDARY", "CREASE", "SHADOW"):
            cells.append({"region": region, "edge": edge})
    assert producer(cells) == ["VEHICLE_BODY"] * 3 + ["GENERIC"] + (
        ["VEHICLE_BODY"] * 3 + ["GENERIC"]) * 3


def test_other_owners_stay_generic(producer):
    cells = [
        # Ambient meshes that are not the race car: weapon, airplane fracture shard.
        {"region": (AMBIENT, 200, 3, FAMILY_DYNAMIC)},
        {"region": (AMBIENT, 237, 3, FAMILY_DYNAMIC)},
        # A static face with the race-car material column is not a vehicle.
        {"region": (STATIC, 218, 3, FAMILY_STATIC)},
        # Other mover kinds and materials: a bird kind without the bird
        # material, a spider face without a spider material, rain 12.
        {"region": ((2 << 24) | 1, 230, 3, FAMILY_DYNAMIC)},
        {"region": ((10 << 24) | 1, 230, 3, FAMILY_DYNAMIC)},
        # Limb and spider materials on another kind are not limbs.
        {"region": (AMBIENT, 239, 3, FAMILY_DYNAMIC)},
        {"region": (AMBIENT, 213, 3, FAMILY_DYNAMIC)},
        {"region": (STATIC, 246, 3, FAMILY_STATIC)},
        {"region": ((12 << 24) | 1, 0, 3, FAMILY_DYNAMIC)},
    ]
    assert producer(cells) == ["GENERIC"] * len(cells)


def test_tutorial_car_materials_reach_body_and_wheel_roles(producer):
    # FL-4831: colour differences must not drop a variant out of the
    # AAHub-informed vehicle grammar. Part identity, not colour, picks wheels.
    cells = []
    for material in (214, 215, 216, 217, 219):
        for part in range(5):
            for edge in ("SILHOUETTE", "BOUNDARY", "CREASE", "SHADOW"):
                cells.append({"region": (AMBIENT, material, (part << 20) + 1, FAMILY_DYNAMIC), "edge": edge})
    body = ["VEHICLE_BODY"] * 3 + ["GENERIC"]
    wheel = ["VEHICLE_WHEEL"] * 2 + ["GENERIC"] * 2
    assert producer(cells) == (body + wheel * 4) * 5
    # Old aliased colours are NOT a new global vehicle admission rule.
    assert producer([{"region": (AMBIENT, m, 1, FAMILY_DYNAMIC)}
                     for m in (200, 201, 203, 213, 220)]) == ["GENERIC"] * 5


SPIDER = (10 << 24) | 77      # VSB_DYNAMIC_KIND_IDS &"spider"
BIRD = (2 << 24) | 3004       # VSB_DYNAMIC_KIND_IDS &"bird_face"


def test_spider_legs_and_bird_wings_are_limb_strokes(producer):
    # OPERATOR_DECISION_SPIDER_BIRD_STICK_MOTIF_20260928: every non-shadow
    # mark of a limb is its one axis stroke; a shadow chord stays generic.
    cells = []
    for region in ((SPIDER, 239, 81, FAMILY_DYNAMIC), (SPIDER, 248, 125, FAMILY_DYNAMIC),
                   (BIRD, 213, 1, FAMILY_DYNAMIC), (BIRD, 213, 2, FAMILY_DYNAMIC)):
        for edge in ("SILHOUETTE", "BOUNDARY", "CREASE", "SHADOW"):
            cells.append({"region": region, "edge": edge})
    assert producer(cells) == (["LIMB_STROKE"] * 3 + ["GENERIC"]) * 4


def test_spider_body_and_saddle_keep_the_body_outline(producer):
    cells = []
    for material in (246, 247, 238):
        for edge in ("SILHOUETTE", "BOUNDARY", "CREASE", "SHADOW"):
            cells.append({"region": (SPIDER, material, 5, FAMILY_DYNAMIC), "edge": edge})
    assert producer(cells) == (["BODY_OUTLINE"] * 2 + ["GENERIC"] * 2) * 3


def test_no_source_edge_means_no_role_even_if_the_anchor_is_an_actor(producer):
    # meta.y carries the anchor tap's source kind 5 (actor) and material; the
    # source-edge region is absent, so identity is unknown and no role is given.
    anchor = 1 | (237 << 8) | (5 << 24)
    assert producer([{"region": (0, 0, 0, 0), "meta_y": anchor},
                     {"region": (0, 218, 0, 0), "meta_y": anchor}]) == ["GENERIC", "GENERIC"]


def test_role_is_anchored_on_world_identity_not_screen_facts(producer):
    # Same source face, edge kind and material; everything that moves with the
    # camera (anchor world point, range, object-budget slot, paint pixel
    # address, canonical contour hashes) varies. The role must not.
    base = [(ACTOR, 237, ACTOR_FACE + 1, FAMILY_ACTOR), (AIRPLANE, 200, 5, FAMILY_DYNAMIC),
            (AMBIENT, 219, 800, FAMILY_DYNAMIC), (STATIC, 210, 77, FAMILY_STATIC),
            (SPIDER, 239, 81, FAMILY_DYNAMIC), (SPIDER, 246, 4, FAMILY_DYNAMIC),
            (BIRD, 213, 1, FAMILY_DYNAMIC)]
    frames = [
        {"paint": 0, "world": (0.0, 0.0, 0.0), "geo": (1.0, 0.0), "contour": (1, 1)},
        {"paint": 123456, "world": (133.0, 11.0, -72.0), "geo": (48.5, 3.0), "contour": (99, 21)},
        {"paint": 7, "world": (-4.25, 0.5, 9.0), "geo": (900.0, 17.0), "contour": (0xFFFFFFF0, 4)},
    ]
    results = [producer([{"region": region, **frame} for region in base]) for frame in frames]
    assert results[0] == ["BODY_OUTLINE", "VEHICLE_BODY", "VEHICLE_BODY", "GENERIC",
                          "LIMB_STROKE", "BODY_OUTLINE", "LIMB_STROKE"]
    assert all(result == results[0] for result in results)


def test_actor_part_word_does_not_change_the_body_role(producer):
    # Every rig part (hips, head, hand) of one actor is the same body outline;
    # the part word and generation only name the face.
    faces = [(g << 20) | (bone * 8192 + ordinal) for g, bone, ordinal in
             ((1, 0, 0), (1, 5, 3), (2, 10, 511), (255, 63, 8191))]
    assert producer([{"region": (ACTOR, 237, face + 1, FAMILY_ACTOR)} for face in faces]) == [
        "BODY_OUTLINE"] * len(faces)


def test_wire_constants_match_their_owners():
    source = FIGURE.read_text()
    constants = dict((name, int(value)) for name, value in re.findall(
        r"const uint TFF_FIG_(\w+) = (\d+)u;", source))
    registry = SCENE_DB.read_text()
    table = registry[registry.index("const VSB_DYNAMIC_KIND_IDS"):]
    table = table[:table.index("}")]
    kinds = {name: int(value) for name, value in re.findall(r'&"(\w+)": (\d+)', table)}
    assert constants["MOVER_AIRPLANE"] == kinds["airplane"]
    assert constants["MOVER_AMBIENT_MESH"] == kinds["ambient_mesh"]
    world = WORLD.read_text()
    body = int(re.search(r"const RACE_CAR_MAT_BODY := (\d+)", world)[1])
    accent = int(re.search(r"const RACE_CAR_MAT_ACCENT := (\d+)", world)[1])
    assert (constants["RACE_CAR_MAT_BODY"], constants["RACE_CAR_MAT_ACCENT"]) == (body, accent)
    for name in ("MUSTANG", "NISSAN", "BASIC", "WHEEL"):
        assert constants["CAR_MAT_" + name] == int(re.search(
            rf"const CAR_MAT_{name} := (\d+)", world)[1])
    # The baked car uses exactly these two columns, and no other World.gd
    # material constant reuses them.
    car = json.loads(RACE_CAR.read_text())
    rows = car["rows"]
    assert {int(rows[face * 16 + 3]) for face in range(car["face_count"])} == {body, accent}
    others = {int(value) for name, value in re.findall(r"const (\w*MAT\w*) := (\d+)\b", world)
              if name not in ("RACE_CAR_MAT_BODY", "RACE_CAR_MAT_ACCENT")}
    assert not others & {body, accent}
    contract = expand(FIGURE)
    assert "const uint RENDER_SOURCE_ACTOR_POSE_KIND = 128u;" in contract


def test_only_supported_roles_are_produced():
    # Face features and drapery are effects-owned marks, not contour roles.
    # Wheel and wing come from the rigid part id (FL-4902 lane VP; rules in
    # test_fl4902_true_ff_vehicle_parts.py).
    source = FIGURE.read_text()
    body = source[source.index("uint tff_figure_role_of("):source.index("uint tff_role_figure(")]
    produced = set(re.findall(r"TFF_ROLE_(\w+)", body))
    assert produced == {"GENERIC", "BODY_OUTLINE", "VEHICLE_BODY", "VEHICLE_WHEEL",
                        "VEHICLE_WING", "LIMB_STROKE"}
    assert produced <= set(role_ids())


def test_gaze_permission_requires_an_authored_eye_not_a_figure_role(tmp_path):
    compiler = shutil.which("clang++") or shutil.which("g++")
    assert compiler, "C++ compiler required"
    kinds = "\n".join(f"const uint TFF_KIND_{name} = {value}u;" for name, value in kind_ids().items())
    main = r'''
int main() {
    for (uint role = 0u; role <= TFF_ROLE_LIMB_STROKE; ++role) {
        if (tff_fig_allows_gaze_anchor(role, false)) return 2;
        if (tff_fig_allows_gaze_anchor(role, true) != (role == TFF_ROLE_FACE_EYE)) return 3;
    }
}
'''
    path, binary = tmp_path / "gaze.cpp", tmp_path / "gaze"
    path.write_text(HARNESS + structural_cell() + "\n" + kinds + "\n" + ROLES.read_text()
                    + "\n" + expand(FIGURE) + main)
    subprocess.run([compiler, "-std=c++17", str(path), "-o", str(binary)],
                   check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], check=True, capture_output=True, text=True)


def test_include_compiles_as_glsl(tmp_path):
    validator = shutil.which("glslangValidator")
    if not validator:
        pytest.skip("glslangValidator not installed")
    shader = "\n".join([
        "#version 450",
        "#define TRUE_FF_PARAMS_BINDING 1",
        expand(PARAMS),
        expand(ROLES),
        expand(FIGURE),
        "layout(local_size_x=1) in;",
        "layout(set=0,binding=0,std430) readonly buffer S { StructuralCell cells[]; } s;",
        "layout(set=0,binding=2,std430) writeonly buffer R { uint roles[]; } r;",
        "void main() { r.roles[gl_GlobalInvocationID.x] = tff_role_figure(s.cells[gl_GlobalInvocationID.x]); }",
    ])
    path = tmp_path / "figure.comp"
    path.write_text(shader)
    result = subprocess.run([validator, "-V", "-S", "comp", "-o", "/dev/null", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
