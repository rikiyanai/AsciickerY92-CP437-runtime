"""Offline units for the single True FF run selector (FL-4902, map FL-4900).

Compiles the production selector include (true_ff_run_select.glslinc, with
true_ff_keyframe.glslinc and true_ff_roles.glslinc) as C++ and drives it over
the compiled run table (outline_language.v1.json run_table).

Checks:
- generic fallback: over the existing tracks and junctions, tff_select_run with
  TFF_ROLE_GENERIC returns today's primary idiom and phase advance (the deleted
  representation() / junction_representation() searches over the deleted
  keyframe packing, kept here as a C++ reference); the width substitute equals
  the deleted representation_substitute() on every plain site, and the unit
  prints how many joint states differ (the deleted search reached across
  overlapping joint tracks; a row now carries its own detail pair);
- role rows win over the generic rows; a role with rows for the language never
  falls back; a role without rows uses the generic rows;
- TRAIN fail-closed: the compiler rejects a run row whose sequence is not in
  the mode-qualified TRAIN admission map, and a water row without geometry;
- ordered-run member choice depends only on (contour id, arc ordinal).

No Godot, GPU, capture, replay or proof infrastructure: this establishes the
selector arithmetic over the compiled table only, not renderer behaviour.
"""
import copy
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from scripts.compile_true_ff_outline_language import compile_run_table

ROOT = Path(__file__).resolve().parents[2]
COMPOSITION = ROOT / "godot_project/render/ascii/composition"
LANGUAGE = ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json"
CATALOG = ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json"

PRELUDE = r'''
#include <algorithm>
#include <cassert>
#include <cstdint>
#include <iostream>
#include <string>
#include <vector>
typedef uint32_t uint;
using std::max; using std::min;
struct uvec2 { uint x,y; };
struct uvec4 { uint x,y,z,w; };
struct vec4 { float x,y,z,w; };
struct ivec4 { int x,y,z,w; ivec4(int a=0,int b=0,int c=0,int d=0):x(a),y(b),z(c),w(d) {} };
'''


def selector_cpp() -> str:
    """The production includes as C++: `out T name` becomes a reference."""
    text = "".join((COMPOSITION / name).read_text() for name in
                   ("true_ff_keyframe.glslinc", "true_ff_roles.glslinc", "true_ff_run_select.glslinc"))
    return re.sub(r"\bout (\w+) (\w+)", r"\1& \2", text)


def cpp_float(value) -> str:
    return repr(float(value)) + "f"


