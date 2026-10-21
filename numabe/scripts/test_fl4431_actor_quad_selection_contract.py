#!/usr/bin/env python3
"""Contract for the post-cutover actor candidate and native-pixel owners.

The historical version of this test asserted a Product-side 4-slot actor
raster selector. That selector was retired by the retained-scene cutover.
Product now publishes a bounded, depth-sorted candidate schedule. The
compositor owns the exact native-pixel ray, atlas, waterline, and world-depth
decision, then records the actor owner after painting the world underlay.

This is source and mutation evidence only. It does not claim runtime pixels or
frame-rate behavior.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
COMPOSITOR = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"


def function(source: str, signature: str) -> str:
    """Extract one GLSL function without inheriting neighboring owners."""
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def compact(source: str) -> str:
    return re.sub(r"\s+", "", source)


def contract_failures(product: str, compositor: str) -> list[str]:
    failures: list[str] = []
    publish = function(product, "void publish_actor_candidates_for_cell(")
    product_main = function(product, "void main() {")
    fragment = function(
        compositor,
        "CompositorActorFragment compositor_actor_fragment_for_cell(",
    )
    compositor_main = function(compositor, "void main() {")
    compact_publish = compact(publish)
    compact_fragment = compact(fragment)
    compact_main = compact(compositor_main)

    # Product's current actor owner is admission and ordering only. It must not
    # acquire a native ray, atlas lookup, or final paint decision.
    for token in (
        "actor_schedule_rect_overlaps_cell(",
        ")&&!actor_attached_effect_schedules_cell(",
        "actor_candidate_view_depth(actor_row)",
        "actor_candidate_product.values[base+insert_at+1u]",
        "while(insert_at>0u)",
        "actor_candidate_view_depth(previous_row)<=depth",
        "actor_candidate_product.values[base]=count",
    ):
        if token not in compact_publish:
            failures.append(f"candidate publication law missing: {token}")
    for token in (
        "scene_query_",
        "compositor_actor_hit(",
        "actor_standee",
        "record_final_cell_paint(",
        "imageStore(",
    ):
        if token in compact_publish:
            failures.append(f"Product candidate schedule gained raster owner: {token}")
    if compact(product_main).count("publish_actor_candidates_for_cell(") != 1:
        failures.append("Product main must publish actor candidates exactly once")

    # The compositor consumes the immutable schedule and performs the exact
    # per-pixel actor election. World depth is an immutable precomputed context
    # shared by candidates, not a Product-side actor ANSI-cell election.
    for token in (
        "actor_candidate_product.values[base+candidate+1u]",
        "compositor_actor_ray_from_pixel(",
        "compositor_actor_hit(row,ray)",
        "fl4346_prepare_world_depth_context(",
        "if(!fl4346_actor_survives_world_depth(",
        "fl4346_actor_survives_world_depth(",
        "actor_hit_survives_water_surface(hit)",
        "fragment.valid=true",
        "fragment.row=row",
    ):
        if token not in compact_fragment:
            failures.append(f"native-pixel actor law missing: {token}")
    if "vsb_product_select_actor_paint" in compact_fragment:
        failures.append("retired Product actor raster selector returned")

    owner_start = compact_main.find("if(actor_fragment.valid)")
    record_start = compact_main.find("record_final_cell_paint(", owner_start)
    if owner_start < 0 or record_start < 0 or owner_start >= record_start:
        failures.append("final actor owner must be established before paint receipt")
    else:
        owner_block = compact_main[owner_start:record_start]
        for token in (
            "painted_owner_kind=CO_OWNER_ACTOR_SPRITE;",
            "painted_stable_id=uint(actor_fragment.row+1);",
            "painted_anchor_xz=actor_fragment.root.xz;",
        ):
            if token not in owner_block:
                failures.append(f"final actor publication law missing: {token}")
    if compact_main.count("compositor_actor_fragment_for_cell(") != 1:
        failures.append("compositor main must invoke native actor election once")
    return failures


def assert_contract(product: str, compositor: str) -> None:
    failures = contract_failures(product, compositor)
    if failures:
        raise AssertionError("\n".join(failures))


def mutation_controls(product: str, compositor: str) -> None:
    """Each retained owner seam has a source mutation that turns it red."""
    publish_call = (
        "publish_actor_candidates_for_cell(cell_index, ansi_xy);"
    )
    mutant = product.replace(publish_call, "/* candidate publication removed */", 1)
    assert mutant != product
    try:
        assert_contract(mutant, compositor)
    except AssertionError:
        pass
    else:
        raise AssertionError("candidate publication removal was not caught")

    mutant = product.replace(
        "&& !actor_attached_effect_schedules_cell(",
        "&& false && !actor_attached_effect_schedules_cell(",
        1,
    )
    assert mutant != product
    try:
        assert_contract(mutant, compositor)
    except AssertionError:
        pass
    else:
        raise AssertionError("attached-effect admission removal was not caught")

    mutant = compositor.replace(
        "painted_owner_kind = CO_OWNER_ACTOR_SPRITE;",
        "painted_owner_kind = owner_kind;",
        1,
    )
    assert mutant != compositor
    try:
        assert_contract(product, mutant)
    except AssertionError:
        pass
    else:
        raise AssertionError("actor owner publication removal was not caught")

    mutant = compositor.replace(
        "if (!fl4346_actor_survives_world_depth(",
        "if (true || !fl4346_actor_survives_world_depth(",
        1,
    )
    assert mutant != compositor
    try:
        assert_contract(product, mutant)
    except AssertionError:
        pass
    else:
        raise AssertionError("native world-depth gate removal was not caught")


def main() -> int:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert_contract(product, compositor)
    mutation_controls(product, compositor)
    print(
        "FL4431_ACTOR_QUAD_SELECTION_CONTRACT_PASS "
        "product_candidate_schedule=true native_pixel_election=true "
        "final_owner_publication=true mutations=4 runtime_claim=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
