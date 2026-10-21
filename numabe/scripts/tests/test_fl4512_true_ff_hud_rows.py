"""FL-4512 G7 offline units (synthetic): reserved HUD rows and HUD gap spelling.

Compiles the production GLSL integer helpers as C++ (ivec2 shim only) and checks
them on synthetic page extents. No Godot, GPU, capture, replay or proof
infrastructure. This does not execute the row composer, the preparation pass or
the raster, and it does not establish HUD visual quality.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
COMPOSITION = ROOT / "godot_project/render/ascii/composition"
HUD_ROWS = COMPOSITION / "true_ff_hud_rows.glslinc"
SPACING = COMPOSITION / "true_ff_spacing.glslinc"
HUD_TEXT = COMPOSITION / "TrueFFHudText.gd"

PRELUDE = r'''
#include <cassert>
#include <iostream>
#include <string>
using uint=unsigned int;
struct ivec2 { int x,y; ivec2(int a,int b):x(a),y(b) {} };
'''


def run_cpp(body: str, *includes: Path) -> str:
    source = PRELUDE + "".join(f'#include "{p}"\n' for p in includes) + body
    with tempfile.TemporaryDirectory(prefix="ff-hud-unit-") as directory:
        binary = str(Path(directory) / "hud-unit")
        subprocess.run(["c++", "-std=c++17", "-x", "c++", "-", "-o", binary],
                       input=source, text=True, check=True, capture_output=True)
        return subprocess.run([binary], text=True, check=True, capture_output=True).stdout


class TrueFFHudRows(unittest.TestCase):
    def test_reserved_rows_stay_whole_on_screen_for_every_legal_shift(self):
        # Synthetic 1280x720 viewport, TrueFFPagePhase margin (22, 17), native
        # pitch 17. Page 1324x754 -> 45 rows; always-visible rows are 2..41.
        out = run_cpp(r'''
int main() {
    const int pitch=17, margin=17, view=720, page=view+2*margin;
    const int rows=(page+pitch-1)/pitch;
    ivec2 reserve(3,3);
    assert(tff_hud_first_row(page,view,pitch)==2);
    assert(tff_hud_last_row(view,pitch)==41);
    int slots=0;
    for(int row=0;row<rows;++row) {
        int slot=tff_hud_slot(row,page,view,pitch,reserve);
        bool reserved=tff_hud_row_reserved(row,page,view,pitch,reserve);
        bool expect_reserved=row<=4 || row>=39;
        assert(reserved==expect_reserved);
        if(slot>=0) {
            ++slots;
            assert(reserved); // every HUD row is kept free of world requests
            assert(slot==(row<20?row-2:3+row-39));
            // The raster adds shift - margin; |shift| <= margin.
            for(int shift=-margin;shift<=margin;++shift) {
                int top=row*pitch+shift-margin;
                assert(top>=0 && top+pitch<=view);
            }
        }
    }
    assert(slots==6);
    assert(tff_hud_left(1280+44,1280)==44);
    // (0,0) reserves nothing: world composition is unchanged.
    for(int row=0;row<rows;++row) {
        assert(!tff_hud_row_reserved(row,page,view,pitch,ivec2(0,0)));
        assert(tff_hud_slot(row,page,view,pitch,ivec2(0,0))==-1);
    }
    std::cout<<"reserved rows checked\n";
}
''', HUD_ROWS)
        self.assertIn("reserved rows checked", out)

    def test_short_viewport_gives_top_rows_precedence(self):
        # Synthetic 102 px viewport: always-visible rows 2..5 only.
        out = run_cpp(r'''
int main() {
    const int pitch=17, view=102, page=view+34;
    ivec2 reserve(3,3);
    assert(tff_hud_slot(2,page,view,pitch,reserve)==0);
    assert(tff_hud_slot(4,page,view,pitch,reserve)==2);
    assert(tff_hud_slot(5,page,view,pitch,reserve)==5); // last bottom slot survives
    assert(tff_hud_slot(3,page,view,pitch,reserve)==1);  // top wins the collision
    assert(tff_hud_slot(6,page,view,pitch,reserve)==-1);
    for(int row=0;row<(page+pitch-1)/pitch;++row)
        assert(tff_hud_row_reserved(row,page,view,pitch,reserve));
    std::cout<<"collision checked\n";
}
''', HUD_ROWS)
        self.assertIn("collision checked", out)

    def test_hud_gap_spelling_obeys_the_space_law(self):
        # Native Saitamaar 16 px: U+0020 = 5 px, U+3000 = 11 px. For every
        # minimum width, the chosen gap is the narrowest legal one, spells to
        # exactly that width, never opens a leading gap with U+0020 and never
        # puts three U+0020 together.
        out = run_cpp(r'''
int main() {
    for(int leading=0;leading<2;++leading) for(int minimum=0;minimum<=240;++minimum) {
        int gap=tff_space_min_distance(minimum,5,11,leading!=0);
        assert(gap>=minimum);
        for(int d=minimum;d<gap;++d) assert(tff_space_counts(d,5,11,leading!=0).x<0);
        ivec2 counts=tff_space_counts(gap,5,11,leading!=0);
        assert(counts.x>=0);
        std::u32string row;
        for(int i=0;i<counts.x+counts.y;++i) row+=char32_t(tff_space_codepoint(counts,i));
        int width=0,run=0;
        for(size_t i=0;i<row.size();++i) {
            width+=row[i]==U' '?5:11;
            run=row[i]==U' '?run+1:0;
            assert(run<=2);
            if(leading) assert(!(i==0 && row[i]==U' '));
        }
        assert(width==gap);
    }
    // Word gaps from HUD text: one, two and three source spaces.
    assert(tff_space_min_distance(5,5,11,false)==5);
    assert(tff_space_min_distance(10,5,11,false)==10);
    assert(tff_space_min_distance(15,5,11,false)==16); // U+3000 U+0020, never three U+0020
    // Opening gap to the always-visible edge: four U+3000.
    assert(tff_space_min_distance(44,5,11,true)==44);
    assert(tff_space_counts(44,5,11,true).y==0);
    assert(tff_space_min_distance(-1,5,11,false)==-1);
    std::cout<<"gap spelling checked\n";
}
''', SPACING)
        self.assertIn("gap spelling checked", out)

    def test_hud_line_paper_boxes_tile_the_line_and_keep_white_ink_legible(self):
        # FL-4907 SILENT_ZERO_OUTPUT_TRACE_20260930 (synthetic): the HUD FG is
        # the Control label colour (white by default) and the page paper is a
        # pale sky tone. A line "AB  C" (advances 10, 8, 10; one 10 px word gap)
        # starting at page x 44. The paper boxes from tff_hud_paper_left must
        # tile [44, last end) with no hole at the gap and no overlap, and the
        # two-phase composition of true_ff_feature_glyphs.glsl (box BG first,
        # then ink) must make every ink pixel differ from every non-ink pixel
        # of the line. Ink-only composition (the defect) leaves nothing visible.
        out = run_cpp(r'''
#include <vector>
int main() {
    const int starts[3]={44,54,72}, advances[3]={10,8,10};
    const int paper=0xFFFFFF, fg=0xFFFFFF, bg=0x000000;
    std::vector<int> row(120,paper), ink_only(120,paper), owner(120,-1);
    int pen=0;
    for(int i=0;i<3;++i) {
        int left=tff_hud_paper_left(i==0,starts[i],pen);
        int right=starts[i]+advances[i];
        assert(left<=starts[i]);
        for(int x=left;x<right;++x) { assert(owner[x]<0); owner[x]=i; row[x]=bg; }
        pen=right;
    }
    for(int x=0;x<120;++x) assert((owner[x]>=0)==(x>=44 && x<82));
    for(int i=0;i<3;++i) for(int dx=1;dx<advances[i]-1;dx+=2) {
        row[starts[i]+dx]=fg;
        ink_only[starts[i]+dx]=fg;
    }
    int inked=0;
    for(int x=44;x<82;++x) if(row[x]==fg) ++inked;
    assert(inked>0);
    for(int x=44;x<82;++x) if(row[x]!=fg) assert(row[x]==bg && bg!=fg);
    for(int x=0;x<120;++x) assert(ink_only[x]==paper);  // the defect: invisible
    std::cout<<"hud paper checked\n";
}
''', HUD_ROWS)
        self.assertIn("hud paper checked", out)

    def test_single_owner_of_reserved_row_counts(self):
        # Static source check: the counts are declared once, in TrueFFHudText.
        declared = [p for p in (ROOT / "godot_project/render").rglob("*.gd")
                    if "RESERVED_ROWS :=" in p.read_text(encoding="utf-8")]
        self.assertEqual(declared, [HUD_TEXT])


if __name__ == "__main__":
    unittest.main()
