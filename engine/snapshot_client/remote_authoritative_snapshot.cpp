#include "snapshot_client/remote_authoritative_snapshot.h"

#include "snapshot_client/snapshot_stream_applier.h"
#include "snapshot_client/remote_snapshot_presentation_track.h"
#include "human.h"
#include "mp_move.h"
#include "multiplayer_protocol.h"
#include "remote_actor_roster.h"
#include "remote_authoritative_presentation_lifecycle.h"

bool ApplyRemoteAuthoritativeSnapshot(
	Human* remote,
	Server* server,
	World* world,
	uint16_t remote_id,
	const STRUCT_SNAPSHOT_ENTITY* ent,
	uint32_t last_snapshot_tick,
	uint64_t arrival_wall_stamp_us,
	RemoteAuthoritativeSnapshotApplyResult* out)
{
	if (!out)
		return false;
	memset(out, 0, sizeof(*out));
	if (!remote || !ent)
		return false;

	if (!SnapshotPoseSane(ent->pos, ent->dir))
	{
		out->rejected_pose = true;
		return false;
	}

	if (MpMoveShouldRejectStaleOriginSnapshot(remote->pos, ent->pos))
	{
		out->rejected_stale_origin = true;
		return false;
	}

	BootstrapRemoteActorRosterSlotForSnapshot(server, remote, remote_id);
	out->bootstrapped_roster = true;

	AcceptRemoteActorPresentationSnapshot(
		remote, ent, ent->pos, ent->dir, last_snapshot_tick, arrival_wall_stamp_us);
	out->accepted_presentation = true;

	if (world)
	{
		QueueRemoteActorPresentationInstInvalidation(remote, world);
		out->queued_inst_invalidation = true;
	}

	out->applied = true;
	return true;
}
