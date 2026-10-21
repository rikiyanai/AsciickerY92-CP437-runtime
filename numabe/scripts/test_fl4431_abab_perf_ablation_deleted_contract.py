#!/usr/bin/env python3
"""Guard the physical retirement of FL-4431's runtime measurement owner.

The shipping renderer has one selected program.  This contract rejects any
resurrected host scheduler, dispatch omission, Product phase skip, Product
spine checkpoint, and VSB acceleration specialization.  The explicit b62
evidence specialization remains permitted because it observes output without
changing visible ownership.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import pathlib
import re
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOST_PATH = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
PRODUCT_PATH = ROOT / "godot_project/render/product/cell_owner_product.glsl"
VSB_PATH = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"

HOST_FORBIDDEN = (
    "FL4431_ABAB",
    "_fl4431_abab",
    "FL4431_PERF_",
    "_fl4431_perf_",
    "FL4431_PRODUCT_SPINE",
    "FL4431_VSB_DIAG_SAMPLE",
)
PRODUCT_FORBIDDEN = (
    "PRODUCT_DECODE_FIRST",
    "PRODUCT_SKIP_",
    "PRODUCT_SPINE_STAGE",
    "PRODUCT_LEAF_LIVE_MASK_ENABLED",
    "PRODUCT_DYNAMIC_SHADOW_BOUND_ENABLED",
    "PRODUCT_WAKE_LANE_BOUND_ENABLED",
    "q3_probe_payload_spine_marker",
    "vsb_product_spine_marker_from_record",
    "product_spine_stage_is_truncated",
)
VSB_FORBIDDEN = (
    "VSB_HEIGHT_CORNER_CACHE_ENABLED",
    "VSB_B82_MIN_SKIP_ENABLED",
    "VSB_DYNAMIC_LEAF_AABB_ENABLED",
    "VSB_STATIC_TLAS_ORDERED_ENABLED",
    "VSB_B83_MIN_SKIP_ENABLED",
    "FL4431_PERF_",
)


def gd_function(source: str, name: str) -> str | None:
    """Return one top-level GDScript function without matching other owners."""

    match = re.search(rf"(?m)^func\s+{re.escape(name)}\b", source)
    if match is None:
        return None
    next_function = re.search(r"(?m)^func\s+\w+", source[match.end() :])
    end = (
        len(source)
        if next_function is None
        else match.end() + next_function.start()
    )
    return source[match.start() : end]


def glsl_function(source: str, name: str) -> str | None:
    """Return one GLSL function by balancing braces from its declaration."""

    match = re.search(rf"\bvoid\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
    if match is None:
        return None
    depth = 0
    for index in range(match.end() - 1, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    return None


def contract(host: str, product: str, vsb: str) -> bool:
    if any(token in host for token in HOST_FORBIDDEN):
        return False
    if any(token in product for token in PRODUCT_FORBIDDEN):
        return False
    if any(token in vsb for token in VSB_FORBIDDEN):
        return False

    # Host construction exposes only b62's explicit evidence specialization.
    # Dispatch ownership is pinned inside the live render callback. A whole-file
    # token search would also match the helper definition after the callback
    # had stopped calling it, falsely reporting that Product still ran.
    render_callback = gd_function(host, "_render_callback")
    if render_callback is None:
        return False
    if host.count("product_b62_diagnostics_specialization.constant_id = 17") != 1:
        return False
    if "product_specializations.append(product_b62_diagnostics_specialization)" not in host:
        return False
    if (
        render_callback.count(
            "rd.compute_list_bind_compute_pipeline(product_compute_list, vsb_dispatch_pipeline)"
        )
        != 1
    ):
        return False
    if render_callback.count("_dispatch_cell_owner_product(") != 1:
        return False
    if (
        "rd.compute_list_bind_compute_pipeline(\n"
        "\t\t\tcompute_list,\n"
        "\t\t\tcompositor_pipeline_for_frame\n"
        "\t\t)" not in render_callback
    ):
        return False

    # Product keeps decode-first refusal, full compiler/finalizer phases, one
    # actor selection per ANSI invocation, and exactly one final b55 store.
    product_shapes = (
        "if (decode == VSB_PRODUCT_DECODE_ABSENT)",
        "vsb_product_select_actor_paint_quad(",
        "compile_probe_subcell_payload(",
        "vsb_product_finalize_record(",
        "vsb_product_consider_leaf_record(",
        "LeafCellProduct staged_leaf_winners[4];",
        "float staged_leaf_best_alpha[4];",
    )
    if any(shape not in product for shape in product_shapes):
        return False
    product_main = glsl_function(product, "main")
    if product_main is None:
        return False
    # Explicit absence must reject before Product allocates the large compiler
    # input. This is the selected decode-first program, not a specialization.
    # Scoping the comparison to main prevents helper declarations elsewhere in
    # the shader from satisfying the ordering check.
    absence_position = product_main.find(
        "if (decode == VSB_PRODUCT_DECODE_ABSENT)"
    )
    compiler_input_position = product_main.find("VsbProductCompileInput")
    if not (
        0 <= absence_position < compiler_input_position
        and product_main.count("VsbProductCompileInput") == 1
    ):
        return False
    final_store = (
        "finalized_probe_subcell_products.products[final_index] =\n"
        "\t\t\tstaged_final_records[slot];"
    )
    if product.count(final_store) != 1:
        return False

    # VSB permanently keeps each measured-winning acceleration and requires
    # the shipping b83 identity rather than offering a lower-hierarchy arm.
    vsb_shapes = (
        "if (!cache_valid)",
        "max(lower_guard_enter_y, exit_y) < tile_min - epsilon",
        "instance.aabb_min.xyz - vec3(leaf_epsilon)",
        "instance.aabb_max.xyz + vec3(leaf_epsilon)",
        "!= floatBitsToUint(1.0)",
        "texelFetch(heightfield_super_bounds_tex, super_tile, 0).rg",
    )
    return all(shape in vsb for shape in vsb_shapes)


def shader_compiles(source: str) -> bool:
    compiler = shutil.which("glslangValidator")
    if compiler is None:
        return True
    stripped = source.splitlines()
    if stripped and stripped[0].strip() == "#[compute]":
        stripped = stripped[1:]
    with tempfile.TemporaryDirectory(prefix="fl4431-ablation-mutant-") as tmp:
        output = pathlib.Path(tmp) / "mutant.spv"
        completed = subprocess.run(
            [
                compiler,
                "--stdin",
                "-S",
                "comp",
                "--target-env",
                "vulkan1.1",
                "-V",
                "-o",
                str(output),
            ],
            input="\n".join(stripped) + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    return completed.returncode == 0


def main() -> int:
    host = HOST_PATH.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    vsb = VSB_PATH.read_text(encoding="utf-8")
    if not contract(host, product, vsb):
        raise AssertionError("shipping measurement-owner deletion contract failed")

    mutants = {
        "host_abab_scheduler_resurrected": (
            host.replace(
                "var _vsb_named_absence_warnings: Dictionary = {}",
                "var _vsb_named_absence_warnings: Dictionary = {}\n"
                "var _fl4431_abab_gate: String = \"\"",
                1,
            ),
            product,
            vsb,
        ),
        "host_product_dispatch_removed": (
            host.replace(
                "\t\t\t_dispatch_cell_owner_product(\n",
                "\t\t\t_missing_cell_owner_product_dispatch(\n",
                1,
            ),
            product,
            vsb,
        ),
        "product_phase_skip_resurrected": (
            host,
            product.replace(
                "layout(constant_id = 17) const bool PRODUCT_B62_DIAGNOSTICS_ENABLED",
                "layout(constant_id = 5) const bool PRODUCT_SKIP_FOLIAGE = false;\n"
                "layout(constant_id = 17) const bool PRODUCT_B62_DIAGNOSTICS_ENABLED",
                1,
            ),
            vsb,
        ),
        # These five owners were measured as regressions and then physically
        # retired. Each compile-valid resurrection proves the deletion guard
        # names the specific owner rather than only broad ABAB vocabulary.
        "product_leaf_live_mask_resurrected": (
            host,
            product + "\nconst bool PRODUCT_LEAF_LIVE_MASK_ENABLED = false;\n",
            vsb,
        ),
        "product_dynamic_shadow_bound_resurrected": (
            host,
            product + "\nconst bool PRODUCT_DYNAMIC_SHADOW_BOUND_ENABLED = false;\n",
            vsb,
        ),
        "product_wake_lane_bound_resurrected": (
            host,
            product + "\nconst bool PRODUCT_WAKE_LANE_BOUND_ENABLED = false;\n",
            vsb,
        ),
        "vsb_cache_specialization_resurrected": (
            host,
            product,
            vsb.replace(
                "const float A3D_SOURCE_HEIGHT_DIVISOR",
                "layout(constant_id = 1) const bool "
                "VSB_HEIGHT_CORNER_CACHE_ENABLED = true;\n"
                "const float A3D_SOURCE_HEIGHT_DIVISOR",
                1,
            ),
        ),
        "vsb_static_ordered_resurrected": (
            host,
            product,
            vsb + "\nconst bool VSB_STATIC_TLAS_ORDERED_ENABLED = false;\n",
        ),
        "vsb_b83_minimum_resurrected": (
            host,
            product,
            vsb + "\nconst bool VSB_B83_MIN_SKIP_ENABLED = false;\n",
        ),
    }
    for name, (mutant_host, mutant_product, mutant_vsb) in mutants.items():
        if contract(mutant_host, mutant_product, mutant_vsb):
            raise AssertionError(f"mutation survived deletion contract: {name}")
        if name.startswith("product_") and not shader_compiles(mutant_product):
            raise AssertionError(f"Product resurrection mutant did not compile: {name}")
        if name.startswith("vsb_") and not shader_compiles(mutant_vsb):
            raise AssertionError(f"VSB resurrection mutant did not compile: {name}")

    print(
        "FL4431_ABAB_PERF_ABLATION_DELETED_CONTRACT_PASS "
        f"host_tokens={len(HOST_FORBIDDEN)} "
        f"product_tokens={len(PRODUCT_FORBIDDEN)} "
        f"vsb_tokens={len(VSB_FORBIDDEN)} mutants={len(mutants)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
