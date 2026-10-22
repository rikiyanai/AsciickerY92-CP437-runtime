#include <stdint.h>
#include "network_ingest.h"
#include "game.h"
#include "actor_visual_profile_packet.h"
#include "remote_actor_roster.h"
#include "snapshot_client/snapshot_npc_repository.h"
#include "server/multiplayer_protocol.h"

bool ApplyAppearancePacket(Server* server, Game* game, const uint8_t* ptr, int size)
{
	if (size != (int)sizeof(STRUCT_BRC_APPEARANCE_STATE_V2))
		return true;
	if (!server || !game)
		return true;
	const STRUCT_BRC_APPEARANCE_STATE_V2* appearance = (const STRUCT_BRC_APPEARANCE_STATE_V2*)ptr;
	server->authority.auth_item.appearance_v2_packets++;
	server->authority.auth_item.last_appearance_v2_entity_id = appearance->entity_id;
	server->authority.auth_item.last_appearance_v2_entity_type = appearance->entity_type;

	AppearanceStateV2 next = {};
	ApplyActorVisualProfilePacketToClientState(&next, appearance);

	if (appearance->entity_type == APPEARANCE_V2_ENTITY_PLAYER)
	{
		if (server->connection.local_id >= 0 &&
			appearance->entity_id == (uint16_t)server->connection.local_id)
		{
			// FL-4076: appearance packets are server-owned state, not render-row
			// admission decisions. Do not let the client's previous transient
			// presentation_kind_id reject a valid new loadout; the renderer's exact
			// ActorVisualProfile lookup remains the single fail-closed presentation
			// boundary once snapshot state and appearance state are combined.
			game->player.appearance_v2 = next;
			return true;
		}
		if (appearance->entity_id < (uint16_t)server->connection.max_clients)
		{
			Human* remote = server->authority.others + appearance->entity_id;
			BootstrapRemoteActorRosterSlotForSnapshot(
				server, remote, appearance->entity_id);
			remote->appearance_v2 = next;
			return true;
		}
		return true;
	}

	if (appearance->entity_type == APPEARANCE_V2_ENTITY_NPC)
	{
		SnapshotNpcRepositoryContext ctx = {};
		ctx.snapshot_client = &server->authority.snapshot_client;
		ctx.npc_repo = &server->authority.npc_repo;
		ctx.max_clients = server->connection.max_clients;
		ApplySnapshotNpcAppearanceState(&ctx, appearance->entity_id, &next);
		return true;
	}

	return true;
}
