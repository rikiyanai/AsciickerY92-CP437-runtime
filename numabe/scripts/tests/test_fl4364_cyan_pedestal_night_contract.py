"""F15 cyan-pedestal night-light contract; headed visual acceptance remains open."""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "godot_project/scripts/World.gd"
EFFECT = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def function(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("\n", start)
    next_function = source.find("\nfunc ", brace + 1)
    if next_function < 0:
        next_function = len(source)
    return source[start:next_function]


def compact(source: str) -> str:
    source = re.sub(r"#[^\n]*", "", source)
    source = source.replace("\\", "")
    return re.sub(r"\s+", "", source)


def test_retained_pedestal_is_exactly_four_closed_boxes_at_p2() -> None:
    source = WORLD.read_text(encoding="utf-8")
    owner = function(source, "func _append_static_world_cyan_pedestal(")
    assert "const STATIC_WORLD_CYAN_PEDESTAL_ANCHOR_XZ := Vector2(290.134, 43.566)" in source
    assert "const STATIC_WORLD_CYAN_PEDESTAL_MAT := 225" in source
    assert "const STATIC_WORLD_CYAN_PEDESTAL_FACE_COUNT := 24" in source
    assert owner.count("_append_static_world_box(") == 4
    assert '"face_count": int(face_rows.size() / 16) - face_count_before' in owner
    assert '"light_anchor": light_anchor' in owner


def test_light_anchor_publishes_only_after_geometry_parity() -> None:
    source = WORLD.read_text(encoding="utf-8")
    install = function(source, "func _install_static_world_mesh(")
    failure = "if cyan_pedestal_faces != STATIC_WORLD_CYAN_PEDESTAL_FACE_COUNT:"
    publish = 'effect.call("set_scene_geometry_db", scene_db, scene_receipt)'
    light = '"set_cyan_pedestal_light_anchor"'
    assert failure in install
    assert "return" in install[install.index(failure) : install.index(publish)]
    assert install.index(publish) < install.index(light)


def test_pedestal_material_is_byte_safe_and_has_one_matlib_column() -> None:
    source = WORLD.read_text(encoding="utf-8")
    assert 0 <= 225 <= 255
    assert "var cyan_pedestal := Color(0.33, 0.35, 0.53)" in source
    assert source.count("[STATIC_WORLD_CYAN_PEDESTAL_MAT, cyan_pedestal]") == 1


def test_night_record_has_exact_identity_color_and_no_shadow_flag() -> None:
    source = EFFECT.read_text(encoding="utf-8")
    owner = function(source, "func _add_cyan_pedestal_light_record(")
    for token in (
        "const LOCAL_LIGHT_KIND_CYAN_PEDESTAL := 6",
        "const CYAN_PEDESTAL_LIGHT_SOURCE_ID := 4364001",
        "const CYAN_PEDESTAL_LIGHT_RADIUS_WORLD := 14.0",
        "const CYAN_PEDESTAL_LIGHT_INTENSITY := 1.65",
        "const CYAN_PEDESTAL_LIGHT_COLOR := Vector3(0.18, 0.92, 1.0)",
    ):
        assert token in source
    assert "if not celestial.night_enabled():" in owner
    assert "if not _cyan_pedestal_light_anchor_set:" in owner
    assert "rec.kind = LOCAL_LIGHT_KIND_CYAN_PEDESTAL" in owner
    assert "rec.source_id = CYAN_PEDESTAL_LIGHT_SOURCE_ID" in owner
    assert "rec.flags = 0" in owner


def test_six_clear_weather_pedestal_sparks_are_fixed_world_glyph_facts() -> None:
    source = EFFECT.read_text(encoding="utf-8")
    owner = function(source, "func _add_cyan_pedestal_light_record(")
    owner_compact = compact(owner)
    offsets_start = source.index(
        "const CYAN_PEDESTAL_SPARK_OFFSETS: Array[Vector3] = ["
    )
    offsets_end = source.index("\n]", offsets_start)
    offsets = source[offsets_start:offsets_end]
    for offset in (
        "Vector3(-0.90, 0.45, 0.15)",
        "Vector3(0.65, 0.85, -0.25)",
        "Vector3(-0.35, 1.35, -0.60)",
        "Vector3(0.30, 1.85, 0.55)",
        "Vector3(0.90, 2.25, 0.10)",
        "Vector3(-0.70, 2.65, 0.45)",
    ):
        assert offset in offsets
    assert offsets.count("Vector3(") == 6

    # The unchanged point-light record is admitted before clear weather gates
    # only the six binding-13 glyph facts.
    assert owner.index("candidates.append(rec)") < owner.index(
        "if weather_kind != WEATHER_KIND_CLEAR:"
    )
    assert "for spark_index in range(CYAN_PEDESTAL_SPARK_OFFSETS.size()):" in owner
    assert (
        "glyph_rec.source_id=CYAN_PEDESTAL_LIGHT_SOURCE_ID+1+spark_index"
        in owner_compact
    )
    assert tuple(4364001 + 1 + index for index in range(6)) == tuple(
        range(4364002, 4364008)
    )
    assert "glyph_rec.kind=LOCAL_LIGHT_KIND_CYAN_PEDESTAL" in owner_compact
    assert "glyph_rec.glyph_id=0xF9ifspark_indexin[1,4]else0xFA" in owner_compact
    assert "glyph_rec.glyph_flags=32" in owner_compact
    assert (
        "glyph_rec.world_pos=_cyan_pedestal_light_anchor+"
        "CYAN_PEDESTAL_SPARK_OFFSETS[spark_index]" in owner_compact
    )
    assert "glyph_rec.color=Vector3(0.90,0.98,1.0)" in owner_compact
    assert "glyph_rec.phase_quantized=0" in owner_compact
    assert "glyph_rec.lifetime_ticks=0" in owner_compact
    assert "glyphs.append(glyph_rec)" in owner_compact
    for forbidden in (
        "time_seconds",
        "frame_index",
        "camera",
        "projection",
        "VSB_PROJECTION_",
    ):
        assert forbidden not in owner_compact


def test_existing_generic_point_scan_consumes_kind_six_without_new_shader_owner() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    shared = product[product.index("VsbProductLocalLightSlotFacts vsb_product_collect_local_light_slot_facts(") :]
    assert "if (kind == 4)" in shared
    assert "|| kind == LOCAL_LIGHT_KIND_DOWNWASH) continue;" in shared
    assert "accumulated += slot_1.rgb * falloff;" in shared
    assert "kind == 6" not in product


def test_firefly_producer_remains_independent_and_after_pedestal() -> None:
    source = EFFECT.read_text(encoding="utf-8")
    build = function(source, "func _build_local_light_records(")
    assert build.index(
        "_add_cyan_pedestal_light_record(candidates, glyph_candidates)"
    ) < build.index(
        "_add_firefly_records(candidates, glyph_candidates, frame_index, time_seconds)"
    )
    fireflies = function(source, "func _add_firefly_records(")
    assert "LOCAL_LIGHT_KIND_CYAN_PEDESTAL" not in fireflies
    assert "LOCAL_LIGHT_KIND_FIREFLY" in fireflies
