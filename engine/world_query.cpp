// world_query.cpp — Read-only world queries and instance accessors
//
// Extracted from engine/world.cpp.
// Contains: QueryWorld, QueryWorldBSP, CollectMeshInsts, HitWorld,
// QueryWorldItems, all mesh/instance getters, and IsMaterialUsedInWorld.
//
// SEE ALSO: world.h, world_internal.h

#include "world_internal.h"

// ============================================================================
// QueryWorld
// ============================================================================

void QueryWorld(World* w, int planes, double plane[][4], QueryWorldCB* cb, void* cookie)
{
    if (!w) return;
    if (WorldDebugEnabled() && !w->root)
    {
        fprintf(stderr, "[World] QueryWorld: root is null insts=%d temp=%d\n",
            w->insts, w->temp_insts);
    }
    w->Query(planes, plane, cb, cookie);
}

// ============================================================================
// CollectMeshInsts helpers
// ============================================================================

static void AppendInstUnique(Inst*** arr, int* count, int* cap, Inst* inst)
{
    if (!inst) return;
    for (int i = 0; i < *count; i++)
    {
        if ((*arr)[i] == inst)
            return;
    }
    if (*count >= *cap)
    {
        *cap = *cap ? *cap * 2 : 64;
        *arr = (Inst**)realloc(*arr, sizeof(Inst*) * *cap);
    }
    (*arr)[(*count)++] = inst;
}

static void CollectInstsFromBSP(BSP* bsp, Inst*** arr, int* count, int* cap)
{
    if (!bsp) return;

    switch (bsp->type)
    {
    case BSP::BSP_TYPE_INST:
        AppendInstUnique(arr, count, cap, (Inst*)bsp);
        return;
    case BSP::BSP_TYPE_NODE:
    {
        BSP_Node* n = (BSP_Node*)bsp;
        CollectInstsFromBSP(n->bsp_child[0], arr, count, cap);
        CollectInstsFromBSP(n->bsp_child[1], arr, count, cap);
        return;
    }
    case BSP::BSP_TYPE_NODE_SHARE:
    {
        BSP_NodeShare* s = (BSP_NodeShare*)bsp;
        CollectInstsFromBSP(s->bsp_child[0], arr, count, cap);
        CollectInstsFromBSP(s->bsp_child[1], arr, count, cap);
        Inst* i = s->head;
        while (i)
        {
            AppendInstUnique(arr, count, cap, i);
            i = i->next;
        }
        return;
    }
    case BSP::BSP_TYPE_LEAF:
    {
        BSP_Leaf* l = (BSP_Leaf*)bsp;
        Inst* i = l->head;
        while (i)
        {
            AppendInstUnique(arr, count, cap, i);
            i = i->next;
        }
        return;
    }
    }
}

// ============================================================================
// CollectMeshInsts
// ============================================================================

int CollectMeshInsts(World* w, Inst*** out)
{
    if (!w || !out) return 0;
    int count = 0;
    int cap = 0;
    *out = nullptr;

    if (w->root)
        CollectInstsFromBSP(w->root, out, &count, &cap);

    for (Inst* i = w->head_inst; i; i = i->next)
    {
        if (i->inst_type == Inst::INST_TYPE::MESH)
            AppendInstUnique(out, &count, &cap, i);
    }
    return count;
}

// ============================================================================
// QueryWorldBSP
// ============================================================================

void QueryWorldBSP(World* w, int planes, double plane[][4],
    void (*cb)(int level, const float bbox[6], void* cookie), void* cookie)
{
    if (!w || !w->root || !cb) return;
    World::QueryBSP(1, w->root, planes, plane, cb, cookie);
}

// ============================================================================
// Mesh getters
// ============================================================================

Mesh* GetFirstMesh(World* w) { return w ? w->head_mesh : nullptr; }
Mesh* GetLastMesh(World* w)  { return w ? w->tail_mesh : nullptr; }
Mesh* GetPrevMesh(Mesh* m)   { return m ? m->prev : nullptr; }
Mesh* GetNextMesh(Mesh* m)   { return m ? m->next : nullptr; }
World* GetMeshWorld(Mesh* m) { return m ? m->world : nullptr; }

