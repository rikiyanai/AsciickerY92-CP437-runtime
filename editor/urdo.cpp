//==============================================================================
// UNDO/REDO SYSTEM
//==============================================================================
// PURPOSE:
//   Provides undo/redo capability for ALL editor operations: terrain editing
//   (height/visual map modifications), mesh/sprite instance placement, and
//   terrain patch creation/deletion. All edits flow through URDO_* functions.
//
// ARCHITECTURE -- DOUBLY-LINKED LIST WITH CURSOR:
//
//   [op1] <-> [op2] <-> [op3] <-> [op4] <-> [op5]
//                         ^undo     ^redo
//
//   - undo pointer: Last executed operation (can be undone)
//   - redo pointer: Next undone operation (can be redone)
//   - New operations: Appended after undo pointer, redo chain purged
//   - Navigation: Moving undo/redo pointers moves cursor along operation chain
//
// GROUP NESTING (stack-based, up to 64 levels):
//   - URDO_Open(): Push new group onto stack, start collecting operations
//   - URDO_Close(): Pop group, seal as single undo unit
//   - Example: Merge operation creates group containing:
//     [GROUP] -> [PATCH_CREATE] -> [PATCH_UPDATE_HEIGHT] -> [PATCH_UPDATE_VISUAL]
//   - Undo reverses ALL operations in group atomically (user sees one undo step)
//   - Groups can nest: painting multiple patches creates outer group with inner
//     groups per patch
//
// SIX OPERATION TYPES:
//
//   1. CMD_GROUP: Nested group (undone/redone atomically)
//      Contains: group_head/group_tail pointers to child operations
//
//   2. CMD_PATCH_CREATE: Create or delete terrain patch (toggle attached state)
//      Contains: terrain pointer, patch pointer, cx/cy coords, attached flag
//      Do(): Toggles between attached (in terrain) and detached (in URDO)
//
//   3. CMD_PATCH_UPDATE_HEIGHT: Snapshot of height map before editing (SWAP on undo)
//      Contains: patch pointer, height[HEIGHT_CELLS+1][HEIGHT_CELLS+1] array
//      Do(): SWAPs terrain height data with stored snapshot
//
//   4. CMD_PATCH_UPDATE_VISUAL: Snapshot of visual map before editing (SWAP on undo)
//      Contains: patch pointer, visual[VISUAL_CELLS][VISUAL_CELLS] array
//      Do(): SWAPs terrain visual data (materials/shading) with stored snapshot
//
//   5. CMD_PATCH_DIAG: Snapshot of diagonal flag before flipping (SWAP on undo)
//      Contains: patch pointer, diag bitmask (which triangles face which way)
//      Do(): SWAPs diagonal state with stored snapshot
//
//   6. CMD_INST_CREATE: Create or delete mesh/sprite instance (toggle attached state)
//      Contains: inst pointer, attached flag
//      Do(): Toggles between attached (in world) and detached (in URDO)
//
// THE SWAP PATTERN (critical invariant):
//
//   URDO_PatchUpdateHeight::Do() and URDO_PatchUpdateVisual::Do() SWAP terrain
//   data with stored snapshot. After swap, the URDO struct holds the NEW data
//   (which becomes the OLD data on next undo/redo), and terrain holds the
//   restored OLD data. This makes undo and redo the SAME operation -- Do(true)
//   and Do(false) are identical, just swapping the same data back and forth.
//
//   WHY SWAP not copy: O(1) operation (no allocation), minimal memory (single
//   snapshot stored), undo and redo are symmetric (no separate logic needed).
//
// MEMORY TRACKING:
//   - bytes counter: Total memory used by undo/redo history
//   - Per-operation size tracking: Each Free() decrements bytes by struct size
//   - Detached patch data: TerrainDispose() returns size when freeing patches
//   - Reported via URDO_Bytes() for UI display (shows user how much RAM undo uses)
//
// KEY INVARIANTS:
//   - group_open > 0 while between Open/Close (operations accumulate in group)
//   - Cannot undo/redo while group is open (assert guards in URDO_Undo/Redo)
//   - PurgeRedo() called before new operations outside group (divergent history)
//   - Detached patches/instances freed when operation freed (no memory leaks)
//   - stack_depth tracks group nesting (stack[64] holds group pointers)
//
// Tags: [FLOW:EDITOR] All terrain/mesh/sprite modifications go through URDO_*
//==============================================================================

