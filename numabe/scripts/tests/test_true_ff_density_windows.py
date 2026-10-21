"""Offline float32 tests of the production source-arc density arithmetic.

The GLSL function bodies are compiled as C++ without reimplementing the
window equations. This does not execute a GPU or establish visual stability.
The brute-force oracle visits the finest world-arc lattice and admits a point
only when its ordinal belongs to its local dyadic level. Window enumeration
must find exactly that set, without duplicates or a changed world phase.
"""
from __future__ import annotations

import math
from pathlib import Path
import random
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
GEOMETRY = ROOT / "godot_project/render/ascii/composition/true_ff_contour_geometry.glslinc"
DENSITY = ROOT / "godot_project/render/ascii/features/true_ff_feature_density.glslinc"


def function_body(source: str, name: str) -> str:
    match = re.search(rf"(?m)^\w+\s+{name}\([^{{]+\{{", source)
    assert match, f"missing production function {name}"
    start = source.index("{", match.start())
    end, depth = start + 1, 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


@pytest.fixture(scope="module")
def density_unit(tmp_path_factory):
    compiler = shutil.which("c++")
    assert compiler, "offline production-arithmetic test requires a C++ compiler"
    source = GEOMETRY.read_text()
    functions = "\n".join(function_body(source, name) for name in (
        "tff_arc_level", "tff_arc_spacing", "tff_arc_step_at",
        "tff_arc_level_window", "tff_arc_range",
    ))
    program = r'''
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <set>
using uint = std::uint32_t;
using std::min; using std::max;
using std::ceil; using std::sqrt; using std::exp2;
float clamp(float x, double lo, double hi) { return std::clamp(x, float(lo), float(hi)); }
float max(float a, double b) { return std::max(a, float(b)); }
float min(float a, double b) { return std::min(a, float(b)); }
uint floatBitsToUint(float value) { uint bits; std::memcpy(&bits, &value, 4); return bits; }
float uintBitsToFloat(uint bits) { float value; std::memcpy(&value, &bits, 4); return value; }
struct TFFInterval { float lo, hi; };
struct TFFSiteRange { int first, count; };
'''+functions+'\n'+DENSITY.read_text()+r'''
int main() {
    float length, units, wa, wb, quantum, phase;
    while (std::cin >> length >> units >> wa >> wb >> quantum >> phase) {
        std::set<int> windowed, exhaustive;
        TffDensity density = tff_density_make(length, units, wa, wb);
        uint mask = tff_density_plan_mask(density, quantum);
        int repeats = 0;
        uint bound = tff_density_bound(density, mask, quantum);
        for (int level = 0; level < 30; ++level) {
            TFFInterval w = tff_density_window(density, quantum, mask, uint(level));
            if (w.hi <= w.lo) continue;
            float spacing = tff_arc_spacing(level, quantum);
            float lo = phase + length * w.lo, hi = phase + length * w.hi;
            TFFSiteRange range = tff_arc_range(lo, hi, spacing);
            for (int j = range.first; j < range.first + range.count; ++j) {
                float t = (float(j) * spacing - phase) / length;
                if (t < 0.0f || t >= 1.0f) continue;
                int local = int(tff_density_point_level(density, t, quantum));
                if (local != level) continue;
                if (!windowed.insert(j * (1 << level)).second) ++repeats;
            }
        }
        TFFSiteRange all = tff_arc_range(phase, phase + length, quantum);
        if (all.count > 2000000) return 2;
        for (int q = all.first; q < all.first + all.count; ++q) {
            float t = (float(q) * quantum - phase) / length;
            if (t < 0.0f || t >= 1.0f) continue;
            int level = tff_arc_level(tff_arc_step_at(length, units, wa, wb, t), quantum);
            if (q % (1 << level) == 0) exhaustive.insert(q);
        }
        std::cout << (windowed == exhaustive) << ' ' << repeats << ' '
                  << bound << ' ' << windowed.size();
        for (int q : windowed) std::cout << ' ' << q;
        std::cout << '\n';
    }
}
'''
    binary = tmp_path_factory.mktemp("true-ff-density") / "density_unit"
    result = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", str(binary)],
                            input=program, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return binary


