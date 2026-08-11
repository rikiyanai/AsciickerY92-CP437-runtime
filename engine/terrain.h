// =============================================================================
// Terrain System — Quadtree Patch Management, Height/Visual Data, Spatial Queries
// =============================================================================
//
// PURPOSE:
// Public API for the quadtree-based terrain system. Terrain is composed of
// discrete Patch tiles, each containing a HEIGHT_CELLS+1 x HEIGHT_CELLS+1
// vertex heightmap and a VISUAL_CELLS x VISUAL_CELLS material/visual grid.
// Patches are stored in a dynamically-expanding quadtree for O(log N) spatial
// lookups, frustum culling, and raycasting.
//
// KEY TYPES:
// - Terrain: Opaque root container (quadtree + world-space offset + stats).
//            Full definition lives in terrain.cpp (QuadItem/Node/Patch hierarchy).
// - Patch:   Opaque leaf node holding height[], visual[], diag bitfield, and
//            optional dark (shadow) and TexAlloc (GPU texture) data.
// - PatchIndex: Flat array entry mapping (x,y) coords to a Patch pointer,
//               built during LoadTerrain for editor patch selection.
//
// INCLUDED BY:
// - render.h / render.cpp (terrain rendering pipeline stages 2+4)
// - world.h / world.cpp   (world references terrain for shadow updates)
// - physics.h             (terrain collision / ground height queries)
// - asciiid.cpp           (editor: terrain editing tools)
// - game.h / game_svr.cpp (game-time terrain queries)
// - urdo.h                (undo/redo uses Detach/Attach/Dispose)
// - enemygen.cpp          (enemy placement on terrain surface)
//
// RELATIONSHIP TO terrain.cpp:
// This header exposes only opaque pointers and the public API. The internal
// QuadItem/Node/Patch structs, quadtree traversal, BSP raycasting variants
// (HitTerrain0-7), and file I/O format details are all in terrain.cpp (3300+ lines).
//
// FILE FORMAT:
// SaveTerrain/LoadTerrain use the .a3d binary format with "AS3D" magic.
// See terrain.cpp for the FileHeader (16 bytes) + FilePatch (188 bytes each) layout.
// =============================================================================

#pragma once

#include <stdio.h>
#include "world.h"

// WHY DARK_TERRAIN: Enables per-patch 64-bit shadow/occlusion bitmask (8x8 cells,
// 1 bit each). Used by the render pipeline to darken terrain cells that are in
// shadow from world geometry. Compile-time toggle because shadow computation is
// expensive and not needed for all build targets (e.g. headless server).
#define DARK_TERRAIN

// WHY HEIGHT_SCALE 16: Each visual character cell spans 16 discrete z-steps in
// the heightmap. This ratio controls the vertical resolution of terrain geometry
// relative to the horizontal grid. Changing this value breaks existing .a3d files.
// [DATA-CONTRACT:A3D]
#define HEIGHT_SCALE 16 // how may z-steps produces 1 visual cell

// WHY HEIGHT_CELLS 4: Each patch has a 5x5 vertex grid (HEIGHT_CELLS+1 per axis),
// forming a 4x4 quad grid for collision/physics triangulation. This is deliberately
// coarser than VISUAL_CELLS to keep raycasting cheap while visual detail is higher.
// [DATA-CONTRACT:A3D]
#define HEIGHT_CELLS 4 // num of verts -1 along patch X and Y axis

// WHY VISUAL_CELLS 8: Each patch has an 8x8 material grid for rendering. This is
// 2x the height grid resolution, allowing finer material detail (grass/sand/rock
// boundaries) without increasing collision geometry complexity.
// [DATA-CONTRACT:A3D]
#define VISUAL_CELLS 8

#ifdef TEXHEAP
// WHY this formula: Computes how many patches fit in a single 1024-entry texture
// page. Uses max(VISUAL_CELLS, HEIGHT_CELLS+1) because both visual and height data
// are uploaded to the GPU texture heap, and the larger dimension determines the
// per-patch slot size.
// TODO(PIPELINE-FIX): This capacity calculation assumes VISUAL_CELLS >= HEIGHT_CELLS+1
// in practice (8 > 5), but the ternary guard exists for safety. If these constants
// change, verify GPU upload offsets in render.cpp still align.
#define TERRAIN_TEXHEAP_CAPACITY (1024 / (VISUAL_CELLS > HEIGHT_CELLS+1 ? VISUAL_CELLS : HEIGHT_CELLS+1))
#endif