#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include "urdo.h"
#include "render.h"

extern void* GetMaterialArr();
extern void* GetPaletteArr();
extern void EditorMaterialUndoUpdate(int material_id);
extern void EditorPaletteUndoUpdate(int palette_id);
extern void EditorTerrainOverviewMarkPatchDirty(Patch* p);
extern void EditorTerrainOverviewMarkTerrainTopologyDirty(Terrain* t);

struct PaletteUndoView
{
	uint8_t rgb[3 * 256];
	char name[64];
};

struct URDO
{
	URDO* next;
	URDO* prev;

	enum CMD
	{
		CMD_GROUP,
		CMD_PATCH_CREATE,
		CMD_PATCH_UPDATE_HEIGHT,
		CMD_PATCH_UPDATE_VISUAL,
		CMD_PATCH_DIAG,
		CMD_INST_CREATE,
		CMD_MATERIAL_UPDATE,
		CMD_PALETTE_UPDATE,
	} cmd;

	void Do(bool un);
	static URDO* Alloc(CMD c);
	void Free();
};

struct URDO_Group : URDO
{
	URDO* group_head;
	URDO* group_tail;

	static void Open();
	static void Close();
	void Do(bool un);
};

struct URDO_PatchCreate : URDO
{
	int cx, cy;
	Terrain* terrain;
	Patch* patch;
	bool attached;

	static void Delete(Terrain* t, Patch* p);
	static Patch* Create(Terrain* t, int x, int y, int z);

	void Do(bool un);
};

struct URDO_PatchUpdateHeight : URDO
{
	Patch* patch;
	uint16_t height[HEIGHT_CELLS + 1][HEIGHT_CELLS + 1];
	uint16_t diag;

	static void Open(Patch* p); // alloc and copy original
	void Do(bool un);
};

struct URDO_PatchUpdateVisual : URDO
{
	Patch* patch;
	uint16_t visual[VISUAL_CELLS][VISUAL_CELLS];

	static void Open(Patch* p); // alloc and copy original
	void Do(bool un);
};

struct URDO_PatchDiag : URDO
{
	Patch* patch;
	uint16_t diag;

	static void Open(Patch* p);
	void Do(bool un);
};

struct URDO_InstCreate : URDO
{
	Inst* inst;
	bool attached;

	/*
	int flags;
	Inst* inst;
	int story_id;

	Mesh* mesh;
	double tm[16];

	World* w;
	float pos[3];

	Sprite* s; // anim >= 0
	Item* item; // anim < 0

	float yaw;
	int anim;
	int frame;
	int reps[4];
	*/

	static void Delete(Inst* i);
	static Inst* Create(Mesh* m, int flags, double tm[16], int story_id);
	static Inst* Create(World* w, Sprite* s, int flags, float pos[3], float yaw, int anim, int frame, int reps[4], int story_id);
	static Inst* Create(World* w, Item* item, int flags, float pos[3], float yaw, int story_id);

	void Do(bool un);
};

struct URDO_MaterialUpdate : URDO
{
	int material_id;
	uint32_t schema_version;
	MatCell shade[4][16];
	GlyphId glyph_cells[4][16];
	bool had_glyph_plane;

	static void Open(int material_id);
	void Do(bool un);
};

struct URDO_PaletteUpdate : URDO
{
	int palette_id;
	uint8_t rgb[3 * 256];

	static void Open(int palette_id);
	void Do(bool un);
};

static size_t bytes = 0;
static URDO* undo = 0;
static URDO* redo = 0;

static int group_open = 0;
static int stack_depth = 0;
static URDO_Group* stack[64];

