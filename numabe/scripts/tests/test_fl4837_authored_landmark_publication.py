"""FL-4837: authored map content must reach the running game, and the face
budget that wiring it consumes must REFUSE rather than warn.

These two are tested together on purpose. Publishing the full manifest moves
faces into the fixed bank of a 16-bit domain (FL4789_VSB_STATIC_FACE_DOMAIN_MAX
= 0xFFFF) and takes the same number out of the WorldPartition tree budget. A
generic appender loop shipped with a warn-only budget is a regression generator.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "godot_project/scripts/World.gd"
LANDMARKS_GD = ROOT / "godot_project/scripts/world/AuthoredLandmarks.gd"
MANIFEST = ROOT / "godot_project/assets/world_mesh/authored_landmarks.json"
MESH_DIR = ROOT / "godot_project/assets/world_mesh"

# The four placements World used to publish BY NAME, and the two it did not.
# `_append_static_world_rock_field` keeps a historical name but publishes the
# pieta statue: it reads `_landmark_mesh("pieta_statue")`. FL-4837 and FL-4839
# SPEC PART 2 both read that name as a rock field and therefore counted 10922
# pieta faces as newly added, quoting an 11555-face cost. The real cost of
# wiring the loop is castle_interior + castle_bridge only. The former
# castle_massif was a fake cone mesh over real A3D terrain and is intentionally
# no longer an authored landmark.
PREVIOUSLY_PUBLISHED = ("lighthouse", "pieta_statue", "female_hand", "cyan_pedestal")
PREVIOUSLY_VIEWER_ONLY = ("castle_interior", "castle_bridge")
VSB_STATIC_FACE_DOMAIN_MAX = 0xFFFF


def _placements() -> dict:
    return json.loads(MANIFEST.read_text())["placements"]


def _face_cost(landmark_id: str) -> int:
    entry = _placements()[landmark_id]
    if "boxes" in entry:
        return len(entry["boxes"]) * 6   # _append_static_world_box emits six
    doc = json.loads((ROOT / entry["mesh"].replace("assets/", "godot_project/assets/", 1)).read_text())
    return int(doc["face_count"])


def test_manifest_still_holds_the_six_placements_this_entry_measured() -> None:
    assert set(_placements()) == set(PREVIOUSLY_PUBLISHED) | set(PREVIOUSLY_VIEWER_ONLY)


def test_the_real_measured_cost_of_wiring_the_loop_is_the_two_castle_pieces() -> None:
    previously = sum(_face_cost(i) for i in PREVIOUSLY_PUBLISHED)
    newly = sum(_face_cost(i) for i in PREVIOUSLY_VIEWER_ONLY)
    assert previously == 19472, previously
    assert newly == 489, newly
    assert previously + newly == 19961
    # The corrected number, stated so a future reader cannot re-derive 11555.
    assert newly != 11555
    # And the wired configuration still fits the domain with room for trees.
    assert previously + newly < VSB_STATIC_FACE_DOMAIN_MAX


def test_world_enumerates_the_manifest_instead_of_naming_four_appenders() -> None:
    source = WORLD.read_text()
    publication = source.split("func _publish_partitioned_static_world_mesh(", 1)[1]
    publication = publication.split("\nfunc ", 1)[0]
    # The publication path must not call any landmark appender by name any more.
    for named in ("_append_static_world_rock_field(",
                  "_append_static_world_cyan_pedestal(",
                  "_append_static_world_female_hand(",
                  "_append_static_world_lighthouse("):
        assert named not in publication, named
    assert "_append_authored_landmarks(" in publication
    # The map gate reads the manifest's own declaration rather than a literal.
    assert 'catalog_map == String(AuthoredLandmarksScript.map_id())' in publication
    assert 'catalog_map == "game_map_y8"' not in publication


def test_the_loop_iterates_ids_and_has_a_generic_worker_for_a_new_placement() -> None:
    source = WORLD.read_text()
    loop = source.split("func _append_authored_landmarks(", 1)[1].split("\nfunc ", 1)[0]
    assert "AuthoredLandmarksScript.ids()" in loop
    # A manifest id with no special case must still publish.
    assert "_append_static_world_landmark_mesh(" in loop
    assert "func _append_static_world_landmark_mesh(" in source


def test_the_budget_gate_refuses_and_prints_the_arithmetic() -> None:
    source = WORLD.read_text()
    loop = source.split("func _append_authored_landmarks(", 1)[1].split("\nfunc ", 1)[0]
    assert "FL4837_LANDMARK_BUDGET_REFUSED" in loop
    assert "action=REFUSED_NOT_WARNED" in loop
    # Refuse, not warn: the over-domain branch must push_error and return a
    # failure the caller turns into a refused publication.
    gate = loop.split("planned_total > FL4789_VSB_STATIC_FACE_DOMAIN_MAX", 1)[1]
    gate = gate.split("# Pass 3", 1)[0]
    assert "push_error(" in gate
    assert "push_warning(" not in gate
    assert '"ok": false' in gate
    for number in ("domain=%d", "planned_fixed_faces=%d", "over_by=%d",
                   "oversubscription=%.4fx", "breakdown=%s"):
        assert number in gate, number
    # The caller must treat a refused plan as a failed publication.
    publication = source.split("func _publish_partitioned_static_world_mesh(", 1)[1]
    publication = publication.split("\nfunc ", 1)[0]
    refusal = publication.split("_append_authored_landmarks(", 1)[1]
    assert 'landmark_publication.get("ok", false)' in refusal
    assert "return false" in refusal


def test_an_uncostable_placement_is_a_refusal_not_a_free_placement() -> None:
    source = LANDMARKS_GD.read_text()
    assert "static func planned_face_count(" in source
    plan = source.split("static func planned_face_count(", 1)[1].split("\nstatic func ", 1)[0]
    # Every failure path returns -1, never 0. A placement whose cost cannot be
    # established must not be admitted as costing nothing.
    assert plan.count("return -1") == 3
    assert "static func map_id(" in source


def test_the_generic_receipt_names_every_published_placement() -> None:
    # The five legacy candidate keys cannot describe a newly authored
    # placement. `authored_landmark_faces` can, which is what makes an
    # authoring tool's output observable in a runtime receipt.
    source = WORLD.read_text()
    assert '"authored_landmark_faces": authored_landmark_faces.duplicate(true)' in source
    assert "FL4837_AUTHORED_LANDMARKS_PUBLISHED" in source
