#!/usr/bin/env python3
# =============================================================================
# scripts/annotate_fl4684_capture_scene_phase.py
# =============================================================================
# RECORD WHICH CAPTURED FRAMES SHOW GAMEPLAY AND WHICH SHOW THE MAIN MENU
#
# WHY THIS EXISTS, IN PLAIN LANGUAGE
# ----------------------------------
# The FL-4684 desktop driver photographs the game's viewport several times
# during a session. It cannot tell what those photographs CONTAIN. That turned
# out to matter a great deal.
#
# In the first desktop run, four of the five photographs were of the MAIN MENU,
# not of gameplay. The main menu is not a blank screen: it draws a full 3D scene
# behind the title and the menu items. So the renderer was running, the
# scene-fact producer was emitting, and the pictures looked like a real game --
# but nobody was playing.
#
# The consequence was a wrong conclusion. The only large difference between two
# consecutive photographs was 82.8%, and that was read as "the camera moved".
# It was not. It was the moment the player left the menu and gameplay began.
# Measured properly there were ZERO consecutive gameplay-to-gameplay pairs, so
# that run proves nothing at all about camera motion.
#
# A percentage cannot distinguish those two things on its own. Somebody has to
# LOOK at the frames. This script records the result of that looking, together
# with who did it and what evidence backed it, so the judgement is auditable
# instead of being an unwritten assumption inside a number.
#
# WHAT A "SCENE PHASE" IS
# -----------------------
#   "main_menu"    -- the title screen. Its 3D backdrop still renders, so the
#                     producer still emits, but no one is playing.
#   "in_play"      -- the player is in the world.
#   "unclassified" -- nobody has looked yet. Frames in this state are never
#                     allowed to support a motion claim.
#
# Only a consecutive pair where BOTH frames are "in_play" may be used as
# evidence that the camera moved.
#
# USAGE
#   python3 scripts/annotate_fl4684_capture_scene_phase.py \
#       --receipt .run/fl4684_native_live_producer/receipt.json \
#       --phases main_menu,main_menu,main_menu,main_menu,in_play \
#       --classified-by "agent visual inspection" \
#       --evidence "shot 000 shows the asciicker title and the NEW GAME menu; \
# shots 001-003 are byte-identical to it; in shot 004 the title band differs by \
# 89.65% and the menu-item column by 85.78%, so the menu chrome is gone"
# =============================================================================

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The only phase names this contract recognises. Anything else is refused rather
# than written, so a typo cannot silently become a new category that downstream
# code does not know how to treat.
VALID_PHASES = {"main_menu", "in_play", "unclassified"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--receipt", required=True, help="the run receipt to annotate"
    )
    parser.add_argument(
        "--phases",
        required=True,
        help="comma-separated phase per captured frame, in capture order",
    )
    parser.add_argument(
        "--classified-by",
        required=True,
        help="who looked at the frames, e.g. 'operator' or 'agent visual inspection'",
    )
    parser.add_argument(
        "--evidence",
        required=True,
        help="what was actually seen or measured that justifies the labels",
    )
    args = parser.parse_args()

    receipt_path = Path(args.receipt)
    if not receipt_path.is_absolute():
        receipt_path = ROOT / receipt_path
    receipt = json.loads(receipt_path.read_text())

    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    bad = sorted(set(phases) - VALID_PHASES)
    if bad:
        parser.error(f"unknown scene phase(s): {bad}; allowed: {sorted(VALID_PHASES)}")

    captures = receipt.get("viewport_captures", [])
    if len(phases) != len(captures):
        parser.error(
            f"{len(phases)} phases given but the receipt has {len(captures)} captures"
        )

    # Attach the phase to each capture, and also record the classification as a
    # block of its own so the provenance travels with the receipt.
    for capture, phase in zip(captures, phases):
        capture["scene_phase"] = phase
    receipt["capture_scene_phase_classification"] = {
        "phases": phases,
        "classified_by": args.classified_by,
        "evidence": args.evidence,
        "why_this_is_recorded": (
            "The driver photographs the viewport but cannot tell whether a "
            "photograph shows gameplay or the main menu. The main menu renders a "
            "full 3D scene behind it, so the producer emits either way and a "
            "frame-difference percentage alone cannot separate 'the camera "
            "moved' from 'the player left the menu'. Only a consecutive pair "
            "where both frames are in_play may support a motion claim."
        ),
    }

    receipt_path.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for p in phases:
        counts[p] = counts.get(p, 0) + 1
    in_play_pairs = sum(
        1
        for a, b in zip(phases, phases[1:])
        if a == "in_play" and b == "in_play"
    )
    print(f"annotated {receipt_path}")
    print(f"  phase counts        : {counts}")
    print(f"  in-play consecutive pairs usable for motion: {in_play_pairs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
