#pragma once

// =============================================================================
// FL-4257 Phase 0 — Curvature contract (server publisher / client consumer).
// =============================================================================
//
// Server is the sole owner of curvature per Law 2 (Server Owns Gameplay State).
// Clients receive curvature via the authoritative state surface and MUST fail
// closed (Law 6) when presence=0 — never substitute a local Euclidean default.
//
// Wire format (authoritative_state.json):
//   "curvature_kappa":   float | null   (null when authority absent)
//   "curvature_kind":    int   | null   (0=Euclidean, 1=spherical, 2=hyperbolic)
//   "curvature_present": 0 | 1          (presence bit; never null)
//
// Phase 0 mapping (server-canonical, mirrored in server/server_state.h):
//   kind 0 -> kappa  0.0  (Euclidean)
//   kind 1 -> kappa +1.0  (spherical / elliptic — primary proof target)
//   kind 2 -> kappa -1.0  (hyperbolic — reserved for later phases)
//
// Phase 0 scope: scaffolding only. Render/collision math does NOT yet branch on
// kappa; Phase 1 wires ProjectCoords/UnprojectCoords and the per-step geodesic
// path. Any code reading `curvature_kappa` before then is for diagnostics and
// recorder evidence only and must respect the null-vs-zero distinction.
// =============================================================================

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Canonical kind constants — must match SVR_CURVATURE_KIND_* in
// server/server_state.h. Diverging requires a contract review per Law 1.
#define ASCK_CURVATURE_KIND_EUCLIDEAN   0u
#define ASCK_CURVATURE_KIND_SPHERICAL   1u
#define ASCK_CURVATURE_KIND_HYPERBOLIC  2u
#define ASCK_CURVATURE_KIND_NONE        0xFFu

// Client-facing resolved view of server-authoritative curvature. `present=0`
// means the server published null; in that case `kappa`/`kind` must not be
// consumed by render or collision math.
typedef struct AsciCurvatureView
{
    uint8_t present;
    uint8_t kind;
    float   kappa;
    // FL-4257 corrective — server-derived chart anchor (published with
    // kappa; meaningless when present=0).
    float   anchor[3];
} AsciCurvatureView;

// Returns a "no authority" view. Callers MUST use this rather than constructing
// `{0,0,0.0f}` themselves so that the fail-closed contract is grep-traceable.
static inline AsciCurvatureView AsciCurvatureViewAbsent(void)
{
    AsciCurvatureView v;
    v.present = 0;
    v.kind = ASCK_CURVATURE_KIND_NONE;
    v.kappa = 0.0f;
    v.anchor[0] = v.anchor[1] = v.anchor[2] = 0.0f;
    return v;
}

// Build a view from server intake values. The wire carries `curvature_present`
// strictly as 0 or 1 (never null, never any other value) plus a kind that may
// be null. Callers MUST decode the wire first and pass BOTH values: a JSON
// decoder that maps null kind -> 0 would otherwise manufacture Euclidean
// authority out of an absent server truth. Law 6 fail-closed:
//   - present_wire != 1 -> absent, no matter what kind says (rejects 0, the
//     documented absent value, AND any other byte that violates the
//     presence-bit contract — invalid presence is not presence).
//   - present_wire == 1 with an unrecognised kind -> also absent (invalid
//     authority is not authority).
//
// This is the single client-side adapter from the JSON wire to the runtime
// view. Phase 0: not yet called from render — present for recorder/diagnostic
// paths and to make the fail-closed contract testable.
static inline AsciCurvatureView AsciCurvatureViewFromIntake(uint8_t present_wire,
                                                            uint8_t kind)
{
    if (present_wire != 1)
    {
        return AsciCurvatureViewAbsent();
    }
    AsciCurvatureView v = AsciCurvatureViewAbsent();
    if (kind == ASCK_CURVATURE_KIND_EUCLIDEAN ||
        kind == ASCK_CURVATURE_KIND_SPHERICAL ||
        kind == ASCK_CURVATURE_KIND_HYPERBOLIC)
    {
        v.kind = kind;
        v.kappa = (kind == ASCK_CURVATURE_KIND_SPHERICAL)  ? +1.0f
                : (kind == ASCK_CURVATURE_KIND_HYPERBOLIC) ? -1.0f
                                                           :  0.0f;
        v.present = 1;
        return v;
    }
    return AsciCurvatureViewAbsent();
}

// FL-4257 corrective — adapter for the SERVER-PUBLISHED wire (RSP_JOIN):
// the server publishes its DERIVED physical kappa (1/R^2, world units) and
// the chart anchor; the kind-canonical kappa above is only the legacy
// intake adapter. Law 6 fail-closed: invalid presence, unrecognised kind,
// or a kappa whose sign contradicts the kind all collapse to absent.
static inline AsciCurvatureView AsciCurvatureViewFromPublished(
    uint8_t present_wire, uint8_t kind, float kappa, const float anchor[3])
{
    if (present_wire != 1)
        return AsciCurvatureViewAbsent();
    AsciCurvatureView v = AsciCurvatureViewAbsent();
    if (kind == ASCK_CURVATURE_KIND_EUCLIDEAN && kappa == 0.0f)
        { v.kind = kind; v.kappa = 0.0f; }
    else if (kind == ASCK_CURVATURE_KIND_SPHERICAL && kappa > 0.0f)
        { v.kind = kind; v.kappa = kappa; }
    else if (kind == ASCK_CURVATURE_KIND_HYPERBOLIC && kappa < 0.0f)
        { v.kind = kind; v.kappa = kappa; }
    else
        return AsciCurvatureViewAbsent();
    if (anchor)
    {
        v.anchor[0] = anchor[0];
        v.anchor[1] = anchor[1];
        v.anchor[2] = anchor[2];
    }
    v.present = 1;
    return v;
}

#ifdef __cplusplus
}
#endif
