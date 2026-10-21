// engine/render/authoring_ports_v1.h
// Neutral authoring/workspace ports, interface version 1.
//
// PURPOSE:
// Single build-connected boundary between shared/presenter code and the
// FL-4512 authoring implementation (resolver install/config/counters,
// profile scoring predicates, global glyph-path resolution, profile
// workspace). Presenter and tool code reach every authoring duty ONLY
// through the AuthoringPortV1 slots below; no consumer TU may include a
// product path. Implementations stay product-side.
//
// CONTRACT:
// - Header-only, neutral: pragma once, stdint/stddef/stdbool only, struct
//   definitions only. The shared vocabulary structs below moved verbatim
//   from the product headers (identical field order and types); product
//   headers include this file and declare (and define) the functions.
// - Every slot is nullable. Callers MUST null-check the table pointer AND
//   each slot before use. The AsciickerAuthoring shorthand lazy-installs
//   like the other port shorthands (declared in renderer_ports_v1.h).
// - Additive: slots are appended, never renumbered or reordered.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

// Per-material profile. Verbatim from
// apps/fl4512-runtime/engine/fl4131_harri_resolver.h: same struct used by
// the editor sliders and the production runtime.

// FL-4512 U4 ProfileKeyV1. Verbatim from
// apps/fl4512-runtime/engine/fl4512_render_product_profiles.h: rendering
// profiles are qualified by surface namespace; the numeric material key is
// never a complete profile identity. Values stay stable because GPU
// textures, traces, U9 plans, and receipts serialize them directly. Plain
// C enum (no fixed underlying type) so C and C++ consumers share it.
enum Fl4512ProfileNamespaceV1 {
    FL4512_PROFILE_NAMESPACE_NONE = 0,
    FL4512_PROFILE_NAMESPACE_TERRAIN = 1,
    FL4512_PROFILE_NAMESPACE_MESH = 2,
    FL4512_PROFILE_NAMESPACE_COUNT = 3
};

struct ProfileKeyV1 {
    uint8_t profile_namespace;
    int16_t material_key;
};

// Pure key makers, moved verbatim (no product state, no I/O).
static inline struct ProfileKeyV1 Fl4512MakeProfileKey(
    uint8_t profile_namespace, int material_key)
{
    const struct ProfileKeyV1 key = { profile_namespace,
        (int16_t)material_key };
    return key;
}

static inline int Fl4512FlattenProfileKey(struct ProfileKeyV1 key)
{
    if ((key.profile_namespace != FL4512_PROFILE_NAMESPACE_TERRAIN &&
         key.profile_namespace != FL4512_PROFILE_NAMESPACE_MESH) ||
        key.material_key < 0 || key.material_key >= 256)
        return -1;
    return (key.profile_namespace - FL4512_PROFILE_NAMESPACE_TERRAIN) * 256 +
        key.material_key;
}

static inline struct ProfileKeyV1 Fl4512UnflattenProfileKey(int profile_index)
{
    if (profile_index < 0 || profile_index >= 512)
        return Fl4512MakeProfileKey(FL4512_PROFILE_NAMESPACE_NONE, -1);
    return Fl4512MakeProfileKey(
        (uint8_t)(FL4512_PROFILE_NAMESPACE_TERRAIN + profile_index / 256),
        profile_index % 256);
}

// Defaults for the profile above match the historical
// Fl4131RuntimeMatProfile values so the legacy
// assets/a3d/fl4131_harri_mat_profiles.json schema keeps parsing.
struct Fl4131HarriMatProfile
{
    bool  initialized;
    bool  allow_arabic;
    bool  allow_math;
    bool  allow_shapes;
    bool  allow_box;
    bool  allow_punct;
    bool  allow_other;
    bool  allow_katakana;
    float min_density;
    float max_density;
};

// Resolver config. Verbatim from
// apps/fl4512-runtime/engine/fl4131_harri_resolver.h: role_weights and
// normalize flag are the only knobs that drift between editor and runtime.
struct Fl4131HarriResolverConfig
{
    float role_weights[6];
    int   normalize_src6;   // 1 = min/max-normalize before scoring, 0 = raw
    float range_floor;      // normalization is skipped when (hi - lo) < floor
};

// Live production counter snapshot. Verbatim from
// apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h: snapshot (not
// pointer access) so the editor cannot accidentally mutate resolver state.
struct Fl4131RuntimeHarriCounterSnapshot
{
    int pre_glyph_scene_rgba_seen;
    int eligible;
    int winner_count;
    int extended_winners;
    int cp437_default_cells;
    int fallback_cells;
    int frames_logged;
    int gpu_parity_seen;
    int cpu_gpu_disagree;
    int gpu_authoritative_overrides;
};

