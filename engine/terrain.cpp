/**
 * @file terrain.cpp
 * @brief Quadtree-based terrain management system for Asciicker engine
 *
 * This module implements a hierarchical terrain system using a quadtree structure
 * for efficient spatial queries, rendering, and ray casting. The terrain is divided
 * into patches, each containing height and visual data, organized in a tree structure
 * that allows for dynamic expansion in any direction.
 *
 * ## Architecture
 *
 * The terrain uses a quadtree where:
 * - Internal nodes (Node) contain 4 children pointers
 * - Leaf nodes (Patch) contain actual terrain data
 * - Both inherit from QuadItem which tracks parent, height bounds, and neighbor flags
 *
 * ```
 *                    Terrain (root container)
 *                         |
 *                    QuadItem (Node or Patch)
 *                    /    |    |    \
 *                 [0]   [1]  [2]   [3]   <- quadrant indices
 *                  |
 *              (recursive until Patch leaves)
 * ```
 *
 * ## Data Structures
 *
 * - Terrain: Root container with world-space offset (x,y), tree level, and statistics
 * - Node: Internal quadtree node with 4 QuadItem* children
 * - Patch: Leaf node containing:
 *   - height[HEIGHT_CELLS+1][HEIGHT_CELLS+1]: Vertex height map (5x5 vertices)
 *   - visual[VISUAL_CELLS][VISUAL_CELLS]: Material/visual data (8x8 cells)
 *   - diag: Bitfield for triangle diagonal orientation per cell
 *   - dark: Shadow/occlusion data (when DARK_TERRAIN defined)
 * - QuadItem: Base with parent pointer, lo/hi height bounds, neighbor flags (8-bit CCW)
 *
 * ## Neighbor Flags Layout
 *
 * ```
 *     bit7  bit0  bit1
 *       \    |    /
 *        +-------+
 *   bit6-|   P   |-bit2
 *        +-------+
 *       /    |    \
 *     bit5  bit4  bit3
 * ```
 *
 * ## Key Functions
 *
 * Creation/Deletion:
 * - CreateTerrain(z)      - Create terrain with initial height z (-1 for empty)
 * - DeleteTerrain(t)      - Free all terrain memory
 * - AddTerrainPatch(t,x,y,z) - Add patch at coordinates, auto-expands tree
 * - DelTerrainPatch(t,x,y)   - Remove patch, auto-shrinks tree
 *
 * Queries:
 * - GetTerrainPatch(t,x,y)     - Get patch at world coordinates
 * - GetTerrainNeighbor(p,dx,dy) - Get neighboring patch relative to p
 * - QueryTerrain(...)          - Frustum/radius culled iteration over patches
 *
 * Ray Casting:
 * - HitTerrain(t,p,v,ret,nrm)  - Cast ray, return hit patch and position
 * - HitPatch(p,x,y,ray,...)    - Test ray against single patch triangles
 * - HitTerrain0-7              - Directional variants based on ray sign bits
 *
 * Updates:
 * - UpdateTerrainHeightMap(p)  - Recalculate bounds, diagonals, upload to GPU
 * - UpdateTerrainVisualMap(p)  - Upload visual data to GPU
 * - UpdateNodes(p)             - Propagate bounds up the tree
 *
 * File I/O:
 * - SaveTerrain(t,f)   - Write terrain to file (AS3D format)
 * - LoadTerrain(f,idx) - Read terrain from file, optionally build patch index
 *
 * Attach/Detach (for streaming):
 * - TerrainDetach(t,p,px,py) - Remove patch from tree without freeing
 * - TerrainAttach(t,p,x,y)   - Insert detached patch into tree
 * - TerrainDispose(p)        - Free a detached patch
 *
 * ## Compile-Time Options
 *
 * - EDITOR: Enable editor-specific features (texheap include)
 * - TEXHEAP: Enable GPU texture heap for patch data
 * - DARK_TERRAIN: Enable shadow/occlusion tracking per visual cell
 *
 * ## Known Issues / TODOs
 *
 * 1. Line ~170: Foliage sprite 'd' field - unclear if "depth" or "height"
 * 2. Lines ~479,491: Tap3x3::Sample() boundary condition changed from >= to >
 *    with comment "assuming '>' is fresher" - needs verification
 * 3. Lines ~465-466: Debug leftover `int a = 0;` in empty else block
 * 4. Line ~2529: Commented assertion for bottom-up rays - unclear if
 *    raytraced reflections need support for sign_case & 4
 *
 * ## Critical Constraints
 *
 * - Patch::ta MUST be at tail of struct (memory layout for TexHeap)
 * - Terrain::th MUST be at tail of struct (memory layout for TexHeap)
 * - TerrainDispose() requires patch to be detached first
 * - Node::quad[] children must all be same type (all Nodes or all Patches)
 *
 * @see terrain.h for public interface declarations
 * @see texheap.h for GPU texture allocation (when TEXHEAP defined)
 */

#include "platform/time_backend.h"

#include <stdint.h>

#define _USE_MATH_DEFINES
#include <math.h>

#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <float.h>

#ifdef EDITOR
#include "texheap.h"
#endif

#include "terrain.h"
#include "matrix.h"

#include "fast_rand.h"



inline int my_abs(int i)
{
    if (i<0)
	return -i;
    return i;
}





struct Node;

struct QuadItem
{
	Node* parent;
	uint16_t lo, hi;
	uint16_t flags; // 8 bits of neighbors, CCW, bit0 is on (-,-), bit7 is on (-,0)
};

struct Node : QuadItem
{
	QuadItem* quad[4]; // all 4 are same, either Nodes or Patches, at least 1 must not be NULL
};

struct Patch : QuadItem // 564 bytes (512x512 'raster' map would require 564KB)
{
#ifdef DARK_TERRAIN
	uint64_t dark; // (8x8)
#endif

	// visual contains:                grass, sand, rock,
	// 1bit elevation, 6bit material
	uint16_t visual[VISUAL_CELLS][VISUAL_CELLS];
	uint16_t height[HEIGHT_CELLS + 1][HEIGHT_CELLS + 1];
	uint16_t diag; // (4x4)

#ifdef TEXHEAP
	TexAlloc* ta; // MUST BE AT THE TAIL OF STRUCT !!!
#endif
};

struct Terrain
{
	int x, y; // worldspace origin from tree origin
	int level; // 0 -> root is patch, -1 -> empty
	QuadItem* root;  // Node or Patch or NULL
	int nodes;
	int patches;

#ifdef TEXHEAP
	TexHeap th; // MUST BE AT THE TAIL OF STRUCT !!!
#endif
};

#ifdef TEXHEAP
static bool TerrainTexHeapEnabled()
{
	const char* env = getenv("ASCIICKER_DISABLE_TERRAIN_TEXHEAP");
	return !(env && env[0] && strcmp(env, "0") != 0);
}
#endif

void GetTerrainBase(Terrain* t, int b[2])
{
	b[0] = t->x;
	b[1] = t->y;
}

void SetTerrainBase(Terrain* t, const int b[2])
{
	t->x = b[0];
	t->y = b[1];
}

Terrain* CreateTerrain(int z)
{
	Terrain* t = (Terrain*)malloc(sizeof(Terrain));
	t->x = 0;
	t->y = 0;
	t->nodes = 0;

#ifdef TEXHEAP
	memset(&t->th, 0, sizeof(t->th));

	if (TerrainTexHeapEnabled())
	{
		int cap = TERRAIN_TEXHEAP_CAPACITY;

		TexDesc desc[2]=
		{
			{HEIGHT_CELLS + 1, HEIGHT_CELLS + 1, GL_R16UI},
			{VISUAL_CELLS, VISUAL_CELLS, GL_R16UI}
		};

		t->th.Create(cap,cap, 2, desc, sizeof(TexPageBuffer));
	}
#endif

	if (z >= 0)
	{
		t->level = 0;

		Patch* p = (Patch*)malloc(sizeof(Patch));
		p->parent = 0;
		p->lo = z;
		p->hi = z;
		p->flags = 0; // (no neighbor)

#ifdef DARK_TERRAIN
		p->dark = 0;
#endif

		for (int y = 0; y <= HEIGHT_CELLS; y++)
			for (int x = 0; x <= HEIGHT_CELLS; x++)
				p->height[y][x] = z;
		p->diag = 0;

		/*
		for (int y = 0; y < VISUAL_CELLS; y++)
			for (int x = 0; x < VISUAL_CELLS; x++)
				p->visual[y][x] = fast_rand();
		*/
		memset(p->visual,0x01,sizeof(uint16_t)*VISUAL_CELLS*VISUAL_CELLS);

		t->root = p;
		t->patches = 1;

#ifdef TEXHEAP
		p->ta = 0;
		if (TerrainTexHeapEnabled())
		{
			TexData data[2]=
			{
				{GL_RED_INTEGER, GL_UNSIGNED_SHORT, p->height},
				{GL_RED_INTEGER, GL_UNSIGNED_SHORT, p->visual},
			};
			p->ta = t->th.Alloc(data);
			if (p->ta)
				p->ta->user = p;
		}
#endif
	}
	else
	{
		t->level = -1;
		t->root = 0;
		t->patches = 0;
	}

	return t;
}

static void DeleteTerrain(Node* n, int lev)
{
	if (lev == 1)
	{
		for (int i = 0; i < 4; i++)
		{
			Patch* p = (Patch*)n->quad[i];
			if (p)
				free(p);
		}
	}
	else
	{
		for (int i = 0; i < 4; i++)
		{
			Node* c = (Node*)n->quad[i];
			if (c)
				DeleteTerrain(c, lev - 1);
		}
	}

	free(n);
}

void DeleteTerrain(Terrain* t)
{
	if (!t)
		return;

#ifdef TEXHEAP
	t->th.Destroy();
#endif

	if (!t->root)
	{
		free(t);
		return;
	}

	if (t->level == 0)
	{
		Patch* p = (Patch*)t->root;
		free(p);
		free(t);
		return;
	}

	int lev = t->level;
	int xy = 0;
	Node* n = (Node*)t->root;
	free(t);

	DeleteTerrain(n, lev);

	/*

	while (true)
	{
	recurse:
		lev--;

		if (!lev)
		{
			for (int i = 0; i < 4; i++)
			{
				Patch* p = (Patch*)n->quad[i];
				if (p)
					free(p);
			}
		}
		else
		{
			for (int i = 0; i < 4; i++)
			{
				if (n->quad[i])
				{
					xy = (xy << 2) + i;
					n = (Node*)n->quad[i];
					goto recurse;
				}
			}
		}

		while (true)
		{
			Node* p = n->parent;
			free(n);

			if (!p)
				return;

			while ((xy & 3) < 3)
			{
				xy++;
				if (p->quad[xy & 3])
				{
					n = (Node*)p->quad[xy & 3];
					goto recurse;
				}
			}

			xy <<= 2;
			n = p;
			lev++;
		}
	}
	*/
}

struct Tap3x3
{
	Tap3x3(Patch* c)
	{
		assert(c);
		p[0][0] = GetTerrainNeighbor(c, -1, -1);
		p[0][1] = GetTerrainNeighbor(c, 0, -1);
		p[0][2] = GetTerrainNeighbor(c, +1, -1);
		p[1][0] = GetTerrainNeighbor(c, -1, 0);
		p[1][1] = c;
		p[1][2] = GetTerrainNeighbor(c, +1, 0);
		p[2][0] = GetTerrainNeighbor(c, -1, +1);
		p[2][1] = GetTerrainNeighbor(c, 0, +1);
		p[2][2] = GetTerrainNeighbor(c, +1, +1);
	}

	void SetDiag(int x, int y, bool d)
	{
		int px = 1, py = 1;

		if (x < 0)
		{
			x += HEIGHT_CELLS;
			px = 0;
		}
		else
		if (x >= HEIGHT_CELLS)
		{
			x -= HEIGHT_CELLS;
			px = 2;
		}

		if (y < 0)
		{
			y += HEIGHT_CELLS;
			py = 0;
		}
		else
		if (y >= HEIGHT_CELLS)
		{
			y -= HEIGHT_CELLS;
			py = 2;
		}

		if (p[py][px])
		{
			if (d)
				p[py][px]->diag |= 1 << (x + y * HEIGHT_CELLS);
			else
				p[py][px]->diag &= ~(1 << (x + y * HEIGHT_CELLS));
		}
		else
		{
			int a = 0;
		}
	}

	int Sample(int x, int y)
	{
		int px = 1, py = 1;

		if (x < 0)
		{
			x += HEIGHT_CELLS;
			px = 0;
		}
		else
		if (x /*>=*/ > HEIGHT_CELLS) // assuming '>' is fresher
		{
			x -= HEIGHT_CELLS;
			px = 2;
		}

		if (y < 0)
		{
			y += HEIGHT_CELLS;
			py = 0;
		}
		else
		if (y /*>=*/ > HEIGHT_CELLS) // assuming '>' is fresher
		{
			y -= HEIGHT_CELLS;
			py = 2;
		}

		if (!p[py][px])
		{
			if (px == 0)
				x = 0;
			else
			if (px == 2)
				x = HEIGHT_CELLS;

			if (py == 0)
				y = 0;
			else
			if (py == 2)
				y = HEIGHT_CELLS;

			px = 1;
			py = 1;
		}

		return p[py][px]->height[y][x];
	}

