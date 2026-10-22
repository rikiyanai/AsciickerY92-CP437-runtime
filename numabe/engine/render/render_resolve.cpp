// render_resolve.cpp — Stage 6: downsample SampleBuffer → AnsiCell
//
// Extracted from render_scene.cpp.
// SEE ALSO: render_scene.cpp (caller), render_stage_resolve.h

#include <cstdio>
#include <algorithm>
#include <math.h>
#include <stdint.h>
#include <limits.h>

#include "render_internal.h"
#include "../glyph_id.h"
#include "matrix.h"
#include "material_glyph_plane.h"
#include "renderer_ports_v1.h"
#include "render_stages.h"
// FL-4684 / FL-4685 live producer emission census. This is an OBSERVATION-ONLY
// recorder: it adds up which scene-fact addresses the producer below actually
// emits during a real play session, and how many stroke splits/merges the
// Active Stroke owner performed while that was happening. It is switched off by
// default and cannot change a single pixel. See the long explanation at the top
// of the header for why the measurement exists and how it is switched on.

// Product seam: shape6 dispatch (writers, parity-fill, scene-fact,
// session-live, resolver-hook call, stage input) is product-owned behind the
// neutral renderer port slots; shared call sites never name the
// product implementation.

// FL-4512 seam: the projected-feature bundle build (bundle struct, U7
// topology, product-glyph-program and aspect-pool calls, sequence/
// counterfactual appends) is Current-owned and runs behind the
// build_projected_bundle hook. The pure reason-code names live with the
// Current module; this TU keeps no bundle helper.

