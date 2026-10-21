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
#include "renderer_ports_v1.h"
#include "fl4208_surface_identity.h" // FL-4208 N0a: Fl4208ProvisionalId / Fl4208FrameTrace
#include "sprite.h"
#include "sprite_constants.h"
#include "terrain.h"
#include "world.h"
#include "PerlinNoise.hpp"
// FL-4257 Phase 1 — Renderer caches the server-authoritative curvature view.
// Server is the sole owner (Law 2). The renderer is a cached reader; the only
// legal writer is RenderSetCurvatureFromAuthoritative below. Construction sets
// the field to the canonical absent form so render fails closed (Law 6) until
// the authoritative-state JSON intake calls the setter.
#include "curvature.h"

#define DBL

#ifdef __cplusplus

// Shared RGB555 -> terminal material lookup used by the resolve pass after the
// render split. This must stay visible to both render_scene.cpp and
// render_world_pass.cpp.
extern uint8_t auto_mat[32 * 32 * 32 * 3];

static inline uint8_t RenderS3FlipGlyphY(uint8_t gl)
{
    switch (gl)
    {
        case '^': return 'v';
        case 'v': return '^';
        case 'V': return '^';
        case '/': return '\\';
        case '\\': return '/';
        case ',': return '`';
        case '`': return ',';
        case 220: return 223; // lower half -> upper half
        case 223: return 220; // upper half -> lower half
        case 218: return 192; // box upper-left -> lower-left
        case 191: return 217; // box upper-right -> lower-right
        case 192: return 218;
        case 217: return 191;
        default: return gl;
    }
}

struct Character;


struct Sample
{
    uint16_t visual;
    uint8_t diffuse;
	uint8_t spare;
	uint8_t glyph_dispatch_surface;
	uint8_t s3_parity;
	int16_t material_id;
	float height;
    // FL-4208 N0a (round-3 finding #4 + #6): per-frame interned index into the
    // Fl4208SurfaceId side table, written in the same depth-winning branch as
    // visual/height. 0 = INVALID (no terrain identity / overflow). Presentation
    // does NOT read this in N0a — it is dormant identity instrumentation.
    uint32_t surface_id_index;

