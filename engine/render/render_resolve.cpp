// render_resolve.cpp — Stage 6: downsample SampleBuffer → AnsiCell
//
// Extracted from render_scene.cpp.
// SEE ALSO: render_scene.cpp (caller), render_stage_resolve.h

#include <cstdio>
#include <stdint.h>

#include "render_internal.h"
#include "matrix.h"
#include "material_glyph_plane.h"

extern uint8_t auto_mat[];

#ifdef __EMSCRIPTEN__
extern "C" void WebRenderGlyphSidecarWrite(int x, int y, int render_w, int render_h, uint32_t glyph_id);
#elif !defined(SERVER)
extern "C" void NativeRenderGlyphSidecarWrite(int x, int y, int render_w, int render_h, uint32_t glyph_id);
#endif

static void ApplyMaterialGlyphPlaneCell(Renderer* r, AnsiCell* cell, const Material* material, int elev, int shade)
{
	if (!material || !material->glyph_plane)
		return;
	GlyphId glyph_id = material_glyph_plane_lookup(material->glyph_plane, elev, shade);
	if (!glyph_id_is_extended(glyph_id))
		return;

	uint16_t coverage = material_glyph_plane_lookup_coverage(material->glyph_plane, elev, shade);
	r->material_glyph_report.extended_cells_seen++;
	r->material_glyph_report.last_glyph_id = glyph_id;
	r->material_glyph_report.last_coverage = coverage;
	if (coverage == 0)
	{
		cell->fg = 16;
		cell->bk = 16 + 36 * 5;
		cell->gl = '!';
		cell->spare = 0xFF;
		r->material_glyph_report.diagnostic_cells_rendered++;
		r->material_glyph_report.last_display_glyph = '!';
		return;
	}
	uint8_t display_glyph = material_glyph_plane_coverage_display_glyph(coverage);
	cell->gl = display_glyph;
	r->material_glyph_report.coverage_cells_rendered++;
	r->material_glyph_report.last_display_glyph = display_glyph;
}

static void ApplyMaterialGlyphPlaneCell(Renderer* r, AnsiCell* cell, const Material* material, int elev, int shade, int x, int y, int width, int height)
{
	ApplyMaterialGlyphPlaneCell(r, cell, material, elev, shade);
#ifdef __EMSCRIPTEN__
	if (!material || !material->glyph_plane)
		return;
	GlyphId glyph_id = material_glyph_plane_lookup(material->glyph_plane, elev, shade);
	if (glyph_id_is_extended(glyph_id))
		WebRenderGlyphSidecarWrite(x, y, width, height, glyph_id);
#elif !defined(SERVER)
	if (!material || !material->glyph_plane)
		return;
	GlyphId glyph_id = material_glyph_plane_lookup(material->glyph_plane, elev, shade);
	uint16_t coverage = material_glyph_plane_lookup_coverage(material->glyph_plane, elev, shade);
	if (glyph_id_is_extended(glyph_id) && coverage != 0)
		NativeRenderGlyphSidecarWrite(x, y, width, height, glyph_id);
#endif
}

