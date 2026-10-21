// server/connection/server_connection.cpp — Server connection/transport functions
//
// Functions that operate on the connection/session/lag slice of Server.
// These can be tested without the gameplay authority side (NPCs, items, combat).

#include "connection/server_connection_state.h"
#include "network.h"

// As the split deepens, connection-level helpers (lag sampling, packet framing,
// session lifecycle) move here from engine/game_app.cpp,
// engine/network_ingest_lag.cpp, and engine/network_ingest_dispatch.cpp.
