// server_authority.cpp — Server gameplay authority functions
//
// Functions that operate on the gameplay-authority slice of Server:
// player roster, snapshot client state, NPC repository, item state, combat.
// These can be tested without the transport/connection layer.

#include "server_authority.h"
#include "game.h"

// As the split deepens, authority-level helpers (roster traversal, NPC
// lifecycle, item state queries) move here from game_render_bridge.cpp,
// game.cpp, authoritative_item_*.cpp, and snapshot_npc_*.cpp.
