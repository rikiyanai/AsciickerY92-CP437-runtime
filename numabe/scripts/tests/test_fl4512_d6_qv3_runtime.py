from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "assets/glyphs/fixtures/fl4512_d6_aspect_golden_v1.json"


def _cpp_double(value: float) -> str:
    return format(float(value), ".17g")


def test_native_qv3_polar_and_q15_match_the_golden_corpus(tmp_path: Path) -> None:
    fixture = json.loads(FIXTURE.read_text())
    source = tmp_path / "aspect_qv3_probe.cpp"
    binary = tmp_path / "aspect_qv3_probe"
    lines = [
        '#include "engine/render/fl4512_aspect_qv3.h"',
        "#include <cmath>",
        "int main() {",
    ]
    next_failure = 1
    for case in fixture["polar_cases"]:
        matrix = ",".join(_cpp_double(value) for value in case["C_row_major"])
        lines.append(f"  {{ double c[9]={{{matrix}}}; Fl4512AspectPolarFactV3 f={{}};")
        if "expected_cause" in case:
            cause = {
                "reflected_transform": "FL4512_ASPECT_POLAR_REFLECTED",
                "determinant_overflow": "FL4512_ASPECT_POLAR_DETERMINANT_OVERFLOW",
            }[case["expected_cause"]]
            lines.append(
                "    if (Fl4512RightPolarV3(c,&f) || f.failure!="
                f"{cause}) return {next_failure}; }}"
            )
        else:
            expected = case["expected"]
            expected_r = ",".join(_cpp_double(value) for value in expected["R_row_major"])
            expected_s = ",".join(_cpp_double(value) for value in expected["S_row_major"])
            lines.extend(
                [
                    f"    double er[9]={{{expected_r}}}, es[9]={{{expected_s}}};",
                    f"    if (!Fl4512RightPolarV3(c,&f)) return {next_failure};",
                    f"    if (f.iterations!={expected['iterations']}) return {next_failure + 1};",
                    "    for(int i=0;i<9;i++) if(std::fabs(f.r[i]-er[i])>"
                    "FL4512_ASPECT_POLAR_TOLERANCE_V3 || std::fabs(f.s[i]-es[i])>"
                    f"FL4512_ASPECT_POLAR_TOLERANCE_V3) return {next_failure + 2};",
                    f"    if (!f.valid || f.reconstruction_residual>FL4512_ASPECT_POLAR_TOLERANCE_V3) return {next_failure + 3}; }}",
                ]
            )
        next_failure += 4
    lines.extend(["  return 0;", "}"])
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_runtime_source_has_no_qv2_aspect_owner() -> None:
    sources = "\n".join(
        path.read_text()
        for path in (
            ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp",
            ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h",
            ROOT / "engine/render/render_world_pass.cpp",
            ROOT / "engine/render/render_internal.h",
            ROOT / "engine/render/render_resolve.cpp",
            ROOT / "engine/render/render_frame_input.h",
            ROOT / "platform/terminal_gl_present.cpp",
        )
    )
    assert "qv2" not in sources
    assert "Fl4512AspectQ15ForCurrentMeshV2" not in sources
    assert "aspect_quantization_version != 2" not in sources
    assert "aspect_quantization_version = f.aspect_valid ? 2" not in sources
    assert "quantization_version = 2" not in sources
    assert "Fl4512AspectDirectionQ15V3" in sources
    assert "aspect_quantization_version = f.aspect_valid ? 3" in sources
    assert "cell_aspect_polar_c_f64_bits" in sources
    assert "diagnostic_order" in sources
    assert "reconstruction_residual" in sources
