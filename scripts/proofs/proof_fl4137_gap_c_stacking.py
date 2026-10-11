#!/usr/bin/env python3
"""Compatibility front door for FL-4137 stacking source checks.

Stacking target selection still lives in server_tick because it is item
placement intent resolution, not movement support. Runtime collision/support
must not use the deleted MpPlacedBlockCollider/W5 lane. This wrapper first
guards the new owner boundary; headed gameplay proof still owns stand-on and
side-collision evidence.
"""

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).with_name("proof_fl4137_block_world_mesh_owner.py")),
    run_name="__main__",
)
