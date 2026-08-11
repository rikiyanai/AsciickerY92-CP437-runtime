// render_world_pass.cpp — World/mesh/patch rendering pipeline
//
// Extracted from engine/render.cpp.
// SEE ALSO: render.h, render_internal.h

#include "render_rasterize.h"
#include "matrix.h"
#include "game.h"
#include "snapshot_npc_sprite_data_tag.h"

static int create_auto_mat(uint8_t mat[]);
uint8_t auto_mat[/*b*/32/*g*/ * 32/*r*/ * 32/*bg,fg,gl*/ * 3];
int auto_mat_result = create_auto_mat(auto_mat);
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
// view*instance matrix, computes per-face diffuse lighting from the surface
// normal, then delegates to Rasterize<> with an inline Shader that writes
// RGB555 + diffuse into the SampleBuffer (flagged with spare|0x8 for mesh).
void Renderer::RenderFace(float coords[9], uint8_t colors[12], uint32_t visual, void* cookie)
{
	struct Shader
	{
		void BlendVR(Sample*s, float z, const float* vr)
		{
			if (s->height < z)
			{
				if (global_refl_mode)
				{
					if (z < water + HEIGHT_SCALE / 8)
					{
						if (z > water)
							s->height = water;
						else
							s->height = z;

						int r8 = std::clamp((int)floor(vr[1]), 0, 255);
						int r5 = (r8 * 249 + 1014) >> 11;
						int g8 = std::clamp((int)floor(vr[2]), 0, 255);
						int g5 = (g8 * 249 + 1014) >> 11;
						int b8 = std::clamp((int)floor(vr[3]), 0, 255);
						int b5 = (b8 * 249 + 1014) >> 11;

						// Mesh path: store direct RGB555 color in Sample.visual instead of
						// a terrain material id. Resolve will recognize spare&0x8 later.
						s->visual = r5 | (g5 << 5) | (b5 << 10);
						s->diffuse = diffuse;
						s->spare = (s->spare & ~0x44) | 0x8 | 0x3;
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

						int r8 = std::clamp((int)floor(vr[1]), 0, 255);
						int r5 = (r8 * 249 + 1014) >> 11;
						int g8 = std::clamp((int)floor(vr[2]), 0, 255);
						int g5 = (g8 * 249 + 1014) >> 11;
						int b8 = std::clamp((int)floor(vr[3]), 0, 255);
						int b5 = (b8 * 249 + 1014) >> 11;

						// Mesh path: write a packed color directly into the hidden
						// SampleBuffer; no Material::shade lookup is used for meshes.
						s->visual = r5 | (g5 << 5) | (b5 << 10);
						s->diffuse = diffuse;
						s->spare = (s->spare & ~(0x3|0x44)) | 0x8 | 0x1;
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
		float light[4];
		uint8_t diffuse; // shading experiment
	} shader;

	Renderer* r = (Renderer*)cookie;
	shader.water = r->water;

	// temporarily, let's transform verts for each face separately

	int v[3][4];

	float tmp0[4], tmp1[4], tmp2[4];

	{
		float xyzw[] = { coords[0], coords[1], coords[2], 1.0f };
		Product(r->viewinst_tm, xyzw, tmp0);

		if (r->perspective) // #if PERSPECTIVE_TEST 
		{
			float ws[4];
			Product(r->inst_tm, xyzw, ws);
			float viewer_dist; // {vx,vy,vz}  r->pos
			float eye_to_vtx[3] =
			{
				ws[0] * HEIGHT_CELLS - r->view_pos[0],
				ws[1] * HEIGHT_CELLS - r->view_pos[1],
				ws[2] - r->view_pos[2],
			};

			viewer_dist = DotProduct(eye_to_vtx, r->view_dir);
			if (viewer_dist > 0)
			{
				viewer_dist = 1.0f/viewer_dist;

				float fx = tmp0[0];
				float fy = tmp0[1];

				fx = (fx - r->view_ofs[0]) * viewer_dist + r->view_ofs[0];
				fy = (fy - r->view_ofs[1]) * viewer_dist + r->view_ofs[1];

				int tx = (int)floorf(fx + 0.5f);
				int ty = (int)floorf(fy + 0.5f);

				v[0][0] = tx;
				v[0][1] = ty;
				v[0][2] = (int)floor(tmp0[2] + 0.5f);
				v[0][3] = 0; // clip flags
			}
			else
				return;
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
			float ws[4];
			Product(r->inst_tm, xyzw, ws);
			float viewer_dist; // {vx,vy,vz}  r->pos
			float eye_to_vtx[3] =
			{
				ws[0] * HEIGHT_CELLS - r->view_pos[0],
				ws[1] * HEIGHT_CELLS - r->view_pos[1],
				ws[2] - r->view_pos[2],
			};

			viewer_dist = DotProduct(eye_to_vtx, r->view_dir);
			if (viewer_dist > 0)
			{
				viewer_dist = 1.0f/viewer_dist;

				float fx = tmp1[0];
				float fy = tmp1[1];

				fx = (fx - r->view_ofs[0]) * viewer_dist + r->view_ofs[0];
				fy = (fy - r->view_ofs[1]) * viewer_dist + r->view_ofs[1];

				int tx = (int)floorf(fx + 0.5f);
				int ty = (int)floorf(fy + 0.5f);

				v[1][0] = tx;
				v[1][1] = ty;
				v[1][2] = (int)floor(tmp1[2] + 0.5f);
				v[1][3] = 0; // clip flags
			}
			else
				return;
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
			float ws[4];
			Product(r->inst_tm, xyzw, ws);
			float viewer_dist; // {vx,vy,vz}  r->pos
			float eye_to_vtx[3] =
			{
				ws[0] * HEIGHT_CELLS - r->view_pos[0],
				ws[1] * HEIGHT_CELLS - r->view_pos[1],
				ws[2] - r->view_pos[2],
			};

			viewer_dist = DotProduct(eye_to_vtx, r->view_dir);
			if (viewer_dist > 0)
			{
				viewer_dist = 1.0f/viewer_dist;

				float fx = tmp2[0];
				float fy = tmp2[1];

				fx = (fx - r->view_ofs[0]) * viewer_dist + r->view_ofs[0];
				fy = (fy - r->view_ofs[1]) * viewer_dist + r->view_ofs[1];

				int tx = (int)floorf(fx + 0.5f);
				int ty = (int)floorf(fy + 0.5f);

				v[2][0] = tx;
				v[2][1] = ty;
				v[2][2] = (int)floor(tmp2[2] + 0.5f);
				v[2][3] = 0; // clip flags
			}
			else
				return;
		}
		else // #else
		{
			v[2][0] = (int)floor(tmp2[0] + 0.5f);
			v[2][1] = (int)floor(tmp2[1] + 0.5f);
			v[2][2] = (int)floor(tmp2[2] + 0.5f);
			v[2][3] = 0; // clip flags
		} // #endif
	}

	int w = r->sample_buffer.w;
	int h = r->sample_buffer.h;
	Sample* ptr = r->sample_buffer.ptr;

	// normal is const, could be baked into mesh
	float e1[] = { coords[3] - coords[0], coords[4] - coords[1], coords[5] - coords[2] };
	float e2[] = { coords[6] - coords[0], coords[7] - coords[1], coords[8] - coords[2] };

	float n[4] =
	{
		e1[1] * e2[2] - e1[2] * e2[1],
		e1[2] * e2[0] - e1[0] * e2[2],
		e1[0] * e2[1] - e1[1] * e2[0],
		0
	};

	float inst_n[4];
	Product(r->inst_tm, n, inst_n);

	inst_n[2] /= HEIGHT_SCALE;

	float nn = 1.0f / sqrtf(inst_n[0] * inst_n[0] + inst_n[1] * inst_n[1] + inst_n[2] * inst_n[2]);

	float df = nn * (inst_n[0] * r->light[0] + inst_n[1] * r->light[1] + inst_n[2] * r->light[2]);

	//diffuse = 1.0;

	df = df * (1.0f - 0.5f*r->light[3]) + 0.5f*r->light[3];
	df += 0.5;

	if (df > 1)
		df = 1;
	if (df < 0)
		df = 0;

	shader.diffuse = (int)(df * 0xFF);

	if (global_refl_mode)
	{
		const int* pv[3] = { v[2],v[1],v[0] };
		float tri_varying[3][4] =
		{
			{ (float)v[2][2], (float)colors[8], (float)colors[9], (float)colors[10] },
			{ (float)v[1][2], (float)colors[4], (float)colors[5], (float)colors[6] },
			{ (float)v[0][2], (float)colors[0], (float)colors[1], (float)colors[2] },
		};

		//for (int i = 0; i < 12; i++)
		//	colors[i] = colors[i] * 3 / 4;

		RasterizeVarying<Sample, Shader, 4>(r->sample_buffer.ptr, r->sample_buffer.w, r->sample_buffer.h, &shader, pv, visual&(1<<30), tri_varying);
	}
	else
	{
		const int* pv[3] = { v[0],v[1],v[2] };
		float tri_varying[3][4] =
		{
			{ (float)v[0][2], (float)colors[0], (float)colors[1], (float)colors[2] },
			{ (float)v[1][2], (float)colors[4], (float)colors[5], (float)colors[6] },
			{ (float)v[2][2], (float)colors[8], (float)colors[9], (float)colors[10] },
		};
		RasterizeVarying<Sample, Shader, 4>(r->sample_buffer.ptr, r->sample_buffer.w, r->sample_buffer.h, &shader, pv, visual&(1<<30), tri_varying);
	}
}

// Renderer::RenderSprite (static callback) lives in render_sprite_blit.cpp

void Renderer::RenderMesh(Inst* inst, Mesh* m, double* tm, void* cookie)
{
	(void)inst;
	Renderer* r = (Renderer*)cookie;
	double view_tm[16]=
	{
		r->mul[0] * HEIGHT_CELLS, r->mul[1] * HEIGHT_CELLS, 0.0, 0.0,
		r->mul[2] * HEIGHT_CELLS, r->mul[3] * HEIGHT_CELLS, 0.0, 0.0,
		r->mul[4], r->mul[5], global_refl_mode ? -1.0 : 1.0, 0.0,
		r->add[0], r->add[1], r->add[2], 1.0
	};

	r->inst_tm = tm;
	MatProduct(view_tm, tm, r->viewinst_tm);
	QueryMesh(m, Renderer::RenderFace, r);

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
				if (global_refl_mode)
				{
					if (z < water + HEIGHT_SCALE / 8)
					{
						if (z > water)
							s->height = water;
						else
							s->height = z;

						int u = std::clamp((int)floor(vr[1]), 0, VISUAL_CELLS - 1);
						int v = std::clamp((int)floor(vr[2]), 0, VISUAL_CELLS - 1);

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
							s->diffuse = diffuse;
							s->spare |= parity | 0x3;
							s->spare &= ~(0x44|0x8); // clear mesh and lines
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

						int u = std::clamp((int)floor(vr[1]), 0, VISUAL_CELLS - 1);
						int v = std::clamp((int)floor(vr[2]), 0, VISUAL_CELLS - 1);

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
							s->diffuse = diffuse;

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
		float light[4];
		uint8_t diffuse; // shading experiment
		uint8_t parity;
#ifdef DARK_TERRAIN
		uint64_t dark;
#endif
	} shader;

	Renderer* r = (Renderer*)cookie;

	double* mul = r->mul;

	int iadd[2] = { (int)r->add[0], (int)r->add[1] };
	double* add = r->add;

	int w = r->sample_buffer.w;
	int h = r->sample_buffer.h;
	Sample* ptr = r->sample_buffer.ptr;

	uint16_t* hmap = GetTerrainHeightMap(p);
	
	uint16_t* hm = hmap;

	// transform patch verts xy+dx+dy, together with hmap into this array
	int xyzf[HEIGHT_CELLS + 1][HEIGHT_CELLS + 1][4];

	for (int dy = 0; dy <= HEIGHT_CELLS; dy++)
	{
		int vy = y * HEIGHT_CELLS + dy * VISUAL_CELLS;

		for (int dx = 0; dx <= HEIGHT_CELLS; dx++)
		{
			int vx = x * HEIGHT_CELLS + dx * VISUAL_CELLS;
			int vz = *(hm++);

			if (global_refl_mode)
			{
				if (r->perspective) // #if PERSPECTIVE_TEST
				{
					float viewer_dist; // {vx,vy,vz}  r->pos
					float eye_to_vtx[3] =
					{
						vx - r->view_pos[0],
						vy - r->view_pos[1],
						vz - r->view_pos[2],
					};

					viewer_dist = DotProduct(eye_to_vtx, r->view_dir);
					if (viewer_dist > 0)
					{
						viewer_dist = 1.0f/viewer_dist;

						float fx = (float)(mul[0] * vx + mul[2] * vy);// + add[0];
						float fy = (float)(mul[1] * vx + mul[3] * vy + mul[5] * vz);// + add[1];

						fx *= viewer_dist;
						fy *= viewer_dist;

						float qx = (float)((add[0] - r->view_ofs[0]) * viewer_dist + r->view_ofs[0]);
						float qy = (float)((add[1] - r->view_ofs[1]) * viewer_dist + r->view_ofs[1]);

						fx += qx;
						fy += qy;

						int tx = (int)floorf(fx + 0.5f);
						int ty = (int)floorf(fy + 0.5f);

						xyzf[dy][dx][0] = tx;
						xyzf[dy][dx][1] = ty;
						xyzf[dy][dx][2] = (int)(2 * r->water) - vz;

						// todo: if patch is known to fully fit in screen, set f=0 
						// otherwise we need to check if / which screen edges cull each vertex
						xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
					}
					else
					{
						// cull entire patch if any vertex is behind view_pos
						return;
					}
				}
				else // #else
				{
					if (r->int_flag)
					{
						int tx = (int)floor(mul[0] * vx + mul[2] * vy + 0.5 + add[0]);
						int ty = (int)floor(mul[1] * vx + mul[3] * vy + mul[5] * vz + 0.5 + add[1]);

						xyzf[dy][dx][0] = tx;
						xyzf[dy][dx][1] = ty;
						xyzf[dy][dx][2] = (int)(2 * r->water) - vz;

						// todo: if patch is known to fully fit in screen, set f=0 
						// otherwise we need to check if / which screen edges cull each vertex
						xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
					}
					else
					{
						int tx = (int)floor(mul[0] * vx + mul[2] * vy + 0.5) + iadd[0];
						int ty = (int)floor(mul[1] * vx + mul[3] * vy + mul[5] * vz + 0.5) + iadd[1];

						xyzf[dy][dx][0] = tx;
						xyzf[dy][dx][1] = ty;
						xyzf[dy][dx][2] = (int)(2 * r->water) - vz;

						// todo: if patch is known to fully fit in screen, set f=0 
						// otherwise we need to check if / which screen edges cull each vertex
						xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
					}
				} // #endif
			}
			else
			{
				if (r->perspective) // #if PERSPECTIVE_TEST
				{
					float viewer_dist; // {vx,vy,vz}  r->pos
					float eye_to_vtx[3] =
					{
						vx - r->view_pos[0],
						vy - r->view_pos[1],
						vz - r->view_pos[2],
					};

					viewer_dist = DotProduct(eye_to_vtx, r->view_dir);
					if (viewer_dist > 0)
					{
						viewer_dist = 1.0f/viewer_dist;
						
						float fx = (float)(mul[0] * vx + mul[2] * vy);// + add[0];
						float fy = (float)(mul[1] * vx + mul[3] * vy + mul[5] * vz);// + add[1];

						fx *= viewer_dist;
						fy *= viewer_dist;

						float qx = (float)((add[0] - r->view_ofs[0]) * viewer_dist + r->view_ofs[0]);
						float qy = (float)((add[1] - r->view_ofs[1]) * viewer_dist + r->view_ofs[1]);

						fx += qx;
						fy += qy;

						int tx = (int)floorf(fx + 0.5f);
						int ty = (int)floorf(fy + 0.5f);

						xyzf[dy][dx][0] = tx;
						xyzf[dy][dx][1] = ty;
						xyzf[dy][dx][2] = vz;

						// todo: if patch is known to fully fit in screen, set f=0 
						// otherwise we need to check if / which screen edges cull each vertex
						xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
					}
					else
					{
						// cull entire patch if any vertex is behind view_pos
						return;
					}
				}
				else // #else
				{
					// transform 
					if (r->int_flag)
					{
						int tx = (int)floor(mul[0] * vx + mul[2] * vy + 0.5 + add[0]);
						int ty = (int)floor(mul[1] * vx + mul[3] * vy + mul[5] * vz + 0.5 + add[1]);

						xyzf[dy][dx][0] = tx;
						xyzf[dy][dx][1] = ty;
						xyzf[dy][dx][2] = vz;

						// todo: if patch is known to fully fit in screen, set f=0 
						// otherwise we need to check if / which screen edges cull each vertex
						xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
					}
					else
					{
						int tx = (int)floor(mul[0] * vx + mul[2] * vy + 0.5) + iadd[0];
						int ty = (int)floor(mul[1] * vx + mul[3] * vy + mul[5] * vz + 0.5) + iadd[1];

						xyzf[dy][dx][0] = tx;
						xyzf[dy][dx][1] = ty;
						xyzf[dy][dx][2] = vz;

						// todo: if patch is known to fully fit in screen, set f=0 
						// otherwise we need to check if / which screen edges cull each vertex
						xyzf[dy][dx][3] = (tx < 0) | ((tx > w) << 1) | ((ty < 0) << 2) | ((ty > h) << 3);
					}
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

	const int (*uv)[2] = r->patch_uv;
	auto RasterizePatchTriangle = [&](const int* tri[3], const int tri_uv[6])
	{
		float tri_varying[3][3] =
		{
			{ (float)tri[0][2], (float)tri_uv[0], (float)tri_uv[1] },
			{ (float)tri[1][2], (float)tri_uv[2], (float)tri_uv[3] },
			{ (float)tri[2][2], (float)tri_uv[4], (float)tri_uv[5] },
		};
		RasterizeVarying<Sample, Shader, 3>(ptr, w, h, &shader, tri, false, tri_varying);
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
				if (global_refl_mode)
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
					shader.Diffuse(xyzf[dy][dx][2] - xyzf[dy][dx + 1][2], xyzf[dy][dx][2] - xyzf[dy + 1][dx][2]);
					RasterizePatchTriangle(lo, lo_uv);
				}

				// .__.
				//  \ |
				//   \|
				//    '
				// upper triangle
				if (global_refl_mode)
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
					shader.Diffuse(xyzf[dy + 1][dx][2] - xyzf[dy + 1][dx + 1][2], xyzf[dy][dx + 1][2] - xyzf[dy + 1][dx + 1][2]);
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
				if (global_refl_mode)
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
					shader.Diffuse(xyzf[dy][dx][2] - xyzf[dy][dx + 1][2], xyzf[dy][dx + 1][2] - xyzf[dy + 1][dx + 1][2]);
					RasterizePatchTriangle(lo, lo_uv);
				}


				// upper triangle
				// .__.
				// | / 
				// |/  
				// '
				if (global_refl_mode)
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
					shader.Diffuse(xyzf[dy + 1][dx][2] - xyzf[dy + 1][dx + 1][2], xyzf[dy][dx][2] - xyzf[dy + 1][dx][2]);
					RasterizePatchTriangle(up, up_uv);
				}
			}
		}
	}


	if (!global_refl_mode) // disabled on reflections
	{
		// grid lines thru middle of patch?
		int mid = (HEIGHT_CELLS + 1) / 2;

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
