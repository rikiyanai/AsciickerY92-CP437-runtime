// world_instance.cpp — World mesh/instance lifecycle and BSP attachment
//
// Extracted from engine/world.cpp.
// SEE ALSO: world.h, world_internal.h

#include "world_internal.h"

void DeleteMesh(Mesh* m)
{
    if (!m)
        return;
    m->world->DelMesh(m);
}

Inst* CreateInst(Mesh* m, int flags, const double tm[16], const char* name, int story_id)
{
    if (!m)
        return 0;
    return m->world->AddInst(m, flags, tm, name, story_id);
}

Inst* CreateInst(World* w, Sprite* s, int flags, float pos[3], float yaw, int anim, int frame, int reps[4], const char* name, int story_id)
{
    if (!s)
        return 0;
    return w->AddInst(s, flags, pos, yaw, anim, frame, reps, name, story_id);
}

Inst* CreateInst(World* w, Item* item, int flags, float pos[3], float yaw, int story_id)
{
    if (!item)
        return 0;
    return w->AddInst(item, flags, pos, yaw, story_id);
}

void DeleteInst(Inst* i)
{
    if (!i)
        return;
    if (i->inst_type == Inst::INST_TYPE::MESH)
        ((MeshInst*)i)->mesh->world->DelInst(i);
    else if (i->inst_type == Inst::INST_TYPE::SPRITE)
        ((SpriteInst*)i)->w->DelInst(i);
    else if (i->inst_type == Inst::INST_TYPE::ITEM)
        ((ItemInst*)i)->w->DelInst(i);
}

bool DetachInst(World* w, Inst* inst)
{
    // move it to flat list
    if (!inst->bsp_parent && inst != w->root)
        return false; // already out

    if (inst == w->root)
    {
        w->root = 0;
    }
    else switch (inst->bsp_parent->type)
    {
        case BSP::BSP_TYPE_NODE_SHARE:
        case BSP::BSP_TYPE_NODE:
        {
            BSP_Node* n = (BSP_Node*)inst->bsp_parent;
            if (n->bsp_child[0] == inst)
                n->bsp_child[0] = 0;
            else if (n->bsp_child[1] == inst)
                n->bsp_child[1] = 0;
            else
            {
                BSP_NodeShare* s = (BSP_NodeShare*)inst->bsp_parent;
                if (inst->bsp_parent->type == BSP::BSP_TYPE_NODE_SHARE)
                {
                    if (inst->prev)
                        inst->prev->next = inst->next;
                    else
                        s->head = inst->next;
                    if (inst->next)
                        inst->next->prev = inst->prev;
                    else
                        s->tail = inst->prev;
                }
                else
                    assert(0);
            }
            break;
        }
        case BSP::BSP_TYPE_LEAF:
        {
            BSP_Leaf* l = (BSP_Leaf*)inst->bsp_parent;
            if (inst->prev)
                inst->prev->next = inst->next;
            else
                l->head = inst->next;
            if (inst->next)
                inst->next->prev = inst->prev;
            else
                l->tail = inst->prev;
            break;
        }
        case BSP::BSP_TYPE_INST:
            break;
    }

    inst->next = w->head_inst;
    inst->prev = 0;
    if (w->head_inst)
        w->head_inst->prev = inst;
    else
        w->tail_inst = inst;
    w->head_inst = inst;
    inst->bsp_parent = 0;

    return true;
}

bool AttachInst(World* w, Inst* inst)
{
    switch (inst->inst_type)
    {
        case Inst::INST_TYPE::SPRITE:
        {
            SpriteInst* si = (SpriteInst*)inst;
            Sprite* s = si->sprite;
            si->bbox[0] = s->proj_bbox[0] + si->pos[0];
            si->bbox[1] = s->proj_bbox[1] + si->pos[0];
            si->bbox[2] = s->proj_bbox[2] + si->pos[1];
            si->bbox[3] = s->proj_bbox[3] + si->pos[1];
            si->bbox[4] = s->proj_bbox[4] + si->pos[2];
            si->bbox[5] = s->proj_bbox[5] + si->pos[2];
            break;
        }
        case Inst::INST_TYPE::ITEM:
            abort();
        case Inst::INST_TYPE::MESH:
            ((MeshInst*)inst)->UpdateBox();
            break;
    }

    if (inst->bsp_parent || inst == w->root)
        return false;
    if (!w->root)
        return false;

    return w->root->InsertInst(w, inst);
}

void ShowInst(Inst* i)
{
    i->flags |= INST_FLAGS::INST_VISIBLE;
}

void HideInst(Inst* i)
{
    i->flags &= ~INST_FLAGS::INST_VISIBLE;
}

void UpdateSpriteInst(World* world, Inst* i, Sprite* sprite, const float pos[3], float yaw, int anim, int frame, const int reps[4])
{
    if (!i)
        return;
    assert(i->inst_type == Inst::INST_TYPE::SPRITE);

    DetachInst(world, i);

    SpriteInst* si = (SpriteInst*)i;
    si->sprite = sprite;
    si->pos[0] = pos[0];
    si->pos[1] = pos[1];
    si->pos[2] = pos[2];
    si->yaw = yaw;
    si->anim = anim;
    si->frame = frame;
    si->reps[0] = reps[0];
    si->reps[1] = reps[1];
    si->reps[2] = reps[2];
    si->reps[3] = reps[3];

    if (i->flags & INST_FLAGS::INST_VOLATILE)
        return;

    AttachInst(world, i);
}

