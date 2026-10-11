#pragma once

#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <ctype.h>
#include <math.h>
#include <assert.h>
#include <float.h>

#include "sprite.h"
#include "world.h"
#include "matrix.h"
#include "inventory.h"

// Minimap marker struct and functions moved to world_minimap_markers.h/cpp
#include "world_minimap_markers.h"

// Inline so all TUs sharing world_internal.h can elide the call.
// Static locals are shared across TUs (C++ inline semantics).
inline bool WorldDebugEnabled()
{
    static bool checked = false;
    static bool enabled = false;
    if (!checked)
    {
        checked = true;
        enabled = (getenv("ASCIICKER_WORLD_DEBUG") != nullptr);
    }
    return enabled;
}

// Internal type definitions (Vert, Face, Mesh, BSP, Inst, etc.)
// ═══════════════════════════════════════════════════════════

struct Line;
struct Face;

struct Vert
{
    Mesh* mesh;

    // in mesh
	Vert* next;
	Vert* prev;

	Face* face_list;
	Line* line_list;

    // regardless of mesh type (2d/3d)
    // we keep z (makes it easier to switch mesh types back and forth)

	int vert_id; // index into 
	float xyzw[4];
	unsigned char rgba[4];

    bool sel;
};

struct Face
{
    Mesh* mesh;

    // in mesh
	Face* next;
	Face* prev;

	Vert* abc[3];
	Face* share_next[3]; // next triangle sharing given vertex

	uint32_t visual; // matid_8bits, 3 x {shade_7bits,elev_1bit}

    bool freestyle;
};

struct Line
{
    Mesh* mesh;

    // in mesh
	Line* next;
	Line* prev;

	Vert* ab[2];
	Line* share_next[2]; // next line sharing given vertex

	uint32_t visual; // line style & height(depth) offset?
};

struct Inst;
struct World;
struct MeshInst;

struct Mesh
{
    World* world;
    char* name; // in form of library path?
    void* cookie;

    // in world
	Mesh* next;
	Mesh* prev;

    enum TYPE
    {
        MESH_TYPE_2D,
        MESH_TYPE_3D
    };

    TYPE type;

	int faces;
	Face* head_face;
	Face* tail_face;

	int lines;
	Line* head_line;
	Line* tail_line;

	int verts;
	Vert* head_vert;
	Vert* tail_vert;

    // untransformed bbox
    float bbox[6];

    MeshInst* share_list;

    bool Update(const char* path);
};

struct BSP
{
    enum TYPE
    {
        BSP_TYPE_NODE,
        BSP_TYPE_NODE_SHARE,
        BSP_TYPE_LEAF,
        BSP_TYPE_INST // mesh or sprite inst
    };

    TYPE type;    
    float bbox[6]; // in world coords
    BSP* bsp_parent; // BSP_Node or BSP_NodeShare or NULL if not attached to tree

	bool InsertInst(World* w, Inst* i);
};

struct BSP_Node : BSP
{
    BSP* bsp_child[2];
};

struct BSP_NodeShare : BSP_Node
{
    // list of shared instances 
    Inst* head; 
    Inst* tail;
};

struct BSP_Leaf : BSP
{
    // list of instances 
   Inst* head; 
   Inst* tail;
};

struct Inst : BSP
{
	enum INST_TYPE
	{
		MESH = 1,
		SPRITE = 2,
		ITEM = 3
	};

	INST_TYPE inst_type;
	char* name;
	int story_id;

    // in BSP_Leaf::inst / BSP_NodeShare::inst
	Inst* next;
	Inst* prev;    

    int /*FLAGS*/ flags; 
};

struct MeshInst : Inst
{
	Mesh* mesh;
	double tm[16]; // absoulte! mesh->world

	MeshInst* share_next; // next instance sharing same mesh

	void UpdateBox()
	{
		float w[4];
		Vert* v = mesh->head_vert;

		if (!v && WorldDebugEnabled())
		{
			fprintf(stderr, "[World] UpdateBox: mesh has no verts name=%s\n", name ? name : "(null)");
		}

		if (v)
		{
			Product(tm, v->xyzw, w);
			bbox[0] = w[0];
			bbox[1] = w[0];
			bbox[2] = w[1];
			bbox[3] = w[1];
			bbox[4] = w[2];
			bbox[5] = w[2];
			v = v->next;
		}

		while (v)
		{
			Product(tm, v->xyzw, w);
			bbox[0] = fminf(bbox[0], w[0]);
			bbox[1] = fmaxf(bbox[1], w[0]);
			bbox[2] = fminf(bbox[2], w[1]);
			bbox[3] = fmaxf(bbox[3], w[1]);
			bbox[4] = fminf(bbox[4], w[2]);
			bbox[5] = fmaxf(bbox[5], w[2]);
			v = v->next;
		}

		if (WorldDebugEnabled())
		{
			if (bbox[0] > bbox[1] || bbox[2] > bbox[3] || bbox[4] > bbox[5])
			{
				fprintf(stderr, "[World] UpdateBox: invalid bbox name=%s bbox=[%.2f %.2f %.2f %.2f %.2f %.2f]\n",
					name ? name : "(null)", bbox[0], bbox[1], bbox[2], bbox[3], bbox[4], bbox[5]);
			}
		}
	}
	bool HitFace(double ray[10], double ret[3], double nrm[3], bool positive_only, bool solid_only, uint8_t* out_color = 0)
	{
		if (!mesh)
			return false;

		bool flag = false;

		Face* f = flags & INST_FLAGS::INST_VISIBLE ? mesh->head_face : 0;
		while (f)
		{
			if (solid_only)
			{
				// [DATA-CONTRACT:AKM] alpha >= 128 is passthrough; solid_only must
				// therefore SKIP those faces and keep the <128 collision surfaces.
				if ((f->abc[0]->rgba[3] | f->abc[1]->rgba[3] | f->abc[2]->rgba[3]) & 0x80)
				{
					f = f->next;
					continue;
				}
			}

			double v0[4], v1[4], v2[4];
			Product(tm, f->abc[0]->xyzw, v0);
			Product(tm, f->abc[1]->xyzw, v1);
			Product(tm, f->abc[2]->xyzw, v2);

			double u, v;
			if (RayIntersectsTriangle(ray, v0, v1, v2, ret, positive_only, &u, &v))
			{
				if (nrm)
				{
					double d1[3] = { v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2] };
					double d2[3] = { v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2] };
					CrossProduct(d1, d2, nrm);
				}

				if (out_color)
				{
					double w = 1.0 - u - v;
					out_color[0] = (uint8_t)(f->abc[0]->rgba[0] * w + f->abc[1]->rgba[0] * u + f->abc[2]->rgba[0] * v);
					out_color[1] = (uint8_t)(f->abc[0]->rgba[1] * w + f->abc[1]->rgba[1] * u + f->abc[2]->rgba[1] * v);
					out_color[2] = (uint8_t)(f->abc[0]->rgba[2] * w + f->abc[1]->rgba[2] * u + f->abc[2]->rgba[2] * v);
				}

				flag = true;
			}

			f = f->next;
		}

		return flag;
	}
};

