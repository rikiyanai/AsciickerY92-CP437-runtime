"""Ortho ink-transparency contracts for grass blades and rain (FL-4359).

Screenshot evidence showed meadow blade cells and rain streak cells painting
their compiled paper as opaque rectangles in ortho, plus rain falling through
tree canopies. These source contracts pin the three repairs:

1. The FL-4740 mask-win paint must not spread an ink-only winner's paper
   (meadow 255, weather 205) over all four quarters. Quarters keep their own
   paper so blades and drops read as ink strokes over the scene; the winner
   still owns the ink and the mask.
2. Meadow-space quarters never compete for the mask, and a blank meadow
   center never seeds it (FL-4216 blank rule would collapse either to 0xDB).
3. Rain lands on tree-canopy tops via the RainCanopyAdmission surface fact
   (godot_project/scripts/world/weather/RainCanopyAdmission.gd). Aircraft and flies keep the shared terrain/water fact.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SCREEN_ADAPTER = ROOT / "godot_project/render/product/adapters/ortho/product_screen_adapter.glslinc"
CENTER = ROOT / "godot_project/render/product/adapters/ortho/product_center_presentation.glslinc"
COMPOSITOR = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"
OWNER = ROOT / "godot_project/scripts/world/weather/RainCanopyAdmission.gd"
WORLD = ROOT / "godot_project/scripts/World.gd"


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_ink_only_winner_takes_paper_from_the_shared_paper_election():
    # FL-4848: an ink-only winner (blade 255, weather 205) owns no paper. The
    # paper comes from the shared nearest-owner election over every quarter
    # candidate, and Product never rewrites a quarter's own paint.
    adapter = SCREEN_ADAPTER.read_text(encoding="utf-8")
    assert "CELL_CANDIDATE_OWNS_PAPER" in adapter
    assert "fl4848_paper = cell_presentation_paper_merge(fl4848_paper, fl4848_quarter[slot]);" in adapter
    assert "cell_presentation_result(" in adapter
    assert not re.search(r"staged_final_records\[[^\]]+\]\.(fg|bg)\.rgb\s*=", adapter)


def test_meadow_space_never_owns_the_mask():
    adapter = SCREEN_ADAPTER.read_text(encoding="utf-8")
    assert re.search(
        r"visible_records\[slot\]\.material\s*==\s*STATIC_WORLD_MEADOW_BLADE_MAT"
        r"\s*&&\s*product_terrain_blank_glyph\(candidate_glyph\)",
        adapter,
    ), "meadow-space quarters must skip mask candidacy"
    center = CENTER.read_text(encoding="utf-8")
    assert re.search(
        r"compiled_record\.r1\.x\s*==\s*STATIC_WORLD_MEADOW_BLADE_MAT"
        r"\s*&&\s*product_terrain_blank_glyph\(compiled_record\.r0\.z\)",
        center,
    ), "blank meadow center must decline the seed"


def test_ortho_borrow_stays_centre_keyed_per_pinned_contract():
    # test_fl4057 pins centre-keyed borrow ("one whole visible source" per
    # cell). Per-quarter borrow would trade cell uniformity for nothing: in
    # all-ink cells there is no non-ink quarter to borrow either way.
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert "!source_material_owns_paper(centre.r1.x)" in compositor


def test_rain_lands_on_tree_canopies():
    owner = OWNER.read_text(encoding="utf-8")
    assert "func canopy_top_at" in owner
    assert "func surface_fact" in owner
    assert "func append_source_facts" in owner
    # Rain alone uses the canopy-aware fact; aircraft and flies keep sharing
    # the unmodified terrain/water fact.
    world = WORLD.read_text(encoding="utf-8")
    # Delete-first: World keeps no canopy cache or writer methods.
    assert "_rain_canopy_box_cache" not in world
    assert "func _rain_canopy_boxes" not in world
    assert "func _rain_canopy_top_at" not in world
    assert "func _rain_surface_fact" not in world
    # World keeps scene lifecycle/publication integration only.
    assert "_rain_canopy.append_source_facts(" in world
    rain_call = world.split("_rain_canopy.append_source_facts(", 1)[1][:600]
    assert "_aircraft_ground_effect_surface_fact" in rain_call
    assert "_aircraft_ground_effect_surface_fact" in world
    flies_call = world.split("_flies_presentation.append_facts(facts, center,", 1)[1][:400]
    assert "_aircraft_ground_effect_surface_fact" in flies_call
    assert "canopy" not in flies_call


def test_cell_presentation_never_rewrites_quarter_records():
    # FL-4848: the elected mask lives only in the NativePresentationResult
    # buffer. Quarter status, glyph and provenance words are never restamped.
    adapter = SCREEN_ADAPTER.read_text(encoding="utf-8")
    writes = re.findall(
        r"staged_final_records\s*\[[^\]]+\]\s*\."
        r"(r0(?:\.[xyzw])?|r1(?:\.[xyzw])?)\s*=(?!=)",
        adapter,
    )
    assert writes == [], writes
    assert "fl4359_cell_presentation.records[cell_index] = fl4848_result;" in adapter
    assert "final_product_status_pack_cell_mask" not in adapter
