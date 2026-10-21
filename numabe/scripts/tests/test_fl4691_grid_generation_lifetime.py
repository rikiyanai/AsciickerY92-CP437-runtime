"""Structural guards for the production grid-generation lifetime suite.

Behavior belongs to the GDScript suite that calls PerspectiveProbePipeline
directly. This file deliberately contains no translated/executed GDScript
body and no parallel scheduler model.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "godot_project/render/backends/perspective/PerspectiveProbeBackend.gd"
SPLAT = ROOT / "godot_project/render/backends/perspective/splat/perspective_probe_splat.glsl"
CPU_SUITE = ROOT / "godot_project/scripts/tests/test_fl4691_grid_generation_lifetime_cpu.gd"


def test_lifetime_behavior_uses_production_gdscript_interfaces() -> None:
    cpu = CPU_SUITE.read_text(encoding="utf-8")
    assert '"_schedule_generation_paint"' in cpu
    assert '"_scheduled_capture_faces"' in cpu
    forbidden_translation = "exec" + "(compile("
    assert forbidden_translation not in Path(__file__).read_text(encoding="utf-8")


def test_grid_content_key_excludes_live_eye_and_dynamic_owners() -> None:
    source = PIPELINE.read_text(encoding="utf-8")
    body = source.split("func _grid_capture_content_key()", 1)[1].split(
        "\nfunc ", 1)[0]
    for key in ("static_draw_packet_key", "terrain_revision", "probe_near", "probe_far"):
        assert f'"{key}"' in body
    for key in (
        "eye_origin", "frame_index", "scene_revision", "dynamic_revision",
        "water_revision", "colour_frame_time_seconds",
    ):
        assert f'"{key}"' not in body
    assert "range(65, 75)" in body
    paint = source.split("func _schedule_generation_paint(", 1)[1].split(
        "\nfunc ", 1)[0]
    for key in ("dynamic_revision", "actor_revision", "water_revision"):
        assert f'"{key}"' not in paint


def test_transition_partition_uses_the_actual_clipped_raster() -> None:
    shader = SPLAT.read_text(encoding="utf-8")
    assert "float threshold = bayer4(cell);" in shader
    assert "pixel / max(splat.glyph_grid.xy, uvec2(1u))" in shader
    source = PIPELINE.read_text(encoding="utf-8")
    body = source.split("func _splat_param_bytes()", 1)[1].split("\nfunc ", 1)[0]
    assert "_encode_uvec4(bytes, 192, _cell_pixel_size.x," in body
    assert "_cell_pixel_size.y, _splat_extent.x, _splat_extent.y)" in body
    assert "_splat_extent.y /" not in body
