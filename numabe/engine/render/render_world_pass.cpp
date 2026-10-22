// render_world_pass.cpp — World/mesh/patch rendering pipeline
//
// Extracted from engine/render.cpp.
// SEE ALSO: render.h, render_internal.h
//
// FL-4257 Phase 1 S3 — every patch-vertex projection now routes through
// engine/render/render_projection.cpp's ProjectWorldVertex* dispatchers so
// the cached server-authoritative AsciCurvatureView is the SOLE projection
// ownership entry. No r->mul / r->add / r->view_pos / r->view_dir /
// r->view_ofs reads remain in this file.

#include "render_rasterize.h"
#include "matrix.h"
#include "game.h"
#include "renderer_ports_v1.h"
#include "snapshot_npc_sprite_data_tag.h"
#include "render_world_projection.h"
#include <algorithm>
#include <cassert>
#include <cmath>
#include <climits>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <vector>
#include "render_s3.h"
// (mesh-differential include moved with its sole consumer to the Current module.)
#include "fl4652_upstream_tonal_base.h"

static int create_auto_mat(uint8_t mat[]);
uint8_t auto_mat[/*b*/32/*g*/ * 32/*r*/ * 32/*bg,fg,gl*/ * 3];
int auto_mat_result = create_auto_mat(auto_mat);

static uint64_t RenderFnv1a64(const void* data, size_t size, uint64_t seed = 1469598103934665603ULL)
{
	const uint8_t* p = (const uint8_t*)data;
	uint64_t h = seed;
	for (size_t i = 0; i < size; i++) { h ^= p[i]; h *= 1099511628211ULL; }
	return h;
}

static int RenderClampInt(int value, int lo, int hi)
{
	return value < lo ? lo : (value > hi ? hi : value);
}

static uint64_t RenderMeshInstanceUid(Inst* inst)
{
	return GetInstPersistentUid(inst);
}

static uint32_t RenderStablePrimitiveId(const float coords[9])
{
	int32_t q[9];
	for (int i = 0; i < 9; i++) q[i] = (int32_t)lroundf(coords[i] * 256.0f);
	const uint64_t h = RenderFnv1a64(q, sizeof(q));
	return (uint32_t)(h ^ (h >> 32));
}

// FL-4512 seam: projected-triangle registration helpers are Current-owned (module).

// FL-4512 seam: stroke capture/admission/reprojection/reachability are Current-owned (module).

// FL-4512 seam: Active-Stroke/projection finalization is Current-owned (module).

// FL-4512 seam: lattice math helpers are Current-owned (module).
// FL-4512 seam: the U2 kind-1 lattice producer is Current-owned (module).
// FL-4512 seam: projected-feature classification is Current-owned (module).
// FL-4512 seam: vertex-index/suggestive-init helpers are Current-owned (module).
// FL-4512 seam: kind-4 suggestive emission is Current-owned (module).
static int create_auto_mat(uint8_t mat[])
{
	/*
	#define FLO(x) ((int)floor(5 * x / 31.0f))
	#define REM(x) (5*x-31*flo[x])
	*/

	#define MCV 5
	#define MCV_TO_5(mcv) (((mcv) * 5 + MCV/2) / MCV)
	#define FLO(x) ((int)floor(MCV * x / 31.0f))
	#define REM(x) (MCV*x-31*flo[x])

	static const int flo[32] =
	{
		FLO(0),  FLO(1),  FLO(2),  FLO(3),
		FLO(4),  FLO(5),  FLO(6),  FLO(7),
		FLO(8),  FLO(9),  FLO(10), FLO(11),
		FLO(12), FLO(13), FLO(14), FLO(15),
		FLO(16), FLO(17), FLO(18), FLO(19),
		FLO(20), FLO(21), FLO(22), FLO(23),
		FLO(24), FLO(25), FLO(26), FLO(27),
		FLO(28), FLO(29), FLO(30), FLO(31),
	};

	static const int rem[32]=
	{
		REM(0),  REM(1),  REM(2),  REM(3),
		REM(4),  REM(5),  REM(6),  REM(7),
		REM(8),  REM(9),  REM(10), REM(11),
		REM(12), REM(13), REM(14), REM(15),
		REM(16), REM(17), REM(18), REM(19),
		REM(20), REM(21), REM(22), REM(23),
		REM(24), REM(25), REM(26), REM(27),
		REM(28), REM(29), REM(30), REM(31),
	};

	static const char glyph[] = " ..::%";

	int max_pr = 0;

	int i = 0;
	for (int b=0; b<32; b++)
	{
		int p[3];
		p[2] = rem[b];
		int B[2] = { flo[b],std::min(MCV, flo[b] + 1) };
		for (int g = 0; g < 32; g++)
		{
			p[1] = rem[g];
			int G[2] = { flo[g],std::min(MCV, flo[g] + 1) };
			for (int r = 0; r < 32; r++,i++)
			{
				p[0] = rem[r];
				int R[2] = { flo[r],std::min(MCV, flo[r] + 1) };

				float best_sd = -1;
				float best_pr;
				int best_lo;
				int best_hi;

				// check all pairs of 8 cube verts
				for (int lo = 0; lo < 7; lo++)
				{
					int v0[3] = { R[lo & 1], G[(lo & 2) >> 1], B[(lo & 4) >> 2] };

					int pv0[3]=
					{
						R[0] * 31 + p[0] - v0[0] * 31,
						G[0] * 31 + p[1] - v0[1] * 31,
						B[0] * 31 + p[2] - v0[2] * 31,
					};

					for (int hi = lo + 1; hi < 8; hi++)
					{
						int v1[3] = { R[hi & 1], G[(hi & 2) >> 1], B[(hi & 4) >> 2] };
						int v10[3] = { 31*(v1[0] - v0[0]), 31*(v1[1] - v0[1]), 31*(v1[2] - v0[2]) };

						int v10_sqrlen = v10[0] * v10[0] + v10[1] * v10[1] + v10[2] * v10[2];

						float pr = v10_sqrlen ? (v10[0] * pv0[0] + v10[1] * pv0[1] + v10[2] * pv0[2]) / (float)v10_sqrlen : 0.0f;

						// projection point
						float prp[3] = { v10[0] * pr, v10[1] * pr, v10[2] * pr };

						// dist vect
						float prv[3] = { pv0[0] - prp[0], pv0[1] - prp[1], pv0[2] - prp[2] };

						// square dist
						float sd = sqrtf(prv[0] * prv[0] + prv[1] * prv[1] + prv[2] * prv[2]);

						if (sd < best_sd || best_sd < 0)
						{
							best_sd = sd;
							best_pr = pr;
							best_lo = lo;
							best_hi = hi;
						}
					}
				}

				int idx = 3 * (r + 32 * (g + 32 * b));
				int shd = (int)floorf( best_pr * 11 + 0.5f );

				if (shd > 11)
				{
					shd = 11;
				}

				if (shd < 0)
				{
					shd = 0;
				}

				if (shd < 6)
				{
					mat[idx + 0] = 16 + 36 * MCV_TO_5(R[best_lo & 1]) + 6 * MCV_TO_5(G[(best_lo & 2) >> 1]) + MCV_TO_5(B[(best_lo & 4) >> 2]);
					mat[idx + 1] = 16 + 36 * MCV_TO_5(R[best_hi & 1]) + 6 * MCV_TO_5(G[(best_hi & 2) >> 1]) + MCV_TO_5(B[(best_hi & 4) >> 2]);
					mat[idx + 2] = glyph[shd];
				}
				else
				{
					mat[idx + 0] = 16 + 36 * MCV_TO_5(R[best_hi & 1]) + 6 * MCV_TO_5(G[(best_hi & 2) >> 1]) + MCV_TO_5(B[(best_hi & 4) >> 2]);
					mat[idx + 1] = 16 + 36 * MCV_TO_5(R[best_lo & 1]) + 6 * MCV_TO_5(G[(best_lo & 2) >> 1]) + MCV_TO_5(B[(best_lo & 4) >> 2]);
					mat[idx + 2] = glyph[11-shd];
				}
			}
		}
	}

	return 1;
}

