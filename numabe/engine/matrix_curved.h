#pragma once

// =============================================================================
// FL-4257 Phase 0 — Curved-space (spherical-first) primitives.
//
// Companion to engine/matrix_euclidean.h. Promotable runtime translation units
// may include EITHER this header OR matrix_euclidean.h, never both at once.
// The no-dual-include rule is audited by scripts/analyze_runs.py
// audit-no-dual-curvature-include (FL-4257 Phase 0 Step 12).
//
// SCOPE FOR PHASE 0:
// - Pure header-only spherical primitives (no global state, no I/O).
// - Operates on column-major float vectors `[x,y,z]` consistent with the
//   Euclidean kernel's 3-element vector convention.
// - Server is the sole owner of curvature kappa (Law 2). Functions take kappa
//   or a unit-sphere radius explicitly so the caller cannot smuggle in a
//   second owner via a hidden default.
// - Spherical (kappa > 0) is the primary proof target per the 2026-06-09 ADR.
//   Hyperbolic (kappa < 0) helpers are reserved for later phases; this header
//   intentionally leaves geodesic Lorentz primitives unimplemented to avoid
//   premature contracts.
//
// PHASE 0 NON-GOALS:
// - No Project/Unproject hooks: those live in engine/render/render_projection
//   and land in Phase 1.
// - No matrix-stack interop with the column-major 4x4 stack in
//   matrix_euclidean.h: spherical rotation is expressed via direct vector ops
//   (Rodrigues on the unit sphere) so the Phase 0 surface stays free of mixed
//   storage conventions.
// =============================================================================

#include <math.h>
#include <stdint.h>

#ifndef ASCK_CURVED_PI
#define ASCK_CURVED_PI 3.14159265358979323846
#endif

// kappa-classification helpers. These are the SOLE legal interpreters of
// server-published curvature_kappa on the curved-space side; any duplicate
// classifier elsewhere is a Law 1 violation.
static inline int CurvedKappaIsEuclidean(float kappa)
{
    return kappa == 0.0f;
}

static inline int CurvedKappaIsSpherical(float kappa)
{
    return kappa > 0.0f;
}

static inline int CurvedKappaIsHyperbolic(float kappa)
{
    return kappa < 0.0f;
}

// Returns 1 if kappa is the canonical Phase 0 spherical value (+1.0f). Phase 0
// only accepts the canonical spherical sign; Phase 1+ can broaden this.
static inline int CurvedKappaIsCanonicalSpherical(float kappa)
{
    return kappa == 1.0f;
}

