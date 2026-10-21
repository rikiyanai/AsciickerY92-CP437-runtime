#!/usr/bin/env python3
"""FL-4431 exact-zero local-light visibility admission contract.

Product currently performs retained static and actor visibility scans after the
distance and normal terms have already reduced a light's contribution to exact
zero. Those scans cannot affect pixels: multiplying zero by either visibility
result stays zero, bonfire warmth receives zero, and the later light-luma gate
rejects the zero accumulated value.

This red-first contract requires the exact-zero rejection before visibility.
It deliberately rejects tolerance thresholds: small positive light is still a
real authored contribution and must retain the existing visibility and paint
path.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def extract_glsl_function(source: str, name: str) -> str:
    match = re.search(
        rf"\b(?:bool|float|vec3)\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        source,
    )
    assert match is not None, f"missing GLSL function {name}"
    depth = 1
    cursor = match.end()
    while cursor < len(source) and depth:
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
        cursor += 1
    assert depth == 0, f"unterminated GLSL function {name}"
    return source[match.end() : cursor - 1]


def compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def post_normal_falloff(
    radius_t: float,
    intensity: float,
    normal_applies: bool,
    normal_dot: float,
) -> float:
    falloff = f32(f32(f32(radius_t) * f32(radius_t)) * f32(intensity))
    if normal_applies:
        falloff = f32(falloff * f32(max(normal_dot, 0.0)))
    return falloff


def downstream_zero_facts(
    falloff: float,
    visibility: float,
    kind: int,
) -> tuple[float, float, bool]:
    """Return accumulated scalar, warmth, and final light-band admission."""
    visible_falloff = f32(f32(falloff) * f32(visibility))
    accumulated = visible_falloff
    warmth = f32(visible_falloff * f32(0.85)) if kind == 1 else 0.0
    light_band_admitted = accumulated >= f32(0.001)
    return accumulated, warmth, light_band_admitted


def local_light_source_shape(source: str) -> bool:
    """Require one exact-zero continue plus both existing visibility branches."""
    body = compact(extract_glsl_function(source, "vsb_product_local_light_addition"))
    normal_term = body.find("falloff*=max(dot(")
    zero_admission = body.find("if(falloff==0.0)continue;")
    visibility_gate = body.find(
        "if((local_light_flags(light_index)&LOCAL_LIGHT_FLAG_CAST_SHADOW)!=0)"
    )
    visibility_call = body.find("vsb_product_local_light_visibility(")
    occlusion_branch = body.find(
        "floatocclusion_depth=local_light_occlusion_depth_key(light_index);"
    )
    pre_visibility = (
        body[normal_term:visibility_gate]
        if normal_term >= 0 and visibility_gate >= 0
        else ""
    )
    return (
        normal_term >= 0
        and normal_term < zero_admission < visibility_gate < visibility_call
        and occlusion_branch > visibility_call
        and pre_visibility.count("continue;") == 1
        and body.count("if(falloff==0.0)continue;") == 1
        and "falloff<=" not in pre_visibility
        and "abs(falloff)" not in pre_visibility
    )


def source_with_selected_admission(source: str) -> str:
    """Create the exact selected source shape for destructive red-first tests."""
    if local_light_source_shape(source):
        return source
    marker = (
        "\t\tif ((local_light_flags(light_index) "
        "& LOCAL_LIGHT_FLAG_CAST_SHADOW) != 0) {"
    )
    assert marker in source
    return source.replace(
        marker,
        "\t\tif (falloff == 0.0) continue;\n" + marker,
        1,
    )


def evaluate_light_sequence(
    entries: tuple[tuple[int, float, float], ...],
    skip_exact_zero: bool,
) -> tuple[float, float, int, bool, bool]:
    """Model every output-sensitive scalar changed by loop admission.

    The third tuple value is the downstream multiplier. It represents both the
    cast-shadow visibility result and the non-shadow occlusion multiplier:
    both branches only multiply the already-computed falloff.
    """
    accumulated = f32(0.0)
    warmth = f32(0.0)
    accepted_count = 0
    for kind, falloff, downstream_multiplier in entries:
        falloff = f32(falloff)
        if skip_exact_zero and falloff == 0.0:
            continue
        visible = f32(falloff * f32(downstream_multiplier))
        if kind == 1:
            warmth = f32(warmth + f32(visible * f32(0.85)))
        accumulated = f32(accumulated + visible)
        accepted_count += 1
    bonfire_contributed = warmth > f32(0.01)
    light_band_admitted = (
        accepted_count > 0 and accumulated >= f32(0.001)
    )
    return (
        accumulated,
        warmth,
        accepted_count,
        bonfire_contributed,
        light_band_admitted,
    )


@pytest.mark.parametrize("kind", [0, 1, 2, 3, 5])
@pytest.mark.parametrize("visibility", [0.0, 0.25, 1.0])
@pytest.mark.parametrize(
    ("radius_t", "intensity", "normal_applies", "normal_dot"),
    [
        (0.0, 1.0, False, 1.0),
        (1.0, 0.0, False, 1.0),
        (1.0, 1.0, True, -1.0),
        (0.0, 0.0, True, 0.0),
    ],
)
def test_exact_zero_cannot_change_visible_local_light_output(
    kind: int,
    visibility: float,
    radius_t: float,
    intensity: float,
    normal_applies: bool,
    normal_dot: float,
) -> None:
    falloff = post_normal_falloff(
        radius_t,
        intensity,
        normal_applies,
        normal_dot,
    )
    assert falloff == 0.0
    accumulated, warmth, admitted = downstream_zero_facts(
        falloff,
        visibility,
        kind,
    )
    assert accumulated == 0.0
    assert warmth == 0.0
    assert not admitted


def test_positive_contribution_is_not_part_of_the_admission_cut() -> None:
    """A widened threshold is a visual change, not this optimization."""
    falloff = post_normal_falloff(0.5, 0.2, True, 0.8)
    assert falloff > 0.0
    accumulated, warmth, admitted = downstream_zero_facts(
        falloff,
        1.0,
        1,
    )
    assert accumulated > 0.0
    assert warmth > 0.01
    assert admitted
    assert not (falloff == 0.0)
    assert falloff <= 0.2  # A damaging broad-threshold mutant skips this light.


@pytest.mark.parametrize(
    "entries",
    [
        ((0, 0.0, 0.0),),
        ((1, 0.0, 1.0),),
        ((1, 0.0, 0.0), (2, 0.08, 1.0)),
        ((0, 0.0, 1.0), (1, 0.02, 0.75), (2, 0.0, 0.25)),
    ],
)
def test_mixed_light_outputs_ignore_only_zero_accepted_count(
    entries: tuple[tuple[int, float, float], ...],
) -> None:
    eager = evaluate_light_sequence(entries, False)
    selected = evaluate_light_sequence(entries, True)
    assert eager[0] == selected[0]  # accumulated light
    assert eager[1] == selected[1]  # bonfire warmth
    assert eager[3] == selected[3]  # contributed flag
    assert eager[4] == selected[4]  # final light-band admission
    # accepted_count is private and may differ only when no positive term was
    # deleted; the final luma admission above proves that difference invisible.
    positive_count = sum(1 for _kind, falloff, _multiplier in entries if falloff > 0.0)
    assert selected[2] == positive_count


def test_shipping_source_rejects_exact_zero_before_visibility() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert local_light_source_shape(source), (
        "RED: Product still performs retained local-light visibility after "
        "distance/normal attenuation has reached exact zero, or a broader "
        "positive-light rejection has been added beside the exact cut"
    )


def test_source_guard_rejects_broad_threshold_and_late_admission_mutants() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    selected = source_with_selected_admission(source)
    assert local_light_source_shape(selected)

    broad = selected.replace(
        "\t\tif (falloff == 0.0) continue;",
        "\t\tif (falloff <= 0.2) continue;\n"
        "\t\tif (falloff == 0.0) continue;",
        1,
    )
    assert broad != selected
    assert not local_light_source_shape(broad)

    late = selected.replace(
        "\t\tif (falloff == 0.0) continue;",
        "",
        1,
    )
    late = late.replace(
        "\t\t\tfalloff *= vsb_product_local_light_visibility(",
        "\t\t\tfalloff *= vsb_product_local_light_visibility(",
        1,
    )
    assert not local_light_source_shape(late)

    non_shadow_deleted = selected.replace(
        "float occlusion_depth = local_light_occlusion_depth_key(light_index);",
        "float removed_occlusion_depth = "
        "local_light_occlusion_depth_key(light_index);",
        1,
    )
    assert non_shadow_deleted != selected
    assert not local_light_source_shape(non_shadow_deleted)
