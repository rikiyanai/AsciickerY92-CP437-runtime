"""Offline units for the True FF open-water crest and the spray decoration
(FL-4902 lanes WC and WD, map FL-4900).

Archive (lane WD): ARCHIVE names the TRAIN rough-water windows (senpaku
resK-171, resK-172, resK-170) and calm references (resK-234, resK-19,
resK-276) whose crest runs were measured in Saitamaar 16 px metrics
(17 px rows). ARCHIVE_ROUGH holds the measured rough-water ranges; the
archive test re-measures them when the archive is present.

Crest: the world crest law was deleted with the screen-derived pipeline
(FL-4907); the archive ranges it was measured against remain.

Angles (lane WD): the compiled crest run rows are driven through the
production selector (tff_select_run, as C++): every plain arm angle and
every joint angle of either bow sign selects a crest row, every authored
crest row is selected somewhere, and the compiler rejects a candidate that
earlier candidates shadow (the former crown turn row).

Spray: checks that SPRAY is no contour role and no run row, that the committed language is the compiler's output, and that the spray
material fails closed without TRAIN admission.

No Godot, GPU, capture, replay or proof infrastructure: this establishes the
arithmetic and the compiled data only, not renderer behaviour.
"""
import copy
import json
import math
from pathlib import Path
import re
import statistics
import unittest

from scripts.compile_true_ff_outline_language import (ASSETS, compile_material_interiors,
                                                      compile_run_table, native_shapes,
                                                      validate_water_role_table)
from scripts.tests.test_fl4902_run_select import PRELUDE, run as run_selector, selector_cpp

ROOT = Path(__file__).resolve().parents[2]
COMPOSITION = ROOT / "godot_project/render/ascii/composition"
LANGUAGE = ASSETS / "outline_language.v1.json"
TRAIN = Path("/Users/r/Projects/unicode-glyph-morphology-explorer/assets/glyphs/corpus/"
             "aahub_aa003_train.sjis_combos.v1.json")
AAHUB = Path("/Users/r/Projects/ascii-art-archive/collections/aahub")

# ---- Archive measurement (FL-4902 lane WD). Windows are [x0, x1) px and
# [row0, row1) art rows after `skip` header lines; each is water only (the
# ship, sky and smoke are outside it). A crest run is a maximal run of stroke
# glyphs in one row (spaces, tone dots and spray dots end it).
ARCHIVE = {
    "resK-171 top": ("senpaku/resK-171.txt", 2, (250, 733, 0, 8)),
    "resK-171 bottom": ("senpaku/resK-171.txt", 2, (0, 450, 17, 23)),
    "resK-172 top": ("senpaku/resK-172.txt", 2, (250, 719, 0, 8)),
    "resK-170 bow wave": ("senpaku/resK-170.txt", 1, (0, 250, 31, 36)),
}
ARCHIVE_CALM = {
    "resK-234 calm": ("senpaku/resK-234.txt", 0, (330, 577, 12, 30)),
    "resK-19 calm": ("senpaku/resK-19.txt", 2, (0, 526, 5, 8)),
    "resK-276 lake": ("senpaku/resK-276.txt", 1, (0, 874, 13, 18)),
}
# Measured by _measure_archive over ARCHIVE (min, max across the windows).
ARCHIVE_ROUGH = {
    "runs_per_1e4_px2": (7.92, 11.11),
    "run_px_median": (16.0, 21.0),
    "run_px_p90": (39.0, 51.0),
    "run_nn_px_median": (18.4, 19.7),
    "gap_px_median": (11.0, 33.0),
    "blank_share": (0.349, 0.625),
}
# Stroke angle bins (degrees, screen y down) of the geometry tracks.
ANGLE_BINS = ((-91, -75), (-75, -35), (-35, -10), (-10, 10), (10, 35), (35, 75), (75, 91))
ROW_PX = 17.0
SPACES = {" ", "　"}
TONE = set(".:;,･・∴∵…‥．：；，")
DOTS = set("ﾟ｡。°゜oｏ○〇")


