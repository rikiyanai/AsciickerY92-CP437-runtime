#!/usr/bin/env python3
"""FL-4431 stage-61 shadow density-only source contract.

Stage 61 consumes only the maximum actor/dynamic shadow density and the binary
retained-static hit density. Caster ids, caster positions, selected dynamic
identity, and the external static face payload do not reach attenuation.

This red-first contract requires those three adapters to return scalar density
while preserving the existing strict maximum, static admission, terminal
shortcut, blob/static composition, and operation order. Mutation witnesses
restore each discarded provenance path and drift both selection laws.

The contract is structural evidence. It does not measure GPU time, establish
headed visual behavior, record operator acceptance, or close FL-4431.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"

ACTOR_DENSITY = "actor_row_shadow_density_at_world"
STATIC_DENSITY = "q3_probe_payload_static_shadow_density"
DYNAMIC_DENSITY = "dynamic_mover_shadow_density_at_world"
CAST_ATTENUATION = "q3_probe_payload_cast_shadow_attenuation"
DYNAMIC_CONSIDER = "vsb_retained_consider_dynamic_mover"
STATIC_SELECTOR = "vsb_retained_select_q3_static_shadow_caster"
POINT_SELECTOR = "vsb_retained_select_point_light_caster"
STATIC_TRAVERSAL = "vsb_retained_select_static_shadow_caster"
STATIC_BLAS = "vsb_retained_static_blas_select"
STATIC_CONSIDER = "vsb_retained_consider_static_caster"


def without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def compact(source: str) -> str:
    return re.sub(r"\s+", "", without_comments(source))


def function_text(source: str, name: str) -> str:
    """Return one complete GLSL function definition by brace depth."""
    clean = without_comments(source)
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


def struct_text(source: str, name: str) -> str:
    match = re.search(
        rf"\bstruct\s+{re.escape(name)}\s*\{{(?P<body>.*?)\}}\s*;",
        without_comments(source),
        re.S,
    )
    return "" if match is None else match.group("body")


def block_text(compact_source: str, marker: str) -> str:
    """Return a braced statement body beginning at *marker*."""
    start = compact_source.find(marker)
    if start < 0:
        return ""
    brace = compact_source.find("{", start + len(marker))
    if brace < 0:
        return ""
    depth = 0
    for index in range(brace, len(compact_source)):
        if compact_source[index] == "{":
            depth += 1
        elif compact_source[index] == "}":
            depth -= 1
            if depth == 0:
                return compact_source[brace + 1 : index]
    return ""


def ordered(body: str, tokens: tuple[str, ...]) -> bool:
    cursor = -1
    for token in tokens:
        cursor = body.find(token, cursor + 1)
        if cursor < 0:
            return False
    return True


def density_only_contract_errors(source: str) -> list[str]:
    """Return function-scoped deviations from the selected deletion."""
    errors: list[str] = []
    clean = without_comments(source)
    whole = compact(clean)

    if re.search(r"\bstruct\s+ShadowFact\b", clean):
        errors.append("ShadowFact provenance type remains")
    if "STATIC_MESH_SHADOW_SOURCE_BASE" in clean:
        errors.append("static shadow provenance source base remains")
    if re.search(r"\bdynamic_mover_source_base\s*\(", clean):
        errors.append("dynamic shadow provenance source helper remains")

    dynamic_struct = compact(
        struct_text(clean, "VsbRetainedDynamicSelection")
    )
    if not dynamic_struct:
        errors.append("dynamic selection struct is missing")
    elif "caster_id" in dynamic_struct or "caster_xz" in dynamic_struct:
        errors.append("dynamic shadow provenance fields remain")

    actor = compact(function_text(clean, ACTOR_DENSITY))
    if not actor:
        errors.append("actor density-only function is missing")
    else:
        actor_order = (
            "floatshadow_density=0.0;",
            "vec3foot=actor_foot_world(row);",
            "if(receiver_world.y-foot.y>ACTOR_SHADOW_ELEVATION_THRESHOLD)continue;",
            "if(max(abs(receiver_world.x-foot.x),abs(receiver_world.z-foot.z))>=0.95)continue;",
            "vec2world_size=max(actor_fixed256_pair(row,13),vec2(0.001));",
            "floatdensity=",
            "if(density>shadow_density){",
            "shadow_density=density;",
            "returnshadow_density;",
        )
        if not ordered(actor, actor_order):
            errors.append("actor strict density maximum drifted")
        if (
            "actor_cell_word(15,row)" in actor
            or "source_key" in actor
            or "caster_id" in actor
            or "caster_xz" in actor
            or "ShadowFact" in actor
        ):
            errors.append("actor provenance remains")

    static = compact(function_text(clean, STATIC_DENSITY))
    if not static:
        errors.append("static density-only function is missing")
    else:
        static_shape = (
            "if(receiver_owner_kind==OWNER_SKY"
            "||receiver_owner_kind==OWNER_ACTOR_SPRITE)return0.0;",
            "VsbRetainedStaticCasterSelectionselected="
            "vsb_retained_select_q3_static_shadow_caster("
            "shadow_ray,receiver_face_idx);",
            "returnselected.valid?1.0:0.0;",
        )
        if not ordered(static, static_shape):
            errors.append("static density admission shape drifted")
        if (
            "caster_id" in static
            or "caster_xz" in static
            or "STATIC_MESH_SHADOW_SOURCE_BASE" in static
            or "selected.facts.row_0" in static
            or "selected.facts.row_1" in static
            or "selected.facts.row_2" in static
            or "selected.facts.row_3" in static
            or "ShadowFact" in static
        ):
            errors.append("static provenance remains")

    selector = compact(function_text(clean, STATIC_SELECTOR))
    exact_selector = (
        "returnvsb_retained_select_static_shadow_caster("
        "shadow_ray,receiver_face_idx,FL4270_STATIC_FACE_SCAN_MAX,true);"
    )
    if exact_selector not in selector:
        errors.append("static selector admission drifted")
    point_selector = compact(function_text(clean, POINT_SELECTOR))
    exact_point_selector = (
        "returnvsb_retained_select_static_shadow_caster("
        "shadow_ray,receiver_face_idx,FL4270_STATIC_FACE_SCAN_MAX,false);"
    )
    if exact_point_selector not in point_selector:
        errors.append("point-light full-selection mode drifted")
    if re.search(
        r"\bvsb_retained_select_sun_shadow_caster\s*\(",
        clean,
    ):
        errors.append("dead sun-shadow wrapper resurrected")

    static_consider = compact(function_text(clean, STATIC_CONSIDER))
    q3_return = static_consider.find("if(q3_any_hit)returntrue;")
    point_valid = static_consider.find("selected.valid=true;")
    point_legacy = static_consider.find(
        "selected.legacy_face_index=facts.legacy_face_index;"
    )
    point_depth = static_consider.find("selected.depth=hit_depth;")
    if not (
        q3_return >= 0
        and point_valid > q3_return
        and point_legacy > point_valid
        and point_depth > point_legacy
    ):
        errors.append("Q3 any-hit constructs point-light payload")

    static_blas = compact(function_text(clean, STATIC_BLAS))
    if static_blas.count("if(q3_any_hit&&hit)returntrue;") != 2:
        errors.append("BLAS any-hit propagation drifted")
    static_traversal = compact(
        function_text(clean, STATIC_TRAVERSAL)
    )
    if static_traversal.count("if(q3_any_hit&&hit)") != 2:
        errors.append("TLAS any-hit propagation drifted")

    dynamic_consider = compact(
        function_text(clean, DYNAMIC_CONSIDER)
    )
    shadow_branch = block_text(
        dynamic_consider,
        "if(selection_mode==0)",
    )
    if not shadow_branch:
        errors.append("dynamic shadow branch is missing")
    else:
        dynamic_order = (
            "floatdensity=",
            "if(!(density>selected.density))continue;",
            "selected.density=density;",
            "selected.valid=true;",
            "continue;",
        )
        if not ordered(shadow_branch, dynamic_order):
            errors.append("dynamic strict density maximum drifted")
        if (
            "floatBitsToUint(density)" in shadow_branch
            or "vsb_retained_dynamic_identity_precedes(" in shadow_branch
            or "selected.identity" in shadow_branch
            or "selected.facts" in shadow_branch
            or "selected.caster_id" in shadow_branch
            or "selected.caster_xz" in shadow_branch
            or "selected.depth" in shadow_branch
            or "dynamic_mover_source_base(" in shadow_branch
        ):
            errors.append("dynamic shadow provenance remains")

    dynamic = compact(function_text(clean, DYNAMIC_DENSITY))
    if not dynamic:
        errors.append("dynamic density-only function is missing")
    else:
        dynamic_shape = (
            "VsbRetainedDynamicSelectionselected="
            "vsb_retained_select_dynamic_shadow_caster(receiver_world);",
            "if(!selected.valid)return0.0;",
            "returnclamp(selected.density,0.0,1.0);",
        )
        if not ordered(dynamic, dynamic_shape):
            errors.append("dynamic density adapter drifted")
        if (
            "caster_id" in dynamic
            or "caster_xz" in dynamic
            or "ShadowFact" in dynamic
        ):
            errors.append("dynamic density adapter restores provenance")

    cast = compact(function_text(clean, CAST_ATTENUATION))
    if not cast:
        errors.append("shadow attenuation function is missing")
    else:
        cast_order = (
            "precisefloatstatic_density=0.0;",
            "if(fully_shadowed_ratio!=1.0){",
            f"static_density={STATIC_DENSITY}(",
            "if(static_density>0.0&&fully_shadowed_ratio<=0.38){",
            "returnfully_shadowed_ratio;",
            "precisefloatblob_density=max(",
            f"{ACTOR_DENSITY}(receiver_world),",
            f"{DYNAMIC_DENSITY}(receiver_world));",
            "precisefloatblob_attenuation=max("
            "mix(1.0,0.38,clamp(blob_density,0.0,1.0)),0.08);",
            "precisefloatstatic_attenuation="
            "q3_probe_payload_static_shadow_light_attenuation("
            "static_density,fully_shadowed_ratio);",
            "returnmin(blob_attenuation,static_attenuation);",
        )
        if not ordered(cast, cast_order):
            errors.append("shadow attenuation composition/order drifted")
        if (
            ".density" in cast
            or "actor_row_shadow_fact_at_world(" in cast
            or "q3_probe_payload_static_shadow_fact(" in cast
            or "dynamic_mover_shadow_fact_at_world(" in cast
        ):
            errors.append("shadow attenuation still consumes provenance")

    return errors


SELECTED_SOURCE = r"""
struct VsbRetainedStaticCasterSelection {
    bool valid;
    uint legacy_face_index;
    precise float depth;
};

