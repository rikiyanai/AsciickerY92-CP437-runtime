"""Execute shared GLSL normal/exposure math on CPU; not visual acceptance."""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "godot_project/render/product/cell_owner_product.glsl"

from scripts.tests.test_fl4359_rain_snow_unowned_bg import (
    _expand_includes as _expand_product_includes,
)


def _product_source() -> str:
    return _expand_product_includes(_expand_includes(SOURCE.read_text()))


def function(source, name):
    match = re.search(r"(?:vec3|bool|float|void) " + name + r"\([^;{}]*\)\s*\{", source)
    assert match, name
    start = match.start()
    depth = 0
    for end in range(source.index("{", start), len(source)):
        depth += (source[end] == "{") - (source[end] == "}")
        if depth == 0:
            return source[start:end + 1]
    raise AssertionError(name)


def test_actual_normal_and_exposure_math_rejects_old_fold(tmp_path):
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        pytest.skip("C++ compiler required for CPU execution of shared GLSL math")
    source = _product_source()
    names = ("product_signed_probe_normal", "product_stable_probe_normal",
             "vsb_retained_spider_material", "product_closed_dynamic_material",
             "product_dynamic_material_normal",
             "product_lighting_value")
    functions = "\n".join(function(source, name) for name in names)
    shim = r'''
#include <cmath>
#include <algorithm>
using uint = unsigned int;
struct vec3 {
    float x, y, z;
    vec3(float a, float b, float c): x(a), y(b), z(c) {}
    vec3 operator/(float s) const { return vec3(x/s, y/s, z/s); }
    vec3 operator-() const { return vec3(-x, -y, -z); }
};
float length(vec3 n) { return std::sqrt(n.x*n.x+n.y*n.y+n.z*n.z); }
float clamp(float x, float a, float b) { return std::max(a, std::min(b, x)); }
float mix(float a, float b, float t) { return a+(b-a)*t; }
float smoothstep(float a, float b, float x) {
    float t=clamp((x-a)/(b-a), 0, 1); return t*t*(3-2*t);
}
'''
    exercise = r'''
int main() {
    for (uint material : {220u,238u,239u,246u,247u,248u,252u,253u,254u}) {
        vec3 top=product_dynamic_material_normal(material, vec3(0,2,0));
        vec3 bottom=product_dynamic_material_normal(material, vec3(0,-2,0));
        if (top.y != 1 || bottom.y != -1) return 1;
        float a=product_lighting_value(top, std::max(top.y, 0.0f));
        float b=product_lighting_value(bottom, std::max(bottom.y, 0.0f));
        if (a-b < 0.5) return 2;
    }
    for (uint material : {2u,237u,240u,241u,251u,255u}) {
        if (product_dynamic_material_normal(material, vec3(0,-2,0)).y != 1) return 3;
    }
    if (product_dynamic_material_normal(246u, vec3(0,0,0)).y != 1) return 4;
    return 0;
}
'''
    for old_fold in (False, True):
        code = functions
        if old_fold:
            code = code.replace("closed_surface ? product_signed_probe_normal(normal)",
                                "closed_surface ? product_stable_probe_normal(normal)")
        cpp = tmp_path / ("old.cpp" if old_fold else "current.cpp")
        cpp.write_text(shim + code + exercise)
        binary = cpp.with_suffix("")
        subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(binary)],
                       capture_output=True, text=True, check=True)
        result = subprocess.run([str(binary)], check=False)
        assert result.returncode == (1 if old_fold else 0)


def test_material_shadow_and_lit_finish_share_signed_selection():
    source = _product_source()
    for name in ("product_compile_mesh_material",
                 "product_finish_material_lighting"):
        body = function(source, name)
        assert "product_dynamic_material_normal(" in body, name
        assert "OWNER_DYNAMIC_MESH" in body, name


