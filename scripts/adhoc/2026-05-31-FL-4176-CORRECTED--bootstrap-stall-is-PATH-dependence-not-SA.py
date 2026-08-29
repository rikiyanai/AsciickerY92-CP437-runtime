# Ad hoc script: FL-4176 CORRECTED — bootstrap stall is PATH dependence, not SAVE_MAP. Server resolves dependent mesh paths relative to .a3d location. Maps in .run/ cannot find assets/meshes/ siblings; world loads with broken instance refs; snapshot serialization silently fails downstream. Workaround: save FL-4131-patched maps INTO assets/a3d/ where mesh siblings exist. Repro: cp game_map_y8.a3d .run/ and run server on the copy — bootstrap times out without any SAVE_MAP involvement.
# Created: 2026-05-31
# Canonical gap: <describe what tool should own this>

"""Corrected isolation for the bootstrap stall.

Earlier I claimed ASCIIID SAVE_MAP itself corrupts .a3d. That was wrong.
Real root cause: server resolves .a3d-relative mesh paths.

Repro WITHOUT any SAVE_MAP involvement:
  1. cp assets/a3d/game_map_y8.a3d .run/y8_pure_copy.a3d
  2. ./.run/server --map .run/y8_pure_copy.a3d --port 38403 --max-players 4
  3. Chrome bootstrap: TIMES OUT 20s
  4. Same server, same binary, .a3d at assets/a3d/ path: BOOTSTRAPS

The fix for FL-4131 lane: SAVE_MAP patched maps into assets/a3d/ so meshes
resolve. SAVE_MAP itself is fine; it just produces a v4-format file (extra 24
bytes player_start trailer) and the server reads v4 correctly.

Separate server bug: server should fail loud at startup when dependent
meshes cannot be resolved, instead of silently producing a state that breaks
snapshot delivery downstream.
"""
import sys
print(__doc__)
sys.exit(0)