int GetMeshName(Mesh* m, char* buf, int size)
{
    if (!m || !buf || size <= 0) return 0;
    int len = (int)strlen(m->name);
    if (len >= size) len = size - 1;
    memcpy(buf, m->name, len);
    buf[len] = 0;
    return len;
}

void GetMeshBBox(Mesh* m, float bbox[6])
{
    if (!m) return;
    for (int i = 0; i < 6; i++) bbox[i] = m->bbox[i];
}

void QueryMesh(Mesh* m, void (*cb)(float coords[9], uint8_t colors[12],
    uint32_t visual, void* cookie), void* cookie)
{
    if (!m || !cb) return;

    float coords[9];
    uint8_t colors[12];

    Face* f = m->head_face;
    while (f)
    {
        for (int j = 0; j < 3; j++)
        {
            coords[j * 3 + 0] = f->abc[j]->xyzw[0];
            coords[j * 3 + 1] = f->abc[j]->xyzw[1];
            coords[j * 3 + 2] = f->abc[j]->xyzw[2];
            colors[j * 4 + 0] = f->abc[j]->rgba[0];
            colors[j * 4 + 1] = f->abc[j]->rgba[1];
            colors[j * 4 + 2] = f->abc[j]->rgba[2];
            colors[j * 4 + 3] = f->abc[j]->rgba[3];
        }
        cb(coords, colors, f->visual, cookie);
        f = f->next;
    }

    Line* l = m->head_line;
    while (l)
    {
        coords[0] = l->ab[0]->xyzw[0];
        coords[1] = l->ab[0]->xyzw[1];
        coords[2] = l->ab[0]->xyzw[2];
        colors[0] = l->ab[0]->rgba[0];
        colors[1] = l->ab[0]->rgba[1];
        colors[2] = l->ab[0]->rgba[2];
        colors[3] = l->ab[0]->rgba[3];

        coords[3] = l->ab[1]->xyzw[0];
        coords[4] = l->ab[1]->xyzw[1];
        coords[5] = l->ab[1]->xyzw[2];
        colors[4] = l->ab[1]->rgba[0];
        colors[5] = l->ab[1]->rgba[1];
        colors[6] = l->ab[1]->rgba[2];
        colors[7] = l->ab[1]->rgba[3];

        cb(coords, colors, l->visual, cookie);
        l = l->next;
    }
}

int QueryMeshLimited(Mesh* m, int max_callbacks,
    void (*cb)(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie),
    void* cookie)
{
    if (!m || !cb || max_callbacks <= 0)
        return 0;

    float coords[9];
    uint8_t colors[12];
    int emitted = 0;

    Face* f = m->head_face;
    while (f && emitted < max_callbacks)
    {
        if ((f->visual & (1u << 31)) != 0)
        {
            f = f->next;
            continue;
        }
        if (f->abc[0]->rgba[3] > 128 || f->abc[1]->rgba[3] > 128 || f->abc[2]->rgba[3] > 128)
        {
            f = f->next;
            continue;
        }
        for (int j = 0; j < 3; j++)
        {
            coords[j * 3 + 0] = f->abc[j]->xyzw[0];
            coords[j * 3 + 1] = f->abc[j]->xyzw[1];
            coords[j * 3 + 2] = f->abc[j]->xyzw[2];
            colors[j * 4 + 0] = f->abc[j]->rgba[0];
            colors[j * 4 + 1] = f->abc[j]->rgba[1];
            colors[j * 4 + 2] = f->abc[j]->rgba[2];
            colors[j * 4 + 3] = f->abc[j]->rgba[3];
        }
        cb(coords, colors, f->visual, cookie);
        emitted++;
        f = f->next;
    }

    Line* l = m->head_line;
    while (l && emitted < max_callbacks)
    {
        coords[0] = l->ab[0]->xyzw[0];
        coords[1] = l->ab[0]->xyzw[1];
        coords[2] = l->ab[0]->xyzw[2];
        colors[0] = l->ab[0]->rgba[0];
        colors[1] = l->ab[0]->rgba[1];
        colors[2] = l->ab[0]->rgba[2];
        colors[3] = l->ab[0]->rgba[3];

        coords[3] = l->ab[1]->xyzw[0];
        coords[4] = l->ab[1]->xyzw[1];
        coords[5] = l->ab[1]->xyzw[2];
        colors[4] = l->ab[1]->rgba[0];
        colors[5] = l->ab[1]->rgba[1];
        colors[6] = l->ab[1]->rgba[2];
        colors[7] = l->ab[1]->rgba[3];

        cb(coords, colors, l->visual, cookie);
        emitted++;
        l = l->next;
    }

    return emitted;
}

