#pragma once

#include <stdint.h>

struct Game;
struct Human;
struct Server;
struct STRUCT_BRC_EXIT;
struct STRUCT_BRC_JOIN;

bool RemoteActorRosterSlotHasState(const Server* server, const Human* h);
bool ApplyRemoteActorJoinPacket(Game* game, Server* server, const STRUCT_BRC_JOIN* join);
void ApplyRemoteActorExitPacket(Game* game, Server* server, const STRUCT_BRC_EXIT* leave);
void ResetRemoteActorRosterSlot(
	Game* game,
	Server* server,
	Human* h,
	int pid,
	int inst_reason,
	bool emit_chat);
void BootstrapRemoteActorRosterSlotForSnapshot(Server* server, Human* h, uint16_t remote_id);
void SanitizeRemoteActorRoster(Game* game, Server* server);
void RemoveRemoteActorRosterSlotById(
	Game* game,
	Server* server,
	uint16_t remote_id,
	int inst_reason,
	bool emit_chat);
