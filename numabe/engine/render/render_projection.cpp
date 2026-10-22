// render_projection.cpp — projection/unprojection extracted from render.cpp
#include <math.h>
#include "render_internal.h"
#include "matrix.h"
// FL-4257 corrective (2026-06-11) — the Phase 1 spherical render branch
// (render_projection_spherical.cpp, a sinc(arc) screen warp around the
// Euclidean divide with a render-owned scene-scale constant) was REJECTED
// and hard-deleted: it synthesized curvature scale inside the renderer
// instead of consuming server-owned S3 state, and never changed the
// geometry model. Spherical projection now lives in the single 4D owner
// (render_s3.cpp); the entries below route there and FAIL CLOSED on any
// published curvature the owner does not recognise (Law 6).
#include "render_s3.h"

static inline bool RenderProjectionCurvedPublished(const Renderer* r)
{
    return r && r->curvature.present == 1 &&
           r->curvature.kind != ASCK_CURVATURE_KIND_EUCLIDEAN;
}


// WHY ProjectCoords: Maps a 3D world position to 2D screen coordinates
// (in AnsiCell units). Used by gameplay code to position UI elements
// (name labels, HP bars) relative to world objects. Supports both
// isometric and perspective projection paths.
bool ProjectCoords(Renderer* r, const float pos[3], int view[3])
{
	// FL-4257 — published non-Euclidean curvature routes to the single 4D
	// spherical owner (render_s3.cpp), which itself fails closed on any
	// unrecognised kind (Law 6). Absent presence = Euclidean baseline.
	if (RenderProjectionCurvedPublished(r))
		return RenderS3ProjectCoords(r, pos, view);

	// TODO: add perspective!
	float w_pos[3] = { pos[0] * HEIGHT_CELLS, pos[1] * HEIGHT_CELLS, pos[2] };

	if (r->perspective)
	{

		float vx = w_pos[0], vy = w_pos[1], vz = w_pos[2];
		float viewer_dist; // {vx,vy,vz}  r->pos
		float eye_to_vtx[3] =
		{
			vx - r->view_pos[0],
			vy - r->view_pos[1],
			vz - r->view_pos[2],
		};

		viewer_dist = DotProduct(eye_to_vtx, r->view_dir);

		if (viewer_dist <= 0)
			return false;

		float fx = (float)(r->mul[0] * vx + r->mul[2] * vy + r->add[0]);
		float fy = (float)(r->mul[1] * vx + r->mul[3] * vy + r->mul[5] * vz + r->add[1]);

		float recp_dist = 1.0f / viewer_dist;

		fx = (fx - r->view_ofs[0]) * recp_dist + r->view_ofs[0];
		fy = (fy - r->view_ofs[1]) * recp_dist + r->view_ofs[1];

		int tx = (int)floorf(fx + 0.5f);
		int ty = (int)floorf(fy + 0.5f);

		view[0] = (tx - 1) >> 1;
		view[1] = (ty - 1) >> 1;
		view[2] = (int)floorf(w_pos[2] + 0.5f) + HEIGHT_SCALE / 2;
	}
	else
	{
		int tx = (int)floor(r->mul[0] * w_pos[0] + r->mul[2] * w_pos[1] + 0.5 + r->add[0]);
		int ty = (int)floor(r->mul[1] * w_pos[0] + r->mul[3] * w_pos[1] + r->mul[5] * w_pos[2] + 0.5 + r->add[1]);

		view[0] = (tx - 1) >> 1;
		view[1] = (ty - 1) >> 1;
		view[2] = (int)floorf(w_pos[2] + 0.5f) + HEIGHT_SCALE / 2;
	}

	return true;
}


// WHY UnprojectCoords2D: Inverse of ProjectCoords — maps a 2D screen
// position back to 3D world coords by reading the depth from SampleBuffer.
// Used for mouse picking (click on screen -> find world object).
bool UnprojectCoords2D(Renderer* r, const int xy[2], float pos[3])
{
	// FL-4257 — spherical unprojection is owned by render_s3.cpp (arc-depth
	// inverse); the Euclidean inverse below never runs under curvature.
	if (RenderProjectionCurvedPublished(r))
		return RenderS3UnprojectCoords2D(r, xy, pos);

	int w = (r->sample_buffer.w - 4) / 2;
	int h = (r->sample_buffer.h - 4) / 2;

	if (xy[0] < 0 || xy[1] < 0 || xy[0] >= w || xy[1] >= h)
		return false;

	// readback height (max of 4 samples)
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
	if (height < sh[1])
		height = sh[1];
	if (height < sh[2])
		height = sh[2];
	if (height < sh[3])
		height = sh[3];

	if (r->perspective)
	{
		int xyz[3] = {xy[0], xy[1], (int)floorf(height+0.5f)};
		return UnprojectCoords3D(r,xyz,pos);
	}
	else
	{
		double p[4] = { (double)x,(double)y,(double)height,1.0 };
		double w[4];
		Product(r->inv_tm, p, w);
		pos[0] = (float)(w[0]);
		pos[1] = (float)(w[1]);
		pos[2] = (float)(w[2]);
	}

	return true;
}

