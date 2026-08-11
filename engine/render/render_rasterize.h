// render_rasterize.h — Bresenham, Shader, Rasterize templates
// Extracted from render.cpp for use by render_world_pass.cpp
#pragma once
#include "render_internal.h"
#include <algorithm>
#include <float.h>

// Shared edge-function helpers. These replace the old BC_A/BC_P macros so the
// rasterizer math has one typed owner and no macro lifetime leak across TUs.
inline int RasterCellArea(const int* a, const int* b, const int* c)
{
	return 2 * (((b)[0] - (a)[0]) * ((c)[1] - (a)[1]) - ((b)[1] - (a)[1]) * ((c)[0] - (a)[0]));
}

inline int RasterCellPoint(const int* a, const int* b, const int* c)
{
	return ((b)[0] - (a)[0]) * (2 * (c)[1] + 1 - 2 * (a)[1]) - ((b)[1] - (a)[1]) * (2 * (c)[0] + 1 - 2 * (a)[0]);
}

template <int VaryingCount>
inline void RasterComputeVaryingGradients(const int* v[3], const float varying[3][VaryingCount], int area, float origin[VaryingCount], float dx[VaryingCount], float dy[VaryingCount])
{
	float inv_area = 2.0f / (float)area;
	float e01x = (float)(v[1][0] - v[0][0]);
	float e01y = (float)(v[1][1] - v[0][1]);
	float e02x = (float)(v[2][0] - v[0][0]);
	float e02y = (float)(v[2][1] - v[0][1]);

	for (int i = 0; i < VaryingCount; i++)
	{
		float d01 = varying[1][i] - varying[0][i];
		float d02 = varying[2][i] - varying[0][i];
		dx[i] = (d01 * e02y - d02 * e01y) * inv_area;
		dy[i] = (d02 * e01x - d01 * e02x) * inv_area;
		origin[i] = varying[0][i] - (float)v[0][0] * dx[i] - (float)v[0][1] * dy[i];
	}
}

struct RasterSampleWriterNoop
{
	template <typename Sample>
	void operator()(Sample*, float, const float*) const {}
};

struct RasterCellWriterNoop
{
	void operator()(AnsiCell*, float, const float*) const {}
};

// SampleBuffer is 2x supersampled — every other column suffices.
template <typename Sample, int VaryingCount = 0, typename Writer = RasterSampleWriterNoop>
inline void Bresenham(Sample* buf, int w, int h, int from[3], int to[3], int _or, const float* from_vary = 0, const float* to_vary = 0, Writer writer = Writer())
{
	int sx = to[0] - from[0];
	int sy = to[1] - from[1];

	if (sx == 0 && sy==0)
		return;

	int sz = to[2] - from[2];

	int ax = sx >= 0 ? sx : -sx;
	int ay = sy >= 0 ? sy : -sy;

	if (ax >= ay)
	{
		// horizontal domain

		if (from[0] > to[0])
		{
			int* swap = from;
			from = to;
			to = swap;
			const float* vary_swap = from_vary;
			from_vary = to_vary;
			to_vary = vary_swap;
			sx = -sx;
			sy = -sy;
			sz = -sz;
		}
		float n = +1.0f / sx;

		int	x0 = (std::max(0, from[0]) + 1) & ~1; // round up start x, so we won't produce out of domain samples
		int	x1 = std::min(w, to[0]);

		for (int x = x0; x < x1; x+=2)
		{
			float a = x - from[0] + 0.5f;
			int y = (int)floor((a * sy)*n + from[1] + 0.5f);
			if (y >= 0 && y < h)
			{
				float z = (a * sz) * n + from[2];
				Sample* ptr = buf + w * y + x;
				if (ptr->DepthTest_RO(z))
				{
					ptr->spare |= _or;
					if (from_vary && to_vary)
					{
						float vary[VaryingCount ? VaryingCount : 1];
						float t = a * n;
						for (int i = 0; i < VaryingCount; i++)
							vary[i] = from_vary[i] + t * (to_vary[i] - from_vary[i]);
						writer(ptr, z, vary);
					}
				}
				ptr++;
				if (ptr->DepthTest_RO(z))
				{
					ptr->spare |= _or;
					if (from_vary && to_vary)
					{
						float vary[VaryingCount ? VaryingCount : 1];
						float t = a * n;
						for (int i = 0; i < VaryingCount; i++)
							vary[i] = from_vary[i] + t * (to_vary[i] - from_vary[i]);
						writer(ptr, z, vary);
					}
				}
			}
		}
	}
	else
	{
		// vertical domain

		if (from[1] > to[1])
		{
			int* swap = from;
			from = to;
			to = swap;
			const float* vary_swap = from_vary;
			from_vary = to_vary;
			to_vary = vary_swap;
			sx = -sx;
			sy = -sy;
			sz = -sz;
		}
		float n = 1.0f / sy;

		int y0 = std::max(0, from[1]);
		int y1 = std::min(h, to[1]);

		for (int y = y0; y < y1; y++)
		{
			int a = y - from[1];
			int x = (int)floor((a * sx) * n + from[0] + 0.5f);
			if (x >= 0 && x < w)
			{
				float z = (a * sz)*n + from[2];
				Sample* ptr = buf + w * y + x;
				if (ptr->DepthTest_RO(z))
				{
					ptr->spare |= _or;
					if (from_vary && to_vary)
					{
						float vary[VaryingCount ? VaryingCount : 1];
						float t = a * n;
						for (int i = 0; i < VaryingCount; i++)
							vary[i] = from_vary[i] + t * (to_vary[i] - from_vary[i]);
						writer(ptr, z, vary);
					}
				}
			}
		}
	}
}

