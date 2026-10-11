#!/usr/bin/env python3
"""FL-4137 Gap B (review fix) — preview valid/invalid affordance proof.

Two layers:

  Layer 1 (source-shape):
    - authoritative_world_item_appearance.h carries the additive Gap B fields:
      placed, half_extent, height, preview_valid, preview_reason, plus the
      AuthoritativeWorldItemPreviewReason enum (NONE / OK /
      BLOCKED_PLACED_OVERLAP only — STACK_CAP removed, see review-fix note).
    - authoritative_world_item_appearance.cpp wires the snapshot helper, the
      validity helper, the per-frame placed-snapshot collection, the per-row
      fill of the new fields, and the held-preview INST_VISIBLE toggle.
    - CollectClientPlacedItemColliders FILTERS by APPEARANCE_ITEM_STATE_PLACED
      AND APPEARANCE_ITEM_STATE_COLLIDABLE (not owner_id alone), matching the
      server's SVR_PLACED_ITEM_COLLIDABLE gate.
    - row.placed is derived from APPEARANCE_ITEM_STATE_PLACED, not
      owner_id == 0xffff (dropped loot is owner_id==0xffff but not placed).
    - EvaluateHeldPreviewPlacementValidity mirrors server ORDER: lift candidate
      Z first (with cap), then overlap-check at the lifted Z. The earlier
      overlap-first ordering produced false-red on every same-XY stacked
      placement the server would have accepted.
    - The client's stack-cap constant exists and is the same MVP value as the
      server's SVR_PLACE_MAX_STACK_LAYERS (lockstep change required).
    - SvrPlaceOwnedItemFromPlayer remains the sole writer of placed pos /
      state (the placement writer signature and SVR_PLACE_MAX_STACK_LAYERS
      constant are unchanged by this commit).
    - INST_VISIBLE is the only inst flag toggled by the held-preview path
      (no INST_USE_TREE / INST_VOLATILE / authority touch).

  Layer 2 (arithmetic):
    - Python re-derivation of the client mirror in server order (lift-then-
      overlap). Tested with the legacy_yy_block parameters (radius=2,
      height=16, max_stack_layers=4) against six cases including the
      false-green regression: a single block under the target XY at the
      player's Z must now report OK because the mirror lifts candidate_z
      above the existing top before the overlap check runs.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
APPEARANCE_H = REPO_ROOT / "engine" / "authoritative_world_item_appearance.h"
APPEARANCE_CPP = REPO_ROOT / "engine" / "authoritative_world_item_appearance.cpp"
SERVER_TICK_CPP = REPO_ROOT / "server" / "server_tick.cpp"
PROTOCOL_COMMON_H = REPO_ROOT / "server" / "protocol" / "protocol_common.h"


def fail(msg: str) -> None:
    print(f"proof_fl4137_gap_b_preview_validity: FAIL -- {msg}")
    sys.exit(1)


def assert_contains(path: Path, pattern: str, why: str) -> None:
    text = path.read_text()
    if re.search(pattern, text) is None:
        fail(f"missing in {path.name}: {why}\n  pattern: {pattern}")


def assert_absent(path: Path, pattern: str, why: str) -> None:
    text = path.read_text()
    if re.search(pattern, text) is not None:
        fail(f"unexpected in {path.name}: {why}\n  pattern: {pattern}")


def source_shape_checks() -> None:
    # Header: enum (without STACK_CAP) + additive fields.
    assert_contains(
        APPEARANCE_H,
        r"enum AuthoritativeWorldItemPreviewReason",
        "preview-reason enum",
    )
    for sym in (
        "PREVIEW_PLACEMENT_REASON_NONE",
        "PREVIEW_PLACEMENT_REASON_OK",
        "PREVIEW_PLACEMENT_REASON_BLOCKED_PLACED_OVERLAP",
    ):
        assert_contains(APPEARANCE_H, sym, f"enum value {sym}")
    assert_absent(
        APPEARANCE_H,
        r"PREVIEW_PLACEMENT_REASON_BLOCKED_STACK_CAP",
        "STACK_CAP enum value must be removed (server vocabulary has no distinct cap reason)",
    )
    for field in (
        r"uint8_t placed;",
        r"uint8_t preview_valid;",
        r"uint8_t preview_reason;",
        r"float half_extent;",
        r"float height;",
    ):
        assert_contains(APPEARANCE_H, field, f"row field {field}")

    # CPP: helpers exist with correct shape.
    assert_contains(
        APPEARANCE_CPP,
        r"static const int AUTHORITATIVE_WORLD_ITEM_PREVIEW_MAX_STACK_LAYERS\s*=\s*4;",
        "client preview stack-layer cap constant",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"struct AuthoritativeWorldItemPlacedSnapshot",
        "placed-snapshot struct",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"static int CollectClientPlacedItemColliders\(",
        "snapshot collector",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"static uint8_t EvaluateHeldPreviewPlacementValidity\(",
        "validity helper",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"static bool SnapHeldPreviewXYToExistingBlockGrid\(",
        "held preview snap-to-existing-block-grid helper",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"pos\[0\]\s*=\s*floorf\(pos\[0\]\)\s*\+\s*0\.5f;",
        "held preview X snaps to server grid center before validity",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"SnapHeldPreviewXYToExistingBlockGrid\s*\(",
        "held preview applies same snap-to-existing-grid affordance as server",
    )

    # CPP: protocol_common header is pulled in for the flag constants.
    assert_contains(
        APPEARANCE_CPP,
        r'#include "server/protocol/protocol_common\.h"',
        "protocol_common header pulled in for APPEARANCE_ITEM_STATE_* flags",
    )

    # CPP: collector iterates the server-owned authoritative item list and
    # filters by owner_id==0xffff PLUS the PLACED+COLLIDABLE mask.
    assert_contains(
        APPEARANCE_CPP,
        r"server->authority\.auth_item\.items\[i\]",
        "snapshot reads server-owned authoritative item list",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"ai->owner_id\s*!=\s*0xffff",
        "snapshot filters by world-owned ownership marker",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"APPEARANCE_ITEM_STATE_PLACED\s*\|\s*APPEARANCE_ITEM_STATE_COLLIDABLE",
        "snapshot requires PLACED + COLLIDABLE flags (mirrors SVR_PLACED_ITEM_COLLIDABLE)",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"ai->v2_state_flags\s*&\s*placed_collidable_mask",
        "snapshot uses the placed/collidable mask to reject loot/non-blocks",
    )

    # CPP: validity helper mirrors server ORDER — lift-first (with cap),
    # then overlap-at-lifted-Z. The lift loop must appear before the
    # overlap loop, and the overlap loop must use candidate_z / candidate_top,
    # not preview_pos[2] / preview_top.
    cpp_text = APPEARANCE_CPP.read_text()
    helper_m = re.search(
        r"static uint8_t EvaluateHeldPreviewPlacementValidity\([\s\S]*?\n\}\n",
        cpp_text,
    )
    if helper_m is None:
        fail("could not extract EvaluateHeldPreviewPlacementValidity body")
    body = helper_m.group(0)

    lift_idx = body.find("candidate_z = top_z")
    overlap_idx = body.find("PREVIEW_PLACEMENT_REASON_BLOCKED_PLACED_OVERLAP")
    if lift_idx < 0:
        fail("validity helper missing candidate_z lift assignment")
    if overlap_idx < 0:
        fail("validity helper missing overlap reason emission")
    if not (lift_idx < overlap_idx):
        fail("validity helper must lift candidate_z BEFORE the overlap check (server order)")

    assert_contains(
        APPEARANCE_CPP,
        r"candidate_top\s*<=\s*c\.pos\[2\]\s*\|\|\s*candidate_z\s*>=\s*other_top",
        "overlap vertical-band check uses LIFTED candidate_z / candidate_top",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"dx\s*\*\s*dx\s*\+\s*dy\s*\*\s*dy\s*>=\s*min_dist\s*\*\s*min_dist",
        "XY radius check mirrors server SvrPlacedBlockPositionOccupied",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"player_z\s*\+\s*max_stack_lift",
        "stack-cap is anchored to the player's feet",
    )

    # CPP: row.placed reads APPEARANCE_ITEM_STATE_PLACED, not owner_id alone.
    assert_contains(
        APPEARANCE_CPP,
        r"row\.placed\s*=\s*\(ai->v2_state_flags\s*&\s*APPEARANCE_ITEM_STATE_PLACED\)\s*\?\s*1\s*:\s*0;",
        "row.placed is APPEARANCE_ITEM_STATE_PLACED (not owner_id)",
    )
    assert_absent(
        APPEARANCE_CPP,
        r"row\.placed\s*=\s*\(ai->owner_id\s*==\s*0xffff\)",
        "row.placed must NOT be derived from owner_id alone",
    )

    # CPP: per-frame snapshot is collected once before the row loop.
    assert_contains(
        APPEARANCE_CPP,
        r"placed_snapshot\[\s*AUTHORITATIVE_WORLD_ITEM_PLACED_SNAPSHOT_CAP\s*\]",
        "per-frame snapshot buffer",
    )
    assert_contains(
        APPEARANCE_CPP,
        r"const int placed_snapshot_count\s*=",
        "per-frame snapshot count",
    )

    # CPP: row fill writes the new fields.
    for assignment in (
        r"row\.placed\s*=",
        r"row\.half_extent\s*=",
        r"row\.height\s*=",
        r"row\.preview_valid\s*=",
        r"row\.preview_reason\s*=",
    ):
        assert_contains(APPEARANCE_CPP, assignment, f"row assignment {assignment}")

    # CPP: visibility toggle ONLY on the held preview, using ShowInst/HideInst.
    assert_contains(
        APPEARANCE_CPP,
        r"if\s*\(held_placeable_preview\)\s*\{\s*\n\s*if\s*\(preview_valid\)\s*\n\s*ShowInst\(vis->inst\);\s*\n\s*else\s*\n\s*HideInst\(vis->inst\);",
        "visibility toggle is held-preview-scoped",
    )

    # SERVER: writer signature unchanged + cap constant unchanged. These
    # are the Gap C invariants Gap B must NOT touch.
    assert_contains(
        SERVER_TICK_CPP,
        r"static bool SvrPlaceOwnedItemFromPlayer\(ServerState\* state,",
        "SvrPlaceOwnedItemFromPlayer signature unchanged",
    )
    assert_contains(
        SERVER_TICK_CPP,
        r"static constexpr int SVR_PLACE_MAX_STACK_LAYERS\s*=\s*4;",
        "server stack cap unchanged at 4 (must match client mirror)",
    )

    # PROTOCOL: confirm the flag bits we depend on are present + stable.
    assert_contains(
        PROTOCOL_COMMON_H,
        r"#define APPEARANCE_ITEM_STATE_PLACED\s+0x0008",
        "APPEARANCE_ITEM_STATE_PLACED bit",
    )
    assert_contains(
        PROTOCOL_COMMON_H,
        r"#define APPEARANCE_ITEM_STATE_COLLIDABLE\s+0x0010",
        "APPEARANCE_ITEM_STATE_COLLIDABLE bit",
    )

    # CPP: no new authority-side surface. The Gap B file must not emit a
    # placement intent itself (kind = ITEM_ACTION_REQ_PLACE assignment),
    # must not call SvrPlaceOwned*, and must not register the preview in
    # world_pickup_rows. Comment occurrences of the token are fine — the
    # invariant is "no new emitter/sender", not "no mention".
    assert_absent(
        APPEARANCE_CPP,
        r"\.kind\s*=\s*ITEM_ACTION_REQ_PLACE",
        "no new placement intent emitter in the appearance file",
    )
    # Forbid CALL syntax `Name(` (no whitespace before paren). Comment prose
    # writes `Name (server_tick.cpp:...)` with a space, which is fine.
    assert_absent(
        APPEARANCE_CPP,
        r"SvrPlaceOwnedItemFromPlayer\(",
        "appearance file must not call the server placement writer",
    )

    print("proof_fl4137_gap_b_preview_validity: source-shape OK")


# -- Layer 2: Python re-derivation -------------------------------------------


def evaluate_preview(
    player_z: float,
    preview_pos,
    half_extent: float,
    height: float,
    preview_item_id: int,
    placed,
    max_layers: int = 4,
):
    """Python mirror of EvaluateHeldPreviewPlacementValidity in cpp.

    Server order: lift candidate_z first (capped), then overlap-check at the
    lifted Z. Returns server-compatible reason strings.
    """
    if half_extent <= 0.0 or height <= 0.0:
        return "OK"

    # Step 1: lift candidate_z.
    max_lift = height * max_layers
    candidate_z = preview_pos[2]
    for c in placed:
        if c["item_id"] == preview_item_id:
            continue
        if abs(c["pos"][0] - preview_pos[0]) > c["half_extent"]:
            continue
        if abs(c["pos"][1] - preview_pos[1]) > c["half_extent"]:
            continue
        top_z = c["pos"][2] + c["height"]
        if top_z <= candidate_z:
            continue
        if top_z > player_z + max_lift:
            continue
        candidate_z = top_z

    # Step 2: overlap-check at the lifted candidate_z.
    candidate_top = candidate_z + height
    for c in placed:
        if c["item_id"] == preview_item_id:
            continue
        min_dist = half_extent + c["half_extent"]
        dx = c["pos"][0] - preview_pos[0]
        dy = c["pos"][1] - preview_pos[1]
        if dx * dx + dy * dy >= min_dist * min_dist:
            continue
        other_top = c["pos"][2] + c["height"]
        if candidate_top <= c["pos"][2] or candidate_z >= other_top:
            continue
        return "BLOCKED_PLACED_OVERLAP"
    return "OK"


def arithmetic_checks() -> None:
    cases = []

    # Case 1: floor placement, no blocks in the way.
    cases.append(
        (
            "floor_empty",
            dict(
                player_z=55.0,
                preview_pos=(10.5, 10.5, 55.0),
                half_extent=2.0,
                height=16.0,
                preview_item_id=42,
                placed=[],
            ),
            "OK",
        )
    )

    # Case 2 — FALSE-GREEN REGRESSION (the bug that triggered the review fix):
    # preview starts at player_z; one existing block at same XY at the same Z.
    # Old client mirror (overlap-first) would have returned BLOCKED_PLACED_OVERLAP.
    # New mirror lifts candidate_z above the existing top and returns OK,
    # matching the server SvrPlaceOwnedItemFromPlayer outcome.
    cases.append(
        (
            "false_green_regression_lifts_above_existing",
            dict(
                player_z=55.0,
                preview_pos=(10.5, 10.5, 55.0),
                half_extent=2.0,
                height=16.0,
                preview_item_id=42,
                placed=[
                    {"pos": (10.5, 10.5, 55.0), "half_extent": 2.0, "height": 16.0, "item_id": 99},
                ],
            ),
            "OK",
        )
    )

    # Case 3: cap-exceeded. Five one-cell blocks stacked; the lift loop caps at
    # the 4th top (z=119 = 55+4*16); the 5th block still occupies z=119..135, so
    # the overlap check at the lifted Z fails. Reason matches the server
    # SvrPlacedBlockPositionOccupied "placed_block_overlap" emission.
    cases.append(
        (
            "cap_exceeded_falls_through_to_overlap_at_lifted_z",
            dict(
                player_z=55.0,
                preview_pos=(10.5, 10.5, 55.0),
                half_extent=2.0,
                height=16.0,
                preview_item_id=42,
                placed=[
                    {"pos": (10.5, 10.5, 55.0), "half_extent": 2.0, "height": 16.0, "item_id": 90},
                    {"pos": (10.5, 10.5, 71.0), "half_extent": 2.0, "height": 16.0, "item_id": 91},
                    {"pos": (10.5, 10.5, 87.0), "half_extent": 2.0, "height": 16.0, "item_id": 92},
                    {"pos": (10.5, 10.5, 103.0), "half_extent": 2.0, "height": 16.0, "item_id": 93},
                    {"pos": (10.5, 10.5, 119.0), "half_extent": 2.0, "height": 16.0, "item_id": 94},
                ],
            ),
            "BLOCKED_PLACED_OVERLAP",
        )
    )

    # Case 4: different-XY placement is always OK regardless of stack.
    cases.append(
        (
            "different_xy_ok",
            dict(
                player_z=55.0,
                preview_pos=(20.5, 10.5, 55.0),
                half_extent=2.0,
                height=16.0,
                preview_item_id=42,
                placed=[
                    {"pos": (10.5, 10.5, 55.0), "half_extent": 2.0, "height": 16.0, "item_id": 99},
                ],
            ),
            "OK",
        )
    )

    # Case 5 — DROPPED / WORLD-OWNED NON-PLACED ITEM. The collector C++
    # filter excludes items without PLACED|COLLIDABLE flags, so a dropped
    # sword (owner_id==0xffff, flags missing) never enters the snapshot. The
    # Python helper here is fed an empty placed list to model that, and a
    # comment in the source-shape layer asserts the C++ filter mask. This
    # case proves the validity helper itself never false-reds when the
    # snapshot is empty.
    cases.append(
        (
            "dropped_loot_excluded_by_collector_filter",
            dict(
                player_z=55.0,
                preview_pos=(10.5, 10.5, 55.0),
                half_extent=2.0,
                height=16.0,
                preview_item_id=42,
                placed=[],  # collector excludes non-PLACED|COLLIDABLE rows
            ),
            "OK",
        )
    )

    # Case 6: vertical-band disjoint stays valid when the lift loop can
    # reach the top. Two blocks stacked: player at z=55, blocks at 55+71.
    # Lift loop bumps candidate_z to 87. Overlap check at z=87..103 against
    # blocks (55..71) and (71..87) -> bands disjoint -> OK.
    cases.append(
        (
            "stacked_above_two_blocks_ok",
            dict(
                player_z=55.0,
                preview_pos=(10.5, 10.5, 55.0),
                half_extent=2.0,
                height=16.0,
                preview_item_id=42,
                placed=[
                    {"pos": (10.5, 10.5, 55.0), "half_extent": 2.0, "height": 16.0, "item_id": 90},
                    {"pos": (10.5, 10.5, 71.0), "half_extent": 2.0, "height": 16.0, "item_id": 91},
                ],
            ),
            "OK",
        )
    )

    passed = 0
    for name, kwargs, expected in cases:
        got = evaluate_preview(**kwargs)
        if got != expected:
            fail(f"arithmetic case {name}: expected {expected} got {got}")
        passed += 1
    print(f"proof_fl4137_gap_b_preview_validity: arithmetic {passed}/{len(cases)} cases OK")


def lockstep_cap_check() -> None:
    """Client preview cap MUST equal server SVR_PLACE_MAX_STACK_LAYERS."""
    client_m = re.search(
        r"AUTHORITATIVE_WORLD_ITEM_PREVIEW_MAX_STACK_LAYERS\s*=\s*(\d+)",
        APPEARANCE_CPP.read_text(),
    )
    server_m = re.search(
        r"SVR_PLACE_MAX_STACK_LAYERS\s*=\s*(\d+)",
        SERVER_TICK_CPP.read_text(),
    )
    if not client_m or not server_m:
        fail("could not locate stack-cap constants on both sides")
    if client_m.group(1) != server_m.group(1):
        fail(
            f"stack cap mismatch: client={client_m.group(1)} server={server_m.group(1)}"
        )
    if client_m.group(1) != "4":
        fail(f"stack cap drifted from MVP value 4 (now {client_m.group(1)})")
    print(f"proof_fl4137_gap_b_preview_validity: stack cap lockstep OK (client={server_m.group(1)} server={server_m.group(1)})")


def main() -> None:
    source_shape_checks()
    lockstep_cap_check()
    arithmetic_checks()
    print("proof_fl4137_gap_b_preview_validity: PASS")


if __name__ == "__main__":
    main()