struct Terrain;

// WHY z=-1 default: -1 means create an empty terrain with no initial patch.
// Any non-negative z creates a single patch at origin with that base height.
Terrain* CreateTerrain(int z=-1);
void DeleteTerrain(Terrain* t);

// WHY base offset: The quadtree's internal coordinate system can shift as the
// tree expands. Base tracks the world-space origin of the tree root so that
// patch coordinates can be translated to/from absolute world coordinates.
void GetTerrainBase(Terrain* t, int b[2]);
void SetTerrainBase(Terrain* t, const int b[2]);

// [DATA-CONTRACT:A3D] Writes terrain to .a3d binary format ("AS3D" magic).
// [FLOW:WORLD] Called by editor save and game state serialization.
bool SaveTerrain(const Terrain* t, FILE* f);

struct Patch;

// WHY PatchIndex: Provides a flat indexed view of all patches after loading,
// used by the editor for sequential patch iteration and selection UI.
// [DATA-CONTRACT:A3D]
struct PatchIndex
{
	int32_t x,y;
	Patch* patch;
};

// [DATA-CONTRACT:A3D] Reads terrain from .a3d binary format.
// WHY optional idx: When non-null, builds a PatchIndex array for editor use.
// Game runtime passes null (no index needed for gameplay).
Terrain* LoadTerrain(FILE* f, PatchIndex** idx = 0);
void FreePatchIndex(PatchIndex* idx);

// [FLOW:WORLD] Patch coordinate lookup (both directions).
Patch* GetTerrainPatch(Terrain* t, int x, int y);
void GetTerrainPatch(Terrain* t, Patch* p, int* x, int* y);

Patch* AddTerrainPatch(Terrain* t, int x, int y, int z);
bool DelTerrainPatch(Terrain* t, int x, int y);

// WHY dedicated to urdo: These bypass normal add/delete and manipulate the
// quadtree at a low level for undo/redo. Detach removes a patch from the tree
// without freeing it, Attach re-inserts it, Dispose frees a detached patch.
// Return value is byte count for memory tracking in the undo stack.
// TODO(PIPELINE-FIX): Using these outside the urdo system will corrupt the
// quadtree's internal node counts and height bounds propagation.
// don't  use, deticated to urdo only!
size_t TerrainDetach(Terrain* t, Patch* p, int* x, int* y);
size_t TerrainAttach(Terrain* t, Patch* p, int x, int y);
size_t TerrainDispose(Patch* p);

Patch* GetTerrainNeighbor(Patch* p, int sign_x, int sign_y);

// WHY raw pointer returns: Caller gets direct access to patch-internal arrays
// for in-place editing (editor height brush, material paint). After modification,
// caller MUST call UpdateTerrainHeightMap/UpdateTerrainVisualMap to propagate
// changes to GPU and quadtree bounds.
// TODO(PIPELINE-FIX): No bounds checking -- caller must respect array dimensions
// (HEIGHT_CELLS+1)^2 for height, (VISUAL_CELLS)^2 for visual.
uint16_t* GetTerrainHeightMap(Patch* p);
uint16_t* GetTerrainVisualMap(Patch* p);

// WHY ghost: Computes a preview heightmap row for a not-yet-existing patch,
// used by the editor to show terrain continuation at patch boundaries.
Patch* CalcTerrainGhost(Terrain* t, int x, int y, int z, uint16_t ghost[4 * HEIGHT_CELLS]);

// [FLOW:RENDER] Must be called after modifying height/visual data to upload
// changes to GPU and recalculate quadtree height bounds.
void UpdateTerrainHeightMap(Patch* p);
void UpdateTerrainVisualMap(Patch* p);

void GetTerrainLimits(Patch* p, uint16_t* lo, uint16_t* hi);

#ifdef TEXHEAP
// [FLOW:RENDER] GPU texture heap integration -- patches store their visual and
// height data in a shared GPU texture atlas managed by TexHeap (see texheap.h).
TexHeap* GetTerrainTexHeap(Terrain* t);
TexAlloc* GetTerrainTexAlloc(Patch* p);