inline bool HitSprite(Sprite* sprite, int anim, int frame, float pos[3], float yaw, double ray[10], double ret[3], bool positive_only)
{
	// maybe just plane equation first (must pass through pos and its normal is {-ray_x,-ray_y,0}
	double plane[4] = { -ray[3],-ray[4],0,0 };
	double dpos[3] = { pos[0],pos[1],pos[2] };
	plane[3] = -DotProduct(plane, dpos);

	// so we can calc plane ray intersection
	double d = ray[3] * plane[0] + ray[4] * plane[1] + ray[5] * plane[2];
	if (fabs(d) < 0.001)
		return false;
	double n = ray[6] * plane[0] + ray[7] * plane[1] + ray[8] * plane[2] + plane[3];
	double q = -n / d;

	if (positive_only && q < 0)
		return false;
	if (q > ray[9])
		return false;

	double p[3] =
	{
		ray[6] + q * ray[3],
		ray[7] + q * ray[4],
		ray[8] + q * ray[5]
	};

	// get frame from sprite

	double rot_yaw = atan2(ray[4], ray[3]) * 180 / M_PI - 90;
	Sprite* s = sprite;
	if (!s || s->anims <= 0 || s->angles <= 0)
		return false;
	if (anim < 0 || anim >= s->anims)
		anim = 0;
	int len = s->anim[anim].length;
	if (len <= 0)
		return false;
	if (frame < 0)
		frame = 0;
	else
		frame %= len;
	float angle = yaw;
	int ang = (int)floor((angle - rot_yaw) * s->angles / 360.0f + 0.5f);
	ang = ang >= 0 ? ang % s->angles : (ang % s->angles + s->angles) % s->angles;

	int i = frame + ang * len;
	//if (proj && s->projs > 1)
	//	i += s->anim[anim].length * s->angles;
	Sprite::Frame* f = s->atlas + s->anim[anim].frame_idx[i];

	// transform intersection to cell coords
	float zoom = 2.0f / 3.0f;
	float cos30 = (float)cos(30 * M_PI / 180);
	float dwx = (float)(zoom * f->width * 0.5f * cos(rot_yaw*M_PI / 180));
	float dwy = (float)(zoom * f->width * 0.5f * sin(rot_yaw*M_PI / 180));
	float dlz = zoom * -f->ref[1] * 0.5f / cos30 * HEIGHT_SCALE;
	float dhz = zoom * (f->height - f->ref[1] * 0.5f) / cos30 * HEIGHT_SCALE;

	float ds = 2.0 * (/*zoom*/ 1.0 * /*scale*/ 3.0) / VISUAL_CELLS * 0.5 /*we're not dbl_wh*/;
	float dz_dy = HEIGHT_SCALE / (cos30 * HEIGHT_CELLS * ds);

	double left = (pos[0] - dwx)*dwx + (pos[1] - dwy)*dwy;
	double right = (pos[0] + dwx)*dwx + (pos[1] + dwy)*dwy;
	double bottom = (pos[2] + dlz);
	double top = (pos[2] + dhz);

	double dot_xy = p[0] * dwx + p[1] * dwy;
	double dot_z = p[2];

	int x = (int)floor((dot_xy - left) / (right - left) * f->width);
	int y = (int)floor((dot_z - bottom) / (top - bottom) * f->height);

	if (x >= 0 && x < f->width && y >= 0 && y < f->height)
	{
		// inside rect
		AnsiCell* ac = f->cell + x + y * f->width;
		if (ac->bk != 255 && ac->gl != 219 || ac->fg != 255 && ac->gl != 0 && ac->gl != 32)
		{
			// not transparent

			// todo later
			// we could do raster check using current font if either fg or bk is transparant
			// ...

			float h = (float)(HEIGHT_SCALE / 4 + pos[2] + (2.0*ac->spare + f->ref[2]) * 0.5 * dz_dy);
			if (ret[2] < h)
			{
				// printf("%d,%d\n", x, y);
				ret[0] = p[0];
				ret[1] = p[1];
				ret[2] = h;

				ray[9] = q;
				return true;
			}
		}
	}

	return false;
}

struct SpriteInst : Inst
{
	World* w;
	Sprite* sprite;
	void* data; // player(human) or creature or null
	int anim;
	int frame;
	int reps[4];
	float yaw;
	float pos[3];

	bool Hit(double ray[10], double ret[3], bool positive_only)
	{
		if (flags & INST_FLAGS::INST_VISIBLE)
			return HitSprite(sprite, anim, frame, pos, yaw, ray, ret, positive_only);
		return false;
	}
};

#ifdef EDITOR
inline bool HitSprite(Sprite* sprite, int anim, int frame, float pos[3], float yaw, double p[3], double v[3], double ret[3], bool positive_only)
{
	double ray[10] = 
	{
		p[1] * v[2] - p[2] * v[1],
		p[2] * v[0] - p[0] * v[2],
		p[0] * v[1] - p[1] * v[0],
		v[0], v[1], v[2],
		p[0], p[1], p[2],
		FLT_MAX
	};

	return HitSprite(sprite, anim, frame, pos, yaw, ray, ret, positive_only);
}
#endif

struct ItemInst : Inst
{
	World* w;
	Item* item;
	float yaw;
	float pos[3];

	bool Hit(double ray[10], double ret[3], bool positive_only)
	{
		if (!(flags & INST_FLAGS::INST_VISIBLE))
			return false;

		const double* d = ray + 3;
		const double* p = ray + 6;
		double tmin = positive_only ? 0.0 : -DBL_MAX;
		double tmax = DBL_MAX;
		for (int axis = 0; axis < 3; axis++)
		{
			const double minv = bbox[axis * 2 + 0];
			const double maxv = bbox[axis * 2 + 1];
			if (fabs(d[axis]) < 1e-9)
			{
				if (p[axis] < minv || p[axis] > maxv)
					return false;
				continue;
			}
			double a = (minv - p[axis]) / d[axis];
			double b = (maxv - p[axis]) / d[axis];
			if (a > b)
			{
				double tmp = a;
				a = b;
				b = tmp;
			}
			if (a > tmin)
				tmin = a;
			if (b < tmax)
				tmax = b;
			if (tmin > tmax)
				return false;
		}
		if (ret)
		{
			ret[0] = p[0] + d[0] * tmin;
			ret[1] = p[1] + d[1] * tmin;
			ret[2] = p[2] + d[2] * tmin;
		}
		ray[9] = tmin;
		return true;
	}

};

extern ItemInst* item_inst_cache;
static Item** delete_item_queue = 0;
static int delete_item_queue_count = 0;
static int delete_item_queue_cap = 0;


static void ResetDeleteItemQueue()
{
	delete_item_queue_count = 0;
}

static void QueueDeleteItem(Item* item)
{
	if (!item)
		return;
	for (int i = 0; i < delete_item_queue_count; i++)
	{
		if (delete_item_queue[i] == item)
			return;
	}
	if (delete_item_queue_count >= delete_item_queue_cap)
	{
		int next_cap = delete_item_queue_cap ? delete_item_queue_cap * 2 : 64;
		Item** next = (Item**)realloc(delete_item_queue, sizeof(Item*) * next_cap);
		if (!next)
			abort();
		delete_item_queue = next;
		delete_item_queue_cap = next_cap;
	}
	delete_item_queue[delete_item_queue_count++] = item;
}

static void FlushDeleteItemQueue()
{
	for (int i = 0; i < delete_item_queue_count; i++)
		DestroyItem(delete_item_queue[i]);
	delete_item_queue_count = 0;
}

inline ItemInst* AllocItemInst()
{
	if (!item_inst_cache)
		return (ItemInst*)malloc(sizeof(ItemInst));
	ItemInst* ii = item_inst_cache;
	item_inst_cache = (ItemInst*)ii->next;
	return ii;
}

inline void FreeItemInst(ItemInst* ii)
{
	ii->next = item_inst_cache;
	item_inst_cache = ii;
}

void PurgeItemInstCache();

// Defined in world_core.cpp; shared across all world_*.cpp TUs.
extern int bsp_tests;
extern int bsp_insts;
extern int bsp_nodes;


// ── Helpers needed by split files (defined in world.cpp) ──

extern SpriteInst* delete_sprite_list;
void DeleteItemInsts(BSP* bsp, bool all);
void DeleteSpriteInsts(BSP* bsp);

// ═══════════════════════════════════════════════════════════
// struct World (with inline method bodies)
// ═══════════════════════════════════════════════════════════

struct World
{
    int meshes;
    Mesh* head_mesh;
    Mesh* tail_mesh;

    Mesh* LoadMesh(const char* path, const char* name);

    // Find existing mesh by name or load from file. Deduplicates meshes
    // referenced multiple times in .a3d files.
    Mesh* FindOrLoadMesh(const char* path, const char* name)
    {
        const char* key = name ? name : path;
        for (Mesh* m = head_mesh; m; m = m->next)
        {
            if (m->name && strcmp(m->name, key) == 0)
                return m;
        }
        return LoadMesh(path, name);
    }

    Mesh* AddMesh(const char* name = 0, void* cookie = 0)
    {
        Mesh* m = (Mesh*)malloc(sizeof(Mesh));

        m->world = this;
        m->type = Mesh::MESH_TYPE_3D;
        m->name = name ? strdup(name) : 0;
        m->cookie = cookie;

        m->next = 0;
        m->prev = tail_mesh;
        if (tail_mesh)
            tail_mesh->next = m;
        else
            head_mesh = m;
        tail_mesh = m;    

        m->share_list = 0;
 
        m->verts = 0;
        m->head_vert = 0;
        m->tail_vert = 0;
        
        m->faces = 0;
        m->head_face = 0;
        m->tail_face = 0;

        m->lines = 0;
        m->head_line = 0;
        m->tail_line = 0;

        memset(m->bbox,0,sizeof(float[6]));

        meshes++;

        return m;
    }

