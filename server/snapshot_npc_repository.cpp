// snapshot_npc_repository.cpp — NPC snapshot state repository
//
// Owns the ServerSnapshotNpcRepository: NPC snapshot data that is
// published to clients each tick. Extracted from Server state.

#include "snapshot_npc_repository.h"

#include <string.h>

static void SnapshotNpcRepository_Reset(ServerSnapshotNpcRepository* repo)
{
    if (!repo)
        return;
    memset(repo, 0, sizeof(*repo));
}

static int SnapshotNpcRepository_FindNpcIndex(const ServerSnapshotNpcRepository* repo,
                                        uint16_t entity_id)
{
    if (!repo)
        return -1;
    for (int i = 0; i < (int)repo->npc_count; i++)
    {
        if (repo->npcs[i].entity_id == entity_id)
            return i;
    }
    return -1;
}

static int SnapshotNpcRepository_AddOrUpdateNpc(ServerSnapshotNpcRepository* repo,
                                          const ServerSnapshotNpcRepository::SnapshotNpcState* state)
{
    if (!repo || !state)
        return -1;
    int idx = SnapshotNpcRepository_FindNpcIndex(repo, state->entity_id);
    if (idx >= 0)
    {
        repo->npcs[idx] = *state;
        return idx;
    }
    if (repo->npc_count >= ServerSnapshotNpcRepository::MAX_SNAPSHOT_NPCS)
        return -1;
    idx = (int)repo->npc_count++;
    repo->npcs[idx] = *state;
    return idx;
}

static void SnapshotNpcRepository_RemoveNpc(ServerSnapshotNpcRepository* repo, uint16_t entity_id)
{
    if (!repo)
        return;
    int idx = SnapshotNpcRepository_FindNpcIndex(repo, entity_id);
    if (idx < 0)
        return;
    for (int i = idx; i + 1 < (int)repo->npc_count; i++)
        repo->npcs[i] = repo->npcs[i + 1];
    if (repo->npc_count > 0)
    {
        repo->npc_count--;
        memset(&repo->npcs[repo->npc_count], 0, sizeof(repo->npcs[0]));
    }
}

static int SnapshotNpcRepository_FindAppearanceCacheIndex(const ServerSnapshotNpcRepository* repo,
                                                    uint16_t entity_id)
{
    if (!repo)
        return -1;
    for (int i = 0; i < (int)repo->npc_count; i++)
    {
        if (repo->appearance_cache[i].entity_id == entity_id)
            return i;
    }
    return -1;
}

static int SnapshotNpcRepository_FindVisualIndex(const ServerSnapshotNpcRepository* repo,
                                           uint16_t entity_id)
{
    if (!repo)
        return -1;
    for (int i = 0; i < (int)repo->npc_count; i++)
    {
        if (repo->visuals[i].entity_id == entity_id)
            return i;
    }
    return -1;
}