def run(test, source: str, marker: str) -> str:
    with tempfile.TemporaryDirectory(prefix="ff-run-select-unit-") as directory:
        binary = str(Path(directory) / "run-select-unit")
        compiled = subprocess.run(["c++", "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                                  input=source, text=True, capture_output=True)
        test.assertEqual(compiled.returncode, 0, compiled.stderr[-3000:])
        result = subprocess.run([binary], capture_output=True, text=True)
        test.assertEqual(result.returncode, 0, result.stdout[-2000:] + result.stderr[-2000:])
        test.assertIn(marker, result.stdout)
        return result.stdout


class TrueFFRunSelect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authored = json.loads(LANGUAGE.read_text())
        cls.glyphs = {row["char"]: row for row in json.loads(CATALOG.read_text())["glyphs"]}
        cls.rows = cls.authored["run_table"]["rows"]
        cls.sequences = []
        for row in cls.rows:
            for text in (row["sequence"], row["substitute"]):
                if text and text not in cls.sequences:
                    cls.sequences.append(text)

    def index(self, text):
        return self.sequences.index(text) if text else -1

    def advance(self, text):
        return sum(self.glyphs[char]["advance_px"] for char in text)

    def table_cpp(self, rows=None):
        rows = self.rows if rows is None else rows
        languages = self.authored["languages"]
        lines = []
        for row in rows:
            key = [row["role_id"], languages[row["language"]], row["flags"], row["detail"]]
            lines.append("{{%s},{%s},{%s},ivec4(%d,%d,%d,0)}" % (
                ",".join(f"{v}u" for v in key), ",".join(cpp_float(v) for v in row["lo"]),
                ",".join(cpp_float(v) for v in row["hi"]),
                self.index(row["sequence"]), self.index(row["substitute"]), row["phase_advance_px"]))
        return ("std::vector<TFFRunRow> table={\n" + ",\n".join(lines) + "};\n"
                "int tff_run_row_count() { return int(table.size()); }\n"
                "TFFRunRow tff_run_row(int index) { return table[index]; }\n")

    def test_terrain_relief_roles_select_authored_hard_runs(self):
        # FL-4902 lane R: the role ids produced by the world-relief helper
        # must reach authored rows in the one production selector. Restricting
        # this production table to the three role rows makes a generic fallback
        # unable to satisfy the check even where a glyph is shared.
        roles = {row["role"]: row["role_id"] for row in self.rows}
        authored_rows = [row for row in self.rows
                         if row["role"] in ("skyline", "ridge", "gully")]
        source = PRELUDE + selector_cpp() + self.table_cpp(authored_rows) + f'''
int main() {{
    ivec4 skyline, ridge, gully;
    assert(tff_select_run({roles["skyline"]}u,1u,vec4{{-20.f,40.f,-5.f,8.f}},TFF_RUN_JOINT_ARMS,
        TFF_BANK_UNKNOWN,uvec2{{17u,TFF_RUN_NO_ORDINAL}},skyline));
    assert(skyline.x=={self.index("／￣")});
    assert(tff_select_run({roles["ridge"]}u,1u,vec4{{-20.f,40.f,-5.f,8.f}},TFF_RUN_JOINT_ARMS,
        TFF_BANK_UNKNOWN,uvec2{{19u,TFF_RUN_NO_ORDINAL}},ridge));
    assert(ridge.x=={self.index("／￣")});
    assert(tff_select_run({roles["gully"]}u,1u,vec4{{-20.f,40.f,5.f,8.f}},TFF_RUN_JOINT_ARMS,
        TFF_BANK_UNKNOWN,uvec2{{23u,TFF_RUN_NO_ORDINAL}},gully));
    assert(gully.x=={self.index("＿／")});
    std::cout<<"terrain relief authored runs checked\\n";
}}
'''
        run(self, source, "terrain relief authored runs checked")

    def test_generic_fallback_equals_the_deleted_keyframe_searches(self):
        # Reference: the deleted TrueFFComposition keyframe/junction packing
        # and the deleted prepare searches, over the same authored tracks.
        geometry = self.authored["geometry_keyframes"]
        languages = self.authored["languages"]
        split = geometry["short_extent_max_px"]
        keyframes = []
        for track in geometry["tracks"]:
            phase = max(self.advance(text) for height in track["heights"] for text in height[1:])
            mask = sum(languages[name] for name in track["languages"])
            bow = track.get("bow_ratio", track.get("bow", [-1000000, 1000000]))
            flags = (1 if "bow" in track or "bow_ratio" in track else 0) | (2 if "bow_ratio" in track else 0)
            height_lo = 0.0
            for height in track["heights"]:
                for detail in range(2):
                    extent_lo = max(0.0 if detail == 0 else split, float(track.get("min_extent_px", 0.0)))
                    extent_hi = split if detail == 0 else 1000000.0
                    if extent_lo >= extent_hi:
                        continue
                    keyframes.append(([track["angle"][0], extent_lo, bow[0], height_lo],
                                      [track["angle"][1], extent_hi, bow[1], height[0]],
                                      self.index(height[detail + 1]), mask, phase, flags))
                height_lo = height[0]
        for junction in geometry["junctions"]:
            mask = sum(languages[name] for name in junction["languages"])
            sectors = sum(1 << arm for arm in junction["arms"])
            keyframes.append(([sectors, 0, 0, 0], [0, 0, 0, 0], self.index(junction["sequence"]), mask, 0, 4))
        reference = "Old old[]={" + ",\n".join(
            "{{%s},{%s},%d,%d,%d,%d}" % (",".join(cpp_float(v) for v in lo), ",".join(cpp_float(v) for v in hi),
                                         idiom, mask, phase, flags)
            for lo, hi, idiom, mask, phase, flags in keyframes) + "};\n"
        advances = "int advance_px[]={" + ",".join(str(self.advance(text)) for text in self.sequences) + "};\n"
        source = PRELUDE + selector_cpp() + self.table_cpp() + r'''
struct Old { float lo[4],hi[4]; int idiom,mask,phase,flags; };
''' + reference + advances + r'''
bool old_in(const Old& k,float angle,float extent,float bow,float height) {
    return tff_keyframe_contains(angle,extent,tff_keyframe_bow(bow,extent,(k.flags&2)!=0),height,
        k.lo[0],k.hi[0],k.lo[1],k.hi[1],k.lo[2],k.hi[2],k.lo[3],k.hi[3]);
}
int old_representation(uint language,bool joint,float angle,float extent,float bow,float height,int& phase) {
    phase=1;
    for(const Old& k:old) {
        if((uint(k.mask)&language)==0u || (k.flags&4)!=0 || ((k.flags&1)!=0)!=joint) continue;
        if(old_in(k,angle,extent,bow,height)) { phase=k.phase;return k.idiom; }
    }
    return -1;
}
int old_substitute(uint language,bool joint,float angle,float extent,float bow,float height,int chosen) {
    int best=-1,width=0x7fffffff;
    for(const Old& k:old) {
        if(k.idiom==chosen || (uint(k.mask)&language)==0u) continue;
        if((k.flags&4)!=0 || ((k.flags&1)!=0)!=joint) continue;
        float b=tff_keyframe_bow(bow,extent,(k.flags&2)!=0);
        if(!tff_keyframe_contains(angle,k.lo[1],b,height,k.lo[0],k.hi[0],k.lo[1],k.hi[1],
            k.lo[2],k.hi[2],k.lo[3],k.hi[3])) continue;
        if(advance_px[k.idiom]<width) { width=advance_px[k.idiom];best=k.idiom; }
    }
    return best;
}
int old_junction(uint language,uint sectors) {
    for(const Old& k:old)
        if((k.flags&4)!=0 && (uint(k.mask)&language)!=0u && uint(k.lo[0])==sectors) return k.idiom;
    return -1;
}
int main() {
    const uvec2 none={0u,TFF_RUN_NO_ORDINAL};
    long states=0,joint_substitute_delta=0;
    for(uint language:{1u,2u,4u,8u}) for(int joint=0;joint<2;++joint)
    for(float angle=-90.0f;angle<=91.0f;angle+=0.5f)
    for(float extent:{4.0f,7.999f,8.0f,12.0f,17.9f,18.0f,25.9f,26.0f,40.0f,68.0f})
    for(float bow:{-12.0f,-2.0f,-1.5f,-0.1f,0.0f,0.1f,1.5f,2.0f,12.0f})
    for(float height:{0.0f,2.8f,4.5f,5.999f,6.0f,7.5f,10.0f,11.0f,13.3f,16.999f}) {
        int phase;
        int chosen=old_representation(language,joint!=0,angle,extent,bow,height,phase);
        ivec4 run;
        bool selected=tff_select_run(TFF_ROLE_GENERIC,language,vec4{angle,extent,bow,height},
            joint?TFF_RUN_JOINT_ARMS:0u,TFF_BANK_UNKNOWN,none,run);
        assert(selected==(chosen>=0));
        if(!selected) continue;
        assert(run.x==chosen && run.z==phase);
        int substitute=old_substitute(language,joint!=0,angle,extent,bow,height,chosen);
        // A joint row keeps only its own detail pair: where the deleted search
        // reached a different track (a corner's same-side shoulder), the row
        // now has no substitute. Never a different substitute.
        if(joint && run.y!=substitute) { assert(run.y==-1);++joint_substitute_delta; }
        else assert(run.y==substitute);
        ++states;
    }
    for(uint language:{1u,2u,4u,8u}) for(uint sectors=1u;sectors<256u;++sectors) {
        int bits=__builtin_popcount(sectors);
        if(bits<3 || bits>4) continue;
        ivec4 run;
        int chosen=old_junction(language,sectors);
        bool selected=tff_select_run(TFF_ROLE_GENERIC,language,vec4{0,0,0,0},sectors,
            TFF_BANK_UNKNOWN,none,run);
        assert(selected==(chosen>=0) && (!selected || (run.x==chosen && run.y==-1)));
    }
    std::cout<<states<<" generic states equal; joint substitute delta "<<joint_substitute_delta<<"\n";
}
'''
        output = run(self, source, "generic states equal")
        print(output.strip())

    def test_role_rows_win_and_authored_roles_never_fall_back(self):
        roles = {row["role"]: row["role_id"] for row in self.rows}
        shoreline = [row for row in self.rows if row["role"] == "shoreline"]
        crest = [row for row in self.rows if row["role"] == "crest"]
        self.assertTrue(shoreline and crest)
        self.assertTrue(all(row["language"] == "water" and row["decorations"] == [] for row in shoreline))
        bank_flags = {row["sequence"]: (row["flags"] >> 16) & 3 for row in shoreline}
        self.assertEqual(bank_flags, {"＿": 1, "￣": 2, "―": 3})
        consts = {name: self.index(text) for name, text in
                  (("LOW", "＿"), ("HIGH", "￣"), ("HORIZON", "―"),
                   ("ARCH", "⌒"))}
        source = PRELUDE + selector_cpp() + self.table_cpp() + "".join(
            f"const int {name}={value};\n" for name, value in consts.items()) + f'''
const uint SHORELINE={roles["shoreline"]}u,CREST={roles["crest"]}u;
''' + r'''
int pick(uint role,uint language,vec4 shape,uint sectors,uint bank_side) {
    ivec4 run;
    return tff_select_run(role,language,shape,sectors,bank_side,uvec2{7u,11u},run)?run.x:-2;
}
int main() {
    assert(SHORELINE==TFF_ROLE_SHORELINE && CREST==TFF_ROLE_CREST);
    // The three shoreline rows share one numeric interval. The compiled bank
    // flags select each authored edge instead of making the first row win.
    vec4 level={0.0f,40.0f,0.0f,3.0f};
    assert(pick(TFF_ROLE_GENERIC,8u,level,0u,TFF_BANK_UNKNOWN)==HIGH);
    assert(pick(TFF_ROLE_SHORELINE,8u,level,0u,TFF_BANK_LOWER)==LOW);
    assert(pick(TFF_ROLE_SHORELINE,8u,level,0u,TFF_BANK_UPPER)==HIGH);
    assert(pick(TFF_ROLE_SHORELINE,8u,level,0u,TFF_BANK_HORIZON)==HORIZON);
    // An absent or impossible bank fact fails closed. It must not fall back to
    // the generic high bar, and a side mismatch must not cross-select a row.
    assert(pick(TFF_ROLE_SHORELINE,8u,level,0u,TFF_BANK_UNKNOWN)==-2);
    assert(pick(TFF_ROLE_SHORELINE,8u,level,0u,99u)==-2);
    assert(pick(TFF_ROLE_SHORELINE,8u,level,0u,TFF_BANK_LOWER)==LOW);
    assert(pick(TFF_ROLE_SHORELINE,8u,level,0u,TFF_BANK_UPPER)!=LOW);
    assert(pick(TFF_ROLE_SHORELINE,8u,level,0u,TFF_BANK_HORIZON)!=HIGH);
    // Crest (FL-4902 lane WD): a bent level joint takes the crown, and so
    // does a level crest arm site (the lobe crown; the calm-water wave line
    // is no crest row).
    assert(pick(TFF_ROLE_CREST,8u,vec4{0.0f,40.0f,-80.0f,3.0f},TFF_RUN_JOINT_ARMS,
               TFF_BANK_UNKNOWN)==ARCH);
    assert(pick(TFF_ROLE_CREST,8u,vec4{0.0f,40.0f,0.0f,3.0f},0u,TFF_BANK_UNKNOWN)==ARCH);
    // Authored role, no matching row: no run (no_candidate_admission).
    assert(pick(TFF_ROLE_SHORELINE,8u,vec4{45.0f,40.0f,0.0f,3.0f},0u,TFF_BANK_LOWER)==-2);
    assert(pick(TFF_ROLE_SHORELINE,8u,vec4{0.0f,4.0f,0.0f,3.0f},0u,TFF_BANK_LOWER)==-2);
    // A role without rows for the language, or without rows at all, takes
    // the generic rows of that language. Terrain relief and retained
    // vegetation now have authored hard/plant rows, so they are excluded
    // from this fallback-only set.
    for(uint role:{TFF_ROLE_SHORELINE,TFF_ROLE_CLOUD_LOBE})
        for(uint language:{1u,2u,4u}) for(float angle:{-80.0f,-45.0f,-20.0f,0.0f,20.0f,45.0f,80.0f})
            for(float height:{1.0f,8.0f,15.0f}) {
                vec4 shape={angle,40.0f,0.0f,height};
                assert(pick(role,language,shape,0u,TFF_BANK_UNKNOWN)==
                       pick(TFF_ROLE_GENERIC,language,shape,0u,TFF_BANK_UNKNOWN));
            }
    std::cout<<"role rows checked\n";
}
'''
        run(self, source, "role rows checked")

    def test_new_water_roles_select_authored_materials_and_oriented_tracks(self):
        roles = {
            name: next(row["role_id"] for row in self.rows if row["role"] == name)
            for name in ("shore_break", "waterfall_ledge", "waterfall_strand", "waterfall_impact")
        }
        glyphs = {
            "BREAK_LONG": self.index('~"'),
            "BREAK_SHORT": self.index(",,"),
            "LEDGE": self.index("_,"),
            "STRAND_LONG": self.index("|"),
            "STRAND_SHORT": self.index("!"),
            "IMPACT_FAN": self.index("从"),
            "IMPACT_DROP": self.index("o"),
            "RISING_DIAGONAL": self.index("／"),
            "FALLING_DIAGONAL": self.index("＼"),
        }
        constants = [
            *(f"const uint {name.upper()}={value}u;" for name, value in roles.items()),
            *(f"const int {name}={value};" for name, value in glyphs.items()),
        ]
        source = PRELUDE + selector_cpp() + self.table_cpp() + "".join(constants) + r'''
int pick(uint role,float angle,float extent,float height) {
    ivec4 run;
    return tff_select_run(role,8u,vec4{angle,extent,0.0f,height},0u,
                          TFF_BANK_UNKNOWN,uvec2{0u,0u},run)?run.x:-2;
}
int main() {
    // Every authored candidate is checked at its lower bound and immediately
    // below its upper half-open boundary. Extent 16/24 is the authored split.
    assert(pick(SHORE_BREAK,-20.0f,0.0f,7.0f)==BREAK_SHORT);
    assert(pick(SHORE_BREAK,-20.0f,15.999f,16.999f)==BREAK_SHORT);
    assert(pick(SHORE_BREAK,-20.0f,16.0f,0.0f)==BREAK_LONG);
    assert(pick(SHORE_BREAK,19.999f,100.0f,7.999f)==BREAK_LONG);
    assert(pick(SHORE_BREAK,0.0f,100.0f,8.0f)!=BREAK_LONG);
    assert(pick(SHORE_BREAK,0.0f,10.0f,6.999f)!=BREAK_SHORT);

    assert(pick(WATERFALL_LEDGE,-35.0f,0.0f,8.0f)==LEDGE);
    assert(pick(WATERFALL_LEDGE,34.999f,999.0f,16.999f)==LEDGE);
    assert(pick(WATERFALL_LEDGE,0.0f,40.0f,7.999f)!=LEDGE);

    assert(pick(WATERFALL_STRAND,-90.0f,0.0f,0.0f)==STRAND_SHORT);
    assert(pick(WATERFALL_STRAND,-90.0f,23.999f,16.999f)==STRAND_SHORT);
    assert(pick(WATERFALL_STRAND,-90.0f,24.0f,0.0f)==STRAND_LONG);
    assert(pick(WATERFALL_STRAND,-75.001f,100.0f,16.999f)==STRAND_LONG);
    assert(pick(WATERFALL_STRAND,75.0f,0.0f,0.0f)==STRAND_SHORT);
    assert(pick(WATERFALL_STRAND,75.0f,24.0f,16.999f)==STRAND_LONG);
    assert(pick(WATERFALL_STRAND,90.999f,100.0f,0.0f)==STRAND_LONG);

    assert(pick(WATERFALL_IMPACT,-35.0f,0.0f,5.5f)==IMPACT_DROP);
    assert(pick(WATERFALL_IMPACT,34.999f,15.999f,15.499f)==IMPACT_DROP);
    assert(pick(WATERFALL_IMPACT,-20.0f,16.0f,7.5f)==IMPACT_FAN);
    assert(pick(WATERFALL_IMPACT,19.999f,100.0f,8.499f)==IMPACT_FAN);
    assert(pick(WATERFALL_IMPACT,0.0f,10.0f,15.5f)!=IMPACT_DROP);
    assert(pick(WATERFALL_IMPACT,0.0f,40.0f,8.5f)!=IMPACT_FAN);
    assert(pick(WATERFALL_IMPACT,20.0f,40.0f,8.0f)!=IMPACT_FAN);

    // The waterfall strand keeps the existing water orientation tracks for
    // slanted segments. A falling/rising slash must not become a vertical bar.
    assert(pick(WATERFALL_STRAND,-60.0f,40.0f,3.0f)==RISING_DIAGONAL);
    assert(pick(WATERFALL_STRAND,60.0f,40.0f,3.0f)==FALLING_DIAGONAL);
    assert(pick(WATERFALL_STRAND,-60.0f,40.0f,3.0f)!=STRAND_LONG);
    assert(pick(WATERFALL_STRAND,60.0f,40.0f,3.0f)!=STRAND_LONG);

    // Every new role has a real authored result over the full straight-track
    // angle partition and every generated height rung.
    for(uint role:{SHORE_BREAK,WATERFALL_LEDGE,WATERFALL_STRAND,WATERFALL_IMPACT})
        for(float angle:{-89.0f,-60.0f,-30.0f,-15.0f,-5.0f,5.0f,15.0f,30.0f,
                         45.0f,60.0f,80.0f,90.0f})
            for(float height:{0.0f,4.5f,6.0f,10.0f,13.3f,16.999f})
                assert(pick(role,angle,40.0f,height)>=0);

    // Once a role has rows for water, a miss must stay a miss. It cannot
    // fall through to a generic water row at the same shape.
    for(uint role:{SHORE_BREAK,WATERFALL_LEDGE,WATERFALL_STRAND,WATERFALL_IMPACT}) {
        ivec4 run;
        assert(!tff_select_run(role,8u,vec4{0.0f,40.0f,0.0f,17.0f},0u,
                               TFF_BANK_UNKNOWN,uvec2{0u,0u},run));
        assert(run.x==-1 && run.y==-1);
    }
    std::cout<<"new water roles checked\n";
}
'''
        run(self, source, "new water roles checked")

    def test_vertical_site_pitch_does_not_depend_on_strand_advance(self):
        geometry = (COMPOSITION / "true_ff_contour_geometry.glslinc").read_text()
        units = geometry[geometry.index("float tff_site_units("):]
        units = units[:units.index("\n}") + 2]
        source = PRELUDE + "\n#include <cmath>\nusing std::abs;\n" + units + r'''
int main() {
    // Native widths 4 and 16 do not change vertical row stepping. Test the
    // production function, including the major-axis boundary control.
    for(float dx:{-4.0f,0.0f,4.0f})
        for(float dy:{-17.0f,17.0f}) {
            assert(tff_site_units(dx,dy,4.0f,17.0f)==1.0f);
            assert(tff_site_units(dx,dy,16.0f,17.0f)==1.0f);
        }
    assert(tff_site_units(16.0f,0.0f,4.0f,17.0f)==4.0f);
    assert(tff_site_units(16.0f,0.0f,16.0f,17.0f)==1.0f);
    std::cout<<"vertical phase checked\n";
}
'''
        run(self, source, "vertical phase checked")

    def test_compiler_fails_closed_on_unadmitted_run_sequences(self):
        admitted = {(group["mode"], text) for group in self.authored["groups"] for text in group["sequences"]}
        admitted = {key: {} for key in admitted}
        args = (self.authored, admitted, self.glyphs, self.authored["water_role_admissions"],
                self.authored["material_interiors"])
        # The committed table is exactly the compiler's output.
        self.assertEqual(compile_run_table(*args)["rows"], self.rows)
        # FL-4902 lane WC: spray emits no run row (it is the water_spray
        # decoration); its TRAIN fail-closed check is test_fl4902_true_ff_water_crest_spray.
        for missing in (("empty", "＿"), ("empty", "┬"), ("empty", "⌒ヽ")):
            broken = dict(admitted)
            del broken[missing]
            with self.assertRaises(ValueError, msg=missing):
                compile_run_table(self.authored, broken, *args[2:])
        water = copy.deepcopy(self.authored["water_role_admissions"])
        del water[0]["candidates"][0]["geometry"]
        with self.assertRaises(ValueError):
            compile_run_table(self.authored, admitted, self.glyphs, water, self.authored["material_interiors"])
        unknown = copy.deepcopy(self.authored["water_role_admissions"])
        unknown[0]["role"] = "lagoon"
        with self.assertRaises(ValueError):
            compile_run_table(self.authored, admitted, self.glyphs, unknown, self.authored["material_interiors"])

    def test_ordered_run_member_depends_only_on_contour_and_ordinal(self):
        # A synthetic ordered run of three members (flags bits 8..15) after one
        # generic level row; members are only reachable through the hash.
        base = {"role_id": 1, "language": "water", "detail": 0, "substitute": "",
                "phase_advance_px": 16, "decorations": [], "mode": "empty",
                "lo": [-10.0, 0.0, -1000000.0, 0.0], "hi": [10.0, 1000000.0, 1000000.0, 17.0]}
        rows = [dict(base, flags=3 << 8, sequence="＿"), dict(base, flags=0, sequence="￣"),
                dict(base, flags=0, sequence="―")]
        source = PRELUDE + selector_cpp() + self.table_cpp(rows) + r'''
int pick(uvec2 contour,float angle,float height) {
    ivec4 run;
    assert(tff_select_run(TFF_ROLE_SHORELINE,8u,vec4{angle,30.0f,0.0f,height},0u,
                          TFF_BANK_UNKNOWN,contour,run));
    for(int row=0;row<3;++row) if(table[row].run.x==run.x) return row;
    return -1;
}
int main() {
    int seen[3]={0,0,0};
    for(uint key:{0x13579bdfu,0x2468ace1u,7u}) for(uint ordinal=0u;ordinal<3000u;++ordinal) {
        uvec2 contour={key,ordinal};
        int member=pick(contour,0.0f,3.0f);
        // Shape changes inside the run's interval never move the member.
        assert(member==pick(contour,-9.5f,16.0f) && member==pick(contour,9.5f,0.0f));
        assert(member==int(tff_run_member(key,ordinal,3u)));
        ++seen[member];
    }
    for(int member=0;member<3;++member) assert(seen[member]>2500);
    // No ordinal (joints, junctions): the first member.
    assert(pick(uvec2{0x13579bdfu,TFF_RUN_NO_ORDINAL},0.0f,3.0f)==0);
    // A single row is never a hashed choice.
    assert(tff_run_member(5u,9u,1u)==0u);
    std::cout<<"ordered runs checked\n";
}
'''
        run(self, source, "ordered runs checked")


if __name__ == "__main__":
    unittest.main()