// WHY PerspectiveCorrectCellLine: Sprites and projectiles need to draw
// lines at the AnsiCell resolution (not SampleBuffer resolution), but must
// still depth-test against the 2x supersampled SampleBuffer. The 1/w
// interpolation (ka = ka / ((1-t)*d_from + t*d_to)) corrects for perspective
// foreshortening — without it, attributes would interpolate linearly in
// screen space, causing visual stretching on angled surfaces.
template <typename Sample, int VaryingCount = 0, typename Writer = RasterCellWriterNoop>
inline void PerspectiveCorrectCellLine(/*const*/Sample* smp, AnsiCell* buf, int w, int h, int from[3], int to[3], float d_from, float d_to, int gl, int fg, const float* from_vary = 0, const float* to_vary = 0, Writer writer = Writer())
{
	int sx = to[0] - from[0];
	int sy = to[1] - from[1];

	if (sx == 0 && sy == 0)
		return;

	int sz = to[2] - from[2];

	int ax = sx >= 0 ? sx : -sx;
	int ay = sy >= 0 ? sy : -sy;

	if (ax >= ay)
	{
		// horizontal domain

		if (from[0] > to[0])
		{
			int* swap = from;
			from = to;
			to = swap;
			const float* vary_swap = from_vary;
			from_vary = to_vary;
			to_vary = vary_swap;
			float d_swap = d_from;
			d_from = d_to;
			d_to = d_swap;
			sx = -sx;
			sy = -sy;
			sz = -sz;
		}
		float n = +1.0f / sx;

		int	x0 = std::max(0, from[0]);
		int	x1 = std::min(w, to[0]);

		for (int x = x0; x < x1; x++)
		{
			float a = x - from[0] + 0.5f;
			int y = (int)floor((a * sy)*n + from[1] + 0.5f);
			if (y >= 0 && y < h)
			{
				int hx = 2 * x + 2;
				int hy = 2 * y + 2;

				float ka = a * n * d_to;
				ka = ka / ((1 - a * n) * d_from + ka);
				float z = sz * ka + from[2];

				/*const*/Sample* test = smp + hy * (2 * w + 4) + hx;
				if (test->DepthTest_RO(z))
				{
					AnsiCell* ptr = buf + w * y + x;
					int av = AverageGlyph(ptr, 0xF);
					ptr->bk = av;
					ptr->fg = LightenColor(LightenColor(av));
					ptr->gl = gl;
					if (from_vary && to_vary)
					{
						float vary[VaryingCount ? VaryingCount : 1];
						for (int i = 0; i < VaryingCount; i++)
							vary[i] = from_vary[i] + ka * (to_vary[i] - from_vary[i]);
						writer(ptr, z, vary);
					}
				}
			}
		}
	}
	else
	{
		// vertical domain

		if (from[1] > to[1])
		{
			int* swap = from;
			from = to;
			to = swap;
			const float* vary_swap = from_vary;
			from_vary = to_vary;
			to_vary = vary_swap;
			float d_swap = d_from;
			d_from = d_to;
			d_to = d_swap;
			sx = -sx;
			sy = -sy;
			sz = -sz;
		}
		float n = 1.0f / sy;

		int y0 = std::max(0, from[1]);
		int y1 = std::min(h, to[1]);

		for (int y = y0; y < y1; y++)
		{
			int a = y - from[1];
			int x = (int)floor((a * sx) * n + from[0] + 0.5f);
			if (x >= 0 && x < w)
			{
				int hx = 2 * x + 2;
				int hy = 2 * y + 2;

				float ka = a * n * d_to;
				ka = ka / ((1 - a * n) * d_from + ka);
				float z = sz * ka + from[2];

				/*const*/Sample* test = smp + hy * (2 * w + 4) + hx;
				if (test->DepthTest_RO(z))
				{
					AnsiCell* ptr = buf + w * y + x;
					int av = AverageGlyph(ptr, 0xF);
					ptr->bk = av;
					ptr->fg = LightenColor(LightenColor(av));
					ptr->gl = gl;
					if (from_vary && to_vary)
					{
						float vary[VaryingCount ? VaryingCount : 1];
						for (int i = 0; i < VaryingCount; i++)
							vary[i] = from_vary[i] + ka * (to_vary[i] - from_vary[i]);
						writer(ptr, z, vary);
					}
				}
			}
		}
	}
}

