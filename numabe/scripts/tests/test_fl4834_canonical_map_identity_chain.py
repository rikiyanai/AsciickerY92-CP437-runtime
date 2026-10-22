"""FL-4834 / FL-4833: one elected canonical map, and the identity chain across it.

WHAT THIS SUITE OWNS
--------------------
The election recorded under FL-4834 is that
``godot_project/assets/maps/game_map_y8.a3d`` is the single canonical Y8 map,
and that ``assets/a3d/game_map_y8_original_game_map.a3d`` is a REDUCED
derivative, not a second source of truth.  The tests below are the falsifiers
for that election and for the world-format migration it unblocks.

WHY IDENTITIES AND NOT COUNTS
-----------------------------
A count test passes while a migration quietly substitutes one item for another,
moves a spawn point by a fraction of a world unit, or re-rounds a float.  Every
comparison here is over IEEE-754 bits and definition ids, so a substitution or a
re-rounding fails just as loudly as a deletion.

WHAT THIS SUITE DOES NOT CLAIM
------------------------------
Nothing about how any of this LOOKS.  The renderer is recorded broken
(FL-4359 OPEN).  These are byte facts.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# FL-4834 ELECTION.  The canonical Y8 map is the one the Colyseus authority and
# the Godot client both load (colyseus_server/src/sim/terrain.ts:67,
# godot_project/scripts/map/A3DMapLoader.gd:15, PlayableMap.gd:13).
ELECTED_CANONICAL_MAP = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
# Explicit legacy negative control for the former v1 reader refusal. The
# elected Y8 map is migrated; this tracked v1 map keeps the pre-v3 item layout
# available to prove the refusal path still fails closed.
LEGACY_NEGATIVE_MAP = ROOT / "assets/a3d/game_map_y7.a3d"
# The reduced derivative the election retires AS A SOURCE.  It stays in the tree
# as the material-palette reference
# (addons/io_asciicker/scene/default_materials.py:104).
REDUCED_TWIN = ROOT / "assets/a3d/game_map_y8_original_game_map.a3d"
# The map that already has the shape the Y8 migration is heading for: one file
# that is both source and runtime, world format v4, carrying a player start.
SBU_MAP = ROOT / "godot_project/assets/maps/sbu_carto_scale225_20260601.a3d"

A3D_EDIT = ROOT / "docs/agent/cli-anything/a3d_edit.py"
APPLY_MAP_EDITS = ROOT / "scripts/apply_map_edits.py"
SPAWN_COMPILER = ROOT / "scripts/compile_fl4798_map_spawns.py"
SHIPPED_MANIFEST = ROOT / "godot_project/assets/world_spawns/game_map_y8.fl4798_map_spawns.v1.json"
ASCIIID_BINARY = ROOT / ".run" / "asciicker-presenter"

A3D_HEIGHT_SCALE = 16.0


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def edit():
    return _load("fl4834_a3d_edit", A3D_EDIT)


@pytest.fixture(scope="module")
def cli():
    return _load("fl4834_apply_map_edits", APPLY_MAP_EDITS)


@pytest.fixture(scope="module")
def compiler():
    return _load("fl4834_spawn_compiler", SPAWN_COMPILER)


def _sections(edit, path: Path):
    return edit.read_a3d_sections(path, strict_markers=False)


def _item_identity(inst) -> tuple:
    """An item's identity: what it is, how many, and exactly where."""
    return (
        int(inst.item_definition_id),
        int(inst.item_count),
        struct.pack("<3f", *[float(v) for v in inst.pos]),
        struct.pack("<f", float(inst.yaw)),
    )


def _sprite_identity(inst) -> tuple:
    return (str(inst.inst_name),
            struct.pack("<3f", *[float(v) for v in inst.pos]),
            struct.pack("<f", float(inst.yaw)))


def _mesh_identity(inst) -> tuple:
    return (str(inst.mesh_name),
            struct.pack("<3d", *[float(v) for v in inst.transform[12:15]]))


