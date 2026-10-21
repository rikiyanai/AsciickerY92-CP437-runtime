"""FL-4907: offline float32 execution of production affine helper bodies.

The C++ vector/matrix primitives stand in for GLSL built-ins. NumPy supplies
the independent inverse/SVD oracle. This is not Metal execution or a proof run.
"""
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from test_true_ff_density_windows import function_body

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "godot_project/render/ascii/features/true_ff_feature_passes.glslinc"
BOUNDS = ROOT / "godot_project/render/ascii/features/true_ff_feature_bounds.glslinc"


@pytest.fixture(scope="module")
def affine_unit(tmp_path_factory):
    compiler = shutil.which("c++")
    assert compiler
    source = SOURCE.read_text() + "\n" + BOUNDS.read_text()
    functions = "\n".join(function_body(source, name) for name in (
        "tff_xform_covector", "tff_xform_normal", "tff_instance_metric", "tff_xform_cone_angle",
    )).replace("out float condition_bound", "float& condition_bound")
    program = r'''
#include <algorithm>
#include <cmath>
#include <iomanip>
#include <iostream>
using std::min; using std::max; using std::abs; using std::sqrt;
using std::atan; using std::tan;
float max(float a, double b) { return std::max(a, float(b)); }
struct vec3 {
    float x, y, z;
    vec3 operator/(float a) const { return {x/a,y/a,z/a}; }
};
vec3 operator+(vec3 a, vec3 b) { return {a.x+b.x,a.y+b.y,a.z+b.z}; }
vec3 operator*(vec3 a, float b) { return {a.x*b,a.y*b,a.z*b}; }
float dot(vec3 a, vec3 b) { return a.x*b.x+a.y*b.y+a.z*b.z; }
float length(vec3 a) { return sqrt(dot(a,a)); }
vec3 cross(vec3 a, vec3 b) { return {a.y*b.z-a.z*b.y,a.z*b.x-a.x*b.z,a.x*b.y-a.y*b.x}; }
struct mat3 {
    vec3 col[3];
    vec3 operator[](int i) const { return col[i]; }
};
mat3 transpose(mat3 m) {
    return {{{m[0].x,m[1].x,m[2].x},{m[0].y,m[1].y,m[2].y},{m[0].z,m[1].z,m[2].z}}};
}
mat3 inverse(mat3 m) {
    float det=dot(m[0],cross(m[1],m[2]));
    return transpose({{cross(m[1],m[2])/det,cross(m[2],m[0])/det,cross(m[0],m[1])/det}});
}
vec3 operator*(mat3 m, vec3 v) { return m[0]*v.x+m[1]*v.y+m[2]*v.z; }
struct TffFeatureInstance { mat3 local_to_world; };
const float TFF_PI=3.14159265358979323846f, TFF_HALF_PI=TFF_PI*.5f;
''' + functions + r'''
int main() {
    float a[9], angle; vec3 n;
    std::cout << std::setprecision(9);
    while(std::cin >> a[0]) {
        for(int i=1;i<9;++i) std::cin >> a[i];
        std::cin >> n.x >> n.y >> n.z >> angle;
        TffFeatureInstance inst{{{{a[0],a[3],a[6]},{a[1],a[4],a[7]},{a[2],a[5],a[8]}}}};
        vec3 cov=tff_xform_covector(inst,n), unit=tff_xform_normal(inst,n);
        float condition, bound=tff_instance_metric(inst,condition);
        std::cout << cov.x << ' ' << cov.y << ' ' << cov.z << ' '
                  << unit.x << ' ' << unit.y << ' ' << unit.z << ' '
                  << bound << ' ' << condition << ' ' << tff_xform_cone_angle(angle,condition) << '\n';
    }
}
'''
    binary = tmp_path_factory.mktemp("true-ff-affine") / "affine_unit"
    compiled = subprocess.run([compiler, "-std=c++17", "-O1", "-x", "c++", "-", "-o", str(binary)],
                              input=program, text=True, capture_output=True)
    assert compiled.returncode == 0, compiled.stderr
    return binary


def test_production_affine_helpers_against_inverse_and_svd(affine_unit):
    rng = np.random.default_rng(4907)
    bases = [np.eye(3), np.diag([-2., .5, 3.]),
             np.array([[1., 1., 0.], [0., 1., 0.], [0., 0., 1.]]),
             np.array([[1., .6, 0.], [0., .8, 0.], [0., 0., 1.]])]
    bases += [np.eye(3) + rng.normal(scale=.2, size=(3, 3)) for _ in range(64)]
    normal = np.array([1., 2., 3.]) / np.sqrt(14.)
    data = "\n".join(" ".join(map(str, [*m.flat, *normal, .2])) for m in bases)
    result = subprocess.run([str(affine_unit)], input=data, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    rows = np.array([list(map(float, line.split())) for line in result.stdout.splitlines()])
    assert len(rows) == len(bases)
    for basis, row in zip(bases, rows):
        expected = np.linalg.inv(basis).T @ normal
        np.testing.assert_allclose(row[:3], expected, rtol=2e-6, atol=2e-7)
        np.testing.assert_allclose(row[3:6], expected / np.linalg.norm(expected), rtol=2e-6, atol=2e-7)
        assert row[6] >= np.linalg.svd(basis, compute_uv=False)[0] - 1e-6
        if row[7] < 0.:
            assert row[8] == -1.
        else:
            assert row[7] >= np.linalg.cond(basis) - 1e-6
            assert row[8] >= .2 - 1e-6