def _measure_archive(windows):
    """Crest-run statistics per archive window (Saitamaar 16 px advances)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(AAHUB / "Saitamaar.ttf"), 16)
    angle_of = {}

    def glyph_angle(char):
        if char not in angle_of:
            image = Image.new("L", (40, 24), 0)
            ImageDraw.Draw(image).text((4, 2), char, font=font, fill=255)
            ys, xs = np.nonzero(np.array(image) > 127)
            angle_of[char] = None
            if len(xs) >= 3:
                x, y = xs - xs.mean(), ys - ys.mean()
                cxx, cyy, cxy = (x * x).mean(), (y * y).mean(), (x * y).mean()
                spread = math.sqrt(((cxx - cyy) / 2) ** 2 + cxy ** 2)
                if (cxx + cyy) / 2 + spread > 2.0 * max((cxx + cyy) / 2 - spread, 1e-6):
                    angle_of[char] = 0.5 * math.degrees(math.atan2(2 * cxy, cxx - cyy))
        return angle_of[char]

    result = {}
    for name, (path, skip, (x0, x1, r0, r1)) in windows.items():
        lines = (AAHUB / path).read_text(encoding="utf-8").split("\n")[skip:]
        runs, blank, angles = [], 0.0, []
        for row in range(r0, r1):
            x, run = 0.0, None
            cells = []
            for char in lines[row]:
                cells.append((char, x, font.getlength(char)))
                x += font.getlength(char)
            cells.append((" ", x, max(0.0, x1 - x)))
            for char, cx, width in cells:
                inside = min(cx + width, x1) - max(cx, x0)
                if inside <= 0:
                    continue
                if char in SPACES or char in TONE or char in DOTS:
                    blank += inside if char in SPACES else 0.0
                    if run:
                        runs.append(run)
                        run = None
                    continue
                if run is None:
                    run = [row, cx, cx + width, char]
                else:
                    run[2], run[3] = cx + width, run[3] + char
            if run:
                runs.append(run)
        for run in runs:
            angles.extend(a for a in map(glyph_angle, run[3]) if a is not None)
        area = (x1 - x0) * ROW_PX * (r1 - r0)
        widths = [run[2] - run[1] for run in runs]
        centres = [((run[1] + run[2]) / 2, run[0] * ROW_PX) for run in runs]
        nn = [min(math.dist(c, o) for j, o in enumerate(centres) if j != i)
              for i, c in enumerate(centres)]
        gaps = []
        for row in range(r0, r1):
            spans = sorted((run[1], run[2]) for run in runs if run[0] == row)
            gaps.extend(b[0] - a[1] for a, b in zip(spans, spans[1:]))
        result[name] = {
            "runs_per_1e4_px2": round(len(runs) / area * 1e4, 2),
            "run_px_median": round(statistics.median(widths), 1),
            "run_px_p90": round(float(np.percentile(widths, 90)), 1),
            "run_nn_px_median": round(statistics.median(nn), 1),
            "gap_px_median": round(statistics.median(gaps), 1) if gaps else 0.0,
            "blank_share": round(blank / ((x1 - x0) * (r1 - r0)), 3),
            "angle_bins": [sum(lo <= a < hi for a in angles) for lo, hi in ANGLE_BINS],
        }
    return result


def _roles():
    text = (COMPOSITION / "true_ff_roles.glslinc").read_text()
    return {name: int(value) for name, value in
            re.findall(r"const uint TFF_ROLE_(\w+)\s*=\s*(\d+)u;", text)}


class TrueFFWaterCrest(unittest.TestCase):
    def test_archive_measurement_reproduces_the_recorded_ranges(self):
        if not (AAHUB / "Saitamaar.ttf").exists():
            self.skipTest("AAHub archive not present")
        train = json.loads(TRAIN.read_text())["per_slug"] if TRAIN.exists() else {"senpaku": 1}
        self.assertIn("senpaku", train)  # admission uses TRAIN slugs only
        measured = _measure_archive(ARCHIVE)
        for name, values in measured.items():
            print(name, values)
        for key, (lo, hi) in ARCHIVE_ROUGH.items():
            got = [values[key] for values in measured.values()]
            self.assertEqual((min(got), max(got)), (lo, hi), key)
        # Rough water has crest runs at every stroke angle (over the windows).
        bins = [sum(values["angle_bins"][k] for values in measured.values())
                for k in range(len(ANGLE_BINS))]
        self.assertTrue(all(bins), bins)
        calm = _measure_archive(ARCHIVE_CALM)
        for name, values in calm.items():
            print(name, values)
        # The calm lake keeps nearly all water empty of crest runs.
        self.assertLess(calm["resK-276 lake"]["runs_per_1e4_px2"], 1.0)

class TrueFFWaterCrestAngles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.language = json.loads(LANGUAGE.read_text())
        cls.rows = cls.language["run_table"]["rows"]
        cls.sequences = []
        for row in cls.rows:
            for text in (row["sequence"], row["substitute"]):
                if text and text not in cls.sequences:
                    cls.sequences.append(text)

    def table_cpp(self):
        languages = self.language["languages"]
        lines = []
        for row in self.rows:
            key = [row["role_id"], languages[row["language"]], row["flags"], row["detail"]]
            lines.append("{{%s},{%s},{%s},ivec4(%d,%d,%d,0)}" % (
                ",".join(f"{v}u" for v in key), ",".join(repr(float(v)) + "f" for v in row["lo"]),
                ",".join(repr(float(v)) + "f" for v in row["hi"]),
                self.sequences.index(row["sequence"]),
                self.sequences.index(row["substitute"]) if row["substitute"] else -1,
                row["phase_advance_px"]))
        return ("std::vector<TFFRunRow> table={\n" + ",\n".join(lines) + "};\n"
                "int tff_run_row_count() { return int(table.size()); }\n"
                "TFFRunRow tff_run_row(int index) { return table[index]; }\n")

    def test_every_crest_arm_and_joint_angle_selects_a_crest_row(self):
        crest = [index for index, row in enumerate(self.rows) if row["role"] == "crest"]
        self.assertEqual(len(crest), 18)
        water = self.language["languages"]["water"]
        source = PRELUDE + "#include <cstdio>\n#include <set>\n" + selector_cpp() + self.table_cpp() + r'''
int main() {
    std::set<int> reached;
    int checks = 0;
    auto pick = [&](vec4 shape, uint sectors) {
        for (int index = 0; index < tff_run_row_count(); ++index) {
            TFFRunRow row = tff_run_row(index);
            if (row.key.x == TFF_ROLE_CREST && tff_run_row_matches(row, ''' + f"{water}u" + r''', shape,
                    sectors, TFF_BANK_UNKNOWN)) return index;
        }
        return -1;
    };
    for (int step = 0; step <= 3600; ++step) {
        float angle = step == 3600 ? 90.0f : -89.95f + 0.05f * float(step);
        for (float extent : {0.5f, 12.0f, 40.0f, 400.0f}) for (float height : {0.0f, 8.5f, 16.9f}) {
            ivec4 run;
            // A plain arm site (no bow) and a bent joint of either bow sign.
            vec4 arm = {angle, extent, 0.0f, height};
            assert(tff_select_run(TFF_ROLE_CREST, ''' + f"{water}u" + r''', arm, 0u, TFF_BANK_UNKNOWN,
                                  uvec2{3u, 5u}, run) && run.x >= 0);
            reached.insert(pick(arm, 0u));
            for (float ratio : {-3.0f, -0.4f, -0.01f, 0.01f, 0.4f, 3.0f}) {
                vec4 joint = {angle, extent, ratio * extent, height};
                assert(tff_select_run(TFF_ROLE_CREST, ''' + f"{water}u" + r''', joint, TFF_RUN_JOINT_ARMS,
                                      TFF_BANK_UNKNOWN, uvec2{3u, 5u}, run) && run.x >= 0);
                reached.insert(pick(joint, TFF_RUN_JOINT_ARMS));
            }
            ++checks;
        }
    }
    assert(!reached.count(-1));
    std::printf("crest angles %d reached", checks);
    for (int index : reached) std::printf(" %d", index);
    std::printf("\n");
}
'''
        output = run_selector(self, source, "crest angles")
        reached = {int(value) for value in output.split("reached")[1].split()}
        self.assertEqual(reached, set(crest))

    def test_level_crests_select_whole_native_wave_runs(self):
        # FL-4512 D4/D8: test the production selector, not just JSON presence.
        # Growth adds a second native wave; no cloud crown or generic bar.
        water = self.language["languages"]["water"]
        one, pair = (self.sequences.index(text) for text in ("～", "～～"))
        source = PRELUDE + selector_cpp() + self.table_cpp() + f'''
int main() {{
    for(float angle:{{-9.99f,0.f,9.99f}})
    for(float height:{{0.f,4.f,8.5f,16.9f}})
    for(float extent:{{0.5f,16.f,31.999f,32.f,48.f,400.f}}) {{
        ivec4 run;
        assert(tff_select_run(TFF_ROLE_CREST,{water}u,
            vec4{{angle,extent,0.f,height}},0u,TFF_BANK_UNKNOWN,
            uvec2{{53u,127u}},run));
        assert(run.x==(extent<32.f?{one}:{pair}));
        assert(run.y==-1 && run.z==(extent<32.f?16:32));
    }}
    std::cout<<"native wave runs selected\\n";
}}
'''
        run_selector(self, source, "native wave runs selected")
        glyphs = {g["char"]: g for g in json.loads(
            (ASSETS / "saitamaar/catalog.json").read_text())["glyphs"]}
        self.assertEqual(glyphs["～"]["advance_px"], 16)
        shapes = native_shapes(glyphs, (ASSETS / "saitamaar/atlas.png").read_bytes())
        native = shapes["～"][0]
        # Actual mask support in the long run is two identical intact glyphs
        # at native prefix origins. This does not claim scene admission.
        paired = native + [(x + 16, y) for x, y in native]
        self.assertEqual(len(paired), 2 * len(native))
        self.assertTrue(all(0 <= x < 32 and 0 <= y < 17 for x, y in paired))
        evidence = {(e["mode"], e["text"]): e for e in self.language["sequence_evidence"]}
        self.assertIn(("empty", "～～"), evidence)

    def test_compiler_rejects_a_shadowed_crest_candidate(self):
        admitted = {(group["mode"], text): {} for group in self.language["groups"]
                    for text in group["sequences"]}
        catalog = json.loads((ASSETS / "saitamaar/catalog.json").read_text())
        glyphs = {row["char"]: row for row in catalog["glyphs"]}
        water = copy.deepcopy(self.language["water_role_admissions"])
        crest = next(role for role in water if role["role"] == "crest")
        crown = next(c for c in crest["candidates"] if c["selector_key"] == "crest.convex.crown")
        turn = next(c for c in crest["candidates"] if c["selector_key"] == "crest.convex.right_turn")
        # The lane WC rows: the crown over [-35, 35) before the turn over [-35, 0).
        crown["geometry"]["angle"] = [-35, 35]
        turn["geometry"]["angle"] = [-35, 0]
        crest["candidates"].remove(turn)
        crest["candidates"].insert(crest["candidates"].index(crown) + 1, turn)
        with self.assertRaisesRegex(ValueError, "shadowed by earlier candidates: crest: crest.convex.right_turn"):
            compile_run_table(self.language, admitted, glyphs, water, self.language["material_interiors"])
        # The committed table compiles.
        compile_run_table(self.language, admitted, glyphs, self.language["water_role_admissions"],
                          self.language["material_interiors"])


class TrueFFWaterSpray(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.language = json.loads(LANGUAGE.read_text())
        cls.roles = _roles()
        catalog = json.loads((ASSETS / "saitamaar/catalog.json").read_text())
        cls.glyphs = {g["char"]: g for g in catalog["glyphs"]}
        cls.shapes = native_shapes(cls.glyphs, (ASSETS / "saitamaar/atlas.png").read_bytes())
        cls.admitted = {(e["mode"], e["text"]): e for e in cls.language["sequence_evidence"]}
        cls.policy = {"interior_authoring": [rule for rule in cls.language["interior_authoring"]
                                             if rule["name"] == "water_spray"]}

    def spray(self):
        rules = [rule for rule in self.language["material_interiors"] if rule["name"] == "water_spray"]
        self.assertEqual(len(rules), 1)
        return rules[0]

    def test_spray_is_no_contour_role_and_no_run_row(self):
        self.assertNotIn("SPRAY", self.roles)
        self.assertEqual(self.roles["COUNT"], 30)
        rows = self.language["run_table"]["rows"]
        self.assertFalse([row for row in rows if row["role"] == "spray" or row["role_id"] == 5])
        # Deleted duplicate: the repeated line field decoration.
        self.assertNotIn("water_sparse", [rule["name"] for rule in self.language["material_interiors"]])
        self.assertFalse([row for row in rows if "water_sparse" in row["decorations"]])

    def test_committed_spray_rule_is_the_compiler_output(self):
        compiled = compile_material_interiors(self.policy, self.admitted, self.glyphs, self.shapes,
                                              {}, self.language["water_role_admissions"])
        self.assertEqual(compiled, [self.spray()])

    def test_spray_rule_rejects_non_contour_and_unknown_parents(self):
        for roles in (["spray"], ["generic"], ["lagoon"], [], ["crest", "crest"]):
            policy = copy.deepcopy(self.policy)
            policy["interior_authoring"][0]["roles"] = roles
            with self.assertRaises(ValueError, msg=roles):
                compile_material_interiors(policy, self.admitted, self.glyphs, self.shapes, {},
                                           self.language["water_role_admissions"])

    def test_spray_fails_closed_without_train_admission(self):
        water = self.language["water_role_admissions"]
        # No admitted spray glyph: no material.
        admitted = {key: value for key, value in self.admitted.items() if key[0] != "sparse_scatter"}
        with self.assertRaisesRegex(ValueError, "No TRAIN material idiom for water_spray"):
            compile_material_interiors(self.policy, admitted, self.glyphs, self.shapes, {}, water)
        # Admission without TRAIN or water-role-table evidence does not count.
        stripped = copy.deepcopy(self.admitted)
        for (mode, text), record in stripped.items():
            if mode == "sparse_scatter":
                record["evidence"] = [e for e in record["evidence"]
                                      if e["section"] == "operator_semantic_admission"]
        with self.assertRaisesRegex(ValueError, "No TRAIN material idiom for water_spray"):
            compile_material_interiors(self.policy, stripped, self.glyphs, self.shapes, {}, water)
        # The spray candidates themselves are admitted only from the pinned
        # TRAIN sections: remove the chosen glyph from TRAIN and the table fails.
        if not TRAIN.exists():
            self.skipTest("pinned TRAIN aggregate not present")
        source = json.loads(TRAIN.read_text())
        chosen = self.spray()["sequence"]
        for section in ("top_glyphs", "idioms", "bigrams_outline", "trigrams_outline"):
            source[section] = [row for row in source[section] if row.get("g") != chosen]
        authoring = json.loads((ASSETS / "outline_authoring.v1.json").read_text())
        with self.assertRaisesRegex(ValueError, "not in the pinned TRAIN sections: spray"):
            validate_water_role_table(authoring, source, self.glyphs, lambda *args: None)


if __name__ == "__main__":
    unittest.main()
