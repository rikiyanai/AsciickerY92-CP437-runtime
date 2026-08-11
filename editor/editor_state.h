// editor_state.h — Editor state and document state structs
//
// PURPOSE: Consolidate editor-wide state (tool mode, prefs, cursor) and
// document state (current map path, save errors) into typed structs.
//
// MIGRATION: FL-2785 — extract from file-scoped static globals in asciiid.cpp.
// EditorState holds per-session tool/Ui state (mode, brush, view).
// EditorDocumentState holds per-file document state (path, error).
//
// SEE ALSO:
// - editor/asciiid.cpp — consumer, globals being migrated here

#pragma once

#include <stdint.h>

#include "terrain.h"
#include "world.h"
#include "sprite.h"

// ── Editor document state (per-file) ──

struct EditorDocumentState
{
    // Current map file path (empty = no document loaded)
    char current_map_path[4096];

    // Last save error message (empty = no error)
    char last_save_map_error[256];

    // Startup map path (passed via command line)
    char startup_map_path[1024];

    // Startup sprite path (passed via command line)
    char startup_sprite_path[1024];

    // Whether to start in viewer mode
    bool startup_viewer_mode;

    // Whether to start in sprite browser mode
    bool startup_sprite_browser;

    // Whether MCP (external control) mode is active
    bool mcp_mode;

    // Whether batch mode is active (process all stdin commands sync, no render loop)
    bool batch_mode;
};

// ── Editor UI/tool state (per-session) ──

struct EditorState
{
    // Current editing mode (0=Sculpt, 1=Mat-id, 2=Mesh, 3=Diag, 4=Sprite, 5=Item, 6=EnemyGen)
    int edit_mode = 0;

    // Brush settings (Sculpt/MAT-id modes)
    // FL-3714: these must be nonzero before the first UI frame.  A zero
    // br_radius makes sculpt-drag stamping use a zero stride and can hang.
    float br_radius = 20.0f;
    int brush_shape = 0;       // 0=Gaussian, 1=Square, 2=Noise
    float br_alpha = 0.10f;    // brush strength (positive=raise, negative=lower)
    float br_tile_radius = 1.0f;  // radius for multi-tile creation/deletion (patches)
    bool br_limit = false;         // limit brush to specific height

    // Painting state
    int creating = 0;
    int painting = 0;
    int painting_x = 0;
    int painting_y = 0;

    // Probe state (Ctrl+Shift+Click terrain height sample)
    int probe_z = 0;

    // Current story id
    int story_id = -1;

    // Diagonal flip tracking
    bool diag_flipped = false;

    // Enemy generator prefs
    int eg_alive_max = 1;
    int eg_revive_min = 0;
    int eg_revive_max = 0;
    int eg_armor = 0;
    int eg_helmet = 0;
    int eg_shield = 0;
    int eg_sword = 0;
    int eg_crossbow = 0;

    // Spinning/cursor state
    int spinning = 0;
    int spinning_x = 0;
    int spinning_y = 0;
};

// ── Global instances ──

extern EditorDocumentState g_editor_document;
extern EditorState g_editor_state;
