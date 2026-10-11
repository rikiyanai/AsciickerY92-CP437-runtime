// =============================================================================
// World/BSP System — Instance Management, Mesh Resources, Spatial Queries
// =============================================================================
//
// PURPOSE:
// Public API for the game world: a container of Mesh resources and Inst (Instance)
// placements organized in a BSP tree for efficient frustum culling and raycasting.
// Instances can reference static Mesh geometry (.akm files), animated Sprites, or
// inventory Items. The BSP tree is rebuilt on demand via RebuildWorld().
//
// KEY TYPES:
// - World: Opaque root container (BSP tree + mesh library + instance lists).
//          Full definition in world.cpp includes BSP node types (NODE, NODE_SHARE,
//          LEAF, INST) and the Mesh/MeshInst/SpriteInst/ItemInst structs.
// - Mesh:  Opaque shared geometry resource loaded from .akm (PLY-based) files.
//          Multiple Inst can reference the same Mesh.
// - Inst:  Opaque instance placed in the world with position, transform, flags.
//          Three variants internally: MeshInst, SpriteInst, ItemInst.
// - QueryWorldCB: Callback struct for frustum-culled world traversal, with
//                 separate mesh and sprite callbacks.
//
// INCLUDED BY:
// - terrain.h (forward declaration for UpdateTerrainDark shadow pass)
// - render.h / render.cpp (world rendering pipeline stage 3)
// - physics.h             (world collision / raycasting)
// - asciiid.cpp           (editor: instance placement and manipulation)
// - game.h / game.cpp     (game-time instance management)
// - game_svr.cpp          (server-side world state)
// - urdo.h                (undo/redo uses SoftInstAdd/SoftInstDel/HardInstDel)
// - enemygen.cpp          (enemy instance creation and hit testing)
//
// RELATIONSHIP TO world.cpp:
// This header exposes only opaque pointers and the public API. The internal
// BSP tree structure (BSP_TYPE_NODE/NODE_SHARE/LEAF/INST), Mesh face/vertex
// storage, instance variant structs, and file I/O details are all in
// world.cpp (5000+ lines).
//
// FILE FORMAT:
// SaveWorld/LoadWorld use the .a3d binary format. Meshes are referenced by
// name/path string; the caller must ensure .akm files are available at load time.
// =============================================================================

#pragma once

// WHY forward declarations (not #include): World, Mesh, Inst are opaque types
// whose full definitions live in world.cpp. Headers that need these types only
// need pointer declarations, avoiding pulling in the large internal structs.
struct World;
struct Mesh;
struct Inst;
struct Sprite;
struct Item;
struct MinimapMarker;

World* CreateWorld();
void DeleteWorld(World* w);
// [FLOW:WORLD] Reconstructs the BSP tree from all INST_USE_TREE instances.
// WHY boxes param: When true, creates BSP nodes using instance bounding boxes
// instead of centroids, producing tighter spatial partitioning at higher build cost.
void RebuildWorld(World* w, bool boxes = false);

// -----------------------------------------------------------------------------
// Mesh Resource Management
// -----------------------------------------------------------------------------
// [DATA-CONTRACT:AKM] Meshes are loaded from .akm files (PLY format with vertex
// colors). The path string is stored and used for re-loading and serialization.
Mesh* LoadMesh(World* w, const char* path, const char* name = 0);
Mesh* FindOrLoadMesh(World* w, const char* path, const char* name = 0);  // O(n) scan — deduplicates by name, linear in mesh count
void DeleteMesh(Mesh* m);
bool ResolveMeshAssetPath(char* out, int out_size, const char* base_path, const char* mesh_name);

bool IsMaterialUsedInWorld(World* w, int mat_id);

// [DATA-CONTRACT:AKM] Re-reads geometry from .akm file, replacing existing faces.
bool UpdateMesh(Mesh* m, const char* path);

// WHY linked-list traversal API: Meshes form a doubly-linked list in World for
// sequential iteration (editor mesh browser, material auditing). Not indexed
// because mesh count is typically small (<100) and insertion/deletion is frequent.
Mesh* GetFirstMesh(World* w);
Mesh* GetLastMesh(World* w);
Mesh* GetPrevMesh(Mesh* m);
Mesh* GetNextMesh(Mesh* m);

// WHY cookie: Opaque user data pointer for editor UI (e.g., linking a Mesh to
// its tree widget node without modifying the Mesh struct).
void* GetMeshCookie(Mesh* m);
void  SetMeshCookie(Mesh* m, void* cookie);

World* GetMeshWorld(Mesh* m);
int GetMeshName(Mesh* m, char* buf, int size);
void GetMeshBBox(Mesh* m, float bbox[6]);

