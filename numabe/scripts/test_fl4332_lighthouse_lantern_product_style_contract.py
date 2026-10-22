#!/usr/bin/env python3
"""FL-4332 retained lighthouse-lantern Product styling contract.

Material 204 is an already-retained static identity. At night, and only while
the host has admitted the existing kind-4 lighthouse record, Product adds that
record's color to the material foreground and background without changing its
glyph. The directional beam remains the next independent Product effect.

This contract is intentionally destructive: every ownership, admission,
ordering, glyph, and coefficient mutant below must be rejected. It supplies
source and arithmetic evidence only. Runtime visual review remains pending.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import sys
from math import isclose
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_fl4431_vsb_activation_contract import glsl_reachable_functions


ROOT = SCRIPTS_DIR.parent
PRODUCT_PATH = ROOT / "godot_project/render/product/cell_owner_product.glsl"
WORLD_PATH = ROOT / "godot_project/scripts/World.gd"
HOST_PATH = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
COMPOSITOR_PATH = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"

STYLE_HELPER = "vsb_product_apply_lighthouse_lantern_style"
COLLECTOR_HELPER = "vsb_product_collect_local_light_slot_facts"
LANTERN_DESTRUCTIVE_MUTANT_COUNT = 19


def _without_glsl_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _without_gd_comments(source: str) -> str:
    return re.sub(r"(?m)#.*$", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def _glsl_function_text(source: str, name: str) -> str:
    """Return one GLSL function definition using balanced braces."""
    clean = _without_glsl_comments(source)
    match = re.search(
        rf"(?m)^\s*(?:precise\s+)?[A-Za-z_]\w*\s+"
        rf"{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        clean,
        re.S,
    )
    if match is None:
        return ""
    brace = clean.find("{", match.start())
    depth = 0
    for index in range(brace, len(clean)):
        if clean[index] == "{":
            depth += 1
        elif clean[index] == "}":
            depth -= 1
            if depth == 0:
                return clean[match.start() : index + 1]
    return ""


def _gd_function_text(source: str, name: str) -> str:
    """Return one top-level GDScript function."""
    clean = _without_gd_comments(source)
    match = re.search(rf"(?m)^func\s+{re.escape(name)}\s*\(", clean)
    if match is None:
        return ""
    next_function = re.search(r"(?m)^func\s+", clean[match.end() :])
    if next_function is None:
        return clean[match.start() :]
    return clean[match.start() : match.end() + next_function.start()]


def _definition_count(source: str, name: str) -> int:
    clean = _without_glsl_comments(source)
    return len(
        re.findall(
            rf"(?m)^\s*(?:precise\s+)?[A-Za-z_]\w*\s+"
            rf"{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
            clean,
            re.S,
        )
    )


def _replace_once(source: str, old: str, new: str) -> str:
    assert old in source, f"mutation source token missing: {old!r}"
    return source.replace(old, new, 1)


def _ordered(body: str, tokens: tuple[str, ...]) -> bool:
    cursor = -1
    for token in tokens:
        cursor = body.find(token, cursor + 1)
        if cursor < 0:
            return False
    return True


def _lantern_contract_errors(
    product: str,
    world: str,
    host: str,
    compositor: str,
    *,
    check_reachability: bool = True,
) -> tuple[str, ...]:
    """Return every deviation from the selected material-204 Product law."""
    errors: list[str] = []
    clean_product = _without_glsl_comments(product)
    style = _compact(_glsl_function_text(product, STYLE_HELPER))
    collector = _compact(_glsl_function_text(product, COLLECTOR_HELPER))
    finalizer = _compact(
        _glsl_function_text(product, "vsb_product_finalize_record")
    )

    if _definition_count(product, STYLE_HELPER) != 1:
        errors.append("lantern styling must have exactly one Product owner")
    if _definition_count(product, COLLECTOR_HELPER) != 1:
        errors.append("kind-4 admission must have exactly one typed collector")

    index_law = (
        "intraw_count=local_light_record_count();",
        "intscan_count=clamp(raw_count,0,LOCAL_LIGHT_MAX_RECORDS);",
        "for(intlight_index=0;"
        "light_index<LOCAL_LIGHT_MAX_RECORDS;"
        "light_index+=1){",
        "if(light_index>=scan_count)break;",
        "intkind=int(slot_2.x);",
        "if(kind==4){",
        "if(!facts.lantern_present){",
        "facts.lantern_present=true;",
        "facts.lantern_color="
        "clamp(slot_1.rgb,vec3(0.0),vec3(1.0));",
    )
    if not collector or not _ordered(collector, index_law):
        errors.append("kind-4 record admission drifted")

    admission_law = (
        "!input_value.retained_face.valid",
        "input_value.context.surface_kind!=OWNER_STATIC_MESH",
        "record.r0.y!=OWNER_STATIC_MESH",
        "input_value.identity.material!=204u",
        "input_value.retained_face.material_id"
        "!=input_value.identity.material",
        "!q3_probe_payload_night_enabled(input_value.context.flags)",
        "if(!local_light_facts.lantern_present)return;",
    )
    if not style or not _ordered(style, admission_law):
        errors.append("lantern retained-static night admission drifted")

    if (
        "vec3lighthouse_color=local_light_facts.lantern_color;"
    ) not in style:
        errors.append("lantern color no longer consumes the kind-4 record")
    if (
        "record.fg.rgb=clamp("
        "record.fg.rgb+lighthouse_color*0.60,"
        "vec3(0.0),vec3(1.0));"
    ) not in style:
        errors.append("lantern foreground coefficient drifted")
    if (
        "record.bg.rgb=clamp("
        "record.bg.rgb+lighthouse_color*0.60*0.28,"
        "vec3(0.0),vec3(1.0));"
    ) not in style:
        errors.append("lantern background coefficient drifted")

    for constructor in re.findall(r"vec3\s*\(([^()]*)\)", style):
        if "," in constructor:
            errors.append("lantern Product stage owns a forbidden RGB literal")
            break

    if "record.r0.z" in style:
        errors.append("lantern Product stage changed the retained material glyph")
    forbidden_beam_ownership = (
        "OWNER_BEAM",
        "PRODUCT_CONTRIBUTED_LIGHTHOUSE_BEAM_BIT",
        "lighthouse_beam_screen_stroke_glyph",
        "compute_lighthouse_beam_cell_product",
        "SEC_BLEND_",
        "record.r0.y=",
    )
    if any(token in style for token in forbidden_beam_ownership):
        errors.append("lantern Product stage acquired beam ownership")

    night_dim = finalizer.find(
        "product_apply_scene_land_daynight_pair("
        "night,record.fg.rgb,record.bg.rgb);"
    )
    style_call = finalizer.find(
        "vsb_product_apply_lighthouse_lantern_style("
        "record,input_value,local_light_facts);"
    )
    beam_effect = finalizer.find(
        "EffectCellProductbeam=local_light_facts.beam;"
    )
    if not 0 <= night_dim < style_call < beam_effect:
        errors.append("lantern stage order drifted")
    immediate_edge = (
        "vsb_product_apply_lighthouse_lantern_style("
        "record,input_value,local_light_facts);"
        "EffectCellProductbeam=local_light_facts.beam;"
    )
    if immediate_edge not in finalizer:
        errors.append("lantern stage is not immediately before the beam effect")
    if finalizer.count(
        "vsb_product_apply_lighthouse_lantern_style("
        "record,input_value,local_light_facts);"
    ) != 1:
        errors.append("lantern finalizer call count drifted")

    if check_reachability:
        reached = glsl_reachable_functions(product)
        if STYLE_HELPER not in reached or COLLECTOR_HELPER not in reached:
            errors.append("lantern stage is unreachable from Product main")

    compact_world = _compact(_without_gd_comments(world))
    if "varlh_frame:=Color(0.20,0.20,0.24)" not in compact_world:
        errors.append("generic material-204 daytime base was deleted")
    if "[203,lh_masonry],[204,lh_frame]" not in compact_world:
        errors.append("generic material-204 matlib column was deleted")
    if (
        "varmat_glyph:int=glyph" not in compact_world
        or "varmat_f:float=1.0ifis_specialelsef" not in compact_world
    ):
        errors.append("generic material-204 density styling drifted")

    clean_host = _without_gd_comments(host)
    compact_host = _compact(clean_host)
    build_records = _compact(
        _gd_function_text(host, "_build_local_light_records")
    )
    add_beam = _compact(
        _gd_function_text(host, "_add_lighthouse_beam_records")
    )
    if "constLOCAL_LIGHT_KIND_LIGHTHOUSE_BEAM:=4" not in compact_host:
        errors.append("host kind-4 identity drifted")
    if (
        "ifbaseline_feature_lighthouse_lightandcelestial.night_enabled():"
        "_add_lighthouse_beam_records(candidates,time_seconds)"
    ) not in build_records:
        errors.append("host lighthouse beam night gate drifted")
    if "204" in build_records:
        errors.append("host beam admission depends on material 204")
    if "cone.color=Vector3(1.0,0.93,0.70)" not in add_beam:
        errors.append("host kind-4 color authority drifted")
    if "cone.kind=LOCAL_LIGHT_KIND_LIGHTHOUSE_BEAM" not in add_beam:
        errors.append("host kind-4 source identity drifted")

    for path_name, source in (
        ("World", world),
        ("host", host),
        ("compositor", compositor),
    ):
        if STYLE_HELPER in source:
            errors.append(f"{path_name} acquired duplicate lantern styling")

    if "vec3(1.0,0.93,0.70)" in _compact(clean_product):
        errors.append("Product acquired the host-owned lighthouse RGB literal")
    return tuple(errors)


def _selected_sources() -> tuple[str, str, str, str]:
    return (
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8")),
        WORLD_PATH.read_text(encoding="utf-8"),
        HOST_PATH.read_text(encoding="utf-8"),
        COMPOSITOR_PATH.read_text(encoding="utf-8"),
    )


def test_lantern_additive_arithmetic() -> None:
    """Pin additive coefficients plus saturation independently from source text."""
    foreground = (0.10, 0.20, 0.30)
    background = (0.04, 0.08, 0.12)
    color = (1.00, 0.93, 0.70)
    styled_foreground = tuple(
        min(1.0, base + light * 0.60)
        for base, light in zip(foreground, color)
    )
    styled_background = tuple(
        min(1.0, base + light * 0.60 * 0.28)
        for base, light in zip(background, color)
    )
    assert all(
        isclose(actual, expected)
        for actual, expected in zip(
            styled_foreground, (0.7, 0.758, 0.72)
        )
    )
    assert all(
        isclose(actual, expected)
        for actual, expected in zip(
            styled_background, (0.208, 0.23624, 0.2376)
        )
    )
    assert tuple(
        min(1.0, 0.90 + light * 0.60) for light in color
    ) == (1.0, 1.0, 1.0)


def test_shipping_source_matches_selected_lantern_product_law() -> None:
    """Require the selected source law in the active shared tree."""
    errors = _lantern_contract_errors(*_selected_sources())
    assert not errors, "RED: " + "; ".join(errors)


def test_lantern_contract_rejects_each_destructive_mutant() -> None:
    """Exercise every named failure against the selected shipping sources."""
    product, world, host, compositor = _selected_sources()
    assert not _lantern_contract_errors(
        product,
        world,
        host,
        compositor,
        check_reachability=False,
    )

    style_call = (
        "\tvsb_product_apply_lighthouse_lantern_style(\n"
        "\t\trecord, input_value, local_light_facts);\n"
    )
    product_without_call = _replace_once(product, style_call, "")
    before_night = _replace_once(
        product_without_call,
        "\tfloat night = product_day_night_factor();\n",
        style_call + "\tfloat night = product_day_night_factor();\n",
    )
    after_beam = _replace_once(
        product_without_call,
        "\tvsb_product_apply_effect(beam, record);\n",
        "\tvsb_product_apply_effect(beam, record);\n" + style_call,
    )
    duplicate_style = product + "\n" + _glsl_function_text(
        product, STYLE_HELPER
    )

    mutations = (
        (
            "material 203 admitted",
            _replace_once(
                product,
                "input_value.identity.material != 204u",
                "input_value.identity.material != 203u",
            ),
            world,
            host,
            compositor,
            "lantern retained-static night admission drifted",
        ),
        (
            "daylight admitted",
            _replace_once(
                product,
                "|| !q3_probe_payload_night_enabled(\n"
                "\t\t\t\tinput_value.context.flags)) return;",
                "|| !q3_probe_payload_clouds_enabled(\n"
                "\t\t\t\tinput_value.context.flags)) return;",
            ),
            world,
            host,
            compositor,
            "lantern retained-static night admission drifted",
        ),
        (
            "missing kind-4 record admitted",
            _replace_once(
                product,
                "if (!local_light_facts.lantern_present) return;",
                "",
            ),
            world,
            host,
            compositor,
            "lantern retained-static night admission drifted",
        ),
        (
            "kind-4 selector drifted to bonfire",
            _replace_once(
                product,
                "if (kind == 4) {",
                "if (kind == 1) {",
            ),
            world,
            host,
            compositor,
            "kind-4 record admission drifted",
        ),
        (
            "Product hardcoded the beam RGB",
            _replace_once(
                product,
                "local_light_facts.lantern_color",
                "vec3(1.0, 0.93, 0.70)",
            ),
            world,
            host,
            compositor,
            "lantern Product stage owns a forbidden RGB literal",
        ),
        (
            "lantern changed to a beam-stroke glyph",
            _replace_once(
                product,
                "\trecord.fg.rgb = clamp(\n",
                "\trecord.r0.z = 0x2Du;\n\trecord.fg.rgb = clamp(\n",
            ),
            world,
            host,
            compositor,
            "lantern Product stage changed the retained material glyph",
        ),
        (
            "lantern changed to the deleted full-block glyph",
            _replace_once(
                product,
                "\trecord.fg.rgb = clamp(\n",
                "\trecord.r0.z = 0xDBu;\n\trecord.fg.rgb = clamp(\n",
            ),
            world,
            host,
            compositor,
            "lantern Product stage changed the retained material glyph",
        ),
        (
            "foreground lift drifted",
            _replace_once(
                product,
                "record.fg.rgb + lighthouse_color * 0.60",
                "record.fg.rgb + lighthouse_color * 0.59",
            ),
            world,
            host,
            compositor,
            "lantern foreground coefficient drifted",
        ),
        (
            "background ratio drifted",
            _replace_once(
                product,
                "lighthouse_color * 0.60 * 0.28",
                "lighthouse_color * 0.60 * 0.27",
            ),
            world,
            host,
            compositor,
            "lantern background coefficient drifted",
        ),
        (
            "lantern stage moved before night dimming",
            before_night,
            world,
            host,
            compositor,
            "lantern stage order drifted",
        ),
        (
            "lantern stage moved after the beam",
            after_beam,
            world,
            host,
            compositor,
            "lantern stage order drifted",
        ),
        (
            "lantern stage acquired beam ownership",
            _replace_once(
                product,
                "\trecord.fg.rgb = clamp(\n",
                "\trecord.r0.y = OWNER_BEAM;\n\trecord.fg.rgb = clamp(\n",
            ),
            world,
            host,
            compositor,
            "lantern Product stage acquired beam ownership",
        ),
        (
            "second Product styling owner added",
            duplicate_style,
            world,
            host,
            compositor,
            "lantern styling must have exactly one Product owner",
        ),
        (
            "World acquired duplicate styling",
            product,
            world + f"\nfunc {STYLE_HELPER}() -> void:\n\tpass\n",
            host,
            compositor,
            "World acquired duplicate lantern styling",
        ),
        (
            "host acquired duplicate styling",
            product,
            world,
            host + f"\nfunc {STYLE_HELPER}() -> void:\n\tpass\n",
            compositor,
            "host acquired duplicate lantern styling",
        ),
        (
            "compositor acquired duplicate styling",
            product,
            world,
            host,
            compositor + f"\nvoid {STYLE_HELPER}() {{}}\n",
            "compositor acquired duplicate lantern styling",
        ),
        (
            "generic daytime material-204 column deleted",
            product,
            _replace_once(world, ", [204, lh_frame]", ""),
            host,
            compositor,
            "generic material-204 matlib column was deleted",
        ),
        (
            "beam gate made dependent on material-204 visibility",
            product,
            world,
            _replace_once(
                host,
                "if baseline_feature_lighthouse_light and "
                "celestial.night_enabled():",
                "if baseline_feature_lighthouse_light and "
                "celestial.night_enabled() and "
                "lighthouse_material_204_visible:",
            ),
            compositor,
            "host lighthouse beam night gate drifted",
        ),
        (
            "host kind-4 color authority drifted",
            product,
            world,
            _replace_once(
                host,
                "cone.color = Vector3(1.0, 0.93, 0.70)",
                "cone.color = Vector3(1.0, 0.93, 0.68)",
            ),
            compositor,
            "host kind-4 color authority drifted",
        ),
    )
    assert len(mutations) == LANTERN_DESTRUCTIVE_MUTANT_COUNT

    for (
        label,
        mutant_product,
        mutant_world,
        mutant_host,
        mutant_compositor,
        expected,
    ) in mutations:
        errors = _lantern_contract_errors(
            mutant_product,
            mutant_world,
            mutant_host,
            mutant_compositor,
            check_reachability=False,
        )
        assert expected in errors, f"{label} escaped: {errors}"


def main() -> int:
    test_lantern_additive_arithmetic()
    test_shipping_source_matches_selected_lantern_product_law()
    test_lantern_contract_rejects_each_destructive_mutant()
    print(
        "FL4332_LIGHTHOUSE_LANTERN_PRODUCT_STYLE_CONTRACT_OK "
        f"mutants={LANTERN_DESTRUCTIVE_MUTANT_COUNT}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