	void Update()
	{
		for (int y = -1; y <= HEIGHT_CELLS; y++)
		{
			for (int x = -1; x <= HEIGHT_CELLS; x++)
			{
				int c0 =
					+ Sample(x + 2, y + 2)
					+ Sample(x + 1, y + 2)
					+ Sample(x + 2, y + 1)
					+ Sample(x - 1, y - 1)
					+ Sample(x - 1, y)
					+ Sample(x, y - 1)
					- Sample(x, y)
					- Sample(x + 1, y + 1)
					- Sample(x + 1, y) * 2
					- Sample(x, y + 1) * 2;

				int c1 =
					+ Sample(x - 1, y + 2)
					+ Sample(x - 1, y + 1)
					+ Sample(x, y + 2)
					+ Sample(x + 2, y - 1)
					+ Sample(x + 1, y - 1)
					+ Sample(x + 2, y)
					- Sample(x, y + 1)
					- Sample(x + 1, y)
					- Sample(x, y) * 2
					- Sample(x + 1, y + 1) * 2;

				SetDiag(x, y, my_abs(c0) > my_abs(c1));
			}
		}
	}

	Patch* p[3][3];
};

Patch* GetTerrainPatch(Terrain* t, int x, int y)
{
	if (!t->root)
		return 0;

	x += t->x;
	y += t->y;

	int range = 1 << t->level;
	if (x < 0 || y < 0 || x >= range || y >= range)
		return 0;

	if (t->level == 0)
		return (Patch*)t->root;

	int lev = t->level;

	Node* n = (Node*)t->root;
	while (n)
	{
		lev--;
		int i = ((x >> lev) & 1) | (((y >> lev) & 1) << 1);

		if (lev)
			n = (Node*)n->quad[i];
		else
			return (Patch*)n->quad[i];
	}

	return 0;
}

void GetTerrainPatch(Terrain* t, Patch* p, int* x, int* y)
{
	int px = 0, py = 0;

	int lev = 0;
	QuadItem* q = p;
	Node* n = p->parent;
	while (n)
	{
		for (int i=0; i<4; i++)
			if (n->quad[i] == q)
			{
				px = px | ((i & 1) << lev);
				py = py | (((i>>1) & 1) << lev);
				break;
			}

		q = n;
		n = n->parent;
		lev++;
	}

	if (x)
		*x = px - t->x;
	if (x)
		*y = py - t->y;
}

static void UpdateNodes(Patch* p)
{
	QuadItem* q = p;
	Node* n = p->parent;

	while (n)
	{
		int lo = 0xffff;
		int hi = 0x0000;
		int fl = 0xFF;

		for (int i = 0; i < 4; i++)
		{
			if (n->quad[i])
			{
				lo = n->quad[i]->lo < lo ? n->quad[i]->lo : lo;
				hi = n->quad[i]->hi > hi ? n->quad[i]->hi : hi;
				fl = fl & n->quad[i]->flags;
			}
		}

		n->lo = lo;
		n->hi = hi;
		n->flags = fl;

		n = n->parent;
	}
}

bool DelTerrainPatch(Terrain* t, int x, int y)
{
	Patch* p = GetTerrainPatch(t, x, y);
	if (!p)
		return false;

	int flags = p->flags;
	Node* n = p->parent;

#ifdef TEXHEAP
	if (TerrainTexHeapEnabled() && p->ta)
	{
		TexAlloc* last = p->ta->Free();
		if (last)
		{
			Patch* l = (Patch*)last->user;
			UpdateTerrainVisualMap(l);
			UpdateTerrainHeightMap(l);
		}
	}
#endif
	free(p);

	t->patches--;

	if (!n)
	{
		t->level = -1;
		t->root = 0;
		return true;
	}

	// leaf trim

	QuadItem* q = p;

	while (true)
	{
		int c = 0;
		for (int i = 0; i < 4; i++)
		{
			if (n->quad[i] == q)
				n->quad[i] = 0;
			else
			if (n->quad[i])
				c++;
		}

		if (!c)
		{
			q = n;
			n = n->parent;
			free((Node*)q);
			t->nodes--;
		}
		else
			break;
	}

	// root trim

	n = (Node*)t->root;
	while (true)
	{
		int c = 0;
		int j = -1;
		for (int i = 0; i < 4; i++)
		{
			if (n->quad[i])
			{
				j = i;
				c++;
			}
		}

		// lost ...
		assert(j >= 0);

		if (c > 1)
			break;

		t->level--;

		if (j & 1)
			t->x -= 1 << t->level;
		if (j & 2)
			t->y -= 1 << t->level;

		t->root = n->quad[j];
		t->root->parent = 0;
		free(n);
		t->nodes--;

		if (t->level)
			n = (Node*)n->quad[j];
		else
			break;
	}

	Patch* np[8] =
	{
		flags & 0x01 ? GetTerrainPatch(t, x - 1, y - 1) : 0,
		flags & 0x02 ? GetTerrainPatch(t, x, y - 1) : 0,
		flags & 0x04 ? GetTerrainPatch(t, x + 1, y - 1) : 0,
		flags & 0x08 ? GetTerrainPatch(t, x + 1, y) : 0,
		flags & 0x10 ? GetTerrainPatch(t, x + 1, y + 1) : 0,
		flags & 0x20 ? GetTerrainPatch(t, x, y + 1) : 0,
		flags & 0x40 ? GetTerrainPatch(t, x - 1, y + 1) : 0,
		flags & 0x80 ? GetTerrainPatch(t, x - 1, y) : 0,
	};

	for (int i = 0; i < 8; i++)
	{
		if (np[i])
		{
			int j = (i + 4) & 7;
			np[i]->flags &= ~(1 << j);
		}
	}

	return true;
}

// [FLOW:WORLD] Patch creation -- allocate, init height/visual arrays, set neighbor flags
//
// WHY this function exists: Adds a terrain patch at world coordinates (x, y) with initial height z.
// The quadtree automatically expands upward if the patch is outside current bounds. This "grow upward"
// strategy avoids the need for a fixed maximum world size - the tree can expand infinitely in any direction.
Patch* AddTerrainPatch(Terrain* t, int x, int y, int z)
{
	const char* dbg_env = getenv("ASCIICKER_TERRAIN_DEBUG");
	const bool debug = dbg_env && *dbg_env;

	// WHY special case for empty terrain: First patch becomes the root with level 0
	if (!t->root)
	{
		t->x = -x;
		t->y = -y;
		t->level = 0;

		Patch* p = (Patch*)malloc(sizeof(Patch));
		p->parent = 0;
		p->lo = z;
		p->hi = z;
		p->flags = 0; // no neighbor (8-bit CCW neighbor flags, see header diagram)

#ifdef DARK_TERRAIN
		p->dark = 0;
#endif

		for (int y = 0; y <= HEIGHT_CELLS; y++)
			for (int x = 0; x <= HEIGHT_CELLS; x++)
				p->height[y][x] = z;
		p->diag = 0;

		/*
		for (int y = 0; y < VISUAL_CELLS; y++)
			for (int x = 0; x < VISUAL_CELLS; x++)
				p->visual[y][x] = fast_rand();
		*/
	
		memset(p->visual,0x01,sizeof(uint16_t)*VISUAL_CELLS*VISUAL_CELLS);

		t->root = p;
		t->patches = 1;

#ifdef TEXHEAP
		p->ta = 0;
		if (TerrainTexHeapEnabled())
		{
			TexData data[2] =
			{
				{GL_RED_INTEGER, GL_UNSIGNED_SHORT, p->height},
				{GL_RED_INTEGER, GL_UNSIGNED_SHORT, p->visual},
			};
			p->ta = t->th.Alloc(data);
			if (p->ta)
				p->ta->user = p;
		}
#endif

		return p;
	}

	if (debug)
	{
		fprintf(stderr, "[AddTerrainPatch] req=(%d,%d) offset=(%d,%d) level=%d\n",
			x, y, t->x, t->y, t->level);
		fflush(stderr);
	}

	x += t->x;
	y += t->y;

	// [FLOW:WORLD] Tree insertion -- parent split, quadrant calculation, auto-expand if out of bounds
	//
	// WHY auto-expand upward: When a new patch is added outside current quadtree bounds,
	// we create new parent nodes above the current root. This allows infinite world expansion
	// without pre-allocating a fixed maximum size. The root "grows upward" rather than failing.

	int range = 1 << t->level;

	// WHY expand loops: Each iteration adds one tree level, doubling the spatial coverage.
	// We expand until the new patch (x, y) fits within [0, 2*range) x [0, 2*range).
	while (x < 0)
	{
		if (debug)
		{
			fprintf(stderr, "[AddTerrainPatch] expand x<0 range=%d x=%d y=%d\n", range, x, y);
			fflush(stderr);
		}
		Node* n = (Node*)malloc(sizeof(Node));
		t->nodes++;

		// WHY quadrant selection (2 * y < range): Determines which quadrant the old root
		// should occupy in the new larger parent. The old root becomes child quad[1] or quad[3]
		// depending on whether it's in the left or right half of the new parent's spatial domain.
		if (2 * y < range)
		{
			n->quad[0] = 0;
			n->quad[1] = 0;
			n->quad[2] = 0;
			n->quad[3] = t->root;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->x += range;
			x += range;

			t->y += range;
			y += range;
		}
		else
		{
			n->quad[0] = 0;
			n->quad[1] = t->root;
			n->quad[2] = 0;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->x += range;
			x += range;
		}

		range *= 2;

		n->parent = 0;
		t->root->parent = n;
		t->root = n;
	}

	while (y < 0)
	{
		if (debug)
		{
			fprintf(stderr, "[AddTerrainPatch] expand y<0 range=%d x=%d y=%d\n", range, x, y);
			fflush(stderr);
		}
		Node* n = (Node*)malloc(sizeof(Node));
		t->nodes++;

		if (2 * x < range)
		{
			n->quad[0] = 0;
			n->quad[1] = 0;
			n->quad[2] = 0;
			n->quad[3] = t->root;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->x += range;
			x += range;

			t->y += range;
			y += range;
		}
		else
		{
			n->quad[0] = 0;
			n->quad[1] = 0;
			n->quad[2] = t->root;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->y += range;
			y += range;
		}

		range *= 2;

		n->parent = 0;
		t->root->parent = n;
		t->root = n;
	}

	while (x >= range)
	{
		if (debug)
		{
			fprintf(stderr, "[AddTerrainPatch] expand x>=range range=%d x=%d y=%d\n", range, x, y);
			fflush(stderr);
		}
		Node* n = (Node*)malloc(sizeof(Node));
		t->nodes++;

		if (2 * y > range)
		{
			n->quad[0] = t->root;
			n->quad[1] = 0;
			n->quad[2] = 0;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;
		}
		else
		{
			n->quad[0] = 0;
			n->quad[1] = 0;
			n->quad[2] = t->root;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->y += range;
			y += range;
		}

		range *= 2;

		n->parent = 0;
		t->root->parent = n;
		t->root = n;
	}

	while (y >= range)
	{
		if (debug)
		{
			fprintf(stderr, "[AddTerrainPatch] expand y>=range range=%d x=%d y=%d\n", range, x, y);
			fflush(stderr);
		}
		Node* n = (Node*)malloc(sizeof(Node));
		t->nodes++;

		if (2 * x > range)
		{
			n->quad[0] = t->root;
			n->quad[1] = 0;
			n->quad[2] = 0;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;
		}
		else
		{
			n->quad[0] = 0;
			n->quad[1] = t->root;
			n->quad[2] = 0;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->x += range;
			x += range;
		}

		range *= 2;

		n->parent = 0;
		t->root->parent = n;
		t->root = n;
	}

	int lev = t->level;

	if (lev == 0)
	{
		// already exist
		return (Patch*)t->root;
	}

	// create children from root to x,y
	//
	// WHY descend from root: Now that we've ensured (x, y) is within bounds, we descend
	// the tree from root to leaf, creating nodes as needed along the path to (x, y).

	Node* n = (Node*)t->root;
	while (n)
	{
		lev--;
		// WHY quadrant index calculation: Maps 2D grid position (x, y) to child index 0-3.
		// Formula: i = x_bit | (y_bit << 1) where x_bit and y_bit are extracted from
		// coordinates at current tree level. This gives quadrant layout:
		//   quad[0] = (x_bit=0, y_bit=0) = bottom-left
		//   quad[1] = (x_bit=1, y_bit=0) = bottom-right
		//   quad[2] = (x_bit=0, y_bit=1) = top-left
		//   quad[3] = (x_bit=1, y_bit=1) = top-right
		int i = ((x >> lev) & 1) | (((y >> lev) & 1) << 1);

		if (lev)
		{
			if (!(Node*)n->quad[i])
			{
				Node* c = (Node*)malloc(sizeof(Node));
				t->nodes++;

				c->parent = n;
				c->quad[0] = c->quad[1] = c->quad[2] = c->quad[3] = 0;
				n->quad[i] = c;
			}

			n = (Node*)n->quad[i];
		}
		else
		{
			if (n->quad[i])
			{
				// already exist
				return (Patch*)n->quad[i];
			}

			Patch* p = (Patch*)malloc(sizeof(Patch));
			t->patches++;

			n->quad[i] = p;
			p->parent = n;
			p->flags = 0;

#ifdef DARK_TERRAIN
			p->dark = 0;
#endif

			int nx = x - t->x, ny = y - t->y;
			p->diag = 0;

			if (debug)
			{
				fprintf(stderr, "[AddTerrainPatch] created patch at (%d,%d) level=%d\n",
					nx, ny, t->level);
				fflush(stderr);
			}

			Patch* np[8] =
			{
				GetTerrainPatch(t, nx - 1, ny - 1),
				GetTerrainPatch(t, nx, ny - 1),
				GetTerrainPatch(t, nx + 1, ny - 1),
				GetTerrainPatch(t, nx + 1, ny),
				GetTerrainPatch(t, nx + 1, ny + 1),
				GetTerrainPatch(t, nx, ny + 1),
				GetTerrainPatch(t, nx - 1, ny + 1),
				GetTerrainPatch(t, nx - 1, ny),
			};

			for (int i = 0; i < 8; i++)
			{
				if (np[i])
				{
					int f = np[i]->flags;
					int j = (i + 4) & 7;
					np[i]->flags |= 1 << j;
					p->flags |= 1 << i;

					if (f!=np[i]->flags)
						UpdateNodes(np[i]);

					// fill shared vertices

					switch (i)
					{
						case 0:
							p->height[0][0] = np[i]->height[HEIGHT_CELLS][HEIGHT_CELLS];
							break;

						case 1:
							for (int x=0; x<= HEIGHT_CELLS; x++)
								p->height[0][x] = np[i]->height[HEIGHT_CELLS][x];
							break;

						case 2:
							p->height[0][HEIGHT_CELLS] = np[i]->height[HEIGHT_CELLS][0];
							break;

						case 3:
							for (int y = 0; y <= HEIGHT_CELLS; y++)
								p->height[y][HEIGHT_CELLS] = np[i]->height[y][0];
							break;

						case 4:
							p->height[HEIGHT_CELLS][HEIGHT_CELLS] = np[i]->height[0][0];
							break;

						case 5:
							for (int x = 0; x <= HEIGHT_CELLS; x++)
								p->height[HEIGHT_CELLS][x] = np[i]->height[0][x];
							break;

						case 6:
							p->height[HEIGHT_CELLS][0] = np[i]->height[0][HEIGHT_CELLS];
							break;

						case 7:
							for (int y = 0; y <= HEIGHT_CELLS; y++)
								p->height[y][0] = np[i]->height[y][HEIGHT_CELLS];
							break;
					}
				}
			}

			// set free corners

			if (!(p->flags & 0x83))
				p->height[0][0] = z;

			if (!(p->flags & 0x0E))
				p->height[0][HEIGHT_CELLS] = z;

			if (!(p->flags & 0x38))
				p->height[HEIGHT_CELLS][HEIGHT_CELLS] = z;

			if (!(p->flags & 0xE0))
				p->height[HEIGHT_CELLS][0] = z;

			// interpolate free edges

			if (!(p->flags & 0x02))
			{
				// bottom
				int y = 0;
				int h0 = p->height[y][0];
				int h1 = p->height[y][HEIGHT_CELLS];
				for (int x = 1; x < HEIGHT_CELLS; x++)
					p->height[y][x] = (h0 * (HEIGHT_CELLS - x) + h1 * x + HEIGHT_CELLS / 2) / HEIGHT_CELLS;
			}

			if (!(p->flags & 0x08))
			{
				// right
				int x = HEIGHT_CELLS;
				int h0 = p->height[0][x];
				int h1 = p->height[HEIGHT_CELLS][x];
				for (int y = 1; y < HEIGHT_CELLS; y++)
					p->height[y][x] = (h0 * (HEIGHT_CELLS - y) + h1 * y + HEIGHT_CELLS / 2) / HEIGHT_CELLS;
			}

			if (!(p->flags & 0x20))
			{
				// top
				int y = HEIGHT_CELLS;
				int h0 = p->height[y][0];
				int h1 = p->height[y][HEIGHT_CELLS];
				for (int x = 1; x < HEIGHT_CELLS; x++)
					p->height[y][x] = (h0 * (HEIGHT_CELLS - x) + h1 * x + HEIGHT_CELLS / 2) / HEIGHT_CELLS;
			}

			if (!(p->flags & 0x80))
			{
				// left
				int x = 0;
				int h0 = p->height[0][x];
				int h1 = p->height[HEIGHT_CELLS][x];
				for (int y = 1; y < HEIGHT_CELLS; y++)
					p->height[y][x] = (h0 * (HEIGHT_CELLS - y) + h1 * y + HEIGHT_CELLS / 2) / HEIGHT_CELLS;
			}

			// interpolate inter-patch vertices

			for (int y = 1; y < HEIGHT_CELLS; y++)
			{
				for (int x = 1; x < HEIGHT_CELLS; x++)
				{
					double avr = 0;
					double nrm = 0;

					for (int e = 0; e < HEIGHT_CELLS; e++)
					{
						double w;

						w = 1.0 / sqrt((x - e)*(x - e) + y * y);
						nrm += w;
						avr += p->height[0][e] * w;

						w = 1.0 / sqrt((x - HEIGHT_CELLS) * (x - HEIGHT_CELLS) + (y - e)*(y - e));
						nrm += w;
						avr += p->height[e][HEIGHT_CELLS] * w;

						w = 1.0 / sqrt((x - HEIGHT_CELLS + e)*(x - HEIGHT_CELLS + e) + (y - HEIGHT_CELLS)*(y - HEIGHT_CELLS));
						nrm += w;
						avr += p->height[HEIGHT_CELLS][HEIGHT_CELLS - e] * w;

						w = 1.0 / sqrt(x * x + (y - HEIGHT_CELLS + e)*(y - HEIGHT_CELLS + e));
						nrm += w;
						avr += p->height[HEIGHT_CELLS - e][0] * w;
					}

					p->height[y][x] = (int)round(avr / nrm);
				}
			}

			p->lo = 0xffff;
			p->hi = 0x0000;
			for (int y = 0; y <= HEIGHT_CELLS; y++)
			{
				for (int x = 0; x <= HEIGHT_CELLS; x++)
				{
					p->lo = p->height[y][x] < p->lo ? p->height[y][x] : p->lo;
					p->hi = p->height[y][x] > p->hi ? p->height[y][x] : p->hi;
				}
			}

			/*
			for (int y = 0; y < VISUAL_CELLS; y++)
				for (int x = 0; x < VISUAL_CELLS; x++)
					p->visual[y][x] = fast_rand();
			*/
			memset(p->visual,0x01,sizeof(uint16_t)*VISUAL_CELLS*VISUAL_CELLS);

			// update diag flags
			Tap3x3 tap(p);
			tap.Update();

			UpdateNodes(p);

#ifdef TEXHEAP
			p->ta = 0;
			if (TerrainTexHeapEnabled())
			{
				TexData data[2] =
				{
					{GL_RED_INTEGER, GL_UNSIGNED_SHORT, p->height},
					{GL_RED_INTEGER, GL_UNSIGNED_SHORT, p->visual},
				};
				p->ta = t->th.Alloc(data);
				if (p->ta)
					p->ta->user = p;
			}
#endif

			return p;
		}
	}

	assert(0); // should never reach here
	return 0;
}

