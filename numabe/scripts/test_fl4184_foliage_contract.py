#!/usr/bin/env python3
"""FL-4184 terrain-foliage ownership contract after the FL-4708 tree cut."""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
EFFECT = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
SCENE_DB = ROOT / "godot_project/render/scene/SceneGeometryDB.gd"


def test_terrain_foliage_keeps_one_product_owner() -> None:
    product = _expand_includes(PRODUCT.read_text())
    assert "FOLIAGE_SOURCE_TERRAIN_GRASS = 0" in product
    assert "void compute_product_foliage_fact(" in product
    assert product.count("void compute_product_foliage_fact(") == 1
    assert "foliage_base_glyph_for_coverage(" in product


def test_tree_and_falling_leaf_runtime_stays_deleted() -> None:
    combined = "\n".join(
        path.read_text() for path in (PRODUCT, EFFECT, SCENE_DB)
    )
    retired = (
        "FOLIAGE_SOURCE_TREE_CANOPY",
        "FOLIAGE_SOURCE_FALLING_LEAF",
        "STATIC_WORLD_TREE_LEAF_MAT",
        "STATIC_WORLD_TREE_BARK_MAT",
        "LeafParticleBank",
        "leaf_particle_bank_rd",
        "vsb_emit_retained_leaf_bank",
        "vsb_retained_enumerate_canopy_face",
        "canopy_identity_bytes",
    )
    assert not any(token in combined for token in retired)


if __name__ == "__main__":
    test_terrain_foliage_keeps_one_product_owner()
    test_tree_and_falling_leaf_runtime_stays_deleted()
    print("FL4184_FOLIAGE_CONTRACT_PASS")