bool UnprojectCoords3D(Renderer* r, const int xyz[3], float pos[3])
{
	// FL-4257 — spherical 3D unprojection is owned by render_s3.cpp.
	if (RenderProjectionCurvedPublished(r))
		return RenderS3UnprojectCoords3D(r, xyz, pos);

	// readback height (max of 4 samples)

	if (r->perspective)
	{
		float tm0 = (float)(r->mul[0]);
		float tm1 = (float)(r->mul[1]);
		float tm4 = (float)(r->mul[2]);
		float tm5 = (float)(r->mul[3]);

		float tm8 = 0;
		float tm9 = (float)(r->mul[5]);
		float tm12 = (float)(r->add[0]);
		float tm13 = (float)(r->add[1]);

		float z = (float)(xyz[2]);

		float ww_x, ww_y, ww_c, wx_x, wx_y, wx_c, wy_x, wy_y, wy_c;
		ww_x = r->view_dir[0]*tm5 - r->view_dir[1]*tm1;
		ww_y = r->view_dir[1]*tm0 - r->view_dir[0]*tm4;
		ww_c = tm1*tm4 - tm0*tm5;
		wx_x = (r->view_pos[0]*tm5*r->view_dir[0] + r->view_dir[1]*(-r->view_ofs[1] + r->view_pos[1]*tm5 + tm13 + tm9*z));
		wx_y = (r->view_pos[0]*tm4*r->view_dir[0] + r->view_dir[1]*(-r->view_ofs[0] + r->view_pos[1]*tm4 + tm12 + tm8*z));
		wx_c = tm5*(-r->view_ofs[0] + tm12 + tm8*z) + tm4*(r->view_ofs[1] - tm13 - tm9*z);
		wy_x = (r->view_pos[1]*tm1*r->view_dir[1] + r->view_dir[0]*(-r->view_ofs[1] + r->view_pos[0]*tm1 + tm13 + tm9*z));
		wy_y = (r->view_pos[1]*tm0*r->view_dir[1] + r->view_dir[0]*(-r->view_ofs[0] + r->view_pos[0]*tm0 + tm12 + tm8*z));
		wy_c = tm1*(r->view_ofs[0] - tm12 - tm8*z) + tm0*(-r->view_ofs[1] + tm13 + tm9*z);


		float sx_dx = 2.0f*xyz[0]+2  - r->view_ofs[0];
		float sy_dy = 2.0f*xyz[1]+2 - r->view_ofs[1];

		float ww = (sx_dx*ww_x + sy_dy*ww_y + ww_c);
		if (ww<0)
		{
			ww = 1.0f/ww;
			float wx = ww * (wx_c + wx_x * sx_dx - wx_y * sy_dy);
			float wy = ww * (wy_c - wy_x * sx_dx + wy_y * sy_dy);

			pos[0] = wx;
			pos[1] = wy;
			pos[2] = z;
		}
		else
		{
			return false;
		}
	}
	else
	{
		double p[4] = { 2*xyz[0] + 1.0, 2*xyz[1] + 1.0, (double)xyz[2], 1.0 };
		double w[4];
		Product(r->inv_tm, p, w);
		pos[0] = (float)(w[0]);
		pos[1] = (float)(w[1]);
		pos[2] = (float)(w[2]);
	}

	return true;
}


// =============================================================================
// FL-4257 Phase 1 S3 — world-vertex projection helpers (Euclidean baseline).
//
// These helpers exist so render_world_pass.cpp's patch loop and
// render_sprite_blit.cpp's sprite loop can stop reading r->mul, r->add,
// r->view_pos, r->view_dir, r->view_ofs directly. The Euclidean math here
// is BYTE-IDENTICAL to the pre-S3 inline code: same operator order, same
// intermediate temporaries, same FP rounding.
// =============================================================================

#include "render_world_projection.h"

