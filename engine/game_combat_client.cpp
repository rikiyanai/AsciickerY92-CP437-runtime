// game_combat_client.cpp -- combat client helpers
// extracted from game.cpp
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <math.h>
#include "game.h"
#include "fast_rand.h"
#include "world.h"
#include "render.h"


bool ShouldWriteLocalSinglePlayerBloodDecals()
{
	return server == 0;
}


static bool FindVerifierNearestAttackTarget(const LocalPlayerState& player, Server* srv, int target_kind, uint16_t* out_target_id, float out_pos[3], bool* out_is_npc)
{
	if (!srv || !out_target_id || !out_pos || !out_is_npc)
		return false;

	bool want_players = (target_kind == 0 || target_kind == 1);
	bool want_npcs = (target_kind == 0 || target_kind == 2);
	bool found = false;
	bool best_is_npc = false;
	uint16_t best_id = 0xffff;
	float best_pos[3] = { 0, 0, 0 };
	float best_dd = 1e30f;

	if (want_players)
	{
		float player_pos[3] = { player.pos[0], player.pos[1], player.pos[2] };
		for (Human* rh = srv->authority.head; rh; rh = (Human*)rh->next)
		{
			if (rh->life_state == LIFE_STATE::DEAD)
				continue;
			float remote_pos[3] = { rh->pos[0], rh->pos[1], rh->pos[2] };
			float dx = remote_pos[0] - player_pos[0];
			float dy = remote_pos[1] - player_pos[1];
			float dd = dx * dx + dy * dy;
			if (!found || dd < best_dd)
			{
				best_dd = dd;
				best_id = (uint16_t)(rh - srv->authority.others);
				best_pos[0] = remote_pos[0];
				best_pos[1] = remote_pos[1];
				best_pos[2] = remote_pos[2];
				best_is_npc = false;
				found = true;
			}
		}
	}

	if (want_npcs && srv->authority.npc_repo.npc_count > 0)
	{
		float player_pos[3] = { player.pos[0], player.pos[1], player.pos[2] };
		for (int ni = 0; ni < (int)srv->authority.npc_repo.npc_count; ni++)
		{
			const ServerSnapshotNpcRepository::SnapshotNpcState* sn = &srv->authority.npc_repo.npcs[ni];
			bool alive = ((sn->state_flags & SNAPSHOT_STATE_ALIVE) != 0) && sn->hp > 0;
			if (!alive)
				continue;
			float dx = sn->pos[0] - player_pos[0];
			float dy = sn->pos[1] - player_pos[1];
			float dd = dx * dx + dy * dy;
			if (!found || dd < best_dd)
			{
				best_dd = dd;
				best_id = sn->entity_id;
				best_pos[0] = sn->pos[0];
				best_pos[1] = sn->pos[1];
				best_pos[2] = sn->pos[2];
				best_is_npc = true;
				found = true;
			}
		}
	}

	if (!found)
		return false;

	*out_target_id = best_id;
	out_pos[0] = best_pos[0];
	out_pos[1] = best_pos[1];
	out_pos[2] = best_pos[2];
	*out_is_npc = best_is_npc;
	return true;
}


int VerifierStartAttackNearest(Game* g, int target_kind)
{
	(void)g; (void)target_kind;
	return -1;
}


int VerifierSetDebugDamage(Game* g, int enabled)
{
	(void)g; (void)enabled;
	return -1;
}


void BloodLeak(Character* c, int steps)
{
	if (!ShouldWriteLocalSinglePlayerBloodDecals())
	{
		c->leak = 0;
		c->leak_steps = 0;
		return;
	}

	c->leak_steps += steps;

	if (!c->leak)
		c->leak_steps = 0;
	else
	if (c->leak_steps >= 5)
	{
		c->leak_steps -= c->leak_steps / 5 * 5;

		float dR = 1.0;
		float dr = dR * sqrtf((fast_rand() & 0xfff) / (float)0xfff);
		float dt = (fast_rand() & 0xfff) * (float)(2.0 * M_PI) / (float)0xfff;
		float xy[2] = { c->pos[0] + dr * cosf(dt), c->pos[1] + dr * sinf(dt) };
		PaintTerrain(xy, fast_rand() % 20 * 0.1f, 5/*session.blood*/);

		c->leak--;
	}
}