// WHY CellLine: Non-perspective variant of line drawing at AnsiCell
// resolution. Used for orthographic projection where 1/w correction is
// unnecessary (all depths are linearly interpolated in screen space).
template <typename Sample, int VaryingCount = 0, typename Writer = RasterCellWriterNoop>
inline void CellLine(/*const*/Sample* smp, AnsiCell* buf, int w, int h, int from[3], int to[3], int gl, int fg, const float* from_vary = 0, const float* to_vary = 0, Writer writer = Writer())
{
	int sx = to[0] - from[0];
	int sy = to[1] - from[1];

	if (sx == 0 && sy == 0)
		return;

	int sz = to[2] - from[2];

	int ax = sx >= 0 ? sx : -sx;
	int ay = sy >= 0 ? sy : -sy;

	if (ax >= ay)
	{
		// horizontal domain

		if (from[0] > to[0])
		{
			int* swap = from;
			from = to;
			to = swap;
			const float* vary_swap = from_vary;
			from_vary = to_vary;
			to_vary = vary_swap;
			sx = -sx;
			sy = -sy;
			sz = -sz;
		}
		float n = +1.0f / sx;

		int	x0 = std::max(0, from[0]);
		int	x1 = std::min(w, to[0]);

		for (int x = x0; x < x1; x++)
		{
			float a = x - from[0] + 0.5f;
			int y = (int)floor((a * sy)*n + from[1] + 0.5f);
			if (y >= 0 && y < h)
			{
				int hx = 2 * x + 2;
				int hy = 2 * y + 2;
				float z = (a * sz) * n + from[2];

				/*const*/Sample* test = smp + hy * (2 * w + 4) + hx;
				if (test->DepthTest_RO(z))
				{
					AnsiCell* ptr = buf + w * y + x;
					int av = AverageGlyph(ptr, 0xF);
					ptr->bk = av;
					ptr->fg = LightenColor(LightenColor(av));
					ptr->gl = gl;
					if (from_vary && to_vary)
					{
						float vary[VaryingCount ? VaryingCount : 1];
						float t = a * n;
						for (int i = 0; i < VaryingCount; i++)
							vary[i] = from_vary[i] + t * (to_vary[i] - from_vary[i]);
						writer(ptr, z, vary);
					}
				}
			}
		}
	}
	else
	{
		// vertical domain

		if (from[1] > to[1])
		{
			int* swap = from;
			from = to;
			to = swap;
			const float* vary_swap = from_vary;
			from_vary = to_vary;
			to_vary = vary_swap;
			sx = -sx;
			sy = -sy;
			sz = -sz;
		}
		float n = 1.0f / sy;

		int y0 = std::max(0, from[1]);
		int y1 = std::min(h, to[1]);

		for (int y = y0; y < y1; y++)
		{
			int a = y - from[1];
			int x = (int)floor((a * sx) * n + from[0] + 0.5f);
			if (x >= 0 && x < w)
			{
				int hx = 2 * x + 2;
				int hy = 2 * y + 2;
				float z = (a * sz) * n + from[2];

				/*const*/Sample* test = smp + hy * (2 * w + 4) + hx;
				if (test->DepthTest_RO(z))
				{
					AnsiCell* ptr = buf + w * y + x;
					int av = AverageGlyph(ptr, 0xF);
					ptr->bk = av;
					ptr->fg = LightenColor(LightenColor(av));
					ptr->gl = gl;
					if (from_vary && to_vary)
					{
						float vary[VaryingCount ? VaryingCount : 1];
						float t = a * n;
						for (int i = 0; i < VaryingCount; i++)
							vary[i] = from_vary[i] + t * (to_vary[i] - from_vary[i]);
						writer(ptr, z, vary);
					}
				}
			}
		}
	}
}


