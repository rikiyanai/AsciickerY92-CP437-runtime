// minimap_renderer.h — In-game minimap rendering module
//
// PURPOSE:
// Owns the runtime minimap renderer (top-right corner inset map),
// including terrain sampling, mesh footprint rasterization, marker
// label fitting, and the auto-shot proof capture helper.
//
// Previously these functions were split between game.cpp (helpers)
// and game_render_bridge.cpp (RenderMinimap), with extern forward
// declarations bridging the gap.

#pragma once

#include <stdint.h>

struct AnsiCell;
struct RenderFrameInput;
struct RenderFrameReport;
struct MinimapMarker;
struct Terrain;
struct World;

struct MinimapRenderer
{
    // ── Main minimap render ──
    // Draws the top-right minimap overlay: terrain background, mesh
    // building footprints, NPC/enemy dots, remote player dots, marker
    // labels with directional player indicator.
    static void Render(
        AnsiCell* ptr, int width, int height,
        float player_x, float player_y, float player_z,
        float player_dir,
        float yaw, float zoom,
        World* world,
        Terrain* terrain,
        float water_level,
        const RenderFrameInput* frame_input,
        RenderFrameReport* out_report = nullptr);

    // ── Auto-shot proof capture ──
    // Snaps the local player to the first non-generic minimap marker
    // position when auto-shot first-frame capture is enabled.
    // Returns true if the player was repositioned.
    // The caller (game_render_bridge) is responsible for writing the snapped
    // position back into Game::player.pos and PhysicsIO::pos.
    static bool PrimeAutoShotProofCapture(
        const RenderFrameInput* frame_input,
        float local_display_pos[3],
        float* local_display_dir);
};

// Utility: draw text directly into the AnsiCell buffer.
// Used by PaintLocalAuthorityHoldScreen (game.cpp) and MinimapRenderer.
void DrawMiniText(AnsiCell* ptr, int width, int height, int x, int y,
                  const char* text, uint8_t fg, uint8_t bk, int max_w);