enum { FL4512_PROFILE_WORKSPACE_PATH_CAPACITY = 4096 };

// FL-4512 ABI-B18 workspace record. Verbatim from
// apps/fl4512-runtime/engine/fl4512_profile_workspace.h: configured exactly
// once before any profile loader, serializer, compiler transaction, reload,
// save, or rollback. Only the mutable profile/pool pair is rooted here.
struct Fl4512ProfileWorkspaceV1
{
    const char* schema_id;
    char root_path[FL4512_PROFILE_WORKSPACE_PATH_CAPACITY];
    const char* profile_relative_path;
    const char* compiled_pool_relative_path;
    char profile_path[FL4512_PROFILE_WORKSPACE_PATH_CAPACITY];
    char compiled_pool_path[FL4512_PROFILE_WORKSPACE_PATH_CAPACITY];
    char profile_before_sha256[65];
    char compiled_pool_before_sha256[65];
    char source_fixture_sha256[65];
    char global_atlas_sha256[65];
    char global_catalog_sha256[65];
    char global_registry_sha256[65];
    char global_scale_fixture_sha256[65];
    char semantic_workspace_sha256[65];
};

// Authoring ports: resolver lifecycle/config/counters, profile scoring,
// global glyph-path resolution, profile workspace, live pool/cell queries.
// Self-acquiring through AsciickerAuthoring (renderer_ports_v1.h); the
// linked product adapter installs the table when it owns this capability.
// Classic leaves it null and does not include this vocabulary (FL-4512).
struct AuthoringPortV1;

// Structural vertical-relation vocabulary. Verbatim values from
// apps/fl4512-runtime/engine/fl4512_render_product_profiles.h (single
// owner of the EDGE-route claim predicate lives product-side; only the
// constant vocabulary moves so mirrors test the same bounds).
enum {
    FL4260_VERTICAL_RELATION_UNKNOWN  = -1,
    FL4260_VERTICAL_RELATION_FLAT     = 0,
    FL4260_VERTICAL_RELATION_RISE     = 1,
    FL4260_VERTICAL_RELATION_FALL     = 2,
    FL4260_VERTICAL_RELATION_RIDGE    = 3,
    FL4260_VERTICAL_RELATION_VALLEY   = 4,
    FL4260_VERTICAL_RELATION_WALL     = 5,
    FL4260_VERTICAL_RELATION_OVERHANG = 6
};

// Resolver call ABI tags. Forward declarations only; definitions stay
// product-side. Consumers hold pointers (hook installs, observers).
struct GlyphResolverCellInput;
struct GlyphResolverCellResult;

// Compass direction/flow vocabulary. Verbatim values from
// apps/fl4512-runtime/engine/fl4512_render_product_profiles.h (claim
// predicates stay single-owner product-side; only the constants move).
enum {
    FL4260_DIRECTION_UNKNOWN = -1,
    FL4260_DIRECTION_N  = 0,
    FL4260_DIRECTION_NE = 1,
    FL4260_DIRECTION_E  = 2,
    FL4260_DIRECTION_SE = 3,
    FL4260_DIRECTION_S  = 4,
    FL4260_DIRECTION_SW = 5,
    FL4260_DIRECTION_W  = 6,
    FL4260_DIRECTION_NW = 7
};

enum {
    FL4260_FLOW_UNKNOWN = -1,
    FL4260_FLOW_N  = 0,
    FL4260_FLOW_NE = 1,
    FL4260_FLOW_E  = 2,
    FL4260_FLOW_SE = 3,
    FL4260_FLOW_S  = 4,
    FL4260_FLOW_SW = 5,
    FL4260_FLOW_W  = 6,
    FL4260_FLOW_NW = 7
};

// Mesh-feature and role vocabulary. Verbatim values from
// apps/fl4512-runtime/engine/fl4512_render_product_profiles.h (claim
// predicates stay single-owner product-side; only the constants move).
enum {
    FL4512_MESH_FEATURE_UNKNOWN           = -1,
    FL4512_MESH_FEATURE_SILHOUETTE        = 0,
    FL4512_MESH_FEATURE_CREASE            = 1,
    FL4512_MESH_FEATURE_MATERIAL_BOUNDARY = 2
};

enum Fl4512SelectedRoleV1 {
    FL4512_ROLE_NONE = 0,
    FL4512_ROLE_MESH_FEATURE = 1,
    FL4512_ROLE_EDGE = 2,
    FL4512_ROLE_DIRECTION = 3,
    FL4512_ROLE_FLOW = 4,
    FL4512_ROLE_FILLED = 5,
    FL4512_ROLE_TONAL = 6
};

