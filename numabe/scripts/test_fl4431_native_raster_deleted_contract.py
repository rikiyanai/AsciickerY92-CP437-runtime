#!/usr/bin/env python3
"""Source contract for the FL-4431 redundant native-raster deletion.

The deletion rule is about RUNTIME OWNERSHIP: no executable branch may read the
retired FL4431_NATIVE_RASTER_OFF gate, and the camera cull mask stays pinned to
zero. A '#' comment that names the retired gate to explain WHY the code below it
looks the way it does is documentation, not a runtime owner — so the gate-absence
assertion runs against the comment-stripped source. (The original form of this
check forbade the raw string anywhere in World.gd, which its own explanatory
comment at the cull-mask pin tripped — the check had failed since it landed.)
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

from test_fl4431_vsb_activation_contract import (
    extract_gd_function,
    gd_target_guard_states,
)


ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "godot_project/scripts/World.gd"
EFFECT = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def strip_gd_comments(src: str) -> str:
    """Drop everything from the first '#' to end-of-line on each GDScript line,
    leaving only executable text for the ownership assertions below."""
    return "\n".join(line.split("#", 1)[0] for line in src.splitlines())


def strip_glsl_comments(src: str) -> str:
    """Remove GLSL comments before checking executable ownership."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def main() -> int:
    world = WORLD.read_text(encoding="utf-8")
    effect = EFFECT.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "camera.cull_mask = 0" in world
    assert "FL4431_NATIVE_RASTER_OFF" not in strip_gd_comments(world)
    assert "access_resolved_color = false" in effect
    assert "access_resolved_depth = false" in effect
    assert "get_depth_layer(" not in effect
    assert "native_depth_image" not in effect
    assert "u_native_depth" not in effect
    # Binding 23 was the retired SolidWaterCellBits owner. Do not conflate
    # generic numeric-binding absence with the F11 ownership deletion: reject
    # the named owner, its cell predicate, and its Product scan directly.
    product_executable = strip_glsl_comments(product)
    assert "binding = 23" not in effect
    assert "binding = 23" not in product_executable
    for retired in (
        "SolidWaterCellBits",
        "cell_solid_in_water",
        "product_fl4216_shore_fact",
    ):
        assert retired not in product_executable, (
            f"retired FL-4216 shoreline owner remains in Product: {retired}"
        )
    assert not re.search(
        r"for\s*\(\s*int\s+(?:dx|dy)\s*=\s*-6",
        product_executable,
    ), "retired 13x13 shoreline scan remains in Product"
    # The product dispatch must still be REACHED. Its intent here is "the ASCII
    # product stage is still what draws the frame, now that the native scene
    # raster is gone" -- it is a liveness check, not a signature check.
    #
    # It used to be spelled as the exact one-line string
    # "_dispatch_cell_owner_product(push, pixel_ctx)". The FL-4431 activation
    # gave that call two more arguments, so it is now written across several
    # lines. The live caller opens one shared compute list and passes it in with
    # the prepared dispatch, letting VSB and Product share explicit fences.
    # Search only the real callback body: the same call in a dead helper must
    # not satisfy this liveness contract.
    executable_effect = strip_gd_comments(effect)
    callback = extract_gd_function(executable_effect, "_render_callback")
    assert callback is not None, "_render_callback is missing"
    call = re.search(
        r"_dispatch_cell_owner_product\(\s*push\s*,\s*pixel_ctx\b",
        callback,
    )
    assert call is not None, (
        "the cell-owner product dispatch is no longer called with (push, pixel_ctx, ...); "
        "the ASCII product stage must remain the thing that draws the frame"
    )
    assert gd_target_guard_states(
        callback, "_dispatch_cell_owner_product("
    ) == [False], (
        "the sole Product dispatch must be executable inside _render_callback"
    )

    print("PASS: native scene raster and stale resolved-depth product path are deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