    bool DelMesh(Mesh* m)
    {
        if (!m || m->world != this)
            return false;

        // kill sharing insts
        Inst* i = m->share_list;
        while (m->share_list)
            DelInst(m->share_list);

        Face* f = m->head_face;
        while (f)
        {
            Face* n = f->next;
            free(f);
            f=n;
        }

        Line* l = m->head_line;
        while (l)
        {
            Line* n = l->next;
            free(l);
            l=n;
        }        

        Vert* v = m->head_vert;
        while (v)
        {
            Vert* n = v->next;
            free(v);
            v=n;
        }

        if (m->name)
            free(m->name);

        if (m->prev)
            m->prev->next = m->next;
        else
            head_mesh = m->next;

        if (m->next)
            m->next->prev = m->prev;
        else
            tail_mesh = m->prev;

        free(m);
        meshes--;

        return true;
    }

    int insts; // all (meshes, sprites, edit items, world items)
	int temp_insts; // only world items

    // only non-bsp
    Inst* head_inst; 
    Inst* tail_inst;

	Inst* AddInst(Item* item, int flags, float pos[3], float yaw, int story_id)
	{
		if (!item)
			return 0;

		ItemInst* i = AllocItemInst();
		memset(i, 0, sizeof(ItemInst));
		i->story_id = story_id;
		i->inst_type = Inst::INST_TYPE::ITEM;
		i->w = this;
		i->item = item;
		i->yaw = yaw;
		i->pos[0] = pos ? pos[0] : 0.0f;
		i->pos[1] = pos ? pos[1] : 0.0f;
		i->pos[2] = pos ? pos[2] : 0.0f;

		const float half = 2.5f;
		i->bbox[0] = i->pos[0] - half;
		i->bbox[1] = i->pos[0] + half;
		i->bbox[2] = i->pos[1] - half;
		i->bbox[3] = i->pos[1] + half;
		i->bbox[4] = i->pos[2];
		i->bbox[5] = i->pos[2] + half;

		i->type = BSP::BSP_TYPE_INST;
		i->flags = flags;
		i->bsp_parent = 0;
		i->next = 0;
		i->prev = tail_inst;
		if (tail_inst)
			tail_inst->next = i;
		else
			head_inst = i;
		tail_inst = i;

		if (flags & INST_FLAGS::INST_VOLATILE)
			temp_insts++;

		insts++;
		item->inst = i;
		return i;
	}

	Inst* AddInst(Sprite* s, int flags, float pos[3], float yaw, int anim, int frame, int reps[4], const char* name, int story_id)
	{
		SpriteInst* i = (SpriteInst*)malloc(sizeof(SpriteInst));
		i->story_id = story_id;
		i->inst_type = Inst::INST_TYPE::SPRITE;
		i->w = this;
		i->sprite = s;
		i->data = 0;

		i->bbox[0] = s->proj_bbox[0] + pos[0];
		i->bbox[1] = s->proj_bbox[1] + pos[0];
		i->bbox[2] = s->proj_bbox[2] + pos[1];
		i->bbox[3] = s->proj_bbox[3] + pos[1];
		i->bbox[4] = s->proj_bbox[4] + pos[2];
		i->bbox[5] = s->proj_bbox[5] + pos[2];

		i->pos[0] = pos[0];
		i->pos[1] = pos[1];
		i->pos[2] = pos[2];

		i->yaw = yaw;
		i->anim = anim;
		i->frame = frame;
		i->reps[0] = reps[0];
		i->reps[1] = reps[1];
		i->reps[2] = reps[2];
		i->reps[3] = reps[3];

		i->name = name ? strdup(name) : 0;

		i->type = BSP::BSP_TYPE_INST;
		i->flags = flags;
		i->bsp_parent = 0;

		i->next = 0;
		i->prev = tail_inst;
		if (tail_inst)
			tail_inst->next = i;
		else
			head_inst = i;
		tail_inst = i;

        if (flags & INST_FLAGS::INST_VOLATILE)
            temp_insts++;
        
        insts++;

		if (WorldDebugEnabled())
		{
			fprintf(stderr,
				"[World] AddSpriteInst name=%s flags=0x%x pos=(%.2f %.2f %.2f) bbox=[%.2f %.2f %.2f %.2f %.2f %.2f]\n",
				name ? name : "(null)",
				flags,
				i->pos[0], i->pos[1], i->pos[2],
				i->bbox[0], i->bbox[1], i->bbox[2], i->bbox[3], i->bbox[4], i->bbox[5]);
			if (!(flags & INST_USE_TREE))
				fprintf(stderr, "[World] AddSpriteInst missing INST_USE_TREE name=%s\n", name ? name : "(null)");
		}
        return i;
    }

    Inst* AddInst(Mesh* m, int flags, const double tm[16], const char* name, int story_id)
    {
        if (!m || m->world != this)
            return 0;

		MeshInst* i = (MeshInst*)malloc(sizeof(MeshInst));

		i->story_id = story_id;
		i->inst_type = Inst::INST_TYPE::MESH;

		if (tm)
		{
			memcpy(i->tm, tm, sizeof(double[16]));
		}
		else
		{
			memset(i->tm, 0, sizeof(double[16]));
			i->tm[0] = i->tm[5] = i->tm[10] = i->tm[15] = 1.0;
			i->bbox[0] = m->bbox[0];
			i->bbox[1] = m->bbox[1];
			i->bbox[2] = m->bbox[2];
			i->bbox[3] = m->bbox[3];
			i->bbox[4] = m->bbox[4];
			i->bbox[5] = m->bbox[5];
		}

        i->name = name ? strdup(name) : 0;

        i->mesh = m;

		i->UpdateBox();

        i->type = BSP::BSP_TYPE_INST;
        i->flags = flags;
        i->bsp_parent = 0;

        if (m)
        {
            i->share_next = m->share_list;
            m->share_list = i;
        }
        else
            i->share_next = 0;

        i->next = 0;
        i->prev = tail_inst;
        if (tail_inst)
            tail_inst->next = i;
        else
            head_inst = i;
        tail_inst = i;  

		if (flags & INST_FLAGS::INST_VOLATILE)
			temp_insts++;
        
        insts++;
        return i;
    }

	bool DelInst(Inst* i)
	{
		if (!i)
			return false;
		if (i->inst_type == Inst::INST_TYPE::MESH)
			return DelInst((MeshInst*)i);
		if (i->inst_type == Inst::INST_TYPE::SPRITE)
			return DelInst((SpriteInst*)i);
		if (i->inst_type == Inst::INST_TYPE::ITEM)
			return DelInst((ItemInst*)i);
		return false;
	}

