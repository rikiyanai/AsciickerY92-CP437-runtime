from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path
import json
import re
import struct


ROOT = Path(__file__).resolve().parents[2]
WORLD_PATH = ROOT / "godot_project/scripts/World.gd"
PRODUCT_PATH = ROOT / "godot_project/render/product/cell_owner_product.glsl"
EFFECT_PATH = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
SCENE_DB_PATH = ROOT / "godot_project/render/scene/SceneGeometryDB.gd"
VSB_PATH = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
IMPORTER_PATH = ROOT / "godot_project/addons/asciicker_importer/import_a3d.gd"
A3D_PATH = ROOT / "godot_project/assets/maps/game_map_y8.a3d"
VISUAL_CELLS = 8
HEIGHT_CELLS = 4
HEIGHT_SCALE = 16.0


class Heightfield:
    def __init__(self, path: Path) -> None:
        data = path.read_bytes()
        patch_count = struct.unpack_from("<I", data, 8)[0]
        self.patches = {}
        offset = 16
        for _index in range(patch_count):
            patch_x, patch_z = struct.unpack_from("<ii", data, offset)
            heights = struct.unpack_from("<25H", data, offset + 136)
            self.patches[(patch_x, patch_z)] = heights
            offset += 188
        patch_xs = [key[0] for key in self.patches]
        patch_zs = [key[1] for key in self.patches]
        min_x = min(patch_xs)
        min_z = min(patch_zs)
        width = max(patch_xs) - min_x + 1
        depth = max(patch_zs) - min_z + 1
        self.center_x = (min_x + width / 2.0) * VISUAL_CELLS
        self.center_z = (min_z + depth / 2.0) * VISUAL_CELLS
        self.origin_x = min_x * VISUAL_CELLS - self.center_x
        self.origin_z = min_z * VISUAL_CELLS - self.center_z
        self.width = width * VISUAL_CELLS
        self.depth = depth * VISUAL_CELLS

    def height(self, world_x: float, world_z: float) -> float | None:
        source_x = world_x + self.center_x
        source_z = world_z + self.center_z
        patch_x = int(source_x // VISUAL_CELLS)
        patch_z = int(source_z // VISUAL_CELLS)
        heights = self.patches.get((patch_x, patch_z))
        if heights is None:
            return None
        local_x = source_x - patch_x * VISUAL_CELLS
        local_z = source_z - patch_z * VISUAL_CELLS
        height_x = local_x / 2.0
        height_z = local_z / 2.0
        x0 = int(height_x // 1)
        z0 = int(height_z // 1)
        x1 = min(x0 + 1, HEIGHT_CELLS)
        z1 = min(z0 + 1, HEIGHT_CELLS)
        tx = height_x - x0
        tz = height_z - z0
        h00 = heights[z0 * (HEIGHT_CELLS + 1) + x0]
        h10 = heights[z0 * (HEIGHT_CELLS + 1) + x1]
        h01 = heights[z1 * (HEIGHT_CELLS + 1) + x0]
        h11 = heights[z1 * (HEIGHT_CELLS + 1) + x1]
        top = h00 * (1.0 - tx) + h10 * tx
        bottom = h01 * (1.0 - tx) + h11 * tx
        return (top * (1.0 - tz) + bottom * tz) / HEIGHT_SCALE


def _hash_unit(index: int, salt: int) -> float:
    value = (index + 1) * 1103515245 + salt * 12345
    value = ((value >> 16) ^ value) & 0x7FFFFFFF
    return float(value % 10000) / 10000.0


def _distributed_tree_heights(heightfield: Heightfield, count: int) -> list[float]:
    heights = []
    for index in range(count):
        x = heightfield.origin_x + 18.0 + (heightfield.width - 36.0) * _hash_unit(index, 53)
        z = heightfield.origin_z + 18.0 + (heightfield.depth - 36.0) * _hash_unit(index, 62)
        height = heightfield.height(x, z)
        if height is not None:
            heights.append(height)
    return heights


def test_fl4708_retained_tree_successor_replaces_only_the_old_publisher() -> None:
    source = WORLD_PATH.read_text()
    retired_tokens = (
        "STATIC_WORLD_TREE_COUNT",
        "STATIC_WORLD_TREE_MAX_GROUND_Y",
        "STATIC_WORLD_NOTE_TREE_FACES_RESOURCES",
        "_append_static_world_tree_grove",
        "_append_static_world_tree_volume",
        "_append_static_world_note_tree",
        "_ensure_tree_variant_faces",
    )
    assert not any(token in source for token in retired_tokens)
    assert "const FL4708_AUTHORED_TREE_COUNT := 179" in source
    assert "const FL4708_TREE_LAB_COUNT := 3" in source
    assert "const FL4708_TREE_COUNT := FL4708_AUTHORED_TREE_COUNT" in source
    assert "_append_fl4708_retained_tree_instances(" not in source
    assert "_append_fl4708_retained_tree(" not in source
    assert "_install_fl4789_compiled_tree_registry(" in source
    assert "admit_compiled_asset" in source
    assert "seal_instance_registry" in source
    assert "STATIC_WORLD_MESH loaded trees=%d" in source
    assert "owner=world_partition_to_scene_geometry_db" in source


def test_fl4708_delete_first_has_no_hidden_grove_or_distributed_fallback() -> None:
    source = WORLD_PATH.read_text()
    assert "STATIC_WORLD_SPAWN_GROVE_ANCHOR_XZ" not in source
    assert "STATIC_WORLD_REVIEW_ANCHOR_XZ" not in source
    assert "STATIC_WORLD_FLAT_REVIEW_ANCHOR_XZ" not in source
    assert "tree_birch_" not in source
    assert "using procedural box trees" not in source


def test_fl4708_delete_first_skips_authored_tree_records_before_instantiation() -> None:
    importer = IMPORTER_PATH.read_text()
    skip = 'if mesh_name.to_lower() == "tree" or inst_name.to_lower() == "tree":'
    assert skip in importer
    assert importer.index(skip) < importer.index("var instance_map = {")
    assert "res://assets/props/tree_varied.tscn" not in importer
    for retired_path in (
        ROOT / "godot_project/assets/props/tree_varied.tscn",
        ROOT / "godot_project/assets/props/tree_1.tscn",
        ROOT / "godot_project/assets/props/tree_variant_picker.gd",
    ):
        assert not retired_path.exists()
    assert not list(
        (ROOT / "godot_project/assets/world_mesh").glob("tree_birch*_faces.json")
    )


def test_fl4708_delete_first_removes_the_complete_tree_and_falling_leaf_owner() -> None:
    sources = "\n".join(
        path.read_text()
        for path in (PRODUCT_PATH, EFFECT_PATH, SCENE_DB_PATH, VSB_PATH)
    )
    retired_tokens = (
        "q3_probe_payload_canopy_footprint_relax",
        "q3_probe_payload_canopy_coverage_at_cell",
        "vsb_product_selected_canopy",
        "STATIC_WORLD_TREE_LEAF_MAT",
        "STATIC_WORLD_TREE_BARK_MAT",
        "vsb_retained_enumerate_canopy_face",
        "vsb_emit_retained_leaf_bank",
        "LeafParticleBank",
        "leaf_particle_bank_rd",
        "canopy_identity_bytes",
        "FOLIAGE_SOURCE_FALLING_LEAF",
        "FOLIAGE_SOURCE_TREE_CANOPY",
        "product_tree_selected_subcell_count",
    )
    assert not any(token in sources for token in retired_tokens)
    assert "layout(set = 0, binding = 59" not in _expand_includes(PRODUCT_PATH.read_text())
    assert "_probe_u_storage(59" not in EFFECT_PATH.read_text()


def test_pieta_statue_is_the_playable_owner_at_the_saved_rock_marks() -> None:
    """The giant mossy Pieta statue must be wired into the playable world.

    HISTORY -- WHY THIS TEST USED TO ASSERT THE OPPOSITE. An earlier version of
    this test was named `test_pieta_has_no_playable_static_world_owner` and
    FORBADE every Pieta token in World.gd. That guard existed because the first
    Pieta attempt was baked only 3 units tall -- shorter than a 3.4-unit tree --
    so in play it looked like the model had failed to load, and it was pulled
    back out of the world.

    On 2026-07-27 the operator asked for it back, deliberately and much larger,
    replacing the small mossy boulders. The old bake was the problem, not the
    idea, so the guard is inverted rather than deleted: the statue must now be
    present, and it must be present at a size that cannot be mistaken for a
    failed load.
    """
    source = WORLD_PATH.read_text()

    # The statue is wired through the baked-faces resource and the operator's
    # saved marks. Both must exist or nothing stands in the world.
    assert "STATIC_WORLD_PIETA_STATUE_FACES_RESOURCE" in source
    assert "pieta_statue_mossy_faces.json" in source
    assert "STATIC_WORLD_PIETA_STATUE_MARKS" in source
    assert "STATIC_WORLD_PIETA_STATUE_SINK" in source

    # The retained geometry DB must store ONE shared asset for every mark. With
    # a 10,922-face model, per-mark duplication would be a real memory cost.
    assert 'register_asset("pieta_statue"' in source
    assert 'add_instance("pieta_statue"' in source

    # Guard the size regression directly. The bake command recorded in World.gd
    # must ask for a monumental height -- this is the exact failure that got the
    # statue removed the first time, so it is asserted rather than trusted.
    assert "--target-size 30.0" in source
    # And it must be baked overgrown, which is what produces the mossy top.
    assert "--moss-threshold 0.15" in source
    # The source mesh is Z-up; without this the statue lies on its side, which
    # the operator saw and reported in a headed run (FL-4203).
    assert "--up-axis +z" in source

    # Exactly ONE statue. A first pass placed two; the operator reviewed it and
    # asked for only the western one, at the coordinate they read off the HUD.
    marks_block = source.split("const STATIC_WORLD_PIETA_STATUE_MARKS := [", 1)[1]
    marks_block = marks_block.split("]", 1)[0]
    assert marks_block.count("Vector2(") == 1, "expected exactly one statue mark"
    assert "Vector2(133.0, -72.0)" in marks_block
    # The removed eastern statue must not creep back in.
    assert "Vector2(246.5, 51.9)" not in marks_block


def test_pieta_moss_shares_the_terrain_grass_foliage_grammar() -> None:
    """Moss must grow the same blades and flowers as the surrounding meadow.

    FL-4203: the operator asked for the statue's moss to share the terrain grass
    material and its flower foregrounds, rather than being a separate flat tint
    invented for stone. Moss faces are therefore sent through the SAME grass
    grammar terrain uses, keyed on the ground cell the moss stands over.
    """
    product = _expand_includes((WORLD_PATH.parent.parent / "render/product/cell_owner_product.glsl").read_text())

    # Moss must be admitted into the foliage pass at all -- previously only
    # terrain and tree canopy were.
    assert "bool moss_face = context.surface_kind == OWNER_STATIC_MESH" in product
    assert "context.material == STATIC_WORLD_MOSS_MAT" in product

    # It must reuse the meadow's own coverage query and the shared finishing
    # pass, not a private copy of the grass rules.
    assert "foliage_blade_coverage_at_cell(moss_cell)" in product
    assert "q3_probe_payload_finish_foliage(\n\t\t\tmoss_cell" in product

    # Flowers must be reachable on moss, which is the visible half of the ask.
    assert (
        "if (shape == FOLIAGE_SHAPE_FLOWER) markers |= Q3_DIAG_MARKER_FLOWER;"
        in product
    )

    # The base paint preserves the authored rock/moss split while both faces
    # reuse terrain's real tonal compiler. The mesh retains geometry, normals,
    # depth, and shadow ownership.
    assert "int product_static_rock_moss_proxy_material(" in product
    assert "if (material == STATIC_WORLD_ROCK_MAT) return 5;" in product
    assert "if (material == STATIC_WORLD_MOSS_MAT)" in product
    assert "ground_family == TERRAIN_FAMILY_GRASS ? ground_material : 1" in product
    assert "q3_probe_payload_apply_static_rock_moss_material(" in product
    assert "product_terrain_ramp_facts_for_surface(" in product
    assert "product_apply_material_ramp_steps(" in product
    assert "product_terrain_apply_product_tone(" in product
    assert "PRODUCT_TERRAIN_ROLE_CLIFF_FACE" in product
    assert "Moss and meadow now" in product
    assert "bg = mix(bg, foliage_fg * 0.24" not in product


def test_pieta_statue_bake_is_monumental_and_mossy() -> None:
    """The baked asset on disk must match what World.gd promises.

    World.gd only records the bake COMMAND in a comment; this checks the actual
    JSON that ships, so a stale or re-baked-too-small file cannot slip through.
    The 3.0-unit first attempt would fail every assertion here.
    """
    faces_path = (
        WORLD_PATH.parent.parent / "assets/world_mesh/pieta_statue_mossy_faces.json"
    )
    assert faces_path.exists(), f"baked statue missing: {faces_path}"
    doc = json.loads(faces_path.read_text())

    # Every face is 16 floats: 4 corners of (x, y, z, w). The y of each corner
    # is at offsets 1, 5, 9, 13 -- so stepping the flat array by 4 from index 1
    # visits every corner's height.
    rows = doc["rows"]
    heights = [rows[i] for i in range(1, len(rows), 4)]
    model_height = max(heights) - min(heights)

    # Must clearly out-scale the tallest existing landmark (the 13.48-unit
    # lighthouse) and tower over a 3.4-unit tree. This is the anti-"looks like
    # it didn't load" assertion.
    assert model_height > 25.0, f"statue only {model_height:.2f}u tall (needs >25)"
    assert model_height > 13.48 * 1.5, "statue must clearly out-scale the lighthouse"

    # Moss (211) on upward faces and bare rock (210) elsewhere: both ids must be
    # present, otherwise the statue is either bald stone or fully overgrown.
    mats = doc["mats"]
    assert mats.get("rock") == 210 and mats.get("moss") == 211
    face_mats = {int(rows[i * 16 + 3]) for i in range(doc["face_count"])}
    assert 210 in face_mats and 211 in face_mats
    moss_faces = sum(
        1 for i in range(doc["face_count"]) if int(rows[i * 16 + 3]) == 211
    )
    moss_fraction = moss_faces / doc["face_count"]
    # A believable overgrowth: a visible mossy cap, not a full moss shell.
    assert 0.05 < moss_fraction < 0.45, f"moss covers {moss_fraction:.1%} of faces"