void* GetMeshCookie(Mesh* m) { return m ? m->cookie : nullptr; }
void SetMeshCookie(Mesh* m, void* cookie) { if (m) m->cookie = cookie; }
int GetMeshFaces(Mesh* m) { return m ? m->faces : 0; }

// ============================================================================
// Instance accessors
// ============================================================================

Mesh* GetInstMesh(Inst* i)
{
    return (i && i->inst_type == Inst::INST_TYPE::MESH) ? ((MeshInst*)i)->mesh : nullptr;
}

int GetInstFlags(Inst* i) { return i ? i->flags : 0; }
void SetInstFlags(Inst* i, int flags) { if (i) i->flags = flags; }
int GetInstStoryID(Inst* i) { return i ? i->story_id : 0; }
const char* GetInstName(Inst* i) { return i ? i->name : nullptr; }

void SetInstStoryID(Inst* i, int id) { if (i) i->story_id = id; }

bool GetInstTM(Inst* i, double tm[16])
{
    if (i && i->inst_type == Inst::INST_TYPE::MESH)
    {
        memcpy(tm, ((MeshInst*)i)->tm, sizeof(double[16]));
        return true;
    }
    return false;
}

void SetInstTM(Inst* i, const double tm[16])
{
    if (i && i->inst_type == Inst::INST_TYPE::MESH)
    {
        MeshInst* mi = (MeshInst*)i;
        memcpy(mi->tm, tm, sizeof(double) * 16);
        mi->UpdateBox();
    }
}

void GetInstBBox(Inst* i, double bbox[6])
{
    if (!i) return;
    for (int j = 0; j < 6; j++) bbox[j] = i->bbox[j];
}

World* GetInstWorld(Inst* i)
{
    if (!i) return nullptr;
    if (i->inst_type == Inst::INST_TYPE::SPRITE)
        return ((SpriteInst*)i)->w;
    if (i->inst_type == Inst::INST_TYPE::ITEM)
        return ((ItemInst*)i)->w;
    if (i->inst_type == Inst::INST_TYPE::MESH)
        return ((MeshInst*)i)->mesh ? ((MeshInst*)i)->mesh->world : nullptr;
    return nullptr;
}

Sprite* GetInstSprite(Inst* i, float pos[3], float* yaw, int* anim, int* frame, int reps[4])
{
    if (!i || i->inst_type != Inst::SPRITE) return nullptr;
    SpriteInst* si = (SpriteInst*)i;
    if (pos) { pos[0] = si->pos[0]; pos[1] = si->pos[1]; pos[2] = si->pos[2]; }
    if (yaw) *yaw = si->yaw;
    if (anim) *anim = si->anim;
    if (frame) *frame = si->frame;
    if (reps) { reps[0] = si->reps[0]; reps[1] = si->reps[1]; reps[2] = si->reps[2]; reps[3] = si->reps[3]; }
    return si->sprite;
}

void* GetInstSpriteData(Inst* i)
{
    return (i && i->inst_type == Inst::SPRITE) ? ((SpriteInst*)i)->data : nullptr;
}

bool SetInstSpriteData(Inst* i, void* data)
{
    if (!i || i->inst_type != Inst::SPRITE) return false;
    ((SpriteInst*)i)->data = data;
    return true;
}

