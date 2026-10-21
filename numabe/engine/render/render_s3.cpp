// render_s3.cpp — FL-4257 corrective rebuild: the single 4D spherical render
// owner. See render_s3.h for the ownership contract. This is the ONE render
// translation unit that includes matrix_curved.h (no-dual-include audit);
// it never calls the Euclidean projection kernel.

#include <math.h>
#include "render_internal.h"
#include "matrix_curved.h"
#include "render_s3.h"

// Arc-depth encoding. The sample buffer's existing comparator keeps the
// LARGER height value, so nearer (smaller arc) must encode larger:
//   key = C - arc*S,  arc in [0, 2*pi)  =>  key in (C - 2*pi*S, C].
// Keys stay far above every world height and the -1e6 empty sentinel, so a
// surviving sentinel is a measurable "ray hit nothing" — never repainted
// (the resolve water fill is gated off under spherical curvature).
static const double kS3DepthC = 800000.0;
static const double kS3DepthS = 10000.0;

// Minimum forward DIRECTION component (sin of the angular near guard).
static const double kS3ForwardMin = 0.017;

int RenderS3Active(const Renderer* r)
{
	return r && r->s3_active == 1;
}

int RenderS3FailClosed(const Renderer* r)
{
	return r && r->s3_active == 2;
}

void RenderS3SetImage(Renderer* r, int image)
{
	r->s3_image = image ? 1 : 0;
}

float RenderS3LastDepth(const Renderer* r)
{
	return r ? r->s3_last_depth : 0.0f;
}

int RenderS3DepthOrientationParity(float depth_key)
{
	double arc = (kS3DepthC - (double)depth_key) / kS3DepthS;
	return arc > 0.5 * ASCK_CURVED_PI && arc < 1.5 * ASCK_CURVED_PI;
}

