#include "remote_observer_probe.h"

#include "game.h"
#include "remote_mounted_witness.h"

bool RemoteObserverHasDeathEpoch(const Human* remote)
{
	return remote && (remote->life_state == LIFE_STATE::DEAD);
}

bool RemoteObserverProbePidValid(const Server* server, int pid)
{
	return server && pid >= 0 &&
		pid < server->connection.max_clients &&
		pid < (int)(sizeof(server->authority.combat_obs.obs_remote_death_seq) / sizeof(server->authority.combat_obs.obs_remote_death_seq[0]));
}

void ResetRemoteObserverProbe(Server* server, int pid)
{
	if (!RemoteObserverProbePidValid(server, pid))
		return;
	server->authority.combat_obs.obs_remote_death_seq[pid] = 0;
	server->authority.combat_obs.obs_remote_last_death_source[pid] = 0;
	server->authority.combat_obs.obs_remote_respawn_seq[pid] = 0;
	server->authority.combat_obs.obs_remote_corpse_create_seq[pid] = 0;
	server->authority.combat_obs.obs_remote_corpse_delete_seq[pid] = 0;
	server->authority.combat_obs.obs_remote_corpse_create_count[pid] = 0;
	server->authority.combat_obs.obs_remote_corpse_delete_count[pid] = 0;
	server->authority.combat_obs.obs_remote_last_corpse_create_reason[pid] = 0;
	server->authority.combat_obs.obs_remote_last_corpse_delete_reason[pid] = 0;
}

void RemoteObserverInitializeJoinState(Server* server, Human* remote, int pid)
{
	if (!remote)
		return;
	remote->dbg_obs_death_seq = 0;
	remote->dbg_obs_last_death_source = 0;
	remote->dbg_obs_respawn_seq = 0;
	remote->dbg_obs_corpse_create_seq = 0;
	remote->dbg_obs_corpse_delete_seq = 0;
	remote->dbg_obs_corpse_create_count = 0;
	remote->dbg_obs_corpse_delete_count = 0;
	remote->dbg_obs_last_corpse_create_reason = 0;
	remote->dbg_obs_last_corpse_delete_reason = 0;
	remote->dbg_obs_last_death_transition_source = 0;
	remote->dbg_obs_last_death_transition_setaction_ok = -1;
	remote->dbg_obs_last_death_transition_pre_action = -1;
	remote->dbg_obs_last_death_transition_pre_mount = -1;
	remote->dbg_obs_last_death_transition_post_action = -1;
	remote->dbg_obs_last_death_transition_post_mount = -1;
	ResetRemoteObserverProbe(server, pid);
}

void RemoteObserverNoteDeathSeq(Server* server, Human* remote, int pid, uint32_t source)
{
	if (!remote)
		return;
	remote->dbg_obs_death_seq++;
	remote->dbg_obs_last_death_source = source;
	if (!RemoteObserverProbePidValid(server, pid))
		return;
	server->authority.combat_obs.obs_remote_death_seq[pid]++;
	server->authority.combat_obs.obs_remote_last_death_source[pid] = source;
}

void RemoteObserverNoteRespawnSeq(Server* server, Human* remote, int pid)
{
	if (!remote)
		return;
	RemoteMountedWitnessResetObserverDeathHistory(remote);
	remote->dbg_obs_respawn_seq++;
	if (!RemoteObserverProbePidValid(server, pid))
		return;
	server->authority.combat_obs.obs_remote_respawn_seq[pid]++;
}

void RemoteObserverNoteCorpseCreate(Server* server, Human* remote, int pid, uint32_t reason)
{
	if (!remote)
		return;
	remote->dbg_obs_corpse_create_count++;
	remote->dbg_obs_corpse_create_seq = remote->dbg_obs_death_seq;
	remote->dbg_obs_last_corpse_create_reason = reason;
	if (!RemoteObserverProbePidValid(server, pid))
		return;
	server->authority.combat_obs.obs_remote_corpse_create_count[pid]++;
	server->authority.combat_obs.obs_remote_corpse_create_seq[pid] = server->authority.combat_obs.obs_remote_death_seq[pid];
	server->authority.combat_obs.obs_remote_last_corpse_create_reason[pid] = reason;
}

void RemoteObserverNoteCorpseDelete(Server* server, Human* remote, int pid, uint32_t reason)
{
	if (!remote)
		return;
	remote->dbg_obs_corpse_delete_count++;
	remote->dbg_obs_corpse_delete_seq = remote->dbg_obs_death_seq;
	remote->dbg_obs_last_corpse_delete_reason = reason;
	if (!RemoteObserverProbePidValid(server, pid))
		return;
	server->authority.combat_obs.obs_remote_corpse_delete_count[pid]++;
	server->authority.combat_obs.obs_remote_corpse_delete_seq[pid] = server->authority.combat_obs.obs_remote_death_seq[pid];
	server->authority.combat_obs.obs_remote_last_corpse_delete_reason[pid] = reason;
}
