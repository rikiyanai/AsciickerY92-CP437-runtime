#include <assert.h>
#include <stdio.h>

#include "game.h"
#include "multiplayer_protocol.h"
#include "snapshot_client/remote_snapshot_presentation_track.h"

int main()
{
	Human remote = {};
	STRUCT_SNAPSHOT_ENTITY snapshot = {};
	float pos[3] = { 1.0f, 2.0f, 3.0f };

	snapshot.life_state = 1;
	snapshot.hp = 73;
	snapshot.max_hp = 100;
	AcceptRemoteActorPresentationSnapshot(
		&remote, &snapshot, pos, 45.0f, 10u, 1000000u);

	assert(remote.HP == 73);
	assert(remote.MAX_HP == 100);

	snapshot.life_state = 2;
	snapshot.hp = 0;
	AcceptRemoteActorPresentationSnapshot(
		&remote, &snapshot, pos, 45.0f, 11u, 1033333u);

	assert(remote.HP == 0);
	assert(remote.MAX_HP == 100);
	puts("remote snapshot HP propagation tests passed");
	return 0;
}