// WHY dispatch-based polymorphism via switch on cmd type:
//   C-style struct with type tag for minimal overhead (no vtable). Each URDO
//   operation is a C struct (not C++ class) with a cmd enum tag. This avoids
//   virtual function call overhead and keeps memory layout simple for manual
//   memory management. The switch dispatches to the correct subtype's Do()
//   based on the cmd tag (manual vtable lookup).
void URDO::Do(bool un)
{
	switch (cmd)
	{
		case CMD_GROUP: ((URDO_Group*)this)->Do(un); break;
		case CMD_PATCH_CREATE: ((URDO_PatchCreate*)this)->Do(un); break;
		case CMD_PATCH_UPDATE_HEIGHT: ((URDO_PatchUpdateHeight*)this)->Do(un); break;
		case CMD_PATCH_UPDATE_VISUAL: ((URDO_PatchUpdateVisual*)this)->Do(un); break;
			case CMD_PATCH_DIAG: ((URDO_PatchDiag*)this)->Do(un); break;
			case CMD_INST_CREATE: ((URDO_InstCreate*)this)->Do(un); break;
			case CMD_MATERIAL_UPDATE: ((URDO_MaterialUpdate*)this)->Do(un); break;
			case CMD_PALETTE_UPDATE: ((URDO_PaletteUpdate*)this)->Do(un); break;
			default:
				assert(0);
		}
}

// WHY different cleanup per type:
//   PATCH_CREATE must detach/free patch if it was an undo of delete (patch not
//   in terrain, attached=false). INST_CREATE must free instance if it was an
//   undo of delete (instance not in world, attached=false). GROUP recursively
//   frees child operations (depth-first traversal). Other types just free the
//   URDO struct itself (height/visual/diag snapshots have no external resources).
void URDO::Free()
{
	switch (cmd)
	{
		case CMD_GROUP: 
		{
			URDO* u = ((URDO_Group*)this)->group_head;
			while (u)
			{
				URDO* n = u->next;
				u->Free();
				u = n;
			}

			bytes -= sizeof(URDO_Group);
			break;
		}
		case CMD_PATCH_CREATE:
		{
			URDO_PatchCreate* pc = (URDO_PatchCreate*)this;
			if (!pc->attached)
				bytes -= TerrainDispose(pc->patch);
			bytes -= sizeof(URDO_PatchCreate);
			break;
		}
		case CMD_PATCH_UPDATE_HEIGHT: 
			bytes -= sizeof(URDO_PatchUpdateHeight); 
			break;
		case CMD_PATCH_UPDATE_VISUAL: 
			bytes -= sizeof(URDO_PatchUpdateVisual); 
			break;
		case CMD_PATCH_DIAG:
			bytes -= sizeof(URDO_PatchDiag);
			break;

			case CMD_INST_CREATE:
			{
				URDO_InstCreate* ic = (URDO_InstCreate*)this;
				if (!ic->attached)
					HardInstDel(ic->inst);
				bytes -= sizeof(URDO_InstCreate);
				break;
			}
			case CMD_MATERIAL_UPDATE:
				bytes -= sizeof(URDO_MaterialUpdate);
				break;
			case CMD_PALETTE_UPDATE:
				bytes -= sizeof(URDO_PaletteUpdate);
				break;

			default:
				assert(0);
	}

	free(this);
}

// WHY append to undo chain AND push into group stack:
//   Dual bookkeeping needed because group nesting creates a tree structure
//   mapped onto a linear doubly-linked list. New operations are added to:
//   1) Global undo chain (urdo pointer, linear history)
//   2) Current group's child list (stack[stack_depth-1]->group_tail, tree)
//   This allows both linear navigation (undo/redo pointers walk the chain) and
//   hierarchical grouping (groups contain child operations).
URDO* URDO::Alloc(CMD c)
{
	size_t s = 0;
	switch (c)
	{
		case CMD_GROUP: s = sizeof(URDO_Group); break;
		case CMD_PATCH_CREATE: s = sizeof(URDO_PatchCreate); break;
		case CMD_PATCH_UPDATE_HEIGHT: s = sizeof(URDO_PatchUpdateHeight); break;
			case CMD_PATCH_UPDATE_VISUAL: s = sizeof(URDO_PatchUpdateVisual); break;
			case CMD_PATCH_DIAG: s = sizeof(URDO_PatchDiag); break;
			case CMD_INST_CREATE: s = sizeof(URDO_InstCreate); break;
			case CMD_MATERIAL_UPDATE: s = sizeof(URDO_MaterialUpdate); break;
			case CMD_PALETTE_UPDATE: s = sizeof(URDO_PaletteUpdate); break;
			default:
				assert(0);
		}

	URDO* urdo = (URDO*)malloc(s);
	memset(urdo, 0, s);

	bytes += s;

	if (stack_depth)
	{
		URDO_Group* p = stack[stack_depth - 1];
		if (!p->group_head)
			p->group_head = urdo;
		p->group_tail = urdo;
	}

	urdo->prev = undo;
	urdo->next = 0;
	urdo->cmd = c;
	if (undo)
		undo->next = urdo;
	undo = urdo;

	return urdo;
}