struct VsbRetainedDynamicSelection {
    bool valid;
    VsbSemanticIdentity identity;
    VsbRetainedFaceFacts facts;
    precise float depth;
    precise float density;
};

float actor_row_shadow_density_at_world(vec3 receiver_world) {
    float shadow_density = 0.0;
    int actor_count = max(params.misc.x, 0);
    actor_count = min(actor_count, WORLD_SHADOW_MAX_CASTERS);
    for (int row = 0; row < WORLD_SHADOW_MAX_CASTERS; row++) {
        if (row >= actor_count) break;
        vec3 foot = actor_foot_world(row);
        if (receiver_world.y - foot.y > ACTOR_SHADOW_ELEVATION_THRESHOLD) continue;
        if (max(abs(receiver_world.x - foot.x),
                abs(receiver_world.z - foot.z)) >= 0.95) continue;
        vec2 world_size = max(actor_fixed256_pair(row, 13), vec2(0.001));
        float radius = clamp(
            max(world_size.x * 0.62, world_size.y * 0.20), 0.42, 0.95);
        float density = (1.0 - smoothstep(
            0.0, max(radius, 0.001),
            length(receiver_world.xz - foot.xz))) * 0.84;
        if (density > shadow_density) {
            shadow_density = density;
        }
    }
    return shadow_density;
}