// Single shared per-cell PROFILE result. Verbatim from
// apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h: one bundled
// read-only result per terrain cell (glyph/marker outcome, origin, ramp,
// density). Origin/absence taxonomies stay product-side; only this plain
// data carrier moves so presenter diagnostics read it neutrally.
struct Fl4260PerCellResult
{
    unsigned int  glyph_id;
    int           source_state;
    int           marker_class;
    unsigned char fg;
    unsigned char bg;
    bool          fg_bg_is_marker;
    int           material_id;
    int           ramp;
    int           density;
    int           absence_state;
    const char*   absence_reason;
};

// Pool-route key vocabulary. Verbatim values from
// apps/fl4512-runtime/engine/fl4512_render_product_profiles.h (routing
// stays single-owner product-side; only the constants move).
enum Fl4512PoolRouteKeyV1 {
    FL4512_POOL_ROUTE_NONE = 0,
    FL4512_POOL_ROUTE_MESH_BUNDLE = 1,
    FL4512_POOL_ROUTE_EDGE = 2,
    FL4512_POOL_ROUTE_DIRECTION = 3,
    FL4512_POOL_ROUTE_FLOW = 4,
    FL4512_POOL_ROUTE_FILLED_FIELD = 5,
    FL4512_POOL_ROUTE_TONAL_RAMP_DENSITY = 6
};

struct CompiledProgramUpdateV1;

struct AuthoringPortV1
{
    // 0 = uninstalled (zero table: every slot null, fail closed).
    // 1 = installed by the linked product adapter.
    uint32_t version;
    bool (*resolver_install)(void);
    bool (*resolver_installed)(void);
    void (*set_resolver_config)(const float role_weights6[6],
        int normalize_src6, float range_floor);
    void (*get_counter_snapshot)(
        struct Fl4131RuntimeHarriCounterSnapshot* out);
    uint64_t (*get_profile_revision)(void);
    void (*profile_set_defaults)(struct Fl4131HarriMatProfile* profile);
    bool (*profile_allows_repertoire)(
        const struct Fl4131HarriMatProfile* profile, const char* repertoire);
    float (*density_from_shape6)(const float shape6_norm[6]);
    float (*weighted_squared_l2)(const float source6[6],
        const float glyph6[6], const float role_weights6[6]);
    // Global glyph-path resolution against the immutable executable-derived
    // root. out must hold FL4512_PROFILE_WORKSPACE_PATH_CAPACITY bytes.
    bool (*resolve_global_glyph_path)(const char* repo_relative_path,
        char* out);
    const char* (*global_shape_catalog_path)(void);
    const char* (*global_atlas_registry_path)(void);
    const char* (*global_page16_path)(void);
    const char* (*global_aspect_authoring_path)(void);
    const char* (*global_mesh_asset_metric_path)(void);
    const char* (*global_aspect_golden_path)(void);
    const char* (*global_projected_scale_golden_path)(void);
    bool (*configure_profile_workspace)(const char* root_path,
        const char* global_glyph_root_path, char* reason_out,
        size_t reason_size);
    const struct Fl4512ProfileWorkspaceV1* (*get_profile_workspace)(void);
    const char* (*workspace_profile_path)(void);
    const char* (*workspace_compiled_pool_path)(void);
    // Live profile queries (per-material gate + active color). Thin
    // product calls; the policy lives product-side.
    bool (*profile_live)(struct ProfileKeyV1 key);
    bool (*get_active_profile_color)(struct ProfileKeyV1 key, int elv,
        int shade, uint8_t* out_fg, uint8_t* out_bg);
    // Live pool/cell queries for editor proof panels. Thin product calls;
    // the data lives product-side.
    bool (*any_profile_live)(void);
    bool (*get_live_profile_ramp_density_counts)(struct ProfileKeyV1 key,
        int* out_ramp, int* out_density, unsigned int* out_first_gid);
    int (*get_live_bucket_pools)(struct ProfileKeyV1 key,
        unsigned int* out_gids, float* out_shape6, int max_count,
        int* bucket_offset, int* bucket_len);
    void (*get_per_cell_result)(int terrain_mat_id, int elv_quantized,
        int shade_quantized, const float src6[6],
        struct Fl4260PerCellResult* out);
    // Opaque product-owned transaction. Successful publication consumes the
    // handle; on failure the caller discards it. Invoke between native frames.
    struct CompiledProgramUpdateV1* (*prepare_compiled_program)(
        const char* receipt_path, char* reason_out, size_t reason_size);
    bool (*publish_compiled_program)(struct CompiledProgramUpdateV1* update);
    void (*discard_compiled_program)(struct CompiledProgramUpdateV1* update);
    const char* (*workspace_active_program_path)(void);
};