def _enemy_identity(gen) -> tuple:
    return (struct.pack("<3f", *[float(v) for v in gen.pos]),
            int(gen.alive_max), int(gen.revive_min), int(gen.revive_max),
            int(gen.armor), int(gen.helmet), int(gen.shield),
            int(gen.sword), int(gen.crossbow))


def _by_variant(instances, variant):
    return [i for i in instances if getattr(i, "variant", "mesh") == variant]


# ---------------------------------------------------------------------------
# The election itself.
# ---------------------------------------------------------------------------

def test_the_elected_canonical_map_decodes_at_all(edit):
    """It could not, before FL-4834: its 154 items are pre-v3 records.

    This is the whole reason the repository believed a v1 map "cannot represent
    an item".  It represents them; nothing here could read them.
    """
    _pre, raw_version, instances, start, gens, markers = _sections(edit, ELECTED_CANONICAL_MAP)
    version = -raw_version if raw_version < 0 else raw_version
    assert len(_by_variant(instances, "item")) == 154
    assert len(_by_variant(instances, "sprite")) == 48
    assert len(_by_variant(instances, "mesh")) == 1083
    assert len(gens) == 26
    if version < 4:
        assert start is None, "a pre-v4 document has no player-start slot at all"


def test_authoring_defaults_use_the_elected_document():
    """The election must reach the live editor/compiler, not only this suite."""
    placements = _load("fl4834_tree_defaults", ROOT / "scripts/compile_fl4708_y8_tree_placements.py")
    assert placements.DEFAULT_SOURCE == ELECTED_CANONICAL_MAP
    viewer = (ROOT / "godot_project/scripts/map/Y8IsoViewer.gd").read_text()
    assert 'const EDIT_SOURCE_MAP := "godot_project/assets/maps/game_map_y8.a3d"' in viewer
    assert '_args.get("edit-runtime-terrain", source)' in viewer
    assert 'const EDIT_SOURCE_MAP := "assets/a3d/game_map_y8_original_game_map.a3d"' not in viewer
    session = (ROOT / "godot_project/scripts/map/MapEditSession.gd").read_text()
    for producer in [
        "compile_fl4708_y8_tree_placements.py",
        "compile_fl4798_map_spawns.py",
        "compile_fl4789_chunk_catalog.py",
        "bake_bridge_deck_slabs.py",
        "compile_fl4789_actor_atlas_catalog.py",
    ]:
        assert producer in session, f"canonical editor save is not wired to {producer}"
    assert "_build_race_overlay()" in viewer


def test_the_elected_map_carries_every_record_the_reduced_twin_has(edit):
    """The election's load-bearing claim: electing the runtime map loses nothing.

    If this ever fails, the retired twin holds an authored record the canonical
    map does not, and the election recorded under FL-4834 is wrong.
    """
    _p, _v, elected, _s, elected_gens, _m = _sections(edit, ELECTED_CANONICAL_MAP)
    _p2, _v2, twin, _s2, twin_gens, _m2 = _sections(edit, REDUCED_TWIN)

    assert sorted(_item_identity(i) for i in _by_variant(elected, "item")) == \
        sorted(_item_identity(i) for i in _by_variant(twin, "item")), \
        "item identities diverge between the two candidate maps"
    assert sorted(_sprite_identity(i) for i in _by_variant(elected, "sprite")) == \
        sorted(_sprite_identity(i) for i in _by_variant(twin, "sprite"))
    assert sorted(_enemy_identity(g) for g in elected_gens) == \
        sorted(_enemy_identity(g) for g in twin_gens)

    elected_meshes = sorted(_mesh_identity(i) for i in _by_variant(elected, "mesh"))
    twin_meshes = sorted(_mesh_identity(i) for i in _by_variant(twin, "mesh"))
    missing = sorted(set(twin_meshes) - set(elected_meshes))
    assert missing == [], (
        f"the elected canonical map is missing {len(missing)} mesh placements that "
        "only the retired twin has; re-open the FL-4834 election before shipping"
    )
    assert len(elected_meshes) > len(twin_meshes), (
        "the elected map is the RICHER document (1083 mesh records against 322). "
        "If the counts ever converge, re-measure the election rather than trusting "
        "this assertion's premise."
    )


