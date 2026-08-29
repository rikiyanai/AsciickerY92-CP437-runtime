"""FL-4063 / Q6 ratchet: closed compiler-owned enum of per-cell partition
categories.

After scripts/lib/transparency_methods.py classifies a cell as "potentially
visible" (NOT structurally transparent per the XP's transparency_method),
the cell falls into one of these four categories. Author decisions live in
profiles.json `cell_partition_decisions` keyed by
(source_xp_id, source_layer_index, atlas_x, atlas_y).

Initial closed set:
    render_visible        — Claimed by a render role mask (Q3 partition
                            scope; Q6 only uses this as the default for
                            cells without an explicit decision).
    key_cell              — Transparent because XP convention says so.
                            Listed explicitly (not silently stripped) so
                            future readers see the intent. Distinct from
                            non_render for diagnostic clarity.
    non_render            — Visible/source cell intentionally excluded from
                            compose (file artifact, debug glyph,
                            never-rendered decoration, etc.).
    render_opaque_black   — Intentional black art/shadow/detail. Renders as
                            glyph=32 + fg=BLACK + bg=BLACK. Never
                            auto-stripped — author must opt in.

Q6 scope: every cell that the transparency_method classifies as visible AND
that violates the runtime transparency contract (yellow leak, black-on-black)
requires an explicit category. Compiler refuses to emit a row whose source
cell is undecided.

Q3 scope (later ratchet): will require EVERY reached cell (not just leaks)
to be partitioned by the render-role enum + non_render. This module is
shared infrastructure.

Per FL-4065 / Q8 addability guard: adding a new category requires a
diff-reviewed code change here, never a profiles.json edit. New categories
must be generic visual capabilities, not skin/mount/item-specific
exceptions.

NON_RENDER vs runtime role enum (FL-4060 / Q3 lock): non_render is an
authoring/validator partition category and DOES NOT appear in the runtime
CompiledActorVisualLayer role_enum. Runtime never composes a non_render
layer.
"""

from __future__ import annotations


CELL_CATEGORY_RENDER_VISIBLE = "render_visible"
CELL_CATEGORY_KEY_CELL = "key_cell"
CELL_CATEGORY_NON_RENDER = "non_render"
CELL_CATEGORY_RENDER_OPAQUE_BLACK = "render_opaque_black"

ALLOWED_CELL_PARTITION_CATEGORIES = frozenset({
    CELL_CATEGORY_RENDER_VISIBLE,
    CELL_CATEGORY_KEY_CELL,
    CELL_CATEGORY_NON_RENDER,
    CELL_CATEGORY_RENDER_OPAQUE_BLACK,
})

# Categories that mean "cell does not appear in the composed final pixel".
# Used by the pre-compose filter to skip cells before they enter compose.
NON_COMPOSED_CATEGORIES = frozenset({
    CELL_CATEGORY_KEY_CELL,
    CELL_CATEGORY_NON_RENDER,
})


def category_excludes_from_compose(category: str) -> bool:
    """True iff cells with this category should be filtered out before compose."""
    if category not in ALLOWED_CELL_PARTITION_CATEGORIES:
        raise ValueError(
            f"unknown cell_partition_category {category!r}; "
            f"allowed={sorted(ALLOWED_CELL_PARTITION_CATEGORIES)}"
        )
    return category in NON_COMPOSED_CATEGORIES


def decision_key(
    source_xp_id: str, source_layer_index: int, atlas_x: int, atlas_y: int
) -> tuple:
    """Canonical lookup tuple for cell_partition_decisions."""
    return (source_xp_id, int(source_layer_index), int(atlas_x), int(atlas_y))


def build_decision_index(
    decisions: list[dict],
) -> dict[tuple, dict]:
    """Build a {key -> decision} dict from the cell_partition_decisions list.
    Raises ValueError on duplicate keys (one cell, multiple decisions = bug)."""
    index: dict[tuple, dict] = {}
    for i, entry in enumerate(decisions):
        if not isinstance(entry, dict):
            raise ValueError(f"cell_partition_decisions[{i}] must be object")
        for field in ("source_xp_id", "source_layer_index", "atlas_x", "atlas_y", "category", "reason"):
            if field not in entry:
                raise ValueError(f"cell_partition_decisions[{i}] missing required field {field}")
        if entry["category"] not in ALLOWED_CELL_PARTITION_CATEGORIES:
            raise ValueError(
                f"cell_partition_decisions[{i}] category {entry['category']!r} not in "
                f"{sorted(ALLOWED_CELL_PARTITION_CATEGORIES)}"
            )
        if not isinstance(entry["reason"], str) or not entry["reason"].strip():
            raise ValueError(
                f"cell_partition_decisions[{i}] reason must be non-empty string"
            )
        key = decision_key(
            entry["source_xp_id"],
            entry["source_layer_index"],
            entry["atlas_x"],
            entry["atlas_y"],
        )
        if key in index:
            raise ValueError(
                f"cell_partition_decisions[{i}] duplicate key {key}; one cell can have "
                "exactly one category"
            )
        index[key] = entry
    return index
