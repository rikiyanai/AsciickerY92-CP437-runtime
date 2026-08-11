// render_internal.h — Internal render struct definitions
//
// PURPOSE: Shared type definitions for engine/render.cpp and split slices.
// Contains internal struct definitions (Sample, SampleBuffer, SpriteRenderBuf,
// Renderer) with their inline method bodies.
//
// SEE ALSO: render.h (public API), render.cpp (implementations)

#pragma once

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "render.h"
#include "render_frame_input.h"
#include "render_frame_report.h"
#include "sprite.h"
#include "sprite_constants.h"
#include "terrain.h"
#include "world.h"
#include "PerlinNoise.hpp"

#define DBL

#ifdef __cplusplus

// Shared RGB555 -> terminal material lookup used by the resolve pass after the
// render split. This must stay visible to both render_scene.cpp and
// render_world_pass.cpp.
extern uint8_t auto_mat[32 * 32 * 32 * 3];

struct Character;

struct Sample
{
    uint16_t visual;
    uint8_t diffuse;
    uint8_t spare;
    float height;

    inline bool DepthTest_RO(float z)
    {
        return height <= z + HEIGHT_SCALE / 2;
    }
};

struct SampleBuffer
{
    int w, h;
    Sample* ptr;
};

struct SpriteRenderBuf
{
    Sprite* sprite;
    int s_pos[3];
    int angle;
    int anim;
    int frame;
    int reps[4];
    float dist;
    uint8_t alpha;
    bool refl;
    bool tracked_remote;
    bool tracked_npc;
    bool is_local_player;
    int16_t remote_pid;    // slot index in others[], -1 if not a remote
    Character* character;
    uint8_t render_order_bias;
    uint8_t character_clr;   // Character::clr cached at queue time
    int16_t npc_hp;
    int16_t npc_max_hp;
    uint16_t npc_presentation_kind_id;
    uint8_t npc_life_state;

    static int FarToNear(const void* a, const void* b);
};

struct SpriteBlitDiagnostics
{
    bool drew_any = false;
    int depth_pass_cells = 0;
    int depth_fail_cells = 0;
    int depth_fail_mesh_cells = 0;
    int depth_fail_mesh_samples = 0;
    int candidate_cells = 0;
    int water_reject_cells = 0;
    float candidate_min_z = 0.0f;
    float candidate_max_z = 0.0f;
    int reject_reason = 0; // 0 = none, 1-8 = see render_sprite_blit.cpp
    bool clip_reject = false;
    float center_terrain_height = 0.0f;
    bool has_center_terrain_height = false;
    float blit_pos_z = 0.0f;
    float water_plane_z = 0.0f;
    int clip_left = 0;
    int clip_right = 0;
    int clip_bottom = 0;
    int clip_top = 0;
    int unclipped_left = 0;
    int unclipped_right = 0;
    int unclipped_bottom = 0;
    int unclipped_top = 0;
    int frame_width = 0;
    int frame_height = 0;
    int ref_x = 0;
    int ref_y = 0;
    int screen_pos_x = 0;
    int screen_pos_y = 0;
};

struct Renderer
{
    void Init()
    {
        memset(this, 0, sizeof(Renderer));
        pn.reseed(std::default_random_engine::default_seed);
    }

    void Free()
    {
        if (sample_buffer.ptr)
            free(sample_buffer.ptr);
        if (sprites_alloc)
            free(sprites_alloc);
    }

    uint64_t stamp;
    siv::PerlinNoise pn;
    double pn_time;

    SampleBuffer sample_buffer; // render surface

    int sprites_alloc_size;
    int sprites;
    SpriteRenderBuf* sprites_alloc;

    uint8_t* buffer;
    int buffer_size; // ansi_buffer allocation size in cells (minimize reallocs)


    static void RenderPatch(Patch* p, int x, int y, int view_flags, void* cookie /*Renderer*/);
    static void RenderSprite(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie /*Renderer*/);
    static void RenderMesh(Inst* inst, Mesh* m, double* tm, void* cookie /*Renderer*/);
    static void RenderFace(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie /*Renderer*/);

    // unstatic -> needs R/W access to sample_buffer.ptr[].height for depth testing!
    SpriteBlitDiagnostics RenderSprite(AnsiCell* ptr, int width, int height, Sprite* s, bool refl, int anim, int frame, int angle, int pos[3]);

    // transform
    double mul[6]; // 3x2 rot part
    double add[3]; // post rotated and rounded translation
    float yaw, pos[3];
    float water;
    float light[4];
    bool int_flag;
    bool perspective;
    double inv_tm[16]; // for unproject

    // perspective test
    float view_dir[3];
    float view_pos[3];
    float view_ofs[2]; // dw/2 + shift[0]*2, dh/2 + shift[1]*2
    float focal;

    double viewinst_tm[16];
    const double* inst_tm;

    // Per-frame render-owned queue facts filled during the QueryWorld callback.
    RenderQueueReport queue_report;
    ActorFinalBodyReport actor_report;
    MaterialGlyphRenderReport material_glyph_report;

    // Per-frame input owned by game_render_bridge.cpp.
    const RenderFrameInput* frame_input;

    int patch_uv[HEIGHT_CELLS][2]; // constant
};

extern int render_break_point[2];
extern bool global_refl_mode;

#endif /* __cplusplus */
