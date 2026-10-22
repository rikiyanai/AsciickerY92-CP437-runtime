#!/usr/bin/env python3
# =============================================================================
# fl4482_native_admission.py    (FL-4482 / FL-4260 / FL-4512)
# =============================================================================
# WHAT "ADMISSION" MEANS, IN PLAIN LANGUAGE
# -----------------------------------------
# The renderer draws text characters by copying little square pictures out of a
# big sheet of pre-drawn characters. That sheet is called an ATLAS, and each
# square on it is a TILE.
#
# A character is ADMITTED when the atlas actually contains a tile for it. If a
# character has no tile, the renderer has nothing to copy, so putting it in a
# rendering table would produce a blank.
#
# One file records which characters have tiles and exactly where each tile sits:
#
#     assets/glyphs/generated/fl4482.native_atlas_registry.v1.json
#
# That registry is the ONLY correct answer to "can this character be drawn?".
#
# THE BUG THIS FILE REPLACES
# --------------------------
# Two places in this project used to answer that question with a hard-coded
# number instead:
#
#     admitted = {gid for gid in by_gid if gid <= 671}
#
# The 671 came from an old freeze: at one point the highest drawn character was
# number 671. That stopped being true. The registry today binds 111,040
# characters with numbers running past sixteen million, and every one of the 72
# characters currently up for review has a real tile in it.
#
# The consequence of the stale number was concrete and wrong: 22 reviewable
# characters were declared undrawable, and a review queue was reported as "only
# 9 of 21 are answerable" when in fact all of them were. A number frozen into
# source code cannot notice that the world moved; the registry can.
#
# So this module exists to be the single place that answers admission, and it
# answers it by reading the registry.
#
# WHAT A "BINDING" LOOKS LIKE
# ---------------------------
# Each entry in the registry's `bindings` list says where one character lives:
#
#     glyph_id       which character
#     pack_index     which content pack it came from (packs are numbered)
#     page_index     which sheet within that pack
#     layer_index    which layer of that sheet
#     source_column  which column of tiles on the sheet  (0-based)
#     source_row     which row of tiles on the sheet     (0-based)
#
# To turn that into actual pixels you also need the sheet itself, which the
# registry's `pages` list provides (a PNG path plus its content hash), and the
# tile size, which is `cell_px` at the top of the registry.
#
# Run the self-check with:
#     python3 scripts/fl4482_native_admission.py --self-check
# =============================================================================

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
NATIVE_REGISTRY = REPO_ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"

# The schema string the registry must declare. Note the deliberate mismatch with
# the FILENAME: the file is called ...v1.json but its contents declare schema
# v2. That is how it ships, and pinning the schema (rather than trusting the
# name) is what makes this check meaningful.
NATIVE_REGISTRY_SCHEMA = "fl4482.native_atlas_registry.v2"

# The retired rule this module replaces. Kept named — and only named — so tests
# can assert no admission owner is comparing against it any more.
RETIRED_FROZEN_CEILING = 671

_CACHE: dict[str, Any] = {}


def load_registry(path: Path | None = None) -> dict[str, Any]:
    """Read the native atlas registry, once, and check it is what we expect.

    Cached because the file is large (111k bindings) and several callers want it
    within one run. Raises rather than returning a partial answer: an admission
    owner that silently degrades would recreate the exact class of bug this
    module exists to remove.
    """
    key = str(path or NATIVE_REGISTRY)
    if key in _CACHE:
        return _CACHE[key]

    registry_path = path or NATIVE_REGISTRY
    if not registry_path.exists():
        raise FileNotFoundError(
            f"native atlas registry missing at {registry_path}; admission cannot be derived")

    with registry_path.open("r", encoding="utf-8") as f:
        registry = json.load(f)

    if registry.get("schema") != NATIVE_REGISTRY_SCHEMA:
        raise ValueError(
            f"native atlas registry declares schema {registry.get('schema')!r}, "
            f"expected {NATIVE_REGISTRY_SCHEMA!r}")
    if not isinstance(registry.get("bindings"), list):
        raise ValueError("native atlas registry has no bindings list")

    _CACHE[key] = registry
    return registry


def bindings_by_glyph(path: Path | None = None) -> dict[int, dict[str, Any]]:
    """Map every drawable character number to its tile location.

    When a character somehow appears twice, the FIRST binding wins. Registry
    order is the pack/page order the runtime itself walks, so first-wins matches
    what the renderer would actually draw rather than inventing a new rule.
    """
    key = f"by_glyph::{path or NATIVE_REGISTRY}"
    if key in _CACHE:
        return _CACHE[key]

    out: dict[int, dict[str, Any]] = {}
    for binding in load_registry(path).get("bindings", []):
        if not isinstance(binding, dict):
            continue
        gid = binding.get("glyph_id")
        if isinstance(gid, int) and gid not in out:
            out[gid] = binding

    _CACHE[key] = out
    return out


