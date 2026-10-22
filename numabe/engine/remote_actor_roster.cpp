#include "remote_actor_roster.h"

#include "game_utility.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "authoritative_presentation_adapters.h"
#include "snapshot_client/remote_authoritative_snapshot.h"
#include "snapshot_client/snapshot_entity_decoder.h"
#include "snapshot_client/snapshot_stream_applier.h"
#include "game.h"
#include "remote_observer_probe.h"
#include "remote_authoritative_presentation_lifecycle.h"


bool RemoteActorRosterSlotHasState(const Server* server, const Human* h)
{
	if (!server || !h)
		return false;
	return h->name[0] != 0 ||
		h->inst != 0 ||
		h->prev != 0 ||
		h->next != 0 ||
		server->authority.head == h ||
		server->authority.tail == h ||
		h->talks > 0 ||
		h->sprite != 0 ||
		h->appearance_v2.valid ||
		h->remote_presentation_track.interp_ring_write_idx != 0u ||
		h->remote_presentation_track.pending_inst_invalidation_reason != 0u ||
		h->remote_presentation_track.pending_inst_invalidation_clear_aliases != 0u;
}

bool ApplyRemoteActorJoinPacket(Game* game, Server* server, const STRUCT_BRC_JOIN* join)
{
	if (!server || !join)
		return false;
	if (join->id >= server->connection.max_clients)
		return false;
	if (!RemoteSnapshotPoseSane(join->pos, join->dir))
		return false;

	Human* h = server->authority.others + join->id;
	if (RemoteActorRosterSlotHasState(server, h))
		ResetRemoteActorRosterSlot(game, server, h, (int)join->id, 31, false);

	memset(h, 0, sizeof(Human));
	snprintf(h->name, sizeof(h->name), "%s", join->name);
	ConvertToCP437(h->name_cp437, h->name, (int)sizeof(h->name_cp437));
	BootstrapRemoteActorRosterSlotForSnapshot(server, h, join->id);

	h->clr = 0;
	uint8_t join_life_state = join->life_state;
	uint8_t join_mount_state = join->mount_state;
	uint8_t join_locomotion_state = join->locomotion_state;
	uint8_t join_combat_state = join->combat_state;
	uint16_t join_presentation_kind_id = join->presentation_kind_id;
	if (!ValidateAppearanceRuntimeInputs(
			&join_life_state,
			&join_mount_state,
			&join_locomotion_state,
			&join_combat_state,
			&join_presentation_kind_id,
			"BRC_JOIN",
			join->id,
			SNAPSHOT_ENTITY_PLAYER))
		return false;
	h->life_state = join_life_state;
	h->mount_state = join_mount_state;
	h->locomotion_state = join_locomotion_state;
	h->combat_state = join_combat_state;
	h->presentation_kind_id = join_presentation_kind_id;
	h->presentation_selector_failure_reason = ACTOR_VISUAL_PROFILE_FAILURE_NONE;
	h->presentation_started_tick = join->presentation_started_tick;
	h->dir = join->dir;
	h->pos[0] = join->pos[0];
	h->pos[1] = join->pos[1];
	h->pos[2] = join->pos[2];

	ActorPresentationResult join_presentation =
		ResolveRemoteAuthoritativeCharacterPresentation(
			h, h->clr, join->presentation_started_tick);
	h->sprite = join_presentation.sprite;
	h->anim = join_presentation.anim;
	h->frame = join_presentation.frame;

	RemoteObserverInitializeJoinState(server, h, (int)join->id);
	ChatLog("%s joined\n", join->name);
	return true;
}

void ApplyRemoteActorExitPacket(Game* game, Server* server, const STRUCT_BRC_EXIT* leave)
{
	if (!server || !leave || leave->id >= server->connection.max_clients)
		return;
	Human* h = server->authority.others + leave->id;
	ResetRemoteActorRosterSlot(game, server, h, (int)leave->id, 33, true);
}

static void FreeRemoteActorRosterTalks(Human* h)
{
	if (!h)
		return;
	int talks = h->talks;
	if (talks < 0)
		talks = 0;
	if (talks > (int)(sizeof(h->talk) / sizeof(h->talk[0])))
		talks = (int)(sizeof(h->talk) / sizeof(h->talk[0]));
	for (int i = 0; i < talks; i++)
	{
		if (h->talk[i].box)
			free(h->talk[i].box);
		h->talk[i].box = 0;
	}
	h->talks = 0;
}

