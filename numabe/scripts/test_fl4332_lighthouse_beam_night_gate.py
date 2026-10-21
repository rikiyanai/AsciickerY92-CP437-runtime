#!/usr/bin/env python3
"""Guard the visible FL-4332 lighthouse beam after retained-scene activation.

The kind-4 producer and single-pass beam collector still exist, so this
behavior was not retired with probe splat. The test stays red unless the
retained Product main path reaches both the collector and its stroke-glyph
law. A dead helper is not coverage and must not be mistaken for a rotating
beam.
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import sys
from pathlib import Path

# Pytest imports this file from the repository root, while direct execution
# starts inside `scripts/`. Put that one directory on the module search path so
# both entry points consume the same helper instead of silently testing
# different import environments.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_fl4431_vsb_activation_contract import (
    glsl_reachable_functions,
    strip_gd_comments,
)


ROOT = Path(__file__).resolve().parent.parent
WORLD = ROOT / "godot_project/scripts/World.gd"
HOST = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
COMPOSITOR = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"


def main() -> int:
    world = WORLD.read_text(encoding="utf-8")
    host = HOST.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compositor = COMPOSITOR.read_text(encoding="utf-8")

    assert 'effect.call("set_lighthouse_beam_anchor", lantern_world)' in world
    assert "const LOCAL_LIGHT_KIND_LIGHTHOUSE_BEAM := 4" in host
    assert "if baseline_feature_lighthouse_light and celestial.night_enabled():" in host
    assert "_add_lighthouse_beam_records(candidates, time_seconds)" in host
    assert "if (kind == 4)" in product
    assert "LighthouseCone lighthouse_cone_from_rows(" in product
    assert (
        "VsbProductLocalLightSlotFacts "
        "vsb_product_collect_local_light_slot_facts(" in product
    )
    assert "finalized_subcell_products.products[" in compositor

    # A declaration plus a call in dead code is still invisible. Walk the
    # parsed Product call graph from main, which is the activated compute entry
    # point that owns every final subcell record.
    reached = glsl_reachable_functions(product)
    assert "vsb_product_collect_local_light_slot_facts" in reached
    assert "lighthouse_beam_screen_stroke_glyph" in reached
    assert "apply_effect_cell_product_to_paint" in reached, (
        "beam product exists but its effect-to-paint apply edge is unreachable"
    )

    # Structural reachability cannot prove motion by itself. It can still
    # reject the two source defects that make rotation impossible: zero speed,
    # plus an azimuth function that never multiplies elapsed frame time.
    clean_host = strip_gd_comments(host)
    speed = re.search(
        r"const\s+LIGHTHOUSE_BEAM_ROT_RAD_PER_S\s*:?=\s*([0-9.]+)",
        clean_host,
    )
    assert speed is not None and float(speed.group(1)) > 0.0, (
        "lighthouse angular speed must stay positive"
    )
    azimuth = clean_host.split(
        "func get_lighthouse_beam_azimuth(time_seconds: float) -> float:", 1
    )[1].split("\nfunc ", 1)[0]
    assert "time_seconds * LIGHTHOUSE_BEAM_ROT_RAD_PER_S" in azimuth
    print("FL4332_RETAINED_LIGHTHOUSE_BEAM_NIGHT_GATE_OK")
    return 0


def test_lighthouse_beam_night_gate() -> None:
    """Expose the structural gate to normal pytest collection."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
