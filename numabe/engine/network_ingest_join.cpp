#include <stdint.h>
#include "network_ingest.h"
#include "game.h"
#include "remote_actor_roster.h"
#include "server/multiplayer_protocol.h"

bool ApplyJoinPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	if (size == (int)sizeof(STRUCT_BRC_JOIN))
		ApplyRemoteActorJoinPacket(game, server, (const STRUCT_BRC_JOIN*)ptr);
	return true;
}

bool ApplyExitPacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	if (size == (int)sizeof(STRUCT_BRC_EXIT))
		ApplyRemoteActorExitPacket(game, server, (const STRUCT_BRC_EXIT*)ptr);
	return true;
}