def evaluate(binary, cases):
    data = "\n".join(" ".join(map(str, row)) for row in cases) + "\n"
    result = subprocess.run([str(binary)], input=data, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    rows = [list(map(int, row.split())) for row in result.stdout.splitlines()]
    assert len(rows) == len(cases)
    for case, row in zip(cases, rows):
        equal, duplicates, bound, count, *ordinals = row
        assert equal == 1, (case, "window discovery missed finest-lattice members")
        assert duplicates == 0, (case, "threshold point emitted twice")
        assert count == len(ordinals) <= bound, (case, "demand bound underestimated")
    return [set(row[4:]) for row in rows]


@pytest.fixture(scope="module")
def projected_density_unit(tmp_path_factory):
    """Execute the production projection metric, not a Python copy of it."""
    compiler = shutil.which("c++")
    assert compiler
    source = GEOMETRY.read_text()
    functions = "\n".join(function_body(source, name) for name in (
        "tff_site_units", "tff_arc_level", "tff_arc_step_at",
    ))
    program = r'''
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
using std::abs; using std::max;
float max(float a, double b) { return std::max(a, float(b)); }
''' + functions + r'''
int main() {
    float dx, dy, advance, pitch;
    while (std::cin >> dx >> dy >> advance >> pitch) {
        float units = tff_site_units(dx, dy, advance, pitch);
        float step = tff_arc_step_at(1.0f, units, 1.0f, 1.0f, 0.5f);
        std::cout << std::setprecision(9) << units << ' '
                  << tff_arc_level(step, 1.0f / 1024.0f) << '\n';
    }
}
'''
    binary = tmp_path_factory.mktemp("true-ff-projected-density") / "projection_unit"
    result = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", str(binary)],
                            input=program, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return binary


def projected_metrics(binary, cases):
    result = subprocess.run([str(binary)], input="\n".join(" ".join(map(str, x)) for x in cases),
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    rows = [(float(row.split()[0]), int(row.split()[1])) for row in result.stdout.splitlines()]
    assert len(rows) == len(cases)
    return rows


@pytest.mark.parametrize("advance", [11., 16.])
def test_native_metric_has_no_raw_diagonal_density_jump(projected_density_unit, density_unit, advance):
    # FL-4907: an infinitesimal slope change at |dx| == |dy| is not a
    # half-density event. The old raw-axis branch selects level 6 then 7.
    # The live feature renderer uses the 11px U+3000 advance, not the
    # 16px font size. Cover both the production metric and the original red.
    diagonal = 16.5 * advance
    for sx, sy in [(1, 1), (-1, 1), (1, -1), (-1, -1)]:
        cases = [(sx * diagonal, sy * dy, advance, 17.)
                 for dy in (diagonal - .001, diagonal, diagonal + .001)]
        rows = projected_metrics(projected_density_unit, cases)
        assert [level for _, level in rows] == [6, 6, 6]
        assert [units for units, _ in rows] == pytest.approx([16.5] * 3)
        sites = evaluate(density_unit, [(1., units, 1., 1., 1. / 1024., 0.)
                                       for units, _ in rows])
        assert sites[0] == sites[1] == sites[2] == set(range(0, 1024, 64))


def test_native_metric_normalizes_axes_before_selecting_extent(projected_density_unit):
    cases = [(dx, dy, a, p) for a, p in [(11., 17.), (16., 17.), (3., 17.), (17., 16.)]
             for dx, dy in [(0., 0.), (80., 0.), (0., 85.), (264., 264.),
                            (-264., 264.), (160., 170.), (160., 170.001)]]
    rows = projected_metrics(projected_density_unit, cases)
    for case, (units, _) in zip(cases, rows):
        dx, dy, advance, pitch = case
        assert units == pytest.approx(max(abs(dx) / advance, abs(dy) / pitch), rel=2e-7)


def test_production_windows_cover_local_members_at_all_phases(density_unit):
    cases = [(12., 40., wa, wb, 1. / 1024., phase)
             for wa, wb in [(1., 1.), (1., 64.), (64., 1.), (1., 1.0000001192092896)]
             for phase in [0., .0003, .17, 8.125]]
    evaluate(density_unit, cases)


def test_production_windows_match_fine_lattice_randomly(density_unit):
    rng = random.Random(4907)
    cases = [(rng.uniform(.02, 20.), rng.uniform(.1, 250.),
              2. ** rng.uniform(-8., 8.), 2. ** rng.uniform(-8., 8.),
              1. / 1024., rng.uniform(0., 40.)) for _ in range(180)]
    evaluate(density_unit, cases)


def test_projected_growth_adds_sites_without_moving_existing_ordinals(density_unit):
    for wa, wb in [(1., 1.), (.1, 20.), (20., .1)]:
        cases = [(10., units, wa, wb, 1. / 1024., .171875)
                 for units in [2., 4., 8., 16., 32., 64., 128.]]
        sets = evaluate(density_unit, cases)
        for smaller, larger in zip(sets, sets[1:]):
            assert smaller <= larger


def test_far_end_does_not_inherit_near_end_finest_spacing(density_unit):
    length, units, wa, wb, quantum, phase = 100., 100., 1., 100., 1. / 1024., 0.
    sites, = evaluate(density_unit, [(length, units, wa, wb, quantum, phase)])
    finest_endpoint_step = length / units * wa / wb
    level = max(0, math.ceil(math.log2(finest_endpoint_step / quantum)))
    old_uniform_count = math.ceil(length / (quantum * 2 ** level))
    assert 0 < len(sites) < old_uniform_count / 10


def test_clipping_preserves_source_arc_phase_and_local_density(density_unit):
    # Perspective projection of a straight segment has derivative proportional
    # to wa*wb/w(t)^2. Cropping changes its projected length, never the source
    # phase. The same world point must therefore choose the same dyadic level.
    length, units, quantum, phase = 100., 103., 1. / 1024., 5.125
    for wa, wb in [(1., 1.), (1., 100.), (100., 1.)]:
        intervals = [(.125, .5), (.5, .875), (.125, .875)]
        cases = [(length, units, wa, wb, quantum, phase)]
        for lo, hi in intervals:
            w0, w1 = wa + (wb - wa) * lo, wa + (wb - wa) * hi
            clipped_units = units * wa * wb * (hi - lo) / (w0 * w1)
            cases.append((length * (hi - lo), clipped_units, w0, w1,
                          quantum, phase + length * lo))
        full, *clipped = evaluate(density_unit, cases)
        for (lo, hi), actual in zip(intervals, clipped):
            expected = {q for q in full
                        if phase + length * lo <= q * quantum < phase + length * hi}
            assert actual == expected


@pytest.mark.parametrize("case", [
    (1., 1e-8, 1., 2., 1., 0.),
    (189.6485595703125, 1640.01611328125, 214.78672790527344,
     .04890911281108856, .0003241185040678829, .030783044174313545),
])
def test_adversarial_discovery_counterexamples(density_unit, case):
    evaluate(density_unit, [case])