int GetMeshFaces(Mesh* m);
// [FLOW:RENDER] Iterates all faces in a mesh, calling cb with vertex positions
// (3x3 floats), vertex colors (3x4 bytes RGBA), and visual material ID.
void QueryMesh(Mesh* m, void (*cb)(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie), void* cookie);
int QueryMeshLimited(Mesh* m, int max_callbacks,
	void (*cb)(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie),
	void* cookie);

// -----------------------------------------------------------------------------
// Instance Management (Static Mesh & Sprite Placements)
// -----------------------------------------------------------------------------
// WHY three CreateInst overloads: Internally creates different instance subtypes
// (ItemInst, SpriteInst, MeshInst) with type-specific data. The overload
// resolution selects the correct internal allocation and BSP insertion path.
Inst* CreateInst(World* w, Item* item, int flags, float pos[3], float yaw, int story_id);
Inst* CreateInst(World* w, Sprite* s, int flags, float pos[3], float yaw, int anim, int frame, int reps[4], const char* name, int story_id);
// [DATA-CONTRACT:AKM] MeshInst stores a 4x4 transform matrix (column-major double[16]).
Inst* CreateInst(Mesh* m, int flags, const double tm[16], const char* name, int story_id);
void DeleteInst(Inst* i);

// WHY story_id: Links instances to the game plot/scripting system. Each placed
// object can be referenced by script via its story_id for quest triggers,
// dialogue, and item interactions.
// for GAMEPLOT/EDITOR
void SetInstStoryID(Inst* i, int id);

World* GetInstWorld(Inst* i);

Mesh* GetInstMesh(Inst* i);
int GetInstFlags(Inst* i);
void SetInstFlags(Inst* i, int flags);
int GetInstStoryID(Inst* i);
const char* GetInstName(Inst* i);
// WHY double tm[16]: 4x4 column-major transform matrix. Uses double precision
// because world coordinates can be large (terrain extends thousands of units)
// and single-float would accumulate visible jitter at distance.
bool GetInstTM(Inst* i, double tm[16]);
void SetInstTM(Inst* i, const double tm[16]);
void GetInstBBox(Inst* i, double bbox[6]);


// [FLOW:RENDER] Sprite instance update/query -- sprites are billboard instances
// with animation state (anim, frame) and palette remapping (reps[4]).
void UpdateSpriteInst(World* world, Inst* i, Sprite* sprite, const float pos[3], float yaw, int anim, int frame, const int reps[4]);
Sprite* GetInstSprite(Inst* i, float pos[3], float* yaw, int* anim, int* frame, int reps[4]);
bool SetInstSpriteData(Inst* i, void* data);
void* GetInstSpriteData(Inst* i);

Item* GetInstItem(Inst* i, float pos[3], float* yaw);
typedef void (*WorldItemInstCB)(Inst* inst, Item* item, const float pos[3], float yaw, int story_id, void* cookie);
void QueryWorldItems(World* w, WorldItemInstCB cb, void* cookie);

int AnimateSpriteInst(Inst* i, uint64_t stamp);

void ShowInst(Inst* i);
void HideInst(Inst* i);

// WHY bit flags (not enum class): Flags are combined with bitwise OR and tested
// with bitwise AND throughout the codebase. Plain enum allows this without casts.
enum INST_FLAGS
{
    INST_VISIBLE = 0x1,    // WHY 0x1: Instance is rendered (hidden instances skip query callbacks)
    INST_USE_TREE = 0x2,   // WHY 0x2: Instance participates in BSP tree (vs. flat list for volatile objects)
	INST_VOLATILE = 0x4,   // WHY 0x4: Instance is temporary (NPCs, projectiles) -- skipped by editor save
	INST_SELECTED = 0x8    // WHY 0x8: Editor selection highlight flag
};

// new
// void QueryWorld(World* w, int planes, double plane[][4], void(*cb)(Sprite* s, float pos[3], float yaw, int anim, float frame, void* cookie), void* cookie);

// [FLOW:RENDER] Callback struct for frustum-culled world traversal.
// WHY separate mesh_cb and sprite_cb: Meshes and sprites have fundamentally
// different rendering paths (mesh: transform matrix + face iteration; sprite:
// billboard positioning + animation frame lookup). Splitting callbacks avoids
// runtime type checks in the hot render loop.
struct QueryWorldCB
{
	void(*mesh_cb)(Inst* i, Mesh* m, double tm[16], void* cookie);
	void(*sprite_cb)(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie);
	// FL-2957 attempt #24: early-exit callback for BSP traversal.
	// When non-NULL, Query checks this before each child recursion and flat-list
	// step. Return false to abort traversal immediately. NULL = always continue
	// (backward compatible — existing callers zero-initialize trailing fields).
	// LINEAGE_JSON: {"fl":"FL-2957","attempt":24,"commit":"pending","attempt_total":24,"closed":0,"what":"BSP early-exit via should_continue callback on QueryWorldCB","result":"pending"}
	bool(*should_continue)(void* cookie);
	// FL-2957: optional query center for nearest-first BSP child ordering.
	// When non-NULL, Query visits the child whose bbox center is closer to
	// this point first, filling the soup with nearby geometry before the
	// early-exit cap fires. NULL = default traversal order.
	const double* query_center;
};