// todo: lets add "int varyings" template arg
// and add "const float varying[3][varyings]" function param (values at verts)
// so we can calc here values in lower-left corner and x,y gradients
// and provide all varyings interpolated into shader->Blend() call
// we should do it also for 'z' coord
// Translation for readers: today Rasterize() only hands interpolated depth and
// barycentric weights to the shader. This TODO proposes upgrading it so the
// rasterizer can also interpolate arbitrary per-vertex attributes ("varyings")
// such as UVs, colors, normals, or even depth itself in a more precomputed way.
//
// Implementation plan (POST-SHIPPABILITY ONLY):
// 1. Do not touch this before current shippability gates are met. This is
//    renderer performance debt, not a current ship blocker.
// 2. Keep the existing barycentric path as the reference implementation.
// 3. Add a second rasterizer path/helper that precomputes x/y gradients for a
//    bounded set of attributes instead of changing every shader at once.
// 4. Start with the lowest-risk attribute set:
//    - z only, or
//    - z + terrain UVs for RenderPatch()
//    Do not convert every attribute/shader in one pass.
// 5. Keep shader ownership deletion-first: when a varying moves from
//    shader-side recompute to rasterizer-side interpolation, delete the old
//    path instead of running both indefinitely.
// 6. Verify no visual drift on the exact fragile surfaces:
//    - adjacent triangle seams / edge ownership
//    - terrain material boundaries
//    - sprite occlusion against terrain/meshes
//    - water reflections / silhouette lines
// 7. Only expand beyond the bounded first pass if profiling shows the
//    triangle rasterizer is hot enough to justify the extra complexity.
//
// Main risks:
// - 1-sample drift in z/UV interpolation can change occlusion or material picks
// - triangle edge ownership changes can create cracks or double-draw seams
// - perspective paths can become wrong if attributes are not interpolated with
//   the same correctness assumptions as today
// - generic varyings can increase code complexity/register pressure enough to
//   erase the expected speedup

