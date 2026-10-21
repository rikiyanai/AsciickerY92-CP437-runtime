"""FL-4839 / FL-4833: the authoring round trip and the two gates that guard it.

Every fact here is a SERVER FACT. The renderer is recorded broken (FL-4359 OPEN,
FL-4775 RUNTIME_RED), so this suite deliberately proves nothing about how an
authored placement LOOKS. It proves that the bytes are right, that a zero-edit
save is a byte no-op, and that an unrepresentable placement is REFUSED rather
than written and silently dropped.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/apply_map_edits.py"
SOURCE_MAP = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
RUNTIME_MAP = SOURCE_MAP
# Explicit legacy negative control. The shipped Y8 document is migrated v4;
# this tracked v1 map still carries pre-v3 items and must remain refused by the
# product codec rather than weakening the landed canonical assertion.
LEGACY_MAP = ROOT / "assets/a3d/game_map_y7.a3d"
SPAWN_COMPILER = ROOT / "scripts/compile_fl4798_map_spawns.py"
EDITOR_SESSION = ROOT / "godot_project/scripts/map/MapEditSession.gd"
EDITOR_VIEWER = ROOT / "godot_project/scripts/map/Y8IsoViewer.gd"
MAP_LOADER = ROOT / "godot_project/scripts/map/A3DMapLoader.gd"
TOUCH_CONTROLS = ROOT / "godot_project/scripts/map/MapEditorTouchControls.gd"
RUNTIME_MAP_CATALOG = ROOT / "godot_project/scripts/map/RuntimeMapCatalog.gd"
AUTHORING_LAUNCHER = ROOT / "scripts/launch_godot_map_authoring.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def edits():
    return _load("fl4839_apply_map_edits", CLI)


def test_the_runtime_map_is_migrated_and_product_admitted(edits) -> None:
    caps = edits.capabilities(RUNTIME_MAP)
    assert caps["world_format_version"] == 4
    assert caps["canonical_codec_accepts"] is True
    assert caps["authoring_blocked"] is False
    assert caps["record_kinds"]["player_start"]["format_min_version"] == 4
    assert caps["record_kinds"]["item"]["authorable"] is True
    assert caps["record_kinds"]["player_start"]["authorable"] is True


def test_legacy_negative_control_remains_refused(edits) -> None:
    caps = edits.capabilities(LEGACY_MAP)
    assert caps["world_format_version"] == 1
    assert caps["canonical_codec_accepts"] is False
    assert caps["authoring_blocked"] is True
    assert "item record before v3" in caps["canonical_codec_error"]


@pytest.mark.parametrize("failure", ["exception", "unstable"])
def test_candidate_codec_refusal_never_replaces_the_saved_map(edits, tmp_path, monkeypatch, failure):
    target = tmp_path / "existing.a3d"
    shutil.copy2(SOURCE_MAP, target)
    original = target.read_bytes()
    codec = edits._codec()
    original_roundtrip = codec.roundtrip_verdict
    # Capability inspection sees the valid existing document. Only the changed
    # serialized candidate is refused, reproducing the previous post-write bug.
    def reject_candidate(data):
        if data == original:
            return original_roundtrip(data)
        if failure == "exception":
            raise ValueError("injected candidate refusal")
        return {"roundtrip_stable": False}
    monkeypatch.setattr(codec, "roundtrip_verdict", reject_candidate)
    monkeypatch.setattr(edits, "_codec", lambda: codec)
    with pytest.raises(edits.MapEditError, match="MAP_EDIT_REJECTED"):
        edits.apply_delta(target, {"$schema": edits.DELTA_SCHEMA, "ops": [{
            "op": "add_marker", "name": "candidate", "world_xz": [1, 2],
        }]}, runtime_terrain=target)
    assert target.read_bytes() == original
    assert target.with_suffix(".a3d.bak").read_bytes() == original


def test_legacy_item_representability_is_measured_from_the_file_not_asserted(edits) -> None:
    """The four properties must disagree with each other, because they do.

    FL-4834 premise correction. A single conflated table said `item` needs v3,
    and the shipped v1 map holds 154 of them. Falsifier for this test: the
    capability block reporting format_can_represent(item)=False for a document
    the same call counts 154 item records in, or reporting the refusal as a
    FORMAT limit when the refusing code is our own reader.
    """
    caps = edits.capabilities(LEGACY_MAP)
    item = caps["record_kinds"]["item"]
    start = caps["record_kinds"]["player_start"]

    # The count is read out of the document; it is the evidence, not a constant.
    assert item["records_present_in_document"] == 154
    assert edits.record_counts(LEGACY_MAP)["item"] == 154

    # 1. the FORMAT can hold it at v1 -- proven by the 154 that are in there.
    assert item["format_can_represent"] is True
    assert item["format_layout"] == "legacy_pre_v3"
    # 2. our WRITER can emit that layout.
    assert item["writer_can_author"] is True
    # 3. our READER cannot read it back. This is the defect, and it is ours.
    assert item["reader_can_consume"] is False
    assert item["reader_stricter_than_format"] is True
    assert "READER DEFECT" in item["reader_defect"]
    assert "not a format limit" in item["reader_defect"]
    # 4. document level.
    assert caps["canonical_codec_accepts"] is False

    # The roll-ups must keep the three causes apart.
    assert caps["format_unrepresentable_record_kinds"] == ["player_start"]
    assert caps["reader_defect_record_kinds"] == ["item"]
    assert "item" not in caps["format_unrepresentable_record_kinds"]

    # player_start is the one that IS a format limit, and stays one.
    assert start["format_can_represent"] is False
    assert start["reader_stricter_than_format"] is False
    assert start["records_present_in_document"] == 0


def test_the_writer_really_does_emit_a_legacy_item_into_a_v1_document(edits, tmp_path) -> None:
    """writer_can_author is claimed; this writes one and reads it back.

    Falsifier: the re-read returns a different record count, loses any prior
    identity, moves one terrain byte, or returns the appended item changed.
    """
    fmt = edits._a3d_format()
    a3d = edits._a3d_edit()
    target = tmp_path / "v1_author.a3d"
    shutil.copy2(LEGACY_MAP, target)

    pre, raw_version, instances, start, gens, markers = a3d.read_a3d_sections(
        target, strict_markers=False)
    assert raw_version == -1, "this falsifier only means anything on a v1 document"
    before = [edits._instance_identity(inst) for inst in instances]

    new = fmt.A3DInstance(variant="item")
    new.item_definition_id, new.item_count = 13, 1
    new.visual_style_id = fmt.LEGACY_ITEM_VISUAL_STYLE_ID
    new.presentation_kind_id = fmt.LEGACY_ITEM_PRESENTATION_KIND_ID
    new.legacy_item_layout = True
    new.pos, new.yaw, new.flags, new.story_id = [1.0, 2.0, 3.0], 0.25, 3, -1
    instances.append(new)
    a3d.write_a3d_sections(target, pre, raw_version, instances, start, gens, markers)

    after_pre, after_raw, after, _s, _g, _m = a3d.read_a3d_sections(
        target, strict_markers=False)
    assert after_pre == pre, "a world-section write must not touch terrain"
    assert after_raw == -1, "the document must keep its own version"
    assert len(after) == len(before) + 1
    ids = [edits._instance_identity(inst) for inst in after]
    assert ids[:len(before)] == before
    assert ids[-1] == edits._instance_identity(new)

    # And the honest other half: this repository still cannot READ that document.
    codec = edits._codec()
    with pytest.raises(Exception, match="item record before v3"):
        codec.parse_document(target.read_bytes())


def test_the_a3d_edit_cli_writer_keeps_the_documents_own_version(edits, tmp_path) -> None:
    """FL-4834: the OTHER writer entry point had the bug write_a3d_sections fixed.

    ``write_a3d_instances`` (used by a3d_edit `append` and `delete`) called
    A3DInstance.write() with no version, so it fell back to 4: on a v1 document
    that wrote the v3 item identity bundle and desynced every following record.
    Falsifier: a zero-edit read/write of the explicit v1 legacy control changing
    one byte, or the same on the migrated v4 canonical map changing one byte.
    """
    a3d = edits._a3d_edit()
    for name, source in (("v1", LEGACY_MAP), ("v4", SOURCE_MAP)):
        target = tmp_path / f"cli_{name}.a3d"
        shutil.copy2(source, target)
        original = target.read_bytes()
        pre, fmt_version, instances, tail = a3d.read_a3d_instances(target)
        a3d.write_a3d_instances(target, pre, fmt_version, instances, tail)
        assert target.read_bytes() == original, (
            f"{name}: a zero-edit a3d_edit write must be a byte no-op")


def test_the_elected_placement_source_can_hold_every_authored_record(edits) -> None:
    caps = edits.capabilities(SOURCE_MAP)
    assert caps["world_format_version"] == 4
    assert caps["canonical_codec_accepts"] is True
    assert caps["authoring_blocked"] is False
    for kind in ("mesh", "item", "player_start", "enemy_generator", "marker"):
        assert caps["record_kinds"][kind]["authorable"] is True
        assert caps["record_kinds"][kind]["format_can_represent"] is True
        assert caps["record_kinds"][kind]["reader_can_consume"] is True


def test_a3_zero_edit_save_is_a_byte_no_op(edits, tmp_path) -> None:
    # The falsifier for a lossy round trip. If reading and re-writing an
    # untouched document changes one byte, every later claim about an authored
    # edit is unfalsifiable.
    target = tmp_path / "noop.a3d"
    shutil.copy2(SOURCE_MAP, target)
    receipt = edits.apply_delta(
        SOURCE_MAP, {"$schema": edits.DELTA_SCHEMA, "ops": []},
        out_path=target, runtime_terrain=RUNTIME_MAP,
    )
    assert target.read_bytes() == SOURCE_MAP.read_bytes()
    assert receipt["canonical_verdict"]["roundtrip_stable"] is True


def test_a1_every_authored_record_kind_survives_the_round_trip(edits, tmp_path) -> None:
    target = tmp_path / "edited.a3d"
    shutil.copy2(SOURCE_MAP, target)
    delta = {"$schema": edits.DELTA_SCHEMA, "ops": [
        {"op": "add_mesh", "mesh": "tree-1.akm", "name": "fl4839_probe",
         "world_xz": [40.0, 32.0], "godot_y": 14.625},
        {"op": "add_item", "item_definition_id": 21, "world_xz": [41.0, 33.0],
         "godot_y": 14.765625, "item_count": 1},
        {"op": "add_enemy_generator", "world_xz": [45.0, 35.0], "godot_y": 15.296875,
         "alive_max": 2, "revive_min": 5, "revive_max": 10, "equipment": {"sword": 3}},
        {"op": "set_player_start", "world_xz": [40.0, 32.0], "godot_y": 14.625},
        {"op": "add_marker", "name": "fl4839_probe", "label": "Probe",
         "world_xz": [40.0, 32.0], "marker_type": 0, "glyph": "P"},
    ]}
    before = edits.read_placements(SOURCE_MAP, RUNTIME_MAP)["counts"]
    receipt = edits.apply_delta(SOURCE_MAP, delta, out_path=target,
                               runtime_terrain=RUNTIME_MAP)
    assert receipt["canonical_verdict"]["roundtrip_stable"] is True
    # The world format is PRESERVED, not upgraded. A canonical v6 rewrite would
    # change 1.1 MB of unrelated bytes and invalidate the FL-4789 catalog.
    assert receipt["canonical_verdict"]["world_version"] == 4

    after = edits.read_placements(target, RUNTIME_MAP)
    assert after["counts"]["meshes"] == before["meshes"] + 1
    assert after["counts"]["items"] == before["items"] + 1
    assert after["counts"]["enemy_generators"] == before["enemy_generators"] + 1
    assert after["counts"]["minimap_markers"] == before["minimap_markers"] + 1
    assert after["counts"]["player_start"] is True

    # Coordinates survive both directions exactly: the read and the write use
    # one statement of the contract, so a value written back is a no-op.
    placed = next(m for m in after["meshes"] if m["name"] == "fl4839_probe")
    assert placed["world_xz"] == [40.0, 32.0]
    assert placed["godot_y"] == 14.625
    assert after["player_start"]["world_xz"] == [40.0, 32.0]
    assert after["minimap_markers"][0]["world_xz"] == [40.0, 32.0]


def test_fl4880_race_marker_replace_delete_preserves_generic_markers(edits, tmp_path) -> None:
    target = tmp_path / "race_markers.a3d"
    after_delete = tmp_path / "race_markers_after_delete.a3d"
    shutil.copy2(SOURCE_MAP, target)
    replace = {"$schema": edits.DELTA_SCHEMA, "ops": [
        {"op": "add_marker", "name": "generic_keep", "world_xz": [1.0, 2.0]},
        {"op": "replace_race_markers", "markers": [
            {"name": "race_gate_0", "world_xz": [63.0, 90.0]},
            {"name": "race_gate_1", "world_xz": [74.0, 93.0]},
            {"name": "race_grid_0", "world_xz": [59.0, 84.5]},
        ]},
    ]}
    receipt = edits.apply_delta(SOURCE_MAP, replace, out_path=target,
                                runtime_terrain=RUNTIME_MAP)
    assert receipt["canonical_verdict"]["roundtrip_stable"] is True
    markers = edits.read_placements(target, RUNTIME_MAP)["minimap_markers"]
    names = {row["name"] for row in markers}
    assert {"race_gate_0", "race_gate_1", "race_grid_0"} <= names
    assert "generic_keep" in names
    deleted = edits.apply_delta(target, {"$schema": edits.DELTA_SCHEMA, "ops": [
        {"op": "delete_marker", "name": "race_gate_1"},
    ]}, out_path=after_delete, runtime_terrain=RUNTIME_MAP)
    assert deleted["canonical_verdict"]["roundtrip_stable"] is True
    after_names = {row["name"] for row in edits.read_placements(after_delete, RUNTIME_MAP)["minimap_markers"]}
    assert "race_gate_1" not in after_names
    assert {"race_gate_0", "race_grid_0", "generic_keep"} <= after_names
    with pytest.raises(edits.MapEditError, match="admits race_gate_N"):
        edits.apply_delta(SOURCE_MAP, {"$schema": edits.DELTA_SCHEMA, "ops": [{
            "op": "delete_marker", "name": "race_gate_bad",
        }]}, out_path=tmp_path / "bad.a3d", runtime_terrain=RUNTIME_MAP)


def test_fl4880_track_control_points_roundtrip_in_the_same_a3d_marker_section(
        edits, tmp_path) -> None:
    target = tmp_path / "race_path.a3d"
    shutil.copy2(SOURCE_MAP, target)
    profile = "route.v1|profile=road|width=12.000|elevation=4.000|bank=8.000"
    delta = {"$schema": edits.DELTA_SCHEMA, "ops": [{
        "op": "replace_race_markers",
        "markers": [
            {"name": "race_path_0", "label": profile, "world_xz": [0.0, 0.0]},
            {"name": "race_path_1", "label": profile, "world_xz": [40.0, 0.0]},
            {"name": "race_path_2", "label": profile, "world_xz": [40.0, 40.0]},
            {"name": "race_gate_0", "world_xz": [0.0, 0.0]},
            {"name": "race_gate_1", "world_xz": [40.0, 0.0]},
            {"name": "race_grid_0", "world_xz": [-12.0, 0.0]},
        ],
    }]}
    receipt = edits.apply_delta(SOURCE_MAP, delta, out_path=target,
                                runtime_terrain=RUNTIME_MAP)
    assert receipt["canonical_verdict"]["roundtrip_stable"] is True
    markers = edits.read_placements(target, RUNTIME_MAP)["minimap_markers"]
    by_name = {row["name"]: row for row in markers}
    assert [by_name[f"race_path_{i}"]["world_xz"] for i in range(3)] == [
        [0.0, 0.0], [40.0, 0.0], [40.0, 40.0],
    ]
    assert by_name["race_path_0"]["label"] == profile
    assert {"race_gate_0", "race_gate_1", "race_grid_0"} <= by_name.keys()


def test_a5_an_authored_edit_reaches_the_shipped_spawn_manifest(edits, tmp_path) -> None:
    # Runtime reachability without a booted game: the SAME compiler the server
    # reads (colyseus_server/src/sim/map_spawns.ts loads this product) sees the
    # placement. compile_spawns requires a repository-relative source, so the
    # edited map is written beside the canonical one and removed afterwards.
    target = ROOT / "assets/a3d/.fl4839_roundtrip_probe.a3d"
    try:
        shutil.copy2(SOURCE_MAP, target)
        edits.apply_delta(SOURCE_MAP, {"$schema": edits.DELTA_SCHEMA, "ops": [
            {"op": "add_item", "item_definition_id": 21, "world_xz": [41.0, 33.0],
             "godot_y": 14.765625, "item_count": 1},
            {"op": "set_player_start", "world_xz": [40.0, 32.0], "godot_y": 14.625},
            {"op": "replace_race_markers", "markers": [
                {"name": "race_gate_0", "world_xz": [63.0, 90.0]},
                {"name": "race_gate_1", "world_xz": [74.0, 93.0]},
                {"name": "race_grid_0", "world_xz": [59.0, 84.5]},
            ]},
        ]}, out_path=target, runtime_terrain=RUNTIME_MAP)
        compiler = _load("fl4839_spawn_compiler", SPAWN_COMPILER)
        shipped = json.loads(
            (ROOT / "godot_project/assets/world_spawns/game_map_y8.fl4798_map_spawns.v1.json").read_text()
        )
        after = compiler.compile_spawns(target, RUNTIME_MAP)
        assert after["counts"]["items"] == shipped["counts"]["items"] + 1
        assert shipped["counts"]["player_start"] is False
        assert after["counts"]["player_start"] is True
        assert after["items"][-1]["prototype"]["name"] == "Meat"
        assert after["items"][-1]["world_xz"] == [41.0, 33.0]
        assert [row["name"] for row in after["minimap_markers"]] == [
            "race_gate_0", "race_gate_1", "race_grid_0",
        ]
    finally:
        target.unlink(missing_ok=True)
        target.with_suffix(".a3d.bak").unlink(missing_ok=True)
        target.with_suffix(".a3d.tmp").unlink(missing_ok=True)


def test_a_blocked_placement_is_refused_and_names_the_property_that_blocked_it(
        edits, tmp_path) -> None:
    """Both refusals are kept, and each names its OWN cause.

    FL-4834: `add_item` on the v1 legacy control is refused because our READER
    cannot read the record back, not because v1 cannot hold one -- it holds 154.
    `set_player_start` is refused because v1 genuinely has no slot. Falsifier:
    either refusal citing the other's cause, or either one writing a byte.
    """
    for op, expect_in_message, must_not_say in (
        ({"op": "add_item", "item_definition_id": 21, "world_xz": [0.0, 0.0],
          "godot_y": 0.0},
         "READER DEFECT", "UNREPRESENTABLE in this document"),
        ({"op": "set_player_start", "world_xz": [0.0, 0.0], "godot_y": 0.0},
         "UNREPRESENTABLE", "READER DEFECT"),
    ):
        target = tmp_path / "should_not_change.a3d"
        shutil.copy2(LEGACY_MAP, target)
        original = target.read_bytes()
        with pytest.raises(edits.MapEditError) as excinfo:
            edits.apply_delta(LEGACY_MAP,
                              {"$schema": edits.DELTA_SCHEMA, "ops": [op]},
                              out_path=target, runtime_terrain=LEGACY_MAP)
        message = str(excinfo.value)
        assert "MAP_EDIT_REFUSED_UNREPRESENTABLE" in message
        assert expect_in_message in message
        assert must_not_say not in message
        # Refusal is total: the target is byte-identical.
        assert target.read_bytes() == original
        target.unlink(missing_ok=True)


def test_frozen_enemy_ranges_refuse_rather_than_saturate(edits, tmp_path) -> None:
    target = tmp_path / "ranges.a3d"
    shutil.copy2(SOURCE_MAP, target)
    for bad in (
        {"alive_max": 8, "revive_min": 0, "revive_max": 0},
        {"alive_max": 1, "revive_min": 20, "revive_max": 5},
        {"alive_max": 1, "revive_min": 0, "revive_max": 31},
    ):
        payload = {"op": "add_enemy_generator", "world_xz": [40.0, 32.0],
                   "godot_y": 14.0, **bad}
        with pytest.raises(edits.MapEditError):
            edits.apply_delta(SOURCE_MAP, {"$schema": edits.DELTA_SCHEMA,
                                           "ops": [payload]},
                              out_path=target, runtime_terrain=RUNTIME_MAP)


def test_the_addon_codec_reads_and_writes_the_v5_and_v6_mesh_tail() -> None:
    # FL-4839: a3d_format.A3DInstance used to stop at flags/story for every
    # world version. scripts/fl4588/a3d_document_codec.py ALWAYS serialises v6,
    # so a canonicalised document walked 16 bytes short per mesh record in the
    # very compilers that consume the map. Reading v4 must be unchanged.
    import io
    fmt = _load("fl4839_a3d_format", ROOT / "addons/io_asciicker/scene/a3d_format.py")
    inst = fmt.A3DInstance(mesh_name="tree-1.akm", inst_name="probe", variant="mesh")
    inst.material_key, inst.material_override, inst.uid = 201, 1, 12345

    for version in (4, 5, 6):
        buffer = io.BytesIO()
        inst.write(buffer, format_version=version)
        payload = buffer.getvalue()
        buffer.seek(0)
        back = fmt.A3DInstance.from_file(buffer, format_version=version)
        assert buffer.tell() == len(payload), f"v{version} read/write length disagree"
        assert back.mesh_name == "tree-1.akm"
        assert back.material_key == (201 if version >= 5 else 200)
        assert back.uid == (12345 if version >= 6 else 0)

    v4_len = len(_written(fmt, inst, 4))
    assert len(_written(fmt, inst, 5)) == v4_len + 8
    assert len(_written(fmt, inst, 6)) == v4_len + 16


def _written(fmt, inst, version: int) -> bytes:
    import io
    buffer = io.BytesIO()
    inst.write(buffer, format_version=version)
    return buffer.getvalue()


def test_terrain_sculpt_roundtrips_existing_a3d_patch_section(edits, tmp_path) -> None:
    target = tmp_path / "sculpted.a3d"
    shutil.copy2(SOURCE_MAP, target)
    before = target.read_bytes()
    receipt = edits.apply_delta(
        SOURCE_MAP,
        {"$schema": edits.DELTA_SCHEMA, "ops": [{
            "op": "sculpt_terrain", "world_xz": [40.0, 32.0],
            "delta_y": 1.0, "radius": 3.0,
        }]},
        out_path=target, runtime_terrain=RUNTIME_MAP,
    )
    assert receipt["terrain"]["strokes"] == 1
    assert receipt["terrain"]["vertices_changed"] > 0
    assert receipt["canonical_verdict"]["roundtrip_stable"] is True
    after = target.read_bytes()
    assert len(after) == len(before)
    # World records and materials remain byte-stable; only the existing patch
    # height section is allowed to change.
    patch_count = int.from_bytes(before[8:12], "little")
    pre_end = 16 + patch_count * 188 + 256 * 512
    assert before[pre_end:] == after[pre_end:]
    assert before[:16] == after[:16]
    assert before[16:pre_end] != after[16:pre_end]


def test_terrain_sculpt_rejects_bad_strokes_without_writing(edits, tmp_path) -> None:
    target = tmp_path / "bad_sculpt.a3d"
    shutil.copy2(SOURCE_MAP, target)
    original = target.read_bytes()
    for op, expected in (
        ({"op": "sculpt_terrain", "world_xz": [40.0, 32.0],
          "delta_y": 0.001, "radius": 3.0}, "at least one 1/16"),
        ({"op": "sculpt_terrain", "world_xz": [40.0, 32.0],
          "delta_y": 1.0, "radius": 0.0}, "radius"),
        ({"op": "sculpt_terrain", "world_xz": [1.0e9, 1.0e9],
          "delta_y": 1.0, "radius": 1.0}, "did not touch"),
    ):
        with pytest.raises(edits.MapEditError, match=expected):
            edits.apply_delta(SOURCE_MAP, {"$schema": edits.DELTA_SCHEMA, "ops": [op]},
                              out_path=target, runtime_terrain=RUNTIME_MAP)
        assert target.read_bytes() == original


def test_authored_race_barrier_mesh_reaches_spawn_compiler(edits, tmp_path) -> None:
    target = tmp_path / "barrier_course.a3d"
    shutil.copy2(SOURCE_MAP, target)
    edits.apply_delta(
        SOURCE_MAP,
        {"$schema": edits.DELTA_SCHEMA, "ops": [{
            "op": "add_mesh", "mesh": "barrier.akm", "name": "fl4880_barrier_0",
            "world_xz": [40.0, 32.0], "godot_y": 14.625,
        }]},
        out_path=target, runtime_terrain=RUNTIME_MAP,
    )
    compiler = _load("fl4839_spawn_compiler_barrier", SPAWN_COMPILER)
    manifest = compiler.compile_spawns(target, RUNTIME_MAP)
    assert manifest["race_barriers"][0]["name"] == "fl4880_barrier_0"
    assert manifest["race_barriers"][0]["mesh"] == "barrier.akm"
    assert manifest["counts"]["race_barriers"] == 1


def test_editor_product_failure_can_retry_without_replaying_delta() -> None:
    session = EDITOR_SESSION.read_text(encoding="utf-8")
    save = session.split("func save(", 1)[1]
    retry = session.split("func retry_product_rebuild(", 1)[1]
    assert "for attempt_index in range(2)" in session
    assert "_pending_product_rebuild_map" in session
    assert "func product_rebuild_pending()" in session
    assert "return retry_product_rebuild(repo_root, python_bin)" in save
    assert "ops.clear()" in save
    assert "_set_product_rebuild_pending(saved_map, saved_runtime)" in save
    assert '"products_pending": product_rebuild_pending()' in save
    assert "_pending_product_rebuild_map = \"\"" in retry
    assert "FL4839_EDIT_PRODUCTS_RETRY_ACCEPTED" in retry


def test_touch_toolbar_is_gated_without_changing_embedded_read_only_mode() -> None:
    viewer = EDITOR_VIEWER.read_text(encoding="utf-8")
    controls = TOUCH_CONTROLS.read_text(encoding="utf-8")
    assert "DisplayServer.is_touchscreen_available()" in viewer
    # Embedded gameplay maps may expose navigation controls on touch/web, but
    # they must never configure the authoring palette.
    assert "return _is_touch_context() if embedded else (_edit_mode or _is_touch_context())" in viewer
    touch_builder = viewer.split("func _build_touch_controls()", 1)[1].split("\nfunc ", 1)[0]
    embedded_branch = touch_builder.split("if embedded:", 1)[1].split("else:", 1)[0]
    assert "_touch_controls.configure_navigation()" in embedded_branch
    assert "_touch_controls.visible and _touch_controls.route_touch(event)" in viewer
    assert "_touch_controls.set_toolbar_visible(_touch_toolbar_allowed())" in viewer
    assert "func set_toolbar_visible(visible: bool) -> void:" in controls
    assert "cancel_touches()" in controls.split("func set_toolbar_visible", 1)[1].split("func route_touch", 1)[0]


def test_viewer_and_launcher_elect_the_same_requested_map_before_loading() -> None:
    viewer = EDITOR_VIEWER.read_text(encoding="utf-8")
    catalog = RUNTIME_MAP_CATALOG.read_text(encoding="utf-8")
    launcher = AUTHORING_LAUNCHER.read_text(encoding="utf-8")
    ready = viewer.split("func _ready()", 1)[1].split("func _load_terrain", 1)[0]
    assert "var map_request := _requested_map_from_args()" in ready
    assert ready.index("map_path = requested_map") < ready.index("_load_terrain()")
    request = viewer.split("func _requested_map_from_args()", 1)[1].split("func _store_arg", 1)[0]
    assert 'RuntimeMapCatalogScript.map_id_for_resource_path(candidate)' in request
    assert 'candidate.begins_with("godot_project/")' in request
    arg_parser = catalog.split("static func _map_id_from_args()", 1)[1].split("static func _menu_map_id", 1)[0]
    assert 'arg.begins_with("--map=")' in arg_parser
    assert 'if arg.begins_with("--map-id=")' in arg_parser
    assert 'parser.add_argument(\n        "--map-id"' in launcher
    assert 'requested_map.is_file()' in launcher
    notes = viewer.split("func _build_notes()", 1)[1].split("func _rebuild_note_visuals", 1)[0]
    assert 'map_path.get_file().get_basename() != "game_map_y8"' in notes
    assert 'user://%s_iso_viewer_notes.v1.json' in notes


def test_standalone_authoring_window_is_resizable_and_escape_returns_to_menu() -> None:
    viewer = EDITOR_VIEWER.read_text(encoding="utf-8")
    launcher = AUTHORING_LAUNCHER.read_text(encoding="utf-8")
    ready = viewer.split("func _ready()", 1)[1].split("func _load_terrain", 1)[0]
    escape = viewer.split("elif return_to_menu_on_close:", 1)[1].split("_refresh_hud()", 1)[0]

    assert 'parser.add_argument("--resolution", default="1280x800")' in launcher
    assert '"--windowed",' in launcher
    assert 'user_args.insert(0, "--return-to-menu")' in launcher
    assert "DisplayServer.WINDOW_FLAG_RESIZE_DISABLED, false" in ready
    assert '_args.has("return-to-menu")' in ready
    assert "return_to_menu_on_close = true" in ready
    assert "_return_to_menu()" in escape


def test_large_map_brush_drag_defers_rebuild_and_uses_indexed_patch_queries() -> None:
    viewer = EDITOR_VIEWER.read_text(encoding="utf-8")
    loader = MAP_LOADER.read_text(encoding="utf-8")
    session = EDITOR_SESSION.read_text(encoding="utf-8")
    begin = viewer.split("func _begin_edit_brush_stroke", 1)[1].split("\nfunc ", 1)[0]
    continued = viewer.split("func _continue_edit_brush_stroke", 1)[1].split("\nfunc ", 1)[0]
    finish = viewer.split("func _finish_edit_brush_stroke", 1)[1].split("\nfunc ", 1)[0]
    preview_mesh = viewer.split("func _apply_terrain_preview_mesh", 1)[1].split("\nfunc ", 1)[0]
    edit_preview = viewer.split("func _refresh_edit_preview", 1)[1].split("\nfunc ", 1)[0]
    stage_edit = viewer.split("func _stage_edit", 1)[1].split("\nfunc ", 1)[0]
    lookup = loader.split("func _patch_for_godot_xz", 1)[1].split("\nfunc ", 1)[0]

    assert "_edit_brush_preview_dirty = true" in begin
    assert "_refresh_terrain_preview()" not in begin
    assert "_refresh_terrain_preview()" not in continued
    assert "if refresh and _edit_brush_preview_dirty:" in finish
    assert "MAX_EDIT_BRUSH_SAMPLES_PER_EVENT" in continued
    assert "if _edit_brush_stroke_active:" in stage_edit
    assert "Meshes, sprites, items, spawns and markers do not mutate terrain" in stage_edit
    assert "_request_edit_preview_refresh" in stage_edit
    assert "_build_race_overlay()" not in stage_edit
    assert "_terrain_preview_patches_in_radius(centre_source, radius)" in viewer
    assert "LARGE_MAP_FULL_PREVIEW_PATCH_LIMIT" in preview_mesh
    assert "TerrainEditOverlay" in preview_mesh
    assert 'if op_name in ["sculpt_terrain", "paint_material", "patch_diagonal"]:' in edit_preview
    assert "PendingSculpt_" not in edit_preview
    assert "_patches_by_coord.get(Vector2i(patch_x, patch_y), {})" in lookup
    sculpt = session.split(
        "func stage_terrain_sculpt", 1)[1].split("\nfunc ", 1)[0]
    assert "if _active_brush_stroke_group == 0:" in sculpt
    assert sculpt.count("FL4839_TERRAIN_SCULPT_STAGED") == 1
    assert "_rebuild_terrain_collision()" not in preview_mesh

    marker_update = session.split("func update_generic_marker", 1)[1].split("\nfunc ", 1)[0]
    assert 'String(ops.back().get("op", "")) == "replace_generic_markers"' in marker_update
    assert 'int(ops.back().get("_editor_brush_stroke_group", 0)) == group' in marker_update


def test_interactive_placement_paths_do_not_rebuild_large_map_terrain() -> None:
    viewer = EDITOR_VIEWER.read_text(encoding="utf-8")
    stage_edit = viewer.split("func _stage_edit", 1)[1].split("\nfunc ", 1)[0]
    process = viewer.split("func _process", 1)[1].split("\nfunc ", 1)[0]
    drag = viewer.split(
        "elif event is InputEventMouseMotion and _edit_drag_active", 1
    )[1].split("elif event is InputEventMouseButton", 1)[0]
    commands = viewer.split("func _on_editor_touch_command", 1)[1].split(
        "\nfunc _resolve_repo_root", 1
    )[0]

    assert "var terrain_tool := tool in" in stage_edit
    assert "MapEditSessionScript.Tool.TERRAIN_SCULPT" in stage_edit
    assert "MapEditSessionScript.Tool.MATERIAL_BRUSH" in stage_edit
    assert "MapEditSessionScript.Tool.DIAGONAL_BRUSH" in stage_edit
    assert "_request_edit_preview_refresh" in stage_edit
    assert "_refresh_terrain_preview()" in stage_edit
    assert stage_edit.index("if terrain_tool:") < stage_edit.index("_refresh_terrain_preview()")

    assert "_edit_preview_refresh_pending" in process
    assert "_race_overlay_refresh_pending" in process
    assert "_request_edit_preview_refresh()" in drag
    assert "_refresh_edit_preview()" not in drag

    refresh_tail = commands.rsplit("# Refresh only the products the command can change.", 1)[1]
    assert '"delete_all_meshes"' in refresh_tail
    assert '"update_selected"' in refresh_tail
    assert '"reset_items"' in refresh_tail
    assert '"update_enemy"' in refresh_tail
    assert '"assign_story"' in refresh_tail
    assert '"build_track"' in refresh_tail
    assert "_request_edit_preview_refresh" in refresh_tail


def test_scripted_course_barriers_are_staged_before_the_save_transaction() -> None:
    viewer = EDITOR_VIEWER.read_text(encoding="utf-8")
    ready = viewer.split("func _ready()", 1)[1].split("func _load_terrain", 1)[0]
    barrier_call = 'if _args.has("race-barriers"):\n\t\t_generate_race_barriers_from_args()'
    save_call = 'if _args.has("edit-save"):'
    assert barrier_call in ready
    assert ready.index(barrier_call) < ready.index(save_call)