// WHY recursive stack unwinding:
//   Groups contain sub-operations that may themselves be groups, requiring
//   depth-first traversal to free all memory. The algorithm walks backward from
//   undo pointer, freeing operations. When encountering a GROUP, it must:
//   1) Restore undo/redo pointers to before the group (g->prev/g->next)
//   2) Recursively free child operations (g->group_head)
//   3) Continue unwinding if more groups on stack
//   This handles nested groups correctly (innermost groups freed first).
static void PurgeUndo()
{
	while (1)
	{
		URDO* head = redo;
		if (head)
			head->prev = 0;

		while (undo)
		{
			URDO* prev = undo->prev;
			undo->Free();
			undo = prev;
		}

		if (stack_depth)
		{
			URDO_Group* g = stack[--stack_depth];
			g->group_head = head;
			if (!head)
			{
				undo = g->prev;
				redo = g->next;
				
				g->group_tail = 0;

				g->Free();
				if (redo)
					redo->prev = undo;
				if (undo)
					undo->next = redo;
			}
			else
			{
				redo = g;
				undo = g->prev;
			}
		}
		else
			break;
	}
}

// WHY symmetric to PurgeUndo:
//   The redo chain has the same nested group structure and needs identical
//   recursive cleanup. Walks forward from redo pointer (instead of backward from
//   undo pointer), freeing redoable operations. When encountering a GROUP, it
//   restores undo/redo pointers and recursively frees child operations. Called
//   when new operations create divergent history (redo chain becomes invalid).
static void PurgeRedo()
{
	while (1)
	{
		URDO* tail = undo;
		if (tail)
			tail->next = 0;

		while (redo)
		{
			URDO* next = redo->next;
			redo->Free();
			redo = next;
		}

		if (stack_depth)
		{
			URDO_Group* g = stack[--stack_depth];
			g->group_tail = tail;
			if (!tail)
			{
				redo = g->next;
				undo = g->prev;

				g->group_head = 0;

				g->Free();
				if (undo)
					undo->next = redo;
				if (redo)
					redo->prev = undo;
			}
			else
			{
				undo = g;
				redo = g->next;
			}
		}
		else
			break;
	}
}

void URDO_Purge()
{
	assert(!group_open);

	PurgeUndo();
	PurgeRedo();
}

bool URDO_CanUndo()
{
	if (group_open)
		return false;
	if (undo)
		return true;
	int d = stack_depth;
	while (d)
	{
		if (stack[--d]->prev)
			return true;
	}
	return false;
}

bool URDO_CanRedo()
{
	if (group_open)
		return false;
	if (redo)
		return true;
	int d = stack_depth;
	while (d)
	{
		if (stack[--d]->next)
			return true;
	}
	return false;
}

size_t URDO_Bytes()
{
	return bytes;
}