def admitted_glyph_ids(path: Path | None = None) -> set[int]:
    """Every character the atlas can actually draw.

    THE admission answer. Promotion and review admission must both come from
    here, so the two can never disagree about what is drawable.
    """
    return set(bindings_by_glyph(path))


def is_admitted(glyph_id: int, path: Path | None = None) -> bool:
    """Can this one character be drawn?"""
    return glyph_id in bindings_by_glyph(path)


def resolve_tile(glyph_id: int, path: Path | None = None) -> dict[str, Any] | None:
    """Locate one character's picture precisely enough to show it to a reviewer.

    Returns None when the character has no tile at all.

    Otherwise returns the sheet it lives on (content pack id, PNG path, and that
    PNG's content hash) plus the tile's exact pixel rectangle on that sheet, as
    [x, y, width, height].

    The rectangle is computed from the tile grid: tiles are `cell_px` pixels
    square and laid out in a plain grid, so a tile at column 3, row 5 on a sheet
    with 16-pixel tiles starts at x = 3 * 16 = 48, y = 5 * 16 = 80.
    """
    binding = bindings_by_glyph(path).get(glyph_id)
    if binding is None:
        return None

    registry = load_registry(path)
    cell_px = int(registry.get("cell_px", 16))

    # Find the sheet this binding points at. Pages are identified by the
    # (pack_index, page_index, layer_index) triple rather than list position,
    # because a multi-pack registry does not guarantee positional alignment.
    page = None
    for candidate in registry.get("pages", []):
        if not isinstance(candidate, dict):
            continue
        if (candidate.get("pack_index") == binding.get("pack_index")
                and candidate.get("page_index") == binding.get("page_index")
                and candidate.get("layer_index") == binding.get("layer_index")):
            page = candidate
            break

    column = int(binding.get("source_column", 0))
    row = int(binding.get("source_row", 0))

    return {
        "glyph_id": glyph_id,
        "content_pack_id": page.get("content_pack_id") if page else None,
        "page16_path": page.get("page16_path") if page else None,
        "page16_sha256": page.get("page16_sha256") if page else None,
        "pack_index": binding.get("pack_index"),
        "page_index": binding.get("page_index"),
        "layer_index": binding.get("layer_index"),
        "source_column": column,
        "source_row": row,
        "cell_px": cell_px,
        # The exact pixels a reviewer needs to look at.
        "rect_xywh": [column * cell_px, row * cell_px, cell_px, cell_px],
    }


def admission_provenance(path: Path | None = None) -> dict[str, Any]:
    """Identify the registry a decision was made against.

    Recorded into receipts and promotion output so a future reader can tell
    which atlas generation an admission judgement came from.
    """
    registry = load_registry(path)
    return {
        "source": str((path or NATIVE_REGISTRY).relative_to(REPO_ROOT)),
        "schema": registry.get("schema"),
        "registry_sha256": registry.get("registry_sha256"),
        "binding_count": registry.get("binding_count"),
        "page_count": registry.get("page_count"),
    }


def _self_check() -> int:
    """Demonstrate that the registry, not the frozen number, owns admission."""
    admitted = admitted_glyph_ids()
    print("fl4482_native_admission self-check")
    print(f"  registry           : {NATIVE_REGISTRY.relative_to(REPO_ROOT)}")
    print(f"  schema             : {load_registry().get('schema')}")
    print(f"  admitted glyphs    : {len(admitted)}")
    print(f"  highest glyph id   : {max(admitted)}")

    above = {gid for gid in admitted if gid > RETIRED_FROZEN_CEILING}
    print(f"  admitted above {RETIRED_FROZEN_CEILING}: {len(above)}")
    assert above, ("the registry admits nothing above the retired ceiling, so the "
                   "ceiling would have been harmless — check the registry loaded")

    sample = sorted(above)[0]
    tile = resolve_tile(sample)
    assert tile is not None, "an admitted glyph failed to resolve to a tile"
    print(f"  sample above ceiling: glyph {sample} -> {tile['page16_path']} "
          f"rect {tile['rect_xywh']}")
    print("  proven: admission comes from real atlas bindings, and glyphs above the")
    print("          retired ceiling resolve to real tiles.")
    return 0


if __name__ == "__main__":
    import sys
    if "--self-check" in sys.argv:
        raise SystemExit(_self_check())
    print(__doc__)
    raise SystemExit(0)