// WHY this function exists: Given a patch p and relative offset (dx, dy), find the neighboring
// patch in the quadtree. This uses a two-phase algorithm: ascend to find common ancestor, then
// descend to target. This is more efficient than recalculating absolute coordinates and searching.
Patch* GetTerrainNeighbor(Patch* p, int dx, int dy)
{
	if (dx == 0 && dy == 0)
		return p;

	// Phase 1: Ascend to find common ancestor
	// WHY accumulate offset: As we climb the tree, we track the cumulative spatial offset
	// of the starting patch relative to each ancestor's origin. When (dx, dy) falls within
	// the ancestor's spatial domain, we've found the common ancestor.
	int r = 1;

	QuadItem* q = p;
	Node* n = q->parent;

	while (n)
	{
		// WHY check which quadrant: Determine which child q is, and adjust cumulative offset.
		// quad[0] = no offset, quad[1] = +r in x, quad[2] = +r in y, quad[3] = +r in both.
		if (n->quad[1] == q)
			dx += r;
		else
		if (n->quad[2] == q)
			dy += r;
		else
		if (n->quad[3] == q)
		{
			dx += r;
			dy += r;
		}
		else
			assert(n->quad[0] == q);

		r <<= 1;

		if (dx >= 0 && dx < r && dy >= 0 && dy < r)
			break; // in range! Found common ancestor

		q = n;
		n = n->parent;
	}

	// Phase 2: Descend to target patch
	// WHY descend with quadrant index: Now that we know the target is within node n's spatial
	// domain, we descend by computing quadrant indices from the offset (dx, dy) at each level.
	while (n)
	{
		int hr = r >> 1;

		// WHY quadrant calculation: Same as insertion - compute which child contains the target.
		// i = x_bit | (y_bit << 1) where bits are determined by comparing offset to half-range.
		int i = 0;
		if (dx >= hr)
		{
			i |= 1;
			dx -= hr;
		}

		if (dy >= hr)
		{
			i |= 2;
			dy -= hr;
		}

		if (hr == 1)
			return (Patch*)n->quad[i];

		r = hr;
		n = (Node*)n->quad[i];
	}

	return 0;
}


int GetTerrainPatches(Terrain* t)
{
	return t->patches;
}

size_t GetTerrainBytes(Terrain* t)
{
	return t->patches * sizeof(Patch) + t->nodes * sizeof(Node);
}


uint16_t* GetTerrainHeightMap(Patch* p)
{
	return (uint16_t*)p->height;
}

uint16_t* GetTerrainVisualMap(Patch* p)
{
	return (uint16_t*)p->visual;
}

Patch* CalcTerrainGhost(Terrain* t, int x, int y, int z, uint16_t ghost[4 * HEIGHT_CELLS])
{
	Patch* p = GetTerrainPatch(t, x, y);
	if (p)
	{
		int i = 0;
		for (int x = 0; x < HEIGHT_CELLS; x++)
			ghost[i++] = p->height[0][x];
		for (int y = 0; y < HEIGHT_CELLS; y++)
			ghost[i++] = p->height[y][HEIGHT_CELLS];
		for (int x = HEIGHT_CELLS; x > 0; x--)
			ghost[i++] = p->height[HEIGHT_CELLS][x];
		for (int y = HEIGHT_CELLS; y > 0; y--)
			ghost[i++] = p->height[y][0];

		return p;
	}

	int nx = x, ny = y;

	Patch* np[8] =
	{
		GetTerrainPatch(t, nx - 1, ny - 1),
		GetTerrainPatch(t, nx, ny - 1),
		GetTerrainPatch(t, nx + 1, ny - 1),
		GetTerrainPatch(t, nx + 1, ny),
		GetTerrainPatch(t, nx + 1, ny + 1),
		GetTerrainPatch(t, nx, ny + 1),
		GetTerrainPatch(t, nx - 1, ny + 1),
		GetTerrainPatch(t, nx - 1, ny),
	};

	int flags = 0;

	for (int i = 0; i < 8; i++)
	{
		if (np[i])
		{
			flags |= 1 << i;

			// fill shared vertices

			switch (i)
			{
			case 0:
				ghost[0] = np[i]->height[HEIGHT_CELLS][HEIGHT_CELLS];
				break;

			case 1:
				for (int x = 0; x <= HEIGHT_CELLS; x++)
					ghost[x] = np[i]->height[HEIGHT_CELLS][x];
				break;

			case 2:
				ghost[HEIGHT_CELLS] = np[i]->height[HEIGHT_CELLS][0];
				break;

			case 3:
				for (int y = 0; y <= HEIGHT_CELLS; y++)
					ghost[HEIGHT_CELLS+y] = np[i]->height[y][0];
				break;

			case 4:
				ghost[2*HEIGHT_CELLS] = np[i]->height[0][0];
				break;

			case 5:
				for (int x = 0; x <= HEIGHT_CELLS; x++)
					ghost[3*HEIGHT_CELLS-x] = np[i]->height[0][x];
				break;

			case 6:
				ghost[3*HEIGHT_CELLS] = np[i]->height[0][HEIGHT_CELLS];
				break;

			case 7:
				ghost[0] = np[i]->height[0][HEIGHT_CELLS];
				for (int y = 1; y <= HEIGHT_CELLS; y++)
					ghost[4*HEIGHT_CELLS-y] = np[i]->height[y][HEIGHT_CELLS];
				break;
			}
		}
	}

	// set free corners

	if (!(flags & 0x83))
		ghost[0] = z;

	if (!(flags & 0x0E))
		ghost[HEIGHT_CELLS] = z;

	if (!(flags & 0x38))
		ghost[2*HEIGHT_CELLS] = z;

	if (!(flags & 0xE0))
		ghost[3*HEIGHT_CELLS] = z;

	// interpolate free edges

	if (!(flags & 0x02))
	{
		// bottom
		int y = 0;
		int h0 = ghost[0];
		int h1 = ghost[HEIGHT_CELLS];
		for (int x = 1; x < HEIGHT_CELLS; x++)
			ghost[x] = (h0 * (HEIGHT_CELLS - x) + h1 * x + HEIGHT_CELLS / 2) / HEIGHT_CELLS;
	}

	if (!(flags & 0x08))
	{
		// right
		int x = HEIGHT_CELLS;
		int h0 = ghost[HEIGHT_CELLS];
		int h1 = ghost[2*HEIGHT_CELLS];;
		for (int y = 1; y < HEIGHT_CELLS; y++)
			ghost[HEIGHT_CELLS+y] = (h0 * (HEIGHT_CELLS - y) + h1 * y + HEIGHT_CELLS / 2) / HEIGHT_CELLS;
	}

	if (!(flags & 0x20))
	{
		// top
		int y = HEIGHT_CELLS;
		int h0 = ghost[3*HEIGHT_CELLS];
		int h1 = ghost[2*HEIGHT_CELLS];
		for (int x = 1; x < HEIGHT_CELLS; x++)
			ghost[3*HEIGHT_CELLS-x] = (h0 * (HEIGHT_CELLS - x) + h1 * x + HEIGHT_CELLS / 2) / HEIGHT_CELLS;
	}

	if (!(flags & 0x80))
	{
		// left
		int x = 0;
		int h0 = ghost[0];
		int h1 = ghost[3*HEIGHT_CELLS];
		for (int y = 1; y < HEIGHT_CELLS; y++)
			ghost[4*HEIGHT_CELLS-y] = (h0 * (HEIGHT_CELLS - y) + h1 * y + HEIGHT_CELLS / 2) / HEIGHT_CELLS;
	}

	return 0;
}


