"""FL-4907 RASTER lane: P5 row layout and P6 dispatch checks.

Offline only: the Python reference (scripts/true_ff_feature_layout_ref.py)
states the law; the production include
(godot_project/render/ascii/features/true_ff_feature_layout.glslinc, with the
kept keyframe/roles/run-select/spacing includes) is compiled as C++ and
compared with it; the two shaders are compiled with glslangValidator; the
renderer script is checked statically. No Godot, GPU, capture or proof tool
runs, so nothing here establishes renderer behaviour at runtime.
"""
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import true_ff_feature_layout_ref as ref  # noqa: E402

ASCII = ROOT / "godot_project/render/ascii"
FEATURES = ASCII / "features"
COMPOSITION = ASCII / "composition"
RENDERER = FEATURES / "TrueFFFeatureRenderer.gd"
LAYOUT_GLSL = FEATURES / "true_ff_feature_layout.glsl"
GLYPHS_GLSL = FEATURES / "true_ff_feature_glyphs.glsl"

WATER = 8
HARD = 1
PLANT = 4

PRELUDE = r'''
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <vector>
typedef uint32_t uint;
using std::max; using std::min; using std::abs; using std::floor; using std::sqrt; using std::log2;
struct uvec2 { uint x,y; };
struct uvec4 { uint x,y,z,w; };
struct vec4 { float x,y,z,w; vec4(float a=0,float b=0,float c=0,float d=0):x(a),y(b),z(c),w(d) {} };
struct ivec2 { int x,y; ivec2(int a=0,int b=0):x(a),y(b) {} };
struct ivec4 { int x,y,z,w; ivec4(int a=0,int b=0,int c=0,int d=0):x(a),y(b),z(c),w(d) {}
    int operator[](int i) const { return i==0?x:(i==1?y:(i==2?z:w)); } };
inline float atan(float y,float x) { return std::atan2(y,x); }
'''


def glsl_as_cpp() -> str:
    parts = [(COMPOSITION / name).read_text() for name in (
        "true_ff_keyframe.glslinc", "true_ff_roles.glslinc", "true_ff_run_select.glslinc",
        "true_ff_spacing.glslinc")]
    parts.append((FEATURES / "true_ff_feature_layout.glslinc").read_text())
    return re.sub(r"\bout (\w+) (\w+)", r"\1& \2", "\n".join(parts))


def cpp_float(value) -> str:
    return repr(float(value)) + "f"


def table_cpp(table: ref.Table) -> str:
    lines = []
    for row in table.rows:
        lines.append("{{%s},{%s},{%s},ivec4(%d,%d,%d,0)}" % (
            ",".join(f"{v}u" for v in row.key), ",".join(cpp_float(v) for v in row.lo),
            ",".join(cpp_float(v) for v in row.hi), row.primary, row.substitute, row.phase))
    flags = ",".join(f"{i.flags}u" for i in table.idioms)
    return ("std::vector<TFFRunRow> table={\n" + ",\n".join(lines) + "};\n"
            "int tff_run_row_count() { return int(table.size()); }\n"
            "TFFRunRow tff_run_row(int index) { return table[index]; }\n"
            # Synthetic tables are not pre-indexed like the production
            # catalog. Expose their full span; the real selector still
            # applies each row's role/language/shape predicate unchanged.
            "ivec2 tff_feature_run_span(uint, uint) { return ivec2(0,int(table.size())); }\n"
            f"std::vector<uint> idiom_flags={{{flags}}};\n"
            "uint tff_feature_idiom_flags(int idiom) { return idiom_flags[idiom]; }\n")


