// engine/render/renderer_ports_v1.h
// Neutral product ports, interface version 1.
//
// PURPOSE:
// Single build-connected boundary between shared code (engine/, platform/)
// and product adapters. Product behavior is reached ONLY through the five
// nullable tables below; product state is carried ONLY as the opaque
// AsciickerProductStateV1 pointer. No shared TU may include a product path
// or complete the state type. Classic and FL4512 each install an explicit
// adapter (apps/classic-runtime, apps/fl4512-runtime); shared code holds
// no product-selection branch.
//
// CONTRACT:
// - Header-only, neutral: pragma once, stdint/stddef/stdbool plus the
//   verified leaf-neutral engine/glyph_id.h (stdint-only, no repo includes,
//   no cycle) and struct forward declarations only.
// - Every slot is nullable. Callers MUST null-check the bundle pointer, the
//   table pointer, AND each slot before use. An uninstalled product fails
//   closed through nulls.
// - Table accessors (pointer semantics; the bundle has ONE program-wide
//   instance, see storage below): AsciickerProductSetPortsV1 (installer
//   writes the bundle), AsciickerProductPortsCached (reads WITHOUT
//   installing; the only legal accessor from teardown), AsciickerProductHooks
//   (lazy: installs once on first use, then returns the bundle). Fetch once
//   per pass and never hold one across a Set.
// - Per-table shorthands AsciickerRenderer/Presentation/Inspection/Startup
//   lazily install and return one table or null, like AsciickerAuthoring.
// CHANGELOG (additive; slots never renumbered or reordered):
//   v1 2026-09-24: split of the 35-slot AsciickerProductRuntimeV1 into four
//     cohesive tables. Slot signatures unchanged; shared call sites cross
//     one table each. Tool launching lives in ProductStartupV1 (application
//     shell calls it; it is not a renderer operation).
//   v1 2026-09-24: RendererProductV1 appends resolve_single_sample_cell for
//     the shared no-color arm; both product tables install it.

#pragma once

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "../glyph_id.h" // VERIFIED leaf-neutral: stdint-only.

struct Renderer;
struct AnsiCell;
struct Sample;
struct Inst;
struct Mesh;
struct TermCellInspectionV1;
// FL-4512: optional capability. Renderer-only consumers must not compile
// authoring profile/config/workspace vocabulary through this Interface.
struct AuthoringPortV1;

// Opaque product runtime state. Completed ONLY in a product adapter module.
struct AsciickerProductStateV1;

// Product byte-sink callback: (data, len, bridge_frame, user). Implemented
// shared-side (terminal deposit shims); product serializers call it.
// Signature mirrors the product deposit contract exactly.
typedef void (*AsciickerCellDepositV1)(const char* data, size_t len,
    unsigned long long frame, void* user);

// Per-frame plain-data input. No product types, no pointers to product memory.
struct AsciickerProductFrameV1
{
    uint64_t frame_no;
    uint64_t world_gen;
    int w;
    int h;
};

