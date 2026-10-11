// world_editor_ops.cpp — High-level editor/undo operations
//
// Extracted from engine/world.cpp.
// Contains: ResetItemInsts, PurgeWorldItemInsts
// (the higher-level world operations).
//
// The low-level BSP tree operations remain in world.cpp.
//
// PurgeItemInstCache is defined here — canonical owner.
// Forward-declared in world_internal.h.

#include "world_internal.h"

void PurgeItemInstCache()
{
    ItemInst* ii = item_inst_cache;
    while (ii)
    {
        ItemInst* next = (ItemInst*)ii->next;
        free(ii);
        ii = next;
    }
    item_inst_cache = 0;
}

static void CloneItemInsts(World* w, BSP* bsp)
{
    if (bsp->type == BSP::BSP_TYPE_LEAF)
    {
        Inst* i = ((BSP_Leaf*)bsp)->head;
        while (i)
        {
            if (i->inst_type == Inst::INST_TYPE::ITEM)
            {
                Item* item = ((ItemInst*)i)->item;
                if (item->purpose == Item::EDIT)
                {
                    Item* clone = CreateItem();
                    memcpy(clone, item, sizeof(Item));
                    clone->purpose = Item::WORLD;
                    clone->inst = 0;
                    clone->inst = CreateInst(w, clone, i->flags, ((ItemInst*)i)->pos, ((ItemInst*)i)->yaw, ((ItemInst*)i)->story_id);
                }
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
            if (item->purpose == Item::EDIT)
            {
                Item* clone = CreateItem();
                memcpy(clone, item, sizeof(Item));
                clone->purpose = Item::WORLD;
                clone->inst = 0;
                clone->inst = CreateInst(w, clone, i->flags, ((ItemInst*)i)->pos, ((ItemInst*)i)->yaw, ((ItemInst*)i)->story_id);
            }
        }
    }
    else if (bsp->type == BSP::BSP_TYPE_NODE)
    {
        BSP_Node* n = (BSP_Node*)bsp;
        if (n->bsp_child[0])
            CloneItemInsts(w, n->bsp_child[0]);
        if (n->bsp_child[1])
            CloneItemInsts(w, n->bsp_child[1]);
    }
    else if (bsp->type == BSP::BSP_TYPE_NODE_SHARE)
    {
        BSP_NodeShare* s = (BSP_NodeShare*)bsp;
        if (s->bsp_child[0])
            CloneItemInsts(w, s->bsp_child[0]);
        if (s->bsp_child[1])
            CloneItemInsts(w, s->bsp_child[1]);
        Inst* i = s->head;
        while (i)
        {
            if (i->inst_type == Inst::INST_TYPE::ITEM)
            {
                Item* item = ((ItemInst*)i)->item;
                if (item->purpose == Item::EDIT)
                {
                    Item* clone = CreateItem();
                    memcpy(clone, item, sizeof(Item));
                    clone->purpose = Item::WORLD;
                    clone->inst = 0;
                    clone->inst = CreateInst(w, clone, i->flags, ((ItemInst*)i)->pos, ((ItemInst*)i)->yaw, ((ItemInst*)i)->story_id);
                }
            }
            i = i->next;
        }
    }
    else
    {
        assert(0);
    }
}

void ResetItemInsts(World* w)
{
    ResetDeleteItemQueue();

    if (w)
    {
        Inst* i = w->head_inst;
        while (i)
        {
            if (i->inst_type == Inst::INST_TYPE::ITEM)
            {
                Item* item = ((ItemInst*)i)->item;
                if (item->purpose == Item::WORLD)
                    QueueDeleteItem(item);
            }
            i = i->next;
        }

        if (w->root)
            DeleteItemInsts(w->root, false);
    }

    FlushDeleteItemQueue();

    if (w && w->root)
        CloneItemInsts(w, w->root);
    RebuildWorld(w);
}

void PurgeWorldItemInsts(World* w)
{
    ResetDeleteItemQueue();

    if (w)
    {
        Inst* i = w->head_inst;
        while (i)
        {
            if (i->inst_type == Inst::INST_TYPE::ITEM)
            {
                Item* item = ((ItemInst*)i)->item;
                if (item->purpose == Item::WORLD)
                    QueueDeleteItem(item);
            }
            i = i->next;
        }

        if (w->root)
            DeleteItemInsts(w->root, false);
    }

    FlushDeleteItemQueue();

    if (w)
        RebuildWorld(w);
}
