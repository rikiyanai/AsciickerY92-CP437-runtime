// server message ingestion — thin dispatch seam
// Each case delegates to an independently auditable packet-family owner.
#include <stdint.h>
#include "game.h"
#include "game_api.h"
#include "network_ingest.h"
#include "remote_actor_roster.h"

extern Terrain* terrain;
extern World* world;

bool Server::Proc(const uint8_t* ptr, int size)
{
	bool runtime_world_ready_for_mp_apply =
		(game != 0) &&
		(!game->ui.main_menu) &&
		(game->physics != 0) &&
		(world != 0) &&
		(terrain != 0);
	if (!runtime_world_ready_for_mp_apply && size > 0)
	{
		// Long-load maps can receive packets before world/terrain are initialized.
		// High-rate pose/snapshot traffic can be dropped (it will be refreshed later),
		// but one-shot state packets like JOIN/EXIT and V2 authoritative item events must
		// survive pre-world loading or the client permanently misses baseline state.
		switch (ptr[0])
		{
			case 'j': // join baseline must not be dropped
			case 'e': // exit updates roster state
			case 'b': // authoritative baseline must survive loading so client can ACK/apply once world is ready
			case 'q': // authoritative deltas must keep flowing through load/bootstrap
			case 'a': // server-owned V2 appearance packets are baseline-like
			case 'i': // server-owned V2 item packets are baseline-like
			case 'c': // read-only collision debug is allowed through load/bootstrap
				break; // allow through even before world is ready
			default:
				return true;
		}
	}

	// Opcode accounting diagnostic
	if (prime_game)
	{
		if (ptr[0] == 'p') prime_game->debug.dbg_proc_opcode_p++;
		else if (ptr[0] == 'b') prime_game->debug.dbg_proc_opcode_b++;
		else if (ptr[0] == 'q') prime_game->debug.dbg_proc_opcode_q++;
		else prime_game->debug.dbg_proc_opcode_other++;
	}

	switch (ptr[0])
	{
		case 'j': return ApplyJoinPacket(this, prime_game, ptr, size);
		case 'e': return ApplyExitPacket(this, prime_game, ptr, size);
		case 'a': return ApplyAppearancePacket(this, prime_game, ptr, size);
		case 'p': return true; // retired pose
		case 'b':
		case 'q': ApplySnapshotPacket(this, prime_game, terrain, world, ptr, size); return true;
		case 't': return ApplyChatPacket(this, prime_game, ptr, size);
		case 'l': return ApplyLagPacket(this, prime_game, ptr, size);
		case 'h':
		case 'd':
		case 'k':
		case 'r': return ApplyCombatPacket(this, prime_game, ptr, size);
		case 'i': return ApplyItemPacket(this, prime_game, ptr, size);
		case 'v': return ApplyDecalPacket(this, prime_game, terrain, ptr, size);
		case 'c': return ApplyCollisionDebugPacket(this, prime_game, ptr, size);
		default:
			return false;
	}
}