// Patch form (factored add — matches render_world_pass.cpp:631+ / :707+).
// viewer_dist out is POSITIVE on success; caller inverts if it needs 1/dist.
static inline bool ProjectWorldVertexPatchPerspEuclidean(
	Renderer* r, float vx, float vy, float vz,
	float* fx_out, float* fy_out, float* viewer_dist_out)
{
	double* mul = r->mul;
	double* add = r->add;

	float eye_to_vtx[3] =
	{
		vx - r->view_pos[0],
		vy - r->view_pos[1],
		vz - r->view_pos[2],
	};
	float viewer_dist = DotProduct(eye_to_vtx, r->view_dir);
	if (viewer_dist <= 0.0f)
		return false;

	float inv_dist = 1.0f / viewer_dist;

	float fx = (float)(mul[0] * vx + mul[2] * vy);
	float fy = (float)(mul[1] * vx + mul[3] * vy + mul[5] * vz);

	fx *= inv_dist;
	fy *= inv_dist;

	float qx = (float)((add[0] - r->view_ofs[0]) * inv_dist + r->view_ofs[0]);
	float qy = (float)((add[1] - r->view_ofs[1]) * inv_dist + r->view_ofs[1]);

	fx += qx;
	fy += qy;

	*fx_out = fx;
	*fy_out = fy;
	*viewer_dist_out = viewer_dist;
	return true;
}

// Sprite form (full add inside parens — matches render_sprite_blit.cpp:437+
// / :516+, and the form used by ProjectCoords). FP rounding pattern differs
// from the patch form even though algebraically equivalent.
static inline bool ProjectWorldVertexSpritePerspEuclidean(
	Renderer* r, float vx, float vy, float vz,
	float* fx_out, float* fy_out, float* viewer_dist_out)
{
	float eye_to_vtx[3] =
	{
		vx - r->view_pos[0],
		vy - r->view_pos[1],
		vz - r->view_pos[2],
	};
	float viewer_dist = DotProduct(eye_to_vtx, r->view_dir);
	if (viewer_dist <= 0.0f)
		return false;

	float fx = (float)(r->mul[0] * vx + r->mul[2] * vy + r->add[0]);
	float fy = (float)(r->mul[1] * vx + r->mul[3] * vy + r->mul[5] * vz + r->add[1]);

	float recp_dist = 1.0f / viewer_dist;
	fx = (fx - r->view_ofs[0]) * recp_dist + r->view_ofs[0];
	fy = (fy - r->view_ofs[1]) * recp_dist + r->view_ofs[1];

	*fx_out = fx;
	*fy_out = fy;
	*viewer_dist_out = viewer_dist;
	return true;
}

// Ortho int_flag form (matches render_world_pass.cpp:679+/754+ and
// render_sprite_blit.cpp:464+).
static inline bool ProjectWorldVertexOrthoIntFlagEuclidean(
	Renderer* r, float vx, float vy, float vz,
	int* tx_out, int* ty_out)
{
	*tx_out = (int)floor(r->mul[0] * vx + r->mul[2] * vy + 0.5 + r->add[0]);
	*ty_out = (int)floor(r->mul[1] * vx + r->mul[3] * vy + r->mul[5] * vz + 0.5 + r->add[1]);
	return true;
}

// Ortho iadd form (matches render_world_pass.cpp:692+/769+). iadd is
// derived from r->add cast to int, applied OUTSIDE the floor.
static inline bool ProjectWorldVertexOrthoIaddEuclidean(
	Renderer* r, float vx, float vy, float vz,
	int* tx_out, int* ty_out)
{
	int iadd0 = (int)r->add[0];
	int iadd1 = (int)r->add[1];
	*tx_out = (int)floor(r->mul[0] * vx + r->mul[2] * vy + 0.5) + iadd0;
	*ty_out = (int)floor(r->mul[1] * vx + r->mul[3] * vy + r->mul[5] * vz + 0.5) + iadd1;
	return true;
}

// Face-form perspective (mesh-triangle vertex; matches
// render_world_pass.cpp:241+/288+/340+). Caller has already done
// r->viewinst_tm and r->inst_tm transforms; helper applies the perspective
// divide using the cached view fields.
static inline bool ProjectFaceVertexPerspEuclidean(
	Renderer* r,
	float pre_fx, float pre_fy,
	float ws_x, float ws_y, float ws_z,
	float* fx_out, float* fy_out, float* viewer_dist_out)
{
	float eye_to_vtx[3] =
	{
		ws_x * HEIGHT_CELLS - r->view_pos[0],
		ws_y * HEIGHT_CELLS - r->view_pos[1],
		ws_z - r->view_pos[2],
	};
	float viewer_dist = DotProduct(eye_to_vtx, r->view_dir);
	if (viewer_dist <= 0.0f)
		return false;

	float inv_dist = 1.0f / viewer_dist;
	float fx = (pre_fx - r->view_ofs[0]) * inv_dist + r->view_ofs[0];
	float fy = (pre_fy - r->view_ofs[1]) * inv_dist + r->view_ofs[1];

	*fx_out = fx;
	*fy_out = fy;
	*viewer_dist_out = viewer_dist;
	return true;
}

