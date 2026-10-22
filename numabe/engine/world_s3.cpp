// world_s3.cpp — FL-4257 world-model water sphere. See world_s3.h for the
// ownership contract. The closed ocean is ACTUAL S3 world geometry: a
// geodesic tessellation of the entire ground sphere at water level (an
// octahedron subdivided on the sphere — no anchor-centric chart rings, no
// rim tuning), rasterized with PROJECTION-SAFE recursive subdivision:
// every triangle is split in embedded space until it either projects
// entirely inside the current image at rasterizable screen size (linear
// leaf fill is then exact to sampling tolerance) or shrinks below angular
// resolution at a hemisphere gate (clipped). This replaces — and bans —
// the finite chart-ring globe path and its blow-up/log-ring workarounds.

#include <math.h>
#include "render/render_internal.h"
#include "render/render_s3.h"
#include "world_s3.h"

// Leaf acceptance: max screen extent of a triangle rasterized linearly.
static const float kS3WaterLeafPx = 28.0f;
// Subdivision floor: triangles below this angular size (radians on the
// ground sphere) that still straddle a gate are dropped — subsample slivers.
static const double kS3WaterMinAng = 2e-4;
static const int kS3WaterMaxDepth = 5;

struct S3WaterCtx
{
	Renderer* r;
	double cphi, sphi; // cos/sin of the radial water angle
	double wave_k;     // world-space wave frequency (radians on the sphere)
};

// World-space wave field on the sphere: three crossed stripe systems give
// a quasi-checker that never lines up with the screen — texture attached
// to the WORLD, so its screen size follows the curvature (reverse
// perspective magnifies the far side's waves).
static inline float S3WavePattern(const S3WaterCtx& c, const double u[3])
{
	double k = c.wave_k;
	double v = sin(k * u[0]) + sin(k * u[1] + 1.7) + sin(k * u[2] + 3.9);
	return (float)(0.5 + v / 6.0); // ~[0,1]
}

// Embed a unit ground-sphere direction at water height.
static inline void S3WaterEmbed(const S3WaterCtx& c, const double u[3], double P[4])
{
	P[0] = c.cphi * u[0];
	P[1] = c.cphi * u[1];
	P[2] = c.cphi * u[2];
	P[3] = c.sphi;
}

static inline void S3Mid(const double a[3], const double b[3], double m[3])
{
	m[0] = a[0] + b[0];
	m[1] = a[1] + b[1];
	m[2] = a[2] + b[2];
	double l = sqrt(m[0] * m[0] + m[1] * m[1] + m[2] * m[2]);
	if (l > 1e-15) { m[0] /= l; m[1] /= l; m[2] /= l; }
}

static inline double S3AngSize(const double a[3], const double b[3], const double c3[3])
{
	double d0 = 1.0 - (a[0]*b[0] + a[1]*b[1] + a[2]*b[2]);
	double d1 = 1.0 - (b[0]*c3[0] + b[1]*c3[1] + b[2]*c3[2]);
	double d2 = 1.0 - (c3[0]*a[0] + c3[1]*a[1] + c3[2]*a[2]);
	double dm = d0 > d1 ? d0 : d1;
	if (d2 > dm) dm = d2;
	return sqrt(2.0 * dm); // ~ max edge angle
}

static void S3WaterTri(const S3WaterCtx& c,
                       const double u0[3], const double u1[3], const double u2[3],
                       double ang, int depth)
{
	float fx[3], fy[3], dep[3], vd;
	double P[4];
	S3WaterEmbed(c, u0, P);
	int ok0 = RenderS3ProjectEmbedded(c.r, P, &fx[0], &fy[0], &vd, &dep[0]);
	S3WaterEmbed(c, u1, P);
	int ok1 = RenderS3ProjectEmbedded(c.r, P, &fx[1], &fy[1], &vd, &dep[1]);
	S3WaterEmbed(c, u2, P);
	int ok2 = RenderS3ProjectEmbedded(c.r, P, &fx[2], &fy[2], &vd, &dep[2]);
	int nok = ok0 + ok1 + ok2;
	float pat[3] = { S3WavePattern(c, u0), S3WavePattern(c, u1), S3WavePattern(c, u2) };

	if (nok == 3)
	{
		float ex = fmaxf(fx[0], fmaxf(fx[1], fx[2])) - fminf(fx[0], fminf(fx[1], fx[2]));
		float ey = fmaxf(fy[0], fmaxf(fy[1], fy[2])) - fminf(fy[0], fminf(fy[1], fy[2]));
		if (ex <= kS3WaterLeafPx && ey <= kS3WaterLeafPx)
		{
			RenderS3FillWaterLeaf(c.r, fx, fy, dep, pat);
			return;
		}
		// fully off-screen leaves stop early (their children stay inside
		// the same projected hull — the gnomonic image of a geodesic
		// triangle wholly inside one hemisphere is convex-bounded).
		int w = c.r->sample_buffer.w, h = c.r->sample_buffer.h;
		if ((fx[0] < 0 && fx[1] < 0 && fx[2] < 0) ||
		    (fy[0] < 0 && fy[1] < 0 && fy[2] < 0) ||
		    (fx[0] >= w && fx[1] >= w && fx[2] >= w) ||
		    (fy[0] >= h && fy[1] >= h && fy[2] >= h))
			return;
	}
	else
	{
		// Gate-straddling (or near-plane-grazing) triangle: clip by
		// subdivision until the pieces land in one image or vanish.
		if (ang < kS3WaterMinAng || depth >= kS3WaterMaxDepth)
			return;
	}
	if (depth >= kS3WaterMaxDepth)
		return;

	double m01[3], m12[3], m20[3];
	S3Mid(u0, u1, m01);
	S3Mid(u1, u2, m12);
	S3Mid(u2, u0, m20);
	double ha = 0.5 * ang;
	S3WaterTri(c, u0, m01, m20, ha, depth + 1);
	S3WaterTri(c, m01, u1, m12, ha, depth + 1);
	S3WaterTri(c, m20, m12, u2, ha, depth + 1);
	S3WaterTri(c, m01, m12, m20, ha, depth + 1);
}