// WHY stack traversal for nested group undo:
//   When encountering a GROUP operation, must recursively undo all operations
//   inside the group in reverse order. Algorithm:
//   1) Pop stack if at group boundary (restore undo/redo to before group)
//   2) Descend into groups (push onto stack, move undo to group_tail)
//   3) Undo leaf operation (undo->Do(true), move undo pointer backward)
//   4) Unwind stack if depth exceeded (undo entire group atomically)
//   max_depth parameter controls granularity (0=undo one leaf, 64=undo everything).
void URDO_Undo(int max_depth)
{
	assert(!group_open);

	while (!undo && stack_depth)
	{
		URDO_Group* g = stack[--stack_depth];
		undo = g->prev;
		redo = g;
	}

	while (undo && undo->cmd == URDO::CMD_GROUP && stack_depth < max_depth)
	{
		URDO_Group* g = (URDO_Group*)undo;
		stack[stack_depth++] = g;
		undo = g->group_tail;
		redo = 0;
	}

	if (stack_depth <= max_depth && undo)
	{
		undo->Do(true);
		redo = undo;
		undo = undo->prev;
		return;
	}

	while (stack_depth > max_depth)
	{
		while (undo)
		{
			undo->Do(true);
			redo = undo;
			undo = undo->prev;
		}

		URDO_Group* g = stack[--stack_depth];
		undo = g->prev;
		redo = g;
	}
}

// WHY symmetric stack traversal for redo:
//   Mirror of URDO_Undo, processes group operations in forward order. Algorithm:
//   1) Pop stack if at group boundary (restore undo/redo to after group)
//   2) Descend into groups (push onto stack, move redo to group_head)
//   3) Redo leaf operation (redo->Do(false), move redo pointer forward)
//   4) Unwind stack if depth exceeded (redo entire group atomically)
//   Same max_depth semantics as Undo.
void URDO_Redo(int max_depth)
{
	assert(!group_open);

	while (!redo && stack_depth)
	{
		URDO_Group* g = stack[--stack_depth];
		redo = g->next;
		undo = g;
	}

	while (redo && redo->cmd == URDO::CMD_GROUP && stack_depth < max_depth)
	{
		URDO_Group* g = (URDO_Group*)redo;
		stack[stack_depth++] = g;
		redo = g->group_head;
		undo = 0;
	}

	if (stack_depth <= max_depth && redo)
	{
		redo->Do(false);
		undo = redo;
		redo = redo->next;
		return;
	}

	while (stack_depth > max_depth)
	{
		while (redo)
		{
			redo->Do(true);
			undo = redo;
			redo = redo->next;
		}

		URDO_Group* g = stack[--stack_depth];
		redo = g->next;
		undo = g;
	}
}

void URDO_Open()
{
	assert(group_open<64);
	URDO_Group::Open();
}

void URDO_Close()
{
	assert(group_open>0);
	URDO_Group::Close();
}

Inst* URDO_Create(Mesh* m, int flags, double tm[16], int story_id)
{
	assert(group_open < 64);

	return URDO_InstCreate::Create(m,flags,tm, story_id);
}

Inst* URDO_Create(World* w, Sprite* s, int flags, float pos[3], float yaw, int anim, int frame, int reps[4], int story_id)
{
	assert(group_open < 64);

	return URDO_InstCreate::Create(w,s,flags,pos,yaw,anim,frame,reps,story_id);
}

Inst* URDO_Create(World* w, Item* item, int flags, float pos[3], float yaw, int story_id)
{
	assert(group_open < 64);

	return URDO_InstCreate::Create(w, item, flags, pos, yaw, story_id);
}

void URDO_Delete(Inst* i)
{
	assert(group_open < 64);
	URDO_InstCreate::Delete(i);
}

Patch* URDO_Create(Terrain* t, int x, int y, int z)
{
	assert(group_open < 64);
	return URDO_PatchCreate::Create(t,x,y,z);
}

void URDO_Delete(Terrain* t, Patch* p)
{
	assert(group_open < 64);
	URDO_PatchCreate::Delete(t,p);
}

void URDO_Patch(Patch* p, bool visual)
{
	if (visual)
		URDO_PatchUpdateVisual::Open(p);
	else
		URDO_PatchUpdateHeight::Open(p);
}

void URDO_Diag(Patch* p)
{
	URDO_PatchDiag::Open(p);
}

void URDO_Material(int material_id)
{
	URDO_MaterialUpdate::Open(material_id);
}

void URDO_Palette(int palette_id)
{
	URDO_PaletteUpdate::Open(palette_id);
}