// [FLOW:RENDER] Frustum-culled BSP traversal -- calls mesh_cb/sprite_cb for visible instances.
void QueryWorld(World* w, int planes, double plane[][4], QueryWorldCB* cb, void* cookie);
// [FLOW:RENDER] Debug BSP visualization -- calls cb with each BSP node's level and bbox.
void QueryWorldBSP(World* w, int planes, double plane[][4], void (*cb)(int level, const float bbox[6], void* cookie), void* cookie);
int CollectMeshInsts(World* w, Inst*** out);


// [FLOW:RENDER] Raycasting against all world instances.
// TODO(PIPELINE-FIX): positive_only=false allows hits behind the ray origin,
// which can produce unexpected results if the ray origin is inside geometry.
struct HitFilter
{
    bool skip_volatile;   // if true, skip INST_VOLATILE instances
    bool solid_only;      // if true, skip transparent/passthrough geometry
    bool sprites_too;     // if false, skip sprite billboard hit testing
    HitFilter(bool skip_volatile = false, bool solid_only = false, bool sprites_too = true)
        : skip_volatile(skip_volatile), solid_only(solid_only), sprites_too(sprites_too) {}
};
Inst* HitWorld(World* w, double p[3], double v[3], double ret[3], double nrm[3], bool positive_only = false, const HitFilter& filter = HitFilter(), uint8_t* out_color = 0);

// [DATA-CONTRACT:A3D] Serializes world state (mesh references + instance data) to binary.
bool SaveWorld(World* w, FILE* f);

// [DATA-CONTRACT:A3D] Deserializes world from .a3d binary.
// Runtime entrypoint: loads items directly for gameplay.
World* LoadWorldRuntime(FILE* f);
// Editor entrypoint: clones items so the editor can mutate copies without affecting originals.
World* LoadWorldForEditor(FILE* f);
bool WorldGetPlayerStart(World* w, float pos[3], float* yaw = 0, float* dir = 0);
void WorldSetPlayerStart(World* w, const float pos[3], float yaw, float dir);

// [DATA-CONTRACT:A3D] Embedded minimap markers live after enemy generators in
// newer .a3d files. Older files may omit this section entirely.
bool SaveMinimapMarkers(FILE* f);
void LoadMinimapMarkers(FILE* f);
void FreeMinimapMarkers();
MinimapMarker* GetFirstMinimapMarker();
MinimapMarker* GetNextMinimapMarker(MinimapMarker* marker);
const char* GetMinimapMarkerName(MinimapMarker* marker);
const char* GetMinimapMarkerLabel(MinimapMarker* marker);
float GetMinimapMarkerX(MinimapMarker* marker);
float GetMinimapMarkerY(MinimapMarker* marker);
uint8_t GetMinimapMarkerFg(MinimapMarker* marker);
uint8_t GetMinimapMarkerGlyph(MinimapMarker* marker);
uint8_t GetMinimapMarkerType(MinimapMarker* marker);

void PurgeItemInstCache();
void PurgeWorldItemInsts(World* w);
void ResetItemInsts(World* w);

// WHY AttachInst: Instances start in a flat linked list (no BSP). This function
// attempts to insert an instance into the BSP tree for faster spatial queries.
// Returns false if the BSP tree needs rebuilding first.
bool DetachInst(World* w, Inst* i);
bool AttachInst(World* w, Inst* i); // tries to move from flat list to bsp
bool DetachInst(World* w, Inst* i); // removes from bsp back to flat list

// WHY Soft/Hard distinction: SoftInstAdd/Del modify BSP tree linkage without
// freeing memory (reversible for undo). HardInstDel permanently frees the instance.
// TODO(PIPELINE-FIX): Calling SoftInstDel then HardInstDel on the same instance
// is required for correct cleanup. Calling HardInstDel alone on a BSP-linked
// instance will leave dangling BSP pointers.
// undo/redo only!!!
void SoftInstAdd(Inst* i);
void SoftInstDel(Inst* i);
void HardInstDel(Inst* i);

#ifdef EDITOR
// WHY EDITOR guard: HitSprite is only needed for the editor's enemy generation
// tool, which tests ray hits against individual sprite frames for placement
// validation. Game runtime uses HitWorld which handles sprites internally.
// used in editor for enemy gen hit testing
bool HitSprite(Sprite* sprite, int anim, int frame, float pos[3], float yaw, double p[3], double v[3], double ret[3], bool positive_only);
#endif