def test_finished_closed_facets_are_not_repainted_by_row_zero_light(tmp_path):
    """Execute the real finish stage with counted shadow/light dependencies.

    The late dispatcher is gone: mesh material evaluation names the remaining
    cast/gain terms in ProductMaterialPaint.lighting_finish, and
    product_finish_material_lighting applies only those terms. Closed facets
    carry finish 0 and must pass through byte-identical with no dependency
    evaluation; open dynamic facets get exactly one shadow and one gain
    evaluation. The legacy mutant deletes the zero-finish early return, which
    reopens finished facets to the row-zero fallback path.
    """
    compiler = shutil.which("clang++") or shutil.which("g++")
    if not compiler:
        pytest.skip("C++ compiler required")
    source = _product_source()
    finish = function(source, "product_finish_material_lighting")
    assert "PRODUCT_LIGHTING_FINISH_CAST" in finish
    assert "PRODUCT_LIGHTING_FINISH_GAIN" in finish
    assert "q3_probe_payload_apply_lit_gain(fg, bg, context.lit_rgb);" in finish
    functions = finish.replace("inout vec3 ", "vec3 &").replace("precise ", "")
    shim = r'''
#include <cmath>
#include <initializer_list>
using uint = unsigned int;
const uint OWNER_TERRAIN=1, OWNER_STATIC_MESH=2, OWNER_DYNAMIC_MESH=3, OWNER_WATER=4;
const uint PRODUCT_LIGHTING_FINISH_CAST=1, PRODUCT_LIGHTING_FINISH_GAIN=2;
struct vec2 { float x=0, y=0; };
vec2 operator-(vec2 a, vec2 b) { vec2 r; r.x=a.x-b.x; r.y=a.y-b.y; return r; }
struct ivec2 { int x=0,y=0; ivec2() {} ivec2(int a,int b):x(a),y(b) {} ivec2(vec2 v):x(int(v.x)),y(int(v.y)) {} };
vec2 floor(vec2 v) { return v; }
struct vec3 {
    float x,y,z; vec2 xz;
    vec3(float a=0,float b=0,float c=0):x(a),y(b),z(c) {}
    void operator*=(float s) { x*=s; y*=s; z*=s; }
};
struct ProbePayloadContext {
    uint surface_kind=OWNER_DYNAMIC_MESH, material=220;
    vec3 anchor_world, payload_normal, effective_sun_dir, lit_rgb;
    ivec2 material_cell;
    int shadow_hint_address=0;
};
struct ProductShadowReceiver { vec3 world,normal; };
struct { struct { vec2 xy; } terrain_params; } params;
int shadow_calls=0, gain_calls=0;
vec3 product_dynamic_material_normal(uint,vec3 n) {return n;}
ProductShadowReceiver q3_probe_payload_cell_shadow_receiver(uint,ivec2) {return {};}
float q3_probe_payload_cast_shadow_attenuation(vec3,vec3,vec3,uint,int,int) {
    ++shadow_calls; return 0.5;
}
void q3_probe_payload_apply_lit_gain(vec3 &fg,vec3 &bg,vec3) {
    ++gain_calls; fg*=0.55; bg*=0.55;
}
'''
    exercise = r'''
int main() {
    ProbePayloadContext c;
    for (uint material : {220u,238u,239u,246u,247u,248u,252u,253u,254u}) {
        // Closed facets carry finish 0 from mesh material evaluation.
        c.material=material;
        vec3 fg(0.8,0.4,0.2), bg(0.3,0.2,0.1);
        product_finish_material_lighting(c, 0u, fg, bg);
        if (fg.x!=0.8f || bg.x!=0.3f || shadow_calls || gain_calls) return 1;
    }
    c.material=220;
    vec3 fg(0.8,0.4,0.2), bg(0.3,0.2,0.1);
    product_finish_material_lighting(c,
        PRODUCT_LIGHTING_FINISH_CAST | PRODUCT_LIGHTING_FINISH_GAIN, fg, bg);
    if (shadow_calls!=1 || gain_calls!=1 || std::abs(fg.x-0.22f)>1e-6) return 2;
    return 0;
}
'''
    cpp = tmp_path / "finish.cpp"
    cpp.write_text(shim + functions + exercise)
    binary = cpp.with_suffix("")
    subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(binary)],
                   text=True, capture_output=True, check=True)
    assert subprocess.run([str(binary)], check=False).returncode == 0
    # The row-zero fallback stays deleted: removing the shadow multiply must
    # change the open-facet result, which the exercise pins at 0.22.
    muted, count = re.subn(r"fg \*= attenuation;", "", functions)
    assert count == 1
    cpp = tmp_path / "noshadow_finish.cpp"
    cpp.write_text(shim + muted + exercise)
    binary = cpp.with_suffix("")
    subprocess.run([compiler, "-std=c++17", str(cpp), "-o", str(binary)],
                   text=True, capture_output=True, check=True)
    assert subprocess.run([str(binary)], check=False).returncode == 2


def test_closed_material_consumes_shadow_before_palette_and_preserves_emission():
    material = function(_product_source(), "product_compile_mesh_material")
    assert "if (!emissive_dynamic && (closed_dynamic" in material
    assert material.index("q3_probe_payload_cast_shadow_attenuation(") < material.index("product_quantise_shd(")
    assert "context.surface_kind == OWNER_STATIC_MESH ? context.material_cell.x : -1" in material
    assert "(closed_dynamic ? 1.0 : mix(0.72, 1.12, authored_shade))" in material
    assert "OWNER_DYNAMIC_MESH && !closed_dynamic" in material
    assert "if (!emissive_dynamic) {\n\t\tproduct_apply_ambient_base" in material
    # The closed gate owns the finish flags: no second classification downstream.
    assert "paint.lighting_finish = !context.payload_from_backstop && !closed_dynamic" in material
