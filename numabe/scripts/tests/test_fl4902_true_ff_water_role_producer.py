"""Offline units for the True FF water role producer (FL-4902 lane W).

A glslang compile of the bound adapters (tff_role_water, tff_water_land,
tff_water_bank) inside a compute harness checks the GLSL of
composition/true_ff_role_water.glslinc; a second check compiles the P0
pre-pass stages that draw the water records. The world feature chords were
deleted with the screen-derived pipeline (FL-4907).

No Godot, GPU, capture, replay or proof infrastructure.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.fl4895_glsl_check import expand

ROOT = Path(__file__).resolve().parents[2]

HARNESS = '''#[compute]
#version 450
#define TRUE_FF_PARAMS_BINDING 1
#include "res://render/ascii/structure/true_ff_frame_params.glslinc"
#include "res://render/ascii/composition/true_ff_roles.glslinc"
layout(local_size_x=64,local_size_y=1,local_size_z=1) in;
layout(set=0,binding=0,std430) readonly buffer Structure { StructuralCell cells[]; } source;
layout(set=0,binding=2,std430) writeonly buffer Out { uvec4 values[]; } result;
layout(set=0,binding=15) uniform usampler2D region_ids;
#include "res://render/ascii/composition/true_ff_role_water.glslinc"
void main() {
	StructuralCell owner = source.cells[gl_GlobalInvocationID.x];
	vec2 land = tff_water_land(owner);
	result.values[gl_GlobalInvocationID.x] = uvec4(tff_role_water(owner), floatBitsToUint(land), tff_water_bank(owner));
}
'''


class TrueFFWaterRoleProducer(unittest.TestCase):
    def test_bound_producer_compiles_as_glsl(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "harness.glsl"
            harness.write_text(HARNESS)
            comp = Path(tmp) / "harness.comp"
            comp.write_text("#version 450\n" + expand(harness))
            r = subprocess.run(["glslangValidator", "-V", "-S", "comp", "-o", "/dev/null", str(comp)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])

    def test_water_geometry_consumers_compile_as_glsl(self):
        source = (ROOT / "godot_project/render/ascii/structure/true_ff_gbuffer.glsl").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            for stage, suffix in (("vertex", "vert"), ("fragment", "frag")):
                body = source.split("#[" + stage + "]", 1)[1].split("#[", 1)[0]
                harness = Path(tmp) / ("gbuffer." + suffix + ".glsl")
                harness.write_text(body)
                compiled = Path(tmp) / ("gbuffer." + suffix)
                compiled.write_text("#version 450\n" + expand(harness))
                result = subprocess.run(
                    ["glslangValidator", "-V", "-S", suffix, "-o", "/dev/null", str(compiled)],
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
