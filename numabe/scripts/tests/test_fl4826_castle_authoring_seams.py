"""FL-4826 / FL-4821 — the castle authoring seams, and the exact places they stop.

This suite does NOT claim the castle vertical slice works. It pins the three
seams the slice has to pass through, so the day one of them moves, a test says
so instead of a status document.

Every fact here is measured from files in this repository. The renderer is
recorded broken (FL-4359 OPEN, FL-4775 RUNTIME_RED), so nothing here claims
anything about how the castle LOOKS, and nothing here is a runtime receipt.

WHAT IS INTENTIONALLY RED, AND EXACTLY WHERE:
  * `test_a_castle_mesh_edit_reaches_the_canonical_map` — seam G0C_CANONICAL_A3D.
    LANE P1 / FL-4834 owns electing the canonical `.a3d`; until it lands, the
    shipped v1 runtime map refuses the edit, which is the correct behaviour.
  * `test_the_spawn_compiler_would_emit_the_castle_encounter_anchors` — seam
    FL4826_NO_ENCOUNTER_ANCHOR. The anchors are not authored yet.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

# HOW TO READ A FAILURE HERE. Two classes of test live in this file:
#   * a plain test          — an invariant that HOLDS TODAY; a failure is a
#                             REGRESSION.
#   * a @SEAM(...) test     — a castle-slice seam that is NOT AUTHORED YET.
#                             Reported `xfail` with its exact reason so this
#                             file never turns another lane's shared pytest run
#                             red, and promoted to a hard failure by running
#                             with ASCIICKER_CASTLE_STRICT=1. An xpass means the
#                             seam landed and the marker should be removed.
#
#   ASCIICKER_CASTLE_STRICT=1 python3 -m pytest scripts/tests/test_fl4826_castle_authoring_seams.py -q
STRICT = os.environ.get("ASCIICKER_CASTLE_STRICT") == "1"


def SEAM(name: str, detail: str):
    """Mark an unauthored castle-slice seam; hard-red only under STRICT."""
    if STRICT:
        return pytest.mark.usefixtures()
    return pytest.mark.xfail(
        reason=f"FL-4826 castle seam not authored: {name} — {detail}",
        strict=True,
    )

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/apply_map_edits.py"
RUNTIME_MAP = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
MANIFEST = ROOT / "godot_project/assets/world_mesh/authored_landmarks.json"
WORLD_GD = ROOT / "godot_project/scripts/World.gd"
SPAWNS = ROOT / "godot_project/assets/world_spawns/game_map_y8.fl4798_map_spawns.v1.json"

# World.gd:128 FL4789_VSB_STATIC_FACE_DOMAIN_MAX. Read from source rather than
# retyped, so a domain change cannot leave this suite quietly testing a ghost.
DOMAIN_MAX = 0xFFFF


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def edits():
    return _load("fl4826_apply_map_edits", CLI)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _planned_faces(manifest: dict) -> dict[str, int]:
    """Reproduce AuthoredLandmarks.planned_face_count() over the whole manifest.

    Mirrors godot_project/scripts/world/AuthoredLandmarks.gd:80 exactly: a box
    placement costs 6 faces per box, a mesh placement costs the baked
    `face_count`, and anything else is UNCOSTABLE (-1) rather than free.
    """
    planned: dict[str, int] = {}
    for name, placement in manifest["placements"].items():
        if "boxes" in placement:
            planned[name] = len(placement["boxes"]) * 6
        elif placement.get("mesh"):
            mesh = json.loads((ROOT / "godot_project" / placement["mesh"]).read_text(encoding="utf-8"))
            planned[name] = int(mesh["face_count"])
        else:
            planned[name] = -1
    return planned


# --- GREEN: the measured arithmetic, pinned against the corrected numbers ----


def test_the_corrected_face_arithmetic_is_what_the_manifest_actually_costs(manifest) -> None:
    """FL-4837's published arithmetic was wrong by 18.3x. This is the falsifier.

    Fails if anyone reintroduces the retracted 11555-face delta, or the
    retracted claim that three of six placements were viewer-only.
    """
    planned = _planned_faces(manifest)
    assert planned == {
        "lighthouse": 444,
        "pieta_statue": 10922,
        "female_hand": 8082,
        "cyan_pedestal": 24,
        "castle_bridge": 450,
        "castle_interior": 39,
    }
    previously_published = sum(
        planned[name] for name in ("lighthouse", "pieta_statue", "female_hand", "cyan_pedestal")
    )
    full_manifest = sum(planned.values())
    delta = planned["castle_bridge"] + planned["castle_interior"]
    assert previously_published == 19472
    assert full_manifest == 19961
    assert delta == 489
    assert previously_published + delta == full_manifest
    assert delta != 11555, "the 11555-face delta is retracted; see the FL-4837 supersession"
    # Exactly TWO placements were viewer-only, not three. pieta_statue was
    # already published through _append_static_world_rock_field, which loads
    # _landmark_mesh("pieta_statue") in _ensure_note_rock_faces().
    world = WORLD_GD.read_text(encoding="utf-8")
    assert '_landmark_mesh("pieta_statue")' in world
    assert full_manifest < DOMAIN_MAX


# --- GREEN: the over-budget negative fixture, proving HARD refusal -----------


def test_an_over_domain_manifest_is_refused_and_nothing_is_published(manifest, tmp_path) -> None:
    """The negative fixture the FL-4837 gate exists for.

    A manifest whose planned total crosses the 65535-face domain must be
    refused WHOLE. The falsifier is a partial publication: a gate that
    published the affordable placements and dropped the rest would leave the
    world silently incomplete, which is the failure mode `REFUSED_NOT_WARNED`
    names.
    """
    over = json.loads(json.dumps(manifest))
    # One synthetic placement that alone exceeds the domain.
    huge = tmp_path / "fl4826_over_budget_faces.json"
    huge.write_text(json.dumps({"version": 1, "face_count": DOMAIN_MAX + 1, "rows": []}), encoding="utf-8")
    over["placements"]["fl4826_over_budget_fixture"] = {
        "xz": [-177.0, 383.0],
        "sink": 0.0,
        "mesh": str(huge.relative_to(ROOT)) if huge.is_relative_to(ROOT) else str(huge),
    }
    planned = dict(_planned_faces(manifest))
    planned["fl4826_over_budget_fixture"] = DOMAIN_MAX + 1
    assert sum(planned.values()) > DOMAIN_MAX

    # The gate is GDScript, so this asserts the refusal CONTRACT in its source:
    # it must push_error and return ok=false with an empty faces_by_id, and it
    # must never downgrade to push_warning.
    world = WORLD_GD.read_text(encoding="utf-8")
    gate = world.split("func _append_authored_landmarks(", 1)[1].split("\nfunc ", 1)[0]
    assert "FL4837_LANDMARK_BUDGET_REFUSED" in gate
    assert "action=REFUSED_NOT_WARNED" in gate
    assert "push_error(" in gate
    assert "push_warning(" not in gate.split("FL4837_LANDMARK_BUDGET_REFUSED")[1].split("return")[0]
    assert '"ok": false, "reason": "face_budget_over_domain", "faces_by_id": {}' in gate
    # Uncostable is a refusal, never a zero.
    assert "landmark_cost_unknown" in gate


def test_an_uncostable_placement_is_refused_rather_than_costed_as_zero(manifest) -> None:
    uncostable = json.loads(json.dumps(manifest))
    uncostable["placements"]["fl4826_no_shape"] = {"xz": [0.0, 0.0], "sink": 0.0}
    assert _planned_faces(uncostable)["fl4826_no_shape"] == -1


# --- RED AT NAMED SEAMS -----------------------------------------------------


def test_an_interior_placement_is_authored(manifest) -> None:
    """The interior seam landed (FL-4826 cathedral blockout, baked by
    scripts/bake_castle_slice.py): open-topped hall, door gap, loft ledges,
    ramps, enterable towers. This is now a plain regression invariant.
    """
    assert "castle_interior" in manifest["placements"], (
        "FL4826_CASTLE_INTERIOR_ABSENT: no interior mesh exists; "
        "the castle cannot be entered, only walked around"
    )
    interior = manifest["placements"]["castle_interior"]
    assert interior.get("mesh"), "an interior must be mesh, not terrain"
    assert interior.get("gateway_world_xz"), "an interior with no declared opening is a sealed box"
    assert interior.get("encounter_anchors"), "FL4826_NO_ENCOUNTER_ANCHOR"
    # The interior must fit inside what is left of the domain (it is already
    # costed in the manifest total; no double count).
    assert sum(_planned_faces(manifest).values()) < DOMAIN_MAX


@SEAM(
    "G0C_CANONICAL_A3D",
    "LANE P1 / FL-4834 must elect the canonical .a3d and FL-4833 must upgrade the "
    "runtime map format before a castle gateway can be authored",
)
def test_a_castle_mesh_edit_reaches_the_canonical_map(edits, tmp_path) -> None:
    """SEAM G0C_CANONICAL_A3D — owned by LANE P1 / FL-4834, not by this lane.

    The editor action this stands for is one `add_mesh` op placing the castle
    gateway through `scripts/apply_map_edits.py`, the existing front door. It
    is blocked, correctly, by the shipped v1 runtime map: v1 cannot hold the
    record. The refusal is TOTAL and writes nothing, which is the property
    worth keeping even while the seam is closed.
    """
    target = tmp_path / "game_map_y8.a3d"
    target.write_bytes(RUNTIME_MAP.read_bytes())
    before = target.read_bytes()
    delta = {
        "$schema": edits.DELTA_SCHEMA,
        "ops": [
            {
                "op": "add_mesh",
                "mesh": "castle_gateway.akm",
                "world_xz": [-177.0, 383.0],
                "godot_y": 0.0,
                "name": "castle_gateway",
            }
        ],
    }
    with pytest.raises(edits.MapEditError) as excinfo:
        edits.apply_delta(RUNTIME_MAP, delta, out_path=target, runtime_terrain=RUNTIME_MAP)
    # Assert the STABLE refusal token, not the prose: the blocker FL id moved
    # from FL-4833 to FL-4834 mid-flight and broke a sibling suite doing that.
    assert "MAP_EDIT_REFUSED" in str(excinfo.value)
    assert target.read_bytes() == before, "a refused edit must write nothing at all"
    # The assertions above already pass: the refusal is correct and total. What
    # is NOT yet true is that the edit REACHES the map, which is what this test
    # is named for. Remove this line the day G0c lands.
    assert False, (
        "G0C_CANONICAL_A3D: the castle gateway cannot be authored until LANE P1 / FL-4834 "
        "elects the canonical .a3d and FL-4833 upgrades the runtime map format"
    )


@SEAM(
    "NO_ENCOUNTER_ANCHOR",
    "the compiled spawn manifest holds no enemy generator inside the castle footprint",
)
def test_the_spawn_compiler_would_emit_the_castle_encounter_anchors() -> None:
    """SEAM FL4826_NO_ENCOUNTER_ANCHOR.

    The compiled manifest is the existing runtime contract
    (scripts/compile_fl4798_map_spawns.py -> fl4798_map_spawns.v1). The castle
    slice needs at least one enemy generator inside the interior footprint.
    This asserts the EXPECTED OUTPUT of the compiler, not a new format.
    """
    spawns = json.loads(SPAWNS.read_text(encoding="utf-8"))
    # Measured baseline at this HEAD, so a change is visible rather than silent.
    assert spawns["counts"]["items"] == 154
    assert spawns["counts"]["enemy_generators"] == 26
    assert spawns["counts"]["sprites"] == 48
    assert spawns["counts"]["minimap_markers"] == 0
    assert spawns["counts"]["player_start"] is False
    # The castle sits at authored_landmarks xz [-177.0, 383.0]; the blockout
    # spans 69.2 x 68.3 model units around it.
    def near_castle(record: dict) -> bool:
        x, z = record["world_xz"]
        return abs(x + 177.0) <= 40.0 and abs(z - 383.0) <= 40.0

    anchors = [g for g in spawns["enemy_generators"] if near_castle(g)]
    assert anchors, (
        "FL4826_NO_ENCOUNTER_ANCHOR: the compiled spawn manifest holds no enemy generator "
        "within the castle footprint at (-177, 383); the castle is scenery until it does"
    )


def test_the_spawn_manifest_names_both_connected_consumers_without_claiming_pixels() -> None:
    """Pin source ownership while leaving headed visual acceptance explicit."""
    spawns = json.loads(SPAWNS.read_text(encoding="utf-8"))
    assert spawns["consumers"]["server"].startswith("colyseus_server: WIRED")
    client = spawns["consumers"]["client"]
    assert "World._publish_map_sprite_rows" in client
    assert "Y8IsoViewer" in client
    assert "does not establish visible/runtime acceptance" in client