void vsb_retained_consider_dynamic_mover(
    uint mover_index, int selection_mode, ViewRay ray, vec3 receiver_world,
    float world_depth, bool bonfire_smoke_only,
    inout VsbRetainedDynamicSelection selected
) {
    for (uint local_face = 0u; local_face < face_count; ++local_face) {
        VsbRetainedFaceFacts facts;
        vec4 retained_anchor;
        if (!vsb_retained_dynamic_face_by_index(
                legacy_face, facts, retained_anchor)) continue;
        if (selection_mode == 0) {
            if (facts.actor_effect_kind != 1u) continue;
            vec3 caster_center = 0.25 * (
                facts.row_0.xyz + facts.row_1.xyz
                + facts.row_2.xyz + facts.row_3.xyz);
            float altitude_intensity = 1.0;
            float radius = 1.0;
            float density = (1.0 - smoothstep(
                0.0, radius,
                length(receiver_world.xz - caster_center.xz)))
                * altitude_intensity;
            if (!(density > selected.density)) continue;
            selected.density = density;
            selected.valid = true;
            continue;
        } else if (selection_mode == 1) {
            selected.depth = depth;
        } else {
            selected.depth = depth;
        }
        selected.valid = true;
        selected.identity.object_id = mover.identity_range.x;
        selected.identity.primitive_id = local_face;
        selected.identity.material = facts.material_id;
        selected.identity.stable_world_anchor = retained_anchor;
        selected.facts = facts;
    }
}