# ---------------------------------------------------------------------------
# Decode / encode symmetry — the precondition for any migration.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("map_path", [ELECTED_CANONICAL_MAP, REDUCED_TWIN, SBU_MAP],
                         ids=lambda p: p.name)
def test_world_section_round_trip_is_byte_stable(edit, tmp_path, map_path):
    """Read every section and write them straight back: same bytes, no exceptions.

    This covers three separate asymmetries FL-4834 found and closed: the pre-v3
    item layout, the v5/v6 mesh tail that ``write_a3d_sections`` never passed
    down, and the absent trailing marker section that used to grow a v1 document
    by four bytes on every save.
    """
    staged = tmp_path / map_path.name
    shutil.copy2(map_path, staged)
    original = staged.read_bytes()
    edit.write_a3d_sections(staged, *_sections(edit, staged))
    assert staged.read_bytes() == original, f"{map_path.name} is not byte-stable on re-save"


# ---------------------------------------------------------------------------
# The migration.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def migrated(cli, tmp_path_factory):
    """A tracked legacy map upgraded to v4, with an explicitly authored start."""
    out_dir = tmp_path_factory.mktemp("fl4834_migration") / "maps"
    out_dir.mkdir(parents=True)
    out = out_dir / LEGACY_NEGATIVE_MAP.name
    receipt = cli.upgrade_world_format(
        LEGACY_NEGATIVE_MAP, 4, out,
        player_start={"world_xz": [-12.257, 108.677], "godot_y": 0.0, "yaw": 0.0},
    )
    return out, receipt


def test_migration_raises_the_version_and_touches_no_terrain(cli, migrated):
    out, receipt = migrated
    assert receipt["world_format_version_before"] == \
        cli._world_format_version(LEGACY_NEGATIVE_MAP)
    assert receipt["world_format_version_after"] == 4
    assert receipt["terrain_bytes_unchanged"] is True
    # Byte-level restatement of that claim, independent of the receipt: the
    # header, every patch and the material table are the same bytes.
    before = LEGACY_NEGATIVE_MAP.read_bytes()
    after = out.read_bytes()
    patch_count = struct.unpack_from("<I", before, 8)[0]
    pre_size = 16 + patch_count * 188 + 256 * 512
    assert before[:pre_size] == after[:pre_size]


def test_migration_preserves_every_item_identity(edit, migrated):
    out, receipt = migrated
    _p, _v, before, _s, before_gens, _m = _sections(edit, LEGACY_NEGATIVE_MAP)
    _p2, raw, after, start, after_gens, _m2 = _sections(edit, out)
    assert -raw == 4

    before_items = sorted(_item_identity(i) for i in _by_variant(before, "item"))
    after_items = sorted(_item_identity(i) for i in _by_variant(after, "item"))
    assert after_items == before_items, "the migration lost or altered an item identity"
    assert len(after_items) == 154
    assert receipt["legacy_item_records_upgraded"] == 154

    assert sorted(_sprite_identity(i) for i in _by_variant(after, "sprite")) == \
        sorted(_sprite_identity(i) for i in _by_variant(before, "sprite"))
    assert sorted(_mesh_identity(i) for i in _by_variant(after, "mesh")) == \
        sorted(_mesh_identity(i) for i in _by_variant(before, "mesh"))
    assert sorted(_enemy_identity(g) for g in after_gens) == \
        sorted(_enemy_identity(g) for g in before_gens)


