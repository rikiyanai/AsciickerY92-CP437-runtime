#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def test_waterline_clip_is_actor_water_mask_without_full_height_water_occlusion() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "bool actor_sample_survives_waterline_clip(int row, vec2 local_uv, bool base_is_water)" in src
    assert "if (!actor_sample_survives_waterline_clip(" in src
    assert "row, standee.local_uv," in src
    assert "base_is_water)) continue;" in src
    assert "receivers[slot].world_kind\n\t\t\t\t\t\t\t\t== OWNER_WATER;" in src
    assert "receivers[slot].world_kind\n\t\t\t\t\t\t\t\t== OWNER_TERRAIN" in src
    assert "|| water_surface;" in src
    assert "receivers[slot].world_receiver_y" in src
    assert "> actor_foot_world(row).y\n\t\t\t\t\t\t\t\t\t+ 0.55;" in src
    assert "bool terrain_occludes_actor =\n\t\t\t\t\t\t\tterrain_like" not in src