// -----------------------------------------------------------------------------
// Barycentric Triangle Rasterizer
// -----------------------------------------------------------------------------
// WHY barycentric rasterization: it naturally produces interpolation weights
// for every pixel inside a triangle, enabling smooth attribute interpolation
// (UV coords, depth, color) without separate scanline conversion logic.
//
// ALGORITHM: bounding-box traversal with edge function inside/outside tests.
// For each pixel in the triangle's bbox, compute 3 edge functions — if all
// have the same sign, the pixel is inside the triangle.
//
// Template Params:
// - Sample: Buffer pixel type (must have DepthTest interface)
// - Shader: Logic to determine pixel color based on barycentric weights.
//   Uses compile-time duck typing: Shader must provide
//   Blend(Sample*, float z, float bc[3]). No vtable overhead.
template <typename Sample, typename Shader>
inline void Rasterize(Sample* buf, int w, int h, Shader* s, const int* v[3], bool dblsided)
{
	// each v[i] must point to 4 ints: {x,y,z,f} where f should indicate culling bits (can be 0)
	// shader must implement: bool Shader::Fill(Sample* s, int bc[3])
	// where bc contains 3 barycentric weights which are normalized to 0x8000 (use '>>15' after averaging)
	// Sample must implement bool DepthTest(int z, int divisor);
	// it must return true if z/divisor passes depth test on this sample
	// if test passes, it should write new z/d to sample's depth (if something like depth write mask is enabled)

	// EDGE FUNCTION MATH DERIVATION:
	// The edge function for edge (a->b) evaluated at point c is:
	//   e(a,b,c) = (b.x - a.x)*(c.y - a.y) - (b.y - a.y)*(c.x - a.x)
	// This computes the signed area of the parallelogram formed by vectors
	// (a->b) and (a->c). The sign tells us which side of edge (a->b) point c
	// lies on. If all 3 edge functions for a triangle's edges have the same
	// sign at point p, then p is inside the triangle. The 2x factor in BC_A
	// gives the full triangle area (sum of 3 edge functions = signed area).

	if ((v[0][3] & v[1][3] & v[2][3]) == 0)
	{
		int area = RasterCellArea(v[0],v[1],v[2]);

		if (area > 0)
		{
			if (area >= 0x10000)
				return;			

			float normalizer = (1.0f - FLT_EPSILON) / area;

			// canvas intersection with triangle bbox
			int left = std::max(0, std::min(v[0][0], std::min(v[1][0], v[2][0])));
			int right = std::min(w, std::max(v[0][0], std::max(v[1][0], v[2][0])));
			int bottom = std::max(0, std::min(v[0][1], std::min(v[1][1], v[2][1])));
			int top = std::min(h, std::max(v[0][1], std::max(v[1][1], v[2][1])));

			Sample* col = buf + bottom * w + left;
			for (int y = bottom; y < top; y++, col+=w)
			{
				Sample* row = col;
				for (int x = left; x < right; x++, row++)
				{
					int p[2] = { x,y };

					int bc[3] =
					{
						RasterCellPoint(v[1], v[2], p),
						RasterCellPoint(v[2], v[0], p),
						RasterCellPoint(v[0], v[1], p)
					};

					// WHY all-positive test: for CCW winding, all 3 edge functions
					// are positive inside the triangle. Negative = outside.
					if (bc[0] < 0 || bc[1] < 0 || bc[2] < 0)
						continue;

					// WHY edge pairing: when bc[i]==0, the pixel lies exactly ON an
					// edge. Without this tie-breaking rule, adjacent triangles sharing
					// that edge would both claim the pixel (double-draw). The x-coord
					// comparison ensures exactly one triangle "owns" each shared edge.
					if (bc[0] == 0 && v[1][0] <= v[2][0] ||
						bc[1] == 0 && v[2][0] <= v[0][0] ||
						bc[2] == 0 && v[0][0] <= v[1][0])
					{
						continue;
					}

					// assert(bc[0] + bc[1] + bc[2] == area);

					// WHY normalize: convert integer edge function values to [0,1]
					// barycentric weights for attribute interpolation (depth, UV, color)
					float nbc[3] =
					{
						bc[0] * normalizer,
						bc[1] * normalizer,
						bc[2] * normalizer
					};

					float z = nbc[0] * v[0][2] + nbc[1] * v[1][2] + nbc[2] * v[2][2];
					s->Blend(row,z,nbc);
				}
			}
		}
		else
		if (area < 0 && dblsided)
		{
			if (area <= -0x10000)
				return;
			assert(area > -0x10000);
			float normalizer = (1.0f - FLT_EPSILON) / area;

			// canvas intersection with triangle bbox
			int left = std::max(0, std::min(v[0][0], std::min(v[1][0], v[2][0])));
			int right = std::min(w, std::max(v[0][0], std::max(v[1][0], v[2][0])));
			int bottom = std::max(0, std::min(v[0][1], std::min(v[1][1], v[2][1])));
			int top = std::min(h, std::max(v[0][1], std::max(v[1][1], v[2][1])));

			Sample* col = buf + bottom * w + left;
			for (int y = bottom; y < top; y++, col += w)
			{
				Sample* row = col;
				for (int x = left; x < right; x++, row++)
				{
					int p[2] = { x,y };

					int bc[3] =
					{
						RasterCellPoint(v[1], v[2], p),
						RasterCellPoint(v[2], v[0], p),
						RasterCellPoint(v[0], v[1], p)
					};

					if (bc[0] > 0 || bc[1] > 0 || bc[2] > 0)
						continue;

					// edge pairing
					if (bc[0] == 0 && v[1][0] <= v[2][0] ||
						bc[1] == 0 && v[2][0] <= v[0][0] ||
						bc[2] == 0 && v[0][0] <= v[1][0])
					{
						continue;
					}

					// assert(bc[0] + bc[1] + bc[2] == area);

					float nbc[3] =
					{
						bc[0] * normalizer,
						bc[1] * normalizer,
						bc[2] * normalizer
					};

					float z = nbc[0] * v[0][2] + nbc[1] * v[1][2] + nbc[2] * v[2][2];
					s->Blend(row, z, nbc);
				}
			}
		}
	}
}

