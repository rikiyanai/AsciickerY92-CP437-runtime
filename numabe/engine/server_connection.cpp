// server_connection.cpp — Server connection/transport functions
//
// Functions that operate on the connection/session/lag slice of Server.
// These can be tested without the gameplay authority side (NPCs, items, combat).

#include "server_connection.h"
#include "../server/network.h"

// Currently Server::Proc, Server::Send, Server::Log are defined in:
//   engine/game_app.cpp  (native)
//   engine/network_ingest_dispatch.cpp  (web Proc)
//   server/game_svr.cpp  (server stubs)
// and remain there. As the split deepens, connection-level helpers
// (lag sampling, packet framing) move here.