void WorldS3SubmitWaterGlobe(Renderer* r, float water_native_z)
{
	if (!RenderS3Active(r))
		return;
	if (!r->sample_buffer.ptr || r->sample_buffer.w <= 0 || r->sample_buffer.h <= 0)
		return;

	// FL-4257 web/first-person closure owner: solve the water great-2-sphere
	// analytically along each screen ray. The retired tessellated octahedron
	// path below dropped gate-straddling leaves at recursion depth, which left
	// visible blue holes and zero water terminations in headed S3 proof.
	const double sphi = sin(((double)water_native_z - r->s3_anchor[2]) /
	                       (double)HEIGHT_SCALE / r->s3_R);
	const double pi2 = 2.0 * 3.14159265358979323846;
	const double depth_c = 800000.0;
	const double depth_s = 10000.0;
	const uint16_t vis = (uint16_t)(2 | (8 << 5) | (20 << 10));
	for (int y = 0; y < r->sample_buffer.h; y++)
	{
		for (int x = 0; x < r->sample_buffer.w; x++)
		{
			Sample* s = r->sample_buffer.ptr + (size_t)y * (size_t)r->sample_buffer.w + (size_t)x;
			double dx = ((double)x + 0.5 - r->s3_ofs[0]) / r->s3_focal;
			double dy = ((double)y + 0.5 - r->s3_ofs[1]) / r->s3_focal_y;
			double d[4];
			double dl2 = 0.0;
			for (int i = 0; i < 4; i++)
			{
				d[i] = dx * r->s3_right[i] + dy * r->s3_up[i] + r->s3_fwd[i];
				dl2 += d[i] * d[i];
			}
			if (dl2 <= 1e-18)
				continue;
			double inv_dl = 1.0 / sqrt(dl2);
			for (int i = 0; i < 4; i++)
				d[i] *= inv_dl;
			double A = r->s3_cam[3];
			double B = d[3];
			double C = sqrt(A * A + B * B);
			if (C <= 1e-12)
				continue;
			double q = sphi / C;
			if (q > 1.0) q = 1.0;
			if (q < -1.0) q = -1.0;
			double base = atan2(B, A);
			double delta = acos(q);
			double arc0 = base + delta;
			double arc1 = base - delta;
			while (arc0 < 0.0) arc0 += pi2;
			while (arc1 < 0.0) arc1 += pi2;
			while (arc0 >= pi2) arc0 -= pi2;
			while (arc1 >= pi2) arc1 -= pi2;
			double arc = arc0 < arc1 ? arc0 : arc1;
			if (arc * r->s3_R < 0.25)
				arc = arc0 > arc1 ? arc0 : arc1;
			double depth = depth_c - arc * depth_s;
			if (s->height >= (float)depth)
				continue;
			double pat = sin(11.0 * dx + 0.7) + sin(13.0 * dy + 2.1);
			int dif = (int)(130.0 + 35.0 * pat);
			s->height = (float)depth;
			s->visual = vis;
			s->diffuse = (uint8_t)(dif < 40 ? 40 : (dif > 255 ? 255 : dif));
			s->spare = 0x8;
			s->glyph_dispatch_surface = GLYPH_DISPATCH_AUTOMAP_MESH_RGB;
			s->s3_parity = (uint8_t)RenderS3DepthOrientationParity((float)depth);
			r->s3_water_count++;
		}
	}
	return;

	S3WaterCtx c;
	c.r = r;
	double phi = ((double)water_native_z - r->s3_anchor[2]) /
	             (double)HEIGHT_SCALE / r->s3_R;
	c.cphi = cos(phi);
	c.sphi = sin(phi);
	// wave length ~6 world units: k = 2*pi*R / 6
	c.wave_k = 2.0 * 3.14159265358979 * r->s3_R / 6.0;

	// Octahedron over the whole ground sphere: 6 vertices, 8 faces —
	// uniform closed cover, no pole/rim special cases.
	static const double V[6][3] = {
		{ 1, 0, 0 }, { -1, 0, 0 },
		{ 0, 1, 0 }, { 0, -1, 0 },
		{ 0, 0, 1 }, { 0, 0, -1 },
	};
	static const int F[8][3] = {
		{ 4, 0, 2 }, { 4, 2, 1 }, { 4, 1, 3 }, { 4, 3, 0 },
		{ 5, 2, 0 }, { 5, 1, 2 }, { 5, 3, 1 }, { 5, 0, 3 },
	};
	for (int f = 0; f < 8; f++)
		S3WaterTri(c, V[F[f][0]], V[F[f][1]], V[F[f][2]],
		           1.5707963267948966, 0);
}
