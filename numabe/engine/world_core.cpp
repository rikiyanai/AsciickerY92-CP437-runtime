// world_core.cpp — World creation / destruction
//
// Extracted from engine/world.cpp.
// SEE ALSO: world.h, world_internal.h

#include "world_internal.h"

#include <atomic>

// FL-4208 H4: sole writer of World::generation. Monotonic, starts at 1 so the
// first world instance gets generation 1 (0 stays the fail-closed INVALID).
// fetch_add returns the previous value; nothing outside CreateWorld() touches it.
static std::atomic<uint64_t> g_next_world_generation{1};

uint64_t GetWorldGeneration(const World* w)
{
    return w ? w->generation : 0;
}

// delete_sprite_list: staging list used by DeleteWorld to collect SpriteInsts
// before freeing them. Declared extern in world_internal.h.
SpriteInst* delete_sprite_list = nullptr;
ItemInst* item_inst_cache = 0;

// BSP counters — declared extern in world_internal.h; defined here as the
// single owner so all world_*.cpp TUs share one set of counters.
int bsp_tests = 0;
int bsp_insts = 0;
int bsp_nodes = 0;

// WorldDebugEnabled() is defined in world_query.cpp (extern, env-flag check).

// Shared constructor body. candidate=true builds a CANDIDATE world for
// FL-4588 staged document validation: it deliberately does NOT advance the
// monotonic generation counter, because SurfaceId temporal identity is
// keyed on world generations and a REJECTED upload must leave the identity
// sequence of future live worlds untouched. A candidate world carries the
// fail-closed sentinel generation 0 until (and unless)
// WorldAssignLiveGeneration promotes it during an atomic document commit.
static World* CreateWorldInternal(bool candidate)
{
    World* w = (World*)malloc(sizeof(World));

    // FL-4208 H4: stamp the monotonic generation exactly once, here, at creation.
    // fetch_add returns the previous counter value; the first world gets 1.
    w->generation = candidate
        ? 0
        : g_next_world_generation.fetch_add(1, std::memory_order_relaxed);
	w->next_mesh_persistent_uid = 1;

    w->meshes = 0;
    w->head_mesh = 0;
    w->tail_mesh = 0;
    w->mesh_name_index = 0;
    w->insts = 0;
    w->temp_insts = 0;
    w->head_inst = 0;
    w->tail_inst = 0;
    w->editable = 0;
    w->root = 0;
    w->has_player_start = false;
    w->player_start_pos[0] = 0.0f;
    w->player_start_pos[1] = 0.0f;
    w->player_start_pos[2] = 0.0f;
    w->player_start_yaw = 0.0f;
    w->player_start_dir = 0.0f;

    return w;
}

World* CreateWorld()
{
    return CreateWorldInternal(false);
}

// FL-4588: candidate world for staged document validation (see
// CreateWorldInternal). Generation stays the fail-closed 0 sentinel.
World* CreateWorldCandidate()
{
    return CreateWorldInternal(true);
}

// FL-4588: promote a candidate world to a live one at atomic document
// commit. This is the ONLY moment a candidate may consume a generation —
// after full validation, immediately before the live swap — so rejected
// uploads never perturb the sequence. Returns false (and changes nothing)
// if the world already holds a live generation.
bool WorldAssignLiveGeneration(World* w)
{
    if (!w || w->generation != 0)
        return false;
    w->generation = g_next_world_generation.fetch_add(1, std::memory_order_relaxed);
    return true;
}

void DeleteWorld(World* w)
{
    if (!w)
        return;

    ResetDeleteItemQueue();
    delete_sprite_list = 0;

    Inst* i = w->head_inst;
    while (i)
    {
        if (i->inst_type == Inst::INST_TYPE::ITEM)
        {
            Item* item = ((ItemInst*)i)->item;
            if (item->purpose == Item::WORLD)
                QueueDeleteItem(item);
        }
        else if (i->inst_type == Inst::INST_TYPE::SPRITE)
        {
            SpriteInst* si = (SpriteInst*)i;
            si->sprite = (Sprite*)delete_sprite_list;
            delete_sprite_list = si;
        }
        i = i->next;
    }

    if (w->root)
        DeleteItemInsts(w->root, true);

    FlushDeleteItemQueue();

    if (w->root)
        DeleteSpriteInsts(w->root);

    SpriteInst* si = delete_sprite_list;
    while (si)
    {
        SpriteInst* n = (SpriteInst*)si->sprite;
        DeleteInst(si);
        si = n;
    }

    if (w->root)
        w->DeleteBSP(w->root);

    w->root = 0;

    while (w->meshes)
        w->DelMesh(w->head_mesh);

    w->FreeMeshNameIndex();
    free(w);
}

void DeleteItemInsts(BSP* bsp, bool all)
{
    if (bsp->type == BSP::BSP_TYPE_LEAF)
    {
        Inst* i = ((BSP_Leaf*)bsp)->head;
        while (i)
        {
            if (i->inst_type == Inst::INST_TYPE::ITEM)
            {
                Item* item = ((ItemInst*)i)->item;
                if (all || item->purpose == Item::WORLD)
                    QueueDeleteItem(item);
            }
            i = i->next;
        }
    }
    else if (bsp->type == BSP::BSP_TYPE_INST)
    {
        Inst* i = (Inst*)bsp;
        if (i->inst_type == Inst::INST_TYPE::ITEM)
        {
            Item* item = ((ItemInst*)i)->item;
            if (all || item->purpose == Item::WORLD)
                QueueDeleteItem(item);
        }
    }
    else
    {
        BSP_Node* n = (BSP_Node*)bsp;
        if (n->bsp_child[0]) DeleteItemInsts(n->bsp_child[0], all);
        if (n->bsp_child[1]) DeleteItemInsts(n->bsp_child[1], all);
    }
}

void DeleteSpriteInsts(BSP* bsp)
{
    if (bsp->type == BSP::BSP_TYPE_LEAF)
    {
        Inst* i = ((BSP_Leaf*)bsp)->head;
        while (i)
        {
            if (i->inst_type == Inst::INST_TYPE::SPRITE)
            {
                SpriteInst* si = (SpriteInst*)i;
                si->sprite = (Sprite*)delete_sprite_list;
                delete_sprite_list = si;
            }
            i = i->next;
        }
    }
    else if (bsp->type == BSP::BSP_TYPE_INST)
    {
        Inst* i = (Inst*)bsp;
        if (i->inst_type == Inst::INST_TYPE::SPRITE)
        {
            SpriteInst* si = (SpriteInst*)i;
            si->sprite = (Sprite*)delete_sprite_list;
            delete_sprite_list = si;
        }
    }
    else
    {
        BSP_Node* n = (BSP_Node*)bsp;
        if (n->bsp_child[0]) DeleteSpriteInsts(n->bsp_child[0]);
        if (n->bsp_child[1]) DeleteSpriteInsts(n->bsp_child[1]);
    }
}


void RebuildWorld(World* w, bool boxes)
{
    if (w)
        w->Rebuild(boxes);
}