def run_cpp(test, body: str, table: ref.Table) -> str:
    if shutil.which("c++") is None:
        test.skipTest("no C++ compiler")
    source = PRELUDE + table_cpp_forward() + glsl_as_cpp() + "\n" + table_cpp(table) + body
    with tempfile.TemporaryDirectory(prefix="ff-feature-layout-") as directory:
        binary = str(Path(directory) / "unit")
        compiled = subprocess.run(["c++", "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                                  input=source, text=True, capture_output=True)
        test.assertEqual(compiled.returncode, 0, compiled.stderr[-4000:])
        result = subprocess.run([binary], capture_output=True, text=True)
        test.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return result.stdout


def table_cpp_forward() -> str:
    """The feature-class constants the include reads from PASSES' ABI."""
    abi = (FEATURES / "true_ff_feature_abi.glslinc").read_text()
    return "\n".join(re.findall(r"^const uint TFF_CLASS_\w+ = \d+u;$", abi, re.M)) + "\n"


def synthetic_table(members: int) -> ref.Table:
    """A one-role table whose rows form an ordered run of `members` members."""
    language = json.loads(ref.LANGUAGE.read_text())
    texts = ["/", "|", "\\"][:members]
    rows = []
    for index, text in enumerate(texts):
        rows.append({"role_id": ref.ROLE_GENERIC, "language": "hard",
                     "flags": (members << ref.RUN_MEMBERS_SHIFT) if index == 0 else 0,
                     "detail": 0, "mode": "empty", "sequence": text, "substitute": "",
                     "lo": [-91.0, 0.0, -1e6, 0.0], "hi": [91.0, 1e6, 1e6, 17.0],
                     "phase_advance_px": 0, "decorations": []})
    language["run_table"] = dict(language["run_table"], rows=rows)
    return ref.Table(language)


def site(x, y, chain=7, seed=0, role=ref.ROLE_GENERIC, language=HARD, depth=10.0,
         tx=16.0, ty=0.0, object_id=5, bank=0):
    return ref.Site(float(x), float(y), float(depth), 0.0, chain, seed,
                    ref.pack_role(role, language, 0, bank), object_id, float(tx), float(ty))


class GapFillLaw(unittest.TestCase):
    def brute(self, gap, leading):
        best = None
        for a in range(0, gap // ref.FULL_ADVANCE + 3):
            for b in range(0, 2 * a + (0 if leading else 2) + 1):
                key = (abs(11 * a + 5 * b - gap), a + b, b)
                if best is None or key < best[0]:
                    best = (key, (a, b))
        return best[1]

    def test_gap_fill_exhaustive_0_to_200(self):
        for leading in (True, False):
            for gap in range(0, 201):
                a, b = ref.gap_fill(gap, leading)
                self.assertGreaterEqual(a, 0)
                self.assertGreaterEqual(b, 0)
                self.assertLessEqual(b, 2 * a + (0 if leading else 2), (gap, leading))
                self.assertEqual((a, b), self.brute(gap, leading), (gap, leading))
                exact = ref.space_counts(gap, leading)
                if exact[0] >= 0:
                    # Exact spellings are the production tff_space_counts ones.
                    self.assertEqual((a, b), exact, (gap, leading))
                    self.assertEqual(11 * a + 5 * b, gap)

    def test_no_leading_half_space_and_at_most_two_adjacent(self):
        for gap in range(0, 201):
            a, b = ref.gap_fill(gap, True)
            if a == 0:
                self.assertEqual(b, 0, gap)

    def test_glsl_gap_fill_matches_reference(self):
        table = ref.Table()
        out = run_cpp(self, r'''
int main() {
    for(int leading=0;leading<2;++leading) for(int gap=0;gap<=200;++gap) {
        ivec2 r=tff_feature_gap_fill(gap,leading!=0,5,11);
        std::printf("%d %d %d %d\n",leading,gap,r.x,r.y);
    }
}
''', table)
        for line in out.split("\n"):
            if not line:
                continue
            leading, gap, a, b = map(int, line.split())
            self.assertEqual((a, b), ref.gap_fill(gap, bool(leading)), line)

    def test_group_thresholds(self):
        self.assertEqual(ref.group_threshold(ref.last_illegal(False)), 84)
        self.assertEqual(ref.group_threshold(ref.last_illegal(True)), 95)

    def test_operator_two_half_spacing_is_derived_from_slots(self):
        # FL-4512 SPACE_RULE_DOUBLE_HALF_MIDROW_20260928 is an explicit
        # preserved-whitespace decision, not the skill's BBS-collapse law.
        # Execute the actual count and spelling functions, then independently
        # reconstruct their widths and compare the old/new hole populations.
        out = run_cpp(self, r'''
int main() {
    for(int leading=0;leading<2;++leading) for(int gap=0;gap<400;++gap) {
        ivec2 counts=tff_space_counts(gap,5,11,leading!=0);
        int width=0,run=0,longest=0,first=0;
        if(counts.x>=0) for(int i=0;i<counts.x+counts.y;++i) {
            int cp=tff_space_codepoint(counts,i);
            if(i==0) first=cp;
            width+=cp==32?5:11;
            run=cp==32?run+1:0;longest=max(longest,run);
        }
        std::printf("%d %d %d %d %d %d %d\n",leading,gap,counts.x,counts.y,width,longest,first);
    }
}
''', ref.Table())
        holes = {False: [], True: []}
        for line in out.splitlines():
            leading, gap, full, half, width, longest, first = map(int, line.split())
            is_leading = bool(leading)
            if full < 0:
                holes[is_leading].append(gap)
                continue
            # a full spaces supply a slots at row start and a+1 mid-row.
            self.assertLessEqual(half, 2 * (full + (not is_leading)), line)
            self.assertEqual(width, gap, line)
            self.assertLessEqual(longest, 2, line)
            if is_leading:
                self.assertNotEqual(first, 32, line)
        for leading, expected_new, expected_old in ((False, (40, 83), (65, 138)),
                                                     (True, (50, 94), (75, 149))):
            old_holes = [gap for gap in range(400)
                         if not any((gap - 11 * full) % 5 == 0
                                    and (gap - 11 * full) // 5 <= full + (not leading)
                                    for full in range(gap // 11 + 1))]
            self.assertEqual((len(holes[leading]), max(holes[leading])), expected_new)
            self.assertEqual((len(old_holes), max(old_holes)), expected_old)


class GlyphChoice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = ref.Table()

    def test_closed_representative_uses_whole_authored_native_glyph(self):
        # Compile the actual P1 closed-mark owner. Long marks retain the whole
        # authored idiom; sub-cell marks select one whole catalog glyph.
        native = (FEATURES / 'true_ff_native_language.glslinc').read_text()
        buffers = r'''
struct FeatureGlyph { ivec4 metric,atlas,ink; };
std::vector<FeatureGlyph> native_glyphs;
std::vector<uint> representative_words;
ivec4 tff_native_glyph_metric(int glyph) { return native_glyphs[glyph].metric; }
ivec4 tff_native_glyph_ink(int glyph) { return native_glyphs[glyph].ink; }
int tff_native_compact_axis(int, uint) { return -1; }
uint tff_native_palette_count(uint language) {
    uint count=representative_words[(language&15u)*8u];return count<=7u?count:0u;
}
int tff_native_palette_glyph(uint language,uint member) {
    return int(representative_words[(language&15u)*8u+1u+member]);
}
'''
        body = buffers + native + '\nint main() {\n'
        body += 'native_glyphs.resize(65536);representative_words.resize(128);\n'
        dots = {39, 44, 46, 96, 12289, 65344, 65380}
        for language in (1, 2, 4, 8):
            palette = sorted({member for row in self.table.rows if row.key[1] & language
                              for member in self.table.idioms[row.primary].members if member in dots})
            self.assertLessEqual(len(palette), 7)
            body += 'representative_words[%d]=%du;\n' % (language * 8, len(palette))
            for member, cp in enumerate(palette):
                body += 'representative_words[%d]=%du;\n' % (language * 8 + 1 + member, cp)
        for cp, g in self.table.glyphs.items():
            x0, y0, x1, y1 = g['ink_bounds']
            body += ('native_glyphs[%d]={ivec4(%d,%d,%d,%d),ivec4(),ivec4(%d,%d,%d,%d)};\n'
                     % (cp, g['advance_px'], g['bearing_x'], g['bearing_y'], cp,
                        x0, y0, x1 - x0, y1 - y0))
        cases = [(lang, 1., 0., 1., height + .25)
                 for lang in (1, 2, 4, 8) for height in range(17)]
        import math
        cases += [(lang, math.cos(math.radians(angle)), math.sin(math.radians(angle)), extent, 8.5)
                  for lang in (1, 2, 4, 8) for angle in (-80., -45., 0., 45., 80.)
                  for extent in (11., 32.)]
        for lang, tx, ty, extent, height in cases:
            body += ('{ int selected=tff_native_closed_idiom(vec4(%s,%s,0.f,%s),%s,%du,11,false);'
                     'if(selected==-1) return 7;std::printf("%%d\\n",selected); }\n' % (
                         cpp_float(tx), cpp_float(ty), cpp_float(extent), cpp_float(height), lang))
        body += '}\n'
        lines = run_cpp(self, body, self.table).splitlines()
        self.assertEqual(len(lines), len(cases))
        for (lang, tx, ty, extent, height), line in zip(cases, lines):
            selected = int(line)
            if extent < 11.:
                candidates = {member for row in self.table.rows if row.key[1] & lang
                              for member in self.table.idioms[row.primary].members if member in dots}
                expected_cp = min(candidates, key=lambda candidate: (
                    abs((self.table.glyphs[candidate]['ink_bounds'][1]
                         + self.table.glyphs[candidate]['ink_bounds'][3]) * .5 - height), candidate))
                expected = -expected_cp - 2
            else:
                angle = ref.f32_angle(ref.f32(tx), ref.f32(ty))
                expected = ref.select(self.table, 0, lang, (angle, extent, 0., height), 0, 0)
            self.assertEqual(selected, expected, line)
        self.assertIn('tff_native_closed_idiom(source.tangent',
                      (FEATURES / 'true_ff_feature_native.glsl').read_text())

    def test_closed_representative_anchor_is_on_retained_surface(self):
        wrapper = (FEATURES / 'true_ff_feature_extract.glsl').read_text()
        source = (FEATURES / 'true_ff_feature_extract_common.glslinc').read_text()
        self.assertNotIn('void representative(', wrapper + source)  # no late P2 replacement
        shared = (FEATURES / 'true_ff_feature_passes.glslinc').read_text()
        traverse = (FEATURES / 'true_ff_feature_traverse.glslinc').read_text()
        witness = shared.split('TffSite tff_witness_site(', 1)[1].split('float tff_scene_extent(', 1)[0]
        self.assertIn('site.source_world = vec4(r.world,', witness)
        self.assertIn('site.source_normal = vec4(r.normal,', witness)
        self.assertIn('site.source = r.source;', witness)
        self.assertNotIn('n.sphere.xyz', witness)
        self.assertIn('TffSite site=tff_witness_site(', traverse)
        self.assertIn('append_witness(site)', traverse)
        self.assertIn('TFF_PENDING_CLOSED_WITNESS', traverse)
        self.assertIn('TFF_PENDING_SCENE_WITNESS', traverse)
        kernel = (FEATURES / 'true_ff_feature_traverse_witness_kernel.glslinc').read_text()
        self.assertIn('if(lane.budget.x==0u)', kernel)
        self.assertIn('tff_witness_pressure(lane)', kernel)
        self.assertIn('if(!tff_witness_append(site))', kernel)
        extent = source.split('float representative_extent(', 1)[1].split('\nvoid extract(', 1)[0]
        self.assertIn('(hi.x - lo.x) / cam.page.z', extent)
        self.assertIn('(hi.y - lo.y) / cam.page.w', extent)

    def test_source_extent_survives_unit_direction_into_real_selector(self):
        # Exercise the production shape builder and authored selector together.
        # This is an offline source test, not a claim about the rendered art.
        body = 'int main() {\n'
        cases = [(angle, span) for angle in (-73., -27., 2., 27., 73.)
                 for span in (1., 4., 16., 64., 128.)]
        import math
        for angle, span in cases:
            tx, ty = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            body += ('{ vec4 shape=tff_feature_shape(%s,%s,%s,8.f); '
                     'std::printf("%%.3f %%d\\n",shape.y,'
                     'tff_feature_select(0u,1u,shape,0u,0u)); }\n') % (
                         cpp_float(tx), cpp_float(ty), cpp_float(span))
        body += '}\n'
        for (angle, span), line in zip(cases, run_cpp(self, body, self.table).splitlines()):
            extent, chosen = line.split()
            self.assertEqual(float(extent), span)
            # Float angle thresholds can land on either side; compare at the
            # same unit-vector-derived float32 angle as the production helper.
            tx, ty = ref.f32(math.cos(math.radians(angle))), ref.f32(math.sin(math.radians(angle)))
            expected = ref.select(self.table, 0, 1, (ref.f32_angle(tx, ty), span, 0., 8.), 0, 0)
            self.assertEqual(int(chosen), expected)
        sites = (FEATURES / 'true_ff_feature_sites.glsl').read_text()
        self.assertLess(sites.index('float source_extent ='), sites.index('TffClippedDensity span;'))
        self.assertIn('float(coarse_origin + j) * spacing, source_extent', sites)
        native = (FEATURES / 'true_ff_feature_native.glsl').read_text()
        self.assertIn('source.tangent.y,source.tangent.w,height', native)
        prepare = LAYOUT_GLSL.read_text().split('void prepare_node(', 1)[1].split('#include', 1)[0]
        self.assertNotIn('tff_feature_shape(', prepare)  # P5 consumes, not reselects.

    def shapes(self, rng, n):
        for _ in range(n):
            yield (ref.f32(rng.uniform(-90.0, 90.0)), ref.f32(rng.choice([4.0, 12.0, 20.0, 30.0, 40.0, 80.0])),
                   0.0, ref.f32(rng.uniform(0.0, 16.99)))

    def test_glsl_select_matches_reference(self):
        rng = random.Random(4907)
        # Explicit controls keep this comparison sensitive to both admitting
        # an authored recipe and refusing a missing role. A random-hit quota
        # instead measured the now-deleted generic-role fallback.
        cases = [(0, HARD, (0., 16., 0., 8.), 0, 0),
                 (255, HARD, (0., 16., 0., 8.), 0, 0)]
        for shape in self.shapes(rng, 3000):
            role = rng.randrange(0, 30)
            language = rng.choice([1, 2, 4, 8])
            bank = rng.randrange(0, 4)
            seed = rng.randrange(0, 1 << 32)
            cases.append((role, language, shape, bank, seed))
        body = "int main() {\n"
        for role, language, shape, bank, seed in cases:
            body += ("std::printf(\"%%d\\n\",tff_feature_select(%du,%du,vec4(%s),%du,%du));\n"
                     % (role, language, ",".join(cpp_float(v) for v in shape), bank, seed))
        body += "}\n"
        got = [int(v) for v in run_cpp(self, body, self.table).split()]
        want = [ref.select(self.table, *case) for case in cases]
        self.assertEqual(got, want)
        self.assertGreaterEqual(got[0], 0)
        self.assertEqual(got[1], -1)

    def test_glsl_geometry_helpers_match_reference(self):
        rng = random.Random(17)
        tangents = [(rng.uniform(-40, 40), rng.uniform(-40, 40)) for _ in range(200)]
        depths = [rng.uniform(0.1, 600.0) for _ in range(200)]
        body = "int main() {\n"
        for tx, ty in tangents:
            body += "std::printf(\"%%.4f\\n\",tff_feature_angle(%s,%s));\n" % (cpp_float(tx), cpp_float(ty))
        for d in depths:
            body += "std::printf(\"%%d\\n\",tff_feature_depth_band(%s));\n" % cpp_float(d)
        body += "}\n"
        out = run_cpp(self, body, self.table).split()
        for (tx, ty), value in zip(tangents, out[:200]):
            self.assertAlmostEqual(float(value), ref.f32_angle(ref.f32(tx), ref.f32(ty)), places=2)
        for d, value in zip(depths, out[200:]):
            self.assertEqual(int(value), ref.depth_band(ref.f32(d)))

    def test_glsl_role_word_matches_reference(self):
        body = "int main() {\n"
        cases = [(c, lang) for c in range(0, 10) for lang in (1, 2, 4, 8)]
        for c, lang in cases:
            body += "std::printf(\"%%u\\n\",tff_feature_role_word(%du,%du));\n" % (c, lang)
        body += "}\n"
        got = [int(v) for v in run_cpp(self, body, self.table).split()]
        self.assertEqual(got, [ref.role_word(c, lang) for c, lang in cases])
        self.assertEqual(ref.role_word(ref.CLASS_SHORELINE, 1) & ref.ROLE_MASK, ref.ROLE_SHORELINE)
        self.assertEqual((ref.role_word(ref.CLASS_SHORELINE, 1) >> ref.LANGUAGE_SHIFT) & 15, WATER)

    def test_choice_is_pure(self):
        rng = random.Random(1)
        for shape in self.shapes(rng, 500):
            args = (rng.randrange(0, 30), rng.choice([1, 2, 4, 8]), shape, 0, rng.randrange(0, 1000))
            self.assertEqual(ref.select(self.table, *args), ref.select(self.table, *args))

    def test_static_camera_gives_identical_frame_under_any_site_order(self):
        rng = random.Random(9)
        page = ref.Page(640, 360, 3.25, 7.5)
        sites = [site(rng.uniform(0, 630), rng.uniform(0, 350), chain=rng.randrange(1, 9),
                      seed=rng.randrange(0, 64), role=rng.choice([0, 9, 10, 12, 14]),
                      language=rng.choice([1, 2, 4]), tx=rng.uniform(-20, 20), ty=rng.uniform(-20, 20),
                      depth=rng.uniform(2, 200)) for _ in range(400)]
        first = ref.layout(self.table, sites, page)
        self.assertTrue(first)
        for _ in range(3):
            shuffled = list(sites)
            rng.shuffle(shuffled)
            self.assertEqual(sorted(ref.layout(self.table, shuffled, page), key=repr), sorted(first, key=repr))

    def test_member_index_is_finest_ordinal_mod_run_length(self):
        table = synthetic_table(3)
        shape = (0.0, 16.0, 0.0, 8.0)
        for seed in range(0, 50):
            self.assertEqual(table.idioms[ref.select(table, 0, HARD, shape, 0, seed)].text,
                             ["/", "|", "\\"][seed % 3])

    def test_inserting_finer_sites_never_renumbers_existing_ones(self):
        table = synthetic_table(2)
        shape = (0.0, 16.0, 0.0, 8.0)
        # A nested lattice: level L holds finest ordinals k * 2^(4-L).
        coarse = {k * 4: ref.select(table, 0, HARD, shape, 0, k * 4) for k in range(16)}
        finer = {k * 2: ref.select(table, 0, HARD, shape, 0, k * 2) for k in range(32)}
        for ordinal, idiom in coarse.items():
            self.assertEqual(finer[ordinal], idiom)
        # And the same holds through the whole layout: the glyphs of the
        # existing sites are unchanged when finer sites are added far enough
        # away to form separate groups.
        page = ref.Page(4000, 40)
        base = [site(20 + 200 * k, 8, chain=3, seed=4 * k) for k in range(10)]
        extra = [site(120 + 200 * k, 8, chain=3, seed=4 * k + 2) for k in range(10)]
        before = {(i.x, i.codepoint) for i in ref.layout(table, base, page)}
        after = {(i.x, i.codepoint) for i in ref.layout(table, base + extra, page)}
        self.assertTrue(before)
        self.assertLessEqual(before, after)

    def test_no_single_crest_wave(self):
        rng = random.Random(3)
        crest_texts = set()
        for shape in self.shapes(rng, 4000):
            for bank in range(4):
                idiom = ref.select(self.table, ref.ROLE_CREST, WATER, shape, bank, rng.randrange(0, 99))
                if idiom < 0:
                    continue
                text = self.table.idioms[idiom].text
                crest_texts.add(text)
                self.assertNotEqual(text, "～")
                self.assertNotEqual(text, "≈")
                self.assertNotEqual(text, "～～")
                for wave in ref.WAVE_GLYPHS:
                    if wave in text:
                        self.assertEqual(text, ref.CREST_PAIR)
        # The operator-approved pair is now authored; superseded forms must
        # not be required by this test merely because they used to be present.
        crest_rows = {self.table.idioms[r.primary].text for r in self.table.rows if r.key[0] == ref.ROLE_CREST}
        self.assertIn(ref.CREST_PAIR, crest_rows)
        self.assertIn(ref.CREST_PAIR, crest_texts)
        self.assertFalse(crest_rows & {"～", "～～", "≈"})
        self.assertEqual(ref.CREST_PAIR, "\u2248\u2248")
        self.assertEqual(ref.idiom_flags("～"), ref.IDIOM_NOT_CREST_PAIR)
        self.assertEqual(ref.idiom_flags("～～"), ref.IDIOM_NOT_CREST_PAIR)
        self.assertEqual(ref.idiom_flags("≈"), ref.IDIOM_NOT_CREST_PAIR)
        self.assertEqual(ref.idiom_flags("≈≈≈"), ref.IDIOM_NOT_CREST_PAIR)
        self.assertEqual(ref.idiom_flags("≈≈"), 0)

    def test_no_alphanumerics_in_scenery_roles(self):
        from dataclasses import replace

        # Exercise the guard with a deliberate forbidden catalog row. The
        # shipping vocabulary need not contain a policy violation to test
        # that one would be rejected; the unmodified row is its control.
        for role in (ref.ROLE_GENERIC, ref.ROLE_BODY_OUTLINE):
            table = synthetic_table(1)
            table.rows[0] = replace(table.rows[0], key=(role, *table.rows[0].key[1:]))
            args = (role, HARD, (0., 16., 0., 8.), 0, 0)
            address = ref.select(table, *args)
            self.assertGreaterEqual(address, 0)
            table.idioms[address] = replace(table.idioms[address], flags=ref.IDIOM_ALNUM)
            self.assertEqual(ref.select(table, *args), -1)
            body = ('int main(){std::printf("%%d\\n",'
                    'tff_feature_select(%du,1u,vec4(0.f,16.f,0.f,8.f),0u,0u));}' % role)
            self.assertEqual(run_cpp(self, body, table).strip(), '-1')
        rng = random.Random(5)
        for shape in self.shapes(rng, 1500):
            for role in range(0, ref.ROLE_BODY_OUTLINE):
                for language in (1, 2, 4, 8):
                    idiom = ref.select(self.table, role, language, shape, rng.randrange(0, 4), rng.randrange(0, 9))
                    if idiom >= 0:
                        self.assertFalse(re.search(r"[A-Za-z0-9]", self.table.idioms[idiom].text),
                                         (role, self.table.idioms[idiom].text))


class RowLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = ref.Table()
        cls.thresholds = (ref.group_threshold(ref.last_illegal(False)),
                          ref.group_threshold(ref.last_illegal(True)))

    def random_row(self, rng, n, width=1200):
        return [site(rng.uniform(0, width - 20), rng.uniform(1, 15), chain=rng.randrange(1, 6),
                     seed=rng.randrange(0, 32), role=rng.choice([0, 10, 12, 14]),
                     language=rng.choice([1, 2, 4]), tx=rng.uniform(-24, 24), ty=rng.uniform(-24, 24),
                     depth=rng.uniform(2, 300), object_id=rng.randrange(1, 4)) for _ in range(n)]

    def group_outputs(self, result):
        out = {}
        for members, path in zip(result.groups, result.paths):
            key = tuple(sorted(ref.site_order(result.nodes[i].site) for i in members))
            out[key] = [(ref.site_order(result.nodes[i].site), result.nodes[i].origin + c) for i, c in path]
        return out

    def test_rows_are_legal_native_text(self):
        rng = random.Random(11)
        page = ref.Page(1200, 17, 0.0, 17.0)
        placed = 0
        for _ in range(40):
            result = ref.layout_row(self.table, self.random_row(rng, 40), 0, page, self.thresholds)
            pen, first = 0, True
            for path in result.paths:
                for i, c in path:
                    start = result.nodes[i].origin + c
                    self.assertGreaterEqual(start, pen)
                    self.assertGreaterEqual(ref.space_counts(start - pen, first)[0], 0)
                    pen = start + result.nodes[i].advance
                    self.assertLessEqual(pen, page.width)
                    first = False
            for gap, leading, (a, b) in result.gaps:
                self.assertEqual(11 * a + 5 * b, gap)
            placed += sum(len(path) for path in result.paths)
        self.assertGreater(placed, 200)

    def test_group_result_depends_only_on_its_own_sites(self):
        rng = random.Random(21)
        page = ref.Page(1200, 17, 0.0, 17.0)
        compared = 0
        for _ in range(60):
            sites = [site(150 * g + 20 + rng.uniform(0, 30), rng.uniform(1, 15), chain=rng.randrange(1, 6),
                          seed=rng.randrange(0, 32), role=rng.choice([0, 10, 12, 14]),
                          language=rng.choice([1, 2, 4]), tx=rng.uniform(-24, 24), ty=rng.uniform(-24, 24),
                          depth=rng.uniform(2, 300)) for g in range(7) for _ in range(3)]
            before = self.group_outputs(ref.layout_row(self.table, sites, 0, page, self.thresholds))
            changed = list(sites)
            k = rng.randrange(len(changed))
            s = changed[k]
            changed[k] = ref.Site(s.x + rng.uniform(-6, 6), s.y, s.depth, s.q, s.chain, s.seed + 1,
                                  s.role_word, s.object_id, s.tx + rng.uniform(-3, 3), s.ty)
            after = self.group_outputs(ref.layout_row(self.table, changed, 0, page, self.thresholds))
            touched = {ref.site_order(changed[k]), ref.site_order(s)}
            for key, output in after.items():
                if touched & set(key):
                    continue
                if key in before:
                    self.assertEqual(output, before[key])
                    compared += 1
        self.assertGreater(compared, 100)

    def test_each_group_equals_its_solve_in_isolation(self):
        rng = random.Random(31)
        page = ref.Page(1200, 17, 0.0, 17.0)
        for _ in range(30):
            result = ref.layout_row(self.table, self.random_row(rng, 30), 0, page, self.thresholds)
            self.assertGreater(len(result.groups), 0)
            for members, path in zip(result.groups, result.paths):
                alone = ref.solve_group([result.nodes[i] for i in members])
                self.assertEqual(path, [(members[k], c) for k, c in alone])

    def test_row_binning_uses_one_native_page_lattice(self):
        page = ref.Page(100, 100, 0.0, 4.25)
        self.assertEqual((page.x0, page.top0), (0.0, 0.0))
        self.assertEqual(ref.row_of(17.0, page.top0), 1)
        self.assertEqual(ref.row_of(16.99, page.top0), 0)
        moved = ref.Page(100, 100, 0.75, 4.5)
        self.assertEqual((moved.x0, moved.top0), (page.x0, page.top0))


class StaticChecks(unittest.TestCase):
    def test_native_catalog_widths_and_ink_coverage_use_production_predicates(self):
        """CPU predicate execution with the actual catalog; not GPU admission."""
        catalog = json.loads((ASCII.parents[1] / 'assets/glyphs/true_ff/saitamaar/catalog.json').read_text())
        glyphs = {item['char']: item for item in catalog['glyphs']}
        self.assertEqual([glyphs[c]['advance_px'] for c in '. /≈／'], [3, 5, 8, 7, 16])
        self.assertEqual(glyphs['　']['advance_px'], 11)
        functions = []
        for filename, name in [('true_ff_native_row_admission.glslinc', 'tff_native_advance_valid'),
                               ('true_ff_coverage_law.glslinc', 'tff_coverage_ink_intersects')]:
            source = (FEATURES / filename).read_text()
            start = source.index('bool ' + name + '(')
            opening = source.index('{', start)
            depth, end = 1, opening + 1
            while depth:
                depth += (source[end] == '{') - (source[end] == '}')
                end += 1
            functions.append(source[start:end])
        widths = ','.join(str(g['advance_px']) for g in catalog['glyphs'])
        comma = glyphs['、']['ink_bounds']
        program = '\n'.join(functions) + f'''
int main() {{
    int widths[] = {{{widths}}};
    for (int width: widths) if (!tff_native_advance_valid(width, 0)) return 1;
    if (!tff_native_advance_valid(7, 7)) return 2; // required ≈≈
    if (tff_native_advance_valid(0, 0) || tff_native_advance_valid(-1, 0)
        || tff_native_advance_valid(7, -1) || tff_native_advance_valid(7, 2147483646)) return 3;
    if (tff_coverage_ink_intersects(16, {comma[0]}, {comma[0] + comma[2]}, 22, 33)) return 4;
    if (!tff_coverage_ink_intersects(16, {comma[0]}, {comma[0] + comma[2]}, 11, 22)) return 5;
    if (tff_coverage_ink_intersects(16, 3, 3, 11, 22)) return 6;
    if (tff_coverage_ink_intersects(16, 0, 3, 19, 22)) return 7;
    if (!tff_coverage_ink_intersects(16, -2, 3, 11, 16)) return 8; // native overhang
    if (!tff_coverage_ink_intersects(16, 1, 20, 22, 27)) return 9; // authored internal gap
}}
'''
        if shutil.which('c++') is None:
            self.skipTest('no C++ compiler')
        with tempfile.TemporaryDirectory(prefix='ff-native-metrics-') as directory:
            binary = str(Path(directory) / 'unit')
            compiled = subprocess.run(['c++', '-std=c++17', '-x', 'c++', '-', '-o', binary],
                                      input=program, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([binary], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        native = (FEATURES / 'true_ff_feature_native.glsl').read_text()
        self.assertIn('tff_native_advance_valid(g.metric.x,advance) || offset!=advance', native)
        self.assertNotIn('g.metric.x!=int(pc.native.x)', native)
        host = (FEATURES / 'TrueFFFeaturePasses.gd').read_text()
        self.assertIn('minimum_advance <= 0', host)
        self.assertNotIn('int(language.get("minimum_advance", -1)) != 5', host)
        self.assertIn('bool tff_coverage_ink_intersects(',
                      (FEATURES / 'true_ff_coverage_law.glslinc').read_text())
        for filename in ('true_ff_feature_owed_common.glslinc', 'true_ff_feature_coverage.glslinc'):
            self.assertIn('tff_coverage_ink_intersects(', (FEATURES / filename).read_text())

    def test_scene_bound_lifetime_predicates_use_source_semantics(self):
        """Execute the production predicates, not a copied policy function."""
        shader = (FEATURES / 'true_ff_scene_admission.glslinc').read_text()
        functions = []
        for name in ('tff_scene_bound_is_lifetime', 'tff_scene_frame_bound_is_exact'):
            start = shader.index('bool ' + name + '(')
            opening = shader.index('{', start)
            depth, end = 1, opening + 1
            while depth:
                depth += (shader[end] == '{') - (shader[end] == '}')
                end += 1
            functions.append(shader[start:end])
        if shutil.which('c++') is None:
            self.skipTest('no C++ compiler')
        program = r'''
#include <cstdint>
using uint = uint32_t;
struct TffFeatureInstance { struct { uint z; } identity; };
''' + '\n'.join(functions) + r'''
int main() {
    struct Case { uint kind,subtype; bool retained,view; };
    const Case cases[] = {
        {1,0,true,true}, {2,0,true,true}, {3,1,true,true},
        {3,2,false,false}, {3,3,false,true}, {3,4,false,true},
        {3,5,true,true}, {3,6,false,false}, {3,7,false,true},
        {6,6,false,false}, {7,0,false,true}, {7,1,false,true}
    };
    for (const auto &c:cases) {
        TffFeatureInstance source{{c.kind | (c.subtype << 8)}};
        if(tff_scene_bound_is_lifetime(source)!=c.retained) return 1;
        if(tff_scene_frame_bound_is_exact(source)!=c.view) return 2;
    }
}
'''
        with tempfile.TemporaryDirectory(prefix='ff-bound-lifetime-') as directory:
            binary = str(Path(directory) / 'unit')
            compiled = subprocess.run(['c++', '-std=c++17', '-x', 'c++', '-', '-o', binary],
                                      input=program, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([binary], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('floatBitsToUint(b.hi.w)!=scene_nodes[at].seed.z', shader)
        self.assertNotIn('floatBitsToUint(b.hi.w)!=scene.identity.w', shader)

    def test_p5_shared_certificate_requires_one_exact_owner(self):
        shader = LAYOUT_GLSL.read_text()
        functions = []
        for name in ('native_certified', 'fold_site', 'same_fold', 'shadow_site',
                     'ordinary_representative_site', 'same_representative_semantics',
                     'native_member', 'same_certificate', 'group_certificates'):
            start = shader.index(name + '(')
            start = shader.rfind('\n', 0, start) + 1
            opening = shader.index('{', start)
            depth, end = 1, opening + 1
            while depth:
                depth += (shader[end] == '{') - (shader[end] == '}')
                end += 1
            functions.append(shader[start:end])
        abi = (FEATURES / 'true_ff_native_row_admission.glslinc').read_text()
        flags = '\n'.join(re.findall(
            r'^const uint TFF_NATIVE_(?:CERTIFIED|SHARED_MEMBER|OCCLUDED_MEMBER|OCCLUSION_OWNER) = .*;',
            abi, re.M))
        adapter = r'''
struct FeatureSite { vec4 position;uvec4 key;vec4 tangent,source_world,source_normal;uvec4 source,identity; };
struct Sites { std::vector<FeatureSite> items; } sites;
struct Rows { std::vector<uint> items; } row_sites;
struct Slots { std::vector<uvec2> items; } site_slots;
struct Push { vec4 lattice; } pc;
bool equal(uvec4 a,uvec4 b){return a.x==b.x&&a.y==b.y&&a.z==b.z&&a.w==b.w;}
bool equal(vec4 a,vec4 b){return a.x==b.x&&a.y==b.y&&a.z==b.z&&a.w==b.w;}
bool all(bool v){return v;}
const uint LANES=64u;
uint row_incomplete=0u;
void atomicOr(uint& target,uint value){target|=value;}
void layout_sync(){}
'''
        body = flags + adapter + '\n'.join(functions) + r'''
int main(){
    FeatureSite owner{};
    owner.position=vec4(25.f,8.f,1.f,0.f);
    owner.key={1u,uint(-42),TFF_NATIVE_CERTIFIED<<16u,9u};
    owner.tangent=vec4(1.f,0.f,22.f,1.f);
    owner.source={205u,0u,8u<<8u,0u};
    owner.identity={9u,22u,TFF_CLASS_AUTHORED_STROKE,3u};
    FeatureSite member=owner;member.key.z|=TFF_NATIVE_SHARED_MEMBER<<16u;
    if(!same_certificate(owner,member)) return 1;
    auto wrong=member;wrong.tangent.z+=5.f;if(same_certificate(owner,wrong)) return 2;
    wrong=member;wrong.position.y+=17.f;if(same_certificate(owner,wrong)) return 3;
    wrong=member;wrong.key.y+=1u;if(same_certificate(owner,wrong)) return 4;
    wrong=member;wrong.identity.w+=1u;if(same_certificate(owner,wrong)) return 5;
    wrong=member;wrong.key.z=0u;if(same_certificate(owner,wrong)) return 6;
    sites.items={owner,member};row_sites.items={0u,1u};site_slots.items={{0u,0u},{0u,1u}};
    group_certificates(0u,2u,0u);
    if(row_incomplete || site_slots.items[1].y!=0u) return 7;
    sites.items={owner,owner};row_incomplete=0u;group_certificates(0u,2u,0u);
    if(!row_incomplete) return 8;
    sites.items={member};row_incomplete=0u;group_certificates(0u,1u,0u);
    if(!row_incomplete) return 9;
    std::puts("shared certificate controls=9");
}
'''
        self.assertEqual(run_cpp(self, body, ref.Table()).strip(), 'shared certificate controls=9')

    def test_p3_reference_rejects_foreign_depth_jumps_in_both_projections(self):
        import numpy as np
        import true_ff_feature_passes_ref as passes

        for ortho in (False, True):
            cam = passes.make_camera((0., 0., 5.), (0., 0., 0.), ortho=ortho,
                                     width=5., height=5.)
            def ndc(depth):
                world = cam.eye + cam.forward * depth
                clip = cam.world_to_clip @ np.append(world, 1.)
                return clip[2] / clip[3]
            pp = passes.Prepass(np.full((5, 5), ndc(5.), np.float32),
                                np.full((5, 5), 8, np.uint32),
                                np.ones((5, 5), np.uint32),
                                np.full((5, 5), 22, np.uint32))
            pp.depth[2, 2] = ndc(2.)
            pp.object_id[2, 2], pp.face_id[2, 2] = 7, 11
            rear = passes.Site(2.5, 2.5, 4., 0, 1, passes.CREASE, 0, 0, 7, (1., 0.), 0.)
            self.assertFalse(passes.visible(cam, pp, rear), ortho)
            # Keep object identity equal while only the P0 face changes.
            pp.object_id[:, :] = 7
            self.assertFalse(passes.visible(cam, pp, rear), ortho)
            rear.depth = 2.
            self.assertTrue(passes.visible(cam, pp, rear), ortho)

    def test_p3_slope_uses_p0_surface_identity_before_neighbor_depth(self):
        """Execute the production P3 predicate with a scalar P0 adapter.

        This synthetic-fixture checks the visibility decision, not a Godot
        frame. The unchanged shader admits rear sites when an isolated near
        texel's unrelated neighbours become a false surface-slope allowance.
        """
        if shutil.which("c++") is None:
            self.skipTest("no C++ compiler")
        shader = (FEATURES / "true_ff_feature_passes.glslinc").read_text()
        predicate = shader[shader.index('bool tff_same_p0_surface('):shader.index('#ifndef TFF_WITNESS_COMMIT')]
        live = (FEATURES / 'true_ff_live_feature_source.glslinc').read_text()
        predicate = live[live.index('bool tff_live_figure_metadata('):live.index('#include "res://render/ascii/features/true_ff_live_sources')] + predicate
        predicate = predicate.replace('s.pos.xy', 'vec2(s.pos.x,s.pos.y)')
        predicate = predicate.replace('cam.page.xy', 'vec2(cam.page.x,cam.page.y)')
        predicate = predicate.replace('texelFetch(prepass_primitive, pa, 0).xy', 'xy(texelFetch(prepass_primitive, pa, 0))')
        predicate = predicate.replace('texelFetch(prepass_primitive, pb, 0).xy', 'xy(texelFetch(prepass_primitive, pb, 0))')
        program = r'''
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
using uint=uint32_t;
using std::abs;using std::min;using std::max;
struct vec2 { float x,y;vec2(float v):x(v),y(v){} vec2(float a,float b):x(a),y(b){} };
struct vec4 { float x,y,z,w; };
struct ivec2 { int x,y;ivec2(int v):x(v),y(v){} ivec2(int a,int b):x(a),y(b){} };
struct uvec4 { uint x,y,z,w; };
struct uvec2 {uint x,y;};
uvec2 xy(uvec4 a){return {a.x,a.y};}
bool equal(uvec2 a,uvec2 b){return a.x==b.x&&a.y==b.y;}
bool all(bool v){return v;}
struct bvec2 { bool x,y; };
bool any(bvec2 v) { return v.x||v.y; }
bvec2 lessThan(vec2 a,vec2 b) { return {a.x<b.x,a.y<b.y}; }
bvec2 greaterThanEqual(vec2 a,vec2 b) { return {a.x>=b.x,a.y>=b.y}; }
bvec2 lessThan(ivec2 a,ivec2 b) { return {a.x<b.x,a.y<b.y}; }
bvec2 greaterThanEqual(ivec2 a,ivec2 b) { return {a.x>=b.x,a.y>=b.y}; }
ivec2 operator+(ivec2 a,ivec2 b) { return {a.x+b.x,a.y+b.y}; }
ivec2 operator-(ivec2 a,int b) { return {a.x-b,a.y-b}; }
ivec2 operator*(ivec2 a,int b) { return {a.x*b,a.y*b}; }
ivec2 min(ivec2 a,ivec2 b) { return {min(a.x,b.x),min(a.y,b.y)}; }
uint floatBitsToUint(float a) { uint b;std::memcpy(&b,&a,4);return b; }
float uintBitsToFloat(uint a) { float b;std::memcpy(&b,&a,4);return b; }
struct TffSite { vec4 pos;uvec4 ids;uvec4 source{}; };
struct Camera { vec4 page; } cam{{5,5,11,17}};
float depths[5][5];uvec4 identities[5][5];int prepass_ids=0,prepass_primitive=1;
ivec2 tff_prepass_size() { return {5,5}; }
ivec2 tff_prepass_texel(vec2 p) { return {int(std::floor(p.x)),int(std::floor(p.y))}; }
vec2 tff_prepass_texel_centre_px(ivec2 p) { return {p.x+.5f,p.y+.5f}; }
float tff_prepass_depth(ivec2 p) { return depths[p.y][p.x]; }
float tff_view_depth_of_ndc(vec2,float z) { return z; }
uvec4 texelFetch(int texture,ivec2 p,int) { return texture==prepass_ids?identities[p.y][p.x]:uvec4{1u,1u,0u,0u}; }
void flat(float d,uvec4 ids) {
    for(int y=0;y<5;++y) for(int x=0;x<5;++x) { depths[y][x]=d;identities[y][x]=ids; }
}
''' + predicate + r'''
bool site_visible(TffSite s){return tff_site_visible(s);}
int main() {
    TffSite rear{{2.5f,2.5f,4.f,0.f},{1u,0u,0u,7u}};
    // Positive sensitivity: old source grants 3 depth units of false slope.
    flat(5.f,{8u,1u,22u,1u});depths[2][2]=2.f;identities[2][2]={7u,1u,11u,1u};
    if(site_visible(rear)) { std::puts("unrelated-object slope leaked rear site");return 1; }
    // An object id alone is not a surface correspondence.
    flat(5.f,{7u,1u,22u,1u});depths[2][2]=2.f;identities[2][2]={7u,1u,11u,1u};
    if(site_visible(rear)) { std::puts("other-face slope leaked rear site");return 2; }
    // A lone visible source mark and a genuinely nearer mark stay responsive.
    rear.pos.z=2.f;if(!site_visible(rear)) return 3;
    rear.pos.z=1.9f;if(!site_visible(rear)) return 4;
    flat(3.f,{7u,1u,11u,1u});rear.pos.z=4.f;if(site_visible(rear)) return 5;
    // Same-face gradient, not an occluder discontinuity, owns the bias.
    for(int y=0;y<5;++y) for(int x=0;x<5;++x) depths[y][x]=3.f+.004f*(x-2);
    rear.pos.z=3.003f;if(!site_visible(rear)) return 6;
    rear.pos.z=3.006f;if(site_visible(rear)) return 7;
    // Repeated identical P0/site input has the same result without history.
    rear.pos.z=3.003f;for(int frame=0;frame<100;++frame) if(!site_visible(rear)) return 8;
    std::puts("P3 identity/depth synthetic controls=8");
}
'''
        with tempfile.TemporaryDirectory(prefix="ff-p3-identity-") as directory:
            binary = str(Path(directory) / "unit")
            compiled = subprocess.run(["c++", "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
                                      input=program, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([binary], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("controls=8", result.stdout)

    def test_p5_p6_site_address_roundtrip_preserves_all_bits(self):
        # Execute the actual producer expression and consumer argument as C++
        # scalar arithmetic. No GPU buffer allocation or rendered frame.
        layout = LAYOUT_GLSL.read_text()
        glyphs = GLYPHS_GLSL.read_text()
        producer = re.search(r"instance\.paint=(uvec4\([^;]+\));", layout)
        consumer = re.search(r"feature_surface\((instance\.paint\.[^,]+),surface\)", glyphs)
        self.assertIsNotNone(producer)
        self.assertIsNotNone(consumer)
        expression = producer.group(1).replace("sites.items[node.meta.x].key.w", "object_id")
        expression = expression.replace("node.meta.x", "site")
        flags = "\n".join(re.findall(r"^const uint TFF_INSTANCE_\w+=\d+u;", layout, re.M))
        if shutil.which("c++") is None:
            self.skipTest("no C++ compiler")
        program = r'''
#include <cstdint>
#include <cstdio>
using uint = uint32_t;
struct uvec4 { uint x,y,z,w; uvec4(uint a,uint b,uint c,uint d):x(a),y(b),z(c),w(d){} };
struct Instance { uvec4 paint; };
''' + flags + r'''
int main() {
    for (uint site : {0u, 1u, 0xfffffeu, 0xffffffu, 0x1000000u,
                       0x1000001u, 0x1ffffffu, 0xffffffffu}) {
        for (int m=0;m<4;++m) for (bool row_inexact : {false,true}) {
            uint object_id=123u;
            Instance instance{''' + expression + r'''};
            uint decoded=''' + consumer.group(1) + r''';
            // Inexact-row publication was deleted. Rejected rows are named
            // and excluded; surviving instances never claim that old mode.
            uint expected_flags=(m==0?TFF_INSTANCE_FIRST_MEMBER:0u);
            if(decoded!=site || (instance.paint.w&0xffu)!=expected_flags) {
                std::printf("site=%u decoded=%u flags=%u expected=%u\n",
                    site,decoded,instance.paint.w,expected_flags);
                return 1;
            }
        }
    }
}
'''
        program = "#include <initializer_list>\n" + program
        with tempfile.TemporaryDirectory(prefix="ff-site-address-") as directory:
            binary = str(Path(directory) / "unit")
            compiled = subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", binary],
                                      input=program, text=True, capture_output=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([binary], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Paper is now text-order-owned for both HUD and world. HUD flags
        # still choose its flat paper before any world-site lookup; z is not
        # a packed float address or a lookup in the HUD branch.
        self.assertIn("instance_interval=row_paper_interval(index,count,instance);", glyphs)
        hud_branch = glyphs.split('if(hud) {', 1)[1].split('} else {', 1)[0]
        self.assertIn('instance.paint.y', hud_branch)
        self.assertNotIn('feature_surface', hud_branch)
        self.assertIn("TFF_INSTANCE_HUD", glyphs)
        self.assertIn("const INSTANCE_BYTES := 32", RENDERER.read_text())

    def test_dispatches_are_indirect_or_host_count_sized(self):
        source = RENDERER.read_text()
        direct = re.findall(r"compute_list_dispatch\(([^)]*)\)", source)
        self.assertTrue(direct)
        for args in direct:
            self.assertEqual([a.strip() for a in args.split(",")][1:], ["1", "1", "1"], args)
        indirect = re.findall(r"compute_list_dispatch_indirect\(\s*list\s*,\s*_control\s*,\s*(\w+)\)", source)
        self.assertEqual(sorted(set(indirect)), ["CONTROL_COVERAGE_ARGS", "CONTROL_GLYPH_ARGS", "CONTROL_LAYOUT_ARGS", "CONTROL_SITE_ARGS"])
        self.assertNotIn("draw_list", source)
        self.assertIn("STORAGE_BUFFER_USAGE_DISPATCH_INDIRECT", source)

    def test_shaders_bind_set_zero_and_never_query_runtime_length(self):
        for shader in (LAYOUT_GLSL, GLYPHS_GLSL):
            text = shader.read_text()
            for match in re.finditer(r"layout\s*\(([^)]*)\)", text):
                for value in re.findall(r"\bset\s*=\s*(\d+)", match.group(1)):
                    self.assertEqual(value, "0", shader.name)
            code = re.sub(r"//[^\n]*", "", text)
            self.assertIsNone(re.search(r"\.\s*length\s*\(", code), shader.name)

    def test_shaders_include_kept_owners_without_copies(self):
        layout = LAYOUT_GLSL.read_text()
        for name in ("true_ff_run_select.glslinc", "true_ff_roles.glslinc", "true_ff_spacing.glslinc"):
            self.assertIn(f'#include "res://render/ascii/composition/{name}"', layout)
        self.assertIn('#include "res://render/ascii/law/true_ff_surface_paint.glslinc"', GLYPHS_GLSL.read_text())
        include = (FEATURES / "true_ff_feature_layout.glslinc").read_text()
        for symbol in ("ivec2 tff_space_counts(", "bool tff_run_row_matches(", "const uint TFF_ROLE_CREST"):
            self.assertNotIn(symbol, include + layout)

    def test_glyph_paint_takes_no_light_normal_dither_or_history(self):
        code = re.sub(r"//[^\n]*", "", GLYPHS_GLSL.read_text())
        for banned in ("scene_normal", "bayer", "dither", "previous", "history", "fade"):
            self.assertNotIn(banned, code.lower())

    def test_renderer_crest_and_alnum_flags_match_reference(self):
        source = RENDERER.read_text()
        self.assertIn("const IDIOM_ALNUM := %d" % ref.IDIOM_ALNUM, source)
        self.assertIn("const IDIOM_NOT_CREST_PAIR := %d" % ref.IDIOM_NOT_CREST_PAIR, source)
        self.assertIn("0xFF5E, 0x2248", source)
        self.assertIn('const CREST_PAIR := "\\u2248\\u2248"', source)
        self.assertEqual([ord(c) for c in ref.WAVE_GLYPHS], [0xFF5E, 0x2248])

    def test_site_abi_matches_passes(self):
        passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text()
        self.assertIn("const SITE_BYTES := 112", passes)
        self.assertIn("const SITE_BYTES := 112", RENDERER.read_text())
        site = re.search(r"struct TffSite \{(.*?)\};", (FEATURES / "true_ff_feature_passes.glslinc").read_text(), re.S)
        self.assertEqual(re.findall(r"(vec4|uvec4) (\w+);", site.group(1)),
                         [("vec4", "pos"), ("uvec4", "ids"), ("vec4", "tangent"),
                          ("vec4", "source_world"), ("vec4", "source_normal"),
                          ("uvec4", "source"), ("uvec4", "identity")])
        for shader in (LAYOUT_GLSL, GLYPHS_GLSL):
            text = shader.read_text()
            self.assertIn("vec4 source_world;", text)
            self.assertIn("vec4 source_normal;", text)
            self.assertIn("uvec4 source;", text)

    def test_material_and_paint_are_source_owned(self):
        layout = LAYOUT_GLSL.read_text()
        glyphs = GLYPHS_GLSL.read_text()
        self.assertNotIn("uniform usampler2D prepass_ids", layout)
        self.assertNotIn("textureSize(prepass_ids", layout)
        self.assertNotIn("uniform sampler2D scene_depth", glyphs)
        self.assertNotIn("uniform usampler2D scene_ids", glyphs)
        self.assertNotIn("textureSize(scene_ids", glyphs)
        self.assertIn("languages.words[kind*256u+min(s.source.x,255u)]", layout)
        self.assertIn("surface.world=s.source_world.xyz", glyphs)
        self.assertIn("surface.normal=normalize(s.source_normal.xyz)", glyphs)
        self.assertIn("surface.ids=uvec4(s.key.w,s.source.x,0u,0u)", glyphs)
        self.assertNotIn("surface.ids=uvec4(s.key.w,s.source.x,s.source.y,s.source.z)", glyphs)

    def test_glslang_compiles_both_shaders(self):
        if shutil.which("glslangValidator") is None:
            self.skipTest("glslangValidator missing")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/fl4895_glsl_check.py"),
                                 "render/ascii/features/true_ff_feature_layout.glsl",
                                 "render/ascii/features/true_ff_feature_glyphs.glsl"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
