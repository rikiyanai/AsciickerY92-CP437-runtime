"""Offline unit tests for the production True FF source-crossing predicate.

The C++ harness below compiles the current GLSL bodies of ``tff_g``,
``tff_source_edge_g``, and ``tff_source_crossing`` directly from
``true_ff_feature_passes.glslinc``, plus ``tff_live_feature_geometry`` and
``tff_live_source_crossing`` (the body ``tff_source_crossing`` delegates to)
from ``true_ff_vegetation_feature_source.glslinc``.  It therefore tests the production
predicate rather than a separately handwritten Python equivalent.

This is rung-1 synthetic-fixture evidence only: it does not launch Godot, a
GPU, or the compositor.  The harness stubs only the facts outside this
predicate's decision surface: local-to-world point/normal transforms are
identity transforms, and octahedral decoding maps a few packed test tokens to
fixed unit normals.  Camera ``eye``/``forward`` values are explicit fixtures;
the extracted production ``tff_g`` chooses the orthographic or perspective
fact exactly as the shader does.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "godot_project/render/ascii/features/true_ff_feature_passes.glslinc"
LIVE_SOURCE = (ROOT / "godot_project/render/ascii/features/"
               "true_ff_vegetation_feature_source.glslinc")
LIVE_GEOMETRY = SOURCE.with_name("true_ff_live_sources.glslinc")
LIVE_MATH = SOURCE.with_name("true_ff_live_source_math.glslinc")


def _extract_function(text: str, name: str) -> str:
    """Extract one production GLSL function, preserving its body verbatim."""
    import re

    match = re.search(
        rf"(?m)^(?:float|bool|uvec2)\s+{name}\s*\([^)]*\)\s*\{{", text
    )
    assert match is not None, f"production function {name} is missing"
    brace = text.find("{", match.start(), match.end())
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[match.start():end]


PRELUDE = r'''
#include <cmath>
#include <cstdint>
#include <cstdlib>

using uint = std::uint32_t;
const uint TFF_SLOT_ADMITTED = 17u;
void tff_contract_error(uint) {}

struct vec3 {
    float x, y, z;
    vec3(float a = 0.0f) : x(a), y(a), z(a) {}
    vec3(float a, float b, float c) : x(a), y(b), z(c) {}
};

struct vec4 {
    float x, y, z, w;
    // GLSL's swizzle is a member access in the extracted production bodies.
    vec3 xyz;
    vec4(float a = 0.0f, float b = 0.0f, float c = 0.0f, float d = 0.0f)
        : x(a), y(b), z(c), w(d), xyz(a, b, c) {}
    vec4(vec3 p, float d) : vec4(p.x, p.y, p.z, d) {}
};

// These fixtures exercise the production predicate with an identity live
// frame, not sky/wake deformation or matrix inversion. Keep that boundary
// explicit while extracting the new production geometry delegation too.
struct mat4 {};
struct mat3 { explicit mat3(mat4) {} };
mat3 inverse(mat3 m) { return m; }
mat3 transpose(mat3 m) { return m; }
vec4 operator*(mat4, vec4 p) { return p; }
vec3 operator*(mat3, vec3 p) { return p; }

struct uvec4 {
    uint x, y, z, w;
    uvec4(uint a = 0u, uint b = 0u, uint c = 0u, uint d = 0u)
        : x(a), y(b), z(c), w(d) {}
    uint operator[](uint i) const {
        switch(i) { case 0: return x; case 1: return y; case 2: return z; default: return w; }
    }
};

struct TffFeatureRecord {
    vec4 p0;
    vec4 p1;
    uvec4 normals;
    uvec4 ident;
};

// Transform facts are deliberately stubbed to identity.  Transform behavior
// is not admitted by this module; source-edge predicate behavior is.
// refs/identity exist only because the extracted vegetation branch names them.
struct TffFeatureInstance { uvec4 refs; uvec4 identity; };
vec3 tff_xform_point(TffFeatureInstance, vec3 p) { return p; }
vec3 tff_xform_covector(TffFeatureInstance, vec3 n) { return n; }
bool tff_live_source_transform(TffFeatureInstance, mat4& transform) {
    transform = mat4{};
    return true;
}
bool tff_source_finite(vec3 p) {
    return std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
}

// Octahedral decoding is also deliberately stubbed.  The packed values are
// test tokens, not an alternate implementation of the production codec.
vec3 tff_oct_decode(uint packed) {
    switch (packed) {
        case 1u: return vec3(0.0f, 0.0f, 1.0f);
        case 2u: return vec3(0.0f, 0.0f, -1.0f);
        case 3u: return vec3(1.0f, 0.0f, 0.0f);
        default: return vec3(0.0f, 0.0f, 1.0f);
    }
}

struct Camera { vec4 eye; vec4 forward; } cam;

vec3 operator-(vec3 a, vec3 b) { return vec3(a.x - b.x, a.y - b.y, a.z - b.z); }
float dot(vec3 a, vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
vec3 cross(vec3 a, vec3 b) {
    return vec3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
vec3 normalize(vec3 a) {
    float l = std::sqrt(dot(a, a));
    return vec3(a.x / l, a.y / l, a.z / l);
}

const uint TFF_CLASS_SMOOTH_CANDIDATE = 1u;
const uint TFF_FEATURE_FLAG_MATERIAL_MASK = 0xFFFFu;  // true_ff_feature_abi.glslinc
const uint TFF_LIVE_INACTIVE = 1u << 23u;
const uint TFF_SLOT_META = 0u;
const uint TFF_ERROR_SCENE_SOURCE = 1u << 26u;
uint args[4] = {};
uint atomicOr(uint& target, uint value) { uint prior=target; target|=value; return prior; }

// The extracted tff_live_feature_geometry also owns the live vegetation
// branch (material id 255).  These fixtures never select it: its buffer and
// deformation dependencies exist only so the production body compiles, and
// every one aborts if reached.
template <class T> struct TffAbortBuffer {
    struct Values { T operator[](uint) const { std::abort(); } } values;
};
TffAbortBuffer<uvec4> tff_vegetation_features, tff_vegetation_assets;
TffAbortBuffer<vec4> tff_vegetation_rows, tff_vegetation_instances;
struct { uvec4 values; } vegetation_counts;
uint floatBitsToUint(float) { std::abort(); }
uint vegetation_face_index(uint) { std::abort(); }
vec3 vegetation_source_world_point(vec3, vec4, vec3, vec3, vec3) { std::abort(); }
void vegetation_deform_card_vertices(vec3 (&)[4], uint) { std::abort(); }
'''


def _production_cpp() -> str:
    source = SOURCE.read_text(encoding="utf-8")
    live = LIVE_SOURCE.read_text(encoding="utf-8")
    # These are the actual production implementations, in production include
    # order.  Only the declarations and helper facts above are C++ fixtures;
    # the predicate bodies are not duplicated in this test.
    extracted = "\n\n".join(
        [_extract_function(source, "tff_g")]
        + [_extract_function(LIVE_MATH.read_text(), "tff_live_active"),
           _extract_function(LIVE_GEOMETRY.read_text(), "tff_live_source_geometry")]
        + [_extract_function(live, name) for name in
           ("tff_live_feature_geometry", "tff_live_source_crossing")]
        + [_extract_function(source, "tff_current_geometry")]
        + [_extract_function(source, name) for name in
           ("tff_source_edge_g", "tff_source_crossing", "tff_source_crossing_geometry")]
    )
    # The only required GLSL-to-C++ conversion is the output parameter syntax;
    # all expressions and branches remain the extracted production text.
    return (extracted.replace(", out float t)", ", float& t)")
            .replace("out vec3 ", "vec3& "))


PROGRAM = r'''
TffFeatureRecord feature(float p0_z, float p1_z, uint cls = 1u) {
    return {vec4(0.0f, 0.0f, p0_z, 0.0f),
            vec4(0.0f, 0.0f, p1_z, 0.0f),
            uvec4(0u, 0u, 1u, 1u),
            uvec4(cls, 0u, 0u, 0u)};
}

bool close(float a, float b) { return std::fabs(a - b) < 1e-6f; }

void perspective_camera() {
    cam.eye = vec4(0.0f, 0.0f, 10.0f, 0.0f);
    cam.forward = vec4(0.0f, 0.0f, 1.0f, 0.0f);
}

void orthographic_camera() {
    cam.eye = vec4(0.0f, 0.0f, 10.0f, 1.0f);
    cam.forward = vec4(0.0f, 0.0f, 1.0f, 0.0f);
}

int main() {
    TffFeatureInstance inst{};
    // Production callers pass the feature's source address with the record
    // read from it. Fixture records carry a non-vegetation material id, so
    // the address only selects; it never feeds the geometry.
    const uint address = 0u;
    float t = -1.0f;

    // The production predicate treats zero as nonpositive: zero and a
    // negative endpoint do not cross, while zero and a positive endpoint do.
    perspective_camera();
    if (tff_source_crossing(inst, address, feature(10.0f, 0.0f), t)) return 1;
    if (!close(t, 0.0f)) return 2;
    if (!tff_source_crossing(inst, address, feature(10.0f, 20.0f), t)) return 3;
    if (!close(t, 0.0f)) return 4;

    // Same-sign endpoints reject; opposite signs accept at the linear root.
    if (tff_source_crossing(inst, address, feature(0.0f, 5.0f), t)) return 5;
    if (tff_source_crossing(inst, address, feature(15.0f, 20.0f), t)) return 6;
    if (!tff_source_crossing(inst, address, feature(0.0f, 20.0f), t)) return 7;
    if (!close(t, 0.5f)) return 8;

    // Reversing both endpoints reverses the parameter, without changing the
    // source edge's geometric crossing.
    float forward_t = -1.0f, reverse_t = -1.0f;
    if (!tff_source_crossing(inst, address, feature(0.0f, 14.0f), forward_t)) return 9;
    if (!tff_source_crossing(inst, address, feature(14.0f, 0.0f), reverse_t)) return 10;
    if (!close(reverse_t, 1.0f - forward_t)) return 11;

    // A fixed class is never promoted to a smooth source crossing, even when
    // its endpoint g values have opposite signs.
    if (tff_source_crossing(inst, address, feature(0.0f, 20.0f, 2u), t)) return 12;
    if (!close(t, 0.0f)) return 13;

    // The extracted production tff_g uses forward for orthographic cameras
    // and the eye-to-vertex vector for perspective cameras.
    orthographic_camera();
    if (tff_source_crossing(inst, address, feature(0.0f, 20.0f), t)) return 14;
    perspective_camera();
    if (!tff_source_crossing(inst, address, feature(0.0f, 20.0f), t)) return 15;
    if (!close(t, 0.5f)) return 16;

    // Orthographic crossings depend on endpoint normals, not positions.
    // Distinct packed tokens ensure source_edge_g reads z for endpoint zero
    // and w for endpoint one; a duplicated normal lane would reject this.
    orthographic_camera();
    TffFeatureRecord normal_crossing = feature(0.0f, 20.0f);
    normal_crossing.normals = uvec4(0u, 0u, 1u, 2u);
    if (!tff_source_crossing(inst, address, normal_crossing, t)) return 17;
    if (!close(t, 0.5f)) return 18;
    normal_crossing.normals = uvec4(0u, 0u, 1u, 3u);
    if (!tff_source_crossing(inst, address, normal_crossing, t)) return 19;
    if (!close(t, 1.0f)) return 20;
    normal_crossing.normals = uvec4(0u, 0u, 3u, 1u);
    if (!tff_source_crossing(inst, address, normal_crossing, t)) return 21;
    if (!close(t, 0.0f)) return 22;
    normal_crossing.normals = uvec4(0u, 0u, 2u, 3u);
    if (tff_source_crossing(inst, address, normal_crossing, t)) return 23;
    normal_crossing.normals = uvec4(0u, 0u, 3u, 3u);
    if (tff_source_crossing(inst, address, normal_crossing, t)) return 24;

    // The delegated geometry must reject an inactive source and nonfinite
    // endpoints rather than publishing a fabricated stationary crossing.
    TffFeatureRecord inactive = feature(0.0f, 20.0f);
    inactive.ident.w |= TFF_LIVE_INACTIVE;
    if (tff_source_crossing(inst, address, inactive, t)) return 25;
    TffFeatureRecord invalid = feature(0.0f, 20.0f);
    invalid.p1 = vec4(0.0f, 0.0f, NAN, 0.0f);
    if (tff_source_crossing(inst, address, invalid, t)) return 26;

    return 0;
}
'''


def test_production_source_crossing_predicate_offline_cpp():
    """Run the extracted P1/P2 production predicate through a C++ harness."""
    if shutil.which("c++") is None:
        pytest.skip("C++ compiler unavailable")
    program = PRELUDE + "\n" + _production_cpp() + "\n" + PROGRAM
    with tempfile.TemporaryDirectory(prefix="true-ff-source-crossing-") as directory:
        binary = str(Path(directory) / "source_crossing_unit")
        compiled = subprocess.run(
            ["c++", "-std=c++17", "-O1", "-x", "c++", "-", "-o", binary],
            input=program,
            text=True,
            capture_output=True,
        )
        assert compiled.returncode == 0, compiled.stderr
        checked = subprocess.run([binary], capture_output=True, text=True)
        assert checked.returncode == 0, checked.stderr


def test_production_source_links_preserve_vertex_topology_offline_cpp(tmp_path):
    """Execute the actual shared linker, including its crossing predicate.

    Fixtures supply one retained triangle, normal tokens and identity transforms.
    All 27 ternary vertex signs, both windings and all eight stored endpoint
    orientations are exercised. No GPU or asset/runtime claim follows.
    """
    compiler = shutil.which("c++")
    assert compiler, "offline production-link test requires C++"
    facts = r'''
#include <iostream>
const uint TFF_NONE=0xFFFFFFFFu;
struct uvec2 {
    uint v[2];
    explicit uvec2(uint a) : v{a,a} {}
    uvec2(uint a,uint b) : v{a,b} {}
    uint& operator[](uint i) { return v[i]; }
};
bool equal(vec3 a,vec3 b) { return a.x==b.x && a.y==b.y && a.z==b.z; }
bool all(bool v) { return v; }
TffFeatureRecord records[3];
TffFeatureRecord tff_feature(TffFeatureInstance,uint address) { return records[address]; }
uvec2 tff_adjacent_faces(TffFeatureInstance,uint) { return uvec2(0u,TFF_NONE); }
uvec4 tff_face(TffFeatureInstance,uint) { return uvec4(0u,1u,2u,0u); }
'''
    main = r'''
int main() {
    TffFeatureInstance inst{};
    cam.eye=vec4(0,0,4,1); cam.forward=vec4(0,0,1,0);
    vec4 vertices[3]={vec4(0,0,0,0),vec4(1,0,0,0),vec4(0,1,0,0)};
    uint checked=0;
    for(uint code=0;code<27;++code) for(uint reverse=0;reverse<2;++reverse)
    for(uint flips=0;flips<8;++flips) {
        uint normal[3]={code%3+1,(code/3)%3+1,(code/9)%3+1};
        bool crossing[3]; uint count=0;
        for(uint edge=0;edge<3;++edge) {
            uint a=reverse?(3-edge)%3:edge;
            uint b=reverse?(2-edge)%3:(edge+1)%3;
            crossing[edge]=(normal[a]==1u)!=(normal[b]==1u);
            count+=crossing[edge];
            if(flips&(1u<<edge)) { uint tmp=a; a=b; b=tmp; }
            records[edge]={vertices[a],vertices[b],uvec4(0,0,normal[a],normal[b]),uvec4(1,0,0,0)};
        }
        if(count!=0u && count!=2u) return 1;
        for(uint edge=0;edge<3;++edge) {
            uvec2 got=tff_source_links(inst,edge);
            if(!crossing[edge]) {
                if(got[0]!=TFF_NONE || got[1]!=TFF_NONE) return 2;
                continue;
            }
            uint other=0;
            while(other<3 && (other==edge || !crossing[other])) ++other;
            if(other==3) return 3;
            bool outgoing=normal[reverse?(3-edge)%3:edge]==1u;
            uint lane=outgoing?0u:1u;
            if(got[lane]!=other || got[lane^1u]!=TFF_NONE) return 4;
            if(tff_source_links(inst,other)[lane^1u]!=edge) return 5;
            uint saved=records[other].ident.x;
            records[other].ident.x=2u;
            uvec2 absent=tff_source_links(inst,edge);
            if(absent[0]!=TFF_NONE || absent[1]!=TFF_NONE) return 6;
            records[other].ident.x=saved;
        }
        ++checked;
    }
    std::cout<<checked;
}
'''
    link = "\n".join(_extract_function(SOURCE.read_text(), name) for name in
                     ("tff_source_links_geometry", "tff_source_links"))
    program = PRELUDE + _production_cpp() + facts + link + main
    binary = tmp_path / "source_links_unit"
    compiled = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", str(binary)],
                              input=program, text=True, capture_output=True)
    assert compiled.returncode == 0, compiled.stderr
    checked = subprocess.run([str(binary)], capture_output=True, text=True)
    assert checked.returncode == 0, (checked.returncode, checked.stderr)
    assert checked.stdout == "432"
