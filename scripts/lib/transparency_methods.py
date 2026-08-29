"""FL-4063 / Q6 ratchet: closed compiler-owned enum of per-XP transparency methods.

Each source_xp_manifest entry MUST declare a transparency_method. The
compiler/validator applies the declared method to every reached cell of
that XP; remaining yellow/black leaks require an explicit per-cell
author decision (see scripts/lib/cell_partition_categories.py).

Initial closed set:
    legacy_yellow_key   — L0 yellow cells mark transparent in L_n (upstream
                          msokalski/asciicker convention).
    none                — XP carries no transparency convention; every cell
                          renders opaque per its glyph/fg/bg.

Per FL-4065 / Q8 addability guard: adding a new method requires a
diff-reviewed code change here, NOT a profiles.json edit. New methods are
allowed only when they represent a generic visual capability (a new
transparency CONVENTION), never a per-skin/per-mount/per-item exception.

NOT in initial set: l0_inverse_mask. Re-add only when a real upstream XP
proves that legacy_yellow_key + per-cell decisions cannot express its
transparency truthfully.
"""

from __future__ import annotations


TRANSPARENCY_METHOD_LEGACY_YELLOW_KEY = "legacy_yellow_key"
TRANSPARENCY_METHOD_NONE = "none"

ALLOWED_TRANSPARENCY_METHODS = frozenset({
    TRANSPARENCY_METHOD_LEGACY_YELLOW_KEY,
    TRANSPARENCY_METHOD_NONE,
})

LEGACY_YELLOW_KEY_RGB = (255, 255, 85)
BLACK_RGB = (0, 0, 0)
MAGIC_PINK_RGB = (255, 0, 255)


def cell_is_legacy_yellow_key_transparent(
    layer0_cell_bg_rgb: tuple[int, int, int] | None,
) -> bool:
    """True iff the legacy convention says the cell at this atlas position
    is transparent (L0 background is the legacy yellow key)."""
    if layer0_cell_bg_rgb is None:
        return False
    return tuple(layer0_cell_bg_rgb) == LEGACY_YELLOW_KEY_RGB


def cell_visible_under_method(
    *,
    method: str,
    glyph: int,
    fg_rgb: tuple[int, int, int],
    bg_rgb: tuple[int, int, int],
    layer0_cell_bg_rgb: tuple[int, int, int] | None,
) -> bool:
    """Apply the declared method strictly. Returns True iff the cell is
    potentially visible (i.e., NOT structurally transparent per the method).

    legacy_yellow_key: cells whose bg matches the L0 key color (typically
    (255,255,85)) are key_cell per the upstream convention — fully
    transparent, including any glyph painted on top. Magic-pink bg
    (255,0,255) is the universal transparency sentinel and is also
    treated as key_cell. The author who declared transparency_method=
    legacy_yellow_key has thereby declared that L0-bg-matching cells are
    transparent; the compiler applying the method is the explicit
    classification — NOT a silent strip.

    none: no transparency convention; every cell is opaque per its
    declared colors. Upstream content gaps that would leak under this
    method must be explicitly classified via cell_partition_decisions.

    Cells returned True by this function may still need an explicit
    cell_partition_decision if they violate the runtime transparency
    contract (yellow leak / black-on-black) despite passing the method's
    structural visibility test.
    """
    if method == TRANSPARENCY_METHOD_LEGACY_YELLOW_KEY:
        bg = tuple(bg_rgb)
        l0_bg = tuple(layer0_cell_bg_rgb) if layer0_cell_bg_rgb is not None else None
        if bg == MAGIC_PINK_RGB:
            return False
        if l0_bg is not None and bg == l0_bg:
            return False
        return True
    if method == TRANSPARENCY_METHOD_NONE:
        return True
    raise ValueError(
        f"unknown transparency_method {method!r}; "
        f"allowed={sorted(ALLOWED_TRANSPARENCY_METHODS)}"
    )


def cell_is_yellow_leak(
    fg_rgb: tuple[int, int, int],
    bg_rgb: tuple[int, int, int],
) -> bool:
    """True iff the cell's color is legacy yellow in fg or bg. A cell that's
    visible AND yellow violates the runtime transparency contract — the
    author must classify it via cell_partition_decisions."""
    return (
        tuple(fg_rgb) == LEGACY_YELLOW_KEY_RGB
        or tuple(bg_rgb) == LEGACY_YELLOW_KEY_RGB
    )


def cell_is_black_on_black(
    glyph: int,
    fg_rgb: tuple[int, int, int],
    bg_rgb: tuple[int, int, int],
) -> bool:
    """True iff the cell is opaque black-on-black (glyph=space + black fg +
    black bg). Per Q6 lock: this is NEVER auto-transparent. Author must
    explicitly classify via cell_partition_decisions."""
    return (
        glyph == 32
        and tuple(fg_rgb) == BLACK_RGB
        and tuple(bg_rgb) == BLACK_RGB
    )
