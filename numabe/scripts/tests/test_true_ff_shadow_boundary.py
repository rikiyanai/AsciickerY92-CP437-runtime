"""FL-4907 GPU_SHADOW_BOUNDARY_PASS_20261002: offline unit of the production
shadow-outline GLSL (true_ff_shadow_boundary.glslinc), compiled as C++.

Covers the receiver shadow-state law, the shadow-bit edge predicate, per-cell
P0 window partition, one-site-per-cell election, the outline tangent and the
stable site identity/source words. Synthetic P0 state grids only: no GPU,
light-map raster, Metal, frame or visual evidence.
"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "godot_project/render/ascii/features"
INCLUDE = FEATURES / "true_ff_shadow_boundary.glslinc"
ABI = FEATURES / "true_ff_feature_abi.glslinc"
STROKE_BANK = FEATURES / "TrueFFStrokeBank.gd"

PRELUDE = r'''
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <algorithm>
using uint = uint32_t;
struct ivec2;
struct vec2 { float x, y;
    vec2(float a = 0) : x(a), y(a) {}
    vec2(float a, float b) : x(a), y(b) {}
    explicit vec2(const ivec2& v); };
struct ivec2 { int x, y;
    ivec2(int a = 0) : x(a), y(a) {}
    ivec2(int a, int b) : x(a), y(b) {}
    explicit ivec2(const vec2& v) : x(int(v.x)), y(int(v.y)) {} };
vec2::vec2(const ivec2& v) : x(float(v.x)), y(float(v.y)) {}
struct uvec4 { uint x, y, z, w; uvec4(uint a = 0, uint b = 0, uint c = 0, uint d = 0) : x(a), y(b), z(c), w(d) {} };
vec2 operator+(vec2 a, vec2 b) { return {a.x + b.x, a.y + b.y}; }
vec2 operator-(vec2 a, vec2 b) { return {a.x - b.x, a.y - b.y}; }
vec2 operator*(vec2 a, vec2 b) { return {a.x * b.x, a.y * b.y}; }
vec2 operator/(vec2 a, vec2 b) { return {a.x / b.x, a.y / b.y}; }
vec2 operator+(vec2 a, double b) { return {float(a.x + b), float(a.y + b)}; }
vec2 operator-(vec2 a, double b) { return {float(a.x - b), float(a.y - b)}; }
vec2 operator*(vec2 a, double b) { return {float(a.x * b), float(a.y * b)}; }
vec2 operator-(vec2 a) { return {-a.x, -a.y}; }
ivec2 operator+(ivec2 a, ivec2 b) { return {a.x + b.x, a.y + b.y}; }
float dot(vec2 a, vec2 b) { return a.x * b.x + a.y * b.y; }
vec2 normalize(vec2 a) { float l = std::sqrt(dot(a, a)); return {a.x / l, a.y / l}; }
vec2 ceil(vec2 a) { return {std::ceil(a.x), std::ceil(a.y)}; }
ivec2 clamp(ivec2 v, ivec2 lo, ivec2 hi) { return {std::min(std::max(v.x, lo.x), hi.x), std::min(std::max(v.y, lo.y), hi.y)}; }
float max(float a, double b) { return std::max(a, float(b)); }
float min(float a, double b) { return std::min(a, float(b)); }
'''

# Synthetic P0 grid: per-texel shadow state and receiver id.
GRID = r'''
const int W = 16, H = 16;
uint g_state[H][W], g_receiver[H][W];
uint tff_shadow_texel_state(ivec2 t) { return g_state[t.y][t.x]; }
uint tff_shadow_texel_receiver(ivec2 t) { return g_receiver[t.y][t.x]; }
ivec2 tff_shadow_texel_size() { return ivec2(W, H); }
void fill(uint state, uint receiver) {
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) { g_state[y][x] = state; g_receiver[y][x] = receiver; }
}
bool close_to(float a, float b) { return std::fabs(a - b) < 1e-4f; }
'''

CASES = r'''
int main() {
    // 1. Receiver state law: facing, plain depth bias and slope bias.
    float texel = 0.5f, scale = 100.0f;
    if (tff_shadow_state(0.5f, 0.2f, scale, 0.0f, texel) != TFF_SHADOW_AWAY) return 1;
    if (tff_shadow_state(0.5f, 0.2f, scale, -0.3f, texel) != TFF_SHADOW_AWAY) return 1;
    if (tff_shadow_state(0.5f, 0.2f, scale, std::nanf(""), texel) != TFF_SHADOW_AWAY) return 1;
    if (tff_shadow_state(0.5f, 0.5f, scale, 1.0f, texel) != TFF_SHADOW_LIT) return 2;
    if (tff_shadow_state(0.5f, 0.4f, scale, 1.0f, texel) != TFF_SHADOW_SHADOWED) return 3;
    // Inside one texel of map quantization at normal incidence: lit.
    if (tff_shadow_state(0.5f, 0.5f - 0.9f * texel / scale, scale, 1.0f, texel) != TFF_SHADOW_LIT) return 4;
    // A grazing receiver tolerates its own slope across one texel (no acne);
    // the same depth difference at normal incidence is a real occluder.
    float diff = 4.0f * texel / scale;
    if (tff_shadow_state(0.5f, 0.5f - diff, scale, 0.2f, texel) != TFF_SHADOW_LIT) return 5;
    if (tff_shadow_state(0.5f, 0.5f - diff, scale, 1.0f, texel) != TFF_SHADOW_SHADOWED) return 6;

    // 2. Edge predicate: only LIT/SHADOWED of ONE receiver pair.
    if (!tff_shadow_boundary_pair(TFF_SHADOW_LIT, TFF_SHADOW_SHADOWED, 7, 7)) return 10;
    if (!tff_shadow_boundary_pair(TFF_SHADOW_SHADOWED, TFF_SHADOW_LIT, 7, 7)) return 10;
    if (tff_shadow_boundary_pair(TFF_SHADOW_LIT, TFF_SHADOW_SHADOWED, 7, 8)) return 11;
    if (tff_shadow_boundary_pair(TFF_SHADOW_AWAY, TFF_SHADOW_SHADOWED, 7, 7)) return 12;
    if (tff_shadow_boundary_pair(TFF_SHADOW_NONE, TFF_SHADOW_SHADOWED, 7, 7)) return 12;
    if (tff_shadow_boundary_pair(TFF_SHADOW_SHADOWED, TFF_SHADOW_SHADOWED, 7, 7)) return 13;

    // 3. Vertical shadow edge (x >= 8 shadowed): the shadowed side owns it.
    fill(TFF_SHADOW_LIT, 7);
    for (int y = 0; y < H; ++y) for (int x = 8; x < W; ++x) g_state[y][x] = TFF_SHADOW_SHADOWED;
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x)
        if (tff_shadow_boundary_texel(ivec2(x, y)) != (x == 8)) return 20;
    ivec2 chosen;
    if (!tff_shadow_elect(ivec2(4, 0), ivec2(12, 16), vec2(6.0f, 3.0f), chosen)) return 21;
    if (chosen.x != 8 || chosen.y != 2) return 22; // nearest centre, then row-major
    vec2 t = tff_shadow_tangent(ivec2(8, 5));
    if (!close_to(t.x, 0.0f) || !close_to(t.y, 1.0f)) return 23;
    // A window without a crossing elects nothing.
    if (tff_shadow_elect(ivec2(0, 0), ivec2(8, 16), vec2(4.0f, 8.0f), chosen)) return 24;

    // 4. Diagonal edge (x + y >= 16 shadowed): canonical tangent (1,-1)/sqrt2.
    fill(TFF_SHADOW_LIT, 7);
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) if (x + y >= 16) g_state[y][x] = TFF_SHADOW_SHADOWED;
    t = tff_shadow_tangent(ivec2(8, 8));
    if (!close_to(t.x, 0.70710678f) || !close_to(t.y, -0.70710678f)) return 30;

    // 5. An identity edge is not a shadow edge: object 8 (shadowed) beside
    // lit object 7 owes no outline; attached shade (AWAY) owes none either.
    fill(TFF_SHADOW_LIT, 7);
    for (int y = 0; y < H; ++y) for (int x = 8; x < W; ++x) { g_state[y][x] = TFF_SHADOW_SHADOWED; g_receiver[y][x] = 8; }
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) if (tff_shadow_boundary_texel(ivec2(x, y))) return 40;
    fill(TFF_SHADOW_AWAY, 7);
    for (int y = 0; y < H; ++y) for (int x = 8; x < W; ++x) g_state[y][x] = TFF_SHADOW_SHADOWED;
    for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) if (tff_shadow_boundary_texel(ivec2(x, y))) return 41;

    // 6. Tie-break: equal distance, the smaller receiver wins.
    // The larger receiver comes first in scan order and must still lose.
    fill(TFF_SHADOW_LIT, 5);
    for (int y = 0; y < H; ++y) for (int x = 0; x < 8; ++x) g_receiver[y][x] = 9;
    g_state[4][3] = TFF_SHADOW_SHADOWED; // receiver 9, left of centre
    g_state[4][12] = TFF_SHADOW_SHADOWED; // receiver 5, right of centre
    if (!tff_shadow_elect(ivec2(0, 0), ivec2(16, 16), vec2(8.0f, 4.5f), chosen)) return 50;
    if (chosen.x != 12 || chosen.y != 4) return 51;

    // 7. Native cell windows partition the P0 texels: page 37x23, cells
    // 11x17, P0 19x12. Every texel belongs to exactly one cell window.
    {
        int owners[12][19] = {};
        vec2 page(37.0f, 23.0f), cell(11.0f, 17.0f);
        for (int cy = 0; cy < 2; ++cy) for (int cx = 0; cx < 4; ++cx) {
            vec2 lo_page = vec2(float(cx), float(cy)) * cell;
            vec2 hi_page(std::min(lo_page.x + cell.x, page.x), std::min(lo_page.y + cell.y, page.y));
            ivec2 lo, hi;
            tff_shadow_cell_window(lo_page, hi_page, page, ivec2(19, 12), lo, hi);
            for (int y = lo.y; y < hi.y; ++y) for (int x = lo.x; x < hi.x; ++x) ++owners[y][x];
        }
        for (int y = 0; y < 12; ++y) for (int x = 0; x < 19; ++x) if (owners[y][x] != 1) return 60;
    }

    // 8. Identity and seed: receiver + caster only, never the cell/texel.
    uvec4 id = tff_shadow_site_identity(7, 0x2A, 99);
    if (id.x != 7 || id.y != 0x2A || id.z != TFF_CLASS_AUTHORED_STROKE || id.w != 99) return 70;
    if (tff_shadow_seed(7, 0x2A) != tff_shadow_seed(7, 0x2A)) return 71;
    if (tff_shadow_seed(7, 0x2A) == tff_shadow_seed(7, 0x2B)) return 72;
    uvec4 source = tff_shadow_site_source();
    if (source.x != TFF_SHADOW_MATERIAL || ((source.z >> 8) & 255u) != STROKE_SOURCE_SHADOW
            || ((source.z >> 21) & 3u) != STROKE_VISIBILITY_RECEIVER || (source.z & 63u) != 0u
            || source.y != 0u || source.w != 0u) return 73;
    return 0;
}
'''


def _gd_const(text, name):
    match = re.search(rf"const {name}: int = (\d+)", text)
    assert match, name
    return int(match.group(1))


def _program(mutation=None):
    include = INCLUDE.read_text(encoding="utf-8")
    if mutation == "no_receiver_check":
        include = include.replace("return receiver_a == receiver_b\n        &&", "return true\n        &&")
        assert "return true\n        &&" in include
    elif mutation == "no_slope_bias":
        include = include.replace("TFF_SHADOW_SLOPE_TEXELS * slope", "0.0 * slope")
        assert "0.0 * slope" in include
    # GLSL `out T name` parameters are C++ references.
    include = re.sub(r"\bout (\w+) (\w+)", r"\1& \2", include)
    abi = ABI.read_text(encoding="utf-8")
    stroke_class = re.search(r"const uint TFF_CLASS_AUTHORED_STROKE = (\d+)u;", abi)
    assert stroke_class
    bank = STROKE_BANK.read_text(encoding="utf-8")
    constants = (f"const uint TFF_CLASS_AUTHORED_STROKE = {stroke_class.group(1)}u;\n"
                 f"const uint STROKE_SOURCE_SHADOW = {_gd_const(bank, 'SOURCE_SHADOW')}u;\n"
                 f"const uint STROKE_VISIBILITY_RECEIVER = {_gd_const(bank, 'VISIBILITY_RECEIVER')}u;\n")
    return PRELUDE + constants + include + GRID + CASES


@pytest.mark.parametrize("mutation,expected", [(None, 0), ("no_receiver_check", 11), ("no_slope_bias", 5)])
def test_production_shadow_boundary_law_and_falsifiers(tmp_path, mutation, expected):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable for offline GLSL unit")
    binary = tmp_path / "shadow_boundary_unit"
    result = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", str(binary)],
                            input=_program(mutation), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == expected, result.stderr


def test_cpu_shadow_path_deleted_and_gpu_pass_wired():
    """Source-level: the CPU producer, terrain ray and live shadow accessor
    are gone; shadow proposals precede P1 and publish only after the joint
    native/owed certificate, before P3."""
    for name in ("TrueFFShadowFeatures.gd", "true_ff_terrain_ray.glslinc",
                 "true_ff_live_shadow_source.glslinc"):
        assert not (FEATURES / name).exists(), name
    world = (FEATURES / "TrueFFWorldFeatureSources.gd").read_text(encoding="utf-8")
    assert "Shadow" not in re.sub(r"(?m)^\s*#.*$", "", world)
    passes = (FEATURES / "TrueFFFeaturePasses.gd").read_text(encoding="utf-8")
    run = passes.split("func execute(", 1)[1].split("\nfunc ", 1)[0]
    assert run.index('SHADOW_PATH, [0,') < run.index('SHADOW_PATH, [1,') < run.index('SHADOW_PATH, [2,')
    assert run.index('SHADOW_PATH, [2,') < run.index('_record_cut_round(')
    assert run.index('_record_cut_round(') < run.index('SHADOW_PATH, [3,') < run.index('SITES_PATH, [1,')
    assert run.index('SITES_PATH, [1,') < run.index('VISIBILITY_PATH')
    record = passes.split("func _record_cut_round(", 1)[1].split("\nfunc ", 1)[0]
    assert record.index('SITES_PATH, [0,') < record.index('_dispatch_native_certificate(')
    assert '_dispatch_owed(list, 6,' in record
    for path in FEATURES.glob("*.glsl*"):
        text = path.read_text(encoding="utf-8")
        assert "tff_terrain_ray" not in text and "tff_live_shadow_feature" not in text, path
        # Godot Metal rejects runtime-array .length() (FL-4907).
        if path.name.startswith("true_ff_shadow_boundary"):
            assert ".length()" not in text and "coherent" not in text, path
