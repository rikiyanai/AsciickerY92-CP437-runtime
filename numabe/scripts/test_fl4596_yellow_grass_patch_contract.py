#!/usr/bin/env python3
"""Static contract for the retained FL-4596 yellow-patch correction.

The accepted base grass, blade, canopy, and fallback palettes stay unchanged.
The deleted probe-resolve mirror is no longer inspected: Product is the sole
paint owner after FL-4431.  Foliage clump geometry stays unchanged.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "godot_project" / "assets" / "maps" / "game_map_y8.a3d"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"

PATCH_BYTES = 188
MAT_BYTES = 4 * 16 * 8
GRASS_MATERIAL_IDS = (0, 1, 3)
AUTHORED_GRASS_RAMP = {
    (0, 0, 0),
    (0, 51, 0),
    (0, 102, 0),
    (51, 153, 0),
    (102, 204, 0),
    (153, 255, 51),
    (255, 255, 153),
}
REJECTED_BROAD_RETINT_RAMP = {
    (18, 45, 27),
    (31, 76, 43),
    (55, 112, 65),
    (82, 145, 84),
    (126, 174, 105),
    (174, 202, 137),
}


def _grass_ramp_branch(source: str) -> str:
    """Return just the grass branch of product_apply_material_ramp_steps.

    Several functions in this shader branch on the grass family, so the search
    is anchored to the ramp function BY NAME first, and only then cut down to
    its `if (family == TERRAIN_FAMILY_GRASS) { ... }` block. Scoping matters:
    a whole-file search for a hue assignment cannot tell whether that
    assignment actually applies to grass.
    """
    fn = source[source.index("void product_apply_material_ramp_steps(") :]
    branch = fn[fn.index("if (family == TERRAIN_FAMILY_GRASS) {") :]
    return branch[: branch.index("} else if (family == TERRAIN_FAMILY_DIRT) {")]


def _material_block(data: bytes, mat_id: int) -> bytes:
    patch_count = int.from_bytes(data[8:12], "little")
    offset = 16 + patch_count * PATCH_BYTES + mat_id * MAT_BYTES
    return data[offset : offset + MAT_BYTES]


def _rgb_slots(block: bytes) -> set[tuple[int, int, int]]:
    return {
        tuple(block[cell + rel : cell + rel + 3])
        for cell in range(0, MAT_BYTES, 8)
        for rel in (0, 4)
    }


def test_authored_grass_palette_is_preserved() -> None:
    data = MAP.read_bytes()
    blocks = [_material_block(data, mat_id) for mat_id in GRASS_MATERIAL_IDS]
    assert all(len(block) == MAT_BYTES for block in blocks)
    assert blocks[0] == blocks[1] == blocks[2]
    slots = _rgb_slots(blocks[0])
    assert slots == AUTHORED_GRASS_RAMP
    assert REJECTED_BROAD_RETINT_RAMP.isdisjoint(slots)


def test_foliage_clump_geometry_is_preserved() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "float clump = mix(0.44, 1.34, patch_a) * mix(0.86, 1.12, patch_b);" in source
    assert "foliage_fg *= clump;" in source
    assert "clump_warm" not in source
    assert "vec3(1.16, 1.08, 0.70)" not in source


def test_ground_grass_patch_hue_owners_are_deleted() -> None:
    """The grass ramp may swing hue GREEN-ONLY, and never at map scale.

    FL-4596's original defect had two independent causes: a light endpoint
    with starved blue (B=0.54) that read olive/yellow, and a coarse scale-6.0
    patch-noise lane that painted those tints as map-sized islands. The
    2026-07-16 repair banned hue variation entirely (identity vec3(1.0) ends);
    the 2026-08-28 operator falsifier "grass hue is all a similar shade of
    green with a tiny ramp" reversed that over-correction. The contract now
    pins the rebalance: a green-led swing whose blue stays near neutral, the
    coarse patch lane still dead, and only the cell-frequency lane restored --
    fine noise cannot aggregate into islands.
    """
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    # FL-4596 repair (2026-07-29): this pin was written with parentheses around
    # the family comparison. Commit d11bcfaed reformatted the line without them
    # while consolidating the ramp inputs into product_terrain_ramp_facts, and
    # the assertion has failed ever since -- which meant every assertion BELOW
    # it never ran, leaving the whole FL-4596 boundary silently unguarded. The
    # pin is now matched to the live source text.
    assert "float patch_w = family == TERRAIN_FAMILY_GRASS ? 0.0 : 0.08;" in source
    assert "float cell_w = family == TERRAIN_FAMILY_GRASS ? 0.09 : 0.04;" in source
    # The rejected FL-4596 tints stay banned everywhere, including the June
    # ramp light endpoint whose starved blue produced the olive read.
    assert "vec3(1.12, 1.18, 0.80)" not in source
    assert "vec3(0.72, 0.90, 0.66)" not in source
    assert "vec3(1.12, 1.16, 0.54)" not in source

    # FL-4596 review 2026-07-29, finding 3: the two hue pins below used to be
    # searched for across the WHOLE shader. That could false-pass -- moving the
    # neutral assignments into some other family's branch while giving grass a
    # different, non-neutral hue would still satisfy a whole-file search. They
    # are now scoped to the grass branch of the ramp function, which is the only
    # place they mean anything.
    grass = _grass_ramp_branch(source)
    assert "shadow_hue = vec3(0.30, 0.50, 0.22);" in grass
    assert "light_hue = vec3(1.02, 1.16, 0.86);" in grass
    # ...and every LIGHT endpoint assigned inside that branch must keep blue
    # near neutral relative to the G channel (B >= 0.7 * G). The FL-4596
    # olive/yellow defect was a bright-cell failure: the blue-starved LIT end
    # (June's B=0.54, deleted by d25809fce) read as yellow, so the property
    # binds light_hue only. The shadow end is dark, may stay cool and
    # low-blue, and is pinned exactly above.
    for assignment in re.findall(r"light_hue\s*=\s*vec3\(([^)]*)\);", grass):
        r, g, b = (float(c.strip()) for c in assignment.split(","))
        assert b >= 0.7 * g, (
            f"blue-starved grass light endpoint found: vec3({assignment})")

    # Dither owner (FL-4596 second pass, 2026-08-28): ~90% of the June-24
    # working point (bc21ac3c4: amp 0.105, weights 0.18/0.12). This is the only
    # per-cell colour lane alive at noon (the ramp is gated off above
    # raw01 = 3/7), so the half-June first pass left the meadow homogeneous.
    # Blue stays >= 0.7*G at the warm end so the olive/lime read cannot return.
    assert "vec3(1.10, 1.16, 0.86)" in source
    assert "vec3(0.76, 0.92, 0.80)" in source
    assert "fg_tint_weight = 0.15;" in source
    assert "bg_tint_weight = 0.10;" in source


def test_grass_ramp_depth_is_june_depth_in_both_registers() -> None:
    """FL-4183/RQ-133 (2026-08-30): BOTH registers share the June-14 depth.

    Operator direction 2026-08-30: the 2026-06-24 build (9b135d8e9) read with
    more height/slope 3D relief; restore that readability. This supersedes the
    2026-08-28 midpoint state, where FL-4431 step S4a kept the June-14 depth
    ORTHOGRAPHIC-only and left a relaxed flattening (amp x0.70, floor_always)
    on the perspective side. With the depth law now identical on both sides,
    the S4a projection split inside the grass branch was DELETED rather than
    kept as two copies of the same numbers -- so the contract pins one shared
    set of June-14 numbers (June-14 baseline ascii_compositor.glsl:3373-3380)
    and bans the split plus the perspective flattening residue from the grass
    branch.
    """
    grass = _grass_ramp_branch(_expand_includes(PRODUCT.read_text(encoding="utf-8")))

    # Shared June-14 depth: amplitude x1.30, slope darkening 0.22, shadow
    # floors 0.42/0.44 applied in the lowest band only (floor_always false).
    assert "amp *= 1.30;" in grass
    assert "slope_dark_scale = 0.22;" in grass
    assert "floor_fg_coef = 0.42" in grass
    assert "floor_bg_coef = 0.44" in grass
    assert "floor_always = false;" in grass

    # The deleted S4a split and the superseded perspective flattening must
    # not reappear inside the grass branch.
    assert "if (ortho_register) {" not in grass
    assert "amp *= 0.70;" not in grass
    assert "floor_always = true;" not in grass
    # Retained ban from the S4a review: depth must never key off a per-cell
    # paint-mode selector.
    assert "stable_cutover_paint" not in grass


def test_accepted_non_patch_palettes_are_preserved() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    effect = EFFECT.read_text(encoding="utf-8")
    # FL-4183/RQ-133 (2026-08-30): the olive-drifted stem pins
    # vec3(0.251, 0.318, 0.113) / vec3(0.482, 0.558, 0.225) were superseded by
    # the restored June-24 (9b135d8e9) CIE-Lab stem endpoints, so those pins
    # are retired and replaced with the June pair (#315A21 / #76A948).
    assert "vec3(0.192, 0.353, 0.129)" in product
    assert "vec3(0.463, 0.663, 0.282)" in product
    # vec3(0.50, 0.86, 0.18) was a legacy-tree-owner palette pin; that owner
    # was deliberately deleted by fb48c0fe3 (FL-4708), so the pin is retired
    # rather than asserted against a constant that no longer has an owner.
    # FL-4183/RQ-133 (2026-08-30): the grass fallback pins likewise moved from
    # the drifted vec3(0.420, 0.575, 0.210) / vec3(0.210, 0.345, 0.095) to the
    # June-24 fallback pair (#8AB94F / #406F27), mirrored in the host palette.
    assert "vec3(0.541, 0.726, 0.310)" in product
    assert "vec3(0.250, 0.434, 0.153)" in product
    assert "Color(0.541, 0.726, 0.310)" in effect
    assert "Color(0.250, 0.434, 0.153)" in effect


def main() -> int:
    test_authored_grass_palette_is_preserved()
    test_foliage_clump_geometry_is_preserved()
    test_ground_grass_patch_hue_owners_are_deleted()
    test_grass_ramp_depth_is_june_depth_in_both_registers()
    test_accepted_non_patch_palettes_are_preserved()
    print("FL4596_YELLOW_GRASS_PATCH_CONTRACT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
