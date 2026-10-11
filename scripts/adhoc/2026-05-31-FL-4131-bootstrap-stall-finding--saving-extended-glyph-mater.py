# Ad hoc script: FL-4131 bootstrap stall finding — saving extended-glyph material patches via ASCIIID SAVE_MAP into a .a3d breaks multiplayer bootstrap delivery. Same .run/server binary works with baseline y8 (parallel session PID 44672 shows GAME RUNNING) but times out with y8+FL4131 patches. Real bug uncovered while attempting headed proof per user directive.
# Created: 2026-05-31
# Canonical gap: <describe what tool should own this>

import sys

print("FL-4131 bootstrap stall — repro steps:")
print("  1. ./.run/asciiid --cdp 47755 &")
print("  2. CDP LOAD_MAP assets/a3d/game_map_y8.a3d")
print("  3. CDP FL4131_APPLY_EXTENDED_PRESET 1 11")
print("  4. CDP SAVE_MAP .run/y8_with_fl4131_patches.a3d")
print("  5. ./.run/server --map .run/y8_with_fl4131_patches.a3d --port 38403 --max-players 4")
print("  6. Chrome at http://127.0.0.1:38083/index.html?...&server=localhost%3A38403&map=...")
print("  7. Bootstrap times out at 20006ms")
print("")
print("Server log shows: WS handshake, JoinV2 with matching manifest hash,")
print("[FL036-ALIVE] JOINED->ALIVE, [SPAWN-DECISION], then silence.")
print("No subsequent snapshots reach the client; inboundGameplayQueueLength stays 0.")
print("")
print("Baseline y8 unpatched works on same binary. Just adding FL-4131 patches via SAVE_MAP breaks it.")
print("")
print("Implication: FL-4131 admitted material patches are NOT multiplayer-safe today.")
print("Earlier Step 8 + Step 10-substitute PASS verdicts relied on a partial readiness")
print("check (server_tick>0 OR tick>0 OR auth_item_sample.length>0) that fires before")
print("full bootstrap. The manifest agreement protocol is proven; the actual snapshot")
print("delivery with FL-4131 material data is the broken seam.")
sys.exit(0)
