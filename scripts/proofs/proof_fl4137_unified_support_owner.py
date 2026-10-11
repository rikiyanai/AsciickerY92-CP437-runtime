#!/usr/bin/env python3
"""Compatibility front door for FL-4137 unified support-owner checks.

The previous version asserted the now-deleted placed-block-only collector. That
was a false-green surface for the wrong architecture. Delegate to the active
owner proof, which requires placed blocks to enter mp_step through World mesh
instances like AKM meshes.
"""

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).with_name("proof_fl4137_block_world_mesh_owner.py")),
    run_name="__main__",
)
