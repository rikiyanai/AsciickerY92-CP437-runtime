"""FL-4902 lane SB: spider and bird limbs as one stroke (skeleton stick-figure motif).

OPERATOR_DECISION_SPIDER_BIRD_STICK_MOTIF_20260928. No Godot launch or visual
claim. Checks:
- the limb wire constants of the role producer
  (render/ascii/composition/true_ff_role_figure.glslinc TFF_FIG_*) against
  their GDScript owners;
- the TFF_ROLE_LIMB_STROKE run rows: straight rows copy the generic straight
  tracks, joint rows are TRAIN-admitted, and every plain and joint shape has
  a run (the role never falls back and never drops a limb site or knee).
The role producer rules are covered in test_fl4902_true_ff_role_figure.py.
The G-buffer limb ribbon was deleted with the screen-derived pipeline (FL-4907).
"""
import json
import re
import unittest
from pathlib import Path

from scripts.tests.test_fl4902_run_select import PRELUDE, run, selector_cpp

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "godot_project"
FIGURE = PROJECT / "render/ascii/composition/true_ff_role_figure.glslinc"
SCENE_DB = PROJECT / "render/scene/SceneGeometryDB.gd"
WORLD = PROJECT / "scripts/World.gd"
BIRDS = PROJECT / "scripts/visual/BirdFlock3D.gd"
LANGUAGE = PROJECT / "assets/glyphs/true_ff/outline_language.v1.json"
CATALOG = PROJECT / "assets/glyphs/true_ff/saitamaar/catalog.json"

# GLSL vector subset the limb include uses, as C++.
VEC_SHIM = r'''
#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
using std::min; using std::max;
typedef uint32_t uint;
struct vec2 { float x = 0, y = 0; vec2() {} vec2(float a, float b) : x(a), y(b) {} };
struct vec3 { float x = 0, y = 0, z = 0; vec3() {} vec3(float a, float b, float c) : x(a), y(b), z(c) {} };
struct vec4 { float x = 0, y = 0, z = 0, w = 0; vec4() {}
              vec4(float a, float b, float c, float d) : x(a), y(b), z(c), w(d) {} };
struct uvec4 { uint x = 0, y = 0, z = 0, w = 0; };
inline vec2 operator+(vec2 a, vec2 b) { return {a.x + b.x, a.y + b.y}; }
inline vec2 operator-(vec2 a, vec2 b) { return {a.x - b.x, a.y - b.y}; }
inline vec2 operator*(vec2 a, vec2 b) { return {a.x * b.x, a.y * b.y}; }
inline vec2 operator/(vec2 a, vec2 b) { return {a.x / b.x, a.y / b.y}; }
inline vec2 operator+(vec2 a, float s) { return {a.x + s, a.y + s}; }
inline vec2 operator*(vec2 a, float s) { return {a.x * s, a.y * s}; }
inline vec2 operator/(vec2 a, float s) { return {a.x / s, a.y / s}; }
inline vec3 operator+(vec3 a, vec3 b) { return {a.x + b.x, a.y + b.y, a.z + b.z}; }
inline vec3 operator-(vec3 a, vec3 b) { return {a.x - b.x, a.y - b.y, a.z - b.z}; }
inline vec3 operator*(vec3 a, float s) { return {a.x * s, a.y * s, a.z * s}; }
inline float length(vec2 v) { return std::sqrt(v.x * v.x + v.y * v.y); }
'''


def constants(path: Path, prefix: str) -> dict:
    return {name: int(value) for name, value in
            re.findall(rf"const uint {prefix}(\w+) = (\d+)u;", path.read_text())}


def gd_int(path: Path, name: str) -> int:
    return int(re.search(rf"const {name} := (\d+)", path.read_text())[1])