bool vsb_retained_consider_static_caster(
    ViewRay shadow_ray,
    uint object_id,
    uint primitive_id,
    bool q3_any_hit,
    inout VsbRetainedStaticCasterSelection selected
) {
    VsbRetainedFaceFacts facts;
    precise vec4 retained_anchor;
    if (!vsb_retained_static_face_by_indices(
            object_id, primitive_id, facts, retained_anchor, false)) {
        return false;
    }
    if (facts.material_id == 250u) return false;
    precise float t_0 = product_ray_triangle_t(
        shadow_ray, facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz);
    precise float t_1 = product_ray_triangle_t(
        shadow_ray, facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz);
    if (t_0 < 0.0 && t_1 < 0.0) return false;
    precise float hit_depth =
        t_0 < 0.0 ? t_1 : (t_1 < 0.0 ? t_0 : min(t_0, t_1));
    if (q3_any_hit) return true;
    selected.valid = true;
    selected.legacy_face_index = facts.legacy_face_index;
    selected.depth = hit_depth;
    return true;
}

bool vsb_retained_static_blas_select(
    ViewRay shadow_ray,
    uint object_id,
    int receiver_face_idx,
    int scan_cap,
    bool q3_any_hit,
    inout VsbRetainedStaticCasterSelection selected
) {
    bool hit = vsb_retained_consider_static_caster(
        shadow_ray, object_id, 0u, q3_any_hit, selected);
    if (q3_any_hit && hit) return true;
    hit = vsb_retained_consider_static_caster(
        shadow_ray, object_id, 1u, q3_any_hit, selected);
    if (q3_any_hit && hit) return true;
    return false;
}

VsbRetainedStaticCasterSelection
vsb_retained_select_static_shadow_caster(
    ViewRay shadow_ray,
    int receiver_face_idx,
    int scan_cap,
    bool q3_any_hit
) {
    VsbRetainedStaticCasterSelection selected;
    selected.valid = false;
    bool hit = vsb_retained_static_blas_select(
        shadow_ray, 0u, receiver_face_idx, scan_cap, q3_any_hit, selected);
    if (q3_any_hit && hit) selected.valid = true;
    hit = vsb_retained_static_blas_select(
        shadow_ray, 1u, receiver_face_idx, scan_cap, q3_any_hit, selected);
    if (q3_any_hit && hit) {
        selected.valid = true;
        return selected;
    }
    return selected;
}

