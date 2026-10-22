#!/usr/bin/env python3
"""FL-4431 Q3 Slice 2: keep sky out of the admitted-hit compiler.

The old Slice-2 contract treated a dormant resolve-era sky helper forest as a
live compiler closure.  The retained owner law has two explicit paths instead:
the no-hit decode calls the Product sky adapter, while admitted VSB hits map to
static mesh, terrain, water, or dynamic mesh before entering the payload
compiler.  This is static source/compiler evidence only.  It makes no runtime,
Metal, FPS, framebuffer, or visual-acceptance claim.
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
PRODUCT_PATH = REPO / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
DEAD_SKY_OWNERS = (
    "q3_probe_payload_sky_glyph",
    "q3_probe_payload_sky_bg",
    "q3_probe_payload_sky_fg",
    "q3_probe_payload_compile_sky_record",
)


def without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def compact(source: str) -> str:
    return re.sub(r"\s+", "", without_comments(source))


def assert_contract(source: str) -> None:
    executable = without_comments(source)
    for name in DEAD_SKY_OWNERS:
        assert name not in executable, f"dead sky owner resurrected: {name}"

    compiler = compact(
        function(source, "ProbePayloadCompileResult compile_probe_subcell_payload(")
    )
    assert compiler.count("q3_probe_payload_visible_record(") == 1
    assert "result.record=q3_probe_payload_visible_record(" in compiler
    assert "OWNER_SKY" not in compiler
    assert "q3_probe_payload_sky_record(" not in compiler

    prepare = compact(
        function(source, "VsbProductCompileInput vsb_product_compile_input(")
    )
    for owner in (
        "VSB_OWNER_STATIC",
        "VSB_OWNER_TERRAIN",
        "VSB_OWNER_WATER",
        "VSB_OWNER_DYNAMIC",
    ):
        assert f"record.owner_kind=={owner}" in prepare, (
            f"admitted hit owner mapping missing: {owner}"
        )
    assert "record.owner_kind==OWNER_SKY" not in prepare

    main = compact(function(source, "void main()"))
    sky_branch = main.index("if(decode==VSB_PRODUCT_DECODE_SKY){")
    hit_branch = main.index("elseif(decode==VSB_PRODUCT_DECODE_HIT){")
    sky_adapter = main.index("vsb_product_sky_record(", sky_branch)
    compiler_call = main.index("compile_probe_subcell_payload(", hit_branch)
    assert sky_adapter < hit_branch, "sky adapter escaped its no-hit branch"
    assert compiler_call > hit_branch, "hit compiler call left the hit branch"
    assert main.count("vsb_product_sky_record(") == 1
    assert main.count("compile_probe_subcell_payload(") == 1
    assert main.count("if(decode==VSB_PRODUCT_DECODE_HIT){") == 1


def compile_product(source: str) -> tuple[bool, str]:
    compiler = shutil.which("glslangValidator")
    if compiler is None:
        return False, "glslangValidator is missing"
    lines = source.splitlines(keepends=True)
    if lines and lines[0].startswith("#["):
        source = "".join(lines[1:])
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".comp", encoding="utf-8", delete=False
    ) as handle:
        handle.write(source)
        path = Path(handle.name)
    try:
        result = subprocess.run(
            [compiler, "--target-env", "vulkan1.1", "-S", "comp", str(path)],
            capture_output=True,
            text=True,
            timeout=900,
        )
    finally:
        path.unlink(missing_ok=True)
    return result.returncode == 0, "\n".join((result.stdout, result.stderr))


def mutation_controls(source: str) -> None:
    compiler_owner = source.replace(
        "ProbePayloadCompileResult result;",
        "ProbePayloadCompileResult result;\n\tif (context.surface_kind == OWNER_SKY) return result;",
        1,
    )
    assert compiler_owner != source
    try:
        assert_contract(compiler_owner)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError("compiler sky-owner mutation was not caught")

    record = source.replace(
        "result.record = q3_probe_payload_visible_record(",
        "result.record = q3_probe_payload_empty_record(",
        1,
    )
    assert record != source
    try:
        assert_contract(record)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError("compiler visible-record mutation was not caught")

    sky_route = source.replace(
        "vsb_product_sky_record(input_value.ray, subcell);",
        "vsb_product_failure_final_record();",
        1,
    )
    assert sky_route != source
    try:
        assert_contract(sky_route)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError("canonical W-SKY route mutation was not caught")

    resurrected = source + "\nuint q3_probe_payload_compile_sky_record() { return 0u; }\n"
    try:
        assert_contract(resurrected)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError("resurrected dead compiler sky branch was not caught")


def main() -> int:
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    assert_contract(source)
    compiled, diagnostic = compile_product(source)
    assert compiled, diagnostic
    mutation_controls(source)
    print(
        "FL4431_Q3_SLICE2_CONTRACT_PASS "
        "admitted_hit_compiler=true canonical_w_sky_outside_compiler=true "
        "dead_sky_branch=false glslang_vulkan=true mutations=4 runtime_claim=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
