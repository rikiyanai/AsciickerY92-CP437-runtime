"""Offline units for FF's production space emitter and authored language.

Compiles the pure GLSL integer helper as C++ (ivec2 shim only), then checks
emitted codepoint rows. No Godot, GPU, capture, replay or proof infrastructure.
This does not execute the GPU fitter or establish renderer quality/performance.
"""
import json
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
import re
from scripts.compile_true_ff_outline_language import validate_subject_keyframes

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "godot_project/render/ascii/composition/true_ff_spacing.glslinc"
KEYFRAME = ROOT / "godot_project/render/ascii/composition/true_ff_keyframe.glslinc"
CONTOUR_GEOMETRY = ROOT / "godot_project/render/ascii/composition/true_ff_contour_geometry.glslinc"
LANGUAGE = ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json"
CATALOG = ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json"


class NativeTextRows(unittest.TestCase):
    def test_feature_depth_bands_stay_in_the_production_band_range(self):
        # The production producer alone: every depth maps to a band in
        # 1..TFF_FEATURE_BANDS, nearer depths never get a farther band, and
        # depths >= 256 clamp to band 1 (a ninth band was the old defect).
        feature = (ROOT / "godot_project/render/ascii/features/true_ff_feature_layout.glslinc").read_text()
        producer = re.search(r"int tff_feature_depth_band\(float depth\) \{.*?\n\}", feature, re.S).group()
        bands = re.search(r"const int TFF_FEATURE_BANDS=\d+;", feature).group()
        source = r'''
#include <cassert>
#include <cmath>
using std::floor; using std::log2;
''' + bands + "\n" + producer + r'''
int main() {
    const float depths[]={0.1f,1.f,2.f,4.f,8.f,16.f,32.f,64.f,127.99f,
        128.f,255.99f,256.f,512.f,1000000.f};
    int previous=TFF_FEATURE_BANDS;
    for(float depth:depths) {
        int band=tff_feature_depth_band(depth);
        assert(band>=1 && band<=TFF_FEATURE_BANDS && band<=previous);
        previous=band;
    }
    assert(tff_feature_depth_band(256.f)==1);
}
'''
        with tempfile.TemporaryDirectory(prefix="ff-feature-depth-gain-") as directory:
            binary = str(Path(directory) / "unit")
            compiled = subprocess.run(["c++", "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                                      input=source, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr[-3000:])
            result = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_actor_face_identity_is_pose_local_and_product_scoped(self):
        shader = ROOT / "godot_project/render/ascii/structure/true_ff_gbuffer.glsl"
        # G15 (65e3dfdce): the low 20 bits are the rig part word, bone * 8192 +
        # the face ordinal inside that bone, not the face order of the pose.
        helper = re.search(r"uint actor_face_identity\(uint generation, uint part_word\) \{.*?\n\}",
                           shader.read_text(), re.S)
        self.assertIsNotNone(helper)
        source = r'''
#include <cassert>
using uint=unsigned int;
''' + helper.group() + r'''
int main() {
    for(uint generation=1;generation<=4094;++generation) {
        // Changing the pose bank offset is deliberately not an identity input.
        uint first=actor_face_identity(generation,0);
        uint last=actor_face_identity(generation,1048575);
        assert(first==(generation<<20u));
        assert(last-first==1048575u && last+1u!=0u);
        if(generation<4094) assert(last<actor_face_identity(generation+1u,0));
        assert(actor_face_identity(generation,639)-first==639u);
        // Same bone and ordinal give the same id; another bone never collides.
        uint hand=actor_face_identity(generation,17u*8192u+5u);
        assert(hand==actor_face_identity(generation,17u*8192u+5u));
        assert(hand!=actor_face_identity(generation,18u*8192u+5u));
    }
}
'''
        with tempfile.TemporaryDirectory(prefix="ff-actor-identity-unit-") as directory:
            binary = str(Path(directory) / "identity-unit")
            subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", binary],
                           input=source, text=True, check=True, capture_output=True)
            subprocess.run([binary], check=True, capture_output=True)

    def test_fire_clocks_are_authored_and_sparse_marks_cannot_outlive_anchors(self):
        policy = json.loads(LANGUAGE.read_text())
        admitted = {(g["mode"], text) for g in policy["groups"] for text in g["sequences"]}
        fire = next(s for s in policy["subject_keyframes"] if s["subject"] == "bonfire_flame")
        self.assertEqual(fire["clock"], "world_period")
        self.assertEqual(fire["phase_identity"], "source_id")
        self.assertEqual(fire["period_seconds"], 0.36)
        parts = {p["id"]: p for p in fire["parts"]}
        self.assertEqual([key[0] for key in parts["T0"]["keys_by_tier"][0]], [0, 0.12, 0.24])
        self.assertEqual(parts["K"]["period_seconds"], 0.20)
        self.assertEqual(parts["K"]["alive"], [[0, 0.08]])
        self.assertNotIn("keys", parts["B"])
        validate_subject_keyframes(policy, admitted)
        for mutation in ("zero_period", "phase_identity", "independent_orphan", "late_key",
                         "tier_hole", "run_budget", "phase_salt"):
            candidate = copy.deepcopy(policy)
            subject = next(s for s in candidate["subject_keyframes"] if s["subject"] == "bonfire_flame")
            p = {part["id"]: part for part in subject["parts"]}
            if mutation == "zero_period":
                subject["period_seconds"] = 0
            elif mutation == "phase_identity":
                subject["phase_identity"] = "frame"
            elif mutation == "independent_orphan":
                p["T2"]["alive"] = [[0, 0.30]]
            elif mutation == "late_key":
                p["T0"]["keys_by_tier"][0][-1][0] = 0.36
            elif mutation == "tier_hole":
                p["T0"]["keys_by_tier"][3] = None
            elif mutation == "run_budget":
                p["B"]["idiom_by_tier"][1] = ["ゝ__ノ", "ゝ__ノ"]
            else:
                p["K"]["phase_salt"] = -1
            with self.assertRaises(ValueError, msg=mutation):
                validate_subject_keyframes(candidate, admitted)

    def test_subject_parts_use_one_vocabulary_and_nested_birth_tiers(self):
        policy = json.loads(LANGUAGE.read_text())
        admitted = {(group["mode"], text) for group in policy["groups"]
                    for text in group["sequences"]}
        validate_subject_keyframes(policy, admitted)
        self.assertEqual(policy["budgets"]["optional_shares"], {"effects": 0.25, "sky": 0.15})
        self.assertEqual(policy["budgets"]["tier_rows"], [1.5, 3.5, 7.5])
        self.assertFalse(policy["sky_authoring"]["star_twinkle"])
        for mode, chars in [("empty", "γ☆人"), ("sparse_scatter", "ﾟ｡")]:
            for char in chars:
                self.assertIn((mode, char), admitted)
        smoke = policy["subject_keyframes"][0]
        previous = set()
        for tier in range(4):
            current = {p["id"] for p in smoke["parts"] if p["birth_tier"] <= tier}
            self.assertLessEqual(previous, current)
            previous = current
        # Reject a missing higher-tier part, fill substitution, unadmitted
        # spelling and a sparse mark that survives its contour attachment.
        for mutation in ("tier_hole", "fill", "unknown", "orphan_mark", "key_order"):
            candidate = copy.deepcopy(policy)
            parts = candidate["subject_keyframes"][0]["parts"]
            if mutation == "tier_hole":
                parts[0]["idiom_by_tier"][3] = None
            elif mutation == "fill":
                parts[0]["role"] = "bounded_fill"
            elif mutation == "unknown":
                parts[0]["idiom_by_tier"][0] = ["NOT_ADMITTED"]
            elif mutation == "orphan_mark":
                parts[-1]["alive"] = [[0.4, 1.0]]
            else:
                parts[2]["keys"].reverse()
            with self.assertRaises(ValueError, msg=mutation):
                validate_subject_keyframes(candidate, admitted)

    def test_source_contour_geometry_drives_monotone_text_ladder(self):
        # Exercise the production scalar geometry helper, then emit the real
        # authored shallow-fall track. No GPU/raster evidence is manufactured.
        source = r'''
#include <cmath>
#include <cassert>
#include <iostream>
using std::abs; using std::sqrt; using std::ceil;
using std::floor; using std::min; using std::max;
typedef unsigned int uint;
float atan(float y,float x) { return std::atan2(y,x); }
''' + f'#include "{CONTOUR_GEOMETRY}"\n' + r'''
int main() {
    // Exact reverse coverage rejects a hole between old point samples.
    // On a 12 px arm, disks at 0, 6, 12 with radius 2 cover all three sample
    // sites, but the first disk ends before the second begins.
    auto left=tff_arm_ink_interval(0,0,12,0,0,0,2);
    auto middle=tff_arm_ink_interval(0,0,12,0,6,0,2);
    assert(left.lo<=0 && left.hi<middle.lo);
    auto touching=tff_arm_ink_interval(0,0,12,0,6,0,3);
    auto first=tff_arm_ink_interval(0,0,12,0,0,0,3);
    assert(first.hi==touching.lo);
    auto off=tff_arm_ink_interval(0,0,12,0,6,4,3);
    assert(off.hi<off.lo);
    auto translated=tff_arm_ink_interval(20,30,32,30,26,30,3);
    assert(translated.lo==touching.lo && translated.hi==touching.hi);
    auto reversed=tff_arm_ink_interval(12,0,0,0,0,0,3);
    assert(reversed.lo==1-first.hi && reversed.hi==1-first.lo);
    auto point=tff_arm_ink_interval(4,5,4,5,4,5,3);
    assert(point.lo==0 && point.hi==1);
    // A source joint's signed bow is invariant to edge winding and page
    // translation. Straight/degenerate chords do not invent curvature.
    assert(tff_source_bow(0,8,8,2,16,8)==-6);
    assert(tff_source_bow(16,8,8,2,0,8)==-6);
    assert(tff_source_bow(100,58,108,52,116,58)==-6);
    assert(tff_source_bow(0,8,8,14,16,8)==6);
    assert(tff_source_bow(0,0,8,8,16,16)==0);
    assert(tff_source_bow(0,0,8,8,0,0)==0);
    // Source ownership cannot leak into the next row or past native advance.
    assert(tff_arm_box_fraction(8,8,8,26,0,0,16,17)==0.5f);
    assert(tff_arm_box_fraction(8,8,24,8,0,0,16,17)==0.5f);
    assert(tff_arm_box_fraction(8,8,0,-8,0,0,16,17)==0.5f);
    assert(tff_arm_box_fraction(8,0,8,-8,0,0,16,17)==0);
    assert(tff_arm_box_fraction(8,8,0,0,0,0,16,17)==1);
    assert(tff_arm_box_fraction(18,8,8,8,0,0,16,17)<0);
    for(int x=0;x<=48;++x) {
        auto a=tff_contour_geometry(0,1,48,16,float(x),100);
        auto b=tff_contour_geometry(48,16,0,1,float(x),-100);
        if(std::abs(a.angle-b.angle)>1e-5 || std::abs(a.height-b.height)>1e-5) return 1;
        std::cout<<a.angle<<" "<<a.extent<<" "<<a.height<<"\n";
    }
}
'''
        with tempfile.TemporaryDirectory(prefix="ff-contour-unit-") as directory:
            binary = str(Path(directory) / "contour-unit")
            subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", binary],
                           input=source, text=True, check=True, capture_output=True)
            values = [list(map(float, line.split())) for line in subprocess.check_output(
                [binary], text=True).splitlines()]
        tracks = json.loads(LANGUAGE.read_text())["geometry_keyframes"]["tracks"]
        track = next(t for t in tracks if t["angle"] == [10, 35])
        levels = [next(i for i, height in enumerate(track["heights"])
                       if row[2] < height[0]) for row in values]
        self.assertEqual(levels, sorted(levels))
        self.assertGreaterEqual(len(track["heights"]), 4)
        self.assertEqual(set(levels), set(range(len(track["heights"]))))
        self.assertAlmostEqual(values[0][0], values[-1][0])
        self.assertAlmostEqual(values[0][1], values[-1][1])
        row = "".join(track["heights"][level][1] for level in levels)
        self.assertEqual(row, "".join(height[1] * levels.count(index)
                                    for index, height in enumerate(track["heights"])))
        measurements = json.loads(LANGUAGE.read_text())["ladder_measurements"]
        for measured in measurements.values():
            self.assertTrue(all(5 <= level["ink_pixels"] <= 7 for level in measured["levels"]))
            heights = [level["height_px"] for level in measured["levels"]]
            self.assertTrue(all(b - a >= 1 for a, b in zip(heights, heights[1:])))
        # This is one geometry/ladder unit, not runtime correspondence or the
        # complete run-budget/continuation acceptance surface.

    def test_authored_keyframes_emit_one_representation_per_geometry_state(self):
        authored = json.loads(LANGUAGE.read_text())
        geometry = authored["geometry_keyframes"]
        admitted = {s for g in authored["groups"] if g["mode"] == "empty"
                    for s in g["sequences"]}
        rules = []
        split = geometry["short_extent_max_px"]
        sequences = []
        for track in geometry["tracks"]:
            lo = 0
            mask = sum(authored["languages"][name] for name in track["languages"])
            self.assertLess(track["angle"][0], track["angle"][1])
            for hi, short, long in track["heights"]:
                self.assertGreater(hi, lo)
                for sequence, extent_lo, extent_hi in [(short, 0, split), (long, split, 1000000)]:
                    extent_lo = max(extent_lo, track.get("min_extent_px", 0))
                    if extent_lo >= extent_hi:
                        continue
                    self.assertIn(sequence, admitted)
                    if sequence not in sequences:
                        sequences.append(sequence)
                    bounds = [*track["angle"], extent_lo, extent_hi,
                              *track.get("bow_ratio", track.get("bow", [-1000000, 1000000])), lo, hi]
                    # Native measured centroids produce fractional thresholds.
                    # Match the uploaded float32 table without C++ list narrowing.
                    flags = int("bow" in track or "bow_ratio" in track) | (2 if "bow_ratio" in track else 0)
                    literals = [str(sequences.index(sequence)), str(mask), str(flags)]
                    literals.extend(f"{float(value)}f" for value in bounds)
                    rules.append("{" + ",".join(literals) + "}")
                lo = hi
            self.assertEqual(lo, 17)
        # Compile the production interval predicate, not a Python copy of it.
        # Repeated emitted rows cannot change representation when only the
        # placement cost changes. Authored slope/height/detail boundaries can.
        source = r'''
#include <cassert>
#include <iostream>
#include <string>
#include <vector>
struct ivec2 { int x,y; ivec2(int a,int b):x(a),y(b) {} };
''' + f'#include "{HELPER}"\n#include "{KEYFRAME}"\n' + r'''
struct Rule { int idiom,mask,flags; float a,b,c,d,e,f,g,h; };
Rule rules[] = {''' + ",\n".join(rules) + r'''};
int choose(float angle,float extent,float bow,float height,int language,bool joint=false) {
    for(auto r:rules) if((r.mask&language) && (r.flags&1)==int(joint) && tff_keyframe_contains(
        angle,extent,tff_keyframe_bow(bow,extent,(r.flags&2)!=0),height,
        r.a,r.b,r.c,r.d,r.e,r.f,r.g,r.h)) return r.idiom;
    return -1;
}
std::u32string sequences[] = {''' + ",".join('U' + json.dumps(s, ensure_ascii=False) for s in sequences) + r'''};
int main() {
    int count=0;
    for(int language:{1,2,4,8}) for(int angle=-90;angle<=90;++angle)
    for(float extent:{4.0f,7.999f,8.0f,12.0f}) for(float bow:{-2.0f,0.0f,2.0f})
    for(float height:{0.0f,5.999f,6.0f,10.999f,11.0f,16.999f}) {
        int id=choose(angle,extent,bow,height,language);
        assert(id>=0);
        std::u32string row;
        auto spaces=tff_space_counts(55,5,11,true);
        for(int i=0;i<spaces.x+spaces.y;++i) row+=char32_t(tff_space_codepoint(spaces,i));
        row+=sequences[id];
        assert(row[0]!=32 && row.back()!=32 && row.back()!=12288);
        assert(row.find(U"   ")==std::u32string::npos); // SPACE_RULE_DOUBLE_HALF_MIDROW_20260928
        // Fit is deliberately absent from choose's inputs: even a near-tie
        // score reversal cannot substitute a different idiom.
        for(float ignored_fit:{0.0001f,0.0f,-0.0001f}) {
            (void)ignored_fit;
            assert(choose(angle,extent,bow,height,language)==id);
        }
        ++count;
    }
    assert(choose(0,12,0,5.999,1)!=choose(0,12,0,6,1));
    assert(choose(0,7.999,0,8,1)!=choose(0,8,0,8,1));
    assert(choose(34.999,12,0,8,1)!=choose(35,12,0,8,1));
    assert(choose(0,12,-1.501,4,2,true)>=0);
    assert(choose(0,12,-1.5,4,2,true)==-1); // no straight fallback for a joint
    assert(sequences[choose(0,24,-6,4,2,true)]==U"⌒ヽ");
    assert(sequences[choose(-10,24,6,4,2,true)]==U"_ノ");
    assert(sequences[choose(10,24,6,4,2,true)]==U"ヽ_");
    assert(sequences[choose(-25,28,-6,4,1,true)]==U"／￣");
    assert(sequences[choose(-25,28,6,4,1,true)]==U"＿／");
    assert(sequences[choose(25,28,-6,4,1,true)]==U"￣＼");
    assert(sequences[choose(25,28,6,4,1,true)]==U"＼＿");
    assert(choose(45,10,5,4,1,true)==-1); // do not consume a tiny branch with a wide corner
    assert(sequences[choose(-45,20,-10,4,1,true)]==U"|￣");
    assert(sequences[choose(45,20,-10,4,1,true)]==U"￣|");
    assert(sequences[choose(45,20,10,4,1,true)]==U"|＿");
    assert(sequences[choose(-45,20,10,4,1,true)]==U"＿|");
    // Uniform projection scale leaves the corner-vs-shoulder relation alone.
    assert(choose(-45,20,10,4,1,true)==choose(-45,40,20,4,1,true));
    assert(tff_keyframe_bow(10,0,true)==0);
    std::cout << count << " authored geometry rows checked\n";
}
'''
        with tempfile.TemporaryDirectory(prefix="ff-keyframe-unit-") as directory:
            binary = str(Path(directory) / "keyframe-unit")
            subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", binary],
                           input=source, text=True, check=True, capture_output=True)
            result = subprocess.run([binary], check=True, capture_output=True, text=True)
            self.assertIn("authored geometry rows checked", result.stdout)

    def test_production_spacing_emits_legal_rows(self):
        source = r'''
#include <cassert>
#include <iostream>
#include <vector>
struct ivec2 { int x,y; ivec2(int a,int b):x(a),y(b) {} };
''' + '#include "' + str(HELPER) + '"\n' + r'''
int main() {
    int tested=0;
    for(bool leading : {false,true}) for(int distance=0;distance<=4096;++distance) {
        auto counts=tff_space_counts(distance,5,11,leading);
        bool reachable=false;
        for(int wide=0;wide<=distance/11;++wide) {
            int rem=distance-wide*11;
            if(rem%5==0 && rem/5<=2*wide+(leading?0:2)) reachable=true;
        }
        assert((counts.x>=0)==reachable);
        if(!reachable) continue;
        std::vector<int> row;
        if(!leading) row.push_back(124); // an existing stroke, not a blank
        int advance=0;
        for(int index=0;index<counts.x+counts.y;++index) {
            int cp=tff_space_codepoint(counts,index);
            assert(cp==32 || cp==12288);
            assert(!row.empty() || cp!=32);
            // SPACE_RULE_DOUBLE_HALF_MIDROW_20260928: never three halves.
            assert(row.size()<2 || row.back()!=32 || row[row.size()-2]!=32 || cp!=32);
            row.push_back(cp); advance+=cp==32?5:11;
        }
        row.push_back(124); // every emitted gap is followed by its stroke
        assert(advance==distance);
        assert(row.back()!=32 && row.back()!=12288);
        ++tested;
    }
    assert(tff_space_counts(5,5,11,true).x<0);
    assert(tff_space_counts(10,5,11,true).x<0);
    assert(tff_space_counts(21,5,11,true).x==1);
    assert(tff_space_counts(10,5,11,false).y==2);
    assert(tff_space_counts(83,5,11,false).x<0);
    assert(tff_space_counts(94,5,11,true).x<0);
    int holes=0;
    for(int d=95;d<=4096;++d) holes+=tff_space_counts(d,5,11,true).x<0;
    assert(holes==0);
    std::vector<int> empty_row;
    assert(empty_row.empty());
    std::cout << tested << " legal emitted rows checked\n";
}
'''
        with tempfile.TemporaryDirectory(prefix="ff-row-unit-") as directory:
            binary = str(Path(directory) / "spacing-unit")
            subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", binary],
                           input=source, text=True, check=True, capture_output=True)
            result = subprocess.run([binary], check=True, capture_output=True, text=True)
            self.assertIn("legal emitted rows checked", result.stdout)

    def test_authored_language_is_native_and_bounded(self):
        authored = json.loads(LANGUAGE.read_text())
        catalog = json.loads(CATALOG.read_text())
        glyphs = {g["codepoint"]: g for g in catalog["glyphs"]}
        # G7 product text uses the same native font, including letters/digits
        # absent from the contour corpus. Capability must not substitute a
        # different glyph or silently drop a HUD line for printable ASCII.
        self.assertTrue(set(range(32, 127)).issubset(glyphs))
        self.assertEqual(catalog["source_font_sha256"],
                         "592bf6be803ed76311841b95a10b20db3df0ec693a15f858757d47420b9b3a28")
        for cp in range(33, 127):
            self.assertTrue(glyphs[cp]["has_ink"])
            self.assertGreater(glyphs[cp]["advance_px"], 0)
            self.assertEqual(int(glyphs[cp]["advance_px"]), glyphs[cp]["advance_px"])
        self.assertEqual(authored["space_codepoints"], [32, 12288])
        self.assertEqual(authored["row_step_px"], 17)
        self.assertEqual(glyphs[32]["advance_px"], 5)
        self.assertEqual(glyphs[12288]["advance_px"], 11)
        self.assertEqual({g["mode"] for g in authored["groups"]},
                         {"empty", "bounded_fill", "sparse_scatter"})
        for group in authored["groups"]:
            if group["mode"] == "bounded_fill":
                self.assertEqual(group["boundary"], "touch_contour_or_taper")
            for row in group["sequences"]:
                self.assertLessEqual(len(row), 4)
                self.assertNotIn("‾", row)
                self.assertFalse(row.startswith(" "))
                self.assertNotIn("  ", row)
                self.assertFalse(row.endswith((" ", "　")))
                for char in row:
                    self.assertIn(ord(char), glyphs, repr(char))
                    glyph = glyphs[ord(char)]
                    x0, y0, x1, y1 = glyph["ink_bounds"]
                    self.assertGreaterEqual(x0, 0, repr(char))
                    self.assertLessEqual(x1, glyph["advance_px"], repr(char))
                    self.assertGreaterEqual(y0, 0, repr(char))
                    self.assertLessEqual(y1, 17, repr(char))
        budgets = authored["budgets"]
        self.assertEqual(budgets["frame_ink_max"], 0.053)
        self.assertEqual(budgets["occupancy_max"], 0.65)
        self.assertEqual(budgets["horizontal_run_max"], 4)
        self.assertEqual(budgets["bottom_continuation_min"], 0.55)
        self.assertEqual(budgets["continuation_distance_px"], 3)

if __name__ == "__main__":
    unittest.main()
