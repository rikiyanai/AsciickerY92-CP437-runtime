// snapshot_client_state.cpp — Snapshot stream observability per client
//
// Owns SnapshotClientState struct operations.
// Extracted from Server in server_state.h integration layer.

#include "snapshot_client_state.h"

#include <string.h>

static void SnapshotClientState_Reset(SnapshotClientState* state)
{
    if (!state)
        return;
    memset(state, 0, sizeof(*state));
}

static void SnapshotClientState_NoteAck(SnapshotClientState* state,
                                 uint16_t seq,
                                 uint32_t tick)
{
    if (!state)
        return;
    state->snapshot_ack_packets++;
    state->last_snapshot_ack_seq = seq;
    state->last_snapshot_ack_tick = tick;
}

static void SnapshotClientState_NoteSnapshot(SnapshotClientState* state,
                                      uint16_t seq,
                                      uint32_t tick,
                                      uint32_t entity_count,
                                      bool is_delta,
                                      uint64_t wall_stamp_us)
{
    if (!state)
        return;

    if (state->last_snapshot_seq != 0)
    {
        uint16_t expected = state->last_snapshot_seq + 1;
        if (seq != expected)
            state->snapshot_gap_count++;
    }

    state->snapshot_packets++;
    state->last_snapshot_seq = seq;
    state->last_snapshot_tick = tick;
    state->last_snapshot_wall_stamp_us = wall_stamp_us;
    state->snapshot_last_entity_count = entity_count;
    state->snapshot_last_is_delta = is_delta ? 1 : 0;
}