// Renderer stage ports: lifecycle plus the resolve/scene/mesh/sprite/measure
// stages shared callers require. Every slot takes ONLY the opaque product
// state, shared value types (Renderer, AnsiCell, Sample, Inst, Mesh by
// forward declaration), coordinates, and plain data.
struct RendererProductV1
{
    // 0 = uninstalled (zero table: every slot null, fail closed).
    // 1 = installed by the linked product adapter.
    uint32_t version;
    AsciickerProductStateV1* (*create)(void);
    void (*destroy)(AsciickerProductStateV1*);
    void (*begin_frame)(AsciickerProductStateV1*, Renderer*,
        const AsciickerProductFrameV1*);
    // Resolve stage (render_resolve.cpp). The cell slots collapse the
    // former bundle+publish+dispatch sequences; relation computation,
    // canonical material, fact routing, and every cell paint live
    // product-side. Nonzero return means the product painted the cell;
    // shared code paints a fail-closed diagnostic otherwise.
    void (*resolve_pre_pass)(AsciickerProductStateV1*, Renderer*, int w, int h);
    uint8_t (*resolve_automap_cell)(AsciickerProductStateV1*, Renderer*,
        AnsiCell*, int cx, int cy, int w, int h, int mat_id, int has_material,
        uint8_t dispatch, int elv, int shd, const Sample* src, int stride,
        const uint8_t diffuses[4]);
    uint8_t (*resolve_terrain_cell)(AsciickerProductStateV1*, Renderer*,
        AnsiCell*, int cx, int cy, int w, int h, int mat_id, int has_material,
        uint8_t dispatch, int elv, int shd, const Sample* src, int stride,
        const uint8_t diffuses[4], int water_owned);
    void (*seal_world_cell)(AsciickerProductStateV1*, int idx, uint8_t fg,
        uint8_t bk, uint8_t gl, uint8_t spare, uint8_t valid);
    void (*publish_frame_carriers)(AsciickerProductStateV1*);
    // Scene frame drivers (render_scene.cpp): begin/end bracket the frame,
    // finalize runs post-world pre-resolve, pre-glyph runs post-identity
    // pre-resolve. World-frame close runs at bridge close sites.
    void (*finalize_projected_features)(AsciickerProductStateV1*, Renderer*);
    void (*build_pre_glyph)(AsciickerProductStateV1*, Renderer*, int w, int h);
    void (*close_world_frame)(const AnsiCell* cells, int w, int h, int stride);
    // Mesh stage (render_world_pass.cpp). Tone context storage lives
    // product-side: begin_face initializes it from the per-face inputs,
    // write_sample consumes it. Face callbacks run sequentially on one
    // thread, so one product-owned slot per state is exact. Normal-matrix
    // scratch lives product-side (computed in submit_begin from tm).
    void (*register_projected_triangle)(AsciickerProductStateV1*, Renderer*,
        const float coords[9], const int pv[3][4], const float normal[3],
        uint32_t prim_id);
    void (*mesh_submit_begin)(AsciickerProductStateV1*, Renderer*,
        const Inst* inst, const Mesh* mesh, const double tm[16]);
    void (*mesh_submit_end)(AsciickerProductStateV1*, Renderer*, const Mesh* mesh);
    void (*capture_stroke_reprojection)(AsciickerProductStateV1*, Renderer*);
    void (*mesh_tone_begin_face)(AsciickerProductStateV1*,
        const Sample* sample_base, float ambient, uint8_t normal_source);
    void (*mesh_tone_write_sample)(AsciickerProductStateV1*,
        const Sample* sample_base, Sample* s, int16_t mu);
    void (*mesh_tone_compute_vertex)(AsciickerProductStateV1*,
        const float normals[9], const float light[4], int s3_active,
        float mu_out[3], float tone_out[3]);
    // Sprite stage (render_sprite_blit.cpp). Ops/masks cross as bytes; the
    // product resolves attribution internally and self-acquires its state.
    void (*sprite_track_composition)(Renderer*, int x, int y, int w, int h,
        const AnsiCell* base, const AnsiCell* source, uint8_t fg_op,
        uint8_t bk_op, uint8_t fg_mask, uint8_t bk_mask);
    void (*sprite_dispatch_shape6)(Renderer*, AnsiCell* cell, int x, int y,
        int w, int h);
    // Pure sample measurement (render_resolve.cpp path selection). Null
    // product falls back to the caller-provided fallback / -1 (no material).
    uint8_t (*measure_dispatch_surface)(const Sample* src, int stride,
        uint8_t fallback);
    int (*measure_cell_material_vote)(const Sample* src, int stride);
    // Single-sample resolve (render_resolve.cpp no-color arm). The product
    // fills the cell straight from the material recipe plus its coverage
    // display. Nonzero return means the product painted the cell; shared
    // code paints a fail-closed diagnostic otherwise. Appended 2026-09-24;
    // earlier slots never renumbered or reordered.
    uint8_t (*resolve_single_sample_cell)(AsciickerProductStateV1*,
        Renderer*, AnsiCell*, int x, int y, int w, int h, int visual);
};

// Presentation backend ports: final-cell commit plus the bridge/atlas extent
// operations the terminal present path requires. Self-acquiring (no state
// param): shared call sites hold no renderer handle; the adapter reaches
// the installed product state internally.
struct PresentationBackendV1
{
    uint32_t version;
    void (*commit_presentation_cells)(AnsiCell* cells, GlyphId* final_glyphs,
        int w, int h);
    bool (*resolve_atlas_page_path)(const char* name, char* out_path,
        int out_cap);
    void (*bridge_reset_for_frame)(int w, int h);
    void (*bridge_upload_extent)(int w, int h, int* out_w, int* out_h);
    uint64_t (*bridge_frame_id)(void);
    // True when the product presents through the compiled atlas pipeline
    // (final identity plus LUT plus page textures, Unicode shader). False
    // selects the Classic font-sampler pipeline. Runtime query, never a
    // preprocessor branch: both pipelines compile in every product.
    bool (*presents_compiled_atlas)(void);
    // Web frame commit: commits the final tuple for a web-presented frame
    // through the product bridge owned adapter-side. The browser has no
    // final-glyph plane; the product owns commit state internally.
    void (*commit_web_frame)(AnsiCell* cells, int w, int h);
};

