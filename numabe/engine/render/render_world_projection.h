#pragma once

// =============================================================================
// FL-4257 Phase 1 S3 — world-vertex projection dispatchers.
//
// These declarations are the public entrypoint for terrain-patch and
// sprite-billboard projection. Before S3 the math was inlined inside the
// patch loop in render_world_pass.cpp and the sprite loop in
// render_sprite_blit.cpp; each site re-read r->mul, r->add, r->view_pos,
// r->view_dir, r->view_ofs directly, which meant the spherical curvature
// view added in S2 was bypassed for the visible world geometry. S3 deletes
// that inline ownership and routes every world-vertex projection through
// the cached server-authoritative view so visible terrain and sprites
// follow the same κ-aware branch as ProjectCoords.
//
// Each dispatcher branches on the cached Renderer.curvature:
//   - present == 1 && kind == spherical -> spherical sibling
//   - present == 1 && kind == Euclidean -> Euclidean baseline (byte-identical
//     to the pre-S3 inline math)
//   - present == 1 && kind unrecognised -> fail closed (return false)
//   - present == 0 -> Euclidean baseline
//
// Ortho projection has no spherical sibling in Phase 1. Spherical curvature
// with r->perspective == false fails closed (the engine has no notion of
// "isometric spherical").
// =============================================================================

struct Renderer;

struct RenderEuclideanProjectionFrameV1
{
	double view_pos[3];
	double view_dir[3];
	double view_ofs[2];
};

#ifdef __cplusplus
extern "C++" {
#endif

// Patch-form perspective projection. Matches the factored-add form the patch
// hot loop uses today. *viewer_dist on success is the POSITIVE forward
// distance (NOT 1/dist); caller inverts as needed. Returns false on cull.
bool ProjectWorldVertexPatchPersp(
    Renderer* r, float vx, float vy, float vz,
    float* fx, float* fy, float* viewer_dist);

// Sprite-form perspective projection. Adds r->add inside the parens (matches
// ProjectCoords' algebraic form); the FP rounding pattern differs slightly
// from the patch form even though they are algebraically equivalent. The
// sprite hot loop's existing math is preserved by routing through this
// helper, not the patch one. *viewer_dist is POSITIVE on success.
bool ProjectWorldVertexSpritePersp(
    Renderer* r, float vx, float vy, float vz,
    float* fx, float* fy, float* viewer_dist);

// Ortho projection — int_flag form. fx and fy are integer screen positions
// in float bookkeeping (Renderer.mul has double precision so the helper
// returns int to preserve the original truncation semantics).
bool ProjectWorldVertexOrthoIntFlag(
    Renderer* r, float vx, float vy, float vz,
    int* tx, int* ty);

// Ortho projection — int-add form (offsets come from r->add cast to int
// outside the sum, vs the int_flag form which adds them inside the floor).
// Same fail-closed semantics under spherical curvature.
bool ProjectWorldVertexOrthoIadd(
    Renderer* r, float vx, float vy, float vz,
    int* tx, int* ty);

// Face-form perspective projection. The mesh face path pre-transforms each
// vertex through r->viewinst_tm into (pre_fx, pre_fy, pre_fz) and through
// r->inst_tm into (ws_x, ws_y, ws_z), then needs to apply the perspective
// divide using the cached view fields. This helper hides the divide so the
// face hot loop doesn't read r->view_pos / r->view_dir / r->view_ofs
// directly. ws_x and ws_y are PRE-HEIGHT_CELLS (helper scales internally to
// match the legacy inline math). *viewer_dist is POSITIVE on success.
bool ProjectFaceVertexPersp(
    Renderer* r,
    float pre_fx, float pre_fy,
    float ws_x, float ws_y, float ws_z,
    float* fx, float* fy, float* viewer_dist);

// Read-only Euclidean camera values for renderer algorithms that need a
// differential rather than a projected vertex. The projection owner rejects
// this request whenever published non-Euclidean curvature is active.
bool ReadEuclideanProjectionFrameV1(
    const Renderer* r, RenderEuclideanProjectionFrameV1* out);

// Mesh view-transform bundler. Builds the same 4x4 view_tm the legacy
// RenderMesh used to inline: r->mul scaled by HEIGHT_CELLS in xy, raw mul
// rows on z, r->add as translation, with the y-axis sign flipped under
// reflection. Output goes into view_tm_out[16]. This is matrix construction,
// not per-vertex projection; the projection itself still happens in the
// face dispatcher. By extracting this helper the per-mesh caller no longer
// reads r->mul / r->add directly.
void BuildMeshViewTransformEuclidean(Renderer* r, bool reflect, double view_tm_out[16]);

#ifdef __cplusplus
}
#endif