// WHY RenderFace: Callback for QueryMesh — receives a single triangle face
// from a 3D mesh. Transforms vertices from model space through the combined
// view*instance matrix, computes per-vertex continuous tone from transported
// normal, then delegates to Rasterize<> with an inline Shader that writes
// RGB555 + diffuse into the SampleBuffer (flagged with spare|0x8 for mesh).
void Renderer::RenderFace(float coords[9], uint8_t colors[12], float normals[9],
	uint8_t normal_source, uint32_t visual, void* cookie)
{
	struct Shader
	{
		bool WinsMeshDepth(Sample* s, float z) const
		{
			if (s->height < z)
				return true;
			if (s->height != z || !fl4208_prov || !sample_base)
				return false;
			const ptrdiff_t pidx = s - sample_base;
			if (pidx < 0 || pidx >= fl4208_cap)
				return false;
			const Fl4208ProvisionalId& previous = fl4208_prov[pidx];
			return previous.valid && previous.owner_kind == FL4208_OWNER_MESH &&
			       primitive_id < previous.primitive_id;
		}

		// FL-4936 P2: mesh_owner.v1 is a full SHA-256 of (uid, primitive_id).
		// It used to run for every face every frame, including faces that never
		// win a sample. Only WriteMeshIdentity reads it, so the digest is now
		// computed on the first sample this face actually publishes and reused
		// for the rest of the face. Published owner_x/owner_y bytes are unchanged.
		uint64_t OwnerHash()
		{
			if (!owner_hash_ready)
			{
				owner_hash64 = Fl4208MeshOwnerHashV1(mesh_instance_uid, primitive_id);
				owner_hash_ready = true;
			}
			return owner_hash64;
		}

		void WriteMeshIdentity(Sample* s, const float* vr)
		{
			if (!fl4208_prov || !sample_base)
				return;
			const ptrdiff_t pidx = s - sample_base;
			if (pidx < 0 || pidx >= fl4208_cap)
				return;
			Fl4208ProvisionalId* out = fl4208_prov + pidx;
			memset(out, 0, sizeof(*out));
			// Production reaches RenderFace only after RenderMesh establishes
			// a valid sheet.  A direct diagnostic call may still exercise
			// raster math, but it cannot publish an owner without that fact.
			if (visibility_sheet == RENDER_VISIBILITY_SHEET_INVALID)
				return;
				const float recip_w = vr[9];
				if (!(recip_w > 0.0f) || mesh_instance_uid == 0)
					return;
				const float b1 = vr[7] / recip_w;
				const float b2 = vr[8] / recip_w;
			out->world_generation = world_generation;
			out->mesh_instance_uid = mesh_instance_uid;
			out->primitive_id = primitive_id;
			const uint64_t owner_hash = OwnerHash();
			out->owner_x = (int32_t)(uint32_t)owner_hash;
			out->owner_y = (int32_t)(uint32_t)(owner_hash >> 32);
			out->anchor_u_q = Fl4208MeshAnchorQuantV1(b1);
			out->anchor_v_q = Fl4208MeshAnchorQuantV1(b2);
			out->material = (uint16_t)face_material_id;
			out->owner_kind = FL4208_OWNER_MESH;
			// The current invocation computed this sheet before QueryMesh.
			// Store it before valid=1 so readers never observe a complete
			// provisional identity paired with a stale sheet byte.
			out->visibility_sheet = visibility_sheet;
			out->valid = 1;
		}

			void BlendVR(Sample*s, float z, const float* vr)
			{
				if (WinsMeshDepth(s, z))
				{
					const float recip_w = vr[9];
					// FL-4652 CT-1: every progression revision reads the same
					// face-constant upstream tonal byte. The deleted revision
					// switch cannot revive smooth vertex-normal presentation.
					float tone_f = (float)upstream_tonal_base_diffuse;
					if (tone_f < 0.0f) tone_f = 0.0f;
					if (tone_f > 255.0f) tone_f = 255.0f;
					const uint8_t tone_u8 = (uint8_t)floorf(tone_f + 0.5f);
					// Mu is pure varying math (shared inputs); the installed
					// product's slot consumes it, the Classic slot ignores it.
					float mu_f = recip_w > 0.0f ? vr[5] / recip_w : 0.0f;
					if (mu_f < -1.0f) mu_f = -1.0f;
					if (mu_f > 1.0f) mu_f = 1.0f;
					int mu_q15_i = (int)floorf(mu_f * 32767.0f + (mu_f >= 0.0f ? 0.5f : -0.5f));
					if (mu_q15_i < -32767) mu_q15_i = -32767;
					if (mu_q15_i > 32767) mu_q15_i = 32767;
					if (reflection_mode)
				{
					if (z < water + HEIGHT_SCALE / 8)
					{
						if (z > water)
							s->height = water;
						else
							s->height = z;

						int r8 = RenderClampInt((int)floor(vr[1]), 0, 255);
						int r5 = (r8 * 249 + 1014) >> 11;
						int g8 = RenderClampInt((int)floor(vr[2]), 0, 255);
						int g5 = (g8 * 249 + 1014) >> 11;
						int b8 = RenderClampInt((int)floor(vr[3]), 0, 255);
						int b5 = (b8 * 249 + 1014) >> 11;

						// Mesh path: store direct RGB555 color in Sample.visual while
						// preserving the face material owner separately.
						s->visual = r5 | (g5 << 5) | (b5 << 10);
						s->material_id = face_material_id;
							s->diffuse = tone_u8;
						s->spare = (s->spare & ~0x44) | 0x8 | 0x3;
						s->glyph_dispatch_surface = GLYPH_DISPATCH_AUTOMAP_MESH_RGB;
						s->s3_parity = s3_enabled ? (uint8_t)RenderS3DepthOrientationParity(z) : 0;
						// Product tone sidecar store through the neutral slot,
						// attempted whenever a product table is installed.
						// Classic carries no tone sidecar and its slot is a no-op.
						{
							const RendererProductV1* write_nrt = AsciickerRenderer();
							if (write_nrt &&
								write_nrt->mesh_tone_write_sample != nullptr)
								write_nrt->mesh_tone_write_sample(product_state,
									sample_base, s, (int16_t)mu_q15_i);
						}
							WriteMeshIdentity(s, vr);
					}
				}
				else 
				{
					if (z >= water - HEIGHT_SCALE / 8)
					{
						if (z < water)
							s->height = water;
						else
							s->height = z;

						int r8 = RenderClampInt((int)floor(vr[1]), 0, 255);
						int r5 = (r8 * 249 + 1014) >> 11;
						int g8 = RenderClampInt((int)floor(vr[2]), 0, 255);
						int g5 = (g8 * 249 + 1014) >> 11;
						int b8 = RenderClampInt((int)floor(vr[3]), 0, 255);
						int b5 = (b8 * 249 + 1014) >> 11;

						// Mesh path: write packed color directly; material ownership is
						// carried separately for glyph-policy routing.
						s->visual = r5 | (g5 << 5) | (b5 << 10);
						s->material_id = face_material_id;
							s->diffuse = tone_u8;
						s->spare = (s->spare & ~(0x3|0x44)) | 0x8 | 0x1;
						s->glyph_dispatch_surface = GLYPH_DISPATCH_AUTOMAP_MESH_RGB;
						s->s3_parity = s3_enabled ? (uint8_t)RenderS3DepthOrientationParity(z) : 0;
						// Product tone sidecar store through the neutral slot,
						// attempted whenever a product table is installed.
						// Classic carries no tone sidecar and its slot is a no-op.
						{
							const RendererProductV1* write_nrt = AsciickerRenderer();
							if (write_nrt &&
								write_nrt->mesh_tone_write_sample != nullptr)
								write_nrt->mesh_tone_write_sample(product_state,
									sample_base, s, (int16_t)mu_q15_i);
						}
							WriteMeshIdentity(s, vr);
					}
				}
			}
		}

		/*
		inline void Diffuse(int dzdx, int dzdy)
		{
			float nl = (float)sqrt(dzdx * dzdx + dzdy * dzdy + HEIGHT_SCALE * HEIGHT_SCALE);
			float df = (dzdx * light[0] + dzdy * light[1] + HEIGHT_SCALE * light[2]) / nl;
			df = df * (1.0f - 0.5f*light[3]) + 0.5f*light[3];
			diffuse = df <= 0 ? 0 : (int)(df * 0xFF);
		}
		*/

		float water;
		bool reflection_mode;
		// FL-4652 CT-1: direct and reflected depth wins read one immutable
		// upstream per-face tonal byte. No revision-local tone owner survives.
	uint8_t upstream_tonal_base_diffuse;
	// Product state pointer for the neutral slot calls below; null while
	// Classic is installed (its create returns null). Tone context storage
	// lives product-side; no capacity/alignment contract crosses the seam.
	AsciickerProductStateV1* product_state;
			uint8_t s3_enabled;
		int16_t face_material_id;
		Sample* sample_base;
			Fl4208ProvisionalId* fl4208_prov;
			int fl4208_cap;
		uint64_t world_generation;
		uint64_t mesh_instance_uid;
		uint64_t owner_hash64;
		bool owner_hash_ready;
		uint32_t primitive_id;
		uint8_t visibility_sheet;
	} shader;

	Renderer* r = (Renderer*)cookie;
	shader.water = r->water;
	shader.reflection_mode = r->reflection_mode;
	// FL-4512 U1: every face rendered for this MeshInst receives its resolved
	// MaterialKeyV1. The retired vertex-colour-byte and fixture-constant paths
	// cannot select material identity here.
	shader.face_material_id = r->mesh_material_key;
	shader.sample_base = r->sample_buffer.ptr;
	shader.fl4208_prov = r->fl4208_prov;
	shader.fl4208_cap = r->fl4208_prov_cap;
// Product tone sidecar: context acquisition per face through the neutral
// slot into product-owned storage, attempted whenever a product table is
// installed. Classic carries no tone context and its slot is a no-op.
shader.product_state = r
	? (AsciickerProductStateV1*)r->product_runtime_state : NULL;
{
	const RendererProductV1* tone_nrt = AsciickerRenderer();
	if (tone_nrt && tone_nrt->mesh_tone_begin_face != nullptr)
		tone_nrt->mesh_tone_begin_face(shader.product_state,
			r->sample_buffer.ptr, r->light[3], normal_source);
}
	shader.world_generation = r->fl4208_world_gen;
	shader.mesh_instance_uid = r->mesh_instance_uid;
	shader.primitive_id = RenderStablePrimitiveId(coords);
	shader.owner_hash64 = 0;
	shader.owner_hash_ready = false;
	shader.visibility_sheet = r->mesh_visibility_sheet;

	// temporarily, let's transform verts for each face separately

	int v[3][4];

	float tmp0[4], tmp1[4], tmp2[4];
	float vertex_recip_w[3] = { 1.0f, 1.0f, 1.0f };

	{
		float xyzw[] = { coords[0], coords[1], coords[2], 1.0f };
		Product(r->viewinst_tm, xyzw, tmp0);

		if (r->perspective) // #if PERSPECTIVE_TEST
		{
			// FL-4257 Phase 1 S3 — route face vertex 0 through the cached
			// curvature view. Caller still owns the viewinst_tm / inst_tm
			// matrix transforms (those are modelling, not projection); the
			// projection divide happens in ProjectFaceVertexPersp.
			float ws[4];
			Product(r->inst_tm, xyzw, ws);
			float fx, fy, viewer_dist;
			if (!ProjectFaceVertexPersp(r, tmp0[0], tmp0[1], ws[0], ws[1], ws[2],
				&fx, &fy, &viewer_dist))
				return;
			vertex_recip_w[0] = 1.0f / viewer_dist;
			int tx = (int)floorf(fx + 0.5f);
			int ty = (int)floorf(fy + 0.5f);
			v[0][0] = tx;
			v[0][1] = ty;
			v[0][2] = RenderS3Active(r)
				? (int)floorf(RenderS3LastDepth(r) + 0.5f)
				: (int)floor(tmp0[2] + 0.5f);
			v[0][3] = 0; // clip flags
		}
		else //#else
		{
			v[0][0] = (int)floor(tmp0[0] + 0.5f);
			v[0][1] = (int)floor(tmp0[1] + 0.5f);
			v[0][2] = (int)floor(tmp0[2] + 0.5f);
			v[0][3] = 0; // clip flags
		} //#endif
	}

	{
		float xyzw[] = { coords[3], coords[4], coords[5], 1.0f };
		Product(r->viewinst_tm, xyzw, tmp1);

		if (r->perspective) // #if PERSPECTIVE_TEST
		{
			// FL-4257 Phase 1 S3 — face vertex 1 routed through dispatcher.
			float ws[4];
			Product(r->inst_tm, xyzw, ws);
			float fx, fy, viewer_dist;
			if (!ProjectFaceVertexPersp(r, tmp1[0], tmp1[1], ws[0], ws[1], ws[2],
				&fx, &fy, &viewer_dist))
				return;
			vertex_recip_w[1] = 1.0f / viewer_dist;
			int tx = (int)floorf(fx + 0.5f);
			int ty = (int)floorf(fy + 0.5f);
			v[1][0] = tx;
			v[1][1] = ty;
			v[1][2] = RenderS3Active(r)
				? (int)floorf(RenderS3LastDepth(r) + 0.5f)
				: (int)floor(tmp1[2] + 0.5f);
			v[1][3] = 0; // clip flags
		}
		else // #else
		{
			v[1][0] = (int)floor(tmp1[0] + 0.5f);
			v[1][1] = (int)floor(tmp1[1] + 0.5f);
			v[1][2] = (int)floor(tmp1[2] + 0.5f);
			v[1][3] = 0; // clip flags
		} //#endif
	}

	if (visual & (1<<31))
	{
		Bresenham(r->sample_buffer.ptr,r->sample_buffer.w,r->sample_buffer.h, v[0], v[1], 0x40);
		return;
	}

	{
		float xyzw[] = { coords[6], coords[7], coords[8], 1.0f };
		Product(r->viewinst_tm, xyzw, tmp2);

		if (r->perspective) // #if PERSPECTIVE_TEST
		{
			// FL-4257 Phase 1 S3 — face vertex 2 routed through dispatcher.
			float ws[4];
			Product(r->inst_tm, xyzw, ws);
			float fx, fy, viewer_dist;
			if (!ProjectFaceVertexPersp(r, tmp2[0], tmp2[1], ws[0], ws[1], ws[2],
				&fx, &fy, &viewer_dist))
				return;
			vertex_recip_w[2] = 1.0f / viewer_dist;
			int tx = (int)floorf(fx + 0.5f);
			int ty = (int)floorf(fy + 0.5f);
			v[2][0] = tx;
			v[2][1] = ty;
			v[2][2] = RenderS3Active(r)
				? (int)floorf(RenderS3LastDepth(r) + 0.5f)
				: (int)floor(tmp2[2] + 0.5f);
			v[2][3] = 0; // clip flags
		}
		else // #else
		{
			v[2][0] = (int)floor(tmp2[0] + 0.5f);
			v[2][1] = (int)floor(tmp2[1] + 0.5f);
			v[2][2] = (int)floor(tmp2[2] + 0.5f);
			v[2][3] = 0; // clip flags
		} // #endif
	}

	// FL-4257 — near-gate blow-up wedges are skipped for mesh faces too.
	if (RenderS3Active(r) && RenderS3TriBlowup(r, v[0], v[1], v[2]))
		return;

	int mesh_v[3][4];
	// Parity zero is meaningful only after the current invocation established
	// parity validity.  An unavailable parity can never select either fold.
	int mesh_far_side = RenderS3Active(r) && r->s3_mesh_parity_valid &&
		r->s3_mesh_parity;
	if (mesh_far_side)
	{
		for (int i = 0; i < 3; i++)
		{
			for (int j = 0; j < 4; j++)
				mesh_v[i][j] = v[i][j];
			mesh_v[i][1] = 2 * r->s3_mesh_mirror_y - v[i][1];
		}
	}

	int w = r->sample_buffer.w;
	int h = r->sample_buffer.h;
	Sample* ptr = r->sample_buffer.ptr;

	// FL-4512: the face normal remains the projected-feature owner. It no longer
	// owns tonal lighting; mesh_tone_transfer.v1 uses the connectivity-derived
	// vertex normals supplied by QueryMesh below.
	float e1[] = { coords[3] - coords[0], coords[4] - coords[1], coords[5] - coords[2] };
	float e2[] = { coords[6] - coords[0], coords[7] - coords[1], coords[8] - coords[2] };

	float n[4] =
	{
		e1[1] * e2[2] - e1[2] * e2[1],
		e1[2] * e2[0] - e1[0] * e2[2],
		e1[0] * e2[1] - e1[1] * e2[0],
		0
	};

	// FL-4652 CT-1: compute the upstream flat per-face source for every
	// progression revision. Invalid geometry publishes the helper's explicit
	// dark byte; the deleted smooth-tone path is never a fallback owner.
	const float geometric_normal[3] = { n[0], n[1], n[2] };
	const Fl4652UpstreamTonalBaseV1 upstream_tonal_base =
		Fl4652ComputeUpstreamTonalBaseV1(geometric_normal, r->inst_tm,
			r->light, HEIGHT_SCALE);
	shader.upstream_tonal_base_diffuse = upstream_tonal_base.valid
		? upstream_tonal_base.diffuse_u8 : 0u;

	float inst_n[4];
	Product(r->inst_tm, n, inst_n);

	inst_n[2] /= HEIGHT_SCALE;

	const float feature_len2 = inst_n[0] * inst_n[0] + inst_n[1] * inst_n[1] + inst_n[2] * inst_n[2];
	const float feature_inv_len = feature_len2 > 0.0f && isfinite(feature_len2)
		? 1.0f / sqrtf(feature_len2) : 0.0f;
	const float feature_normal[3] = {
		inst_n[0] * feature_inv_len,
		inst_n[1] * feature_inv_len,
		inst_n[2] * feature_inv_len
	};
	const int (*feature_v)[4] = mesh_far_side ? mesh_v : v;
// Product triangle registration (feature build + store append) through the
// neutral slot, attempted whenever a product table is installed; the
// per-face geometry above stays shared. Classic carries no feature store
// and its slot is a no-op.
{
	const RendererProductV1* reg_nrt = AsciickerRenderer();
	AsciickerProductStateV1* reg_nst = r
		? (AsciickerProductStateV1*)r->product_runtime_state : NULL;
	if (reg_nrt && reg_nrt->register_projected_triangle != nullptr)
		reg_nrt->register_projected_triangle(reg_nst, r, coords, feature_v,
			feature_normal, shader.primitive_id);
}

// Product per-vertex mu + tone through the neutral slot (normal-matrix
// scratch lives product-side), attempted whenever a product table is
// installed. Zeroed arrays are the Classic result (its slot writes nothing).
float vertex_mu[3] = {};
float vertex_tone_u8[3] = {};
{
	const RendererProductV1* vx_nrt = AsciickerRenderer();
	AsciickerProductStateV1* vx_nst = r
		? (AsciickerProductStateV1*)r->product_runtime_state : NULL;
	if (vx_nrt && vx_nrt->mesh_tone_compute_vertex != nullptr)
		vx_nrt->mesh_tone_compute_vertex(vx_nst, normals, r->light,
			RenderS3Active(r) ? 1 : 0, vertex_mu, vertex_tone_u8);
	(void)normals;
	(void)normal_source;
}

	if (r->reflection_mode)
	{
		const int* pv[3] = { v[2],v[1],v[0] };
		float tri_varying[3][10] =
		{
			{ (float)v[2][2], (float)colors[8], (float)colors[9], (float)colors[10], vertex_tone_u8[2] * vertex_recip_w[2], vertex_mu[2] * vertex_recip_w[2], 0, 0, vertex_recip_w[2], vertex_recip_w[2] },
			{ (float)v[1][2], (float)colors[4], (float)colors[5], (float)colors[6], vertex_tone_u8[1] * vertex_recip_w[1], vertex_mu[1] * vertex_recip_w[1], 0, vertex_recip_w[1], 0, vertex_recip_w[1] },
			{ (float)v[0][2], (float)colors[0], (float)colors[1], (float)colors[2], vertex_tone_u8[0] * vertex_recip_w[0], vertex_mu[0] * vertex_recip_w[0], vertex_recip_w[0], 0, 0, vertex_recip_w[0] },
		};
		float tri_varying_flip[3][10] =
		{
			{ (float)v[2][2], (float)colors[8], (float)colors[9], (float)colors[10], vertex_tone_u8[2] * vertex_recip_w[2], vertex_mu[2] * vertex_recip_w[2], 0, 0, vertex_recip_w[2], vertex_recip_w[2] },
			{ (float)v[0][2], (float)colors[0], (float)colors[1], (float)colors[2], vertex_tone_u8[0] * vertex_recip_w[0], vertex_mu[0] * vertex_recip_w[0], vertex_recip_w[0], 0, 0, vertex_recip_w[0] },
			{ (float)v[1][2], (float)colors[4], (float)colors[5], (float)colors[6], vertex_tone_u8[1] * vertex_recip_w[1], vertex_mu[1] * vertex_recip_w[1], 0, vertex_recip_w[1], 0, vertex_recip_w[1] },
		};
		const int* pv_flip[3] = { mesh_v[2], mesh_v[0], mesh_v[1] };

		//for (int i = 0; i < 12; i++)
		//	colors[i] = colors[i] * 3 / 4;

		RasterizeVarying<Sample, Shader, 10>(r->sample_buffer.ptr, r->sample_buffer.w, r->sample_buffer.h, &shader,
			mesh_far_side ? pv_flip : pv, visual&(1<<30),
			mesh_far_side ? tri_varying_flip : tri_varying);
	}
	else
	{
		const int* pv[3] = { v[0],v[1],v[2] };
		float tri_varying[3][10] =
		{
			{ (float)v[0][2], (float)colors[0], (float)colors[1], (float)colors[2], vertex_tone_u8[0] * vertex_recip_w[0], vertex_mu[0] * vertex_recip_w[0], vertex_recip_w[0], 0, 0, vertex_recip_w[0] },
			{ (float)v[1][2], (float)colors[4], (float)colors[5], (float)colors[6], vertex_tone_u8[1] * vertex_recip_w[1], vertex_mu[1] * vertex_recip_w[1], 0, vertex_recip_w[1], 0, vertex_recip_w[1] },
			{ (float)v[2][2], (float)colors[8], (float)colors[9], (float)colors[10], vertex_tone_u8[2] * vertex_recip_w[2], vertex_mu[2] * vertex_recip_w[2], 0, 0, vertex_recip_w[2], vertex_recip_w[2] },
		};
		float tri_varying_flip[3][10] =
		{
			{ (float)v[0][2], (float)colors[0], (float)colors[1], (float)colors[2], vertex_tone_u8[0] * vertex_recip_w[0], vertex_mu[0] * vertex_recip_w[0], vertex_recip_w[0], 0, 0, vertex_recip_w[0] },
			{ (float)v[2][2], (float)colors[8], (float)colors[9], (float)colors[10], vertex_tone_u8[2] * vertex_recip_w[2], vertex_mu[2] * vertex_recip_w[2], 0, 0, vertex_recip_w[2], vertex_recip_w[2] },
			{ (float)v[1][2], (float)colors[4], (float)colors[5], (float)colors[6], vertex_tone_u8[1] * vertex_recip_w[1], vertex_mu[1] * vertex_recip_w[1], 0, vertex_recip_w[1], 0, vertex_recip_w[1] },
		};
		const int* pv_flip[3] = { mesh_v[0], mesh_v[2], mesh_v[1] };
		RasterizeVarying<Sample, Shader, 10>(r->sample_buffer.ptr, r->sample_buffer.w, r->sample_buffer.h, &shader,
			mesh_far_side ? pv_flip : pv, visual&(1<<30),
			mesh_far_side ? tri_varying_flip : tri_varying);
	}
}