// Inspection ports: read-only cell inspection, bridge dump, cell trace, and
// honest readiness reporting. Self-acquiring like the presentation ports.
struct InspectionPortV1
{
    uint32_t version;
    int (*inspect_cell)(const AnsiCell* cells, const GlyphId* final_glyphs,
        int w, int h, int x, int y, TermCellInspectionV1* out);
    int (*dump_bridge_cells)(const char* path, int term_w, int term_h,
        AsciickerCellDepositV1 deposit, void* user);
    void (*emit_cell_trace)(const AnsiCell* cells,
        const GlyphId* final_glyphs, int w, int h, bool armed,
        AsciickerCellDepositV1 deposit, void* user);
    bool (*profile_ready)(void);
    bool (*capture_ready)(bool require_active_stroke);
    // Neutral scene-fact row snapshot (plain data, no product types). Lets
    // diagnostics consumers read product bridge rows without including
    // product headers or touching the product bridge global.
    int (*read_scene_fact_rows)(struct AsciickerSceneFactRowV1* rows, int cap,
        uint8_t* plane_valid_out, uint16_t* plane_w_out,
        uint16_t* plane_h_out, uint16_t* valid_count_out,
        uint64_t* bridge_frame_out);
    // Product emission census as JSON bytes (web diagnostics surface).
    // Returns bytes written excluding the terminator, or 0 on failure.
    int (*diagnostics_census_json)(char* buf, int cap);
};

// Neutral scene-fact row snapshot. Mirrors the fields diagnostics surfaces
// actually read; partition outcome crosses as plain integers.
struct AsciickerSceneFactRowV1
{
    uint8_t valid;
    uint8_t schema_version;
    uint32_t cell_x;
    uint32_t cell_y;
    uint32_t plane_index;
    uint64_t surface_id;
    uint64_t feature_ref;
    int32_t material_namespace;
    int32_t material_key;
    uint8_t role;
    uint8_t route_family_mask;
    uint8_t payload_sha256[32];
    uint32_t partition_outcome;
    uint32_t partition_failure;
    uint8_t profile_scoped;
};

// FL-4512: optional application tool. The linked product owns the label, availability
// and action. A present descriptor has process lifetime and a nonempty label
// plus a non-null launch callback; absence is a null descriptor, not a stub.
struct ProductToolV1
{
    const char* label;
    void (*launch)(void);
};

// Product startup ports: argv configuration plus idempotent runtime
// installation. Tool launching lives here (not in the renderer table): the
// application shell calls it; it is a product-lifecycle operation.
// Self-acquiring.
struct ProductStartupV1
{
    uint32_t version;
    int (*startup_configure_from_argv)(int argc, char** argv,
        const char* base_path);
    void (*ensure_runtime_installed)(void);
    const ProductToolV1* tool;
};

// Installed bundle: one table pointer per port family. A product adapter
// installs supported capabilities; Classic has no authoring table.
struct AsciickerProductPortsV1
{
    uint32_t version;
    const RendererProductV1* renderer;
    const PresentationBackendV1* presentation;
    const InspectionPortV1* inspection;
    const ProductStartupV1* startup;
    const AuthoringPortV1* authoring;
};

// Product installer, defined by the linked product adapter (FL4512 adapter
// in Current builds, Classic adapter in Classic builds). Declared here ONLY
// so the lazy accessor below can call it. Shared code never selects between
// adapters; the link does. A binary without any adapter fails to link on
// first lazy use, which forces explicit adapter ownership.
void AsciickerProductInstallV1(void);

// Inline storage cell: exactly one instance program-wide (function-local
// static inside an inline function; C++11-safe, no inline variable).
inline AsciickerProductPortsV1* AsciickerProductPortsStorageV1(void)
{
    static AsciickerProductPortsV1 ports = {};
    return &ports;
}

inline void AsciickerProductSetPortsV1(const AsciickerProductPortsV1* ports)
{
    *AsciickerProductPortsStorageV1() = *ports;
}

// Cached read. NEVER installs. The only legal accessor from teardown.
// Returns the bundle pointer (never null); tables are null until installed.
inline const AsciickerProductPortsV1* AsciickerProductPortsCached(void)
{
    return AsciickerProductPortsStorageV1();
}

// Lazy read. Installs ONCE on first use (version 0 means uninstalled),
// then returns the bundle pointer.
inline const AsciickerProductPortsV1* AsciickerProductHooks(void)
{
    AsciickerProductPortsV1* cell = AsciickerProductPortsStorageV1();
    if (cell->version == 0)
    {
        AsciickerProductInstallV1();
        cell->version = 1;
    }
    return cell;
}

// Per-table shorthands. Lazily install; unsupported capabilities return null.
inline const RendererProductV1* AsciickerRenderer(void)
{
    const AsciickerProductPortsV1* ports = AsciickerProductHooks();
    return ports ? ports->renderer : NULL;
}

inline const PresentationBackendV1* AsciickerPresentation(void)
{
    const AsciickerProductPortsV1* ports = AsciickerProductHooks();
    return ports ? ports->presentation : NULL;
}

inline const InspectionPortV1* AsciickerInspection(void)
{
    const AsciickerProductPortsV1* ports = AsciickerProductHooks();
    return ports ? ports->inspection : NULL;
}

inline const ProductStartupV1* AsciickerStartup(void)
{
    const AsciickerProductPortsV1* ports = AsciickerProductHooks();
    return ports ? ports->startup : NULL;
}

inline const AuthoringPortV1* AsciickerAuthoring(void)
{
    const AsciickerProductPortsV1* ports = AsciickerProductHooks();
    return ports ? ports->authoring : NULL;
}
