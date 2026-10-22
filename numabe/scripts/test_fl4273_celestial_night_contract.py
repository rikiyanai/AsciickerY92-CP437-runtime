#!/usr/bin/env python3
"""Prove the retained Product path consumes the FL-4273 shared moon.

FL-4431 deleted the probe-resolve mirror.  The host still freezes one moon
direction, and Product is now the only shader that may consume it.
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EFFECT = ROOT / "godot_project/render/scene/lighting/CelestialFrameState.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def main() -> int:
    effect = EFFECT.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "const DAY_CYCLE_MOON_VERTICAL_SCALE := 0.12" in effect
    assert "var antipode := -sun_direction(t)" in effect
    assert "antipode.y * DAY_CYCLE_MOON_VERTICAL_SCALE" in effect
    assert "moon_dir = _day_cycle_moon_dir(t)" in effect
    assert "sun_dir = sun_direction(t)" in effect
    assert "vec4 moon_dir;" in product
    assert "vec4 sky_ctl;" in product
    assert "vec3 product_moon_dir()" in product
    assert "resolve_moon_dir" not in product
    print("FL4273_VISIBLE_SHARED_MOON_CONTRACT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