Item* GetInstItem(Inst* i, float pos[3], float* yaw)
{
    if (!i || i->inst_type != Inst::ITEM) return nullptr;
    ItemInst* ii = (ItemInst*)i;
    if (pos) { pos[0] = ii->pos[0]; pos[1] = ii->pos[1]; pos[2] = ii->pos[2]; }
    if (yaw) *yaw = ii->yaw;
    return ii->item;
}

// ============================================================================
// QueryWorldItems
// ============================================================================

static void QueryWorldItemInst(Inst* inst, WorldItemInstCB cb, void* cookie)
{
    if (!inst || !cb || inst->inst_type != Inst::ITEM) return;
    ItemInst* ii = (ItemInst*)inst;
    cb(inst, ii->item, ii->pos, ii->yaw, ii->story_id, cookie);
}

static void QueryWorldItemsBSP(BSP* bsp, WorldItemInstCB cb, void* cookie)
{
    if (!bsp || !cb) return;
    switch (bsp->type)
    {
    case BSP::BSP_TYPE_INST:
        QueryWorldItemInst((Inst*)bsp, cb, cookie);
        return;
    case BSP::BSP_TYPE_NODE:
    {
        BSP_Node* n = (BSP_Node*)bsp;
        QueryWorldItemsBSP(n->bsp_child[0], cb, cookie);
        QueryWorldItemsBSP(n->bsp_child[1], cb, cookie);
        return;
    }
    case BSP::BSP_TYPE_NODE_SHARE:
    {
        BSP_NodeShare* s = (BSP_NodeShare*)bsp;
        QueryWorldItemsBSP(s->bsp_child[0], cb, cookie);
        QueryWorldItemsBSP(s->bsp_child[1], cb, cookie);
        Inst* i = s->head;
        while (i)
        {
            QueryWorldItemInst(i, cb, cookie);
            i = i->next;
        }
        return;
    }
    case BSP::BSP_TYPE_LEAF:
    {
        BSP_Leaf* l = (BSP_Leaf*)bsp;
        Inst* i = l->head;
        while (i)
        {
            QueryWorldItemInst(i, cb, cookie);
            i = i->next;
        }
        return;
    }
    }
}

void QueryWorldItems(World* w, WorldItemInstCB cb, void* cookie)
{
    if (!w || !cb) return;
    QueryWorldItemsBSP(w->root, cb, cookie);
    for (Inst* i = w->head_inst; i; i = i->next)
        QueryWorldItemInst(i, cb, cookie);
}

// ============================================================================
// HitWorld
// ============================================================================