// Renderer::RenderSprite (static callback) lives in render_sprite_blit.cpp

void Renderer::RenderMesh(Inst* inst, Mesh* m, double* tm, void* cookie)
{
	Renderer* r = (Renderer*)cookie;
	// FL-4512 VS2: invocation-local identity starts empty.  In particular,
	// parity zero must not survive as an apparent near-side S3 fact when the
	// current mesh anchor has not projected successfully.
	r->s3_mesh_parity = 0;
	r->s3_mesh_parity_valid = 0;
	r->mesh_visibility_sheet = RENDER_VISIBILITY_SHEET_INVALID;
// Product per-submit setup (feature-begin snapshot, tone normal-matrix
// scratch, scale/aspect authority derivation) through the neutral slot,
// attempted whenever a product table is installed. State is created once
// through the neutral create and carried on the Renderer as an opaque
// pointer. Classic carries no product state: its create returns null and
// its submit slot is a no-op.
{
	const RendererProductV1* sub_nrt = AsciickerRenderer();
	AsciickerProductStateV1* sub_nst = r
		? (AsciickerProductStateV1*)r->product_runtime_state : NULL;
	if (sub_nst == nullptr && sub_nrt != nullptr &&
		sub_nrt->create != nullptr)
	{
		sub_nst = sub_nrt->create();
		if (r)
			r->product_runtime_state = sub_nst;
	}
	if (sub_nrt != nullptr && sub_nrt->mesh_submit_begin != nullptr)
		sub_nrt->mesh_submit_begin(sub_nst, r, inst, m, tm);
}
	double view_tm[16];
	// FL-4257 Phase 1 S3 — view_tm bundling moved to render_projection.cpp so
	// this file no longer reads r->mul / r->add directly.
	BuildMeshViewTransformEuclidean(r, r->reflection_mode, view_tm);

	r->inst_tm = tm;
	r->mesh_instance_uid = RenderMeshInstanceUid(inst);
	r->mesh_face_index = 0;
	r->mesh_material_key = (int16_t)GetInstMaterialKey(inst);
	MatProduct(view_tm, tm, r->viewinst_tm);
	r->s3_mesh_mirror_y = 0;
	RenderVisibilitySheetFailureV1 sheet_failure =
		RENDER_VISIBILITY_SHEET_FAILURE_NONE;
	// FL-4652 fail-closed repair: RenderS3Setup has three states.
	// State zero is a legitimate Euclidean frame, state one is authoritative
	// S3, and state two means published curvature failed validation. Preserve
	// the complete state here so state two can never masquerade as Euclidean.
	const int s3_submission_state = r->s3_active;
	if (s3_submission_state == RENDER_S3_SUBMISSION_ACTIVE)
	{
		if (!inst)
		{
			// S3 parity belongs to the projected instance anchor.  Without an
			// instance there is no legal current-invocation parity fact.
			sheet_failure =
				RENDER_VISIBILITY_SHEET_FAILURE_S3_PARITY_UNAVAILABLE;
		}
		else
		{
			double bbox[6];
			GetInstBBox(inst, bbox);
			const float cx = (float)((bbox[0] + bbox[1]) * 0.5);
			const float cy = (float)((bbox[2] + bbox[3]) * 0.5);
			const float cz = (float)((bbox[4] + bbox[5]) * 0.5);
			float fx, fy, viewer_dist;
			if (ProjectFaceVertexPersp(
				r, 0.0f, 0.0f, cx, cy, cz, &fx, &fy, &viewer_dist))
			{
				// The projection depth is now a fact from this invocation.
				// Mark validity only after deriving parity from that depth.
				r->s3_mesh_parity =
					RenderS3DepthOrientationParity(RenderS3LastDepth(r)) ? 1 : 0;
				r->s3_mesh_parity_valid = 1;
				r->s3_mesh_mirror_y = (int)floorf(fy + 0.5f);
			}
			else
			{
				// Projection failure rejects the whole mesh before QueryMesh.
				// Treating the still-zero parity byte as evidence would merge
				// distinct visibility sheets.
				sheet_failure =
					RENDER_VISIBILITY_SHEET_FAILURE_S3_PROJECTION_FAILED;
			}
		}
	}
	if (sheet_failure == RENDER_VISIBILITY_SHEET_FAILURE_NONE)
	{
		r->mesh_visibility_sheet = RenderVisibilitySheetForSubmissionV1(
			s3_submission_state, r->s3_image,
			r->s3_mesh_parity_valid != 0, r->s3_mesh_parity,
			r->reflection_mode, &sheet_failure);
	}
	if (sheet_failure != RENDER_VISIBILITY_SHEET_FAILURE_NONE)
	{
		// One rejected invocation contributes one exact diagnostic.  This
		// observation does not retry with a guessed sheet.
		++r->mesh_visibility_sheet_failure_count;
		r->mesh_visibility_sheet_last_failure = sheet_failure;
	}
	else
	{
		// QueryMesh is reachable only after sheet derivation succeeds.
		// Sample winners and projected features therefore share one immutable
		// invocation identity. Capture the transform by value before QueryMesh
		// emits any object-local Active Stroke anchors.
// Product reprojection capture + post-QueryMesh classification and kind-4
// emission through neutral slots, attempted whenever a product table is
// installed. Classic carries no stroke store and its slots are no-ops.
{
	const RendererProductV1* cap_nrt = AsciickerRenderer();
	AsciickerProductStateV1* cap_nst = r
		? (AsciickerProductStateV1*)r->product_runtime_state : NULL;
	if (cap_nrt && cap_nrt->capture_stroke_reprojection != nullptr)
		cap_nrt->capture_stroke_reprojection(cap_nst, r);
}
		QueryMesh(m, Renderer::RenderFace, r);
{
	const RendererProductV1* end_nrt = AsciickerRenderer();
	AsciickerProductStateV1* end_nst = r
		? (AsciickerProductStateV1*)r->product_runtime_state : NULL;
	if (end_nrt && end_nrt->mesh_submit_end != nullptr)
		end_nrt->mesh_submit_end(end_nst, r, m);
}
	}
	r->mesh_material_key = -1;
	r->mesh_instance_uid = 0;
	r->mesh_projected_scale_authority_valid = 0;
	memset(r->mesh_asset_metric_q24_8, 0, sizeof(r->mesh_asset_metric_q24_8));
	memset(r->mesh_scale_asset_id, 0, sizeof(r->mesh_scale_asset_id));
	// Aspect reset lives product-side (World_MeshSubmitBegin resets before
	// derive); shared never touches product-owned aspect state.
	r->s3_mesh_parity = 0;
	r->s3_mesh_parity_valid = 0;
	r->mesh_visibility_sheet = RENDER_VISIBILITY_SHEET_INVALID;
	r->s3_mesh_mirror_y = 0;

	// transform verts int integer coords
	// ...

	// given interpolated RGB -> round to 555, store it in visual
	// copy to diffuse to diffuse
	// mark mash 'auto-material' as 0x8 flag in spare

	// in post pass:
	// if sample has 0x8 flag
	//   multiply rgb by diffuse (into 888 bg=fg)
	// apply color mixing with neighbours
	// if at least 1 sample have mesh bit in spare
	// - round mixed bg rgb to R5G5B5 and use auto_material[32K] -> {bg,fg,gl}
	// else apply gridlines etc.
}

