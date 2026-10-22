#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"


def test_dump_metadata_names_cell_owner_product_as_authority() -> None:
    src = EFFECT.read_text(encoding="utf-8")

    required = [
        '"contract": "cell_owner_product_consumed_by_final_paint"',
        '"owner_product": "cell_owner_product_ssbo consumed by ascii_compositor.glsl main dispatch"',
        '"actor": "actor visibility is cell_owner_kind=actor_sprite plus actor row fields from cell_owner_product_ssbo"',
        '"actor_rows": _pending_actor_cell_debug_rows.duplicate(true)',
        '"sink": "foot sink and waterline clip are cell_owner_product_ssbo actor fields"',
        '"blood": "cell_owner_blood_visible and product paint_fg/bg from cell_owner_product_ssbo"',
        '"foliage": "cell_owner_foliage_class/coverage/density_class and glyph_id from cell_owner_product_ssbo"',
        '"water_state": "cell_owner_kind=water from cell_owner_product_ssbo; CPU color inference is debug only"',
        '"shadow": "cell_owner_shadow_class and cell_owner_shadow_density from cell_owner_product_ssbo; luma estimate is debug only"',
    ]
    for needle in required:
        assert needle in src, needle

    forbidden = [
        '"contract": "cells.jsonl combines separate surfaces and must not be read as one painted truth"',
        '"owner_product": "visible_surface_owner_image fact readback, not proof that final dispatch painted that owner"',
        '"water_state": "shader material-family fact when present, otherwise CPU color inference and marked per row"',
        '"blood": "blood is not emitted per cell; visible blood needs actor-owner final paint evidence"',
        '"sink": "foot sink is sidecar row data; waterline clip is not emitted as per-cell proof here"',
    ]
    for needle in forbidden:
        assert needle not in src, needle