	bool DelInst(MeshInst* i)
	{
        if (!i || !i->mesh || i->mesh->world != this)
            return false;

        if (i->mesh)
        {
            MeshInst** s = &i->mesh->share_list;
            while (*s != i)
                s = &(*s)->share_next;
            *s = (*s)->share_next;
        }

        if (!i->bsp_parent)
        {
            if (i->prev)
                i->prev->next = i->next;
            else
                head_inst = i->next;

            if (i->next)
                i->next->prev = i->prev;
            else
                tail_inst = i->prev;
        }
        else
        {
            if (i->bsp_parent->type == BSP::BSP_TYPE_LEAF)
            {
                BSP_Leaf* leaf = (BSP_Leaf*)i->bsp_parent;

                if (i->prev)
                    i->prev->next = i->next;
                else
                    leaf->head = i->next;

                if (i->next)
                    i->next->prev = i->prev;
                else
                    leaf->tail = i->prev;

                if (leaf->head == 0)
                {
                    // do ancestors cleanup
                    // ...
                }
            }
            else
            if (i->bsp_parent->type == BSP::BSP_TYPE_NODE_SHARE)
            {
                BSP_NodeShare* share = (BSP_NodeShare*)i->bsp_parent;

                if (share->bsp_child[0] == i)
                    share->bsp_child[0] = 0;
                else
                if (share->bsp_child[1] == i)
                    share->bsp_child[1] = 0;
                else
                {
                    if (i->prev)
                        i->prev->next = i->next;
                    else
                        share->head = i->next;

                    if (i->next)
                        i->next->prev = i->prev;
                    else
                        share->tail = i->prev;
                }

                if (share->head == 0 && share->bsp_child[0]==0 && share->bsp_child[1]==0)
                {
                    // do ancestors cleanup
                    // ...
                }
            }
            else
            if (i->bsp_parent->type == BSP::BSP_TYPE_NODE)
            {
                BSP_Node* node = (BSP_Node*)i->bsp_parent;
                if (node->bsp_child[0] == i)
                    node->bsp_child[0] = 0;
                else
                if (node->bsp_child[1] == i)
                    node->bsp_child[1] = 0;

                if (node->bsp_child[0]==0 && node->bsp_child[1]==0)
                {
                    // do ancestors cleanup
                    // ...
                }
            }
            else
            {
                assert(0);
            }
        }

        if (i->name)
            free(i->name);

        if (editable == i)
            editable = 0;

        if (root == i)
            root = 0;
           
		if (i->flags & INST_FLAGS::INST_VOLATILE)
			temp_insts--;

        insts--;
        free(i);

        return true;
    }

	// [FLOW:WORLD] Instance deletion -- remove sprite instance from BSP tree or flat list
	bool DelInst(SpriteInst* i)
	{
		if (!i)
			return false;

		if (!i->bsp_parent)
		{
			if (i->prev)
				i->prev->next = i->next;
			else
				head_inst = i->next;

			if (i->next)
				i->next->prev = i->prev;
			else
				tail_inst = i->prev;
		}
		else
		{
			if (i->bsp_parent->type == BSP::BSP_TYPE_LEAF)
			{
				BSP_Leaf* leaf = (BSP_Leaf*)i->bsp_parent;

				if (i->prev)
					i->prev->next = i->next;
				else
					leaf->head = i->next;

				if (i->next)
					i->next->prev = i->prev;
				else
					leaf->tail = i->prev;

				if (leaf->head == 0)
				{
					// do ancestors cleanup
					// ...
				}
			}
			else
				if (i->bsp_parent->type == BSP::BSP_TYPE_NODE_SHARE)
				{
					BSP_NodeShare* share = (BSP_NodeShare*)i->bsp_parent;

					if (share->bsp_child[0] == i)
						share->bsp_child[0] = 0;
					else
						if (share->bsp_child[1] == i)
							share->bsp_child[1] = 0;
						else
						{
							if (i->prev)
								i->prev->next = i->next;
							else
								share->head = i->next;

							if (i->next)
								i->next->prev = i->prev;
							else
								share->tail = i->prev;
						}

					if (share->head == 0 && share->bsp_child[0] == 0 && share->bsp_child[1] == 0)
					{
						// do ancestors cleanup
						// ...
					}
				}
				else
					if (i->bsp_parent->type == BSP::BSP_TYPE_NODE)
					{
						BSP_Node* node = (BSP_Node*)i->bsp_parent;
						if (node->bsp_child[0] == i)
							node->bsp_child[0] = 0;
						else
							if (node->bsp_child[1] == i)
								node->bsp_child[1] = 0;

						if (node->bsp_child[0] == 0 && node->bsp_child[1] == 0)
						{
							// do ancestors cleanup
							// ...
						}
					}
					else
					{
						assert(0);
					}
		}

		if (i->name)
			free(i->name);

		if (editable == i)
			editable = 0;

		if (root == i)
			root = 0;

		if (i->flags & INST_FLAGS::INST_VOLATILE)
			temp_insts--;

		insts--;
		free(i);

		return true;
	}


	bool DelInst(ItemInst* i)
	{
		if (!i)
			return false;

		if (!i->bsp_parent)
		{
			if (i->prev)
				i->prev->next = i->next;
			else
				head_inst = i->next;

			if (i->next)
				i->next->prev = i->prev;
			else
				tail_inst = i->prev;
		}
		else
		{
			if (i->bsp_parent->type == BSP::BSP_TYPE_LEAF)
			{
				BSP_Leaf* leaf = (BSP_Leaf*)i->bsp_parent;

				if (i->prev)
					i->prev->next = i->next;
				else
					leaf->head = i->next;

				if (i->next)
					i->next->prev = i->prev;
				else
					leaf->tail = i->prev;

				// WHY ancestor cleanup needed: When leaf becomes empty, walking up the
				// tree to collapse empty parent nodes prevents memory waste and improves
				// query performance (fewer empty nodes to traverse).
				// TODO(WORLD-CLEANUP): Ancestor cleanup after BSP deletion is STUBBED.
				// Empty leaves accumulate, degrading query performance over time. Should
				// walk up tree: if parent has 2 empty children, delete parent and recurse.
				if (leaf->head == 0)
				{
					// do ancestors cleanup
					// ...
				}
			}
			else
			if (i->bsp_parent->type == BSP::BSP_TYPE_NODE_SHARE)
			{
				BSP_NodeShare* share = (BSP_NodeShare*)i->bsp_parent;

				if (share->bsp_child[0] == i)
					share->bsp_child[0] = 0;
				else
				if (share->bsp_child[1] == i)
					share->bsp_child[1] = 0;
				else
				{
					if (i->prev)
						i->prev->next = i->next;
					else
						share->head = i->next;

					if (i->next)
						i->next->prev = i->prev;
					else
						share->tail = i->prev;
				}

				// TODO(WORLD-CLEANUP): Ancestor cleanup stub (see BSP_TYPE_LEAF case above)
				if (share->head == 0 && share->bsp_child[0] == 0 && share->bsp_child[1] == 0)
				{
					// do ancestors cleanup
					// ...
				}
			}
			else
			if (i->bsp_parent->type == BSP::BSP_TYPE_NODE)
			{
				BSP_Node* node = (BSP_Node*)i->bsp_parent;
				if (node->bsp_child[0] == i)
					node->bsp_child[0] = 0;
				else
				if (node->bsp_child[1] == i)
					node->bsp_child[1] = 0;

				// TODO(WORLD-CLEANUP): Ancestor cleanup stub (see BSP_TYPE_LEAF case above)
				if (node->bsp_child[0] == 0 && node->bsp_child[1] == 0)
				{
					// do ancestors cleanup
					// ...
				}
			}
			else
			{
				assert(0);
			}
		}

		if (i->name)
			free(i->name);

		if (editable == i)
			editable = 0;

		if (root == i)
			root = 0;


		//if (i->item->purpose == Item::WORLD)
		if (i->flags & INST_FLAGS::INST_VOLATILE)
			temp_insts--;

		insts--;

		// item insts are frequently allocated and freed
		// cache em!

		FreeItemInst(i);

		return true;
	}

    // currently selected instance (its mesh) for editting
    // overrides visibility?
    Inst* editable;

    // now we want to form a tree of Insts
    BSP* root;

	bool has_player_start;
	float player_start_pos[3];
	float player_start_yaw;
	float player_start_dir;

    void DeleteBSP(BSP* bsp)
    {
        if (bsp->type == BSP::BSP_TYPE_NODE)
        {
            BSP_Node* node = (BSP_Node*)bsp;

            if (node->bsp_child[0])
                DeleteBSP(node->bsp_child[0]);
            if (node->bsp_child[1])
                DeleteBSP(node->bsp_child[1]);
            free (bsp);
        }
        else
        if (bsp->type == BSP::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* share = (BSP_NodeShare*)bsp;

            if (share->bsp_child[0])
                DeleteBSP(share->bsp_child[0]);
            if (share->bsp_child[1])
                DeleteBSP(share->bsp_child[1]);

            if (share->head)
            {
                Inst* i = share->head;
                while (i)
                {
                    i->bsp_parent = 0;
                    i=i->next;
                }

                if (tail_inst)
                {
                    share->head->prev = tail_inst;
                    tail_inst->next = share->head;
                    tail_inst = share->tail;
                }
                else
                {
                    head_inst = share->head; 
                    tail_inst = share->tail;
                }
            }
            free (bsp);
        }        
        else
        if (bsp->type == BSP::BSP_TYPE_LEAF)
        {
            BSP_Leaf* leaf = (BSP_Leaf*)bsp;
            if (leaf->head)
            {
                Inst* i = leaf->head;
                while (i)
                {
                    i->bsp_parent = 0;
                    i=i->next;
                }

                if (tail_inst)
                {
                    leaf->head->prev = tail_inst;
                    tail_inst->next = leaf->head;
                    tail_inst = leaf->tail;
                }
                else
                {
                    head_inst = leaf->head; 
                    tail_inst = leaf->tail;
                }
            }
            free (bsp);
        }        
        else
        if (bsp->type == BSP::BSP_TYPE_INST)
        {
            Inst* inst = (Inst*)bsp;

            bsp->bsp_parent = 0;
            inst->next=0;
            inst->prev = tail_inst;
            if (tail_inst)
                tail_inst->next = inst;
            else
                head_inst = inst;
            tail_inst=inst;
        }
        else
        {
            assert(0);
        }
        
    }