// ============================================================================
// BSP::InsertInst — Insert instance into BSP subtree
// ============================================================================
//
// Attempts to insert Inst into this BSP node's subtree. Returns false if
// the instance bbox does not fit within this node's bbox. On success the
// instance is linked into the appropriate leaf or share list and its
// bsp_parent is set.
//
// Moved from world.cpp as part of world split (FL-2919).

bool BSP::InsertInst(World* w, Inst* i)
{
	// i->bbox must be up to date !

	if (bbox[0] > i->bbox[0] || bbox[1] < i->bbox[1] ||
		bbox[2] > i->bbox[2] || bbox[3] < i->bbox[3] ||
		bbox[4] > i->bbox[4] || bbox[5] < i->bbox[5])
	{
		return false;
	}

	switch (type)
	{
		case BSP::BSP_TYPE_INST:
			return false;

		case BSP::BSP_TYPE_NODE:
		case BSP::BSP_TYPE_NODE_SHARE:
		{
			BSP_Node* n = (BSP_Node*)this;
			if (n->bsp_child[0])
			{
				if (n->bsp_child[0]->InsertInst(w,i))
					return true;
			}
			if (n->bsp_child[1])
			{
				if (n->bsp_child[1]->InsertInst(w,i))
					return true;
			}

			bool ok = false;
			if (!n->bsp_child[0])
			{
				n->bsp_child[0] = i;
				ok = true;
			}
			if (!n->bsp_child[1])
			{
				n->bsp_child[1] = i;
				ok = true;
			}

			if (!ok)
			{
				BSP_NodeShare* s = (BSP_NodeShare*)this;
				if (type != BSP::BSP_TYPE_NODE_SHARE)
				{
					type = BSP::BSP_TYPE_NODE_SHARE;
					s->head = 0;
					s->tail = 0;
				}

				i->bsp_parent = this;

				if (i->prev)
					i->prev->next = i->next;
				else
					w->head_inst = i->next;

				if (i->next)
					i->next->prev = i->prev;
				else
					w->tail_inst = i->prev;

				i->prev = 0;

				i->next = s->head;
				if (s->head)
					s->head->prev = i;
				else
					s->tail = i;

				s->head = i;

				return true;
			}

			if (ok)
			{
				i->bsp_parent = this;

				if (i->prev)
					i->prev->next = i->next;
				else
					w->head_inst = i->next;

				if (i->next)
					i->next->prev = i->prev;
				else
					w->tail_inst = i->prev;

				i->next = 0;
				i->prev = 0;

				return true;
			}

			break;
		}

		case BSP::BSP_TYPE_LEAF:
		{
			BSP_Leaf* l = (BSP_Leaf*)this;

			i->bsp_parent = this;

			if (i->prev)
				i->prev->next = i->next;
			else
				w->head_inst = i->next;

			if (i->next)
				i->next->prev = i->prev;
			else
				w->tail_inst = i->prev;

			i->prev = 0;

			i->next = l->head;
			if (l->head)
				l->head->prev = i;
			else
				l->tail = i;

			l->head = i;

			return true;
		}

	}

	return false;
}

// ============================================================================
// SoftInstAdd / SoftInstDel / HardInstDel — undo/redo only
// ============================================================================
//
// These are used by the editor undo/redo system (urdo.h/urdo.cpp). They
// manage BSP attachment without double-free and without permanent deletion.
// Moved from world.cpp as part of world split (FL-2919).

void SoftInstAdd(Inst* i)
{
	World* w = GetInstWorld(i);

	// it is external thing

	i->next = w->head_inst;
	if (w->head_inst)
		w->head_inst->prev = i;
	else
		w->tail_inst = i;
	w->head_inst = i;

	// it is in flat list now

	AttachInst(w, i);

	// if there was place it is in bsp otherwise in flat list
	w->insts++;
}

void SoftInstDel(Inst* i)
{
	World* w = GetInstWorld(i);

	// it is in bsp or flat

	DetachInst(w, i);

	// it is in flat list now.

	if (i->prev)
		i->prev->next = i->next;
	else
		w->head_inst = i->next;

	if (i->next)
		i->next->prev = i->prev;
	else
		w->tail_inst = i->prev;

	// now it is external
	i->next = 0;
	i->prev = 0;

	w->insts--;
}

void HardInstDel(Inst* i)
{
	// assuming it is external !!!

	if (i->inst_type == Inst::INST_TYPE::ITEM)
	{
		// it destroys inst too!
		((ItemInst*)i)->item->inst = 0;
		DestroyItem(((ItemInst*)i)->item);
	}
	else
	if (i->inst_type == Inst::INST_TYPE::MESH)
	{
		MeshInst* m = (MeshInst*)i;
		// unref
		if (m->mesh)
		{
			MeshInst** s = &m->mesh->share_list;
			while (*s != m)
				s = &(*s)->share_next;
			*s = (*s)->share_next;
		}
	}

	if (i->name)
		free(i->name);

	free(i);
}
