#!/usr/bin/env python3
"""FL-4101 Product daylight-godray contract.

TXN-11 deletes the falsified structural step-train criterion.  The rejected
criterion asserted 10-16 micro-runs, 6-22px widths, 1-2px transitions, and a
0.02 luminance floor.  Direct target inspection showed that those numbers
measured cell texture inside rays, not the rays themselves.

The visibility gate below is derived by running this same analyzer over
docs/agent/godot-visual-restoration-and-target-handoff/targets/
01-t3ssel8r-godrays-video-frame.png at 720 scale.  With the target-described
diagonal veil normal, the target measures:

    normal=130 deg, veils=6, widths=[39, 55, 157, 15, 71, 84],
    ray_gap_contrast=0.01756, local_noise_sigma=0.00362, snr=4.86,
    ray_interior_saturation/gap_saturation=0.76.

The Product frame gate therefore checks the visible target property: 3-8 broad
soft veils, ray-vs-gap contrast above 3x local off-ray scene noise, and a haze
flattening/desaturation drop inside rays relative to gaps.  It intentionally
does not assert micro-step counts, micro-widths, or flat-plateau stair steps.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from dataclasses import dataclass
from pathlib import Path
import hashlib
import re

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
TARGET = (
    ROOT
    / "docs/agent/godot-visual-restoration-and-target-handoff/targets/"
    "01-t3ssel8r-godrays-video-frame.png"
)
TXN10_COUNTEREXAMPLES = [
    ROOT / ".run/final_render_cell_dump/1786960364_29069/final.png",
    ROOT / ".run/final_render_cell_dump/1786960719_29087/final.png",
    ROOT / ".run/final_render_cell_dump/1786960969_66298/final.png",
]

# The ground cloud owner is outside TXN-11's godray-query veil rendering.  This
# is the exact function-body byte identity for receiver cloud shadows, including
# the surface binary smoothstep that godray queries must not reuse directly.
GROUND_CLOUD_DENSITY_SHA256 = (
    "a652a37a1864c962617b915ca09b604888b05d2edb03d471bf2c0a12500c41e0"
)

TARGET_VEIL_NORMAL_DEGREES = 130
TARGET_PROFILE_PERCENTILE = 48.0
VEIL_COUNT_MIN = 3
VEIL_COUNT_MAX = 8
VISIBILITY_SNR_MIN = 3.0
HAZE_SATURATION_RATIO_MAX = 0.86


@dataclass(frozen=True)
class VeilVisibilityMeasurement:
    image: Path
    normal_degrees: int
    veil_count: int
    widths_px: list[int]
    ray_gap_contrast: float
    local_noise_sigma: float
    snr: float
    haze_saturation_ratio: float

    @property
    def passes_visibility_gate(self) -> bool:
        return (
            VEIL_COUNT_MIN <= self.veil_count <= VEIL_COUNT_MAX
            and self.snr >= VISIBILITY_SNR_MIN
            and self.haze_saturation_ratio <= HAZE_SATURATION_RATIO_MAX
        )


def function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        depth += source[index] == "{"
        depth -= source[index] == "}"
        if depth == 0:
            return source[start:index + 1]
    raise AssertionError(f"unterminated function {signature}")


def _declared_function_names(source: str, pattern: str) -> list[str]:
    return re.findall(
        rf"(?m)^\s*(?:precise\s+)?(?:[A-Za-z_][A-Za-z0-9_]*|void|float|vec2|vec3|vec4)\s+"
        rf"({pattern})\s*\(",
        source,
    )


def _profile_blur(values: np.ndarray, sigma: float) -> np.ndarray:
    kernel = max(3, int(round(sigma * 6.0)) | 1)
    return cv2.GaussianBlur(
        values.astype(np.float32).reshape(1, -1),
        (kernel, 1),
        sigmaX=sigma,
        borderType=cv2.BORDER_REPLICATE,
    ).ravel()


def _profile_morph(mask: np.ndarray, operation: int, width: int) -> np.ndarray:
    return cv2.morphologyEx(
        mask.astype(np.uint8).reshape(1, -1),
        operation,
        np.ones((1, width), np.uint8),
    ).astype(bool).ravel()


def measure_veil_visibility(
    image_path: Path,
    *,
    normal_degrees: int = TARGET_VEIL_NORMAL_DEGREES,
    profile_percentile: float = TARGET_PROFILE_PERCENTILE,
) -> VeilVisibilityMeasurement:
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    if width != 720:
        image = image.resize((720, round(height * 720 / width)), Image.Resampling.LANCZOS)
    rgb = np.asarray(image).astype(np.float32) / 255.0
    luma = (
        0.2126 * rgb[:, :, 0]
        + 0.7152 * rgb[:, :, 1]
        + 0.0722 * rgb[:, :, 2]
    )
    rows, cols = luma.shape
    yy, xx = np.indices((rows, cols))
    center_x = (cols - 1) * 0.5
    center_y = (rows - 1) * 0.5
    theta = np.deg2rad(float(normal_degrees))
    projected = (xx - center_x) * np.cos(theta) + (yy - center_y) * np.sin(theta)
    offset = -np.floor(projected.min())
    profile_index = np.round(projected.ravel() + offset).astype(int)
    profile_len = int(profile_index.max()) + 1

    profile_sum = np.bincount(profile_index, luma.ravel(), minlength=profile_len)
    profile_count = np.bincount(profile_index, minlength=profile_len)
    profile = profile_sum / np.maximum(profile_count, 1)
    valid = profile_count > max(12, 0.01 * profile_count.max())

    broad_profile = _profile_blur(profile, 6.0) - _profile_blur(profile, 55.0)
    local_profile_noise = profile - _profile_blur(profile, 10.0)
    threshold = float(np.percentile(broad_profile[valid], profile_percentile))
    ray_profile_mask = (broad_profile > threshold) & valid
    ray_profile_mask = _profile_morph(ray_profile_mask, cv2.MORPH_CLOSE, 21)
    ray_profile_mask = _profile_morph(ray_profile_mask, cv2.MORPH_OPEN, 7)

    widths: list[int] = []
    start: int | None = None
    for index, value in enumerate(ray_profile_mask):
        if value and start is None:
            start = index
        if start is not None and ((not value) or index == len(ray_profile_mask) - 1):
            end = index if not value else index + 1
            width_px = end - start
            if width_px >= 10:
                widths.append(width_px)
            start = None

    ray_profile = ray_profile_mask & valid
    gap_profile = (~ray_profile_mask) & valid
    ray_gap_contrast = float(
        np.median(broad_profile[ray_profile]) - np.median(broad_profile[gap_profile])
    )
    local_noise_sigma = float(
        1.4826
        * np.median(
            np.abs(local_profile_noise[gap_profile] - np.median(local_profile_noise[gap_profile]))
        )
    )

    eroded_ray_profile = cv2.erode(
        ray_profile_mask.astype(np.uint8).reshape(1, -1),
        np.ones((1, 15), np.uint8),
    ).astype(bool).ravel()
    per_pixel_profile_index = np.clip(
        np.round(projected + offset).astype(int),
        0,
        len(ray_profile_mask) - 1,
    )
    center_mask = np.zeros_like(luma, dtype=bool)
    center_mask[int(rows * 0.12):int(rows * 0.92), int(cols * 0.03):int(cols * 0.97)] = True
    ray_pixels = eroded_ray_profile[per_pixel_profile_index] & center_mask
    gap_pixels = (~ray_profile_mask[per_pixel_profile_index]) & center_mask
    hsv = cv2.cvtColor((rgb * 255.0).astype(np.uint8), cv2.COLOR_RGB2HSV).astype(np.float32)
    saturation = hsv[:, :, 1] / 255.0
    haze_saturation_ratio = float(
        np.median(saturation[ray_pixels])
        / max(float(np.median(saturation[gap_pixels])), 1e-6)
    )

    return VeilVisibilityMeasurement(
        image=image_path,
        normal_degrees=normal_degrees,
        veil_count=len(widths),
        widths_px=widths,
        ray_gap_contrast=ray_gap_contrast,
        local_noise_sigma=local_noise_sigma,
        snr=ray_gap_contrast / max(local_noise_sigma, 1e-6),
        haze_saturation_ratio=haze_saturation_ratio,
    )


def assert_premature_godray_owner_retired(source: str) -> None:
    finalizer = function_body(source, "ProbeSubcellProductRecord vsb_product_finalize_record(")
    helper = function_body(source, "ProductDaylightGodrayVeil product_daylight_godray_veil(")
    for token in ("night_moon_godray_inscatter", "NIGHT_GODRAY_", "product_spike_2am_look"):
        assert token not in source, f"retired godray owner survived: {token}"

    assert source.count("struct ProductDaylightGodrayVeil") == 1
    assert source.count(
        "ProductDaylightGodrayVeil product_daylight_godray_veil(\n"
        "\tViewRay ray,\n"
        "\tfloat receiver_depth,\n"
        "\tbool capture_visibility\n"
        ")"
    ) == 1
    for pin in (
        "ProductDaylightGodrayVeil empty_veil = product_empty_daylight_godray_veil();",
        "precise vec3 sun = vsb_product_selected_effective_sun_dir;",
        "precise vec2 plane_normal_xz = vec2(-sun_xz.y, sun_xz.x) / sun_xz_len;",
        "precise vec3 plane_normal = vec3(",
        "precise float plane_t_step =\n\t\tFL4101_DAY_GODRAY_PLANE_GAP / absolute_rate;",
        "precise vec3 column_mid_world = ray.near_world + ray.dir * (0.5 * t_end);",
        "precise float column_veil =",
        "product_godray_cloud_veil_strength_at_sample_xz(\n\t\t\tcolumn_cloud_sample_xz);",
        "float front_to_back_transmittance = 1.0;",
        "float accumulated_strength = 0.0;",
        "precise float sample_strength = column_veil * ridge_lit;",
        "front_to_back_transmittance *= 1.0 - clamp(layer_opacity, 0.0, 0.96);",
        "ProductDaylightGodrayVeil veil;",
        "veil.strength = resolved_strength;",
        "veil.light = FL4101_DAY_GODRAY_VEIL_LIGHT * resolved_strength;",
        "product_capture_godray_accumulated_light = veil.light;",
        "return veil;",
    ):
        assert pin in helper, f"world-column veil pin drifted: {pin}"

    raw_names = _declared_function_names(source, r"product_godray_cloud_veil_strength_at_sample_xz")
    assert raw_names == ["product_godray_cloud_veil_strength_at_sample_xz"]
    raw_sampler = function_body(source, f"{raw_names[0]}(")
    uv_names = _declared_function_names(source, r"product_cloud_sample_uvs")
    assert uv_names == ["product_cloud_sample_uvs"]
    uv_sampler = function_body(source, "product_cloud_sample_uvs(")
    broad_sampler = function_body(source, "product_cloud_raw_mass_from_uvs(")
    assert "product_cloud_sample_uvs(" in raw_sampler
    assert "product_cloud_raw_mass_from_uvs(" in raw_sampler
    assert broad_sampler.count("stable_value_noise(") == 2
    assert "product_cloud_shadow_time(" in uv_sampler
    assert "cloud_dir" in uv_sampler
    assert "time * 0.10" in uv_sampler
    assert "sample_xz * 0.135" in uv_sampler
    assert "sample_xz * 0.108" in uv_sampler
    for forbidden in (
        "sample_xz * 0.27",
        "sample_xz * 0.216",
        "stable_value_noise(cloud_uvs.xy * 1.40",
        "stable_value_noise(cloud_uvs.zw * 6.10",
        "smoothstep(0.50, 0.82",
        "step(0.52",
    ):
        assert forbidden not in raw_sampler + uv_sampler
    assert "1.0 - smoothstep(" in raw_sampler
    ground_density = function_body(source, "float product_cloud_density_at_sample_xz(")
    assert hashlib.sha256(ground_density.encode()).hexdigest() == GROUND_CLOUD_DENSITY_SHA256

    ridge_names = _declared_function_names(
        source, r"product_godray_terrain_light_visibility_at_air_sample"
    )
    assert len(ridge_names) == 1, "ridge visibility helper must be singular"
    ridge = function_body(source, f"{ridge_names[0]}(")
    assert f"{ridge_names[0]}(" in helper
    for pin in (
        "const int FL4101_DAY_GODRAY_PLANES = 16;",
        "const float FL4101_DAY_GODRAY_PLANE_GAP = 9.0;",
        "const vec3 FL4101_DAY_GODRAY_VEIL_LIGHT = vec3(",
        "const vec3 FL4101_DAY_GODRAY_VEIL_COLOR = vec3(",
        "const float FL4101_DAY_GODRAY_LAYER_ALPHA = ",
        "const float FL4101_DAY_GODRAY_HAZE_MIX = ",
        "const float FL4101_DAY_GODRAY_GAP_RETENTION = ",
    ):
        assert pin in source, f"world-column veil constant drifted: {pin}"
    assert "sun.xz" in ridge
    assert "cell_max_height(" in ridge
    assert ridge.count("for (") == 1
    assert "any(lessThan(probe_cell, ivec2(0)))" in ridge
    assert "any(greaterThanEqual(probe_cell, dims))" in ridge
    ridge_code = "\n".join(line.split("//", 1)[0] for line in ridge.splitlines())
    for forbidden in (
        "q3_probe_payload_", "TLAS", "receiver_cast_shadow",
        "heightfield_shadow_attenuation", "receiver_world",
    ):
        assert forbidden not in ridge_code

    helper_code = "\n".join(line.split("//", 1)[0] for line in helper.splitlines())
    assert "receiver_world" not in helper_code
    assert "receiver_depth /" not in helper_code
    assert "SCREEN_UV" not in helper_code
    assert "camera_position" not in helper_code
    for retired in ("shaft_0", "shaft_1", "shaft_2", "shaft_3", "lake_along", "Rake", "coverage", "terrain_visibility"):
        assert retired not in helper_code, f"authored godray geometry survived: {retired}"

    assert "if (!baseline_feature_night_enabled())" in finalizer
    assert finalizer.count(
        "ProductDaylightGodrayVeil godray_veil = product_daylight_godray_veil(\n"
        "\t\t\tinput_value.ray, effect_depth, capture_godray_visibility);"
    ) == 1
    for pin in (
        "float gap_retention =",
        "FL4101_DAY_GODRAY_GAP_RETENTION, 1.0, godray_veil.strength);",
        "float haze_mix =",
        "godray_veil.strength * FL4101_DAY_GODRAY_HAZE_MIX",
        "mix(record.fg.rgb * gap_retention + godray_veil.light,",
        "mix(record.bg.rgb * gap_retention + godray_veil.light * 0.82,",
        "FL4101_DAY_GODRAY_VEIL_COLOR",
    ):
        assert pin in finalizer, f"finalizer veil/haze pin drifted: {pin}"
    assert "record.r0.y != OWNER_SKY" not in finalizer[finalizer.index("ProductDaylightGodrayVeil godray_veil"):]
    assert "OWNER_WATER" not in finalizer[finalizer.index("ProductDaylightGodrayVeil godray_veil"):]
    assert "OWNER_TERRAIN" not in finalizer[finalizer.index("ProductDaylightGodrayVeil godray_veil"):]
    assert source.count("vec3 vsb_product_selected_effective_sun_dir;") == 1
    assert source.count("float product_cloud_density_at_sample_xz(") == 1


def test_premature_daytime_godray_owner_is_retired() -> None:
    assert_premature_godray_owner_retired(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


def test_target_image_passes_visibility_gate() -> None:
    measurement = measure_veil_visibility(TARGET)
    assert measurement.veil_count == 6
    assert measurement.widths_px == [39, 55, 157, 15, 71, 84]
    assert round(measurement.ray_gap_contrast, 5) == 0.01756
    assert round(measurement.local_noise_sigma, 5) == 0.00362
    assert round(measurement.snr, 2) == 4.86
    assert round(measurement.haze_saturation_ratio, 2) == 0.76
    assert measurement.passes_visibility_gate


def test_txn10_counterexamples_fail_visibility_gate_when_available() -> None:
    available = [path for path in TXN10_COUNTEREXAMPLES if path.exists()]
    assert available, "TXN-10 counterexample frames are missing from the local evidence corpus"
    for path in available:
        measurement = measure_veil_visibility(path)
        assert not measurement.passes_visibility_gate, (
            path,
            measurement,
        )
