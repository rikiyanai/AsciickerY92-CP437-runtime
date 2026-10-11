//==============================================================================
// UNDO/REDO PUBLIC API
//==============================================================================
// PURPOSE: Public API for editor undo/redo system
//
// API FUNCTIONS:
//
//   URDO_Undo(max_depth): Undo last operation or entire group. Cannot call
//     while group is open. max_depth controls granularity (0=one leaf, 64=all).
//
//   URDO_Redo(max_depth): Redo next undone operation. Cannot call while group
//     is open. max_depth controls granularity.
//
//   URDO_Open(): Start a new operation group. All subsequent URDO_* calls until
//     URDO_Close() are grouped as one undo unit. Purges redo chain if top-level
//     group (divergent history).
//
//   URDO_Close(): End current operation group. Seals grouped operations. Deletes
//     group if empty (no operations added).
//
//   URDO_CanUndo() / URDO_CanRedo(): Query whether undo/redo is available.
//     Returns false if group is open or no operations in history.
//
//   URDO_Bytes(): Total memory used by undo/redo history (for UI display).
//
//   URDO_Purge(): Free all undo/redo history (reset to empty state).
//
// BRACKET SEMANTICS:
//
//   URDO_Open();
//     URDO_PatchUpdateHeight(patch);  // Grouped
//     URDO_PatchUpdateVisual(patch);  // Grouped
//   URDO_Close();
//   // User hits Ctrl+Z -> BOTH operations undone atomically
//
// EDITOR OPERATION FUNCTIONS (terrain/instance specific):
//
//   URDO_PatchCreate / URDO_Delete(Terrain*, Patch*): Create/delete terrain patch
//   URDO_PatchUpdateHeight / URDO_PatchUpdateVisual: Snapshot before editing
//   URDO_PatchDiag: Snapshot before flipping diagonal
//   URDO_Create / URDO_Delete(Inst*): Create/delete mesh/sprite instance
//
// Tags: [FLOW:EDITOR] Undo/redo API consumed by asciiid.cpp editor
//==============================================================================

#pragma once

#include  <stdint.h>
#include "terrain.h"
#include "world.h"
#include "inventory.h"

bool URDO_CanUndo();
bool URDO_CanRedo();

size_t URDO_Bytes();

void URDO_Purge();

void URDO_Undo(int max_depth);
void URDO_Redo(int max_depth);

// groupping
void URDO_Open();
void URDO_Close();

// patches

Patch* URDO_Create(Terrain* t, int x, int y, int z); // replacement for AddTerrainPatch
void URDO_Delete(Terrain* t, Patch* p); // replacement for DelTerrainPatch

void URDO_Patch(Patch* p, bool visual = false); // call before changing height map
void URDO_Diag(Patch* p); // call before flipping diag
void URDO_Material(int material_id); // snapshot one material shade table before editing
void URDO_Palette(int palette_id); // snapshot one palette RGB table before editing

// meshes & sprites (instances)

Inst* URDO_Create(World* w, Item* item, int flags, float pos[3], float yaw, int story_id);
Inst* URDO_Create(World* w, Sprite* s, int flags, float pos[3], float yaw, int anim, int frame, int reps[4], int story_id); // replacement for CreateInst
Inst* URDO_Create(Mesh* m, int flags, double tm[16], int story_id); // replacement for CreateInst
void URDO_Delete(Inst* i); // replacement for DeleteInst