void GetTerrainLimits(Patch* p, uint16_t* lo, uint16_t* hi)
{
	if (lo)
		*lo = p->lo;
	if (hi)
		*hi = p->hi;
}


void UpdateTerrainHeightMap(Patch* p)
{
	p->lo = 0xffff;
	p->hi = 0x0000;

	for (int y = 0; y <= HEIGHT_CELLS; y++)
	{
		for (int x = 0; x <= HEIGHT_CELLS; x++)
		{
			p->lo = p->height[y][x] < p->lo ? p->height[y][x] : p->lo;
			p->hi = p->height[y][x] > p->hi ? p->height[y][x] : p->hi;
		}
	}

	Tap3x3 tap(p);
	tap.Update();

#ifdef TEXHEAP
	if (TerrainTexHeapEnabled() && p->ta)
	{
		TexData data = { GL_RED_INTEGER, GL_UNSIGNED_SHORT, p->height };
		p->ta->Update(0, 1, &data); // ONLY HEIGHT !!!
	}
#endif

	UpdateNodes(p);
}

void UpdateTerrainVisualMap(Patch* p)
{
#ifdef TEXHEAP
	if (TerrainTexHeapEnabled() && p->ta)
	{
		TexData data = { GL_RED_INTEGER, GL_UNSIGNED_SHORT, p->visual };
		p->ta->Update(1, 1, &data); // ONLY VISUAL !!!
	}
#endif
}

#ifdef TEXHEAP
TexHeap* GetTerrainTexHeap(Terrain* t)
{
	return &t->th;
}

TexAlloc* GetTerrainTexAlloc(Patch* p)
{
	return p->ta;
}
#endif

uint16_t GetTerrainHi(Patch* p, uint16_t* lo)
{
	if (lo)
		*lo = p->lo;
	return p->hi;
}

uint16_t GetTerrainDiag(Patch* p)
{
	return p->diag;
}

void SetTerrainDiag(Patch* p, uint16_t diag)
{
	p->diag = diag;
}

#ifdef DARK_TERRAIN
uint64_t GetTerrainDark(Patch* p)
{
	return p->dark;
}

void SetTerrainDark(Patch* p, uint64_t dark)
{
	p->dark = dark;
}
#endif

void QueryTerrainSample(Patch* p, int x, int y, void(*cb)(Patch* p, int u, int v, double coords[3], void* cookie), void* cookie)
{
	static const double sxy = (double)VISUAL_CELLS / (double)HEIGHT_CELLS;

	for (int v = 0; v < VISUAL_CELLS; v++)
	{
		double fv = (2 * v + 1) * HEIGHT_CELLS / (2.0 * VISUAL_CELLS);
		int hy = (int)floor(fv);
		fv -= hy;

		for (int u = 0; u < VISUAL_CELLS; u++)
		{
			// get the geometry triangle for visual uv

			double fu = (2 * u + 1) * HEIGHT_CELLS / (2.0 * VISUAL_CELLS);
			int hx = (int)floor(fu);
			fu -= hx;

			double h;

			bool rot = p->diag & (1 << (hx + hy * HEIGHT_CELLS));

			if (rot)
			{
				if (u < VISUAL_CELLS - v)
				{
					// v[2], v[0], v[1]
					h = p->height[hy][hx] +
						fu * (p->height[hy][hx + 1] - p->height[hy][hx]) +
						fv * (p->height[hy + 1][hx] - p->height[hy][hx]);
				}
				else
				{
					// v[2], v[1], v[3]
					h = p->height[hy + 1][hx + 1] +
						(1 - fu) * (p->height[hy + 1][hx] - p->height[hy + 1][hx + 1]) +
						(1 - fv) * (p->height[hy][hx + 1] - p->height[hy + 1][hx + 1]);
				}
			}
			else
			{
				if (u < y)
				{
					// v[0], v[3], v[2]
					h = p->height[hy][hx] +
						fu * (p->height[hy + 1][hx + 1] - p->height[hy + 1][hx]) +
						fv * (p->height[hy + 1][hx] - p->height[hy][hx]);
				}
				else
				{
					// v[0], v[1], v[3]
					h = p->height[hy][hx] +
						fu * (p->height[hy][hx + 1] - p->height[hy][hx]) +
						fv * (p->height[hy + 1][hx + 1] - p->height[hy][hx + 1]);
				}
			}

			double pnt[] = { x + u + 0.5, y + v + 0.5, h};
			cb(p, u,v, pnt, cookie);
		}
	}
}

void QueryTerrainSample(QuadItem* q, int x, int y, int range, void(*cb)(Patch* p, int u, int v, double coords[3], void* cookie), void* cookie)
{
	if (range > VISUAL_CELLS)
	{
		range >>= 1;
		Node* n = (Node*)q;
		if (n->quad[0])
			QueryTerrainSample(n->quad[0], x, y, range, cb, cookie);
		if (n->quad[1])
			QueryTerrainSample(n->quad[1], x + range, y, range, cb, cookie);
		if (n->quad[2])
			QueryTerrainSample(n->quad[2], x, y + range, range, cb, cookie);
		if (n->quad[3])
			QueryTerrainSample(n->quad[3], x + range, y + range, range, cb, cookie);
	}
	else
		QueryTerrainSample((Patch*)q, x, y, cb, cookie);
}

#ifdef DARK_TERRAIN

struct DarkUpdater
{
	static void cb(Patch* p, int u, int v, double coords[3], void* cookie)
	{
		DarkUpdater* updater = (DarkUpdater*)cookie;

		double hit[3] = { coords[0], coords[1], coords[2] };
		Patch* q = HitTerrain(updater->t, coords, updater->lightdir, hit, 0);

		uint64_t mask = ((uint64_t)1) << (u + VISUAL_CELLS * v);

		if (q)
		{
			if (/*q != p || */ hit[2] > coords[2] + HEIGHT_SCALE/4)
			{
				p->dark |= mask;
				return;
			}
		}

		Inst* i = HitWorld(updater->w, coords, updater->lightdir, hit, 0, false, HitFilter(updater->editor));

		if (i)
		{
			if (hit[2] > coords[2])
			{
				p->dark |= mask;
				return;
			}
		}

		p->dark &= ~mask;
	}

	bool editor;
	Terrain* t;
	World* w;
	double lightdir[3];
};

struct DeferredTerrainDarkBootstrapState
{
	PatchIndex* index;
	int patch_count;
	int patch_iter;
	bool editor;
	bool announced;
	float lightpos[3];
};

static DeferredTerrainDarkBootstrapState deferred_terrain_dark_bootstrap = {};

static void ResetDeferredTerrainDarkBootstrapState()
{
	if (deferred_terrain_dark_bootstrap.index)
		FreePatchIndex(deferred_terrain_dark_bootstrap.index);
	deferred_terrain_dark_bootstrap = {};
}

void UpdateTerrainDark(Terrain* t, PatchIndex* pi, World* w, float lightpos[3], bool editor)
{
	DarkUpdater updater = { editor, t, w, {-lightpos[0], -lightpos[1], -lightpos[2] * HEIGHT_SCALE} };
	QueryTerrainSample(pi->patch, pi->x*VISUAL_CELLS, pi->y*VISUAL_CELLS, DarkUpdater::cb, &updater);
}

void UpdateTerrainDark(Terrain* t, World* w, float lightpos[3], bool editor)
{
	DarkUpdater updater = { editor, t, w, {-lightpos[0], -lightpos[1], -lightpos[2] * HEIGHT_SCALE} };
	if (t->root)
		QueryTerrainSample(t->root, -t->x*VISUAL_CELLS, -t->y*VISUAL_CELLS, VISUAL_CELLS << t->level, DarkUpdater::cb, &updater);
}

void DeferTerrainDarkBootstrap(PatchIndex* idx, int patch_count, float lightpos[3], bool editor)
{
	ResetDeferredTerrainDarkBootstrapState();
	if (!idx || patch_count <= 0)
		return;
	deferred_terrain_dark_bootstrap.index = idx;
	deferred_terrain_dark_bootstrap.patch_count = patch_count;
	deferred_terrain_dark_bootstrap.patch_iter = 0;
	deferred_terrain_dark_bootstrap.editor = editor;
	deferred_terrain_dark_bootstrap.announced = false;
	deferred_terrain_dark_bootstrap.lightpos[0] = lightpos[0];
	deferred_terrain_dark_bootstrap.lightpos[1] = lightpos[1];
	deferred_terrain_dark_bootstrap.lightpos[2] = lightpos[2];
}

void CancelDeferredTerrainDarkBootstrap()
{
	ResetDeferredTerrainDarkBootstrapState();
}

bool StepDeferredTerrainDarkBootstrap(Terrain* t, World* w, int patch_budget, uint32_t max_us)
{
	if (!deferred_terrain_dark_bootstrap.index)
		return false;
	if (!t || !w || patch_budget <= 0)
		return true;

	if (!deferred_terrain_dark_bootstrap.announced)
	{
		printf("[TERRAIN_DARK] background bootstrap %d patches (%d/frame)\n",
			deferred_terrain_dark_bootstrap.patch_count, patch_budget);
		fflush(stdout);
		deferred_terrain_dark_bootstrap.announced = true;
	}

	int patch_end = deferred_terrain_dark_bootstrap.patch_iter + patch_budget;
	if (patch_end > deferred_terrain_dark_bootstrap.patch_count)
		patch_end = deferred_terrain_dark_bootstrap.patch_count;

	uint64_t start_us = a3dGetTime();
	for (int n = deferred_terrain_dark_bootstrap.patch_iter; n < patch_end; n++)
	{
		PatchIndex* pi = deferred_terrain_dark_bootstrap.index + n;
		UpdateTerrainDark(t, pi, w, deferred_terrain_dark_bootstrap.lightpos,
			deferred_terrain_dark_bootstrap.editor);
		deferred_terrain_dark_bootstrap.patch_iter = n + 1;
		if (max_us > 0 && n + 1 < patch_end &&
			(uint32_t)(a3dGetTime() - start_us) >= max_us)
			break;
	}

	if (deferred_terrain_dark_bootstrap.patch_iter >= deferred_terrain_dark_bootstrap.patch_count)
	{
		printf("[TERRAIN_DARK] background bootstrap complete (%d patches)\n",
			deferred_terrain_dark_bootstrap.patch_count);
		fflush(stdout);
		ResetDeferredTerrainDarkBootstrapState();
		return false;
	}

	return true;
}

bool StepDeferredTerrainDarkBootstrap(Terrain* t, World* w, int patch_budget)
{
	return StepDeferredTerrainDarkBootstrap(t, w, patch_budget, 0);
}
#endif

// [FLOW:WORLD] Query traversal -- frustum test, quadrant mask, recursive descent to visible patches
//
// WHY this function exists: Iterates over all patches in the quadtree, invoking a callback for each.
// This is the simplest query variant (no frustum culling), used when you need to visit every patch.
static inline /*__forceinline*/ void QueryTerrain(QuadItem* q, int x, int y, int range, int view_flags, void(*cb)(Patch* p, int x, int y, int view_flags, void* cookie), void* cookie)
{
	if (range == VISUAL_CELLS)
	{
		// WHY view_flags & ~q->flags: The view_flags encode which neighbor directions are relevant
		// for rendering edge conditions. The ~q->flags mask removes directions where this patch
		// actually has a neighbor (neighbor flags are 8-bit CCW, bit pattern documented in header).
		cb((Patch*)q, x, y, view_flags & ~q->flags, cookie);
	}
	else
	{
		Node* n = (Node*)q;

		range >>= 1;

		// WHY visit all 4 children: This simple variant has no culling - it visits every patch.
		// Other QueryTerrain overloads add frustum/radius culling to skip invisible subtrees.
		if (n->quad[0])
			QueryTerrain(n->quad[0], x, y, range, view_flags, cb, cookie);
		if (n->quad[1])
			QueryTerrain(n->quad[1], x + range, y, range, view_flags, cb, cookie);
		if (n->quad[2])
			QueryTerrain(n->quad[2], x, y + range, range, view_flags, cb, cookie);
		if (n->quad[3])
			QueryTerrain(n->quad[3], x + range, y + range, range, view_flags, cb, cookie);
	}
}

static inline /*__forceinline*/ bool QueryTerrainShouldContinue(QueryTerrainCB* cb, void* cookie)
{
	return !cb || !cb->should_continue || cb->should_continue(cookie);
}

