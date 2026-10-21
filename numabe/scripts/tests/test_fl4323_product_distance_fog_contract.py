#!/usr/bin/env python3
"""FL-4323 / RQ-182 Product cloud-air fog contract.

The operator-designated 012331fe3 target lights the camera-to-receiver air
column through gaps in the Product cloud field. A camera-distance sky-colour
fade is a rejected substitute. The compositor may not discover or repaint fog.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
COMPOSITOR = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def _function_body(source: str, name: str) -> str:
    """Return one GLSL function body using balanced braces."""
    clean = _without_comments(source)
    match = re.search(
        rf"(?m)^\s*(?:[A-Za-z_]\w*\s+)+{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        clean,
        re.S,
    )
    assert match is not None, f"missing Product function: {name}"
    brace = clean.find("{", match.start())
    depth = 0
    for index in range(brace, len(clean)):
        if clean[index] == "{":
            depth += 1
        elif clean[index] == "}":
            depth -= 1
            if depth == 0:
                return clean[match.start() : index + 1]
    raise AssertionError(f"unterminated Product function: {name}")


def _replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, f"mutation precondition drifted: {old!r}"
    return source.replace(old, new, 1)


def _contract_errors(product: str, compositor: str) -> tuple[str, ...]:
    errors: list[str] = []
    clean = _without_comments(product)
    compact = _compact(clean)
    sky = _compact(_function_body(product, "product_sky_paint"))
    night_cloud = _compact(_function_body(product, "night_sky_cloud_occlusion"))
    water_pair = _compact(_function_body(product, "product_apply_day_water_pair"))
    cloud_density = _compact(
        _function_body(product, "product_cloud_density_at_sample_xz")
    )
    cloud_shadow_density = _compact(
        _function_body(product, "product_cloud_shadow_density_at_sample_xz")
    )
    projected_shadow = _compact(
        _function_body(product, "product_cloud_shadow_projected_world")
    )
    clean_compositor = _without_comments(compositor)

    for rejected in (
        "ProductDistanceFogFact",
        "product_distance_fog_fact",
        "product_apply_distance_fog",
        "product_distance_fog_visible_target",
        "ProductPerspectiveAirFogFact",
        "product_perspective_air_fog_fact",
        "product_apply_perspective_air_fog",
        "FL4323_FOG_START_FRACTION",
        "FL4323_FOG_END_FRACTION",
        "FL4323_FOG_DAY_TARGET_LUMA",
        "FL4323_FOG_NIGHT_TARGET_LUMA",
        "FL4323_PERSPECTIVE_AIR_FOG_START_FRACTION",
        "FL4323_PERSPECTIVE_AIR_FOG_END_FRACTION",
        "ProductRq182FarFogFact",
        "product_rq182_far_fog_fact",
        "product_apply_rq182_far_fog",
        "FL4323_FOG_RECEIVER_RANGE_MAX_CELLS",
        "haze_tint",
    ):
        if rejected in compact:
            errors.append(f"rejected Product fog substitute is live: {rejected}")
        if rejected in clean_compositor:
            errors.append(f"compositor regained a fog substitute: {rejected}")

    cutover = _compact(_function_body(product, "q3_probe_payload_cutover_cloud_shadow_for_cell"))
    for token in (
        "center=q3_probe_payload_live_cloud_shadow_for_cell(cell,flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(1,0),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(-1,0),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(0,1),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(0,-1),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(1,1),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(1,-1),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(-1,1),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(-1,-1),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(2,0),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(-2,0),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(0,2),flags)",
        "q3_probe_payload_live_cloud_shadow_for_cell(cell+ivec2(0,-2),flags)",
        "returnsmoothstep(0.20,0.82,filtered);",
    ):
        if token not in cutover:
            errors.append(f"cutover lost current live-cloud token: {token}")

    for token in (
        "FL4273_CLOUD_BLOCK_SCALE=0.40",
        "floor(uv0/FL4273_CLOUD_BLOCK_SCALE)+vec2(0.5)",
        "floor(uv1/FL4273_CLOUD_BLOCK_SCALE)+vec2(0.5)",
        "stable_value_noise(block_center_0*FL4273_CLOUD_MASS_FREQ,11u)",
        "stable_value_noise(block_center_1*FL4273_CLOUD_MASS_FREQ,23u)",
    ):
        if token not in cloud_density:
            errors.append(f"012331fe3 shared cloud block owner lost token: {token}")

    for token in (
        "stable_value_noise(uv0*FL4273_CLOUD_MASS_FREQ,11u)",
        "stable_value_noise(uv1*FL4273_CLOUD_MASS_FREQ,23u)",
    ):
        if token not in cloud_shadow_density:
            errors.append(f"continuous receiver-shadow filter lost token: {token}")
    for forbidden in (
        "FL4273_CLOUD_BLOCK_SCALE",
        "floor(uv0/",
        "floor(uv1/",
        "block_center_",
    ):
        if forbidden in cloud_shadow_density:
            errors.append(
                f"receiver-shadow filter regained temporal block step: {forbidden}"
            )
    if (
        "product_cloud_shadow_density_at_sample_xz((receiver+sun*t_cloud).xz)"
        not in projected_shadow
    ):
        errors.append("projected receiver shadow bypasses its continuous filter")

    for required in (
        "ProductCloudAirColumnFogFact",
        "product_cloud_air_column_fog_fact",
    ):
        if required not in compact:
            errors.append(f"RQ-182 cloud-air fog fact missing: {required}")
        if required in clean_compositor:
            errors.append(f"compositor illegally owns RQ-182 fog: {required}")
    air_fog = _compact(_function_body(product, "product_cloud_air_column_fog_fact"))
    finalizer = _compact(_function_body(product, "vsb_product_finalize_record"))
    for token in (
        "constintFL4323_CLOUD_AIR_FOG_STEPS=10;",
        "constfloatFL4323_CLOUD_AIR_FOG_PHASE_POWER=3.0;",
        "constfloatFL4323_CLOUD_AIR_FOG_PHASE_FLOOR=0.08;",
        "constfloatFL4323_CLOUD_AIR_FOG_INTENSITY=0.55;",
        "constvec3FL4323_CLOUD_AIR_FOG_TINT=vec3(0.42,0.54,0.74);",
        "constfloatFL4323_CLOUD_AIR_FOG_GAP_LOW=0.35;",
        "constfloatFL4323_CLOUD_AIR_FOG_GAP_HIGH=0.85;",
    ):
        if token not in compact:
            errors.append(f"012331fe3 cloud-air fog constant drifted: {token}")
    for token in (
        "moon=normalize(effective_sun_dir());",
        "moon.y<0.05||!baseline_feature_clouds_enabled()",
        "dot(receiver_world-ray.near_world,ray.dir)",
        "pow(facing_moon,FL4323_CLOUD_AIR_FOG_PHASE_POWER)",
        "for(inti=0;i<FL4323_CLOUD_AIR_FOG_STEPS;++i)",
        "(float(i)+0.5)/float(FL4323_CLOUD_AIR_FOG_STEPS)",
        "(FL4321_CLOUD_PLANE_Y-air_sample.y)/max(moon.y,0.10)",
        "product_cloud_density_at_sample_xz((air_sample+moon*cloud_depth).xz)",
        "smoothstep(FL4323_CLOUD_AIR_FOG_GAP_LOW,FL4323_CLOUD_AIR_FOG_GAP_HIGH,1.0-cloud)",
        "fact.strength=lit*phase*FL4323_CLOUD_AIR_FOG_INTENSITY;",
        "fact.light=FL4323_CLOUD_AIR_FOG_TINT*fact.strength;",
    ):
        if token not in air_fog:
            errors.append(f"RQ-182 cloud-air fog fact lost required token: {token}")
    for token in (
        # Vegetation now consumes this same cloud field in its single exposure
        # owner. Only non-vegetation retains the historical additive finalizer.
        "if(baseline_feature_night_enabled()&&record.r0.y!=OWNER_SKY&&!vegetation_receiver)",
        "product_cloud_air_column_fog_fact(input_value.ray,effect_receiver)",
        "record.fg.rgb=clamp(record.fg.rgb+air_fog.light,vec3(0.0),vec3(1.0));",
        "record.bg.rgb=clamp(record.bg.rgb+air_fog.light,vec3(0.0),vec3(1.0));",
    ):
        if token not in finalizer:
            errors.append(f"RQ-182 cloud-air finalizer lost required token: {token}")

    for historical_token in (
        "vec3night_base_fg=mix(vec3(0.08,0.10,0.20),vec3(0.52,0.64,0.88),flash*(0.45+cloud*0.45));",
        "paint.foreground=mix(SKY_FG,night_base_fg,nf);",
        "vec3night_bg=vec3(0.014,0.020,0.046);",
        "vec3cloud_lift=vec3(0.09,0.105,0.16)*cloud*(0.18+halo*0.28);",
        "night_sky_cloud_occlusion(sky_ray)",
    ):
        if historical_token not in sky:
            errors.append(f"ProductSkyPaint lost 012331fe3 fog/sky token: {historical_token}")
    if "paint.foreground=paint.background;" in sky:
        errors.append("ordinary sky ink was collapsed into paper again")

    for cloud_token in (
        "!baseline_feature_clouds_enabled()",
        "product_cloud_density_at_sample_xz(cloud_world.xz)",
    ):
        if cloud_token not in night_cloud:
            errors.append(f"night sky cloud occlusion lost Product cloud token: {cloud_token}")

    for water_token in (
        "WATER_FOG_COLOR",
        "WATER_FOG_COLOR_NIGHT",
        "WATER_FOG_FG_PEAK",
        "WATER_FOG_BG_PEAK",
        "mix(foreground,fog_target,foreground_fog_amount)",
        "mix(clamp(background,vec3(0.0),vec3(1.0)),fog_target,background_fog_amount)",
    ):
        if water_token not in water_pair:
            errors.append(f"water-column fog token missing: {water_token}")

    return tuple(errors)


def _compile_product_shader() -> None:
    compiler = shutil.which("glslangValidator")
    validator = shutil.which("spirv-val")
    assert compiler is not None, "glslangValidator is required for FL-4323"
    assert validator is not None, "spirv-val is required for FL-4323"
    source = _text(PRODUCT)
    assert source.startswith("#[compute]\n"), "Product shader lost Godot compute marker"
    with tempfile.TemporaryDirectory(prefix="fl4323-product-") as temp_dir:
        shader_path = Path(temp_dir) / "cell_owner_product.comp"
        spirv_path = Path(temp_dir) / "cell_owner_product.spv"
        shader_path.write_text(source.removeprefix("#[compute]\n"), encoding="utf-8")
        compiled = subprocess.run(
            [
                compiler,
                "-V",
                "--target-env",
                "vulkan1.2",
                "-S",
                "comp",
                str(shader_path),
                "-o",
                str(spirv_path),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert compiled.returncode == 0, compiled.stdout
        validated = subprocess.run(
            [validator, str(spirv_path)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert validated.returncode == 0, validated.stdout


def test_actual_product_shader_compiles_and_uses_rq182_fog_sources() -> None:
    product = _text(PRODUCT)
    compositor = _text(COMPOSITOR)
    errors = _contract_errors(product, compositor)
    assert not errors, "RED: " + "; ".join(errors)
    _compile_product_shader()


@pytest.mark.parametrize(
    ("label", "mutate", "expected"),
    (
        (
            "reintroduced perspective air fog owner",
            lambda source: source.replace(
                "float product_distance_to_segment",
                "struct ProductPerspectiveAirFogFact { bool eligible; };\nfloat product_distance_to_segment",
                1,
            ),
            "rejected Product fog substitute is live",
        ),
        (
            "ordinary sky collapsed to paper",
            lambda source: _replace_once(
                source,
                "paint.foreground = mix(SKY_FG, night_base_fg, nf);",
                "paint.foreground = paint.background;",
            ),
            "ordinary sky ink was collapsed",
        ),
        (
            "distance fog owner restored",
            lambda source: _replace_once(
                source,
                "float product_distance_to_segment",
                "struct ProductRq182FarFogFact { bool eligible; };\nfloat product_distance_to_segment",
            ),
            "rejected Product fog substitute is live",
        ),
        (
            "cloud-air fog stops sampling the Product cloud field",
            lambda source: _replace_once(
                source,
                "float cloud = product_cloud_density_at_sample_xz(\n\t\t\t(air_sample + moon * cloud_depth).xz);",
                "float cloud = 0.0;",
            ),
            "RQ-182 cloud-air fog fact lost required token",
        ),
        (
            "historical ten-step integration reduced",
            lambda source: _replace_once(
                source,
                "const int FL4323_CLOUD_AIR_FOG_STEPS = 10;",
                "const int FL4323_CLOUD_AIR_FOG_STEPS = 8;",
            ),
            "012331fe3 cloud-air fog constant drifted",
        ),
        (
            "receiver shadow reuses block-sampled fog field",
            lambda source: _replace_once(
                source,
                "return product_cloud_shadow_density_at_sample_xz(\n"
                "\t\t(receiver + sun * t_cloud).xz);",
                "return product_cloud_density_at_sample_xz(\n"
                "\t\t(receiver + sun * t_cloud).xz);",
            ),
            "projected receiver shadow bypasses its continuous filter",
        ),
    ),
)
def test_destructive_mutants_turn_contract_red(
    label: str, mutate, expected: str
) -> None:
    product = mutate(_text(PRODUCT))
    errors = _contract_errors(product, _text(COMPOSITOR))
    assert errors, f"RED control slipped through: {label}"
    assert any(expected in error for error in errors), (label, errors)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