// 3-vector helpers (local to this header so curved-space code does not depend
// on the Euclidean header's templated Product/CrossProduct/DotProduct).
static inline float CurvedDot3(const float a[3], const float b[3])
{
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

static inline void CurvedCross3(const float a[3], const float b[3], float out[3])
{
    out[0] = a[1] * b[2] - a[2] * b[1];
    out[1] = a[2] * b[0] - a[0] * b[2];
    out[2] = a[0] * b[1] - a[1] * b[0];
}

static inline float CurvedLength3(const float v[3])
{
    return (float)sqrt((double)CurvedDot3(v, v));
}

// Normalise in-place. Returns 0 (and leaves v unchanged) if length underflows.
static inline int CurvedNormalize3(float v[3])
{
    float l = CurvedLength3(v);
    if (l <= 1e-12f) return 0;
    float inv = 1.0f / l;
    v[0] *= inv; v[1] *= inv; v[2] *= inv;
    return 1;
}

// Great-circle angle between two unit-length vectors on a sphere of unit
// radius. Returns angle in radians in [0, pi]. Uses atan2 of (||a x b||, a.b)
// for numeric stability at small and near-pi angles — acos(a.b) loses
// precision near the poles.
static inline float SphericalGreatCircleAngle(const float a[3], const float b[3])
{
    float c[3];
    CurvedCross3(a, b, c);
    float s = CurvedLength3(c);
    float d = CurvedDot3(a, b);
    return (float)atan2((double)s, (double)d);
}

// Great-circle arc length on a sphere of given radius (kappa = 1/radius^2 in
// the spherical regime). Returns 0 when radius is non-positive — caller must
// have already proved curvature_present=1 before calling.
static inline float SphericalArcLength(const float a[3], const float b[3], float radius)
{
    if (radius <= 0.0f) return 0.0f;
    return radius * SphericalGreatCircleAngle(a, b);
}

// Project a 3-vector onto the unit sphere by normalising. Returns 1 on
// success, 0 if the input is too short to define a direction (caller MUST
// fail closed in that case — never substitute a default direction).
static inline int SphericalProjectToUnit(const float in[3], float out[3])
{
    out[0] = in[0]; out[1] = in[1]; out[2] = in[2];
    return CurvedNormalize3(out);
}

// Rodrigues' rotation of vector `v` around unit axis `k` by angle `theta`
// (radians). Pure spherical-friendly form: result lies on the same sphere as
// v provided ||k|| == 1. Caller is responsible for normalising k.
//
// out and v may alias; out and k must NOT alias.
static inline void SphericalRotateAroundAxis(const float v[3],
                                             const float k[3],
                                             float theta,
                                             float out[3])
{
    float c = (float)cos((double)theta);
    float s = (float)sin((double)theta);
    float dot = CurvedDot3(k, v);
    float cross[3];
    CurvedCross3(k, v, cross);

    float vx = v[0], vy = v[1], vz = v[2];
    out[0] = vx * c + cross[0] * s + k[0] * dot * (1.0f - c);
    out[1] = vy * c + cross[1] * s + k[1] * dot * (1.0f - c);
    out[2] = vz * c + cross[2] * s + k[2] * dot * (1.0f - c);
}

// SphericalCurvatureRadius — inverse of sqrt(kappa) when kappa > 0. Returns 0
// when kappa is non-positive. Phase 0 callers should check
// CurvedKappaIsSpherical(kappa) first.
static inline float SphericalCurvatureRadius(float kappa)
{
    if (!CurvedKappaIsSpherical(kappa)) return 0.0f;
    return (float)(1.0 / sqrt((double)kappa));
}

// =============================================================================
// S3 WORLD MODEL — FL-4257 corrective rebuild (2026-06-11).
//
// Cited math: Szirmay-Kalos & Magdics, "Adapting Game Engines to Curved
// Spaces" (The Visual Computer, 2022), spherical (kappa=+1) branch. Points
// live on the unit 3-sphere S3 = {P in R4 : <P,P> = 1} after scaling world
// distances by R = 1/sqrt(kappa_phys); <.,.> is the EUCLIDEAN 4D dot
// product (curved_dot_product below — for kappa>0 the embedding metric is
// Euclidean; the Lorentzian form is the hyperbolic branch, deferred).
// Geodesics are great circles advanced with the exponential map
// (curved_geodesic_point); frames move by parallel transport
// (curved_parallel_transport); every pair of points has an antipodal pair
// (curved_antipode) and distance arc = atan2-stable angle * R.
//
// ASCIIcker world mapping ("ground sphere x radial height"):
//   The gameplay surface is the EQUATORIAL ground 2-sphere of S3 (4th
//   coordinate 0); heights are RADIAL offsets along the 4th axis and
//   gravity points radially back toward the ground sphere. The
//   azimuthal-equidistant chart at the anchor maps import-payload (cx,cy)
//   to the ground sphere and native z to the radial angle:
//     rho=|c|, theta=rho/R, phi=h/R, d=c/rho:
//     P = (cos(phi) sin(theta) d.x, cos(phi) sin(theta) d.y,
//          cos(phi) cos(theta), sin(phi))
//   CHART COORDINATES ARE IMPORT/DEBUG/COMPAT ONLY (A3D heightfield
//   indexing, wire compatibility). Runtime truth is the embedded pose;
//   curved_from_embedding exists for those compat outputs and fails (0)
//   at the vertical poles where the chart is undefined.
// =============================================================================

// 4D dot product on the spherical embedding (kappa>0 metric).
static inline double curved_dot_product(const double a[4], const double b[4])
{
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
}

// Normalise a 4-vector onto S3. Returns 0 (input unchanged) on underflow —
// caller must fail closed, never substitute a default point.
static inline int curved_normalize(double v[4])
{
    double l = sqrt(curved_dot_product(v, v));
    if (l <= 1e-12) return 0;
    double inv = 1.0 / l;
    v[0] *= inv; v[1] *= inv; v[2] *= inv; v[3] *= inv;
    return 1;
}

// Stable geodesic angle between two points on S3 (radians, [0, pi]).
static inline double curved_arc_angle(const double a[4], const double b[4])
{
    double d = curved_dot_product(a, b);
    double diff[4] = { a[0]-b[0], a[1]-b[1], a[2]-b[2], a[3]-b[3] };
    double sum[4]  = { a[0]+b[0], a[1]+b[1], a[2]+b[2], a[3]+b[3] };
    double sd = sqrt(curved_dot_product(diff, diff));
    double ss = sqrt(curved_dot_product(sum, sum));
    (void)d;
    return 2.0 * atan2(sd, ss); // = angle, stable near 0 and pi
}

// Exponential map: advance from origin along unit tangent dir by angle ang.
// out = cos(ang) * origin + sin(ang) * dir. dir must be unit and tangent
// (<origin,dir> = 0); out lies on S3 exactly up to FP.
static inline void curved_geodesic_point(const double origin[4],
                                         const double dir[4],
                                         double ang, double out[4])
{
    double c = cos(ang), s = sin(ang);
    for (int i = 0; i < 4; i++)
        out[i] = c * origin[i] + s * dir[i];
}

// Parallel transport of tangent vector v along the geodesic from origin in
// direction dir by angle ang. The transported direction itself is
//   dir' = cos(ang) * dir - sin(ang) * origin;
// components of v orthogonal to the motion plane are unchanged.
static inline void curved_parallel_transport(const double origin[4],
                                             const double dir[4],
                                             double ang,
                                             const double v[4],
                                             double out[4])
{
    double c = cos(ang), s = sin(ang);
    double vd = curved_dot_product(v, dir);
    double vo = curved_dot_product(v, origin);
    for (int i = 0; i < 4; i++)
    {
        double in_plane = vd * (c * dir[i] - s * origin[i])
                        + vo * (c * origin[i] + s * dir[i]);
        double off_plane = v[i] - vd * dir[i] - vo * origin[i];
        out[i] = in_plane + off_plane;
    }
}

// Antipodal point: every P on S3 has exactly one -P at arc angle pi.
static inline void curved_antipode(const double p[4], double out[4])
{
    out[0] = -p[0]; out[1] = -p[1]; out[2] = -p[2]; out[3] = -p[3];
}

static inline int curved_is_antipodal(const double a[4], const double b[4],
                                      double tolerance)
{
    return curved_arc_angle(a, b) >= ASCK_CURVED_PI - tolerance;
}

// Chart -> S3 embedding (see header block for the formula). R in world
// units; cx/cy chart offsets from the anchor in world units; h native-z
// offset from the anchor level DIVIDED BY the native height scale by the
// caller (h is in world-metric units here).
static inline void curved_to_embedding(double R, double cx, double cy,
                                       double h, double out[4])
{
    double rho = sqrt(cx * cx + cy * cy);
    double dx = 1.0, dy = 0.0;
    if (rho > 1e-12) { dx = cx / rho; dy = cy / rho; }
    double theta = rho / R;
    double phi = h / R;
    double cp = cos(phi), sp = sin(phi);
    double st = sin(theta), ct = cos(theta);
    out[0] = cp * st * dx;
    out[1] = cp * st * dy;
    out[2] = cp * ct;
    out[3] = sp;
}

// S3 -> chart. IMPORT/DEBUG/COMPAT ONLY — never a runtime truth source.
// Returns 0 at the vertical poles (chart undefined: |cos(phi)| ~ 0).
static inline int curved_from_embedding(double R, const double p[4],
                                        double* cx, double* cy, double* h)
{
    double phi = asin(p[3] > 1.0 ? 1.0 : (p[3] < -1.0 ? -1.0 : p[3]));
    double cp = cos(phi);
    if (cp <= 1e-9) return 0;
    double gx = p[0] / cp, gy = p[1] / cp, gz = p[2] / cp;
    double theta = atan2(sqrt(gx * gx + gy * gy), gz);
    double rho = theta * R;
    double dl = sqrt(gx * gx + gy * gy);
    if (dl > 1e-12) { *cx = rho * gx / dl; *cy = rho * gy / dl; }
    else            { *cx = 0.0; *cy = 0.0; }
    *h = phi * R;
    return 1;
}

// Tangent frame at a chart point: east (chart +x transported), north
// (chart +y transported), up (radial, d/dh). Orthonormal, tangent to S3.
static inline void curved_world_frame(double R, double cx, double cy, double h,
                                      double east[4], double north[4],
                                      double up[4])
{
    double rho = sqrt(cx * cx + cy * cy);
    double dx = 1.0, dy = 0.0;
    if (rho > 1e-12) { dx = cx / rho; dy = cy / rho; }
    double theta = rho / R, phi = h / R;
    double cp = cos(phi), sp = sin(phi);
    double st = sin(theta), ct = cos(theta);
    // radial-in-chart direction (d/drho) and transverse (d/dpsi normalised)
    double er[4] = { cp * ct * dx, cp * ct * dy, -cp * st, 0.0 };
    double et[4] = { -dy, dx, 0.0, 0.0 };
    // chart east = +x axis direction at the anchor, transported along the
    // chart ray (the azimuthal-equidistant chart transports the anchor
    // frame radially): east = dx*er - dy*et rotated back by the ray angle.
    for (int i = 0; i < 4; i++)
    {
        east[i]  = dx * er[i] - dy * et[i];
        north[i] = dy * er[i] + dx * et[i];
    }
    // er carries cos(phi) on its e4-free part while et does not, so the
    // mixed east/north combinations lose exact orthonormality at h != 0;
    // Gram-Schmidt restores it (east normalised, north orthogonalised
    // against east, then normalised).
    double el = sqrt(curved_dot_product(east, east));
    if (el > 1e-12) for (int i = 0; i < 4; i++) east[i] /= el;
    double ne = curved_dot_product(north, east);
    for (int i = 0; i < 4; i++) north[i] -= ne * east[i];
    double nl = sqrt(curved_dot_product(north, north));
    if (nl > 1e-12) for (int i = 0; i < 4; i++) north[i] /= nl;
    up[0] = -sp * st * dx;
    up[1] = -sp * st * dy;
    up[2] = -sp * ct;
    up[3] = cp;
}
