#!/usr/bin/env python3
"""Guard the single typed sky author retained by the FL-4431 Product cutover."""

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


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
SKY_BUILDER = "product_sky_paint"
SKY_AUTHOR = "vsb_product_sky_record"
FINALIZER = "vsb_product_finalize_record"
LAND_NIGHT_PAIR = "product_apply_scene_land_daynight_pair"


def function_body(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;]*?\)\s*\{{", source, re.S)
    if match is None:
        return ""
    start = match.end() - 1
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start + 1 : index]
    return ""


def normalized(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return re.sub(r"\s+", "", text)


def require_ordered(
    body: str,
    tokens: tuple[str, ...],
    error: str,
    errors: list[str],
) -> None:
    """Pin a source law to one function body and preserve evaluation order."""
    cursor = 0
    for token in tokens:
        found = body.find(token, cursor)
        if found < 0:
            errors.append(error)
            return
        cursor = found + len(token)


def contract_errors(source: str) -> tuple[str, ...]:
    errors: list[str] = []
    builder = normalized(function_body(source, SKY_BUILDER))
    author = normalized(function_body(source, SKY_AUTHOR))
    finalizer = normalized(function_body(source, FINALIZER))
    land_night_pair = normalized(function_body(source, LAND_NIGHT_PAIR))
    main_body = function_body(source, "main")
    all_clean = normalized(source)

    if not builder:
        errors.append("typed ProductSkyPaint builder is missing")
    single_fact_calls = (
        ("product_day_night_factor()", "day-night factor"),
        ("night_sky_cloud_occlusion(sky_ray)", "cloud field"),
        ("night_lightning_flash_strength()", "lightning field"),
        ("cell_owner_hash01(star_cell,4126u)", "admitted star hash"),
    )
    for call, label in single_fact_calls:
        if builder.count(call) != 1:
            errors.append(
                f"ProductSkyPaint must evaluate {label} exactly once"
            )
    if builder.count("night_moon_tangent(") != 1:
        errors.append("ProductSkyPaint must evaluate moon tangent exactly once")

    # This static contract guards the single-author topology and serialized
    # payload route. Sky colors, thresholds, gradients, and contrast belong to
    # rendered-frame tests tied to an exact scene, pose, time, and baseline.

    author_law = (
        "ProductSkyPaintpaint=product_sky_paint(ray);",
        "if(q3_probe_payload_card_highlight_enabled("
        "vsb_product_payload_flags())){",
        "paint.foreground*=0.22;",
        "paint.background*=0.22;",
        "ProbePayloadRecordpayload=q3_probe_payload_sky_record("
        "paint.glyph,paint.foreground,paint.background,",
    )
    require_ordered(
        author,
        author_law,
        "typed sky author lost builder, F10, or payload call order",
        errors,
    )
    sky_definition_count = len(
        re.findall(r"\bProductSkyPaint\s+product_sky_paint\s*\(", source)
    )
    sky_call_count = all_clean.count("product_sky_paint(")
    if (
        sky_definition_count != 1
        or sky_call_count != 5
        or all_clean.count("ProductSkyPaintsky=product_sky_paint(ray);") != 1
        or all_clean.count(
            "ProductSkyPainthorizon_sky=product_sky_paint(horizon_ray);"
        ) != 1
        or all_clean.count(
            "product_sky_paint(fl4740_sky_cell_ray).glyph;"
        ) != 1
    ):
        errors.append(
            "ProductSkyPaint must have one definition, one payload author, and three bounded background/glyph consumers"
        )
    if finalizer.count("product_sky_paint(") != 0:
        errors.append("finalizer restored a duplicate ProductSkyPaint repaint")
    if "cell_owner_hash01(hash_xy,4126u)" in builder:
        errors.append("star identity reverted to the screen-subcell hash")
    for retired_helper in (
        "sky_glyph_for_ray(",
        "sky_paint_fg_for_ray(",
        "sky_paint_bg_for_ray(",
        "night_moon_dir_coverage(",
        "night_moon_dir_halo(",
    ):
        if retired_helper in all_clean:
            errors.append(f"retired split sky helper survived: {retired_helper}")

    # Foreground ink and background paper are two outputs of one immutable
    # land-night fact. The old scalar helper recomputed the identical
    # night/brightness/tint closure for each output. Pin the pair author to one
    # definition and one finalizer call so that split owner cannot return.
    if (
        len(
            re.findall(
                r"\bvoid\s+product_apply_scene_land_daynight_pair\s*\(",
                source,
            )
        )
        != 1
        or finalizer.count("product_apply_scene_land_daynight_pair(") != 1
    ):
        errors.append(
            "land-night pair must have one definition plus one live author call"
        )
    if "product_scene_land_daynight(" in all_clean:
        errors.append(
            "retired split land-night helper survived: "
            "product_scene_land_daynight("
        )
    if not land_night_pair:
        errors.append("typed land-night pair helper is missing")
    elif "product_day_night_factor()" in land_night_pair:
        errors.append(
            "land-night pair re-read night instead of consuming caller fact"
        )
    finalizer_law = (
        "floatnight=product_day_night_factor();",
        "if(night>0.0001&&!bonfire_emissive){",
        "if(record.r0.y==OWNER_WATER",
        "}elseif(record.r0.y!=OWNER_SKY",
        "product_apply_scene_land_daynight_pair("
        "night,record.fg.rgb,record.bg.rgb);",
        "VsbProductLocalLightSlotFactslocal_light_facts="
        "vsb_product_collect_local_light_slot_facts(",
    )
    require_ordered(
        finalizer,
        finalizer_law,
        "water, land-night, or effect ordering drifted",
        errors,
    )
    if finalizer.count(
        "product_apply_scene_land_daynight_pair("
        "night,record.fg.rgb,record.bg.rgb);"
    ) != 1:
        errors.append("land-night pair finalizer call count drifted")
    if "if(record.r0.y==OWNER_SKY)returnrecord;" in finalizer:
        errors.append("sky early-return hides later Product effects")

    if all_clean.count("vsb_product_sky_record(input_value.ray,subcell)") != 1:
        errors.append("only clean no-hit may use the typed sky author")
    if "distant_water_sky_paint" in main_body:
        errors.append("far-water typed-sky owner survived")
    return tuple(errors)


def replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError(f"mutation anchor count drifted: {old!r}")
    return source.replace(old, new, 1)


def replace_once_after(source: str, anchor: str, old: str, new: str) -> str:
    anchor_index = source.index(anchor)
    suffix = source[anchor_index:]
    if suffix.count(old) != 1:
        raise ValueError(f"scoped mutation anchor count drifted: {old!r}")
    return source[:anchor_index] + suffix.replace(old, new, 1)


def move_land_pair_after_b12(source: str) -> str:
    """Move the land pair after light collection while keeping GLSL valid."""
    pair_call = (
        "\t\t\tproduct_apply_scene_land_daynight_pair(\n"
        "\t\t\t\tnight, record.fg.rgb, record.bg.rgb);\n"
    )
    without_pair = replace_once(source, pair_call, "")
    collector_tail = "\t\t\trecord.r0.y != OWNER_SKY);\n"
    moved_call = (
        collector_tail
        + "\tif (night > 0.0001\n"
        + "\t\t\t&& record.r0.y != OWNER_WATER\n"
        + "\t\t\t&& record.r0.y != OWNER_SKY) {\n"
        + "\t\tproduct_apply_scene_land_daynight_pair(\n"
        + "\t\t\tnight, record.fg.rgb, record.bg.rgb);\n"
        + "\t}\n"
    )
    return replace_once(without_pair, collector_tail, moved_call)


def damaging_mutants(source: str) -> list[tuple[str, str, str]]:
    mutants = [
        (
            "sky_repaint_restored",
            replace_once(
                source,
                "\t\tif (record.r0.y == OWNER_WATER",
                "\t\tif (record.r0.y == OWNER_SKY) {\n"
                "\t\t\tProductSkyPaint duplicate_sky =\n"
                "\t\t\t\tproduct_sky_paint(input_value.ray);\n"
                "\t\t\trecord.r0.z = duplicate_sky.glyph;\n"
                "\t\t} else if (record.r0.y == OWNER_WATER",
            ),
            "ProductSkyPaint must have one definition, one payload author, and three bounded background/glyph consumers",
        ),
        (
            "sky_early_return_inserted",
            replace_once_after(
                source,
                "ProbeSubcellProductRecord vsb_product_finalize_record(",
                "\tivec2 cell_px = max(params.cell_dims.xy, ivec2(1));",
                "\tif (record.r0.y == OWNER_SKY) return record;\n"
                "\tivec2 cell_px = max(params.cell_dims.xy, ivec2(1));",
            ),
            "sky early-return hides later Product effects",
        ),
        (
            "f10_gate_removed",
            replace_once(
                source,
                "if (q3_probe_payload_card_highlight_enabled(\n"
                "\t\t\tvsb_product_payload_flags())) {",
                "if (false) {",
            ),
            "typed sky author lost builder, F10, or payload call order",
        ),
        (
            "sky_receives_land_night_tint",
            replace_once(
                source,
                "} else if (record.r0.y != OWNER_SKY\n"
                "\t\t\t\t&& !input_value.context.payload_from_backstop) {",
                "} else {",
            ),
            "water, land-night, or effect ordering drifted",
        ),
        (
            "land_pair_duplicate_night_read",
            replace_once(
                source,
                "\tif (nf <= 0.0001) return;"
                "                   // full day: byte-identical no-op",
                "\tnf = product_day_night_factor();\n"
                "\tif (nf <= 0.0001) return;"
                "                   // full day: byte-identical no-op",
            ),
            "land-night pair re-read night instead of consuming caller fact",
        ),
        (
            "land_pair_moved_after_b12",
            move_land_pair_after_b12(source),
            "water, land-night, or effect ordering drifted",
        ),
        (
            "split_land_helper_resurrected",
            replace_once(
                replace_once(
                    source,
                    "void product_apply_scene_land_daynight_pair(\n",
                    "vec3 product_scene_land_daynight(vec3 color) {\n"
                    "\treturn color;\n"
                    "}\n\n"
                    "void product_apply_scene_land_daynight_pair(\n",
                ),
                "\t\t\tproduct_apply_scene_land_daynight_pair(\n"
                "\t\t\t\tnight, record.fg.rgb, record.bg.rgb);",
                "\t\t\trecord.fg.rgb = "
                "product_scene_land_daynight(record.fg.rgb);\n"
                "\t\t\trecord.bg.rgb = "
                "product_scene_land_daynight(record.bg.rgb);",
            ),
            "retired split land-night helper survived: "
            "product_scene_land_daynight(",
        ),
        (
            "second_cloud_computation",
            replace_once(
                source,
                "\tfloat cloud = night_sky_cloud_occlusion(sky_ray);",
                "\tfloat cloud = night_sky_cloud_occlusion(sky_ray);\n"
                "\tcloud = max(\n"
                "\t\tcloud, night_sky_cloud_occlusion(sky_ray));",
            ),
            "ProductSkyPaint must evaluate cloud field exactly once",
        ),
        (
            "second_tangent_computation",
            replace_once(
                source,
                "\tbool moon_tangent_valid =\n"
                "\t\tnight_moon_tangent(sky_ray.dir, moon_tangent);",
                "\tbool moon_tangent_valid =\n"
                "\t\tnight_moon_tangent(sky_ray.dir, moon_tangent);\n"
                "\tvec2 duplicate_moon_tangent;\n"
                "\tmoon_tangent_valid = moon_tangent_valid\n"
                "\t\t|| night_moon_tangent(\n"
                "\t\t\tsky_ray.dir, duplicate_moon_tangent);",
            ),
            "ProductSkyPaint must evaluate moon tangent exactly once",
        ),
        (
            "second_lightning_computation",
            replace_once(
                source,
                "\tfloat flash = night_lightning_flash_strength();",
                "\tfloat flash = night_lightning_flash_strength();\n"
                "\tflash = max(flash, night_lightning_flash_strength());",
            ),
            "ProductSkyPaint must evaluate lightning field exactly once",
        ),
        (
            "second_star_computation",
            replace_once(
                source,
                "\t\tstar_hash = cell_owner_hash01(star_cell, 4126u);",
                "\t\tstar_hash = cell_owner_hash01(star_cell, 4126u);\n"
                "\t\tstar_hash = max(\n"
                "\t\t\tstar_hash, cell_owner_hash01(star_cell, 4126u));",
            ),
            "ProductSkyPaint must evaluate admitted star hash exactly once",
        ),
        (
            "retired_moon_coverage_owner_resurrected",
            source
            + "\nfloat night_moon_dir_coverage(vec3 ray_dir) {\n"
            + "\tvec2 point;\n"
            + "\tif (!night_moon_tangent(ray_dir, point)) return 0.0;\n"
            + "\tfloat radius = night_moon_angular_radius();\n"
            + "\treturn clamp(1.0 - smoothstep(\n"
            + "\t\tradius * 0.82, radius * 1.02, length(point)),\n"
            + "\t\t0.0, 1.0);\n"
            + "}\n",
            "retired split sky helper survived: night_moon_dir_coverage(",
        ),
        (
            "retired_moon_halo_owner_resurrected",
            source
            + "\nfloat night_moon_dir_halo(vec3 ray_dir) {\n"
            + "\tvec2 point;\n"
            + "\tif (!night_moon_tangent(ray_dir, point)) return 0.0;\n"
            + "\tfloat radius = night_moon_angular_radius();\n"
            + "\treturn 1.0 - smoothstep(\n"
            + "\t\tradius * 1.1, radius * 3.4, length(point));\n"
            + "}\n",
            "retired split sky helper survived: night_moon_dir_halo(",
        ),
    ]
    return mutants


def compile_glsl(source: str) -> tuple[bool, str]:
    compiler = shutil.which("glslangValidator")
    if compiler is None:
        raise AssertionError("glslangValidator is required")
    lines = source.splitlines(keepends=True)
    if lines and lines[0].startswith("#["):
        source = "".join(lines[1:])
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".glsl", encoding="utf-8", delete=False
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
    diagnostic = "\n".join(
        text for text in (result.stdout, result.stderr) if text
    )
    return result.returncode == 0, diagnostic


def main() -> int:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    errors = list(contract_errors(source))
    mutants = damaging_mutants(source)
    escaped: list[str] = []
    wrong_reason: list[str] = []
    for name, mutant, expected_error in mutants:
        compiled, diagnostic = compile_glsl(mutant)
        if not compiled:
            errors.append(f"mutant {name} was not compile-valid: {diagnostic}")
            continue
        mutant_errors = contract_errors(mutant)
        if not mutant_errors:
            escaped.append(name)
        elif expected_error not in mutant_errors:
            wrong_reason.append(
                f"{name}: expected {expected_error!r}, got {mutant_errors!r}"
            )
    if escaped:
        errors.append(f"damaging mutants escaped: {escaped}")
    if wrong_reason:
        errors.append(f"damaging mutants failed for the wrong reason: {wrong_reason}")
    if errors:
        for error in errors:
            print(f"FL4431_PRODUCT_SKY_SINGLE_AUTHOR_FAIL {error}")
        return 1
    print(
        "FL4431_PRODUCT_SKY_SINGLE_AUTHOR_PASS "
        "sky_paint_definitions=1 sky_payload_authors=1 "
        "sky_bounded_consumers=3 far_water_water_owned=true "
        f"mutants={len(mutants)} effects_after_sky=true"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
