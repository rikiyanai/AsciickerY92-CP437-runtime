// authoritative_item_server_state.cpp — Authoritative item/server state
//
// Owns AuthoritativeItemServerState: server-authoritative item/world-item
// mutation event observability and item visual state tracking.
// Extracted from Server state.

#include "authoritative_item_server_state.h"

#include <string.h>

static void AuthoritativeItemServerState_Reset(AuthoritativeItemServerState* ais)
{
    if (!ais)
        return;
    memset(ais, 0, sizeof(*ais));
}

static int AuthoritativeItemServerState_FindLocalItemIndex(const AuthoritativeItemServerState* ais,
                                                     uint16_t item_id)
{
    if (!ais)
        return -1;
    for (int i = 0; i < (int)ais->item_local_owned_count; i++)
    {
        if (ais->item_local_ids[i] == item_id)
            return i;
    }
    return -1;
}

static int AuthoritativeItemServerState_AddLocalItem(AuthoritativeItemServerState* ais,
                                               uint16_t item_id)
{
    if (!ais || item_id == 0)
        return -1;
    if (AuthoritativeItemServerState_FindLocalItemIndex(ais, item_id) >= 0)
        return -1; // already tracked
    if (ais->item_local_owned_count >= AuthoritativeItemServerState::MAX_AUTHORITATIVE_ITEMS)
        return -1;
    int idx = (int)ais->item_local_owned_count++;
    ais->item_local_ids[idx] = item_id;
    return idx;
}

static bool AuthoritativeItemServerState_RemoveLocalItem(AuthoritativeItemServerState* ais,
                                                   uint16_t item_id)
{
    if (!ais)
        return false;
    int idx = AuthoritativeItemServerState_FindLocalItemIndex(ais, item_id);
    if (idx < 0)
        return false;
    for (int i = idx; i + 1 < (int)ais->item_local_owned_count; i++)
        ais->item_local_ids[i] = ais->item_local_ids[i + 1];
    if (ais->item_local_owned_count > 0)
    {
        ais->item_local_owned_count--;
        ais->item_local_ids[ais->item_local_owned_count] = 0;
    }
    return true;
}

static int AuthoritativeItemServerState_FindVisualIndex(const AuthoritativeItemServerState* ais,
                                                  uint16_t item_id)
{
    if (!ais)
        return -1;
    for (int i = 0; i < (int)ais->item_count; i++)
    {
        if (ais->item_visuals[i].item_id == item_id)
            return i;
    }
    return -1;
}
