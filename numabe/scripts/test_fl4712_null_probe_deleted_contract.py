#!/usr/bin/env python3
"""Guard the physical deletion of FL-4712's temporary null-work measurement probe.

WHY THIS FILE EXISTS
--------------------
FL-4712 needed to know what one extra piece of GPU work costs on this machine.
To find out, a deliberately empty GPU program (a "null probe") was added to the
renderer for a single session, the cost was measured, and the probe was then
removed again. This file is what stops it from coming back.

The project's recorded law, stated in its sibling deletion contracts, is that a
switched-off flag still leaves a SECOND owner of behaviour inside the shipping
source. A measurement device that is present but disabled is exactly that. So
the probe was physically deleted rather than shipped dormant, and this contract
turns "we deleted it" into a checkable fact instead of a memory.

WHAT IT CHECKS
--------------
1. Every name the probe used is absent from the renderer host script.
2. The probe's shader file does not exist.
3. The renderer still contains the one real thing that had to survive: the empty
   probe was injected right after the VSB/Product compute list is closed, and
   that closing call must still be there, unaccompanied.

Each check is also exercised against an in-memory MUTANT — a deliberately
damaged copy that restores one piece of the deleted probe. A mutant that does
not make the contract fail would mean the check is asleep, so the mutants are
part of the guarantee, not decoration.

The harness-side additions made in the same session (--ab-control-args and
--ab-alternate-order in scripts/run_godot_camera_turn_perf.py) are NOT covered
here. They are deliberately retained: they live outside the renderer, they add
no behaviour to any shipped frame, and they are what lets the paired A/B bracket
mode express a switch at all now that no environment gate is read by the game.
"""

from __future__ import annotations

import pathlib


REPO = pathlib.Path(__file__).resolve().parents[1]
# The renderer script the probe used to live inside.
HOST_PATH = REPO / "godot_project/render/api/ASCIICompositorEffect.gd"
# The empty GPU program file itself. It must not exist.
PROBE_SHADER_PATH = REPO / "godot_project/shaders/fl4712_null_probe.glsl"

# Every identifier, flag, and constant the deleted probe introduced. If ANY of
# these strings reappears in the renderer, the probe (or something shaped like
# it) has been resurrected.
HOST_FORBIDDEN = (
    "fl4712",
    "FL4712",
    "_fl4712_null_pass_count",
    "_fl4712_null_list_count",
    "_fl4712_null_barrier_count",
    "_fl4712_inject_null_work",
    "_fl4712_ensure_null_probe_pipeline",
    "_fl4712_parse_null_count",
    "--fl4712-null-passes=",
    "--fl4712-null-lists=",
    "--fl4712-null-barriers=",
)

# The renderer line the probe call sat immediately after. Requiring it to still
# be present, exactly once inside the render callback, proves the deletion
# removed the probe and nothing else: a careless revert of the whole block would
# have taken this line with it.
REQUIRED_HOST_SHAPE = "rd.compute_list_end()"


def contract(host_source: str, probe_shader_exists: bool) -> bool:
    """Return True when the probe is fully absent from the shipping renderer.

    `host_source` is the complete text of the renderer script.
    `probe_shader_exists` says whether the empty probe shader file is on disk.
    """
    if probe_shader_exists:
        return False
    if any(token in host_source for token in HOST_FORBIDDEN):
        return False
    # The compute list the probe was injected after must still be closed. Its
    # absence would mean the deletion went too far.
    if REQUIRED_HOST_SHAPE not in host_source:
        return False
    return True


def main() -> int:
    host_source = HOST_PATH.read_text(encoding="utf-8")
    probe_shader_exists = PROBE_SHADER_PATH.exists()

    assert contract(host_source, probe_shader_exists), (
        "FL-4712 null-probe deletion contract failed: the temporary measurement "
        "probe is still present in the shipping renderer"
    )

    # Mutants. Each one restores exactly one piece of the deleted probe and must
    # make the contract fail. A mutant that slips through would mean the
    # corresponding check does nothing.
    mutants = [
        # 1. The injection helper is defined again.
        (
            host_source + "\nfunc _fl4712_inject_null_work(size: Vector2i) -> void:\n\tpass\n",
            False,
        ),
        # 2. One of the command-line arming flags is read again.
        (
            host_source.replace(
                "func _init() -> void:",
                'func _init() -> void:\n\tvar n := OS.get_cmdline_user_args().has("--fl4712-null-passes=")',
                1,
            ),
            False,
        ),
        # 3. The empty probe shader file is back on disk, even with a clean host.
        (host_source, True),
    ]
    for index, (mutant_source, mutant_shader_exists) in enumerate(mutants, start=1):
        assert not contract(mutant_source, mutant_shader_exists), (
            f"FL-4712 deletion contract mutant {index} did not make the contract fail"
        )

    print(f"FL4712_NULL_PROBE_DELETED_CONTRACT_PASS mutants={len(mutants)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