static inline /*__forceinline*/ void QueryTerrain(QuadItem* q, int x, int y, int range, int view_flags, QueryTerrainCB* cb, void* cookie)
{
	if (!QueryTerrainShouldContinue(cb, cookie))
		return;
	if (range == VISUAL_CELLS)
	{
		if (cb && cb->patch_cb)
			cb->patch_cb((Patch*)q, x, y, view_flags & ~q->flags, cookie);
		return;
	}

	Node* n = (Node*)q;
	range >>= 1;
	if (n->quad[0])
		QueryTerrain(n->quad[0], x, y, range, view_flags, cb, cookie);
	if (n->quad[1] && QueryTerrainShouldContinue(cb, cookie))
		QueryTerrain(n->quad[1], x + range, y, range, view_flags, cb, cookie);
	if (n->quad[2] && QueryTerrainShouldContinue(cb, cookie))
		QueryTerrain(n->quad[2], x, y + range, range, view_flags, cb, cookie);
	if (n->quad[3] && QueryTerrainShouldContinue(cb, cookie))
		QueryTerrain(n->quad[3], x + range, y + range, range, view_flags, cb, cookie);
}

// WHY frustum-culled variant: This overload performs frustum culling to skip patches outside
// the view volume. It tests the quadtree node's 3D bounding box (spatial extent + height range)
// against frustum planes, rejecting entire subtrees that are completely outside the view.
static void inline /*__forceinline*/ QueryTerrain(QuadItem* q, int x, int y, int range, int planes, double* plane[], int view_flags, void(*cb)(Patch* p, int x, int y, int view_flags, void* cookie), void* cookie)
{
	int hi = q->hi;
	int lo = q->lo;
	int fl = view_flags & ~q->flags;

	if (fl)
		lo = 0;

	int c[4] = { x, y, lo, 1 }; // 0,0,0

	// WHY test all 8 corners: To determine if the axis-aligned bounding box (AABB) of this
	// quadtree node is completely outside any frustum plane, we test all 8 corners of the box
	// against each plane. If all 8 corners are on the negative side of any plane, the entire
	// subtree can be rejected. The 8 corners are formed by (x, x+range) × (y, y+range) × (lo, hi).
	for (int i = 0; i < planes; i++)
	{
		int neg_pos[2] = { 0,0 };

		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[0] += range; // 1,0,0
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[1] += range; // 1,1,0
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[0] -= range; // 0,1,0
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[2] = hi; // 0,1,1
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[0] += range; // 1,1,1
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[1] -= range; // 1,0,1
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[0] -= range; // 0,0,1
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[2] = lo; // 0,0,0

		// WHY neg_pos[0] == 8 early out: If all 8 corners are on the negative side of this plane,
		// the entire AABB is outside the frustum. We can reject the whole subtree immediately.
		if (neg_pos[0] == 8)
			return;

		// WHY neg_pos[1] == 8 plane removal: If all 8 corners are on the positive side, this
		// entire subtree is completely inside this plane. We can remove it from further testing
		// by swapping it with the last plane and decrementing the plane count.
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

	if (range == VISUAL_CELLS)
	{
		cb((Patch*)q, x, y, planes == 0 ? 0 : fl, cookie);
	}
	else
	{
		Node* n = (Node*)q;

		range >>= 1;

		// WHY switch to simple query when planes == 0: If we've eliminated all frustum planes
		// (entire subtree is inside view), we can skip plane tests for all descendants and use
		// the faster non-culling traversal. Pass 0 for view_flags so descendants also skip
		// per-edge clip (RQ-059 containment early-exit).
		if (!planes)
		{
			if (n->quad[0])
				QueryTerrain(n->quad[0], x, y, range, 0, cb, cookie);
			if (n->quad[1])
				QueryTerrain(n->quad[1], x + range, y, range, 0, cb, cookie);
			if (n->quad[2])
				QueryTerrain(n->quad[2], x, y + range, range, 0, cb, cookie);
			if (n->quad[3])
				QueryTerrain(n->quad[3], x + range, y + range, range, 0, cb, cookie);
		}
		else
		{
			if (n->quad[0])
				QueryTerrain(n->quad[0], x, y, range, planes, plane, view_flags, cb, cookie);
			if (n->quad[1])
				QueryTerrain(n->quad[1], x + range, y, range, planes, plane, view_flags, cb, cookie);
			if (n->quad[2])
				QueryTerrain(n->quad[2], x, y + range, range, planes, plane, view_flags, cb, cookie);
			if (n->quad[3])
				QueryTerrain(n->quad[3], x + range, y + range, range, planes, plane, view_flags, cb, cookie);
		}
	}
}

static void inline /*__forceinline*/ QueryTerrain(QuadItem* q, int x, int y, int range, int planes, double* plane[], int view_flags, QueryTerrainCB* cb, void* cookie)
{
	if (!QueryTerrainShouldContinue(cb, cookie))
		return;

	int hi = q->hi;
	int lo = q->lo;
	int fl = view_flags & ~q->flags;

	if (fl)
		lo = 0;

	int c[4] = { x, y, lo, 1 };
	for (int i = 0; i < planes; i++)
	{
		int neg_pos[2] = { 0,0 };

		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[0] += range;
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[1] += range;
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[0] -= range;
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[2] = hi;
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[0] += range;
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[1] -= range;
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[0] -= range;
		neg_pos[PositiveProduct(plane[i], c)] ++;

		c[2] = lo;

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

	if (range == VISUAL_CELLS)
	{
		// RQ-059: fully contained (planes==0) → no per-edge clip needed
		if (cb && cb->patch_cb)
			cb->patch_cb((Patch*)q, x, y, planes == 0 ? 0 : fl, cookie);
		return;
	}

	Node* n = (Node*)q;
	range >>= 1;

	if (!planes)
	{
		// RQ-059: pass 0 (fully inside frustum) so descendants skip per-edge clip
		if (n->quad[0])
			QueryTerrain(n->quad[0], x, y, range, 0, cb, cookie);
		if (n->quad[1] && QueryTerrainShouldContinue(cb, cookie))
			QueryTerrain(n->quad[1], x + range, y, range, 0, cb, cookie);
		if (n->quad[2] && QueryTerrainShouldContinue(cb, cookie))
			QueryTerrain(n->quad[2], x, y + range, range, 0, cb, cookie);
		if (n->quad[3] && QueryTerrainShouldContinue(cb, cookie))
			QueryTerrain(n->quad[3], x + range, y + range, range, 0, cb, cookie);
	}
	else
	{
		if (n->quad[0])
			QueryTerrain(n->quad[0], x, y, range, planes, plane, view_flags, cb, cookie);
		if (n->quad[1] && QueryTerrainShouldContinue(cb, cookie))
			QueryTerrain(n->quad[1], x + range, y, range, planes, plane, view_flags, cb, cookie);
		if (n->quad[2] && QueryTerrainShouldContinue(cb, cookie))
			QueryTerrain(n->quad[2], x, y + range, range, planes, plane, view_flags, cb, cookie);
		if (n->quad[3] && QueryTerrainShouldContinue(cb, cookie))
			QueryTerrain(n->quad[3], x + range, y + range, range, planes, plane, view_flags, cb, cookie);
	}
}

void QueryTerrain(Terrain* t, int planes, double plane[][4], int view_flags, void(*cb)(Patch* p, int x, int y, int view_flags, void* cookie), void* cookie)
{
	if (!t || !t->root)
		return;

	if (planes<=0)
		QueryTerrain(t->root, -t->x*VISUAL_CELLS, -t->y*VISUAL_CELLS, VISUAL_CELLS << t->level, view_flags & 0xAA, cb, cookie);
	else
	{
		double* pp[6] = { plane[0],plane[1],plane[2],plane[3],plane[4],plane[5] };
		QueryTerrain(t->root, -t->x*VISUAL_CELLS, -t->y*VISUAL_CELLS, VISUAL_CELLS << t->level, planes, pp, view_flags & 0xAA, cb, cookie);
	}
}

void QueryTerrain(Terrain* t, int planes, double plane[][4], int view_flags, QueryTerrainCB* cb, void* cookie)
{
	if (!t || !t->root || !cb || !cb->patch_cb)
		return;

	if (planes<=0)
		QueryTerrain(t->root, -t->x*VISUAL_CELLS, -t->y*VISUAL_CELLS, VISUAL_CELLS << t->level, view_flags & 0xAA, cb, cookie);
	else
	{
		double* pp[6] = { plane[0],plane[1],plane[2],plane[3],plane[4],plane[5] };
		QueryTerrain(t->root, -t->x*VISUAL_CELLS, -t->y*VISUAL_CELLS, VISUAL_CELLS << t->level, planes, pp, view_flags & 0xAA, cb, cookie);
	}
}


void QueryTerrain(QuadItem* q, int x, int y, int range, const double xyr[3], int view_flags, void(*cb)(Patch* p, int x, int y, int view_flags, void* cookie), void* cookie)
{
	int hit = 0;

	double rr = xyr[2] * xyr[2];
	double dx0 = xyr[0] - x; dx0 *= dx0;
	double dy0 = xyr[1] - y; dy0 *= dy0;
	double dx1 = xyr[0] - x - range; dx1 *= dx1;
	double dy1 = xyr[1] - y - range; dy1 *= dy1;

	if (dx0 + dy0 < rr) 
		hit++;

	if (dx1 + dy0 < rr)
		hit++;

	if (dx0 + dy1 < rr)
		hit++;
	
	if (dx1 + dy1 < rr)
		hit++;

	if (!hit)
	{
		bool fit_x = xyr[0] >= x && xyr[0] <= x + range;
		bool fit_y = xyr[1] >= y && xyr[1] <= y + range;

		if (fit_y && xyr[0] >= x - xyr[2] && xyr[0] <= x + range + xyr[2] ||
			fit_x && xyr[1] >= y - xyr[2] && xyr[1] <= y + range + xyr[2])
		{
			hit = 1;
		}
		else
			return;
	}

	if (range == VISUAL_CELLS)
	{
		cb((Patch*)q, x, y, view_flags & ~q->flags, cookie);
	}
	else
	{
		Node* n = (Node*)q;
		range >>= 1;

		if (hit == 4)
		{
			if (n->quad[0])
				QueryTerrain(n->quad[0], x, y, range, view_flags, cb, cookie);
			if (n->quad[1])
				QueryTerrain(n->quad[1], x + range, y, range, view_flags, cb, cookie);
			if (n->quad[2])
				QueryTerrain(n->quad[2], x, y + range, range, view_flags, cb, cookie);
			if (n->quad[3])
				QueryTerrain(n->quad[3], x + range, y + range, range, view_flags, cb, cookie);
		}
		else
		{
			if (n->quad[0])
				QueryTerrain(n->quad[0], x, y, range, xyr, view_flags, cb, cookie);
			if (n->quad[1])
				QueryTerrain(n->quad[1], x + range, y, range, xyr, view_flags, cb, cookie);
			if (n->quad[2])
				QueryTerrain(n->quad[2], x, y + range, range, xyr, view_flags, cb, cookie);
			if (n->quad[3])
				QueryTerrain(n->quad[3], x + range, y + range, range, xyr, view_flags, cb, cookie);
		}
	}
}

void QueryTerrain(Terrain* t, double x, double y, double r, int view_flags, void(*cb)(Patch* p, int x, int y, int view_flags, void* cookie), void* cookie)
{
	if (!t || !t->root)
		return;

	if (r <= 0)
		return;

	double xyr[3] = { x,y,r };

	QueryTerrain(t->root, -t->x*VISUAL_CELLS, -t->y*VISUAL_CELLS, VISUAL_CELLS << t->level, xyr, view_flags & 0xAA, cb, cookie);
}


int triangle_intersections = 0; 
int hit_patch_tests = 0;
bool HitPatch(Patch* p, int x, int y, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	hit_patch_tests ++;
	static const double sxy = (double)VISUAL_CELLS / (double)HEIGHT_CELLS;
	bool hit = false;

	int rot = p->diag;

	for (int hy = 0; hy < HEIGHT_CELLS; hy++)
	{
		for (int hx = 0; hx < HEIGHT_CELLS; hx++)
		{
			double x0 = x + hx * sxy, x1 = x0 + sxy;
			double y0 = y + hy * sxy, y1 = y0 + sxy;

			double v[4][3] =
			{
				{x0,y0,(double)p->height[hy][hx]},
				{x1,y0,(double)p->height[hy][hx+1]},
				{x0,y1,(double)p->height[hy+1][hx]},
				{x1,y1,(double)p->height[hy+1][hx+1]},
			};

			if (rot & 1)
			{
				triangle_intersections++;
				if (RayIntersectsTriangle(ray, v[2], v[0], v[1], ret, positive_only))
				{
					hit |= 1;

					if (nrm)
					{
						double e1[3] = {v[0][0]-v[2][0],v[0][1]-v[2][1],v[0][2]-v[2][2]};
						double e2[3] = {v[1][0]-v[2][0],v[1][1]-v[2][1],v[1][2]-v[2][2]};
						CrossProduct(e1,e2,nrm);
					}
				}

				triangle_intersections++;
				if (RayIntersectsTriangle(ray, v[2], v[1], v[3], ret, positive_only))
				{
					hit |= 1;

					if (nrm)
					{
						double e1[3] = {v[1][0]-v[2][0],v[1][1]-v[2][1],v[1][2]-v[2][2]};
						double e2[3] = {v[3][0]-v[2][0],v[3][1]-v[2][1],v[3][2]-v[2][2]};
						CrossProduct(e1,e2,nrm);
					}
				}
			}
			else
			{
				triangle_intersections++;
				if (RayIntersectsTriangle(ray, v[0], v[3], v[2], ret, positive_only))
				{
					hit |= 1;

					if (nrm)
					{
						double e1[3] = {v[3][0]-v[0][0],v[3][1]-v[0][1],v[3][2]-v[0][2]};
						double e2[3] = {v[2][0]-v[0][0],v[2][1]-v[0][1],v[2][2]-v[0][2]};
						CrossProduct(e1,e2,nrm);
					}
				}

				triangle_intersections++;
				if (RayIntersectsTriangle(ray, v[0], v[1], v[3], ret, positive_only))
				{
					hit |= 1;

					if (nrm)
					{
						double e1[3] = {v[1][0]-v[0][0],v[1][1]-v[0][1],v[1][2]-v[0][2]};
						double e2[3] = {v[3][0]-v[0][0],v[3][1]-v[0][1],v[3][2]-v[0][2]};
						CrossProduct(e1,e2,nrm);
					}
				}
			}

			rot >>= 1;
		}
	}

	return hit;
}

// WHY this variant exists: Specialized for rays heading in the (+X, +Y, +Z) octant.
// This eliminates per-step direction checks in the tight inner loop.
Patch* HitTerrain0(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	int qlo = q->lo;
	int qhi = q->hi;

	// WHY early rejection using height bounds: If the ray passes entirely above or below
	// the quadtree node's height range [qlo, qhi], we can reject the entire subtree without
	// descending. This is a crucial optimization that skips potentially thousands of patch tests.
	// The 6 plane tests form a bounding box test in 3D space (ray vs. axis-aligned box with
	// height range [qlo, qhi]).
	if (ray[1] - qlo * ray[3] + ray[5] * (x + range) > 0 ||
		ray[5] * (y + range) - ray[0] - qlo * ray[4] > 0 ||
		ray[2] - ray[4] * x + ray[3] * (y + range) > 0 ||
		qhi * ray[3] - ray[5] * x - ray[1] > 0 ||
		ray[0] + qhi * ray[4] - ray[5] * y > 0 ||
		ray[4] * (x + range) - ray[3] * y - ray[2] > 0)
		return 0;

	// WHY range == VISUAL_CELLS test: When we've descended to leaf level (patch size),
	// we switch from quadtree traversal to per-triangle intersection testing.
	if (range == VISUAL_CELLS)
	{
		Patch* p = (Patch*)q;
		// WHY 2 triangles per cell: Each terrain cell is split into 2 triangles by a diagonal.
		// The 'diag' field in the patch controls which orientation (NW-SE or NE-SW) for each cell.
		// HitPatch tests both triangles for every cell in the 8x8 grid.
		if (HitPatch(p, x, y, ray, ret, nrm, positive_only))
			return p;
		else
			return 0;
	}

	// WHY recurse in this specific order: For rays heading (+X, +Y, +Z), we traverse
	// quadrants in front-to-back order to find the closest hit. The order quad[0] -> quad[1]
	// -> quad[2] -> quad[3] corresponds to spatial layout. Later hits overwrite earlier ones
	// only if closer (handled by HitPatch updating ray[9] = min distance).
	range >>= 1;
	Node* n = (Node*)q;
	Patch* p = 0;
	if (n->quad[0])
	{
		Patch* h = HitTerrain0(n->quad[0], x, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[1])
	{
		Patch* h = HitTerrain0(n->quad[1], x+range, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[2])
	{
		Patch* h = HitTerrain0(n->quad[2], x, y+range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[3])
	{
		Patch* h = HitTerrain0(n->quad[3], x+range, y+range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}

	return p;
}

// (Same algorithm as HitTerrain0, specialized for ray direction (-X, +Y, +Z))
Patch* HitTerrain1(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	int qlo = q->lo;
	int qhi = q->hi;

	if (ray[5] * (y + range) - ray[0] - qlo * ray[4] > 0 ||
		qlo * ray[3] - ray[5] * x - ray[1] > 0 ||
		ray[2] - ray[4] * x + ray[3] * y > 0 ||
		ray[0] + qhi * ray[4] - ray[5] * y > 0 ||
		ray[1] - qhi * ray[3] + ray[5] * (x + range) > 0 ||
		ray[4] * (x + range) - ray[3] * (y + range) - ray[2] > 0)
		return 0;

	if (range == VISUAL_CELLS)
	{
		Patch* p = (Patch*)q;
		if (HitPatch(p, x, y, ray, ret, nrm, positive_only))
			return p;
		else
			return 0;
	}

	// recurse
	range >>= 1;
	Node* n = (Node*)q;
	Patch* p = 0;
	if (n->quad[0])
	{
		Patch* h = HitTerrain1(n->quad[0], x, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[1])
	{
		Patch* h = HitTerrain1(n->quad[1], x + range, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[2])
	{
		Patch* h = HitTerrain1(n->quad[2], x, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[3])
	{
		Patch* h = HitTerrain1(n->quad[3], x + range, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}

	return p;
}

// (Same algorithm as HitTerrain0, specialized for ray direction (+X, -Y, +Z))
Patch* HitTerrain2(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	int qlo = q->lo;
	int qhi = q->hi;

	if (ray[0] + qlo * ray[4] - ray[5] * y > 0 ||
		ray[1] - qlo * ray[3] + ray[5] * (x + range) > 0 ||
		ray[2] + ray[3] * (y + range) - ray[4] * (x + range) > 0 ||
		ray[5] * (y + range) - ray[0] - qhi * ray[4] > 0 ||
		qhi * ray[3] - ray[5] * x - ray[1] > 0 ||
		ray[4] * x - ray[3] * y - ray[2] > 0)
		return 0;

	if (range == VISUAL_CELLS)
	{
		Patch* p = (Patch*)q;
		if (HitPatch(p, x, y, ray, ret, nrm, positive_only))
			return p;
		else
			return 0;
	}

	// recurse
	range >>= 1;
	Node* n = (Node*)q;
	Patch* p = 0;
	if (n->quad[0])
	{
		Patch* h = HitTerrain2(n->quad[0], x, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[1])
	{
		Patch* h = HitTerrain2(n->quad[1], x + range, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[2])
	{
		Patch* h = HitTerrain2(n->quad[2], x, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[3])
	{
		Patch* h = HitTerrain2(n->quad[3], x + range, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}

	return p;
}

// (Same algorithm as HitTerrain0, specialized for ray direction (-X, -Y, +Z))
Patch* HitTerrain3(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	int qlo = q->lo;
	int qhi = q->hi;

	if (qlo * ray[3] - ray[5] * x - ray[1] > 0 ||
		ray[0] + qlo * ray[4] - ray[5] * y > 0 ||
		ray[2] - ray[4] * (x + range) + ray[3] * y > 0 ||
		ray[1] - qhi * ray[3] + ray[5] * (x + range) > 0 ||
		ray[5] * (y + range) - ray[0] - qhi * ray[4] > 0 ||
		ray[4] * x - ray[3] * (y + range) - ray[2] > 0)
		return 0;

	if (range == VISUAL_CELLS)
	{
		Patch* p = (Patch*)q;
		if (HitPatch(p, x, y, ray, ret, nrm, positive_only))
			return p;
		else
			return 0;
	}

	// recurse
	range >>= 1;
	Node* n = (Node*)q;
	Patch* p = 0;
	if (n->quad[0])
	{
		Patch* h = HitTerrain3(n->quad[0], x, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[1])
	{
		Patch* h = HitTerrain3(n->quad[1], x + range, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[2])
	{
		Patch* h = HitTerrain3(n->quad[2], x, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[3])
	{
		Patch* h = HitTerrain3(n->quad[3], x + range, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}

	return p;
}

// (Same algorithm as HitTerrain0, specialized for ray direction (+X, +Y, -Z))
Patch* HitTerrain4(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	int qlo = q->lo;
	int qhi = q->hi;

	if (ray[0] + qhi * ray[4] - ray[5] * (y + range) > 0 ||
		-ray[1] + qhi * ray[3] - ray[5] * (x + range) > 0 ||
		-ray[2] + ray[4] * (x + range) - ray[3] * y > 0 ||
		-ray[0] - qlo * ray[4] + ray[5] * y > 0 ||
		ray[1] - qlo * ray[3] + ray[5] * x > 0 ||
		ray[2] - ray[4] * x + ray[3] * (y + range) > 0)
		return 0;


	if (range == VISUAL_CELLS)
	{
		Patch* p = (Patch*)q;
		if (HitPatch(p, x, y, ray, ret, nrm, positive_only))
			return p;
		else
			return 0;
	}

	// recurse
	range >>= 1;
	Node* n = (Node*)q;
	Patch* p = 0;
	if (n->quad[0])
	{
		Patch* h = HitTerrain4(n->quad[0], x, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[1])
	{
		Patch* h = HitTerrain4(n->quad[1], x + range, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[2])
	{
		Patch* h = HitTerrain4(n->quad[2], x, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[3])
	{
		Patch* h = HitTerrain4(n->quad[3], x + range, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}

	return p;
}

// (Same algorithm as HitTerrain0, specialized for ray direction (-X, +Y, -Z))
Patch* HitTerrain5(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	int qlo = q->lo;
	int qhi = q->hi;

	if (ray[1] - qhi * ray[3] + ray[5] * x > 0 ||
		ray[0] + qhi * ray[4] - ray[5] * (y + range) > 0 ||
		-ray[2] + ray[4] * (x + range) - ray[3] * (y + range) > 0 ||
		-ray[1] + qlo * ray[3] - ray[5] * (x + range) > 0 ||
		-ray[0] - qlo * ray[4] + ray[5] * y > 0 ||
		ray[2] - ray[4] * x + ray[3] * y > 0)
		return 0;

	if (range == VISUAL_CELLS)
	{
		Patch* p = (Patch*)q;
		if (HitPatch(p, x, y, ray, ret, nrm, positive_only))
			return p;
		else
			return 0;
	}

	// recurse
	range >>= 1;
	Node* n = (Node*)q;
	Patch* p = 0;
	if (n->quad[0])
	{
		Patch* h = HitTerrain5(n->quad[0], x, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[1])
	{
		Patch* h = HitTerrain5(n->quad[1], x + range, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[2])
	{
		Patch* h = HitTerrain5(n->quad[2], x, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[3])
	{
		Patch* h = HitTerrain5(n->quad[3], x + range, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}

	return p;
}

// (Same algorithm as HitTerrain0, specialized for ray direction (+X, -Y, -Z))
Patch* HitTerrain6(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	int qlo = q->lo;
	int qhi = q->hi;

	if (-ray[1] + qhi * ray[3] - ray[5] * (x + range) > 0 ||
		-ray[0] - qhi * ray[4] + ray[5] * y > 0 ||
		-ray[2] + ray[4] * x - ray[3] * y > 0 ||
		ray[1] - qlo * ray[3] + ray[5] * x > 0 ||
		ray[0] + qlo * ray[4] - ray[5] * (y + range) > 0 ||
		ray[2] - ray[4] * (x + range) + ray[3] * (y + range) > 0)
		return 0;

	if (range == VISUAL_CELLS)
	{
		Patch* p = (Patch*)q;
		if (HitPatch(p, x, y, ray, ret, nrm, positive_only))
			return p;
		else
			return 0;
	}

	// recurse
	range >>= 1;
	Node* n = (Node*)q;
	Patch* p = 0;
	if (n->quad[0])
	{
		Patch* h = HitTerrain6(n->quad[0], x, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[1])
	{
		Patch* h = HitTerrain6(n->quad[1], x + range, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[2])
	{
		Patch* h = HitTerrain6(n->quad[2], x, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[3])
	{
		Patch* h = HitTerrain6(n->quad[3], x + range, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}

	return p;
}

// (Same algorithm as HitTerrain0, specialized for ray direction (-X, -Y, -Z))
Patch* HitTerrain7(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only)
{
	int qlo = q->lo;
	int qhi = q->hi;

	if (-ray[0] - qhi * ray[4] + ray[5] * y > 0 ||
		ray[1] - qhi * ray[3] + ray[5] * x > 0 ||
		-ray[2] + ray[4] * x - ray[3] * (y + range) > 0 ||
		ray[0] + qlo * ray[4] - ray[5] * (y + range) > 0 ||
		-ray[1] + qlo * ray[3] - ray[5] * (x + range) > 0 ||
		ray[2] - ray[4] * (x + range) + ray[3] * y > 0)
		return 0;

	if (range == VISUAL_CELLS)
	{
		Patch* p = (Patch*)q;
		if (HitPatch(p, x, y, ray, ret, nrm, positive_only))
			return p;
		else
			return 0;
	}

	// recurse
	range >>= 1;
	Node* n = (Node*)q;
	Patch* p = 0;
	if (n->quad[0])
	{
		Patch* h = HitTerrain7(n->quad[0], x, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[1])
	{
		Patch* h = HitTerrain7(n->quad[1], x + range, y, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[2])
	{
		Patch* h = HitTerrain7(n->quad[2], x, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}
	if (n->quad[3])
	{
		Patch* h = HitTerrain7(n->quad[3], x + range, y + range, range, ray, ret, nrm, positive_only);
		if (h)
			p = h;
	}

	return p;
}

double HitTerrain(Patch* p, double u, double v)
{
	if (u<0 || u>1 || v<0 || v>1 || !p)
		return -1;

	int u0 = (int)floor(u*HEIGHT_CELLS), u1;
	if (u0==HEIGHT_CELLS)
	{
		u0 = HEIGHT_CELLS-1;
		u1 = HEIGHT_CELLS;
		u = 1.0;
	}
	else
	{
		u1 = u0+1;
		u = u * HEIGHT_CELLS - u0;
	}

	int v0 = (int)floor(v*HEIGHT_CELLS), v1;
	if (v0==HEIGHT_CELLS)
	{
		v0 = HEIGHT_CELLS-1;
		v1 = HEIGHT_CELLS;
		v = 1.0;
	}
	else
	{
		v1 = v0+1;
		v = v * HEIGHT_CELLS - v0;
	}

	if (p->diag & (1<<(u0+v0*HEIGHT_CELLS)))
	{
		// diagonal is u0,v0 - u1,v1

		if (u+v<1)
		{
			// interp. u0,v0 u1,v0 u0,v1
			int h00 = p->height[v0][u0];
			int h10 = p->height[v1][u0];
			int h01 = p->height[v0][u1];
			return h00 + u*(h01 - h00) + v*(h10 - h00);
		}
		else
		{
			// interp. u1,v1 u0,v1 u1,v0
			int h11 = p->height[v1][u1];
			int h10 = p->height[v1][u0];
			int h01 = p->height[v0][u1];
			return h11 + (1-u)*(h10 - h11) + (1-v)*(h01 - h11);
		}
	}
	else
	{
		// diagonal is u0,v1 - u1,v0

		if (u-v>0)
		{
			// interp. u1,v0 u1,v1 u0,v0
			int h01 = p->height[v0][u1];
			int h11 = p->height[v1][u1];
			int h00 = p->height[v0][u0];
			return h01 + (1-u)*(h00 - h01) + v*(h11 - h01);
		}
		else
		{
			// interp. u0,v1 u0,v0 u1,v1
			int h10 = p->height[v1][u0];
			int h00 = p->height[v0][u0];
			int h11 = p->height[v1][u1];
			return h10 + u*(h11 - h10) + (1-v)*(h00 - h10);
		}
	}
	
	return -1;
}

// [FLOW:WORLD] Ray intersection -- directional variant dispatch, quadtree descent, triangle tests per cell
//
// WHY 8 HitTerrain variants: Each HitTerrain function encodes ray direction sign bits to avoid
// per-step sign comparisons in the tight traversal loop. This is a classic raytracing optimization
// where we specialize the traversal code based on which octant the ray is heading.
//
// 3-bit direction sign encoding:
//   bit0 = sign(v[0]): 0=positive X, 1=negative X
//   bit1 = sign(v[1]): 0=positive Y, 1=negative Y
//   bit2 = sign(v[2]): 0=positive Z, 1=negative Z
//
// Dispatch table (sign_case is used as function pointer table index):
//   HitTerrain0 (000): v[0]>=0, v[1]>=0, v[2]>=0  (ray heading +X, +Y, +Z)
//   HitTerrain1 (001): v[0]<0,  v[1]>=0, v[2]>=0  (ray heading -X, +Y, +Z)
//   HitTerrain2 (010): v[0]>=0, v[1]<0,  v[2]>=0  (ray heading +X, -Y, +Z)
//   HitTerrain3 (011): v[0]<0,  v[1]<0,  v[2]>=0  (ray heading -X, -Y, +Z)
//   HitTerrain4 (100): v[0]>=0, v[1]>=0, v[2]<0   (ray heading +X, +Y, -Z)
//   HitTerrain5 (101): v[0]<0,  v[1]>=0, v[2]<0   (ray heading -X, +Y, -Z)
//   HitTerrain6 (110): v[0]>=0, v[1]<0,  v[2]<0   (ray heading +X, -Y, -Z)
//   HitTerrain7 (111): v[0]<0,  v[1]<0,  v[2]<0   (ray heading -X, -Y, -Z)
//
// Example: ray heading (+X, -Y, +Z) -> signs = (0, 1, 0) -> binary 010 = 2 -> HitTerrain2
//
// Each variant uses hardcoded step directions (no branches) for maximum inner-loop performance.
Patch* HitTerrain(Terrain* t, double p[3], double v[3], double ret[3], double nrm[3], bool positive_only)
{
	if (!t || !t->root)
		return 0;

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

	// Compute sign_case index from ray direction signs
	int sign_case = 0;

	if (v[0] >= 0)
		sign_case |= 1;
	if (v[1] >= 0)
		sign_case |= 2;
	if (v[2] >= 0)
		sign_case |= 4;

	// assert((sign_case & 4) == 0); // watching from the bottom? -> raytraced reflections?

	// Function pointer table for 8 directional variants
	static Patch* (* const func_vect[])(QuadItem* q, int x, int y, int range, double ray[10], double ret[3], double nrm[3], bool positive_only) =
	{
		HitTerrain0,
		HitTerrain1,
		HitTerrain2,
		HitTerrain3,
		HitTerrain4,
		HitTerrain5,
		HitTerrain6,
		HitTerrain7
	};

	hit_patch_tests = 0;
	triangle_intersections = 0;
	Patch* patch = func_vect[sign_case](t->root, -t->x*VISUAL_CELLS, -t->y*VISUAL_CELLS, VISUAL_CELLS << t->level, ray, ret, nrm, positive_only);
	return patch;
}

size_t TerrainDetach(Terrain* t, Patch* p, int* px, int* py)
{
	int x, y;
	GetTerrainPatch(t, p, &x, &y);

	if (px)
		*px = x;
	if (py)
		*py = y;

	int flags = p->flags;
	Node* n = p->parent;

	/////////////
	// free(p);
	t->patches--;
	p->parent = 0;
	/////////////

	if (!n)
	{
		t->level = -1;
		t->root = 0;
		return sizeof(Patch);
	}

	// leaf trim

	QuadItem* q = p;

	while (true)
	{
		int c = 0;
		for (int i = 0; i < 4; i++)
		{
			if (n->quad[i] == q)
				n->quad[i] = 0;
			else
				if (n->quad[i])
					c++;
		}

		if (!c)
		{
			q = n;
			n = n->parent;
			free((Node*)q);
			t->nodes--;
		}
		else
			break;
	}

	// root trim

	n = (Node*)t->root;
	while (true)
	{
		int c = 0;
		int j = -1;
		for (int i = 0; i < 4; i++)
		{
			if (n->quad[i])
			{
				j = i;
				c++;
			}
		}

		// lost ...
		assert(j >= 0);

		if (c > 1)
			break;

		t->level--;

		if (j & 1)
			t->x -= 1 << t->level;
		if (j & 2)
			t->y -= 1 << t->level;

		t->root = n->quad[j];
		t->root->parent = 0;
		free(n);
		t->nodes--;

		if (t->level)
			n = (Node*)t->root;
		else
			break;
	}

	Patch* np[8] =
	{
		flags & 0x01 ? GetTerrainPatch(t, x - 1, y - 1) : 0,
		flags & 0x02 ? GetTerrainPatch(t, x, y - 1) : 0,
		flags & 0x04 ? GetTerrainPatch(t, x + 1, y - 1) : 0,
		flags & 0x08 ? GetTerrainPatch(t, x + 1, y) : 0,
		flags & 0x10 ? GetTerrainPatch(t, x + 1, y + 1) : 0,
		flags & 0x20 ? GetTerrainPatch(t, x, y + 1) : 0,
		flags & 0x40 ? GetTerrainPatch(t, x - 1, y + 1) : 0,
		flags & 0x80 ? GetTerrainPatch(t, x - 1, y) : 0,
	};

	for (int i = 0; i < 8; i++)
	{
		if (np[i])
		{
			int j = (i + 4) & 7;
			np[i]->flags &= ~(1 << j);
		}
	}

	return sizeof(Patch);
}

size_t TerrainAttach(Terrain* t, Patch* p, int x, int y)
{
	if (!t->root)
	{
		t->x = -x;
		t->y = -y;
		t->level = 0;

		p->parent = 0;

		t->root = p;
		t->patches = 1;

		return sizeof(Patch);
	}

	x += t->x;
	y += t->y;

	// create parents such root encloses x,y

	int range = 1 << t->level;

	while (x < 0)
	{
		Node* n = (Node*)malloc(sizeof(Node));
		t->nodes++;

		if (2 * y < range)
		{
			n->quad[0] = 0;
			n->quad[1] = 0;
			n->quad[2] = 0;
			n->quad[3] = t->root;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->x += range;
			x += range;

			t->y += range;
			y += range;
		}
		else
		{
			n->quad[0] = 0;
			n->quad[1] = t->root;
			n->quad[2] = 0;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->x += range;
			x += range;
		}

		range *= 2;

		n->parent = 0;
		t->root->parent = n;
		t->root = n;
	}

	while (y < 0)
	{
		Node* n = (Node*)malloc(sizeof(Node));
		t->nodes++;

		if (2 * x < range)
		{
			n->quad[0] = 0;
			n->quad[1] = 0;
			n->quad[2] = 0;
			n->quad[3] = t->root;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->x += range;
			x += range;

			t->y += range;
			y += range;
		}
		else
		{
			n->quad[0] = 0;
			n->quad[1] = 0;
			n->quad[2] = t->root;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->y += range;
			y += range;
		}

		range *= 2;

		n->parent = 0;
		t->root->parent = n;
		t->root = n;
	}

	while (x >= range)
	{
		Node* n = (Node*)malloc(sizeof(Node));
		t->nodes++;

		if (2 * y > range)
		{
			n->quad[0] = t->root;
			n->quad[1] = 0;
			n->quad[2] = 0;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;
		}
		else
		{
			n->quad[0] = 0;
			n->quad[1] = 0;
			n->quad[2] = t->root;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->y += range;
			y += range;
		}

		range *= 2;

		n->parent = 0;
		t->root->parent = n;
		t->root = n;
	}

	while (y >= range)
	{
		Node* n = (Node*)malloc(sizeof(Node));
		t->nodes++;

		if (2 * x > range)
		{
			n->quad[0] = t->root;
			n->quad[1] = 0;
			n->quad[2] = 0;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;
		}
		else
		{
			n->quad[0] = 0;
			n->quad[1] = t->root;
			n->quad[2] = 0;
			n->quad[3] = 0;

			n->lo = t->root->lo;
			n->hi = t->root->hi;

			t->level++;

			t->x += range;
			x += range;
		}

		range *= 2;

		n->parent = 0;
		t->root->parent = n;
		t->root = n;
	}

	int lev = t->level;

	if (lev == 0)
	{
		// already exist
		return 0;
	}

	// create children from root to x,y

	Node* n = (Node*)t->root;
	while (n)
	{
		lev--;
		int i = ((x >> lev) & 1) | (((y >> lev) & 1) << 1);

		if (lev)
		{
			if (!(Node*)n->quad[i])
			{
				Node* c = (Node*)malloc(sizeof(Node));
				t->nodes++;

				c->parent = n;
				c->quad[0] = c->quad[1] = c->quad[2] = c->quad[3] = 0;
				n->quad[i] = c;
			}

			n = (Node*)n->quad[i];
		}
		else
		{
			if (n->quad[i])
			{
				// already exist
				return 0;
			}

			t->patches++;

			n->quad[i] = p;
			p->parent = n;

			UpdateNodes(p);

			return sizeof(Patch);
		}
	}

	assert(0); // should never reach here
	return 0;
}

size_t TerrainDispose(Patch* p)
{
	// MUST BE DETACHED!

#ifdef TEXHEAP
	if (TerrainTexHeapEnabled() && p->ta)
	{
		TexAlloc* last = p->ta->Free();
		if (last)
		{
			Patch* l = (Patch*)last->user;
			UpdateTerrainVisualMap(l);
			UpdateTerrainHeightMap(l);
		}
	}
#endif

	free(p);

	return sizeof(Patch);
}



struct FileHeader
{
	uint32_t file_sign;
	uint32_t header_size;
	uint32_t num_patches;
	uint32_t reserved;
};

// [DATA-CONTRACT:A3D] FilePatch struct - matches on-disk binary layout (188 bytes total)
struct FilePatch
{
	int32_t x,y; // 8 bytes - patch world coordinates
	uint16_t visual[VISUAL_CELLS][VISUAL_CELLS]; // 128 bytes (8x8 cells, 2 bytes each)
	uint16_t height[HEIGHT_CELLS + 1][HEIGHT_CELLS + 1]; // 50 bytes (5x5 vertices, 2 bytes each)
	uint16_t diag; // 2 bytes - triangle diagonal orientation bitfield
};

void SaveTree(FILE* f, int x, int y, int lev, const QuadItem* item)
{
	if (!lev)
	{
		// [DATA-CONTRACT:A3D] Terrain patch serialization format (188 bytes per patch):
		//   int32_t x, y                      (8 bytes) - patch coordinates in world space
		//   uint16_t visual[8][8]             (128 bytes) - material/shade/elevation per cell
		//   uint16_t height[5][5]             (50 bytes) - vertex heights (5x5 grid)
		//   uint16_t diag                     (2 bytes) - triangle diagonal orientation bitfield
		// Total: 8 + 128 + 50 + 2 = 188 bytes per patch
		const Patch* p = (const Patch*)item;
		fwrite(&x,1,sizeof(int32_t),f);
		fwrite(&y,1,sizeof(int32_t),f);
		fwrite(p->visual,VISUAL_CELLS*VISUAL_CELLS,sizeof(uint16_t),f);
		fwrite(p->height,(HEIGHT_CELLS+1)*(HEIGHT_CELLS+1),sizeof(uint16_t),f);
		fwrite(&p->diag,1,sizeof(int16_t),f);
		return;
	}

	const Node* n = (const Node*)item;
	lev--;

	int r = 1<<lev;

	if (n->quad[0])
		SaveTree(f,x,y,lev,n->quad[0]);
	if (n->quad[1])
		SaveTree(f,x+r,y,lev,n->quad[1]);
	if (n->quad[2])
		SaveTree(f,x,y+r,lev,n->quad[2]);
	if (n->quad[3])
		SaveTree(f,x+r,y+r,lev,n->quad[3]);
}

// [DATA-CONTRACT:A3D] Terrain file format (.a3d):
//   FileHeader (16 bytes):
//     uint32_t file_sign = "AS3D"     (4 bytes) - magic number signature
//     uint32_t header_size            (4 bytes) - sizeof(FileHeader) for version check
//     uint32_t num_patches            (4 bytes) - count of terrain patches in file
//     uint32_t reserved               (4 bytes) - reserved for future use
//   Followed by num_patches × FilePatch records (188 bytes each, see SaveTree)
bool SaveTerrain(const Terrain* t, FILE* f)
{
	if (!t || !f)
		return false;

	FileHeader hdr =
	{
		*(uint32_t*)"AS3D",
		(uint32_t)sizeof(FileHeader),
		(uint32_t)t->patches,
		(uint32_t)0
	};

	fwrite(&hdr,1,sizeof(FileHeader),f);

	if (t->root)
		SaveTree(f, -t->x, -t->y, t->level,t->root);

	return !ferror(f);
}

#ifdef TEXHEAP
// [FL-3838] GPU readback verification for terrain visual map uploads.
// Gated behind ASCIICKER_GPU_VERIFY env var (value = sample rate, e.g. 100 = every 100th patch).
// Reads each GPU texture page once (cached), then compares per-patch 8x8 visual regions.
static void VerifyTerrainVisualGPU(Patch* p, int patch_x, int patch_y)
{
	const char* verify_env = getenv("ASCIICKER_GPU_VERIFY");
	if (!verify_env || !*verify_env)
		return;

	int sample_rate = atoi(verify_env);
	if (sample_rate <= 0)
		sample_rate = 100;

	static int patch_count = 0;
	patch_count++;
	if (patch_count % sample_rate != 0)
		return;

	if (!p->ta)
	{
		printf("[FL-3838] GPU_VERIFY patch=(%d,%d) SKIP (p->ta is null)\n",
			patch_x, patch_y);
		fflush(stdout);
		return;
	}

	TexPage* page = p->ta->page;
	TexHeap* heap = page->heap;
	int tex_id = page->tex[1]; // layer 1 = visual

	// Cache full-page readback per unique GPU page
	static GLuint cached_page_id = 0;
	static uint16_t* page_buf = 0;
	static int page_w = 0;
	static int page_h = 0;

	if ((GLuint)(uintptr_t)page != cached_page_id)
	{
		free(page_buf);
		page_w = heap->cap_x * heap->tex[1].item_w;
		page_h = heap->cap_y * heap->tex[1].item_h;
		size_t buf_bytes = (size_t)page_w * page_h * sizeof(uint16_t);
		page_buf = (uint16_t*)malloc(buf_bytes);
		if (!page_buf)
		{
			printf("[FL-3838] GPU_VERIFY patch=(%d,%d) OOM (page %dx%d)\n",
				patch_x, patch_y, page_w, page_h);
			fflush(stdout);
			cached_page_id = 0;
			return;
		}

		GLint prev_tex;
		glGetIntegerv(GL_TEXTURE_BINDING_2D, &prev_tex);
		glBindTexture(GL_TEXTURE_2D, tex_id);
		GLenum stale = glGetError(); (void)stale;
		glGetTexImage(GL_TEXTURE_2D, 0, GL_RED_INTEGER, GL_UNSIGNED_SHORT, page_buf);
		GLenum read_err = glGetError();
		glBindTexture(GL_TEXTURE_2D, prev_tex);

		if (read_err != GL_NO_ERROR)
		{
			printf("[FL-3838] GPU_VERIFY patch=(%d,%d) READBACK_FAIL gl_err=0x%04X\n",
				patch_x, patch_y, read_err);
			fflush(stdout);
			free(page_buf);
			page_buf = 0;
			cached_page_id = 0;
			return;
		}

		cached_page_id = (GLuint)(uintptr_t)page;
	}

	// Compare: visual data lives at (p->ta->x * item_w, p->ta->y * item_h) in the page
	int base_x = p->ta->x * heap->tex[1].item_w;
	int base_y = p->ta->y * heap->tex[1].item_h;

	int mismatches = 0;
	int first_cell_x = -1, first_cell_y = -1;
	uint16_t first_cpu = 0, first_gpu = 0;

	for (int vy = 0; vy < VISUAL_CELLS; vy++)
	{
		for (int vx = 0; vx < VISUAL_CELLS; vx++)
		{
			uint16_t cpu_val = p->visual[vy][vx];
			uint16_t gpu_val = page_buf[(base_y + vy) * page_w + (base_x + vx)];
			if (cpu_val != gpu_val)
			{
				if (first_cell_x < 0)
				{
					first_cell_x = vx;
					first_cell_y = vy;
					first_cpu = cpu_val;
					first_gpu = gpu_val;
				}
				mismatches++;
			}
		}
	}

	if (mismatches == 0)
		printf("[FL-3838] GPU_VERIFY patch=(%d,%d) MATCH\n", patch_x, patch_y);
	else
		printf("[FL-3838] GPU_VERIFY patch=(%d,%d) MISMATCH mismatches=%d first_cell=(%d,%d) cpu=0x%04X gpu=0x%04X\n",
			patch_x, patch_y, mismatches,
			first_cell_x, first_cell_y,
			first_cpu, first_gpu);
	fflush(stdout);
}
#endif

// [DATA-CONTRACT:A3D] Terrain file loader - reads .a3d format (see SaveTerrain for format spec).
// WHY PatchIndex**: Optionally builds an index array mapping patch number to (Patch*, x, y) for
// fast lookup by index. This is used by the editor for patch selection and modification.
Terrain* LoadTerrain(FILE* f, PatchIndex** idx)
{
	if (!f)
		return 0;

	const char* dbg_env = getenv("ASCIICKER_TERRAIN_DEBUG");
	const bool debug = dbg_env && *dbg_env;

	FileHeader hdr;
	if (fread(&hdr,1,sizeof(FileHeader),f)!=sizeof(FileHeader))
	{
		return 0;
	}

	if (hdr.file_sign != *(uint32_t*)"AS3D" ||
		hdr.header_size != sizeof(FileHeader))
	{
		return 0;
	}

	PatchIndex* index = idx ? (PatchIndex*)malloc(sizeof(PatchIndex) * hdr.num_patches) : 0;
	int min_x = 0, max_x = 0, min_y = 0, max_y = 0;
	bool coords_init = false;

	if (debug)
	{
		fprintf(stderr, "[LoadTerrain] Loading %u patches\n", hdr.num_patches);
		fflush(stderr);
	}

	Terrain* t = CreateTerrain();
	for (unsigned i = 0; i < hdr.num_patches; i++)
	{
		FilePatch pch;
		if (fread(&pch,1,sizeof(FilePatch),f)!=sizeof(FilePatch))
		{
			DeleteTerrain(t);
			if (idx)
				*idx = 0;
			return 0;
		}

		if (debug)
		{
			fprintf(stderr, "[LoadTerrain] Patch %u: (%d, %d)\n", i, pch.x, pch.y);
			fflush(stderr);
		}

		Patch* p = AddTerrainPatch(t, pch.x, pch.y, 0);

		if (index)
		{
			PatchIndex* pi = index+i;
			pi->patch = p;
			pi->x = pch.x;
			pi->y = pch.y;
		}

		memcpy(p->visual,pch.visual,sizeof(uint16_t)*VISUAL_CELLS*VISUAL_CELLS);
		memcpy(p->height,pch.height,sizeof(uint16_t)*(HEIGHT_CELLS+1)*(HEIGHT_CELLS+1));

		UpdateTerrainVisualMap(p);
		UpdateTerrainHeightMap(p);

#ifdef TEXHEAP
		// [FL-3838] Periodic GL flush during bulk terrain loading.
		// macOS Metal/OpenGL silently drops glTexSubImage2D data when
		// thousands of integer texture uploads (GL_R16UI) accumulate
		// without a sync point. Flushing every 64 patches forces the
		// Metal command encoder to commit pending operations.
		if (TerrainTexHeapEnabled() && (i & 63) == 63)
			glFlush();
#endif

		// [FL-3838] Probe: log p->ta status (GPU verify moved to post-load)
		if (debug)
		{
#ifdef TEXHEAP
			printf("[FL-3838] LOAD patch=(%d,%d) ta=%s\n",
				pch.x, pch.y, p->ta ? "non-null" : "NULL");
			fflush(stdout);
#endif
		}

		p->diag = pch.diag;

		if (debug)
		{
			if (!coords_init)
			{
				min_x = max_x = pch.x;
				min_y = max_y = pch.y;
				coords_init = true;
			}
			else
			{
				min_x = pch.x < min_x ? pch.x : min_x;
				max_x = pch.x > max_x ? pch.x : max_x;
				min_y = pch.y < min_y ? pch.y : min_y;
				max_y = pch.y > max_y ? pch.y : max_y;
			}
			fprintf(stderr, "[LoadTerrain] Patch %u: complete\n", i);
			fflush(stderr);
		}
	}

	if (debug)
	{
		fprintf(stderr, "[LoadTerrain] All %u patches loaded\n", hdr.num_patches);
		if (coords_init)
		{
			fprintf(stderr, "[LoadTerrain] Coord range X=[%d,%d] Y=[%d,%d]\n",
				min_x, max_x, min_y, max_y);
		}
		fflush(stderr);
	}

#ifdef TEXHEAP
	// [FL-3838] Final sync: ensure all terrain texture uploads are fully
	// committed to GPU before rendering begins. Without this, the first
	// frame after LOAD_MAP may render stale (zero) texture data.
	if (TerrainTexHeapEnabled())
		glFinish();

	// [FL-3838] Post-load GPU verification: fresh readback after glFinish
	{
		const char* verify_env = getenv("ASCIICKER_GPU_VERIFY");
		if (verify_env && *verify_env && TerrainTexHeapEnabled())
		{
			Patch** all_patches = 0;
			int num_patches = 0;
			GetAllTerrainPatches(t, &all_patches, &num_patches);

			int total_match = 0, total_mismatch = 0, total_skip = 0;
			uint16_t first_mm_cpu = 0, first_mm_gpu = 0;

			TexPage* last_page = 0;
			uint16_t* gpu_buf = 0;
			int pw = 0, ph = 0;

			for (int pi = 0; pi < num_patches; pi++)
			{
				Patch* p = all_patches[pi];
				if (!p->ta) { total_skip++; continue; }

				TexPage* page = p->ta->page;
				if (page != last_page)
				{
					free(gpu_buf);
					pw = t->th.cap_x * t->th.tex[1].item_w;
					ph = t->th.cap_y * t->th.tex[1].item_h;
					gpu_buf = (uint16_t*)malloc((size_t)pw * ph * sizeof(uint16_t));
					if (!gpu_buf) break;

					GLint prev_tex;
					glGetIntegerv(GL_TEXTURE_BINDING_2D, &prev_tex);
					glBindTexture(GL_TEXTURE_2D, page->tex[1]);
					glGetTexImage(GL_TEXTURE_2D, 0, GL_RED_INTEGER, GL_UNSIGNED_SHORT, gpu_buf);
					glBindTexture(GL_TEXTURE_2D, prev_tex);
					last_page = page;
				}

				int bx = p->ta->x * t->th.tex[1].item_w;
				int by = p->ta->y * t->th.tex[1].item_h;
				bool ok = true;
				for (int vy = 0; vy < VISUAL_CELLS && ok; vy++)
					for (int vx = 0; vx < VISUAL_CELLS && ok; vx++)
					{
						uint16_t cv = p->visual[vy][vx];
						uint16_t gv = gpu_buf[(by + vy) * pw + (bx + vx)];
						if (cv != gv)
						{
							ok = false;
							if (total_mismatch == 0) { first_mm_cpu = cv; first_mm_gpu = gv; }
						}
					}

				if (ok) total_match++; else total_mismatch++;
			}

			free(gpu_buf);
			free(all_patches);

			printf("[FL-3838] POST_LOAD_VERIFY: %d MATCH, %d MISMATCH, %d SKIP (of %d patches)\n",
				total_match, total_mismatch, total_skip, num_patches);
			if (total_mismatch > 0)
				printf("[FL-3838] POST_LOAD_VERIFY: first mismatch cpu=0x%04X gpu=0x%04X\n",
					first_mm_cpu, first_mm_gpu);
			fflush(stdout);
		}
	}
#endif

	if (idx)
		*idx = index;

	return t;
}

void FreePatchIndex(PatchIndex* idx)
{
	if (idx)
		free(idx);
}

// Helper to recursively collect patches
static void CollectPatchesRecursive(Node* n, int level, Patch*** out, int* count, int* cap) {
    if (!n) return;
    if (level == 0) {
        if (*count >= *cap) {
            *cap = (*cap == 0) ? 16 : (*cap * 2);
            *out = (Patch**)realloc(*out, sizeof(Patch*) * (*cap));
        }
        (*out)[(*count)++] = (Patch*)n;
    } else {
        for (int i=0; i<4; i++) {
            if (n->quad[i]) 
                CollectPatchesRecursive((Node*)n->quad[i], level-1, out, count, cap);
        }
    }
}

void GetAllTerrainPatches(Terrain* t, Patch*** out_patches, int* out_count) {
    if (!t || !t->root) {
        *out_patches = 0;
        *out_count = 0;
        return;
    }

    int cap = 16;
    int count = 0;
    Patch** patches = (Patch**)malloc(sizeof(Patch*) * cap);

    if (t->level == 0) {
        patches[count++] = (Patch*)t->root;
    } else {
        CollectPatchesRecursive((Node*)t->root, t->level, &patches, &count, &cap);
    }
    
    *out_patches = patches;
    *out_count = count;
}
