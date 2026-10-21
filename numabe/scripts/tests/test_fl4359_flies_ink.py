"""FL-4359 — circling flies as glyph-only ink with an UNOWNED background.

Operator requirement (2026-09-09): "need 'flies' tiny . or , or ' or " or * or v
glyphs circleing".  The consolidated visual contract
(docs/plans/GODOT_GOAL.MD, anchor visual-contract-2026-09-07, family table)
states that an ink family is FG flat with BG **Unowned**, and its falsifier is
"marks erase the scene with rectangles".

These are SOURCE-OWNERSHIP and SHADER-COMPILE contracts, the sibling of
test_fl4359_rain_snow_unowned_bg.py.  They prove that the insect swarm joined
the EXISTING ink-only law rather than opening a second one, and that every
touched variant still compiles.  They are NOT a rendered-swarm acceptance
verdict: no cell colour, no frame, and no visual claim is established here.
The runtime circling/determinism laws belong to the headless GD test
godot_project/scripts/tests/test_fl4359_flies_presentation.gd.
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
BUILDER = PROJECT / "scripts/world/weather/FliesPresentationBuilder.gd"
RAIN_BUILDER = PROJECT / "scripts/world/weather/RainPresentationBuilder.gd"
HOST = PROJECT / "render/api/ASCIICompositorEffect.gd"
WORLD = PROJECT / "scripts/World.gd"

# The ink-only material a fly shares with every other weather mark.  Flies
# deliberately add NO new material: the "BG Unowned" families stay exactly
# 205 (retained weather ink + gun tracer) and 255 (the meadow blade), so the
# single predicate in each stage keeps covering them all.
INK_ONLY_MATERIALS = ("205u", "255u")
FLY_MATERIAL = "205u"
# The operator's alphabet as codepoints, in the order both owners list it.
FLY_GLYPH_CODEPOINTS = (46, 44, 39, 34, 42, 118)  # . , ' " * v
# 11 drop, 12 splash, 13 flake, 14 FLY.
FLY_EFFECT_KIND = "14u"


def weather_branch() -> str:
    """The retained-weather arm of vsb_product_finalize_record."""
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    start = source.index("// FL-4359: weather geometry was elected by the same capture depth")
    end = source.index("return product_present_surface(", start)
    return source[start:end]


def flies_face_builder() -> str:
    host = HOST.read_text(encoding="utf-8")
    return host.split("func _append_dynamic_flies(", 1)[1].split("\nfunc ", 1)[0]


# ---------------------------------------------------------------------------
# 1. ONE ink-only predicate still covers every ink family, flies included.
# ---------------------------------------------------------------------------


def test_publish_ink_only_predicate_covers_the_fly_material() -> None:
    publish = PUBLISH.read_text(encoding="utf-8")
    assert '#include "res://render/core/contracts/source/render_source_contract.glslinc"' in publish
    assert "bool payload_is_ink_only" not in publish
    predicate = (PUBLISH.parent / "render_source_contract.glslinc").read_text().split("bool source_material_owns_paper(uint material)", 1)[1].split("}", 1)[0]
    for material in INK_ONLY_MATERIALS:
        assert f"material != {material}" in predicate
    assert f"material != {FLY_MATERIAL}" in predicate, "a fly is ink-only"
    # No second predicate was opened for the insect family.
    assert "fly" not in predicate


def test_compositor_ink_only_predicate_covers_the_fly_material() -> None:
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert '#include "res://render/core/contracts/source/render_source_contract.glslinc"' in compositor
    assert "bool fl4359_material_is_ink_only" not in compositor
    predicate = (COMPOSITOR.parent / "render_source_contract.glslinc").read_text().split("bool source_material_owns_paper(uint material)", 1)[1].split("}", 1)[0]
    for material in INK_ONLY_MATERIALS:
        assert f"material != {material}" in predicate
    assert f"material != {FLY_MATERIAL}" in predicate
    assert "fly" not in predicate


def test_flies_add_no_new_ink_material() -> None:
    """The builder publishes the shared weather ink id, never a new one."""
    builder = BUILDER.read_text(encoding="utf-8")
    assert "const FLY_MAT_ID := 205" in builder
    assert '"mat_id": FLY_MAT_ID' in builder


# ---------------------------------------------------------------------------
# 2. The weather branch separates a fly from a drop with the field it already
#    reads, and never authors the fly's paper.
# ---------------------------------------------------------------------------


def test_weather_branch_separates_flies_by_the_existing_effect_kind() -> None:
    branch = weather_branch()
    assert (
        f"bool fly = input_value.retained_face.actor_effect_kind == {FLY_EFFECT_KIND};"
        in branch
    ), "flies reuse the effect-kind field the branch already reads"
    # The drop/splash/flake arms are untouched.
    assert "bool splash = input_value.retained_face.actor_effect_kind == 12u;" in branch
    assert "bool snow = input_value.retained_face.actor_effect_kind == 13u;" in branch


def test_weather_branch_paints_the_operator_glyph_alphabet() -> None:
    branch = weather_branch()
    table = ", ".join(f"{code}u" for code in FLY_GLYPH_CODEPOINTS)
    assert f"uint fly_glyphs[6] = uint[6]({table});" in branch
    # The glyph is READ BACK from the producer's identity, never re-hashed here,
    # so a fly cannot flicker between shapes frame to frame.
    assert (
        "uint fly_variant = min(input_value.retained_face.source_id & 7u, 5u);" in branch
    )
    assert "record.r0.z = fly\n\t\t\t? fly_glyphs[fly_variant]" in branch
    # Rain and snow keep their own glyphs.
    assert "(snow ? snow_glyphs[snow_variant] : (splash ? 111u : 124u));" in branch


def test_fly_ink_is_a_dark_warm_silhouette_and_owns_no_paper() -> None:
    branch = weather_branch()
    assert "vec3 fly_ink = vec3(0.08, 0.07, 0.05);" in branch
    assert "record.fg.rgb = fly\n\t\t\t? fly_ink * strength" in branch
    # The UNOWNED-BG law the rain lane established still holds for the whole
    # branch: nothing here writes a fly's own paper rectangle.
    assert "record.bg.rgb = record.fg.rgb * 0.22;" not in branch
    assert "UNOWNED BG" in branch
    assert "record.bg.rgb = fly" not in branch


def test_the_two_glyph_owners_list_the_same_alphabet() -> None:
    """The builder chooses the glyph; the shader reads its index back. Both
    tables are pinned together so neither side can be reordered alone."""
    builder = BUILDER.read_text(encoding="utf-8")
    listed = re.search(r"const FLY_GLYPHS := \[([0-9, ]+)\]", builder)
    assert listed, "the builder owns the alphabet"
    codes = tuple(int(part) for part in listed.group(1).split(","))
    assert codes == FLY_GLYPH_CODEPOINTS
    branch = weather_branch()
    shader_table = re.search(r"uint fly_glyphs\[6\] = uint\[6\]\(([0-9u, ]+)\);", branch)
    assert shader_table
    shader_codes = tuple(
        int(part.strip().rstrip("u")) for part in shader_table.group(1).split(",")
    )
    assert shader_codes == codes, "one alphabet, listed identically in both owners"


def test_the_glyph_index_rides_the_low_three_bits_of_the_source_id() -> None:
    builder = BUILDER.read_text(encoding="utf-8")
    encoder = builder.split("static func _fly_source_id(", 1)[1].split("\n\n", 1)[0]
    assert "(glyph_index & 7)" in encoder
    assert "((index & 7) << 3)" in encoder
    assert "(tile_bits << 6)" in encoder


# ---------------------------------------------------------------------------
# 3. Geometry: a fly is never wider or taller than one character.
# ---------------------------------------------------------------------------


def test_a_fly_is_never_larger_than_one_character() -> None:
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
    # The insect lane declares the SAME character halves the rain lane does.
    rain = RAIN_BUILDER.read_text(encoding="utf-8")
    for name in ("INK_HALF_WIDTH", "INK_HALF_HEIGHT"):
        rain_value = re.search(rf"const {name} := ([\d.]+)", rain)
        fly_value = re.search(rf"const {name} := ([\d.]+)", builder)
        assert rain_value and fly_value
        assert rain_value.group(1) == fly_value.group(1)
    assert '"radius": INK_HALF_WIDTH' in builder
    assert '"half_length": INK_HALF_HEIGHT' in builder

    # The sole geometry owner refuses a wider or taller mark even if a producer
    # changes: BOTH extents are clamped to half a character.
    face = flies_face_builder()
    assert (
        'var half_width: float = minf(float(fact["radius"]),\n'
        "\t\tFL4359_EFFECT_CHARACTER_WORLD_SIZE.x * 0.5)" in face
    )
    assert (
        'var half_height: float = minf(float(fact["half_length"]),\n'
        "\t\tFL4359_EFFECT_CHARACTER_WORLD_SIZE.y * 0.5)" in face
    )
    # FL-4667: the crossed pair is the edge-on visibility guarantee. Two faces
    # per fly, no card, no third quad.
    assert face.count("_append_dynamic_mover_face(") == 1
    assert "for axis in [Vector3.RIGHT, Vector3.BACK]:" in face


# ---------------------------------------------------------------------------
# 4. Producer wiring: one anchor owner, one geometry owner, bounded cost.
# ---------------------------------------------------------------------------


def test_world_publishes_flies_every_frame_and_counts_their_faces() -> None:
    world = WORLD.read_text(encoding="utf-8")
    assert 'preload("res://scripts/world/weather/FliesPresentationBuilder.gd").new()' in world
    assert "func _append_flies_source_facts(facts: Array) -> int:" in world
    assert "var flies_faces := _append_flies_source_facts(dynamic_source_facts)" in world
    assert "var dynamic_face_count := weapon_faces + rain_faces + flies_faces + " in world
    assert "flies_faces + bird_faces" in world
    # The swarm reuses the retained ground query, never a private terrain read.
    producer = world.split("func _append_flies_source_facts(", 1)[1].split("\n\n", 1)[0]
    assert "_aircraft_ground_effect_surface_fact" in producer
    assert "presentation_time_seconds()" in producer


def test_the_compositor_routes_fly_particles_to_the_fly_geometry_owner() -> None:
    host = HOST.read_text(encoding="utf-8")
    case = host.split('\t\t\t"rain":\n', 1)[1].split('\t\t\t"projectile":', 1)[0]
    assert 'if bool(particle.get("fly", false)):' in case
    assert "_append_dynamic_flies(face_rows, particle)" in case
    assert "_append_dynamic_rain(face_rows, particle)" in case


def test_the_swarm_lane_is_deterministic_and_cost_bounded() -> None:
    builder = BUILDER.read_text(encoding="utf-8")
    # No per-frame randomness and no wall clock: an anchor is a world fact.
    # Comment lines are stripped first, so the prose may name what it forbids.
    code = "\n".join(
        line for line in builder.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in ("randf", "randi", "randomize", "Time.", "OS.", "Engine."):
        assert forbidden not in code
    resident = re.search(r"const RESIDENT_RADIUS := (\d+)", builder)
    tile = re.search(r"const SWARM_TILE_SIZE := ([\d.]+)", builder)
    swarms = re.search(r"const MAX_SWARMS := (\d+)", builder)
    flies = re.search(r"const MAX_FLIES := (\d+)", builder)
    assert resident and tile and swarms and flies
    # Bounded to the operator's 40-unit radius around the local player.
    assert int(resident.group(1)) * float(tile.group(1)) <= 40.0
    # Worst case per frame, two faces per fly.
    assert int(swarms.group(1)) * int(flies.group(1)) <= 84
    assert "const MAX_FLIES_TOTAL := MAX_SWARMS * MAX_FLIES" in builder
    # Fly counts and geometry stay inside the operator's authored bands.
    assert "const MIN_FLIES := 3" in builder
    assert "const MAX_FLIES := 7" in builder
    assert "const MIN_RADIUS := 0.35" in builder
    assert "const MAX_RADIUS := 0.8" in builder
    assert "const MIN_HEIGHT := 0.4" in builder
    assert "const MAX_HEIGHT := 1.2" in builder


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