// --- Dispatchers ------------------------------------------------------------
// Each one is the public entrypoint world_pass / sprite_blit calls. The
// dispatcher reads the cached server-authoritative AsciCurvatureView and
// routes accordingly. There is no fallback that smuggles in a Euclidean
// default when curvature is present-but-invalid; Law 6 says fail closed.

bool ProjectWorldVertexPatchPersp(
	Renderer* r, float vx, float vy, float vz,
	float* fx, float* fy, float* viewer_dist)
{
	if (RenderProjectionCurvedPublished(r))
		return RenderS3ProjectWorldVertex(r, vx, vy, vz, fx, fy, viewer_dist);
	return ProjectWorldVertexPatchPerspEuclidean(r, vx, vy, vz, fx, fy, viewer_dist);
}

bool ProjectWorldVertexSpritePersp(
	Renderer* r, float vx, float vy, float vz,
	float* fx, float* fy, float* viewer_dist)
{
	if (RenderProjectionCurvedPublished(r))
		return RenderS3ProjectWorldVertex(r, vx, vy, vz, fx, fy, viewer_dist);
	return ProjectWorldVertexSpritePerspEuclidean(r, vx, vy, vz, fx, fy, viewer_dist);
}

// Ortho has no spherical sibling in Phase 1. Spherical+ortho fails closed.
bool ProjectWorldVertexOrthoIntFlag(
	Renderer* r, float vx, float vy, float vz,
	int* tx, int* ty)
{
	if (RenderProjectionCurvedPublished(r))
		return false; // no spherical ortho: fail closed (Law 6)
	return ProjectWorldVertexOrthoIntFlagEuclidean(r, vx, vy, vz, tx, ty);
}

bool ProjectWorldVertexOrthoIadd(
	Renderer* r, float vx, float vy, float vz,
	int* tx, int* ty)
{
	if (RenderProjectionCurvedPublished(r))
		return false; // no spherical ortho: fail closed (Law 6)
	return ProjectWorldVertexOrthoIaddEuclidean(r, vx, vy, vz, tx, ty);
}

bool ProjectFaceVertexPersp(
	Renderer* r,
	float pre_fx, float pre_fy,
	float ws_x, float ws_y, float ws_z,
	float* fx, float* fy, float* viewer_dist)
{
	if (RenderProjectionCurvedPublished(r))
		return RenderS3ProjectFaceVertex(r, ws_x, ws_y, ws_z, fx, fy, viewer_dist);
	return ProjectFaceVertexPerspEuclidean(r, pre_fx, pre_fy, ws_x, ws_y, ws_z, fx, fy, viewer_dist);
}

bool ReadEuclideanProjectionFrameV1(
	const Renderer* r, RenderEuclideanProjectionFrameV1* out)
{
	if (!r || !out || RenderProjectionCurvedPublished(r))
		return false;
	for (int axis = 0; axis < 3; ++axis)
	{
		out->view_pos[axis] = r->view_pos[axis];
		out->view_dir[axis] = r->view_dir[axis];
	}
	out->view_ofs[0] = r->view_ofs[0];
	out->view_ofs[1] = r->view_ofs[1];
	return true;
}

void BuildMeshViewTransformEuclidean(Renderer* r, bool reflect, double view_tm_out[16])
{
	// FL-4257 Phase 1 S3 — moved from render_world_pass.cpp RenderMesh inline
	// construction. Matrix bundling stays Euclidean for Phase 1; the per-vertex
	// spherical adjustment happens in ProjectFaceVertexPerspSpherical using
	// the same pre_fx/pre_fy this transform produces. Bundling a spherical
	// view_tm matrix is Phase 2 work.
	view_tm_out[0]  = r->mul[0] * HEIGHT_CELLS;
	view_tm_out[1]  = r->mul[1] * HEIGHT_CELLS;
	view_tm_out[2]  = 0.0;
	view_tm_out[3]  = 0.0;
	view_tm_out[4]  = r->mul[2] * HEIGHT_CELLS;
	view_tm_out[5]  = r->mul[3] * HEIGHT_CELLS;
	view_tm_out[6]  = 0.0;
	view_tm_out[7]  = 0.0;
	view_tm_out[8]  = r->mul[4];
	view_tm_out[9]  = r->mul[5];
	view_tm_out[10] = reflect ? -1.0 : 1.0;
	view_tm_out[11] = 0.0;
	view_tm_out[12] = r->add[0];
	view_tm_out[13] = r->add[1];
	view_tm_out[14] = r->add[2];
	view_tm_out[15] = 1.0;
}