void RenderStageResolve(
	Renderer* r,
	AnsiCell* ptr,
	int width,
	int height,
	Material* matlib,
	const double inv_tm[16],
	float water)
{
    // Product frame pre-pass (identity copy, scene-fact plane init,
    // observer-carrier copy) through the neutral slot, attempted whenever a
    // product table is installed (lazy install guarantees it). The Renderer
    // carries the product runtime state as an opaque pointer; Classic
    // carries a frame counter and its pre-pass slot stays a no-op.
	const RendererProductV1* nrt = AsciickerRenderer();
	AsciickerProductStateV1* nst =
	    (r != nullptr) ? (AsciickerProductStateV1*)r->product_runtime_state : nullptr;
	if (nrt && nrt->resolve_pre_pass != nullptr)
	    nrt->resolve_pre_pass(nst, r, width, height);
    // matlib serves the product slots through GetMaterialArr; the shared
    // path keeps no material paint.
    (void)matlib;

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
			const int water_linecase =
				((src[0].spare & 0x40) >> 6) |
				((src[1].spare & 0x40) >> 5) |
				((src[dw].spare & 0x40) >> 4) |
				((src[dw + 1].spare & 0x40) >> 3);
			// Water is a visible compositor owner, not a terrain material profile.
			// Decide that ownership before either resolver hook runs so a buried
			// terrain fact can never publish a glyph that survives the water pass.
			const bool euclidean_water_synthesis =
				!(r->curvature.present == 1 &&
				  r->curvature.kind != ASCK_CURVATURE_KIND_EUCLIDEAN);
			const bool water_surface_owned = water_linecase != 0 ||
				(euclidean_water_synthesis &&
				 src[0].height < water && src[1].height < water &&
				 src[dw].height < water && src[dw + 1].height < water);
			const int mat_owner[4] = {
					RenderSampleMaterialId(src[0]),
					RenderSampleMaterialId(src[1]),
					RenderSampleMaterialId(src[dw]),
					RenderSampleMaterialId(src[dw + 1])
			};
			int mat[4] = {
				mat_owner[0] >= 0 ? mat_owner[0] : (src[0].visual & 0x00FF),
				mat_owner[1] >= 0 ? mat_owner[1] : (src[1].visual & 0x00FF),
				mat_owner[2] >= 0 ? mat_owner[2] : (src[dw].visual & 0x00FF),
				mat_owner[3] >= 0 ? mat_owner[3] : (src[dw + 1].visual & 0x00FF)
			};
			int dif[4] = { src[0].diffuse , src[1].diffuse, src[dw].diffuse, src[dw + 1].diffuse };

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

            // Product routing (no preprocessor, no product names): a cell
            // with mixed reflection or any visual sample resolves through
            // the installed product automap slot; every other sampled cell
            // resolves through the terrain slot. Both products paint their
            // own cells inside those slots; this TU keeps no paint.
            // Equivalent to the retired vote loop: it armed use_auto_mat
            // for mixed reflection (below) or any visual sample (!m pass).
            bool has_refl = (spr[0] & 3) == 3 || (spr[1] & 3) == 3 || (spr[2] & 3) == 3 || (spr[3] & 3) == 3;
            bool has_norm = (spr[0] & 3) == 1 || (spr[1] & 3) == 1 || (spr[2] & 3) == 1 || (spr[3] & 3) == 1;
            bool has_visual = (spr[0] & 0x8) || (spr[1] & 0x8) || (spr[2] & 0x8) || (spr[3] & 0x8);
            bool use_auto_mat = (has_refl && has_norm) || has_visual;

			if (use_auto_mat)
			{
                // Explicit product dispatch (no preprocessor, no product names):
                // the installed product resolves through its automap slot
                // below; the terrain else arm does the same through its own.
				// Cell finish through one neutral slot, attempted whenever a
				// product table is installed (lazy install guarantees it): the
				// installed product finishes the cell from these measurements.
				// FL-4131 Step 5: source-cell shape6 resolver hook (auto_mat path).
				// Material-owned mesh cells carry a real material id; unowned
				// sky/sprite/mixed-color cells remain pass-through.
				// FL-4131 S2: dispatch surface tagged AUTOMAP_MESH_RGB. Sprite/actor
				// cells drawn before resolve are routed by Sample::glyph_dispatch_surface.
				// Deferred actor sprites render after resolve and dispatch from
				// render_sprite_blit.cpp at the sprite owner site.
				// Product sample measurement through neutral slots. The installed
				// product resolves dispatch/material from samples; Classic
				// measure slots return the caller-provided surface and -1 (no material).
				uint8_t auto_dispatch_surface = water_surface_owned
					? (uint8_t)GLYPH_DISPATCH_WATER
					: (uint8_t)GLYPH_DISPATCH_AUTOMAP_MESH_RGB;
				int auto_material_id = -1;
				{
					const RendererProductV1* meas_nrt = AsciickerRenderer();
					if (!water_surface_owned && meas_nrt != nullptr)
					{
						if (meas_nrt->measure_dispatch_surface != nullptr)
							auto_dispatch_surface =
								meas_nrt->measure_dispatch_surface(src, dw,
									(uint8_t)GLYPH_DISPATCH_AUTOMAP_MESH_RGB);
						if (meas_nrt->measure_cell_material_vote != nullptr)
							auto_material_id =
								meas_nrt->measure_cell_material_vote(src, dw);
					}
				}
				const bool auto_has_material =
				    auto_dispatch_surface == GLYPH_DISPATCH_AUTOMAP_MESH_RGB &&
				    auto_material_id >= 0 && auto_material_id < 256;
				const int auto_hook_material = auto_has_material ? auto_material_id : mat[0];
				const uint8_t auto_diffuses[4] = {
					src[0].diffuse, src[1].diffuse,
					src[dw].diffuse, src[dw + 1].diffuse };
                // Product resolve: bundle build + bridge publication + resolver
                // dispatch collapsed behind one neutral slot. Relation unknowns,
                // canonical material, and fact routing live product-side.
                {
                    const RendererProductV1* auto_nrt = AsciickerRenderer();
                    AsciickerProductStateV1* auto_nst =
                        (AsciickerProductStateV1*)r->product_runtime_state;
                    uint8_t auto_applied = 0;
                    if (auto_nst != nullptr && auto_nrt &&
                    auto_nrt->resolve_automap_cell != nullptr)
                        auto_applied = auto_nrt->resolve_automap_cell(auto_nst, r, ptr,
                            x, y, width, height,
                            auto_hook_material, auto_has_material ? 1 : 0,
                            auto_dispatch_surface, elv, shd, src, dw, auto_diffuses);
                    if (!auto_applied)
                    {
                        // Fail closed: no product painted (uninstalled table
                        // or broken state). Unreached by linked products:
                        // both paint unconditionally in their slots.
                        ptr->gl = '?';
                        ptr->bk = 16;
                        ptr->fg = (uint8_t)(16 + 36 * 5 + 6 * 5);
                        ptr->spare = 0xFF;
                    }
                }
				}
			else
			{

				const uint8_t terrain_dispatch_surface = water_surface_owned
					? (uint8_t)GLYPH_DISPATCH_WATER
					: (uint8_t)GLYPH_DISPATCH_TERRAIN_MATERIAL;
				const uint8_t terrain_diffuses[4] = {
					src[0].diffuse, src[1].diffuse,
					src[dw].diffuse, src[dw + 1].diffuse };
                // Product terrain resolve behind one neutral slot. Relation
                // computation, canonical material, fact routing, and resolver
                // dispatch live product-side; both products paint
                // unconditionally in their slots.
                uint8_t terrain_applied = 0;
                {
                    const RendererProductV1* terr_nrt = AsciickerRenderer();
                    AsciickerProductStateV1* terr_nst =
                        (AsciickerProductStateV1*)r->product_runtime_state;
                    if (terr_nst != nullptr && terr_nrt &&
                        terr_nrt->resolve_terrain_cell != nullptr)
                        terrain_applied =
                            terr_nrt->resolve_terrain_cell(terr_nst, r, ptr,
                                x, y, width, height, mat[0],
                                water_surface_owned ? 0 : 1,
                                terrain_dispatch_surface, elv, shd, src, dw,
                                terrain_diffuses,
                                water_surface_owned ? 1 : 0);
                }
                if (!terrain_applied)
                {
                    // Fail closed (see automap arm).
                    ptr->gl = '?';
                    ptr->bk = 16;
                    ptr->fg = (uint8_t)(16 + 36 * 5 + 6 * 5);
                    ptr->spare = 0xFF;
                }
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

			int linecase = water_linecase;
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
			// FL-4257 corrective (2026-06-11) — water synthesis is a
			// EUCLIDEAN-BASELINE-ONLY resolve feature. Under published
			// non-Euclidean curvature water is closed world-model geometry
			// rendered through the projection owner; fail closed (Law 6).
			if (!(r->curvature.present == 1 &&
			      r->curvature.kind != ASCK_CURVATURE_KIND_EUCLIDEAN) &&
			    src[0].height < water && src[1].height < water && src[dw].height < water && src[dw+1].height < water)
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
						// FL-4257 corrective (2026-06-11) — the sinc(arc) water resolve
					// post-correction is deleted with its rejected owner.
					// Water under spherical curvature is closed world-model
					// geometry rendered through the 4D owner; this resolve
					// branch is Euclidean-baseline-only (gated below).
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
			if (r->s3_active == 1)
			{
				int s3_parity_count =
					(src[0].s3_parity ? 1 : 0) +
					(src[1].s3_parity ? 1 : 0) +
					(src[dw].s3_parity ? 1 : 0) +
					(src[dw + 1].s3_parity ? 1 : 0);
				if (s3_parity_count >= 2)
					ptr->gl = RenderS3FlipGlyphY(ptr->gl);
			}

			// Seal the complete world-owned source tuple after water and S3 glyph
			// transforms. Later Game::Render layers are compared with this plane by
			// TERM++; any changed tuple is authored overlay content, never a profile
			// cell that the compiled selector may repaint.
			// Product world-cell bridge seal through the neutral slot, attempted
			// whenever a product table is installed. Classic carries no bridge
			// planes and its seal slot is a no-op.
			{
				const RendererProductV1* seal_nrt = AsciickerRenderer();
				AsciickerProductStateV1* seal_nst =
					(AsciickerProductStateV1*)r->product_runtime_state;
				if (seal_nrt && seal_nrt->seal_world_cell != nullptr)
					seal_nrt->seal_world_cell(seal_nst, y * 160 + x,
						ptr->fg, ptr->bk, ptr->gl, ptr->spare, 1u);
			}

			src += 2;
#else
            // Non-supersampled build only. DBL cells always consume a 2x2
            // sample footprint, whether automap or terrain owns their paint.
                const RendererProductV1* single_nrt = AsciickerRenderer();
                AsciickerProductStateV1* single_nst =
                    (AsciickerProductStateV1*)r->product_runtime_state;
                uint8_t single_applied = 0;
                if (single_nst != nullptr && single_nrt &&
                    single_nrt->resolve_single_sample_cell != nullptr)
                    single_applied = single_nrt->resolve_single_sample_cell(
                        single_nst, r, ptr, x, y, width, height,
                        src[0].visual);
                if (!single_applied)
                {
                    // Fail closed (see automap arm).
                    ptr->gl = '?';
                    ptr->bk = 16;
                    ptr->fg = (uint8_t)(16 + 36 * 5 + 6 * 5);
                    ptr->spare = 0xFF;
                }

                src++;
#endif

		}

		#ifdef DBL
		src += 4 + dw;
		#else
		src += 2;
		#endif
	}
// Product frame-carrier publication (sequence claims, spacetime store,
// counterfactual refs) through the neutral slot, attempted whenever a
// product table is installed. Classic carries no carriers and its slot is
// a no-op.
{
	const RendererProductV1* carr_nrt = AsciickerRenderer();
	AsciickerProductStateV1* carr_nst =
		(AsciickerProductStateV1*)r->product_runtime_state;
	if (carr_nrt && carr_nrt->publish_frame_carriers != nullptr)
		carr_nrt->publish_frame_carriers(carr_nst);
}
}