def test_migrated_item_identity_bundle_is_complete_and_stable(edit, migrated):
    """The legacy item bundle is filled without changing authored identity."""
    out, _receipt = migrated
    _p, _v, migrated_instances, _s, _g, _m = _sections(edit, out)
    _p2, _v2, legacy_instances, _s2, _g2, _m2 = _sections(edit, LEGACY_NEGATIVE_MAP)
    legacy_identity = sorted(
        (int(i.item_definition_id), int(i.item_count),
         struct.pack("<3f", *[float(v) for v in i.pos]), struct.pack("<f", float(i.yaw)))
        for i in _by_variant(legacy_instances, "item")
    )
    migrated_identity = sorted(
        (int(i.item_definition_id), int(i.item_count),
         struct.pack("<3f", *[float(v) for v in i.pos]), struct.pack("<f", float(i.yaw)))
        for i in _by_variant(migrated_instances, "item")
    )
    assert migrated_identity == legacy_identity
    assert {(int(i.visual_style_id), int(i.presentation_kind_id))
            for i in _by_variant(migrated_instances, "item")} == {(1, 0)}


@pytest.mark.parametrize("existing_target", [False, True])
def test_migration_refuses_when_a_record_is_silently_dropped(cli, tmp_path, monkeypatch, existing_target):
    """The falsifier for the whole migration: a dropped item must REFUSE, not pass.

    The writer is replaced with one that drops a single item record.  Nothing
    else changes.  A migration that only counted records would still succeed
    here, which is why it does not count them.
    """
    real_edit = cli._a3d_edit()

    class DroppingEdit:
        read_a3d_sections = staticmethod(real_edit.read_a3d_sections)

        @staticmethod
        def write_a3d_sections(path, pre, fmt_version, instances, start, gens, markers, **kwargs):
            kept, dropped = [], False
            for inst in instances:
                if not dropped and getattr(inst, "variant", "") == "item":
                    dropped = True
                    continue
                kept.append(inst)
            assert dropped, "fixture precondition: the map must contain an item"
            return real_edit.write_a3d_sections(path, pre, fmt_version, kept, start,
                                                gens, markers, **kwargs)

    monkeypatch.setattr(cli, "_a3d_edit", lambda: DroppingEdit)
    out = tmp_path / "dropped.a3d"
    if existing_target:
        shutil.copy2(ELECTED_CANONICAL_MAP, out)
    original = out.read_bytes() if existing_target else None
    with pytest.raises(cli.MapEditError) as excinfo:
        cli.upgrade_world_format(ELECTED_CANONICAL_MAP, 4, out)
    assert "UPGRADE_REJECTED_INSTANCE_IDENTITY_LOST" in str(excinfo.value)
    if existing_target:
        assert out.read_bytes() == original, "refused migration must preserve the saved map"
    else:
        assert not out.exists(), "refused new-output migration must not publish a target"


@pytest.mark.parametrize("existing_target", [False, True])
def test_migration_refuses_when_the_player_start_is_silently_dropped(cli, tmp_path, monkeypatch, existing_target):
    """Same falsifier for the spawn point, which has no count to check at all."""
    real_edit = cli._a3d_edit()

    class StartDroppingEdit:
        read_a3d_sections = staticmethod(real_edit.read_a3d_sections)

        @staticmethod
        def write_a3d_sections(path, pre, fmt_version, instances, start, gens, markers, **kwargs):
            return real_edit.write_a3d_sections(path, pre, fmt_version, instances, None,
                                                gens, markers, **kwargs)

    monkeypatch.setattr(cli, "_a3d_edit", lambda: StartDroppingEdit)
    out = tmp_path / "no_start.a3d"
    if existing_target:
        shutil.copy2(ELECTED_CANONICAL_MAP, out)
    original = out.read_bytes() if existing_target else None
    with pytest.raises(cli.MapEditError) as excinfo:
        cli.upgrade_world_format(
            ELECTED_CANONICAL_MAP, 4, out,
            player_start={"world_xz": [-12.257, 108.677], "godot_y": 0.0, "yaw": 0.0},
        )
    assert "UPGRADE_REJECTED_PLAYER_START_IDENTITY_LOST" in str(excinfo.value)
    if existing_target:
        assert out.read_bytes() == original, "refused migration must preserve the saved map"
    else:
        assert not out.exists(), "refused migration must not publish a missing player start"