// WHY purge redo before new group (divergent history):
//   Opening a top-level group (group_open==0) means user is making new edits
//   after undo, so redo chain becomes invalid (divergent timeline). Purging
//   redo frees that memory. WHY push stack: Groups nest like function calls,
//   and Alloc() needs to know which group to add operations to (stack[stack_depth-1]).
//   Subsequent URDO_* calls add to innermost group's list.
void URDO_Group::Open()
{
	if (!group_open)
		PurgeRedo();
	group_open++;

	URDO_Group* g = (URDO_Group*)Alloc(CMD_GROUP);

	stack[stack_depth++] = g;

	g->group_head = 0;
	g->group_tail = 0;

	undo = 0;
	redo = 0;
}

// WHY delete empty groups (user opened group but made no changes):
//   If group_head is null, no operations were added to the group, so the group
//   node itself is useless. Free it and restore undo pointer. WHY swap global/
//   group head/tail lists: Sealed group becomes a single node in parent's list.
//   The group's child operations (group_head to group_tail) remain accessible
//   via the group node, but global undo pointer now points to the group itself.
void URDO_Group::Close()
{
	group_open--;

	// here we swap global with group lists
	URDO_Group* g = stack[--stack_depth];

	if (!g->group_head)
	{
		// delete empty group
		undo = g->prev;
		if (g->prev)
			g->prev->next = 0;
		g->Free();
		return;
	}

	g->group_tail = undo;
	undo = g;
}

void URDO_Group::Do(bool un)
{
	if (un)
	{
		URDO* urdo = group_tail;
		while (urdo)
		{
			urdo->Do(true);
			urdo = urdo->prev;
		}
	}
	else
	{
		URDO* urdo = group_head;
		while (urdo)
		{
			urdo->Do(false);
			urdo = urdo->next;
		}
	}
}

void URDO_PatchUpdateHeight::Open(Patch* p)
{
	if (!group_open)
		PurgeRedo();

	URDO_PatchUpdateHeight* urdo = (URDO_PatchUpdateHeight*)Alloc(CMD_PATCH_UPDATE_HEIGHT);

	urdo->patch = p;
	memcpy(urdo->height, GetTerrainHeightMap(p), sizeof(uint16_t)*(HEIGHT_CELLS+1)*(HEIGHT_CELLS+1));
	urdo->diag = GetTerrainDiag(p);
}

void URDO_PatchUpdateVisual::Open(Patch* p)
{
	if (!group_open)
		PurgeRedo();

	URDO_PatchUpdateVisual* urdo = (URDO_PatchUpdateVisual*)Alloc(CMD_PATCH_UPDATE_VISUAL);

	urdo->patch = p;
	memcpy(urdo->visual, GetTerrainVisualMap(p), sizeof(uint16_t)*VISUAL_CELLS*VISUAL_CELLS);
}

// WHY SWAP not copy:
//   After swap, URDO holds NEW data (which becomes OLD data on next undo/redo),
//   and terrain holds restored OLD data. Next Do() swaps again, restoring NEW.
//   This makes undo and redo IDENTICAL operations (both just swap). O(1) time
//   complexity (loop over height map cells, no allocation). No separate undo vs
//   redo logic needed. This is the core insight of the swap-based undo system.
void URDO_PatchUpdateHeight::Do(bool un)
{
	uint16_t* t = GetTerrainHeightMap(patch);
	uint16_t* u = (uint16_t*)height;
	for (int i = 0; i < (HEIGHT_CELLS + 1)*(HEIGHT_CELLS + 1); i++)
	{
		uint16_t s = t[i];
		t[i] = u[i];
		u[i] = s;
	}

	uint16_t d = diag;
	diag = GetTerrainDiag(patch);

	UpdateTerrainHeightMap(patch);
	SetTerrainDiag(patch,d);
	EditorTerrainOverviewMarkPatchDirty(patch);
}

// WHY swap visual data mirrors the height swap pattern:
//   Same O(1) swap invariant applies to visual map (materials/shading data).
//   After swap, URDO holds NEW visual data, terrain holds OLD visual data. Next
//   Do() swaps again. Undo and redo are identical (both swap). This symmetry
//   simplifies the code and ensures undo/redo correctness (no asymmetric bugs).
void URDO_PatchUpdateVisual::Do(bool un)
{
	uint16_t* t = GetTerrainVisualMap(patch);
	uint16_t* u = (uint16_t*)visual;
	for (int i = 0; i < VISUAL_CELLS*VISUAL_CELLS; i++)
	{
		uint16_t s = t[i];
		t[i] = u[i];
		u[i] = s;
	}

	UpdateTerrainVisualMap(patch);
	EditorTerrainOverviewMarkPatchDirty(patch);
}