static void UnlinkRemoteActorRosterSlot(Server* server, Human* h)
{
	if (!server || !h)
		return;
	if (h->prev)
		h->prev->next = h->next;
	else if (server->authority.head == h)
		server->authority.head = (Human*)h->next;
	if (h->next)
		h->next->prev = h->prev;
	else if (server->authority.tail == h)
		server->authority.tail = (Human*)h->prev;
	h->prev = 0;
	h->next = 0;
}

static void LinkRemoteActorRosterSlotAtHead(Server* server, Human* h)
{
	if (!server || !h)
		return;
	if (server->authority.head == h || server->authority.tail == h || h->prev || h->next)
		return;
	h->prev = 0;
	h->next = server->authority.head;
	if (server->authority.head)
		server->authority.head->prev = h;
	else
		server->authority.tail = h;
	server->authority.head = h;
}

static void SeedRemoteActorDefaultIdentity(Human* h, uint16_t remote_id)
{
	if (!h || h->name[0] != 0)
		return;
	h->clr = 0;
	snprintf(h->name, sizeof(h->name), "P%u", (unsigned)remote_id);
	ConvertToCP437(h->name_cp437, h->name, (int)sizeof(h->name_cp437));
}

void ResetRemoteActorRosterSlot(
	Game* game,
	Server* server,
	Human* h,
	int pid,
	int inst_reason,
	bool emit_chat)
{
	if (!server || !h)
		return;
	if (emit_chat && h->name[0])
		ChatLog("%s left\n", h->name);
	FreeRemoteActorRosterTalks(h);
	UnlinkRemoteActorRosterSlot(server, h);
	RemoteAuthoritativePresentationDeleteInst(game, server, h, pid, inst_reason, true);
	if (pid >= 0)
		ResetRemoteObserverProbe(server, pid);
	memset(h, 0, sizeof(Human));
}

void BootstrapRemoteActorRosterSlotForSnapshot(Server* server, Human* h, uint16_t remote_id)
{
	if (!server || !h)
		return;
	if (!RemoteActorRosterSlotHasState(server, h))
		memset(h, 0, sizeof(Human));
	LinkRemoteActorRosterSlotAtHead(server, h);
	SeedRemoteActorDefaultIdentity(h, remote_id);
}

void RemoveRemoteActorRosterSlotById(
	Game* game,
	Server* server,
	uint16_t remote_id,
	int inst_reason,
	bool emit_chat)
{
	if (!server || remote_id >= (uint16_t)server->connection.max_clients)
		return;
	Human* remote = server->authority.others + remote_id;
	if (!RemoteActorRosterSlotHasState(server, remote))
		return;
	ResetRemoteActorRosterSlot(
		game,
		server,
		remote,
		(int)remote_id,
		inst_reason,
		emit_chat);
}

void SanitizeRemoteActorRoster(Game* game, Server* server)
{
	if (!game || !server)
		return;
	Human* new_head = 0;
	Human* new_tail = 0;
	for (int pid = 0; pid < server->connection.max_clients; pid++)
	{
		Human* slot = server->authority.others + pid;
		bool keep = false;
		if (pid == server->connection.local_id)
			keep = slot->name[0] || slot->inst || slot->sprite ||
				slot->life_state == LIFE_STATE::DEAD || slot->talks > 0;
		else if (slot->name[0] != 0)
			keep = true;
		if (!keep)
		{
			if (pid != server->connection.local_id && slot->name[0] != 0)
			{
				game->debug.dbg_roster_evict_count++;
				game->debug.dbg_roster_last_evicted_pid = pid;
			}
			if (pid != server->connection.local_id && RemoteActorRosterSlotHasState(server, slot))
				ResetRemoteActorRosterSlot(game, server, slot, pid, 35, false);
			else
			{
				slot->prev = 0;
				slot->next = 0;
				if (pid != server->connection.local_id && slot->name[0] == 0)
				{
					slot->sprite = 0;
					slot->talks = 0;
				}
			}
			continue;
		}
		slot->prev = new_tail;
		slot->next = 0;
		if (new_tail)
			new_tail->next = slot;
		else
			new_head = slot;
		new_tail = slot;
	}
	server->authority.head = new_head;
	server->authority.tail = new_tail;
}
