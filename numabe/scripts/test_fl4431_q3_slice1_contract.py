#!/usr/bin/env python3
"""FL-4431 Q3 Slice 1: retain the canonical Product sky constructor.

The former Slice-1 inventory described a dormant resolve forest. The retained
scene cutover deleted that forest's sky glyph/colour compiler helpers. The
live rule is narrower: ``q3_probe_payload_sky_record`` remains a pure record
constructor, ``vsb_product_sky_record`` owns the ProductSkyPaint-to-record
adapter, and the VSB_PRODUCT_DECODE_SKY branch calls that adapter directly.

This contract is static compiler evidence. It does not claim runtime sky
pixels, Metal behavior, FPS, or visual acceptance.
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
PRODUCT_PATH = REPO / "godot_project/render/product/cell_owner_product.glsl"
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

    constructor = compact(
        function(source, "ProbePayloadRecord q3_probe_payload_sky_record(")
    )
    for token in (
        "record.r0=uvec4(FINAL_PRODUCT_STATUS_ORDINARY,OWNER_SKY,glyph,0u);",
        "record.r1=provenance;",
        "record.fg=vec4(fg,1.0);",
        "record.bg=vec4(bg,1.0);",
        "record.anchor=vec4(geometry_anchor,1.0);",
        "returnrecord;",
    ):
        assert token in constructor, f"sky constructor law missing: {token}"

    adapter = compact(function(source, "ProbeSubcellProductRecord vsb_product_sky_record("))
    for token in (
        "ProductSkyPaintpaint=product_sky_paint(ray);",
        "q3_probe_payload_card_highlight_enabled(",
        "q3_probe_payload_sky_record(",
        "returnvsb_product_record_from_payload(payload);",
    ):
        assert token in adapter, f"W-SKY adapter law missing: {token}"
    assert adapter.count("q3_probe_payload_sky_record(") == 1

    main = compact(function(source, "void main() {"))
    assert "if(decode==VSB_PRODUCT_DECODE_SKY){" in main
    assert "final_record=vsb_product_sky_record(" in main
    assert "final_record=vsb_product_finalize_record(" in main
    assert main.count("vsb_product_sky_record(") == 1


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
    constructor = source.replace(
        "FINAL_PRODUCT_STATUS_ORDINARY, OWNER_SKY, glyph, 0u",
        "FINAL_PRODUCT_STATUS_ORDINARY, OWNER_NONE, glyph, 0u",
        1,
    )
    assert constructor != source
    try:
        assert_contract(constructor)
    except AssertionError:
        pass
    else:
        raise AssertionError("sky constructor owner mutation was not caught")

    adapter = source.replace(
        "ProbePayloadRecord payload = q3_probe_payload_sky_record(",
        "ProbePayloadRecord payload = q3_probe_payload_visible_record(",
        1,
    )
    assert adapter != source
    try:
        assert_contract(adapter)
    except AssertionError:
        pass
    else:
        raise AssertionError("W-SKY adapter constructor mutation was not caught")

    main = source.replace(
        "vsb_product_sky_record(input_value.ray, subcell);",
        "vsb_product_failure_final_record();",
        1,
    )
    assert main != source
    try:
        assert_contract(main)
    except AssertionError:
        pass
    else:
        raise AssertionError("canonical W-SKY call removal was not caught")

    resurrected = source + "\nuint q3_probe_payload_sky_glyph() { return 0u; }\n"
    try:
        assert_contract(resurrected)
    except AssertionError:
        pass
    else:
        raise AssertionError("resurrected dead sky helper was not caught")


def main() -> int:
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    assert_contract(source)
    compiled, diagnostic = compile_product(source)
    assert compiled, diagnostic
    mutation_controls(source)
    print(
        "FL4431_Q3_SLICE1_CONTRACT_PASS "
        "canonical_w_sky=true dead_sky_compiler_owners=false "
        "glslang_vulkan=true mutations=4 runtime_claim=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