    inline bool DepthTest_RO(float z)
    {
        return height <= z + HEIGHT_SCALE / 2;
    }
};
// Renderer-neutral material identity carried by every sample. Both Classic
// CP437 and the product adapter consume this normalized upstream contract.
// Moved delete-first out of the product resolver policy header: shared TUs
// need it without including a product path.
static inline int RenderSampleMaterialId(const Sample& sample)
{
	return sample.material_id >= 0 && sample.material_id < 256
		? (int)sample.material_id : -1;
}
// Sprite composition field operations, shared by Classic composition and the
// product attribution recorder (ops cross the seam as bytes; the numeric
// parity with the product replay ABI is asserted product-side). Moved
// delete-first out of render_sprite_blit.cpp: the product TU needs the
// enumerators without including a shared .cpp. Values stable.
enum SpriteCompositionFieldOpV1 : uint8_t {
	SPRITE_COMPOSITION_FIELD_UNKNOWN = 0,
	SPRITE_COMPOSITION_FIELD_BASE_FG = 1,
	SPRITE_COMPOSITION_FIELD_BASE_BK = 2,
	SPRITE_COMPOSITION_FIELD_SOURCE_FG = 3,
	SPRITE_COMPOSITION_FIELD_SOURCE_BK = 4,
	SPRITE_COMPOSITION_FIELD_BASE_AVERAGE = 5,
	SPRITE_COMPOSITION_FIELD_SOURCE_AVERAGE = 6,
	SPRITE_COMPOSITION_FIELD_LIGHTEN_BASE_AVERAGE = 7,
	SPRITE_COMPOSITION_FIELD_LIGHTEN_BASE_FG = 8,
	SPRITE_COMPOSITION_FIELD_LIGHTEN_BASE_BK = 9
};
// FL-4208 N0a (round-3 finding #6): the surface_id_index lands here, so the
// size gate lands here too. 12 -> 16 bytes (the +4 interned-index layout, chosen
// over embedding the 24-byte struct).
static_assert(sizeof(Sample) == 16, "FL-4208: Sample must be 16 bytes (material owner + uint32 surface_id_index)");

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
    // FL-4257 finding 5 — arc-proportional sprite scale (arc/sin(arc),
    // clamped): 1 on the Euclidean baseline; grows past the sight equator.
    uint8_t s3_scale;
    uint8_t s3_parity;
    uint8_t character_clr;   // Character::clr cached at queue time
    uint8_t rapture_dissolve; // 0..255 body dissolve amount
    bool rapture_body;        // dead player in rapture window; suppress HP bar
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
		// CreateRenderer value-initializes the complete object. Whole-object
		// memset is forbidden because Renderer owns non-trivial C++ containers.
        pn.reseed(std::default_random_engine::default_seed);
		environment_animation_enabled = 1;
        // FL-4257 Phase 1 — explicit canonical-absent init. memset would leave
        // curvature.kind=0 (Euclidean), but the canonical absent form has
        // kind=NONE so grep traces of "render has no server curvature yet"
        // resolve to one place. present=0 makes the projection branch fall
        // through to the Euclidean baseline (Law 6 fail closed).
        curvature = AsciCurvatureViewAbsent();
		// FL-4512 seam: Current runtime state is created lazily through the
		// hook table (create slot) on first frame use, never here: shared
		// Init must not install hooks or complete the Current type.
		product_runtime_state = nullptr;
    }

    // FL-4257 Phase 1 — sole legal writer of `curvature`. Server is the
    // authority (Law 2); the renderer is a cached reader, not an owner. This
    // setter takes the same (present_wire, kind) wire shape that
    // engine/curvature.h documents so the JSON decoder cannot route through
    // any path that bypasses AsciCurvatureViewFromIntake's strict validation.
    // present_wire != 1 OR unrecognised kind both fail closed to absent.
    void SetCurvatureFromAuthoritative(uint8_t present_wire, uint8_t kind)
    {
        curvature = AsciCurvatureViewFromIntake(present_wire, kind);
    }

    // FL-4257 corrective — published-wire setter (RSP_JOIN payload: derived
    // physical kappa + chart anchor). This is the runtime intake; the
    // (present, kind) intake above remains the legacy/diagnostic adapter.
    void SetCurvatureFromPublished(uint8_t present_wire, uint8_t kind,
                                   float kappa, const float anchor[3])
    {
        curvature = AsciCurvatureViewFromPublished(present_wire, kind, kappa, anchor);
    }

    void SetS3AuthoritativePose(uint8_t valid, const double ground[3],
                                const double east[3], float height, float yaw)
    {
        s3_pose_valid = valid ? 1 : 0;
        if (ground)
            memcpy(s3_pose_ground, ground, sizeof(s3_pose_ground));
        else
            memset(s3_pose_ground, 0, sizeof(s3_pose_ground));
        if (east)
            memcpy(s3_pose_east, east, sizeof(s3_pose_east));
        else
            memset(s3_pose_east, 0, sizeof(s3_pose_east));
        s3_pose_height = height;
        s3_pose_yaw = yaw;
    }

    // FL-4257 — client-owned spherical camera VIEW (presentation only; NOT
    // gameplay state). SOLE writer of s3_first_person / s3_free_pitch.
    // game_render_bridge.cpp calls this every frame from the client CameraState
    // so s3_first_person is never read uninitialised by the camera builder.
    // Body facing (yaw) is server-authoritative via SetS3AuthoritativePose and
    // the first-person camera renders THAT yaw — first-person horizontal look is
    // a body-yaw intent (CameraState::s3_look_yaw_delta) handled in the local
    // player authority, not a render offset. Only pitch is presentation here.
    void SetS3CameraView(int first_person, float free_pitch)
    {
        s3_first_person = first_person ? 1 : 0;
        s3_free_pitch = free_pitch;
    }

    void Free()
    {
        if (sample_buffer.ptr)
            free(sample_buffer.ptr);
        if (sprites_alloc)
            free(sprites_alloc);
        // FL-4208 N0a correction (reviewer flag #3): the provisional plane and
        // per-cell trace capture are owned here and were leaking at teardown.
        if (fl4208_prov)
            free(fl4208_prov);
        if (fl4208_cell_ids)
            free(fl4208_cell_ids);
        if (fl4208_cell_state)
            free(fl4208_cell_state);
        if (fl4208_cell_reason)
            free(fl4208_cell_reason);
		// FL-4512 seam: the Current module owns every allocation behind the
		// opaque state (pre-glyph planes, tone sidecar, projected features,
		// sequence/counterfactual rows, stroke store/events/repro rows,
		// session, reachability). Destroy through the CACHED table only:
		// teardown must never install hooks. Classic observes a zero table
		// and a null pointer, so this block is a no-op there by construction.
		if (product_runtime_state)
		{
			const AsciickerProductPortsV1* ports = AsciickerProductPortsCached();
			const RendererProductV1* rt = ports ? ports->renderer : NULL;
			if (rt && rt->destroy)
				rt->destroy((AsciickerProductStateV1*)product_runtime_state);
			product_runtime_state = nullptr;
		}
    }

    uint64_t stamp;
    siv::PerlinNoise pn;
    double pn_time;
	uint8_t environment_animation_enabled;

    SampleBuffer sample_buffer; // render surface

    // FL-4208 N0a (presentation-neutral): provisional terrain-identity plane,
    // sized to sample_buffer (fl4208_prov_cap = dw*dh). BlendVR writes a
    // provisional identity here per terrain Sample; the post-raster identity
    // pass interns the FINAL survivors. NOT read by presentation. fl4208_world_gen
    // is cached per frame from GetWorldGeneration(w); fl4208_trace accumulates
    // per-frame stats when tracing is enabled.
    struct Fl4208ProvisionalId* fl4208_prov;
    int                         fl4208_prov_cap;
    uint64_t                    fl4208_world_gen;
    struct Fl4208FrameTrace     fl4208_trace;
    // FL-4588 candidate-render isolation: surface identities belong to this
    // Renderer instance. A private counterfactual render must not clear and
    // repopulate the live renderer's per-frame identity table.
    Fl4208IdentityInterner      fl4208_interner;
    // FL-4208 N0a correction (reviewer flag #4): per-cell identity capture for
    // the trace. Aggregate counts alone cannot drive the N0b ANCHOR_BINS sweep —
    // that needs the full per-cell Fl4208SurfaceId so two bin settings can be
    // compared cell-for-cell. Allocated/written only when trace is enabled
    // (sized width*height); state[i] = 0 empty/non-terrain, 1 valid, 2 rejected.
    struct Fl4208SurfaceId*     fl4208_cell_ids;
    uint8_t*                    fl4208_cell_state;
    uint8_t*                    fl4208_cell_reason;
    int                         fl4208_cell_cap;
    uint64_t                    fl4208_frame;       // monotonic frame index for trace receipts
    // FL-4208 N2.0 (reviewer fix): fl4208_frame is POST-incremented inside
    // Fl4208ResolveCellIdentities, so after that pass r->fl4208_frame already holds
    // the NEXT frame's number. The identity grid captured this pass belongs to the
    // PRE-increment value. We stash that exact captured value here so downstream
    // consumers (the N2.0 bridge copy in RenderStageResolve, which runs AFTER the
    // identity pass) record the grid's TRUE capture frame, not next frame. This is
    // the SAME value the trace receipt writes (render_scene.cpp Fl4208WriteTraceFrame).
    uint64_t                    fl4208_capture_frame; // frame the current identity grid was captured on
    // ---------------------------------------------------------------------
    // NEUTRAL PRODUCT SEAM (v1)
    // The product runtime state lives in a product adapter module
    // (AsciickerProductStateV1, completed product-side). The shared Renderer
    // carries only this opaque bridge pointer: null until a product adapter
    // claims it, destroyed in Free() through the cached renderer table
    // (never install-from-Free). Shared TUs never complete the type, never
    // install hooks, and never touch product fields except through the
    // renderer port slots.
    void* product_runtime_state;
    uint64_t              mesh_instance_uid;
    uint32_t              mesh_face_index;

    int sprites_alloc_size;
    int sprites;
    SpriteRenderBuf* sprites_alloc;

    uint8_t* buffer;
    int buffer_size; // ansi_buffer allocation size in cells (minimize reallocs)


    static void RenderPatch(Patch* p, int x, int y, int view_flags, void* cookie /*Renderer*/);
    static void RenderSprite(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie /*Renderer*/);
    static void RenderMesh(Inst* inst, Mesh* m, double* tm, void* cookie /*Renderer*/);
    static void RenderFace(float coords[9], uint8_t colors[12], float normals[9],
		uint8_t normal_source, uint32_t visual, void* cookie /*Renderer*/);

    // unstatic -> needs R/W access to sample_buffer.ptr[].height for depth testing!
    SpriteBlitDiagnostics RenderSprite(AnsiCell* ptr, int width, int height, Sprite* s, bool refl, int anim, int frame, int angle, int pos[3], uint8_t rapture_dissolve = 0);

    // transform
    double mul[6]; // 3x2 rot part
    double add[3]; // post rotated and rounded translation
    float yaw, pos[3];
    float water;
    float light[4];
    bool int_flag;
    bool perspective;
    // Reflection phase belongs to this renderer. Candidate renders must not
    // toggle a process-global phase flag observed by the live renderer.
    bool reflection_mode;
    double inv_tm[16]; // for unproject

    // perspective test
    float view_dir[3];
    float view_pos[3];
    float view_ofs[2]; // dw/2 + shift[0]*2, dh/2 + shift[1]*2
    float focal;

    double viewinst_tm[16];
    const double* inst_tm;
	// FL-4512 U1: set once for each MeshInst before QueryMesh. RenderFace reads
	// this resolved instance MaterialKey; packed vertex RGB is appearance only.
	int16_t mesh_material_key;
	uint8_t mesh_projected_scale_authority_valid;
	uint32_t mesh_asset_metric_q24_8[3];
	char mesh_scale_asset_id[65];
	// Aspect fact ownership lives product-side (Fl4512RuntimeStateV1):
	// derived in World_MeshSubmitBegin, read by triangle/register and
	// contour paths. No product type is named here.
    RenderQueueReport queue_report;
    ActorFinalBodyReport actor_report;
    MaterialGlyphRenderReport material_glyph_report;


    // Per-frame input owned by game_render_bridge.cpp.
    const RenderFrameInput* frame_input;

    // FL-4257 Phase 1 — cached read of server-authoritative curvature view.
    // SOLE writer: SetCurvatureFromAuthoritative (above). Project/Unproject
    // branch on this field. When present=0 the projection takes the Euclidean
    // baseline path so absent server authority is byte-identical to Phase 0.
    AsciCurvatureView curvature;

    // FL-4257 — 4D spherical owner per-frame view state. Written ONLY by
    // RenderS3Setup (render_s3.cpp); s3_active: 0 Euclidean, 1 spherical,
    // 2 fail-closed (published-but-unrecognised curvature).
    int s3_active;
    int s3_image;
    double s3_R;
    double s3_anchor[3];
    int s3_pose_valid;
    double s3_pose_ground[3];
    double s3_pose_east[3];
    float s3_pose_height;
    float s3_pose_yaw;
    double s3_cam[4];
    double s3_right[4];
    double s3_up[4];
    double s3_fwd[4];
    double s3_focal;
    double s3_focal_y;
    double s3_ofs[2];
    mutable float s3_last_depth;
    // FL-4257 — per-frame observability counters (report lane, item 10).
    mutable unsigned s3_primary_count;
    mutable unsigned s3_antipodal_count;
    unsigned s3_water_count;
    // FL-4257 — projection rejection census (observational): why points were
    // refused by RenderS3ProjectEmbedded. Indexed by the gate that fired.
    mutable unsigned s3_rej_pole;      // direction undefined at (anti)pode
    mutable unsigned s3_rej_near;      // arc near-plane guard
    mutable unsigned s3_rej_gate[2];   // hemisphere gate, per image
    int s3_sprite_scale; // active sprite blit scale (composite loop sets per buf)
    int s3_sprite_parity; // active sprite vertical parity under S3 far-side fold
    int s3_mesh_parity; // active mesh instance vertical parity under S3 far-side fold
    // FL-4512 VS1/VS2: parity zero is a real S3 result only when this validity
    // byte proves the current RenderMesh invocation derived it successfully.
    // Both fields are cleared at invocation entry and exit.
    uint8_t s3_mesh_parity_valid;
    // The exact direct/reflection/S3 sheet for the current mesh submission.
    // INVALID means QueryMesh must not run, so no sample and no projected
    // feature can acquire a plausible identity from stale invocation state.
    uint8_t mesh_visibility_sheet;
    // Per-frame fail-closed census.  These fields observe rejection; they do
    // not repair, retry, and choose another sheet.
    uint32_t mesh_visibility_sheet_failure_count;
    RenderVisibilitySheetFailureV1 mesh_visibility_sheet_last_failure;
    int s3_mesh_mirror_y; // projected mesh-instance anchor for far-side silhouette flip
    int s3_first_person; // camera policy selector (0 orbit, 1 first person)
    float s3_free_pitch; // FL-4257 first-person look pitch (deg, +up; clamped in builder; presentation)

    int patch_uv[HEIGHT_CELLS][2]; // constant
};


extern int render_break_point[2];

#endif /* __cplusplus */
