#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_SHADER = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def test_flowers_keep_june20_product_owned_seed_admission():
	src = _expand_includes(PRODUCT_SHADER.read_text(encoding="utf-8"))

	assert "const float FOLIAGE_FLOWER_FREQUENCY     = 0.72;" in src
	assert "if (slot_role == FOLIAGE_ROLE_FLOWER || seed1 < FOLIAGE_FLOWER_FREQUENCY * 0.24) variant = FOLIAGE_VARIANT_FLOWER;" in src
	assert "if (slot_role == FOLIAGE_ROLE_FLOWER) variant = FOLIAGE_VARIANT_FLOWER;" not in src
	assert "else if (slot_role == FOLIAGE_ROLE_LEAF_LEFT || slot_role == FOLIAGE_ROLE_LEAF_RIGHT || seed2 < FOLIAGE_ACCENT_PROBABILITY) variant = FOLIAGE_VARIANT_ACCENT_BLADE;" in src
