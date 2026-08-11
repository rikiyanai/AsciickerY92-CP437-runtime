// world_core.cpp — World creation / destruction
//
// Extracted from engine/world.cpp.
// SEE ALSO: world.h, world_internal.h

#include "world_internal.h"

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

World* CreateWorld()
{
    World* w = (World*)malloc(sizeof(World));

    w->meshes = 0;
    w->head_mesh = 0;
    w->tail_mesh = 0;
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
