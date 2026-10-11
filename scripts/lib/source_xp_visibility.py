"""Source XP visibility helpers for ActorVisualProfile validation.

Q3's partition rule is evaluated in source-XP atlas coordinates. These
helpers intentionally do not decide ownership; they only enumerate candidate
cells after the source XP's declared transparency method and explicit Q6
cell partition decisions have been applied.
"""

from __future__ import annotations

from pathlib import Path

from scripts.pipeline.xp_core import XPFile
from scripts.lib import cell_partition_categories
from scripts.lib import transparency_methods


def load_xp(path: Path) -> XPFile:
    return XPFile(str(path))


def layer0_bg_rgb(xp: XPFile, x: int, y: int) -> tuple[int, int, int] | None:
    if not xp.layers:
        return None
    layer0 = xp.layers[0]
    if y < 0 or y >= layer0.height or x < 0 or x >= layer0.width:
        return None
    return tuple(layer0.data[y][x][2])


def visible_cells_after_transparency(
    *,
    xp: XPFile,
    source_xp_id: str,
    source_layer_index: int,
    transparency_method: str,
    decision_index: dict[tuple, dict] | None = None,
) -> dict[tuple[int, int], tuple[int, tuple[int, int, int], tuple[int, int, int]]]:
    """Return visible atlas cells after Q6 transparency decisions.

    Keys are atlas (x, y) positions in the XP layer. Values are
    (glyph, fg_rgb, bg_rgb). `key_cell` and `non_render` decisions are
    excluded; `render_opaque_black` is retained as explicit visible black art.
    """
    if source_layer_index < 0 or source_layer_index >= len(xp.layers):
        raise ValueError(
            f"source layer {source_layer_index} outside XP layer count {len(xp.layers)}"
        )
    layer = xp.layers[source_layer_index]
    decisions = decision_index or {}
    visible: dict[tuple[int, int], tuple[int, tuple[int, int, int], tuple[int, int, int]]] = {}
    for y, row in enumerate(layer.data):
        for x, cell in enumerate(row):
            glyph = int(cell[0])
            fg = tuple(cell[1])
            bg = tuple(cell[2])
            if not transparency_methods.cell_visible_under_method(
                method=transparency_method,
                glyph=glyph,
                fg_rgb=fg,
                bg_rgb=bg,
                layer0_cell_bg_rgb=layer0_bg_rgb(xp, x, y),
            ):
                continue
            decision = decisions.get(
                cell_partition_categories.decision_key(
                    source_xp_id, source_layer_index, x, y
                )
            )
            if decision is not None:
                category = decision["category"]
                if cell_partition_categories.category_excludes_from_compose(category):
                    continue
                if category == cell_partition_categories.CELL_CATEGORY_RENDER_OPAQUE_BLACK:
                    visible[(x, y)] = (32, (0, 0, 0), (0, 0, 0))
                    continue
            visible[(x, y)] = (glyph, fg, bg)
    return visible
