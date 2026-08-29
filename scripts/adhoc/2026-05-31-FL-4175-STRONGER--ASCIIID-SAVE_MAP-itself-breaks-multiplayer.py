# Ad hoc script: FL-4175 STRONGER — ASCIIID SAVE_MAP itself breaks multiplayer bootstrap. Isolated by loading baseline y8 then immediately saving without patches. Resulting map breaks bootstrap. Original works. So the bug is in the a3d write path, NOT in FL-4131 patches.
# Created: 2026-05-31
# Canonical gap: <describe what tool should own this>

"""Isolation test for SAVE_MAP corruption.

Repro:
  1. ./.run/asciiid --cdp 47760 &
  2. CDP LOAD_MAP assets/a3d/game_map_y8.a3d
  3. CDP SAVE_MAP .run/y8_resaved_no_patches.a3d   (NO patches applied)
  4. ./.run/server --map .run/y8_resaved_no_patches.a3d --port 38403 --max-players 4
  5. Chrome on that server -> bootstrap timeout 20002ms

Same server binary serves the ORIGINAL game_map_y8.a3d fine.
Only the resaved copy breaks bootstrap.

Implication: root cause is in editor/asciiid.cpp SAVE_MAP code, not in the
material patches. FL-4131 patched-map bootstrap symptom is just one
consequence of the wider corruption.
"""
import sys
print(__doc__)
sys.exit(0)