    struct BSP_Item
    {
        Inst* inst;
        float area;
    };

    // WHY SplitBSP: Recursively builds axis-aligned BSP tree from instance array
    // for fast spatial queries (frustum culling, raycasting). Selects best split
    // axis (X/Y/Z) by testing variance along each dimension, sorts instances by
    // centroid, and recursively subdivides into balanced subtrees.
    // [FLOW:WORLD] BSP tree construction -- recursive top-down partitioning
    BSP* SplitBSP(BSP_Item* arr, int num)
    {
        assert(num>0);

        // WHY base case num==1: Single instance promoted directly to BSP_TYPE_INST
        // node to avoid unnecessary leaf wrapper overhead. This instance IS the BSP
        // node, tested directly during queries.
        if (num == 1)
        {
            Inst* inst = arr[0].inst;
            inst->bsp_parent = 0;
            inst->prev = 0;
            inst->next = 0;
            return inst;
        }

        struct CentroidSorter
        {
            static int sortX(const void* a, const void* b)
            {
                Inst* ia = ((BSP_Item*)a)->inst;
                Inst* ib = ((BSP_Item*)b)->inst;

                float diff = (ia->bbox[0]+ia->bbox[1])-(ib->bbox[0]+ib->bbox[1]);
                if (diff<0)
                    return -1;
                if (diff>0)
                    return +1;
                return 0;
            }
            static int sortY(const void* a, const void* b)
            {
                Inst* ia = ((BSP_Item*)a)->inst;
                Inst* ib = ((BSP_Item*)b)->inst;

                float diff = (ia->bbox[2]+ia->bbox[3])-(ib->bbox[2]+ib->bbox[3]);
                if (diff<0)
                    return -1;
                if (diff>0)
                    return +1;
                return 0;
            }
            static int sortZ(const void* a, const void* b)
            {
                Inst* ia = ((BSP_Item*)a)->inst;
                Inst* ib = ((BSP_Item*)b)->inst;

                float diff = (ia->bbox[4]+ia->bbox[5])-(ib->bbox[4]+ib->bbox[5]);
                if (diff<0)
                    return -1;
                if (diff>0)
                    return +1;
                return 0;
            }
        };

        static int (*sort[3])(const void* a, const void* b) = 
        {
            CentroidSorter::sortX,
            CentroidSorter::sortY,
            CentroidSorter::sortZ
        };

        float best_cost = -1;
        int best_axis = -1;
        int best_item = -1;

        // WHY test all 3 axes: Find best split by comparing surface area heuristic
        // (SAH) cost for each axis. Lower cost = more balanced spatial partitioning,
        // leading to faster culling during queries. Algorithm computes cumulative
        // bbox surface areas for left/right partitions at each split position.
        // actualy it could be better to splin only in x and y (axis<2)
        for (int axis=0; axis<3; axis++)
        {
            qsort(arr, num, sizeof(BSP_Item), sort[axis]);

            float lo_bbox[6] =
            {
                arr[0].inst->bbox[0],
                arr[0].inst->bbox[1],
                arr[0].inst->bbox[2],
                arr[0].inst->bbox[3],
                arr[0].inst->bbox[4],
                arr[0].inst->bbox[5]
            };

            for (int i=0; i<num; i++)
            {
                lo_bbox[0] = fminf( lo_bbox[0], arr[i].inst->bbox[0]);
                lo_bbox[1] = fmaxf( lo_bbox[1], arr[i].inst->bbox[1]);
                lo_bbox[2] = fminf( lo_bbox[2], arr[i].inst->bbox[2]);
                lo_bbox[3] = fmaxf( lo_bbox[3], arr[i].inst->bbox[3]);
                lo_bbox[4] = fminf( lo_bbox[4], arr[i].inst->bbox[4]);
                lo_bbox[5] = fmaxf( lo_bbox[5], arr[i].inst->bbox[5]);

                arr[i].area = 
                    (lo_bbox[1]-lo_bbox[0]) * (lo_bbox[3]-lo_bbox[2]) * HEIGHT_SCALE +
                    (lo_bbox[3]-lo_bbox[2]) * (lo_bbox[5]-lo_bbox[4]) +
                    (lo_bbox[5]-lo_bbox[4]) * (lo_bbox[1]-lo_bbox[0]);                
            }

            float hi_bbox[6] =
            {
                arr[num-1].inst->bbox[0],
                arr[num-1].inst->bbox[1],
                arr[num-1].inst->bbox[2],
                arr[num-1].inst->bbox[3],
                arr[num-1].inst->bbox[4],
                arr[num-1].inst->bbox[5]
            };

            for (int i=num-1; i>0; i--)
            {
                hi_bbox[0] = fminf( hi_bbox[0], arr[i].inst->bbox[0]);
                hi_bbox[1] = fmaxf( hi_bbox[1], arr[i].inst->bbox[1]);
                hi_bbox[2] = fminf( hi_bbox[2], arr[i].inst->bbox[2]);
                hi_bbox[3] = fmaxf( hi_bbox[3], arr[i].inst->bbox[3]);
                hi_bbox[4] = fminf( hi_bbox[4], arr[i].inst->bbox[4]);
                hi_bbox[5] = fmaxf( hi_bbox[5], arr[i].inst->bbox[5]);

                float area = 
                    (hi_bbox[1]-hi_bbox[0]) * (hi_bbox[3]-hi_bbox[2]) * HEIGHT_SCALE +
                    (hi_bbox[3]-hi_bbox[2]) * (hi_bbox[5]-hi_bbox[4]) +
                    (hi_bbox[5]-hi_bbox[4]) * (hi_bbox[1]-hi_bbox[0]);

                // cost of {0..i-1}, {i..num-1}
                float cost = arr[i-1].area * i + area * (num-i);
                if (cost < best_cost || best_cost < 0)
                {
                    best_cost = cost;
                    best_item = i;
                    best_axis = axis;
                }
            }
        }

        if (best_axis != -1)
        {
            if (best_cost + arr[num-1].area * 2 > arr[num-1].area * num)
                best_axis = -1;
        }

        // WHY best_axis == -1: No good split found (cost too high or variance too low)
        // → create BSP_LEAF with linked list of all instances. During queries, this
        // leaf's entire instance list is tested sequentially (linear search).
        // [FLOW:WORLD] BSP leaf creation -- termination of recursive subdivision
        if (best_axis == -1)
        {
            // fill final list of instances

            BSP_Leaf* leaf = (BSP_Leaf*)malloc(sizeof(BSP_Leaf));
            leaf->bsp_parent = 0;
            leaf->type = BSP::BSP_TYPE_LEAF;

            leaf->bbox[0] = arr[0].inst->bbox[0];
            leaf->bbox[1] = arr[0].inst->bbox[1];
            leaf->bbox[2] = arr[0].inst->bbox[2];
            leaf->bbox[3] = arr[0].inst->bbox[3];
            leaf->bbox[4] = arr[0].inst->bbox[4];
            leaf->bbox[5] = arr[0].inst->bbox[5];
            
            leaf->head = arr[0].inst;
            leaf->tail = arr[num-1].inst;
            
            arr[0].inst->prev = 0;
            arr[num-1].inst->next = 0;

            arr[0].inst->bsp_parent = leaf;
            if (num>1)
            {
                arr[0].inst->next = arr[1].inst;
                arr[num-1].inst->prev = arr[num-2].inst;
                arr[num-1].inst->bsp_parent = leaf;

                leaf->bbox[0] = fminf( leaf->bbox[0], arr[num-1].inst->bbox[0]);
                leaf->bbox[1] = fmaxf( leaf->bbox[1], arr[num-1].inst->bbox[1]);
                leaf->bbox[2] = fminf( leaf->bbox[2], arr[num-1].inst->bbox[2]);
                leaf->bbox[3] = fmaxf( leaf->bbox[3], arr[num-1].inst->bbox[3]);
                leaf->bbox[4] = fminf( leaf->bbox[4], arr[num-1].inst->bbox[4]);
                leaf->bbox[5] = fmaxf( leaf->bbox[5], arr[num-1].inst->bbox[5]);                
            }

            for (int i=1; i<num-1; i++)
            {
                leaf->bbox[0] = fminf( leaf->bbox[0], arr[i].inst->bbox[0]);
                leaf->bbox[1] = fmaxf( leaf->bbox[1], arr[i].inst->bbox[1]);
                leaf->bbox[2] = fminf( leaf->bbox[2], arr[i].inst->bbox[2]);
                leaf->bbox[3] = fmaxf( leaf->bbox[3], arr[i].inst->bbox[3]);
                leaf->bbox[4] = fminf( leaf->bbox[4], arr[i].inst->bbox[4]);
                leaf->bbox[5] = fmaxf( leaf->bbox[5], arr[i].inst->bbox[5]);  
                                
                arr[i].inst->bsp_parent = leaf;
                arr[i].inst->prev = arr[i-1].inst;
                arr[i].inst->next = arr[i+1].inst;
            }

            assert(leaf->head);
            assert(leaf->tail);
            assert(num==1 && leaf->head == leaf->tail || num!=1 && leaf->head != leaf->tail);
            assert(leaf->head->prev == 0);
            assert(leaf->tail->next == 0);

            Inst* i = leaf->head;
            int c = 0;
            while (i)
            {
                c++;
                i=i->next;
                if (i)
                    assert(i->prev->next == i);
            }

            assert(c==num);

            i = leaf->tail;
            c = 0;
            while (i)
            {
                c++;
                i=i->prev;
                if (i)
                    assert(i->next->prev == i);
            }

            assert(c==num);

            return leaf;
        }
        // WHY else: Good split found → create BSP_NODE and recursively subdivide.
        // Left child gets instances [0..best_item), right gets [best_item..num).
        // Each child computes its bbox, then parent bbox is union of children.
        // [FLOW:WORLD] BSP interior node creation -- recursive subdivision
        else
        {
            int axis = best_axis;
            qsort(arr, num, sizeof(BSP_Item), sort[axis]);

            // BSP_Node* node = (BSP_Node*)malloc(sizeof(BSP_Node));
            // WHY allocate BSP_NodeShare size: Makes it easy to upgrade BSP_NODE to
            // BSP_NODE_SHARE later if instances are found straddling the split plane
            // (currently not implemented, but space is pre-allocated for future use).
			BSP_Node* node = (BSP_Node*)malloc(sizeof(BSP_NodeShare)); // make it easily changable!

            node->bsp_parent = 0;
            node->type = BSP::BSP_TYPE_NODE;

            // WHY recursive calls: Each child subtree independently partitions its
            // instance subset. This builds a balanced tree with O(log n) query depth.
            node->bsp_child[0] = SplitBSP(arr + 0, best_item);
            node->bsp_child[0]->bsp_parent = node;

            node->bsp_child[1] = SplitBSP(arr + best_item, num - best_item);
            node->bsp_child[1]->bsp_parent = node;

            node->bbox[0] = fminf( node->bsp_child[0]->bbox[0], node->bsp_child[1]->bbox[0]);
            node->bbox[1] = fmaxf( node->bsp_child[0]->bbox[1], node->bsp_child[1]->bbox[1]);
            node->bbox[2] = fminf( node->bsp_child[0]->bbox[2], node->bsp_child[1]->bbox[2]);
            node->bbox[3] = fmaxf( node->bsp_child[0]->bbox[3], node->bsp_child[1]->bbox[3]);
            node->bbox[4] = fminf( node->bsp_child[0]->bbox[4], node->bsp_child[1]->bbox[4]);
            node->bbox[5] = fmaxf( node->bsp_child[0]->bbox[5], node->bsp_child[1]->bbox[5]);               

            return node;
        }
    }

