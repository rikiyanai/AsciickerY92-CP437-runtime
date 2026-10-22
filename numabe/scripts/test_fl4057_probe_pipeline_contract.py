#!/usr/bin/env python3
"""Prove the FL-4057 pipeline has one retained-scene authority.

FL-4431 physically deleted the eight-pass probe pipeline this test originally
cross-checked. The current perspective successor is a candidate-only probe
boundary: probe data enters VSB at b61-b63, VSB alone writes b64, Product reads
that record, and the compositor paints finalized output. Orthographic direct
SceneQuery remains projection-owned inside VSB. This contract pins that
ownership boundary and compiles each surviving shader.
The small compiler helpers remain public because the Q3 semantic tests share
the exact same Vulkan 1.1 falsifier.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import shutil
import subprocess
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
SHADERS = REPO / "godot_project"
HOST = REPO / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
SURVIVING_PASSES = {
    "visible_surface_builder": "render/backends/ortho/visibility/visible_surface_builder.glsl",
    "cell_owner_product": "render/product/cell_owner_product.glsl",
    "ascii_compositor": "render/ascii/raster/ascii_compositor.glsl",
}
DELETED_PASSES = (
    "ascii_probe_capture",
    "ascii_probe_capture_downsample",
    "ascii_probe_cull",
    "ascii_probe_edge_mask",
    "ascii_probe_light",
    "ascii_probe_resolve",
    "ascii_probe_splat",
    "ascii_probe_splat_args",
    "terrain_cell_resolve",
    "terrain_cell_splat",
    "terrain_cell_splat_clear",
)


def split_stages(source: str) -> dict[str, str]:
    """Split a Godot-tagged shader into named compiler stage bodies."""
    stages: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in source.splitlines():
        marker = re.match(r"\s*#\[(compute|vertex|fragment)\]\s*$", line)
        if marker:
            if current is not None:
                stages[current] = "\n".join(lines)
            current = marker.group(1)
            lines = []
        else:
            lines.append(line)
    if current is not None:
        stages[current] = "\n".join(lines)
    return stages


def glslang_bin() -> str | None:
    """Return the installed GLSL compiler used by every static shader gate."""
    for candidate in ("glslangValidator", "glslang"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def compile_stage(
    compiler: str, stage: str, body: str, temp_dir: Path
) -> tuple[bool, str]:
    """Compile one isolated stage against the shipping Vulkan 1.1 target."""
    suffix = {"compute": "comp", "vertex": "vert", "fragment": "frag"}[stage]
    source_path = temp_dir / f"stage.{suffix}"
    output_path = temp_dir / f"stage.{suffix}.spv"
    source_path.write_text(body, encoding="utf-8")
    result = subprocess.run(
        [
            compiler,
            "--target-env",
            "vulkan1.1",
            "-S",
            suffix,
            "-o",
            str(output_path),
            str(source_path),
        ],
        capture_output=True,
        text=True,
        errors="replace",
    )
    detail = (result.stdout + result.stderr).strip()
    return result.returncode == 0, detail


def main() -> int:
    """Check deletion, single authority, ABI handoffs, then compile the chain."""
    missing = [name for name, path in SURVIVING_PASSES.items() if not (SHADERS / path).is_file()]
    assert not missing, f"missing retained-scene shaders: {missing}"
    survivors = [name for name in DELETED_PASSES if list(SHADERS.rglob(f"{name}.glsl"))]
    assert not survivors, f"retired probe shaders returned: {survivors}"

    vsb = (SHADERS / "render/backends/ortho/visibility/visible_surface_builder.glsl").read_text(encoding="utf-8")
    product = _expand_includes((SHADERS / "render/product/cell_owner_product.glsl").read_text(encoding="utf-8"))
    compositor = (SHADERS / "render/ascii/raster/ascii_compositor.glsl").read_text(encoding="utf-8")
    host = HOST.read_text(encoding="utf-8")
    builder = (
        REPO / "godot_project" / "render" / "core" / "contracts" / "visibility" / "VisibleSurfaceABI.gd"
    ).read_text(encoding="utf-8")
    # Binding 64 intentionally omits `writeonly`. Godot's Apple translation
    # rejected the prior runtime-array qualifier path, so this retained-pipeline
    # contract pins the same Metal-safe declaration as the activation battery.
    vsb_output = re.search(
        r"binding\s*=\s*64\s*,\s*std430\s*\)\s*"
        r"(?P<qualifiers>(?:(?:readonly|writeonly|restrict)\s+)*)"
        r"buffer\s+VisibleSurfaceProducts",
        re.sub(r"//[^\n]*", "", vsb),
    )
    assert vsb_output is not None, "VSB binding 64 output block is missing"
    assert "writeonly" not in vsb_output.group("qualifiers").split()
    assert "binding = 61, std430) readonly buffer ProbeCandidateRecords" in vsb
    assert "binding = 62, std140) uniform ProbeCandidateControl" in vsb
    assert "binding = 63, std430) buffer ProbeCandidateDiagnostics" in vsb
    assert "const PROBE_CANDIDATE_RECORD_STRIDE_BYTES := 128" in builder
    assert "const RECORD_STRIDE_BYTES := 96" in builder
    assert "binding = 67, std430) readonly buffer VsbVisibleSurfaceProducts" in product
    assert "visible_surface_products.records[" in vsb
    # Product stages all four quarter-cell records so the one falling-leaf bank
    # replay can visit them together. Parse the actual assignment instead of
    # matching its old thread-local spelling: exactly one binding-55 store must
    # publish the post-leaf staged record for the matching slot.
    final_product_stores = re.findall(
        r"finalized_probe_subcell_products\.products\s*"
        r"\[\s*([^]]+?)\s*\]\s*=\s*([^;]+);",
        re.sub(r"//[^\n]*", "", product),
        re.S,
    )
    assert [
        (re.sub(r"\s+", "", index), re.sub(r"\s+", "", value))
        for index, value in final_product_stores
    ] == [("final_index", "staged_final_records[slot]")]
    assert re.sub(r"//[^\n]*", "", compositor).count("imageStore(") == 1
    assert "rd.compute_list_bind_compute_pipeline(product_compute_list, vsb_dispatch_pipeline)" in host
    assert "_dispatch_cell_owner_product(" in host
    # The compositor can select its ordinary shader pipeline plus the
    # capture-instrumented variant, but both paint routes bind that one selected
    # pipeline immediately before the final image dispatch.
    compact_host = re.sub(r"\s+", "", re.sub(r"#[^\n]*", "", host))
    compositor_bind = (
        "rd.compute_list_bind_compute_pipeline("
        "compute_list,compositor_pipeline_for_frame)"
    )
    compositor_set = (
        "rd.compute_list_bind_uniform_set(compute_list,uniform_set,0)"
    )
    compositor_dispatch = (
        "rd.compute_list_dispatch(compute_list,groups_x,groups_y,1)"
    )
    assert compact_host.count(compositor_bind) == 1
    assert compact_host.index(compositor_bind) < compact_host.index(compositor_set)
    assert compact_host.index(compositor_set) < compact_host.index(compositor_dispatch)

    compiler = glslang_bin()
    assert compiler is not None, "glslangValidator/glslang is required"
    with tempfile.TemporaryDirectory(prefix="fl4057-retained-pipeline-") as temp:
        temp_dir = Path(temp)
        for name in SURVIVING_PASSES:
            source = (SHADERS / SURVIVING_PASSES[name]).read_text(encoding="utf-8")
            stages = split_stages(source)
            assert "compute" in stages, f"{name} compute stage missing"
            ok, detail = compile_stage(compiler, "compute", stages["compute"], temp_dir)
            assert ok, f"{name} failed Vulkan 1.1 compile: {detail}"

    print("FL4057_RETAINED_SCENE_PIPELINE_CONTRACT_OK passes=3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
