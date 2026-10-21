"""FL-4431 RenderingDevice teardown ownership contract.

This is a source contract only. It proves that the compositor's predelete path
owns and frees the RIDs that were omitted from the r3 teardown report; it does
not claim that a running renderer has zero live allocations.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPOSITOR = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
POSTPROCESS = ROOT / "godot_project/render/post/PostProcessPipeline.gd"
WAKE_FIELD = ROOT / "godot_project/render/effects/water/WakeTrailField.gd"
BLOOD_FIELD = ROOT / "godot_project/render/effects/blood/BloodMarkField.gd"
WATER_FIELD = ROOT / "godot_project/render/effects/water/WaterInteractionFieldBuffer.gd"
COMBAT_FIELD = ROOT / "godot_project/render/effects/combat/CombatEventField.gd"
WATERFALL_FIELD = ROOT / "godot_project/render/effects/water/WaterfallSiteField.gd"
DEBUG_PALETTE_FIELD = (
    ROOT / "godot_project/render/diagnostics/palette/DebugPaletteField.gd"
)
LOCAL_LIGHT_FIELD = ROOT / "godot_project/render/scene/lighting/LocalLightField.gd"
CELESTIAL_FIELD = ROOT / "godot_project/render/scene/lighting/CelestialFrameState.gd"

LIST_RIDS = (
    "_pixel_camera_payload_rd",
)

OWNER_RELEASES = (
    "post_process.release_gpu()",
    "wake_trail.release_gpu()",
    "blood_mark.release_gpu()",
    "water_field.release_gpu()",
    "combat_events.release_gpu()",
    "debug_palette.release_gpu()",
    "local_lights.release_gpu()",
    "celestial.release_gpu()",
    "waterfall_sites.release_gpu()",
)


def _source() -> str:
    return COMPOSITOR.read_text(encoding="utf-8")


def _teardown(source: str) -> str:
    start = source.index("func _notification(what: int) -> void:")
    end = source.index("\nfunc _initialize_compute()", start)
    return source[start:end]


def _rid_list(teardown: str) -> str:
    start = teardown.index("\tfor r in [")
    end = teardown.index("\n\t\tif r.is_valid():", start)
    return teardown[start:end]


def _assert_teardown_contract(source: str) -> None:
    teardown = _teardown(source)
    assert "if what != NOTIFICATION_PREDELETE:" in teardown
    assert "if rd == null:" in teardown

    assert 'var present_shader:' not in source
    # The fused postprocess pipeline owns the remaining final-pass programs,
    # scratch, and trace slots, so predelete reaches them through one release
    # owner instead of direct frees.
    assert teardown.count("post_process.release_gpu()") == 1
    assert "rd.free_rid(tilt_shift_shader)" not in teardown
    assert "tilt_shift_trace_shader" not in teardown
    postprocess = POSTPROCESS.read_text(encoding="utf-8")
    assert postprocess.count("_rd.free_rid(_blur_shader)") == 1
    assert postprocess.count("_rd.free_rid(_trace_shader)") == 1
    # Scratch frees on resize and on release; both are the same owner.
    assert postprocess.count("_rd.free_rid(_scratch_rd)") == 2
    assert "_rd.free_rid(slot)" in postprocess

    assert teardown.count("wake_trail.release_gpu()") == 1
    wake_field = WAKE_FIELD.read_text(encoding="utf-8")
    assert wake_field.count("_rd.free_rid(_trail_rd)") == 1
    assert wake_field.count("_rd.free_rid(_clock_rd)") == 1

    assert teardown.count("blood_mark.release_gpu()") == 1
    assert "_blood_mark_rd" not in source
    blood_field = BLOOD_FIELD.read_text(encoding="utf-8")
    assert blood_field.count("_rd.free_rid(_mark_rd)") == 1

    assert teardown.count("water_field.release_gpu()") == 1
    assert "_water_interaction_field_rd" not in source
    water_field = WATER_FIELD.read_text(encoding="utf-8")
    # The same owner frees on full-update resize and on final teardown.
    assert water_field.count("_rd.free_rid(_field_rd)") == 2

    assert teardown.count("combat_events.release_gpu()") == 1
    assert "_fl4407_combat_event_rd" not in source
    combat_field = COMBAT_FIELD.read_text(encoding="utf-8")
    assert combat_field.count("_rd.free_rid(_event_rd)") == 1

    assert teardown.count("debug_palette.release_gpu()") == 1
    assert "_debug_palette_rd" not in source
    debug_palette_field = DEBUG_PALETTE_FIELD.read_text(encoding="utf-8")
    assert debug_palette_field.count("_rd.free_rid(_rid)") == 1

    assert teardown.count("local_lights.release_gpu()") == 1
    assert "_local_light_control_rd" not in source
    assert "_local_light_record_rd" not in source
    assert "_local_light_glyph_record_rd" not in source
    local_light_field = LOCAL_LIGHT_FIELD.read_text(encoding="utf-8")
    assert local_light_field.count("_rd.free_rid(_control_rd)") == 1
    assert local_light_field.count("_rd.free_rid(_record_rd)") == 1
    assert local_light_field.count("_rd.free_rid(_glyph_rd)") == 1

    assert teardown.count("celestial.release_gpu()") == 1
    assert "_moon_light_control_rd" not in source
    celestial_field = CELESTIAL_FIELD.read_text(encoding="utf-8")
    assert celestial_field.count("_rd.free_rid(_moon_rd)") == 1

    assert teardown.count("waterfall_sites.release_gpu()") == 1
    assert "_waterfall_sites_rd" not in source
    assert "WATERFALL_SITES" not in source
    assert "WATER_FLOW_FIELD_DRIVEN_ROWS" not in source
    waterfall_field = WATERFALL_FIELD.read_text(encoding="utf-8")
    assert waterfall_field.count("_rd.free_rid(_sites_rd)") == 1

    rid_list = _rid_list(teardown)
    for rid in LIST_RIDS:
        assert rid_list.count(rid) == 1
        # These independent compositor RIDs have no second direct free call in
        # teardown. Their predelete owner is the shared list; the payload RID
        # may also be replaced by its same-owner resize path.
        assert teardown.count(f"rd.free_rid({rid})") == 0

    list_guard = teardown.find("if r.is_valid():", teardown.find("\tfor r in ["))
    list_free = teardown.find("rd.free_rid(r)", list_guard)
    assert list_guard >= 0
    assert list_free > list_guard
    assert teardown.count("rd.free_rid(r)") == 1


def test_compositor_predelete_frees_all_r3_omitted_owned_rids() -> None:
    _assert_teardown_contract(_source())


@pytest.mark.parametrize(
    "owner_or_rid", OWNER_RELEASES + LIST_RIDS
)
def test_each_required_teardown_rid_mutation_turns_red(owner_or_rid: str) -> None:
    source = _source()
    if owner_or_rid in OWNER_RELEASES:
        mutated, replacements = source.replace(owner_or_rid, "", 1), 1
    else:
        mutated, replacements = re.subn(
            rf"(?m)^[ \t]*{re.escape(owner_or_rid)},[ \t]*\n",
            "",
            source,
            count=1,
        )
    assert replacements == 1
    with pytest.raises(AssertionError):
        _assert_teardown_contract(mutated)