VsbRetainedStaticCasterSelection
vsb_retained_select_point_light_caster(
    ViewRay shadow_ray, int receiver_face_idx
) {
    return vsb_retained_select_static_shadow_caster(
        shadow_ray, receiver_face_idx, FL4270_STATIC_FACE_SCAN_MAX, false);
}

VsbRetainedStaticCasterSelection
vsb_retained_select_q3_static_shadow_caster(
    ViewRay shadow_ray, int receiver_face_idx
) {
    return vsb_retained_select_static_shadow_caster(
        shadow_ray, receiver_face_idx, FL4270_STATIC_FACE_SCAN_MAX, true);
}

float q3_probe_payload_static_shadow_density(
    vec3 receiver_world,
    vec3 receiver_normal,
    uint receiver_owner_kind,
    int receiver_face_idx,
    ProbePayloadFlags flags
) {
    if (receiver_owner_kind == OWNER_SKY
            || receiver_owner_kind == OWNER_ACTOR_SPRITE) return 0.0;
    vec3 sun = normalize(q3_probe_payload_effective_sun_dir(flags));
    vec3 n = length(receiver_normal) > 1e-6
        ? normalize(receiver_normal) : vec3(0.0, 1.0, 0.0);
    vec3 light_side_n = dot(n, sun) >= 0.0 ? n : -n;
    ViewRay shadow_ray;
    shadow_ray.near_world =
        receiver_world + light_side_n * 0.02 + sun * 0.001;
    shadow_ray.dir = sun;
    shadow_ray.len = 1000.0;
    VsbRetainedStaticCasterSelection selected =
        vsb_retained_select_q3_static_shadow_caster(
            shadow_ray, receiver_face_idx);
    return selected.valid ? 1.0 : 0.0;
}

float dynamic_mover_shadow_density_at_world(vec3 receiver_world) {
    VsbRetainedDynamicSelection selected =
        vsb_retained_select_dynamic_shadow_caster(receiver_world);
    if (!selected.valid) return 0.0;
    return clamp(selected.density, 0.0, 1.0);
}

