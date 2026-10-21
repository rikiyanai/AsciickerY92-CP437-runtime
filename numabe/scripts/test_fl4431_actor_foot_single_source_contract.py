#!/usr/bin/env python3
"""Contract for the current actor-root fact and its two shader consumers.

The retired FL-4431 Product raster selector used a typed ``ActorRowFootFact``
seam. That seam no longer exists because Product only schedules candidates and
the compositor owns native-pixel actor visibility. The live law is one packed
actor-root decoder per shader stage, with every stage-local consumer receiving
that immutable root fact rather than rebuilding packed X/Y/Z words.

This source contract intentionally does not claim that Product pre-elects an
actor-owned ANSI cell. It checks the current candidate/publication split and
uses mutation controls to keep both root decoders and their consumers honest.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
COMPOSITOR = ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl"


def function(source: str, signature: str) -> str:
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
    return "".join(source.split())


def assert_product_root(source: str) -> None:
    root = compact(source and function(source, "vec3 actor_root_world("))
    expected = (
        "uintpfx=actor_cell_word(4,row);"
        "uintpfz=actor_cell_word(7,row);"
        "uintpfy=actor_cell_word(16,row);"
        "floatfx=float(pfx)/64.0-4096.0;"
        "floatfz=float(pfz)/64.0-4096.0;"
        "floatfy=float(pfy)/64.0-4096.0;"
        "returnvec3(fx,fy,fz);"
    )
    assert expected in root

    depth = compact(function(source, "float actor_candidate_view_depth("))
    effect = compact(function(source, "bool actor_attached_effect_schedules_cell("))
    publish = compact(function(source, "void publish_actor_candidates_for_cell("))
    for consumer, name in ((depth, "candidate depth"), (effect, "effect admission")):
        assert "actor_root_world(row)" in consumer, name
        for forbidden in (
            "actor_cell_word(4,row)",
            "actor_cell_word(7,row)",
            "actor_cell_word(16,row)",
        ):
            assert forbidden not in consumer, f"{name} rebuilt packed root: {forbidden}"
    assert "actor_candidate_view_depth(actor_row)" in publish
    for forbidden in (
        "actor_cell_word(4,row)",
        "actor_cell_word(7,row)",
        "actor_cell_word(16,row)",
    ):
        assert forbidden not in publish, f"candidate publisher rebuilt root: {forbidden}"


def assert_compositor_root(source: str) -> None:
    root = compact(function(source, "vec3 compositor_actor_root_world("))
    expected = (
        "returnvec3(float(actor_cell_word(4,row))/64.0-4096.0,"
        "float(actor_cell_word(16,row))/64.0-4096.0,"
        "float(actor_cell_word(7,row))/64.0-4096.0);"
    )
    assert expected in root

    hit = compact(function(source, "CompositorActorHit compositor_actor_hit("))
    assert "hit.root=compositor_actor_root_world(row);" in hit
    assert hit.count("compositor_actor_root_world(row)") == 1
    for forbidden in (
        "actor_cell_word(4,row)",
        "actor_cell_word(7,row)",
        "actor_cell_word(16,row)",
    ):
        assert forbidden not in hit, f"native hit rebuilt packed root: {forbidden}"


def assert_contract(product: str, compositor: str) -> None:
    assert_product_root(product)
    assert_compositor_root(compositor)


def mutation_controls(product: str, compositor: str) -> None:
    # Product's packed-word arithmetic is a single immutable fact. A changed
    # scale must be visible to the contract rather than hidden by equivalent
    # regrouping or a duplicate consumer.
    mutant = product.replace(
        "float fx = float(pfx) / 64.0 - 4096.0;",
        "float fx = float(pfx) / 32.0 - 4096.0;",
        1,
    )
    assert mutant != product
    try:
        assert_contract(mutant, compositor)
    except AssertionError:
        pass
    else:
        raise AssertionError("Product actor-root decode mutation was not caught")

    mutant = product.replace(
        "actor_root_world(row) - product_vsb_frame.camera_origin_near.xyz",
        "vec3(0.0) - product_vsb_frame.camera_origin_near.xyz",
        1,
    )
    assert mutant != product
    try:
        assert_contract(mutant, compositor)
    except AssertionError:
        pass
    else:
        raise AssertionError("Product actor-root consumer bypass was not caught")

    mutant = compositor.replace(
        "float(actor_cell_word(16, row)) / 64.0 - 4096.0,",
        "float(actor_cell_word(7, row)) / 64.0 - 4096.0,",
        1,
    )
    assert mutant != compositor
    try:
        assert_contract(product, mutant)
    except AssertionError:
        pass
    else:
        raise AssertionError("compositor actor-root decode mutation was not caught")

    mutant = compositor.replace(
        "hit.root = compositor_actor_root_world(row);",
        "hit.root = vec3(0.0);",
        1,
    )
    assert mutant != compositor
    try:
        assert_contract(product, mutant)
    except AssertionError:
        pass
    else:
        raise AssertionError("native hit root bypass was not caught")


def main() -> int:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert_contract(product, compositor)
    mutation_controls(product, compositor)
    print(
        "FL4431_ACTOR_FOOT_SINGLE_SOURCE_CONTRACT_PASS "
        "product_root_fact=true compositor_root_fact=true "
        "candidate_native_split=true mutations=4 runtime_claim=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
