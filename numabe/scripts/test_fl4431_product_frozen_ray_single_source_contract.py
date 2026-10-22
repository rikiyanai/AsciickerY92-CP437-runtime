#!/usr/bin/env python3
"""FL-4431 Product single-source frozen-ray ownership contract.

Product builds the exact binding-68 VSB ray once inside each admitted subcell's
compile slot and threads that value plus its independent validity bit through
the same-slot consumers. Separate center/base and below-water samples remain
intentional because they are different samples, not reconstructed same-slot
receipts.

Run: ``python3 scripts/test_fl4431_product_frozen_ray_single_source_contract.py``
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
PRODUCT_PATH = REPO / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def compact(source: str) -> str:
    return re.sub(r"\s+", "", without_comments(source))


def function_span(source: str, name: str) -> tuple[int, int]:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        source,
        re.S,
    )
    assert match is not None, f"missing function definition: {name}"
    brace = source.find("{", match.start())
    depth = 0
    for pos in range(brace, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), pos + 1
    raise AssertionError(f"unterminated function: {name}")


def function_body(source: str, name: str) -> str:
    start, end = function_span(source, name)
    body = source[start:end]
    return body[body.find("{") + 1 : body.rfind("}")]


def replace_in_function(
    source: str,
    name: str,
    old: str,
    new: str,
) -> str:
    start, end = function_span(source, name)
    function = source[start:end]
    assert function.count(old) == 1, (
        f"{name}: mutation target count={function.count(old)} for {old!r}"
    )
    return source[:start] + function.replace(old, new, 1) + source[end:]


def assert_single_source_contract(source: str) -> None:
    source_compact = compact(source)
    main = compact(function_body(source, "main"))
    receiver = compact(function_body(source, "vsb_product_frozen_receiver"))
    empty_receiver = compact(
        function_body(source, "vsb_product_empty_frozen_receiver")
    )
    compile_input = compact(
        function_body(source, "vsb_product_compile_input")
    )
    water_for_ray = compact(
        function_body(source, "vsb_product_water_plane_hit_for_ray")
    )
    water_below = compact(
        function_body(source, "vsb_product_water_plane_hit")
    )
    water_footprint = compact(
        function_body(source, "vsb_product_water_surface_footprint")
    )
    prepare_water = compact(
        function_body(source, "vsb_product_prepare_water")
    )
    water_apply = compact(
        function_body(source, "q3_probe_payload_apply_water")
    )
    payload_root = compact(
        function_body(source, "compile_probe_subcell_payload")
    )

    # One definition plus the known sample owners: water-below, center terrain,
    # center surface, admitted-slot Product compilation, and center sky glyph.
    # Any restored same-slot reconstruction turns this red.
    assert source_compact.count("vsb_product_frozen_ray(") == 6
    assert main.count("vsb_product_frozen_ray(") == 2
    assert compile_input.count("vsb_product_frozen_ray(") == 0
    assert receiver.count("vsb_product_frozen_ray(") == 0
    assert water_apply.count("vsb_product_frozen_ray(") == 0
    assert "q3_probe_payload_safe_ray_dir" not in source_compact

    # The validity bit is its own receipt. It cannot be inferred from receiver
    # admission, ray length, the owner kind, nor a default direction.
    assert "boolray_valid;" in source_compact
    assert "receiver.ray_valid=false;" in empty_receiver
    assert (
        "receiver.ray=frozen_ray;"
        "receiver.ray_valid=frozen_ray_valid;" in receiver
    )
    assert "if(!receiver.ray_valid)returnreceiver;" in receiver
    assert (
        "frozen_ray_valid=vsb_product_frozen_ray(observer,"
        "vec2(subcell)+vec2(0.5),subcell_extent,frozen_ray);" in main
    )
    assert (
        "frozen_receiver=vsb_product_frozen_receiver("
        "visible,decode,frozen_ray,frozen_ray_valid);" in main
    )
    assert "VsbFrozenReceiverContextfrozen_receivers[4];" not in main
    assert "boolstaged_frozen_receiver_valid[4];" in main
    assert "floatstaged_frozen_receiver_depth[4];" in main
    assert "vec3staged_frozen_receiver_position[4];" in main

    # Both direct sky and retained-hit compilation consume the exact same-slot
    # value and the independent validity bit.
    assert (
        "input_value.ray=frozen_ray;"
        "if(frozen_ray_valid)" in main
    )
    assert (
        "vsb_product_compile_input(cohort,observer,"
        "visible,subcell,subcell_extent,"
        "frozen_ray,frozen_ray_valid)" in main
    )
    assert "input_value.ray=frozen_ray;" in compile_input
    assert (
        "if(!frozen_ray_valid){"
        "vsb_product_prepare_diagnostic=0x0201u;"
        "returninput_value;}" in compile_input
    )

    # Water reuses the current ray without changing any hit arithmetic. The
    # neighboring row remains a separately constructed ray by definition.
    water_math = (
        "if(abs(ray.dir.y)<1.0e-4)returnfalse;"
        "floatdepth=(WATER_SURFACE_Y-ray.near_world.y)/ray.dir.y;"
        "if(!vsb_product_finite_float(depth)"
        "||depth<=0.0||depth>ray.len){returnfalse;}"
        "hit=ray.near_world+ray.dir*depth;"
        "returnvsb_product_finite_vec3(hit);"
    )
    assert water_math in water_for_ray
    assert water_below.count("vsb_product_frozen_ray(") == 1
    assert (
        "returnvsb_product_water_plane_hit_for_ray(ray,hit);"
        in water_below
    )
    assert (
        "vsb_product_water_plane_hit_for_ray(current_ray,hit_here)"
        in water_footprint
    )
    assert (
        "vsb_product_water_plane_hit(observer,"
        "subcell+ivec2(0,1),subcell_extent,hit_below)"
        in water_footprint
    )
    assert (
        "vsb_product_water_surface_footprint(observer,"
        "input_value.ray,subcell,subcell_extent)" in prepare_water
    )

    # Far-water sky takes the direction already carried by compile input.
    assert "horizon_ray.dir=normalize(frozen_ray_dir);" in water_apply
    assert (
        "q3_probe_payload_apply_water(cohort,observer,"
        "context,facts,frozen_ray_dir,glyph,fg,bg)" in payload_root
    )
    assert (
        "compile_probe_subcell_payload(cohort,observer,"
        "input_value.context,input_value.facts,input_value.ray.dir)" in main
    )


def mutation_witnesses(source: str) -> dict[str, str]:
    mutants = {
        "receiver_reconstruction_restored": replace_in_function(
            source,
            "vsb_product_frozen_receiver",
            "\treceiver.ray = frozen_ray;",
            "\tvsb_product_frozen_ray(vec2(0.5), ivec2(1), receiver.ray);",
        ),
        "compile_input_reconstruction_restored": replace_in_function(
            source,
            "vsb_product_compile_input",
            "\tinput_value.ray = frozen_ray;",
            "\tvsb_product_frozen_ray("
            "vec2(subcell) + vec2(0.5), subcell_extent, input_value.ray);",
        ),
        "direct_sky_reconstruction_restored": replace_in_function(
            source,
            "main",
            "\t\t\tinput_value.ray = frozen_ray;",
            "\t\t\tvsb_product_frozen_ray("
            "vec2(subcell) + vec2(0.5), subcell_extent, input_value.ray);",
        ),
        "water_current_reconstruction_restored": replace_in_function(
            source,
            "vsb_product_water_surface_footprint",
            "vsb_product_water_plane_hit_for_ray(current_ray, hit_here)",
            "vsb_product_water_plane_hit(subcell, subcell_extent, hit_here)",
        ),
        "distant_sky_reconstruction_restored": replace_in_function(
            source,
            "q3_probe_payload_apply_water",
            "\t\thorizon_ray.dir = normalize(frozen_ray_dir);",
            "\tViewRay ray;\n"
            "\tvsb_product_frozen_ray("
            "vec2(context.slot_xy) + vec2(0.5), "
            "context.ansi_dims * 2, ray);\n"
            "\t\thorizon_ray.dir = ray.dir;",
        ),
        "compile_invalid_bit_bypassed": replace_in_function(
            source,
            "vsb_product_compile_input",
            "\tif (!frozen_ray_valid) {",
            "\tif (false) {",
        ),
        "receiver_invalid_bit_bypassed": replace_in_function(
            source,
            "vsb_product_frozen_receiver",
            "\tif (!receiver.ray_valid) return receiver;",
            "\tif (false) return receiver;",
        ),
        "sky_invalid_bit_bypassed": replace_in_function(
            source,
            "main",
            "\t\t\tif (frozen_ray_valid) {",
            "\t\t\tif (frozen_receiver.valid) {",
        ),
    }
    reds: dict[str, str] = {}
    for name, mutant in mutants.items():
        try:
            assert_single_source_contract(mutant)
        except AssertionError as error:
            reds[name] = str(error) or "AssertionError"
            continue
        raise AssertionError(f"mutation stayed green: {name}")
    return reds


def main() -> int:
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    source = source.replace('#include "res://render/product/diagnostics/product_observer.glslinc"',
        (PRODUCT_PATH.parent / "product_observer.glslinc").read_text(encoding="utf-8"))
    assert_single_source_contract(source)
    reds = mutation_witnesses(source)
    for name, detail in reds.items():
        print(f"  RED {name}: {detail}")
    print(
        "FL4431_PRODUCT_FROZEN_RAY_SINGLE_SOURCE_CONTRACT_PASS "
        f"mutations={','.join(reds)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