void URDO_MaterialUpdate::Open(int material_id)
{
	if (!group_open)
		PurgeRedo();

	URDO_MaterialUpdate* urdo = (URDO_MaterialUpdate*)Alloc(CMD_MATERIAL_UPDATE);
	Material* materials = (Material*)GetMaterialArr();

	urdo->material_id = material_id;
	urdo->schema_version = 2;
	memcpy(urdo->shade, materials[material_id].shade, sizeof(urdo->shade));
	urdo->had_glyph_plane = materials[material_id].glyph_plane && materials[material_id].glyph_plane->cells;
	for (int row = 0; row < 4; row++)
	{
		for (int col = 0; col < 16; col++)
		{
			if (urdo->had_glyph_plane)
				urdo->glyph_cells[row][col] = materials[material_id].glyph_plane->cells[row * 16 + col];
			else
				urdo->glyph_cells[row][col] = GLYPH_ID_NONE;
		}
	}
}

void URDO_MaterialUpdate::Do(bool un)
{
	Material* materials = (Material*)GetMaterialArr();
	MatCell swap[4][16];
	GlyphId glyph_swap[4][16];
	bool current_had_glyph_plane = materials[material_id].glyph_plane && materials[material_id].glyph_plane->cells;

	memcpy(swap, materials[material_id].shade, sizeof(swap));
	memcpy(materials[material_id].shade, shade, sizeof(shade));
	memcpy(shade, swap, sizeof(shade));

	for (int row = 0; row < 4; row++)
	{
		for (int col = 0; col < 16; col++)
		{
			if (current_had_glyph_plane)
				glyph_swap[row][col] = materials[material_id].glyph_plane->cells[row * 16 + col];
			else
				glyph_swap[row][col] = GLYPH_ID_NONE;
		}
	}
	if (had_glyph_plane)
	{
		if (!materials[material_id].glyph_plane || !materials[material_id].glyph_plane->cells)
		{
			materials[material_id].glyph_plane = material_glyph_plane_alloc();
			if (materials[material_id].glyph_plane)
				material_glyph_plane_init(materials[material_id].glyph_plane);
		}
		if (materials[material_id].glyph_plane && materials[material_id].glyph_plane->cells)
		{
			for (int row = 0; row < 4; row++)
			{
				for (int col = 0; col < 16; col++)
					materials[material_id].glyph_plane->cells[row * 16 + col] = glyph_cells[row][col];
			}
		}
	}
	else if (current_had_glyph_plane)
	{
		material_glyph_plane_free(materials[material_id].glyph_plane);
		materials[material_id].glyph_plane = NULL;
	}
	memcpy(glyph_cells, glyph_swap, sizeof(glyph_cells));
	had_glyph_plane = current_had_glyph_plane;
	EditorMaterialUndoUpdate(material_id);
}

void URDO_PaletteUpdate::Open(int palette_id)
{
	if (!group_open)
		PurgeRedo();

	URDO_PaletteUpdate* urdo = (URDO_PaletteUpdate*)Alloc(CMD_PALETTE_UPDATE);
	PaletteUndoView* palettes = (PaletteUndoView*)GetPaletteArr();

	urdo->palette_id = palette_id;
	memcpy(urdo->rgb, palettes[palette_id].rgb, sizeof(urdo->rgb));
}

void URDO_PaletteUpdate::Do(bool un)
{
	PaletteUndoView* palettes = (PaletteUndoView*)GetPaletteArr();
	uint8_t swap[3 * 256];

	memcpy(swap, palettes[palette_id].rgb, sizeof(swap));
	memcpy(palettes[palette_id].rgb, rgb, sizeof(rgb));
	memcpy(rgb, swap, sizeof(rgb));
	EditorPaletteUndoUpdate(palette_id);
}