static inline double S3Dot3d(const double a[3], const double b[3])
{
	return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

static inline void S3Cross3d(const double a[3], const double b[3], double out[3])
{
	out[0] = a[1] * b[2] - a[2] * b[1];
	out[1] = a[2] * b[0] - a[0] * b[2];
	out[2] = a[0] * b[1] - a[1] * b[0];
}

static int S3Normalize3d(double v[3])
{
	double l = sqrt(S3Dot3d(v, v));
	if (l <= 1e-12)
		return 0;
	double inv = 1.0 / l;
	v[0] *= inv;
	v[1] *= inv;
	v[2] *= inv;
	return 1;
}

static void S3TransportGroundVector(const double start_g[3], const double dir[3],
                                    const double end_g[3], double angle,
                                    const double v[3], double out[3])
{
	double end_t[3] = {
		cos(angle) * dir[0] - sin(angle) * start_g[0],
		cos(angle) * dir[1] - sin(angle) * start_g[1],
		cos(angle) * dir[2] - sin(angle) * start_g[2],
	};
	double vd = S3Dot3d(v, dir);
	double vg = S3Dot3d(v, start_g);
	for (int i = 0; i < 3; i++)
	{
		double off = v[i] - vd * dir[i] - vg * start_g[i];
		out[i] = off + vd * end_t[i] + vg * end_g[i];
	}
	S3Normalize3d(out);
}

static int RenderS3BuildAuthoritativeCamera(Renderer* r, double R)
{
	if (!r || r->s3_pose_valid != 1 || R <= 0.0)
		return 0;
	double g[3] = { r->s3_pose_ground[0], r->s3_pose_ground[1], r->s3_pose_ground[2] };
	double e0[3] = { r->s3_pose_east[0], r->s3_pose_east[1], r->s3_pose_east[2] };
	if (!S3Normalize3d(g))
		return 0;
	double ge = S3Dot3d(g, e0);
	for (int i = 0; i < 3; i++)
		e0[i] -= ge * g[i];
	if (!S3Normalize3d(e0))
		return 0;
	double e1[3];
	S3Cross3d(g, e0, e1);
	if (!S3Normalize3d(e1))
		return 0;

	// Body facing (yaw) is server-authoritative; the first-person camera renders
	// THAT yaw. First-person horizontal look is a body-yaw INTENT (handled in the
	// local player authority, which feeds the single yaw owner), so the view and
	// the body never diverge — turning the look turns the body and movement
	// follows. Orbit also uses pure body yaw, so the accepted far-side parity
	// checkpoint stays byte-identical.
	const double yawr = (double)r->s3_pose_yaw * (ASCK_CURVED_PI / 180.0);
	const double fwd_e = -sin(yawr);
	const double fwd_n = cos(yawr);
	double fwd_h[3];
	double right_h[3];
	for (int i = 0; i < 3; i++)
	{
		fwd_h[i] = fwd_e * e0[i] + fwd_n * e1[i];
		right_h[i] = cos(yawr) * e0[i] + sin(yawr) * e1[i];
	}
	S3Normalize3d(fwd_h);
	S3Normalize3d(right_h);

	const double orbit = r->s3_first_person ? 0.0 : 3.0;
	const double eye_h = r->s3_first_person ? 1.6 : 1.8;
	// Pitch math convention: positive tilts the forward ray DOWN (it subtracts
	// the radial up below). Orbit keeps its fixed 8 deg down-look. First person
	// uses the client free-look pitch where +deg means look UP, so we negate it
	// and clamp to keep the tangent frame non-degenerate near the poles.
	double fp_pitch_deg = (double)r->s3_free_pitch;
	if (fp_pitch_deg > 85.0) fp_pitch_deg = 85.0;
	if (fp_pitch_deg < -85.0) fp_pitch_deg = -85.0;
	const double pitch = (r->s3_first_person ? -fp_pitch_deg : 8.0)
		* (ASCK_CURVED_PI / 180.0);

	double cam_g[3] = { g[0], g[1], g[2] };
	double cam_fwd_h[3] = { fwd_h[0], fwd_h[1], fwd_h[2] };
	double cam_right_h[3] = { right_h[0], right_h[1], right_h[2] };
	if (orbit > 0.0)
	{
		double back[3] = { -fwd_h[0], -fwd_h[1], -fwd_h[2] };
		double a = orbit / R;
		double ca = cos(a), sa = sin(a);
		for (int i = 0; i < 3; i++)
			cam_g[i] = ca * g[i] + sa * back[i];
		S3Normalize3d(cam_g);
		S3TransportGroundVector(g, back, cam_g, a, fwd_h, cam_fwd_h);
		S3TransportGroundVector(g, back, cam_g, a, right_h, cam_right_h);
	}

	double h = ((double)r->s3_pose_height - r->s3_anchor[2]) / (double)HEIGHT_SCALE + eye_h;
	double phi = h / R;
	double cp_h = cos(phi), sp_h = sin(phi);
	for (int i = 0; i < 3; i++)
		r->s3_cam[i] = cp_h * cam_g[i];
	r->s3_cam[3] = sp_h;

	double up_radial[4] = {
		-sp_h * cam_g[0],
		-sp_h * cam_g[1],
		-sp_h * cam_g[2],
		cp_h,
	};
	double cp = cos(pitch), sp = sin(pitch);
	for (int i = 0; i < 3; i++)
	{
		r->s3_right[i] = cam_right_h[i];
		r->s3_fwd[i] = cp * cam_fwd_h[i] - sp * up_radial[i];
		r->s3_up[i] = sp * cam_fwd_h[i] + cp * up_radial[i];
	}
	r->s3_right[3] = 0.0;
	r->s3_fwd[3] = -sp * up_radial[3];
	r->s3_up[3] = cp * up_radial[3];
	return 1;
}

int RenderS3Setup(Renderer* r, const float player_pos[3], float player_yaw,
                  int dw, int dh, const int scene_shift[2])
{
	(void)player_pos;
	(void)player_yaw;
	r->s3_active = 0;
	r->s3_image = 0;
	r->s3_primary_count = 0;
	r->s3_antipodal_count = 0;
	r->s3_water_count = 0;
	r->s3_rej_pole = 0;
	r->s3_rej_near = 0;
	r->s3_rej_gate[0] = 0;
	r->s3_rej_gate[1] = 0;
	if (r->curvature.present != 1)
		return 0;
	if (r->curvature.kind == ASCK_CURVATURE_KIND_EUCLIDEAN)
		return 0;
	if (r->curvature.kind != ASCK_CURVATURE_KIND_SPHERICAL ||
	    !CurvedKappaIsSpherical(r->curvature.kappa))
	{
		r->s3_active = 2; // published-but-unrecognised: fail closed (Law 6)
		return 0;
	}
	double R = (double)SphericalCurvatureRadius(r->curvature.kappa);
	if (R <= 0.0)
	{
		r->s3_active = 2;
		return 0;
	}

	r->s3_R = R;
	r->s3_anchor[0] = r->curvature.anchor[0];
	r->s3_anchor[1] = r->curvature.anchor[1];
	r->s3_anchor[2] = r->curvature.anchor[2];

	if (!RenderS3BuildAuthoritativeCamera(r, R))
	{
		r->s3_active = 2;
		return 0;
	}

	// Pinhole intrinsics in sample space. Terminal cells are ~2:1 tall and
	// the sample grid is 2x2 per cell, so equal world angles need half the
	// sample count vertically.
	r->s3_focal = 0.5 * (double)(dw > dh ? dw : dh);
	r->s3_focal_y = 0.5 * r->s3_focal;
	r->s3_ofs[0] = (double)(dw / 2 + scene_shift[0] * 2);
	r->s3_ofs[1] = (double)(dh * 0.34 + scene_shift[1] * 2);

	r->s3_active = 1;
	return 1;
}

static inline double S3Dot4(const double a[4], const double b[4])
{
	return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
}

int RenderS3ProjectWorld(const Renderer* r, float wx, float wy, float wz,
                         float* fx, float* fy, float* viewer_dist,
                         float* depth)
{
	if (!r || r->s3_active != 1)
		return 0;
	double cx = (double)wx - r->s3_anchor[0];
	double cy = (double)wy - r->s3_anchor[1];
	double h = ((double)wz - r->s3_anchor[2]) / (double)HEIGHT_SCALE;

	// Closed-disc cull: the spherical world is the chart disc rho <= pi*R
	// (R derived from authored-LAND extent; the disc rim is the antipode).
	// Sea-filler tiles beyond the disc lie past the antipode and would
	// double-cover the far side; the world-model water globe owns the rest
	// of the sphere, so culling them loses nothing authored.
	double rho = sqrt(cx * cx + cy * cy);
	if (rho > ASCK_CURVED_PI * r->s3_R * (1.0 + 1e-6))
		return 0;

	double P[4];
	curved_to_embedding((float)r->s3_R, cx, cy, h, P);
	return RenderS3ProjectEmbedded(r, P, fx, fy, viewer_dist, depth);
}

int RenderS3ProjectEmbedded(const Renderer* r, const double P[4],
                            float* fx, float* fy, float* viewer_dist,
                            float* depth)
{
	if (!r || r->s3_active != 1)
		return 0;
	// SO(4) view rotation: coordinates of P on the camera frame.
	double qr = S3Dot4(P, r->s3_right);
	double qu = S3Dot4(P, r->s3_up);
	double qf = S3Dot4(P, r->s3_fwd);
	double qw = S3Dot4(P, r->s3_cam);
	if (qw > 1.0) qw = 1.0;
	if (qw < -1.0) qw = -1.0;
	double arc = acos(qw);

	// (qr,qu,qf) = sin(arc) * d where d is the RAY DIRECTION at the eye, so
	// the screen ratios qr/qf, qu/qf are the direction's GNOMONIC image for
	// every arc in (0, pi) — including the far side past the equator of
	// sight, whose position components all shrink with sin(arc). The
	// hemisphere gate therefore tests the DIRECTION component
	// d.fwd = qf/sin(arc), never qf itself (gating on qf silently culls
	// the antipodal far side because sin(arc) -> 0 there).
	double salpha = sqrt(qr * qr + qu * qu + qf * qf);
	if (salpha <= 1e-9)
	{
		r->s3_rej_pole++;
		return 0; // exact camera (anti)pode: direction undefined
	}
	// Arc near-plane: geometry grazing the orbit camera explodes in the
	// divide.
	if (arc * r->s3_R < 0.25)
	{
		r->s3_rej_near++;
		return 0;
	}

	// MANDATORY dual image: image 0 sees rays leaving the eye forward
	// (depth = arc); image 1 sees the same world the long way around the
	// great circle (complementary hemisphere gate, depth = 2*pi - arc).
	if (r->s3_image == 0)
	{
		if (qf < kS3ForwardMin * salpha)
		{
			r->s3_rej_gate[0]++;
			return 0;
		}
	}
	else
	{
		if (qf > -kS3ForwardMin * salpha)
		{
			r->s3_rej_gate[1]++;
			return 0;
		}
		arc = 2.0 * ASCK_CURVED_PI - arc;
	}

	if (r->s3_image == 0)
		r->s3_primary_count++;
	else
		r->s3_antipodal_count++;

	double inv = 1.0 / qf;
	*fx = (float)(r->s3_ofs[0] + r->s3_focal * qr * inv);
	*fy = (float)(r->s3_ofs[1] + r->s3_focal_y * qu * inv);
	*viewer_dist = (float)(arc * r->s3_R);
	*depth = (float)(kS3DepthC - arc * kS3DepthS);
	return 1;
}

// ── Seam entries (called from render_projection.cpp's dispatchers) ──────────

bool RenderS3ProjectCoords(Renderer* r, const float pos[3], int view[3])
{
	if (r->s3_active != 1)
		return false;
	float fx, fy, vd, depth;
	int saved = r->s3_image;
	r->s3_image = 0; // UI anchors use the near image
	int ok = RenderS3ProjectWorld(r, pos[0], pos[1], pos[2], &fx, &fy, &vd, &depth);
	r->s3_image = saved;
	if (!ok)
		return false;
	int tx = (int)floorf(fx + 0.5f);
	int ty = (int)floorf(fy + 0.5f);
	view[0] = (tx - 1) >> 1;
	view[1] = (ty - 1) >> 1;
	view[2] = (int)floorf(depth + 0.5f);
	return true;
}

bool RenderS3UnprojectCoords3D(Renderer* r, const int xyz[3], float pos[3])
{
	if (r->s3_active != 1)
		return false;
	double arc = (kS3DepthC - (double)xyz[2]) / kS3DepthS;
	if (arc <= 0.0 || arc >= 2.0 * ASCK_CURVED_PI)
		return false;
	double sx = 2.0 * xyz[0] + 2.0, sy = 2.0 * xyz[1] + 2.0;
	double dx = (sx - r->s3_ofs[0]) / r->s3_focal;
	double dy = (sy - r->s3_ofs[1]) / r->s3_focal_y;
	double t[4];
	for (int i = 0; i < 4; i++)
		t[i] = dx * r->s3_right[i] + dy * r->s3_up[i] + r->s3_fwd[i];
	double tl = sqrt(S3Dot4(t, t));
	if (tl <= 1e-12)
		return false;
	double P[4];
	double ca = cos(arc), sa = sin(arc);
	for (int i = 0; i < 4; i++)
		P[i] = ca * r->s3_cam[i] + sa * t[i] / tl;
	double cx, cy, h;
	if (!curved_from_embedding((float)r->s3_R, P, &cx, &cy, &h))
		return false;
	pos[0] = (float)(cx + r->s3_anchor[0]);
	pos[1] = (float)(cy + r->s3_anchor[1]);
	pos[2] = (float)(h * (double)HEIGHT_SCALE + r->s3_anchor[2]);
	return true;
}

bool RenderS3UnprojectCoords2D(Renderer* r, const int xy[2], float pos[3])
{
	if (r->s3_active != 1)
		return false;
	int w = (r->sample_buffer.w - 4) / 2;
	int h = (r->sample_buffer.h - 4) / 2;
	if (xy[0] < 0 || xy[1] < 0 || xy[0] >= w || xy[1] >= h)
		return false;
	int x = 2 + xy[0] * 2;
	int y = 2 + xy[1] * 2;
	int y0 = r->sample_buffer.w * y + x;
	int y1 = y0 + r->sample_buffer.w;
	float sh[4] =
	{
		r->sample_buffer.ptr[y0].height,
		r->sample_buffer.ptr[y0 + 1].height,
		r->sample_buffer.ptr[y1].height,
		r->sample_buffer.ptr[y1 + 1].height,
	};
	float height = sh[0];
	for (int i = 1; i < 4; i++)
		if (height < sh[i]) height = sh[i];
	int xyz[3] = { xy[0], xy[1], (int)floorf(height + 0.5f) };
	return RenderS3UnprojectCoords3D(r, xyz, pos);
}

bool RenderS3ProjectWorldVertex(Renderer* r, float vx, float vy, float vz,
                                float* fx, float* fy, float* viewer_dist)
{
	if (r->s3_active != 1)
		return false;
	float depth;
	if (!RenderS3ProjectWorld(r, vx / (float)HEIGHT_CELLS, vy / (float)HEIGHT_CELLS,
	                          vz, fx, fy, viewer_dist, &depth))
		return false;
	r->s3_last_depth = depth;
	return true;
}

bool RenderS3ProjectFaceVertex(Renderer* r, float ws_x, float ws_y, float ws_z,
                               float* fx, float* fy, float* viewer_dist)
{
	if (r->s3_active != 1)
		return false;
	float depth;
	if (!RenderS3ProjectWorld(r, ws_x, ws_y, ws_z, fx, fy, viewer_dist, &depth))
		return false;
	r->s3_last_depth = depth;
	return true;
}

int RenderS3TriBlowup(const Renderer* r, const int* a, const int* b, const int* c)
{
	if (!r || r->s3_active != 1)
		return 0;
	// tri[2] carries the arc depth key under the 4D owner.
	int dmin = a[2] < b[2] ? (a[2] < c[2] ? a[2] : c[2]) : (b[2] < c[2] ? b[2] : c[2]);
	int dmax = a[2] > b[2] ? (a[2] > c[2] ? a[2] : c[2]) : (b[2] > c[2] ? b[2] : c[2]);
	double arc_near = (kS3DepthC - (double)dmax) / kS3DepthS; // nearest vertex
	(void)dmin;
	if (arc_near >= 0.5 * ASCK_CURVED_PI)
		return 0; // far-side magnification is legitimate
	int w = r->sample_buffer.w, h = r->sample_buffer.h;
	int minx = a[0] < b[0] ? (a[0] < c[0] ? a[0] : c[0]) : (b[0] < c[0] ? b[0] : c[0]);
	int maxx = a[0] > b[0] ? (a[0] > c[0] ? a[0] : c[0]) : (b[0] > c[0] ? b[0] : c[0]);
	int miny = a[1] < b[1] ? (a[1] < c[1] ? a[1] : c[1]) : (b[1] < c[1] ? b[1] : c[1]);
	int maxy = a[1] > b[1] ? (a[1] > c[1] ? a[1] : c[1]) : (b[1] > c[1] ? b[1] : c[1]);
	return (maxx - minx) > 2 * w || (maxy - miny) > 2 * h;
}

static void RenderS3TerrainChartMidpoint(const S3TerrainVertex& p1,
                                         const S3TerrainVertex& p2,
                                         S3TerrainVertex& out)
{
	out.wx = 0.5f * (p1.wx + p2.wx);
	out.wy = 0.5f * (p1.wy + p2.wy);
	out.wz = 0.5f * (p1.wz + p2.wz);
	out.u = 0.5f * (p1.u + p2.u);
	out.v = 0.5f * (p1.v + p2.v);
}

void RenderS3TerrainMidpoint(const Renderer* r, const S3TerrainVertex& p1,
                             const S3TerrainVertex& p2, S3TerrainVertex& out)
{
	out.u = 0.5f * (p1.u + p2.u);
	out.v = 0.5f * (p1.v + p2.v);
	if (!r || r->s3_active != 1)
	{
		RenderS3TerrainChartMidpoint(p1, p2, out);
		return;
	}

	double P1[4], P2[4], M[4];
	curved_to_embedding(r->s3_R, (double)p1.wx - r->s3_anchor[0],
	                    (double)p1.wy - r->s3_anchor[1],
	                    ((double)p1.wz - r->s3_anchor[2]) / (double)HEIGHT_SCALE,
	                    P1);
	curved_to_embedding(r->s3_R, (double)p2.wx - r->s3_anchor[0],
	                    (double)p2.wy - r->s3_anchor[1],
	                    ((double)p2.wz - r->s3_anchor[2]) / (double)HEIGHT_SCALE,
	                    P2);
	double len2 = 0.0;
	for (int i = 0; i < 4; i++)
	{
		M[i] = P1[i] + P2[i];
		len2 += M[i] * M[i];
	}
	if (len2 <= 1e-18)
	{
		RenderS3TerrainChartMidpoint(p1, p2, out);
		return;
	}
	double inv_len = 1.0 / sqrt(len2);
	for (int i = 0; i < 4; i++)
		M[i] *= inv_len;
	double cx, cy, h;
	if (!curved_from_embedding(r->s3_R, M, &cx, &cy, &h))
	{
		RenderS3TerrainChartMidpoint(p1, p2, out);
		return;
	}
	out.wx = (float)(cx + r->s3_anchor[0]);
	out.wy = (float)(cy + r->s3_anchor[1]);
	out.wz = (float)(h * (double)HEIGHT_SCALE + r->s3_anchor[2]);
}

// ── World-model water sphere: leaf rasterizer ───────────────────────────────
// The sphere tessellation and projection-safe subdivision are OWNED by the
// world model (engine/world_s3.cpp); leaves arrive pre-projected and
// screen-small, so a linear fill is exact to sampling tolerance — no
// blow-up guards, no rim-ring tuning.

void RenderS3FillWaterLeaf(Renderer* r,
                           const float vfx[3], const float vfy[3],
                           const float vdepth[3], const float vpat[3])
{
	if (!r || r->s3_active != 1 || !r->sample_buffer.ptr)
		return;
	int w = r->sample_buffer.w;
	int hgt = r->sample_buffer.h;
	Sample* buf = r->sample_buffer.ptr;

	int minx = (int)floorf(fminf(vfx[0], fminf(vfx[1], vfx[2])));
	int maxx = (int)ceilf(fmaxf(vfx[0], fmaxf(vfx[1], vfx[2])));
	int miny = (int)floorf(fminf(vfy[0], fminf(vfy[1], vfy[2])));
	int maxy = (int)ceilf(fmaxf(vfy[0], fmaxf(vfy[1], vfy[2])));
	if (minx < 0) minx = 0;
	if (miny < 0) miny = 0;
	if (maxx >= w) maxx = w - 1;
	if (maxy >= hgt) maxy = hgt - 1;
	if (minx > maxx || miny > maxy)
		return;

	float d12 = (vfy[1] - vfy[2]) * (vfx[0] - vfx[2]) +
	            (vfx[2] - vfx[1]) * (vfy[0] - vfy[2]);
	if (fabsf(d12) < 1e-6f)
		return;
	float invd = 1.0f / d12;
	const uint16_t vis = (uint16_t)(2 | (8 << 5) | (20 << 10));

	for (int y = miny; y <= maxy; y++)
	{
		for (int x = minx; x <= maxx; x++)
		{
			float px = (float)x + 0.5f, py = (float)y + 0.5f;
			float l0 = ((vfy[1] - vfy[2]) * (px - vfx[2]) +
			            (vfx[2] - vfx[1]) * (py - vfy[2])) * invd;
			float l1 = ((vfy[2] - vfy[0]) * (px - vfx[2]) +
			            (vfx[0] - vfx[2]) * (py - vfy[2])) * invd;
			float l2 = 1.0f - l0 - l1;
			if (l0 < 0.0f || l1 < 0.0f || l2 < 0.0f)
				continue;
			float depth = l0 * vdepth[0] + l1 * vdepth[1] + l2 * vdepth[2];
			Sample* s = buf + (size_t)w * y + x;
			if (s->height >= depth)
				continue;
			// Surface, not sky: brightness is carried by the WORLD-SPACE
			// wave pattern (interpolated from the vertices) quantised to
			// crisp bands, with only a mild arc falloff for depth. The
			// pattern is attached to the sphere, so near waves are small
			// and the far side's waves arrive magnified overhead — the
			// texture gradient a featureless shade cannot provide.
			float arcf = (float)((kS3DepthC - (double)depth) / kS3DepthS);
			float fall = 0.75f + 0.25f * (float)cos((double)(arcf * 0.85f));
			float pat = l0 * vpat[0] + l1 * vpat[1] + l2 * vpat[2];
			int band = (int)floorf(pat * 2.0f) & 1;
			int dif = (int)((130.0f + 70.0f * (pat - floorf(pat)) +
			                 (band ? 35.0f : 0.0f)) * fall);
			s->height = depth;
			r->s3_water_count++;
			s->visual = vis;
			s->material_id = (int16_t)(vis & 0x00FF);
			s->diffuse = (uint8_t)(dif < 40 ? 40 : (dif > 255 ? 255 : dif));
			s->spare = 0x8;
			s->glyph_dispatch_surface = GLYPH_DISPATCH_AUTOMAP_MESH_RGB;
			s->s3_parity = (uint8_t)RenderS3DepthOrientationParity(depth);
		}
	}
}

// FL-4257 — build the minimap's player-centred tangent frame from the
// SERVER-TRANSPORTED pose (Law 1/2: render server truth, do not recompute a
// second chart-frame owner). The server parallel-transports s3_east along the
// real path (server/mp_step_s3.cpp); the angle between that frame and the
// chart frame is the holonomy the FL-4257 proof gates measure, so the minimap
// MUST use the transported frame or it falsely shows chart-aligned motion.
//
// The ground 2-sphere of S3 is the {w=0} slice (matrix_curved.h: h=0 ->
// embedding[3]=0), so the server's 3-vector s3_ground / s3_east lift losslessly
// to 4-vectors (·,·,·,0). north = ground x east on that slice = the server's
// own working north (S3Cross3(g, e0, e1)). Returns 0 (caller falls back to the
// flat chart) when curvature is inactive or no valid authoritative pose is
// cached — Law 6 fail-closed, never a substituted default frame.
static int S3MinimapFrame(const Renderer* r, double Pp[4],
                          double east4[4], double north4[4])
{
	if (!r || r->s3_active != 1 || r->s3_R <= 0.0 || !r->s3_pose_valid)
		return 0;
	Pp[0] = r->s3_pose_ground[0];
	Pp[1] = r->s3_pose_ground[1];
	Pp[2] = r->s3_pose_ground[2];
	Pp[3] = 0.0;
	if (!curved_normalize(Pp))
		return 0;
	east4[0] = r->s3_pose_east[0];
	east4[1] = r->s3_pose_east[1];
	east4[2] = r->s3_pose_east[2];
	east4[3] = 0.0;
	// Re-orthonormalise against the ground point (defensive; the server already
	// re-orthonormalises each step before publishing).
	double pe = curved_dot_product(Pp, east4);
	for (int i = 0; i < 4; i++)
		east4[i] -= pe * Pp[i];
	if (!curved_normalize(east4))
		return 0;
	// north = ground x east on the {w=0} 2-sphere (3D cross, 4th component 0).
	north4[0] = Pp[1] * east4[2] - Pp[2] * east4[1];
	north4[1] = Pp[2] * east4[0] - Pp[0] * east4[2];
	north4[2] = Pp[0] * east4[1] - Pp[1] * east4[0];
	north4[3] = 0.0;
	if (!curved_normalize(north4))
		return 0;
	return 1;
}

// FL-4257 — is the player-centred transported-frame minimap projection active?
// (spherical curvature + a valid authoritative pose). Lets the minimap widen its
// chart-space pre-cull so geodesic-bent footprints are not dropped before
// RenderS3MinimapProject can place them.
int RenderS3MinimapActive(const Renderer* r)
{
	double Pp[4], east[4], north[4];
	return S3MinimapFrame(r, Pp, east, north) ? 1 : 0;
}

// FL-4257 — player-centred azimuthal-equidistant minimap sample. See
// render_s3.h. Uses the SERVER-TRANSPORTED frame (S3MinimapFrame) and the
// trusted matrix_curved primitives (exponential map, chart inverse).
// Euclidean/inactive, no-authoritative-pose, and pole-degenerate samples fail
// closed (return 0) so the caller keeps the flat-chart sample and non-spherical
// runs stay byte-identical.
int RenderS3MinimapSample(const Renderer* r, float px, float py,
                          float east_m, float north_m,
                          float* out_wx, float* out_wy)
{
	if (!out_wx || !out_wy)
		return 0;
	double Pp[4], east[4], north[4];
	if (!S3MinimapFrame(r, Pp, east, north))
		return 0;
	double R = r->s3_R;
	double ax = r->s3_anchor[0], ay = r->s3_anchor[1];

	// Screen offset as a tangent direction + geodesic distance.
	double s = sqrt((double)east_m * (double)east_m +
	                (double)north_m * (double)north_m);
	if (s < 1e-6)
	{
		*out_wx = px;
		*out_wy = py;
		return 1; // player cell: no drift to correct
	}
	double ie = (double)east_m / s, in_ = (double)north_m / s;
	double dir[4];
	for (int i = 0; i < 4; i++)
		dir[i] = ie * east[i] + in_ * north[i];
	if (!curved_normalize(dir))
		return 0; // degenerate tangent: fail closed

	// Advance along the great circle by arc = distance / R, then back to chart.
	double Q[4];
	curved_geodesic_point(Pp, dir, s / R, Q);
	double qx, qy, qh;
	if (!curved_from_embedding(R, Q, &qx, &qy, &qh))
		return 0; // vertical pole: chart undefined, fail closed

	*out_wx = (float)(ax + qx);
	*out_wy = (float)(ay + qy);
	return 1;
}

// FL-4257 — inverse of RenderS3MinimapSample: project a world chart point
// (wx,wy) on the ground sheet to its player-centred tangent offset
// (east_m,north_m) in the SERVER-TRANSPORTED frame (the log map). This places
// markers, NPC/remote dots, and mesh-footprint vertices on their TRUE geodesic
// position instead of a chart-linear offset, so every minimap layer shares the
// one transported frame the terrain background already uses. Fail-closed
// (return 0, caller keeps the linear offset) when curvature is inactive, no
// authoritative pose is cached, or the target coincides with the player.
int RenderS3MinimapProject(const Renderer* r, float px, float py,
                           float wx, float wy,
                           float* out_east_m, float* out_north_m)
{
	if (!out_east_m || !out_north_m)
		return 0;
	double Pp[4], east[4], north[4];
	if (!S3MinimapFrame(r, Pp, east, north))
		return 0;
	double R = r->s3_R;
	double ax = r->s3_anchor[0], ay = r->s3_anchor[1];

	// Target world point lifted to the ground sheet (h = 0).
	double Q[4];
	curved_to_embedding(R, (double)wx - ax, (double)wy - ay, 0.0, Q);

	double ang = curved_arc_angle(Pp, Q);
	if (ang < 1e-9)
	{
		*out_east_m = 0.0f;
		*out_north_m = 0.0f;
		return 1; // target is the player cell
	}
	// Unit tangent at the player pointing toward the target.
	double dir[4];
	for (int i = 0; i < 4; i++)
		dir[i] = Q[i] - cos(ang) * Pp[i];
	if (!curved_normalize(dir))
		return 0;
	double dist = ang * R;
	*out_east_m = (float)(dist * curved_dot_product(dir, east));
	*out_north_m = (float)(dist * curved_dot_product(dir, north));
	return 1;
}