// WHY RenderPatch: Callback for QueryTerrain — receives one terrain patch.
// Transforms the patch's (HEIGHT_CELLS+1)^2 vertex grid to screen coords,
// splits each cell into 2 triangles (diagonal determined by GetTerrainDiag),
// computes per-triangle diffuse lighting, then rasterizes each triangle via
// Rasterize<> with an inline Shader that samples the patch's visual map.
// we could easily make it template of <Sample,Shader>
void Renderer::RenderPatch(Patch* p, int x, int y, int view_flags, void* cookie /*Renderer*/)
{
	struct Shader
	{
		void BlendVR(Sample*s, float z, const float* vr)
		{
			if (s->height < z)
			{
				if (reflection_mode)
				{
					if (z < water + HEIGHT_SCALE / 8)
					{
						if (z > water)
							s->height = water;
						else
							s->height = z;

						int u = RenderClampInt((int)floor(vr[1]), 0, VISUAL_CELLS - 1);
						int v = RenderClampInt((int)floor(vr[2]), 0, VISUAL_CELLS - 1);

						/*
						if (u >= VISUAL_CELLS || v >= VISUAL_CELLS)
						{
							// detect overflow
							s->visual = 2;
						}
						else
						*/
						{
							// Terrain path: one Sample remembers the winning terrain depth,
							// packed terrain visual word, light level, and parity flags.
							s->visual = map[v * VISUAL_CELLS + u];
							s->material_id = (int16_t)(s->visual & 0x00FF);
							s->diffuse = diffuse;
							s->spare |= parity | 0x3;
							s->spare &= ~(0x44|0x8); // clear mesh and lines
							s->glyph_dispatch_surface = GLYPH_DISPATCH_TERRAIN_MATERIAL;
							s->s3_parity = s3_enabled ? (uint8_t)RenderS3DepthOrientationParity(z) : 0;
							// FL-4208 N0a correction (reviewer flag #1, round 2): a
							// reflected fragment just won this sample. Do NOT write a
							// reflection identity (reflected (u,v) is not the on-screen
							// owner), AND invalidate any stale direct provisional written
							// earlier this frame so the post-raster pass admits no
							// terrain identity for a reflection-won sample.
							Fl4208InvalidateProvisional(s);
						}
					}
				}
				else
				{
					if (z >= water - HEIGHT_SCALE / 8)
					{
						if (z < water)
							s->height = water;
						else
							s->height = z;

						int u = RenderClampInt((int)floor(vr[1]), 0, VISUAL_CELLS - 1);
						int v = RenderClampInt((int)floor(vr[2]), 0, VISUAL_CELLS - 1);

						/*
						if (u >= VISUAL_CELLS || v >= VISUAL_CELLS)
						{
							// detect overflow
							s->visual = 2;
						}
						else
						*/
						{
							int visual_idx = v * VISUAL_CELLS + u;
							uint16_t m = map[visual_idx];
							// Bit 15 is not just cosmetic. If set, this terrain sample is
							// raised by one HEIGHT_SCALE step, so it can occlude sprites and
							// other geometry differently during later depth tests.
							if (m & 0x8000)
								s->height += HEIGHT_SCALE;

							s->visual = m;
							s->material_id = (int16_t)(m & 0x00FF);
							s->diffuse = diffuse;
							s->glyph_dispatch_surface = GLYPH_DISPATCH_TERRAIN_MATERIAL;
							s->s3_parity = s3_enabled ? (uint8_t)RenderS3DepthOrientationParity(z) : 0;
							Fl4208WriteProvisional(s, vr); // FL-4208 N0a: provisional terrain identity

							#ifdef DARK_TERRAIN
							if (dark&(((uint64_t)1) << visual_idx))
							{
								if (s->diffuse > 64)
									s->diffuse -= 64;
								else
									s->diffuse = 0;
							}
							#endif

							/*
							if (dark&(((uint64_t)1) << visual_idx))
								s->diffuse /= 4;
							else
								s->diffuse *= 16;
							*/

							s->spare = (s->spare & ~(0x8|0x3|0x44)) | parity; // clear refl, mesh and line, then add parity
						}
					}
				}
			}
		}

		inline void Diffuse(int dzdx, int dzdy)
		{
			// Collapse terrain slope + light direction into one byte so resolve can
			// later choose a 0..15 material light band cheaply.
			float nl = (float)sqrt(dzdx * dzdx + dzdy * dzdy + HEIGHT_SCALE * HEIGHT_SCALE);
			float df = (dzdx * light[0] + dzdy * light[1] + HEIGHT_SCALE * light[2]) / nl;
			df = df * (1.0f - 0.5f*light[3]) + 0.5f*light[3];
			diffuse = df <= 0 ? 0 : (int)(df * 0xFF);
		}

		uint16_t* map; // points to array of VISUAL_CELLS x VISUAL_CELLS ushorts
		float water;
		bool reflection_mode;
		float light[4];
		uint8_t diffuse; // shading experiment
		uint8_t s3_enabled;
		uint8_t parity;
		// FL-4208 N0a (presentation-neutral): provisional identity plumbing. The
		// terrain winning branches write a provisional Fl4208SurfaceId into
		// fl4208_prov[s - fl4208_base]; the post-raster pass interns survivors.
		Fl4208ProvisionalId* fl4208_prov;
		Sample*              fl4208_base;
		int                  fl4208_cap;
		uint64_t             fl4208_gen;
		int32_t              fl4208_patch_x;
		int32_t              fl4208_patch_y;
		uint32_t             fl4208_bins;

		// FL-4208 N0a (presentation-neutral): record this winning terrain
		// fragment's provisional identity. NOT interned here — interning every
		// transient depth-winner would over-admit past the 57600 capacity; the
		// post-raster pass interns only final survivors. vr[1]/vr[2] are in
		// visual-cell space [0,VISUAL_CELLS): integer part = within-patch cell,
		// fraction = sub-cell anchor. QueryTerrain already supplies patch x/y as
		// the patch's world visual-cell origin.
		inline void Fl4208WriteProvisional(Sample* s, const float* vr)
		{
			if (!fl4208_prov)
				return;
			long pidx = (long)(s - fl4208_base);
			if (pidx < 0 || pidx >= fl4208_cap)
				return;
			int fu = (int)floorf(vr[1]); if (fu < 0) fu = 0; if (fu > VISUAL_CELLS - 1) fu = VISUAL_CELLS - 1;
			int fv = (int)floorf(vr[2]); if (fv < 0) fv = 0; if (fv > VISUAL_CELLS - 1) fv = VISUAL_CELLS - 1;
			float lu = (float)vr[1] - (float)fu;
			float lv = (float)vr[2] - (float)fv;
			Fl4208ProvisionalId* pp = fl4208_prov + pidx;
			memset(pp, 0, sizeof(*pp));
			// Multiplying the origin by VISUAL_CELLS a second time produced an
			// identity that could not join the same cell in the frozen document.
			pp->owner_x = fl4208_patch_x + fu;
			pp->owner_y = fl4208_patch_y + fv;
			pp->world_generation = fl4208_gen;
			pp->anchor_u_q = Fl4208AnchorQuant(lu, fl4208_bins);
			pp->anchor_v_q = Fl4208AnchorQuant(lv, fl4208_bins);
			// FL-4208 N0a correction (reviewer flag #2): store the RESOLVED
			// material_id (visual & 0x00FF), the same value render_scene.cpp:81
			// and render_resolve.cpp use, so the cell conflict gate agrees on
			// material rather than on cosmetic high bits of the visual word.
			pp->material = Fl4208ResolvedMaterialId(s->visual);
			pp->owner_kind = FL4208_OWNER_TERRAIN;
			pp->valid = 1;
		}

		// FL-4208 N0a correction (reviewer flag #1, round 2): the reflection
		// pass runs AFTER direct terrain into the SAME SampleBuffer. When a
		// reflected fragment WINS a sample that direct terrain already wrote, the
		// direct provisional (valid=1) is stale — the on-screen pixel now shows
		// the reflection, not the direct cell. Removing the reflection write is
		// not enough; the stale direct identity survives and the post-raster pass
		// would intern it. Explicitly invalidate so the reflected-won sample
		// carries NO terrain identity (fail closed, Law 6).
		inline void Fl4208InvalidateProvisional(Sample* s)
		{
			if (!fl4208_prov)
				return;
			long pidx = (long)(s - fl4208_base);
			if (pidx < 0 || pidx >= fl4208_cap)
				return;
			fl4208_prov[pidx].valid = 0;
		}
#ifdef DARK_TERRAIN
		uint64_t dark;
#endif
	} shader;

	Renderer* r = (Renderer*)cookie;
	shader.s3_enabled = RenderS3Active(r) ? 1 : 0;
	shader.reflection_mode = r->reflection_mode;

	// FL-4208 N0a: hand the provisional identity plane + this patch's global cell
	// origin to the shader. owner_x/owner_y = patch(x,y)*VISUAL_CELLS + within-cell
	// (u,v); injective because the within-cell index is < VISUAL_CELLS.
	shader.fl4208_prov    = r->fl4208_prov;
	shader.fl4208_base    = r->sample_buffer.ptr;
	shader.fl4208_cap     = r->fl4208_prov_cap;
	shader.fl4208_gen     = r->fl4208_world_gen;
	shader.fl4208_patch_x = x;
	shader.fl4208_patch_y = y;
	shader.fl4208_bins    = g_fl4208_config.anchor_bins;

	// FL-4257 Phase 1 S3 — patch projection now goes through
	// ProjectWorldVertexPatchPersp / ProjectWorldVertexOrtho* in
	// render_projection.cpp. The mul/add/iadd aliases that the inline math
	// used to need are gone; reading the Renderer matrices directly here
	// would resurrect the second projection owner the dispatcher exists to
	// delete.

	int w = r->sample_buffer.w;
	int h = r->sample_buffer.h;
	Sample* ptr = r->sample_buffer.ptr;

	uint16_t* hmap = GetTerrainHeightMap(p);
	
	uint16_t* hm = hmap;

	// transform patch verts xy+dx+dy, together with hmap into this array
	int xyzf[HEIGHT_CELLS + 1][HEIGHT_CELLS + 1][4];
	// FL-4257 — true terrain heights per vertex: under the 4D spherical
	// owner xyzf[..][2] carries the arc DEPTH KEY, so the diffuse slope
	// lighting below must keep shading from REAL height differences.
	int vzs[HEIGHT_CELLS + 1][HEIGHT_CELLS + 1];

	for (int dy = 0; dy <= HEIGHT_CELLS; dy++)
	{
		int vy = y * HEIGHT_CELLS + dy * VISUAL_CELLS;

		for (int dx = 0; dx <= HEIGHT_CELLS; dx++)
		{
			int vx = x * HEIGHT_CELLS + dx * VISUAL_CELLS;
			int vz = *(hm++);
			vzs[dy][dx] = vz;

			if (r->reflection_mode)
			{
				if (r->perspective) // #if PERSPECTIVE_TEST
				{
					float fx, fy, viewer_dist;
					if (!ProjectWorldVertexPatchPersp(r, (float)vx, (float)vy, (float)vz,
						&fx, &fy, &viewer_dist))
					{
						// cull entire patch if any vertex is behind view_pos
						// (or fails closed under invalid curvature authority)
						return;
					}
					int tx = (int)floorf(fx + 0.5f);
					int ty = (int)floorf(fy + 0.5f);

					xyzf[dy][dx][0] = tx;
					xyzf[dy][dx][1] = ty;
					xyzf[dy][dx][2] = (int)(2 * r->water) - vz;

					// todo: if patch is known to fully fit in screen, set f=0
					// otherwise we need to check if / which screen edges cull each vertex
					xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
				}
				else // #else
				{
					int tx, ty;
					bool ortho_ok = r->int_flag
						? ProjectWorldVertexOrthoIntFlag(r, (float)vx, (float)vy, (float)vz, &tx, &ty)
						: ProjectWorldVertexOrthoIadd(r, (float)vx, (float)vy, (float)vz, &tx, &ty);
					if (!ortho_ok)
						return;

					xyzf[dy][dx][0] = tx;
					xyzf[dy][dx][1] = ty;
					xyzf[dy][dx][2] = (int)(2 * r->water) - vz;

					// todo: if patch is known to fully fit in screen, set f=0
					// otherwise we need to check if / which screen edges cull each vertex
					xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
				} // #endif
			}
			else
			{
				if (r->perspective) // #if PERSPECTIVE_TEST
				{
					float fx, fy, viewer_dist;
					if (!ProjectWorldVertexPatchPersp(r, (float)vx, (float)vy, (float)vz,
						&fx, &fy, &viewer_dist))
					{
						// Euclidean: cull entire patch (vertex behind eye).
						// 4D spherical owner: the vertex is outside THIS
						// image's hemisphere — the patch legitimately
						// straddles the gate on the closed sphere, so flag
						// the vertex and skip only its triangles (the
						// complementary image pass renders the other side).
						if (!RenderS3Active(r))
							return;
						xyzf[dy][dx][0] = 0;
						xyzf[dy][dx][1] = 0;
						xyzf[dy][dx][2] = 0;
						xyzf[dy][dx][3] = 16;
						continue;
					}
					int tx = (int)floorf(fx + 0.5f);
					int ty = (int)floorf(fy + 0.5f);

					xyzf[dy][dx][0] = tx;
					xyzf[dy][dx][1] = ty;
					// Under the 4D owner xyzf[..][2] carries the ARC DEPTH
					// KEY (one depth policy across both images); vzs[][]
					// below keeps the true height for lighting.
					xyzf[dy][dx][2] = RenderS3Active(r)
						? (int)floorf(RenderS3LastDepth(r) + 0.5f) : vz;

					// todo: if patch is known to fully fit in screen, set f=0
					// otherwise we need to check if / which screen edges cull each vertex
					xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
				}
				else // #else
				{
					int tx, ty;
					bool ortho_ok = r->int_flag
						? ProjectWorldVertexOrthoIntFlag(r, (float)vx, (float)vy, (float)vz, &tx, &ty)
						: ProjectWorldVertexOrthoIadd(r, (float)vx, (float)vy, (float)vz, &tx, &ty);
					if (!ortho_ok)
						return;

					xyzf[dy][dx][0] = tx;
					xyzf[dy][dx][1] = ty;
					xyzf[dy][dx][2] = vz;

					// todo: if patch is known to fully fit in screen, set f=0
					// otherwise we need to check if / which screen edges cull each vertex
					xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
				} // #endif
			}
		}
	}

	uint16_t  diag = GetTerrainDiag(p);

	// 2 parity bits for drawing lines around patches
	// 0 - no patch rendered here
	// 1 - odd
	// 2 - even
	// 3 - under water

#ifdef DARK_TERRAIN
	shader.dark = GetTerrainDark(p);
#endif

	shader.parity = (((x^y)/VISUAL_CELLS) & 1) + 1; 
	shader.water = r->water;
	shader.map = GetTerrainVisualMap(p);

	shader.light[0] = r->light[0];
	shader.light[1] = r->light[1];
	shader.light[2] = r->light[2];
	shader.light[3] = r->light[3];

	/*
	shader.light[0] = 0;
	shader.light[1] = 0;
	shader.light[2] = 1;
	*/

//	if (shader.parity == 1)
//		return;

	hm = hmap;

	// Diffuse slope source: true heights under the 4D spherical owner
	// (xyzf[..][2] is a depth key there); xyzf heights on the Euclidean
	// baseline (byte-identical).
	const bool s3_heights = RenderS3Active(r) != 0;
	auto DZ = [&](int y1, int x1, int y0, int x0) -> int
	{
		return s3_heights ? (vzs[y1][x1] - vzs[y0][x0])
		                  : (xyzf[y1][x1][2] - xyzf[y0][x0][2]);
	};
	const int (*uv)[2] = r->patch_uv;
	auto RasterizePatchTriangle = [&](const int* tri[3], const int tri_uv[6])
	{
		// 4D spherical owner: bit 16 marks a vertex outside this image's
		// hemisphere; its triangles belong to the complementary image.
		if ((tri[0][3] | tri[1][3] | tri[2][3]) & 16)
			return;
		// ...and near-gate blow-up wedges are skipped (see render_s3.h).
		if (RenderS3Active(r) && RenderS3TriBlowup(r, tri[0], tri[1], tri[2]))
			return;
		float tri_varying[3][3] =
		{
			{ (float)tri[0][2], (float)tri_uv[0], (float)tri_uv[1] },
			{ (float)tri[1][2], (float)tri_uv[2], (float)tri_uv[3] },
			{ (float)tri[2][2], (float)tri_uv[4], (float)tri_uv[5] },
		};
		RasterizeVarying<Sample, Shader, 3>(ptr, w, h, &shader, tri, false, tri_varying);
	};

	// FL-4257 terrain rim LOD — projection-safe subdivision for spherical
	// cells (the terrain analog of the water sphere's subdivision): a cell
	// triangle is split in WORLD space until its projection fits one image
	// at rasterizable screen size. Without this, cells near the camera's
	// antipodal (conjugate) point — whose true image smears across the
	// whole sky — rasterize as giant linear streaks.
		#if defined(__EMSCRIPTEN__)
		const float kS3TerrainLeafPx = 512.0f;
		const int kS3TerrainMaxDepth = 0;
		#else
		const float kS3TerrainLeafPx = 96.0f;
		const int kS3TerrainMaxDepth = 3;
		#endif
	const float kS3TerrainHaloDropX = (float)w * 1.25f;
	const float kS3TerrainHaloDropY = (float)h * 1.25f;
	std::function<void(const S3TerrainVertex&, const S3TerrainVertex&, const S3TerrainVertex&, int)>
	S3SubdivTri = [&](const S3TerrainVertex& a, const S3TerrainVertex& b,
	                  const S3TerrainVertex& c, int rec)
	{
		float fx[3], fy[3], dep[3], vd;
		const S3TerrainVertex* vv[3] = { &a, &b, &c };
		int ok = 0;
		for (int i = 0; i < 3; i++)
		{
			if (ProjectWorldVertexPatchPersp(r, vv[i]->wx, vv[i]->wy, vv[i]->wz,
				&fx[i], &fy[i], &vd))
			{
				dep[i] = RenderS3LastDepth(r);
				ok++;
			}
		}
		if (ok == 3)
		{
			float ex = fmaxf(fx[0], fmaxf(fx[1], fx[2])) - fminf(fx[0], fminf(fx[1], fx[2]));
			float ey = fmaxf(fy[0], fmaxf(fy[1], fy[2])) - fminf(fy[0], fminf(fy[1], fy[2]));
			// FL-4257 starburst fix: leaf acceptance is extent-bounded.
			// The old cap-time rasterization turned near-conjugate
			// slivers into screen-wide linear streaks whose interpolated
			// depth beat legitimate near terrain.
			if (ex <= kS3TerrainLeafPx && ey <= kS3TerrainLeafPx)
			{
				int t0[4] = { (int)floorf(fx[0] + 0.5f), (int)floorf(fy[0] + 0.5f), (int)floorf(dep[0] + 0.5f), 0 };
				int t1[4] = { (int)floorf(fx[1] + 0.5f), (int)floorf(fy[1] + 0.5f), (int)floorf(dep[1] + 0.5f), 0 };
				int t2[4] = { (int)floorf(fx[2] + 0.5f), (int)floorf(fy[2] + 0.5f), (int)floorf(dep[2] + 0.5f), 0 };
				const int* tri[3] = { t0, t1, t2 };
				float tri_varying[3][3] =
				{
					{ (float)t0[2], a.u, a.v },
					{ (float)t1[2], b.u, b.v },
					{ (float)t2[2], c.u, c.v },
				};
				RasterizeVarying<Sample, Shader, 3>(ptr, w, h, &shader, tri, false, tri_varying);
				return;
			}
			if (ex > kS3TerrainHaloDropX || ey > kS3TerrainHaloDropY)
				return;
			// off-screen prune
			if ((fx[0] < 0 && fx[1] < 0 && fx[2] < 0) ||
			    (fy[0] < 0 && fy[1] < 0 && fy[2] < 0) ||
			    (fx[0] >= w && fx[1] >= w && fx[2] >= w) ||
			    (fy[0] >= h && fy[1] >= h && fy[2] >= h))
				return;
		}
		// Recursion floor: a piece still oversized this deep is a
		// conjugate-halo / gate sliver. Drop it; the closed world has
		// nearer geometry on those rays.
		if (rec >= kS3TerrainMaxDepth)
			return;
		auto mid = [&](const S3TerrainVertex& p1, const S3TerrainVertex& p2) -> S3TerrainVertex
		{
			S3TerrainVertex out;
			RenderS3TerrainMidpoint(r, p1, p2, out);
			return out;
		};
		S3TerrainVertex mab = mid(a, b), mbc = mid(b, c), mca = mid(c, a);
		S3SubdivTri(a, mab, mca, rec + 1);
		S3SubdivTri(mab, b, mbc, rec + 1);
		S3SubdivTri(mca, mbc, c, rec + 1);
		S3SubdivTri(mab, mbc, mca, rec + 1);
	};
	// World coords + explicit uv for a cell corner of the current patch.
	const bool s3_cells = RenderS3Active(r) != 0;
	auto S3V = [&](int cdy, int cdx, int uu, int vv) -> S3TerrainVertex
	{
		S3TerrainVertex v;
		v.wx = (float)(x * HEIGHT_CELLS + cdx * VISUAL_CELLS);
		v.wy = (float)(y * HEIGHT_CELLS + cdy * VISUAL_CELLS);
		v.wz = (float)vzs[cdy][cdx];
		v.u = (float)uu;
		v.v = (float)vv;
		return v;
	};

	for (int dy = 0; dy < HEIGHT_CELLS; dy++, hm++)
	{
		for (int dx = 0; dx < HEIGHT_CELLS; dx++,diag>>=1, hm++)
		{
			//if (!(diag & 1))
			if (diag & 1)
			{
				// .
				// |\
				// |_\
				// '  '
				// lower triangle

				// terrain should keep diffuse map with timestamp of light modification it was updated to
				// then if current light timestamp is different than in terrain we need to update diffuse (into terrain)
				// now we should simply use diffuse from terrain
				// note: if terrain is being modified, we should clear its timestamp or immediately update diffuse
				if (r->reflection_mode)
				{
					//done
					int lo_uv[] = { uv[dx][0],uv[dy][1], uv[dx][1],uv[dy][0], uv[dx][0],uv[dy][0] };
					const int* lo[3] = { xyzf[dy + 1][dx], xyzf[dy][dx + 1], xyzf[dy][dx] };
					shader.Diffuse(-xyzf[dy][dx][2] + xyzf[dy][dx + 1][2], -xyzf[dy][dx][2] + xyzf[dy + 1][dx][2]);
					RasterizePatchTriangle(lo, lo_uv);
				}
				else
				{
					int lo_uv[] = { uv[dx][0],uv[dy][0], uv[dx][1],uv[dy][0], uv[dx][0],uv[dy][1] };
					const int* lo[3] = { xyzf[dy][dx], xyzf[dy][dx + 1], xyzf[dy + 1][dx] };
					shader.Diffuse(DZ(dy, dx, dy, dx + 1), DZ(dy, dx, dy + 1, dx));
					if (s3_cells)
					{
						S3SubdivTri(S3V(dy, dx, uv[dx][0], uv[dy][0]),
						            S3V(dy, dx + 1, uv[dx][1], uv[dy][0]),
						            S3V(dy + 1, dx, uv[dx][0], uv[dy][1]), 0);
					}
					else
					RasterizePatchTriangle(lo, lo_uv);
				}

				// .__.
				//  \ |
				//   \|
				//    '
				// upper triangle
				if (r->reflection_mode)
				{
					//done
					int up_uv[] = { uv[dx][1],uv[dy][0], uv[dx][0],uv[dy][1], uv[dx][1],uv[dy][1] };
					const int* up[3] = { xyzf[dy][dx + 1], xyzf[dy + 1][dx], xyzf[dy + 1][dx + 1] };
					shader.Diffuse(-xyzf[dy + 1][dx][2] + xyzf[dy + 1][dx + 1][2], -xyzf[dy][dx + 1][2] + xyzf[dy + 1][dx + 1][2]);
					RasterizePatchTriangle(up, up_uv);
				}
				else
				{
					int up_uv[] = { uv[dx][1],uv[dy][1], uv[dx][0],uv[dy][1], uv[dx][1],uv[dy][0] };
					const int* up[3] = { xyzf[dy + 1][dx + 1], xyzf[dy + 1][dx], xyzf[dy][dx + 1] };
					shader.Diffuse(DZ(dy + 1, dx, dy + 1, dx + 1), DZ(dy, dx + 1, dy + 1, dx + 1));
					if (s3_cells)
					{
						S3SubdivTri(S3V(dy + 1, dx + 1, uv[dx][1], uv[dy][1]),
						            S3V(dy + 1, dx, uv[dx][0], uv[dy][1]),
						            S3V(dy, dx + 1, uv[dx][1], uv[dy][0]), 0);
					}
					else
					RasterizePatchTriangle(up, up_uv);
				}
			}
			else
			{
				// lower triangle
				//    .
				//   /|
				//  /_|
				// '  '
				if (r->reflection_mode)
				{
					// done
					int lo_uv[] = { uv[dx][0],uv[dy][0], uv[dx][1],uv[dy][1], uv[dx][1],uv[dy][0] };
					const int* lo[3] = { xyzf[dy][dx], xyzf[dy + 1][dx + 1], xyzf[dy][dx + 1] };
					shader.Diffuse(-xyzf[dy][dx][2] + xyzf[dy][dx + 1][2], -xyzf[dy][dx + 1][2] + xyzf[dy + 1][dx + 1][2]);
					RasterizePatchTriangle(lo, lo_uv);
				}
				else
				{
					int lo_uv[] = { uv[dx][1],uv[dy][0], uv[dx][1],uv[dy][1], uv[dx][0],uv[dy][0] };
					const int* lo[3] = { xyzf[dy][dx + 1], xyzf[dy + 1][dx + 1], xyzf[dy][dx] };
					shader.Diffuse(DZ(dy, dx, dy, dx + 1), DZ(dy, dx + 1, dy + 1, dx + 1));
					if (s3_cells)
					{
						S3SubdivTri(S3V(dy, dx + 1, uv[dx][1], uv[dy][0]),
						            S3V(dy + 1, dx + 1, uv[dx][1], uv[dy][1]),
						            S3V(dy, dx, uv[dx][0], uv[dy][0]), 0);
					}
					else
					RasterizePatchTriangle(lo, lo_uv);
				}


				// upper triangle
				// .__.
				// | / 
				// |/  
				// '
				if (r->reflection_mode)
				{
					//done
					int up_uv[] = { uv[dx][1],uv[dy][1], uv[dx][0],uv[dy][0], uv[dx][0],uv[dy][1] };
					const int* up[3] = { xyzf[dy + 1][dx + 1], xyzf[dy][dx], xyzf[dy + 1][dx]  };
					shader.Diffuse(-xyzf[dy + 1][dx][2] + xyzf[dy + 1][dx + 1][2], -xyzf[dy][dx][2] + xyzf[dy + 1][dx][2]);
					RasterizePatchTriangle(up, up_uv);
				}
				else
				{
					int up_uv[] = { uv[dx][0],uv[dy][1], uv[dx][0],uv[dy][0], uv[dx][1],uv[dy][1] };
					const int* up[3] = { xyzf[dy + 1][dx], xyzf[dy][dx], xyzf[dy + 1][dx + 1] };
					shader.Diffuse(DZ(dy + 1, dx, dy + 1, dx + 1), DZ(dy, dx, dy + 1, dx));
					if (s3_cells)
					{
						S3SubdivTri(S3V(dy + 1, dx, uv[dx][0], uv[dy][1]),
						            S3V(dy, dx, uv[dx][0], uv[dy][0]),
						            S3V(dy + 1, dx + 1, uv[dx][1], uv[dy][1]), 0);
					}
					else
					RasterizePatchTriangle(up, up_uv);
				}
			}
		}
	}


	if (!r->reflection_mode) // disabled on reflections
	{
		// grid lines thru middle of patch?
		int mid = (HEIGHT_CELLS + 1) / 2;
		if (RenderS3Active(r))
			for (int gy = 0; gy <= HEIGHT_CELLS; gy++)
				for (int gx = 0; gx <= HEIGHT_CELLS; gx++)
					if (xyzf[gy][gx][3] & 16)
						return; // flagged vertices carry no screen position

		for (int lin = 0; lin <= HEIGHT_CELLS; lin++)
		{
			xyzf[lin][mid][2] += HEIGHT_SCALE / 2;
			if (mid != lin)
				xyzf[mid][lin][2] += HEIGHT_SCALE / 2;
		}

		for (int lin = 0; lin < HEIGHT_CELLS; lin++)
		{
			Bresenham(ptr, w, h, xyzf[lin][mid], xyzf[lin + 1][mid], 0x04);
			Bresenham(ptr, w, h, xyzf[mid][lin], xyzf[mid][lin + 1], 0x04);
		}
	}
}

// Renderer::RenderSprite (non-static member) lives in render_sprite_blit.cpp