def test_migration_never_downgrades(cli, tmp_path):
    with pytest.raises(cli.MapEditError):
        cli.upgrade_world_format(SBU_MAP, 3, tmp_path / "downgraded.a3d")


# ---------------------------------------------------------------------------
# The gate remains explicit for legacy documents after migration lands.
# ---------------------------------------------------------------------------

def test_the_capability_gate_tracks_the_map_it_is_asked_about(cli, migrated):
    """The refusal is kept, not deleted; it is keyed to the document, not wished away.

    Before the migration the elected map refuses an item and a player start AND
    is refused wholesale by the product codec.  After it, both are allowed.  The
    same function reports both, so the gate cannot be "made green" by editing it.
    """
    out, _receipt = migrated
    before = cli.capabilities(LEGACY_NEGATIVE_MAP)
    after = cli.capabilities(out)

    version = before["world_format_version"]
    # FL-4834: `authorable` is the CONJUNCTION of the four separate properties,
    # so it is derived here from the same four rather than from one version
    # number. A gate that could be satisfied by the version alone is what let
    # "v1 cannot contain an item" stand beside a v1 map holding 154 of them.
    for kind in ("item", "player_start"):
        row = before["record_kinds"][kind]
        assert row["authorable"] == bool(
            row["format_can_represent"] and row["writer_can_author"]
            and row["reader_can_consume"] and before["canonical_codec_accepts"])
    # The item refusal before migration is a READER defect, never a format one.
    assert before["record_kinds"]["item"]["format_can_represent"] is True
    assert before["record_kinds"]["item"]["records_present_in_document"] == 154
    assert "item" not in before["format_unrepresentable_record_kinds"]
    assert before["reader_defect_record_kinds"] == ["item"]
    # The player start refusal before migration IS a format one, and stays.
    assert before["record_kinds"]["player_start"]["format_can_represent"] == (version >= 4)
    if version < 4:
        assert before["authoring_blocked"] is True
        assert "FL-4833" in before["authoring_blocker"]

    assert after["world_format_version"] == 4
    assert after["canonical_codec_accepts"] is True
    for kind in ("item", "player_start"):
        row = after["record_kinds"][kind]
        assert row["format_can_represent"] is True
        assert row["writer_can_author"] is True
        assert row["reader_can_consume"] is True
        assert row["authorable"] is True
    assert after["format_unrepresentable_record_kinds"] == []
    assert after["reader_defect_record_kinds"] == []
    assert after["authoring_blocked"] is False


def test_the_product_codec_accepts_the_migrated_map_and_refuses_the_v1_one(edit, migrated):
    """The acceptance oracle is the C++ product, not this suite's own reader."""
    if not ASCIIID_BINARY.is_file():
        pytest.skip(f"product binary not built: {ASCIIID_BINARY}")
    out, _receipt = migrated
    env = dict(os.environ)
    env["ASCIID_FL4588_CODEC_ROUNDTRIP"] = f"{ELECTED_CANONICAL_MAP};{LEGACY_NEGATIVE_MAP};{out}"
    completed = subprocess.run(
        [str(ASCIIID_BINARY)], cwd=ROOT, env=env, capture_output=True, text=True,
    )
    lines = [ln for ln in completed.stdout.splitlines()
             if ln.startswith("[FL-4588][CODEC-ROUNDTRIP]")]
    assert len(lines) == 3, completed.stdout[-2000:]
    facts = {ln.split(" path=")[1].split(" ")[0]: ln for ln in lines}
    migrated_line = facts[str(out)]
    assert " ok=1 " in migrated_line and " world_version=4 " in migrated_line, migrated_line
    _p, _v, migrated_instances, _s, _g, _m = _sections(edit, out)
    assert f" instances={len(migrated_instances)} " in migrated_line, migrated_line

    canonical_line = facts[str(ELECTED_CANONICAL_MAP)]
    assert " ok=1 " in canonical_line and " world_version=4 " in canonical_line, canonical_line
    legacy_line = facts[str(LEGACY_NEGATIVE_MAP)]
    assert "reason=world_stream_invalid" in legacy_line, legacy_line


