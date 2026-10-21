"""FL-4359 — rain and snow as glyph-only ink with an UNOWNED background.

Operator requirement (2026-09-09): "RAIN: THEY NEED TO BE BLUE/DARK GLYPHS, NOT
CARDS. SAME WITH SNOW."  The consolidated visual contract
(docs/plans/GODOT_GOAL.MD, anchor visual-contract-2026-09-07, family table)
states: rain and falling snow are FG flat with BG **Unowned**, and its falsifier
is "Drops/flakes erase the scene with rectangles".

These are SOURCE-OWNERSHIP and SHADER-COMPILE contracts.  They prove the law is
written into the four stages that own it and that every touched variant still
compiles.  They are NOT a rendered-rain acceptance verdict: no cell colour, no
frame, and no visual claim is established here.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "godot_project"
SHADERS = PROJECT
PRODUCT = SHADERS / "render/product/cell_owner_product.glsl"
PUBLISH = SHADERS / "render/backends/perspective/publish/perspective_probe_payload_publish.glsl"
COMPOSITOR = SHADERS / "render/ascii/raster/ascii_compositor.glsl"
BUILDER = PROJECT / "scripts/world/weather/RainPresentationBuilder.gd"
HOST = PROJECT / "render/api/ASCIICompositorEffect.gd"
CPP_WEATHER = REPO / "engine/weather.cpp"

# The two ink-only materials of the visual contract's "BG Unowned" families.
# 205 is the retained weather ink (rain drop, splash, snow flake) and the gun
# tracer; 255 is STATIC_WORLD_MEADOW_BLADE_MAT, the terrain foliage blade.
INK_ONLY_MATERIALS = ("205u", "255u")


def weather_branch() -> str:
    """The retained-weather arm of vsb_product_finalize_record."""
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    start = source.index("// FL-4359: weather geometry was elected by the same capture depth")
    end = source.index("return product_present_surface(", start)
    return source[start:end]


# ---------------------------------------------------------------------------
# 1. The ink itself: dark blue rain, C++ snow, and no authored paper.
# ---------------------------------------------------------------------------


def test_weather_ink_never_authors_its_own_paper() -> None:
    branch = weather_branch()
    # The dark rectangle the operator photographed was authored right here.
    assert "record.bg.rgb = record.fg.rgb * 0.22;" not in branch
    assert "UNOWNED BG" in branch
    assert "vec3 unowned_paper = record.bg.rgb;" in branch
    assert "record.bg = vec4(unowned_paper, 0.0);" in branch


def test_rain_ink_is_dark_blue_and_snow_follows_the_cpp_palette() -> None:
    branch = weather_branch()
    assert "vec3(0.20, 0.30, 0.55)" in branch, "rain ink must read dark blue"
    # C++ engine/weather.cpp gives a flake white (255,255,255) or light blue
    # (200,220,255); the same stable source hash that picks the fall-speed
    # family picks between them, so a flake never flickers.
    assert "vec3(200.0, 220.0, 255.0) / 255.0" in branch
    assert "(snow_variant & 1u)" in branch
    assert "product_apply_scene_land_daynight_pair(night" in branch


def test_snow_glyphs_match_the_cpp_snow_family() -> None:
    branch = weather_branch()
    assert "uint snow_glyphs[4] = uint[4](42u, 43u, 46u, 44u);" in branch
    assert "(splash ? 111u : 124u)" in branch, "drop keeps '|', ripple keeps 'o'"
    if CPP_WEATHER.exists():
        cpp = CPP_WEATHER.read_text(encoding="utf-8", errors="replace")
        assert "snow_glyphs[4] = { 0x2A, 0x2B, 0x2E, 0x2C }" in cpp


# ---------------------------------------------------------------------------
# 2. Perspective: the publish elects this cell's paper from a non-ink source.
# ---------------------------------------------------------------------------


def test_publish_owns_one_ink_only_predicate_covering_weather_and_foliage() -> None:
    publish = PUBLISH.read_text(encoding="utf-8")
    predicate = (PUBLISH.parent / "render_source_contract.glslinc").read_text().split("bool source_material_owns_paper(uint material)", 1)[1].split("}", 1)[0]
    for material in INK_ONLY_MATERIALS:
        assert f"material != {material}" in predicate


def test_publish_elects_paper_from_a_visible_non_ink_sample() -> None:
    publish = PUBLISH.read_text(encoding="utf-8")
    election = publish.split("uint paper_background = centre_background;", 1)[1].split(
        "uint glyph = payload.x & 0xfffu;", 1
    )[0]
    # One whole visible source, chosen by distance from the cell centre.
    assert "source_material_owns_paper(candidate.w & 0xffffu)" in publish
    assert "distance_squared < best_paper_distance" in publish
    assert "best_paper_background = candidate.z;" in publish
    # RQ-170: the final selection copies one reduced candidate; it does not
    # average or interpolate competing paper colors.
    assert "paper_background = publish_paper_background[0];" in election
    for forbidden in ("mix(", "+ publish_paper_background[0]", "/ 2"):
        assert forbidden not in election
    assert "record.bg = vec4(unpackUnorm4x8(paper_background).rgb, 1.0);" in publish
    assert "if (valid && paper_elected) head_status |= STATUS_INK_PAPER_ELECTED;" in publish
    assert "(!weather_ink || paper_elected)" in publish


def test_publish_and_compositor_agree_on_the_elected_paper_status_bit() -> None:
    publish = PUBLISH.read_text(encoding="utf-8")
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert "const uint STATUS_INK_PAPER_ELECTED = 1u << 4u;" in publish
    assert (
        "const uint FL4359_RESOLVE_STATUS_INK_PAPER_ELECTED = 1u << 4u;" in compositor
    )
    # Bit 4 must not collide with the existing resolve-status bits 0..3.
    for existing in ("VALID = 1u << 0u", "BOUNDARY = 1u << 1u",
                     "TRANSITION_BLEND = 1u << 2u", "PAPER_IS_TEXEL = 1u << 3u"):
        assert existing in compositor


def test_compositor_reveals_the_elected_paper_under_an_ink_only_pixel() -> None:
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    predicate = (COMPOSITOR.parent / "render_source_contract.glslinc").read_text().split("bool source_material_owns_paper(uint material)", 1)[1].split("}", 1)[0]
    for material in INK_ONLY_MATERIALS:
        assert f"material != {material}" in predicate
    paper = compositor.split("// A missing glyph must remain diagnostic", 1)[1].split(
        "float intensity =", 1
    )[0]
    assert "fl4359_pixel_is_ink_only && fl4359_elected_paper_valid" in paper
    assert "? fl4359_elected_paper" in paper
    # Every non-ink pixel still paints its own depth-winning splat paper.
    assert ": unpackUnorm4x8(visible_payload.z).rgb)" in paper
    assert (
        "fl4359_elected_paper_valid = (resolve_record.head.x" in compositor
        and "& FL4359_RESOLVE_STATUS_INK_PAPER_ELECTED) != 0u;" in compositor
    )


def test_perspective_bg_uses_per_pixel_depth_winning_transport_in_both_modes() -> None:
    """Perspective BG (RQ-170 September 9 amendment): every native pixel
    transports its own depth-winning splat paper. Flat BG denotes discrete
    source-texel paint, never one cell-centre colour replicated over the cell.
    The superseded whole-cell expansion (elected paper over the entire cell in
    flat-BG mode) must not return: no presentation-mode branch may assign the
    elected paper to unrelated texel footprints."""
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    paper = compositor.split("// A missing glyph must remain diagnostic", 1)[1].split(
        "float intensity =", 1
    )[0]
    # One paper assignment, fed by the per-pixel splat with the elected-paper
    # fallback for ink-only pixels only.
    assert paper.count("resolved.bg =") == 1
    assert "unpackUnorm4x8(visible_payload.z).rgb" in paper
    assert "fl4359_pixel_is_ink_only && fl4359_elected_paper_valid" in paper
    assert "? fl4359_elected_paper" in paper
    # No whole-cell replication of the elected paper in any mode branch.
    assert "if ((presentation_mode & 1u) == 0u)" not in paper
    assert "fl4359_resolve_admitted ? fl4359_elected_paper : vec3(0.0)" not in paper


# ---------------------------------------------------------------------------
# 3. Ortho ("P" key) parity: the same law over the cell's four quarters.
# ---------------------------------------------------------------------------


def test_ortho_product_path_applies_the_same_unowned_bg_law() -> None:
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    ortho = compositor.split("if (!fl4359_perspective_view) {", 1)[1].split(
        "// Glyph identity is discrete.", 1
    )[0]
    assert "!source_material_owns_paper(centre.r1.x)" in ortho
    assert "!source_material_owns_paper(source.r1.x)" in ortho
    assert "final_product_status_is_valid(source.r0.x)" in ortho
    assert "resolved.bg = ortho_paper;" in ortho
    # One whole quarter supplies the paper; nothing is blended across quarters.
    assert "mix(" not in ortho


# ---------------------------------------------------------------------------
# 4. Geometry: a weather mark is never wider than the character it paints.
# ---------------------------------------------------------------------------


def test_weather_particles_are_never_wider_than_one_character() -> None:
    host = HOST.read_text(encoding="utf-8")
    character = re.search(
        r"const FL4359_EFFECT_CHARACTER_WORLD_SIZE := Vector2\(([\d.]+), ([\d.]+)\)",
        host,
    )
    assert character, "the measured effect character size is the shared width owner"
    half_width = float(character.group(1)) / 2.0
    half_height = float(character.group(2)) / 2.0

    builder = BUILDER.read_text(encoding="utf-8")
    declared_width = re.search(r"const INK_HALF_WIDTH := ([\d.]+)", builder)
    declared_height = re.search(r"const INK_HALF_HEIGHT := ([\d.]+)", builder)
    assert declared_width and declared_height
    assert abs(float(declared_width.group(1)) - half_width) < 1e-6
    assert abs(float(declared_height.group(1)) - half_height) < 1e-6

    # Every published half-width is the character half-width, not a wider body.
    assert "var radius: float = INK_HALF_WIDTH" in builder
    assert re.search(r"radius = INK_HALF_WIDTH\n\s+half_length = INK_HALF_HEIGHT", builder)
    assert "0.14" not in builder.split("var radius", 1)[1].split("particles.append", 1)[0]

    # The sole geometry owner refuses a wider mark even if a producer changes.
    rain_face = host.split("func _append_dynamic_rain(", 1)[1].split("\nfunc ", 1)[0]
    assert (
        'var half_width: float = minf(float(fact["radius"]),\n'
        "\t\tFL4359_EFFECT_CHARACTER_WORLD_SIZE.x * 0.5)" in rain_face
    )
    # Falling marks retain the crossed edge-on pair. Impact uses one bounded
    # loop of ground-ring segments rather than crossed splash strips.
    assert rain_face.count("_append_dynamic_mover_face(") == 2
    assert "for axis in [Vector3.RIGHT, Vector3.BACK]:" in rain_face
    assert 'var segment_count := clampi(int(fact.get("ripple_segments", 8)), 3, 12)' in rain_face
    assert "for segment in range(segment_count):" in rain_face
    assert "var radial := Vector3(cos(angle), 0.0, sin(angle))" in rain_face


# ---------------------------------------------------------------------------
# 5. Every touched shader variant still compiles.
# ---------------------------------------------------------------------------

VARIANTS = {
    "cell_owner_product.glsl": [
        [],
        ["FL4359_TEXEL_SHADE"],
        ["FL4359_SOURCE_EFFECTS"],
        ["FL4359_PRODUCT_IDENTITY_CAPTURE"],
    ],
    "ascii_compositor.glsl": [[], ["FL4431_FINAL_CELL_CAPTURE"]],
    "perspective_probe_payload_publish.glsl": [[]],
}
STAGE_SUFFIX = {"vertex": "vert", "fragment": "frag", "compute": "comp"}


def _expand_includes(text: str, stack: tuple[Path, ...] = ()) -> str:
    def include(match: re.Match[str]) -> str:
        path = PROJECT / match.group(1)
        assert path not in stack, f"cyclic shader include: {path}"
        return _expand_includes(path.read_text(encoding="utf-8"), (*stack, path))

    return re.sub(r'^\s*#include\s+"res://([^"]+)"\s*$', include, text, flags=re.MULTILINE)


def _stages(source: str) -> dict[str, str]:
    stages: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in source.splitlines():
        marker = re.fullmatch(r"\s*#\[(compute|vertex|fragment)\]\s*", line)
        if marker:
            if current is not None:
                stages[current] = "\n".join(lines)
            current = marker.group(1)
            lines = []
        else:
            lines.append(line)
    if current is not None:
        stages[current] = "\n".join(lines)
    return stages


@pytest.mark.parametrize(
    "shader,defines",
    [(name, tuple(defines)) for name, variants in VARIANTS.items() for defines in variants],
)
def test_touched_shader_variants_compile(shader: str, defines: tuple[str, ...], tmp_path: Path) -> None:
    compiler = shutil.which("glslangValidator") or shutil.which("glslang")
    if not compiler:
        pytest.skip("glslangValidator/glslang is required")
    # Godot expands res:// includes and injects the -D defines the host passes
    # in ASCIICompositorEffect._compile_compute; feed glslang the same sources.
    shader_path = {"cell_owner_product.glsl": PRODUCT, "ascii_compositor.glsl": COMPOSITOR,
                   "perspective_probe_payload_publish.glsl": PUBLISH}[shader]
    for stage, body in _stages(_expand_includes(shader_path.read_text(encoding="utf-8"))).items():
        suffix = STAGE_SUFFIX[stage]
        path = tmp_path / f"{shader}.{'_'.join(defines) or 'base'}.{suffix}"
        path.write_text(body, encoding="utf-8")
        command = [compiler, "--target-env", "vulkan1.2", "-S", suffix]
        command += [f"-D{define}=1" for define in defines]
        command += ["-o", str(path.with_suffix(f".{suffix}.spv")), str(path)]
        result = subprocess.run(command, capture_output=True, text=True, errors="replace")
        assert result.returncode == 0, result.stdout + result.stderr
