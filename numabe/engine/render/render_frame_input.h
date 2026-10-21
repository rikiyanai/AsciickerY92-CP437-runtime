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
// FL-4208 N2.0: the GPU bridge carries a presentation-neutral per-cell terrain
// surface identity (Fl4208SurfaceId) so the TERM++ temporal-measurement probe can
// match a cell to the SAME world surface across frames (screen-independent), which
// is the prerequisite for measuring hysteresis. The header is #pragma once and
// only pulls stdint, so embedding the 24-byte value type by value is safe.
#include "fl4208_surface_identity.h"

struct Human;
struct Character;
struct Inst;
struct Sample;
struct Renderer;
struct Sprite;

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

enum {
    FL4260_CELL_FACT_VERTICAL_RELATION = 1u << 0,
    FL4260_CELL_FACT_DIRECTION         = 1u << 1,
    FL4260_CELL_FACT_FLOW              = 1u << 2
};

struct RenderProjectileLine
{
	float from[3];
	float to[3];
	uint64_t spawn_stamp;
	uint16_t item_definition_id;
	uint8_t active;
};

enum { RENDER_MAX_RAPTURES = 16 };

// Render-only anchor derived from a server-owned death tick and lifted pose.
// It is never a world object and has no physics, collision, or targeting role.
struct RenderRapture
{
	float ground[3];
	uint32_t ticks_since_death;
	uint32_t seed;
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

// FL-4131 / FL-4206 / FL-4207 Step 5: source-cell shape6 sampling hook.
// RenderStageResolve fills shape6_internal + external10 from the canonical
// PRE_GLYPH_SCENE_RGBA buffer plus its luma/depth/material/shade/dispatch
// planes (built once per frame by the Current pre-glyph scene owner and
// invoked through the neutral build_pre_glyph slot). The hook receives
// harri_scene_* pointers and reads the same six-region geometry the glyph
// catalog uses. If harri_scene_rgba is NULL the canonical source is missing;
// the caller does not invoke this hook with substitute data. U6 later presents
// a named missing-authority tuple for a live profile cell. The hook then
// asks an external resolver (ASCIIID editor today, runtime resolver
// companion later) to pick a GlyphId from the manifest-covered shape-lab catalog.
// The resolver writes the chosen id back into 'out'. The resolve stage stages
// that identity for the shared P7 final-tuple owner; neither presenter may
// reconstruct or mutate the selection.
// FL-4131 S2 (2026-06-03): dispatch source classifier for per-surface
// accounting. Identifies which game render path produced the resolver
// dispatch so the runtime hook can count terrain vs auto-material mesh
// vs sprite/actor dispatch independently. UNKNOWN_VISUAL is the
// fail-closed sentinel: any caller that forgets to tag the dispatch
// shows up as `unknown_*` in the receipt instead of being mis-bucketed.
// TERRAIN_MATERIAL and AUTOMAP_MESH_RGB are the only profile-scoped owners.
// WATER and ANSI_OVERLAY are explicit non-profile owners: water synthesis and
// post-world UI composition must retain their authored identities instead of
// inheriting a buried terrain/mesh decision. SPRITE_ACTOR is likewise counted
// separately and remains outside profile selection so canonical actors do not
// get consumed by the compiled selector.
enum {
    GLYPH_DISPATCH_UNKNOWN_VISUAL   = 0,
    GLYPH_DISPATCH_TERRAIN_MATERIAL = 1,
    GLYPH_DISPATCH_AUTOMAP_MESH_RGB = 2,
    GLYPH_DISPATCH_SPRITE_ACTOR     = 3,
    GLYPH_DISPATCH_WATER            = 4,
    GLYPH_DISPATCH_ANSI_OVERLAY      = 5,
};

// FL-4131 GPU bridge (current-only mutable diagnostic state) lives in its
// current-runtime owner. This neutral header defines no bridge struct and
// declares no bridge instance; Classic parses this header.

struct RenderDebugHooks
{
    void (*after_terrain_stage)(Renderer* renderer, void* user);
    // FL-4257 ownership diagnostic (observational only): fired after each S3
    // raster stage so a probe can attribute every winning sample to the stage
    // that wrote it. image: 0 near, 1 antipodal; stage: 0 terrain, 1 world
    // (meshes/sprites), 2 water globe. Null in normal runs.
    void (*s3_after_stage)(Renderer* renderer, int image, int stage, void* user);
    void (*after_reflection_stage)(Renderer* renderer, void* user);
    void (*water_pass_cell)(Renderer* renderer, const WaterPassCellDump* dump, void* user);
    // Candidate and counterfactual renders set this to suppress process-global
    // diagnostic initialization plus trace-file publication. The isolated
    // renderer still computes full per-cell identities in its owned buffers.
    bool isolated_observation;
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
    uint32_t authoritative_tick;
    RenderRapture raptures[RENDER_MAX_RAPTURES];
    int rapture_count;
    Sprite* ufo_sprite;
    RenderDebugHooks debug_hooks;
};