Inst* HitWorld(World* w, double p[3], double v[3], double ret[3], double nrm[3],
    bool positive_only, const HitFilter& filter, uint8_t* out_color)
{
    return w->HitWorld(p, v, ret, nrm, positive_only, filter, out_color);
}
static Inst* HitWorld0(BSP* q, double ray[10], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
	{
		if (!q)
			return 0;

        const float x[2] = {q->bbox[0],q->bbox[1]};
		const float y[2] = {q->bbox[2],q->bbox[3]};
		const float z[2] = {q->bbox[4],q->bbox[5]};

		if (positive_only)
		{
			// do not recurse if all 8 corners projected onto ray are negative
		}

		if (ray[1] - z[0] * ray[3] + ray[5] * x[1] > 0 ||
			ray[5] * y[1] - ray[0] - z[0] * ray[4] > 0 ||
			ray[2] - ray[4] * x[0] + ray[3] * y[1] > 0 ||
			z[1] * ray[3] - ray[5] * x[0] - ray[1] > 0 ||
			ray[0] + z[1] * ray[4] - ray[5] * y[0] > 0 ||
			ray[4] * x[1] - ray[3] * y[0] - ray[2] > 0)
			return 0;

		if (q->type == BSP::TYPE::BSP_TYPE_INST)
		{
			Inst* inst = (Inst*)q;
			if (filter.skip_volatile && (inst->flags & INST_VOLATILE))
				return 0;

			if (inst->inst_type == Inst::INST_TYPE::MESH)
			{
				if (((MeshInst*)inst)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::SPRITE)
			{
				if (filter.sprites_too && ((SpriteInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::ITEM)
			{
				if (filter.sprites_too && ((ItemInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			return 0;
		}
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)q;
            Inst* i = HitWorld0(n->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld0(n->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)q;

            Inst* i = HitWorld0(s->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld0(s->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;

            j = s->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

                j=j->next;
            }
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)q;

            Inst* i = 0;
            Inst* j = l->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

                j=j->next;
            }
            return i;
        }
        
		return 0;
	}

static Inst* HitWorld1(BSP* q, double ray[10], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
	{
		if (!q)
			return 0;

		const float x[2] = { q->bbox[0],q->bbox[1] };
		const float y[2] = { q->bbox[2],q->bbox[3] };
		const float z[2] = { q->bbox[4],q->bbox[5] };

		if (positive_only)
		{
			// do not recurse if all 8 corners projected onto ray are negative
		}

		if (ray[5] * y[1] - ray[0] - z[0] * ray[4] > 0 ||
			z[0] * ray[3] - ray[5] * x[0] - ray[1] > 0 ||
			ray[2] - ray[4] * x[0] + ray[3] * y[0] > 0 ||
			ray[0] + z[1] * ray[4] - ray[5] * y[0] > 0 ||
			ray[1] - z[1] * ray[3] + ray[5] * x[1] > 0 ||
			ray[4] * x[1] - ray[3] * y[1] - ray[2] > 0)
			return 0;

		if (q->type == BSP::TYPE::BSP_TYPE_INST)
		{
			Inst* inst = (Inst*)q;
			if (filter.skip_volatile && (inst->flags & INST_VOLATILE))
				return 0;

			if (inst->inst_type == Inst::INST_TYPE::MESH)
			{
				if (((MeshInst*)inst)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::SPRITE)
			{
				if (filter.sprites_too && ((SpriteInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::ITEM)
			{
				if (filter.sprites_too && ((ItemInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			return 0;
		}
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)q;
            Inst* i = HitWorld1(n->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld1(n->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)q;

            Inst* i = HitWorld1(s->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld1(s->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;

            j = s->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)q;

            Inst* i = 0;
            Inst* j = l->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

                j=j->next;
            }
            return i;
        }

		return 0;
	}

static Inst* HitWorld2(BSP* q, double ray[10], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
	{
		if (!q)
			return 0;

		const float x[2] = { q->bbox[0],q->bbox[1] };
		const float y[2] = { q->bbox[2],q->bbox[3] };
		const float z[2] = { q->bbox[4],q->bbox[5] };

		if (positive_only)
		{
			// do not recurse if all 8 corners projected onto ray are negative
		}

		if (ray[0] + z[0] * ray[4] - ray[5] * y[0] > 0 ||
			ray[1] - z[0] * ray[3] + ray[5] * x[1] > 0 ||
			ray[2] + ray[3] * y[1] - ray[4] * x[1] > 0 ||
			ray[5] * y[1] - ray[0] - z[1] * ray[4] > 0 ||
			z[1] * ray[3] - ray[5] * x[0] - ray[1] > 0 ||
			ray[4] * x[0] - ray[3] * y[0] - ray[2] > 0)
			return 0;

		if (q->type == BSP::TYPE::BSP_TYPE_INST)
		{
			Inst* inst = (Inst*)q;
			if (filter.skip_volatile && (inst->flags & INST_VOLATILE))
				return 0;

			if (inst->inst_type == Inst::INST_TYPE::MESH)
			{
				if (((MeshInst*)inst)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::SPRITE)
			{
				if (filter.sprites_too && ((SpriteInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::ITEM)
			{
				if (filter.sprites_too && ((ItemInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			return 0;
		}
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)q;
            Inst* i = HitWorld2(n->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld2(n->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)q;

            Inst* i = HitWorld2(s->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld2(s->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;

            j = s->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
			}
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)q;

            Inst* i = 0;
            Inst* j = l->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }

		return 0;
	}

static Inst* HitWorld3(BSP* q, double ray[10], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
	{
		if (!q)
			return 0;

		const float x[2] = { q->bbox[0],q->bbox[1] };
		const float y[2] = { q->bbox[2],q->bbox[3] };
		const float z[2] = { q->bbox[4],q->bbox[5] };

		if (positive_only)
		{
			// do not recurse if all 8 corners projected onto ray are negative
		}

		if (z[0] * ray[3] - ray[5] * x[0] - ray[1] > 0 ||
			ray[0] + z[0] * ray[4] - ray[5] * y[0] > 0 ||
			ray[2] - ray[4] * x[1] + ray[3] * y[0] > 0 ||
			ray[1] - z[1] * ray[3] + ray[5] * x[1] > 0 ||
			ray[5] * y[1] - ray[0] - z[1] * ray[4] > 0 ||
			ray[4] * x[0] - ray[3] * y[1] - ray[2] > 0)
			return 0;

		if (q->type == BSP::TYPE::BSP_TYPE_INST)
		{
			Inst* inst = (Inst*)q;
			if (filter.skip_volatile && (inst->flags & INST_VOLATILE))
				return 0;

			if (inst->inst_type == Inst::INST_TYPE::MESH)
			{
				if (((MeshInst*)inst)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::SPRITE)
			{
				if (filter.sprites_too && ((SpriteInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::ITEM)
			{
				if (filter.sprites_too && ((ItemInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}

			return 0;
		}
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)q;
            Inst* i = HitWorld3(n->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld3(n->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)q;

            Inst* i = HitWorld3(s->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld3(s->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;

            j = s->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)q;

            Inst* i = 0;
            Inst* j = l->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }

		return 0;
	}

static Inst* HitWorld4(BSP* q, double ray[10], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
	{
		if (!q)
			return 0;

		const float x[2] = { q->bbox[0],q->bbox[1] };
		const float y[2] = { q->bbox[2],q->bbox[3] };
		const float z[2] = { q->bbox[4],q->bbox[5] };

		if (positive_only)
		{
			// do not recurse if all 8 corners projected onto ray are negative
		}

		if (ray[0] + z[1] * ray[4] - ray[5] * y[1] > 0 ||
			-ray[1] + z[1] * ray[3] - ray[5] * x[1] > 0 ||
			-ray[2] + ray[4] * x[1] - ray[3] * y[0] > 0 ||
			-ray[0] - z[0] * ray[4] + ray[5] * y[0] > 0 ||
			ray[1] - z[0] * ray[3] + ray[5] * x[0] > 0 ||
			ray[2] - ray[4] * x[0] + ray[3] * y[1] > 0)
			return 0;

		if (q->type == BSP::TYPE::BSP_TYPE_INST)
		{
			Inst* inst = (Inst*)q;
			if (filter.skip_volatile && (inst->flags & INST_VOLATILE))
				return 0;

			if (inst->inst_type == Inst::INST_TYPE::MESH)
			{
				if (((MeshInst*)inst)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::SPRITE)
			{
				if (filter.sprites_too && ((SpriteInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::ITEM)
			{
				if (filter.sprites_too && ((ItemInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}

			return 0;
		}
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)q;
            Inst* i = HitWorld4(n->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld4(n->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)q;

            Inst* i = HitWorld4(s->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld4(s->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;

            j = s->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)q;

            Inst* i = 0;
            Inst* j = l->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }

		return 0;
	}

static Inst* HitWorld5(BSP* q, double ray[10], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
	{
		if (!q)
			return 0;

		const float x[2] = { q->bbox[0],q->bbox[1] };
		const float y[2] = { q->bbox[2],q->bbox[3] };
		const float z[2] = { q->bbox[4],q->bbox[5] };

		if (positive_only)
		{
			// do not recurse if all 8 corners projected onto ray are negative
		}

		if (ray[1] - z[1] * ray[3] + ray[5] * x[0] > 0 ||
			ray[0] + z[1] * ray[4] - ray[5] * y[1] > 0 ||
			-ray[2] + ray[4] * x[1] - ray[3] * y[1] > 0 ||
			-ray[1] + z[0] * ray[3] - ray[5] * x[1] > 0 ||
			-ray[0] - z[0] * ray[4] + ray[5] * y[0] > 0 ||
			ray[2] - ray[4] * x[0] + ray[3] * y[0] > 0)
			return 0;

		if (q->type == BSP::TYPE::BSP_TYPE_INST)
		{
			Inst* inst = (Inst*)q;
			if (filter.skip_volatile && (inst->flags & INST_VOLATILE))
				return 0;

			if (inst->inst_type == Inst::INST_TYPE::MESH)
			{
				if (((MeshInst*)inst)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::SPRITE)
			{
				if (filter.sprites_too && ((SpriteInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::ITEM)
			{
				if (filter.sprites_too && ((ItemInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}

			return 0;
		}
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)q;
            Inst* i = HitWorld5(n->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld5(n->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)q;

            Inst* i = HitWorld5(s->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld5(s->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;

            j = s->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)q;

            Inst* i = 0;
            Inst* j = l->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }

		return 0;
	}

static Inst* HitWorld6(BSP* q, double ray[10], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
	{
		if (!q)
			return 0;

		const float x[2] = { q->bbox[0],q->bbox[1] };
		const float y[2] = { q->bbox[2],q->bbox[3] };
		const float z[2] = { q->bbox[4],q->bbox[5] };

		if (positive_only)
		{
			// do not recurse if all 8 corners projected onto ray are negative
		}

		if (-ray[1] + z[1] * ray[3] - ray[5] * x[1] > 0 ||
			-ray[0] - z[1] * ray[4] + ray[5] * y[0] > 0 ||
			-ray[2] + ray[4] * x[0] - ray[3] * y[0] > 0 ||
			ray[1] - z[0] * ray[3] + ray[5] * x[0] > 0 ||
			ray[0] + z[0] * ray[4] - ray[5] * y[1] > 0 ||
			ray[2] - ray[4] * x[1] + ray[3] * y[1] > 0)
			return 0;

		if (q->type == BSP::TYPE::BSP_TYPE_INST)
		{
			Inst* inst = (Inst*)q;
			if (filter.skip_volatile && (inst->flags & INST_VOLATILE))
				return 0;

			if (inst->inst_type == Inst::INST_TYPE::MESH)
			{
				if (((MeshInst*)inst)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::SPRITE)
			{
				if (filter.sprites_too && ((SpriteInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::ITEM)
			{
				if (filter.sprites_too && ((ItemInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}

			return 0;
		}
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)q;
            Inst* i = HitWorld6(n->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld6(n->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)q;

            Inst* i = HitWorld6(s->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld6(s->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;

            j = s->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)q;

            Inst* i = 0;
            Inst* j = l->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }

		return 0;
	}

static Inst* HitWorld7(BSP* q, double ray[10], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
	{
		if (!q)
			return 0;

		const float x[2] = { q->bbox[0],q->bbox[1] };
		const float y[2] = { q->bbox[2],q->bbox[3] };
		const float z[2] = { q->bbox[4],q->bbox[5] };

		if (positive_only)
		{
			// do not recurse if all 8 corners projected onto ray are negative
		}

		if (-ray[0] - z[1] * ray[4] + ray[5] * y[0] > 0 ||
			ray[1] - z[1] * ray[3] + ray[5] * x[0] > 0 ||
			-ray[2] + ray[4] * x[0] - ray[3] * y[1] > 0 ||
			ray[0] + z[0] * ray[4] - ray[5] * y[1] > 0 ||
			-ray[1] + z[0] * ray[3] - ray[5] * x[1] > 0 ||
			ray[2] - ray[4] * x[1] + ray[3] * y[0] > 0)
			return 0;

		if (q->type == BSP::TYPE::BSP_TYPE_INST)
		{
			Inst* inst = (Inst*)q;
			if (filter.skip_volatile && (inst->flags & INST_VOLATILE))
				return 0;

			if (inst->inst_type == Inst::INST_TYPE::MESH)
			{
				if (((MeshInst*)inst)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::SPRITE)
			{
				if (filter.sprites_too && ((SpriteInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}
			else
			if (inst->inst_type == Inst::INST_TYPE::ITEM)
			{
				if (filter.sprites_too && ((ItemInst*)inst)->Hit(ray, ret, positive_only))
					return inst;
				else
					return 0;
			}

			return 0;
		}
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)q;
            Inst* i = HitWorld7(n->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld7(n->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)q;

            Inst* i = HitWorld7(s->bsp_child[0], ray, ret, nrm, positive_only, filter, out_color);
            Inst* j = HitWorld7(s->bsp_child[1], ray, ret, nrm, positive_only, filter, out_color);
            i = j ? j : i;

            j = s->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }
        else
        if (q->type == BSP::TYPE::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)q;

            Inst* i = 0;
            Inst* j = l->head;
            while (j)
            {
				if (filter.skip_volatile && (j->flags & INST_VOLATILE))
				{
					j=j->next;
					continue;
				}

				if (j->inst_type == Inst::INST_TYPE::MESH)
				{
					if (((MeshInst*)j)->HitFace(ray, ret, nrm, positive_only, filter.solid_only, out_color))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::SPRITE && filter.sprites_too)
				{
					if (((SpriteInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}
				else
				if (j->inst_type == Inst::INST_TYPE::ITEM && filter.sprites_too)
				{
					if (((ItemInst*)j)->Hit(ray, ret, positive_only))
						i = j;
				}

				j = j->next;
            }
            return i;
        }

		return 0;
	}


    // RAY HIT using plucker
    Inst* World::HitWorld(double p[3], double v[3], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color)
    {
		if (!root)
			return 0;

		/*
		double max_z = 0;
		if (positive_only)
		{
			max_z = p[2];
			p[0] += v[0];
			p[1] += v[1];
			p[2] += v[2];
		}
		*/

		// p should be projected to the BOTTOM plane!
		double ray[] =
		{
			p[1] * v[2] - p[2] * v[1],
			p[2] * v[0] - p[0] * v[2],
			p[0] * v[1] - p[1] * v[0],
			v[0], v[1], v[2],
			p[0], p[1], p[2], // used by triangle-ray intersection
			FLT_MAX
		};

		int sign_case = 0;

		if (v[0] >= 0)
			sign_case |= 1;
		if (v[1] >= 0)
			sign_case |= 2;
		if (v[2] >= 0)
			sign_case |= 4;

		// assert((sign_case & 4) == 0); // watching from the bottom? -> raytraced reflections?

		static Inst* (*const func_vect[])(BSP* q, double ray[10], double ret[3], double nrm[3], bool, const HitFilter&, uint8_t*) =
		{
			HitWorld0,
			HitWorld1,
			HitWorld2,
			HitWorld3,

			HitWorld4,
			HitWorld5,
			HitWorld6,
			HitWorld7,
		};

		/*
		if (!positive_only)
		{
			// otherwie ret must be preinitialized
			ret[0] = p[0];
			ret[1] = p[1];
			ret[2] = p[2];
		}
		*/

		Inst* inst = func_vect[sign_case](root, ray, ret, nrm, positive_only, filter, out_color);
		return inst;
    }


// ============================================================================
// IsMaterialUsedInWorld
// ============================================================================

bool IsMaterialUsedInWorld(World* w, int mat_id)
{
    if (!w) return false;
    Mesh* m = w->head_mesh;
    while (m)
    {
        Face* f = m->head_face;
        while (f)
        {
            if ((f->visual & 0xFF) == (unsigned)mat_id)
                return true;
            f = f->next;
        }
        m = m->next;
    }
    return false;
}

// WorldDebugEnabled — moved inline to world_internal.h