# ---------------------------------------------------------------------------
# canonical .a3d -> compiler input -> manifest identity.
# ---------------------------------------------------------------------------

def test_the_shipped_manifest_carries_the_canonical_map_item_identities(edit):
    """Link two of the chain: every manifest item row is a real record in the map.

    The comparison is over (definition id, count, world X/Z) after applying the
    compiler's own coordinate contract, so a row invented by the compiler, a row
    dropped by it, or a row moved by it all fail.
    """
    manifest = json.loads(SHIPPED_MANIFEST.read_text())
    _p, _v, instances, _s, _g, _m = _sections(edit, ELECTED_CANONICAL_MAP)
    centre = manifest["runtime_center_offset"]

    from_map = sorted(
        (int(i.item_definition_id), int(i.item_count),
         round(float(i.pos[0]) - centre[0], 4), round(float(i.pos[1]) - centre[1], 4))
        for i in _by_variant(instances, "item")
    )
    from_manifest = sorted(
        (int(row["item_definition_id"]), int(row["item_count"]),
         round(float(row["world_xz"][0]), 4), round(float(row["world_xz"][1]), 4))
        for row in manifest["items"]
    )
    assert from_manifest == from_map, (
        "the shipped spawn manifest and the elected canonical map disagree about "
        "which items exist and where"
    )


def test_the_compiler_refuses_a_source_the_product_codec_refuses(compiler):
    """The silent-drop path FL-4834 closed.

    ``_player_start`` used to swallow every exception and return None, so a
    refused document compiled a manifest that declared "no player start" and
    nothing said the start had never been looked for.
    """
    version = struct.unpack_from(
        "<i", LEGACY_NEGATIVE_MAP.read_bytes(),
        16 + struct.unpack_from("<I", LEGACY_NEGATIVE_MAP.read_bytes(), 8)[0] * 188 + 256 * 512,
    )[0]
    assert -version == 1
    with pytest.raises(SystemExit) as excinfo:
        compiler._player_start(LEGACY_NEGATIVE_MAP)
    assert "FL4798_MAP_SPAWNS_SOURCE_REFUSED" in str(excinfo.value)


def test_one_canonical_map_compiles_a_manifest_that_keeps_the_spawn_point(compiler, migrated, tmp_path):
    """Links three and four: one file is BOTH source and runtime, and the start survives.

    This is the shape the election is for.  ``source_a3d_sha256`` and
    ``runtime_terrain_sha256`` are the same digest because there is one map.
    """
    out, _receipt = migrated
    product = compiler.compile_spawns(out, out)
    assert product["source_a3d_sha256"] == product["runtime_terrain_sha256"]
    assert product["source_a3d_path"] == product["runtime_terrain_path"]
    assert product["counts"]["items"] == 154
    assert product["counts"]["player_start"] is True
    assert product["runtime_terrain_authoring"]["world_format_version"] == 4
    authoring = product["runtime_terrain_authoring"]
    assert authoring["unrepresentable_record_kinds"] == []
    assert authoring["format_unrepresentable_record_kinds"] == []
    assert authoring["writer_unsupported_record_kinds"] == []
    assert authoring["reader_defect_record_kinds"] == []
    assert authoring["canonical_codec_accepts"] is True
    assert authoring["record_kinds"]["item"]["records_present_in_document"] == 154
    assert authoring["authoring_blocked"] is False

    start = product["player_start"]
    # float32 storage is the only rounding allowed between the request and here.
    assert start["world_xz"][0] == pytest.approx(-12.257, abs=1e-3)
    assert start["world_xz"][1] == pytest.approx(108.677, abs=1e-3)
    assert start["ground_y"] is not None, "the authored start must stand on known terrain"
