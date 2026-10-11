// server/authority/authority_snapshots.cpp — Snapshot authority
//
// Functions operating on SnapshotClientState and snapshot streams.
// These can be tested without the transport layer.

#include "authority/server_authority_state.h"
#include "game.h"

// As the split deepens, snapshot apply/validation helpers move here from
// engine/authoritative_snapshot_stream_applier.cpp and
// engine/authoritative_remote_snapshot_player.cpp.