class TrueFFLimbStroke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kinds = {name: int(value) for name, value in re.findall(
            r'&"(\w+)": (\d+)', SCENE_DB.read_text()[SCENE_DB.read_text().index("const VSB_DYNAMIC_KIND_IDS"):]
            .split("}")[0])}
        cls.const = constants(FIGURE, "TFF_FIG_")

    # --- identity -------------------------------------------------------
    def test_wire_constants_match_their_owners(self):
        c = self.const
        self.assertEqual(c["MOVER_BIRD"], self.kinds["bird_face"])
        self.assertEqual(c["MOVER_SPIDER"], self.kinds["spider"])
        self.assertEqual(c["BIRD_MATERIAL"], gd_int(BIRDS, "BIRD_MAT_ID"))
        self.assertEqual(c["BIRD_MATERIAL"], gd_int(WORLD, "BIRD_MAT_SILHOUETTE"))
        for name in ("SEAT", "DARK_LEG", "DARK_BODY", "SILVER_BODY", "SILVER_LEG"):
            self.assertEqual(c["SPIDER_" + name], gd_int(WORLD, "SPIDER_MAT_" + name), name)

    # --- run rows -------------------------------------------------------
    def test_limb_rows_are_single_strokes_and_cover_every_shape(self):
        language = json.loads(LANGUAGE.read_text())
        rows = language["run_table"]["rows"]
        roles = constants(PROJECT / "render/ascii/composition/true_ff_roles.glslinc", "TFF_ROLE_")
        limb = [row for row in rows if row["role"] == "limb_stroke"]
        self.assertTrue(limb)
        self.assertTrue(all(row["role_id"] == roles["LIMB_STROKE"] and row["decorations"] == []
                            and row["language"] == "hard" for row in limb))
        admitted = {text for group in language["groups"] if group["mode"] == "empty"
                    for text in group["sequences"]}
        self.assertTrue(all(row["sequence"] in admitted for row in limb))
        # Straight rows are the generic straight rows verbatim; no outline
        # corner, shoulder or heel idiom is a limb run.
        generic = [row for row in rows if row["role"] == "generic" and row["language"] == "hard"]
        key = ("track", "flags", "detail", "sequence", "substitute", "lo", "hi", "phase_advance_px")
        straight = [row for row in limb if row["flags"] == 0]
        self.assertEqual(sorted({row["track"] for row in straight}), sorted(
            language["limb_stroke_authoring"]["straight_tracks"]))
        for row in straight:
            self.assertTrue(any(all(g[k] == row[k] for k in key) for g in generic), row["track"])
        self.assertFalse({row["sequence"] for row in limb} & {"|￣", "￣|", "|＿", "＿|", "／￣", "＿／",
                                                              "￣＼", "＼＿"})
        joints = [row for row in limb if row["flags"] != 0]
        self.assertEqual({row["sequence"] for row in joints} & {"∧", "∨", "＜", "＞"}, {"∧", "∨", "＜", "＞"})
        # Every plain and joint shape a limb can produce selects a run.
        sequences = []
        for row in rows:
            for text in (row["sequence"], row["substitute"]):
                if text and text not in sequences:
                    sequences.append(text)
        glyphs = {g["char"]: g for g in json.loads(CATALOG.read_text())["glyphs"]}
        lines = []
        for row in rows:
            k = [row["role_id"], language["languages"][row["language"]], row["flags"], row["detail"]]
            lines.append("{{%s},{%s},{%s},ivec4(%d,%d,%d,0)}" % (
                ",".join(f"{v}u" for v in k), ",".join(repr(float(v)) + "f" for v in row["lo"]),
                ",".join(repr(float(v)) + "f" for v in row["hi"]),
                sequences.index(row["sequence"]),
                sequences.index(row["substitute"]) if row["substitute"] else -1, row["phase_advance_px"]))
        table = ("std::vector<TFFRunRow> table={\n" + ",\n".join(lines) + "};\n"
                 "int tff_run_row_count() { return int(table.size()); }\n"
                 "TFFRunRow tff_run_row(int index) { return table[index]; }\n")
        index = {text: sequences.index(text) for text in ("∧", "∨", "＜", "＞", "|￣", "＿／")}
        hard = language["languages"]["hard"]
        source = PRELUDE + selector_cpp() + table + "".join(
            f"const int IDX_{n}={v};\n" for n, v in zip(("UP", "DOWN", "LT", "GT", "CORNER", "HEEL"),
                                                        index.values())) + r'''
int pick(float angle,float extent,float bow,float height,uint sectors) {
    ivec4 run;
    return tff_select_run(TFF_ROLE_LIMB_STROKE,''' + f"{hard}u" + r''',vec4{angle,extent,bow,height},
        sectors,TFF_BANK_UNKNOWN,uvec2{3u,5u},run)?run.x:-2;
}
int main() {
    long plain=0,joints=0;
    for(float angle=-89.75f;angle<=90.0f;angle+=0.5f)
    for(float extent:{3.0f,8.0f,20.0f,40.0f,90.0f})
    for(float height:{0.0f,3.0f,6.0f,9.0f,12.0f,16.9f}) {
        int p=pick(angle,extent,0.0f,height,0u);
        assert(p>=0 && p!=IDX_CORNER && p!=IDX_HEEL);++plain;
        for(float ratio:{-3.0f,-0.5f,-0.21f,-0.1f,0.0f,0.1f,0.21f,0.5f,3.0f}) {
            int j=pick(angle,extent,ratio*extent,height,TFF_RUN_JOINT_ARMS);
            assert(j>=0);++joints;
        }
    }
    // A level knee bent up is a wedge, bent down a vee; steep knees point sideways.
    assert(pick(0.0f,40.0f,-20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_UP);
    assert(pick(0.0f,40.0f,20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_DOWN);
    assert(pick(80.0f,40.0f,20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_LT);
    assert(pick(80.0f,40.0f,-20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_GT);
    assert(pick(-80.0f,40.0f,20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_GT);
    assert(pick(-80.0f,40.0f,-20.0f,8.0f,TFF_RUN_JOINT_ARMS)==IDX_LT);
    // The generic role still draws its outline corner for the same bent shape.
    ivec4 run;
    assert(tff_select_run(TFF_ROLE_GENERIC,''' + f"{hard}u" + r''',vec4{-40.0f,40.0f,-20.0f,8.0f},
        TFF_RUN_JOINT_ARMS,TFF_BANK_UNKNOWN,uvec2{3u,5u},run) && run.x==IDX_CORNER);
    std::cout<<plain<<" plain and "<<joints<<" joint limb states select a run\n";
}
'''
        print(run(self, source, "limb states select a run").strip())


if __name__ == "__main__":
    unittest.main()