void URDO_PatchDiag::Open(Patch* p)
{
	if (!group_open)
		PurgeRedo();

	URDO_PatchDiag* urdo = (URDO_PatchDiag*)Alloc(CMD_PATCH_DIAG);

	urdo->patch = p;
	urdo->diag = GetTerrainDiag(p);
}

void URDO_PatchDiag::Do(bool un)
{
	uint16_t d = diag;
	diag = GetTerrainDiag(patch);
	SetTerrainDiag(patch, d);
	EditorTerrainOverviewMarkPatchDirty(patch);
}

void URDO_PatchCreate::Delete(Terrain* t, Patch* p)
{
	if (!group_open)
		PurgeRedo();

	URDO_PatchCreate* urdo = (URDO_PatchCreate*)Alloc(CMD_PATCH_CREATE);

	urdo->terrain = t;
	urdo->patch = p;

	bytes += TerrainDetach(t,p,&urdo->cx, &urdo->cy);
	EditorTerrainOverviewMarkTerrainTopologyDirty(t);
	urdo->attached = false;
}

Patch* URDO_PatchCreate::Create(Terrain* t, int x, int y, int z)
{
	if (!group_open)
		PurgeRedo();

	URDO_PatchCreate* urdo = (URDO_PatchCreate*)Alloc(CMD_PATCH_CREATE);

	urdo->terrain = t;
	urdo->patch = AddTerrainPatch(t,x,y,z);
	EditorTerrainOverviewMarkTerrainTopologyDirty(t);
	urdo->attached = true;
	urdo->cx = x;
	urdo->cy = y;

	return urdo->patch;
}

// WHY toggle attached state:
//   Create and delete are inverse operations, so the SAME Do() function handles
//   both by toggling whether the patch is attached to terrain. When attached=true,
//   Do() detaches (TerrainDetach, mimics delete). When attached=false, Do()
//   reattaches (TerrainAttach, mimics create). This symmetry means undo and redo
//   are identical (just toggle the same flag), avoiding duplicate code.
void URDO_PatchCreate::Do(bool un)
{
	if (attached)
	{
		bytes += TerrainDetach(terrain, patch, &cx, &cy);
		EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
		attached = false;
	}
	else
	{
		bytes -= TerrainAttach(terrain, patch, cx, cy);
		EditorTerrainOverviewMarkTerrainTopologyDirty(terrain);
		attached = true;
	}
}

void URDO_InstCreate::Delete(Inst* i)
{
	if (!group_open)
		PurgeRedo();

	URDO_InstCreate* urdo = (URDO_InstCreate*)Alloc(CMD_INST_CREATE);

	urdo->inst = i;
	SoftInstDel(i);
	urdo->attached = false;
}

Inst* URDO_InstCreate::Create(Mesh* m, int flags, double tm[16], int story_id)
{
	if (!group_open)
		PurgeRedo();

	URDO_InstCreate* urdo = (URDO_InstCreate*)Alloc(CMD_INST_CREATE);

	urdo->inst = CreateInst(m,flags,tm,0,story_id);
	urdo->attached = true;

	return urdo->inst;
}

Inst* URDO_InstCreate::Create(World* w, Sprite* s, int flags, float pos[3], float yaw, int anim, int frame, int reps[4], int story_id)
{
	if (!group_open)
		PurgeRedo();

	URDO_InstCreate* urdo = (URDO_InstCreate*)Alloc(CMD_INST_CREATE);

	urdo->inst = CreateInst(w,s,flags,pos,yaw,anim,frame,reps,0,story_id);
	urdo->attached = true;

	return urdo->inst;
}

Inst* URDO_InstCreate::Create(World* w, Item* item, int flags, float pos[3], float yaw, int story_id)
{
	if (!group_open)
		PurgeRedo();

	URDO_InstCreate* urdo = (URDO_InstCreate*)Alloc(CMD_INST_CREATE);

	urdo->inst = CreateInst(w, item, flags, pos, yaw, story_id);
	urdo->attached = true;

	return urdo->inst;
}


void URDO_InstCreate::Do(bool un)
{
	if (attached)
	{
		SoftInstDel(inst);
		attached = false;
	}
	else
	{
		SoftInstAdd(inst);
		attached = true;
	}
}