// WHY TexPageBuffer: Batches VBO data references for an entire texture page so
// that all patches sharing a GPU texture page can be drawn in a single draw call.
// The prev/next pointers form a linked list of dirty pages needing re-upload.
// [FLOW:RENDER]
struct TexPageBuffer
{
	TexPage* prev;
	TexPage* next;
	int size;

	// WHY this array size: 5 GLints per patch slot (VBO offset, stride, etc.)
	// times max patches per page. 81920 bytes covers the worst case where every
	// slot in the page is occupied.
	// large enough to refer to the whole page content by vbo (81920 bytes)
	GLint data[5 * TERRAIN_TEXHEAP_CAPACITY];
};

#endif

int GetTerrainPatches(Terrain* t);
// New helper for testing
void GetAllTerrainPatches(Terrain* t, Patch*** out_patches, int* out_count);
size_t GetTerrainBytes(Terrain* t);

// WHY diag bitfield: 16-bit field controlling triangle diagonal orientation for
// each of the 4x4 height cells (1 bit per cell). Determines which diagonal is
// used when splitting each quad into two triangles for rendering/collision.
// [DATA-CONTRACT:A3D]
uint16_t GetTerrainDiag(Patch* p);
void SetTerrainDiag(Patch* p, uint16_t diag);

uint16_t GetTerrainHi(Patch* p, uint16_t* lo = 0);

#ifdef DARK_TERRAIN
// WHY uint64_t dark: 64 bits = 8x8 grid of 1-bit shadow flags, one per visual
// cell. Set bits indicate the cell is in shadow from world geometry casting
// against the light direction. Compact representation avoids per-cell struct overhead.
uint64_t GetTerrainDark(Patch* p);
void SetTerrainDark(Patch* p, uint64_t dark);
// [FLOW:RENDER] Recomputes shadow bitmasks for all patches by raycasting against
// world geometry. The editor variant uses PatchIndex for selective update.
void UpdateTerrainDark(Terrain* t, World* w, float lightpos[3], bool editor);
void UpdateTerrainDark(Terrain* t, PatchIndex* pi, World* w, float lightpos[3], bool editor);
// Selected-map native startup can defer the whole-map terrain-dark bootstrap so
// gameplay enters first and dark occlusion finishes incrementally in runtime.
void DeferTerrainDarkBootstrap(PatchIndex* idx, int patch_count, float lightpos[3], bool editor);
void CancelDeferredTerrainDarkBootstrap();
bool StepDeferredTerrainDarkBootstrap(Terrain* t, World* w, int patch_budget);
bool StepDeferredTerrainDarkBootstrap(Terrain* t, World* w, int patch_budget, uint32_t max_us);
#endif

// [FLOW:RENDER] Frustum/radius culled iteration -- calls cb for each visible patch.
// WHY two overloads: radius query (circular area around player) for gameplay,
// plane query (view frustum planes) for rendering.
struct QueryTerrainCB
{
	void(*patch_cb)(Patch* p, int x, int y, int view_flags, void* cookie);
	bool(*should_continue)(void* cookie);
};

void QueryTerrain(Terrain* t, double x, double y, double r, int view_flags, void(*cb)(Patch* p, int x, int y, int view_flags, void* cookie), void* cookie);
void QueryTerrain(Terrain* t, int planes, double plane[][4], int view_flags, void (*cb)(Patch* p, int x, int y, int view_flags, void* cookie), void* cookie);
void QueryTerrain(Terrain* t, int planes, double plane[][4], int view_flags, QueryTerrainCB* cb, void* cookie);

// [FLOW:RENDER] Raycasting -- returns hit patch and intersection point.
// WHY ret[4]: xyz position + distance parameter t in the 4th element.
// TODO(PIPELINE-FIX): nrm default 0 (null pointer) means normal is not computed
// unless caller explicitly provides storage. Callers that need surface normal
// for lighting must pass a valid double[3].
Patch* HitTerrain(Terrain* t, double p[3], double v[3], double ret[4], double nrm[3]=0, bool positive_only = false);

double HitTerrain(Patch* p, double u, double v); // u,v must be normalized