template <typename Sample, typename Shader, int VaryingCount>
inline void RasterizeVarying(Sample* buf, int w, int h, Shader* s, const int* v[3], bool dblsided, const float varying[3][VaryingCount])
{
	static_assert(VaryingCount > 0, "RasterizeVarying requires z in varying slot 0");

	if ((v[0][3] & v[1][3] & v[2][3]) != 0)
		return;

	int area = RasterCellArea(v[0], v[1], v[2]);
	if (area == 0)
		return;
	if (area >= 0x10000 || area <= -0x10000)
		return;
	if (area < 0 && !dblsided)
		return;

	float normalizer = (1.0f - FLT_EPSILON) / (float)area;
	bool positive = area > 0;
	bool fallback = (area > -64 && area < 64);

	float origin[VaryingCount];
	float grad_x[VaryingCount];
	float grad_y[VaryingCount];
	if (!fallback)
		RasterComputeVaryingGradients<VaryingCount>(v, varying, area, origin, grad_x, grad_y);

	int left = std::max(0, std::min(v[0][0], std::min(v[1][0], v[2][0])));
	int right = std::min(w, std::max(v[0][0], std::max(v[1][0], v[2][0])));
	int bottom = std::max(0, std::min(v[0][1], std::min(v[1][1], v[2][1])));
	int top = std::min(h, std::max(v[0][1], std::max(v[1][1], v[2][1])));

	Sample* col = buf + bottom * w + left;
	for (int y = bottom; y < top; y++, col += w)
	{
		float row_vary[VaryingCount];
		if (!fallback)
		{
			for (int i = 0; i < VaryingCount; i++)
				row_vary[i] = origin[i] + ((float)left + 0.5f) * grad_x[i] + ((float)y + 0.5f) * grad_y[i];
		}

		Sample* row = col;
		for (int x = left; x < right; x++, row++)
		{
			int p[2] = { x, y };
			int bc[3] =
			{
				RasterCellPoint(v[1], v[2], p),
				RasterCellPoint(v[2], v[0], p),
				RasterCellPoint(v[0], v[1], p)
			};

			if (positive)
			{
				if (bc[0] < 0 || bc[1] < 0 || bc[2] < 0)
					goto advance_varyings;
			}
			else
			{
				if (bc[0] > 0 || bc[1] > 0 || bc[2] > 0)
					goto advance_varyings;
			}

			if (bc[0] == 0 && v[1][0] <= v[2][0] ||
				bc[1] == 0 && v[2][0] <= v[0][0] ||
				bc[2] == 0 && v[0][0] <= v[1][0])
			{
				goto advance_varyings;
			}

			if (fallback)
			{
				float nbc[3] =
				{
					bc[0] * normalizer,
					bc[1] * normalizer,
					bc[2] * normalizer
				};
				float vr[VaryingCount];
				for (int i = 0; i < VaryingCount; i++)
					vr[i] = nbc[0] * varying[0][i] + nbc[1] * varying[1][i] + nbc[2] * varying[2][i];
				s->BlendVR(row, vr[0], vr);
			}
			else
			{
				s->BlendVR(row, row_vary[0], row_vary);
			}

advance_varyings:
			if (!fallback)
			{
				for (int i = 0; i < VaryingCount; i++)
					row_vary[i] += grad_x[i];
			}
		}
	}
}


// WHY Sample struct: Each sample in the 2x supersampled buffer stores the
// rasterized result for one sub-pixel. visual holds either a material index
// (terrain: mat | shade<<8 | elev<<15) or RGB555 (meshes, flagged by spare&0x8).
// diffuse is the lighting value (0-255). spare packs multiple bit flags:
// bit 0-1: parity (0=empty, 1=odd, 2=even, 3=reflection),