	void Rebuild(bool boxes)
	{
		if (root)
		{
			DeleteBSP(root);
			root = 0;
		}

        if (!insts)
            return;

		if (WorldDebugEnabled())
			fprintf(stderr, "[World] Rebuild begin insts=%d boxes=%d\n", insts, boxes ? 1 : 0);

        // MAY BE SLOW: 1/2 * num^3
        /*
        int num = 0;

        BSP** arr = (BSP**)malloc(sizeof(BSP*) * insts);
        
        for (Inst* inst = head_inst; inst; inst=inst->next)
        {
            if (inst->flags & INST_USE_TREE)
                arr[num++] = inst;
        }

        while (num>1)
        {
            int a = 0;
            int b = 1;
            float e = -1;

            for (int u=0; u<num-1; u++)
            {
                for (int v=u+1; v<num; v++)
                {
                    float bbox[6] =
                    {
                        fminf( arr[u]->bbox[0] , arr[v]->bbox[0] ),
                        fmaxf( arr[u]->bbox[1] , arr[v]->bbox[1] ),
                        fminf( arr[u]->bbox[2] , arr[v]->bbox[2] ),
                        fmaxf( arr[u]->bbox[3] , arr[v]->bbox[3] ),
                        fminf( arr[u]->bbox[4] , arr[v]->bbox[4] ),
                        fmaxf( arr[u]->bbox[5] , arr[v]->bbox[5] )
                    };

                    float vol = (bbox[1]-bbox[0]) * (bbox[3]-bbox[2]) * (bbox[5]-bbox[4]);
                    
                    float u_vol = (arr[u]->bbox[1]-arr[u]->bbox[0]) * (arr[u]->bbox[3]-arr[u]->bbox[2]) * (arr[u]->bbox[5]-arr[u]->bbox[4]);
                    float v_vol = (arr[v]->bbox[1]-arr[v]->bbox[0]) * (arr[v]->bbox[3]-arr[v]->bbox[2]) * (arr[v]->bbox[5]-arr[v]->bbox[4]);
                    
                    vol -= u_vol + v_vol; // minimize volumne expansion

                    // minimize volume difference between children
                    if (u_vol > v_vol)
                        vol += u_vol-v_vol;
                    else
                        vol += v_vol-u_vol;

                    if (vol < e || e<0)
                    {
                        a = u;
                        b = v;
                        e = vol;
                    }
                }
            }

            BSP_Node* node = (BSP_Node*)malloc(sizeof(BSP_Node));

            node->bsp_parent = 0;
            node->type = BSP::BSP_TYPE_NODE;

            node->bsp_child[0] = arr[a];
            node->bsp_child[1] = arr[b];

            node->bbox[0] = fminf( arr[a]->bbox[0] , arr[b]->bbox[0] );
            node->bbox[1] = fmaxf( arr[a]->bbox[1] , arr[b]->bbox[1] );
            node->bbox[2] = fminf( arr[a]->bbox[2] , arr[b]->bbox[2] );
            node->bbox[3] = fmaxf( arr[a]->bbox[3] , arr[b]->bbox[3] );
            node->bbox[4] = fminf( arr[a]->bbox[4] , arr[b]->bbox[4] );
            node->bbox[5] = fmaxf( arr[a]->bbox[5] , arr[b]->bbox[5] );

            num--;
            if (b!=num)
                arr[b] = arr[num];

            arr[a] = node;
        }
        root = arr[0];
        free(arr);
        */

        // LET'S TRY:
        // https://graphics.stanford.edu/~boulos/papers/togbvh.pdf
        // http://www.cs.uu.nl/docs/vakken/magr/2016-2017/slides/lecture%2003%20-%20the%20perfect%20BVH.pdf

        // KEY IDEAS
        // leaf nodes need ability of multiple objects encapsulation
        // object can be referenced by both leaf siblings -> use NodeShare in place of Node !!! 
        // ... need only to ensure that union of both leaf bboxes fully encapsulates that object

        BSP_Item* arr = (BSP_Item*)malloc(sizeof(BSP_Item) * insts);

        int count = 0;
		int skipped = 0;
        for (Inst* inst = head_inst; inst; )
        {
			// update inst bbox
			if (boxes)
			{
				if (inst->inst_type == Inst::INST_TYPE::MESH)
					((MeshInst*)inst)->UpdateBox();
			}

            Inst* next = inst->next;
            if (inst->flags & INST_USE_TREE)
            {
				if (count==insts)
				{
					int defect=0;
				}
                arr[count++].inst = inst;

                // extract!
                if (inst->prev)
                    inst->prev->next = inst->next;
                else
                    head_inst = inst->next;
                if (inst->next)
                    inst->next->prev = inst->prev;
                else
                    tail_inst = inst->prev;
            }
			else
			{
				if (WorldDebugEnabled() && inst->inst_type == Inst::INST_TYPE::MESH && skipped < 5)
				{
					fprintf(stderr, "[World] Rebuild skip (no INST_USE_TREE) name=%s flags=0x%x\n",
						inst->name ? inst->name : "(null)", inst->flags);
				}
				skipped++;
			}
            inst = next;
        }

		if (WorldDebugEnabled())
			fprintf(stderr, "[World] Rebuild collected=%d skipped=%d\n", count, skipped);

        if (count)
        {
            // split recursively!
            root = SplitBSP(arr, count);

            if (!root)
            {
                // if failed we need to put them back
                for (int i=0; i<count; i++)
                {
                    Inst* inst = arr[i].inst;
                    inst->bsp_parent = 0;
                    inst->prev = tail_inst;
                    inst->next = 0;
                    if (tail_inst)
                        tail_inst->next = inst;
                    else
                        head_inst = inst;
                    tail_inst = inst;                        
                }
            }

            free(arr);
        }
    }

