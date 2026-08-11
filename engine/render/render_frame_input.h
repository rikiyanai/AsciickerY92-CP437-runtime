// render_frame_input.h — Narrow render-side input structs
//
// PURPOSE:
// Defines the per-frame data that the renderer needs from game state.
// This replaces direct prime_game->debug.* and server->* access in
// render_scene.cpp and render_sprite_blit.cpp.
//
// OWNERSHIP:
// - RenderFrameInput: populated by game_render_bridge.cpp before ::Render()
// - RenderFrameReport: render-owned output in render_frame_report.h
//
// SEE ALSO: render_internal.h (stores frame_input pointer on Renderer)

#pragma once

#include <stdint.h>

struct Human;
struct Character;
struct Inst;
struct Renderer;

// ── Frame inputs (game → render) ──

struct RenderTrackingTargets
{
    int tracked_remote_pid;      // dbg_remote0_pid, -1 if none
    int tracked_npc_entity_id;   // dbg_tracked_npc0_entity_id, -1 if none
};

struct RenderRemoteRosterView
{
    const Human* others;        // server->authority.others
    int max_clients;            // server->connection.max_clients
    const Character* local_player; // &prime_game->player
};

struct SnapshotNpcRenderView
{
    // Flat arrays from ServerSnapshotNpcRepository — render reads inst,
    // entity_id, hp, max_hp, presentation_kind_id, life_state.
    struct NpcVisualSlot
    {
        uint16_t entity_id;
        uint16_t presentation_kind_id;
        Inst* inst;
        int16_t hp;
        int16_t max_hp;
    };
    struct NpcStateSlot
    {
        uint16_t entity_id;
        uint8_t life_state;
    };
    NpcVisualSlot visuals[64];
    NpcStateSlot npcs[64];
    int npc_count;
};

struct MinimapNpcDot
{
    float pos[2];
    uint8_t is_enemy;   // ch->enemy
    uint8_t has_data;   // ch->data != nullptr
};

struct MinimapRemoteDot
{
    float pos[2];
    uint8_t alive;      // life_state != DEAD
};

enum { RENDER_MAX_PROJECTILE_LINES = 16 };

struct RenderProjectileLine
{
	float from[3];
	float to[3];
	uint64_t spawn_stamp;
	uint16_t item_definition_id;
	uint8_t active;
};

// WaterPassCellDump — per-cell data captured at the water-mutation boundary
// inside RenderStageResolve. The hook is called for every output cell that
// reaches the water-line / underwater-Perlin decision point.
struct WaterPassCellDump
{
    int cell_x;
    int cell_y;
    int sx;  // sample buffer x = 2*cell_x + 2
    int sy;  // sample buffer y = 2*cell_y + 2

    // Four 2×2 sub-sample facts before water mutation.
    uint16_t sample_visuals[4];
    uint8_t  sample_diffuses[4];
    uint8_t  sample_spares[4];
    float    sample_heights[4];

    // Output AnsiCell state right before the water decision.
    uint8_t before_fg;
    uint8_t before_bk;
    uint8_t before_gl;
    uint8_t before_spare;

    // Output AnsiCell state after the water decision (may equal before).
    uint8_t after_fg;
    uint8_t after_bk;
    uint8_t after_gl;
    uint8_t after_spare;

    // Renderer water-plane z (float, as passed to RenderStageResolve).
    float water_level;

    // ― Water-line path (spare & 0x40) ―
    int linecase;   // 4-bit case from the four src[].spare & 0x40 bits

    // ― Underwater Perlin path ―
    bool   perlin_path;            // true when all 4 samples are below water
    double perlin_input_x;         // world x passed to octaveNoise0_1
    double perlin_input_y;         // world y
    double perlin_input_time;      // r->pn_time
    double perlin_value;           // raw octaveNoise0_1 return
    int    water_id;               // (int)(perlin_value * 5) - 2, clamped

    bool   mutation_applied;
    char   mutation_reason[64];    // fixed-size owner tag
};

struct RenderDebugHooks
{
    void (*after_terrain_stage)(Renderer* renderer, void* user);
    void (*after_reflection_stage)(Renderer* renderer, void* user);
    void (*water_pass_cell)(Renderer* renderer, const WaterPassCellDump* dump, void* user);
    void* user;
};

struct RenderFrameInput
{
    RenderTrackingTargets tracking;
    RenderRemoteRosterView remote;
    SnapshotNpcRenderView snapshot_npcs;
    bool valid;                              // false when no game/server context

    // Minimap / debug seam (FL-2726 / FL-2920)
    bool auto_shot_enabled;
    MinimapNpcDot npc_dots[64];
    int npc_dot_count;
    MinimapRemoteDot remote_dots[64];
    int remote_dot_count;
    RenderProjectileLine projectile_lines[RENDER_MAX_PROJECTILE_LINES];
    int projectile_line_count;
    RenderDebugHooks debug_hooks;
};