void RenderStageResolve(
	Renderer* r,
	AnsiCell* ptr,
	int width,
	int height,
	Material* matlib,
	const double inv_tm[16],
	float water)
{
	// Perspective water coefficients: reconstructed from r->mul/r->add
	// (r->add[1] = tm[13] + 0.5, and in perspective mode int_flag is false
	// so add[0,1] exactly equal tm[12], tm[13]+0.5.)
	float ww_x = 0, ww_y = 0, ww_c = 0;
	float wx_x = 0, wx_y = 0, wx_c = 0;
	float wy_x = 0, wy_y = 0, wy_c = 0;
	if (r->perspective)
	{
		// Reconstruct reflected tm elements from the transform cached on Renderer.
		// tm[0..5,8,9,12,13] are all derivable from r->mul/add.
		double tm0 = r->mul[0];
		double tm1 = r->mul[1];
		double tm4 = r->mul[2];
		double tm5 = r->mul[3];
		double tm8 = 0.0;
		double tm9 = r->mul[5];
		double tm12 = r->add[0];
		double tm13 = r->add[1] - 0.5;
		float vd0 = r->view_dir[0];
		float vd1 = r->view_dir[1];
		float vp0 = r->view_pos[0];
		float vp1 = r->view_pos[1];
		float vo0 = r->view_ofs[0];
		float vo1 = r->view_ofs[1];
		ww_x = (float)(vd0 * tm5 - vd1 * tm1);
		ww_y = (float)(vd1 * tm0 - vd0 * tm4);
		ww_c = (float)(tm1 * tm4 - tm0 * tm5);
		wx_x = (float)(vp0 * tm5 * vd0 + vd1 * (-vo1 + vp1 * tm5 + tm13 + tm9 * water));
		wx_y = (float)(vp0 * tm4 * vd0 + vd1 * (-vo0 + vp1 * tm4 + tm12 + tm8 * water));
		wx_c = (float)(tm5 * (-vo0 + tm12 + tm8 * water) + tm4 * (vo1 - tm13 - tm9 * water));
		wy_x = (float)(vp1 * tm1 * vd1 + vd0 * (-vo1 + vp0 * tm1 + tm13 + tm9 * water));
		wy_y = (float)(vp1 * tm0 * vd1 + vd0 * (-vo0 + vp0 * tm0 + tm12 + tm8 * water));
		wy_c = (float)(tm1 * (vo0 - tm12 - tm8 * water) + tm0 * (-vo1 + tm13 + tm9 * water));
	}

	int dw = r->sample_buffer.w; // canonical stride set during SampleBuffer allocation

	Sample* src = r->sample_buffer.ptr + 2 + 2 * dw;
	for (int y = 0; y < height; y++)
	{
		for (int x = 0; x < width; x++, ptr++)
		{
			if (x == render_break_point[0] && y == render_break_point[1])
			{
				render_break_point[0] = -1;
				render_break_point[1] = -1;
			}
			// Resolve step: compress four hidden sub-samples into one visible
			// terminal cell, choosing colors and a CP437 glyph that best represent
			// what those four samples contain.


#ifdef DBL

// average 4 backgrounds
// mask 11 (something rendered)
			int spr[4] = { src[0].spare & 11, src[1].spare & 11, src[dw].spare & 11, src[dw + 1].spare & 11 };
			int mat[4] = { src[0].visual & 0x00FF , src[1].visual & 0x00FF, src[dw].visual & 0x00FF, src[dw + 1].visual & 0x00FF };
			int dif[4] = { src[0].diffuse , src[1].diffuse, src[dw].diffuse, src[dw + 1].diffuse };
			int vis[4] = { src[0].visual, src[1].visual, src[dw].visual, src[dw + 1].visual };
			// Read the 2x2 block as four tiny votes about this final cell's material,
			// light, and depth situation.

			// TODO:
			// every material must have 16x16 map and uses visual shade to select Y and lighting to select X
			// animated materials additionaly pre shifts and wraps visual shade by current time scaled by material's 'speed'

			// Turn neighboring 1-bit elevation flags into a 4-way ramp selector.
			int e_lo = (src[-dw].visual >> 15) + (src[-dw + 1].visual >> 15);
			int e_mi = (src[0].visual >> 15) + (src[1].visual >> 15);
			int e_hi = (src[dw].visual >> 15) + (src[dw + 1].visual >> 15);

			int elv;
			if (e_lo <= 1)
			{
				if (e_hi <= 1)
					elv = 3; // lo
				else
					elv = 2; // raise
			}
			else
			{
				if (e_hi <= 1)
					elv = 0; // lower
				else
					elv = 1; // hi
			}

			/*
			int shd = 0; // (src[0].visual >> 8) & 0x007F;

			int gl = matlib[mat[0]].shade[1][shd].gl;
			int bg[3] = { 0,0,0 };
			int fg[3] = { 0,0,0 };
			for (int i = 0; i < 4; i++)
			{
				bg[0] += matlib[mat[i]].shade[1][shd].bg[0] * dif[i];
				bg[1] += matlib[mat[i]].shade[1][shd].bg[1] * dif[i];
				bg[2] += matlib[mat[i]].shade[1][shd].bg[2] * dif[i];
				fg[0] += matlib[mat[i]].shade[1][shd].fg[0] * dif[i];
				fg[1] += matlib[mat[i]].shade[1][shd].fg[1] * dif[i];
				fg[2] += matlib[mat[i]].shade[1][shd].fg[2] * dif[i];
			}
			*/

			// Average four 0..255 diffuse values into one 0..15 material shade band.
			int shd = (dif[0] + dif[1] + dif[2] + dif[3] + 17 * 2) / (17 * 4); // 17: FF->F, 4: avr

			

			// This is where a material recipe becomes a candidate visible glyph.
			int gl = matlib[mat[0]].shade[elv][shd].gl;

			int bg[3] = { 0,0,0 }; // 4
			int fg[3] = { 0,0,0 };

			int half_h[2][2] = { {0,1},{2,3} };
			int half_v[2][2] = { {0,2},{1,3} };
			int bg_h[2][3] = { { 0,0,0 },{ 0,0,0 } }; // 0+1 \ 2+3 
			int bg_v[2][3] = { { 0,0,0 },{ 0,0,0 } }; // 0+2 | 1+3

			// auto_mat means "let the generic RGB-to-terminal approximation logic
			// decide the cell", usually because mesh color or mixed reflection is present.
			bool use_auto_mat = false;

			// These errors measure whether the 2x2 block looks more like a
			// top/bottom split or a left/right split.
			int err_h = 0;
			int err_v = 0;

			// if cell contains both refl and non-refl terrain enable auto-mat
			bool has_refl = (spr[0] & 3) == 3 || (spr[1] & 3) == 3 || (spr[2] & 3) == 3 || (spr[3] & 3) == 3;
			bool has_norm = (spr[0] & 3) == 1 || (spr[1] & 3) == 1 || (spr[2] & 3) == 1 || (spr[3] & 3) == 1;
			if (has_refl && has_norm)
			{
				use_auto_mat = true;
			}

			for (int m = 0; m < 2; m++)
			{
				for (int i = 0; i < 4; i++)
				{
					//if (spr[i])
					{
						if (spr[i] & 0x8)
						{
							int r = ((vis[i] & 0x1F) * 527 + 23) >> 6;
							int g = (((vis[i] >> 5) & 0x1F) * 527 + 23) >> 6;
							int b = (((vis[i] >> 10) & 0x1F) * 527 + 23) >> 6;

							if ((spr[i] & 0x3) == 3)
							{
								r = r * dif[i] / 400;
								g = g * dif[i] / 400;
								b = b * dif[i] / 400;
							}
							else
							{
								r = r * dif[i] / 255;
								g = g * dif[i] / 255;
								b = b * dif[i] / 255;
							}

							if (i == 0 || i == 1)
							{
								if (m)
								{
									err_h += abs(bg_h[0][0] - 4 * r);
									err_h += abs(bg_h[0][1] - 4 * g);
									err_h += abs(bg_h[0][2] - 4 * b);
								}
								else
								{
									bg_h[0][0] += 2 * r;
									bg_h[0][1] += 2 * g;
									bg_h[0][2] += 2 * b;
								}
							}
							if (i == 2 || i == 3)
							{
								if (m)
								{
									err_h += abs(bg_h[1][0] - 4 * r);
									err_h += abs(bg_h[1][1] - 4 * g);
									err_h += abs(bg_h[1][2] - 4 * b);
								}
								else
								{
									bg_h[1][0] += 2 * r;
									bg_h[1][1] += 2 * g;
									bg_h[1][2] += 2 * b;
								}
							}

							if (i == 0 || i == 2)
							{
								if (m)
								{
									err_v += abs(bg_v[0][0] - 4 * r);
									err_v += abs(bg_v[0][1] - 4 * g);
									err_v += abs(bg_v[0][2] - 4 * b);
								}
								else
								{
									bg_v[0][0] += 2 * r;
									bg_v[0][1] += 2 * g;
									bg_v[0][2] += 2 * b;
								}
							}
							if (i == 1 || i == 3)
							{
								if (m)
								{
									err_v += abs(bg_v[1][0] - 4 * r);
									err_v += abs(bg_v[1][1] - 4 * g);
									err_v += abs(bg_v[1][2] - 4 * b);
								}
								else
								{
									bg_v[1][0] += 2 * r;
									bg_v[1][1] += 2 * g;
									bg_v[1][2] += 2 * b;
								}
							}

							if (!m)
							{
								bg[0] += r;
								bg[1] += g;
								bg[2] += b;
								use_auto_mat = true;
							}
						}
						else
						{
							int s = dif[i] / 17;
							int r = matlib[mat[i]].shade[elv][s].bg[0];
							int g = matlib[mat[i]].shade[elv][s].bg[1];
							int b = matlib[mat[i]].shade[elv][s].bg[2];

							if ((spr[i] & 0x3) == 3)
							{
								r = r * 255 / 400;
								g = g * 255 / 400;
								b = b * 255 / 400;
							}

							if (i == 0 || i == 1)
							{
								if (m)
								{
									err_h += abs(bg_h[0][0] - 4 * r);
									err_h += abs(bg_h[0][1] - 4 * g);
									err_h += abs(bg_h[0][2] - 4 * b);
								}
								else
								{
									bg_h[0][0] += 2 * r;
									bg_h[0][1] += 2 * g;
									bg_h[0][2] += 2 * b;
								}
							}
							if (i == 2 || i == 3)
							{
								if (m)
								{
									err_h += abs(bg_h[1][0] - 4 * r);
									err_h += abs(bg_h[1][1] - 4 * g);
									err_h += abs(bg_h[1][2] - 4 * b);
								}
								else
								{
									bg_h[1][0] += 2*r;
									bg_h[1][1] += 2*g;
									bg_h[1][2] += 2*b;
								}
							}

							if (i == 0 || i == 2)
							{
								if (m)
								{
									err_v += abs(bg_v[0][0] - 4 * r);
									err_v += abs(bg_v[0][1] - 4 * g);
									err_v += abs(bg_v[0][2] - 4 * b);
								}
								else
								{
									bg_v[0][0] += 2*r;
									bg_v[0][1] += 2*g;
									bg_v[0][2] += 2*b;
								}
							}
							if (i == 1 || i == 3)
							{
								if (m)
								{
									err_v += abs(bg_v[1][0] - 4 * r);
									err_v += abs(bg_v[1][1] - 4 * g);
									err_v += abs(bg_v[1][2] - 4 * b);
								}
								else
								{
									bg_v[1][0] += 2*r;
									bg_v[1][1] += 2*g;
									bg_v[1][2] += 2*b;
								}
							}

							if (!m)
							{
								bg[0] += r;
								bg[1] += g;
								bg[2] += b;

								if ((spr[i] & 0x3) == 0x3)
								{
									fg[0] += matlib[mat[i]].shade[elv][s].fg[0] * 255 / 400;
									fg[1] += matlib[mat[i]].shade[elv][s].fg[1] * 255 / 400;
									fg[2] += matlib[mat[i]].shade[elv][s].fg[2] * 255 / 400;
								}
								else
								{
									fg[0] += matlib[mat[i]].shade[elv][s].fg[0];
									fg[1] += matlib[mat[i]].shade[elv][s].fg[1];
									fg[2] += matlib[mat[i]].shade[elv][s].fg[2];
								}
							}
						}
					}
				}
			}

			if (use_auto_mat)
			{
				// WORKS REALY WELL! 
				bool vh_near = true;

				if (err_h * 1000 < err_v * 999)
				{
					vh_near = false;
					// Strong horizontal split: use an upper-half block glyph so one
					// terminal cell can show two colors sharply.
					ptr->gl = 0xDF;

					int auto_mat_lo = 3 * ((bg_h[0][0]+20) / 33 + 32 * ((bg_h[0][1]+20) / 33) + 32 * 32 * ((bg_h[0][2]+20) / 33));
					int auto_mat_hi = 3 * ((bg_h[1][0]+20) / 33 + 32 * ((bg_h[1][1]+20) / 33) + 32 * 32 * ((bg_h[1][2]+20) / 33));

					ptr->bk = auto_mat[auto_mat_lo + 0];
					ptr->fg = auto_mat[auto_mat_hi + 0];
				}
				else
				if (err_v * 1000 < err_h * 999)
				{
					vh_near = false;
					// Strong vertical split: use a right-half block glyph.
					ptr->gl = 0xDE;

					int auto_mat_lt = 3 * ((bg_v[0][0]+20) / 33 + 32 * ((bg_v[0][1]+20) / 33) + 32 * 32 * ((bg_v[0][2]+20) / 33));
					int auto_mat_rt = 3 * ((bg_v[1][0]+20) / 33 + 32 * ((bg_v[1][1]+20) / 33) + 32 * 32 * ((bg_v[1][2]+20) / 33));

					ptr->bk = auto_mat[auto_mat_lt + 0];
					ptr->fg = auto_mat[auto_mat_rt + 0];
				}

				
				if (ptr->bk == ptr->fg || vh_near)
				{
					// avr4
					int auto_mat_idx = 3 * (bg[0] / 33 + 32 * (bg[1] / 33) + 32 * 32 * (bg[2] / 33));
					ptr->gl = auto_mat[auto_mat_idx + 2];
					ptr->bk = auto_mat[auto_mat_idx + 0];
					ptr->fg = auto_mat[auto_mat_idx + 1];
					ptr->spare = 0xFF;
				}
			}
			else
			{
				int bk_rgb[3] =
				{
					(bg[0] + 102) / 204,
					(bg[1] + 102) / 204,
					(bg[2] + 102) / 204
				};

				// Final terrain-only write: convert averaged RGB into terminal palette
				// indices and store the chosen glyph into the visible AnsiCell.
				ptr->gl = gl;
				ptr->bk = 16 + 36*bk_rgb[0] + bk_rgb[1] * 6 + bk_rgb[2];
				ptr->fg = 16 + (36*((fg[0] + 102) / 204) + (((fg[1] + 102) / 204) * 6) + ((fg[2] + 102) / 204));
				ptr->spare = 0xFF;

				// collect line bits

				if (elv == 3) // only low elevation
				{
					int linecase = ((src[0].spare & 0x4) >> 2) | ((src[1].spare & 0x4) >> 1) | (src[dw].spare & 0x4) | ((src[dw + 1].spare & 0x4) << 1);
					static const int linecase_glyph[] = { 0, ',', ',', ',', '`', ';', ';', ';', '`', ';', ';', ';', '`', ';', ';', ';' };
					if (linecase)
						ptr->gl = linecase_glyph[linecase];
				}

				if (elv == 1 || elv == 3) // no elev change
				{
					// silhouette repetitoire:  _-/\| (should not be used by materials?)
					float z_hi = src[dw].height + src[dw + 1].height;
					float z_lo = src[0].height + src[1].height;
					float z_pr = src[-dw].height + src[1 - dw].height;

					float minus = z_lo - z_hi;
					float under = z_pr - z_lo;

					static const float thresh = 1 * HEIGHT_SCALE;

					if (minus > under)
					{
						if (minus > thresh)
						{
							ptr->gl = 0xC4; // '-'
							bk_rgb[0] = std::max(0, bk_rgb[0] - 1);
							bk_rgb[1] = std::max(0, bk_rgb[1] - 1);
							bk_rgb[2] = std::max(0, bk_rgb[2] - 1);
							ptr->fg = 16 + 36 * bk_rgb[0] + bk_rgb[1] * 6 + bk_rgb[2];
						}
					}
					else
					{
						if (under > thresh)
						{
							ptr->gl = 0x5F; // '_'
							bk_rgb[0] = std::max(0, bk_rgb[0] - 1);
							bk_rgb[1] = std::max(0, bk_rgb[1] - 1);
							bk_rgb[2] = std::max(0, bk_rgb[2] - 1);
							ptr->fg = 16 + 36 * bk_rgb[0] + bk_rgb[1] * 6 + bk_rgb[2];
						}
					}
				}
				ApplyMaterialGlyphPlaneCell(r, ptr, &matlib[mat[0]], elv, shd, x, y, width, height);
			}

			// --- water-pass fixture: save before state ---
			bool _wp_dump = (r->frame_input && r->frame_input->debug_hooks.water_pass_cell);
			WaterPassCellDump _wp;
			if (_wp_dump)
			{
				_wp.cell_x = x;
				_wp.cell_y = y;
				_wp.sx = 2 * x + 2;
				_wp.sy = 2 * y + 2;
				_wp.sample_visuals[0]  = src[0].visual;
				_wp.sample_visuals[1]  = src[1].visual;
				_wp.sample_visuals[2]  = src[dw].visual;
				_wp.sample_visuals[3]  = src[dw + 1].visual;
				_wp.sample_diffuses[0] = src[0].diffuse;
				_wp.sample_diffuses[1] = src[1].diffuse;
				_wp.sample_diffuses[2] = src[dw].diffuse;
				_wp.sample_diffuses[3] = src[dw + 1].diffuse;
				_wp.sample_spares[0]   = src[0].spare;
				_wp.sample_spares[1]   = src[1].spare;
				_wp.sample_spares[2]   = src[dw].spare;
				_wp.sample_spares[3]   = src[dw + 1].spare;
				_wp.sample_heights[0]  = src[0].height;
				_wp.sample_heights[1]  = src[1].height;
				_wp.sample_heights[2]  = src[dw].height;
				_wp.sample_heights[3]  = src[dw + 1].height;
				_wp.before_fg    = ptr->fg;
				_wp.before_bk    = ptr->bk;
				_wp.before_gl    = ptr->gl;
				_wp.before_spare = ptr->spare;
				_wp.water_level  = water;
				_wp.linecase     = 0;
				_wp.perlin_path  = false;
				_wp.perlin_input_x   = 0.0;
				_wp.perlin_input_y   = 0.0;
				_wp.perlin_input_time = 0.0;
				_wp.perlin_value     = 0.0;
				_wp.water_id         = 0;
				_wp.mutation_applied = false;
				_wp.mutation_reason[0] = '\0';
			}

			int linecase = ((src[0].spare & 0x40) >> 6) | ((src[1].spare & 0x40) >> 5) | ((src[dw].spare & 0x40)>>4) | ((src[dw + 1].spare & 0x40) >> 3);
			if (_wp_dump)
				_wp.linecase = linecase;
			static const int linecase_glyph[] = { 0, ',', ',', ',', '`', ';', ';', ';', '`', ';', ';', ';', '`', ';', ';', ';' };
			if (linecase)
			{
				ptr->gl = linecase_glyph[linecase];
				ptr->fg = 16;
				if (_wp_dump)
				{
					_wp.mutation_applied = true;
					snprintf(_wp.mutation_reason, sizeof(_wp.mutation_reason),
						"water_line_spare_0x40_linecase_%d", linecase);
				}
			}
			else
			if (src[0].height < water && src[1].height < water && src[dw].height < water && src[dw+1].height < water)
			{
				double w[4]; 
				if (r->perspective) // #if PERSPECTIVE_TEST
				{
					float sx_dx = 2.0f*x - r->view_ofs[0];
					float sy_dy = 2.0f*y - r->view_ofs[1];
					float ww = (sx_dx*ww_x + sy_dy*ww_y + ww_c);
					if (ww<0)
					{
						ww = 1.0f/ww;
						float wx = ww * (wx_c + wx_x * sx_dx - wx_y * sy_dy);
						float wy = ww * (wy_c - wy_x * sx_dx + wy_y * sy_dy);
						w[0] = wx;
						w[1] = wy;
					}
					else
					{
						ptr->gl = ' ';
						if (_wp_dump)
						{
							_wp.mutation_applied = true;
							snprintf(_wp.mutation_reason, sizeof(_wp.mutation_reason),
								"water_perspective_clip_ww_positive");
							_wp.after_fg    = ptr->fg;
							_wp.after_bk    = ptr->bk;
							_wp.after_gl    = ptr->gl;
							_wp.after_spare = ptr->spare;
							r->frame_input->debug_hooks.water_pass_cell(r, &_wp, r->frame_input->debug_hooks.user);
						}
						src += 2;
						continue;
					}
				}
				else // #else
				{
					double s[4] = { 2.0*x, 2.0*y, water, 1.0 };
					Product(inv_tm, s, w); // convert from screen to world
					w[0] = round(w[0]);
					w[1] = round(w[1]);
				}
				// #endif

				if (_wp_dump)
				{
					_wp.perlin_path = true;
					_wp.perlin_input_x = w[0] * 0.05;
					_wp.perlin_input_y = w[1] * 0.05;
					_wp.perlin_input_time = r->pn_time;
				}

				double d = r->pn.octaveNoise0_1(w[0] * 0.05, w[1] * 0.05, r->pn_time, 4);

				int id = (int)(d * 5) - 2;

				if (id < -1)
					id = 2;
				if (id > 1)
					id = -2;

				if (_wp_dump)
				{
					_wp.perlin_value = d;
					_wp.water_id = id;
				}

				if (id > 0)
				{
					int c = ptr->fg - 16;
					int cr = c / 36;
					c -= cr * 36;
					int cg = c / 6;
					c -= cr * 6;
					int cb = c;

					if (cr < 5 && cg < 5 /*&& cb < 5*/)
					{
						if (cb < 5)
							ptr->fg += 1 + 6 + 36;
						else
							ptr->fg += 6 + 36;
						if (_wp_dump)
						{
							_wp.mutation_applied = true;
							snprintf(_wp.mutation_reason, sizeof(_wp.mutation_reason),
								"underwater_perlin_id_positive");
						}
					}
				}
				else
				if (id < 0)
				{
					int c = ptr->fg - 16;
					int cr = c / 36;
					c -= cr * 36;
					int cg = c / 6;
					c -= cr * 6;
					int cb = c;

					if (cr > 0 && cg > 0 /*&& cb > 0*/)
					{
						if (cb > 0)
							ptr->fg -= 1 + 6 + 36;
						else
							ptr->fg -= 6 + 36;
						if (_wp_dump)
						{
							_wp.mutation_applied = true;
							snprintf(_wp.mutation_reason, sizeof(_wp.mutation_reason),
								"underwater_perlin_id_negative");
						}
					}
				}
				if (_wp_dump && !_wp.mutation_applied)
					snprintf(_wp.mutation_reason, sizeof(_wp.mutation_reason),
						"underwater_perlin_no_color_change");
			}
			else if (_wp_dump && !_wp.mutation_applied)
				snprintf(_wp.mutation_reason, sizeof(_wp.mutation_reason),
					"no_water_line_no_underwater_samples");

			if (_wp_dump)
			{
				_wp.after_fg    = ptr->fg;
				_wp.after_bk    = ptr->bk;
				_wp.after_gl    = ptr->gl;
				_wp.after_spare = ptr->spare;
				r->frame_input->debug_hooks.water_pass_cell(r, &_wp, r->frame_input->debug_hooks.user);
			}

			// xterm conv

			src += 2;


			
			#else
			
			int mat = src[0].visual & 0x00FF;
			int shd = 0; // (src[0].visual >> 8) & 0x007F;
			int elv = 0; // (src[0].visual >> 15) & 0x0001;

			// fill from material
			const MatCell* cell = &(matlib[mat].shade[1][shd]);
			const uint8_t* bg = matlib[mat].shade[1][shd].bg;
			const uint8_t* fg = matlib[mat].shade[1][shd].fg;

			ptr->gl = cell->gl;
			ptr->bk = 16 + (((bg[0] + 25) / 51) * 36 + ((bg[1] + 25) / 51) * 6 + ((bg[2] + 25) / 51));
			ptr->fg = 16 + (((fg[0] + 25) / 51) * 36 + ((fg[1] + 25) / 51) * 6 + ((fg[2] + 25) / 51));
			ptr->spare = 0xFF;
			ApplyMaterialGlyphPlaneCell(r, ptr, &matlib[mat], elv, shd, x, y, width, height);

			src++;
			#endif

		}

		#ifdef DBL
		src += 4 + dw;
		#else
		src += 2;
		#endif
	}


}