	Inst* HitWorld(double p[3], double v[3], double ret[3], double nrm[3], bool positive_only, const HitFilter& filter, uint8_t* out_color = 0);

    static void QueryBSP(int level, BSP* bsp, int planes, double plane[][4], void (*cb)(int level, const float bbox[6], void* cookie), void* cookie)
    {
        // temporarily don't check planes
        cb(level, bsp->bbox,cookie);

        if (bsp->type == BSP::BSP_TYPE_NODE)
        {
            BSP_Node* n = (BSP_Node*)bsp;

            if (n->bsp_child[0])
                QueryBSP(level+1, n->bsp_child[0], planes, plane, cb, cookie);
            if (n->bsp_child[1])
                QueryBSP(level+1, n->bsp_child[1], planes, plane, cb, cookie);
        }
        else
        if (bsp->type == BSP::BSP_TYPE_NODE_SHARE)
        {
            BSP_NodeShare* s = (BSP_NodeShare*)bsp;

            if (s->bsp_child[0])
                QueryBSP(level+1, s->bsp_child[0], planes, plane, cb, cookie);
            if (s->bsp_child[1])
                QueryBSP(level+1, s->bsp_child[1], planes, plane, cb, cookie);

            Inst* i = s->head;
            while (i)
            {
                QueryBSP(level+1, i, planes, plane, cb, cookie);
                i=i->next;
            }
        }
        else
        if (bsp->type == BSP::BSP_TYPE_LEAF)
        {
            BSP_Leaf* l = (BSP_Leaf*)bsp;

            Inst* i = l->head;
            while (i)
            {
                QueryBSP(level+1, i, planes, plane, cb, cookie);
                i=i->next;
            }
        }
        else
        {
            assert(bsp->type == BSP::BSP_TYPE_INST);
        }
        
    }

    // MESHES IN HULL


    // MESHES IN HULL

    // recursive no clipping
    // FL-2957 attempt #24: early-exit via cb->should_continue
    static void Query(BSP* bsp, QueryWorldCB* cb, void* cookie)
    {
        // FL-2957 #24: abort traversal when collector signals completion
        if (cb->should_continue && !cb->should_continue(cookie))
            return;

        if (bsp->type == BSP::BSP_TYPE_LEAF)
        {
            bsp_nodes++;
            Inst* i = ((BSP_Leaf*)bsp)->head;
            while (i)
            {
                if (cb->should_continue && !cb->should_continue(cookie))
                    return;
                Query(i,cb,cookie);
                i=i->next;
            }
        }
        else
        if (bsp->type == BSP::BSP_TYPE_INST)
        {
            bsp_insts++;
            Inst* i = (Inst*)bsp;
			if (i->inst_type == Inst::INST_TYPE::MESH)
				cb->mesh_cb(i, ((MeshInst*)i)->mesh, ((MeshInst*)i)->tm, cookie);
			else
			if (i->inst_type == Inst::INST_TYPE::SPRITE)
			{
				SpriteInst* si = (SpriteInst*)i;
				if (i->flags & INST_FLAGS::INST_VISIBLE)
					cb->sprite_cb(si, si->sprite, si->pos, si->yaw, si->anim, si->frame, si->reps, cookie);
			}
			else
			if (i->inst_type == Inst::INST_TYPE::ITEM)
			{
				// Map-authored item instances are gameplay content for the
				// authoritative item loader, not mesh/sprite renderables.
				// QueryWorldItems owns their enumeration.
			}
        }
        else
        if (bsp->type == BSP::BSP_TYPE_NODE)
        {
            bsp_nodes++;
            BSP_Node* n = (BSP_Node*)bsp;
            if (n->bsp_child[0])
                Query(n->bsp_child[0],cb,cookie);
            if (n->bsp_child[1] && !(cb->should_continue && !cb->should_continue(cookie)))
                Query(n->bsp_child[1],cb,cookie);
        }
        else
        if (bsp->type == BSP::BSP_TYPE_NODE_SHARE)
        {
            bsp_nodes++;
            BSP_NodeShare* s = (BSP_NodeShare*)bsp;
            if (s->bsp_child[0])
                Query(s->bsp_child[0],cb,cookie);
            if (s->bsp_child[1] && !(cb->should_continue && !cb->should_continue(cookie)))
                Query(s->bsp_child[1],cb,cookie);
            Inst* i = s->head;
            while (i)
            {
                if (cb->should_continue && !cb->should_continue(cookie))
                    return;
                Query(i,cb,cookie);
                i=i->next;
            }
        }
        else
        {
            assert(0);
        }

    }