precise float q3_probe_payload_cast_shadow_attenuation(
    vec3 receiver_world,
    vec3 receiver_normal,
    uint receiver_owner_kind,
    int receiver_face_idx,
    ProbePayloadFlags flags
) {
    precise vec3 n = length(receiver_normal) > 1e-6
        ? normalize(receiver_normal) : vec3(0.0, 1.0, 0.0);
    precise float lambert_raw = max(dot(
        n, normalize(q3_probe_payload_effective_sun_dir(flags))), 0.0);
    precise float hemisphere = smoothstep(-0.18, 0.92, n.y);
    precise float ambient_value = clamp(
        0.16 + mix(0.28, 0.74, hemisphere) * 0.34, 0.0, 1.0);
    precise float lit_value =
        max(product_lighting_value(n, lambert_raw), 1e-4);
    precise float fully_shadowed_ratio =
        clamp(ambient_value / lit_value, 0.0, 1.0);
    precise float static_density = 0.0;
    if (fully_shadowed_ratio != 1.0) {
        static_density = q3_probe_payload_static_shadow_density(
            receiver_world, receiver_normal, receiver_owner_kind,
            receiver_face_idx, flags);
        if (static_density > 0.0 && fully_shadowed_ratio <= 0.38) {
            return fully_shadowed_ratio;
        }
    }
    precise float blob_density = max(
        actor_row_shadow_density_at_world(receiver_world),
        dynamic_mover_shadow_density_at_world(receiver_world));
    precise float blob_attenuation = max(
        mix(1.0, 0.38, clamp(blob_density, 0.0, 1.0)), 0.08);
    precise float static_attenuation =
        q3_probe_payload_static_shadow_light_attenuation(
            static_density, fully_shadowed_ratio);
    return min(blob_attenuation, static_attenuation);
}
"""


def test_shipping_source_requires_density_only_stage61_contract() -> None:
    errors = density_only_contract_errors(
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    )
    assert not errors, (
        "RED: stage-61 shadow still constructs discarded caster provenance; "
        "replace the actor/static/dynamic adapters with the pinned scalar "
        "density-only contract"
    )


def test_selected_density_only_fixture_satisfies_contract() -> None:
    assert density_only_contract_errors(SELECTED_SOURCE) == []


@pytest.mark.parametrize(
    ("name", "mutant", "expected_error"),
    (
        (
            "actor provenance restoration",
            SELECTED_SOURCE.replace(
                "            shadow_density = density;\n",
                "            shadow_density = density;\n"
                "            uint source_key = actor_cell_word(15, row);\n",
                1,
            ),
            "actor provenance remains",
        ),
        (
            "static center restoration",
            SELECTED_SOURCE.replace(
                "    return selected.valid ? 1.0 : 0.0;\n",
                "    vec2 caster_xz = (0.25 * (\n"
                "        selected.facts.row_0 + selected.facts.row_1\n"
                "        + selected.facts.row_2 + selected.facts.row_3)).xz;\n"
                "    return selected.valid ? 1.0 : 0.0;\n",
                1,
            ),
            "static provenance remains",
        ),
        (
            "dynamic identity restoration",
            SELECTED_SOURCE.replace(
                "            selected.valid = true;\n"
                "            continue;\n",
                "            selected.valid = true;\n"
                "            selected.identity.object_id = "
                "mover.identity_range.x;\n"
                "            continue;\n",
                1,
            ),
            "dynamic shadow provenance remains",
        ),
        (
            "density maximum drift",
            SELECTED_SOURCE.replace(
                "        if (density > shadow_density) {\n",
                "        if (density >= shadow_density) {\n",
                1,
            ),
            "actor strict density maximum drifted",
        ),
        (
            "dynamic NaN admission",
            SELECTED_SOURCE.replace(
                "            if (!(density > selected.density)) continue;\n",
                "            if (density <= selected.density) continue;\n",
                1,
            ),
            "dynamic strict density maximum drifted",
        ),
        (
            "static admission drift",
            SELECTED_SOURCE.replace(
                "shadow_ray, receiver_face_idx, "
                "FL4270_STATIC_FACE_SCAN_MAX, true);",
                "shadow_ray, receiver_face_idx, 511, true);",
                1,
            ),
            "static selector admission drifted",
        ),
        (
            "point-light any-hit leakage",
            SELECTED_SOURCE.replace(
                "FL4270_STATIC_FACE_SCAN_MAX, false);",
                "FL4270_STATIC_FACE_SCAN_MAX, true);",
                1,
            ),
            "point-light full-selection mode drifted",
        ),
        (
            "BLAS any-hit return removed",
            SELECTED_SOURCE.replace(
                "    if (q3_any_hit && hit) return true;\n",
                "    if (q3_any_hit && hit) selected.valid = true;\n",
                1,
            ),
            "BLAS any-hit propagation drifted",
        ),
        (
            "Q3 builds point-light payload",
            SELECTED_SOURCE.replace(
                "    if (q3_any_hit) return true;\n"
                "    selected.valid = true;\n",
                "    selected.valid = true;\n"
                "    if (q3_any_hit) return true;\n",
                1,
            ),
            "Q3 any-hit constructs point-light payload",
        ),
        (
            "dead sun wrapper restored",
            SELECTED_SOURCE
            + "\nVsbRetainedStaticCasterSelection "
            "vsb_retained_select_sun_shadow_caster("
            "ViewRay ray, int receiver) {\n"
            "    return vsb_retained_select_static_shadow_caster("
            "ray, receiver, FL4270_STATIC_FACE_SCAN_MAX, true);\n"
            "}\n",
            "dead sun-shadow wrapper resurrected",
        ),
    ),
)
def test_destructive_mutants_are_rejected(
    name: str,
    mutant: str,
    expected_error: str,
) -> None:
    assert mutant != SELECTED_SOURCE, name
    errors = density_only_contract_errors(mutant)
    assert expected_error in errors, f"{name} survived: {errors}"