    // recursive
    // FL-2957 attempt #24: early-exit via cb->should_continue
    static void Query(BSP* bsp, int planes, double* plane[], QueryWorldCB* cb, void* cookie)
    {
        // FL-2957 #24: abort traversal before bbox test when collector signals done.
        // This is the primary fix — previously, the entire BSP frustum was traversed
        // even after the soup collector capped at 1024 items. In dense BSP regions
        // this wasted 149ms of pure traversal (passive-20260505-025840 ci=1).
        if (cb->should_continue && !cb->should_continue(cookie))
            return;

        if (bsp->type == BSP::BSP_TYPE_INST)
        {
            Inst* inst = (Inst*)bsp;
            // Volatile character sprites are updated every frame and can momentarily
            // drift out of sync with conservative bbox/frustum rejection. Keep them
            // queryable so multiplayer remotes do not vanish while labels still project.
            if (inst->inst_type == Inst::INST_TYPE::SPRITE &&
                (inst->flags & INST_FLAGS::INST_VOLATILE) &&
                ((SpriteInst*)inst)->data)
            {
                bsp_insts++;
                SpriteInst* si = (SpriteInst*)inst;
                if (inst->flags & INST_FLAGS::INST_VISIBLE)
                    cb->sprite_cb(si, si->sprite, si->pos, si->yaw, si->anim, si->frame, si->reps, cookie);
                return;
            }
        }

        float c[4] = { bsp->bbox[0], bsp->bbox[2], bsp->bbox[4], 1 }; // 0,0,0

        bsp_tests++;

        for (int i = 0; i < planes; i++)
        {
            int neg_pos[2] = { 0,0 };

            neg_pos[PositiveProduct(plane[i], c)] ++;

            c[0] = bsp->bbox[1]; // 1,0,0
            neg_pos[PositiveProduct(plane[i], c)] ++;

            c[1] = bsp->bbox[3]; // 1,1,0
            neg_pos[PositiveProduct(plane[i], c)] ++;

            c[0] = bsp->bbox[0]; // 0,1,0
            neg_pos[PositiveProduct(plane[i], c)] ++;

            c[2] = bsp->bbox[5]; // 0,1,1
            neg_pos[PositiveProduct(plane[i], c)] ++;

            c[0] = bsp->bbox[1]; // 1,1,1
            neg_pos[PositiveProduct(plane[i], c)] ++;

            c[1] = bsp->bbox[2]; // 1,0,1
            neg_pos[PositiveProduct(plane[i], c)] ++;

            c[0] = bsp->bbox[0]; // 0,0,1
            neg_pos[PositiveProduct(plane[i], c)] ++;

            c[2] = bsp->bbox[4]; // 0,0,0

            if (neg_pos[0] == 8)
                return;

            if (neg_pos[1] == 8)
            {
                planes--;
                if (i < planes)
                {
                    double* swap = plane[i];
                    plane[i] = plane[planes];
                    plane[planes] = swap;
                }
                i--;
            }
        }

        if (bsp->type == BSP::BSP_TYPE_INST)
        {
            bsp_insts++;
            Inst* i = (Inst*)bsp;
			if (i->inst_type == Inst::INST_TYPE::MESH)
				cb->mesh_cb(i, ((MeshInst*)i)->mesh, ((MeshInst*)i)->tm, cookie);
			else
			if (i->inst_type == Inst::INST_TYPE::SPRITE)
			{
				SpriteInst* si = (SpriteInst*)i;
				if (i->flags & INST_FLAGS::INST_VISIBLE)
					cb->sprite_cb(si,si->sprite, si->pos, si->yaw, si->anim, si->frame, si->reps, cookie);
			}
			else
			if (i->inst_type == Inst::INST_TYPE::ITEM)
			{
				// Map-authored item instances are gameplay content for the
				// authoritative item loader, not mesh/sprite renderables.
				// QueryWorldItems owns their enumeration.
			}
		}
        else
        if (bsp->type == BSP::BSP_TYPE_NODE)
        {
            bsp_nodes++;
            BSP_Node* n = (BSP_Node*)bsp;
            // FL-2957: nearest-first child ordering — visit the child whose
            // bbox center is closer to query_center first. This fills the
            // soup with nearby geometry before the early-exit cap fires.
            int first = 0, second = 1;
            if (cb->query_center && n->bsp_child[0] && n->bsp_child[1])
            {
                const double* qc = cb->query_center;
                double dx0 = qc[0] - 0.5 * (n->bsp_child[0]->bbox[0] + n->bsp_child[0]->bbox[1]);
                double dy0 = qc[1] - 0.5 * (n->bsp_child[0]->bbox[2] + n->bsp_child[0]->bbox[3]);
                double dx1 = qc[0] - 0.5 * (n->bsp_child[1]->bbox[0] + n->bsp_child[1]->bbox[1]);
                double dy1 = qc[1] - 0.5 * (n->bsp_child[1]->bbox[2] + n->bsp_child[1]->bbox[3]);
                if (dx1*dx1 + dy1*dy1 < dx0*dx0 + dy0*dy0)
                { first = 1; second = 0; }
            }
            if (planes)
            {
                if (n->bsp_child[first])
                    Query(n->bsp_child[first],planes,plane,cb,cookie);
                if (n->bsp_child[second] && !(cb->should_continue && !cb->should_continue(cookie)))
                    Query(n->bsp_child[second],planes,plane,cb,cookie);
            }
            else
            {
                if (n->bsp_child[first])
                    Query(n->bsp_child[first],cb,cookie);
                if (n->bsp_child[second] && !(cb->should_continue && !cb->should_continue(cookie)))
                    Query(n->bsp_child[second],cb,cookie);
            }
        }
        else
        if (bsp->type == BSP::BSP_TYPE_NODE_SHARE)
        {
            bsp_nodes++;
            BSP_NodeShare* s = (BSP_NodeShare*)bsp;
            int first = 0, second = 1;
            if (cb->query_center && s->bsp_child[0] && s->bsp_child[1])
            {
                const double* qc = cb->query_center;
                double dx0 = qc[0] - 0.5 * (s->bsp_child[0]->bbox[0] + s->bsp_child[0]->bbox[1]);
                double dy0 = qc[1] - 0.5 * (s->bsp_child[0]->bbox[2] + s->bsp_child[0]->bbox[3]);
                double dx1 = qc[0] - 0.5 * (s->bsp_child[1]->bbox[0] + s->bsp_child[1]->bbox[1]);
                double dy1 = qc[1] - 0.5 * (s->bsp_child[1]->bbox[2] + s->bsp_child[1]->bbox[3]);
                if (dx1*dx1 + dy1*dy1 < dx0*dx0 + dy0*dy0)
                { first = 1; second = 0; }
            }
            if (planes)
            {
                if (s->bsp_child[first])
                    Query(s->bsp_child[first],planes,plane,cb,cookie);
                if (s->bsp_child[second] && !(cb->should_continue && !cb->should_continue(cookie)))
                    Query(s->bsp_child[second],planes,plane,cb,cookie);

                Inst* i = s->head;
                while (i)
                {
                    if (cb->should_continue && !cb->should_continue(cookie))
                        break;
                    Query(i,planes,plane,cb,cookie);
                    i=i->next;
                }
            }
            else
            {
                if (s->bsp_child[first])
                    Query(s->bsp_child[first],cb,cookie);
                if (s->bsp_child[second] && !(cb->should_continue && !cb->should_continue(cookie)))
                    Query(s->bsp_child[second],cb,cookie);

                Inst* i = s->head;
                while (i)
                {
                    if (cb->should_continue && !cb->should_continue(cookie))
                        break;
                    Query(i,cb,cookie);
                    i=i->next;
                }
            }
        }
        else
        if (bsp->type == BSP::BSP_TYPE_LEAF)
        {
            bsp_nodes++;
            BSP_Leaf* l = (BSP_Leaf*)bsp;
            if (planes)
            {
                Inst* i = l->head;
                while (i)
                {
                    if (cb->should_continue && !cb->should_continue(cookie))
                        break;
                    Query(i,planes,plane,cb,cookie);
                    i=i->next;
                }
            }
            else
            {
                Inst* i = l->head;
                while (i)
                {
                    if (cb->should_continue && !cb->should_continue(cookie))
                        break;
                    Query(i,cb,cookie);
                    i=i->next;
                }
            }
        }        
        else
        {
            assert(0);
        }
    }

    // WHY World::Query: Main entry point for spatial queries — traverses both
    // BSP tree (root) and flat instance list (head_inst), invoking callbacks for
    // visible instances. Used by renderer for frustum culling and by physics for
    // raycasting. Callback pattern decouples traversal logic from rendering/physics.
    // [FLOW:WORLD] World query -- frustum culling and spatial traversal entry point
    void Query(int planes, double plane[][4], QueryWorldCB* cb, void* cookie)
    {
        bsp_tests=0;
        bsp_insts=0;
        bsp_nodes=0;

        // WHY static first: BSP tree instances (root) tested before flat list
        // (head_inst) to maximize early rejection via spatial culling.
        if (root)
        {
			if (planes > 0)
			{
				//double* pp[4] = { plane[0],plane[1],plane[2],plane[3] };
				double* pp[6] = { plane[0],plane[1],plane[2],plane[3],plane[4],plane[5] };

				Query(root, planes, pp, cb, cookie);
			}
			else
			{
				Query(root, cb, cookie);
			}
        }

		// WHY dynamic after: Flat list (head_inst) holds instances NOT in BSP tree
		// (e.g., dynamic objects not yet inserted, or flagged !INST_USE_TREE).
		// FL-2957 #24: early-exit check before flat list scan
		Inst* i = head_inst;
		if (planes > 0)
		{
			// double* pp[4] = { plane[0],plane[1],plane[2],plane[3] };
			double* pp[6] = { plane[0],plane[1],plane[2],plane[3],plane[4],plane[5] };

			while (i)
			{
				if (cb->should_continue && !cb->should_continue(cookie))
					break;
				Query(i, planes, pp, cb, cookie);
				i = i->next;
			}
		}
		else
		{
			while (i)
			{
				if (cb->should_continue && !cb->should_continue(cookie))
					break;
				Query(i, cb, cookie);
				i = i->next;
			}
		}
	}
};
